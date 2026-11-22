// Tests de la dedupe de eventos + throttle de dispatch por lectura
// (2026-11-20, cuota D1: 100.000 filas/día por cuenta).
//
// El ciclo automático reevaluaba cada minuto los mismos clips frenados
// y dejaba ~11.000 filas/día de auto_publish idénticas (212 mensajes
// distintos de 10.975 en 24 h). logWorkerEvent/logClipEvent ya no
// escriben un evento idéntico dentro de la ventana de 6 h: se guarda
// la primera ocurrencia y un recordatorio cada 6 h. hasRecentBrainToken
// es el throttle de dispatch que sobrevive a un fallo de escrituras
// (lectura de brain_tokens, no depende de que el UPDATE de
// last_live_dispatch_at persista). Contra SQLite REAL (bun:sqlite).
import { describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { hasRecentBrainToken } from "../src/brain";
import {
  EVENT_DEDUPE_WINDOW_MS,
  logClipEvent,
  logWorkerEvent,
} from "../src/db";
import type { D1Database } from "../src/cloudflare";

const NOW = Date.now();
const MIN = 60_000;
const HOUR = 60 * MIN;

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
    "CREATE TABLE brain_tokens (" +
      "token_hash TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, " +
      "expires_at INTEGER NOT NULL, used_at INTEGER, " +
      "created_at INTEGER NOT NULL)",
  );
  const d1 = { prepare: (sql: string) => new SqliteStmt(raw, sql) } as unknown as D1Database;
  return { d1, raw };
}

function count(raw: Database, table: string): number {
  return (raw.query(`SELECT COUNT(*) AS n FROM ${table}`).get() as { n: number }).n;
}

describe("dedupe de worker_events (cuota D1)", () => {
  test("el mismo freno repetido cada minuto solo se escribe una vez", async () => {
    const { d1, raw } = makeDb();
    const msg = "Clip #102 frenado en tiktok: sin subtítulos completos: necesita transcripción humana.";
    for (let i = 0; i < 60; i++) {
      await logWorkerEvent(d1, "auto_publish", msg, "sebastian");
    }
    expect(count(raw, "worker_events")).toBe(1);
  });

  test("un motivo distinto sí se escribe (cambia el estado)", async () => {
    const { d1, raw } = makeDb();
    await logWorkerEvent(d1, "auto_publish", "Clip #102 frenado en tiktok: sin subtítulos completos.", "sebastian");
    await logWorkerEvent(d1, "auto_publish", "Clip #102 frenado en tiktok: tope diario alcanzado.", "sebastian");
    await logWorkerEvent(d1, "auto_publish", "Clip #102 frenado en youtube_shorts: sin subtítulos completos.", "sebastian");
    expect(count(raw, "worker_events")).toBe(3);
  });

  test("pasada la ventana, el mismo freno vuelve a escribirse (recordatorio)", async () => {
    const { d1, raw } = makeDb();
    const msg = "Clip #102 frenado en tiktok: sin subtítulos completos.";
    raw
      .query("INSERT INTO worker_events (tenant_id, at, kind, message) VALUES (?, ?, ?, ?)")
      .run("sebastian", NOW - EVENT_DEDUPE_WINDOW_MS - MIN, "auto_publish", msg);
    await logWorkerEvent(d1, "auto_publish", msg, "sebastian");
    expect(count(raw, "worker_events")).toBe(2);
  });

  test("dentro de la ventana, el mismo freno no se escribe", async () => {
    const { d1, raw } = makeDb();
    const msg = "Clip #102 frenado en tiktok: sin subtítulos completos.";
    raw
      .query("INSERT INTO worker_events (tenant_id, at, kind, message) VALUES (?, ?, ?, ?)")
      .run("sebastian", NOW - 5 * MIN, "auto_publish", msg);
    await logWorkerEvent(d1, "auto_publish", msg, "sebastian");
    expect(count(raw, "worker_events")).toBe(1);
  });

  test("otro tenant u otro kind no comparten la dedupe", async () => {
    const { d1, raw } = makeDb();
    const msg = "Clip #102 frenado en tiktok: sin subtítulos completos.";
    await logWorkerEvent(d1, "auto_publish", msg, "sebastian");
    await logWorkerEvent(d1, "auto_publish", msg, "kaez");
    await logWorkerEvent(d1, "ciclo", msg, "sebastian");
    expect(count(raw, "worker_events")).toBe(3);
  });
});

describe("dedupe de clip_events (cuota D1)", () => {
  test("el mismo freno de clip repetido solo se escribe una vez", async () => {
    const { d1, raw } = makeDb();
    const msg = "Publicación frenada en tiktok: sin subtítulos completos.";
    for (let i = 0; i < 10; i++) {
      await logClipEvent(d1, 102, "publicado", "worker", msg, true, "sebastian");
    }
    expect(count(raw, "clip_events")).toBe(1);
  });

  test("otro clip u otro step sí se escriben", async () => {
    const { d1, raw } = makeDb();
    const msg = "Publicación frenada en tiktok: sin subtítulos completos.";
    await logClipEvent(d1, 102, "publicado", "worker", msg, true, "sebastian");
    await logClipEvent(d1, 103, "publicado", "worker", msg, true, "sebastian");
    await logClipEvent(d1, 102, "aviso", "worker", msg, true, "sebastian");
    expect(count(raw, "clip_events")).toBe(3);
  });

  test("pasada la ventana, el mismo freno de clip vuelve a escribirse", async () => {
    const { d1, raw } = makeDb();
    const msg = "Publicación frenada en tiktok: sin subtítulos completos.";
    raw
      .query("INSERT INTO clip_events (clip_id, tenant_id, at, step, actor, message, failed) VALUES (?, ?, ?, ?, ?, ?, ?)")
      .run(102, "sebastian", NOW - EVENT_DEDUPE_WINDOW_MS - MIN, "publicado", "worker", msg, 1);
    await logClipEvent(d1, 102, "publicado", "worker", msg, true, "sebastian");
    expect(count(raw, "clip_events")).toBe(2);
  });
});

describe("hasRecentBrainToken — throttle de dispatch por lectura", () => {
  const WINDOW = 15 * MIN;

  test("sin tokens, no hay dispatch reciente", async () => {
    const { d1 } = makeDb();
    expect(await hasRecentBrainToken(d1, "sebastian", WINDOW, NOW)).toBe(false);
  });

  test("token de hace 5 min: reciente; de hace 20 min: ya no", async () => {
    const { d1, raw } = makeDb();
    raw
      .query("INSERT INTO brain_tokens (token_hash, tenant_id, expires_at, used_at, created_at) VALUES (?, ?, ?, NULL, ?)")
      .run("hash-a", "sebastian", NOW + 30 * MIN, NOW - 5 * MIN);
    expect(await hasRecentBrainToken(d1, "sebastian", WINDOW, NOW)).toBe(true);
    const { d1: d1b, raw: rawb } = makeDb();
    rawb
      .query("INSERT INTO brain_tokens (token_hash, tenant_id, expires_at, used_at, created_at) VALUES (?, ?, ?, NULL, ?)")
      .run("hash-b", "sebastian", NOW + 30 * MIN, NOW - 20 * MIN);
    expect(await hasRecentBrainToken(d1b, "sebastian", WINDOW, NOW)).toBe(false);
  });

  test("el token de otro tenant no frena este dispatch", async () => {
    const { d1, raw } = makeDb();
    raw
      .query("INSERT INTO brain_tokens (token_hash, tenant_id, expires_at, used_at, created_at) VALUES (?, ?, ?, NULL, ?)")
      .run("hash-c", "kaez", NOW + 30 * MIN, NOW - MIN);
    expect(await hasRecentBrainToken(d1, "sebastian", WINDOW, NOW)).toBe(false);
  });
});
