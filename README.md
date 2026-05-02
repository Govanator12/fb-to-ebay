# fb-to-ebay

A [Claude Code](https://claude.com/claude-code) skill that cross-posts a Facebook Marketplace listing to eBay. You paste a Marketplace URL into Claude; it fetches the listing, polishes the title and description for eBay's conventions, suggests a category, and (after you approve) publishes via the eBay Sell APIs.

The "intelligence" — extracting fields from the page, normalizing copy, picking a category — happens inside your existing Claude Code session, so it doesn't cost anything beyond your Claude subscription. Only the eBay-side calls run as code.

**Direction is one-way: Facebook Marketplace → eBay.**

> **Looking for bidirectional support, or want to fix the FB image-expiry / login-wall problems?** See the experimental [`playwright-version`](https://github.com/Govanator12/fb-to-ebay/tree/playwright-version) branch — it adds Playwright browser automation to scrape FB more reliably and to post **eBay → Facebook**. Comes with selector brittleness and account-safety tradeoffs; check that branch's README before using.

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

You need an eBay developer account, `uv`, and a one-time OAuth grant. ~10 minutes.

### 1. Install `uv`

The scripts use [PEP 723 inline-dependency](https://peps.python.org/pep-0723/) headers, so they self-install via `uv`. The Astral installer is the fastest path:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart your shell so `~/.local/bin` is on PATH. (Avoid the `astral-uv` snap — it's classic-confinement and lags upstream.)

### 2. Get eBay developer credentials

1. Sign up at <https://developer.ebay.com/signin> (free) and complete the developer-program approval (usually instant; sometimes a short queue).
2. Create an application keyset under "My Account → Application Keysets". You get separate **App ID**, **Cert ID**, **Dev ID** sets for **Sandbox** and **Production**. Use the Sandbox set first.
3. Set up a **RuName** under "User Tokens → Get a Token from eBay via Your Application". When prompted, choose **OAuth** (not Auth'n'Auth — that's the old Trading-API flow we don't use). Provide three HTTPS URLs:
   - **Auth accepted URL**, **Auth declined URL**, **Privacy policy URL**
   - eBay only stores the strings; it doesn't fetch them. After consent your browser will land at `<accepted-url>?code=...` and you'll copy the URL from the address bar.
   - **`https://localhost/...` URLs are rejected** by eBay's portal. Use `https://example.com/accepted` etc. instead.
4. After saving, eBay shows your **RuName** — a string like `Yourname-yourapp-SBX-abc123def-1234abcd`. That string (NOT the URLs you typed) is the value of `EBAY_RUNAME`.

### 3. Register a sandbox test user

Production uses your real eBay account, but **Sandbox is a separate database with fake users**. You must register one before the OAuth flow can complete.

1. Go to <https://developer.ebay.com/sandbox/register> and create a user with any username + password.
2. eBay automatically prepends `TESTUSER_` to your username. So if you registered as `myusername`, your sign-in username is `TESTUSER_myusername`. **This is the #1 reason "wrong password" errors happen on the OAuth flow** — eBay returns a generic password error when it can't find the user.
3. Manage / reset passwords for your sandbox users at <https://developer.ebay.com/develop/tools/sandbox>.

### 4. Configure `.env`

```bash
mkdir -p ~/.config/fb-to-ebay
cp ~/.claude/skills/fb-to-ebay/.env.example ~/.config/fb-to-ebay/.env
$EDITOR ~/.config/fb-to-ebay/.env
```

Fill in `EBAY_APP_ID`, `EBAY_CERT_ID`, `EBAY_DEV_ID`, `EBAY_RUNAME`. Leave `EBAY_ENV=sandbox` until you've done a full dry-run.

### 5. One-time OAuth login

```bash
uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_auth.py login
```

The script prints a URL. Open it in a browser, log in with your `TESTUSER_<name>` credentials, grant consent. You'll be redirected to your auth-accepted URL — your browser will probably show a 404 (since `example.com` doesn't host that page), but the URL in the address bar is what matters. Copy the **entire** URL (with the `?code=...` query string) and paste it back into the script.

**If your shell can't pipe stdin to interactive prompts** (e.g. Claude Code's bash input mode), use the two-step form: run `login` to get the URL, then complete the exchange with the URL as a flag:

```bash
uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_auth.py login --redirect-url "https://example.com/accepted?code=..."
```

The double quotes are required — the URL contains `&` and `=` that the shell would otherwise interpret.

The token is cached at `~/.config/fb-to-ebay/token.json` (mode `0600`). The access token lasts 2 hours and auto-refreshes; the refresh token is good for ~18 months. Scopes granted: `api_scope`, `sell.inventory`, `sell.account`, `commerce.catalog.readonly`. If you change `SCOPES` in `ebay_auth.py`, re-run `login` to mint a new token — refresh won't add scopes.

### 6. Opt into Business Policies, create policies, register a location

eBay requires three policies (payment, return, fulfillment) **and** at least one inventory location before any listing can be published. Sandbox needs API setup; production users can do most of this through the seller hub UI but the API works there too.

For **sandbox**, the seller-hub UI for policies isn't reliable, so use the Account API directly. Run all of this in one shell session — `$TOKEN` reuses your saved access token:

```bash
HOST=api.sandbox.ebay.com   # or api.ebay.com for production
TOKEN=$(uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_auth.py token)

# 6a. Opt in to business-policy management (sandbox only — production is opted in by default)
curl -X POST https://$HOST/sell/account/v1/program/opt_in \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"programType":"SELLING_POLICY_MANAGEMENT"}'

# 6b. Fulfillment policy: USPS Priority, free domestic shipping, 1-day handling
curl -X POST https://$HOST/sell/account/v1/fulfillment_policy \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "Content-Language: en-US" \
  -d '{
    "name":"Default US Fulfillment","marketplaceId":"EBAY_US",
    "categoryTypes":[{"name":"ALL_EXCLUDING_MOTORS_VEHICLES"}],
    "handlingTime":{"value":1,"unit":"DAY"},
    "shippingOptions":[{"optionType":"DOMESTIC","costType":"FLAT_RATE",
      "shippingServices":[{"sortOrder":1,"shippingCarrierCode":"USPS","shippingServiceCode":"USPSPriority","freeShipping":true}]}]}'

# 6c. Payment policy: immediate payment via eBay Managed Payments
curl -X POST https://$HOST/sell/account/v1/payment_policy \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "Content-Language: en-US" \
  -d '{
    "name":"Default Payment Policy","marketplaceId":"EBAY_US",
    "categoryTypes":[{"name":"ALL_EXCLUDING_MOTORS_VEHICLES"}],"immediatePay":true}'

# 6d. Return policy: 30-day money-back, buyer pays return shipping
curl -X POST https://$HOST/sell/account/v1/return_policy \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "Content-Language: en-US" \
  -d '{
    "name":"Default Return Policy","marketplaceId":"EBAY_US",
    "returnsAccepted":true,"returnPeriod":{"value":30,"unit":"DAY"},
    "refundMethod":"MONEY_BACK","returnShippingCostPayer":"BUYER"}'

# 6e. Register an inventory location (replace the address with yours)
curl -X POST https://$HOST/sell/inventory/v1/location/default \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "Content-Language: en-US" \
  -d '{
    "location":{"address":{"country":"US","postalCode":"98101","stateOrProvince":"WA","city":"Seattle"}},
    "name":"Default location","merchantLocationStatus":"ENABLED","locationTypes":["WAREHOUSE"]}'
```

Each policy call returns a JSON body with the policy ID (`fulfillmentPolicyId`, `paymentPolicyId`, `returnPolicyId`). Append them to your `.env` so `ebay_publish.py` doesn't make you paste them into every draft:

```
EBAY_FULFILLMENT_POLICY_ID=<id from 6b>
EBAY_PAYMENT_POLICY_ID=<id from 6c>
EBAY_RETURN_POLICY_ID=<id from 6d>
EBAY_MERCHANT_LOCATION_KEY=default
```

### 7. Verify

```bash
# Auth + Taxonomy API
uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_taxonomy.py "Vintage leather jacket"

# Confirm policy opt-in landed
curl -sH "Authorization: Bearer $(uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_auth.py token)" \
  https://api.sandbox.ebay.com/sell/account/v1/program/get_opted_in_programs
```

The taxonomy call should print three category suggestions as JSON. The opt-in check should show `SELLING_POLICY_MANAGEMENT` in the programs list. If either returns a 403, your token is missing a scope — re-run `ebay_auth.py login`.

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
