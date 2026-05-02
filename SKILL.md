---
name: fb-to-ebay
description: Cross-post a listing between Facebook Marketplace and eBay in either direction. The user pastes a Marketplace or eBay URL; you fetch and normalize the listing, propose a draft on the destination platform, then publish — eBay via the Sell APIs, Facebook via Playwright browser automation. Use this skill whenever the user pastes a facebook.com/marketplace URL, an ebay.com/itm URL, mentions "crossposting" or "mirroring", asks to "list this on eBay" or "post this on FB", or describes any listing they want to mirror to the other platform — even if they don't say the word "skill".
---

# fb-to-ebay (Playwright bidirectional)

A workflow for mirroring listings between Facebook Marketplace and eBay in either direction. The eBay side uses well-documented Sell APIs. The Facebook side uses Playwright with a captured browser session, since FB has no public listing API for individual sellers — for reads, this gets past the login wall and downloads images locally before they expire; for writes, it pre-fills the create-listing form so the user can review and submit.

The "intelligence" — extracting fields from a noisy DOM, polishing the title, mapping conditions, choosing categories — is your job in conversation, not a separate LLM call.

## Mental model

The user already wrote and listed the item on one platform. They don't want to retype it. Your job is high-fidelity translation: keep the meaning, change the formatting to match the destination's conventions and required fields. Default to the user's voice, not yours. When you have to invent something they didn't write (a longer description, a category guess), be transparent about it and let them correct you before anything goes live.

eBay listings are **expensive to fix once published** (relisting fees, search ranking resets); FB listings put your **personal account at risk** of automation flagging on every post. Either way, always show a proposed draft and wait for explicit approval before publishing. Never assume "looks fine, ship it."

## Direction detection

Decide direction from what the user pasted:
- **`facebook.com/marketplace/item/...`** → FB → eBay
- **`ebay.com/itm/...`** or a 9-14 digit eBay item ID → eBay → FB
- Ambiguous? Ask the user.

## Prerequisites (check first)

Before doing real work:

1. `~/.config/fb-to-ebay/.env` exists with `EBAY_APP_ID`, `EBAY_CERT_ID`, `EBAY_DEV_ID`, `EBAY_RUNAME`, `EBAY_ENV`. Missing? Point at `.env.example` in the skill directory and stop.
2. `~/.config/fb-to-ebay/token.json` exists. Missing? Tell the user to run `uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_auth.py login` and ask before running it on their behalf — it opens a browser. (Two-step form for non-interactive shells: print the URL with `login`, complete with `login --redirect-url "<pasted-url>"`.)
3. **For any FB-side step:** `~/.config/fb-to-ebay/fb_session.json` exists. Missing? Tell the user to run `uv run ~/.claude/skills/fb-to-ebay/scripts/fb_session.py` (one-time, ~30 seconds, manual login). Ask before running.
4. **For any FB-side step:** Playwright's Chromium binary is installed. If not, the script will print the install command (`uv run --with playwright playwright install chromium`) — don't try to install it silently.
5. `EBAY_ENV` value: surface it in chat ("publishing to **sandbox**") so the user never confuses environments. Default sandbox in any first-time interaction.

## Workflow A: Facebook → eBay

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

### 4. Resolve image hosting

eBay needs publicly-reachable HTTPS image URLs. The FB URLs in `imageUrls` are signed and short-lived; eBay typically can't fetch them server-side. Ask the user to either re-host the downloaded images (paths in `localImages`) on Imgur/S3/their own server and paste the new URLs, or skip images for the first try and add them later via the eBay UI. Don't pretend the FB URLs will work — they usually don't.

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

Write the approved draft to a temp JSON file matching `references/draft_schema.md` (eBay-side schema), then:

```
uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_publish.py --draft /tmp/ebay-draft.json
```

Policy IDs and merchant location auto-fill from `.env` if not in the draft. The script prints the live listing URL on success.

## Workflow B: eBay → Facebook

### 1. Fetch the eBay listing

```
uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_fetch.py <ebay-url-or-itemid> --out /tmp/ebay-draft.json
```

Calls the Browse API and downloads eBay's hosted images locally. Output draft includes `ebayCategoryPath`, `ebayCondition`, etc. — eBay's vocabulary, not FB's.

### 2. Normalize for FB

Read `references/fb_field_map.md` for the rules:

- **Title**: pass through (FB allows 100 chars).
- **Description**: strip HTML from the eBay description (FB renders plain text only). Add the eBay `conditionDescription` here if it had one.
- **Price**: pass through (just the number).
- **Condition**: map eBay enum → FB display string (`USED_EXCELLENT` → `"Used - Like New"`).
- **Category**: map eBay's deep category path → one of FB's flat top-level options. The reference lists common mappings; ask the user when ambiguous.

### 3. Show the draft

Display title, price, FB category, FB condition, description preview, image count. Wait for explicit approval.

### 4. Pre-fill the FB form

Write the FB-shaped draft to a temp JSON file (keys: `title`, `price`, `fbCategory`, `fbCondition`, `description`, `localImages`), then:

```
uv run ~/.claude/skills/fb-to-ebay/scripts/fb_post.py --draft /tmp/fb-draft.json
```

Opens a real Chromium window with the create-listing form pre-filled. **By default the script does not click Publish** — it leaves the form open for the user to review and submit. They press Enter in the terminal when done to close the browser. The user can pass `--auto-publish` after several successful manual runs if they trust the selectors.

## Errors and gotchas

- **Missing eBay business policies** (`Error 25709` or similar payment/return/fulfillment policy errors): account hasn't set up policies yet. Production users can use https://www.ebay.com/sh/policies in the seller hub UI; sandbox users have to use the Account API directly (the seller hub for policies isn't reliable on sandbox). Either way, the README's "Setup → step 6" section walks through opting in, creating the three policies, and registering an inventory location. After setup, write the resulting IDs into the user's `.env` (`EBAY_FULFILLMENT_POLICY_ID`, `EBAY_PAYMENT_POLICY_ID`, `EBAY_RETURN_POLICY_ID`) so they don't need to live in every draft.
- **Missing inventory location** (`Error 25007` or "merchant location key not found"): same root cause — first-time setup. Create one with `POST /sell/inventory/v1/location/{key}` (see README step 6e) and put `EBAY_MERCHANT_LOCATION_KEY=<key>` in `.env`.
- **Category requires aspects** (`Error 25002`, "item specific X is missing"): the chosen `categoryId` requires structured item-specifics. Call `GET /commerce/taxonomy/v1/category_tree/{tree}/get_item_aspects_for_category?category_id=<id>` to see what's needed, ask the user for the values, add them under `aspects: { ... }` in the draft, retry.
- **Image upload fails on eBay**: usually the FB image URL has expired (signed/short-lived). Ask the user to re-host the local copies.
- **FB login expired** (fb_fetch returns mostly-empty fields, fb_post can't reach the form): re-run `fb_session.py`.
- **FB security challenge mid-script**: the open browser will show a CAPTCHA or identity check. Have the user solve it manually, then press Enter to continue. Re-run `fb_session.py` afterward to capture refreshed cookies.
- **Selector breakage on FB**: FB rewrites its DOM frequently. If `fb_fetch.py` returns mostly-empty fields, or `fb_post.py` warns about fields it couldn't fill, the selectors in those scripts need updating. Tell the user — don't pretend the data is good.
- **eBay listing not found via Browse API**: usually means the listing was ended or the ID is wrong. Confirm the URL.
- **Token expired**: the publish/fetch scripts auto-refresh. If refresh itself fails, prompt the user to re-run `ebay_auth.py login`.
- **Sandbox confusion**: if the user sees a sandbox URL when they expected production (or vice versa), check `EBAY_ENV` in their `.env`. Don't change this for them without confirmation.

## Account-safety notes for the FB side

- Use the headed browser (default for `fb_post.py`); headless triggers more bot detection.
- Don't bulk-post. Spread runs over hours — rate matters more than volume.
- The user is putting their personal FB account at risk every time `fb_post.py` runs. Be explicit about that the first time it comes up.

## Reference files

- `references/ebay_field_map.md` — eBay condition codes, title rules, required offer fields
- `references/fb_field_map.md` — FB condition strings, category list, eBay-to-FB mapping
- `references/draft_schema.md` — JSON shapes (eBay-side, FB-side, intermediate fetch output)

## Why this skill exists

The user has a Claude Max plan, which covers Claude Code/chat usage but not separate Anthropic API billing. As a skill, the polish step happens in the conversation that's already paid for; only platform calls run as code. Keep that constraint in mind — don't suggest external paid services unless the user explicitly opts in.
