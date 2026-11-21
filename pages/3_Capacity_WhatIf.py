import streamlit as st

from core.contracts import fake_plan, to_dict

st.title("Capacity & What-if")
st.caption("Owner: Teammate B - placeholder")
st.write("Clock:", st.session_state["clock"])
st.json(to_dict(fake_plan()))
