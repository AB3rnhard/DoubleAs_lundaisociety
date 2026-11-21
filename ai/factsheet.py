"""Build the evidence factsheet (IDs) a briefing is generated from, and the get_briefing entry point.

Owner: A (Teammate A). Built in prompt A4.

Public interface:
    build_factsheet(pid, clock) -> dict          pure, deterministic, data with ts <= clock only
    get_briefing(pid, clock, live=False) -> dict  verified briefing (claude / cache) or rule-based

Evidence ids: facts F1..Fn (numbered in the order profile, plan, queue, news2, vital, artefact,
deviation), notes N-###, messages M-### (as in the data).
live=False never makes a network call: it only reads the cache and otherwise falls back to the
deterministic rule-based briefing. An AI output that fails ai.verifier is never returned.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path

import pandas as pd

from ai import claude_client as cc
from ai.schemas import BRIEFING_PROMPT_VERSION, BRIEFING_SCHEMA
from ai.verifier import deterministic_briefing, verify
from clinical.artefact import to_iso
from clinical.queue import readings
from clinical.trend import deviations
from clinical.views import patient_header
from core.loader import load_messages, load_monitoring_plans, load_notes, load_vitals

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
RECENT = pd.Timedelta(hours=6)
ARTEFACT_WINDOW = pd.Timedelta(hours=24)
MAX_FACTS = 60

_PARAM_NAMES = {"hr": "HR", "spo2": "SpO2", "rr": "RR", "sbp": "SBP", "temp_c": "Temp"}


# ---------- number formatting (as written in the data) ----------
def _int(x) -> str:
    return str(round(float(x)))


def _temp(x) -> str:
    return f"{float(x):.1f}"


def _med(x) -> str:
    """At most one decimal, a trailing .0 dropped (130.0 -> 130, 37.2 -> 37.2)."""
    s = f"{round(float(x), 1):.1f}"
    return s.removesuffix(".0")


def _fmt(param: str, x) -> str:
    return _temp(x) if param == "temp_c" else _int(x)


def _hhmm(ts) -> str:
    return pd.Timestamp(ts).strftime("%H:%M")


# ---------- data ----------
@lru_cache(maxsize=1)
def _tables() -> dict:
    return {
        "plans": load_monitoring_plans().drop_duplicates("patient_id", keep="last").set_index("patient_id"),
        "notes": load_notes(),
        "messages": load_messages(),
        "vitals": load_vitals(),
    }


def expected_status(reasons: list[str]) -> str:
    """Rule-based status the briefing must carry (the model copies it)."""
    r = set(reasons)
    if r & {"NEWS2_URGENT", "NEWS2_EMERGENCY"} or {"TREND", "SYMPTOM_MESSAGE"} <= r:
        return "needs_review_now"
    if r & {"TREND", "SYMPTOM_MESSAGE", "PLAN_BREACH"}:
        return "review_today"
    return "routine"


def _vital_text(row) -> str:
    oxygen = "yes" + (f" ({_int(row['o2_l_min'])} L/min)" if row["o2_l_min"] and row["o2_l_min"] > 0 else "") \
        if row["on_oxygen"] else "no"
    return (f"{_hhmm(row['ts'])} HR {_int(row['hr'])}, SpO2 {_int(row['spo2'])}, RR {_int(row['rr'])}, "
            f"SBP {_int(row['sbp'])}, Temp {_temp(row['temp_c'])}, on oxygen {oxygen}, "
            f"consciousness {row['consciousness']}")


def _plan_facts(plan, reasons: list[str], last) -> list[dict]:
    """One fact per monitoring-plan rule. `triggered` (internal) is evaluated on the latest good reading."""
    def breached(test) -> bool:
        return bool(last is not None and test(last))

    rules = [
        (f"Plan: SpO2 scale {_int(plan['spo2_scale'])}, target range "
         + f"{_int(plan['spo2_target_low'])}-{_int(plan['spo2_target_high'])}",
         breached(lambda r: r["spo2"] < plan["spo2_target_low"]
                  or (r["spo2"] > plan["spo2_target_high"] and r["on_oxygen"] == 1))),
        (f"Plan: HR range {_int(plan['hr_low'])}-{_int(plan['hr_high'])}",
         breached(lambda r: r["hr"] < plan["hr_low"] or r["hr"] > plan["hr_high"])),
        (f"Plan: RR high limit {_int(plan['rr_high'])}", breached(lambda r: r["rr"] > plan["rr_high"])),
        (f"Plan: SBP low limit {_int(plan['sbp_low'])}", breached(lambda r: r["sbp"] < plan["sbp_low"])),
        (f"Plan: Temp high limit {_temp(plan['temp_high'])}", breached(lambda r: r["temp_c"] > plan["temp_high"])),
        (f"Plan: NEWS2 urgent threshold {_int(plan['news2_urgent_threshold'])} "
         + "(or any single parameter scoring 3)", "NEWS2_URGENT" in reasons),
        (f"Plan: NEWS2 emergency threshold {_int(plan['news2_emergency_threshold'])}",
         "NEWS2_EMERGENCY" in reasons),
    ]
    facts = [{"type": "plan", "ts": None, "text": text, "triggered": hit} for text, hit in rules]
    if isinstance(plan["custom_rule"], str) and plan["custom_rule"].strip():
        facts.append({"type": "plan", "ts": None, "triggered": False,
                      "text": f"Plan custom rule (not machine-checked): {plan['custom_rule'].strip()}"})
    if isinstance(plan["set_by"], str) and plan["set_by"].strip():
        facts.append({"type": "plan", "ts": None, "triggered": None,
                      "text": f"Plan set by {plan['set_by'].strip()}"})
    return facts


def _stay_text(h: dict) -> str:
    """Stay wording: unchanged for virtual-ward patients (warmed cache keys), hospital stay for inpatients."""
    if h.get("location") == "INPATIENT":
        return f"day {h['day_of_stay']} of hospital stay (bed {h['bed_or_slot']})"
    return f"day {h['day_of_stay']} of virtual-ward stay"


def _profile_text(h: dict) -> str:
    comorb = "; ".join(h["comorbidities"]) or "none recorded"
    stay = _stay_text(h)
    return (f"{h['name']}, {h['age']} {h['sex']}. Diagnosis: {h['primary_diagnosis']} ({h['icd10']}). "
            f"Comorbidities: {comorb}. {stay[0].upper() + stay[1:]}. "
            f"Lives alone: {'yes' if h['lives_alone'] else 'no'}. "
            f"Caregiver at home: {h['caregiver_at_home'] or 'none'}. "
            f"Current therapy: {h['current_therapy']}. Visits per day: {h['visits_per_day']}.")


@lru_cache(maxsize=256)
def _build(pid: str, clock: str) -> dict:
    cut = pd.Timestamp(clock)
    h = patient_header(pid, cut)  # KeyError for an unknown patient
    t = _tables()
    reasons = list(h["reasons"])
    r = readings(pid, cut)
    good = r[~r["artefact"]]
    last = good.iloc[-1] if not good.empty else None

    facts: list[dict] = [{"type": "profile", "ts": None, "text": _profile_text(h)}]
    if pid in t["plans"].index:
        facts += _plan_facts(t["plans"].loc[pid], reasons, last)

    queue_text = ("Queue: No active flags" if not reasons else
                  f"Queue: reasons {', '.join(reasons)}; priority {h['priority']}"
                  + (f"; first flagged {_hhmm(h['flagged_at'])}" if h["flagged_at"] else ""))
    facts.append({"type": "queue", "ts": to_iso(cut), "text": queue_text})

    recent = good[good["ts"] > cut - RECENT]
    if last is None:
        facts.append({"type": "news2", "ts": None, "text": "NEWS2 now: no good reading available"})
    else:
        facts.append({"type": "news2", "ts": to_iso(last["ts"]),
                      "text": f"NEWS2 now: {int(last['news2'])} (highest single parameter score "
                              f"{int(last['news2_max_single'])}), latest good reading at {_hhmm(last['ts'])}"})
    if not recent.empty:
        series = ", ".join(f"{_hhmm(x.ts)} {int(x.news2)}" for x in recent.itertuples())
        facts.append({"type": "news2", "ts": to_iso(recent["ts"].iloc[-1]),
                      "text": f"NEWS2 of good readings in the last 6 h: {series}"})

    for x in recent.to_dict("records"):
        facts.append({"type": "vital", "ts": to_iso(x["ts"]), "text": _vital_text(x)})

    art = r[r["artefact"] & (r["ts"] > cut - ARTEFACT_WINDOW)]
    for x in art.to_dict("records"):
        facts.append({"type": "artefact", "ts": to_iso(x["ts"]),
                      "text": f"{_vital_text(x)}, signal quality {x['signal_quality']:.2f}: "
                              "artefact, excluded from scoring, re-measure"})

    vitals = t["vitals"]
    inpatient = h.get("location") == "INPATIENT"
    for d in ([] if inpatient else deviations(pid, cut, vitals[vitals["patient_id"] == pid])):
        p = d["param"]
        facts.append({"type": "deviation", "ts": d["ts"], "param": p,
                      "baseline_text": _med(d["baseline_median"]), "value_text": _fmt(p, d["value"]),
                      "text": f"{p} {_fmt(p, d['value'])} at {_hhmm(d['ts'])} "
                              f"(baseline median {_med(d['baseline_median'])}, z {d['z']:.1f})"})

    # at most 60 facts: drop the oldest vital facts first, then the oldest artefacts
    for kind in ("vital", "artefact"):
        while len(facts) > MAX_FACTS and any(f["type"] == kind for f in facts):
            facts.remove(next(f for f in facts if f["type"] == kind))
    facts = [{"id": f"F{i}", **f} for i, f in enumerate(facts, start=1)]

    notes = t["notes"]
    notes = notes[(notes["patient_id"] == pid) & (notes["ts"] <= cut)].sort_values(["ts", "note_id"])
    msgs = t["messages"]
    msgs = msgs[(msgs["patient_id"] == pid) & (msgs["ts"] <= cut)].sort_values(["ts", "message_id"])

    profile_line = (f"{h['name']}, {h['age']} {h['sex']}, {h['primary_diagnosis']}, "
                    f"{_stay_text(h)}")
    return {
        "patient_id": pid,
        "clock": to_iso(cut),
        "profile_line": profile_line,
        "reasons": reasons,
        "expected_status": expected_status(reasons),
        "facts": facts,
        "notes": [{"id": str(n.note_id), "ts": to_iso(n.ts), "author_role": str(n.author_role),
                   "note_type": str(n.note_type), "text": str(n.text)} for n in notes.itertuples(index=False)],
        "messages": [{"id": str(m.message_id), "ts": to_iso(m.ts), "sender": str(m.sender),
                      "text": str(m.text)} for m in msgs.itertuples(index=False)],
    }


def build_factsheet(pid: str, clock: str | pd.Timestamp) -> dict:
    """Evidence for one patient at `clock` (data with ts <= clock only). Unknown pid -> KeyError."""
    return copy.deepcopy(_build(pid, to_iso(pd.Timestamp(clock))))


# ---------- prompt ----------
def load_system_prompt(name: str = BRIEFING_PROMPT_VERSION) -> str:
    """Prompt file without its first line (the `<!-- version -->` marker)."""
    with open(PROMPT_DIR / f"{name}.md", encoding="utf-8") as f:
        return f.read().split("\n", 1)[1].strip()


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _evidence_view(fs: dict) -> tuple[list[dict], list[dict], list[dict]]:
    facts = [{k: f[k] for k in ("id", "type", "ts", "text")} for f in fs["facts"]]
    return facts, fs["notes"], fs["messages"]


def build_user_message(fs: dict) -> str:
    facts, notes, messages = _evidence_view(fs)
    return (f"Patient summary: {fs['profile_line']}\n"
            f"Clock: {fs['clock']}\n"
            f"Queue reasons: {', '.join(fs['reasons']) or 'none'}\n"
            f"Rule-based status: {fs['expected_status']}\n"
            f"FACTS:\n{_json(facts)}\n"
            f"NOTES:\n{_json(notes)}\n"
            f"MESSAGES:\n{_json(messages)}\n"
            "Write the briefing.")


def _cache_key(fs: dict, model: str) -> str:
    digest = hashlib.sha256(_json(_evidence_view(fs)).encode("utf-8")).hexdigest()[:12]
    return f"{BRIEFING_PROMPT_VERSION}|{model}|{fs['patient_id']}|{fs['clock']}|{digest}"


# ---------- get_briefing ----------
def get_briefing(pid: str, clock: str | pd.Timestamp, live: bool = False) -> dict:
    """Verified briefing for the Patient page. See the module docstring for the guarantees."""
    fs = build_factsheet(pid, clock)
    model = cc._default_model()
    key = _cache_key(fs, model)
    expected = fs["expected_status"]

    def result(source: str, briefing: dict, checks: dict, retried: bool, note: str) -> dict:
        logger.info("briefing %s %s: source=%s retried=%s %s", pid, fs["clock"], source, retried, note)
        return {"patient_id": pid, "clock": fs["clock"], "source": source, "briefing": briefing,
                "checks": {k: checks[k] for k in ("n_checked", "n_ok", "errors")}, "factsheet": fs,
                "model": model if source != "rule_based" else "", "prompt_version": BRIEFING_PROMPT_VERSION,
                "cache_key": key, "retried": retried, "note": note}

    def fallback(note: str, retried: bool, failed: dict | None = None) -> dict:
        det = deterministic_briefing(fs)
        return result("rule_based", det, failed or verify(det, fs, expected), retried, note)

    system = load_system_prompt()
    user = build_user_message(fs)
    failed: dict | None = None
    retried = False
    try:
        for attempt, (k, message) in enumerate([(key, user), (key + "|retry", None)]):
            if attempt == 1:
                if not live and cc._cache_get(k) is None:
                    break  # nothing cached for the retry and no network allowed
                retried = True
                message = user + "\n\nYour previous answer failed these checks: " + "; ".join(failed["errors"][:10])
            was_cached = cc._cache_get(k) is not None
            try:
                out = cc.call_json(system, message, BRIEFING_SCHEMA, model, k,
                                   prompt_version=BRIEFING_PROMPT_VERSION, cache_only=not live)
            except cc.CacheMiss:
                if attempt == 0:
                    return fallback("No cached Claude briefing for this clock; showing the rule-based summary.",
                                    False)
                break
            check = verify(out, fs, expected)
            if check["ok"]:
                return result("cache" if was_cached else "claude", out, check, retried, "")
            failed = check
            logger.warning("briefing %s failed verification (attempt %d): %s", pid, attempt + 1,
                           check["errors"][:5])
        if live and failed is not None:  # do not let a bad cached answer block later retries
            cc.cache_drop(key)
            cc.cache_drop(key + "|retry")
        return fallback("Claude output failed verification, so the rule-based summary is shown instead: "
                        + "; ".join(failed["errors"][:5]), retried, failed)
    except Exception as exc:  # noqa: BLE001  ClaudeError, network, anything: never break the page
        logger.warning("briefing %s: %s: %s", pid, type(exc).__name__, exc)
        return fallback(f"Claude briefing unavailable ({type(exc).__name__}); showing the rule-based summary.",
                        retried)


if __name__ == "__main__":
    import time

    _pid, _clock = "P-002", "2026-11-21T13:00"
    _fs = build_factsheet(_pid, _clock)
    print("facts:", len(_fs["facts"]), "| notes:", len(_fs["notes"]), "| messages:", len(_fs["messages"]))
    for _f in _fs["facts"][:5]:
        print(f"  {_f['id']} [{_f['type']}] {_f['text']}")
    print("expected_status:", _fs["expected_status"])
    get_briefing(_pid, _clock, live=False)  # warm
    _t = time.perf_counter()
    _b = get_briefing(_pid, _clock, live=False)
    _ms = (time.perf_counter() - _t) * 1000
    print(f"get_briefing(live=False): source={_b['source']} checks={_b['checks']['n_ok']}/"
          f"{_b['checks']['n_checked']} ({_ms:.0f} ms)")
    print("headline:", _b["briefing"]["headline"])
