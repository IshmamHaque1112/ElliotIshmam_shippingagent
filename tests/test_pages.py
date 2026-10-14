import re

import pytest

import app as webapp
import limits
import security
import sim
import store
from helpers import FakeClient, basic, good_submission, reply, submit_client, use
import prompt

PAGES = ["/", "/shipments", "/queue", "/queue.csv", "/cases", "/runs", "/eval", "/carriers", "/digest", "/simulate",
         "/shipments/SH-1004", "/shipments/SH-1001", "/shipments/SH-1010", "/shipments/SH-1011", "/case/SH-1004", "/case/SH-1010"]


def client():
    return webapp.app.test_client()


def get(path):
    return client().get(path, headers=basic())


def post(path, data=None, token=True):
    form = dict(data or {})
    if token:
        form["csrf_token"] = security.csrf_token()
    return client().post(path, data=form, headers=basic())


def analyze_with(monkeypatch, client_obj, shipment="SH-1004"):
    monkeypatch.setattr(webapp.views.analysis.agent, "make_client", lambda: client_obj)
    return post(f"/case/{shipment}/analyze")


@pytest.mark.parametrize("dataset", ["dev", "extended"])
def test_every_page_renders_on_both_datasets(monkeypatch, dataset):
    monkeypatch.setenv("DATASET", dataset)
    for path in PAGES + (["/shipments/SH-3001", "/shipments?page=2&state=delayed"] if dataset == "extended" else []):
        if dataset == "extended" and path.startswith(("/shipments/SH-10", "/case/SH-10")):
            continue
        r = get(path)
        assert r.status_code == 200, (path, r.status_code)


def test_pages_use_no_inline_styles_or_external_assets():
    for path in ("/", "/shipments/SH-1004", "/queue", "/case/SH-1004", "/simulate"):
        html = get(path).get_data(as_text=True)
        assert "style=" not in html and "<style" not in html, path
        assert not re.search(r'(src|href)="https?://', html), path
    r = get("/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"] and r.headers["X-Content-Type-Options"] == "nosniff"
    assert "/static/app.css" in r.get_data(as_text=True) and get("/static/app.css").status_code == 200


def test_static_files_also_need_the_login():
    assert client().get("/static/app.css").status_code == 401


def test_dashboard_shows_value_at_risk_and_ranked_top_rows():
    html = get("/").get_data(as_text=True)
    assert "Revenue exposed" in html and "$37,575.00" in html and html.index("SH-1004") < html.index("SH-1008")
    assert "Analyze the top exceptions" in html and "Not analyzed" in html


def test_deliveries_page_lists_everything_with_filters_and_paging(monkeypatch):
    monkeypatch.setenv("DATASET", "extended")
    html = get("/shipments").get_data(as_text=True)
    assert "60 shipments match" in html and "Page 1 of 3" in html and "page=2" in html
    assert "SH-3001" in get("/shipments?q=SH-3001").get_data(as_text=True)
    assert "No shipments match" in get("/shipments?q=zzzz").get_data(as_text=True)
    only = get("/shipments?state=on_track&page_size=100").get_data(as_text=True)
    assert "14 shipments match" in only


def test_shipment_detail_has_timeline_charts_and_marks_carrier_text_untrusted():
    html = get("/shipments/SH-1012").get_data(as_text=True)
    assert "Untrusted carrier text" in html and "authoritative latest" in html and "<svg" in html
    assert "ETA history vs promise" in html and "Penalty clock" in html and "Raw tool data" in html
    assert "duplicate scan(s) collapsed" in get("/shipments/SH-1011").get_data(as_text=True)


def test_unable_to_verify_detail_does_not_present_status_as_fact():
    html = get("/shipments/SH-1010").get_data(as_text=True)
    assert "Unable to verify" in html and "Not available" in html and "503" in html


def test_what_if_is_computed_by_code_and_matches_the_ranking():
    html = get("/shipments/SH-1004?option=PRIORITY_AIR").get_data(as_text=True)
    assert "net benefit $6,337.50" in html and "Computed by code" in html
    assert "What if" in get("/shipments/SH-1004?option=NOPE").get_data(as_text=True)


def test_queue_csv_is_protected_against_formula_injection():
    from views import _safe_cell
    assert _safe_cell("=HYPERLINK(1)") == "'=HYPERLINK(1)" and _safe_cell("+1") == "'+1" and _safe_cell("Acme") == "Acme" and _safe_cell(5) == 5
    body = get("/queue.csv").get_data(as_text=True)
    assert body.splitlines()[0].startswith("rank,shipment_id") and body.splitlines()[1].split(",")[1] == "SH-1004"


def test_case_page_is_rules_only_without_saving_or_calling_claude(monkeypatch):
    monkeypatch.setattr(webapp.views.analysis.agent, "make_client", lambda: (_ for _ in ()).throw(AssertionError("no API call")))
    html = get("/case/SH-1004").get_data(as_text=True)
    assert "Live rules-only view" in html and "37,575.00" in html and "No drafts in rules-only mode" in html
    assert store.list_cases("dev") == [] and get("/case/SH-1001").status_code == 404 and get("/case/SH-9999").status_code == 404


def test_save_rules_only_case_then_manage_it():
    r = post("/case/SH-1004/report")
    assert r.status_code == 303
    cid = int(r.headers["Location"].rsplit("/", 1)[1])
    html = get(f"/cases/{cid}").get_data(as_text=True)
    assert "Fallback case file" in html and "Disposition" in html and "stale" not in html.lower().split("disposition")[0]
    assert post(f"/cases/{cid}/disposition", {"status": "in_progress", "owner": "ishmam", "note": "calling carrier"}).status_code == 303
    assert "in progress" in get(f"/cases/{cid}").get_data(as_text=True) and "calling carrier" in get(f"/cases/{cid}").get_data(as_text=True)
    assert post(f"/cases/{cid}/disposition", {"status": "bogus"}).status_code == 400
    assert post(f"/cases/{cid}/feedback", {"verdict": "wrong", "note": "bad cause"}).status_code == 303
    assert "bad cause" in get(f"/cases/{cid}").get_data(as_text=True)
    assert "#%d" % cid in get("/cases").get_data(as_text=True) and "in_progress" in get("/cases?status=in_progress").get_data(as_text=True)
    assert "No saved cases yet" in get("/cases?status=resolved").get_data(as_text=True)


def test_claude_case_redirects_to_saved_page_with_readonly_drafts(monkeypatch):
    r = analyze_with(monkeypatch, submit_client())
    assert r.status_code == 303
    html = get(r.headers["Location"]).get_data(as_text=True)
    assert html.count("readonly") >= 2 and "Hub Bottleneck" in html and "Re-analyze with Claude" in html and "Fallback case file" not in html
    assert limits.count == 1 and "ok" in get("/runs").get_data(as_text=True)


def test_stale_and_fallback_banners(monkeypatch):
    cid = int(post("/case/SH-1004/report").headers["Location"].rsplit("/", 1)[1])
    sim.advance(24)
    html = get(f"/cases/{cid}").get_data(as_text=True)
    assert "Stale." in html and "Fallback case file" in html and "stale" in get("/cases").get_data(as_text=True)


def test_model_text_is_escaped_on_every_page(monkeypatch):
    evil = good_submission(timeline_summary="<script>alert(1)</script>", unknowns=["<img src=x onerror=alert(2)>"],
                           customer_email="</textarea><script>alert(3)</script> SH-1004")
    r = analyze_with(monkeypatch, FakeClient([reply(use(prompt.SUBMIT_TOOL_NAME, evil))]))
    for path in (r.headers["Location"], "/cases", "/runs", "/"):
        html = get(path).get_data(as_text=True)
        assert "<script>alert" not in html and "<img src=x" not in html, path
    assert "&lt;script&gt;alert(1)" in get(r.headers["Location"]).get_data(as_text=True)


def test_carrier_text_is_escaped_on_the_detail_page(monkeypatch):
    import tools
    original = tools.event_timeline

    def poisoned(ds, sid):
        out = original(ds, sid)
        if sid == "SH-1004":
            out = dict(out, events=[dict(e, carrier_note="<script>alert(9)</script>") for e in out["events"]])
        return out
    monkeypatch.setitem(tools.TOOLS, "event_timeline", poisoned)
    html = get("/shipments/SH-1004").get_data(as_text=True)
    assert "<script>alert(9)" not in html and "&lt;script&gt;alert(9)" in html


def test_posts_need_csrf_and_login():
    for path in ("/case/SH-1004/analyze", "/case/SH-1004/report", "/analyze-top", "/simulate/advance", "/simulate/reset", "/simulate/fault"):
        assert post(path, token=False).status_code == 403, path
        assert client().post(path, data={"csrf_token": "x"}).status_code == 401
    assert post("/cases/1/disposition", {"status": "resolved"}, token=False).status_code == 403


def test_cap_missing_key_and_connection_behaviour_in_the_ui(monkeypatch):
    monkeypatch.setattr(limits, "count", limits.MAX_ANALYSES)
    monkeypatch.setattr(webapp.views.analysis.agent, "make_client", lambda: (_ for _ in ()).throw(AssertionError("must not run")))
    r = post("/case/SH-1004/analyze")
    assert r.status_code == 429 and b"analysis limit" in r.data and b"rules-only case file" in r.data
    monkeypatch.setattr(limits, "count", 0)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None, raising=False)
    monkeypatch.undo() if False else None
    from api_errors import MissingKeyError
    def no_key():
        raise MissingKeyError("ANTHROPIC_API_KEY is missing.")
    monkeypatch.setattr(webapp.views.analysis.agent, "make_client", no_key)
    for _ in range(limits.MAX_ANALYSES + 1):
        assert post("/case/SH-1004/analyze").status_code == 200
    assert limits.count == 0


def test_analyze_top_skips_fresh_cases_and_respects_the_cap(monkeypatch):
    monkeypatch.setattr(webapp.views.analysis.agent, "make_client", lambda: FakeClient([reply(use(prompt.SUBMIT_TOOL_NAME, good_submission(), str(i))) for i in range(4)]))
    r = post("/analyze-top", {"n": "3"})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Batch analysis" in html and limits.count == 3
    assert len(store.list_cases("dev")) == 3
    r = post("/analyze-top", {"n": "1"})   # the next exception that has no fresh Claude case
    assert limits.count == 4
    assert post("/analyze-top", {"n": "abc"}).status_code == 400


def test_simulation_advance_reset_and_faults_change_the_queue_and_persist():
    before = get("/queue").get_data(as_text=True)
    assert post("/simulate/advance", {"hours": "24"}).status_code == 303 and sim.offset_hours() == 24
    assert "+24" in get("/simulate").get_data(as_text=True)
    assert store.get_setting("sim")["offset_hours"] == 24
    assert post("/simulate/advance", {"hours": "-5"}).status_code == 400 and post("/simulate/advance", {"hours": "x"}).status_code == 400
    post("/simulate/reset")
    assert sim.offset_hours() == 0 and get("/queue").get_data(as_text=True) == before
    post("/simulate/fault", {"shipment_id": "SH-1001", "tool": "get_tracking_status", "fault": "http_503"})
    assert "Unable to verify" in get("/shipments/SH-1001").get_data(as_text=True)
    post("/simulate/fault", {"shipment_id": "SH-1010", "tool": "get_tracking_status", "fault": "off"})
    assert "Unable to verify" not in get("/shipments/SH-1010").get_data(as_text=True)
    assert post("/simulate/fault", {"shipment_id": "SH-9999", "tool": "x", "fault": "http_503"}).status_code == 400
    post("/simulate/reset")
    assert "Unable to verify" in get("/shipments/SH-1010").get_data(as_text=True)


def test_saved_simulation_state_is_restored_for_a_new_process():
    post("/simulate/advance", {"hours": "6"})
    sim.reset()
    webapp._loaded_for = None
    get("/")
    assert sim.offset_hours() == 6


def test_advancing_the_clock_never_improves_a_state():
    order = {"on_track": 0, "at_risk": 1, "unable_to_verify": 1, "delayed": 2, "delivered_late": 2}
    import pipeline
    base = {r["shipment_id"]: (order[r["state"]], r["exposure_usd"] or 0) for r in pipeline.snapshot().rows}
    for hours in (6, 24, 72):
        sim.advance(hours)
        for r in pipeline.snapshot().rows:
            assert order[r["state"]] >= base[r["shipment_id"]][0], (hours, r["shipment_id"])
            assert (r["exposure_usd"] or 0) >= base[r["shipment_id"]][1] - 0.01, (hours, r["shipment_id"])


def test_second_render_of_the_queue_does_no_fetching(monkeypatch):
    get("/queue")
    import fetch
    monkeypatch.setattr(fetch, "fetch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched on a cached render")))
    for path in ("/queue", "/", "/shipments", "/shipments/SH-1004"):
        assert get(path).status_code == 200


def test_cache_invalidates_when_the_simulation_changes():
    import pipeline
    first = pipeline.snapshot()
    assert pipeline.snapshot() is first
    sim.advance(6)
    assert pipeline.snapshot() is not first


def test_carriers_and_digest_pages_have_expected_content():
    html = get("/carriers").get_data(as_text=True)
    assert "Northline Freight" in html and "Exception rate" in html
    d = get("/digest").get_data(as_text=True)
    assert "draft preview; nothing is sent" in d and "Closed late, claim candidates (1)" in d


def test_eval_page_empty_and_with_results(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "ROOT", tmp_path)
    assert "No eval results yet" in get("/eval").get_data(as_text=True)
    (tmp_path / "eval_results.json").write_text('{"summary": {"passed": 3, "failed": 1, "skipped": 0, "mode": "no-llm", "model": null, "dataset": "dev"}, "results": [{"shipment_id": "SH-1", "check": "state", "result": "FAIL", "detail": "<b>x</b>"}]}')
    (tmp_path / "eval_history.jsonl").write_text('{"at": "t", "dataset": "dev", "mode": "no-llm", "model": null, "passed": 3, "failed": 1, "token_usage": null}\n')
    html = get("/eval").get_data(as_text=True)
    assert "Latest results" in html and "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html


def test_csrf_token_is_not_the_same_across_secrets(monkeypatch):
    a = security.csrf_token()
    monkeypatch.setenv("FLASK_SECRET_KEY", "different")
    assert security.csrf_token() != a
