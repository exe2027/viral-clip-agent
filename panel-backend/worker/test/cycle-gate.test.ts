// Tests de la puerta del ciclo "cuando toca" (A3, 2026-11-20).
// Contra SQLite REAL (bun:sqlite, mismo motor que D1), no contra el
// mock FakeD1: así se verifican el next_run_at, el fail-open y la
// salida temprana con cero escrituras de verdad. Sin red (fetch stub
// que rechaza), sin D1 de producción (la base es :memory: y se crea
// en cada test).
import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import { Database } from "bun:sqlite";
import {
  cycleGate,
  idleCycleGate,
  isDailyDispatchDue,
  isLiveDispatchDue,
  isWatchdogDispatchDue,
  nextDueJobAt,
  pendingWriteJournal,
  CYCLE_REVIEW_INTERVAL_MS,
} from "../src/cycle-gate";
import { runWorkerCycle } from "../src/jobs";
import { clearBudgetCounterCache } from "../src/budget";
import type { D1Database, Env } from "../src/cloudflare";

// --- Shim mínimo D1Database sobre bun:sqlite (como queue-order.test.ts) ---
class SqliteStmt {
  constructor(
    private db: Database,
    private sql: string,
    private params: unknown[] = [],
  ) {}
  bind(...values: unknown[]): SqliteStmt {
    return new SqliteStmt(this.db, this.sql, values);
  }
  async first<T = Record<string, unknown>>(): Promise<T | null> {
    const row = this.db.query(this.sql).get(...(this.params as []));
    return (row as T | undefined) ?? null;
  }
  async all<T = Record<string, unknown>>(): Promise<{ results: T[]; success: boolean; meta: Record<string, unknown> }> {
    return { results: this.db.query(this.sql).all(...(this.params as [])) as T[], success: true, meta: {} };
  }
  async run(): Promise<{ results: never[]; success: boolean; meta: { changes?: number; last_row_id?: number } }> {
    const info = this.db.query(this.sql).run(...(this.params as []));
    return { results: [], success: true, meta: { changes: info.changes, last_row_id: Number(info.lastInsertRowid) } };
  }
}

// --- R2 falso (solo get/put/head: lo que usan wal.ts y el ciclo) ---
class FakeR2 {
  store = new Map<string, string>();
  async get(key: string): Promise<{ arrayBuffer(): Promise<ArrayBuffer> } | null> {
    const text = this.store.get(key);
    if (text === undefined) return null;
    return { arrayBuffer: async () => new TextEncoder().encode(text).buffer as ArrayBuffer };
  }
  async put(key: string, body: string | Uint8Array): Promise<void> {
    this.store.set(key, typeof body === "string" ? body : new TextDecoder().decode(body));
  }
  async head(key: string): Promise<{ size: number } | null> {
    const text = this.store.get(key);
    return text === undefined ? null : { size: text.length };
  }
}

const TABLES = [
  "worker_state",
  "jobs",
  "settings",
  "brain_tokens",
  "worker_phases",
  "worker_events",
  "clip_events",
  "api_accounts",
  "clip_stats",
  "clips",
] as const;

function makeDbPair(): { d1: D1Database; raw: Database } {
  const raw = new Database(":memory:");
  raw.exec(
    "CREATE TABLE worker_state (tenant_id TEXT PRIMARY KEY, running INTEGER NOT NULL DEFAULT 0, " +
      "started_at INTEGER, last_note TEXT NOT NULL DEFAULT '', updated_at INTEGER NOT NULL, " +
      "last_live_dispatch_at INTEGER, last_cycle_review_at INTEGER)",
  );
  raw.exec(
    "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, " +
      "kind TEXT NOT NULL DEFAULT 'publish', clip_id INTEGER, platform TEXT, api_account_id INTEGER, " +
      "state TEXT NOT NULL DEFAULT 'scheduled', next_run_at INTEGER, created_at INTEGER NOT NULL, " +
      "updated_at INTEGER NOT NULL DEFAULT 0, run_token TEXT, claimed_by TEXT, resume_data TEXT, " +
      "posted_day TEXT, platform_id TEXT, evidence_url TEXT, last_error TEXT, retryable INTEGER, " +
      "attempts INTEGER NOT NULL DEFAULT 0, slot_day TEXT, language TEXT, title TEXT, caption TEXT, " +
      "r2_key TEXT, idempotency_key TEXT, manual INTEGER NOT NULL DEFAULT 0, deleted_at INTEGER, " +
      "wal_reconciled INTEGER NOT NULL DEFAULT 0)",
  );
  raw.exec("CREATE TABLE settings (tenant_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at INTEGER NOT NULL)");
  raw.exec(
    "CREATE TABLE brain_tokens (token_hash TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, " +
      "expires_at INTEGER NOT NULL, used_at INTEGER, created_at INTEGER NOT NULL)",
  );
  raw.exec("CREATE TABLE worker_phases (tenant_id TEXT PRIMARY KEY, phase TEXT NOT NULL, updated_at INTEGER NOT NULL)");
  raw.exec(
    "CREATE TABLE worker_events (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, " +
      "kind TEXT NOT NULL, message TEXT NOT NULL, at INTEGER NOT NULL)",
  );
  raw.exec(
    "CREATE TABLE clip_events (id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER, tenant_id TEXT, " +
      "step TEXT NOT NULL, message TEXT NOT NULL, at INTEGER NOT NULL)",
  );
  raw.exec(
    "CREATE TABLE api_accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, " +
      "platform TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'es', first_published_at INTEGER, " +
      "published_count INTEGER NOT NULL DEFAULT 0, strikes INTEGER NOT NULL DEFAULT 0, " +
      "maturity TEXT NOT NULL DEFAULT 'nueva', maturity_checked_day TEXT)",
  );
  raw.exec(
    "CREATE TABLE clip_stats (id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER NOT NULL, " +
      "tenant_id TEXT NOT NULL, platform TEXT NOT NULL, recorded_at INTEGER NOT NULL)",
  );
  raw.exec(
    "CREATE TABLE clips (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, " +
      "title TEXT NOT NULL DEFAULT '', score INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'en_carpeta', " +
      "archived INTEGER NOT NULL DEFAULT 0, origin TEXT NOT NULL DEFAULT 'vod', " +
      "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL DEFAULT 0)",
  );
  const d1 = { prepare: (sql: string) => new SqliteStmt(raw, sql) } as unknown as D1Database;
  return { d1, raw };
}

function seedWorkerState(raw: Database, over: Record<string, unknown> = {}): void {
  const row = { tenant_id: "sebastian", running: 1, started_at: 1, last_note: "", updated_at: 1, last_live_dispatch_at: null, last_cycle_review_at: null, ...over };
  raw.query("INSERT INTO worker_state (tenant_id, running, started_at, last_note, updated_at, last_live_dispatch_at, last_cycle_review_at) VALUES (?, ?, ?, ?, ?, ?, ?)")
    .run(row.tenant_id, row.running, row.started_at, row.last_note, row.updated_at, row.last_live_dispatch_at, row.last_cycle_review_at);
}

function seedJob(raw: Database, over: Record<string, unknown> = {}): number {
  const row = {
    tenant_id: "sebastian", kind: "publish", clip_id: null, platform: "tiktok", api_account_id: null,
    state: "scheduled", next_run_at: null, created_at: 1000, updated_at: 1000, run_token: null,
    claimed_by: null, resume_data: null, posted_day: null, platform_id: null, evidence_url: null,
    last_error: null, retryable: null, attempts: 0, slot_day: null, language: "es",
    title: "", caption: "", r2_key: null, idempotency_key: null, manual: 0, deleted_at: null, wal_reconciled: 0,
    ...over,
  };
  const info = raw.query(
    "INSERT INTO jobs (tenant_id, kind, clip_id, platform, api_account_id, state, next_run_at, created_at, updated_at, " +
      "run_token, claimed_by, resume_data, posted_day, platform_id, evidence_url, last_error, retryable, attempts, " +
      "slot_day, language, title, caption, r2_key, idempotency_key, manual, deleted_at, wal_reconciled) " +
      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
  ).run(
    row.tenant_id, row.kind, row.clip_id, row.platform, row.api_account_id, row.state, row.next_run_at,
    row.created_at, row.updated_at, row.run_token, row.claimed_by, row.resume_data, row.posted_day,
    row.platform_id, row.evidence_url, row.last_error, row.retryable, row.attempts, row.slot_day,
    row.language, row.title, row.caption, row.r2_key, row.idempotency_key, row.manual, row.deleted_at,
    row.wal_reconciled,
  );
  return Number(info.lastInsertRowid);
}

function seedSettings(raw: Database, data: Record<string, unknown>): void {
  raw.query("INSERT INTO settings (tenant_id, data, updated_at) VALUES (?, ?, ?)")
    .run("sebastian", JSON.stringify(data), Date.now());
}

function seedBrainToken(raw: Database, createdAt: number): void {
  raw.query("INSERT INTO brain_tokens (token_hash, tenant_id, expires_at, used_at, created_at) VALUES (?, ?, ?, NULL, ?)")
    .run(`hash-${createdAt}`, "sebastian", createdAt + 30 * 60 * 1000, createdAt);
}

function seedPhase(raw: Database, phase: string, updatedAt: number): void {
  raw.query("INSERT INTO worker_phases (tenant_id, phase, updated_at) VALUES (?, ?, ?)")
    .run("sebastian", phase, updatedAt);
}

function seedWorkerEvent(raw: Database, kind: string, message: string, at: number): void {
  raw.query("INSERT INTO worker_events (tenant_id, kind, message, at) VALUES (?, ?, ?, ?)")
    .run("sebastian", kind, message, at);
}

function snapshot(raw: Database): Record<string, unknown[]> {
  const out: Record<string, unknown[]> = {};
  for (const t of TABLES) {
    try {
      out[t] = raw.query(`SELECT * FROM ${t}`).all() as unknown[];
    } catch {
      out[t] = [];
    }
  }
  return out;
}

function makeEnv(d1: D1Database, bucket: FakeR2): Env {
  return {
    DB: d1,
    TENANT: "sebastian",
    CLIPS_BUCKET: bucket as unknown as Env["CLIPS_BUCKET"],
    CREDENTIAL_ENCRYPTION_KEY: Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"),
    GITHUB_TOKEN: "",
    GITHUB_REPO: "exe2027/viral-clip-agent",
  } as unknown as Env;
}

// Fecha congelada (como analytics-auto.test.ts): 2026-11-20 15:00 UTC
// = 12:00 en Santiago (fuera de las 09:00 del dispatch diario).
const NOON = new Date("2026-11-20T15:00:00.000Z").getTime();
// 2026-11-20 12:00 UTC = 09:00 en Santiago (hora del dispatch diario).
const NINE = new Date("2026-11-20T12:00:00.000Z").getTime();

async function withFrozenDate<T>(ms: number, fn: () => Promise<T>): Promise<T> {
  const RealDate = globalThis.Date;
  const Mock = function (this: Date, ...args: unknown[]) {
    return args.length === 0 ? new RealDate(ms) : new RealDate(...(args as [number]));
  } as unknown as DateConstructor;
  Mock.now = () => ms;
  Mock.parse = RealDate.parse;
  Mock.UTC = RealDate.UTC;
  globalThis.Date = Mock;
  try {
    return await fn();
  } finally {
    globalThis.Date = RealDate;
  }
}

const realFetch = globalThis.fetch;
beforeEach(() => {
  clearBudgetCounterCache();
  globalThis.fetch = (async () => {
    throw new Error("sin red en tests");
  }) as typeof fetch;
});
afterEach(() => {
  globalThis.fetch = realFetch;
  clearBudgetCounterCache();
});

describe("cycleGate — la puerta (solo lecturas)", () => {
  test("nada pendiente → no corre, motivo none, y ningún dispatch pendiente", async () => {
    const { d1, raw } = makeDbPair();
    // Revisión reciente (hace 5 min), último live hace 5 min, watchdog enviado hace 5 min.
    seedWorkerState(raw, { last_cycle_review_at: NOON - 5 * 60 * 1000, last_live_dispatch_at: NOON - 5 * 60 * 1000 });
    seedWorkerEvent(raw, "github_dispatch", "Dispatch panel-watchdog enviado a GitHub (x).", NOON - 5 * 60 * 1000);
    const env = makeEnv(d1, new FakeR2());
    const g = await withFrozenDate(NOON, () => cycleGate(env, "sebastian", Date.now()));
    expect(g.run).toBe(false);
    expect(g.reason).toBe("none");
    expect(g.nextDueAtMs).toBeNull();
    expect(g.reviewDue).toBe(false);
    expect(g.liveDue).toBe(false);
    expect(g.watchdogDue).toBe(false);
    expect(g.dailyDue).toBe(false);
    expect(g.journalPending).toBe(false);
  });

  test("job que vence en 30 s → corre, motivo due-job", async () => {
    const { d1, raw } = makeDbPair();
    seedWorkerState(raw, { last_cycle_review_at: NOON - 5 * 60 * 1000 });
    seedJob(raw, { next_run_at: NOON + 30 * 1000 });
    const env = makeEnv(d1, new FakeR2());
    const g = await withFrozenDate(NOON, () => cycleGate(env, "sebastian", Date.now()));
    expect(g.run).toBe(true);
    expect(g.reason).toBe("due-job");
    expect(g.nextDueAtMs).toBe(NOON + 30 * 1000);
  });

  test("job que vence en 2 h → no corre (no vence en el próximo minuto)", async () => {
    const { d1, raw } = makeDbPair();
    seedWorkerState(raw, { last_cycle_review_at: NOON - 5 * 60 * 1000 });
    seedJob(raw, { next_run_at: NOON + 2 * 3600 * 1000 });
    const env = makeEnv(d1, new FakeR2());
    const g = await withFrozenDate(NOON, () => cycleGate(env, "sebastian", Date.now()));
    expect(g.run).toBe(false);
    expect(g.nextDueAtMs).toBe(NOON + 2 * 3600 * 1000);
  });

  test("job con next_run_at NULL → vence ya", async () => {
    const { d1, raw } = makeDbPair();
    seedWorkerState(raw, { last_cycle_review_at: NOON - 5 * 60 * 1000 });
    seedJob(raw, { next_run_at: null });
    const env = makeEnv(d1, new FakeR2());
    const g = await withFrozenDate(NOON, () => cycleGate(env, "sebastian", Date.now()));
    expect(g.run).toBe(true);
    expect(g.reason).toBe("due-job");
    expect(g.nextDueAtMs).toBe(0);
  });

  test("job processing con lease vencido → corre (hay que reanudarlo)", async () => {
    const { d1, raw } = makeDbPair();
    seedWorkerState(raw, { last_cycle_review_at: NOON - 5 * 60 * 1000 });
    seedJob(raw, { state: "processing", next_run_at: NOON - 1000, claimed_by: "worker" });
    const env = makeEnv(d1, new FakeR2());
    const g = await withFrozenDate(NOON, () => cycleGate(env, "sebastian", Date.now()));
    expect(g.run).toBe(true);
    expect(g.reason).toBe("due-job");
  });

  test("solo jobs posted/failed/draft → no cuentan como pendientes", async () => {
    const { d1, raw } = makeDbPair();
    seedWorkerState(raw, { last_cycle_review_at: NOON - 5 * 60 * 1000 });
    seedJob(raw, { state: "posted", next_run_at: NOON - 1000 });
    seedJob(raw, { state: "failed", next_run_at: NOON - 1000 });
    seedJob(raw, { state: "draft", next_run_at: null });
    const env = makeEnv(d1, new FakeR2());
    const g = await withFrozenDate(NOON, () => cycleGate(env, "sebastian", Date.now()));
    expect(g.run).toBe(false);
    expect(g.nextDueAtMs).toBeNull();
  });

  test("revisión periódica: hace 13 min toca; hace 5 min no; nunca, toca", async () => {
    const envOf = (lastReview: number | null) => {
      const { d1, raw } = makeDbPair();
      seedWorkerState(raw, { last_cycle_review_at: lastReview });
      return makeEnv(d1, new FakeR2());
    };
    const old = await withFrozenDate(NOON, () => cycleGate(envOf(NOON - 13 * 60 * 1000), "sebastian", Date.now()));
    expect(old.run).toBe(true);
    expect(old.reason).toBe("review");
    expect(old.reviewDue).toBe(true);
    const recent = await withFrozenDate(NOON, () => cycleGate(envOf(NOON - 5 * 60 * 1000), "sebastian", Date.now()));
    expect(recent.run).toBe(false);
    expect(recent.reviewDue).toBe(false);
    const never = await withFrozenDate(NOON, () => cycleGate(envOf(null), "sebastian", Date.now()));
    expect(never.run).toBe(true);
    expect(never.reason).toBe("review");
    // El intervalo es 12 min: justo en el límite toca.
    const edge = await withFrozenDate(NOON, () => cycleGate(envOf(NOON - CYCLE_REVIEW_INTERVAL_MS), "sebastian", Date.now()));
    expect(edge.reviewDue).toBe(true);
  });

  test("dispatch diario: a las 09:00 sin marca → dailyDue true pero el publicador no corre", async () => {
    const { d1, raw } = makeDbPair();
    seedWorkerState(raw, { last_cycle_review_at: NINE - 5 * 60 * 1000 });
    const env = makeEnv(d1, new FakeR2());
    const g = await withFrozenDate(NINE, () => cycleGate(env, "sebastian", Date.now()));
    expect(g.dailyDue).toBe(true);
    expect(g.run).toBe(false);
    // Con la marca del día, ya no toca.
    seedSettings(raw, { brain_daily_dispatched_day: "2026-11-20" });
    const g2 = await withFrozenDate(NINE, () => cycleGate(env, "sebastian", Date.now()));
    expect(g2.dailyDue).toBe(false);
    // Fuera de las 09:00, tampoco.
    const g3 = await withFrozenDate(NOON, () => cycleGate(env, "sebastian", Date.now()));
    expect(g3.dailyDue).toBe(false);
  });

  test("isDailyDispatchDue directa: 09:59 toca, 10:00 ya no", async () => {
    const { d1, raw } = makeDbPair();
    seedWorkerState(raw, {});
    expect(await isDailyDispatchDue(d1, "sebastian", new Date("2026-11-20T12:59:00.000Z").getTime())).toBe(true);
    expect(await isDailyDispatchDue(d1, "sebastian", new Date("2026-11-20T13:00:00.000Z").getTime())).toBe(false);
  });

  test("dispatch en vivo: freno por lectura, sin escribir nada", async () => {
    const { d1, raw } = makeDbPair();
    seedWorkerState(raw, { last_live_dispatch_at: NOON - 20 * 60 * 1000 });
    const env = makeEnv(d1, new FakeR2());
    // Sin token reciente ni fase: toca.
    expect(await isLiveDispatchDue(env, "sebastian", NOON)).toBe(true);
    // Último dispatch hace 5 min: no toca.
    const { d1: d1b, raw: rawb } = makeDbPair();
    seedWorkerState(rawb, { last_live_dispatch_at: NOON - 5 * 60 * 1000 });
    expect(await isLiveDispatchDue(makeEnv(d1b, new FakeR2()), "sebastian", NOON)).toBe(false);
    // Token reciente (hace 5 min): no toca aunque el throttle de worker_state pase.
    seedBrainToken(raw, NOON - 5 * 60 * 1000);
    expect(await isLiveDispatchDue(env, "sebastian", NOON)).toBe(false);
    // Sin token pero con corrida en curso: no toca.
    const { d1: d1c, raw: rawc } = makeDbPair();
    seedWorkerState(rawc, { last_live_dispatch_at: NOON - 20 * 60 * 1000 });
    seedPhase(rawc, "buscando_picos", NOON - 60 * 1000);
    expect(await isLiveDispatchDue(makeEnv(d1c, new FakeR2()), "sebastian", NOON)).toBe(false);
    // Fase vieja (>25 min): la corrida ya no está en curso, toca.
    const { d1: d1d, raw: rawd } = makeDbPair();
    seedWorkerState(rawd, { last_live_dispatch_at: NOON - 20 * 60 * 1000 });
    seedPhase(rawd, "cortando_clip", NOON - 30 * 60 * 1000);
    expect(await isLiveDispatchDue(makeEnv(d1d, new FakeR2()), "sebastian", NOON)).toBe(true);
    // Fase terminal reciente: no hay corrida en curso, toca.
    const { d1: d1e, raw: rawe } = makeDbPair();
    seedWorkerState(rawe, { last_live_dispatch_at: NOON - 20 * 60 * 1000 });
    seedPhase(rawe, "sin_directos", NOON - 60 * 1000);
    expect(await isLiveDispatchDue(makeEnv(d1e, new FakeR2()), "sebastian", NOON)).toBe(true);
  });

  test("dispatch del vigilante: lo decide el último evento enviado (lectura)", async () => {
    const { d1, raw } = makeDbPair();
    seedWorkerState(raw, {});
    // Sin eventos: toca.
    expect(await isWatchdogDispatchDue(d1, "sebastian", NOON)).toBe(true);
    // Enviado hace 5 min: no toca.
    seedWorkerEvent(raw, "github_dispatch", "Dispatch panel-watchdog enviado a GitHub (x).", NOON - 5 * 60 * 1000);
    expect(await isWatchdogDispatchDue(d1, "sebastian", NOON)).toBe(false);
    // Enviado hace 20 min: toca.
    const { d1: d1b, raw: rawb } = makeDbPair();
    seedWorkerState(rawb, {});
    seedWorkerEvent(rawb, "github_dispatch", "Dispatch panel-watchdog enviado a GitHub (x).", NOON - 20 * 60 * 1000);
    expect(await isWatchdogDispatchDue(d1b, "sebastian", NOON)).toBe(true);
    // Un dispatch FALLIDO no frena el reintento.
    const { d1: d1c, raw: rawc } = makeDbPair();
    seedWorkerState(rawc, {});
    seedWorkerEvent(rawc, "github_dispatch", "Dispatch panel-watchdog falló: GitHub devolvió HTTP 500.", NOON - 60 * 1000);
    expect(await isWatchdogDispatchDue(d1c, "sebastian", NOON)).toBe(true);
  });

  test("diario pendiente (R2): sin entradas no; con entrada sin volcar sí; con marcador que la cubre no", async () => {
    const bucket = new FakeR2();
    const { d1, raw } = makeDbPair();
    const env = makeEnv(d1, bucket);
    expect(await pendingWriteJournal(env, "sebastian", NOON)).toBe(false);
    const line = JSON.stringify({ v: 1, type: "intent", at: "2026-11-20T12:00:00.000Z", tenant: "sebastian", clip_id: 91, platform: "tiktok", account_id: 5, job_id: 7, idempotency_key: "k", day: "2026-11-20" });
    bucket.store.set("wal/sebastian/2026-11-20.jsonl", `${line}\n`);
    expect(await pendingWriteJournal(env, "sebastian", NOON)).toBe(true);
    // Con marcador que cubre la entrada, ya no hay nada pendiente.
    bucket.store.set("wal/sebastian/2026-11-20.flushed.json", JSON.stringify({ flushed_entries: 1, flushed_at: "2026-11-20T12:01:00.000Z", posted: 0 }));
    expect(await pendingWriteJournal(env, "sebastian", NOON)).toBe(false);
    // Llega una entrada nueva tras el marcador: vuelve a haber pendiente.
    bucket.store.set("wal/sebastian/2026-11-20.jsonl", `${line}\n${line}\n`);
    expect(await pendingWriteJournal(env, "sebastian", NOON)).toBe(true);
    // Y la puerta lo ve como motivo para correr.
    seedWorkerState(raw, { last_cycle_review_at: NOON - 5 * 60 * 1000 });
    const g = await withFrozenDate(NOON, () => cycleGate(env, "sebastian", Date.now()));
    expect(g.run).toBe(true);
    expect(g.reason).toBe("journal");
    expect(g.journalPending).toBe(true);
  });

  test("nextDueJobAt: el mínimo entre scheduled/processing; null si no hay", async () => {
    const { d1, raw } = makeDbPair();
    expect(await nextDueJobAt(d1, "sebastian")).toBeNull();
    seedJob(raw, { next_run_at: NOON + 3600 * 1000 });
    seedJob(raw, { next_run_at: NOON + 60 * 1000 });
    seedJob(raw, { state: "posted", next_run_at: NOON - 1000 });
    expect(await nextDueJobAt(d1, "sebastian")).toBe(NOON + 60 * 1000);
  });

  test("fail-open: sin tabla jobs (lectura rota) la puerta dice que corre", async () => {
    const { d1, raw } = makeDbPair();
    seedWorkerState(raw, { last_cycle_review_at: NOON - 5 * 60 * 1000 });
    raw.exec("DROP TABLE jobs");
    const env = makeEnv(d1, new FakeR2());
    const g = await withFrozenDate(NOON, () => cycleGate(env, "sebastian", Date.now()));
    expect(g.run).toBe(true);
    expect(g.reason).toBe("due-job");
  });

  test("idleCycleGate: todo en falso", () => {
    const g = idleCycleGate();
    expect(g.run).toBe(false);
    expect(g.reason).toBe("none");
    expect(g.liveDue).toBe(false);
    expect(g.watchdogDue).toBe(false);
    expect(g.dailyDue).toBe(false);
    expect(g.journalPending).toBe(false);
  });
});

describe("runWorkerCycle — salida temprana silenciosa", () => {
  test("nada pendiente → no corre y CERO escrituras en D1", async () => {
    const { d1, raw } = makeDbPair();
    seedWorkerState(raw, { last_cycle_review_at: NOON - 5 * 60 * 1000, last_live_dispatch_at: NOON - 5 * 60 * 1000 });
    seedWorkerEvent(raw, "github_dispatch", "Dispatch panel-watchdog enviado a GitHub (x).", NOON - 5 * 60 * 1000);
    const env = makeEnv(d1, new FakeR2());
    const before = snapshot(raw);
    const r = await withFrozenDate(NOON, () => runWorkerCycle(env, "sebastian"));
    expect(r.ran).toBe(false);
    expect(r.processed).toBe(0);
    expect(r.autoEnqueued).toBe(0);
    expect(r.gate.reason).toBe("none");
    // Ni un evento "nada que hacer": el snapshot es idéntico.
    expect(snapshot(raw)).toEqual(before);
  });

  test("Worker parado → no corre y la puerta es la vacía", async () => {
    const { d1, raw } = makeDbPair();
    seedWorkerState(raw, { running: 0 });
    const env = makeEnv(d1, new FakeR2());
    const before = snapshot(raw);
    const r = await withFrozenDate(NOON, () => runWorkerCycle(env, "sebastian"));
    expect(r.ran).toBe(false);
    expect(r.gate).toEqual(idleCycleGate());
    expect(snapshot(raw)).toEqual(before);
  });

  test("revisión periódica → corre y la única escritura D1 es la marca de revisión", async () => {
    const { d1, raw } = makeDbPair();
    seedWorkerState(raw, { last_cycle_review_at: NOON - 13 * 60 * 1000 });
    const env = makeEnv(d1, new FakeR2());
    const before = snapshot(raw);
    const r = await withFrozenDate(NOON, () => runWorkerCycle(env, "sebastian"));
    expect(r.ran).toBe(true);
    expect(r.gate.reason).toBe("review");
    expect(r.processed).toBe(0);
    expect(r.autoEnqueued).toBe(0);
    // La marca quedó en ahora.
    const afterState = raw.query("SELECT last_cycle_review_at FROM worker_state WHERE tenant_id = 'sebastian'").get() as { last_cycle_review_at: number };
    expect(afterState.last_cycle_review_at).toBe(NOON);
    // Todo lo demás idéntico a antes (la única escritura fue esa).
    const expected = structuredClone(before);
    (expected.worker_state[0] as Record<string, unknown>).last_cycle_review_at = NOON;
    expect(snapshot(raw)).toEqual(expected);
  });

  test("job que vence pronto → corre la parte de publicar (paso vod_check)", async () => {
    const { d1, raw } = makeDbPair();
    seedWorkerState(raw, { last_cycle_review_at: NOON - 5 * 60 * 1000 });
    const jobId = seedJob(raw, { kind: "vod_check", clip_id: null, state: "scheduled", next_run_at: NOON - 1000, created_at: NOON - 60 * 1000 });
    const env = makeEnv(d1, new FakeR2());
    const r = await withFrozenDate(NOON, () => runWorkerCycle(env, "sebastian"));
    expect(r.ran).toBe(true);
    expect(r.gate.reason).toBe("due-job");
    expect(r.processed).toBe(1);
    const job = raw.query("SELECT state, next_run_at, resume_data FROM jobs WHERE id = ?").get(jobId) as { state: string; next_run_at: number; resume_data: string };
    expect(job.state).toBe("processing");
    // El paso processing reprograma el siguiente paso a 10 s.
    expect(job.next_run_at).toBe(NOON + 10 * 1000);
    expect(job.resume_data).toBe("{}");
  });

  test("job que vence en 30 s → el ciclo corre pero el job aún no se toma", async () => {
    const { d1, raw } = makeDbPair();
    seedWorkerState(raw, { last_cycle_review_at: NOON - 5 * 60 * 1000 });
    const jobId = seedJob(raw, { kind: "vod_check", clip_id: null, state: "scheduled", next_run_at: NOON + 30 * 1000, created_at: NOON - 60 * 1000 });
    const env = makeEnv(d1, new FakeR2());
    const r = await withFrozenDate(NOON, () => runWorkerCycle(env, "sebastian"));
    expect(r.ran).toBe(true);
    expect(r.gate.reason).toBe("due-job");
    expect(r.processed).toBe(0);
    const job = raw.query("SELECT state FROM jobs WHERE id = ?").get(jobId) as { state: string };
    expect(job.state).toBe("scheduled");
  });

  test("force=true (avance manual) corre aunque no toque nada", async () => {
    const { d1, raw } = makeDbPair();
    seedWorkerState(raw, { last_cycle_review_at: NOON - 5 * 60 * 1000 });
    const env = makeEnv(d1, new FakeR2());
    const r = await withFrozenDate(NOON, () => runWorkerCycle(env, "sebastian", { force: true }));
    expect(r.ran).toBe(true);
    expect(r.gate.reason).toBe("none");
  });

  test("dispatch diario y en vivo pendientes: el publicador no corre pero la puerta los marca", async () => {
    const { d1, raw } = makeDbPair();
    // Revisión reciente, sin jobs, sin diario: el publicador no corre.
    seedWorkerState(raw, { last_cycle_review_at: NINE - 5 * 60 * 1000, last_live_dispatch_at: NINE - 20 * 60 * 1000 });
    const env = makeEnv(d1, new FakeR2());
    const r = await withFrozenDate(NINE, () => runWorkerCycle(env, "sebastian"));
    expect(r.ran).toBe(false);
    // Pero la puerta dice que tocan el dispatch diario y el en vivo:
    // el scheduled handler los dispara aunque el publicador no corriera.
    expect(r.gate.dailyDue).toBe(true);
    expect(r.gate.liveDue).toBe(true);
    expect(r.gate.watchdogDue).toBe(true);
  });
});

// Fin del archivo.
