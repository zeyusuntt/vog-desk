"""Hawaii subset of EPA AQS pre-generated hourly SO2 files (parameter 42401).

Source: https://aqs.epa.gov/aqsweb/airdata/download_files.html
Times are kept as given: Date/Time Local (HST) and Date/Time GMT.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pandas as pd

HAWAII_STATE_CODE = "15"


def load_hawaii(zip_path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as fh:
            df = pd.read_csv(fh, dtype=str)
    df = df[df["State Code"] == HAWAII_STATE_CODE].copy()
    df["site_id"] = df["State Code"] + df["County Code"] + df["Site Num"]
    df["time_utc"] = pd.to_datetime(df["Date GMT"] + " " + df["Time GMT"], utc=True)
    df["time_local"] = pd.to_datetime(df["Date Local"] + " " + df["Time Local"])
    df["so2_ppb"] = pd.to_numeric(df["Sample Measurement"], errors="coerce")
    keep = ["site_id", "POC", "Latitude", "Longitude", "County Name", "time_utc", "time_local",
            "so2_ppb", "Units of Measure", "Qualifier", "Method Code", "Method Name", "Date of Last Change"]
    return df[keep].rename(columns=lambda c: c.lower().replace(" ", "_"))


def main(argv: list[str]) -> None:
    out = Path(argv[-1])
    frames = [load_hawaii(Path(p)) for p in argv[:-1]]
    df = pd.concat(frames, ignore_index=True).sort_values(["site_id", "poc", "time_utc"])
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"wrote {out}: {len(df)} rows")
    summary = df.groupby("site_id").agg(first=("time_local", "min"), last=("time_local", "max"),
                                         rows=("so2_ppb", "size"), max_ppb=("so2_ppb", "max"),
                                         units=("units_of_measure", "first"))
    print(summary.to_string())


if __name__ == "__main__":
    main(sys.argv[1:])
