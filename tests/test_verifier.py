"""Tests for ai.verifier (verify, deterministic_briefing). Owner: Teammate A. Built in A4."""
from __future__ import annotations

import copy

import pytest

from ai.factsheet import build_factsheet
from ai.schemas import BRIEFING_SCHEMA
from ai.verifier import deterministic_briefing, extract_doses, extract_numbers, verify
from clinical.views import virtual_ward_patients

DAY = "2026-11-21"
PID, CLOCK = "P-002", f"{DAY}T13:00"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("AI_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture(scope="module")
def fs():
    return build_factsheet(PID, CLOCK)


def _fact(fs, **match):
    return next(f for f in fs["facts"] if all(f.get(k) == v for k, v in match.items()))


def _valid(fs: dict) -> dict:
    dev_t, dev_r = _fact(fs, param="temp_c"), _fact(fs, param="rr")
    urgent = next(f for f in fs["facts"] if "urgent threshold" in f["text"])
    return {
        "headline": "Early deterioration signs while NEWS2 is still low",
        "status": fs["expected_status"],
        "summary_points": [{"text": "Temp 37.9 and RR 19 are above baseline, and the patient reports shivering",
                            "evidence_ids": [dev_t["id"], dev_r["id"], "M-009"]}],
        "changes_vs_baseline": [{"parameter": "temp_c", "baseline": "37.2", "current": "37.9",
                                 "evidence_ids": [dev_t["id"]]}],
        "plan_rules": [{"rule": "NEWS2 urgent threshold 5", "state": "not_triggered",
                        "evidence_ids": [urgent["id"]]}],
        "relevant_history": [{"text": "Previous pneumonia needed oxygen",
                              "evidence_ids": [_fact(fs, type="profile")["id"]]}],
        "suggested_actions": [{"action": "Consider a clinician review today",
                               "rationale": "Symptoms reported at 12:00", "evidence_ids": ["M-009"]}],
        "missing_information": ["Latest bloods"],
    }


def _with(fs, path: tuple, value) -> dict:
    out = _valid(fs)
    node = out
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return out


def test_valid_briefing_passes(fs):
    res = verify(_valid(fs), fs, fs["expected_status"])
    assert res["ok"] and res["errors"] == []
    # 5 claim dicts + headline + status + 1 missing_information string
    assert res["n_ok"] == res["n_checked"] == 8


def test_unknown_evidence_id_fails(fs):
    res = verify(_with(fs, ("summary_points", 0, "evidence_ids"), ["F999"]), fs)
    assert not res["ok"] and res["n_ok"] < res["n_checked"]
    assert any("summary_points[0]" in e and "F999" in e for e in res["errors"])


def test_empty_evidence_ids_fail(fs):
    res = verify(_with(fs, ("relevant_history", 0, "evidence_ids"), []), fs)
    assert not res["ok"]
    assert any("relevant_history[0]" in e and "no evidence_ids" in e for e in res["errors"])


def test_number_not_in_cited_facts_fails(fs):
    res = verify(_with(fs, ("summary_points", 0, "text"), "Temp 38.4 is above baseline"), fs)
    assert not res["ok"]
    assert any("summary_points[0]: number 38.4 not in F" in e for e in res["errors"])


def test_number_must_be_in_a_cited_item_not_just_the_factsheet(fs):
    hr = next(f for f in fs["facts"] if f["type"] == "vital" and f["text"].startswith("13:00"))
    value = hr["text"].split("HR ")[1].split(",")[0]
    out = _with(fs, ("summary_points", 0, "text"), f"HR {value} at last reading")
    out["summary_points"][0]["evidence_ids"] = [_fact(fs, param="temp_c")["id"]]
    assert not verify(out, fs)["ok"]
    out["summary_points"][0]["evidence_ids"] = [hr["id"]]
    assert verify(out, fs)["ok"]


def test_trailing_zero_is_the_same_number(fs):
    assert verify(_with(fs, ("summary_points", 0, "text"), "Temp 37.90 is above baseline"), fs)["ok"]
    assert verify(_with(fs, ("changes_vs_baseline", 0, "current"), "37.90"), fs)["ok"]


def test_ids_times_dates_and_glued_digits_are_ignored(fs):
    text = "At 12:00 on 2026-11-21 SpO2 and NEWS2 stayed stable, see F20 and J18.9"
    out = _with(fs, ("summary_points", 0, "text"), text)
    out["summary_points"][0]["evidence_ids"] = [_fact(fs, param="temp_c")["id"]]
    assert verify(out, fs)["ok"]


def test_dose_fails_even_with_a_number_from_evidence(fs):
    out = _with(fs, ("suggested_actions", 0, "action"), "Consider 500 mg paracetamol")
    res = verify(out, fs)
    assert not res["ok"] and any("suggested_actions[0].action" in e for e in res["errors"])


def test_dose_in_cited_text_passes(fs):
    profile = _fact(fs, type="profile")["id"]
    out = _with(fs, ("relevant_history", 0, "text"), "Needed 2 L/min oxygen in 2025")
    out["relevant_history"][0]["evidence_ids"] = [profile]
    assert verify(out, fs)["ok"]
    out["relevant_history"][0]["text"] = "Needed 4 L/min oxygen in 2025"
    assert not verify(out, fs)["ok"]


def test_headline_number_not_in_factsheet_fails(fs):
    res = verify(_with(fs, ("headline",),"Patient has 41 abnormal readings"), fs)
    assert not res["ok"] and any(e.startswith("headline: number 41") for e in res["errors"])


def test_headline_number_from_factsheet_passes(fs):
    assert verify(_with(fs, ("headline",), "Temp 37.9 and rising"), fs)["ok"]


def test_missing_information_number_is_checked(fs):
    res = verify(_with(fs, ("missing_information", 0), "Bloods from 77 days ago"), fs)
    assert not res["ok"] and any(e.startswith("missing_information[0]: number 77") for e in res["errors"])


def test_status_checked_only_when_expected_status_given(fs):
    out = _with(fs, ("status",), "routine")
    assert verify(out, fs)["ok"]
    res = verify(out, fs, fs["expected_status"])
    assert not res["ok"] and any(e.startswith("status:") for e in res["errors"])


def test_non_dict_output_fails_without_raising(fs):
    assert not verify([], fs)["ok"]


def test_verify_does_not_mutate_inputs(fs):
    out, sheet = _valid(fs), copy.deepcopy(fs)
    verify(out, sheet, fs["expected_status"])
    assert out == _valid(fs) and sheet == fs


def test_evidence_in_other_lists_is_accepted():
    sheet = {"facts": [], "pfacts": [{"id": "P1", "text": "Bed count 12"}]}
    ok = {"headline": "Beds", "items": [{"text": "12 beds", "evidence_ids": ["P1"]}]}
    assert verify(ok, sheet)["ok"]
    bad = {"headline": "Beds", "items": [{"text": "13 beds", "evidence_ids": ["P1"]}]}
    assert not verify(bad, sheet)["ok"]


def test_extract_numbers_and_doses():
    assert extract_numbers("13:00 HR 96, SpO2 94, NEWS2 5, F12, N-005, J18.9, 2026-11-21, 37.90") == {
        96.0, 94.0, 5.0, 37.9}
    assert extract_numbers("range 94-100 and 6h") == {94.0, 100.0}
    assert extract_doses("Give 500 mg and 2L/min, 1.5 g, 3 units") == {"500mg", "2l/min", "1.5g", "3units"}
    assert extract_doses("great 5 grams") == set()


# ---------- deterministic briefing ----------
@pytest.mark.parametrize("hour", ["09:00", "13:00", "21:00"])
def test_deterministic_briefing_passes_verify_for_every_patient(hour):
    for pid in virtual_ward_patients():
        sheet = build_factsheet(pid, f"{DAY}T{hour}")
        det = deterministic_briefing(sheet)
        res = verify(det, sheet, sheet["expected_status"])
        assert res["ok"], (pid, hour, res["errors"][:3])
        assert res["n_ok"] == res["n_checked"] > 0
        assert set(det) == set(BRIEFING_SCHEMA["required"])
        assert det["suggested_actions"] == [] and det["missing_information"] == []


def test_deterministic_briefing_content_for_p002(fs):
    det = deterministic_briefing(fs)
    assert det["headline"] == "Rule-based summary: TREND, SYMPTOM_MESSAGE"
    assert det["status"] == "needs_review_now"
    assert {c["parameter"] for c in det["changes_vs_baseline"]} == {"rr", "temp_c"}
    assert any(h["evidence_ids"] == ["M-009"] for h in det["relevant_history"])
    states = {r["rule"]: r["state"] for r in det["plan_rules"]}
    assert set(states.values()) <= {"triggered", "not_triggered"} and states


def test_deterministic_headline_without_flags():
    sheet = build_factsheet("P-001", f"{DAY}T09:00")
    assert sheet["reasons"] == []
    assert deterministic_briefing(sheet)["headline"] == "Rule-based summary: no active flags"
