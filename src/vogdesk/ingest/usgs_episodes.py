"""Parse the USGS Kilauea episode table (start and pause times in HST) and join HANS publication times.

Source: https://www.usgs.gov/volcanoes/kilauea/science/eruption-information
USGS labels the table as unreviewed preliminary estimates. Times are converted with HST = UTC-10.
The USGS page is retrospective, so a forecast may only use an episode boundary after the first
HANS notice that published it (from ingest/hans.py). If no notice was parsed, availability falls
back to the boundary time plus FALLBACK_LAG.
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pandas as pd

HST = pd.Timedelta(hours=10)
FALLBACK_LAG = pd.Timedelta(hours=12)


def parse_hst(s: str) -> pd.Timestamp:
    s = re.sub(r"\s+", " ", str(s).replace("\xa0", " ")).strip()
    s = s.replace(" - ", " ").replace("noon", "12 p.m.").replace("midnight", "12 a.m.")
    s = re.sub(r"(\d)\s*([ap])\.m\.", r"\1 \2m", s)
    return (pd.Timestamp(s) + HST).tz_localize("UTC")


def parse_end(pause: str, start: str) -> pd.Timestamp:
    """Pause cells sometimes omit the year ("July 9 - 1:20 p.m."); take it from the start cell."""
    if not isinstance(pause, str) or not re.search(r"\d", pause):
        return pd.NaT
    if not re.search(r"\d{4}", pause):
        year = re.search(r"\d{4}", str(start)).group(0)
        pause = re.sub(r"^(\w+ \d{1,2})", rf"\1, {year}", pause.strip())
    return parse_hst(pause)


def load(html_path: Path) -> pd.DataFrame:
    tables = pd.read_html(io.StringIO(html_path.read_text(encoding="utf-8", errors="ignore")))
    t = next(t for t in tables if str(t.iloc[0, 0]).startswith("Episode Number"))
    t.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in t.iloc[0]]
    t = t.iloc[1:]
    ep_col = next(c for c in t.columns if c.startswith("Episode"))
    start_col = next(c for c in t.columns if c.startswith("Start"))
    pause_col = next(c for c in t.columns if c.startswith("Pause Date"))
    height_col = next(c for c in t.columns if "Fountain Height" in c)
    vol_col = next(c for c in t.columns if "volume" in c.lower())
    out = pd.DataFrame({
        "episode": pd.to_numeric(t[ep_col], errors="coerce"),
        "start_utc": t[start_col].map(parse_hst),
        "end_utc": [parse_end(e, s) for e, s in zip(t[pause_col], t[start_col])],
        "max_fountain_m": pd.to_numeric(t[height_col].astype(str).str.extract(r"([\d,]+)")[0].str.replace(",", ""), errors="coerce"),
        "volume_mm3": pd.to_numeric(t[vol_col].astype(str).str.extract(r"([\d.]+)")[0], errors="coerce"),
    }).dropna(subset=["episode"])
    out["episode"] = out["episode"].astype(int)
    return out.sort_values("episode").reset_index(drop=True)


def main(argv: list[str]) -> None:
    usgs = load(Path(argv[0]))
    hans = pd.read_parquet(argv[1])
    m = usgs.merge(hans[["episode", "start_time_utc", "end_time_utc", "start_first_published_utc",
                         "end_first_published_utc"]], on="episode", how="left")
    m["start_diff_min"] = (m["start_time_utc"] - m["start_utc"]).dt.total_seconds() / 60
    m["end_diff_min"] = (m["end_time_utc"] - m["end_utc"]).dt.total_seconds() / 60
    m["start_known_utc"] = m["start_first_published_utc"].where(
        m["start_diff_min"].abs() <= 90, m["start_utc"] + FALLBACK_LAG)
    m["end_known_utc"] = m["end_first_published_utc"].where(
        m["end_diff_min"].abs() <= 90, m["end_utc"] + FALLBACK_LAG)
    m["start_known_source"] = (m["start_diff_min"].abs() <= 90).map({True: "hans", False: "fallback"})
    m["end_known_source"] = (m["end_diff_min"].abs() <= 90).map({True: "hans", False: "fallback"})
    keep = ["episode", "start_utc", "end_utc", "max_fountain_m", "volume_mm3", "start_known_utc", "end_known_utc",
            "start_known_source", "end_known_source", "start_diff_min", "end_diff_min"]
    out = m[keep]
    out.to_parquet(argv[2], index=False)
    pd.set_option("display.width", 220)
    print(f"USGS episodes: {len(usgs)} ({usgs.episode.min()} to {usgs.episode.max()})")
    agree_s = (out.start_diff_min.abs() <= 90).sum(); agree_e = (out.end_diff_min.abs() <= 90).sum()
    has_s = out.start_diff_min.notna().sum(); has_e = out.end_diff_min.notna().sum()
    print(f"start: HANS parsed {has_s}, within 90 min of USGS {agree_s}; end: parsed {has_e}, within 90 min {agree_e}")
    bad = out[(out.start_diff_min.abs() > 90) | (out.end_diff_min.abs() > 90)]
    print("disagreements (HANS minus USGS, minutes):"); print(bad[["episode", "start_utc", "start_diff_min", "end_utc", "end_diff_min"]].to_string(index=False))
    lag = (out.start_known_utc - out.start_utc)[out.start_known_source == "hans"].dt.total_seconds() / 60
    print(f"publication lag after start, minutes: median {lag.median():.0f}, 90th pct {lag.quantile(0.9):.0f}, max {lag.max():.0f}")
    print(out[["episode", "start_utc", "end_utc", "max_fountain_m"]].tail(6).to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1:])
