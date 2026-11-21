import logging
import re

import streamlit as st

from ai import explainer
from clinical.queue import get_ward_state
from core import ledger, loader
from core.contracts import PROFILES, Decision, Plan
from ops import eligibility, whatif_cpsat
from ops.capacity import get_capacity
from ops.whatif_view import (
    RESOURCE_LABELS,
    binding_resources,
    describe_moves,
    plan_evidence,
    plan_item_id,
    plan_summary,
    signoff_tasks,
)

logger = logging.getLogger(__name__)

PROFILE_TITLES = {"safety_first": "Safety-first", "balanced": "Balanced", "max_capacity": "Max-capacity"}
ROLES = ["Bed manager", "On-call physician"]
PATHWAYS = {"home_transfer": "Home transfer", "stepdown": "Step-down"}
GROUP_BADGE = {"Suitable": "green", "Needs assessment": "orange", "Not eligible": "gray"}


def _plain(text: str) -> str:
    """Escape characters that Streamlit markdown would treat as LaTeX."""
    return str(text).replace("$", "\\$")


@st.cache_data
def _capacity(clock: str, esc: tuple[str, ...]) -> dict:
    return get_capacity(clock, get_ward_state(clock, list(esc)))


@st.cache_data
def _eligibility(clock: str):
    return eligibility.evaluate(*eligibility.load_scored(clock), clock)


@st.cache_data
def _names() -> dict[str, str]:
    p = loader.load_patients()
    return dict(zip(p["patient_id"], p["name"], strict=True))


@st.cache_data
def _admissions():
    return loader.load_admissions()


@st.cache_data
def _notes(clock: str) -> dict[str, str]:
    n = loader.at_clock(loader.load_notes(), clock)
    return dict(zip(n["note_id"], n["text"], strict=True))


@st.cache_data
def _plans(clock: str, n: int, esc: tuple[str, ...]) -> dict[str, Plan]:
    return whatif_cpsat.run_all_profiles(n, get_ward_state(clock, list(esc)))


@st.cache_data
def _shadow(clock: str, n: int, esc: tuple[str, ...], profile: str, resource: str) -> int:
    return whatif_cpsat.shadow_price(n, get_ward_state(clock, list(esc)), profile, resource)


@st.cache_data
def _explain(key: str, _plans: dict, _cap: dict, _shadow: dict, esc: tuple[str, ...], clock: str) -> dict:
    """Cached on the fact-sheet hash (key), so reruns and clock moves back and forth are instant."""
    return explainer.explain(_plans, _cap, shadow=_shadow, escalated=esc, clock=clock)


def _kpis(cap: dict) -> None:
    beds, slots, kits, visits, pc = (cap["beds"], cap["virtual_slots"], cap["kits"], cap["visits"],
                                     cap["primary_care_handover"])
    cols = st.columns(5)
    cols[0].metric("Beds occupied", f"{beds['occupied']}/{beds['total']}",
                   help=f"Overflow: up to {beds['overflow_max']} extra bed(s)")
    cols[1].metric("Virtual slots used", f"{slots['occupied']}/{slots['total']}")
    cols[2].metric("Kits free", kits["free"],
                   help=None if kits["cleaning_ready"] else
                   f"+{kits['in_cleaning']} in cleaning, ready {kits['cleaning_ready_at'][11:16]}")
    cols[3].metric("Visits booked", f"{visits['booked']}/{visits['capacity']}",
                   help=f"+{visits['overtime_extra']} with overtime, {visits['overtime_cost_sek']:,} SEK")
    cols[4].metric("Primary-care handovers", pc["capacity"], help=f"Capacity today ({pc['team']})")


def _adopted_ids() -> set[str]:
    return {d.item_id for d in ledger.list_decisions(patient_id="WARD", item_type="capacity_plan")}


def _verified_line(result: dict | None, profile: str) -> str | None:
    """one_line of a verified Claude explanation for this profile, else None."""
    if not result or result["source"] != "claude" or not result["verified"] or result["withheld"]:
        return None
    return next((i["one_line"] for i in result["output"]["plans"] if i["profile"] == profile), None)


def _chips(ids) -> str:
    return " ".join(f":gray-badge[{_plain(i)}]" for i in ids)


def _explanation(result: dict, cols) -> None:
    """Render a verified explanation under the plan cards (never called with verified=False)."""
    out, by_id = result["output"], {f["id"]: f["text"] for f in result["facts"]}
    if result["source"] == "claude":
        head = (f":green-badge[{result['n_ok']}/{result['n_checked']} claims verified] "
                ":gray-badge[AI draft - the bed manager decides]")
    elif result["withheld"]:
        head = ":orange-badge[AI draft withheld: failed verification] :gray-badge[Deterministic summary]"
    else:
        head = ":gray-badge[Deterministic summary]"
    st.markdown(f"##### Scenario explanation {head}")
    items = {i["profile"]: i for i in out["plans"]}
    for col, profile in zip(cols, PROFILES, strict=True):
        item = items[profile]
        with col, st.container(border=True):
            st.markdown(f"**{PROFILE_TITLES[profile]}**: {_plain(item['one_line'])}")
            st.markdown("\n".join(f"- {_plain(t)}" for t in item["tradeoffs"]))
            if item["signoffs"]:
                st.markdown(" ".join(f":orange-badge[Sign-off: {_plain(x).replace('EXPEDITE_', 'expedite ')}]"
                                     for x in item["signoffs"]))
            st.markdown(f"Main risk: {_plain(item['main_risk'])}")
            st.markdown(_chips(item["evidence_ids"]))
            with st.expander("Evidence"):
                for e in item["evidence_ids"]:
                    st.markdown(f"**{e}**: {_plain(by_id.get(e, ''))}")
    bc = out["binding_constraint"]
    st.markdown(f"**Binding constraint: {_plain(bc['resource'].replace('_', ' '))}**. "
                f"{_plain(bc['explanation'])} {_chips(bc['evidence_ids'])}")
    st.info(_plain(out["decision_needed"]))


def _plan_card(profile: str, plan: Plan, ctx: dict) -> None:
    clock, n, esc, cap, names = ctx["clock"], ctx["n"], ctx["esc"], ctx["cap"], ctx["names"]
    item_id = plan_item_id(plan, clock, n, esc)
    solver = ":gray-badge[CP-SAT optimal]" if plan.solver == "cp-sat" else ":orange-badge[Heuristic]"
    with st.container(border=True):
        st.markdown(f"#### {PROFILE_TITLES[profile]} {solver}")
        st.markdown(f"### Placed {plan.placed} of {plan.needing_placement}")
        if plan.external_transfers:
            st.markdown(f":red-badge[{plan.external_transfers} external transfer"
                        f"{'s' if plan.external_transfers != 1 else ''}]")
        for label, items in describe_moves(plan, names, ctx["admissions"]):
            if label.startswith("External"):
                continue
            st.markdown(f"**{label}**")
            st.markdown("\n".join(f"- {_plain(i)}" for i in items))
        if plan.signoffs_required:
            st.markdown(" ".join(f":orange-badge[Sign-off: {s.replace('EXPEDITE_', 'expedite ')}]"
                                 for s in plan.signoffs_required))
        else:
            st.markdown(":gray-badge[No sign-offs]")
        badges = []
        if plan.overtime:
            badges.append(f":gray-badge[Overtime team +{cap['visits']['overtime_extra']} visits]")
        if plan.overflow_used:
            badges.append(":red-badge[Overflow bed]")
        if plan.uses_cleaning_kit:
            badges.append(f":gray-badge[Kit from cleaning, {cap['kits']['cleaning_ready_at'][11:16]}]")
        if plan.spare_beds:
            badges.append(f":gray-badge[Spare beds: {plan.spare_beds}]")
        if badges:
            st.markdown(" ".join(badges))
        binding = set(binding_resources(plan))
        for key in ("beds", "kits", "visits", "slots"):
            used, total = plan.usage[key]
            st.progress(min(1.0, used / total) if total else 1.0,
                        text=f"{RESOURCE_LABELS[key]} {used}/{total}" + (" - full" if key in binding else ""))
        st.caption(f"Score {plan.objective} (lower is better)")

        role = st.selectbox("Role", ROLES, key=f"cap_role_{profile}")
        reason = st.text_input("Reason (optional)", key=f"cap_reason_{profile}")
        adopted = item_id in _adopted_ids()
        slot = st.empty()
        if adopted:
            slot.markdown(":green-badge[Adopted]")
        if st.button("Adopt plan", key=f"cap_adopt_{profile}", disabled=adopted, type="primary"):
            suggestion, version = plan_summary(plan), None
            ai_line = _verified_line(ctx.get("explain"), profile)
            if ai_line:
                suggestion += f" AI explanation: {ai_line}"
                version = explainer.PROMPT_VERSION
            ledger.record(Decision(ts=clock, patient_id="WARD", item_type="capacity_plan", item_id=item_id,
                                   ai_suggestion=suggestion, clinician_action="approve",
                                   clinician_role=role, reason=reason or "Adopted from what-if",
                                   evidence_ids=plan_evidence(plan, ctx["elig"])), prompt_version=version)
            tasks = st.session_state["tasks"]
            have = {t["task_id"] for t in tasks}
            new = [t for t in signoff_tasks(plan, item_id, clock, ctx["elig"]) if t["task_id"] not in have]
            tasks.extend(new)
            st.toast(f"Plan adopted: {len(new)} sign-off task(s) sent")
            slot.markdown(":green-badge[Adopted]")
            logger.info("capacity plan adopted: %s (%d tasks)", item_id, len(new))


def _what_if(ctx: dict) -> None:
    esc_all = ctx["escalated"]
    c1, c2, c3 = st.columns([1, 1, 1], vertical_alignment="bottom")
    n = c1.number_input("+N urgent admissions", min_value=0, max_value=len(ctx["admissions"]), value=5,
                        step=1, key="cap_n")
    with c2:
        incl = st.toggle("Include escalated patients", value=True, key="cap_incl_esc", disabled=not esc_all)
        if not esc_all:
            st.caption("None marked yet (Patients page)")
    if c3.button("Find plans", type="primary", key="cap_find"):
        st.session_state["cap_show"] = True
    if not st.session_state.get("cap_show"):
        st.caption("Choose how many urgent admissions arrive, then press Find plans.")
        return

    esc = tuple(esc_all) if (incl and esc_all) else ()
    ctx = {**ctx, "n": int(n), "esc": esc}
    clock = ctx["clock"]
    with st.spinner("Solving"):
        plans = _plans(clock, int(n), esc)
    shadow = {p: {r: _shadow(clock, int(n), esc, p, r) for r in ("kits", "beds")} for p in PROFILES}
    result = None
    if st.session_state.get("cap_explained"):
        key = explainer.cache_key_for(explainer.build_factsheet(plans, ctx["cap"], shadow, esc))
        with st.spinner("Explaining"):
            result = _explain(key, plans, ctx["cap"], shadow, esc, clock)
        ctx["explain"] = result
    cols = st.columns(3)
    for col, profile in zip(cols, PROFILES, strict=True):
        with col:
            _plan_card(profile, plans[profile], ctx)

    for resource, noun in (("kits", "monitoring kit"), ("beds", "bed")):
        for profile in PROFILES:
            extra = shadow[profile][resource]
            if extra > 0:
                st.info(f"One more {noun} would place {extra} more patient{'s' if extra != 1 else ''} "
                        f"({PROFILE_TITLES[profile]})")

    if st.button("Explain plans", key="cap_explain"):
        st.session_state["cap_explained"] = True
        st.rerun()
    if result is not None and result["verified"]:
        _explanation(result, st.columns(3))


def _eligibility_board(ctx: dict) -> None:
    elig, names, cap, notes = ctx["elig"], ctx["names"], ctx["cap"], ctx["notes"]
    risk = cap["transfer_risk"]
    groups: dict[str, list] = {g: [] for g in GROUP_BADGE}
    for r in elig.itertuples():
        groups[eligibility.STATUS_GROUP[r.status]].append(r)
    for col, (group, rows) in zip(st.columns(3), groups.items(), strict=True):
        with col:
            st.markdown(f"##### :{GROUP_BADGE[group]}-badge[{group}] {len(rows)}")
            for r in rows:
                with st.container(border=True):
                    st.markdown(f"**{r.patient_id} {_plain(names.get(r.patient_id, ''))}**")
                    line = f"{PATHWAYS[r.pathway]} - :{GROUP_BADGE[group]}-badge[{r.status.replace('_', ' ')}]"
                    if r.pathway == "home_transfer" and r.patient_id in risk:
                        line += f" - Transfer risk {risk[r.patient_id]:.2f}"
                    st.markdown(line)
                    st.markdown("\n".join(f"- {_plain(x)}" for x in r.reasons))
                    st.markdown(" ".join(f":gray-badge[{_plain(e)}]" for e in r.evidence_ids))
                    with st.expander("Evidence"):
                        for e in r.evidence_ids:
                            if re.fullmatch(r"N-\d+", e):
                                st.markdown(f"**{e}**: {_plain(notes.get(e, '(not yet written)'))}")
                            else:
                                st.markdown(f"- {_plain(e)}")


clock = st.session_state["clock"]
escalated = tuple(st.session_state["escalated"])
names = _names()
cap = _capacity(clock, escalated)
elig = _eligibility(clock)
ctx = {"clock": clock, "escalated": escalated, "names": names, "cap": cap, "elig": elig,
       "admissions": _admissions(), "notes": _notes(clock)}

st.title("Admission Planning")
st.caption(f"Clock {clock[11:16]}")
_kpis(cap)
if escalated:
    st.markdown("  \n".join(
        f":orange-badge[Possible readmission] {pid} {_plain(names.get(pid, ''))}: needs a bed, kit returns to pool"
        for pid in escalated))

tab_whatif, tab_elig = st.tabs(["What-if", "Eligibility board"])
with tab_whatif:
    _what_if(ctx)
    open_tasks = [t for t in st.session_state["tasks"] if t.get("status") == "open"]
    with st.expander(f"Open sign-off tasks ({len(open_tasks)})"):
        if not open_tasks:
            st.caption("None")
        for t in open_tasks:
            st.markdown(f"- {t['action'].capitalize()}: **{t['patient_id']}** ({t['profile']}, "
                        f"{t['created_at'][11:16]})")
with tab_elig:
    _eligibility_board(ctx)
