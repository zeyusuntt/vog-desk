# Development log

Every tuning decision on the development split, with the score before and after, as docs/PREREG.md requires. Dates are PDT.

The key commits carry git tags: `prereg` (pre-registration frozen before the first fit), `freeze-v4` (desk rules frozen before the held-out run), `heldout-scored` (the held-out period scored once) and `cursor-provider` (the Nightshift provider block built in Cursor). Other short commit ids in this log come from the working repository. The public history was rewritten once, before publication, to drop planning notes and a local path; that kept every commit's date but changed its id, so `git log --before` and the tags are the way to find a commit named here. `scripts/prove.py` checks the timeline through the tags.

## 2026-10-23: pre-registration frozen, first fit, dev evaluation

- 15:28 PDT, git tag `prereg`: PREREG frozen (availability table, 12 model inputs, action-threshold procedure). No model had been fit.
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

## 2026-10-24 00:30 PDT: first full dev backtest of the desk (version 1), and what it taught

Both variants ran over the 175 dev nights (no-review: 172, three nights failed on NVIDIA rate limits and were not rerun before the design changed). Provisional t* = 0.19 was used for the orange/yellow mapping; probabilities are stored so t* can be chosen afterwards.

| forecast | Brier | BSS vs clim | AUC | CSI-max t* | CSI |
|---|---|---|---|---|---|
| 4 model only | 0.175 | −0.032 | 0.536 | 0.02 | 0.192 |
| 5 desk, no review (v1) | 0.160 | +0.012 | 0.534 | 0.07 | 0.202 |
| 6 full desk (v1) | 0.167 | +0.015 | 0.517 | 0.06 | 0.209 |
| 7 recent rate | 0.154 | +0.092 | 0.564 | 0.15 | 0.204 |

Paired differences (7-day blocks, 90% CI): full desk minus model −0.0080 [−0.0146, −0.0014]; full desk minus recent rate +0.0131 [−0.0015, +0.0290]; full desk minus no-review +0.0017 [−0.0008, +0.0046]. Full desk handed over 0 nights; the no-review variant 16 (Ultra chose handover itself when the streams conflicted and no re-query was allowed).

Reading: version 1 of the desk beats the frozen model (the primary claim's direction) but loses to the memory-free adaptive baseline, and the review loop did not help. The DutyLead's mean weights were lr 0.32, analog 0.29, recent rate 0.38. The analog stream was the weak link: Brier 0.173, AUC 0.45, mean probability 0.03, because memory holds 356 nights from 2025 and the ten "most similar" nights were mostly quiet 2025 nights in the same tag class. The memory was feeding the stale regime back in.

Offline experiments on the dev nights (no model calls, memory as of each issue time):
- Analog recency window (365, 180, 120, 90, 60, 45 days) and k = 10, 20, 30: at best Brier 0.161, AUC 0.60 raw; shrinking toward the recent rate with prior weight m = 10 gives 0.154, matching the recent rate itself. The tag classes carry little signal beyond the base rate in 2026.
- Refitting the same 12-input logistic regression on the verified nights in memory as of each issue time: last 120 days, C = 0.1: Brier 0.147, AUC 0.68; 60-day half-life weights over all history: 0.147, 0.68; whole history unweighted: 0.160, 0.68. This is the memory stream that works: memory as training data, not as a lookup of similar nights.

Decisions (dev tuning, desk only; forecasts 1 to 4 and 7 unchanged):
1. New stream G2 "memory model": the frozen model's pipeline refit at issue time on verified nights in memory within the last 120 days (at least 40 nights and 3 events; else all history with a 60-day half-life; else unavailable and flagged). `src/vogdesk/desk/memory_model.py`.
2. Analogs become G4: k = 20, candidates from the last 365 days when at least 5 exist, rate shrunk toward the recent rate with weight 10; still used for the discussion, the split flag and the orange clause.
3. The disagreement check compares the frozen model with the memory model (gap 0.3) instead of with the analog rate.
4. The DutyLead prompt names the four streams and says what each is; the template fallback weights are lr 0.2, memory model 0.5, recent rate 0.3.
Version 2 backtests run as `--tag v2`; version 1 results stay in `backtest_dev_<variant>.parquet` for the record.

## 2026-10-24 00:30 PDT: the publication-lag question, settled before the held-out run

The Pahala availability rule had to be settled before the held-out period was scored. Measurement at 07:25 UTC on 24 Oct against the live AirNow files: the 05Z hour (19:00 HST) already held a Pahala SO2 row in a file last modified 07:24 UTC, so that hour was public within 2 h 24 min of its end; 04Z and 03Z likewise (within 3 h, the files are rewritten as late data arrive). The hours 23Z to 02Z (13:00 to 16:00 HST on 23 Oct), which were missing at the 18:00 HST issue, were still missing at 07:25 UTC: that was a four-hour hole in the station's feed, not a lag. The Volcanoes NP station had every hour.

Decision: PREREG's 2-hour availability rule stands (no change 2). What the 23 Oct evening showed is a data gap right before issue, which the desk must notice: a new review flag `stale_data` fires when the latest published Pahala hour begins before 13:00 HST (more than 3 h before the usable cutoff) and orders a re-query of the last 6 hours. The 23 Oct entry above is corrected by this one. `scripts/verify_live.py` keeps recording the latest Pahala hour at each issue so the lag can be watched over the live nights; if a systematic lag ever appears, it will be logged as a PREREG change before any held-out scoring, not after.

Order of events for the record: dev backtest v2 is running with the 2-hour rule; t* and the conflict thresholds will be frozen from it; the held-out run follows the freeze.

## 2026-10-24 02:00 PDT: dev backtest v2, and the decision to let code hold the blend

v2 over the dev nights (full 173 of 175, no-review 169 of 175; the rest hit NVIDIA rate limits and are being rerun):

| stream or forecast | Brier | AUC | note |
|---|---|---|---|
| G1 frozen model | 0.177 | 0.539 | |
| G2 memory model | **0.148** | **0.685** | refit on the last 120 days of verified nights, as of each issue |
| G3 recent rate (from memory) | 0.163 | 0.478 | |
| G4 analogs, shrunk | 0.175 | 0.431 | no signal beyond the base rate |
| 6 full desk v2 (Ultra's weights) | 0.162 | 0.575 | mean weights lr 0.13, memory 0.30, recent 0.26, analog 0.12 |
| 5 desk no review v2 | 0.160 | 0.528 | 6 handovers |

Two findings. First, a bug: on some nights Ultra returned its weights keyed by guidance id (G1 to G4) instead of by stream name; `combine()` ignored them and fell back to the frozen model alone. Fixed (ids are now accepted). Second, and more important: the desk's blend was worse than its own best input. A grid search over convex weights of the four streams on the dev nights puts all weight on G2 (Brier 0.1477; 5-block cross-validation of the weight choice gives the same 0.1477, so this is not an overfit), and every mixture with the other streams is worse. The language model is not a better blender than arithmetic, and asking it to be one cost 0.014 Brier.

Decision (dev tuning, desk only, logged): the base probability is set by code from the stream order memory model, then recent rate, then frozen model (the first one available). The DutyLead's job becomes what a duty forecaster actually does: read the evidence and the flags, and either accept the base, adjust it by at most 0.15 with a stated reason tied to a flag or an evidence id (persistence contradiction, stale or missing data, a regime the memory model has not seen, analog maxima), or hand the case to a human. The adjustment and its reason are recorded with the decision. The discussion rules are unchanged. The no-review variant gets the same protocol without the re-query step, so the ablation measures the review itself. Version 3 backtests run as `--tag v3`; v2 results are kept for the record. t* and the conflict thresholds are frozen from v3.

## 2026-10-24 02:45 PDT: label audit against the DOH lists (scripts/label_audit.py)

Training year 2025: DOH lists 20 Pahala exceedance nights inside our 18:00 to 12:00 HST window; our labels have 21; 20 are in both with daily maxima within 0.5 ppb. One DOH row (11 Mar 2025, 3 to 4 p.m., 147 ppb) is a daytime exceedance outside the window and is correctly not an event for us. One of our nights (3 Mar 2025, 123.9 ppb from AQS validated data) is not in the DOH list. Development period: DOH 31 nights, ours 33, 31 in both within 1 ppb; our two extra nights (21 May, 25 Jun 2026) come from preliminary AirNow values. DAY0's "25 of 25 days" compared calendar days of exceedance; this audit compares nights, which is the unit the forecast uses. Tables: docs/LABEL_AUDIT_train.md, docs/LABEL_AUDIT_dev.md. The held-out audit runs after the held-out forecasts are scored.

Also saved: H-MEM dream and sleep previews over the live memory (633 nights) and the World Model record of the live case, docs/evidence/dreams/2026-10-24.json. Nothing was approved or applied; the previews are what the Verifier would show a person every seven nights.

## 2026-10-24 04:00 PDT: versions 3 and 4 on dev, and the freeze

Version 3 (code base, free adjustment within 0.15) over 174 dev nights, 33 events: base (memory model) Brier 0.1473, AUC 0.681; the desk after the DutyLead's adjustments 0.1534, AUC 0.638. Ultra proposed 32 adjustments, 31 of them downward (mean −0.08 on those nights), and on those 32 nights the desk scored 0.251 against the base's 0.218: the model talked itself out of events on the strength of wind and stability it read in the evidence, which the memory model had already priced in. Paired difference desk minus base +0.0060, 90% CI [+0.0009, +0.0124]. On the 47 nights where the review raised a flag, the adjustments helped a little (0.1087 against 0.1112).

Version 4 is one rule on top of v3: the adjustment is applied only when the review raised a flag; otherwise the base stands. Because the v3 run recorded base, flags and the proposed adjustment per night, v4's probabilities follow exactly from v3 without another model run (`scripts/derive_v4.py`; the v3 traces' discussion texts still describe the unapplied adjustment on unflagged nights, and the file says so). Full desk v4: Brier 0.1467, AUC 0.687 (6 of 32 proposed adjustments kept); gated minus base −0.0007, 90% CI [−0.0014, −0.0000]. Against the pre-registered comparators on the same nights: model only 0.1763, recent rate 0.1607.

CSI over the threshold grid for the full desk v4: 0.10 → 0.236, 0.15 → 0.241, 0.18 → 0.262, 0.20 → 0.254, 0.25 → 0.216. **t* = 0.18** (hits 17, misses 16, false alarms 32; POD 0.52, FAR 0.65). Forecast 4's CSI-maximising t* on dev is 0.02.

Frozen (PREREG "Frozen values", this commit): t* 0.18 for forecasts 5 and 6, 0.02 for forecast 4, and every desk rule listed there. The held-out period is run once after this commit. The no-review variant's v3 run is still finishing its last nights; its v4 derivation and t* check follow the same rule and are recorded below when done.

## 2026-10-24 04:10 PDT: held-out run started

Full desk (v4, frozen at tag freeze-v4) on the held-out period, started 04:10 PDT at commit 8bc82db (the only change since the freeze is making `--i-am-sure` a real argument; the first attempt was rejected by argparse). 102 valid nights between 1 July and 21 October 2026. Memory for the run is seeded with every verified night through 21 October, each stamped with its publication time, so the as-of query gives each issue exactly the nights a desk running since 2025 would have remembered. The no-review variant follows as soon as its dev v3 run finishes and its v4 derivation is recorded.

## 2026-10-24 04:30 PDT: development period, final (frozen desk v4), all seven forecasts

175 valid nights, 33 events, base rate 0.189. `scripts/evaluate.py --split dev --tag v4`.

| forecast | Brier | BSS vs clim | BSS vs persist | AUC | t* | hits | misses | FA | POD | FAR | CSI |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 climatology 0.059 | 0.170 | 0.000 | 0.354 | 0.500 | | | | | | | |
| 2 persistence | 0.263 | −0.548 | 0.000 | | 1 | 9 | 24 | 22 | 0.27 | 0.71 | 0.164 |
| 3 episode rule | 0.274 | −0.615 | −0.043 | | 1 | 0 | 33 | 15 | 0.00 | 1.00 | 0.000 |
| 4 model only | 0.175 | −0.032 | 0.333 | 0.536 | 0.02 | 33 | 0 | 139 | 1.00 | 0.81 | 0.192 |
| 5 desk, no review (v4) | 0.146 | +0.141 | 0.445 | 0.685 | 0.18 | 17 | 16 | 31 | 0.52 | 0.65 | 0.266 |
| 6 full desk (v4) | 0.147 | +0.141 | 0.433 | 0.687 | 0.18 | 17 | 16 | 32 | 0.52 | 0.65 | 0.262 |
| 7 recent rate | 0.154 | +0.092 | 0.414 | 0.564 | 0.15 | 21 | 12 | 70 | 0.64 | 0.77 | 0.204 |

Paired Brier differences, 7-day block bootstrap 90% intervals: full desk minus model −0.0296 [−0.0521, −0.0099]; full desk minus recent rate −0.0083 [−0.0158, −0.0013]; no-review minus model −0.0294; no-review minus recent rate −0.0083. Handovers: full desk 0, no-review 3 (Ultra chose them; counted separately). Reading, plainly: on dev the desk's value is the memory model; the review loop changed the numbers on almost no nights (the gated adjustments were 6 of 174 for the full desk and 10 of 175 for the no-review variant), so forecasts 5 and 6 are near twins. The review's worth, if any, has to show in the held-out period or in handover accuracy; it is not claimed from dev. The dev period was used for tuning, so these numbers are tuning numbers; the held-out period is the test.

## 2026-10-24 05:40 PDT: held-out period, scored once (docs/RESULTS.md)

Run at the frozen code (tag freeze-v4), scored with `scripts/results.py --split heldout --i-am-sure --t-star 0.18 --tag v4` at 3ff6d78. 102 valid nights, 39 events, base rate 0.382.

| forecast | Brier | BSS vs clim | AUC | at t* | POD | FAR | CSI |
|---|---|---|---|---|---|---|---|
| 1 climatology | 0.341 | 0 | | | | | |
| 2 persistence | 0.455 | −0.32 | | 1 | 0.41 | 0.59 | 0.26 |
| 3 episode rule | 0.422 | −0.24 | | 1 | 0.03 | 0.83 | 0.02 |
| 4 model only | 0.332 | +0.03 | 0.614 | 0.18 | 0.08 | 0.40 | 0.07 |
| 5 desk, no review | 0.224 | +0.34 | 0.657 | 0.18 | 0.95 | 0.58 | 0.41 |
| 6 full desk | **0.215** | **+0.37** | 0.687 | 0.18 | 0.95 | 0.58 | 0.42 |
| 7 recent rate | 0.246 | +0.28 | 0.397 | 0.13 | 1.00 | 0.62 | 0.38 |

Paired differences (7-day blocks, 90%): 6 minus 4 −0.117 [−0.157, −0.080]; 6 minus 7 −0.031 [−0.048, −0.014]; 6 minus 5 −0.009 [−0.019, +0.001] (14-day: [−0.016, −0.000]); 4 minus 1 −0.009 [−0.023, +0.004]. Both pre-registered claims hold. Inside the desk: base (memory model) 0.2199, full desk 0.2153, no-review 0.2244; 82 nights flagged; 8 handovers in the no-review variant (5 events), none in the full desk; Ultra fell back to the template on 23 nights. Nothing was changed after seeing these numbers; the live desk runs the same frozen code.

## 2026-10-24 06:40 PDT: SAMPLE_MODE recordings

`scripts/record_samples.py` ran the frozen desk on 7 May, 2 Mar and 16 Jun 2026 (local Parquet, memory seeded through 30 Jun, t* 0.18), both variants, through Nightshift with recording on. Six distinct model replies were saved (the no-review variant shares the full variant's prompts when no flag is raised, so the same reply serves both). `tests/test_sample_mode.py` replays a full and a no-review case with SAMPLE_MODE=true and no token, checks every model call came from the recording, and matches the recorded decision. 180 tests. Note for judges: the replay key hashes the exact prompt, so a changed prompt or t* needs new recordings; `samples/manifest.json` records both.

## 2026-10-24 07:30 PDT: cold run from a fresh clone

The first Docker run failed: raw GFS and sounding files, derived Parquet and the model pickle are git-ignored, so a stranger's clone had none of them. Fixed with three changes (commit 352f2ca): `samples/data` carries only the rows the three recorded nights need (about 50 KB of public EPA, AirNow, USGS, IEM and NOAA data; the full dataset is still rebuilt by the scripts); the frozen model loads from a full-precision parameter export when the pickle is absent (identical to the pickle on every dev night); the committed memory seed is copied before use. Verified in a fresh clone with `env -i` (no `.env`, no data): `run.py` replays 2026-05-07 with both model calls served from the recording, 175 tests pass and 15 skip (Zetaris, sidecar, full data). The Docker image built from that clone replays 2026-05-07 (full) and 2026-06-16 (no review) with no environment. Per-night forecasts of the frozen desk for dev and held-out are committed under `results/`.

## 2026-10-24 09:00 PDT: a defect in the G3 stream, found after held-out scoring

Building the results page, the "recent event rate" bar scored 0.273 where RESULTS reports forecast 7 at 0.246. Cause: `recent_rate_guidance` queried memory with `k=400`; the memory query ranks by relevance, so with more than 400 nights in memory the newest 30 of a relevance-ranked 400 were not the last 30 nights. Dev: differs on 100 of 174 nights (mean gap 0.03). Held-out: differs on all 102 (mean gap 0.16; memory then held 500 to 630 nights).

Reach: G3 is never the base while the memory model is available (it was available on every held-out night), so `base_p` is unaffected. G3 did reach the duty lead's prompt and the analog shrinkage prior, so some of the 47 applied adjustments were made while reading a wrong recent rate. Forecast 7 in every table comes from the labels and was always right.

Decision: the held-out results are not re-run (they were scored once, and re-running would be tuning on the test). The defect is reported in RESULTS.md and results/README.md, the cap is raised for the live desk (PREREG change 3), and a regression test (`test_recent_rate_guidance_uses_the_last_30_nights_even_when_memory_holds_hundreds`) fails on the old cap and passes on the new one. The results CSVs gain `p_forecast7` so the page shows the pre-registered forecast 7.

## 2026-10-24 09:20 PDT: the same truncation on the live path

Following the G3 defect: the Node sidecar clamped `k` to 100 in `/hmem/query`. The live desk on 23 October therefore fit its memory model on 100 relevance-ranked nights (its note says "refit on 100 verified nights ... all history, 60-day half-life", because the last 120 days could not be found among them) rather than on the 120-day window. Dev and held-out ran on the in-process memory, which never capped, so the scored results are unaffected. Cap raised to 100,000; a sidecar integration test adds 130 nights and fails on the old server, passes on the new. With the fix, the live memory model as of 18:00 HST on 24 October trains on the last 120 days of verified nights. PREREG change 4.

## 2026-10-24 10:40 PDT: two corrections found while writing the deck

- Timing of the first live desk decision. The model-only forecast for 23 October was committed at 21:02 PDT (18:02 HST). The full desk's decision for the same night was committed at 00:21 PDT on 24 October, which is 21:21 HST, three hours into the window. Its inputs were cut at 18:00 HST and the outcome was not known until noon on 24 October, so it is still a forecast. The README, the page and the video script said "committed before the night happened"; they now say "before the outcome was known". Later nights run at 20:57 PDT (17:57 HST).
- The SAMPLE_MODE replay test has failed since the G3 fix (da1b418). The fix changed G3 and the shrunk analog stream on the recorded nights, so the duty lead's prompt changed and its recorded reply no longer matched. The full suite was not run before that commit. The samples are re-recorded with the current code.

## 2026-10-24 18:07 PDT: first live night verified (2026-10-23)

- Outcome: the window 18:00 HST 23 Oct to 12:00 HST 24 Oct had all 18 Pāhala hours. The highest hour was 170 ppb at 05:00 HST, and seven hours were at or above 75 ppb (02:00 to 08:00). An event night. The desk had issued 0.28, prepare for yellow, so the call is a hit; Brier 0.522 for the desk and 0.852 for the model alone (0.077). The night is in the sidecar memory as mem-634 and in Postgres; `live/2026-10-23/scene.json` now carries the hours at all three monitors.
- Threshold: the verifier still defaulted to 0.19, the threshold before the freeze, so `t_star_used` reads 0.19. At the frozen 0.18 the outcome is the same (0.28 is above both). The verifier was not re-run, because a second run would write the night into memory twice. Its default now reads the frozen value from the desk.
- Publication lag, measured again: at 15:09 HST on 24 Oct the latest Pāhala SO2 hour on AirNow was 13:00 HST (hour beginning), posted in the 23Z file; the 14:00 HST file (00Z on 25 Oct) existed but held no SO2 for Pāhala, Volcanoes NP or Ocean View yet. So by mid-afternoon the DOH feed was about one hour behind the end of the hour, against about six hours at 18:00 HST on 23 Oct. The lag is variable, which fits the earlier reading that 23 Oct evening was a feed gap rather than a fixed delay; the `stale_data` flag stays the right response. Verification at 15:00 HST had the whole window.

## 2026-10-24 21:50 PDT: first live night run by the frozen desk (2026-10-24)

- Run started 20:57 PDT (17:57 HST) and wrote its forecast at 21:50 PDT (18:50 HST); inputs were cut at 18:00 HST and the window closes at 12:00 HST on 25 October. Data through Zetaris; the Hilo sounding fetch (IEM) failed again, so the sounding is a gap.
- Streams: frozen model 0.03, memory model 0.25, recent rate 0.33, analogs 0.31 (6 of 20 analog nights had an event). The review flagged analog_split and data_gap and re-queried four tables in one round. The desk issued the memory model's 0.25 unchanged, prepare for yellow, with a caveat.
- Nemotron Ultra's reply failed validation twice (the first was not the required JSON; the second cited 14, the number of quiet analog nights, which the validator did not accept), so the code's template wrote the discussion. Lightning's summary passed.
- Two defects found reading the trace, both in the frozen rules, both affecting how the night is described: (1) the data_gap flag is marked "gap filled on re-query" when any re-queried table returned rows, even though the sounding re-query (E10) was still empty; the same happened on 23 October. (2) The validator does not accept numbers that appear in the review's own flag details, so a reply that cites the analog split is rejected. Neither changed the probability tonight: the base is the memory model and no adjustment was made. Fixes follow, with tests, for the live desk only.

## 2026-10-24 22:10 PDT: the two defects fixed for the live desk (PREREG changes 5 and 6)

- `duty_lead.resolve_flag`: a data gap closes only when the re-query of the missing source returns rows; otherwise the flag stays open as "still missing on re-query" and the review may use its second round. Disagreement flags are settled as before.
- `duty_lead.decision_numbers`: numbers in the review's flag details and resolutions may be cited by the duty lead.
- Tests: `tests/test_review_resolution.py` (four cases, including the sounding gap of 24 October). The recorded sample nights have no flags, so their replays are unchanged; 190 tests pass. Held-out results are not re-run, and the 23 and 24 October live forecasts stand as issued.

## 2026-10-25 18:07 PDT: second live night verified (2026-10-24)

- Outcome: all 18 Pāhala hours from 18:00 HST 24 Oct to 12:00 HST 25 Oct are in, and the highest was 21 ppb, so no hour reached 75 ppb. The desk had issued 0.25, prepare for yellow, so the call is a false alarm. Brier 0.060 for the desk against 0.001 for the frozen model alone (0.03), which was right on this night. Labels are AirNow preliminary; `t_star_used` is 0.18. The night is in the sidecar memory as mem-635 and in Postgres.
- Live record after two nights: one hit (23 Oct) and one false alarm (24 Oct). Summed Brier over the two nights is 0.582 for the desk and 0.853 for the frozen model. Two nights say nothing about skill; they are recorded as they came.
- Publication lag: at 15:07 HST on 25 Oct the latest Pāhala SO2 hour on AirNow was 13:00 HST (hour beginning, the 23Z file), the same one-hour gap as on 24 Oct.
- Gap found while checking: `scripts/verify_live.py` writes each live night into memory, but it does not send the trust-ledger feedback that the backtest verifier sends (which analog nights agreed with the outcome), so `ledger_entries` is 0 on both live nights. The held-out results used the backtest verifier and are not affected. Adding the feedback to live verification would change what later live nights retrieve, so it is left as it is and noted here rather than changed mid-run.

## 2026-10-25 21:50 PDT: third live night (2026-10-25)

- Run started 20:57 PDT (17:57 HST) and wrote its forecast at 21:49 PDT (18:49 HST); inputs were cut at 18:00 HST and the window closes at 12:00 HST on 26 October. Data through Zetaris; the Hilo sounding fetch (IEM) failed again, so the sounding is a gap.
- Streams: frozen model 0.03, memory model 0.09, recent rate 0.33, analogs 0.31 (6 of 20 analog nights had an event; the highest analog maximum was 170 ppb). Pāhala's last 24 usable hours peaked at 21 ppb, and episode 55 ended 8.4 days before.
- The review flagged analog_split and data_gap. The first re-query returned the same analog rows, so the split was settled as a real disagreement. The sounding was still missing, so under PREREG change 5 the gap stayed open and the review used its second round; the sounding was still missing after it.
- Nemotron Ultra's reply passed validation, the first time on a live night (it cited the 6 and 14 from the flag detail, which PREREG change 6 allows). It raised the base by 0.06, citing the open data gap and the analog split, to 0.15. That is below the frozen threshold of 0.18, so the call is normal operations. Lightning's summary passed. Both calls were answered by the models requested.

## 2026-10-26 09:00 PDT: the disagreement handover cannot fire

- Found by a second model while checking the README. `duty_lead.must_handover` sends a case to a person when model and memory are more than 0.3 apart and a disagreement flag is still open after two rounds. But `requery_node` settles every disagreement flag on the first re-query (same rows back means the disagreement is real), so no such flag is open at round 2. The backtests confirm it: no full-desk handovers in any variant; the eight handovers in the no-review runs were the duty lead's own choice.
- What does send a case to a person: tonight's GFS run still missing after two rounds, or the duty lead choosing a handover. README, deck and submission notes now say this. The rule itself is left as frozen; the scored results and the live nights are unaffected, since the branch never ran.

## 2026-10-26 20:56 PDT: third live night verified (2026-10-25)

- Outcome: 17 of the 18 Pāhala hours from 18:00 HST 25 Oct to 12:00 HST 26 Oct are in (01:00 HST is missing on AirNow). The highest was 27 ppb at 08:00 HST, so no hour reached 75 ppb. The desk had issued 0.15, normal operations, so the call is a correct negative. Brier 0.023 for the desk against 0.001 for the frozen model alone (0.03). Labels are AirNow preliminary; `t_star_used` is 0.18. The night is in the sidecar memory as mem-636 and in Postgres.
- Live record after three nights: one hit (23 Oct), one false alarm (24 Oct) and one correct negative (25 Oct). Summed Brier is 0.606 for the desk and 0.854 for the frozen model. Most of the model's total comes from the one event night it missed. Three nights are too few to say anything about skill.
- Publication lag: at 17:57 HST on 26 Oct the latest Pāhala SO2 hour on AirNow was 16:00 HST (hour beginning, the 02Z file on 27 Oct). That is the same one-hour gap as at the 24 and 25 Oct issue times.
- This verification ran at 17:56 HST, about three minutes before tonight's desk run, because the scheduled check started late. The 25 Oct night was in memory before the 26 Oct run began.
