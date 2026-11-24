// Tests de la validación diaria de cuentas (spec 2026-11-23,
// SPEC-2026-11-23-validacion-diaria-cuentas.md, §4).
// Contra SQLite REAL (bun:sqlite, mismo motor que D1) + fetch con
// cola (sin red real, sin D1 real, sin secretos reales). Las rutas
// HTTP se prueban con el handler de verdad (worker.fetch) + sesión
// Bearer <redacted> Las cuentas se siembran con credenciales cifradas de
// verdad (encryptCredential con la clave del test).
import { afterEach, describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { createSessionToken } from "../src/auth";
import { encryptCredential } from "../src/crypto";
import type { D1Database, Env } from "../src/cloudflare";
import worker from "../src/index";
import { ACCOUNT_VALIDATION_DAY_KEY, isAccountValidationDue } from "../src/cycle-gate";
import { getSettings, isAccountVerificationCurrent } from "../src/db";
import { maybeValidateAccountsDaily, validateAccountsDaily } from "../src/daily-validation";
import { SqliteStmt } from "./budget-test-db";

const SECRET = Buffer.from("eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee").toString("base64"); // SOLO TEST
const KEY = Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"); // SOLO TEST
const KEY_BAD = Buffer.from("fedcba9876543210fedcba9876543210").toString("base64"); // SOLO TEST
const NOW = Date.UTC(2026, 10, 23, 18, 0, 0); // 23-nov 15:00 Santiago
const HOUR_BEFORE = Date.UTC(2026, 10, 23, 17, 0, 0); // 23-nov 14:00 Santiago
const NEXT_DAY = Date.UTC(2026, 10, 24, 18, 0, 0); // 24-nov 15:00 Santiago
const YT_CRED = JSON.stringify({
  token: "ya29.viejo",
  refresh_token: "1//refresh",
  client_id: "cid.apps.googleusercontent.com",
  client_secret: "csecret",
});
const YT_SCOPE = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.force-ssl";

function makeDb(): { d1: D1Database; raw: Database } {
  const raw = new Database(":memory:");
  raw.exec("CREATE TABLE settings (tenant_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at INTEGER NOT NULL)");
  raw.exec(
    "CREATE TABLE api_accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, platform TEXT NOT NULL, " +
      "language TEXT NOT NULL DEFAULT 'es', label TEXT NOT NULL DEFAULT '', credential TEXT NOT NULL DEFAULT '', " +
      "credential_enc TEXT NOT NULL DEFAULT '', extra TEXT NOT NULL DEFAULT '', extra_enc TEXT NOT NULL DEFAULT '', " +
      "status TEXT NOT NULL DEFAULT 'pendiente', note TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, " +
      "verified_at INTEGER, verify_ok INTEGER, verify_message TEXT NOT NULL DEFAULT '', credential_changed_at INTEGER, " +
      "first_published_at INTEGER, published_count INTEGER NOT NULL DEFAULT 0, strikes INTEGER NOT NULL DEFAULT 0, " +
      "maturity TEXT NOT NULL DEFAULT 'nueva', maturity_checked_day TEXT)",
  );
  raw.exec(
    "CREATE TABLE accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, platform TEXT NOT NULL, " +
      "label TEXT NOT NULL DEFAULT '', handle TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '', " +
      "note TEXT NOT NULL DEFAULT '', sort INTEGER NOT NULL DEFAULT 0, language TEXT NOT NULL DEFAULT 'es')",
  );
  raw.exec(
    "CREATE TABLE worker_state (tenant_id TEXT PRIMARY KEY, running INTEGER NOT NULL DEFAULT 0, " +
      "started_at INTEGER, last_note TEXT NOT NULL DEFAULT '', updated_at INTEGER NOT NULL, " +
      "last_live_dispatch_at INTEGER, last_cycle_review_at INTEGER)",
  );
  raw.exec("CREATE TABLE worker_events (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, at INTEGER NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL)");
  raw.exec(
    "CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, kind TEXT NOT NULL, " +
      "clip_id INTEGER, message TEXT NOT NULL, created_at INTEGER NOT NULL, read_at INTEGER)",
  );
  raw.exec(
    "CREATE TABLE event_counters (tenant_id TEXT NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, " +
      "window_start INTEGER NOT NULL, count INTEGER NOT NULL DEFAULT 1, first_at INTEGER NOT NULL, last_at INTEGER NOT NULL, " +
      "PRIMARY KEY (tenant_id, source, kind, message, window_start))",
  );
  const d1 = {
    prepare: (sql: string) => new SqliteStmt(raw, sql),
    batch: async (stmts: SqliteStmt[]) => {
      const out = [];
      for (const s of stmts) out.push(await s.run());
      return out;
    },
  } as unknown as D1Database;
  return { d1, raw };
}

function makeEnv(d1: D1Database): Env {
  return {
    DB: d1,
    TENANT: "sebastian",
    CLIPS_BUCKET: {} as Env["CLIPS_BUCKET"],
    SESSION_SECRET: <redacted> SECRET,
    CREDENTIAL_ENCRYPTION_KEY: KEY,
    BRIDGE_TOKEN: <redacted> "bridge-test",
    GITHUB_TOKEN: <redacted> "gh-test",
    ALLOWED_EMAIL: "",
    GITHUB_REPO: "exe2027/viral-clip-agent",
  } as Env;
}

function seedWorkerState(raw: Database, running = 1): void {
  raw.query("INSERT INTO worker_state (tenant_id, running, started_at, last_note, updated_at) VALUES ('sebastian', ?, ?, '', ?)").run(running, NOW, NOW);
}

async function seedAccount(
  raw: Database,
  over: {
    platform: string;
    language?: string;
    label?: string;
    credential?: string;
    extra?: string;
    key?: string;
    verify_ok?: number | null;
    verified_at?: number | null;
    verify_message?: string;
    credential_changed_at?: number | null;
    status?: string;
  },
): Promise<number> {
  const enc = over.credential ? await encryptCredential(over.credential, over.key ?? KEY, "sebastian") : "";
  const extraEnc = over.extra ? await encryptCredential(over.extra, over.key ?? KEY, "sebastian") : "";
  const info = raw
    .query(
      "INSERT INTO api_accounts (tenant_id, platform, language, label, credential, credential_enc, extra, extra_enc, status, created_at, verified_at, verify_ok, verify_message, credential_changed_at) " +
        "VALUES ('sebastian', ?, ?, ?, '', ?, '', ?, ?, ?, ?, ?, ?, ?)",
    )
    .run(
      over.platform,
      over.language ?? "es",
      over.label ?? over.platform,
      enc,
      extraEnc,
      over.status ?? "pendiente",
      NOW,
      over.verified_at ?? null,
      over.verify_ok ?? null,
      over.verify_message ?? "",
      over.credential_changed_at ?? null,
    );
  return Number(info.lastInsertRowid);
}

function seedSocial(raw: Database, platform: string, language = "es", label = ""): number {
  const info = raw
    .query("INSERT INTO accounts (tenant_id, platform, label, handle, status, note, sort, language) VALUES ('sebastian', ?, ?, ?, 'conectada', '', 0, ?)")
    .run(platform, label || platform, `@${platform}`, language);
  return Number(info.lastInsertRowid);
}

function readAccount(raw: Database, id: number): Record<string, unknown> {
  return raw.query("SELECT * FROM api_accounts WHERE id = ?").get(id) as Record<string, unknown>;
}

function readSettings(raw: Database): Record<string, unknown> {
  const row = raw.query("SELECT data FROM settings WHERE tenant_id = 'sebastian'").get() as { data: string } | undefined;
  return row ? (JSON.parse(row.data) as Record<string, unknown>) : {};
}

function countNotifications(raw: Database, kind = "credential"): number {
  return (raw.query("SELECT COUNT(*) AS n FROM notifications WHERE tenant_id = 'sebastian' AND kind = ?").get(kind) as { n: number }).n;
}

function listNotifications(raw: Database): Array<{ kind: string; message: string }> {
  return raw.query("SELECT kind, message FROM notifications WHERE tenant_id = 'sebastian' ORDER BY id").all() as Array<{ kind: string; message: string }>;
}

function countWorkerEvents(raw: Database, kind: string, like = "%"): number {
  return (raw.query("SELECT COUNT(*) AS n FROM worker_events WHERE tenant_id = 'sebastian' AND kind = ? AND message LIKE ?").get(kind, like) as { n: number }).n;
}

// --- Fetch con cola (sin red real) ---
type SeenRequest = { url: string; method: string };
let seen: SeenRequest[] = [];
let queue: Array<{ status: number; body: unknown }> = [];
const realFetch = globalThis.fetch;

function stubFetch(responses: Array<{ status: number; body: unknown }>): void {
  seen = [];
  queue = [...responses];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    seen.push({ url, method: init?.method ?? "GET" });
    const next = queue.shift() ?? { status: 500, body: { error: "sin respuesta en la cola del test" } };
    return new Response(JSON.stringify(next.body), { status: next.status, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
}

afterEach(() => {
  globalThis.fetch = realFetch;
});

// Respuestas reutilizables de la cola (en orden de llamada).
const ytTokenOk = { status: 200, body: { access_token: "ya29.fresco" } };
const ytTokeninfoOk = { status: 200, body: { scope: YT_SCOPE } };
const ytTokenBad = { status: 400, body: { error: "invalid_grant" } };
const ytToken500 = { status: 500, body: { error: "internal" } };
const zernioHealthy = { status: 200, body: { status: "healthy", issues: [] } };
const geminiOk = { status: 200, body: { models: [] } };
const gemini500 = { status: 500, body: { error: "internal" } };

async function callApi(env: Env, method: string, path: string, opts: { auth?: boolean; body?: Record<string, unknown> } = {}): Promise<{ status: number; json: Record<string, unknown> }> {
  const headers: Record<string, string> = {};
  if (opts.auth !== false) {
    const token = await createSessionToken("sebastian", "sebastian", SECRET);
    headers.Authorization = `Bearer ${token}`;
  }
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await worker.fetch(
    new Request(`https://worker.test${path}`, {
      method,
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    }),
    env,
    { waitUntil: () => {}, passThroughOnException: () => {} } as never,
  );
  return { status: res.status, json: (await res.json()) as Record<string, unknown> };
}

describe("puerta isAccountValidationDue", () => {
  test("antes de las 15:00 Chile no toca", async () => {
    const { d1 } = makeDb();
    expect(await isAccountValidationDue(d1, "sebastian", HOUR_BEFORE)).toBe(false);
  });

  test("a las 15:00 sin marca del día toca", async () => {
    const { d1 } = makeDb();
    expect(await isAccountValidationDue(d1, "sebastian", NOW)).toBe(true);
  });

  test("no revalida dos veces el mismo día", async () => {
    const { d1, raw } = makeDb();
    raw.query("INSERT INTO settings (tenant_id, data, updated_at) VALUES ('sebastian', ?, ?)").run(JSON.stringify({ [ACCOUNT_VALIDATION_DAY_KEY]: { day: "2026-11-23", n_ok: 1, n_invalid: 0, checked_at: NOW } }), NOW);
    expect(await isAccountValidationDue(d1, "sebastian", NOW)).toBe(false);
  });

  test("al día siguiente vuelve a tocar", async () => {
    const { d1, raw } = makeDb();
    raw.query("INSERT INTO settings (tenant_id, data, updated_at) VALUES ('sebastian', ?, ?)").run(JSON.stringify({ [ACCOUNT_VALIDATION_DAY_KEY]: { day: "2026-11-23", n_ok: 1, n_invalid: 0, checked_at: NOW } }), NOW);
    expect(await isAccountValidationDue(d1, "sebastian", NEXT_DAY)).toBe(true);
  });

  test("si no se puede leer settings, fail-open (valida)", async () => {
    const broken = { prepare: () => { throw new Error("D1 caído"); } } as unknown as D1Database;
    expect(await isAccountValidationDue(broken, "sebastian", NOW)).toBe(true);
  });
});

describe("isAccountVerificationCurrent (el campo que manda el display)", () => {
  test("OK viejo con credencial cambiada después NO es vigente", () => {
    expect(isAccountVerificationCurrent({ verify_ok: 1, verified_at: NOW - 86400_000, credential_changed_at: NOW - 3600_000 })).toBe(false);
  });
  test("OK posterior al cambio de credencial SÍ es vigente", () => {
    expect(isAccountVerificationCurrent({ verify_ok: 1, verified_at: NOW, credential_changed_at: NOW - 3600_000 })).toBe(true);
  });
  test("nunca verificada (verified_at null) NO es vigente", () => {
    expect(isAccountVerificationCurrent({ verify_ok: 1, verified_at: null })).toBe(false);
  });
  test("sin cambio registrado (credential_changed_at null) se respeta el OK", () => {
    expect(isAccountVerificationCurrent({ verify_ok: 1, verified_at: NOW - 86400_000, credential_changed_at: null })).toBe(true);
  });
});

describe("corrida diaria validateAccountsDaily", () => {
  test("flip válida→inválida dispara EXACTAMENTE 1 alerta con el texto exacto", async () => {
    const { d1, raw } = makeDb();
    const env = makeEnv(d1);
    seedWorkerState(raw, 1);
    await seedAccount(raw, { platform: "youtube", label: "YouTube ES", credential: YT_CRED, verify_ok: 1, verified_at: NOW - 3600_000, verify_message: "YouTube aceptó el token OAuth (scope youtube.upload, renovado con refresh_token).", status: "conectada" });
    // Verificación: token 400 (auth) + sonda: refresh 400 (sin access → sonda_fallida, sin tokeninfo).
    stubFetch([ytTokenBad, ytTokenBad]);
    const r = await validateAccountsDaily(env, "sebastian", NOW);
    expect(r.ran).toBe(true);
    expect(r.reason).toBe("done");
    expect(r.summary).toEqual({ day: "2026-11-23", n_ok: 0, n_invalid: 1, checked_at: NOW });
    const acc = readAccount(raw, 1);
    expect(acc.verify_ok).toBe(0);
    expect(countNotifications(raw)).toBe(1);
    const [n] = listNotifications(raw);
    expect(n.message).toMatch(/^Cuenta inválida: youtube es \(YouTube ES\) — YouTube no renovó el token con ese refresh_token: reautoriza OAuth y pega el JSON nuevo\. — 2026-11-23 15:00$/);
  });

  test("ya-inválida no re-alerta al día siguiente (y el día queda marcado)", async () => {
    const { d1, raw } = makeDb();
    const env = makeEnv(d1);
    seedWorkerState(raw, 1);
    await seedAccount(raw, { platform: "youtube", label: "YouTube ES", credential: YT_CRED, verify_ok: 0, verified_at: NOW - 86400_000, verify_message: "YouTube no renovó el token.", status: "error" });
    stubFetch([ytTokenBad, ytTokenBad]);
    const r = await validateAccountsDaily(env, "sebastian", NEXT_DAY);
    expect(r.ran).toBe(true);
    expect(r.summary?.n_invalid).toBe(1);
    expect(countNotifications(raw)).toBe(0);
    expect((readSettings(raw)[ACCOUNT_VALIDATION_DAY_KEY] as { day: string }).day).toBe("2026-11-24");
  });

  test("valida tras cambio de credencial: OK anterior caducado, verificar OK no alerta (recuperación)", async () => {
    const { d1, raw } = makeDb();
    const env = makeEnv(d1);
    seedWorkerState(raw, 1);
    await seedAccount(raw, {
      platform: "youtube", label: "YouTube ES", credential: YT_CRED,
      verify_ok: 1, verified_at: NOW - 86400_000, credential_changed_at: NOW - 3600_000,
      verify_message: "YouTube aceptó el token OAuth (scope youtube.upload, renovado con refresh_token).", status: "conectada",
    });
    // Verificación OK (token + tokeninfo) + sonda OK (token + tokeninfo).
    stubFetch([ytTokenOk, ytTokeninfoOk, ytTokenOk, ytTokeninfoOk]);
    const r = await validateAccountsDaily(env, "sebastian", NOW);
    expect(r.ran).toBe(true);
    expect(r.summary).toEqual({ day: "2026-11-23", n_ok: 1, n_invalid: 0, checked_at: NOW });
    expect(countNotifications(raw)).toBe(0);
    expect(readAccount(raw, 1).verify_ok).toBe(1);
  });

  test("fallo transitorio (red/5xx) no escribe inválido, no marca el día y reintenta en la siguiente pasada", async () => {
    const { d1, raw } = makeDb();
    const env = makeEnv(d1);
    seedWorkerState(raw, 1);
    await seedAccount(raw, { platform: "youtube", label: "YouTube ES", credential: YT_CRED, verify_ok: 1, verified_at: NOW - 3600_000, verify_message: "YouTube aceptó el token OAuth (scope youtube.upload, renovado con refresh_token).", status: "conectada" });
    // Pasada 1: token 500 (transient) + sonda OK (token + tokeninfo).
    stubFetch([ytToken500, ytTokenOk, ytTokeninfoOk]);
    const r1 = await validateAccountsDaily(env, "sebastian", NOW);
    expect(r1.ran).toBe(true);
    expect(r1.reason).toBe("partial");
    expect(r1.partial).toBe(true);
    expect(r1.transient).toBe(1);
    expect(r1.summary).toBeNull();
    const acc1 = readAccount(raw, 1);
    expect(acc1.verify_ok).toBe(1);
    expect(acc1.verified_at).toBe(NOW - 3600_000);
    expect(readSettings(raw)[ACCOUNT_VALIDATION_DAY_KEY]).toBeUndefined();
    expect(countNotifications(raw)).toBe(0);
    // Pasada 2 (misma hora, la puerta sigue debida): verificación OK + sonda OK → día marcado.
    stubFetch([ytTokenOk, ytTokeninfoOk, ytTokenOk, ytTokeninfoOk]);
    const r2 = await validateAccountsDaily(env, "sebastian", NOW + 5 * 60_000);
    expect(r2.reason).toBe("done");
    expect(r2.summary).toEqual({ day: "2026-11-23", n_ok: 1, n_invalid: 0, checked_at: NOW + 5 * 60_000 });
  });

  test("corrida parcial (una cuenta transient) no escribe resumen ni marca el día", async () => {
    const { d1, raw } = makeDb();
    const env = makeEnv(d1);
    seedWorkerState(raw, 1);
    await seedAccount(raw, { platform: "youtube", label: "YouTube ES", credential: YT_CRED, verify_ok: 1, verified_at: NOW - 3600_000, verify_message: "x", status: "conectada" });
    await seedAccount(raw, { platform: "gemini", label: "Gemini", credential: "AIza-test-key" });
    // YT OK (token + tokeninfo), Gemini 500 (transient), sonda YT OK (token + tokeninfo).
    stubFetch([ytTokenOk, ytTokeninfoOk, gemini500, ytTokenOk, ytTokeninfoOk]);
    const r = await validateAccountsDaily(env, "sebastian", NOW);
    expect(r.partial).toBe(true);
    expect(r.summary).toBeNull();
    expect(r.valid).toBe(1);
    expect(r.transient).toBe(1);
    expect(readAccount(raw, 1).verify_ok).toBe(1);
    expect(readAccount(raw, 2).verify_ok).toBeNull();
    expect(readSettings(raw)[ACCOUNT_VALIDATION_DAY_KEY]).toBeUndefined();
  });

  test("fallo sistémico de descifrado (todas las cuentas) no escribe inválido, no alerta y deja constancia", async () => {
    const { d1, raw } = makeDb();
    const env = makeEnv(d1);
    seedWorkerState(raw, 1);
    await seedAccount(raw, { platform: "youtube", label: "YouTube ES", credential: YT_CRED, key: KEY_BAD, verify_ok: 1, verified_at: NOW - 3600_000, status: "conectada" });
    await seedAccount(raw, { platform: "gemini", label: "Gemini", credential: "AIza-test-key", key: KEY_BAD, verify_ok: 1, verified_at: NOW - 3600_000, status: "conectada" });
    stubFetch([]);
    const r = await validateAccountsDaily(env, "sebastian", NOW);
    expect(r.ran).toBe(true);
    expect(r.reason).toBe("systemic_decrypt");
    expect(r.summary).toBeNull();
    expect(readAccount(raw, 1).verify_ok).toBe(1);
    expect(readAccount(raw, 2).verify_ok).toBe(1);
    expect(countNotifications(raw)).toBe(0);
    expect(countWorkerEvents(raw, "validacion_cuentas", "%sistémico%")).toBe(1);
    expect(seen).toHaveLength(0);
  });

  test("una cuenta con error de descifrado no aborta la corrida (las demás se validan)", async () => {
    const { d1, raw } = makeDb();
    const env = makeEnv(d1);
    seedWorkerState(raw, 1);
    const badId = await seedAccount(raw, { platform: "youtube", label: "YouTube ES", credential: YT_CRED, key: KEY_BAD, verify_ok: 1, verified_at: NOW - 3600_000, status: "conectada" });
    await seedAccount(raw, { platform: "gemini", label: "Gemini", credential: "AIza-test-key" });
    // Gemini OK; sonda: YT sin credenciales descifrables → sin_conexion, sin fetch.
    stubFetch([geminiOk]);
    const r = await validateAccountsDaily(env, "sebastian", NOW);
    expect(r.reason).toBe("done");
    expect(r.summary).toEqual({ day: "2026-11-23", n_ok: 1, n_invalid: 1, checked_at: NOW });
    const bad = readAccount(raw, badId);
    expect(bad.verify_ok).toBe(0);
    expect(String(bad.verify_message)).toContain("No se pudo descifrar la credencial guardada");
    expect(countNotifications(raw)).toBe(1);
  });

  test("Worker parado no valida (sin validar hoy, sin alertas)", async () => {
    const { d1, raw } = makeDb();
    const env = makeEnv(d1);
    seedWorkerState(raw, 0);
    await seedAccount(raw, { platform: "youtube", label: "YouTube ES", credential: YT_CRED, verify_ok: 1, verified_at: NOW - 3600_000, status: "conectada" });
    stubFetch([]);
    const r = await maybeValidateAccountsDaily(env, "sebastian", NOW);
    expect(r?.ran).toBe(false);
    expect(r?.reason).toBe("worker_stopped");
    expect(readSettings(raw)[ACCOUNT_VALIDATION_DAY_KEY]).toBeUndefined();
    expect(countNotifications(raw)).toBe(0);
    expect(seen).toHaveLength(0);
  });

  test("el resumen checked_at es la hora de la corrida, no verified_at (A2 no lo refresca)", async () => {
    const { d1, raw } = makeDb();
    const env = makeEnv(d1);
    seedWorkerState(raw, 1);
    // Verificación idéntica a la guardada: A2 salta la escritura y verified_at queda viejo.
    await seedAccount(raw, {
      platform: "youtube", label: "YouTube ES", credential: YT_CRED,
      verify_ok: 1, verified_at: NOW - 7200_000,
      verify_message: "YouTube aceptó el token OAuth (scope youtube.upload, renovado con refresh_token).", status: "conectada",
    });
    stubFetch([ytTokenOk, ytTokeninfoOk, ytTokenOk, ytTokeninfoOk]);
    const r = await validateAccountsDaily(env, "sebastian", NOW);
    expect(r.summary?.checked_at).toBe(NOW);
    expect(readAccount(raw, 1).verified_at).toBe(NOW - 7200_000);
  });

  test("TikTok OK vía Zernio aunque la sonda diga sin_veredicto", async () => {
    const { d1, raw } = makeDb();
    const env = makeEnv(d1);
    seedWorkerState(raw, 1);
    await seedAccount(raw, { platform: "tiktok", label: "TikTok ES", credential: "zk_test_key", extra: JSON.stringify({ via: "zernio", account_id: "acc-1" }) });
    // Verificación: health Zernio OK. Sonda: sin cuentas YT/Meta → sin fetch.
    stubFetch([zernioHealthy]);
    const r = await validateAccountsDaily(env, "sebastian", NOW);
    expect(r.reason).toBe("done");
    expect(r.summary).toEqual({ day: "2026-11-23", n_ok: 1, n_invalid: 0, checked_at: NOW });
    expect(readAccount(raw, 1).verify_ok).toBe(1);
    const settings = readSettings(raw);
    const probes = settings.engagement_scopes as Record<string, { state: string }>;
    expect(probes["tiktok|es"].state).toBe("sin_veredicto");
  });
});

describe("rutas HTTP", () => {
  test("POST /api/accounts/validate-daily sin sesión → 401", async () => {
    const { d1 } = makeDb();
    const env = makeEnv(d1);
    const r = await callApi(env, "POST", "/api/accounts/validate-daily", { auth: false });
    expect(r.status).toBe(401);
  });

  test("POST /api/accounts/validate-daily con sesión marca el día aunque el Worker esté parado", async () => {
    const { d1, raw } = makeDb();
    const env = makeEnv(d1);
    seedWorkerState(raw, 0);
    await seedAccount(raw, { platform: "gemini", label: "Gemini", credential: "AIza-test-key" });
    stubFetch([geminiOk]);
    const r = await callApi(env, "POST", "/api/accounts/validate-daily", { body: {} });
    expect(r.status).toBe(200);
    expect(r.json.ok).toBe(true);
    expect(r.json.partial).toBe(false);
    expect(r.json.summary).toEqual({ day: expect.any(String), n_ok: 1, n_invalid: 0, checked_at: expect.any(Number) });
    // El día marcado es el día Chile del checked_at del MISMO
    // resumen (invariante que usa la puerta); no se compara con el
    // reloj real: otros archivos de la suite mockean Date y el
    // reloj puede cambiar entre la llamada y la aserción.
    const summary = r.json.summary as { day: string; checked_at: number };
    expect(summary.day).toBe(
      new Intl.DateTimeFormat("en-CA", { timeZone: "America/Santiago", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(summary.checked_at)),
    );
    expect((readSettings(raw)[ACCOUNT_VALIDATION_DAY_KEY] as { day: string }).day).toBe(summary.day);
  });

  test("GET /api/social-accounts enriquece cada fila con la validez de su cuenta API (meta alimenta IG y FB)", async () => {
    const { d1, raw } = makeDb();
    const env = makeEnv(d1);
    seedSocial(raw, "youtube", "es", "YouTube ES");
    seedSocial(raw, "tiktok", "es", "TikTok ES");
    seedSocial(raw, "instagram", "es", "Instagram ES");
    seedSocial(raw, "facebook", "es", "Facebook ES");
    await seedAccount(raw, { platform: "youtube", language: "es", label: "YouTube ES", credential: YT_CRED, verify_ok: 1, verified_at: NOW, verify_message: "YouTube aceptó el token.", status: "conectada" });
    await seedAccount(raw, { platform: "tiktok", language: "es", label: "TikTok ES", credential: "zk_test_key", verify_ok: 0, verified_at: NOW, verify_message: "Zernio rechazó la clave.", status: "error" });
    await seedAccount(raw, { platform: "meta", language: "es", label: "Meta", credential: "EAATest", verify_ok: 1, verified_at: NOW - 86400_000, credential_changed_at: NOW - 3600_000, verify_message: "Meta aceptó el token.", status: "conectada" });
    const r = await callApi(env, "GET", "/api/social-accounts");
    expect(r.status).toBe(200);
    const accounts = r.json.accounts as Array<Record<string, unknown>>;
    const byPlatform = new Map(accounts.map((a) => [String(a.platform), a]));
    expect(byPlatform.get("youtube")?.verification_current).toBe(true);
    expect(byPlatform.get("tiktok")?.verification_current).toBe(false);
    expect(byPlatform.get("tiktok")?.verify_message).toBe("Zernio rechazó la clave.");
    // Meta con OK viejo + credencial cambiada después: NO vigente, en IG y en FB.
    expect(byPlatform.get("instagram")?.verification_current).toBe(false);
    expect(byPlatform.get("facebook")?.verification_current).toBe(false);
    expect(byPlatform.get("instagram")?.api_account_id).toBe(byPlatform.get("facebook")?.api_account_id);
  });
});
