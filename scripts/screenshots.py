"""Optional smoke test: load every page with Playwright and save screenshots to ./screenshots.

    pip install playwright && playwright install chromium     (not in requirements.txt)
    DEMO_USERNAME=demo DEMO_PASSWORD=... python scripts/screenshots.py http://127.0.0.1:5000
"""

import os
import sys

PAGES = ["/", "/shipments", "/queue", "/cases", "/runs", "/carriers", "/digest", "/eval", "/simulate", "/shipments/SH-1004"]


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed; skipping.", file=sys.stderr)
        return 0
    base = (sys.argv[1:] or ["http://127.0.0.1:5000"])[0].rstrip("/")
    os.makedirs("screenshots", exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(http_credentials={"username": os.environ["DEMO_USERNAME"], "password": os.environ["DEMO_PASSWORD"]})
        page = context.new_page()
        for path in PAGES:
            response = page.goto(base + path)
            assert response and response.status == 200, (path, response and response.status)
            page.screenshot(path=f"screenshots/{path.strip('/').replace('/', '_') or 'dashboard'}.png", full_page=True)
        browser.close()
    print(f"Saved {len(PAGES)} screenshots to ./screenshots")
    return 0


if __name__ == "__main__":
    sys.exit(main())
