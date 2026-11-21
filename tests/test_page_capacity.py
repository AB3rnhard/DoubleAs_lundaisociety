"""AppTest for pages/3_Capacity_WhatIf.py. Owner: Teammate B. Never calls Claude."""
import copy
import re
import time
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ai import explainer
from core import ledger, loader
from core.contracts import PROFILES, T0, fake_ward_state
from ops import eligibility, whatif_cpsat
from ops.capacity import get_capacity
from ops.whatif_view import plan_item_id, signoff_tasks

PAGE = str(Path(__file__).resolve().parent.parent / "pages" / "3_Capacity_WhatIf.py")
EXP = loader.load_expected_results()
REF = EXP["whatif_plus5_with_P002_readmission_reference"]
CLOCK_14 = "2026-11-21T14:00"


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("AI_CACHE_DIR", str(tmp_path / "ai_cache"))  # never write into the repo's ai/cache


def make(clock=T0, escalated=None) -> AppTest:
    at = AppTest.from_file(PAGE, default_timeout=60)
    at.session_state["clock"] = clock
    at.session_state["escalated"] = list(escalated or [])
    at.session_state["tasks"] = []
    return at.run()


def find(at):
    return next(b for b in at.button if b.label == "Find plans")


def placed_texts(at):
    return [m.value for m in at.markdown if m.value.startswith("### Placed")]


def test_loads_with_five_metrics():
    at = make()
    assert not at.exception
    assert len(at.metric) == 5
    assert at.title[0].value == "Capacity & What-if"


def test_find_plans_matches_reference():
    at = make(T0, ["P-002"])
    find(at).click().run()
    assert not at.exception
    texts = placed_texts(at)
    assert len(texts) == 3
    for text, prof in zip(texts, PROFILES, strict=True):
        assert text == f"### Placed {REF[prof]['placed']} of {REF[prof]['patients_needing_placement']}"
    assert any("Possible readmission" in m.value for m in at.markdown)


def test_clock_14_story():
    at = make(CLOCK_14, ["P-002"])
    find(at).click().run()
    assert not at.exception
    for text, prof in zip(placed_texts(at), PROFILES, strict=True):
        assert text == f"### Placed {REF[prof]['placed']} of {REF[prof]['patients_needing_placement']}"
    assert any(f"Spare beds: {REF['max_capacity']['spare_beds_after_plan']}" in m.value for m in at.markdown)
    assert any(i.value.startswith("One more monitoring kit") for i in at.info)


def test_adopt_balanced_once():
    at = make(T0, ["P-002"])
    find(at).click().run()
    next(b for b in at.button if b.key == "cap_adopt_balanced").click().run()
    assert not at.exception
    rows = ledger.list_decisions()
    assert len(rows) == 1 and rows[0].item_type == "capacity_plan" and rows[0].patient_id == "WARD"
    plan = whatif_cpsat.run_all_profiles(5, fake_ward_state(T0, ["P-002"]))["balanced"]
    elig = eligibility.evaluate(*eligibility.load_scored(T0), T0)
    item_id = plan_item_id(plan, T0, 5, ["P-002"])
    assert rows[0].item_id == item_id
    assert at.session_state["tasks"] == signoff_tasks(plan, item_id, T0, elig)
    assert [t["subject"] for t in at.session_state["tasks"]] == plan.signoffs_required
    # rerun: adopted badge, button disabled, no duplicates
    at.run()
    assert next(b for b in at.button if b.key == "cap_adopt_balanced").disabled
    assert any(m.value == ":green-badge[Adopted]" for m in at.markdown)
    assert len(ledger.list_decisions()) == 1
    assert len(at.session_state["tasks"]) == len(plan.signoffs_required)


def test_eligibility_board_one_card_per_row():
    at = make(CLOCK_14)
    assert not at.exception
    n = len(eligibility.evaluate(*eligibility.load_scored(CLOCK_14), CLOCK_14))
    cards = [m for m in at.markdown if re.match(r"\*\*[PIA]-\d+ ", m.value)]
    assert len(cards) == n and len({c.value for c in cards}) == n
    assert len(at.expander) >= n


def test_warm_run_under_one_second():
    at = make(T0, ["P-002"])
    find(at).click().run()
    t = time.perf_counter()
    at.run()
    assert time.perf_counter() - t < 1.0


# ---------------------------------------------------------------- B4: scenario explainer
class _Fake:
    def __init__(self, out):
        self.out, self.calls = out, 0

    def __call__(self, *a, **k):
        self.calls += 1
        return copy.deepcopy(self.out)


def _valid_explanation(clock=T0, esc=("P-002",)):
    state = fake_ward_state(clock, list(esc))
    plans = whatif_cpsat.run_all_profiles(5, state)
    shadow = {p: {r: whatif_cpsat.shadow_price(5, state, p, r) for r in ("kits", "beds")} for p in PROFILES}
    fs = explainer.build_factsheet(plans, get_capacity(clock, state), shadow, esc)
    return explainer.deterministic_explanation(plans, fs)


def _explain_flow(monkeypatch, out):
    st.cache_data.clear()
    fake = _Fake(out)
    monkeypatch.setattr(explainer, "_client", lambda: fake)
    at = make(T0, ["P-002"])
    find(at).click().run()
    assert not any(m.value.startswith("##### Scenario explanation") for m in at.markdown)  # no call before click
    assert fake.calls == 0
    next(b for b in at.button if b.key == "cap_explain").click().run()
    assert not at.exception
    return at, fake


def test_explain_plans_verified_badge(monkeypatch):
    out = _valid_explanation()
    at, fake = _explain_flow(monkeypatch, out)
    head = next(m.value for m in at.markdown if m.value.startswith("##### Scenario explanation"))
    assert re.search(r":green-badge\[\d+/\d+ claims verified\]", head) and "the bed manager decides" in head
    assert fake.calls == 1
    assert any(out["plans"][0]["one_line"] in m.value for m in at.markdown)
    at.run()  # rerun is cached: no second call
    assert fake.calls == 1


def test_explain_invalid_output_is_withheld(monkeypatch):
    bad = _valid_explanation()
    bad["plans"][1]["one_line"] = "ZZZMARKER we recommend Balanced with 77 transfers."
    at, fake = _explain_flow(monkeypatch, bad)
    head = next(m.value for m in at.markdown if m.value.startswith("##### Scenario explanation"))
    assert "AI draft withheld: failed verification" in head and "Deterministic summary" in head
    assert "claims verified" not in head
    assert not any("ZZZMARKER" in m.value for m in at.markdown)
    assert fake.calls == 2


def test_explain_without_client_shows_deterministic(monkeypatch):
    st.cache_data.clear()
    monkeypatch.setattr(explainer, "_client", lambda: None)
    at = make(T0, ["P-002"])
    find(at).click().run()
    next(b for b in at.button if b.key == "cap_explain").click().run()
    head = next(m.value for m in at.markdown if m.value.startswith("##### Scenario explanation"))
    assert head.endswith(":gray-badge[Deterministic summary]") and "withheld" not in head


def test_adopt_stores_ai_explanation(monkeypatch):
    out = _valid_explanation()
    at, _ = _explain_flow(monkeypatch, out)
    next(b for b in at.button if b.key == "cap_adopt_balanced").click().run()
    row = ledger.list_decisions()[0]
    assert row.ai_suggestion.startswith("Solver output")
    assert f" AI explanation: {out['plans'][1]['one_line']}" in row.ai_suggestion
    with ledger._connect() as con:
        assert con.execute("SELECT prompt_version FROM decisions").fetchone()[0] == "explainer_v1"


def test_adopt_without_verified_claude_keeps_b3_behaviour(monkeypatch):
    st.cache_data.clear()
    monkeypatch.setattr(explainer, "_client", lambda: None)
    at = make(T0, ["P-002"])
    find(at).click().run()
    next(b for b in at.button if b.key == "cap_explain").click().run()
    next(b for b in at.button if b.key == "cap_adopt_balanced").click().run()
    row = ledger.list_decisions()[0]
    assert "AI explanation" not in row.ai_suggestion
    with ledger._connect() as con:
        assert con.execute("SELECT prompt_version FROM decisions").fetchone()[0] is None
