"""NEWS2 scoring (RCP 2017) with the patient's SpO2 scale (1 or 2).

Owner: A (Teammate A). Built in prompt A1.

Monitoring-plan thresholds (hr_high, sbp_low, ...) are NOT used here: they belong to
PLAN_BREACH in A2. The only plan field used is `spo2_scale`.

Public functions:
    score(rr, spo2, on_oxygen, sbp, hr, consciousness, temp_c, scale=1)
        -> (total, max_single, components)
    score_df(vitals, plans, scale_override=None) -> DataFrame with NEWS2 columns added.
        No clock filter and no signal-quality filter here (callers use core.loader.at_clock;
        the artefact filter is A2's job).
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

COMPONENTS = ("rr", "spo2", "oxygen", "sbp", "hr", "consciousness", "temp_c")
OUTPUT_COLUMNS = ["spo2_scale", "news2", "news2_max_single", *[f"n2_{c}" for c in COMPONENTS]]

# Each table is an ordered list of (upper_bound, points); the first bound with x <= bound wins,
# the last entry (bound None) catches everything above. No gaps between bands.
_RR = [(8, 3), (11, 1), (20, 0), (24, 2), (None, 3)]
_SPO2_SCALE1 = [(91, 3), (93, 2), (95, 1), (None, 0)]
_SPO2_SCALE2_AIR = [(83, 3), (85, 2), (87, 1), (None, 0)]
# Only used when on oxygen AND spo2 >= 93; below that the "air" table applies (88-92 scores 0).
_SPO2_SCALE2_O2 = [(94, 1), (96, 2), (None, 3)]
_SBP = [(90, 3), (100, 2), (110, 1), (219, 0), (None, 3)]
_HR = [(40, 3), (50, 1), (90, 0), (110, 1), (130, 2), (None, 3)]
_TEMP = [(35.0, 3), (36.0, 1), (38.0, 0), (39.0, 1), (None, 2)]
_CONSCIOUSNESS = {"A": 0, "C": 3, "V": 3, "P": 3, "U": 3}


def _band(x: float, table: list[tuple[float | None, int]]) -> int:
    for cut, points in table:
        if cut is None or x <= cut:
            return points
    raise AssertionError("unreachable: table must end with a catch-all band")  # pragma: no cover


def score(
    rr: float,
    spo2: float,
    on_oxygen: bool | int,
    sbp: float,
    hr: float,
    consciousness: str,
    temp_c: float,
    scale: int = 1,
) -> tuple[int, int, dict[str, int]]:
    """Return (total, max_single, components) for one set of observations."""
    if scale not in (1, 2):
        raise ValueError(f"SpO2 scale must be 1 or 2, got {scale!r}")
    level = str(consciousness).strip().upper()
    if level not in _CONSCIOUSNESS:
        raise ValueError(f"Unknown consciousness level {consciousness!r}; expected A/C/V/P/U")

    oxygen = bool(on_oxygen)
    if scale == 1:
        spo2_points = _band(spo2, _SPO2_SCALE1)
    elif oxygen and spo2 >= 93:
        spo2_points = _band(spo2, _SPO2_SCALE2_O2)
    else:
        spo2_points = _band(spo2, _SPO2_SCALE2_AIR)

    components = {
        "rr": _band(rr, _RR),
        "spo2": spo2_points,
        "oxygen": 2 if oxygen else 0,
        "sbp": _band(sbp, _SBP),
        "hr": _band(hr, _HR),
        "consciousness": _CONSCIOUSNESS[level],
        "temp_c": _band(temp_c, _TEMP),
    }
    return sum(components.values()), max(components.values()), components


def score_df(
    vitals: pd.DataFrame,
    plans: pd.DataFrame,
    scale_override: int | None = None,
) -> pd.DataFrame:
    """Score every vitals row. Returns a new frame (same index/order) with NEWS2 columns added.

    The SpO2 scale comes from the patient's plan (Scale 1 when a patient has no plan, with one
    warning per such patient). `scale_override` (1 or 2) forces one scale for every row.
    """
    if scale_override is not None and scale_override not in (1, 2):
        raise ValueError(f"scale_override must be 1, 2 or None, got {scale_override!r}")

    out = vitals.copy()
    if out.empty:
        for col in OUTPUT_COLUMNS:
            out[col] = pd.Series(dtype="int64")
        return out

    if scale_override is not None:
        out["spo2_scale"] = scale_override
    else:
        plan_scale = plans.drop_duplicates("patient_id", keep="last").set_index("patient_id")["spo2_scale"]
        scales = out["patient_id"].map(plan_scale)
        for pid in sorted(out.loc[scales.isna(), "patient_id"].unique()):
            logger.warning("No monitoring plan for %s: using SpO2 Scale 1", pid)
        out["spo2_scale"] = scales.fillna(1).astype(int)

    rows = [
        score(r.rr, r.spo2, r.on_oxygen, r.sbp, r.hr, r.consciousness, r.temp_c, int(r.spo2_scale))
        for r in out.itertuples(index=False)
    ]
    out["news2"] = [total for total, _, _ in rows]
    out["news2_max_single"] = [mx for _, mx, _ in rows]
    for comp in COMPONENTS:
        out[f"n2_{comp}"] = [parts[comp] for _, _, parts in rows]
    for col in OUTPUT_COLUMNS:
        out[col] = out[col].astype(int)
    return out


if __name__ == "__main__":
    from core.loader import load_monitoring_plans, load_vitals

    _vitals = load_vitals()
    _plans = load_monitoring_plans()
    _aware = score_df(_vitals, _plans)
    _naive = score_df(_vitals, _plans, scale_override=1)

    print("P-002 NEWS2 per stream hour:")
    _p2 = _aware[(_aware["patient_id"] == "P-002") & (_aware["phase"] == "stream")]
    for _, _row in _p2.iterrows():
        print(f"  {_row['ts']:%H:%M}: {_row['news2']}")

    def _alerts(df: pd.DataFrame) -> int:
        vw = df[df["patient_id"].str.startswith("P-")]
        return int(((vw["news2"] >= 5) | (vw["news2_max_single"] == 3)).sum())

    print(f"Virtual-ward alert rows, plan-aware scoring: {_alerts(_aware)}")
    print(f"Virtual-ward alert rows, scale_override=1:   {_alerts(_naive)}")
