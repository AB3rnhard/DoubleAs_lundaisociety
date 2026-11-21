"""Greedy fallback for the what-if planner (always feasible, not optimal).

Owner: B (Teammate B). Built in prompt B2.

Public interface: run_whatif(n, ward_state, profile) -> Plan  (same signature as whatif_cpsat)
"""
from __future__ import annotations

import copy
import logging

from core.contracts import PROFILES, Plan, Profile, WardState
from ops.whatif_model import Decisions, WhatIfInputs, build_inputs, evaluate_plan, make_plan

logger = logging.getLogger(__name__)


def _complete(inp: WhatIfInputs, d: Decisions, profile: Profile) -> None:
    """Fill bed/external (and overflow) from the chosen levers and home placements."""
    rest = [p.pid for p in sorted(inp.people, key=lambda p: p.kind != "escalated") if p.pid not in d.home]
    cap = inp.free_beds + len(d.transfer) + len(d.expedite)
    w = inp.weights[profile]
    d.overflow = bool(w["allow_overflow"] and inp.overflow_max >= 1 and len(rest) > cap)
    cap += int(d.overflow)
    d.bed, d.external = set(rest[:cap]), set(rest[cap:])


def _ok(inp, d, profile) -> bool:
    _complete(inp, d, profile)
    return evaluate_plan(inp, d, profile)[0]


def _obj(inp, d, profile) -> float:
    _complete(inp, d, profile)
    return evaluate_plan(inp, d, profile)[2]


def _try_add(inp, d, profile, apply) -> Decisions | None:
    """Apply an action; if resources break, enable levers (cleaning kit, overtime, step-downs) to repair."""
    t = copy.deepcopy(d)
    apply(t)
    if _ok(inp, t, profile):
        return t
    w = inp.weights[profile]
    smap = {x.pid: x for x in inp.stepdowns}
    if inp.cleaning_lever and not t.cleaning_kit:
        t.cleaning_kit = True
        if _ok(inp, t, profile):
            return t
    if not t.overtime:
        t.overtime = True
        if _ok(inp, t, profile):
            return t
    for x in sorted(inp.stepdowns, key=lambda x: (x.signoff, x.risk, x.pid)):
        if x.pid in t.stepdown or len(t.stepdown) >= inp.pc_cap:
            continue
        used = sum(smap[s].signoff for s in t.stepdown) + len(t.expedite) + sum(
            c.signoff for c in inp.transfers if c.pid in t.transfer)
        if used + x.signoff > w["max_assess"]:
            continue
        t.stepdown.add(x.pid)
        if _ok(inp, t, profile):
            return t
    return None


def _construct(inp: WhatIfInputs, profile: Profile, home_first: bool) -> Decisions:
    d = Decisions()
    homes = [p.pid for p in inp.people if p.home_ok]

    def place_home():
        nonlocal d
        for pid in homes:
            r = _try_add(inp, d, profile, lambda t, pid=pid: t.home.add(pid))
            if r is not None:
                d = r

    def free_beds():
        nonlocal d
        while True:
            _complete(inp, d, profile)
            if not d.external and not d.overflow:
                return
            if len([p for p in inp.people if p.pid not in d.home]) <= inp.free_beds + len(d.transfer) + len(d.expedite):
                return
            moves = [("e", x) for x in inp.expedites if x not in d.expedite]
            moves += [("t", x.pid) for x in sorted(inp.transfers, key=lambda x: (x.signoff, x.risk, x.pid))
                      if x.pid not in d.transfer]
            for kind, pid in moves:
                add = (lambda t, pid=pid: t.expedite.add(pid)) if kind == "e" else (lambda t, pid=pid: t.transfer.add(pid))
                r = _try_add(inp, d, profile, add)
                if r is not None:
                    d = r
                    break
            else:
                return

    (place_home if home_first else free_beds)()
    (free_beds if home_first else place_home)()
    _complete(inp, d, profile)
    return d


def _prune(inp: WhatIfInputs, d: Decisions, profile: Profile) -> Decisions:
    """Drop levers that do not pay for themselves (keeps feasibility, never worsens the objective)."""
    improved = True
    while improved:
        improved = False
        cur = _obj(inp, d, profile)
        for attr in ("transfer", "stepdown", "expedite"):
            for x in sorted(getattr(d, attr)):
                t = copy.deepcopy(d)
                getattr(t, attr).discard(x)
                if _ok(inp, t, profile) and _obj(inp, t, profile) <= cur + 1e-9:
                    d, cur, improved = t, _obj(inp, t, profile), True
        for attr in ("overtime", "cleaning_kit"):
            if getattr(d, attr):
                t = copy.deepcopy(d)
                setattr(t, attr, False)
                if _ok(inp, t, profile) and _obj(inp, t, profile) <= cur + 1e-9:
                    d, cur, improved = t, _obj(inp, t, profile), True
    _complete(inp, d, profile)
    return d


def solve_inputs(inp: WhatIfInputs, profile: Profile) -> Plan:
    best = Decisions()
    _complete(inp, best, profile)          # all-external baseline is always feasible
    best_obj = evaluate_plan(inp, best, profile)[2]
    for home_first in (True, False):
        d = _prune(inp, _construct(inp, profile, home_first), profile)
        ok, _, obj, _ = evaluate_plan(inp, d, profile)
        if ok and obj < best_obj:
            best, best_obj = d, obj
    return make_plan(inp, best, profile, "greedy")


def run_whatif(n: int, ward_state: WardState, profile: Profile) -> Plan:
    return solve_inputs(build_inputs(n, ward_state), profile)


if __name__ == "__main__":
    import time

    from clinical.queue import get_ward_state
    from core import loader
    from core.contracts import T0
    ref = loader.load_expected_results()
    flagged = get_ward_state("2026-11-21T14:00").queue[0].patient_id  # top of the real queue once flagged
    for label, key, esc in (("+5", "whatif_plus5_reference", []),
                            ("+5 & readmission", "whatif_plus5_with_P002_readmission_reference", [flagged])):
        ws = get_ward_state(T0, esc)
        inp = build_inputs(5, ws)
        for prof in PROFILES:
            t = time.perf_counter()
            pl = solve_inputs(inp, prof)
            print(f"{label:22} {prof:13} obj {pl.objective:8.2f} (ref {ref[key][prof]['objective']:8.2f}) "
                  f"placed {pl.placed}/{pl.needing_placement} tr={pl.transfers_home} sd={pl.stepdowns} "
                  f"home={pl.direct_home} exp={pl.expedite_discharges} spare={pl.spare_beds} "
                  f"{time.perf_counter() - t:.3f}s")
