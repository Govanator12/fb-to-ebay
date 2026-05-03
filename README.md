# fb-to-ebay

A [Claude Code](https://claude.com/claude-code) skill that crossposts a Facebook Marketplace listing to eBay. You paste a Marketplace URL into Claude; it scrapes the listing, polishes the title and description for eBay's conventions, picks a category, and (after you approve) publishes via the eBay Sell APIs.

The "intelligence" — extracting fields, normalizing copy, mapping conditions, picking a category — happens inside your existing Claude Code session, so it doesn't cost anything beyond your Claude subscription. Only the eBay-side calls run as code.

## How it works

```
┌──────────────────────────────────────────────────────────────────┐
│  You paste a facebook.com/marketplace URL into Claude            │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│ fb_fetch.py (Playwright + saved FB session)                      │
│ - extracts title, description, price, condition, photos          │
│ - downloads images locally to ~/.cache/fb-to-ebay/<slug>/        │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│ Claude polishes title for eBay search, expands description,      │
│ runs ebay_taxonomy.py for category, ebay_conditions.py for       │
│ valid condition enums, asks you for weight + dimensions,         │
│ shows the draft, waits for explicit approval                     │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│ ebay_publish.py (Sell Inventory API)                             │
│ - mints a per-listing fulfillment policy (calculated rates)      │
│ - uploads photos to eBay Picture Services (EPS)                  │
│ - createInventoryItem → createOffer → publishOffer               │
│ - rolls back orphans on failure                                  │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
                         live eBay URL
```

The reverse direction (eBay → FB) is **not implemented** in this repo — the skill is one-way, FB → eBay. FB has no public listing-write API for individuals; posting on FB requires Playwright form-driving, which is feasible but hasn't been validated end-to-end. If you want it later, contributions welcome.

## Install

```bash
git clone https://github.com/Govanator12/fb-to-ebay.git ~/.claude/skills/fb-to-ebay
```

Claude Code auto-discovers skills in `~/.claude/skills/`. The skill triggers when you paste a `facebook.com/marketplace` URL or ask Claude to "crosspost", "mirror", or "list this on eBay".

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
   - **Production keyset blocked by "Marketplace Account Deletion" requirement?** New developer accounts can't create production keysets until they comply with eBay's marketplace deletion notification process. If you're not storing other users' eBay data (which is the case for this skill — you only manage your own listings), apply for the exemption: go to <https://developer.ebay.com/marketplace-account-deletion>, toggle **"Not persisting eBay data"** ON, pick the matching exemption reason, write a one-sentence justification (e.g. "manages only my own seller listings; no buyer/third-party data persisted"), submit. The exemption is usually auto-granted instantly. Sandbox keysets are not blocked by this.
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

Fill in `EBAY_SANDBOX_APP_ID`, `EBAY_SANDBOX_CERT_ID`, `EBAY_SANDBOX_DEV_ID`, `EBAY_SANDBOX_RUNAME` with your sandbox keyset values. Leave `EBAY_ENV=sandbox`. The matching `EBAY_PRODUCTION_*` block stays empty for now — fill it in later (see [Going to production](#going-to-production)) so a single `.env` holds both environments and you can flip between them just by changing `EBAY_ENV`.

> **Backward-compat:** the bare unprefixed keys (`EBAY_APP_ID`, etc.) still work as a fallback if no env-prefixed value is set. Old single-env setups don't need to be migrated.

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

The token is cached per-environment at `~/.config/fb-to-ebay/token-<env>.json` (e.g. `token-sandbox.json`, mode `0600`) so logging into one environment doesn't clobber the other. The access token lasts 2 hours and auto-refreshes; the refresh token is good for ~18 months. Scopes granted: `api_scope`, `sell.inventory`, `sell.account`. If you change `SCOPES` in `ebay_auth.py`, re-run `login` to mint a new token — refresh won't add scopes.

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

# 6b. Fulfillment policy: buyer-pays calculated USPS Parcel + Priority, 2-day handling.
# Notes:
# - costType CALCULATED requires the inventory item to have packageWeightAndSize.
#   ebay_publish.py wires that in from the draft's weightLbs + boxDimensionsIn.
# - USPSParcel is the canonical "ground" code that round-trips cleanly. eBay
#   silently renames USPSStandardPost -> USPSParcel on storage, which breaks
#   policy reuse. USPSGroundAdvantage is rejected by eBay's LSAS validator on
#   some accounts. USPSParcel works in both sandbox and production.
# - This is just a baseline; ebay_publish.py also creates per-listing policies
#   when the draft has shipping overrides (handling/pickup/intl).
curl -X POST https://$HOST/sell/account/v1/fulfillment_policy \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "Content-Language: en-US" \
  -d '{
    "name":"fb2ebay-default","marketplaceId":"EBAY_US",
    "categoryTypes":[{"name":"ALL_EXCLUDING_MOTORS_VEHICLES"}],
    "handlingTime":{"value":2,"unit":"DAY"},
    "shippingOptions":[{"optionType":"DOMESTIC","costType":"CALCULATED",
      "shippingServices":[
        {"sortOrder":1,"shippingCarrierCode":"USPS","shippingServiceCode":"USPSParcel","freeShipping":false,"buyerResponsibleForShipping":false,"buyerResponsibleForPickup":false},
        {"sortOrder":2,"shippingCarrierCode":"USPS","shippingServiceCode":"USPSPriority","freeShipping":false,"buyerResponsibleForShipping":false,"buyerResponsibleForPickup":false}
      ]}],
    "pickupDropOff":false,"globalShipping":false}'

# 6c. Payment policy: immediate payment via eBay Managed Payments
curl -X POST https://$HOST/sell/account/v1/payment_policy \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "Content-Language: en-US" \
  -d '{"name":"fb2ebay-default","marketplaceId":"EBAY_US",
       "categoryTypes":[{"name":"ALL_EXCLUDING_MOTORS_VEHICLES"}],"immediatePay":true}'

# 6d. Return policy: no returns by default. (Most categories accept this; some
# — newer electronics — require returns. Edit later if you hit that.) If you
# prefer 30-day money-back, swap returnsAccepted to true and add
# "returnPeriod":{"value":30,"unit":"DAY"},"refundMethod":"MONEY_BACK",
# "returnShippingCostPayer":"BUYER" to the body.
curl -X POST https://$HOST/sell/account/v1/return_policy \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "Content-Language: en-US" \
  -d '{"name":"fb2ebay-default","marketplaceId":"EBAY_US",
       "categoryTypes":[{"name":"ALL_EXCLUDING_MOTORS_VEHICLES"}],"returnsAccepted":false}'

# 6e. Register an inventory location (replace the address with yours)
curl -X POST https://$HOST/sell/inventory/v1/location/default \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "Content-Language: en-US" \
  -d '{
    "location":{"address":{"country":"US","postalCode":"98101","stateOrProvince":"WA","city":"Seattle"}},
    "name":"Default location","merchantLocationStatus":"ENABLED","locationTypes":["WAREHOUSE"]}'
```

Each policy call returns a JSON body with the policy ID (`fulfillmentPolicyId`, `paymentPolicyId`, `returnPolicyId`). Append them to your `.env` under the env-prefixed keys so a single `.env` can carry both sandbox and production:

```
EBAY_SANDBOX_FULFILLMENT_POLICY_ID=<id from 6b>
EBAY_SANDBOX_PAYMENT_POLICY_ID=<id from 6c>
EBAY_SANDBOX_RETURN_POLICY_ID=<id from 6d>
EBAY_SANDBOX_MERCHANT_LOCATION_KEY=default
```

(Use the `EBAY_PRODUCTION_*` prefix when you do the same setup against production. Bare `EBAY_FULFILLMENT_POLICY_ID` etc. still work as a backward-compat fallback.)

### 7. Verify

```bash
# Auth + Taxonomy API
uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_taxonomy.py "Vintage leather jacket"

# Confirm policy opt-in landed
curl -sH "Authorization: Bearer $(uv run ~/.claude/skills/fb-to-ebay/scripts/ebay_auth.py token)" \
  https://api.sandbox.ebay.com/sell/account/v1/program/get_opted_in_programs
```

The taxonomy call should print three category suggestions as JSON. The opt-in check should show `SELLING_POLICY_MANAGEMENT` in the programs list. If either returns a 403, your token is missing a scope — re-run `ebay_auth.py login`.

### 8. Install Playwright's Chromium binary (one-time)

```bash
uv run --with playwright playwright install chromium
```

`fb_fetch.py` uses Playwright to scrape Marketplace listings (FB has no public read API). The Chromium download is ~170 MB; the install is one-time per machine.

### 9. Capture a Facebook session

```bash
uv run ~/.claude/skills/fb-to-ebay/scripts/fb_session.py
```

A real Chromium window opens. Log in to FB by hand (handles 2FA / CAPTCHA naturally). When you can see your news feed, return to the terminal and press Enter. Cookies are cached at `~/.config/fb-to-ebay/fb_session.json`. Re-run any time the session expires (FB usually keeps you logged in for weeks).

## Going to production

After steps 1-9 give you a working sandbox, the move to production looks like this:

1. **Confirm Managed Payments is active on your real eBay account** at <https://www.ebay.com/sh/ovw> → Payments. New sellers need to complete bank-account linking + identity verification before any `publishOffer` call will succeed.
2. **Create a production application keyset** at <https://developer.ebay.com/my/keys> (Production tab). New developer accounts may be blocked by the marketplace-deletion requirement — see step 2 above.
3. **Create a production RuName** the same way as the sandbox one. Production tends to want a real privacy-policy URL eventually, but the same `https://example.com/...` placeholders work for getting started.
4. **Add the prod values** to `.env` under `EBAY_PRODUCTION_APP_ID`, `EBAY_PRODUCTION_CERT_ID`, `EBAY_PRODUCTION_DEV_ID`, `EBAY_PRODUCTION_RUNAME`.
5. **Flip `EBAY_ENV=production`** and re-run `ebay_auth.py login` to mint a `token-production.json` against your real eBay account.
6. **Run the same step-6 setup** (opt-in + 3 policies + location) but against `api.ebay.com` instead of `api.sandbox.ebay.com`. Save the new IDs under `EBAY_PRODUCTION_*` keys in `.env`.
7. **Production-only quirks to know about**:
   - `USPSGroundAdvantage` is rejected by eBay's LSAS validator on some accounts even though it's the "official" 2023 USPS rebrand. `USPSParcel` works everywhere — that's the default.
   - `commerce.catalog.readonly` scope requires explicit eBay approval and is unused by this skill (already removed).
   - First listing incurs a free insertion fee (most casual sellers get ~250/month free), then ~13% final-value fee + payment-processing fee on sale.
   - Once a real listing publishes, real buyers can buy it. Be ready to ship within your handling-time SLA.

A single `.env` carries both sets of credentials side-by-side. Switch envs by changing only the `EBAY_ENV=` line — no need to re-edit anything else.

## Usage

In Claude Code, paste a Facebook Marketplace listing URL:

```
> https://www.facebook.com/marketplace/item/1234567890
```

The skill auto-triggers, runs `fb_fetch.py`, proposes a draft, you approve or edit, and it publishes to eBay. See `SKILL.md` for the full workflow Claude follows.

## Non-US sellers

The defaults assume a US-based seller posting to `EBAY_US` with USPS shipping in pound/inch units. To use a different marketplace, override these env vars in `.env`:

| Setting | US default | What to change for others |
|---|---|---|
| `EBAY_MARKETPLACE_ID` | `EBAY_US` | `EBAY_GB`, `EBAY_AU`, `EBAY_CA`, `EBAY_DE`, etc. |
| `EBAY_SHIPPING_SERVICES` | `USPSParcel,USPSPriority` | UK: `RoyalMailFirstClassStandard,RoyalMailSecondClassStandard`<br>AU: `AU_StandardDelivery,AU_ExpressDelivery`<br>(Find valid codes via the Trading API `GeteBayDetails` call with `DetailName=ShippingServiceDetails`.) |
| `EBAY_WEIGHT_UNIT` | `POUND` | `KILOGRAM`, `GRAM`, `OUNCE` |
| `EBAY_DIMENSION_UNIT` | `INCH` | `CENTIMETER` |
| Currency in drafts | `USD` | Set `price.currency` in each draft (`GBP`, `EUR`, `AUD`, etc.) |
| Inventory location address (step 6e) | Seattle, WA | Replace with your real ship-from address before running the curl |

The default fulfillment policy in step 6b also assumes USPS — replace `shippingServiceCode` and `shippingCarrierCode` values to match your carrier before running it.

## Scripts

All scripts are standalone — they declare their own dependencies via [PEP 723 inline metadata](https://peps.python.org/pep-0723/) and run with [uv](https://docs.astral.sh/uv/). No project venv to manage.

| Script | Purpose |
|---|---|
| `scripts/ebay_auth.py` | OAuth login + token refresh + `token` subcommand. Per-env tokens (`token-sandbox.json` / `token-production.json`); supports `--redirect-url` for non-interactive shells. |
| `scripts/ebay_taxonomy.py` | Suggest top-3 eBay categories for a title via the Commerce Taxonomy API |
| `scripts/ebay_conditions.py` | List the valid condition IDs + Inventory-API enums for a chosen category (call before picking a condition) |
| `scripts/ebay_eps.py` | Upload local image files to eBay Picture Services, return eBay-hosted URLs (called automatically by `ebay_publish`) |
| `scripts/ebay_publish.py` | Publish a draft via the Inventory API chain; auto-uploads `localImages` via EPS, mints per-listing fulfillment policies, rolls back orphans on failure; supports `--dry-run` |
| `scripts/fb_session.py` | One-time interactive FB login (headed Chromium); saves session cookies |
| `scripts/fb_fetch.py` | Scrape a Marketplace listing URL via Playwright using the saved session, download images locally |

## Repo layout

```
fb-to-ebay/
├── SKILL.md              # the manifest Claude reads when the skill triggers
├── README.md             # this file
├── .env.example          # template for ~/.config/fb-to-ebay/.env
├── .gitignore
├── scripts/
│   ├── ebay_auth.py        # OAuth login + per-env token cache
│   ├── ebay_taxonomy.py    # category suggestions
│   ├── ebay_conditions.py  # valid conditions for a category
│   ├── ebay_eps.py         # upload local images to eBay Picture Services
│   ├── ebay_publish.py     # publish chain (auto-EPS, dynamic policy, rollback)
│   ├── fb_session.py       # one-time FB login
│   └── fb_fetch.py         # scrape a Marketplace listing
└── references/
    ├── ebay_field_map.md   # condition codes, title rules, required fields
    └── draft_schema.md     # JSON shape ebay_publish.py expects
```

## Account-safety warning

`fb_fetch.py` drives a real Chromium session against your personal Facebook account to scrape Marketplace listings. FB actively detects automated activity and can flag, restrict, or ban accounts in extreme cases. Mitigations:

- Run at human pace — one listing at a time, not in a tight loop.
- If FB throws a security challenge during a scrape, solve it in the open browser, then re-run `fb_session.py` to refresh cookies.
- The scrape relies on undocumented DOM patterns and CSS class hashes. Selectors will break when FB rewrites their UI. Be ready to fix `fb_fetch.py` (the cover-photo detection and description extraction are the most fragile parts).

## Known limitations

- **Selector brittleness.** FB has no public API; the scripts walk a React-rendered DOM. Things will break.
- **Sandbox shipping rates are fake.** Sandbox uses canned (often inflated) rates instead of real USPS pricing. Production rates are realistic.
- **Sandbox vs production.** Default is sandbox. Always do a full sandbox dry-run before flipping `EBAY_ENV=production`.
- **Single account.** Tokens and sessions are per-user; no multi-account support.
- **Calculated rate only.** The default fulfillment policy is calculated USPS Parcel + Priority. Flat-rate or calculated-with-other-carriers requires editing the policy by hand or extending `build_dynamic_fulfillment_policy` in `ebay_publish.py`.

## License

MIT. Use freely, no warranty.
