#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright>=1.45"]
# ///
"""Capture a Facebook login session for the Playwright-based scripts.

Launches a real Chromium window pointing at facebook.com. You log in by hand
(handles 2FA, CAPTCHA, security challenges naturally). When you're done, come
back to this terminal and press Enter — the script saves the browser context
state to ~/.config/ebay-lister/fb_session.json so future scripts can reuse it
without prompting for credentials.

Run again whenever the session expires (FB usually keeps you logged in for
weeks unless you trigger a security check).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "ebay-lister"
SESSION_PATH = CONFIG_DIR / "fb_session.json"


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit(
            "playwright not available. This script needs `uv` to resolve "
            "playwright as a script dependency, then a one-time browser "
            "install: `uv run --with playwright playwright install chromium`"
        )

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    print("Opening Chromium. Log in to Facebook by hand — solve any 2FA / "
          "CAPTCHA if asked. When you can see your news feed, come back here.")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=False)
        except Exception as e:
            sys.exit(
                f"Failed to launch Chromium: {e}\n"
                "If this is a missing-browser error, run:\n"
                "  uv run --with playwright playwright install chromium"
            )
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")

        input("\n→ Press Enter HERE once you're logged in (the browser stays open).\n")

        context.storage_state(path=str(SESSION_PATH))
        browser.close()

    os.chmod(SESSION_PATH, 0o600)
    print(f"\n✓ Session saved to {SESSION_PATH}")


if __name__ == "__main__":
    main()
