import harness


def test_prepare_marks_complete_sample_ready():
    result = harness.prepare_shipment("SHIP-1001")

    assert result["decision"] == "READY_FOR_AI"
    assert result["reasons"] == []


def test_prepare_routes_incomplete_sample_to_manual_review():
    result = harness.prepare_shipment("SHIP-1003")

    assert result["decision"] == "NEEDS_MANUAL_REVIEW"
    assert result["reasons"] == ["Missing or blank required field: origin"]


def test_classify_does_not_call_model_for_unknown_shipment(monkeypatch):
    def unexpected_call(_shipment):
        raise AssertionError("The model must not run for an unknown shipment.")

    monkeypatch.setattr(harness, "classify_shipment", unexpected_call)

    result = harness.classify_if_ready("SHIP-9999")

    assert result["decision"] == "NEEDS_MANUAL_REVIEW"
    assert result["shipment"] is None


def test_classify_does_not_call_model_for_incomplete_shipment(monkeypatch):
    def unexpected_call(_shipment):
        raise AssertionError("The model must not run for an invalid shipment.")

    monkeypatch.setattr(harness, "classify_shipment", unexpected_call)

    result = harness.classify_if_ready("SHIP-1003")

    assert result["decision"] == "NEEDS_MANUAL_REVIEW"
    assert result["reasons"] == ["Missing or blank required field: origin"]


def test_classify_returns_allowed_model_decision_and_reason(monkeypatch):
    monkeypatch.setattr(
        harness,
        "classify_shipment",
        lambda _shipment: "NEEDS_ATTENTION\nWeather disruption caused a delay.",
    )

    result = harness.classify_if_ready("SHIP-1002")

    assert result["decision"] == "NEEDS_ATTENTION"
    assert result["reasons"] == ["Weather disruption caused a delay."]


def test_classify_sends_unexpected_model_decision_to_manual_review(monkeypatch):
    monkeypatch.setattr(harness, "classify_shipment", lambda _shipment: "Probably fine")

    result = harness.classify_if_ready("SHIP-1001")

    assert result["decision"] == "NEEDS_MANUAL_REVIEW"
    assert result["reasons"] == [
        "Claude returned an unexpected classification; dispatcher review is needed."
    ]
