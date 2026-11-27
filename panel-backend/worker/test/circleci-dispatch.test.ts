// Tests del Worker — recopilación larga en CircleCI (spec 2026-11-26,
// §7.2): triggerCircleCI (exportada, M8) y endpoint POST
// /api/worker/run-compilation, más ?limit= en GET /api/clips (§5.5).
// Sin red, sin D1 real: mock en memoria de D1Database (subconjunto
// SQL del Worker), adaptado de facebook-cut-worker.test.ts, con dos
// extensiones que este camino necesita: LIMIT ? (listPublishedClips
// liga el límite) y ORDER BY COALESCE(published_at, updated_at).
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { createSessionToken } from "../src/auth";
import { clearBudgetCounterCache, clearCurrentBudgetGuard } from "../src/budget";
import type { Env } from "../src/cloudflare";
import worker, {
  CIRCLECI_COMPILATION_FAILURE_PREFIX,
  CIRCLECI_COMPILATION_SUCCESS_PREFIX,
  triggerCircleCI,
} from "../src/index";
import { resetDispatchFailureState, resetQuotaBreakerState, tripQuotaBreaker } from "../src/retry";

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
  const conds = splitTop(where.replace(/\s+AND\s+/gi, "")).flatMap((c) => c.split("")).map((c) => c.trim()).filter(Boolean);
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
  const keys = splitTop(orderBy).map((part) => {
    const coalesce = /^COALESCE\((\w+),\s*(\w+)\)(?:\s+(ASC|DESC))?$/i.exec(part.trim());
    if (coalesce) {
      return { cols: [coalesce[1] as string, coalesce[2] as string], desc: (coalesce[3] ?? "ASC").toUpperCase() === "DESC" };
    }
    const m = /^(\w+)(?:\s+(ASC|DESC))?$/i.exec(part.trim());
    if (!m) throw new Error(`ORDER BY no soportado en el mock: ${part}`);
    return { cols: [m[1] as string], desc: (m[2] ?? "ASC").toUpperCase() === "DESC" };
  });
  return [...rows].sort((a, b) => {
    for (const { cols, desc } of keys) {
      const av = cols.map((c) => a[c]).find((v) => v !== null && v !== undefined) as number | string | null | undefined;
      const bv = cols.map((c) => b[c]).find((v) => v !== null && v !== undefined) as number | string | null | undefined;
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
    const m = /^SELECT\s+(.+?)\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+?))?(?:\s+ORDER BY\s+(.+?))?(?:\s+LIMIT\s+(\d+|\?))?\s*$/is.exec(sql);
    if (!m) throw new Error(`SELECT no soportado en el mock: ${sql}`);
    const [, colsRaw, table, where, orderBy, limitRaw] = m as [string, string, string, string | undefined, string | undefined, string | undefined];
    let rows = [...(this.tables[table as string] ?? [])];
    if (where) rows = rows.filter(parseWhere(where, params));
    rows = applyOrder(rows, orderBy);
    if (limitRaw) {
      // LIMIT ? es siempre el último placeholder ligado.
      const n = limitRaw === "?" ? Number(params[params.length - 1]) : Number(limitRaw);
      rows = rows.slice(0, n);
    }
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
    CIRCLECI_PROJECT_SLUG: "gh/exe2027/viral-clip-agent",
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

function events(db: FakeD1): Row[] {
  return db.tables.worker_events ?? [];
}

function mockCircleCI(status: number, body: Record<string, unknown>): { calls: string[] } {
  const calls: string[] = [];
  globalThis.fetch = (async (input: unknown, init?: { body?: unknown; headers?: unknown }) => {
    const url = typeof input === "string" ? input : (input as { url: string }).url;
    calls.push(url);
    expect(url).toBe("https://circleci.com/api/v2/project/gh/exe2027/viral-clip-agent/pipeline");
    // El token NUNCA viaja en la URL ni en el body.
    expect(url).not.toContain("circle-test");
    expect(String((init as { body?: string }).body ?? "")).not.toContain("circle-test");
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  return { calls };
}

// ---------------------------------------------------------------------------
// triggerCircleCI (spec §5.2, §7.2)
// ---------------------------------------------------------------------------
describe("triggerCircleCI", () => {
  test("sin CIRCLECI_API_TOKEN → {ok:false} y registra el fallo, sin llamar a CircleCI", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { CIRCLECI_API_TOKEN: "" });
    const { calls } = mockCircleCI(201, { id: "pipe-1" });
    const r = await triggerCircleCI(env);
    expect(r.ok).toBe(false);
    expect(calls.length).toBe(0);
    expect(events(db).length).toBe(1);
    expect(String(events(db)[0]?.message ?? "")).toContain("Recopilación NO disparada a CircleCI (sin CIRCLECI_API_TOKEN)");
    expect(String(events(db)[0]?.message ?? "")).not.toContain("circle-test");
  });

  test("circuito 7500 abierto → {ok:false} SIN escribir y sin llamar a CircleCI", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { CIRCLECI_API_TOKEN: "circle-test" });
    tripQuotaBreaker(Date.now());
    const { calls } = mockCircleCI(201, { id: "pipe-1" });
    const r = await triggerCircleCI(env);
    expect(r.ok).toBe(false);
    expect(calls.length).toBe(0);
    expect(events(db).length).toBe(0);
  });

  test("éxito reciente (< 3 h) → {ok:false, throttled:true}, sin llamar y sin escribir", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { CIRCLECI_API_TOKEN: "circle-test" });
    db.seed("worker_events", {
      tenant_id: "sebastian", kind: "circleci_compilation_dispatch",
      message: "Recopilación disparada a CircleCI (pipeline pipe-viejo)", at: Date.now(),
    });
    const { calls } = mockCircleCI(201, { id: "pipe-nuevo" });
    const r = await triggerCircleCI(env);
    expect(r.ok).toBe(false);
    expect((r as { throttled?: boolean }).throttled).toBe(true);
    expect(calls.length).toBe(0);
    expect(events(db).length).toBe(1);
  });

  test("un disparo FALLIDO reciente NO envenena el throttle: 2xx → {ok:true, pipeline_id}", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { CIRCLECI_API_TOKEN: "circle-test" });
    db.seed("worker_events", {
      tenant_id: "sebastian", kind: "circleci_compilation_dispatch",
      message: "Recopilación NO disparada a CircleCI (CircleCI devolvió HTTP 401)", at: Date.now(),
    });
    const { calls } = mockCircleCI(201, { id: "pipe-abc-123" });
    const r = await triggerCircleCI(env);
    expect(r.ok).toBe(true);
    expect((r as { pipeline_id?: string }).pipeline_id).toBe("pipe-abc-123");
    expect(calls.length).toBe(1);
  });

  test("2xx de CircleCI → {ok:true, pipeline_id} y el mensaje de éxito incluye el pipeline_id y casa con el prefijo", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { CIRCLECI_API_TOKEN: "circle-test" });
    mockCircleCI(201, { id: "pipe-xyz-9" });
    const r = await triggerCircleCI(env);
    expect(r.ok).toBe(true);
    expect((r as { pipeline_id?: string }).pipeline_id).toBe("pipe-xyz-9");
    expect(events(db).length).toBe(1);
    const msg = String(events(db)[0]?.message ?? "");
    expect(msg).toBe("Recopilación disparada a CircleCI (pipeline pipe-xyz-9)");
    expect(msg.startsWith(CIRCLECI_COMPILATION_SUCCESS_PREFIX)).toBe(true);
    expect(events(db)[0]?.kind).toBe("circleci_compilation_dispatch");
  });

  test("4xx de CircleCI → {ok:false} y registra el fallo con el motivo", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { CIRCLECI_API_TOKEN: "circle-test" });
    mockCircleCI(401, { message: "Bad credentials" });
    const r = await triggerCircleCI(env);
    expect(r.ok).toBe(false);
    expect(events(db).length).toBe(1);
    const msg = String(events(db)[0]?.message ?? "");
    expect(msg.startsWith(CIRCLECI_COMPILATION_FAILURE_PREFIX)).toBe(true);
    expect(msg).toContain("HTTP 401");
  });

  test("fallo de red → {ok:false} y registra el fallo", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { CIRCLECI_API_TOKEN: "circle-test" });
    globalThis.fetch = (async () => {
      throw new Error("fetch failed");
    }) as typeof fetch;
    const r = await triggerCircleCI(env);
    expect(r.ok).toBe(false);
    expect(events(db).length).toBe(1);
    expect(String(events(db)[0]?.message ?? "")).toContain("Recopilación NO disparada a CircleCI (");
  });
});

// ---------------------------------------------------------------------------
// POST /api/worker/run-compilation (spec §5.1, §7.2)
// ---------------------------------------------------------------------------
describe("POST /api/worker/run-compilation", () => {
  test("sin sesión → 401", async () => {
    const env = makeEnv(new FakeD1(), { CIRCLECI_API_TOKEN: "circle-test" });
    const r = await call(env, "POST", "/api/worker/run-compilation", { body: {} });
    expect(r.status).toBe(401);
  });

  test("con sesión y CircleCI aceptando → 200 {ok:true, pipeline_id}", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { CIRCLECI_API_TOKEN: "circle-test" });
    mockCircleCI(201, { id: "pipe-endpoint-1" });
    const r = await call(env, "POST", "/api/worker/run-compilation", {
      token: await tokenFor("seba", "sebastian"),
      body: {},
    });
    expect(r.status).toBe(200);
    expect(r.json.ok).toBe(true);
    expect(r.json.pipeline_id).toBe("pipe-endpoint-1");
  });

  test("sin token → 502 {ok:false} (nunca éxito falso)", async () => {
    const env = makeEnv(new FakeD1(), { CIRCLECI_API_TOKEN: "" });
    const r = await call(env, "POST", "/api/worker/run-compilation", {
      token: await tokenFor("seba", "sebastian"),
      body: {},
    });
    expect(r.status).toBe(502);
    expect(r.json.ok).toBe(false);
    expect(String(r.json.error ?? "")).toContain("CircleCI");
  });

  test("throttle de 3 h → 200 con {ok:false} (M4: no es error del servidor)", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { CIRCLECI_API_TOKEN: "circle-test" });
    db.seed("worker_events", {
      tenant_id: "sebastian", kind: "circleci_compilation_dispatch",
      message: "Recopilación disparada a CircleCI (pipeline pipe-viejo)", at: Date.now(),
    });
    const { calls } = mockCircleCI(201, { id: "pipe-nuevo" });
    const r = await call(env, "POST", "/api/worker/run-compilation", {
      token: await tokenFor("seba", "sebastian"),
      body: {},
    });
    expect(r.status).toBe(200);
    expect(r.json.ok).toBe(false);
    expect(String(r.json.error ?? "")).toContain("menos de 3 h");
    expect(calls.length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// GET /api/clips?status=publicado&limit= (spec §5.5)
// ---------------------------------------------------------------------------
describe("GET /api/clips?status=publicado&limit=", () => {
  function seedPublished(db: FakeD1): void {
    for (let i = 1; i <= 3; i++) {
      db.seed("clips", {
        id: i, tenant_id: "sebastian", title: `Clip publicado ${i}`, status: "publicado",
        archived: 0, published_at: 1000 + i, updated_at: 1000 + i, viral: "{}",
      });
    }
  }

  test("limit=2 devuelve 2 de 3", async () => {
    const db = new FakeD1();
    seedPublished(db);
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/clips?status=publicado&limit=2", {
      token: await tokenFor("seba", "sebastian"),
    });
    expect(r.status).toBe(200);
    expect((r.json.clips as unknown[]).length).toBe(2);
  });

  test("sin limit → default 50 (devuelve los 3)", async () => {
    const db = new FakeD1();
    seedPublished(db);
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/clips?status=publicado", {
      token: await tokenFor("seba", "sebastian"),
    });
    expect(r.status).toBe(200);
    expect((r.json.clips as unknown[]).length).toBe(3);
  });

  test("limit=500 devuelve los 3 (el driver pide 500)", async () => {
    const db = new FakeD1();
    seedPublished(db);
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/clips?status=publicado&limit=500", {
      token: await tokenFor("seba", "sebastian"),
    });
    expect(r.status).toBe(200);
    expect((r.json.clips as unknown[]).length).toBe(3);
  });

  test("limit=0 se clamp a 1 y limit=abc cae al default 50", async () => {
    const db = new FakeD1();
    seedPublished(db);
    const env = makeEnv(db);
    const r0 = await call(env, "GET", "/api/clips?status=publicado&limit=0", {
      token: await tokenFor("seba", "sebastian"),
    });
    expect((r0.json.clips as unknown[]).length).toBe(1);
    const rAbc = await call(env, "GET", "/api/clips?status=publicado&limit=abc", {
      token: await tokenFor("seba", "sebastian"),
    });
    expect((rAbc.json.clips as unknown[]).length).toBe(3);
  });
});
