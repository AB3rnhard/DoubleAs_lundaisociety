"""AppTest for pages/4_Ledger.py + ops.ledger_view. Owner: Teammate B. Never calls Claude."""
import json
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ai.factsheet import get_briefing
from ai.schemas import BRIEFING_PROMPT_VERSION
from ai.triage import inbox
from clinical import decisions as cd
from core import ledger
from core.contracts import T0, Decision, fake_ward_state, to_dict
from ops import eligibility, ledger_view, whatif_cpsat
from ops.whatif_view import plan_evidence, plan_item_id, plan_summary

PAGE = str(Path(__file__).resolve().parent.parent / "pages" / "4_Ledger.py")
C13, C14, C15 = "2026-11-21T13:00", "2026-11-21T14:00", "2026-11-21T15:00"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("DECISIONS_PENDING_PATH", str(tmp_path / "pending.jsonl"))
    monkeypatch.setenv("AI_CACHE_DIR", str(tmp_path / "ai_cache"))
    monkeypatch.setenv("DEMO_MODE", "1")
    return tmp_path


def make(clock=C15) -> AppTest:
    at = AppTest.from_file(PAGE, default_timeout=60)
    at.session_state["clock"] = clock
    at.session_state["escalated"] = []
    at.session_state["tasks"] = []
    return at.run()


def seed():
    """briefing (13:00), message_reply (14:00), capacity_plan (09:00) in the ledger; one reject only pending (15:00)."""
    res = get_briefing("P-002", C13, live=False)
    brief = cd.briefing_decision(res, "approve", "On-call physician", "ok", C13)
    ledger.record(brief)
    item = inbox(C14)[0]
    reply = cd.message_reply_decision(item, "Thanks, we will call you.", C14)
    ledger.record(reply)
    plan = whatif_cpsat.run_all_profiles(5, fake_ward_state(T0, ["P-002"]))["balanced"]
    elig = eligibility.evaluate(*eligibility.load_scored(T0), T0)
    ledger.record(Decision(T0, "WARD", "capacity_plan", plan_item_id(plan, T0, 5, ["P-002"]), plan_summary(plan),
                           "edit", "Bed manager", "tweak", plan_evidence(plan, elig)))
    pend = cd.briefing_decision(res, "reject", "On-call physician", "not agreed", C15)
    path = cd._pending_path()
    with open(path, "w", encoding="utf-8") as f:
        # brief and reply also exist in the ledger: must not duplicate
        f.writelines(json.dumps(to_dict(d), ensure_ascii=False) + "\n" for d in (pend, brief, reply))
    return res, item


def test_empty_state():
    at = make()
    assert not at.exception
    assert at.title[0].value == "Ledger"
    assert any(i.value.startswith("No decisions yet") for i in at.info)
    assert len(at.dataframe) == 0 and len(at.metric) == 6


def test_seeded_rows_newest_first_no_duplicates():
    seed()
    at = make()
    assert not at.exception
    df = at.dataframe[0].value
    assert list(df["type"]) == ["briefing", "message_reply", "briefing", "capacity_plan"]
    assert list(df["time"]) == ["15:00", "14:00", "13:00", "09:00"]
    assert list(df["stored"]) == ["pending file", "ledger", "ledger", "ledger"]
    assert df["AI suggestion"][0].startswith("[") and df["AI suggestion"][3].startswith("Solver output")
    assert list(df["prompt version"])[3] == "solver"
    assert not at.info


def test_counters_match_seeded_actions():
    seed()
    at = make()
    m = {x.label: x.value for x in at.metric}
    # approve: briefing; edit: custom reply + capacity plan; reject: pending briefing
    assert (m["AI suggestions approved"], m["AI suggestions edited"], m["AI suggestions rejected"]) == ("1", "2", "1")
    assert m["Sign-offs recorded"] == "0"
    assert int(m["Alerts suppressed"]) > 0 and int(m["Artefacts caught"]) >= 1


def test_filters_reduce_rows():
    seed()
    at = make()
    at.multiselect(key="led_type").select("briefing").run()
    assert list(at.dataframe[0].value["type"]) == ["briefing", "briefing"]
    at.multiselect(key="led_action").select("reject").run()
    assert list(at.dataframe[0].value["action"]) == ["reject"]
    at.multiselect(key="led_pid").select("WARD").run()
    assert len(at.dataframe[0].value) == 0


def test_csv_header_and_content():
    seed()
    rows = ledger_view.load_rows()
    raw = ledger_view.csv_bytes(rows)
    assert raw.startswith(b"\xef\xbb\xbf")
    lines = raw.decode("utf-8-sig").splitlines()
    template = (Path(__file__).resolve().parent.parent / "data" / "decisions_template.csv").read_text(
        encoding="utf-8").splitlines()[0]
    assert lines[0] == f"{template},prompt_version,stored"
    assert any(line.startswith("pending-") for line in lines[1:])
    versions = {r["item_type"]: ledger_view.prompt_version_of(r) for r in rows}
    assert versions["capacity_plan"] == "solver"
    assert versions["briefing"] in ("rule-based", BRIEFING_PROMPT_VERSION)


def test_prompt_version_prefers_stored():
    row = {"item_type": "capacity_plan", "prompt_version": "explainer_v1", "ai_suggestion": "x"}
    assert ledger_view.prompt_version_of(row) == "explainer_v1"
    claude = {"item_type": "briefing", "prompt_version": None, "ai_suggestion": json.dumps({"source": "claude"})}
    assert ledger_view.prompt_version_of(claude) == BRIEFING_PROMPT_VERSION


def test_warm_run_under_one_second():
    seed()
    at = make()
    t = time.perf_counter()
    at.run()
    assert time.perf_counter() - t < 1.0


# ---------------------------------------------------------------- sign-off tasks
def _task(tid="PLAN-x:P-002"):
    return {"task_id": tid, "type": "signoff", "subject": "P-002", "patient_id": "P-002", "action": "transfer home",
            "plan_item_id": "PLAN-x", "profile": "balanced", "created_at": T0, "status": "open",
            "evidence_ids": ["P-002", "E-1"]}


def _with_task():
    at = AppTest.from_file(PAGE, default_timeout=60)
    at.session_state["clock"] = C15
    at.session_state["escalated"] = []
    at.session_state["tasks"] = [_task()]
    return at.run()


def test_signoff_approve_writes_one_row_and_closes_task():
    at = _with_task()
    next(b for b in at.button if b.key == "led_ok_PLAN-x:P-002").click().run()
    assert not at.exception
    rows = ledger.list_rows(item_type="signoff")
    assert len(rows) == 1 and rows[0]["clinician_action"] == "approve" and rows[0]["patient_id"] == "P-002"
    assert rows[0]["evidence_ids"] == ["P-002", "E-1"] and rows[0]["clinician_role"] == "On-call physician"
    assert at.session_state["tasks"][0]["status"] == "approved"
    assert not any(b.key == "led_ok_PLAN-x:P-002" for b in at.button)  # no second click possible
    at.run()
    assert len(ledger.list_rows(item_type="signoff")) == 1
    assert {x.label: x.value for x in at.metric}["Sign-offs recorded"] == "1"


def test_signoff_reject_needs_reason():
    at = _with_task()
    next(b for b in at.button if b.key == "led_no_PLAN-x:P-002").click().run()
    assert ledger.list_rows() == [] and at.session_state["tasks"][0]["status"] == "open"
    assert any("reason" in w.value for w in at.warning)
    at.text_input(key="led_reason_PLAN-x:P-002").set_value("Not safe").run()
    next(b for b in at.button if b.key == "led_no_PLAN-x:P-002").click().run()
    row = ledger.list_rows(item_type="signoff")[0]
    assert row["clinician_action"] == "reject" and row["reason"] == "Not safe"
    assert at.session_state["tasks"][0]["status"] == "rejected"
