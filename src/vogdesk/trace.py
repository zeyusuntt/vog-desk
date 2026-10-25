"""JSONL trace of a case, one line per step, readable without LangGraph.

Schema: {case_id, ts, agent, step, inputs_hash, sql[], llm_call,
output, duration_ms}. Traces live under logs/<case_id>.jsonl; the live desk also copies the file next
to its forecast. Nothing here prints secrets: SQL is logged after the as-of rewrite, never credentials.
"""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "logs"


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


class Trace:
    def __init__(self, case_id: str, path: Path | None = None):
        self.case_id = case_id
        self.path = path or (LOG_DIR / f"{case_id}.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.n = 0

    def write(self, agent: str, step: str, inputs=None, output=None, sql: list[str] | None = None,
              llm_call: dict | None = None, duration_ms: int = 0, **extra) -> dict:
        line = {"case_id": self.case_id, "seq": self.n, "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "agent": agent, "step": step, "inputs_hash": _hash(inputs) if inputs is not None else None,
                "sql": sql or [], "llm_call": llm_call, "output": output, "duration_ms": duration_ms}
        line.update(extra)
        with open(self.path, "a") as fh:
            fh.write(json.dumps(line, default=str) + "\n")
        self.n += 1
        return line

    @contextmanager
    def step(self, agent: str, step: str, inputs=None):
        """Usage: with trace.step("EvidenceAgent", "recent_so2", inputs) as rec: ...; rec["output"] = ..."""
        rec: dict = {"sql": [], "llm_call": None, "output": None}
        t0 = time.perf_counter()
        try:
            yield rec
        finally:
            self.write(agent, step, inputs=inputs, output=rec.get("output"), sql=rec.get("sql"),
                       llm_call=rec.get("llm_call"), duration_ms=int((time.perf_counter() - t0) * 1000),
                       **{k: v for k, v in rec.items() if k not in ("sql", "llm_call", "output")})

    def lines(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(ln) for ln in self.path.read_text().splitlines() if ln.strip()]
