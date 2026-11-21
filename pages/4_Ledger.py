import json
import logging

import streamlit as st

from ops import kpis, ledger_view

logger = logging.getLogger(__name__)


@st.cache_data
def _alerts(clock: str) -> dict:
    return kpis.alert_kpis(clock)


@st.cache_data
def _artefacts(clock: str) -> int:
    return kpis.artefacts_caught(clock)


clock = st.session_state["clock"]
st.title("Ledger")
st.caption("Every AI suggestion and every human decision, with its evidence.")

rows = ledger_view.load_rows()  # not cached: new decisions must appear immediately
c = ledger_view.counters(rows)
alerts = _alerts(clock)
cols = st.columns(6)
cols[0].metric("AI suggestions approved", c["approved"])
cols[1].metric("AI suggestions edited", c["edited"])
cols[2].metric("AI suggestions rejected", c["rejected"])
cols[3].metric("Sign-offs recorded", c["signoffs"])
cols[4].metric("Alerts suppressed", alerts["suppressed"],
               help=f"Since monitoring start, naive -> plan-aware: {alerts['naive']} -> {alerts['plan_aware']}")
cols[5].metric("Artefacts caught", _artefacts(clock))

if not rows:
    st.info("No decisions yet: approve a briefing (Patient), reply in the Inbox (Command Center) "
            "or adopt a plan (Capacity & What-if).")
else:
    f1, f2, f3 = st.columns(3)
    pids = f1.multiselect("Patient", sorted({r["patient_id"] for r in rows}), key="led_pid")
    types = f2.multiselect("Item type", sorted({r["item_type"] for r in rows}), key="led_type")
    acts = f3.multiselect("Action", sorted({r["clinician_action"] for r in rows}), key="led_action")
    shown = ledger_view.filter_rows(rows, pids, types, acts)

    event = st.dataframe(ledger_view.table_frame(shown), hide_index=True, width="stretch",
                         on_select="rerun", selection_mode="single-row", key="led_table")
    st.download_button("Export CSV", ledger_view.csv_bytes(shown), file_name=f"ledger_{clock[11:13]}{clock[14:16]}.csv",
                       mime="text/csv", key="led_export")

    sel = event.selection.rows if event and event.selection else []
    if sel and sel[0] < len(shown):
        r = shown[sel[0]]
        st.subheader(f"{r['item_id']} - {r['clinician_action']}")
        try:
            st.code(json.dumps(json.loads(r["ai_suggestion"]), indent=2, ensure_ascii=False), language="json")
        except (TypeError, ValueError):
            st.write((r["ai_suggestion"] or "").replace("$", "\\$"))
        st.markdown(" ".join(f":gray-badge[{e}]" for e in r["evidence_ids"]) or "No evidence ids.")
