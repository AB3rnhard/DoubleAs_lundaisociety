"""Pure presentation helpers for the Capacity & What-if page (no Streamlit).

Owner: B (Teammate B). Built in prompt B3.

Public interface: plan_item_id, describe_moves, plan_summary, plan_evidence, signoff_tasks, binding_resources.
"""
from __future__ import annotations

import hashlib
import json
import logging

import pandas as pd

from core.contracts import Plan, to_dict

logger = logging.getLogger(__name__)

_EXPEDITE = "EXPEDITE_"
RESOURCE_LABELS = {"beds": "Beds", "kits": "Kits", "visits": "Visits", "slots": "Virtual slots"}


def plan_item_id(plan: Plan, clock: str, n: int, escalated) -> str:
    """Stable id: PLAN-<profile>-<HHMM>-n<N>-e<#escalated>-<6-char hash of the plan>."""
    digest = hashlib.sha1(json.dumps(to_dict(plan), sort_keys=True, default=str).encode("utf-8")).hexdigest()[:6]
    return f"PLAN-{plan.profile}-{clock[11:13]}{clock[14:16]}-n{n}-e{len(list(escalated))}-{digest}"


def _label(pid: str, names: dict, diagnoses: dict) -> str:
    extra = names.get(pid) or diagnoses.get(pid) or ""
    return f"{pid} {extra}".strip()


def describe_moves(plan: Plan, names: dict, admissions_df: pd.DataFrame) -> list[tuple[str, list[str]]]:
    """[(label, display strings)] for each non-empty move group, in a fixed order."""
    diag = dict(zip(admissions_df["admission_id"], admissions_df["working_diagnosis"], strict=True))
    out = [("Transfer home", [_label(p, names, diag) for p in plan.transfers_home]),
           ("Step-down to primary care", [_label(p, names, diag) for p in plan.stepdowns]),
           ("Direct to virtual ward", [_label(p, names, diag) for p in plan.direct_home]),
           ("Expedite discharge", [_label(p, names, diag) for p in plan.expedite_discharges])]
    if plan.external_transfers:
        out.append(("External transfer (count)", [str(plan.external_transfers)]))
    return [(label, items) for label, items in out if items]


def plan_summary(plan: Plan) -> str:
    """One deterministic sentence of solver output (not AI). Every number comes from the Plan."""
    parts = [f"Solver output ({plan.solver}, {plan.profile}): places {plan.placed} of {plan.needing_placement}",
             f"{plan.external_transfers} external transfer(s)",
             f"{len(plan.transfers_home)} transfer(s) home",
             f"{len(plan.stepdowns)} step-down(s)",
             f"{len(plan.direct_home)} direct to virtual ward",
             f"{len(plan.expedite_discharges)} expedited discharge(s)"]
    flags = [t for t, on in (("overtime team", plan.overtime), ("overflow bed", plan.overflow_used),
                             ("cleaning kit", plan.uses_cleaning_kit)) if on]
    tail = f"; uses {', '.join(flags)}" if flags else ""
    return f"{parts[0]}; " + ", ".join(parts[1:]) + f"; {plan.spare_beds} spare bed(s); score {plan.objective}{tail}."


def _strip(subject: str) -> str:
    return subject.removeprefix(_EXPEDITE)


def plan_evidence(plan: Plan, eligibility_df: pd.DataFrame) -> list[str]:
    """Moved/placed ids plus the eligibility evidence of moved patients; de-duplicated, stable order."""
    moved = [*plan.transfers_home, *plan.stepdowns, *plan.direct_home, *plan.expedite_discharges]
    ev = list(moved)
    by_pid = {r.patient_id: list(r.evidence_ids) for r in eligibility_df.itertuples()}
    for pid in moved:
        ev.extend(by_pid.get(pid, []))
    return list(dict.fromkeys(ev))


def _action(plan: Plan, subject: str) -> str:
    if subject.startswith(_EXPEDITE):
        return "expedite discharge"
    return "step-down" if subject in plan.stepdowns else "transfer home"


def signoff_tasks(plan: Plan, item_id: str, clock: str, eligibility_df: pd.DataFrame | None = None) -> list[dict]:
    """One open sign-off task per plan.signoffs_required entry."""
    by_pid = {}
    if eligibility_df is not None:
        by_pid = {r.patient_id: list(r.evidence_ids) for r in eligibility_df.itertuples()}
    tasks = []
    for subject in plan.signoffs_required:
        pid = _strip(subject)
        tasks.append({"task_id": f"{item_id}:{subject}", "type": "signoff", "subject": subject,
                      "patient_id": pid, "action": _action(plan, subject), "plan_item_id": item_id,
                      "profile": plan.profile, "created_at": clock, "status": "open",
                      "evidence_ids": list(dict.fromkeys([pid, *by_pid.get(pid, [])]))})
    return tasks


def binding_resources(plan: Plan) -> list[str]:
    """Usage keys where used >= capacity (the constraints that are full)."""
    return [k for k, (used, cap) in plan.usage.items() if used >= cap]


if __name__ == "__main__":
    from clinical.queue import get_ward_state
    from core import loader
    from core.contracts import T0
    from ops import eligibility, whatif_cpsat
    esc = [get_ward_state("2026-11-21T14:00").queue[0].patient_id]  # top of the real queue once flagged
    ws = get_ward_state(T0, esc)
    plans = whatif_cpsat.run_all_profiles(5, ws)
    names = dict(zip(loader.load_patients()["patient_id"], loader.load_patients()["name"], strict=True))
    adm = loader.load_admissions()
    elig = eligibility.evaluate(*eligibility.load_scored(T0), T0)
    for pl in plans.values():
        iid = plan_item_id(pl, T0, 5, esc)
        print(iid, plan_summary(pl))
        print(describe_moves(pl, names, adm), binding_resources(pl))
        print(signoff_tasks(pl, iid, T0, elig))
