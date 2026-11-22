// Tests A2 — "escribir solo cuando cambia algo" (2026-11-20, cuota D1).
//
// Cubre: dedupe extendida a TODAS las tablas de eventos (notifications
// y la ruta de worker-phase, que se saltaba la dedupe), contadores
// agregados (0034), salto de UPDATE sin cambios, batch (D1 batch) y
// caducidad nocturna (14 días, lotes de 500, hito 70%). Contra
// SQLite REAL (bun:sqlite, mismo motor que D1). Sin red, sin D1 de
// producción: la base es :memory: y se crea en cada test. Las
// migraciones 0034/0036 se leen del árbol y se ejecutan en :memory:
// eso valida su sintaxis sin aplicarlas en la D1 real (cuota
// agotada: 104.610/100.000, reset 21:00 -03).
//
// El reloj es un parámetro explícito (nowMsValue / opts.nowMsValue /
// filas sembradas con hora fija): no se mockea Date.now.
import { describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { D1_DAILY_ACCOUNT_LIMIT } from "../src/budget";
import {
  EVENT_COUNTER_SAMPLE_MS,
  EVENT_DEDUPE_WINDOW_MS,
  EVENT_PRUNE_BATCH_SIZE,
  EVENT_PRUNE_MAX_ROWS_PER_NIGHT,
  EVENT_PRUNE_WRITE_GATE,
  createClipStat,
  createTelegramStopConfirmation,
  effectiveSettings,
  eventWindowStart,
  estimateTodayWrites,
  formatEventCounterSummary,
  hasRecentIdenticalNotification,
  isPruneHour,
  lastIdenticalNotificationAt,
  listEventCounters,
  logClipEvent,
  logWorkerEvent,
  markJobDeleted,
  moveQueueClip,
  pruneOldEvents,
  recordEventCounter,
  refreshAccountMaturity,
  saveAnalyticsStatus,
  saveSettings,
  updateClip,
  updateClipPublishState,
  updateClipPublishStates,
  updateInfluencer,
  type EventCounterRow,
} from "../src/db";
import { notify } from "../src/jobs";
import { handleReportClipEvent } from "../src/worker-phase";
import type { D1Database } from "../src/cloudflare";

const NOW = Date.now();
const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;
// 03:30 America/Santiago (nov = -03): hora fija de la caducidad.
const PRUNE_NOW = Date.UTC(2026, 10, 20, 6, 30, 0);

// --- Shim D1 sobre bun:sqlite, con registro de escrituras y batch ---
interface ExecEntry {
  sql: string;
  changes: number;
}

class SqliteStmt {
  constructor(
    private db: Database,
    private sql: string,
    private params: unknown[] = [],
    private log?: ExecEntry[],
  ) {}
  bind(...values: unknown[]): SqliteStmt {
    return new SqliteStmt(this.db, this.sql, values, this.log);
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
    this.log?.push({ sql: this.sql, changes: info.changes });
    return { results: [], success: true, meta: { changes: info.changes, last_row_id: Number(info.lastInsertRowid) } };
  }
}

function makeDb(): { d1: D1Database; raw: Database; execLog: ExecEntry[]; batchCalls: { n: number } } {
  const raw = new Database(":memory:");
  const execLog: ExecEntry[] = [];
  const batchCalls = { n: 0 };
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
    "CREATE TABLE notifications (" +
      "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, " +
      "kind TEXT NOT NULL, clip_id INTEGER, message TEXT NOT NULL, " +
      "created_at INTEGER NOT NULL, read_at INTEGER)",
  );
  raw.exec(
    "CREATE TABLE event_counters (" +
      "tenant_id TEXT NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL, " +
      "message TEXT NOT NULL, window_start INTEGER NOT NULL, " +
      "count INTEGER NOT NULL DEFAULT 1, first_at INTEGER NOT NULL, " +
      "last_at INTEGER NOT NULL, " +
      "PRIMARY KEY (tenant_id, source, kind, message, window_start))",
  );
  raw.exec(
    "CREATE TABLE clips (" +
      "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, " +
      "title TEXT NOT NULL DEFAULT '', score INTEGER NOT NULL DEFAULT 0, " +
      "status TEXT NOT NULL DEFAULT 'en_carpeta', archived INTEGER NOT NULL DEFAULT 0, " +
      "origin TEXT NOT NULL DEFAULT 'vod', platforms TEXT NOT NULL DEFAULT '[]', " +
      "publish_states TEXT NOT NULL DEFAULT '{}', published_at INTEGER, " +
      "manual_rank INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL DEFAULT 0)",
  );
  raw.exec(
    "CREATE TABLE influencers (" +
      "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, " +
      "name TEXT NOT NULL DEFAULT '', platform TEXT NOT NULL DEFAULT '', " +
      "profile_url TEXT NOT NULL DEFAULT '', language TEXT NOT NULL DEFAULT 'es', " +
      "permission INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 0, " +
      "notes TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL DEFAULT 0, " +
      "game TEXT NOT NULL DEFAULT '', permission_date TEXT NOT NULL DEFAULT '', " +
      "publish_platforms TEXT NOT NULL DEFAULT '[]')",
  );
  raw.exec(
    "CREATE TABLE settings (tenant_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at INTEGER NOT NULL)",
  );
  raw.exec(
    "CREATE TABLE api_accounts (" +
      "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, " +
      "platform TEXT NOT NULL, first_published_at INTEGER, " +
      "published_count INTEGER NOT NULL DEFAULT 0, strikes INTEGER NOT NULL DEFAULT 0, " +
      "maturity TEXT NOT NULL DEFAULT 'nueva', maturity_checked_day TEXT)",
  );
  raw.exec(
    "CREATE TABLE telegram_links (" +
      "chat_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, " +
      "linked_at INTEGER NOT NULL, last_sent_day TEXT)",
  );
  raw.exec(
    "CREATE TABLE telegram_stop_confirmations (" +
      "chat_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, expires_at INTEGER NOT NULL)",
  );
  raw.exec(
    "CREATE TABLE analytics_status (" +
      "tenant_id TEXT NOT NULL, platform TEXT NOT NULL, status TEXT NOT NULL, " +
      "reason TEXT NOT NULL DEFAULT '', last_measured_at INTEGER, updated_at INTEGER NOT NULL, " +
      "PRIMARY KEY (tenant_id, platform))",
  );
  raw.exec(
    "CREATE TABLE jobs (" +
      "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, " +
      "clip_id INTEGER, platform TEXT, state TEXT NOT NULL DEFAULT 'scheduled', " +
      "deleted_at INTEGER, created_at INTEGER NOT NULL DEFAULT 0)",
  );
  raw.exec(
    "CREATE TABLE brain_tokens (" +
      "token_hash TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, " +
      "expires_at INTEGER NOT NULL, used_at INTEGER, created_at INTEGER NOT NULL)",
  );
  raw.exec(
    "CREATE TABLE clip_stats (" +
      "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, " +
      "clip_id INTEGER NOT NULL, platform TEXT NOT NULL, views INTEGER, " +
      "retention_pct REAL, shares INTEGER, saves INTEGER, comments INTEGER, " +
      "new_subs INTEGER, window_label TEXT NOT NULL, recorded_at INTEGER NOT NULL)",
  );
  const d1 = {
    prepare: (sql: string) => new SqliteStmt(raw, sql, [], execLog),
    batch: async (stmts: SqliteStmt[]) => {
      batchCalls.n++;
      const out = [];
      for (const s of stmts) out.push(await s.run());
      return out;
    },
  } as unknown as D1Database;
  return { d1, raw, execLog, batchCalls };
}

function count(raw: Database, table: string): number {
  return (raw.query(`SELECT COUNT(*) AS n FROM ${table}`).get() as { n: number }).n;
}

function writes(execLog: ExecEntry[], table?: string): number {
  return execLog
    .filter((e) => e.changes > 0 && (!table || new RegExp(`\\b${table}\\b`, "i").test(e.sql)))
    .reduce((a, e) => a + e.changes, 0);
}

function seedClip(raw: Database, over: Record<string, unknown> = {}): number {
  const row = {
    tenant_id: "sebastian",
    title: "clip",
    score: 80,
    status: "en_carpeta",
    archived: 0,
    origin: "vod",
    platforms: JSON.stringify(["youtube_shorts", "tiktok"]),
    publish_states: "{}",
    published_at: null,
    manual_rank: null,
    created_at: 1000,
    updated_at: 1000,
    ...over,
  };
  const info = raw
    .query(
      "INSERT INTO clips (tenant_id, title, score, status, archived, origin, platforms, publish_states, published_at, manual_rank, created_at, updated_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )
    .run(
      row.tenant_id, row.title, row.score, row.status, row.archived, row.origin,
      row.platforms, row.publish_states, row.published_at, row.manual_rank,
      row.created_at, row.updated_at,
    );
  return Number(info.lastInsertRowid);
}

describe("dedupe extendida: notifications (A2)", () => {
  test("notify() con el mismo aviso repetido: 1 fila + 1 contador", async () => {
    const { d1, raw } = makeDb();
    const msg = "Cuenta #1 (tiktok): tope diario alcanzado.";
    await notify(d1, { kind: "credential", message: msg }, "sebastian");
    await notify(d1, { kind: "credential", message: msg }, "sebastian");
    await notify(d1, { kind: "credential", message: msg }, "sebastian");
    expect(count(raw, "notifications")).toBe(1);
    const counters = raw.query("SELECT * FROM event_counters").all() as EventCounterRow[];
    // Un solo contador (notifications): en el camino suprimido notify
    // vuelve antes de llamar a logWorkerEvent, así que el evento
    // "aviso" de worker_events solo se escribe una vez (llamada 1)
    // y nunca se suprime: no hay nada que contar ahí.
    expect(counters).toHaveLength(1);
    const c = counters[0] as EventCounterRow;
    expect(c.source).toBe("notifications");
    expect(c.kind).toBe("credential");
    expect(c.count).toBe(1);
  });

  test("otro kind u otro mensaje sí se avisa", async () => {
    const { d1, raw } = makeDb();
    await notify(d1, { kind: "credential", message: "Fallo A." }, "sebastian");
    await notify(d1, { kind: "failed", message: "Fallo A." }, "sebastian");
    await notify(d1, { kind: "credential", message: "Fallo B." }, "sebastian");
    expect(count(raw, "notifications")).toBe(3);
  });

  test("pasada la ventana, el mismo aviso vuelve a escribirse", async () => {
    const { d1, raw } = makeDb();
    const msg = "Fallo A.";
    raw
      .query("INSERT INTO notifications (tenant_id, kind, clip_id, message, created_at) VALUES (?, ?, NULL, ?, ?)")
      .run("sebastian", "credential", msg, NOW - EVENT_DEDUPE_WINDOW_MS - MIN);
    await notify(d1, { kind: "credential", message: msg }, "sebastian");
    expect(count(raw, "notifications")).toBe(2);
  });

  test("hasRecentIdenticalNotification / lastIdenticalNotificationAt", async () => {
    const { d1, raw } = makeDb();
    expect(await hasRecentIdenticalNotification(d1, "sebastian", "credential", "Fallo A.", NOW)).toBe(false);
    expect(await lastIdenticalNotificationAt(d1, "sebastian", "credential", "Fallo A.")).toBeNull();
    raw
      .query("INSERT INTO notifications (tenant_id, kind, clip_id, message, created_at) VALUES (?, ?, NULL, ?, ?)")
      .run("sebastian", "credential", "Fallo A.", NOW - 5 * MIN);
    expect(await hasRecentIdenticalNotification(d1, "sebastian", "credential", "Fallo A.", NOW)).toBe(true);
    expect(await lastIdenticalNotificationAt(d1, "sebastian", "credential", "Fallo A.")).toBe(NOW - 5 * MIN);
    expect(await hasRecentIdenticalNotification(d1, "kaez", "credential", "Fallo A.", NOW)).toBe(false);
  });
});

describe("dedupe extendida: worker-phase ya no escribe directo (A2)", () => {
  test("handleReportClipEvent con el mismo descarte: 1 fila + contador", async () => {
    const { d1, raw } = makeDb();
    const body = { kind: "clip_descartado", message: "Clip sin webcam: no pasa la puerta." };
    const r1 = await handleReportClipEvent(d1, "sebastian", body, NOW);
    const r2 = await handleReportClipEvent(d1, "sebastian", body, NOW);
    expect(r1.status).toBe(200);
    expect(r2.status).toBe(200);
    expect(count(raw, "worker_events")).toBe(1);
    expect(count(raw, "event_counters")).toBe(1);
  });
});

describe("contadores agregados (A2, migración 0034)", () => {
  test("60 repeticiones del mismo freno: 1 evento + 1 contador (count=1)", async () => {
    const { d1, raw } = makeDb();
    const msg = "Clip #102 frenado en tiktok: tope diario alcanzado.";
    for (let i = 0; i < 60; i++) {
      await logWorkerEvent(d1, "auto_publish", msg, "sebastian", { nowMsValue: NOW });
    }
    expect(count(raw, "worker_events")).toBe(1);
    expect(count(raw, "event_counters")).toBe(1);
    const c = raw.query("SELECT * FROM event_counters").get() as EventCounterRow;
    expect(c.source).toBe("worker_events");
    expect(c.count).toBe(1);
    expect(c.first_at).toBe(NOW);
    expect(c.last_at).toBe(NOW);
  });

  test("muestreo horario: +30 min no suma, +61 min suma 1", async () => {
    const { d1 } = makeDb();
    const msg = "Clip #102 frenado en tiktok: tope diario alcanzado.";
    await recordEventCounter(d1, "worker_events", "sebastian", "auto_publish", msg, NOW, NOW);
    await recordEventCounter(d1, "worker_events", "sebastian", "auto_publish", msg, NOW, NOW + 30 * MIN);
    let rows = await listEventCounters(d1, "sebastian", 0);
    expect(rows).toHaveLength(1);
    expect(rows[0]?.count).toBe(1);
    await recordEventCounter(d1, "worker_events", "sebastian", "auto_publish", msg, NOW, NOW + 61 * MIN);
    rows = await listEventCounters(d1, "sebastian", 0);
    expect(rows[0]?.count).toBe(2);
    expect(rows[0]?.last_at).toBe(NOW + 61 * MIN);
  });

  test("ventana nueva: el recordatorio abre otro contador", async () => {
    const { d1, raw } = makeDb();
    const msg = "Clip #102 frenado en tiktok: tope diario alcanzado.";
    await logWorkerEvent(d1, "auto_publish", msg, "sebastian", { nowMsValue: NOW });
    await logWorkerEvent(d1, "auto_publish", msg, "sebastian", { nowMsValue: NOW + MIN });
    expect(count(raw, "event_counters")).toBe(1);
    // Pasada la ventana de 6 h: recordatorio (nuevo evento) y, a la
    // siguiente repetición, contador nuevo para la ventana nueva.
    await logWorkerEvent(d1, "auto_publish", msg, "sebastian", { nowMsValue: NOW + EVENT_DEDUPE_WINDOW_MS + MIN });
    expect(count(raw, "worker_events")).toBe(2);
    await logWorkerEvent(d1, "auto_publish", msg, "sebastian", { nowMsValue: NOW + EVENT_DEDUPE_WINDOW_MS + 2 * MIN });
    expect(count(raw, "event_counters")).toBe(2);
  });

  test("eventWindowStart alinea a 6 h y el resumen dice muestras, no veces", () => {
    expect(eventWindowStart(NOW) % (6 * HOUR)).toBe(0);
    expect(EVENT_COUNTER_SAMPLE_MS).toBe(HOUR);
    const row: EventCounterRow = {
      tenant_id: "sebastian",
      source: "worker_events",
      kind: "auto_publish",
      message: "Clip #102 frenado en tiktok: tope diario alcanzado.",
      window_start: Date.UTC(2026, 10, 20, 6, 0, 0),
      count: 3,
      first_at: Date.UTC(2026, 10, 20, 11, 5, 0), // 08:05 Santiago
      last_at: Date.UTC(2026, 10, 20, 16, 47, 0), // 13:47 Santiago
    };
    expect(formatEventCounterSummary(row)).toBe(
      "Clip #102 frenado en tiktok: tope diario alcanzado.: activo entre 08:05 y 13:47 (3 muestras/hora)",
    );
  });

  test("logClipEvent suprimido también deja contador (clip_events)", async () => {
    const { d1, raw } = makeDb();
    const msg = "Publicación frenada en tiktok: tope diario alcanzado.";
    await logClipEvent(d1, 102, "publicado", "worker", msg, true, "sebastian", { nowMsValue: NOW });
    await logClipEvent(d1, 102, "publicado", "worker", msg, true, "sebastian", { nowMsValue: NOW + MIN });
    expect(count(raw, "clip_events")).toBe(1);
    const c = raw.query("SELECT * FROM event_counters").get() as EventCounterRow;
    expect(c.source).toBe("clip_events");
    expect(c.kind).toBe("publicado");
  });
});

describe("salto de UPDATE sin cambios (A2)", () => {
  test("updateClip con el mismo título: no escribe nada", async () => {
    const { d1, raw, execLog } = makeDb();
    const id = seedClip(raw, { title: "clip A", updated_at: 1000 });
    execLog.length = 0;
    const row = await updateClip(d1, id, "sebastian", { title: "clip A" });
    expect(row?.title).toBe("clip A");
    expect(writes(execLog)).toBe(0);
    expect((raw.query("SELECT updated_at AS u FROM clips WHERE id = ?").get(id) as { u: number }).u).toBe(1000);
  });

  test("updateClip con título distinto: escribe una vez", async () => {
    const { d1, raw, execLog } = makeDb();
    const id = seedClip(raw, { title: "clip A", updated_at: 1000 });
    execLog.length = 0;
    const row = await updateClip(d1, id, "sebastian", { title: "clip B" });
    expect(row?.title).toBe("clip B");
    expect(writes(execLog)).toBe(1);
  });

  test("updateInfluencer con los mismos valores: no escribe", async () => {
    const { d1, raw, execLog } = makeDb();
    const info = raw
      .query("INSERT INTO influencers (tenant_id, name, platform, profile_url, language, permission, active, notes, created_at, game, permission_date, publish_platforms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
      .run("sebastian", "Rubius", "twitch", "https://twitch.tv/rubius", "es", 1, 1, "", 1000, "GTA VI", "2026-01-01", "[]");
    const id = Number(info.lastInsertRowid);
    execLog.length = 0;
    const row = await updateInfluencer(d1, id, "sebastian", { name: "Rubius", active: 1 });
    expect(row?.name).toBe("Rubius");
    expect(writes(execLog)).toBe(0);
    execLog.length = 0;
    await updateInfluencer(d1, id, "sebastian", { name: "Rubius2" });
    expect(writes(execLog)).toBe(1);
  });

  test("saveAnalyticsStatus con el mismo estado: no escribe", async () => {
    const { d1, raw, execLog } = makeDb();
    raw
      .query("INSERT INTO analytics_status (tenant_id, platform, status, reason, last_measured_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)")
      .run("sebastian", "youtube", "ok", "", null, 1000);
    execLog.length = 0;
    await saveAnalyticsStatus(d1, "sebastian", "youtube", "ok", "", null);
    expect(writes(execLog)).toBe(0);
    execLog.length = 0;
    await saveAnalyticsStatus(d1, "sebastian", "youtube", "error", "sin permiso", null);
    expect(writes(execLog)).toBe(1);
    expect(count(raw, "analytics_status")).toBe(1);
  });

  test("saveSettings con los mismos datos: no escribe", async () => {
    const { d1, raw, execLog } = makeDb();
    await saveSettings(d1, "sebastian", { a: 1 });
    expect(count(raw, "settings")).toBe(1);
    execLog.length = 0;
    await saveSettings(d1, "sebastian", { a: 1 });
    expect(writes(execLog)).toBe(0);
    execLog.length = 0;
    await saveSettings(d1, "sebastian", { a: 2 });
    expect(writes(execLog)).toBe(1);
  });

  test("updateClipPublishState con el mismo parche: no escribe", async () => {
    const { d1, raw, execLog } = makeDb();
    const states = {
      youtube_shorts: { state: "programado", scheduledFor: "2026-11-20T12:00:00.000Z", publishedAt: null, note: "En cola.", since: "2026-11-20T00:00:00.000Z", updatedAt: "2026-11-20T00:00:00.000Z" },
    };
    const id = seedClip(raw, { publish_states: JSON.stringify(states), updated_at: 1000 });
    execLog.length = 0;
    await updateClipPublishState(d1, id, "youtube_shorts", { state: "programado", scheduledFor: "2026-11-20T12:00:00.000Z", note: "En cola." }, "sebastian");
    expect(writes(execLog)).toBe(0);
    expect((raw.query("SELECT updated_at AS u FROM clips WHERE id = ?").get(id) as { u: number }).u).toBe(1000);
  });

  test("markJobDeleted dos veces: la segunda no escribe", async () => {
    const { d1, raw, execLog } = makeDb();
    const info = raw
      .query("INSERT INTO jobs (tenant_id, clip_id, platform, state, deleted_at, created_at) VALUES (?, ?, ?, 'posted', NULL, ?)")
      .run("sebastian", 102, "youtube_shorts", 1000);
    const jobId = Number(info.lastInsertRowid);
    expect(jobId).toBeGreaterThan(0);
    await markJobDeleted(d1, "sebastian", 102, "youtube_shorts", NOW);
    const first = (raw.query("SELECT deleted_at AS d FROM jobs WHERE id = ?").get(jobId) as { d: number }).d;
    expect(first).toBe(NOW);
    execLog.length = 0;
    await markJobDeleted(d1, "sebastian", 102, "youtube_shorts", NOW + MIN);
    expect(writes(execLog)).toBe(0);
    expect((raw.query("SELECT deleted_at AS d FROM jobs WHERE id = ?").get(jobId) as { d: number }).d).toBe(NOW);
  });

  test("createClipStat con la misma medición: devuelve la existente sin INSERT", async () => {
    const { d1, raw, execLog } = makeDb();
    const data = { platform: "youtube", views: 100, retention_pct: null, shares: null, saves: null, comments: 5, new_subs: null, window_label: "7d", recorded_at: NOW };
    const first = await createClipStat(d1, "sebastian", 102, data);
    expect(count(raw, "clip_stats")).toBe(1);
    execLog.length = 0;
    const second = await createClipStat(d1, "sebastian", 102, { ...data, recorded_at: NOW + MIN });
    expect(second.id).toBe(first.id);
    expect(writes(execLog)).toBe(0);
    expect(count(raw, "clip_stats")).toBe(1);
    execLog.length = 0;
    await createClipStat(d1, "sebastian", 102, { ...data, views: 150, recorded_at: NOW + 2 * MIN });
    expect(writes(execLog)).toBe(1);
    expect(count(raw, "clip_stats")).toBe(2);
  });
});

describe("batch: varias escrituras en una sola operación (A2)", () => {
  test("refreshAccountMaturity: 3 cuentas en UN batch", async () => {
    const { d1, raw, batchCalls, execLog } = makeDb();
    for (const platform of ["youtube", "tiktok", "meta"]) {
      raw
        .query("INSERT INTO api_accounts (tenant_id, platform, first_published_at, published_count, strikes, maturity, maturity_checked_day) VALUES (?, ?, NULL, 0, 0, 'nueva', '2000-01-01')")
        .run("sebastian", platform);
    }
    const today = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Santiago", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
    // Ya revisada hoy: no entra en el batch.
    raw
      .query("INSERT INTO api_accounts (tenant_id, platform, first_published_at, published_count, strikes, maturity, maturity_checked_day) VALUES (?, ?, NULL, 0, 0, 'nueva', ?)")
      .run("sebastian", "youtube", today);
    const r = await refreshAccountMaturity(d1, "sebastian", effectiveSettings({}));
    expect(r.checked).toBe(3);
    expect(batchCalls.n).toBe(1);
    expect(execLog.filter((e) => /^UPDATE api_accounts/i.test(e.sql) && e.changes > 0)).toHaveLength(3);
    const days = raw.query("SELECT maturity_checked_day AS d FROM api_accounts").all() as Array<{ d: string }>;
    expect(days.filter((x) => x.d === today)).toHaveLength(4);
  });

  test("createTelegramStopConfirmation: DELETE+INSERT en UN batch", async () => {
    const { d1, raw, batchCalls } = makeDb();
    await createTelegramStopConfirmation(d1, "111", "sebastian", NOW + 5 * MIN);
    await createTelegramStopConfirmation(d1, "111", "sebastian", NOW + 10 * MIN);
    expect(batchCalls.n).toBe(2);
    expect(count(raw, "telegram_stop_confirmations")).toBe(1);
    expect(
      (raw.query("SELECT expires_at AS e FROM telegram_stop_confirmations WHERE chat_id = ?").get("111") as { e: number }).e,
    ).toBe(NOW + 10 * MIN);
  });

  test("updateClipPublishStates: 2 plataformas en UN solo UPDATE", async () => {
    const { d1, raw, execLog } = makeDb();
    const id = seedClip(raw);
    execLog.length = 0;
    await updateClipPublishStates(
      d1,
      id,
      [
        { platform: "youtube_shorts", patch: { state: "programado", scheduledFor: "2026-11-20T12:00:00.000Z", note: "En cola." } },
        { platform: "tiktok", patch: { state: "programado", scheduledFor: "2026-11-20T12:00:00.000Z", note: "En cola." } },
      ],
      "sebastian",
    );
    const updates = execLog.filter((e) => /^UPDATE clips/i.test(e.sql) && e.changes > 0);
    expect(updates).toHaveLength(1);
    const states = JSON.parse(
      (raw.query("SELECT publish_states AS s FROM clips WHERE id = ?").get(id) as { s: string }).s,
    ) as Record<string, { state: string }>;
    expect(states.youtube_shorts?.state).toBe("programado");
    expect(states.tiktok?.state).toBe("programado");
  });

  test("moveQueueClip: materializar + intercambiar en UN batch", async () => {
    const { d1, raw, batchCalls, execLog } = makeDb();
    const a = seedClip(raw, { title: "a", score: 90, created_at: 1000 });
    const b = seedClip(raw, { title: "b", score: 80, created_at: 2000 });
    const c = seedClip(raw, { title: "c", score: 70, created_at: 3000 });
    const r = await moveQueueClip(d1, b, "sebastian", "up");
    expect(r.ok).toBe(true);
    expect(batchCalls.n).toBe(1);
    expect(execLog.filter((e) => /^UPDATE clips SET manual_rank/i.test(e.sql) && e.changes > 0)).toHaveLength(3);
    const ranks = raw.query("SELECT id, manual_rank AS m FROM clips ORDER BY manual_rank ASC").all() as Array<{ id: number; m: number }>;
    expect(ranks.map((x) => x.id)).toEqual([b, a, c]);
    // Segundo movimiento: solo los 2 intercambios, en otro batch.
    execLog.length = 0;
    await moveQueueClip(d1, b, "sebastian", "down");
    expect(batchCalls.n).toBe(2);
    expect(execLog.filter((e) => /^UPDATE clips SET manual_rank/i.test(e.sql) && e.changes > 0)).toHaveLength(2);
    const ranks2 = raw.query("SELECT id, manual_rank AS m FROM clips ORDER BY manual_rank ASC").all() as Array<{ id: number; m: number }>;
    expect(ranks2.map((x) => x.id)).toEqual([a, b, c]);
  });
});

describe("caducidad nocturna de eventos (A2)", () => {
  test("isPruneHour: solo las 03:00 Santiago", () => {
    expect(isPruneHour(Date.UTC(2026, 10, 20, 6, 0, 0))).toBe(true); // 03:00
    expect(isPruneHour(Date.UTC(2026, 10, 20, 6, 59, 0))).toBe(true); // 03:59
    expect(isPruneHour(Date.UTC(2026, 10, 20, 5, 59, 0))).toBe(false); // 02:59
    expect(isPruneHour(Date.UTC(2026, 10, 20, 7, 0, 0))).toBe(false); // 04:00
    expect(isPruneHour(Date.UTC(2026, 10, 20, 12, 0, 0))).toBe(false); // 09:00
  });

  test("pruneOldEvents borra lo de más de 14 días y deja lo nuevo", async () => {
    const { d1, raw } = makeDb();
    const oldAt = PRUNE_NOW - 15 * DAY;
    const newAt = PRUNE_NOW - DAY;
    for (let i = 0; i < 3; i++) {
      raw.query("INSERT INTO worker_events (tenant_id, at, kind, message) VALUES (?, ?, ?, ?)").run("sebastian", oldAt, "ciclo", `viejo ${i}`);
      raw.query("INSERT INTO clip_events (clip_id, tenant_id, at, step, actor, message, failed) VALUES (?, ?, ?, ?, ?, ?, 0)").run(100 + i, "sebastian", oldAt, "aviso", "worker", `viejo ${i}`);
      raw.query("INSERT INTO notifications (tenant_id, kind, clip_id, message, created_at) VALUES (?, ?, NULL, ?, ?)").run("sebastian", "info", `viejo ${i}`, oldAt);
    }
    raw.query("INSERT INTO worker_events (tenant_id, at, kind, message) VALUES (?, ?, ?, ?)").run("sebastian", newAt, "ciclo", "nuevo");
    raw.query("INSERT INTO clip_events (clip_id, tenant_id, at, step, actor, message, failed) VALUES (?, ?, ?, ?, ?, ?, 0)").run(200, "sebastian", newAt, "aviso", "worker", "nuevo");
    raw.query("INSERT INTO event_counters (tenant_id, source, kind, message, window_start, count, first_at, last_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)").run("sebastian", "worker_events", "ciclo", "viejo", oldAt, oldAt, oldAt);
    // El otro tenant no se toca.
    raw.query("INSERT INTO worker_events (tenant_id, at, kind, message) VALUES (?, ?, ?, ?)").run("kaez", oldAt, "ciclo", "viejo kaez");
    const r = await pruneOldEvents(d1, "sebastian", PRUNE_NOW);
    expect(r.ran).toBe(true);
    expect(r.deleted.worker_events).toBe(3);
    expect(r.deleted.clip_events).toBe(3);
    expect(r.deleted.notifications).toBe(3);
    expect(r.deleted.event_counters).toBe(1);
    expect(count(raw, "worker_events")).toBe(2); // nuevo sebastian + viejo kaez
    expect((raw.query("SELECT message AS m FROM worker_events WHERE tenant_id = 'sebastian'").get() as { m: string }).m).toBe("nuevo");
  });

  test("el límite de 14 días es estricto: justo en el corte se queda", async () => {
    const { d1, raw } = makeDb();
    const cutoff = PRUNE_NOW - 14 * DAY;
    raw.query("INSERT INTO worker_events (tenant_id, at, kind, message) VALUES (?, ?, ?, ?)").run("sebastian", cutoff, "ciclo", "justo en el corte");
    raw.query("INSERT INTO worker_events (tenant_id, at, kind, message) VALUES (?, ?, ?, ?)").run("sebastian", cutoff - 1, "ciclo", "un ms más viejo");
    const r = await pruneOldEvents(d1, "sebastian", PRUNE_NOW);
    expect(r.deleted.worker_events).toBe(1);
    expect((raw.query("SELECT message AS m FROM worker_events").get() as { m: string }).m).toBe("justo en el corte");
  });

  test("fuera de la hora de caducidad no corre", async () => {
    const { d1, raw } = makeDb();
    raw.query("INSERT INTO worker_events (tenant_id, at, kind, message) VALUES (?, ?, ?, ?)").run("sebastian", PRUNE_NOW - 15 * DAY, "ciclo", "viejo");
    const r = await pruneOldEvents(d1, "sebastian", Date.UTC(2026, 10, 20, 12, 0, 0));
    expect(r.ran).toBe(false);
    expect(r.reason).toBe("not_prune_hour");
    expect(count(raw, "worker_events")).toBe(1);
  });

  test("hito del 70%: con 70.000 escrituras hoy no corre", async () => {
    const { d1, raw } = makeDb();
    // 70.000 filas de hoy en un solo INSERT (CTE recursiva, local).
    raw.exec(
      `INSERT INTO worker_events (tenant_id, at, kind, message) ` +
        `WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM c WHERE n < 70000) ` +
        `SELECT 'sebastian', ${PRUNE_NOW}, 'x', 'm' FROM c`,
    );
    expect(await estimateTodayWrites(d1, "sebastian", PRUNE_NOW)).toBe(70000);
    expect(EVENT_PRUNE_WRITE_GATE).toBe(Math.floor(D1_DAILY_ACCOUNT_LIMIT * 0.7));
    raw.query("INSERT INTO worker_events (tenant_id, at, kind, message) VALUES (?, ?, ?, ?)").run("sebastian", PRUNE_NOW - 15 * DAY, "ciclo", "viejo");
    const r = await pruneOldEvents(d1, "sebastian", PRUNE_NOW);
    expect(r.ran).toBe(false);
    expect(r.reason).toBe("over_budget_gate");
    expect(r.estimatedWritesToday).toBe(70000);
    expect(count(raw, "worker_events")).toBe(70001);
  });

  test("tope por noche: 5.600 viejos → borra 5.000 en 10 lotes de 500", async () => {
    const { d1, raw, execLog } = makeDb();
    raw.exec(
      `INSERT INTO worker_events (tenant_id, at, kind, message) ` +
        `WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM c WHERE n < 5600) ` +
        `SELECT 'sebastian', ${PRUNE_NOW - 15 * DAY}, 'x', 'm' FROM c`,
    );
    const r = await pruneOldEvents(d1, "sebastian", PRUNE_NOW);
    expect(r.ran).toBe(true);
    expect(r.deleted.worker_events).toBe(EVENT_PRUNE_MAX_ROWS_PER_NIGHT);
    expect(count(raw, "worker_events")).toBe(600);
    const deletes = execLog.filter((e) => /^DELETE FROM worker_events/i.test(e.sql));
    expect(deletes).toHaveLength(10);
    expect(deletes.every((e) => e.changes === EVENT_PRUNE_BATCH_SIZE)).toBe(true);
  });
});

describe("migraciones 0034 y 0036 (sin aplicar, solo sintaxis)", () => {
  test("0034 crea event_counters + índices de dedupe de notifications", async () => {
    const { raw } = makeDb();
    raw.exec("DROP TABLE event_counters");
    const sql = await Bun.file(new URL("../../migrations/0034_event_counters.sql", import.meta.url)).text();
    raw.exec(sql);
    const tables = raw.query("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'event_counters'").all();
    expect(tables).toHaveLength(1);
    const idx = raw.query("SELECT name FROM sqlite_master WHERE type = 'index' AND name IN ('idx_event_counters_window', 'idx_notifications_dedupe')").all();
    expect(idx).toHaveLength(2);
  });

  test("0036 quita los 5 índices sin uso y nada más", async () => {
    const { raw } = makeDb();
    for (const name of ["jobs_posted_day", "notifications_unread", "worker_events_tenant", "clips_publish_blocked", "clip_previews_tenant"]) {
      raw.exec(`CREATE INDEX ${name} ON worker_events(tenant_id)`);
    }
    raw.exec("CREATE INDEX idx_worker_events_dedupe ON worker_events(tenant_id, kind, message, at)");
    const sql = await Bun.file(new URL("../../migrations/0036_drop_unused_indexes.sql", import.meta.url)).text();
    raw.exec(sql);
    const gone = raw.query("SELECT name FROM sqlite_master WHERE type = 'index' AND name IN ('jobs_posted_day', 'notifications_unread', 'worker_events_tenant', 'clips_publish_blocked', 'clip_previews_tenant')").all();
    expect(gone).toHaveLength(0);
    const kept = raw.query("SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_worker_events_dedupe'").all();
    expect(kept).toHaveLength(1);
  });
});
