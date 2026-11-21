"""Story mode: sidebar buttons that walk the presenter through the one demo story.

Owner: shared. Built in I1.

Steps are plain data (clock, page, query params, extra state). No step approves a briefing,
marks a readmission or adopts a plan: those stay human clicks. Only Reset clears `escalated`
and `tasks`, so the steps can be clicked in any order.

State changes run in the button's on_click callback (callbacks run before the script, so the
`clock` slider key may be written). The callback stores `story_goto = (page, query_params)`;
app.py pops it and calls st.switch_page.
"""
import logging

import streamlit as st

logger = logging.getLogger(__name__)

REMOVE = object()  # marker: delete this session-state key

CC = "pages/1_Command_Center.py"
PATIENT = "pages/2_Patient.py"
CAPACITY = "pages/3_Capacity_WhatIf.py"
LEDGER = "pages/4_Ledger.py"

STEPS: list[dict] = [
    {"key": "s1", "label": "09:00 Morning overview", "clock": "2026-11-21T09:00",
     "page": CC, "query_params": {}, "extra_state": {}},
    {"key": "s2", "label": "12:00 Artefact + message", "clock": "2026-11-21T12:00",
     "page": CC, "query_params": {}, "extra_state": {}},
    {"key": "s3", "label": "13:00 P-002 flagged", "clock": "2026-11-21T13:00",
     "page": CC, "query_params": {}, "extra_state": {}},
    {"key": "s4", "label": "Open P-002 briefing", "clock": "2026-11-21T13:00",
     "page": PATIENT, "query_params": {"pid": "P-002"}, "extra_state": {}},
    {"key": "s5", "label": "14:00 +5 admissions", "clock": "2026-11-21T14:00",
     "page": CAPACITY, "query_params": {}, "extra_state": {"cap_show": True}},
    {"key": "s6", "label": "Ledger", "clock": None,
     "page": LEDGER, "query_params": {}, "extra_state": {}},
    {"key": "reset", "label": "Reset", "clock": "2026-11-21T09:00",
     "page": CC, "query_params": {},
     "extra_state": {"escalated": [], "tasks": [], "cap_show": REMOVE, "cap_explained": REMOVE}},
]
_BY_KEY = {s["key"]: s for s in STEPS}


def updates_for(key: str) -> dict:
    """Session-state changes of one step (value REMOVE = delete the key), incl. story_goto."""
    step = _BY_KEY[key]
    out: dict = {}
    if step["clock"] is not None:
        out["clock"] = step["clock"]
    out.update(step["extra_state"])
    out["story_goto"] = (step["page"], dict(step["query_params"]))
    return out


def _apply(key: str) -> None:
    for k, v in updates_for(key).items():
        if v is REMOVE:
            st.session_state.pop(k, None)
        else:
            st.session_state[k] = v
    logger.info("story step %s", key)


def render() -> None:
    with st.sidebar.expander("Demo story", expanded=False):
        for step in STEPS:
            st.button(step["label"], key=f"story_{step['key']}", on_click=_apply,
                      args=(step["key"],), width="stretch")
        marked = bool(st.session_state.get("escalated"))
        text = f"P-002 marked for possible readmission: {'yes' if marked else 'no'}"
        if not marked:
            text += " (step 4: Approve + Mark possible readmission)"
        st.caption(text)


if __name__ == "__main__":
    for s in STEPS:
        print(s["key"], "|", s["label"], "|", s["clock"], "|", s["page"], "|", s["query_params"],
              "|", {k: ("<remove>" if v is REMOVE else v) for k, v in s["extra_state"].items()})
