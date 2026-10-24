# vog-desk

A forecast desk for volcanic smog (vog) at Pāhala, Hawaiʻi Island. Every evening at 18:00 HST it answers one question: **between now and noon tomorrow, will any hour at the Pāhala monitor reach 75 ppb of sulfur dioxide?** It gives a calibrated probability, a recommendation that follows the 2018 Hawaiʻi Schools SO2 Action Plan, and a discussion in which every number points at the evidence it came from. The next day it checks itself against the monitor, remembers the night, and tells its memory which analogs helped.

Built for the Open Agent Hackathon 2026 (GenAI Works), Track 3, Reasoning Architecture, by a team of one. Work in this repository started inside the build window (22 October 2026, 00:00 UTC). Status and honest numbers: [docs/DEVLOG.md](docs/DEVLOG.md); results on the held-out period: [docs/RESULTS.md](docs/RESULTS.md) (written once, at the end).

## Why a desk, not a model

Kīlauea has erupted in fountaining episodes since December 2024, 55 so far. Each one sends gas down the southwest flank into Kaʻū. In 2025 the Department of Health logged 25 one-hour SO2 exceedances at Pāhala, most between 02:00 and 09:00 HST, while families sleep with the windows shut and children get ready for school. The schools plan reacts after thirty minutes of elevated readings; nobody gives the evening-before warning.

The statistical model alone cannot do it. Fit on 2025, it learned that bad nights come with fountaining and light winds. In 2026 the eruption changed: episodes are shorter, pauses longer, and the gas reaches Pāhala between episodes on trade-wind nights, three times as often. On the development period (January to June 2026) the frozen model scores below climatology. A desk that remembers verified nights, retrieves analogs as of the issue time, checks its two guidance streams against each other, re-queries the data layer when they disagree, and hands over to a human when it cannot resolve the conflict is the thing being tested here. So is the honest question of whether it beats a dumb adaptive baseline (the event rate of the last 30 verified nights). See [docs/PREREG.md](docs/PREREG.md) for what was fixed before any model was fit, and the one logged change since.

## What a forecast may see

Every input carries the time it became public, and the forecast is cut at 18:00 HST:

| Source | Available at |
|---|---|
| Hourly SO2, AQS validated and AirNow preliminary | hour + 2 h (AirNow posts about 30 min after the hour) |
| HVO episode starts and ends | the first notice that published them |
| GFS 0.25° | the 18Z run of the issue day only |
| Hilo soundings | launch + 3 h; latest usable is 00Z |
| Memory (H-MEM) | a night only after its window closed and was verified |

The rules live in `src/vogdesk/asof.py`, in the Zetaris semantic layer as an `available_utc` column on every table, and in the tests, which run against Zetaris itself.

## How the desk works

```
run.py / POST /run
  EvidenceAgent   seven fixed as-of queries through Zetaris (run_sql rewrites every table with available_utc <= issue)
                  Nemotron Lightning writes the regime summary; a validator checks every number against the evidence
  ForecasterAgent G1 frozen logistic model  G2 ten analog nights from H-MEM (as of issue, Wilson interval)  G3 recent event rate
  Review          five checks in code: model vs memory gap, analog split, persistence contradiction, regime unseen, data gaps
                  on a flag: targeted re-query (upwind station, GFS 850 vs 925, episode status), then guidance again (max 2 rounds)
  DutyLead        Nemotron Ultra states weights over the streams and writes a cited discussion; code applies the PREREG action mapping
                  or hands the case to a human with a reason
  Verifier (D+1)  scores the night, writes it to H-MEM with its publication time, gives the analogs helpful/wrong feedback,
                  records forecast and verification in Postgres (read back through Zetaris)
```

LangGraph `StateGraph`, Pydantic state, JSONL trace per case under `logs/`, World Model case record in the memory sidecar. `--no-review` is the ablation (forecast 5).

## Results

Pre-registered before the first fit ([docs/PREREG.md](docs/PREREG.md), commit `8649b40`); thresholds frozen before the held-out run (`4b766f1`); the held-out period was scored once ([docs/RESULTS.md](docs/RESULTS.md)).

Held-out, 1 July to 21 October 2026, 102 valid nights, 39 events (base rate 0.38):

| forecast | Brier | BSS vs climatology | AUC | CSI at t* |
|---|---|---|---|---|
| climatology (2025 rate 0.059) | 0.341 | 0 | | |
| persistence | 0.455 | −0.32 | | 0.26 |
| model only (frozen on 2025) | 0.332 | +0.03 | 0.61 | 0.07 |
| desk without review | 0.224 | +0.34 | 0.66 | 0.41 |
| **full desk** | **0.215** | **+0.37** | **0.69** | **0.42** |
| recent-rate baseline (last 30 verified nights) | 0.246 | +0.28 | 0.40 | 0.38 |

Paired Brier difference, 7-day block bootstrap 90% interval: full desk minus model −0.117 [−0.157, −0.080]; full desk minus recent-rate baseline −0.031 [−0.048, −0.014]; full desk minus desk without review −0.009 [−0.019, +0.001]. The gain comes mostly from the memory model, the pre-registered pipeline refit each night on the verified nights the desk remembers; the review loop added a small, borderline improvement; the frozen 2025 model is near climatology because the eruption's regime changed (DEVLOG). Labels match the Department of Health's own exceedance list on 39 of 39 held-out nights. Development-period numbers (used for tuning) are in [docs/DEVLOG.md](docs/DEVLOG.md). Live forecasts since 23 October are committed under `live/` before the outcome is known and verified the next day.

## Run it

```bash
uv venv --python 3.12 && uv pip install -e ".[dev,model,agents,infra]"
.venv/bin/python -m pytest -q tests                       # 100+ tests, the Zetaris and sidecar ones skip when those are down
SAMPLE_MODE=true python run.py --issue 2025-09-02         # no keys: local Parquet, recorded model replies, in-process memory
python run.py --issue 2025-09-02                          # live: Zetaris, Nightshift, memory-service
python run.py --issue 2025-09-02 --no-review              # forecast 5
uvicorn api:app --port 8000                               # POST /run {"issue_date": "2025-09-02"}
docker build -t vog-desk . && docker run --rm vog-desk --issue 2025-09-02
```

Local data layer and Zetaris wiring: [infra/README.md](infra/README.md), then `python infra/zetaris/setup.py`. Memory sidecar: [memory-service/README.md](memory-service/README.md). Raw data is rebuilt by `scripts/fetch_*.py` and listed with SHA-256 in `data/MANIFEST.tsv`; derived Parquet is not committed.

## Sponsor technology

- **Zetaris** is the data layer. Eight sources (Parquet on a local S3 store, Postgres over JDBC) behind one Unified Semantic Layer, `pahala_exposure`, eight governed tables each with `available_utc`, eleven data quality rules (one of them flags exactly the two censored Ocean View hours of 3 Sep 2025 that DOH marks with an asterisk). Agents read only through `run_sql`. Scripts: `infra/zetaris/`; reflection for the judges: [docs/ZETARIS_REFLECTION.md](docs/ZETARIS_REFLECTION.md); evidence: `docs/evidence/zetaris-dq.txt`.
- **Meterless engines.** H-MEM and the World Model reference implementations run unchanged in `memory-service/` (conformance 13 of 13 and 8 of 8, `docs/evidence/conformance-*.txt`). The sidecar adds the as-of rule: a night is retrievable only after its verification time. The desk uses memory in a working feature: analog retrieval with a trace, verification write-back with source and confidence, trust-ledger feedback, night-level conflict detection with the 0.70 gate.
- **Meterless Nightshift** is the primary model provider (`src/vogdesk/llm.py`), with explicit model ids so every trace line names the model that answered: `nvidia/nemotron-3.5-lightning-30b-a3b` for evidence narration (thinking off) and `nvidia/nemotron-3-ultra-550b-a55b` for the DutyLead. Provider grade: [docs/evidence/provider-grade.md](docs/evidence/provider-grade.md).
- **NVIDIA Nemotron**: the two models above, through NVIDIA's free endpoint registered in Nightshift. `meterless.provider_model` is logged on every call.
- **Cursor** built the Nightshift provider block in `llm.py` (commit `f559fa7`, review in [docs/cursor/B-review.md](docs/cursor/B-review.md)). Everything else was built with Claude Code and Codex; all code was read and tested by the author.

## Pre-existing components

Declared: the Meterless reference engines (Apache-2.0, vendored unchanged, `memory-service/engines/UPSTREAM.txt`) and, for the demo replay, the author's Windward terrain scene (github.com/zeyusuntt/windward). All data is public (EPA, AirNow, USGS, DOH, IEM, NOAA).

## Limits

One station, one threshold, one training year with 21 events. An hourly mean of 75 ppb does not show that a 15-minute average reached the plan's yellow level. AirNow values after March 2026 are preliminary and rounded to whole ppb. The desk recommends; the school's designated person decides. Everything that did not work is in DEVLOG.
