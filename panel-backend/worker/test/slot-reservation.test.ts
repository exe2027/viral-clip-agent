// Tests de regresión de las 2 fixes de NEEDS-VALIDATION run-2 §3 y §4
// (migración 0022, 2026-11-18):
// §3 run token / lease fencing: el apply de un reclamo viejo no
// pisa al reclamo vigente (sin doble markPosted ni doble
// published_count); el lease sigue siendo 300 s.
// §4 reserva atómica de cupo: dos jobs del mismo grupo
// (plataforma+idioma) en un ciclo no rebasan el plan; grupos
// distintos siguen en paralelo; un job multi-step reusa su cupo.
// Sin red (fetch stub), sin D1 real: mock en memoria de D1Database
// (copia del de security-fixes.test.ts, con dueJobs fiel — incluye
// 'processing' — y SET con `col = col + 1` y COALESCE, que usa
// markPosted) y R2 falso. Sin credenciales reales.
import { describe, expect, test, afterEach } from "bun:test";
import { encryptCredential } from "../src/crypto";
import type { Env } from "../src/cloudflare";
import type { ClipRow } from "../src/db";
import { reservePublishSlot } from "../src/gate";
import {
  claimJobForCycle,
  markDraft,
  markPosted,
  resumeJobForCycle,
  runWorkerCycle,
  santiagoDayKey,
  type JobRow,
} from "../src/jobs";

const SECRET = Buffer.from("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb").toString("base64"); // SOLO TEST
const KEY = Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"); // 32 bytes, SOLO TEST
const MP4_FAKE = new Uint8Array([0, 0, 0, 24, 102, 116, 121, 112, 1, 2, 3, 4]);

type Row = Record<string, unknown>;

// --- Mock D1 en memoria (copia del de security-fixes.test.ts) ---
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
    // Fiel al SQL real: state IN ('scheduled','processing').
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
      // SET mezcla ? con literales (claimJob: state = 'processing'),
      // aritmética (published_count = published_count + 1) y
      // COALESCE(first_published_at, ?) (markPosted).
      const appliers: Array<(r: Row) => void> = [];
      let pi = 0;
      for (const part of splitSetParts(setRaw)) {
        const p = part.trim();
        const mm = /^(\w+)\s*=\s*\?$/.exec(p);
        const lit = /^(\w+)\s*=\s*'([^']*)'$/.exec(p);
        const num = /^(\w+)\s*=\s*(-?\d+)$/.exec(p);
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

// --- Stub de fetch (adaptadores) con retardo para ver solapes ---
const realFetch = globalThis.fetch;
let maxConcurrent = 0;
let inFlight = 0;
let postIdx = 0;
const POST_IDS = ["tt_post_1", "tt_post_2", "tt_post_3"];

function stubFetch(): void {
  maxConcurrent = 0;
  inFlight = 0;
  postIdx = 0;
  globalThis.fetch = (async (input: string | URL | Request, _init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    inFlight++;
    maxConcurrent = Math.max(maxConcurrent, inFlight);
    try {
      await new Promise((r) => setTimeout(r, 10));
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
      if (url.includes("oauth2.googleapis.com/token")) return json({ access_token: "ya29.test" });
      if (url === "https://upload.test/yt-session") return json({ id: "yt_123" });
      if (url.includes("creator_info")) return json({ data: { privacy_level_options: ["PUBLIC_TO_EVERYONE"] }, error: { code: "ok" } });
      if (url.includes("/video/init/")) return json({ data: { publish_id: "pub1", upload_url: "https://upload.test/tt-chunk" }, error: { code: "ok" } });
      if (url.includes("/status/fetch/")) {
        const id = POST_IDS[postIdx++] ?? `tt_post_${postIdx}`;
        return json({ data: { status: "PUBLISH_COMPLETE", post_id: id }, error: { code: "ok" } });
      }
      return json({ error: "stub sin ruta" }, 500);
    } finally {
      inFlight--;
    }
  }) as typeof fetch;
}

afterEach(() => {
  globalThis.fetch = realFetch;
});

// --- Datos de prueba ---
function clipRow(over: Row = {}): Row {
  const platform = (over.platform as string) ?? "tiktok";
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
    platforms: JSON.stringify([platform]),
    scheduled_for: null,
    published_at: null,
    file_deleted: 0,
    created_at: 1000,
    updated_at: 1000,
    captions: "{}",
    viral: JSON.stringify({ platforms: { [platform]: { score: 95 } } }),
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
async function seedAccount(db: FakeD1, platform: "tiktok" | "youtube", id: number): Promise<void> {
  const credential = platform === "tiktok"
    ? await encryptCredential("act.test-tiktok", KEY, "sebastian")
    : await encryptCredential(JSON.stringify({ client_id: "cid", client_secret: "csec", refresh_token: "rtok" }), KEY, "sebastian");
  db.seed("api_accounts", {
    id,
    tenant_id: "sebastian",
    platform,
    language: "es",
    label: `${platform} ES`,
    credential: "",
    credential_enc: credential,
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
    resume_data: JSON.stringify({ step: "status", publishId: "pub1" }),
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

function accountRow(db: FakeD1, id: number): Row {
  return (db.tables["api_accounts"] ?? []).find((a) => a.id === id) as Row;
}

function slotRow(db: FakeD1, platform: string): Row | undefined {
  return (db.tables["publish_slots"] ?? []).find((s) => s.platform === platform);
}

// ---------------------------------------------------------------------------
// §4 — dos jobs del mismo grupo en un ciclo, plan 1
// ---------------------------------------------------------------------------
describe("§4 reserva de cupo: mismo grupo, plan 1", () => {
  test("dos jobs tiktok/es con plan 1 → solo 1 posted, el 2º se reprograma (retry) por tope/plan/espaciamiento [F2]", async () => {
    stubFetch();
    const db = new FakeD1();
    await seedAccount(db, "tiktok", 10);
    seedSettings(db, { resto_plan: 1, resto_max: 1, spacing_hours: 2 });
    seedRunning(db);
    db.seed("clips", clipRow({ id: 1 }));
    db.seed("clips", clipRow({ id: 2, platforms: JSON.stringify(["tiktok"]), viral: '{"platforms":{"tiktok":{"score":95}}}' }));
    seedJob(db, { id: 1, clip_id: 1, created_at: 1000 });
    seedJob(db, { id: 2, clip_id: 2, idempotency_key: "k2", r2_key: "clips/sebastian/2/bbb_y.mp4", created_at: 2000 });
    const bucket = new FakeR2();
    bucket.store.set("clips/sebastian/1/aaa_x.mp4", MP4_FAKE);
    bucket.store.set("clips/sebastian/2/bbb_y.mp4", MP4_FAKE);
    const env = makeEnv(db, bucket);
    const r = await runWorkerCycle(env, "sebastian");
    expect(r.ran).toBe(true);
    expect(r.processed).toBe(2);
    const posted = (db.tables["jobs"] ?? []).filter((j) => j.state === "posted");
    const retried = (db.tables["jobs"] ?? []).filter((j) => j.state === "scheduled");
    expect(posted).toHaveLength(1);
    // F2 (spec 2026-11-24): el perdedor ya no va a draft: queda
    // scheduled con next_run_at futuro (mañana, por plan cubierto)
    // y 1 intento usado; no publica.
    expect(retried).toHaveLength(1);
    expect(String(retried[0]?.last_error ?? "")).toMatch(/plan del día cubierto|tope diario|espaciamiento|1 por plataforma y franja/);
    expect(typeof retried[0]?.next_run_at).toBe("number");
    expect((retried[0]?.next_run_at as number) > Date.now()).toBe(true);
    expect(retried[0]?.retryable).toBe(1);
    // Un solo published_count: el perdedor no contó como publicado.
    expect(accountRow(db, 10).published_count).toBe(21);
    // El cupo se soltó tras publicar: no queda reserva colgada.
    expect(slotRow(db, "tiktok")?.reserved_count ?? 0).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// §4 — grupos distintos siguen en paralelo
// ---------------------------------------------------------------------------
describe("§4 grupos distintos en paralelo", () => {
  test("tiktok + youtube en el mismo ciclo: ambos publican y los dispatch se solapan", async () => {
    stubFetch();
    const db = new FakeD1();
    await seedAccount(db, "tiktok", 10);
    await seedAccount(db, "youtube", 11);
    seedSettings(db, {});
    seedRunning(db);
    db.seed("clips", clipRow({ id: 1 }));
    db.seed("clips", clipRow({ id: 2, platform: "youtube_shorts" }));
    seedJob(db, { id: 1, clip_id: 1, created_at: 1000 });
    seedJob(db, {
      id: 2, clip_id: 2, platform: "youtube_shorts", api_account_id: 11,
      idempotency_key: "k2", r2_key: "clips/sebastian/2/bbb_y.mp4",
      resume_data: JSON.stringify({ step: "upload", sessionUrl: "https://upload.test/yt-session", bytesUploaded: 0, totalBytes: MP4_FAKE.byteLength }),
      created_at: 2000,
    });
    const bucket = new FakeR2();
    bucket.store.set("clips/sebastian/1/aaa_x.mp4", MP4_FAKE);
    bucket.store.set("clips/sebastian/2/bbb_y.mp4", MP4_FAKE);
    const env = makeEnv(db, bucket);
    const r = await runWorkerCycle(env, "sebastian");
    expect(r.ran).toBe(true);
    expect(r.processed).toBe(2);
    expect(jobRow(db, 1).state).toBe("posted");
    expect(jobRow(db, 2).state).toBe("posted");
    expect(jobRow(db, 2).evidence_url).toBe("https://www.youtube.com/shorts/yt_123");
    expect(maxConcurrent).toBeGreaterThanOrEqual(2);
    expect(accountRow(db, 10).published_count).toBe(21);
    expect(accountRow(db, 11).published_count).toBe(21);
  });
});

// ---------------------------------------------------------------------------
// §3 — run token / lease fencing
// ---------------------------------------------------------------------------
describe("§3 run token: el apply del reclamo viejo no pisa al vigente", () => {
  test("re-claim con token nuevo → markPosted con el token viejo no cambia nada; con el nuevo sí, una sola vez", async () => {
    const db = new FakeD1();
    await seedAccount(db, "tiktok", 10);
    db.seed("clips", clipRow({ id: 1 }));
    seedJob(db, { id: 1, clip_id: 1 });
    // Ciclo 1 reclama (token T1).
    expect(await claimJobForCycle(db, 1, "sebastian")).toBe(true);
    const first = jobRow(db, 1);
    const token1 = first.run_token as string;
    expect(typeof token1).toBe("string");
    expect(token1).toMatch(/^[0-9a-f]{32}$/);
    expect(first.claimed_by).toBe("worker");
    // El lease vence: el ciclo 2 re-reclama (token T2).
    first.next_run_at = Date.now() - 1000;
    expect(await resumeJobForCycle(db, 1, "sebastian")).toBe(true);
    const second = jobRow(db, 1);
    const token2 = second.run_token as string;
    expect(token2).toMatch(/^[0-9a-f]{32}$/);
    expect(token2).not.toBe(token1);
    // El apply del perdedor (T1) no marca posted ni cuenta nada.
    const stale = { ...(second as unknown as JobRow), run_token: token1 };
    await markPosted(db, stale, "tt_old", "https://www.tiktok.com/@elclipdegta6/video/111");
    expect(jobRow(db, 1).state).toBe("processing");
    expect(jobRow(db, 1).evidence_url).toBeNull();
    expect(accountRow(db, 10).published_count).toBe(20);
    // El apply del ganador (T2) sí marca posted, una sola vez.
    await markPosted(db, second as unknown as JobRow, "tt_new", "https://www.tiktok.com/@elclipdegta6/video/222");
    expect(jobRow(db, 1).state).toBe("posted");
    expect(jobRow(db, 1).evidence_url).toBe("https://www.tiktok.com/@elclipdegta6/video/222");
    expect(accountRow(db, 10).published_count).toBe(21);
    // Ni siquiera un draft con el token viejo mueve la fila ya publicada.
    await markDraft(db, stale, "tarde");
    expect(jobRow(db, 1).state).toBe("posted");
    expect(accountRow(db, 10).published_count).toBe(21);
  });

  test("sin token (puente / legacy) markPosted sigue funcionando como antes", async () => {
    const db = new FakeD1();
    await seedAccount(db, "tiktok", 10);
    db.seed("clips", clipRow({ id: 1 }));
    seedJob(db, { id: 1, clip_id: 1, state: "processing", run_token: null });
    const snap = jobRow(db, 1) as unknown as JobRow;
    await markPosted(db, snap, "tt_bridge", "https://www.tiktok.com/@elclipdegta6/video/333");
    expect(jobRow(db, 1).state).toBe("posted");
    expect(accountRow(db, 10).published_count).toBe(21);
  });
});

// ---------------------------------------------------------------------------
// §3 — el lease sigue siendo 300 s
// ---------------------------------------------------------------------------
describe("§3 lease de 300 s", () => {
  test("claim y resume ponen next_run_at = ahora + 300_000 ms", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow({ id: 1 }));
    seedJob(db, { id: 1, clip_id: 1 });
    const beforeClaim = Date.now();
    expect(await claimJobForCycle(db, 1, "sebastian")).toBe(true);
    const lease1 = (jobRow(db, 1).next_run_at as number) - beforeClaim;
    expect(lease1).toBeGreaterThanOrEqual(299_000);
    expect(lease1).toBeLessThanOrEqual(301_000);
    jobRow(db, 1).next_run_at = Date.now() - 1000;
    const beforeResume = Date.now();
    expect(await resumeJobForCycle(db, 1, "sebastian")).toBe(true);
    const lease2 = (jobRow(db, 1).next_run_at as number) - beforeResume;
    expect(lease2).toBeGreaterThanOrEqual(299_000);
    expect(lease2).toBeLessThanOrEqual(301_000);
  });
});

// ---------------------------------------------------------------------------
// §4 — multi-step: el cupo se mantiene y se reusa sin duplicar
// ---------------------------------------------------------------------------
describe("§4 multi-step: el cupo se mantiene entre pasos", () => {
  test("creator → init → (reanudación) init→upload: un solo cupo reservado, slot_day de hoy", async () => {
    stubFetch();
    const db = new FakeD1();
    await seedAccount(db, "tiktok", 10);
    seedSettings(db, {});
    seedRunning(db);
    db.seed("clips", clipRow({ id: 1 }));
    // Sin resume_data: el primer dispatch hace creator_info.
    seedJob(db, { id: 1, clip_id: 1, resume_data: null });
    const bucket = new FakeR2();
    bucket.store.set("clips/sebastian/1/aaa_x.mp4", MP4_FAKE);
    const env = makeEnv(db, bucket);
    const today = santiagoDayKey();
    const r1 = await runWorkerCycle(env, "sebastian");
    expect(r1.processed).toBe(1);
    expect(jobRow(db, 1).state).toBe("processing");
    expect(JSON.parse(String(jobRow(db, 1).resume_data)).step).toBe("init");
    expect(jobRow(db, 1).slot_day).toBe(today);
    expect(slotRow(db, "tiktok")?.reserved_count).toBe(1);
    // Vence el next_run_at del paso (10 s): el ciclo 2 reanuda.
    jobRow(db, 1).next_run_at = Date.now() - 1000;
    const r2 = await runWorkerCycle(env, "sebastian");
    expect(r2.processed).toBe(1);
    expect(jobRow(db, 1).state).toBe("processing");
    expect(JSON.parse(String(jobRow(db, 1).resume_data)).step).toBe("upload");
    // Sigue un solo cupo: la reanudación reusó el hold, no tomó otro.
    expect(slotRow(db, "tiktok")?.reserved_count).toBe(1);
    expect(jobRow(db, 1).slot_day).toBe(today);
    expect((db.tables["publish_slots"] ?? []).filter((s) => s.platform === "tiktok")).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// Publicación MANUAL (2026-11-19): desde la orden SIN AMONTONAR
// (09:25, 2026-11-21) la reserva TAMBIÉN frena por tope para un job
// manual, igual que para uno automático. Antes la manual saltaba
// el tope (y tomaba el cupo igual); ese bypass ya no existe.
// ---------------------------------------------------------------------------
describe("reserva de cupo: job manual TAMBIÉN respeta el tope (SIN AMONTONAR)", () => {
  test("tope lleno (1/1): auto frena, manual TAMBIÉN frena", async () => {
    stubFetch();
    const db = new FakeD1();
    await seedAccount(db, "tiktok", 10);
    seedSettings(db, { resto_plan: 1, resto_max: 1, spacing_hours: 2 });
    // Un publicado hoy: el cupo 1/1 ya está cubierto.
    seedJob(db, { id: 1, clip_id: 1, state: "posted", posted_day: santiagoDayKey(), created_at: 1000 });
    const auto = await reservePublishSlot(db, { id: 99, tenant_id: "sebastian", platform: "tiktok", language: "es" });
    expect(auto.ok).toBe(false);
    if (!auto.ok) expect(auto.reason).toMatch(/tope diario/);
    const manual = await reservePublishSlot(db, { id: 100, tenant_id: "sebastian", platform: "tiktok", language: "es", manual: 1 });
    expect(manual.ok).toBe(false);
    if (!manual.ok) expect(manual.reason).toMatch(/tope diario/);
    expect(slotRow(db, "tiktok")?.reserved_count ?? 0).toBe(0);
  });
});
