---
name: ebay-lister
description: List an item on eBay. The primary path is listing a brand-new item from scratch — the user describes the item and supplies photos, and you polish the title/description for eBay's conventions, pick a category, gather shipping details, then publish via the eBay Sell APIs. An optional feature crossposts an existing Facebook Marketplace listing instead: the user pastes a Marketplace URL and you scrape it with Playwright (saved FB session) rather than interviewing them. Use this skill whenever the user asks to "list this on eBay", "post a new item to eBay", "sell something on eBay", describes an item they want listed, or — for the crosspost feature — pastes a facebook.com/marketplace URL or asks to "crosspost"/"mirror" a listing. Trigger even if they don't say the word "skill".
---

# ebay-lister

A workflow for getting one of the user's items listed on eBay. The eBay side is always published the same way — via the Sell Inventory API plus Trading API EPS for image upload. There are two ways to feed it:

- **List from scratch (primary).** There is no source listing. The user describes a brand-new item in conversation and supplies photos directly (a folder of files). This is the main use of the skill.
- **Crosspost from Facebook (optional feature).** The user has already listed the item on Facebook Marketplace and doesn't want to retype it. Scrape the Marketplace listing with Playwright + a saved FB session instead of interviewing the user. One-way only: FB → eBay.

The "intelligence" — eliciting or extracting fields, polishing the title, mapping conditions, choosing categories — is your job in conversation, not a separate LLM call.

## Which way in

- The user wants to **list something new** ("post this to eBay", "sell my desk lamp on eBay") with no existing listing behind it → **list from scratch** (main Workflow below).
- The user **pastes a `facebook.com/marketplace` URL**, or refers to "my FB listing" / "crosspost this" / "mirror this" → **crosspost feature** (section after the main Workflow).
- If it's ambiguous, ask: "Are we listing this fresh, or is it already up on Facebook Marketplace?"

Both paths converge on the same draft JSON (`references/draft_schema.md`) and the same `ebay_publish.py` step.

## Mental model

**From scratch:** the user hasn't written a listing anywhere. Your job is to interview them for the facts (what is it, condition, what's included, flaws, dimensions) and draft eBay copy from those facts. You're writing the listing *with* them — propose copy, but make clear it's a draft and invite corrections.

**Crosspost:** the user already wrote and listed the item on Facebook. Your job is high-fidelity translation: keep the meaning, change the formatting to match eBay's conventions and required fields. Default to the user's voice, not yours.

In both paths, when you invent something the user didn't give you (a longer description, a category guess), be transparent about it and let them correct you before anything goes live.

eBay listings are **expensive to fix once published** (relisting fees, search ranking resets). Always show a proposed draft and wait for explicit approval before publishing. Never assume "looks fine, ship it."

## Prerequisites (check first)

Before doing real work:

1. `~/.config/ebay-lister/.env` exists with `EBAY_ENV` set and the corresponding env-prefixed credentials populated (e.g. `EBAY_SANDBOX_APP_ID` / `EBAY_SANDBOX_CERT_ID` / `EBAY_SANDBOX_DEV_ID` / `EBAY_SANDBOX_RUNAME` when `EBAY_ENV=sandbox`). Missing? Point at `.env.example` in the skill directory and stop. (Bare `EBAY_APP_ID` etc. still work as a legacy fallback.)
2. `~/.config/ebay-lister/token-{env}.json` exists for the active environment (e.g. `token-sandbox.json` or `token-production.json`). Missing? Tell the user to run `uv run ~/.claude/skills/ebay-lister/scripts/ebay_auth.py login` and ask before running it on their behalf — it opens a browser. (Two-step form for non-interactive shells: print the URL with `login`, complete with `login --redirect-url "<pasted-url>"`.)
3. `EBAY_ENV` value: surface it in chat ("publishing to **sandbox**") so the user never confuses environments. Default sandbox in any first-time interaction.

The crosspost feature needs two more, **only when that feature is used** — skip these entirely for a from-scratch listing:

4. `~/.config/ebay-lister/fb_session.json` exists. Missing? Tell the user to run `uv run ~/.claude/skills/ebay-lister/scripts/fb_session.py` (one-time, ~30 seconds, manual login). Ask before running.
5. Playwright's Chromium binary is installed. If not, the script will print the install command (`uv run --with playwright playwright install chromium`) — don't try to install it silently.

## Workflow — list an item from scratch

This is the primary path. There's no source listing, so you elicit the item, scaffold a draft, polish it, and publish.

### 1. Interview the user for the item

Gather the facts in conversation before scaffolding anything. You need, at minimum:

- **What it is** — brand, model, type. Enough to write a searchable title and pick a category.
- **Condition** — new or used, plus the honest condition story (flaws, wear, what works). Maps to an eBay enum via `references/ebay_field_map.md`.
- **Price** — what they want to list it at.
- **What's included** — accessories, original box, manuals.
- **Photos** — ask where the image files are. A folder path is easiest; individual files are fine too. At least one image is required to publish.
- **Weight + box dimensions** — feeds the carrier's calculated shipping rate.

Ask for these conversationally, grouped — don't interrogate one field at a time. If the user volunteered some up front, only ask for the gaps.

### 2. Scaffold the draft

Write a template draft, pre-filling `localImages` from the user's photo folder:

```
uv run ~/.claude/skills/ebay-lister/scripts/new_listing.py --out /tmp/ebay-draft.json --images <folder-or-files>
```

This writes `/tmp/ebay-draft.json` with every field present — required ones as `TODO` placeholders. Then fill those in with polished values: read `references/ebay_field_map.md` for the rules, write a searchable title (eBay rejects > 80 chars — count yours before saving), expand the description into three short paragraphs, and pick the condition enum.

If the user has no photos ready, scaffold without `--images` and tell them the `localImages` array must have at least one path before publishing.

For aspects (item-specifics) you supply — `Brand`, `Type`, `Theme`, `Genre`, `Color`, `Material` — assume single-valued unless you have evidence otherwise. Many categories reject multi-value lists with errorId 25002 ("X should contain only one value"). When in doubt, pick the one most-relevant value rather than listing all of them.

### 3. Suggest a category

```
uv run ~/.claude/skills/ebay-lister/scripts/ebay_taxonomy.py "<polished title>"
```

Show all 3 suggestions with full category paths. Don't auto-select — categories are sticky.

### 3a. Validate conditions for the chosen category

Once the user picks a category, immediately run:

```
uv run ~/.claude/skills/ebay-lister/scripts/ebay_conditions.py <categoryId>
```

This prints the valid condition IDs and the Inventory API enum to use. Many categories — especially collectibles, antiques, art — only support generic "Used" (id 3000), which means `USED_GOOD` / `USED_VERY_GOOD` / `USED_ACCEPTABLE` get rejected at publishOffer time. In those categories, map the user's "like new" / "good" / etc. to `USED_EXCELLENT` (the enum eBay translates to "Used"), and put the granular detail in `conditionDescription` so buyers see it.

If the item's real condition can't be honestly represented within what the category allows (e.g. it's used but the category only allows "New"), tell the user — don't quietly downgrade.

### 4. Images (automatic)

`ebay_publish.py` auto-uploads any `localImages` to eBay Picture Services (EPS) at publish time and uses the resulting eBay-hosted URLs in the inventory item. You don't need to ask the user to re-host anywhere. Just keep the `localImages` paths in the draft and let the publish script handle it.

### 5. Confirm shipping/listing details

Before showing the final draft, confirm these five per-listing answers. Each has a sensible env-var fallback in `.env` so they don't have to be asked every time:

| Question | Env var (skip the question if set) | Default if unset |
|---|---|---|
| Estimated weight + box dimensions? | — (always per-listing) | Ask |
| Allow local pickup? | `EBAY_OFFER_LOCAL_PICKUP` | `true` (casual sellers often suit local pickup) |
| Ship internationally? | `EBAY_SHIP_INTERNATIONALLY` | `false` |
| Handling time (business days between sale and drop-off)? | `EBAY_DEFAULT_HANDLING_DAYS` | `2` |
| Returns accepted? | uses existing return policy | Don't re-ask unless user wants override |

Skip whatever's already in env. Only ask the user about fields that have no env value AND no per-listing override in the draft. Read your env setting back at the user the first time it's used so they know it's wired up ("I'll use 2-day handling from your env — say so if this listing needs different.").

For weight + dimensions specifically, ask plainly ("about how much does it weigh and what size box?"). These feed the carrier's calculated rate.

### 6. Show the draft

Display title, price, condition, category, description preview, image source, plus the shipping summary built from step 5's answers. Wait for explicit approval.

### 7. Publish

The draft is already at `/tmp/ebay-draft.json` from step 2. Run a dry-run first — it catches leftover `TODO` placeholders *and* any item specifics the category requires but the draft is missing — then publish:

```
uv run ~/.claude/skills/ebay-lister/scripts/ebay_publish.py --dry-run --draft /tmp/ebay-draft.json
uv run ~/.claude/skills/ebay-lister/scripts/ebay_publish.py --draft /tmp/ebay-draft.json
```

What the script does for you automatically:
- Rejects the draft pre-flight if any `TODO` scaffold placeholder is still unfilled.
- Pre-checks the category's **required item specifics** via the Taxonomy API and fails *before* creating anything if the draft is missing one (or has a bad `SELECTION_ONLY` value) — listing exactly what to add. This is a read-only call, so `--dry-run` runs it too.
- Mints a per-listing fulfillment policy via `createFulfillmentPolicy` if the draft has shipping overrides (handlingDays / localPickup / shipInternationally) or any of the corresponding env defaults are set; reuses an existing matching policy if one is already there.
- Uploads each path in `localImages` to eBay Picture Services (EPS) and uses the eBay-hosted URLs in the inventory item.
- Builds `packageWeightAndSize` from `weightLbs` + `boxDimensionsIn` so calculated rates work.
- Falls back to env-default policy IDs (`EBAY_*_FULFILLMENT_POLICY_ID` etc.) and merchant location key when not in the draft.
- Rolls back orphan inventory items + offers if any step fails (no debris piles up across retries).

Prints the live listing URL on success.

## Optional feature — crosspost from a Facebook Marketplace listing

Use this when the item is already listed on Facebook Marketplace and the user pasted its URL. It replaces steps 1–2 of the main Workflow (you scrape instead of interview); steps 3–7 are identical. Direction is one-way: FB → eBay. Check the crosspost-only prerequisites (4 and 5 above) before starting.

### C1. Fetch the FB listing

```
uv run ~/.claude/skills/ebay-lister/scripts/fb_fetch.py <fb-url> --out /tmp/fb-draft.json
```

Logs into FB with the saved session, scrapes title/description/price/condition/images, downloads images to `~/.cache/ebay-lister/<slug>/`. Output JSON includes `fbCondition` (raw FB string) plus `imageUrls` (FB CDN — short-lived) and `localImages` (downloaded paths).

### C2. Normalize for eBay

Read `references/ebay_field_map.md` for the rules. Rewrite the title (eBay rejects > 80 chars — count yours before saving the draft), expand the description, map `fbCondition` → eBay enum (e.g. `"Used - Like New"` → `USED_EXCELLENT`). Same single-value aspect caution as the main Workflow step 2.

Write the polished result to `/tmp/ebay-draft.json` in the `references/draft_schema.md` shape — keep the `localImages` paths from `fb_fetch.py`. From here, continue at **Workflow step 3** (suggest a category) and proceed identically through publish.

## Example: a typical from-scratch run

**User:** "I want to list my old desk lamp on eBay. Photos are in `~/Pictures/desk-lamp`."

**You:**
1. No FB URL → list from scratch. Interview: ask for brand/type, condition + flaws, price, what's included, and weight/size in one grouped message. User: "IKEA Forsa work lamp, black, used but works fine, small scuff on the base. $15. Just the lamp, no bulb. Maybe 3 lbs, fits a 14×10×8 box."
2. Run `new_listing.py --out /tmp/ebay-draft.json --images ~/Pictures/desk-lamp` → template written, 3 photos pre-filled into `localImages`. Fill the `TODO`s: title `"IKEA Forsa Work Lamp Black Adjustable Desk Task Light Metal"` (≤80 chars), a three-paragraph description (what it is + the scuff, specifics, "no bulb included / ships USPS"), condition `USED_GOOD`, price `15.00`, `weightLbs` 3, `boxDimensionsIn` [14,10,8].
3. Run `ebay_taxonomy.py "IKEA Forsa Work Lamp ..."` → show the 3 category suggestions, user picks one.
4. Run `ebay_conditions.py <categoryId>` → confirm `USED_GOOD` is accepted (or remap).
5. Confirm shipping settings (env defaults + the weight/size already given).
6. Show the draft for approval. On "go", run `ebay_publish.py --dry-run --draft /tmp/ebay-draft.json` to catch leftover `TODO`s, then publish for real. Show the live URL.

## Example: a typical crosspost run

**User:** `https://www.facebook.com/marketplace/item/1234567890`

**You:**
1. FB URL → crosspost feature. Run `fb_fetch.py` on the URL → get back title `"Vintage Levi's 501 jeans 32x34"`, fbCondition `"Used - Good"`, price `$45`, 4 photos in `~/.cache/ebay-lister/vintage-levis-501-jeans-32x34/`.
2. Polish the title to `"Vintage Levi's 501 Jeans Men's 32x34 Straight Leg Denim Distressed"` (front-loads keywords, ≤80 chars). Write the polished draft to `/tmp/ebay-draft.json`.
3. Run `ebay_taxonomy.py "Vintage Levi's 501 Jeans Men's 32x34 ..."` → top suggestions `11483 (Men's > Jeans)`, `175771 (Vintage > ...)`, `155182 (Specialty)`. Show all three. User picks `11483`.
4. Run `ebay_conditions.py 11483` → category accepts `USED_GOOD`. Map FB's `"Used - Good"` to `USED_GOOD`.
5. Confirm shipping settings — env has handlingDays=2, localPickup=false, shipInternationally=false, so only ask about weight and dimensions. User: "about 1 lb in a small mailer, maybe 12×9×2".
6. Show the proposed draft in chat. Wait for "publish" / "yes" / "go".
7. Run `ebay_publish.py --draft /tmp/ebay-draft.json`. Show the live URL it prints.

If anything fails (LSAS, missing aspect, condition rejection), check the Errors section below and recover — don't blindly retry.

## Errors and gotchas

- **Missing eBay business policies** (`Error 25709` or similar payment/return/fulfillment policy errors): account hasn't set up policies yet. Production users can use https://www.ebay.com/sh/policies in the seller hub UI; sandbox users have to use the Account API directly (the seller hub for policies isn't reliable on sandbox). Either way, the README's "Setup → step 6" section walks through opting in, creating the three policies, and registering an inventory location. After setup, write the resulting IDs into the user's `.env` (`EBAY_*_FULFILLMENT_POLICY_ID`, etc.) so they don't need to live in every draft.
- **Missing inventory location** (`Error 25007` or "merchant location key not found"): same root cause — first-time setup. Create one with `POST /sell/inventory/v1/location/{key}` (see README step 6e) and put `EBAY_MERCHANT_LOCATION_KEY=<key>` in `.env`.
- **Category requires aspects** (`Error 25002`, "item specific X is missing"): `ebay_publish.py` pre-checks this before creating anything and normally fails early with the exact list of missing aspects — add them under `aspects: { ... }` in the draft (single-element value lists) and retry. If a raw `25002` still reaches publishOffer, the pre-check call was skipped or errored (it's non-fatal by design); call `GET /commerce/taxonomy/v1/category_tree/{tree}/get_item_aspects_for_category?category_id=<id>` yourself to see what's needed.
- **Unfilled `TODO` placeholder** (`ebay_publish.py` exits before calling eBay): the from-scratch scaffold from `new_listing.py` still has placeholder values. The error lists each unfilled path — fill them in and retry.
- **EPS upload fails** (Trading API XML error from `ebay_eps.py`): usually the local image file is missing/corrupt or eBay's EPS service is having a hiccup. Confirm the local file exists and try again. EPS-hosted images expire after 30 days if not associated with an active listing — fine since we publish immediately.
- **`Invalid <ShippingPackage>`** (errorId 25101): the chosen shipping service doesn't accept CALCULATED rates with the given package, or `packageType` is incompatible with the service. The publish script omits packageType deliberately to avoid this. If it still fires, swap the shipping service (try `USPSParcel` or `USPSPriority`) or check that weight + dimensions are sane.
- **`LSAS validation failed`** when creating a fulfillment policy: eBay's Listing Shipping Advisor Service rejected a service code on this account. `USPSGroundAdvantage` is a known offender on some accounts despite being the modern code. Use `USPSParcel` instead (set `EBAY_SHIPPING_SERVICES=USPSParcel,USPSPriority`).
- **`invalid_scope`** at OAuth time on production: a requested OAuth scope isn't granted to the app on production. Check the SCOPES list in `ebay_auth.py` against what's allowed — `commerce.catalog.readonly` was previously a culprit and has been removed.
- **Token expired**: the publish/fetch scripts auto-refresh. If refresh itself fails, prompt the user to re-run `ebay_auth.py login`.
- **Sandbox confusion**: if the user sees a sandbox URL when they expected production (or vice versa), check `EBAY_ENV` in their `.env`. Don't change this for them without confirmation.

Crosspost-feature-only errors:

- **FB login expired** (`fb_fetch.py` returns mostly-empty fields): re-run `fb_session.py`.
- **FB security challenge mid-script**: the open browser will show a CAPTCHA or identity check. Have the user solve it manually, then re-run `fb_session.py` afterward to capture refreshed cookies.
- **Selector breakage on FB**: FB rewrites its DOM frequently. If `fb_fetch.py` returns mostly-empty fields, the selectors in that script need updating. Tell the user — don't pretend the data is good.

## Managing existing listings

For inspecting and tweaking items already on the account (no new listing), use `ebay_listings.py` plus a few direct Sell Inventory API calls. The scope is intentionally small — listing, withdrawing, simple price updates, republishing — because anything fancier is rare enough that ad-hoc curl is fine.

```
# Show everything on the account (defaults to EBAY_ENV from .env)
uv run ~/.claude/skills/ebay-lister/scripts/ebay_listings.py
uv run ~/.claude/skills/ebay-lister/scripts/ebay_listings.py --env production
uv run ~/.claude/skills/ebay-lister/scripts/ebay_listings.py --status PUBLISHED
uv run ~/.claude/skills/ebay-lister/scripts/ebay_listings.py --json   # for piping into jq / Python
```

Output columns: title, status (`PUBLISHED` / `UNPUBLISHED` / `NO_OFFER`), price, and the live listing URL or offer ID. Pass `--env <other>` to read the other environment without editing `.env`; the script promotes the right `EBAY_<env>_*` credentials internally.

Common follow-ups, all hitting `/sell/inventory/v1/offer/{offerId}`:

- **Withdraw a live listing** (pulls it from the marketplace; inventory item + offer stay on file): `POST .../withdraw`. State flips to `UNPUBLISHED`.
- **Republish a withdrawn offer** (same SKU, same offer, no recreate): `POST .../publish`. eBay issues a fresh listingId — the old URL doesn't come back.
- **Change price**: `updateOffer` is a full PUT replace, so `GET` the offer first, edit `pricingSummary.price.value`, drop read-only fields (`offerId`, `status`, `listing`), then PUT. A 25402 warning ("funds may be on hold") is just eBay's standard new-seller boilerplate, not a failure.
- **Delete entirely**: `DELETE .../offer/{offerId}` then `DELETE .../inventory_item/{sku}` if you also want the SKU gone.

Don't bulk-republish a pile of `UNPUBLISHED` offers without knowing why they went down — eBay sometimes auto-unpublishes for policy/aspect issues, and republishing without fixing the root cause will just fail or re-trigger the takedown.

## Reference files

- `references/ebay_field_map.md` — eBay condition codes, title rules, required offer fields
- `references/draft_schema.md` — JSON shape `ebay_publish.py` expects

## Why this skill exists

The user has a Claude Max plan, which covers Claude Code/chat usage but not separate Anthropic API billing. As a skill, the polish step happens in the conversation that's already paid for; only eBay-side calls run as code. Keep that constraint in mind — don't suggest external paid services unless the user explicitly opts in.
