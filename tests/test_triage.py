"""Tests for ai.triage (message triage, nurse inbox support). Owner: Teammate A. Built in prompt A5.

No network: ai.claude_client._create is patched. The AI cache is a folder in tmp_path.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from ai import claude_client as cc
from ai import triage
from ai.schemas import TRIAGE_PROMPT_VERSION, TRIAGE_SCHEMA
from ai.triage import (
    cached_symptom,
    check_triage,
    classify,
    inbox,
    is_symptom,
    load_system_prompt,
    rule_based_triage,
)
from clinical.queue import refresh
from clinical.trend import keyword_symptom
from core.loader import load_messages

ROOT = Path(__file__).resolve().parent.parent
DAY = "2026-11-21"
NURSE = "Thank you for telling us. A nurse will call you shortly."
LATER = "Thank you for your message. A nurse will reply today."

# Fake labels (Prompt Pack 3.3): everything not listed here is "other" with needs_clinician false.
LABELS = {
    "M-001": ("logistics", "today", False, []),
    "M-004": ("medication_question", "today", False, []),
    "M-005": ("medication_question", "today", False, []),
    "M-007": ("discharge_question", "today", False, []),
    "M-009": ("clinical_symptom", "within_1h", True, ["out of breath", "shivery"]),
    "M-010": ("device_issue", "today", False, []),
    "M-011": ("medication_question", "today", False, []),
    "M-012": ("relative_concern", "within_1h", True, ["pale"]),
    "M-013": ("logistics", "today", False, []),
    "M-014": ("relative_concern", "within_1h", True, ["confused"]),
}


def messages() -> list[dict]:
    m = load_messages()
    return [{"message_id": str(r.message_id), "patient_id": str(r.patient_id), "ts": f"{r.ts:%Y-%m-%dT%H:%M}",
             "sender": str(r.sender), "text": str(r.text)} for r in m.itertuples(index=False)]


def msg(mid: str) -> dict:
    return next(m for m in messages() if m["message_id"] == mid)


def fake_output(mid: str) -> dict:
    category, urgency, needs, terms = LABELS.get(mid, ("other", "today", False, []))
    return {"category": category, "urgency": urgency, "symptom_terms": terms, "needs_clinician": needs,
            "draft_reply": NURSE if needs else LATER}


def write_fake_cache(overrides: dict[str, dict] | None = None) -> None:
    """Cache entries for every message, written through the same private helper call_json uses.

    `overrides` maps a message id to the output to cache instead of the fake label."""
    for m in messages():
        out = (overrides or {}).get(m["message_id"]) or fake_output(m["message_id"])
        cc._cache_put(triage._cache_key(m), "fake-model", TRIAGE_PROMPT_VERSION, out)
    refresh()


def response(obj: dict) -> SimpleNamespace:
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(obj))],
                           usage=SimpleNamespace(input_tokens=1, output_tokens=1))


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("AI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CLAUDE_RUNTIME_MODEL", "test-model")
    refresh()
    yield
    refresh()


def cache_files(tmp_path) -> list[Path]:
    d = tmp_path / "cache"
    return list(d.glob("*.json")) if d.exists() else []


@pytest.fixture
def live(monkeypatch):
    """DEMO_MODE off and a recording stand-in for the network call. Set live.queue to the answers."""
    monkeypatch.setenv("DEMO_MODE", "0")
    state = SimpleNamespace(queue=[], calls=[])

    def fake_create(**kwargs):
        state.calls.append(kwargs)
        return response(state.queue.pop(0))

    monkeypatch.setattr(cc, "_create", fake_create)
    return state


# ---------- prompt and schema ----------
def test_prompt_file_and_schema():
    text = load_system_prompt()
    assert text.startswith("You triage chat messages")
    assert "<!--" not in text
    for phrase in ("copy words or short phrases exactly", "not in the message", "new or worse symptom"):
        assert phrase in text
    assert TRIAGE_PROMPT_VERSION == "triage_v1"
    assert TRIAGE_SCHEMA["additionalProperties"] is False
    assert set(TRIAGE_SCHEMA["required"]) == set(TRIAGE_SCHEMA["properties"])


def test_user_message_has_only_sender_and_text():
    m = msg("M-014")
    user = triage.build_user_message(m)
    assert user == f"Sender: {m['sender']}\nMessage: {m['text']}"
    assert "P-008" not in user and "M-014" not in user


# ---------- rule_based_triage ----------
def test_rule_based_passes_checks_for_all_messages_and_matches_keywords():
    for m in messages():
        rb = rule_based_triage(m)
        assert check_triage(rb, m) == {"ok": True, "errors": []}, m["message_id"]
        assert is_symptom(rb) == keyword_symptom(m["text"]), m["message_id"]
    assert {m["message_id"] for m in messages() if is_symptom(rule_based_triage(m))} == {"M-009", "M-012", "M-014"}


# ---------- check_triage ----------
def good(mid: str = "M-009") -> tuple[dict, dict]:
    return fake_output(mid), msg(mid)


def errors_of(out: dict, m: dict) -> list[str]:
    res = check_triage(out, m)
    assert res["ok"] is (not res["errors"])
    return res["errors"]


def test_check_triage_passes_a_good_output():
    out, m = good()
    assert errors_of(out, m) == []


def test_check_triage_term_not_in_text():
    out, m = good()
    out["symptom_terms"] = ["chest pain"]
    assert any(e.startswith("symptom_terms") for e in errors_of(out, m))


def test_check_triage_accepts_shared_stem():
    out, m = good()
    out["symptom_terms"] = ["shivering"]  # the message says "shivery"
    assert errors_of(out, m) == []
    out["symptom_terms"] = ["SHIVERY"]  # case-insensitive
    assert errors_of(out, m) == []


def test_check_triage_dose_in_draft():
    out, m = good()
    out["draft_reply"] = NURSE + " Take 500 mg now."
    assert any("dose" in e for e in errors_of(out, m))


def test_check_triage_number_not_in_message():
    out, m = good("M-008")  # the message has 81.2 and 0.6
    out["draft_reply"] = LATER + " Your weight of 81.2 is noted."
    assert errors_of(out, m) == []
    out["draft_reply"] = LATER + " Your weight of 82 is noted."
    assert any("number 82" in e for e in errors_of(out, m))


def test_check_triage_clinical_symptom_needs_clinician():
    out, m = good()
    out["needs_clinician"] = False
    assert any(e.startswith("needs_clinician") for e in errors_of(out, m))
    out["needs_clinician"] = True
    out["urgency"] = "today"
    assert errors_of(out, m) == []
    out["category"], out["urgency"], out["needs_clinician"] = "other", "now", False
    assert any(e.startswith("needs_clinician") for e in errors_of(out, m))


def test_check_triage_needs_clinician_draft_names_a_nurse():
    out, m = good()
    out["draft_reply"] = "Thank you for telling us. We will call you shortly."
    assert any(e.startswith("draft_reply") and "nurse" in e for e in errors_of(out, m))


def test_check_triage_empty_draft_and_malformed_output():
    out, m = good()
    out["draft_reply"] = "   "
    assert "draft_reply: empty" in errors_of(out, m)
    assert not check_triage("nonsense", m)["ok"]
    assert not check_triage({}, m)["ok"]
    assert not check_triage({**fake_output("M-009"), "category": "banana"}, m)["ok"]


# ---------- is_symptom ----------
def test_is_symptom_rule():
    t = fake_output("M-009")
    assert is_symptom({**t, "category": "clinical_symptom", "needs_clinician": False})
    assert is_symptom({**t, "category": "relative_concern", "needs_clinician": True})
    assert not is_symptom({**t, "category": "relative_concern", "needs_clinician": False})
    assert not is_symptom({**t, "category": "medication_question", "needs_clinician": True})
    assert not is_symptom({**t, "category": "medication_question", "needs_clinician": False,
                           "symptom_terms": ["breath"]})


# ---------- classify ----------
def test_classify_valid_answer_then_cache(live, tmp_path):
    m = msg("M-009")
    live.queue = [fake_output("M-009")]
    first = classify(m, live=True)
    assert (first["source"], first["retried"], first["symptom"]) == ("claude", False, True)
    assert first["triage"] == fake_output("M-009") and first["checks"] == {"ok": True, "errors": []}
    assert first["model"] == "test-model" and first["prompt_version"] == "triage_v1"
    assert len(cache_files(tmp_path)) == 1 and len(live.calls) == 1
    call = live.calls[0]
    assert call["messages"][0]["content"] == triage.build_user_message(m)
    assert call["max_tokens"] == 2000

    second = classify(m, live=True)
    assert second["source"] == "cache" and len(live.calls) == 1
    assert classify(m, live=False)["source"] == "cache"


def test_classify_invalid_then_valid_is_retried(live, tmp_path):
    m = msg("M-009")
    bad = {**fake_output("M-009"), "draft_reply": "A nurse will call. Take 500 mg."}
    live.queue = [bad, fake_output("M-009")]
    res = classify(m, live=True)
    assert (res["source"], res["retried"]) == ("claude", True)
    assert res["triage"] == fake_output("M-009")
    assert len(live.calls) == 2
    assert "failed these checks" in live.calls[1]["messages"][0]["content"]
    assert len(cache_files(tmp_path)) == 2  # the invalid first answer is kept: demo mode needs it to find the retry
    again = classify(m, live=False)
    assert (again["source"], again["retried"]) == ("cache", True)
    assert again["triage"] == fake_output("M-009")


def test_classify_invalid_twice_falls_back_and_drops_both_entries(live, tmp_path):
    m = msg("M-009")
    bad = {**fake_output("M-009"), "draft_reply": "A nurse will call. Take 500 mg."}
    live.queue = [bad, bad]
    res = classify(m, live=True)
    assert (res["source"], res["retried"]) == ("rule_based", True)
    assert res["triage"] == rule_based_triage(m)
    assert not cache_files(tmp_path)
    assert "Take 500" not in json.dumps(res)
    assert "dose" in res["note"]
    assert classify(m, live=False)["source"] == "rule_based"


def test_classify_without_cache_and_demo_mode_is_rule_based_without_network(monkeypatch):
    def boom(**kwargs):
        raise AssertionError("no network call allowed")

    monkeypatch.setattr(cc, "_create", boom)
    m = msg("M-009")
    for demo in ("1", "0"):
        monkeypatch.setenv("DEMO_MODE", demo)
        res = classify(m, live=False)
        assert res["source"] == "rule_based" and res["triage"] == rule_based_triage(m)
        assert res["model"] == ""
    monkeypatch.setenv("DEMO_MODE", "1")
    assert classify(m, live=True)["source"] == "rule_based"  # DEMO_MODE wins over live


def test_classify_api_error_falls_back(live, monkeypatch):
    def fail(**kwargs):
        raise cc.ClaudeError("boom")

    monkeypatch.setattr(cc, "_create", fail)
    res = classify(msg("M-009"), live=True)
    assert res["source"] == "rule_based" and "unavailable" in res["note"]


def test_cache_key_does_not_change_with_the_model(live, monkeypatch):
    m = msg("M-009")
    key = triage._cache_key(m)
    live.queue = [fake_output("M-009")]
    assert classify(m, live=True)["source"] == "claude"
    monkeypatch.setenv("CLAUDE_RUNTIME_MODEL", "another-model")
    assert triage._cache_key(m) == key
    res = classify(m, live=True)
    assert res["source"] == "cache" and len(live.calls) == 1
    assert res["model"] == "test-model"  # the model that produced it, read from the cache entry


# ---------- cached_symptom ----------
def test_cached_symptom_none_with_empty_cache():
    assert all(cached_symptom(m["message_id"], m["text"]) is None for m in messages())


def test_cached_symptom_with_fake_cache():
    write_fake_cache()
    got = {m["message_id"]: cached_symptom(m["message_id"], m["text"]) for m in messages()}
    assert {k for k, v in got.items() if v} == {"M-009", "M-012", "M-014"}
    assert all(v is False for k, v in got.items() if k not in {"M-009", "M-012", "M-014"})


def test_cached_symptom_none_for_a_cached_output_that_fails_its_checks():
    m = msg("M-009")
    bad = {**fake_output("M-009"), "symptom_terms": ["chest pain"]}
    cc._cache_put(triage._cache_key(m), "fake-model", TRIAGE_PROMPT_VERSION, bad)
    assert cached_symptom(m["message_id"], m["text"]) is None


def test_cached_symptom_reads_the_retry_entry():
    m = msg("M-009")
    cc._cache_put(triage._cache_key(m) + "|retry", "fake-model", TRIAGE_PROMPT_VERSION, fake_output("M-009"))
    assert cached_symptom(m["message_id"], m["text"]) is True


def test_cached_symptom_never_creates_a_client(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "0")
    monkeypatch.setattr(cc, "_create", lambda **k: pytest.fail("network call"))
    assert cached_symptom("M-009", msg("M-009")["text"]) is None


# ---------- inbox ----------
@pytest.mark.parametrize("hour", ["09:00", "12:00", "21:00"])
def test_inbox_order_and_count(hour):
    clock = f"{DAY}T{hour}"
    items = inbox(clock)
    expected = load_messages()
    expected = expected[expected["ts"] <= pd.Timestamp(clock)]
    assert len(items) == len(expected)
    stamps = [i["ts"] for i in items]
    assert stamps == sorted(stamps, reverse=True)
    assert all(pd.Timestamp(s) <= pd.Timestamp(clock) for s in stamps)
    assert all(set(i) == {"message_id", "patient_id", "ts", "sender", "text", "triage"} for i in items)
    assert all(i["triage"]["source"] == "rule_based" for i in items)  # empty cache, no network


def test_inbox_with_fake_cache_uses_the_cache():
    write_fake_cache()
    items = inbox(f"{DAY}T21:00")
    assert {i["triage"]["source"] for i in items} == {"cache"}
    by_id = {i["message_id"]: i["triage"] for i in items}
    assert by_id["M-014"]["triage"]["category"] == "relative_concern" and by_id["M-014"]["symptom"]
    assert by_id["M-010"]["triage"]["category"] == "device_issue"


# ---------- imports ----------
@pytest.mark.parametrize("module", ["clinical.trend", "ai.triage", "ai.verifier", "ai.factsheet",
                                    "clinical.queue", "clinical.decisions", "clinical.views"])
def test_module_imports_in_a_fresh_process(module):
    proc = subprocess.run([sys.executable, "-c", f"import {module}"], check=False, cwd=ROOT,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
