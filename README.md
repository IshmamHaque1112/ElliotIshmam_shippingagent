# Delayed Shipment Exception Co-Pilot

A read-only agent that helps a logistics dispatcher with delayed shipments. Deterministic rules decide which shipments are delayed or at risk and rank them by financial exposure. Claude investigates only the exceptions and writes an evidence-backed case file with a recommendation and email drafts for a human to review.

**Rules decide, the model explains.** Code computes state, severity, exposure and every number. Claude classifies the likely cause, cites evidence and drafts messages. It cannot change a figure, and any dollar amount it invents is rejected.

Nothing in this project sends email, books freight, files claims or writes to an outside system. All data is fictional and static (`data/shipments_dataset.json`, 12 shipments, or `data/shipments_extended.json`, 60 shipments; choose with `DATASET`).

The full spec is `docs/PRD_delayed_shipment_agent_v0.5.md` (section 6 "Rules" and section 9 "Case file" are binding).

## Architecture

```text
static dataset (dev 12 / extended 60) -> mock_service (429/503/malformed JSON faults + simulation overrides)
   -> fetch (timeout, 3 attempts, back-off) -> tools (3 read-only tools + data-quality gate)
   -> rules.py (state, penalties, exposure, ranking, urgency, mitigation, what-if; no LLM, no network)
   -> pipeline.py (ranked queue, value at risk, cached snapshot)
        |
        +-- exceptions only --> agent.py (Claude tool-use loop, max 8 tool calls) --> case_file.py (code numbers + model narrative)
                                                                                         |
   store.py (SQLite: cases, Claude runs, dispositions, feedback) <---- analysis.py -------+
        |
   app.py: views.py (pages) + api.py (JSON v1)   main.py (CLI)   report.py (static HTML)   eval.py (hidden labels)
```

States: `unable_to_verify`, `on_track`, `delivered_late`, `delayed`, `at_risk`. Only `delayed`, `at_risk`, `delivered_late` and `unable_to_verify` reach the agent. On-track shipments never trigger an API call.

## Quick setup

Run `./setup.sh` (macOS/Linux) or `./setup.ps1` (Windows). It creates `.venv`, installs the requirements and runs the offline tests. Copy `.env.example` to `.env` and fill it in (the key is only needed for Claude analysis).

| Variable | Needed for |
|---|---|
| `ANTHROPIC_API_KEY` | Claude analysis (web "Analyze", CLI `analyze`, live eval). Not needed for rules-only use. |
| `ANTHROPIC_MODEL` | Optional. Default `claude-sonnet-5-5`. |
| `DEMO_USERNAME`, `DEMO_PASSWORD`, `FLASK_SECRET_KEY` | Web app and API. All three are required; every request gets HTTP 503 until they are set. |
| `DATASET` | `dev` (default) or `extended`. |
| `RUNS_DB` | Optional path of the SQLite file (default `./runs.db`, gitignored). |

## Run

```bash
flask --app app run                         # the web app (sign in with the demo login)
DATASET=extended flask --app app run        # the same with 60 shipments
python main.py queue --no-llm               # value-at-risk header and ranked queue, no API call
python main.py analyze SH-1004              # Claude case file for one shipment (needs a key)
python main.py analyze --no-llm             # rules-only case files, no key
python main.py report --out report.html     # one self-contained static HTML snapshot
python eval.py --no-llm --dataset extended  # check rules against the hidden labels
python eval.py --model claude-haiku-5-5     # live; compare models (results appear on /eval)
python scripts/client_demo.py http://127.0.0.1:5000   # a script calling the JSON API
pytest
```

## Pages

| Page | What it shows |
|---|---|
| `/` Dashboard | Value at risk, state breakdown, exposure by carrier and root cause, top 10, "Analyze top N" |
| `/shipments` | Every delivery with search, filters, sorting and paging |
| `/shipments/<id>` | Facts, event timeline (UTC, facility and dispatcher time), ETA and penalty charts, mitigation options, what-if, saved cases, raw tool data |
| `/queue` | Full ranking, "why this rank", CSV export |
| `/cases`, `/cases/<id>` | Saved case files, disposition (acknowledged, in progress, resolved, dismissed), feedback, stale and fallback banners |
| `/runs` | One row per Claude run: model, tools, tokens, estimated cost, duration, status, rejections |
| `/carriers`, `/digest` | Carrier scorecard and a read-only daily roll-up preview |
| `/eval` | Stored eval results and a model-comparison history (evals run from the CLI only) |
| `/simulate` | Move the simulated clock forward and inject or remove faults |

Cases are saved only when you analyze with Claude or choose "Save rules-only case". A saved Claude case is marked **stale** when the rules output changes (for example after the simulated clock moves).

## JSON API (schema v1)

Same login as the pages. `GET /api/summary`, `/api/shipments` (filters `state`, `carrier`, `tier`, `severity`, `badge`, `q`, `sort`, `page`, `page_size`), `/api/shipments/<id>`, `/api/queue`, `/api/cases`, `/api/cases/<id>`, `/api/runs`. POSTs (`/api/shipments/<id>/analyze`, `/api/cases/<id>/disposition`, `/api/cases/<id>/feedback`) need the CSRF token in an `X-CSRF-Token` header. Analyze counts against the usage cap exactly like the web button. Errors are `{"schema": "v1", "error": "...", "code": 4xx}`. Carrier text in responses is untrusted data (see `untrusted_fields`).

## Safety design

- Carrier notes and all free text in tool results are untrusted data: the system prompt says so, the pages label and escape them, and a tool guard blocks calls about any other shipment.
- The model's only output channel is `submit_case_analysis`, which has no state, severity, badge or dollar field. Dollar amounts in its text are checked against the rules output; a bad submission is retried once, then replaced by a rules-only fallback.
- Fail closed: login and secret must be set. Pages send a strict Content-Security-Policy (no inline styles or scripts, no external assets), CSV export neutralizes formula injection, and the key is read from the environment only and never stored or logged.
- Hidden labels are read only by `eval.py` and the tests (a test enforces it). No hold-out set lives in this repo.

## Limits

- Static fictional data; rules never read the real clock. The simulated clock only moves forward.
- The usage cap, the assessment cache and the simulation state are per process: run one worker (the Dockerfile does).
- Cost figures are estimates and appear only after you add model prices to `config.PRICES`.
- The web view skips retry back-off sleeps (the service is simulated); the CLI and the agent keep them.
- When tracking fails the carrier is unknown, so such a case file has no carrier contact.
- Not built: prompt caching, automatic model routing (decide with `eval.py --model` evidence first), and a live Anthropic-path test without a key.
- `requirements.txt` gives minimum versions only.
