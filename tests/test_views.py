"""Tests for the patient view (queue.readings, clinical.views, pages). Owner: Teammate A. Built in A3.

Every acceptance number comes from core.loader.load_expected_results().
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from clinical.queue import READING_COLUMNS, get_ward_state, readings
from clinical.views import (
    news2_figure,
    patient_header,
    timeline,
    virtual_ward_patients,
    vitals_figure,
)
from core import ledger
from core.loader import load_expected_results, load_messages, load_notes, load_patients

EXP = load_expected_results()
P2 = EXP["P-002"]
ART = EXP["alert_burden_virtual_ward_84h"]["artefact_readings_excluded"][0]
ROOT = Path(__file__).resolve().parent.parent
DAY = "2026-11-21"
CLOCKS = [f"{DAY}T{h:02d}:00" for h in (9, 12, 13, 17, 21)]
FLAGS = ["trend_flag", "symptom_flag", "urgent", "emergency", "plan_breach"]


def _hour(ts: str) -> str:
    return ts[11:16]


def _rows(fig) -> set[str]:
    return {k for k in fig.layout if k.startswith("yaxis")}


def _trace_names(fig) -> set[str]:
    return {t.name for t in fig.data}


# ---------- readings ----------
@pytest.mark.parametrize("clock", CLOCKS)
def test_readings_never_after_clock(clock):
    for pid in virtual_ward_patients():
        r = readings(pid, clock)
        assert (r["ts"] <= pd.Timestamp(clock)).all()
        assert r["ts"].is_monotonic_increasing


def test_readings_artefact_row_has_no_flags():
    r = readings(ART["patient_id"], ART["ts"])
    art = r[r["artefact"]]
    assert len(art) == 1
    assert pd.Timestamp(art["ts"].iloc[0]) == pd.Timestamp(ART["ts"])
    assert not art[FLAGS].any(axis=None)


def test_readings_p002_news2_by_hour_stream():
    r = readings("P-002", f"{DAY}T21:00")
    stream = r[r["phase"] == "stream"]
    got = {_hour(f"{ts:%Y-%m-%dT%H:%M}"): int(n) for ts, n in zip(stream["ts"], stream["news2"], strict=True)}
    got = {k: v for k, v in got.items() if k in P2["news2_by_hour_stream"]}
    assert got == P2["news2_by_hour_stream"]


def test_readings_p002_trend_flag_count():
    r = readings("P-002", f"{DAY}T21:00")
    assert int(r["trend_flag"].sum()) == EXP["reference_trend_flags_per_virtual_ward_patient"]["P-002"]


def test_readings_unknown_pid_empty_with_columns():
    r = readings("X-999", f"{DAY}T21:00")
    assert r.empty
    assert list(r.columns) == READING_COLUMNS


def test_readings_does_not_mutate_cache():
    a = readings("P-002", f"{DAY}T21:00")
    a.loc[:, "news2"] = 99
    assert (readings("P-002", f"{DAY}T21:00")["news2"] != 99).all()


# ---------- header ----------
def test_virtual_ward_patients():
    ids = virtual_ward_patients()
    assert ids and all(p.startswith("P-") for p in ids)
    assert "P-002" in ids


def test_patient_header_p002():
    clock = f"{DAY}T13:00"
    h = patient_header("P-002", clock)
    pat = load_patients().set_index("patient_id").loc["P-002"]
    assert h["name"] == pat["name"]
    assert h["day_of_stay"] == (pd.Timestamp(clock) - pat["virtual_ward_start"]).days + 1
    item = next(q for q in get_ward_state(clock).queue if q.patient_id == "P-002")
    assert h["reasons"] == item.reasons
    assert (h["priority"], h["news2"], h["flagged_at"]) == (item.priority, item.news2, item.flagged_at)
    assert h["custom_rule"]  # P-002 has a custom rule in its plan


def test_patient_header_no_custom_rule_is_none():
    h = patient_header("P-001", f"{DAY}T13:00")
    assert h["custom_rule"] is None


# ---------- vitals figure ----------
@pytest.mark.parametrize("clock", [CLOCKS[0], CLOCKS[2], CLOCKS[-1]])
def test_vitals_figure_five_rows_and_no_future_data(clock):
    fig = vitals_figure("P-002", clock)
    assert {"yaxis", "yaxis2", "yaxis3", "yaxis4", "yaxis5"} <= _rows(fig)
    assert "yaxis6" not in _rows(fig)
    for t in fig.data:
        assert (pd.to_datetime(list(t.x)) <= pd.Timestamp(clock)).all()


def test_vitals_figure_artefact_trace():
    pid, ts = ART["patient_id"], ART["ts"]
    at = vitals_figure(pid, ts)
    assert "artefact" in _trace_names(at)
    art = next(t for t in at.data if t.name == "artefact")
    assert pd.Timestamp(ts) in set(pd.to_datetime(list(art.x)))
    before = vitals_figure(pid, f"{pd.Timestamp(ts) - pd.Timedelta(hours=1):%Y-%m-%dT%H:%M}")
    assert "artefact" not in _trace_names(before)


def test_vitals_figure_news2_alert_trace():
    assert "NEWS2 alert" in _trace_names(vitals_figure("P-002", f"{DAY}T21:00"))
    assert "NEWS2 alert" not in _trace_names(vitals_figure("P-002", f"{DAY}T09:00"))


def test_vitals_figure_trend_trace():
    first = P2["reference_trend_flag_first"]
    assert "trend" in _trace_names(vitals_figure("P-002", first))
    assert "trend" not in _trace_names(vitals_figure("P-002", "2026-11-21T09:00"))


def test_vitals_figure_default_x_range_is_last_48h():
    fig = vitals_figure("P-002", f"{DAY}T21:00")
    lo, hi = (pd.Timestamp(x) for x in fig.layout.xaxis.range)
    assert hi == pd.Timestamp(f"{DAY}T21:00")
    assert hi - lo == pd.Timedelta(hours=48)


# ---------- NEWS2 figure ----------
def test_news2_figure_matches_expected_and_not_future():
    clock = f"{DAY}T21:00"
    bar = news2_figure("P-002", clock).data[0]
    xs = pd.to_datetime(list(bar.x))
    assert (xs <= pd.Timestamp(clock)).all()
    got = {f"{x:%H:%M}": int(y) for x, y in zip(xs, bar.y, strict=True) if x >= pd.Timestamp(f"{DAY}T10:00")}
    assert got == P2["news2_by_hour_stream"]


def test_news2_figure_window_is_24h():
    clock = f"{DAY}T21:00"
    xs = pd.to_datetime(list(news2_figure("P-002", clock).data[0].x))
    assert xs.min() >= pd.Timestamp(clock) - pd.Timedelta(hours=24)


# ---------- timeline ----------
def test_timeline_p002_symptom_message():
    t0 = P2["patient_message_first_symptom"]
    at = timeline("P-002", t0)
    sym = [e for e in at if e["kind"] == "message" and e["ts"] == t0]
    assert sym and all(e["symptom"] for e in sym)
    before = f"{pd.Timestamp(t0) - pd.Timedelta(hours=1):%Y-%m-%dT%H:%M}"
    assert not [e for e in timeline("P-002", before) if e["kind"] == "message" and e["ts"] == t0]


def test_timeline_order_and_cutoff():
    clock = f"{DAY}T13:00"
    tl = timeline("P-002", clock)
    stamps = [e["ts"] for e in tl]
    assert stamps == sorted(stamps, reverse=True)
    assert all(pd.Timestamp(s) <= pd.Timestamp(clock) for s in stamps)
    assert all(e["kind"] in {"note", "message"} for e in tl)


def test_timeline_note_count_matches_loader():
    clock = f"{DAY}T21:00"
    notes = load_notes()
    expected = len(notes[(notes["patient_id"] == "P-002") & (notes["ts"] <= pd.Timestamp(clock))])
    assert len([e for e in timeline("P-002", clock) if e["kind"] == "note"]) == expected


# ---------- page smoke tests ----------
@pytest.fixture(autouse=True)
def _ai_env(tmp_path, monkeypatch):
    """No network and no writes outside tmp_path: DEMO_MODE=1, empty AI cache, pending decisions file."""
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("AI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DECISIONS_PENDING_PATH", str(tmp_path / "pending.jsonl"))
    monkeypatch.setenv("LEDGER_DB", str(tmp_path / "ledger.db"))  # never read the real ledger.db
    monkeypatch.delattr(ledger, "record", raising=False)



def _run(page: str, clock: str) -> AppTest:
    at = AppTest.from_file(str(ROOT / page), default_timeout=30)
    at.session_state["clock"] = clock
    at.session_state["escalated"] = []
    at.session_state["tasks"] = []
    return at.run()


@pytest.mark.parametrize("page", ["pages/1_Command_Center.py", "pages/2_Patient.py"])
@pytest.mark.parametrize("hour", ["09:00", "12:00", "13:00", "21:00"])
def test_pages_render(page, hour):
    at = _run(page, f"{DAY}T{hour}")
    assert not at.exception


def test_patient_page_default_is_top_of_queue():
    clock = f"{DAY}T13:00"
    at = _run("pages/2_Patient.py", clock)
    assert not at.exception
    assert at.selectbox(key="patient_pid").value == get_ward_state(clock).queue[0].patient_id


def test_patient_page_query_param_deep_link():
    at = AppTest.from_file(str(ROOT / "pages/2_Patient.py"), default_timeout=30)
    at.session_state["clock"] = f"{DAY}T13:00"
    at.session_state["escalated"] = []
    at.session_state["tasks"] = []
    at.query_params["pid"] = "P-001"
    at.run()
    assert not at.exception
    assert at.selectbox[0].value == "P-001"


# ---------- A4: AI briefing and decision form on the Patient page ----------
def _patient_page(clock: str = f"{DAY}T13:00") -> AppTest:
    return _run("pages/2_Patient.py", clock)


def _texts(at: AppTest) -> str:
    return " ".join(str(m.value) for m in at.markdown)


def _form_key(at: AppTest, prefix: str) -> str:
    return next(w.key for w in [*at.radio, *at.selectbox, *at.text_area, *at.checkbox]
                if w.key and w.key.startswith(prefix))


def _submit(at: AppTest):
    return next(b for b in at.button if b.label == "Submit decision")


def test_patient_page_shows_rule_based_briefing_with_empty_cache():
    at = _patient_page()
    assert not at.exception
    text = _texts(at)
    assert "AI briefing" in " ".join(s.value for s in at.subheader)
    assert "Rule-based summary" in text
    assert "Needs review now" in text  # P-002 at 13:00: TREND + SYMPTOM_MESSAGE
    assert ":gray-badge[F" in text  # evidence ids on the items
    assert any("DEMO_MODE=1" in c.value for c in at.caption)


def test_generate_briefing_button_in_demo_mode_does_not_raise():
    at = _patient_page()
    at.button(key="briefing_generate").click().run()
    assert not at.exception
    assert "Rule-based summary" in _texts(at)  # nothing cached, no live call


def test_decision_form_adds_patient_to_escalated_and_writes_pending_file(tmp_path):
    at = _patient_page()
    pid = at.selectbox[0].value
    at.checkbox(key=_form_key(at, "briefing_readmit")).check()
    _submit(at).click().run()
    assert not at.exception
    assert pid in at.session_state["escalated"]
    assert at.success
    rows = [json.loads(x) for x in (tmp_path / "pending.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert (rows[0]["patient_id"], rows[0]["item_type"], rows[0]["clinician_action"]) == (
        pid, "briefing", "approve")
    assert rows[0]["item_id"] == f"BRF-{pid}-1300"


def test_decision_form_no_duplicate_in_escalated_and_escalates_on_reject():
    at = _patient_page()
    pid = at.selectbox[0].value
    at.session_state["escalated"] = [pid]
    at.radio(key=_form_key(at, "briefing_action")).set_value("Reject")
    at.text_area(key=_form_key(at, "briefing_text")).set_value("Not convincing")
    at.checkbox(key=_form_key(at, "briefing_readmit")).check()
    _submit(at).click().run()
    assert not at.exception
    assert at.session_state["escalated"] == [pid]


def test_decision_form_edit_without_text_is_refused(tmp_path):
    at = _patient_page()
    at.radio(key=_form_key(at, "briefing_action")).set_value("Edit")
    _submit(at).click().run()
    assert not at.exception
    assert at.error and not (tmp_path / "pending.jsonl").exists()
    assert at.session_state["escalated"] == []


# ---------- A5: Inbox on the Command Center ----------
def _command_center(clock: str) -> AppTest:
    st.cache_data.clear()  # the page caches inbox(clock); each test has its own AI cache folder
    return _run("pages/1_Command_Center.py", clock)


def _n_messages(clock: str) -> int:
    m = load_messages()
    return int((m["ts"] <= pd.Timestamp(clock)).sum())


@pytest.mark.parametrize("hour", ["09:00", "12:00", "21:00"])
def test_command_center_inbox_has_one_reply_box_per_message(hour):
    clock = f"{DAY}T{hour}"
    at = _command_center(clock)
    assert not at.exception
    boxes = [t for t in at.text_area if t.key and t.key.startswith("inbox_reply_")]
    assert len(boxes) == _n_messages(clock)
    assert "Home Hospital Inbox" in [s.value for s in at.subheader]
    assert any("DEMO_MODE=1" in c.value for c in at.caption)
    assert at.metric[-1].label == "Messages needing a clinician (not replied)"


def test_command_center_d0_layout_at_13():
    at = _command_center(f"{DAY}T13:00")
    assert not at.exception
    assert at.title[0].value == "Patients Dashboard"
    heads = [s.value for s in at.subheader]
    order = ["Patients at home (virtual ward)", "ATTENTION HERE", "Patients at the hospital (inpatients)",
             "Home Hospital Inbox"]
    assert [heads.index(h) for h in order] == sorted(heads.index(h) for h in order)
    labels = [m.label for m in at.metric]
    for t in ["Alerts suppressed", "Patients flagged", "Earliest flag vs NEWS2", "Patients at home",
              "Virtual slots used", "Beds", "Kits free", "Visits"]:
        assert t in labels
    p = load_patients()
    n_inp = int((p["location"] == "INPATIENT").sum())
    assert any(len(d.value) == n_inp for d in at.dataframe)


def test_command_center_older_messages_are_in_a_collapsed_expander():
    at = _command_center(f"{DAY}T21:00")
    assert any(e.label.startswith("Older messages") for e in at.expander)


def test_command_center_send_reply_writes_one_pending_decision(tmp_path):
    clock = f"{DAY}T12:00"
    at = _command_center(clock)
    newest = load_messages()
    newest = newest[newest["ts"] <= pd.Timestamp(clock)].sort_values(["ts", "message_id"]).iloc[-1]
    mid = str(newest["message_id"])
    at.text_area(key=f"inbox_reply_{mid}").set_value("A nurse will call you shortly, please stay near the phone.")
    at.button(key=f"inbox_send_{mid}").click().run()
    assert not at.exception
    assert at.success and "not sent to the patient" in at.success[0].value
    rows = [json.loads(x) for x in (tmp_path / "pending.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert (rows[0]["item_type"], rows[0]["item_id"], rows[0]["clinician_role"], rows[0]["clinician_action"]) == (
        "message_reply", f"RPL-{mid}", "nurse", "edit")
    assert rows[0]["patient_id"] == str(newest["patient_id"]) and rows[0]["evidence_ids"] == [mid]
    # the next render shows it as replied and has one reply box fewer
    st.cache_data.clear()
    at.run()
    assert not at.exception
    assert f"inbox_reply_{mid}" not in [t.key for t in at.text_area]
    assert any("Replied" in m.value for m in at.markdown)


def test_command_center_empty_reply_is_refused_and_nothing_is_written(tmp_path):
    at = _command_center(f"{DAY}T12:00")
    at.text_area(key="inbox_reply_M-009").set_value("   ")
    at.button(key="inbox_send_M-009").click().run()
    assert not at.exception
    assert at.error and not (tmp_path / "pending.jsonl").exists()


def test_command_center_triage_button_in_demo_mode_does_not_raise():
    at = _command_center(f"{DAY}T21:00")
    at.button(key="inbox_triage").click().run()
    assert not at.exception
    assert len([t for t in at.text_area if t.key.startswith("inbox_reply_")]) == _n_messages(f"{DAY}T21:00")
