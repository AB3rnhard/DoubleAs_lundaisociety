"""Tests for ai/explainer.py. Owner: Teammate B. Never calls the real Claude API."""
import copy
import dataclasses
import logging
import re
from pathlib import Path

import pytest

from ai import explainer
from ai.schemas import EXPLAINER_SCHEMA
from core import loader
from core.contracts import PROFILES, T0, fake_ward_state
from ops import whatif_cpsat
from ops.capacity import get_capacity

EXP = loader.load_expected_results()
ESC_REF = EXP["whatif_plus5_with_P002_readmission_reference"]
CLOCK_14 = "2026-11-21T14:00"
ESC_ID = fake_ward_state().queue[0].patient_id  # same escalated patient as the reference scenario


def _scenario(clock, escalated):
    state = fake_ward_state(clock, list(escalated))
    plans = whatif_cpsat.run_all_profiles(5, state)
    shadow = {p: {r: whatif_cpsat.shadow_price(5, state, p, r) for r in ("kits", "beds")} for p in PROFILES}
    return plans, get_capacity(clock, state), shadow, tuple(escalated)


@pytest.fixture(scope="module")
def scenarios():
    return {(c, bool(e)): _scenario(c, e) for c in (T0, CLOCK_14) for e in ((), (ESC_ID,))}


@pytest.fixture
def sc(scenarios):
    return scenarios[(CLOCK_14, True)]


def sheet_of(s):
    plans, cap, shadow, esc = s
    return explainer.build_factsheet(plans, cap, shadow, esc)


class FakeClient:
    """call_json stand-in: returns queued outputs (an Exception instance is raised)."""

    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), []

    def __call__(self, system, user, schema, model=None, effort=None, cache_key=None):
        self.calls.append({"system": system, "user": user, "schema": schema, "model": model,
                           "effort": effort, "cache_key": cache_key})
        out = self.outputs[min(len(self.calls), len(self.outputs)) - 1]
        if isinstance(out, Exception):
            raise out
        return copy.deepcopy(out)


def install(monkeypatch, client):
    monkeypatch.setattr(explainer, "_client", lambda: client)


def run(s, **kw):
    plans, cap, shadow, esc = s
    return explainer.explain(plans, cap, shadow=shadow, escalated=esc, clock=CLOCK_14, **kw)


def valid_output(s):
    return explainer.deterministic_explanation(s[0], sheet_of(s))


# ------------------------------------------------------------------ fact sheet
def test_factsheet_ids_sequential_and_unique(sc):
    ids = [f["id"] for f in sheet_of(sc)["facts"]]
    assert ids == [f"P{i}" for i in range(1, len(ids) + 1)]


def test_factsheet_deterministic_and_key_sensitive(scenarios):
    a, b = scenarios[(CLOCK_14, True)], scenarios[(CLOCK_14, False)]
    assert sheet_of(a) == sheet_of(a)
    ka, kb = (explainer.cache_key_for(sheet_of(x)) for x in (a, b))
    assert ka == explainer.cache_key_for(sheet_of(a)) and ka != kb and ka.startswith("explainer:v1:")
    assert explainer.cache_key_for(sheet_of(a), "other-model") != ka
    assert explainer.cache_key_for(sheet_of(scenarios[(T0, True)])) != ka
    plans, cap, shadow, esc = a
    changed = {**plans, "balanced": dataclasses.replace(plans["balanced"], spare_beds=plans["balanced"].spare_beds + 1)}
    assert explainer.cache_key_for(explainer.build_factsheet(changed, cap, shadow, esc)) != ka


def test_factsheet_contains_plan_numbers(scenarios):
    for s in scenarios.values():
        fs = sheet_of(s)
        for profile, plan in s[0].items():
            text = " | ".join(fs["by_id"][i] for i in fs["plan_fact_ids"][profile])
            assert f"places {plan.placed} of {plan.needing_placement}" in text
            assert f"spare beds {plan.spare_beds}" in text
            for used, cap in plan.usage.values():
                assert f"{used}/{cap}" in text
            for pid in (*plan.transfers_home, *plan.stepdowns, *plan.direct_home, *plan.signoffs_required):
                assert pid in text


def test_factsheet_matches_expected_results(scenarios):
    fs = sheet_of(scenarios[(T0, True)])
    for profile in PROFILES:
        r = ESC_REF[profile]
        text = " | ".join(fs["by_id"][i] for i in fs["plan_fact_ids"][profile])
        assert f"places {r['placed']} of {r['patients_needing_placement']}" in text


def test_factsheet_has_no_patient_names(sc):
    names = [n for n in loader.load_patients()["name"] if n]
    blob = " ".join(f["text"] for f in sheet_of(sc)["facts"])
    assert not [n for n in names if n in blob]


def test_escalated_fact_present(scenarios):
    assert any(f"{ESC_ID} possible readmission" in f["text"] for f in sheet_of(scenarios[(T0, True)])["facts"])
    assert not any("readmission" in f["text"] for f in sheet_of(scenarios[(T0, False)])["facts"])


# ------------------------------------------------------------------ schema
def test_schema_strict_and_supported():
    unsupported = {"minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"}

    def walk(node):
        if isinstance(node, dict):
            assert not unsupported & set(node)
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(EXPLAINER_SCHEMA)


# ------------------------------------------------------------------ deterministic explanation
def test_deterministic_passes_verification_all_scenarios(scenarios):
    for s in scenarios.values():
        fs = sheet_of(s)
        det = explainer.deterministic_explanation(s[0], fs)
        ok, errors, n_checked, n_ok = explainer.verify_output(det, s[0], fs)
        assert ok and not errors and n_checked == n_ok > 0
        assert [i["profile"] for i in det["plans"]] == list(PROFILES)


# ------------------------------------------------------------------ explain(): Claude paths
def test_valid_fake_claude_output_is_verified(monkeypatch, sc):
    fake = FakeClient(valid_output(sc))
    install(monkeypatch, fake)
    res = run(sc)
    assert res["source"] == "claude" and res["verified"] and not res["withheld"]
    assert res["n_ok"] == res["n_checked"] > 0 and len(fake.calls) == 1
    call = fake.calls[0]
    assert call["schema"] is EXPLAINER_SCHEMA and call["effort"] == "low" and call["cache_key"] == res["cache_key"]
    assert "Explain the plans." in call["user"] and "P1" in call["user"]
    assert res["prompt_version"] == "explainer_v1"


def _bad_outputs(s):
    good = valid_output(s)
    outs = {}
    o = copy.deepcopy(good)
    o["plans"][0]["evidence_ids"].append("P999")
    outs["unknown id"] = o
    o = copy.deepcopy(good)
    o["plans"][1]["one_line"] = "Balanced needs 7 external transfers."
    outs["number not in facts"] = o
    o = copy.deepcopy(good)
    o["plans"] = o["plans"][:2]
    outs["missing profile"] = o
    o = copy.deepcopy(good)
    o["plans"][1]["signoffs"] = ["EXPEDITE_I-99"]
    outs["wrong signoffs"] = o
    o = copy.deepcopy(good)
    o["binding_constraint"]["resource"] = "primary_care_handover"
    outs["resource not binding"] = o
    o = copy.deepcopy(good)
    o["plans"][1]["one_line"] = "We recommend Balanced."
    outs["preference language"] = o
    return outs


@pytest.mark.parametrize("case", ["unknown id", "number not in facts", "missing profile", "wrong signoffs",
                                  "resource not binding", "preference language"])
def test_invalid_output_retries_once_then_withholds(monkeypatch, sc, case):
    fake = FakeClient(_bad_outputs(sc)[case])
    install(monkeypatch, fake)
    res = run(sc)
    assert len(fake.calls) == 2
    assert "failed verification" in fake.calls[1]["user"] and "- " in fake.calls[1]["user"]
    assert fake.calls[1]["cache_key"].endswith(":retry1")
    assert not fake.calls[0]["cache_key"].endswith(":retry1")
    assert res["source"] == "deterministic" and res["withheld"] is True and res["verified"] is True
    assert res["n_ok"] == res["n_checked"] and res["errors"]


def test_retry_can_succeed(monkeypatch, sc):
    install(monkeypatch, FakeClient(_bad_outputs(sc)["unknown id"], valid_output(sc)))
    res = run(sc)
    assert res["source"] == "claude" and res["verified"] and not res["withheld"]


def test_resource_beds_fails_when_not_binding(monkeypatch, sc):
    plans, cap, shadow, esc = sc
    slack = {k: dataclasses.replace(p, usage={**p.usage, "beds": (p.usage["beds"][0] - 1, p.usage["beds"][1])})
             for k, p in plans.items()}
    fs = explainer.build_factsheet(slack, cap, shadow, esc)
    out = explainer.deterministic_explanation(slack, fs)
    assert explainer.verify_output(out, slack, fs)[0]
    out["binding_constraint"]["resource"] = "beds"
    ok, errors, _, _ = explainer.verify_output(out, slack, fs)
    assert not ok and any("not binding" in e for e in errors)


def test_malformed_output_fails_cleanly(monkeypatch, sc):
    fake = FakeClient({"unexpected": 1})
    install(monkeypatch, fake)
    res = run(sc)
    assert res["source"] == "deterministic" and res["withheld"] and len(fake.calls) == 2


# ------------------------------------------------------------------ explain(): fallbacks
def test_client_unavailable_returns_deterministic(monkeypatch, sc):
    install(monkeypatch, None)
    res = run(sc)
    assert res["source"] == "deterministic" and res["verified"] and not res["withheld"]
    assert res["reason"] == "claude_client not available"


def test_client_import_error_is_handled(monkeypatch, sc):
    # real _client(): ai.claude_client is a stub (or real); either way explain must not raise
    res = run(sc)
    assert res["verified"] and res["source"] in ("claude", "deterministic")


def test_client_raises_returns_deterministic(monkeypatch, sc):
    fake = FakeClient(RuntimeError("DEMO_MODE cache miss"))
    install(monkeypatch, fake)
    res = run(sc)
    assert res["source"] == "deterministic" and res["verified"] and not res["withheld"]
    assert len(fake.calls) == 1


def test_live_false_makes_no_call(monkeypatch, sc):
    fake = FakeClient(valid_output(sc))
    install(monkeypatch, fake)
    assert run(sc, live=False)["source"] == "deterministic" and not fake.calls


def test_local_verifier_used_when_missing(monkeypatch, sc, caplog):
    import ai.verifier as v
    monkeypatch.delattr(v, "verify_claims", raising=False)
    caplog.set_level(logging.INFO, logger="ai.explainer")
    install(monkeypatch, FakeClient(valid_output(sc)))
    assert run(sc)["source"] == "claude"
    assert any("local _verify_claims" in r.message for r in caplog.records)


def test_local_claim_semantics():
    facts = {"P1": "Plan: cost 9,500 SEK at 15:00, risk 0.330, I-03 and EXPEDITE_I-02", "P2": "other 7"}
    ok, errors, n, n_ok = explainer._verify_claims(
        [{"text": "Costs 9500 SEK at 15:00 with risk 0.33 for I-03, P-005 and A-03", "evidence_ids": ["P1"]},
         {"text": "At 16:00", "evidence_ids": ["P1"]},
         {"text": "No ids", "evidence_ids": []},
         {"text": "Unknown", "evidence_ids": ["P9"]},
         {"text": "Seven is 7", "evidence_ids": ["P1"]}], facts)
    assert (n, n_ok, ok) == (5, 1, False) and len(errors) == 4


# ------------------------------------------------------------------ hygiene
def test_no_patient_specific_logic():
    src = (Path(explainer.__file__)).read_text(encoding="utf-8")
    assert not re.findall(r"[\"'](P|I|A)-\d", src)
