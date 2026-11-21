"""Current ward capacity state (beds, kits, visits, slots) at a clock time.

Owner: B (Teammate B). Built in prompt B1.

Public interface: get_capacity(clock, ward_state) -> dict
"""
from __future__ import annotations

import logging

import pandas as pd

from core import loader
from core.contracts import T0, WardState, fake_ward_state
from ops import eligibility

logger = logging.getLogger(__name__)


def get_capacity(clock: str = T0, ward_state: WardState | None = None) -> dict:
    """Static capacity from resources.json; dynamics = cleaning kit ready time + escalated patients."""
    if ward_state is None:
        ward_state = fake_ward_state(clock)
    res = loader.load_resources()
    patients = loader.load_patients()
    c = pd.Timestamp(clock)

    beds, vw, kits = res["physical_beds"], res["virtual_ward"], res["monitoring_kits"]
    vw_rows = int((patients["location"] == "VIRTUAL_WARD").sum())
    if vw_rows != vw["occupied"]:
        logger.warning("virtual ward occupied mismatch: resources=%s patients.csv=%s", vw["occupied"], vw_rows)

    cleaning_ready = c >= pd.Timestamp(kits["cleaning_ready_at"])
    in_cleaning = 0 if cleaning_ready else kits["in_cleaning"]
    kits_free = kits["available_now"] + (kits["in_cleaning"] if cleaning_ready else 0)

    esc = set(ward_state.escalated)
    with_kit = patients[patients["patient_id"].isin(esc) & patients["kit_id"].notna()]

    teams = res["mobile_teams"]
    plans = loader.load_monitoring_plans()
    vitals = loader.at_clock(loader.load_vitals(), clock)
    inp_ids = patients.loc[patients["location"] == "INPATIENT", "patient_id"]
    scored = _score(vitals[vitals["patient_id"].isin(inp_ids)], plans)

    disc = patients[patients["planned_discharge_at"].notna()]
    return {
        "clock": clock,
        "beds": {"total": beds["total"], "occupied": beds["occupied"],
                 "free": beds["total"] - beds["occupied"],
                 "overflow_max": beds["overflow_beds_max"], "overflow_used": 0},
        "virtual_slots": {"total": vw["slots_total"], "occupied": vw["occupied"],
                          "free": vw["slots_total"] - vw["occupied"]},
        "kits": {"total": kits["total"], "in_use": kits["in_use"], "free": kits_free,
                 "in_cleaning": in_cleaning, "cleaning_ready_at": kits["cleaning_ready_at"],
                 "cleaning_ready": bool(cleaning_ready)},
        "visits": {"booked": res["visits_booked_today"],
                   "capacity": sum(t["visit_capacity_per_day"] for t in teams),
                   "overtime_extra": res["overtime"]["extra_visit_capacity"],
                   "overtime_cost_sek": res["overtime"]["cost_sek"]},
        "primary_care_handover": {"capacity": res["primary_care_handover"]["capacity_today"],
                                  "team": res["primary_care_handover"]["team"]},
        "transfer_out": dict(res["transfer_out"]),
        "pending_readmissions": list(ward_state.escalated),
        "kits_returning": len(with_kit),
        "transfer_risk": eligibility.transfer_risk(scored, clock, patients),
        "planned_discharges": [{"patient_id": r.patient_id, "at": r.planned_discharge_at.strftime("%Y-%m-%dT%H:%M")}
                               for r in disc.itertuples()],
    }


def _score(vitals: pd.DataFrame, plans: pd.DataFrame) -> pd.DataFrame:
    from ops._news2 import resolve_score_df
    return resolve_score_df()(vitals, plans)


if __name__ == "__main__":
    import json
    print(json.dumps(get_capacity(T0), indent=1))
    print(json.dumps(get_capacity("2026-11-21T15:00", fake_ward_state(escalated=["P-002"])), indent=1))
