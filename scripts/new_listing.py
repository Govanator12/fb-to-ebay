#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Scaffold a blank eBay draft for listing an item *from scratch* (no Facebook source).

This is the entry point for the skill's "from scratch" mode. Unlike the
crosspost flow — where `fb_fetch.py` produces a half-filled draft scraped from
a Marketplace listing — here there is no source listing. This script writes a
template draft JSON with every field present (placeholders for the ones you
must fill in), optionally pre-populating `localImages` from a folder of photos.

Claude then fills in the placeholders in conversation (polished title,
description, condition, category, price, shipping), and `ebay_publish.py`
publishes the result. The draft shape is identical to the crosspost flow —
see references/draft_schema.md.

Usage:
  uv run new_listing.py --out /tmp/ebay-draft.json
  uv run new_listing.py --out /tmp/ebay-draft.json --images ~/Pictures/desk-lamp
  uv run new_listing.py --out /tmp/ebay-draft.json --images a.jpg b.jpg c.jpg
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Placeholder sentinel. ebay_publish.py's validate_draft() rejects empty/
# missing required fields, so an un-filled draft fails fast with a clear
# message rather than publishing garbage.
TODO = "TODO"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp", ".tiff"}


def collect_images(items: list[str]) -> list[str]:
    """Resolve --images args (a directory and/or individual files) to file paths.

    A single directory arg is expanded to every image file inside it, sorted
    by name so photo order is stable. Individual file args are kept as given.
    """
    paths: list[str] = []
    for item in items:
        p = Path(item).expanduser()
        if p.is_dir():
            found = sorted(
                c for c in p.iterdir()
                if c.is_file() and c.suffix.lower() in IMAGE_EXTS
            )
            if not found:
                print(f"  ! no image files found in {p}", file=sys.stderr)
            paths.extend(str(c) for c in found)
        elif p.is_file():
            paths.append(str(p))
        else:
            print(f"  ! image path does not exist: {p}", file=sys.stderr)
    return paths


def build_template(images: list[str]) -> dict:
    """A draft with every field present — required ones as TODO placeholders.

    Optional fields are included too so the from-scratch user sees the full
    surface area; delete any that don't apply before publishing.
    """
    return {
        "_instructions": (
            "Fill in every TODO below. Required: title, description, condition, "
            "categoryId, price. Run `ebay_publish.py --dry-run --draft <thisfile>` "
            "to validate, then drop --dry-run to publish. See "
            "references/draft_schema.md and references/ebay_field_map.md. "
            "Delete this _instructions key and any optional fields you don't need."
        ),
        "title": TODO,                       # <=80 chars, front-load keywords
        "description": TODO,                 # plain text or simple HTML
        "condition": TODO,                   # enum, e.g. NEW / USED_EXCELLENT / USED_GOOD
        "conditionDescription": "",          # optional free-text note; "" = omit
        "categoryId": TODO,                  # from ebay_taxonomy.py
        "price": {"value": TODO, "currency": "USD"},
        "quantity": 1,
        "weightLbs": TODO,                   # number; needed for calculated shipping
        "boxDimensionsIn": [TODO, TODO, TODO],  # [L, W, H]
        "localImages": images,               # file paths; EPS-uploaded at publish time
        "aspects": {},                       # {"Brand": ["..."], ...} — fill if category requires
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, type=Path,
                        help="Where to write the draft JSON (e.g. /tmp/ebay-draft.json)")
    parser.add_argument("--images", nargs="*", default=[],
                        help="A folder of photos and/or individual image files")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite --out if it already exists")
    args = parser.parse_args()

    if args.out.exists() and not args.force:
        sys.exit(f"{args.out} already exists — pass --force to overwrite.")

    images = collect_images(args.images)
    template = build_template(images)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(template, indent=2) + "\n")

    print(f"✓ Wrote draft template to {args.out}")
    if images:
        print(f"  {len(images)} image(s) pre-filled into localImages:")
        for p in images:
            print(f"    - {p}")
    else:
        print("  No images yet — add file paths to the localImages array "
              "(at least one image is required to publish).")
    print()
    print("Next:")
    print("  1. Fill in every TODO in the draft (title, description, condition,")
    print("     categoryId, price, weight + dimensions).")
    print("  2. Suggest a category:  uv run ebay_taxonomy.py \"<polished title>\"")
    print("  3. Check conditions:    uv run ebay_conditions.py <categoryId>")
    print("  4. Validate:            uv run ebay_publish.py --dry-run --draft "
          f"{args.out}")
    print("  5. Publish:             uv run ebay_publish.py --draft "
          f"{args.out}")


if __name__ == "__main__":
    main()
