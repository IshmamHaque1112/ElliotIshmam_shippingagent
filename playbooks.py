"""Static, code-owned playbook checklists keyed by root cause. Claude never writes these."""

import json
from typing import Any

import config

GENERIC = "Generic"


def load() -> dict[str, list[dict[str, str]]]:
    raw = json.loads((config.DATA_DIR / "playbooks.json").read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def for_case(case: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """The playbook for a case: its Claude root cause if there is one, else the generic checklist."""
    books = load()
    cause = case["classification"]["root_cause"] if case.get("narrative") == "claude" else None
    key = cause if cause in books else GENERIC
    return key, books[key]
