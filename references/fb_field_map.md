# Facebook Marketplace field mapping reference

Use this when posting an eBay listing to FB Marketplace via `fb_post.py`. FB's
form uses display strings (not enums) for both category and condition, so
selectors match on visible text — get these spellings exactly right or the
dropdown click won't find the option.

## Condition

FB Marketplace's condition dropdown shows these literal options:

- `New`
- `Used - Like New`
- `Used - Good`
- `Used - Fair`

Map from eBay's enum:

| eBay condition | FB condition |
|---|---|
| `NEW`, `NEW_OTHER`, `NEW_WITH_DEFECTS` | `New` |
| `USED_EXCELLENT`, `MANUFACTURER_REFURBISHED`, `CERTIFIED_REFURBISHED` | `Used - Like New` |
| `USED_VERY_GOOD`, `USED_GOOD`, `SELLER_REFURBISHED` | `Used - Good` |
| `USED_ACCEPTABLE`, `FOR_PARTS_OR_NOT_WORKING` | `Used - Fair` |

If the eBay listing has a `conditionDescription` (e.g. "small tear on left sleeve"), append it to the FB description rather than trying to fit it into the structured condition.

## Category

FB's category dropdown is a flat list (no parent navigation). Top-level
options that exist as of this writing:

- `Antiques & Collectibles`
- `Arts & Crafts`
- `Auto Parts`
- `Baby Products`
- `Bags & Luggage`
- `Books, Films & Music`
- `Clothing & Accessories`
- `Electronics`
- `Free Stuff`
- `Furniture`
- `Garden & Outdoor`
- `Health & Beauty`
- `Home Goods`
- `Home Improvement Supplies`
- `Jewelry & Watches`
- `Musical Instruments`
- `Office Supplies`
- `Pet Supplies`
- `Sporting Goods`
- `Tickets`
- `Tools`
- `Toys & Games`
- `Video Games & Consoles`
- `Other`

When mapping from eBay's deep category tree (e.g. `Clothing, Shoes & Accessories > Men > Men's Clothing > Coats, Jackets & Vests`), pick the closest top-level FB equivalent. If unsure, ask the user.

**Mapping examples:**
- eBay `Clothing, Shoes & Accessories > *` → FB `Clothing & Accessories`
- eBay `Cell Phones & Accessories > *` → FB `Electronics`
- eBay `Home & Garden > Furniture > *` → FB `Furniture`
- eBay `Home & Garden > Yard, Garden & Outdoor Living > *` → FB `Garden & Outdoor`
- eBay `Toys & Hobbies > *` → FB `Toys & Games`
- eBay `Sporting Goods > *` → FB `Sporting Goods`
- eBay anything you can't place → ask the user; defaulting to `Other` makes the listing hard to find.

## Title

FB's title field has a 100-char limit (more generous than eBay's 80). You can pass the eBay title through unchanged.

## Description

FB descriptions render plain text only — strip any HTML from the eBay description before passing it through. The `fb_post.py` script doesn't do this for you; do it in chat before writing the draft JSON.

## Price

Single number, no formatting. FB infers currency from the seller's location, so don't include currency symbols.

## Photos

FB's create-listing flow accepts up to 10 photos. The file picker takes
multiple files at once — `fb_post.py` passes the whole `localImages` list to a
single `set_input_files` call, which is the most reliable way.

## Location

Auto-filled from the seller's account. Only set the `location` field in the
draft if you want to override it (e.g. for a meetup-only listing in a
different city).

## Account-safety notes

- Run with the headed browser (default) — headless triggers more bot detection.
- Don't run dozens of posts in a row. FB's automation detection looks at
  rate, not just volume; spread posts over hours.
- If FB shows a security challenge (CAPTCHA, identity verify), solve it in
  the open browser yourself, then press Enter in the terminal so the script
  continues. Re-run `fb_session.py` afterward to refresh the session.
- Keep `--auto-publish` off until you've done several manual reviews. A bad
  listing is harder to delete than to not publish.
