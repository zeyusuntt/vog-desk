# Day 0 checkpoint (22 October 2026)

## Data gate

| Check | Result |
|---|---|
| AQS hourly SO2, Hawaiʻi | 13 sites, 1 Jan 2025 to 31 Mar 2026. Pāhala: 10,486 hours, about 4% missing |
| AirNow hourly, Hawaiʻi | 5,664 of 5,664 hourly files fetched, 1 Mar to 22 Oct 2026. Pāhala SO2 present in 5,002 hours (about 12% missing) |
| AirNow against AQS, Pāhala, March 2026 overlap | AirNow row time equals the AQS UTC hour: correlation 0.9999, mean absolute difference 0.36 ppb (AirNow rounds to whole ppb). One-hour shifts drop the correlation to 0.85 |
| USGS episode table | 55 episodes, every start and end parsed |
| HANS notices against the USGS table | Ends: 41 of 42 parsed notices within 90 minutes of USGS. Starts: 38 of 42; the other 4 notices date the start from precursor activity. Median delay from start to first notice 37 minutes, 90th percentile about 7 hours |
| Our labels against the DOH 2025 exceedance list | Pāhala: 25 of 25 days agree, same daily maximum within 1 ppb. Ocean View: 22 of 22 days agree |
| Hilo soundings (PITO) | 1,424 launches, 1 Dec 2024 to 17 Oct 2026 12Z. The most recent days are not yet in the archive |
| GFS 0.25° point values | 2,001 of 2,001 files (667 issue days × leads 10, 16, 22 h from the 18Z run), 7 fields × 5 points each, none missing |
| Data manifest | `data/MANIFEST.tsv` lists 244 raw files with SHA-256 |

## Threshold, by the rule written before modelling

2025 training year at Pāhala: 13 event nights at 110 ppb, 21 at 75 ppb. Fewer than 20 at 110 ppb, so the threshold is 75 ppb.

## Reference forecasts on the 2025 training nights

356 valid nights, 21 events, climatology probability 0.059.

| Forecast | Hits | Misses | False alarms | CSI | Brier skill vs climatology |
|---|---:|---:|---:|---:|---:|
| Climatology (0.059) | 0 | 21 | 0 | 0.000 | 0.00 |
| Persistence (any hour at or above 75 ppb in the 24 h before issue) | 3 | 18 | 20 | 0.073 | -0.92 |
| Episode rule (fountaining known to be active in the 24 h before issue) | 9 | 12 | 49 | 0.129 | -2.09 |

Only 9 of the 21 event nights followed a fountaining episode known to be active in the previous 24 hours. The other 12 came during pauses, when the wind decides where the gas goes. Fountaining alone is not enough to forecast Pāhala.

## Sanity check of the GFS extraction

For the night of 2 to 3 September 2025 (Ocean View 1,206 ppb at 01:00 to 02:00 HST, above the analyzer's range; Pāhala 317 ppb at 05:00 to 06:00 HST), the 18Z GFS run of 2 September gives 2 to 3 m/s winds from the north to north-northeast at 925 hPa over the summit, which carries the plume south toward Kaʻū.
