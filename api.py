"""POST /run: the same desk as run.py, behind FastAPI.

    uvicorn api:app --port 8000
    curl -s localhost:8000/run -H 'content-type: application/json' -d '{"issue_date": "2025-09-02"}'

Request: {"issue_date": "YYYY-MM-DD", "review": true, "data": "zetaris"|"local", "provider": "nightshift"|"sample"}
Response: the decision, guidance, flags, model calls and the trace path. SAMPLE_MODE=true makes the
defaults local data, recorded model replies and in-process memory, so no key is needed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from run import build_context  # noqa: E402
from vogdesk.desk.graph import DEFAULT_T_STAR, run_case  # noqa: E402

SAMPLE = os.environ.get("SAMPLE_MODE", "false").lower() in ("1", "true", "yes")
app = FastAPI(title="vog-desk", version="0.1.0", description="Next-night SO2 exposure forecast desk for Pahala, Hawaii Island")


class RunRequest(BaseModel):
    issue_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    review: bool = True
    data: str = "local" if SAMPLE else "zetaris"
    provider: str = "sample" if SAMPLE else "nightshift"
    memory: str = "fake" if SAMPLE else "http"
    t_star: float = float(os.environ.get("T_STAR", DEFAULT_T_STAR))


@app.get("/health")
def health() -> dict:
    return {"ok": True, "sample_mode": SAMPLE}


@app.post("/run")
def run(req: RunRequest) -> dict:
    try:
        ctx = build_context(req.data, req.provider, req.memory, None, req.t_star)
        state = run_case(ctx, req.issue_date, review_enabled=req.review)
    except Exception as e:  # surface the reason, never a stack trace with paths
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)[:300]}") from e
    return {"case_id": state["case_id"], "issue_date_hst": state["issue_date_hst"], "data_backend": state["data_backend"],
            "decision": state["decision"], "guidance": state["guidance"], "flags": state["flags"],
            "review_round": state.get("review_round"), "llm_calls": state["llm_calls"], "trace": str(ctx.trace.path)}
