"""Milestones F1 to F4: sign-in page, theme, per-session dataset toggle, daily Claude budget. SDK mocked; no key."""

import glob
import importlib.util
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import app as webapp
import config
import limits
import pipeline
import security
import store
from helpers import FakeClient, basic, good_submission, reply, use
import prompt

ROOT = Path(__file__).resolve().parent.parent


def client():
    return webapp.app.test_client()


def csrf_of(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def login(c, username="pursuit-demo", password="test-only-password", next_url="/"):
    page = c.get("/login")
    return c.post("/login", data={"csrf_token": csrf_of(page.get_data(as_text=True)), "username": username, "password": password, "next": next_url})


def signed_in():
    c = client()
    assert login(c).status_code == 303
    return c


def token(c):
    return csrf_of(c.get("/").get_data(as_text=True))


def toggle_options(html):
    select = re.search(r'<select name="dataset".*?</select>', html, re.S).group(0)
    return re.findall(r'<option value="(\w+)"', select)


def dataset_of(c):
    return c.get("/api/summary").get_json()["dataset"]


# ------------------------------------------------------------------ F1 sign-in
def test_login_page_has_the_exact_pitch_in_a_white_box_with_calibri():
    html = client().get("/login").get_data(as_text=True)
    pitch = "Hi. We are Elliot and Ishmam. We are intending to provide a shipping agent that can flag delayed cases before even the carriers can even figure out what is going on, and rank it by money lost."
    assert pitch in html and 'name="username"' in html and 'type="password"' in html
    css = (ROOT / "static" / "app.css").read_text()
    box = re.search(r"\.pitch\s*\{([^}]*)\}", css).group(1)
    assert "background: #ffffff" in box and "Calibri" in box and "Carlito" in box       # white box, Calibri with a fallback
    assert " style=" not in html and "<script" not in html and "cdn" not in html.lower()


def test_signing_in_sets_a_signed_http_only_cookie_and_opens_the_pages():
    c = client()
    r = login(c)
    cookie = r.headers["Set-Cookie"]
    assert r.status_code == 303 and r.headers["Location"] == "/"
    assert "copilot_session=" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "test-only-password" not in cookie and "pursuit-demo" not in cookie                    # nothing personal in the cookie
    assert c.get("/").status_code == 200 and c.get("/api/summary").status_code == 200


def test_wrong_name_and_wrong_password_look_the_same_and_make_no_session():
    c = client()
    a, b = login(c, "nobody", "test-only-password"), login(c, "pursuit-demo", "wrong")
    assert a.status_code == b.status_code == 401
    strip = lambda r: re.sub(r'value="[0-9a-f]{64}"', "", r.get_data(as_text=True))
    assert strip(a) == strip(b) and "copilot_session" not in (a.headers.get("Set-Cookie") or "") + (b.headers.get("Set-Cookie") or "")
    assert c.get("/").status_code == 302


def test_login_form_needs_its_own_csrf_token_tied_to_its_cookie():
    c = client()
    c.get("/login")
    assert c.post("/login", data={"username": "pursuit-demo", "password": "test-only-password"}).status_code == 403
    assert c.post("/login", data={"csrf_token": "x", "username": "pursuit-demo", "password": "test-only-password"}).status_code == 403
    page = client().get("/login").get_data(as_text=True)
    assert client().post("/login", data={"csrf_token": csrf_of(page), "username": "pursuit-demo", "password": "test-only-password"}).status_code == 403   # no cookie: refused


def test_failed_logins_lock_the_address_and_the_lock_expires():
    c = client()
    for _ in range(config.LOGIN_MAX_FAILURES_IP):
        assert login(c, "pursuit-demo", "bad").status_code == 401
    assert login(c).status_code == 429                                  # even the right password is refused while locked
    with store.connect() as conn:
        conn.execute("UPDATE login_attempts SET at_epoch = at_epoch - 100000")
    assert login(c).status_code == 303


def test_a_good_sign_in_clears_earlier_failures():
    c = client()
    for _ in range(config.LOGIN_MAX_FAILURES_IP - 1):
        login(c, "pursuit-demo", "bad")
    assert login(c).status_code == 303
    assert store.recent_failures("127.0.0.1", "pursuit-demo", 900)[0] == 0


@pytest.mark.parametrize("target,expected", [("https://evil.example/x", "/"), ("//evil.example", "/"), ("/\\evil.example", "/"), ("javascript:alert(1)", "/"),
                                              ("/login", "/"), ("/ask", "/ask"), ("/cases?status=new", "/cases?status=new")])
def test_next_only_goes_to_paths_inside_the_app(target, expected):
    c = client()
    assert login(c, next_url=target).headers["Location"] == expected
    assert security.safe_next(target) == expected


def test_unauthenticated_pages_redirect_and_the_api_and_posts_get_401():
    c = client()
    for path in ("/", "/shipments", "/queue", "/cases", "/handoff", "/ask", "/runs", "/carriers", "/digest", "/eval", "/simulate"):
        r = c.get(path)
        assert r.status_code == 302 and r.headers["Location"].startswith("/login?next="), path
    assert c.get("/api/summary").status_code == 401 and "WWW-Authenticate" in c.get("/api/summary").headers
    assert c.post("/simulate/reset", data={}).status_code == 401 and c.post("/dataset", data={"dataset": "dev"}).status_code == 401
    assert c.post("/logout", data={}).status_code == 401


def test_the_requested_page_is_remembered_through_sign_in():
    c = client()
    r = c.get("/ask")
    nxt = r.headers["Location"]
    assert nxt == "/login?next=/ask" and 'name="next" value="/ask"' in c.get(nxt).get_data(as_text=True)


@pytest.mark.parametrize("missing", ["DEMO_USERNAME", "DEMO_PASSWORD", "FLASK_SECRET_KEY"])
def test_sign_in_page_itself_refuses_when_a_secret_is_missing(monkeypatch, missing):
    monkeypatch.delenv(missing)
    for path in ("/login", "/", "/healthz"):
        assert client().get(path).status_code == 503


def test_logout_needs_csrf_and_ends_the_session_on_the_server():
    c = signed_in()
    cookie = c.get_cookie("copilot_session").value
    assert c.post("/logout", data={}).status_code == 403
    assert c.post("/logout", data={"csrf_token": token(c)}).status_code == 303
    replay = client()
    replay.set_cookie("copilot_session", cookie)                           # someone kept the old cookie
    assert replay.get("/").status_code == 302


def test_a_forged_or_expired_cookie_is_not_a_session():
    c = client()
    c.set_cookie("copilot_session", "not-signed")
    assert c.get("/").status_code == 302
    c = signed_in()
    with store.connect() as conn:
        conn.execute("UPDATE sessions SET expires_epoch = ?", (time.time() - 5,))
    assert c.get("/").status_code == 302


def test_basic_header_still_works_for_scripts_and_is_throttled():
    assert client().get("/api/summary", headers=basic()).status_code == 200
    c = client()
    codes = [c.get("/api/summary", headers=basic(password="bad")).status_code for _ in range(config.LOGIN_MAX_FAILURES_IP + 1)]
    assert codes[:config.LOGIN_MAX_FAILURES_IP] == [401] * config.LOGIN_MAX_FAILURES_IP and codes[-1] == 429
    assert c.get("/api/summary", headers=basic()).status_code == 429


def test_csrf_tokens_differ_per_session():
    a, b = signed_in(), signed_in()
    assert token(a) != token(b)
    assert b.post("/simulate/reset", data={"csrf_token": token(a)}).status_code == 403   # a token from another session is refused


def test_the_cookie_is_marked_secure_when_asked(monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "true")
    r = login(client())
    assert "Secure" in r.headers["Set-Cookie"] and "Strict-Transport-Security" in r.headers


# ------------------------------------------------------------------ F2 theme
def _contrast():
    spec = importlib.util.spec_from_file_location("check_contrast", ROOT / "scripts" / "check_contrast.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_colour_pair_meets_its_contrast_ratio():
    rows = _contrast().check()
    assert rows and all(ok for *_x, ok in rows), [r for r in rows if not r[4]]


def test_theme_is_times_new_roman_on_crimson_violet_with_amber_text_and_a_light_print_version():
    css = (ROOT / "static" / "app.css").read_text()
    v = _contrast().variables(css)
    assert '"Times New Roman"' in re.search(r"body\s*\{[^}]*\}", css).group(0)
    assert int(v["ink"][1:3], 16) > 0xE0 and 0x90 < int(v["ink"][3:5], 16) < 0xD8 and int(v["ink"][5:7], 16) < 0x80     # amber: strong red, mid green, low blue
    r, g, b = (int(v["bg"][i:i + 2], 16) for i in (1, 3, 5))
    assert r > g and b > g and r < 0x60 and b > 0x20       # a dark crimson-violet
    assert "@media print" in css and "--bg: #fff" in css and "prefers-reduced-motion" in css and "prefers-contrast" in css


def test_states_have_a_shape_cue_and_not_only_a_colour():
    css = (ROOT / "static" / "app.css").read_text()
    for cls in ("delayed", "at-risk", "on-track", "unable-to-verify", "delivered-late"):
        assert f".chip.{cls}::before" in css


def test_no_inline_styles_scripts_or_cdn_in_any_template_and_csp_is_unchanged():
    for path in glob.glob(str(ROOT / "templates" / "*.html")):
        if path.endswith("report.html"):
            continue   # the saved offline report is one self-contained file by design, so it carries its own stylesheet
        text = open(path, encoding="utf-8").read()
        assert " style=" not in text and "<style" not in text and "cdn" not in text.lower(), path
        assert not re.search(r"<script(?![^>]*\bsrc=)", text), path
    assert security.CSP == "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'"


# ------------------------------------------------------------------ F3 dataset toggle
def test_a_new_session_starts_on_the_default_dataset_and_the_header_offers_the_three():
    c = signed_in()
    html = c.get("/").get_data(as_text=True)
    assert dataset_of(c) == "dev" and "dev data" in html
    assert toggle_options(html) == ["dev", "extended", "edge"]
    assert "/static/loading.gif" in html and 'id="loading"' in html and 'class="apply"' in html       # spinner and the no-JavaScript button


def test_the_default_dataset_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("DATASET", "extended")
    assert dataset_of(signed_in()) == "extended"
    monkeypatch.setenv("DATASET", "nonsense")
    assert config.default_dataset() == "dev"


def test_switching_changes_the_whole_analysis_and_goes_to_the_dashboard():
    c = signed_in()
    sizes = {}
    for name in ("extended", "edge", "dev"):
        r = c.post("/dataset", data={"csrf_token": token(c), "dataset": name})
        assert r.status_code == 303 and r.headers["Location"] == "/"
        sizes[name] = len(c.get("/api/shipments?per_page=500").get_json()["items"]) if False else c.get("/api/summary").get_json()
        assert dataset_of(c) == name and f"{name} data" in c.get("/").get_data(as_text=True)
    assert sizes["extended"]["value_at_risk"] != sizes["dev"]["value_at_risk"] != sizes["edge"]["value_at_risk"]


def test_two_sessions_can_use_different_datasets_and_clocks_at_once():
    a, b = signed_in(), signed_in()
    a.post("/dataset", data={"csrf_token": token(a), "dataset": "extended"})
    assert dataset_of(a) == "extended" and dataset_of(b) == "dev"
    a.post("/simulate/advance", data={"csrf_token": token(a), "hours": "12"})
    assert "+12 h" in a.get("/").get_data(as_text=True) and "Simulated clock" not in b.get("/").get_data(as_text=True)
    assert "+12 h" in a.get("/").get_data(as_text=True)                                   # kept for the next request
    a.post("/dataset", data={"csrf_token": token(a), "dataset": "dev"})
    assert "Simulated clock" not in a.get("/").get_data(as_text=True)                     # each dataset has its own clock
    a.post("/dataset", data={"csrf_token": token(a), "dataset": "extended"})
    assert "+12 h" in a.get("/").get_data(as_text=True)
    assert "Simulated clock" not in signed_in().get("/").get_data(as_text=True)           # a new sign-in starts at the base time


def test_a_new_sign_in_after_sign_out_starts_clean():
    c = signed_in()
    c.post("/simulate/advance", data={"csrf_token": token(c), "hours": "5"})
    c.post("/logout", data={"csrf_token": token(c)})
    login(c)
    assert "Simulated clock" not in c.get("/").get_data(as_text=True)


@pytest.mark.parametrize("value", ["", "custom", "../edge", "EDGE ", "dev;extended", "all"])
def test_the_toggle_only_accepts_the_fixed_names(value):
    c = signed_in()
    assert c.post("/dataset", data={"csrf_token": token(c), "dataset": value}).status_code == 400
    assert dataset_of(c) == "dev"


def test_the_toggle_needs_csrf_a_session_and_never_lists_a_private_file(monkeypatch, tmp_path):
    c = signed_in()
    assert c.post("/dataset", data={"dataset": "edge"}).status_code == 403
    assert c.post("/dataset", data={"csrf_token": "x", "dataset": "edge"}).status_code == 403
    assert client().post("/dataset", data={"dataset": "edge"}).status_code == 401
    assert client().post("/dataset", data={"csrf_token": security.csrf_token(), "dataset": "edge"}, headers=basic()).status_code == 400   # scripts have no toggle
    monkeypatch.setenv("DATASET_FILE", str(tmp_path / "private.json"))
    html = c.get("/").get_data(as_text=True)
    assert toggle_options(html) == ["dev", "extended", "edge"] and "private" not in html


def test_switching_back_to_a_cached_dataset_makes_no_fetch_calls(monkeypatch):
    c = signed_in()
    c.get("/")
    c.post("/dataset", data={"csrf_token": token(c), "dataset": "extended"})
    c.get("/")
    import fetch
    monkeypatch.setattr(fetch, "fetch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetch on a cached dataset")))
    c.post("/dataset", data={"csrf_token": token(c), "dataset": "dev"})
    assert c.get("/").status_code == 200
    c.post("/dataset", data={"csrf_token": token(c), "dataset": "extended"})
    assert c.get("/").status_code == 200


def test_saved_cases_stay_with_their_own_dataset():
    c = signed_in()
    case = {"shipment_id": "SH-1004", "state": "delayed", "severity": "HIGH", "narrative": "none (fallback)", "run_status": "NEW",
            "financial": {"total_exposure_usd": 1.0, "priority_score": 1}}
    store.save_case(case, dataset="dev", sim_now_utc="2026-10-08T14:00:00Z", input_hash="h", model=None)
    assert len(c.get("/api/cases").get_json()["cases"]) == 1
    c.post("/dataset", data={"csrf_token": token(c), "dataset": "extended"})
    assert c.get("/api/cases").get_json()["cases"] == []


def test_switching_datasets_does_not_reset_the_daily_budget(monkeypatch):
    c = signed_in()
    before = c.get("/api/summary").get_json()["analyses_remaining"]
    monkeypatch.setattr(webapp.views.analysis.agent, "make_client", lambda: FakeClient([reply(use(prompt.SUBMIT_TOOL_NAME, good_submission()))]))
    assert c.post("/case/SH-1004/analyze", data={"csrf_token": token(c)}).status_code == 303
    mid = c.get("/api/summary").get_json()["analyses_remaining"]
    c.post("/dataset", data={"csrf_token": token(c), "dataset": "extended"})
    c.post("/dataset", data={"csrf_token": token(c), "dataset": "dev"})
    assert mid == before - 1 and c.get("/api/summary").get_json()["analyses_remaining"] == mid


# ------------------------------------------------------------------ F4 daily budget
def test_the_count_lives_in_the_database_so_a_restart_does_not_reset_it():
    assert limits.reserve() and limits.reserve()
    importlib.reload(limits)                       # a new process would start with fresh module state
    assert limits.used() == 2 and limits.remaining() == config.DAILY_CALLS_PER_SESSION - 2


def test_release_gives_the_slot_back_once():
    assert limits.reserve()
    limits.release()
    limits.release()
    assert limits.used() == 0


def test_the_caller_cap_and_the_global_cap_each_refuse_with_their_own_message():
    limits.set_used(config.DAILY_CALLS_PER_SESSION)
    assert not limits.reserve() and "your Claude analysis limit" in limits.message()
    store.set_usage(limits.day(), limits.GLOBAL, config.DAILY_CALLS_GLOBAL)
    store.set_usage(limits.day(), limits.identity(), 0)
    assert not limits.reserve() and "This demo has reached" in limits.message()
    assert limits.remaining() == 0


def test_each_session_has_its_own_cap_but_all_share_the_global_one():
    a, b = signed_in(), signed_in()
    sid_a = a.get_cookie("copilot_session")
    with store.connect() as conn:
        sids = [r[0] for r in conn.execute("SELECT sid FROM sessions ORDER BY created_at, rowid")]
    store.set_usage(limits.day(), "sid:" + sids[0], config.DAILY_CALLS_PER_SESSION)
    assert a.get("/api/summary").get_json()["analyses_remaining"] == 0 and b.get("/api/summary").get_json()["analyses_remaining"] == config.DAILY_CALLS_PER_SESSION
    store.set_usage(limits.day(), limits.GLOBAL, config.DAILY_CALLS_GLOBAL)
    assert b.get("/api/summary").get_json()["analyses_remaining"] == 0


def test_many_threads_cannot_take_more_than_the_cap():
    store.connect().close()
    results = []
    def worker():
        results.append(limits.reserve())
    threads = [threading.Thread(target=worker) for _ in range(40)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(results) == config.DAILY_CALLS_PER_SESSION and limits.used() == config.DAILY_CALLS_PER_SESSION


def test_a_new_day_starts_a_new_count(monkeypatch):
    limits.set_used(config.DAILY_CALLS_PER_SESSION)
    assert not limits.reserve()
    monkeypatch.setattr(limits, "day", lambda: "2099-01-02")
    assert limits.reserve() and limits.used() == 1


def test_day_rolls_over_in_the_configured_time_zone():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", limits.day()) and config.USAGE_TIMEZONE == "America/New_York"


def test_if_the_counter_cannot_be_used_no_call_is_made_and_rules_pages_still_work(monkeypatch):
    c = signed_in()
    def broken(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(store, "reserve_usage", broken)
    monkeypatch.setattr(webapp.views.analysis.agent, "make_client", lambda: (_ for _ in ()).throw(AssertionError("must not call Claude")))
    r = c.post("/case/SH-1004/analyze", data={"csrf_token": token(c)})
    assert r.status_code == 429 and "usage counter could not be reached" in r.get_data(as_text=True) and "rules-only" in r.get_data(as_text=True)
    assert c.get("/").status_code == 200
    monkeypatch.setattr(store, "usage_count", broken)
    assert limits.remaining() == 0


def test_kill_switch_blocks_every_claude_path_and_shows_zero_left(monkeypatch):
    monkeypatch.setenv("MODEL_CALLS_ENABLED", "false")
    monkeypatch.setattr(webapp.views.analysis.agent, "make_client", lambda: (_ for _ in ()).throw(AssertionError("must not call Claude")))
    monkeypatch.setattr(webapp.views.ask_module.agent, "make_client", lambda: (_ for _ in ()).throw(AssertionError("must not call Claude")))
    c = signed_in()
    t = token(c)
    assert c.post("/case/SH-1004/analyze", data={"csrf_token": t}).status_code == 429
    assert c.post("/ask", data={"csrf_token": t, "question": "what is late?"}).status_code == 429
    assert c.post("/analyze-top", data={"csrf_token": t, "n": "1"}).status_code in (200, 429)
    api = c.post("/api/shipments/SH-1004/analyze", headers={"X-CSRF-Token": t})
    assert api.status_code == 429 and "switched off" in api.get_json()["error"]
    assert c.get("/api/summary").get_json()["analyses_remaining"] == 0 and limits.used() == 0
    for value in ("0", "no", "OFF"):
        monkeypatch.setenv("MODEL_CALLS_ENABLED", value)
        assert not limits.enabled()
    monkeypatch.setenv("MODEL_CALLS_ENABLED", "true")
    assert limits.enabled()


def test_the_ask_page_shows_the_daily_count():
    html = signed_in().get("/ask").get_data(as_text=True)
    assert f"{config.DAILY_CALLS_PER_SESSION} of {config.DAILY_CALLS_PER_SESSION}" in html and "today" in html


# ------------------------------------------------------------------ bug fixes: clean validation of numbers
@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "-5", "abc", "", "1e999", "99999"])
def test_clock_hours_reject_non_numbers_negatives_and_absurd_values(bad):
    c = signed_in()
    assert c.post("/simulate/advance", data={"csrf_token": token(c), "hours": bad}).status_code == 400
    assert "Simulated clock" not in c.get("/").get_data(as_text=True)


def test_replay_and_api_bodies_with_the_wrong_shape_get_400_not_500():
    c = signed_in()
    t = token(c)
    c.post("/simulate/replay/start", data={"csrf_token": t})
    for body in ({"hours": "nan"}, {"hours": [1]}, {"hours": {"a": 1}}, {"hours": -3}, {"hours": 0}, {"hours": 10 ** 9}, {"hours": True and "x"}):
        r = c.post("/api/replay/step", json=body, headers={"X-CSRF-Token": t})
        assert r.status_code == 400, body
    assert c.post("/api/replay/step", json=[1, 2], headers={"X-CSRF-Token": t}).status_code == 400
    assert c.post("/api/replay/step", json={"hours": 6}, headers={"X-CSRF-Token": t}).status_code == 200


def test_healthz_is_public_and_never_calls_claude(monkeypatch):
    monkeypatch.setattr(webapp.views.analysis.agent, "make_client", lambda: (_ for _ in ()).throw(AssertionError("no Claude")))
    r = client().get("/healthz")
    assert r.status_code == 200 and r.get_data(as_text=True) == "ok"


def test_header_shows_the_calls_left_today_and_a_cap_stop_is_logged_as_info_not_an_error():
    import logging
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logger = logging.getLogger("copilot.limits")
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        c = signed_in()
        assert f"Claude calls left today: {config.DAILY_CALLS_PER_SESSION}" in c.get("/").get_data(as_text=True)
        limits.set_used(config.DAILY_CALLS_PER_SESSION)
        assert not limits.reserve()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    assert [r.levelname for r in records] == ["INFO"] and "daily cap reached" in records[0].getMessage()
    assert "calls left today" not in client().get("/login").get_data(as_text=True)


def test_expired_and_ended_sessions_are_cleaned_up_on_the_next_sign_in():
    old = signed_in()
    old.post("/simulate/advance", data={"csrf_token": token(old), "hours": "3"})
    ended = signed_in()
    ended.post("/logout", data={"csrf_token": token(ended)})
    with store.connect() as conn:
        conn.execute("UPDATE sessions SET expires_epoch = ? WHERE rowid = (SELECT MIN(rowid) FROM sessions)", (time.time() - 5,))
    signed_in()
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1           # only the new one is left
        assert conn.execute("SELECT COUNT(*) FROM session_state").fetchone()[0] == 0     # and so is the clock saved for the expired one
