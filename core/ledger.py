"""SQLite decision ledger and Claude response cache (ledger.db).

Owner: B (Teammate B). Built in prompts B3 (back-end) and B5 (page).

Public interface (stable, used by A and by the pages):
    record(decision: Decision) -> int
    list_decisions(patient_id=None, item_type=None) -> list[Decision]   # newest first
    cache_get(key: str) -> dict | None
    cache_put(key: str, response: dict, model: str, prompt_version: str) -> None   # upsert

The DB path is env LEDGER_DB or <repo root>/ledger.db. A new connection is opened per call.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import get_args

from core.contracts import Action, Decision

logger = logging.getLogger(__name__)

# contracts.py uses `from __future__ import annotations`, so the Literal is a string there; mirror it here.
_ITEM_TYPES = {"briefing", "capacity_plan", "message_reply", "signoff", "artefact"}
_ACTIONS = set(get_args(Action))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, patient_id TEXT NOT NULL, item_type TEXT NOT NULL, item_id TEXT NOT NULL,
    ai_suggestion TEXT, clinician_action TEXT NOT NULL, clinician_role TEXT, reason TEXT,
    evidence_ids TEXT NOT NULL DEFAULT '[]', prompt_version TEXT, recorded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_cache (
    cache_key TEXT PRIMARY KEY, model TEXT, prompt_version TEXT, response_json TEXT NOT NULL,
    created_at TEXT NOT NULL);
"""


def db_path() -> Path:
    return Path(os.environ.get("LEDGER_DB") or Path(__file__).resolve().parent.parent / "ledger.db")


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def record(decision: Decision, prompt_version: str | None = None) -> int:
    """Insert a decision; returns the row id. Raises ValueError on an unknown item_type/action."""
    if decision.item_type not in _ITEM_TYPES:
        raise ValueError(f"invalid item_type {decision.item_type!r}")
    if decision.clinician_action not in _ACTIONS:
        raise ValueError(f"invalid clinician_action {decision.clinician_action!r}")
    with closing(_connect()) as con, con:
        cur = con.execute(
            "INSERT INTO decisions (ts, patient_id, item_type, item_id, ai_suggestion, clinician_action,"
            " clinician_role, reason, evidence_ids, prompt_version, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (decision.ts, decision.patient_id, decision.item_type, decision.item_id, decision.ai_suggestion,
             decision.clinician_action, decision.clinician_role, decision.reason,
             json.dumps(list(decision.evidence_ids), ensure_ascii=False), prompt_version, _now()))
        rid = int(cur.lastrowid)
    logger.info("ledger: recorded #%s %s %s %s", rid, decision.item_type, decision.item_id, decision.clinician_action)
    return rid


def list_decisions(patient_id: str | None = None, item_type: str | None = None) -> list[Decision]:
    """Newest first (by row id), optionally filtered."""
    where, args = [], []
    if patient_id is not None:
        where.append("patient_id = ?")
        args.append(patient_id)
    if item_type is not None:
        where.append("item_type = ?")
        args.append(item_type)
    sql = "SELECT * FROM decisions" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC"
    with closing(_connect()) as con:
        rows = con.execute(sql, args).fetchall()
    return [Decision(ts=r["ts"], patient_id=r["patient_id"], item_type=r["item_type"], item_id=r["item_id"],
                     ai_suggestion=r["ai_suggestion"] or "", clinician_action=r["clinician_action"],
                     clinician_role=r["clinician_role"] or "", reason=r["reason"] or "",
                     evidence_ids=json.loads(r["evidence_ids"])) for r in rows]


def list_rows(patient_id: str | None = None, item_type: str | None = None,
              action: str | None = None) -> list[dict]:
    """All columns as dicts (evidence_ids as a list), newest first by (ts desc, id desc), optionally filtered."""
    where, args = [], []
    for col, val in (("patient_id", patient_id), ("item_type", item_type), ("clinician_action", action)):
        if val is not None:
            where.append(f"{col} = ?")
            args.append(val)
    sql = ("SELECT * FROM decisions" + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY ts DESC, id DESC")
    with closing(_connect()) as con:
        rows = con.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["evidence_ids"] = json.loads(d["evidence_ids"] or "[]")
        out.append(d)
    return out


def decision_key(d: Decision | dict) -> tuple:
    """Identity used to de-duplicate a decision across the ledger and the pending file."""
    get = d.get if isinstance(d, dict) else (lambda k: getattr(d, k))
    return (get("ts"), get("item_id"), get("clinician_action"), get("reason") or "")


def import_pending(path: str | Path | None = None) -> int:
    """Record every pending-file decision that is not yet in the ledger; return how many were recorded.

    After a successful import (count > 0) the file is renamed to <name>.imported-<YYYYmmddHHMM>, never deleted.
    """
    from clinical import decisions as cd  # lazy: clinical.decisions imports this module

    src = Path(path) if path else cd._pending_path()
    if not src.exists():
        return 0
    pending = []
    with open(src, encoding="utf-8") as f:
        pending = [Decision(**json.loads(line)) for line in f if line.strip()]
    seen = {decision_key(r) for r in list_rows()}
    n = 0
    for d in sorted(pending, key=lambda x: x.ts):  # oldest first keeps ids in time order
        k = decision_key(d)
        if k in seen:
            continue
        record(d)
        seen.add(k)
        n += 1
    if n:
        src.rename(src.with_name(f"{src.name}.imported-{datetime.now():%Y%m%d%H%M}"))
        logger.info("ledger: imported %d pending decisions from %s", n, src.name)
    return n


def cache_get(key: str) -> dict | None:
    """Unused; the AI cache lives in ai/cache (see changes_from_baseline)."""
    with closing(_connect()) as con:
        row = con.execute("SELECT response_json FROM ai_cache WHERE cache_key = ?", (key,)).fetchone()
    return json.loads(row["response_json"]) if row else None


def cache_put(key: str, response: dict, model: str, prompt_version: str) -> None:
    """Insert or replace the cached response for key. Unused; the AI cache lives in ai/cache (see changes_from_baseline)."""
    with closing(_connect()) as con, con:
        con.execute("INSERT OR REPLACE INTO ai_cache (cache_key, model, prompt_version, response_json, created_at)"
                    " VALUES (?,?,?,?,?)",
                    (key, model, prompt_version, json.dumps(response, ensure_ascii=False), _now()))


if __name__ == "__main__":
    import sys
    import tempfile
    if "--import-pending" in sys.argv:
        print(f"imported {import_pending()} pending decision(s)")
        raise SystemExit(0)
    os.environ["LEDGER_DB"] = str(Path(tempfile.mkdtemp()) / "demo.db")
    record(Decision("2026-11-21T14:00", "P-002", "briefing", "BRF-P-002-1400", "Possible deterioration [F1]",
                    "approve", "On-call physician", "Agree – Åsa", ["F1", "N-004"]))
    cache_put("k", {"a": 1}, "model", "v1")
    for d in list_decisions():
        print(d)
    print(cache_get("k"))
