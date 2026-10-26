"""The desk's data access: fixed, as-of queries against the pahala_exposure semantic layer.

Two backends answer the same five questions with the same column names. `ZetarisBackend` sends SQL
through `run_sql`, which cuts every time-stamped table at the issue time (the only way an
agent reads data in the judged configuration). `LocalBackend` answers from the Parquet files through
the same as-of rules in Python, for SAMPLE_MODE and for tests without Zetaris. A test checks that the
two agree on the same night.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from vogdesk.asof import gfs_run_for, visible
from vogdesk.sources import SITES, Sources

PAHALA, HVNP, OCEAN_VIEW = SITES["pahala"], SITES["hvnp"], SITES["ocean_view"]


@dataclass
class Rows:
    frame: pd.DataFrame
    source: str
    sql: str | None
    query_hash: str
    rows_hash: str
    elapsed_ms: int = 0

    @property
    def available_utc(self) -> pd.Timestamp | None:
        if "available_utc" in self.frame and len(self.frame):
            return pd.to_datetime(self.frame["available_utc"], utc=True).max()
        return None


def _hash_frame(df: pd.DataFrame) -> str:
    return hashlib.sha256(df.to_json(orient="records", date_format="iso", default_handler=str).encode()).hexdigest()[:16]


def _rows(df: pd.DataFrame, source: str, sql: str | None, elapsed_ms: int = 0) -> Rows:
    return Rows(frame=df.reset_index(drop=True), source=source, sql=sql,
                query_hash=hashlib.sha256((sql or source).encode()).hexdigest()[:16],
                rows_hash=_hash_frame(df), elapsed_ms=elapsed_ms)


class Backend(Protocol):
    name: str

    def so2_rows(self, site_id: str, start_utc: pd.Timestamp, as_of: pd.Timestamp) -> Rows: ...
    def gfs_rows(self, issue_date: pd.Timestamp, as_of: pd.Timestamp) -> Rows: ...
    def sounding_rows(self, as_of: pd.Timestamp, max_age_h: int = 18) -> Rows: ...
    def episode_rows(self, as_of: pd.Timestamp) -> Rows: ...
    def action_levels(self) -> Rows: ...


def _ts(t: pd.Timestamp) -> str:
    return pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%d %H:%M:%S")


class ZetarisBackend:
    name = "zetaris"

    def __init__(self, client=None):
        from vogdesk.zetaris import Zetaris
        self.z = client or Zetaris.from_env()

    def _run(self, sql: str, as_of: pd.Timestamp, parse_ts: tuple[str, ...] = ()) -> Rows:
        r = self.z.run_sql(sql, as_of, limit=5000)
        df = r.frame()
        for c in df.columns:
            if c in parse_ts or c.endswith("_utc"):
                df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
            elif c in ("so2_ppb", "value", "grid_lat", "grid_lon", "inversion_base_m", "max_fountain_m",
                       "so2_ppm_min", "so2_ppm_max"):
                df[c] = pd.to_numeric(df[c], errors="coerce")
            elif c in ("episode", "fxx", "plan_level"):
                df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
            elif c == "preliminary":
                df[c] = df[c].astype(str).str.lower().eq("true")
        return _rows(df, f"zetaris:{self.z.base_url}", r.sql, r.elapsed_ms)

    def so2_rows(self, site_id: str, start_utc: pd.Timestamp, as_of: pd.Timestamp) -> Rows:
        sql = (f"SELECT site_id, time_utc, so2_ppb, data_source, preliminary, available_utc "
               f"FROM pahala_exposure.so2_hourly WHERE site_id = '{site_id}' AND time_utc >= TIMESTAMP '{_ts(start_utc)}' "
               f"ORDER BY time_utc")
        return self._run(sql, as_of)

    def gfs_rows(self, issue_date: pd.Timestamp, as_of: pd.Timestamp) -> Rows:
        run = gfs_run_for(issue_date)
        sql = (f"SELECT run_init_utc, fxx, valid_utc, var, level, point, value, available_utc "
               f"FROM pahala_exposure.gfs_points WHERE run_init_utc = TIMESTAMP '{_ts(run)}' ORDER BY fxx, var, level, point")
        return self._run(sql, as_of)

    def sounding_rows(self, as_of: pd.Timestamp, max_age_h: int = 18) -> Rows:
        since = pd.Timestamp(as_of) - pd.Timedelta(hours=max_age_h)
        sql = (f"SELECT valid_utc, inversion_base_m, available_utc FROM pahala_exposure.sounding_levels "
               f"WHERE valid_utc >= TIMESTAMP '{_ts(since)}' ORDER BY valid_utc DESC")
        return self._run(sql, as_of)

    def episode_rows(self, as_of: pd.Timestamp) -> Rows:
        sql = ("SELECT episode, start_utc, end_utc, start_known_utc, end_known_utc FROM pahala_exposure.episodes_known "
               "ORDER BY episode DESC")
        return self._run(sql, as_of)

    def action_levels(self) -> Rows:
        sql = "SELECT plan_level, color, so2_ppm_min, so2_ppm_max, averaging FROM pahala_exposure.action_plan_levels ORDER BY plan_level"
        return self._run(sql, pd.Timestamp.now(tz="UTC"))


class LocalBackend:
    name = "local"

    def __init__(self, src: Sources):
        self.src = src

    def so2_rows(self, site_id: str, start_utc: pd.Timestamp, as_of: pd.Timestamp) -> Rows:
        s = visible(self.src.so2, as_of)
        s = s[(s.site_id == site_id) & (s.time_utc >= start_utc)].sort_values("time_utc")
        s = s[["site_id", "time_utc", "so2_ppb", "source", "preliminary", "available_utc"]].rename(columns={"source": "data_source"})
        return _rows(s, "local:so2_hourly", None)

    def gfs_rows(self, issue_date: pd.Timestamp, as_of: pd.Timestamp) -> Rows:
        g = visible(self.src.gfs, as_of)
        g = g[g.run_init_utc == gfs_run_for(issue_date)].sort_values(["fxx", "var", "level", "point"])
        return _rows(g[["run_init_utc", "fxx", "valid_utc", "var", "level", "point", "value", "available_utc"]], "local:gfs_points", None)

    def sounding_rows(self, as_of: pd.Timestamp, max_age_h: int = 18) -> Rows:
        r = visible(self.src.raob, as_of)
        r = r[r.valid_utc >= pd.Timestamp(as_of) - pd.Timedelta(hours=max_age_h)].sort_values("valid_utc", ascending=False)
        return _rows(r[["valid_utc", "inversion_base_m", "available_utc"]], "local:sounding_levels", None)

    def episode_rows(self, as_of: pd.Timestamp) -> Rows:
        e = self.src.episodes[self.src.episodes.start_known_utc <= as_of].copy()
        known_end = e.end_known_utc.notna() & (e.end_known_utc <= as_of)
        e["end_utc"] = e["end_utc"].where(known_end)
        e["end_known_utc"] = e["end_known_utc"].where(known_end)
        e = e.sort_values("episode", ascending=False)[["episode", "start_utc", "end_utc", "start_known_utc", "end_known_utc"]]
        return _rows(e, "local:episodes_known", None)

    def action_levels(self) -> Rows:
        df = pd.DataFrame({"plan_level": range(6), "color": ["green", "yellow", "orange", "red", "purple", "maroon"],
                           "so2_ppm_min": [0.0, 0.11, 0.21, 1.01, 3.01, 5.01], "so2_ppm_max": [0.10, 0.20, 1.00, 3.00, 5.00, None],
                           "averaging": "15-minute average"})
        return _rows(df, "local:action_plan_levels", None)


def open_backend(kind: str, src: Sources | None = None) -> Backend:
    if kind == "zetaris":
        return ZetarisBackend()
    if kind == "local":
        from vogdesk import sources
        return LocalBackend(src or sources.load())
    raise ValueError(f"unknown data backend {kind!r}")
