"""Record the model replies for the SAMPLE_MODE cases so a judge can run the desk with no keys.

Runs the full desk on local Parquet with the in-process memory seeded through the night before each
case, through Nightshift with RECORD_SAMPLES on, so every reply is saved under samples/llm/<key>.json.
The replay key is a hash of the exact messages, so the same t* and the same prompts must be used when
replaying; `samples/manifest.json` records them. Three cases by default: an event night with a re-query,
a quiet night, and a night where the streams conflict.

Usage: .venv/bin/python scripts/record_samples.py [--dates 2026-05-07 2026-03-02 2026-06-16] [--t-star 0.19]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
os.environ["RECORD_SAMPLES"] = "1"
os.environ["SAMPLE_ROOT"] = str(ROOT / "samples" / "llm")

from run import build_context  # noqa: E402
from vogdesk.desk.graph import run_case  # noqa: E402

DEFAULT_DATES = ["2026-05-07", "2026-03-02", "2026-06-16"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", nargs="*", default=DEFAULT_DATES)
    ap.add_argument("--t-star", type=float, default=float(os.environ.get("T_STAR", 0.19)))
    args = ap.parse_args()
    mem_path = ROOT / "samples" / "memory_seed.json"
    if not mem_path.exists():
        subprocess.run([sys.executable, "scripts/seed_memory.py", "--through", "2026-06-30", "--memory", "fake", "--memory-path", str(mem_path)],
                       cwd=ROOT, check=True)
    manifest = {"t_star": args.t_star, "cases": [], "memory_seed": "samples/memory_seed.json",
                "note": "Replay with SAMPLE_MODE=true and T_STAR equal to t_star; the replay key hashes the exact prompt."}
    for d in args.dates:
        for review in (True, False):
            ctx = build_context("local", "nightshift", "fake", mem_path, args.t_star)
            state = run_case(ctx, d, review_enabled=review, case_id=f"sample-{d}-{'full' if review else 'noreview'}")
            (ROOT / "samples" / "cases").mkdir(parents=True, exist_ok=True)
            out = ROOT / "samples" / "cases" / f"{state['case_id']}.json"
            out.write_text(json.dumps({k: state[k] for k in ("case_id", "issue_date_hst", "decision", "guidance", "flags", "llm_calls", "review_round")},
                                      indent=1, default=str))
            manifest["cases"].append({"date": d, "review": review, "case": str(out.relative_to(ROOT)), "trace": str(ctx.trace.path.relative_to(ROOT)),
                                      "decision": state["decision"]["kind"], "p": state["decision"]["p"], "level": state["decision"]["level"]})
            print(d, "review" if review else "no review", "->", state["decision"]["kind"], state["decision"]["p"], state["decision"]["level"])
    (ROOT / "samples" / "manifest.json").write_text(json.dumps(manifest, indent=1))
    n = len(list((ROOT / "samples" / "llm").glob("*.json")))
    print(f"recorded {n} model replies under samples/llm; manifest written")


if __name__ == "__main__":
    main()
