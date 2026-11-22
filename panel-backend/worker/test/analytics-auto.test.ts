// Tests de la medición automática de analíticas + auto-boost por
// tops medidos + auto-enqueue (2026-11-19).
// Sin red real (fetch con mock), sin D1 real: mock en memoria de
// D1Database copiado de security-fixes.test.ts, más un R2 falso.
import { afterEach, describe, expect, test } from "bun:test";
import { encryptCredential } from "../src/crypto";
import type { Env } from "../src/cloudflare";
import {
  boostBonusByClip,
  effectiveQueuePriority,
  listPostedJobs,
  queueClipCompare,
  type ClipRow,
  type ClipStatRow,
} from "../src/db";
import { computeAnalytics } from "../src/index";
import { runWorkerCycle, santiagoDayKey } from "../src/jobs";
import {
  collectAnalytics,
  extractTikTokVideoId,
  fetchFacebookStats,
  fetchInstagramStats,
  fetchTikTokStats,
  fetchYoutubeStats,
  StatsPermissionError,
} from "../src/publish/stats";

const SECRET = Buffer.from("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb").toString("base64"); // SOLO TEST
const KEY = Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"); // 32 bytes, SOLO TEST
const MP4_FAKE = new Uint8Array([0, 0, 0, 24, 102, 116, 121, 112, 1, 2, 3, 4]);

type Row = Record<string, unknown>;

// --- Mock D1 en memoria (copia del de clip-language.test.ts) ---
class FakeStmt {
  constructor(
    private db: FakeD1,
    private sql: string,
    private params: unknown[] = [],
  ) {}
  bind(...values: unknown[]): FakeStmt {
    return new FakeStmt(this.db, this.sql, values);
  }
  async first<T = Record<string, unknown>>(): Promise<T | null> {
    const rows = this.db.runSelect(this.sql, this.params);
    return (rows[0] as T | undefined) ?? null;
  }
  async all<T = Record<string, unknown>>(): Promise<{ results: T[]; success: boolean; meta: Record<string, unknown> }> {
    return { results: this.db.runSelect(this.sql, this.params) as T[], success: true, meta: {} };
  }
  async run(): Promise<{ results: never[]; success: boolean; meta: { changes?: number; last_row_id?: number } }> {
    return this.db.runWrite(this.sql, this.params);
  }
}

function parseWhere(where: string, params: unknown[]): (r: Row) => boolean {
  const conds = where.split(/\s+AND\s+/i).map((c) => c.trim());
  let pi = 0;
  const tests: ((r: Row) => boolean)[] = [];
  for (const c of conds) {
    const eq = /^(\w+)\s*=\s*\?$/.exec(c);
    const neq = /^(\w+)\s*<>\s*\?$/.exec(c);
    const neqLit = /^(\w+)\s*<>\s*'([^']*)'$/.exec(c);
    const eqLit = /^(\w+)\s*=\s*'([^']*)'$/.exec(c);
    const isNotNull = /^(\w+)\s+IS\s+NOT\s+NULL$/i.exec(c);
    // (next_run_at IS NULL OR next_run_at <= ?) del SELECT de
    // /api/jobs/next: consume un parámetro.
    const nextRunOr = /^\(?(\w+)\s+IS\s+NULL\s+OR\s+\1\s*<=\s*\?\)?$/i.exec(c);
    const lt = /^(\w+)\s*<=?\s*\?$/.exec(c);
    const gt = /^(\w+)\s*>=?\s*\?$/.exec(c);
    const eqNum = /^(\w+)\s*=\s*(\d+)$/.exec(c);
    const inList = /^(\w+)\s+IN\s*\(([^)]+)\)$/i.exec(c);
    if (eq) {
      const col = eq[1] as string;
      const val = params[pi++];
      tests.push((r) => r[col] === val);
    } else if (lt) {
      const col = lt[1] as string;
      const val = params[pi++];
      tests.push((r) => (r[col] as number) < (val as number));
    } else if (gt) {
      const col = gt[1] as string;
      const val = params[pi++];
      tests.push((r) => (r[col] as number) > (val as number));
    } else if (neq) {
      const col = neq[1] as string;
      const val = params[pi++];
      tests.push((r) => r[col] !== val);
    } else if (neqLit) {
      const col = neqLit[1] as string;
      const lit = neqLit[2] as string;
      tests.push((r) => r[col] !== lit);
    } else if (eqLit) {
      const col = eqLit[1] as string;
      const lit = eqLit[2] as string;
      tests.push((r) => r[col] === lit);
    } else if (isNotNull) {
      const col = isNotNull[1] as string;
      tests.push((r) => r[col] !== null && r[col] !== undefined);
    } else if (nextRunOr) {
      const col = nextRunOr[1] as string;
      const val = params[pi++];
      tests.push((r) => r[col] === null || r[col] === undefined || (r[col] as number) <= (val as number));
    } else if (eqNum) {
      const col = eqNum[1] as string;
      const num = Number(eqNum[2]);
      tests.push((r) => r[col] === num);
    } else if (inList) {
      const col = inList[1] as string;
      const vals = (inList[2] as string).split(",").map((s) => s.trim().replace(/^'(.*)'$/, "$1"));
      tests.push((r) => vals.includes(String(r[col])));
    } else {
      throw new Error(`WHERE no soportado en el mock: ${c}`);
    }
  }
  return (r) => tests.every((t) => t(r));
}

function applyOrder(rows: Row[], orderBy: string | undefined): Row[] {
  if (!orderBy) return rows;
  const keys = orderBy.split(",").map((part) => {
    const liveCase = /^CASE WHEN (\w+) = 'live' THEN 0 ELSE 1 END$/i.exec(part.trim());
    if (liveCase) return { col: liveCase[1] as string, desc: false, liveRank: true };
    const m = /^(\w+)(?:\s+(ASC|DESC))?$/i.exec(part.trim());
    if (!m) throw new Error(`ORDER BY no soportado en el mock: ${part}`);
    return { col: m[1] as string, desc: (m[2] ?? "ASC").toUpperCase() === "DESC", liveRank: false };
  });
  return [...rows].sort((a, b) => {
    for (const { col, desc, liveRank } of keys) {
      const av = (liveRank ? (a[col] === "live" ? 0 : 1) : a[col]) as number | string | null;
      const bv = (liveRank ? (b[col] === "live" ? 0 : 1) : b[col]) as number | string | null;
      if (av === bv) continue;
      const cmp = av == null ? -1 : bv == null ? 1 : av < bv ? -1 : 1;
      return desc ? -cmp : cmp;
    }
    return 0;
  });
}

class FakeD1 {
  tables: Record<string, Row[]> = {};

  seed(table: string, row: Row): void {
    (this.tables[table] ??= []).push({ ...row });
  }

  prepare(sql: string): FakeStmt {
    return new FakeStmt(this, sql);
  }
  // A2: batch ejecuta las sentencias en orden, como la D1 real. Antes
  // era un stub que devolvía [] sin ejecutar nada.
  async batch(statements: FakeStmt[]): Promise<Array<{ results: never[]; success: boolean; meta: { changes?: number; last_row_id?: number } }>> {
    const out = [];
    for (const s of statements) out.push(await s.run());
    return out;
  }
  async exec(): Promise<{ results: never[]; success: boolean; meta: Record<string, unknown> }> {
    return { results: [], success: true, meta: {} };
  }

  runSelect(sql: string, params: unknown[]): Row[] {
    // dueJobs (jobs.ts): LEFT JOIN con clips para el segmento LIVE.
    if (/FROM jobs LEFT JOIN clips/i.test(sql)) {
      const [tenant, now, limit] = params as [string, number, number];
      const clips = this.tables["clips"] ?? [];
      const rows = [...(this.tables["jobs"] ?? [])]
        .filter((j) => j.tenant_id === tenant && j.state === "scheduled" && (j.next_run_at === null || j.next_run_at === undefined || (j.next_run_at as number) <= now))
        .map((j) => {
          const clip = clips.find((c) => c.id === j.clip_id && c.tenant_id === j.tenant_id);
          return { ...j, __origin: clip?.origin ?? "vod" };
        })
        .sort((a, b) => ((a.__origin === "live" ? 0 : 1) - (b.__origin === "live" ? 0 : 1)) || ((a.created_at as number) - (b.created_at as number)));
      return rows.slice(0, limit).map(({ __origin: _o, ...rest }) => rest);
    }
    const m = /^SELECT\s+(.+?)\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+?))?(?:\s+ORDER BY\s+(.+?))?(?:\s+LIMIT\s+(\d+))?\s*$/is.exec(sql);
    if (!m) throw new Error(`SELECT no soportado en el mock: ${sql}`);
    const [, colsRaw, table, where, orderBy, limitRaw] = m as [string, string, string, string | undefined, string | undefined, string | undefined];
    let rows = [...(this.tables[table as string] ?? [])];
    if (where) rows = rows.filter(parseWhere(where, params));
    rows = applyOrder(rows, orderBy);
    if (limitRaw) rows = rows.slice(0, Number(limitRaw));
    const cols = (colsRaw as string).trim();
    if (cols === "*") return rows;
    const countMatch = /^COUNT\(\*\) AS (\w+)$/i.exec(cols);
    if (countMatch) return [{ [countMatch[1] as string]: rows.length }];
    const wanted = cols.split(",").map((c) => c.trim());
    return rows.map((r) => {
      const out: Row = {};
      for (const c of wanted) {
        // jobs.* / clips.* con prefijo de tabla.
        const col = c.includes(".") ? (c.split(".")[1] as string) : c;
        out[c] = r[col];
      }
      return out;
    });
  }

  runWrite(sql: string, params: unknown[]): { results: never[]; success: boolean; meta: { changes?: number; last_row_id?: number } } {
    if (/^INSERT INTO settings/i.test(sql)) {
      const rows = (this.tables.settings ??= []);
      const [tenant, data, updatedAt] = params as [string, string, number];
      const existing = rows.find((r) => r.tenant_id === tenant);
      if (existing) {
        existing.data = data;
        existing.updated_at = updatedAt;
        return { results: [], success: true, meta: { changes: 1 } };
      }
      const id = rows.reduce((max, r) => Math.max(max, Number(r.id ?? 0)), 0) + 1;
      rows.push({ id, tenant_id: tenant, data, updated_at: updatedAt });
      return { results: [], success: true, meta: { changes: 1, last_row_id: id } };
    }
    const insert = /^INSERT INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)\s*$/is.exec(sql);
    if (insert) {
      const [, table, colsRaw, valsRaw] = insert as [string, string, string, string];
      const cols = colsRaw.split(",").map((c) => c.trim());
      const vals = valsRaw.split(",").map((s) => s.trim());
      const rows = (this.tables[table as string] ??= []);
      const id = rows.reduce((max, r) => Math.max(max, Number(r.id ?? 0)), 0) + 1;
      const row: Row = { id };
      let pi = 0;
      cols.forEach((c, i) => {
        const v = vals[i] as string;
        if (v === "?") {
          row[c] = params[pi++];
        } else if (/^'(.*)'$/.test(v)) {
          row[c] = (v as string).slice(1, -1);
        } else if (/^-?\d+$/.test(v)) {
          row[c] = Number(v);
        } else if (/^NULL$/i.test(v)) {
          row[c] = null;
        } else {
          throw new Error(`VALUES no soportado en el mock: ${v}`);
        }
      });
      rows.push(row);
      return { results: [], success: true, meta: { changes: 1, last_row_id: id } };
    }
    const update = /^UPDATE\s+(\w+)\s+SET\s+(.+?)\s+WHERE\s+(.+)$/is.exec(sql);
    if (update) {
      const [, table, setRaw, where] = update as [string, string, string, string];
      // SET mezcla ? con literales (claimJob: state = 'processing').
      const setCols: string[] = [];
      const setVals: unknown[] = [];
      let pi = 0;
      for (const part of setRaw.split(",")) {
        const p = part.trim();
        const mm = /^(\w+)\s*=\s*\?$/.exec(p);
        const lit = /^(\w+)\s*=\s*'([^']*)'$/.exec(p);
        const num = /^(\w+)\s*=\s*(-?\d+)$/.exec(p);
        if (mm) {
          setCols.push(mm[1] as string);
          setVals.push(params[pi++]);
        } else if (lit) {
          setCols.push(lit[1] as string);
          setVals.push(lit[2]);
        } else if (num) {
          setCols.push(num[1] as string);
          setVals.push(Number(num[2]));
        } else {
          throw new Error(`SET no soportado en el mock: ${part}`);
        }
      }
      const whereParams = params.slice(pi);
      const rows = this.tables[table as string] ?? [];
      const match = parseWhere(where, whereParams);
      let changes = 0;
      for (const r of rows) {
        if (!match(r)) continue;
        setCols.forEach((c, i) => {
          r[c] = setVals[i];
        });
        changes++;
      }
      return { results: [], success: true, meta: { changes } };
    }
    const del = /^DELETE FROM\s+(\w+)\s+WHERE\s+(.+)$/is.exec(sql);
    if (del) {
      const [, table, where] = del as [string, string];
      const rows = this.tables[table as string] ?? [];
      const match = parseWhere(where, params);
      const kept = rows.filter((r) => !match(r));
      const changes = rows.length - kept.length;
      this.tables[table as string] = kept;
      return { results: [], success: true, meta: { changes } };
    }
    throw new Error(`Escritura no soportada en el mock: ${sql}`);
  }
}

// --- Mock R2 en memoria ---
class FakeR2 {
  store = new Map<string, Uint8Array>();
  async put(key: string, body: ReadableStream | Uint8Array, _opts?: unknown): Promise<{ key: string; size: number }> {
    const buf = body instanceof Uint8Array ? body : new Uint8Array(await new Response(body as ReadableStream).arrayBuffer());
    this.store.set(key, buf);
    return { key, size: buf.byteLength };
  }
  async get(key: string, options?: { range?: { offset: number; length: number } }): Promise<{ body: ReadableStream; httpMetadata: { contentType: string }; size: number; arrayBuffer(): Promise<ArrayBuffer> } | null> {
    const data = this.store.get(key);
    if (!data) return null;
    const slice = options?.range ? data.slice(options.range.offset, options.range.offset + (options.range.length ?? data.byteLength)) : data;
    return {
      body: new ReadableStream({ start(c) { c.enqueue(slice); c.close(); } }),
      httpMetadata: { contentType: "video/mp4" },
      size: data.byteLength,
      arrayBuffer: async () => slice.buffer.slice(0) as ArrayBuffer,
    };
  }
  async head(key: string): Promise<{ size: number } | null> {
    const data = this.store.get(key);
    return data ? { size: data.byteLength } : null;
  }
  async delete(key: string): Promise<void> {
    this.store.delete(key);
  }
}

function makeEnv(db: FakeD1, bucket: FakeR2): Env {
  return {
    DB: db as unknown as Env["DB"],
    TENANT: "sebastian",
    CLIPS_BUCKET: bucket as unknown as Env["CLIPS_BUCKET"],
    SESSION_SECRET: SECRET,
    CREDENTIAL_ENCRYPTION_KEY: KEY,
    BRIDGE_TOKEN: "bridge-test",
    GITHUB_TOKEN: "",
    ALLOWED_EMAIL: "",
    GITHUB_REPO: "exe2027/viral-clip-agent",
  };
}

// --- Datos de prueba ---
function clipRow(over: Row = {}): Row {
  return {
    id: 1,
    tenant_id: "sebastian",
    source_id: null,
    influencer_id: null,
    title: "Clip de prueba",
    start_sec: 100,
    end_sec: 148, // 48 s
    score: 80,
    status: "aprobado",
    subtitle_style: "tiktok",
    seo_title: "",
    seo_description: "",
    seo_tags: "",
    thumb_text: "",
    platforms: JSON.stringify(["tiktok"]),
    scheduled_for: null,
    published_at: null,
    file_deleted: 0,
    created_at: 1000,
    updated_at: 1000,
    captions: "{}",
    viral: '{"platforms":{"tiktok":{"score":95}}}',
    seo_overrides: "[]",
    preview_seen: 1,
    archived: 0,
    origin: "vod",
    publish_states: "{}",
    publish_blocked: 0,
    needs_human_transcription: 0,
    subtitles_complete: 1,
    video_r2_key: "",
    game: "GTA VI",
    language: "es",
    ...over,
  };
}

/** Cuenta ESTABLECIDA (plan resto 2/día vigente) con credencial. */
function seedAccount(db: FakeD1, over: Row = {}): void {
  db.seed("api_accounts", {
    id: 10,
    tenant_id: "sebastian",
    platform: "tiktok",
    language: "es",
    label: "TikTok ES",
    credential: "",
    credential_enc: "v1:xxx", // no descifra: el adaptador dirá "sin token"
    extra: "",
    extra_enc: "",
    status: "conectada",
    note: "",
    created_at: 1,
    verified_at: 2000,
    verify_ok: 1,
    verify_message: "ok",
    first_published_at: Date.now() - 40 * 86400_000,
    published_count: 20,
    strikes: 0,
    maturity: "establecida",
    maturity_checked_day: santiagoDayKey(),
    ...over,
  });
}

function seedJob(db: FakeD1, over: Row = {}): void {
  db.seed("jobs", {
    id: 1,
    tenant_id: "sebastian",
    kind: "publish",
    clip_id: 1,
    platform: "tiktok",
    api_account_id: 10,
    state: "scheduled",
    idempotency_key: "k1",
    resume_data: null,
    platform_id: null,
    evidence_url: null,
    title: "t",
    caption: "t",
    r2_key: "clips/sebastian/1/aaa_x.mp4",
    language: "es",
    posted_day: null,
    attempts: 0,
    last_error: null,
    retryable: 1,
    next_run_at: null,
    created_at: 1000,
    updated_at: 1000,
    ...over,
  });
}


// --- Fetch con mock ---
const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

function jsonResponse(status: number, obj: unknown): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

function statRow(over: Partial<ClipStatRow> & { id: number }): ClipStatRow {
  return {
    id: over.id,
    tenant_id: "sebastian",
    clip_id: 1,
    platform: "youtube",
    views: 100,
    retention_pct: null,
    shares: null,
    saves: null,
    comments: null,
    new_subs: null,
    window_label: "7d",
    recorded_at: Date.now(),
    ...over,
  };
}

function clipObj(over: Partial<ClipRow> = {}): ClipRow {
  return {
    id: 1,
    tenant_id: "sebastian",
    source_id: null,
    influencer_id: null,
    title: "t",
    start_sec: 0,
    end_sec: 40,
    score: 80,
    status: "aprobado",
    subtitle_style: "tiktok",
    seo_title: "",
    seo_description: "",
    seo_tags: "",
    thumb_text: "",
    platforms: '["tiktok"]',
    scheduled_for: null,
    published_at: null,
    file_deleted: 0,
    created_at: 1000,
    updated_at: 1000,
    captions: "{}",
    viral: "{}",
    seo_overrides: "[]",
    preview_seen: 1,
    archived: 0,
    origin: "vod",
    publish_states: "{}",
    publish_blocked: 0,
    needs_human_transcription: 0,
    subtitles_complete: 1,
    video_r2_key: "",
    game: "GTA VI",
    language: "es",
    manual_rank: null,
    ...over,
  } as ClipRow;
}

function seedSettings(db: FakeD1, data: Record<string, unknown>): void {
  db.seed("settings", { tenant_id: "sebastian", data: JSON.stringify(data), updated_at: 1 });
}

function seedRunning(db: FakeD1, running = 1): void {
  db.seed("worker_state", { tenant_id: "sebastian", running, started_at: 1, last_note: "", updated_at: 1 });
}

async function seedYoutubeAccount(db: FakeD1): Promise<void> {
  db.seed("api_accounts", {
    id: 11,
    tenant_id: "sebastian",
    platform: "youtube",
    language: "es",
    label: "YouTube ES",
    credential: "",
    credential_enc: await encryptCredential("APIKEY-TEST", KEY, "sebastian"),
    extra: "",
    extra_enc: await encryptCredential(JSON.stringify({ client_id: "cid", client_secret: "csec", refresh_token: "rtok" }), KEY, "sebastian"),
    status: "conectada",
    note: "",
    created_at: 1,
    verified_at: 2000,
    verify_ok: 1,
    verify_message: "ok",
    first_published_at: Date.now() - 40 * 86400_000,
    published_count: 20,
    strikes: 0,
    maturity: "establecida",
    maturity_checked_day: santiagoDayKey(),
  });
}

async function seedTikTokAccount(db: FakeD1): Promise<void> {
  db.seed("api_accounts", {
    id: 10,
    tenant_id: "sebastian",
    platform: "tiktok",
    language: "es",
    label: "TikTok ES",
    credential: "",
    credential_enc: await encryptCredential("tiktok-access-token", KEY, "sebastian"),
    extra: "",
    extra_enc: "",
    status: "conectada",
    note: "",
    created_at: 1,
    verified_at: 2000,
    verify_ok: 1,
    verify_message: "ok",
    first_published_at: Date.now() - 40 * 86400_000,
    published_count: 20,
    strikes: 0,
    maturity: "establecida",
    maturity_checked_day: santiagoDayKey(),
  });
}

// ---------------------------------------------------------------------------
// Boost por tops medidos (matemática pura)
// ---------------------------------------------------------------------------
describe("boost por tops medidos", () => {
  test("ratio = mediana de views/mediana por plataforma; bonus = round(5*log2(ratio))", () => {
    const now = Date.now();
    const stats = [
      statRow({ id: 1, clip_id: 1, platform: "youtube", views: 2000, recorded_at: now - 1000 }),
      statRow({ id: 2, clip_id: 2, platform: "youtube", views: 1000, recorded_at: now - 1000 }),
      statRow({ id: 3, clip_id: 3, platform: "youtube", views: 500, recorded_at: now - 1000 }),
      statRow({ id: 4, clip_id: 1, platform: "tiktok", views: 100, recorded_at: now - 1000 }),
      statRow({ id: 5, clip_id: 2, platform: "tiktok", views: 200, recorded_at: now - 1000 }),
      statRow({ id: 6, clip_id: 3, platform: "tiktok", views: 200, recorded_at: now - 1000 }),
    ];
    const boost = boostBonusByClip(stats, now);
    // Mediana YT 1000, mediana TT 200. Clip 1: ratios 2 y 0,5 → mediana 1,25 → round(5*log2(1,25)) = 2.
    expect(boost.get(1)).toBe(2);
    // Clip 2: ratios 1 y 1 → ratio 1 → bonus 0.
    expect(boost.get(2) ?? 0).toBe(0);
    // Clip 3: ratios 0,5 y 1 → mediana 0,75 < 1 → sin bonus.
    expect(boost.has(3)).toBe(false);
  });

  test("el bonus tiene tope 10 (ratio 8 → 5*3 = 15 → 10)", () => {
    const now = Date.now();
    const stats = [
      statRow({ id: 1, clip_id: 1, platform: "youtube", views: 8000, recorded_at: now - 1000 }),
      statRow({ id: 2, clip_id: 2, platform: "youtube", views: 1000, recorded_at: now - 1000 }),
      statRow({ id: 3, clip_id: 3, platform: "youtube", views: 1000, recorded_at: now - 1000 }),
      statRow({ id: 4, clip_id: 4, platform: "youtube", views: 1000, recorded_at: now - 1000 }),
      statRow({ id: 5, clip_id: 5, platform: "youtube", views: 1000, recorded_at: now - 1000 }),
    ];
    // Mediana de [8000, 1000, 1000, 1000, 1000] = 1000; ratio 8 → 5*log2(8) = 15 → tope 10.
    expect(boostBonusByClip(stats, now).get(1)).toBe(10);
  });

  test("sin medición no hay bonus; medición de hace más de 7 días no cuenta; manda la última por (clip, plataforma)", () => {
    const now = Date.now();
    expect(boostBonusByClip([], now).size).toBe(0);
    const old = [
      statRow({ id: 1, clip_id: 1, platform: "youtube", views: 8000, recorded_at: now - 8 * 86400_000 }),
      statRow({ id: 2, clip_id: 2, platform: "youtube", views: 1000, recorded_at: now - 1000 }),
    ];
    expect(boostBonusByClip(old, now).size).toBe(0);
    const superseded = [
      statRow({ id: 1, clip_id: 1, platform: "youtube", views: 8000, recorded_at: now - 2000 }),
      statRow({ id: 2, clip_id: 1, platform: "youtube", views: 1000, recorded_at: now - 1000 }),
      statRow({ id: 3, clip_id: 2, platform: "youtube", views: 1000, recorded_at: now - 1000 }),
    ];
    // Última del clip 1: 1000, igual a la mediana → ratio 1 → bonus 0.
    expect(boostBonusByClip(superseded, now).get(1) ?? 0).toBe(0);
  });

  test("la prioridad efectiva tiene tope 100", () => {
    expect(effectiveQueuePriority(clipObj({ viral: '{"priority":96}' }), 10)).toBe(100);
    expect(effectiveQueuePriority(clipObj({ score: 70 }), 10)).toBe(80);
  });

  test("un VOD medido no pasa sobre un LIVE; el orden manual manda sobre el boost", () => {
    const live = clipObj({ id: 1, origin: "live", score: 70, created_at: 1000 });
    const vodTop = clipObj({ id: 2, origin: "vod", score: 95, viral: '{"priority":95}', created_at: 2000 });
    const boost = new Map([[2, 10]]);
    expect([vodTop, live].sort((a, b) => queueClipCompare(a, b, boost))[0]?.id).toBe(1);
    const manual = clipObj({ id: 3, origin: "vod", score: 50, manual_rank: 1, created_at: 3000 });
    const autoTop = clipObj({ id: 4, origin: "vod", score: 90, viral: '{"priority":90}', created_at: 4000 });
    const boost2 = new Map([[4, 10]]);
    expect([autoTop, manual].sort((a, b) => queueClipCompare(a, b, boost2))[0]?.id).toBe(3);
  });

  test("con boost, el clip medido sube dentro de su segmento; sin boost, el orden es el de antes", () => {
    const a = clipObj({ id: 1, score: 80, created_at: 1000 });
    const b = clipObj({ id: 2, score: 75, viral: '{"priority":75}', created_at: 2000 });
    expect([b, a].sort((x, y) => queueClipCompare(x, y))[0]?.id).toBe(1);
    const boost = new Map([[2, 10]]);
    expect([a, b].sort((x, y) => queueClipCompare(x, y, boost))[0]?.id).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// Fetchers por plataforma (fetch con mock)
// ---------------------------------------------------------------------------
describe("fetchers de analíticas (fetch mockeado)", () => {
  test("YouTube: views + comments; 401 → StatsPermissionError (sin reintento)", async () => {
    globalThis.fetch = (async (url: unknown) => {
      const u = String(url);
      if (u.includes("oauth2.googleapis.com/token")) return jsonResponse(200, { access_token: "AT-TEST" });
      if (u.includes("youtube/v3/videos")) {
        expect(u).toContain("fields=items(id,statistics(viewCount,likeCount,commentCount))");
        return jsonResponse(200, { items: [{ id: "YT1", statistics: { viewCount: "1234", likeCount: "10", commentCount: "5" } }] });
      }
      throw new Error(`fetch sin mock: ${u}`);
    }) as typeof fetch;
    const res = await fetchYoutubeStats({ accessToken: "", refreshToken: "rtok", clientId: "cid", clientSecret: "csec" }, ["YT1"]);
    expect(res.get("YT1")).toEqual({ views: 1234, comments: 5 });
    globalThis.fetch = (async (url: unknown) => {
      const u = String(url);
      if (u.includes("oauth2.googleapis.com/token")) return jsonResponse(200, { access_token: "AT-TEST" });
      return jsonResponse(403, { error: { code: 403, message: "forbidden" } });
    }) as typeof fetch;
    await expect(fetchYoutubeStats({ accessToken: "", refreshToken: "rtok", clientId: "cid", clientSecret: "csec" }, ["YT1"])).rejects.toBeInstanceOf(StatsPermissionError);
  });

  test("Instagram: retención derivada avg_watch/duración con tope 100", async () => {
    globalThis.fetch = (async (url: unknown) => {
      const u = String(url);
      expect(u).not.toContain("crossposted_views");
      expect(u).not.toContain("facebook_views");
      return jsonResponse(200, {
        data: [
          { name: "views", values: [{ value: 1000 }] },
          { name: "likes", values: [{ value: 50 }] },
          { name: "comments", values: [{ value: 7 }] },
          { name: "saved", values: [{ value: 3 }] },
          { name: "shares", values: [{ value: 2 }] },
          { name: "ig_reels_avg_watch_time", values: [{ value: 20000 }] },
        ],
      });
    }) as typeof fetch;
    const m = await fetchInstagramStats("TOKEN", "MEDIA1", 40000);
    expect(m).toEqual({ views: 1000, retention_pct: 50, shares: 2, saves: 3, comments: 7 });
    globalThis.fetch = (async () => jsonResponse(200, {
      data: [{ name: "views", values: [{ value: 10 }] }, { name: "ig_reels_avg_watch_time", values: [{ value: 999999 }] }],
    })) as typeof fetch;
    const capped = await fetchInstagramStats("TOKEN", "MEDIA1", 40000);
    expect(capped.retention_pct).toBe(100);
  });

  test("Instagram: código Meta 200/10 → StatsPermissionError", async () => {
    globalThis.fetch = (async () => jsonResponse(400, { error: { code: 10, message: "Permissions error" } })) as typeof fetch;
    await expect(fetchInstagramStats("TOKEN", "MEDIA1", 40000)).rejects.toBeInstanceOf(StatsPermissionError);
  });

  test("Facebook: si el conjunto completo falla, usa el mínimo; retención = completas/vistas", async () => {
    let calls = 0;
    globalThis.fetch = (async (url: unknown) => {
      const u = String(url);
      if (u.includes("video_insights")) {
        calls++;
        if (u.includes("blue_reels_play_count")) {
          return jsonResponse(400, { error: { code: 100, message: "metric not available" } });
        }
        return jsonResponse(200, {
          data: [
            { name: "total_video_views", values: [{ value: 500 }] },
            { name: "total_video_complete_views", values: [{ value: 250 }] },
          ],
        });
      }
      if (u.includes("fields=comments")) {
        return jsonResponse(200, { comments: { summary: { total_count: 7 } }, shares: { count: 3 } });
      }
      throw new Error(`fetch sin mock: ${u}`);
    }) as typeof fetch;
    const m = await fetchFacebookStats("TOKEN", "VID1");
    expect(calls).toBe(2);
    expect(m).toEqual({ views: 500, retention_pct: 50, shares: 3, saves: null, comments: 7 });
  });

  test("TikTok: 401 scope_not_authorized → StatsPermissionError; el id real sale de la evidence_url", async () => {
    globalThis.fetch = (async () => jsonResponse(401, { error: { code: "scope_not_authorized", message: "scope not authorized" } })) as typeof fetch;
    await expect(fetchTikTokStats("TOKEN", ["777"])).rejects.toBeInstanceOf(StatsPermissionError);
    expect(extractTikTokVideoId("https://www.tiktok.com/@elclipdegta6/video/777777")).toBe("777777");
    expect(extractTikTokVideoId("https://www.tiktok.com/@x/video/123?lang=es")).toBe("123");
    expect(extractTikTokVideoId(null)).toBeNull();
    expect(extractTikTokVideoId("https://zernio.example/post/abc")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// collectAnalytics (FakeD1 + fetch mockeado)
// ---------------------------------------------------------------------------
describe("collectAnalytics", () => {
  test("YouTube mide y guarda en clip_stats con window 7d; segunda pasada en 6 h no repite", async () => {
    const db = new FakeD1();
    await seedYoutubeAccount(db);
    db.seed("clips", clipRow({ id: 1 }));
    db.seed("jobs", {
      id: 50, tenant_id: "sebastian", kind: "publish", clip_id: 1, platform: "youtube_shorts",
      api_account_id: 11, state: "posted", platform_id: "YT1",
      evidence_url: "https://www.youtube.com/shorts/YT1", language: "es",
      posted_day: santiagoDayKey(), attempts: 0, last_error: null, retryable: 1,
      next_run_at: null, created_at: 1000, updated_at: Date.now(),
    });
    globalThis.fetch = (async (url: unknown) => {
      const u = String(url);
      if (u.includes("oauth2.googleapis.com/token")) return jsonResponse(200, { access_token: "AT-TEST" });
      if (u.includes("youtube/v3/videos")) {
        return jsonResponse(200, { items: [{ id: "YT1", statistics: { viewCount: "1234", likeCount: "10", commentCount: "5" } }] });
      }
      throw new Error(`fetch sin mock: ${u}`);
    }) as typeof fetch;
    const env = makeEnv(db, new FakeR2());
    const s1 = await collectAnalytics(env, "sebastian");
    expect(s1.ran).toBe(true);
    expect(s1.collected).toBe(1);
    const stats = db.tables["clip_stats"] ?? [];
    expect(stats).toHaveLength(1);
    expect(stats[0]?.platform).toBe("youtube");
    expect(stats[0]?.views).toBe(1234);
    expect(stats[0]?.comments).toBe(5);
    expect(stats[0]?.retention_pct).toBeNull();
    expect(stats[0]?.window_label).toBe("7d");
    const status = db.tables["analytics_status"] ?? [];
    expect(status).toHaveLength(1);
    expect(status[0]?.platform).toBe("youtube");
    expect(status[0]?.status).toBe("ok");
    const s2 = await collectAnalytics(env, "sebastian");
    expect(s2.ran).toBe(false);
    expect((db.tables["clip_stats"] ?? [])).toHaveLength(1);
  });

  test("TikTok bloqueado por permisos: sin_permiso con motivo video.list, sin cifras inventadas", async () => {
    const db = new FakeD1();
    await seedTikTokAccount(db);
    db.seed("clips", clipRow({ id: 1 }));
    db.seed("jobs", {
      id: 51, tenant_id: "sebastian", kind: "publish", clip_id: 1, platform: "tiktok",
      api_account_id: 10, state: "posted", platform_id: "999",
      evidence_url: "https://www.tiktok.com/@elclipdegta6/video/777777", language: "es",
      posted_day: santiagoDayKey(), attempts: 0, last_error: null, retryable: 1,
      next_run_at: null, created_at: 1000, updated_at: Date.now(),
    });
    globalThis.fetch = (async () => jsonResponse(401, { error: { code: "scope_not_authorized", message: "scope not authorized" } })) as typeof fetch;
    const env = makeEnv(db, new FakeR2());
    const s = await collectAnalytics(env, "sebastian");
    expect(s.ran).toBe(true);
    expect(s.collected).toBe(0);
    expect(s.statuses[0]?.platform).toBe("tiktok");
    expect(s.statuses[0]?.status).toBe("sin_permiso");
    expect(s.statuses[0]?.reason).toContain("video.list");
    expect(db.tables["clip_stats"] ?? []).toHaveLength(0);
    const status = db.tables["analytics_status"] ?? [];
    expect(status[0]?.status).toBe("sin_permiso");
    const events = db.tables["worker_events"] ?? [];
    expect(events.some((e) => String(e.message ?? "").includes("video.list"))).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Auto-enqueue en runWorkerCycle
// ---------------------------------------------------------------------------

// Fecha congelada (copia de clip02.test.ts): el auto-enqueue es
// automático y el gate de franja (reglas 2026-11-19) solo deja
// encolar dentro de 12:00-14:00, 18:00-20:00 o 21:00-23:00 (hora
// Chile). 2026-11-19T16:00Z = 13:00 en Santiago (franja mañana);
// además ese día NO es el día libre de acc-10 (miércoles).
async function withFrozenDate<T>(iso: string, fn: () => Promise<T>): Promise<T> {
  const RealDate = globalThis.Date;
  const t = new RealDate(iso).getTime();
  const Mock = function (this: Date, ...args: unknown[]) {
    return args.length === 0 ? new RealDate(t) : new RealDate(...(args as [number]));
  } as unknown as DateConstructor;
  Mock.now = () => t;
  Mock.parse = RealDate.parse;
  Mock.UTC = RealDate.UTC;
  globalThis.Date = Mock;
  try {
    return await fn();
  } finally {
    globalThis.Date = RealDate;
  }
}

describe("auto-enqueue de tops (runWorkerCycle)", () => {
  test("apagado por defecto (sin auto_publish_top): no encola nada", async () => {
    const db = new FakeD1();
    seedAccount(db);
    seedRunning(db);
    db.seed("clips", clipRow({ id: 1 }));
    const env = makeEnv(db, new FakeR2());
    const r = await runWorkerCycle(env, "sebastian");
    expect(r.ran).toBe(true);
    expect(r.autoEnqueued).toBe(0);
    expect(db.tables["jobs"] ?? []).toHaveLength(0);
  });

  test("encendido y gate que pasa: encola el top y el ciclo lo ejecuta (sin MP4 → draft, no publica)", async () => {
    const db = new FakeD1();
    seedAccount(db);
    seedRunning(db);
    seedSettings(db, { auto_publish_top: true });
    db.seed("clips", clipRow({ id: 1 }));
    const env = makeEnv(db, new FakeR2());
    // Dentro de franja (13:00 Santiago): fuera de ella el gate
    // frena el auto-enqueue con "fuera de franja" por diseño.
    const r = await withFrozenDate("2026-11-19T16:00:00.000Z", () => runWorkerCycle(env, "sebastian"));
    expect(r.ran).toBe(true);
    expect(r.autoEnqueued).toBe(1);
    const jobs = db.tables["jobs"] ?? [];
    expect(jobs).toHaveLength(1);
    expect(jobs[0]?.clip_id).toBe(1);
    expect(jobs[0]?.platform).toBe("tiktok");
    expect(jobs[0]?.state).toBe("draft");
  });

  test("encendido pero fuera de franja: no encola (el gate frena la automática fuera de 12-14/18-20/21-23)", async () => {
    const db = new FakeD1();
    seedAccount(db);
    seedRunning(db);
    seedSettings(db, { auto_publish_top: true });
    db.seed("clips", clipRow({ id: 1 }));
    const env = makeEnv(db, new FakeR2());
    // 10:00 en Santiago: fuera de franja.
    const r = await withFrozenDate("2026-11-19T13:00:00.000Z", () => runWorkerCycle(env, "sebastian"));
    expect(r.ran).toBe(true);
    expect(r.autoEnqueued).toBe(0);
    expect(db.tables["jobs"] ?? []).toHaveLength(0);
    const events = db.tables["worker_events"] ?? [];
    expect(events.some((e) => String(e.message ?? "").includes("fuera de franja"))).toBe(true);
  });

  test("encendido pero el gate frena (SEO 79 < 80): no encola y el freno queda en clip_events con el motivo literal", async () => {
    const db = new FakeD1();
    seedAccount(db);
    seedRunning(db);
    seedSettings(db, { auto_publish_top: true });
    db.seed("clips", clipRow({ id: 1, viral: '{"platforms":{"tiktok":{"score":79}}}' }));
    const env = makeEnv(db, new FakeR2());
    const r = await runWorkerCycle(env, "sebastian");
    expect(r.ran).toBe(true);
    expect(r.autoEnqueued).toBe(0);
    expect(db.tables["jobs"] ?? []).toHaveLength(0);
    const events = db.tables["clip_events"] ?? [];
    expect(events.some((e) => String(e.message ?? "").includes("SEO 79: bajo la puerta de 80 en tiktok"))).toBe(true);
    const workerEvents = db.tables["worker_events"] ?? [];
    expect(workerEvents.some((e) => String(e.message ?? "").includes("SEO 79: bajo la puerta de 80 en tiktok"))).toBe(true);
  });

  test("Worker parado (/parar): no corre el ciclo ni el auto-enqueue", async () => {
    const db = new FakeD1();
    seedAccount(db);
    seedSettings(db, { auto_publish_top: true });
    db.seed("clips", clipRow({ id: 1 }));
    const env = makeEnv(db, new FakeR2());
    const r = await runWorkerCycle(env, "sebastian");
    expect(r.ran).toBe(false);
    expect(r.autoEnqueued).toBe(0);
    expect(db.tables["jobs"] ?? []).toHaveLength(0);
  });

  test("clip con job publish vigente no se vuelve a encolar", async () => {
    const db = new FakeD1();
    seedAccount(db);
    seedRunning(db);
    seedSettings(db, { auto_publish_top: true });
    db.seed("clips", clipRow({ id: 1 }));
    seedJob(db, { id: 1, clip_id: 1, state: "scheduled", r2_key: null });
    const env = makeEnv(db, new FakeR2());
    const r = await runWorkerCycle(env, "sebastian");
    expect(r.autoEnqueued).toBe(0);
    expect((db.tables["jobs"] ?? []).filter((j) => j.clip_id === 1)).toHaveLength(1);
  });
});

async function seedMetaAccount(db: FakeD1): Promise<void> {
  db.seed("api_accounts", {
    id: 12,
    tenant_id: "sebastian",
    platform: "meta",
    language: "es",
    label: "Meta ES",
    credential: "",
    credential_enc: await encryptCredential("META-TOKEN-TEST", KEY, "sebastian"),
    extra: "",
    extra_enc: await encryptCredential(
      JSON.stringify({ ig_user_id: "17841425387646573", page_id: "1346242648582758" }),
      KEY,
      "sebastian",
    ),
    status: "conectada",
    note: "",
    created_at: 1,
    verified_at: 2000,
    verify_ok: 1,
    verify_message: "ok",
    first_published_at: Date.now() - 40 * 86400_000,
    published_count: 20,
    strikes: 0,
    maturity: "establecida",
    maturity_checked_day: santiagoDayKey(),
  });
}

// ---------------------------------------------------------------------------
// Corrección 2026-11-19 (0031): vídeos borrados y medios que no se ven
// ---------------------------------------------------------------------------
describe("corrección analíticas 2026-11-19: borrados y medios que no cargan", () => {
  test("YouTube: un vídeo ausente en la respuesta 200 se marca borrado y deja de contar como publicado", async () => {
    const db = new FakeD1();
    await seedYoutubeAccount(db);
    db.seed("clips", clipRow({ id: 1 }));
    db.seed("clips", clipRow({ id: 2, title: "Clip borrado" }));
    const now = Date.now();
    for (const [jobId, clipId, pid] of [
      [50, 1, "YT1"],
      [51, 2, "YT2"],
    ] as const) {
      db.seed("jobs", {
        id: jobId, tenant_id: "sebastian", kind: "publish", clip_id: clipId, platform: "youtube_shorts",
        api_account_id: 11, state: "posted", platform_id: pid,
        evidence_url: `https://www.youtube.com/shorts/${pid}`, language: "es",
        posted_day: santiagoDayKey(), attempts: 0, last_error: null, retryable: 1,
        next_run_at: null, created_at: 1000, updated_at: now,
      });
    }
    globalThis.fetch = (async (url: unknown) => {
      const u = String(url);
      if (u.includes("oauth2.googleapis.com/token")) return jsonResponse(200, { access_token: "AT-TEST" });
      if (u.includes("youtube/v3/videos")) {
        // YT2 no viene: borrado o privado (caso clip 83, TuGUDv75_NQ).
        return jsonResponse(200, { items: [{ id: "YT1", statistics: { viewCount: "100", likeCount: "1", commentCount: "0" } }] });
      }
      throw new Error(`fetch sin mock: ${u}`);
    }) as typeof fetch;
    const env = makeEnv(db, new FakeR2());
    const s = await collectAnalytics(env, "sebastian");
    expect(s.ran).toBe(true);
    expect(s.collected).toBe(1);
    const jobs = db.tables["jobs"] ?? [];
    const deleted = jobs.find((j) => j.clip_id === 2);
    expect(typeof deleted?.deleted_at).toBe("number");
    const alive = jobs.find((j) => j.clip_id === 1);
    expect(alive?.deleted_at ?? null).toBeNull();
    // listPostedJobs ya no lo cuenta: publicados YT ES = 1, no 2.
    const posted = await listPostedJobs(db as never, "sebastian");
    expect(posted.map((j) => j.clip_id)).toEqual([1]);
  });

  test("TikTok: un vídeo ausente en la respuesta se marca borrado", async () => {
    const db = new FakeD1();
    await seedTikTokAccount(db);
    db.seed("clips", clipRow({ id: 1 }));
    db.seed("clips", clipRow({ id: 2, title: "Clip borrado" }));
    const now = Date.now();
    for (const [jobId, clipId, vid] of [
      [50, 1, "777777"],
      [51, 2, "888888"],
    ] as const) {
      db.seed("jobs", {
        id: jobId, tenant_id: "sebastian", kind: "publish", clip_id: clipId, platform: "tiktok",
        api_account_id: 10, state: "posted", platform_id: `z-${vid}`,
        evidence_url: `https://www.tiktok.com/@elclipdegta6/video/${vid}`, language: "es",
        posted_day: santiagoDayKey(), attempts: 0, last_error: null, retryable: 1,
        next_run_at: null, created_at: 1000, updated_at: now,
      });
    }
    globalThis.fetch = (async () => jsonResponse(200, {
      data: { videos: [{ id: "777777", view_count: 100, like_count: 1, comment_count: 2, share_count: 3 }] },
    })) as typeof fetch;
    const env = makeEnv(db, new FakeR2());
    const s = await collectAnalytics(env, "sebastian");
    expect(s.ran).toBe(true);
    expect(s.collected).toBe(1);
    const jobs = db.tables["jobs"] ?? [];
    expect(typeof jobs.find((j) => j.clip_id === 2)?.deleted_at).toBe("number");
    expect(jobs.find((j) => j.clip_id === 1)?.deleted_at ?? null).toBeNull();
  });

  test("Instagram: un medio que no carga no tumba la medición del resto (caso clip 7 / clip 15)", async () => {
    const db = new FakeD1();
    await seedMetaAccount(db);
    db.seed("clips", clipRow({ id: 1, title: "Clip malo" }));
    db.seed("clips", clipRow({ id: 2, title: "Clip bueno" }));
    const now = Date.now();
    // El malo procesa PRIMERO (updated_at mayor): con el código viejo
    // abortaba el grupo y el bueno nunca se medía.
    db.seed("jobs", {
      id: 50, tenant_id: "sebastian", kind: "publish", clip_id: 1, platform: "instagram_reels",
      api_account_id: 12, state: "posted", platform_id: "MEDIA_BAD",
      evidence_url: "https://www.instagram.com/reel/MEDIA_BAD/", language: "es",
      posted_day: santiagoDayKey(), attempts: 0, last_error: null, retryable: 1,
      next_run_at: null, created_at: 1000, updated_at: now + 1000,
    });
    db.seed("jobs", {
      id: 51, tenant_id: "sebastian", kind: "publish", clip_id: 2, platform: "instagram_reels",
      api_account_id: 12, state: "posted", platform_id: "MEDIA_OK",
      evidence_url: "https://www.instagram.com/reel/MEDIA_OK/", language: "es",
      posted_day: santiagoDayKey(), attempts: 0, last_error: null, retryable: 1,
      next_run_at: null, created_at: 1000, updated_at: now,
    });
    globalThis.fetch = (async (url: unknown) => {
      const u = String(url);
      if (u.includes("MEDIA_BAD")) {
        return jsonResponse(400, { error: { code: 100, message: "Unsupported get request. Object with ID 'MEDIA_BAD' does not exist, cannot be loaded due to missing permissions, or does not support this operation." } });
      }
      if (u.includes("MEDIA_OK")) {
        return jsonResponse(200, {
          data: [
            { name: "views", values: [{ value: 1000 }] },
            { name: "ig_reels_avg_watch_time", values: [{ value: 20000 }] },
          ],
        });
      }
      throw new Error(`fetch sin mock: ${u}`);
    }) as typeof fetch;
    const env = makeEnv(db, new FakeR2());
    const s = await collectAnalytics(env, "sebastian");
    expect(s.ran).toBe(true);
    expect(s.collected).toBe(1);
    const stats = db.tables["clip_stats"] ?? [];
    expect(stats).toHaveLength(1);
    expect(stats[0]?.clip_id).toBe(2);
    expect(stats[0]?.views).toBe(1000);
    // El malo NO se marca borrado: el 400 es ambiguo (ver 0031).
    const jobs = db.tables["jobs"] ?? [];
    expect(jobs.find((j) => j.clip_id === 1)?.deleted_at ?? null).toBeNull();
    const status = db.tables["analytics_status"] ?? [];
    expect(status[0]?.status).toBe("ok");
    expect(String(status[0]?.reason ?? "")).toContain("1 clip(s) medidos.");
    expect(String(status[0]?.reason ?? "")).toContain("1 sin medir");
  });

  test("Facebook: un vídeo que falla no tumba la medición del resto", async () => {
    const db = new FakeD1();
    await seedMetaAccount(db);
    db.seed("clips", clipRow({ id: 1, title: "Clip malo" }));
    db.seed("clips", clipRow({ id: 2, title: "Clip bueno" }));
    const now = Date.now();
    db.seed("jobs", {
      id: 50, tenant_id: "sebastian", kind: "publish", clip_id: 1, platform: "facebook_reels",
      api_account_id: 12, state: "posted", platform_id: "VID_BAD",
      evidence_url: "https://www.facebook.com/reel/VID_BAD/", language: "es",
      posted_day: santiagoDayKey(), attempts: 0, last_error: null, retryable: 1,
      next_run_at: null, created_at: 1000, updated_at: now + 1000,
    });
    db.seed("jobs", {
      id: 51, tenant_id: "sebastian", kind: "publish", clip_id: 2, platform: "facebook_reels",
      api_account_id: 12, state: "posted", platform_id: "VID_OK",
      evidence_url: "https://www.facebook.com/reel/VID_OK/", language: "es",
      posted_day: santiagoDayKey(), attempts: 0, last_error: null, retryable: 1,
      next_run_at: null, created_at: 1000, updated_at: now,
    });
    globalThis.fetch = (async (url: unknown) => {
      const u = String(url);
      if (u.includes("VID_BAD")) {
        return jsonResponse(400, { error: { code: 100, message: "Unsupported get request. Object with ID 'VID_BAD' does not exist." } });
      }
      if (u.includes("video_insights")) {
        return jsonResponse(200, {
          data: [
            { name: "total_video_views", values: [{ value: 500 }] },
            { name: "total_video_complete_views", values: [{ value: 250 }] },
          ],
        });
      }
      if (u.includes("fields=comments")) {
        return jsonResponse(200, { comments: { summary: { total_count: 7 } }, shares: { count: 3 } });
      }
      throw new Error(`fetch sin mock: ${u}`);
    }) as typeof fetch;
    const env = makeEnv(db, new FakeR2());
    const s = await collectAnalytics(env, "sebastian");
    expect(s.ran).toBe(true);
    expect(s.collected).toBe(1);
    const stats = db.tables["clip_stats"] ?? [];
    expect(stats).toHaveLength(1);
    expect(stats[0]?.clip_id).toBe(2);
    expect(stats[0]?.views).toBe(500);
  });

  test("Instagram: el 400 'does not exist' aclara que el token puede no ver el medio", async () => {
    globalThis.fetch = (async () => jsonResponse(400, {
      error: { code: 100, message: "Unsupported get request. Object with ID 'X' does not exist, cannot be loaded due to missing permissions, or does not support this operation." },
    })) as typeof fetch;
    await expect(fetchInstagramStats("TOKEN", "X", 40000)).rejects.toThrow(/no puede verlo/);
  });

  test("computeAnalytics: un canal sin cuenta ni publicados no hereda el auto_status de la plataforma", async () => {
    const db = new FakeD1();
    // Solo cuentas ES de IG y FB; nada EN. Solo jobs ES.
    db.seed("accounts", { id: 5, tenant_id: "sebastian", platform: "instagram", language: "es", handle: "@elmegaclipgta6", sort: 1 });
    db.seed("accounts", { id: 6, tenant_id: "sebastian", platform: "facebook", language: "es", handle: "Elclipdegta6", sort: 2 });
    db.seed("accounts", { id: 2, tenant_id: "sebastian", platform: "youtube", language: "en", handle: "@gta6clips", sort: 3 });
    db.seed("clips", clipRow({ id: 7, title: "Clip 7" }));
    db.seed("clips", clipRow({ id: 12, title: "Clip 12", language: "en" }));
    db.seed("clips", clipRow({ id: 13, title: "Clip 13" }));
    db.seed("jobs", {
      id: 3, tenant_id: "sebastian", kind: "publish", clip_id: 7, platform: "instagram_reels",
      api_account_id: 5, state: "posted", platform_id: "18484769662119306",
      evidence_url: "https://www.instagram.com/p/Df5iORvnD_x/", language: "es",
      posted_day: santiagoDayKey(), attempts: 0, last_error: null, retryable: 1,
      next_run_at: null, created_at: 1000, updated_at: 1000,
    });
    db.seed("jobs", {
      id: 12, tenant_id: "sebastian", kind: "publish", clip_id: 13, platform: "facebook_reels",
      api_account_id: 6, state: "posted", platform_id: "1650979722719612",
      evidence_url: "https://www.facebook.com/reel/1650979722719612/", language: "es",
      posted_day: santiagoDayKey(), attempts: 0, last_error: null, retryable: 1,
      next_run_at: null, created_at: 1000, updated_at: 1000,
    });
    db.seed("jobs", {
      id: 18, tenant_id: "sebastian", kind: "publish", clip_id: 12, platform: "youtube_shorts",
      api_account_id: 2, state: "posted", platform_id: "b5uA-t5xr5Y",
      evidence_url: "https://www.youtube.com/shorts/b5uA-t5xr5Y", language: "en",
      posted_day: santiagoDayKey(), attempts: 0, last_error: null, retryable: 1,
      next_run_at: null, created_at: 1000, updated_at: 1000,
    });
    db.seed("analytics_status", {
      tenant_id: "sebastian", platform: "instagram", status: "error",
      reason: "Instagram: HTTP 400: Unsupported get request. Object with ID '18484769662119306' does not exist.",
      last_measured_at: null, updated_at: 1,
    });
    db.seed("analytics_status", {
      tenant_id: "sebastian", platform: "facebook", status: "sin_permiso",
      reason: "Facebook: sin permiso de insights (código Meta 200).",
      last_measured_at: null, updated_at: 1,
    });
    db.seed("analytics_status", {
      tenant_id: "sebastian", platform: "youtube", status: "sin_permiso",
      reason: "YouTube: permiso denegado (HTTP 403).",
      last_measured_at: null, updated_at: 1,
    });
    const env = makeEnv(db, new FakeR2());
    const { channels } = await computeAnalytics(env, "sebastian");
    const at = (platform: string, lang: string) =>
      channels.find((c) => c.platform === platform && c.language === lang)?.auto_status ?? null;
    // ES conserva su error; EN (sin cuenta ni jobs) no hereda nada.
    expect(at("instagram", "es")?.status).toBe("error");
    expect(String(at("instagram", "es")?.reason ?? "")).toContain("18484769662119306");
    expect(at("instagram", "en")).toBeNull();
    expect(at("facebook", "es")?.status).toBe("sin_permiso");
    expect(at("facebook", "en")).toBeNull();
    // Un canal EN con cuenta y publicados SÍ conserva el estado.
    expect(at("youtube", "en")?.status).toBe("sin_permiso");
    // Publicados: IG ES 1 (clip 7), FB ES 1 (clip 13), YT EN 1 (clip 12).
    const pub = (platform: string, lang: string) =>
      channels.find((c) => c.platform === platform && c.language === lang)?.published_clips ?? -1;
    expect(pub("instagram", "es")).toBe(1);
    expect(pub("instagram", "en")).toBe(0);
    expect(pub("facebook", "es")).toBe(1);
    expect(pub("youtube", "en")).toBe(1);
  });
});
