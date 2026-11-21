"""Readmission probability for virtual-ward patients: illustrative hand-set formula, not a validated model.

Owner: A (Teammate A). Added in refinement D1.

Public interface: probability(news2, reasons, age, n_comorbidities, lives_alone) -> float
                  readmission_risk(clock, ward_state=None) -> dict[str, float]   (one per queue patient)

A logistic function of the current NEWS2, queue reasons and a few profile facts. The weights are chosen by
hand so that the demo story reads sensibly; they are not fitted to data. Virtual-ward patients only.
"""
from __future__ import annotations

import logging
import math

from clinical.queue import get_ward_state
from core.contracts import WardState
from core.loader import load_patients

logger = logging.getLogger(__name__)

INTERCEPT = -3.2
WEIGHTS = {"news2": 0.25, "age_per_year_over_65": 0.03, "comorbidity": 0.2, "lives_alone": 0.3,
           "NEWS2_EMERGENCY": 2.0, "NEWS2_URGENT": 1.5, "TREND": 1.2, "SYMPTOM_MESSAGE": 0.6,
           "PLAN_BREACH": 0.4}
REASON_WEIGHTS = {"NEWS2_EMERGENCY", "NEWS2_URGENT", "TREND", "SYMPTOM_MESSAGE", "PLAN_BREACH"}
P_MIN, P_MAX = 0.01, 0.95


def probability(news2: int, reasons: list[str], age: int, n_comorbidities: int, lives_alone: bool) -> float:
    """Logistic probability, clipped to [0.01, 0.95] and rounded to 2 decimals."""
    x = (INTERCEPT + WEIGHTS["news2"] * news2 + WEIGHTS["age_per_year_over_65"] * max(0, age - 65)
         + WEIGHTS["comorbidity"] * n_comorbidities + WEIGHTS["lives_alone"] * bool(lives_alone)
         + sum(WEIGHTS[r] for r in set(reasons) if r in REASON_WEIGHTS))
    p = 1 / (1 + math.exp(-x))
    return round(min(P_MAX, max(P_MIN, p)), 2)


def _n_comorbidities(text) -> int:
    return len([c for c in str(text).split(";") if c.strip()]) if isinstance(text, str) else 0


def readmission_risk(clock: str, ward_state: WardState | None = None) -> dict[str, float]:
    """Readmission probability for each patient in the queue at `clock`."""
    state = ward_state if ward_state is not None else get_ward_state(clock)
    pts = load_patients().set_index("patient_id")
    out: dict[str, float] = {}
    for q in state.queue:
        p = pts.loc[q.patient_id]
        out[q.patient_id] = probability(q.news2, list(q.reasons), int(p["age"]),
                                        _n_comorbidities(p["comorbidities"]), bool(p["lives_alone"]))
    return out


if __name__ == "__main__":
    for _h in ("09:00", "13:00", "21:00"):
        _c = f"2026-11-21T{_h}"
        _r = readmission_risk(_c)
        _top = sorted(_r.items(), key=lambda kv: -kv[1])[:4]
        print(_c, "top:", _top, "| max of others:", max(v for k, v in _r.items() if k != "P-002"))
