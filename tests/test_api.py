import json

import pytest
from anthropic import APIConnectionError, APIStatusError
from types import SimpleNamespace

import app as webapp
import limits
import security
import sim
import store
from helpers import FakeClient, basic, good_submission, reply, submit_client, use
import prompt


def get(path, **kw):
    return webapp.app.test_client().get(path, headers=basic(), **kw)


def post(path, data=None, token=True):
    headers = dict(basic(), **({"X-CSRF-Token": security.csrf_token()} if token else {}))
    return webapp.app.test_client().post(path, json=data or {}, headers=headers)


def patch_client(monkeypatch, client):
    monkeypatch.setattr(webapp.api.analysis.agent, "make_client", lambda: client)


def test_auth_required_and_fail_closed(monkeypatch):
    assert webapp.app.test_client().get("/api/summary").status_code == 401
    monkeypatch.delenv("FLASK_SECRET_KEY")
    assert get("/api/summary").status_code == 503


def test_summary_has_schema_and_value_at_risk():
    body = get("/api/summary").get_json()
    assert body["schema"] == "v1" and body["dataset"] == "dev"
    assert body["value_at_risk"]["active_exceptions"] == 9 and body["by_state"]["delayed"] == 6


def test_shipments_pagination_filter_search_and_sort_on_extended(monkeypatch):
    monkeypatch.setenv("DATASET", "extended")
    body = get("/api/shipments?page_size=10").get_json()
    assert body["total"] == 60 and body["pages"] == 6 and len(body["items"]) == 10
    delayed = get("/api/shipments?state=delayed&page_size=100").get_json()
    assert delayed["total"] == 29 and {r["state"] for r in delayed["items"]} == {"delayed"}
    top = get("/api/shipments?sort=exposure&page_size=3").get_json()["items"]
    assert [r["exposure_usd"] for r in top] == sorted((r["exposure_usd"] for r in top), reverse=True)
    one = get("/api/shipments?q=sh-3001").get_json()
    assert one["total"] == 1 and one["items"][0]["shipment_id"] == "SH-3001"
    assert get("/api/shipments?page=999&page_size=-5").get_json()["page"] == 60
    assert get("/api/shipments?page=abc").get_json()["page"] == 1
    assert get("/api/shipments?carrier=ZZZ").get_json()["total"] == 0


def test_rows_without_a_value_sort_last_in_both_directions():
    for direction in ("asc", "desc"):
        items = get(f"/api/shipments?sort=exposure&dir={direction}&page_size=100").get_json()["items"]
        values = [r["exposure_usd"] for r in items]
        assert values == sorted(values, reverse=direction == "desc")


def test_shipment_detail_has_raw_tool_data_and_no_internals_or_labels():
    body = get("/api/shipments/sh-1012").get_json()
    assert body["tracking"]["carrier"]["name"] and body["order"]["order_id"] and body["timeline"]["events"]
    assert "timeline.events[].carrier_note" in body["untrusted_fields"]
    text = json.dumps(body)
    assert "_data" not in text and "expected_" not in text and "ground_truth" not in text


def test_not_found_is_json_without_a_stack_trace():
    r = get("/api/shipments/SH-9999")
    assert r.status_code == 404 and r.get_json() == {"schema": "v1", "error": "not found", "code": 404}
    assert get("/api/cases/999").status_code == 404


def test_queue_matches_the_ranked_order():
    ids = [r["shipment_id"] for r in get("/api/queue").get_json()["queue"]]
    assert ids[:3] == ["SH-1004", "SH-1008", "SH-1003"] and len(ids) == 10


def test_posts_need_csrf():
    assert post("/api/shipments/SH-1004/analyze", token=False).status_code == 403
    assert post("/api/cases/1/disposition", {"status": "resolved"}, token=False).status_code == 403


def test_analyze_saves_case_and_logs_the_run(monkeypatch):
    patch_client(monkeypatch, submit_client())
    r = post("/api/shipments/SH-1004/analyze")
    assert r.status_code == 200 and r.get_json()["narrative"] == "claude" and limits.count == 1
    cases = get("/api/cases").get_json()["cases"]
    assert len(cases) == 1 and cases[0]["stale"] is False and cases[0]["case"]["financial"]["total_exposure_usd"] == 37575.0
    run = get("/api/runs").get_json()
    assert run["runs"][0]["status"] == "ok" and run["runs"][0]["input_tokens"] == 100 and run["totals"]["runs"] == 1
    assert run["totals"]["est_cost_usd"] is None   # no prices configured


def test_retried_and_fallback_runs_are_logged(monkeypatch):
    bad = good_submission(root_cause="Gremlins")
    patch_client(monkeypatch, FakeClient([reply(use(prompt.SUBMIT_TOOL_NAME, bad, "1")), reply(use(prompt.SUBMIT_TOOL_NAME, good_submission(), "2"))]))
    assert post("/api/shipments/SH-1004/analyze").status_code == 200
    patch_client(monkeypatch, FakeClient([reply(use(prompt.SUBMIT_TOOL_NAME, bad, "1")), reply(use(prompt.SUBMIT_TOOL_NAME, bad, "2"))]))
    post("/api/shipments/SH-1008/analyze")
    runs = {r["shipment_id"]: r for r in get("/api/runs").get_json()["runs"]}
    assert runs["SH-1004"]["status"] == "retried" and runs["SH-1004"]["retries"] == 1
    assert runs["SH-1008"]["status"] == "fallback" and "schema problems" in runs["SH-1008"]["rejection_reason"]


def test_api_errors_are_logged_and_use_the_cap(monkeypatch):
    def boom(**kw):
        raise APIStatusError("boom", response=SimpleNamespace(status_code=500, headers={}, request=None), body=None)
    patch_client(monkeypatch, SimpleNamespace(messages=SimpleNamespace(create=boom)))
    r = post("/api/shipments/SH-1004/analyze")
    assert r.status_code == 502 and limits.count == 1
    assert get("/api/runs").get_json()["runs"][0]["status"] == "error"


def test_missing_key_and_connection_errors_do_not_use_the_cap(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None, raising=False)
    assert [post("/api/shipments/SH-1004/analyze").status_code for _ in range(limits.MAX_ANALYSES + 1)] == [503] * (limits.MAX_ANALYSES + 1)
    assert limits.count == 0

    def unreachable(**kw):
        raise APIConnectionError(request=None)
    patch_client(monkeypatch, SimpleNamespace(messages=SimpleNamespace(create=unreachable)))
    assert post("/api/shipments/SH-1004/analyze").status_code == 502
    assert limits.count == 0 and get("/api/runs").get_json()["runs"] == []


def test_cap_blocks_before_any_model_call(monkeypatch):
    monkeypatch.setattr(limits, "count", limits.MAX_ANALYSES)
    monkeypatch.setattr(webapp.api.analysis.agent, "make_client", lambda: (_ for _ in ()).throw(AssertionError("must not run")))
    assert post("/api/shipments/SH-1004/analyze").status_code == 429


def test_on_track_and_unknown_shipments_cannot_be_analyzed():
    assert post("/api/shipments/SH-1001/analyze").status_code == 404
    assert post("/api/shipments/SH-9999/analyze").status_code == 404


def test_disposition_and_feedback_flow(monkeypatch):
    patch_client(monkeypatch, submit_client())
    cid = post("/api/shipments/SH-1004/analyze").get_json()["case_id"]
    assert post(f"/api/cases/{cid}/disposition", {"status": "acknowledged", "owner": "elliot", "note": "on it"}).status_code == 200
    assert post(f"/api/cases/{cid}/disposition", {"status": "nonsense"}).status_code == 400
    assert post(f"/api/cases/{cid}/feedback", {"verdict": "useful"}).status_code == 200
    assert post(f"/api/cases/{cid}/feedback", {"verdict": "great"}).status_code == 400
    detail = get(f"/api/cases/{cid}").get_json()
    assert detail["case"]["disposition"]["status"] == "acknowledged" and len(detail["feedback"]) == 1
    assert get("/api/cases?status=acknowledged").get_json()["cases"][0]["id"] == cid
    assert get("/api/cases?status=resolved").get_json()["cases"] == []


def test_saved_case_becomes_stale_when_the_clock_moves(monkeypatch):
    patch_client(monkeypatch, submit_client())
    post("/api/shipments/SH-1004/analyze")
    assert get("/api/cases").get_json()["cases"][0]["stale"] is False
    sim.advance(24)
    assert get("/api/cases").get_json()["cases"][0]["stale"] is True
    sim.reset()


def test_run_status_turns_worsened_after_the_clock_moves(monkeypatch):
    patch_client(monkeypatch, submit_client())
    post("/api/shipments/SH-1004/analyze")
    sim.advance(24)
    patch_client(monkeypatch, submit_client())
    post("/api/shipments/SH-1004/analyze")
    statuses = [c["run_status"] for c in get("/api/cases").get_json()["cases"]]
    assert statuses == ["WORSENED", "NEW"]
