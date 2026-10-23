/**
 * Memory sidecar for the vog desk.
 *
 * Runs the Meterless H-MEM and World Model reference implementations unchanged (engines/) and
 * exposes them over HTTP for the Python desk (src/vogdesk/memory.py, HttpMemory). The one thing
 * the engines do not know about is the forecast's information cutoff: a night may only be
 * remembered once its outcome was known. This service keeps that rule. Every record carries
 * `available_utc`; a query with `as_of_utc` scores only records with available_utc <= as_of_utc,
 * using the engine's own hybrid ranking (retrieval.scoreOne) so the scoring stays the engine's.
 *
 * Storage: MEMORY_ROOT (default ./data/default). Backtests use one root per run and load nights in
 * order. Nothing here calls a model or the network.
 *
 *   MEMORY_ROOT=data/backtest-1 PORT=8790 npm start
 */
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { HMEM } from "./engines/hmem/reference/src/index.ts";
import type { LedgerEntry, MemoryRecord } from "./engines/hmem/reference/src/index.ts";
import { WorldModel } from "./engines/world-model/reference/src/index.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(process.env.MEMORY_ROOT ?? path.join(HERE, "data", "default"));
const PORT = Number(process.env.PORT ?? 8790);
const ACTOR = "vog-desk";

fs.mkdirSync(path.join(ROOT, "hmem"), { recursive: true });
fs.mkdirSync(path.join(ROOT, "wm"), { recursive: true });

// --- engines, unmodified ---------------------------------------------------------------------
const hmem = new HMEM({ persistDir: path.join(ROOT, "hmem"), actor: ACTOR });
const wm = new WorldModel({ namespace: "vogdesk", storage: "local", directory: path.join(ROOT, "wm") });

// --- as-of index (ours): what the engine does not store --------------------------------------
type Meta = { available_utc: string; created_utc: string; metadata: Record<string, unknown>; client_id?: string };
type Index = { records: Record<string, Meta>; wm_events: Record<string, string[]> };
const INDEX_FILE = path.join(ROOT, "asof.json");
const index: Index = fs.existsSync(INDEX_FILE)
  ? JSON.parse(fs.readFileSync(INDEX_FILE, "utf-8"))
  : { records: {}, wm_events: {} };
const saveIndex = () => fs.writeFileSync(INDEX_FILE, JSON.stringify(index, null, 1) + "\n");

// --- helpers -----------------------------------------------------------------------------------
class HttpError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

function parseAware(value: unknown, field: string): Date {
  if (typeof value !== "string" || !/(Z|[+-]\d\d:?\d\d)$/.test(value)) {
    throw new HttpError(400, `${field} must be an ISO timestamp with a timezone`);
  }
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) throw new HttpError(400, `${field} is not a valid timestamp`);
  return d;
}

const KIND_MAP: Record<string, "add" | "feedback" | "supersede" | "conflict" | undefined> = {
  create: "add", feedback: "feedback", conflict_detected: "conflict", conflict_resolved: "conflict",
  update: "supersede", merge: "supersede",
};

function toPyLedger(e: LedgerEntry, n: number) {
  const kind = KIND_MAP[e.action];
  if (!kind) return null;
  const delta = e.action === "feedback" ? (e.details?.delta as number | undefined) ?? null : null;
  return {
    id: `${e.memoryId}:${n}`, record_id: e.memoryId, ts_utc: new Date(e.timestamp).toISOString(), kind, delta,
    note: `engine:${e.action}${e.details ? " " + JSON.stringify(e.details).slice(0, 200) : ""}`,
  };
}

function toPyRecord(m: MemoryRecord) {
  const meta = index.records[m.id];
  return {
    id: m.id, content: m.content, source: m.source, confidence: m.confidence, tags: m.tags,
    available_utc: meta?.available_utc ?? null, created_utc: meta?.created_utc ?? new Date(m.timestamp).toISOString(),
    metadata: { ...(meta?.metadata ?? {}), layer: m.layer, domain: m.domain, type: m.type },
    superseded_by: m.supersededBy ?? null,
  };
}

function readBody(req: http.IncomingMessage): Promise<any> {
  return new Promise((resolve, reject) => {
    let data = "";
    req.on("data", (c) => { data += c; if (data.length > 2_000_000) reject(new HttpError(413, "body too large")); });
    req.on("end", () => { try { resolve(data ? JSON.parse(data) : {}); } catch { reject(new HttpError(400, "invalid JSON")); } });
    req.on("error", reject);
  });
}

// --- H-MEM routes -----------------------------------------------------------------------------
function hmemAdd(body: any) {
  if (typeof body.content !== "string" || !body.content.trim()) throw new HttpError(400, "content required");
  if (typeof body.source !== "string" || !body.source.trim()) throw new HttpError(400, "source required: a record needs provenance");
  const confidence = Number(body.confidence);
  if (!(confidence >= 0 && confidence <= 1)) throw new HttpError(400, "confidence must be within [0, 1]");
  const available = parseAware(body.available_utc, "available_utc");
  const created = body.created_utc ? parseAware(body.created_utc, "created_utc") : new Date();
  const tags: string[] = Array.isArray(body.tags) ? body.tags.map(String) : [];
  const rec = hmem.add({
    content: body.content, source: body.source, confidence, tags: [...tags, `available:${available.toISOString()}`],
    type: "factual", layer: body.layer ?? "working",
  });
  index.records[rec.id] = {
    available_utc: available.toISOString(), created_utc: created.toISOString(),
    metadata: body.metadata ?? {}, client_id: body.id,
  };
  saveIndex();
  return { id: rec.id, record: toPyRecord(rec) };
}

function hmemQuery(body: any) {
  const asOf = parseAware(body.as_of_utc, "as_of_utc");
  const text = String(body.text ?? "");
  const tags: string[] = Array.isArray(body.tags) ? body.tags.map(String) : [];
  const k = Math.max(1, Math.min(100, Number(body.k ?? 10)));
  const queryText = [text, ...tags].join(" ").trim() || "night";
  const scored: { m: MemoryRecord; score: number; matched: string[] }[] = [];
  for (const m of hmem.store.all()) {
    const meta = index.records[m.id];
    if (!meta) continue;                                   // no availability known: never returned
    if (new Date(meta.available_utc) > asOf) continue;      // the as-of rule
    if (m.supersededBy) continue;
    const matched = tags.filter((t) => m.tags.includes(t));
    if (tags.length && matched.length < tags.length) continue;
    scored.push({ m, score: hmem.retrieval.scoreOne(m, queryText), matched });
  }
  scored.sort((a, b) => b.score - a.score || b.m.confidence - a.m.confidence || a.m.id.localeCompare(b.m.id));
  const top = scored.slice(0, k);
  for (const { m } of top) {
    hmem.store.touch(m.id);
    hmem.ledger.log(m.id, "read", ACTOR, { details: { query: queryText, as_of_utc: asOf.toISOString() } });
  }
  return {
    results: top.map(({ m, score, matched }) => ({
      record: toPyRecord(m), score,
      trace: {
        engine: "hmem-reference hybrid ranking", engine_score: Number(score.toFixed(4)), matched_tags: matched,
        available_utc: index.records[m.id].available_utc, as_of_utc: asOf.toISOString(), layer: m.layer,
      },
    })),
    trace: { candidates_visible: scored.length, as_of_utc: asOf.toISOString(), query: queryText },
  };
}

function hmemFeedback(body: any) {
  const id = String(body.record_id ?? "");
  if (!hmem.store.get(id)) throw new HttpError(404, `unknown record ${id}`);
  const kind = body.kind;
  if (!["helpful", "not_helpful", "wrong"].includes(kind)) throw new HttpError(400, "kind must be helpful, not_helpful or wrong");
  hmem.feedback(id, kind);
  const history = hmem.ledger.history(id);
  const last = history[history.length - 1];
  const entry = toPyLedger(last, history.length - 1) ?? {
    id: `${id}:${history.length - 1}`, record_id: id, ts_utc: new Date(last.timestamp).toISOString(),
    kind: "feedback", delta: null, note: `engine:${last.action}`,
  };
  return entry;
}

function hmemLedger(id: string) {
  if (!hmem.store.get(id)) throw new HttpError(404, `unknown record ${id}`);
  const entries = hmem.ledger.history(id).map(toPyLedger).filter(Boolean);
  return { entries, engine_entries: hmem.ledger.history(id).length };
}

// The engine's scan finds conflicts by text heuristics. The desk's own contradiction, two records for
// the same night and station with opposite outcomes, is checked here and handed to the engine's
// autoResolve so the 0.70 gate, the supersededBy edge and the ledger entries are the engine's.
function nightConflicts() {
  const byKey = new Map<string, MemoryRecord[]>();
  for (const m of hmem.store.all()) {
    if (m.supersededBy) continue;
    const night = m.tags.find((t) => t.startsWith("night:"));
    const station = m.tags.find((t) => t.startsWith("station:"));
    if (!night || !station) continue;
    const key = `${night}|${station}`;
    byKey.set(key, [...(byKey.get(key) ?? []), m]);
  }
  const out: { id: string; memoryA: string; memoryB: string; reason: string; confidence: number; resolved: boolean }[] = [];
  let n = 0;
  for (const [key, group] of byKey) {
    for (let i = 0; i < group.length; i++) {
      for (let j = i + 1; j < group.length; j++) {
        const ea = index.records[group[i].id]?.metadata?.event;
        const eb = index.records[group[j].id]?.metadata?.event;
        if (typeof ea !== "boolean" || typeof eb !== "boolean" || ea === eb) continue;
        const c = { id: `night-conflict-${++n}-${Date.now()}`, memoryA: group[i].id, memoryB: group[j].id,
                    reason: `opposite outcome for ${key.replace("|", " ")}`, confidence: 0.9, resolved: false };
        hmem.ledger.log(c.memoryA, "conflict_detected", ACTOR, { details: { with: c.memoryB, reason: c.reason, confidence: c.confidence } });
        out.push(c);
      }
    }
  }
  return out;
}

function hmemConflicts() {
  const found = [...hmem.conflicts.scan(), ...nightConflicts()];
  const out = found.map((c) => {
    const outcome = hmem.conflicts.autoResolve(c);
    const a = hmem.store.get(c.memoryA); const b = hmem.store.get(c.memoryB);
    const winner = outcome === "resolved" ? (a?.supersededBy ? c.memoryB : c.memoryA) : null;
    return {
      record_ids: [c.memoryA, c.memoryB], field: c.reason, resolution: outcome === "resolved" ? "auto_resolved" : "needs_review",
      winner, note: `engine confidence ${c.confidence.toFixed(2)}; gate ${0.7}`,
    };
  });
  return { conflicts: out, queued: hmem.conflicts.queue().length };
}

function hmemDream() {
  const proposals = hmem.dreaming.dream();
  return { proposals: proposals.map((p) => `${p.type}: ${p.content}`), approved: false, pending: hmem.dreaming.pending().length, raw: proposals };
}

function hmemSleep() {
  const p = hmem.sleep.preview();
  return { would_consolidate: p.toConsolidate.length, would_archive: p.toArchive.length, would_synthesize: p.toSynthesize.length,
           applied: false, backup: "preview only", preview: p };
}

// --- World Model routes ----------------------------------------------------------------------
async function wmIngest(body: any) {
  const caseId = String(body.case_id ?? "");
  if (!caseId) throw new HttpError(400, "case_id required");
  const event = body.event ?? {};
  const eventId = String(event.event_id ?? event.id ?? `${caseId}:${JSON.stringify(event).length}:${event.kind ?? "event"}:${event.step ?? ""}`);
  const seen = (index.wm_events[caseId] ??= []);
  if (seen.includes(eventId)) return { ok: true, deduplicated: true, event_id: eventId };
  const at = event.ts ?? event.at ?? new Date().toISOString();
  const source = { kind: "agent", by: String(event.agent ?? "desk"), at };
  const entity = await wm.upsertEntity({ type: "case", externalKey: { system: "vogdesk", id: caseId }, attrs: { case_id: caseId }, source });
  await wm.assertFact({ about: entity.id, predicate: String(event.kind ?? "event"), value: { ...event, event_id: eventId },
                        confidence: typeof event.confidence === "number" ? event.confidence : 1, source });
  seen.push(eventId);
  saveIndex();
  return { ok: true, deduplicated: false, event_id: eventId, entity_id: entity.id };
}

function wmContext(caseId: string) {
  const entity = wm.view("graph").query({ type: "case", where: (e) => e.attrs?.case_id === caseId })[0];
  if (!entity) return { case_id: caseId, events: [], n: 0 };
  const facts = wm.view("facts").about(entity.id);
  return { case_id: caseId, entity_id: entity.id, n: facts.length,
           events: facts.map((f) => ({ predicate: f.predicate, asserted_at: f.assertedAt, superseded: Boolean(f.supersededBy), ...(f.value as object) })),
           audit_entries: wm.audit().length };
}

// --- server ------------------------------------------------------------------------------------
const server = http.createServer(async (req, res) => {
  const send = (status: number, body: unknown) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(body));
  };
  try {
    const url = new URL(req.url ?? "/", "http://localhost");
    const p = url.pathname;
    if (req.method === "GET" && p === "/health") {
      return send(200, { ok: true, engines: { hmem: "meterless reference", world_model: "meterless reference" },
                         records: hmem.store.all().length, ledger_entries: hmem.ledger.all().length, memory_root: ROOT });
    }
    if (req.method === "GET" && p.startsWith("/hmem/ledger/")) return send(200, hmemLedger(decodeURIComponent(p.slice("/hmem/ledger/".length))));
    if (req.method === "GET" && p.startsWith("/wm/context/")) return send(200, wmContext(decodeURIComponent(p.slice("/wm/context/".length))));
    if (req.method !== "POST") throw new HttpError(404, "not found");
    const body = await readBody(req);
    switch (p) {
      case "/hmem/add": return send(200, hmemAdd(body));
      case "/hmem/query": return send(200, hmemQuery(body));
      case "/hmem/feedback": return send(200, hmemFeedback(body));
      case "/hmem/conflicts/detect": return send(200, hmemConflicts());
      case "/hmem/dream/preview": return send(200, hmemDream());
      case "/hmem/sleep/preview": return send(200, hmemSleep());
      case "/wm/ingest": return send(200, await wmIngest(body));
      default: throw new HttpError(404, "not found");
    }
  } catch (e: any) {
    const status = e instanceof HttpError ? e.status : 500;
    send(status, { error: String(e?.message ?? e) });
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`memory-service listening on http://127.0.0.1:${PORT}  MEMORY_ROOT=${ROOT}  records=${hmem.store.all().length}`);
});
