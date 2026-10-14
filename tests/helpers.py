"""Shared fakes for SDK-mocked tests (no network, no key)."""

import base64
import json
from types import SimpleNamespace

import prompt


def use(name, payload, id="tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=payload, id=id)


def reply(*blocks, input_tokens=100, output_tokens=40):
    return SimpleNamespace(content=list(blocks), usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens))


class FakeClient:
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
        timeline_summary="Stuck at the hub.", unknowns=[], data_quality_notes=[],
        customer_email="Subject: shipment update. Last scan at the hub. We are asking the carrier for a firm ETA.",
        carrier_inquiry="Please confirm the outbound slot.", recommendation_rationale="Best net benefit.",
    )
    data.update(over)
    return data


def submit_client(**over):
    return FakeClient([reply(use(prompt.SUBMIT_TOOL_NAME, good_submission(**over)))])


def basic(username="pursuit-demo", password="test-only-password"):
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}
