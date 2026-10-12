import base64

import app as webapp
import pytest


@pytest.fixture(autouse=True)
def configure_demo_login(monkeypatch):
    monkeypatch.setenv("DEMO_USERNAME", "pursuit-demo")
    monkeypatch.setenv("DEMO_PASSWORD", "test-only-password")
    monkeypatch.setattr(webapp, "_classification_count", 0)


def auth_headers():
    token = base64.b64encode(b"pursuit-demo:test-only-password").decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_home_page_shows_lookup_actions():
    webapp.app.config.update(TESTING=True)
    response = webapp.app.test_client().get("/", headers=auth_headers())

    assert response.status_code == 200
    assert b"Prepare" in response.data
    assert b"Classify with Claude" in response.data


def test_prepare_route_displays_complete_local_shipment():
    webapp.app.config.update(TESTING=True)
    response = webapp.app.test_client().post(
        "/", data={"tracking_number": "SHIP-1001", "action": "prepare"}, headers=auth_headers()
    )

    assert response.status_code == 200
    assert b"READY FOR AI" in response.data
    assert b"Boston, MA" in response.data


def test_prepare_route_displays_manual_review_for_incomplete_shipment():
    webapp.app.config.update(TESTING=True)
    response = webapp.app.test_client().post(
        "/", data={"tracking_number": "SHIP-1003", "action": "prepare"}, headers=auth_headers()
    )

    assert response.status_code == 200
    assert b"NEEDS MANUAL REVIEW" in response.data
    assert b"Missing or blank required field: origin" in response.data


def test_classify_route_displays_mocked_model_result(monkeypatch):
    webapp.app.config.update(TESTING=True)
    monkeypatch.setattr(
        webapp,
        "classify_if_ready",
        lambda _tracking_number: {
            "decision": "NEEDS_ATTENTION",
            "shipment": {
                "tracking_number": "SHIP-1002",
                "status": "delayed",
                "origin": "Los Angeles, CA",
                "destination": "New York, NY",
                "expected_delivery": "2026-10-13",
                "last_update": "2026-10-12T08:45:00-04:00",
                "exception": "Weather disruption at the regional hub",
            },
            "reasons": ["Weather disruption caused a delay."],
        },
    )

    response = webapp.app.test_client().post(
        "/", data={"tracking_number": "SHIP-1002", "action": "classify"}, headers=auth_headers()
    )

    assert response.status_code == 200
    assert b"NEEDS ATTENTION" in response.data
    assert b"Weather disruption caused a delay." in response.data


def test_classify_route_shows_missing_key_error_without_traceback(monkeypatch):
    webapp.app.config.update(TESTING=True)

    def missing_key(_tracking_number):
        raise RuntimeError("ANTHROPIC_API_KEY is missing; check the local .env file.")

    monkeypatch.setattr(webapp, "classify_if_ready", missing_key)

    response = webapp.app.test_client().post(
        "/", data={"tracking_number": "SHIP-1001", "action": "classify"}, headers=auth_headers()
    )

    assert response.status_code == 200
    assert b"Could not classify shipment" in response.data
    assert b"ANTHROPIC_API_KEY is missing" in response.data
    assert b"Traceback" not in response.data


def test_home_page_requires_login():
    response = webapp.app.test_client().get("/")

    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers


def test_app_fails_closed_if_demo_login_is_not_configured(monkeypatch):
    monkeypatch.delenv("DEMO_USERNAME", raising=False)
    monkeypatch.delenv("DEMO_PASSWORD", raising=False)

    response = webapp.app.test_client().get("/")

    assert response.status_code == 503
    assert b"Demo access is not configured" in response.data


def test_classification_cap_stops_before_model_call(monkeypatch):
    monkeypatch.setattr(webapp, "_classification_count", webapp.MAX_CLASSIFICATIONS)

    def unexpected_call(_tracking_number):
        raise AssertionError("The model must not run after the demo cap is reached.")

    monkeypatch.setattr(webapp, "classify_if_ready", unexpected_call)
    response = webapp.app.test_client().post(
        "/", data={"tracking_number": "SHIP-1001", "action": "classify"}, headers=auth_headers()
    )

    assert response.status_code == 429
    assert b"classification limit" in response.data
