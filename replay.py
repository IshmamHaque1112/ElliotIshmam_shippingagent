"""Event replay: show the dataset as it looked at an earlier time by hiding events that had not happened yet.

Pure code. The data never contains events after the dataset's own clock, so replay never needs any: it only hides.
A shipment appears once it has both a scan and an ETA observation. At or after the dataset's own time nothing is
hidden, so the end of a replay is exactly the normal snapshot. The rules are not touched; they simply get a
smaller dataset and an earlier `now`.
"""

import copy
from datetime import datetime
from typing import Any

import rules


def _event_time(event: dict[str, Any], facilities: dict[str, Any]) -> datetime:
    return rules.parse_ts(event["timestamp"], facilities[event["facility_id"]]["tz"])


def end_time(dataset: dict[str, Any]) -> datetime:
    return rules.parse_ts(dataset["meta"]["simulated_now_utc"])


def appears_at(shipment: dict[str, Any], facilities: dict[str, Any]) -> datetime | None:
    """When the shipment first has both a scan and an ETA observation (None if it never does)."""
    events = [_event_time(e, facilities) for e in shipment["timeline"]["events"]]
    etas = [rules.parse_ts(h["observed_at"]) for h in shipment["timeline"].get("eta_history") or []]
    if not events or not etas:
        return None
    return max(min(events), min(etas))


def start_time(dataset: dict[str, Any]) -> datetime:
    times = [t for t in (appears_at(s, dataset["facilities"]) for s in dataset["shipments"]) if t]
    return min(times) if times else end_time(dataset)


def visible(dataset: dict[str, Any], now: datetime) -> dict[str, Any]:
    """A copy of the dataset as of `now`. At or after the dataset's own time it is the dataset unchanged."""
    if now >= end_time(dataset):
        return dataset
    facilities, shipments = dataset["facilities"], []
    for original in dataset["shipments"]:
        first = appears_at(original, facilities)
        if first is None or first > now:
            continue
        s = copy.deepcopy(original)
        events = [e for e in s["timeline"]["events"] if _event_time(e, facilities) <= now]
        history = [h for h in s["timeline"].get("eta_history") or [] if rules.parse_ts(h["observed_at"]) <= now]
        s["timeline"] = {**s["timeline"], "events": events, "eta_history": history}
        last = max(events, key=lambda e: _event_time(e, facilities))
        tracking = s["tracking"]
        delivered = tracking.get("delivered_at_utc") and any(e["code"] == "DELIVERED" for e in events) and rules.parse_ts(tracking["delivered_at_utc"]) <= now
        every_event_visible = len(events) == len(original["timeline"]["events"])
        tracking.update(
            latest_checkpoint={k: last[k] for k in ("timestamp", "facility_id", "code", "description")},
            current_location=facilities[last["facility_id"]]["name"], last_updated_utc=rules.iso(now),
            eta_utc=None if delivered else max(history, key=lambda h: rules.parse_ts(h["observed_at"]))["eta"],
            delivered_at_utc=tracking["delivered_at_utc"] if delivered else None)
        if not every_event_visible:
            tracking["status_raw"] = "IN_TRANSIT"
        shipments.append(s)
    return {**dataset, "shipments": shipments}


def state(dataset: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {"start_utc": rules.iso(start_time(dataset)), "end_utc": rules.iso(end_time(dataset)), "now_utc": rules.iso(now)}
