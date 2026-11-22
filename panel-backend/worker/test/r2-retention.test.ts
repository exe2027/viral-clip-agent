// Tests de la retención 24 h del MP4 en R2 (0039, orden del usuario
// 2026-11-21 21:28; resuelve la excepción del borrado 2026-11-20
// 10:05). Sin red, sin D1 real: SQLite en memoria (bun:sqlite) con
// el mismo patrón que budget-test-db.ts. Cubren: (a) al completarse
// todas las plataformas se fija el plazo 24 h (vía markPosted, el
// gatillo real, y vía stampRetentionDeadline); (b) antes de las 24 h
// NO se borra; (c) tras 24 h sin repub pendiente SÍ se borra de R2;
// (d) con repub pendiente (takedown) NO se borra aunque pasen 24 h;
// (e) el borrado no toca D1 (clip/jobs/links intactos). Más:
// desglose por plataforma, resumen de retención, backfill para
// completados por WAL/manual, y degradación sin la columna 0039.
import { describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import type { D1Database, Env, R2Bucket } from "../src/cloudflare";
import { createSessionToken } from "../src/auth";
import worker from "../src/index";
import { SqliteStmt } from "./budget-test-db";
import {
  backfillRetentionDeadlines,
  clipPublishBreakdown,
  clipRetentionSummary,
  R2_RETENTION_MS,
  stampRetentionDeadline,
  sweepExpiredRetentions,
  type PublishBreakdownEntry,
} from "../src/r2-retention";
import { markPosted, type JobRow } from "../src/jobs";
import type { ClipRow } from "../src/db";

const NOW = 1795300000000; // 2026-11-21 ~21:06 -03, fijo para los plazos
const KEY = "clips/sebastian/1/abc123_clip.mp4";

function makeRetentionDb(): { d1: D1Database; raw: Database } {
  const raw = new Database(":memory:");
  raw.exec(
    "CREATE TABLE clips (" +
      "id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'en_carpeta', " +
      "title TEXT NOT NULL DEFAULT '', platforms TEXT NOT NULL DEFAULT '[]', publish_states TEXT NOT NULL DEFAULT '{}', " +
      "published_at INTEGER, video_r2_key TEXT NOT NULL DEFAULT '', file_deleted INTEGER NOT NULL DEFAULT 0, " +
      "r2_delete_after INTEGER, archived INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)",
  );
  raw.exec(
    "CREATE TABLE jobs (" +
      "id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'publish', clip_id INTEGER, " +
      "platform TEXT, api_account_id INTEGER, state TEXT NOT NULL, idempotency_key TEXT, resume_data TEXT, " +
      "platform_id TEXT, evidence_url TEXT, title TEXT NOT NULL DEFAULT '', caption TEXT NOT NULL DEFAULT '', " +
      "r2_key TEXT, language TEXT NOT NULL DEFAULT 'es', posted_day TEXT, attempts INTEGER NOT NULL DEFAULT 0, " +
      "last_error TEXT, retryable INTEGER NOT NULL DEFAULT 0, next_run_at INTEGER, " +
      "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, claimed_by TEXT, run_token TEXT, slot_day TEXT, manual INTEGER)",
  );
  raw.exec(
    "CREATE TABLE clip_events (" +
      "id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER NOT NULL, tenant_id TEXT NOT NULL, " +
      "at INTEGER NOT NULL, step TEXT NOT NULL, actor TEXT NOT NULL, message TEXT NOT NULL, failed INTEGER NOT NULL DEFAULT 0)",
  );
  raw.exec(
    "CREATE TABLE worker_events (" +
      "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, at INTEGER NOT NULL, " +
      "kind TEXT NOT NULL, message TEXT NOT NULL)",
  );
  raw.exec(
    "CREATE TABLE event_counters (" +
      "tenant_id TEXT NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, " +
      "window_start INTEGER NOT NULL, count INTEGER NOT NULL, first_at INTEGER NOT NULL, last_at INTEGER NOT NULL, " +
      "PRIMARY KEY (tenant_id, source, kind, message, window_start))",
  );
  const d1 = { prepare: (sql: string) => new SqliteStmt(raw, sql) } as unknown as D1Database;
  return { d1, raw };
}

function insertClip(
  raw: Database,
  over: Partial<{ id: number; status: string; platforms: string; video_r2_key: string; file_deleted: number; r2_delete_after: number | null; published_at: number | null }> = {},
): number {
  const id = over.id ?? 1;
  raw
    .query(
      "INSERT INTO clips (id, tenant_id, status, title, platforms, publish_states, published_at, video_r2_key, file_deleted, r2_delete_after, archived, created_at, updated_at) " +
        "VALUES (?, 'sebastian', ?, 'clip', ?, '{}', ?, ?, ?, ?, 0, ?, ?)",
    )
    .run(
      id,
      over.status ?? "en_carpeta",
      over.platforms ?? '["youtube_shorts","tiktok"]',
      over.published_at ?? null,
      over.video_r2_key ?? KEY,
      over.file_deleted ?? 0,
      over.r2_delete_after ?? null,
      NOW,
      NOW,
    );
  return id;
}

function insertJob(
  raw: Database,
  over: Partial<{ id: number; clip_id: number; platform: string; state: string; evidence_url: string | null }> = {},
): number {
  const id = over.id ?? 1;
  raw
    .query(
      "INSERT INTO jobs (id, tenant_id, kind, clip_id, platform, api_account_id, state, evidence_url, title, caption, language, created_at, updated_at) " +
        "VALUES (?, 'sebastian', 'publish', ?, ?, NULL, ?, ?, 't', '', 'es', ?, ?)",
    )
    .run(id, over.clip_id ?? 1, over.platform ?? "youtube_shorts", over.state ?? "scheduled", over.evidence_url ?? null, NOW, NOW);
  return id;
}

function clipRow(raw: Database, id = 1): Record<string, unknown> {
  return raw.query("SELECT * FROM clips WHERE id = ?").get(id) as Record<string, unknown>;
}

function jobRow(raw: Database, id = 1): Record<string, unknown> {
  return raw.query("SELECT * FROM jobs WHERE id = ?").get(id) as Record<string, unknown>;
}

/** R2 falso: solo registra los delete (el borrado es el efecto). */
function makeFakeR2(): { bucket: R2Bucket; deleted: string[] } {
  const deleted: string[] = [];
  const bucket = {
    delete: async (key: string) => {
      deleted.push(key);
    },
  } as unknown as R2Bucket;
  return { bucket, deleted };
}

describe("retención 24 h del MP4 en R2 (0039)", () => {
  test("(a) markPosted fija el plazo 24 h cuando el posted completa todas las plataformas", async () => {
    const { d1, raw } = makeRetentionDb();
    insertClip(raw, { id: 1, platforms: '["youtube_shorts"]', status: "en_carpeta" });
    insertJob(raw, { id: 1, clip_id: 1, platform: "youtube_shorts", state: "scheduled" });
    const job = jobRow(raw, 1) as unknown as JobRow;
    await markPosted(d1, job, "vid123", "https://www.youtube.com/shorts/abc123");
    expect(jobRow(raw, 1).state).toBe("posted");
    const after = clipRow(raw, 1);
    expect(after.status).toBe("publicado");
    expect(typeof after.r2_delete_after).toBe("number");
    // El plazo es ahora + 24 h (con tolerancia de 60 s por el reloj).
    expect(Math.abs(Number(after.r2_delete_after) - (Date.now() + R2_RETENTION_MS))).toBeLessThan(60_000);
    expect(after.file_deleted).toBe(0);
  });

  test("(a) stampRetentionDeadline fija el plazo solo si todas publicaron y ninguna falta", async () => {
    const { d1, raw } = makeRetentionDb();
    insertClip(raw, { id: 1 });
    insertJob(raw, { id: 1, clip_id: 1, platform: "youtube_shorts", state: "posted", evidence_url: "https://www.youtube.com/shorts/aaa" });
    insertJob(raw, { id: 2, clip_id: 1, platform: "tiktok", state: "posted", evidence_url: "https://www.tiktok.com/@x/video/1" });
    expect(await stampRetentionDeadline(d1, 1, "sebastian", NOW)).toBe(true);
    expect(clipRow(raw, 1).r2_delete_after).toBe(NOW + R2_RETENTION_MS);
    // Segunda llamada NO reinicia el plazo (se fija una sola vez).
    expect(await stampRetentionDeadline(d1, 1, "sebastian", NOW + 3_600_000)).toBe(false);
    expect(clipRow(raw, 1).r2_delete_after).toBe(NOW + R2_RETENTION_MS);
  });

  test("(a) con un job pendiente NO se fija el plazo", async () => {
    const { d1, raw } = makeRetentionDb();
    insertClip(raw, { id: 1 });
    insertJob(raw, { id: 1, clip_id: 1, platform: "youtube_shorts", state: "posted", evidence_url: "https://www.youtube.com/shorts/aaa" });
    insertJob(raw, { id: 2, clip_id: 1, platform: "tiktok", state: "scheduled" });
    expect(await stampRetentionDeadline(d1, 1, "sebastian", NOW)).toBe(false);
    expect(clipRow(raw, 1).r2_delete_after).toBeNull();
  });

  test("(a) sin ningún posted NO se fija el plazo", async () => {
    const { d1, raw } = makeRetentionDb();
    insertClip(raw, { id: 1 });
    insertJob(raw, { id: 1, clip_id: 1, platform: "youtube_shorts", state: "scheduled" });
    expect(await stampRetentionDeadline(d1, 1, "sebastian", NOW)).toBe(false);
    expect(clipRow(raw, 1).r2_delete_after).toBeNull();
  });

  test("(b) antes de las 24 h NO se borra", async () => {
    const { d1, raw } = makeRetentionDb();
    insertClip(raw, { id: 1, status: "publicado", r2_delete_after: NOW + 3_600_000 });
    insertJob(raw, { id: 1, clip_id: 1, platform: "youtube_shorts", state: "posted", evidence_url: "https://www.youtube.com/shorts/aaa" });
    insertJob(raw, { id: 2, clip_id: 1, platform: "tiktok", state: "posted", evidence_url: "https://www.tiktok.com/@x/video/1" });
    const { bucket, deleted } = makeFakeR2();
    const res = await sweepExpiredRetentions({ DB: d1, CLIPS_BUCKET: bucket }, "sebastian", NOW);
    expect(res).toEqual({ checked: 0, deleted: 0, skipped: 0, errors: [] });
    expect(deleted).toEqual([]);
    expect(clipRow(raw, 1).file_deleted).toBe(0);
  });

  test("(c) tras 24 h sin repub pendiente SÍ se borra de R2", async () => {
    const { d1, raw } = makeRetentionDb();
    insertClip(raw, { id: 1, status: "publicado", r2_delete_after: NOW - 1 });
    insertJob(raw, { id: 1, clip_id: 1, platform: "youtube_shorts", state: "posted", evidence_url: "https://www.youtube.com/shorts/aaa" });
    insertJob(raw, { id: 2, clip_id: 1, platform: "tiktok", state: "posted", evidence_url: "https://www.tiktok.com/@x/video/1" });
    const { bucket, deleted } = makeFakeR2();
    const res = await sweepExpiredRetentions({ DB: d1, CLIPS_BUCKET: bucket }, "sebastian", NOW);
    expect(res.checked).toBe(1);
    expect(res.deleted).toBe(1);
    expect(res.skipped).toBe(0);
    expect(res.errors).toEqual([]);
    expect(deleted).toEqual([KEY]);
    expect(clipRow(raw, 1).file_deleted).toBe(1);
  });

  test("(d) con repub pendiente (takedown) NO se borra aunque pasen 24 h", async () => {
    const { d1, raw } = makeRetentionDb();
    insertClip(raw, { id: 1, status: "publicado", r2_delete_after: NOW - 1 });
    insertJob(raw, { id: 1, clip_id: 1, platform: "youtube_shorts", state: "posted", evidence_url: "https://www.youtube.com/shorts/aaa" });
    // El |repubN del vigilante: mismo clip+plataforma ya publicado,
    // otro job pendiente esperando re-subida.
    insertJob(raw, { id: 2, clip_id: 1, platform: "youtube_shorts", state: "scheduled" });
    insertJob(raw, { id: 3, clip_id: 1, platform: "tiktok", state: "posted", evidence_url: "https://www.tiktok.com/@x/video/1" });
    const { bucket, deleted } = makeFakeR2();
    const res = await sweepExpiredRetentions({ DB: d1, CLIPS_BUCKET: bucket }, "sebastian", NOW);
    expect(res.deleted).toBe(0);
    expect(res.skipped).toBe(1);
    expect(deleted).toEqual([]);
    expect(clipRow(raw, 1).file_deleted).toBe(0);
  });

  test("(e) el borrado no toca D1: clip, jobs y links intactos", async () => {
    const { d1, raw } = makeRetentionDb();
    insertClip(raw, { id: 1, status: "publicado", r2_delete_after: NOW - 1 });
    insertJob(raw, { id: 1, clip_id: 1, platform: "youtube_shorts", state: "posted", evidence_url: "https://www.youtube.com/shorts/aaa" });
    insertJob(raw, { id: 2, clip_id: 1, platform: "tiktok", state: "posted", evidence_url: "https://www.tiktok.com/@x/video/1" });
    const { bucket } = makeFakeR2();
    await sweepExpiredRetentions({ DB: d1, CLIPS_BUCKET: bucket }, "sebastian", NOW);
    const clip = clipRow(raw, 1);
    expect(clip.id).toBe(1);
    expect(clip.title).toBe("clip");
    expect(clip.status).toBe("publicado");
    expect(clip.platforms).toBe('["youtube_shorts","tiktok"]');
    expect(clip.video_r2_key).toBe(KEY);
    expect(clip.file_deleted).toBe(1);
    // Solo file_deleted cambió: los jobs y sus links siguen ahí.
    expect(jobRow(raw, 1).state).toBe("posted");
    expect(jobRow(raw, 1).evidence_url).toBe("https://www.youtube.com/shorts/aaa");
    expect(jobRow(raw, 2).state).toBe("posted");
    expect(jobRow(raw, 2).evidence_url).toBe("https://www.tiktok.com/@x/video/1");
    // Y no se borra dos veces: el segundo barrido no encuentra nada.
    const res2 = await sweepExpiredRetentions({ DB: d1, CLIPS_BUCKET: bucket }, "sebastian", NOW);
    expect(res2.checked).toBe(0);
    expect(res2.deleted).toBe(0);
  });

  test("backfill fija el plazo a completados sin plazo (WAL / reconciliación manual)", async () => {
    const { d1, raw } = makeRetentionDb();
    insertClip(raw, { id: 1, status: "publicado", r2_delete_after: null });
    insertJob(raw, { id: 1, clip_id: 1, platform: "youtube_shorts", state: "posted", evidence_url: "https://www.youtube.com/shorts/aaa" });
    insertJob(raw, { id: 2, clip_id: 1, platform: "tiktok", state: "posted", evidence_url: "https://www.tiktok.com/@x/video/1" });
    expect(await backfillRetentionDeadlines(d1, "sebastian", NOW)).toBe(1);
    expect(clipRow(raw, 1).r2_delete_after).toBe(NOW + R2_RETENTION_MS);
  });

  test("desglose por plataforma: dónde se subió, dónde falta, esperando re-subida", async () => {
    const { d1, raw } = makeRetentionDb();
    insertClip(raw, { id: 1, platforms: '["youtube_shorts","tiktok","instagram_reels"]' });
    insertJob(raw, { id: 1, clip_id: 1, platform: "youtube_shorts", state: "posted", evidence_url: "https://www.youtube.com/shorts/aaa" });
    insertJob(raw, { id: 2, clip_id: 1, platform: "tiktok", state: "scheduled" });
    insertJob(raw, { id: 3, clip_id: 1, platform: "instagram_reels", state: "failed" });
    const bd = await clipPublishBreakdown(d1, 1, "sebastian");
    const byPlat = new Map(bd.map((b) => [b.platform, b]));
    expect(byPlat.get("youtube_shorts")).toMatchObject({ state: "posted", evidence_url: "https://www.youtube.com/shorts/aaa", awaiting_reupload: false });
    expect(byPlat.get("tiktok")).toMatchObject({ state: "pending", evidence_url: null, awaiting_reupload: false });
    expect(byPlat.get("instagram_reels")).toMatchObject({ state: "failed", awaiting_reupload: false });

    // Takedown: YT publicado + otro YT pendiente = esperando re-subida.
    insertJob(raw, { id: 4, clip_id: 1, platform: "youtube_shorts", state: "draft" });
    const bd2 = await clipPublishBreakdown(d1, 1, "sebastian");
    const yt = bd2.find((b) => b.platform === "youtube_shorts") as PublishBreakdownEntry;
    expect(yt.state).toBe("posted");
    expect(yt.evidence_url).toBe("https://www.youtube.com/shorts/aaa");
    expect(yt.awaiting_reupload).toBe(true);
  });

  test("resumen de retención: subidos / borrado / sin_mp4 / en_cola", () => {
    const base: ClipRow = {
      id: 1, tenant_id: "sebastian", source_id: null, influencer_id: null, title: "t", start_sec: 0, end_sec: 40,
      score: 80, status: "publicado", subtitle_style: "tiktok", seo_title: "", seo_description: "", seo_tags: "",
      platforms: '["youtube_shorts"]', scheduled_for: null, published_at: NOW, file_deleted: 0,
      created_at: NOW, updated_at: NOW, captions: "{}", viral: "{}", seo_overrides: "[]", preview_seen: 1,
      archived: 0, origin: "vod", publish_states: "{}", game: "GTA VI", publish_blocked: 0,
      needs_human_transcription: 0, subtitles_complete: 1, video_r2_key: KEY, r2_delete_after: NOW + R2_RETENTION_MS,
    } as ClipRow;
    const posted: PublishBreakdownEntry[] = [{ platform: "youtube_shorts", state: "posted", evidence_url: "https://www.youtube.com/shorts/aaa", job_state: "posted", awaiting_reupload: false }];
    expect(clipRetentionSummary(base, posted)).toMatchObject({
      state: "subidos", delete_after: NOW + R2_RETENTION_MS, awaiting_reupload: false, uploaded: ["youtube_shorts"], pending: [],
    });
    expect(clipRetentionSummary({ ...base, file_deleted: 1 }, posted).state).toBe("borrado");
    expect(clipRetentionSummary({ ...base, video_r2_key: "" }, posted).state).toBe("sin_mp4");
    expect(clipRetentionSummary({ ...base, status: "en_carpeta" }, [{ ...posted[0], state: "pending", evidence_url: null, job_state: "scheduled" }]).state).toBe("en_cola");
    expect(clipRetentionSummary(base, [{ ...posted[0], awaiting_reupload: true }]).awaiting_reupload).toBe(true);
  });

  test("sin la columna 0039 la retención degrada sin tumbar nada", async () => {
    const raw = new Database(":memory:");
    raw.exec(
      "CREATE TABLE clips (" +
        "id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'en_carpeta', " +
        "title TEXT NOT NULL DEFAULT '', platforms TEXT NOT NULL DEFAULT '[]', publish_states TEXT NOT NULL DEFAULT '{}', " +
        "published_at INTEGER, video_r2_key TEXT NOT NULL DEFAULT '', file_deleted INTEGER NOT NULL DEFAULT 0, " +
        "archived INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)",
    );
    raw.exec(
      "CREATE TABLE jobs (" +
        "id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'publish', clip_id INTEGER, " +
        "platform TEXT, state TEXT NOT NULL, evidence_url TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)",
    );
    const d1 = { prepare: (sql: string) => new SqliteStmt(raw, sql) } as unknown as D1Database;
    raw.query("INSERT INTO clips (id, tenant_id, status, platforms, video_r2_key, created_at, updated_at) VALUES (1, 'sebastian', 'publicado', '[\"youtube_shorts\"]', ?, ?, ?)").run(KEY, NOW, NOW);
    raw.query("INSERT INTO jobs (id, tenant_id, kind, clip_id, platform, state, evidence_url, created_at, updated_at) VALUES (1, 'sebastian', 'publish', 1, 'youtube_shorts', 'posted', 'https://www.youtube.com/shorts/aaa', ?, ?)").run(NOW, NOW);
    expect(await stampRetentionDeadline(d1, 1, "sebastian", NOW)).toBe(false);
    const { bucket, deleted } = makeFakeR2();
    const res = await sweepExpiredRetentions({ DB: d1, CLIPS_BUCKET: bucket }, "sebastian", NOW);
    expect(res.deleted).toBe(0);
    expect(deleted).toEqual([]);
  });
});

// --- Contrato de API que consume el panel (Editor + sección "Subidos") ---
// El handler de verdad (worker.fetch) contra SQLite real: prueba el
// cableado, no solo las funciones. La sesión es un Bearer con el
// token de sesión (mismo patrón que endpoints-nuevos-2.test.ts).
const TEST_SECRET = Buffer.from("cccccccccccccccccccccccccccccccc").toString("base64"); // SOLO TEST

function makeTestEnv(d1: D1Database): Env {
  return {
    DB: d1,
    TENANT: "sebastian",
    CLIPS_BUCKET: {} as Env["CLIPS_BUCKET"],
    SESSION_SECRET: TEST_SECRET,
    CREDENTIAL_ENCRYPTION_KEY: Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"),
    BRIDGE_TOKEN: "test-bridge",
    GITHUB_TOKEN: "test-gh",
    ALLOWED_EMAIL: "",
    GITHUB_REPO: "exe2027/viral-clip-agent",
  } as Env;
}

async function callApi(env: Env, method: string, path: string): Promise<{ status: number; json: Record<string, unknown> }> {
  const token = await createSessionToken("sebastian", "sebastian", TEST_SECRET);
  const res = await worker.fetch(
    new Request(`https://worker.test${path}`, { method, headers: { Authorization: `Bearer ${token}` } }),
    env,
    { waitUntil: () => {}, passThroughOnException: () => {} } as never,
  );
  return { status: res.status, json: (await res.json()) as Record<string, unknown> };
}

describe("GET /api/clips/:id y ?status=publicado (contrato del panel)", () => {
  test("GET /api/clips/:id devuelve publish_breakdown + retention", async () => {
    const { d1, raw } = makeRetentionDb();
    insertClip(raw, { id: 1, status: "publicado", r2_delete_after: NOW + R2_RETENTION_MS, published_at: NOW });
    insertJob(raw, { id: 1, clip_id: 1, platform: "youtube_shorts", state: "posted", evidence_url: "https://www.youtube.com/shorts/aaa" });
    insertJob(raw, { id: 2, clip_id: 1, platform: "tiktok", state: "scheduled" });
    const env = makeTestEnv(d1);
    const res = await callApi(env, "GET", "/api/clips/1");
    expect(res.status).toBe(200);
    expect(res.json.ok).toBe(true);
    const bd = res.json.publish_breakdown as PublishBreakdownEntry[];
    expect(bd).toHaveLength(2);
    expect(bd.find((b) => b.platform === "youtube_shorts")).toMatchObject({ state: "posted", evidence_url: "https://www.youtube.com/shorts/aaa" });
    expect(bd.find((b) => b.platform === "tiktok")).toMatchObject({ state: "pending" });
    expect(res.json.retention).toMatchObject({
      state: "subidos", delete_after: NOW + R2_RETENTION_MS, uploaded: ["youtube_shorts"], pending: ["tiktok"],
    });
  });

  test("GET /api/clips?status=publicado lista solo los publicados (la Cola no)", async () => {
    const { d1, raw } = makeRetentionDb();
    insertClip(raw, { id: 1, status: "publicado", r2_delete_after: NOW + R2_RETENTION_MS, published_at: NOW });
    insertClip(raw, { id: 2, status: "en_carpeta", video_r2_key: "" });
    const env = makeTestEnv(d1);
    const pub = await callApi(env, "GET", "/api/clips?status=publicado");
    expect(pub.status).toBe(200);
    expect((pub.json.clips as Array<{ id: number }>).map((c) => c.id)).toEqual([1]);
    const cola = await callApi(env, "GET", "/api/clips");
    expect(cola.status).toBe(200);
    expect((cola.json.clips as Array<{ id: number }>).map((c) => c.id)).toEqual([2]);
  });
});
