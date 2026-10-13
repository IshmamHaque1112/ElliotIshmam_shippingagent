"""Local Flask interface: ranked queue (no API call) and per-case analysis (rules only or with Claude)."""

import hmac
import os
from threading import Lock

from anthropic import APIConnectionError, APIError
from dotenv import load_dotenv
from flask import Flask, Response, abort, render_template, request

import agent
import case_file
import pipeline
from api_errors import MissingKeyError, api_error_message

app = Flask(__name__)
load_dotenv()

MAX_ANALYSES = 10
_analysis_count = 0
_analysis_lock = Lock()


def _same(supplied: str | None, expected: str) -> bool:
    """Constant-time comparison that accepts any text, including non-ASCII or missing values."""
    return hmac.compare_digest((supplied or "").encode("utf-8"), expected.encode("utf-8"))


@app.before_request
def require_demo_login() -> Response | tuple[str, int] | None:
    """Fail closed: the demo login and the session secret must both be configured."""
    expected_username = os.getenv("DEMO_USERNAME")
    expected_password = os.getenv("DEMO_PASSWORD")
    if not expected_username or not expected_password or not os.getenv("FLASK_SECRET_KEY"):
        return "Demo access is not configured on this server.", 503

    credentials = request.authorization
    username = password = None
    if credentials is not None and credentials.type == "basic":
        username, password = credentials.username, credentials.password
    if not (_same(username, expected_username) & _same(password, expected_password)):
        return Response(
            "Sign in to access the demo.", 401, {"WWW-Authenticate": 'Basic realm="Delayed Shipment Co-Pilot"'}
        )
    return None


def csrf_token() -> str:
    """Stateless token: an HMAC of the demo user under FLASK_SECRET_KEY. A cross-site page cannot read it."""
    secret = os.environ["FLASK_SECRET_KEY"].encode("utf-8")
    return hmac.new(secret, b"csrf:" + os.environ["DEMO_USERNAME"].encode("utf-8"), "sha256").hexdigest()


def _check_csrf() -> None:
    if not _same(request.form.get("csrf_token", ""), csrf_token()):
        abort(403)


def _release_analysis() -> None:
    global _analysis_count
    with _analysis_lock:
        _analysis_count = max(0, _analysis_count - 1)


def _assessment(service, shipment_id: str):
    return pipeline.assess(service, shipment_id)


@app.get("/")
def queue():
    assessments = pipeline.assess_all(pipeline.load_service())
    return render_template(
        "queue.html", queue=pipeline.ranked_queue(assessments), var=pipeline.value_at_risk(assessments),
        csrf_token=csrf_token(), max_analyses=MAX_ANALYSES,
    )


def _exception_or_404(service, shipment_id: str):
    assessment = _assessment(service, shipment_id)
    if assessment["state"] not in pipeline.EXCEPTION_STATES:
        abort(404)
    return assessment


@app.get("/case/<shipment_id>")
def rules_case(shipment_id: str):
    service = pipeline.load_service()
    if not any(s["shipment_id"].upper() == shipment_id.upper() for s in service.dataset["shipments"]):
        abort(404)
    assessment = _exception_or_404(service, shipment_id)
    case = case_file.build_case_file(assessment, None, status=case_file.run_status(assessment, save=False))
    return render_template("case.html", case=case, csrf_token=csrf_token())


@app.post("/case/<shipment_id>/analyze")
def analyze_case(shipment_id: str):
    global _analysis_count
    _check_csrf()
    service = pipeline.load_service()
    if not any(s["shipment_id"].upper() == shipment_id.upper() for s in service.dataset["shipments"]):
        abort(404)
    assessment = _exception_or_404(service, shipment_id)
    status = case_file.run_status(assessment, save=False)

    def page(case, error=None, code=200):
        return render_template("case.html", case=case, error=error, csrf_token=csrf_token()), code

    rules_only = case_file.build_case_file(assessment, None, status=status)
    with _analysis_lock:
        if _analysis_count >= MAX_ANALYSES:
            return page(rules_only, "This demo has reached its Claude analysis limit. Ask the demo owner before retrying.", 429)
        _analysis_count += 1

    try:
        client = agent.make_client()
        case = case_file.build_case_file(
            assessment, lambda hint: agent.investigate(client, service, assessment, hint), status=status
        )
    except (MissingKeyError, APIConnectionError) as error:
        _release_analysis()  # nothing reached Anthropic, so it must not use the cap
        message = str(error) if isinstance(error, MissingKeyError) else api_error_message(error)
        return page(rules_only, f"Could not analyze with Claude: {message} Showing the rules-only case file.")
    except APIError as error:
        return page(rules_only, f"{api_error_message(error)} Showing the rules-only case file.")
    return page(case)
