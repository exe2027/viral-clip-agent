// Ayuda compartida para los tests de presupuesto (A1/A6):
// D1 falso mínimo sobre SQLite real (bun:sqlite), mismo patrón
// que event-dedupe.test.ts. Solo las tablas que tocan el
// contador, el monitor y el informe diario. Sin red, sin D1 real.
import { Database } from "bun:sqlite";
import type { D1Database } from "../src/cloudflare";

export class SqliteStmt {
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

export function makeBudgetDb(): { d1: D1Database; raw: Database } {
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
    "CREATE TABLE telegram_links (" +
      "chat_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, " +
      "linked_at INTEGER NOT NULL, last_sent_day TEXT)",
  );
  raw.exec(
    "CREATE TABLE jobs (" +
      "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, " +
      "kind TEXT NOT NULL DEFAULT 'publish', clip_id INTEGER, " +
      "platform TEXT, state TEXT NOT NULL, posted_day TEXT, " +
      "updated_at INTEGER NOT NULL, created_at INTEGER NOT NULL DEFAULT 0)",
  );
  const d1 = { prepare: (sql: string) => new SqliteStmt(raw, sql) } as unknown as D1Database;
  return { d1, raw };
}

export function countRows(raw: Database, table: string): number {
  return (raw.query(`SELECT COUNT(*) AS n FROM ${table}`).get() as { n: number }).n;
}
