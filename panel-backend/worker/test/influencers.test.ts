// Tests de influencers (2026-11-18): los 10 del lanzamiento con
// game GTA VI, fecha de permiso y plataformas; regla de activación
// ampliada; estado en vivo/offline (sin inventar: sin credenciales
// de Twitch, "sin verificar" con el motivo).
import { describe, expect, test, afterEach } from "bun:test";
import { createSessionToken } from "../src/auth";
import type { Env } from "../src/cloudflare";
import { encryptCredential } from "../src/crypto";
import worker from "../src/index";

const SECRET = Buffer.from("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb").toString("base64"); // SOLO TEST
const KEY = Buffer.from("0123456789abcdef0123456789abcdef").toString("base64");

type Row = Record<string, unknown>;

class FakeStmt {
  constructor(private db: FakeD1, private sql: string, private params: unknown[] = []) {}
  bind(...values: unknown[]): FakeStmt {
    return new FakeStmt(this.db, this.sql, values);
  }
  async first<T = Record<string, unknown>>(): Promise<T | null> {
    return (this.db.runSelect(this.sql, this.params)[0] as T | undefined) ?? null;
  }
  async all<T = Record<string, unknown>>(): Promise<{ results: T[]; success: boolean; meta: Record<string, unknown> }> {
    return { results: this.db.runSelect(this.sql, this.params) as T[], success: true, meta: {} };
  }
  async run(): Promise<{ results: never[]; success: boolean; meta: { changes?: number; last_row_id?: number } }> {
    return this.db.runWrite(this.sql, this.params);
  }
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
    const m = /^SELECT\s+(.+?)\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+?))?(?:\s+ORDER BY\s+.+?)?\s*$/is.exec(sql);
    if (!m) throw new Error(`SELECT no soportado en el mock: ${sql}`);
    const [, colsRaw, table, where] = m as [string, string, string, string | undefined];
    let rows = [...(this.tables[table as string] ?? [])];
    if (where) {
      const conds = where.split(/\s+AND\s+/i).map((c) => c.trim());
      let pi = 0;
      for (const c of conds) {
        const eq = /^(\w+)\s*=\s*\?$/.exec(c);
        if (!eq) throw new Error(`WHERE no soportado en el mock: ${c}`);
        const col = eq[1] as string;
        const val = params[pi++];
        rows = rows.filter((r) => r[col] === val);
      }
    }
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
      const parts = setRaw.split(",").map((p) => p.trim());
      const setCols: string[] = [];
      const setVals: unknown[] = [];
      let pi = 0;
      for (const part of parts) {
        const ph = /^(\w+)\s*=\s*\?$/.exec(part);
        const lit = /^(\w+)\s*=\s*'([^']*)'$/.exec(part);
        if (ph) {
          setCols.push(ph[1] as string);
          setVals.push(params[pi++]);
        } else if (lit) {
          setCols.push(lit[1] as string);
          setVals.push(lit[2]);
        } else {
          throw new Error(`SET no soportado en el mock: ${part}`);
        }
      }
      const whereParams = params.slice(pi);
      const rows = this.tables[table as string] ?? [];
      const conds = where.split(/\s+AND\s+/i).map((c) => c.trim());
      let wpi = 0;
      const tests: Array<(r: Row) => boolean> = [];
      for (const c of conds) {
        const eq = /^(\w+)\s*=\s*\?$/.exec(c);
        if (!eq) throw new Error(`WHERE no soportado en el mock: ${c}`);
        const col = eq[1] as string;
        const val = whereParams[wpi++];
        tests.push((r) => r[col] === val);
      }
      let changes = 0;
      for (const r of rows) {
        if (!tests.every((t) => t(r))) continue;
        setCols.forEach((c, i) => {
          r[c] = setVals[i];
        });
        changes++;
      }
      return { results: [], success: true, meta: { changes } };
    }
    throw new Error(`Escritura no soportada en el mock: ${sql}`);
  }
}

function makeEnv(db: FakeD1): Env {
  return {
    DB: db as unknown as Env["DB"],
    TENANT: "sebastian",
    CLIPS_BUCKET: {} as Env["CLIPS_BUCKET"],
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

async function call(env: Env, method: string, path: string, opts: { token?: string; body?: Record<string, unknown> } = {}) {
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
  const text = await res.text();
  return { status: res.status, json: JSON.parse(text) as Record<string, unknown> };
}

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

describe("influencers: los 10 del lanzamiento", () => {
  test("POST AuronPlay activo: 201 con game GTA VI y plataformas", async () => {
    const env = makeEnv(new FakeD1());
    const r = await call(env, "POST", "/api/influencers", {
      token: await tokenFor("seba", "sebastian"),
      body: {
        handle: "AuronPlay", platform: "twitch", url: "https://www.twitch.tv/auronplay",
        language: "es", permission: true, active: true,
        game: "GTA VI", permission_date: "2026-11-18",
        publish_platforms: ["youtube", "tiktok", "instagram", "facebook"],
      },
    });
    expect(r.status).toBe(201);
    const inf = r.json.influencer as Record<string, unknown>;
    expect(inf.game).toBe("GTA VI");
    expect(inf.permission_date).toBe("2026-11-18");
    expect(inf.publish_platforms).toEqual(["youtube", "tiktok", "instagram", "facebook"]);
    expect(inf.active).toBe(true);
  });

  test("POST con game GTA V: 400 categoría", async () => {
    const env = makeEnv(new FakeD1());
    const r = await call(env, "POST", "/api/influencers", {
      token: await tokenFor("seba", "sebastian"),
      body: { handle: "AuronPlay", platform: "twitch", game: "GTA V", permission: true, active: true },
    });
    expect(r.status).toBe(400);
    expect(String(r.json.error)).toContain("GTA VI");
  });

  test("POST de un handle fuera de los 10 activo: 400 con la lista", async () => {
    const env = makeEnv(new FakeD1());
    const r = await call(env, "POST", "/api/influencers", {
      token: await tokenFor("seba", "sebastian"),
      body: { handle: "OtroStreamer", platform: "twitch", permission: true, active: true },
    });
    expect(r.status).toBe(400);
    expect(String(r.json.error)).toContain("10 del lanzamiento");
  });

  test("PATCH reactiva IlloJuan (ya no está pausado)", async () => {
    const db = new FakeD1();
    db.seed("influencers", {
      id: 1, tenant_id: "sebastian", name: "IlloJuan", platform: "twitch",
      profile_url: "https://www.twitch.tv/illojuan", language: "es",
      permission: 1, active: 0, notes: "", created_at: 1,
      game: "GTA VI", permission_date: "2026-11-16",
      publish_platforms: '["youtube","tiktok","instagram","facebook"]',
    });
    const env = makeEnv(db);
    const r = await call(env, "PATCH", "/api/influencers/1", {
      token: await tokenFor("seba", "sebastian"),
      body: { active: true },
    });
    expect(r.status).toBe(200);
    expect((r.json.influencer as Record<string, unknown>).active).toBe(true);
  });
});

describe("GET /api/influencers/live", () => {
  test("sin credenciales de Twitch: sin verificar con el motivo exacto", async () => {
    const db = new FakeD1();
    db.seed("influencers", {
      id: 1, tenant_id: "sebastian", name: "Rubius", platform: "twitch",
      profile_url: "https://www.twitch.tv/rubius", language: "es",
      permission: 1, active: 1, notes: "", created_at: 1,
      game: "GTA VI", permission_date: "2026-11-18",
      publish_platforms: '["youtube","tiktok","instagram","facebook"]',
    });
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/influencers/live", { token: await tokenFor("seba", "sebastian") });
    expect(r.status).toBe(200);
    const live = r.json.live as Array<{ live: boolean | null; reason: string }>;
    expect(live[0]?.live).toBeNull();
    expect(live[0]?.reason).toContain("Client ID/Secret de Twitch");
  });

  test("con credenciales de Twitch: Rubius en directo, IlloJuan offline", async () => {
    const db = new FakeD1();
    const credEnc = await encryptCredential("sec-twitch", KEY, "sebastian");
    const extraEnc = await encryptCredential(JSON.stringify({ client_id: "cid-test" }), KEY, "sebastian");
    db.seed("api_accounts", {
      id: 1, tenant_id: "sebastian", platform: "twitch", language: "es", label: "Twitch",
      credential: "", credential_enc: credEnc, extra: "", extra_enc: extraEnc,
      status: "conectada", note: "", created_at: 1, verified_at: 1, verify_ok: 1, verify_message: "ok",
    });
    for (const [id, name] of [[1, "Rubius"], [2, "IlloJuan"]] as Array<[number, string]>) {
      db.seed("influencers", {
        id, tenant_id: "sebastian", name, platform: "twitch",
        profile_url: `https://www.twitch.tv/${name.toLowerCase()}`, language: "es",
        permission: 1, active: 1, notes: "", created_at: 1,
        game: "GTA VI", permission_date: "2026-11-18",
        publish_platforms: '["youtube","tiktok","instagram","facebook"]',
      });
    }
    globalThis.fetch = (async (input: string | URL | Request) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      if (url.includes("id.twitch.tv/oauth2/token")) {
        return new Response(JSON.stringify({ access_token: "app.token" }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.includes("api.twitch.tv/helix/streams")) {
        return new Response(JSON.stringify({ data: [{ user_login: "rubius", title: "GTA VI", viewer_count: 123 }] }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({}), { status: 500, headers: { "Content-Type": "application/json" } });
    }) as typeof fetch;
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/influencers/live", { token: await tokenFor("seba", "sebastian") });
    expect(r.status).toBe(200);
    const live = r.json.live as Array<{ handle: string; live: boolean | null; viewers: number | null }>;
    expect(live.find((x) => x.handle === "Rubius")?.live).toBe(true);
    expect(live.find((x) => x.handle === "Rubius")?.viewers).toBe(123);
    expect(live.find((x) => x.handle === "IlloJuan")?.live).toBe(false);
  });
});
