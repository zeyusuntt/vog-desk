"""The Node sidecar (memory-service/server.ts) obeys the same as-of rule as FakeMemory.

Starts the sidecar on a free port with a temporary MEMORY_ROOT. Skipped when Node or the service's
dependencies are not installed (`cd memory-service && npm install`).
"""
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vogdesk.memory import HttpMemory, MemoryRecord

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "memory-service"
U = timezone.utc


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def sidecar(tmp_path_factory):
    if shutil.which("npx") is None or not (SERVICE / "node_modules" / "tsx").exists():
        pytest.skip("memory-service dependencies not installed")
    port = _free_port()
    root = tmp_path_factory.mktemp("memory-root")
    # start_new_session so the whole process group (npx -> node) can be killed; killing npx alone orphans node.
    proc = subprocess.Popen(["npx", "tsx", "server.ts"], cwd=SERVICE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True,
                            env={"PATH": __import__("os").environ["PATH"], "HOME": __import__("os").environ.get("HOME", ""),
                                 "MEMORY_ROOT": str(root), "PORT": str(port)})
    client = HttpMemory(f"http://127.0.0.1:{port}")
    for _ in range(60):
        if client.ping():
            break
        time.sleep(0.5)
    else:
        _stop(proc)
        pytest.fail("memory-service did not start:\n" + proc.stdout.read().decode(errors="replace")[-2000:])
    yield client
    _stop(proc)


def _stop(proc: subprocess.Popen) -> None:
    import os, signal
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


def _night(day: str, event: bool, available: datetime, confidence: float = 0.8, source: str | None = None) -> MemoryRecord:
    return MemoryRecord(
        content=f"Night of {day} at Pahala: trade winds toward Pahala during a pause, {'event' if event else 'quiet'}.",
        source=source or f"verifier:{day}", confidence=confidence,
        tags=[f"night:{day}", "station:pahala", "regime:trade", "episode:pause", f"outcome:{'event' if event else 'quiet'}"],
        available_utc=available, metadata={"event": event, "max_ppb": 84 if event else 12})


def test_query_respects_available_utc(sidecar):
    # Night 2026-01-05 is verified at 12:00 HST on 06 Jan (22:00 UTC); night 2026-01-06 a day later.
    id1 = sidecar.add(_night("2026-01-05", True, datetime(2026, 1, 6, 22, 0, tzinfo=U)))
    id2 = sidecar.add(_night("2026-01-06", False, datetime(2026, 1, 7, 22, 0, tzinfo=U)))
    tags = ["regime:trade", "station:pahala"]
    # 18:00 HST on 05 Jan (04:00 UTC 06 Jan): neither night is verified yet.
    assert sidecar.query("trade pause night", tags, datetime(2026, 1, 6, 4, 0, tzinfo=U)) == []
    # 18:00 HST on 06 Jan: only the first night is known.
    got = sidecar.query("trade pause night", tags, datetime(2026, 1, 7, 4, 0, tzinfo=U))
    assert [r.record.id for r in got] == [id1]
    assert got[0].trace["matched_tags"] == tags and got[0].record.metadata["event"] is True
    # Exactly at the availability instant the record is visible; one second before it is not.
    assert [r.record.id for r in sidecar.query("x", tags, datetime(2026, 1, 7, 22, 0, tzinfo=U))] == [id2, id1] or \
           {r.record.id for r in sidecar.query("x", tags, datetime(2026, 1, 7, 22, 0, tzinfo=U))} == {id1, id2}
    assert [r.record.id for r in sidecar.query("x", tags, datetime(2026, 1, 7, 21, 59, 59, tzinfo=U))] == [id1]


def test_feedback_and_ledger_go_through_the_engine(sidecar):
    rid = sidecar.add(_night("2026-02-01", True, datetime(2026, 2, 2, 22, 0, tzinfo=U)))
    before = sidecar.query("x", ["night:2026-02-01"], datetime(2026, 3, 1, tzinfo=U))[0].record.confidence
    entry = sidecar.feedback(rid, "helpful")
    assert entry.kind == "feedback" and entry.delta == pytest.approx(0.05)
    after = sidecar.query("x", ["night:2026-02-01"], datetime(2026, 3, 1, tzinfo=U))[0].record.confidence
    assert after == pytest.approx(before + 0.05)
    kinds = [e.kind for e in sidecar.ledger(rid)]
    assert kinds[0] == "add" and "feedback" in kinds
    with pytest.raises(KeyError):
        sidecar.ledger("mem-does-not-exist")


def test_same_night_opposite_outcome_is_a_conflict_and_loser_is_hidden(sidecar):
    strong = sidecar.add(_night("2026-03-01", True, datetime(2026, 3, 2, 22, 0, tzinfo=U), confidence=0.95))
    weak = sidecar.add(_night("2026-03-01", False, datetime(2026, 3, 3, 0, 0, tzinfo=U), confidence=0.05, source="reanalysis:draft"))
    conflicts = [c for c in sidecar.detect_conflicts() if set(c.record_ids) == {strong, weak}]
    assert conflicts, "the night-level contradiction must be reported"
    c = conflicts[0]
    assert c.resolution in ("auto_resolved", "needs_review")
    visible = {r.record.id for r in sidecar.query("x", ["night:2026-03-01"], datetime(2026, 4, 1, tzinfo=U))}
    if c.resolution == "auto_resolved":
        assert c.winner == strong and visible == {strong}
    else:
        assert visible == {strong, weak}


def test_world_model_case_context_is_idempotent(sidecar):
    ev = {"event_id": "e1", "kind": "evidence", "agent": "EvidenceAgent", "ts": "2026-01-06T04:01:00Z", "n_items": 3}
    sidecar.wm_ingest("case-test", ev)
    sidecar.wm_ingest("case-test", ev)
    sidecar.wm_ingest("case-test", {"event_id": "e2", "kind": "decision", "agent": "DutyLead", "ts": "2026-01-06T04:03:00Z", "p": 0.31})
    ctx = sidecar.wm_context("case-test")
    assert ctx["n"] == 2 and [e["predicate"] for e in ctx["events"]] == ["evidence", "decision"]
    assert sidecar.wm_context("case-none")["n"] == 0
