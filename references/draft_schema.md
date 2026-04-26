# Draft JSON schema

The shape `ebay_publish.py --draft <path>` expects. Write this to a temp file (e.g. `/tmp/ebay-draft.json`) before invoking the publish script.

## Minimal example

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

## Field reference

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
| `imageUrls` | yes | Array of publicly-accessible HTTPS URLs |
| `aspects` | no | `{ "Brand": ["..."], "Size": ["..."] }` — required by some categories |
| `marketplaceId` | no | Defaults to `EBAY_MARKETPLACE_ID` env var (or `EBAY_US`) |
| `merchantLocationKey` | yes | Inventory location key, usually `"default"` |
| `fulfillmentPolicyId` | yes | From eBay business policies |
| `paymentPolicyId` | yes | From eBay business policies |
| `returnPolicyId` | yes | From eBay business policies |
| `sku` | no | Auto-generated from title + timestamp if omitted |

## Image URL caveat

eBay fetches `imageUrls` server-side. **Facebook's CDN URLs are signed and expire**, so passing an `fbcdn.net` URL directly often fails (eBay sees a 403 or expired signature). Workarounds, in order of effort:

1. The user re-uploads images to Imgur or another public host and pastes those URLs.
2. The user host them on their own server / S3 bucket / GitHub Pages.
3. Future: add an EPS (eBay Picture Services) upload helper to this skill that re-hosts images on eBay's CDN. Not implemented in v1.

If the publish fails with an image-fetch error, surface the URL eBay couldn't reach and ask the user to re-host that image.
