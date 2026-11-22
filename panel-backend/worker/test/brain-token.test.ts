// Tests del cerebro genérico: token de un solo uso + credenciales
// desde el panel + throttle en vivo por tenant + dispatch diario
// (2026-11-19, el panel es la ÚNICA interfaz: sin secretos por
// tenant en GitHub, sin environments por tenant).
// Sin red real (fetch con mock), sin D1 real: mock en memoria de
// D1Database copiado de analytics-auto.test.ts (+ IS NULL, que
// usa el canje atómico del token).
import { afterEach, describe, expect, test } from "bun:test";
import { tokenFingerprint } from "../src/auth";
import { encryptCredential } from "../src/crypto";
import type { Env } from "../src/cloudflare";
import { issueBrainToken, liveRunInFlight, redeemBrainToken, resolveBrainCredentials } from "../src/brain";
import worker, { liveRunStatus, maybeDispatchDaily } from "../src/index";

const SECRET = Buffer.from("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb").toString("base64"); // SOLO TEST
const KEY = Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"); // 32 bytes, SOLO TEST
const R2_BASE = "https://pub-5f09f432fcae41dcb9e4d114ce8ff987.r2.dev";
const NINE_AM = Date.parse("2026-11-19T12:00:00Z"); // 09:00 Santiago
const TEN_AM = Date.parse("2026-11-19T13:00:00Z"); // 10:00 Santiago

type Row = Record<string, unknown>;

// --- Mock D1 en memoria (copia del de analytics-auto.test.ts) ---
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
    const isNull = /^(\w+)\s+IS\s+NULL$/i.exec(c);
    const lt = /^(\w+)\s*<=?\s*\?$/.exec(c);
    const gt = /^(\w+)\s*>=?\s*\?$/.exec(c);
    const eqNum = /^(\w+)\s*=\s*(\d+)$/.exec(c);
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
    } else if (isNull) {
      const col = isNull[1] as string;
      tests.push((r) => r[col] === null || r[col] === undefined);
    } else if (eqNum) {
      const col = eqNum[1] as string;
      const num = Number(eqNum[2]);
      tests.push((r) => r[col] === num);
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

function makeEnv(db: FakeD1, over: Partial<Env> = {}): Env {
  return {
    DB: db as unknown as Env["DB"],
    TENANT: "sebastian",
    CLIPS_BUCKET: {} as unknown as Env["CLIPS_BUCKET"],
    SESSION_SECRET: SECRET,
    CREDENTIAL_ENCRYPTION_KEY: KEY,
    BRIDGE_TOKEN: "bridge-test",
    GITHUB_TOKEN: "gh-test",
    ALLOWED_EMAIL: "",
    GITHUB_REPO: "exe2027/viral-clip-agent",
    CLIP_PUBLIC_BASE_URL: R2_BASE,
    ...over,
  };
}

const ctx = { waitUntil: () => {}, passThroughOnException: () => {} };

async function callRaw(
  env: Env,
  method: string,
  path: string,
  opts: { token?: string; body?: Record<string, unknown> } = {},
): Promise<{ status: number; text: string; json: Record<string, unknown> | null }> {
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
  let json: Record<string, unknown> | null = null;
  try {
    json = JSON.parse(text) as Record<string, unknown>;
  } catch {
    json = null;
  }
  return { status: res.status, text, json };
}

async function call(env: Env, method: string, path: string, opts: { token?: string; body?: Record<string, unknown> } = {}) {
  const r = await callRaw(env, method, path, opts);
  if (!r.json) throw new Error(`Respuesta sin JSON (${r.status}): ${r.text.slice(0, 200)}`);
  return { status: r.status, json: r.json };
}

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

function stubGithub(status: number, calls: { url: string; body: Record<string, unknown> }[]): void {
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
    calls.push({ url, body });
    return new Response(JSON.stringify({}), { status, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
}

async function seedApiAccount(
  db: FakeD1,
  over: { tenant: string; platform: string; language?: string; credential: string; extra?: Record<string, unknown> },
): Promise<void> {
  const credentialEnc = await encryptCredential(over.credential, KEY, over.tenant);
  const extraEnc = over.extra ? await encryptCredential(JSON.stringify(over.extra), KEY, over.tenant) : "";
  db.seed("api_accounts", {
    id: (db.tables.api_accounts?.length ?? 0) + 1,
    tenant_id: over.tenant,
    platform: over.platform,
    language: over.language ?? "es",
    label: `${over.platform}-${over.tenant}`,
    credential: "",
    credential_enc: credentialEnc,
    extra: "",
    extra_enc: extraEnc,
    status: "conectada",
    note: "",
    created_at: 1,
    verified_at: 2000,
    verify_ok: 1,
    verify_message: "ok",
  });
}

function settingsData(db: FakeD1, tenant: string): Record<string, unknown> {
  const row = (db.tables.settings ?? []).find((s) => s.tenant_id === tenant);
  return row ? (JSON.parse(String(row.data)) as Record<string, unknown>) : {};
}

describe("token de un solo uso (brain_tokens)", () => {
  test("issue + redeem: el canje devuelve el tenant y el segundo canje falla", async () => {
    const db = new FakeD1();
    const raw = await issueBrainToken(db as never, "sebastian");
    expect(typeof raw).toBe("string");
    expect(raw.length).toBeGreaterThan(20);
    expect(await redeemBrainToken(db as never, raw, "sebastian")).toBe("sebastian");
    await expect(redeemBrainToken(db as never, raw, "sebastian")).rejects.toThrow("ya canjeado");
  });

  test("en D1 solo queda la huella SHA-256, nunca el token en claro", async () => {
    const db = new FakeD1();
    const raw = await issueBrainToken(db as never, "kaez");
    const rows = db.tables.brain_tokens ?? [];
    expect(rows.length).toBe(1);
    expect(rows[0]?.token_hash).not.toBe(raw);
    expect(rows[0]?.token_hash).toBe(await tokenFingerprint(raw));
    expect(JSON.stringify(rows)).not.toContain(raw);
    expect(rows[0]?.tenant_id).toBe("kaez");
    expect(rows[0]?.used_at).toBeNull();
  });

  test("token desconocido: inválido; token caducado: caducado", async () => {
    const db = new FakeD1();
    await expect(redeemBrainToken(db as never, "no-existe", "sebastian")).rejects.toThrow("inválido");
    const raw = "token-caducado-test";
    db.seed("brain_tokens", {
      token_hash: await tokenFingerprint(raw),
      tenant_id: "sebastian",
      expires_at: Date.now() - 1000,
      used_at: null,
      created_at: Date.now() - 31 * 60_000,
    });
    await expect(redeemBrainToken(db as never, raw, "sebastian")).rejects.toThrow("caducado");
  });

  test("el token lleva su tenant: en el Worker de sebastian un token de kaez se rechaza SIN consumirlo; en el de kaez canjea", async () => {
    const db = new FakeD1();
    const raw = await issueBrainToken(db as never, "kaez");
    await expect(redeemBrainToken(db as never, raw, "sebastian")).rejects.toThrow("otro tenant");
    // El rechazo no consume el token: el router sin estado prueba
    // primero en un Worker y luego en el otro con el mismo token.
    expect(await redeemBrainToken(db as never, raw, "kaez")).toBe("kaez");
  });
});

describe("resolveBrainCredentials (credenciales desde el panel)", () => {
  test("round-trip: descifra twitch/gemini/youtube/zernio/meta del tenant", async () => {
    const db = new FakeD1();
    await seedApiAccount(db, { tenant: "sebastian", platform: "twitch", credential: "tw.secret", extra: { client_id: "tw-id" } });
    await seedApiAccount(db, { tenant: "sebastian", platform: "gemini", credential: "gem-key" });
    await seedApiAccount(db, { tenant: "sebastian", platform: "youtube", language: "es", credential: "", extra: { client_id: "yt-id", client_secret: "yt-sec", refresh_token: "yt-ref" } });
    await seedApiAccount(db, { tenant: "sebastian", platform: "youtube", language: "en", credential: "", extra: { client_id: "yt-en-id", client_secret: "yt-en.sec", refresh_token: "yt-en-ref" } });
    await seedApiAccount(db, { tenant: "sebastian", platform: "tiktok", language: "es", credential: "zernio.key", extra: { via: "zernio", account_id: "acc-es" } });
    await seedApiAccount(db, { tenant: "sebastian", platform: "meta", credential: "page-token", extra: { ig_user_id: "17841", page_id: "13462" } });
    const env = makeEnv(db);
    const c = await resolveBrainCredentials(env, "sebastian");
    expect(c.twitch).toEqual({ client_id: "tw-id", client_secret: "tw.secret" });
    expect(c.gemini).toEqual({ api_key: "gem-key" });
    expect(c.youtube.es).toEqual({ client_id: "yt-id", client_secret: "yt-sec", refresh_token: "yt-ref" });
    expect(c.youtube.en).toEqual({ client_id: "yt-en-id", client_secret: "yt-en.sec", refresh_token: "yt-en-ref" });
    expect(c.zernio.es).toEqual({ api_key: "zernio.key", account_id: "acc-es" });
    expect(c.zernio.en).toBeNull();
    expect(c.tiktok_direct.es).toBeNull();
    expect(c.meta).toEqual({ page_token: "page-token", ig_user_id: "17841", page_id: "13462" });
    expect(c.clip_public_base_url).toBe(R2_BASE);
  });

  test("youtube desde el JSON de la credencial cuando extra no trae los tres campos", async () => {
    const db = new FakeD1();
    await seedApiAccount(db, {
      tenant: "sebastian", platform: "youtube", language: "es",
      credential: JSON.stringify({ client_id: "c-id", client_secret: "c-sec", refresh_token: "c-ref" }),
    });
    const c = await resolveBrainCredentials(makeEnv(db), "sebastian");
    expect(c.youtube.es).toEqual({ client_id: "c-id", client_secret: "c-sec", refresh_token: "c-ref" });
  });

  test("aislamiento por tenant: kaez sin twitch => twitch null; cada uno ve solo lo suyo", async () => {
    const db = new FakeD1();
    await seedApiAccount(db, { tenant: "sebastian", platform: "twitch", credential: "tw.secret", extra: { client_id: "tw-id" } });
    await seedApiAccount(db, { tenant: "sebastian", platform: "youtube", language: "es", credential: "", extra: { client_id: "yt-seba", client_secret: "s", refresh_token: "r" } });
    await seedApiAccount(db, { tenant: "kaez", platform: "youtube", language: "es", credential: "", extra: { client_id: "yt-kaez", client_secret: "s", refresh_token: "r" } });
    await seedApiAccount(db, { tenant: "kaez", platform: "gemini", credential: "gem.kaez" });
    const env = makeEnv(db);
    const seba = await resolveBrainCredentials(env, "sebastian");
    expect(seba.youtube.es?.client_id).toBe("yt-seba");
    expect(seba.twitch?.client_id).toBe("tw-id");
    expect(seba.gemini).toBeNull();
    const kaez = await resolveBrainCredentials(env, "kaez");
    expect(kaez.youtube.es?.client_id).toBe("yt-kaez");
    expect(kaez.twitch).toBeNull();
    expect(kaez.gemini).toEqual({ api_key: "gem.kaez" });
    const vacio = await resolveBrainCredentials(env, "sebastian");
    expect(vacio.tiktok_direct.es).toBeNull();
    expect(vacio.tiktok_direct.en).toBeNull();
  });
});

describe("throttle en vivo por tenant (D1, worker_phases)", () => {
  test("fase reciente de vivo => en curso; terminal o vieja => libre", async () => {
    const db = new FakeD1();
    const now = Date.now();
    db.seed("worker_phases", { tenant_id: "sebastian", phase: "buscando_picos", updated_at: now - 60_000 });
    expect(await liveRunInFlight(db as never, "sebastian", now)).toBe(true);
    const db2 = new FakeD1();
    db2.seed("worker_phases", { tenant_id: "sebastian", phase: "sin_directos", updated_at: now - 60_000 });
    expect(await liveRunInFlight(db2 as never, "sebastian", now)).toBe(false);
    const db3 = new FakeD1();
    db3.seed("worker_phases", { tenant_id: "sebastian", phase: "cortando_clip", updated_at: now - 30 * 60_000 });
    expect(await liveRunInFlight(db3 as never, "sebastian", now)).toBe(false);
  });

  test("la corrida de sebastian no frena a kaez (liveRunStatus por tenant)", async () => {
    const db = new FakeD1();
    const now = Date.now();
    db.seed("worker_phases", { tenant_id: "sebastian", phase: "buscando_picos", updated_at: now - 60_000 });
    const env = makeEnv(db);
    expect(await liveRunStatus(env, "sebastian")).toBe("busy");
    expect(await liveRunStatus(env, "kaez")).toBe("clear");
  });
});

describe("maybeDispatchDaily (pasada diaria 09:00 Santiago)", () => {
  test("a las 09:00 dispara panel-daily con tenant + token canjeable, y marca el día", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { TENANT: "kaez" });
    const calls: { url: string; body: Record<string, unknown> }[] = [];
    stubGithub(200, calls);
    await maybeDispatchDaily(env, "kaez", NINE_AM);
    expect(calls.length).toBe(1);
    expect(calls[0]?.url).toContain("/repos/exe2027/viral-clip-agent/dispatches");
    expect(calls[0]?.body.event_type).toBe("panel-daily");
    const payload = calls[0]?.body.client_payload as Record<string, unknown>;
    expect(payload.tenant).toBe("kaez");
    expect(typeof payload.brain_token).toBe("string");
    // El token del payload canjea de verdad y devuelve el tenant.
    expect(await redeemBrainToken(db as never, String(payload.brain_token), "kaez")).toBe("kaez");
    expect(settingsData(db, "kaez").brain_daily_dispatched_day).toBe("2026-11-19");
  });

  test("segunda llamada el mismo día no re-dispara", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { TENANT: "kaez" });
    const calls: { url: string; body: Record<string, unknown> }[] = [];
    stubGithub(200, calls);
    await maybeDispatchDaily(env, "kaez", NINE_AM);
    await maybeDispatchDaily(env, "kaez", NINE_AM + 60_000);
    expect(calls.length).toBe(1);
  });

  test("fuera de las 09:00 no dispara", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { TENANT: "kaez" });
    const calls: { url: string; body: Record<string, unknown> }[] = [];
    stubGithub(200, calls);
    await maybeDispatchDaily(env, "kaez", TEN_AM);
    expect(calls.length).toBe(0);
    expect(settingsData(db, "kaez").brain_daily_dispatched_day).toBeUndefined();
  });

  test("si GitHub falla (500), no marca el día y el siguiente ciclo reintenta", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { TENANT: "kaez" });
    const calls: { url: string; body: Record<string, unknown> }[] = [];
    stubGithub(500, calls);
    await maybeDispatchDaily(env, "kaez", NINE_AM);
    expect(calls.length).toBe(1);
    expect(settingsData(db, "kaez").brain_daily_dispatched_day).toBeUndefined();
    stubGithub(200, calls);
    await maybeDispatchDaily(env, "kaez", NINE_AM + 5 * 60_000);
    expect(calls.length).toBe(2);
    expect(settingsData(db, "kaez").brain_daily_dispatched_day).toBe("2026-11-19");
  });

  test("no dispara para otro tenant: en el Worker de sebastian, maybeDispatchDaily(kaez) no llega a GitHub ni marca el día", async () => {
    const db = new FakeD1();
    const env = makeEnv(db);
    const calls: { url: string; body: Record<string, unknown> }[] = [];
    stubGithub(200, calls);
    await maybeDispatchDaily(env, "kaez", NINE_AM);
    expect(calls.length).toBe(0);
    expect(settingsData(db, "kaez").brain_daily_dispatched_day).toBeUndefined();
  });
});

describe("HTTP: /api/brain/session + /api/bridge/credentials", () => {
  test("canje por HTTP: sesión de 12 h; segundo canje del mismo token da 401", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { TENANT: "kaez" });
    const raw = await issueBrainToken(db as never, "kaez");
    const r = await call(env, "POST", "/api/brain/session", { body: { token: raw } });
    expect(r.status).toBe(200);
    expect(r.json.tenant).toBe("kaez");
    expect(typeof r.json.token).toBe("string");
    expect((r.json.token as string).split(".").length).toBe(2);
    const r2 = await callRaw(env, "POST", "/api/brain/session", { body: { token: raw } });
    expect(r2.status).toBe(401);
    const r3 = await callRaw(env, "POST", "/api/brain/session", { body: { token: "no-existe" } });
    expect(r3.status).toBe(401);
    const r4 = await callRaw(env, "POST", "/api/brain/session", { body: {} });
    expect(r4.status).toBe(400);
  });

  test("canje por HTTP en el Worker equivocado: un token de kaez en el Worker de sebastian da 401 sin consumirlo", async () => {
    const db = new FakeD1();
    const env = makeEnv(db);
    const raw = await issueBrainToken(db as never, "kaez");
    const r = await callRaw(env, "POST", "/api/brain/session", { body: { token: raw } });
    expect(r.status).toBe(401);
    // Sin consumir: en el Worker de kaez el mismo token canjea.
    const envK = makeEnv(db, { TENANT: "kaez" });
    const ok = await call(envK, "POST", "/api/brain/session", { body: { token: raw } });
    expect(ok.status).toBe(200);
    expect(ok.json.tenant).toBe("kaez");
  });

  test("con la sesión del cerebro, /api/bridge/credentials devuelve solo sus credenciales", async () => {
    const db = new FakeD1();
    await seedApiAccount(db, { tenant: "kaez", platform: "youtube", language: "es", credential: "", extra: { client_id: "yt-kaez", client_secret: "s", refresh_token: "r" } });
    await seedApiAccount(db, { tenant: "kaez", platform: "gemini", credential: "gem.kaez" });
    await seedApiAccount(db, { tenant: "sebastian", platform: "youtube", language: "es", credential: "", extra: { client_id: "yt-seba", client_secret: "s", refresh_token: "r" } });
    const env = makeEnv(db, { TENANT: "kaez" });
    const raw = await issueBrainToken(db as never, "kaez");
    const session = await call(env, "POST", "/api/brain/session", { body: { token: raw } });
    const sessionToken = String(session.json.token);
    const r = await call(env, "GET", "/api/bridge/credentials", { token: sessionToken });
    expect(r.status).toBe(200);
    expect(r.json.tenant).toBe("kaez");
    const youtube = r.json.youtube as Record<string, Record<string, string> | null>;
    expect(youtube.es?.client_id).toBe("yt-kaez");
    expect(youtube.en).toBeNull();
    expect((r.json.gemini as Record<string, string> | null)?.api_key).toBe("gem.kaez");
    expect(r.json.twitch).toBeNull();
    expect(r.json.clip_public_base_url).toBe(R2_BASE);
  });

  test("sin sesión, /api/bridge/credentials da 401; una sesión de sebastian no ve lo de kaez", async () => {
    const db = new FakeD1();
    await seedApiAccount(db, { tenant: "kaez", platform: "gemini", credential: "gem.kaez" });
    await seedApiAccount(db, { tenant: "sebastian", platform: "gemini", credential: "gem-seba" });
    const env = makeEnv(db);
    const r0 = await callRaw(env, "GET", "/api/bridge/credentials");
    expect(r0.status).toBe(401);
    const raw = await issueBrainToken(db as never, "sebastian");
    const session = await call(env, "POST", "/api/brain/session", { body: { token: raw } });
    const r = await call(env, "GET", "/api/bridge/credentials", { token: String(session.json.token) });
    expect(r.status).toBe(200);
    expect(r.json.tenant).toBe("sebastian");
    expect((r.json.gemini as Record<string, string> | null)?.api_key).toBe("gem-seba");
  });
});
