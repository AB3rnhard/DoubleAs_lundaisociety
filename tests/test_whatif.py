"""Tests for the what-if model, CP-SAT and greedy fallback. Owner: Teammate B."""
import re
import sys
import time
from pathlib import Path

import pytest

from core import loader
from core.contracts import PROFILES, T0, fake_ward_state
from ops import greedy, whatif_cpsat
from ops.whatif_model import build_inputs, evaluate_plan

EXP = loader.load_expected_results()
ROOT = Path(__file__).resolve().parent.parent
BASE, READMIT = "whatif_plus5_reference", "whatif_plus5_with_P002_readmission_reference"
CASES = [(BASE, []), (READMIT, ["P-002"])]
ALL = [(k, e, p) for k, e in CASES for p in PROFILES]


def ws(clock=T0, escalated=None):
    return fake_ward_state(clock=clock, escalated=escalated or [])


@pytest.fixture(scope="module")
def solved():
    out = {}
    for key, esc in CASES:
        inp = build_inputs(5, ws(T0, esc))
        out[key] = (inp, whatif_cpsat.run_all_profiles(5, ws(T0, esc)), {p: greedy.solve_inputs(inp, p) for p in PROFILES})
    return out


def _decisions(plan, inp):
    from ops.whatif_model import Decisions
    home = set(plan.direct_home)
    rest = [p.pid for p in sorted(inp.people, key=lambda p: p.kind != "escalated") if p.pid not in home]
    cap = inp.free_beds + len(plan.transfers_home) + len(plan.expedite_discharges) + int(plan.overflow_used)
    return Decisions(set(plan.transfers_home), set(plan.stepdowns), set(plan.expedite_discharges), home,
                     set(rest[:cap]), set(rest[cap:]), plan.overflow_used, plan.overtime, plan.uses_cleaning_kit)


@pytest.mark.parametrize("key,esc,profile", ALL)
def test_cpsat_matches_reference(solved, key, esc, profile):
    r, pl = EXP[key][profile], solved[key][1][profile]
    assert pl.solver == "cp-sat"
    assert pl.objective == pytest.approx(r["objective"], abs=0.01)
    assert (pl.placed, pl.external_transfers, pl.needing_placement) == (
        r["placed"], r["external_transfers"], r["patients_needing_placement"])
    assert pl.transfers_home == sorted(r["transfers_to_virtual_ward"])
    assert pl.stepdowns == sorted(r["stepdowns_to_primary_care"])
    assert pl.direct_home == sorted(r["direct_to_virtual_ward"])
    assert bool(pl.expedite_discharges) == r["expedite_I02_discharge"]
    assert pl.overflow_used == r["overflow_bed_used"]
    assert pl.overtime == r["overtime_team"]
    assert pl.uses_cleaning_kit == r["uses_kit_from_cleaning_ready_15_00"]
    assert pl.spare_beds == r["spare_beds_after_plan"]
    assert set(pl.signoffs_required) == set(r["requires_clinician_assessment"])
    assert pl.usage["kits"] == (r["kits_used"], r["kits_supply"])
    assert pl.usage["visits"] == (r["visits_booked"], r["visit_capacity"])
    assert pl.usage["slots"][0] == r["virtual_slots_used"]


@pytest.mark.parametrize("key,esc,profile", ALL)
def test_checker_agrees_with_cpsat(solved, key, esc, profile):
    inp, cp, _ = solved[key]
    ok, viol, obj, usage = evaluate_plan(inp, _decisions(cp[profile], inp), profile)
    assert ok, viol
    assert obj == pytest.approx(cp[profile].objective, abs=0.01)
    assert usage == cp[profile].usage


@pytest.mark.parametrize("key,esc,profile", ALL)
def test_greedy_feasible_and_not_better(solved, key, esc, profile):
    inp, cp, gr = solved[key]
    g = gr[profile]
    assert g.solver == "greedy"
    ok, viol, _o, _ = evaluate_plan(inp, _decisions(g, inp), profile)
    assert ok, viol
    assert g.objective >= cp[profile].objective - 1e-6


def test_greedy_public_entry():
    pl = greedy.run_whatif(5, ws(T0, ["P-002"]), "balanced")
    assert pl.solver == "greedy" and pl.needing_placement == 6


def test_ortools_missing_falls_back_to_greedy(monkeypatch):
    for mod in [m for m in sys.modules if m == "ortools" or m.startswith("ortools.")]:
        monkeypatch.setitem(sys.modules, mod, None)
    pl = whatif_cpsat.run_whatif(5, ws(T0, ["P-002"]), "balanced")
    assert pl.solver == "greedy"
    inp = build_inputs(5, ws(T0, ["P-002"]))
    assert evaluate_plan(inp, _decisions(pl, inp), "balanced")[0]


def test_demo_clock_story_14h():
    """README story: 5/6, 6/6, 6/6 + 1 spare with P-002 readmitted (objectives differ slightly: risk at 14:00)."""
    plans = whatif_cpsat.run_all_profiles(5, ws("2026-11-21T14:00", ["P-002"]))
    for p in PROFILES:
        r, pl = EXP[READMIT][p], plans[p]
        assert (pl.placed, pl.external_transfers, pl.spare_beds) == (
            r["placed"], r["external_transfers"], r["spare_beds_after_plan"])
        assert (len(pl.transfers_home), len(pl.stepdowns), len(pl.direct_home), bool(pl.expedite_discharges)) == (
            len(r["transfers_to_virtual_ward"]), len(r["stepdowns_to_primary_care"]),
            len(r["direct_to_virtual_ward"]), r["expedite_I02_discharge"])


@pytest.mark.parametrize("key,esc", CASES)
def test_shadow_price_kits(key, esc):
    sp = whatif_cpsat.shadow_price(5, ws(T0, esc), "safety_first", "kits")
    print(f"shadow price kits [{key}] safety_first = {sp}")
    assert sp >= 1


def test_shadow_price_beds_supported():
    assert whatif_cpsat.shadow_price(5, ws(T0), "safety_first", "beds") >= 0
    with pytest.raises(ValueError):
        whatif_cpsat.shadow_price(5, ws(T0), "safety_first", "unicorns")


def test_n_zero():
    for esc in ([], ["P-002"]):
        for p in PROFILES:
            pl = whatif_cpsat.run_whatif(0, ws(T0, esc), p)
            assert pl.placed == pl.needing_placement == len(esc)
            assert pl.external_transfers == 0


def test_n_too_large_is_clipped():
    n_rows = len(loader.load_admissions())
    pl = whatif_cpsat.run_whatif(n_rows + 50, ws(T0), "balanced")
    assert pl.needing_placement == n_rows
    assert greedy.run_whatif(n_rows + 50, ws(T0), "balanced").needing_placement == n_rows


def test_timing_all_profiles():
    t = time.perf_counter()
    whatif_cpsat.run_all_profiles(5, ws(T0, ["P-002"]))
    assert time.perf_counter() - t < 2


def test_no_patient_specific_logic():
    rx = re.compile(r"[\"'](P|I|A)-\d")
    for f in ("whatif_model.py", "whatif_cpsat.py", "greedy.py"):
        text = (ROOT / "ops" / f).read_text(encoding="utf-8")
        assert not rx.search(text), f


def test_real_ward_state_escalation_drives_the_whatif():
    """Story link: Patient page escalates a pid -> get_ward_state(..., escalated) -> run_whatif (real queue, no fakes)."""
    from clinical.queue import get_ward_state
    pid = get_ward_state("2026-11-21T14:00").queue[0].patient_id  # top of the queue once flagged (14:00 story clock)
    for key, esc in ((BASE, []), (READMIT, [pid])):
        state = get_ward_state(T0, esc)
        assert state.escalated == esc
        for p in PROFILES:
            r, pl = EXP[key][p], whatif_cpsat.run_whatif(5, state, p)
            assert pl.objective == pytest.approx(r["objective"], abs=0.01)
            assert (pl.placed, pl.needing_placement, pl.external_transfers) == (
                r["placed"], r["patients_needing_placement"], r["external_transfers"])
