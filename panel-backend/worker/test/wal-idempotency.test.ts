// Tests B1 (diario WAL en R2 + volcado) y B2 (idempotencia y
// comprobación en plataforma). Sin red real, sin D1 real: R2 falso
// en memoria, D1 sobre SQLite real (bun:sqlite) o stub que
// devuelve 7500. Nada sube a ninguna plataforma.
import { describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import type { Env } from "../src/cloudflare";
import type { D1Database } from "../src/cloudflare";
import { santiagoDayKey } from "../src/gate";
import {
  appendWalEntry,
  flushWalDay,
  isD1QuotaError,
  readWalEntries,
  walFindPosted,
  walKey,
  type WalEntry,
} from "../src/wal";
import {
  checkDuplicateBeforeUpload,
  checkFacebookAlreadyPublished,
  checkInstagramAlreadyPublished,
  checkTiktokAlreadyPublished,
  checkYoutubeAlreadyPublished,
  clipMarker,
  containsClipMarker,
  type FetchJson,
} from "../src/publish/idempotency";
import type { JobRow } from "../src/jobs";

const TENANT = "sebastian" as const;
const DAY = santiagoDayKey();

// --- R2 falso en memoria ---
class FakeR2 {
  store = new Map<string, string>();
  puts = 0;
  failPut = false;
  async put(key: string, value: string | ArrayBuffer | ReadableStream): Promise<{ key: string; size: number }> {
    this.puts++;
    if (this.failPut) throw new Error("R2 caído (test)");
    const text = typeof value === "string" ? value : new TextDecoder().decode(value as ArrayBuffer);
    this.store.set(key, text);
    return { key, size: text.length };
  }
  async get(key: string): Promise<{ arrayBuffer: () => Promise<ArrayBuffer>; size: number; body: ReadableStream } | null> {
    const text = this.store.get(key);
    if (text === undefined) return null;
    const bytes = new TextEncoder().encode(text);
    return {
      arrayBuffer: async () => bytes.buffer as ArrayBuffer,
      size: bytes.length,
      body: new ReadableStream({ start(c) { c.enqueue(bytes); c.close(); } }),
    };
  }
  async head(key: string): Promise<{ size: number } | null> {
    const text = this.store.get(key);
    return text === undefined ? null : { size: text.length };
  }
  async delete(key: string): Promise<void> {
    this.store.delete(key);
  }
}

function makeEnv(r2: FakeR2, db?: unknown): Env {
  return { DB: (db ?? {}) as D1Database, CLIPS_BUCKET: r2 as unknown as Env["CLIPS_BUCKET"] } as Env;
}

// --- D1 sobre SQLite real (como event-dedupe.test.ts) + batch ---
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

function makeSqliteDb(): { d1: D1Database; raw: Database; batchCalls: { n: number } } {
  const raw = new Database(":memory:");
  raw.exec(
    "CREATE TABLE jobs (" +
      "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL DEFAULT 'sebastian', " +
      "kind TEXT NOT NULL DEFAULT 'publish', clip_id INTEGER, platform TEXT, api_account_id INTEGER, " +
      "state TEXT NOT NULL DEFAULT 'scheduled', idempotency_key TEXT, platform_id TEXT, " +
      "evidence_url TEXT, title TEXT NOT NULL DEFAULT '', caption TEXT NOT NULL DEFAULT '', " +
      "r2_key TEXT, attempts INTEGER NOT NULL DEFAULT 0, retryable INTEGER NOT NULL DEFAULT 1, " +
      "next_run_at INTEGER, posted_day TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, " +
      "wal_reconciled INTEGER NOT NULL DEFAULT 0)",
  );
  const batchCalls = { n: 0 };
  const d1 = {
    prepare: (sql: string) => new SqliteStmt(raw, sql),
    batch: async (stmts: SqliteStmt[]) => {
      batchCalls.n++;
      const out = [];
      for (const s of stmts) out.push(await s.run());
      return out;
    },
  } as unknown as D1Database;
  return { d1, raw, batchCalls };
}

function job(over: Partial<JobRow> = {}): JobRow {
  return {
    id: 5,
    tenant_id: TENANT,
    kind: "publish",
    clip_id: 91,
    platform: "youtube_shorts",
    api_account_id: 2,
    state: "scheduled",
    idempotency_key: "key-91-yt",
    resume_data: null,
    platform_id: null,
    evidence_url: null,
    title: "t",
    caption: "GTA 6: reacción",
    r2_key: "clips/sebastian/91/x.mp4",
    language: "es",
    posted_day: null,
    attempts: 0,
    last_error: null,
    retryable: 1,
    next_run_at: null,
    created_at: Date.now(),
    updated_at: Date.now(),
    ...over,
  } as JobRow;
}

function intentEntry(over: Partial<WalEntry> = {}): Omit<WalEntry, "v" | "at" | "tenant"> {
  return {
    type: "intent",
    clip_id: 91,
    platform: "youtube_shorts",
    account_id: 2,
    job_id: 5,
    idempotency_key: "key-91-yt",
    day: DAY,
    ...over,
  };
}

describe("B1 — diario WAL en R2", () => {
  test("añadir intención y resultado, y leerlos de vuelta", async () => {
    const r2 = new FakeR2();
    const env = makeEnv(r2);
    expect(await appendWalEntry(env, TENANT, intentEntry())).toBe(true);
    expect(
      await appendWalEntry(env, TENANT, {
        ...intentEntry(),
        type: "result",
        outcome: "posted",
        platform_id: "abc123",
        url: "https://www.youtube.com/shorts/abc123",
      }),
    ).toBe(true);
    const entries = await readWalEntries(env, TENANT, DAY);
    expect(entries).toHaveLength(2);
    expect(entries[0]?.type).toBe("intent");
    expect(entries[1]?.outcome).toBe("posted");
    expect(entries[1]?.url).toBe("https://www.youtube.com/shorts/abc123");
  });

  test("solo-agregar: una segunda entrada no borra la primera", async () => {
    const r2 = new FakeR2();
    const env = makeEnv(r2);
    await appendWalEntry(env, TENANT, intentEntry());
    await appendWalEntry(env, TENANT, intentEntry({ job_id: 6, idempotency_key: "key-91-tt" }));
    const raw = r2.store.get(walKey(TENANT, DAY)) ?? "";
    expect(raw.split("\n").filter(Boolean)).toHaveLength(2);
    expect(await readWalEntries(env, TENANT, DAY)).toHaveLength(2);
  });

  test("si R2 falla, añadir devuelve false y no lanza", async () => {
    const r2 = new FakeR2();
    r2.failPut = true;
    expect(await appendWalEntry(makeEnv(r2), TENANT, intentEntry())).toBe(false);
  });

  test("walFindPosted encuentra por clip+plataforma y por clave; la intención sola no basta", async () => {
    const r2 = new FakeR2();
    const env = makeEnv(r2);
    await appendWalEntry(env, TENANT, intentEntry());
    expect(await walFindPosted(env, TENANT, 91, "youtube_shorts", [DAY], "key-91-yt")).toBeNull();
    await appendWalEntry(env, TENANT, {
      ...intentEntry(),
      type: "result",
      outcome: "posted",
      platform_id: "abc123",
      url: "https://www.youtube.com/shorts/abc123",
    });
    const found = await walFindPosted(env, TENANT, 91, "youtube_shorts", [DAY], "key-91-yt");
    expect(found?.platformId).toBe("abc123");
    expect(found?.evidenceUrl).toBe("https://www.youtube.com/shorts/abc123");
    // Por clave aunque cambie el clip (misma clave = mismo intento).
    const byKey = await walFindPosted(env, TENANT, 999, "youtube_shorts", [DAY], "key-91-yt");
    expect(byKey?.platformId).toBe("abc123");
    expect(await walFindPosted(env, TENANT, 91, "tiktok", [DAY], "otra-clave")).toBeNull();
  });
});

describe("B1 — volcado a D1 en un solo lote", () => {
  test("vuelca el resultado posted y marca el job (posted_day, platform_id, evidence_url, wal_reconciled)", async () => {
    const r2 = new FakeR2();
    const { d1, raw, batchCalls } = makeSqliteDb();
    const env = makeEnv(r2, d1);
    raw.query("INSERT INTO jobs (id, tenant_id, clip_id, platform, state, idempotency_key, created_at, updated_at) VALUES (5, 'sebastian', 91, 'youtube_shorts', 'processing', 'key-91-yt', 1, 1)").run();
    await appendWalEntry(env, TENANT, intentEntry());
    await appendWalEntry(env, TENANT, {
      ...intentEntry(),
      type: "result",
      outcome: "posted",
      platform_id: "abc123",
      url: "https://www.youtube.com/shorts/abc123",
    });
    const res = await flushWalDay(env, TENANT, DAY);
    expect(res.flushed).toBe(1);
    expect(res.deferred).toBe(false);
    expect(batchCalls.n).toBe(1);
    const row = raw.query("SELECT state, platform_id, evidence_url, posted_day, wal_reconciled FROM jobs WHERE id = 5").get() as Record<string, unknown>;
    expect(row.state).toBe("posted");
    expect(row.platform_id).toBe("abc123");
    expect(row.evidence_url).toBe("https://www.youtube.com/shorts/abc123");
    expect(row.posted_day).toBe(DAY);
    expect(row.wal_reconciled).toBe(1);
    // Segundo volcado: el marcador lo salta, sin otro batch.
    const res2 = await flushWalDay(env, TENANT, DAY);
    expect(res2.skipped).toBe(true);
    expect(batchCalls.n).toBe(1);
  });

  test("ante 7500 se retira sin reintentar en la misma llamada y sin marcador", async () => {
    const r2 = new FakeR2();
    let batchCalls = 0;
    const d1 = {
      prepare: () => ({ bind: (..._a: unknown[]) => ({ run: async () => ({ meta: {} }) }) }),
      batch: async () => {
        batchCalls++;
        throw new Error("D1_ERROR: too many writes, code 7500");
      },
    } as unknown as D1Database;
    const env = makeEnv(r2, d1);
    await appendWalEntry(env, TENANT, intentEntry());
    await appendWalEntry(env, TENANT, {
      ...intentEntry(),
      type: "result",
      outcome: "posted",
      platform_id: "abc123",
      url: "https://www.youtube.com/shorts/abc123",
    });
    const res = await flushWalDay(env, TENANT, DAY);
    expect(res.deferred).toBe(true);
    expect(res.flushed).toBe(0);
    expect(batchCalls).toBe(1); // un intento, sin reintento en la llamada
    expect(r2.store.has(`${walKey(TENANT, DAY).replace(".jsonl", "")}.flushed.json`)).toBe(false);
    // Siguiente ciclo: lo vuelve a intentar exactamente una vez.
    const res2 = await flushWalDay(env, TENANT, DAY);
    expect(res2.deferred).toBe(true);
    expect(batchCalls).toBe(2);
  });

  test("isD1QuotaError casa el 7500 y no un error cualquiera", () => {
    expect(isD1QuotaError(new Error("D1_ERROR: ... 7500 ..."))).toBe(true);
    expect(isD1QuotaError(new Error("UNIQUE constraint failed"))).toBe(false);
    expect(isD1QuotaError(new Error("network down"))).toBe(false);
  });
});

describe("B2 — marca [clip:id] legacy, solo lectura", () => {
  test("la marca ya NO se añade a captions nuevos; solo se lee en posts viejos que la llevan", () => {
    // appendClipMarker se eliminó el 2026-11-23 (era visible en
    // público, p. ej. el reel del clip 168 en Instagram). Quedan
    // clipMarker/containsClipMarker para detectar posts viejos.
    expect(clipMarker(91)).toBe("[clip:91]");
    expect(containsClipMarker("algo [clip:91] más", 91)).toBe(true);
    expect(containsClipMarker("algo [clip:92] más", 91)).toBe(false);
    expect(containsClipMarker("GTA 6: reacción", 91)).toBe(false);
  });
});

describe("B2 — misma clave → no se sube (diario primero)", () => {
  test("el diario con posted para la misma idempotency_key frena sin consultar la plataforma", async () => {
    const r2 = new FakeR2();
    const env = makeEnv(r2);
    await appendWalEntry(env, TENANT, intentEntry());
    await appendWalEntry(env, TENANT, {
      ...intentEntry(),
      type: "result",
      outcome: "posted",
      platform_id: "abc123",
      url: "https://www.youtube.com/shorts/abc123",
    });
    let platformCalls = 0;
    const spy: FetchJson = async () => {
      platformCalls++;
      return { status: 200, json: {} };
    };
    const dup = await checkDuplicateBeforeUpload(env, job(), {}, spy);
    expect(dup.duplicate).toBe(true);
    expect(dup.source).toBe("wal");
    expect(dup.evidenceUrl).toBe("https://www.youtube.com/shorts/abc123");
    expect(platformCalls).toBe(0);
  });
});

describe("B2 — comprobación en plataforma (clientes mockeados)", () => {
  test("YouTube: encuentra por la marca en la descripción y no por título repetido", async () => {
    const origFetch = globalThis.fetch;
    try {
      globalThis.fetch = (async (url: unknown) => {
        const u = String(url);
        const body = (o: unknown) => ({ status: 200, text: async () => JSON.stringify(o), headers: new Headers() });
        if (u.includes("oauth2.googleapis.com/token")) return body({ access_token: "tok" });
        if (u.includes("/youtube/v3/channels")) {
          return body({ items: [{ contentDetails: { relatedPlaylists: { uploads: "UUxxx" } } }] });
        }
        if (u.includes("/youtube/v3/playlistItems")) {
          return body({
            items: [
              { snippet: { title: "GTA 6: reacción", description: "otro clip", resourceId: { videoId: "otro1" } } },
              { snippet: { title: "GTA 6: reacción", description: "GTA 6: reacción\n[clip:91]", resourceId: { videoId: "abc123" } } },
            ],
          });
        }
        throw new Error(`fetch sin mock: ${u}`);
      }) as typeof fetch;
      const found = await checkYoutubeAlreadyPublished(
        { youtube: { accessToken: "", refreshToken: "r", clientId: "c", clientSecret: "s" } },
        91,
      );
      expect(found.status).toBe("found");
      expect(found.platformId).toBe("abc123");
      expect(found.evidenceUrl).toBe("https://www.youtube.com/shorts/abc123");
    } finally {
      globalThis.fetch = origFetch;
    }
  });

  test("YouTube: sin marca en ningún vídeo → not_found", async () => {
    const origFetch = globalThis.fetch;
    try {
      globalThis.fetch = (async (url: unknown) => {
        const u = String(url);
        const body = (o: unknown) => ({ status: 200, text: async () => JSON.stringify(o), headers: new Headers() });
        if (u.includes("oauth2.googleapis.com/token")) return body({ access_token: "tok" });
        if (u.includes("/youtube/v3/channels")) return body({ items: [{ contentDetails: { relatedPlaylists: { uploads: "UUxxx" } } }] });
        if (u.includes("/youtube/v3/playlistItems")) {
          return body({ items: [{ snippet: { title: "GTA 6: reacción", description: "sin marca", resourceId: { videoId: "otro1" } } }] });
        }
        throw new Error(`fetch sin mock: ${u}`);
      }) as typeof fetch;
      const res = await checkYoutubeAlreadyPublished(
        { youtube: { accessToken: "", refreshToken: "r", clientId: "c", clientSecret: "s" } },
        91,
      );
      expect(res.status).toBe("not_found");
    } finally {
      globalThis.fetch = origFetch;
    }
  });

  test("Instagram: encuentra por la marca en el caption", async () => {
    const fetchJson: FetchJson = async (url) => {
      expect(url).toContain("/123/media");
      return {
        status: 200,
        json: {
          data: [
            { id: "111", caption: "otro", permalink: "https://www.instagram.com/p/AAA/" },
            { id: "222", caption: "GTA 6: reacción\n[clip:91]", permalink: "https://www.instagram.com/reel/222/" },
          ],
        },
      };
    };
    const res = await checkInstagramAlreadyPublished({ instagram: "tok", igUserId: "123" }, 91, fetchJson);
    expect(res.status).toBe("found");
    expect(res.platformId).toBe("222");
    expect(res.evidenceUrl).toBe("https://www.instagram.com/reel/222/");
  });

  test("Facebook: encuentra por la marca en la descripción", async () => {
    const fetchJson: FetchJson = async (url) => {
      expect(url).toContain("/456/videos");
      return {
        status: 200,
        json: { data: [{ id: "789", description: "GTA 6: reacción\n[clip:91]", permalink_url: "https://www.facebook.com/reel/789/" }] },
      };
    };
    const res = await checkFacebookAlreadyPublished({ facebook: "tok", pageId: "456" }, 91, fetchJson);
    expect(res.status).toBe("found");
    expect(res.platformId).toBe("789");
    expect(res.evidenceUrl).toBe("https://www.facebook.com/reel/789/");
  });

  test("TikTok: sin listado — unavailable, la garantía recae en diario+clave", () => {
    const res = checkTiktokAlreadyPublished();
    expect(res.status).toBe("unavailable");
    expect(res.reason).toMatch(/video\.list/);
  });

  test("checkDuplicateBeforeUpload: plataforma encuentra por marca → duplicate (source platform)", async () => {
    const r2 = new FakeR2();
    const env = makeEnv(r2);
    const fetchJson: FetchJson = async () => ({
      status: 200,
      json: { data: [{ id: "222", caption: "GTA 6: reacción\n[clip:91]", permalink: "https://www.instagram.com/reel/222/" }] },
    });
    const dup = await checkDuplicateBeforeUpload(
      env,
      job({ platform: "instagram_reels" }),
      { instagram: "tok", igUserId: "123" },
      fetchJson,
    );
    expect(dup.duplicate).toBe(true);
    expect(dup.source).toBe("platform");
    expect(dup.platformId).toBe("222");
  });

  test("checkDuplicateBeforeUpload: diario vacío + plataforma sin marca → no duplica", async () => {
    const r2 = new FakeR2();
    const env = makeEnv(r2);
    const fetchJson: FetchJson = async () => ({ status: 200, json: { data: [{ id: "111", caption: "sin marca" }] } });
    const dup = await checkDuplicateBeforeUpload(
      env,
      job({ platform: "instagram_reels" }),
      { instagram: "tok", igUserId: "123" },
      fetchJson,
    );
    expect(dup.duplicate).toBe(false);
    expect(dup.platformCheck).toBe("not_found");
  });
});

describe("Migración 0035 (sin aplicar)", () => {
  test("añade wal_reconciled a jobs sin tocar idempotency_key", async () => {
    const sql = await Bun.file(new URL("../../migrations/0035_wal_reconciled.sql", import.meta.url)).text();
    const raw = new Database(":memory:");
    raw.exec(
      "CREATE TABLE jobs (" +
        "id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL DEFAULT 'publish', clip_id INTEGER, " +
        "platform TEXT, api_account_id INTEGER, state TEXT NOT NULL DEFAULT 'scheduled', " +
        "idempotency_key TEXT, platform_id TEXT, evidence_url TEXT, title TEXT NOT NULL DEFAULT '', " +
        "caption TEXT NOT NULL DEFAULT '', r2_key TEXT, attempts INTEGER NOT NULL DEFAULT 0, " +
        "retryable INTEGER NOT NULL DEFAULT 1, next_run_at INTEGER, posted_day TEXT, " +
        "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)",
    );
    raw.exec(sql);
    const cols = raw.query("PRAGMA table_info(jobs)").all() as Array<{ name: string }>;
    expect(cols.map((c) => c.name)).toContain("wal_reconciled");
    expect(cols.map((c) => c.name)).toContain("idempotency_key");
  });
});
