"""Wraps the read-only tools with fault injection from fault_schedule.json.

The service returns JSON *text*, like an HTTP body, so a "malformed_json" fault can truncate it.
It never reads the hidden ground-truth labels.
"""

import json
from typing import Any

import tools


class ServiceError(Exception):
    """A simulated HTTP failure (429 or 503)."""

    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


class MockService:
    def __init__(self, dataset: dict[str, Any], faults: dict[str, Any] | None = None):
        self.dataset = dataset
        self.faults = faults or {}
        self.call_counts: dict[tuple[str, str], int] = {}

    def total_calls(self, shipment_id: str | None = None) -> int:
        return sum(n for (sid, _), n in self.call_counts.items() if shipment_id in (None, sid))

    def call(self, tool: str, shipment_id: str) -> str:
        if tool not in tools.TOOLS:
            raise ValueError(f"unknown tool {tool!r}")
        key = (str(shipment_id).strip().upper(), tool)
        self.call_counts[key] = self.call_counts.get(key, 0) + 1

        fault = self.faults.get(key[0], {}).get(tool)
        if fault and (fault["times"] == "always" or self.call_counts[key] <= fault["times"]):
            kind = fault["fault"]
            if kind == "http_429":
                raise ServiceError(429)
            if kind == "http_503":
                raise ServiceError(503)
            if kind == "malformed_json":
                return json.dumps(tools.TOOLS[tool](self.dataset, shipment_id))[:40]
            raise ValueError(f"unknown fault {kind!r}")

        return json.dumps(tools.TOOLS[tool](self.dataset, shipment_id))
