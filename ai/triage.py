"""Claude triage of patient messages, with the keyword rules as the fallback.

Owner: A (Teammate A). Built in prompt A5.

Public interface (defined in prompt A5, not listed in CLAUDE.md Interfaces):
    classify(message, live=False) -> dict      verified triage (claude / cache) or rule-based
    check_triage(output, message) -> {"ok", "errors"}   the triage "verifier"
    rule_based_triage(message) -> dict         schema-shaped, from the keyword list in clinical.trend
    is_symptom(triage) -> bool                 does this message count as a symptom message?
    cached_symptom(message_id, text) -> bool | None     cache only, never a network call
    inbox(clock, live=False) -> list[dict]     messages with ts <= clock, newest first, each with its triage

A message is {"message_id", "patient_id", "ts", "sender", "text"} (one row of core.loader.load_messages()).
live=False never makes a network call: it reads the cache and otherwise falls back to the keyword rules.
A triage output that failed check_triage is never returned. Import rule: this module may import
clinical.trend at module level; clinical.trend imports this module only inside a function.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from functools import lru_cache
from pathlib import Path

import pandas as pd

from ai import claude_client as cc
from ai.schemas import TRIAGE_PROMPT_VERSION, TRIAGE_SCHEMA
from ai.verifier import extract_doses, extract_numbers
from clinical.artefact import to_iso
from clinical.trend import SYMPTOM_PATTERN, keyword_symptom
from core.loader import load_messages

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
CATEGORIES = TRIAGE_SCHEMA["properties"]["category"]["enum"]
URGENCIES = TRIAGE_SCHEMA["properties"]["urgency"]["enum"]
STEM = 4  # letters two words must share to count as the same word (shivery / shivering)


# ---------- prompt and cache key ----------
@lru_cache(maxsize=1)
def load_system_prompt(name: str = TRIAGE_PROMPT_VERSION) -> str:
    """Prompt file without its first line (the `<!-- version -->` marker)."""
    with open(PROMPT_DIR / f"{name}.md", encoding="utf-8") as f:
        return f.read().split("\n", 1)[1].strip()


def build_user_message(message: dict) -> str:
    """Sender and text only: no name, patient id or clinical data leaves the machine."""
    return f"Sender: {message['sender']}\nMessage: {message['text']}"


def _cache_key(message: dict) -> str:
    digest = hashlib.sha256(build_user_message(message).encode("utf-8")).hexdigest()[:12]
    return f"{TRIAGE_PROMPT_VERSION}|{message['message_id']}|{digest}"  # the model is deliberately not in the key


@lru_cache(maxsize=1)
def _senders() -> dict[str, str]:
    m = load_messages()
    return dict(zip(m["message_id"].astype(str), m["sender"].astype(str), strict=True))


def _cached_model(key: str) -> str:
    """Model recorded in a cache entry (the key does not contain it)."""
    try:
        with open(cc._cache_path(key), encoding="utf-8") as f:
            return str(json.load(f).get("model") or cc._default_model())
    except (OSError, ValueError):
        return cc._default_model()


# ---------- rules ----------
def is_symptom(triage: dict) -> bool:
    """A symptom message: clinical_symptom, or relative_concern that needs a clinician."""
    cat = triage.get("category")
    return cat == "clinical_symptom" or (cat == "relative_concern" and bool(triage.get("needs_clinician")))


def _words(text: str) -> list[str]:
    return [w for w in "".join(c if c.isalpha() else " " for c in text.lower()).split() if w]


def _term_in_text(term: str, text: str) -> bool:
    """Every word of the term is in the message, or shares a stem of STEM letters with one of its words."""
    lower, text_words = text.lower(), _words(text)
    for w in _words(term):
        if w in lower:
            continue
        if len(w) >= STEM and any(len(os.path.commonprefix([w, t])) >= STEM for t in text_words):
            continue
        return False
    return bool(_words(term))


def _num_in(numbers: set[float], x: float) -> bool:
    return any(math.isclose(x, n, abs_tol=1e-9) for n in numbers)


def check_triage(output: dict, message: dict) -> dict:
    """Checks on a triage output. Never raises; error strings name the field and stay short."""
    if not isinstance(output, dict):
        return {"ok": False, "errors": ["output: not a JSON object"]}
    errors: list[str] = []
    text = str(message["text"])
    category, urgency = output.get("category"), output.get("urgency")
    terms, needs, draft = output.get("symptom_terms"), output.get("needs_clinician"), output.get("draft_reply")

    if category not in CATEGORIES:
        errors.append(f"category: {category!r} is not one of the allowed values")
    if urgency not in URGENCIES:
        errors.append(f"urgency: {urgency!r} is not one of the allowed values")
    if not isinstance(needs, bool):
        errors.append("needs_clinician: must be true or false")
    if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
        errors.append("symptom_terms: must be a list of strings")
        terms = []
    for t in terms:
        if not _term_in_text(t, text):
            errors.append(f"symptom_terms: '{t[:30]}' is not in the message")
    if needs is False and (category == "clinical_symptom" or urgency in ("now", "within_1h")):
        errors.append("needs_clinician: must be true for clinical_symptom or urgency now / within_1h")

    if not isinstance(draft, str) or not draft.strip():
        errors.append("draft_reply: empty")
    else:
        for d in sorted(extract_doses(draft)):
            errors.append(f"draft_reply: contains a dose ({d})")
        allowed = extract_numbers(text)
        for n in sorted(extract_numbers(draft)):
            if not _num_in(allowed, n):
                errors.append(f"draft_reply: number {n:g} is not in the message")
        if needs is True and "nurse" not in draft.lower():
            errors.append("draft_reply: must say that a nurse will call or reply")
    return {"ok": not errors, "errors": errors}


def rule_based_triage(message: dict) -> dict:
    """Triage from the keyword list in clinical.trend (the fallback). Passes check_triage by construction."""
    text = str(message["text"])
    if keyword_symptom(text):
        terms = list(dict.fromkeys(m.group(0).lower() for m in SYMPTOM_PATTERN.finditer(text)))
        return {"category": "clinical_symptom", "urgency": "within_1h", "symptom_terms": terms,
                "needs_clinician": True,
                "draft_reply": "Thank you for telling us. A nurse will call you shortly."}
    return {"category": "other", "urgency": "today", "symptom_terms": [], "needs_clinician": False,
            "draft_reply": "Thank you for your message. A nurse will reply today."}


# ---------- cache-only lookup for the trend detector ----------
def cached_symptom(message_id: str, text: str) -> bool | None:
    """Symptom verdict from a cached, valid triage; None when there is none. Never creates a client."""
    message = {"message_id": message_id, "text": text, "sender": _senders().get(str(message_id), "patient")}
    key = _cache_key(message)
    for k in (key, key + "|retry"):
        out = cc._cache_get(k)
        if out is not None and check_triage(out, message)["ok"]:
            return is_symptom(out)
    return None


# ---------- classify ----------
def classify(message: dict, live: bool = False) -> dict:
    """Triage of one message. See the module docstring for the guarantees."""
    mid = str(message["message_id"])
    key = _cache_key(message)
    model = cc._default_model()

    def result(source: str, triage: dict, checks: dict, retried: bool, note: str, used_key: str = "") -> dict:
        logger.info("triage %s: source=%s retried=%s %s", mid, source, retried, note)
        shown_model = "" if source == "rule_based" else (_cached_model(used_key) if source == "cache" else model)
        return {"message_id": mid, "source": source, "triage": triage, "symptom": is_symptom(triage),
                "checks": {"ok": checks["ok"], "errors": list(checks["errors"])}, "model": shown_model,
                "prompt_version": TRIAGE_PROMPT_VERSION, "cache_key": key, "retried": retried, "note": note}

    def fallback(note: str, retried: bool, failed: dict | None = None) -> dict:
        rb = rule_based_triage(message)
        return result("rule_based", rb, failed or check_triage(rb, message), retried, note)

    system = load_system_prompt()
    user = build_user_message(message)
    failed: dict | None = None
    retried = False
    try:
        for attempt, k in enumerate((key, key + "|retry")):
            message_text = user
            if attempt == 1:
                if not live and cc._cache_get(k) is None:
                    break  # nothing cached for the retry and no network allowed
                retried = True
                message_text = user + "\n\nYour previous answer failed these checks: " + "; ".join(
                    failed["errors"][:10])
            was_cached = cc._cache_get(k) is not None
            try:
                out = cc.call_json(system, message_text, TRIAGE_SCHEMA, cache_key=k, max_tokens=2000,
                                   prompt_version=TRIAGE_PROMPT_VERSION, cache_only=not live)
            except cc.CacheMiss:
                if attempt == 0:
                    return fallback("No cached triage for this message; keyword rules used.", False)
                break
            check = check_triage(out, message)
            if check["ok"]:
                return result("cache" if was_cached else "claude", out, check, retried, "", k)
            failed = check
            logger.warning("triage %s failed its checks (attempt %d): %s", mid, attempt + 1, check["errors"][:5])
        if live and failed is not None:  # do not let a bad cached answer block later retries
            cc.cache_drop(key)
            cc.cache_drop(key + "|retry")
        return fallback("Claude triage failed its checks, keyword rules used instead: "
                        + "; ".join((failed or {"errors": []})["errors"][:5]), retried, failed)
    except Exception as exc:  # noqa: BLE001  ClaudeError, network, anything: never break the page
        logger.warning("triage %s: %s: %s", mid, type(exc).__name__, exc)
        return fallback(f"Claude triage unavailable ({type(exc).__name__}); keyword rules used.", retried)


def inbox(clock: str | pd.Timestamp, live: bool = False) -> list[dict]:
    """Every message with ts <= clock, newest first, each as {**message (ts ISO), "triage": classify(...)}."""
    cut = pd.Timestamp(clock)
    m = load_messages()
    m = m[m["ts"] <= cut].sort_values(["ts", "message_id"], ascending=False)
    items = []
    for r in m.itertuples(index=False):
        msg = {"message_id": str(r.message_id), "patient_id": str(r.patient_id), "ts": to_iso(r.ts),
               "sender": str(r.sender), "text": str(r.text)}
        items.append({**msg, "triage": classify(msg, live=live)})
    return items


if __name__ == "__main__":
    import time

    _t = time.perf_counter()
    _items = inbox("2026-11-21T21:00", live=False)
    _ms = (time.perf_counter() - _t) * 1000
    for _i in _items[::-1]:
        _r = _i["triage"]
        print(f"{_i['message_id']}  {_i['sender'][:24]:<24} {_r['triage']['category']:<18} "
              f"{_r['triage']['urgency']:<10} needs_clinician={_r['triage']['needs_clinician']!s:<5} "
              f"symptom={_r['symptom']!s:<5} source={_r['source']}")
    print(f"inbox('2026-11-21T21:00'), cache only: {len(_items)} messages in {_ms:.0f} ms")
