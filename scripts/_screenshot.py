"""
Usage: _screenshot.py <url1> <out1.png> [<url2> <out2.png> ...]

Writes 1280x800 PNGs of each URL after a 1.5s settle.
Reads HERMES_ADMIN_COOKIE env var (Cookie header value) if set, for authed pages.
Exits non-zero if any URL returns non-200 or PNG is < 10KB.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

MIN_PNG_BYTES = 10_000
VIEWPORT = {"width": 1280, "height": 800}
SETTLE_MS = 1_500


def main(argv: list[str]) -> int:
    if len(argv) < 2 or len(argv) % 2 != 0:
        print(__doc__, file=sys.stderr)
        return 2

    pairs: list[tuple[str, Path]] = []
    for i in range(0, len(argv), 2):
        pairs.append((argv[i], Path(argv[i + 1])))

    cookie_header = os.environ.get("HERMES_ADMIN_COOKIE", "").strip()

    from playwright.sync_api import sync_playwright

    failures: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            context = browser.new_context(viewport=VIEWPORT)
            if cookie_header:
                # Cookie header value like "name=value" or "n1=v1; n2=v2"
                cookies = []
                for part in cookie_header.split(";"):
                    part = part.strip()
                    if not part or "=" not in part:
                        continue
                    name, _, value = part.partition("=")
                    for url, _out in pairs:
                        # Derive domain/path from URL host.
                        from urllib.parse import urlparse

                        u = urlparse(url)
                        cookies.append(
                            {
                                "name": name.strip(),
                                "value": value.strip(),
                                "domain": u.hostname or "127.0.0.1",
                                "path": "/",
                            }
                        )
                        break
                if cookies:
                    context.add_cookies(cookies)

            for url, out_path in pairs:
                page = context.new_page()
                resp = page.goto(url, wait_until="networkidle")
                if resp is None:
                    failures.append(f"{url}: no response")
                    page.close()
                    continue
                if resp.status != 200:
                    failures.append(f"{url}: HTTP {resp.status}")
                    page.close()
                    continue
                page.wait_for_timeout(SETTLE_MS)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(out_path), full_page=False)
                page.close()

                size = out_path.stat().st_size if out_path.exists() else 0
                if size < MIN_PNG_BYTES:
                    failures.append(f"{out_path}: {size} bytes (< {MIN_PNG_BYTES} floor)")
        finally:
            browser.close()

    if failures:
        for f in failures:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
