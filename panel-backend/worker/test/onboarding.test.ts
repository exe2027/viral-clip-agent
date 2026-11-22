// Tests del asistente de onboarding (2026-11-18):
// copiar base (solo reglas, nunca secretos), OAuth Google/Meta
// (start + callback con intercambio de código), credenciales
// Twitch/Gemini por pegado y verify-all. Sin red real (fetch falso),
// sin D1 real (mock mínimo: solo el SQL que usan estos endpoints).
import { describe, expect, test, afterEach } from "bun:test";
import { createOAuthState, createSessionToken, verifyOAuthState } from "../src/auth";
import type { Env } from "../src/cloudflare";
import { extractBaseSettings } from "../src/db";
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

function makeEnv(db: FakeD1, over: Partial<Env> = {}): Env {
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
    ...over,
  };
}

const ctx = { waitUntil: () => {}, passThroughOnException: () => {} };

async function tokenFor(username: string, tenant: "sebastian" | "kaez"): Promise<string> {
  return createSessionToken(username, tenant, SECRET);
}

async function callRaw(
  env: Env,
  method: string,
  path: string,
  opts: { token?: string; body?: Record<string, unknown> } = {},
): Promise<{ status: number; headers: Headers; text: string; json: Record<string, unknown> | null }> {
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
  return { status: res.status, headers: res.headers, text, json };
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

function stubFetchByUrl(handlers: Array<{ match: (url: string) => boolean; status: number; body: unknown }>) {
  globalThis.fetch = (async (input: string | URL | Request) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const h = handlers.find((x) => x.match(url));
    if (!h) return new Response(JSON.stringify({ error: `sin stub para ${url}` }), { status: 500, headers: { "Content-Type": "application/json" } });
    return new Response(JSON.stringify(h.body), { status: h.status, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
}

describe("copia de la configuración base", () => {
  test("extractBaseSettings: solo reglas, nunca secretos", () => {
    const out = extractBaseSettings({
      layout: "rdjavi", min_virality: 50, youtube_plan: 3,
      telegram_bot_token: "SECRETO", telegram_chat_id: "123", clave_rara: 1,
    });
    expect(out).toEqual({ layout: "rdjavi", min_virality: 50, youtube_plan: 3 });
    expect(JSON.stringify(out)).not.toContain("SECRETO");
  });

  test("copy-settings copia los DEFAULTS comunes al tenant nuevo (nunca las reglas de otro tenant)", async () => {
    const db = new FakeD1();
    db.seed("settings", {
      id: 1, tenant_id: "sebastian",
      data: JSON.stringify({ layout: "rdjavi", min_virality: 55, youtube_plan: 3, telegram_bot_token: "SECRETO-SEBA", otra: 1 }),
      updated_at: 1,
    });
    const env = makeEnv(db, { TENANT: "kaez" });
    const r = await call(env, "POST", "/api/onboarding/copy-settings", { token: await tokenFor("kaez", "kaez"), body: {} });
    expect(r.status).toBe(200);
    expect(r.json.copied).toBe(true);
    const kaez = db.tables.settings?.find((s) => s.tenant_id === "kaez");
    expect(kaez).toBeDefined();
    const data = JSON.parse(String(kaez?.data)) as Record<string, unknown>;
    // Defaults comunes, NO los de sebastian (55): 2026-11-19,
    // auditoría multi-tenant (hallazgo ALTO 5). Puerta única 45/80
    // (P0 #3, 2026-11-21).
    expect(data.min_virality).toBe(45);
    expect(data.min_seo).toBe(80);
    expect(data.layout).toBe("rdjavi");
    expect(data.telegram_bot_token).toBeUndefined();
    expect(data.otra).toBeUndefined();
    // Sebastian intacto, con su secreto y sus 55.
    const seba = db.tables.settings?.find((s) => s.tenant_id === "sebastian");
    const sebaData = JSON.parse(String(seba?.data)) as Record<string, unknown>;
    expect(sebaData.telegram_bot_token).toBe("SECRETO-SEBA");
    expect(sebaData.min_virality).toBe(55);
    // Segunda vez: ya copiada.
    const r2 = await call(env, "POST", "/api/onboarding/copy-settings", { token: await tokenFor("kaez", "kaez"), body: {} });
    expect(r2.json.already).toBe(true);
  });

  test("status: kaez nuevo no está completo; sebastian con settings sí tiene base", async () => {
    const db = new FakeD1();
    db.seed("settings", { id: 1, tenant_id: "sebastian", data: JSON.stringify({ layout: "rdjavi" }), updated_at: 1 });
    const env = makeEnv(db, { TENANT: "kaez" });
    const kaez = await call(env, "GET", "/api/onboarding/status", { token: await tokenFor("kaez", "kaez") });
    expect(kaez.json.onboarding.settings_copied).toBe(false);
    expect(kaez.json.onboarding.complete).toBe(false);
    expect(kaez.json.onboarding.worker_running).toBe(false);
    const oauth = kaez.json.onboarding.oauth as Record<string, { configured: boolean; mode: string }>;
    expect(oauth.google?.configured).toBe(false);
    expect(oauth.google?.mode).toBe("oauth");
    expect(oauth.zernio?.mode).toBe("dashboard");
  });
});

describe("state OAuth", () => {
  test("roundtrip y rechazo de state manipulado", async () => {
    const state = await createOAuthState("google", "kaez", SECRET);
    const back = await verifyOAuthState(state, SECRET, "kaez");
    expect(back?.provider).toBe("google");
    expect(back?.tenant).toBe("kaez");
    // El mismo state en el Worker de sebastian no vale.
    expect(await verifyOAuthState(state, SECRET, "sebastian")).toBeNull();
    const tampered = state.slice(0, -2) + "xx";
    expect(await verifyOAuthState(tampered, SECRET, "kaez")).toBeNull();
    expect(await verifyOAuthState(null, SECRET, "kaez")).toBeNull();
  });

  test("roundtrip con accountId: el state lleva la cuenta elegida", async () => {
    const state = await createOAuthState("google", "kaez", SECRET, 3);
    const back = await verifyOAuthState(state, SECRET, "kaez");
    expect(back?.accountId).toBe(3);
    const plain = await createOAuthState("google", "kaez", SECRET);
    expect((await verifyOAuthState(plain, SECRET, "kaez"))?.accountId).toBeUndefined();
    // Un state con accountId manipulado no verifica.
    const tampered = state.slice(0, -2) + "xx";
    expect(await verifyOAuthState(tampered, SECRET, "kaez")).toBeNull();
  });
});

describe("OAuth Google", () => {
  test("start sin cliente configurado: 400 con el motivo exacto", async () => {
    const env = makeEnv(new FakeD1(), { TENANT: "kaez" });
    const r = await call(env, "GET", "/api/oauth/google/start", { token: await tokenFor("kaez", "kaez") });
    expect(r.status).toBe(400);
    expect(String(r.json.error)).toContain("cliente OAuth de Google");
    expect(String(r.json.error)).toContain("avc.vikingosfoodspa.workers.dev/api/oauth/google/callback");
  });

  test("start con cliente: 302 a accounts.google.com con redirect y state", async () => {
    const env = makeEnv(new FakeD1(), { TENANT: "kaez", GOOGLE_CLIENT_ID: "id-test", GOOGLE_CLIENT_SECRET: "sec.test" });
    const r = await callRaw(env, "GET", "/api/oauth/google/start", { token: await tokenFor("kaez", "kaez") });
    expect(r.status).toBe(302);
    const loc = r.headers.get("Location") ?? "";
    expect(loc).toContain("accounts.google.com/o/oauth2/v2/auth");
    expect(loc).toContain("redirect_uri=https%3A%2F%2Favc.vikingosfoodspa.workers.dev%2Fapi%2Foauth%2Fgoogle%2Fcallback");
    expect(loc).toContain("youtube.upload");
    expect(loc).toContain("state=");
  });

  test("callback con state válido: intercambia el código, guarda y verifica YouTube", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { TENANT: "kaez", GOOGLE_CLIENT_ID: "id-test", GOOGLE_CLIENT_SECRET: "sec.test" });
    stubFetchByUrl([
      { match: (u) => u.includes("tokeninfo"), status: 200, body: { scope: "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly" } },
      { match: (u) => u.includes("oauth2.googleapis.com/token"), status: 200, body: { access_token: "ya29.nuevo", refresh_token: "1//refresh-test", expires_in: 3600 } },
    ]);
    const state = await createOAuthState("google", "kaez", SECRET);
    const r = await callRaw(env, "GET", `/api/oauth/google/callback?code=codigo-test&state=${encodeURIComponent(state)}`);
    expect(r.status).toBe(302);
    const loc = r.headers.get("Location") ?? "";
    expect(loc).toContain("avc-em9.pages.dev");
    expect(loc).toContain("oauth=google&ok=1");
    const accs = db.tables.api_accounts ?? [];
    expect(accs.length).toBe(1);
    expect(accs[0]?.platform).toBe("youtube");
    expect(accs[0]?.tenant_id).toBe("kaez");
    expect(accs[0]?.verify_ok).toBe(1);
    expect(accs[0]?.status).toBe("conectada");
    expect(String(accs[0]?.credential_enc ?? "")).not.toContain("refresh-test");
    expect(String(accs[0]?.credential ?? "")).toBe("");
  });

  test("start con ?account=3: el state lleva la cuenta; con cuenta inexistente: 400", async () => {
    const db = new FakeD1();
    db.seed("api_accounts", { id: 3, tenant_id: "kaez", platform: "youtube", language: "en", label: "YouTube", credential: "", credential_enc: "", extra: "", extra_enc: "", status: "conectada" });
    const env = makeEnv(db, { TENANT: "kaez", GOOGLE_CLIENT_ID: "id-test", GOOGLE_CLIENT_SECRET: "sec.test" });
    const r = await callRaw(env, "GET", "/api/oauth/google/start?account=3", { token: await tokenFor("kaez", "kaez") });
    expect(r.status).toBe(302);
    const loc = r.headers.get("Location") ?? "";
    const stateParam = new URL(loc).searchParams.get("state") ?? "";
    expect((await verifyOAuthState(stateParam, SECRET, "kaez"))?.accountId).toBe(3);
    const bad = await call(env, "GET", "/api/oauth/google/start?account=999", { token: await tokenFor("kaez", "kaez") });
    expect(bad.status).toBe(400);
    expect(String(bad.json.error)).toContain("Cuenta OAuth");
  });

  test("callback con state manipulado: 302 ok=0, no crea nada", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { TENANT: "kaez", GOOGLE_CLIENT_ID: "id-test", GOOGLE_CLIENT_SECRET: "sec.test" });
    const state = await createOAuthState("google", "kaez", SECRET);
    const bad = state.slice(0, -2) + "xx";
    const r = await callRaw(env, "GET", `/api/oauth/google/callback?code=x&state=${encodeURIComponent(bad)}`);
    expect(r.status).toBe(302);
    expect(r.headers.get("Location") ?? "").toContain("ok=0");
    expect(db.tables.api_accounts ?? []).toEqual([]);
  });

  test("callback con state de la cuenta EN: guarda el token en la cuenta EN, no en la ES", async () => {
    // Regresión 2026-11-19: el callback guardaba SIEMPRE en la cuenta
    // ES (upsert sin idioma, default es), así que re-autorizar EN
    // sobrescribía la ES y la EN quedaba con el token viejo (403 sin
    // youtube.readonly). El state lleva la cuenta pulsada.
    const db = new FakeD1();
    db.seed("api_accounts", { id: 2, tenant_id: "kaez", platform: "youtube", language: "es", label: "YouTube", credential: "", credential_enc: "viejo-es-cifrado", extra: "", extra_enc: "", status: "conectada" });
    db.seed("api_accounts", { id: 3, tenant_id: "kaez", platform: "youtube", language: "en", label: "YouTube", credential: "", credential_enc: "viejo-en-cifrado", extra: "", extra_enc: "", status: "conectada" });
    const env = makeEnv(db, { TENANT: "kaez", GOOGLE_CLIENT_ID: "id-test", GOOGLE_CLIENT_SECRET: "sec.test" });
    stubFetchByUrl([
      { match: (u) => u.includes("tokeninfo"), status: 200, body: { scope: "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly" } },
      { match: (u) => u.includes("oauth2.googleapis.com/token"), status: 200, body: { access_token: "ya29.nuevo-en", refresh_token: "1//refresh-en", expires_in: 3600 } },
    ]);
    const state = await createOAuthState("google", "kaez", SECRET, 3);
    const r = await callRaw(env, "GET", `/api/oauth/google/callback?code=codigo-en&state=${encodeURIComponent(state)}`);
    expect(r.status).toBe(302);
    expect(r.headers.get("Location") ?? "").toContain("oauth=google&ok=1");
    const accs = db.tables.api_accounts ?? [];
    expect(accs.length).toBe(2);
    const es = accs.find((a) => a.id === 2);
    const en = accs.find((a) => a.id === 3);
    expect(es?.credential_enc).toBe("viejo-es-cifrado");
    expect(en?.credential_enc).not.toBe("viejo-en-cifrado");
    expect(String(en?.credential_enc ?? "")).not.toContain("refresh-en");
    expect(en?.verify_ok).toBe(1);
    expect(en?.status).toBe("conectada");
  });

  test("callback sin accountId (legado): guarda en la cuenta ES", async () => {
    const db = new FakeD1();
    db.seed("api_accounts", { id: 2, tenant_id: "kaez", platform: "youtube", language: "es", label: "YouTube", credential: "", credential_enc: "viejo-es-cifrado", extra: "", extra_enc: "", status: "conectada" });
    const env = makeEnv(db, { TENANT: "kaez", GOOGLE_CLIENT_ID: "id-test", GOOGLE_CLIENT_SECRET: "sec.test" });
    stubFetchByUrl([
      { match: (u) => u.includes("tokeninfo"), status: 200, body: { scope: "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly" } },
      { match: (u) => u.includes("oauth2.googleapis.com/token"), status: 200, body: { access_token: "ya29.nuevo-es", refresh_token: "1//refresh-es", expires_in: 3600 } },
    ]);
    const state = await createOAuthState("google", "kaez", SECRET);
    const r = await callRaw(env, "GET", `/api/oauth/google/callback?code=codigo-es&state=${encodeURIComponent(state)}`);
    expect(r.status).toBe(302);
    expect(r.headers.get("Location") ?? "").toContain("oauth=google&ok=1");
    const accs = db.tables.api_accounts ?? [];
    expect(accs.length).toBe(1);
    expect(accs[0]?.credential_enc).not.toBe("viejo-es-cifrado");
    expect(accs[0]?.verify_ok).toBe(1);
  });
});

describe("OAuth Meta", () => {
  test("start sin app configurada: 400 con el motivo exacto", async () => {
    const env = makeEnv(new FakeD1(), { TENANT: "kaez" });
    const r = await call(env, "GET", "/api/oauth/meta/start", { token: await tokenFor("kaez", "kaez") });
    expect(r.status).toBe(400);
    expect(String(r.json.error)).toContain("Meta");
    expect(String(r.json.error)).toContain("api/oauth/meta/callback");
  });

  test("callback con state válido: token largo, Página, IG y verificación", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { TENANT: "kaez", META_APP_ID: "app-test", META_APP_SECRET: "appsec-test" });
    stubFetchByUrl([
      { match: (u) => u.includes("/oauth/access_token") && u.includes("fb_exchange_token"), status: 200, body: { access_token: "token.largo" } },
      { match: (u) => u.includes("/oauth/access_token"), status: 200, body: { access_token: "token.corto" } },
      { match: (u) => u.includes("/me/accounts"), status: 200, body: { data: [{ id: "page-1", access_token: "token.pagina", name: "Elclipdegta6" }] } },
      { match: (u) => u.includes("instagram_business_account"), status: 200, body: { instagram_business_account: { id: "ig-1" } } },
      { match: (u) => u.includes("/ig-1?fields=username"), status: 200, body: { username: "elclipdegta6" } },
      { match: (u) => u.includes("graph.facebook.com/v26.0/me"), status: 200, body: { id: "page-1", name: "Elclipdegta6" } },
    ]);
    const state = await createOAuthState("meta", "kaez", SECRET);
    const r = await callRaw(env, "GET", `/api/oauth/meta/callback?code=codigo-test&state=${encodeURIComponent(state)}`);
    expect(r.status).toBe(302);
    expect(r.headers.get("Location") ?? "").toContain("oauth=meta&ok=1");
    const accs = db.tables.api_accounts ?? [];
    expect(accs.length).toBe(1);
    expect(accs[0]?.platform).toBe("meta");
    expect(accs[0]?.tenant_id).toBe("kaez");
    expect(accs[0]?.verify_ok).toBe(1);
  });
});

describe("Twitch y Gemini por pegado + verify-all", () => {
  test("credentials crea la cuenta cifrada; verify-all la verifica de verdad", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { TENANT: "kaez" });
    const r = await call(env, "POST", "/api/onboarding/credentials", {
      token: await tokenFor("kaez", "kaez"),
      body: { platform: "twitch", credential: "secret-twitch", extra: JSON.stringify({ client_id: "cid-test" }) },
    });
    expect(r.status).toBe(200);
    expect(r.json.account.platform).toBe("twitch");
    const acc = (db.tables.api_accounts ?? [])[0];
    expect(String(acc?.credential_enc ?? "")).not.toContain("secret-twitch");
    stubFetchByUrl([
      { match: (u) => u.includes("id.twitch.tv/oauth2/token"), status: 200, body: { access_token: "tw-app.tok" } },
      { match: (u) => u.includes("id.twitch.tv/oauth2/validate"), status: 200, body: { client_id: "cid-test" } },
    ]);
    const v = await call(env, "POST", "/api/onboarding/verify-all", { token: await tokenFor("kaez", "kaez"), body: {} });
    expect(v.status).toBe(200);
    const results = v.json.results as Array<{ platform: string; ok: boolean }>;
    expect(results.find((x) => x.platform === "twitch")?.ok).toBe(true);
    expect(v.json.all_ok).toBe(true);
  });

  test("Gemini con key mala: verify-all dice el motivo, no un falso Conectada", async () => {
    const db = new FakeD1();
    const env = makeEnv(db, { TENANT: "kaez" });
    await call(env, "POST", "/api/onboarding/credentials", {
      token: await tokenFor("kaez", "kaez"),
      body: { platform: "gemini", credential: "key-mala" },
    });
    stubFetchByUrl([
      { match: (u) => u.includes("generativelanguage.googleapis.com"), status: 403, body: { error: { message: "API key not valid" } } },
    ]);
    const v = await call(env, "POST", "/api/onboarding/verify-all", { token: await tokenFor("kaez", "kaez"), body: {} });
    const results = v.json.results as Array<{ platform: string; ok: boolean; message: string }>;
    expect(results.find((x) => x.platform === "gemini")?.ok).toBe(false);
    expect(results.find((x) => x.platform === "gemini")?.message).toContain("Gemini");
    expect(v.json.all_ok).toBe(false);
    expect((db.tables.api_accounts ?? [])[0]?.verify_ok).toBe(0);
  });

  test("verify-all sin cuentas: all_ok false, sin inventar nada", async () => {
    const env = makeEnv(new FakeD1(), { TENANT: "kaez" });
    const v = await call(env, "POST", "/api/onboarding/verify-all", { token: await tokenFor("kaez", "kaez"), body: {} });
    expect(v.json.results).toEqual([]);
    expect(v.json.all_ok).toBe(false);
  });
});
