"""Patient view data and figures for the UI.

Owner: A (Teammate A). Built in prompt A3.

Public interface (pure functions, no streamlit; everything uses only data with ts <= clock):
    virtual_ward_patients() -> list[str]
    patient_header(pid, clock) -> dict
    vitals_figure(pid, clock) -> plotly Figure   (HR, SpO2, RR, SBP, Temp stacked, shared x axis)
    news2_figure(pid, clock) -> plotly Figure    (NEWS2 bars of the last 24 h)
    timeline(pid, clock) -> list[dict]           (notes and messages, newest first)

get_briefing(pid, clock, live=False) (CLAUDE.md interface) lives in ai/factsheet.py (built in A4), not
here: this module must not import ai.* (import cycle, since ai.factsheet imports patient_header).
"""
from __future__ import annotations

import logging
from functools import lru_cache

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from clinical.artefact import to_iso
from clinical.queue import get_ward_state, readings
from clinical.trend import SIGN, Z_THRESHOLD, baselines, is_symptom_message
from core.loader import (
    load_messages,
    load_monitoring_plans,
    load_notes,
    load_patients,
    load_vitals,
)

logger = logging.getLogger(__name__)

ACCENT = "#0F766E"
RED = "#DC2626"
AMBER = "#D97706"
GREY = "#6B7280"
BAND = "rgba(15, 118, 110, 0.10)"

VITALS_WINDOW = pd.Timedelta(hours=48)
NEWS2_WINDOW = pd.Timedelta(hours=24)
SD_BAND = 2.5

# row order: (readings column, trend param, axis title, plan low, plan high)
ROWS = [
    ("hr", "hr", "HR (bpm)", "hr_low", "hr_high"),
    ("spo2", "spo2", "SpO2 (%)", "spo2_target_low", "spo2_target_high"),
    ("rr", "rr", "RR (/min)", None, "rr_high"),
    ("sbp", "sbp", "SBP (mmHg)", "sbp_low", None),
    ("temp_c", "temp_c", "Temp (°C)", None, "temp_high"),
]
ARTEFACT_ROWS = ("hr", "spo2")


@lru_cache(maxsize=1)
def _static() -> dict:
    """Static tables, loaded once. Callers get copies through the public functions."""
    patients = load_patients().set_index("patient_id", drop=False)
    plans = load_monitoring_plans().drop_duplicates("patient_id", keep="last").set_index("patient_id")
    return {
        "patients": patients,
        "plans": plans,
        "notes": load_notes(),
        "messages": load_messages(),
        "baselines": baselines(load_vitals()),
    }


def virtual_ward_patients() -> list[str]:
    """Virtual-ward patient ids (prefix "P-"), sorted."""
    return sorted(p for p in _static()["patients"].index if str(p).startswith("P-"))


def _clean(value):
    """NaN/NaT -> None, numpy scalars -> python."""
    if pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def patient_header(pid: str, clock: str | pd.Timestamp) -> dict:
    """Static patient facts plus the patient's queue item at `clock`. Raises KeyError for unknown pid."""
    st_ = _static()
    p = st_["patients"].loc[pid]
    plan = st_["plans"].loc[pid] if pid in st_["plans"].index else None
    cut = pd.Timestamp(clock)
    start = p["virtual_ward_start"]
    day = int((cut - start).days) + 1 if pd.notna(start) else None
    item = next((q for q in get_ward_state(cut).queue if q.patient_id == pid), None)
    comorb = _clean(p["comorbidities"])
    return {
        "patient_id": pid,
        "name": p["name"],
        "age": int(p["age"]),
        "sex": p["sex"],
        "primary_diagnosis": p["primary_diagnosis"],
        "icd10": p["icd10"],
        "comorbidities": [c.strip() for c in str(comorb).split(";") if c.strip()] if comorb else [],
        "municipality": p["municipality"],
        "lives_alone": bool(p["lives_alone"]),
        "caregiver_at_home": _clean(p["caregiver_at_home"]),
        "current_therapy": _clean(p["current_therapy"]),
        "visits_per_day": _clean(p["visits_per_day"]),
        "spo2_scale": int(plan["spo2_scale"]) if plan is not None else int(p["spo2_scale"]),
        "plan_set_by": _clean(plan["set_by"]) if plan is not None else None,
        "custom_rule": _clean(plan["custom_rule"]) if plan is not None else None,
        "day_of_stay": day,
        "reasons": list(item.reasons) if item else [],
        "priority": item.priority if item else 0,
        "news2": item.news2 if item else 0,
        "news2_max_single": item.news2_max_single if item else 0,
        "flagged_at": item.flagged_at if item else None,
    }


def _marker_traces(fig: go.Figure, r: pd.DataFrame, row: int, col: str, param: str, base: dict | None,
                   first_row: bool) -> None:
    """Add the alert / trend / artefact markers for one row. Empty traces are omitted."""
    alert = r[r["urgent"] | r["emergency"]]
    if not alert.empty:
        fig.add_trace(go.Scatter(
            x=alert["ts"], y=alert[col], mode="markers", name="NEWS2 alert", legendgroup="NEWS2 alert",
            showlegend=first_row, marker={"color": RED, "size": 10, "line": {"color": "white", "width": 1}},
            hovertemplate="%{y} at %{x|%d %b %H:%M}<br>NEWS2 alert<extra></extra>"), row=row, col=1)

    soft = r[(r["trend_flag"] | r["symptom_flag"]) & ~(r["urgent"] | r["emergency"])]
    if base is not None and not soft.empty:
        z = SIGN[param] * (soft[col] - base["median"][param]) / base["robust_sd"][param]
        soft = soft[z >= Z_THRESHOLD]
    if not soft.empty:
        fig.add_trace(go.Scatter(
            x=soft["ts"], y=soft[col], mode="markers", name="trend", legendgroup="trend",
            showlegend=first_row, marker={"color": AMBER, "size": 10, "line": {"color": "white", "width": 1}},
            hovertemplate="%{y} at %{x|%d %b %H:%M}<br>trend / symptom flag<extra></extra>"), row=row, col=1)

    art = r[r["artefact"]]
    if col in ARTEFACT_ROWS and not art.empty:
        fig.add_trace(go.Scatter(
            x=art["ts"], y=art[col], mode="markers", name="artefact", legendgroup="artefact",
            showlegend=first_row, marker={"color": GREY, "size": 9, "symbol": "x"},
            hovertemplate="%{y} at %{x|%d %b %H:%M}<br>artefact (low signal quality)<extra></extra>"),
            row=row, col=1)


def _hline(ax: str, y: float, color: str, dash: str, width: float) -> dict:
    """Horizontal line across one subplot (a layout shape: much faster than add_hline(row=...))."""
    return {"type": "line", "xref": f"x{ax} domain", "yref": f"y{ax}", "x0": 0, "x1": 1, "y0": y, "y1": y,
            "line": {"color": color, "dash": dash, "width": width}}


def vitals_figure(pid: str, clock: str | pd.Timestamp) -> go.Figure:
    """Five stacked vitals rows with plan targets, personal baseline and flags; data up to `clock` only."""
    st_ = _static()
    cut = pd.Timestamp(clock)
    r = readings(pid, cut)
    plan = st_["plans"].loc[pid] if pid in st_["plans"].index else None
    base = st_["baselines"].get(pid)
    good = r[~r["artefact"]]

    fig = make_subplots(rows=len(ROWS), cols=1, shared_xaxes=True, vertical_spacing=0.04)
    shapes: list[dict] = []
    for i, (col, param, title, low, high) in enumerate(ROWS, start=1):
        fig.add_trace(go.Scatter(
            x=good["ts"], y=good[col], mode="lines+markers", name=title.split(" ")[0],
            line={"color": ACCENT, "width": 2}, marker={"size": 4, "color": ACCENT}, showlegend=False,
            hovertemplate="%{y} at %{x|%d %b %H:%M}<extra></extra>"), row=i, col=1)

        ax = "" if i == 1 else str(i)
        if plan is not None:
            if low is not None and high is not None:
                shapes.append({"type": "rect", "xref": f"x{ax} domain", "yref": f"y{ax}", "x0": 0, "x1": 1,
                               "y0": float(plan[low]), "y1": float(plan[high]), "fillcolor": BAND,
                               "line": {"width": 0}, "layer": "below"})
            elif high is not None or low is not None:
                shapes.append(_hline(ax, float(plan[high if high is not None else low]), ACCENT, "dot", 1.5))
        if base is not None:
            for sign in (-1, 1):
                shapes.append(_hline(ax, base["median"][param] + sign * SD_BAND * base["robust_sd"][param],
                                     GREY, "dash", 1))

        _marker_traces(fig, r, i, col, param, base, first_row=(i == 1))
        fig.update_yaxes(title_text=title, row=i, col=1)

    fig.update_xaxes(range=[cut - VITALS_WINDOW, cut])
    fig.update_layout(shapes=shapes)
    fig.update_layout(template="plotly_white", height=900, margin={"l": 60, "r": 20, "t": 30, "b": 40},
                      legend={"orientation": "h", "y": 1.04, "x": 0}, hovermode="closest")
    return fig


def news2_figure(pid: str, clock: str | pd.Timestamp) -> go.Figure:
    """NEWS2 per good reading in the 24 h up to `clock`; red where urgent or emergency."""
    st_ = _static()
    cut = pd.Timestamp(clock)
    r = readings(pid, cut)
    r = r[~r["artefact"] & (r["ts"] >= cut - NEWS2_WINDOW)]
    alert = r["urgent"] | r["emergency"]

    fig = go.Figure(go.Bar(
        x=r["ts"], y=r["news2"], marker={"color": [RED if a else ACCENT for a in alert]}, name="NEWS2",
        hovertemplate="NEWS2 %{y} at %{x|%d %b %H:%M}<extra></extra>"))
    if pid in st_["plans"].index:
        thr = int(st_["plans"].loc[pid, "news2_urgent_threshold"])
        fig.add_hline(y=thr, line={"color": RED, "dash": "dash", "width": 1.5},
                      annotation_text=f"urgent >= {thr}", annotation_position="top left")
    fig.update_xaxes(range=[cut - NEWS2_WINDOW - pd.Timedelta(minutes=30), cut + pd.Timedelta(minutes=30)])
    fig.update_yaxes(title_text="NEWS2", rangemode="tozero")
    fig.update_layout(template="plotly_white", height=250, showlegend=False,
                      margin={"l": 60, "r": 20, "t": 30, "b": 40})
    return fig


def timeline(pid: str, clock: str | pd.Timestamp) -> list[dict]:
    """Notes and messages of the patient with ts <= clock, newest first."""
    st_ = _static()
    cut = pd.Timestamp(clock)
    items: list[dict] = []
    notes = st_["notes"]
    for n in notes[(notes["patient_id"] == pid) & (notes["ts"] <= cut)].itertuples(index=False):
        items.append({"kind": "note", "id": str(n.note_id), "ts": to_iso(n.ts), "who": str(n.author_role),
                      "text": str(n.text), "symptom": False})
    msgs = st_["messages"]
    for m in msgs[(msgs["patient_id"] == pid) & (msgs["ts"] <= cut)].itertuples(index=False):
        items.append({"kind": "message", "id": str(m.message_id), "ts": to_iso(m.ts), "who": str(m.sender),
                      "text": str(m.text), "symptom": is_symptom_message(m.text)})
    items.sort(key=lambda x: (x["ts"], x["id"]), reverse=True)
    return items


if __name__ == "__main__":
    import time

    _clock, _pid = "2026-11-21T13:00", "P-002"
    # warm static data, the queue cache and plotly's lazy template load so the timing is per call
    patient_header(_pid, _clock)
    vitals_figure(_pid, _clock)
    news2_figure(_pid, _clock)
    _t = time.perf_counter()
    _h = patient_header(_pid, _clock)
    _v = vitals_figure(_pid, _clock)
    _n = news2_figure(_pid, _clock)
    _tl = timeline(_pid, _clock)
    _ms = (time.perf_counter() - _t) * 1000
    print("header:", _h)
    print("vitals traces:", len(_v.data), [t.name for t in _v.data])
    print("news2 traces:", len(_n.data))
    print("timeline ids:", [x["id"] for x in _tl])
    print(f"header + vitals + news2 + timeline: {_ms:.0f} ms")
