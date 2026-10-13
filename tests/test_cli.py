import sys

import main


def test_classify_cli_reports_missing_key_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["main.py", "classify", "SHIP-1001"])

    def missing_key(_tracking_number):
        raise RuntimeError("ANTHROPIC_API_KEY is missing; check the local .env file.")

    monkeypatch.setattr(main, "classify_if_ready", missing_key)

    exit_code = main.main()
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "Could not classify shipment" in captured.err
    assert "ANTHROPIC_API_KEY is missing" in captured.err
    assert captured.out == ""
