"""Analog retrieval, conflict flags, night records and the recent-rate forecast (no network)."""
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from vogdesk.analogs import (
    AnalogResult, conflict_flags, night_record, retrieve_analogs, situation_tags, situation_text, wilson,
)
from vogdesk.baselines import recent_rate
from vogdesk.features import FEATURES
from vogdesk.memory import FakeMemory, MemoryRecord, Retrieved

UTC = timezone.utc
NAN = float("nan")


def feats(**kw) -> dict:
    base = {k: NAN for k in FEATURES}
    base.update(episode_active=0.0, gfs925_speed_summit=5.0, gfs925_along_summit=4.0,
                gfs925_sector_frac=1.0, raob_inversion_base_m=1500.0, log_pahala_max_24h=math.log1p(10.0))
    base.update(kw)
    return base


def tag(tags: list[str], prefix: str) -> str:
    return next(t for t in tags if t.startswith(prefix))


# situation_tags

def test_regime_bins():
    assert "regime:trade" in situation_tags(feats(gfs925_speed_summit=6.0, gfs925_along_summit=3.0))
    assert "regime:kona" in situation_tags(feats(gfs925_speed_summit=6.0, gfs925_along_summit=-3.0))
    assert "regime:cross" in situation_tags(feats(gfs925_speed_summit=6.0, gfs925_along_summit=1.0))
    assert "regime:cross" in situation_tags(feats(gfs925_speed_summit=6.0, gfs925_along_summit=-2.9))
    assert "regime:light" in situation_tags(feats(gfs925_speed_summit=1.9, gfs925_along_summit=1.9))
    assert "regime:trade" in situation_tags(feats(gfs925_speed_summit=2.0, gfs925_along_summit=1.0))


def test_episode_sector_inversion_recent_bins():
    t = situation_tags(feats(episode_active=1.0, gfs925_sector_frac=0.0, raob_inversion_base_m=999.0))
    assert {"station:pahala", "episode:active", "sector:none", "inversion:low"} <= set(t)
    assert "episode:pause" in situation_tags(feats(episode_active=0.0))
    assert "sector:part" in situation_tags(feats(gfs925_sector_frac=0.33))
    assert "sector:all" in situation_tags(feats(gfs925_sector_frac=1.0))
    for base, want in [(1000.0, "mid"), (2000.0, "mid"), (2001.0, "high"), (3999.0, "high"), (4000.0, "none")]:
        assert f"inversion:{want}" in situation_tags(feats(raob_inversion_base_m=base)), base
    for ppb, want in [(0.0, "quiet"), (19.0, "quiet"), (20.0, "elevated"), (74.0, "elevated"),
                      (75.0, "event"), (300.0, "event")]:
        assert f"pahala_recent:{want}" in situation_tags(feats(log_pahala_max_24h=math.log1p(ppb))), ppb


def test_nan_gives_unknown_tags():
    t = situation_tags({k: NAN for k in FEATURES})
    assert {"regime:unknown", "sector:unknown", "inversion:unknown", "pahala_recent:unknown",
            "station:pahala", "episode:pause"} == set(t)
    text = situation_text({k: NAN for k in FEATURES}, t)
    assert text.count("unknown") >= 5


def test_tags_are_sorted_and_deterministic():
    f = feats()
    assert situation_tags(f) == situation_tags(dict(f)) == sorted(situation_tags(f))


def test_situation_text_reports_given_numbers():
    f = feats(gfs925_speed_summit=5.26, raob_inversion_base_m=1534.0, log_pahala_max_24h=math.log1p(12.0))
    text = situation_text(f, situation_tags(f))
    assert "5.3 m/s" in text and "1534 m" in text and "12 ppb" in text and "trade" in text
    assert "," not in text


# retrieve_analogs

def stored(date: str, avail: datetime, event: bool, f: dict | None = None) -> MemoryRecord:
    return night_record(date, f or feats(), event, 90.0 if event else 5.0, 18, f"verifier:{date}", avail)


def test_retrieve_never_returns_a_night_verified_after_issue():
    mem = FakeMemory()
    as_of = datetime(2026, 2, 1, 4, 0, tzinfo=UTC)
    before = stored("2026-01-30", as_of - timedelta(hours=10), True)
    after = stored("2026-01-31", as_of + timedelta(hours=10), False)
    mem.add(before)
    mem.add(after)
    res = retrieve_analogs(mem, feats(), as_of)
    assert res.analog_ids == [before.id]
    assert after.id not in res.analog_ids
    assert res.analog_nights == ["2026-01-30"]
    assert res.as_of_utc == as_of
    assert res.n == 1 and res.n_events == 1 and res.p == 1.0


def test_retrieve_requires_aware_time():
    with pytest.raises(ValueError):
        retrieve_analogs(FakeMemory(), feats(), datetime(2026, 2, 1, 4, 0))


class RecordingMemory:
    """Returns records only when the query tags are a subset of those the record was stored with."""

    def __init__(self, records: list[MemoryRecord]) -> None:
        self.records, self.calls = records, []

    def query(self, text, tags, as_of_utc, k=10):
        self.calls.append((text, list(tags), as_of_utc, k))
        hits = [r for r in self.records if set(tags) <= set(r.tags) and r.available_utc <= as_of_utc]
        return [Retrieved(record=r, score=1.0, trace={"matched_tags": list(tags)}) for r in hits[:k]]


def test_fallback_drops_inversion_then_sector():
    as_of = datetime(2026, 3, 1, 4, 0, tzinfo=UTC)
    avail = datetime(2026, 2, 1, tzinfo=UTC)
    f = feats()
    other_inv = feats(raob_inversion_base_m=500.0)  # same except inversion
    other_sector = feats(raob_inversion_base_m=500.0, gfs925_sector_frac=0.0)  # inversion and sector differ

    # level 0: five matching nights
    mem = RecordingMemory([stored(f"2026-01-{i:02d}", avail, i % 2 == 0, f) for i in range(1, 6)])
    res = retrieve_analogs(mem, f, as_of)
    assert res.fallback_level == 0 and len(mem.calls) == 1
    assert "inversion:mid" in res.tags_used and "sector:all" in res.tags_used

    # level 1: only the inversion differs
    mem = RecordingMemory([stored(f"2026-01-{i:02d}", avail, True, other_inv) for i in range(1, 6)])
    res = retrieve_analogs(mem, f, as_of)
    assert res.fallback_level == 1 and len(mem.calls) == 2
    assert not any(t.startswith("inversion:") for t in res.tags_used)
    assert "sector:all" in res.tags_used and res.n == 5

    # level 2: inversion and sector both differ
    mem = RecordingMemory([stored(f"2026-01-{i:02d}", avail, False, other_sector) for i in range(1, 6)])
    res = retrieve_analogs(mem, f, as_of)
    assert res.fallback_level == 2 and len(mem.calls) == 3
    assert not any(t.startswith(("inversion:", "sector:")) for t in res.tags_used)
    for keep in ("station:pahala", "episode:pause", "regime:trade"):
        assert keep in res.tags_used
    assert all(c[2] == as_of for c in mem.calls)  # the cutoff never changes
    assert not any(t.startswith("pahala_recent:") for c in mem.calls for t in c[1])


def test_records_without_event_are_listed_but_not_counted():
    mem = FakeMemory()
    as_of = datetime(2026, 3, 1, 4, 0, tzinfo=UTC)
    avail = datetime(2026, 2, 1, tzinfo=UTC)
    mem.add(stored("2026-01-01", avail, True))
    odd = stored("2026-01-02", avail, False)
    odd.metadata = {"note": "no outcome"}
    mem.add(odd)
    res = retrieve_analogs(mem, feats(), as_of)
    assert res.n == 1 and res.n_events == 1 and len(res.analog_ids) == 2
    assert {t["event"] for t in res.trace} == {True, None}
    assert res.max_ppb_seen == 90.0
    empty = retrieve_analogs(FakeMemory(), feats(), as_of)
    assert empty.p is None and empty.lo is None and empty.n == 0 and empty.max_ppb_seen is None


def test_split_needs_three_of_each():
    mem = FakeMemory()
    as_of = datetime(2026, 3, 1, 4, 0, tzinfo=UTC)
    avail = datetime(2026, 2, 1, tzinfo=UTC)
    for i in range(6):
        mem.add(stored(f"2026-01-{i + 1:02d}", avail, i < 3))
    assert retrieve_analogs(mem, feats(), as_of).split
    mem = FakeMemory()
    for i in range(6):
        mem.add(stored(f"2026-01-{i + 1:02d}", avail, i < 2))
    assert not retrieve_analogs(mem, feats(), as_of).split


# wilson

def test_wilson():
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(3, 10)
    assert 0 < lo < 0.3 < hi < 1
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and 0 < hi < 0.3
    lo, hi = wilson(10, 10)
    assert hi == 1.0 and lo > 0.7


# conflict_flags

def result(n: int, n_events: int) -> AnalogResult:
    ids = [f"id{i}" for i in range(n)]
    return AnalogResult(
        p=n_events / n if n else None, lo=None, hi=None, n=n, n_events=n_events, analog_ids=ids,
        analog_nights=[], max_ppb_seen=None, tags_used=[], query_text="", as_of_utc=datetime(2026, 1, 1, tzinfo=UTC),
        split=n_events >= 3 and n - n_events >= 3, fallback_level=0,
    )


def codes(flags: list[dict]) -> list[str]:
    return [x["code"] for x in flags]


def test_conflict_flags():
    assert conflict_flags(0.1, result(10, 1)) == []
    split = conflict_flags(0.5, result(10, 5))
    assert codes(split) == ["analog_split"]
    assert split[0]["evidence_ids"] == [f"id{i}" for i in range(10)]
    assert codes(conflict_flags(0.1, result(10, 5))) == ["analog_split", "model_analog_disagree"]
    assert codes(conflict_flags(0.7, result(10, 2))) == ["model_analog_disagree"]
    assert conflict_flags(0.5, result(10, 2)) == []  # gap exactly 0.3 is not above it
    assert codes(conflict_flags(0.1, result(4, 0))) == ["analogs_sparse"]
    assert codes(conflict_flags(0.1, result(0, 0))) == ["analogs_sparse"]
    assert codes(conflict_flags(0.5, result(10, 2), gap=0.1)) == ["model_analog_disagree"]


# night_record

def test_night_record():
    avail = datetime(2026, 1, 6, 22, 0, tzinfo=UTC)
    f = feats(log_hvnp_max_6h=NAN)
    r = night_record("2026-01-05", f, True, 93.0, 18, "verifier:2026-01-05", avail, p_model=0.2, p_analog=NAN)
    assert r.confidence == 0.9 and r.available_utc == avail
    assert "night:2026-01-05" in r.tags and "outcome:event" in r.tags and "station:pahala" in r.tags
    assert r.content.endswith("Outcome: event, max 93 ppb.")
    assert r.metadata["event"] is True and r.metadata["max_ppb"] == 93.0 and r.metadata["n_hours"] == 18
    assert r.metadata["p_model"] == 0.2 and r.metadata["p_analog"] is None
    assert r.metadata["features"]["log_hvnp_max_6h"] is None
    vals = [v for v in r.metadata["features"].values() if v is not None]
    assert vals and all(math.isfinite(v) for v in vals)
    q = night_record("2026-01-06", f, False, 12.0, 14, "v", avail)
    assert q.confidence == 0.75 and "outcome:quiet" in q.tags and q.content.endswith("Outcome: quiet, max 12 ppb.")
    assert night_record("2026-01-07", f, False, 12.0, 17, "v", avail).confidence == 0.9
    assert night_record("2026-01-07", f, False, 12.0, 16, "v", avail).confidence == 0.75
    assert night_record("2026-01-07", f, False, 12.0, 13, "v", avail).confidence == 0.5
    mem = FakeMemory()
    mem.add(r)  # a record built here is accepted by memory
    assert r.model_dump(mode="json")


# recent_rate

def label_frame(events: set[str], start="2026-01-01", end="2026-02-28", invalid: set[str] = frozenset()) -> pd.DataFrame:
    idx = pd.date_range(start, end, freq="D", name="issue_date_hst")
    ev = np.array([d.strftime("%Y-%m-%d") in events for d in idx])
    valid = np.array([d.strftime("%Y-%m-%d") not in invalid for d in idx])
    return pd.DataFrame({"n_hours": 18, "max_ppb": np.where(ev, 90.0, 2.0), "event": ev, "valid": valid}, index=idx)


def test_recent_rate_last_30_nights_before_issue():
    d = pd.DatetimeIndex(["2026-02-01"])
    lab = label_frame({"2026-01-05", "2026-01-20"})
    out = recent_rate(lab, d)
    assert out.name == "recent_rate" and list(out.index) == list(d)
    assert out.iloc[0] == pytest.approx(2 / 30)
    # the night issued on the issue date is not verified yet
    same_day = recent_rate(label_frame({"2026-01-05", "2026-01-20", "2026-02-01"}), d)
    assert same_day.iloc[0] == pytest.approx(2 / 30)
    # the night issued the day before is verified
    day_before = recent_rate(label_frame({"2026-01-05", "2026-01-20", "2026-01-31"}), d)
    assert day_before.iloc[0] == pytest.approx(3 / 30)
    # older than 30 nights falls out of the window
    old = recent_rate(label_frame({"2026-01-01"}), d)
    assert old.iloc[0] == 0.0


def test_recent_rate_falls_back_with_few_nights():
    lab = label_frame({"2026-01-02"})
    out = recent_rate(lab, pd.DatetimeIndex(["2026-01-01", "2026-01-05", "2026-01-11", "2026-01-12"]))
    # prior nights: 0, 4, 10, 11 -> only the last two reach 10
    assert list(out.iloc[:2]) == [0.059, 0.059]
    assert out.iloc[2] == pytest.approx(1 / 10)
    assert out.iloc[3] == pytest.approx(1 / 11)
    assert recent_rate(lab, pd.DatetimeIndex(["2026-01-05"]), fallback=0.2).iloc[0] == 0.2


def test_recent_rate_skips_invalid_nights():
    d = pd.DatetimeIndex(["2026-02-01"])
    lab = label_frame({"2026-01-05"}, start="2025-12-01", invalid={"2026-01-31", "2026-01-30", "2026-01-29"})
    # the three invalid nights are skipped, so the window reaches back two nights further, to 2025-12-30
    assert recent_rate(label_frame({"2025-12-30"}, start="2025-12-01", invalid={"2026-01-31", "2026-01-30", "2026-01-29"}), d).iloc[0] == pytest.approx(1 / 30)
    assert recent_rate(label_frame({"2025-12-29"}, start="2025-12-01", invalid={"2026-01-31", "2026-01-30", "2026-01-29"}), d).iloc[0] == 0.0
    assert recent_rate(lab, d).iloc[0] == pytest.approx(1 / 30)
    # too many invalid nights leaves fewer than 10 usable ones
    lab = label_frame(set(), end="2026-01-20", invalid={f"2026-01-{i:02d}" for i in range(1, 12)})
    assert recent_rate(lab, pd.DatetimeIndex(["2026-01-21"])).iloc[0] == 0.059
