"""Fit forecast 4 on the 2025 training nights (docs/PREREG.md). Writes models/lr_2025.joblib and .json.

Usage: .venv/bin/python scripts/train.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vogdesk import dataset, model, sources  # noqa: E402
from vogdesk.metrics import auc, brier, reliability  # noqa: E402

OUT = model.MODEL_DIR / "lr_2025.joblib"


def main() -> None:
    src = sources.load()
    X, lab = dataset.build(src, "train")
    y = lab["event"].astype(float)
    print(f"training nights {len(y)}, events {int(y.sum())}, base rate {y.mean():.3f}")
    m = model.fit(X, y)
    try:
        m.notes["git_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        m.notes["git_head"] = "unknown"
    oof = pd.Series(m.notes.pop("_oof"))
    m.save(OUT)
    print("out-of-fold Brier by C:", {k: round(v, 4) for k, v in m.oof_brier.items()}, "chosen", m.C)
    print(f"OOF raw: Brier {brier(oof, y):.4f}, AUC {auc(oof, y):.3f}; climatology Brier {brier([dataset.CLIMATOLOGY] * len(y), y):.4f}")
    cal = m.isotonic.predict(oof)
    print(f"OOF after isotonic (in-sample for the isotonic step): Brier {brier(cal, y):.4f}")
    print("reliability of OOF raw predictions:")
    print(reliability(oof, y).to_string(index=False))
    print("standardised coefficients:")
    print(m.coefficients().round(3).to_string())
    print(f"wrote {OUT} and {OUT.with_suffix('.json')}")


if __name__ == "__main__":
    main()
