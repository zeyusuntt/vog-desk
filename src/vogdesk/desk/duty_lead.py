"""DutyLead: reviews, orders re-queries, and decides. The probability is formed from the guidance
streams with weights the model states; the code checks the weights, applies the action mapping frozen
in PREREG, and validates every number in the discussion against the evidence. A reply that fails twice
is replaced by a template decision so the desk always issues something auditable.
"""
from __future__ import annotations

import json
import re

import numpy as np

from vogdesk.desk.evidence import evidence_table, numbers_in, validate_numbers
from vogdesk.desk.prompts import DUTY_LEAD_SYSTEM, DUTY_LEAD_USER
from vogdesk.desk.state import Decision, EvidenceItem, Flag, Guidance, LLMCall
from vogdesk.llm import ULTRA, ChatClient, Message

MAX_ROUNDS = 2
HANDOVER_GAP = 0.3
ORANGE_ANALOG_PPB = 210.0  # 0.21 ppm, the plan's orange threshold, on an analog night's hourly maximum

REQUERY_FOR = {
    "model_analog_disagree": ["hvnp_6h", "gfs", "episode"],
    "analog_split": ["gfs", "pahala_6h"],
    "persistence_contradiction": ["pahala_6h", "hvnp_6h", "ocean_view_6h"],
    "regime_unseen": ["gfs", "sounding"],
    "data_gap": ["gfs", "sounding", "pahala_24h"],
    "dq_failed": ["pahala_24h", "ocean_view_6h"],
    "analogs_sparse": [],
    "stale_data": ["pahala_6h"],
}


def requery_targets(flags: list[Flag]) -> list[str]:
    out: list[str] = []
    for f in flags:
        for t in REQUERY_FOR.get(f.code, []):
            if t not in out:
                out.append(t)
    return out


def action_level(p: float, t_star: float, analog_max_ppb: float | None) -> str:
    """PREREG mapping: p >= 2 t*, or any of the top analog nights at or above 0.21 ppm, means prepare for orange."""
    if p >= 2 * t_star or (analog_max_ppb is not None and analog_max_ppb >= ORANGE_ANALOG_PPB):
        return "prepare_orange"
    if p >= t_star:
        return "prepare_yellow"
    return "normal"


RECOMMENDATION = {
    "normal": "Normal operations. The probability is shown for information; no action under the schools plan is triggered by a forecast.",
    "prepare_yellow": "Prepare for yellow: have the plan's yellow-level actions ready for the morning (sensitive students, outdoor activity). "
                      "An hourly mean of 75 ppb does not by itself show a 15-minute average at the yellow level.",
    "prepare_orange": "Prepare for orange: brief staff tonight on the plan's orange-level actions and check the monitor at 06:00 HST.",
}


def guidance_table(guidance: list[Guidance]) -> str:
    rows = []
    for g in guidance:
        p = "none" if g.p is None else f"{g.p:.2f}"
        iv = f" (90% interval {g.interval[0]:.2f} to {g.interval[1]:.2f})" if g.interval else ""
        rows.append(f"[{g.id}] {g.stream}: p = {p}{iv}; {g.note}")
    return "\n".join(rows)


def flags_table(flags: list[Flag]) -> str:
    if not flags:
        return "none"
    return "\n".join(f"- {f.code}: {f.detail}" + (f" (resolved: {f.resolution})" if f.resolved else " (unresolved)") for f in flags)


def combine(guidance: list[Guidance], weights: dict[str, float]) -> float:
    streams = {g.stream: g.p for g in guidance if g.p is not None}
    w = {k: float(v) for k, v in weights.items() if k in streams and v >= 0}
    if not w or sum(w.values()) <= 0:
        w = {"lr": 1.0} if "lr" in streams else {next(iter(streams)): 1.0}
    total = sum(w.values())
    return float(np.clip(sum(streams[k] * v / total for k, v in w.items()), 0.0, 1.0))


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def template_decision(guidance: list[Guidance], flags: list[Flag], t_star: float, analog_max: float | None,
                      kind: str, reason: str | None) -> Decision:
    g = {x.stream: x for x in guidance}
    weights = {"lr": 0.5, "analog": 0.5} if g.get("analog") and g["analog"].p is not None else {"lr": 1.0}
    p = combine(guidance, weights)
    level = action_level(p, t_star, analog_max)
    parts = [f"The model gives {g['lr'].p:.2f} [G1]."]
    if g.get("analog") and g["analog"].p is not None:
        parts.append(f"The memory's analog nights give {g['analog'].p:.2f} [G2].")
    if g.get("recent_rate"):
        parts.append(f"The event rate over recent verified nights is {g['recent_rate'].p:.2f} [G3].")
    parts.append(f"The desk combines them with weights {', '.join(f'{k} {v:.2f}' for k, v in weights.items())} to {p:.2f}.")
    if flags:
        parts.append("Review flags: " + "; ".join(f.code for f in flags) + ".")
    parts.append(RECOMMENDATION[level])
    parts.append("This discussion was produced by the desk's template because the language model's reply did not pass validation.")
    return Decision(kind=kind, p=round(p, 4), level=level, recommendation=RECOMMENDATION[level], discussion=" ".join(parts),
                    cites=[x.id for x in guidance], handover_reason=reason, weights=weights)


def must_handover(guidance: list[Guidance], flags: list[Flag], round_: int) -> str | None:
    g = {x.stream: x for x in guidance}
    unresolved = [f for f in flags if not f.resolved]
    gap = (g.get("analog") and g["analog"].p is not None and abs(g["lr"].p - g["analog"].p) > HANDOVER_GAP)
    if round_ >= MAX_ROUNDS and gap and any(f.code in ("model_analog_disagree", "analog_split") for f in unresolved):
        return f"model and memory disagree by more than {HANDOVER_GAP:.1f} after {round_} review rounds"
    if any(f.code == "data_gap" and "GFS" in f.detail for f in unresolved) and round_ >= MAX_ROUNDS:
        return "the GFS run for tonight is missing after re-query"
    return None


def decide(chat: ChatClient, issue_date: str, regime_summary: str, evidence: list[EvidenceItem], guidance: list[Guidance],
           flags: list[Flag], t_star: float, round_: int, analog_max: float | None) -> tuple[Decision, LLMCall]:
    forced = must_handover(guidance, flags, round_)
    user = DUTY_LEAD_USER.format(issue_date=issue_date, round=round_, max_rounds=MAX_ROUNDS, regime_summary=regime_summary,
                                 evidence_table=evidence_table(evidence), guidance_table=guidance_table(guidance),
                                 flags_table=flags_table(flags), t_star=t_star)
    if forced:
        user += f"\n\nThe code has determined this case must be handed to a human: {forced}. Set kind to handover and explain in the discussion."
    messages = [Message(role="system", content=DUTY_LEAD_SYSTEM), Message(role="user", content=user)]
    allowed = {round(g.p, 2) for g in guidance if g.p is not None} | {round(g.p, 4) for g in guidance if g.p is not None} | {round(t_star, 2), round(2 * t_star, 2)}
    for g in guidance:
        if g.interval:
            allowed |= {round(g.interval[0], 2), round(g.interval[1], 2)}
        allowed |= numbers_in(g.note)  # analog counts and maxima named in the guidance notes
    if analog_max is not None:
        allowed |= {float(analog_max), round(float(analog_max))}
    validation = "ok"
    last = None
    all_problems: list[str] = []
    for attempt in range(2):
        r = chat.complete(messages, ULTRA, max_tokens=4000, temperature=0.2)  # reasoning goes before </think> and shares the budget
        last = r
        data = _parse_json(r.content)
        problems = []
        if not data or not isinstance(data.get("weights"), dict) or not isinstance(data.get("discussion"), str):
            problems.append("reply is not the required JSON with weights and discussion")
        else:
            kind = data.get("kind") if data.get("kind") in ("issue", "issue_with_caveat", "handover") else "issue"
            if forced:
                kind = "handover"
            p = combine(guidance, data["weights"])
            weight_numbers = {round(float(v), 2) for v in data["weights"].values() if isinstance(v, (int, float))}
            problems = validate_numbers(data["discussion"], evidence, allowed | weight_numbers | {round(p, 2), round(p, 4)})
            sentences = [s for s in re.split(r"(?<=[.!?])\s+", data["discussion"].strip()) if s]
            if not 5 <= len(sentences) <= 12:
                problems.append(f"discussion has {len(sentences)} sentences, need 6 to 10")
        if not problems:
            level = action_level(p, t_star, analog_max)
            cites = sorted(set(re.findall(r"\[(E\d+|G\d+)\]", data["discussion"])))
            dec = Decision(kind=kind, p=round(p, 4), level=level, recommendation=RECOMMENDATION[level], discussion=data["discussion"].strip(),
                           cites=cites, handover_reason=(data.get("handover_reason") or forced) if kind == "handover" else None,
                           weights={k: float(v) for k, v in data["weights"].items()})
            return dec, LLMCall(agent="DutyLead", model_requested=r.model_requested, provider_model=r.provider_model, latency_ms=r.latency_ms,
                                prompt_tokens=(r.usage or {}).get("prompt_tokens"), completion_tokens=(r.usage or {}).get("completion_tokens"),
                                cached=r.cached, validation=validation, problems=all_problems, reply_preview=r.content[:600])
        all_problems.extend(f"attempt {attempt + 1}: {p}" for p in problems)
        validation = "retried"
        messages.append(Message(role="assistant", content=r.content))
        messages.append(Message(role="user", content="Your reply failed validation: " + "; ".join(problems[:6]) +
                                ". Return the JSON again, using only numbers from the evidence and guidance, each followed by its id."))
    dec = template_decision(guidance, flags, t_star, analog_max, "handover" if forced else "issue_with_caveat", forced)
    return dec, LLMCall(agent="DutyLead", model_requested=ULTRA, provider_model=getattr(last, "provider_model", None),
                        latency_ms=getattr(last, "latency_ms", 0), cached=getattr(last, "cached", False), validation="fallback_template",
                        problems=all_problems, reply_preview=getattr(last, "content", "")[:600])
