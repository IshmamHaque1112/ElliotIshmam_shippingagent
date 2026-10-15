"""Read-only JSON API (schema v1). POSTs need the CSRF token (X-CSRF-Token header or csrf_token field)."""

from typing import Any

from flask import Blueprint, jsonify, request

import analysis
import config
import limits
import listing
import pipeline
import security
import store

bp = Blueprint("api", __name__, url_prefix="/api")
UNTRUSTED = ["timeline.events[].carrier_note", "timeline.events[].description"]
STATUS = {"limit": 429, "no_key": 503, "connection": 502, "api_error": 502}


def ok(**body: Any):
    return jsonify({"schema": "v1", **body})


def fail(message: str, code: int):
    return jsonify({"schema": "v1", "error": message, "code": code}), code


def case_view(row: dict[str, Any], snap: pipeline.Snapshot) -> dict[str, Any]:
    current = snap.by_id.get(row["shipment_id"])
    stale = current is None or row["dataset"] != snap.dataset_name or row["input_hash"] != pipeline.rules_hash(current)
    return {"id": row["id"], "shipment_id": row["shipment_id"], "state": row["state"], "severity": row["severity"],
            "exposure_usd": row["exposure_usd"], "narrative": row["narrative"], "model": row["model"],
            "run_status": row["run_status"], "sim_now_utc": row["sim_now_utc"], "created_at": row["created_at"],
            "stale": stale, "disposition": row.get("disposition"), "case": row["case"]}


@bp.get("/summary")
def summary():
    snap = pipeline.snapshot()
    count = lambda key: {v: sum(1 for r in snap.rows if r[key] == v) for v in sorted({r[key] for r in snap.rows if r[key]})}
    return ok(dataset=snap.dataset_name, sim_now_utc=snap.sim_now_utc, value_at_risk=snap.var, by_state=count("state"),
              by_severity=count("severity"), by_badge=count("urgency_badge"), analyses_remaining=limits.remaining())


@bp.get("/shipments")
def shipments():
    snap = pipeline.snapshot()
    page = listing.paginate(listing.filter_rows(snap.rows, request.args), request.args, config.WEB_PAGE_SIZE)
    return ok(**page)


@bp.get("/shipments/<shipment_id>")
def shipment(shipment_id: str):
    snap = pipeline.snapshot()
    a = snap.by_id.get(shipment_id.upper())
    if a is None:
        return fail("not found", 404)
    row = next(r for r in snap.rows if r["shipment_id"] == a["shipment_id"])
    data = a.get("_data") or {}
    latest = store.latest_case_for(a["shipment_id"], snap.dataset_name)
    return ok(shipment=row, assessment={k: v for k, v in a.items() if not k.startswith("_")},
              tracking=data.get("get_tracking_status"), order=data.get("order_context"), timeline=data.get("event_timeline"),
              untrusted_fields=UNTRUSTED, latest_case_id=latest["id"] if latest else None)


@bp.get("/queue")
def queue():
    snap = pipeline.snapshot()
    return ok(value_at_risk=snap.var, queue=sorted((r for r in snap.rows if r["rank"]), key=lambda r: r["rank"]))


@bp.get("/cases")
def cases():
    snap = pipeline.snapshot()
    rows = store.list_cases(snap.dataset_name, shipment_id=request.args.get("shipment_id"), status=request.args.get("status"),
                            severity=request.args.get("severity"))
    return ok(cases=[case_view(r, snap) for r in rows])


@bp.get("/cases/<int:case_id>")
def case(case_id: int):
    row = store.get_case(case_id)
    if row is None:
        return fail("not found", 404)
    row["disposition"] = store.latest_dispositions([case_id]).get(case_id, {"status": "new"})
    return ok(case=case_view(row, pipeline.snapshot()), history=store.disposition_history(case_id), feedback=store.feedback_for(case_id),
              trace=store.trace_for_case(case_id), approval=store.approval_current(case_id), approval_history=store.approval_history(case_id), trace_untrusted=["trace.attempts[].calls[].summary", "trace.attempts[].calls[].arguments"])


def incident_view(c: dict[str, Any], detail: bool = False) -> dict[str, Any]:
    out = {k: c[k] for k in ("id", "kind", "title", "size", "exposure_usd", "carriers", "first_scan_utc", "last_scan_utc", "why", "recommendation")}
    out["shipment_ids"] = [m["shipment_id"] for m in c["members"]]
    if detail:
        out["members"] = c["members"]
        out["untrusted_fields"] = ["members[].note", "carrier_note"]
        out["carrier_note"] = c.get("carrier_note")
    return out


@bp.get("/incidents")
def incidents():
    import clustering
    return ok(incidents=[incident_view(c) for c in clustering.for_snapshot(pipeline.snapshot())])


@bp.get("/incidents/<incident_id>")
def incident(incident_id: str):
    import clustering
    found = clustering.find(clustering.for_snapshot(pipeline.snapshot()), incident_id)
    return ok(incident=incident_view(found, True)) if found else fail("not found", 404)


@bp.get("/watch")
def watch():
    snap = pipeline.snapshot()
    return ok(watch=[{"shipment_id": sid, "reasons": w["reasons"], "state": snap.by_id[sid]["state"]} for sid, w in sorted(snap.watch.items())])


@bp.post("/ask")
def ask_route():
    """Ask a question about the queue. Counts against the usage cap exactly like analyze."""
    import ask as ask_module
    security.check_csrf()
    question = " ".join(str(_body().get("question", "")).split())[:ask_module.MAX_QUESTION_CHARS]
    if not question:
        return fail("question is required", 400)
    kind, outcome, message = ask_module.ask_with_cap(question)
    if kind != "ok":
        return fail(message, STATUS[kind])
    return ok(ask_id=outcome.ask_id, status=outcome.status, answer=outcome.answer, shipment_ids=outcome.shipment_ids,
              message=None if outcome.answer else ask_module.NO_ANSWER, analyses_remaining=limits.remaining())


@bp.get("/replay")
def replay_get():
    import views
    return ok(**views.replay_state())


@bp.post("/replay/<action>")
def replay_post(action: str):
    """Used by the play button. Same actions as the page: start, step (hours), end, stop."""
    import views
    security.check_csrf()
    try:
        views.replay_apply(action, float(_body()["hours"]) if _body().get("hours") else None)
    except (ValueError, KeyError):
        return fail("bad replay request", 400)
    return ok(**views.replay_state())


@bp.get("/forecast")
def forecast():
    import forecast as forecast_module
    return ok(**forecast_module.project(pipeline.snapshot()))


@bp.get("/handoff")
def handoff():
    import views  # the page and the API share one report builder
    snap = pipeline.snapshot()
    report = views.handoff_report(request.args.get("since"), snap.dataset_name)
    brief = lambda rows: [{"case_id": c["id"], "shipment_id": c["shipment_id"], "state": c["state"], "exposure_usd": c["exposure_usd"],
                           "disposition": c["disposition"]} for c in rows]
    return ok(since=report["since"], marker=report["marker"], new=brief(report["new"]), worsened=brief(report["worsened"]),
              resolved=brief(report["resolved"]),
              open_by_owner=[{"owner": g["owner"], "exposure_usd": g["exposure_usd"], "cases": brief(g["cases"])} for g in report["open_by_owner"]])


@bp.get("/runs")
def runs():
    return ok(runs=store.list_runs(), totals=store.run_totals(), by_model=store.runs_by_model(),
              estimate_note="Costs are estimates and appear only when config.PRICES has the model.")


def _body() -> dict[str, Any]:
    return request.get_json(silent=True) or request.form.to_dict()


@bp.post("/cases/<int:case_id>/disposition")
def disposition(case_id: int):
    security.check_csrf()
    if store.get_case(case_id) is None:
        return fail("not found", 404)
    body = _body()
    try:
        store.add_disposition(case_id, str(body.get("status", "")), body.get("owner"), body.get("note"))
    except ValueError:
        return fail("status must be one of " + ", ".join(store.DISPOSITIONS), 400)
    return ok(case_id=case_id, status=body["status"])


@bp.post("/cases/<int:case_id>/feedback")
def feedback(case_id: int):
    security.check_csrf()
    if store.get_case(case_id) is None:
        return fail("not found", 404)
    body = _body()
    try:
        store.add_feedback(case_id, str(body.get("verdict", "")), body.get("note"))
    except ValueError:
        return fail("verdict must be one of " + ", ".join(store.VERDICTS), 400)
    return ok(case_id=case_id, verdict=body["verdict"])


@bp.post("/shipments/<shipment_id>/analyze")
def analyze(shipment_id: str):
    security.check_csrf()
    snap = pipeline.snapshot()
    a = snap.by_id.get(shipment_id.upper())
    if a is None or a["state"] not in pipeline.EXCEPTION_STATES:
        return fail("not found", 404)
    kind, outcome, message = analysis.analyze_with_cap(a["shipment_id"])
    if kind != "ok":
        return fail(message, STATUS[kind])
    return ok(case_id=outcome.case_id, narrative=outcome.case.narrative, analyses_remaining=limits.remaining())
