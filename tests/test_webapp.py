import base64
import re
from types import SimpleNamespace

import pytest
from anthropic import APIConnectionError, APIStatusError

import app as webapp
import case_file
import pipeline
from api_errors import MissingKeyError


@pytest.fixture(autouse=True)
def configure(monkeypatch):
    monkeypatch.setenv("DEMO_USERNAME", "pursuit-demo")
    monkeypatch.setenv("DEMO_PASSWORD", "test-only-password")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setattr(webapp, "_analysis_count", 0)
    webapp.app.config.update(TESTING=True)


def basic(username="pursuit-demo", password="test-only-password"):
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def client_with_token():
    client = webapp.app.test_client()
    page = client.get("/", headers=basic()).get_data(as_text=True)
    return client, re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


def analyze(client, token, shipment="SH-1004"):
    return client.post(f"/case/{shipment}/analyze", data={"csrf_token": token}, headers=basic())


def test_queue_page_needs_no_api_call_and_is_ranked(monkeypatch):
    monkeypatch.setattr(webapp.agent, "make_client", lambda: (_ for _ in ()).throw(AssertionError("no API call")))
    html = webapp.app.test_client().get("/", headers=basic()).get_data(as_text=True)
    assert "Active exceptions" in html and html.index("SH-1004") < html.index("SH-1008") < html.index("SH-1003")
    assert "Analyze with Claude" in html and "Rules only" in html


def test_rules_only_case_page_shows_numbers_and_readonly_drafts():
    html = webapp.app.test_client().get("/case/SH-1004", headers=basic()).get_data(as_text=True)
    assert "37,575.00" in html and "Priority air re-consignment" in html and "555-0142" in html
    assert "No drafts in rules-only mode" in html


def test_unknown_and_on_track_cases_are_404():
    c = webapp.app.test_client()
    assert c.get("/case/SH-9999", headers=basic()).status_code == 404
    assert c.get("/case/SH-1001", headers=basic()).status_code == 404


def test_login_required_and_bad_headers_get_401_not_500():
    c = webapp.app.test_client()
    assert c.get("/").status_code == 401
    assert c.get("/", headers=basic("démo", "pw")).status_code == 401
    assert c.get("/", headers={"Authorization": "Bearer abc"}).status_code == 401
    assert c.get("/", headers={"Authorization": "Basic !!!"}).status_code == 401


@pytest.mark.parametrize("missing", ["DEMO_USERNAME", "DEMO_PASSWORD", "FLASK_SECRET_KEY"])
def test_fails_closed_when_any_secret_is_missing(monkeypatch, missing):
    monkeypatch.delenv(missing)
    assert webapp.app.test_client().get("/", headers=basic()).status_code == 503


def test_analyze_without_csrf_token_is_forbidden(monkeypatch):
    monkeypatch.setattr(webapp.agent, "make_client", lambda: (_ for _ in ()).throw(AssertionError("must not run")))
    c, _ = client_with_token()
    assert c.post("/case/SH-1004/analyze", data={}, headers=basic()).status_code == 403
    assert c.post("/case/SH-1004/analyze", data={"csrf_token": "wrong"}, headers=basic()).status_code == 403
    assert webapp.app.test_client().post("/case/SH-1004/analyze", data={"csrf_token": "x"}, headers=basic()).status_code == 403


def test_cap_stops_before_the_model_call(monkeypatch):
    monkeypatch.setattr(webapp, "_analysis_count", webapp.MAX_ANALYSES)
    monkeypatch.setattr(webapp.agent, "make_client", lambda: (_ for _ in ()).throw(AssertionError("must not run")))
    c, token = client_with_token()
    response = analyze(c, token)
    assert response.status_code == 429 and b"analysis limit" in response.data


def test_missing_key_does_not_use_the_cap_and_shows_rules_only(monkeypatch):
    def no_key():
        raise MissingKeyError("ANTHROPIC_API_KEY is missing.")
    monkeypatch.setattr(webapp.agent, "make_client", no_key)
    c, token = client_with_token()
    codes = [analyze(c, token).status_code for _ in range(webapp.MAX_ANALYSES + 1)]
    assert 429 not in codes and webapp._analysis_count == 0
    assert b"rules-only case file" in analyze(c, token).data


def test_connection_failures_do_not_use_the_cap(monkeypatch):
    def unreachable():
        raise APIConnectionError(request=None)
    monkeypatch.setattr(webapp.agent, "make_client", unreachable)
    c, token = client_with_token()
    codes = [analyze(c, token).status_code for _ in range(webapp.MAX_ANALYSES + 1)]
    assert 429 not in codes and webapp._analysis_count == 0


def test_requests_that_reach_the_api_use_the_cap(monkeypatch):
    monkeypatch.setattr(webapp.agent, "make_client", lambda: object())

    def server_error(*args, **kwargs):
        raise APIStatusError("boom", response=SimpleNamespace(status_code=500, headers={}, request=None), body=None)
    monkeypatch.setattr(webapp.agent, "investigate", server_error)
    c, token = client_with_token()
    assert analyze(c, token).status_code == 200
    assert webapp._analysis_count == 1


def test_model_text_is_escaped_in_the_case_page(monkeypatch):
    monkeypatch.setattr(webapp.agent, "make_client", lambda: object())
    evil = dict(
        root_cause="Unknown", accountability="Undetermined", confidence=0.1, evidence_footnotes=[],
        timeline_summary="<script>alert(1)</script>", unknowns=["<img src=x onerror=alert(2)>"], data_quality_notes=[],
        customer_email="</textarea><script>alert(3)</script> SH-1004", carrier_inquiry="SH-1004 inquiry",
        recommendation_rationale="ok",
    )
    monkeypatch.setattr(webapp.agent, "investigate", lambda *a, **k: evil)
    c, token = client_with_token()
    html = analyze(c, token).get_data(as_text=True)
    assert "<script>alert" not in html and "&lt;script&gt;alert(1)" in html and "onerror=alert(2)>" not in html
    assert "claude" in html


def test_successful_analysis_shows_claude_case_with_drafts(monkeypatch):
    monkeypatch.setattr(webapp.agent, "make_client", lambda: object())
    ok = dict(
        root_cause="Hub Bottleneck", accountability="Carrier Fault", confidence=0.9,
        evidence_footnotes=[{"tool": "event_timeline", "field": "events", "claim": "Waiting"}],
        timeline_summary="Stuck at Chicago.", unknowns=[], data_quality_notes=[],
        customer_email="Customer draft SH-1004", carrier_inquiry="Carrier draft SH-1004", recommendation_rationale="Best net.",
    )
    monkeypatch.setattr(webapp.agent, "investigate", lambda *a, **k: ok)
    c, token = client_with_token()
    html = analyze(c, token).get_data(as_text=True)
    assert "Customer draft SH-1004" in html and "readonly" in html and "Hub Bottleneck" in html
