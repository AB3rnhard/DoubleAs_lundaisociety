"""Refinement D1: inpatient profiles, readmission risk, incoming patients (numbers from the data files)."""
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

from clinical.queue import get_ward_state, readings
from clinical.readmission import P_MAX, P_MIN, readmission_risk
from core.loader import load_admissions, load_demand_forecast, load_expected_results, load_patients
from ops.kpis import incoming_24h

ROOT = Path(__file__).resolve().parent.parent
DAY = "2026-11-21"
EXP = load_expected_results()
_p = load_patients()
INPATIENTS = sorted(_p.loc[_p["location"] == "INPATIENT", "patient_id"])


def test_inpatient_readings_and_news2_max():
    # the inpatient with the highest expected 24 h maximum: a stronger check than an arbitrary one
    pid = max(INPATIENTS, key=lambda p: EXP["inpatient_news2_last24h"][p]["max"])
    t0 = pd.Timestamp(f"{DAY}T09:00")
    r = readings(pid, t0)
    assert not r.empty
    assert not r[["trend_flag", "symptom_flag"]].any().any()
    window = r[~r["artefact"] & (r["ts"] > t0 - pd.Timedelta(hours=24)) & (r["ts"] <= t0)]
    assert int(window["news2"].max()) == EXP["inpatient_news2_last24h"][pid]["max"]


def test_readmission_risk_range_and_p002_trend():
    hours = ["09:00", "13:00", "17:00", "21:00"]
    risks = {h: readmission_risk(f"{DAY}T{h}") for h in hours}
    for r in risks.values():
        assert all(P_MIN <= v <= P_MAX for v in r.values())
    p2 = [risks[h]["P-002"] for h in hours]
    assert p2 == sorted(p2)
    first = EXP["P-002"]["reference_trend_flag_first"][11:16]
    for h in hours:
        if h >= first:
            r = risks[h]
            assert r["P-002"] == max(r.values())


def test_incoming_24h():
    c = f"{DAY}T09:00"
    adm = load_admissions()
    out = incoming_24h(c)
    eta = pd.to_datetime(adm["eta"])
    known = adm[(eta > pd.Timestamp(c)) & (eta <= pd.Timestamp(c) + pd.Timedelta(hours=24))]
    assert out["known"] == len(known)
    assert out["known_bed"] == int(known["needs_physical_bed"].sum())
    assert abs(out["forecast"] - load_demand_forecast()["expected_admissions"].sum()) < 0.05
    assert out["total"] == round(out["known"] + out["forecast"], 2)
    assert incoming_24h(f"{DAY}T21:00")["known"] == 0


def test_patient_page_inpatient_deep_link():
    at = AppTest.from_file(str(ROOT / "pages/2_Patient.py"), default_timeout=30)
    at.session_state["clock"] = f"{DAY}T13:00"
    at.session_state["escalated"] = []
    at.session_state["tasks"] = []
    at.query_params["pid"] = INPATIENTS[0]
    at.run()
    assert not at.exception
    assert not [c for c in at.checkbox if "briefing_readmit" in str(c.key)]


def test_queue_stays_virtual_ward_only():
    assert all(q.patient_id.startswith("P-") for q in get_ward_state(f"{DAY}T13:00").queue)
