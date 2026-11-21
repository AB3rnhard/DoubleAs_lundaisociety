import logging
import os

import streamlit as st
from dotenv import load_dotenv

from core import story

load_dotenv()
os.makedirs("logs", exist_ok=True)
logging.basicConfig(filename="logs/app.log", level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")

st.set_page_config(page_title="Sjukhus Hemma Copilot", layout="wide")

HOURS = [f"2026-11-21T{h:02d}:00" for h in range(9, 22)]
st.session_state.setdefault("clock", HOURS[0])
st.session_state.setdefault("escalated", [])
st.session_state.setdefault("tasks", [])

pg = st.navigation([
    st.Page("pages/1_Command_Center.py", title="Command Center", default=True),
    st.Page("pages/2_Patient.py", title="Patient"),
    st.Page("pages/3_Capacity_WhatIf.py", title="Capacity & What-if"),
    st.Page("pages/4_Ledger.py", title="Ledger"),
])

st.sidebar.select_slider("Simulation clock", options=HOURS, key="clock", format_func=lambda t: t[11:])
story.render()
st.sidebar.caption("Research prototype. Synthetic data. Not for clinical use.")

goto = st.session_state.pop("story_goto", None)
if goto:
    st.switch_page(goto[0], query_params=goto[1])

pg.run()
