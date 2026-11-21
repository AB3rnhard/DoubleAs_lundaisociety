"""Hospital-at-home eligibility for inpatients (home transfer) and virtual-ward step-down candidates.

Owner: B (Teammate B). Built in prompt B1.

Public interface: evaluate(...), news2_last24h(...), transfer_risk(...), STATUS_GROUP.
"""
from __future__ import annotations

import logging
import re
import unicodedata

import pandas as pd

from core import loader
from core.contracts import T0

logger = logging.getLogger(__name__)

STATUS_GROUP = {"suitable": "Suitable", "needs_assessment": "Needs assessment",
                "not_suitable": "Not eligible", "not_eligible_geography": "Not eligible",
                "discharge_planned": "Not eligible"}

MIN_QUALITY = 0.5
_NOT_SUITABLE_RX = re.compile(r"NSTEMI|telemetry|\bTIA\b|imaging|sepsis", re.IGNORECASE)
_IV_RX = re.compile(r"\bIV\b")  # case-sensitive on purpose
_STEPDOWN_RX = re.compile(r"step[- ]?down|hand[- ]?over|primary[- ]care", re.IGNORECASE)
_FALL_RX = re.compile(r"\bfall", re.IGNORECASE)
COLUMNS = ["patient_id", "pathway", "status", "reasons", "evidence_ids", "requires_signoff"]


def _norm(s) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().strip().lower()


def _empty(v) -> bool:
    return v is None or (not isinstance(v, str) and pd.isna(v)) or str(v).strip() == ""


def _window(vitals_scored: pd.DataFrame, clock) -> pd.DataFrame:
    """Good-quality readings in (clock-24h, clock]."""
    c = pd.Timestamp(clock)
    v = vitals_scored
    m = (v["ts"] > c - pd.Timedelta(hours=24)) & (v["ts"] <= c) & (v["signal_quality"] >= MIN_QUALITY)
    return v[m]


def news2_last24h(vitals_scored: pd.DataFrame, clock=T0) -> pd.DataFrame:
    """[patient_id, max, mean] of NEWS2 over good readings in (clock-24h, clock]."""
    w = _window(vitals_scored, clock)
    return w.groupby("patient_id")["news2"].agg(["max", "mean"]).reset_index()


def transfer_risk(vitals_scored: pd.DataFrame, clock=T0, patients: pd.DataFrame | None = None) -> dict[str, float]:
    """Inpatients: min(1, mean NEWS2 last 24 h / 5), 2 dp (from the unrounded mean)."""
    if patients is None:
        patients = loader.load_patients()
    inp = patients.loc[patients["location"] == "INPATIENT", "patient_id"]
    stats = news2_last24h(vitals_scored, clock).set_index("patient_id")["mean"]
    return {pid: round(min(1.0, float(stats.get(pid, 0.0)) / 5), 2) for pid in inp}


def _stepdown_candidates(patients: pd.DataFrame, notes: pd.DataFrame) -> dict[str, list[str]]:
    """patient_id -> supporting physician note ids, for virtual-ward patients without IV therapy."""
    out: dict[str, list[str]] = {}
    phys = notes[notes["author_role"] == "physician"]
    for p in patients[patients["location"] == "VIRTUAL_WARD"].itertuples():
        if _IV_RX.search(str(p.current_therapy)):
            continue
        n = phys[(phys["patient_id"] == p.patient_id) & phys["text"].str.contains(_STEPDOWN_RX)]
        if len(n):
            out[p.patient_id] = list(n["note_id"])
    return out


def evaluate(patients, vitals_scored, plans, notes, clock=T0, resources=None) -> pd.DataFrame:
    """Eligibility per inpatient (home_transfer) and step-down candidate (stepdown). First rule wins."""
    resources = resources or loader.load_resources()
    catchment = {_norm(m) for m in resources["catchment_municipalities"]}
    c = pd.Timestamp(clock)
    patients = patients.copy()
    patients["planned_discharge_at"] = pd.to_datetime(patients["planned_discharge_at"], errors="coerce")
    notes = notes[notes["ts"] <= c]
    vitals_scored = vitals_scored[vitals_scored["ts"] <= c]
    win = _window(vitals_scored, clock)
    stats = win.groupby("patient_id")["news2"].agg(["max", "mean"])
    scale = plans.set_index("patient_id")["spo2_scale"].to_dict()
    stepdown = _stepdown_candidates(patients, notes)

    rows = []
    for p in patients.itertuples():
        if p.location == "INPATIENT":
            pathway = "home_transfer"
        elif p.patient_id in stepdown:
            pathway = "stepdown"
        else:
            continue
        pid = p.patient_id
        pnotes = notes[notes["patient_id"] == pid]
        ev: list[str] = list(stepdown.get(pid, []))
        why: list[str] = []
        pw = win[win["patient_id"] == pid]
        nmax = int(stats["max"][pid]) if pid in stats.index else 0
        non_a = pw[pw["consciousness"].astype(str).str.upper() != "A"]
        kw = _NOT_SUITABLE_RX.search(f"{p.primary_diagnosis} {p.current_therapy}")

        if pathway == "home_transfer" and _norm(p.municipality) not in catchment:
            status = "not_eligible_geography"
            why.append(f"Home municipality {p.municipality} is outside the catchment")
            ev.append(f"municipality={p.municipality}")
        elif pathway == "home_transfer" and not _empty(p.planned_discharge_at):
            at = p.planned_discharge_at.strftime("%Y-%m-%dT%H:%M")
            status = "discharge_planned"
            why.append(f"Discharge already planned at {at}")
            ev.append(f"planned_discharge_at={at}")
            ev.extend(pnotes[pnotes["text"].str.contains("discharge", case=False)]["note_id"])
        elif nmax >= 5 or len(non_a) or kw:
            status = "not_suitable"
            if nmax >= 5:
                why.append(f"NEWS2 max {nmax} in last 24 h")
                ev.append(f"news2_max_24h={nmax}")
            if len(non_a):
                cs = "/".join(sorted(set(non_a["consciousness"])))
                why.append(f"Consciousness not alert ({cs})")
                ev.append(f"consciousness={cs}")
            if kw:
                why.append(f"Diagnosis/therapy needs hospital care ({kw.group(0)})")
                ev.append(f"diagnosis~{kw.group(0).lower()}")
        else:
            facts: list[str] = []
            if str(p.consent_hospital_at_home).lower() == "unknown":
                why.append("Consent for home care not yet asked")
                facts.append("consent=unknown")
            if int(scale.get(pid, p.spo2_scale)) == 2:
                why.append("SpO2 scale 2 (hypercapnic target): physician must confirm")
                facts.append("spo2_scale=2")
            fall = pnotes[pnotes["text"].str.contains(_FALL_RX)]
            if bool(p.lives_alone) and (len(fall) or _FALL_RX.search(str(p.comorbidities))):
                why.append("Lives alone with fall history")
                facts += ["lives_alone=True", "fall_history=True"]
                ev.extend(fall["note_id"])
            mid = pw[pw["news2"].isin([3, 4])]
            if len(mid):
                why.append(f"NEWS2 {int(mid['news2'].max())} recorded in last 24 h")
                facts.append(f"news2_max_24h={nmax}")
            if why:
                status = "needs_assessment"
                ev.extend(facts)
            else:
                status = "suitable"
                why.append("All eligibility checks passed")
                ev += [f"news2_max_24h={nmax}", "consciousness=A",
                       f"consent={str(p.consent_hospital_at_home).lower()}", f"municipality={p.municipality}"]
                ev.extend(pnotes[(pnotes["author_role"] == "physician")
                                 & pnotes["text"].str.contains("suitable for sjukhus hemma", case=False)]["note_id"])
        rows.append({"patient_id": pid, "pathway": pathway, "status": status, "reasons": why,
                     "evidence_ids": list(dict.fromkeys(ev)), "requires_signoff": status == "needs_assessment"})
    return pd.DataFrame(rows, columns=COLUMNS)


def load_scored(clock=T0):
    """Convenience: (patients, scored vitals <= clock, plans, notes <= clock) via core.loader."""
    from ops._news2 import resolve_score_df
    plans = loader.load_monitoring_plans()
    vitals = resolve_score_df()(loader.at_clock(loader.load_vitals(), clock), plans)
    return loader.load_patients(), vitals, plans, loader.at_clock(loader.load_notes(), clock)


if __name__ == "__main__":
    pts, vit, pl, nt = load_scored(T0)
    df = evaluate(pts, vit, pl, nt, T0)
    with pd.option_context("display.max_colwidth", 90, "display.width", 250):
        print(df.to_string())
    print(transfer_risk(vit, T0, pts))
