import json

import pytest

import store


def fake_case(sid="SH-1004", state="delayed", exposure=100.0):
    return {"shipment_id": sid, "state": state, "severity": "LOW", "narrative": "claude", "run_status": "NEW",
            "financial": {"total_exposure_usd": exposure, "priority_score": exposure}}


def save(**kw):
    return store.save_case(fake_case(**kw), dataset="dev", sim_now_utc="2026-10-08T14:00:00Z", input_hash="abc", model="m")


def test_cases_are_appended_never_overwritten():
    first, second = save(), save(exposure=500.0)
    assert second > first
    assert store.get_case(first)["exposure_usd"] == 100.0
    assert store.latest_case_for("SH-1004", "dev")["id"] == second
    assert [c["id"] for c in store.cases_for("SH-1004", "dev")] == [second, first]
    assert store.get_case(first)["case"]["shipment_id"] == "SH-1004"


def test_dispositions_default_to_new_and_keep_history():
    cid = save()
    assert store.list_cases("dev")[0]["disposition"]["status"] == "new"
    store.add_disposition(cid, "acknowledged", "elliot", "looking")
    store.add_disposition(cid, "resolved", "elliot", "fixed")
    assert store.list_cases("dev")[0]["disposition"]["status"] == "resolved"
    assert len(store.disposition_history(cid)) == 2
    assert [c["id"] for c in store.list_cases("dev", status="resolved")] == [cid]
    assert store.list_cases("dev", status="new") == []
    with pytest.raises(ValueError):
        store.add_disposition(cid, "bogus")


def test_feedback_and_settings_round_trip():
    cid = save()
    store.add_feedback(cid, "useful", "nice")
    assert store.feedback_for(cid)[0]["verdict"] == "useful"
    with pytest.raises(ValueError):
        store.add_feedback(cid, "meh")
    store.set_setting("sim", {"offset_hours": 6})
    store.set_setting("sim", {"offset_hours": 24})
    assert store.get_setting("sim") == {"offset_hours": 24} and store.get_setting("nope", 1) == 1


def test_run_log_totals_and_latest_only_filter():
    a = save(); save(); save(sid="SH-1008")
    store.add_run(case_id=a, shipment_id="SH-1004", model="m", status="ok", input_tokens=10, output_tokens=5, est_cost_usd=0.5)
    store.add_run(shipment_id="SH-1004", model="m", status="error", input_tokens=2, output_tokens=0)
    totals = store.run_totals()
    assert totals["runs"] == 2 and totals["input_tokens"] == 12 and totals["est_cost_usd"] == 0.5
    assert totals["by_status"] == {"ok": 1, "error": 1}
    assert len(store.list_cases("dev", latest_only=True)) == 2
    assert store.runs_by_model()[0]["errors"] == 1


def test_store_text_is_stored_verbatim_for_later_escaping():
    cid = store.save_case(dict(fake_case(), timeline_summary="<b>x</b>"), dataset="dev", sim_now_utc="t", input_hash="h", model=None)
    assert json.loads(json.dumps(store.get_case(cid)["case"]))["timeline_summary"] == "<b>x</b>"
