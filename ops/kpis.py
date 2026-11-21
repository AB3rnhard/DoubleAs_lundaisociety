"""Command Center KPIs: alerts suppressed, patients flagged, flag lead time vs NEWS2, capacity. Pure, no Streamlit.

Owner: B (Teammate B). Built in prompt B5.

Public interface: alert_kpis, artefacts_caught, lead_times, earliest_lead, patients_flagged,
                  command_center_kpis, kpi_tiles.

Everything is computed from data with ts <= clock (clinical.queue already filters), so there is no look-ahead.
Alert counts cover all data up to the clock ("since monitoring start"), not a calendar day.
"""
from __future__ import annotations

import logging

import pandas as pd

from clinical import queue
from ops.capacity import get_capacity

logger = logging.getLogger(__name__)

FLAG_REASONS = {"TREND", "SYMPTOM_MESSAGE", "NEWS2_URGENT", "NEWS2_EMERGENCY", "PLAN_BREACH"}


def _iso(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M")


def alert_kpis(clock: str) -> dict:
    """Naive (Scale 1, no artefact filter) vs plan-aware alert readings from clinical.queue.alert_burden."""
    b = queue.alert_burden(clock)
    naive, aware = b["naive_scale1_alert_readings"], b["plan_aware_filtered_alert_readings"]
    return {"naive": naive, "plan_aware": aware, "suppressed": naive - aware,
            "reduction_pct": round(100 * (naive - aware) / naive, 1) if naive else 0.0}


def artefacts_caught(clock: str) -> int:
    """Artefact readings (signal_quality < threshold) up to the clock; source: clinical.artefact.artefact_list
    on all virtual-ward readings of clinical.queue (via get_ward_state(...).artefacts)."""
    return len(queue.get_ward_state(clock).artefacts)


def lead_times(clock: str) -> list[dict]:
    """One entry per virtual-ward patient with a trend/symptom flag up to the clock."""
    cut = pd.Timestamp(clock)
    out = []
    for item in queue.get_ward_state(clock).queue:
        r = queue.readings(item.patient_id, clock)
        flags = r[r["trend_flag"] | r["symptom_flag"]]
        if flags.empty:
            continue
        first = flags.iloc[0]
        trend = r[r["trend_flag"]]
        trig = r[r["urgent"] | r["emergency"]]
        first_ts = pd.Timestamp(first["ts"])
        trig_ts = pd.Timestamp(trig.iloc[0]["ts"]) if not trig.empty else None
        trend_ts = pd.Timestamp(trend.iloc[0]["ts"]) if not trend.empty else None
        hours = lambda a, b: (b - a).total_seconds() / 3600
        out.append({
            "patient_id": item.patient_id,
            "first_flag_at": _iso(first_ts),
            "flag_kind": "fused" if bool(first["symptom_flag"]) else "trend",
            "first_trend_at": _iso(trend_ts) if trend_ts is not None else None,
            "first_news2_trigger_at": _iso(trig_ts) if trig_ts is not None else None,
            "lead_hours": hours(first_ts, trig_ts) if trig_ts is not None else None,
            "trend_lead_hours": hours(trend_ts, trig_ts) if trig_ts is not None and trend_ts is not None else None,
            "running_hours": hours(first_ts, cut) if trig_ts is None else None,
        })
    return out


def earliest_lead(clock: str) -> dict | None:
    """Entry for the KPI tile: the largest lead_hours, else the longest running_hours, else None."""
    entries = lead_times(clock)
    done = [e for e in entries if e["lead_hours"] is not None]
    if done:
        return max(done, key=lambda e: e["lead_hours"])
    return max(entries, key=lambda e: e["running_hours"]) if entries else None


def patients_flagged(clock: str, escalated=()) -> int:
    state = queue.get_ward_state(clock, list(escalated))
    return sum(1 for q in state.queue if FLAG_REASONS & set(q.reasons))


def command_center_kpis(clock: str, escalated=()) -> dict:
    state = queue.get_ward_state(clock, list(escalated))
    cap = get_capacity(clock, state)
    return {"clock": clock, "alerts": alert_kpis(clock), "artefacts_caught": artefacts_caught(clock),
            "patients_flagged": sum(1 for q in state.queue if FLAG_REASONS & set(q.reasons)),
            "earliest_lead": earliest_lead(clock), "capacity": cap}


def _hours(h: float) -> str:
    return f"{h:g} h"


def kpi_tiles(kpis: dict) -> list[dict]:
    """[{label, value, help}] for st.metric; strings only."""
    a, cap, lead = kpis["alerts"], kpis["capacity"], kpis["earliest_lead"]
    if lead is None:
        lead_value, lead_help = "No flag yet", "No trend or symptom flag has been raised up to the clock."
    elif lead["lead_hours"] is not None:
        lead_value = f"{_hours(lead['lead_hours'])} earlier"
        lead_help = (f"{lead['patient_id']}: first {lead['flag_kind']} flag {lead['first_flag_at'][11:]}, "
                     f"NEWS2 trigger {lead['first_news2_trigger_at'][11:]}.")
    else:
        lead_value = (f"flag {lead['first_flag_at'][11:]}, NEWS2 not yet triggered "
                      f"({_hours(lead['running_hours'])})")
        lead_help = f"{lead['patient_id']}: first {lead['flag_kind']} flag; the NEWS2 trigger has not happened yet."
    k = cap["kits"]
    kits = f"{k['free']}" + (f" (+{k['in_cleaning']} at {k['cleaning_ready_at'][11:]})" if k["in_cleaning"] else "")
    v, b = cap["visits"], cap["beds"]
    return [
        {"label": "Alerts suppressed", "value": f"{a['suppressed']} ({a['naive']} -> {a['plan_aware']})",
         "help": "Since monitoring start: naive Scale 1 alerts -> plan-aware alerts (own plan scale, artefacts removed)."},
        {"label": "Patients flagged", "value": str(kpis["patients_flagged"]),
         "help": "Virtual-ward patients with a trend, symptom, NEWS2 or plan-breach flag now."},
        {"label": "Earliest flag vs NEWS2", "value": lead_value, "help": lead_help},
        {"label": "Beds", "value": f"{b['occupied']}/{b['total']}", "help": "Occupied / total physical beds."},
        {"label": "Kits free", "value": kits, "help": "Free monitoring kits; cleaning kits return when ready."},
        {"label": "Visits", "value": f"{v['booked']}/{v['capacity']}", "help": "Booked / capacity of home visits today."},
    ]


if __name__ == "__main__":
    for _h in ("09:00", "13:00", "21:00"):
        _c = f"2026-11-21T{_h}"
        _k = command_center_kpis(_c, [])
        print(_c, [(t["label"], t["value"]) for t in kpi_tiles(_k)])
        print("  lead_times:", lead_times(_c))
