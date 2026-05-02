#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""Fetch an eBay listing into a draft JSON, ready for fb_post.py.

Uses the Browse API (getItemByLegacyId), which returns full listing details
including eBay-hosted image URLs (no expiry headaches like FB's CDN). Also
downloads images locally so fb_post.py can attach them.

Usage:
  uv run ebay_fetch.py https://www.ebay.com/itm/123456789012
  uv run ebay_fetch.py 123456789012 --out /tmp/draft.json

The category and condition shown on FB use FB's vocabulary, not eBay's, so
this script preserves the eBay strings under ebayCategoryPath / ebayCondition
and leaves Claude to map them to fbCategory / fbCondition before posting.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from ebay_auth import api_host, get_access_token, load_env  # noqa: E402

CACHE_DIR = Path.home() / ".cache" / "fb-to-ebay"


def slugify(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")[:60] or "ebay-listing"


def extract_legacy_id(arg: str) -> str:
    if arg.isdigit():
        return arg
    m = re.search(r"/itm/(?:[^/]+/)?(\d{9,14})", arg)
    if not m:
        sys.exit(f"Couldn't find a legacy item ID in {arg!r}. Pass either the URL or the bare item-ID.")
    return m.group(1)


def fetch_item(env: dict, token: str, legacy_id: str) -> dict:
    resp = httpx.get(
        f"https://{api_host(env)}/buy/browse/v1/item/get_item_by_legacy_id",
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": env.get("EBAY_MARKETPLACE_ID", "EBAY_US"),
        },
        params={"legacy_item_id": legacy_id, "fieldgroups": "PRODUCT,COMPACT,ADDITIONAL_SELLER_DETAILS"},
        timeout=30,
    )
    if resp.status_code != 200:
        sys.exit(f"Browse API failed ({resp.status_code}): {resp.text}")
    return resp.json()


def to_draft(item: dict) -> dict:
    images: list[str] = []
    if item.get("image", {}).get("imageUrl"):
        images.append(item["image"]["imageUrl"])
    for extra in item.get("additionalImages", []) or []:
        if extra.get("imageUrl") and extra["imageUrl"] not in images:
            images.append(extra["imageUrl"])

    price = item.get("price", {})
    return {
        "title": item.get("title", ""),
        "description": item.get("description") or item.get("shortDescription", ""),
        "ebayCondition": item.get("condition", ""),
        "ebayConditionId": item.get("conditionId"),
        "ebayCategoryPath": item.get("categoryPath", ""),
        "ebayCategoryId": item.get("categoryId"),
        "price": {
            "value": str(price.get("value", "")),
            "currency": price.get("currency", "USD"),
        },
        "imageUrls": images,
        "location": ", ".join(
            v for v in [
                item.get("itemLocation", {}).get("city"),
                item.get("itemLocation", {}).get("stateOrProvince"),
            ] if v
        ) or None,
        "sourceUrl": item.get("itemWebUrl", ""),
    }


def download_images(urls: list[str], out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for i, url in enumerate(urls):
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                print(f"  ! Failed to download image {i + 1}: {e}", file=sys.stderr)
                continue
            ext = ".jpg"
            ctype = resp.headers.get("content-type", "")
            if "png" in ctype:
                ext = ".png"
            elif "webp" in ctype:
                ext = ".webp"
            path = out_dir / f"img-{i + 1:02d}{ext}"
            path.write_bytes(resp.content)
            saved.append(str(path))
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("listing", help="eBay listing URL or bare legacy item ID")
    parser.add_argument("--out", type=Path, help="Write draft JSON here (default: stdout)")
    parser.add_argument("--no-images", action="store_true", help="Skip image download")
    args = parser.parse_args()

    legacy_id = extract_legacy_id(args.listing)
    env = load_env()
    token = get_access_token(env)
    item = fetch_item(env, token, legacy_id)
    draft = to_draft(item)

    if not args.no_images and draft["imageUrls"]:
        cache_dir = CACHE_DIR / slugify(draft["title"])
        print(f"Downloading {len(draft['imageUrls'])} image(s) to {cache_dir}...", file=sys.stderr)
        draft["localImages"] = download_images(draft["imageUrls"], cache_dir)

    output = json.dumps(draft, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(output)
        print(f"✓ Wrote draft to {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
