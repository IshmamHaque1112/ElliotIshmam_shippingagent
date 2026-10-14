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
    return ok(case=case_view(row, pipeline.snapshot()), history=store.disposition_history(case_id), feedback=store.feedback_for(case_id))


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
