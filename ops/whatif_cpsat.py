"""CP-SAT what-if planner: place +N admissions under a profile.

Owner: B (Teammate B). Built in prompt B2.

Public interface: run_whatif(n, ward_state, profile) -> Plan ; run_all_profiles(n, ward_state) ;
shadow_price(n, ward_state, profile, resource="kits") -> int
"""
from __future__ import annotations

import logging

from core.contracts import PROFILES, Plan, Profile, WardState
from ops import greedy
from ops.whatif_model import Decisions, WhatIfInputs, build_inputs, make_plan

logger = logging.getLogger(__name__)

SCALE = 100


def _w(x: float) -> int:
    return round(x * SCALE)


def solve_inputs(inp: WhatIfInputs, profile: Profile) -> Plan:
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        logger.warning("ortools unavailable: falling back to greedy")
        return greedy.solve_inputs(inp, profile)

    w = inp.weights[profile]
    m = cp_model.CpModel()
    tr = {x.pid: m.NewBoolVar(f"tr_{x.pid}") for x in inp.transfers}
    sd = {x.pid: m.NewBoolVar(f"sd_{x.pid}") for x in inp.stepdowns}
    ex = {x: m.NewBoolVar(f"ex_{x}") for x in inp.expedites}
    ovf = m.NewBoolVar("overflow")
    ot = m.NewBoolVar("overtime")
    ck = m.NewBoolVar("cleaning_kit")
    bed, ext, home = {}, {}, {}
    for p in inp.people:
        bed[p.pid] = m.NewBoolVar(f"bed_{p.pid}")
        ext[p.pid] = m.NewBoolVar(f"ext_{p.pid}")
        opts = [bed[p.pid], ext[p.pid]]
        if p.home_ok:
            home[p.pid] = m.NewBoolVar(f"home_{p.pid}")
            opts.append(home[p.pid])
        m.AddExactlyOne(opts)

    if not (w["allow_overflow"] and inp.overflow_max >= 1):
        m.Add(ovf == 0)
    if not inp.cleaning_lever:
        m.Add(ck == 0)
    n_tr, n_sd, n_ex = sum(tr.values()), sum(sd.values()), sum(ex.values())
    n_bed, n_home = sum(bed.values()), sum(home.values())
    freed = inp.free_beds + n_tr + n_ex
    m.Add(n_bed <= freed + ovf)
    m.Add(n_bed == freed + 1).OnlyEnforceIf(ovf)          # overflow only if it is actually filled
    spare = freed + ovf - n_bed

    m.Add(n_tr + n_home <= inp.kits_avail + ck + n_sd + inp.esc_kits)
    vis_sd = sum(x.visits * sd[x.pid] for x in inp.stepdowns)
    vis_tr = sum(x.visits * tr[x.pid] for x in inp.transfers)
    vis_home = sum(p.home_visits * home[p.pid] for p in inp.people if p.home_ok)
    m.Add(inp.booked - vis_sd - inp.esc_visits + vis_tr + vis_home <= inp.team_cap + inp.ot_extra * ot)
    m.Add(inp.slots_occ - n_sd - inp.esc_slots + n_tr + n_home <= inp.slots_total)
    m.Add(n_sd <= inp.pc_cap)
    assess = (sum(tr[x.pid] for x in inp.transfers if x.signoff) + sum(sd[x.pid] for x in inp.stepdowns if x.signoff)
              + n_ex)
    m.Add(assess <= w["max_assess"])

    m.Minimize(_w(w["w_external"]) * sum(ext.values()) + _w(w["w_assess"]) * assess
               + _w(w["w_overflow"]) * ovf + _w(w["w_overtime"]) * ot + _w(w["w_kitdelay"]) * ck
               + sum(round(w["w_risk"] * round(x.risk * 100)) * tr[x.pid] for x in inp.transfers)
               - _w(w["w_buffer"]) * spare + 10 * (n_tr + n_sd))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5
    solver.parameters.num_workers = 8
    status = solver.Solve(m)
    if status == cp_model.INFEASIBLE or status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        logger.warning("CP-SAT status %s: falling back to greedy", solver.StatusName(status))
        return greedy.solve_inputs(inp, profile)
    if status != cp_model.OPTIMAL:
        logger.warning("CP-SAT returned %s, not OPTIMAL (%s)", solver.StatusName(status), profile)

    on = lambda d: {k for k, v in d.items() if solver.Value(v)}
    dec = Decisions(transfer=on(tr), stepdown=on(sd), expedite=on(ex), home=on(home), bed=on(bed),
                    external=on(ext), overflow=bool(solver.Value(ovf)), overtime=bool(solver.Value(ot)),
                    cleaning_kit=bool(solver.Value(ck)))
    return make_plan(inp, dec, profile, "cp-sat", objective=round(solver.ObjectiveValue() / SCALE, 2))


def run_whatif(n: int, ward_state: WardState, profile: Profile) -> Plan:
    return solve_inputs(build_inputs(n, ward_state), profile)


def run_all_profiles(n: int, ward_state: WardState) -> dict[Profile, Plan]:
    inp = build_inputs(n, ward_state)
    return {p: solve_inputs(inp, p) for p in PROFILES}


def shadow_price(n: int, ward_state: WardState, profile: Profile, resource: str = "kits") -> int:
    """Extra patients placed if one more unit of `resource` ("kits" or "beds") were available."""
    if resource not in ("kits", "beds"):
        raise ValueError(f"unsupported resource {resource!r}")
    base = solve_inputs(build_inputs(n, ward_state), profile)
    more = solve_inputs(build_inputs(n, ward_state, {resource: 1}), profile)
    return more.placed - base.placed


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
        t = time.perf_counter()
        plans = run_all_profiles(5, ws)
        print(f"== {label}  ({time.perf_counter() - t:.2f} s for 3 profiles)")
        for prof, pl in plans.items():
            r = ref[key][prof]
            print(f"{prof:13} obj {pl.objective:8.2f} (ref {r['objective']:8.2f}) placed {pl.placed}/{pl.needing_placement} "
                  f"tr={pl.transfers_home} sd={pl.stepdowns} home={pl.direct_home} exp={pl.expedite_discharges} "
                  f"spare={pl.spare_beds} signoffs={pl.signoffs_required} usage={pl.usage}")
        print("shadow price kits:", {p: shadow_price(5, ws, p, "kits") for p in PROFILES},
              "beds:", {p: shadow_price(5, ws, p, "beds") for p in PROFILES})
