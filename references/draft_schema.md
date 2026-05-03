# Draft JSON schemas

Two shapes — one for each publish target. `ebay_publish.py` reads the eBay-side schema; `fb_post.py` reads the FB-side schema. The fetch scripts (`fb_fetch.py`, `ebay_fetch.py`) output a third intermediate shape (described at the bottom) that Claude transforms into one of these before publishing.

## eBay-side schema (consumed by `ebay_publish.py`)

Write this to a temp file (e.g. `/tmp/ebay-draft.json`) before invoking the publish script.

### Minimal example

```json
{
  "title": "Apple iPhone 13 128GB Blue Unlocked Smartphone",
  "description": "<p>Apple iPhone 13, 128GB, Blue, factory unlocked. Excellent condition with light scratches on the back; screen is flawless.</p><ul><li>Battery health: 89%</li><li>Includes original box, no charger</li></ul><p>Local pickup available in the Bay Area, will ship USPS Priority.</p>",
  "condition": "USED_EXCELLENT",
  "categoryId": "9355",
  "price": { "value": "350.00", "currency": "USD" },
  "quantity": 1,
  "imageUrls": [
    "https://example.com/phone-front.jpg",
    "https://example.com/phone-back.jpg"
  ],
  "merchantLocationKey": "default",
  "fulfillmentPolicyId": "6196932000",
  "paymentPolicyId": "6196933000",
  "returnPolicyId": "6196934000"
}
```

### Field reference

| Field | Required | Notes |
|---|---|---|
| `title` | yes | ≤80 chars, see ebay_field_map.md |
| `description` | yes | Plain text or simple HTML |
| `condition` | yes | One of the enum strings in ebay_field_map.md |
| `conditionDescription` | no | Free-text condition note (e.g. "small tear on left sleeve") |
| `categoryId` | yes | From `ebay_taxonomy.py` output |
| `price.value` | yes | String, decimal (e.g. "45.00") |
| `price.currency` | no | Defaults to "USD" |
| `quantity` | no | Defaults to 1 |
| `imageUrls` | yes\* | Array of publicly-accessible HTTPS URLs (e.g. eBay-hosted, Imgur). \*Optional if `localImages` is set — the publish script will EPS-upload them and populate `imageUrls` automatically. |
| `localImages` | yes\* | Array of local file paths. Auto-uploaded to eBay Picture Services (EPS) at publish time. \*Optional if `imageUrls` is already set with reachable URLs. |
| `aspects` | no | `{ "Brand": ["..."], "Size": ["..."] }` — required by some categories |
| `marketplaceId` | no | Defaults to `EBAY_MARKETPLACE_ID` env var (or `EBAY_US`) |
| `merchantLocationKey` | yes* | Inventory location key, usually `"default"`. Falls back to `EBAY_MERCHANT_LOCATION_KEY` env var. |
| `fulfillmentPolicyId` | yes* | From eBay business policies. Falls back to `EBAY_FULFILLMENT_POLICY_ID` env var. |
| `paymentPolicyId` | yes* | From eBay business policies. Falls back to `EBAY_PAYMENT_POLICY_ID` env var. |
| `returnPolicyId` | yes* | From eBay business policies. Falls back to `EBAY_RETURN_POLICY_ID` env var. |
| `sku` | no | Auto-generated from title + timestamp if omitted |
| `handlingDays` | no | Per-listing handling time override (business days). Falls back to `EBAY_DEFAULT_HANDLING_DAYS`, then 2. |
| `localPickup` | no | Per-listing local-pickup override (true/false). Falls back to `EBAY_OFFER_LOCAL_PICKUP`, then true. |
| `shipInternationally` | no | Per-listing international-shipping override. Falls back to `EBAY_SHIP_INTERNATIONALLY`, then false. |
| `weightLbs` | recommended | Item weight in pounds. Wired into the inventory item's `packageWeightAndSize.weight` block — required for CALCULATED shipping rates to actually compute. |
| `boxDimensionsIn` | recommended | Box dimensions `[L, W, H]` in inches. Wired into `packageWeightAndSize.dimensions`. Required alongside `weightLbs` for CALCULATED rates. |
| `freeShipping` | no | If `true`, the per-listing fulfillment policy uses FLAT_RATE with $0 shipping (seller eats it). Default `false` (CALCULATED, buyer pays). |

If any of `handlingDays`, `localPickup`, `shipInternationally` (or their env equivalents) are set, `ebay_publish.py` mints a fresh per-listing fulfillment policy on the fly via `createFulfillmentPolicy` and uses its ID instead of `EBAY_FULFILLMENT_POLICY_ID`. Otherwise the env-default policy is used as-is.

\* Required at offer-creation time, but `ebay_publish.py` will use the corresponding env var if the draft omits it. Set the env vars in `~/.config/fb-to-ebay/.env` to avoid pasting policy IDs into every draft.

### Images

`ebay_publish.py` automatically uploads any `localImages` to eBay Picture Services (EPS) via `ebay_eps.py` and replaces them with eBay-hosted URLs in the inventory item. This is the standard path for FB→eBay listings — `fb_fetch.py` downloads the photos to a local cache, the publish script uploads them to EPS, eBay stores them on its own CDN.

You can also pass pre-hosted `imageUrls` directly (e.g. eBay-hosted from a previous run, Imgur URLs, your own S3 bucket) and skip `localImages` entirely.

**Don't pass FB CDN URLs (`fbcdn.net`) directly.** They're signed and expire within minutes; eBay's server-side fetch will 403. Use `localImages` so the upload happens immediately while the URL is still valid.

## FB-side schema (consumed by `fb_post.py`)

Write this to a temp file (e.g. `/tmp/fb-draft.json`) before invoking `fb_post.py --draft <path>`.

```json
{
  "title": "Vintage Brown Leather Bomber Jacket Men's Medium",
  "price": { "value": "85.00", "currency": "USD" },
  "fbCategory": "Clothing & Accessories",
  "fbCondition": "Used - Like New",
  "description": "Vintage brown leather bomber jacket, men's medium. Excellent condition with light patina; lining intact, all zippers work. Smoke-free home. Local pickup in the Bay Area.",
  "localImages": [
    "~/.cache/fb-to-ebay/vintage-bomber-jacket/img-01.jpg",
    "~/.cache/fb-to-ebay/vintage-bomber-jacket/img-02.jpg"
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `title` | yes | ≤100 chars on FB |
| `price.value` | yes | String number, no currency symbol |
| `fbCategory` | yes | Exact text from FB's category dropdown — see fb_field_map.md |
| `fbCondition` | yes | One of `New`, `Used - Like New`, `Used - Good`, `Used - Fair` |
| `description` | yes | Plain text only — strip any HTML before passing |
| `localImages` | recommended | Absolute file paths; FB's file picker accepts the whole list at once |
| `location` | no | Override FB's default (account location) only when needed |

## Intermediate shape (fetch script outputs)

Both `fb_fetch.py` and `ebay_fetch.py` print a draft that Claude reshapes before publishing. The intermediate keys carry the raw extracted data:

- `title`, `description`, `price`, `imageUrls`, `localImages`, `location`, `sourceUrl` — common to both
- From `fb_fetch.py`: `fbCondition` (raw FB string)
- From `ebay_fetch.py`: `ebayCondition`, `ebayConditionId`, `ebayCategoryPath`, `ebayCategoryId`

Claude's job is to:
1. Polish title/description for the destination's conventions
2. Map condition to the destination's vocabulary
3. Pick a destination category (using `ebay_taxonomy.py` for FB→eBay, or fb_field_map.md for eBay→FB)
4. Add policy IDs (eBay-side only — usually picked up from env vars automatically)
5. Decide what to do about images (re-host for eBay, attach locally for FB)
6. Write out the destination-shaped draft
