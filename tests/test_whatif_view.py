"""Tests for ops.whatif_view helpers. Owner: Teammate B."""
import re

import pytest

from core import loader
from core.contracts import PROFILES, T0, fake_ward_state
from ops import eligibility, whatif_cpsat
from ops.whatif_view import (
    binding_resources,
    describe_moves,
    plan_evidence,
    plan_item_id,
    plan_summary,
    signoff_tasks,
)

EXP = loader.load_expected_results()
READMIT = "whatif_plus5_with_P002_readmission_reference"
BASE = "whatif_plus5_reference"
CASES = {BASE: [], READMIT: ["P-002"]}


@pytest.fixture(scope="module")
def plans():
    return {k: whatif_cpsat.run_all_profiles(5, fake_ward_state(T0, esc)) for k, esc in CASES.items()}


@pytest.fixture(scope="module")
def elig():
    return eligibility.evaluate(*eligibility.load_scored(T0), T0)


@pytest.fixture(scope="module")
def names():
    p = loader.load_patients()
    return dict(zip(p["patient_id"], p["name"], strict=True))


def test_signoff_tasks_match_plan(plans, elig):
    for key, esc in CASES.items():
        for prof, plan in plans[key].items():
            iid = plan_item_id(plan, T0, 5, esc)
            tasks = signoff_tasks(plan, iid, T0, elig)
            assert [t["subject"] for t in tasks] == plan.signoffs_required
            assert sorted(t["subject"] for t in tasks) == sorted(EXP[key][prof]["requires_clinician_assessment"])
            for t in tasks:
                assert t["task_id"] == f"{iid}:{t['subject']}"
                assert t["patient_id"] == t["subject"].removeprefix("EXPEDITE_")
                assert t["type"] == "signoff" and t["status"] == "open" and t["created_at"] == T0
                assert t["profile"] == prof and t["plan_item_id"] == iid
                assert t["action"] in {"step-down", "transfer home", "expedite discharge"}
                assert (t["action"] == "expedite discharge") == t["subject"].startswith("EXPEDITE_")


def test_evidence_ids_exist(plans, elig):
    notes = loader.load_notes().set_index("note_id")["patient_id"].to_dict()
    known = set(loader.load_patients()["patient_id"]) | set(loader.load_admissions()["admission_id"])
    for key in CASES:
        for plan in plans[key].values():
            ev = plan_evidence(plan, elig)
            assert ev == list(dict.fromkeys(ev))
            assert ev == plan_evidence(plan, elig)
            for e in ev:
                assert e in known or e in notes or re.fullmatch(r"[a-z0-9_~]+=?.*", e) and ("=" in e or "~" in e), e
            moved = {*plan.transfers_home, *plan.stepdowns, *plan.direct_home, *plan.expedite_discharges}
            assert moved <= set(ev)


def test_item_ids_stable_and_distinct(plans):
    ids = {}
    for prof, plan in plans[READMIT].items():
        a = plan_item_id(plan, "2026-11-21T14:00", 5, ["P-002"])
        assert a == plan_item_id(plan, "2026-11-21T14:00", 5, ["P-002"])
        assert re.fullmatch(rf"PLAN-{prof}-1400-n5-e1-[0-9a-f]{{6}}", a)
        ids[prof] = a
    assert len(set(ids.values())) == len(PROFILES)
    bal = plans[READMIT]["balanced"]
    assert plan_item_id(bal, T0, 5, []) != plan_item_id(bal, T0, 5, ["P-002"])
    assert plan_item_id(bal, T0, 5, []) != plan_item_id(bal, T0, 6, [])


def test_binding_includes_kits_for_safety_first(plans):
    for key in CASES:
        pl = plans[key]["safety_first"]
        assert "kits" in binding_resources(pl)
        assert pl.usage["kits"][0] == EXP[key]["safety_first"]["kits_used"]
        for k, (used, cap) in pl.usage.items():
            assert (k in binding_resources(pl)) == (used >= cap)


def test_describe_moves_and_summary(plans, names):
    adm = loader.load_admissions()
    for key in CASES:
        for prof, plan in plans[key].items():
            ref = EXP[key][prof]
            moves = dict(describe_moves(plan, names, adm))
            assert [m.split()[0] for m in moves.get("Transfer home", [])] == ref["transfers_to_virtual_ward"]
            assert [m.split()[0] for m in moves.get("Step-down to primary care", [])] == ref["stepdowns_to_primary_care"]
            assert [m.split()[0] for m in moves.get("Direct to virtual ward", [])] == ref["direct_to_virtual_ward"]
            assert ("External transfer (count)" in moves) == (ref["external_transfers"] > 0)
            assert all(len(m.split()) > 1 for k, items in moves.items() if not k.startswith("External") for m in items)
            s = plan_summary(plan)
            assert f"places {ref['placed']} of {ref['patients_needing_placement']}" in s
            assert str(ref["objective"]) in s and "Solver output" in s
