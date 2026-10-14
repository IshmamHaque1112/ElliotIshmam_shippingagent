import os
import sys
import threading
from pathlib import Path

from werkzeug.serving import make_server

import app as webapp
import main
import store
from helpers import basic, submit_client
import security

ROOT = Path(__file__).resolve().parent.parent


def test_report_is_self_contained_and_includes_saved_cases(tmp_path, monkeypatch, capsys):
    store.save_case({"shipment_id": "SH-1004", "state": "delayed", "severity": "HIGH", "narrative": "none (fallback)", "run_status": "NEW",
                     "urgency_badge": "IMMEDIATE_SLACK_OR_CALL", "carrier": "Northline Freight",
                     "financial": {"total_exposure_usd": 37575.0, "priority_score": 1, "order_value_usd": 28500, "customer": "<b>Evil</b>", "customer_tier": "VIP_ENTERPRISE"},
                     "timing": {}, "classification": {"root_cause": "Unknown", "accountability": "Undetermined", "confidence": 0.0},
                     "evidence_footnotes": [], "timeline_summary": "<script>x</script>", "unknowns": [], "data_quality": [],
                     "recommendation": {"action_id": "PRIORITY_AIR"}, "carrier_contact": None, "drafts": {"customer_email": "", "carrier_inquiry": ""}},
                    dataset="dev", sim_now_utc="2026-10-08T14:00:00Z", input_hash="old", model=None)
    out = tmp_path / "report.html"
    assert main.main(["report", "--out", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert "Revenue exposed" in html and "Saved cases (1)" in html and "<b>stale</b>" in html
    assert "<script>x" not in html and "&lt;script&gt;x" in html and "&lt;b&gt;Evil" in html
    assert 'src="http' not in html and 'href="http' not in html and "/static/" not in html


def test_client_demo_calls_the_api_over_real_http(monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / "scripts"))
    import client_demo
    server = make_server("127.0.0.1", 0, webapp.app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert client_demo.main([f"http://127.0.0.1:{server.server_port}"]) == 0
    finally:
        server.shutdown()
    out = capsys.readouterr().out
    assert out.startswith("Active exceptions: 9") and " 1. SH-1004" in out


def test_client_demo_needs_credentials(monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / "scripts"))
    import client_demo
    monkeypatch.delenv("DEMO_USERNAME")
    assert client_demo.main(["http://127.0.0.1:1"]) == 2
