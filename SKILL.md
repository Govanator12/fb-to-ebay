---
name: fb-to-ebay
description: Crosspost a Facebook Marketplace listing to eBay. The user pastes a Marketplace URL; you scrape the listing with Playwright (using a saved FB session), polish the title/description for eBay's conventions, pick a category, gather shipping details, then publish via the eBay Sell APIs. Use this skill whenever the user pastes a facebook.com/marketplace URL, mentions "crossposting" / "mirroring" a listing to eBay, asks to "list this on eBay", or describes any FB Marketplace item they want on eBay — even if they don't say the word "skill".
---

# fb-to-ebay

A workflow for taking one of the user's Facebook Marketplace listings and republishing it on eBay. Direction is one-way: FB → eBay only. The Marketplace side is scraped with Playwright + a saved FB session; the eBay side is published via the Sell Inventory API plus Trading API EPS for image upload.

The "intelligence" — extracting fields from a noisy DOM, polishing the title, mapping conditions, choosing categories — is your job in conversation, not a separate LLM call.

## Mental model

The user already wrote and listed the item on Facebook. They don't want to retype it. Your job is high-fidelity translation: keep the meaning, change the formatting to match eBay's conventions and required fields. Default to the user's voice, not yours. When you have to invent something they didn't write (a longer description, a category guess), be transparent about it and let them correct you before anything goes live.

eBay listings are **expensive to fix once published** (relisting fees, search ranking resets). Always show a proposed draft and wait for explicit approval before publishing. Never assume "looks fine, ship it."

## Prerequisites (check first)

Before doing real work:

1. `~/.config/fb-to-ebay/.env` exists with `EBAY_ENV` set and the corresponding env-prefixed credentials populated (e.g. `EBAY_SANDBOX_APP_ID` / `EBAY_SANDBOX_CERT_ID` / `EBAY_SANDBOX_DEV_ID` / `EBAY_SANDBOX_RUNAME` when `EBAY_ENV=sandbox`). Missing? Point at `.env.example` in the skill directory and stop. (Bare `EBAY_APP_ID` etc. still work as a legacy fallback.)
2. `~/.config/fb-to-ebay/token-{env}.json` exists for the active environment (e.g. `token-sandbox.json` or `token-production.json`). Missing? Tell the user to run `uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_auth.py login` and ask before running it on their behalf — it opens a browser. (Two-step form for non-interactive shells: print the URL with `login`, complete with `login --redirect-url "<pasted-url>"`.)
3. `~/.config/fb-to-ebay/fb_session.json` exists. Missing? Tell the user to run `uv run ~/.claude/skills/fb-to-ebay/scripts/fb_session.py` (one-time, ~30 seconds, manual login). Ask before running.
4. Playwright's Chromium binary is installed. If not, the script will print the install command (`uv run --with playwright playwright install chromium`) — don't try to install it silently.
5. `EBAY_ENV` value: surface it in chat ("publishing to **sandbox**") so the user never confuses environments. Default sandbox in any first-time interaction.

## Workflow

### 1. Fetch the FB listing

```
uv run ~/.claude/skills/fb-to-ebay/scripts/fb_fetch.py <fb-url> --out /tmp/fb-draft.json
```

Logs into FB with the saved session, scrapes title/description/price/condition/images, downloads images to `~/.cache/fb-to-ebay/<slug>/`. Output JSON includes `fbCondition` (raw FB string) plus `imageUrls` (FB CDN — short-lived) and `localImages` (downloaded paths).

### 2. Normalize for eBay

Read `references/ebay_field_map.md` for the rules. Rewrite the title (≤80 chars, search-friendly), expand the description, map `fbCondition` → eBay enum (e.g. `"Used - Like New"` → `USED_EXCELLENT`).

### 3. Suggest a category

```
uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_taxonomy.py "<polished title>"
```

Show all 3 suggestions with full category paths. Don't auto-select — categories are sticky.

### 3a. Validate conditions for the chosen category

Once the user picks a category, immediately run:

```
uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_conditions.py <categoryId>
```

This prints the valid condition IDs and the Inventory API enum to use. Many categories — especially collectibles, antiques, art — only support generic "Used" (id 3000), which means `USED_GOOD` / `USED_VERY_GOOD` / `USED_ACCEPTABLE` get rejected at publishOffer time. In those categories, map FB's "Used - Like New" / "Used - Good" / etc. to `USED_EXCELLENT` (the enum eBay translates to "Used"), and put the granular detail in `conditionDescription` so buyers see it.

If the FB condition can't be honestly represented within what the category allows (e.g., FB "Used - Good" but the category only allows "New"), tell the user — don't quietly downgrade.

### 4. Images (automatic)

`ebay_publish.py` auto-uploads any `localImages` to eBay Picture Services (EPS) at publish time and uses the resulting eBay-hosted URLs in the inventory item. You don't need to ask the user to re-host anywhere. Just keep the `localImages` paths from `fb_fetch.py` in the draft and let the publish script handle it.

### 5. Confirm shipping/listing details

Before showing the final draft, confirm these five per-listing answers. Each has a sensible env-var fallback in `.env` so they don't have to be asked every time:

| Question | Env var (skip the question if set) | Default if unset |
|---|---|---|
| Estimated weight + box dimensions? | — (always per-listing) | Ask |
| Allow local pickup? | `EBAY_OFFER_LOCAL_PICKUP` | `true` (FB items often suit local pickup) |
| Ship internationally? | `EBAY_SHIP_INTERNATIONALLY` | `false` |
| Handling time (business days between sale and drop-off)? | `EBAY_DEFAULT_HANDLING_DAYS` | `2` |
| Returns accepted? | uses existing return policy | Don't re-ask unless user wants override |

Skip whatever's already in env. Only ask the user about fields that have no env value AND no per-listing override in the draft. Read your env setting back at the user the first time it's used so they know it's wired up ("I'll use 2-day handling from your env — say so if this listing needs different.").

For weight + dimensions specifically, ask plainly ("about how much does it weigh and what size box?"). These feed the carrier's calculated rate.

### 6. Show the draft

Display title, price, condition, category, description preview, image source, plus the shipping summary built from step 5's answers. Wait for explicit approval.

### 7. Publish

Write the approved draft to a temp JSON file matching `references/draft_schema.md`, then:

```
uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_publish.py --draft /tmp/ebay-draft.json
```

What the script does for you automatically:
- Mints a per-listing fulfillment policy via `createFulfillmentPolicy` if the draft has shipping overrides (handlingDays / localPickup / shipInternationally) or any of the corresponding env defaults are set; reuses an existing matching policy if one is already there.
- Uploads each path in `localImages` to eBay Picture Services (EPS) and uses the eBay-hosted URLs in the inventory item.
- Builds `packageWeightAndSize` from `weightLbs` + `boxDimensionsIn` so calculated rates work.
- Falls back to env-default policy IDs (`EBAY_*_FULFILLMENT_POLICY_ID` etc.) and merchant location key when not in the draft.
- Rolls back orphan inventory items + offers if any step fails (no debris piles up across retries).

Prints the live listing URL on success.

## Example: a typical run

Concrete walk-through of what a successful interaction looks like, so you can pattern-match in similar situations.

**User:** `https://www.facebook.com/marketplace/item/1234567890`

**You:**
1. Run `fb_fetch.py` on the URL → get back title `"Vintage Levi's 501 jeans 32x34"`, fbCondition `"Used - Good"`, price `$45`, 4 photos in `~/.cache/fb-to-ebay/vintage-levis-501-jeans-32x34/`.
2. Polish the title to `"Vintage Levi's 501 Jeans Men's 32x34 Straight Leg Denim Distressed"` (front-loads keywords, ≤80 chars).
3. Run `ebay_taxonomy.py "Vintage Levi's 501 Jeans Men's 32x34 ..."` → top suggestions are `11483 (Men's > Jeans)`, `175771 (Vintage > Men's > ...)`, `155182 (Specialty)`. Show all three. User picks `11483`.
4. Run `ebay_conditions.py 11483` → see the category accepts `USED_EXCELLENT`, `USED_VERY_GOOD`, `USED_GOOD`, `USED_ACCEPTABLE`. Map FB's `"Used - Good"` to `USED_GOOD`.
5. Confirm shipping settings — env has handlingDays=2, localPickup=false, shipInternationally=false, so only ask about weight and dimensions ("about how much does it weigh and what size box?"). User: "about 1 lb in a small mailer, maybe 12×9×2".
6. Show the proposed draft in chat (title, price, condition + USED_GOOD enum, category 11483, description preview, 4 photos, shipping summary). Wait for "publish" / "yes" / "go".
7. Write the draft to `/tmp/ebay-draft.json` and run `ebay_publish.py --draft /tmp/ebay-draft.json`. Show the live URL it prints.

If anything fails (LSAS, missing aspect, condition rejection), check the Errors section below and recover — don't blindly retry.

## Errors and gotchas

- **Missing eBay business policies** (`Error 25709` or similar payment/return/fulfillment policy errors): account hasn't set up policies yet. Production users can use https://www.ebay.com/sh/policies in the seller hub UI; sandbox users have to use the Account API directly (the seller hub for policies isn't reliable on sandbox). Either way, the README's "Setup → step 6" section walks through opting in, creating the three policies, and registering an inventory location. After setup, write the resulting IDs into the user's `.env` (`EBAY_*_FULFILLMENT_POLICY_ID`, etc.) so they don't need to live in every draft.
- **Missing inventory location** (`Error 25007` or "merchant location key not found"): same root cause — first-time setup. Create one with `POST /sell/inventory/v1/location/{key}` (see README step 6e) and put `EBAY_MERCHANT_LOCATION_KEY=<key>` in `.env`.
- **Category requires aspects** (`Error 25002`, "item specific X is missing"): the chosen `categoryId` requires structured item-specifics. Call `GET /commerce/taxonomy/v1/category_tree/{tree}/get_item_aspects_for_category?category_id=<id>` to see what's needed, ask the user for the values, add them under `aspects: { ... }` in the draft, retry.
- **EPS upload fails** (Trading API XML error from `ebay_eps.py`): usually the local image file is missing/corrupt or eBay's EPS service is having a hiccup. Confirm the local file exists and try again. EPS-hosted images expire after 30 days if not associated with an active listing — fine since we publish immediately.
- **`Invalid <ShippingPackage>`** (errorId 25101): the chosen shipping service doesn't accept CALCULATED rates with the given package, or `packageType` is incompatible with the service. The publish script omits packageType deliberately to avoid this. If it still fires, swap the shipping service (try `USPSParcel` or `USPSPriority`) or check that weight + dimensions are sane.
- **`LSAS validation failed`** when creating a fulfillment policy: eBay's Listing Shipping Advisor Service rejected a service code on this account. `USPSGroundAdvantage` is a known offender on some accounts despite being the modern code. Use `USPSParcel` instead (set `EBAY_SHIPPING_SERVICES=USPSParcel,USPSPriority`).
- **`invalid_scope`** at OAuth time on production: a requested OAuth scope isn't granted to the app on production. Check the SCOPES list in `ebay_auth.py` against what's allowed — `commerce.catalog.readonly` was previously a culprit and has been removed.
- **FB login expired** (`fb_fetch.py` returns mostly-empty fields): re-run `fb_session.py`.
- **FB security challenge mid-script**: the open browser will show a CAPTCHA or identity check. Have the user solve it manually, then re-run `fb_session.py` afterward to capture refreshed cookies.
- **Selector breakage on FB**: FB rewrites its DOM frequently. If `fb_fetch.py` returns mostly-empty fields, the selectors in that script need updating. Tell the user — don't pretend the data is good.
- **Token expired**: the publish/fetch scripts auto-refresh. If refresh itself fails, prompt the user to re-run `ebay_auth.py login`.
- **Sandbox confusion**: if the user sees a sandbox URL when they expected production (or vice versa), check `EBAY_ENV` in their `.env`. Don't change this for them without confirmation.

## Reference files

- `references/ebay_field_map.md` — eBay condition codes, title rules, required offer fields
- `references/draft_schema.md` — JSON shape `ebay_publish.py` expects

## Why this skill exists

The user has a Claude Max plan, which covers Claude Code/chat usage but not separate Anthropic API billing. As a skill, the polish step happens in the conversation that's already paid for; only eBay-side calls run as code. Keep that constraint in mind — don't suggest external paid services unless the user explicitly opts in.
