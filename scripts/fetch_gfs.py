"""Extract point values from archived GFS 0.25-degree forecasts on AWS (noaa-gfs-bdp-pds).

Information cutoff: a forecast issued at 18:00 HST on day D (04:00 UTC on D+1) uses the GFS run
initialised at 18Z on day D (UTC). NCEP finishes 18Z products a few hours after initialisation
(https://www.nco.ncep.noaa.gov/pmb/nwprod/prodstat/), so this run is public at least ~6 hours
before issue. The 00Z run of D+1 is not used: it may not be complete at 04Z.
Leads f010, f016, f022 are valid at 18:00, 00:00 and 06:00 HST of the forecast night.
Only byte ranges of the needed GRIB messages are downloaded (from the .idx files); the GRIB
pieces are decoded with eccodes and deleted.
Usage: python scripts/fetch_gfs.py 2025-01-01 2026-10-22 [workers]
"""
from __future__ import annotations

import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import eccodes
import pandas as pd
import requests

BUCKET = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
OUT = Path(__file__).resolve().parents[1] / "data" / "raw" / "gfs"
LEADS = (10, 16, 22)
FIELDS = [("UGRD", "925 mb"), ("VGRD", "925 mb"), ("UGRD", "850 mb"), ("VGRD", "850 mb"),
          ("UGRD", "10 m above ground"), ("VGRD", "10 m above ground"), ("HPBL", "surface")]
POINTS = {"summit": (19.406, -155.283), "pahala": (19.197, -155.481), "ocean_view": (19.110, -155.765),
          "kona": (19.644, -155.996), "upwind_ene": (19.80, -154.80)}


def ranges_for(idx_text: str) -> list[tuple[str, str, int, int | None]]:
    lines = [ln.split(":") for ln in idx_text.strip().splitlines()]
    out = []
    for i, parts in enumerate(lines):
        var, level = parts[3], parts[4]
        if (var, level) in FIELDS:
            start = int(parts[1])
            end = int(lines[i + 1][1]) - 1 if i + 1 < len(lines) else None
            out.append((var, level, start, end))
    return out


def one(day: date, fxx: int, session: requests.Session) -> list[dict]:
    stem = f"gfs.{day:%Y%m%d}/18/atmos/gfs.t18z.pgrb2.0p25.f{fxx:03d}"
    idx = session.get(f"{BUCKET}/{stem}.idx", timeout=60)
    if idx.status_code != 200:
        return [{"issue_date_hst": day.isoformat(), "fxx": fxx, "var": None, "status": idx.status_code}]
    rows = []
    for var, level, start, end in ranges_for(idx.text):
        rng = f"bytes={start}-{'' if end is None else end}"
        r = session.get(f"{BUCKET}/{stem}", headers={"Range": rng}, timeout=120)
        r.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".grib2") as tf:
            tf.write(r.content); tf.flush()
            with open(tf.name, "rb") as fh:
                gid = eccodes.codes_grib_new_from_file(fh)
                try:
                    valid = pd.Timestamp(str(eccodes.codes_get(gid, "validityDate")) +
                                         f"{eccodes.codes_get(gid, 'validityTime'):04d}", tz="UTC")
                    for name, (lat, lon) in POINTS.items():
                        nearest = eccodes.codes_grib_find_nearest(gid, lat, lon % 360)[0]
                        rows.append({"issue_date_hst": day.isoformat(), "run_init_utc": f"{day.isoformat()}T18:00Z",
                                     "fxx": fxx, "valid_utc": valid.isoformat(), "var": var, "level": level,
                                     "point": name, "grid_lat": nearest["lat"], "grid_lon": nearest["lon"] - 360,
                                     "value": nearest["value"], "status": 200})
                finally:
                    eccodes.codes_release(gid)
    return rows


def main(start: str, end: str, workers: int = 8) -> None:
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    days = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
    jobs = [(d, f) for d in days for f in LEADS]
    OUT.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(lambda j: one(j[0], j[1], session), jobs):
            rows.extend(res)
    df = pd.DataFrame(rows)
    out = OUT / f"gfs_points_{start}_{end}.parquet"
    df.to_parquet(out, index=False)
    bad = df[df.status != 200]
    print(f"wrote {out}: {len(df)} rows from {len(jobs)} files; missing files {len(bad)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 8)
