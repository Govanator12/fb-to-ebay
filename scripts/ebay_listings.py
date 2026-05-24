#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""List your eBay inventory items + offers.

Walks getInventoryItems and getOffers to show every SKU you have on the
account, with offer status, price, and the live listing URL when published.

Usage:
  uv run ebay_listings.py                  # uses EBAY_ENV from .env
  uv run ebay_listings.py --env production # override active env
  uv run ebay_listings.py --json           # raw JSON dump for downstream tooling
  uv run ebay_listings.py --status PUBLISHED  # filter offers by status
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from ebay_auth import api_host, get_access_token, load_env  # noqa: E402


def fetch_inventory_items(host: str, token: str) -> list[dict]:
    items: list[dict] = []
    offset = 0
    while True:
        resp = httpx.get(
            f"https://{host}/sell/inventory/v1/inventory_item",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": 100, "offset": offset},
            timeout=60,
        )
        if resp.status_code == 404:
            return items
        resp.raise_for_status()
        body = resp.json()
        batch = body.get("inventoryItems", [])
        items.extend(batch)
        if len(batch) < 100:
            break
        offset += 100
    return items


def fetch_offers_for_sku(host: str, token: str, sku: str) -> list[dict]:
    resp = httpx.get(
        f"https://{host}/sell/inventory/v1/offer",
        headers={"Authorization": f"Bearer {token}"},
        params={"sku": sku},
        timeout=60,
    )
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return resp.json().get("offers", [])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=["sandbox", "production"], help="Override EBAY_ENV from .env")
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of a table")
    parser.add_argument("--status", help="Filter by offer status (e.g. PUBLISHED, UNPUBLISHED)")
    args = parser.parse_args()

    env = load_env()
    if args.env and args.env != env["EBAY_ENV"]:
        # Re-promote env-scoped credentials for the requested environment, since
        # load_env() already promoted the sandbox/production set based on the
        # value of EBAY_ENV in .env.
        env["EBAY_ENV"] = args.env
        prefix = f"EBAY_{args.env.upper()}_"
        for key in list(env):
            if key.startswith(prefix):
                env[f"EBAY_{key[len(prefix):]}"] = env[key]
    host = api_host(env)
    token = get_access_token(env)

    items = fetch_inventory_items(host, token)
    rows: list[dict] = []
    for item in items:
        sku = item.get("sku")
        title = (item.get("product") or {}).get("title", "")
        offers = fetch_offers_for_sku(host, token, sku) if sku else []
        if not offers:
            rows.append({
                "sku": sku,
                "title": title,
                "status": "NO_OFFER",
                "price": None,
                "currency": None,
                "offerId": None,
                "listingId": None,
                "url": None,
                "categoryId": None,
            })
            continue
        for offer in offers:
            status = offer.get("status")
            if args.status and status != args.status:
                continue
            price = ((offer.get("pricingSummary") or {}).get("price") or {})
            listing_id = (offer.get("listing") or {}).get("listingId")
            url = None
            if listing_id:
                domain = "sandbox.ebay.com" if env["EBAY_ENV"] == "sandbox" else "ebay.com"
                url = f"https://www.{domain}/itm/{listing_id}"
            rows.append({
                "sku": sku,
                "title": title or offer.get("listingDescription", "")[:60],
                "status": status,
                "price": price.get("value"),
                "currency": price.get("currency"),
                "offerId": offer.get("offerId"),
                "listingId": listing_id,
                "url": url,
                "categoryId": offer.get("categoryId"),
            })

    if args.json:
        print(json.dumps(rows, indent=2))
        return

    if not rows:
        print(f"(no inventory items found in {env['EBAY_ENV']})")
        return

    title_w = max(len(r["title"]) for r in rows)
    title_w = min(title_w, 60)
    print(f"\neBay listings ({env['EBAY_ENV']}) — {len(rows)} row(s)\n")
    print(f"{'Title':<{title_w}}  {'Status':<14}  {'Price':<10}  Offer / Listing")
    print(f"{'-' * title_w}  {'-' * 14}  {'-' * 10}  {'-' * 40}")
    for r in rows:
        t = r["title"][:title_w]
        price = f"{r['currency'] or ''} {r['price'] or ''}".strip() or "—"
        ref = r["url"] or f"offer:{r['offerId']}"
        print(f"{t:<{title_w}}  {r['status']:<14}  {price:<10}  {ref}")
    print()


if __name__ == "__main__":
    main()
