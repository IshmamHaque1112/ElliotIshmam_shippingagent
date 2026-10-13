import json

import main


def test_analyze_no_llm_needs_no_key_and_never_sends_anything(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("config.RUN_STATE_PATH", tmp_path / "s.json")
    assert main.main(["analyze", "SH-1004", "--no-llm", "--json"]) == 0
    case = json.loads(capsys.readouterr().out)[0]
    assert case["narrative"] == "none (fallback)" and case["financial"]["total_exposure_usd"] == 37575.0


def test_analyze_without_key_explains_how_to_continue(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None, raising=False)
    assert main.main(["analyze", "SH-1004"]) == 2
    assert "--no-llm" in capsys.readouterr().err


def test_analyze_on_track_shipment_is_not_an_exception(capsys):
    assert main.main(["analyze", "SH-1001", "--no-llm"]) == 1
