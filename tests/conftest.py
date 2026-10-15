import pytest

import limits
import pipeline
import sim


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Every test gets its own database, a clean simulation, an empty cache, a fresh cap and demo secrets."""
    monkeypatch.setenv("RUNS_DB", str(tmp_path / "runs.db"))
    monkeypatch.setenv("DEMO_USERNAME", "pursuit-demo")
    monkeypatch.setenv("DEMO_PASSWORD", "test-only-password")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.delenv("DATASET", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    sim.reset()
    pipeline.clear_cache()
    import app as webapp
    webapp._loaded_for = None
    webapp.app.config.update(TESTING=True)
