"""HTML pages. Server-rendered Jinja (autoescape on); every POST needs the CSRF token."""

import csv
import io
import json
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from jinja2 import Undefined
from flask import Blueprint, Response, abort, redirect, render_template, request, url_for

import analysis
import approvals
import ask as ask_module
import case_file
import claims
import clustering
import charts
import config
import eval_view
import forecast as forecast_module
import handoff as handoff_module
import limits
import listing
import pipeline
import playbooks
import replay as replay_module
import rules
import security
import sim
import store

bp = Blueprint("views", __name__)
NAV = [("views.dashboard", "Dashboard"), ("views.shipments", "Deliveries"), ("views.queue", "Ranking"), ("views.cases", "Cases"), ("views.handoff", "Handoff"), ("views.ask", "Ask"),
       ("views.runs", "Claude runs"), ("views.carriers", "Carriers"), ("views.digest", "Digest"), ("views.evaluation", "Eval"),
       ("views.simulate", "Simulate")]


@bp.app_context_processor
def inject():
    return {"csrf_token": security.csrf_token(), "nav": NAV, "sim_offset": sim.offset_hours(), "replay_on": sim.replay_utc(), "dataset_name": config.dataset_name(),
            "remaining": limits.remaining(), "max_analyses": limits.MAX_ANALYSES, "active": request.endpoint}


@bp.app_template_filter("usd")
def usd(value: Any) -> str:
    return "n/a" if value is None or isinstance(value, Undefined) else f"${value:,.2f}"


@bp.app_template_filter("css")
def css(value: Any) -> str:
    return str(value or "none").lower().replace("_", "-").replace(" ", "-")


@bp.app_template_filter("words")
def words(value: Any) -> str:
    return str(value or "-").replace("_", " ")


@bp.app_template_filter("hrs")
def hrs(value: Any) -> str:
    return "-" if value is None or isinstance(value, Undefined) else f"{value:,.1f}h"


@bp.app_template_filter("num")
def num(value: Any, places: int = 1) -> str:
    return "-" if value is None or isinstance(value, Undefined) else f"{value:,.{places}f}"


def page_url(n: int) -> str:
    """Query string for page n that keeps the other filters (properly escaped)."""
    merged = {k: v for k, v in request.args.items() if k != "page"}
    merged["page"] = n
    return "?" + urlencode(merged)


bp.add_app_template_global(page_url)


def case_status(snap: pipeline.Snapshot) -> dict[str, dict[str, Any]]:
    """Latest saved case per shipment with a fresh/stale flag."""
    out = {}
    for c in store.list_cases(snap.dataset_name, latest_only=True):
        current = snap.by_id.get(c["shipment_id"])
        out[c["shipment_id"]] = {"id": c["id"], "narrative": c["narrative"], "disposition": c["disposition"]["status"],
                                 "fresh": current is not None and c["input_hash"] == pipeline.rules_hash(current), "case": c["case"]}
    return out


def exception_or_404(snap: pipeline.Snapshot, shipment_id: str) -> dict[str, Any]:
    a = snap.by_id.get(shipment_id.upper())
    if a is None or a["state"] not in pipeline.EXCEPTION_STATES:
        abort(404)
    return a


def live_case(snap: pipeline.Snapshot, a: dict[str, Any]) -> dict[str, Any]:
    status = analysis.run_status_from_store(a, snap.dataset_name)
    return case_file.build_case_file(a, None, status=status).model_dump()


# ---------------------------------------------------------------- dashboard
def cost_estimate(n: int) -> str:
    totals = store.run_totals()
    if totals["est_cost_usd"] is None or not totals["runs"]:
        return "No estimate yet: set prices in config.PRICES and run at least one analysis."
    return f"About ${totals['est_cost_usd'] / totals['runs'] * n:,.2f} (estimate from past runs)."


@bp.get("/")
def dashboard(batch=None, error=None):
    snap = pipeline.snapshot()
    status = case_status(snap)
    active = [r for r in snap.rows if r["state"] in ("delayed", "at_risk", "unable_to_verify")]
    by_carrier: dict[str, float] = {}
    by_cause: dict[str, float] = {}
    for r in active:
        by_carrier[r["carrier"] or "Unknown"] = by_carrier.get(r["carrier"] or "Unknown", 0) + (r["exposure_usd"] or 0)
        saved = status.get(r["shipment_id"])
        cause = saved["case"]["classification"]["root_cause"] if saved and saved["narrative"] == "claude" else "Not analyzed"
        by_cause[cause] = by_cause.get(cause, 0) + (r["exposure_usd"] or 0)
    counts = {s: sum(1 for r in snap.rows if r["state"] == s) for s in charts.STATE_ORDER}
    usd_fmt = lambda v: f"${v:,.0f}"
    fc = forecast_module.project(snap)
    incidents = clustering.for_snapshot(snap)[:5]
    watching = [{**next(r for r in snap.rows if r["shipment_id"] == sid), "reasons": w["reasons"]} for sid, w in sorted(snap.watch.items())]
    options = [n for n in (1, 3, 5) if n <= max(1, limits.remaining())]
    return render_template(
        "dashboard.html", snap=snap, var=snap.var, top=sorted((r for r in snap.rows if r["rank"]), key=lambda r: r["rank"])[:10],
        state_bar=charts.state_bar(counts),
        carrier_chart=charts.bar_chart(sorted(((k, v, "c-acc") for k, v in by_carrier.items()), key=lambda i: -i[1]), usd_fmt, label="Exposure by carrier"),
        cause_chart=charts.bar_chart(sorted(((k, v, "c-unk" if k == "Not analyzed" else "c-warn") for k, v in by_cause.items()), key=lambda i: -i[1]), usd_fmt, label="Exposure by root cause"),
        status=status, options=options, cost_note=cost_estimate(1), batch=batch, error=error,
        incidents=incidents, watching=watching, forecast=fc, forecast_chart=charts.forecast_chart(fc["points"]))


@bp.post("/analyze-top")
def analyze_top():
    security.check_csrf()
    try:
        n = max(1, min(5, int(request.form.get("n", "1"))))
    except ValueError:
        abort(400)
    snap = pipeline.snapshot()
    status = case_status(snap)
    todo = [a for a in snap.queue if not (status.get(a["shipment_id"]) and status[a["shipment_id"]]["fresh"] and status[a["shipment_id"]]["narrative"] == "claude")][:n]
    results, error = [], None
    for a in todo:
        kind, outcome, message = analysis.analyze_with_cap(a["shipment_id"])
        if kind != "ok":
            error = message
            break
        results.append({"shipment_id": a["shipment_id"], "case_id": outcome.case_id, "narrative": outcome.case.narrative})
    if not todo:
        error = "Every top-ranked exception already has a fresh Claude case."
    return dashboard(batch=results, error=error)


# ---------------------------------------------------------------- deliveries and detail
@bp.get("/shipments")
def shipments():
    snap = pipeline.snapshot()
    rows = listing.filter_rows(snap.rows, request.args)
    page = listing.paginate(rows, request.args, config.WEB_PAGE_SIZE)
    opts = {"states": charts.STATE_ORDER, "carriers": sorted({(r["carrier_id"], r["carrier"]) for r in snap.rows}),
            "tiers": sorted({r["tier"] for r in snap.rows}), "severities": ["HIGH", "MEDIUM", "LOW"],
            "badges": ["IMMEDIATE_SLACK_OR_CALL", "DAILY_ROLLUP"]}
    return render_template("shipments.html", page=page, opts=opts, status=case_status(snap), snap=snap)


def timeline_view(snap: pipeline.Snapshot, a: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    data = a.get("_data") or {}
    timeline, facilities, dtz = data.get("event_timeline"), snap.dataset["facilities"], snap.dataset["meta"]["dispatcher_timezone"]
    if not timeline:
        return [], 0
    events = rules.normalized_events(timeline["events"], facilities)
    out = []
    for i, (ts, e) in enumerate(events):
        fac = facilities[e["facility_id"]]
        out.append({"utc": rules.iso(ts), "facility": fac["name"], "code": e["code"], "description": e["description"],
                    "local": rules.local_string(ts, fac["tz"]), "dispatcher": rules.local_string(ts, dtz),
                    "note": e.get("carrier_note"), "latest": i == len(events) - 1})
    return out, len(timeline["events"]) - len(events)


@bp.get("/shipments/<shipment_id>")
def shipment(shipment_id: str):
    snap = pipeline.snapshot()
    a = snap.by_id.get(shipment_id.upper())
    if a is None:
        abort(404)
    row = next(r for r in snap.rows if r["shipment_id"] == a["shipment_id"])
    data = a.get("_data") or {}
    order, timeline = data.get("order_context"), data.get("event_timeline")
    events, duplicates = timeline_view(snap, a)
    what = None
    if order and a["projected_hours_late"] is not None and request.args.get("option"):
        what = rules.what_if(order, a["projected_hours_late"], request.args["option"])
    saved = store.cases_for(a["shipment_id"], snap.dataset_name)
    current_hash = pipeline.rules_hash(a)
    return render_template(
        "shipment.html", a=a, row=row, order=order, tracking=data.get("get_tracking_status"), events=events, duplicates=duplicates,
        eta_chart=charts.eta_chart(timeline["eta_history"], a["promise_utc"], snap.sim_now_utc) if timeline and a["promise_utc"] else None,
        penalty_chart=charts.penalty_chart(order, a["hours_late_now"], a["projected_hours_late"])
        if order and a["state"] in ("delayed", "at_risk", "delivered_late") else None,
        what=what, saved=saved, current_hash=current_hash, snap=snap, is_exception=a["state"] in pipeline.EXCEPTION_STATES,
        raw=json.dumps({k: data.get(k) for k in ("get_tracking_status", "order_context", "event_timeline")}, indent=2))


# ---------------------------------------------------------------- ranking
@bp.get("/queue")
def queue():
    snap = pipeline.snapshot()
    rows = [r for r in listing.filter_rows(snap.rows, request.args) if r["rank"]]
    rows.sort(key=lambda r: r["rank"])
    return render_template("queue.html", rows=rows, status=case_status(snap), snap=snap, vip=config.VIP_MULTIPLIER)


def _safe_cell(value: Any) -> Any:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@bp.get("/queue.csv")
def queue_csv():
    snap = pipeline.snapshot()
    out = io.StringIO()
    writer = csv.writer(out)
    cols = ["rank", "shipment_id", "state", "urgency_badge", "severity", "exposure_usd", "priority_score", "customer", "carrier",
            "promise_utc", "minutes_to_milestone", "recommended_action_id"]
    writer.writerow(cols)
    for r in sorted((r for r in snap.rows if r["rank"]), key=lambda r: r["rank"]):
        writer.writerow([_safe_cell(r[c]) for c in cols])  # carrier-controlled text could start with = + - @
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=queue.csv"})


# ---------------------------------------------------------------- cases
@bp.get("/case/<shipment_id>")
def rules_case(shipment_id: str, error: str | None = None, code: int = 200):
    snap = pipeline.snapshot()
    a = exception_or_404(snap, shipment_id)
    latest = store.latest_case_for(a["shipment_id"], snap.dataset_name)
    case = live_case(snap, a)
    book, steps = playbooks.for_case(case)
    return render_template("case.html", case=case, saved=None, live=True, error=error, latest=latest, snap=snap, history=[], feedback=[],
                           trace=None, playbook_name=book, playbook_steps=steps, ticks={}), code


@bp.post("/case/<shipment_id>/analyze")
def analyze_case(shipment_id: str):
    security.check_csrf()
    snap = pipeline.snapshot()
    a = exception_or_404(snap, shipment_id)
    kind, outcome, message = analysis.analyze_with_cap(a["shipment_id"])
    if kind == "ok":
        return redirect(url_for("views.case_detail", case_id=outcome.case_id), 303)
    return rules_case(shipment_id, error=f"{message} Showing the rules-only case file.", code=429 if kind == "limit" else 200)


@bp.post("/case/<shipment_id>/report")
def report_case(shipment_id: str):
    security.check_csrf()
    snap = pipeline.snapshot()
    exception_or_404(snap, shipment_id)
    return redirect(url_for("views.case_detail", case_id=analysis.report_rules_only(shipment_id.upper()).case_id), 303)


@bp.get("/cases")
def cases():
    snap = pipeline.snapshot()
    rows = store.list_cases(snap.dataset_name, shipment_id=request.args.get("shipment_id") or None, status=request.args.get("status") or None,
                            severity=request.args.get("severity") or None)
    for c in rows:
        current = snap.by_id.get(c["shipment_id"])
        c["stale"] = current is None or c["input_hash"] != pipeline.rules_hash(current)
    return render_template("cases.html", cases=rows, statuses=store.DISPOSITIONS, snap=snap)


@bp.get("/cases/<int:case_id>")
def case_detail(case_id: int):
    saved = store.get_case(case_id)
    if saved is None:
        abort(404)
    snap = pipeline.snapshot()
    current = snap.by_id.get(saved["shipment_id"])
    saved["stale"] = current is None or saved["input_hash"] != pipeline.rules_hash(current) or saved["dataset"] != snap.dataset_name
    saved["disposition"] = store.latest_dispositions([case_id]).get(case_id, {"status": "new", "owner": None, "note": None})
    book, steps = playbooks.for_case(saved["case"])
    role = approvals.needs_request(saved["case"])
    current = store.approval_current(case_id) if role else None
    approval = {"role": role, "role_label": approvals.ROLE_LABELS.get(role, role), "current": current,
                "next": approvals.next_statuses(current["status"] if current else None), "history": store.approval_history(case_id)} if role else None
    return render_template("case.html", case=saved["case"], saved=saved, live=False, approval=approval, error=None, latest=None, snap=snap,
                           history=store.disposition_history(case_id), feedback=store.feedback_for(case_id),
                           statuses=store.DISPOSITIONS, verdicts=store.VERDICTS, trace=store.trace_for_case(case_id),
                           playbook_name=book, playbook_steps=steps, ticks=store.playbook_state(case_id))


@bp.post("/cases/<int:case_id>/approval")
def set_approval(case_id: int):
    """Draft a request, or record the status a human reports (requested, approved, declined). Nothing is sent."""
    security.check_csrf()
    saved = store.get_case(case_id)
    if saved is None:
        abort(404)
    role = approvals.needs_request(saved["case"])
    if role is None:
        abort(400)
    current = store.approval_current(case_id)
    status = request.form.get("status", "")
    if status not in approvals.next_statuses(current["status"] if current else None):
        abort(400)
    draft = approvals.build_draft(saved["case"]) if status == "drafted" else None
    store.add_approval_event(case_id, role, status, draft, request.form.get("actor"), request.form.get("note"))
    return redirect(url_for("views.case_detail", case_id=case_id) + "#approval", 303)


@bp.post("/cases/<int:case_id>/playbook")
def set_playbook(case_id: int):
    security.check_csrf()
    saved = store.get_case(case_id)
    if saved is None:
        abort(404)
    _, steps = playbooks.for_case(saved["case"])
    ids = [s["id"] for s in steps]
    store.set_playbook(case_id, {d for d in request.form.getlist("done") if d in ids}, ids)
    return redirect(url_for("views.case_detail", case_id=case_id) + "#playbook", 303)


@bp.post("/cases/<int:case_id>/disposition")
def set_disposition(case_id: int):
    security.check_csrf()
    if store.get_case(case_id) is None:
        abort(404)
    try:
        store.add_disposition(case_id, request.form.get("status", ""), request.form.get("owner"), request.form.get("note"))
    except ValueError:
        abort(400)
    return redirect(url_for("views.case_detail", case_id=case_id), 303)


@bp.post("/cases/<int:case_id>/feedback")
def set_feedback(case_id: int):
    security.check_csrf()
    if store.get_case(case_id) is None:
        abort(404)
    try:
        store.add_feedback(case_id, request.form.get("verdict", ""), request.form.get("note"))
    except ValueError:
        abort(400)
    return redirect(url_for("views.case_detail", case_id=case_id), 303)


# ---------------------------------------------------------------- ask the queue
@bp.get("/ask")
def ask(error: str | None = None, result=None, question: str = "", code: int = 200):
    return render_template("ask.html", snap=pipeline.snapshot(), asks=store.list_asks(10), error=error, result=result, question=question,
                           max_chars=ask_module.MAX_QUESTION_CHARS), code


@bp.post("/ask")
def ask_post():
    security.check_csrf()
    question = " ".join(request.form.get("question", "").split())[:ask_module.MAX_QUESTION_CHARS]
    if not question:
        abort(400)
    kind, outcome, message = ask_module.ask_with_cap(question)
    if kind == "ok":
        return ask(result=outcome, question=question)
    return ask(error=message, question=question, code=429 if kind == "limit" else 200)


# ---------------------------------------------------------------- incidents and claims
@bp.get("/incidents/<incident_id>")
def incident(incident_id: str):
    snap = pipeline.snapshot()
    found = clustering.find(clustering.for_snapshot(snap), incident_id)
    if found is None:
        abort(404)
    return render_template("incident.html", snap=snap, inc=found, status=case_status(snap))


@bp.get("/claims/<shipment_id>")
def claim(shipment_id: str):
    snap = pipeline.snapshot()
    a = snap.by_id.get(shipment_id.upper())
    if a is None or a["state"] != "delivered_late":
        abort(404)
    data = a.get("_data") or {}
    order, tracking = data["order_context"], data["get_tracking_status"]
    steps = rules.penalty_steps(order, a["hours_late_now"])
    events, duplicates = timeline_view(snap, a)
    carrier = snap.dataset["carriers"].get(tracking["carrier_id"])
    return render_template("claim.html", snap=snap, a=a, order=order, steps=steps, events=events, duplicates=duplicates, carrier=carrier,
                           claim_link=f"{carrier['claim_url']}{a['shipment_id']}" if carrier and carrier.get("claim_url") else None,
                           text=claims.claim_text(a, order, carrier, steps, events))


# ---------------------------------------------------------------- handoff
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def handoff_report(since: str | None, dataset: str) -> dict[str, Any]:
    """Grouped changes since a chosen UTC time (YYYY-MM-DDTHH:MM:SSZ) or, by default, the last reviewed marker."""
    marker = store.get_setting("handoff_reviewed")
    chosen = None
    if since:
        try:
            datetime.strptime(since, TS_FORMAT)
            chosen = since
        except ValueError:
            chosen = None
    w = handoff_module.window(marker, chosen)
    report = handoff_module.build(store.list_cases(dataset), store.dispositions_for_dataset(dataset), w)
    report.update(marker=marker, since=chosen, bad_since=bool(since) and chosen is None)
    return report


@bp.get("/handoff")
def handoff():
    snap = pipeline.snapshot()
    return render_template("handoff.html", snap=snap, report=handoff_report(request.args.get("since"), snap.dataset_name))


@bp.post("/handoff/review")
def handoff_review():
    security.check_csrf()
    store.set_setting("handoff_reviewed", {**store.max_ids(), "at": store._now(), "by": (request.form.get("by") or "").strip()[:80] or None})
    return redirect(url_for("views.handoff"), 303)


# ---------------------------------------------------------------- runs and eval
@bp.get("/runs")
def runs():
    return render_template("runs.html", runs=store.list_runs(), totals=store.run_totals(), by_model=store.runs_by_model(), prices=config.PRICES)


@bp.get("/eval")
def evaluation():
    results, history = None, []
    try:
        results = json.loads((config.ROOT / "eval_results.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        history = [json.loads(line) for line in (config.ROOT / "eval_history.jsonl").read_text(encoding="utf-8").splitlines()[-30:] if line.strip()][::-1]
    except (OSError, ValueError):
        pass
    cards = eval_view.scorecards(results["results"]) if results else []
    t = eval_view.trend(history)
    return render_template("eval.html", results=results, history=history, cards=cards,
                           rate_chart=charts.trend_chart(t["pass_rate"], lambda v: f"{v:.0f}%", label="Eval pass rate across stored runs"),
                           token_chart=charts.trend_chart(t["tokens"], lambda v: f"{v:,.0f}", label="Tokens used across stored runs"))


# ---------------------------------------------------------------- carriers and digest
def carrier_stats(snap: pipeline.Snapshot) -> list[dict[str, Any]]:
    saved = case_status(snap)
    out = []
    for cid in sorted({r["carrier_id"] for r in snap.rows}):
        rows = [r for r in snap.rows if r["carrier_id"] == cid]
        active = [r for r in rows if r["state"] in ("delayed", "at_risk", "unable_to_verify")]
        dwell = [snap.by_id[r["shipment_id"]] for r in rows if snap.by_id[r["shipment_id"]]["dwell_hours"] is not None and r["state"] in ("delayed", "at_risk")]
        cases_ = [saved[r["shipment_id"]] for r in rows if r["shipment_id"] in saved]
        out.append({"carrier_id": cid, "name": rows[0]["carrier"], "shipments": len(rows), "exceptions": len(active) + sum(1 for r in rows if r["state"] == "delivered_late"),
                    "delivered_late": sum(1 for r in rows if r["state"] == "delivered_late"), "exposure_usd": sum(r["exposure_usd"] or 0 for r in active),
                    "avg_dwell_ratio": (sum(a["dwell_hours"] / a["dwell_baseline_hours"] for a in dwell) / len(dwell)) if dwell else None,
                    "cases": len(cases_), "fallbacks": sum(1 for c in cases_ if c["narrative"] != "claude")})
    for s in out:
        s["exception_rate"] = s["exceptions"] / s["shipments"] if s["shipments"] else 0
    return sorted(out, key=lambda s: -s["exposure_usd"])


@bp.get("/carriers")
def carriers():
    snap = pipeline.snapshot()
    stats = carrier_stats(snap)
    return render_template("carriers.html", stats=stats, chart=charts.bar_chart([(s["name"], s["exception_rate"] * 100, "c-warn") for s in stats], lambda v: f"{v:.0f}%", label="Exception rate by carrier"))


def digest_text(snap: pipeline.Snapshot) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    daily = sorted((r for r in snap.rows if r["state"] in ("delayed", "at_risk", "unable_to_verify") and r["urgency_badge"] == "DAILY_ROLLUP"), key=lambda r: -(r["priority_score"] or 0))
    closed = [r for r in snap.rows if r["state"] == "delivered_late"]
    lines = [f"Daily roll-up for {snap.sim_now_utc} (draft preview; nothing is sent)", "", f"Needs review today ({len(daily)}):"]
    lines += [f"- {r['shipment_id']} {r['customer']}: {r['state'].replace('_', ' ')}, exposure {usd(r['exposure_usd'])}, suggested: {r['recommended_action_id']}" for r in daily] or ["- none"]
    lines += ["", f"Closed late, claim candidates ({len(closed)}):"]
    lines += [f"- {r['shipment_id']} {r['customer']}: penalty owed {usd(r['exposure_usd'])}" for r in closed] or ["- none"]
    return daily, closed, "\n".join(lines)


@bp.get("/digest")
def digest():
    snap = pipeline.snapshot()
    daily, closed, text = digest_text(snap)
    return render_template("digest.html", daily=daily, closed=closed, text=text, snap=snap)


# ---------------------------------------------------------------- simulation
def _persist() -> None:
    store.set_setting("sim", sim.export())
    pipeline.clear_cache()


def replay_state() -> dict[str, Any]:
    """Where the replay is and what the queue looks like right now (also served as JSON to the play button)."""
    import tools
    snap = pipeline.snapshot()
    dataset = tools.load_dataset()
    active = bool(sim.replay_utc())
    info = replay_module.state(dataset, rules.parse_ts(sim.replay_utc()) if active else pipeline.now_of(dataset))
    top = sorted((r for r in snap.rows if r["rank"]), key=lambda r: r["rank"])[:5]
    return {"active": active, **info, "at_end": active and info["now_utc"] == info["end_utc"], "shipments_visible": len(snap.rows),
            "shipments_total": len(dataset["shipments"]), "active_exceptions": snap.var["active_exceptions"],
            "revenue_exposed_usd": snap.var["revenue_exposed_usd"], "sim_now_utc": snap.sim_now_utc,
            "top": [{"shipment_id": r["shipment_id"], "state": r["state"], "exposure_usd": r["exposure_usd"]} for r in top]}


def replay_apply(action: str, hours: float | None = None) -> None:
    """start (at the earliest time), step (forward only, capped at the data's own time), end, or stop. Persists the state."""
    import tools
    dataset = tools.load_dataset()
    end = replay_module.end_time(dataset)
    if action == "start":
        sim.set_replay(rules.iso(replay_module.start_time(dataset)))
    elif action == "end":
        sim.set_replay(rules.iso(end))
    elif action == "stop":
        sim.set_replay(None)
    elif action == "step":
        if not sim.replay_utc() or hours is None or not 0 < hours <= 24 * 30:
            raise ValueError("step needs an active replay and 0 < hours <= 720")
        sim.set_replay(rules.iso(min(rules.parse_ts(sim.replay_utc()) + timedelta(hours=hours), end)))
    else:
        raise ValueError("unknown replay action")
    _persist()


@bp.post("/simulate/replay/<action>")
def simulate_replay(action: str):
    security.check_csrf()
    try:
        replay_apply(action, float(request.form["hours"]) if request.form.get("hours") else None)
    except (ValueError, KeyError):
        abort(400)
    return redirect(url_for("views.simulate") + "#replay", 303)


@bp.get("/simulate")
def simulate():
    snap = pipeline.snapshot()
    scheduled = tools_schedule()
    return render_template("simulate.html", snap=snap, overrides=sim.overrides(), scheduled=scheduled, faults=sim.FAULTS,
                           tool_names=("get_tracking_status", "order_context", "event_timeline"), ids=[r["shipment_id"] for r in snap.rows],
                           saved_cases=len(store.list_cases(snap.dataset_name)), replay=replay_state(), csrf=security.csrf_token())


def tools_schedule() -> dict[str, Any]:
    import tools
    return tools.load_fault_schedule()


@bp.post("/simulate/advance")
def simulate_advance():
    security.check_csrf()
    try:
        hours = float(request.form.get("hours", ""))
        sim.advance(hours)
    except ValueError:
        abort(400)
    _persist()
    return redirect(url_for("views.simulate"), 303)


@bp.post("/simulate/reset")
def simulate_reset():
    security.check_csrf()
    sim.reset()
    _persist()
    return redirect(url_for("views.simulate"), 303)


@bp.post("/simulate/fault")
def simulate_fault():
    security.check_csrf()
    sid, tool, fault = request.form.get("shipment_id", "").upper(), request.form.get("tool", ""), request.form.get("fault", "")
    snap = pipeline.snapshot()
    if sid not in snap.by_id or tool not in ("get_tracking_status", "order_context", "event_timeline"):
        abort(400)
    try:
        sim.set_fault(sid, tool, None if fault == "clear" else fault)
    except ValueError:
        abort(400)
    _persist()
    return redirect(url_for("views.simulate"), 303)
