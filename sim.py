"""Simulation controls: a forward-only clock offset, an event-replay time and fault overrides on top of the schedule.

The state lives in a small object. A web request for a signed-in session runs with that session's own object (see
use()), so two people never move each other's clock. Outside a request, or for a script using Basic auth, the one
shared default object is used, exactly as before.
"""

import copy
import json
from contextvars import ContextVar
from threading import Lock
from typing import Any

_lock = Lock()
FAULTS = ("http_429", "http_503", "malformed_json")


class State:
    def __init__(self) -> None:
        self.offset_hours = 0.0
        self.replay_utc: str | None = None   # when set, the dataset is shown as of this time (see replay.py)
        self.overrides: dict[str, dict[str, Any]] = {}   # shipment id -> tool -> fault spec, or None to switch a scheduled fault off


_default = State()
_current: ContextVar[State | None] = ContextVar("sim_state", default=None)


def _cur() -> State:
    return _current.get() or _default


def from_export(data: dict[str, Any] | None) -> State:
    state = State()
    state.offset_hours = float((data or {}).get("offset_hours", 0.0))
    state.replay_utc = (data or {}).get("replay_utc")
    state.overrides = copy.deepcopy((data or {}).get("overrides", {}))
    return state


def use(state: State | None):
    """Run the rest of this request with `state`. Returns a token for release()."""
    return _current.set(state)


def release(token) -> None:
    _current.reset(token)


def offset_hours() -> float:
    return _cur().offset_hours


def advance(hours: float) -> float:
    """Move the simulated clock forward. The data has no later events, so backwards is not allowed."""
    if hours < 0:
        raise ValueError("the simulated clock only moves forward")
    with _lock:
        state = _cur()
        state.offset_hours += hours
        return state.offset_hours


def replay_utc() -> str | None:
    return _cur().replay_utc


def set_replay(value: str | None) -> None:
    with _lock:
        _cur().replay_utc = value


def reset() -> None:
    with _lock:
        state = _cur()
        state.offset_hours = 0.0
        state.replay_utc = None
        state.overrides.clear()


def set_fault(shipment_id: str, tool: str, fault: str | None) -> None:
    """fault is one of FAULTS (always failing), "off" (remove a scheduled fault) or None (clear the override)."""
    with _lock:
        tools = _cur().overrides.setdefault(shipment_id.upper(), {})
        if fault is None:
            tools.pop(tool, None)
        elif fault == "off":
            tools[tool] = None
        elif fault in FAULTS:
            tools[tool] = {"fault": fault, "times": "always"}
        else:
            raise ValueError(f"unknown fault {fault!r}")
        if not tools:
            _cur().overrides.pop(shipment_id.upper(), None)


def overrides() -> dict[str, dict[str, Any]]:
    with _lock:
        return copy.deepcopy(_cur().overrides)


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
    state = _cur()
    return json.dumps([state.offset_hours, overrides(), state.replay_utc], sort_keys=True)


def export() -> dict[str, Any]:
    state = _cur()
    return {"offset_hours": state.offset_hours, "overrides": overrides(), "replay_utc": state.replay_utc}


def load(data: dict[str, Any] | None) -> None:
    """Replace the current state's contents (used to restore the saved shared state)."""
    fresh = from_export(data)
    with _lock:
        state = _cur()
        state.offset_hours, state.replay_utc, state.overrides = fresh.offset_hours, fresh.replay_utc, fresh.overrides


def export_of(state: State) -> dict[str, Any]:
    return {"offset_hours": state.offset_hours, "overrides": copy.deepcopy(state.overrides), "replay_utc": state.replay_utc}
