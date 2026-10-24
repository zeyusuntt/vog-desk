"""The night scene shows only what the desk could see at 18:00, and the monitor hours only after verification."""
from datetime import datetime, timezone

import pandas as pd
import pytest

from vogdesk import sources
from vogdesk.desk.verifier import verification_time
from vogdesk.scene import STATIONS, build_scene

DATE = "2026-05-07"  # a recorded sample night, present in the full data and in samples/data


@pytest.fixture(scope="module")
def src():
    return sources.load()


def test_hours_are_withheld_until_the_night_is_verified(src):
    before = verification_time(pd.Timestamp(DATE)) - pd.Timedelta(minutes=1)
    scene = build_scene(src, DATE, now_utc=before.to_pydatetime())
    assert scene["hours"] is None and scene["hours_source"] is None


def test_hours_appear_after_verification_for_every_monitor(src):
    after = verification_time(pd.Timestamp(DATE)) + pd.Timedelta(minutes=1)
    scene = build_scene(src, DATE, now_utc=after.to_pydatetime())
    assert set(scene["hours"]) == {s["id"] for s in STATIONS}
    assert all(len(v) == 18 for v in scene["hours"].values())
    assert any(v is not None for v in scene["hours"]["pahala"])


def test_forecast_part_comes_from_the_issue_time_evidence(src):
    scene = build_scene(src, DATE, now_utc=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert scene["issue_utc"].startswith("2026-05-08T04:00")
    assert 1 <= len(scene["wind"]) <= 3
    for w in scene["wind"]:
        assert 0 <= w["from_deg"] <= 360 and w["speed_ms"] >= 0
    assert scene["inversion"] is None or 500 <= scene["inversion"]["base_m"] < 4000
