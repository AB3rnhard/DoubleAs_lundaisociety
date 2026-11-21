"""TEMPORARY fallback mirroring clinical.news2 (A1). Remove after the 12:45 merge once tests pass
with A's version.

Interface mirrored from A1:
  score(rr, spo2, on_oxygen, sbp, hr, consciousness, temp_c, scale) -> (total, max_single, components)
  score_df(vitals, plans) -> vitals + int columns "news2" and "news2_max_single"
RCP NEWS2 (2017) tables. `resolve_score_df()` picks clinical.news2.score_df when it exists.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def _rr(v: float) -> int:
    return 3 if v <= 8 else 1 if v <= 11 else 0 if v <= 20 else 2 if v <= 24 else 3


def _spo2(v: float, on_oxygen: int, scale: int) -> int:
    if scale == 2:
        if v <= 83:
            return 3
        if v <= 85:
            return 2
        if v <= 87:
            return 1
        if v <= 92 or not on_oxygen:
            return 0
        return 1 if v <= 94 else 2 if v <= 96 else 3
    return 3 if v <= 91 else 2 if v <= 93 else 1 if v <= 95 else 0


def _sbp(v: float) -> int:
    return 3 if v <= 90 else 2 if v <= 100 else 1 if v <= 110 else 0 if v <= 219 else 3


def _hr(v: float) -> int:
    return 3 if v <= 40 else 1 if v <= 50 else 0 if v <= 90 else 1 if v <= 110 else 2 if v <= 130 else 3


def _temp(v: float) -> int:
    return 3 if v <= 35.0 else 1 if v <= 36.0 else 0 if v <= 38.0 else 1 if v <= 39.0 else 2


def score(rr, spo2, on_oxygen, sbp, hr, consciousness, temp_c, scale=1):
    """Return (total, max_single, components dict)."""
    comp = {
        "rr": _rr(rr),
        "spo2": _spo2(spo2, int(on_oxygen), int(scale)),
        "air_oxygen": 2 if int(on_oxygen) else 0,
        "sbp": _sbp(sbp),
        "hr": _hr(hr),
        "consciousness": 0 if str(consciousness).strip().upper() == "A" else 3,
        "temp": _temp(temp_c),
    }
    return sum(comp.values()), max(comp.values()), comp


def score_df(vitals: pd.DataFrame, plans: pd.DataFrame) -> pd.DataFrame:
    """Add int columns news2 / news2_max_single using each patient's plan spo2_scale (default 1)."""
    scales = plans.set_index("patient_id")["spo2_scale"].to_dict()
    out = vitals.copy()
    totals, maxes = [], []
    for r in out.itertuples(index=False):
        t, m, _ = score(r.rr, r.spo2, r.on_oxygen, r.sbp, r.hr, r.consciousness, r.temp_c,
                        scales.get(r.patient_id, 1))
        totals.append(t)
        maxes.append(m)
    out["news2"] = pd.Series(totals, index=out.index, dtype=int)
    out["news2_max_single"] = pd.Series(maxes, index=out.index, dtype=int)
    return out


def resolve_score_df():
    """clinical.news2.score_df if available (A's real one), else this fallback."""
    try:
        from clinical.news2 import score_df as real
        logger.info("ops: using clinical.news2.score_df")
        return real
    except ImportError:
        logger.info("ops: clinical.news2.score_df missing, using ops._news2 fallback")
        return score_df


if __name__ == "__main__":
    print(score(24, 93, 0, 100, 111, "A", 38.5, 1))
    print("resolved:", resolve_score_df().__module__)
