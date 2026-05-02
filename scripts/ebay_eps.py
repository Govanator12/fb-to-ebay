#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""Upload local image files to eBay Picture Services (EPS).

Wraps the Trading API's UploadSiteHostedPictures call (XML, one image per
request) using our existing OAuth token. Returns eBay-hosted URLs that the
Inventory API can use directly — no third-party image hosting required.

Usage as a CLI:
  uv run ebay_eps.py path/to/img1.jpg path/to/img2.jpg ...
  # → prints one eBay URL per line

Usage as a library (called by ebay_publish.py):
  from ebay_eps import upload_images
  urls = upload_images(env, token, [Path("img1.jpg"), Path("img2.jpg")])

Notes:
- EPS pictures expire after 30 days if not associated with a published listing
  — fine for our flow since we publish immediately after uploading.
- Trading API uses a separate gateway URL (api.ebay.com/ws/api.dll, or the
  sandbox equivalent), not the REST host.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from ebay_auth import get_access_token, load_env  # noqa: E402

TRADING_HOST = {
    "sandbox": "https://api.sandbox.ebay.com/ws/api.dll",
    "production": "https://api.ebay.com/ws/api.dll",
}

UPLOAD_REQUEST_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<UploadSiteHostedPicturesRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <PictureName>{name}</PictureName>
  <PictureSet>Standard</PictureSet>
</UploadSiteHostedPicturesRequest>
""".strip()

PIC_URL_RE = re.compile(r"<FullURL>([^<]+)</FullURL>")
ERROR_RE = re.compile(r"<LongMessage>([^<]+)</LongMessage>")


def _trading_headers(env: dict, token: str) -> dict[str, str]:
    return {
        "X-EBAY-API-IAF-TOKEN": token,
        "X-EBAY-API-COMPATIBILITY-LEVEL": "1193",
        "X-EBAY-API-CALL-NAME": "UploadSiteHostedPictures",
        "X-EBAY-API-SITEID": "0",  # 0 = US
    }


def _multipart_body(xml_request: str, image_bytes: bytes, content_type: str) -> tuple[bytes, str]:
    """Build the multipart/form-data body the Trading API requires.

    The XML payload goes first (Content-Disposition: form-data; name="XML Payload"),
    then the binary image (name="dummy"). The order matters — XML must be first.
    """
    boundary = "----fb-to-ebay-boundary"
    parts = [
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="XML Payload"\r\n'
        f'Content-Type: text/xml;charset=utf-8\r\n\r\n'
        f'{xml_request}\r\n'.encode(),
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="dummy"; filename="image"\r\n'
        f'Content-Transfer-Encoding: binary\r\n'
        f'Content-Type: {content_type}\r\n\r\n'.encode(),
        image_bytes,
        f'\r\n--{boundary}--\r\n'.encode(),
    ]
    return b''.join(parts), f'multipart/form-data; boundary={boundary}'


def _content_type_for(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(ext, "image/jpeg")


def upload_image(env: dict, token: str, path: Path) -> str:
    """Upload a single image to EPS, return the eBay-hosted FullURL."""
    if not path.exists():
        sys.exit(f"Image not found: {path}")
    image_bytes = path.read_bytes()
    xml = UPLOAD_REQUEST_TEMPLATE.format(name=path.stem[:30])
    body, content_type = _multipart_body(xml, image_bytes, _content_type_for(path))

    url = TRADING_HOST[env["EBAY_ENV"]]
    headers = _trading_headers(env, token)
    headers["Content-Type"] = content_type
    resp = httpx.post(url, headers=headers, content=body, timeout=120)
    if resp.status_code != 200:
        sys.exit(f"EPS upload HTTP {resp.status_code}: {resp.text[:500]}")

    text = resp.text
    pic_url_match = PIC_URL_RE.search(text)
    if pic_url_match:
        return pic_url_match.group(1)

    err_match = ERROR_RE.search(text)
    if err_match:
        sys.exit(f"EPS upload returned error: {err_match.group(1)}")
    sys.exit(f"EPS upload returned unexpected response: {text[:500]}")


def upload_images(env: dict, token: str, paths: list[Path]) -> list[str]:
    """Upload multiple images sequentially, return eBay URLs in same order."""
    return [upload_image(env, token, p) for p in paths]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="Image files to upload")
    args = parser.parse_args()

    env = load_env()
    token = get_access_token(env)
    print(f"Uploading {len(args.paths)} image(s) to EPS ({env['EBAY_ENV']})...", file=sys.stderr)
    for path in args.paths:
        url = upload_image(env, token, path)
        print(url)


if __name__ == "__main__":
    main()
