"""Small SQLite store (standard library only): saved cases, Claude runs, dispositions, feedback, settings.

Cases are never overwritten; every analysis is a new row. The path comes from config.db_path() at call time.
"""

import json
import sqlite3
import time
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
CREATE TABLE IF NOT EXISTS traces(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, case_id INTEGER, shipment_id TEXT NOT NULL, trace_json TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS traces_case ON traces(case_id);
CREATE TABLE IF NOT EXISTS playbook_ticks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL, step_id TEXT NOT NULL, done INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ticks_case ON playbook_ticks(case_id, step_id, id);
CREATE TABLE IF NOT EXISTS approval_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL, draft TEXT,
  actor TEXT, note TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS approvals_case ON approval_events(case_id, id);
CREATE TABLE IF NOT EXISTS asks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, question TEXT NOT NULL, answer TEXT, shipment_ids TEXT, status TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  sid TEXT PRIMARY KEY, username TEXT NOT NULL, dataset TEXT NOT NULL, created_at TEXT NOT NULL, expires_epoch REAL NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS session_state(
  sid TEXT NOT NULL, dataset TEXT NOT NULL, sim_json TEXT NOT NULL, PRIMARY KEY(sid, dataset));
CREATE TABLE IF NOT EXISTS login_attempts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT NOT NULL, username TEXT NOT NULL, ok INTEGER NOT NULL, at_epoch REAL NOT NULL);
CREATE INDEX IF NOT EXISTS login_ip ON login_attempts(ip, at_epoch);
CREATE TABLE IF NOT EXISTS usage_daily(day TEXT NOT NULL, scope TEXT NOT NULL, calls INTEGER NOT NULL, PRIMARY KEY(day, scope));
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
        conn.execute("PRAGMA journal_mode=WAL")   # several gunicorn workers share this file
        if "kind" not in {r["name"] for r in conn.execute("PRAGMA table_info(claude_runs)")}:  # databases made before E3
            conn.execute("ALTER TABLE claude_runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'case'")
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
            "duration_ms", "retries", "rejection_reason", "kind")
    fields.setdefault("kind", "case")  # 'case' = a case-file analysis, 'ask' = a question to the queue
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


# ---- ask the queue
def save_ask(question: str, answer: str | None, shipment_ids: list[str], status: str, run_id: int | None) -> int:
    return _insert("INSERT INTO asks(run_id,question,answer,shipment_ids,status,created_at) VALUES(?,?,?,?,?,?)",
                   (run_id, question, answer, json.dumps(shipment_ids), status, _now()))


def list_asks(limit: int = 20) -> list[dict[str, Any]]:
    rows = _rows("SELECT * FROM asks ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["shipment_ids"] = json.loads(r["shipment_ids"] or "[]")
    return rows


# ---- agent traces (one row per analysis; error runs have no case)
def save_trace(trace: dict[str, Any], *, shipment_id: str, run_id: int | None = None, case_id: int | None = None) -> int:
    return _insert("INSERT INTO traces(run_id,case_id,shipment_id,trace_json,created_at) VALUES(?,?,?,?,?)",
                   (run_id, case_id, shipment_id, json.dumps(trace), _now()))


def trace_for_case(case_id: int) -> dict[str, Any] | None:
    rows = _rows("SELECT * FROM traces WHERE case_id=? ORDER BY id DESC LIMIT 1", (case_id,))
    if not rows:
        return None
    return {**json.loads(rows[0]["trace_json"]), "run_id": rows[0]["run_id"], "recorded_at": rows[0]["created_at"]}


def trace_for_run(run_id: int) -> dict[str, Any] | None:
    rows = _rows("SELECT * FROM traces WHERE run_id=? ORDER BY id DESC LIMIT 1", (run_id,))
    return json.loads(rows[0]["trace_json"]) if rows else None


# ---- playbook ticks (append-only; the latest row per step is the current state)
def playbook_state(case_id: int) -> dict[str, bool]:
    rows = _rows("SELECT step_id, done FROM playbook_ticks WHERE id IN (SELECT MAX(id) FROM playbook_ticks WHERE case_id=? GROUP BY step_id)", (case_id,))
    return {r["step_id"]: bool(r["done"]) for r in rows}


def set_playbook(case_id: int, done_steps: set[str], all_steps: list[str]) -> int:
    """Append a row for each step whose state changed. Returns how many rows were added."""
    current, added = playbook_state(case_id), 0
    for step in all_steps:
        want = step in done_steps
        if current.get(step, False) != want:
            _insert("INSERT INTO playbook_ticks(case_id,step_id,done,created_at) VALUES(?,?,?,?)", (case_id, step, int(want), _now()))
            added += 1
    return added


# ---- approval requests (text for a human plus a status a human sets; append-only history, nothing is ever sent)
def approval_history(case_id: int) -> list[dict[str, Any]]:
    return _rows("SELECT * FROM approval_events WHERE case_id=? ORDER BY id DESC", (case_id,))


def approval_current(case_id: int) -> dict[str, Any] | None:
    """Latest status, with the draft text carried forward from the 'drafted' row."""
    rows = _rows("SELECT * FROM approval_events WHERE case_id=? ORDER BY id", (case_id,))
    if not rows:
        return None
    current = dict(rows[-1])
    current["draft"] = next((r["draft"] for r in reversed(rows) if r["draft"]), None)
    return current


def add_approval_event(case_id: int, role: str, status: str, draft: str | None = None, actor: str | None = None, note: str | None = None) -> int:
    return _insert("INSERT INTO approval_events(case_id,role,status,draft,actor,note,created_at) VALUES(?,?,?,?,?,?,?)",
                   (case_id, role, status, draft, (actor or "")[:80] or None, (note or "")[:1000] or None, _now()))


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


def dispositions_for_dataset(dataset: str) -> list[dict[str, Any]]:
    return _rows("SELECT d.* FROM dispositions d JOIN cases c ON c.id=d.case_id WHERE c.dataset=? ORDER BY d.id", (dataset,))


def max_ids() -> dict[str, int]:
    row = _rows("SELECT (SELECT COALESCE(MAX(id),0) FROM cases) c, (SELECT COALESCE(MAX(id),0) FROM dispositions) d")[0]
    return {"case_id": row["c"], "disposition_id": row["d"]}


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


# ---- sessions (sign-in). Wall-clock time is fine here: these rows are about people, not about shipments.
def create_session(sid: str, username: str, dataset: str, lifetime_h: float) -> None:
    with closing(connect()) as conn:   # tidy up: expired or ended sessions and their saved clocks
        conn.execute("DELETE FROM session_state WHERE sid IN (SELECT sid FROM sessions WHERE expires_epoch<=? OR revoked=1)", (time.time(),))
        conn.execute("DELETE FROM sessions WHERE expires_epoch<=? OR revoked=1", (time.time(),))
        conn.commit()
    _insert("INSERT INTO sessions(sid,username,dataset,created_at,expires_epoch,revoked) VALUES(?,?,?,?,?,0)",
            (sid, username, dataset, _now(), time.time() + lifetime_h * 3600))


def get_session(sid: str) -> dict[str, Any] | None:
    """The session if it exists, is not revoked and has not expired."""
    rows = _rows("SELECT * FROM sessions WHERE sid=? AND revoked=0 AND expires_epoch>?", (sid, time.time()))
    return rows[0] if rows else None


def set_session_dataset(sid: str, dataset: str) -> None:
    with closing(connect()) as conn:
        conn.execute("UPDATE sessions SET dataset=? WHERE sid=?", (dataset, sid))
        conn.commit()


def revoke_session(sid: str) -> None:
    with closing(connect()) as conn:
        conn.execute("UPDATE sessions SET revoked=1 WHERE sid=?", (sid,))
        conn.execute("DELETE FROM session_state WHERE sid=?", (sid,))
        conn.commit()


def get_session_sim(sid: str, dataset: str) -> dict[str, Any] | None:
    rows = _rows("SELECT sim_json FROM session_state WHERE sid=? AND dataset=?", (sid, dataset))
    return json.loads(rows[0]["sim_json"]) if rows else None


def set_session_sim(sid: str, dataset: str, state: dict[str, Any]) -> None:
    with closing(connect()) as conn:
        conn.execute("INSERT INTO session_state(sid,dataset,sim_json) VALUES(?,?,?) ON CONFLICT(sid,dataset) DO UPDATE SET sim_json=excluded.sim_json",
                     (sid, dataset, json.dumps(state, sort_keys=True)))
        conn.commit()


# ---- failed-login throttle
def record_login(ip: str, username: str, ok: bool) -> None:
    with closing(connect()) as conn:
        conn.execute("INSERT INTO login_attempts(ip,username,ok,at_epoch) VALUES(?,?,?,?)", (ip, username[:80], int(ok), time.time()))
        if ok:   # a good sign-in clears that address's earlier failures
            conn.execute("DELETE FROM login_attempts WHERE ip=? AND ok=0", (ip,))
        conn.execute("DELETE FROM login_attempts WHERE at_epoch<?", (time.time() - 86400,))
        conn.commit()


def recent_failures(ip: str, username: str, window_s: float) -> tuple[int, int]:
    since = time.time() - window_s
    rows = _rows("SELECT (SELECT COUNT(*) FROM login_attempts WHERE ok=0 AND ip=? AND at_epoch>?) AS by_ip, "
                 "(SELECT COUNT(*) FROM login_attempts WHERE ok=0 AND username=? AND at_epoch>?) AS by_user", (ip, since, username[:80], since))
    return int(rows[0]["by_ip"]), int(rows[0]["by_user"])


# ---- daily Claude-call counters, shared by every worker on this database
def reserve_usage(day: str, scopes: list[tuple[str, int]]) -> str | None:
    """Take one call from every scope in one transaction. Returns None on success, else the scope that is full."""
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for scope, cap in scopes:
                row = conn.execute("SELECT calls FROM usage_daily WHERE day=? AND scope=?", (day, scope)).fetchone()
                if (row["calls"] if row else 0) >= cap:
                    conn.rollback()
                    return scope
            for scope, _cap in scopes:
                conn.execute("INSERT INTO usage_daily(day,scope,calls) VALUES(?,?,1) ON CONFLICT(day,scope) DO UPDATE SET calls=calls+1", (day, scope))
            conn.commit()
            return None
        except Exception:
            conn.rollback()
            raise


def release_usage(day: str, scopes: list[str]) -> None:
    with closing(connect()) as conn:
        for scope in scopes:
            conn.execute("UPDATE usage_daily SET calls=MAX(0,calls-1) WHERE day=? AND scope=?", (day, scope))
        conn.commit()


def usage_count(day: str, scope: str) -> int:
    rows = _rows("SELECT calls FROM usage_daily WHERE day=? AND scope=?", (day, scope))
    return int(rows[0]["calls"]) if rows else 0


def set_usage(day: str, scope: str, calls: int) -> None:
    with closing(connect()) as conn:
        conn.execute("INSERT INTO usage_daily(day,scope,calls) VALUES(?,?,?) ON CONFLICT(day,scope) DO UPDATE SET calls=excluded.calls", (day, scope, calls))
        conn.commit()


def purge_usage_before(day: str) -> None:
    with closing(connect()) as conn:
        conn.execute("DELETE FROM usage_daily WHERE day<?", (day,))
        conn.commit()
