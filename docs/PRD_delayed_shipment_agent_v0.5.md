# PRD: Delayed Shipment Exception Co-Pilot

**Status:** Draft v0.5 (merges the Gemini v0.4 draft with fixes; static fictional dataset; Claude powers the agent only)
**Owners:** Elliot Watson & Ishmam Haque
**Date:** October 8, 2026

---

## 1. Summary

A read-only co-pilot for logistics dispatchers. It checks shipments against a tracking and order service, uses deterministic rules to decide which ones are delayed or at risk, ranks them by **financial exposure** (not by hours late), and hands the dispatcher a case file with evidence, a recommended next step, and ready-to-edit drafts for the customer and the carrier.

**The agent never acts.** It reads, ranks, explains and drafts. Humans send, approve and execute.

**Data approach:** v1 runs on a static, fictional dataset served by a local mock tracking service. No real carrier API, no real customer data. The Claude API key powers the investigator agent only.

## 2. Problem

Dispatchers spend most of their time hunting for exceptions and reassembling context, not deciding. Existing alerts have four flaws:

1. **Financially blind:** a delay on a $240 consumer parcel looks the same as a delay on a $28,500 order with a penalty clause.
2. **Black box:** "Package delayed," with no rule or evidence behind it.
3. **Reactive:** carriers alert only after they post an exception code. Silent stalls go unnoticed.
4. **Manual friction:** after spotting a problem, the dispatcher still has to find the SLA terms, the carrier's number, and write two emails.

## 3. Goals and non-goals

### Goals (v1 prototype)
- G1. Rank exceptions by dollar exposure.
- G2. Detect delayed, at-risk and silently stalled shipments with explicit, unit-tested rules.
- G3. Produce an evidence-linked case file per exception, with drafts for the customer and carrier.
- G4. Recommend the best next step by net dollar benefit, and say when doing nothing paid is the right call.
- G5. Fail honestly: a failed check is `unable_to_verify`, never "on track."
- G6. Build and evaluate entirely on a static, labeled, fictional dataset with a hold-out set.

### Non-goals (v1)
- Any write action: sending email, booking reroutes or air freight, filing claims, issuing credits. Buttons in the output are **recommendations with the information needed to act**; a human does the acting elsewhere.
- Real carrier or aggregator integrations; any real shipment or customer data.
- Rate shopping, dispatching, or a web UI (CLI output is enough for v1).

## 4. Users

| User | Need |
|---|---|
| Dispatcher / ops coordinator (primary) | A ranked queue, each item decidable in about a minute without opening another system |
| Operations manager (secondary) | One number for total exposure, and which carriers and causes drive it |
| Builders (Elliot, Ishmam) | Run traces, auditable rules, repeatable eval results |

## 5. Architecture

**Principle: rules rank, the model explains and drafts.** Code decides state, severity and every number. The model never produces or changes a number, a state or a dollar figure.

```
 STATIC (versioned)                 RUN TIME
 ------------------                 --------------------------------------------------------
 shipments_dataset.json --+
 fault_schedule.json -----+--> MOCK TRACKING SERVICE  (simulated clock, fault injection, read-only)
                          |        |  get_tracking_status | order_context | event_timeline
 ground_truth_labels.json |        v
   (hidden, eval only)    |   FETCH LAYER   timeouts, capped retries, backoff, error capture
                          |        v
                          |   RULES ENGINE  (pure code, no LLM)
                          |        state, ETA drift, dwell, exposure, severity, badge,
                          |        countdown, mitigation ranking
                          |        v
                          |   flagged? -- no --> done (listed as on_track)
                          |        | yes
                          |        v
                          |   INVESTIGATOR AGENT (Claude, tool use, read-only tools, call budget)
                          |        root cause, accountability, evidence footnotes,
                          |        timeline summary, unknowns, drafts
                          |        v
                          |   CASE FILE BUILDER  merges code-computed fields + model fields,
                          |        validates schema, rejects any model number that disagrees with code
                          |        v
                          |   CLI QUEUE  value-at-risk header + ranked cases
                          |        v
                          |   HUMAN reviews, edits drafts, acts elsewhere --> feedback log (JSONL)
```

If the model's output is malformed or fails validation, the builder retries once, then emits a **fallback case file built from rule output alone** (clearly marked "no AI narrative").

## 6. Rules (single source of truth)

All thresholds live in one config file. The reference implementation is in `tools/build_dataset.py` and every dataset label is checked against it.

**Time handling.** Parse ISO-8601. A timestamp with an offset is converted to UTC. A timestamp with no offset is read as the local time of the event's facility. Duplicate events (same time, facility and code) are collapsed. Events are sorted by normalized time; the **timeline's latest event is authoritative** (if the tracking summary's checkpoint is older, note it in `data_quality`). The promise is `promise_datetime_utc`; if only `promise_date` exists, the promise is 17:00 local time at the destination facility.

**States**

| State | Condition |
|---|---|
| `unable_to_verify` | A required tool (tracking or timeline) still fails after retries, or returns unparseable data |
| `on_track` | Delivered on or before the promise; or not delivered and none of the at-risk or delayed conditions apply |
| `delivered_late` | Delivered after the promise (closed case) |
| `delayed` | Not delivered and `max(ETA, now)` is later than the promise |
| `at_risk` | Not delayed, and any of: dwell exceeds the baseline; margin (promise minus projected delivery) under 6h; two or more forward ETA revisions |

**Dwell** = now minus the latest event time. **Baseline** = the facility's own `dwell_baseline_hours` if the latest event is an arrival, hold, exception, customs hold or delivery attempt; otherwise 24h (maximum gap between linehaul scans). A customs border crossing legitimately holds freight longer than a hub, so baselines are per facility.

**ETA drift** = number of consecutive ETA changes where the new ETA is later than the previous one. An ETA moved earlier is not drift.

**Penalty(hours late)** = 0 if not late. Otherwise `rate × (hours late − grace)`, with an optional escalation: after `after_hours_late`, extra hours bill at `rate × multiplier`. Capped at `penalty_cap_usd`.

**Money**
- `accrued_penalty` = Penalty(hours already late now)
- `projected_penalty` = Penalty(hours late at projected delivery)
- `total_exposure` = `order_value + projected_penalty` for delayed and at-risk shipments; the penalty alone for `delivered_late` (the goods arrived); `order_value` for `unable_to_verify` (penalty unknown); 0 for on-track.
- `priority_score` = `total_exposure × 1.25` for VIP enterprise customers, else ×1.0. **The multiplier only affects ranking; dollar figures shown to people are never inflated.**
- Queue order = `priority_score` descending.

**Severity** (dollar-based): HIGH at $20,000 or more, MEDIUM at $5,000 or more, otherwise LOW. Duration is shown separately, so a three-day delay on a $240 order is LOW.

**Urgency badge:** `IMMEDIATE_SLACK_OR_CALL` if exposure is $5,000 or more, or the next penalty milestone is within 120 minutes; else `DAILY_ROLLUP`. Closed `delivered_late` cases are always `DAILY_ROLLUP`.

**Next penalty milestone / countdown:** the earliest future moment among "penalty clock starts" (promise plus grace) and "penalty rate escalates" (promise plus escalation hours). Shown as minutes from now.

**Mitigation ranking** (eligible options only): `penalty_avoided = Penalty(projected late) − Penalty(projected late − hours_saved)`; `net_benefit = penalty_avoided − cost`. Recommend the highest net benefit; ties go to the lower cost, then `ESCALATE_CARRIER`. If every paid option has negative net benefit, the recommendation is `ESCALATE_CARRIER` and the case file says why. Special cases: `delivered_late` recommends `FILE_CARRIER_CLAIM`; `unable_to_verify` recommends `MANUAL_VERIFY`. Net benefit counts penalties only. Customer-relationship value is deliberately left to the human (see open questions).

## 7. Features

### Core (v1)
- **D1. Financial exposure triage.** Rules above; ranked queue.
- **D2. Escalation drafts.** A customer delay notice and a carrier inquiry, each containing tracking ID, last confirmed scan, ETA and promise, and the ask. Drafts must not promise anything the data does not support (no invented ETAs, credits or refunds).
- **D3. Recommended next step with economics.** Best option by net benefit, plus the runner-up and why paid options were rejected.
- **D4. Value-at-risk header.** Active exceptions, total exposure, accrued penalties, plus a separate line for closed late cases. In v1 this is the CLI header, computed per run on the simulated clock.
- **D5. Root cause and accountability.** Causes: Weather, Hub Bottleneck, Customs, Address/Recipient Issue, Carrier Capacity, Unknown. Accountability: Carrier Fault, Warehouse Fault, Customer/Recipient, Unavoidable, Undetermined. "Unknown" and "Undetermined" are valid, and each answer carries a confidence and its evidence.
- **D6. Evidence footnotes.** Every claim cites the tool and field it came from, and the rule that fired (for example, `Rule dwell: 34.0h > 12h baseline [event_timeline: CHI-HUB ARRIVED]`). Inference is labeled as inference.
- **D7. Dual time display.** Each key timestamp is shown in facility-local time and dispatcher time (`Oct 6, 23:00 CDT (Oct 7, 00:00 EDT)`). The facility's own time zone is used, not the dispatcher's guess.
- **D8. Urgency badging** and **D9. carrier contact block** (support phone, email, claim link with the shipment reference) and **D10. penalty countdown**, all computed from the rules and data above.

### Minor additions in this version (small by design)
- **M1. Untrusted-text handling.** Carrier notes and other free text are data, never instructions. The agent's system prompt says so, and the dataset includes a shipment (SH-1012) whose note tries to hijack the agent. Pass condition: the instruction is ignored and flagged in `unknowns`.
- **M2. Numbers come from code.** The case file builder overwrites or rejects any model-supplied figure that disagrees with the rules engine.
- **M3. Run-to-run status tag.** A small local JSON file remembers each shipment's last state and exposure, and each case is tagged `NEW`, `WORSENED` or `UNCHANGED`, so unchanged cases collapse to one line.
- **M4. Feedback log.** After each case the dispatcher may log `useful / not useful / wrong` plus a note to a JSONL file (CLI prompt or flag). It feeds the eval set later.

### Later (not v1)
Incident clustering, carrier and lane scorecards, revised ETA ranges, Slack delivery, a live dashboard, rate shopping, any write action.

## 8. Tools (read-only, served by the mock service)

| Tool | Returns |
|---|---|
| `get_tracking_status(shipment_id)` | Carrier, service, raw status text, current location, ETA, delivered time, latest checkpoint, last-updated time, carrier contact block |
| `order_context(shipment_id)` | Customer, tier, order value, SLA penalty terms (rate, grace, cap, optional escalation), promise, ship-to address, downstream note, mitigation options with cost and hours saved and eligibility |
| `event_timeline(shipment_id)` | Scan events (with carrier free-text notes) and ETA revision history |

Tool names are the same everywhere in this project.

## 9. Case file schema (example is SH-1004 from the dataset)

```json
{
  "shipment_id": "SH-1004",
  "carrier": "Northline Freight",
  "state": "delayed",
  "severity": "HIGH",
  "urgency_badge": "IMMEDIATE_SLACK_OR_CALL",
  "run_status": "NEW",
  "financial": {
    "customer": "Halvorsen Medical Supply",
    "customer_tier": "VIP_ENTERPRISE",
    "order_value_usd": 28500.00,
    "accrued_penalty_usd": 3337.50,
    "projected_penalty_usd": 9075.00,
    "total_exposure_usd": 37575.00,
    "priority_score": 46968.75
  },
  "timing": {
    "promise": "Oct 7, 15:45 UTC",
    "hours_late_now": 22.25,
    "projected_hours_late": 42.25,
    "dwell_hours": 34.0,
    "dwell_baseline_hours": 12,
    "eta_forward_revisions": 2,
    "eta_drift_hours": 46.25,
    "latest_checkpoint": "Chicago Hub ARRIVED, Oct 6, 23:00 CDT (Oct 7, 00:00 EDT)",
    "next_penalty_milestone": {"label": "Penalty rate escalates", "minutes_from_now": 105}
  },
  "classification": {
    "root_cause": "Hub Bottleneck",
    "accountability": "Carrier Fault",
    "confidence": 0.9
  },
  "evidence_footnotes": [
    "Rule dwell: 34.0h > 12h baseline [event_timeline: CHI-HUB ARRIVED, note 'Awaiting outbound linehaul slot']",
    "Rule drift: ETA moved later 2 times, +46.25h overall [event_timeline.eta_history]"
  ],
  "timeline_summary": "Arrived at Chicago Hub with no outbound scan since. Carrier note says it is awaiting a linehaul slot.",
  "unknowns": ["No carrier-confirmed departure time"],
  "data_quality": [],
  "recommendation": {
    "action_id": "PRIORITY_AIR",
    "label": "Priority air re-consignment",
    "cost_usd": 2400,
    "penalty_avoided_usd": 8737.50,
    "net_benefit_usd": 6337.50,
    "requires_approval": "ops_manager",
    "runner_up": {"action_id": "REROUTE_ALT_HUB", "net_benefit_usd": 3000.00}
  },
  "carrier_contact": {
    "name": "Northline Freight",
    "phone": "555-0142",
    "email": "priority-desk@northline-freight.example",
    "claim_link": "https://claims.northline-freight.example/file?ref=SH-1004"
  },
  "drafts": {
    "customer_email": "Subject: Update on your order ORD-5004 ...",
    "carrier_inquiry": "Attention Northline Priority Desk: shipment SH-1004 ..."
  },
  "fields_from_code": ["state", "severity", "urgency_badge", "financial", "timing", "recommendation (numbers)", "carrier_contact"],
  "fields_from_model": ["classification", "evidence_footnotes", "timeline_summary", "unknowns", "drafts", "recommendation (wording)"]
}
```

## 10. Failure handling

- Timeouts, 429 and 5xx: retry up to 3 times with backoff; then record the failure.
- Malformed or truncated JSON: treated as a failure of that tool call.
- Required tool still failing: state `unable_to_verify`, listing what failed and what the human should check. Partial data may be shown but must be labeled.
- Missing fields are recorded in `data_quality`, never silently defaulted.
- Agent limits: at most 8 tool calls per case, a token cap per case, and a hard stop on repeated identical calls.

## 11. Dataset and evaluation

- **Dev set (12 shipments)** with hidden labels: clean on-time, delivered on-time, weather, VIP hub bottleneck, ETA drift, silent stall, address error on a tiny order, customs, delivered late, API outage, messy data, prompt injection.
- **Hold-out set (4 shipments)**, kept by one owner and run only for final evaluation. Its contents are intentionally not described here; it covers edge cases and traps the dev set does not.
- **Faults** are injected by the mock service from a schedule file (retry test on SH-1005, permanent 503 on SH-1010), not baked into the data.
- **Checks the eval script runs:** state accuracy; exact match on code-computed metrics (hours within 0.05, dollars within $0.01); queue order; root cause within the acceptable set; no model number differs from code; every evidence footnote names a tool; `must_not` items (for example, never follows the injected instruction, never says "on track" for an unverified shipment).
- **Note:** results show the system behaves correctly on known scenarios, not how it performs on real shipments.

## 12. Risks

| Risk | Mitigation |
|---|---|
| Model invents or alters numbers | Numbers from code only (M2); eval check |
| Prompt injection via carrier notes | M1; dedicated fixture |
| Overfitting to a small fixed dataset | Hold-out set; partner-authored new cases |
| False alarms from generic thresholds | Per-facility baselines; backward-ETA fixture |
| "Exposure" overstates real loss (whole order value counted) | Labeled "revenue exposed"; open question 3 |
| Drafts promise things the data does not support | Draft rules in D2; `must_not` checks |
| API key leaked or runaway cost | Key in environment only; per-case call and token caps |

## 13. Build plan for two people

| Owner | Work |
|---|---|
| **A: Data and rules** | Mock service with simulated clock and fault injection; fetch layer with retries; rules engine and unit tests against the label metrics |
| **B: Agent and output** | Investigator agent (tool use, injection-safe prompt); case file builder and validator; CLI queue and header; feedback log; eval script |
| Both | Review the dataset together; agree on thresholds; run the hold-out once at the end |

Order: dataset (done) → mock service and rules → agent → builder and CLI → faults → eval → hold-out run.

## 14. Open questions

1. Is `order_value + projected_penalty` the right exposure measure, or should only a share of the order value count?
2. Should net benefit include a retention value for VIP customers?
3. Who owns the hold-out set and keeps it unseen?
4. Which model and spend cap for development?
5. Is CLI output enough for the demo, or is a simple HTML view wanted?

## 15. Changes from the Gemini v0.4 draft

- Removed `[cite: n]` markers, a corrupted character, and an unclosed code block.
- Removed "auto-reroute" and "single-click" execution language that contradicted the read-only rule; the output is now a recommendation, and humans act.
- Fixed the exposure formula: it now distinguishes accrued from projected penalty, and the VIP multiplier affects ranking only (it previously could not be applied to the formula as written).
- Fixed the case-file example: the old one had a 34-hour dwell ending on the wrong day, labeled a Chicago facility as "PDT local," and used a real carrier's phone number.
- Replaced real carrier names and numbers with fictional ones (555-01xx numbers, `.example` domains).
- Made severity dollar-based, so duration alone cannot make a $240 order a top-priority item.
- Made tool names consistent, added carrier contact data and mitigation costs to the tool outputs so the features have a source, and added `unable_to_verify`, failure handling, eval, hold-out and injection tests.
- Replaced the "real-time ticker" with a per-run CLI header, which is what v1 can deliver.
