"""Write verified nights into memory the way the Verifier would have, night by night, with the time each
outcome became known (12:00 HST the next day plus the 2 h publication lag).

Used to give the desk its history before a backtest or the live run: the 2025 training nights first,
then each later night only after its window closed. A query as of an issue time therefore sees exactly
the nights a desk running since 2025 would have remembered.

Usage: .venv/bin/python scripts/seed_memory.py --through 2025-12-31 [--memory fake --memory-path data/memory/seed.json | --memory http]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from vogdesk import dataset, sources  # noqa: E402
from vogdesk.analogs import night_record  # noqa: E402
from vogdesk.asof import HOURLY_LAG, HST  # noqa: E402
from vogdesk.features import feature_table  # noqa: E402
from vogdesk.memory import FakeMemory, HttpMemory  # noqa: E402


def verification_time(issue_date: pd.Timestamp) -> pd.Timestamp:
    """12:00 HST on D+1 is when the window closes; the last hour is public 2 h later."""
    return (issue_date.normalize() + pd.Timedelta(days=1, hours=12) + HST + HOURLY_LAG).tz_localize("UTC")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--through", required=True, help="last issue date to write (inclusive)")
    ap.add_argument("--memory", choices=["fake", "http"], default="fake")
    ap.add_argument("--memory-path", type=Path, default=ROOT / "data" / "memory" / "seed.json")
    ap.add_argument("--source-label", default="verifier:backfill")
    args = ap.parse_args()
    src = sources.load()
    lab = dataset.labels(src)
    dates = pd.date_range(args.start, args.through, freq="D")
    lab = lab.reindex(dates)
    valid = lab.index[lab["valid"].fillna(False).astype(bool)]
    X = feature_table(src, valid)
    if args.memory == "http":
        mem = HttpMemory(os.environ.get("MEMORY_URL", "http://127.0.0.1:8790"))
        if not mem.ping():
            raise SystemExit("memory-service not reachable")
    else:
        args.memory_path.parent.mkdir(parents=True, exist_ok=True)
        mem = FakeMemory(args.memory_path)
    existing = set()
    try:
        for r in mem.query("verified night", ["station:pahala"], pd.Timestamp.now(tz="UTC").to_pydatetime(), k=5000):
            existing |= {t for t in r.record.tags if t.startswith("night:")}
    except Exception:
        pass
    n = 0
    for d in valid:
        if f"night:{d:%Y-%m-%d}" in existing:
            continue
        row = lab.loc[d]
        f = {k: (float("nan") if pd.isna(v) else v) for k, v in X.loc[d].items()}
        rec = night_record(f"{d:%Y-%m-%d}", f, bool(row["event"]), float(row["max_ppb"]), int(row["n_hours"]),
                           source=f"{args.source_label}:{d:%Y-%m-%d}", available_utc=verification_time(d).to_pydatetime())
        mem.add(rec)
        n += 1
    print(f"wrote {n} nights ({len(existing)} already present) through {args.through} into {args.memory} memory")


if __name__ == "__main__":
    main()
