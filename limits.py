"""Daily budget for Claude calls, kept in the database so it survives restarts and is shared by every worker.

Two caps apply to every call: one for the whole server and one for the caller (a signed-in session, or the username
of a script using Basic auth). The day rolls over at midnight in config.USAGE_TIMEZONE. A call is reserved before it
is made and released if nothing reached Anthropic. If the counter cannot be read or written, no call is allowed
(fail closed). MODEL_CALLS_ENABLED=false switches every Claude path off at once.
"""

import logging
import os
import sqlite3
from contextvars import ContextVar
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
import store

MAX_ANALYSES = config.DAILY_CALLS_PER_SESSION   # per caller, per day (kept under its old name for the pages and tests)
GLOBAL = "global"
log = logging.getLogger("copilot.limits")

_identity: ContextVar[str | None] = ContextVar("usage_identity", default=None)
_held: ContextVar[tuple | None] = ContextVar("usage_held", default=None)
_reason: ContextVar[str | None] = ContextVar("usage_reason", default=None)

MESSAGES = {
    "off": "Claude calls are switched off on this server (MODEL_CALLS_ENABLED=false).",
    "global": "This demo has reached its Claude analysis limit for today. It resets at midnight Eastern time.",
    "caller": "You have reached your Claude analysis limit for today. It resets at midnight Eastern time.",
    "store": "The usage counter could not be reached, so Claude calls are paused. Rules-only views still work.",
}


def enabled() -> bool:
    return (os.environ.get("MODEL_CALLS_ENABLED") or "true").strip().lower() not in ("0", "false", "no", "off")


def day() -> str:
    """Calendar day in the usage time zone. This is the real clock on purpose: it is a spend limit, not a rule."""
    return datetime.now(ZoneInfo(config.USAGE_TIMEZONE)).strftime("%Y-%m-%d")


def set_identity(scope: str | None):
    return _identity.set(scope)


def reset_identity(token) -> None:
    _identity.reset(token)


def identity() -> str:
    return _identity.get() or "user:" + (os.environ.get("DEMO_USERNAME") or "anon")


def _caps() -> list[tuple[str, int]]:
    return [(GLOBAL, config.DAILY_CALLS_GLOBAL), (identity(), config.DAILY_CALLS_PER_SESSION)]


def reserve() -> bool:
    """Take one slot before calling Anthropic. False means no call is allowed now; message() says why."""
    _reason.set(None)
    if not enabled():
        _reason.set("off")
        log.info("Claude calls are switched off; no call made")
        return False
    scopes, today = _caps(), day()
    try:
        full = store.reserve_usage(today, scopes)
        store.purge_usage_before((datetime.now(ZoneInfo(config.USAGE_TIMEZONE)) - timedelta(days=config.USAGE_KEEP_DAYS)).strftime("%Y-%m-%d"))
    except (sqlite3.Error, OSError):
        _reason.set("store")
        return False
    if full is not None:
        _reason.set("global" if full == GLOBAL else "caller")
        log.info("daily cap reached (%s); no Claude call made", _reason.get())   # an expected stop, not an error
        return False
    _held.set((today, [s for s, _ in scopes]))
    return True


def release() -> None:
    """Give the slot back when nothing reached Anthropic (missing key, no connection)."""
    held = _held.get()
    if not held:
        return
    _held.set(None)
    try:
        store.release_usage(*held)
    except (sqlite3.Error, OSError):
        pass   # the slot stays used: the safe direction


def message() -> str:
    return MESSAGES.get(_reason.get() or "caller", MESSAGES["caller"])


def remaining() -> int:
    """Calls this caller may still make today (the smaller of the two caps)."""
    if not enabled():
        return 0
    try:
        today = day()
        return max(0, min(config.DAILY_CALLS_GLOBAL - store.usage_count(today, GLOBAL),
                          config.DAILY_CALLS_PER_SESSION - store.usage_count(today, identity())))
    except (sqlite3.Error, OSError):
        return 0


def used(scope: str = GLOBAL) -> int:
    return store.usage_count(day(), scope)


def set_used(calls: int, scope: str | None = None) -> None:
    """Test and admin helper: set today's counters for the whole server and for a caller."""
    store.set_usage(day(), GLOBAL, calls)
    store.set_usage(day(), scope or identity(), calls)
