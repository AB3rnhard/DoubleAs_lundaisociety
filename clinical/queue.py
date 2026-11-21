"""Prioritised ward queue combining NEWS2, trend, messages and artefacts.

Owner: A (Teammate A). Built in prompt A2.

Public interface: get_ward_state(clock, escalated=None) -> WardState
                  alert_burden(clock=None) -> dict   (naive vs plan-aware alert counts)
                  refresh() -> None                  (clear caches after the triage cache changed, A5)

Virtual-ward patients only (patient_id starts with "P-"). Everything is computed once on the full
data (cached, never mutated) and filtered by clock per call, so no row after the clock is used.
"""
from __future__ import annotations

import logging
from functools import lru_cache

import pandas as pd

from clinical.artefact import artefact_list, is_artefact, to_iso
from clinical.news2 import score_df
from clinical.trend import detect
from core.contracts import QueueItem, Reason, WardState
from core.loader import at_clock, load_messages, load_monitoring_plans, load_patients, load_vitals

logger = logging.getLogger(__name__)

NEWS2_ALERT = 5  # alert reading: news2 >= 5 or any single parameter scoring 3


def _alert(df: pd.DataFrame) -> pd.Series:
    return (df["news2"] >= NEWS2_ALERT) | (df["news2_max_single"] == 3)


def _add_plan_flags(good: pd.DataFrame) -> pd.DataFrame:
    """Add emergency, urgent and plan_breach to good readings that carry the plan columns."""
    good["emergency"] = good["news2"] >= good["news2_emergency_threshold"]
    good["urgent"] = ~good["emergency"] & ((good["news2"] >= good["news2_urgent_threshold"])
                                           | (good["news2_max_single"] == 3))
    good["plan_breach"] = (
        (good["spo2"] < good["spo2_target_low"])
        | ((good["spo2"] > good["spo2_target_high"]) & (good["on_oxygen"] == 1))
        | (good["hr"] < good["hr_low"]) | (good["hr"] > good["hr_high"])
        | (good["rr"] > good["rr_high"]) | (good["sbp"] < good["sbp_low"])
        | (good["temp_c"] > good["temp_high"])
    )
    return good


_PLAN_COLUMNS = ["patient_id", "spo2_target_low", "spo2_target_high", "hr_low", "hr_high", "rr_high",
                 "sbp_low", "temp_high", "news2_urgent_threshold", "news2_emergency_threshold"]


@lru_cache(maxsize=1)
def _load() -> dict:
    """Load and score the virtual-ward data once. Callers must copy before changing anything."""
    vitals = load_vitals()
    vw = vitals[vitals["patient_id"].str.startswith("P-")].sort_values(["patient_id", "ts"])
    vw = vw.reset_index(drop=True)
    plans = load_monitoring_plans().drop_duplicates("patient_id", keep="last")
    messages = load_messages()

    scored = score_df(vw, plans)
    naive = score_df(vw, plans, scale_override=1)
    scored["artefact"] = is_artefact(scored)
    scored["naive_alert"] = _alert(naive)
    scored["aware_alert"] = _alert(scored) & ~scored["artefact"]

    det = detect(vw, messages)
    det["ts"] = pd.to_datetime(det["ts"])
    good = scored[~scored["artefact"]].merge(
        det[["patient_id", "ts", "trend_flag", "symptom_flag", "symptom_message_ids"]],
        on=["patient_id", "ts"], how="left")
    good["trend_flag"] = good["trend_flag"].fillna(False).astype(bool)
    good["symptom_flag"] = good["symptom_flag"].fillna(False).astype(bool)
    good["symptom_message_ids"] = good["symptom_message_ids"].map(lambda x: x if isinstance(x, list) else [])

    good = good.merge(plans[["patient_id", "spo2_target_low", "spo2_target_high", "hr_low", "hr_high",
                             "rr_high", "sbp_low", "temp_high", "news2_urgent_threshold",
                             "news2_emergency_threshold"]], on="patient_id", how="left")
    good = _add_plan_flags(good)
    good["any_flag"] = good["trend_flag"] | good["symptom_flag"] | good["urgent"] | good["emergency"]
    good = good.sort_values(["patient_id", "ts"]).reset_index(drop=True)
    logger.info("Loaded %d virtual-ward readings (%d good) for the queue", len(scored), len(good))
    return {"all": scored, "good": good, "patients": sorted(scored["patient_id"].unique())}


@lru_cache(maxsize=1)
def _load_inpatients() -> dict:
    """Score the inpatient vitals once (no trend, no symptom flags). Callers must copy before changing."""
    patients = load_patients()
    ids = set(patients.loc[patients["location"] == "INPATIENT", "patient_id"])
    vitals = load_vitals()
    ip = vitals[vitals["patient_id"].isin(ids)].sort_values(["patient_id", "ts"]).reset_index(drop=True)
    plans = load_monitoring_plans().drop_duplicates("patient_id", keep="last")
    scored = score_df(ip, plans)
    scored["artefact"] = is_artefact(scored)
    good = scored[~scored["artefact"]].merge(plans[_PLAN_COLUMNS], on="patient_id", how="left")
    good = _add_plan_flags(good)
    good["trend_flag"] = False
    good["symptom_flag"] = False
    good = good.sort_values(["patient_id", "ts"]).reset_index(drop=True)
    logger.info("Loaded %d inpatient readings (%d good)", len(scored), len(good))
    return {"all": scored, "good": good, "patients": sorted(ids)}


def refresh() -> None:
    """Clear the in-process caches that depend on symptom classification (call after the triage cache changed).

    clinical.views._static is not cleared: it holds notes, messages and baselines, none of which use triage.
    """
    from ai.factsheet import _build  # lazy: ai.factsheet imports this module

    _load.cache_clear()
    _build.cache_clear()
    logger.info("queue and factsheet caches cleared")


def _burden(frame: pd.DataFrame) -> dict:
    """Alert counts for a frame of scored readings (needs naive_alert and aware_alert columns)."""
    naive, aware = frame[frame["naive_alert"]], frame[frame["aware_alert"]]
    return {
        "naive_scale1_alert_readings": len(naive),
        "naive_by_patient": {str(p): int(n) for p, n in naive.groupby("patient_id").size().items()},
        "plan_aware_filtered_alert_readings": len(aware),
        "plan_aware_by_patient": {str(p): int(n) for p, n in aware.groupby("patient_id").size().items()},
        "suppressed": int((frame["naive_alert"] & ~frame["aware_alert"]).sum()),
    }


def alert_burden(clock: str | pd.Timestamp | None = None) -> dict:
    """Naive (Scale 1, no artefact filter) vs plan-aware (plan scale, artefacts removed) alert readings."""
    frame = _load()["all"]
    if clock is not None:
        frame = at_clock(frame, clock)
    return _burden(frame)


READING_COLUMNS = ["patient_id", "ts", "hr", "spo2", "rr", "sbp", "temp_c", "consciousness", "on_oxygen",
                   "o2_l_min", "source", "signal_quality", "phase", "news2", "news2_max_single",
                   "artefact", "trend_flag", "symptom_flag", "urgent", "emergency", "plan_breach"]
_FLAG_COLUMNS = ["trend_flag", "symptom_flag", "urgent", "emergency", "plan_breach"]


def readings(pid: str, clock: str | pd.Timestamp) -> pd.DataFrame:
    """Every reading of `pid` with ts <= clock (any signal quality), sorted by ts, as a new frame.

    Same flags as the queue. Artefact rows carry no flags (all False). Inpatients are included (never a
    trend or symptom flag). Unknown pid -> empty frame.
    """
    data = _load()
    if pid not in data["patients"]:
        data = _load_inpatients()
    cut = pd.Timestamp(clock)
    allr = data["all"]
    allr = allr[(allr["patient_id"] == pid) & (allr["ts"] <= cut)]
    good = data["good"]
    good = good[(good["patient_id"] == pid) & (good["ts"] <= cut)]
    out = allr.merge(good[["ts", *_FLAG_COLUMNS]], on="ts", how="left").sort_values("ts")
    for col in _FLAG_COLUMNS:
        out[col] = out[col].fillna(False).astype(bool)
    return out[READING_COLUMNS].reset_index(drop=True)


def inpatient_item(pid: str, clock: str | pd.Timestamp) -> QueueItem | None:
    """Queue-style item for an inpatient from its latest good reading (None when there is none yet).

    Same reason weights as get_ward_state (no trend or symptom). The queue itself stays virtual-ward only.
    """
    data = _load_inpatients()
    cut = pd.Timestamp(clock)
    good = data["good"]
    good = good[(good["patient_id"] == pid) & (good["ts"] <= cut)]
    if good.empty:
        return None
    r = good.iloc[-1]
    reasons: list[Reason] = []
    priority = 0
    for reason, hit, weight in [("NEWS2_EMERGENCY", r["emergency"], 100), ("NEWS2_URGENT", r["urgent"], 50),
                                ("PLAN_BREACH", r["plan_breach"], 20)]:
        if hit:
            reasons.append(reason)
            priority += weight
    priority += int(r["news2"])
    first = good[good["urgent"] | good["emergency"]]
    return QueueItem(
        patient_id=pid, priority=int(priority), reasons=reasons, news2=int(r["news2"]),
        news2_max_single=int(r["news2_max_single"]),
        flagged_at=to_iso(first["ts"].iloc[0]) if not first.empty else None, evidence_ids=[])


def get_ward_state(clock: str | pd.Timestamp, escalated: list[str] | None = None) -> WardState:
    """Build the prioritised queue for all virtual-ward patients using only data with ts <= clock."""
    data = _load()
    cut = pd.Timestamp(clock)
    good = data["good"][data["good"]["ts"] <= cut]
    everything = at_clock(data["all"], cut)

    current = good.groupby("patient_id").tail(1).set_index("patient_id")
    flagged = good[good["any_flag"]].groupby("patient_id")["ts"].min()
    latest_any = everything.sort_values(["patient_id", "ts"]).groupby("patient_id").tail(1)
    latest_is_artefact = dict(zip(latest_any["patient_id"], latest_any["artefact"], strict=True))

    items: list[QueueItem] = []
    for pid in data["patients"]:
        reasons: list[Reason] = []
        news2 = max_single = 0
        evidence: list[str] = []
        priority = 0
        if pid in current.index:
            r = current.loc[pid]
            news2, max_single = int(r["news2"]), int(r["news2_max_single"])
            flags = [("NEWS2_EMERGENCY", r["emergency"], 100), ("NEWS2_URGENT", r["urgent"], 50),
                     ("TREND", r["trend_flag"], 30), ("PLAN_BREACH", r["plan_breach"], 20),
                     ("SYMPTOM_MESSAGE", r["symptom_flag"], 15)]
            for reason, hit, weight in flags:
                if hit:
                    reasons.append(reason)
                    priority += weight
            if r["symptom_flag"]:
                evidence = list(r["symptom_message_ids"])
            priority += news2
        if latest_is_artefact.get(pid, False):
            reasons.append("ARTEFACT")
        items.append(QueueItem(
            patient_id=pid, priority=int(priority), reasons=reasons, news2=news2,
            news2_max_single=max_single,
            flagged_at=to_iso(flagged[pid]) if pid in flagged.index else None,
            evidence_ids=evidence))

    items.sort(key=lambda q: (-q.priority, q.patient_id))
    return WardState(
        clock=to_iso(cut), queue=items, escalated=list(escalated or []),
        suppressed_alerts=_burden(everything)["suppressed"],
        artefacts=artefact_list(everything))


if __name__ == "__main__":
    import time

    get_ward_state("2026-11-21T09:00")  # warm the cache so the timing below is per-call
    for _hour in ("09:00", "12:00", "13:00", "17:00", "21:00"):
        _t = time.perf_counter()
        _state = get_ward_state(f"2026-11-21T{_hour}")
        _ms = (time.perf_counter() - _t) * 1000
        print(f"\n=== {_state.clock}  ({_ms:.0f} ms)  suppressed_alerts={_state.suppressed_alerts}  "
              f"artefacts={len(_state.artefacts)}")
        for _q in _state.queue[:5]:
            print(f"  {_q.patient_id}  prio={_q.priority:<3} news2={_q.news2} (max {_q.news2_max_single}) "
                  f"flagged_at={_q.flagged_at}  {', '.join(_q.reasons) or '-'}")
        for _a in _state.artefacts:
            print("  artefact:", _a)
