"""Analog nights: the desk's second guidance stream, built from verified nights in memory.

For an issue date D the desk describes the evening as tags and a short text, asks memory for the
ten most similar earlier nights, and reports how often those nights had an event. This is a second
probability estimate shown next to the statistical model. It is not averaged into it.

The information cutoff is enforced by `memory.query(..., as_of_utc=issue_utc)`. This module always
passes the issue time it was given and never a later one. A night enters memory only after its
window closed and its outcome was verified (docs/PREREG.md, "Information available at issue").
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np
from pydantic import BaseModel, Field

from vogdesk.features import FEATURES
from vogdesk.memory import Memory, MemoryRecord

WILSON_Z_90 = 1.645
CONFLICT_GAP = 0.3  # model and analogs differ by more than this in probability (plan 2.4)
SPLIT_MIN = 3  # at least this many events and this many non-events among the analogs (plan 2.4)
SPARSE_N = 5  # fewer rated analogs than this is flagged as sparse
LIGHT_WIND_MS = 2.0
TRADE_FRACTION = 0.5
INVERSION_LOW_M, INVERSION_MID_MAX_M, INVERSION_NONE_M = 1000.0, 2000.0, 4000.0
PAHALA_ELEVATED_PPB, PAHALA_EVENT_PPB = 20.0, 75.0
FULL_NIGHT_HOURS, PARTIAL_NIGHT_HOURS = 17, 14


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x)) or bool(np.isnan(x))


def _pahala_max_ppb(f: dict) -> float:
    """Recent Pahala maximum in ppb, from log(1 + ppb). NaN when unknown. Rounded to avoid 74.9999."""
    x = f.get("log_pahala_max_24h", np.nan)
    return float("nan") if _nan(x) else round(float(np.expm1(x)), 6)


def situation_tags(f: dict) -> list[str]:
    """Deterministic, sorted tags that describe the evening from a feature dict."""
    tags = ["station:pahala"]
    tags.append("episode:active" if f.get("episode_active") == 1.0 else "episode:pause")

    speed, along = f.get("gfs925_speed_summit", np.nan), f.get("gfs925_along_summit", np.nan)
    if _nan(speed) or _nan(along):
        regime = "unknown"
    elif speed < LIGHT_WIND_MS:
        regime = "light"
    elif along >= TRADE_FRACTION * speed:
        regime = "trade"
    elif along <= -TRADE_FRACTION * speed:
        regime = "kona"
    else:
        regime = "cross"
    tags.append(f"regime:{regime}")

    frac = f.get("gfs925_sector_frac", np.nan)
    if _nan(frac):
        sector = "unknown"
    elif frac <= 0.0:
        sector = "none"
    elif frac >= 1.0:
        sector = "all"
    else:
        sector = "part"
    tags.append(f"sector:{sector}")

    base = f.get("raob_inversion_base_m", np.nan)
    if _nan(base):
        inversion = "unknown"
    elif base < INVERSION_LOW_M:
        inversion = "low"
    elif base <= INVERSION_MID_MAX_M:
        inversion = "mid"
    elif base < INVERSION_NONE_M:
        inversion = "high"
    else:
        inversion = "none"
    tags.append(f"inversion:{inversion}")

    ppb = _pahala_max_ppb(f)
    if math.isnan(ppb):
        recent = "unknown"
    elif ppb < PAHALA_ELEVATED_PPB:
        recent = "quiet"
    elif ppb < PAHALA_EVENT_PPB:
        recent = "elevated"
    else:
        recent = "event"
    tags.append(f"pahala_recent:{recent}")
    return sorted(tags)


def _tag_value(tags: list[str], prefix: str) -> str:
    for t in tags:
        if t.startswith(prefix):
            return t[len(prefix):]
    return "unknown"


def situation_text(f: dict, tags: list[str]) -> str:
    """One plain sentence about the evening. Missing values are written as unknown."""
    speed = f.get("gfs925_speed_summit", np.nan)
    frac = f.get("gfs925_sector_frac", np.nan)
    base = f.get("raob_inversion_base_m", np.nan)
    ppb = _pahala_max_ppb(f)
    speed_s = "unknown" if _nan(speed) else f"{float(speed):.1f} m/s"
    frac_s = "unknown" if _nan(frac) else f"{float(frac):.2f}"
    base_s = "unknown" if _nan(base) else f"{float(base):.0f} m"
    ppb_s = "unknown" if math.isnan(ppb) else f"{ppb:.0f} ppb"
    return (
        f"Pahala evening with {_tag_value(tags, 'regime:')} wind regime, "
        f"episode {_tag_value(tags, 'episode:')}, "
        f"925 hPa summit wind speed {speed_s}, "
        f"sector fraction {frac_s}, "
        f"inversion base {base_s}, "
        f"recent Pahala 24 h max {ppb_s}."
    )


def wilson(k: int, n: int, z: float = WILSON_Z_90) -> tuple[float, float]:
    """Wilson score interval (lo, hi) for k events in n nights. (0, 1) when n is 0."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


class AnalogResult(BaseModel):
    p_shrunk: float | None = None  # rate shrunk toward a prior (recent rate), (k + m*prior)/(n + m)
    prior: float | None = None
    prior_weight: int = 0
    p: float | None  # event rate among analogs with a known outcome, None when there are none
    lo: float | None
    hi: float | None
    n: int  # analogs with a known outcome
    n_events: int
    analog_ids: list[str]  # every retrieved record, in rank order
    analog_nights: list[str]  # the date part of each record's night tag, same order
    max_ppb_seen: float | None
    tags_used: list[str]
    query_text: str
    as_of_utc: datetime
    trace: list[dict] = Field(default_factory=list)
    split: bool
    fallback_level: int  # 0 full tag set, 1 inversion dropped, 2 inversion and sector dropped


def _event_of(rec: MemoryRecord) -> bool | None:
    e = rec.metadata.get("event")
    return e if isinstance(e, bool) else None


def _max_ppb_of(rec: MemoryRecord) -> float | None:
    v = rec.metadata.get("max_ppb")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(v):
        return None
    return float(v)


def retrieve_analogs(memory: Memory, f: dict, as_of_utc: datetime, k: int = 20, min_n: int = 5,
                     window_days: int | None = 365, fetch: int = 60) -> AnalogResult:
    """Ask memory for the k most similar earlier nights as of the issue time and summarise them.

    If fewer than min_n records come back, drop the inversion tag and retry, then the sector tag
    too. Station, episode and regime are never dropped. Dev tuning (23 Oct 2026, DEVLOG): fetch more
    candidates, prefer nights verified within `window_days` of the issue time when at least min_n of
    them exist, keep the k best by engine score. k went from 10 to 20 to reduce sampling noise.
    """
    if as_of_utc.tzinfo is None or as_of_utc.utcoffset() is None:
        raise ValueError("as_of_utc must be timezone-aware")
    all_tags = situation_tags(f)
    text = situation_text(f, all_tags)
    base = [t for t in all_tags if not t.startswith("pahala_recent:")]  # recent Pahala goes in the text only
    levels = [
        base,
        [t for t in base if not t.startswith("inversion:")],
        [t for t in base if not t.startswith(("inversion:", "sector:"))],
    ]
    level, tags, hits = 0, levels[0], []
    for level, tags in enumerate(levels):
        hits = memory.query(text, tags, as_of_utc, max(k, fetch))
        if window_days is not None:
            recent = [h for h in hits if h.record.available_utc >= as_of_utc - timedelta(days=window_days)]
            if len(recent) >= min_n:
                hits = recent
        hits = hits[:k]  # memory returns them ranked by the engine's score
        if len(hits) >= min_n:
            break

    events = [_event_of(h.record) for h in hits]
    known = [e for e in events if e is not None]
    n, n_events = len(known), sum(known)
    lo, hi = wilson(n_events, n) if n else (None, None)
    ppbs = [v for v in (_max_ppb_of(h.record) for h in hits) if v is not None]
    nights = [_tag_value(h.record.tags, "night:") for h in hits]
    trace = [
        {
            "id": h.record.id, "night": night, "score": h.score,
            "matched_tags": list(h.trace.get("matched_tags", [])),
            "event": e, "max_ppb": _max_ppb_of(h.record),
        }
        for h, night, e in zip(hits, nights, events)
    ]
    return AnalogResult(
        p=n_events / n if n else None, lo=lo, hi=hi, n=n, n_events=n_events,
        analog_ids=[h.record.id for h in hits], analog_nights=nights,
        max_ppb_seen=max(ppbs) if ppbs else None,
        tags_used=list(tags), query_text=text, as_of_utc=as_of_utc, trace=trace,
        split=n_events >= SPLIT_MIN and n - n_events >= SPLIT_MIN, fallback_level=level,
    )


def conflict_flags(model_p: float, analogs: AnalogResult, gap: float = CONFLICT_GAP) -> list[dict]:
    """Flags for the duty lead. Defaults are the pre-registered values in the implementation plan."""
    flags = []
    if analogs.split:
        flags.append({
            "code": "analog_split",
            "detail": f"{analogs.n_events} events and {analogs.n - analogs.n_events} quiet nights among {analogs.n} analogs",
            "evidence_ids": list(analogs.analog_ids),
        })
    if analogs.p is not None and abs(model_p - analogs.p) > gap:
        flags.append({
            "code": "model_analog_disagree",
            "detail": f"model {model_p:.2f} against analog rate {analogs.p:.2f}, gap above {gap:.2f}",
            "evidence_ids": list(analogs.analog_ids),
        })
    if analogs.n < SPARSE_N:
        flags.append({
            "code": "analogs_sparse",
            "detail": f"only {analogs.n} analogs with a known outcome, fewer than {SPARSE_N}",
            "evidence_ids": list(analogs.analog_ids),
        })
    return flags


def _finite_or_none(x) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def night_record(
    issue_date: str, f: dict, event: bool, max_ppb: float, n_hours: int, source: str,
    available_utc: datetime, p_model: float | None = None, p_analog: float | None = None,
) -> MemoryRecord:
    """The record the Verifier writes once a night is verified."""
    tags = situation_tags(f)
    outcome = "event" if event else "quiet"
    content = f"{situation_text(f, tags)} Outcome: {outcome}, max {max_ppb:.0f} ppb."
    if n_hours >= FULL_NIGHT_HOURS:
        confidence = 0.9
    elif n_hours >= PARTIAL_NIGHT_HOURS:
        confidence = 0.75
    else:
        confidence = 0.5
    return MemoryRecord(
        content=content, source=source, confidence=confidence,
        tags=sorted(tags + [f"night:{issue_date}", f"outcome:{outcome}"]),
        available_utc=available_utc,
        metadata={
            "event": bool(event), "max_ppb": _finite_or_none(max_ppb), "n_hours": int(n_hours),
            "p_model": _finite_or_none(p_model), "p_analog": _finite_or_none(p_analog),
            "features": {k: _finite_or_none(f.get(k)) for k in FEATURES},
        },
    )


SHRINK_WEIGHT = 10   # dev tuning 23 Oct 2026: analogs with k=20 shrunk toward the recent rate with m=10 (DEVLOG)
ANALOG_WINDOW_DAYS = 365


def shrink(result: AnalogResult, prior: float | None, m: int = SHRINK_WEIGHT) -> AnalogResult:
    """Pull a noisy analog rate toward the recent-rate prior; with n analog nights the prior counts as m nights."""
    if prior is None:
        return result
    k, n = result.n_events, result.n
    result.p_shrunk = (k + m * prior) / (n + m) if (n + m) > 0 else prior
    result.prior, result.prior_weight = prior, m
    return result
