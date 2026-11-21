"""Shared data contracts between clinical (A), ops (B), ai and the UI.
Change only in a separate commit 'contracts: ...' after both teammates agree."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Reason = Literal["TREND", "SYMPTOM_MESSAGE", "NEWS2_URGENT", "NEWS2_EMERGENCY",
                 "PLAN_BREACH", "ARTEFACT"]
Profile = Literal["safety_first", "balanced", "max_capacity"]
Action = Literal["approve", "edit", "reject"]

T0 = "2026-11-21T09:00"
PROFILES: tuple[Profile, ...] = ("safety_first", "balanced", "max_capacity")


@dataclass
class QueueItem:
    patient_id: str
    priority: int
    reasons: list[Reason]
    news2: int
    news2_max_single: int
    flagged_at: str | None          # ISO "YYYY-MM-DDTHH:MM" of first flag, None if not flagged
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class WardState:
    clock: str                      # ISO time of the simulation clock
    queue: list[QueueItem]          # sorted by priority, highest first
    escalated: list[str]            # patient_ids a clinician marked "possible readmission"
    suppressed_alerts: int          # naive alerts avoided up to clock
    artefacts: list[dict]           # {patient_id, ts, spo2, hr, signal_quality, action}


@dataclass
class Plan:
    profile: Profile
    placed: int
    needing_placement: int
    external_transfers: int
    transfers_home: list[str]
    stepdowns: list[str]
    direct_home: list[str]
    expedite_discharges: list[str]
    overflow_used: bool
    overtime: bool
    uses_cleaning_kit: bool
    signoffs_required: list[str]
    spare_beds: int
    usage: dict[str, tuple[int, int]]   # {"beds": (used, cap), "kits": ..., "visits": ..., "slots": ...}
    objective: float
    solver: Literal["cp-sat", "greedy"]


@dataclass
class Decision:
    ts: str
    patient_id: str                 # or "WARD" for capacity plans
    item_type: Literal["briefing", "capacity_plan", "message_reply", "signoff", "artefact"]
    item_id: str
    ai_suggestion: str
    clinician_action: Action
    clinician_role: str
    reason: str
    evidence_ids: list[str] = field(default_factory=list)


def to_dict(obj) -> dict:
    """Dataclass -> plain dict (for JSON, SQLite, Streamlit)."""
    return asdict(obj)


# ---------- fakes so each teammate can work before the other's code exists ----------
def fake_ward_state(clock: str = "2026-11-21T13:00", escalated: list[str] | None = None) -> WardState:
    return WardState(
        clock=clock,
        queue=[QueueItem("P-002", 45, ["TREND", "SYMPTOM_MESSAGE"], 0, 0,
                         "2026-11-21T12:00", ["F1", "F2", "M-009"])],
        escalated=escalated if escalated is not None else [],
        suppressed_alerts=40,
        artefacts=[{"patient_id": "P-011", "ts": "2026-11-21T12:00", "spo2": 76, "hr": 118,
                    "signal_quality": 0.22, "action": "re-measure"}],
    )


def fake_plan(profile: Profile = "balanced") -> Plan:
    return Plan(profile=profile, placed=5, needing_placement=5, external_transfers=0,
                transfers_home=["I-03", "I-06"], stepdowns=["P-005"], direct_home=["A-03", "A-05"],
                expedite_discharges=["I-02"], overflow_used=False, overtime=True,
                uses_cleaning_kit=True, signoffs_required=["EXPEDITE_I-02"], spare_beds=0,
                usage={"beds": (12, 12), "kits": (4, 4), "visits": (33, 35), "slots": (17, 18)},
                objective=21.3, solver="greedy")


if __name__ == "__main__":
    print(fake_ward_state())
    print(fake_plan())
