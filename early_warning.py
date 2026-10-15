"""Early warning: how long before the carrier's own data admits a problem do the rules flag the shipment?

Pure code, no clock and no model. For each shipment it walks through the moments when anything could change the
rules' answer (each scan, each ETA observation, a dwell limit passing, the margin before the promise running out, the
promise itself), shows the rules only what had happened by then (replay.visible), and records the first moment the
state was at_risk or delayed. The carrier "admits" a problem at the earliest of:
  * a scan that says so (EXCEPTION, HELD, CUSTOMS_HOLD, DELIVERY_ATTEMPTED),
  * the carrier's own ETA moving past the promise,
  * the promise time passing without a delivery on or before it.
Free-text carrier notes are never used: they are untrusted and are not evidence for a number.
The result is a measurement on the dataset it is run on (a static fictional one here), not a promise about real carriers.
"""

import statistics
from datetime import datetime, timedelta
from typing import Any

import config
import replay
import rules

FLAGGED = ("at_risk", "delayed")
CATEGORIES = ("earlier", "same_time", "later", "open", "missed", "recovered", "false_alarm", "cannot_assess")

LABELS = {
    "earlier": "Flagged before the carrier admitted a problem",
    "same_time": "Flagged at the same moment",
    "later": "Flagged after the carrier admitted it",
    "open": "Flagged; the carrier has not admitted a problem yet",
    "missed": "Late, but the rules never flagged it before the end",
    "recovered": "Flagged earlier, back on track now (not delivered yet)",
    "false_alarm": "Flagged, then delivered on time",
    "cannot_assess": "Cannot assess (data missing or in conflict)",
}


def _times(shipment: dict[str, Any], dataset: dict[str, Any], now: datetime) -> list[tuple[datetime, datetime]]:
    """(moment, when to ask the rules) for every point at or before `now` where the answer for this shipment could change.

    Boundaries such as "dwell passes the baseline" are reported at the exact moment but asked a minute later, because
    the rules use strict comparisons."""
    facilities = dataset["facilities"]
    order = shipment["order"]
    minute = timedelta(minutes=1)
    found: dict[datetime, datetime] = {}
    exact = lambda t: found.setdefault(t, t)                  # something new became visible: ask at that moment
    boundary = lambda t: found.setdefault(t, t + minute)      # a limit was crossed: ask just after it
    for when, event in rules.normalized_events(shipment["timeline"]["events"], facilities):
        exact(when)
        baseline = facilities[event["facility_id"]]["dwell_baseline_hours"] if event["code"] in config.FACILITY_CODES else config.LINEHAUL_MAX_SCAN_GAP_H
        boundary(when + timedelta(hours=baseline))            # dwell passes the baseline
    for h in shipment["timeline"].get("eta_history") or []:
        exact(rules.parse_ts(h["observed_at"]))
        boundary(rules.parse_ts(h["eta"]))                    # now passes the ETA, so the projection moves
    try:
        promise = rules.resolve_promise(order, facilities[shipment["tracking"]["destination_facility_id"]]["tz"])
    except (KeyError, TypeError, ValueError):
        promise = None
    if promise is not None:
        boundary(promise - timedelta(hours=config.MARGIN_AT_RISK_H))
        boundary(promise)
    exact(now)
    return sorted((t, ask) for t, ask in found.items() if t <= now)


def _state_at(shipment: dict[str, Any], dataset: dict[str, Any], when: datetime) -> str | None:
    """The rules' state if they had been run at `when`, seeing only what had happened by then."""
    one = replay.visible({**dataset, "shipments": [shipment]}, when)
    if not one["shipments"]:
        return None
    s = one["shipments"][0]
    verdict = rules.evaluate(s["shipment_id"], s["tracking"], s["order"], s["timeline"], dataset["facilities"], when, dataset["meta"]["dispatcher_timezone"])
    return verdict["state"]


def _admission(shipment: dict[str, Any], dataset: dict[str, Any], now: datetime) -> tuple[datetime, str] | None:
    """When the carrier's own data first admits a problem (and which signal), as of `now`. None if it has not."""
    facilities, order = dataset["facilities"], shipment["order"]
    options: list[tuple[datetime, str]] = []
    for when, event in rules.normalized_events(shipment["timeline"]["events"], facilities):
        if event["code"] in config.ADMISSION_CODES and when <= now:
            options.append((when, f"a {event['code']} scan"))
            break
    try:
        promise = rules.resolve_promise(order, facilities[shipment["tracking"]["destination_facility_id"]]["tz"])
    except (KeyError, TypeError, ValueError):
        return min(options) if options else None
    for h in sorted(shipment["timeline"].get("eta_history") or [], key=lambda x: rules.parse_ts(x["observed_at"])):
        if rules.parse_ts(h["observed_at"]) <= now and rules.parse_ts(h["eta"]) > promise:
            options.append((rules.parse_ts(h["observed_at"]), "its own ETA moved past the promise"))
            break
    delivered = shipment["tracking"].get("delivered_at_utc")
    delivered_at = rules.parse_ts(delivered) if delivered else None
    if promise <= now and (delivered_at is None or delivered_at > promise):
        options.append((promise, "the promise time passed without a delivery"))
    return min(options) if options else None


def evaluate(shipment: dict[str, Any], dataset: dict[str, Any], now: datetime, current_state: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {"shipment_id": shipment["shipment_id"], "state": current_state, "flagged_at": None, "admitted_at": None,
                           "admission_signal": None, "lead_hours": None, "category": None, "note": None}
    if current_state == "unable_to_verify":
        row.update(category="cannot_assess", note="the rules cannot give a state for this shipment, so there is nothing to time")
        return row
    flagged_at = None
    for moment, ask in _times(shipment, dataset, now):
        if _state_at(shipment, dataset, min(ask, now)) in FLAGGED:
            flagged_at = moment
            break
    admission = _admission(shipment, dataset, now)
    first_seen = replay.appears_at(shipment, dataset["facilities"])
    if admission is not None and first_seen is not None and admission[0] < first_seen:
        admission = (first_seen, admission[1])    # the carrier said so before there was anything for the rules to look at: count from the first look
    delivered = shipment["tracking"].get("delivered_at_utc")
    late = current_state in ("delayed", "delivered_late")
    if flagged_at is not None:
        row["flagged_at"] = rules.iso(flagged_at)
    if admission is not None:
        row["admitted_at"], row["admission_signal"] = rules.iso(admission[0]), admission[1]
    if flagged_at is None:
        if late and admission is not None:
            row["category"] = "missed"
        return row
    if current_state == "on_track":
        row["category"] = "false_alarm" if delivered else "recovered"
        return row
    if admission is None:
        row["category"] = "open"
        return row
    lead = rules.hours_between(admission[0], flagged_at)
    row["lead_hours"] = round(lead, 2)
    row["category"] = "earlier" if lead > 0 else "same_time" if lead == 0 else "later"
    return row


def compute(dataset: dict[str, Any], now: datetime, states: dict[str, str]) -> dict[str, Any]:
    """Rows for every shipment that has a category, plus the headline numbers. `states` maps shipment id to its current state."""
    rows = []
    for shipment in dataset["shipments"]:
        try:
            row = evaluate(shipment, dataset, now, states.get(shipment["shipment_id"]))
        except (KeyError, TypeError, ValueError):   # data too broken to walk through (the rules call these unable_to_verify)
            row = {"shipment_id": shipment.get("shipment_id"), "state": states.get(shipment.get("shipment_id")), "flagged_at": None, "admitted_at": None,
                   "admission_signal": None, "lead_hours": None, "category": "cannot_assess", "note": "the data could not be read"}
        if row["category"]:
            rows.append(row)
    rows.sort(key=lambda r: (CATEGORIES.index(r["category"]), -(r["lead_hours"] or 0), r["shipment_id"]))
    counts = {c: sum(1 for r in rows if r["category"] == c) for c in CATEGORIES}
    leads = [r["lead_hours"] for r in rows if r["category"] == "earlier"]
    measured = counts["earlier"] + counts["same_time"] + counts["later"]
    summary = {
        "measured": measured, "counts": counts, "median_lead_hours": round(statistics.median(leads), 1) if leads else None,
        "shortest_lead_hours": min(leads) if leads else None, "longest_lead_hours": max(leads) if leads else None,
        "headline": None,
    }
    if measured:
        summary["headline"] = (f"Flagged before the carrier admitted a problem on {counts['earlier']} of {measured} late shipments "
                               f"({counts['same_time']} at the same moment, {counts['later']} later)"
                               + (f"; median lead {summary['median_lead_hours']:.1f} h." if leads else "."))
    return {"rows": rows, "summary": summary, "labels": LABELS, "as_of_utc": rules.iso(now), "scope": "measured on this dataset only"}


_cache: dict[tuple, dict[str, Any]] = {}


def for_snapshot(snap: Any) -> dict[str, Any]:
    """The metric for a pipeline snapshot, cached with it (same dataset, same simulated time, same rules output)."""
    key = (snap.dataset_name, snap.sim_now_utc, id(snap))
    if key not in _cache:
        while len(_cache) >= 8:
            _cache.pop(next(iter(_cache)))
        _cache[key] = compute(snap.dataset, rules.parse_ts(snap.sim_now_utc), {a["shipment_id"]: a["state"] for a in snap.assessments})
    return _cache[key]


def row_for(result: dict[str, Any], shipment_id: str) -> dict[str, Any] | None:
    return next((r for r in result["rows"] if r["shipment_id"] == shipment_id), None)
