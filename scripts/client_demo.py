"""Example of a script calling the app's JSON API with basic auth (standard library only).

    DEMO_USERNAME=demo DEMO_PASSWORD=... python scripts/client_demo.py http://127.0.0.1:5000
"""

import base64
import json
import os
import sys
import urllib.request


def get(base: str, path: str, username: str, password: str) -> dict:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(base.rstrip("/") + path, headers={"Authorization": f"Basic {token}"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def main(argv: list[str] | None = None) -> int:
    base = (argv or sys.argv[1:] or ["http://127.0.0.1:5000"])[0]
    user, password = os.environ.get("DEMO_USERNAME", ""), os.environ.get("DEMO_PASSWORD", "")
    if not user or not password:
        print("Set DEMO_USERNAME and DEMO_PASSWORD first.", file=sys.stderr)
        return 2
    summary = get(base, "/api/summary", user, password)
    var = summary["value_at_risk"]
    print(f"Active exceptions: {var['active_exceptions']} | Revenue exposed: ${var['revenue_exposed_usd']:,.2f} "
          f"| Accrued penalties: ${var['accrued_penalties_usd']:,.2f}")
    for row in get(base, "/api/queue", user, password)["queue"][:10]:
        print(f"{row['rank']:>2}. {row['shipment_id']}  {row['state']:<16} ${row['exposure_usd'] or 0:>10,.2f}  {row['recommended_action_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
