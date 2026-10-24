"""The memory model: the same logistic regression as forecast 4, refit at issue time on the verified nights
the desk remembers.

Dev finding (23 Oct 2026, DEVLOG): the frozen 2025 model scores below climatology in 2026 because the drivers
changed sign, and analog event rates carry little beyond the base rate. Refitting the same 12 inputs on the
last 120 days of verified nights in memory gives Brier 0.147 and AUC 0.68 on dev against 0.154 and 0.56 for the
recent-rate baseline. Memory is the training set: every record the Verifier wrote carries the night's features,
outcome, source and confidence, and only records with available_utc <= issue are used.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from vogdesk.features import FEATURES
from vogdesk.memory import Memory
from vogdesk.model import make_pipeline

WINDOW_DAYS = 120
MIN_NIGHTS = 40
MIN_EVENTS = 3
C = 0.1
HALFLIFE_FALLBACK_DAYS = 60


def verified_nights(memory: Memory, as_of_utc: datetime, k: int = 100000) -> pd.DataFrame:
    """Nights in memory visible at as_of with features and a boolean outcome, newest first."""
    rows = []
    for r in memory.query("verified night Pahala", ["station:pahala"], as_of_utc, k=k):
        md = r.record.metadata or {}
        feats = md.get("features") or {}
        ev = md.get("event")
        if not isinstance(ev, bool) or not feats:
            continue
        night = next((t.split(":", 1)[1] for t in r.record.tags if t.startswith("night:")), None)
        rows.append({"record_id": r.record.id, "night": night, "available_utc": r.record.available_utc, "event": float(ev),
                     "confidence": r.record.confidence, **{f: (np.nan if feats.get(f) is None else float(feats[f])) for f in FEATURES}})
    df = pd.DataFrame(rows)
    return df.sort_values("available_utc", ascending=False).reset_index(drop=True) if len(df) else df


def fit_memory_model(memory: Memory, as_of_utc: datetime) -> tuple[object | None, dict]:
    """Returns (fitted pipeline or None, note dict). Window first; whole history with recency weights as fallback."""
    nights = verified_nights(memory, as_of_utc)
    note = {"n_in_memory": int(len(nights)), "window_days": WINDOW_DAYS, "C": C}
    if nights.empty:
        return None, note | {"reason": "no verified nights with features in memory"}
    recent = nights[nights.available_utc >= as_of_utc - timedelta(days=WINDOW_DAYS)]
    if len(recent) >= MIN_NIGHTS and recent.event.sum() >= MIN_EVENTS:
        train, weights, how = recent, None, f"last {WINDOW_DAYS} days"
    elif len(nights) >= MIN_NIGHTS and nights.event.sum() >= MIN_EVENTS:
        age = np.array([(as_of_utc - a).total_seconds() / 86400 for a in nights.available_utc])
        train, weights, how = nights, 0.5 ** (age / HALFLIFE_FALLBACK_DAYS), f"all history, {HALFLIFE_FALLBACK_DAYS}-day half-life"
    else:
        return None, note | {"reason": f"only {len(nights)} nights / {int(nights.event.sum())} events in memory"}
    pipe = make_pipeline(C)
    pipe.fit(train[FEATURES], train["event"], lr__sample_weight=weights)
    note |= {"n_train": int(len(train)), "events_train": int(train.event.sum()), "how": how,
             "newest_night": train.night.iloc[0], "oldest_night": train.night.iloc[-1], "record_ids": train.record_id.tolist()[:50]}
    return pipe, note


def predict(pipe, features: dict) -> float:
    X = pd.DataFrame([{f: (np.nan if features.get(f) is None else features.get(f)) for f in FEATURES}])
    return float(np.clip(pipe.predict_proba(X)[:, 1][0], 0.0, 1.0))
