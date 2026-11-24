// Tests de los endpoints nuevos (Backend Dev 1, fase 2):
// GET /api/social-accounts, POST /api/social-accounts/:id/verify,
// GET/POST/PATCH/DELETE /api/influencers, GET /api/sources[/:id],
// GET /api/panel-users.
// Sin red, sin D1 real: un mock en memoria de D1Database que
// interpreta el subconjunto SQL que usa el Worker (SELECT con
// WHERE/ORDER BY/LIMIT, INSERT, UPDATE, DELETE con prepared
// statements). La sesión se crea de verdad con createSessionToken.
import { beforeEach, describe, expect, test } from "bun:test";
import { createSessionToken, hashPassword, requireTenant, verifySessionToken } from "../src/auth";
import type { Env } from "../src/cloudflare";
import worker, { clearJobsCache } from "../src/index";

const SECRET = Buffer.from("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb").toString("base64"); // SOLO TEST

type Row = Record<string, unknown>;

// --- Mock D1 en memoria ---
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
    // Literal de texto (lo usa getLoginUser: status = 'activo').
    const eqLit = /^(\w+)\s*=\s*'([^']*)'$/.exec(c);
    // Comparación < / > con placeholder (la usa revokeToken para
    // purgar huellas caducadas: expires_at < ?).
    const lt = /^(\w+)\s*<\s*\?$/.exec(c);
    const gt = /^(\w+)\s*>\s*\?$/.exec(c);
    // Literal numérico (lo usa listQueueClips: archived = 0).
    const eqNum = /^(\w+)\s*=\s*(\d+)$/.exec(c);
    // Lista IN (lo usa listQueueClips: status IN ('a','b')).
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
    // Upsert de settings (saveSettings): INSERT ... ON CONFLICT(tenant_id).
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
      const [, table, colsRaw] = insert as [string, string, string];
      const cols = colsRaw.split(",").map((c) => c.trim());
      const rows = (this.tables[table as string] ??= []);
      const id = rows.reduce((max, r) => Math.max(max, Number(r.id ?? 0)), 0) + 1;
      const row: Row = { id };
      cols.forEach((c, i) => {
        row[c] = params[i];
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

describe("GET /api/social-accounts", () => {
  test("sin sesión responde 401", async () => {
    const env = makeEnv(new FakeD1());
    const r = await call(env, "GET", "/api/social-accounts");
    expect(r.status).toBe(401);
  });

  test("lista vacía sin filas (nunca inventa cuentas)", async () => {
    const env = makeEnv(new FakeD1());
    const r = await call(env, "GET", "/api/social-accounts", { token: await tokenFor("seba", "sebastian") });
    expect(r.status).toBe(200);
    expect(r.json.accounts).toEqual([]);
  });

  test("cada tenant ve solo sus cuentas sociales (cada Worker solo sirve a su tenant)", async () => {
    const db = new FakeD1();
    db.seed("accounts", { id: 1, tenant_id: "sebastian", platform: "youtube", label: "YT ES", handle: "@elclipdegta6", status: "conectada", note: "", sort: 1 });
    db.seed("accounts", { id: 2, tenant_id: "kaez", platform: "tiktok", label: "TT kaez", handle: "@kaez", status: "pendiente", note: "", sort: 1 });
    const env = makeEnv(db);
    const seba = await call(env, "GET", "/api/social-accounts", { token: await tokenFor("seba", "sebastian") });
    expect((seba.json.accounts as unknown[]).length).toBe(1);
    expect((seba.json.accounts as { handle: string }[])[0]?.handle).toBe("@elclipdegta6");
    // Una sesión de kaez en el Worker de sebastian no vale (401).
    const wrong = await call(env, "GET", "/api/social-accounts", { token: await tokenFor("kaez", "kaez") });
    expect(wrong.status).toBe(401);
    // En el Worker de kaez, la misma sesión ve solo lo de kaez.
    const envK = makeEnv(db, { TENANT: "kaez" });
    const kaez = await call(envK, "GET", "/api/social-accounts", { token: await tokenFor("kaez", "kaez") });
    expect((kaez.json.accounts as unknown[]).length).toBe(1);
    expect((kaez.json.accounts as { handle: string }[])[0]?.handle).toBe("@kaez");
  });

  test("la respuesta no incluye credenciales", async () => {
    const db = new FakeD1();
    db.seed("accounts", { id: 1, tenant_id: "sebastian", platform: "youtube", label: "YT", handle: "@x", status: "conectada", note: "", sort: 0 });
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/social-accounts", { token: await tokenFor("seba", "sebastian") });
    const raw = JSON.stringify(r.json);
    expect(raw).not.toContain("credential");
    expect(raw).not.toContain("password");
  });
});

describe("POST /api/social-accounts/:id/verify", () => {
  test("cuenta existente: 501 verificación no disponible, nunca éxito fingido", async () => {
    const db = new FakeD1();
    db.seed("accounts", { id: 1, tenant_id: "sebastian", platform: "youtube", label: "YT", handle: "@x", status: "pendiente", note: "", sort: 0 });
    const env = makeEnv(db);
    const r = await call(env, "POST", "/api/social-accounts/1/verify", { token: await tokenFor("seba", "sebastian") });
    expect(r.status).toBe(501);
    expect(r.json.ok).toBe(false);
    expect(String(r.json.error)).toContain("verificación no disponible");
  });

  test("cuenta inexistente o de otro tenant: 404", async () => {
    const db = new FakeD1();
    db.seed("accounts", { id: 1, tenant_id: "kaez", platform: "youtube", label: "YT", handle: "@x", status: "pendiente", note: "", sort: 0 });
    const env = makeEnv(db);
    const r1 = await call(env, "POST", "/api/social-accounts/99/verify", { token: await tokenFor("seba", "sebastian") });
    expect(r1.status).toBe(404);
    const r2 = await call(env, "POST", "/api/social-accounts/1/verify", { token: await tokenFor("seba", "sebastian") });
    expect(r2.status).toBe(404);
  });
});

describe("influencers", () => {
  test("lista vacía sin filas", async () => {
    const env = makeEnv(new FakeD1());
    const r = await call(env, "GET", "/api/influencers", { token: await tokenFor("seba", "sebastian") });
    expect(r.status).toBe(200);
    expect(r.json.influencers).toEqual([]);
  });

  test("crear IlloJuan activo con permiso: 201", async () => {
    const env = makeEnv(new FakeD1());
    const r = await call(env, "POST", "/api/influencers", {
      token: await tokenFor("seba", "sebastian"),
      body: { platform: "twitch", handle: "IlloJuan", url: "https://www.twitch.tv/illojuan", language: "es", permission: true, active: true },
    });
    expect(r.status).toBe(201);
    const inf = r.json.influencer as Record<string, unknown>;
    expect(inf.handle).toBe("IlloJuan");
    expect(inf.active).toBe(true);
    expect(inf.permission).toBe(true);
  });

  test("crear otro influencer activo: 400 con el motivo (regla de lanzamiento)", async () => {
    const env = makeEnv(new FakeD1());
    const r = await call(env, "POST", "/api/influencers", {
      token: await tokenFor("seba", "sebastian"),
      body: { platform: "twitch", handle: "OtroStreamer", permission: true, active: true },
    });
    expect(r.status).toBe(400);
    expect(String(r.json.error)).toContain("10 del lanzamiento");
    const list = await call(env, "GET", "/api/influencers", { token: await tokenFor("seba", "sebastian") });
    expect(list.json.influencers).toEqual([]);
  });

  test("crear otro influencer desactivado: 201", async () => {
    const env = makeEnv(new FakeD1());
    const r = await call(env, "POST", "/api/influencers", {
      token: await tokenFor("seba", "sebastian"),
      body: { platform: "youtube", handle: "OtroStreamer", active: false },
    });
    expect(r.status).toBe(201);
    expect((r.json.influencer as Record<string, unknown>).active).toBe(false);
  });

  test("validación: plataforma inválida, sin handle y URL sin http: 400", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await tokenFor("seba", "sebastian");
    const badPlatform = await call(env, "POST", "/api/influencers", { token: tok, body: { platform: "facebook", handle: "X", active: false } });
    expect(badPlatform.status).toBe(400);
    const noHandle = await call(env, "POST", "/api/influencers", { token: tok, body: { platform: "twitch", active: false } });
    expect(noHandle.status).toBe(400);
    const badUrl = await call(env, "POST", "/api/influencers", { token: tok, body: { platform: "twitch", handle: "X", url: "twitch.tv/x", active: false } });
    expect(badUrl.status).toBe(400);
  });

  test("PATCH: activar a otro se rechaza; editar notas y permiso sí pasa", async () => {
    const db = new FakeD1();
    db.seed("influencers", { id: 1, tenant_id: "sebastian", name: "Otro", platform: "twitch", profile_url: "", language: "es", permission: 0, active: 0, notes: "", created_at: 1000 });
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    const activate = await call(env, "PATCH", "/api/influencers/1", { token: tok, body: { active: true } });
    expect(activate.status).toBe(400);
    expect(String(activate.json.error)).toContain("10 del lanzamiento");
    const edit = await call(env, "PATCH", "/api/influencers/1", { token: tok, body: { notes: "pendiente de permiso", permission: true } });
    expect(edit.status).toBe(200);
    expect((edit.json.influencer as Record<string, unknown>).notes).toBe("pendiente de permiso");
    expect((edit.json.influencer as Record<string, unknown>).active).toBe(false);
  });

  test("PATCH: renombrar a IlloJuan y activar en el mismo cambio sí pasa", async () => {
    const db = new FakeD1();
    db.seed("influencers", { id: 1, tenant_id: "sebastian", name: "Otro", platform: "twitch", profile_url: "", language: "es", permission: 0, active: 0, notes: "", created_at: 1000 });
    const env = makeEnv(db);
    const r = await call(env, "PATCH", "/api/influencers/1", {
      token: await tokenFor("seba", "sebastian"),
      body: { handle: "@IlloJuan", active: true, permission: true },
    });
    expect(r.status).toBe(200);
    expect((r.json.influencer as Record<string, unknown>).active).toBe(true);
  });

  test("PATCH y DELETE de inexistente: 404", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await tokenFor("seba", "sebastian");
    expect((await call(env, "PATCH", "/api/influencers/99", { token: tok, body: { notes: "x" } })).status).toBe(404);
    expect((await call(env, "DELETE", "/api/influencers/99", { token: tok })).status).toBe(404);
  });

  test("DELETE borra y ya no aparece en la lista", async () => {
    const db = new FakeD1();
    db.seed("influencers", { id: 1, tenant_id: "sebastian", name: "Otro", platform: "twitch", profile_url: "", language: "es", permission: 0, active: 0, notes: "", created_at: 1000 });
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    expect((await call(env, "DELETE", "/api/influencers/1", { token: tok })).status).toBe(200);
    const list = await call(env, "GET", "/api/influencers", { token: tok });
    expect(list.json.influencers).toEqual([]);
  });

  test("aislamiento por tenant en influencers: sesión de otro tenant da 401; en su Worker no ve lo ajeno", async () => {
    const db = new FakeD1();
    db.seed("influencers", { id: 1, tenant_id: "sebastian", name: "IlloJuan", platform: "twitch", profile_url: "", language: "es", permission: 1, active: 1, notes: "", created_at: 1000 });
    const env = makeEnv(db);
    const tokK = await tokenFor("kaez", "kaez");
    expect((await call(env, "GET", "/api/influencers", { token: tokK })).status).toBe(401);
    expect((await call(env, "PATCH", "/api/influencers/1", { token: tokK, body: { notes: "x" } })).status).toBe(401);
    expect((await call(env, "DELETE", "/api/influencers/1", { token: tokK })).status).toBe(401);
    // En el Worker de kaez, la misma sesión no ve el influencer de sebastian.
    const envK = makeEnv(db, { TENANT: "kaez" });
    const kaez = await call(envK, "GET", "/api/influencers", { token: tokK });
    expect(kaez.json.influencers).toEqual([]);
    const patch = await call(envK, "PATCH", "/api/influencers/1", { token: tokK, body: { notes: "x" } });
    expect(patch.status).toBe(404);
    const del = await call(envK, "DELETE", "/api/influencers/1", { token: tokK });
    expect(del.status).toBe(404);
  });
});

describe("GET /api/sources", () => {
  test("sin filas: lista vacía y last_vod_check null", async () => {
    const env = makeEnv(new FakeD1());
    const r = await call(env, "GET", "/api/sources", { token: await tokenFor("seba", "sebastian") });
    expect(r.status).toBe(200);
    expect(r.json.sources).toEqual([]);
    expect(r.json.last_vod_check).toBeNull();
  });

  test("devuelve las fuentes del tenant y la última revisión del cerebro", async () => {
    const db = new FakeD1();
    db.seed("sources", { id: 1, tenant_id: "sebastian", url: "https://youtube.com/watch?v=1", platform: "youtube", title: "VOD 1", streamer: "IlloJuan", status: "procesada", progress: 100, status_note: "", influencer_id: null, game: "GTA 6", created_at: 1000, updated_at: 2000 });
    db.seed("sources", { id: 2, tenant_id: "kaez", url: "https://youtube.com/watch?v=2", platform: "youtube", title: "VOD kaez", streamer: "IlloJuan", status: "pendiente", progress: 0, status_note: "", influencer_id: null, game: "", created_at: 1000, updated_at: 2000 });
    db.seed("vod_checks", { id: 1, tenant_id: "sebastian", ran_at: 3000, summary: "2 VODs nuevos", findings: 2 });
    db.seed("vod_checks", { id: 2, tenant_id: "sebastian", ran_at: 1000, summary: "vieja", findings: 0 });
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/sources", { token: await tokenFor("seba", "sebastian") });
    expect((r.json.sources as unknown[]).length).toBe(1);
    expect((r.json.sources as { title: string }[])[0]?.title).toBe("VOD 1");
    expect((r.json.last_vod_check as { summary: string }).summary).toBe("2 VODs nuevos");
  });

  test("GET /api/sources/:id: 200 con la fila, 404 si no existe o es de otro tenant", async () => {
    const db = new FakeD1();
    db.seed("sources", { id: 1, tenant_id: "sebastian", url: "https://youtube.com/watch?v=1", platform: "youtube", title: "VOD 1", streamer: "IlloJuan", status: "procesada", progress: 100, status_note: "", influencer_id: null, game: "GTA 6", created_at: 1000, updated_at: 2000 });
    db.seed("sources", { id: 2, tenant_id: "kaez", url: "https://youtube.com/watch?v=2", platform: "youtube", title: "VOD kaez", streamer: "IlloJuan", status: "pendiente", progress: 0, status_note: "", influencer_id: null, game: "", created_at: 1000, updated_at: 2000 });
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    const ok = await call(env, "GET", "/api/sources/1", { token: tok });
    expect(ok.status).toBe(200);
    expect((ok.json.source as { title: string }).title).toBe("VOD 1");
    expect((await call(env, "GET", "/api/sources/99", { token: tok })).status).toBe(404);
    expect((await call(env, "GET", "/api/sources/2", { token: tok })).status).toBe(404);
  });
});

describe("POST /api/clips (crear clip en la Cola, 2026-11-18)", () => {
  const valid = {
    title: "IlloJuan no ve el 67",
    start_sec: 25380,
    end_sec: 25420,
    platforms: ["youtube_shorts", "tiktok", "instagram_reels", "facebook_reels"],
    seo_title: "IlloJuan no ve el 67",
    seo_description: "El chat se llena de 67 y de ciego.",
    subtitles_complete: 1,
    game: "GTA VI",
  };

  test("sin sesión responde 401", async () => {
    const env = makeEnv(new FakeD1());
    expect((await call(env, "POST", "/api/clips", { body: valid })).status).toBe(401);
  });

  test("crea el clip en 'en_carpeta', desbloqueado con subtítulos completos, y aparece en la Cola", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await tokenFor("seba", "sebastian");
    const r = await call(env, "POST", "/api/clips", { token: tok, body: valid });
    expect(r.status).toBe(201);
    const clip = r.json.clip as Record<string, unknown>;
    expect(clip.title).toBe("IlloJuan no ve el 67");
    expect(clip.status).toBe("en_carpeta");
    expect(clip.start_sec).toBe(25380);
    expect(clip.end_sec).toBe(25420);
    expect(clip.publish_blocked).toBe(0);
    expect(clip.subtitles_complete).toBe(1);
    expect(JSON.parse(String(clip.platforms))).toEqual(valid.platforms);
    const queue = await call(env, "GET", "/api/clips", { token: tok });
    expect((queue.json.clips as unknown[]).length).toBe(1);
    expect(((queue.json.clips as Record<string, unknown>[])[0] as Record<string, unknown>).id).toBe(clip.id);
    const one = await call(env, "GET", `/api/clips/${clip.id}`, { token: tok });
    expect(one.status).toBe(200);
  });

  test("sin subtitles_complete nace bloqueado (regla: sin subtítulos no se publica)", async () => {
    const env = makeEnv(new FakeD1());
    const r = await call(env, "POST", "/api/clips", {
      token: await tokenFor("seba", "sebastian"),
      body: { ...valid, subtitles_complete: 0 },
    });
    expect(r.status).toBe(201);
    expect((r.json.clip as Record<string, unknown>).publish_blocked).toBe(1);
  });

  // Regla 40-60 / 25-35 ELIMINADA 2026-11-21: 25, 30, 38 y 40 s
  // pasan; solo 61 s (tope 60) da 400.
  test("duración sin mínimo: 25, 30, 38 y 40 s pasan, 61 s no", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await tokenFor("seba", "sebastian");
    const exact30 = await call(env, "POST", "/api/clips", { token: tok, body: { ...valid, start_sec: 100, end_sec: 130 } });
    expect(exact30.status).toBe(201);
    const inRange = await call(env, "POST", "/api/clips", { token: tok, body: { ...valid, start_sec: 100, end_sec: 125 } });
    expect(inRange.status).toBe(201);
    const tooLong = await call(env, "POST", "/api/clips", { token: tok, body: { ...valid, start_sec: 100, end_sec: 161 } });
    expect(tooLong.status).toBe(400);
    const short38 = await call(env, "POST", "/api/clips", { token: tok, body: { ...valid, start_sec: 100, end_sec: 138 } });
    expect(short38.status).toBe(201);
    const min40 = await call(env, "POST", "/api/clips", { token: tok, body: { ...valid, start_sec: 100, end_sec: 140 } });
    expect(min40.status).toBe(201);
    expect((min40.json.clip as Record<string, unknown>).end_sec).toBe(140);
  });

  test("ajustes: PUT /api/settings guarda y POST /api/clips respeta la duración de Ajustes", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await tokenFor("seba", "sebastian");
    const put = await call(env, "PUT", "/api/settings", { token: tok, body: { min_duration_s: 45, max_duration_s: 55, layout: "rdjavi", thumbnail_style: "gta6" } });
    expect(put.status).toBe(200);
    const got = await call(env, "GET", "/api/settings", { token: tok });
    expect((got.json.settings as Record<string, unknown>).min_duration_s).toBe(45);
    expect((got.json.settings as Record<string, unknown>).layout).toBe("rdjavi");
    // Un clip de 40 s ahora da 400 (mínimo 45 en Ajustes).
    const r40 = await call(env, "POST", "/api/clips", { token: tok, body: { ...valid, start_sec: 100, end_sec: 140 } });
    expect(r40.status).toBe(400);
    expect(String(r40.json.error)).toMatch(/45/);
    // Uno de 50 s pasa.
    const r50 = await call(env, "POST", "/api/clips", { token: tok, body: { ...valid, start_sec: 200, end_sec: 250 } });
    expect(r50.status).toBe(201);
  });

  test("gate GTA VI: sin game o con GTA V da 400; GTA VI (y variantes) pasa", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await tokenFor("seba", "sebastian");
    // valid ya trae game; quitarlo:
    const { game: _g, ...sinGame } = valid as Record<string, unknown>;
    void _g;
    const rSinGame = await call(env, "POST", "/api/clips", { token: tok, body: sinGame });
    expect(rSinGame.status).toBe(400);
    expect(String(rSinGame.json.error)).toContain("GTA VI");
    const gtaV = await call(env, "POST", "/api/clips", { token: tok, body: { ...valid, game: "GTA V", start_sec: 300, end_sec: 340 } });
    expect(gtaV.status).toBe(400);
    expect(String(gtaV.json.error)).toContain("GTA VI");
    const gtaViLower = await call(env, "POST", "/api/clips", { token: tok, body: { ...valid, game: "gta vi", start_sec: 400, end_sec: 440 } });
    expect(gtaViLower.status).toBe(201);
    expect((gtaViLower.json.clip as Record<string, unknown>).game).toBe("GTA VI");
  });

  test("validación: sin título, sin plataformas, plataforma inválida y end <= start: 400", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await tokenFor("seba", "sebastian");
    const noTitle = await call(env, "POST", "/api/clips", { token: tok, body: { ...valid, title: "  " } });
    expect(noTitle.status).toBe(400);
    const noPlatforms = await call(env, "POST", "/api/clips", { token: tok, body: { ...valid, platforms: [] } });
    expect(noPlatforms.status).toBe(400);
    const badPlatform = await call(env, "POST", "/api/clips", { token: tok, body: { ...valid, platforms: ["youtube"] } });
    expect(badPlatform.status).toBe(400);
    const badRange = await call(env, "POST", "/api/clips", { token: tok, body: { ...valid, start_sec: 200, end_sec: 200 } });
    expect(badRange.status).toBe(400);
    const list = await call(env, "GET", "/api/clips", { token: tok });
    expect(list.json.clips).toEqual([]);
  });

  test("aislamiento por tenant: sesión de kaez en el Worker de sebastian da 401; en su Worker no ve el clip de seba", async () => {
    const db = new FakeD1();
    const env = makeEnv(db);
    await call(env, "POST", "/api/clips", { token: await tokenFor("seba", "sebastian"), body: valid });
    const wrong = await call(env, "GET", "/api/clips", { token: await tokenFor("kaez", "kaez") });
    expect(wrong.status).toBe(401);
    const envK = makeEnv(db, { TENANT: "kaez" });
    const kaez = await call(envK, "GET", "/api/clips", { token: await tokenFor("kaez", "kaez") });
    expect(kaez.json.clips).toEqual([]);
  });

  test("registra el evento 'detectado' en Tracking", async () => {
    const db = new FakeD1();
    const env = makeEnv(db);
    const r = await call(env, "POST", "/api/clips", { token: await tokenFor("seba", "sebastian"), body: valid });
    expect(r.status).toBe(201);
    const clipId = (r.json.clip as Record<string, unknown>).id as number;
    const tracking = await call(env, "GET", `/api/clips/${clipId}/tracking`, { token: await tokenFor("seba", "sebastian") });
    expect(tracking.status).toBe(200);
    const events = tracking.json.events as Record<string, unknown>[];
    expect(events.length).toBe(1);
    expect(events[0]?.step).toBe("detectado");
  });
});

describe("GET /api/panel-users", () => {
  test("sin sesión responde 401", async () => {
    const env = makeEnv(new FakeD1());
    expect((await call(env, "GET", "/api/panel-users")).status).toBe(401);
  });

  test("M1: cada usuario ve solo su propio tenant, con solo los 4 campos públicos, sin password_hash", async () => {
    const db = new FakeD1();
    db.seed("panel_users", { id: 1, username: "seba", tenant: "sebastian", status: "activo", created_at: 1000, password_hash: "pbkdf2$100000$AAA$BBB" });
    db.seed("panel_users", { id: 2, username: "kaez", tenant: "kaez", status: "activo", created_at: 1000, password_hash: "pbkdf2$100000$CCC$DDD" });
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/panel-users", { token: await tokenFor("seba", "sebastian") });
    expect(r.status).toBe(200);
    const users = r.json.users as Record<string, unknown>[];
    expect(users.length).toBe(1);
    expect(users.map((u) => u.username)).toEqual(["seba"]);
    for (const u of users) {
      expect(Object.keys(u).sort()).toEqual(["created_at", "status", "tenant", "username"]);
    }
    const raw = JSON.stringify(r.json);
    expect(raw).not.toContain("password_hash");
    expect(raw).not.toContain("pbkdf2");
    expect(raw).not.toContain("AAA");
    expect(raw).not.toContain("kaez");
    // Una sesión de kaez en el Worker de sebastian no vale (401).
    const wrong = await call(env, "GET", "/api/panel-users", { token: await tokenFor("kaez", "kaez") });
    expect(wrong.status).toBe(401);
    // En el Worker de kaez, el otro tenant ve solo al suyo.
    const envK = makeEnv(db, { TENANT: "kaez" });
    const r2 = await call(envK, "GET", "/api/panel-users", { token: await tokenFor("kaez", "kaez") });
    expect(r2.status).toBe(200);
    expect((r2.json.users as Record<string, unknown>[]).map((u) => u.username)).toEqual(["kaez"]);
  });
});

// --- Frente 2: B1 (logout revoca), M3 (tracking step), M5 (crear
// cuenta API), M13 (GET /api/jobs) ---

describe("B1: logout revoca el token Bearer", () => {
  test("login → logout → /me con el mismo Bearer da 401", async () => {
    const env = makeEnv(new FakeD1());
    const token = await tokenFor("seba", "sebastian");
    const meBefore = await call(env, "GET", "/api/auth/me", { token });
    expect(meBefore.status).toBe(200);
    const out = await call(env, "POST", "/api/auth/logout", { token });
    expect(out.status).toBe(200);
    const meAfter = await call(env, "GET", "/api/auth/me", { token });
    expect(meAfter.status).toBe(401);
  });

  test("logout sin token sigue devolviendo 200 y limpia la cookie", async () => {
    const env = makeEnv(new FakeD1());
    const out = await call(env, "POST", "/api/auth/logout");
    expect(out.status).toBe(200);
  });

  test("revocar un token no afecta a un token nuevo del mismo usuario", async () => {
    const env = makeEnv(new FakeD1());
    const oldToken = await tokenFor("seba", "sebastian");
    await call(env, "POST", "/api/auth/logout", { token: oldToken });
    // El token solo lleva exp en ms (sin nonce): dos tokens creados
    // en el mismo milisegundo son idénticos. Esperar 2 ms para que
    // el token nuevo sea distinto del revocado (flake 2026-11-18:
    // fallaba 1 de cada 2-3 pasadas de la suite).
    await new Promise((r) => setTimeout(r, 2));
    const fresh = await tokenFor("seba", "sebastian");
    expect(fresh).not.toBe(oldToken);
    const me = await call(env, "GET", "/api/auth/me", { token: fresh });
    expect(me.status).toBe(200);
  });
});

describe("M3: POST /api/tracking/step", () => {
  function seedClip(db: FakeD1, id: number, tenant: string): void {
    db.seed("clips", { id, tenant_id: tenant, title: "Clip", start_sec: 0, end_sec: 40, status: "en_carpeta" });
  }

  test("sin sesión responde 401", async () => {
    const env = makeEnv(new FakeD1());
    expect((await call(env, "POST", "/api/tracking/step", { body: { clip_id: 1, step: "detectado" } })).status).toBe(401);
  });

  test("registra el paso y se ve en GET /api/clips/:id/tracking", async () => {
    const db = new FakeD1();
    seedClip(db, 1, "sebastian");
    const env = makeEnv(db);
    const r = await call(env, "POST", "/api/tracking/step", {
      token: await tokenFor("seba", "sebastian"),
      body: { clip_id: 1, step: "cortando", estado: "completado", message: "Corte terminado." },
    });
    expect(r.status).toBe(201);
    expect(r.json.event).toMatchObject({ clip_id: 1, step: "cortando", estado: "completado", actor: "seba" });
    const tr = await call(env, "GET", "/api/clips/1/tracking", { token: await tokenFor("seba", "sebastian") });
    expect(tr.status).toBe(200);
    const events = tr.json.events as Record<string, unknown>[];
    expect(events.length).toBe(1);
    expect(events[0]?.step).toBe("cortando");
    expect(events[0]?.message).toBe("Corte terminado.");
  });

  test("estado fallido marca failed=1; 404 si el clip es de otro tenant", async () => {
    const db = new FakeD1();
    seedClip(db, 1, "sebastian");
    seedClip(db, 2, "kaez");
    const env = makeEnv(db);
    const token = await tokenFor("seba", "sebastian");
    const r = await call(env, "POST", "/api/tracking/step", {
      token,
      body: { clip_id: 1, step: "subtitulos", estado: "fallido", message: "Sin voz detectada." },
    });
    expect(r.status).toBe(201);
    expect(r.json.event).toMatchObject({ failed: true, estado: "fallido" });
    const other = await call(env, "POST", "/api/tracking/step", {
      token,
      body: { clip_id: 2, step: "detectado" },
    });
    expect(other.status).toBe(404);
  });

  test("step fuera de la lista canónica y estado inválido dan 400", async () => {
    const db = new FakeD1();
    seedClip(db, 1, "sebastian");
    const env = makeEnv(db);
    const token = await tokenFor("seba", "sebastian");
    const badStep = await call(env, "POST", "/api/tracking/step", { token, body: { clip_id: 1, step: "inventado" } });
    expect(badStep.status).toBe(400);
    const badState = await call(env, "POST", "/api/tracking/step", { token, body: { clip_id: 1, step: "detectado", estado: "raro" } });
    expect(badState.status).toBe(400);
    const noClip = await call(env, "POST", "/api/tracking/step", { token, body: { step: "detectado" } });
    expect(noClip.status).toBe(400);
  });

  test("step revalidado (loop auto-seo) se registra y se ve en tracking", async () => {
    const db = new FakeD1();
    seedClip(db, 1, "sebastian");
    const env = makeEnv(db);
    const token = await tokenFor("seba", "sebastian");
    const r = await call(env, "POST", "/api/tracking/step", {
      token,
      body: { clip_id: 1, step: "revalidado", estado: "completado", message: "revalidación automática: SEO 85 veredicto aprobado" },
    });
    expect(r.status).toBe(201);
    expect(r.json.event).toMatchObject({ clip_id: 1, step: "revalidado", estado: "completado" });
    const tr = await call(env, "GET", "/api/clips/1/tracking", { token });
    expect(tr.status).toBe(200);
    const events = tr.json.events as Record<string, unknown>[];
    expect(events.some((e) => e.step === "revalidado")).toBe(true);
  });
});

describe("M5: POST /api/accounts", () => {
  test("sin sesión responde 401", async () => {
    const env = makeEnv(new FakeD1());
    expect((await call(env, "POST", "/api/accounts", { body: { platform: "youtube" } })).status).toBe(401);
  });

  test("crea la cuenta sin credencial (pendiente) y aparece en GET /api/accounts", async () => {
    const env = makeEnv(new FakeD1());
    const token = await tokenFor("seba", "sebastian");
    const r = await call(env, "POST", "/api/accounts", {
      token,
      body: { platform: "tiktok", language: "es", label: "TikTok ES" },
    });
    expect(r.status).toBe(201);
    expect(r.json.account).toMatchObject({ platform: "tiktok", language: "es", label: "TikTok ES", status: "pendiente", has_credential: false });
    const list = await call(env, "GET", "/api/accounts", { token });
    expect(list.status).toBe(200);
    const accs = list.json.accounts as Record<string, unknown>[];
    expect(accs.length).toBe(1);
    expect(accs[0]?.platform).toBe("tiktok");
  });

  test("plataforma o idioma inválidos dan 400; duplicado da 409", async () => {
    const db = new FakeD1();
    db.seed("api_accounts", { id: 1, tenant_id: "sebastian", platform: "youtube", language: "es", label: "YT", credential: "", credential_enc: "", extra: "", extra_enc: "", status: "pendiente", note: "", created_at: 1, verified_at: null, verify_ok: null, verify_message: "" });
    const env = makeEnv(db);
    const token = await tokenFor("seba", "sebastian");
    expect((await call(env, "POST", "/api/accounts", { token, body: { platform: "instagram" } })).status).toBe(400);
    expect((await call(env, "POST", "/api/accounts", { token, body: { platform: "youtube", language: "fr" } })).status).toBe(400);
    const dup = await call(env, "POST", "/api/accounts", { token, body: { platform: "youtube", language: "es" } });
    expect(dup.status).toBe(409);
    // El mismo platform+language en el otro tenant sí se puede crear
    // (en SU Worker); en este Worker la sesión de kaez da 401.
    expect((await call(env, "POST", "/api/accounts", { token: await tokenFor("kaez", "kaez"), body: { platform: "youtube", language: "es" } })).status).toBe(401);
    const envK = makeEnv(db, { TENANT: "kaez" });
    const other = await call(envK, "POST", "/api/accounts", { token: await tokenFor("kaez", "kaez"), body: { platform: "youtube", language: "es" } });
    expect(other.status).toBe(201);
  });
});

describe("M13: GET /api/jobs", () => {
  // El caché de /api/jobs (60 s, a nivel de módulo) lo comparte todo
  // el archivo: sin limpiarlo, el [] del test de "vacío" envenena
  // al test siguiente (misma key tenant|limit|state).
  beforeEach(() => {
    clearJobsCache();
  });

  function seedJob(db: FakeD1, row: Record<string, unknown>): void {
    db.seed("jobs", { kind: "publish", clip_id: 1, platform: "youtube_shorts", title: "t", attempts: 0, last_error: null, platform_id: null, evidence_url: null, created_at: 1000, updated_at: 1000, ...row });
  }

  test("sin sesión responde 401; vacío devuelve jobs: []", async () => {
    const env = makeEnv(new FakeD1());
    expect((await call(env, "GET", "/api/jobs")).status).toBe(401);
    const r = await call(env, "GET", "/api/jobs", { token: await tokenFor("seba", "sebastian") });
    expect(r.status).toBe(200);
    expect(r.json.jobs).toEqual([]);
  });

  test("cada tenant ve solo sus jobs, ordenados por created_at DESC, con filtro state", async () => {
    const db = new FakeD1();
    seedJob(db, { id: 1, tenant_id: "sebastian", state: "posted", created_at: 1000 });
    seedJob(db, { id: 2, tenant_id: "sebastian", state: "failed", created_at: 3000 });
    seedJob(db, { id: 3, tenant_id: "kaez", state: "posted", created_at: 2000 });
    const env = makeEnv(db);
    const token = await tokenFor("seba", "sebastian");
    const r = await call(env, "GET", "/api/jobs", { token });
    expect(r.status).toBe(200);
    const jobs = r.json.jobs as Record<string, unknown>[];
    expect(jobs.map((j) => j.id)).toEqual([2, 1]);
    const failed = await call(env, "GET", "/api/jobs?state=failed", { token });
    expect((failed.json.jobs as Record<string, unknown>[]).map((j) => j.id)).toEqual([2]);
    const bad = await call(env, "GET", "/api/jobs?state=raro", { token });
    expect(bad.status).toBe(400);
  });
});

// --- Un Worker = un tenant (2026-11-19, refactor single-tenant) ---
describe("un Worker = un tenant (requireTenant + fronteras)", () => {
  test("requireTenant: sin TENANT o con valor inválido falla en voz alta; válido lo devuelve", () => {
    expect(() => requireTenant({} as Env)).toThrow("TENANT no configurado");
    expect(() => requireTenant({ TENANT: "" } as Env)).toThrow("TENANT no configurado");
    expect(() => requireTenant({ TENANT: "colega" } as Env)).toThrow("TENANT no configurado");
    expect(requireTenant({ TENANT: "sebastian" } as Env)).toBe("sebastian");
    expect(requireTenant({ TENANT: "kaez" } as Env)).toBe("kaez");
  });

  test("verifySessionToken: una sesión del otro tenant no verifica en este Worker", async () => {
    const tokK = await createSessionToken("kaez", "kaez", SECRET);
    expect(await verifySessionToken(tokK, SECRET, "sebastian")).toBeNull();
    const back = await verifySessionToken(tokK, SECRET, "kaez");
    expect(back?.tenant).toBe("kaez");
    expect(back?.sub).toBe("kaez");
  });

  test("/api/health devuelve SOLO este tenant (sin lista de tenants)", async () => {
    const db = new FakeD1();
    db.seed("worker_state", { tenant_id: "sebastian", running: 1, started_at: 1000, last_note: "", updated_at: 1000 });
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/health");
    expect(r.status).toBe(200);
    expect(r.json.app).toBe("avc");
    expect(r.json.tenant).toBe("sebastian");
    expect(r.json.worker_running).toBe(true);
    expect("tenants" in r.json).toBe(false);
    const envK = makeEnv(db, { TENANT: "kaez" });
    const rk = await call(envK, "GET", "/api/health");
    expect(rk.json.tenant).toBe("kaez");
    expect(rk.json.worker_running).toBe(false);
  });

  test("/api/auth/me con sesión del otro tenant da 401", async () => {
    const env = makeEnv(new FakeD1());
    expect((await call(env, "GET", "/api/auth/me", { token: await tokenFor("kaez", "kaez") })).status).toBe(401);
    expect((await call(env, "GET", "/api/auth/me", { token: await tokenFor("seba", "sebastian") })).status).toBe(200);
  });

  test("login: un usuario del otro tenant da 401 aunque la contraseña sea correcta", async () => {
    const db = new FakeD1();
    db.seed("panel_users", { id: 1, username: "seba", tenant: "sebastian", status: "activo", created_at: 1000, password_hash: await hashPassword("clave.seba") });
    db.seed("panel_users", { id: 2, username: "kaez", tenant: "kaez", status: "activo", created_at: 1000, password_hash: await hashPassword("clave.kaez") });
    const env = makeEnv(db);
    // El propio tenant sí entra.
    const ok = await call(env, "POST", "/api/auth/login", { body: { username: "seba", password: "clave.seba" } });
    expect(ok.status).toBe(200);
    expect(ok.json.tenant).toBe("sebastian");
    // El usuario de kaez, con su contraseña correcta, en el Worker de sebastian: 401 genérico.
    const wrong = await call(env, "POST", "/api/auth/login", { body: { username: "kaez", password: "clave.kaez" } });
    expect(wrong.status).toBe(401);
    // En el Worker de kaez, ese mismo usuario entra.
    const envK = makeEnv(db, { TENANT: "kaez" });
    const okK = await call(envK, "POST", "/api/auth/login", { body: { username: "kaez", password: "clave.kaez" } });
    expect(okK.status).toBe(200);
    expect(okK.json.tenant).toBe("kaez");
  });
});
