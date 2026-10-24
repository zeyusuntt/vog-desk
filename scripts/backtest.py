"""Run the desk over a split, night by night, with memory that knows only earlier nights.

Memory is seeded with every verified night through the end of the split, each with the time its outcome
became public, so the as-of query at each issue time sees exactly the nights a desk running since 2025
would have remembered. After each case the Verifier scores it and gives the analogs feedback, as it
would have the next day. Results go to data/derived/backtest_<split>_<variant>.parquet and traces to
logs/backtest/<variant>/. Re-running skips nights already done.

Usage: .venv/bin/python scripts/backtest.py --split dev --variant full [--workers 3] [--limit N] [--data local|zetaris]
       variants: full (forecast 6), noreview (forecast 5)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from vogdesk import dataset, model as model_mod, sources  # noqa: E402
from vogdesk.desk.data import open_backend  # noqa: E402
from vogdesk.desk.graph import DEFAULT_T_STAR, DeskContext, run_case  # noqa: E402
from vogdesk.desk.verifier import verify_case  # noqa: E402
from vogdesk.llm import open_chat  # noqa: E402
from vogdesk.memory import FakeMemory  # noqa: E402
from vogdesk.trace import Trace  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=list(dataset.SPLITS), default="dev")
    ap.add_argument("--variant", choices=["full", "noreview"], default="full")
    ap.add_argument("--data", choices=["local", "zetaris"], default="local")
    ap.add_argument("--provider", choices=["nightshift", "sample"], default="nightshift")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--t-star", type=float, default=float(os.environ.get("T_STAR", DEFAULT_T_STAR)))
    ap.add_argument("--memory-path", type=Path, default=None)
    args = ap.parse_args()
    if args.split == "heldout" and "--i-am-sure" not in sys.argv:
        sys.exit("The held-out split is run once, after freezing. Pass --i-am-sure.")

    src = sources.load()
    X, lab = dataset.build(src, args.split)
    dates = [d.strftime("%Y-%m-%d") for d in lab.index]
    out_path = ROOT / "data" / "derived" / f"backtest_{args.split}_{args.variant}.parquet"
    done = set(pd.read_parquet(out_path)["issue_date_hst"]) if out_path.exists() else set()
    todo = [d for d in dates if d not in done][: args.limit]
    print(f"{args.split} {args.variant}: {len(dates)} nights, {len(done)} done, {len(todo)} to run")
    if not todo:
        return

    mem_path = args.memory_path or ROOT / "data" / "memory" / f"backtest_{args.split}_{args.variant}.json"
    mem_path.parent.mkdir(parents=True, exist_ok=True)
    memory = FakeMemory(mem_path)
    seeded = {t for r in memory.query("verified night", ["station:pahala"], pd.Timestamp.now(tz="UTC").to_pydatetime(), k=5000)
              for t in r.record.tags if t.startswith("night:")}
    if len(seeded) < 300:
        import subprocess
        end = dataset.SPLITS[args.split][1]
        subprocess.run([sys.executable, "scripts/seed_memory.py", "--through", end, "--memory", "fake", "--memory-path", str(mem_path)], cwd=ROOT, check=True)
        memory = FakeMemory(mem_path)

    backend = open_backend(args.data, src)
    chat = open_chat(args.provider)
    m = model_mod.load()
    log_dir = ROOT / "logs" / "backtest" / args.variant
    log_dir.mkdir(parents=True, exist_ok=True)

    def one(date: str) -> dict:
        ctx = DeskContext(backend=backend, sources=src, chat=chat, memory=memory, model=m, trace=Trace("init", log_dir / "init.jsonl"), t_star=args.t_star)
        try:
            state = run_case(ctx, date, review_enabled=(args.variant == "full"), case_id=f"bt-{args.split}-{args.variant}-{date}")
            ver = verify_case(state, src, memory, args.t_star, write_memory=False, feedback=True)  # nights are pre-seeded; only feedback
            g = {x["stream"]: x for x in state["guidance"]}
            dec = state["decision"]
            return {"issue_date_hst": date, "variant": args.variant, "p": dec["p"], "kind": dec["kind"], "level": dec["level"],
                    "p_model": g.get("lr", {}).get("p"), "p_analog": g.get("analog", {}).get("p"), "p_recent": g.get("recent_rate", {}).get("p"),
                    "n_analogs": len(g.get("analog", {}).get("analog_ids", [])), "flags": ",".join(sorted({f["code"] for f in state["flags"]})),
                    "review_round": state.get("review_round", 0), "weights": json.dumps(dec.get("weights", {})),
                    "llm_validation": ",".join(c["validation"] for c in state["llm_calls"]),
                    "llm_ms": sum(c["latency_ms"] for c in state["llm_calls"]),
                    "event": ver.event, "max_ppb": ver.observed_max_ppb, "outcome": ver.outcome, "brier": ver.brier,
                    "trace": str(ctx.trace.path), "error": None}
        except Exception as e:  # record the failure, keep going
            traceback.print_exc()
            return {"issue_date_hst": date, "variant": args.variant, "error": f"{type(e).__name__}: {str(e)[:300]}"}

    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(one, d): d for d in todo}
        for fut in as_completed(futures):
            row = fut.result()
            rows.append(row)
            ok = pd.DataFrame([r for r in rows if not r.get("error")])
            if out_path.exists():
                ok = pd.concat([pd.read_parquet(out_path), ok], ignore_index=True).drop_duplicates("issue_date_hst", keep="last")
            if len(ok):
                ok.sort_values("issue_date_hst").to_parquet(out_path, index=False)
            print(f"{row['issue_date_hst']}: " + (f"error {row['error']}" if row.get("error") else
                  f"p={row['p']:.2f} {row['kind']} {row['level']} event={row['event']} {row['outcome']} ({row['llm_ms']/1000:.0f}s llm)"), flush=True)
    errs = [r for r in rows if r.get("error")]
    print(f"done: {len(rows) - len(errs)} ok, {len(errs)} errors -> {out_path}")


if __name__ == "__main__":
    main()
