"""Calls a tool through the mock service with retries, backoff and structured failures."""

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

import config
from mock_service import MockService, ServiceError


@dataclass
class FetchResult:
    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    attempts: int = 0

    def as_tool_content(self) -> dict[str, Any]:
        if self.ok:
            return self.data or {}
        return {"error": "tool_unavailable", "message": self.error, "attempts": self.attempts}


def fetch(
    service: MockService,
    tool: str,
    shipment_id: str,
    attempts: int = config.FETCH_ATTEMPTS,
    backoff: float = config.FETCH_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchResult:
    error = "no attempt made"
    for attempt in range(1, attempts + 1):
        try:
            data = json.loads(service.call(tool, shipment_id))
            if not isinstance(data, dict):
                raise ValueError("response was not a JSON object")
            return FetchResult(True, data=data, attempts=attempt)
        except ServiceError as exc:
            error = str(exc)
        except ValueError:  # includes json.JSONDecodeError
            error = "malformed JSON response"
        if attempt < attempts:
            sleep(backoff * 2 ** (attempt - 1))
    return FetchResult(False, error=f"{tool} failed after {attempts} attempts: {error}", attempts=attempts)
