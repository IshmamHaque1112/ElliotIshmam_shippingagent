"""Small SQLite store (standard library only): saved cases, Claude runs, dispositions, feedback, settings.

Cases are never overwritten; every analysis is a new row. The path comes from config.db_path() at call time.
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

import config

SCHEMA_VERSION = 1
DISPOSITIONS = ("new", "acknowledged", "in_progress", "resolved", "dismissed")
VERDICTS = ("useful", "not useful", "wrong")
_initialized: set[str] = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases(
  id INTEGER PRIMARY KEY AUTOINCREMENT, shipment_id TEXT NOT NULL, dataset TEXT NOT NULL, sim_now_utc TEXT NOT NULL,
  state TEXT NOT NULL, severity TEXT, exposure_usd REAL, priority_score REAL, narrative TEXT NOT NULL, model TEXT,
  run_status TEXT, input_hash TEXT NOT NULL, case_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claude_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER, shipment_id TEXT, model TEXT, status TEXT NOT NULL,
  tool_calls INTEGER, input_tokens INTEGER, output_tokens INTEGER, est_cost_usd REAL, duration_ms INTEGER,
  retries INTEGER, rejection_reason TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dispositions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL, status TEXT NOT NULL, owner TEXT, note TEXT,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS feedback(
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL, verdict TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS cases_shipment ON cases(shipment_id, id);
CREATE INDEX IF NOT EXISTS disp_case ON dispositions(case_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # wall-clock time for audit rows only


def connect() -> sqlite3.Connection:
    path = str(config.db_path())
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    if path not in _initialized:
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        _initialized.add(path)
    return conn


def _rows(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _insert(sql: str, args: tuple) -> int:
    with closing(connect()) as conn:
        cur = conn.execute(sql, args)
        conn.commit()
        return int(cur.lastrowid)


# ---- cases
def save_case(case: dict[str, Any], *, dataset: str, sim_now_utc: str, input_hash: str, model: str | None) -> int:
    fin = case["financial"]
    return _insert(
        "INSERT INTO cases(shipment_id,dataset,sim_now_utc,state,severity,exposure_usd,priority_score,narrative,model,"
        "run_status,input_hash,case_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (case["shipment_id"], dataset, sim_now_utc, case["state"], case["severity"], fin["total_exposure_usd"],
         fin["priority_score"], case["narrative"], model, case["run_status"], input_hash, json.dumps(case), _now()),
    )


def _case(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    row["case"] = json.loads(row.pop("case_json"))
    return row


def get_case(case_id: int) -> dict[str, Any] | None:
    rows = _rows("SELECT * FROM cases WHERE id=?", (case_id,))
    return _case(rows[0]) if rows else None


def latest_case_for(shipment_id: str, dataset: str) -> dict[str, Any] | None:
    rows = _rows("SELECT * FROM cases WHERE shipment_id=? AND dataset=? ORDER BY id DESC LIMIT 1", (shipment_id, dataset))
    return _case(rows[0]) if rows else None


def cases_for(shipment_id: str, dataset: str) -> list[dict[str, Any]]:
    return [_case(r) for r in _rows("SELECT * FROM cases WHERE shipment_id=? AND dataset=? ORDER BY id DESC", (shipment_id, dataset))]


def list_cases(dataset: str, *, shipment_id: str | None = None, status: str | None = None, severity: str | None = None,
               latest_only: bool = False) -> list[dict[str, Any]]:
    """Cases newest first, each with its current disposition (default 'new')."""
    sql, args = "SELECT * FROM cases WHERE dataset=?", [dataset]
    if shipment_id:
        sql += " AND shipment_id=?"; args.append(shipment_id.upper())
    if severity:
        sql += " AND severity=?"; args.append(severity.upper())
    if latest_only:
        sql += " AND id IN (SELECT MAX(id) FROM cases WHERE dataset=? GROUP BY shipment_id)"; args.append(dataset)
    out = [_case(r) for r in _rows(sql + " ORDER BY id DESC", tuple(args))]
    current = latest_dispositions([c["id"] for c in out])
    for c in out:
        c["disposition"] = current.get(c["id"], {"status": "new", "owner": None, "note": None, "created_at": c["created_at"]})
    return [c for c in out if not status or c["disposition"]["status"] == status]


# ---- runs
def add_run(**fields: Any) -> int:
    cols = ("case_id", "shipment_id", "model", "status", "tool_calls", "input_tokens", "output_tokens", "est_cost_usd",
            "duration_ms", "retries", "rejection_reason")
    values = tuple(fields.get(c) for c in cols)
    return _insert(f"INSERT INTO claude_runs({','.join(cols)},created_at) VALUES({','.join('?' * len(cols))},?)", values + (_now(),))


def list_runs(limit: int = 500) -> list[dict[str, Any]]:
    return _rows("SELECT * FROM claude_runs ORDER BY id DESC LIMIT ?", (limit,))


def run_totals() -> dict[str, Any]:
    rows = _rows("SELECT status, COUNT(*) n, COALESCE(SUM(input_tokens),0) i, COALESCE(SUM(output_tokens),0) o, "
                 "SUM(est_cost_usd) c FROM claude_runs GROUP BY status")
    return {"runs": sum(r["n"] for r in rows), "by_status": {r["status"]: r["n"] for r in rows},
            "input_tokens": sum(r["i"] for r in rows), "output_tokens": sum(r["o"] for r in rows),
            "est_cost_usd": sum(r["c"] for r in rows if r["c"] is not None) if any(r["c"] is not None for r in rows) else None}


def runs_by_model() -> list[dict[str, Any]]:
    return _rows("SELECT model, COUNT(*) runs, SUM(status='fallback') fallbacks, SUM(status='error') errors, "
                 "COALESCE(SUM(input_tokens),0) input_tokens, COALESCE(SUM(output_tokens),0) output_tokens, "
                 "SUM(est_cost_usd) est_cost_usd, AVG(duration_ms) avg_ms FROM claude_runs GROUP BY model ORDER BY runs DESC")


# ---- dispositions and feedback
def add_disposition(case_id: int, status: str, owner: str | None = None, note: str | None = None) -> int:
    if status not in DISPOSITIONS:
        raise ValueError(f"status must be one of {DISPOSITIONS}")
    return _insert("INSERT INTO dispositions(case_id,status,owner,note,created_at) VALUES(?,?,?,?,?)",
                   (case_id, status, (owner or "")[:80] or None, (note or "")[:1000] or None, _now()))


def latest_dispositions(case_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not case_ids:
        return {}
    marks = ",".join("?" * len(case_ids))
    rows = _rows(f"SELECT * FROM dispositions WHERE id IN (SELECT MAX(id) FROM dispositions WHERE case_id IN ({marks}) GROUP BY case_id)",
                 tuple(case_ids))
    return {r["case_id"]: r for r in rows}


def disposition_history(case_id: int) -> list[dict[str, Any]]:
    return _rows("SELECT * FROM dispositions WHERE case_id=? ORDER BY id DESC", (case_id,))


def add_feedback(case_id: int, verdict: str, note: str | None = None) -> int:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    return _insert("INSERT INTO feedback(case_id,verdict,note,created_at) VALUES(?,?,?,?)", (case_id, verdict, (note or "")[:1000] or None, _now()))


def feedback_for(case_id: int) -> list[dict[str, Any]]:
    return _rows("SELECT * FROM feedback WHERE case_id=? ORDER BY id DESC", (case_id,))


# ---- settings
def get_setting(key: str, default: Any = None) -> Any:
    rows = _rows("SELECT value FROM settings WHERE key=?", (key,))
    return json.loads(rows[0]["value"]) if rows else default


def set_setting(key: str, value: Any) -> None:
    with closing(connect()) as conn:
        conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        conn.commit()
