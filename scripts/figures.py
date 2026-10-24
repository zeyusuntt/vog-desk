"""Result figures for RESULTS.md, the page and the deck, drawn from the committed per-night CSVs in results/.

Charting rules (one system everywhere): Public Sans; three categorical series at most, in a palette
validated for colour-vision separation (desk blue, frozen-model orange, recent-rate aqua); 2 px lines,
8 px markers with a surface ring, solid hairline grids, selective direct labels, text in ink tokens,
never in series colours. Reads only results/*.csv, so it runs from a fresh clone.

Usage: .venv/bin/python scripts/figures.py [--split heldout|dev]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "docs" / "figures"
FONTS = ROOT / "assets" / "fonts"

SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#1c2227", "#46525b", "#6f7a82", "#e3e7e9", "#c3cacf"
DESK, MODEL, RECENT, NOREVIEW = "#2a78d6", "#eb6834", "#1baf7a", "#8c98a0"
T_STAR = 0.18
EDGES = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0001])


def style() -> None:
    family = "DejaVu Sans"
    for ttf in sorted(FONTS.glob("PublicSans-*.ttf")):
        font_manager.fontManager.addfont(str(ttf))
        family = "Public Sans"
    plt.rcParams.update({
        "font.family": family, "font.size": 11, "text.color": INK, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
        "axes.edgecolor": AXIS, "axes.linewidth": 0.8, "axes.facecolor": SURFACE, "figure.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "grid.linestyle": "-", "axes.axisbelow": True,
        "axes.spines.top": False, "axes.spines.right": False, "axes.titlesize": 13, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "axes.titlepad": 12, "legend.frameon": False, "legend.fontsize": 10, "xtick.major.size": 0, "ytick.major.size": 0,
    })


def load(split: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    full = pd.read_csv(ROOT / "results" / f"{split}_full_v4.csv").dropna(subset=["brier"]).sort_values("issue_date_hst")
    nr = pd.read_csv(ROOT / "results" / f"{split}_noreview_v4.csv").dropna(subset=["brier"]).sort_values("issue_date_hst")
    for d in (full, nr):
        d["y"] = d["event"].astype(str).str.lower().isin(["true", "1"]).astype(float)
    return full, nr


def brier(p, y) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def reliability(ax, full: pd.DataFrame) -> None:
    ax.plot([0, 1], [0, 1], color=AXIS, lw=1, zorder=1)
    y = full["y"].to_numpy()
    for col, color, name, label_counts in (("p_model", MODEL, "Frozen 2025 model", False), ("p_forecast7", RECENT, "Recent event rate", False),
                                           ("p", DESK, "Full desk", True)):
        p = full[col].to_numpy(float)
        b = np.digitize(p, EDGES) - 1
        xs, ys, ns = [], [], []
        for i in range(len(EDGES) - 1):
            m = b == i
            if m.sum() >= 3:
                xs.append(p[m].mean()); ys.append(y[m].mean()); ns.append(int(m.sum()))
        ax.plot(xs, ys, color=color, lw=2, marker="o", ms=8, mec=SURFACE, mew=2, label=name, zorder=3 if label_counts else 2)
        if label_counts:
            for xv, yv, n in zip(xs, ys, ns):
                ax.annotate(f"{n}", (xv, yv), textcoords="offset points", xytext=(7, -12), fontsize=9, color=INK2)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.set_aspect("equal")
    ax.set_xlabel("forecast probability (bins with at least 3 nights)"); ax.set_ylabel("share of nights that reached 75 ppb")
    ax.legend(loc="upper left")


def value_curve(ax, full: pd.DataFrame) -> None:
    y = full["y"].to_numpy(); base = y.mean()
    ratios = np.round(np.arange(0.05, 0.501, 0.01), 2)
    for col, color, name in (("p_model", MODEL, "Frozen 2025 model"), ("p_forecast7", RECENT, "Recent event rate"), ("p", DESK, "Full desk")):
        p = full[col].to_numpy(float); vals = []
        for r in ratios:
            warn = p >= r
            hit, fa, miss = np.mean(warn & (y == 1)), np.mean(warn & (y == 0)), np.mean(~warn & (y == 1))
            clim, perfect = min(r, base), base * r
            vals.append((clim - ((hit + fa) * r + miss)) / (clim - perfect) if clim > perfect else np.nan)
        ax.plot(ratios, vals, color=color, lw=2, label=name)
    ax.axhline(0, color=AXIS, lw=1)
    ax.set_ylim(-0.6, 0.8); ax.set_xlim(0.05, 0.5)
    ax.set_xlabel("cost of acting on a warning, as a share of the loss it prevents"); ax.set_ylabel("value relative to a perfect forecast")
    ax.legend(loc="upper right")


def brier_bars(ax, full: pd.DataFrame, nr: pd.DataFrame) -> None:
    y = full["y"].to_numpy(); base = y.mean()
    rows = [("Full desk", brier(full["p"], y), DESK), ("Desk without the review", brier(nr["p"], nr["y"]), NOREVIEW),
            ("Recent event rate", brier(full["p_forecast7"], y), NOREVIEW), ("Frozen 2025 model", brier(full["p_model"], y), NOREVIEW),
            ("2025 climatology", brier(np.full(len(y), 0.059), y), NOREVIEW)]
    ref = base * (1 - base)
    ax.axvline(ref, color=INK2, lw=1, zorder=1)
    ax.annotate(f"a constant at the season's own rate,\nknown only afterwards: {ref:.3f}", xy=(ref, -0.62), xytext=(ref + 0.008, -0.62),
                fontsize=9, color=INK2, va="bottom", annotation_clip=False)
    for i, (name, v, color) in enumerate(rows):
        ax.barh(i, v, height=0.52, color=color, alpha=1 if i == 0 else 0.55, zorder=2)
        ax.text(v + 0.006, i, f"{v:.3f}", va="center", fontsize=11, fontweight="bold" if i == 0 else "normal", color=INK, zorder=3,
                bbox=dict(boxstyle="square,pad=0.15", fc=SURFACE, ec="none"))
    ax.set_yticks(range(len(rows)), [r[0] for r in rows]); ax.set_ylim(len(rows) - 0.5, -0.9)
    ax.tick_params(axis="y", labelsize=11, labelcolor=INK)
    ax.set_xlim(0, max(r[1] for r in rows) * 1.18); ax.grid(axis="y", visible=False)
    ax.set_xlabel("Brier score, lower is better")


def nights_dots(ax, full: pd.DataFrame) -> None:
    d = full.reset_index(drop=True); x = np.arange(len(d)); p = d["p"].to_numpy(float); ev = d["y"].to_numpy() == 1
    ax.axhline(T_STAR, color="#d6a90e", lw=2, zorder=1)
    ax.text(0, T_STAR - 0.035, f"action threshold {T_STAR:.0%}", fontsize=9, color=INK2, va="top")
    ax.scatter(x[~ev], p[~ev], s=34, facecolor=SURFACE, edgecolor=MUTED, linewidth=1.3, zorder=2, label="stayed below 75 ppb")
    ax.scatter(x[ev], p[ev], s=40, facecolor=INK, edgecolor=SURFACE, linewidth=1.5, zorder=3, label="reached 75 ppb")
    months = pd.to_datetime(d["issue_date_hst"]).dt.strftime("%B")
    starts = [i for i in range(len(d)) if i == 0 or months[i] != months[i - 1]]
    ax.set_xticks(starts, [months[i] for i in starts])
    ax.set_ylim(0, 1); ax.set_xlim(-2, len(d) + 1)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    ax.set_ylabel("desk probability"); ax.legend(loc="upper left", ncol=2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["heldout", "dev"], default="heldout")
    args = ap.parse_args()
    style()
    full, nr = load(args.split)
    n, k = len(full), int(full["y"].sum())
    period = "held-out season, 1 July to 21 October 2026" if args.split == "heldout" else "development period, January to June 2026"
    FIG.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(6.2, 6.4)); reliability(ax, full)
    ax.set_title(f"Reliability, {period}\n{n} nights, {k} reached 75 ppb", fontsize=12)
    fig.tight_layout(); fig.savefig(FIG / f"reliability_{args.split}.png", dpi=200); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.4, 4.4)); value_curve(ax, full)
    ax.set_title(f"When acting on the warning pays, {period}", fontsize=12)
    fig.tight_layout(); fig.savefig(FIG / f"value_{args.split}.png", dpi=200); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.4, 4.0)); brier_bars(ax, full, nr)
    ax.set_title(f"Brier score, {period}", fontsize=12, pad=30)
    fig.tight_layout(); fig.savefig(FIG / f"brier_{args.split}.png", dpi=200); plt.close(fig)

    fig, ax = plt.subplots(figsize=(9.6, 3.8)); nights_dots(ax, full)
    ax.set_title(f"Every night of the {period}", fontsize=12)
    fig.tight_layout(); fig.savefig(FIG / f"nights_{args.split}.png", dpi=200); plt.close(fig)
    print(f"wrote reliability, value, brier and nights figures for {args.split} under {FIG}")


if __name__ == "__main__":
    main()
