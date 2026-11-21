"""Clinician decisions on a briefing: bridge to core.ledger until Teammate B's ledger exists.

Owner: A (Teammate A). Built in prompt A4.

Public interface:
    record_decision(decision) -> "ledger" | "pending"
    list_pending() -> list[Decision]               newest first
    briefing_decision(result, action, role, reason, clock) -> Decision

If core.ledger has a callable `record`, decisions go there. Otherwise one JSON line per decision is
appended to logs/decisions_pending.jsonl (env DECISIONS_PENDING_PATH overrides). core/ledger.py is
never edited from here. Once the ledger exists, import the pending file with
list_pending() + ledger.record() and delete it (see changes_from_baseline.md).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pandas as pd

from clinical.artefact import to_iso
from core import ledger
from core.contracts import Decision, to_dict

logger = logging.getLogger(__name__)

DEFAULT_PENDING = Path(__file__).resolve().parent.parent / "logs" / "decisions_pending.jsonl"


def _pending_path() -> Path:
    return Path(os.environ.get("DECISIONS_PENDING_PATH") or DEFAULT_PENDING)


def record_decision(decision: Decision) -> str:
    """Write one decision. Returns "ledger" or "pending" (where it went)."""
    record = getattr(ledger, "record", None)
    if callable(record):
        record(decision)
        logger.info("decision %s recorded in the ledger", decision.item_id)
        return "ledger"
    path = _pending_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(to_dict(decision), ensure_ascii=False) + "\n")
    logger.info("decision %s appended to %s", decision.item_id, path.name)
    return "pending"


def list_pending() -> list[Decision]:
    """Decisions in the pending file, newest first (latest written first for equal timestamps)."""
    path = _pending_path()
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        rows = [Decision(**json.loads(line)) for line in f if line.strip()]
    order = sorted(range(len(rows)), key=lambda i: (rows[i].ts, i), reverse=True)
    return [rows[i] for i in order]


def _cited_ids(obj) -> set[str]:
    if isinstance(obj, dict):
        ids = set(obj["evidence_ids"]) if isinstance(obj.get("evidence_ids"), list) else set()
        return ids.union(*(_cited_ids(v) for v in obj.values()))
    if isinstance(obj, list):
        return set().union(*(_cited_ids(v) for v in obj)) if obj else set()
    return set()


def briefing_decision(result: dict, action: str, role: str, reason: str, clock: str | pd.Timestamp) -> Decision:
    """Decision for the briefing shown (`result` from get_briefing). For "edit", `reason` is the edited text."""
    pid = result["patient_id"]
    cut = pd.Timestamp(clock)
    shown = {"source": result["source"], "briefing": result["briefing"]}
    return Decision(
        ts=to_iso(cut),
        patient_id=pid,
        item_type="briefing",
        item_id=f"BRF-{pid}-{cut:%H%M}",
        ai_suggestion=json.dumps(shown, ensure_ascii=False, separators=(",", ":")),
        clinician_action=action,  # type: ignore[arg-type]
        clinician_role=role,
        reason=reason,
        evidence_ids=sorted(_cited_ids(result["briefing"])),
    )


if __name__ == "__main__":
    from ai.factsheet import get_briefing

    _res = get_briefing("P-002", "2026-11-21T13:00", live=False)
    _d = briefing_decision(_res, "approve", "physician", "Agree, will call the patient.", "2026-11-21T13:00")
    print({**to_dict(_d), "ai_suggestion": _d.ai_suggestion[:80] + "..."})
    print("target:", "ledger" if callable(getattr(ledger, "record", None)) else f"pending file {_pending_path()}")
