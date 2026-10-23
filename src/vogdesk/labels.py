"""Labels: night-window SO2 events at a station, with validity rules fixed before modelling.

Issue time: 18:00 HST on day D. Window: 18:00 HST day D to 12:00 HST day D+1 (18 hourly values,
hour-beginning stamps 18:00 ... 11:00 HST). Event: any valid hourly SO2 >= threshold.
Validity: positive if any hour meets the threshold; otherwise negative only if at least 14 of 18
hours are present. Missing windows are excluded, never counted as negatives.
"""
from __future__ import annotations

import pandas as pd

HST = pd.Timedelta(hours=10)
MIN_HOURS_FOR_NEGATIVE = 14


def hourly_series(df: pd.DataFrame, site_id: str, value_col: str) -> pd.Series:
    s = df[df.site_id == site_id].set_index("time_utc")[value_col].sort_index()
    return s[~s.index.duplicated(keep="first")]


def night_labels(hourly: pd.Series, threshold_ppb: float) -> pd.DataFrame:
    local = hourly.copy()
    local.index = local.index.tz_convert(None) - HST  # naive HST, hour-beginning
    shifted = local.copy()
    shifted.index = shifted.index - pd.Timedelta(hours=18)  # 18:00 day D -> 00:00 day D
    in_window = shifted.index.hour < 18
    w = shifted[in_window]
    g = w.groupby(w.index.normalize())
    out = pd.DataFrame({"n_hours": g.count(), "max_ppb": g.max()})
    out["event"] = (out["max_ppb"] >= threshold_ppb)
    out["valid"] = out["event"] | (out["n_hours"] >= MIN_HOURS_FOR_NEGATIVE)
    out.index.name = "issue_date_hst"
    return out
