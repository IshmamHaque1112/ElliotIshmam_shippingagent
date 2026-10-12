"""Local Flask web interface for the Shipping Agent demo."""

import hmac
import os
from threading import Lock
from typing import Any

from anthropic import APIConnectionError, APIError, APIStatusError
from dotenv import load_dotenv
from flask import Flask, Response, render_template, request

from harness import classify_if_ready, prepare_shipment


app = Flask(__name__)
load_dotenv()

MAX_CLASSIFICATIONS = 10
_classification_count = 0
_classification_lock = Lock()


@app.before_request
def require_demo_login() -> Response | tuple[str, int] | None:
    """Require a configured shared demo login before serving any page."""
    expected_username = os.getenv("DEMO_USERNAME")
    expected_password = os.getenv("DEMO_PASSWORD")
    if not expected_username or not expected_password:
        return "Demo access is not configured on this server.", 503

    credentials = request.authorization
    username = credentials.username if credentials else ""
    password = credentials.password if credentials else ""
    valid_username = hmac.compare_digest(username, expected_username)
    valid_password = hmac.compare_digest(password, expected_password)
    if not (valid_username and valid_password):
        return Response(
            "Sign in to access the Shipping Agent demo.",
            401,
            {"WWW-Authenticate": 'Basic realm="Shipping Agent demo"'},
        )
    return None


def api_error_message(error: APIError) -> str:
    """Turn Anthropic SDK exceptions into concise messages for the page."""
    if isinstance(error, APIConnectionError):
        return "Could not reach Anthropic. Check your internet connection and try again."

    if isinstance(error, APIStatusError):
        if error.status_code == 401:
            message = "Anthropic rejected the API key. Check that the local .env key is valid."
        elif error.status_code == 429:
            message = "Anthropic rate or usage limit reached. Check account limits before retrying."
        elif error.status_code == 402:
            message = "Anthropic reports a billing problem. Check the API account billing settings."
        else:
            message = f"Anthropic returned an API error (HTTP {error.status_code})."

        request_id = getattr(error, "request_id", None)
        if request_id:
            message += f" Request ID: {request_id}."
        return message

    return "Anthropic request failed. Check the API configuration and try again."


@app.get("/")
def home():
    return render_template("index.html")


@app.post("/")
def submit_shipment():
    global _classification_count

    tracking_number = request.form.get("tracking_number", "").strip()
    action = request.form.get("action", "")

    if not tracking_number:
        return render_template("index.html", error="Enter a tracking number.")

    if action == "prepare":
        result = prepare_shipment(tracking_number)
        return render_template("index.html", result=result, tracking_number=tracking_number)

    if action != "classify":
        return render_template("index.html", error="Choose Prepare or Classify with Claude.")

    prepared = prepare_shipment(tracking_number)
    if prepared["decision"] != "READY_FOR_AI":
        return render_template("index.html", result=prepared, tracking_number=tracking_number)

    with _classification_lock:
        if _classification_count >= MAX_CLASSIFICATIONS:
            return render_template(
                "index.html",
                error="This demo has reached its Claude classification limit. Ask the demo owner before retrying.",
                tracking_number=tracking_number,
            ), 429
        _classification_count += 1

    try:
        result = classify_if_ready(tracking_number)
    except RuntimeError as error:
        return render_template(
            "index.html",
            error=f"Could not classify shipment: {error}",
            tracking_number=tracking_number,
        )
    except APIConnectionError as error:
        return render_template(
            "index.html", error=api_error_message(error), tracking_number=tracking_number
        )
    except APIError as error:
        return render_template(
            "index.html", error=api_error_message(error), tracking_number=tracking_number
        )

    return render_template("index.html", result=result, tracking_number=tracking_number)
