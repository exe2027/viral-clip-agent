// Validadores anti falsos positivos (spec 2026-11-23, V1+V2+V3).
// V1: cada veredicto de la sonda contra su evidencia real (no contra
// el texto del panel). V2: "Conectada" solo vale si la verificación
// es posterior al último cambio de credencial. V3: la pasada registra
// lo encontrado por plataforma+idioma, para que un 0/0 se pueda
// interpretar. Sin red real (fetch con cola), sin D1 real
// (bun:sqlite en memoria), sin secretos reales.
import { afterEach, describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { readFileSync } from "node:fs";
import { encryptCredential } from "../src/crypto";
import type { D1Database, Env } from "../src/cloudflare";
import { effectiveSettings, updateApiAccountVerification } from "../src/db";
import { isAccountVerificationCurrent } from "../src/db";
import { getEngagementStatus } from "../src/publish/zernio-engagement";
import { runZernioEngagement } from "../src/publish/zernio-engagement";
import { META_SCOPES, YOUTUBE_COMMENT_SCOPE } from "../src/publish/oauth-scopes";
import {
  invalidateProbeCache,
  probeNativeScopes,
  runEngagement,
  runNativeEngagement,
} from "../src/publish/native-engagement";
import { SqliteStmt } from "./budget-test-db";

const KEY = Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"); // SOLO TEST
const NOW = Date.UTC(2026, 10, 23, 18, 0, 0); // 23-nov 15:00 Santiago
const YT_SCOPES_OK =
  "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/youtube.force-ssl";
const YT_SCOPES_SIN_COMENTAR =
  "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly";
const META_APP_ID = "app.test";

function makeDb(): { d1: D1Database; raw: Database } {
  const raw = new Database(":memory:");
  raw.exec("CREATE TABLE settings (tenant_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at INTEGER NOT NULL)");
  raw.exec(
    "CREATE TABLE api_accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, platform TEXT NOT NULL, " +
      "language TEXT NOT NULL DEFAULT 'es', label TEXT NOT NULL DEFAULT '', credential TEXT NOT NULL DEFAULT '', " +
      "credential_enc TEXT NOT NULL DEFAULT '', extra TEXT NOT NULL DEFAULT '', extra_enc TEXT NOT NULL DEFAULT '', " +
      "status TEXT NOT NULL DEFAULT 'pendiente', note TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, " +
      "verified_at INTEGER, verify_ok INTEGER, verify_message TEXT NOT NULL DEFAULT '', " +
      "credential_changed_at INTEGER)",
  );
  raw.exec(
    "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'publish', " +
      "clip_id INTEGER, platform TEXT, language TEXT NOT NULL DEFAULT 'es', api_account_id INTEGER, state TEXT NOT NULL, " +
      "idempotency_key TEXT, resume_data TEXT, platform_id TEXT, evidence_url TEXT, title TEXT NOT NULL DEFAULT '', " +
      "caption TEXT NOT NULL DEFAULT '', r2_key TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, " +
      "retryable INTEGER NOT NULL DEFAULT 1, next_run_at INTEGER, posted_day TEXT, deleted_at INTEGER, " +
      "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)",
  );
  raw.exec(
    "CREATE TABLE clips (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, source_id INTEGER, " +
      "influencer_id INTEGER, title TEXT NOT NULL, start_sec INTEGER NOT NULL DEFAULT 0, end_sec INTEGER NOT NULL DEFAULT 0, " +
      "score INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'sugerido', " +
      "language TEXT NOT NULL DEFAULT 'es', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)",
  );
  raw.exec("CREATE TABLE worker_events (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, at INTEGER NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL)");
  raw.exec(
    "CREATE TABLE event_counters (tenant_id TEXT NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, " +
      "window_start INTEGER NOT NULL, count INTEGER NOT NULL DEFAULT 1, first_at INTEGER NOT NULL, last_at INTEGER NOT NULL, " +
      "PRIMARY KEY (tenant_id, source, kind, message, window_start))",
  );
  raw.exec(
    "CREATE TABLE engagement_log (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, day TEXT NOT NULL, " +
      "platform TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'es', account_id TEXT, action TEXT NOT NULL, " +
      "target_post_id TEXT, comment_id TEXT, status TEXT NOT NULL, reason TEXT, created_at INTEGER NOT NULL)",
  );
  raw.exec(
    "CREATE TABLE engagement_replied (tenant_id TEXT NOT NULL, comment_id TEXT NOT NULL, platform TEXT NOT NULL, " +
      "replied_at INTEGER NOT NULL, PRIMARY KEY (tenant_id, comment_id))",
  );
  raw.exec(
    "CREATE TABLE engagement_targets (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, platform TEXT NOT NULL, " +
      "language TEXT NOT NULL DEFAULT 'es', streamer TEXT NOT NULL, post_url TEXT NOT NULL, post_id TEXT NOT NULL, " +
      "kind TEXT NOT NULL, comment_text TEXT, status TEXT NOT NULL DEFAULT 'pending', reason TEXT, " +
      "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)",
  );
  const d1 = { prepare: (sql: string) => new SqliteStmt(raw, sql) } as unknown as D1Database;
  return { d1, raw };
}

function fakeEnv(d1: D1Database, extra: Record<string, string> = {}): Env {
  return { DB: d1, CREDENTIAL_ENCRYPTION_KEY: KEY, ...extra } as unknown as Env;
}

async function seedSettings(raw: Database, settings: Record<string, unknown>): Promise<void> {
  raw.query("INSERT INTO settings (tenant_id, data, updated_at) VALUES ('sebastian', ?1, ?2)").run(JSON.stringify(settings), NOW);
}

async function seedYoutube(raw: Database, language: string): Promise<void> {
  const cred = await encryptCredential("{}", KEY, "sebastian");
  const extra = await encryptCredential(JSON.stringify({ client_id: "cid.test", client_secret: "csec.test", refresh_token: "rtok.test" }), KEY, "sebastian");
  raw.query(
    "INSERT INTO api_accounts (tenant_id, platform, language, label, credential_enc, extra_enc, status, created_at) " +
      "VALUES ('sebastian', 'youtube', ?1, 'YouTube', ?2, ?3, 'ok', ?4)",
  ).run(language, cred, extra, NOW);
}

async function seedMeta(raw: Database, language: string): Promise<void> {
  const cred = await encryptCredential("meta-token.test", KEY, "sebastian");
  const extra = await encryptCredential(JSON.stringify({ ig_user_id: "17841400008460056", page_id: "123456789" }), KEY, "sebastian");
  raw.query(
    "INSERT INTO api_accounts (tenant_id, platform, language, label, credential_enc, extra_enc, status, created_at) " +
      "VALUES ('sebastian', 'meta', ?1, 'Meta', ?2, ?3, 'ok', ?4)",
  ).run(language, cred, extra, NOW);
}

async function seedZernioTikTok(raw: Database, language: string): Promise<void> {
  const cred = await encryptCredential("zk_.test", KEY, "sebastian");
  const extra = await encryptCredential(JSON.stringify({ via: "zernio", account_id: `acc_tt_${language}` }), KEY, "sebastian");
  raw.query(
    "INSERT INTO api_accounts (tenant_id, platform, language, label, credential_enc, extra_enc, status, created_at) " +
      "VALUES ('sebastian', 'tiktok', ?1, 'TikTok Zernio', ?2, ?3, 'ok', ?4)",
  ).run(language, cred, extra, NOW);
}

interface SeenRequest {
  url: string;
  method: string;
  body: string;
}

let seen: SeenRequest[] = [];
let queue: Array<{ status: number; body: unknown }> = [];
const realFetch = globalThis.fetch;

function stubFetch(responses: Array<{ status: number; body: unknown }>) {
  seen = [];
  queue = [...responses];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    let body = "";
    if (typeof init?.body === "string") body = init.body;
    seen.push({ url, method: init?.method ?? "GET", body });
    const next = queue.shift() ?? { status: 500, body: { error: "sin respuesta en la cola del test" } };
    return new Response(JSON.stringify(next.body), { status: next.status, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
}

afterEach(() => {
  globalThis.fetch = realFetch;
});

/** debug_token realista: app_id + is_valid + expires_at (0 = no
 *  expira). Sin estos campos la sonda V1 ya no da OK. */
function debugToken(scopes: string[], extra: Record<string, unknown> = {}): { status: number; body: unknown } {
  return {
    status: 200,
    body: { data: { app_id: META_APP_ID, is_valid: true, expires_at: 0, scopes, ...extra } },
  };
}

describe("V1: sonda de engagement contra su evidencia real", () => {
  test("YouTube OK = solo force-ssl (definición del código); sin force-ssl es sin_permiso con el scope exacto", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {});
    await seedYoutube(raw, "es");
    // Solo force-ssl, sin upload ni readonly: OK según el probe.
    stubFetch([
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { scope: YOUTUBE_COMMENT_SCOPE } },
    ]);
    const r1 = await probeNativeScopes(fakeEnv(d1), "sebastian", { force: true, nowMs: NOW });
    expect(r1.probes["youtube|es"]?.state).toBe("ok");
    expect(r1.probes["youtube|es"]?.missing_scopes).toEqual([]);
    // Sin force-ssl: sin_permiso, y nombra el scope que falta.
    stubFetch([
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { scope: YT_SCOPES_SIN_COMENTAR } },
    ]);
    const r2 = await probeNativeScopes(fakeEnv(d1), "sebastian", { force: true, nowMs: NOW });
    expect(r2.probes["youtube|es"]?.state).toBe("sin_permiso");
    expect(r2.probes["youtube|es"]?.missing_scopes).toEqual([YOUTUBE_COMMENT_SCOPE]);
  });

  test("Meta: debug_token que falla es sonda_fallida, NO sin_sonda", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {});
    await seedMeta(raw, "es");
    stubFetch([{ status: 500, body: { error: { message: "error interno" } } }]);
    const r = await probeNativeScopes(fakeEnv(d1, { META_APP_ID, META_APP_SECRET: "sec.test" }), "sebastian", { force: true, nowMs: NOW });
    expect(r.probes["instagram|es"]?.state).toBe("sonda_fallida");
    expect(r.probes["facebook|es"]?.state).toBe("sonda_fallida");
    // El error es el mensaje de Graph (no "sin_sonda": la app SÍ
    // está configurada, lo que falló fue la llamada).
    expect(r.probes["instagram|es"]?.error).toContain("error interno");
  });

  test("Meta: token de OTRA app con los scopes puestos NO da OK (falso positivo que V1 cierra)", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {});
    await seedMeta(raw, "es");
    stubFetch([
      debugToken(["instagram_manage_comments", "instagram_manage_engagement", "pages_manage_engagement"], { app_id: "otra.app" }),
    ]);
    const r = await probeNativeScopes(fakeEnv(d1, { META_APP_ID, META_APP_SECRET: "sec.test" }), "sebastian", { force: true, nowMs: NOW });
    expect(r.probes["instagram|es"]?.state).toBe("sonda_fallida");
    expect(r.probes["facebook|es"]?.state).toBe("sonda_fallida");
    expect(r.probes["instagram|es"]?.error).toContain("otra app");
  });

  test("Meta: token expirado según debug_token NO da OK", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {});
    await seedMeta(raw, "es");
    const expiredAt = Math.floor(NOW / 1000) - 3600;
    stubFetch([
      debugToken(["instagram_manage_comments", "instagram_manage_engagement", "pages_manage_engagement"], { expires_at: expiredAt }),
    ]);
    const r = await probeNativeScopes(fakeEnv(d1, { META_APP_ID, META_APP_SECRET: "sec.test" }), "sebastian", { force: true, nowMs: NOW });
    expect(r.probes["instagram|es"]?.state).toBe("sonda_fallida");
    expect(r.probes["instagram|es"]?.error).toContain("expirado");
  });

  test("Meta: token no válido según debug_token NO da OK", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {});
    await seedMeta(raw, "es");
    stubFetch([
      debugToken(["instagram_manage_comments"], { is_valid: false }),
    ]);
    const r = await probeNativeScopes(fakeEnv(d1, { META_APP_ID, META_APP_SECRET: "sec.test" }), "sebastian", { force: true, nowMs: NOW });
    expect(r.probes["instagram|es"]?.state).toBe("sonda_fallida");
    expect(r.probes["instagram|es"]?.error).toContain("no es válido");
  });

  test("Instagram: OK con solo el scope de respuesta; el de likes afuera faltante es nota, no fallo", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {});
    await seedMeta(raw, "es");
    stubFetch([debugToken(["instagram_manage_comments"])]);
    const r = await probeNativeScopes(fakeEnv(d1, { META_APP_ID, META_APP_SECRET: "sec.test" }), "sebastian", { force: true, nowMs: NOW });
    expect(r.probes["instagram|es"]?.state).toBe("ok");
    expect(r.probes["instagram|es"]?.missing_scopes).toEqual(["instagram_manage_engagement"]);
    expect(r.probes["instagram|es"]?.error).toContain("para likes afuera");
    // Inverso: con el scope de likes pero SIN el de respuesta, es sin_permiso.
    stubFetch([debugToken(["instagram_manage_engagement", "pages_manage_engagement"])]);
    const r2 = await probeNativeScopes(fakeEnv(d1, { META_APP_ID, META_APP_SECRET: "sec.test" }), "sebastian", { force: true, nowMs: NOW });
    expect(r2.probes["instagram|es"]?.state).toBe("sin_permiso");
    expect(r2.probes["instagram|es"]?.missing_scopes).toContain("instagram_manage_comments");
  });

  test("TikTok: la sonda declara sin_veredicto explícito, nunca una casilla vacía ni un OK implícito", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {});
    await seedYoutube(raw, "es");
    stubFetch([
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { scope: YT_SCOPES_OK } },
    ]);
    const r = await probeNativeScopes(fakeEnv(d1), "sebastian", { force: true, nowMs: NOW });
    expect(r.probes["tiktok|es"]?.state).toBe("sin_veredicto");
    expect(r.probes["tiktok|en"]?.state).toBe("sin_veredicto");
    expect(r.probes["tiktok|es"]?.error).toContain("Cuentas");
    expect(r.probes["youtube|es"]?.state).toBe("ok");
  });

  test("la sonda indica si el resultado es de caché o fresco, y su edad", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {});
    await seedYoutube(raw, "es");
    stubFetch([
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { scope: YT_SCOPES_OK } },
    ]);
    const fresh = await probeNativeScopes(fakeEnv(d1), "sebastian", { force: true, nowMs: NOW });
    expect(fresh.cached).toBe(false);
    expect(fresh.checked_at).toBe(NOW);
    stubFetch([]);
    const cached = await probeNativeScopes(fakeEnv(d1), "sebastian", { nowMs: NOW + 1000 });
    expect(cached.cached).toBe(true);
    expect(cached.checked_at).toBe(NOW); // edad: 1 s, viene de checked_at
    expect(seen).toHaveLength(0);
    // Forzada: fresca otra vez.
    stubFetch([
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { scope: YT_SCOPES_OK } },
    ]);
    const forced = await probeNativeScopes(fakeEnv(d1), "sebastian", { force: true, nowMs: NOW + 2000 });
    expect(forced.cached).toBe(false);
  });

  test("invalidar la caché tras cambiar credenciales: la siguiente sonda es fresca", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {});
    await seedYoutube(raw, "es");
    stubFetch([
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { scope: YT_SCOPES_OK } },
    ]);
    await probeNativeScopes(fakeEnv(d1), "sebastian", { force: true, nowMs: NOW });
    await invalidateProbeCache(d1, "sebastian");
    stubFetch([
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { scope: YT_SCOPES_OK } },
    ]);
    const r = await probeNativeScopes(fakeEnv(d1), "sebastian", { nowMs: NOW + 1000 });
    expect(r.cached).toBe(false);
    expect(seen).toHaveLength(2);
  });

  test("estado de referencia 21:09: YT ES+EN, IG ES y FB ES en OK; TikTok sin_veredicto", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {});
    await seedYoutube(raw, "es");
    await seedYoutube(raw, "en");
    await seedMeta(raw, "es");
    const metaScopes = META_SCOPES.split(",");
    const future = Math.floor(NOW / 1000) + 86400;
    stubFetch([
      { status: 200, body: { access_token: "ya29.es" } },
      { status: 200, body: { scope: YT_SCOPES_OK } },
      debugToken(metaScopes, { expires_at: future }),
      { status: 200, body: { access_token: "ya29.en" } },
      { status: 200, body: { scope: YT_SCOPES_OK } },
    ]);
    const r = await probeNativeScopes(fakeEnv(d1, { META_APP_ID, META_APP_SECRET: "sec.test" }), "sebastian", { force: true, nowMs: NOW });
    expect(r.cached).toBe(false);
    expect(r.probes["youtube|es"]?.state).toBe("ok");
    expect(r.probes["youtube|en"]?.state).toBe("ok");
    expect(r.probes["instagram|es"]?.state).toBe("ok");
    expect(r.probes["instagram|es"]?.error).toBeNull();
    expect(r.probes["facebook|es"]?.state).toBe("ok");
    expect(r.probes["instagram|en"]?.state).toBe("sin_conexion");
    expect(r.probes["facebook|en"]?.state).toBe("sin_conexion");
    expect(r.probes["tiktok|es"]?.state).toBe("sin_veredicto");
    expect(r.probes["tiktok|en"]?.state).toBe("sin_veredicto");
  });

  test("getEngagementStatus: TikTok lee el veredicto guardado (sin_veredicto) y expone si la sonda está cacheada", async () => {
    const { d1, raw } = makeDb();
    const at = Date.now();
    await seedSettings(raw, {
      engagement_enabled: true,
      engagement_scopes: {
        "tiktok|es": { state: "sin_veredicto", granted_scopes: [], missing_scopes: [], checked_at: at, error: "la sonda no cubre TikTok: el estado real está en Cuentas (vía Zernio)" },
      },
      engagement_scopes_at: at,
    });
    const st = await getEngagementStatus(fakeEnv(d1), "sebastian");
    expect(st.native["tiktok|es"]?.state).toBe("sin_veredicto");
    expect(st.native["tiktok|es"]?.via).toBe("zernio");
    expect(st.native["tiktok|es"]?.error).toContain("Cuentas");
    expect(st.scopes_checked_at).toBe(at);
    expect(st.scopes_cached).toBe(true);
  });
});

describe("V2: vigencia de la verificación en Cuentas", () => {
  test("isAccountVerificationCurrent: solo vale si verified_at >= último cambio de credencial", () => {
    // Verificado después del cambio: vigente.
    expect(isAccountVerificationCurrent({ verified_at: 2000, verify_ok: 1, credential_changed_at: 1000 })).toBe(true);
    // Verificado ANTES del cambio: falso positivo (es de la credencial anterior).
    expect(isAccountVerificationCurrent({ verified_at: 1000, verify_ok: 1, credential_changed_at: 2000 })).toBe(false);
    // Sin cambio registrado (filas pre-0043): la verificación vigente se respeta.
    expect(isAccountVerificationCurrent({ verified_at: 1000, verify_ok: 1, credential_changed_at: null })).toBe(true);
    expect(isAccountVerificationCurrent({ verified_at: 1000, verify_ok: 1 })).toBe(true);
    // Nunca verificada o con error: nunca vigente.
    expect(isAccountVerificationCurrent({ verified_at: null, verify_ok: 1, credential_changed_at: null })).toBe(false);
    expect(isAccountVerificationCurrent({ verified_at: 2000, verify_ok: 0, credential_changed_at: 1000 })).toBe(false);
  });

  test("migración 0043: añade credential_changed_at a api_accounts", () => {
    const sql = readFileSync(new URL("../../migrations/0043_credential_changed_at.sql", import.meta.url), "utf-8");
    const raw = new Database(":memory:");
    raw.exec(
      "CREATE TABLE api_accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, platform TEXT NOT NULL, " +
        "language TEXT NOT NULL DEFAULT 'es', label TEXT NOT NULL DEFAULT '', credential TEXT NOT NULL DEFAULT '', " +
        "credential_enc TEXT NOT NULL DEFAULT '', extra TEXT NOT NULL DEFAULT '', extra_enc TEXT NOT NULL DEFAULT '', " +
        "status TEXT NOT NULL DEFAULT 'pendiente', note TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, " +
        "verified_at INTEGER, verify_ok INTEGER, verify_message TEXT NOT NULL DEFAULT '')",
    );
    raw.exec(sql);
    const cols = raw.query("PRAGMA table_info(api_accounts)").all() as Array<{ name: string }>;
    expect(cols.map((c) => c.name)).toContain("credential_changed_at");
  });

  test("A2 es diseño: re-verificar sin cambio NO refresca verified_at (no es falso positivo)", async () => {
    const { d1, raw } = makeDb();
    // Semilla con un verified_at arbitrario (no el reloj real: en la
    // suite completa otro archivo fija Date.now, así que aquí no se
    // compara contra el reloj, solo contra la semilla).
    const SEEDED = 1234567890000;
    raw.query(
      "INSERT INTO api_accounts (tenant_id, platform, language, label, status, created_at, verified_at, verify_ok, verify_message) " +
        "VALUES ('sebastian', 'youtube', 'es', 'YouTube', 'conectada', ?1, ?2, 1, 'YouTube aceptó el token OAuth.')",
    ).run(NOW, SEEDED);
    const id = (raw.query("SELECT id FROM api_accounts").get() as { id: number }).id;
    await updateApiAccountVerification(d1, id, "sebastian", true, "conectada", "YouTube aceptó el token OAuth.");
    const row1 = raw.query("SELECT verified_at, verify_message FROM api_accounts WHERE id = ?1").get(id) as { verified_at: number; verify_message: string };
    expect(row1.verified_at).toBe(SEEDED);
    // Con un mensaje distinto SÍ se actualiza (mensaje nuevo y
    // verified_at distinto de la semilla).
    await updateApiAccountVerification(d1, id, "sebastian", true, "conectada", "YouTube aceptó el token OAuth (nuevo).");
    const row2 = raw.query("SELECT verified_at, verify_message FROM api_accounts WHERE id = ?1").get(id) as { verified_at: number; verify_message: string };
    expect(row2.verify_message).toBe("YouTube aceptó el token OAuth (nuevo).");
    expect(row2.verified_at).not.toBe(SEEDED);
  });

  test("regresión hecho 2: engagement_enabled guardado como 1 en D1 se lee encendido", () => {
    expect(effectiveSettings({ engagement_enabled: 1 }).engagement_enabled).toBe(true);
    expect(effectiveSettings({ engagement_enabled: true }).engagement_enabled).toBe(true);
    expect(effectiveSettings({ engagement_enabled: 0 }).engagement_enabled).toBe(false);
    expect(effectiveSettings({}).engagement_enabled).toBe(false);
  });
});

describe("V3: la pasada registra lo encontrado por plataforma+idioma", () => {
  test("nativa YT: 1 comentario encontrado, 1 post consultado, 0 fallos", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, { engagement_enabled: true });
    await seedYoutube(raw, "es");
    raw.query("INSERT INTO clips (id, tenant_id, title, language, created_at, updated_at) VALUES (7, 'sebastian', 'Rubius descubre los cromos', 'es', ?1, ?1)").run(NOW);
    raw.query(
      "INSERT INTO jobs (tenant_id, kind, clip_id, platform, language, state, platform_id, evidence_url, created_at, updated_at) " +
        "VALUES ('sebastian', 'publish', 7, 'youtube_shorts', 'es', 'posted', 'vid1', 'https://www.youtube.com/shorts/vid1', ?1, ?1)",
    ).run(NOW);
    const threads = {
      items: [
        { id: "th1", snippet: { topLevelComment: { id: "c1", snippet: { textDisplay: "¡Qué buen clip!", authorDisplayName: "fan123" } } }, replies: { comments: [] } },
      ],
    };
    stubFetch([
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { scope: YT_SCOPES_OK } },
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { items: [{ snippet: { title: "ElClipDeGTA6" } }] } },
      { status: 200, body: threads },
      { status: 200, body: {} },
      { status: 200, body: {} },
    ]);
    const s = await runNativeEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW, sleepMs: 0, force: true });
    expect(s.replies).toBe(1);
    expect(s.found["youtube|es"]).toEqual({ comments: 1, postsChecked: 1, postsFailed: 0 });
  });

  test("nativa YT: el listado falla → postsFailed 1, comments 0: es 'no se pudo listar', no 'sin comentarios'", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, { engagement_enabled: true });
    await seedYoutube(raw, "es");
    raw.query("INSERT INTO clips (id, tenant_id, title, language, created_at, updated_at) VALUES (7, 'sebastian', 'Rubius descubre los cromos', 'es', ?1, ?1)").run(NOW);
    raw.query(
      "INSERT INTO jobs (tenant_id, kind, clip_id, platform, language, state, platform_id, evidence_url, created_at, updated_at) " +
        "VALUES ('sebastian', 'publish', 7, 'youtube_shorts', 'es', 'posted', 'vid1', 'https://www.youtube.com/shorts/vid1', ?1, ?1)",
    ).run(NOW);
    stubFetch([
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { scope: YT_SCOPES_OK } },
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { items: [{ snippet: { title: "ElClipDeGTA6" } }] } },
      { status: 500, body: { error: { message: "error interno" } } },
    ]);
    const s = await runNativeEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW, sleepMs: 0, force: true });
    expect(s.replies).toBe(0);
    expect(s.found["youtube|es"]).toEqual({ comments: 0, postsChecked: 1, postsFailed: 1 });
    expect(s.skipped.some((x) => x.what.includes("hilo youtube vid1"))).toBe(true);
  });

  test("nativa YT: 0 comentarios en 1 post consultado → 'sin trabajo', distinto del fallo", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, { engagement_enabled: true });
    await seedYoutube(raw, "es");
    raw.query("INSERT INTO clips (id, tenant_id, title, language, created_at, updated_at) VALUES (7, 'sebastian', 'Rubius descubre los cromos', 'es', ?1, ?1)").run(NOW);
    raw.query(
      "INSERT INTO jobs (tenant_id, kind, clip_id, platform, language, state, platform_id, evidence_url, created_at, updated_at) " +
        "VALUES ('sebastian', 'publish', 7, 'youtube_shorts', 'es', 'posted', 'vid1', 'https://www.youtube.com/shorts/vid1', ?1, ?1)",
    ).run(NOW);
    stubFetch([
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { scope: YT_SCOPES_OK } },
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { items: [{ snippet: { title: "ElClipDeGTA6" } }] } },
      { status: 200, body: { items: [] } },
    ]);
    const s = await runNativeEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW, sleepMs: 0, force: true });
    expect(s.replies).toBe(0);
    expect(s.found["youtube|es"]).toEqual({ comments: 0, postsChecked: 1, postsFailed: 0 });
  });

  test("Zernio TikTok: 2 comentarios encontrados en 1 post, ambos respondidos", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, { engagement_enabled: true });
    await seedZernioTikTok(raw, "es");
    const accounts = { accounts: [{ _id: "acc_tt_es", platform: "tiktok", username: "elclipdegta6____bits" }] };
    const inbox = { data: [{ id: "post1", platform: "tiktok", accountId: "acc_tt_es", commentCount: 2 }] };
    const comments = [
      { id: "c1", text: "¡Buenísimo!", from: { username: "fan1" } },
      { id: "c2", text: "jaja qué bueno", from: { username: "fan2" } },
    ];
    stubFetch([
      { status: 200, body: accounts },
      { status: 200, body: inbox },
      { status: 200, body: { comments } },
      { status: 200, body: {} },
      { status: 200, body: {} },
      { status: 200, body: {} },
      { status: 200, body: {} },
    ]);
    const s = await runZernioEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW, sleepMs: 0, force: true, platforms: ["tiktok"] });
    expect(s.replies).toBe(2);
    expect(s.found["tiktok|es"]).toEqual({ comments: 2, postsChecked: 1, postsFailed: 0 });
  });

  test("Zernio TikTok: el inbox falla → postsFailed 1, no 'sin comentarios'", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, { engagement_enabled: true });
    await seedZernioTikTok(raw, "es");
    const accounts = { accounts: [{ _id: "acc_tt_es", platform: "tiktok", username: "elclipdegta6____bits" }] };
    stubFetch([
      { status: 200, body: accounts },
      { status: 500, body: { error: { message: "inbox caído" } } },
    ]);
    const s = await runZernioEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW, sleepMs: 0, force: true, platforms: ["tiktok"] });
    expect(s.found["tiktok|es"]).toEqual({ comments: 0, postsChecked: 0, postsFailed: 1 });
    expect(s.skipped.some((x) => x.what.includes("inbox tiktok"))).toBe(true);
  });

  test("orquestador: el found combina nativa YT + Zernio TikTok", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, { engagement_enabled: true });
    await seedYoutube(raw, "es");
    await seedZernioTikTok(raw, "es");
    raw.query("INSERT INTO clips (id, tenant_id, title, language, created_at, updated_at) VALUES (7, 'sebastian', 'Rubius descubre los cromos', 'es', ?1, ?1)").run(NOW);
    raw.query(
      "INSERT INTO jobs (tenant_id, kind, clip_id, platform, language, state, platform_id, evidence_url, created_at, updated_at) " +
        "VALUES ('sebastian', 'publish', 7, 'youtube_shorts', 'es', 'posted', 'vid1', 'https://www.youtube.com/shorts/vid1', ?1, ?1)",
    ).run(NOW);
    const threads = {
      items: [
        { id: "th1", snippet: { topLevelComment: { id: "c1", snippet: { textDisplay: "¡Qué buen clip!", authorDisplayName: "fan123" } } }, replies: { comments: [] } },
      ],
    };
    const accounts = { accounts: [{ _id: "acc_tt_es", platform: "tiktok", username: "elclipdegta6____bits" }] };
    stubFetch([
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { scope: YT_SCOPES_OK } },
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { items: [{ snippet: { title: "ElClipDeGTA6" } }] } },
      { status: 200, body: threads },
      { status: 200, body: {} },
      { status: 200, body: {} },
      { status: 200, body: accounts },
      { status: 200, body: { data: [] } },
    ]);
    const s = await runEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW, sleepMs: 0, force: true });
    expect(s.ran).toBe(true);
    expect(s.found["youtube|es"]).toEqual({ comments: 1, postsChecked: 1, postsFailed: 0 });
    expect(s.found["tiktok|es"]).toEqual({ comments: 0, postsChecked: 0, postsFailed: 0 });
  });
});
