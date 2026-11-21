"""Tests for NEWS2 scoring. Owner: Teammate A. Filled in prompt A1."""
from __future__ import annotations

import pandas as pd
import pytest

from clinical.news2 import COMPONENTS, OUTPUT_COLUMNS, score, score_df
from core.contracts import T0
from core.loader import (
    at_clock,
    load_expected_results,
    load_monitoring_plans,
    load_vitals,
)

# A fully normal observation set; tests override one field at a time.
NORMAL = {
    "rr": 16,
    "spo2": 98,
    "on_oxygen": False,
    "sbp": 130,
    "hr": 70,
    "consciousness": "A",
    "temp_c": 37.0,
    "scale": 1,
}


def component(name: str, **overrides) -> int:
    return score(**{**NORMAL, **overrides})[2][name]


# --------------------------------------------------------------------------- unit tests


@pytest.mark.parametrize(
    "rr, expected",
    [(8, 3), (9, 1), (11, 1), (12, 0), (20, 0), (21, 2), (24, 2), (25, 3)],
)
def test_rr(rr, expected):
    assert component("rr", rr=rr) == expected


@pytest.mark.parametrize("spo2, expected", [(91, 3), (92, 2), (93, 2), (94, 1), (95, 1), (96, 0)])
def test_spo2_scale1(spo2, expected):
    assert component("spo2", spo2=spo2, scale=1) == expected


@pytest.mark.parametrize(
    "spo2, expected",
    [(83, 3), (84, 2), (85, 2), (86, 1), (87, 1), (88, 0), (92, 0), (93, 0), (97, 0)],
)
def test_spo2_scale2_on_air(spo2, expected):
    assert component("spo2", spo2=spo2, scale=2, on_oxygen=False) == expected


@pytest.mark.parametrize(
    "spo2, expected",
    [(87, 1), (88, 0), (92, 0), (93, 1), (94, 1), (95, 2), (96, 2), (97, 3)],
)
def test_spo2_scale2_on_oxygen(spo2, expected):
    assert component("spo2", spo2=spo2, scale=2, on_oxygen=True) == expected


@pytest.mark.parametrize("scale", [1, 2])
@pytest.mark.parametrize(
    "on_oxygen, expected", [(False, 0), (0, 0), (True, 2), (1, 2)]
)
def test_oxygen_component(scale, on_oxygen, expected):
    assert component("oxygen", on_oxygen=on_oxygen, scale=scale) == expected


@pytest.mark.parametrize(
    "sbp, expected",
    [(90, 3), (91, 2), (100, 2), (101, 1), (110, 1), (111, 0), (219, 0), (220, 3)],
)
def test_sbp(sbp, expected):
    assert component("sbp", sbp=sbp) == expected


@pytest.mark.parametrize(
    "hr, expected",
    [(40, 3), (41, 1), (50, 1), (51, 0), (90, 0), (91, 1), (110, 1), (111, 2), (130, 2), (131, 3)],
)
def test_pulse(hr, expected):
    assert component("hr", hr=hr) == expected


@pytest.mark.parametrize(
    "level, expected",
    [("A", 0), ("C", 3), ("V", 3), ("P", 3), ("U", 3), ("a", 0), ("c", 3)],
)
def test_consciousness(level, expected):
    assert component("consciousness", consciousness=level) == expected


def test_consciousness_unknown_raises():
    with pytest.raises(ValueError):
        score(**{**NORMAL, "consciousness": "X"})


@pytest.mark.parametrize(
    "temp, expected",
    [
        (35.0, 3), (35.1, 1), (36.0, 1), (36.05, 0), (36.1, 0),
        (38.0, 0), (38.1, 1), (39.0, 1), (39.1, 2),
    ],
)
def test_temp(temp, expected):
    assert component("temp_c", temp_c=temp) == expected


@pytest.mark.parametrize("scale", [0, 3])
def test_invalid_scale_raises(scale):
    with pytest.raises(ValueError):
        score(**{**NORMAL, "scale": scale})


def test_all_normal_reading():
    total, max_single, parts = score(**NORMAL)
    assert (total, max_single) == (0, 0)
    assert parts == dict.fromkeys(COMPONENTS, 0)


def test_mixed_reading_total_max_and_components():
    total, max_single, parts = score(
        rr=22, spo2=93, on_oxygen=1, sbp=95, hr=115, consciousness="V", temp_c=38.5, scale=1
    )
    assert parts == {
        "rr": 2, "spo2": 2, "oxygen": 2, "sbp": 2, "hr": 2, "consciousness": 3, "temp_c": 1,
    }
    assert total == 14
    assert max_single == 3


def test_component_keys_fixed_order():
    assert list(score(**NORMAL)[2]) == [
        "rr", "spo2", "oxygen", "sbp", "hr", "consciousness", "temp_c",
    ]


# --------------------------------------------------------------------------- score_df


@pytest.fixture(scope="module")
def vitals() -> pd.DataFrame:
    return load_vitals()


@pytest.fixture(scope="module")
def plans() -> pd.DataFrame:
    return load_monitoring_plans()


@pytest.fixture(scope="module")
def scored(vitals, plans) -> pd.DataFrame:
    return score_df(vitals, plans)


def test_score_df_does_not_mutate_input(vitals, plans):
    before = vitals.copy()
    score_df(vitals, plans)
    pd.testing.assert_frame_equal(vitals, before)


def test_score_df_keeps_rows_and_index_and_adds_columns(vitals, scored):
    assert len(scored) == len(vitals)
    assert scored.index.equals(vitals.index)
    assert list(scored["patient_id"]) == list(vitals["patient_id"])
    for col in [*vitals.columns, *OUTPUT_COLUMNS]:
        assert col in scored.columns
    for col in OUTPUT_COLUMNS:
        assert pd.api.types.is_integer_dtype(scored[col])


def test_score_df_total_is_sum_of_components(scored):
    comp_cols = [f"n2_{c}" for c in COMPONENTS]
    assert (scored[comp_cols].sum(axis=1) == scored["news2"]).all()
    assert (scored[comp_cols].max(axis=1) == scored["news2_max_single"]).all()


def test_scale_override_changes_scale2_patient_only(vitals, plans, scored):
    scale2_ids = set(plans.loc[plans["spo2_scale"] == 2, "patient_id"])
    assert scale2_ids, "data should contain at least one Scale 2 patient"
    naive = score_df(vitals, plans, scale_override=1)
    assert (naive["spo2_scale"] == 1).all()
    is_s2 = scored["patient_id"].isin(scale2_ids)
    assert (scored.loc[is_s2, "spo2_scale"] == 2).all()
    # Scale 2 patients: the SpO2 component differs on at least one row; everyone else is identical.
    assert (scored.loc[is_s2, "n2_spo2"] != naive.loc[is_s2, "n2_spo2"]).any()
    pd.testing.assert_series_equal(scored.loc[~is_s2, "news2"], naive.loc[~is_s2, "news2"])


def test_scale_override_invalid_raises(vitals, plans):
    with pytest.raises(ValueError):
        score_df(vitals, plans, scale_override=3)


def test_score_df_empty_input(vitals, plans):
    out = score_df(vitals.iloc[0:0], plans)
    assert out.empty
    for col in OUTPUT_COLUMNS:
        assert col in out.columns


def test_score_df_missing_plan_defaults_to_scale1(vitals, plans):
    row = vitals.iloc[[0]].copy()
    row["patient_id"] = "NO-PLAN"
    out = score_df(row, plans)
    assert out["spo2_scale"].iloc[0] == 1


# --------------------------------------------------------------------------- acceptance

EXPECTED = load_expected_results()


def _p002_stream(scored: pd.DataFrame) -> pd.DataFrame:
    return scored[(scored["patient_id"] == "P-002") & (scored["phase"] == "stream")]


def test_p002_news2_by_hour_stream(scored):
    got = {f"{ts:%H:%M}": int(n) for ts, n in zip(_p002_stream(scored)["ts"], _p002_stream(scored)["news2"])}
    assert got == EXPECTED["P-002"]["news2_by_hour_stream"]


def test_p002_news2_at_t0(scored):
    row = scored[(scored["patient_id"] == "P-002") & (scored["ts"] == pd.Timestamp(T0))]
    assert len(row) == 1
    assert int(row["news2"].iloc[0]) == EXPECTED["P-002"]["news2_at_T0"]


def test_p002_first_news2_ge5(scored):
    p2 = scored[scored["patient_id"] == "P-002"].sort_values("ts")
    first = p2.loc[p2["news2"] >= 5, "ts"].iloc[0]
    assert f"{first:%Y-%m-%dT%H:%M}" == EXPECTED["P-002"]["first_news2_ge5"]


def test_p002_first_single_param_3(scored):
    p2 = scored[scored["patient_id"] == "P-002"].sort_values("ts")
    first = p2.loc[p2["news2_max_single"] == 3, "ts"].iloc[0]
    assert f"{first:%Y-%m-%dT%H:%M}" == EXPECTED["P-002"]["first_single_param_3"]


@pytest.mark.parametrize("pid", sorted(EXPECTED["inpatient_news2_last24h"]))
def test_inpatient_news2_last24h(scored, pid):
    expected = EXPECTED["inpatient_news2_last24h"][pid]
    t0 = pd.Timestamp(T0)
    rows = at_clock(scored[scored["patient_id"] == pid], T0)
    rows = rows[rows["ts"] > t0 - pd.Timedelta(hours=24)]
    assert len(rows) > 0
    assert int(rows["news2"].max()) == expected["max"]
    assert round(float(rows["news2"].mean()), 2) == expected["mean"]
