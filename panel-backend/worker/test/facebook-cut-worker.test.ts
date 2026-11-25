// Tests del Worker — entrada de Facebook (spec 2026-11-25, §4):
// isFacebookUrl, resolución por plataforma con dos/tres Nonis
// (source_key "fb:..." → entrada facebook, estricto: sin entrada
// facebook → 400 sin fallback a Twitch), clip de 18,85 s con los
// ajustes por defecto, endpoint POST /api/worker/run-facebook con
// exención explícita (freno de reintentos + throttle de 15 min) y
// GET /api/influencers/live con una fila platform=facebook.
// Sin red, sin D1 real: mock en memoria de D1Database
// (subconjunto SQL del Worker + COUNT(*)).
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { createSessionToken } from "../src/auth";
import { clearBudgetCounterCache, clearCurrentBudgetGuard } from "../src/budget";
import type { Env } from "../src/cloudflare";
import worker, { isFacebookUrl } from "../src/index";
import { noteDispatchFailure, resetDispatchFailureState, resetQuotaBreakerState } from "../src/retry";

const SECRET = Buffer.from("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb").toString("base64"); // SOLO TEST
const KEY = Buffer.from("0123456789abcdef0123456789abcdef").toString("base64");

type Row = Record<string, unknown>;

// --- Mock D1 en memoria ---
function splitTop(s: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let quote = "";
  let cur = "";
  for (const ch of s) {
    if (quote) {
      cur += ch;
      if (ch === quote) quote = "";
      continue;
    }
    if (ch === "'" || ch === '"') quote = ch;
    if (ch === "(") depth++;
    if (ch === ")") depth--;
    if (ch === "," && depth === 0) {
      out.push(cur.trim());
      cur = "";
      continue;
    }
    cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

function parseWhere(where: string, params: unknown[]): (r: Row) => boolean {
  const conds = splitTop(where.replace(/\s+AND\s+/gi, "\u0001")).flatMap((c) => c.split("\u0001")).map((c) => c.trim()).filter(Boolean);
  let pi = 0;
  const tests: Array<(r: Row) => boolean> = [];
  for (const c of conds) {
    const eq = /^(\w+)\s*=\s*\?$/.exec(c);
    const neq = /^(\w+)\s*<>\s*\?$/.exec(c);
    const lt = /^(\w+)\s*<\s*\?$/.exec(c);
    const gt = /^(\w+)\s*>\s*\?$/.exec(c);
    const eqLit = /^(\w+)\s*=\s*'([^']*)'$/.exec(c);
    const neqLit = /^(\w+)\s*<>\s*'([^']*)'$/.exec(c);
    const eqNum = /^(\w+)\s*=\s*(-?\d+)$/.exec(c);
    const isNull = /^(\w+)\s+IS\s+NULL$/i.exec(c);
    const isNotNull = /^(\w+)\s+IS\s+NOT\s+NULL$/i.exec(c);
    const like = /^(\w+)\s+LIKE\s+\?$/.exec(c);
    const inList = /^(\w+)\s+IN\s*\(([^)]+)\)$/i.exec(c);
    if (eq) {
      const col = eq[1] as string;
      const val = params[pi++];
      tests.push((r) => r[col] === val);
    } else if (neq) {
      const col = neq[1] as string;
      const val = params[pi++];
      tests.push((r) => r[col] !== val);
    } else if (lt) {
      const col = lt[1] as string;
      const val = params[pi++];
      tests.push((r) => (r[col] as number) < (val as number));
    } else if (gt) {
      const col = gt[1] as string;
      const val = params[pi++];
      tests.push((r) => (r[col] as number) > (val as number));
    } else if (eqLit) {
      const col = eqLit[1] as string;
      tests.push((r) => r[col] === eqLit[2]);
    } else if (neqLit) {
      const col = neqLit[1] as string;
      tests.push((r) => r[col] !== neqLit[2]);
    } else if (eqNum) {
      const col = eqNum[1] as string;
      tests.push((r) => r[col] === Number(eqNum[2]));
    } else if (isNull) {
      const col = isNull[1] as string;
      tests.push((r) => r[col] === null || r[col] === undefined);
    } else if (isNotNull) {
      const col = isNotNull[1] as string;
      tests.push((r) => r[col] !== null && r[col] !== undefined);
    } else if (like) {
      const col = like[1] as string;
      const pat = String(params[pi++]).replace(/%/g, "");
      tests.push((r) => String(r[col] ?? "").includes(pat));
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
    const m = /^(\w+)(?:\s+(ASC|DESC))?$/i.exec(part.trim());
    if (!m) throw new Error(`ORDER BY no soportado en el mock: ${part}`);
    return { col: m[1] as string, desc: (m[2] ?? "ASC").toUpperCase() === "DESC" };
  });
  return [...rows].sort((a, b) => {
    for (const { col, desc } of keys) {
      const av = a[col] as number | string | null;
      const bv = b[col] as number | string | null;
      if (av === bv) continue;
      const cmp = av == null ? -1 : bv == null ? 1 : av < bv ? -1 : 1;
      return desc ? -cmp : cmp;
    }
    return 0;
  });
}

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
    const m = /^SELECT\s+(.+?)\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+?))?(?:\s+ORDER BY\s+(.+?))?(?:\s+LIMIT\s+(\d+))?\s*$/is.exec(sql);
    if (!m) throw new Error(`SELECT no soportado en el mock: ${sql}`);
    const [, colsRaw, table, where, orderBy, limitRaw] = m as [string, string, string, string | undefined, string | undefined, string | undefined];
    let rows = [...(this.tables[table as string] ?? [])];
    if (where) rows = rows.filter(parseWhere(where, params));
    rows = applyOrder(rows, orderBy);
    if (limitRaw) rows = rows.slice(0, Number(limitRaw));
    const cols = (colsRaw as string).trim();
    if (/^COUNT\(\*\)\s+AS\s+n$/i.test(cols)) return [{ n: rows.length }];
    if (cols === "*") return rows;
    const wanted = cols.split(",").map((c) => c.trim());
    return rows.map((r) => {
      const out: Row = {};
      for (const c of wanted) out[c] = r[c];
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
      const vals = splitTop(valsRaw);
      const rows = (this.tables[table as string] ??= []);
      const id = rows.reduce((max, r) => Math.max(max, Number(r.id ?? 0)), 0) + 1;
      const row: Row = { id };
      let pi = 0;
      vals.forEach((v, i) => {
        if (v === "?") row[cols[i] as string] = params[pi++];
        else if (/^NULL$/i.test(v)) row[cols[i] as string] = null;
        else if (/^'([^']*)'$/.test(v)) row[cols[i] as string] = /^'([^']*)'$/.exec(v)?.[1];
        else if (/^-?\d+$/.test(v)) row[cols[i] as string] = Number(v);
        else throw new Error(`INSERT valor no soportado en el mock: ${v}`);
      });
      if (table === "clips") {
        const defaults: Record<string, unknown> = {
          archived: 0, preview_seen: 0, file_deleted: 0, published_at: null,
          viral: "{}", seo_overrides: "[]", publish_states: "{}", origin: "vod",
          source_key: "",
        };
        for (const [k, v] of Object.entries(defaults)) if (row[k] === undefined) row[k] = v;
      }
      rows.push(row);
      return { results: [], success: true, meta: { changes: 1, last_row_id: id } };
    }
    const update = /^UPDATE\s+(\w+)\s+SET\s+(.+?)\s+WHERE\s+(.+)$/is.exec(sql);
    if (update) {
      const [, table, setRaw, where] = update as [string, string, string];
      const setCols: string[] = [];
      const setVals: unknown[] = [];
      let pi = 0;
      for (const part of splitTop(setRaw)) {
        const ph = /^(\w+)\s*=\s*\?$/.exec(part);
        const lit = /^(\w+)\s*=\s*'([^']*)'$/.exec(part);
        const num = /^(\w+)\s*=\s*(-?\d+)$/.exec(part);
        const nul = /^(\w+)\s*=\s*NULL$/i.exec(part);
        if (ph) {
          setCols.push(ph[1] as string);
          setVals.push(params[pi++]);
        } else if (lit) {
          setCols.push(lit[1] as string);
          setVals.push(lit[2]);
        } else if (num) {
          setCols.push(num[1] as string);
          setVals.push(Number(num[2]));
        } else if (nul) {
          setCols.push(nul[1] as string);
          setVals.push(null);
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

function makeEnv(db: FakeD1, over: Partial<Env> = {}): Env {
  return {
    DB: db as unknown as Env["DB"],
    TENANT: "sebastian",
    CLIPS_BUCKET: { head: async () => ({}) } as unknown as Env["CLIPS_BUCKET"],
    SESSION_SECRET: SECRET,
    CREDENTIAL_ENCRYPTION_KEY: KEY,
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
) {
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
beforeEach(() => {
  resetDispatchFailureState();
  resetQuotaBreakerState();
  clearBudgetCounterCache();
  clearCurrentBudgetGuard();
});
afterEach(() => {
  globalThis.fetch = realFetch;
  resetDispatchFailureState();
  resetQuotaBreakerState();
  clearBudgetCounterCache();
  clearCurrentBudgetGuard();
});

function influencerRow(id: number, name: string, platform: string, over: Partial<Row> = {}): Row {
  return {
    id, tenant_id: "sebastian", name, platform,
    profile_url: platform === "youtube" ? `https://www.youtube.com/@${name.toLowerCase()}` : `https://www.twitch.tv/${name.toLowerCase()}`,
    language: "es", permission: 1, active: 1, notes: "", created_at: 1,
    game: "GTA VI", permission_date: "2026-11-18",
    publish_platforms: '["youtube","tiktok","instagram","facebook"]',
    ...over,
  };
}

function seedTwoRubius(db: FakeD1): void {
  db.seed("influencers", influencerRow(1, "Rubius", "twitch"));
  db.seed("influencers", influencerRow(2, "Rubius", "youtube"));
}

function validClipBody(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    title: "Rubius consigue 6 estrellas en GTA 6",
    start_sec: 100, end_sec: 150, score: 56, game: "GTA VI",
    platforms: ["youtube_shorts"], language: "es", subtitles_complete: 1,
    influencer_id: "rubius",
    viral: {
      viral_score: 52,
      seo_scores: { youtube_shorts: 90, tiktok: 91, instagram_reels: 89, facebook_reels: 88 },
    },
    ...over,
  };
}
// ---------------------------------------------------------------------------
// isFacebookUrl (spec §3.1/§3.4: mismo predicado que is_facebook_url)
// ---------------------------------------------------------------------------
describe("isFacebookUrl", () => {
  test("facebook.com/reel, /share/r/, /watch/?v= y m.facebook.com → true", () => {
    expect(isFacebookUrl("https://www.facebook.com/reel/1918676049105232/")).toBe(true);
    expect(isFacebookUrl("https://www.facebook.com/share/r/18cMsFx95G/")).toBe(true);
    expect(isFacebookUrl("https://www.facebook.com/watch/?v=1918676049105232")).toBe(true);
    expect(isFacebookUrl("https://m.facebook.com/reel/1918676049105232/")).toBe(true);
    expect(isFacebookUrl("https://facebook.com/reel/1918676049105232")).toBe(true);
  });

  test("fb.watch con cualquier ruta → true (el host manda)", () => {
    expect(isFacebookUrl("https://fb.watch/abc123XYZ/")).toBe(true);
    expect(isFacebookUrl("https://fb.watch/x")).toBe(true);
  });

  test("otros hosts y rutas sin reel/share/watch → false", () => {
    expect(isFacebookUrl("https://www.youtube.com/watch?v=NrNdw_Y6_bI")).toBe(false);
    expect(isFacebookUrl("https://www.twitch.tv/noni")).toBe(false);
    // /watch/ sin v= no es un reel.
    expect(isFacebookUrl("https://www.facebook.com/watch/")).toBe(false);
    // Perfil de Facebook sin /reel/ ni /share/r/ no es un reel.
    expect(isFacebookUrl("https://facebook.com/people/Clips-de-Noni/61559354617566/")).toBe(false);
    expect(isFacebookUrl("https://facebook.com.evil.com/reel/123/")).toBe(false);
    expect(isFacebookUrl("no-es-una-url")).toBe(false);
    expect(isFacebookUrl("")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// POST /api/clips: resolución por plataforma con tres Nonis (spec §3.3)
// ---------------------------------------------------------------------------
function seedThreeNonis(db: FakeD1): void {
  db.seed("influencers", influencerRow(1, "Noni", "twitch"));
  db.seed("influencers", influencerRow(2, "Noni", "youtube"));
  db.seed("influencers", influencerRow(3, "Noni", "facebook", {
    profile_url: "https://facebook.com/people/Clips-de-Noni/61559354617566/",
  }));
}

function noniClipBody(over: Record<string, unknown> = {}): Record<string, unknown> {
  return validClipBody({
    title: "Noni intenta un vuelo cinematográfico con una avioneta y se estrella",
    influencer_id: "noni",
    ...over,
  });
}

describe("POST /api/clips con tres Nonis (spec §3.3)", () => {
  test('con source_key "fb:..." → id de la entrada facebook', async () => {
    const db = new FakeD1();
    seedThreeNonis(db);
    const env = makeEnv(db);
    const r = await call(env, "POST", "/api/clips", {
      token: await tokenFor("seba", "sebastian"),
      body: noniClipBody({ source_key: "fb:1918676049105232", source_id: 1918676049105232 }),
    });
    expect(r.status).toBe(201);
    const clip = r.json.clip as Record<string, unknown>;
    expect(clip.influencer_id).toBe(3);
    expect(clip.source_key).toBe("fb:1918676049105232");
  });

  test("con source_key de YouTube → id de la entrada youtube", async () => {
    const db = new FakeD1();
    seedThreeNonis(db);
    const env = makeEnv(db);
    const r = await call(env, "POST", "/api/clips", {
      token: await tokenFor("seba", "sebastian"),
      body: noniClipBody({ source_key: "NrNdw_Y6_bI", source_id: 0 }),
    });
    expect(r.status).toBe(201);
    expect((r.json.clip as Record<string, unknown>).influencer_id).toBe(2);
  });

  test("sin source_key → id de la entrada twitch", async () => {
    const db = new FakeD1();
    seedThreeNonis(db);
    const env = makeEnv(db);
    const r = await call(env, "POST", "/api/clips", {
      token: await tokenFor("seba", "sebastian"),
      body: noniClipBody({ source_id: 2928823801 }),
    });
    expect(r.status).toBe(201);
    expect((r.json.clip as Record<string, unknown>).influencer_id).toBe(1);
  });

  test('"fb:" sin entrada facebook → 400 SIN caer a Twitch', async () => {
    const db = new FakeD1();
    db.seed("influencers", influencerRow(1, "Noni", "twitch"));
    db.seed("influencers", influencerRow(2, "Noni", "youtube"));
    const env = makeEnv(db);
    const tok = await tokenFor("seba", "sebastian");
    const r = await call(env, "POST", "/api/clips", {
      token: tok,
      body: noniClipBody({ source_key: "fb:1918676049105232", source_id: 1918676049105232 }),
    });
    expect(r.status).toBe(400);
    expect(String(r.json.error)).toContain("noni");
    // Y no se creó ningún clip (el fallback a Twitch no encoló nada).
    const list = await call(env, "GET", "/api/clips", { token: tok });
    expect(list.json.clips).toEqual([]);
  });

  test("el fallback a cualquier plataforma se mantiene para YouTube/Twitch", async () => {
    // Solo existe la entrada Twitch de Noni: un clip de YouTube
    // (source_key no vacío, sin entrada youtube) cae a Twitch
    // como hoy (compat declarada en la spec de YouTube).
    const db = new FakeD1();
    db.seed("influencers", influencerRow(1, "Noni", "twitch"));
    const env = makeEnv(db);
    const r = await call(env, "POST", "/api/clips", {
      token: await tokenFor("seba", "sebastian"),
      body: noniClipBody({ source_key: "NrNdw_Y6_bI", source_id: 0 }),
    });
    expect(r.status).toBe(201);
    expect((r.json.clip as Record<string, unknown>).influencer_id).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// POST /api/clips: duración 18,85 s con los ajustes por defecto (§3.2)
// ---------------------------------------------------------------------------
describe("POST /api/clips acepta el reel de 18,85 s (spec §3.2)", () => {
  test("clip de 18,85 s → 201 con min_duration_s=1 por defecto", async () => {
    // El Worker guarda start_sec/end_sec enteros: 18,85 s se
    // redondea a 19 s (100 → 119). La puerta es 1–60 s por
    // defecto, así que pasa sin ningún cambio en el Worker.
    const db = new FakeD1();
    seedThreeNonis(db);
    const env = makeEnv(db);
    const r = await call(env, "POST", "/api/clips", {
      token: await tokenFor("seba", "sebastian"),
      body: noniClipBody({ source_key: "fb:1918676049105232", start_sec: 100, end_sec: 119 }),
    });
    expect(r.status).toBe(201);
    const clip = r.json.clip as Record<string, unknown>;
    expect(clip.end_sec - (clip.start_sec as number)).toBe(19);
  });

  test("un clip de 10 s también pasa (el suelo de 40 s quedó eliminado)", async () => {
    const db = new FakeD1();
    seedThreeNonis(db);
    const env = makeEnv(db);
    const r = await call(env, "POST", "/api/clips", {
      token: await tokenFor("seba", "sebastian"),
      body: noniClipBody({ start_sec: 0, end_sec: 10 }),
    });
    expect(r.status).toBe(201);
  });
});

// ---------------------------------------------------------------------------
// POST /api/worker/run-facebook (spec §3.4)
// ---------------------------------------------------------------------------
describe("POST /api/worker/run-facebook (spec §3.4)", () => {
  test("URL no-Facebook → 400", async () => {
    const env = makeEnv(new FakeD1(), { GITHUB_TOKEN: "gh-test" });
    const r = await call(env, "POST", "/api/worker/run-facebook", {
      token: await tokenFor("seba", "sebastian"),
      body: { facebook_url: "https://youtu.be/NrNdw_Y6_bI" },
    });
    expect(r.status).toBe(400);
    expect(String(r.json.error)).toContain("Facebook");
  });

  test("sin sesión → 401", async () => {
    const env = makeEnv(new FakeD1());
    const r = await call(env, "POST", "/api/worker/run-facebook", {
      body: { facebook_url: "https://www.facebook.com/share/r/18cMsFx95G/" },
    });
    expect(r.status).toBe(401);
  });

  test("dispatch fallido (sin GITHUB_TOKEN) → ok:false con motivo", async () => {
    const env = makeEnv(new FakeD1(), { GITHUB_TOKEN: "" });
    const r = await call(env, "POST", "/api/worker/run-facebook", {
      token: await tokenFor("seba", "sebastian"),
      body: { facebook_url: "https://www.facebook.com/share/r/18cMsFx95G/" },
    });
    expect(r.json.ok).toBe(false);
    expect(String(r.json.error ?? "")).toContain("No se pudo disparar");
  });

  test("dispatch OK: client_payload lleva facebook_url (y no youtube_url)", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { GITHUB_TOKEN: "gh-test" });
    const seen: Array<Record<string, unknown>> = [];
    globalThis.fetch = (async (input: unknown, init?: { body?: unknown }) => {
      const url = typeof input === "string" ? input : (input as { url: string }).url;
      if (url.includes("api.github.com")) {
        seen.push(JSON.parse(String((init as { body?: string }).body ?? "{}")) as Record<string, unknown>);
      }
      return new Response(JSON.stringify({}), { status: 201, headers: { "Content-Type": "application/json" } });
    }) as typeof fetch;
    const r = await call(env, "POST", "/api/worker/run-facebook", {
      token: await tokenFor("seba", "sebastian"),
      body: { facebook_url: "https://www.facebook.com/share/r/18cMsFx95G/" },
    });
    expect(r.json.ok).toBe(true);
    expect(r.json.facebook_url).toBe("https://www.facebook.com/share/r/18cMsFx95G/");
    expect(seen.length).toBe(1);
    const payload = (seen[0]?.client_payload ?? null) as Record<string, unknown> | null;
    expect(payload?.facebook_url).toBe("https://www.facebook.com/share/r/18cMsFx95G/");
    expect(payload?.youtube_url).toBeUndefined();
    expect(payload?.tenant).toBe("sebastian");
    expect(typeof payload?.brain_token).toBe("string");
  });

  test("tras un panel-live fallido reciente el freno de reintentos NO lo frena (ni el throttle de 15 min)", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { GITHUB_TOKEN: "gh-test" });
    // 3 fallos de panel-live hoy → dispatchRetryAllowed = false.
    const now = Date.now();
    noteDispatchFailure("panel-live", now);
    noteDispatchFailure("panel-live", now);
    noteDispatchFailure("panel-live", now);
    // Token reciente (< 15 min) → el throttle normal también frenaría.
    db.seed("brain_tokens", { token_hash: "h", tenant_id: "sebastian", expires_at: now + 600_000, used_at: null, created_at: now });
    let githubCalls = 0;
    globalThis.fetch = (async (input: unknown) => {
      const url = typeof input === "string" ? input : (input as { url: string }).url;
      if (url.includes("api.github.com")) githubCalls++;
      return new Response(JSON.stringify({}), { status: 201, headers: { "Content-Type": "application/json" } });
    }) as typeof fetch;
    const r = await call(env, "POST", "/api/worker/run-facebook", {
      token: await tokenFor("seba", "sebastian"),
      body: { facebook_url: "https://www.facebook.com/share/r/18cMsFx95G/" },
    });
    expect(githubCalls).toBe(1);
    expect(r.json.ok).toBe(true);
  });

  test("dispatch rechazado por GitHub tras freno reciente → ok:false (no éxito falso)", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { GITHUB_TOKEN: "gh-test" });
    const now = Date.now();
    noteDispatchFailure("panel-live", now);
    noteDispatchFailure("panel-live", now);
    noteDispatchFailure("panel-live", now);
    globalThis.fetch = (async () => {
      return new Response(JSON.stringify({ message: "Bad credentials" }), { status: 401, headers: { "Content-Type": "application/json" } });
    }) as typeof fetch;
    const r = await call(env, "POST", "/api/worker/run-facebook", {
      token: await tokenFor("seba", "sebastian"),
      body: { facebook_url: "https://www.facebook.com/share/r/18cMsFx95G/" },
    });
    expect(r.json.ok).toBe(false);
    expect(String(r.json.error ?? "")).toContain("No se pudo disparar");
  });
});

// ---------------------------------------------------------------------------
// GET /api/influencers/live con una fila platform=facebook
// ---------------------------------------------------------------------------
describe("GET /api/influencers/live con entrada facebook", () => {
  test("la fila facebook aparece con live null y motivo (sin romperse)", async () => {
    const db = new FakeD1();
    seedThreeNonis(db);
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/influencers/live", { token: await tokenFor("seba", "sebastian") });
    expect(r.status).toBe(200);
    const live = r.json.live as Array<{ handle: string; platform: string; live: boolean | null; reason: string }>;
    const fb = live.find((x) => x.platform === "facebook");
    expect(fb).toBeDefined();
    expect(fb?.handle).toBe("Noni");
    expect(fb?.live).toBeNull();
    expect(fb?.reason).toContain("facebook");
    expect(live.find((x) => x.platform === "twitch")?.handle).toBe("Noni");
  });
});
