"""Agent trace: what the investigator did for one analysis. Pure functions, no I/O.

A trace holds ordered tool calls per submission attempt and why an attempt was rejected. Tool results can contain
carrier text, so they are stored as short truncated strings and always escaped when shown. Nothing here ever sees
the API key or request headers.
"""

import json
from typing import Any

import config

OUTCOMES = ("ok", "tool_error", "repeated_call", "wrong_shipment", "budget_exhausted")


def clip(value: Any, limit: int | None = None) -> str:
    limit = limit or config.TRACE_TEXT_LIMIT
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def call_entry(tool: str, arguments: Any, content: dict[str, Any]) -> dict[str, Any]:
    """One tool call as the model saw it. `content` is what the agent returned to the model."""
    error = content.get("error") if isinstance(content, dict) else None
    outcome = "ok" if not error else "tool_error" if error == "tool_unavailable" else error
    if outcome not in OUTCOMES:
        outcome = "tool_error"
    return {"tool": clip(tool, 60), "arguments": clip(arguments, 200), "outcome": outcome,
            "refused": outcome in ("repeated_call", "wrong_shipment", "budget_exhausted"), "summary": clip(content)}


def classify_rejection(reason: str) -> str:
    if reason.startswith("schema problems"):
        return "schema_error"
    if "dollar figures" in reason:
        return "invented_figure"
    return "agent_error"


def build(attempt_calls: list[list[dict[str, Any]]], attempt_results: list[dict[str, Any]], *, error: str | None = None,
          final: str | None = None) -> dict[str, Any]:
    """Assemble the stored trace. An attempt with no recorded result ended with an API error."""
    attempts = []
    for i, calls in enumerate(attempt_calls):
        result = attempt_results[i] if i < len(attempt_results) else {"outcome": "api_error", "kind": "api_error", "reason": error or ""}
        attempts.append({"attempt": i + 1, "calls": calls, "submitted": result.get("kind") != "agent_error" and result["outcome"] != "api_error",
                         "outcome": result["outcome"], "kind": result.get("kind"), "reason": clip(result.get("reason", ""), 400)})
    return {"version": 1, "final": final, "error": clip(error, 300) if error else None, "attempts": attempts,
            "tool_calls": sum(len(a["calls"]) for a in attempts)}
