"""Plain-language explanation of what-if plans (Claude draft, verified, deterministic fallback).

Owner: B (Teammate B). Built in prompt B4.

Public interface: build_factsheet, cache_key_for, explain, deterministic_explanation, verify_output.
Flow: factsheet -> Claude (call_json; cache only when live=False) -> ai.verifier.verify + structure checks ->
one retry with the error list (cache key `<key>|retry`) -> deterministic summary.
explain() never raises: on any failure it returns the deterministic explanation.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path

from core.contracts import PROFILES, Plan

logger = logging.getLogger(__name__)

PROMPT_VERSION = "explainer_v1"
PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / f"{PROMPT_VERSION}.md"
DEFAULT_MODEL = "claude-haiku-5-5"

PROFILE_LABELS = {"safety_first": "Safety-first", "balanced": "Balanced", "max_capacity": "Max-capacity"}
# plan.usage key -> (schema resource name, noun used in facts)
USAGE_KEYS = {"beds": "beds", "kits": "kits", "visits": "visits", "slots": "virtual_slots"}
USAGE_WORDS = {"beds": "beds", "kits": "kits", "visits": "visits", "slots": "virtual slots"}
SHADOW_NOUNS = {"kits": "monitoring kit", "beds": "bed"}
PREFERENCE_RE = re.compile(r"\b(recommend|best option|should (choose|pick|adopt)|we suggest|I suggest)\b",
                           re.IGNORECASE)


# ---------------------------------------------------------------- fact sheet
def _label(profile: str) -> str:
    return PROFILE_LABELS[profile]


def _usage_text(plan: Plan) -> str:
    binding = {k for k, (used, cap) in plan.usage.items() if used >= cap}
    return "; ".join(f"{USAGE_WORDS[k]} {used}/{cap}" + (" (full)" if k in binding else "")
                     for k, (used, cap) in plan.usage.items())


def build_factsheet(plans: dict, capacity: dict, shadow: dict | None = None, escalated=()) -> dict:
    """Numbered facts P1..Pn (ids and digits only, no names). Stable order: ward, escalated, plans."""
    facts: list[dict] = []

    def add(text: str) -> str:
        fid = f"P{len(facts) + 1}"
        facts.append({"id": fid, "text": text})
        return fid

    beds, slots, kits, visits = capacity["beds"], capacity["virtual_slots"], capacity["kits"], capacity["visits"]
    ward = [add(f"Ward: beds occupied {beds['occupied']}/{beds['total']}; overflow up to "
                f"{beds['overflow_max']} extra bed"),
            add(f"Ward: virtual slots used {slots['occupied']}/{slots['total']}")]
    kit_text = f"Ward: monitoring kits free {kits['free']}"
    if not kits["cleaning_ready"]:
        kit_text += f"; {kits['in_cleaning']} more in cleaning, ready {kits['cleaning_ready_at'][11:16]}"
    ward.append(add(kit_text))
    ward.append(add(f"Ward: visits booked {visits['booked']}/{visits['capacity']}; overtime adds "
                    f"{visits['overtime_extra']} visits for {visits['overtime_cost_sek']:,} SEK"))
    ward.append(add(f"Ward: primary-care handover capacity {capacity['primary_care_handover']['capacity']}"))
    for pid in escalated:
        ward.append(add(f"{pid} possible readmission: needs a bed; kit returns to the pool"))

    risk = capacity.get("transfer_risk", {})
    plan_fact_ids: dict[str, list[str]] = {}
    kinds: dict[str, dict] = {}
    for profile in PROFILES:
        plan, lab = plans[profile], _label(profile)
        k: dict = {}
        k["placed"] = add(f"{lab}: places {plan.placed} of {plan.needing_placement} patients")
        k["external"] = add(f"{lab}: external transfers {plan.external_transfers}")
        moves = [(name, ids) for name, ids in (("transfers home", plan.transfers_home),
                                               ("step-downs", plan.stepdowns),
                                               ("direct to virtual ward", plan.direct_home),
                                               ("expedited discharges", plan.expedite_discharges)) if ids]
        k["moves"] = add(f"{lab}: " + ("moves - " + "; ".join(f"{n} {', '.join(ids)}" for n, ids in moves)
                                       if moves else "no patients moved"))
        k["risk"] = [add(f"{lab}: {pid} transfer risk {risk[pid]:.2f}")
                     for pid in plan.transfers_home if risk.get(pid, 0) > 0]
        costs = [f"overtime {'yes' if plan.overtime else 'no'}",
                 f"overflow bed {'yes' if plan.overflow_used else 'no'}"]
        kit = f"kit from cleaning {'yes' if plan.uses_cleaning_kit else 'no'}"
        if plan.uses_cleaning_kit:
            kit += f", ready {kits['cleaning_ready_at'][11:16]}"
        k["costs"] = add(f"{lab}: " + "; ".join([*costs, kit]))
        k["signoffs"] = add(f"{lab}: sign-offs required " + (", ".join(plan.signoffs_required)
                                                             if plan.signoffs_required else "none"))
        k["spare"] = add(f"{lab}: spare beds {plan.spare_beds}")
        k["usage"] = add(f"{lab}: usage {_usage_text(plan)}")
        binding = [USAGE_KEYS[key] for key, (used, cap) in plan.usage.items() if used >= cap]
        k["binding"] = add(f"{lab}: binding resources " + (", ".join(binding) if binding else "none"))
        k["shadow"] = []
        for resource, noun in SHADOW_NOUNS.items():
            extra = (shadow or {}).get(profile, {}).get(resource, 0)
            if extra > 0:
                k["shadow"].append(add(f"{lab}: one more {noun} places {extra} more "
                                       f"patient{'s' if extra != 1 else ''}"))
        kinds[profile] = k
        plan_fact_ids[profile] = [i for v in k.values() for i in (v if isinstance(v, list) else [v])]
    return {"facts": facts, "by_id": {f["id"]: f["text"] for f in facts}, "plan_fact_ids": plan_fact_ids,
            "ward_fact_ids": ward, "plan_facts": kinds}


def _model() -> str:
    return os.getenv("CLAUDE_RUNTIME_MODEL", DEFAULT_MODEL)


def cache_key_for(factsheet: dict, model: str | None = None) -> str:
    payload = json.dumps([PROMPT_VERSION, model or _model(), factsheet["facts"]], sort_keys=True,
                         ensure_ascii=False)
    return "explainer:v1:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- verification
def _structure_errors(output: dict, plans: dict, factsheet: dict) -> list[str]:
    errors: list[str] = []
    items = output["plans"]
    seen = [i.get("profile") for i in items]
    if sorted(seen, key=str) != sorted(PROFILES):
        errors.append(f"plans must cover each of {list(PROFILES)} exactly once, got {seen}")
    for item in items:
        profile = item.get("profile")
        if profile not in plans:
            continue
        if set(item.get("signoffs", [])) != set(plans[profile].signoffs_required):
            errors.append(f"{profile}: signoffs {item.get('signoffs')} do not match the plan's sign-offs "
                          f"{list(plans[profile].signoffs_required)}")
        if not set(item.get("evidence_ids") or []) & set(factsheet["plan_fact_ids"][profile]):
            errors.append(f"{profile}: evidence_ids cite none of this plan's facts")
    binding_any = {k for p in plans.values() for k, (used, cap) in p.usage.items() if used >= cap}
    resource = output["binding_constraint"].get("resource")
    names = {USAGE_KEYS[k] for k in binding_any}
    if binding_any:
        if resource not in names:
            errors.append(f"binding_constraint.resource {resource!r} is not binding in any plan "
                          f"(binding: {sorted(names)})")
    elif resource != "none":
        errors.append(f"binding_constraint.resource {resource!r}: nothing is binding, expected 'none'")
    texts = [t for i in items for t in (i.get("one_line", ""), *i.get("tradeoffs", []), i.get("main_risk", ""))]
    texts += [output["binding_constraint"].get("explanation", ""), output.get("decision_needed", "")]
    errors += [f"preference language: {t[:60]!r}" for t in map(str, texts) if PREFERENCE_RE.search(t)]
    return errors


def verify_output(output, plans: dict, factsheet: dict) -> tuple[bool, list[str], int, int]:
    """ai.verifier.verify (claims, numbers, evidence ids) + structural checks. Returns (ok, errors, n_checked, n_ok)."""
    from ai.verifier import verify

    try:
        res = verify(output, {"facts": factsheet["facts"]})
        errors = [*res["errors"], *_structure_errors(output, plans, factsheet)]
    except (KeyError, TypeError, AttributeError) as exc:
        return False, [f"malformed output: {type(exc).__name__}: {exc}"], 0, 0
    return not errors, errors, res["n_checked"], res["n_ok"]


# ---------------------------------------------------------------- deterministic fallback
def _strip(text: str) -> str:
    return text.split(": ", 1)[1] if ": " in text else text


def deterministic_explanation(plans: dict, factsheet: dict) -> dict:
    """Template sentences copied from the facts; passes the same verification as the Claude output."""
    by_id = factsheet["by_id"]
    items = []
    for profile in PROFILES:
        plan, k = plans[profile], factsheet["plan_facts"][profile]
        txt = lambda fid: _strip(by_id[fid])
        cited = [k["placed"], k["moves"], k["external"], k["costs"], k["signoffs"], k["spare"], k["usage"]]
        if plan.placed < plan.needing_placement:
            risk_ids, risk = [k["placed"]], f"Gap remains: {txt(k['placed'])}"
        elif k["risk"]:
            risk_ids, risk = [k["risk"][0]], f"Transfer risk: {txt(k['risk'][0])}"
        elif plan.spare_beds == 0:
            risk_ids, risk = [k["spare"]], f"No buffer: {txt(k['spare'])}"
        else:
            risk_ids, risk = [k["binding"]], f"Tightest: {txt(k['binding'])}"
        items.append({
            "profile": profile,
            "one_line": f"{_label(profile)} {txt(k['placed'])}.",
            "tradeoffs": [txt(i) for i in (k["moves"], k["external"], k["costs"], k["signoffs"], k["usage"])],
            "signoffs": list(plan.signoffs_required),
            "main_risk": risk,
            "evidence_ids": list(dict.fromkeys([*cited, *k["risk"], *k["shadow"], *risk_ids]))})
    binding = {key: [p for p in PROFILES if plans[p].usage[key][0] >= plans[p].usage[key][1]]
               for key in USAGE_KEYS}
    best = max(USAGE_KEYS, key=lambda key: len(binding[key]))
    if binding[best]:
        spread = "every plan" if len(binding[best]) == len(PROFILES) else "some plans"
        bc = {"resource": USAGE_KEYS[best],
              "explanation": f"{USAGE_WORDS[best].capitalize()} are fully used in {spread}.",
              "evidence_ids": [factsheet["plan_facts"][p]["binding"] for p in binding[best]]}
    else:
        bc = {"resource": "none", "explanation": "No resource is fully used in any plan.",
              "evidence_ids": [factsheet["plan_facts"][p]["binding"] for p in PROFILES]}
    return {"plans": items, "binding_constraint": bc,
            "decision_needed": "The team must decide which plan to adopt and whether to accept its "
                               "sign-offs, overtime and overflow use."}


# ---------------------------------------------------------------- Claude call
def _client():
    """call_json from ai.claude_client, or None when A4 is not merged yet (monkeypatchable)."""
    try:
        from ai.claude_client import call_json
    except ImportError:
        return None
    return call_json


def _system_prompt() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8").strip()


def _user_message(factsheet: dict, clock: str) -> str:
    lines = "\n".join(json.dumps(f, ensure_ascii=False) for f in factsheet["facts"])
    return (f"Clock: {clock}\nProfiles: {', '.join(PROFILES)}\nFACTS:\n{lines}\n\nExplain the plans.")


def explain(plans: dict, capacity: dict, *, shadow: dict | None = None, escalated=(), clock: str,
            live: bool = True) -> dict:
    """Verified explanation of the three plans. Never raises; falls back to the deterministic summary."""
    from ai.schemas import EXPLAINER_SCHEMA
    fs = build_factsheet(plans, capacity, shadow, escalated)
    model = _model()
    key = cache_key_for(fs, model)
    base = {"facts": fs["facts"], "cache_key": key, "prompt_version": PROMPT_VERSION, "model": model}

    def fallback(reason: str, withheld: bool, errors: list[str], n_checked=0, n_ok=0) -> dict:
        det = deterministic_explanation(plans, fs)
        ok, det_errors, dn, dok = verify_output(det, plans, fs)
        result = {**base, "source": "deterministic", "verified": ok, "withheld": withheld, "output": det,
                  "n_checked": dn, "n_ok": dok, "errors": errors or det_errors, "reason": reason}
        logger.info("explainer: source=deterministic verified=%s withheld=%s reason=%s errors=%s",
                    ok, withheld, reason, errors)
        return result

    call = _client()
    if call is None:
        return fallback("claude_client not available", False, [])
    system, user = _system_prompt(), _user_message(fs, clock)
    keys = (key, f"{key}|retry")  # A's retry-key convention
    errors: list[str] = []
    try:
        for attempt in range(2):
            msg = user if attempt == 0 else (
                user + "\n\nYour previous answer failed verification. Fix these errors and answer again:\n"
                + "\n".join(f"- {e}" for e in errors))
            try:
                output = call(system, msg, EXPLAINER_SCHEMA, model=model, effort="low", cache_key=keys[attempt],
                              prompt_version=PROMPT_VERSION, cache_only=not live)
            except Exception:
                if attempt == 1 and errors and not live:
                    break  # the first cached draft failed verification and there is no cached retry
                raise
            ok, errors, n_checked, n_ok = verify_output(output, plans, fs)
            logger.info("explainer: claude attempt %d verified=%s %d/%d errors=%s",
                        attempt + 1, ok, n_ok, n_checked, errors)
            if ok:
                return {**base, "source": "claude", "verified": True, "withheld": False, "output": output,
                        "n_checked": n_checked, "n_ok": n_ok, "errors": [], "reason": ""}
    except Exception as exc:  # noqa: BLE001 - any API / cache-miss failure must fall back
        logger.warning("explainer: claude call failed (%s: %s)", type(exc).__name__, exc)
        return fallback(f"claude call failed: {type(exc).__name__}", False, [])
    if live:
        _drop_cached(keys)
    return fallback("claude draft failed verification", True, errors)


def _drop_cached(keys) -> None:
    """After two failed live attempts forget both cached drafts so they are not served again."""
    try:
        from ai.claude_client import cache_drop
        for k in keys:
            cache_drop(k)
    except Exception as exc:  # noqa: BLE001 - cache hygiene must never break the page
        logger.warning("explainer: cache_drop failed (%s)", exc)


if __name__ == "__main__":
    import sys

    from clinical.queue import get_ward_state
    from core.contracts import T0
    from ops import whatif_cpsat
    from ops.capacity import get_capacity

    pid = get_ward_state("2026-11-21T14:00").queue[0].patient_id  # top of the real queue once flagged
    state = get_ward_state(T0, [pid])
    cap = get_capacity(T0, state)
    plans_ = whatif_cpsat.run_all_profiles(5, state)
    shadow_ = {p: {r: whatif_cpsat.shadow_price(5, state, p, r) for r in SHADOW_NOUNS} for p in PROFILES}
    sheet = build_factsheet(plans_, cap, shadow_, state.escalated)
    for f in sheet["facts"]:
        print(f["id"], f["text"])
    det = deterministic_explanation(plans_, sheet)
    print(json.dumps(det, indent=1))
    ok_, errs_, n_, n_ok_ = verify_output(det, plans_, sheet)
    print(f"verification: ok={ok_} {n_ok_}/{n_} errors={errs_}")
    if "--live" in sys.argv:
        res = explain(plans_, cap, shadow=shadow_, escalated=state.escalated, clock=T0)
        print(json.dumps({k: v for k, v in res.items() if k != "facts"}, indent=1))
