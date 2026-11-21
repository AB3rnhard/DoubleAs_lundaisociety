"""Verify that every AI sentence cites valid evidence IDs and matches the facts.

Owner: A (Teammate A). Built in prompt A4.

Public interface:
    verify(output, factsheet, expected_status=None) -> {"ok", "n_checked", "n_ok", "errors"}
    deterministic_briefing(factsheet) -> dict   rule-based briefing in the BRIEFING_SCHEMA shape

Schema-agnostic: evidence = every dict with "id" and "text" in any list of the factsheet (facts,
notes, messages, or other lists such as the explainer's P# facts). A claim is each dict in the output
that has an "evidence_ids" key, plus every plain string (headline, status, missing_information).
Checks: ids exist and are present; every number in a claim equals a number in one of its cited items
(plain strings: a number anywhere in the factsheet); doses must appear verbatim in the cited text;
status equals the rule-based status when given.
"""
from __future__ import annotations

import logging
import math
import re

from clinical.trend import is_symptom_message

logger = logging.getLogger(__name__)

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?)?")
_TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_ID = re.compile(r"\b(?:F\d+|[NMPIA]-\d+)\b")
_ICD = re.compile(r"\b[A-Z]\d{2}(?:\.\d+)?\b")
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?(?![A-Za-z0-9_])")
_DOSE = re.compile(r"\d+(?:\.\d+)?\s?(?:mg|g|mcg|µg|ml|L/min|IU|units?)\b", re.IGNORECASE)


def _strip(text: str) -> str:
    """Text with ISO dates, clock times, evidence ids and ICD-10 codes blanked out."""
    for pattern in (_ISO, _TIME, _ID, _ICD):
        text = pattern.sub(" ", text)
    return text


def extract_numbers(text: str) -> set[float]:
    """Numbers in `text`, ignoring ids, times, dates, ICD-10 codes and digits glued to letters."""
    return {float(m) for m in _NUMBER.findall(_strip(text))}


def extract_doses(text: str) -> set[str]:
    """Dose expressions, normalised (lower case, no space between number and unit)."""
    return {re.sub(r"\s+", "", m.lower()) for m in _DOSE.findall(text)}


def _has(numbers: set[float], x: float) -> bool:
    return any(math.isclose(x, n, abs_tol=1e-9) for n in numbers)


def _evidence(factsheet: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for value in factsheet.values():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("text"), str):
                    out[item["id"]] = item["text"]
    return out


def _all_strings(obj) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _all_strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _all_strings(v)]
    return []


def _claims(obj, path: str = ""):
    """Yield (path, claim) where claim is a dict with evidence_ids or a plain string."""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        if "evidence_ids" in obj and path:
            yield path, obj
            return
        for k, v in obj.items():
            yield from _claims(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _claims(v, f"{path}[{i}]")


def _fmt_number(x: float) -> str:
    return str(int(x)) if x == int(x) else repr(x)


def verify(output: dict, factsheet: dict, expected_status: str | None = None) -> dict:
    """Check an AI output against the factsheet. Never raises for a malformed output: it reports errors."""
    evidence = _evidence(factsheet)
    sheet_text = " ".join(_all_strings(factsheet))
    sheet_numbers = extract_numbers(sheet_text)
    sheet_doses = extract_doses(sheet_text)
    errors: list[str] = []
    n_checked = n_ok = 0

    if not isinstance(output, dict):
        return {"ok": False, "n_checked": 0, "n_ok": 0, "errors": ["output: not a JSON object"]}

    for path, claim in _claims(output):
        found: list[str] = []
        if isinstance(claim, str):
            for n in sorted(extract_numbers(claim)):
                if not _has(sheet_numbers, n):
                    found.append(f"{path}: number {_fmt_number(n)} not in the factsheet")
            for d in sorted(extract_doses(claim) - sheet_doses):
                found.append(f"{path}: dose {d} not in the factsheet")
            if path == "status" and expected_status is not None and claim != expected_status:
                found.append(f"status: expected {expected_status}, got {claim}")
        else:
            ids = claim.get("evidence_ids")
            if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
                found.append(f"{path}: no evidence_ids")
                ids = []
            unknown = [i for i in ids if i not in evidence]
            for i in unknown:
                found.append(f"{path}: unknown evidence id {i}")
            cited = [i for i in ids if i in evidence]
            cited_numbers = set().union(*(extract_numbers(evidence[i]) for i in cited)) if cited else set()
            cited_doses = set().union(*(extract_doses(evidence[i]) for i in cited)) if cited else set()
            label = ", ".join(cited)
            for key, value in claim.items():
                if not cited or key == "evidence_ids" or not isinstance(value, str):
                    continue  # nothing to compare against: the id errors above already fail the claim
                where = path if key == "text" else f"{path}.{key}"
                for n in sorted(extract_numbers(value)):
                    if not _has(cited_numbers, n):
                        found.append(f"{where}: number {_fmt_number(n)} not in {label}")
                for d in sorted(extract_doses(value) - cited_doses):
                    found.append(f"{where}: dose {d} not in {label}")
        n_checked += 1
        if found:
            errors.extend(found)
        else:
            n_ok += 1

    if expected_status is not None and "status" not in output:
        errors.append("status: missing")
    result = {"ok": not errors, "n_checked": n_checked, "n_ok": n_ok, "errors": errors}
    if errors:
        logger.info("verification failed: %d error(s), first: %s", len(errors), errors[0])
    return result


def deterministic_briefing(factsheet: dict) -> dict:
    """Rule-based briefing built only from the factsheet; passes verify() by construction."""
    facts = factsheet["facts"]
    reasons = factsheet["reasons"]
    by_type: dict[str, list[dict]] = {}
    for f in facts:
        by_type.setdefault(f["type"], []).append(f)

    summary = [{"text": f["text"], "evidence_ids": [f["id"]]}
               for f in by_type.get("queue", []) + by_type.get("news2", [])[:1]]
    changes = [{"parameter": f["param"], "baseline": f["baseline_text"], "current": f["value_text"],
                "evidence_ids": [f["id"]]} for f in by_type.get("deviation", [])]
    plan_rules = [{"rule": f["text"], "state": "triggered" if f["triggered"] else "not_triggered",
                   "evidence_ids": [f["id"]]} for f in by_type.get("plan", []) if f.get("triggered") is not None]

    history: list[dict] = []
    if factsheet["notes"]:
        note = max(factsheet["notes"], key=lambda n: (n["ts"], n["id"]))
        history.append({"text": note["text"], "evidence_ids": [note["id"]]})
    history += [{"text": m["text"], "evidence_ids": [m["id"]]} for m in factsheet["messages"]
                if is_symptom_message(m["text"])]

    return {
        "headline": "Rule-based summary: " + (", ".join(reasons) if reasons else "no active flags"),
        "status": factsheet["expected_status"],
        "summary_points": summary,
        "changes_vs_baseline": changes,
        "plan_rules": plan_rules,
        "relevant_history": history,
        "suggested_actions": [],
        "missing_information": [],
    }


if __name__ == "__main__":
    from ai.factsheet import build_factsheet

    _fs = build_factsheet("P-002", "2026-11-21T13:00")
    _det = deterministic_briefing(_fs)
    print("headline:", _det["headline"])
    print("verify(deterministic):", verify(_det, _fs, _fs["expected_status"]))
    _bad = {**_det, "summary_points": [{"text": "Temp 41.3", "evidence_ids": ["F999"]}]}
    print("verify(bad):", verify(_bad, _fs, _fs["expected_status"])["errors"])
