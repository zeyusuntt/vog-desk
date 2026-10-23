# Pre-registration (draft, to be committed on 23 October 2026 before any model is fit)

Everything below is fixed before the development or held-out data are scored. Changes after the commit that freezes this file are listed at the bottom with a reason. The held-out period is scored once.

## Question and event

- Station: Pāhala, AQS 15-001-2016.
- Issue: 18:00 HST on day D. Window: 18:00 HST day D to 12:00 HST day D+1.
- Event: any hourly SO2 at or above 75 ppb in the window. Chosen on 22 October 2026 by a rule written before any data were examined (0.11 ppm if 2025 had at least 20 such nights; it had 13).
- Validity: a night is positive if any hour meets the threshold; negative only if at least 14 of 18 hours are present; otherwise excluded.
- Label source: AQS validated data where available (to 31 March 2026), AirNow preliminary data after that.

## Splits

- Training: issue dates 1 January to 31 December 2025.
- Development: 1 January to 30 June 2026.
- Held out: 1 July to 21 October 2026. Scored once, after the system and all thresholds are frozen.
- Live: issue dates from 23 October 2026, committed before the outcome is known. Reported separately; it shows the system runs, not that it is calibrated.

## Information available at issue

AQS and AirNow hours before issue; HVO facts after their first publication (or 12 hours after the event when no notice matches); the GFS 18Z run of day D; soundings up to 00Z on D+1 (14:00 HST on D). H-MEM and analog memory hold only nights whose outcome was known before issue. Enforced in code and tested.

## Forecasts compared

1. Climatology: 0.059, the 2025 training base rate, frozen.
2. Persistence: event in the 24 hours before issue.
3. Episode rule: fountaining known to be active in the 24 hours before issue.
4. Model only: one logistic regression with calibration, fit on training only.
5. Desk without review: the agents without the duty lead's re-query step.
6. Full desk.

## Metrics

- Contingency table, POD, false-alarm ratio, CSI at the action threshold.
- Brier score and Brier skill score against climatology and against persistence; reliability diagram with bin counts.
- ROC AUC.
- Relative economic value for cost-loss ratios 0.05 to 0.5, with always-act and never-act references.
- "Hand to a human" outcomes are counted separately and never as correct.
- Confidence intervals: block bootstrap over contiguous calendar blocks of 7 days, including quiet days, 2,000 resamples; sensitivity with 3-day and 14-day blocks.

## Primary claim

On the held-out period, the full desk has a lower Brier score than the model-only forecast. The paired difference and its block-bootstrap 90% interval are reported whatever the sign. The action threshold on probability is chosen on the development period by maximising CSI and then frozen.

## Changes after freezing

None yet.
