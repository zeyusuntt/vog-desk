"""Model inputs for one issue date, computed only from rows visible at the issue time.

The 12 inputs and their definitions are frozen in docs/PREREG.md ("Model"). Every function takes
the issue time T and starts by cutting its source at T with asof.visible, so a value that was
not public at 18:00 HST cannot reach a feature even if the archive holds it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from vogdesk.asof import gfs_run_for, issue_time_utc, visible
from vogdesk.sources import SITES, Sources

BEARING_SUMMIT_TO_PAHALA = 222.6  # degrees, direction the wind must blow toward
SECTOR_HALF_WIDTH = 35.0
CAP_HOURS_SINCE_END = 60 * 24
LEADS_ALL, LEADS_NIGHT = (10, 16, 22), (16, 22)
RAOB_MAX_AGE = pd.Timedelta(hours=18)

FEATURES = [
    "episode_active", "log_hours_since_end",
    "log_pahala_max_24h", "log_pahala_mean_6h", "log_hvnp_max_6h",
    "gfs925_along_summit", "gfs925_speed_summit", "gfs850_along_summit", "gfs925_sector_frac",
    "gfs10m_speed_pahala", "log_hpbl_pahala_night", "raob_inversion_base_m",
]


def usable_hours(src: Sources, site: str, t: pd.Timestamp, n_hours: int) -> pd.Series:
    """The n most recent hourly values published by t (hour-beginning t-2h back to t-(n+1)h)."""
    s = src.so2[src.so2.site_id == SITES[site]]
    s = visible(s, t)
    last = t - pd.Timedelta(hours=2)
    s = s[(s.time_utc <= last) & (s.time_utc > last - pd.Timedelta(hours=n_hours))]
    return s.set_index("time_utc")["so2_ppb"]


def episode_state(episodes: pd.DataFrame, t: pd.Timestamp) -> dict:
    known = episodes[episodes.start_known_utc <= t]
    ended = known.end_known_utc.notna() & (known.end_known_utc <= t)
    active = bool((~ended).any())
    ends = known.loc[ended, "end_utc"]
    if active:
        hours = 0.0
    elif len(ends):
        hours = min((t - ends.max()).total_seconds() / 3600, CAP_HOURS_SINCE_END)
    else:
        hours = float(CAP_HOURS_SINCE_END)
    return {"episode_active": float(active), "log_hours_since_end": float(np.log1p(hours))}


def wind_parts(u: float, v: float) -> tuple[float, float, float]:
    """(component toward Pahala, speed, direction the wind blows from in degrees)."""
    b = np.radians(BEARING_SUMMIT_TO_PAHALA)
    along = u * np.sin(b) + v * np.cos(b)
    return float(along), float(np.hypot(u, v)), float(np.degrees(np.arctan2(-u, -v)) % 360)


def gfs_features(gfs: pd.DataFrame, issue_date: pd.Timestamp, t: pd.Timestamp) -> dict:
    run = gfs_run_for(issue_date)
    g = visible(gfs, t)
    g = g[g.run_init_utc == run]
    nan = {k: np.nan for k in FEATURES if k.startswith(("gfs", "log_hpbl"))}
    if g.empty:
        return nan
    val = g.set_index(["fxx", "var", "level", "point"])["value"]

    def get(fxx, var, level, point):
        return val.get((fxx, var, level, point), np.nan)

    out = dict(nan)
    for level, key in (("925 mb", "gfs925"), ("850 mb", "gfs850")):
        parts = [wind_parts(get(f, "UGRD", level, "summit"), get(f, "VGRD", level, "summit")) for f in LEADS_ALL]
        out[f"{key}_along_summit"] = float(np.nanmean([p[0] for p in parts]))
        if key == "gfs925":
            out["gfs925_speed_summit"] = float(np.nanmean([p[1] for p in parts]))
            from_dir = BEARING_SUMMIT_TO_PAHALA - 180
            dist = [abs((p[2] - from_dir + 180) % 360 - 180) for p in parts if not np.isnan(p[2])]
            out["gfs925_sector_frac"] = float(np.mean([d <= SECTOR_HALF_WIDTH for d in dist])) if dist else np.nan
    sp = [np.hypot(get(f, "UGRD", "10 m above ground", "pahala"), get(f, "VGRD", "10 m above ground", "pahala"))
          for f in LEADS_NIGHT]
    out["gfs10m_speed_pahala"] = float(np.nanmean(sp))
    hpbl = [get(f, "HPBL", "surface", "pahala") for f in LEADS_NIGHT]
    out["log_hpbl_pahala_night"] = float(np.log(max(np.nanmin(hpbl), 10.0))) if not np.all(np.isnan(hpbl)) else np.nan
    return out


def raob_features(raob: pd.DataFrame, t: pd.Timestamp) -> dict:
    r = visible(raob, t)
    r = r[r.valid_utc >= t - RAOB_MAX_AGE].dropna(subset=["inversion_base_m"])
    return {"raob_inversion_base_m": float(r.sort_values("valid_utc").inversion_base_m.iloc[-1]) if len(r) else np.nan}


def _log_stat(s: pd.Series, how: str) -> float:
    if s.empty:
        return np.nan
    x = s.clip(lower=0)
    return float(np.log1p(x.max() if how == "max" else x.mean()))


def features_for(src: Sources, issue_date: pd.Timestamp) -> dict:
    d = pd.Timestamp(issue_date).normalize()
    t = issue_time_utc(d)
    out = {"issue_date_hst": d, "issue_utc": t}
    out |= episode_state(src.episodes, t)
    out["log_pahala_max_24h"] = _log_stat(usable_hours(src, "pahala", t, 24), "max")
    out["log_pahala_mean_6h"] = _log_stat(usable_hours(src, "pahala", t, 6), "mean")
    out["log_hvnp_max_6h"] = _log_stat(usable_hours(src, "hvnp", t, 6), "max")
    out |= gfs_features(src.gfs, d, t)
    out |= raob_features(src.raob, t)
    return out


def feature_table(src: Sources, issue_dates) -> pd.DataFrame:
    rows = [features_for(src, d) for d in issue_dates]
    return pd.DataFrame(rows).set_index("issue_date_hst")
