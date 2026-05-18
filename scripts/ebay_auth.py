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

CONFIG_DIR = Path.home() / ".config" / "ebay-lister"
ENV_PATH = CONFIG_DIR / ".env"

# Keys whose value is environment-specific. The .env may set
# EBAY_SANDBOX_<KEY> and EBAY_PRODUCTION_<KEY> independently; load_env
# promotes the active environment's value to bare EBAY_<KEY> so the rest
# of the codebase reads it as before. If only the bare EBAY_<KEY> is set,
# it's used regardless of environment (legacy single-env layout).
ENV_SCOPED_KEYS = (
    "APP_ID", "CERT_ID", "DEV_ID", "RUNAME",
    "FULFILLMENT_POLICY_ID", "PAYMENT_POLICY_ID", "RETURN_POLICY_ID",
    "MERCHANT_LOCATION_KEY",
)


def token_path(env: dict[str, str]) -> Path:
    """Per-environment token file so sandbox + production tokens coexist."""
    return CONFIG_DIR / f"token-{env['EBAY_ENV']}.json"

SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
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
    if env.get("EBAY_ENV") not in ("sandbox", "production"):
        sys.exit(f"EBAY_ENV must be 'sandbox' or 'production', got {env.get('EBAY_ENV')!r}")
    # Promote env-scoped values to the bare key so a single .env can hold
    # both sandbox and production credentials side by side.
    prefix = f"EBAY_{env['EBAY_ENV'].upper()}_"
    for suffix in ENV_SCOPED_KEYS:
        scoped_val = env.get(prefix + suffix)
        if scoped_val:
            env[f"EBAY_{suffix}"] = scoped_val
    for required in ("EBAY_APP_ID", "EBAY_CERT_ID", "EBAY_RUNAME"):
        if not env.get(required):
            sys.exit(
                f"{ENV_PATH} is missing {required} (or {prefix}{required[5:]} "
                f"for the active EBAY_ENV={env['EBAY_ENV']})"
            )
    return env


def auth_host(env: dict[str, str]) -> str:
    return "auth.sandbox.ebay.com" if env["EBAY_ENV"] == "sandbox" else "auth.ebay.com"


def api_host(env: dict[str, str]) -> str:
    return "api.sandbox.ebay.com" if env["EBAY_ENV"] == "sandbox" else "api.ebay.com"


def basic_auth(env: dict[str, str]) -> str:
    raw = f"{env['EBAY_APP_ID']}:{env['EBAY_CERT_ID']}".encode()
    return base64.b64encode(raw).decode()


def save_token(env: dict[str, str], payload: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = token_path(env)
    payload["fetched_at"] = int(time.time())
    path.write_text(json.dumps(payload, indent=2))
    os.chmod(path, 0o600)


def load_token(env: dict[str, str]) -> dict:
    path = token_path(env)
    if not path.exists():
        # Fall back to the legacy single-environment token file for backward
        # compat with users who set up before the per-env split.
        legacy = CONFIG_DIR / "token.json"
        if legacy.exists():
            return json.loads(legacy.read_text())
        sys.exit(
            f"No cached token at {path}. Run "
            f"`uv run {Path(__file__).resolve()} login` first."
        )
    return json.loads(path.read_text())


def cmd_login(env: dict[str, str], redirect_url: str | None = None) -> None:
    if redirect_url is None:
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
            "Copy the FULL URL of that page (it contains ?code=...) and paste it below "
            "— or re-run with --redirect-url <url> if you can't paste interactively."
        )
        try:
            redirect_url = input("\nRedirect URL: ").strip()
        except EOFError:
            sys.exit(
                "\nNo stdin available. Re-run with the redirect URL as a flag:\n"
                f"  uv run {Path(__file__).name} login --redirect-url \"<paste-url-here>\""
            )

    parsed = urllib.parse.urlparse(redirect_url)
    code = urllib.parse.parse_qs(parsed.query).get("code", [None])[0]
    if not code:
        sys.exit("Could not find ?code= in the URL you provided.")

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
    save_token(env, resp.json())
    print(f"\n✓ Saved token to {token_path(env)}")


def cmd_refresh(env: dict[str, str]) -> dict:
    token = load_token(env)
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
    save_token(env, new_token)
    return new_token


def get_access_token(env: dict[str, str]) -> str:
    """Library helper: return a valid access token, refreshing if expired."""
    token = load_token(env)
    expires_at = token.get("fetched_at", 0) + token.get("expires_in", 0) - 60
    if time.time() >= expires_at:
        token = cmd_refresh(env)
    return token["access_token"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["login", "refresh", "token"])
    parser.add_argument(
        "--redirect-url",
        help="(login only) Pass the post-consent redirect URL on the command line "
        "instead of pasting it interactively. Useful when stdin isn't available.",
    )
    args = parser.parse_args()
    env = load_env()
    if args.command == "login":
        cmd_login(env, redirect_url=args.redirect_url)
    elif args.command == "refresh":
        cmd_refresh(env)
        print("✓ Access token refreshed.")
    elif args.command == "token":
        print(get_access_token(env))


if __name__ == "__main__":
    main()
