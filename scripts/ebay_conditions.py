#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""List the valid item conditions for an eBay category, with the Inventory API enum to use.

Many eBay categories restrict which condition options are allowed. Notably,
collectibles categories often only support the generic 'Used' (condition id
3000) — the granular USED_GOOD / USED_VERY_GOOD / USED_ACCEPTABLE enums get
rejected at publishOffer time. Call this BEFORE picking a condition for the
draft so you can map FB's condition string to a valid eBay enum upfront,
instead of failing at publish.

Usage:
  uv run ebay_conditions.py <categoryId>

Prints one row per valid condition: <id> <description> <inventory-api-enum>.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from ebay_auth import api_host, get_access_token, load_env  # noqa: E402

# eBay's condition ID → Inventory API enum mapping. The Inventory API rejects
# enums for IDs the category doesn't accept, so this table is just a reverse
# lookup from "what does eBay allow" to "what string do I send".
ID_TO_ENUM = {
    "1000": "NEW",
    "1500": "NEW_OTHER",
    "1750": "NEW_WITH_DEFECTS",
    "2000": "MANUFACTURER_REFURBISHED",
    "2010": "CERTIFIED_REFURBISHED",
    "2020": "EXCELLENT_REFURBISHED",
    "2030": "VERY_GOOD_REFURBISHED",
    "2040": "GOOD_REFURBISHED",
    "2500": "SELLER_REFURBISHED",
    "2750": "LIKE_NEW",
    # 3000 = generic "Used". In categories that only allow 3000 (most
    # collectibles, art, antiques), the Inventory API expects USED_EXCELLENT
    # as the enum — eBay translates it back to "Used" before display.
    "3000": "USED_EXCELLENT",
    "4000": "USED_VERY_GOOD",
    "5000": "USED_GOOD",
    "6000": "USED_ACCEPTABLE",
    "7000": "FOR_PARTS_OR_NOT_WORKING",
}


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: ebay_conditions.py <categoryId>")
    category_id = sys.argv[1]
    env = load_env()
    marketplace = env.get("EBAY_MARKETPLACE_ID", "EBAY_US")
    token = get_access_token(env)

    resp = httpx.get(
        f"https://{api_host(env)}/sell/metadata/v1/marketplace/{marketplace}/get_item_condition_policies",
        headers={"Authorization": f"Bearer {token}"},
        params={"filter": "categoryIds:{" + category_id + "}"},
        timeout=30,
    )
    if resp.status_code != 200:
        sys.exit(f"get_item_condition_policies failed ({resp.status_code}): {resp.text}")

    policies = resp.json().get("itemConditionPolicies", [])
    matching = [p for p in policies if p.get("categoryId") == category_id]
    if not matching:
        sys.exit(f"No condition policy returned for category {category_id}.")

    policy = matching[0]
    required = policy.get("itemConditionRequired", False)
    print(f"category {category_id} (condition required: {required}):")
    for c in policy.get("itemConditions", []):
        cid = c["conditionId"]
        desc = c["conditionDescription"]
        enum = ID_TO_ENUM.get(cid, "?")
        print(f"  {cid:>5}  {desc:<35}  {enum}")


if __name__ == "__main__":
    main()
