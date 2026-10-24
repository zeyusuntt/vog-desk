"""ForecasterAgent: the statistical model and the memory's analogs, as two separate guidance streams.

The model (forecast 4, frozen) gives G1. The analogs retrieved from H-MEM as of the issue time give G2
with a Wilson interval. The recent event rate over the last verified nights (forecast 7) gives G3 as a
third reference. Nothing is averaged here; the DutyLead sees all three and states its weights.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from vogdesk.analogs import AnalogResult, conflict_flags, retrieve_analogs, shrink
from vogdesk.desk import memory_model as mm
from vogdesk.dataset import CLIMATOLOGY
from vogdesk.desk.state import EvidenceItem, Flag, Guidance
from vogdesk.memory import Memory
from vogdesk.model import VogModel


def model_guidance(m: VogModel, features: dict, evidence_ids: list[str]) -> tuple[Guidance, float, list[str]]:
    X = pd.DataFrame([features])
    p = float(m.probability(X)[0])
    raw = float(m.raw_probability(X)[0])
    outside = m.outside_training_range(pd.Series(features))
    return (Guidance(id="G1", stream="lr", p=round(p, 4), inputs=evidence_ids,
                     note=f"logistic regression fit on 2025 with isotonic calibration; raw {raw:.3f}"
                          + (f"; inputs outside the training range: {', '.join(outside)}" if outside else "")),
            p, outside)


def memory_model_guidance(memory: Memory, features: dict, as_of_utc: datetime) -> tuple[Guidance, dict]:
    """G2: the same 12-input model refit on the verified nights in memory (memory_model.py)."""
    pipe, note = mm.fit_memory_model(memory, as_of_utc)
    if pipe is None:
        return Guidance(id="G2", stream="memory_model", p=None, note=f"memory model unavailable: {note.get('reason')}"), note
    p = mm.predict(pipe, features)
    return (Guidance(id="G2", stream="memory_model", p=round(p, 4), analog_ids=note.get("record_ids", [])[:50],
                     note=f"logistic regression refit on {note['n_train']} verified nights in memory ({note['how']}, "
                          f"{note['events_train']} events, nights {note['oldest_night']} to {note['newest_night']})"), note)


def analog_guidance(memory: Memory, features: dict, as_of_utc: datetime, prior: float | None = None) -> tuple[Guidance, AnalogResult]:
    """G4: the event rate among the most similar remembered nights, shrunk toward the recent rate."""
    a = shrink(retrieve_analogs(memory, features, as_of_utc), prior)
    p_use = a.p_shrunk if a.p_shrunk is not None else a.p
    note = (f"{a.n_events} of {a.n} analog nights had an event (raw rate {a.p:.2f})" if a.p is not None else "no analog nights with a known outcome")
    note += f"; tags {', '.join(a.tags_used)}"
    if a.p_shrunk is not None:
        note += f"; shrunk toward the recent rate {a.prior:.2f} with weight {a.prior_weight}"
    if a.fallback_level:
        note += f"; fallback level {a.fallback_level}"
    if a.max_ppb_seen is not None:
        note += f"; highest analog max {a.max_ppb_seen:.0f} ppb"
    g = Guidance(id="G4", stream="analog", p=None if p_use is None else round(p_use, 4),
                 interval=None if a.p is None else (round(a.lo, 4), round(a.hi, 4)),
                 analog_ids=a.analog_ids, note=note)
    return g, a


def recent_rate_guidance(memory: Memory, as_of_utc: datetime, n_nights: int = 30, min_nights: int = 10) -> Guidance:
    """Forecast 7 from memory: the event rate over the last 30 verified nights visible at issue."""
    recs = memory.query("verified night Pahala", ["station:pahala"], as_of_utc, k=400)
    nights = sorted(((r.record.available_utc, r.record.metadata.get("event")) for r in recs
                     if isinstance(r.record.metadata.get("event"), bool)), key=lambda x: x[0])[-n_nights:]
    if len(nights) < min_nights:
        return Guidance(id="G3", stream="recent_rate", p=CLIMATOLOGY, note=f"only {len(nights)} verified nights in memory; 2025 climatology used")
    rate = float(np.mean([e for _, e in nights]))
    return Guidance(id="G3", stream="recent_rate", p=round(rate, 4), note=f"event rate over the last {len(nights)} verified nights")


def persistence_flag(evidence: list[EvidenceItem], p_model: float, p_analog: float | None) -> Flag | None:
    """Recent hours high while both guidances are low, or the reverse."""
    e24 = next((e for e in evidence if e.values.get("query") == "pahala_24h"), None)
    if e24 is None or e24.values.get("n_hours", 0) == 0:
        return None
    high_recent = e24.values.get("hours_at_or_above_75", 0) > 0
    both_low = p_model < 0.15 and (p_analog is None or p_analog < 0.15)
    both_high = p_model > 0.5 and (p_analog is not None and p_analog > 0.5)
    quiet_recent = e24.values.get("max_ppb", 0) < 10
    if high_recent and both_low:
        return Flag(code="persistence_contradiction", evidence_ids=[e24.id],
                    detail=f"{e24.values['hours_at_or_above_75']} of the last 24 hours reached 75 ppb but both guidances are below 0.15")
    if quiet_recent and both_high:
        return Flag(code="persistence_contradiction", evidence_ids=[e24.id],
                    detail=f"last 24 h max {e24.values['max_ppb']:.1f} ppb but both guidances are above 0.5")
    return None


def data_gap_flags(evidence: list[EvidenceItem]) -> list[Flag]:
    flags = []
    for e in evidence:
        q = e.values.get("query")
        if q == "gfs" and e.values.get("rows", 0) == 0:
            flags.append(Flag(code="data_gap", detail="GFS 18Z run not available at issue", evidence_ids=[e.id]))
        if q == "sounding" and e.values.get("n", 1) == 0:
            flags.append(Flag(code="data_gap", detail="no Hilo sounding in the last 18 hours", evidence_ids=[e.id]))
        if q == "pahala_24h" and e.values.get("n_hours", 24) < 14:
            flags.append(Flag(code="data_gap", detail=f"only {e.values.get('n_hours', 0)} of 24 recent Pahala hours published", evidence_ids=[e.id]))
        if e.dq_flags:
            flags.append(Flag(code="dq_failed", detail=f"{e.id} failed data quality rule(s): {', '.join(e.dq_flags)}", evidence_ids=[e.id]))
    return flags


def review_flags(evidence: list[EvidenceItem], p_model: float, analogs: AnalogResult, outside: list[str],
                 p_memory: float | None = None, memory_ids: list[str] | None = None, gap: float = 0.3) -> list[Flag]:
    """The DutyLead's checks, in code: (a) model vs memory disagreement, (b) analog split, (c) persistence,
    (d) regime unseen, (e) data gaps and data quality."""
    flags: list[Flag] = []
    if p_memory is None:
        flags.append(Flag(code="memory_model_unavailable", detail="too few verified nights in memory to refit the model"))
    elif abs(p_model - p_memory) > gap:
        flags.append(Flag(code="model_memory_disagree", detail=f"frozen model {p_model:.2f} against memory model {p_memory:.2f}, gap above {gap:.2f}",
                          evidence_ids=(memory_ids or [])[:20]))
    flags += [Flag(code=f["code"], detail=f["detail"], evidence_ids=f["evidence_ids"]) for f in conflict_flags(p_model, analogs)
              if f["code"] != "model_analog_disagree"]  # the memory model, not the noisy analog rate, carries the disagreement check
    pf = persistence_flag(evidence, p_model, p_memory if p_memory is not None else analogs.p)
    if pf:
        flags.append(pf)
    if outside:
        flags.append(Flag(code="regime_unseen", detail=f"model inputs outside the 2025 training range: {', '.join(outside)}"))
    flags.extend(data_gap_flags(evidence))
    return flags
