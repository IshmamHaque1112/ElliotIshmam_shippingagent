import json

import fetch
import pipeline
from mock_service import MockService, ServiceError
import tools
import pytest


def service(faults=None):
    return MockService(tools.load_dataset(), faults if faults is not None else tools.load_fault_schedule())


def test_http_429_then_success_counts_calls():
    svc = service({"SH-1001": {"order_context": {"fault": "http_429", "times": 2}}})
    for _ in range(2):
        with pytest.raises(ServiceError):
            svc.call("order_context", "SH-1001")
    assert json.loads(svc.call("order_context", "SH-1001"))["order_id"]
    assert svc.call_counts[("SH-1001", "order_context")] == 3


def test_malformed_json_is_truncated_text():
    svc = service({"SH-1001": {"order_context": {"fault": "malformed_json", "times": "always"}}})
    with pytest.raises(ValueError):
        json.loads(svc.call("order_context", "SH-1001"))


def test_fetch_recovers_from_two_429s_and_backs_off():
    svc = service()
    sleeps = []
    result = fetch.fetch(svc, "event_timeline", "SH-1005", sleep=sleeps.append)
    assert result.ok and result.attempts == 3
    assert sleeps == [0.5, 1.0]


def test_fetch_gives_a_structured_failure_after_three_attempts():
    result = fetch.fetch(service(), "get_tracking_status", "SH-1010", sleep=lambda s: None)
    assert not result.ok and result.attempts == 3 and "503" in result.error
    assert result.as_tool_content()["error"] == "tool_unavailable"


def test_malformed_json_counts_as_failure():
    svc = service({"SH-1001": {"order_context": {"fault": "malformed_json", "times": "always"}}})
    result = fetch.fetch(svc, "order_context", "SH-1001", sleep=lambda s: None)
    assert not result.ok and "malformed" in result.error


def test_sh1005_recovers_and_sh1010_is_unable_to_verify():
    svc = service()
    ok = pipeline.assess(svc, "SH-1005", sleep=lambda s: None)
    down = pipeline.assess(svc, "SH-1010", sleep=lambda s: None)
    assert ok["state"] != "unable_to_verify" and not ok["fetch_errors"]
    assert down["state"] == "unable_to_verify"
    assert any("unavailable" in r for r in down["reasons"])
