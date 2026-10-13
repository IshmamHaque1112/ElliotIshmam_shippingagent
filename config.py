"""Every tunable threshold in one place (PRD section 6). Rule logic never reads the real clock."""

from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DATASET_PATH = DATA_DIR / "shipments_dataset.json"
FAULT_SCHEDULE_PATH = DATA_DIR / "fault_schedule.json"
RUN_STATE_PATH = Path(__file__).parent / ".run_state.json"
FEEDBACK_PATH = Path(__file__).parent / "feedback.jsonl"

VIP_TIER = "VIP_ENTERPRISE"
VIP_MULTIPLIER = 1.25            # ranking only; never inflates dollar exposure
LINEHAUL_MAX_SCAN_GAP_H = 24     # dwell baseline when the last scan is a departure / in-transit scan
MARGIN_AT_RISK_H = 6             # promise minus projected delivery below this => at_risk
DRIFT_AT_RISK_COUNT = 2          # forward ETA revisions at or above this => at_risk
ETA_REVISION_MIN_SECONDS = 60    # smaller forward moves are noise
PROMISE_LOCAL_HOUR = 17          # a date-only promise means 17:00 local at the destination
SEVERITY_HIGH_USD = 20000
SEVERITY_MEDIUM_USD = 5000
IMMEDIATE_EXPOSURE_USD = 5000
IMMEDIATE_COUNTDOWN_MIN = 120

# Scan codes that describe time spent at a facility (so the facility's own baseline applies).
FACILITY_CODES = {"ARRIVED", "HELD", "EXCEPTION", "CUSTOMS_HOLD", "DELIVERY_ATTEMPTED"}

# Agent / fetch limits
FETCH_ATTEMPTS = 3
FETCH_BACKOFF_SECONDS = 0.5      # doubled after each failed attempt
MAX_TOOL_CALLS_PER_CASE = 8
ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5-5"
ANTHROPIC_TIMEOUT_SECONDS = 60.0
ANTHROPIC_MAX_RETRIES = 2
