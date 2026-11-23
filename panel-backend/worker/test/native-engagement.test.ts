// Tests de Engagement v2 NATIVO (worker/src/publish/
// native-engagement.ts + oauth-scopes.ts, v5.4.0, 2026-11-23).
// Sin red real (fetch con cola), sin D1 real (bun:sqlite en memoria
// con las tablas de la migración 0040 + las que tocan getSettings,
// resolveCreds, listPostedJobs y logWorkerEvent), sin secretos
// reales: tokens y claves inventados.
import { afterEach, describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { encryptCredential } from "../src/crypto";
import type { D1Database, Env } from "../src/cloudflare";
import { getEngagementStatus, listEngagementEvents } from "../src/publish/zernio-engagement";
import {
  GOOGLE_SCOPES,
  INSTAGRAM_LIKE_SCOPE,
  INSTAGRAM_REPLY_SCOPE,
  META_SCOPES,
  YOUTUBE_COMMENT_SCOPE,
} from "../src/publish/oauth-scopes";
import {
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

function makeDb(): { d1: D1Database; raw: Database } {
  const raw = new Database(":memory:");
  raw.exec("CREATE TABLE settings (tenant_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at INTEGER NOT NULL)");
  raw.exec(
    "CREATE TABLE api_accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, platform TEXT NOT NULL, " +
      "language TEXT NOT NULL DEFAULT 'es', label TEXT NOT NULL DEFAULT '', credential TEXT NOT NULL DEFAULT '', " +
      "credential_enc TEXT NOT NULL DEFAULT '', extra TEXT NOT NULL DEFAULT '', extra_enc TEXT NOT NULL DEFAULT '', " +
      "status TEXT NOT NULL DEFAULT 'pendiente', note TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, " +
      "verified_at INTEGER, verify_ok INTEGER, verify_message TEXT NOT NULL DEFAULT '')",
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

describe("scopes OAuth (fuente única)", () => {
  test("GOOGLE_SCOPES incluye youtube.force-ssl y META_SCOPES los dos permisos de comentar", () => {
    expect(GOOGLE_SCOPES).toContain(YOUTUBE_COMMENT_SCOPE);
    expect(GOOGLE_SCOPES).toContain("youtube.upload");
    expect(META_SCOPES).toContain(INSTAGRAM_REPLY_SCOPE);
    expect(META_SCOPES).toContain(INSTAGRAM_LIKE_SCOPE);
    expect(META_SCOPES).toContain("pages_manage_engagement");
  });
});

describe("probeNativeScopes", () => {
  test("YouTube sin force-ssl: sin_permiso con el scope exacto que falta", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {});
    await seedYoutube(raw, "es");
    stubFetch([
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { scope: YT_SCOPES_SIN_COMENTAR } },
    ]);
    const r = await probeNativeScopes(fakeEnv(d1), "sebastian", { force: true, nowMs: NOW });
    expect(r.probes["youtube|es"]?.state).toBe("sin_permiso");
    expect(r.probes["youtube|es"]?.missing_scopes).toEqual([YOUTUBE_COMMENT_SCOPE]);
    expect(r.probes["youtube|es"]?.granted_scopes).toContain("https://www.googleapis.com/auth/youtube.readonly");
    expect(r.probes["youtube|en"]?.state).toBe("sin_conexion");
  });

  test("YouTube con force-ssl: ok; la segunda sonda usa la caché y no llama a Google", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {});
    await seedYoutube(raw, "es");
    stubFetch([
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { scope: YT_SCOPES_OK } },
    ]);
    const r1 = await probeNativeScopes(fakeEnv(d1), "sebastian", { force: true, nowMs: NOW });
    expect(r1.probes["youtube|es"]?.state).toBe("ok");
    expect(seen).toHaveLength(2);
    stubFetch([]);
    const r2 = await probeNativeScopes(fakeEnv(d1), "sebastian", { nowMs: NOW + 1000 });
    expect(r2.probes["youtube|es"]?.state).toBe("ok");
    expect(seen).toHaveLength(0);
  });

  test("Meta sin META_APP_ID/SECRET: sin_sonda y NO se llama a debug_token (no se finge)", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {});
    await seedMeta(raw, "es");
    stubFetch([]);
    const r = await probeNativeScopes(fakeEnv(d1), "sebastian", { force: true, nowMs: NOW });
    expect(r.meta_configured).toBe(false);
    expect(r.probes["instagram|es"]?.state).toBe("sin_sonda");
    expect(r.probes["facebook|es"]?.state).toBe("sin_sonda");
    expect(seen).toHaveLength(0);
  });

  test("Meta con app configurada: debug_token dice los scopes; falta pages_manage_engagement", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {});
    await seedMeta(raw, "es");
    stubFetch([
      { status: 200, body: { data: { scopes: ["public_profile", "instagram_basic", "instagram_manage_comments", "instagram_manage_engagement"] } } },
    ]);
    const env = fakeEnv(d1, { META_APP_ID: "app.test", META_APP_SECRET: "sec.test" });
    const r = await probeNativeScopes(env, "sebastian", { force: true, nowMs: NOW });
    expect(r.meta_configured).toBe(true);
    expect(r.probes["instagram|es"]?.state).toBe("ok");
    expect(r.probes["facebook|es"]?.state).toBe("sin_permiso");
    expect(r.probes["facebook|es"]?.missing_scopes).toEqual(["pages_manage_engagement"]);
    expect(seen[0]?.url).toContain("debug_token");
  });
});

describe("runNativeEngagement", () => {
  test("sin force-ssl: 0 respuestas, NUNCA se llama a comentar, y queda sin_permiso en el log", async () => {
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
      { status: 200, body: { scope: YT_SCOPES_SIN_COMENTAR } },
    ]);
    const s = await runNativeEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW, sleepMs: 0, force: true });
    expect(s.ran).toBe(true);
    expect(s.replies).toBe(0);
    expect(seen.some((r) => r.url.includes("commentThreads") || r.url.includes("/comments"))).toBe(false);
    const events = await listEngagementEvents(d1, "sebastian", 10);
    expect(events.some((e) => e.status === "skipped" && (e.reason ?? "").includes("sin_permiso") && (e.reason ?? "").includes("youtube.force-ssl"))).toBe(true);
  });

  test("con force-ssl: responde y da like a un comentario de YouTube, y no lo repite", async () => {
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
    // Pasada 1: sonda (refresh+tokeninfo), runner (refresh, canal, hilos, reply, like).
    stubFetch([
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { scope: YT_SCOPES_OK } },
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { items: [{ snippet: { title: "ElClipDeGTA6" } }] } },
      { status: 200, body: threads },
      { status: 200, body: {} },
      { status: 200, body: {} },
    ]);
    const s1 = await runNativeEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW, sleepMs: 0, force: true });
    expect(s1.replies).toBe(1);
    expect(s1.commentLikes).toBe(1);
    const replyCall = seen.find((r) => r.method === "POST" && r.url.includes("/youtube/v3/comments?"));
    expect(replyCall).toBeTruthy();
    expect(replyCall?.body).toContain("c1");
    // Pasada 2 (force, sonda cacheada): el mismo comentario ya está en replied.
    stubFetch([
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { items: [{ snippet: { title: "ElClipDeGTA6" } }] } },
      { status: 200, body: threads },
    ]);
    const s2 = await runNativeEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW + 3600_000, sleepMs: 0, force: true });
    expect(s2.replies).toBe(0);
  });

  test("Instagram con scopes: responde por Graph API", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, { engagement_enabled: true });
    await seedMeta(raw, "es");
    raw.query("INSERT INTO clips (id, tenant_id, title, language, created_at, updated_at) VALUES (7, 'sebastian', 'Noni llega a la playa', 'es', ?1, ?1)").run(NOW);
    raw.query(
      "INSERT INTO jobs (tenant_id, kind, clip_id, platform, language, state, platform_id, evidence_url, created_at, updated_at) " +
        "VALUES ('sebastian', 'publish', 7, 'instagram_reels', 'es', 'posted', '17890000000000001', 'https://www.instagram.com/reel/DgFpzP6iJWe/', ?1, ?1)",
    ).run(NOW);
    const env = fakeEnv(d1, { META_APP_ID: "app.test", META_APP_SECRET: "sec.test" });
    const comments = { data: [{ id: "igc1", text: "¡Brutal!", username: "fan9", replies: { data: [] } }] };
    stubFetch([
      { status: 200, body: { data: { scopes: ["public_profile", "instagram_basic", "instagram_manage_comments", "instagram_manage_engagement", "pages_manage_engagement"] } } },
      { status: 200, body: { username: "elmegaclipgta6" } },
      { status: 200, body: comments },
      { status: 200, body: { id: "igc1_reply" } },
      { status: 200, body: { success: true } },
      { status: 200, body: { name: "El Clip de GTA 6" } },
    ]);
    const s = await runNativeEngagement(env, "sebastian", { nowMs: NOW, sleepMs: 0, force: true });
    expect(s.replies).toBe(1);
    expect(s.commentLikes).toBe(1);
    expect(seen.some((r) => r.method === "POST" && r.url.includes("/igc1/replies"))).toBe(true);
  });

  test("objetivo Facebook like con scope: like por Graph; objetivo TikTok queda pendiente (es de Zernio)", async () => {
    const { d1, raw } = makeDb();
    // Fuera de la ventana de modo seguro (24 h): afuera no está
    // bloqueado; el modo seguro se cubre en zernio-engagement.test.ts.
    await seedSettings(raw, { engagement_enabled: true, engagement_first_run_at: NOW - 25 * 3600_000 });
    await seedMeta(raw, "es");
    raw.query(
      "INSERT INTO engagement_targets (tenant_id, platform, language, streamer, post_url, post_id, kind, status, created_at, updated_at) " +
        "VALUES ('sebastian', 'facebook', 'es', 'rubius', 'https://www.facebook.com/reel/1173076921827728/', '1173076921827728', 'like', 'pending', ?1, ?1)",
    ).run(NOW);
    raw.query(
      "INSERT INTO engagement_targets (tenant_id, platform, language, streamer, post_url, post_id, kind, status, created_at, updated_at) " +
        "VALUES ('sebastian', 'tiktok', 'es', 'rubius', 'https://www.tiktok.com/@rubius/video/1234567890123456789', '1234567890123456789', 'like', 'pending', ?1, ?1)",
    ).run(NOW);
    const env = fakeEnv(d1, { META_APP_ID: "app.test", META_APP_SECRET: "sec.test" });
    stubFetch([
      { status: 200, body: { data: { scopes: ["public_profile", "instagram_basic", "instagram_manage_comments", "instagram_manage_engagement", "pages_manage_engagement"] } } },
      { status: 200, body: { username: "elmegaclipgta6" } },
      { status: 200, body: { name: "El Clip de GTA 6" } },
      { status: 200, body: { success: true } },
    ]);
    const s = await runNativeEngagement(env, "sebastian", { nowMs: NOW, sleepMs: 0, force: true });
    expect(s.outwardLikes).toBe(1);
    expect(seen.some((r) => r.method === "POST" && r.url.includes("/1173076921827728/likes"))).toBe(true);
    const fb = raw.query("SELECT status FROM engagement_targets WHERE platform = 'facebook'").get() as { status: string };
    expect(fb.status).toBe("done");
    const tt = raw.query("SELECT status FROM engagement_targets WHERE platform = 'tiktok'").get() as { status: string };
    expect(tt.status).toBe("pending");
  });

  test("objetivo con sin_permiso queda pendiente (no se quema) y se registra", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, { engagement_enabled: true });
    await seedMeta(raw, "es");
    raw.query(
      "INSERT INTO engagement_targets (tenant_id, platform, language, streamer, post_url, post_id, kind, status, created_at, updated_at) " +
        "VALUES ('sebastian', 'facebook', 'es', 'rubius', 'https://www.facebook.com/reel/1173076921827728/', '1173076921827728', 'like', 'pending', ?1, ?1)",
    ).run(NOW);
    const env = fakeEnv(d1, { META_APP_ID: "app.test", META_APP_SECRET: "sec.test" });
    stubFetch([
      { status: 200, body: { data: { scopes: ["public_profile", "instagram_basic"] } } },
    ]);
    const s = await runNativeEngagement(env, "sebastian", { nowMs: NOW, sleepMs: 0, force: true });
    expect(s.outwardLikes).toBe(0);
    expect(seen.some((r) => r.url.includes("/likes"))).toBe(false);
    const row = raw.query("SELECT status FROM engagement_targets WHERE platform = 'facebook'").get() as { status: string };
    expect(row.status).toBe("pending");
    const events = await listEngagementEvents(d1, "sebastian", 10);
    expect(events.some((e) => (e.reason ?? "").includes("sin_permiso") && (e.reason ?? "").includes("pages_manage_engagement"))).toBe(true);
  });
});

describe("runEngagement (orquestador v2)", () => {
  test("nativa YT + Zernio solo TikTok: el objetivo TikTok lo marca Zernio, sin tocar la nativa", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, { engagement_enabled: true });
    await seedYoutube(raw, "es");
    await seedZernioTikTok(raw, "es");
    raw.query(
      "INSERT INTO engagement_targets (tenant_id, platform, language, streamer, post_url, post_id, kind, status, created_at, updated_at) " +
        "VALUES ('sebastian', 'tiktok', 'es', 'rubius', 'https://www.tiktok.com/@rubius/video/1234567890123456789', '1234567890123456789', 'like', 'pending', ?1, ?1)",
    ).run(NOW);
    const accounts = { accounts: [{ _id: "acc_tt", platform: "tiktok", username: "elclipdegta6____bits" }] };
    stubFetch([
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { scope: YT_SCOPES_OK } },
      { status: 200, body: { access_token: "ya29.test" } },
      { status: 200, body: { items: [{ snippet: { title: "ElClipDeGTA6" } }] } },
      { status: 200, body: accounts },
      { status: 200, body: { data: [] } },
    ]);
    const s = await runEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW, sleepMs: 0, force: true });
    expect(s.ran).toBe(true);
    expect(s.native.ran).toBe(true);
    expect(s.zernio.ran).toBe(true);
    // Zernio no llamó a ningún inbox que no sea TikTok.
    expect(seen.filter((r) => r.url.includes("/inbox/comments?"))).toHaveLength(1);
    expect(seen.filter((r) => r.url.includes("/inbox/comments?"))[0]?.url).toContain("platform=tiktok");
    const row = raw.query("SELECT status, reason FROM engagement_targets WHERE platform = 'tiktok'").get() as { status: string; reason: string };
    expect(row.status).toBe("skipped");
    expect(row.reason).toContain("tiktok_sin_like_zernio");
  });

  test("apagado: ni nativa ni Zernio llaman a nada", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {});
    stubFetch([]);
    const s = await runEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW, sleepMs: 0 });
    expect(s.ran).toBe(false);
    expect(seen).toHaveLength(0);
  });
});

describe("getEngagementStatus v2", () => {
  test("status trae la vista nativa por plataforma+idioma y los últimos eventos", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {
      engagement_enabled: true,
      engagement_scopes: {
        "youtube|es": { state: "ok", granted_scopes: ["a", "b", "c"], missing_scopes: [], checked_at: NOW, error: null },
        "instagram|es": { state: "sin_permiso", granted_scopes: ["instagram_basic"], missing_scopes: ["instagram_manage_comments"], checked_at: NOW, error: "falta instagram_manage_comments" },
      },
      engagement_scopes_at: NOW,
    });
    raw.query(
      "INSERT INTO engagement_log (tenant_id, day, platform, language, action, status, reason, created_at) " +
        "VALUES ('sebastian', '2026-11-23', 'instagram', 'es', 'reply', 'skipped', 'sin_permiso: falta scope instagram_manage_comments', ?1)",
    ).run(NOW);
    const st = await getEngagementStatus(fakeEnv(d1), "sebastian");
    expect(st.enabled).toBe(true);
    expect(st.native["youtube|es"]?.state).toBe("ok");
    expect(st.native["youtube|es"]?.via).toBe("nativa");
    expect(st.native["instagram|es"]?.state).toBe("sin_permiso");
    expect(st.native["instagram|es"]?.missing_scopes).toEqual(["instagram_manage_comments"]);
    expect(st.native["facebook|es"]?.state).toBe("sin_sonda");
    expect(st.native["tiktok|es"]?.via).toBe("zernio");
    expect(st.scopes_checked_at).toBe(NOW);
    expect(st.events).toHaveLength(1);
    expect(st.events[0]?.reason).toContain("sin_permiso");
    expect(st.note).toContain("nativa");
  });
});
