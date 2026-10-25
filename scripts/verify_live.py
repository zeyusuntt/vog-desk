"""Verify a live forecast the next day: fetch the night's hours, score the case, remember it, record it.

Run at or after 12:00 HST on D+1 (the window closes then; AirNow posts the last hour about 30 minutes
later, and the DOH stations can lag several hours, so 15:00 HST is a safer time). Writes `verification`
into live/<D>/forecast.json, the night into H-MEM (sidecar if up, else the in-process file), the
forecast and verification rows into Postgres, and `live/index.json`. Commit the result locally.

Usage: .venv/bin/python scripts/verify_live.py --issue 2026-10-23 [--no-fetch]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from vogdesk import sources  # noqa: E402
from vogdesk.desk.graph import DEFAULT_T_STAR  # noqa: E402
from vogdesk.desk.state import Verification  # noqa: E402
from vogdesk.desk.verifier import observe, outcome_of, verification_time  # noqa: E402
from vogdesk.analogs import night_record  # noqa: E402
from vogdesk.memory import FakeMemory, HttpMemory, MemoryUnavailable  # noqa: E402

PY = sys.executable


def refresh(issue: pd.Timestamp) -> None:
    end = (issue + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    subprocess.run([PY, "scripts/fetch_airnow.py", issue.strftime("%Y-%m-%d"), end], cwd=ROOT, check=True)
    subprocess.run([PY, "-m", "vogdesk.ingest.airnow", "data/raw/airnow", "data/derived/airnow_hawaii.parquet"], cwd=ROOT, check=True)
    subprocess.run([PY, "scripts/load_minio.py"], cwd=ROOT, check=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", required=True)
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--t-star", type=float, default=float(os.environ.get("T_STAR", DEFAULT_T_STAR)))
    args = ap.parse_args()
    issue = pd.Timestamp(args.issue)
    path = ROOT / "live" / issue.strftime("%Y-%m-%d") / "forecast.json"
    fc = json.loads(path.read_text())
    if not args.no_fetch:
        refresh(issue)
    src = sources.load()
    max_ppb, n_hours, event = observe(src, issue)
    desk = fc.get("desk") or {}
    decision = desk.get("decision") or {"kind": "issue", "p": fc["probability"]}
    outcome = outcome_of(decision, event, args.t_star)
    brier = None if event is None else float((decision["p"] - float(event)) ** 2)
    model_brier = None if event is None else float((fc["probability"] - float(event)) ** 2)
    ver = Verification(observed_max_ppb=max_ppb, n_hours=n_hours, event=event, outcome=outcome, brier=brier,
                       verified_utc=datetime.now(timezone.utc))
    # Remember the night (features come from the forecast file; outcome from the monitor).
    feats = {k: (float("nan") if v is None else v) for k, v in (fc.get("features") or {}).items()}
    g = {x["stream"]: x for x in desk.get("guidance", [])}
    rec = night_record(issue.strftime("%Y-%m-%d"), feats, bool(event) if event is not None else False, max_ppb or 0.0, n_hours,
                       source=f"verifier:live:{issue:%Y-%m-%d}", available_utc=verification_time(issue).to_pydatetime(),
                       p_model=fc.get("probability"), p_analog=g.get("analog", {}).get("p"))
    rec.metadata |= {"decision_p": decision["p"], "outcome": outcome, "p_memory_model": g.get("memory_model", {}).get("p")}
    memory_note = "not written (window not valid)"
    if event is not None:
        try:
            mem = HttpMemory(os.environ.get("MEMORY_URL", "http://127.0.0.1:8790"))
            if not mem.ping():
                raise MemoryUnavailable("sidecar down")
            ver.memory_record_id = mem.add(rec); memory_note = "sidecar"
        except MemoryUnavailable:
            mem = FakeMemory(ROOT / "data" / "memory" / "live.json")
            ver.memory_record_id = mem.add(rec); memory_note = "in-process file (sidecar down)"
    pg = False
    try:
        from vogdesk.institutional import record_forecast, record_verification
        case_id = desk.get("case_id") or f"live-{issue:%Y-%m-%d}-model-only"
        state = {"case_id": case_id, "issue_date_hst": fc["issue_date_hst"], "issue_utc": fc["issue_utc"],
                 "decision": decision | {"level": decision.get("level", "normal"), "discussion": decision.get("discussion", fc.get("note", ""))},
                 "trace_path": str(path.parent / "trace.jsonl")}
        pg = record_forecast(state, forecast_kind=fc.get("kind", "model_only")) and record_verification(case_id, ver, "airnow")
    except Exception as e:
        print("postgres:", type(e).__name__, str(e)[:120])
    # The 18 hourly values of the window, for the night strip on the page (hour-beginning HST).
    t0 = pd.Timestamp(fc["issue_utc"])
    series = sources.station_series(src, "pahala")
    hours = []
    for k in range(18):
        t = t0 + pd.Timedelta(hours=k)
        v = series.get(t)
        hours.append({"hst": (t.tz_convert(None) - pd.Timedelta(hours=10)).strftime("%H:%M"),
                      "ppb": None if v is None or pd.isna(v) else float(v)})
    fc["verification"] = ver.model_dump(mode="json") | {"model_only_brier": model_brier, "memory": memory_note, "postgres": bool(pg),
                                                         "label_source": "airnow preliminary", "t_star_used": args.t_star, "hours": hours}
    path.write_text(json.dumps(fc, indent=1, default=str))
    try:  # refresh the night scene with the measured hours at all three monitors
        from vogdesk.scene import build_scene
        (path.parent / "scene.json").write_text(json.dumps(build_scene(src, issue), indent=1, ensure_ascii=False))
    except Exception as e:
        print("scene:", type(e).__name__, str(e)[:120])
    index = ROOT / "live" / "index.json"
    dates = sorted({p.name for p in (ROOT / "live").iterdir() if p.is_dir() and (p / "forecast.json").exists()}, reverse=True)
    index.write_text(json.dumps(dates))
    print(json.dumps(fc["verification"], indent=1, default=str))


if __name__ == "__main__":
    main()
