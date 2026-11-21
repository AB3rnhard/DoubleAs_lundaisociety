import streamlit as st

from core.contracts import fake_ward_state, to_dict

st.title("Command Center")
st.caption("Owner: Teammate A - placeholder")
st.write("Clock:", st.session_state["clock"])
st.dataframe([to_dict(q) for q in fake_ward_state(st.session_state["clock"]).queue])
