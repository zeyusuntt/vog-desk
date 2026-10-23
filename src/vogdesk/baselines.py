"""Reference forecasts that use only information available at issue time (18:00 HST, day D).

climatology: constant probability = training base rate (frozen from the training split).
persistence: 1 if any hourly SO2 >= threshold in the 24 h before issue, else 0.
episode_rule: 1 if a fountaining episode is known (published) to have been active within the 24 h
  before issue, using HANS publication times or the conservative fallback in usgs_episodes.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HST = pd.Timedelta(hours=10)


def issue_time_utc(issue_date_hst: pd.Timestamp) -> pd.Timestamp:
    return (issue_date_hst + pd.Timedelta(hours=18) + HST).tz_localize("UTC")


def persistence(hourly: pd.Series, issue_dates: pd.DatetimeIndex, threshold: float) -> pd.Series:
    vals = []
    for d in issue_dates:
        t = issue_time_utc(d)
        w = hourly[(hourly.index >= t - pd.Timedelta(hours=24)) & (hourly.index < t)]
        vals.append(float((w >= threshold).any()) if len(w) else np.nan)
    return pd.Series(vals, index=issue_dates, name="persistence")


def episode_rule(episodes: pd.DataFrame, issue_dates: pd.DatetimeIndex) -> pd.Series:
    vals = []
    for d in issue_dates:
        t = issue_time_utc(d)
        known = episodes[episodes.start_known_utc <= t]
        end_seen = known.end_utc.where(known.end_known_utc <= t)  # end only counts once published
        active = known[(end_seen.isna()) | (end_seen >= t - pd.Timedelta(hours=24))]
        vals.append(float(len(active) > 0))
    return pd.Series(vals, index=issue_dates, name="episode_rule")


def scores(p: pd.Series, y: pd.Series, clim: float) -> dict:
    ok = p.notna() & y.notna()
    p, y = p[ok].astype(float), y[ok].astype(float)
    hit = int(((p >= 0.5) & (y == 1)).sum()); miss = int(((p < 0.5) & (y == 1)).sum())
    fa = int(((p >= 0.5) & (y == 0)).sum()); cn = int(((p < 0.5) & (y == 0)).sum())
    bs = float(((p - y) ** 2).mean()); bs_clim = float(((clim - y) ** 2).mean())
    return {"n": len(y), "events": int(y.sum()), "hits": hit, "misses": miss, "false_alarms": fa, "correct_neg": cn,
            "POD": hit / (hit + miss) if hit + miss else np.nan,
            "FAR_ratio": fa / (hit + fa) if hit + fa else np.nan,
            "CSI": hit / (hit + miss + fa) if hit + miss + fa else np.nan,
            "Brier": bs, "BSS_vs_clim": 1 - bs / bs_clim if bs_clim else np.nan}
