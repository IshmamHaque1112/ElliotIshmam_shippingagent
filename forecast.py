"""If-nothing-changes forecast. Re-runs the rules at later times on a copy of the already fetched data.

It never moves the simulated clock, never calls the service and never touches the snapshot cache: `rules.evaluate`
takes `now` as a parameter, so a later time is just a different argument.
"""

import copy
from datetime import datetime, timedelta
from typing import Any

import config
import rules

ACTIVE = ("delayed", "at_risk", "unable_to_verify")


def _evaluate_at(snap, now: datetime, hours: float) -> list[dict[str, Any]]:
    later = now + timedelta(hours=hours)
    facilities, dtz = snap.dataset["facilities"], snap.dataset["meta"]["dispatcher_timezone"]
    out = []
    for a in snap.assessments:
        data = copy.deepcopy(a.get("_data") or {})
        tracking, order, timeline = data.get("get_tracking_status"), data.get("order_context"), data.get("event_timeline")
        unavailable = [n for n, v in (("get_tracking_status", tracking), ("event_timeline", timeline), ("order_context", order)) if v is None]
        out.append(rules.evaluate(a["shipment_id"], tracking, order, timeline, facilities, later, dtz, unavailable))
    return out


def project(snap, horizons: tuple[int, ...] | None = None) -> dict[str, Any]:
    """Active exposure now and at each horizon, plus how many exceptions are new at that point."""
    horizons = tuple(horizons or config.FORECAST_HOURS)
    now = rules.parse_ts(snap.sim_now_utc)
    current_ids = {a["shipment_id"] for a in snap.assessments if a["state"] in ACTIVE}
    points = []
    for h in (0, *horizons):
        rows = _evaluate_at(snap, now, h)
        active = [r for r in rows if r["state"] in ACTIVE]
        points.append({"hours": h, "active_exposure_usd": round(sum(r["total_exposure_usd"] or 0 for r in active), 2),
                       "active_exceptions": len(active), "new_exceptions": sorted(r["shipment_id"] for r in active if r["shipment_id"] not in current_ids)})
    base = points[0]["active_exposure_usd"]
    for p in points:
        p["growth_usd"] = round(p["active_exposure_usd"] - base, 2)
    return {"base_usd": base, "points": points, "sim_now_utc": snap.sim_now_utc}
