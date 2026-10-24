"""The memory model (desk/memory_model.py) and the forecaster glue around it, against an in-process FakeMemory.

Nights are synthetic: the 12 model inputs are drawn from a seeded generator and the outcome is planted so that a
higher 925 hPa along-summit wind means a higher event probability. No network, no LLM."""
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from vogdesk.analogs import AnalogResult, night_record
from vogdesk.desk import memory_model as mm
from vogdesk.desk.forecaster import analog_guidance, memory_model_guidance, review_flags
from vogdesk.features import FEATURES
from vogdesk.memory import FakeMemory, MemoryRecord

UTC = timezone.utc
SIGNAL = "gfs925_along_summit"


def available_utc(night: date) -> datetime:
    """12:00 HST on D+1 plus 2 hours, which is 22:00 UTC on D+1."""
    return datetime(night.year, night.month, night.day, 22, 0, tzinfo=UTC) + timedelta(days=1)


def synthetic_features(rng: np.random.Generator) -> dict:
    f = {name: float(rng.normal(0.0, 1.0)) for name in FEATURES}
    f["episode_active"] = 0.0
    f["gfs925_speed_summit"] = float(4.0 + rng.normal(0.0, 0.5))
    f["gfs925_sector_frac"] = float(rng.uniform(0.0, 1.0))
    f["raob_inversion_base_m"] = float(rng.uniform(500.0, 3000.0))
    f[SIGNAL] = float(rng.normal(1.5, 2.0))
    return f


def fill_memory(memory: FakeMemory, first_night: date, n_nights: int, events: list[bool] | None = None) -> list[dict]:
    """One verified night per day from first_night. Returns [{night, features, event, available_utc}], oldest first."""
    rng = np.random.default_rng(0)
    out = []
    for i in range(n_nights):
        night = first_night + timedelta(days=i)
        f = synthetic_features(rng)
        noise = float(rng.normal(0.0, 1.0))
        event = bool(f[SIGNAL] + noise > 2.0) if events is None else events[i]
        avail = available_utc(night)
        memory.add(night_record(night.isoformat(), f, event, max_ppb=90.0 if event else 3.0, n_hours=20,
                                source="test", available_utc=avail))
        out.append({"night": night.isoformat(), "features": f, "event": event, "available_utc": avail})
    return out


def noise_free_features(along: float) -> dict:
    f = synthetic_features(np.random.default_rng(1))
    f[SIGNAL] = along
    return f


def analog_result(p: float | None = 0.5, n: int = 10, n_events: int = 5, split: bool = False) -> AnalogResult:
    return AnalogResult(p=p, lo=0.2, hi=0.8, n=n, n_events=n_events, analog_ids=[], analog_nights=[], max_ppb_seen=None,
                        tags_used=[], query_text="", as_of_utc=datetime(2026, 6, 1, tzinfo=UTC), split=split, fallback_level=0)


def codes(flags) -> set[str]:
    return {f.code for f in flags}


# verified_nights

def test_verified_nights_excludes_a_night_verified_one_hour_after_as_of():
    mem = FakeMemory()
    nights = fill_memory(mem, date(2026, 1, 1), 10)
    as_of = nights[-1]["available_utc"] - timedelta(hours=1)
    df = mm.verified_nights(mem, as_of)
    assert len(df) == 9
    assert nights[-1]["night"] not in set(df.night)
    assert (df.available_utc <= as_of).all()


def test_verified_nights_includes_a_night_verified_exactly_at_as_of():
    mem = FakeMemory()
    nights = fill_memory(mem, date(2026, 1, 1), 5)
    df = mm.verified_nights(mem, nights[-1]["available_utc"])
    assert len(df) == 5


def test_verified_nights_is_newest_first_with_every_feature_column():
    mem = FakeMemory()
    nights = fill_memory(mem, date(2026, 1, 1), 12)
    df = mm.verified_nights(mem, nights[-1]["available_utc"] + timedelta(days=1))
    assert list(df.night) == [n["night"] for n in reversed(nights)]
    assert df.available_utc.is_monotonic_decreasing
    for name in FEATURES:
        assert name in df.columns
    newest = nights[-1]
    assert df.loc[0, SIGNAL] == pytest.approx(newest["features"][SIGNAL])
    assert df.loc[0, "event"] == float(newest["event"])


def test_verified_nights_skips_records_without_a_boolean_event_or_without_features():
    mem = FakeMemory()
    nights = fill_memory(mem, date(2026, 1, 1), 3)
    avail = nights[-1]["available_utc"]
    tags = ["station:pahala", "night:2026-01-09"]
    mem.add(MemoryRecord(content="verified night Pahala unknown outcome", source="test", confidence=0.9, tags=tags,
                         available_utc=avail, metadata={"event": None, "features": nights[0]["features"]}))
    mem.add(MemoryRecord(content="verified night Pahala string outcome", source="test", confidence=0.9, tags=tags,
                         available_utc=avail, metadata={"event": "yes", "features": nights[0]["features"]}))
    mem.add(MemoryRecord(content="verified night Pahala no features", source="test", confidence=0.9, tags=tags,
                         available_utc=avail, metadata={"event": True}))
    df = mm.verified_nights(mem, avail + timedelta(days=1))
    assert len(df) == 3


# fit_memory_model: refusals

def test_fit_returns_none_with_a_reason_when_memory_is_empty():
    pipe, note = mm.fit_memory_model(FakeMemory(), datetime(2026, 6, 1, tzinfo=UTC))
    assert pipe is None
    assert note["reason"]
    assert note["n_in_memory"] == 0


def test_fit_returns_none_with_a_reason_below_min_nights():
    mem = FakeMemory()
    n = mm.MIN_NIGHTS - 1
    nights = fill_memory(mem, date(2026, 1, 1), n, events=[i % 3 == 0 for i in range(n)])
    pipe, note = mm.fit_memory_model(mem, nights[-1]["available_utc"] + timedelta(days=1))
    assert pipe is None
    assert str(n) in note["reason"]


def test_fit_returns_none_with_a_reason_below_min_events():
    mem = FakeMemory()
    n = mm.MIN_NIGHTS + 10
    events = [i < mm.MIN_EVENTS - 1 for i in range(n)]
    nights = fill_memory(mem, date(2026, 1, 1), n, events=events)
    pipe, note = mm.fit_memory_model(mem, nights[-1]["available_utc"] + timedelta(days=1))
    assert pipe is None
    assert f"{mm.MIN_EVENTS - 1} events" in note["reason"]


def test_fit_succeeds_at_exactly_min_nights_and_min_events():
    mem = FakeMemory()
    n = mm.MIN_NIGHTS
    events = [i < mm.MIN_EVENTS for i in range(n)]
    nights = fill_memory(mem, date(2026, 1, 1), n, events=events)
    pipe, note = mm.fit_memory_model(mem, nights[-1]["available_utc"] + timedelta(days=1))
    assert pipe is not None
    assert note["n_train"] == n


# fit_memory_model: window and fallback

def test_fit_uses_the_window_and_learns_the_planted_signal():
    mem = FakeMemory()
    as_of = datetime(2026, 6, 30, tzinfo=UTC)
    nights = fill_memory(mem, date(2026, 4, 22), 60)  # last night 2026-06-20, all within 120 days of as_of
    assert sum(n["event"] for n in nights) >= mm.MIN_EVENTS
    pipe, note = mm.fit_memory_model(mem, as_of)
    assert pipe is not None
    assert str(mm.WINDOW_DAYS) in note["how"]
    assert "half-life" not in note["how"]
    assert note["n_train"] == 60
    p_high = mm.predict(pipe, noise_free_features(6.0))
    p_low = mm.predict(pipe, noise_free_features(-4.0))
    assert 0.0 <= p_low <= 1.0 and 0.0 <= p_high <= 1.0
    assert p_high > p_low


def test_fit_falls_back_to_the_whole_history_with_a_half_life_when_the_window_is_empty():
    mem = FakeMemory()
    as_of = datetime(2026, 10, 1, tzinfo=UTC)
    nights = fill_memory(mem, date(2026, 1, 1), 60)  # last night 2026-03-01, far older than 120 days
    assert sum(n["event"] for n in nights) >= mm.MIN_EVENTS
    assert nights[-1]["available_utc"] < as_of - timedelta(days=mm.WINDOW_DAYS)
    pipe, note = mm.fit_memory_model(mem, as_of)
    assert pipe is not None
    assert "half-life" in note["how"]
    assert str(mm.HALFLIFE_FALLBACK_DAYS) in note["how"]
    assert note["n_train"] == 60
    assert 0.0 <= mm.predict(pipe, noise_free_features(1.0)) <= 1.0


def test_fit_falls_back_when_the_window_has_too_few_nights_even_if_history_is_large():
    mem = FakeMemory()
    as_of = datetime(2026, 6, 30, tzinfo=UTC)
    fill_memory(mem, date(2025, 10, 1), 100)  # last night 2026-01-08, older than 120 days before June 30
    fill_memory(mem, date(2026, 6, 10), 10, events=[True, False] * 5)  # only 10 nights inside the window
    pipe, note = mm.fit_memory_model(mem, as_of)
    assert pipe is not None
    assert "half-life" in note["how"]
    assert note["n_train"] == 110


# as-of behaviour

def test_fit_as_of_uses_only_nights_verified_by_then():
    mem = FakeMemory()
    fill_memory(mem, date(2026, 1, 1), 90)  # nights through 2026-03-31
    as_of = datetime(2026, 2, 15, tzinfo=UTC)
    pipe, note = mm.fit_memory_model(mem, as_of)
    assert pipe is not None
    assert date.fromisoformat(note["newest_night"]) <= date(2026, 2, 14)
    assert note["newest_night"] == "2026-02-13"  # the 14th is verified at 22:00 UTC on the 15th, after as_of
    assert note["n_train"] == 44


def test_fit_does_not_change_when_later_nights_are_added_to_memory():
    early = FakeMemory()
    late = FakeMemory()
    fill_memory(early, date(2026, 1, 1), 60)
    fill_memory(late, date(2026, 1, 1), 90)  # same first 60 nights plus 30 later ones
    as_of = available_utc(date(2026, 2, 28))
    pipe_a, note_a = mm.fit_memory_model(early, as_of)
    pipe_b, note_b = mm.fit_memory_model(late, as_of)
    assert note_a["n_train"] == note_b["n_train"]
    f = noise_free_features(2.0)
    assert mm.predict(pipe_a, f) == pytest.approx(mm.predict(pipe_b, f))


# forecaster glue

def test_memory_model_guidance_is_unavailable_when_memory_is_empty():
    g, note = memory_model_guidance(FakeMemory(), noise_free_features(2.0), datetime(2026, 6, 1, tzinfo=UTC))
    assert g.id == "G2"
    assert g.stream == "memory_model"
    assert g.p is None
    assert "unavailable" in g.note
    assert "reason" in note


def test_memory_model_guidance_reports_p_and_training_nights_when_fitted():
    mem = FakeMemory()
    nights = fill_memory(mem, date(2026, 4, 22), 60)
    g, note = memory_model_guidance(mem, noise_free_features(5.0), datetime(2026, 6, 30, tzinfo=UTC))
    assert g.id == "G2" and g.stream == "memory_model"
    assert g.p is not None and 0.0 <= g.p <= 1.0
    assert "60 verified nights" in g.note
    assert nights[0]["night"] in g.note
    assert len(g.analog_ids) == min(50, 60)


def test_review_flags_raises_memory_model_unavailable_when_p_memory_is_none():
    flags = review_flags([], 0.4, analog_result(), [], p_memory=None)
    assert "memory_model_unavailable" in codes(flags)
    assert "model_memory_disagree" not in codes(flags)


def test_review_flags_raises_model_memory_disagree_when_gap_exceeds_threshold():
    flags = review_flags([], 0.1, analog_result(), [], p_memory=0.5, memory_ids=["a", "b"])
    assert "model_memory_disagree" in codes(flags)
    assert "memory_model_unavailable" not in codes(flags)
    flag = next(f for f in flags if f.code == "model_memory_disagree")
    assert flag.evidence_ids == ["a", "b"]


def test_review_flags_disagreement_works_in_both_directions():
    assert "model_memory_disagree" in codes(review_flags([], 0.9, analog_result(), [], p_memory=0.3))


def test_review_flags_raises_neither_memory_flag_when_the_gap_is_small_or_exactly_at_the_limit():
    small = review_flags([], 0.30, analog_result(), [], p_memory=0.40)
    assert not codes(small) & {"memory_model_unavailable", "model_memory_disagree"}
    at_limit = review_flags([], 0.0, analog_result(), [], p_memory=0.3)
    assert not codes(at_limit) & {"memory_model_unavailable", "model_memory_disagree"}


def test_review_flags_ignores_the_analog_rate_for_disagreement():
    flags = review_flags([], 0.05, analog_result(p=0.95, n=10, n_events=9), [], p_memory=0.1)
    assert "model_analog_disagree" not in codes(flags)
    assert "model_memory_disagree" not in codes(flags)


# analog_guidance shrinkage

def analog_setup():
    mem = FakeMemory()
    nights = fill_memory(mem, date(2026, 1, 1), 90)
    as_of = nights[-1]["available_utc"] + timedelta(days=1)
    return mem, noise_free_features(4.0), as_of


def test_analog_guidance_shrinks_the_rate_toward_the_prior():
    mem, f, as_of = analog_setup()
    g, a = analog_guidance(mem, f, as_of, prior=0.3)
    assert a.p is not None and a.n >= 5
    assert a.p != pytest.approx(0.3), "the test needs a raw rate that differs from the prior"
    expected = (a.n_events + 10 * 0.3) / (a.n + 10)
    assert g.p == pytest.approx(expected, abs=1e-4)
    assert min(a.p, 0.3) < g.p < max(a.p, 0.3)
    assert "0.30" in g.note
    assert "shrunk" in g.note


def test_analog_guidance_without_a_prior_reports_the_raw_rate():
    mem, f, as_of = analog_setup()
    g, a = analog_guidance(mem, f, as_of, prior=None)
    assert a.p is not None
    assert g.p == pytest.approx(a.p, abs=1e-4)
    assert a.p_shrunk is None
    assert "shrunk" not in g.note


def test_analog_guidance_with_no_analogs_has_no_p_and_says_so():
    g, a = analog_guidance(FakeMemory(), noise_free_features(4.0), datetime(2026, 6, 1, tzinfo=UTC), prior=0.3)
    assert a.n == 0
    assert g.id == "G4"
    assert "no analog nights" in g.note



def test_recent_rate_guidance_uses_the_last_30_nights_even_when_memory_holds_hundreds():
    # Regression: the stream used a relevance-ranked top 400, so with more nights in memory it was not the last 30.
    from vogdesk.desk.forecaster import recent_rate_guidance
    mem = FakeMemory()
    events = [False] * 470 + [True] * 30  # the 30 most recent nights are all events
    fill_memory(mem, date(2025, 1, 1), 500, events=events)
    as_of = datetime(2026, 6, 1, tzinfo=timezone.utc)
    g = recent_rate_guidance(mem, as_of)
    assert g.p == pytest.approx(1.0)
