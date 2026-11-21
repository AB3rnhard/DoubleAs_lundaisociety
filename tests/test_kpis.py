"""Tests for ops.kpis. Owner: Teammate B. Expected numbers come from data/expected_results.json."""
import re
from pathlib import Path

import pytest

from core import loader
from ops import kpis

EXP = loader.load_expected_results()
BURDEN = EXP["alert_burden_virtual_ward_84h"]
REF = EXP["P-002"]
C = {h: f"2026-11-21T{h}" for h in ("09:00", "13:00", "21:00")}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("DECISIONS_PENDING_PATH", str(tmp_path / "pending.jsonl"))
    monkeypatch.setenv("AI_CACHE_DIR", str(tmp_path / "ai_cache"))
    monkeypatch.setenv("DEMO_MODE", "1")


def test_alert_kpis_at_21():
    a = kpis.alert_kpis(C["21:00"])
    assert a["naive"] == BURDEN["naive_scale1_alert_readings"]
    assert a["plan_aware"] == BURDEN["plan_aware_filtered_alert_readings"]
    assert a["suppressed"] == a["naive"] - a["plan_aware"]
    assert a["reduction_pct"] == round(100 * a["suppressed"] / a["naive"], 1)
    assert kpis.artefacts_caught(C["21:00"]) >= len(BURDEN["artefact_readings_excluded"])


def test_lead_times_at_21():
    entries = kpis.lead_times(C["21:00"])
    assert len(entries) == 1
    e = entries[0]
    assert e["lead_hours"] == REF["lead_time_hours_fused_vs_news2"]
    assert e["trend_lead_hours"] == REF["lead_time_hours_trend_vs_news2"]
    assert e["first_flag_at"] == REF["reference_flag_with_message_fusion_first"]
    assert e["first_trend_at"] == REF["reference_trend_flag_first"]
    assert e["first_news2_trigger_at"] == REF["first_news2_ge5"]
    assert e["flag_kind"] == "fused" and e["running_hours"] is None
    assert kpis.earliest_lead(C["21:00"]) == e


def test_no_look_ahead_at_13():
    e = kpis.lead_times(C["13:00"])[0]
    assert e["lead_hours"] is None and e["first_news2_trigger_at"] is None
    assert e["running_hours"] > 0
    assert kpis.patients_flagged(C["13:00"], []) >= 1


def test_nothing_at_09():
    assert kpis.lead_times(C["09:00"]) == []
    assert kpis.earliest_lead(C["09:00"]) is None
    assert kpis.patients_flagged(C["09:00"], []) == 0


@pytest.mark.parametrize("h", ["09:00", "13:00", "21:00"])
def test_kpi_tiles_derived_from_dicts(h):
    k = kpis.command_center_kpis(C[h], [])
    tiles = {t["label"]: t for t in kpis.kpi_tiles(k)}
    assert list(tiles) == ["Alerts suppressed", "Patients flagged", "Earliest flag vs NEWS2", "Beds", "Kits free",
                           "Visits"]
    a, cap = k["alerts"], k["capacity"]
    assert tiles["Alerts suppressed"]["value"] == f"{a['suppressed']} ({a['naive']} -> {a['plan_aware']})"
    assert tiles["Patients flagged"]["value"] == str(k["patients_flagged"])
    assert tiles["Beds"]["value"] == f"{cap['beds']['occupied']}/{cap['beds']['total']}"
    assert tiles["Visits"]["value"] == f"{cap['visits']['booked']}/{cap['visits']['capacity']}"
    assert tiles["Kits free"]["value"].startswith(str(cap["kits"]["free"]))
    lead = tiles["Earliest flag vs NEWS2"]["value"]
    if h == "09:00":
        assert lead == "No flag yet"
    elif h == "13:00":
        assert "not yet triggered" in lead and "12:00" in lead
    else:
        assert lead == f"{REF['lead_time_hours_fused_vs_news2']:g} h earlier"
    if h == "09:00":
        assert "+" in tiles["Kits free"]["value"]  # cleaning kit still to come


def test_no_patient_specific_logic():
    src = (Path(__file__).resolve().parent.parent / "ops" / "kpis.py").read_text(encoding="utf-8")
    body = src.split('if __name__ == "__main__":')[0]
    assert not re.search(r"[\"'](P|I|A)-\d", body)
