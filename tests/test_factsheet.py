"""Tests for ai.factsheet (factsheet, prompt, get_briefing) and BRIEFING_SCHEMA. Owner: Teammate A. Built in A4.

No network: ai.claude_client._create is patched. Acceptance numbers come from load_expected_results().
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pandas as pd
import pytest

from ai import claude_client as cc
from ai.factsheet import (
    build_factsheet,
    build_user_message,
    expected_status,
    get_briefing,
    load_system_prompt,
)
from ai.schemas import BRIEFING_PROMPT_VERSION, BRIEFING_SCHEMA
from clinical.views import virtual_ward_patients
from core.loader import load_expected_results

EXP = load_expected_results()
P2 = EXP["P-002"]
DAY = "2026-11-21"
CLOCKS = [f"{DAY}T{h:02d}:00" for h in (9, 12, 13, 21)]
STREAM_HOURS = list(P2["news2_by_hour_stream"])
PID, CLOCK = "P-002", f"{DAY}T13:00"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("AI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CLAUDE_RUNTIME_MODEL", "test-model")
    return tmp_path


def _hhmm(h: str) -> str:
    return f"{DAY}T{h}"


# ---------- factsheet ----------
@pytest.mark.parametrize("clock", CLOCKS)
def test_factsheet_ids_order_cap_and_no_future_data(clock):
    for pid in virtual_ward_patients():
        fs = build_factsheet(pid, clock)
        ids = [f["id"] for f in fs["facts"]]
        assert ids == [f"F{i}" for i in range(1, len(ids) + 1)]
        assert len(ids) <= 60
        cut = pd.Timestamp(clock)
        stamps = [f["ts"] for f in fs["facts"] if f["ts"]] + [x["ts"] for x in fs["notes"] + fs["messages"]]
        assert all(pd.Timestamp(s) <= cut for s in stamps)
        assert fs["clock"] == clock and fs["patient_id"] == pid


def test_factsheet_is_deterministic_and_a_copy():
    a = build_factsheet(PID, CLOCK)
    assert a == build_factsheet(PID, CLOCK)
    a["facts"].clear()
    assert build_factsheet(PID, CLOCK)["facts"]


def test_factsheet_unknown_pid_raises_key_error():
    with pytest.raises(KeyError):
        build_factsheet("X-999", CLOCK)


def test_factsheet_fact_types_in_order():
    order = ["profile", "plan", "queue", "news2", "vital", "artefact", "deviation"]
    types = [f["type"] for f in build_factsheet(PID, f"{DAY}T21:00")["facts"]]
    assert types == sorted(types, key=order.index)
    assert types[0] == "profile" and types.count("queue") == 1


@pytest.mark.parametrize("hour", STREAM_HOURS)
def test_p002_deviation_count_by_hour(hour):
    fs = build_factsheet(PID, _hhmm(hour))
    n = len([f for f in fs["facts"] if f["type"] == "deviation"])
    assert n == P2["n_params_beyond_2_5_robust_z_by_hour"][hour]


@pytest.mark.parametrize("hour", STREAM_HOURS)
def test_p002_news2_now_by_hour(hour):
    fs = build_factsheet(PID, _hhmm(hour))
    now = next(f for f in fs["facts"] if f["text"].startswith("NEWS2 now"))
    assert int(re.search(r"NEWS2 now: (\d+)", now["text"]).group(1)) == P2["news2_by_hour_stream"][hour]


def test_p002_deviation_facts_contain_baseline_median_at_21():
    fs = build_factsheet(PID, f"{DAY}T21:00")
    devs = [f for f in fs["facts"] if f["type"] == "deviation"]
    assert len(devs) == 5
    for f in devs:
        median = float(re.search(r"baseline median ([\d.]+)", f["text"]).group(1))
        assert median == pytest.approx(P2["baseline_median"][f["param"]], abs=0.05)


def test_symptom_message_present_at_clock_and_absent_before():
    t0 = P2["patient_message_first_symptom"]
    assert [m["id"] for m in build_factsheet(PID, t0)["messages"] if m["ts"] == t0]
    before = f"{pd.Timestamp(t0) - pd.Timedelta(hours=1):%Y-%m-%dT%H:%M}"
    assert not [m for m in build_factsheet(PID, before)["messages"] if m["ts"] == t0]


def test_expected_status_matches_reference_flags():
    fused = P2["reference_flag_with_message_fusion_first"]
    trend = P2["reference_trend_flag_first"]
    assert build_factsheet(PID, fused)["expected_status"] == "review_today"
    assert build_factsheet(PID, trend)["expected_status"] == "needs_review_now"


def test_expected_status_rules():
    assert expected_status([]) == "routine"
    assert expected_status(["ARTEFACT"]) == "routine"
    assert expected_status(["PLAN_BREACH"]) == "review_today"
    assert expected_status(["TREND"]) == "review_today"
    assert expected_status(["SYMPTOM_MESSAGE"]) == "review_today"
    assert expected_status(["TREND", "SYMPTOM_MESSAGE"]) == "needs_review_now"
    assert expected_status(["NEWS2_URGENT"]) == "needs_review_now"
    assert expected_status(["NEWS2_EMERGENCY"]) == "needs_review_now"


def test_artefact_fact_for_p011_at_artefact_time():
    art = EXP["alert_burden_virtual_ward_84h"]["artefact_readings_excluded"][0]
    fs = build_factsheet(art["patient_id"], art["ts"])
    facts = [f for f in fs["facts"] if f["type"] == "artefact"]
    assert len(facts) == 1
    assert str(art["spo2"]) in facts[0]["text"] and "re-measure" in facts[0]["text"]
    assert "0.22" in facts[0]["text"]


# ---------- prompt and schema ----------
def test_system_prompt_loads_without_marker():
    text = load_system_prompt()
    assert not text.startswith("<!--")
    assert "Do not compute new numbers" in text and "copy the rule-based status" in text


def test_user_message_template():
    fs = build_factsheet(PID, CLOCK)
    msg = build_user_message(fs)
    lines = msg.split("\n")
    assert lines[0].startswith("Patient summary: ") and lines[1] == f"Clock: {CLOCK}"
    assert lines[2].startswith("Queue reasons: ")
    assert lines[3] == f"Rule-based status: {fs['expected_status']}"
    assert {"FACTS:", "NOTES:", "MESSAGES:"} <= set(lines) and lines[-1] == "Write the briefing."
    assert "triggered" not in msg  # internal fact fields are not sent


UNSUPPORTED = {"minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "pattern", "format"}


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_briefing_schema_is_strict():
    objects = [n for n in _walk(BRIEFING_SCHEMA) if n.get("type") == "object"]
    assert len(objects) >= 6
    assert all(o.get("additionalProperties") is False for o in objects)
    assert not [n for n in _walk(BRIEFING_SCHEMA) if UNSUPPORTED & set(n)]
    assert BRIEFING_PROMPT_VERSION == "briefing_v1"


# ---------- get_briefing ----------
def _fact(fs, **match):
    return next(f for f in fs["facts"] if all(f.get(k) == v for k, v in match.items()))


def _valid(fs: dict) -> dict:
    dev_t, dev_r = _fact(fs, param="temp_c"), _fact(fs, param="rr")
    urgent = next(f for f in fs["facts"] if "urgent threshold" in f["text"])
    profile = _fact(fs, type="profile")
    return {
        "headline": "Early deterioration signs while NEWS2 is still low",
        "status": fs["expected_status"],
        "summary_points": [{"text": f"Temp {dev_t['value_text']} and RR {dev_r['value_text']} are above "
                                    "baseline, and the patient reports shivering",
                            "evidence_ids": [dev_t["id"], dev_r["id"], "M-009"]}],
        "changes_vs_baseline": [{"parameter": "temp_c", "baseline": dev_t["baseline_text"],
                                 "current": dev_t["value_text"], "evidence_ids": [dev_t["id"]]}],
        "plan_rules": [{"rule": "NEWS2 urgent threshold 5", "state": "not_triggered",
                        "evidence_ids": [urgent["id"]]}],
        "relevant_history": [{"text": "Previous pneumonia needed oxygen", "evidence_ids": [profile["id"]]}],
        "suggested_actions": [{"action": "Consider a clinician review today",
                               "rationale": "Symptoms reported at 12:00", "evidence_ids": ["M-009"]}],
        "missing_information": ["Latest bloods"],
    }


def _response(payload: dict):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload))],
                           stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1, output_tokens=1))


@pytest.fixture
def live(monkeypatch):
    """DEMO_MODE off; _create returns queued payloads and records the requests."""
    monkeypatch.setenv("DEMO_MODE", "0")
    state = SimpleNamespace(queue=[], calls=[])

    def fake(**kwargs):
        state.calls.append(kwargs)
        return _response(state.queue.pop(0))

    monkeypatch.setattr(cc, "_create", fake)
    return state


def test_get_briefing_live_valid_then_cache(live, tmp_path):
    fs = build_factsheet(PID, CLOCK)
    live.queue.append(_valid(fs))
    res = get_briefing(PID, CLOCK, live=True)
    assert res["source"] == "claude" and not res["retried"] and res["model"] == "test-model"
    assert res["checks"]["n_ok"] == res["checks"]["n_checked"] > 0 and res["checks"]["errors"] == []
    assert res["briefing"]["headline"] == _valid(fs)["headline"]
    assert len(list((tmp_path / "cache").glob("*.json"))) == 1
    assert res["cache_key"].startswith("briefing_v1|test-model|P-002|2026-11-21T13:00|")

    again = get_briefing(PID, CLOCK, live=False)
    assert again["source"] == "cache" and again["briefing"] == res["briefing"]
    assert len(live.calls) == 1


def test_get_briefing_request_uses_schema_and_status_line(live):
    fs = build_factsheet(PID, CLOCK)
    live.queue.append(_valid(fs))
    get_briefing(PID, CLOCK, live=True)
    kw = live.calls[0]
    assert kw["output_config"]["format"]["schema"] == BRIEFING_SCHEMA
    assert f"Rule-based status: {fs['expected_status']}" in kw["messages"][0]["content"]


def test_get_briefing_invalid_then_valid_retries(live):
    fs = build_factsheet(PID, CLOCK)
    bad = _valid(fs)
    bad["summary_points"][0]["evidence_ids"] = ["F999"]
    live.queue += [bad, _valid(fs)]
    res = get_briefing(PID, CLOCK, live=True)
    assert res["retried"] and res["source"] == "claude" and res["checks"]["errors"] == []
    assert len(live.calls) == 2
    assert "failed these checks" in live.calls[1]["messages"][0]["content"]
    assert "F999" in live.calls[1]["messages"][0]["content"]
    assert not res["cache_key"].endswith("|retry")  # the key of the first attempt is reported

    again = get_briefing(PID, CLOCK, live=False)  # first cached answer is bad, retry answer is cached
    assert again["source"] == "cache" and again["retried"] and len(live.calls) == 2


def test_get_briefing_invalid_twice_is_rule_based_and_hides_ai_text(live):
    fs = build_factsheet(PID, CLOCK)
    bad = _valid(fs)
    bad["headline"] = "ZZZ-INVALID-HEADLINE"
    bad["summary_points"][0]["evidence_ids"] = ["F999"]
    live.queue += [bad, bad]
    res = get_briefing(PID, CLOCK, live=True)
    assert res["source"] == "rule_based" and res["retried"] and res["model"] == ""
    assert "ZZZ-INVALID-HEADLINE" not in json.dumps(res["briefing"])
    assert res["briefing"]["headline"].startswith("Rule-based summary")
    assert res["checks"]["errors"] and "F999" in res["checks"]["errors"][0]
    assert "failed verification" in res["note"]

    live.queue += [_valid(fs)]  # the bad answers were dropped from the cache: a new click asks Claude again
    assert get_briefing(PID, CLOCK, live=True)["source"] == "claude"


def test_get_briefing_wrong_status_is_rejected(live):
    fs = build_factsheet(PID, CLOCK)
    wrong = _valid(fs)
    wrong["status"] = "routine"
    live.queue += [wrong, wrong]
    res = get_briefing(PID, CLOCK, live=True)
    assert res["source"] == "rule_based"
    assert any("status" in e for e in res["checks"]["errors"])


def test_get_briefing_not_live_empty_cache_is_rule_based_without_network(live):
    res = get_briefing(PID, CLOCK, live=False)
    assert res["source"] == "rule_based" and not res["retried"]
    assert res["checks"]["n_ok"] == res["checks"]["n_checked"] > 0
    assert live.calls == []


def test_get_briefing_demo_mode_live_true_is_cache_only(monkeypatch, live):
    monkeypatch.setenv("DEMO_MODE", "1")
    res = get_briefing(PID, CLOCK, live=True)
    assert res["source"] == "rule_based" and live.calls == []


def test_get_briefing_api_error_falls_back(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "0")

    def boom(**kw):
        raise cc.ClaudeError("no key")

    monkeypatch.setattr(cc, "_create", boom)
    res = get_briefing(PID, CLOCK, live=True)
    assert res["source"] == "rule_based" and "unavailable" in res["note"]


def test_get_briefing_result_shape(live):
    res = get_briefing(PID, CLOCK, live=False)
    assert set(res) == {"patient_id", "clock", "source", "briefing", "checks", "factsheet", "model",
                        "prompt_version", "cache_key", "retried", "note"}
    assert set(res["checks"]) == {"n_checked", "n_ok", "errors"}
    assert set(res["briefing"]) == set(BRIEFING_SCHEMA["required"])
