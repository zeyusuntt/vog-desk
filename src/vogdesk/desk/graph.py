"""The desk as a LangGraph StateGraph: evidence -> guidance -> review -> (requery -> guidance)* -> decide.

Nodes are plain functions over CaseState. Model calls happen in exactly two nodes (evidence summary on
Lightning, decision on Ultra); everything else is code, so the loop is deterministic and replayable.
`review_enabled=False` is forecast 5 (desk without review): the review node records its flags but never
orders a re-query. Every node appends to the JSONL trace and to the World Model case record.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from langgraph.graph import END, StateGraph

from vogdesk import model as model_mod
from vogdesk.asof import issue_time_utc
from vogdesk.desk import duty_lead, forecaster
from vogdesk.desk.data import Backend
from vogdesk.desk.evidence import collect_evidence, summarise_regime
from vogdesk.desk.state import CaseState, Decision, EvidenceItem, Flag, Guidance
from vogdesk.features import features_for
from vogdesk.llm import ChatClient
from vogdesk.memory import Memory
from vogdesk.sources import Sources
from vogdesk.trace import Trace

DEFAULT_T_STAR = 0.19  # provisional until frozen on dev (PREREG "Frozen values"); read from config at run time


@dataclass
class DeskContext:
    backend: Backend
    sources: Sources
    chat: ChatClient
    memory: Memory
    model: model_mod.VogModel
    trace: Trace
    t_star: float = DEFAULT_T_STAR

    def wm(self, state: CaseState, kind: str, agent: str, **payload) -> None:
        try:
            self.memory.wm_ingest(state["case_id"], {"event_id": f"{state['case_id']}:{kind}:{len(self.trace.lines())}", "kind": kind,
                                                      "agent": agent, "ts": datetime.now(timezone.utc).isoformat(), **payload})
        except Exception as e:  # the World Model is a record, never a reason to stop a forecast
            self.trace.write(agent, "wm_ingest_failed", output=str(e)[:200])


def build_graph(ctx: DeskContext):
    issue_of = lambda s: pd.Timestamp(s["issue_date_hst"])
    as_of_of = lambda s: issue_time_utc(issue_of(s))

    def evidence_node(state: CaseState) -> dict:
        t0 = time.perf_counter()
        items = collect_evidence(ctx.backend, issue_of(state))
        for it in items:
            ctx.trace.write("EvidenceAgent", f"query:{it.values.get('query')}", inputs={"as_of": it.as_of_utc}, sql=[it.sql] if it.sql else [],
                            output={"id": it.id, "summary": it.summary, "rows_hash": it.rows_hash, "available_utc": it.available_utc, "dq": it.dq_flags})
        f = features_for(ctx.sources, issue_of(state))
        feats = {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in f.items() if k not in ("issue_date_hst", "issue_utc")}
        summary, call = summarise_regime(ctx.chat, state["issue_date_hst"], items)
        ctx.trace.write("EvidenceAgent", "regime_summary", inputs={"evidence": [i.id for i in items]}, llm_call=call.model_dump(),
                        output=summary, duration_ms=int((time.perf_counter() - t0) * 1000))
        ctx.wm(state, "evidence", "EvidenceAgent", n_items=len(items), evidence_ids=[i.id for i in items], regime_summary=summary)
        return {"evidence": [i.model_dump() for i in items], "features": feats, "regime_summary": summary,
                "llm_calls": [call.model_dump()], "review_round": 0, "flags": [], "requery_requests": []}

    def guidance_node(state: CaseState) -> dict:
        t0 = time.perf_counter()
        evidence = [EvidenceItem.model_validate(e) for e in state["evidence"]]
        feats = {k: (float("nan") if v is None else v) for k, v in state["features"].items()}
        g1, p_model, outside = forecaster.model_guidance(ctx.model, feats, [e.id for e in evidence if e.kind in ("sql", "forecast", "sounding", "notice")])
        g2, analogs = forecaster.analog_guidance(ctx.memory, feats, as_of_of(state).to_pydatetime())
        g3 = forecaster.recent_rate_guidance(ctx.memory, as_of_of(state).to_pydatetime())
        flags = forecaster.review_flags(evidence, p_model, analogs, outside)
        # Flags already resolved in an earlier round stay resolved.
        prior = {f["code"]: f for f in state.get("flags", []) if f.get("resolved")}
        for fl in flags:
            if fl.code in prior:
                fl.resolved, fl.resolution = True, prior[fl.code]["resolution"]
        ctx.trace.write("ForecasterAgent", "guidance", inputs={"features": state["features"]},
                        output={"G1": g1.model_dump(), "G2": g2.model_dump(), "G3": g3.model_dump(), "analog_trace": analogs.trace[:10],
                                "flags": [f.model_dump() for f in flags]}, duration_ms=int((time.perf_counter() - t0) * 1000))
        ctx.wm(state, "guidance", "ForecasterAgent", p_model=g1.p, p_analog=g2.p, p_recent=g3.p, n_analogs=analogs.n, flags=[f.code for f in flags])
        return {"guidance": [g1.model_dump(), g2.model_dump(), g3.model_dump()], "model_p": p_model, "flags": [f.model_dump() for f in flags],
                "analog_max_ppb": analogs.max_ppb_seen}

    def review_node(state: CaseState) -> dict:
        flags = [Flag.model_validate(f) for f in state["flags"]]
        unresolved = [f for f in flags if not f.resolved]
        targets = duty_lead.requery_targets(unresolved) if state.get("review_enabled", True) else []
        decision = "requery" if targets and state.get("review_round", 0) < duty_lead.MAX_ROUNDS else "decide"
        ctx.trace.write("DutyLead", "review", inputs={"round": state.get("review_round", 0)},
                        output={"flags": [f.code for f in unresolved], "next": decision, "requery": targets, "review_enabled": state.get("review_enabled", True)})
        ctx.wm(state, "review", "DutyLead", round=state.get("review_round", 0), flags=[f.code for f in unresolved], next=decision)
        return {"requery_requests": targets if decision == "requery" else []}

    def requery_node(state: CaseState) -> dict:
        t0 = time.perf_counter()
        targets = state["requery_requests"]
        start = len(state["evidence"]) + 1
        items = collect_evidence(ctx.backend, issue_of(state), start_id=start, targets=targets)
        for it in items:
            ctx.trace.write("DutyLead", f"requery:{it.values.get('query')}", sql=[it.sql] if it.sql else [],
                            output={"id": it.id, "summary": it.summary, "rows_hash": it.rows_hash})
        # A re-query resolves a flag when the fresh rows say the same thing as the first query (same rows hash) or
        # when the gap it was raised for is now filled. The resolution text names the evidence ids used.
        flags = [Flag.model_validate(f) for f in state["flags"]]
        by_query = {e["values"].get("query"): e for e in state["evidence"]}
        for fl in flags:
            if fl.resolved:
                continue
            checked = [it for it in items if it.values.get("query") in duty_lead.REQUERY_FOR.get(fl.code, [])]
            if not checked:
                continue
            if fl.code == "data_gap":
                filled = [it for it in checked if not (it.values.get("rows", 1) == 0 or it.values.get("n", 1) == 0 or it.values.get("n_hours", 24) < 14)]
                if filled:
                    fl.resolved, fl.resolution = True, "gap filled on re-query: " + ", ".join(it.id for it in filled)
            else:
                same = [it for it in checked if by_query.get(it.values.get("query"), {}).get("rows_hash") == it.rows_hash]
                fl.resolved = True
                fl.resolution = ("re-query returned the same rows (" + ", ".join(it.id for it in same) + "); the disagreement is real, not a data error"
                                 if same else "re-query returned different rows: " + ", ".join(it.id for it in checked))
        ctx.trace.write("DutyLead", "requery_done", output={"new_evidence": [i.id for i in items], "resolved": [f.code for f in flags if f.resolved]},
                        duration_ms=int((time.perf_counter() - t0) * 1000))
        ctx.wm(state, "requery", "DutyLead", targets=targets, new_evidence=[i.id for i in items])
        return {"evidence": [i.model_dump() for i in items], "flags": [f.model_dump() for f in flags],
                "review_round": state.get("review_round", 0) + 1, "requery_requests": []}

    def decide_node(state: CaseState) -> dict:
        t0 = time.perf_counter()
        evidence = [EvidenceItem.model_validate(e) for e in state["evidence"]]
        guidance = [Guidance.model_validate(g) for g in state["guidance"]]
        flags = [Flag.model_validate(f) for f in state["flags"]]
        dec, call = duty_lead.decide(ctx.chat, state["issue_date_hst"], state["regime_summary"], evidence, guidance, flags,
                                     state.get("t_star", ctx.t_star), state.get("review_round", 0), state.get("analog_max_ppb"))
        ctx.trace.write("DutyLead", "decide", inputs={"guidance": [g.id for g in guidance], "flags": [f.code for f in flags]},
                        llm_call=call.model_dump(), output=dec.model_dump(), duration_ms=int((time.perf_counter() - t0) * 1000))
        ctx.wm(state, "decision", "DutyLead", decision_kind=dec.kind, p=dec.p, level=dec.level, weights=dec.weights)
        return {"decision": dec.model_dump(), "llm_calls": [call.model_dump()]}

    g = StateGraph(CaseState)
    g.add_node("evidence", evidence_node)
    g.add_node("guidance", guidance_node)
    g.add_node("review", review_node)
    g.add_node("requery", requery_node)
    g.add_node("decide", decide_node)
    g.set_entry_point("evidence")
    g.add_edge("evidence", "guidance")
    g.add_edge("guidance", "review")
    g.add_conditional_edges("review", lambda s: "requery" if s.get("requery_requests") else "decide", {"requery": "requery", "decide": "decide"})
    g.add_edge("requery", "guidance")
    g.add_edge("decide", END)
    return g


def run_case(ctx: DeskContext, issue_date: str, review_enabled: bool = True, case_id: str | None = None,
             checkpoint_db: Path | None = None) -> CaseState:
    case_id = case_id or f"case-{issue_date}-{'full' if review_enabled else 'noreview'}-{uuid.uuid4().hex[:6]}"
    ctx.trace = Trace(case_id, ctx.trace.path.parent / f"{case_id}.jsonl") if ctx.trace else Trace(case_id)
    graph = build_graph(ctx)
    init: CaseState = {"case_id": case_id, "issue_date_hst": issue_date, "issue_utc": issue_time_utc(pd.Timestamp(issue_date)).isoformat(),
                       "review_enabled": review_enabled, "data_backend": ctx.backend.name, "evidence": [], "guidance": [], "flags": [],
                       "llm_calls": [], "errors": [], "t_star": ctx.t_star, "review_round": 0}
    if checkpoint_db:
        from langgraph.checkpoint.sqlite import SqliteSaver
        import sqlite3
        conn = sqlite3.connect(str(checkpoint_db), check_same_thread=False)
        app = graph.compile(checkpointer=SqliteSaver(conn))
        return app.invoke(init, config={"configurable": {"thread_id": case_id}, "recursion_limit": 25})
    app = graph.compile()
    return app.invoke(init, config={"recursion_limit": 25})
