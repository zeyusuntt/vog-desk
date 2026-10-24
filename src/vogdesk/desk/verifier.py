"""Verifier: the next day at 12:00 HST, score the case, remember the night, and tell memory what helped.

Scoring follows PREREG: hit, miss, false alarm or correct negative at the action threshold; a handed-over
case is scored at the probability shown but counted separately; a night with fewer than 14 of 18 hours
and no event is excluded. The night is written to H-MEM with `available_utc` set to when the outcome was
public (12:00 HST D+1 plus the 2 h publication lag), and the analogs that pointed the right way get
`helpful`, the ones that pointed the wrong way `wrong`.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from vogdesk.analogs import night_record
from vogdesk.asof import HOURLY_LAG, HST
from vogdesk.dataset import THRESHOLD_PPB
from vogdesk.desk.state import CaseState, Verification
from vogdesk.labels import MIN_HOURS_FOR_NEGATIVE, night_labels
from vogdesk.memory import Memory, MemoryUnavailable
from vogdesk.sources import Sources, station_series


def verification_time(issue_date: pd.Timestamp) -> pd.Timestamp:
    return (pd.Timestamp(issue_date).normalize() + pd.Timedelta(days=1, hours=12) + HST + HOURLY_LAG).tz_localize("UTC")


def observe(src: Sources, issue_date: pd.Timestamp) -> tuple[float | None, int, bool | None]:
    """The night's maximum, hour count and event flag from the station series (AQS where validated, else AirNow)."""
    lab = night_labels(station_series(src, "pahala"), THRESHOLD_PPB)
    d = pd.Timestamp(issue_date).normalize()
    if d not in lab.index:
        return None, 0, None
    row = lab.loc[d]
    valid = bool(row["valid"])
    return (float(row["max_ppb"]) if pd.notna(row["max_ppb"]) else None, int(row["n_hours"]),
            bool(row["event"]) if valid else None)


def outcome_of(decision: dict, event: bool | None, t_star: float) -> str:
    if event is None:
        return "excluded"
    if decision.get("kind") == "handover":
        return "handover"
    warned = decision["p"] >= t_star
    return {(True, True): "hit", (True, False): "false_alarm", (False, True): "miss", (False, False): "correct_negative"}[(warned, event)]


def verify_case(state: CaseState, src: Sources, memory: Memory | None, t_star: float,
                write_memory: bool = True, feedback: bool = True) -> Verification:
    issue = pd.Timestamp(state["issue_date_hst"])
    max_ppb, n_hours, event = observe(src, issue)
    dec = state["decision"]
    outcome = outcome_of(dec, event, t_star)
    brier = None if event is None else float((dec["p"] - float(event)) ** 2)
    ver = Verification(observed_max_ppb=max_ppb, n_hours=n_hours, event=event, outcome=outcome, brier=brier,
                       verified_utc=verification_time(issue).to_pydatetime())
    if memory is None:
        return ver
    try:
        if write_memory and event is not None:
            feats = {k: (float("nan") if v is None else v) for k, v in (state.get("features") or {}).items()}
            g = {x["stream"]: x for x in state.get("guidance", [])}
            rec = night_record(state["issue_date_hst"], feats, event, max_ppb or 0.0, n_hours,
                               source=f"verifier:{state['case_id']}", available_utc=ver.verified_utc,
                               p_model=g.get("lr", {}).get("p"), p_analog=g.get("analog", {}).get("p"))
            rec.metadata["decision_p"] = dec["p"]
            rec.metadata["outcome"] = outcome
            ver.memory_record_id = memory.add(rec)
        if feedback and event is not None:
            analog_ids = next((x.get("analog_ids", []) for x in state.get("guidance", []) if x["stream"] == "analog"), [])
            for rid in analog_ids:
                try:
                    hits = memory.query("night", [], ver.verified_utc, k=1)  # cheap existence probe is not available; use ledger
                    rec_event = _event_of_record(memory, rid)
                    if rec_event is None:
                        continue
                    memory.feedback(rid, "helpful" if rec_event == event else "wrong")
                    ver.ledger_entries += 1
                except KeyError:
                    continue
    except MemoryUnavailable:
        pass
    return ver


def _event_of_record(memory: Memory, record_id: str) -> bool | None:
    """Look the record up through a tag-free query; the fake and the sidecar both return metadata."""
    try:
        for r in memory.query("night Pahala", ["station:pahala"], datetime.now(timezone.utc), k=5000):
            if r.record.id == record_id:
                ev = r.record.metadata.get("event")
                return ev if isinstance(ev, bool) else None
    except MemoryUnavailable:
        return None
    return None
