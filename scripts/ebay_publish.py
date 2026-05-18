#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""Publish a draft listing to eBay via the Sell Inventory API.

Reads a draft JSON file (see references/draft_schema.md), runs the three-step
chain createOrReplaceInventoryItem -> createOffer -> publishOffer, and prints
the live listing URL on success.

Usage:
  uv run ebay_publish.py --draft path/to/draft.json
  uv run ebay_publish.py --draft path/to/draft.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from ebay_auth import api_host, get_access_token, load_env  # noqa: E402

POLICY_ERROR_CODES = {25709, 25710, 25711}  # missing fulfillment / payment / return policy


class PublishError(RuntimeError):
    """Raised when an eBay API step fails, so we can roll back partial state."""


def required(d: dict, key: str, where: str) -> object:
    if key not in d:
        sys.exit(f"Draft is missing required field {where}.{key}")
    return d[key]


def make_sku(title: str) -> str:
    # eBay SKUs must be alphanumeric only (no hyphens / underscores), max 50 chars.
    safe = "".join(c for c in title.lower() if c.isalnum())[:35]
    return f"{safe}{int(time.time())}"


def post_json(url: str, token: str, body: dict, lang: str = "en-US") -> httpx.Response:
    return httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Content-Language": lang,
            "Accept-Language": lang,
        },
        json=body,
        timeout=60,
    )


def put_json(url: str, token: str, body: dict, lang: str = "en-US") -> httpx.Response:
    return httpx.put(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Content-Language": lang,
            "Accept-Language": lang,
        },
        json=body,
        timeout=60,
    )


def explain_failure(resp: httpx.Response, step: str) -> None:
    print(f"\n✗ {step} failed (HTTP {resp.status_code})", file=sys.stderr)
    try:
        body = resp.json()
        for err in body.get("errors", []):
            code = err.get("errorId")
            msg = err.get("longMessage") or err.get("message", "")
            print(f"  [{code}] {msg}", file=sys.stderr)
            if code in POLICY_ERROR_CODES:
                print(
                    "\n  → Your eBay account is missing a business policy. "
                    "Set them up at https://www.bizpolicy.ebay.com and add the "
                    "policy IDs to your draft (fulfillmentPolicyId, paymentPolicyId, "
                    "returnPolicyId).",
                    file=sys.stderr,
                )
            # eBay reports per-aspect single-value violations as errorId 25002
            # with text like "Theme should contain only one value."
            if "should contain only one value" in msg.lower():
                m = re.search(r"^([A-Z][\w /-]+?) should contain only one value", msg)
                aspect = m.group(1) if m else "this aspect"
                print(
                    f"\n  → Aspect '{aspect}' is single-valued in this category. "
                    f"In the draft's `aspects` block, pass exactly one element "
                    f"(e.g. \"{aspect}\": [\"<one-value>\"]) instead of multiple.",
                    file=sys.stderr,
                )
        if not body.get("errors"):
            print(json.dumps(body, indent=2), file=sys.stderr)
    except json.JSONDecodeError:
        print(resp.text, file=sys.stderr)
    raise PublishError(step)


def find_todo_placeholders(node: object, path: str = "draft") -> list[str]:
    """Recursively collect JSON paths whose value is the literal "TODO" sentinel.

    new_listing.py writes "TODO" into every field a from-scratch draft still
    needs. This walks the draft so a half-filled template fails pre-flight with
    an exact list of what's missing, instead of publishing placeholder text.
    """
    found: list[str] = []
    if isinstance(node, str):
        if node == "TODO":
            found.append(path)
    elif isinstance(node, dict):
        for key, value in node.items():
            found.extend(find_todo_placeholders(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(find_todo_placeholders(value, f"{path}[{i}]"))
    return found


def validate_draft(draft: dict) -> None:
    """Pre-flight checks that don't need an API call.

    Catches the obvious failure modes before we spend a token + EPS upload
    quota + policy creation chasing them. Errors at this layer surface to
    the user as a plain sys.exit (not PublishError) since nothing has been
    created yet — no rollback needed.
    """
    # new_listing.py scaffolds from-scratch drafts with the literal sentinel
    # "TODO" in every field the user still has to fill. Catch any that survived
    # before we publish a listing literally titled "TODO".
    todo_paths = find_todo_placeholders(draft)
    if todo_paths:
        sys.exit(
            "Draft still has unfilled TODO placeholder(s) from new_listing.py:\n"
            + "\n".join(f"  - {p}" for p in todo_paths)
            + "\nFill these in before publishing."
        )

    title = draft.get("title", "")
    if not title:
        sys.exit("Draft is missing required field: title")
    if len(title) > 80:
        sys.exit(
            f"Title is {len(title)} chars — eBay's hard limit is 80. "
            f"Trim before publishing.\n  Title: {title!r}"
        )
    if not draft.get("description"):
        sys.exit("Draft is missing required field: description")
    if not draft.get("imageUrls") and not draft.get("localImages"):
        sys.exit("Draft must include either imageUrls (HTTPS) or localImages (file paths)")
    # Most aspect values are single-valued; multi-value violates a per-category rule.
    # We can't perfectly pre-check (eBay's metadata varies by category) but we can
    # flag the obvious case where someone passed >1 string when stricter aspects
    # like Theme/Genre/Brand usually want exactly one.
    aspects = draft.get("aspects") or {}
    for name, values in aspects.items():
        if not isinstance(values, list):
            sys.exit(f"Draft aspects['{name}'] must be a list, got {type(values).__name__}")
        if len(values) > 1 and name in {"Theme", "Genre", "Brand", "Type", "Material", "Color"}:
            print(
                f"  ! Warning: aspect '{name}' has {len(values)} values "
                f"({values!r}); many categories restrict it to one. If publish "
                f"fails with 'should contain only one value', drop to a single "
                f"element.",
                file=sys.stderr,
            )


def create_inventory_item(env: dict, token: str, sku: str, draft: dict) -> None:
    body = {
        "availability": {"shipToLocationAvailability": {"quantity": draft.get("quantity", 1)}},
        "condition": required(draft, "condition", "draft"),
        "product": {
            "title": required(draft, "title", "draft"),
            "description": required(draft, "description", "draft"),
            "imageUrls": required(draft, "imageUrls", "draft"),
        },
    }
    if "aspects" in draft:
        body["product"]["aspects"] = draft["aspects"]
    if "conditionDescription" in draft:
        body["conditionDescription"] = draft["conditionDescription"]
    pkg = build_package_weight_and_size(draft, env)
    if pkg:
        body["packageWeightAndSize"] = pkg

    url = f"https://{api_host(env)}/sell/inventory/v1/inventory_item/{sku}"
    resp = put_json(url, token, body)
    if resp.status_code not in (200, 204):
        explain_failure(resp, "createOrReplaceInventoryItem")
    print(f"✓ Inventory item created (sku={sku})")


def build_package_weight_and_size(draft: dict, env: dict | None = None) -> dict | None:
    """Convert draft.weightLbs + draft.boxDimensionsIn into eBay's packageWeightAndSize.

    Units default to POUND / INCH (US sellers); override via EBAY_WEIGHT_UNIT
    (POUND, OUNCE, KILOGRAM, GRAM) and EBAY_DIMENSION_UNIT (INCH, CENTIMETER)
    in .env. Field names stay weightLbs / boxDimensionsIn even when the unit
    isn't pounds/inches — they're labels, not unit assertions.

    We deliberately omit packageType — eBay's USPS calculated rates throw
    'Invalid <ShippingPackage>' when a packageType is specified that isn't
    USPS-native (e.g. MAILING_BOX with USPSPriority). Letting eBay infer
    from weight + dimensions is more reliable.
    """
    weight = draft.get("weightLbs")
    dims = draft.get("boxDimensionsIn")
    if weight is None and not dims:
        return None
    env = env or {}
    weight_unit = env.get("EBAY_WEIGHT_UNIT", "POUND").upper()
    dim_unit = env.get("EBAY_DIMENSION_UNIT", "INCH").upper()
    out: dict = {}
    if weight is not None:
        out["weight"] = {"value": float(weight), "unit": weight_unit}
    if dims and len(dims) == 3:
        out["dimensions"] = {
            "length": float(dims[0]),
            "width": float(dims[1]),
            "height": float(dims[2]),
            "unit": dim_unit,
        }
    return out or None


def with_env_default(draft: dict, draft_key: str, env: dict, env_key: str, where: str) -> object:
    """Pull a value from the draft, falling back to an env var, then erroring."""
    if draft_key in draft:
        return draft[draft_key]
    if env_key in env:
        return env[env_key]
    sys.exit(f"Draft is missing {where}.{draft_key} and env has no {env_key}")


def _env_bool(env: dict, key: str, default: bool) -> bool:
    val = env.get(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


SHIPPING_OVERRIDE_KEYS = ("handlingDays", "localPickup", "shipInternationally", "weightLbs", "boxDimensionsIn")


def has_shipping_overrides(draft: dict, env: dict) -> bool:
    """Should we mint a per-listing fulfillment policy instead of using the env default?"""
    if any(k in draft for k in SHIPPING_OVERRIDE_KEYS):
        return True
    return any(env.get(k) for k in ("EBAY_DEFAULT_HANDLING_DAYS", "EBAY_OFFER_LOCAL_PICKUP", "EBAY_SHIP_INTERNATIONALLY"))


def build_dynamic_fulfillment_policy(env: dict, draft: dict, sku: str) -> dict:
    """Build a fulfillment policy body from the listing's shipping settings.

    Defaults to USPS Priority CALCULATED rate (eBay computes cost from
    package weight/dimensions + zip-to-zip), with the buyer paying. This
    matches what most casual sellers want. Free shipping is opt-in via
    `freeShipping: true` in the draft.
    """
    handling_days = int(draft.get("handlingDays") or env.get("EBAY_DEFAULT_HANDLING_DAYS") or 2)
    local_pickup = bool(draft["localPickup"]) if "localPickup" in draft else _env_bool(env, "EBAY_OFFER_LOCAL_PICKUP", True)
    ship_intl = bool(draft["shipInternationally"]) if "shipInternationally" in draft else _env_bool(env, "EBAY_SHIP_INTERNATIONALLY", False)
    free_shipping = bool(draft.get("freeShipping", False))

    # Offer multiple USPS services by default so the buyer can choose between
    # cheapest (Parcel / Ground, 2-5d) and fastest (Priority, 1-3d). Override
    # via EBAY_SHIPPING_SERVICES (comma-separated USPS service codes from
    # GeteBayDetails). Caveats:
    #   - Not every service supports CALCULATED rates. USPSGround and
    #     USPSGroundAdvantage get rejected by eBay's LSAS validator depending
    #     on environment / account state — pick something else if you see
    #     "LSAS validation failed".
    #   - USPSParcel is the canonical sandbox + production "ground" code; the
    #     similar USPSStandardPost gets silently renamed to USPSParcel on
    #     storage in production, which breaks our policy-reuse match logic.
    #     Use USPSParcel directly to avoid that.
    services_csv = env.get("EBAY_SHIPPING_SERVICES") or "USPSParcel,USPSPriority"
    service_codes = [s.strip() for s in services_csv.split(",") if s.strip()]
    domestic_services: list[dict] = []
    for i, code in enumerate(service_codes):
        svc = {
            "sortOrder": i + 1,
            "shippingCarrierCode": "USPS",
            "shippingServiceCode": code,
            "freeShipping": free_shipping,
            "buyerResponsibleForShipping": False,
            "buyerResponsibleForPickup": False,
        }
        if free_shipping:
            svc["shippingCost"] = {"value": "0.0", "currency": "USD"}
        domestic_services.append(svc)

    shipping_options = [{
        "optionType": "DOMESTIC",
        # CALCULATED requires the inventory item to have packageWeightAndSize.
        # If it doesn't, eBay will reject publish — we surface a helpful error.
        "costType": "FLAT_RATE" if free_shipping else "CALCULATED",
        "shippingServices": domestic_services,
    }]
    if ship_intl:
        shipping_options.append({
            "optionType": "INTERNATIONAL",
            "costType": "CALCULATED",
            "shippingServices": [{
                "sortOrder": 1,
                "shippingCarrierCode": "USPS",
                "shippingServiceCode": "USPSPriorityMailInternational",
                "freeShipping": False,
                "buyerResponsibleForShipping": False,
            }],
        })

    return {
        "name": f"fb2ebay-{sku[:40]}-{int(time.time())}"[:64],
        "marketplaceId": draft.get("marketplaceId", env.get("EBAY_MARKETPLACE_ID", "EBAY_US")),
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
        "handlingTime": {"value": handling_days, "unit": "DAY"},
        "shippingOptions": shipping_options,
        "pickupDropOff": local_pickup,
        "globalShipping": False,
    }


def list_fulfillment_policies(env: dict, token: str, marketplace_id: str) -> list[dict]:
    url = f"https://{api_host(env)}/sell/account/v1/fulfillment_policy"
    resp = httpx.get(
        url,
        headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": marketplace_id},
        params={"marketplace_id": marketplace_id},
        timeout=30,
    )
    if resp.status_code != 200:
        return []
    return resp.json().get("fulfillmentPolicies", []) or []


def fulfillment_policies_match(existing: dict, wanted: dict) -> bool:
    """Match on the handful of fields that actually change buyer-visible behavior."""
    if existing.get("handlingTime", {}).get("value") != wanted["handlingTime"]["value"]:
        return False
    if bool(existing.get("pickupDropOff")) != bool(wanted["pickupDropOff"]):
        return False
    if bool(existing.get("globalShipping")) != bool(wanted["globalShipping"]):
        return False

    def primary_service(p: dict) -> dict:
        for opt in p.get("shippingOptions", []):
            if opt.get("optionType") == "DOMESTIC":
                return {
                    "costType": opt.get("costType"),
                    "service": (opt.get("shippingServices") or [{}])[0].get("shippingServiceCode"),
                    "free": bool((opt.get("shippingServices") or [{}])[0].get("freeShipping")),
                }
        return {}

    return primary_service(existing) == primary_service(wanted)


def find_or_create_fulfillment_policy(env: dict, token: str, body: dict) -> str:
    """Reuse an existing matching policy if one exists, else create a new one.

    eBay dedupes fulfillment policies by content (refuses to create a duplicate
    with the same handling/pickup/shipping config), so we look for an existing
    match first.
    """
    for existing in list_fulfillment_policies(env, token, body["marketplaceId"]):
        if fulfillment_policies_match(existing, body):
            print(f"  → reusing existing policy {existing['fulfillmentPolicyId']} ({existing['name']!r})")
            return existing["fulfillmentPolicyId"]
    url = f"https://{api_host(env)}/sell/account/v1/fulfillment_policy"
    resp = post_json(url, token, body)
    if resp.status_code not in (200, 201):
        explain_failure(resp, "createFulfillmentPolicy")
    return resp.json()["fulfillmentPolicyId"]


def create_offer(env: dict, token: str, sku: str, draft: dict) -> str:
    price = required(draft, "price", "draft")
    body = {
        "sku": sku,
        "marketplaceId": draft.get("marketplaceId", env.get("EBAY_MARKETPLACE_ID", "EBAY_US")),
        "format": "FIXED_PRICE",
        "availableQuantity": draft.get("quantity", 1),
        "categoryId": required(draft, "categoryId", "draft"),
        "listingDescription": required(draft, "description", "draft"),
        "pricingSummary": {
            "price": {"value": str(price["value"]), "currency": price.get("currency", "USD")}
        },
        "merchantLocationKey": with_env_default(
            draft, "merchantLocationKey", env, "EBAY_MERCHANT_LOCATION_KEY", "draft"
        ),
        "listingPolicies": {
            "fulfillmentPolicyId": with_env_default(
                draft, "fulfillmentPolicyId", env, "EBAY_FULFILLMENT_POLICY_ID", "draft"
            ),
            "paymentPolicyId": with_env_default(
                draft, "paymentPolicyId", env, "EBAY_PAYMENT_POLICY_ID", "draft"
            ),
            "returnPolicyId": with_env_default(
                draft, "returnPolicyId", env, "EBAY_RETURN_POLICY_ID", "draft"
            ),
        },
    }
    url = f"https://{api_host(env)}/sell/inventory/v1/offer"
    resp = post_json(url, token, body)
    if resp.status_code not in (200, 201):
        explain_failure(resp, "createOffer")
    offer_id = resp.json()["offerId"]
    print(f"✓ Offer created (offerId={offer_id})")
    return offer_id


def publish_offer(env: dict, token: str, offer_id: str) -> str:
    url = f"https://{api_host(env)}/sell/inventory/v1/offer/{offer_id}/publish"
    resp = post_json(url, token, {})
    if resp.status_code not in (200, 201):
        explain_failure(resp, "publishOffer")
    listing_id = resp.json()["listingId"]
    print(f"✓ Listing published (listingId={listing_id})")
    return listing_id


def listing_url(env: dict, listing_id: str) -> str:
    host = "sandbox.ebay.com" if env["EBAY_ENV"] == "sandbox" else "ebay.com"
    return f"https://www.{host}/itm/{listing_id}"


def get_category_tree_id(env: dict, token: str, marketplace: str) -> str:
    """Resolve the category tree id for a marketplace (e.g. EBAY_US -> "0")."""
    resp = httpx.get(
        f"https://{api_host(env)}/commerce/taxonomy/v1/get_default_category_tree_id",
        headers={"Authorization": f"Bearer {token}"},
        params={"marketplace_id": marketplace},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["categoryTreeId"]


def fetch_category_aspects(env: dict, token: str, tree_id: str, category_id: str) -> list[dict]:
    """Return the Taxonomy API's item-aspect metadata for a category."""
    resp = httpx.get(
        f"https://{api_host(env)}/commerce/taxonomy/v1/category_tree/{tree_id}"
        f"/get_item_aspects_for_category",
        headers={"Authorization": f"Bearer {token}"},
        params={"category_id": category_id},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("aspects", []) or []


def check_required_aspects(env: dict, token: str, draft: dict) -> None:
    """Pre-flight: confirm the draft supplies every item specific the category requires.

    eBay only enforces required aspects at publishOffer time (errorId 25002) —
    by which point we've already created an inventory item, uploaded images to
    EPS, and minted a fulfillment policy, all of which then has to roll back.
    Fetching the category's aspect metadata up front lets us fail *before* any
    of that work, with an actionable list of what's missing.

    Non-fatal on network/metadata errors: we warn and let publishOffer remain
    the backstop, so a Taxonomy-API hiccup never blocks an otherwise-valid
    publish.
    """
    category_id = draft.get("categoryId")
    if not category_id:
        return  # create_offer surfaces a missing categoryId on its own
    marketplace = draft.get("marketplaceId", env.get("EBAY_MARKETPLACE_ID", "EBAY_US"))
    try:
        tree_id = get_category_tree_id(env, token, marketplace)
        aspects = fetch_category_aspects(env, token, tree_id, str(category_id))
    except (httpx.HTTPError, KeyError, json.JSONDecodeError) as e:
        print(f"  ! Could not pre-check category aspects ({e}); "
              f"relying on publishOffer to validate them.", file=sys.stderr)
        return

    supplied = draft.get("aspects") or {}
    required_names: list[str] = []
    missing: list[str] = []
    bad_value: list[str] = []
    for a in aspects:
        constraint = a.get("aspectConstraint", {})
        if not constraint.get("aspectRequired"):
            continue
        name = a.get("localizedAspectName")
        required_names.append(name)
        values = [v for v in (supplied.get(name) or []) if str(v).strip()]
        allowed = [v.get("localizedValue") for v in (a.get("aspectValues") or [])]
        if not values:
            hint = f" — e.g. {allowed[:8]}" if allowed else ""
            missing.append(f"{name}{hint}")
        elif constraint.get("aspectMode") == "SELECTION_ONLY":
            # A free-text value is fine for FREE_TEXT aspects, but a
            # SELECTION_ONLY aspect rejects anything off its value list.
            allowed_set = {v for v in allowed if v}
            for val in values:
                if val not in allowed_set:
                    bad_value.append(
                        f"{name}={val!r} is not an accepted value "
                        f"(SELECTION_ONLY); choose from {sorted(allowed_set)[:12]}"
                    )

    if missing or bad_value:
        lines = [f"Category {category_id} requires item specifics the draft doesn't satisfy:"]
        lines += [f"  - missing required aspect: {m}" for m in missing]
        lines += [f"  - {b}" for b in bad_value]
        lines.append(
            "Add/fix them under the draft's `aspects` block (each value a "
            "single-element list, e.g. \"Brand\": [\"Philips\"]) and retry."
        )
        sys.exit("\n".join(lines))

    if required_names:
        print(f"✓ Required item specifics present ({', '.join(required_names)})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", required=True, type=Path, help="Path to draft JSON")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the draft + pre-check category aspects (read-only eBay "
             "calls), then print the draft without creating a listing.",
    )
    args = parser.parse_args()

    if not args.draft.exists():
        sys.exit(f"Draft file not found: {args.draft}")
    draft = json.loads(args.draft.read_text())
    validate_draft(draft)
    sku = draft.get("sku") or make_sku(draft["title"])

    env = load_env()
    print(f"Environment: {env['EBAY_ENV']}")
    token = get_access_token(env)

    # Pre-flight: confirm the category's required item specifics are all in the
    # draft *before* we create an inventory item / upload to EPS / mint a
    # policy. Without this, a missing aspect only surfaces at publishOffer
    # (errorId 25002), forcing a full rollback and retry.
    check_required_aspects(env, token, draft)

    if args.dry_run:
        print("\n--- DRY RUN: draft validated, no listing created ---")
        print(f"SKU: {sku}")
        print("Draft:")
        print(json.dumps(draft, indent=2))
        return

    # Mint a per-listing fulfillment policy if the draft has shipping overrides
    # (handlingDays, localPickup, etc.) or any of the corresponding env defaults
    # are set. Otherwise fall back to EBAY_FULFILLMENT_POLICY_ID.
    if has_shipping_overrides(draft, env) and "fulfillmentPolicyId" not in draft:
        body = build_dynamic_fulfillment_policy(env, draft, sku)
        print(f"Creating per-listing fulfillment policy "
              f"(handling={body['handlingTime']['value']}d, "
              f"pickupDropOff={body['pickupDropOff']}, "
              f"international={len(body['shippingOptions']) > 1})...")
        draft["fulfillmentPolicyId"] = find_or_create_fulfillment_policy(env, token, body)
        print(f"  ✓ fulfillmentPolicyId={draft['fulfillmentPolicyId']}")

    # If the draft has localImages but no usable imageUrls, upload them to EPS
    # so eBay can fetch them. (FB CDN URLs would 403 from eBay's side.)
    local_images = draft.get("localImages") or []
    image_urls = draft.get("imageUrls") or []
    needs_upload = local_images and not any(
        u.startswith("http") and "ebayimg.com" in u for u in image_urls
    )
    if needs_upload:
        from ebay_eps import upload_images
        paths = [Path(p).expanduser() for p in local_images]
        print(f"Uploading {len(paths)} image(s) to eBay Picture Services...")
        eps_urls = upload_images(env, token, paths)
        for url in eps_urls:
            print(f"  ✓ {url}")
        draft["imageUrls"] = eps_urls

    # Track what we created so we can roll back on failure (avoids piling up
    # orphan inventory items + offers across retries).
    created_sku: str | None = None
    created_offer_id: str | None = None
    try:
        create_inventory_item(env, token, sku, draft)
        created_sku = sku
        created_offer_id = create_offer(env, token, sku, draft)
        listing_id = publish_offer(env, token, created_offer_id)
        print(f"\n→ {listing_url(env, listing_id)}")
    except PublishError as e:
        print(f"\nRolling back partial state from {e}...", file=sys.stderr)
        cleanup_orphans(env, token, created_sku, created_offer_id)
        sys.exit(1)


def cleanup_orphans(env: dict, token: str, sku: str | None, offer_id: str | None) -> None:
    """Best-effort: delete an unpublished offer and the inventory item we just created."""
    if offer_id:
        try:
            httpx.delete(
                f"https://{api_host(env)}/sell/inventory/v1/offer/{offer_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            print(f"  ✓ deleted offer {offer_id}", file=sys.stderr)
        except Exception as e:
            print(f"  ! could not delete offer {offer_id}: {e}", file=sys.stderr)
    if sku:
        try:
            httpx.delete(
                f"https://{api_host(env)}/sell/inventory/v1/inventory_item/{sku}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            print(f"  ✓ deleted inventory item {sku}", file=sys.stderr)
        except Exception as e:
            print(f"  ! could not delete inventory item {sku}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
