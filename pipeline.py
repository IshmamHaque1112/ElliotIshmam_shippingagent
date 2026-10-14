"""fetch -> rules for every shipment, with no LLM. The agent only runs afterwards, on exceptions."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from threading import Lock
from typing import Any, Callable

import config
import fetch as fetch_module
import rules
import sim
import tools
from mock_service import MockService

EXCEPTION_STATES = ("delayed", "at_risk", "unable_to_verify", "delivered_late")


def load_service() -> MockService:
    """A fresh mock service (fresh call counts) with any simulation fault overrides applied."""
    return MockService(tools.load_dataset(), sim.merged_faults(tools.load_fault_schedule()))


def now_of(dataset: dict[str, Any]):
    """Simulated time: the dataset's clock plus the forward-only simulation offset. Never the real clock."""
    base = rules.parse_ts(dataset["meta"]["simulated_now_utc"]).astimezone(timezone.utc)
    return base + timedelta(hours=sim.offset_hours())


def assess(service: MockService, shipment_id: str, sleep: Callable[[float], None] | None = None) -> dict[str, Any]:
    """Fetch the three tools (with retries) and apply the rules."""
    kwargs = {"sleep": sleep} if sleep else {}
    results = {name: fetch_module.fetch(service, name, shipment_id, **kwargs) for name in tools.TOOL_NAMES}
    unavailable = [name for name in ("get_tracking_status", "event_timeline") if not results[name].ok]
    if not results["order_context"].ok:
        unavailable.append("order_context")
    data = {name: (r.data if r.ok else None) for name, r in results.items()}
    dataset = service.dataset
    verdict = rules.evaluate(
        shipment_id, data["get_tracking_status"], data["order_context"], data["event_timeline"],
        dataset["facilities"], now_of(dataset), dataset["meta"]["dispatcher_timezone"], unavailable,
    )
    verdict["fetch_errors"] = {n: r.error for n, r in results.items() if not r.ok}
    verdict["_data"] = data  # raw tool data for the case file; stripped from JSON output by callers
    return verdict


def assess_all(service: MockService, sleep: Callable[[float], None] | None = None) -> list[dict[str, Any]]:
    return [assess(service, s["shipment_id"], sleep) for s in service.dataset["shipments"]]


def ranked_queue(assessments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queue = [a for a in assessments if a["state"] in EXCEPTION_STATES]
    return sorted(queue, key=lambda a: (-(a["priority_score"] or 0), a["shipment_id"]))


def value_at_risk(assessments: list[dict[str, Any]]) -> dict[str, Any]:
    active = [a for a in assessments if a["state"] in ("delayed", "at_risk", "unable_to_verify")]
    closed = [a for a in assessments if a["state"] == "delivered_late"]
    return {
        "active_exceptions": len(active),
        "revenue_exposed_usd": round(sum(a["total_exposure_usd"] or 0 for a in active), 2),
        "accrued_penalties_usd": round(sum(a["accrued_penalty_usd"] or 0 for a in active), 2),
        "closed_late_cases": len(closed),
        "closed_late_penalties_usd": round(sum(a["accrued_penalty_usd"] or 0 for a in closed), 2),
        "on_track": sum(1 for a in assessments if a["state"] == "on_track"),
    }


def rules_hash(assessment: dict[str, Any]) -> str:
    """Fingerprint of the rules output; a saved Claude case is stale when this changes."""
    public = {k: v for k, v in assessment.items() if not k.startswith("_")}
    return hashlib.sha256(json.dumps(public, sort_keys=True, default=str).encode()).hexdigest()[:16]


@dataclass
class Snapshot:
    dataset_name: str
    sim_now_utc: str
    dataset: dict[str, Any]
    assessments: list[dict[str, Any]]
    by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    queue: list[dict[str, Any]] = field(default_factory=list)
    var: dict[str, Any] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)


def _row(sh: dict[str, Any], a: dict[str, Any], dataset: dict[str, Any], rank: int | None) -> dict[str, Any]:
    tracking, order = sh["tracking"], sh["order"]
    carrier = dataset["carriers"].get(tracking["carrier_id"], {})
    milestone = a["next_milestone"]
    return {
        "shipment_id": a["shipment_id"], "order_id": order["order_id"], "customer": order["customer_name"],
        "tier": order["customer_tier"], "carrier_id": tracking["carrier_id"], "carrier": carrier.get("name"),
        "service_level": tracking.get("service_level"), "status_raw": tracking["status_raw"], "state": a["state"],
        "severity": a["severity"], "urgency_badge": a["urgency_badge"], "exposure_usd": a["total_exposure_usd"],
        "priority_score": a["priority_score"], "promise_utc": a["promise_utc"], "promise_dispatcher": a["promise_dispatcher"],
        "eta_utc": tracking.get("eta_utc"), "delivered_at_utc": tracking.get("delivered_at_utc"), "rank": rank,
        "minutes_to_milestone": milestone["minutes_from_now"] if milestone else None,
        "recommended_action_id": a["recommended_action_id"], "last_scan_dispatcher": a["last_scan_dispatcher"],
        "location": tracking.get("current_location"), "order_value_usd": order["order_value_usd"],
    }


_cache_lock = Lock()
_cache: dict[tuple, Snapshot] = {}


def snapshot() -> Snapshot:
    """Rules output for every shipment, cached per (dataset, simulation state).

    The web view skips the retry back-off sleeps (the service is simulated, so waiting adds nothing);
    the same retry counts still apply. The CLI and the agent keep the real back-off.
    """
    key = (config.dataset_name(), sim.key())
    with _cache_lock:
        if key in _cache:
            return _cache[key]
        service = load_service()
        assessments = assess_all(service, sleep=lambda s: None)
        queue = ranked_queue(assessments)
        ranks = {a["shipment_id"]: i for i, a in enumerate(queue, 1)}
        by_id = {a["shipment_id"]: a for a in assessments}
        rows = [_row(sh, by_id[sh["shipment_id"]], service.dataset, ranks.get(sh["shipment_id"])) for sh in service.dataset["shipments"]]
        snap = Snapshot(key[0], rules.iso(now_of(service.dataset)), service.dataset, assessments, by_id, queue,
                        value_at_risk(assessments), rows)
        _cache.clear()   # keep only the latest state; the cache is tiny
        _cache[key] = snap
        return snap


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
