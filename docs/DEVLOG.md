# Development log

Every tuning decision on the development split, with the score before and after, as docs/PREREG.md requires. Dates are PDT.

## 2026-10-23: pre-registration frozen, first fit, dev evaluation

- 15:28 PDT, commit `8649b40`: PREREG frozen (availability table, 12 model inputs, action-threshold procedure). No model had been fit.
- 15:34 PDT: first fit, `scripts/train.py`. 356 training nights, 21 events, base rate 0.059.

### Training fit (2025, out of fold over 5 contiguous blocks)

| C | 0.01 | 0.03 | 0.1 | 0.3 | 1 | 3 |
|---|---|---|---|---|---|---|
| OOF Brier | 0.0551 | 0.0537 | **0.0534** | 0.0544 | 0.0556 | 0.0568 |

Climatology Brier on the same nights 0.0555. Chosen C = 0.1. OOF AUC 0.596. After isotonic (fit on the same OOF predictions, so in-sample for that step) Brier 0.0500. The model barely beats climatology out of fold on 2025. Standardised coefficients with the largest size: episode active +0.50, boundary-layer height at Pāhala −0.27, Pāhala 6 h mean +0.21, 925 hPa wind speed at the summit −0.18, Pāhala 24 h max +0.17. The signs are physical for 2025: fountaining, a shallow boundary layer and light winds raise the odds.

### Development split, Jan to Jun 2026 (forecasts 1 to 4; 5 and 6 do not exist yet)

175 valid nights, 33 events, base rate 0.189 (2025: 0.059).

| forecast | Brier | BSS vs clim | BSS vs persistence | AUC | t* (CSI max) | hits | misses | FA | CN | POD | FAR | CSI |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| climatology 0.059 | 0.170 | 0.000 | 0.354 | 0.500 | | | | | | | | |
| persistence | 0.263 | −0.548 | 0.000 | | 1 | 9 | 24 | 22 | 120 | 0.273 | 0.710 | 0.164 |
| episode rule | 0.274 | −0.615 | −0.043 | | 1 | 0 | 33 | 15 | 127 | 0.000 | 1.000 | 0.000 |
| model, raw | 0.174 | −0.026 | 0.337 | 0.575 | 0.02 | 32 | 1 | 126 | 16 | 0.970 | 0.797 | 0.201 |
| model, calibrated | 0.175 | −0.032 | 0.333 | 0.536 | 0.02 | 33 | 0 | 139 | 3 | 1.000 | 0.808 | 0.192 |

Paired Brier difference model minus climatology +0.0055, 7-day block bootstrap 90% CI [−0.0000, +0.0104] (26 blocks, 16 with an event). Model minus persistence −0.088, CI [−0.121, −0.054].

Reliability of the calibrated model on dev: 144 of 175 nights were given 0.02 to 0.05 and 26 of them (18%) had an event. The model is not wrong in rank order so much as it is anchored to a base rate that no longer holds. The CSI-maximising threshold of 0.02 is a symptom of the same thing and would not be a usable action threshold.

### What changed between 2025 and 2026

Univariate AUC of each input, training versus dev:

| input | AUC 2025 | AUC dev 2026 | note |
|---|---|---|---|
| episode active | 0.687 | 0.479 | 9 of 21 training events during a known episode; 0 of 33 dev events |
| hours since last episode end (log) | 0.378 | 0.538 | dev events sit deep in pauses, median about 8 days after the last episode |
| Pāhala 24 h max (log) | 0.655 | 0.682 | the one input whose signal held |
| Pāhala 6 h mean (log) | 0.667 | 0.548 | |
| Volcanoes NP 6 h max (log) | 0.536 | 0.377 | summit station is out of the plume when the plume reaches Pāhala |
| GFS 925 hPa wind toward Pāhala, summit | 0.413 | **0.743** | sign flipped |
| GFS 925 hPa speed, summit | 0.382 | **0.677** | sign flipped: stronger wind, more events in 2026 |
| GFS 850 hPa wind toward Pāhala | 0.428 | **0.744** | sign flipped |
| GFS 925 sector fraction | 0.440 | 0.585 | |
| GFS 10 m speed, Pāhala | 0.485 | 0.520 | |
| GFS boundary-layer height, Pāhala (log) | 0.384 | 0.549 | |
| Hilo inversion base | 0.520 | 0.496 | no signal either year |

Eruption state from the USGS table: 36 episodes in 2025 (median 11.9 h long, median pause 8.6 days), 16 in 2026 to 17 Oct (median 8.7 h, median pause 13.9 days). Pāhala's monthly 90th-percentile hourly SO2 rose from 12 to 23 ppb through 2025 to 35 ppb in May 2026 and 29 in June, while the Volcanoes NP station's 90th percentile fell to about 1 ppb from May 2026. Reading: in 2025 Pāhala's bad nights were mostly winter light-wind nights or fountaining nights (13 of 21 events in Jan to Mar 2025); in 2026 the gas reaching Pāhala comes between episodes, carried by the trade-wind flow down the southwest flank, and the inter-episode degassing appears stronger. A model fit on 2025 learned "light wind, fountaining" and so under-forecasts 2026 trade-wind nights by design. This is a regime shift, not a coding error: the cutoff tests pass, the features are computed from the same code on both periods, and the one input that is regime-independent (recent Pāhala SO2) kept its signal.

Label check against the DOH's own 2026 exceedance list (`exceedances_2026_10_15_17.pdf`, fetched 23 Oct; the file fetched on 22 Oct was a 27-byte 404 stub and was deleted): every DOH Pāhala exceedance whose hour falls in one of our dev windows is in our labels, 34 of 34 rows over 31 nights, daily maxima within 1 ppb (AirNow rounds to whole ppb; one DOH row reads 76 where AirNow gives 75.0). Our labels have two more event nights, 21 May (93 ppb) and 25 Jun (82 ppb), that DOH does not list; both come from preliminary AirNow data and may have failed DOH's validation. So the shift to a 0.19 base rate is in the state's official record, not an artefact of our data handling. Only rows with issue dates up to 30 June were compared or printed; the list also covers the held-out months and was not looked at there.

### Protocol note (disclosure)

While diagnosing the shift I printed monthly event counts for every month in the data, which included the held-out months (Jul to Oct 2026). No forecast was scored on held-out and no model was changed in response, but the held-out base rate is now known to the developer before the held-out run. Recorded here so the held-out result is read with that in mind. The diagnostic queries from now on are restricted to issue dates up to 30 June 2026.

### Tuning decisions

None yet. The model is the pre-registered one. The decision that the dev result forces (keep forecast 4 as the frozen 2025 model and add an adaptive baseline, or refit on 2025 plus dev before the held-out run) is a PREREG change and is logged under "Changes after freezing" in PREREG.md once Zeyu decides.

### Other facts recorded today

- AirNow posts each hourly file about 25 to 30 minutes after the hour (file timestamps 23 Oct: 21:31, 21:58, 22:26 UTC for hours 18, 19, 20). PREREG uses a 2-hour lag, so the last usable hour at 18:00 HST begins at 16:00 HST. Persistence was redefined to use only published hours; DAY0's persistence numbers used the hour ending at issue.
- Feature table for 546 issue dates (1 Jan 2025 to 30 Jun 2026) computes in about 5 s; at most 1.8% of any input is missing.
- Hilo soundings in the IEM archive end on 17 Oct 2026 12Z; recent launches are fetched by `scripts/live.py` and fall back to missing (imputed with the training median) when absent.

## 2026-10-23 evening: the desk, and decisions made while building it

- PREREG change 1 (option A, Zeyu): forecast 7, recent-rate climatology over the last 30 verified nights. On dev it already scores Brier 0.154, BSS +0.09 against climatology, better than the frozen model (−0.03). This is the bar the desk's memory stream must clear.
- Provisional action threshold for the live desk and the backtest: t* = 0.19, the dev base rate, until the CSI-maximising value is read off the completed dev backtest and frozen. Recorded in `DEFAULT_T_STAR`; the backtest stores probabilities, so t* can be chosen afterwards without re-running.
- Lightning (EvidenceAgent) must be called with `chat_template_kwargs.enable_thinking = false`. With thinking on it spends the whole token budget reasoning over the evidence table and Nightshift returns `502 empty_response`, even at `max_tokens` 8000. With thinking off it answers in 2 to 4 s and passes the number validator on the first or second try. Ultra (DutyLead) keeps thinking on (reasoning arrives before `</think>` in the content and is stripped); it takes 20 to 50 s per decision.
- Number validator: every number in a model reply must exist in the evidence or guidance it was given, and a sentence that uses an evidence number must cite an id. A free set needs no citation: counts 0 to 10, the plan constants (75 ppb, 0.11 and 0.21 ppm, 210 ppb, 15-minute), interval level 90, the window sizes, and the weights the DutyLead itself states. Replies failing twice are replaced by a template built from the evidence; the trace records the failure reasons. First version was too strict (it rejected "7 quiet nights" and the model's own weights); loosened as described, with the evidence-number citation rule kept.
- Action mapping follows PREREG literally: p ≥ 2 t*, or any of the top analog nights at or above 0.21 ppm, means prepare for orange. On 2 Sep 2025 (local data, 2025 memory) this gave prepare_orange at p = 0.24 because one analog night reached 290 ppb. Whether the analog-maximum clause fires too often is a dev-period question; if it is changed, it is logged here and in PREREG.
- Backtest design: memory is seeded with every verified night through the end of the split, each stamped with its publication time (12:00 HST next day + 2 h), and the as-of query does the rest. The Verifier's feedback to the analogs (helpful / wrong) is applied after each case. Three cases run at a time per variant; both variants (full, no-review) run against the same Nightshift.

## 2026-10-23 21:00 PDT: first live forecast and a publication-lag finding

- `live/2026-10-23/forecast.json`, model only, p = 0.077 (raw 0.068); climatology 0.059, persistence 0, episode rule 0 (episode 55 ended 17 Oct). GFS 18Z of 23 Oct present (105 rows). The Hilo sounding fetch (IEM) and the HVO notice fetch (USGS) timed out at issue time; the forecast says so in `refresh_status` and used the archived values (latest sounding 17 Oct 12Z, imputed; last known episode 55). Committed at 21:02 PDT, before the window's first hour was public.
- Finding: at 18:00 HST the AirNow hourly files for 13:00 to 16:00 HST existed (33 to 35 Hawaii rows each) but held no Pahala SO2. The latest Pahala hour was 12:00 HST, posted in the 22:00 UTC file. The NPS station (Volcanoes NP) posts within the hour; the Hawaii DOH stations appear to post with a lag of about 4 to 6 hours. PREREG's 2-hour availability rule is therefore optimistic for Pahala in live operation. The archive used for training and dev cannot show this lag because AirNow re-publishes hourly files when late data arrive. Decision pending: measure the lag over the next nights (compare `pahala_last_hour_hst` in each live forecast with the issue time) and, if it holds, log a PREREG change that sets the Pahala availability lag to 6 h before the held-out run. The DOH-station lag is itself a finding worth reporting: a desk that waits for Pahala's own latest hour is working with data five hours old.
