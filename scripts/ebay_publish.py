#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""Publish a draft listing to eBay via the Sell Inventory API.

Reads a draft JSON file (see references/draft_schema.md), runs the three-step
chain createOrReplaceInventoryItem -> createOffer -> publishOffer, and prints
the live listing URL on success.

Usage:
  uv run ebay_publish.py --draft path/to/draft.json
  uv run ebay_publish.py --draft path/to/draft.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from ebay_auth import api_host, get_access_token, load_env  # noqa: E402

POLICY_ERROR_CODES = {25709, 25710, 25711}  # missing fulfillment / payment / return policy


class PublishError(RuntimeError):
    """Raised when an eBay API step fails, so we can roll back partial state."""


def required(d: dict, key: str, where: str) -> object:
    if key not in d:
        sys.exit(f"Draft is missing required field {where}.{key}")
    return d[key]


def make_sku(title: str) -> str:
    # eBay SKUs must be alphanumeric only (no hyphens / underscores), max 50 chars.
    safe = "".join(c for c in title.lower() if c.isalnum())[:35]
    return f"{safe}{int(time.time())}"


def post_json(url: str, token: str, body: dict, lang: str = "en-US") -> httpx.Response:
    return httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Content-Language": lang,
            "Accept-Language": lang,
        },
        json=body,
        timeout=60,
    )


def put_json(url: str, token: str, body: dict, lang: str = "en-US") -> httpx.Response:
    return httpx.put(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Content-Language": lang,
            "Accept-Language": lang,
        },
        json=body,
        timeout=60,
    )


def explain_failure(resp: httpx.Response, step: str) -> None:
    print(f"\n✗ {step} failed (HTTP {resp.status_code})", file=sys.stderr)
    try:
        body = resp.json()
        for err in body.get("errors", []):
            code = err.get("errorId")
            msg = err.get("longMessage") or err.get("message", "")
            print(f"  [{code}] {msg}", file=sys.stderr)
            if code in POLICY_ERROR_CODES:
                print(
                    "\n  → Your eBay account is missing a business policy. "
                    "Set them up at https://www.bizpolicy.ebay.com and add the "
                    "policy IDs to your draft (fulfillmentPolicyId, paymentPolicyId, "
                    "returnPolicyId).",
                    file=sys.stderr,
                )
        if not body.get("errors"):
            print(json.dumps(body, indent=2), file=sys.stderr)
    except json.JSONDecodeError:
        print(resp.text, file=sys.stderr)
    raise PublishError(step)


def create_inventory_item(env: dict, token: str, sku: str, draft: dict) -> None:
    body = {
        "availability": {"shipToLocationAvailability": {"quantity": draft.get("quantity", 1)}},
        "condition": required(draft, "condition", "draft"),
        "product": {
            "title": required(draft, "title", "draft"),
            "description": required(draft, "description", "draft"),
            "imageUrls": required(draft, "imageUrls", "draft"),
        },
    }
    if "aspects" in draft:
        body["product"]["aspects"] = draft["aspects"]
    if "conditionDescription" in draft:
        body["conditionDescription"] = draft["conditionDescription"]
    pkg = build_package_weight_and_size(draft)
    if pkg:
        body["packageWeightAndSize"] = pkg

    url = f"https://{api_host(env)}/sell/inventory/v1/inventory_item/{sku}"
    resp = put_json(url, token, body)
    if resp.status_code not in (200, 204):
        explain_failure(resp, "createOrReplaceInventoryItem")
    print(f"✓ Inventory item created (sku={sku})")


def build_package_weight_and_size(draft: dict) -> dict | None:
    """Convert draft.weightLbs + draft.boxDimensionsIn into eBay's packageWeightAndSize."""
    weight = draft.get("weightLbs")
    dims = draft.get("boxDimensionsIn")
    if weight is None and not dims:
        return None
    out: dict = {"packageType": "MAILING_BOX"}
    if weight is not None:
        out["weight"] = {"value": float(weight), "unit": "POUND"}
    if dims and len(dims) == 3:
        out["dimensions"] = {
            "length": float(dims[0]),
            "width": float(dims[1]),
            "height": float(dims[2]),
            "unit": "INCH",
        }
    return out


def with_env_default(draft: dict, draft_key: str, env: dict, env_key: str, where: str) -> object:
    """Pull a value from the draft, falling back to an env var, then erroring."""
    if draft_key in draft:
        return draft[draft_key]
    if env_key in env:
        return env[env_key]
    sys.exit(f"Draft is missing {where}.{draft_key} and env has no {env_key}")


def _env_bool(env: dict, key: str, default: bool) -> bool:
    val = env.get(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


SHIPPING_OVERRIDE_KEYS = ("handlingDays", "localPickup", "shipInternationally", "weightLbs", "boxDimensionsIn")


def has_shipping_overrides(draft: dict, env: dict) -> bool:
    """Should we mint a per-listing fulfillment policy instead of using the env default?"""
    if any(k in draft for k in SHIPPING_OVERRIDE_KEYS):
        return True
    return any(env.get(k) for k in ("EBAY_DEFAULT_HANDLING_DAYS", "EBAY_OFFER_LOCAL_PICKUP", "EBAY_SHIP_INTERNATIONALLY"))


def build_dynamic_fulfillment_policy(env: dict, draft: dict, sku: str) -> dict:
    """Build a fulfillment policy body from the listing's shipping settings.

    Defaults to USPS Priority CALCULATED rate (eBay computes cost from
    package weight/dimensions + zip-to-zip), with the buyer paying. This
    matches what most casual sellers want. Free shipping is opt-in via
    `freeShipping: true` in the draft.
    """
    handling_days = int(draft.get("handlingDays") or env.get("EBAY_DEFAULT_HANDLING_DAYS") or 2)
    local_pickup = bool(draft["localPickup"]) if "localPickup" in draft else _env_bool(env, "EBAY_OFFER_LOCAL_PICKUP", True)
    ship_intl = bool(draft["shipInternationally"]) if "shipInternationally" in draft else _env_bool(env, "EBAY_SHIP_INTERNATIONALLY", False)
    free_shipping = bool(draft.get("freeShipping", False))

    domestic_service: dict = {
        "sortOrder": 1,
        "shippingCarrierCode": "USPS",
        "shippingServiceCode": "USPSPriority",
        "freeShipping": free_shipping,
        "buyerResponsibleForShipping": False,
        "buyerResponsibleForPickup": False,
    }
    if free_shipping:
        domestic_service["shippingCost"] = {"value": "0.0", "currency": "USD"}

    shipping_options = [{
        "optionType": "DOMESTIC",
        # CALCULATED requires the inventory item to have packageWeightAndSize.
        # If it doesn't, eBay will reject publish — we surface a helpful error.
        "costType": "FLAT_RATE" if free_shipping else "CALCULATED",
        "shippingServices": [domestic_service],
    }]
    if ship_intl:
        shipping_options.append({
            "optionType": "INTERNATIONAL",
            "costType": "CALCULATED",
            "shippingServices": [{
                "sortOrder": 1,
                "shippingCarrierCode": "USPS",
                "shippingServiceCode": "USPSPriorityMailInternational",
                "freeShipping": False,
                "buyerResponsibleForShipping": False,
            }],
        })

    return {
        "name": f"fb2ebay-{sku[:40]}-{int(time.time())}"[:64],
        "marketplaceId": draft.get("marketplaceId", env.get("EBAY_MARKETPLACE_ID", "EBAY_US")),
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
        "handlingTime": {"value": handling_days, "unit": "DAY"},
        "shippingOptions": shipping_options,
        "pickupDropOff": local_pickup,
        "globalShipping": False,
    }


def list_fulfillment_policies(env: dict, token: str, marketplace_id: str) -> list[dict]:
    url = f"https://{api_host(env)}/sell/account/v1/fulfillment_policy"
    resp = httpx.get(
        url,
        headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": marketplace_id},
        params={"marketplace_id": marketplace_id},
        timeout=30,
    )
    if resp.status_code != 200:
        return []
    return resp.json().get("fulfillmentPolicies", []) or []


def fulfillment_policies_match(existing: dict, wanted: dict) -> bool:
    """Loose match — same handling/pickup/intl + same primary domestic service."""
    if existing.get("handlingTime", {}).get("value") != wanted["handlingTime"]["value"]:
        return False
    if bool(existing.get("pickupDropOff")) != bool(wanted["pickupDropOff"]):
        return False
    if bool(existing.get("globalShipping")) != bool(wanted["globalShipping"]):
        return False
    # Compare just the first domestic shipping service code.
    def primary_code(p: dict) -> str | None:
        for opt in p.get("shippingOptions", []):
            if opt.get("optionType") == "DOMESTIC":
                services = opt.get("shippingServices", [])
                if services:
                    return services[0].get("shippingServiceCode")
        return None
    return primary_code(existing) == primary_code(wanted)


def find_or_create_fulfillment_policy(env: dict, token: str, body: dict) -> str:
    """Reuse an existing matching policy if one exists, else create a new one.

    eBay dedupes fulfillment policies by content (refuses to create a duplicate
    with the same handling/pickup/shipping config), so we look for an existing
    match first.
    """
    for existing in list_fulfillment_policies(env, token, body["marketplaceId"]):
        if fulfillment_policies_match(existing, body):
            print(f"  → reusing existing policy {existing['fulfillmentPolicyId']} ({existing['name']!r})")
            return existing["fulfillmentPolicyId"]
    url = f"https://{api_host(env)}/sell/account/v1/fulfillment_policy"
    resp = post_json(url, token, body)
    if resp.status_code not in (200, 201):
        explain_failure(resp, "createFulfillmentPolicy")
    return resp.json()["fulfillmentPolicyId"]


def create_offer(env: dict, token: str, sku: str, draft: dict) -> str:
    price = required(draft, "price", "draft")
    body = {
        "sku": sku,
        "marketplaceId": draft.get("marketplaceId", env.get("EBAY_MARKETPLACE_ID", "EBAY_US")),
        "format": "FIXED_PRICE",
        "availableQuantity": draft.get("quantity", 1),
        "categoryId": required(draft, "categoryId", "draft"),
        "listingDescription": required(draft, "description", "draft"),
        "pricingSummary": {
            "price": {"value": str(price["value"]), "currency": price.get("currency", "USD")}
        },
        "merchantLocationKey": with_env_default(
            draft, "merchantLocationKey", env, "EBAY_MERCHANT_LOCATION_KEY", "draft"
        ),
        "listingPolicies": {
            "fulfillmentPolicyId": with_env_default(
                draft, "fulfillmentPolicyId", env, "EBAY_FULFILLMENT_POLICY_ID", "draft"
            ),
            "paymentPolicyId": with_env_default(
                draft, "paymentPolicyId", env, "EBAY_PAYMENT_POLICY_ID", "draft"
            ),
            "returnPolicyId": with_env_default(
                draft, "returnPolicyId", env, "EBAY_RETURN_POLICY_ID", "draft"
            ),
        },
    }
    url = f"https://{api_host(env)}/sell/inventory/v1/offer"
    resp = post_json(url, token, body)
    if resp.status_code not in (200, 201):
        explain_failure(resp, "createOffer")
    offer_id = resp.json()["offerId"]
    print(f"✓ Offer created (offerId={offer_id})")
    return offer_id


def publish_offer(env: dict, token: str, offer_id: str) -> str:
    url = f"https://{api_host(env)}/sell/inventory/v1/offer/{offer_id}/publish"
    resp = post_json(url, token, {})
    if resp.status_code not in (200, 201):
        explain_failure(resp, "publishOffer")
    listing_id = resp.json()["listingId"]
    print(f"✓ Listing published (listingId={listing_id})")
    return listing_id


def listing_url(env: dict, listing_id: str) -> str:
    host = "sandbox.ebay.com" if env["EBAY_ENV"] == "sandbox" else "ebay.com"
    return f"https://www.{host}/itm/{listing_id}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", required=True, type=Path, help="Path to draft JSON")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the draft and print the assembled request bodies without calling eBay.",
    )
    args = parser.parse_args()

    if not args.draft.exists():
        sys.exit(f"Draft file not found: {args.draft}")
    draft = json.loads(args.draft.read_text())
    sku = draft.get("sku") or make_sku(draft.get("title", "item"))

    env = load_env()
    print(f"Environment: {env['EBAY_ENV']}")

    if args.dry_run:
        print("\n--- DRY RUN, not calling eBay ---")
        print(f"SKU: {sku}")
        print("Draft:")
        print(json.dumps(draft, indent=2))
        return

    token = get_access_token(env)

    # Mint a per-listing fulfillment policy if the draft has shipping overrides
    # (handlingDays, localPickup, etc.) or any of the corresponding env defaults
    # are set. Otherwise fall back to EBAY_FULFILLMENT_POLICY_ID.
    if has_shipping_overrides(draft, env) and "fulfillmentPolicyId" not in draft:
        body = build_dynamic_fulfillment_policy(env, draft, sku)
        print(f"Creating per-listing fulfillment policy "
              f"(handling={body['handlingTime']['value']}d, "
              f"pickupDropOff={body['pickupDropOff']}, "
              f"international={len(body['shippingOptions']) > 1})...")
        draft["fulfillmentPolicyId"] = find_or_create_fulfillment_policy(env, token, body)
        print(f"  ✓ fulfillmentPolicyId={draft['fulfillmentPolicyId']}")

    # If the draft has localImages but no usable imageUrls, upload them to EPS
    # so eBay can fetch them. (FB CDN URLs would 403 from eBay's side.)
    local_images = draft.get("localImages") or []
    image_urls = draft.get("imageUrls") or []
    needs_upload = local_images and not any(
        u.startswith("http") and "ebayimg.com" in u for u in image_urls
    )
    if needs_upload:
        from ebay_eps import upload_images
        paths = [Path(p).expanduser() for p in local_images]
        print(f"Uploading {len(paths)} image(s) to eBay Picture Services...")
        eps_urls = upload_images(env, token, paths)
        for url in eps_urls:
            print(f"  ✓ {url}")
        draft["imageUrls"] = eps_urls

    # Track what we created so we can roll back on failure (avoids piling up
    # orphan inventory items + offers across retries).
    created_sku: str | None = None
    created_offer_id: str | None = None
    try:
        create_inventory_item(env, token, sku, draft)
        created_sku = sku
        created_offer_id = create_offer(env, token, sku, draft)
        listing_id = publish_offer(env, token, created_offer_id)
        print(f"\n→ {listing_url(env, listing_id)}")
    except PublishError as e:
        print(f"\nRolling back partial state from {e}...", file=sys.stderr)
        cleanup_orphans(env, token, created_sku, created_offer_id)
        sys.exit(1)


def cleanup_orphans(env: dict, token: str, sku: str | None, offer_id: str | None) -> None:
    """Best-effort: delete an unpublished offer and the inventory item we just created."""
    if offer_id:
        try:
            httpx.delete(
                f"https://{api_host(env)}/sell/inventory/v1/offer/{offer_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            print(f"  ✓ deleted offer {offer_id}", file=sys.stderr)
        except Exception as e:
            print(f"  ! could not delete offer {offer_id}: {e}", file=sys.stderr)
    if sku:
        try:
            httpx.delete(
                f"https://{api_host(env)}/sell/inventory/v1/inventory_item/{sku}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            print(f"  ✓ deleted inventory item {sku}", file=sys.stderr)
        except Exception as e:
            print(f"  ! could not delete inventory item {sku}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
