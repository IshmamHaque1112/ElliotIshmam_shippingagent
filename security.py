"""Fail-closed sign-in (session cookie, or a Basic header for scripts), CSRF tokens and security headers."""

import hmac
import os
import secrets
from dataclasses import dataclass
from urllib.parse import quote

from flask import Response, abort, g, has_request_context, jsonify, redirect, request
from itsdangerous import BadSignature, URLSafeSerializer

import config
import store

CSP = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'"
PUBLIC_ENDPOINTS = {"views.login", "views.login_post", "views.healthz"}


@dataclass
class Identity:
    kind: str            # "session" (signed in through /login) or "basic" (a script or test sending a Basic header)
    user: str
    sid: str | None = None
    dataset: str | None = None


def same(supplied: str | None, expected: str) -> bool:
    """Constant-time comparison that accepts any text, including non-ASCII or missing values."""
    return hmac.compare_digest((supplied or "").encode("utf-8"), expected.encode("utf-8"))


def configured() -> bool:
    return bool(os.getenv("DEMO_USERNAME") and os.getenv("DEMO_PASSWORD") and os.getenv("FLASK_SECRET_KEY"))


def credentials_ok(username: str | None, password: str | None) -> bool:
    return bool(same(username, os.environ["DEMO_USERNAME"]) & same(password, os.environ["DEMO_PASSWORD"]))


def client_ip() -> str:
    return request.remote_addr or "unknown"


def locked_out(username: str) -> bool:
    by_ip, by_user = store.recent_failures(client_ip(), username, config.LOGIN_WINDOW_MIN * 60)
    return by_ip >= config.LOGIN_MAX_FAILURES_IP or by_user >= config.LOGIN_MAX_FAILURES_USER


# ---- session cookie: only a random session id, signed. Everything else lives in the database, so logout really ends it.
def _signer() -> URLSafeSerializer:
    return URLSafeSerializer(os.environ["FLASK_SECRET_KEY"], salt="copilot-session")


def new_sid() -> str:
    return secrets.token_urlsafe(24)


def sign_sid(sid: str) -> str:
    return _signer().dumps(sid)


def sid_from_cookie() -> str | None:
    raw = request.cookies.get(config.SESSION_COOKIE)
    if not raw:
        return None
    try:
        sid = _signer().loads(raw)
    except BadSignature:
        return None
    return sid if isinstance(sid, str) else None


def secure_cookies() -> bool:
    return request.is_secure or (os.environ.get("COOKIE_SECURE") or "").strip().lower() in ("1", "true", "yes")


def set_session_cookie(response: Response, sid: str) -> None:
    response.set_cookie(config.SESSION_COOKIE, sign_sid(sid), max_age=int(config.SESSION_LIFETIME_H * 3600), httponly=True,
                        samesite="Lax", secure=secure_cookies(), path="/")


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(config.SESSION_COOKIE, path="/")


def authenticate() -> tuple[Identity | None, str | None]:
    """(identity, problem). problem is "locked" when a bad Basic header arrived from a locked-out client."""
    sid = sid_from_cookie()
    if sid:
        row = store.get_session(sid)
        if row:
            return Identity("session", row["username"], sid, row["dataset"]), None
    credentials = request.authorization
    if credentials is not None and credentials.type == "basic":
        username = credentials.username or ""
        if locked_out(username):
            return None, "locked"
        if credentials_ok(credentials.username, credentials.password):
            return Identity("basic", username), None
        store.record_login(client_ip(), username, False)
    return None, None


def safe_next(target: str | None) -> str:
    """Only a path inside this app. Anything else (another site, a scheme, a backslash trick) becomes the home page."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target or "\n" in target or "\r" in target:
        return "/"
    return "/" if target.split("?", 1)[0] == "/login" else target


def require_demo_login():
    """The login and the secret must both be configured, or every request is refused."""
    if not configured():
        return "Demo access is not configured on this server.", 503
    if request.path.startswith("/static/") or request.endpoint in PUBLIC_ENDPOINTS:
        return None
    identity, problem = authenticate()
    if identity is not None:
        g.identity = identity
        return None
    if problem == "locked":
        return Response("Too many failed sign-ins. Try again later.", 429)
    if request.path.startswith("/api/"):
        return Response("Sign in to access the demo.", 401, {"WWW-Authenticate": 'Basic realm="Delayed Shipment Co-Pilot"'})
    if request.method in ("GET", "HEAD"):
        return redirect("/login?next=" + quote(request.full_path.rstrip("?"), safe="/"), 302)
    return Response("Sign in to access the demo.", 401)


# ---- CSRF
def csrf_token() -> str:
    """HMAC under FLASK_SECRET_KEY: per session when signed in through /login, else per demo username (scripts)."""
    secret = os.environ["FLASK_SECRET_KEY"].encode("utf-8")
    identity = g.get("identity") if has_request_context() else None
    subject = b"csrf:sid:" + identity.sid.encode("utf-8") if identity is not None and identity.sid else b"csrf:" + os.environ["DEMO_USERNAME"].encode("utf-8")
    return hmac.new(secret, subject, "sha256").hexdigest()


def check_csrf() -> None:
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
    if not same(supplied, csrf_token()):
        abort(403)


def login_token(nonce: str) -> str:
    """CSRF token for the sign-in form itself, before any session exists: tied to a random cookie set with the form."""
    return hmac.new(os.environ["FLASK_SECRET_KEY"].encode("utf-8"), b"login:" + nonce.encode("utf-8"), "sha256").hexdigest()


def check_login_csrf() -> None:
    nonce = request.cookies.get(config.LOGIN_NONCE_COOKIE) or ""
    supplied = request.form.get("csrf_token") or ""
    if not nonce or not same(supplied, login_token(nonce)):
        abort(403)


def add_headers(response: Response) -> Response:
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Cache-Control", "no-store")
    if secure_cookies():
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


def json_error(error):
    """JSON errors for /api/*, with no stack traces."""
    code = getattr(error, "code", 500) or 500
    names = {400: "bad request", 403: "forbidden", 404: "not found", 405: "method not allowed", 429: "limit reached"}
    return jsonify({"schema": "v1", "error": names.get(code, "server error"), "code": code}), code
