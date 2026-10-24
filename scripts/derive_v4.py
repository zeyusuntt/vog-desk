"""Derive the version-4 dev backtest from the version-3 run by applying the adjustment gate.

Version 4 differs from version 3 in one code rule: the DutyLead's adjustment is applied only when the
review raised a flag. The v3 run recorded, per night, the base probability, the flags and the
adjustment the model proposed, so v4's probabilities follow exactly without another model run. The
discussion texts in the v3 traces still describe the ungated adjustment on unflagged nights; the
derived file says so. Also prints the CSI-maximising t* on the derived forecasts.

Usage: .venv/bin/python scripts/derive_v4.py --variant full|noreview
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from vogdesk.metrics import auc, brier, contingency, csi_max_threshold  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["full", "noreview"], required=True)
    args = ap.parse_args()
    src = ROOT / "data" / "derived" / f"backtest_dev_{args.variant}_v3.parquet"
    d = pd.read_parquet(src).sort_values("issue_date_hst").reset_index(drop=True)
    flagged = d["flags"].fillna("").str.len() > 0
    d["adjustment_proposed"] = d["adjustment"].astype(float)
    d["adjustment"] = np.where(flagged, d["adjustment_proposed"], 0.0)
    d["p"] = (d["base_p"].astype(float) + d["adjustment"]).clip(0, 1).round(4)
    d["variant"] = f"{args.variant}_v4"
    d["derived_from"] = "v3 run with the flag gate applied in post-processing (scripts/derive_v4.py)"
    ok = d.dropna(subset=["brier"]).copy()
    y = ok["event"].astype(float)
    ok["brier"] = (ok["p"] - y) ** 2
    d.loc[ok.index, "brier"] = ok["brier"]
    # outcome at the provisional t* is not recomputed here; evaluate.py scores from p.
    out = ROOT / "data" / "derived" / f"backtest_dev_{args.variant}_v4.parquet"
    d.to_parquet(out, index=False)
    p = ok["p"].to_numpy(float); yy = y.to_numpy()
    t = csi_max_threshold(p, yy); ct = contingency(p, yy, t)
    print(f"{args.variant} v4: {len(ok)} nights, {int(yy.sum())} events; Brier {brier(p, yy):.4f}, AUC {auc(p, yy):.3f}; "
          f"adjustments kept {int((d.adjustment != 0).sum())} of {int((d.adjustment_proposed != 0).sum())} proposed; "
          f"CSI-max t* = {t:.2f} (CSI {ct['CSI']:.3f}, POD {ct['POD']:.2f}, FAR {ct['FAR']:.2f}, hits {ct['hits']}, misses {ct['misses']}, FA {ct['false_alarms']})")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
