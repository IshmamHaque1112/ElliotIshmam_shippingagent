"""Coordinate shipment lookup and validation before any AI work."""

from typing import Any

from model import classify_shipment
from tools import get_shipment, validate_shipment


def prepare_shipment(tracking_number: str) -> dict[str, Any]:
    """Return a manual-review result or a validated shipment ready for AI."""
    shipment = get_shipment(tracking_number)

    if shipment is None:
        return {
            "decision": "NEEDS_MANUAL_REVIEW",
            "shipment": None,
            "reasons": [f"No shipment found for tracking number: {tracking_number.strip()}"],
        }

    problems = validate_shipment(shipment)
    if problems:
        return {
            "decision": "NEEDS_MANUAL_REVIEW",
            "shipment": shipment,
            "reasons": problems,
        }

    return {
        "decision": "READY_FOR_AI",
        "shipment": shipment,
        "reasons": [],
    }


def classify_if_ready(tracking_number: str) -> dict[str, Any]:
    """Classify valid local data with Claude; this function makes an API call."""
    result = prepare_shipment(tracking_number)
    if result["decision"] != "READY_FOR_AI":
        return result

    response = classify_shipment(result["shipment"])
    lines = response.splitlines()
    decision = lines[0].strip() if lines else ""
    allowed_decisions = {"ON_TRACK", "NEEDS_ATTENTION", "NEEDS_MANUAL_REVIEW"}

    if decision not in allowed_decisions:
        return {
            "decision": "NEEDS_MANUAL_REVIEW",
            "shipment": result["shipment"],
            "reasons": ["Claude returned an unexpected classification; dispatcher review is needed."],
        }

    reason = " ".join(line.strip() for line in lines[1:] if line.strip())
    return {
        "decision": decision,
        "shipment": result["shipment"],
        "reasons": [reason] if reason else [],
    }
