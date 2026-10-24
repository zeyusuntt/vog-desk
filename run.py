"""Run the vog desk for one issue date and print the decision.

    python run.py --issue 2025-09-02                       # Zetaris data, Nightshift models, sidecar memory if up
    python run.py --issue 2025-09-02 --no-review           # forecast 5: no re-query loop
    SAMPLE_MODE=true python run.py --issue 2025-09-02      # recorded model replies, local Parquet, in-process memory

Outputs: the decision JSON on stdout, the JSONL trace under logs/<case_id>.jsonl, the case record in the
World Model. Secrets are read from .env inside the app and never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from vogdesk import model as model_mod  # noqa: E402
from vogdesk import sources  # noqa: E402
from vogdesk.desk.data import open_backend  # noqa: E402
from vogdesk.desk.graph import DEFAULT_T_STAR, DeskContext, run_case  # noqa: E402
from vogdesk.llm import open_chat  # noqa: E402
from vogdesk.memory import FakeMemory, HttpMemory  # noqa: E402
from vogdesk.trace import Trace  # noqa: E402


SAMPLE_SEED = ROOT / "samples" / "memory_seed.json"


def _scratch_copy(path: Path) -> Path:
    """The committed memory seed is never written to: the desk works on a temporary copy."""
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="vogdesk-mem-")) / path.name
    shutil.copy(path, tmp)
    return tmp


def build_context(data: str, provider: str | None, memory: str, memory_path: Path | None, t_star: float,
                  sample_root: Path | None = None) -> DeskContext:
    src = sources.load()
    if memory == "fake" and memory_path is None and sources.sample_mode():
        memory_path = SAMPLE_SEED
    if memory_path is not None and Path(memory_path).resolve().is_relative_to(ROOT / "samples"):
        memory_path = _scratch_copy(Path(memory_path))
    backend = open_backend(data, src)
    chat = open_chat(provider, sample_root=sample_root)
    if memory == "http":
        mem = HttpMemory(os.environ.get("MEMORY_URL", "http://127.0.0.1:8790"))
        if not mem.ping():
            print("memory-service not reachable; falling back to the in-process fake", file=sys.stderr)
            mem = FakeMemory(memory_path)
    else:
        mem = FakeMemory(memory_path)
    return DeskContext(backend=backend, sources=src, chat=chat, memory=mem, model=model_mod.load(), trace=Trace("init"), t_star=t_star)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", required=True, help="issue date, HST, e.g. 2025-09-02")
    sample = os.environ.get("SAMPLE_MODE", "false").lower() in ("1", "true", "yes")
    ap.add_argument("--data", choices=["zetaris", "local"], default="local" if sample else "zetaris")
    ap.add_argument("--provider", choices=["nightshift", "sample"], default="sample" if sample else "nightshift")
    ap.add_argument("--memory", choices=["http", "fake"], default="fake" if sample else "http")
    ap.add_argument("--memory-path", type=Path, default=None, help="JSON file for the in-process memory")
    ap.add_argument("--no-review", action="store_true", help="forecast 5: skip the re-query loop")
    ap.add_argument("--t-star", type=float, default=float(os.environ.get("T_STAR", DEFAULT_T_STAR)))
    ap.add_argument("--checkpoint", type=Path, default=None, help="SQLite file for LangGraph checkpoints")
    args = ap.parse_args()
    ctx = build_context(args.data, args.provider, args.memory, args.memory_path, args.t_star)
    state = run_case(ctx, args.issue, review_enabled=not args.no_review, checkpoint_db=args.checkpoint)
    out = {"case_id": state["case_id"], "issue_date_hst": state["issue_date_hst"], "data_backend": state["data_backend"],
           "decision": state["decision"], "guidance": state["guidance"], "flags": state["flags"], "review_round": state.get("review_round"),
           "llm_calls": state["llm_calls"], "trace": str(ctx.trace.path)}
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
