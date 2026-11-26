// Tests de los endpoints nuevos (Backend Dev 2, fase 2):
// POST /api/clips/:id/preview-seen, PATCH /api/clips/:id,
// GET /api/analytics, POST/GET /api/clips/:id/stats,
// GET /api/channels.
// Sin red, sin D1 real: mismo mock en memoria de D1Database que
// endpoints-nuevos.test.ts (Dev 1). La sesión se crea de verdad
// con createSessionToken.
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
    const eqLit = /^(\w+)\s*=\s*'([^']*)'$/.exec(c);
    const isNotNull = /^(\w+)\s+IS\s+NOT\s+NULL$/i.exec(c);
    // Igualdad con literal numérico (la usa listQueueClips:
    // archived = 0) y lista IN (status IN (...)).
    const eqNum = /^(\w+)\s*=\s*(\d+)$/.exec(c);
    const inList = /^(\w+)\s+IN\s*\(([^)]+)\)$/i.exec(c);
    if (eq) {
      const col = eq[1] as string;
      const val = params[pi++];
      tests.push((r) => r[col] === val);
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
      const [, table, colsRaw] = insert as [string, string, string];
      const cols = colsRaw.split(",").map((c) => c.trim());
      const rows = (this.tables[table as string] ??= []);
      const id = rows.reduce((max, r) => Math.max(max, Number(r.id ?? 0)), 0) + 1;
      const row: Row = { id };
      cols.forEach((c, i) => {
        row[c] = params[i];
      });
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

function makeEnv(db: FakeD1, over: Partial<Env> = {}): Env {
  return {
    DB: db as unknown as Env["DB"],
    TENANT: "sebastian",
    CLIPS_BUCKET: {} as Env["CLIPS_BUCKET"],
    SESSION_SECRET: SECRET,
    CREDENTIAL_ENCRYPTION_KEY: Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"),
    BRIDGE_TOKEN: "bridge-test",
    GITHUB_TOKEN: "",
    ALLOWED_EMAIL: "",
    GITHUB_REPO: "exe2027/viral-clip-agent",
    ...over,
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
    game: "GTA VI",
    ...over,
  };
}

describe("POST /api/clips/:id/preview-seen", () => {
  test("sin sesión responde 401", async () => {
    const env = makeEnv(new FakeD1());
    expect((await call(env, "POST", "/api/clips/1/preview-seen")).status).toBe(401);
  });

  test("marca preview_seen=1 y devuelve el clip actualizado", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow());
    const env = makeEnv(db);
    const r = await call(env, "POST", "/api/clips/1/preview-seen", { token: await tokenFor("seba", "sebastian") });
    expect(r.status).toBe(200);
    expect(r.json.already_seen).toBe(false);
    expect((r.json.clip as Row).preview_seen).toBe(1);
    const again = await call(env, "GET", "/api/clips/1", { token: await tokenFor("seba", "sebastian") });
    expect((again.json.clip as Row).preview_seen).toBe(1);
  });

  test("registra un evento en tracking con actor = usuario de la sesión", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow());
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    await call(env, "POST", "/api/clips/1/preview-seen", { token: tok });
    const tracking = await call(env, "GET", "/api/clips/1/tracking", { token: tok });
    const events = tracking.json.events as Row[];
    expect(events.length).toBe(1);
    expect(events[0]?.step).toBe("revision");
    expect(events[0]?.actor).toBe("seba");
    expect(String(events[0]?.message)).toContain("Preview visto");
    expect(events[0]?.failed).toBe(0);
  });

  test("segunda llamada: already_seen true y sin duplicar el evento", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow({ preview_seen: 1 }));
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    const r = await call(env, "POST", "/api/clips/1/preview-seen", { token: tok });
    expect(r.status).toBe(200);
    expect(r.json.already_seen).toBe(true);
    const tracking = await call(env, "GET", "/api/clips/1/tracking", { token: tok });
    expect((tracking.json.events as unknown[]).length).toBe(0);
  });

  test("404 si el clip no existe o es de otro tenant", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow({ id: 1, tenant_id: "kaez" }));
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    expect((await call(env, "POST", "/api/clips/99/preview-seen", { token: tok })).status).toBe(404);
    expect((await call(env, "POST", "/api/clips/1/preview-seen", { token: tok })).status).toBe(404);
    // La sesión de kaez en el Worker de sebastian da 401; en su
    // Worker ve el clip intacto (preview_seen 0).
    expect((await call(env, "GET", "/api/clips/1", { token: await tokenFor("kaez", "kaez") })).status).toBe(401);
    const envK = makeEnv(db, { TENANT: "kaez" });
    const after = await call(envK, "GET", "/api/clips/1", { token: await tokenFor("kaez", "kaez") });
    expect((after.json.clip as Row).preview_seen).toBe(0);
  });
});

describe("PATCH /api/clips/:id", () => {
  test("sin sesión responde 401", async () => {
    const env = makeEnv(new FakeD1());
    expect((await call(env, "PATCH", "/api/clips/1", { token: undefined, body: { seo_title: "x" } })).status).toBe(401);
  });

  test("404 si el clip no existe o es de otro tenant", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow({ id: 1, tenant_id: "kaez" }));
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    expect((await call(env, "PATCH", "/api/clips/99", { token: tok, body: { seo_title: "x" } })).status).toBe(404);
    expect((await call(env, "PATCH", "/api/clips/1", { token: tok, body: { seo_title: "x" } })).status).toBe(404);
  });

  test("edita solo los campos permitidos y devuelve el clip actualizado", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow());
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    const r = await call(env, "PATCH", "/api/clips/1", {
      token: tok,
      body: { subtitle_style: "karaoke", seo_title: "Título SEO", seo_description: "Desc", seo_tags: "gta, clips", thumbnail_text: "MINIATURA" },
    });
    expect(r.status).toBe(200);
    const clip = r.json.clip as Row;
    expect(clip.subtitle_style).toBe("karaoke");
    expect(clip.seo_title).toBe("Título SEO");
    expect(clip.seo_description).toBe("Desc");
    expect(clip.seo_tags).toBe("gta, clips");
    expect(clip.thumb_text).toBe("MINIATURA");
    expect(clip.title).toBe("Clip de prueba");
    expect(clip.score).toBe(80);
  });

  test("campos no permitidos se ignoran; sin campos válidos, 400", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow());
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    const r = await call(env, "PATCH", "/api/clips/1", { token: tok, body: { score: 99, status: "publicado" } });
    expect(r.status).toBe(400);
    const after = await call(env, "GET", "/api/clips/1", { token: tok });
    expect((after.json.clip as Row).title).toBe("Clip de prueba");
    expect((after.json.clip as Row).score).toBe(80);
  });

  test("title sí es campo válido (R8.3, spec títulos 2026-11-26)", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow());
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    const r = await call(env, "PATCH", "/api/clips/1", { token: tok, body: { title: "Otro título" } });
    expect(r.status).toBe(200);
    const after = await call(env, "GET", "/api/clips/1", { token: tok });
    expect((after.json.clip as Row).title).toBe("Otro título");
    expect((after.json.clip as Row).score).toBe(80);
  });

  // Regla 40-60 / 25-35 ELIMINADA 2026-11-21: solo 61 s se
  // rechaza; 30, 28, 20, 36, 38 y 39 pasan y se guardan.
  test("duración sin mínimo: 30, 28, 20, 36, 38 y 39 pasan; solo 61 se rechaza con 400", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow());
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    for (const d of [30, 28, 20, 36, 38, 39]) {
      const r = await call(env, "PATCH", "/api/clips/1", { token: tok, body: { duration: d } });
      expect(r.status).toBe(200);
    }
    const r61 = await call(env, "PATCH", "/api/clips/1", { token: tok, body: { duration: 61 } });
    expect(r61.status).toBe(400);
    const after = await call(env, "GET", "/api/clips/1", { token: tok });
    const clip = after.json.clip as Row;
    expect(clip.end_sec).toBe(139);
    expect(clip.preview_seen).toBe(0);
  });

  test("duración 40 válida: end_sec = start_sec + 40", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow());
    const env = makeEnv(db);
    const r = await call(env, "PATCH", "/api/clips/1", {
      token: await tokenFor("seba", "sebastian"),
      body: { duration: 40 },
    });
    expect(r.status).toBe(200);
    expect(((r.json.clip as Row).end_sec as number) - ((r.json.clip as Row).start_sec as number)).toBe(40);
    expect(r.json.preview_reset).toBe(false);
  });

  test("cambiar la duración con preview visto lo invalida (preview_seen=0) y lo dice", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow({ preview_seen: 1 }));
    const env = makeEnv(db);
    const r = await call(env, "PATCH", "/api/clips/1", {
      token: await tokenFor("seba", "sebastian"),
      body: { duration: 45 },
    });
    expect(r.status).toBe(200);
    expect((r.json.clip as Row).preview_seen).toBe(0);
    expect(r.json.preview_reset).toBe(true);
    expect(String(r.json.preview_note)).toContain("preview");
  });

  test("misma duración no resetea el preview", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow({ preview_seen: 1 }));
    const env = makeEnv(db);
    const r = await call(env, "PATCH", "/api/clips/1", {
      token: await tokenFor("seba", "sebastian"),
      body: { duration: 40, seo_title: "Nuevo" },
    });
    expect(r.status).toBe(200);
    expect((r.json.clip as Row).preview_seen).toBe(1);
    expect(r.json.preview_reset).toBe(false);
  });

  test("target_platforms, schedule y validaciones de estilo", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow());
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    const badStyle = await call(env, "PATCH", "/api/clips/1", { token: tok, body: { subtitle_style: "neon" } });
    expect(badStyle.status).toBe(400);
    const badPlatforms = await call(env, "PATCH", "/api/clips/1", { token: tok, body: { target_platforms: ["youtube"] } });
    expect(badPlatforms.status).toBe(400);
    const badSchedule = await call(env, "PATCH", "/api/clips/1", { token: tok, body: { schedule: "no es fecha" } });
    expect(badSchedule.status).toBe(400);
    const ok = await call(env, "PATCH", "/api/clips/1", {
      token: tok,
      body: { target_platforms: ["youtube_shorts", "instagram_reels"], schedule: "2026-11-19T18:00:00-03:00" },
    });
    expect(ok.status).toBe(200);
    expect(JSON.parse(String((ok.json.clip as Row).platforms))).toEqual(["youtube_shorts", "instagram_reels"]);
    expect((ok.json.clip as Row).scheduled_for).toBe("2026-11-19T18:00:00-03:00");
    const clear = await call(env, "PATCH", "/api/clips/1", { token: tok, body: { schedule: null } });
    expect(clear.status).toBe(200);
    expect((clear.json.clip as Row).scheduled_for).toBeNull();
  });
});

describe("POST /api/clips/:id/stats", () => {
  test("sin sesión responde 401", async () => {
    const env = makeEnv(new FakeD1());
    expect((await call(env, "POST", "/api/clips/1/stats", { body: { platform: "youtube", views: 10 } })).status).toBe(401);
  });

  test("guarda la medición real: 201, opcionales ausentes quedan null (nunca 0)", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow());
    const env = makeEnv(db);
    const r = await call(env, "POST", "/api/clips/1/stats", {
      token: await tokenFor("seba", "sebastian"),
      body: { platform: "youtube", views: 1234, retention: 45.5, window: "24h" },
    });
    expect(r.status).toBe(201);
    const stat = r.json.stat as Row;
    expect(stat.clip_id).toBe(1);
    expect(stat.platform).toBe("youtube");
    expect(stat.views).toBe(1234);
    expect(stat.retention_pct).toBe(45.5);
    expect(stat.window_label).toBe("24h");
    expect(stat.shares).toBeNull();
    expect(stat.saves).toBeNull();
    expect(stat.comments).toBeNull();
    expect(stat.new_subs).toBeNull();
  });

  test("guarda todos los opcionales y normaliza la plataforma del panel", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow());
    const env = makeEnv(db);
    const r = await call(env, "POST", "/api/clips/1/stats", {
      token: await tokenFor("seba", "sebastian"),
      body: { platform: "instagram_reels", views: 50, shares: 3, saves: 2, comments: 1, subscribers: 4 },
    });
    expect(r.status).toBe(201);
    const stat = r.json.stat as Row;
    expect(stat.platform).toBe("instagram");
    expect(stat.shares).toBe(3);
    expect(stat.new_subs).toBe(4);
    expect(stat.subscribers).toBe(4);
    expect(stat.window_label).toBe("7d");
  });

  test("validación: vistas negativas, retención >100, plataforma y ventana inválidas: 400", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow());
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    expect((await call(env, "POST", "/api/clips/1/stats", { token: tok, body: { platform: "youtube", views: -1 } })).status).toBe(400);
    expect((await call(env, "POST", "/api/clips/1/stats", { token: tok, body: { platform: "youtube", views: 10, retention: 101 } })).status).toBe(400);
    expect((await call(env, "POST", "/api/clips/1/stats", { token: tok, body: { platform: "twitch", views: 10 } })).status).toBe(400);
    expect((await call(env, "POST", "/api/clips/1/stats", { token: tok, body: { platform: "youtube", views: 10, window: "28d" } })).status).toBe(400);
    expect((await call(env, "POST", "/api/clips/1/stats", { token: tok, body: { platform: "youtube" } })).status).toBe(400);
    const list = await call(env, "GET", "/api/clips/1/stats", { token: tok });
    expect(list.json.stats).toEqual([]);
  });

  test("404 si el clip no existe o es de otro tenant", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow({ id: 1, tenant_id: "kaez" }));
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    expect((await call(env, "POST", "/api/clips/1/stats", { token: tok, body: { platform: "youtube", views: 1 } })).status).toBe(404);
    expect((await call(env, "GET", "/api/clips/1/stats", { token: tok })).status).toBe(404);
  });
});

describe("GET /api/clips/:id/stats", () => {
  test("lista las mediciones del clip, vacía si no hay", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow());
    db.seed("clip_stats", { id: 1, tenant_id: "sebastian", clip_id: 1, platform: "youtube", views: 100, retention_pct: 50, shares: null, saves: null, comments: null, new_subs: null, window_label: "7d", recorded_at: 2000 });
    db.seed("clip_stats", { id: 2, tenant_id: "sebastian", clip_id: 1, platform: "tiktok", views: 80, retention_pct: 40, shares: 1, saves: 0, comments: 0, new_subs: 0, window_label: "7d", recorded_at: 1000 });
    db.seed("clip_stats", { id: 3, tenant_id: "kaez", clip_id: 1, platform: "youtube", views: 9999, retention_pct: 99, shares: null, saves: null, comments: null, new_subs: null, window_label: "7d", recorded_at: 3000 });
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/clips/1/stats", { token: await tokenFor("seba", "sebastian") });
    expect(r.status).toBe(200);
    const stats = r.json.stats as Row[];
    expect(stats.length).toBe(2);
    expect(stats[0]?.platform).toBe("youtube");
    expect(stats[1]?.platform).toBe("tiktok");
    expect(JSON.stringify(r.json)).not.toContain("9999");
  });
});

describe("GET /api/analytics", () => {
  test("sin sesión responde 401", async () => {
    const env = makeEnv(new FakeD1());
    expect((await call(env, "GET", "/api/analytics")).status).toBe(401);
  });

  test("sin mediciones: medianas null (nunca 0), ranking vacío, sin datos", async () => {
    const env = makeEnv(new FakeD1());
    const r = await call(env, "GET", "/api/analytics", { token: await tokenFor("seba", "sebastian") });
    expect(r.status).toBe(200);
    const platforms = r.json.platforms as Row[];
    expect(platforms.length).toBe(4);
    for (const p of platforms) {
      expect(p.median_views).toBeNull();
      expect(p.median_retention).toBeNull();
      expect(p.measured_clips).toBe(0);
      expect(p.has_data).toBe(false);
      expect(p.ranking).toEqual([]);
    }
    expect(r.json.unmeasured).toEqual([]);
  });

  test("medianas y ranking con mediciones reales; manda la última medición por clip", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow({ id: 1 }));
    db.seed("clips", clipRow({ id: 2, title: "Clip dos" }));
    db.seed("clips", clipRow({ id: 3, title: "Clip tres" }));
    // Clip 1: medición vieja (views 100) y nueva (views 500): manda la nueva.
    db.seed("clip_stats", { id: 1, tenant_id: "sebastian", clip_id: 1, platform: "youtube", views: 100, retention_pct: 10, shares: null, saves: null, comments: null, new_subs: null, window_label: "7d", recorded_at: 1000 });
    db.seed("clip_stats", { id: 2, tenant_id: "sebastian", clip_id: 1, platform: "youtube", views: 500, retention_pct: 60, shares: null, saves: null, comments: null, new_subs: null, window_label: "24h", recorded_at: 2000 });
    db.seed("clip_stats", { id: 3, tenant_id: "sebastian", clip_id: 2, platform: "youtube", views: 300, retention_pct: 40, shares: null, saves: null, comments: null, new_subs: null, window_label: "7d", recorded_at: 1500 });
    db.seed("clip_stats", { id: 4, tenant_id: "sebastian", clip_id: 3, platform: "youtube", views: 300, retention_pct: 70, shares: null, saves: null, comments: null, new_subs: null, window_label: "7d", recorded_at: 1500 });
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/analytics", { token: await tokenFor("seba", "sebastian") });
    const yt = (r.json.platforms as Row[]).find((p) => p.platform === "youtube") as Row;
    expect(yt.measured_clips).toBe(3);
    expect(yt.has_data).toBe(true);
    expect(yt.median_views).toBe(300); // mediana de [500, 300, 300]
    expect(yt.median_retention).toBe(60); // mediana de [60, 40, 70]
    const ranking = yt.ranking as Row[];
    expect(ranking.map((s) => s.clip_id)).toEqual([1, 3, 2]); // 500 primero; empate 300: retención 70 antes que 40
    const tt = (r.json.platforms as Row[]).find((p) => p.platform === "tiktok") as Row;
    expect(tt.median_views).toBeNull();
    expect(tt.measured_clips).toBe(0);
  });

  test("publicados sin medición van a unmeasured; con medición no", async () => {
    const db = new FakeD1();
    const published = JSON.stringify({
      youtube_shorts: { state: "publicado", scheduledFor: null, publishedAt: new Date().toISOString(), note: "", since: "", updatedAt: "" },
      tiktok: { state: "pendiente", scheduledFor: null, publishedAt: null, note: "", since: "", updatedAt: "" },
    });
    db.seed("clips", clipRow({ id: 1, publish_states: published, status: "publicado" }));
    db.seed("clips", clipRow({ id: 2, title: "Clip dos", publish_states: published, status: "publicado" }));
    // 0030: los publicados se cuentan desde los jobs posted (fuente
    // de verdad), no desde publish_states: sin job posted, el clip
    // no cuenta aunque su publish_states diga "publicado".
    db.seed("jobs", { id: 1, tenant_id: "sebastian", kind: "publish", clip_id: 1, platform: "youtube_shorts", language: "es", state: "posted", platform_id: "aaa", evidence_url: "https://www.youtube.com/shorts/aaa", updated_at: 3000 });
    db.seed("jobs", { id: 2, tenant_id: "sebastian", kind: "publish", clip_id: 2, platform: "youtube_shorts", language: "es", state: "posted", platform_id: "bbb", evidence_url: "https://www.youtube.com/shorts/bbb", updated_at: 3001 });
    db.seed("clip_stats", { id: 1, tenant_id: "sebastian", clip_id: 2, platform: "youtube", views: 10, retention_pct: 20, shares: null, saves: null, comments: null, new_subs: null, window_label: "7d", recorded_at: 1000 });
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/analytics", { token: await tokenFor("seba", "sebastian") });
    const yt = (r.json.platforms as Row[]).find((p) => p.platform === "youtube") as Row;
    expect(yt.published_clips).toBe(2);
    expect(r.json.unmeasured).toEqual([{ clip_id: 1, title: "Clip de prueba", platform: "youtube", language: "es" }]);
  });

  test("las mediciones de otro tenant no cuentan", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow({ id: 1 }));
    db.seed("clip_stats", { id: 1, tenant_id: "kaez", clip_id: 1, platform: "youtube", views: 9999, retention_pct: 99, shares: null, saves: null, comments: null, new_subs: null, window_label: "7d", recorded_at: 1000 });
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/analytics", { token: await tokenFor("seba", "sebastian") });
    const yt = (r.json.platforms as Row[]).find((p) => p.platform === "youtube") as Row;
    expect(yt.median_views).toBeNull();
    expect(yt.measured_clips).toBe(0);
  });
});

describe("GET /api/channels", () => {
  test("sin sesión responde 401", async () => {
    const env = makeEnv(new FakeD1());
    expect((await call(env, "GET", "/api/channels")).status).toBe(401);
  });

  test("sin cuentas ni clips: 8 canales (4 plataformas × es/en), sin_crear, cuota entera, salud sin datos", async () => {
    const env = makeEnv(new FakeD1());
    const r = await call(env, "GET", "/api/channels", { token: await tokenFor("seba", "sebastian") });
    expect(r.status).toBe(200);
    const channels = r.json.channels as Row[];
    // DOS COLAS (0019): un canal por plataforma+idioma.
    expect(channels.map((c) => `${c.platform}|${c.language}`)).toEqual([
      "youtube|es", "youtube|en", "tiktok|es", "tiktok|en",
      "instagram|es", "instagram|en", "facebook|es", "facebook|en",
    ]);
    for (const c of channels) {
      expect(c.account_status).toBe("sin_crear");
      expect(c.account).toBeNull();
      expect(c.used_today).toBe(0);
      // Los topes ya no son fijos: vienen de Ajustes (defaults YT 6,
      // TikTok 3, IG/FB 3; orden usuario 2026-11-21 21:09) y la madurez
      // puede bajarlos; sin cuenta, el default.
      expect(c.daily_cap_fixed).toBe(false);
      expect(c.daily_plan_fixed).toBe(false);
      expect(c.maturity).toBeNull();
      expect(c.published_total).toBe(0);
      expect(c.published_last_7d).toBe(0);
      expect(c.health).toBe("sin_datos");
    }
    const byKey = Object.fromEntries(channels.map((c) => [`${c.platform}|${c.language}`, c]));
    expect((byKey["youtube|es"] as Row).daily_cap).toBe(6);
    expect((byKey["youtube|es"] as Row).remaining_today).toBe(6);
    expect((byKey["youtube|en"] as Row).daily_cap).toBe(6);
    expect((byKey["tiktok|es"] as Row).daily_cap).toBe(3);
    expect((byKey["tiktok|es"] as Row).remaining_today).toBe(3);
    expect(JSON.stringify(r.json)).not.toContain("credential");
  });

  test("estados reales de cuenta API; Meta unificada para Instagram y Facebook", async () => {
    const db = new FakeD1();
    db.seed("api_accounts", { id: 1, tenant_id: "sebastian", platform: "youtube", language: "es", label: "YT ES", credential: "", credential_enc: "v1:xxx", extra: "", extra_enc: "", status: "conectada", note: "", created_at: 1, verified_at: 2000, verify_ok: 1, verify_message: "ok" });
    db.seed("api_accounts", { id: 2, tenant_id: "sebastian", platform: "tiktok", language: "es", label: "TT ES", credential: "", credential_enc: "v1:yyy", extra: "", extra_enc: "", status: "error", note: "", created_at: 1, verified_at: 2000, verify_ok: 0, verify_message: "token vencido" });
    db.seed("api_accounts", { id: 3, tenant_id: "sebastian", platform: "meta", language: "es", label: "Meta ES", credential: "", credential_enc: "", extra: "", extra_enc: "", status: "pendiente", note: "", created_at: 1, verified_at: null, verify_ok: null, verify_message: "" });
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/channels", { token: await tokenFor("seba", "sebastian") });
    const byKey = Object.fromEntries((r.json.channels as Row[]).map((c) => [`${c.platform}|${c.language}`, c]));
    expect(byKey["youtube|es"]?.account_status).toBe("conectada");
    expect(((byKey["youtube|es"] as Row)?.account as Row)?.has_credential).toBe(true);
    expect(byKey["youtube|en"]?.account_status).toBe("sin_crear");
    expect(byKey["tiktok|es"]?.account_status).toBe("no_conectada");
    expect(((byKey["tiktok|es"] as Row)?.account as Row)?.verify_message).toBe("token vencido");
    expect(byKey["instagram|es"]?.account_status).toBe("sin_credencial");
    expect(byKey["facebook|es"]?.account_status).toBe("sin_credencial");
    expect(((byKey["instagram|es"] as Row)?.account as Row)?.id).toBe(3);
    expect(((byKey["facebook|es"] as Row)?.account as Row)?.id).toBe(3);
  });

  test("cuota y publicados se cuentan de publish_states reales (hoy y 7 días)", async () => {
    const db = new FakeD1();
    const now = new Date().toISOString();
    const threeDaysAgo = new Date(Date.now() - 3 * 86400000).toISOString();
    const tenDaysAgo = new Date(Date.now() - 10 * 86400000).toISOString();
    const states = (publishedAt: string) =>
      JSON.stringify({
        youtube_shorts: { state: "publicado", scheduledFor: null, publishedAt, note: "", since: "", updatedAt: "" },
      });
    db.seed("clips", clipRow({ id: 1, publish_states: states(now), status: "publicado" }));
    db.seed("clips", clipRow({ id: 2, title: "Clip dos", publish_states: states(threeDaysAgo), status: "publicado" }));
    db.seed("clips", clipRow({ id: 3, title: "Clip tres", publish_states: states(tenDaysAgo), status: "publicado" }));
    db.seed("clips", clipRow({ id: 4, title: "Clip cuatro", publish_states: "{}", status: "aprobado" }));
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/channels", { token: await tokenFor("seba", "sebastian") });
    const ytEs = (r.json.channels as Row[]).find((c) => c.platform === "youtube" && c.language === "es") as Row;
    expect(ytEs.used_today).toBe(1);
    expect(ytEs.remaining_today).toBe(5); // tope YT 6 (orden 2026-11-21 21:09), 1 usado
    expect(ytEs.published_total).toBe(3);
    expect(ytEs.published_last_7d).toBe(2);
    // DOS COLAS (0019): los publicados en español no gastan la
    // cuota del canal en inglés.
    const ytEn = (r.json.channels as Row[]).find((c) => c.platform === "youtube" && c.language === "en") as Row;
    expect(ytEn.used_today).toBe(0);
    expect(ytEn.published_total).toBe(0);
    expect(ytEn.remaining_today).toBe(6);
    const tt = (r.json.channels as Row[]).find((c) => c.platform === "tiktok" && c.language === "es") as Row;
    expect(tt.used_today).toBe(0);
    expect(tt.published_total).toBe(0);
  });

  test("aislamiento por tenant: clips y cuentas del otro no cuentan", async () => {
    const db = new FakeD1();
    const states = JSON.stringify({
      youtube_shorts: { state: "publicado", scheduledFor: null, publishedAt: new Date().toISOString(), note: "", since: "", updatedAt: "" },
    });
    db.seed("clips", clipRow({ id: 1, tenant_id: "kaez", publish_states: states, status: "publicado" }));
    db.seed("api_accounts", { id: 1, tenant_id: "kaez", platform: "youtube", language: "es", label: "YT kaez", credential: "", credential_enc: "v1:zzz", extra: "", extra_enc: "", status: "conectada", note: "", created_at: 1, verified_at: 2000, verify_ok: 1, verify_message: "ok" });
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/channels", { token: await tokenFor("seba", "sebastian") });
    const yt = (r.json.channels as Row[]).find((c) => c.platform === "youtube" && c.language === "es") as Row;
    expect(yt.account_status).toBe("sin_crear");
    expect(yt.used_today).toBe(0);
    expect(yt.published_total).toBe(0);
  });
});

// --- GET /api/daily-plan (reglas 2026-11-19, §9) ---
describe("GET /api/daily-plan", () => {
  test("sin sesión responde 401", async () => {
    const env = makeEnv(new FakeD1());
    expect((await call(env, "GET", "/api/daily-plan")).status).toBe(401);
  });

  test("fecha inválida es 400; sin cuentas ni clips devuelve el plan vacío de hoy", async () => {
    const env = makeEnv(new FakeD1());
    const token = await tokenFor("seba", "sebastian");
    const bad = await call(env, "GET", "/api/daily-plan?fecha=19-11-2026", { token });
    expect(bad.status).toBe(400);
    const r = await call(env, "GET", "/api/daily-plan", { token });
    expect(r.status).toBe(200);
    expect(r.json.ok).toBe(true);
    expect(typeof r.json.fecha).toBe("string");
    expect(r.json.publicaciones).toEqual([]);
    expect(r.json.en_cola).toEqual([]);
    expect(Array.isArray(r.json.alertas)).toBe(true);
  });

  test("con cuenta TikTok establecida y un clip en Cola: el plan programa 1 publicación en franja", async () => {
    const db = new FakeD1();
    db.seed("api_accounts", {
      id: 10, tenant_id: "sebastian", platform: "tiktok", language: "es", label: "TikTok ES",
      credential: "", credential_enc: "v1:xxx", extra: "", extra_enc: "", status: "conectada",
      note: "", created_at: 1, verified_at: 2000, verify_ok: 1, verify_message: "ok",
      first_published_at: Date.now() - 40 * 86400_000, published_count: 20, strikes: 0,
      maturity: "establecida", maturity_checked_day: null,
    });
    db.seed("clips", clipRow({ id: 1, platforms: JSON.stringify(["tiktok"]), score: 90 }));
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/daily-plan", { token: await tokenFor("seba", "sebastian") });
    expect(r.status).toBe(200);
    const pubs = r.json.publicaciones as Row[];
    expect(pubs).toHaveLength(1);
    expect(pubs[0]?.clip_id).toBe(1);
    expect(pubs[0]?.plataforma).toBe("tiktok");
    // Hora dentro de una franja (12-14, 18-20 o 21-23).
    const [h] = String(pubs[0]?.hora_local ?? "").split(":").map(Number);
    expect([12, 13, 18, 19, 21, 22].includes(h ?? -1)).toBe(true);
    expect(r.json.en_cola).toEqual([]);
  });
});

// --- Frente 2, M2: override SEO 70–89 desde PATCH ---
describe("PATCH /api/clips/:id — seo_override (M2)", () => {
  function viralWith(scores: Record<string, number>): string {
    const platforms: Record<string, { score: number }> = {};
    for (const [p, s] of Object.entries(scores)) platforms[p] = { score: s };
    return JSON.stringify({ platforms });
  }

  test("activa el override con SEO 85 y lo guarda en seo_overrides", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow({ viral: viralWith({ youtube_shorts: 85 }) }));
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    const r = await call(env, "PATCH", "/api/clips/1", {
      token: tok,
      body: { seo_override: { platform: "youtube_shorts", enabled: true } },
    });
    expect(r.status).toBe(200);
    expect(JSON.parse(String((r.json.clip as Row).seo_overrides))).toEqual(["youtube_shorts"]);
  });

  test("rechaza override con SEO 95 (ya pasa), 65 (demasiado bajo) y sin medición", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow({ viral: viralWith({ youtube_shorts: 95, tiktok: 65 }) }));
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    const high = await call(env, "PATCH", "/api/clips/1", { token: tok, body: { seo_override: { platform: "youtube_shorts", enabled: true } } });
    expect(high.status).toBe(400);
    expect(String(high.json.error)).toContain("no necesita override");
    const low = await call(env, "PATCH", "/api/clips/1", { token: tok, body: { seo_override: { platform: "tiktok", enabled: true } } });
    expect(low.status).toBe(400);
    expect(String(low.json.error)).toContain("demasiado bajo");
    const none = await call(env, "PATCH", "/api/clips/1", { token: tok, body: { seo_override: { platform: "instagram_reels", enabled: true } } });
    expect(none.status).toBe(400);
    expect(String(none.json.error)).toContain("Sin medición SEO");
    const after = await call(env, "GET", "/api/clips/1", { token: tok });
    expect((after.json.clip as Row).seo_overrides).toBe("[]");
  });

  test("quitar el override lo saca de la lista; plataforma o enabled inválidos dan 400", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow({ viral: viralWith({ youtube_shorts: 80 }), seo_overrides: '["youtube_shorts"]' }));
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    const off = await call(env, "PATCH", "/api/clips/1", {
      token: tok,
      body: { seo_override: { platform: "youtube_shorts", enabled: false } },
    });
    expect(off.status).toBe(200);
    expect(JSON.parse(String((off.json.clip as Row).seo_overrides))).toEqual([]);
    expect((await call(env, "PATCH", "/api/clips/1", { token: tok, body: { seo_override: { platform: "nope", enabled: true } } })).status).toBe(400);
    expect((await call(env, "PATCH", "/api/clips/1", { token: tok, body: { seo_override: { platform: "youtube_shorts", enabled: "si" } } })).status).toBe(400);
  });
});
