"""Acceptance test: rules output must agree with the hidden labels for every shipment in both datasets."""

import json

import pytest

import config
import eval as evaluation
import pipeline
import tools
from mock_service import MockService

HOURS = {"eta_drift_hours", "dwell_hours", "dwell_baseline_hours", "hours_late_now", "projected_hours_late"}
CASES = [
    (name, label)
    for name in config.DATASET_FILES
    for label in json.loads(evaluation.labels_path(name).read_text(encoding="utf-8"))["labels"]
]


@pytest.fixture(scope="module")
def assessments():
    out = {}
    for name in config.DATASET_FILES:
        service = MockService(tools.load_dataset(config.dataset_path(name)), tools.load_fault_schedule(config.fault_schedule_path(name)))
        out[name] = {a["shipment_id"]: a for a in pipeline.assess_all(service, sleep=lambda s: None)}
    return out


def close(actual, expected, tolerance):
    return actual is not None and abs(actual - expected) <= tolerance


@pytest.mark.parametrize("name,label", CASES, ids=lambda v: v if isinstance(v, str) else v["shipment_id"])
def test_state_and_metrics_match_label(assessments, name, label):
    actual = assessments[name][label["shipment_id"]]
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


@pytest.mark.parametrize("name", list(config.DATASET_FILES))
def test_queue_order_matches_label(assessments, name):
    labels = json.loads(evaluation.labels_path(name).read_text(encoding="utf-8"))
    queue = [a["shipment_id"] for a in pipeline.ranked_queue(list(assessments[name].values()))]
    assert queue == labels["expected_queue_order"]


@pytest.mark.parametrize("name", list(config.DATASET_FILES))
def test_on_track_shipments_trigger_no_agent_work(assessments, name):
    queue = {a["shipment_id"] for a in pipeline.ranked_queue(list(assessments[name].values()))}
    assert {s for s, a in assessments[name].items() if a["state"] == "on_track"}.isdisjoint(queue)


def test_dataset_switch_follows_the_environment(monkeypatch):
    monkeypatch.setenv("DATASET", "extended")
    assert len(tools.load_dataset()["shipments"]) == 60
    monkeypatch.setenv("DATASET", "dev")
    assert len(tools.load_dataset()["shipments"]) == 12
    monkeypatch.setenv("DATASET", "nope")
    with pytest.raises(ValueError):
        config.dataset_name()
