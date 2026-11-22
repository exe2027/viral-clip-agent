// Tests del anti-spam del aviso "hay una corrida en curso" (2026-11-19).
// El Worker corre cada minuto: sin throttle, una corrida larga dejaba
// decenas de avisos idénticos. hasRecentWorkerEvent decide si el último
// aviso igual es reciente. Contra SQLite REAL (bun:sqlite).
import { describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { hasRecentWorkerEvent } from "../src/db";
import type { D1Database } from "../src/cloudflare";

const NOW = Date.parse("2026-11-19T12:00:00Z");
const MIN = 60_000;

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
  const d1 = { prepare: (sql: string) => new SqliteStmt(raw, sql) } as unknown as D1Database;
  return { d1, raw };
}

function seed(raw: Database, row: { tenant: string; at: number; kind: string; message: string }): void {
  raw
    .query("INSERT INTO worker_events (tenant_id, at, kind, message) VALUES (?, ?, ?, ?)")
    .run(row.tenant, row.at, row.kind, row.message);
}

describe("hasRecentWorkerEvent — throttle del aviso de corrida en curso", () => {
  test("sin eventos, no hay aviso reciente", async () => {
    const { d1 } = makeDb();
    expect(await hasRecentWorkerEvent(d1, "sebastian", "live_dispatch", "Hay una corrida", 15 * MIN, NOW)).toBe(false);
  });

  test("aviso igual hace 5 min: reciente; hace 20 min: ya no", async () => {
    const { d1, raw } = makeDb();
    seed(raw, { tenant: "sebastian", at: NOW - 5 * MIN, kind: "live_dispatch", message: "Hay una corrida de cerebro-live en curso o en cola: no se dispara panel-live-sebastian este ciclo (reintento en el siguiente)." });
    expect(await hasRecentWorkerEvent(d1, "sebastian", "live_dispatch", "Hay una corrida", 15 * MIN, NOW)).toBe(true);
    const { d1: d1b, raw: rawb } = makeDb();
    seed(rawb, { tenant: "sebastian", at: NOW - 20 * MIN, kind: "live_dispatch", message: "Hay una corrida de cerebro-live en curso o en cola: no se dispara panel-live-sebastian este ciclo (reintento en el siguiente)." });
    expect(await hasRecentWorkerEvent(d1b, "sebastian", "live_dispatch", "Hay una corrida", 15 * MIN, NOW)).toBe(false);
  });

  test("otro kind, otro prefijo u otro tenant no cuentan como aviso reciente", async () => {
    const { d1, raw } = makeDb();
    seed(raw, { tenant: "sebastian", at: NOW - MIN, kind: "ciclo", message: "Hay una corrida de cerebro-live en curso o en cola." });
    seed(raw, { tenant: "sebastian", at: NOW - MIN, kind: "live_dispatch", message: "Dispatch enviado a GitHub." });
    seed(raw, { tenant: "kaez", at: NOW - MIN, kind: "live_dispatch", message: "Hay una corrida de cerebro-live en curso o en cola." });
    expect(await hasRecentWorkerEvent(d1, "sebastian", "live_dispatch", "Hay una corrida", 15 * MIN, NOW)).toBe(false);
  });
});
