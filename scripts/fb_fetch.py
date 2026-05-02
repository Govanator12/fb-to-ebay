#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright>=1.45", "httpx>=0.27"]
# ///
"""Scrape a Facebook Marketplace listing into a draft JSON.

Uses the FB session captured by fb_session.py (which must be run first).
Downloads images locally to ~/.cache/fb-to-ebay/<slug>/ so they don't
expire, since FB image URLs are signed and short-lived.

Usage:
  uv run fb_fetch.py "https://www.facebook.com/marketplace/item/1234567890/"
  uv run fb_fetch.py "..." --out /tmp/draft.json

Prints a partial draft JSON to stdout (or --out path). The category, eBay
condition enum, and policy IDs are NOT filled in — Claude completes those
in conversation before calling ebay_publish.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import httpx

SESSION_PATH = Path.home() / ".config" / "fb-to-ebay" / "fb_session.json"
CACHE_DIR = Path.home() / ".cache" / "fb-to-ebay"


def slugify(s: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")
    return safe[:60] or hashlib.md5(s.encode()).hexdigest()[:12]


def parse_price(text: str) -> dict | None:
    # Match $1,234.56 / £45 / €99,99 etc.
    m = re.search(r"([\$£€¥])\s?([\d,.\s]+)", text)
    if not m:
        return None
    symbol, raw = m.group(1), m.group(2)
    cleaned = raw.replace(",", "").replace(" ", "")
    try:
        value = f"{float(cleaned):.2f}"
    except ValueError:
        return None
    currency = {"$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY"}.get(symbol, "USD")
    return {"value": value, "currency": currency}


def extract_listing(page) -> dict:
    """Extract fields from a loaded Marketplace listing page.

    Selectors are best-effort; FB rewrites their DOM frequently. We lean on
    og:* meta tags where possible and use visible-text heuristics elsewhere.
    """
    # Wait for either the title heading or an og:title meta — whichever appears.
    page.wait_for_selector("h1, meta[property='og:title']", timeout=30_000)

    title = page.locator("meta[property='og:title']").get_attribute("content")
    if not title:
        h1 = page.locator("h1").first
        title = h1.inner_text() if h1.count() else ""

    og_desc = page.locator("meta[property='og:description']").get_attribute("content") or ""

    # Try to expand "See more" so the full description is in the DOM.
    try:
        see_more = page.get_by_role("button", name=re.compile(r"see more", re.I)).first
        if see_more.count():
            see_more.click(timeout=2_000)
    except Exception:
        pass

    # Description: prefer the first long-ish text block on the page that isn't the title.
    description = og_desc
    for block in page.locator("div[dir='auto']").all()[:20]:
        try:
            txt = block.inner_text().strip()
        except Exception:
            continue
        if len(txt) > len(description) and txt != title:
            description = txt
            break

    body_text = page.inner_text("body")
    price = parse_price(body_text)

    condition_match = re.search(
        r"(?:Condition[:\s]+)([A-Za-z][A-Za-z \-–]{2,40})",
        body_text,
    )
    fb_condition = condition_match.group(1).strip() if condition_match else None

    location_match = re.search(r"(?:Location[:\s]+|Listed in\s+)([A-Za-z][\w ,.\-]{2,60})", body_text)
    location = location_match.group(1).strip() if location_match else None

    # Image URLs: collect from og:image plus any large carousel <img>s.
    image_urls: list[str] = []
    og_img = page.locator("meta[property='og:image']").get_attribute("content")
    if og_img:
        image_urls.append(og_img)
    for img in page.locator("img").all():
        try:
            src = img.get_attribute("src") or ""
            if src.startswith("http") and "scontent" in src and src not in image_urls:
                # Skip tiny avatars/icons by checking rendered size
                box = img.bounding_box()
                if box and box["width"] >= 200:
                    image_urls.append(src)
        except Exception:
            continue

    return {
        "title": title,
        "description": description,
        "fbCondition": fb_condition,
        "price": price,
        "location": location,
        "imageUrls": image_urls,
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
    parser.add_argument("url", help="Facebook Marketplace listing URL")
    parser.add_argument("--out", type=Path, help="Write draft JSON here (default: stdout)")
    parser.add_argument("--no-images", action="store_true", help="Skip image download")
    args = parser.parse_args()

    if not SESSION_PATH.exists():
        sys.exit(
            f"No FB session at {SESSION_PATH}. Run fb_session.py first to log in."
        )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit(
            "playwright not available; install with: "
            "uv run --with playwright playwright install chromium"
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(SESSION_PATH))
        page = context.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)
        listing = extract_listing(page)
        browser.close()

    listing["sourceUrl"] = args.url
    if not args.no_images and listing["imageUrls"]:
        slug = slugify(listing["title"] or "listing")
        cache_dir = CACHE_DIR / slug
        print(f"Downloading {len(listing['imageUrls'])} image(s) to {cache_dir}...", file=sys.stderr)
        listing["localImages"] = download_images(listing["imageUrls"], cache_dir)

    output = json.dumps(listing, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(output)
        print(f"✓ Wrote draft to {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
