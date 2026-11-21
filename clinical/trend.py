"""Early-deterioration trend detection that fires before NEWS2 thresholds.

Owner: A (Teammate A). Built in prompt A2.

Personal baseline per patient (good readings from first_ts + 2 h up to first_ts + 26 h), robust
z-scores in the adverse direction afterwards, TREND flag (>= 2 deviating parameters on two
consecutive good readings) and SYMPTOM flag (>= 1 deviating parameter plus a symptom message in
the previous 2 h). Artefacts (signal_quality < 0.5) are ignored everywhere.
Which messages count as symptom messages: A5 triage first (ai.triage.cached_symptom, cache only, never a
network call), the keyword list below as the fallback when a message has no usable triage.

Public interface (defined in prompt A2, not listed in CLAUDE.md Interfaces):
    keyword_symptom(text) -> bool                 the single place the keyword list lives
    is_symptom_message(text, message_id=None) -> bool   triage first when message_id is given
    baselines(vitals) -> {pid: {"median", "robust_sd", "window"}}
    detect(vitals, messages) -> DataFrame, one row per good post-baseline reading
    deviations(pid, clock, vitals, messages) -> list[dict]
"""
from __future__ import annotations

import logging
import re

import pandas as pd

from clinical.artefact import is_artefact, to_iso

logger = logging.getLogger(__name__)

Z_THRESHOLD = 2.5
BASELINE_SKIP = pd.Timedelta(hours=2)
BASELINE_END = pd.Timedelta(hours=26)
SYMPTOM_WINDOW = pd.Timedelta(hours=2)

PARAMS = ("hr", "rr", "spo2", "temp_c", "sbp")
SIGN = {"hr": 1, "rr": 1, "temp_c": 1, "spo2": -1, "sbp": -1}  # +1: up is adverse, -1: down is adverse
FLOOR = {"hr": 3.0, "rr": 1.0, "spo2": 0.8, "temp_c": 0.15, "sbp": 5.0}
MAD_SCALE = 1.4826

SYMPTOM_PATTERN = re.compile(r"breath|breathless|shiver|fever|pain|confus|dizzy|pale", re.IGNORECASE)

DETECT_COLUMNS = ["patient_id", "ts", *[f"z_{p}" for p in PARAMS], "n_dev", "trend_flag",
                  "symptom_flag", "symptom_message_ids"]


def keyword_symptom(text: str) -> bool:
    """Keyword check for a symptom message (the fallback when there is no triage)."""
    return bool(SYMPTOM_PATTERN.search(str(text)))


def is_symptom_message(text: str, message_id: str | None = None) -> bool:
    """Symptom message? With a `message_id`: the cached Claude triage when there is a valid one, else keywords."""
    if message_id is not None:
        from ai.triage import cached_symptom  # lazy: ai.triage imports this module

        triaged = cached_symptom(str(message_id), str(text))
        if triaged is not None:
            return triaged
    return keyword_symptom(text)


def _norm(vitals: pd.DataFrame) -> pd.DataFrame:
    out = vitals.copy()
    out["ts"] = pd.to_datetime(out["ts"])
    return out


def _baselines_ts(v: pd.DataFrame) -> dict[str, dict]:
    """Baselines with Timestamp windows. `v` is a normalised vitals frame (any quality)."""
    out: dict[str, dict] = {}
    for pid, g in v.groupby("patient_id"):
        first = g["ts"].min()
        start, end = first + BASELINE_SKIP, first + BASELINE_END
        base = g[~is_artefact(g) & (g["ts"] >= start) & (g["ts"] < end)]
        if base.empty:
            logger.warning("No good baseline readings for %s: no trend detection", pid)
            continue
        median = base[list(PARAMS)].median()
        mad = (base[list(PARAMS)] - median).abs().median()
        sd = {p: float(max(MAD_SCALE * mad[p], FLOOR[p])) for p in PARAMS}
        out[str(pid)] = {"median": {p: float(median[p]) for p in PARAMS}, "robust_sd": sd,
                         "window": (start, end)}
    return out


def baselines(vitals: pd.DataFrame) -> dict[str, dict]:
    """Per patient: median and robust_sd per parameter plus the baseline window (ISO start, end)."""
    result = _baselines_ts(_norm(vitals))
    for b in result.values():
        b["window"] = (to_iso(b["window"][0]), to_iso(b["window"][1]))
    return result


def _z_table(vitals: pd.DataFrame) -> pd.DataFrame:
    """Good post-baseline readings with raw values, z_*, n_dev and trend_flag. ts stays a Timestamp."""
    v = _norm(vitals)
    base = _baselines_ts(v)
    good = v[~is_artefact(v)].sort_values(["patient_id", "ts"])
    parts = []
    for pid, g in good.groupby("patient_id"):
        b = base.get(str(pid))
        if b is None:
            continue
        post = g[g["ts"] >= b["window"][1]].copy()
        if post.empty:
            continue
        for p in PARAMS:
            post[f"z_{p}"] = SIGN[p] * (post[p] - b["median"][p]) / b["robust_sd"][p]
        z_cols = [f"z_{p}" for p in PARAMS]
        post["n_dev"] = (post[z_cols] >= Z_THRESHOLD).sum(axis=1).astype(int)
        multi = post["n_dev"] >= 2
        # The first post-baseline reading has no previous good reading, so it cannot flag.
        post["trend_flag"] = multi & multi.shift(1, fill_value=False)
        parts.append(post)
    if not parts:
        return pd.DataFrame(columns=["patient_id", "ts", *PARAMS, *[f"z_{p}" for p in PARAMS],
                                     "n_dev", "trend_flag"])
    return pd.concat(parts, ignore_index=True)


def _symptom_messages(messages: pd.DataFrame) -> dict[str, list[tuple[pd.Timestamp, str]]]:
    out: dict[str, list[tuple[pd.Timestamp, str]]] = {}
    if messages is None or messages.empty:
        return out
    m = messages.copy()
    m["ts"] = pd.to_datetime(m["ts"])
    for r in m.sort_values(["ts", "message_id"]).itertuples(index=False):
        if is_symptom_message(r.text, r.message_id):
            out.setdefault(str(r.patient_id), []).append((r.ts, str(r.message_id)))
    return out


def detect(vitals: pd.DataFrame, messages: pd.DataFrame) -> pd.DataFrame:
    """One row per good post-baseline reading of every patient. New frame; inputs are not mutated.

    Columns: patient_id, ts (ISO string), z_hr, z_rr, z_spo2, z_temp_c, z_sbp, n_dev, trend_flag,
    symptom_flag, symptom_message_ids (list). A result for a reading at t only uses rows with ts <= t.
    """
    table = _z_table(vitals)
    symptoms = _symptom_messages(messages)
    ids_per_row: list[list[str]] = []
    for r in table.itertuples(index=False):
        lo = r.ts - SYMPTOM_WINDOW
        ids_per_row.append([mid for mts, mid in symptoms.get(str(r.patient_id), []) if lo <= mts <= r.ts])
    out = table.copy()
    out["symptom_message_ids"] = pd.Series(ids_per_row, index=out.index, dtype=object)
    out["symptom_flag"] = (out["n_dev"] >= 1) & out["symptom_message_ids"].map(bool)
    out["ts"] = out["ts"].map(to_iso)
    return out[DETECT_COLUMNS].reset_index(drop=True)


def deviations(pid: str, clock: str | pd.Timestamp, vitals: pd.DataFrame,
               messages: pd.DataFrame | None = None) -> list[dict]:
    """Parameters beyond z >= 2.5 on the patient's latest good reading at or before `clock`.

    `messages` is accepted for interface stability; deviations depend on vitals only.
    Returns [{param, baseline_median, value, z (1 decimal), ts}], in PARAMS order.
    """
    v = _norm(vitals)
    v = v[v["ts"] <= pd.Timestamp(clock)]
    base = _baselines_ts(v)
    table = _z_table(v)
    table = table[table["patient_id"] == pid]
    if table.empty or pid not in base:
        return []
    row = table.sort_values("ts").iloc[-1]
    out = []
    for p in PARAMS:
        z = float(row[f"z_{p}"])
        if z >= Z_THRESHOLD:
            out.append({"param": p, "baseline_median": base[pid]["median"][p], "value": float(row[p]),
                        "z": round(z, 1), "ts": to_iso(row["ts"])})
    return out


if __name__ == "__main__":
    from core.loader import load_messages, load_vitals

    _vitals, _messages = load_vitals(), load_messages()
    _base = baselines(_vitals)["P-002"]
    print("P-002 baseline window:", _base["window"])
    print("  median:   ", {k: round(x, 2) for k, x in _base["median"].items()})
    print("  robust_sd:", {k: round(x, 2) for k, x in _base["robust_sd"].items()})
    _det = detect(_vitals, _messages)
    _p2 = _det[(_det["patient_id"] == "P-002") & (_det["ts"] >= "2026-11-21T10:00")]
    print("P-002 per stream hour:")
    for _, _r in _p2.iterrows():
        print(f"  {_r['ts'][11:]}  n_dev={_r['n_dev']}  TREND={bool(_r['trend_flag'])}  "
              f"SYMPTOM={bool(_r['symptom_flag'])} {_r['symptom_message_ids']}")
    print("Deviations at 13:00:", deviations("P-002", "2026-11-21T13:00", _vitals, _messages))
    print("TREND flags per patient:", {p: int(n) for p, n in
                                      _det[_det["patient_id"].str.startswith("P-")]
                                      .groupby("patient_id")["trend_flag"].sum().items() if n})
