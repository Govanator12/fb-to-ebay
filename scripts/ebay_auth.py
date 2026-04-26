#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""eBay OAuth user-grant flow + token cache.

Subcommands:
  login   - Print the authorization URL, prompt the user to paste the
            redirect URL after consent, exchange for tokens, save.
  refresh - Force a refresh of the access token using the cached refresh token.
  token   - Print the current valid access token (refreshing if needed).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

import httpx

CONFIG_DIR = Path.home() / ".config" / "fb-to-ebay"
TOKEN_PATH = CONFIG_DIR / "token.json"
ENV_PATH = CONFIG_DIR / ".env"

SCOPES = [
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account.readonly",
    "https://api.ebay.com/oauth/api_scope/commerce.catalog.readonly",
]


def load_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        sys.exit(
            f"Missing {ENV_PATH}. Copy .env.example from the skill directory "
            f"to {ENV_PATH} and fill in your eBay developer credentials."
        )
    env: dict[str, str] = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    for required in ("EBAY_APP_ID", "EBAY_CERT_ID", "EBAY_RUNAME", "EBAY_ENV"):
        if required not in env:
            sys.exit(f"{ENV_PATH} is missing {required}")
    if env["EBAY_ENV"] not in ("sandbox", "production"):
        sys.exit(f"EBAY_ENV must be 'sandbox' or 'production', got {env['EBAY_ENV']!r}")
    return env


def auth_host(env: dict[str, str]) -> str:
    return "auth.sandbox.ebay.com" if env["EBAY_ENV"] == "sandbox" else "auth.ebay.com"


def api_host(env: dict[str, str]) -> str:
    return "api.sandbox.ebay.com" if env["EBAY_ENV"] == "sandbox" else "api.ebay.com"


def basic_auth(env: dict[str, str]) -> str:
    raw = f"{env['EBAY_APP_ID']}:{env['EBAY_CERT_ID']}".encode()
    return base64.b64encode(raw).decode()


def save_token(payload: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload["fetched_at"] = int(time.time())
    TOKEN_PATH.write_text(json.dumps(payload, indent=2))
    os.chmod(TOKEN_PATH, 0o600)


def load_token() -> dict:
    if not TOKEN_PATH.exists():
        sys.exit(
            f"No cached token at {TOKEN_PATH}. Run "
            f"`uv run {Path(__file__).resolve()} login` first."
        )
    return json.loads(TOKEN_PATH.read_text())


def cmd_login(env: dict[str, str]) -> None:
    params = {
        "client_id": env["EBAY_APP_ID"],
        "response_type": "code",
        "redirect_uri": env["EBAY_RUNAME"],
        "scope": " ".join(SCOPES),
    }
    url = f"https://{auth_host(env)}/oauth2/authorize?{urllib.parse.urlencode(params)}"
    print("\nOpen this URL in your browser, log in, and grant consent:\n")
    print(url)
    print(
        "\nAfter approving, eBay will redirect you to your RuName's landing page. "
        "Copy the FULL URL of that page (it contains ?code=...) and paste it below."
    )
    redirected = input("\nRedirect URL: ").strip()
    parsed = urllib.parse.urlparse(redirected)
    code = urllib.parse.parse_qs(parsed.query).get("code", [None])[0]
    if not code:
        sys.exit("Could not find ?code= in the URL you pasted.")

    resp = httpx.post(
        f"https://{api_host(env)}/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {basic_auth(env)}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": env["EBAY_RUNAME"],
        },
        timeout=30,
    )
    if resp.status_code != 200:
        sys.exit(f"Token exchange failed ({resp.status_code}): {resp.text}")
    save_token(resp.json())
    print(f"\n✓ Saved token to {TOKEN_PATH}")


def cmd_refresh(env: dict[str, str]) -> dict:
    token = load_token()
    if "refresh_token" not in token:
        sys.exit("Cached token has no refresh_token; run `login` again.")
    resp = httpx.post(
        f"https://{api_host(env)}/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {basic_auth(env)}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "scope": " ".join(SCOPES),
        },
        timeout=30,
    )
    if resp.status_code != 200:
        sys.exit(f"Refresh failed ({resp.status_code}): {resp.text}")
    new_token = resp.json()
    # eBay refresh response doesn't return refresh_token; preserve the existing one.
    new_token.setdefault("refresh_token", token["refresh_token"])
    save_token(new_token)
    return new_token


def get_access_token(env: dict[str, str]) -> str:
    """Library helper: return a valid access token, refreshing if expired."""
    token = load_token()
    expires_at = token.get("fetched_at", 0) + token.get("expires_in", 0) - 60
    if time.time() >= expires_at:
        token = cmd_refresh(env)
    return token["access_token"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["login", "refresh", "token"])
    args = parser.parse_args()
    env = load_env()
    if args.command == "login":
        cmd_login(env)
    elif args.command == "refresh":
        cmd_refresh(env)
        print("✓ Access token refreshed.")
    elif args.command == "token":
        print(get_access_token(env))


if __name__ == "__main__":
    main()
