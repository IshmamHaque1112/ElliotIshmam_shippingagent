"""fetch -> rules for every shipment, with no LLM. The agent only runs afterwards, on exceptions."""

from datetime import timezone
from typing import Any, Callable

import config
import fetch as fetch_module
import rules
import tools
from mock_service import MockService

EXCEPTION_STATES = ("delayed", "at_risk", "unable_to_verify", "delivered_late")


def load_service() -> MockService:
    return MockService(tools.load_dataset(), tools.load_fault_schedule())


def now_of(dataset: dict[str, Any]):
    return rules.parse_ts(dataset["meta"]["simulated_now_utc"]).astimezone(timezone.utc)


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
