// Tests del engagement SOLO por Zernio (worker/src/publish/
// zernio-engagement.ts, v5.3.0, 2026-11-23).
// Sin red real (fetch con cola), sin D1 real (bun:sqlite en memoria
// con las tablas de la migración 0040 + las que tocan getSettings,
// resolveCreds, listPostedJobs y logWorkerEvent), sin secretos
// reales: la clave Zernio es inventada ("zk_.test").
import { afterEach, describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { encryptCredential } from "../src/crypto";
import type { D1Database, Env } from "../src/cloudflare";
import { effectiveSettings, validateSettingsInput } from "../src/db";
import {
  countEngagementToday,
  engagementCaps,
  extractNativePostId,
  getEngagementStatus,
  listPendingTargets,
  pickReply,
  runZernioEngagement,
  shouldSkipComment,
} from "../src/publish/zernio-engagement";
import { SqliteStmt } from "./budget-test-db";

const KEY = Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"); // SOLO TEST
const API_KEY = "zk_.test";
const NOW = Date.UTC(2026, 10, 23, 18, 0, 0); // 23-nov 15:00 Santiago

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

function fakeEnv(d1: D1Database): Env {
  return { DB: d1, CREDENTIAL_ENCRYPTION_KEY: KEY } as unknown as Env;
}

async function seedSettings(raw: Database, settings: Record<string, unknown>): Promise<void> {
  raw.query("INSERT INTO settings (tenant_id, data, updated_at) VALUES ('sebastian', ?1, ?2)").run(JSON.stringify(settings), NOW);
}

async function seedZernioTikTok(raw: Database, language: string): Promise<void> {
  const cred = await encryptCredential(API_KEY, KEY, "sebastian");
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

const ACCOUNTS = {
  accounts: [
    { _id: "acc_yt", platform: "youtube", username: "elclipdegta6" },
    { _id: "acc_tt", platform: "tiktok", username: "elclipdegta6____bits" },
  ],
};

describe("pickReply / shouldSkipComment / extractNativePostId", () => {
  test("la respuesta varía por comentario y nunca repite la anterior en la pasada", () => {
    const a = pickReply("es", null, "c1");
    const b = pickReply("es", null, "c1", a.index);
    expect(a.text).not.toBe(b.text);
    expect(a.text.length).toBeGreaterThan(10);
    expect(a.text).not.toContain("{title}");
    const withTitle = pickReply("es", "Rubius descubre los cromos", "c1");
    expect(withTitle.text).toContain("Rubius descubre los cromos");
    const en = pickReply("en", null, "c1");
    expect(en.text).not.toBe(a.text);
  });

  test("se saltan comentarios vacíos, con enlace o demasiado largos", () => {
    expect(shouldSkipComment("")).toBeTruthy();
    expect(shouldSkipComment("mira https://example.com")).toBeTruthy();
    expect(shouldSkipComment("x".repeat(501))).toBeTruthy();
    expect(shouldSkipComment("¡Qué buen clip!")).toBeNull();
  });

  test("extractNativePostId parsea las 4 plataformas y rechaza basura", () => {
    expect(extractNativePostId("tiktok", "https://www.tiktok.com/@elclipdegta6____bits/video/7711400021152812306")).toBe("7711400021152812306");
    expect(extractNativePostId("youtube", "https://www.youtube.com/shorts/1MRFiJBjXaU")).toBe("1MRFiJBjXaU");
    expect(extractNativePostId("instagram", "https://www.instagram.com/reel/DgFpzP6iJWe/")).toBe("DgFpzP6iJWe");
    expect(extractNativePostId("facebook", "https://www.facebook.com/reel/1173076921827728/")).toBe("1173076921827728");
    expect(extractNativePostId("youtube", "https://www.youtube.com/")).toBeNull();
    expect(extractNativePostId("tiktok", "https://www.tiktok.com/@alguien")).toBeNull();
  });
});

describe("settings de engagement", () => {
  test("default: apagado, topes 10/10/5/1", () => {
    const eff = effectiveSettings({});
    expect(eff.engagement_enabled).toBe(false);
    expect(eff.engagement_reply_daily_max).toBe(10);
    expect(eff.engagement_like_daily_max).toBe(10);
    expect(eff.engagement_outward_like_daily_max).toBe(5);
    expect(eff.engagement_outward_comment_daily_max).toBe(1);
  });

  test("validateSettingsInput rechaza topes fuera de rango y enabled no booleano", () => {
    expect(validateSettingsInput({ engagement_reply_daily_max: 99 })).toBeTruthy();
    expect(validateSettingsInput({ engagement_outward_comment_daily_max: 3 })).toBeTruthy();
    expect(validateSettingsInput({ engagement_enabled: "si" })).toBeTruthy();
    expect(validateSettingsInput({ engagement_enabled: true, engagement_reply_daily_max: 10 })).toBeNull();
  });

  test("bug de tipo 2026-11-23: engagement_enabled=1 (número, como lo guardó la D1) se lee encendido", () => {
    expect(effectiveSettings({ engagement_enabled: 1 }).engagement_enabled).toBe(true);
    expect(effectiveSettings({ engagement_enabled: true }).engagement_enabled).toBe(true);
    expect(effectiveSettings({ engagement_enabled: 0 }).engagement_enabled).toBe(false);
    expect(effectiveSettings({ engagement_enabled: false }).engagement_enabled).toBe(false);
    expect(effectiveSettings({ engagement_enabled: "1" }).engagement_enabled).toBe(false);
  });

  test("modo seguro: primera pasada 2/2/0/0, tras 24 h mandan los topes configurados", () => {
    const eff = effectiveSettings({});
    const first = engagementCaps({}, eff, NOW);
    expect(first.safeMode).toBe(true);
    expect(first.reply).toBe(2);
    expect(first.like).toBe(2);
    expect(first.outwardLike).toBe(0);
    expect(first.outwardComment).toBe(0);
    const within = engagementCaps({ engagement_first_run_at: NOW - 3600_000 }, eff, NOW);
    expect(within.safeMode).toBe(true);
    expect(within.outwardLike).toBe(0);
    const after = engagementCaps({ engagement_first_run_at: NOW - 25 * 3600_000 }, eff, NOW);
    expect(after.safeMode).toBe(false);
    expect(after.reply).toBe(10);
    expect(after.like).toBe(10);
    expect(after.outwardLike).toBe(5);
    expect(after.outwardComment).toBe(1);
  });
});

describe("runZernioEngagement", () => {
  test("apagado: no llama a Zernio", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {});
    stubFetch([]);
    const s = await runZernioEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW, sleepMs: 0 });
    expect(s.ran).toBe(false);
    expect(seen).toHaveLength(0);
  });

  test("sin clave Zernio: ES y EN quedan sin_clave_zernio, sin llamar a Zernio", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, { engagement_enabled: true });
    stubFetch([]);
    const s = await runZernioEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW, sleepMs: 0 });
    expect(s.ran).toBe(true);
    expect(s.replies).toBe(0);
    expect(s.perLanguage.es).toContain("sin_clave_zernio");
    expect(s.perLanguage.en).toContain("sin_clave_zernio");
    expect(seen).toHaveLength(0);
  });

  test("responde y da like a un comentario nuevo, y no lo repite en la segunda pasada", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, { engagement_enabled: true });
    await seedZernioTikTok(raw, "es");
    raw.query("INSERT INTO clips (id, tenant_id, title, language, created_at, updated_at) VALUES (7, 'sebastian', 'Rubius descubre los cromos', 'es', ?1, ?1)").run(NOW);
    raw.query(
      "INSERT INTO jobs (tenant_id, kind, clip_id, platform, language, state, platform_id, evidence_url, created_at, updated_at) " +
        "VALUES ('sebastian', 'publish', 7, 'youtube_shorts', 'es', 'posted', '1MRFiJBjXaU', 'https://www.youtube.com/shorts/1MRFiJBjXaU', ?1, ?1)",
    ).run(NOW);
    const inboxYt = { data: [{ id: "zpost1", platform: "youtube", accountId: "acc_yt", commentCount: 1 }] };
    const thread = {
      comments: [{ id: "c1", text: "¡Qué buen clip!", from: { username: "fan123" }, replies: [] }],
    };
    // Pasada 1: cuentas, inbox yt (1 comentario), inbox tiktok/ig/fb vacíos,
    // hilo, reply, like. EN no tiene clave: sin fetch.
    stubFetch([
      { status: 200, body: ACCOUNTS },
      { status: 200, body: inboxYt },
      { status: 200, body: thread },
      { status: 200, body: {} },
      { status: 200, body: {} },
      { status: 200, body: { data: [] } },
      { status: 200, body: { data: [] } },
      { status: 200, body: { data: [] } },
    ]);
    const s1 = await runZernioEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW, sleepMs: 0, force: true });
    expect(s1.replies).toBe(1);
    expect(s1.commentLikes).toBe(1);
    const replyCall = seen.find((r) => r.method === "POST" && r.url.includes("/inbox/comments/zpost1"));
    expect(replyCall).toBeTruthy();
    expect(replyCall?.body).toContain("c1");
    expect(replyCall?.body).not.toContain("🔥🔥");
    expect(await countEngagementToday(d1, "sebastian", "2026-11-23", "youtube", "es", "reply")).toBe(1);
    // Pasada 2 (force): el mismo comentario ya está en replied: 0 respuestas.
    stubFetch([
      { status: 200, body: ACCOUNTS },
      { status: 200, body: inboxYt },
      { status: 200, body: thread },
      { status: 200, body: { data: [] } },
      { status: 200, body: { data: [] } },
      { status: 200, body: { data: [] } },
    ]);
    const s2 = await runZernioEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW + 3600_000, sleepMs: 0, force: true });
    expect(s2.replies).toBe(0);
    expect(await countEngagementToday(d1, "sebastian", "2026-11-23", "youtube", "es", "reply")).toBe(1);
  });

  test("tope diario: con reply_daily_max=1 solo responde al primer comentario", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, { engagement_enabled: true, engagement_reply_daily_max: 1 });
    await seedZernioTikTok(raw, "es");
    const inboxYt = { data: [{ id: "zpost1", platform: "youtube", accountId: "acc_yt", commentCount: 2 }] };
    const thread = {
      comments: [
        { id: "c1", text: "¡Qué buen clip!", from: { username: "fan1" }, replies: [] },
        { id: "c2", text: "¡Otro momentazo!", from: { username: "fan2" }, replies: [] },
      ],
    };
    stubFetch([
      { status: 200, body: ACCOUNTS },
      { status: 200, body: inboxYt },
      { status: 200, body: thread },
      { status: 200, body: {} },
      { status: 200, body: {} },
      { status: 200, body: { data: [] } },
      { status: 200, body: { data: [] } },
      { status: 200, body: { data: [] } },
    ]);
    const s = await runZernioEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW, sleepMs: 0, force: true });
    expect(s.replies).toBe(1);
    expect(seen.filter((r) => r.method === "POST" && r.url.includes("/inbox/comments/zpost1") && !r.url.includes("/like"))).toHaveLength(1);
  });

  test("modo seguro: en la primera pasada solo responde 2 aunque haya 3 comentarios nuevos", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, { engagement_enabled: true });
    await seedZernioTikTok(raw, "es");
    const inboxYt = { data: [{ id: "zpost1", platform: "youtube", accountId: "acc_yt", commentCount: 3 }] };
    const thread = {
      comments: [
        { id: "c1", text: "¡Qué buen clip!", from: { username: "fan1" }, replies: [] },
        { id: "c2", text: "¡Otro momentazo!", from: { username: "fan2" }, replies: [] },
        { id: "c3", text: "¡El mejor clip del día!", from: { username: "fan3" }, replies: [] },
      ],
    };
    stubFetch([
      { status: 200, body: ACCOUNTS },
      { status: 200, body: inboxYt },
      { status: 200, body: thread },
      { status: 200, body: {} },
      { status: 200, body: {} },
      { status: 200, body: {} },
      { status: 200, body: {} },
      { status: 200, body: { data: [] } },
    ]);
    const s = await runZernioEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW, sleepMs: 0, force: true });
    expect(s.replies).toBe(2);
    expect(s.skipped.some((x) => x.reason.includes("modo seguro"))).toBe(true);
    // La primera pasada queda marcada: la siguiente pasada dentro
    // de 24 h sigue en modo seguro.
    const st = await getEngagementStatus(fakeEnv(d1), "sebastian");
    expect(st.safe_mode).toBe(true);
    expect(st.safe_caps.reply).toBe(2);
    expect(st.safe_caps.outward_like).toBe(0);
  });

  test("objetivo TikTok hacia afuera se marca skipped sin llamar al like de Zernio", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, { engagement_enabled: true });
    await seedZernioTikTok(raw, "es");
    raw.query(
      "INSERT INTO engagement_targets (tenant_id, platform, language, streamer, post_url, post_id, kind, status, created_at, updated_at) " +
        "VALUES ('sebastian', 'tiktok', 'es', 'rubius', 'https://www.tiktok.com/@rubius/video/1234567890123456789', '1234567890123456789', 'like', 'pending', ?1, ?1)",
    ).run(NOW);
    stubFetch([
      { status: 200, body: ACCOUNTS },
      { status: 200, body: { data: [] } },
      { status: 200, body: { data: [] } },
      { status: 200, body: { data: [] } },
      { status: 200, body: { data: [] } },
    ]);
    const s = await runZernioEngagement(fakeEnv(d1), "sebastian", { nowMs: NOW, sleepMs: 0, force: true });
    expect(s.outwardLikes).toBe(0);
    expect(seen.some((r) => r.url.includes("/like") && !r.url.includes("comments"))).toBe(false);
    const pending = await listPendingTargets(d1, "sebastian");
    expect(pending).toHaveLength(0);
    const row = raw.query("SELECT status, reason FROM engagement_targets WHERE id = 1").get() as { status: string; reason: string };
    expect(row.status).toBe("skipped");
    expect(row.reason).toContain("tiktok_sin_like_zernio");
  });

  test("status refleja apagado, topes y objetivos pendientes", async () => {
    const { d1, raw } = makeDb();
    await seedSettings(raw, {});
    raw.query(
      "INSERT INTO engagement_targets (tenant_id, platform, language, streamer, post_url, post_id, kind, status, created_at, updated_at) " +
        "VALUES ('sebastian', 'youtube', 'es', 'rubius', 'https://www.youtube.com/shorts/1MRFiJBjXaU', '1MRFiJBjXaU', 'like', 'pending', ?1, ?1)",
    ).run(NOW);
    const st = await getEngagementStatus(fakeEnv(d1), "sebastian");
    expect(st.enabled).toBe(false);
    expect(st.reply_daily_max).toBe(10);
    expect(st.pending_targets).toBe(1);
    expect(st.note).toContain("Zernio");
  });
});
