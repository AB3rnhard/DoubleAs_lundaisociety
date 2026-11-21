"""Clinician decisions on a briefing or a message reply: written to core.ledger.

Owner: A (Teammate A). Built in prompt A4.

Public interface:
    record_decision(decision) -> "ledger" | "pending"
    list_pending() -> list[Decision]               newest first
    briefing_decision(result, action, role, reason, clock) -> Decision
    message_reply_decision(item, final_text, clock) -> Decision   nurse reply to an inbox item (A5)
    replied_message_ids() -> set[str]

Decisions go to core.ledger.record. The pending file (logs/decisions_pending.jsonl, env
DECISIONS_PENDING_PATH overrides) is only a safety fallback, used when core.ledger has no callable
`record`; it is harmless and normally stays empty. Rows left in it are imported with
`python -m core.ledger --import-pending` (idempotent; the file is renamed, never deleted).
`ai_suggestion` is JSON with `source`, `prompt_version` and the shown briefing / triage.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pandas as pd

from ai.schemas import BRIEFING_PROMPT_VERSION, TRIAGE_PROMPT_VERSION
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
    shown = {"source": result["source"], "prompt_version": BRIEFING_PROMPT_VERSION, "briefing": result["briefing"]}
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


def message_reply_decision(item: dict, final_text: str, clock: str | pd.Timestamp) -> Decision:
    """Decision for a nurse reply to one `ai.triage.inbox()` item. Sending only logs it, nothing reaches the patient."""
    triage = item["triage"]
    draft = triage["triage"]["draft_reply"]
    shown = {"source": triage["source"], "prompt_version": TRIAGE_PROMPT_VERSION, "triage": triage["triage"]}
    return Decision(
        ts=to_iso(pd.Timestamp(clock)),
        patient_id=item["patient_id"],
        item_type="message_reply",
        item_id=f"RPL-{item['message_id']}",
        ai_suggestion=json.dumps(shown, ensure_ascii=False, separators=(",", ":")),
        clinician_action="approve" if final_text.strip() == draft.strip() else "edit",
        clinician_role="nurse",
        reason=final_text,
        evidence_ids=[item["message_id"]],
    )


def replied_message_ids() -> set[str]:
    """Message ids that have a message_reply decision, in the ledger and in the pending file.

    Both are read: decisions written before the ledger existed stay in the pending file.
    """
    lister = getattr(ledger, "list_decisions", None)
    rows = (lister() if callable(lister) else []) + list_pending()
    return {str(d.item_id).removeprefix("RPL-") for d in rows if d.item_type == "message_reply"}


if __name__ == "__main__":
    from ai.factsheet import get_briefing

    _res = get_briefing("P-002", "2026-11-21T13:00", live=False)
    _d = briefing_decision(_res, "approve", "physician", "Agree, will call the patient.", "2026-11-21T13:00")
    print({**to_dict(_d), "ai_suggestion": _d.ai_suggestion[:80] + "..."})
    from ai.triage import inbox

    _item = inbox("2026-11-21T13:00")[0]
    _r = message_reply_decision(_item, _item["triage"]["triage"]["draft_reply"], "2026-11-21T13:00")
    print("reply:", _r.item_id, _r.clinician_action, _r.clinician_role, _r.evidence_ids)
    print("replied ids:", sorted(replied_message_ids()))
    print("target:", "ledger" if callable(getattr(ledger, "record", None)) else f"pending file {_pending_path()}")
