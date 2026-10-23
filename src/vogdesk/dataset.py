"""Issue dates, labels and feature tables for the pre-registered splits."""
from __future__ import annotations

import pandas as pd

from vogdesk.features import feature_table
from vogdesk.labels import night_labels
from vogdesk.sources import Sources, station_series

THRESHOLD_PPB = 75.0
CLIMATOLOGY = 0.059  # 2025 training base rate, frozen in PREREG
SPLITS = {
    "train": ("2025-01-01", "2025-12-31"),
    "dev": ("2026-01-01", "2026-06-30"),
    "heldout": ("2026-07-01", "2026-10-21"),
}


def split_dates(name: str) -> pd.DatetimeIndex:
    a, b = SPLITS[name]
    return pd.date_range(a, b, freq="D")


def labels(src: Sources, station: str = "pahala") -> pd.DataFrame:
    return night_labels(station_series(src, station), THRESHOLD_PPB)


def build(src: Sources, name: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Features and labels for the valid nights of a split, aligned on issue date."""
    dates = split_dates(name)
    lab = labels(src).reindex(dates)
    lab["valid"] = lab["valid"].fillna(False).astype(bool)
    lab["event"] = lab["event"].fillna(False).astype(bool)
    X = feature_table(src, dates)
    keep = lab.index[lab["valid"]]
    return X.loc[keep], lab.loc[keep]
