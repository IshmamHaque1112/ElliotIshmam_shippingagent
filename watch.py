"""Early-warning watch list for on_track shipments. A separate indicator: it never changes a state, exposure or rank."""

from typing import Any

import config
import rules


def evaluate(assessment: dict[str, Any], now_utc: str) -> dict[str, Any] | None:
    """Reasons this on_track shipment deserves a look, or None."""
    if assessment["state"] != "on_track" or assessment["dwell_hours"] is None:
        return None
    reasons = []
    baseline = assessment["dwell_baseline_hours"] or 0
    ratio = assessment["dwell_hours"] / baseline if baseline else 0.0
    if baseline and round(assessment["dwell_hours"], 2) > round(config.WATCH_DWELL_RATIO * baseline, 2):  # rounding: 16.8 is not above 70% of 24
        reasons.append(f"dwell {assessment['dwell_hours']:.1f}h is {ratio * 100:.0f}% of the {assessment['dwell_baseline_hours']}h baseline")
    tracking = (assessment.get("_data") or {}).get("get_tracking_status") or {}
    if tracking.get("eta_utc") and assessment["promise_utc"] and (assessment["eta_forward_revisions"] or 0) >= config.WATCH_MIN_REVISIONS:
        now = rules.parse_ts(now_utc)
        margin = rules.hours_between(rules.parse_ts(assessment["promise_utc"]), max(rules.parse_ts(tracking["eta_utc"]), now))
        if margin < config.WATCH_MARGIN_H:
            reasons.append(f"ETA moved later {assessment['eta_forward_revisions']} time(s) and only {margin:.1f}h of margin remain")
    return {"shipment_id": assessment["shipment_id"], "reasons": reasons} if reasons else None


def compute(assessments: list[dict[str, Any]], now_utc: str) -> dict[str, dict[str, Any]]:
    found = (evaluate(a, now_utc) for a in assessments)
    return {w["shipment_id"]: w for w in found if w}
