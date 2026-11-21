"""Shared data loader: one pure function per file in data/, plus at_clock().

Owner: shared. No Streamlit import and no caching here; pages wrap these with st.cache_data.
Never read data/ files directly anywhere else.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _csv(name: str, datetime_cols: tuple[str, ...] = ()) -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / name, encoding="utf-8")
    for col in datetime_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    logger.debug("loaded %s: %d rows", name, len(df))
    return df


def _json(name: str) -> dict:
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def load_patients() -> pd.DataFrame:
    return _csv("patients.csv", ("admitted_at", "virtual_ward_start", "planned_discharge_at"))


def load_vitals() -> pd.DataFrame:
    return _csv("vitals.csv", ("ts",))


def load_monitoring_plans() -> pd.DataFrame:
    return _csv("monitoring_plans.csv", ("set_at",))


def load_notes() -> pd.DataFrame:
    with open(DATA_DIR / "notes.jsonl", encoding="utf-8") as f:
        df = pd.DataFrame([json.loads(line) for line in f if line.strip()])
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    logger.debug("loaded notes.jsonl: %d rows", len(df))
    return df


def load_messages() -> pd.DataFrame:
    return _csv("messages.csv", ("ts",))


def load_resources() -> dict:
    return _json("resources.json")


def load_admissions() -> pd.DataFrame:
    # eta is left as an ISO string: it is compared against the ISO clock by the ops code.
    return _csv("admissions_incoming.csv")


def load_demand_forecast() -> pd.DataFrame:
    return _csv("demand_forecast.csv")


def load_decisions_template() -> pd.DataFrame:
    return _csv("decisions_template.csv")


def load_expected_results() -> dict:
    return _json("expected_results.json")


def load_all() -> dict[str, object]:
    return {
        "patients": load_patients(),
        "vitals": load_vitals(),
        "monitoring_plans": load_monitoring_plans(),
        "notes": load_notes(),
        "messages": load_messages(),
        "resources": load_resources(),
        "admissions": load_admissions(),
        "demand_forecast": load_demand_forecast(),
        "decisions_template": load_decisions_template(),
        "expected_results": load_expected_results(),
    }


def at_clock(df: pd.DataFrame, clock: str | datetime | pd.Timestamp, col: str = "ts") -> pd.DataFrame:
    """Rows with df[col] <= clock. `clock` may be an ISO string or a datetime. Returns a new DataFrame."""
    cutoff = pd.Timestamp(clock)
    return df[df[col] <= cutoff].copy()


if __name__ == "__main__":
    for key, value in load_all().items():
        size = len(value)
        print(f"{key}: {size} {'rows' if isinstance(value, pd.DataFrame) else 'keys'}")
