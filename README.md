# fb-to-ebay

A [Claude Code](https://claude.com/claude-code) skill that cross-posts a Facebook Marketplace listing to eBay. You paste a Marketplace URL into Claude; it fetches the listing, polishes the title and description for eBay's conventions, suggests a category, and (after you approve) publishes via the eBay Sell APIs.

The "intelligence" — extracting fields from the page, normalizing copy, picking a category — happens inside your existing Claude Code session, so it doesn't cost anything beyond your Claude subscription. Only the eBay-side calls run as code.

**Direction is one-way: Facebook Marketplace → eBay.**

## How it works

```
You paste a FB Marketplace URL
        │
        ▼
Claude fetches the page (WebFetch) and extracts title, description,
price, condition, photo URLs
        │
        ▼
Claude rewrites the title (≤80 chars, search-friendly), expands the
description, and calls scripts/ebay_taxonomy.py to suggest categories
        │
        ▼
Claude shows the proposed draft in chat. You edit conversationally
("change condition to Used – Good, drop the price to $45")
        │
        ▼
On your approval, Claude runs scripts/ebay_publish.py which calls
createOrReplaceInventoryItem → createOffer → publishOffer
        │
        ▼
Live eBay listing URL printed in chat
```

## Install

Clone into your Claude skills directory:

```bash
git clone https://github.com/Govanator12/fb-to-ebay.git ~/.claude/skills/fb-to-ebay
```

Claude Code auto-discovers skills in `~/.claude/skills/`. The skill triggers when you paste a `facebook.com/marketplace` URL or ask Claude to "crosspost", "list on eBay", or "mirror" a listing.

## Setup

You need an eBay developer account and a one-time OAuth grant.

### 1. Get eBay developer credentials

1. Sign up at <https://developer.ebay.com/signin> (free).
2. Create an application keyset under "My Account → Application Keysets". You'll get an **App ID**, **Cert ID**, and **Dev ID** for both **sandbox** and **production**.
3. Set up a **RuName** (eBay's name for a registered redirect URI) under "User tokens → Get a token from eBay via your application". For local use, point the RuName at any HTTPS URL you control — even a static "auth successful" page works, since you'll paste the redirect URL back manually.

### 2. Configure `.env`

```bash
mkdir -p ~/.config/fb-to-ebay
cp ~/.claude/skills/fb-to-ebay/.env.example ~/.config/fb-to-ebay/.env
$EDITOR ~/.config/fb-to-ebay/.env
```

Fill in `EBAY_APP_ID`, `EBAY_CERT_ID`, `EBAY_DEV_ID`, `EBAY_RUNAME`. Leave `EBAY_ENV=sandbox` until you've done a full dry-run.

### 3. One-time OAuth login

```bash
uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_auth.py login
```

The script prints a URL. Open it in a browser, log in to eBay, grant consent. You'll be redirected to your RuName's URL — copy that full URL (including the `?code=...` query string) and paste it back into the script. Tokens are cached in `~/.config/fb-to-ebay/token.json` (mode `0600`). The refresh token is good for ~18 months.

### 4. Set up eBay business policies

eBay requires a payment, return, and fulfillment policy on your account before any listing can be published. Create them once at <https://www.bizpolicy.ebay.com> and grab the policy IDs — you'll include them in each draft (or wire them into your shell env if you only ever use one set).

## Usage

In Claude Code:

```
> https://www.facebook.com/marketplace/item/1234567890
```

That's it. The skill auto-triggers, Claude proposes a draft, you approve or edit, and it publishes.

You can also feed it a row from your Facebook data export CSV:

```
> Crosspost row 14 of ~/Downloads/marketplace_listings.csv to eBay
```

## Scripts

All three scripts are standalone — they declare their own dependencies via [PEP 723 inline metadata](https://peps.python.org/pep-0723/) and run with [uv](https://docs.astral.sh/uv/). No project venv to manage.

| Script | Purpose |
|---|---|
| `scripts/ebay_auth.py` | OAuth login + token refresh + `token` subcommand to print a current access token |
| `scripts/ebay_taxonomy.py` | Suggest top-3 eBay categories for a given title |
| `scripts/ebay_publish.py` | Publish a draft JSON to eBay; supports `--dry-run` |

## Repo layout

```
fb-to-ebay/
├── SKILL.md              # the manifest Claude reads when the skill triggers
├── README.md             # this file
├── .env.example          # template for ~/.config/fb-to-ebay/.env
├── .gitignore
├── scripts/
│   ├── ebay_auth.py
│   ├── ebay_taxonomy.py
│   └── ebay_publish.py
└── references/
    ├── ebay_field_map.md # condition codes, title rules, required fields
    └── draft_schema.md   # JSON shape ebay_publish.py expects
```

## Known limitations

- **Image hosting.** Facebook image URLs are signed and short-lived. If eBay can't fetch them server-side, the publish call will fail. Workaround for now: re-upload images to Imgur or another public host and paste those URLs into the draft. Adding eBay Picture Services (EPS) upload is a planned follow-up.
- **Auth wall on FB.** Many Marketplace pages require login to view. If `WebFetch` hits a login wall, Claude falls back to asking you to paste the title, description, price, and image URLs manually.
- **Sandbox vs production.** The skill defaults to sandbox. Confirm you've done a full sandbox dry-run before flipping `EBAY_ENV=production`.
- **One direction only.** This is FB → eBay. eBay → FB is not implemented (and would require browser automation since FB has no public listing-write API for individuals).

## License

MIT. Use freely, no warranty.
