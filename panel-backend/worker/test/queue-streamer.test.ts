// Tests de la columna Streamer en la Cola (2026-11-19).
// listQueueClips resuelve clips.influencer_id -> influencers.name
// y lo devuelve como `streamer`; sin tabla influencers (bases de
// test mínimas) la Cola sigue cargando con streamer null.
// Contra SQLite REAL (bun:sqlite, mismo motor que D1).
import { describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { listQueueClips } from "../src/db";
import type { D1Database } from "../src/cloudflare";

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

function makeDbPair(withInfluencers: boolean): { d1: D1Database; raw: Database } {
  const raw = new Database(":memory:");
  raw.exec(
    "CREATE TABLE clips (" +
      "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, " +
      "title TEXT NOT NULL DEFAULT '', score INTEGER NOT NULL DEFAULT 0, " +
      "status TEXT NOT NULL DEFAULT 'en_carpeta', archived INTEGER NOT NULL DEFAULT 0, " +
      "origin TEXT NOT NULL DEFAULT 'vod', influencer_id INTEGER, " +
      "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL DEFAULT 0, manual_rank INTEGER)",
  );
  if (withInfluencers) {
    raw.exec(
      "CREATE TABLE influencers (" +
        "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, " +
        "tenant_id TEXT NOT NULL DEFAULT 'sebastian')",
    );
  }
  const d1 = { prepare: (sql: string) => new SqliteStmt(raw, sql) } as unknown as D1Database;
  return { d1, raw };
}

function seedClip(raw: Database, row: { tenant: string; title: string; score: number; origin?: string; influencerId?: number | null; created: number }): number {
  const info = raw
    .query("INSERT INTO clips (tenant_id, title, score, origin, influencer_id, created_at) VALUES (?, ?, ?, ?, ?, ?)")
    .run(row.tenant, row.title, row.score, row.origin ?? "vod", row.influencerId ?? null, row.created);
  return Number(info.lastInsertRowid);
}

function seedInfluencer(raw: Database, row: { id: number; name: string; tenant: string }): void {
  raw
    .query("INSERT INTO influencers (id, name, tenant_id) VALUES (?, ?, ?)")
    .run(row.id, row.name, row.tenant);
}

describe("listQueueClips — columna Streamer", () => {
  test("resuelve el nombre por influencer_id; sin influencer o desconocido queda null", async () => {
    const { d1, raw } = makeDbPair(true);
    seedInfluencer(raw, { id: 1, name: "IlloJuan", tenant: "sebastian" });
    seedInfluencer(raw, { id: 2, name: "Rubius", tenant: "sebastian" });
    seedInfluencer(raw, { id: 9, name: "OtroTenant", tenant: "kaez" });
    const withInf = seedClip(raw, { tenant: "sebastian", title: "con", score: 60, influencerId: 1, created: 1000 });
    const unknown = seedClip(raw, { tenant: "sebastian", title: "desconocido", score: 55, influencerId: 99, created: 2000 });
    const noInf = seedClip(raw, { tenant: "sebastian", title: "sin", score: 50, influencerId: null, created: 3000 });
    const rows = await listQueueClips(d1, "sebastian");
    const byId = new Map(rows.map((r) => [r.id, r]));
    expect(byId.get(withInf)?.streamer).toBe("IlloJuan");
    expect(byId.get(unknown)?.streamer).toBeNull();
    expect(byId.get(noInf)?.streamer).toBeNull();
  });

  test("el streamer es por tenant: no se cuela el influencer de otro tenant", async () => {
    const { d1, raw } = makeDbPair(true);
    seedInfluencer(raw, { id: 9, name: "OtroTenant", tenant: "kaez" });
    const clip = seedClip(raw, { tenant: "sebastian", title: "x", score: 60, influencerId: 9, created: 1000 });
    const rows = await listQueueClips(d1, "sebastian");
    expect(rows.find((r) => r.id === clip)?.streamer).toBeNull();
  });

  test("sin tabla influencers la Cola sigue cargando con streamer null", async () => {
    const { d1, raw } = makeDbPair(false);
    const clip = seedClip(raw, { tenant: "sebastian", title: "x", score: 60, influencerId: 1, created: 1000 });
    const rows = await listQueueClips(d1, "sebastian");
    expect(rows.map((r) => r.id)).toEqual([clip]);
    expect(rows[0]?.streamer).toBeNull();
  });

  test("el orden LIVE-antes-que-VOD no cambia con streamer", async () => {
    const { d1, raw } = makeDbPair(true);
    seedInfluencer(raw, { id: 1, name: "IlloJuan", tenant: "sebastian" });
    const vod = seedClip(raw, { tenant: "sebastian", title: "vod", score: 95, influencerId: 1, created: 1000 });
    const live = seedClip(raw, { tenant: "sebastian", title: "live", score: 70, origin: "live", influencerId: 1, created: 2000 });
    const rows = await listQueueClips(d1, "sebastian");
    expect(rows.map((r) => r.id)).toEqual([live, vod]);
    expect(rows[0]?.streamer).toBe("IlloJuan");
  });
});
