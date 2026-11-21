import os

import pandas as pd
import streamlit as st

from ai.factsheet import get_briefing
from clinical.decisions import briefing_decision, record_decision
from clinical.queue import get_ward_state
from clinical.views import (
    inpatients,
    news2_figure,
    patient_header,
    timeline,
    virtual_ward_patients,
    vitals_figure,
)
from core.loader import load_patients
from ops.kpis import inpatient_overview

RED_REASONS = {"NEWS2_EMERGENCY", "NEWS2_URGENT"}
AMBER_REASONS = {"TREND", "SYMPTOM_MESSAGE", "PLAN_BREACH"}


@st.cache_data
def _queue(clock: str) -> list[tuple[str, int, list[str]]]:
    return [(q.patient_id, q.priority, list(q.reasons)) for q in get_ward_state(clock).queue]


@st.cache_data
def _names() -> dict[str, str]:
    p = load_patients()
    return dict(zip(p["patient_id"], p["name"], strict=True))


@st.cache_data
def _beds() -> dict[str, str]:
    p = load_patients()
    return dict(zip(p["patient_id"], p["bed_or_slot"], strict=True))


@st.cache_data
def _overview(clock: str) -> dict[str, dict]:
    return {r["patient_id"]: r for r in inpatient_overview(clock)}


@st.cache_data
def _header(pid: str, clock: str) -> dict:
    return patient_header(pid, clock)


@st.cache_data
def _vitals(pid: str, clock: str):
    return vitals_figure(pid, clock)


@st.cache_data
def _news2(pid: str, clock: str):
    return news2_figure(pid, clock)


@st.cache_data
def _timeline(pid: str, clock: str) -> list[dict]:
    return timeline(pid, clock)


def _chip(reason: str) -> str:
    colour = "red" if reason in RED_REASONS else "orange" if reason in AMBER_REASONS else "gray"
    return f":{colour}-badge[{reason}]"


def _plain(text: str) -> str:
    """Escape characters that Streamlit markdown would treat as LaTeX."""
    return text.replace("$", "\\$")


STATUS_CHIP = {
    "needs_review_now": ":red-badge[Needs review now]",
    "review_today": ":orange-badge[Review today]",
    "routine": ":gray-badge[Routine]",
}
STATE_CHIP = {
    "triggered": ":red-badge[triggered]",
    "approaching": ":orange-badge[approaching]",
    "not_triggered": ":gray-badge[not triggered]",
}
ACTIONS = {"Approve": "approve", "Edit": "edit", "Reject": "reject"}


def _cited(text: str, ids: list[str], evidence: dict[str, str], prefix: str = "") -> None:
    """One line of AI text ending with its evidence ids as badges; hovering shows the cited evidence."""
    badges = " ".join(f":gray-badge[{i}]" for i in ids)
    tooltip = "  \n".join(f"**{i}**: {_plain(evidence.get(i, ''))}" for i in ids)
    st.markdown(f"{prefix}{_plain(text)} {badges}", help=tooltip or None)


def _render_briefing(res: dict) -> None:
    b, fs = res["briefing"], res["factsheet"]
    evidence = {e["id"]: e["text"] for key in ("facts", "notes", "messages") for e in fs[key]}

    chips = [STATUS_CHIP[b["status"]]]
    if res["source"] == "rule_based":
        chips.append(":orange-badge[Rule-based summary]")
    else:
        chips.append(f":green-badge[{res['checks']['n_ok']} of {res['checks']['n_checked']} claims verified]")
    st.markdown(" ".join(chips))
    if res["note"]:
        st.caption(res["note"])
    st.markdown(f"**{_plain(b['headline'])}**")

    def bullets(title: str, items: list[tuple[str, list[str]]]) -> None:
        if items:
            st.markdown(f"**{title}**")
            for text, ids in items:
                _cited(text, ids, evidence, "- ")

    bullets("Summary", [(p["text"], p["evidence_ids"]) for p in b["summary_points"]])

    if b["changes_vs_baseline"]:
        st.markdown("**Changes vs baseline**")
        st.table(pd.DataFrame(
            [{"Parameter": c["parameter"], "Baseline": c["baseline"], "Current": c["current"],
              "Evidence": ", ".join(c["evidence_ids"])} for c in b["changes_vs_baseline"]]
        ).set_index("Parameter"))

    if b["plan_rules"]:
        st.markdown("**Plan rules**")
        for r in b["plan_rules"]:
            _cited(r["rule"], r["evidence_ids"], evidence, f"{STATE_CHIP[r['state']]} ")

    bullets("Relevant history", [(h["text"], h["evidence_ids"]) for h in b["relevant_history"]])
    bullets("Suggested actions (options for the clinician, not decisions)",
            [(f"{a['action']} - {a['rationale']}", a["evidence_ids"]) for a in b["suggested_actions"]])

    if b["missing_information"]:
        st.markdown("**Missing information**")
        for m in b["missing_information"]:
            st.markdown(f"- {_plain(m)}")


def _decision_form(res: dict, pid: str, clock: str) -> None:
    suffix = f"{pid}_{clock}"  # a decision belongs to one patient at one clock time
    with st.form(f"briefing_form_{suffix}", clear_on_submit=True):
        label = st.radio("Decision", list(ACTIONS), horizontal=True, key=f"briefing_action_{suffix}")
        role = st.selectbox("Role", ["physician", "nurse"], key=f"briefing_role_{suffix}")
        text = st.text_area("Edited text (for Edit) or reason (for Reject)", key=f"briefing_text_{suffix}")
        readmit = (False if pid in inpatients() else
                   st.checkbox("Mark possible readmission", key=f"briefing_readmit_{suffix}"))
        submitted = st.form_submit_button("Submit decision")
    if not submitted:
        return
    action = ACTIONS[label]
    if action != "approve" and not text.strip():
        st.error("Write the edited text for Edit, or the reason for Reject.")
        return
    where = record_decision(briefing_decision(res, action, role, text.strip(), clock))
    if readmit and pid not in st.session_state["escalated"]:
        st.session_state["escalated"] = [*st.session_state["escalated"], pid]
    target = ("the ledger" if where == "ledger"
              else "the pending decisions file (logs/decisions_pending.jsonl), to be imported into the ledger")
    st.success(f"Decision '{action}' by {role} recorded in {target}. "
               f"Possible readmissions: {', '.join(st.session_state['escalated']) or 'none'}.")


def _sync_query_param() -> None:
    st.query_params["pid"] = st.session_state["patient_pid"]


clock = st.session_state["clock"]
queue = _queue(clock)
names = _names()
home = virtual_ward_patients()
ward = inpatients()
valid = [*home, *ward]
order = [*[pid for pid, _, _ in queue if pid in home], *ward]
beds = _beds()
reasons_by_pid = {pid: reasons for pid, _, reasons in queue}

requested = st.query_params.get("pid")
if requested in valid:
    st.session_state["patient_pid"] = requested
elif st.session_state.get("patient_pid") not in valid:
    st.session_state["patient_pid"] = order[0]

pid = st.selectbox(
    "Patient", order, key="patient_pid", on_change=_sync_query_param,
    format_func=lambda p: (f"{p} {names.get(p, '')} · inpatient {beds.get(p, '')}" if p in ward else
                           " - ".join([f"{p} {names.get(p, '')}".strip(), ", ".join(reasons_by_pid[p])]
                                      if reasons_by_pid.get(p) else [f"{p} {names.get(p, '')}".strip()])))

h = _header(pid, clock)

st.title(f"{h['name']}  ·  {h['patient_id']}")
is_inpatient = h["location"] == "INPATIENT"
stay = (f"of hospital stay · bed {h['bed_or_slot']}" if is_inpatient else "of virtual-ward stay")
st.caption(f"{h['age']} {h['sex']} · {h['primary_diagnosis']} ({h['icd10']}) · day {h['day_of_stay']} "
           f"{stay} · {h['municipality']}")
if is_inpatient and pid in _overview(clock):
    _o = _overview(clock)[pid]
    st.caption(f"Home care: {_o['home_care'] or '-'} ({_o['reason'] or '-'}) · "
               f"transfer risk {_o['transfer_risk']:.2f}")
st.markdown(" ".join(_chip(r) for r in h["reasons"]) if h["reasons"] else ":gray-badge[No active flags]")

m1, m2, m3 = st.columns(3)
m1.metric("NEWS2 (latest good reading)", h["news2"], help=f"Highest single parameter score: {h['news2_max_single']}")
m2.metric("Priority", h["priority"])
m3.metric("Flagged at", h["flagged_at"][11:] if h["flagged_at"] else "-")

caregiver = h["caregiver_at_home"] or "none"
st.markdown(
    f"**Therapy:** {h['current_therapy']} ({h['visits_per_day']} visits/day)  \n"
    f"**SpO2 scale:** {h['spo2_scale']} (plan set by {h['plan_set_by']})  \n"
    f"**Home:** {'lives alone' if h['lives_alone'] else 'not alone'}, caregiver at home: {caregiver}  \n"
    f"**Comorbidities:** {'; '.join(h['comorbidities']) or 'none recorded'}")
if h["custom_rule"]:
    with st.expander("Monitoring plan: custom rule"):
        st.write(h["custom_rule"])

st.plotly_chart(_vitals(pid, clock), width="stretch")
st.subheader("NEWS2, last 24 h")
st.plotly_chart(_news2(pid, clock), width="stretch")

st.subheader("Timeline")
events = _timeline(pid, clock)
if not events:
    st.caption("No notes or messages up to the clock.")
for ev in events:
    badge = f":orange-badge[{ev['id']}]" if ev["symptom"] else f":gray-badge[{ev['id']}]"
    line = f"{badge} **{ev['ts'].replace('T', ' ')}** · {ev['who']}  \n{_plain(ev['text'])}"
    if ev["symptom"]:
        st.warning(line, icon=":material/chat:")
    else:
        st.markdown(line)

with st.container(border=True):
    st.subheader("AI briefing")
    demo = os.environ.get("DEMO_MODE") == "1"
    generate = st.button("Generate briefing", key="briefing_generate",
                         help="Asks Claude for a briefing (cache only when DEMO_MODE=1). "
                              "Output that fails the verifier is never shown.")
    if generate:
        with st.spinner("Asking Claude and verifying the answer..."):
            res = get_briefing(pid, clock, live=True)
    else:
        res = get_briefing(pid, clock, live=False)
    if demo:
        st.caption("DEMO_MODE=1: cached briefings only, no live Claude call.")
    st.caption(f"Source: {res['source']}" + (f" · model {res['model']}" if res["model"] else "")
               + (" · retried once" if res["retried"] else ""))
    _render_briefing(res)
    st.divider()
    st.markdown("**Clinician review: do you agree with the AI summary?**")
    if pid in st.session_state["escalated"]:
        st.markdown(":red-badge[Marked for possible readmission]")
    _decision_form(res, pid, clock)
