"""Smoke test for a running server: sign in, switch datasets, read the pages, sign out. Makes no Claude call.

Usage:  DEMO_PASSWORD=... python scripts/smoke_test.py https://your-host --username NAME
The password is read from the environment so it never appears in a command line or shell history.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar


def _token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not match:
        raise RuntimeError("no CSRF token on the page")
    return match.group(1)


def run(base: str, username: str, password: str) -> list[tuple[str, bool, str]]:
    base = base.rstrip("/")
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    results: list[tuple[str, bool, str]] = []

    def step(name: str, fn):
        try:
            ok, detail = fn()
        except (urllib.error.URLError, RuntimeError, ValueError, KeyError) as error:
            ok, detail = False, f"{type(error).__name__}: {error}"
        results.append((name, bool(ok), str(detail)))
        return ok

    def fetch(path: str, data: dict | None = None) -> tuple[int, str, str]:
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        try:
            with opener.open(urllib.request.Request(base + path, data=body), timeout=30) as r:
                return r.status, r.geturl(), r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return error.code, error.geturl(), error.read().decode("utf-8", "replace")

    step("health check answers ok", lambda: ((lambda r: (r[0] == 200 and r[2].strip() == "ok", r[0]))(fetch("/healthz"))))
    step("a visitor without a session is sent to the sign-in page", lambda: ((lambda r: ("/login" in r[1], r[1]))(fetch("/"))))

    def sign_in():
        status, _, html = fetch("/login")
        status, url, page = fetch("/login", {"csrf_token": _token(html), "username": username, "password": password, "next": "/"})
        return status == 200 and "/login" not in url and "Dashboard" in page, f"{status} {url}"
    if not step("sign in", sign_in):
        return results
    csrf = _token(fetch("/")[2])

    def datasets():
        seen = {}
        for name in ("extended", "edge", "dev"):
            fetch("/dataset", {"csrf_token": csrf, "dataset": name})
            status, _, body = fetch("/api/summary")
            seen[name] = json.loads(body)["dataset"]
        return all(k == v for k, v in seen.items()), seen
    step("the dataset toggle switches the analysis", datasets)
    step("early warning page and API answer", lambda: ((lambda a, b: (a[0] == 200 and b[0] == 200 and json.loads(b[2])["schema"] == "v1", (a[0], b[0])))(fetch("/early-warning"), fetch("/api/early-warning"))))
    step("the daily Claude budget is readable", lambda: ((lambda r: (isinstance(json.loads(r[2])["analyses_remaining"], int), json.loads(r[2])["analyses_remaining"]))(fetch("/api/summary"))))
    step("a page without a token is refused (CSRF)", lambda: ((lambda r: (r[0] == 403, r[0]))(fetch("/simulate/reset", {}))))

    def sign_out():
        fetch("/logout", {"csrf_token": csrf})
        return "/login" in fetch("/")[1], "back at the sign-in page"
    step("sign out ends the session", sign_out)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--username", default=os.environ.get("DEMO_USERNAME"))
    args = parser.parse_args(argv)
    password = os.environ.get("DEMO_PASSWORD")
    if not args.username or not password:
        print("Set DEMO_PASSWORD in the environment and pass --username (or set DEMO_USERNAME).")
        return 2
    results = run(args.url, args.username, password)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({detail})")
    return 0 if results and all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
