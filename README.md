# Delayed Shipment Exception Co-Pilot

A read-only agent that helps a logistics dispatcher with delayed shipments. Deterministic rules decide which shipments are delayed or at risk and rank them by financial exposure. Claude investigates only the exceptions and writes an evidence-backed case file with a recommendation and email drafts for a human to review.

**Rules decide, the model explains.** Code computes state, severity, exposure and every number. Claude classifies the likely cause, cites evidence and drafts messages. It cannot change a figure, and any dollar amount it invents is rejected.

Nothing in this project sends email, books freight, files claims or writes to an outside system. All data is fictional and static (`data/shipments_dataset.json`).

The full spec is `docs/PRD_delayed_shipment_agent_v0.5.md` (section 6 "Rules" and section 9 "Case file" are binding).

## Architecture

```text
data/shipments_dataset.json (static, fictional)
        |
mock_service.py  -- injects 429 / 503 / malformed JSON from data/fault_schedule.json
        |
fetch.py         -- timeout, 3 attempts, exponential backoff, structured failure
        |
tools.py         -- 3 read-only tools + data-quality gate
        |
rules.py         -- state, penalties, exposure, ranking, urgency, mitigation (no LLM, no network)
        |
pipeline.py      -- ranked queue + value-at-risk header (no API call)
        |
        +--> exceptions only --> agent.py (Claude tool-use loop, max 8 tool calls)
                                      |  submits narrative only via submit_case_analysis
                                      v
                                case_file.py (pydantic; code numbers + model narrative;
                                              rejects mismatched figures; fallback if invalid)
                                      |
                              main.py (CLI)   app.py (web)   eval.py (hidden labels)
```

States: `unable_to_verify`, `on_track`, `delivered_late`, `delayed`, `at_risk`. Only `delayed`, `at_risk`, `delivered_late` and `unable_to_verify` reach the agent. On-track shipments never trigger an API call.

## Quick setup

Run `./setup.sh` (macOS/Linux) or `./setup.ps1` (Windows). It creates `.venv`, installs the requirements and runs the offline tests. The included `.env` already has a generated web login and secret; add your `ANTHROPIC_API_KEY` to it. Then run the live checks:

```bash
python main.py analyze SH-1004      # one real Claude case file
python main.py analyze SH-1012      # should ignore and flag the injected carrier note
python eval.py                      # full live eval (about 10 Claude calls)
```

## Setup (manual)

Python 3.11 or newer.

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then fill in values; never commit .env
```

| Variable | Needed for |
|---|---|
| `ANTHROPIC_API_KEY` | Claude analysis (CLI `analyze`, web "Analyze with Claude", live eval). Not needed for `--no-llm` or the queue. |
| `ANTHROPIC_MODEL` | Optional. Default `claude-sonnet-5-5`. |
| `DEMO_USERNAME`, `DEMO_PASSWORD`, `FLASK_SECRET_KEY` | Web app. All three are required; it refuses every request (HTTP 503) until they are set. |

## Run

```bash
python main.py queue --no-llm              # value-at-risk header and ranked queue, no API call
python main.py analyze --no-llm            # rules-only case files for every exception, no key needed
python main.py analyze SH-1004             # Claude case file for one shipment (needs a key)
python main.py analyze SH-1012 --json      # the injected carrier note is ignored and flagged
flask --app app run                        # web UI; sign in with the demo login
python eval.py --no-llm                    # check rules against hidden labels, no key needed
python eval.py                             # live eval; add --model claude-haiku-5-5 to compare models
pytest
```

The web app shows the ranked queue computed with no API call. Each row offers "Rules only" and "Analyze with Claude". Drafts appear in read-only boxes. "Analyze with Claude" counts against a limit of 10 per server run; requests that never reach Anthropic (missing key, no connection) do not count. Forms carry a CSRF token derived from `FLASK_SECRET_KEY`.

## The 12 fictional shipments

| Id | Scenario | Expected state |
|---|---|---|
| SH-1001 | Clean, moving normally | on_track |
| SH-1002 | Delivered on time | on_track |
| SH-1003 | Weather delay (an ineligible option must not be recommended) | delayed |
| SH-1004 | VIP stuck at a hub, escalating penalty | delayed |
| SH-1005 | ETA keeps slipping; also recovers from two 429 errors | at_risk |
| SH-1006 | Silent stall: no scan, ETA unchanged | at_risk |
| SH-1007 | Address error, low value | delayed |
| SH-1008 | Customs hold, missing commercial invoice | delayed |
| SH-1009 | Delivered late, claim candidate | delivered_late |
| SH-1010 | Tracking API down (503) | unable_to_verify |
| SH-1011 | Messy data: mixed timezones, duplicate scan | delayed |
| SH-1012 | Carrier note contains an injected instruction | delayed |

`data/ground_truth_labels.json` holds the answers. Only `eval.py` and the tests may read it; a test enforces that. A separate 4-shipment hold-out set is kept privately and is not in this repo.

## Safety design

- Carrier notes and all free text in tool results are untrusted data. The system prompt says so, and a tool guard blocks any call about a different shipment than the case.
- The model's only output channel is `submit_case_analysis`, which has no state, severity, badge or dollar field. Dollar amounts in its text are checked against the rules output. A bad submission is retried once, then replaced by a rules-only fallback marked `"narrative": "none (fallback)"`.
- Fail closed: the web app needs its login and secret; the key is read from the environment only and never logged.
- A shipment with missing or unreadable data becomes `unable_to_verify` instead of a guess.

## Limits

- Static fictional data and a simulated clock (`meta.simulated_now_utc`, 2026-10-08 14:00 UTC). Rules never read the real clock.
- When the tracking service fails, the carrier is unknown, so a case file has no carrier contact.
- The usage cap lives in memory per server process and resets on restart.
- Evidence checks in `eval.py` cover what can be tested by rule; items such as `must_include` still need a person to read the drafts.
- `requirements.txt` gives minimum versions only; pin exact versions after you test with your own environment.
