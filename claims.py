"""Claim packet text for a late delivery. Pure; every number comes from code (rules.penalty_steps)."""

from typing import Any


def claim_text(a: dict[str, Any], order: dict[str, Any], carrier: dict[str, Any] | None, steps: dict[str, Any], events: list[dict[str, Any]]) -> str:
    sid = a["shipment_id"]
    lines = [f"Claim packet for shipment {sid} (order {order['order_id']})", "",
             f"Promised: {a['promise_dispatcher']} ({a['promise_utc']} UTC)",
             f"Delivered {a['hours_late_now']:.2f} h after the promise.", "", "Penalty calculation:"]
    for st in steps["steps"]:
        lines.append(f"- {st['label']}: {st['detail']}" + (f" = ${st['amount_usd']:,.2f}" if st["amount_usd"] is not None else ""))
    lines += [f"Penalty owed: ${steps['total_usd']:,.2f}", "", "Timeline (UTC):"]
    lines += [f"- {e['utc']} · {e['facility']} · {e['code']}" for e in events] or ["- no events"]
    if carrier:
        lines += ["", f"Carrier: {carrier.get('name')} · {carrier.get('support_phone') or ''} · {carrier.get('support_email') or ''}"]
        if carrier.get("claim_url"):
            lines.append(f"Claim link: {carrier['claim_url']}{sid}")
    lines += ["", "Prepared for a person to file. This system does not file claims."]
    return "\n".join(lines)
