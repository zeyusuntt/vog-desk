# Pre-registration (frozen 23 October 2026, before any model is fit)

Everything below is fixed before the development or held-out data are scored. Changes after the commit that freezes this file are listed at the bottom with a reason. The held-out period is scored once. Tuning decisions on the development period are logged in `docs/DEVLOG.md`.

## Question and event

- Station: Pāhala, AQS 15-001-2016.
- Issue: 18:00 HST on day D. Window: 18:00 HST day D to 12:00 HST day D+1 (18 hourly values, hour-beginning stamps 18:00 to 11:00 HST).
- Event: any hourly SO2 at or above 75 ppb in the window. Chosen on 22 October 2026 by a rule written before any data were examined (0.11 ppm if 2025 had at least 20 such nights; it had 13).
- Validity: a night is positive if any hour meets the threshold; negative only if at least 14 of 18 hours are present; otherwise excluded.
- Label source: one hourly series per station. AQS validated values where AQS has the hour (to 31 March 2026); AirNow preliminary values for hours AQS does not have.
- The Hawaiʻi Schools SO2 Action Plan (2018) levels are defined on 15-minute averages. An hourly mean of 75 ppb does not imply that any 15-minute average reached the yellow level (0.11 ppm). Every report says so.

## Splits

- Training: issue dates 1 January to 31 December 2025.
- Development: 1 January to 30 June 2026.
- Held out: 1 July to 21 October 2026. Scored once, after the system and all thresholds are frozen. The freezing commit hash is written into `docs/RESULTS.md`.
- Live: issue dates from 23 October 2026, committed before the outcome is known. Reported separately; it shows the system runs, not that it is calibrated.

## Information available at issue

Every input row carries the UTC time it became public (`available_utc`). A forecast issued at time T uses only rows with `available_utc <= T`. The rules:

| Source | `available_utc` | Why |
|---|---|---|
| Hourly SO2 (AQS, AirNow), any station | hour-beginning time + 2 h | AirNow posts each hourly file about 25 to 30 minutes after the hour ends (file timestamps checked 23 Oct 2026). At 18:00 HST the last usable hour is 16:00 to 17:00 HST. |
| AQS validated values for past hours | as above | Validated values were not public in real time. They stand in for the preliminary AirNow values that were, which agree with them at r = 0.9999 (DAY0.md). Stated in every report. |
| Episode start or end (HVO) | first HANS notice that published it; if no notice matches within 90 minutes of the USGS time, the USGS time + 12 h | DAY0.md |
| GFS 0.25° | the 18Z run of day D only, available at 00Z D+1 (run + 6 h) | NCEP finishes 18Z products about 3.5 to 4 h after initialisation; the 00Z run of D+1 is never used |
| Hilo soundings (PITO) | launch time + 3 h; the latest usable is 00Z D+1 (14:00 HST) | |
| Memory (H-MEM, analogs) | a night becomes memory only after its window closes and its verification is written (12:00 HST D+1 at the earliest) | Backtests load memory night by night; memory is reset per backtest run |

These rules are enforced in code (`src/vogdesk/asof.py`) and tested (`tests/`).

## Model (forecast 4), specified before fitting

- Logistic regression with L2 penalty on standardised inputs. Missing inputs are imputed with the training median and get a missing indicator.
- Penalty strength C chosen from {0.01, 0.03, 0.1, 0.3, 1, 3} by Brier score in 5-fold cross-validation over contiguous blocks of the training year (folds are calendar blocks, never shuffled days).
- Calibration: isotonic regression fit on the out-of-fold predictions of the chosen C; the final logistic regression is refit on all training nights and its output passed through that isotonic map.
- Fit on 2025 only. It is not refit on the development or held-out data. The live desk uses the same frozen model.
- Primary inputs, chosen on physical grounds before looking at any relationship with the outcome:
  1. Fountaining episode active at issue, as known at issue (0 or 1).
  2. Hours since the last known episode end, log-scaled, capped at 60 days; 0 while an episode is active.
  3. Pāhala maximum hourly SO2 over the 24 usable hours before issue, log(1 + ppb).
  4. Pāhala mean hourly SO2 over the 6 usable hours before issue, log(1 + ppb).
  5. Hawaiʻi Volcanoes NP (AQS 15-001-0005, near the summit) maximum over the 6 usable hours before issue, log(1 + ppb).
  6. GFS 925 hPa wind at the summit point: component along the summit-to-Pāhala bearing (about 223°), mean over leads 10, 16 and 22 h (positive means toward Pāhala).
  7. GFS 925 hPa wind speed at the summit point, mean over the three leads.
  8. GFS 850 hPa wind at the summit point, component along the same bearing, mean over the three leads.
  9. Fraction of the three leads with the 925 hPa wind at the summit blowing from within ±35° of 043° (toward Pāhala).
  10. GFS 10 m wind speed at the Pāhala point, mean over leads 16 and 22 h (00:00 and 06:00 HST).
  11. GFS boundary-layer height at the Pāhala point, minimum over leads 16 and 22 h, log-scaled.
  12. Hilo 00Z sounding: height of the base of the lowest temperature inversion between 500 and 4,000 m (4,000 m if none).
- Left out of the plan's longer list, with reasons: calendar month and weekday (one training year cannot separate season from episode timing; weekday has no physical path to volcanic SO2); fountain height of the last episode (the USGS table is retrospective and its publication times are unknown); HVO forecast windows (only 4 parsed); Ocean View recent SO2 (downwind of Pāhala on most nights, so it adds little and costs a parameter with 21 training events).
- The development period may change inputs, C grid, calibration method (isotonic, Platt or none) and clipping. Every change is logged in DEVLOG.md with its development score before and after. Nothing is changed after the held-out run.

## Forecasts compared

1. Climatology: 0.059, the 2025 training base rate, frozen.
2. Persistence: 1 if any usable hour (the 24 hours published by issue, hour-beginning 17:00 HST D−1 to 16:00 HST D) reached 75 ppb, else 0.
3. Episode rule: fountaining known to be active in the 24 hours before issue.
4. Model only: the logistic regression above.
5. Desk without review: the agents without the duty lead's re-query step.
6. Full desk.
7. Recent-rate climatology (added after freezing, see below): event rate over the last 30 verified nights.

## Action threshold and recommendation

- The action threshold t* on probability is chosen on the development period by maximising CSI over the grid 0.01, 0.02, ..., 0.60, separately for each probabilistic forecast (4, 5, 6). If several grid values tie, t* is their median (the lower one for an even count). If the development period has fewer than 5 event nights, the training out-of-fold predictions and the development nights are pooled for this choice. t* is then frozen and recorded below before the held-out run.
- Binary forecasts (2, 3) warn when they equal 1. Climatology warns when 0.059 ≥ t* of the model.
- Recommendation from probability p, following the 2018 schools plan: p < t*: normal operations, probability shown; t* ≤ p < 2·t*: prepare for yellow (the plan's yellow actions ready for the morning); p ≥ 2·t*, or any of the top 10 analog nights had an hourly maximum of at least 0.21 ppm: prepare for orange. The duty lead may instead hand the case to a human with a stated reason. The desk recommends; the school's designated person decides.

## Metrics

- Contingency table, POD, false-alarm ratio, CSI at the action threshold.
- Brier score and Brier skill score against climatology and against persistence; reliability diagram with bin counts.
- ROC AUC.
- Relative economic value for cost-loss ratios 0.05 to 0.5, with always-act and never-act references.
- "Hand to a human" outcomes are counted separately and never as correct. For Brier, a handed-over night is scored at the probability the desk showed with the handover.
- Confidence intervals: block bootstrap over contiguous calendar blocks of 7 days, including quiet days, 2,000 resamples; sensitivity with 3-day and 14-day blocks. The number of 7-day blocks containing at least one event is reported as the effective sample size.

## Primary claim

On the held-out period, the full desk has a lower Brier score than the model-only forecast. The paired difference and its block-bootstrap 90% interval are reported whatever the sign.

## Frozen values (filled in before the held-out run, 24 October 2026, commit 4b766f1 at 03:55 PDT)

Chosen on the development period by the procedure above; see DEVLOG.md for the numbers behind each.

- t* for forecast 4 (model only): 0.02 (the CSI-maximising value on dev; the frozen model is anchored to 2025's base rate, so its warnings sit at the bottom of the grid).
- t* for forecasts 5 and 6 (the desk): 0.18 (CSI 0.262 on dev for the full desk; the no-review variant shares the base probability and the same value).
- Desk rules frozen with t*: base probability from the first available stream in the order memory model, recent rate, frozen model; the memory model is the pre-registered pipeline refit on verified nights in memory within the last 120 days (at least 40 nights and 3 events, else the whole history with a 60-day half-life); the DutyLead may adjust the base by at most 0.15, and only on nights where the review raised a flag; review checks: model versus memory-model gap 0.3, analog split 3 and 3 among the analogs, analogs sparse below 5, persistence contradiction (hours at or above 75 ppb with both guidances below 0.15, or 24 h maximum below 10 ppb with both above 0.5), regime unseen (any model input outside the 2025 training range), data gaps (no GFS run, no sounding in 18 h, fewer than 14 of 24 Pahala hours), stale data (latest Pahala hour before 13:00 HST), data quality failures; at most 2 re-query rounds; handover forced when the model and the memory model still differ by more than 0.3 after 2 rounds with the disagreement flag unresolved, or the GFS run is still missing. Analogs: 20 nights from the last 365 days when at least 5 exist, event rate shrunk toward the recent rate with weight 10. Memory becomes available at 12:00 HST on D+1 plus 2 h.
- Freezing commit: the commit that adds this section. Nothing in the desk or the model changes after it until the held-out period has been scored.

## Changes after freezing

1. **23 October 2026, 19:50 PDT, forecast 7 added.** The development split showed a regime shift (DEVLOG.md, 23 Oct): the 2026 base rate is 0.19 against 0.059 in training, and the physical drivers changed sign. The model-only forecast (4) stays exactly as pre-registered, fit on 2025 only, so that the comparison "desk versus the model it was given" is clean. To make sure the desk is not credited merely for tracking a base-rate shift that any adaptive rule would track, a seventh forecast is added as a second reference: **recent-rate climatology**, the event rate over the last 30 verified nights before issue (nights whose window closed and were verified by 12:00 HST the day before issue; fewer than 10 such nights falls back to forecast 1). It uses no model, no memory engine and no reasoning. The primary claim is unchanged (6 beats 4 on Brier); a secondary claim is recorded: the full desk also has a lower Brier than forecast 7. Decided by Zeyu on 23 Oct after seeing dev results, before any held-out scoring.

## Changes made while drafting, before this file was frozen

- Persistence now uses only the hours published by issue (it used the 24 hours ending at 18:00 HST in DAY0.md, which includes an hour not yet posted at issue). DAY0.md numbers were computed with the old definition.
- The model specification, availability table and action-threshold procedure were added on 23 October 2026, before any model was fit.
2. **24 October 2026, 06:10 PDT, after the held-out scoring, display only.** Review flags raised twice for the same item (once from the original query and once from its re-query copy) are now reported once. This changes no probability, threshold or decision: the adjustment gate asks only whether any flag was raised. Logged because the freezing commit said nothing in the desk changes.
3. **24 October 2026, 09:00 PDT, after the held-out scoring, live desk only.** The desk's G3 stream (recent event rate) queried memory with a cap of 400 records ranked by relevance and took the newest 30 of those; with more than 400 nights in memory it was therefore not the last 30 nights. The cap was raised so the stream reads every night in memory. The held-out results stand as run and are not re-scored; the defect and its reach are described in DEVLOG.md and RESULTS.md. Forecast 7 in every table was always computed from the labels and was not affected.
4. **24 October 2026, 09:20 PDT, live desk only.** The memory sidecar capped every query at 100 records, so on the live path the memory model trained on 100 relevance-ranked nights instead of the last 120 days, and G3 had the same truncation. The cap was raised. Backtests (dev and held-out) used the in-process memory, which has no cap, and are unaffected. The 23 October live forecast was made with the capped sidecar and stands as issued.
