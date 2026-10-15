"""Checks the environment before the server starts. Prints problems and exits 1 if any are fatal.

Usage:  python scripts/preflight.py           (reads the environment; a .env file is loaded if python-dotenv is installed)
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def check(env: dict[str, str]) -> tuple[list[str], list[str]]:
    """(fatal problems, warnings). Pure, so it can be tested."""
    errors, warnings = [], []
    for name in ("DEMO_USERNAME", "DEMO_PASSWORD", "FLASK_SECRET_KEY"):
        if not env.get(name):
            errors.append(f"{name} is not set, so the app refuses every request")
    if env.get("FLASK_SECRET_KEY") and len(env["FLASK_SECRET_KEY"]) < 24:
        errors.append("FLASK_SECRET_KEY is shorter than 24 characters; use a long random value")
    if env.get("DEMO_PASSWORD") and len(env["DEMO_PASSWORD"]) < 12:
        warnings.append("DEMO_PASSWORD is shorter than 12 characters")
    if env.get("DEMO_PASSWORD") and env.get("DEMO_PASSWORD") in ("password", "demo", "test-only-password", "changeme"):
        errors.append("DEMO_PASSWORD is a well-known placeholder")
    if env.get("DATASET") and env["DATASET"].strip().lower() not in ("dev", "extended", "edge"):
        errors.append(f"DATASET={env['DATASET']!r} is not one of dev, extended, edge")
    calls_on = (env.get("MODEL_CALLS_ENABLED") or "true").strip().lower() not in ("0", "false", "no", "off")
    if calls_on and not env.get("ANTHROPIC_API_KEY"):
        warnings.append("ANTHROPIC_API_KEY is not set: Claude features will say so and every page still works from the rules")
    if calls_on and env.get("ANTHROPIC_API_KEY"):
        warnings.append("Claude calls are ON: set a monthly spend limit on this key in the Anthropic console, and use a separate key for the demo")
    if env.get("DATASET_FILE") or env.get("EVAL_LABELS_FILE") or env.get("FAULTS_FILE"):
        errors.append("DATASET_FILE, FAULTS_FILE and EVAL_LABELS_FILE are for private eval runs only; unset them on a server")
    if (env.get("COOKIE_SECURE") or "").strip().lower() not in ("1", "true", "yes"):
        warnings.append("COOKIE_SECURE is not true: set it when the site is served over https")
    if not env.get("TRUST_PROXY_HOPS"):
        warnings.append("TRUST_PROXY_HOPS is not set: behind a reverse proxy every visitor will look like the proxy to the login throttle")
    db = Path(env.get("RUNS_DB") or ROOT / "runs.db")
    folder = db.parent
    if not folder.exists():
        errors.append(f"the database folder {folder} does not exist")
    elif not os.access(folder, os.W_OK):
        errors.append(f"the database folder {folder} is not writable")
    elif str(folder).startswith(("/tmp", "/var/tmp")) or str(db) == str(ROOT / "runs.db"):
        warnings.append(f"the database {db} may not be on a persistent volume; sessions and the daily budget would be lost on restart")
    return errors, warnings


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    errors, warnings = check(dict(os.environ))
    for line in errors:
        print("ERROR  ", line)
    for line in warnings:
        print("warning", line)
    print("preflight:", "FAILED" if errors else "ok")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
