"""Tests for capacity state and eligibility. Owner: Teammate B. Filled in prompt B1."""
import re
import time
from pathlib import Path

import pandas as pd
import pytest

from core import loader
from core.contracts import T0, fake_ward_state
from ops import eligibility
from ops.capacity import get_capacity
from ops.eligibility import STATUS_GROUP, evaluate, load_scored, news2_last24h, transfer_risk

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = loader.load_expected_results()


@pytest.fixture(scope="module")
def inputs():
    return load_scored(T0)


@pytest.fixture(scope="module")
def result(inputs):
    return evaluate(*inputs, clock=T0)


def test_news2_last24h_matches_expected(inputs):
    stats = news2_last24h(inputs[1], T0).set_index("patient_id")
    for pid, exp in EXPECTED["inpatient_news2_last24h"].items():
        assert int(stats.loc[pid, "max"]) == exp["max"], pid
        assert round(float(stats.loc[pid, "mean"]), 2) == exp["mean"], pid


def test_inpatient_status_matches_patients_csv(result):
    pts = loader.load_patients()
    exp = pts[pts["location"] == "INPATIENT"].set_index("patient_id")["hah_status"].to_dict()
    got = result[result["pathway"] == "home_transfer"].set_index("patient_id")["status"].to_dict()
    assert got == exp


def test_stepdown_candidates_and_status(result):
    pts = loader.load_patients()
    exp = pts[pts["stepdown_status"].notna()].set_index("patient_id")["stepdown_status"].to_dict()
    got = result[result["pathway"] == "stepdown"].set_index("patient_id")["status"].to_dict()
    assert got == exp


def test_rows_have_reasons_and_valid_evidence(result):
    notes = loader.load_notes().set_index("note_id")["patient_id"].to_dict()
    assert list(result.columns) == ["patient_id", "pathway", "status", "reasons", "evidence_ids",
                                    "requires_signoff"]
    for r in result.itertuples():
        assert r.reasons and r.evidence_ids, r.patient_id
        assert r.status in STATUS_GROUP
        assert r.requires_signoff == (r.status == "needs_assessment")
        for e in r.evidence_ids:
            if re.fullmatch(r"N-\d+", e):
                assert notes[e] == r.patient_id


def test_no_patient_specific_logic():
    rx = re.compile(r"[\"'](P|I|A)-\d")
    for f in ("eligibility.py", "capacity.py"):
        src = (ROOT / "ops" / f).read_text(encoding="utf-8").split('if __name__ == "__main__"')[0]
        assert not rx.search(src), f


def test_transfer_risk_matches_expected(inputs):
    risk = transfer_risk(inputs[1], T0, inputs[0])
    assert risk == pytest.approx(EXPECTED["inpatient_transfer_risk_score"])


def test_capacity_static_and_kits():
    res = loader.load_resources()
    cap = get_capacity(T0)
    assert cap["kits"]["free"] == res["monitoring_kits"]["available_now"]
    assert cap["kits"]["cleaning_ready"] is False
    later = get_capacity("2026-11-21T15:00")
    k = res["monitoring_kits"]
    assert later["kits"]["free"] == k["available_now"] + k["in_cleaning"]
    assert later["kits"]["in_cleaning"] == 0
    assert cap["beds"]["total"] == res["physical_beds"]["total"]
    assert cap["beds"]["free"] == res["physical_beds"]["total"] - res["physical_beds"]["occupied"]
    assert cap["virtual_slots"]["total"] == res["virtual_ward"]["slots_total"]
    assert cap["virtual_slots"]["occupied"] == res["virtual_ward"]["occupied"]
    assert cap["visits"]["booked"] == res["visits_booked_today"]
    assert cap["visits"]["capacity"] == sum(t["visit_capacity_per_day"] for t in res["mobile_teams"])
    assert cap["primary_care_handover"]["capacity"] == res["primary_care_handover"]["capacity_today"]


def test_capacity_escalation_returns_kit():
    cap = get_capacity("2026-11-21T15:00", fake_ward_state(escalated=["P-002"]))
    assert "P-002" in cap["pending_readmissions"]
    assert cap["kits_returning"] == 1


def test_capacity_fast_and_serialisable():
    import json
    t = time.perf_counter()
    cap = get_capacity(T0)
    assert time.perf_counter() - t < 0.3
    json.dumps(cap)


def test_clock_purity():
    clock = "2026-11-20T12:00"
    pts, vit, pl, nt = load_scored(clock)
    assert vit["ts"].max() <= pd.Timestamp(clock)
    df = evaluate(pts, vit, pl, nt, clock)
    assert len(df)
    notes = loader.load_notes().set_index("note_id")["ts"]
    for r in df.itertuples():
        for e in r.evidence_ids:
            if re.fullmatch(r"N-\d+", e):
                assert notes[e] <= pd.Timestamp(clock)
    assert eligibility.news2_last24h(vit, clock)["patient_id"].nunique() > 0
