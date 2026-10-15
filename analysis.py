"""Run one Claude analysis (or save a rules-only case) and record it in the store."""

import time
from dataclasses import dataclass
from typing import Any

from anthropic import APIConnectionError, APIError

import agent
import agent_trace
import limits
import case_file
import config
import pipeline
import store
from api_errors import MissingKeyError, api_error_message

RANK = case_file.STATE_RANK


@dataclass
class Outcome:
    case_id: int
    case: case_file.CaseFile


def run_status_from_store(assessment: dict[str, Any], dataset: str) -> str:
    """NEW if never saved; WORSENED if state or exposure got worse since the last saved case; else UNCHANGED."""
    previous = store.latest_case_for(assessment["shipment_id"], dataset)
    if previous is None:
        return "NEW"
    exposure = assessment["total_exposure_usd"] or 0
    if RANK[assessment["state"]] > RANK.get(previous["state"], 0) or exposure > (previous["exposure_usd"] or 0):
        return "WORSENED"
    return "UNCHANGED"


def _estimate_cost(model: str, usage: dict[str, int]) -> float | None:
    price = config.PRICES.get(model)
    if not price:
        return None
    return round((usage.get("input_tokens", 0) * price[0] + usage.get("output_tokens", 0) * price[1]) / 1_000_000, 6)


def _save(snap: pipeline.Snapshot, a: dict[str, Any], case: case_file.CaseFile, model: str | None) -> int:
    return store.save_case(case.model_dump(), dataset=snap.dataset_name, sim_now_utc=snap.sim_now_utc,
                           input_hash=pipeline.rules_hash(a), model=model)


def report_rules_only(shipment_id: str) -> Outcome:
    snap = pipeline.snapshot()
    a = snap.by_id[shipment_id]
    case = case_file.build_case_file(a, None, status=run_status_from_store(a, snap.dataset_name))
    return Outcome(_save(snap, a, case, None), case)


def analyze(shipment_id: str) -> Outcome:
    """Raises MissingKeyError or an Anthropic error; the caller owns the usage cap.

    A run row is written for everything that reached Anthropic (ok, retried, fallback, error).
    """
    snap = pipeline.snapshot()
    a = snap.by_id[shipment_id]
    status = run_status_from_store(a, snap.dataset_name)
    client = agent.make_client()
    service = pipeline.load_service()
    model, usage, trace, started = agent.model_name(), {}, {}, time.perf_counter()
    attempt_calls: list[list[dict[str, Any]]] = []

    def investigate(hint):
        calls: list[dict[str, Any]] = []
        attempt_calls.append(calls)
        return agent.investigate(client, service, a, hint, usage=usage, calls=calls)

    def trace_doc(**extra) -> dict[str, Any]:
        return agent_trace.build(attempt_calls, trace.get("attempt_results", []), **extra)

    def millis() -> int:
        return int((time.perf_counter() - started) * 1000)

    try:
        case = case_file.build_case_file(a, investigate, status=status, trace=trace)
    except APIError as error:
        if not isinstance(error, APIConnectionError):  # a connection failure never reached Anthropic
            message = api_error_message(error)[:300]
            run_id = store.add_run(shipment_id=shipment_id, model=model, status="error", tool_calls=usage.get("tool_calls", 0),
                                   input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
                                   duration_ms=millis(), retries=max(0, trace.get("attempts", 1) - 1), rejection_reason=message)
            store.save_trace(trace_doc(error=message, final="error"), shipment_id=shipment_id, run_id=run_id)
        raise

    attempts = trace.get("attempts", 1)
    run_state = "fallback" if case.narrative != "claude" else "retried" if attempts > 1 else "ok"
    case_id = _save(snap, a, case, model)
    run_id = store.add_run(case_id=case_id, shipment_id=shipment_id, model=model, status=run_state, tool_calls=usage.get("tool_calls", 0),
                  input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
                  est_cost_usd=_estimate_cost(model, usage), duration_ms=millis(), retries=max(0, attempts - 1),
                  rejection_reason="; ".join(trace.get("rejections", []))[:500] or None)
    store.save_trace(trace_doc(final=run_state), shipment_id=shipment_id, run_id=run_id, case_id=case_id)
    return Outcome(case_id, case)


LIMIT_MESSAGE = "This demo has reached its Claude analysis limit. Ask the demo owner before retrying."


def analyze_with_cap(shipment_id: str) -> tuple[str, Outcome | None, str | None]:
    """Reserve a cap slot, analyze, and give the slot back if nothing reached Anthropic.

    Returns (kind, outcome, message) where kind is ok, limit, no_key, connection or api_error.
    """
    if not limits.reserve():
        return "limit", None, LIMIT_MESSAGE
    try:
        return "ok", analyze(shipment_id), None
    except MissingKeyError as error:
        limits.release()
        return "no_key", None, f"Could not analyze with Claude: {error}"
    except APIConnectionError as error:
        limits.release()
        return "connection", None, api_error_message(error)
    except APIError as error:
        return "api_error", None, api_error_message(error)
