---
name: fb-to-ebay
description: Cross-post a Facebook Marketplace listing to eBay. The user pastes a Marketplace URL or hands over a row from their FB data-export CSV; you fetch and normalize the listing, propose an eBay-formatted draft, then publish it via the eBay Sell APIs. Use this skill whenever the user pastes a facebook.com/marketplace URL, mentions "crossposting" or "mirroring" a listing to eBay, asks to "list this on eBay", uploads a Facebook data export, or describes any item they want to put on eBay that already exists on Marketplace — even if they don't say the word "skill".
---

# fb-to-ebay

A workflow for taking one of the user's Facebook Marketplace listings and republishing it on eBay. The Marketplace side is read manually (no public FB API); the eBay side is published via the Sell Inventory API. The "intelligence" — extracting fields from a noisy HTML page, polishing the title, picking a category, mapping condition strings — is your job, not a separate LLM call.

## Mental model

The user already wrote and listed the item on Facebook. They don't want to retype it. Your job is high-fidelity translation: keep the meaning, change the formatting to match eBay's conventions and required fields. Default to the user's voice, not yours. When you have to invent something they didn't write (a longer description, a category guess), be transparent about it and let them correct you before anything goes live.

eBay listings are **expensive to fix once published** (relisting costs fees, search ranking resets). Always show a proposed draft and wait for explicit approval before running the publish script. Do not assume "looks fine, ship it."

## Prerequisites (check first)

Before doing real work, confirm setup:

1. `~/.config/fb-to-ebay/.env` exists with `EBAY_APP_ID`, `EBAY_CERT_ID`, `EBAY_DEV_ID`, `EBAY_RUNAME`, and `EBAY_ENV` (sandbox or production). If missing, point the user at `.env.example` in the skill directory and stop.
2. `~/.config/fb-to-ebay/token.json` exists. If missing, tell the user they need to run `uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_auth.py login` once and follow the printed URL — then ask before running it on their behalf, since it opens a browser.
3. `EBAY_ENV` value: surface this in chat ("publishing to **sandbox**") so the user never confuses environments. Default to sandbox in any first-time interaction.

## Workflow

### 1. Get the source

The user gives you one of:

- **A Marketplace URL** → use `WebFetch` with a prompt that asks for title, price, condition string, description text, photo URLs, and seller location. If the response is a login wall or empty, fall back to step 1a.
- **A path to a FB data-export CSV** → use `Read` on the row(s) the user names. Marketplace exports include the same fields but cleanly labeled.
- **Raw text the user pasted** → just parse what they wrote.

**1a. Login wall fallback.** If `WebFetch` returns a login screen, ask the user to open the listing in their browser, copy the title/description/price into chat, and paste each photo URL on its own line. Don't keep retrying the fetch.

### 2. Normalize for eBay

Read `references/ebay_field_map.md` for the exact rules. The high-level shape:

- **Title**: ≤80 chars, keywords front-loaded (brand, model, size, color), no ALL CAPS, no emoji.
- **Description**: expand FB's terse style into a few short paragraphs. Lead with what it is and condition; follow with dimensions/specs; close with pickup/shipping notes if the user mentioned them. Plain text or simple HTML (`<p>`, `<ul>`).
- **Condition**: map FB's free-text ("like new", "used – good") to one of eBay's enumerated condition IDs.
- **Price**: keep the FB number unless the user adjusts it.
- **Images**: collect the URLs but don't upload yet — the publish script does that.

### 3. Suggest a category

Run `uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_taxonomy.py "<polished title>"`. It prints the top 3 category suggestions as JSON. Show all three to the user with their full category paths and let them pick — don't auto-select even when the top result is obvious. Categories are sticky and hard to change after publish.

### 4. Show the draft

Format the proposed listing in chat as a structured block — title, price, condition, category, description preview, image count. Ask for approval or edits in conversational terms ("ready to publish, or want to tweak anything?"). Don't go to step 5 until the user says yes.

### 5. Publish

Write the approved draft to a temp JSON file matching `references/draft_schema.md`. Then run:

```
uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_publish.py --draft <path>
```

The script prints the live listing URL on success. Show it to the user.

## Errors

- **Missing eBay business policies** (`Error 25709` or similar payment/return/fulfillment policy errors): the user's eBay account hasn't set up policies yet. Send them to https://www.bizpolicy.ebay.com to create them, then retry. Don't try to create policies via API — the UI is faster and the user only needs to do it once.
- **Image upload fails**: usually because the FB image URL has expired (they're signed and short-lived). Ask the user to refresh the source URL or paste new image links.
- **Token expired**: the publish script auto-refreshes. If refresh itself fails, prompt the user to re-run `ebay_auth.py login`.
- **Sandbox confusion**: if the user sees a sandbox URL when they expected production (or vice versa), check `EBAY_ENV` in their `.env`. Don't change this for them without confirmation.

## Reference files

- `references/ebay_field_map.md` — condition ID table, title rules, description conventions, common required fields per category type
- `references/draft_schema.md` — exact JSON shape `ebay_publish.py` expects

## Why this skill exists

The user has a Claude Max plan, which covers Claude Code/chat usage but not separate Anthropic API billing. Building this as a standalone app would have meant paying per-token for every listing polish. As a skill, the polish step happens in the conversation that's already paid for; only eBay-side calls run as code. Keep that constraint in mind — don't suggest external paid services unless the user explicitly opts in.
