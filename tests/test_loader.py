from core.contracts import T0
from core.loader import (
    at_clock,
    load_admissions,
    load_all,
    load_decisions_template,
    load_demand_forecast,
    load_expected_results,
    load_messages,
    load_monitoring_plans,
    load_notes,
    load_patients,
    load_resources,
    load_vitals,
)


def test_row_counts():
    assert len(load_patients()) == 26
    assert len(load_vitals()) == 1078
    assert len(load_monitoring_plans()) == 26
    assert len(load_notes()) == 26
    assert len(load_messages()) == 15
    assert len(load_admissions()) == 5
    assert len(load_demand_forecast()) == 24


def test_decisions_template_is_empty_with_columns():
    df = load_decisions_template()
    assert len(df) == 0
    assert list(df.columns) == [
        "decision_id", "ts", "patient_id", "item_type", "item_id", "ai_suggestion",
        "clinician_action", "clinician_role", "reason", "evidence_ids",
    ]


def test_at_clock_t0_is_exactly_history():
    vitals = load_vitals()
    cut = at_clock(vitals, T0)
    assert set(cut["phase"]) == {"history"}
    assert len(cut) == int((vitals["phase"] == "history").sum())


def test_expected_results_keys():
    exp = load_expected_results()
    for key in ("P-002", "alert_burden_virtual_ward_84h", "whatif_plus5_reference",
                "whatif_plus5_with_P002_readmission_reference", "profile_definitions"):
        assert key in exp


def test_resources_non_empty():
    res = load_resources()
    assert isinstance(res, dict) and res


def test_load_all_keys():
    assert {"patients", "vitals", "resources", "expected_results"} <= set(load_all())
