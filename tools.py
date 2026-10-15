"""Three read-only tools over the static fictional dataset, plus a data-quality gate."""

import json
from datetime import datetime
from typing import Any

import config

TOOL_NAMES = ("get_tracking_status", "order_context", "event_timeline")


def load_dataset(path=None) -> dict[str, Any]:
    with open(path or config.dataset_path(), encoding="utf-8") as handle:
        return json.load(handle)


def load_fault_schedule(path=None) -> dict[str, Any]:
    path = path or config.fault_schedule_path()
    if path is None:
        return {}
    with open(path, encoding="utf-8") as handle:
        return {k: v for k, v in json.load(handle).items() if not k.startswith("_")}


def _find(dataset: dict[str, Any], shipment_id: str) -> dict[str, Any] | None:
    wanted = str(shipment_id).strip().upper()
    for shipment in dataset["shipments"]:
        if shipment["shipment_id"].upper() == wanted:
            return shipment
    return None


def _not_found(shipment_id: str) -> dict[str, Any]:
    return {"error": "not_found", "message": f"No shipment with id {shipment_id!r}."}


def get_tracking_status(dataset: dict[str, Any], shipment_id: str) -> dict[str, Any]:
    shipment = _find(dataset, shipment_id)
    if shipment is None:
        return _not_found(shipment_id)
    tracking = dict(shipment["tracking"])
    tracking["carrier"] = dataset["carriers"].get(tracking.get("carrier_id"), {})
    return tracking


def order_context(dataset: dict[str, Any], shipment_id: str) -> dict[str, Any]:
    shipment = _find(dataset, shipment_id)
    return _not_found(shipment_id) if shipment is None else dict(shipment["order"])


def event_timeline(dataset: dict[str, Any], shipment_id: str) -> dict[str, Any]:
    shipment = _find(dataset, shipment_id)
    return _not_found(shipment_id) if shipment is None else dict(shipment["timeline"])


TOOLS = {
    "get_tracking_status": get_tracking_status,
    "order_context": order_context,
    "event_timeline": event_timeline,
}


def _parses(value: Any) -> bool:
    try:
        datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def validate_bundle(
    tracking: dict[str, Any], order: dict[str, Any], timeline: dict[str, Any], facilities: dict[str, Any]
) -> list[str]:
    """Return plain-language problems; any problem makes the shipment unable_to_verify."""
    problems: list[str] = []

    for name, value in (("tracking", tracking), ("order", order), ("timeline", timeline)):
        if not isinstance(value, dict) or "error" in value:
            problems.append(f"{name} data is missing or an error object")
    if problems:
        return problems

    if tracking.get("destination_facility_id") not in facilities:
        problems.append("destination facility is unknown")
    for field in ("order_value_usd", "sla_penalty_per_hour_usd", "penalty_cap_usd", "penalty_grace_hours"):
        if not isinstance(order.get(field), (int, float)) or isinstance(order.get(field), bool):
            problems.append(f"order.{field} must be a number")
    if not (order.get("promise_datetime_utc") or order.get("promise_date")):
        problems.append("order has no promise date")
    if isinstance(order.get("order_value_usd"), (int, float)) and order["order_value_usd"] <= 0:
        problems.append("order value must be greater than zero")
    for field in ("sla_penalty_per_hour_usd", "penalty_cap_usd", "penalty_grace_hours"):
        if isinstance(order.get(field), (int, float)) and not isinstance(order.get(field), bool) and order[field] < 0:
            problems.append(f"order.{field} must not be negative")

    events = timeline.get("events")
    if not isinstance(events, list) or not events:
        problems.append("timeline has no events")
    else:
        for index, event in enumerate(events):
            if not _parses(event.get("timestamp")):
                problems.append(f"event {index} has an unreadable timestamp")
            if event.get("facility_id") not in facilities:
                problems.append(f"event {index} has an unknown facility")
    for index, entry in enumerate(timeline.get("eta_history") or []):
        if not (_parses(entry.get("observed_at")) and _parses(entry.get("eta"))):
            problems.append(f"eta_history entry {index} has an unreadable timestamp")
    for field in ("eta_utc", "delivered_at_utc"):
        if tracking.get(field) and not _parses(tracking[field]):
            problems.append(f"tracking.{field} is unreadable")
    if not problems:
        problems += _conflicts(tracking, timeline, facilities)
    return problems


def _conflicts(tracking: dict[str, Any], timeline: dict[str, Any], facilities: dict[str, Any]) -> list[str]:
    """Tracking and timeline that contradict each other. The rules never pick a side silently: a person checks."""
    import rules  # local import: rules imports this module

    found: list[str] = []
    events = [(rules.parse_ts(e["timestamp"], facilities[e["facility_id"]]["tz"]), e) for e in timeline["events"]]
    last_ts, last = max(events, key=lambda pair: pair[0])
    carrier = last.get("carrier_id")
    if carrier and tracking.get("carrier_id") and carrier != tracking["carrier_id"]:
        found.append(f"tracking names {tracking['carrier_id']} as the carrier but the latest scan is from {carrier}")
    checkpoint = tracking.get("latest_checkpoint") or {}
    if checkpoint.get("timestamp") and checkpoint.get("facility_id") in facilities:
        stamp = rules.parse_ts(checkpoint["timestamp"], facilities[checkpoint["facility_id"]]["tz"])
        if (stamp - last_ts).total_seconds() > config.CHECKPOINT_CONFLICT_TOLERANCE_MIN * 60:
            found.append("tracking shows a newer checkpoint than the event timeline")
    if tracking.get("delivered_at_utc") and not any(e.get("code") == "DELIVERED" for _, e in events):
        found.append("tracking says delivered but the timeline has no DELIVERED event")
    return found
