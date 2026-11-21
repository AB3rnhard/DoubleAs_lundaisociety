"""Tests for core.ledger. Owner: Teammate B."""
from pathlib import Path

import pytest

from core import ledger
from core.contracts import Decision

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_DB", str(tmp_path / "t.db"))
    return tmp_path / "t.db"


def dec(pid="P-001", item_type="briefing", action="approve", ev=None, item_id="X-1"):
    return Decision("2026-11-21T14:00", pid, item_type, item_id, "suggestion – åäö", action,
                    "Physician", "because", ev if ev is not None else ["F1", "N-002"])


def test_round_trip_and_row_id(db):
    d = dec()
    rid = ledger.record(d)
    assert rid == 1 and db.exists()
    assert ledger.list_decisions() == [d]


def test_empty_evidence_round_trip():
    ledger.record(dec(ev=[]))
    assert ledger.list_decisions()[0].evidence_ids == []


def test_newest_first_and_filters():
    ledger.record(dec("P-001", item_id="a"))
    ledger.record(dec("P-002", "message_reply", item_id="b"))
    ledger.record(dec("P-001", "capacity_plan", item_id="c"))
    assert [d.item_id for d in ledger.list_decisions()] == ["c", "b", "a"]
    assert [d.item_id for d in ledger.list_decisions(patient_id="P-001")] == ["c", "a"]
    assert [d.item_id for d in ledger.list_decisions(item_type="message_reply")] == ["b"]
    assert ledger.list_decisions(patient_id="P-001", item_type="message_reply") == []


def test_cache_upsert_and_get():
    assert ledger.cache_get("k") is None
    ledger.cache_put("k", {"a": 1, "t": "åäö"}, "m", "v1")
    ledger.cache_put("k", {"a": 2}, "m", "v2")
    assert ledger.cache_get("k") == {"a": 2}


def test_invalid_values_raise():
    with pytest.raises(ValueError):
        ledger.record(dec(action="maybe"))
    with pytest.raises(ValueError):
        ledger.record(dec(item_type="nonsense"))
    assert ledger.list_decisions() == []


def test_repo_db_untouched(db):
    repo_db = ROOT / "ledger.db"
    before = repo_db.stat().st_mtime_ns if repo_db.exists() else None
    ledger.record(dec())
    after = repo_db.stat().st_mtime_ns if repo_db.exists() else None
    assert before == after
    assert ledger.db_path() == db
