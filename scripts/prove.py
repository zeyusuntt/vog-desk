"""Re-check every headline number of the held-out result from committed files, and print PASS or FAIL for each.

Reads only results/heldout_*_v4.csv, docs/LABEL_AUDIT_heldout.md and, when the git history is present, the commit
times of the pre-registration, the freeze and the live forecasts. It does not run the desk, the models or the
network, so it works from a fresh clone. Exit status is 0 only if every check passes.

Usage: .venv/bin/python scripts/prove.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vogdesk.metrics import block_bootstrap_diff  # noqa: E402

T_STAR = 0.18          # frozen in docs/PREREG.md
CLIMATOLOGY = 0.059    # 2025 base rate, frozen in docs/PREREG.md
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((bool(ok), what))
    print(f"{'PASS' if ok else 'FAIL'}  {what}")


def same(x: float, claimed: str) -> bool:
    """True when x rounds to the claimed figure at the claimed number of decimals."""
    decimals = len(claimed.split(".")[1]) if "." in claimed else 0
    return f"{x:+.{decimals}f}".lstrip("+") == claimed.lstrip("+").replace("−", "-")


def load() -> pd.DataFrame:
    full = pd.read_csv(ROOT / "results" / "heldout_full_v4.csv")
    nr = pd.read_csv(ROOT / "results" / "heldout_noreview_v4.csv")[["issue_date_hst", "p"]].rename(columns={"p": "p_noreview"})
    d = full.merge(nr, on="issue_date_hst", how="inner")
    d["y"] = d["event"].astype(str).str.lower().isin(["true", "1"]).astype(float)
    d = d.dropna(subset=["p", "p_noreview", "p_model", "p_forecast7"])
    d.index = pd.to_datetime(d["issue_date_hst"])
    return d.sort_index()


def git_time(rev: str, path: str | None = None) -> pd.Timestamp | None:
    cmd = ["git", "log", "-1", "--format=%cI", rev] if path is None else ["git", "log", "--diff-filter=A", "--format=%cI", "--", path]
    try:
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip().splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return pd.Timestamp(out[-1]).tz_convert("UTC") if out else None


def main() -> None:
    d = load()
    y = d["y"].to_numpy()
    brier = lambda p: float(np.mean((np.asarray(p, float) - y) ** 2))
    print("Held-out period, frozen desk v4, from results/heldout_*_v4.csv\n")
    check(len(d) == 102 and int(y.sum()) == 39, f"102 scored nights with 39 events (found {len(d)} and {int(y.sum())})")
    for name, col, claimed in [("full desk", "p", "0.215"), ("desk without review", "p_noreview", "0.224"),
                               ("recent rate, forecast 7", "p_forecast7", "0.246"), ("frozen 2025 model", "p_model", "0.332")]:
        b = brier(d[col]); check(same(b, claimed), f"Brier, {name}: {b:.4f} reported as {claimed}")
    clim = brier(np.full(len(y), CLIMATOLOGY)); check(same(clim, "0.341"), f"Brier, 2025 climatology: {clim:.4f} reported as 0.341")
    bss = 1 - brier(d["p"]) / clim; check(same(bss, "0.37"), f"Brier skill of the full desk against climatology: {bss:+.3f} reported as +0.37")
    const = brier(np.full(len(y), y.mean())); check(same(const, "0.236"), f"constant at the season's own rate: {const:.4f} reported as 0.236")
    for b_col, claimed in [("p_model", ("-0.117", "-0.157", "-0.080")), ("p_forecast7", ("-0.031", "-0.048", "-0.014")),
                           ("p_noreview", ("-0.009", "-0.019", "+0.001"))]:
        r = block_bootstrap_diff(d["p"], d[b_col], y, d.index, 7)
        ok = same(r["diff"], claimed[0]) and same(r["ci90"][0], claimed[1]) and same(r["ci90"][1], claimed[2])
        check(ok, f"desk minus {b_col}: {r['diff']:+.4f} [{r['ci90'][0]:+.4f}, {r['ci90'][1]:+.4f}] reported as {claimed[0]} [{claimed[1]}, {claimed[2]}]")
    warn = d["p"] >= T_STAR
    hits, fa, miss = int((warn & (y == 1)).sum()), int((warn & (y == 0)).sum()), int((~warn & (y == 1)).sum())
    check((int(warn.sum()), hits, fa, miss) == (87, 37, 50, 2), f"warnings at t* 0.18: {int(warn.sum())} ({hits} hits, {fa} false alarms, {miss} misses), reported as 87 (37, 50, 2)")
    orange = int((d["level"] == "prepare_orange").sum()); check(orange == 40, f"prepare-for-orange nights: {orange}, reported as 40")
    audit = (ROOT / "docs" / "LABEL_AUDIT_heldout.md").read_text()
    m = re.search(r"DOH Pahala exceedance nights: (\d+); ours: (\d+); in both: (\d+)", audit)
    check(bool(m) and m.groups() == ("39", "39", "39"), "labels match the Department of Health list on 39 of 39 nights")

    print("\nTimeline, from git (skipped when the history is not present)\n")
    prereg, freeze, scored = (git_time(t) or git_time(h) for t, h in (("prereg", "8649b40"), ("freeze-v4", "4b766f1"), ("heldout-scored", "f4dffd5")))
    train = git_time("HEAD", "scripts/train.py")
    if None in (prereg, freeze, scored, train):
        print("SKIP  git history for the pre-registration, freeze and scoring commits is not in this clone")
    else:
        check(prereg < train, f"pre-registration {prereg:%Y-%m-%d %H:%M} UTC precedes the training script {train:%Y-%m-%d %H:%M} UTC")
        check(freeze < scored, f"desk frozen {freeze:%Y-%m-%d %H:%M} UTC before the held-out scoring was committed {scored:%Y-%m-%d %H:%M} UTC")
    for f in sorted((ROOT / "live").glob("*/forecast.json")):
        t = git_time("HEAD", str(f.relative_to(ROOT)))
        if t is None:
            print(f"SKIP  {f.parent.name}: no commit time in this clone"); continue
        closes = (pd.Timestamp(f.parent.name) + pd.Timedelta(days=1, hours=12 + 10)).tz_localize("UTC")
        check(t < closes, f"live {f.parent.name}: first committed {t:%Y-%m-%d %H:%M} UTC, before the window closed at {closes:%Y-%m-%d %H:%M} UTC")

    failed = [w for ok, w in results if not ok]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks pass")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
