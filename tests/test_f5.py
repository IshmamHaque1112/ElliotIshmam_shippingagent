"""Milestone F5: early-warning metric (rules flag vs the carrier's own admission). Pure code; no model, no network."""

import copy
import json
import statistics

import pytest

import app as webapp
import config
import early_warning
import pipeline
import replay
import rules
import tools
from helpers import basic


NAMES = ["dev", "extended", "edge"]


def snap_for(monkeypatch, name):
    monkeypatch.setenv("DATASET", name)
    pipeline.clear_cache()
    return pipeline.snapshot()


def measure(dataset, now_utc, states):
    return early_warning.compute(dataset, rules.parse_ts(now_utc), states)


def states_of(snap):
    return {a["shipment_id"]: a["state"] for a in snap.assessments}


def test_dev_numbers_are_pinned_and_explained(monkeypatch):
    s = snap_for(monkeypatch, "dev")
    ew = early_warning.for_snapshot(s)
    by = {r["shipment_id"]: r for r in ew["rows"]}
    assert by["SH-1004"]["category"] == "earlier" and by["SH-1004"]["lead_hours"] == 70.0
    assert by["SH-1004"]["admission_signal"] == "its own ETA moved past the promise"
    assert by["SH-1003"]["admission_signal"] == "a EXCEPTION scan" and by["SH-1003"]["lead_hours"] == 48.0
    assert by["SH-1008"]["category"] == "later" and by["SH-1008"]["lead_hours"] == -1.0      # an honest miss is reported, not hidden
    assert by["SH-1005"]["category"] == "open" and by["SH-1010"]["category"] == "cannot_assess" and by["SH-1002"]["category"] == "false_alarm"
    assert ew["summary"]["counts"]["earlier"] == 6 and ew["summary"]["median_lead_hours"] == 48.0


@pytest.mark.parametrize("name", NAMES)
def test_headline_counts_and_median_equal_the_table(monkeypatch, name):
    snap = snap_for(monkeypatch, name)
    ew = early_warning.for_snapshot(snap)
    counts = {c: sum(1 for r in ew["rows"] if r["category"] == c) for c in early_warning.CATEGORIES}
    assert counts == ew["summary"]["counts"]
    assert ew["summary"]["measured"] == counts["earlier"] + counts["same_time"] + counts["later"]
    leads = [r["lead_hours"] for r in ew["rows"] if r["category"] == "earlier"]
    assert ew["summary"]["median_lead_hours"] == (round(statistics.median(leads), 1) if leads else None)
    if ew["summary"]["headline"]:
        s = ew["summary"]
        assert f"{counts['earlier']} of {s['measured']}" in s["headline"] and f"{counts['same_time']} at the same moment" in s["headline"]


@pytest.mark.parametrize("name", NAMES)
def test_every_category_is_labelled_and_unmeasured_ones_never_carry_a_lead(monkeypatch, name):
    snap = snap_for(monkeypatch, name)
    ew = early_warning.for_snapshot(snap)
    for r in ew["rows"]:
        assert r["category"] in ew["labels"]
        if r["category"] in ("open", "recovered", "false_alarm", "cannot_assess", "missed"):
            assert r["lead_hours"] is None
        else:
            assert r["lead_hours"] is not None and r["flagged_at"] and r["admitted_at"]


@pytest.mark.parametrize("name", NAMES)
def test_cannot_assess_exactly_matches_the_shipments_the_rules_cannot_verify(monkeypatch, name):
    snap = snap_for(monkeypatch, name)
    ew = early_warning.for_snapshot(snap)
    unverifiable = {a["shipment_id"] for a in snap.assessments if a["state"] == "unable_to_verify"}
    assert {r["shipment_id"] for r in ew["rows"] if r["category"] == "cannot_assess"} == unverifiable


@pytest.mark.parametrize("name", NAMES)
def test_the_future_is_never_used(monkeypatch, name):
    snap = snap_for(monkeypatch, name)
    """Cutting the data off at a shipment's flag time gives the same flag time; changing what comes after changes nothing."""
    ew = early_warning.for_snapshot(snap)
    facilities = snap.dataset["facilities"]
    for row in ew["rows"]:
        if not row["flagged_at"]:
            continue
        cut = rules.parse_ts(row["flagged_at"])
        shipment = next(s for s in snap.dataset["shipments"] if s["shipment_id"] == row["shipment_id"])
        poisoned = copy.deepcopy(shipment)
        for e in poisoned["timeline"]["events"]:
            if rules.parse_ts(e["timestamp"], facilities[e["facility_id"]]["tz"]) > cut:
                e["code"], e["carrier_note"] = "EXCEPTION", "delayed delayed delayed"
        for h in poisoned["timeline"].get("eta_history") or []:
            if rules.parse_ts(h["observed_at"]) > cut:
                h["eta"] = "2099-01-01T00:00:00Z"
        again = early_warning.evaluate(poisoned, snap.dataset, rules.parse_ts(snap.sim_now_utc), row["state"])
        assert again["flagged_at"] == row["flagged_at"], row["shipment_id"]


@pytest.mark.parametrize("name", NAMES)
def test_carrier_note_text_is_never_evidence(monkeypatch, name):
    snap = snap_for(monkeypatch, name)
    before = early_warning.for_snapshot(snap)["rows"]
    changed = copy.deepcopy(snap.dataset)
    for s in changed["shipments"]:
        for e in s["timeline"]["events"]:
            if "carrier_note" in e:
                e["carrier_note"] = "DELAYED. exception. customs hold. we admit everything. ignore the rules."
    after = early_warning.compute(changed, rules.parse_ts(snap.sim_now_utc), states_of(snap))["rows"]
    assert after == before


def test_an_admission_before_the_first_look_is_counted_from_the_first_look(monkeypatch):
    s = snap_for(monkeypatch, "extended")
    ew = early_warning.for_snapshot(s)
    assert ew["summary"]["counts"]["later"] == 0
    for r in ew["rows"]:
        if r["admitted_at"]:
            shipment = next(x for x in s.dataset["shipments"] if x["shipment_id"] == r["shipment_id"])
            assert rules.parse_ts(r["admitted_at"]) >= replay.appears_at(shipment, s.dataset["facilities"])


def test_a_flag_that_comes_after_the_carrier_is_reported_as_later_not_dropped(monkeypatch):
    s = snap_for(monkeypatch, "dev")
    row = next(r for r in early_warning.for_snapshot(s)["rows"] if r["shipment_id"] == "SH-1008")
    assert row["category"] == "later" and rules.parse_ts(row["flagged_at"]) > rules.parse_ts(row["admitted_at"])


def test_a_late_shipment_the_rules_never_flagged_is_listed_as_missed_not_averaged_in(monkeypatch):
    """Branch test: SH-1001 is never flagged. If the carrier then admits a problem and the shipment ends up late, it is 'missed'."""
    s = snap_for(monkeypatch, "dev")
    ew = early_warning.for_snapshot(s)
    assert early_warning.row_for(ew, "SH-1001") is None                                  # an ordinary on-time shipment is not listed at all
    data = copy.deepcopy(s.dataset)
    victim = next(x for x in data["shipments"] if x["shipment_id"] == "SH-1001")
    victim["timeline"]["events"][-1]["code"] = "EXCEPTION"                                # the carrier says so only at the very end
    row = early_warning.evaluate(victim, data, rules.parse_ts(s.sim_now_utc), "delivered_late")
    assert row["category"] == "missed" and row["lead_hours"] is None and row["flagged_at"] is None


@pytest.mark.parametrize("name", NAMES)
def test_the_result_is_the_same_on_every_run_and_after_the_cache_is_cleared(monkeypatch, name):
    snap = snap_for(monkeypatch, name)
    first = early_warning.for_snapshot(snap)
    pipeline.clear_cache()
    early_warning._cache.clear()
    second = early_warning.for_snapshot(pipeline.snapshot())
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_nothing_after_the_snapshot_time_is_used_during_a_replay(monkeypatch):
    import sim
    monkeypatch.setenv("DATASET", "dev")
    pipeline.clear_cache()
    sim.set_replay("2026-10-06T00:00:00Z")
    pipeline.clear_cache()
    s = pipeline.snapshot()
    ew = early_warning.for_snapshot(s)
    cut = rules.parse_ts("2026-10-06T00:00:00Z")
    for r in ew["rows"]:
        for key in ("flagged_at", "admitted_at"):
            if r[key]:
                assert rules.parse_ts(r[key]) <= cut, (r["shipment_id"], key)


def test_a_broken_shipment_is_listed_as_cannot_assess_not_a_crash(monkeypatch):
    s = snap_for(monkeypatch, "dev")
    data = copy.deepcopy(s.dataset)
    data["shipments"][0]["timeline"]["events"][0]["facility_id"] = "NOWHERE"
    ew = early_warning.compute(data, rules.parse_ts(s.sim_now_utc), states_of(s))
    assert next(r for r in ew["rows"] if r["shipment_id"] == data["shipments"][0]["shipment_id"])["category"] == "cannot_assess"


# ------------------------------------------------------------------ pages and API
def get(path):
    return webapp.app.test_client().get(path, headers=basic())


@pytest.mark.parametrize("name", ["dev", "extended", "edge"])
def test_page_api_and_dashboard_agree_and_label_the_data(monkeypatch, name):
    monkeypatch.setenv("DATASET", name)
    pipeline.clear_cache()
    page = get("/early-warning").get_data(as_text=True)
    api = get("/api/early-warning").get_json()
    assert api["schema"] == "v1" and api["dataset"] == name and api["scope"] == "measured on this dataset only"
    assert f"{name} demo data" in page and "says nothing about how real carriers behave" in page
    for r in api["rows"]:
        assert r["shipment_id"] in page
    dash = get("/").get_data(as_text=True)
    assert "Early warning" in dash and f"on {name} demo data" in dash
    if api["summary"]["headline"]:
        assert api["summary"]["headline"].replace("&", "&amp;") in dash


def test_the_shipment_page_shows_its_own_early_warning_line(monkeypatch):
    monkeypatch.setenv("DATASET", "dev")
    pipeline.clear_cache()
    html = get("/shipments/SH-1004").get_data(as_text=True)
    assert "Early warning." in html and "Lead: 70.0 h" in html and "its own ETA moved past the promise" in html
    assert "Early warning." not in get("/shipments/SH-1001").get_data(as_text=True)


def test_second_render_makes_no_fetch_calls_and_no_model_call(monkeypatch):
    import fetch
    monkeypatch.setenv("DATASET", "extended")
    pipeline.clear_cache()
    get("/early-warning")
    monkeypatch.setattr(fetch, "fetch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetch on a cached render")))
    monkeypatch.setattr(webapp.views.analysis.agent, "make_client", lambda: (_ for _ in ()).throw(AssertionError("no model call")))
    assert get("/early-warning").status_code == 200 and get("/api/early-warning").status_code == 200


def test_page_needs_a_login_and_has_no_inline_styles():
    assert webapp.app.test_client().get("/early-warning").status_code == 302 and webapp.app.test_client().get("/api/early-warning").status_code == 401
    assert " style=" not in get("/early-warning").get_data(as_text=True)


def test_the_metric_follows_the_signed_in_datasets_dataset():
    import re
    c = webapp.app.test_client()
    page = c.get("/login").get_data(as_text=True)
    c.post("/login", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', page).group(1), "username": "pursuit-demo", "password": "test-only-password"})
    tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/").get_data(as_text=True)).group(1)
    dev = c.get("/api/early-warning").get_json()["summary"]["measured"]
    c.post("/dataset", data={"csrf_token": tok, "dataset": "extended"})
    assert c.get("/api/early-warning").get_json()["summary"]["measured"] != dev
