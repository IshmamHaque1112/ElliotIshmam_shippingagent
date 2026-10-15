"""Milestone F6: container host. Static checks on the container files, preflight, and a real-HTTP smoke test."""

import importlib.util
import re
import threading
from pathlib import Path

import pytest

import app as webapp

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---- container files
def test_dockerfile_runs_as_a_normal_user_with_a_health_check_and_no_secrets():
    text = (ROOT / "Dockerfile").read_text()
    assert re.search(r"^USER app$", text, re.M) and "HEALTHCHECK" in text and "/healthz" in text and 'CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]' in text
    assert "VOLUME /data" in text and "RUNS_DB=/data/runs.db" in text
    for secret in ("ANTHROPIC_API_KEY=", "DEMO_PASSWORD=", "FLASK_SECRET_KEY=", "sk-ant"):
        assert secret not in text


def test_compose_keeps_secrets_out_mounts_a_volume_and_locks_the_container_down():
    text = (ROOT / "docker-compose.yml").read_text()
    assert "env_file: .env" in text and "copilot-data:/data" in text and "read_only: true" in text and "no-new-privileges" in text
    assert "ANTHROPIC_API_KEY:" not in text and "DEMO_PASSWORD:" not in text


def test_dockerignore_keeps_secrets_and_the_database_out_of_the_image():
    lines = (ROOT / ".dockerignore").read_text().split()
    for item in (".env", ".venv", "runs.db", ".git", ".private_eval"):
        assert item in lines or item + "/" in lines, item


def test_gunicorn_settings_allow_long_claude_calls_and_do_not_log_cookies(monkeypatch):
    monkeypatch.setenv("GUNICORN_WORKERS", "3")
    cfg = load_conf()
    assert cfg["workers"] == 3 and cfg["timeout"] >= 120 and "%(q)s" not in cfg["access_log_format"] and "%({" not in cfg["access_log_format"]
    assert cfg["bind"].startswith("0.0.0.0:")


def load_conf():
    spec = importlib.util.spec_from_file_location("gunicorn_conf", ROOT / "gunicorn.conf.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {k: getattr(module, k) for k in dir(module) if not k.startswith("_")}


def test_ci_runs_the_contrast_check_and_the_tests():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "pytest" in ci and "check_contrast.py" in ci


def test_requirements_cover_what_the_app_imports():
    reqs = (ROOT / "requirements.txt").read_text().lower()
    for name in ("flask", "gunicorn", "tzdata", "anthropic", "pydantic", "python-dotenv"):
        assert name in reqs


# ---- preflight
GOOD = {"DEMO_USERNAME": "elliot", "DEMO_PASSWORD": "a-long-demo-password", "FLASK_SECRET_KEY": "x" * 40, "RUNS_DB": "", "COOKIE_SECURE": "true", "TRUST_PROXY_HOPS": "1"}


def env_with(tmp_path, **over):
    env = {**GOOD, "RUNS_DB": str(tmp_path / "data" / "runs.db")}
    (tmp_path / "data").mkdir(exist_ok=True)
    env.update(over)
    return env


def test_preflight_passes_a_good_environment(tmp_path):
    errors, warnings = load("preflight").check(env_with(tmp_path))
    assert errors == []


@pytest.mark.parametrize("name", ["DEMO_USERNAME", "DEMO_PASSWORD", "FLASK_SECRET_KEY"])
def test_preflight_fails_when_a_required_secret_is_missing(tmp_path, name):
    env = env_with(tmp_path)
    del env[name]
    errors, _ = load("preflight").check(env)
    assert any(name in e for e in errors)


def test_preflight_rejects_weak_secrets_private_eval_files_and_a_bad_dataset(tmp_path):
    pre = load("preflight")
    assert pre.check(env_with(tmp_path, FLASK_SECRET_KEY="short"))[0]
    assert pre.check(env_with(tmp_path, DEMO_PASSWORD="changeme"))[0]
    assert pre.check(env_with(tmp_path, DATASET="nope"))[0]
    for name in ("DATASET_FILE", "FAULTS_FILE", "EVAL_LABELS_FILE"):
        assert any("private eval" in e for e in pre.check(env_with(tmp_path, **{name: "/x.json"}))[0])
    assert pre.check(env_with(tmp_path, RUNS_DB=str(tmp_path / "missing" / "runs.db")))[0]


def test_preflight_warns_about_a_missing_key_proxy_and_cookie_settings(tmp_path):
    env = env_with(tmp_path)
    del env["COOKIE_SECURE"], env["TRUST_PROXY_HOPS"]
    _, warnings = load("preflight").check(env)
    text = " ".join(warnings)
    assert "ANTHROPIC_API_KEY is not set" in text and "COOKIE_SECURE" in text and "TRUST_PROXY_HOPS" in text
    _, warnings = load("preflight").check(env_with(tmp_path, ANTHROPIC_API_KEY="k"))
    assert any("spend limit" in w for w in warnings)
    _, warnings = load("preflight").check(env_with(tmp_path, ANTHROPIC_API_KEY="k", MODEL_CALLS_ENABLED="false"))
    assert not any("spend limit" in w for w in warnings)


# ---- smoke test against a real HTTP server (no Claude call)
@pytest.fixture()
def server():
    from werkzeug.serving import make_server
    httpd = make_server("127.0.0.1", 0, webapp.app, threaded=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_smoke_test_passes_against_a_running_server(server, monkeypatch):
    monkeypatch.setattr(webapp.views.analysis.agent, "make_client", lambda: (_ for _ in ()).throw(AssertionError("the smoke test must not call Claude")))
    results = load("smoke_test").run(server, "pursuit-demo", "test-only-password")
    assert results and all(ok for _, ok, _ in results), results
    assert len(results) >= 8


def test_smoke_test_reports_a_wrong_password_as_a_failed_sign_in(server):
    results = load("smoke_test").run(server, "pursuit-demo", "wrong")
    assert [name for name, ok, _ in results if not ok] == ["sign in"]


def test_smoke_test_cli_needs_the_password_in_the_environment(monkeypatch, capsys):
    monkeypatch.delenv("DEMO_PASSWORD", raising=False)
    assert load("smoke_test").main(["http://127.0.0.1:1", "--username", "x"]) == 2


def test_security_headers_hold_on_every_kind_of_response(server):
    import urllib.request
    for path in ("/login", "/healthz", "/static/app.css"):
        headers = urllib.request.urlopen(server + path).headers
        assert "default-src 'self'" in headers["Content-Security-Policy"] and headers["X-Content-Type-Options"] == "nosniff", path
