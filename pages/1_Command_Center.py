import pandas as pd
import streamlit as st

from clinical.queue import get_ward_state
from core.contracts import WardState


@st.cache_data
def _ward_state(clock: str, escalated: tuple[str, ...]) -> WardState:
    return get_ward_state(clock, list(escalated))


state = _ward_state(st.session_state["clock"], tuple(st.session_state["escalated"]))

st.title("Command Center")
st.write("Clock:", state.clock)
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
