"""Issue tonight's forecast at 18:00 HST and write it to live/<issue date>/ before the outcome is known.

Steps: refresh the raw sources that can have changed since the last run (AirNow hours, the GFS 18Z
run of the issue day, recent Hilo soundings, HVO notices), rebuild the derived tables they feed,
compute the pre-registered features as of 18:00 HST, run the frozen model and write forecast.json.
The desk (agents, memory, review) is not wired in yet; until it is, every file says `kind: model_only`.

Usage: .venv/bin/python scripts/live.py [--issue 2026-10-23] [--no-fetch] [--out live]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vogdesk import dataset, model, sources  # noqa: E402
from vogdesk.asof import HST, gfs_run_for, issue_time_utc  # noqa: E402
from vogdesk.baselines import episode_rule, persistence  # noqa: E402
from vogdesk.features import FEATURES, features_for  # noqa: E402

PY = sys.executable


def run(cmd: list[str], check: bool = True) -> int:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, check=check).returncode


def refresh(issue: pd.Timestamp) -> dict:
    """Fetch what the issue needs. Failures are recorded, not hidden; the forecast then says what was missing."""
    status = {}
    day = issue.strftime("%Y-%m-%d")
    end = (issue + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    try:
        run([PY, "scripts/fetch_airnow.py", day, end])
        run([PY, "-m", "vogdesk.ingest.airnow", "data/raw/airnow", "data/derived/airnow_hawaii.parquet"])
        status["airnow"] = "ok"
    except subprocess.CalledProcessError as e:
        status["airnow"] = f"failed: {e}"
    try:
        run([PY, "scripts/fetch_gfs.py", day, day, "4"])
        status["gfs"] = "ok"
    except subprocess.CalledProcessError as e:
        status["gfs"] = f"failed: {e}"
    try:
        start = (issue - pd.Timedelta(days=20)).strftime("%Y-%m-%d")
        run([PY, "scripts/fetch_raob.py", start, end])
        status["raob"] = "ok"
    except subprocess.CalledProcessError as e:
        status["raob"] = f"failed: {e}"
    try:
        run([PY, "scripts/load_minio.py"])  # Zetaris reads the S3 copies; refresh them after every rebuild
        status["s3"] = "ok"
    except subprocess.CalledProcessError as e:
        status["s3"] = f"failed: {e}"
    try:
        run([PY, "scripts/fetch_hans.py", "2024-12-01"])
        run([PY, "-m", "vogdesk.ingest.hans", "data/raw/hans/hi3_notices.json", "data/derived"])
        run(["curl", "-sfL", "-A", "vog-desk/0.1 (research)", "-o", "data/raw/usgs/eruption-information.html",
             "https://www.usgs.gov/volcanoes/kilauea/science/eruption-information"])
        run([PY, "-m", "vogdesk.ingest.usgs_episodes", "data/raw/usgs/eruption-information.html",
             "data/derived/episodes.parquet", "data/derived/episodes_master.parquet"])
        status["episodes"] = "ok"
    except subprocess.CalledProcessError as e:
        status["episodes"] = f"failed: {e}"
    return status


def data_basis(src: sources.Sources, issue: pd.Timestamp, t: pd.Timestamp) -> dict:
    """What the forecast actually saw, with the latest timestamp per source, so the cutoff is auditable."""
    pah = src.so2[(src.so2.site_id == sources.SITES["pahala"]) & (src.so2.available_utc <= t)]
    g = src.gfs[(src.gfs.run_init_utc == gfs_run_for(issue)) & (src.gfs.available_utc <= t)]
    r = src.raob[src.raob.available_utc <= t]
    ep = src.episodes[src.episodes.start_known_utc <= t]
    return {
        "issue_utc": t.isoformat(),
        "pahala_last_hour_utc": pah.time_utc.max().isoformat() if len(pah) else None,
        "pahala_last_hour_hst": (pah.time_utc.max().tz_convert(None) - HST).isoformat() if len(pah) else None,
        "pahala_last_value_ppb": float(pah.sort_values("time_utc").so2_ppb.iloc[-1]) if len(pah) else None,
        "pahala_source_last": str(pah.sort_values("time_utc").source.iloc[-1]) if len(pah) else None,
        "gfs_run_init_utc": gfs_run_for(issue).isoformat(),
        "gfs_rows_seen": int(len(g)),
        "raob_latest_launch_utc": r.valid_utc.max().isoformat() if len(r) else None,
        "last_known_episode": int(ep.episode.max()) if len(ep) else None,
        "last_known_episode_end_utc": ep.end_utc.max().isoformat() if len(ep) and ep.end_utc.notna().any() else None,
        "max_available_utc_used": max(x for x in [pah.available_utc.max() if len(pah) else t - pd.Timedelta(days=9),
                                                   g.available_utc.max() if len(g) else t - pd.Timedelta(days=9),
                                                   r.available_utc.max() if len(r) else t - pd.Timedelta(days=9)]).isoformat(),
    }


def run_desk(issue: pd.Timestamp, memory_path: str) -> dict:
    """Run the LangGraph desk for the issue date; failures are recorded, never hidden."""
    import shutil
    try:
        sys.path.insert(0, str(ROOT))
        from run import build_context
        from vogdesk.desk.graph import DEFAULT_T_STAR, run_case
        ctx = build_context("zetaris", "nightshift", "http", Path(memory_path), float(os.environ.get("T_STAR", DEFAULT_T_STAR)))
        state = run_case(ctx, issue.strftime("%Y-%m-%d"), review_enabled=True, case_id=f"live-{issue:%Y-%m-%d}")
        out_dir = ROOT / "live" / issue.strftime("%Y-%m-%d")
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(ctx.trace.path, out_dir / "trace.jsonl")
        try:
            from vogdesk.institutional import record_forecast
            state["trace_path"] = str(out_dir / "trace.jsonl")
            record_forecast(state, forecast_kind="full_desk")
        except Exception as e:
            print("postgres write failed:", type(e).__name__)
        return {"case_id": state["case_id"], "decision": state["decision"], "guidance": state["guidance"], "flags": state["flags"],
                "review_round": state.get("review_round"), "llm_calls": state["llm_calls"], "data_backend": state["data_backend"],
                "regime_summary": state.get("regime_summary"), "trace": "trace.jsonl"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}


def main() -> None:
    ap = argparse.ArgumentParser()
    now_hst = datetime.now(timezone.utc) - HST.to_pytimedelta()
    ap.add_argument("--issue", default=now_hst.strftime("%Y-%m-%d"), help="issue date (HST), default today")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--out", default="live")
    ap.add_argument("--desk", action="store_true", help="also run the full desk (Zetaris, Nightshift, memory) and record its decision")
    ap.add_argument("--memory-path", default="data/memory/live.json", help="in-process memory file when the sidecar is down")
    args = ap.parse_args()
    issue = pd.Timestamp(args.issue)
    t = issue_time_utc(issue)
    generated = datetime.now(timezone.utc)
    status = {} if args.no_fetch else refresh(issue)

    src = sources.load()
    m = model.load()
    f = features_for(src, issue)
    X = pd.DataFrame([f]).set_index("issue_date_hst")
    p_raw = float(m.raw_probability(X)[0])
    p = float(m.probability(X)[0])
    pers = persistence(sources.station_series(src, "pahala"), pd.DatetimeIndex([issue]), dataset.THRESHOLD_PPB).iloc[0]
    epr = episode_rule(src.episodes, pd.DatetimeIndex([issue])).iloc[0]
    model_json = (model.MODEL_DIR / "lr_2025.json")
    out = {
        "kind": "model_only",
        "note": ("Model-only forecast: the frozen 2025 logistic regression with isotonic calibration. The desk "
                 "(evidence, analog memory, duty-lead review) is not wired in yet. The 2025 model under-forecast the "
                 "January to June 2026 development period (docs/DEVLOG.md); read the probability with that in mind."),
        "issue_date_hst": issue.strftime("%Y-%m-%d"),
        "issue_utc": t.isoformat(),
        "generated_utc": generated.isoformat(timespec="seconds"),
        "generated_before_issue": bool(generated <= t.to_pydatetime()),
        "question": "Any hourly SO2 >= 75 ppb at Pahala (AQS 15-001-2016) between 18:00 HST tonight and 12:00 HST tomorrow",
        "probability": round(p, 4),
        "probability_raw_uncalibrated": round(p_raw, 4),
        "reference": {"climatology_2025": dataset.CLIMATOLOGY, "persistence": None if pd.isna(pers) else float(pers),
                      "episode_rule": float(epr)},
        "recommendation": "withheld: the action threshold t* is not frozen yet (docs/PREREG.md, Frozen values)",
        "features": {k: (None if pd.isna(f[k]) else round(float(f[k]), 4)) for k in FEATURES},
        "features_outside_training_range": m.outside_training_range(pd.Series(f)),
        "data_basis": data_basis(src, issue, t),
        "refresh_status": status,
        "model": {"file": model_json.name, "sha256": hashlib.sha256(model_json.read_bytes()).hexdigest()[:16],
                  "fitted_on": m.fitted_on, "C": m.C, "n_train": m.n_train, "n_events": m.n_events,
                  "git_head_at_training": m.notes.get("git_head")},
        "verification": None,
    }
    if args.desk:
        out["desk"] = run_desk(issue, args.memory_path)
        if out["desk"].get("decision"):
            out["kind"] = "full_desk"
            out["note"] = ("Full desk forecast: evidence through Zetaris, model and memory guidance, DutyLead review on Nemotron Ultra. "
                           "The model-only probability is kept alongside for comparison (forecast 4 versus 6).")
    out_dir = ROOT / args.out / issue.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "forecast.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: out[k] for k in ("issue_date_hst", "probability", "probability_raw_uncalibrated", "reference",
                                           "features_outside_training_range", "refresh_status")}, indent=1, default=str))
    print("data basis:", json.dumps(out["data_basis"], indent=1, default=str))
    print(f"wrote {out_dir / 'forecast.json'}")


if __name__ == "__main__":
    main()
