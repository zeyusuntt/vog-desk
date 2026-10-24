"""Compare our night labels with the Department of Health's own exceedance lists (PDFs under data/raw/doh).

DOH lists one row per station per exceedance hour, with the calendar day and the hour range in HST.
We map each row to the issue night that contains that hour (a row before noon belongs to the night
issued the day before) and compare with our labels. The held-out period is only audited with --split
heldout --i-am-sure, after the held-out forecasts are scored.

Usage: .venv/bin/python scripts/label_audit.py --split train|dev [--markdown]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vogdesk import dataset, sources  # noqa: E402

PDFS = {"2025": ROOT / "data" / "raw" / "doh" / "exceedances_2025.pdf", "2026": ROOT / "data" / "raw" / "doh" / "exceedances_2026_10_15.pdf"}
ROW = re.compile(r"\s*((?:Jan|Feb|Mar|Apr|May|June|July|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d+,\s+(\d{4}))\s+Pahala.*?(\d[\d,]*) ppb \(([^)]*)\)")


def doh_rows() -> pd.DataFrame:
    rows = []
    for year, pdf in PDFS.items():
        if not pdf.exists():
            continue
        text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
        for line in text.splitlines():
            m = ROW.match(line)
            if m:
                day = pd.Timestamp(m.group(1).replace(".", ""))
                hour = m.group(4)
                start = hour.split("-")[0].strip()
                h = int(re.match(r"(\d+)", start).group(1)) if re.match(r"\d+", start) else 12
                pm = "p.m." in start or start.startswith("noon")
                if start.startswith("12 midnight") or start.startswith("midnight"):
                    h, pm = 0, False
                h24 = (h % 12) + (12 if pm and h != 12 else 0) if not start.startswith("noon") else 12
                in_window = h24 >= 18 or h24 < 12           # our night window is 18:00 to 12:00 HST
                issue = day - pd.Timedelta(days=0 if h24 >= 18 else 1)
                rows.append({"doh_day": day, "hour": hour, "hour24": h24, "in_window": in_window,
                             "doh_max_ppb": float(m.group(3).replace(",", "")), "issue_date_hst": issue})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=list(dataset.SPLITS), default="dev")
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()
    if args.split == "heldout" and "--i-am-sure" not in sys.argv:
        sys.exit("The held-out labels are audited after the held-out forecasts are scored. Pass --i-am-sure.")
    a, b = dataset.SPLITS[args.split]
    src = sources.load()
    lab = dataset.labels(src)
    lab = lab[(lab.index >= a) & (lab.index <= b)]
    doh = doh_rows()
    doh = doh[(doh.issue_date_hst >= a) & (doh.issue_date_hst <= b)]
    outside = doh[~doh.in_window]
    doh = doh[doh.in_window]
    ours = lab[lab.event]
    doh_nights = doh.groupby("issue_date_hst").agg(doh_max_ppb=("doh_max_ppb", "max"), rows=("hour", "size"))
    both = doh_nights.join(ours[["max_ppb", "n_hours"]], how="outer", lsuffix="_doh")
    both["agree"] = both.doh_max_ppb.notna() & both.max_ppb.notna()
    n_doh, n_ours = len(doh_nights), len(ours)
    n_both = int(both.agree.sum())
    only_doh = both[both.max_ppb.isna()]
    only_ours = both[both.doh_max_ppb.isna()]
    within = both[both.agree].assign(diff=lambda d: (d.doh_max_ppb - d.max_ppb).abs())
    lines = [f"## Label audit, {args.split} ({a} to {b})", "",
             f"DOH Pahala exceedance nights: {n_doh}; ours: {n_ours}; in both: {n_both}; DOH only: {len(only_doh)}; ours only: {len(only_ours)}.",
             f"Daily maxima agree within 1 ppb on {int((within['diff'] <= 1.0).sum())} of {len(within)} shared nights (largest difference {within['diff'].max():.1f} ppb)." if len(within) else "", ""]
    if len(outside):
        lines += [f"DOH rows outside our 18:00 to 12:00 HST window (daytime exceedances, not events by our definition): {len(outside)}",
                  outside[["doh_day", "hour", "doh_max_ppb"]].to_markdown(index=False), ""]
    if len(only_doh):
        lines += ["DOH only (our night has no event; check validity and the hour mapping):", only_doh[["doh_max_ppb", "rows", "n_hours"]].to_markdown(), ""]
    if len(only_ours):
        lines += ["Ours only (event nights by our labels that the DOH list does not show):", only_ours[["max_ppb", "n_hours"]].to_markdown(), ""]
    text = "\n".join(lines)
    print(text)
    if args.markdown:
        out = ROOT / "docs" / f"LABEL_AUDIT_{args.split}.md"
        out.write_text(text + "\n")
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
