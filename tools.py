"""Read shipment records from the local demo dataset."""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


DATA_FILE = Path(__file__).parent / "data" / "shipments.json"
REQUIRED_FIELDS = (
    "tracking_number",
    "status",
    "origin",
    "destination",
    "expected_delivery",
    "last_update",
)
VALID_STATUSES = {"in_transit", "delayed", "delivered", "exception"}


def get_shipment(tracking_number: str) -> dict[str, Any] | None:
    """Return the matching shipment, or None when it is not in the dataset."""
    with DATA_FILE.open(encoding="utf-8") as file:
        shipments = json.load(file)

    for shipment in shipments:
        if shipment.get("tracking_number") == tracking_number.strip():
            return shipment

    return None


def validate_shipment(shipment: dict[str, Any]) -> list[str]:
    """Return validation problems; an empty list means the record is usable."""
    problems = []

    for field in REQUIRED_FIELDS:
        value = shipment.get(field)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"Missing or blank required field: {field}")

    status = shipment.get("status")
    if isinstance(status, str) and status not in VALID_STATUSES:
        problems.append(f"Unrecognized status: {status}")

    delivery_date = shipment.get("expected_delivery")
    if isinstance(delivery_date, str) and delivery_date.strip():
        try:
            date.fromisoformat(delivery_date)
        except ValueError:
            problems.append("expected_delivery must use YYYY-MM-DD format")

    last_update = shipment.get("last_update")
    if isinstance(last_update, str) and last_update.strip():
        try:
            datetime.fromisoformat(last_update)
        except ValueError:
            problems.append("last_update must be an ISO 8601 date and time")

    return problems
