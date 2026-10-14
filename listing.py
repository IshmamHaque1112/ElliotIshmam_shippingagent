"""Filtering, sorting and paging of shipment rows (shared by the pages and the JSON API)."""

from typing import Any

SORTS = {  # key -> (field, descending by default)
    "exposure": ("exposure_usd", True), "priority": ("priority_score", True),
    "promise": ("promise_utc", False), "id": ("shipment_id", False),
}


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(value)))
    except (TypeError, ValueError):
        return default


def filter_rows(rows: list[dict[str, Any]], args: Any) -> list[dict[str, Any]]:
    out = rows
    for param, field in (("state", "state"), ("carrier", "carrier_id"), ("tier", "tier"),
                               ("severity", "severity"), ("badge", "urgency_badge")):
        value = (args.get(param) or "").strip()
        if value:
            out = [r for r in out if str(r[field] or "").upper() == value.upper()]
    q = (args.get("q") or "").strip().lower()
    if q:
        out = [r for r in out if q in f"{r['shipment_id']} {r['customer']} {r['order_id']}".lower()]
    field, desc = SORTS.get(args.get("sort") or "", SORTS["id"])
    if args.get("dir") in ("asc", "desc"):
        desc = args["dir"] == "desc"
    present = sorted((r for r in out if r[field] is not None), key=lambda r: (r[field], r["shipment_id"]), reverse=desc)
    return present + [r for r in out if r[field] is None]  # rows without a value always sort last


def paginate(rows: list[dict[str, Any]], args: Any, default_size: int = 25) -> dict[str, Any]:
    size = _int(args.get("page_size"), default_size, 1, 100)
    pages = max(1, -(-len(rows) // size))
    page = _int(args.get("page"), 1, 1, pages)
    return {"items": rows[(page - 1) * size: page * size], "page": page, "page_size": size, "pages": pages, "total": len(rows)}
