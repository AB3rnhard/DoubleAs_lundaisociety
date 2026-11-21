"""JSON schemas for Claude outputs. Append-only: each owner adds their own.

Owner: shared (append-only). Built in prompt A4 / B4.
"""
import logging

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    print("ai.schemas: not implemented yet")


# ---- B4: scenario explainer (Teammate B) ----
EXPLAINER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["plans", "binding_constraint", "decision_needed"],
    "properties": {
        "plans": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["profile", "one_line", "tradeoffs", "signoffs", "main_risk", "evidence_ids"],
            "properties": {
                "profile": {"type": "string", "enum": ["safety_first", "balanced", "max_capacity"]},
                "one_line": {"type": "string"},
                "tradeoffs": {"type": "array", "items": {"type": "string"}},
                "signoffs": {"type": "array", "items": {"type": "string"}},
                "main_risk": {"type": "string"},
                "evidence_ids": {"type": "array", "items": {"type": "string"}}}}},
        "binding_constraint": {
            "type": "object", "additionalProperties": False,
            "required": ["resource", "explanation", "evidence_ids"],
            "properties": {
                "resource": {"type": "string", "enum": ["beds", "kits", "visits", "virtual_slots",
                                                        "primary_care_handover", "none"]},
                "explanation": {"type": "string"},
                "evidence_ids": {"type": "array", "items": {"type": "string"}}}},
        "decision_needed": {"type": "string"},
    },
}
