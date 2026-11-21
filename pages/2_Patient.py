import streamlit as st

from clinical.queue import get_ward_state
from clinical.views import (
    news2_figure,
    patient_header,
    timeline,
    virtual_ward_patients,
    vitals_figure,
)
from core.loader import load_patients

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


def _sync_query_param() -> None:
    st.query_params["pid"] = st.session_state["patient_pid"]


clock = st.session_state["clock"]
queue = _queue(clock)
names = _names()
valid = virtual_ward_patients()
order = [pid for pid, _, _ in queue if pid in valid]
reasons_by_pid = {pid: reasons for pid, _, reasons in queue}

requested = st.query_params.get("pid")
if requested in valid:
    st.session_state["patient_pid"] = requested
elif st.session_state.get("patient_pid") not in valid:
    st.session_state["patient_pid"] = order[0]

pid = st.selectbox(
    "Patient", order, key="patient_pid", on_change=_sync_query_param,
    format_func=lambda p: " - ".join([f"{p} {names.get(p, '')}".strip(), ", ".join(reasons_by_pid[p])]
                                     if reasons_by_pid.get(p) else [f"{p} {names.get(p, '')}".strip()]))

h = _header(pid, clock)

st.title(f"{h['name']}  ·  {h['patient_id']}")
st.caption(f"{h['age']} {h['sex']} · {h['primary_diagnosis']} ({h['icd10']}) · day {h['day_of_stay']} "
           f"of virtual-ward stay · {h['municipality']}")
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
    st.info("Built in A4: Claude briefing with evidence IDs, verifier, approve / edit / reject.")
