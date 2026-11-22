// Tests del orden LIVE-antes-que-VOD de la Cola (diseño §4).
// Contra SQLite REAL (bun:sqlite, mismo motor que D1), no contra el
// mock FakeD1: así se verifica el ORDER BY con CASE y el LEFT JOIN
// de verdad. Sin red, sin D1 de producción (la base es :memory: y
// se crea en cada test).
import { describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { listQueueClips, moveQueueClip } from "../src/db";
import { dueJobs } from "../src/jobs";
import type { D1Database } from "../src/cloudflare";

// --- Shim mínimo D1Database sobre bun:sqlite ---
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

function seedClip(db: Database, row: { tenant: string; title: string; score: number; status?: string; archived?: number; origin?: string; created: number }): number {
  const info = db
    .query("INSERT INTO clips (tenant_id, title, score, status, archived, origin, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)")
    .run(row.tenant, row.title, row.score, row.status ?? "en_carpeta", row.archived ?? 0, row.origin ?? "vod", row.created);
  return Number(info.lastInsertRowid);
}

function seedJob(db: Database, row: { tenant: string; clipId: number | null; state?: string; nextRunAt?: number | null; created: number }): number {
  const info = db
    .query("INSERT INTO jobs (tenant_id, kind, clip_id, state, next_run_at, created_at) VALUES (?, 'publish', ?, ?, ?, ?)")
    .run(row.tenant, row.clipId, row.state ?? "scheduled", row.nextRunAt ?? null, row.created);
  return Number(info.lastInsertRowid);
}

// La base :memory: vive detrás del shim; para sembrar se usa el
// mismo objeto Database real que el shim envuelve.
function makeDbPair(): { d1: D1Database; raw: Database } {
  const raw = new Database(":memory:");
  raw.exec(
    "CREATE TABLE clips (" +
      "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, " +
      "title TEXT NOT NULL DEFAULT '', score INTEGER NOT NULL DEFAULT 0, " +
      "status TEXT NOT NULL DEFAULT 'en_carpeta', archived INTEGER NOT NULL DEFAULT 0, " +
      "origin TEXT NOT NULL DEFAULT 'vod', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL DEFAULT 0, manual_rank INTEGER)",
  );
  raw.exec(
    "CREATE TABLE jobs (" +
      "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, " +
      "kind TEXT NOT NULL DEFAULT 'publish', clip_id INTEGER, platform TEXT, " +
      "state TEXT NOT NULL DEFAULT 'scheduled', next_run_at INTEGER, " +
      "created_at INTEGER NOT NULL)",
  );
  const d1 = {
    prepare: (sql: string) => new SqliteStmt(raw, sql),
    // A2: batch ejecuta las sentencias en orden, como la D1 real
    // (moveQueueClip agrupa los cambios de orden en un batch).
    batch: async (stmts: SqliteStmt[]) => {
      const out = [];
      for (const s of stmts) out.push(await s.run());
      return out;
    },
  } as unknown as D1Database;
  return { d1, raw };
}

describe("listQueueClips — LIVE arriba, VOD abajo, por puntos", () => {
  test("un live flojo va antes que un VOD con más puntos", async () => {
    const { d1, raw } = makeDbPair();
    const vod95 = seedClip(raw, { tenant: "sebastian", title: "vod95", score: 95, created: 1000 });
    const live70 = seedClip(raw, { tenant: "sebastian", title: "live70", score: 70, origin: "live", created: 3000 });
    const live90 = seedClip(raw, { tenant: "sebastian", title: "live90", score: 90, origin: "live", created: 2000 });
    const vod60 = seedClip(raw, { tenant: "sebastian", title: "vod60", score: 60, created: 4000 });
    const rows = await listQueueClips(d1, "sebastian");
    expect(rows.map((r) => r.id)).toEqual([live90, live70, vod95, vod60]);
    expect(rows.map((r) => r.origin)).toEqual(["live", "live", "vod", "vod"]);
  });

  test("dentro del segmento manda el puntaje; a igual puntaje, el más nuevo", async () => {
    const { d1, raw } = makeDbPair();
    const a = seedClip(raw, { tenant: "sebastian", title: "live-viejo", score: 80, origin: "live", created: 1000 });
    const b = seedClip(raw, { tenant: "sebastian", title: "live-nuevo", score: 80, origin: "live", created: 2000 });
    const rows = await listQueueClips(d1, "sebastian");
    expect(rows.map((r) => r.id)).toEqual([b, a]);
  });

  test("archivados, publicados y de otro tenant no aparecen; origin raro cae en VOD", async () => {
    const { d1, raw } = makeDbPair();
    seedClip(raw, { tenant: "sebastian", title: "archivado", score: 99, origin: "live", archived: 1, created: 1000 });
    seedClip(raw, { tenant: "sebastian", title: "publicado", score: 100, origin: "live", status: "publicado", created: 1000 });
    seedClip(raw, { tenant: "kaez", title: "otro-tenant", score: 100, origin: "live", created: 1000 });
    const raro = seedClip(raw, { tenant: "sebastian", title: "raro", score: 99, origin: "desconocido", created: 1000 });
    const vod50 = seedClip(raw, { tenant: "sebastian", title: "vod50", score: 50, created: 2000 });
    const rows = await listQueueClips(d1, "sebastian");
    expect(rows.map((r) => r.id)).toEqual([raro, vod50]);
  });
});

describe("dueJobs — el worker vacía LIVE primero", () => {
  test("job live creado después va antes que jobs VOD más viejos; sin clip cuenta como VOD", async () => {
    const { d1, raw } = makeDbPair();
    const liveClip = seedClip(raw, { tenant: "sebastian", title: "live", score: 70, origin: "live", created: 1000 });
    const vodClip = seedClip(raw, { tenant: "sebastian", title: "vod", score: 95, created: 1000 });
    const vodJob = seedJob(raw, { tenant: "sebastian", clipId: vodClip, created: 1000 });
    const noClipJob = seedJob(raw, { tenant: "sebastian", clipId: null, created: 2000 });
    const liveJob = seedJob(raw, { tenant: "sebastian", clipId: liveClip, created: 5000 });
    const rows = await dueJobs(d1, "sebastian", 10);
    expect(rows.map((r) => r.id)).toEqual([liveJob, vodJob, noClipJob]);
  });

  test("el límite corta tras el segmento LIVE: limit 1 devuelve solo el live", async () => {
    const { d1, raw } = makeDbPair();
    const liveClip = seedClip(raw, { tenant: "sebastian", title: "live", score: 70, origin: "live", created: 1000 });
    const vodClip = seedClip(raw, { tenant: "sebastian", title: "vod", score: 95, created: 1000 });
    seedJob(raw, { tenant: "sebastian", clipId: vodClip, created: 1000 });
    const liveJob = seedJob(raw, { tenant: "sebastian", clipId: liveClip, created: 5000 });
    const rows = await dueJobs(d1, "sebastian", 1);
    expect(rows.map((r) => r.id)).toEqual([liveJob]);
  });

  test("vencidos/fallidos/futuros y de otro tenant no salen", async () => {
    const { d1, raw } = makeDbPair();
    const liveClip = seedClip(raw, { tenant: "sebastian", title: "live", score: 70, origin: "live", created: 1000 });
    seedJob(raw, { tenant: "sebastian", clipId: liveClip, state: "failed", created: 1000 });
    seedJob(raw, { tenant: "sebastian", clipId: liveClip, nextRunAt: Date.now() + 3600_000, created: 1000 });
    seedJob(raw, { tenant: "kaez", clipId: liveClip, created: 1000 });
    const ok = seedJob(raw, { tenant: "sebastian", clipId: liveClip, created: 2000 });
    const rows = await dueJobs(d1, "sebastian", 10);
    expect(rows.map((r) => r.id)).toEqual([ok]);
  });
});

describe("orden manual (migración 0024, moveQueueClip)", () => {
  test("un VOD manual sube por encima de los VOD automáticos pero nunca por encima de un LIVE", async () => {
    const { d1, raw } = makeDbPair();
    const live = seedClip(raw, { tenant: "sebastian", title: "live", score: 10, origin: "live", created: 1000 });
    const vodHigh = seedClip(raw, { tenant: "sebastian", title: "vod-alto", score: 95, created: 2000 });
    const vodLow = seedClip(raw, { tenant: "sebastian", title: "vod-bajo", score: 20, created: 3000 });
    const moved = await moveQueueClip(d1, vodLow, "sebastian", "first");
    expect(moved.ok).toBe(true);
    expect(moved.position).toBe(1);
    const rows = await listQueueClips(d1, "sebastian");
    // LIVE primero; dentro de VOD, el manual (#vodLow) antes que el de score 95.
    expect(rows.map((r) => r.id)).toEqual([live, vodLow, vodHigh]);
    // El score no se tocó: la fórmula del cerebro sigue intacta.
    expect(rows.find((r) => r.id === vodLow)?.score).toBe(20);
  });

  test("/subir y /bajar intercambian un puesto; /auto devuelve al orden por score", async () => {
    const { d1, raw } = makeDbPair();
    const a = seedClip(raw, { tenant: "sebastian", title: "a", score: 90, created: 1000 });
    const b = seedClip(raw, { tenant: "sebastian", title: "b", score: 80, created: 2000 });
    const c = seedClip(raw, { tenant: "sebastian", title: "c", score: 70, created: 3000 });
    expect((await listQueueClips(d1, "sebastian")).map((r) => r.id)).toEqual([a, b, c]);
    const up = await moveQueueClip(d1, c, "sebastian", "up");
    expect(up.ok).toBe(true);
    expect(up.position).toBe(2);
    expect((await listQueueClips(d1, "sebastian")).map((r) => r.id)).toEqual([a, c, b]);
    const down = await moveQueueClip(d1, c, "sebastian", "down");
    expect(down.position).toBe(3);
    expect((await listQueueClips(d1, "sebastian")).map((r) => r.id)).toEqual([a, b, c]);
    // Subir el primero no hace nada malo; auto limpia el manual_rank.
    const top = await moveQueueClip(d1, a, "sebastian", "up");
    expect(top.ok).toBe(true);
    expect(top.position).toBe(1);
    await moveQueueClip(d1, c, "sebastian", "first");
    expect((await listQueueClips(d1, "sebastian")).map((r) => r.id)).toEqual([c, a, b]);
    const auto = await moveQueueClip(d1, c, "sebastian", "auto");
    expect(auto.ok).toBe(true);
    expect(auto.manual).toBe(false);
    expect((await listQueueClips(d1, "sebastian")).map((r) => r.id)).toEqual([a, b, c]);
  });

  test("mover un clip de otro tenant o fuera de la Cola no cambia nada", async () => {
    const { d1, raw } = makeDbPair();
    const mine = seedClip(raw, { tenant: "sebastian", title: "mío", score: 80, created: 1000 });
    const other = seedClip(raw, { tenant: "kaez", title: "de kaez", score: 99, created: 1000 });
    seedClip(raw, { tenant: "sebastian", title: "archivado", score: 100, archived: 1, created: 1000 });
    const r1 = await moveQueueClip(d1, other, "sebastian", "first");
    expect(r1.ok).toBe(false);
    const r2 = await moveQueueClip(d1, 9999, "sebastian", "first");
    expect(r2.ok).toBe(false);
    expect((await listQueueClips(d1, "sebastian")).map((r) => r.id)).toEqual([mine]);
    const otherRow = raw.query("SELECT manual_rank FROM clips WHERE id = ?").get(other) as { manual_rank: number | null };
    expect(otherRow.manual_rank).toBeNull();
  });
});
