"""Deterministic rules (PRD section 6). Pure code: no network, no LLM, no real clock."""

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import config
import tools


def parse_ts(value: str, tzname: str | None = None) -> datetime:
    """Offset timestamps convert to UTC; naive ones are read in `tzname` (facility-local)."""
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tzname or "UTC"))
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hours_between(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds() / 3600.0


def local_string(dt: datetime, tzname: str) -> str:
    local = dt.astimezone(ZoneInfo(tzname))
    return f"{local:%b} {local.day}, {local:%H:%M} {local.tzname()}"


def penalty(order: dict[str, Any], hours_late: float) -> float:
    """Penalty for a given lateness: grace, optional escalated rate, then the cap."""
    if hours_late <= 0:
        return 0.0
    grace, rate = order["penalty_grace_hours"], order["sla_penalty_per_hour_usd"]
    esc = order.get("penalty_escalation")
    if esc:
        after = esc["after_hours_late"]
        amount = rate * max(0.0, min(hours_late, after) - grace) + rate * esc["multiplier"] * max(0.0, hours_late - after)
    else:
        amount = rate * max(0.0, hours_late - grace)
    return min(amount, order["penalty_cap_usd"])


def resolve_promise(order: dict[str, Any], dest_tz: str) -> datetime:
    if order.get("promise_datetime_utc"):
        return parse_ts(order["promise_datetime_utc"])
    day = datetime.fromisoformat(order["promise_date"]).date()
    local = datetime(day.year, day.month, day.day, config.PROMISE_LOCAL_HOUR, 0, tzinfo=ZoneInfo(dest_tz))
    return local.astimezone(timezone.utc)


def severity_for(exposure: float) -> str:
    if exposure >= config.SEVERITY_HIGH_USD:
        return "HIGH"
    return "MEDIUM" if exposure >= config.SEVERITY_MEDIUM_USD else "LOW"


def normalized_events(events: list[dict[str, Any]], facilities: dict[str, Any]) -> list[tuple[datetime, dict[str, Any]]]:
    """UTC-normalize, collapse duplicates (same instant, facility and code), sort oldest first."""
    seen: dict[tuple, tuple[datetime, dict[str, Any]]] = {}
    for event in events:
        ts = parse_ts(event["timestamp"], facilities[event["facility_id"]]["tz"])
        seen[(ts, event["facility_id"], event["code"])] = (ts, event)
    return sorted(seen.values(), key=lambda pair: pair[0])


def evaluate(
    shipment_id: str,
    tracking: dict[str, Any] | None,
    order: dict[str, Any] | None,
    timeline: dict[str, Any] | None,
    facilities: dict[str, Any],
    now: datetime,
    dispatcher_tz: str,
    unavailable: list[str] | None = None,
) -> dict[str, Any]:
    """Return state, every metric and the recommendation. `unavailable` lists failed tool names."""
    unavailable = list(unavailable or [])
    out: dict[str, Any] = {
        "shipment_id": shipment_id, "state": None, "reasons": [], "eta_forward_revisions": None,
        "eta_drift_hours": None, "dwell_hours": None, "dwell_baseline_hours": None, "hours_late_now": None,
        "projected_hours_late": None, "accrued_penalty_usd": None, "projected_penalty_usd": None,
        "total_exposure_usd": None, "priority_score": None, "severity": None, "urgency_badge": None,
        "next_milestone": None, "recommended_action_id": None, "mitigation_ranking": [],
        "promise_utc": None, "promise_local": None, "promise_dispatcher": None,
        "last_scan_local": None, "last_scan_dispatcher": None, "latest_checkpoint": None,
    }

    problems = [f"{name} unavailable" for name in unavailable]
    if not problems:
        problems = tools.validate_bundle(tracking, order, timeline, facilities)
    if problems:
        _try_promise(out, order, tracking, facilities, dispatcher_tz)
        return _unable_to_verify(out, order, problems)

    multiplier = config.VIP_MULTIPLIER if order["customer_tier"] == config.VIP_TIER else 1.0
    dest_tz = facilities[tracking["destination_facility_id"]]["tz"]
    promise = resolve_promise(order, dest_tz)
    out["promise_utc"] = iso(promise)
    out["promise_local"] = local_string(promise, dest_tz)
    out["promise_dispatcher"] = local_string(promise, dispatcher_tz)

    events = normalized_events(timeline["events"], facilities)
    last_ts, last = events[-1]  # the timeline's latest event is authoritative, whatever status_raw says
    out["last_scan_local"] = local_string(last_ts, facilities[last["facility_id"]]["tz"])
    out["last_scan_dispatcher"] = local_string(last_ts, dispatcher_tz)
    out["latest_checkpoint"] = (
        f"{facilities[last['facility_id']]['name']} {last['code']}, "
        f"{out['last_scan_local']} ({out['last_scan_dispatcher']})"
    )

    history = sorted((parse_ts(h["observed_at"]), parse_ts(h["eta"])) for h in timeline.get("eta_history") or [])
    minimum = timedelta(seconds=config.ETA_REVISION_MIN_SECONDS)
    forward = sum(1 for i in range(1, len(history)) if history[i][1] - history[i - 1][1] > minimum)
    out["eta_forward_revisions"] = forward
    out["eta_drift_hours"] = hours_between(history[-1][1], history[0][1]) if history else 0.0

    if tracking.get("delivered_at_utc"):
        return _closed(out, order, parse_ts(tracking["delivered_at_utc"]), promise, multiplier)

    eta = parse_ts(tracking["eta_utc"]) if tracking.get("eta_utc") else None
    if eta is None:
        return _unable_to_verify(out, order, ["no ETA and no delivery time"])

    dwell = hours_between(now, last_ts)
    baseline = (
        facilities[last["facility_id"]]["dwell_baseline_hours"]
        if last["code"] in config.FACILITY_CODES
        else config.LINEHAUL_MAX_SCAN_GAP_H
    )
    projected = max(eta, now)
    projected_late = max(0.0, hours_between(projected, promise))
    late_now = max(0.0, hours_between(now, promise))
    margin = hours_between(promise, projected)
    out.update(
        dwell_hours=dwell, dwell_baseline_hours=baseline, hours_late_now=late_now,
        projected_hours_late=projected_late, accrued_penalty_usd=penalty(order, late_now),
        projected_penalty_usd=penalty(order, projected_late),
    )

    if projected_late > 0:
        out["state"] = "delayed"
        out["reasons"].append(f"projected {projected_late:.1f}h past the promise")
    elif dwell > baseline or margin < config.MARGIN_AT_RISK_H or forward >= config.DRIFT_AT_RISK_COUNT:
        out["state"] = "at_risk"
        if dwell > baseline:
            out["reasons"].append(f"dwell {dwell:.1f}h exceeds the {baseline}h baseline")
        if margin < config.MARGIN_AT_RISK_H:
            out["reasons"].append(f"only {margin:.1f}h of margin before the promise")
        if forward >= config.DRIFT_AT_RISK_COUNT:
            out["reasons"].append(f"ETA pushed later {forward} times")
    else:
        out["state"] = "on_track"
        out["total_exposure_usd"] = out["priority_score"] = 0.0
        return _rounded(out)

    exposure = order["order_value_usd"] + out["projected_penalty_usd"]
    out["total_exposure_usd"], out["priority_score"] = exposure, exposure * multiplier
    out["severity"] = severity_for(exposure)
    out["next_milestone"] = _next_milestone(order, promise, now)
    countdown = out["next_milestone"]["minutes_from_now"] if out["next_milestone"] else None
    urgent = exposure >= config.IMMEDIATE_EXPOSURE_USD or (countdown is not None and countdown <= config.IMMEDIATE_COUNTDOWN_MIN)
    out["urgency_badge"] = "IMMEDIATE_SLACK_OR_CALL" if urgent else "DAILY_ROLLUP"
    out["mitigation_ranking"] = _rank_mitigations(order, projected_late)
    out["recommended_action_id"] = out["mitigation_ranking"][0]["action_id"] if out["mitigation_ranking"] else "MANUAL_VERIFY"
    return _rounded(out)


def _try_promise(out, order, tracking, facilities, dispatcher_tz) -> None:
    """Best effort: even when verification fails, show the promise if the order data allows it."""
    try:
        dest = (tracking or {}).get("destination_facility_id")
        tz = facilities[dest]["tz"] if dest in facilities else "UTC"
        promise = resolve_promise(order, tz)
    except (KeyError, TypeError, ValueError):
        return
    out["promise_utc"] = iso(promise)
    out["promise_dispatcher"] = local_string(promise, dispatcher_tz)


def _unable_to_verify(out: dict[str, Any], order: dict[str, Any] | None, problems: list[str]) -> dict[str, Any]:
    out["state"] = "unable_to_verify"
    out["reasons"] = problems
    out["recommended_action_id"] = "MANUAL_VERIFY"
    value = order.get("order_value_usd") if isinstance(order, dict) else None
    if isinstance(value, (int, float)):
        multiplier = config.VIP_MULTIPLIER if order.get("customer_tier") == config.VIP_TIER else 1.0
        out["total_exposure_usd"], out["priority_score"] = float(value), value * multiplier
        out["severity"] = severity_for(value)
        out["urgency_badge"] = "IMMEDIATE_SLACK_OR_CALL" if value >= config.IMMEDIATE_EXPOSURE_USD else "DAILY_ROLLUP"
    return _rounded(out)


def _closed(out, order, delivered, promise, multiplier) -> dict[str, Any]:
    late = max(0.0, hours_between(delivered, promise))
    amount = penalty(order, late)
    out.update(hours_late_now=late, accrued_penalty_usd=amount, total_exposure_usd=amount if late else 0.0)
    out["priority_score"] = out["total_exposure_usd"] * multiplier
    if late > 0:
        out["state"] = "delivered_late"
        out["severity"] = severity_for(amount)
        out["urgency_badge"] = "DAILY_ROLLUP"
        out["recommended_action_id"] = "FILE_CARRIER_CLAIM"
        out["reasons"].append(f"delivered {late:.1f}h after the promise")
    else:
        out["state"] = "on_track"
    return _rounded(out)


def _next_milestone(order: dict[str, Any], promise: datetime, now: datetime) -> dict[str, Any] | None:
    candidates = []
    if order["sla_penalty_per_hour_usd"] > 0:
        candidates.append((promise + timedelta(hours=order["penalty_grace_hours"]), "Penalty clock starts"))
        if order.get("penalty_escalation"):
            candidates.append((promise + timedelta(hours=order["penalty_escalation"]["after_hours_late"]), "Penalty rate escalates"))
    future = sorted(c for c in candidates if c[0] > now)
    if not future:
        return None
    when, label = future[0]
    return {"label": label, "at_utc": iso(when), "minutes_from_now": round(hours_between(when, now) * 60)}


def _rank_mitigations(order: dict[str, Any], projected_late: float) -> list[dict[str, Any]]:
    """Best net benefit first; ties go to lower cost, then to ESCALATE_CARRIER, then to id."""
    ranking = []
    for option in order["mitigation_options"]:
        if not option["eligible"]:
            continue
        avoided = penalty(order, projected_late) - penalty(order, max(0.0, projected_late - option["hours_saved"]))
        ranking.append({
            "action_id": option["action_id"], "label": option.get("label"), "cost_usd": option["cost_usd"],
            "penalty_avoided_usd": round(avoided, 2), "net_benefit_usd": round(avoided - option["cost_usd"], 2),
            "requires_approval": option.get("requires_approval"),
        })
    ranking.sort(key=lambda r: (-r["net_benefit_usd"], r["cost_usd"], r["action_id"] != "ESCALATE_CARRIER", r["action_id"]))
    return ranking


def _rounded(out: dict[str, Any]) -> dict[str, Any]:
    for key, value in list(out.items()):
        if isinstance(value, float):
            out[key] = round(value, 2)
    return out
