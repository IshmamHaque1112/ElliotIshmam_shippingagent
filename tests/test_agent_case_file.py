"""SDK-mocked tests: no network and no API key."""

import json
from types import SimpleNamespace

import pytest

import agent
import case_file
import config
import pipeline
import prompt
from api_errors import MissingKeyError


def use(name, payload, id="tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=payload, id=id)


def reply(*blocks):
    return SimpleNamespace(content=list(blocks))


class FakeClient:
    """Plays back scripted responses and records every request."""

    def __init__(self, responses):
        self.responses, self.requests = list(responses), []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **request):
        self.requests.append(json.loads(json.dumps(request, default=lambda o: "<blocks>")))
        return self.responses.pop(0)


def good_submission(**over):
    data = dict(
        root_cause="Hub Bottleneck", accountability="Carrier Fault", confidence=0.9,
        evidence_footnotes=[{"tool": "event_timeline", "field": "events[-1].carrier_note", "claim": "Awaiting outbound slot"}],
        timeline_summary="Stuck at Chicago Hub.", unknowns=[], data_quality_notes=[],
        customer_email="Subject: SH-1004 update. Last scan Chicago Hub. We are asking the carrier for a firm ETA.",
        carrier_inquiry="SH-1004: please confirm the outbound slot.", recommendation_rationale="Net benefit $6,337.50.",
    )
    data.update(over)
    return data


@pytest.fixture
def setup():
    service = pipeline.load_service()
    return service, pipeline.assess(service, "SH-1004", sleep=lambda s: None)


def test_loop_executes_tools_then_returns_submission(setup):
    service, assessment = setup
    client = FakeClient([reply(use("event_timeline", {"shipment_id": "SH-1004"})), reply(use(prompt.SUBMIT_TOOL_NAME, good_submission()))])
    before = service.call_counts[("SH-1004", "event_timeline")]
    result = agent.investigate(client, service, assessment, sleep=lambda s: None)
    assert result["root_cause"] == "Hub Bottleneck"
    assert service.call_counts[("SH-1004", "event_timeline")] == before + 1
    sent = client.requests[1]["messages"][-1]["content"][0]
    assert sent["type"] == "tool_result" and "events" in sent["content"]


def test_submit_schema_has_no_numbers_state_or_severity():
    props = set(prompt.submit_tool_spec()["input_schema"]["properties"])
    assert props == {"root_cause", "accountability", "confidence", "evidence_footnotes", "timeline_summary", "unknowns",
                     "data_quality_notes", "customer_email", "carrier_inquiry", "recommendation_rationale"}


def test_tool_budget_forces_submit(setup):
    service, assessment = setup
    calls = [reply(use("event_timeline", {"shipment_id": "SH-1004", "n": i}, f"t{i}")) for i in range(config.MAX_TOOL_CALLS_PER_CASE + 1)]
    client = FakeClient(calls + [reply(use(prompt.SUBMIT_TOOL_NAME, good_submission()))])
    before = service.call_counts[("SH-1004", "event_timeline")]
    agent.investigate(client, service, assessment, sleep=lambda s: None)
    assert service.call_counts[("SH-1004", "event_timeline")] == before + config.MAX_TOOL_CALLS_PER_CASE
    assert client.requests[-1]["tool_choice"] == {"type": "tool", "name": prompt.SUBMIT_TOOL_NAME}


def test_repeated_identical_call_is_refused_and_forces_submit(setup):
    service, assessment = setup
    same = {"shipment_id": "SH-1004"}
    client = FakeClient([reply(use("order_context", same, "a")), reply(use("order_context", same, "b")),
                         reply(use(prompt.SUBMIT_TOOL_NAME, good_submission()))])
    before = service.call_counts[("SH-1004", "order_context")]
    agent.investigate(client, service, assessment, sleep=lambda s: None)
    assert service.call_counts[("SH-1004", "order_context")] == before + 1
    assert "tool_choice" in client.requests[-1]


def test_cross_shipment_tool_call_is_blocked(setup):
    service, assessment = setup
    client = FakeClient([reply(use("order_context", {"shipment_id": "SH-1001"})), reply(use(prompt.SUBMIT_TOOL_NAME, good_submission()))])
    agent.investigate(client, service, assessment, sleep=lambda s: None)
    assert service.total_calls("SH-1001") == 0


def analyzer(client, service, assessment):
    return lambda hint: agent.investigate(client, service, assessment, hint, sleep=lambda s: None)


def test_valid_submission_builds_claude_case_file_with_code_numbers(setup, tmp_path):
    service, assessment = setup
    client = FakeClient([reply(use(prompt.SUBMIT_TOOL_NAME, good_submission()))])
    case = case_file.build_case_file(assessment, analyzer(client, service, assessment), state_path=tmp_path / "s.json")
    assert case.narrative == "claude" and case.financial["total_exposure_usd"] == 37575.0
    assert case.recommendation["action_id"] == "PRIORITY_AIR" and case.carrier_contact["phone"] == "555-0142"
    assert case.evidence_footnotes[0].endswith("[event_timeline: events[-1].carrier_note]")


def test_malformed_output_retries_once_then_falls_back(setup, tmp_path):
    service, assessment = setup
    bad = good_submission(root_cause="Gremlins")
    client = FakeClient([reply(use(prompt.SUBMIT_TOOL_NAME, bad, "1")), reply(use(prompt.SUBMIT_TOOL_NAME, bad, "2"))])
    case = case_file.build_case_file(assessment, analyzer(client, service, assessment), state_path=tmp_path / "s.json")
    assert case.narrative == case_file.FALLBACK_NARRATIVE
    assert case.financial["total_exposure_usd"] == 37575.0 and len(client.requests) == 2
    assert "rejected" in client.requests[1]["messages"][0]["content"]


def test_number_mismatch_is_rejected_then_retry_can_succeed(setup, tmp_path):
    service, assessment = setup
    wrong = good_submission(customer_email="SH-1004 is delayed; we will credit $5,000 to your account.")
    client = FakeClient([reply(use(prompt.SUBMIT_TOOL_NAME, wrong, "1")), reply(use(prompt.SUBMIT_TOOL_NAME, good_submission(), "2"))])
    case = case_file.build_case_file(assessment, analyzer(client, service, assessment), state_path=tmp_path / "s.json")
    assert case.narrative == "claude"
    assert "$5000" in client.requests[1]["messages"][0]["content"].replace(",", "")


def test_agent_failure_falls_back_instead_of_crashing(setup, tmp_path):
    service, assessment = setup
    def broken(hint):
        raise agent.AgentError("no submit")
    assert case_file.build_case_file(assessment, broken, state_path=tmp_path / "s.json").narrative == case_file.FALLBACK_NARRATIVE


def test_no_llm_case_file_works_for_unable_to_verify(tmp_path):
    service = pipeline.load_service()
    assessment = pipeline.assess(service, "SH-1010", sleep=lambda s: None)
    case = case_file.build_case_file(assessment, None, state_path=tmp_path / "s.json")
    assert case.state == "unable_to_verify" and case.narrative == case_file.FALLBACK_NARRATIVE
    assert case.recommendation["action_id"] == "MANUAL_VERIFY" and case.data_quality


def test_run_status_new_unchanged_worsened(tmp_path):
    path = tmp_path / "state.json"
    a = {"shipment_id": "SH-X", "state": "at_risk", "total_exposure_usd": 100}
    assert case_file.run_status(a, path) == "NEW"
    assert case_file.run_status(a, path) == "UNCHANGED"
    assert case_file.run_status(dict(a, state="delayed"), path) == "WORSENED"
    assert case_file.run_status(dict(a, state="delayed", total_exposure_usd=50), path) == "UNCHANGED"
    assert case_file.run_status(dict(a, state="delayed", total_exposure_usd=500), path) == "WORSENED"


def test_make_client_requires_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None, raising=False)
    with pytest.raises(MissingKeyError):
        agent.make_client()
