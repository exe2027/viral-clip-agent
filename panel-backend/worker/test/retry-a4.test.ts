// Tests A4 (2026-11-20, anti-recaída): backoff 1/5/15/60, tope 3
// reintentos/job/día, parada hasta el día siguiente, circuito 7500
// (una sola alerta, bucles parados) y freno del auto-enqueue.
// Contra SQLite REAL (bun:sqlite) o dobles en memoria: nunca D1.
import { describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import type { D1Database } from "../src/cloudflare";
import {
  autoEnqueueAllowed,
  canRetryToday,
  dispatchRetryAllowed,
  hasRecentQuotaError,
  isD1QuotaError,
  isQuotaBreakerOpen,
  nextD1ResetMs,
  nextRetryDelayMs,
  noteDispatchFailure,
  noteQuotaAlertAttempt,
  quotaAlertDue,
  resetDispatchFailureState,
  resetQuotaBreakerState,
  retriesToday,
  retryDelayMs,
  santiagoDayKeyOf,
  santiagoDayStartMs,
  tripQuotaBreaker,
  RETRY_BACKOFF_MINUTES,
  MAX_RETRIES_PER_DAY,
  D1_QUOTA_ERROR_CODE,
} from "../src/retry";
import {
  alertQuotaExhausted,
  clearAutoEnqueueState,
  getAutoEnqueueState,
  markFailed,
  recordAutoEnqueueAttempt,
  resetAutoEnqueueStateForTests,
  seedAutoEnqueueStateFromReads,
  type JobRow,
} from "../src/jobs";

const MIN = 60_000;
const NOW = Date.now();

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

function makeDb(): { d1: D1Database; raw: Database } {
  const raw = new Database(":memory:");
  raw.exec(
    "CREATE TABLE worker_events (" +
      "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, " +
      "at INTEGER NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL)",
  );
  raw.exec(
    "CREATE TABLE clip_events (" +
      "id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER NOT NULL, " +
      "tenant_id TEXT NOT NULL DEFAULT 'sebastian', at INTEGER NOT NULL, " +
      "step TEXT NOT NULL, actor TEXT NOT NULL, message TEXT NOT NULL, " +
      "failed INTEGER DEFAULT 0 NOT NULL)",
  );
  raw.exec(
    "CREATE TABLE jobs (" +
      "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, " +
      "kind TEXT NOT NULL, clip_id INTEGER, platform TEXT, " +
      "api_account_id INTEGER, state TEXT NOT NULL, " +
      "idempotency_key TEXT, resume_data TEXT, platform_id TEXT, " +
      "evidence_url TEXT, title TEXT, caption TEXT, r2_key TEXT, " +
      "language TEXT DEFAULT 'es', posted_day TEXT, attempts INTEGER DEFAULT 0, " +
      "last_error TEXT, retryable INTEGER DEFAULT 1, next_run_at INTEGER, " +
      "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, " +
      "claimed_by TEXT, run_token TEXT, slot_day TEXT, manual INTEGER DEFAULT 0)",
  );
  raw.exec(
    "CREATE TABLE clips (" +
      "id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, " +
      "publish_states TEXT DEFAULT '{}', status TEXT DEFAULT 'aprobado', " +
      "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)",
  );
  raw.exec(
    "CREATE TABLE notifications (" +
      "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, " +
      "kind TEXT NOT NULL, clip_id INTEGER, message TEXT NOT NULL, " +
      "created_at INTEGER NOT NULL)",
  );
  const d1 = { prepare: (sql: string) => new SqliteStmt(raw, sql) } as unknown as D1Database;
  return { d1, raw };
}

function count(raw: Database, table: string): number {
  return (raw.query(`SELECT COUNT(*) AS n FROM ${table}`).get() as { n: number }).n;
}

function insertJob(raw: Database, over: Partial<Record<string, unknown>> = {}): number {
  const row = {
    tenant_id: "sebastian", kind: "publish", clip_id: 1, platform: "tiktok",
    api_account_id: 5, state: "scheduled", title: "t", caption: "t",
    language: "es", attempts: 0, retryable: 1, next_run_at: null,
    created_at: NOW, updated_at: NOW, run_token: null, slot_day: null,
    ...over,
  };
  const keys = Object.keys(row);
  const values = keys.map((k) => (row as Record<string, unknown>)[k]);
  raw.query(`INSERT INTO jobs (${keys.join(", ")}) VALUES (${keys.map(() => "?").join(", ")})`).run(...(values as []));
  return (raw.query("SELECT last_insert_rowid() AS id").get() as { id: number }).id;
}

async function readJob(d1: D1Database, id: number): Promise<JobRow> {
  const row = await d1.prepare("SELECT * FROM jobs WHERE id = ?").bind(id).first<JobRow>();
  if (!row) throw new Error(`job ${id} no existe`);
  return row;
}

describe("umbrales A4 (única fuente, retry.ts)", () => {
  test("backoff 1, 5, 15, 60 minutos", () => {
    expect([...RETRY_BACKOFF_MINUTES]).toEqual([1, 5, 15, 60]);
    expect(retryDelayMs(1)).toBe(1 * MIN);
    expect(retryDelayMs(2)).toBe(5 * MIN);
    expect(retryDelayMs(3)).toBe(15 * MIN);
    expect(retryDelayMs(4)).toBe(60 * MIN);
    expect(retryDelayMs(5)).toBeNull();
    expect(retryDelayMs(0)).toBeNull();
  });
  test("tope 3 reintentos por job y día", () => {
    expect(MAX_RETRIES_PER_DAY).toBe(3);
    expect(D1_QUOTA_ERROR_CODE).toBe(7500);
    expect(canRetryToday(0)).toBe(true);
    expect(canRetryToday(2)).toBe(true);
    expect(canRetryToday(3)).toBe(false);
    expect(canRetryToday(4)).toBe(false);
  });
  test("retraso del siguiente reintento según los ya usados hoy", () => {
    expect(nextRetryDelayMs(0)).toBe(1 * MIN);
    expect(nextRetryDelayMs(1)).toBe(5 * MIN);
    expect(nextRetryDelayMs(2)).toBe(15 * MIN);
    expect(nextRetryDelayMs(3)).toBe(60 * MIN);
  });
});

describe("día de Santiago y reset de cuota", () => {
  test("inicio del día de Santiago: 12:00 -03 -> 03:00Z", () => {
    const noon = Date.parse("2026-11-20T12:00:00-03:00");
    expect(santiagoDayStartMs(noon)).toBe(Date.parse("2026-11-20T03:00:00Z"));
    expect(santiagoDayKeyOf(noon)).toBe("2026-11-20");
  });
  test("el reset de cuota es la próxima 00:00 UTC (21:00 -03)", () => {
    expect(nextD1ResetMs(Date.parse("2026-11-20T12:00:00Z"))).toBe(Date.parse("2026-11-21T00:00:00Z"));
    expect(nextD1ResetMs(Date.parse("2026-11-20T23:30:00Z"))).toBe(Date.parse("2026-11-21T00:00:00Z"));
  });
  test("retriesToday: solo cuentan los intentos de hoy", () => {
    const today = santiagoDayKeyOf(NOW);
    expect(retriesToday({ attempts: 2, updated_at: NOW }, today)).toBe(2);
    expect(retriesToday({ attempts: 2, updated_at: NOW - 26 * 3600_000 }, today)).toBe(0);
  });
});

describe("detección del error 7500", () => {
  test("casa por el código 7500 y por D1 + cuota/límite", () => {
    expect(isD1QuotaError(new Error("D1_ERROR: Too many writes (7500)"))).toBe(true);
    expect(isD1QuotaError("falla 7500 al escribir")).toBe(true);
    expect(isD1QuotaError(new Error("D1_ERROR: quota exceeded"))).toBe(true);
    expect(isD1QuotaError(new Error("D1: límite de escrituras superado"))).toBe(true);
    expect(isD1QuotaError(new Error("HTTP 500 Internal Server Error"))).toBe(false);
    expect(isD1QuotaError(new Error("HTTP 429 Too Many Requests"))).toBe(false);
    expect(isD1QuotaError("red: Network connection lost")).toBe(false);
    expect(isD1QuotaError(null)).toBe(false);
  });
});

describe("circuito 7500 (memoria + lectura)", () => {
  test("disparado, sigue abierto hasta el reset y se cierra en él", () => {
    resetQuotaBreakerState();
    const tripAt = Date.parse("2026-11-20T12:00:00Z");
    expect(isQuotaBreakerOpen(tripAt)).toBe(false);
    tripQuotaBreaker(tripAt);
    expect(isQuotaBreakerOpen(tripAt + 60 * MIN)).toBe(true);
    expect(isQuotaBreakerOpen(Date.parse("2026-11-20T23:00:00Z"))).toBe(true);
    expect(isQuotaBreakerOpen(Date.parse("2026-11-21T00:00:00Z"))).toBe(false);
    expect(isQuotaBreakerOpen(Date.parse("2026-11-21T00:00:00Z"))).toBe(false);
  });
  test("la lectura de un 7500 reciente re-dispara el circuito", async () => {
    resetQuotaBreakerState();
    const { d1, raw } = makeDb();
    raw.query("INSERT INTO jobs (tenant_id, kind, clip_id, platform, state, attempts, last_error, created_at, updated_at) VALUES ('sebastian', 'publish', 1, 'tiktok', 'failed', 1, 'D1_ERROR 7500', ?, ?)").run(NOW, NOW - 10 * MIN);
    expect(await hasRecentQuotaError(d1, "sebastian", 60 * MIN, NOW)).toBe(true);
    const { d1: d1b, raw: rawb } = makeDb();
    rawb.query("INSERT INTO worker_events (tenant_id, at, kind, message) VALUES ('sebastian', ?, 'github_dispatch', 'No se pudo emitir el token: D1 7500')").run(NOW - 30 * MIN);
    expect(await hasRecentQuotaError(d1b, "sebastian", 60 * MIN, NOW)).toBe(true);
    const { d1: d1c } = makeDb();
    expect(await hasRecentQuotaError(d1c, "sebastian", 60 * MIN, NOW)).toBe(false);
  });
  test("un 7500 viejo (> 1 h) no re-dispara el circuito", async () => {
    const { d1, raw } = makeDb();
    raw.query("INSERT INTO jobs (tenant_id, kind, clip_id, platform, state, attempts, last_error, created_at, updated_at) VALUES ('sebastian', 'publish', 1, 'tiktok', 'failed', 1, 'D1_ERROR 7500', ?, ?)").run(NOW, NOW - 2 * 3600_000);
    expect(await hasRecentQuotaError(d1, "sebastian", 60 * MIN, NOW)).toBe(false);
  });
  test("alerta única: una al día; si falla, reintento pasado 1 h", async () => {
    resetQuotaBreakerState();
    const { d1, raw } = makeDb();
    const today = santiagoDayKeyOf(NOW);
    expect(quotaAlertDue(NOW, today)).toBe(true);
    expect(await alertQuotaExhausted(d1, "sebastian", undefined, NOW)).toBe("sent");
    expect(count(raw, "notifications")).toBe(1);
    expect(await alertQuotaExhausted(d1, "sebastian", undefined, NOW)).toBe("already");
    expect(count(raw, "notifications")).toBe(1);
    // Al día siguiente vuelve a tocar.
    const tomorrow = Date.parse("2026-11-21T12:00:00-03:00");
    expect(quotaAlertDue(tomorrow, santiagoDayKeyOf(tomorrow))).toBe(true);
  });
  test("alerta fallida (sin canal): no se repite antes de 1 h, sí después", () => {
    resetQuotaBreakerState();
    const today = santiagoDayKeyOf(NOW);
    // Sin envío posible: simula un intento fallido (sin panel ni
    // Telegram no hay canal; noteQuotaAlertAttempt con sent=false).
    noteQuotaAlertAttempt(NOW, false, today);
    expect(quotaAlertDue(NOW + 30 * MIN, today)).toBe(false);
    expect(quotaAlertDue(NOW + 61 * MIN, today)).toBe(true);
  });
});

describe("markFailed con el freno A4 (SQLite)", () => {
  test("fallos transitorios: 1, 5 y 15 min; al 4.º, parado hasta mañana", async () => {
    resetQuotaBreakerState();
    const { d1, raw } = makeDb();
    raw.query("INSERT INTO clips (id, tenant_id, publish_states, created_at, updated_at) VALUES (1, 'sebastian', '{}', ?, ?)").run(NOW, NOW);
    const id = insertJob(raw);
    const t0 = Date.now();
    // 1.er fallo -> reintento en 1 min.
    await markFailed(d1, await readJob(d1, id), "HTTP 500", true);
    let job = await readJob(d1, id);
    expect(job.state).toBe("scheduled");
    expect(job.attempts).toBe(1);
    expect(job.next_run_at).toBeGreaterThanOrEqual(t0 + 1 * MIN - 5000);
    expect(job.next_run_at).toBeLessThanOrEqual(t0 + 1 * MIN + 5000);
    // 2.º fallo -> 5 min.
    await markFailed(d1, job, "HTTP 500", true);
    job = await readJob(d1, id);
    expect(job.state).toBe("scheduled");
    expect(job.attempts).toBe(2);
    expect(job.next_run_at).toBeGreaterThanOrEqual(t0 + 5 * MIN - 5000);
    // 3.er fallo -> 15 min.
    await markFailed(d1, job, "HTTP 500", true);
    job = await readJob(d1, id);
    expect(job.state).toBe("scheduled");
    expect(job.attempts).toBe(3);
    expect(job.next_run_at).toBeGreaterThanOrEqual(t0 + 15 * MIN - 5000);
    // 4.º fallo: tope del día -> failed terminal, sin next_run_at.
    await markFailed(d1, job, "HTTP 500", true);
    job = await readJob(d1, id);
    expect(job.state).toBe("failed");
    expect(job.retryable).toBe(0);
    expect(job.attempts).toBe(4);
    expect(job.last_error).toContain("tope de 3 reintentos");
  });
  test("el contador se resetea al cambiar de día", async () => {
    const { d1, raw } = makeDb();
    raw.query("INSERT INTO clips (id, tenant_id, publish_states, created_at, updated_at) VALUES (1, 'sebastian', '{}', ?, ?)").run(NOW, NOW);
    const yesterday = NOW - 26 * 3600_000;
    const id = insertJob(raw, { attempts: 3, updated_at: yesterday, created_at: yesterday });
    await markFailed(d1, await readJob(d1, id), "HTTP 500", true);
    const job = await readJob(d1, id);
    expect(job.state).toBe("scheduled");
    expect(job.attempts).toBe(1);
  });
  test("error 7500: failed terminal, sin reintento, circuito abierto y una alerta", async () => {
    resetQuotaBreakerState();
    const { d1, raw } = makeDb();
    raw.query("INSERT INTO clips (id, tenant_id, publish_states, created_at, updated_at) VALUES (1, 'sebastian', '{}', ?, ?)").run(NOW, NOW);
    const id = insertJob(raw);
    await markFailed(d1, await readJob(d1, id), "D1_ERROR: Too many writes (7500)", true);
    const job = await readJob(d1, id);
    expect(job.state).toBe("failed");
    expect(job.retryable).toBe(0);
    expect(job.next_run_at).toBeNull();
    expect(isQuotaBreakerOpen(Date.now())).toBe(true);
    expect(count(raw, "notifications")).toBe(1);
    // Un segundo 7500 el mismo día no repite la alerta.
    const id2 = insertJob(raw, { clip_id: 1 });
    await markFailed(d1, await readJob(d1, id2), "D1_ERROR 7500", true);
    expect(count(raw, "notifications")).toBe(1);
  });
});

describe("freno de dispatch fallido", () => {
  test("tras 1 fallo espera 1 min, tras 2 espera 5, tras 3 no hoy", () => {
    resetDispatchFailureState();
    const t0 = Date.parse("2026-11-20T12:00:00Z");
    expect(dispatchRetryAllowed("panel-live", t0)).toBe(true);
    noteDispatchFailure("panel-live", t0);
    expect(dispatchRetryAllowed("panel-live", t0 + 30_000)).toBe(false);
    expect(dispatchRetryAllowed("panel-live", t0 + 61_000)).toBe(true);
    noteDispatchFailure("panel-live", t0 + 61_000);
    expect(dispatchRetryAllowed("panel-live", t0 + 61_000 + 4 * MIN)).toBe(false);
    expect(dispatchRetryAllowed("panel-live", t0 + 61_000 + 5 * MIN + 1000)).toBe(true);
    noteDispatchFailure("panel-live", t0 + 61_000 + 5 * MIN + 1000);
    expect(dispatchRetryAllowed("panel-live", t0 + 61_000 + 5 * MIN + 2000)).toBe(false);
    // Al día siguiente vuelve a tocar.
    expect(dispatchRetryAllowed("panel-live", t0 + 24 * 3600_000)).toBe(true);
  });
  test("un tipo de evento no frena al otro", () => {
    resetDispatchFailureState();
    const t0 = Date.parse("2026-11-20T12:00:00Z");
    noteDispatchFailure("panel-live", t0);
    noteDispatchFailure("panel-live", t0);
    noteDispatchFailure("panel-live", t0);
    expect(dispatchRetryAllowed("panel-live", t0 + 1000)).toBe(false);
    expect(dispatchRetryAllowed("panel-watchdog", t0 + 1000)).toBe(true);
  });
});

describe("freno del auto-enqueue por clip", () => {
  test("sin intentos se puede; con 1 fallo espera 1 min; con 2, 5; con 3, tope", () => {
    const t0 = Date.parse("2026-11-20T12:00:00Z");
    expect(autoEnqueueAllowed({ attemptsToday: 0, lastAttemptMs: null }, t0).allowed).toBe(true);
    expect(autoEnqueueAllowed({ attemptsToday: 1, lastAttemptMs: t0 - 30_000 }, t0).allowed).toBe(false);
    expect(autoEnqueueAllowed({ attemptsToday: 1, lastAttemptMs: t0 - 61_000 }, t0).allowed).toBe(true);
    expect(autoEnqueueAllowed({ attemptsToday: 2, lastAttemptMs: t0 - 4 * MIN }, t0).allowed).toBe(false);
    expect(autoEnqueueAllowed({ attemptsToday: 2, lastAttemptMs: t0 - 6 * MIN }, t0).allowed).toBe(true);
    const capped = autoEnqueueAllowed({ attemptsToday: 3, lastAttemptMs: t0 - 60 * MIN }, t0);
    expect(capped.allowed).toBe(false);
    expect(capped.reason).toContain("tope");
  });
  test("record/limpieza del estado en memoria", () => {
    resetAutoEnqueueStateForTests();
    const today = santiagoDayKeyOf(NOW);
    expect(getAutoEnqueueState(102, today)).toBeNull();
    recordAutoEnqueueAttempt(102, NOW - 1000, today);
    expect(getAutoEnqueueState(102, today)).toEqual({ attemptsToday: 1, lastAttemptMs: NOW - 1000 });
    recordAutoEnqueueAttempt(102, NOW, today);
    expect(getAutoEnqueueState(102, today)?.attemptsToday).toBe(2);
    clearAutoEnqueueState(102);
    expect(getAutoEnqueueState(102, today)).toBeNull();
    // Otro día: el estado viejo no vale.
    recordAutoEnqueueAttempt(102, NOW, today);
    expect(getAutoEnqueueState(102, "2026-11-21")).toBeNull();
  });
  test("siembra por lectura: con intento hoy asume 1; sin intento, nada", async () => {
    resetAutoEnqueueStateForTests();
    const { d1, raw } = makeDb();
    const today = santiagoDayKeyOf(NOW);
    const attemptAt = santiagoDayStartMs(NOW) + 60 * MIN;
    raw.query("INSERT INTO clip_events (clip_id, tenant_id, at, step, actor, message, failed) VALUES (102, 'sebastian', ?, 'publicado', 'worker', 'Publicación frenada en tiktok: tope diario.', 1)").run(attemptAt);
    await seedAutoEnqueueStateFromReads(d1, "sebastian", 102, today, NOW);
    expect(getAutoEnqueueState(102, today)).toEqual({ attemptsToday: 1, lastAttemptMs: attemptAt });
    // Clip sin intentos hoy: sin estado.
    await seedAutoEnqueueStateFromReads(d1, "sebastian", 103, today, NOW);
    expect(getAutoEnqueueState(103, today)).toBeNull();
    // Un intento de AYER no siembra.
    const yesterdayAt = santiagoDayStartMs(NOW) - 60 * MIN;
    raw.query("INSERT INTO clip_events (clip_id, tenant_id, at, step, actor, message, failed) VALUES (104, 'sebastian', ?, 'publicado', 'worker', 'Publicación frenada.', 1)").run(yesterdayAt);
    await seedAutoEnqueueStateFromReads(d1, "sebastian", 104, today, NOW);
    expect(getAutoEnqueueState(104, today)).toBeNull();
  });
});
