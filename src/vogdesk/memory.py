"""Memory interface for the forecast desk, with the information cutoff rule built in.

The desk remembers past nights in Meterless H-MEM. A night becomes memory only after its outcome
is known: its window closes at 12:00 HST the next day and the verification is written. That time
is the record's `available_utc`. A forecast issued at time T may retrieve only records with
available_utc <= T, and `query` enforces this for every tag and every text.

`Memory` is the fixed interface. `FakeMemory` runs in process with the same semantics, so the desk
and its backtests can be built before the Node sidecar exists. `HttpMemory` talks to the sidecar.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field, field_validator

FEEDBACK_DELTA = {"helpful": +0.05, "not_helpful": -0.03, "wrong": -0.20}
AUTO_RESOLVE_CONFIDENCE_GAP = 0.70
SHORT_TERM_DAYS = 30
DEFAULT_BASE_URL = "http://127.0.0.1:8790"

FeedbackKind = Literal["helpful", "not_helpful", "wrong"]


class MemoryUnavailable(RuntimeError):
    """The memory sidecar could not be reached."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


class MemoryRecord(BaseModel):
    id: str = Field(default_factory=_new_id)
    content: str
    source: str
    confidence: float
    tags: list[str] = Field(default_factory=list)
    available_utc: datetime  # when the fact became known, timezone-aware UTC
    created_utc: datetime = Field(default_factory=_now)
    metadata: dict = Field(default_factory=dict)
    superseded_by: str | None = None

    @field_validator("source")
    @classmethod
    def _source_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("source must be non-empty, a record needs provenance")
        return v

    @field_validator("confidence")
    @classmethod
    def _confidence_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return v


class Retrieved(BaseModel):
    record: MemoryRecord
    score: float
    trace: dict = Field(default_factory=dict)  # why it matched: matched tags, token overlap, tier


class LedgerEntry(BaseModel):
    id: str = Field(default_factory=_new_id)
    record_id: str
    ts_utc: datetime = Field(default_factory=_now)
    kind: Literal["add", "feedback", "supersede", "conflict"]
    delta: float | None = None
    note: str = ""


class Conflict(BaseModel):
    record_ids: list[str]
    field: str
    resolution: Literal["auto_resolved", "needs_review"]
    winner: str | None = None
    note: str = ""


class Memory(Protocol):
    def add(self, record: MemoryRecord) -> str: ...

    def query(self, text: str, tags: list[str], as_of_utc: datetime, k: int = 10) -> list[Retrieved]: ...

    def feedback(self, record_id: str, kind: FeedbackKind) -> LedgerEntry: ...

    def ledger(self, record_id: str) -> list[LedgerEntry]: ...

    def detect_conflicts(self) -> list[Conflict]: ...

    def dream_preview(self) -> dict: ...

    def sleep_preview(self) -> dict: ...

    def wm_ingest(self, case_id: str, event: dict) -> None: ...

    def wm_context(self, case_id: str) -> dict: ...


def _is_aware(ts: datetime) -> bool:
    return ts.tzinfo is not None and ts.utcoffset() is not None


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) > 2}


def _tag_value(tags: list[str], prefix: str) -> str | None:
    for t in tags:
        if t.startswith(prefix):
            return t
    return None


class FakeMemory:
    """In-process memory with the semantics of the sidecar, optionally saved to a JSON file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._records: dict[str, MemoryRecord] = {}
        self._ledger: dict[str, list[LedgerEntry]] = {}
        self._wm: dict[str, list[dict]] = {}
        if self.path is not None and self.path.exists():
            self._load()

    # persistence

    def _load(self) -> None:
        data = json.loads(self.path.read_text())
        self._records = {r["id"]: MemoryRecord.model_validate(r) for r in data.get("records", [])}
        self._ledger = {
            rid: [LedgerEntry.model_validate(e) for e in entries]
            for rid, entries in data.get("ledger", {}).items()
        }
        self._wm = data.get("wm", {})

    def _save(self) -> None:
        if self.path is None:
            return
        data = {
            "records": [r.model_dump(mode="json") for r in self._records.values()],
            "ledger": {rid: [e.model_dump(mode="json") for e in es] for rid, es in self._ledger.items()},
            "wm": self._wm,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.path)

    # ledger

    def _log(self, record_id: str, kind: str, delta: float | None = None, note: str = "") -> LedgerEntry:
        entry = LedgerEntry(record_id=record_id, kind=kind, delta=delta, note=note)
        self._ledger.setdefault(record_id, []).append(entry)
        return entry

    # protocol

    def add(self, record: MemoryRecord) -> str:
        if not _is_aware(record.available_utc):
            raise ValueError("available_utc must be timezone-aware UTC")
        if record.id in self._records:
            raise ValueError(f"record id already exists: {record.id}")
        self._records[record.id] = record
        self._log(record.id, "add", note=f"source={record.source}")
        self._save()
        return record.id

    def query(self, text: str, tags: list[str], as_of_utc: datetime, k: int = 10) -> list[Retrieved]:
        if not _is_aware(as_of_utc):
            raise ValueError("as_of_utc must be timezone-aware UTC")
        words = _tokens(text)
        wanted = set(tags)
        hits: list[Retrieved] = []
        for rec in self._records.values():
            if rec.available_utc > as_of_utc or rec.superseded_by is not None:
                continue
            matched = sorted(wanted & set(rec.tags))
            overlap = len(words & _tokens(rec.content))
            score = 2 * len(matched) + overlap
            if score <= 0:
                continue
            trace = {
                "matched_tags": matched,
                "token_overlap": overlap,
                "available_utc": rec.available_utc.isoformat(),
                "as_of_utc": as_of_utc.isoformat(),
            }
            hits.append(Retrieved(record=rec, score=float(score), trace=trace))
        hits.sort(key=lambda h: (h.score, h.record.confidence), reverse=True)
        return hits[: max(k, 0)]

    def feedback(self, record_id: str, kind: FeedbackKind) -> LedgerEntry:
        if record_id not in self._records:
            raise KeyError(record_id)
        if kind not in FEEDBACK_DELTA:
            raise ValueError(f"unknown feedback kind: {kind}")
        rec = self._records[record_id]
        delta = FEEDBACK_DELTA[kind]
        rec.confidence = min(1.0, max(0.0, rec.confidence + delta))
        entry = self._log(record_id, "feedback", delta=delta, note=kind)
        self._save()
        return entry

    def ledger(self, record_id: str) -> list[LedgerEntry]:
        if record_id not in self._records:
            raise KeyError(record_id)
        return list(self._ledger.get(record_id, []))

    def detect_conflicts(self) -> list[Conflict]:
        groups: dict[tuple[str, str], list[MemoryRecord]] = {}
        for rec in self._records.values():
            if rec.superseded_by is not None or not isinstance(rec.metadata.get("event"), bool):
                continue
            night = _tag_value(rec.tags, "night:")
            station = _tag_value(rec.tags, "station:")
            if night is None or station is None:
                continue
            groups.setdefault((night, station), []).append(rec)

        found: list[Conflict] = []
        changed = False
        for (night, station), recs in groups.items():
            yes = [r for r in recs if r.metadata["event"]]
            no = [r for r in recs if not r.metadata["event"]]
            for a in yes:
                for b in no:
                    if a.superseded_by is not None or b.superseded_by is not None:
                        continue  # settled earlier in this same pass
                    gap = abs(a.confidence - b.confidence)
                    label = f"{night} {station}"
                    if round(gap, 9) >= AUTO_RESOLVE_CONFIDENCE_GAP:
                        winner, loser = (a, b) if a.confidence > b.confidence else (b, a)
                        loser.superseded_by = winner.id
                        self._log(loser.id, "supersede", note=f"superseded by {winner.id}")
                        for r in (a, b):
                            self._log(r.id, "conflict", note=f"event disagrees for {label}, gap {gap:.2f}")
                        found.append(Conflict(
                            record_ids=[a.id, b.id], field="event", resolution="auto_resolved",
                            winner=winner.id, note=f"{label}, confidence gap {gap:.2f}",
                        ))
                        changed = True
                    else:
                        found.append(Conflict(
                            record_ids=[a.id, b.id], field="event", resolution="needs_review",
                            winner=None, note=f"{label}, confidence gap {gap:.2f} is below the limit",
                        ))
        if changed:
            self._save()
        return found

    def dream_preview(self) -> dict:
        stats: dict[str, list[bool]] = {}
        for rec in self._records.values():
            for t in rec.tags:
                if t.startswith("regime:"):
                    stats.setdefault(t, []).append(rec.metadata.get("event") is True)
        proposals = []
        for tag in sorted(stats):
            n, hits = len(stats[tag]), sum(stats[tag])
            proposals.append(f"{tag}: {n} records, {hits} with an event, event rate {hits / n:.2f}")
        return {"proposals": proposals, "approved": False}

    def sleep_preview(self) -> dict:
        n_short = 0
        if self._records:
            latest = max(r.available_utc for r in self._records.values())
            cutoff = latest - timedelta(days=SHORT_TERM_DAYS)
            n_short = sum(1 for r in self._records.values() if r.available_utc < cutoff)
        backup = f"{self.path}.bak" if self.path is not None else "none"
        return {"would_consolidate": n_short, "backup": backup, "applied": False}

    def wm_ingest(self, case_id: str, event: dict) -> None:
        events = self._wm.setdefault(case_id, [])
        event_id = event.get("event_id")
        if event_id is not None and any(e.get("event_id") == event_id for e in events):
            return
        events.append(dict(event))
        self._save()

    def wm_context(self, case_id: str) -> dict:
        events = list(self._wm.get(case_id, []))
        return {"case_id": case_id, "events": events, "n": len(events)}


class HttpMemory:
    """Client for the Node sidecar. Same interface as FakeMemory."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        self.client = httpx.Client(base_url=base_url, timeout=30.0)

    def close(self) -> None:
        self.client.close()

    def _call(self, method: str, url: str, payload: dict | None = None):
        try:
            resp = self.client.request(method, url, json=payload)
        except httpx.TransportError as exc:  # connection refused, timeout, DNS, reset
            raise MemoryUnavailable(f"memory sidecar unreachable at {self.client.base_url}: {exc}") from exc
        if resp.status_code == 404 and url.startswith(("/hmem/ledger/", "/hmem/feedback")):
            raise KeyError(url)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _items(body, key: str) -> list:
        return body.get(key, []) if isinstance(body, dict) else body

    def ping(self) -> bool:
        try:
            return self.client.get("/health").status_code == 200
        except httpx.TransportError:
            return False

    def add(self, record: MemoryRecord) -> str:
        body = self._call("POST", "/hmem/add", record.model_dump(mode="json"))
        return str(body["id"]) if isinstance(body, dict) and "id" in body else record.id

    def query(self, text: str, tags: list[str], as_of_utc: datetime, k: int = 10) -> list[Retrieved]:
        if not _is_aware(as_of_utc):
            raise ValueError("as_of_utc must be timezone-aware UTC")
        payload = {"text": text, "tags": tags, "as_of_utc": as_of_utc.isoformat(), "k": k}
        body = self._call("POST", "/hmem/query", payload)
        return [Retrieved.model_validate(x) for x in self._items(body, "results")]

    def feedback(self, record_id: str, kind: FeedbackKind) -> LedgerEntry:
        body = self._call("POST", "/hmem/feedback", {"record_id": record_id, "kind": kind})
        return LedgerEntry.model_validate(body)

    def ledger(self, record_id: str) -> list[LedgerEntry]:
        body = self._call("GET", f"/hmem/ledger/{record_id}")
        return [LedgerEntry.model_validate(x) for x in self._items(body, "entries")]

    def detect_conflicts(self) -> list[Conflict]:
        body = self._call("POST", "/hmem/conflicts/detect", {})
        return [Conflict.model_validate(x) for x in self._items(body, "conflicts")]

    def dream_preview(self) -> dict:
        return self._call("POST", "/hmem/dream/preview", {})

    def sleep_preview(self) -> dict:
        return self._call("POST", "/hmem/sleep/preview", {})

    def wm_ingest(self, case_id: str, event: dict) -> None:
        self._call("POST", "/wm/ingest", {"case_id": case_id, "event": event})

    def wm_context(self, case_id: str) -> dict:
        return self._call("GET", f"/wm/context/{case_id}")


def open_memory(kind: str = "fake", path: Path | None = None, base_url: str = DEFAULT_BASE_URL) -> Memory:
    if kind == "fake":
        return FakeMemory(path)
    if kind == "http":
        return HttpMemory(base_url)
    raise ValueError(f"unknown memory kind: {kind}")


def _smoke() -> None:
    utc = timezone.utc
    with tempfile.TemporaryDirectory() as tmp:
        mem = FakeMemory(Path(tmp) / "memory.json")

        def night(date: str, avail: datetime, event: bool, conf: float, station: str = "pahala") -> MemoryRecord:
            return MemoryRecord(
                content=f"night {date} {station} trade wind pause, event {event}",
                source=f"verifier:{date}", confidence=conf, available_utc=avail,
                tags=[f"night:{date}", f"station:{station}", "regime:trade"],
                metadata={"event": event, "max_ppb": 12.0 if event else 1.0},
            )

        first = mem.add(night("2026-01-05", datetime(2026, 1, 6, 22, 0, tzinfo=utc), True, 0.9))
        mem.add(night("2026-01-06", datetime(2026, 1, 7, 22, 0, tzinfo=utc), False, 0.8))

        as_of = datetime(2026, 1, 7, 4, 0, tzinfo=utc)
        print(f"query as of {as_of.isoformat()}, between the two nights")
        for hit in mem.query("trade wind pause", ["station:pahala", "regime:trade"], as_of):
            print(f"  night={hit.record.tags[0]} score={hit.score} trace={hit.trace}")

        entry = mem.feedback(first, "helpful")
        print(f"feedback: {entry.note} delta={entry.delta}")
        for e in mem.ledger(first):
            print(f"  ledger {e.kind} delta={e.delta} note={e.note}")

        avail = datetime(2026, 1, 8, 22, 0, tzinfo=utc)
        mem.add(night("2026-01-07", avail, True, 0.95))
        mem.add(night("2026-01-07", avail, False, 0.20))
        for c in mem.detect_conflicts():
            print(f"conflict: {c.resolution} field={c.field} winner={c.winner} note={c.note}")

        print(f"dream preview: {mem.dream_preview()}")


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "smoke":
        _smoke()
    else:
        print("usage: python -m vogdesk.memory smoke")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
