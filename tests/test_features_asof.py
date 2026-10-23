"""The feature layer must not see anything that was not public at 18:00 HST on the issue date.

Rules under test are frozen in docs/PREREG.md ("Information available at issue").
"""
import numpy as np
import pandas as pd
import pytest

from vogdesk.asof import gfs_run_for, issue_time_utc
from vogdesk.features import episode_state, features_for, gfs_features, raob_features, usable_hours
from vogdesk.sources import SITES, Sources

D = pd.Timestamp("2025-09-02")
T = issue_time_utc(D)  # 2025-09-03 04:00 UTC


def _so2(site: str, hours_hst: dict[str, float]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(k) + pd.Timedelta(hours=10) for k in hours_hst]).tz_localize("UTC")
    df = pd.DataFrame({"site_id": SITES[site], "time_utc": idx, "so2_ppb": list(hours_hst.values()),
                       "source": "airnow", "preliminary": True})
    df["available_utc"] = df["time_utc"] + pd.Timedelta(hours=2)
    return df


def _gfs(run_init: pd.Timestamp, value: float = 1.0) -> pd.DataFrame:
    rows = []
    for fxx in (10, 16, 22):
        for var, level in (("UGRD", "925 mb"), ("VGRD", "925 mb"), ("UGRD", "850 mb"), ("VGRD", "850 mb"),
                           ("UGRD", "10 m above ground"), ("VGRD", "10 m above ground"), ("HPBL", "surface")):
            for point in ("summit", "pahala"):
                rows.append({"run_init_utc": run_init, "fxx": fxx, "var": var, "level": level, "point": point,
                             "value": value, "available_utc": run_init + pd.Timedelta(hours=6)})
    return pd.DataFrame(rows)


def _sources(so2=None, episodes=None, gfs=None, raob=None) -> Sources:
    empty_so2 = _so2("pahala", {}).iloc[0:0]
    nat = pd.Series([], dtype="datetime64[ns, UTC]")
    empty_ep = pd.DataFrame({"episode": [], "start_utc": nat, "end_utc": nat, "start_known_utc": nat, "end_known_utc": nat})
    empty_gfs = _gfs(gfs_run_for(D)).iloc[0:0]
    empty_raob = pd.DataFrame({"valid_utc": nat, "inversion_base_m": [], "available_utc": nat})
    return Sources(so2=empty_so2 if so2 is None else so2, episodes=empty_ep if episodes is None else episodes,
                   gfs=empty_gfs if gfs is None else gfs, raob=empty_raob if raob is None else raob)


def test_last_usable_hour_is_16_to_17_hst():
    # 17:00 HST hour-beginning is published at 19:00 HST, after issue; 16:00 is published at 18:00, usable.
    so2 = _so2("pahala", {"2025-09-02 16:00": 40.0, "2025-09-02 17:00": 900.0, "2025-09-02 18:00": 900.0})
    h = usable_hours(_sources(so2=so2), "pahala", T, 24)
    assert h.max() == 40.0
    assert len(h) == 1


def test_window_of_24_usable_hours_starts_at_17_hst_previous_day():
    hours = {f"2025-09-01 {h:02d}:00": 1.0 for h in range(0, 24)} | {f"2025-09-02 {h:02d}:00": 1.0 for h in range(0, 24)}
    hours["2025-09-01 16:00"] = 500.0  # 25th hour back: outside
    hours["2025-09-01 17:00"] = 200.0  # 24th hour back: inside
    h = usable_hours(_sources(so2=_so2("pahala", hours)), "pahala", T, 24)
    assert len(h) == 24 and h.max() == 200.0


def test_gfs_uses_only_the_18z_run_of_issue_day():
    good = _gfs(gfs_run_for(D), value=1.0)
    next_run = _gfs(pd.Timestamp("2025-09-03 00:00", tz="UTC"), value=50.0)  # 00Z D+1: complete only after issue
    prev_run = _gfs(pd.Timestamp("2025-09-02 12:00", tz="UTC"), value=50.0)  # older run: not the specified one
    f = gfs_features(pd.concat([good, next_run, prev_run]), D, T)
    assert f["gfs925_speed_summit"] == pytest.approx(np.hypot(1.0, 1.0))
    only_wrong = gfs_features(pd.concat([next_run, prev_run]), D, T)
    assert np.isnan(only_wrong["gfs925_speed_summit"])


def test_gfs_run_for_issue_day_is_18z_same_utc_day():
    assert gfs_run_for(D) == pd.Timestamp("2025-09-02 18:00", tz="UTC")
    assert gfs_run_for(D) + pd.Timedelta(hours=6) <= T


def test_sounding_after_issue_is_not_used():
    raob = pd.DataFrame({
        "valid_utc": pd.to_datetime(["2025-09-02 12:00", "2025-09-03 00:00", "2025-09-03 12:00"], utc=True),
        "inversion_base_m": [1500.0, 2200.0, 900.0]})
    raob["available_utc"] = raob["valid_utc"] + pd.Timedelta(hours=3)
    f = raob_features(raob, T)
    assert f["raob_inversion_base_m"] == 2200.0  # 00Z D+1 (14:00 HST), not the 12Z D+1 launch


def test_episode_end_counts_only_once_published():
    nat = pd.Series([pd.NaT], dtype="datetime64[ns, UTC]")
    ep = pd.DataFrame({"episode": [1], "start_utc": [T - pd.Timedelta(days=2)], "end_utc": [T - pd.Timedelta(hours=3)],
                       "start_known_utc": [T - pd.Timedelta(days=2)], "end_known_utc": [T + pd.Timedelta(hours=1)]})
    s = episode_state(ep, T)
    assert s["episode_active"] == 1.0 and s["log_hours_since_end"] == 0.0
    ep["end_known_utc"] = T - pd.Timedelta(hours=1)
    s = episode_state(ep, T)
    assert s["episode_active"] == 0.0 and s["log_hours_since_end"] == pytest.approx(np.log1p(3.0))


def test_episode_start_counts_only_once_published():
    nat = pd.Series([pd.NaT], dtype="datetime64[ns, UTC]")
    ep = pd.DataFrame({"episode": [1], "start_utc": [T - pd.Timedelta(hours=2)], "end_utc": nat,
                       "start_known_utc": [T + pd.Timedelta(minutes=30)], "end_known_utc": nat})
    assert episode_state(ep, T)["episode_active"] == 0.0


def test_features_for_is_nan_not_future_when_sources_are_empty():
    f = features_for(_sources(), D)
    assert f["issue_utc"] == T
    assert np.isnan(f["log_pahala_max_24h"]) and np.isnan(f["gfs925_along_summit"]) and np.isnan(f["raob_inversion_base_m"])
    assert f["episode_active"] == 0.0


def test_feature_values_are_identical_when_future_rows_are_added():
    """Adding rows published after issue must not change any feature (property over random future rows)."""
    rng = np.random.default_rng(0)
    base_hours = {f"2025-09-02 {h:02d}:00": float(v) for h, v in zip(range(0, 17), rng.integers(0, 50, 17))}
    so2 = _so2("pahala", base_hours)
    gfs = _gfs(gfs_run_for(D), value=2.0)
    src = _sources(so2=so2, gfs=gfs)
    before = features_for(src, D)
    future_hours = {f"2025-09-03 {h:02d}:00": float(v) for h, v in zip(range(0, 12), rng.integers(100, 900, 12))}
    future_hours["2025-09-02 17:00"] = 777.0
    src2 = _sources(so2=pd.concat([so2, _so2("pahala", future_hours)]),
                    gfs=pd.concat([gfs, _gfs(pd.Timestamp("2025-09-03 00:00", tz="UTC"), value=99.0)]))
    after = features_for(src2, D)
    for k, v in before.items():
        if isinstance(v, float) and np.isnan(v):
            assert np.isnan(after[k]), k
        else:
            assert after[k] == v, k
