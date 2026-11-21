import os

import pandas as pd
import streamlit as st

from ai.triage import classify, inbox
from clinical.artefact import SIGNAL_QUALITY_MIN
from clinical.decisions import message_reply_decision, record_decision, replied_message_ids
from clinical.queue import get_ward_state, refresh
from core.contracts import WardState
from core.loader import load_patients
from ops.kpis import command_center_kpis, inpatient_overview, kpi_tiles


@st.cache_data
def _ward_state(clock: str, escalated: tuple[str, ...]) -> WardState:
    return get_ward_state(clock, list(escalated))


@st.cache_data
def _kpis(clock: str, escalated: tuple[str, ...]) -> list[dict]:
    return kpi_tiles(command_center_kpis(clock, list(escalated)))


@st.cache_data
def _hospital(clock: str) -> list[dict]:
    return inpatient_overview(clock)


state = _ward_state(st.session_state["clock"], tuple(st.session_state["escalated"]))

st.title("Patients Dashboard")
st.write("Clock:", state.clock)
_tiles = _kpis(st.session_state["clock"], tuple(st.session_state["escalated"]))
for _i in range(0, len(_tiles), 4):
    _row = _tiles[_i:_i + 4]
    for _col, _t in zip(st.columns(4), _row, strict=False):
        _col.metric(_t["label"], _t["value"], help=_t["help"])
st.metric("Alerts suppressed", state.suppressed_alerts)

st.subheader("Patients at home (virtual ward)")
st.dataframe(
    pd.DataFrame([
        {"patient_id": q.patient_id, "priority": q.priority, "reasons": ", ".join(q.reasons),
         "news2": q.news2, "news2_max_single": q.news2_max_single, "flagged_at": q.flagged_at}
        for q in state.queue
    ]),
    hide_index=True,
)

st.subheader("ATTENTION HERE")
st.caption(f"Readings with low signal quality (signal_quality < {SIGNAL_QUALITY_MIN}): "
           "excluded from alerting, re-measure.")
if state.artefacts:
    st.dataframe(pd.DataFrame(state.artefacts), hide_index=True)
else:
    st.caption("No low-signal readings up to the clock.")


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
            target = "the ledger" if where == "ledger" else "the pending decisions file (logs/decisions_pending.jsonl), to be imported into the ledger"
            st.success(f"Reply logged in {target}. It was not sent to the patient (prototype).")


def _inbox_section(clock: str) -> None:
    st.subheader("Home Hospital Inbox")
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


# ---------- Hospital overview: inpatients (ops.eligibility, no new scoring) ----------
def _hospital_section(clock: str, escalated: list[str]) -> None:
    rows = _hospital(clock)
    st.subheader("Patients at the hospital (inpatients)")
    n_s = sum(r["home_care"] == "Suitable" for r in rows)
    n_a = sum(r["home_care"] == "Needs assessment" for r in rows)
    n_x = sum(r["home_care"] == "Not eligible" for r in rows)
    st.caption(f"{len(rows)} inpatients · home transfer: {n_s} suitable, {n_a} need assessment, "
               f"{n_x} not eligible")
    st.dataframe(
        pd.DataFrame([
            {"bed": r["bed"], "patient": f"{r['patient_id']} {r['name']}", "diagnosis": r["diagnosis"],
             "news2_max_24h": r["news2_max_24h"], "news2_mean_24h": r["news2_mean_24h"],
             "home_care": r["home_care"], "why": r["reason"], "transfer_risk": r["transfer_risk"],
             "planned_discharge": r["planned_discharge"]}
            for r in rows
        ]),
        hide_index=True,
        column_config={
            "bed": st.column_config.TextColumn("Bed"),
            "patient": st.column_config.TextColumn("Patient"),
            "diagnosis": st.column_config.TextColumn("Diagnosis"),
            "news2_max_24h": st.column_config.NumberColumn("NEWS2 max 24 h", format="%d"),
            "news2_mean_24h": st.column_config.NumberColumn("NEWS2 mean 24 h", format="%.2f"),
            "home_care": st.column_config.TextColumn("Home care"),
            "why": st.column_config.TextColumn("Why"),
            "transfer_risk": st.column_config.NumberColumn("Transfer risk", format="%.2f"),
            "planned_discharge": st.column_config.TextColumn("Planned discharge"),
        },
    )
    names = _names()
    for pid in escalated:
        st.markdown(f":orange-badge[Possible readmission] {pid} {names.get(pid, '')}: "
                    "at home now, needs a bed")
    st.caption("Home-care status and reasons come from the Admission Planning eligibility board; "
               "act on them there.")


_hospital_section(state.clock, list(st.session_state["escalated"]))
_inbox_section(state.clock)
