// Tests de los fixes F1, F1b, F2, F2b, F3, F4 y F6 (spec
// 2026-11-24, arreglo del sistema de publicación).
// Sin red real (fetch con mock que tira, salvo el stub de YouTube
// del test F6), sin D1 real: FakeD1 en memoria (copia del de
// slot-reservation.test.ts, con MAX/MIN, `= NULL` en SET y el
// ORDER BY COALESCE de listPublishedClips) + R2 falso. Tiempo
// congelado con withFrozenDate (copia de analytics-auto.test.ts):
// 2026-11-19T16:00Z = 13:00 en Santiago (dentro de franja) y ese
// día NO es el día libre de acc-10/11/12 (miércoles).
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { encryptCredential } from "../src/crypto";
import type { Env } from "../src/cloudflare";
import {
  autoEnqueueTopClips,
  getAutoEnqueueState,
  resetAutoEnqueueStateForTests,
  resetQualityAlertStateForTests,
  runWorkerCycle,
} from "../src/jobs";
import { autoEnqueueAllowed } from "../src/retry";

const SECRET = Buffer.from("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb").toString("base64"); // SOLO TEST
const KEY = Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"); // 32 bytes, SOLO TEST
const MP4_FAKE = new Uint8Array([0, 0, 0, 24, 102, 116, 121, 112, 1, 2, 3, 4]);
// 2026-11-19T16:00Z = 13:00 en Santiago (franja mañana).
const FROZEN_ISO = "2026-11-19T16:00:00.000Z";
const FROZEN_MS = new Date(FROZEN_ISO).getTime();
const FROZEN_DAY = "2026-11-19";
const YESTERDAY = "2026-11-18";

type Row = Record<string, unknown>;

// --- Mock D1 en memoria (copia del de slot-reservation.test.ts) ---
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

/** Parte el SET de un UPDATE por comas sin romper paréntesis
 *  (COALESCE(col, ?) lleva una coma dentro). */
function splitSetParts(setRaw: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let cur = "";
  for (const ch of setRaw) {
    if (ch === "(") depth++;
    if (ch === ")") depth--;
    if (ch === "," && depth === 0) {
      parts.push(cur);
      cur = "";
    } else {
      cur += ch;
    }
  }
  if (cur.trim()) parts.push(cur);
  return parts;
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
        .filter((j) => j.tenant_id === tenant && (j.state === "scheduled" || j.state === "processing") && (j.next_run_at === null || j.next_run_at === undefined || (j.next_run_at as number) <= now))
        .map((j) => {
          const clip = clips.find((c) => c.id === j.clip_id && c.tenant_id === j.tenant_id);
          return { ...j, __origin: clip?.origin ?? "vod" };
        })
        .sort((a, b) => ((a.__origin === "live" ? 0 : 1) - (b.__origin === "live" ? 0 : 1)) || ((a.created_at as number) - (b.created_at as number)));
      return rows.slice(0, limit).map(({ __origin: _o, ...rest }) => rest);
    }
    // listPublishedClips (db.ts): ORDER BY COALESCE(published_at,
    // updated_at) DESC. El genérico no lo soporta: caso aparte.
    if (/FROM clips/i.test(sql) && /COALESCE\(published_at, updated_at\)/i.test(sql)) {
      const [tenant, limit] = params as [string, number];
      const rows = [...(this.tables["clips"] ?? [])]
        .filter((c) => c.tenant_id === tenant && c.archived === 0 && c.status === "publicado")
        .sort((a, b) => (((b.published_at ?? b.updated_at) as number) - ((a.published_at ?? a.updated_at) as number)));
      return rows.slice(0, limit);
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
    // Agregados MAX/MIN (retryAtForGateBlock, F2; siembra A4):
    // la subida más reciente / más vieja. Sin filas, null.
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
      const appliers: Array<(r: Row) => void> = [];
      let pi = 0;
      for (const part of splitSetParts(setRaw)) {
        const p = part.trim();
        const mm = /^(\w+)\s*=\s*\?$/.exec(p);
        const lit = /^(\w+)\s*=\s*'([^']*)'$/.exec(p);
        const num = /^(\w+)\s*=\s*(-?\d+)$/.exec(p);
        const nullLit = /^(\w+)\s*=\s*NULL$/i.exec(p);
        const arith = /^(\w+)\s*=\s*\1\s*\+\s*(\d+)$/.exec(p);
        const coalesce = /^(\w+)\s*=\s*COALESCE\(\1,\s*\?\)$/i.exec(p);
        if (mm) {
          const col = mm[1] as string;
          const v = params[pi++];
          appliers.push((r) => { r[col] = v; });
        } else if (lit) {
          const col = lit[1] as string;
          const v = lit[2] as string;
          appliers.push((r) => { r[col] = v; });
        } else if (num) {
          const col = num[1] as string;
          const v = Number(num[2]);
          appliers.push((r) => { r[col] = v; });
        } else if (nullLit) {
          const col = nullLit[1] as string;
          appliers.push((r) => { r[col] = null; });
        } else if (arith) {
          const col = arith[1] as string;
          const n = Number(arith[2]);
          appliers.push((r) => { r[col] = (typeof r[col] === "number" ? (r[col] as number) : 0) + n; });
        } else if (coalesce) {
          const col = coalesce[1] as string;
          const v = params[pi++];
          appliers.push((r) => { if (r[col] === null || r[col] === undefined) r[col] = v; });
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
        appliers.forEach((a) => a(r));
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

// Fecha congelada (copia de analytics-auto.test.ts).
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

// --- Fetch con mock: sin red (tira), salvo el stub de YouTube del F6 ---
const realFetch = globalThis.fetch;
function stubFetchThrow(): void {
  globalThis.fetch = (async () => {
    throw new Error("sin red en tests");
  }) as typeof fetch;
}
/** YouTube: el refresh de token devuelve access_token; la creación
 *  de la sesión resumable da 500 (reintentable) → el dispatch
 *  termina en error retryable → markFailed → scheduled. */
function stubFetchYoutube500(): void {
  globalThis.fetch = (async (input: string | URL | Request, _init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    if (url.includes("oauth2.googleapis.com/token")) return json({ access_token: "ya29.test" });
    if (url.includes("googleapis.com/upload/youtube")) return json({ error: { message: "boom" } }, 500);
    throw new Error(`fetch sin mock: ${url}`);
  }) as typeof fetch;
}
beforeEach(() => {
  resetAutoEnqueueStateForTests();
  resetQualityAlertStateForTests();
  stubFetchThrow();
});
afterEach(() => {
  globalThis.fetch = realFetch;
});

// --- Datos de prueba ---
const ALL_PLATFORMS = ["youtube_shorts", "tiktok", "instagram_reels", "facebook_reels"];
const ALL_SEO = JSON.stringify({
  platforms: {
    youtube_shorts: { score: 95 },
    tiktok: { score: 95 },
    instagram_reels: { score: 95 },
    facebook_reels: { score: 95 },
  },
});

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
    platforms: JSON.stringify(ALL_PLATFORMS),
    scheduled_for: null,
    published_at: null,
    file_deleted: 0,
    created_at: 1000,
    updated_at: 1000,
    captions: "{}",
    viral: ALL_SEO,
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

/** Cuenta ESTABLECIDA con credencial real (cifrada) para el adaptador. */
async function seedAccount(db: FakeD1, platform: "tiktok" | "youtube" | "meta", id: number): Promise<void> {
  const apiPlatform = platform === "youtube" ? "youtube" : platform;
  const credential = platform === "tiktok"
    ? await encryptCredential("act.test-tiktok", KEY, "sebastian")
    : platform === "youtube"
      ? await encryptCredential(JSON.stringify({ client_id: "cid", client_secret: "csec", refresh_token: "rtok" }), KEY, "sebastian")
      : await encryptCredential("META-TOKEN-TEST", KEY, "sebastian");
  const extra = platform === "meta"
    ? await encryptCredential(JSON.stringify({ ig_user_id: "17841425387646573", page_id: "1346242648582758" }), KEY, "sebastian")
    : platform === "youtube"
      ? await encryptCredential(JSON.stringify({ client_id: "cid", client_secret: "csec", refresh_token: "rtok" }), KEY, "sebastian")
      : "";
  db.seed("api_accounts", {
    id,
    tenant_id: "sebastian",
    platform: apiPlatform,
    language: "es",
    label: `${platform} ES`,
    credential: "",
    credential_enc: credential,
    extra: "",
    extra_enc: extra,
    status: "conectada",
    note: "",
    created_at: 1,
    verified_at: 2000,
    verify_ok: 1,
    verify_message: "ok",
    first_published_at: FROZEN_MS - 40 * 86400_000,
    published_count: 20,
    strikes: 0,
    maturity: "establecida",
    maturity_checked_day: FROZEN_DAY,
  });
}

async function seedAllAccounts(db: FakeD1): Promise<void> {
  await seedAccount(db, "youtube", 11);
  await seedAccount(db, "tiktok", 10);
  await seedAccount(db, "meta", 12);
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
    claimed_by: null,
    run_token: null,
    slot_day: null,
    ...over,
  });
}

function seedRunning(db: FakeD1): void {
  db.seed("worker_state", { tenant_id: "sebastian", running: 1, started_at: 1, last_note: "", updated_at: 1 });
}

function seedSettings(db: FakeD1, data: Record<string, unknown>): void {
  db.seed("settings", { tenant_id: "sebastian", data: JSON.stringify(data), updated_at: 1 });
}

function jobRow(db: FakeD1, id: number): Row {
  return (db.tables["jobs"] ?? []).find((j) => j.id === id) as Row;
}

function jobsForClip(db: FakeD1, clipId: number): Row[] {
  return (db.tables["jobs"] ?? []).filter((j) => j.clip_id === clipId);
}

// ---------------------------------------------------------------------------
// F0 — el hueco YT 00:00 es alcanzable por el auto-enqueue
// ---------------------------------------------------------------------------
describe("F0: el hueco YT 00:00 es alcanzable por el auto-enqueue", () => {
  test("un job YT encolado a las 00:30 nace con next_run_at ≈ ahora", async () => {
    const db = new FakeD1();
    await seedAccount(db, "youtube", 11);
    seedRunning(db);
    seedSettings(db, { auto_publish_top: true });
    db.seed("clips", clipRow({ id: 1, platforms: JSON.stringify(["youtube_shorts"]) }));
    const env = makeEnv(db, new FakeR2());
    // 2026-11-19T03:30Z = 00:30 en Santiago (franja madrugada).
    const frozenMidnight = new Date("2026-11-19T03:30:00.000Z").getTime();
    const r = await withFrozenDate("2026-11-19T03:30:00.000Z", () => autoEnqueueTopClips(env, "sebastian"));
    expect(r.enabled).toBe(true);
    expect(r.enqueued).toBe(1);
    const jobs = jobsForClip(db, 1);
    expect(jobs).toHaveLength(1);
    expect(jobs[0]?.platform).toBe("youtube_shorts");
    expect(jobs[0]?.next_run_at).toBe(frozenMidnight);
  });
});

// ---------------------------------------------------------------------------
// F1 — hasVigente por plataforma + posted-ever en el gate
// ---------------------------------------------------------------------------
describe("F1: hasVigente por plataforma + posted-ever en el gate", () => {
  test("clip con posted hoy solo en YT → encola TT/IG/FB (3) y no re-encola YT, escalonados 15 min", async () => {
    const db = new FakeD1();
    await seedAllAccounts(db);
    seedRunning(db);
    seedSettings(db, { auto_publish_top: true });
    db.seed("clips", clipRow({ id: 1 }));
    // Posted hoy en YT a las 11:30 (fuera de la franja 12:00-14:00,
    // para que el freno sea posted-ever, no 1-por-franja).
    db.seed("jobs", {
      id: 90, tenant_id: "sebastian", kind: "publish", clip_id: 1, platform: "youtube_shorts",
      api_account_id: 11, state: "posted", platform_id: "YT1", evidence_url: "https://www.youtube.com/shorts/YT1",
      language: "es", posted_day: FROZEN_DAY, attempts: 0, last_error: null, retryable: 1,
      next_run_at: null, created_at: FROZEN_MS - 90 * 60_000, updated_at: FROZEN_MS - 90 * 60_000,
    });
    const env = makeEnv(db, new FakeR2());
    const r = await withFrozenDate(FROZEN_ISO, () => autoEnqueueTopClips(env, "sebastian"));
    expect(r.enabled).toBe(true);
    expect(r.enqueued).toBe(3);
    const jobs = jobsForClip(db, 1).filter((j) => j.state === "scheduled");
    expect(jobs.map((j) => j.platform).sort()).toEqual(["facebook_reels", "instagram_reels", "tiktok"]);
    // Escalonado F1.3: la base es nextPlatformHourMs (por
    // plataforma, no nowMs): a las 13:00 TT y FB tienen hueco a
    // las 13:00, pero IG lo tiene a las 18:00 (YT frena y no crea
    // job). jobs.length suma +0/+15/+30 min en orden TT, IG, FB.
    const byPlatform = new Map(jobs.map((j) => [j.platform as string, j.next_run_at as number]));
    expect(byPlatform.get("tiktok")).toBe(FROZEN_MS);
    expect(byPlatform.get("instagram_reels")).toBe(FROZEN_MS + 315 * 60_000);
    expect(byPlatform.get("facebook_reels")).toBe(FROZEN_MS + 30 * 60_000);
  });

  test("clip con posted en TikTok de otro día y sin jobs hoy → no crea job de TikTok (posted-ever), sí de las demás", async () => {
    const db = new FakeD1();
    await seedAllAccounts(db);
    seedRunning(db);
    seedSettings(db, { auto_publish_top: true });
    db.seed("clips", clipRow({ id: 1 }));
    db.seed("jobs", {
      id: 90, tenant_id: "sebastian", kind: "publish", clip_id: 1, platform: "tiktok",
      api_account_id: 10, state: "posted", platform_id: "TT1", evidence_url: "https://www.tiktok.com/@x/video/TT1",
      language: "es", posted_day: YESTERDAY, attempts: 0, last_error: null, retryable: 1,
      next_run_at: null, created_at: FROZEN_MS - 24 * 3600_000, updated_at: FROZEN_MS - 24 * 3600_000,
    });
    const env = makeEnv(db, new FakeR2());
    const r = await withFrozenDate(FROZEN_ISO, () => autoEnqueueTopClips(env, "sebastian"));
    expect(r.enqueued).toBe(3);
    const jobs = jobsForClip(db, 1).filter((j) => j.state === "scheduled");
    expect(jobs.map((j) => j.platform).sort()).toEqual(["facebook_reels", "instagram_reels", "youtube_shorts"]);
  });

  test("clip con jobs scheduled en todas las plataformas → se salta (sin cambio)", async () => {
    const db = new FakeD1();
    await seedAllAccounts(db);
    seedRunning(db);
    seedSettings(db, { auto_publish_top: true });
    db.seed("clips", clipRow({ id: 1 }));
    for (let i = 0; i < ALL_PLATFORMS.length; i++) {
      seedJob(db, { id: 10 + i, clip_id: 1, platform: ALL_PLATFORMS[i] as string, state: "scheduled", next_run_at: FROZEN_MS + 3600_000, idempotency_key: `k${i}` });
    }
    const env = makeEnv(db, new FakeR2());
    const r = await withFrozenDate(FROZEN_ISO, () => autoEnqueueTopClips(env, "sebastian"));
    expect(r.enqueued).toBe(0);
    expect(jobsForClip(db, 1)).toHaveLength(4);
  });
});

// ---------------------------------------------------------------------------
// F1b — clips sellados como publicado vuelven a evaluarse
// ---------------------------------------------------------------------------
describe("F1b: clips sellados como publicado vuelven a evaluarse", () => {
  test("clip publicado con solo YT y TT/IG/FB vinculadas → encola las 3 faltantes", async () => {
    const db = new FakeD1();
    await seedAllAccounts(db);
    seedRunning(db);
    seedSettings(db, { auto_publish_top: true });
    db.seed("clips", clipRow({ id: 1, status: "publicado", platforms: JSON.stringify(["youtube_shorts"]), published_at: FROZEN_MS - 24 * 3600_000, updated_at: FROZEN_MS - 24 * 3600_000 }));
    // YT ya publicado otro día (posted-ever): no se re-encola.
    db.seed("jobs", {
      id: 90, tenant_id: "sebastian", kind: "publish", clip_id: 1, platform: "youtube_shorts",
      api_account_id: 11, state: "posted", platform_id: "YT1", evidence_url: "https://www.youtube.com/shorts/YT1",
      language: "es", posted_day: YESTERDAY, attempts: 0, last_error: null, retryable: 1,
      next_run_at: null, created_at: FROZEN_MS - 24 * 3600_000, updated_at: FROZEN_MS - 24 * 3600_000,
    });
    const env = makeEnv(db, new FakeR2());
    const r = await withFrozenDate(FROZEN_ISO, () => autoEnqueueTopClips(env, "sebastian"));
    expect(r.enqueued).toBe(3);
    const jobs = jobsForClip(db, 1).filter((j) => j.state === "scheduled");
    expect(jobs.map((j) => j.platform).sort()).toEqual(["facebook_reels", "instagram_reels", "tiktok"]);
  });

  test("clip publicado con las 4 plataformas → se salta", async () => {
    const db = new FakeD1();
    await seedAllAccounts(db);
    seedRunning(db);
    seedSettings(db, { auto_publish_top: true });
    db.seed("clips", clipRow({ id: 1, status: "publicado", published_at: FROZEN_MS - 24 * 3600_000, updated_at: FROZEN_MS - 24 * 3600_000 }));
    for (let i = 0; i < ALL_PLATFORMS.length; i++) {
      db.seed("jobs", {
        id: 90 + i, tenant_id: "sebastian", kind: "publish", clip_id: 1, platform: ALL_PLATFORMS[i] as string,
        api_account_id: 10, state: "posted", platform_id: `P${i}`, evidence_url: `https://example.com/${i}`,
        language: "es", posted_day: YESTERDAY, attempts: 0, last_error: null, retryable: 1,
        next_run_at: null, created_at: FROZEN_MS - 24 * 3600_000, updated_at: FROZEN_MS - 24 * 3600_000,
      });
    }
    const env = makeEnv(db, new FakeR2());
    const r = await withFrozenDate(FROZEN_ISO, () => autoEnqueueTopClips(env, "sebastian"));
    expect(r.enqueued).toBe(0);
    expect(jobsForClip(db, 1).filter((j) => j.state === "scheduled")).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// F2 — el freno transitorio en ejecución reprograma, no manda a draft
// ---------------------------------------------------------------------------
describe("F2: el freno transitorio en ejecución reprograma, no manda a draft", () => {
  test("(a) job frenado por espaciamiento → scheduled con next_run_at = última subida posted + spacing (no la marca de la reserva)", async () => {
    const db = new FakeD1();
    await seedAccount(db, "youtube", 11);
    seedRunning(db);
    db.seed("clips", clipRow({ id: 1, platforms: JSON.stringify(["youtube_shorts"]) }));
    // Subida posted en YT a las 11:30 (hace 90 min < 2 h de spacing).
    const lastPostedAt = FROZEN_MS - 90 * 60_000;
    db.seed("jobs", {
      id: 90, tenant_id: "sebastian", kind: "publish", clip_id: 900, platform: "youtube_shorts",
      api_account_id: 11, state: "posted", platform_id: "YT9", evidence_url: "https://www.youtube.com/shorts/YT9",
      language: "es", posted_day: FROZEN_DAY, attempts: 0, last_error: null, retryable: 1,
      next_run_at: null, created_at: lastPostedAt, updated_at: lastPostedAt,
    });
    // Reserva de cupo con marca MÁS RECIENTE (12:50): el retryAt
    // NO puede ser la marca de la reserva, sino la subida posted.
    db.seed("publish_slots", {
      tenant_id: "sebastian", platform: "youtube_shorts", language: "es", day: FROZEN_DAY,
      reserved_count: 1, updated_at: FROZEN_MS - 10 * 60_000,
    });
    seedJob(db, { id: 1, clip_id: 1, platform: "youtube_shorts", api_account_id: 11, r2_key: "clips/sebastian/1/aaa_x.mp4" });
    const bucket = new FakeR2();
    bucket.store.set("clips/sebastian/1/aaa_x.mp4", MP4_FAKE);
    const env = makeEnv(db, bucket);
    const r = await withFrozenDate(FROZEN_ISO, () => runWorkerCycle(env, "sebastian"));
    expect(r.ran).toBe(true);
    expect(r.processed).toBe(1);
    const j = jobRow(db, 1);
    expect(j?.state).toBe("scheduled");
    expect(j?.retryable).toBe(1);
    expect(j?.attempts).toBe(1);
    // 11:30 + 2 h = 13:30 (no 12:50 + 2 h = 14:50, la reserva).
    expect(j?.next_run_at).toBe(lastPostedAt + 2 * 3600_000);
    expect(String(j?.last_error ?? "")).toContain("espaciamiento: menos de 2 h");
  });

  test("(b) job frenado por calidad → draft con retryable=0 (sin cambio)", async () => {
    const db = new FakeD1();
    await seedAccount(db, "youtube", 11);
    seedRunning(db);
    db.seed("clips", clipRow({
      id: 1,
      platforms: JSON.stringify(["youtube_shorts"]),
      viral: JSON.stringify({ platforms: { youtube_shorts: { score: 95 } }, quality_gate: { decision: "rechazado", pasa: false, fallos: ["sin_cara"], motivo: "sin cara visible" } }),
    }));
    seedJob(db, { id: 1, clip_id: 1, platform: "youtube_shorts", api_account_id: 11, r2_key: "clips/sebastian/1/aaa_x.mp4" });
    const bucket = new FakeR2();
    bucket.store.set("clips/sebastian/1/aaa_x.mp4", MP4_FAKE);
    const env = makeEnv(db, bucket);
    const r = await withFrozenDate(FROZEN_ISO, () => runWorkerCycle(env, "sebastian"));
    expect(r.ran).toBe(true);
    expect(r.processed).toBe(1);
    const j = jobRow(db, 1);
    expect(j?.state).toBe("draft");
    expect(j?.retryable).toBe(0);
    expect(String(j?.last_error ?? "")).toContain("calidad: sin cara visible");
  });

  test("(c) job con 3 intentos hoy frenado por espaciamiento → draft (tope de intentos)", async () => {
    const db = new FakeD1();
    await seedAccount(db, "youtube", 11);
    seedRunning(db);
    db.seed("clips", clipRow({ id: 1, platforms: JSON.stringify(["youtube_shorts"]) }));
    const lastPostedAt = FROZEN_MS - 90 * 60_000;
    db.seed("jobs", {
      id: 90, tenant_id: "sebastian", kind: "publish", clip_id: 900, platform: "youtube_shorts",
      api_account_id: 11, state: "posted", platform_id: "YT9", evidence_url: "https://www.youtube.com/shorts/YT9",
      language: "es", posted_day: FROZEN_DAY, attempts: 0, last_error: null, retryable: 1,
      next_run_at: null, created_at: lastPostedAt, updated_at: lastPostedAt,
    });
    // 3 intentos ya usados hoy (updated_at explícito: retriesToday
    // deriva de él).
    seedJob(db, { id: 1, clip_id: 1, platform: "youtube_shorts", api_account_id: 11, r2_key: "clips/sebastian/1/aaa_x.mp4", attempts: 3, updated_at: FROZEN_MS });
    const bucket = new FakeR2();
    bucket.store.set("clips/sebastian/1/aaa_x.mp4", MP4_FAKE);
    const env = makeEnv(db, bucket);
    const r = await withFrozenDate(FROZEN_ISO, () => runWorkerCycle(env, "sebastian"));
    expect(r.ran).toBe(true);
    const j = jobRow(db, 1);
    expect(j?.state).toBe("draft");
    expect(j?.retryable).toBe(0);
    expect(String(j?.last_error ?? "")).toContain("tope de reintentos por hoy");
  });
});

// ---------------------------------------------------------------------------
// F2b — el 15 min también ve a los hermanos en processing
// ---------------------------------------------------------------------------
describe("F2b: el 15 min también ve a los hermanos en processing", () => {
  test("job YT en processing y otro debido en IG 5 min después → el de IG frena por 15 min y se reprograma a +15 min", async () => {
    const db = new FakeD1();
    await seedAccount(db, "youtube", 11);
    await seedAccount(db, "meta", 12);
    seedRunning(db);
    db.seed("clips", clipRow({ id: 1, platforms: JSON.stringify(["youtube_shorts", "instagram_reels"]) }));
    // Hermano en YT en processing desde las 13:00 (reclamado por
    // su grupo, con next_run_at en el futuro para que dueJobs no
    // lo tome en este ciclo).
    seedJob(db, {
      id: 1, clip_id: 1, platform: "youtube_shorts", api_account_id: 11, state: "processing",
      run_token: "tok-a", next_run_at: FROZEN_MS + 300_000, updated_at: FROZEN_MS, created_at: FROZEN_MS - 60_000,
      r2_key: "clips/sebastian/1/aaa_x.mp4", idempotency_key: "k-yt",
    });
    // Job debido en IG.
    seedJob(db, {
      id: 2, clip_id: 1, platform: "instagram_reels", api_account_id: 12, state: "scheduled",
      next_run_at: null, updated_at: FROZEN_MS - 5 * 60_000, created_at: FROZEN_MS - 5 * 60_000,
      r2_key: "clips/sebastian/1/aaa_x.mp4", idempotency_key: "k-ig",
    });
    const bucket = new FakeR2();
    bucket.store.set("clips/sebastian/1/aaa_x.mp4", MP4_FAKE);
    const env = makeEnv(db, bucket);
    const r = await withFrozenDate(FROZEN_ISO, () => runWorkerCycle(env, "sebastian"));
    expect(r.ran).toBe(true);
    expect(r.processed).toBe(1);
    const j = jobRow(db, 2);
    expect(j?.state).toBe("scheduled");
    expect(j?.retryable).toBe(1);
    // 13:00 (última subida del clip en otra plataforma) + 15 min.
    expect(j?.next_run_at).toBe(FROZEN_MS + 15 * 60_000);
    expect(String(j?.last_error ?? "")).toContain("mismo clip en otra plataforma hace 0 min");
    // El hermano sigue en processing, sin tocar.
    expect(jobRow(db, 1)?.state).toBe("processing");
  });
});

// ---------------------------------------------------------------------------
// F3 — el freno A4 solo cuenta bloqueos duros
// ---------------------------------------------------------------------------
describe("F3: el freno A4 solo cuenta bloqueos duros", () => {
  test("todas las plataformas frenadas solo por espaciamiento durante 3 ciclos → al 4.º sigue permitiendo (0 intentos A4)", async () => {
    const db = new FakeD1();
    await seedAllAccounts(db);
    seedRunning(db);
    seedSettings(db, { auto_publish_top: true });
    db.seed("clips", clipRow({ id: 1 }));
    // Una subida posted en cada plataforma a las 11:30 (hace 90
    // min: < 2 h YT y < 3 h resto) y fuera de la franja actual
    // (para que el freno sea espaciamiento, no 1-por-franja).
    for (let i = 0; i < ALL_PLATFORMS.length; i++) {
      db.seed("jobs", {
        id: 90 + i, tenant_id: "sebastian", kind: "publish", clip_id: 900 + i, platform: ALL_PLATFORMS[i] as string,
        api_account_id: 10, state: "posted", platform_id: `P${i}`, evidence_url: `https://example.com/${i}`,
        language: "es", posted_day: FROZEN_DAY, attempts: 0, last_error: null, retryable: 1,
        next_run_at: null, created_at: FROZEN_MS - 90 * 60_000, updated_at: FROZEN_MS - 90 * 60_000,
      });
    }
    const env = makeEnv(db, new FakeR2());
    // 4 ciclos con el reloj avanzando (como exige el backoff si
    // hubiera intentos): T, T+1 min, T+6 min, T+21 min.
    const times = [FROZEN_MS, FROZEN_MS + 60_000, FROZEN_MS + 6 * 60_000, FROZEN_MS + 21 * 60_000];
    for (const t of times) {
      const iso = new Date(t).toISOString();
      const r = await withFrozenDate(iso, () => autoEnqueueTopClips(env, "sebastian"));
      expect(r.enabled).toBe(true);
      expect(r.enqueued).toBe(0);
    }
    expect(jobsForClip(db, 1)).toHaveLength(0);
    // Invariante F3: el clip NUNCA se aparca por frenos
    // transitorios. El contador puede quedar en 1 (no en 0): la
    // siembra pre-existente (seedAutoEnqueueStateFromReads) lee
    // los eventos clip_events "Publicación frenada" del ciclo 1
    // (step 'publicado', actor 'worker') y asume 1 intento ya
    // hecho — aproximación documentada que capa en 1 (la siembra
    // no se repite con estado en memoria) y nunca llega a 3.
    const st = getAutoEnqueueState(1, FROZEN_DAY);
    expect(st?.attemptsToday ?? 0).toBeLessThanOrEqual(1);
    expect(autoEnqueueAllowed(st ?? { attemptsToday: 0, lastAttemptMs: null }, FROZEN_MS + 21 * 60_000).allowed).toBe(true);
  });

  test("mismo escenario con bloqueo por calidad → al 4.º ciclo está aparcado (3 intentos)", async () => {
    const db = new FakeD1();
    await seedAllAccounts(db);
    seedRunning(db);
    seedSettings(db, { auto_publish_top: true });
    db.seed("clips", clipRow({
      id: 1,
      viral: JSON.stringify({
        platforms: { youtube_shorts: { score: 95 }, tiktok: { score: 95 }, instagram_reels: { score: 95 }, facebook_reels: { score: 95 } },
        quality_gate: { decision: "rechazado", pasa: false, fallos: ["sin_cara"], motivo: "sin cara visible" },
      }),
    }));
    const env = makeEnv(db, new FakeR2());
    const times = [FROZEN_MS, FROZEN_MS + 60_000, FROZEN_MS + 6 * 60_000, FROZEN_MS + 21 * 60_000];
    for (const t of times) {
      const iso = new Date(t).toISOString();
      const r = await withFrozenDate(iso, () => autoEnqueueTopClips(env, "sebastian"));
      expect(r.enabled).toBe(true);
      expect(r.enqueued).toBe(0);
    }
    const st = getAutoEnqueueState(1, FROZEN_DAY);
    expect(st?.attemptsToday).toBe(3);
    expect(autoEnqueueAllowed(st ?? { attemptsToday: 0, lastAttemptMs: null }, FROZEN_MS + 21 * 60_000).allowed).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// F4 — el presupuesto de evaluación no lo consumen los saltos
// ---------------------------------------------------------------------------
describe("F4: el presupuesto de evaluación no lo consumen los saltos", () => {
  test("10 clips saltados por hasVigente + 1 válido en la posición 11 → el válido se encola en el mismo ciclo", async () => {
    const db = new FakeD1();
    await seedAllAccounts(db);
    seedRunning(db);
    seedSettings(db, { auto_publish_top: true });
    // 10 clips saltados (score 90: van antes en la Cola) con job
    // scheduled en LAS 4 plataformas (hasVigente los cubre por
    // completo: con un job en solo una plataforma, F1 encolaría
    // las demás vinculadas); el válido (score 80) queda en la
    // posición 11.
    for (let i = 1; i <= 10; i++) {
      db.seed("clips", clipRow({ id: i, score: 90, platforms: JSON.stringify(["tiktok"]) }));
      for (let p = 0; p < ALL_PLATFORMS.length; p++) {
        seedJob(db, { id: 100 + i * 10 + p, clip_id: i, platform: ALL_PLATFORMS[p] as string, state: "scheduled", next_run_at: FROZEN_MS + 3600_000, idempotency_key: `skip-${i}-${p}` });
      }
    }
    db.seed("clips", clipRow({ id: 11, score: 80 }));
    const env = makeEnv(db, new FakeR2());
    const r = await withFrozenDate(FROZEN_ISO, () => autoEnqueueTopClips(env, "sebastian"));
    expect(r.enabled).toBe(true);
    expect(r.enqueued).toBe(4);
    expect(jobsForClip(db, 11).filter((j) => j.state === "scheduled")).toHaveLength(4);
  });

  test("tope de escaneados 60: con 59 saltados el válido en la posición 60 se encola; con 60 saltados el de la 61 no se visita", async () => {
    for (const [skipped, shouldEnqueue] of [[59, true], [60, false]] as const) {
      const db = new FakeD1();
      await seedAllAccounts(db);
      seedRunning(db);
      seedSettings(db, { auto_publish_top: true });
      for (let i = 1; i <= skipped; i++) {
        db.seed("clips", clipRow({ id: i, score: 90, platforms: JSON.stringify(["tiktok"]) }));
        for (let p = 0; p < ALL_PLATFORMS.length; p++) {
          seedJob(db, { id: 100 + i * 10 + p, clip_id: i, platform: ALL_PLATFORMS[p] as string, state: "scheduled", next_run_at: FROZEN_MS + 3600_000, idempotency_key: `skip-${i}-${p}` });
        }
      }
      const validId = skipped + 1;
      db.seed("clips", clipRow({ id: validId, score: 80 }));
      const env = makeEnv(db, new FakeR2());
      const r = await withFrozenDate(FROZEN_ISO, () => autoEnqueueTopClips(env, "sebastian"));
      expect(r.enabled).toBe(true);
      expect(r.enqueued).toBe(shouldEnqueue ? 4 : 0);
      expect(jobsForClip(db, validId).filter((j) => j.state === "scheduled")).toHaveLength(shouldEnqueue ? 4 : 0);
    }
  });
});

// ---------------------------------------------------------------------------
// F6 — barrido de drafts transitorios en el ciclo
// ---------------------------------------------------------------------------
describe("F6: barrido de drafts transitorios en el ciclo", () => {
  test("draft de hoy con last_error transitorio y gate que ya pasa → tras el ciclo está scheduled", async () => {
    const db = new FakeD1();
    await seedAccount(db, "youtube", 11);
    seedRunning(db);
    db.seed("clips", clipRow({ id: 1, platforms: JSON.stringify(["youtube_shorts"]) }));
    seedJob(db, {
      id: 1, clip_id: 1, platform: "youtube_shorts", api_account_id: 11, state: "draft",
      last_error: "gate de publicación: espaciamiento: menos de 2 h desde la última subida en esta plataforma",
      retryable: 0, attempts: 0, updated_at: FROZEN_MS, created_at: FROZEN_MS - 3600_000,
      r2_key: "clips/sebastian/1/aaa_x.mp4",
    });
    const bucket = new FakeR2();
    bucket.store.set("clips/sebastian/1/aaa_x.mp4", MP4_FAKE);
    const env = makeEnv(db, bucket);
    // El adaptador de YouTube da 500 reintentable: el job revivido
    // se ejecuta y vuelve a scheduled por markFailed (backoff).
    stubFetchYoutube500();
    const r = await withFrozenDate(FROZEN_ISO, () => runWorkerCycle(env, "sebastian", { force: true }));
    expect(r.ran).toBe(true);
    const j = jobRow(db, 1);
    expect(j?.state).toBe("scheduled");
    expect(j?.attempts).toBe(1);
  });

  test("draft con motivo duro (calidad) → sigue en draft", async () => {
    const db = new FakeD1();
    await seedAccount(db, "youtube", 11);
    seedRunning(db);
    db.seed("clips", clipRow({ id: 1, platforms: JSON.stringify(["youtube_shorts"]) }));
    seedJob(db, {
      id: 1, clip_id: 1, platform: "youtube_shorts", api_account_id: 11, state: "draft",
      last_error: "gate de publicación: calidad: sin veredicto válido (validación pendiente_revision)",
      retryable: 0, attempts: 0, updated_at: FROZEN_MS, created_at: FROZEN_MS - 3600_000,
      r2_key: "clips/sebastian/1/aaa_x.mp4",
    });
    const env = makeEnv(db, new FakeR2());
    const r = await withFrozenDate(FROZEN_ISO, () => runWorkerCycle(env, "sebastian", { force: true }));
    expect(r.ran).toBe(true);
    const j = jobRow(db, 1);
    expect(j?.state).toBe("draft");
    expect(j?.attempts).toBe(0);
  });

  test("draft con 3 reintentos hoy → sigue en draft", async () => {
    const db = new FakeD1();
    await seedAccount(db, "youtube", 11);
    seedRunning(db);
    db.seed("clips", clipRow({ id: 1, platforms: JSON.stringify(["youtube_shorts"]) }));
    seedJob(db, {
      id: 1, clip_id: 1, platform: "youtube_shorts", api_account_id: 11, state: "draft",
      last_error: "gate de publicación: espaciamiento: menos de 2 h desde la última subida en esta plataforma",
      retryable: 0, attempts: 3, updated_at: FROZEN_MS, created_at: FROZEN_MS - 3600_000,
      r2_key: "clips/sebastian/1/aaa_x.mp4",
    });
    const env = makeEnv(db, new FakeR2());
    const r = await withFrozenDate(FROZEN_ISO, () => runWorkerCycle(env, "sebastian", { force: true }));
    expect(r.ran).toBe(true);
    const j = jobRow(db, 1);
    expect(j?.state).toBe("draft");
    expect(j?.attempts).toBe(3);
  });
});
