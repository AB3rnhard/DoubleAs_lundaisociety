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


# ---------------------------------------------------------------- B5: list_rows + import_pending
@pytest.fixture
def pending(tmp_path, monkeypatch):
    p = tmp_path / "pending.jsonl"
    monkeypatch.setenv("DECISIONS_PENDING_PATH", str(p))
    return p


def test_list_rows_order_filters_and_prompt_version():
    a = dec("P-001", item_id="a")
    b = Decision("2026-11-21T15:00", "P-002", "message_reply", "b", "s", "edit", "nurse", "r", ["M-1"])
    c = Decision("2026-11-21T14:00", "P-001", "capacity_plan", "c", "s", "reject", "Bed manager", "r", [])
    ledger.record(a, prompt_version="briefing_v1")
    ledger.record(b)
    ledger.record(c)
    rows = ledger.list_rows()
    assert [r["item_id"] for r in rows] == ["b", "c", "a"]  # ts desc, then id desc
    assert rows[2]["prompt_version"] == "briefing_v1" and rows[0]["prompt_version"] is None
    assert rows[0]["evidence_ids"] == ["M-1"] and rows[2]["evidence_ids"] == ["F1", "N-002"]
    assert {"id", "recorded_at", "clinician_role"} <= set(rows[0])
    assert [r["item_id"] for r in ledger.list_rows(patient_id="P-001")] == ["c", "a"]
    assert [r["item_id"] for r in ledger.list_rows(item_type="message_reply")] == ["b"]
    assert [r["item_id"] for r in ledger.list_rows(action="reject")] == ["c"]
    assert ledger.list_rows(patient_id="P-002", action="reject") == []


def test_import_pending_imports_once_and_renames(pending):
    from clinical import decisions as cd
    d1, d2 = dec(item_id="x", ev=["F1", "N-1"]), dec("P-002", item_id="y", action="edit")
    for d in (d1, d2):
        with open(pending, "a", encoding="utf-8") as f:
            import json

            from core.contracts import to_dict
            f.write(json.dumps(to_dict(d), ensure_ascii=False) + "\n")
    assert len(cd.list_pending()) == 2
    assert ledger.import_pending() == 2
    assert not pending.exists()
    assert len(list(pending.parent.glob("pending.jsonl.imported-*"))) == 1
    assert sorted(d.item_id for d in ledger.list_decisions()) == ["x", "y"]
    assert next(r for r in ledger.list_rows() if r["item_id"] == "x")["evidence_ids"] == ["F1", "N-1"]
    assert ledger.import_pending() == 0  # file gone: nothing to do


def test_import_pending_is_idempotent_on_duplicates(pending):
    import json

    from core.contracts import to_dict
    d = dec(item_id="x")
    ledger.record(d)  # already in the ledger
    pending.write_text(json.dumps(to_dict(d), ensure_ascii=False) + "\n", encoding="utf-8")
    assert ledger.import_pending() == 0
    assert pending.exists() and len(ledger.list_rows()) == 1
