"""Fetch all HVO Kilauea (hi3) notices since a start date from the USGS HANS search API.

Docs: https://volcanoes.usgs.gov/hans-public/api/search/
Each notice keeps sentUtc (publication time), which bounds what a forecaster could have read.
Usage: python scripts/fetch_hans.py 2024-12-01
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

URL = "https://volcanoes.usgs.gov/hans-public/api/search/search"
OUT = Path(__file__).resolve().parents[1] / "data" / "raw" / "hans"


def main(start: str) -> None:
    start_unix = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    OUT.mkdir(parents=True, exist_ok=True)
    notices, page = [], 0
    while True:
        body = {"obsAbbr": "hvo", "noticeTypeCd": "", "volcCd": "hi3", "startUnixtime": start_unix,
                "endUnixtime": "", "searchText": "", "pageIndex": page}
        r = requests.post(URL, json=body, timeout=60)
        r.raise_for_status()
        batch = r.json().get("noticeData") or []
        if not batch:
            break
        notices.extend(batch)
        page += 1
        time.sleep(0.5)
    (OUT / "hi3_notices.json").write_text(json.dumps(notices))
    types = {}
    for n in notices:
        types[n["noticeTypeCd"]] = types.get(n["noticeTypeCd"], 0) + 1
    print(f"pages {page}, notices {len(notices)}, first {notices[-1]['sentUtc']}, last {notices[0]['sentUtc']}, types {types}")


if __name__ == "__main__":
    main(sys.argv[1])
