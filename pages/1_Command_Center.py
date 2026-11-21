import os

import pandas as pd
import streamlit as st

from ai.triage import classify, inbox
from clinical.decisions import message_reply_decision, record_decision, replied_message_ids
from clinical.queue import get_ward_state, refresh
from core.contracts import WardState
from core.loader import load_patients
from ops.kpis import command_center_kpis, kpi_tiles


@st.cache_data
def _ward_state(clock: str, escalated: tuple[str, ...]) -> WardState:
    return get_ward_state(clock, list(escalated))


@st.cache_data
def _kpis(clock: str, escalated: tuple[str, ...]) -> list[dict]:
    return kpi_tiles(command_center_kpis(clock, list(escalated)))


state = _ward_state(st.session_state["clock"], tuple(st.session_state["escalated"]))

st.title("Command Center")
st.write("Clock:", state.clock)
_tiles = _kpis(st.session_state["clock"], tuple(st.session_state["escalated"]))
for _col, _t in zip(st.columns(len(_tiles)), _tiles, strict=True):
    _col.metric(_t["label"], _t["value"], help=_t["help"])
st.metric("Alerts suppressed", state.suppressed_alerts)

st.dataframe(
    pd.DataFrame([
        {"patient_id": q.patient_id, "priority": q.priority, "reasons": ", ".join(q.reasons),
         "news2": q.news2, "news2_max_single": q.news2_max_single, "flagged_at": q.flagged_at}
        for q in state.queue
    ]),
    hide_index=True,
)

st.subheader("Artefacts")
if state.artefacts:
    st.dataframe(pd.DataFrame(state.artefacts), hide_index=True)
else:
    st.caption("No artefacts")


# ---------- Inbox (A5): triaged patient messages and nurse replies ----------
OLD_AFTER = pd.Timedelta(hours=12)
URGENCY_CHIP = {"now": "red", "within_1h": "orange", "today": "gray", "none": "gray"}


@st.cache_data
def _inbox(clock: str) -> list[dict]:
    return inbox(clock, live=False)


@st.cache_data
def _names() -> dict[str, str]:
    p = load_patients()
    return dict(zip(p["patient_id"], p["name"], strict=True))


def _plain(text: str) -> str:
    """Escape characters that Streamlit markdown would treat as LaTeX."""
    return text.replace("$", "\\$")


def _badge(colour: str, text: str) -> str:
    return f":{colour}-badge[{_plain(text).replace('[', '(').replace(']', ')')}]"


def _message_card(item: dict, clock: str, replied: set[str], names: dict[str, str]) -> None:
    mid, t = item["message_id"], item["triage"]
    tr = t["triage"]
    with st.container(border=True):
        st.markdown(f":gray-badge[{mid}] **{item['ts'].replace('T', ' ')}** · {item['patient_id']} "
                    f"{names.get(item['patient_id'], '')} · {_plain(item['sender'])}")
        st.markdown(_plain(item["text"]))
        chips = [_badge("green", tr["category"].replace("_", " ")),
                 _badge(URGENCY_CHIP[tr["urgency"]], tr["urgency"])]
        if tr["needs_clinician"]:
            chips.append(_badge("orange", "needs clinician"))
        chips += [_badge("gray", term) for term in tr["symptom_terms"]]
        st.markdown(" ".join(chips))
        st.caption(f"Source: {t['source']}" + (f" · model {t['model']}" if t["model"] else "")
                   + (" · retried once" if t["retried"] else "")
                   + (f" · {t['note']}" if t["note"] else ""))
        if mid in replied:
            st.markdown(":green-badge[Replied]")
            return
        text = st.text_area("Draft reply (the nurse edits it before sending)", value=tr["draft_reply"],
                            key=f"inbox_reply_{mid}")
        if st.button("Send (nurse)", key=f"inbox_send_{mid}"):
            if not text.strip():
                st.error("The reply is empty. Write a reply before sending.")
                return
            where = record_decision(message_reply_decision(item, text.strip(), clock))
            target = "the ledger" if where == "ledger" else "the pending decisions file (logs/decisions_pending.jsonl)"
            st.success(f"Reply logged in {target}. It was not sent to the patient (prototype).")


def _inbox_section(clock: str) -> None:
    st.subheader("Inbox")
    demo = os.environ.get("DEMO_MODE") == "1"
    items = _inbox(clock)
    replied = replied_message_ids()
    waiting = [i for i in items
               if i["triage"]["triage"]["needs_clinician"] and i["message_id"] not in replied]
    st.metric("Messages needing a clinician (not replied)", len(waiting))

    if st.button("Triage new messages (live AI)", key="inbox_triage",
                 help="Asks Claude to triage the messages that only have the keyword fallback. "
                      "Output that fails its checks is never shown."):
        todo = [i for i in items if i["triage"]["source"] == "rule_based"]
        with st.spinner(f"Triaging {len(todo)} message(s)..."):
            for i in todo:
                classify({k: i[k] for k in ("message_id", "patient_id", "ts", "sender", "text")}, live=not demo)
        refresh()
        st.cache_data.clear()
        st.rerun()
    if demo:
        st.caption("DEMO_MODE=1: cached triage only, no live Claude call.")

    cutoff = (pd.Timestamp(clock) - OLD_AFTER).strftime("%Y-%m-%dT%H:%M")
    names = _names()
    recent = [i for i in items if i["ts"] >= cutoff]
    older = [i for i in items if i["ts"] < cutoff]
    if not items:
        st.caption("No messages up to the clock.")
    for i in recent:
        _message_card(i, clock, replied, names)
    if older:
        with st.expander(f"Older messages ({len(older)})"):
            for i in older:
                _message_card(i, clock, replied, names)


_inbox_section(state.clock)
