from tools import get_shipment, validate_shipment


def test_get_shipment_finds_record_and_trims_tracking_number():
    shipment = get_shipment("  SHIP-1001  ")

    assert shipment is not None
    assert shipment["destination"] == "Boston, MA"


def test_get_shipment_returns_none_for_unknown_number():
    assert get_shipment("SHIP-9999") is None


def test_validate_shipment_accepts_complete_sample():
    shipment = get_shipment("SHIP-1001")

    assert shipment is not None
    assert validate_shipment(shipment) == []


def test_validate_shipment_reports_missing_field_and_bad_values():
    shipment = {
        "tracking_number": "SHIP-TEST",
        "status": "unknown",
        "origin": " ",
        "destination": "Boston, MA",
        "expected_delivery": "not-a-date",
        "last_update": "not-a-timestamp",
    }

    problems = validate_shipment(shipment)

    assert "Missing or blank required field: origin" in problems
    assert "Unrecognized status: unknown" in problems
    assert "expected_delivery must use YYYY-MM-DD format" in problems
    assert "last_update must be an ISO 8601 date and time" in problems
