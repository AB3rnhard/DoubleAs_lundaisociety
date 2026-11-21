"""Pure helpers for the Ledger page (no Streamlit): merge ledger + pending rows, format, CSV.

Owner: B (Teammate B). Built in prompt B5.

Public interface: load_rows, short_suggestion, prompt_version_of, filter_rows, table_frame, csv_bytes, counters.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import asdict

import pandas as pd

from ai.schemas import BRIEFING_PROMPT_VERSION, TRIAGE_PROMPT_VERSION
from core import ledger

logger = logging.getLogger(__name__)

AI_TYPES = ("briefing", "capacity_plan", "message_reply")
TEMPLATE_COLUMNS = ["decision_id", "ts", "patient_id", "item_type", "item_id", "ai_suggestion", "clinician_action",
                    "clinician_role", "reason", "evidence_ids"]
TABLE_COLUMNS = ["time", "patient", "type", "item_id", "action", "role", "reason", "AI suggestion",
                 "evidence ids", "prompt version", "stored"]


def load_rows() -> list[dict]:
    """Ledger rows (stored="ledger") + pending-file rows not yet imported (id "pending-<n>"), newest first."""
    from clinical import decisions as cd  # lazy: clinical.decisions imports core.ledger

    rows = [{**r, "stored": "ledger"} for r in ledger.list_rows()]
    seen = {ledger.decision_key(r) for r in rows}
    for n, d in enumerate(cd.list_pending(), start=1):
        k = ledger.decision_key(d)
        if k in seen:
            continue
        seen.add(k)
        rows.append({**asdict(d), "id": f"pending-{n}", "prompt_version": None, "recorded_at": None,
                     "evidence_ids": list(d.evidence_ids), "stored": "pending file"})
    # newest first; on equal ts ledger rows (recorded later) come first, higher id first
    rows.sort(key=lambda r: (r["ts"], r["stored"] == "ledger", r["id"] if isinstance(r["id"], int) else 0),
              reverse=True)
    return rows


def _json(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def short_suggestion(row: dict) -> str:
    text = row.get("ai_suggestion") or ""
    kind = row["item_type"]
    if kind == "briefing":
        data = _json(text)
        if isinstance(data, dict) and isinstance(data.get("briefing"), dict):
            tag = "claude" if data.get("source") == "claude" else "rule-based"
            return f"[{tag}] {data['briefing'].get('headline', '')}"
    elif kind == "capacity_plan":
        head, sep, ai = text.partition(" AI explanation:")
        first = head.split(". ")[0].rstrip(".") + "."
        return f"{first} AI explanation:{ai}" if sep else first
    elif kind == "message_reply":
        return row.get("reason") or text[:120]
    return text[:120]


def prompt_version_of(row: dict) -> str:
    """Stored version, else derived from the row: real constants for Claude output, "rule-based"/"solver" otherwise."""
    if row.get("prompt_version"):
        return row["prompt_version"]
    kind = row["item_type"]
    if kind in ("briefing", "message_reply"):
        data = _json(row.get("ai_suggestion") or "")
        if not (isinstance(data, dict) and data.get("source") == "claude"):
            return "rule-based"
        return BRIEFING_PROMPT_VERSION if kind == "briefing" else TRIAGE_PROMPT_VERSION
    if kind == "capacity_plan":
        return "solver"
    return ""


def filter_rows(rows: list[dict], pids=(), types=(), actions=()) -> list[dict]:
    return [r for r in rows if (not pids or r["patient_id"] in pids) and (not types or r["item_type"] in types)
            and (not actions or r["clinician_action"] in actions)]


def table_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{
        "time": r["ts"][11:16], "patient": r["patient_id"], "type": r["item_type"], "item_id": r["item_id"],
        "action": r["clinician_action"], "role": r["clinician_role"], "reason": r["reason"],
        "AI suggestion": short_suggestion(r), "evidence ids": ", ".join(r["evidence_ids"]),
        "prompt version": prompt_version_of(r), "stored": r["stored"]} for r in rows], columns=TABLE_COLUMNS)


def csv_bytes(rows: list[dict]) -> bytes:
    """Template columns + prompt_version + stored; evidence ids joined with ';'; UTF-8 with BOM."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([*TEMPLATE_COLUMNS, "prompt_version", "stored"])
    for r in rows:
        w.writerow([r["id"], r["ts"], r["patient_id"], r["item_type"], r["item_id"], r["ai_suggestion"],
                    r["clinician_action"], r["clinician_role"], r["reason"], ";".join(r["evidence_ids"]),
                    prompt_version_of(r), r["stored"]])
    return buf.getvalue().encode("utf-8-sig")


def counters(rows: list[dict]) -> dict:
    ai = [r for r in rows if r["item_type"] in AI_TYPES]
    return {"approved": sum(r["clinician_action"] == "approve" for r in ai),
            "edited": sum(r["clinician_action"] == "edit" for r in ai),
            "rejected": sum(r["clinician_action"] == "reject" for r in ai),
            "signoffs": sum(r["item_type"] == "signoff" for r in rows)}


if __name__ == "__main__":
    _rows = load_rows()
    for _r in _rows:
        print(_r["id"], _r["ts"], _r["item_type"], prompt_version_of(_r), short_suggestion(_r))
    print(counters(_rows))
