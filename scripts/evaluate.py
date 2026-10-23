"""Score forecasts 1 to 4 on a split (docs/PREREG.md). The held-out split is scored once, at the end.

Usage: .venv/bin/python scripts/evaluate.py --split dev [--markdown docs/DEVLOG.md]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vogdesk import dataset, model, sources  # noqa: E402
from vogdesk.baselines import episode_rule, persistence  # noqa: E402
from vogdesk.metrics import auc, block_bootstrap_diff, brier, bss, contingency, csi_max_threshold, reliability  # noqa: E402
from vogdesk.sources import station_series  # noqa: E402


def forecasts(src: sources.Sources, X: pd.DataFrame, m: model.VogModel | None) -> pd.DataFrame:
    idx = X.index
    f = pd.DataFrame(index=idx)
    f["climatology"] = dataset.CLIMATOLOGY
    f["persistence"] = persistence(station_series(src, "pahala"), idx, dataset.THRESHOLD_PPB).to_numpy()
    f["episode_rule"] = episode_rule(src.episodes, idx).to_numpy()
    if m is not None:
        f["model_raw"] = m.raw_probability(X)
        f["model"] = m.probability(X)
    return f


def report(f: pd.DataFrame, y: pd.Series, split: str) -> str:
    lines = [f"## Evaluation on {split}: {len(y)} valid nights, {int(y.sum())} events, base rate {y.mean():.3f}", ""]
    binary = {"persistence", "episode_rule"}
    rows = []
    for name in f.columns:
        p = f[name].to_numpy(float)
        t = 1.0 if name in binary else (csi_max_threshold(p, y) if name not in ("climatology",) else np.nan)
        c = contingency(p, y, t) if not np.isnan(t) else {k: np.nan for k in ("hits", "misses", "false_alarms", "correct_neg", "POD", "FAR", "CSI")} | {"t": np.nan}
        rows.append({"forecast": name, "Brier": brier(p, y), "BSS_clim": bss(p, y, dataset.CLIMATOLOGY),
                     "BSS_persist": bss(p, y, f["persistence"].to_numpy(float)), "AUC": auc(p, y) if name not in binary else np.nan,
                     "t*": c["t"], "hits": c["hits"], "misses": c["misses"], "FA": c["false_alarms"], "CN": c["correct_neg"],
                     "POD": c["POD"], "FAR": c["FAR"], "CSI": c["CSI"]})
    tab = pd.DataFrame(rows)
    lines.append(tab.to_markdown(index=False, floatfmt=".3f"))
    lines.append("")
    lines.append("t* is the CSI-maximising threshold on this split (PREREG grid 0.01 to 0.60); binary forecasts warn at 1.")
    if "model" in f:
        lines += ["", "### Reliability of the calibrated model", "", reliability(f["model"], y).to_markdown(index=False, floatfmt=".3f")]
        lines += ["", "### Reliability of the raw (uncalibrated) model", "", reliability(f["model_raw"], y).to_markdown(index=False, floatfmt=".3f")]
        bb = block_bootstrap_diff(f["model"], f["climatology"], y, f.index)
        lines += ["", f"Paired Brier difference model minus climatology: {bb['diff']:+.4f}, 7-day block bootstrap 90% CI "
                  f"[{bb['ci90'][0]:+.4f}, {bb['ci90'][1]:+.4f}], {bb['n_blocks']} blocks, {bb['blocks_with_event']} with an event."]
        bb2 = block_bootstrap_diff(f["model"], f["persistence"], y, f.index)
        lines += [f"Paired Brier difference model minus persistence: {bb2['diff']:+.4f}, 90% CI [{bb2['ci90'][0]:+.4f}, {bb2['ci90'][1]:+.4f}]."]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=list(dataset.SPLITS), default="dev")
    ap.add_argument("--model", default=str(model.MODEL_DIR / "lr_2025.joblib"))
    ap.add_argument("--markdown", help="append the report to this file")
    ap.add_argument("--dump", help="write per-night forecasts and labels to this parquet file")
    args = ap.parse_args()
    if args.split == "heldout":
        print("The held-out split is scored once, after freezing. Confirm by passing --split heldout --i-am-sure.", file=sys.stderr)
        if "--i-am-sure" not in sys.argv:
            sys.exit(2)
    src = sources.load()
    X, lab = dataset.build(src, args.split)
    y = lab["event"].astype(float)
    m = model.load(Path(args.model)) if Path(args.model).exists() else None
    f = forecasts(src, X, m)
    text = report(f, y, args.split)
    print(text)
    if args.markdown:
        with open(args.markdown, "a") as fh:
            fh.write("\n" + text + "\n")
    if args.dump:
        f.join(lab).to_parquet(args.dump)


if __name__ == "__main__":
    main()
