// Tests del bug kaez 2026-11-21 (Twitch sin dónde guardarse):
// - Guardar Twitch por onboarding con el estado exacto de kaez
//   (4 cuentas, sin fila twitch) crea la cuenta cifrada.
// - POST /api/accounts acepta twitch y gemini (el front los ofrece).
// - Un error D1 7500 (cuota agotada) al guardar NO sale como un 500
//   pelado sin CORS («Failed to fetch» en el navegador): sale como
//   JSON 503 con CORS y el motivo legible.
// - Paridad front/back: API_PROVIDERS (app.js) y el allowlist del
//   Worker (index.ts) listan los mismos 5 providers, y el selector
//   de index.html no lleva <option> hardcodeadas (se derivan de
//   API_PROVIDERS).
// Sin red real (fetch falso), sin D1 real (mock mínimo: solo el SQL
// que usan estos endpoints).
import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createSessionToken } from "../src/auth";
import type { Env } from "../src/cloudflare";
import worker from "../src/index";

const SECRET = Buffer.from("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb").toString("base64"); // SOLO TEST
const KEY = Buffer.from("0123456789abcdef0123456789abcdef").toString("base64");
const FRONT_ORIGIN = "https://avc-em9.pages.dev";

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

/** D1 con la cuota agotada: cualquier escritura tira el 7500 real. */
class QuotaExhaustedD1 extends FakeD1 {
  override runWrite(): never {
    throw new Error("D1_ERROR: exceeded D1's free tier daily row write limit [code: 7500]");
  }
}

function makeEnv(db: FakeD1, over: Partial<Env> = {}): Env {
  return {
    DB: db as unknown as Env["DB"],
    TENANT: "kaez",
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

async function callRaw(
  env: Env,
  method: string,
  path: string,
  opts: { token?: string; body?: Record<string, unknown>; origin?: string } = {},
): Promise<{ status: number; headers: Headers; text: string; json: Record<string, unknown> | null }> {
  const headers: Record<string, string> = {};
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.origin) headers.Origin = opts.origin;
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

/** Estado exacto de kaez en vivo 2026-11-21: 4 cuentas, sin twitch. */
function seedKaezLiveState(db: FakeD1): void {
  db.seed("settings", { id: 1, tenant_id: "kaez", data: JSON.stringify({ layout: "rdjavi" }), updated_at: 1 });
  const base = { tenant_id: "kaez", language: "es", credential: "", extra: "", extra_enc: "", status: "conectada", verify_ok: 1, verified_at: 1, verify_message: "ok", created_at: 1 };
  db.seed("api_accounts", { ...base, id: 8, platform: "youtube", label: "YouTube", credential_enc: "v1:aaa:bbb" });
  db.seed("api_accounts", { ...base, id: 9, platform: "tiktok", label: "TikTok", credential_enc: "v1:aaa:bbb" });
  db.seed("api_accounts", { ...base, id: 10, platform: "meta", label: "Meta", credential_enc: "v1:aaa:bbb", status: "error", verify_ok: 0 });
  db.seed("api_accounts", { ...base, id: 11, platform: "gemini", label: "Gemini", credential_enc: "v1:aaa:bbb" });
}

describe("bug kaez 2026-11-21: guardar Twitch", () => {
  test("onboarding/credentials crea la fila twitch cifrada con el estado de kaez", async () => {
    const db = new FakeD1();
    seedKaezLiveState(db);
    const env = makeEnv(db);
    const token = await createSessionToken("kaez", "kaez", SECRET);
    const r = await callRaw(env, "POST", "/api/onboarding/credentials", {
      token,
      body: { platform: "twitch", credential: "secret-twitch-kaez", extra: JSON.stringify({ client_id: "cid-kaez" }) },
    });
    expect(r.status).toBe(200);
    expect(r.json?.ok).toBe(true);
    const account = r.json?.account as { id: number; platform: string; label: string };
    expect(account.platform).toBe("twitch");
    const rows = db.tables.api_accounts ?? [];
    expect(rows.length).toBe(5);
    const tw = rows.find((a) => a.platform === "twitch");
    expect(tw).toBeDefined();
    expect(tw?.id).toBe(account.id);
    expect(String(tw?.credential_enc ?? "")).toMatch(/^v1:/);
    expect(String(tw?.credential_enc ?? "")).not.toContain("secret-twitch-kaez");
    expect(String(tw?.credential ?? "")).toBe("");
  });

  test("POST /api/accounts acepta twitch y gemini (201), y rechaza un provider inventado", async () => {
    const db = new FakeD1();
    // Sin gemini en el seed: aquí se prueba CREARLA (en el estado de
    // kaez ya existe y el 409 sería lo correcto).
    db.seed("settings", { id: 1, tenant_id: "kaez", data: JSON.stringify({ layout: "rdjavi" }), updated_at: 1 });
    const base = { tenant_id: "kaez", language: "es", credential: "", extra: "", extra_enc: "", status: "conectada", verify_ok: 1, verified_at: 1, verify_message: "ok", created_at: 1 };
    db.seed("api_accounts", { ...base, id: 8, platform: "youtube", label: "YouTube", credential_enc: "v1:aaa:bbb" });
    db.seed("api_accounts", { ...base, id: 9, platform: "tiktok", label: "TikTok", credential_enc: "v1:aaa:bbb" });
    db.seed("api_accounts", { ...base, id: 10, platform: "meta", label: "Meta", credential_enc: "v1:aaa:bbb", status: "error", verify_ok: 0 });
    const env = makeEnv(db);
    const token = await createSessionToken("kaez", "kaez", SECRET);
    for (const platform of ["twitch", "gemini"]) {
      const r = await callRaw(env, "POST", "/api/accounts", { token, body: { platform, language: "es", label: "" } });
      expect(r.status).toBe(201);
      expect((r.json?.account as { platform: string })?.platform).toBe(platform);
    }
    const bad = await callRaw(env, "POST", "/api/accounts", { token, body: { platform: "kick", language: "es" } });
    expect(bad.status).toBe(400);
    expect(String(bad.json?.error ?? "")).toContain("gemini");
  });
});

describe("bug kaez 2026-11-21: error D1 con CORS, no 'Failed to fetch'", () => {
  test("cuota 7500 al guardar: 503 JSON con CORS y motivo legible, sin throw", async () => {
    const db = new QuotaExhaustedD1();
    seedKaezLiveState(db);
    const env = makeEnv(db);
    const token = await createSessionToken("kaez", "kaez", SECRET);
    // worker.fetch NO debe lanzar: antes devolvía un 500 pelado sin
    // CORS y el navegador lo mostraba como «Failed to fetch».
    const r = await callRaw(env, "POST", "/api/onboarding/credentials", {
      token,
      origin: FRONT_ORIGIN,
      body: { platform: "twitch", credential: "secret-twitch-kaez", extra: JSON.stringify({ client_id: "cid-kaez" }) },
    });
    expect(r.status).toBe(503);
    expect(r.json?.ok).toBe(false);
    expect(String(r.json?.error ?? "")).toContain("Cuota diaria de escrituras D1 agotada");
    expect(String(r.json?.error ?? "")).toContain("21:00");
    expect(r.headers.get("Access-Control-Allow-Origin")).toBe(FRONT_ORIGIN);
  });

  test("otro error interno al guardar: 500 JSON con CORS, sin throw", async () => {
    const db = new FakeD1();
    seedKaezLiveState(db);
    // Romper el cifrado (clave inválida) para forzar un throw dentro
    // del handler sin tocar D1.
    const env = makeEnv(db, { CREDENTIAL_ENCRYPTION_KEY: "no-es-base64-32-bytes" });
    const token = await createSessionToken("kaez", "kaez", SECRET);
    const r = await callRaw(env, "POST", "/api/onboarding/credentials", {
      token,
      origin: FRONT_ORIGIN,
      body: { platform: "twitch", credential: "secret-twitch-kaez", extra: JSON.stringify({ client_id: "cid-kaez" }) },
    });
    expect(r.status).toBe(500);
    expect(r.json?.ok).toBe(false);
    expect(r.json?.error).toBeTruthy();
    expect(r.headers.get("Access-Control-Allow-Origin")).toBe(FRONT_ORIGIN);
  });
});

describe("CORS 2026-11-27: previews de Pages del mismo proyecto", () => {
  const PREVIEW_ORIGIN = "https://085dcb3b.avc-em9.pages.dev";
  const EVIL_ORIGIN = "https://evil.example";

  test("OPTIONS preflight desde un preview devuelve ACAO con ese origen", async () => {
    const db = new FakeD1();
    seedKaezLiveState(db);
    const env = makeEnv(db);
    const r = await callRaw(env, "OPTIONS", "/api/onboarding/credentials", { origin: PREVIEW_ORIGIN });
    expect(r.status).toBe(204);
    expect(r.headers.get("Access-Control-Allow-Origin")).toBe(PREVIEW_ORIGIN);
  });

  test("POST guardar Twitch desde un preview: 200 con ACAO del preview, sin 'Failed to fetch'", async () => {
    const db = new FakeD1();
    seedKaezLiveState(db);
    const env = makeEnv(db);
    const token = await createSessionToken("kaez", "kaez", SECRET);
    const r = await callRaw(env, "POST", "/api/onboarding/credentials", {
      token,
      origin: PREVIEW_ORIGIN,
      body: { platform: "twitch", credential: "secret-twitch-kaez", extra: JSON.stringify({ client_id: "cid-kaez" }) },
    });
    expect(r.status).toBe(200);
    expect(r.headers.get("Access-Control-Allow-Origin")).toBe(PREVIEW_ORIGIN);
  });

  test("origen ajeno: sin ACAO en el preflight", async () => {
    const db = new FakeD1();
    seedKaezLiveState(db);
    const env = makeEnv(db);
    const r = await callRaw(env, "OPTIONS", "/api/onboarding/credentials", { origin: EVIL_ORIGIN });
    expect(r.status).toBe(204);
    expect(r.headers.get("Access-Control-Allow-Origin")).toBeNull();
  });

  test("origen parecido pero no del proyecto (avc-em9.pages.dev.evil.example): sin ACAO", async () => {
    const db = new FakeD1();
    seedKaezLiveState(db);
    const env = makeEnv(db);
    const r = await callRaw(env, "OPTIONS", "/api/onboarding/credentials", { origin: "https://avc-em9.pages.dev.evil.example" });
    expect(r.headers.get("Access-Control-Allow-Origin")).toBeNull();
  });
});

describe("bug kaez 2026-11-21: paridad front/back de providers", () => {
  const appJs = readFileSync(join(import.meta.dir, "../../frontend/dist/app.js"), "utf8");
  const indexHtml = readFileSync(join(import.meta.dir, "../../frontend/dist/index.html"), "utf8");
  const workerSrc = readFileSync(join(import.meta.dir, "../src/index.ts"), "utf8");
  const EXPECTED = ["youtube", "tiktok", "meta", "twitch", "gemini"];

  test("API_PROVIDERS (app.js) lista los 5, Twitch incluido", () => {
    const m = /const API_PROVIDERS = \[([\s\S]*?)\];/.exec(appJs);
    expect(m).not.toBeNull();
    for (const p of EXPECTED) expect(m?.[1] ?? "").toContain(`id: "${p}"`);
  });

  test("el Worker acepta los mismos 5 en POST /api/accounts", () => {
    expect(workerSrc).toContain('["youtube", "tiktok", "meta", "twitch", "gemini"]');
  });

  test("index.html no hardcodea opciones del selector (las pone app.js)", () => {
    const m = /<select id="newApiPlatform">([\s\S]*?)<\/select>/.exec(indexHtml);
    expect(m).not.toBeNull();
    expect(m?.[1] ?? "").not.toContain("<option");
  });

  test("hints de credencial/extra existen para twitch y gemini", () => {
    for (const p of ["twitch", "gemini"]) {
      expect(appJs).toContain(`${p}: "`);
    }
  });
});
