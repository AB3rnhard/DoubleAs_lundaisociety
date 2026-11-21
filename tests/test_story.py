"""Story mode tests (I1). No Claude call; ledger and AI cache go to tmp_path."""
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core import story

ROOT = Path(__file__).resolve().parent.parent
HOUR = re.compile(r"^2026-11-21T(\d{2}):00$")


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("AI_CACHE_DIR", str(tmp_path / "ai_cache"))
    monkeypatch.setenv("DEMO_MODE", "1")


def test_steps_are_valid_data():
    for s in story.STEPS:
        if s["clock"] is not None:
            m = HOUR.match(s["clock"])
            assert m and 9 <= int(m.group(1)) <= 21
        assert (ROOT / s["page"]).is_file()
    assert story.updates_for("s4")["story_goto"] == (story.PATIENT, {"pid": "P-002"})
    assert "clock" not in story.updates_for("s6")


@pytest.mark.parametrize("step", story.STEPS, ids=lambda s: s["key"])
def test_every_button_sets_its_clock(step):
    # fresh AppTest per button: after switch_page AppTest renders no tree (known limit)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    assert not at.exception
    next(b for b in at.button if b.key == f"story_{step['key']}").click().run()
    assert not at.exception
    if step["clock"] is not None:
        assert at.session_state["clock"] == step["clock"]
    if step["key"] == "reset":
        assert at.session_state["escalated"] == [] and at.session_state["tasks"] == []
