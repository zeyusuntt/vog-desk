"""Fetch Hawaii rows (AQSID starting with 15) from AirNow public hourly files.

Source bucket: https://files.airnowtech.org/airnow/YYYY/YYYYMMDD/HourlyData_YYYYMMDDHH.dat
File hours are UTC. Output: one pipe-delimited file per UTC day under data/raw/airnow/,
with the source file name prepended to each row.
Usage: python scripts/fetch_airnow.py 2026-03-01 2026-10-23
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = "https://s3-us-west-1.amazonaws.com/files.airnowtech.org/airnow"
OUT = Path(__file__).resolve().parents[1] / "data" / "raw" / "airnow"


def fetch_hour(ts: datetime) -> tuple[str, list[str], int]:
    name = f"HourlyData_{ts:%Y%m%d%H}.dat"
    url = f"{BASE}/{ts:%Y}/{ts:%Y%m%d}/{name}"
    for _ in range(3):
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 404:
                return name, [], 404
            r.raise_for_status()
            rows = [f"{name}|{ln}" for ln in r.text.splitlines() if "|15" in ln and ln.split("|")[2].startswith("15")]
            return name, rows, 200
        except requests.RequestException:
            continue
    return name, [], -1


def main(start: str, end: str) -> None:
    t0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    t1 = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    hours = []
    t = t0
    while t < t1:
        hours.append(t)
        t += timedelta(hours=1)
    OUT.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[str]] = {}
    status = {"ok": 0, "missing": 0, "failed": 0}
    with ThreadPoolExecutor(max_workers=24) as ex:
        for name, rows, code in ex.map(fetch_hour, hours):
            day = name[11:19]
            by_day.setdefault(day, []).extend(rows)
            status["ok" if code == 200 else "missing" if code == 404 else "failed"] += 1
    for day, rows in by_day.items():
        (OUT / f"{day}.dat").write_text("\n".join(sorted(rows)) + "\n")
    print(f"hours requested {len(hours)}: {status}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
