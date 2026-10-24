# Per-night forecasts behind docs/RESULTS.md and docs/DEVLOG.md

One row per issue night, frozen desk (version 4, commit 4b766f1), produced by `scripts/backtest.py`.

- `heldout_full_v4.csv`, `heldout_noreview_v4.csv`: the held-out period (1 July to 21 October 2026), run once after the freeze. Forecast 6 and forecast 5.
- `dev_full_v4.csv`, `dev_noreview_v4.csv`: the development period (January to June 2026), used for tuning. Version 4 on dev is derived from the version-3 run by applying the flag gate to the recorded adjustments (`scripts/derive_v4.py`; column `derived_from`).

Columns: `p` final probability; `base_p` the code-set base (memory model when available); `adjustment` the DutyLead's applied adjustment (`adjustment_proposed` before the flag gate, dev only); `kind` issue, issue_with_caveat or handover; `level` the schools-plan action at t* = 0.18; `p_model` frozen 2025 model (forecast 4); `p_memory` memory model; `p_recent` recent rate; `p_analog` shrunk analog rate; `flags` review flags raised; `review_round` re-query rounds; `event`, `max_ppb` the observed outcome; `outcome` at t*; `brier` the night's Brier contribution; `llm_validation` per model call (ok, retried, fallback_template); `llm_ms` total model latency.

Forecasts 1 to 3 and 7 are recomputed from the data by `scripts/evaluate.py`. The full traces (JSONL, one per case) are under `logs/backtest/` locally and are not committed for size; the three recorded cases under `samples/` carry complete traces.
