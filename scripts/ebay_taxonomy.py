#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""Suggest eBay categories for a given title.

Usage: uv run ebay_taxonomy.py "Vintage Levi's 501 jeans size 32x34"

Prints up to 3 suggestions as JSON, each with categoryId, categoryName,
and the full categoryPath ("Clothing > Men > Jeans").
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from ebay_auth import api_host, get_access_token, load_env  # noqa: E402

DEFAULT_MARKETPLACE = "EBAY_US"


def get_category_tree_id(env: dict, token: str, marketplace: str) -> str:
    resp = httpx.get(
        f"https://{api_host(env)}/commerce/taxonomy/v1/get_default_category_tree_id",
        headers={"Authorization": f"Bearer {token}"},
        params={"marketplace_id": marketplace},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["categoryTreeId"]


def get_suggestions(env: dict, token: str, tree_id: str, query: str) -> list[dict]:
    resp = httpx.get(
        f"https://{api_host(env)}/commerce/taxonomy/v1/category_tree/{tree_id}/get_category_suggestions",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": query},
        timeout=30,
    )
    if resp.status_code == 204:
        return []
    resp.raise_for_status()
    return resp.json().get("categorySuggestions", [])


def format_suggestion(s: dict) -> dict:
    cat = s.get("category", {})
    ancestors = s.get("categoryTreeNodeAncestors", []) or []
    # Ancestors come in leaf→root order in the API; reverse for display.
    path_parts = [a["categoryName"] for a in reversed(ancestors)] + [cat.get("categoryName", "")]
    return {
        "categoryId": cat.get("categoryId"),
        "categoryName": cat.get("categoryName"),
        "categoryPath": " > ".join(p for p in path_parts if p),
    }


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: ebay_taxonomy.py \"<title>\"")
    query = sys.argv[1]
    env = load_env()
    marketplace = env.get("EBAY_MARKETPLACE_ID", DEFAULT_MARKETPLACE)
    token = get_access_token(env)
    tree_id = get_category_tree_id(env, token, marketplace)
    suggestions = get_suggestions(env, token, tree_id, query)
    if not suggestions:
        sys.exit(f"No category suggestions for {query!r}.")
    print(json.dumps([format_suggestion(s) for s in suggestions[:3]], indent=2))


if __name__ == "__main__":
    main()
