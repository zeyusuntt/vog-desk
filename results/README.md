# Per-night forecasts behind docs/RESULTS.md and docs/DEVLOG.md

One row per issue night, frozen desk (version 4, git tag freeze-v4), produced by `scripts/backtest.py`.

- `heldout_full_v4.csv`, `heldout_noreview_v4.csv`: the held-out period (1 July to 21 October 2026), run once after the freeze. Forecast 6 and forecast 5.
- `dev_full_v4.csv`, `dev_noreview_v4.csv`: the development period (January to June 2026), used for tuning. Version 4 on dev is derived from the version-3 run by applying the flag gate to the recorded adjustments (`scripts/derive_v4.py`; column `derived_from`).

Columns: `p` final probability; `base_p` the code-set base (memory model when available); `adjustment` the DutyLead's applied adjustment (`adjustment_proposed` before the flag gate, dev only); `kind` issue, issue_with_caveat or handover; `level` the schools-plan action at t* = 0.18; `p_model` frozen 2025 model (forecast 4); `p_memory` memory model; `p_recent` the desk's G3 stream as it ran (see the note below); `p_forecast7` the pre-registered forecast 7, the event rate over the last 30 valid nights, computed from the labels; `p_analog` shrunk analog rate; `flags` review flags raised; `review_round` re-query rounds; `event`, `max_ppb` the observed outcome; `outcome` at t*; `brier` the night's Brier contribution; `llm_validation` per model call (ok, retried, fallback_template); `llm_ms` total model latency.

Forecasts 1 to 3 and 7 are recomputed from the data by `scripts/evaluate.py`. The full traces (JSONL, one per case) are under `logs/backtest/` locally and are not committed for size; the three recorded cases under `samples/` carry complete traces.

Note on `p_recent`: the desk's G3 stream asked memory for its 400 most relevant records and took the newest 30 of those. Once memory held more than 400 nights, that was not the last 30 nights. On the held-out nights it differs from forecast 7 on every night (mean absolute gap 0.16). The base probability does not use G3, so `base_p` and the scored `p` are as the frozen desk produced them; G3 reached the duty lead's reading and the analog shrinkage prior. Found after the held-out scoring, fixed for the live desk; see docs/DEVLOG.md, 24 October.
