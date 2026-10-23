"""Build a Kilauea fountaining-episode table from raw HVO notices (USGS HANS).

For every episode we record the start and end times stated by HVO and, separately, the
UTC time of the first notice that published each fact. A forecast issued at time T may only
use facts whose published_utc <= T. Times stated in HST are converted with HST = UTC-10.
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

import pandas as pd

HST_OFFSET = pd.Timedelta(hours=10)
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
TIME = r"(\d{1,2}:\d{2})\s*([ap])\.?\s*m\.?\s*HST"
DATE = rf"((?:{MONTHS})\s+\d{{1,2}}(?:,\s*\d{{4}})?)"
START_RE = re.compile(rf"[Ee]pisode\s+(\d+)\b[^.]{{0,160}}?\bbegan\b[^.]{{0,60}}?\bat\s+{TIME}\s+on\s+{DATE}")
END_RE = re.compile(rf"[Ee]pisode\s+(\d+)\b[^.]{{0,160}}?\b(?:ended|stopped|ceased)\b[^.]{{0,40}}?\bat\s+{TIME}\s+on\s+{DATE}")
FORECAST_RE = re.compile(rf"forecast\s+for\s+episode\s+(\d+)[^.]{{0,80}}?\bbetween\s+{DATE}\s+and\s+{DATE}")


def notice_text(raw_html: str) -> str:
    t = re.sub(r"<br\s*/?>|</p>", "\n", raw_html)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"[ \t]+", " ", html.unescape(t))


def to_utc(clock: str, ampm: str, date: str, sent_utc: pd.Timestamp) -> pd.Timestamp:
    if not re.search(r"\d{4}", date):
        year = sent_utc.year
        guess = pd.Timestamp(f"{date}, {year}")
        if guess > sent_utc.tz_localize(None) + pd.Timedelta(days=31):
            year -= 1
        date = f"{date}, {year}"
    local = pd.Timestamp(f"{date} {clock} {ampm.upper()}M")
    return (local + HST_OFFSET).tz_localize("UTC")


def date_utc(date: str, sent_utc: pd.Timestamp) -> pd.Timestamp:
    return to_utc("12:00", "a", date, sent_utc)


def parse(notices: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    facts, forecasts = [], []
    for n in notices:
        sent = pd.Timestamp(n["sentUtc"], tz="UTC")
        t = notice_text(n["noticeHtml"])
        for kind, rx in (("start", START_RE), ("end", END_RE)):
            for m in rx.finditer(t):
                ep, clock, ampm, date = int(m.group(1)), m.group(2), m.group(3), m.group(4)
                try:
                    when = to_utc(clock, ampm, date, sent)
                except ValueError:
                    continue
                if when > sent + pd.Timedelta(minutes=5):
                    continue  # a stated past event cannot postdate its notice
                facts.append({"episode": ep, "kind": kind, "time_utc": when, "published_utc": sent,
                              "notice_type": n["noticeTypeCd"]})
        for m in FORECAST_RE.finditer(t):
            forecasts.append({"episode": int(m.group(1)), "window_start_utc": date_utc(m.group(2), sent),
                              "window_end_utc": date_utc(m.group(3), sent) + pd.Timedelta(days=1),
                              "published_utc": sent})
    f = pd.DataFrame(facts)
    # The stated time for an episode boundary is the value most often repeated; publication is the earliest notice.
    rows = []
    for (ep, kind), g in f.groupby(["episode", "kind"]):
        stated = g["time_utc"].mode().iloc[0]
        rows.append({"episode": ep, "kind": kind, "time_utc": stated,
                     "first_published_utc": g["published_utc"].min(), "n_notices": len(g),
                     "n_distinct_times": g["time_utc"].nunique()})
    long = pd.DataFrame(rows)
    wide = long.pivot(index="episode", columns="kind")
    wide.columns = [f"{k}_{c}" for c, k in wide.columns]
    wide = wide.reset_index().sort_values("episode")
    return wide, pd.DataFrame(forecasts).drop_duplicates()


def main(argv: list[str]) -> None:
    notices = json.loads(Path(argv[0]).read_text())
    episodes, forecasts = parse(notices)
    out_dir = Path(argv[1])
    episodes.to_parquet(out_dir / "episodes.parquet", index=False)
    forecasts.to_parquet(out_dir / "episode_forecasts.parquet", index=False)
    pd.set_option("display.width", 220)
    cols = ["episode", "start_time_utc", "end_time_utc", "start_first_published_utc", "end_first_published_utc",
            "start_n_distinct_times", "end_n_distinct_times"]
    print(episodes[[c for c in cols if c in episodes]].to_string(index=False))
    print(f"\nforecast windows parsed: {len(forecasts)}")


if __name__ == "__main__":
    main(sys.argv[1:])
