"""Verification metrics named in docs/PREREG.md. All functions take aligned probability and 0/1 arrays."""
from __future__ import annotations

import numpy as np
import pandas as pd

RELIABILITY_EDGES = np.array([0.0, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0001])
THRESHOLD_GRID = np.round(np.arange(0.01, 0.6001, 0.01), 2)


def _clean(p, y):
    p, y = np.asarray(p, float), np.asarray(y, float)
    ok = ~(np.isnan(p) | np.isnan(y))
    return p[ok], y[ok]


def brier(p, y) -> float:
    p, y = _clean(p, y)
    return float(np.mean((p - y) ** 2)) if len(p) else np.nan


def bss(p, y, ref) -> float:
    """Brier skill score of p against a reference forecast (scalar or array) on the same nights."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    ref = np.broadcast_to(np.asarray(ref, float), p.shape)
    ok = ~(np.isnan(p) | np.isnan(y) | np.isnan(ref))
    b, b_ref = np.mean((p[ok] - y[ok]) ** 2), np.mean((ref[ok] - y[ok]) ** 2)
    return float(1 - b / b_ref) if b_ref > 0 else np.nan


def auc(p, y) -> float:
    p, y = _clean(p, y)
    pos, neg = p[y == 1], p[y == 0]
    if not len(pos) or not len(neg):
        return np.nan
    ranks = pd.Series(np.concatenate([pos, neg])).rank().to_numpy()
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def contingency(p, y, t: float) -> dict:
    p, y = _clean(p, y)
    warn = p >= t
    hit = int((warn & (y == 1)).sum()); miss = int((~warn & (y == 1)).sum())
    fa = int((warn & (y == 0)).sum()); cn = int((~warn & (y == 0)).sum())
    return {"t": float(t), "hits": hit, "misses": miss, "false_alarms": fa, "correct_neg": cn,
            "POD": hit / (hit + miss) if hit + miss else np.nan,
            "FAR": fa / (hit + fa) if hit + fa else np.nan,
            "CSI": hit / (hit + miss + fa) if hit + miss + fa else np.nan}


def csi_max_threshold(p, y, grid=THRESHOLD_GRID) -> float:
    """PREREG: maximise CSI over the grid; ties go to the median grid value (lower for an even count)."""
    csi = np.array([contingency(p, y, t)["CSI"] for t in grid])
    if np.all(np.isnan(csi)):
        return float(grid[0])
    best = grid[csi == np.nanmax(csi)]
    return float(best[(len(best) - 1) // 2])


def reliability(p, y, edges=RELIABILITY_EDGES) -> pd.DataFrame:
    p, y = _clean(p, y)
    b = np.digitize(p, edges) - 1
    rows = []
    for i in range(len(edges) - 1):
        m = b == i
        rows.append({"bin": f"[{edges[i]:.2f}, {min(edges[i + 1], 1):.2f})", "n": int(m.sum()),
                     "mean_p": float(p[m].mean()) if m.any() else np.nan,
                     "obs_freq": float(y[m].mean()) if m.any() else np.nan,
                     "events": int(y[m].sum())})
    return pd.DataFrame(rows)


def block_bootstrap_diff(p_a, p_b, y, dates, block_days: int = 7, n: int = 2000, seed: int = 0) -> dict:
    """Paired Brier difference (a minus b) with a block bootstrap over contiguous calendar blocks."""
    p_a, p_b, y = np.asarray(p_a, float), np.asarray(p_b, float), np.asarray(y, float)
    dates = pd.DatetimeIndex(dates)
    ok = ~(np.isnan(p_a) | np.isnan(p_b) | np.isnan(y))
    p_a, p_b, y, dates = p_a[ok], p_b[ok], y[ok], dates[ok]
    d = (p_a - y) ** 2 - (p_b - y) ** 2
    block_id = ((dates - dates.min()).days // block_days)
    blocks = [d[block_id == b] for b in np.unique(block_id)]
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(n):
        pick = rng.integers(0, len(blocks), len(blocks))
        means.append(np.concatenate([blocks[i] for i in pick]).mean())
    lo, hi = np.percentile(means, [5, 95])
    return {"diff": float(d.mean()), "ci90": (float(lo), float(hi)), "n_blocks": len(blocks),
            "blocks_with_event": int(sum((y[block_id == b] == 1).any() for b in np.unique(block_id)))}
