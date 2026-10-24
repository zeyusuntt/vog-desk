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
from vogdesk.baselines import episode_rule, persistence, recent_rate  # noqa: E402
from vogdesk.metrics import auc, block_bootstrap_diff, brier, bss, contingency, csi_max_threshold, reliability  # noqa: E402
from vogdesk.sources import station_series  # noqa: E402


def forecasts(src: sources.Sources, X: pd.DataFrame, m: model.VogModel | None, split: str) -> pd.DataFrame:
    idx = X.index
    f = pd.DataFrame(index=idx)
    f["climatology"] = dataset.CLIMATOLOGY
    f["persistence"] = persistence(station_series(src, "pahala"), idx, dataset.THRESHOLD_PPB).to_numpy()
    f["episode_rule"] = episode_rule(src.episodes, idx).to_numpy()
    if m is not None:
        f["model_raw"] = m.raw_probability(X)
        f["model"] = m.probability(X)
    # Forecasts 5 and 6 come from the desk backtest (scripts/backtest.py); 7 is the recent-rate baseline (PREREG change 1).
    for variant, name in (("noreview", "desk_no_review"), ("full", "desk_full")):
        path = model.ROOT / "data" / "derived" / f"backtest_{split}_{variant}.parquet"
        if path.exists():
            bt = pd.read_parquet(path).set_index(pd.to_datetime(pd.read_parquet(path)["issue_date_hst"]))
            f[name] = bt["p"].reindex(idx)
            f[name + "_handover"] = (bt["kind"] == "handover").reindex(idx)
    f["recent_rate"] = recent_rate(dataset.labels(src), idx).to_numpy()
    return f


def report(f: pd.DataFrame, y: pd.Series, split: str) -> str:
    lines = [f"## Evaluation on {split}: {len(y)} valid nights, {int(y.sum())} events, base rate {y.mean():.3f}", ""]
    binary = {"persistence", "episode_rule"}
    rows = []
    for name in [c for c in f.columns if not c.endswith("_handover")]:
        p = f[name].to_numpy(float)
        if name.startswith("desk_") and f[name].isna().all():
            continue
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
    for name in ("desk_full", "desk_no_review"):
        if name in f and f[name].notna().any():
            cover = int(f[name].notna().sum())
            ho = int(f[name + "_handover"].fillna(False).sum()) if name + "_handover" in f else 0
            lines += ["", f"{name}: {cover} of {len(y)} nights run, {ho} handed to a human (scored at the shown probability, counted separately)."]
            ok = f[name].notna()
            if cover < len(y):
                lines += [f"On the same {cover} nights: model Brier {brier(f.loc[ok, 'model'], y[ok]):.4f}, {name} Brier {brier(f.loc[ok, name], y[ok]):.4f}, "
                          f"recent_rate Brier {brier(f.loc[ok, 'recent_rate'], y[ok]):.4f}."]
            bb = block_bootstrap_diff(f.loc[ok, name], f.loc[ok, "model"], y[ok], f.index[ok])
            lines += [f"Paired Brier difference {name} minus model: {bb['diff']:+.4f}, 7-day block bootstrap 90% CI [{bb['ci90'][0]:+.4f}, {bb['ci90'][1]:+.4f}]."]
            bb7 = block_bootstrap_diff(f.loc[ok, name], f.loc[ok, "recent_rate"], y[ok], f.index[ok])
            lines += [f"Paired Brier difference {name} minus recent_rate: {bb7['diff']:+.4f}, 90% CI [{bb7['ci90'][0]:+.4f}, {bb7['ci90'][1]:+.4f}]."]
            lines += ["", f"### Reliability of {name}", "", reliability(f.loc[ok, name], y[ok]).to_markdown(index=False, floatfmt=".3f")]
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
    ap.add_argument("--i-am-sure", action="store_true", help="required for the held-out split, which is scored once")
    args = ap.parse_args()
    if args.split == "heldout":
        print("The held-out split is scored once, after freezing. Confirm by passing --split heldout --i-am-sure.", file=sys.stderr)
        if not args.i_am_sure:
            sys.exit(2)
    src = sources.load()
    X, lab = dataset.build(src, args.split)
    y = lab["event"].astype(float)
    m = model.load(Path(args.model)) if Path(args.model).exists() else None
    f = forecasts(src, X, m, args.split)
    text = report(f, y, args.split)
    print(text)
    if args.markdown:
        with open(args.markdown, "a") as fh:
            fh.write("\n" + text + "\n")
    if args.dump:
        f.join(lab).to_parquet(args.dump)


if __name__ == "__main__":
    main()
