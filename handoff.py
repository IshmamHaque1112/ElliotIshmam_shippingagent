"""Shift handoff: what changed in the saved cases since a marker. Pure functions over store rows, no I/O.

A window is {"case_id": N, "disposition_id": M, "ts": text or None}. A case is inside it when its id is above N and
(if ts is set) it was created at or after ts. The same goes for dispositions. Review markers use ids, so two actions
in the same second can never be confused.
"""

from typing import Any

UNASSIGNED = "Unassigned"
OPEN_STATUSES = ("new", "in_progress")


def window(marker: dict[str, Any] | None = None, since_ts: str | None = None) -> dict[str, Any]:
    if since_ts:
        return {"case_id": 0, "disposition_id": 0, "ts": since_ts}
    marker = marker or {}
    return {"case_id": int(marker.get("case_id", 0)), "disposition_id": int(marker.get("disposition_id", 0)), "ts": None}


def _in_case_window(case: dict[str, Any], w: dict[str, Any]) -> bool:
    return case["id"] > w["case_id"] and (w["ts"] is None or case["created_at"] >= w["ts"])


def _in_disp_window(disp: dict[str, Any], w: dict[str, Any]) -> bool:
    return disp["id"] > w["disposition_id"] and (w["ts"] is None or disp["created_at"] >= w["ts"])


def _order(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda c: (-(c["exposure_usd"] or 0), c["shipment_id"], -c["id"]))


def build(cases: list[dict[str, Any]], dispositions: list[dict[str, Any]], w: dict[str, Any]) -> dict[str, Any]:
    """`cases`: every saved case of the dataset, each with its current `disposition` dict (status, owner).
    `dispositions`: every disposition row of those cases (id, case_id, status, created_at).
    """
    latest: dict[str, dict[str, Any]] = {}
    for c in cases:  # newest case per shipment
        if c["shipment_id"] not in latest or c["id"] > latest[c["shipment_id"]]["id"]:
            latest[c["shipment_id"]] = c
    in_window = [c for c in cases if _in_case_window(c, w)]
    first_seen = {sid: min(c["id"] for c in cases if c["shipment_id"] == sid) for sid in latest}

    new, worsened, seen = [], [], set()
    for c in sorted(in_window, key=lambda c: c["id"]):
        sid = c["shipment_id"]
        if sid in seen:
            continue
        if c["run_status"] == "NEW" and c["id"] == first_seen[sid]:
            new.append(latest[sid]); seen.add(sid)
    for c in sorted(in_window, key=lambda c: c["id"]):
        if c["shipment_id"] not in seen and c["run_status"] == "WORSENED":
            worsened.append(latest[c["shipment_id"]]); seen.add(c["shipment_id"])

    by_id = {c["id"]: c for c in cases}
    resolved, done = [], set()
    for d in sorted(dispositions, key=lambda d: d["id"], reverse=True):
        c = by_id.get(d["case_id"])
        if c is None or c["id"] in done or d["status"] != "resolved" or not _in_disp_window(d, w):
            continue
        if c["disposition"]["status"] == "resolved":  # still resolved now
            resolved.append(c); done.add(c["id"])

    owners: dict[str, list[dict[str, Any]]] = {}
    for c in latest.values():
        if c["disposition"]["status"] in OPEN_STATUSES:
            owners.setdefault(c["disposition"].get("owner") or UNASSIGNED, []).append(c)
    open_by_owner = [{"owner": o, "cases": _order(rows), "exposure_usd": sum(r["exposure_usd"] or 0 for r in rows)}
                     for o, rows in sorted(owners.items(), key=lambda kv: (kv[0] == UNASSIGNED, kv[0].lower()))]
    return {"new": _order(new), "worsened": _order(worsened), "resolved": _order(resolved), "open_by_owner": open_by_owner,
            "empty": not (new or worsened or resolved or open_by_owner)}
