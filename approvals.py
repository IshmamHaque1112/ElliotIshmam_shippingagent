"""Approval request drafts: text for a human to send, built from code figures. Nothing is ever sent by this system."""

from typing import Any

import case_file
import config

ROLE_LABELS = {"ops_manager": "Operations manager"}


def needs_request(case: dict[str, Any]) -> str | None:
    """The role that must approve the recommended action, if it is anyone other than the dispatcher."""
    role = (case.get("recommendation") or {}).get("requires_approval")
    return role if role and role != "dispatcher" and case["recommendation"].get("net_benefit_usd") is not None else None


def _usd(value: Any) -> str:
    return f"${value:,.2f}"


def build_draft(case: dict[str, Any]) -> str:
    """Fixed template. Every figure comes from the case's code-computed sections. If a saved Claude case exists, its
    rationale (already checked for invented dollar figures) is added as one line; it is dropped if any figure in it
    is not one of ours."""
    rec, fin, timing = case["recommendation"], case["financial"], case["timing"]
    role = needs_request(case)
    if role is None:
        raise ValueError("this case's recommendation needs no approval beyond the dispatcher")
    lines = [
        f"To: {ROLE_LABELS.get(role, role)}",
        f"Subject: Approval request: {rec['label'] or rec['action_id']} for {case['shipment_id']}", "",
        f"Please approve {rec['label'] or rec['action_id']} for shipment {case['shipment_id']} "
        f"(customer {fin.get('customer') or 'unknown'}, {str(fin.get('customer_tier') or '').replace('_', ' ').lower()}).", "",
        f"- Cost: {_usd(rec['cost_usd'])}",
        f"- Penalty avoided: {_usd(rec['penalty_avoided_usd'])}",
        f"- Net benefit: {_usd(rec['net_benefit_usd'])}",
        f"- Current exposure: {_usd(fin['total_exposure_usd'])}",
        f"- Promise: {timing.get('promise') or 'unknown'}; projected {timing.get('projected_hours_late') if timing.get('projected_hours_late') is not None else 'n/a'} h late",
        f"- Latest checkpoint: {timing.get('latest_checkpoint') or 'unknown'}",
    ]
    rationale = rec.get("rationale")
    if case.get("narrative") == "claude" and rationale and not _foreign_figures(rationale, case):
        lines += ["", f"Reason: {rationale}"]
    lines += ["", "Drafted for a person to send. This system does not send, book or approve anything."]
    return "\n".join(lines)


def _foreign_figures(text: str, case: dict[str, Any]) -> list[str]:
    allowed: set[float] = set()
    case_file._numbers({k: case[k] for k in ("financial", "timing", "recommendation")}, allowed)
    return [m for m in case_file.DOLLARS.findall(text) if round(float(m.replace(",", "")), 2) not in allowed]


def next_statuses(current: str | None) -> tuple[str, ...]:
    return config.APPROVAL_FLOW.get(current, ())
