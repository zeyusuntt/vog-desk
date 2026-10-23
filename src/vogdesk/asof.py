"""Information cutoff rules, frozen in docs/PREREG.md.

Every source row carries `available_utc`, the UTC time it became public. A forecast issued at
time T may only use rows with available_utc <= T. The lags below turn observation or run times
into publication times. The Zetaris as-of views (infra/zetaris/) must apply the same rules.
"""
from __future__ import annotations

import pandas as pd

HST = pd.Timedelta(hours=10)
# AirNow posts an hourly file ~25-30 min after the hour ends (checked 23 Oct 2026); round up.
HOURLY_LAG = pd.Timedelta(hours=2)
# The 18Z GFS run of day D is complete well within 6 h; the 00Z run of D+1 is never used.
GFS_LAG = pd.Timedelta(hours=6)
# Radiosonde launch time to archive; the 00Z launch (14:00 HST) is usable at the 18:00 HST issue.
RAOB_LAG = pd.Timedelta(hours=3)


def issue_time_utc(issue_date_hst: pd.Timestamp) -> pd.Timestamp:
    """18:00 HST on the issue date, as UTC."""
    d = pd.Timestamp(issue_date_hst).normalize()
    if d.tzinfo is not None:
        d = d.tz_localize(None)
    return (d + pd.Timedelta(hours=18) + HST).tz_localize("UTC")


def gfs_run_for(issue_date_hst: pd.Timestamp) -> pd.Timestamp:
    """The only GFS run a forecast for issue date D may use: 18Z on day D (UTC)."""
    d = pd.Timestamp(issue_date_hst).normalize()
    if d.tzinfo is not None:
        d = d.tz_localize(None)
    return (d + pd.Timedelta(hours=18)).tz_localize("UTC")


def visible(df: pd.DataFrame, as_of_utc: pd.Timestamp, col: str = "available_utc") -> pd.DataFrame:
    return df[df[col] <= as_of_utc]
