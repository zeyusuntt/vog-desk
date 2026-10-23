# memory-service

Node sidecar that runs the Meterless **H-MEM** and **World Model** reference engines for the desk. The engines under `engines/` are copied unchanged from github.com/meterless/Meterless (Apache-2.0; commit in `engines/UPSTREAM.txt`) and are declared as a pre-existing component. `server.ts` is ours.

What the service adds, and why: the engines store and rank memories, but they do not know when a fact became public. A forecast issued at 18:00 HST may only recall nights whose outcome was already verified. So every record sent to `/hmem/add` carries `available_utc`, and `/hmem/query` takes `as_of_utc` and scores only records with `available_utc <= as_of_utc`, using the engine's own `retrieval.scoreOne`. Reads are logged to the engine's trust ledger with the as-of time, so the audit trail shows what the desk could see.

## Run

```bash
cd memory-service && npm install
npm start                                   # http://127.0.0.1:8790, state under data/default/
MEMORY_ROOT=data/backtest-2026-01 npm start  # one root per backtest run
```

Conformance of the vendored engines (scorecards in `docs/evidence/`):

```bash
npm run conformance:hmem
npm run conformance:world-model
```

## Routes

| Route | Engine call | Notes |
|---|---|---|
| `GET /health` | | record and ledger counts, memory root |
| `POST /hmem/add` | `hmem.add` | body: `content`, `source` (required), `confidence` (0 to 1), `tags`, `available_utc` (tz-aware), `metadata` |
| `POST /hmem/query` | `retrieval.scoreOne` per visible record | body: `text`, `tags` (all must be present), `as_of_utc`, `k`; returns `results[{record, score, trace}]` |
| `POST /hmem/feedback` | `hmem.feedback` | `kind` helpful (+0.05), not_helpful (−0.03), wrong (−0.20); returns the ledger entry |
| `GET /hmem/ledger/:id` | `ledger.history` | mapped to add, feedback, supersede, conflict; `engine_entries` counts all 17 action types |
| `POST /hmem/conflicts/detect` | `conflicts.scan` + `autoResolve` | 0.70 gate; losers get `supersededBy`, nothing is deleted |
| `POST /hmem/dream/preview` | `dreaming.dream` | proposals only, nothing approved |
| `POST /hmem/sleep/preview` | `sleep.preview` | preview only |
| `POST /wm/ingest` | `upsertEntity` + `assertFact` | one `case` entity per case id; idempotent on `event.event_id` |
| `GET /wm/context/:caseId` | `view("facts").about` | the case's events in order |

The Python client is `src/vogdesk/memory.py` (`HttpMemory`); `FakeMemory` there has the same semantics for tests without Node.
