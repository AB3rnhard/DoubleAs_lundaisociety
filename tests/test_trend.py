"""Tests for trend, artefact and queue logic. Owner: Teammate A. Built in prompt A2.

Every acceptance number comes from core.loader.load_expected_results().
"""
from __future__ import annotations

import pandas as pd
import pytest
from test_triage import fake_output, write_fake_cache  # tests/ is on sys.path (pytest rootdir import mode)

from clinical.artefact import SIGNAL_QUALITY_MIN, artefact_list, is_artefact, split
from clinical.news2 import score_df
from clinical.queue import alert_burden, get_ward_state
from clinical.trend import baselines, detect, deviations, is_symptom_message
from core.contracts import T0, WardState
from core.loader import load_expected_results, load_messages, load_monitoring_plans, load_vitals

EXP = load_expected_results()
P2 = EXP["P-002"]
BURDEN = EXP["alert_burden_virtual_ward_84h"]
NEWS2_REASONS = {"NEWS2_URGENT", "NEWS2_EMERGENCY"}
HOURS = [f"2026-11-21T{h:02d}:00" for h in range(9, 22)]


@pytest.fixture(scope="module")
def vitals() -> pd.DataFrame:
    return load_vitals()


@pytest.fixture(scope="module")
def messages() -> pd.DataFrame:
    return load_messages()


@pytest.fixture(scope="module")
def det(vitals, messages) -> pd.DataFrame:
    d = detect(vitals, messages)
    return d[d["patient_id"].str.startswith("P-")]


def _item(state: WardState, pid: str):
    return next(q for q in state.queue if q.patient_id == pid)


def _hhmm(iso: str) -> str:
    return iso[11:16]


def _hours_between(a: str, b: str) -> float:
    return (pd.Timestamp(b) - pd.Timestamp(a)).total_seconds() / 3600


# ----------------------------------------------------------------- artefact
def test_artefact_list_matches_expected(vitals):
    items = artefact_list(vitals)
    assert [{k: v for k, v in i.items() if k != "action"} for i in items] == BURDEN["artefact_readings_excluded"]
    assert all(i["action"] == "re-measure" for i in items)


def test_artefact_threshold_boundary():
    df = pd.DataFrame({"signal_quality": [0.5, 0.49, 0.9]})
    assert list(is_artefact(df)) == [False, True, False]
    assert SIGNAL_QUALITY_MIN == 0.5
    good, bad = split(df)
    assert list(good["signal_quality"]) == [0.5, 0.9]
    assert list(bad["signal_quality"]) == [0.49]


def test_split_returns_new_frames(vitals):
    before = vitals.copy()
    good, bad = split(vitals)
    good["hr"] = -1
    pd.testing.assert_frame_equal(vitals, before)
    assert len(good) + len(bad) == len(vitals)


# -------------------------------------------------------------------- trend
def test_p002_baseline(vitals):
    b = baselines(vitals)["P-002"]
    assert b["median"] == P2["baseline_median"]
    assert {k: round(v, 2) for k, v in b["robust_sd"].items()} == P2["baseline_robust_sd"]


def test_p002_n_dev_per_stream_hour(det):
    p2 = det[(det["patient_id"] == "P-002") & (det["ts"] >= "2026-11-21T10:00")]
    assert {_hhmm(r.ts): int(r.n_dev) for r in p2.itertuples()} == P2["n_params_beyond_2_5_robust_z_by_hour"]


def test_p002_first_flags(det):
    p2 = det[det["patient_id"] == "P-002"]
    assert p2[p2["trend_flag"]]["ts"].min() == P2["reference_trend_flag_first"]
    fused = p2[p2["trend_flag"] | p2["symptom_flag"]]["ts"].min()
    assert fused == P2["reference_flag_with_message_fusion_first"]


def test_trend_flags_per_virtual_ward_patient(det):
    counts = {pid: int(det[(det["patient_id"] == pid)]["trend_flag"].sum()) for pid in
              EXP["reference_trend_flags_per_virtual_ward_patient"]}
    assert counts == EXP["reference_trend_flags_per_virtual_ward_patient"]


def test_lead_times(det):
    p2 = det[det["patient_id"] == "P-002"]
    trend = p2[p2["trend_flag"]]["ts"].min()
    fused = p2[p2["trend_flag"] | p2["symptom_flag"]]["ts"].min()
    assert _hours_between(trend, P2["first_news2_ge5"]) == P2["lead_time_hours_trend_vs_news2"]
    assert _hours_between(fused, P2["first_news2_ge5"]) == P2["lead_time_hours_fused_vs_news2"]


def test_is_symptom_message(messages):
    p2_texts = messages[messages["patient_id"] == "P-002"]["text"]
    assert len(p2_texts) > 0
    assert all(is_symptom_message(t) for t in p2_texts)
    assert not is_symptom_message("What time will the nurse come tomorrow?")


def test_deviations_p002_at_13(vitals, messages):
    devs = deviations("P-002", "2026-11-21T13:00", vitals, messages)
    assert len(devs) >= 2
    assert all(d["z"] >= 2.5 and d["ts"] == "2026-11-21T13:00" for d in devs)
    assert all(set(d) == {"param", "baseline_median", "value", "z", "ts"} for d in devs)


def test_deviations_empty_when_normal(vitals, messages):
    assert deviations("P-002", T0, vitals, messages) == []


def test_detect_has_no_lookahead(vitals, messages, det):
    """Cutting the data at a clock gives the same rows and flags as filtering the full result."""
    for clock in HOURS:
        cut = detect(vitals[vitals["ts"] <= pd.Timestamp(clock)], messages)
        cut = cut[cut["patient_id"].str.startswith("P-")].reset_index(drop=True)
        full = det[det["ts"] <= clock].reset_index(drop=True)
        pd.testing.assert_frame_equal(cut, full)


def test_detect_does_not_mutate_inputs(vitals, messages):
    v_before, m_before = vitals.copy(), messages.copy()
    detect(vitals, messages)
    pd.testing.assert_frame_equal(vitals, v_before)
    pd.testing.assert_frame_equal(messages, m_before)


def _synthetic(rows: list[tuple[int, float, dict]]) -> pd.DataFrame:
    """Patient X: 30 hourly stable readings (baseline 2-26 h), then `rows` = (hour, quality, overrides)."""
    base = {"hr": 80, "rr": 16, "spo2": 97, "temp_c": 37.0, "sbp": 130, "signal_quality": 0.95}
    start = pd.Timestamp("2026-11-19T00:00")
    recs = [{"patient_id": "X", "ts": start + pd.Timedelta(hours=h), **base} for h in range(26)]
    for hour, quality, over in rows:
        recs.append({"patient_id": "X", "ts": start + pd.Timedelta(hours=hour),
                     **base, "signal_quality": quality, **over})
    return pd.DataFrame(recs)


BAD2 = {"rr": 24, "temp_c": 38.5}   # two deviating parameters (rr up, temp up)
EMPTY_MSGS = pd.DataFrame(columns=["message_id", "patient_id", "ts", "text"])


def test_single_deviating_reading_does_not_flag():
    d = detect(_synthetic([(26, 0.95, BAD2), (27, 0.95, {})]), EMPTY_MSGS)
    assert d["n_dev"].tolist() == [2, 0]
    assert not d["trend_flag"].any()


def test_two_consecutive_deviating_readings_flag():
    d = detect(_synthetic([(26, 0.95, BAD2), (27, 0.95, BAD2)]), EMPTY_MSGS)
    assert d["trend_flag"].tolist() == [False, True]


def test_artefact_is_skipped_when_finding_previous_reading():
    d = detect(_synthetic([(26, 0.95, BAD2), (27, 0.2, {"spo2": 70}), (28, 0.95, BAD2)]), EMPTY_MSGS)
    assert len(d) == 2                              # the artefact has no row
    assert d["trend_flag"].tolist() == [False, True]


def test_artefact_does_not_enter_the_baseline():
    d = detect(_synthetic([(10, 0.2, {"hr": 200})]), EMPTY_MSGS)   # extra bad reading inside the window
    assert baselines(_synthetic([(10, 0.2, {"hr": 200})]))["X"]["median"]["hr"] == 80
    assert d.empty


def test_symptom_flag_needs_deviation_and_message_in_window():
    msgs = pd.DataFrame([{"message_id": "M-X", "patient_id": "X", "ts": pd.Timestamp("2026-11-20T02:00"),
                          "text": "I feel breathless"}])
    one_dev = {"rr": 24}
    vit = _synthetic([(26, 0.95, one_dev), (27, 0.95, one_dev), (29, 0.95, one_dev), (30, 0.95, {})])
    d = detect(vit, msgs).set_index("ts")
    assert d.loc["2026-11-20T02:00", "symptom_flag"]            # message at the same timestamp counts
    assert d.loc["2026-11-20T02:00", "symptom_message_ids"] == ["M-X"]
    assert d.loc["2026-11-20T03:00", "symptom_flag"]            # 1 h later
    assert not d.loc["2026-11-20T05:00", "symptom_flag"]        # 3 h later: outside the window
    assert not d.loc["2026-11-20T06:00", "symptom_flag"]        # no deviation


# -------------------------------------------------------------- alert burden
def test_alert_burden_matches_expected():
    b = alert_burden()
    assert b["naive_scale1_alert_readings"] == BURDEN["naive_scale1_alert_readings"]
    assert b["naive_by_patient"] == BURDEN["naive_by_patient"]
    assert b["plan_aware_filtered_alert_readings"] == BURDEN["plan_aware_filtered_alert_readings"]
    assert b["plan_aware_by_patient"] == BURDEN["plan_aware_by_patient"]
    assert b["suppressed"] == b["naive_scale1_alert_readings"] - b["plan_aware_filtered_alert_readings"]


def test_alert_burden_grows_with_clock():
    assert alert_burden("2026-11-21T09:00")["naive_scale1_alert_readings"] <= \
        alert_burden("2026-11-21T21:00")["naive_scale1_alert_readings"]
    assert alert_burden("2026-11-21T21:00") == alert_burden()


# --------------------------------------------------------------------- queue
def test_ward_state_shape():
    state = get_ward_state("2026-11-21T13:00")
    assert isinstance(state, WardState)
    assert state.clock == "2026-11-21T13:00"
    assert len(state.queue) == 14
    assert len({q.patient_id for q in state.queue}) == 14
    keys = [(-q.priority, q.patient_id) for q in state.queue]
    assert keys == sorted(keys)


def test_ward_state_accepts_timestamp_clock():
    assert get_ward_state(pd.Timestamp("2026-11-21T13:00")).clock == "2026-11-21T13:00"


def test_message_fusion_at_12():
    state = get_ward_state(P2["reference_flag_with_message_fusion_first"])
    p2 = _item(state, "P-002")
    assert "SYMPTOM_MESSAGE" in p2.reasons and "TREND" not in p2.reasons
    assert p2.evidence_ids and all(e.startswith("M-") for e in p2.evidence_ids)
    assert state.queue[0].patient_id == "P-002"


def test_trend_flag_at_13():
    state = get_ward_state(P2["reference_trend_flag_first"])
    assert "TREND" in _item(state, "P-002").reasons
    assert state.queue[0].patient_id == "P-002"


def test_t0_is_quiet():
    state = get_ward_state(T0)
    for q in state.queue:
        assert not ({"TREND", "SYMPTOM_MESSAGE"} | NEWS2_REASONS) & set(q.reasons), q


def test_every_hour_invariants():
    plans = load_monitoring_plans()
    scale2 = set(plans[(plans["spo2_scale"] == 2) & plans["patient_id"].str.startswith("P-")]["patient_id"])
    assert scale2
    for clock in HOURS:
        state = get_ward_state(clock)
        for q in state.queue:
            if q.patient_id != "P-002":
                assert "TREND" not in q.reasons, (clock, q)
            if q.patient_id in scale2:
                assert not NEWS2_REASONS & set(q.reasons), (clock, q)
            assert len(NEWS2_REASONS & set(q.reasons)) <= 1, (clock, q)  # never both chips
            assert q.flagged_at is None or q.flagged_at <= clock, (clock, q)
        assert all(a["ts"] <= clock for a in state.artefacts)


def test_artefact_at_12_does_not_alert_and_does_not_leak():
    art = BURDEN["artefact_readings_excluded"][0]
    state = get_ward_state(art["ts"])
    item = _item(state, art["patient_id"])
    assert "ARTEFACT" in item.reasons
    assert not NEWS2_REASONS & set(item.reasons)
    assert [a["patient_id"] for a in state.artefacts] == [art["patient_id"]]
    # news2 is the score of the previous good reading, not of the artefact reading
    vit = load_vitals()
    row = vit[(vit["patient_id"] == art["patient_id"]) & (vit["ts"] == pd.Timestamp(art["ts"]))]
    leaked = score_df(row, load_monitoring_plans()).iloc[0]
    assert (item.news2, item.news2_max_single) != (int(leaked["news2"]), int(leaked["news2_max_single"]))
    prev = vit[(vit["patient_id"] == art["patient_id"]) & (vit["ts"] < pd.Timestamp(art["ts"]))
               & ~is_artefact(vit)].sort_values("ts").tail(1)
    expected = score_df(prev, load_monitoring_plans()).iloc[0]
    assert item.news2 == int(expected["news2"])


def test_artefact_reason_clears_when_reading_is_good_again():
    art = BURDEN["artefact_readings_excluded"][0]
    later = (pd.Timestamp(art["ts"]) + pd.Timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    state = get_ward_state(later)
    assert "ARTEFACT" not in _item(state, art["patient_id"]).reasons
    assert len(state.artefacts) == 1          # the list keeps the history


def test_p002_emergency_at_21():
    state = get_ward_state("2026-11-21T21:00")
    p2 = _item(state, "P-002")
    assert "NEWS2_EMERGENCY" in p2.reasons and "NEWS2_URGENT" not in p2.reasons
    assert p2.news2 == P2["news2_by_hour_stream"]["21:00"]
    assert state.queue[0].patient_id == "P-002"


def test_p002_urgent_at_17():
    p2 = _item(get_ward_state(P2["first_news2_ge5"]), "P-002")
    assert "NEWS2_URGENT" in p2.reasons and "NEWS2_EMERGENCY" not in p2.reasons
    assert p2.news2 == 5


def test_suppressed_alerts_at_21():
    state = get_ward_state("2026-11-21T21:00")
    assert state.suppressed_alerts == BURDEN["naive_scale1_alert_readings"] - \
        BURDEN["plan_aware_filtered_alert_readings"]


def test_flagged_at_keeps_first_flag():
    p2 = _item(get_ward_state("2026-11-21T21:00"), "P-002")
    assert p2.flagged_at == P2["reference_flag_with_message_fusion_first"]


def test_escalated_is_echoed():
    assert get_ward_state(T0).escalated == []
    assert get_ward_state(T0, ["P-002"]).escalated == ["P-002"]
    given = ["P-002"]
    get_ward_state(T0, given).escalated.append("P-003")
    assert given == ["P-002"]                     # caller's list is not shared


def test_ward_state_is_fast_and_repeatable():
    import time
    get_ward_state(T0)
    t = time.perf_counter()
    a = get_ward_state("2026-11-21T17:00")
    elapsed = time.perf_counter() - t
    assert elapsed < 0.3
    assert a == get_ward_state("2026-11-21T17:00")   # cached frames are not mutated between calls


# ------------------------------------------------- A5: triage replaces the keywords
@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    """An empty AI cache folder, no network. The queue caches are cleared before and after."""
    from clinical.queue import refresh

    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("AI_CACHE_DIR", str(tmp_path / "cache"))
    refresh()
    yield tmp_path
    refresh()


def _vw(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[frame["patient_id"].str.startswith("P-")].reset_index(drop=True)


def _check_reference_numbers(d: pd.DataFrame) -> None:
    p2 = d[d["patient_id"] == "P-002"]
    assert p2[p2["trend_flag"]]["ts"].min() == P2["reference_trend_flag_first"]
    assert p2[p2["trend_flag"] | p2["symptom_flag"]]["ts"].min() == P2["reference_flag_with_message_fusion_first"]
    counts = {pid: int(d[d["patient_id"] == pid]["trend_flag"].sum())
              for pid in EXP["reference_trend_flags_per_virtual_ward_patient"]}
    assert counts == EXP["reference_trend_flags_per_virtual_ward_patient"]


def test_reference_numbers_hold_with_empty_cache_and_with_fake_triage(tmp_cache, vitals, messages):
    empty = _vw(detect(vitals, messages))
    _check_reference_numbers(empty)
    write_fake_cache()
    fake = _vw(detect(vitals, messages))
    _check_reference_numbers(fake)
    # the fake labels agree with the keywords on this data, so the symptom ids are the same
    assert empty["symptom_message_ids"].tolist() == fake["symptom_message_ids"].tolist()
    assert empty["symptom_flag"].tolist() == fake["symptom_flag"].tolist()
    assert any(ids for ids in fake["symptom_message_ids"])


def test_queue_reference_numbers_hold_with_fake_triage(tmp_cache):
    from clinical.queue import refresh

    write_fake_cache()
    refresh()
    p2 = _item(get_ward_state(P2["reference_flag_with_message_fusion_first"]), "P-002")
    assert "SYMPTOM_MESSAGE" in p2.reasons
    assert _item(get_ward_state(P2["reference_trend_flag_first"]), "P-002").flagged_at ==         P2["reference_flag_with_message_fusion_first"]


def test_triage_decides_not_the_keywords(tmp_cache, vitals, messages):
    """M-009 contains breathless / shivery keywords, but the cached triage says 'other': no symptom flag."""
    from clinical.queue import refresh

    other = {**fake_output("M-050"), "category": "other", "urgency": "today", "needs_clinician": False,
             "symptom_terms": [], "draft_reply": "Thank you for your message. A nurse will reply today."}
    write_fake_cache({"M-009": other})
    first = P2["patient_message_first_symptom"]
    d = _vw(detect(vitals, messages))
    row = d[(d["patient_id"] == "P-002") & (d["ts"] == first)]
    assert len(row) == 1
    assert not bool(row["symptom_flag"].iloc[0]) and row["symptom_message_ids"].iloc[0] == []
    refresh()
    assert "SYMPTOM_MESSAGE" not in _item(get_ward_state(first), "P-002").reasons


def test_is_symptom_message_uses_keywords_without_an_id_and_with_an_unknown_id(tmp_cache):
    assert is_symptom_message("I feel breathless")
    assert is_symptom_message("I feel breathless", "M-UNKNOWN")  # nothing cached: keywords
    assert not is_symptom_message("What time will the nurse come tomorrow?", "M-001")
