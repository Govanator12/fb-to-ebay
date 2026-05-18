# eBay field mapping reference

Use this when shaping an item — whether described from scratch or scraped from a Facebook Marketplace listing — into the form the eBay Inventory API expects.

## Condition

eBay's Inventory API takes a condition **enum string** (not the numeric ID). Map the item's free-text condition (what the user told you, or what FB Marketplace reported) to one of these:

| Free-text condition (user's words or FB Marketplace) | eBay enum |
|---|---|
| "New", "Brand new", "New with tags" | `NEW` |
| "New (Other)", "Open box" | `NEW_OTHER` |
| "Like new", "Used – Like New" | `USED_EXCELLENT` |
| "Used – Very Good" | `USED_VERY_GOOD` |
| "Used", "Used – Good", "Good condition" | `USED_GOOD` |
| "Fair", "Used – Acceptable", "Worn" | `USED_ACCEPTABLE` |
| "For parts", "Not working", "Salvage" | `FOR_PARTS_OR_NOT_WORKING` |
| "Refurbished" (no certifier given) | `SELLER_REFURBISHED` |

If the user wrote a condition note FB doesn't structure (e.g. "small tear on left sleeve"), keep that text and pass it as `conditionDescription` in the draft — it shows up under the condition on the listing.

Some categories don't allow every condition (e.g. some clothing categories reject `NEW_OTHER`). If the publish call fails with a condition error, ask the user which mapped enum to try next.

## Title (≤80 chars)

Front-load keywords a buyer would actually type into eBay search. The FB title is often conversational ("Cute lamp from grandma's house") — rewrite into searchable terms: brand, model, type, size, color, era.

**Examples:**
- FB: "Old leather jacket, men's medium" → eBay: `Vintage Brown Leather Bomber Jacket Men's Medium 70s Style`
- FB: "iPhone 13 unlocked" → eBay: `Apple iPhone 13 128GB Blue Unlocked Smartphone Excellent Condition`
- FB: "Coffee table" → eBay: `Mid Century Modern Walnut Coffee Table Tapered Legs 48" Solid Wood`

Avoid: ALL CAPS, emoji, "L@@K", "WOW", repeated punctuation. eBay's search ranking penalizes these.

## Description

Plain text or simple HTML. Three short paragraphs:

1. **What it is + condition.** One or two sentences. Lead with the brand/model/type and the actual condition story.
2. **Specifics.** Dimensions, materials, model numbers, included accessories. Bulleted lists are fine (`<ul><li>`).
3. **Logistics.** Pickup/shipping notes, return policy reminder, anything the user wrote about meetup preferences.

Don't add aspirational filler ("Perfect gift!" "Won't last long!"). eBay buyers are skeptical of marketing language.

## Required offer fields

`ebay_publish.py` will fail without these. Capture them in the draft:

- **categoryId** — get from `ebay_taxonomy.py` and confirm with the user
- **price** — `{ "value": "45.00", "currency": "USD" }`
- **merchantLocationKey** — the user's saved inventory location key. They can list locations via `GET /sell/inventory/v1/location` if they don't remember; the default is usually `default`.
- **fulfillmentPolicyId**, **paymentPolicyId**, **returnPolicyId** — set up once at https://www.bizpolicy.ebay.com. Once created, the IDs can be cached in `.env` as `EBAY_FULFILLMENT_POLICY_ID` etc., so they don't have to live in every draft. (The publish script doesn't read those env vars yet — add them to drafts manually for now.)

## Title

eBay rejects titles longer than **80 characters** (`errorId 25718`). The publish script pre-validates and exits before doing any work if the title's too long, so you can't waste an EPS upload on it — but better to count yours upfront and avoid the round-trip. Front-load search keywords (brand, model, type), no ALL CAPS, no emoji.

## Item-specifics ("aspects")

Many categories require structured aspects (e.g. clothing needs Size, Color, Brand, Department). The Taxonomy API has `getItemAspectsForCategory` which returns the required ones for a given categoryId — call it from a one-off `httpx` snippet if a publish fails with an aspect error. Pass aspects as:

```json
"aspects": {
  "Brand": ["Levi's"],
  "Size": ["32x34"],
  "Color": ["Blue"]
}
```

Each value is an array — but **most categories restrict each aspect to a single element**. Multi-value will be rejected with `errorId 25002` ("X should contain only one value"). Some aspects (notably `Theme`, `Genre`, `Brand`, `Type`, `Material`, `Color`) are usually single-valued; default to one element unless you've confirmed via `getItemAspectsForCategory` that the aspect's `aspectMode` is `FREE_TEXT` with `multiValueEnabled: true`. The publish script warns when you pass >1 value for a commonly-single aspect.

## Marketplace ID

US is the default. Other common values: `EBAY_GB`, `EBAY_AU`, `EBAY_CA`. Set via `EBAY_MARKETPLACE_ID` in `.env` if not US.
