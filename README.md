# vog-desk

A vog (volcanic smog) forecast desk for Pāhala, on Hawaiʻi Island. Every evening at 18:00 HST it answers one question: between now and noon tomorrow, will any hour at the Pāhala monitor reach the event threshold for sulfur dioxide (SO2)? It gives a calibrated probability, a recommendation that follows the existing Hawaiʻi schools SO2 action plan, and the evidence behind each number. The next day it checks itself against the measurements.

Built for the Open Agent Hackathon 2026 (GenAI Works), Track 3: Reasoning Architecture. Work in this repository started inside the build window (22 October 2026, 00:00 UTC).

Status: day zero. Raw data, labels and reference forecasts exist; the agents do not yet.

## The question, fixed before modelling

- Issue time: 18:00 HST on day D.
- Window: 18:00 HST day D to 12:00 HST day D+1 (18 hourly values).
- Event: any hourly SO2 at Pāhala (AQS site 15-001-2016) of at least 75 ppb.
- Threshold rule, decided before looking at any data: use 0.11 ppm (the lower edge of the schools plan's yellow level) if the 2025 training year has at least 20 event nights at that level, otherwise 75 ppb (the 1-hour national standard). 2025 had 13 nights at 0.11 ppm, so the threshold is 75 ppb. An hourly mean of 75 ppb does not guarantee that a 15-minute average reached the yellow level; this is stated wherever results are reported.
- A night counts as a negative only if at least 14 of its 18 hours are present. Missing windows are excluded, never counted as negatives.
- Splits: 2025 for training, January to June 2026 for development, July 2026 to 21 October 2026 held out and run once.

## Data, all public

| Source | What | Script |
|---|---|---|
| EPA AQS pre-generated files | Validated hourly SO2, 2025 to March 2026 | `src/vogdesk/ingest/aqs.py` |
| AirNow public hourly files | Preliminary hourly SO2 and station wind, March to October 2026 | `scripts/fetch_airnow.py`, `src/vogdesk/ingest/airnow.py` |
| USGS HANS notices (HVO) | 977 Kīlauea notices since December 2024, with publication times | `scripts/fetch_hans.py`, `src/vogdesk/ingest/hans.py` |
| USGS Kīlauea eruption information | Start and pause times of the 55 fountaining episodes (USGS marks these as preliminary) | `src/vogdesk/ingest/usgs_episodes.py` |
| Hawaiʻi DOH exceedance list | Official 2025 exceedance days, used only to check our labels | `data/raw/doh/` |
| Iowa Environmental Mesonet | Hilo radiosondes (PITO), 00Z and 12Z | `scripts/fetch_raob.py` |
| NOAA GFS 0.25° on AWS | 18Z runs, leads 10, 16 and 22 h, point values near the summit, Pāhala, Ocean View, Kona | `scripts/fetch_gfs.py` |

`python scripts/manifest.py` writes `data/MANIFEST.tsv` with the SHA-256 of every raw file. Raw and derived data are not committed.

## What a forecast may see

Every input carries the time it became public, and features are cut at the issue time:

- AirNow and AQS hours before 18:00 HST.
- HVO facts only after the first notice that published them; when a USGS episode time has no matching notice, it becomes usable 12 hours after the event.
- The GFS run from 18Z on day D, about 10 hours old at issue time. The 00Z run of D+1 is never used.
- Soundings up to the 00Z launch (14:00 HST) on day D.

`tests/test_no_lookahead.py` checks these rules.

## Run

```bash
uv venv --python 3.12 && uv pip install -e ".[dev,model,gfs]" eccodes lxml
python -m pytest -q tests
```

## Tools used to build it

Cursor is the development environment, as the hackathon rules require. Claude Code and Codex were used alongside it, which the hackathon's AI usage policy allows.
