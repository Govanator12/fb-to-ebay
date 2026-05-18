#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright>=1.45", "httpx>=0.27"]
# ///
"""Scrape a Facebook Marketplace listing into a draft JSON.

Uses the FB session captured by fb_session.py (which must be run first).
Downloads images locally to ~/.cache/ebay-lister/<slug>/ so they don't
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
import html as html_lib
import json
import re
import sys
from pathlib import Path

import httpx

SESSION_PATH = Path.home() / ".config" / "ebay-lister" / "fb_session.json"
CACHE_DIR = Path.home() / ".cache" / "ebay-lister"


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


def safe_attr(page, selector: str, attr: str = "content") -> str | None:
    """Return an attribute value if the selector matches, else None.

    Calling get_attribute() on a non-existent locator blocks for 30s before
    timing out, so we count first and skip the call entirely if there's no
    match. FB's DOM omits og:* meta tags in some logged-in views.
    """
    loc = page.locator(selector)
    if loc.count() == 0:
        return None
    try:
        return loc.first.get_attribute(attr, timeout=2_000)
    except Exception:
        return None


def extract_listing(page) -> dict:
    """Extract fields from a loaded Marketplace listing page.

    Selectors are best-effort; FB rewrites their DOM frequently. We lean on
    og:* meta tags where they exist and fall back to visible-text heuristics.
    """
    # Wait for the page to be substantively loaded. h1 is the user-visible signal;
    # the meta-tag check uses state="attached" since meta tags live in <head>
    # and are never "visible" by Playwright's default check.
    page.wait_for_selector("h1, meta[property='og:title']", state="attached", timeout=30_000)

    title = safe_attr(page, "meta[property='og:title']") or ""
    if not title:
        # FB renders multiple h1s (e.g. "Chats" in the Messenger sidebar). Scope
        # to the main content region so we get the listing's title, not chrome.
        # Falls back to the longest h1 on the page if [role=main] isn't there.
        candidates = page.locator("[role='main'] h1, [role='article'] h1").all() or page.locator("h1").all()
        for h1 in candidates:
            try:
                txt = h1.inner_text(timeout=2_000).strip()
                if txt and len(txt) > len(title):
                    title = txt
            except Exception:
                continue

    og_desc = safe_attr(page, "meta[property='og:description']") or ""

    # Try to expand "See more" so the full description is in the DOM.
    try:
        see_more = page.get_by_role("button", name=re.compile(r"see more", re.I)).first
        if see_more.count():
            see_more.click(timeout=2_000)
    except Exception:
        pass

    # Description: prefer the first long-ish text block on the page that isn't the title.
    description = og_desc
    try:
        for block in page.locator("div[dir='auto']").all()[:20]:
            try:
                txt = block.inner_text(timeout=2_000).strip()
            except Exception:
                continue
            if len(txt) > len(description) and txt != title:
                description = txt
                break
    except Exception:
        pass

    body_text = page.inner_text("body", timeout=5_000)
    price = parse_price(body_text)

    condition_match = re.search(
        r"(?:Condition[:\s]+)([A-Za-z][A-Za-z \-–]{2,40})",
        body_text,
    )
    fb_condition = condition_match.group(1).strip() if condition_match else None

    location_match = re.search(r"(?:Location[:\s]+|Listed in\s+)([A-Za-z][\w ,.\-]{2,60})", body_text)
    location = location_match.group(1).strip() if location_match else None

    image_urls = collect_listing_photos(page, og_img=safe_attr(page, "meta[property='og:image']"))

    # Description fallback: scan the page HTML for the JSON-embedded description.
    # FB's React app usually hydrates listing data in a script tag like
    # {"redacted_description":{"text":"..."}} or {"description":{"text":"..."}}.
    if not description:
        try:
            html = page.content()
            for pattern in (
                r'"redacted_description"\s*:\s*{\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"',
                r'"marketplace_listing_description"\s*:\s*{\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"',
                r'"description"\s*:\s*{\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"',
            ):
                m = re.search(pattern, html)
                if m:
                    description = bytes(m.group(1), "utf-8").decode("unicode_escape")
                    break
        except Exception:
            pass

    return {
        "title": title,
        "description": description,
        "fbCondition": fb_condition,
        "price": price,
        "location": location,
        "imageUrls": image_urls,
    }


LISTING_PHOTO_PATH_RE = re.compile(r'https://[^"\s\\]+/t45\.5328-4/([0-9]+_[0-9]+_[0-9]+_n)\.jpg[^"\s\\]*')


def collect_listing_photos(page, og_img: str | None) -> list[str]:
    """Pull just the listing's own photos, not related-items sidebar thumbs.

    The listing's photos all share the `t45.5328-4` CDN path (FB's marketplace
    listing-photo bucket); related-items sidebar thumbs use `t39.84726-6` with
    a `?stp=c<crop>...` parameter. The reliable way to find every listing
    photo is to grep the rendered HTML — every photo is referenced there even
    if its <img> tag isn't yet rendered (FB lazy-loads the carousel beyond
    the cover). DOM walking misses these; HTML scanning catches them all.

    We dedupe by the file-ID portion of the URL (multiple URL variants of the
    same photo at different sizes/crops appear in the HTML) and prefer the
    largest available variant.
    """
    html = page.content()
    by_id: dict[str, str] = {}
    for match in LISTING_PHOTO_PATH_RE.finditer(html):
        url = html_lib.unescape(match.group(0))  # &amp; -> &
        file_id = match.group(1)
        # Skip cropped thumbnails — even on the listing-photo CDN path, FB sometimes
        # uses `c<x>.<y>.<w>.<h>a_` crop parameters for related-item previews.
        if re.search(r"stp=c\d+\.\d+\.\d+\.\d+", url):
            continue
        existing = by_id.get(file_id, "")
        if not existing or _photo_size_score(url) > _photo_size_score(existing):
            by_id[file_id] = url

    # Reorder so the cover photo is first — eBay treats imageUrls[0] as the
    # primary listing photo. Two ways to find FB's cover:
    #   1. og:image meta tag (only present on some logged-in renderings)
    #   2. <img alt="Product photo of …"> — FB sets this on the listing's
    #      main image element. We grep the HTML for src adjacent to that alt.
    cover_file_id = None
    if og_img:
        m = LISTING_PHOTO_PATH_RE.search(og_img)
        if m:
            cover_file_id = m.group(1)
    if not cover_file_id:
        m = re.search(
            r'alt="Product photo of [^"]*"[^>]*src="(https?://[^"]+/t45\.5328-4/([0-9]+_[0-9]+_[0-9]+_n)\.jpg[^"]*)"',
            html,
        )
        # The src= attribute may come BEFORE the alt= attribute too.
        if not m:
            m = re.search(
                r'src="(https?://[^"]+/t45\.5328-4/([0-9]+_[0-9]+_[0-9]+_n)\.jpg[^"]*)"[^>]*alt="Product photo of',
                html,
            )
        if m:
            cover_file_id = m.group(2)

    photos: list[str] = []
    if cover_file_id and cover_file_id in by_id:
        photos.append(by_id.pop(cover_file_id))
    photos.extend(by_id.values())

    if not photos and og_img:
        photos.append(og_img)
    return photos


def _photo_size_score(url: str) -> int:
    """Rank a photo URL by its embedded size hint (larger = better)."""
    m = re.search(r"_[ps](\d+)x(\d+)", url)
    if m:
        return int(m.group(1)) * int(m.group(2))
    if "stp=dst-jpg" in url and "_s" in url:
        m = re.search(r"_s(\d+)x(\d+)", url)
        if m:
            return int(m.group(1)) * int(m.group(2))
    return 1000  # unknown but uncropped → assume reasonable


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
