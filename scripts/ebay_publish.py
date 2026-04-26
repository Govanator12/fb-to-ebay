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


def required(d: dict, key: str, where: str) -> object:
    if key not in d:
        sys.exit(f"Draft is missing required field {where}.{key}")
    return d[key]


def make_sku(title: str) -> str:
    safe = "".join(c if c.isalnum() else "-" for c in title.lower())[:40].strip("-")
    return f"{safe}-{int(time.time())}"


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
    sys.exit(1)


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

    url = f"https://{api_host(env)}/sell/inventory/v1/inventory_item/{sku}"
    resp = put_json(url, token, body)
    if resp.status_code not in (200, 204):
        explain_failure(resp, "createOrReplaceInventoryItem")
    print(f"✓ Inventory item created (sku={sku})")


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
        "merchantLocationKey": required(draft, "merchantLocationKey", "draft"),
        "listingPolicies": {
            "fulfillmentPolicyId": required(draft, "fulfillmentPolicyId", "draft"),
            "paymentPolicyId": required(draft, "paymentPolicyId", "draft"),
            "returnPolicyId": required(draft, "returnPolicyId", "draft"),
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
    create_inventory_item(env, token, sku, draft)
    offer_id = create_offer(env, token, sku, draft)
    listing_id = publish_offer(env, token, offer_id)
    print(f"\n→ {listing_url(env, listing_id)}")


if __name__ == "__main__":
    main()
