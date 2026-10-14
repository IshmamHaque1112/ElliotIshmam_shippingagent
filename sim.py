"""In-memory simulation controls: a forward-only clock offset and fault overrides on top of the schedule."""

import copy
import json
from threading import Lock
from typing import Any

_lock = Lock()
_offset_hours = 0.0
_overrides: dict[str, dict[str, Any]] = {}   # shipment id -> tool -> fault spec, or None to switch a scheduled fault off

FAULTS = ("http_429", "http_503", "malformed_json")


def offset_hours() -> float:
    return _offset_hours


def advance(hours: float) -> float:
    """Move the simulated clock forward. The data has no later events, so backwards is not allowed."""
    global _offset_hours
    if hours < 0:
        raise ValueError("the simulated clock only moves forward")
    with _lock:
        _offset_hours += hours
        return _offset_hours


def reset() -> None:
    global _offset_hours
    with _lock:
        _offset_hours = 0.0
        _overrides.clear()


def set_fault(shipment_id: str, tool: str, fault: str | None) -> None:
    """fault is one of FAULTS (always failing), "off" (remove a scheduled fault) or None (clear the override)."""
    with _lock:
        tools = _overrides.setdefault(shipment_id.upper(), {})
        if fault is None:
            tools.pop(tool, None)
        elif fault == "off":
            tools[tool] = None
        elif fault in FAULTS:
            tools[tool] = {"fault": fault, "times": "always"}
        else:
            raise ValueError(f"unknown fault {fault!r}")
        if not tools:
            _overrides.pop(shipment_id.upper(), None)


def overrides() -> dict[str, dict[str, Any]]:
    with _lock:
        return copy.deepcopy(_overrides)


def merged_faults(schedule: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(schedule)
    for sid, tools in overrides().items():
        for tool, spec in tools.items():
            if spec is None:
                merged.get(sid, {}).pop(tool, None)
            else:
                merged.setdefault(sid, {})[tool] = spec
    return merged


def key() -> str:
    """Changes whenever anything that affects the rules output changes (used to invalidate the cache)."""
    return json.dumps([_offset_hours, overrides()], sort_keys=True)


def export() -> dict[str, Any]:
    return {"offset_hours": _offset_hours, "overrides": overrides()}


def load(state: dict[str, Any] | None) -> None:
    global _offset_hours
    with _lock:
        _offset_hours = float((state or {}).get("offset_hours", 0.0))
        _overrides.clear()
        _overrides.update((state or {}).get("overrides", {}))
