"""Fail-closed basic auth, stateless CSRF token, and security headers."""

import hmac
import os

from flask import Response, abort, jsonify, request

CSP = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'"


def same(supplied: str | None, expected: str) -> bool:
    """Constant-time comparison that accepts any text, including non-ASCII or missing values."""
    return hmac.compare_digest((supplied or "").encode("utf-8"), expected.encode("utf-8"))


def require_demo_login():
    """The demo login and the secret must both be configured, or every request is refused."""
    expected_username = os.getenv("DEMO_USERNAME")
    expected_password = os.getenv("DEMO_PASSWORD")
    if not expected_username or not expected_password or not os.getenv("FLASK_SECRET_KEY"):
        return "Demo access is not configured on this server.", 503
    credentials = request.authorization
    username = password = None
    if credentials is not None and credentials.type == "basic":
        username, password = credentials.username, credentials.password
    if not (same(username, expected_username) & same(password, expected_password)):
        return Response("Sign in to access the demo.", 401, {"WWW-Authenticate": 'Basic realm="Delayed Shipment Co-Pilot"'})
    return None


def csrf_token() -> str:
    """HMAC of the demo user under FLASK_SECRET_KEY. A page on another site cannot read it."""
    secret = os.environ["FLASK_SECRET_KEY"].encode("utf-8")
    return hmac.new(secret, b"csrf:" + os.environ["DEMO_USERNAME"].encode("utf-8"), "sha256").hexdigest()


def check_csrf() -> None:
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
    if not same(supplied, csrf_token()):
        abort(403)


def add_headers(response: Response) -> Response:
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Cache-Control", "no-store")
    return response


def json_error(error):
    """JSON errors for /api/*, with no stack traces."""
    code = getattr(error, "code", 500) or 500
    names = {400: "bad request", 403: "forbidden", 404: "not found", 405: "method not allowed", 429: "limit reached"}
    return jsonify({"schema": "v1", "error": names.get(code, "server error"), "code": code}), code
