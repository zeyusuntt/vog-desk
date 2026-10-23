"""Guards against using information that did not exist at issue time (18:00 HST)."""
import pandas as pd

from vogdesk.baselines import episode_rule, issue_time_utc, persistence
from vogdesk.labels import night_labels


def _hourly(values_by_hst_hour: dict[str, float]) -> pd.Series:
    idx = [pd.Timestamp(k) + pd.Timedelta(hours=10) for k in values_by_hst_hour]  # HST -> UTC
    return pd.Series(list(values_by_hst_hour.values()), index=pd.DatetimeIndex(idx).tz_localize("UTC"))


def test_issue_time_is_18_hst():
    assert issue_time_utc(pd.Timestamp("2025-09-02")) == pd.Timestamp("2025-09-03 04:00", tz="UTC")


def test_label_window_is_18_to_12_hst():
    hours = {f"2025-09-02 {h:02d}:00": 10.0 for h in range(0, 24)} | {f"2025-09-03 {h:02d}:00": 10.0 for h in range(0, 24)}
    hours["2025-09-02 17:00"] = 500.0  # before the window: must not count for 2025-09-02
    hours["2025-09-03 12:00"] = 500.0  # after the window: must not count
    lab = night_labels(_hourly(hours), 75)
    assert not bool(lab.loc["2025-09-02", "event"])
    hours["2025-09-03 05:00"] = 80.0  # inside the window
    lab = night_labels(_hourly(hours), 75)
    assert bool(lab.loc["2025-09-02", "event"])


def test_missing_window_is_not_a_negative():
    hours = {f"2025-09-02 {h:02d}:00": 10.0 for h in range(18, 24)}  # only 6 of 18 hours
    lab = night_labels(_hourly(hours), 75)
    assert not bool(lab.loc["2025-09-02", "valid"])


def test_persistence_ignores_hours_after_issue():
    hours = {f"2025-09-02 {h:02d}:00": 10.0 for h in range(0, 24)}
    hours["2025-09-02 19:00"] = 500.0  # after 18:00 issue
    p = persistence(_hourly(hours), pd.DatetimeIndex([pd.Timestamp("2025-09-02")]), 75)
    assert p.iloc[0] == 0.0


def test_episode_rule_waits_for_publication():
    t_issue = issue_time_utc(pd.Timestamp("2025-09-02"))
    nat = pd.Series([pd.NaT], dtype="datetime64[ns, UTC]")
    ep = pd.DataFrame({"episode": [1], "start_utc": [t_issue - pd.Timedelta(hours=2)],
                       "end_utc": nat, "start_known_utc": [t_issue + pd.Timedelta(minutes=30)],
                       "end_known_utc": nat})
    assert episode_rule(ep, pd.DatetimeIndex([pd.Timestamp("2025-09-02")])).iloc[0] == 0.0
    ep["start_known_utc"] = t_issue - pd.Timedelta(minutes=30)
    assert episode_rule(ep, pd.DatetimeIndex([pd.Timestamp("2025-09-02")])).iloc[0] == 1.0
