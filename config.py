"""Every tunable threshold in one place (PRD section 6). Rule logic never reads the real clock."""

import os
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
RUN_STATE_PATH = ROOT / ".run_state.json"

# Which static fictional dataset is active: DATASET=dev (12 shipments) or DATASET=extended (60). Read at call time.
DATASET_FILES = {
    "dev": ("shipments_dataset.json", "fault_schedule.json"),
    "extended": ("shipments_extended.json", "fault_schedule_extended.json"),
}


def dataset_name() -> str:
    name = (os.environ.get("DATASET") or "dev").strip().lower()
    if name not in DATASET_FILES:
        raise ValueError(f"DATASET must be one of {sorted(DATASET_FILES)}, got {name!r}")
    return name


def dataset_path(name: str | None = None) -> Path:
    return DATA_DIR / DATASET_FILES[name or dataset_name()][0]


def fault_schedule_path(name: str | None = None) -> Path:
    return DATA_DIR / DATASET_FILES[name or dataset_name()][1]


def db_path() -> Path:
    return Path(os.environ.get("RUNS_DB") or ROOT / "runs.db")


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

# Estimated price per million tokens: {"model-name": (input_usd, output_usd)}. Left empty on purpose:
# fill in from Anthropic's published pricing for the models you use. Costs shown in the UI are estimates.
PRICES: dict[str, tuple[float, float]] = {}

WEB_PAGE_SIZE = 25
