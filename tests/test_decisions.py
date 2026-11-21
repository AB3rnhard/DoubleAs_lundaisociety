"""Tests for clinical.decisions (bridge to the ledger). Owner: Teammate A. Built in A4."""
from __future__ import annotations

import json

import pytest

from ai.factsheet import get_briefing
from ai.triage import inbox
from clinical import decisions
from core import ledger
from core.contracts import Decision, to_dict

CLOCK = "2026-11-21T13:00"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("AI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DECISIONS_PENDING_PATH", str(tmp_path / "logs" / "pending.jsonl"))
    monkeypatch.delattr(ledger, "record", raising=False)  # tests cover the "no ledger yet" path


def _decision(i: int = 0, ts: str = CLOCK) -> Decision:
    return Decision(ts=ts, patient_id="P-002", item_type="briefing", item_id=f"BRF-P-002-{i}",
                    ai_suggestion='{"x": "å"}', clinician_action="approve", clinician_role="physician",
                    reason="ok", evidence_ids=["F1", "M-009"])


def test_pending_file_gets_one_valid_json_line_per_record(tmp_path):
    assert decisions.record_decision(_decision(1)) == "pending"
    assert decisions.record_decision(_decision(2)) == "pending"
    lines = (tmp_path / "logs" / "pending.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rows = [json.loads(x) for x in lines]
    assert rows[0] == to_dict(_decision(1))
    assert rows[1]["item_id"] == "BRF-P-002-2"
    assert "å" in lines[0]  # utf-8, not escaped


def test_list_pending_round_trip_newest_first():
    assert decisions.list_pending() == []
    first, later, last = _decision(1, "2026-11-21T09:00"), _decision(2, "2026-11-21T12:00"), _decision(3)
    for d in (first, last, later):
        decisions.record_decision(d)
    got = decisions.list_pending()
    assert got == [last, later, first]
    assert all(isinstance(d, Decision) for d in got)


def test_record_decision_uses_ledger_when_it_exists(monkeypatch, tmp_path):
    seen: list[Decision] = []
    monkeypatch.setattr(ledger, "record", seen.append, raising=False)
    d = _decision()
    assert decisions.record_decision(d) == "ledger"
    assert seen == [d]
    assert not (tmp_path / "logs" / "pending.jsonl").exists()


def test_briefing_decision_fields():
    res = get_briefing("P-002", CLOCK, live=False)
    d = decisions.briefing_decision(res, "edit", "nurse", "Edited: call the patient first", CLOCK)
    assert (d.ts, d.patient_id, d.item_type, d.item_id) == (CLOCK, "P-002", "briefing", "BRF-P-002-1300")
    assert (d.clinician_action, d.clinician_role, d.reason) == ("edit", "nurse", "Edited: call the patient first")
    shown = json.loads(d.ai_suggestion)
    assert shown["source"] == res["source"] and shown["briefing"] == res["briefing"]
    cited = {i for key in ("summary_points", "changes_vs_baseline", "plan_rules", "relevant_history",
                           "suggested_actions") for item in res["briefing"][key] for i in item["evidence_ids"]}
    assert d.evidence_ids == sorted(cited) and d.evidence_ids
    assert len(set(d.evidence_ids)) == len(d.evidence_ids)


def test_briefing_decision_item_id_uses_clock_time():
    res = get_briefing("P-002", "2026-11-21T21:00", live=False)
    assert decisions.briefing_decision(res, "approve", "physician", "", "2026-11-21T21:00").item_id == (
        "BRF-P-002-2100")


# ---------- A5: nurse replies to inbox messages ----------
def _item(mid: str = "M-009") -> dict:
    return next(i for i in inbox("2026-11-21T21:00") if i["message_id"] == mid)


def test_message_reply_decision_approve_when_text_equals_draft():
    item = _item()
    draft = item["triage"]["triage"]["draft_reply"]
    d = decisions.message_reply_decision(item, f"  {draft} ", "2026-11-21T13:00")
    assert (d.ts, d.patient_id, d.item_type, d.item_id) == ("2026-11-21T13:00", "P-002", "message_reply", "RPL-M-009")
    assert (d.clinician_action, d.clinician_role, d.evidence_ids) == ("approve", "nurse", ["M-009"])
    shown = json.loads(d.ai_suggestion)
    assert set(shown) == {"source", "triage"} and shown["triage"]["draft_reply"] == draft
    assert shown["source"] == item["triage"]["source"]


def test_message_reply_decision_edit_stores_the_final_text():
    d = decisions.message_reply_decision(_item(), "A nurse will call you within the hour.", "2026-11-21T13:00")
    assert d.clinician_action == "edit"
    assert d.reason == "A nurse will call you within the hour."


def test_replied_message_ids_from_the_pending_file():
    assert decisions.replied_message_ids() == set()
    decisions.record_decision(decisions.message_reply_decision(_item("M-009"), "ok, a nurse will call", CLOCK))
    decisions.record_decision(_decision(1))  # a briefing decision is not a reply
    assert decisions.replied_message_ids() == {"M-009"}


def test_replied_message_ids_from_the_ledger_when_it_can_list(monkeypatch):
    rows = [decisions.message_reply_decision(_item("M-012"), "text", CLOCK), _decision(1)]
    monkeypatch.setattr(ledger, "list_decisions", lambda: rows, raising=False)
    assert decisions.replied_message_ids() == {"M-012"}
