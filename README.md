# vog-desk

A forecast desk for volcanic smog (vog) at Pāhala, Hawaiʻi Island. Every evening at 18:00 HST it answers one question: **between now and noon tomorrow, will any hour at the Pāhala monitor reach 75 ppb of sulfur dioxide?** It gives a calibrated probability, a recommendation that follows the 2018 Hawaiʻi Schools SO2 Action Plan, and a discussion in which every number points at the evidence it came from. The next day it checks itself against the monitor, remembers the night, and tells its memory which analogs helped.

Built for the Open Agent Hackathon 2026 (GenAI Works), Track 3, Reasoning Architecture, by a team of one. Work in this repository started inside the build window (22 October 2026, 00:00 UTC). Every decision and every number along the way is in [docs/DEVLOG.md](docs/DEVLOG.md); the held-out results, scored once at the end, are in [docs/RESULTS.md](docs/RESULTS.md).

## Why it is a desk

Kīlauea has erupted in fountaining episodes since December 2024, 55 so far. Each one sends gas down the southwest flank into Kaʻū. In 2025 the Department of Health logged 25 one-hour SO2 exceedances at Pāhala, most between 02:00 and 09:00 HST, while families sleep with the windows shut and children get ready for school. The schools plan reacts after thirty minutes of elevated readings. No one issues a warning the evening before.

A statistical model fit on 2025 learned that bad nights come with fountaining and light winds. In 2026 the eruption changed. Episodes got shorter and pauses longer, and the gas now reaches Pāhala between episodes on trade-wind nights, three times as often as in 2025. On the development period (January to June 2026) the frozen model scored below climatology.

So the desk keeps a memory of every verified night and refits the same model on the recent ones each evening. It checks that against the frozen model, the recent event rate and the most similar past nights. When they disagree it re-queries the data, and it hands the night to a person when the conflict will not resolve. The test is whether this beats the frozen model and a plain adaptive baseline, the event rate of the last 30 verified nights. [docs/PREREG.md](docs/PREREG.md) has what was fixed before any model was fit and the four changes logged since.

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
  ForecasterAgent G1 frozen 2025 model   G2 memory model (same model refit on the last 120 days of verified nights in H-MEM)
                  G3 recent event rate   G4 similar remembered nights (as of issue, shrunk toward G3)
  Review          checks in code: frozen vs memory model gap, analog split, persistence contradiction, regime unseen,
                  data gaps, stale data, data quality; on a flag: targeted re-query, then guidance again (max 2 rounds)
  DutyLead        code sets the base from G2; Nemotron Ultra may move it by up to 0.15, only if the review raised a flag,
                  and writes a cited discussion; code applies the PREREG action mapping, or the case goes to a person
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

Paired Brier differences with 7-day block bootstrap 90% intervals:

- full desk minus frozen model: −0.117 [−0.157, −0.080]
- full desk minus recent-rate baseline: −0.031 [−0.048, −0.014]
- full desk minus desk without review: −0.009 [−0.019, +0.001]

The comparison that matters most is the second one. The skill score of +0.37 is measured against the 2025 climatology of 0.059, which is easy to beat in a season where 38% of nights had an event. A constant at that 38%, which nobody could have known in advance, would score 0.236; the desk scored 0.215.

Most of the gain comes from the memory model, the pre-registered pipeline refit each night on the nights the desk remembers. The review loop's contribution is small and not established: its interval touches zero. The frozen 2025 model sits near climatology because the eruption's pattern changed.

At the frozen threshold of 0.18 the desk warned on 87 of 102 nights: 37 hits, 50 false alarms and 2 misses, with 40 of the warnings at the orange level. In a season with an event every 2.6 nights that is a lot of alerts. The value curve in RESULTS.md shows for which costs of acting the warning pays off.

The labels match the Department of Health's own exceedance list on all 39 held-out event nights. One defect turned up after scoring and is described in RESULTS.md: the desk's G3 stream read a truncated slice of memory. It did not touch the base probability or any number in the table. Development-period numbers, used for tuning, are in [docs/DEVLOG.md](docs/DEVLOG.md). Live forecasts since 23 October are committed under `live/` before the outcome is known and checked the next afternoon.

## Run it

Once installed (see below), one command re-checks the headline numbers. It reads only committed files and the git history, and prints PASS or FAIL for each check: every Brier score and paired interval above, the warning counts, the label audit, and the commit times of the pre-registration, the freeze, the scoring and each live forecast.

```bash
.venv/bin/python scripts/prove.py
```

No keys needed for the recorded nights (2026-03-02, 2026-05-07, 2026-06-16): local input slices under `samples/data`, recorded Nemotron replies under `samples/llm`, an in-process memory seeded with every verified night through 30 June 2026.

```bash
docker build -t vog-desk .
docker run --rm vog-desk                                   # 2026-05-07, full desk, SAMPLE_MODE
docker run --rm vog-desk --issue 2026-06-16 --no-review    # forecast 5 on another recorded night
```

Without Docker:

```bash
uv venv --python 3.12 && uv pip install -e ".[dev,model,agents]"
SAMPLE_MODE=true .venv/bin/python run.py --issue 2026-05-07
.venv/bin/python -m pytest -q tests        # Zetaris, sidecar and full-data tests skip when those are absent
```

Live (needs `.env` from `.env.example`, the local data layer, Zetaris and Nightshift):

```bash
docker compose -f infra/compose.yml up -d && python infra/zetaris/setup.py          # S3 store, Postgres, the USL
cd memory-service && npm install && MEMORY_ROOT=$PWD/data/live npm start             # H-MEM and World Model sidecar
python run.py --issue 2026-10-23                                                    # Zetaris data, Nightshift models, sidecar memory
uvicorn api:app --port 8000                                                         # POST /run {"issue_date": "2026-10-23"}
```

The full dataset is rebuilt by `scripts/fetch_*.py` and the ingest modules (sources and SHA-256 in `data/MANIFEST.tsv`); per-night forecasts behind the results are in `results/`. Local data layer and Zetaris wiring: [infra/README.md](infra/README.md). Memory sidecar: [memory-service/README.md](memory-service/README.md).

## Sponsor technology

- Zetaris is the data layer. Eight sources (Parquet on a local S3 store, Postgres over JDBC) behind one Unified Semantic Layer, `pahala_exposure`, eight governed tables each with `available_utc`, eleven data quality rules (one of them flags exactly the two censored Ocean View hours of 3 Sep 2025 that DOH marks with an asterisk). Agents read only through `run_sql`. Scripts: `infra/zetaris/`; reflection for the judges: [docs/ZETARIS_REFLECTION.md](docs/ZETARIS_REFLECTION.md); evidence: `docs/evidence/zetaris-dq.txt`.
- The Meterless H-MEM and World Model reference implementations run unchanged in `memory-service/` (conformance 13 of 13 and 8 of 8, `docs/evidence/conformance-*.txt`). The sidecar adds one rule: a night can be retrieved only after its verification time. The desk uses memory as the training set for its memory model, for similar-night retrieval with a trace, for the verifier's write-back with source and confidence, for trust-ledger feedback, and for night-level conflict detection with the engine's 0.70 gate.
- Meterless Nightshift is the primary model provider (`src/vogdesk/llm.py`), with explicit model ids so every trace line names the model that answered: `nvidia/nemotron-3.5-lightning-30b-a3b` for evidence narration (thinking off) and `nvidia/nemotron-3-ultra-550b-a55b` for the DutyLead. Provider grade: [docs/evidence/provider-grade.md](docs/evidence/provider-grade.md).
- NVIDIA Nemotron supplies the two models above, through NVIDIA's free endpoint registered in Nightshift. `meterless.provider_model` is logged on every call.
- Cursor built the Nightshift provider block in `llm.py` (commit `f559fa7`, review in [docs/cursor/B-review.md](docs/cursor/B-review.md)). Everything else was built with Claude Code and Codex; all code was read and tested by the author.

## Pre-existing components

Declared: the Meterless reference engines (Apache-2.0, vendored unchanged, `memory-service/engines/UPSTREAM.txt`) and, for the demo replay, the author's Windward terrain scene (github.com/zeyusuntt/windward). All data is public (EPA, AirNow, USGS, DOH, IEM, NOAA).

## Limits

One station, one threshold, one training year with 21 events. An hourly mean of 75 ppb does not show that a 15-minute average reached the plan's yellow level. AirNow values after March 2026 are preliminary and rounded to whole ppb. The desk recommends; the school's designated person decides. Everything that did not work is in DEVLOG.
