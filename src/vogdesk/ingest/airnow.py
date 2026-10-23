"""Parse Hawaii rows fetched from AirNow hourly files into a tidy table.

Row format (pipe-delimited, after our prepended source file name):
source_file|date(MM/DD/YY)|time(HH:MM)|AQSID|site_name|gmt_offset|parameter|units|value|data_source
The date and time in each row equal the UTC hour in the file name (checked against AQS in tests).
AirNow values are preliminary and unvalidated.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

COLS = ["source_file", "date", "time", "site_id", "site_name", "gmt_offset", "parameter", "units",
        "value", "data_source"]


def load_dir(raw_dir: Path) -> pd.DataFrame:
    frames = []
    for p in sorted(raw_dir.glob("2*.dat")):
        df = pd.read_csv(p, sep="|", names=COLS, dtype=str, quoting=3)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True).dropna(subset=["site_id"])
    df["time_utc"] = pd.to_datetime(df["date"] + " " + df["time"], format="%m/%d/%y %H:%M", utc=True)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["site_name"] = df["site_name"].str.strip()
    return df[["site_id", "site_name", "time_utc", "parameter", "units", "value", "source_file", "data_source"]]


def main(argv: list[str]) -> None:
    raw_dir, out = Path(argv[0]), Path(argv[1])
    df = load_dir(raw_dir)
    df.to_parquet(out, index=False)
    so2 = df[df.parameter == "SO2"]
    print(f"wrote {out}: {len(df)} rows, SO2 rows {len(so2)}")
    print(so2.groupby(["site_id", "site_name"]).agg(first=("time_utc", "min"), last=("time_utc", "max"),
                                                     n=("value", "size"), max_ppb=("value", "max")).to_string())


if __name__ == "__main__":
    main(sys.argv[1:])
