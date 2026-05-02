#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright>=1.45"]
# ///
"""Open the Facebook Marketplace create-listing form, pre-fill it from a draft, leave it for the user to review and publish.

By default the script does NOT click Publish — it stops with the form filled
out and the browser open, so you can eyeball everything and submit by hand.
This is intentional: FB's automated-activity detection is touchy and a wrong
selector could create garbage listings. Pass --auto-publish only if you've
done a few successful manual runs and trust the selectors.

Draft JSON shape (the keys this script reads):
  {
    "title": str,
    "price": { "value": str, "currency": str },
    "fbCategory": str,       # exact text shown in FB's category dropdown
    "fbCondition": str,      # exact text shown in FB's condition dropdown
    "description": str,
    "localImages": [str],    # absolute file paths
    "location": str          # optional, used if FB doesn't pre-fill
  }
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

SESSION_PATH = Path.home() / ".config" / "fb-to-ebay" / "fb_session.json"
CREATE_URL = "https://www.facebook.com/marketplace/create/item"


def warn(msg: str) -> None:
    print(f"  ! {msg}", file=sys.stderr)


def fill_text(page, label_pattern: str, value: str) -> bool:
    """Find an input/textarea labeled by the given regex and fill it. Return success."""
    try:
        field = page.get_by_label(re.compile(label_pattern, re.I)).first
        field.wait_for(state="visible", timeout=10_000)
        field.click()
        field.fill(value)
        return True
    except Exception as e:
        warn(f"Could not fill field matching {label_pattern!r}: {e}")
        return False


def select_combobox_option(page, label_pattern: str, option_text: str) -> bool:
    """Open a combobox-style FB dropdown by its label and pick an option by exact text."""
    try:
        combo = page.get_by_label(re.compile(label_pattern, re.I)).first
        combo.wait_for(state="visible", timeout=10_000)
        combo.click()
        # Wait for options to appear, then click the matching one.
        page.get_by_role("option", name=re.compile(re.escape(option_text), re.I)).first.click(
            timeout=10_000
        )
        return True
    except Exception as e:
        warn(f"Could not pick option {option_text!r} from {label_pattern!r}: {e}")
        return False


def upload_photos(page, paths: list[str]) -> bool:
    if not paths:
        return True
    try:
        file_input = page.locator("input[type='file']").first
        file_input.set_input_files(paths)
        time.sleep(2)  # let thumbnails render
        return True
    except Exception as e:
        warn(f"Could not attach photos: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", required=True, type=Path)
    parser.add_argument(
        "--auto-publish",
        action="store_true",
        help="Click the Publish button after filling the form (default: leave for manual review).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run headlessly. Not recommended — FB is more likely to flag automated activity.",
    )
    args = parser.parse_args()

    if not SESSION_PATH.exists():
        sys.exit(f"No FB session at {SESSION_PATH}. Run fb_session.py first.")
    if not args.draft.exists():
        sys.exit(f"Draft not found: {args.draft}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit(
            "playwright not available; install with: "
            "uv run --with playwright playwright install chromium"
        )

    draft = json.loads(args.draft.read_text())

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(storage_state=str(SESSION_PATH))
        page = context.new_page()
        page.goto(CREATE_URL, wait_until="domcontentloaded", timeout=60_000)

        # Photos first — FB's form unlocks more fields once at least one photo is attached.
        if "localImages" in draft:
            print("Attaching photos...", file=sys.stderr)
            upload_photos(page, draft["localImages"])

        if "title" in draft:
            print("Filling title...", file=sys.stderr)
            fill_text(page, r"^title$", draft["title"])

        price = draft.get("price", {})
        if price.get("value"):
            print("Filling price...", file=sys.stderr)
            fill_text(page, r"^price$", price["value"])

        if "fbCategory" in draft:
            print(f"Picking category: {draft['fbCategory']}...", file=sys.stderr)
            select_combobox_option(page, r"^category$", draft["fbCategory"])

        if "fbCondition" in draft:
            print(f"Picking condition: {draft['fbCondition']}...", file=sys.stderr)
            select_combobox_option(page, r"^condition$", draft["fbCondition"])

        if "description" in draft:
            print("Filling description...", file=sys.stderr)
            fill_text(page, r"^description$", draft["description"])

        # Location is usually auto-filled from the user's account; only set if explicit.
        if draft.get("location"):
            fill_text(page, r"^location$", draft["location"])

        if args.auto_publish:
            print("Auto-publishing...", file=sys.stderr)
            try:
                # FB's create flow has a "Next" then a "Publish" — click both.
                page.get_by_role("button", name=re.compile(r"^next$", re.I)).first.click(timeout=10_000)
                page.get_by_role("button", name=re.compile(r"^publish$", re.I)).first.click(timeout=10_000)
                page.wait_for_load_state("networkidle", timeout=30_000)
                print("✓ Published.")
            except Exception as e:
                warn(f"Auto-publish failed: {e}. Browser left open so you can finish manually.")
                input("\nPress Enter to close the browser when you're done.\n")
        else:
            print(
                "\n→ Form pre-filled. Review in the browser, fix anything that looks "
                "wrong, then click Next → Publish yourself.",
                file=sys.stderr,
            )
            input("Press Enter HERE when you're done (browser will close).\n")

        browser.close()


if __name__ == "__main__":
    main()
