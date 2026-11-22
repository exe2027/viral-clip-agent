// Tests del flujo del clip 02 (2026-11-18):
// POST /api/clips con el payload EXACTO de la ficha (source_id como
// texto "2928823801", influencer_id como handle "illojuan"),
// PUT /api/clips/:id/video (la key R2 debe quedar guardada en el clip,
// migración 0016), GET /api/clips/:id/video (con fallback a jobs) y
// POST /api/clips/:id/publish con el clip bloqueado por subtítulos.
// Sin red, sin D1 real: mismo mock en memoria de D1Database que
// endpoints-nuevos.test.ts, más un R2 falso. La sesión se crea de
// verdad con createSessionToken.
import { describe, expect, test } from "bun:test";
import { createSessionToken } from "../src/auth";
import type { Env } from "../src/cloudflare";
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
    ...over,
  };
}

// Payload de la ficha del clip 02 (runs/2928823801-verify/clips/
// clip_02_t07h03m20s_s67_ciego_split_ficha.md): source_id como texto,
// influencer_id como handle. NOTA: el clip 02 real es de GTA V y está
// PAUSADO (nunca se publica); aquí el payload lleva game "GTA VI"
// porque el panel solo acepta GTA VI desde el 2026-11-18 (gate de
// categoría). Lo que se prueba es la forma del payload, no el juego
// real del clip 02.
const FICHA_CLIP_02 = {
  title: "El chat solo dice 67 y IlloJuan no entiende nada",
  start_sec: 25380,
  end_sec: 25420,
  platforms: ["youtube_shorts", "tiktok", "instagram_reels", "facebook_reels"],
  seo_title: "El chat solo dice 67 y IlloJuan no entiende nada",
  seo_description: "El chat no para de decir 67 mientras IlloJuan ve la escena de Trevor y Michael en GTA V.",
  seo_tags: ["GTAV", "GTA5", "GTA", "RockstarGames", "IlloJuan", "ElClipDeGTA6"],
  subtitle_style: "karaoke",
  subtitles_complete: false,
  needs_human_transcription: true,
  reason: "Pico de chat 25400 s: 100x 67 en 40 s, Ciego/no cuela",
  source_id: "2928823801",
  influencer_id: "illojuan",
  game: "GTA VI",
};

const MP4_FAKE = new Uint8Array([0, 0, 0, 24, 102, 116, 121, 112, 105, 115, 111, 109, 1, 2, 3, 4, 5, 6, 7, 8]);

// Congela el reloj solo durante fn (para probar publishGate de forma
// determinista, sin depender de la hora real a la que corra la suite).
// La franja ya no existe (2026-11-18): el reloj congelado solo fija
// created_at / espaciamiento. El token de sesión se crea ANTES de
// congelar (su exp es +30 días, así que sigue valiendo).
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

// Cuentas ES verificadas (0019, DOS COLAS): publicar exige la
// cuenta en el idioma del clip; sin ellas el gate frena con
// "sin cuenta … en español". Meta cubre Instagram y Facebook.
function seedEsAccounts(db: FakeD1): void {
  db.seed("api_accounts", { id: 1, tenant_id: "sebastian", platform: "youtube", language: "es", label: "YT ES", credential: "", credential_enc: "v1:xxx", extra: "", extra_enc: "", status: "conectada", note: "", created_at: 1, verified_at: 2000, verify_ok: 1, verify_message: "ok", first_published_at: null, published_count: 0, strikes: 0, maturity: "nueva", maturity_checked_day: null });
  db.seed("api_accounts", { id: 2, tenant_id: "sebastian", platform: "tiktok", language: "es", label: "TT ES", credential: "", credential_enc: "v1:yyy", extra: "", extra_enc: "", status: "conectada", note: "", created_at: 1, verified_at: 2000, verify_ok: 1, verify_message: "ok", first_published_at: null, published_count: 0, strikes: 0, maturity: "nueva", maturity_checked_day: null });
  db.seed("api_accounts", { id: 3, tenant_id: "sebastian", platform: "meta", language: "es", label: "Meta ES", credential: "", credential_enc: "v1:zzz", extra: "", extra_enc: "", status: "conectada", note: "", created_at: 1, verified_at: 2000, verify_ok: 1, verify_message: "ok", first_published_at: null, published_count: 0, strikes: 0, maturity: "nueva", maturity_checked_day: null });
}

// Prepara el clip 02 creado + MP4 subido + subtítulos confirmados +
// preview visto. Devuelve { id, key }.
async function setupUnblockedClip(): Promise<{ db: FakeD1; env: Env; token: string; id: number; key: string }> {
  const db = new FakeD1();
  seedEsAccounts(db);
  db.seed("influencers", { id: 3, tenant_id: "sebastian", name: "IlloJuan", platform: "twitch", profile_url: "", language: "es", permission: 1, active: 1, notes: "", created_at: 1000 });
  const bucket = new FakeR2();
  const env = makeEnv(db, bucket);
  const token = await tokenFor("seba", "sebastian");
  const created = await call(env, "POST", "/api/clips", { token, body: FICHA_CLIP_02 });
  const id = (created.json.clip as Record<string, unknown>).id as number;
  const put = await callRaw(env, "PUT", `/api/clips/${id}/video?filename=clip_02.mp4`, { token, body: MP4_FAKE, contentType: "video/mp4" });
  const key = ((await put.json()) as Record<string, unknown>).r2_key as string;
  const patch = await call(env, "PATCH", `/api/clips/${id}`, { token, body: { subtitles_complete: 1, needs_human_transcription: 0, publish_blocked: 0 } });
  expect(patch.status).toBe(200);
  const seen = await call(env, "POST", `/api/clips/${id}/preview-seen`, { token });
  expect(seen.status).toBe(200);
  return { db, env, token, id, key };
}

describe("POST /api/clips — clip 02 con el payload exacto de la ficha", () => {
  test("sin sesión responde 401", async () => {
    const env = makeEnv(new FakeD1(), new FakeR2());
    expect((await call(env, "POST", "/api/clips", { body: FICHA_CLIP_02 })).status).toBe(401);
  });

  test("crea el clip en_carpeta, bloqueado por subtítulos, con source_id numérico e influencer resuelto", async () => {
    const db = new FakeD1();
    db.seed("influencers", { id: 3, tenant_id: "sebastian", name: "IlloJuan", platform: "twitch", profile_url: "", language: "es", permission: 1, active: 1, notes: "", created_at: 1000 });
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const r = await call(env, "POST", "/api/clips", { token, body: FICHA_CLIP_02 });
    expect(r.status).toBe(201);
    const clip = r.json.clip as Record<string, unknown>;
    expect(clip.status).toBe("en_carpeta");
    expect(clip.title).toBe("El chat solo dice 67 y IlloJuan no entiende nada");
    expect(clip.start_sec).toBe(25380);
    expect(clip.end_sec).toBe(25420);
    expect(clip.source_id).toBe(2928823801);
    expect(clip.influencer_id).toBe(3);
    expect(clip.publish_blocked).toBe(1);
    expect(clip.needs_human_transcription).toBe(1);
    expect(clip.subtitles_complete).toBe(0);
    expect(clip.platforms).toBe(JSON.stringify(["youtube_shorts", "tiktok", "instagram_reels", "facebook_reels"]));
  });

  test("handle de influencer desconocido da 400 con el motivo", async () => {
    const env = makeEnv(new FakeD1(), new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const r = await call(env, "POST", "/api/clips", { token, body: { ...FICHA_CLIP_02, influencer_id: "alguien-mas" } });
    expect(r.status).toBe(400);
    expect(String(r.json.error)).toContain("alguien-mas");
  });

  // Regla 40-60 / 25-35 ELIMINADA 2026-11-21: un clip de 30 s ya pasa.
  test("duración de 30 s ya no da 400 (regla eliminada)", async () => {
    const db = new FakeD1();
    db.seed("influencers", { id: 3, tenant_id: "sebastian", name: "IlloJuan", platform: "twitch", profile_url: "", language: "es", permission: 1, active: 1, notes: "", created_at: 1000 });
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const r = await call(env, "POST", "/api/clips", { token, body: { ...FICHA_CLIP_02, end_sec: 25410 } });
    expect(r.status).toBe(201);
  });
});

describe("PUT /api/clips/:id/video — la key R2 queda en el clip (0016)", () => {
  test("sube el MP4, guarda video_r2_key y GET /video devuelve los bytes", async () => {
    const db = new FakeD1();
    db.seed("influencers", { id: 3, tenant_id: "sebastian", name: "IlloJuan", platform: "twitch", profile_url: "", language: "es", permission: 1, active: 1, notes: "", created_at: 1000 });
    const bucket = new FakeR2();
    const env = makeEnv(db, bucket);
    const token = await tokenFor("seba", "sebastian");
    const created = await call(env, "POST", "/api/clips", { token, body: FICHA_CLIP_02 });
    expect(created.status).toBe(201);
    const id = (created.json.clip as Record<string, unknown>).id as number;
    const put = await callRaw(env, "PUT", `/api/clips/${id}/video?filename=clip_02_t07h03m20s_s67_ciego_split_subs.mp4`, {
      token,
      body: MP4_FAKE,
      contentType: "video/mp4",
    });
    expect(put.status).toBe(200);
    const putJson = (await put.json()) as Record<string, unknown>;
    // La key lleva un componente aleatorio por subida (hallazgo M2,
    // 2026-11-18): ya no es predecible a partir del id del clip.
    expect(String(putJson.r2_key)).toMatch(new RegExp(`^clips/sebastian/${id}/[0-9a-f]{16}_clip_02_t07h03m20s_s67_ciego_split_subs\\.mp4$`));
    expect(putJson.size).toBe(MP4_FAKE.byteLength);
    const got = await call(env, "GET", `/api/clips/${id}`, { token });
    expect((got.json.clip as Record<string, unknown>).video_r2_key).toBe(putJson.r2_key);
    const video = await callRaw(env, "GET", `/api/clips/${id}/video`, { token });
    expect(video.status).toBe(200);
    expect(new Uint8Array(await video.arrayBuffer())).toEqual(MP4_FAKE);
  });

  test("GET /video usa el fallback a jobs cuando el clip no tiene video_r2_key", async () => {
    const db = new FakeD1();
    const key = "clips/sebastian/1/clip_viejo.mp4";
    db.seed("clips", clipRow({ id: 1, video_r2_key: "" }));
    db.seed("jobs", { id: 9, tenant_id: "sebastian", clip_id: 1, platform: "tiktok", r2_key: key, state: "scheduled" });
    const bucket = new FakeR2();
    bucket.store.set(key, MP4_FAKE);
    const env = makeEnv(db, bucket);
    const token = await tokenFor("seba", "sebastian");
    const video = await callRaw(env, "GET", "/api/clips/1/video", { token });
    expect(video.status).toBe(200);
    expect(new Uint8Array(await video.arrayBuffer())).toEqual(MP4_FAKE);
  });
});

describe("POST /api/clips/:id/publish — clip 02", () => {
  const BLOCKED_REASON = "sin subtítulos completos: necesita transcripción humana";

  test("con publish_blocked frena en las 4 plataformas con el motivo exacto y no encola jobs", async () => {
    const db = new FakeD1();
    // Con cuentas ES el gate de idioma pasa y frena por subtítulos;
    // sin cuentas frenaría antes con "sin cuenta … en español".
    seedEsAccounts(db);
    db.seed("influencers", { id: 3, tenant_id: "sebastian", name: "IlloJuan", platform: "twitch", profile_url: "", language: "es", permission: 1, active: 1, notes: "", created_at: 1000 });
    const env = makeEnv(db, new FakeR2());
    const token = await tokenFor("seba", "sebastian");
    const created = await call(env, "POST", "/api/clips", { token, body: FICHA_CLIP_02 });
    const id = (created.json.clip as Record<string, unknown>).id as number;
    const r = await call(env, "POST", `/api/clips/${id}/publish`, { token });
    expect(r.status).toBe(400);
    const expected =
      "Ninguna plataforma pudo encolarse: " +
      ["youtube_shorts", "tiktok", "instagram_reels", "facebook_reels"].map((p) => `${p}: ${BLOCKED_REASON}`).join(" · ");
    expect(r.json.error).toBe(expected);
    expect(db.tables["jobs"] ?? []).toHaveLength(0);
  });

  test("sin franja horaria (2026-11-18): publish a las 13:00 encola igual, sin error de franja", async () => {
    const { db, env, token, id } = await setupUnblockedClip();
    // 13:00 en Santiago (16:00Z): antes era "fuera de franja 18:00–24:00".
    // Decisión del usuario 2026-11-18: da lo mismo la hora, solo se sube.
    const r = await withFrozenDate("2026-11-18T16:00:00.000Z", () => call(env, "POST", `/api/clips/${id}/publish`, { token }));
    expect(r.status).toBe(200);
    expect(String(JSON.stringify(r.json))).not.toContain("fuera de franja");
    expect((db.tables["jobs"] ?? []).length).toBe(4);
  });

  test("publish encola 4 jobs únicos con el r2_key del clip (sin franja)", async () => {
    const { db, env, token, id, key } = await setupUnblockedClip();
    // 19:00 en Santiago (22:00Z): sin franja, encola igual que a las 13:00.
    const r = await withFrozenDate("2026-11-18T22:00:00.000Z", () => call(env, "POST", `/api/clips/${id}/publish`, { token }));
    expect(r.status).toBe(200);
    const jobs = r.json.jobs as { id: number; platform: string; state: string }[];
    expect(jobs).toHaveLength(4);
    expect(jobs.map((j) => j.platform).sort()).toEqual(["facebook_reels", "instagram_reels", "tiktok", "youtube_shorts"]);
    expect(new Set(jobs.map((j) => j.id)).size).toBe(4);
    const stored = db.tables["jobs"] ?? [];
    expect(stored).toHaveLength(4);
    for (const j of stored) expect(j.r2_key).toBe(key);
    const got = await call(env, "GET", `/api/clips/${id}`, { token });
    const states = JSON.parse(String((got.json.clip as Record<string, unknown>).publish_states)) as Record<string, { state: string }>;
    for (const p of ["youtube_shorts", "tiktok", "instagram_reels", "facebook_reels"]) {
      expect(states[p]?.state).toBe("programado");
    }
  });
});
