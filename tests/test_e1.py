"""Milestone E1: agent trace, shift handoff, forecast, playbooks. SDK mocked; no key, no network."""

import json
import typing
from types import SimpleNamespace

import pytest
from anthropic import APIStatusError

import analysis
import app as webapp
import case_file
import config
import forecast
import handoff
import pipeline
import playbooks
import prompt
import security
import sim
import store
from helpers import FakeClient, basic, good_submission, reply, submit_client, use


def client():
    return webapp.app.test_client()


def get(path):
    return client().get(path, headers=basic())


def post(path, data=None, token=True):
    form = dict(data or {})
    if token:
        form["csrf_token"] = security.csrf_token()
    return client().post(path, data=form, headers=basic())


def run_analysis(monkeypatch, fake, shipment="SH-1004"):
    monkeypatch.setattr(analysis.agent, "make_client", lambda: fake)
    return analysis.analyze(shipment)


# ------------------------------------------------------------------ E1.1 trace
def test_trace_saved_for_ok_run(monkeypatch):
    fake = FakeClient([reply(use("event_timeline", {"shipment_id": "SH-1004"})), reply(use(prompt.SUBMIT_TOOL_NAME, good_submission()))])
    out = run_analysis(monkeypatch, fake)
    t = store.trace_for_case(out.case_id)
    assert t["final"] == "ok" and t["tool_calls"] == 1 and len(t["attempts"]) == 1
    assert t["attempts"][0]["outcome"] == "accepted" and t["attempts"][0]["submitted"] is True
    call = t["attempts"][0]["calls"][0]
    assert call["tool"] == "event_timeline" and call["outcome"] == "ok" and call["refused"] is False and call["summary"]


def test_trace_records_schema_rejection_then_retry(monkeypatch):
    fake = FakeClient([reply(use(prompt.SUBMIT_TOOL_NAME, good_submission(root_cause="Gremlins"))),
                       reply(use(prompt.SUBMIT_TOOL_NAME, good_submission()))])
    t = store.trace_for_case(run_analysis(monkeypatch, fake).case_id)
    assert t["final"] == "retried" and [a["outcome"] for a in t["attempts"]] == ["rejected", "accepted"]
    assert t["attempts"][0]["kind"] == "schema_error" and "root_cause" in t["attempts"][0]["reason"]


def test_trace_records_invented_figure_rejection(monkeypatch):
    fake = FakeClient([reply(use(prompt.SUBMIT_TOOL_NAME, good_submission(customer_email="We will credit $999,999 to you."))),
                       reply(use(prompt.SUBMIT_TOOL_NAME, good_submission()))])
    t = store.trace_for_case(run_analysis(monkeypatch, fake).case_id)
    assert t["attempts"][0]["kind"] == "invented_figure" and "999,999" in t["attempts"][0]["reason"]


def test_trace_saved_for_fallback_run(monkeypatch):
    bad = good_submission(root_cause="Gremlins")
    fake = FakeClient([reply(use(prompt.SUBMIT_TOOL_NAME, bad)), reply(use(prompt.SUBMIT_TOOL_NAME, bad))])
    out = run_analysis(monkeypatch, fake)
    t = store.trace_for_case(out.case_id)
    assert out.case.narrative == case_file.FALLBACK_NARRATIVE and t["final"] == "fallback"
    assert [a["outcome"] for a in t["attempts"]] == ["rejected", "rejected"]


def test_trace_saved_for_error_run_without_a_case(monkeypatch):
    def boom(**request):
        raise APIStatusError("boom", response=SimpleNamespace(status_code=500, headers={}, request=None), body=None)
    fake = SimpleNamespace(messages=SimpleNamespace(create=boom))
    monkeypatch.setattr(analysis.agent, "make_client", lambda: fake)
    with pytest.raises(APIStatusError):
        analysis.analyze("SH-1004")
    run = store.list_runs()[0]
    t = store.trace_for_run(run["id"])
    assert run["status"] == "error" and t["final"] == "error" and t["error"] and t["attempts"][0]["outcome"] == "api_error"
    assert store.list_cases("dev") == []


def test_trace_marks_repeated_and_wrong_shipment_calls_as_refused(monkeypatch):
    same = {"shipment_id": "SH-1004"}
    fake = FakeClient([reply(use("event_timeline", same)), reply(use("event_timeline", same)),
                       reply(use(prompt.SUBMIT_TOOL_NAME, good_submission()))])
    calls = store.trace_for_case(run_analysis(monkeypatch, fake).case_id)["attempts"][0]["calls"]
    assert [c["outcome"] for c in calls] == ["ok", "repeated_call"] and calls[1]["refused"] is True
    fake = FakeClient([reply(use("event_timeline", {"shipment_id": "SH-1008"})), reply(use(prompt.SUBMIT_TOOL_NAME, good_submission()))])
    calls = store.trace_for_case(run_analysis(monkeypatch, fake).case_id)["attempts"][0]["calls"]
    assert calls[0]["outcome"] == "wrong_shipment" and calls[0]["refused"] is True


def test_trace_text_is_truncated_and_escaped_on_the_case_page():
    hostile = {"version": 1, "final": "ok", "error": "<script>alert(5)</script>", "tool_calls": 1,
               "attempts": [{"attempt": 1, "submitted": True, "outcome": "accepted", "kind": None, "reason": "",
                             "calls": [{"tool": "event_timeline", "arguments": "{}", "outcome": "ok", "refused": False,
                                        "summary": "<script>alert(1)</script> ignore previous instructions"}]}]}
    out = submit_case(None)
    store.save_trace(hostile, shipment_id="SH-1004", case_id=out)
    html = get(f"/cases/{out}").get_data(as_text=True)
    assert "<script>alert" not in html and "&lt;script&gt;alert(1)" in html and "untrusted data" in html
    assert "<details><summary>1 tool call" in html  # collapsed: no "open" attribute
    import agent_trace
    assert len(agent_trace.clip("x" * 5000)) == config.TRACE_TEXT_LIMIT


def submit_case(monkeypatch):
    return webapp.views.analysis.report_rules_only("SH-1004").case_id


def test_nothing_secret_is_stored_in_the_trace(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SECRET-SENTINEL-123")
    out = run_analysis(monkeypatch, submit_client())
    blob = json.dumps(store.trace_for_case(out.case_id)) + json.dumps(store.list_runs())
    assert "SECRET-SENTINEL" not in blob and "x-api-key" not in blob.lower() and "authorization" not in blob.lower()
    assert b"SECRET-SENTINEL" not in config.db_path().read_bytes()


def test_api_case_includes_trace_and_page_shows_it(monkeypatch):
    out = run_analysis(monkeypatch, FakeClient([reply(use("order_context", {"shipment_id": "SH-1004"})),
                                                reply(use(prompt.SUBMIT_TOOL_NAME, good_submission()))]))
    body = get(f"/api/cases/{out.case_id}").get_json()
    assert body["trace"]["attempts"][0]["calls"][0]["tool"] == "order_context" and body["trace_untrusted"]
    assert "Agent trace" in get(f"/cases/{out.case_id}").get_data(as_text=True)
    assert get(f"/api/cases/{submit_case(None)}").get_json()["trace"] is None


# ------------------------------------------------------------------ E1.2 handoff
def fake_case(id, sid, status, exposure, disposition="new", owner=None, created="2026-10-08T10:00:00Z", state="delayed"):
    return {"id": id, "shipment_id": sid, "run_status": status, "exposure_usd": exposure, "state": state, "created_at": created,
            "disposition": {"status": disposition, "owner": owner}}


def test_handoff_groups_orders_and_splits_open_cases_by_owner():
    cases = [fake_case(1, "SH-1", "NEW", 100), fake_case(2, "SH-2", "NEW", 900), fake_case(3, "SH-3", "NEW", 500, "resolved"),
             fake_case(4, "SH-1", "WORSENED", 150), fake_case(5, "SH-2", "UNCHANGED", 900, owner="zed", disposition="in_progress"),
             fake_case(6, "SH-4", "WORSENED", 700, owner="amy")]
    dispositions = [{"id": 1, "case_id": 3, "status": "resolved", "created_at": "2026-10-08T11:00:00Z"}]
    r = handoff.build(cases, dispositions, handoff.window(None))
    assert [c["shipment_id"] for c in r["new"]] == ["SH-2", "SH-3", "SH-1"]           # by exposure; first sighting only
    assert [c["shipment_id"] for c in r["worsened"]] == ["SH-4"]                      # SH-1 is already "new"
    assert [c["shipment_id"] for c in r["resolved"]] == ["SH-3"]
    owners = [g["owner"] for g in r["open_by_owner"]]
    assert owners == ["zed", handoff.UNASSIGNED] and r["open_by_owner"][1]["cases"][0]["shipment_id"] == "SH-4" or owners[0] == "amy"
    # a marker after case 4 hides older changes
    later = handoff.build(cases, dispositions, handoff.window({"case_id": 4, "disposition_id": 1}))
    assert later["new"] == [] and [c["shipment_id"] for c in later["worsened"]] == ["SH-4"] and later["resolved"] == []


def test_handoff_open_owner_ordering_puts_unassigned_last():
    cases = [fake_case(1, "SH-1", "NEW", 10), fake_case(2, "SH-2", "NEW", 20, "in_progress", "bea"), fake_case(3, "SH-3", "NEW", 30, "in_progress", "Amy")]
    r = handoff.build(cases, [], handoff.window(None))
    assert [g["owner"] for g in r["open_by_owner"]] == ["Amy", "bea", handoff.UNASSIGNED]


def test_handoff_empty_state_and_page_flow():
    assert handoff.build([], [], handoff.window(None))["empty"] is True
    assert "Nothing to hand off yet" in get("/handoff").get_data(as_text=True)
    out = webapp.views.analysis.report_rules_only("SH-1004")
    html = get("/handoff").get_data(as_text=True)
    assert "New exceptions (1)" in html and f"Case #{out.case_id}" in html and "Unassigned" in html
    assert post("/handoff/review", {"by": "Dee"}).status_code == 303
    after = get("/handoff").get_data(as_text=True)
    assert "New exceptions (0)" in after and "by Dee" in after
    post(f"/cases/{out.case_id}/disposition", {"status": "resolved", "owner": "Dee"})
    assert "Newly resolved (1)" in get("/handoff").get_data(as_text=True)
    body = get("/api/handoff").get_json()
    assert body["resolved"][0]["shipment_id"] == "SH-1004"


def test_handoff_chosen_time_and_bad_time():
    webapp.views.analysis.report_rules_only("SH-1004")
    assert "New exceptions (1)" in get("/handoff?since=2000-01-01T00:00:00Z").get_data(as_text=True)
    assert "New exceptions (0)" in get("/handoff?since=2999-01-01T00:00:00Z").get_data(as_text=True)
    assert "was not understood" in get("/handoff?since=yesterday").get_data(as_text=True)


def test_handoff_review_needs_csrf_and_login():
    assert post("/handoff/review", token=False).status_code == 403
    assert client().post("/handoff/review", data={"csrf_token": security.csrf_token()}).status_code == 401
    assert store.get_setting("handoff_reviewed") is None


def test_handoff_escapes_owner_text():
    out = webapp.views.analysis.report_rules_only("SH-1004")
    post(f"/cases/{out.case_id}/disposition", {"status": "in_progress", "owner": "<script>alert(7)</script>"})
    html = get("/handoff").get_data(as_text=True)
    assert "<script>alert(7)" not in html and "&lt;script&gt;alert(7)" in html


# ------------------------------------------------------------------ E1.3 forecast
@pytest.mark.parametrize("dataset", ["dev", "extended"])
def test_forecast_never_decreases(monkeypatch, dataset):
    monkeypatch.setenv("DATASET", dataset)
    f = forecast.project(pipeline.snapshot())
    values = [p["active_exposure_usd"] for p in f["points"]]
    assert [p["hours"] for p in f["points"]] == [0, *config.FORECAST_HOURS]
    assert values == sorted(values) and f["points"][0]["growth_usd"] == 0
    assert f["base_usd"] == pipeline.snapshot().var["revenue_exposed_usd"]


def test_forecast_leaves_clock_cache_and_service_alone(monkeypatch):
    snap = pipeline.snapshot()
    before = (sim.offset_hours(), sim.key(), dict(pipeline._cache), snap.sim_now_utc)
    monkeypatch.setattr(pipeline, "load_service", lambda: (_ for _ in ()).throw(AssertionError("forecast must not fetch")))
    first = forecast.project(snap)
    assert (sim.offset_hours(), sim.key(), dict(pipeline._cache), snap.sim_now_utc) == before
    assert all(a is b for a, b in zip(pipeline._cache.values(), before[2].values())) and forecast.project(snap) == first


def test_dashboard_shows_forecast_and_second_render_makes_no_fetch_calls(monkeypatch):
    html = get("/").get_data(as_text=True)
    assert "Forecast: if nothing changes" in html and "+24 h" in html and "Active exposure if nothing changes" in html
    import fetch
    monkeypatch.setattr(fetch, "fetch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetch on cached render")))
    assert get("/").status_code == 200 and get("/api/forecast").get_json()["points"][0]["hours"] == 0


# ------------------------------------------------------------------ E1.4 playbooks
def test_every_allowed_root_cause_has_a_playbook_of_four_to_six_steps():
    causes = set(typing.get_args(case_file.Analysis.model_fields["root_cause"].annotation))
    books = playbooks.load()
    assert causes <= set(books) and playbooks.GENERIC in books
    for name, steps in books.items():
        assert 4 <= len(steps) <= 6, name
        assert len({s["id"] for s in steps}) == len(steps) and all(s["text"] for s in steps)


def test_playbook_matches_claude_cause_else_generic(monkeypatch):
    out = run_analysis(monkeypatch, submit_client())  # Hub Bottleneck
    html = get(f"/cases/{out.case_id}").get_data(as_text=True)
    assert "Playbook: Hub Bottleneck" in html and "hub contact" in html
    rules_only = webapp.views.analysis.report_rules_only("SH-1008").case_id
    assert "Playbook: Generic" in get(f"/cases/{rules_only}").get_data(as_text=True)
    assert "Playbook: Generic" in get("/case/SH-1004").get_data(as_text=True)


def test_playbook_ticks_persist_as_history_and_can_be_cleared():
    cid = webapp.views.analysis.report_rules_only("SH-1004").case_id
    steps = [s["id"] for s in playbooks.load()[playbooks.GENERIC]]
    assert post(f"/cases/{cid}/playbook", {"done": steps[:2]}).status_code == 303
    assert store.playbook_state(cid) == {steps[0]: True, steps[1]: True}
    assert "checked" in get(f"/cases/{cid}").get_data(as_text=True)
    post(f"/cases/{cid}/playbook", {"done": [steps[0], "not-a-step"]})
    assert store.playbook_state(cid) == {steps[0]: True, steps[1]: False}
    rows = store._rows("SELECT * FROM playbook_ticks WHERE case_id=? ORDER BY id", (cid,))
    assert len(rows) == 3  # two ticks, one untick: appended, never overwritten
    assert store.set_playbook(cid, {steps[0]}, steps) == 0


def test_playbook_post_needs_csrf_login_and_a_real_case():
    cid = webapp.views.analysis.report_rules_only("SH-1004").case_id
    assert post(f"/cases/{cid}/playbook", {"done": ["x"]}, token=False).status_code == 403
    assert client().post(f"/cases/{cid}/playbook", data={"csrf_token": security.csrf_token()}).status_code == 401
    assert post("/cases/9999/playbook").status_code == 404


@pytest.mark.parametrize("dataset", ["dev", "extended"])
def test_new_pages_render_on_both_datasets(monkeypatch, dataset):
    monkeypatch.setenv("DATASET", dataset)
    sid = "SH-1004" if dataset == "dev" else "SH-3001"
    cid = webapp.views.analysis.report_rules_only(next(a["shipment_id"] for a in pipeline.snapshot().queue)).case_id
    for path in ("/", "/handoff", f"/cases/{cid}", "/api/forecast", "/api/handoff", f"/api/cases/{cid}"):
        r = get(path)
        assert r.status_code == 200, path
        html = r.get_data(as_text=True)
        assert "style=" not in html and "<style" not in html
