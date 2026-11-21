"""Detect low-signal-quality vitals (artefacts) and suppress them from alerting with a re-measure action.

Owner: A (Teammate A). Built in prompt A2.

A reading with signal_quality < SIGNAL_QUALITY_MIN is an artefact. Artefacts are excluded from
NEWS2 alerting, the trend baseline, z-scores and plan-breach checks; the clinician is asked to
re-measure instead.

Public functions:
    is_artefact(df) -> pd.Series[bool]
    split(vitals) -> (good, artefacts)
    artefact_list(vitals) -> list[dict]
    to_iso(ts) -> "YYYY-MM-DDTHH:MM"   (shared helper for the clinical modules)
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

SIGNAL_QUALITY_MIN = 0.5


def to_iso(ts: str | pd.Timestamp) -> str:
    """ISO minute string used for every timestamp the clinical modules return."""
    return pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M")


def is_artefact(df: pd.DataFrame) -> pd.Series:
    """True for readings whose signal_quality is below SIGNAL_QUALITY_MIN (0.5 itself is good)."""
    return df["signal_quality"] < SIGNAL_QUALITY_MIN


def split(vitals: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (good, artefacts) as two new frames."""
    mask = is_artefact(vitals)
    return vitals[~mask].copy(), vitals[mask].copy()


def artefact_list(vitals: pd.DataFrame) -> list[dict]:
    """One dict per artefact reading, sorted by ts then patient_id."""
    _, bad = split(vitals)
    bad = bad.sort_values(["ts", "patient_id"])
    items = [
        {
            "patient_id": str(r.patient_id),
            "ts": to_iso(r.ts),
            "spo2": int(r.spo2),
            "hr": int(r.hr),
            "signal_quality": float(r.signal_quality),
            "action": "re-measure",
        }
        for r in bad.itertuples(index=False)
    ]
    logger.debug("artefact_list: %d artefact readings", len(items))
    return items


if __name__ == "__main__":
    from core.loader import load_vitals

    _items = artefact_list(load_vitals())
    print(f"{len(_items)} artefact reading(s) (signal_quality < {SIGNAL_QUALITY_MIN}):")
    for _item in _items:
        print(" ", _item)
