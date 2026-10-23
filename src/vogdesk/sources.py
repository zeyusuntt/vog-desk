"""Canonical source tables with publication times, built from data/derived and data/raw.

Each table has an `available_utc` column set by the rules in asof.py, so features can be cut at
any issue time. Hourly SO2 is one series per station: AQS validated where AQS has the hour,
AirNow preliminary otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from vogdesk.asof import GFS_LAG, HOURLY_LAG, RAOB_LAG

ROOT = Path(__file__).resolve().parents[2]
DERIVED = ROOT / "data" / "derived"
RAW = ROOT / "data" / "raw"

SITES = {"pahala": "150012016", "hvnp": "150010005", "ocean_view": "150012020"}
INVERSION_SEARCH_M = (500.0, 4000.0)


@dataclass
class Sources:
    so2: pd.DataFrame        # site_id, time_utc (hour-beginning), so2_ppb, source, preliminary, available_utc
    episodes: pd.DataFrame   # episodes_master: start/end and when each became known
    gfs: pd.DataFrame        # long GFS point values with run_init_utc and available_utc
    raob: pd.DataFrame       # one row per launch: valid_utc, inversion_base_m, available_utc


def so2_hourly(aqs: pd.DataFrame, airnow: pd.DataFrame) -> pd.DataFrame:
    a = aqs.dropna(subset=["so2_ppb"])
    a = a.sort_values("poc").drop_duplicates(["site_id", "time_utc"])
    a = a.assign(source="aqs", preliminary=False)[["site_id", "time_utc", "so2_ppb", "source", "preliminary"]]
    n = airnow[airnow.parameter == "SO2"].dropna(subset=["value"])
    n = n.drop_duplicates(["site_id", "time_utc"]).rename(columns={"value": "so2_ppb"})
    n = n.assign(source="airnow", preliminary=True)[["site_id", "time_utc", "so2_ppb", "source", "preliminary"]]
    have = pd.MultiIndex.from_frame(a[["site_id", "time_utc"]])
    n = n[~pd.MultiIndex.from_frame(n[["site_id", "time_utc"]]).isin(have)]
    out = pd.concat([a, n], ignore_index=True).sort_values(["site_id", "time_utc"]).reset_index(drop=True)
    out["time_utc"] = out["time_utc"].astype("datetime64[us, UTC]")
    out["available_utc"] = out["time_utc"] + HOURLY_LAG
    return out


def gfs_long(df: pd.DataFrame) -> pd.DataFrame:
    g = df[df.status == 200].copy()
    g["run_init_utc"] = pd.to_datetime(g["run_init_utc"], utc=True)
    g["valid_utc"] = pd.to_datetime(g["valid_utc"], utc=True)
    g["available_utc"] = g["run_init_utc"] + GFS_LAG
    g = g.drop_duplicates(["run_init_utc", "fxx", "var", "level", "point"], keep="last")
    return g[["issue_date_hst", "run_init_utc", "fxx", "valid_utc", "var", "level", "point", "value", "available_utc"]]


def inversion_base_m(levels: pd.DataFrame) -> float:
    """Height of the lowest level, between 500 and 4,000 m, above which temperature increases."""
    lv = levels.dropna(subset=["height_m", "tmpc"]).sort_values("height_m")
    lv = lv[(lv.height_m >= INVERSION_SEARCH_M[0]) & (lv.height_m <= INVERSION_SEARCH_M[1])]
    h, t = lv.height_m.to_numpy(), lv.tmpc.to_numpy()
    if len(h) < 2:
        return np.nan
    up = np.nonzero(np.diff(t) > 0)[0]
    return float(h[up[0]]) if len(up) else INVERSION_SEARCH_M[1]


def raob_launches(levels: pd.DataFrame) -> pd.DataFrame:
    lv = levels.copy()
    for c in ("height_m", "tmpc", "pressure_mb"):
        lv[c] = pd.to_numeric(lv[c], errors="coerce")
    lv["valid_utc"] = pd.to_datetime(lv["validUTC"], utc=True)
    rows = [{"valid_utc": t, "inversion_base_m": inversion_base_m(g)} for t, g in lv.groupby("valid_utc")]
    out = pd.DataFrame(rows)
    out["available_utc"] = out["valid_utc"] + RAOB_LAG
    return out


def load(derived: Path = DERIVED, raw: Path = RAW) -> Sources:
    aqs = pd.read_parquet(derived / "aqs_hawaii_so2.parquet", columns=["site_id", "poc", "time_utc", "so2_ppb"])
    airnow = pd.read_parquet(derived / "airnow_hawaii.parquet", columns=["site_id", "time_utc", "parameter", "value"])
    episodes = pd.read_parquet(derived / "episodes_master.parquet")
    gfs_files = sorted((raw / "gfs").glob("gfs_points_*.parquet"))
    gfs = gfs_long(pd.concat([pd.read_parquet(p) for p in gfs_files], ignore_index=True))
    raob_files = sorted((raw / "raob").glob("PITO_*.csv"))
    raob = raob_launches(pd.concat([pd.read_csv(p, low_memory=False) for p in raob_files], ignore_index=True))
    raob = raob.drop_duplicates("valid_utc", keep="last").sort_values("valid_utc").reset_index(drop=True)
    return Sources(so2=so2_hourly(aqs, airnow), episodes=episodes, gfs=gfs, raob=raob)


def station_series(src: Sources, site: str) -> pd.Series:
    """Hourly SO2 for one station, indexed by hour-beginning UTC (for labels)."""
    s = src.so2[src.so2.site_id == SITES.get(site, site)]
    return s.set_index("time_utc")["so2_ppb"].sort_index()
