"""Shared what-if model: inputs, plain-Python plan checker and Plan builder (no solver code).

Owner: B (Teammate B). Built in prompt B2. Semantics are a port of the brute-force reference in
scripts/generate_datapack.py (never imported from here).

Public interface: build_inputs, evaluate_plan, make_plan, Decisions, WhatIfInputs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache

import pandas as pd

from core import loader
from core.contracts import Plan, Profile, WardState
from ops import eligibility

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Cand:
    """Transfer / step-down candidate."""
    pid: str
    risk: float
    visits: int
    signoff: bool


@dataclass(frozen=True)
class Person:
    """Someone who needs a bed (admission or escalated home patient)."""
    pid: str
    kind: str            # "admission" | "escalated"
    home_ok: bool
    home_visits: int


@dataclass
class WhatIfInputs:
    clock: str
    free_beds: int
    beds_total: int
    overflow_max: int
    kits_avail: int
    cleaning_lever: bool
    booked: int
    team_cap: int
    ot_extra: int
    slots_total: int
    slots_occ: int
    pc_cap: int
    transfers: list[Cand]
    stepdowns: list[Cand]
    expedites: list[str]
    people: list[Person]
    esc_visits: int
    esc_slots: int
    esc_kits: int
    weights: dict[str, dict]
    extra: dict = field(default_factory=dict)

    @property
    def n_escalated(self) -> int:
        return sum(1 for p in self.people if p.kind == "escalated")


@dataclass
class Decisions:
    transfer: set[str] = field(default_factory=set)
    stepdown: set[str] = field(default_factory=set)
    expedite: set[str] = field(default_factory=set)
    home: set[str] = field(default_factory=set)
    bed: set[str] = field(default_factory=set)
    external: set[str] = field(default_factory=set)
    overflow: bool = False
    overtime: bool = False
    cleaning_kit: bool = False


@lru_cache(maxsize=8)
def _eligibility_at(clock: str):
    patients, vitals, plans, notes = eligibility.load_scored(clock)
    elig = eligibility.evaluate(patients, vitals, plans, notes, clock)
    risk = eligibility.transfer_risk(vitals, clock, patients)
    return patients, elig, risk


def build_inputs(n: int, ward_state: WardState, extra: dict | None = None) -> WhatIfInputs:
    extra = dict(extra or {})
    clock = ward_state.clock
    c = pd.Timestamp(clock)
    patients, elig, risk = _eligibility_at(clock)
    res = loader.load_resources()
    P = patients.set_index("patient_id")
    escalated = [p for p in ward_state.escalated]
    bad = [p for p in escalated if p not in P.index or P.loc[p, "location"] != "VIRTUAL_WARD"]
    if bad:
        logger.warning("escalated ids not in the virtual ward are ignored: %s", bad)
    escalated = [p for p in escalated if p not in bad]

    def cand(pid: str, signoff: bool) -> Cand:
        return Cand(pid, float(risk.get(pid, 0.0)), int(P.loc[pid, "visits_per_day"]), bool(signoff))

    ok = {"suitable", "needs_assessment"}
    transfers = [cand(r.patient_id, r.requires_signoff) for r in elig.itertuples()
                 if r.pathway == "home_transfer" and r.status in ok]
    stepdowns = [cand(r.patient_id, r.requires_signoff) for r in elig.itertuples()
                 if r.pathway == "stepdown" and r.status in ok and r.patient_id not in escalated]

    free_beds = res["physical_beds"]["total"] - res["physical_beds"]["occupied"]
    expedites: list[str] = []
    for r in elig.itertuples():
        if r.pathway == "home_transfer" and r.status == "discharge_planned":
            at = P.loc[r.patient_id, "planned_discharge_at"]
            if pd.notna(at) and at > c:
                expedites.append(r.patient_id)
            else:
                logger.info("planned discharge of %s already due at %s: bed counted as free", r.patient_id, clock)
                free_beds += 1
    free_beds += int(extra.get("beds", 0))

    adm = loader.load_admissions().sort_values("eta", kind="stable")
    if n > len(adm):
        logger.warning("n=%d exceeds %d incoming admissions; clipped", n, len(adm))
    adm = adm.head(max(0, n))
    catchment = {eligibility._norm(m) for m in res["catchment_municipalities"]}
    people = []
    for a in adm.itertuples():
        home_ok = (bool(a.direct_hah_possible) and eligibility._norm(a.municipality) in catchment
                   and str(a.consent_hospital_at_home).strip().lower() == "yes")
        people.append(Person(a.admission_id, "admission", home_ok, int(a.visits_per_day_if_home)))
    for pid in sorted(escalated):
        people.append(Person(pid, "escalated", False, 0))

    kits = res["monitoring_kits"]
    cleaning_ready = c >= pd.Timestamp(kits["cleaning_ready_at"])
    kits_avail = kits["available_now"] + (kits["in_cleaning"] if cleaning_ready else 0) + int(extra.get("kits", 0))
    return WhatIfInputs(
        clock=clock, free_beds=free_beds, beds_total=res["physical_beds"]["total"],
        overflow_max=res["physical_beds"]["overflow_beds_max"],
        kits_avail=kits_avail, cleaning_lever=(not cleaning_ready and kits["in_cleaning"] > 0),
        booked=res["visits_booked_today"], team_cap=sum(t["visit_capacity_per_day"] for t in res["mobile_teams"]),
        ot_extra=res["overtime"]["extra_visit_capacity"], slots_total=res["virtual_ward"]["slots_total"],
        slots_occ=res["virtual_ward"]["occupied"], pc_cap=res["primary_care_handover"]["capacity_today"],
        transfers=sorted(transfers, key=lambda x: x.pid), stepdowns=sorted(stepdowns, key=lambda x: x.pid),
        expedites=sorted(expedites), people=people,
        esc_visits=sum(int(P.loc[p, "visits_per_day"]) for p in escalated),
        esc_slots=len(escalated), esc_kits=sum(1 for p in escalated if pd.notna(P.loc[p, "kit_id"])
                                              and str(P.loc[p, "kit_id"]).strip() != ""),
        weights=loader.load_expected_results()["profile_definitions"], extra=extra)


def spare_beds(inp: WhatIfInputs, d: Decisions) -> int:
    return inp.free_beds + len(d.transfer) + len(d.expedite) + int(d.overflow) - len(d.bed)


def evaluate_plan(inp: WhatIfInputs, d: Decisions, profile: Profile):
    """Check every rule. Returns (feasible, violations, objective, usage)."""
    w = inp.weights[profile]
    v: list[str] = []
    tmap = {x.pid: x for x in inp.transfers}
    smap = {x.pid: x for x in inp.stepdowns}
    for name, chosen, allowed in (("transfer", d.transfer, tmap), ("stepdown", d.stepdown, smap),
                                  ("expedite", d.expedite, set(inp.expedites))):
        for x in chosen:
            if x not in allowed:
                v.append(f"{name} {x} is not a candidate")
    pmap = {p.pid: p for p in inp.people}
    for p in inp.people:
        k = (p.pid in d.bed) + (p.pid in d.external) + (p.pid in d.home)
        if k != 1:
            v.append(f"{p.pid} assigned {k} times")
    for x in d.home:
        if x in pmap and not pmap[x].home_ok:
            v.append(f"{x} cannot go direct home")
    for x in d.bed | d.external | d.home:
        if x not in pmap:
            v.append(f"unknown person {x}")
    if d.cleaning_kit and not inp.cleaning_lever:
        v.append("cleaning kit lever not available")
    if d.overflow and not (w["allow_overflow"] and inp.overflow_max >= 1):
        v.append("overflow not allowed")
    if len(d.stepdown) > inp.pc_cap:
        v.append("primary-care capacity exceeded")

    tr = [tmap[x] for x in d.transfer if x in tmap]
    sd = [smap[x] for x in d.stepdown if x in smap]
    assess = sum(x.signoff for x in tr + sd) + len(d.expedite)
    if assess > w["max_assess"]:
        v.append(f"assessments {assess} > {w['max_assess']}")
    spare = spare_beds(inp, d)
    if spare < 0:
        v.append("beds over capacity")
    if d.overflow and spare != 0:
        v.append("overflow bed not filled")
    n_home = len(d.home)
    kits_used = len(tr) + n_home
    kits_supply = inp.kits_avail + int(d.cleaning_kit) + len(sd) + inp.esc_kits
    if kits_used > kits_supply:
        v.append(f"kits {kits_used} > {kits_supply}")
    visits = (inp.booked - sum(x.visits for x in sd) - inp.esc_visits + sum(x.visits for x in tr)
              + sum(pmap[x].home_visits for x in d.home if x in pmap))
    vcap = inp.team_cap + inp.ot_extra * int(d.overtime)
    if visits > vcap:
        v.append(f"visits {visits} > {vcap}")
    slots = inp.slots_occ - len(sd) - inp.esc_slots + len(tr) + n_home
    if slots > inp.slots_total:
        v.append(f"slots {slots} > {inp.slots_total}")

    cents = lambda x: round(x * 100)
    obj = (w["w_external"] * len(d.external) + w["w_assess"] * assess + w["w_overflow"] * int(d.overflow)
           + w["w_overtime"] * int(d.overtime) + w["w_kitdelay"] * int(d.cleaning_kit)
           + w["w_risk"] * sum(cents(x.risk) for x in tr) / 100 - w["w_buffer"] * spare
           + 0.1 * (len(tr) + len(sd)))
    ovf = int(d.overflow)
    usage = {"beds": (inp.beds_total - spare + ovf, inp.beds_total + ovf),
             "kits": (kits_used, kits_supply), "visits": (visits, vcap),
             "slots": (slots, inp.slots_total)}
    return (not v, v, round(obj, 2), usage)


def make_plan(inp: WhatIfInputs, d: Decisions, profile: Profile, solver: str,
              objective: float | None = None) -> Plan:
    ok, viol, obj, usage = evaluate_plan(inp, d, profile)
    if not ok:
        logger.error("%s plan for %s violates rules: %s", solver, profile, viol)
    if objective is not None and abs(objective - obj) > 0.01:
        logger.error("%s objective %.2f disagrees with checker %.2f (%s)", solver, objective, obj, profile)
    tmap = {x.pid: x for x in inp.transfers}
    smap = {x.pid: x for x in inp.stepdowns}
    signoffs = ([x for x in d.transfer if tmap[x].signoff] + [x for x in d.stepdown if smap[x].signoff]
                + [f"EXPEDITE_{x}" for x in d.expedite])
    needing = len(inp.people)
    return Plan(profile=profile, placed=needing - len(d.external), needing_placement=needing,
                external_transfers=len(d.external), transfers_home=sorted(d.transfer),
                stepdowns=sorted(d.stepdown), direct_home=sorted(d.home),
                expedite_discharges=sorted(d.expedite), overflow_used=d.overflow, overtime=d.overtime,
                uses_cleaning_kit=d.cleaning_kit, signoffs_required=sorted(signoffs),
                spare_beds=spare_beds(inp, d), usage=usage,
                objective=objective if objective is not None else obj, solver=solver)  # type: ignore[arg-type]
