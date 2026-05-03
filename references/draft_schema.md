# Draft JSON schema

The shape `ebay_publish.py --draft <path>` expects. Write this to a temp file (e.g. `/tmp/ebay-draft.json`) before invoking the publish script.

The intermediate output of `fb_fetch.py` is close to this shape — Claude takes that intermediate JSON, polishes the fields (title, description, condition mapping), adds a category and shipping details, and writes out the final draft below.

## Minimal example

```json
{
  "title": "Apple iPhone 13 128GB Blue Unlocked Smartphone",
  "description": "<p>Apple iPhone 13, 128GB, Blue, factory unlocked. Excellent condition with light scratches on the back; screen is flawless.</p><ul><li>Battery health: 89%</li><li>Includes original box, no charger</li></ul><p>Local pickup available in the Bay Area, will ship USPS Priority.</p>",
  "condition": "USED_EXCELLENT",
  "categoryId": "9355",
  "price": { "value": "350.00", "currency": "USD" },
  "quantity": 1,
  "weightLbs": 1.0,
  "boxDimensionsIn": [8, 6, 4],
  "localImages": [
    "~/.cache/fb-to-ebay/apple-iphone-13/img-01.jpg",
    "~/.cache/fb-to-ebay/apple-iphone-13/img-02.jpg"
  ]
}
```

(`localImages` get auto-uploaded to EPS and replaced with eBay-hosted `imageUrls` at publish time. Policy IDs + merchant location key fall back to `.env`.)

## Field reference

| Field | Required | Notes |
|---|---|---|
| `title` | yes | ≤80 chars, see ebay_field_map.md |
| `description` | yes | Plain text or simple HTML |
| `condition` | yes | One of the enum strings in ebay_field_map.md. Run `ebay_conditions.py <categoryId>` first to confirm the category accepts it. |
| `conditionDescription` | no | Free-text condition note (e.g. "small tear on left sleeve"). Shown under the condition on the listing. |
| `categoryId` | yes | From `ebay_taxonomy.py` output |
| `price.value` | yes | String, decimal (e.g. "45.00") |
| `price.currency` | no | Defaults to "USD" |
| `quantity` | no | Defaults to 1 |
| `imageUrls` | yes\* | Array of publicly-accessible HTTPS URLs (e.g. eBay-hosted, Imgur). \*Optional if `localImages` is set — the publish script EPS-uploads them and populates `imageUrls` automatically. |
| `localImages` | yes\* | Array of local file paths. Auto-uploaded to eBay Picture Services (EPS) at publish time. \*Optional if `imageUrls` is already set with reachable URLs. |
| `aspects` | no | `{ "Brand": ["..."], "Size": ["..."] }` — required by some categories (publish will fail with errorId 25002 if missing) |
| `marketplaceId` | no | Defaults to `EBAY_MARKETPLACE_ID` env var (or `EBAY_US`) |
| `merchantLocationKey` | yes\* | Inventory location key, usually `"default"`. Falls back to `EBAY_MERCHANT_LOCATION_KEY` env var. |
| `fulfillmentPolicyId` | yes\* | From eBay business policies. Falls back to `EBAY_FULFILLMENT_POLICY_ID` env var (or env-prefixed equivalent). |
| `paymentPolicyId` | yes\* | From eBay business policies. Falls back to `EBAY_PAYMENT_POLICY_ID` env var. |
| `returnPolicyId` | yes\* | From eBay business policies. Falls back to `EBAY_RETURN_POLICY_ID` env var. |
| `sku` | no | Auto-generated from title + timestamp if omitted (alphanumeric only, max 50 chars) |
| `handlingDays` | no | Per-listing handling time override (business days). Falls back to `EBAY_DEFAULT_HANDLING_DAYS`, then 2. |
| `localPickup` | no | Per-listing local-pickup override (true/false). Falls back to `EBAY_OFFER_LOCAL_PICKUP`, then true. |
| `shipInternationally` | no | Per-listing international-shipping override. Falls back to `EBAY_SHIP_INTERNATIONALLY`, then false. |
| `weightLbs` | recommended | Item weight (units configurable via `EBAY_WEIGHT_UNIT`, default POUND). Wired into the inventory item's `packageWeightAndSize.weight` block — required for CALCULATED shipping rates. |
| `boxDimensionsIn` | recommended | Box dimensions `[L, W, H]` (units configurable via `EBAY_DIMENSION_UNIT`, default INCH). Wired into `packageWeightAndSize.dimensions`. Required alongside `weightLbs` for CALCULATED rates. |
| `freeShipping` | no | If `true`, the per-listing fulfillment policy uses FLAT_RATE with $0 shipping (seller pays). Default `false` (CALCULATED, buyer pays). |

\* Required at offer-creation time, but `ebay_publish.py` will use the corresponding env var if the draft omits it. Set the env vars in `~/.config/fb-to-ebay/.env` to avoid pasting policy IDs into every draft.

If any of `handlingDays`, `localPickup`, `shipInternationally` (or their env equivalents) are set, `ebay_publish.py` mints a fresh per-listing fulfillment policy on the fly via `createFulfillmentPolicy` (or reuses an existing matching one) and uses its ID instead of the env-default. Otherwise the env-default policy is used as-is.

## Images

`ebay_publish.py` automatically uploads any `localImages` to eBay Picture Services (EPS) via `ebay_eps.py` and replaces them with eBay-hosted URLs in the inventory item. This is the standard path — `fb_fetch.py` downloads the photos to a local cache, the publish script uploads them to EPS, eBay stores them on its own CDN.

You can also pass pre-hosted `imageUrls` directly (e.g. eBay-hosted from a previous run, Imgur URLs, your own S3 bucket) and skip `localImages` entirely.

**Don't pass FB CDN URLs (`fbcdn.net`) directly.** They're signed and expire within minutes; eBay's server-side fetch will 403. Use `localImages` so the upload happens immediately while the URL is still valid.
