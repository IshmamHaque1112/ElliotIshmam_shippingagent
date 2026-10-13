"""Builds the case file. Code owns state and numbers; the model owns the narrative (PRD section 9)."""

import json
import re
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

import config
import prompt
import tools
from agent import AgentError

FALLBACK_NARRATIVE = "none (fallback)"
STATE_RANK = {"on_track": 0, "at_risk": 1, "unable_to_verify": 1, "delayed": 2, "delivered_late": 2}
DOLLARS = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")


class Footnote(BaseModel):
    tool: Literal["get_tracking_status", "order_context", "event_timeline"]
    field: str = Field(min_length=1)
    claim: str = Field(min_length=1)


class Analysis(BaseModel):
    root_cause: Literal["Weather", "Hub Bottleneck", "Customs", "Address/Recipient Issue", "Carrier Capacity", "Unknown"]
    accountability: Literal["Carrier Fault", "Warehouse Fault", "Customer/Recipient", "Unavoidable", "Undetermined"]
    confidence: float = Field(ge=0, le=1)
    evidence_footnotes: list[Footnote]
    timeline_summary: str
    unknowns: list[str]
    data_quality_notes: list[str]
    customer_email: str
    carrier_inquiry: str
    recommendation_rationale: str


class CaseFile(BaseModel):
    shipment_id: str
    carrier: str | None
    state: str
    severity: str | None
    urgency_badge: str | None
    run_status: Literal["NEW", "WORSENED", "UNCHANGED"]
    narrative: str
    financial: dict[str, Any]
    timing: dict[str, Any]
    classification: dict[str, Any]
    evidence_footnotes: list[str]
    timeline_summary: str
    unknowns: list[str]
    data_quality: list[str]
    recommendation: dict[str, Any]
    carrier_contact: dict[str, Any] | None
    drafts: dict[str, str]
    fields_from_code: list[str] = ["state", "severity", "urgency_badge", "financial", "timing", "recommendation (numbers)", "carrier_contact"]
    fields_from_model: list[str] = ["classification", "evidence_footnotes", "timeline_summary", "unknowns", "drafts", "recommendation (wording)"]


def _numbers(value: Any, found: set[float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        found.add(round(float(value), 2))
    elif isinstance(value, dict):
        for item in value.values():
            _numbers(item, found)
    elif isinstance(value, list):
        for item in value:
            _numbers(item, found)


def allowed_dollar_figures(assessment: dict[str, Any]) -> set[float]:
    found: set[float] = set()
    _numbers({k: v for k, v in assessment.items() if k != "_data"}, found)
    _numbers((assessment.get("_data") or {}).get("order_context"), found)
    return found


def mismatched_figures(analysis: Analysis, assessment: dict[str, Any]) -> list[str]:
    """Dollar amounts in the narrative that appear nowhere in the rules output or order data."""
    allowed = allowed_dollar_figures(assessment)
    text = " ".join([analysis.timeline_summary, analysis.customer_email, analysis.carrier_inquiry,
                     analysis.recommendation_rationale, *analysis.unknowns, *analysis.data_quality_notes,
                     *(f.claim for f in analysis.evidence_footnotes)])
    return sorted({m for m in DOLLARS.findall(text) if round(float(m.replace(",", "")), 2) not in allowed})


def run_status(assessment: dict[str, Any], path=None, save: bool = True) -> str:
    path = path or config.RUN_STATE_PATH
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    sid = assessment["shipment_id"]
    now = {"state": assessment["state"], "exposure": assessment["total_exposure_usd"] or 0}
    before = state.get(sid)
    if before is None:
        status = "NEW"
    elif STATE_RANK[now["state"]] > STATE_RANK.get(before["state"], 0) or now["exposure"] > before["exposure"]:
        status = "WORSENED"
    else:
        status = "UNCHANGED"
    if save:
        state[sid] = now
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return status


def _code_sections(assessment: dict[str, Any], status: str) -> dict[str, Any]:
    data = assessment.get("_data") or {}
    tracking, order = data.get("get_tracking_status"), data.get("order_context") or {}
    carrier = (tracking or {}).get("carrier") or None
    sid = assessment["shipment_id"]
    ranking = assessment["mitigation_ranking"]
    best = ranking[0] if ranking else None
    recommendation = {"action_id": assessment["recommended_action_id"], "label": None, "cost_usd": None,
                      "penalty_avoided_usd": None, "net_benefit_usd": None, "requires_approval": None, "runner_up": None}
    if best:
        recommendation.update({k: best.get(k) for k in ("label", "cost_usd", "penalty_avoided_usd", "net_benefit_usd", "requires_approval")})
        if len(ranking) > 1:
            recommendation["runner_up"] = {"action_id": ranking[1]["action_id"], "net_benefit_usd": ranking[1]["net_benefit_usd"]}
    elif recommendation["action_id"] == "MANUAL_VERIFY":
        recommendation["label"] = "Manually verify status with the carrier"
    elif recommendation["action_id"] == "FILE_CARRIER_CLAIM":
        recommendation["label"] = "File a carrier claim for the late delivery"
    return {
        "shipment_id": sid,
        "carrier": carrier["name"] if carrier else None,
        "state": assessment["state"], "severity": assessment["severity"],
        "urgency_badge": assessment["urgency_badge"], "run_status": status,
        "financial": {
            "customer": order.get("customer_name"), "customer_tier": order.get("customer_tier"),
            "order_value_usd": order.get("order_value_usd"), "accrued_penalty_usd": assessment["accrued_penalty_usd"],
            "projected_penalty_usd": assessment["projected_penalty_usd"],
            "total_exposure_usd": assessment["total_exposure_usd"], "priority_score": assessment["priority_score"],
        },
        "timing": {
            "promise": assessment["promise_dispatcher"], "promise_utc": assessment["promise_utc"],
            "hours_late_now": assessment["hours_late_now"], "projected_hours_late": assessment["projected_hours_late"],
            "dwell_hours": assessment["dwell_hours"], "dwell_baseline_hours": assessment["dwell_baseline_hours"],
            "eta_forward_revisions": assessment["eta_forward_revisions"], "eta_drift_hours": assessment["eta_drift_hours"],
            "latest_checkpoint": assessment["latest_checkpoint"], "next_penalty_milestone": assessment["next_milestone"],
        },
        "recommendation": recommendation,
        "carrier_contact": None if not carrier else {
            "name": carrier["name"], "phone": carrier.get("support_phone"), "email": carrier.get("support_email"),
            "claim_link": f"{carrier['claim_url']}{sid}" if carrier.get("claim_url") else None,
        },
    }


def fallback_case_file(assessment: dict[str, Any], status: str) -> CaseFile:
    """Rules-only case file: every number is still correct; the narrative is empty by design."""
    notes = [f"Rule: {reason}" for reason in assessment["reasons"]]
    quality = [f"{tool}: {message}" for tool, message in assessment.get("fetch_errors", {}).items()]
    return CaseFile(
        **_code_sections(assessment, status), narrative=FALLBACK_NARRATIVE,
        classification={"root_cause": "Unknown", "accountability": "Undetermined", "confidence": 0.0},
        evidence_footnotes=notes, timeline_summary="No AI narrative available; see the rule reasons.",
        unknowns=["Root cause and accountability not assessed (no AI narrative)."], data_quality=quality,
        drafts={"customer_email": "", "carrier_inquiry": ""},
    )


def build_case_file(
    assessment: dict[str, Any],
    analyze: Callable[[str | None], dict[str, Any]] | None,
    status: str | None = None,
    state_path=None,
) -> CaseFile:
    """`analyze(hint)` returns the model's raw submission; None means rules only (no LLM)."""
    status = status or run_status(assessment, state_path)
    if analyze is None:
        return fallback_case_file(assessment, status)

    hint = None
    for _attempt in range(2):
        try:
            analysis = Analysis.model_validate(analyze(hint))
        except ValidationError as error:
            hint = f"schema problems: {error.errors()[0]['loc']} {error.errors()[0]['msg']}"
            continue
        except AgentError as error:
            hint = str(error)
            continue
        bad = mismatched_figures(analysis, assessment)
        if bad:
            hint = f"it contained dollar figures that code did not compute: {', '.join('$' + b for b in bad)}. Use only the rules output."
            continue

        sections = _code_sections(assessment, status)
        sections["recommendation"]["rationale"] = analysis.recommendation_rationale
        return CaseFile(
            **sections, narrative="claude",
            classification={"root_cause": analysis.root_cause, "accountability": analysis.accountability, "confidence": analysis.confidence},
            evidence_footnotes=[f"{f.claim} [{f.tool}: {f.field}]" for f in analysis.evidence_footnotes],
            timeline_summary=analysis.timeline_summary, unknowns=analysis.unknowns,
            data_quality=analysis.data_quality_notes,
            drafts={"customer_email": analysis.customer_email, "carrier_inquiry": analysis.carrier_inquiry},
        )
    return fallback_case_file(assessment, status)
