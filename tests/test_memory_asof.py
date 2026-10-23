"""The memory must never return a record before its outcome was known (docs/PREREG.md)."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from vogdesk.memory import (
    AUTO_RESOLVE_CONFIDENCE_GAP,
    FEEDBACK_DELTA,
    FakeMemory,
    MemoryRecord,
    open_memory,
)

UTC = timezone.utc
TAGS = ["night:2026-01-05", "station:pahala", "regime:trade"]


def rec(date="2026-01-05", avail=None, event=True, conf=0.8, station="pahala", **kw) -> MemoryRecord:
    avail = avail or datetime(2026, 1, 6, 22, 0, tzinfo=UTC)  # 12:00 HST on D+1
    return MemoryRecord(
        content=kw.pop("content", f"night {date} trade wind pause at {station}"),
        source=f"verifier:{date}", confidence=conf, available_utc=avail,
        tags=kw.pop("tags", [f"night:{date}", f"station:{station}", "regime:trade"]),
        metadata={"event": event}, **kw,
    )


def test_record_hidden_until_outcome_known():
    mem = FakeMemory()
    rid = mem.add(rec())
    before = datetime(2026, 1, 6, 4, 0, tzinfo=UTC)  # 18:00 HST on D
    after = datetime(2026, 1, 6, 23, 0, tzinfo=UTC)
    assert mem.query("trade wind pause", TAGS, before) == []
    hits = mem.query("trade wind pause", TAGS, after)
    assert [h.record.id for h in hits] == [rid]
    assert hits[0].trace["matched_tags"] == sorted(TAGS)


def test_available_exactly_at_cutoff_is_visible():
    mem = FakeMemory()
    r = rec()
    mem.add(r)
    assert len(mem.query("", TAGS, r.available_utc)) == 1


def test_naive_datetimes_rejected():
    mem = FakeMemory()
    with pytest.raises(ValueError):
        mem.add(rec(avail=datetime(2026, 1, 6, 22, 0)))
    with pytest.raises(ValueError):
        mem.query("x", TAGS, datetime(2026, 1, 6, 22, 0))


def test_query_scoring_and_k():
    mem = FakeMemory()
    a = mem.add(rec(content="alpha beta gamma"))
    b = mem.add(rec(date="2026-01-04", tags=["station:pahala"], content="unrelated words"))
    mem.add(rec(date="2026-01-03", tags=["other"], content="nothing here"))
    now = datetime(2026, 2, 1, tzinfo=UTC)
    hits = mem.query("alpha beta", ["station:pahala", "regime:trade"], now)
    assert [h.record.id for h in hits] == [a, b]
    assert hits[0].score == 2 * 2 + 2
    assert hits[0].trace["token_overlap"] == 2
    assert len(mem.query("alpha beta", ["station:pahala"], now, k=1)) == 1


def test_feedback_deltas_clipping_and_append_only_ledger():
    mem = FakeMemory()
    rid = mem.add(rec(conf=0.5))
    sizes = [len(mem.ledger(rid))]
    e = mem.feedback(rid, "helpful")
    assert e.kind == "feedback" and e.delta == FEEDBACK_DELTA["helpful"]
    assert mem.query("", TAGS, datetime(2026, 2, 1, tzinfo=UTC))[0].record.confidence == pytest.approx(0.55)
    sizes.append(len(mem.ledger(rid)))
    mem.feedback(rid, "not_helpful")
    sizes.append(len(mem.ledger(rid)))
    assert mem.query("", TAGS, datetime(2026, 2, 1, tzinfo=UTC))[0].record.confidence == pytest.approx(0.52)
    mem.feedback(rid, "wrong")
    mem.feedback(rid, "wrong")
    mem.feedback(rid, "wrong")  # 0.32, 0.12, then clipped at 0.0
    assert mem.query("", TAGS, datetime(2026, 2, 1, tzinfo=UTC))[0].record.confidence == 0.0
    for _ in range(25):
        mem.feedback(rid, "helpful")
    assert mem.query("", TAGS, datetime(2026, 2, 1, tzinfo=UTC))[0].record.confidence == 1.0
    sizes.append(len(mem.ledger(rid)))
    assert sizes == sorted(sizes) and len(set(sizes)) == len(sizes)
    assert [e.kind for e in mem.ledger(rid)][:2] == ["add", "feedback"]
    with pytest.raises(KeyError):
        mem.feedback("missing", "helpful")


def test_record_without_provenance_or_bad_confidence_rejected():
    base = dict(content="x", available_utc=datetime(2026, 1, 6, tzinfo=UTC))
    with pytest.raises(ValidationError):
        MemoryRecord(confidence=0.5, **base)  # no source
    with pytest.raises(ValidationError):
        MemoryRecord(source="  ", confidence=0.5, **base)
    with pytest.raises(ValidationError):
        MemoryRecord(source="verifier:2026-01-05", confidence=1.5, **base)
    with pytest.raises(ValidationError):
        MemoryRecord(source="verifier:2026-01-05", confidence=-0.1, **base)


def test_conflict_auto_resolves_at_gap():
    mem = FakeMemory()
    win = mem.add(rec(event=True, conf=0.95))
    lose = mem.add(rec(event=False, conf=0.25))  # gap 0.70
    assert AUTO_RESOLVE_CONFIDENCE_GAP == 0.70
    conflicts = mem.detect_conflicts()
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.resolution == "auto_resolved" and c.winner == win and set(c.record_ids) == {win, lose}
    hits = mem.query("", TAGS, datetime(2026, 2, 1, tzinfo=UTC))
    assert [h.record.id for h in hits] == [win]
    assert [e.kind for e in mem.ledger(lose)] == ["add", "supersede", "conflict"]
    assert [e.kind for e in mem.ledger(win)] == ["add", "conflict"]
    assert mem.detect_conflicts() == []  # the loser is kept but no longer in play


def test_conflict_needs_review_below_gap():
    mem = FakeMemory()
    a = mem.add(rec(event=True, conf=0.9))
    b = mem.add(rec(event=False, conf=0.3))  # gap 0.60
    (c,) = mem.detect_conflicts()
    assert c.resolution == "needs_review" and c.winner is None
    assert len(mem.query("", TAGS, datetime(2026, 2, 1, tzinfo=UTC))) == 2
    assert [e.kind for e in mem.ledger(a)] == ["add"]
    assert [e.kind for e in mem.ledger(b)] == ["add"]


def test_no_conflict_across_nights_stations_or_same_event():
    mem = FakeMemory()
    mem.add(rec(date="2026-01-05", event=True))
    mem.add(rec(date="2026-01-06", event=False))
    mem.add(rec(date="2026-01-05", event=False, station="hvnp"))
    mem.add(rec(date="2026-01-05", event=True, conf=0.1))
    assert mem.detect_conflicts() == []


def test_json_round_trip(tmp_path):
    path = tmp_path / "mem.json"
    mem = FakeMemory(path)
    rid = mem.add(rec(conf=0.5))
    mem.feedback(rid, "helpful")
    mem.wm_ingest("case1", {"event_id": "e1", "x": 1})

    again = open_memory("fake", path)
    assert [e.kind for e in again.ledger(rid)] == ["add", "feedback"]
    assert again.wm_context("case1")["n"] == 1
    before = datetime(2026, 1, 6, 4, 0, tzinfo=UTC)
    after = datetime(2026, 1, 6, 23, 0, tzinfo=UTC)
    assert again.query("trade", TAGS, before) == []
    hits = again.query("trade", TAGS, after)
    assert [h.record.id for h in hits] == [rid]
    assert hits[0].record.confidence == pytest.approx(0.55)
    assert hits[0].record.available_utc == datetime(2026, 1, 6, 22, 0, tzinfo=UTC)


def test_working_memory_is_idempotent_on_event_id():
    mem = FakeMemory()
    mem.wm_ingest("c", {"event_id": "a", "v": 1})
    mem.wm_ingest("c", {"event_id": "a", "v": 2})
    mem.wm_ingest("c", {"v": 3})
    ctx = mem.wm_context("c")
    assert ctx["n"] == 2 and ctx["case_id"] == "c" and ctx["events"][0]["v"] == 1
    assert mem.wm_context("none") == {"case_id": "none", "events": [], "n": 0}


def test_previews_apply_nothing():
    mem = FakeMemory()
    mem.add(rec(event=True))
    mem.add(rec(date="2026-01-06", event=False, avail=datetime(2026, 3, 1, tzinfo=UTC)))
    dream = mem.dream_preview()
    assert dream["approved"] is False and "regime:trade: 2 records, 1 with an event" in dream["proposals"][0]
    sleep = mem.sleep_preview()
    assert sleep == {"would_consolidate": 1, "backup": "none", "applied": False}
