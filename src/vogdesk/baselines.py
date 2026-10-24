"""Reference forecasts that use only information available at issue time (18:00 HST, day D).

climatology: constant probability = training base rate (frozen from the training split).
persistence: 1 if any hourly SO2 >= threshold in the 24 usable hours before issue, else 0. Usable means
  published by issue: AirNow posts an hour about 30 min after it ends, so at 18:00 HST the last usable
  hour begins at 16:00 HST (PREREG: hour-beginning time + 2 h).
episode_rule: 1 if a fountaining episode is known (published) to have been active within the 24 h
  before issue, using HANS publication times or the conservative fallback in usgs_episodes.py.
recent_rate: event fraction of the last 30 valid nights verified by issue (forecast 7, PREREG change 1);
  falls back to climatology when fewer than 10 such nights exist.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from vogdesk.asof import HOURLY_LAG

HST = pd.Timedelta(hours=10)


def issue_time_utc(issue_date_hst: pd.Timestamp) -> pd.Timestamp:
    return (issue_date_hst + pd.Timedelta(hours=18) + HST).tz_localize("UTC")


def persistence(hourly: pd.Series, issue_dates: pd.DatetimeIndex, threshold: float) -> pd.Series:
    vals = []
    for d in issue_dates:
        last = issue_time_utc(d) - HOURLY_LAG  # hour-beginning of the latest published hour
        w = hourly[(hourly.index > last - pd.Timedelta(hours=24)) & (hourly.index <= last)]
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


def recent_rate(labels: pd.DataFrame, issue_dates: pd.DatetimeIndex, n_nights: int = 30,
                min_nights: int = 10, fallback: float = 0.059) -> pd.Series:
    """Event fraction over the last n valid nights verified before issue on each date.

    The night issued on D-1 closes at 12:00 HST on D, before the 18:00 HST issue, so it counts.
    The night issued on D does not. Invalid nights are skipped, never counted as negatives.
    """
    ok = labels[labels["valid"].astype(bool)].sort_index()
    nights, events = ok.index.values, ok["event"].astype(float).to_numpy()
    vals = []
    for d in issue_dates:
        last = (pd.Timestamp(d).normalize() - pd.Timedelta(days=1)).to_datetime64()
        stop = int(np.searchsorted(nights, last, side="right"))
        recent = events[max(0, stop - n_nights):stop]
        vals.append(float(recent.mean()) if len(recent) >= min_nights else float(fallback))
    return pd.Series(vals, index=issue_dates, name="recent_rate", dtype=float)


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
