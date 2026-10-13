"""Acceptance test: rules output must agree with the hidden labels for all 12 shipments."""

import json
import pytest

import config
import pipeline

LABELS = json.loads((config.DATA_DIR / "ground_truth_labels.json").read_text(encoding="utf-8"))
HOURS = {"eta_drift_hours", "dwell_hours", "dwell_baseline_hours", "hours_late_now", "projected_hours_late"}


@pytest.fixture(scope="module")
def assessments():
    return {a["shipment_id"]: a for a in pipeline.assess_all(pipeline.load_service(), sleep=lambda s: None)}


def close(actual, expected, tolerance):
    return actual is not None and abs(actual - expected) <= tolerance


@pytest.mark.parametrize("label", LABELS["labels"], ids=lambda l: l["shipment_id"])
def test_state_and_metrics_match_label(assessments, label):
    actual = assessments[label["shipment_id"]]
    assert actual["state"] == label["expected_state"]
    for key, expected in label["expected_metrics"].items():
        value = actual[key]
        if key == "mitigation_ranking":
            assert [(r["action_id"], r["net_benefit_usd"]) for r in value] == [
                (r["action_id"], r["net_benefit_usd"]) for r in expected
            ]
        elif key == "next_milestone" or isinstance(expected, (str, type(None))):
            assert value == expected, key
        else:
            assert close(value, expected, 0.05 if key in HOURS else 0.01), (key, value, expected)


def test_queue_order_matches_label(assessments):
    queue = [a["shipment_id"] for a in pipeline.ranked_queue(list(assessments.values()))]
    assert queue == LABELS["expected_queue_order"]


def test_on_track_shipments_trigger_no_agent_work(assessments):
    queue = {a["shipment_id"] for a in pipeline.ranked_queue(list(assessments.values()))}
    assert {s for s, a in assessments.items() if a["state"] == "on_track"}.isdisjoint(queue)
