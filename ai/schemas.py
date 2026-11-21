"""JSON schemas for Claude outputs. Append-only: each owner adds their own.

Owner: shared (append-only). Built in prompt A4 / B4.
Every object has additionalProperties false; no minimum / maximum / minLength / maxLength
(not supported by structured outputs).
"""
import logging

logger = logging.getLogger(__name__)

# ---------- A4: clinical briefing ----------
BRIEFING_PROMPT_VERSION = "briefing_v1"

_IDS = {"type": "array", "items": {"type": "string"}}

BRIEFING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "status", "summary_points", "changes_vs_baseline", "plan_rules",
                 "relevant_history", "suggested_actions", "missing_information"],
    "properties": {
        "headline": {"type": "string"},
        "status": {"type": "string", "enum": ["needs_review_now", "review_today", "routine"]},
        "summary_points": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "evidence_ids"],
            "properties": {"text": {"type": "string"}, "evidence_ids": _IDS}}},
        "changes_vs_baseline": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["parameter", "baseline", "current", "evidence_ids"],
            "properties": {
                "parameter": {"type": "string",
                              "enum": ["hr", "spo2", "rr", "sbp", "temp_c", "consciousness"]},
                "baseline": {"type": "string"}, "current": {"type": "string"}, "evidence_ids": _IDS}}},
        "plan_rules": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["rule", "state", "evidence_ids"],
            "properties": {"rule": {"type": "string"},
                           "state": {"type": "string", "enum": ["triggered", "approaching", "not_triggered"]},
                           "evidence_ids": _IDS}}},
        "relevant_history": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "evidence_ids"],
            "properties": {"text": {"type": "string"}, "evidence_ids": _IDS}}},
        "suggested_actions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["action", "rationale", "evidence_ids"],
            "properties": {"action": {"type": "string"}, "rationale": {"type": "string"},
                           "evidence_ids": _IDS}}},
        "missing_information": {"type": "array", "items": {"type": "string"}},
    },
}

# ---------- A5: message triage ----------
TRIAGE_PROMPT_VERSION = "triage_v1"

TRIAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["category", "urgency", "symptom_terms", "needs_clinician", "draft_reply"],
    "properties": {
        "category": {"type": "string", "enum": ["clinical_symptom", "device_issue", "medication_question",
                                                "logistics", "discharge_question", "relative_concern",
                                                "other"]},
        "urgency": {"type": "string", "enum": ["now", "within_1h", "today", "none"]},
        "symptom_terms": {"type": "array", "items": {"type": "string"}},
        "needs_clinician": {"type": "boolean"},
        "draft_reply": {"type": "string"},
    },
}


if __name__ == "__main__":
    print("schemas:", [k for k in globals() if k.endswith("_SCHEMA")])
