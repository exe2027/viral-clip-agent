// Tests de regresión de los 4 hallazgos MEDIUM (2026-11-18):
// M1 re-chequeo del gate en ejecución, M2 R2 privado + URL firmada,
// M3 CSRF por Origin en peticiones con cookie, M4 reclamo atómico.
// Sin red (salvo fetch stub en el test de Instagram), sin D1 real:
// mock en memoria de D1Database (copia del de clip-language.test.ts
// + el SELECT de /api/jobs/next y el LEFT JOIN de dueJobs) y R2 falso.
// Sesiones y firmas reales (createSessionToken, signVideoUrl).
import { describe, expect, test, afterEach } from "bun:test";
import { createSessionCookie, createSessionToken } from "../src/auth";
import type { Env } from "../src/cloudflare";
import type { ClipRow } from "../src/db";
import { publishGate, runWorkerCycle, santiagoDayKey, type GateContext, type JobRow } from "../src/jobs";
import { dispatchPublishStep, type PublishContext } from "../src/publish/index";
import { instagramStep } from "../src/publish/instagram";
import { r2KeyForClip, signVideoUrl, verifySignedVideoUrl, VIDEO_URL_TTL_S } from "../src/r2";
import worker from "../src/index";

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
  async batch(): Promise<never[]> {
    return [];
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
    // Agregados MAX/MIN (retryAtForGateBlock, F2): la subida más
    // reciente / más vieja de una ventana. Sin filas, null.
    const aggMatch = /^(MAX|MIN)\((\w+)\) AS (\w+)$/i.exec(cols);
    if (aggMatch) {
      const [, fn, col, alias] = aggMatch as [string, string, string, string];
      const vals = rows.map((r) => r[col]).filter((v): v is number => typeof v === "number");
      const value = vals.length === 0 ? null : fn.toUpperCase() === "MAX" ? Math.max(...vals) : Math.min(...vals);
      return [{ [alias as string]: value }];
    }
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

const ctx = { waitUntil: () => {}, passThroughOnException: () => {} };

async function tokenFor(username: string, tenant: "sebastian" | "kaez"): Promise<string> {
  return createSessionToken(username, tenant, SECRET);
}

function cookieFor(token: string): string {
  return `panel-session=${token}`;
}

interface CallOpts {
  token?: string;
  cookie?: string;
  origin?: string;
  referer?: string;
  bridge?: boolean;
  body?: Record<string, unknown>;
}

async function call(
  env: Env,
  method: string,
  path: string,
  opts: CallOpts = {},
): Promise<{ status: number; json: Record<string, unknown> }> {
  const headers: Record<string, string> = {};
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`;
  if (opts.cookie) headers.Cookie = opts.cookie;
  if (opts.origin) headers.Origin = opts.origin;
  if (opts.referer) headers.Referer = opts.referer;
  if (opts.bridge) {
    headers["X-Bridge-Token"] = "bridge-test";
    headers["X-Bridge-Tenant"] = "sebastian";
  }
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await worker.fetch(
    new Request(`https://worker.test${path}`, {
      method,
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    }),
    env,
    ctx as never,
  );
  return { status: res.status, json: (await res.json()) as Record<string, unknown> };
}

async function callRaw(
  env: Env,
  method: string,
  path: string,
  opts: { token?: string; cookie?: string; origin?: string; body?: Uint8Array; contentType?: string },
): Promise<Response> {
  const headers: Record<string, string> = {};
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`;
  if (opts.cookie) headers.Cookie = opts.cookie;
  if (opts.origin) headers.Origin = opts.origin;
  if (opts.contentType) headers["Content-Type"] = opts.contentType;
  return worker.fetch(
    new Request(`https://worker.test${path}`, { method, headers, body: opts.body as BodyInit }),
    env,
    ctx as never,
  );
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

function dispatchJob(over: Partial<JobRow> = {}): JobRow {
  return {
    id: 1, tenant_id: "sebastian", kind: "publish", clip_id: 1, platform: "tiktok",
    api_account_id: 10, state: "scheduled", idempotency_key: "k1", resume_data: null,
    platform_id: null, evidence_url: null, title: "t", caption: "t",
    r2_key: "clips/sebastian/1/aaa_x.mp4", language: "es", posted_day: null,
    attempts: 0, last_error: null, retryable: 1, next_run_at: null,
    created_at: 1000, updated_at: 1000,
    ...over,
  };
}

const FRONT = "https://avc-em9.pages.dev";
const EVIL = "https://evil.example";

// ---------------------------------------------------------------------------
// M1 — el gate se re-evalúa en ejecución
// ---------------------------------------------------------------------------
describe("M1 gate re-check en ejecución (dispatchPublishStep)", () => {
  test("4º clip resto con 3/día ya cubierto → retry (reprogramado), no publica [F2]", async () => {
    const db = new FakeD1();
    seedAccount(db);
    db.seed("clips", clipRow({ id: 1 }));
    // 3 publicados hoy hace 3 h (espaciamiento 2 h ya cumplido):
    // el freno es el plan, no el espaciamiento.
    const today = santiagoDayKey();
    const postedAt = Date.now() - 3 * 3600_000;
    for (let i = 0; i < 3; i++) {
      db.seed("jobs", {
        id: 90 + i, tenant_id: "sebastian", clip_id: 900 + i, platform: "tiktok",
        api_account_id: 10, state: "posted", language: "es", posted_day: today,
        updated_at: postedAt, created_at: postedAt,
      });
    }
    const bucket = new FakeR2();
    bucket.store.set("clips/sebastian/1/aaa_x.mp4", MP4_FAKE);
    const env = makeEnv(db, bucket);
    const result = await dispatchPublishStep(env, dispatchJob());
    // F2 (spec 2026-11-24): el freno transitorio (rolling) ya no
    // manda a draft: reprograma al momento en que la ventana de
    // 24 h libera (el posted más viejo + 24 h).
    expect(result.mode).toBe("retry");
    // Plan 3 = rolling 3: el rolling se chequea antes que el plan (gate.ts:258),
    // así que el que frena es el rolling. El comportamiento (no publica) es el mismo.
    expect(String((result as { reason?: string }).reason ?? "")).toContain("tope rolling 24 h alcanzado");
    expect(String((result as { reason?: string }).reason ?? "")).toContain("3/3");
    expect((result as { retryAtMs?: number }).retryAtMs).toBe(postedAt + 24 * 3600_000);
  });

  test("clip archivado después de encolar → draft, no publica", async () => {
    const db = new FakeD1();
    seedAccount(db);
    db.seed("clips", clipRow({ id: 1, archived: 1 }));
    const bucket = new FakeR2();
    bucket.store.set("clips/sebastian/1/aaa_x.mp4", MP4_FAKE);
    const env = makeEnv(db, bucket);
    const result = await dispatchPublishStep(env, dispatchJob());
    expect(result.mode).toBe("draft");
    expect(String((result as { reason?: string }).reason ?? "")).toContain("clip archivado");
  });

  test("sin publicados hoy el gate pasa y llega al adaptador (sin token → draft del adaptador)", async () => {
    const db = new FakeD1();
    seedAccount(db);
    db.seed("clips", clipRow({ id: 1 }));
    const bucket = new FakeR2();
    bucket.store.set("clips/sebastian/1/aaa_x.mp4", MP4_FAKE);
    const env = makeEnv(db, bucket);
    const result = await dispatchPublishStep(env, dispatchJob());
    expect(result.mode).toBe("draft");
    // Llegó al adaptador TikTok: el motivo es de credencial, no del gate.
    expect(String((result as { reason?: string }).reason ?? "")).toContain("sin token de TikTok");
  });
});

describe("M1 gate re-check en runWorkerCycle", () => {
  test("3 clips resto encolados con 3/día cubierto: los 3 se reprograman (retry), ninguno publica [F2]", async () => {
    const db = new FakeD1();
    seedAccount(db);
    db.seed("worker_state", { tenant_id: "sebastian", running: 1, started_at: 1, last_note: "", updated_at: 1 });
    for (let i = 1; i <= 3; i++) {
      db.seed("clips", clipRow({ id: i }));
      seedJob(db, { id: i, clip_id: i, created_at: 1000 + i });
    }
    const today = santiagoDayKey();
    const postedAt = Date.now() - 3 * 3600_000;
    for (let i = 0; i < 3; i++) {
      db.seed("jobs", {
        id: 90 + i, tenant_id: "sebastian", clip_id: 900 + i, platform: "tiktok",
        api_account_id: 10, state: "posted", language: "es", posted_day: today,
        updated_at: postedAt, created_at: postedAt,
      });
    }
    const bucket = new FakeR2();
    bucket.store.set("clips/sebastian/1/aaa_x.mp4", MP4_FAKE);
    const env = makeEnv(db, bucket);
    const r = await runWorkerCycle(env, "sebastian");
    expect(r.ran).toBe(true);
    expect(r.processed).toBe(3);
    const jobs = db.tables["jobs"] ?? [];
    for (let i = 1; i <= 3; i++) {
      const j = jobs.find((x) => x.id === i);
      // F2 (spec 2026-11-24): el freno transitorio (rolling) ya no
      // manda a draft: el job queda scheduled con next_run_at = el
      // posted más viejo + 24 h, retryable=1 y 1 intento usado.
      expect(j?.state).toBe("scheduled");
      expect(j?.retryable).toBe(1);
      expect(j?.attempts).toBe(1);
      expect(j?.next_run_at).toBe(postedAt + 24 * 3600_000);
      expect(String(j?.last_error ?? "")).toContain("tope rolling 24 h alcanzado");
    }
    expect(jobs.filter((j) => j.state === "posted")).toHaveLength(3);
  });

  test("clip archivado en cola → el ciclo lo deja en draft", async () => {
    const db = new FakeD1();
    seedAccount(db);
    db.seed("worker_state", { tenant_id: "sebastian", running: 1, started_at: 1, last_note: "", updated_at: 1 });
    db.seed("clips", clipRow({ id: 1, archived: 1 }));
    seedJob(db, { id: 1, clip_id: 1 });
    const bucket = new FakeR2();
    bucket.store.set("clips/sebastian/1/aaa_x.mp4", MP4_FAKE);
    const env = makeEnv(db, bucket);
    const r = await runWorkerCycle(env, "sebastian");
    expect(r.ran).toBe(true);
    expect(r.processed).toBe(1);
    const j = (db.tables["jobs"] ?? []).find((x) => x.id === 1);
    expect(j?.state).toBe("draft");
    expect(String(j?.last_error ?? "")).toContain("clip archivado");
  });
});

// ---------------------------------------------------------------------------
// M2 — R2 privado, keys impredecibles, URL firmada
// ---------------------------------------------------------------------------
describe("M2 R2: keys impredecibles y URL firmada", () => {
  test("r2KeyForClip: dos subidas difieren y llevan 16 hex aleatorios", () => {
    const a = r2KeyForClip(7, "clip_7.mp4", "sebastian");
    const b = r2KeyForClip(7, "clip_7.mp4", "sebastian");
    expect(a).not.toBe(b);
    expect(a).toMatch(/^clips\/sebastian\/7\/[0-9a-f]{16}_clip_7\.mp4$/);
    expect(b).toMatch(/^clips\/sebastian\/7\/[0-9a-f]{16}_clip_7\.mp4$/);
    expect(a).not.toBe("clips/sebastian/7/clip_7.mp4");
  });

  test("sign/verify: redondo ok; caducada, manipulada o de otro objeto fallan", async () => {
    const env = makeEnv(new FakeD1(), new FakeR2());
    const key = "clips/sebastian/1/aaa_x.mp4";
    const url = await signVideoUrl(env, key);
    expect(url).toMatch(/^https:\/\/avc\.vikingosfoodspa\.workers\.dev\/api\/public-video\?key=/);
    expect(url).not.toContain("r2.dev");
    const u = new URL(url as string);
    expect(await verifySignedVideoUrl(env, key, Number(u.searchParams.get("exp")), u.searchParams.get("sig") ?? "")).toBe(true);
    // Caducada (ttl negativo).
    const expired = await signVideoUrl(env, key, -10);
    const ue = new URL(expired as string);
    expect(await verifySignedVideoUrl(env, key, Number(ue.searchParams.get("exp")), ue.searchParams.get("sig") ?? "")).toBe(false);
    // Firma de otro objeto.
    expect(await verifySignedVideoUrl(env, "clips/sebastian/1/bbb_y.mp4", Number(u.searchParams.get("exp")), u.searchParams.get("sig") ?? "")).toBe(false);
    // Firma manipulada.
    expect(await verifySignedVideoUrl(env, key, Number(u.searchParams.get("exp")), "AAAA")).toBe(false);
    // Key fuera de clips/ no se firma.
    expect(await signVideoUrl(env, "other/1/x.mp4")).toBeNull();
    expect(await verifySignedVideoUrl(env, "other/1/x.mp4", Date.now() / 1000 + 900, "x")).toBe(false);
    expect(VIDEO_URL_TTL_S).toBe(900);
  });

  test("GET /api/public-video: firmada → 200 con bytes; sin firma o caducada → 403", async () => {
    const db = new FakeD1();
    const bucket = new FakeR2();
    const key = "clips/sebastian/1/aaa_x.mp4";
    bucket.store.set(key, MP4_FAKE);
    const env = makeEnv(db, bucket);
    const signed = await signVideoUrl(env, key);
    const ok = await worker.fetch(new Request(signed as string), env, ctx as never);
    expect(ok.status).toBe(200);
    expect(new Uint8Array(await ok.arrayBuffer())).toEqual(MP4_FAKE);
    const u = new URL(signed as string);
    const noSig = await worker.fetch(new Request(`${u.origin}${u.pathname}?key=${encodeURIComponent(key)}&exp=${u.searchParams.get("exp")}`), env, ctx as never);
    expect(noSig.status).toBe(403);
    const expired = await signVideoUrl(env, key, -10);
    const exp = await worker.fetch(new Request(expired as string), env, ctx as never);
    expect(exp.status).toBe(403);
    const head = await worker.fetch(new Request(signed as string, { method: "HEAD" }), env, ctx as never);
    expect(head.status).toBe(200);
  });

  test("PUT /video ya no devuelve public_url: devuelve video_url con sesión", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow({ id: 1 }));
    const bucket = new FakeR2();
    const env = makeEnv(db, bucket);
    const token = await tokenFor("seba", "sebastian");
    const put = await callRaw(env, "PUT", "/api/clips/1/video?filename=test.mp4", { token, body: MP4_FAKE, contentType: "video/mp4" });
    expect(put.status).toBe(200);
    const putJson = (await put.json()) as Record<string, unknown>;
    expect("public_url" in putJson).toBe(false);
    expect(putJson.video_url).toBe("/api/clips/1/video");
    expect(String(putJson.r2_key)).toMatch(/^clips\/sebastian\/1\/[0-9a-f]{16}_test\.mp4$/);
    const video = await callRaw(env, "GET", "/api/clips/1/video", { token });
    expect(video.status).toBe(200);
    expect(new Uint8Array(await video.arrayBuffer())).toEqual(MP4_FAKE);
  });

  test("Instagram recibe la URL firmada del Worker (no r2.dev) y Meta la puede descargar", async () => {
    const realFetch = globalThis.fetch;
    try {
      const db = new FakeD1();
      const key = "clips/sebastian/1/aaa_x.mp4";
      const bucket = new FakeR2();
      bucket.store.set(key, MP4_FAKE);
      const env = makeEnv(db, bucket);
      let capturedVideoUrl = "";
      globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
        const params = new URL(url).searchParams;
        capturedVideoUrl = params.get("video_url") ?? "";
        return new Response(JSON.stringify({ id: "cont_1" }), { status: 200, headers: { "Content-Type": "application/json" } });
      }) as typeof fetch;
      const job: JobRow = dispatchJob({ platform: "instagram_reels", api_account_id: null });
      const ctxIg: PublishContext = {
        job: { ...job, resumeData: null },
        creds: { instagram: "EAATestToken", igUserId: "17841457001703774", pageId: "1346242648582758" },
        r2Size: MP4_FAKE.byteLength,
      };
      const r = await instagramStep(ctxIg, env);
      expect(r.mode).toBe("processing");
      expect(capturedVideoUrl).toMatch(/^https:\/\/avc\.vikingosfoodspa\.workers\.dev\/api\/public-video\?key=/);
      expect(capturedVideoUrl).toContain("sig=");
      expect(capturedVideoUrl).not.toContain("r2.dev");
      // Meta descarga la URL firmada sin sesión: 200 con los bytes.
      const dl = await worker.fetch(new Request(capturedVideoUrl), env, ctx as never);
      expect(dl.status).toBe(200);
      expect(new Uint8Array(await dl.arrayBuffer())).toEqual(MP4_FAKE);
    } finally {
      globalThis.fetch = realFetch;
    }
  });
});

// ---------------------------------------------------------------------------
// M3 — CSRF por Origin en peticiones con cookie
// ---------------------------------------------------------------------------
describe("M3 CSRF: Origin en peticiones con cookie", () => {
  test("POST cross-origin con cookie válida → 403 y el token sigue vivo", async () => {
    const env = makeEnv(new FakeD1(), new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const cookie = cookieFor(token);
    const r = await call(env, "POST", "/api/auth/logout", { cookie, origin: EVIL });
    expect(r.status).toBe(403);
    const me = await call(env, "GET", "/api/auth/me", { cookie });
    expect(me.status).toBe(200);
    expect(me.json.tenant).toBe("sebastian");
  });

  test("POST cross-origin con Referer maligno y sin Origin → 403", async () => {
    const env = makeEnv(new FakeD1(), new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const r = await call(env, "POST", "/api/auth/logout", { cookie: cookieFor(token), referer: EVIL });
    expect(r.status).toBe(403);
  });

  test("POST desde el front con cookie → 200 y el token queda revocado", async () => {
    const env = makeEnv(new FakeD1(), new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const cookie = cookieFor(token);
    const r = await call(env, "POST", "/api/auth/logout", { cookie, origin: FRONT });
    expect(r.status).toBe(200);
    const me = await call(env, "GET", "/api/auth/me", { cookie });
    expect(me.status).toBe(401);
  });

  test("POST desde el front con Referer (sin Origin) → 200", async () => {
    const env = makeEnv(new FakeD1(), new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const r = await call(env, "POST", "/api/auth/logout", { cookie: cookieFor(token), referer: `${FRONT}/` });
    expect(r.status).toBe(200);
  });

  test("POST cross-origin con Bearer (no cookie) → 200: el Bearer no pasa por el gate", async () => {
    const env = makeEnv(new FakeD1(), new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const r = await call(env, "POST", "/api/auth/logout", { token, origin: EVIL });
    expect(r.status).toBe(200);
  });

  test("POST del puente con X-Bridge-Token y Origin maligno → pasa el gate (404, no 403)", async () => {
    const env = makeEnv(new FakeD1(), new FakeR2());
    const r = await call(env, "POST", "/api/jobs/999/complete", { bridge: true, origin: EVIL, body: { state: "failed", error: "x" } });
    expect(r.status).toBe(404);
  });

  test("GET cross-origin con cookie → 200: los GET quedan fuera del gate", async () => {
    const env = makeEnv(new FakeD1(), new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const me = await call(env, "GET", "/api/auth/me", { cookie: cookieFor(token), origin: EVIL });
    expect(me.status).toBe(200);
  });

  test("createSessionCookie emite la cookie con SameSite=None (el front es cross-origin)", async () => {
    const cookie = await createSessionCookie("seba", "sebastian", SECRET);
    expect(cookie).toContain("SameSite=None");
    expect(cookie).toContain("HttpOnly");
    expect(cookie).toContain("Secure");
  });
});

// ---------------------------------------------------------------------------
// M4 — reclamo atómico de jobs
// ---------------------------------------------------------------------------
describe("M4 reclamo atómico (GET /api/jobs/next)", () => {
  function seedOneScheduled(db: FakeD1): void {
    seedJob(db, { id: 5, clip_id: 1, created_at: 1000 });
  }

  test("dos reclamos concurrentes nunca devuelven el mismo job", async () => {
    const db = new FakeD1();
    seedOneScheduled(db);
    const env = makeEnv(db, new FakeR2());
    const [a, b] = await Promise.all([
      call(env, "GET", "/api/jobs/next?kind=publish", { bridge: true }),
      call(env, "GET", "/api/jobs/next?kind=publish", { bridge: true }),
    ]);
    const got = [a, b].map((r) => (r.json.job as { id: number } | null)?.id ?? null);
    expect(got.filter((id) => id === 5)).toHaveLength(1);
    expect(got).toContain(null);
    const jobs = db.tables["jobs"] ?? [];
    expect(jobs).toHaveLength(1);
    expect(jobs[0]?.state).toBe("processing");
  });

  test("segundo reclamo secuencial → null (el job ya está en processing)", async () => {
    const db = new FakeD1();
    seedOneScheduled(db);
    const env = makeEnv(db, new FakeR2());
    const first = await call(env, "GET", "/api/jobs/next?kind=publish", { bridge: true });
    expect((first.json.job as { id: number }).id).toBe(5);
    const second = await call(env, "GET", "/api/jobs/next?kind=publish", { bridge: true });
    expect(second.json.job).toBeNull();
  });

  test("sin jobs → null; dos jobs distintos → cada reclamo uno diferente", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, new FakeR2());
    const empty = await call(env, "GET", "/api/jobs/next?kind=publish", { bridge: true });
    expect(empty.json.job).toBeNull();
    seedJob(db, { id: 5, clip_id: 1, created_at: 1000 });
    seedJob(db, { id: 6, clip_id: 1, created_at: 2000 });
    const a = await call(env, "GET", "/api/jobs/next?kind=publish", { bridge: true });
    const b = await call(env, "GET", "/api/jobs/next?kind=publish", { bridge: true });
    expect((a.json.job as { id: number }).id).toBe(5);
    expect((b.json.job as { id: number }).id).toBe(6);
  });
});

// El gate en sí no cambia: las reglas (duración, SEO, topes) siguen
// siendo las de jobs.test.ts / clip-language.test.ts.
describe("publishGate sin cambios", () => {
  function gateClip(over: Partial<ClipRow> = {}): ClipRow {
    return {
      id: 1, tenant_id: "sebastian", source_id: null, influencer_id: null, title: "t",
      start_sec: 0, end_sec: 48, score: 80, status: "aprobado", subtitle_style: "tiktok",
      seo_title: "", seo_description: "", seo_tags: "", platforms: '["tiktok"]',
      scheduled_for: null, published_at: null, file_deleted: 0, created_at: Date.now(),
      updated_at: Date.now(), captions: "{}", viral: '{"platforms":{"tiktok":{"score":95}}}',
      seo_overrides: "[]", preview_seen: 1, archived: 0, origin: "vod", publish_states: "{}",
      game: "GTA VI", publish_blocked: 0, needs_human_transcription: 0,
      subtitles_complete: 1, video_r2_key: "", language: "es",
      ...over,
    } as ClipRow;
  }
  function gateCtx(over: Partial<GateContext> = {}): GateContext {
    return {
      clip: gateClip(), seoScore: 95, seoOverride: false, publishedToday: 0,
      lastPublishedAtMs: null, streamerAllowed: true, settings: {}, ...over,
    };
  }
  test("61 s, SEO < 80 y tope diario siguen frenando igual (puerta única 45/80, P0 #3)", () => {
    expect(publishGate("tiktok", gateCtx({ clip: gateClip({ end_sec: 61 }) }))).toMatch(/duración/);
    expect(publishGate("tiktok", gateCtx({ seoScore: 79 }))).toMatch(/SEO 79/);
    expect(publishGate("tiktok", gateCtx({ publishedToday: 3 }))).toMatch(/tope diario/);
  });
});
