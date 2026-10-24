"""Shared state of one forecast case: what the desk saw, what it concluded, and why.

Every number an agent reports must point back to an evidence item by id; the structured outputs
below make that a type constraint rather than a prompt wish. Schema follows the desk design
section 4.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    id: str                                  # E1, E2, ...
    kind: Literal["sql", "forecast", "sounding", "notice", "model_input", "memory"]
    as_of_utc: datetime
    source: str                              # e.g. "zetaris:pahala_exposure.so2_hourly", "model:lr_2025"
    sql: str | None = None
    query_hash: str | None = None
    rows_hash: str | None = None
    available_utc: datetime | None = None    # latest publication time among the rows used
    summary: str                             # one plain sentence with the numbers
    values: dict = Field(default_factory=dict)
    dq_flags: list[str] = Field(default_factory=list)  # names of data quality rules the rows failed


class Guidance(BaseModel):
    id: str                                  # G1, G2, ...
    stream: Literal["lr", "analog", "recent_rate", "persistence", "climatology"]
    p: float | None                          # None when the stream has nothing to say
    interval: tuple[float, float] | None = None
    inputs: list[str] = Field(default_factory=list)   # evidence ids
    analog_ids: list[str] = Field(default_factory=list)
    note: str = ""


class Flag(BaseModel):
    code: Literal["analog_split", "model_analog_disagree", "analogs_sparse", "persistence_contradiction",
                  "regime_unseen", "data_gap", "dq_failed", "stale_data"]
    detail: str
    evidence_ids: list[str] = Field(default_factory=list)
    resolved: bool = False
    resolution: str = ""


class Decision(BaseModel):
    kind: Literal["issue", "issue_with_caveat", "handover"]
    p: float = Field(ge=0.0, le=1.0)
    level: Literal["normal", "prepare_yellow", "prepare_orange"]
    recommendation: str
    discussion: str                          # 6 to 10 sentences, each number followed by its evidence id
    cites: list[str] = Field(default_factory=list)
    handover_reason: str | None = None
    weights: dict[str, float] = Field(default_factory=dict)  # how p was formed from the guidance streams


class Verification(BaseModel):
    observed_max_ppb: float | None
    n_hours: int
    event: bool | None
    outcome: Literal["hit", "miss", "false_alarm", "correct_negative", "excluded", "handover"]
    brier: float | None
    memory_record_id: str | None = None
    ledger_entries: int = 0
    verified_utc: datetime


class LLMCall(BaseModel):
    agent: str
    model_requested: str
    provider_model: str | None
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached: bool = False
    validation: str = "ok"                   # ok | retried | fallback_template
    problems: list[str] = Field(default_factory=list)   # validation failures per attempt, for the trace
    reply_preview: str = ""                  # first 600 characters of the last raw reply


def _extend(a: list, b: list) -> list:
    return a + b


class CaseState(TypedDict, total=False):
    case_id: str
    issue_date_hst: str
    issue_utc: str
    review_enabled: bool
    data_backend: str                        # zetaris | local
    evidence: Annotated[list[dict], _extend]
    guidance: list[dict]
    flags: list[dict]
    regime_summary: str
    features: dict
    model_p: float | None
    analog_max_ppb: float | None
    review_round: int
    requery_requests: list[str]
    decision: dict | None
    llm_calls: Annotated[list[dict], _extend]
    errors: Annotated[list[str], _extend]
    t_star: float
