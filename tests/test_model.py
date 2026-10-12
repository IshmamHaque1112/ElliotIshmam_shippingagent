from types import SimpleNamespace

import model


def test_classify_shipment_uses_sdk_and_returns_text(monkeypatch):
    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="ON_TRACK\nNo exception.")]
            )

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.messages = FakeMessages()

    monkeypatch.setattr(model, "Anthropic", FakeAnthropic)
    monkeypatch.setattr(model, "load_dotenv", lambda: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    result = model.classify_shipment({"tracking_number": "SHIP-TEST"})

    assert result == "ON_TRACK\nNo exception."
    assert captured["client"]["api_key"] == "test-key-not-real"
    assert captured["request"]["model"] == model.MODEL
    assert captured["request"]["max_tokens"] == 120


def test_classify_shipment_fails_before_client_if_key_is_missing(monkeypatch):
    monkeypatch.setattr(model, "load_dotenv", lambda: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def unexpected_client(**_kwargs):
        raise AssertionError("The SDK client must not be created without a key.")

    monkeypatch.setattr(model, "Anthropic", unexpected_client)

    try:
        model.classify_shipment({"tracking_number": "SHIP-TEST"})
    except RuntimeError as error:
        assert "ANTHROPIC_API_KEY is missing" in str(error)
    else:
        raise AssertionError("Expected a missing-key RuntimeError.")
