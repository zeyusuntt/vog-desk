"""The no-lookahead rule holds against Zetaris itself, not only against the Parquet files.

Needs the local Zetaris instance with the pahala_exposure USL activated (infra/zetaris/setup.py);
skipped when it is not reachable.
"""
import pandas as pd
import pytest

from vogdesk.zetaris import SqlRefused, Zetaris, ZetarisError

T = pd.Timestamp("2025-09-03 04:00", tz="UTC")   # 18:00 HST on 2 Sep 2025 (AQS era)
T2 = pd.Timestamp("2026-04-03 04:00", tz="UTC")  # 18:00 HST on 2 Apr 2026 (AirNow era, wind exists)


@pytest.fixture(scope="module")
def z():
    try:
        client = Zetaris.from_env()
        client.execute("SELECT 1 AS one", limit=1)
    except (ZetarisError, KeyError) as e:
        pytest.skip(f"Zetaris not reachable: {e}")
    return client


def _ts(v: str) -> pd.Timestamp:
    return pd.Timestamp(v).tz_localize("UTC")


@pytest.mark.parametrize("view,t", [("so2_hourly", T), ("gfs_points", T), ("sounding_levels", T),
                                    ("station_wind", T2), ("so2_hourly", T2)])
def test_nothing_after_as_of_comes_back(z, view, t):
    r = z.run_sql(f"SELECT max(available_utc) AS latest, count(*) AS n FROM pahala_exposure.{view}", t)
    row = r.rows[0]
    assert int(row["n"]) > 0, f"{view} returned no rows before {t}"
    assert _ts(row["latest"]) <= t, f"{view}: {row['latest']} is after as_of {t}"


def test_gfs_run_visible_at_issue_is_18z_of_issue_day(z):
    r = z.run_sql("SELECT max(run_init_utc) AS run FROM pahala_exposure.gfs_points", T)
    assert _ts(r.rows[0]["run"]) == pd.Timestamp("2025-09-02 18:00", tz="UTC")


def test_last_pahala_hour_visible_at_issue_begins_at_16_hst(z):
    r = z.run_sql("SELECT max(time_utc) AS t FROM pahala_exposure.so2_hourly WHERE site_id = '150012016'", T)
    assert _ts(r.rows[0]["t"]) == pd.Timestamp("2025-09-03 02:00", tz="UTC")


def test_episodes_show_only_published_boundaries(z):
    r = z.run_sql("SELECT episode, start_known_utc, end_utc, end_known_utc FROM pahala_exposure.episodes_known", T)
    assert r.rows, "no episodes visible"
    for row in r.rows:
        assert _ts(row["start_known_utc"]) <= T
        if row["end_known_utc"] is not None:
            assert _ts(row["end_known_utc"]) <= T
        else:
            assert row["end_utc"] is None, f"episode {row['episode']}: end shown before it was published"
    # Episode 32 started on 2 Sep 2025 HST and ended after issue: visible, end masked.
    ep32 = [row for row in r.rows if int(row["episode"]) == 32]
    assert ep32 and ep32[0]["end_utc"] is None
    assert not [row for row in r.rows if int(row["episode"]) > 32]


def test_fountain_height_is_never_exposed(z):
    with pytest.raises(ZetarisError):
        z.run_sql("SELECT max_fountain_m FROM pahala_exposure.episodes_known", T)


def test_base_tables_are_refused(z):
    with pytest.raises(SqlRefused):
        z.run_sql("SELECT count(*) FROM VOG_S3.aqs_hawaii_so2", T)
