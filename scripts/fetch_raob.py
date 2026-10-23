"""Fetch Hilo (IEM station PITO, WMO 91285) radiosonde soundings from the Iowa Environmental Mesonet.

Service: https://mesonet.agron.iastate.edu/cgi-bin/request/raob.py?help
Launches are at 00Z (14:00 HST) and 12Z (02:00 HST). A forecast issued at 18:00 HST may use the
00Z sounding of the same evening (launched four hours earlier) and earlier ones.
Usage: python scripts/fetch_raob.py 2024-12-01 2026-10-23
"""
from __future__ import annotations

import io
import sys
import time

import pandas as pd
import requests
from pathlib import Path

URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/raob.py"
OUT = Path(__file__).resolve().parents[1] / "data" / "raw" / "raob"


def main(start: str, end: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    edges = pd.date_range(start, end, freq="MS").tolist() + [pd.Timestamp(end)]
    frames = []
    for a, b in zip(edges[:-1], edges[1:]):
        r = requests.get(URL, params={"station": "PITO", "sts": f"{a:%Y-%m-%dT%H:%MZ}", "ets": f"{b:%Y-%m-%dT%H:%MZ}"}, timeout=120)
        r.raise_for_status()
        frames.append(pd.read_csv(io.StringIO(r.text)))
        time.sleep(1)
    df = pd.concat(frames, ignore_index=True)
    df.to_csv(OUT / f"PITO_{start}_{end}.csv", index=False)
    n = df["validUTC"].nunique()
    print(f"soundings {n}, levels {len(df)}, first {df.validUTC.min()}, last {df.validUTC.max()}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
