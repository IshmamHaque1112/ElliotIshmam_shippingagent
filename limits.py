"""Per-process cap on Claude analyses (the demo spends real credits)."""

from threading import Lock

MAX_ANALYSES = 10
count = 0
_lock = Lock()


def reserve() -> bool:
    """Take one slot before calling Anthropic. False means the cap is reached."""
    global count
    with _lock:
        if count >= MAX_ANALYSES:
            return False
        count += 1
        return True


def release() -> None:
    """Give a slot back when nothing reached Anthropic (missing key, no connection)."""
    global count
    with _lock:
        count = max(0, count - 1)


def remaining() -> int:
    return max(0, MAX_ANALYSES - count)
