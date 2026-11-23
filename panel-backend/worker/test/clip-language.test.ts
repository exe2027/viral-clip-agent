// Tests de DOS COLAS SEPARADAS (2026-11-18, migración 0019):
// idioma del clip ("es" | "en"), puerta de idioma en publishGate y
// en POST /publish, topes por plataforma+idioma, y guarda del Worker
// (dispatchPublishStep) que nunca cruza idiomas.
// Sin red, sin D1 real: mismo mock en memoria de D1Database que
// endpoints-nuevos.test.ts / clip02.test.ts, más un R2 falso. La
// sesión se crea de verdad con createSessionToken.
import { describe, expect, test } from "bun:test";
import { createSessionToken } from "../src/auth";
import type { Env } from "../src/cloudflare";
import type { ClipRow } from "../src/db";
import { publishGate, santiagoDayKey, type GateContext, type JobRow } from "../src/jobs";
import { dispatchPublishStep } from "../src/publish/index";
import worker from "../src/index";

const SECRET = Buffer.from("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb").toString("base64"); // SOLO TEST

type Row = Record<string, unknown>;

// --- Mock D1 en memoria (copia del de endpoints-nuevos.test.ts) ---
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
    // Igualdad con literal (la usa el conteo de publicados de hoy:
    // state = 'posted').
    const eqLit = /^(\w+)\s*=\s*'([^']*)'$/.exec(c);
    // IS NOT NULL (la usa el fallback de r2_key en jobs).
    const isNotNull = /^(\w+)\s+IS\s+NOT\s+NULL$/i.exec(c);
    const lt = /^(\w+)\s*<\s*\?$/.exec(c);
    const gt = /^(\w+)\s*>\s*\?$/.exec(c);
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
    // Segmento LIVE primero (db.ts listQueueClips, jobs.ts dueJobs):
    // CASE WHEN <col> = 'live' THEN 0 ELSE 1 END.
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
    const m = /^SELECT\s+(.+?)\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+?))?(?:\s+ORDER BY\s+(.+?))?(?:\s+LIMIT\s+(\d+))?\s*$/is.exec(sql);
    if (!m) throw new Error(`SELECT no soportado en el mock: ${sql}`);
    const [, colsRaw, table, where, orderBy, limitRaw] = m as [string, string, string, string | undefined, string | undefined, string | undefined];
    let rows = [...(this.tables[table as string] ?? [])];
    if (where) rows = rows.filter(parseWhere(where, params));
    rows = applyOrder(rows, orderBy);
    if (limitRaw) rows = rows.slice(0, Number(limitRaw));
    const cols = (colsRaw as string).trim();
    if (cols === "*") return rows;
    // COUNT(*) AS n (conteo de publicados de hoy por plataforma+
    // idioma): el mock real no lo soporta; aquí devuelve el número
    // de filas filtradas.
    const countMatch = /^COUNT\(\*\) AS (\w+)$/i.exec(cols);
    if (countMatch) return [{ [countMatch[1] as string]: rows.length }];
    const wanted = cols.split(",").map((c) => c.trim());
    return rows.map((r) => {
      const out: Row = {};
      for (const c of wanted) out[c] = r[c];
      return out;
    });
  }

  runWrite(sql: string, params: unknown[]): { results: never[]; success: boolean; meta: { changes?: number; last_row_id?: number } } {
    const insert = /^INSERT INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)\s*$/is.exec(sql);
    if (insert) {
      const [, table, colsRaw, valsRaw] = insert as [string, string, string, string];
      const cols = colsRaw.split(",").map((c) => c.trim());
      const vals = valsRaw.split(",").map((s) => s.trim());
      const rows = (this.tables[table as string] ??= []);
      const id = rows.reduce((max, r) => Math.max(max, Number(r.id ?? 0)), 0) + 1;
      const row: Row = { id };
      // Los VALUES mezclan ? con literales ('publish', 'scheduled',
      // 0, 1, como en INSERT INTO jobs): cada ? consume el siguiente
      // parámetro; el literal se guarda tal cual. Sin esto las
      // columnas se desalineaban y la idempotencia de jobs no veía
      // los jobs ya creados.
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
      // Defaults de D1 que el mock debe emular (si no, listQueueClips
      // filtraría clips recién creados: archived = 0 en la BD real).
      if (table === "clips") {
        const defaults: Record<string, unknown> = {
          archived: 0,
          preview_seen: 0,
          file_deleted: 0,
          published_at: null,
          scheduled_for: null,
          viral: "{}",
          seo_overrides: "[]",
          publish_states: "{}",
          origin: "vod",
          video_r2_key: "",
        };
        for (const [k, v] of Object.entries(defaults)) {
          if (row[k] === undefined) row[k] = v;
        }
      }
      rows.push(row);
      return { results: [], success: true, meta: { changes: 1, last_row_id: id } };
    }
    const update = /^UPDATE\s+(\w+)\s+SET\s+(.+?)\s+WHERE\s+(.+)$/is.exec(sql);
    if (update) {
      const [, table, setRaw, where] = update as [string, string, string];
      const setCols = setRaw.split(",").map((part) => {
        const mm = /^(\w+)\s*=\s*\?$/.exec(part.trim());
        if (!mm) throw new Error(`SET no soportado en el mock: ${part}`);
        return mm[1] as string;
      });
      const setVals = params.slice(0, setCols.length);
      const whereParams = params.slice(setCols.length);
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
  async put(key: string, body: ReadableStream | Uint8Array, _opts?: unknown): Promise<{ size: number }> {
    const buf = body instanceof Uint8Array ? body : new Uint8Array(await new Response(body as ReadableStream).arrayBuffer());
    this.store.set(key, buf);
    return { size: buf.byteLength };
  }
  async get(key: string): Promise<{ body: ReadableStream; httpMetadata: { contentType: string } } | null> {
    const data = this.store.get(key);
    if (!data) return null;
    return {
      body: new ReadableStream({ start(c) { c.enqueue(data); c.close(); } }),
      httpMetadata: { contentType: "video/mp4" },
    };
  }
  async head(key: string): Promise<{ size: number } | null> {
    const data = this.store.get(key);
    return data ? { size: data.byteLength } : null;
  }
}

function makeEnv(db: FakeD1, bucket: FakeR2): Env {
  return {
    DB: db as unknown as Env["DB"],
    TENANT: "sebastian",
    CLIPS_BUCKET: bucket as unknown as Env["CLIPS_BUCKET"],
    SESSION_SECRET: SECRET,
    CREDENTIAL_ENCRYPTION_KEY: Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"),
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

async function call(
  env: Env,
  method: string,
  path: string,
  opts: { token?: string; body?: Record<string, unknown> } = {},
): Promise<{ status: number; json: Record<string, unknown> }> {
  const headers: Record<string, string> = {};
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`;
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
  opts: { token?: string; body?: Uint8Array; contentType?: string },
): Promise<Response> {
  const headers: Record<string, string> = {};
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`;
  if (opts.contentType) headers["Content-Type"] = opts.contentType;
  return worker.fetch(
    new Request(`https://worker.test${path}`, { method, headers, body: opts.body as BodyInit }),
    env,
    ctx as never,
  );
}

function clipRow(over: Row = {}): Row {
  return {
    id: 1,
    tenant_id: "sebastian",
    source_id: null,
    influencer_id: null,
    title: "Clip de prueba",
    start_sec: 100,
    end_sec: 140,
    score: 80,
    status: "aprobado",
    subtitle_style: "tiktok",
    seo_title: "",
    seo_description: "",
    seo_tags: "",
    thumb_text: "",
    platforms: JSON.stringify(["youtube_shorts", "tiktok"]),
    scheduled_for: null,
    published_at: null,
    file_deleted: 0,
    created_at: 1000,
    updated_at: 1000,
    captions: "{}",
    viral: "{}",
    seo_overrides: "[]",
    preview_seen: 0,
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


// Parchea Date.now a un instante dado; devuelve restaurar. Desde
// SIN AMONTONAR (P0 #1) la manual respeta la franja, así que los
// tests que esperan 200 en POST /publish congelan la hora dentro
// de una franja (sábado 2026-11-21 12:30 Santiago; las cuentas ES
// 1, 2 y 3 no descansan ese día).
function patchNow(ms: number): () => void {
  const real = Date.now;
  Date.now = () => ms;
  return () => { Date.now = real; };
}

const SLOT_NOW_MS = Date.UTC(2026, 10, 21, 15, 30, 0); // 12:30 Santiago

// --- Ayudas ---
function seedAccount(db: FakeD1, over: Row): void {
  db.seed("api_accounts", {
    tenant_id: "sebastian", label: "Cuenta", credential: "", credential_enc: "v1:xxx",
    extra: "", extra_enc: "", status: "conectada", note: "", created_at: 1,
    verified_at: 2000, verify_ok: 1, verify_message: "ok",
    first_published_at: null, published_count: 0, strikes: 0,
    maturity: "nueva", maturity_checked_day: null,
    ...over,
  });
}

function seedEsAccounts(db: FakeD1): void {
  seedAccount(db, { id: 1, platform: "youtube", language: "es", label: "YT ES" });
  seedAccount(db, { id: 2, platform: "tiktok", language: "es", label: "TT ES" });
  seedAccount(db, { id: 3, platform: "meta", language: "es", label: "Meta ES" });
}

function seedEnYoutube(db: FakeD1, over: Row = {}): void {
  seedAccount(db, { id: 4, platform: "youtube", language: "en", label: "YT EN", ...over });
}

// Cuenta EN establecida (40 días, 20 publicaciones): el tope de
// calentamiento no interfiere al probar topes/rolling.
const ESTABLISHED_EN = {
  first_published_at: Date.now() - 40 * 86400_000,
  published_count: 20,
  maturity: "establecida",
  maturity_checked_day: santiagoDayKey(),
};

// Clip válido de 48 s, GTA VI, viralidad 55, SEO 92 en YouTube.
function clipBody(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    title: "Clip de prueba dos colas",
    start_sec: 100,
    end_sec: 148,
    platforms: ["youtube_shorts"],
    game: "GTA VI",
    score: 55,
    seo_scores: { youtube_shorts: 92 },
    subtitles_complete: 1,
    ...over,
  };
}

// Crea el clip por API y lo deja desbloqueado (subtítulos + preview
// visto). Devuelve su id.
async function setupUnblockedClip(
  db: FakeD1, env: Env, token: string, body: Record<string, unknown>,
): Promise<number> {
  const created = await call(env, "POST", "/api/clips", { token, body });
  expect(created.status).toBe(201);
  const id = (created.json.clip as Record<string, unknown>).id as number;
  const seen = await call(env, "POST", `/api/clips/${id}/preview-seen`, { token });
  expect(seen.status).toBe(200);
  return id;
}

function gateClip(over: Partial<ClipRow> = {}): ClipRow {
  return {
    id: 1, tenant_id: "sebastian", source_id: null, influencer_id: null, title: "t",
    start_sec: 0, end_sec: 48, score: 80, status: "aprobado", subtitle_style: "tiktok",
    seo_title: "", seo_description: "", seo_tags: "", platforms: '["youtube_shorts"]',
    scheduled_for: null, published_at: null, file_deleted: 0, created_at: Date.now(),
    updated_at: Date.now(), captions: "{}", viral: '{"platforms":{"youtube_shorts":{"score":95}}}',
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

describe("POST /api/clips — idioma (0019)", () => {
  test("clip EN → 201 con language en; GET lo devuelve", async () => {
    const env = makeEnv(new FakeD1(), new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const r = await call(env, "POST", "/api/clips", { token, body: clipBody({ language: "en" }) });
    expect(r.status).toBe(201);
    expect((r.json.clip as Row).language).toBe("en");
    const id = (r.json.clip as Row).id as number;
    const got = await call(env, "GET", `/api/clips/${id}`, { token });
    expect((got.json.clip as Row).language).toBe("en");
    const list = await call(env, "GET", "/api/clips", { token });
    const found = (list.json.clips as Row[]).find((c) => c.id === id) as Row;
    expect(found.language).toBe("en");
  });

  test("language fr → 400", async () => {
    const env = makeEnv(new FakeD1(), new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const r = await call(env, "POST", "/api/clips", { token, body: clipBody({ language: "fr" }) });
    expect(r.status).toBe(400);
    expect(String(r.json.error)).toContain("es o en");
  });

  test("sin language → 201 con language es (clips heredados)", async () => {
    const env = makeEnv(new FakeD1(), new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const r = await call(env, "POST", "/api/clips", { token, body: clipBody() });
    expect(r.status).toBe(201);
    expect((r.json.clip as Row).language).toBe("es");
  });
});

describe("PATCH /api/clips/:id — idioma (0019)", () => {
  test("corrige en → es; fr → 400", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const created = await call(env, "POST", "/api/clips", { token, body: clipBody({ language: "en" }) });
    const id = (created.json.clip as Row).id as number;
    const bad = await call(env, "PATCH", `/api/clips/${id}`, { token, body: { language: "fr" } });
    expect(bad.status).toBe(400);
    const ok = await call(env, "PATCH", `/api/clips/${id}`, { token, body: { language: "es" } });
    expect(ok.status).toBe(200);
    expect((ok.json.clip as Row).language).toBe("es");
  });
});

describe("publishGate — puerta de idioma (0019)", () => {
  test("clip EN contra cuenta ES → bloqueado con motivo de idioma", () => {
    const reason = publishGate("youtube_shorts", gateCtx({
      clip: gateClip({ language: "en" }),
      accountLanguage: "es",
      accountReady: true,
    }));
    expect(reason).toContain("idioma");
    expect(reason).toContain("inglés");
    expect(reason).toContain("español");
    expect(reason).toContain("nunca se publica en el canal del otro idioma");
  });

  test("sin cuenta en el idioma del clip → bloqueado con 'sin cuenta YouTube en inglés'", () => {
    const reason = publishGate("youtube_shorts", gateCtx({
      clip: gateClip({ language: "en" }),
      accountLanguage: null,
      accountReady: false,
    }));
    expect(reason).toContain("sin cuenta YouTube en inglés");
  });

  test("clip EN con cuenta EN verificada → pasa la puerta de idioma", () => {
    expect(publishGate("youtube_shorts", gateCtx({
      clip: gateClip({ language: "en" }),
      accountLanguage: "en",
      accountReady: true,
    }))).toBeNull();
  });

  test("cuenta en el idioma pero sin credencial verificada → bloqueado", () => {
    const reason = publishGate("youtube_shorts", gateCtx({
      clip: gateClip({ language: "en" }),
      accountLanguage: "en",
      accountReady: false,
    }));
    expect(reason).toContain("sin credencial verificada");
  });

  test("clip heredado sin idioma → es: con cuenta ES pasa", () => {
    const clip = gateClip({ language: "es" });
    delete (clip as unknown as Record<string, unknown>).language;
    expect(publishGate("youtube_shorts", gateCtx({
      clip, accountLanguage: "es", accountReady: true,
    }))).toBeNull();
  });
});

describe("POST /api/clips/:id/publish — cruce de idiomas (0019)", () => {
  test("clip EN con solo cuenta ES verificada → 400 con motivo de idioma, sin jobs", async () => {
    const db = new FakeD1();
    seedEsAccounts(db);
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const id = await setupUnblockedClip(db, env, token, clipBody({ language: "en" }));
    const r = await call(env, "POST", `/api/clips/${id}/publish`, { token });
    expect(r.status).toBe(400);
    expect(String(r.json.error)).toContain("sin cuenta YouTube en inglés");
    expect(db.tables["jobs"] ?? []).toHaveLength(0);
  });

  test("clip EN con cuenta EN verificada → 200, 1 job con language en", async () => {
    const db = new FakeD1();
    seedEsAccounts(db);
    seedEnYoutube(db);
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const id = await setupUnblockedClip(db, env, token, clipBody({ language: "en" }));
    const restore = patchNow(SLOT_NOW_MS);
    let r: { status: number; json: Record<string, unknown> };
    try {
      r = await call(env, "POST", `/api/clips/${id}/publish`, { token });
    } finally { restore(); }
    expect(r.status).toBe(200);
    const jobs = r.json.jobs as { id: number; platform: string; state: string }[];
    expect(jobs).toHaveLength(1);
    expect(jobs[0]?.platform).toBe("youtube_shorts");
    const stored = db.tables["jobs"] ?? [];
    expect(stored).toHaveLength(1);
    expect(stored[0]?.language).toBe("en");
    expect(stored[0]?.api_account_id).toBe(4);
  });

  test("clip ES con solo cuenta ES → 200 (el cruce es solo hacia el otro idioma)", async () => {
    const db = new FakeD1();
    seedEsAccounts(db);
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const id = await setupUnblockedClip(db, env, token, clipBody({ language: "es" }));
    const restore = patchNow(SLOT_NOW_MS);
    let r: { status: number; json: Record<string, unknown> };
    try {
      r = await call(env, "POST", `/api/clips/${id}/publish`, { token });
    } finally { restore(); }
    expect(r.status).toBe(200);
    // Unión 14:10: el clip trae solo youtube_shorts pero el tenant
    // tiene las 4 cuentas ES vinculadas → 4 jobs, todos en español.
    expect((r.json.jobs as Row[]).map((j) => j.platform).sort()).toEqual(
      ["facebook_reels", "instagram_reels", "tiktok", "youtube_shorts"],
    );
    expect((db.tables["jobs"] ?? [])[0]?.language).toBe("es");
    expect((db.tables["jobs"] ?? [])[0]?.api_account_id).toBe(1);
  });
});

describe("topes por plataforma+idioma (0019)", () => {
  test("6 publicados hoy en YouTube EN no frenan el clip ES; la manual EN TAMBIÉN frena por tope (SIN AMONTONAR)", async () => {
    const db = new FakeD1();
    seedEsAccounts(db);
    seedEnYoutube(db, ESTABLISHED_EN);
    const restore = patchNow(SLOT_NOW_MS);
    const today = santiagoDayKey();
    // Tope diario YT (6, orden 2026-11-21 21:09) lleno en EN:
    // desde SIN AMONTONAR la manual EN TAMBIÉN frena por el tope
    // diario (antes pasaba con aviso); la ES no se entera del
    // tope EN y publica. updated_at hace >24 h para aislar el
    // tope diario del rolling 24 h (3): el conteo diario usa
    // posted_day, el rolling usa updated_at.
    for (let i = 0; i < 6; i++) {
      db.seed("jobs", {
        id: 10 + i, tenant_id: "sebastian", clip_id: 900 + i, platform: "youtube_shorts",
        api_account_id: 4, state: "posted", language: "en", posted_day: today,
        updated_at: Date.now() - 25 * 3600 * 1000, created_at: Date.now() - 25 * 3600 * 1000,
      });
    }
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const idEn = await setupUnblockedClip(db, env, token, clipBody({ language: "en", title: "Clip EN con tope" }));
    const manual = await call(env, "POST", `/api/clips/${idEn}/publish`, { token });
    expect(manual.status).toBe(400);
    expect(String(JSON.stringify(manual.json))).toContain("tope diario");
    // El clip ES no se entera del tope EN: publica. Unión 14:10:
    // el clip trae solo youtube_shorts pero el tenant tiene las 4
    // cuentas ES vinculadas → 4 jobs, todos en español.
    const idEs = await setupUnblockedClip(db, env, token, clipBody({ language: "es", title: "Clip ES sin tope" }));
    const ok = await call(env, "POST", `/api/clips/${idEs}/publish`, { token });
    restore();
    expect(ok.status).toBe(200);
    expect((db.tables["jobs"] ?? []).filter((j) => j.language === "es")).toHaveLength(4);
  });

  test("rolling 24 h: 6 publicados en 24 h en YouTube EN frenan hasta la manual (YouTube 6, orden 2026-11-23)", async () => {
    const db = new FakeD1();
    seedEsAccounts(db);
    seedEnYoutube(db, ESTABLISHED_EN);
    const today = santiagoDayKey();
    for (let i = 0; i < 6; i++) {
      db.seed("jobs", {
        id: 10 + i, tenant_id: "sebastian", clip_id: 900 + i, platform: "youtube_shorts",
        api_account_id: 4, state: "posted", language: "en", posted_day: today,
        updated_at: Date.now() - 3 * 3600 * 1000, created_at: Date.now() - 3 * 3600 * 1000,
      });
    }
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const idEn = await setupUnblockedClip(db, env, token, clipBody({ language: "en", title: "Clip EN con rolling lleno" }));
    const r = await call(env, "POST", `/api/clips/${idEn}/publish`, { token });
    expect(r.status).toBe(400);
    expect(String(r.json.error ?? "")).toContain("rolling 24 h");
  });

  test("rolling 24 h: 3 publicados en 24 h en YouTube EN ya NO frenan (YouTube 6, orden 2026-11-23)", async () => {
    const db = new FakeD1();
    seedEsAccounts(db);
    seedEnYoutube(db, ESTABLISHED_EN);
    // Tiempos relativos a SLOT_NOW_MS (12:30, en franja): las 3
    // subidas son de hace 3 h (09:30), dentro de 24 h pero con
    // espaciamiento YouTube (2 h) ya cumplido.
    const today = santiagoDayKey(new Date(SLOT_NOW_MS));
    for (let i = 0; i < 3; i++) {
      db.seed("jobs", {
        id: 10 + i, tenant_id: "sebastian", clip_id: 900 + i, platform: "youtube_shorts",
        api_account_id: 4, state: "posted", language: "en", posted_day: today,
        updated_at: SLOT_NOW_MS - 3 * 3600 * 1000, created_at: SLOT_NOW_MS - 3 * 3600 * 1000,
      });
    }
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const idEn = await setupUnblockedClip(db, env, token, clipBody({ language: "en", title: "Clip EN con rolling a 3" }));
    const restore = patchNow(SLOT_NOW_MS);
    let r: { status: number; json: Record<string, unknown> };
    try {
      r = await call(env, "POST", `/api/clips/${idEn}/publish`, { token });
    } finally { restore(); }
    expect(r.status).toBe(200);
  });

  test("clip heredado sin idioma publica por la cuenta ES", async () => {
    const db = new FakeD1();
    seedEsAccounts(db);
    // Fila vieja: sin clave language (la migración 0019 le pone "es").
    const row = clipRow({ id: 1, preview_seen: 1, publish_blocked: 0, subtitles_complete: 1 });
    delete row.language;
    db.seed("clips", row);
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const restore = patchNow(SLOT_NOW_MS);
    let r: { status: number; json: Record<string, unknown> };
    try {
      r = await call(env, "POST", "/api/clips/1/publish", { token });
    } finally { restore(); }
    expect(r.status).toBe(200);
    expect((db.tables["jobs"] ?? [])[0]?.language).toBe("es");
  });
});

describe("dispatchPublishStep — guarda del Worker (0019)", () => {
  test("job con cuenta ES para un clip EN → draft con motivo de idioma, sin publicar", async () => {
    const db = new FakeD1();
    seedEsAccounts(db);
    db.seed("clips", clipRow({ id: 1, language: "en", preview_seen: 1, publish_blocked: 0 }));
    const bucket = new FakeR2();
    const env = makeEnv(db, bucket);
    const job: JobRow = {
      id: 1, tenant_id: "sebastian", kind: "publish", clip_id: 1, platform: "youtube_shorts",
      api_account_id: 1, state: "scheduled", idempotency_key: "k", resume_data: null,
      platform_id: null, evidence_url: null, title: "t", caption: "t",
      r2_key: "clips/sebastian/1/x.mp4", language: "en", posted_day: null,
      attempts: 0, last_error: null, retryable: 1, next_run_at: null,
      created_at: 1000, updated_at: 1000,
    };
    const result = await dispatchPublishStep(env, job);
    expect(result.mode).toBe("draft");
    expect(String((result as { reason?: string }).reason ?? "")).toContain("idioma");
    expect(String((result as { reason?: string }).reason ?? "")).toContain("nunca se publica en el canal del otro idioma");
  });
});

// ---------------------------------------------------------------------------
// Reglas 14:10 y 14:17 (2026-11-21): POST /api/clips/:id/publish con
// scheduled_for y platforms. La hora se congela al sábado 2026-11-21
// 12:30 Santiago (dentro de la franja 12:00-14:00; las cuentas ES 1,
// 2 y 3 no descansan ese día). La franja programada es 18:30 del
// mismo día ("2026-11-21T18:30:00-03:00").
// ---------------------------------------------------------------------------
describe("POST /api/clips/:id/publish — programación y cuentas vinculadas (14:10/14:17)", () => {
  const NOW_MS = SLOT_NOW_MS; // sábado 2026-11-21 12:30 Santiago, dentro de franja
  const SLOT_18H_ISO = "2026-11-21T18:30:00-03:00";
  const SLOT_18H_MS = Date.UTC(2026, 10, 21, 21, 30, 0);

  async function setup(db: FakeD1, env: Env, token: string): Promise<number> {
    seedEsAccounts(db);
    return setupUnblockedClip(db, env, token, clipBody());
  }

  test("scheduled_for en franja futura → 200, jobs con ese next_run_at y scheduled_for guardado", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const id = await setup(db, env, token);
    const restore = patchNow(NOW_MS);
    try {
      const r = await call(env, "POST", `/api/clips/${id}/publish`, {
        token, body: { scheduled_for: SLOT_18H_ISO, platforms: ["youtube_shorts"] },
      });
      expect(r.status).toBe(200);
      expect(r.json.scheduled_for).toBe(new Date(SLOT_18H_MS).toISOString());
      const jobs = r.json.jobs as Row[];
      expect(jobs).toHaveLength(1);
      expect(jobs[0]?.platform).toBe("youtube_shorts");
      expect(jobs[0]?.next_run_at).toBe(SLOT_18H_MS);
      expect(jobs[0]?.state).toBe("scheduled");
      const got = await call(env, "GET", `/api/clips/${id}`, { token });
      expect((got.json.clip as Row).scheduled_for).toBe(new Date(SLOT_18H_MS).toISOString());
    } finally { restore(); }
  });

  test("scheduled_for fuera de franja → 400", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const id = await setup(db, env, token);
    const restore = patchNow(NOW_MS);
    try {
      const r = await call(env, "POST", `/api/clips/${id}/publish`, {
        token, body: { scheduled_for: "2026-11-21T15:30:00-03:00" },
      });
      expect(r.status).toBe(400);
      expect(String(r.json.error)).toMatch(/franja válida/);
    } finally { restore(); }
  });

  test("scheduled_for en el pasado → 400", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const id = await setup(db, env, token);
    const restore = patchNow(NOW_MS);
    try {
      const r = await call(env, "POST", `/api/clips/${id}/publish`, {
        token, body: { scheduled_for: "2026-11-21T11:00:00-03:00" },
      });
      expect(r.status).toBe(400);
      expect(String(r.json.error)).toMatch(/futura/);
    } finally { restore(); }
  });

  test("scheduled_for inválido → 400", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const id = await setup(db, env, token);
    const restore = patchNow(NOW_MS);
    try {
      const r = await call(env, "POST", `/api/clips/${id}/publish`, {
        token, body: { scheduled_for: "mañana a las seis" },
      });
      expect(r.status).toBe(400);
      expect(String(r.json.error)).toMatch(/válida/);
    } finally { restore(); }
  });

  test("platforms inválida → 400", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const id = await setup(db, env, token);
    const restore = patchNow(NOW_MS);
    try {
      const r = await call(env, "POST", `/api/clips/${id}/publish`, {
        token, body: { platforms: ["youtube_shorts", "onlyfans"] },
      });
      expect(r.status).toBe(400);
      expect(String(r.json.error)).toMatch(/Plataforma inválida/);
    } finally { restore(); }
  });

  test("sin platforms: clip con solo youtube_shorts pero 4 cuentas ES vinculadas → 4 jobs (unión 14:10)", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const id = await setup(db, env, token);
    const restore = patchNow(NOW_MS);
    try {
      const r = await call(env, "POST", `/api/clips/${id}/publish`, { token, body: {} });
      expect(r.status).toBe(200);
      const jobs = r.json.jobs as Row[];
      expect(jobs.map((j) => j.platform).sort()).toEqual(
        ["facebook_reels", "instagram_reels", "tiktok", "youtube_shorts"],
      );
    } finally { restore(); }
  });

  test("con platforms: solo esas plataformas aunque haya más cuentas vinculadas", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const id = await setup(db, env, token);
    const restore = patchNow(NOW_MS);
    try {
      const r = await call(env, "POST", `/api/clips/${id}/publish`, {
        token, body: { platforms: ["youtube_shorts", "tiktok"] },
      });
      expect(r.status).toBe(200);
      const jobs = r.json.jobs as Row[];
      expect(jobs.map((j) => j.platform).sort()).toEqual(["tiktok", "youtube_shorts"]);
    } finally { restore(); }
  });

  test("P0 #5 (|repubN): republish=true crea un job nuevo aunque el clip ya salió hoy en esa plataforma", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    // Cuenta TikTok ES establecida (plan 2/día: cabe una segunda
    // publicación hoy). El clip trae solo tiktok.
    seedAccount(db, { id: 2, platform: "tiktok", language: "es", label: "TT ES",
      first_published_at: Date.now() - 40 * 86400_000, published_count: 20,
      maturity: "establecida", maturity_checked_day: santiagoDayKey() });
    const id = await setupUnblockedClip(db, env, token, clipBody({ platforms: ["tiktok"] }));
    // Job original ya publicado hoy en TikTok (12:30, primera franja).
    const today = santiagoDayKey();
    db.seed("jobs", {
      id: 1, tenant_id: "sebastian", clip_id: id, platform: "tiktok",
      api_account_id: 2, state: "posted", language: "es", posted_day: today,
      idempotency_key: "key-base-falsa", created_at: Date.now() - 6 * 3600 * 1000,
      updated_at: Date.now() - 6 * 3600 * 1000,
    });
    const restore = patchNow(NOW_MS); // 12:30 Santiago, dentro de franja
    try {
      // Sin republish: frena por "mismo clip hoy".
      const noRepub = await call(env, "POST", `/api/clips/${id}/publish`, {
        token, body: { platforms: ["tiktok"] },
      });
      expect(noRepub.status).toBe(400);
      expect(String(noRepub.json.error)).toMatch(/ya se publicó hoy/);
      // Con republish: 200 con un job nuevo (key distinta a la base).
      const repub = await call(env, "POST", `/api/clips/${id}/publish`, {
        token, body: { platforms: ["tiktok"], republish: true },
      });
      expect(repub.status).toBe(200);
      const jobs = repub.json.jobs as Row[];
      expect(jobs).toHaveLength(1);
      expect(jobs[0]?.platform).toBe("tiktok");
      expect(jobs[0]?.state).toBe("scheduled");
      expect((db.tables["jobs"] ?? [])).toHaveLength(2);
    } finally { restore(); }
  });
});
