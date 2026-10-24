"""EvidenceAgent: runs the fixed as-of queries, writes evidence items, asks Lightning for words only.

Each evidence item carries the SQL (after the as-of rewrite), the query and row hashes and the latest
publication time among its rows, so a reader can re-run it and get the same answer. The model is asked
to summarise the regime from the numbers; a validator checks that every number in the reply exists in
the evidence and is followed by its id. Replies that fail are retried once and then replaced by a
template built from the evidence itself.
"""
from __future__ import annotations

import re
from datetime import datetime

import numpy as np
import pandas as pd

from vogdesk.asof import HOURLY_LAG, issue_time_utc
from vogdesk.desk.data import HVNP, OCEAN_VIEW, PAHALA, Backend, Rows
from vogdesk.desk.prompts import EVIDENCE_SYSTEM, EVIDENCE_USER
from vogdesk.desk.state import EvidenceItem, LLMCall
from vogdesk.features import BEARING_SUMMIT_TO_PAHALA, SECTOR_HALF_WIDTH, wind_parts
from vogdesk.llm import LIGHTNING, ChatClient, Message

LEADS = (10, 16, 22)
LEAD_HST = {10: "18:00 HST", 16: "00:00 HST", 22: "06:00 HST"}


def _fmt(x, nd=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "not available"
    return f"{x:.{nd}f}"


def _usable(rows: Rows, as_of: pd.Timestamp, hours: int) -> pd.DataFrame:
    df = rows.frame
    if df.empty:
        return df
    last = as_of - HOURLY_LAG
    return df[(df.time_utc <= last) & (df.time_utc > last - pd.Timedelta(hours=hours))]


def _item(eid: str, kind: str, rows: Rows, as_of: pd.Timestamp, summary: str, values: dict, dq: list[str] | None = None) -> EvidenceItem:
    return EvidenceItem(id=eid, kind=kind, as_of_utc=as_of, source=rows.source, sql=rows.sql, query_hash=rows.query_hash,
                        rows_hash=rows.rows_hash, available_utc=rows.available_utc, summary=summary,
                        values={k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in values.items()},
                        dq_flags=dq or [])


def so2_item(eid: str, backend: Backend, site_id: str, label: str, as_of: pd.Timestamp, hours: int) -> EvidenceItem:
    rows = backend.so2_rows(site_id, as_of - pd.Timedelta(hours=hours + 3), as_of)
    w = _usable(rows, as_of, hours)
    vals = {"site_id": site_id, "window_hours": hours, "n_hours": int(len(w))}
    dq = []
    if len(w):
        vals |= {"max_ppb": float(w.so2_ppb.max()), "mean_ppb": float(w.so2_ppb.mean()),
                 "latest_ppb": float(w.sort_values("time_utc").so2_ppb.iloc[-1]),
                 "latest_hour_hst": (w.time_utc.max().tz_convert(None) - pd.Timedelta(hours=10)).strftime("%H:%M"),
                 "preliminary": bool(w.preliminary.any()), "hours_at_or_above_75": int((w.so2_ppb >= 75).sum())}
        if (w.so2_ppb > 1000).any():
            dq.append("analyzer_upper_range")
        summary = (f"{label}: last {hours} usable hours, max {vals['max_ppb']:.1f} ppb, mean {vals['mean_ppb']:.1f} ppb, "
                   f"latest {vals['latest_ppb']:.1f} ppb at {vals['latest_hour_hst']} HST, {vals['n_hours']} of {hours} hours present"
                   + (", preliminary (AirNow)" if vals["preliminary"] else ", validated (AQS)")
                   + (", values above analyzer range flagged" if dq else ""))
    else:
        summary = f"{label}: no hourly values published in the last {hours} hours (data gap)"
    return _item(eid, "sql", rows, as_of, summary, vals, dq)


def episode_item(eid: str, backend: Backend, as_of: pd.Timestamp) -> EvidenceItem:
    rows = backend.episode_rows(as_of)
    e = rows.frame
    vals: dict = {"n_known": int(len(e))}
    if e.empty:
        return _item(eid, "notice", rows, as_of, "HVO episodes: none published yet", vals)
    e = e.sort_values("episode")
    last = e.iloc[-1]
    active = pd.isna(last.end_utc)
    vals |= {"last_episode": int(last.episode), "active_at_issue": bool(active),
             "start_hst": (pd.Timestamp(last.start_utc).tz_convert(None) - pd.Timedelta(hours=10)).strftime("%Y-%m-%d %H:%M")}
    if active:
        vals["hours_since_start"] = round((as_of - pd.Timestamp(last.start_utc)).total_seconds() / 3600, 1)
        summary = (f"HVO: episode {vals['last_episode']} fountaining, began {vals['start_hst']} HST, "
                   f"{vals['hours_since_start']:.1f} h ago, no end published")
    else:
        hours = (as_of - pd.Timestamp(last.end_utc)).total_seconds() / 3600
        vals |= {"hours_since_end": round(hours, 1), "days_since_end": round(hours / 24, 1),
                 "end_hst": (pd.Timestamp(last.end_utc).tz_convert(None) - pd.Timedelta(hours=10)).strftime("%Y-%m-%d %H:%M")}
        summary = (f"HVO: pause; episode {vals['last_episode']} ended {vals['end_hst']} HST, "
                   f"{vals['days_since_end']:.1f} days ago")
    return _item(eid, "notice", rows, as_of, summary, vals)


def gfs_item(eid: str, backend: Backend, issue_date: pd.Timestamp, as_of: pd.Timestamp) -> EvidenceItem:
    rows = backend.gfs_rows(issue_date, as_of)
    g = rows.frame
    if g.empty:
        return _item(eid, "forecast", rows, as_of, "GFS 18Z run: not available at issue (data gap)", {"rows": 0})
    g = g.drop_duplicates(["fxx", "var", "level", "point"], keep="last")  # the two archive files overlap on two days
    val = g.set_index(["fxx", "var", "level", "point"])["value"]
    get = lambda f, v, lv, p: float(val.get((f, v, lv, p), np.nan))
    vals: dict = {"run": "18Z of issue day", "rows": int(len(g))}
    parts = []
    for lead in LEADS:
        for lv, key in (("925 mb", "925"), ("850 mb", "850")):
            along, speed, frm = wind_parts(get(lead, "UGRD", lv, "summit"), get(lead, "VGRD", lv, "summit"))
            vals[f"summit_{key}_speed_ms_f{lead}"] = round(speed, 1)
            vals[f"summit_{key}_from_deg_f{lead}"] = round(frm)
            vals[f"summit_{key}_toward_pahala_ms_f{lead}"] = round(along, 1)
        sp10 = float(np.hypot(get(lead, "UGRD", "10 m above ground", "pahala"), get(lead, "VGRD", "10 m above ground", "pahala")))
        vals[f"pahala_10m_speed_ms_f{lead}"] = round(sp10, 1)
        vals[f"pahala_hpbl_m_f{lead}"] = round(get(lead, "HPBL", "surface", "pahala"))
        vals[f"summit_hpbl_m_f{lead}"] = round(get(lead, "HPBL", "surface", "summit"))
        parts.append(f"{LEAD_HST[lead]}: 925 hPa {vals[f'summit_925_speed_ms_f{lead}']} m/s from {vals[f'summit_925_from_deg_f{lead}']} deg, "
                     f"850 hPa {vals[f'summit_850_speed_ms_f{lead}']} m/s from {vals[f'summit_850_from_deg_f{lead}']} deg, "
                     f"Pahala 10 m {vals[f'pahala_10m_speed_ms_f{lead}']} m/s, boundary layer {vals[f'pahala_hpbl_m_f{lead}']} m")
    from_dir = BEARING_SUMMIT_TO_PAHALA - 180
    in_sector = [abs((vals[f"summit_925_from_deg_f{l}"] - from_dir + 180) % 360 - 180) <= SECTOR_HALF_WIDTH for l in LEADS]
    vals["sector_leads_toward_pahala"] = int(sum(in_sector))
    vals["mean_925_toward_pahala_ms"] = round(float(np.mean([vals[f"summit_925_toward_pahala_ms_f{l}"] for l in LEADS])), 1)
    summary = ("GFS 18Z, summit and Pahala points; " + "; ".join(parts) +
               f". {vals['sector_leads_toward_pahala']} of 3 leads have the 925 hPa wind blowing toward Pahala (from about 043 deg); "
               f"mean 925 hPa component toward Pahala {vals['mean_925_toward_pahala_ms']} m/s")
    return _item(eid, "forecast", rows, as_of, summary, vals)


def sounding_item(eid: str, backend: Backend, as_of: pd.Timestamp) -> EvidenceItem:
    rows = backend.sounding_rows(as_of)
    r = rows.frame.dropna(subset=["inversion_base_m"]) if not rows.frame.empty else rows.frame
    if r.empty:
        return _item(eid, "sounding", rows, as_of, "Hilo sounding: none in the last 18 hours (data gap)", {"n": 0})
    top = r.sort_values("valid_utc").iloc[-1]
    vals = {"launch_utc": pd.Timestamp(top.valid_utc).strftime("%Y-%m-%d %H:%MZ"), "inversion_base_m": float(top.inversion_base_m),
            "none_found": bool(top.inversion_base_m >= 4000)}
    summary = (f"Hilo sounding {vals['launch_utc']}: " +
               ("no temperature inversion found between 500 and 4000 m" if vals["none_found"]
                else f"inversion base at {vals['inversion_base_m']:.0f} m"))
    return _item(eid, "sounding", rows, as_of, summary, vals)


def collect_evidence(backend: Backend, issue_date: pd.Timestamp, as_of: pd.Timestamp | None = None,
                     start_id: int = 1, targets: list[str] | None = None) -> list[EvidenceItem]:
    """The fixed query set. `targets` restricts a re-query to named items."""
    as_of = as_of or issue_time_utc(issue_date)
    plan = {
        "pahala_24h": lambda eid: so2_item(eid, backend, PAHALA, "Pahala", as_of, 24),
        "pahala_6h": lambda eid: so2_item(eid, backend, PAHALA, "Pahala", as_of, 6),
        "hvnp_6h": lambda eid: so2_item(eid, backend, HVNP, "Volcanoes NP (near summit)", as_of, 6),
        "ocean_view_6h": lambda eid: so2_item(eid, backend, OCEAN_VIEW, "Ocean View (farther downwind)", as_of, 6),
        "episode": lambda eid: episode_item(eid, backend, as_of),
        "gfs": lambda eid: gfs_item(eid, backend, issue_date, as_of),
        "sounding": lambda eid: sounding_item(eid, backend, as_of),
    }
    names = targets or list(plan)
    items, n = [], start_id
    for name in names:
        item = plan[name](f"E{n}")
        item.values["query"] = name
        items.append(item)
        n += 1
    return items


def evidence_table(items: list[EvidenceItem]) -> str:
    return "\n".join(f"[{it.id}] {it.summary}" + (f" (DQ: {', '.join(it.dq_flags)})" if it.dq_flags else "") for it in items)


NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")
CITE = re.compile(r"\[(E\d+|G\d+|V\d+|D\d+)\]")


def numbers_in(text: str) -> set[float]:
    return {float(x) for x in NUMBER.findall(text)}


def allowed_numbers(items: list[EvidenceItem]) -> set[float]:
    out: set[float] = set()
    for it in items:
        out |= numbers_in(it.summary)
        for v in it.values.values():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v is not None and not (isinstance(v, float) and np.isnan(v)):
                out |= {float(v), round(float(v)), round(float(v), 1), round(float(v), 2)}
    out |= {75.0, 18.0, 12.0, 24.0, 6.0, 925.0, 850.0, 10.0, 15.0, 0.21, 0.11, 210.0, 3.0, 2.0, 1.0, 0.0}  # window, levels, thresholds
    return out


FREE_NUMBERS = {float(i) for i in range(0, 11)} | {75.0, 15.0, 18.0, 12.0, 24.0, 90.0, 50.0, 0.11, 0.21, 210.0, 1000.0}


def validate_numbers(text: str, items: list[EvidenceItem], extra_allowed: set[float] | None = None) -> list[str]:
    """Numbers in the text that are not in the evidence (or the free set of counts, plan constants and the
    caller's own numbers), plus sentences that use an evidence number without citing its id."""
    allowed = allowed_numbers(items) | (extra_allowed or set()) | FREE_NUMBERS
    close = lambda x, pool: any(abs(x - a) < 1e-9 for a in pool)
    problems = [f"number {x:g} is not in the evidence" for x in numbers_in(text) if not close(x, allowed)]
    free = FREE_NUMBERS | (extra_allowed or set())
    for sent in re.split(r"(?<=[.!?])\s+", text):
        nums = numbers_in(sent)
        if any(not close(x, free) for x in nums) and not CITE.search(sent):
            problems.append(f"sentence uses an evidence number without its [E#] or [G#] id: {sent[:80]}")
    return problems


def template_summary(items: list[EvidenceItem]) -> str:
    return " ".join(f"{it.summary} [{it.id}]." for it in items)


def summarise_regime(chat: ChatClient, issue_date: str, items: list[EvidenceItem]) -> tuple[str, LLMCall]:
    table = evidence_table(items)
    messages = [Message(role="system", content=EVIDENCE_SYSTEM),
                Message(role="user", content=EVIDENCE_USER.format(issue_date=issue_date, evidence_table=table))]
    validation = "ok"
    last_call = None
    all_problems: list[str] = []
    for attempt in range(2):
        r = chat.complete(messages, LIGHTNING, max_tokens=1500, temperature=0.1,
                          extra={"chat_template_kwargs": {"enable_thinking": False}})  # words only; thinking on starves the reply
        last_call = r
        problems = validate_numbers(r.content, items)
        if not problems:
            return r.content.strip(), LLMCall(agent="EvidenceAgent", model_requested=r.model_requested, provider_model=r.provider_model,
                                              latency_ms=r.latency_ms, prompt_tokens=(r.usage or {}).get("prompt_tokens"),
                                              completion_tokens=(r.usage or {}).get("completion_tokens"), cached=r.cached, validation=validation,
                                              problems=all_problems, reply_preview=r.content[:600])
        all_problems.extend(f"attempt {attempt + 1}: {p}" for p in problems)
        validation = "retried"
        messages.append(Message(role="assistant", content=r.content))
        messages.append(Message(role="user", content="Your summary failed validation: " + "; ".join(problems[:5]) +
                                ". Rewrite it using only numbers from the table, each followed by its [E#]."))
    call = LLMCall(agent="EvidenceAgent", model_requested=LIGHTNING, provider_model=getattr(last_call, "provider_model", None),
                   latency_ms=getattr(last_call, "latency_ms", 0), cached=getattr(last_call, "cached", False), validation="fallback_template",
                   problems=all_problems, reply_preview=getattr(last_call, "content", "")[:600])
    return template_summary(items), call
