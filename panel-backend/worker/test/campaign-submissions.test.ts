// Tests de campaign_submissions (0041, 2026-11-23): registro +
// cola en nube de envíos a Content Rewards. Contra SQLite REAL
// (bun:sqlite, mismo motor que D1): la migración 0041 se aplica
// desde el archivo, y las rutas se prueban con el handler de verdad
// (worker.fetch) + sesión Bearer <redacted>. Sin red, sin D1 real.
// Cubren: UNIQUE (un post = una campaña, una vez), lectura por
// clip/plataforma/campaña, POST 409 en duplicado, PATCH submitted
// terminal, el disparador markPosted (rellena post_url +
// published_at_ms REALES), expiración a 30 min y la cola de
// pendientes (backfill de publicados por WAL incluido).
import { describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { D1Database, Env } from "../src/cloudflare";
import { createSessionToken } from "../src/auth";
import worker from "../src/index";
import { SqliteStmt } from "./budget-test-db";
import {
  CAMPAIGN_SUBMIT_WINDOW_MS,
  backfillCampaignSubmissionsPosted,
  createCampaignSubmission,
  expireStaleSubmissions,
  findCampaignSubmission,
  getCampaignSubmission,
  listCampaignSubmissions,
  listPendingCampaignSubmissions,
  stampCampaignSubmissionPosted,
  updateCampaignSubmission,
} from "../src/db";
import { markPosted, type JobRow } from "../src/jobs";

const NOW = 1795300000000; // 2026-11-21 ~21:06 -03, fijo
const SECRET = Buffer.from("dddddddddddddddddddddddddddddddd").toString("base64"); // SOLO TEST
const CAMPAIGN = "051002dd-5c46-43d0-a446-43f2957ba3a9"; // Dasgasd3m
const CAMPAIGN_2 = "6e9ca548-f36c-42c2-abc0-61f89b4b53ba"; // ArthurTV

function makeDb(): { d1: D1Database; raw: Database } {
  const raw = new Database(":memory:");
  raw.exec(readFileSync(join(import.meta.dir, "..", "..", "migrations", "0041_campaign_submissions.sql"), "utf8"));
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

function insertClip(raw: Database, id = 1): void {
  raw
    .query(
      "INSERT INTO clips (id, tenant_id, status, title, platforms, publish_states, published_at, video_r2_key, file_deleted, r2_delete_after, archived, created_at, updated_at) " +
        "VALUES (?, 'sebastian', 'en_carpeta', 'clip', '[\"tiktok\"]', '{}', NULL, '', 0, NULL, 0, ?, ?)",
    )
    .run(id, NOW, NOW);
}

function insertJob(raw: Database, over: { id?: number; clip_id?: number; platform?: string; state?: string; evidence_url?: string | null; updated_at?: number } = {}): number {
  const id = over.id ?? 1;
  raw
    .query(
      "INSERT INTO jobs (id, tenant_id, kind, clip_id, platform, api_account_id, state, evidence_url, title, caption, language, created_at, updated_at) " +
        "VALUES (?, 'sebastian', 'publish', ?, ?, NULL, ?, ?, 't', '', 'es', ?, ?)",
    )
    .run(id, over.clip_id ?? 1, over.platform ?? "tiktok", over.state ?? "scheduled", over.evidence_url ?? null, NOW, over.updated_at ?? NOW);
  return id;
}

function makeTestEnv(d1: D1Database): Env {
  return {
    DB: d1,
    TENANT: "sebastian",
    CLIPS_BUCKET: {} as Env["CLIPS_BUCKET"],
    SESSION_SECRET: SECRET,
    CREDENTIAL_ENCRYPTION_KEY: Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"),
    BRIDGE_TOKEN: "bridge-test",
    GITHUB_TOKEN: "gh-test",
    ALLOWED_EMAIL: "",
    GITHUB_REPO: "exe2027/viral-clip-agent",
  } as Env;
}

async function callApi(env: Env, method: string, path: string, body?: Record<string, unknown>): Promise<{ status: number; json: Record<string, unknown> }> {
  const token = await createSessionToken("sebastian", "sebastian", SECRET);
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await worker.fetch(
    new Request(`https://worker.test${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
    env,
    { waitUntil: () => {}, passThroughOnException: () => {} } as never,
  );
  return { status: res.status, json: (await res.json()) as Record<string, unknown> };
}

describe("migración 0041 + helpers", () => {
  test("la tabla existe con UNIQUE (tenant, clip, plataforma, campaña)", async () => {
    const { d1 } = makeDb();
    const { row, duplicate } = await createCampaignSubmission(d1, "sebastian", {
      clip_id: 174, platform: "tiktok", campaign_id: CAMPAIGN, campaign_name: "Dasgasd3m",
      streamer: "dasgasd3m", gross_rate_per_1k: 2.0, net_rate_per_1k: 1.8,
    });
    expect(duplicate).toBe(false);
    expect(row.status).toBe("pending");
    expect(row.published_at_ms).toBeNull();
    // Segundo registro del mismo envío: duplicado, no error, misma fila.
    const again = await createCampaignSubmission(d1, "sebastian", {
      clip_id: 174, platform: "tiktok", campaign_id: CAMPAIGN,
    });
    expect(again.duplicate).toBe(true);
    expect(again.row.id).toBe(row.id);
    expect((await listCampaignSubmissions(d1, "sebastian", { clipId: 174 })).length).toBe(1);
  });

  test("misma campaña en otra plataforma u otro tenant NO es duplicado", async () => {
    const { d1 } = makeDb();
    await createCampaignSubmission(d1, "sebastian", { clip_id: 174, platform: "tiktok", campaign_id: CAMPAIGN });
    await createCampaignSubmission(d1, "sebastian", { clip_id: 174, platform: "youtube", campaign_id: CAMPAIGN });
    await createCampaignSubmission(d1, "kaez", { clip_id: 174, platform: "tiktok", campaign_id: CAMPAIGN });
    expect((await listCampaignSubmissions(d1, "sebastian", { clipId: 174 })).length).toBe(2);
    expect((await listCampaignSubmissions(d1, "kaez", { clipId: 174 })).length).toBe(1);
    // Otra campaña para el mismo clip+plataforma tampoco es duplicado.
    await createCampaignSubmission(d1, "sebastian", { clip_id: 174, platform: "tiktok", campaign_id: CAMPAIGN_2 });
    expect((await listCampaignSubmissions(d1, "sebastian", { clipId: 174, platform: "tiktok" })).length).toBe(2);
  });

  test("find por tenant+clip+plataforma+campaña (la puerta anti-duplicado)", async () => {
    const { d1 } = makeDb();
    await createCampaignSubmission(d1, "sebastian", { clip_id: 174, platform: "tiktok", campaign_id: CAMPAIGN });
    expect((await findCampaignSubmission(d1, "sebastian", 174, "tiktok", CAMPAIGN))?.status).toBe("pending");
    expect(await findCampaignSubmission(d1, "sebastian", 174, "youtube", CAMPAIGN)).toBeNull();
    expect(await findCampaignSubmission(d1, "kaez", 174, "tiktok", CAMPAIGN)).toBeNull();
  });

  test("PATCH a submitted es terminal: no se puede reescribir", async () => {
    const { d1 } = makeDb();
    const { row } = await createCampaignSubmission(d1, "sebastian", { clip_id: 174, platform: "tiktok", campaign_id: CAMPAIGN });
    const sent = await updateCampaignSubmission(d1, "sebastian", row.id, { status: "submitted", submission_id: "sub_123" });
    expect(sent?.status).toBe("submitted");
    expect(sent?.submission_id).toBe("sub_123");
    expect(await updateCampaignSubmission(d1, "sebastian", row.id, { status: "failed" })).toBeNull();
    expect((await getCampaignSubmission(d1, row.id, "sebastian"))?.status).toBe("submitted");
  });

  test("expiración: pending con published_at_ms hace >30 min -> expired; dentro de ventana, sigue", async () => {
    const { d1 } = makeDb();
    const old = await createCampaignSubmission(d1, "sebastian", {
      clip_id: 174, platform: "tiktok", campaign_id: CAMPAIGN, published_at_ms: NOW - CAMPAIGN_SUBMIT_WINDOW_MS - 1000, post_url: "https://www.tiktok.com/@x/video/1",
    });
    const fresh = await createCampaignSubmission(d1, "sebastian", {
      clip_id: 175, platform: "tiktok", campaign_id: CAMPAIGN, published_at_ms: NOW - 1000, post_url: "https://www.tiktok.com/@x/video/2",
    });
    const noPub = await createCampaignSubmission(d1, "sebastian", { clip_id: 176, platform: "tiktok", campaign_id: CAMPAIGN });
    expect(await expireStaleSubmissions(d1, "sebastian", NOW)).toBe(1);
    expect((await getCampaignSubmission(d1, old.row.id, "sebastian"))?.status).toBe("expired");
    expect((await getCampaignSubmission(d1, fresh.row.id, "sebastian"))?.status).toBe("pending");
    expect((await getCampaignSubmission(d1, noPub.row.id, "sebastian"))?.status).toBe("pending");
  });
});

describe("disparador: markPosted rellena la fila pending con datos REALES", () => {
  test("markPosted estampa post_url + published_at_ms (el mismo de jobs.updated_at)", async () => {
    const { d1, raw } = makeDb();
    insertClip(raw, 1);
    insertJob(raw, { id: 1, clip_id: 1, platform: "tiktok", state: "scheduled" });
    await createCampaignSubmission(d1, "sebastian", { clip_id: 1, platform: "tiktok", campaign_id: CAMPAIGN });
    const job = raw.query("SELECT * FROM jobs WHERE id = 1").get() as unknown as JobRow;
    const before = Date.now();
    await markPosted(d1, job, "vid123", "https://www.tiktok.com/@elclipdegta6/video/1");
    const after = Date.now();
    expect(raw.query("SELECT state FROM jobs WHERE id = 1").get() as { state: string }).toEqual({ state: "posted" });
    const row = await findCampaignSubmission(d1, "sebastian", 1, "tiktok", CAMPAIGN);
    expect(row?.post_url).toBe("https://www.tiktok.com/@elclipdegta6/video/1");
    expect(typeof row?.published_at_ms).toBe("number");
    expect(Number(row?.published_at_ms)).toBeGreaterThanOrEqual(before);
    expect(Number(row?.published_at_ms)).toBeLessThanOrEqual(after);
    // Segunda llamada no lo pisa (published_at_ms ya no es NULL).
    expect(await stampCampaignSubmissionPosted(d1, "sebastian", 1, "tiktok", "https://otro.example/2", Date.now())).toBe(0);
    expect((await findCampaignSubmission(d1, "sebastian", 1, "tiktok", CAMPAIGN))?.post_url).toBe(
      "https://www.tiktok.com/@elclipdegta6/video/1",
    );
  });

  test("backfill: publicado por WAL (UPDATE directo) también rellena la fila en la lectura", async () => {
    const { d1, raw } = makeDb();
    await createCampaignSubmission(d1, "sebastian", { clip_id: 1, platform: "tiktok", campaign_id: CAMPAIGN });
    insertJob(raw, { id: 1, clip_id: 1, platform: "tiktok", state: "posted", evidence_url: "https://www.tiktok.com/@x/video/9", updated_at: NOW - 5000 });
    expect(await backfillCampaignSubmissionsPosted(d1, "sebastian")).toBe(1);
    const row = await findCampaignSubmission(d1, "sebastian", 1, "tiktok", CAMPAIGN);
    expect(row?.post_url).toBe("https://www.tiktok.com/@x/video/9");
    expect(row?.published_at_ms).toBe(NOW - 5000);
  });

  test("cola en nube: pendientes publicadas, las más urgentes primero; lo vencido no sale", async () => {
    const { d1 } = makeDb();
    await createCampaignSubmission(d1, "sebastian", { clip_id: 1, platform: "tiktok", campaign_id: CAMPAIGN, published_at_ms: NOW - 20 * 60 * 1000, post_url: "https://www.tiktok.com/@x/video/1" });
    await createCampaignSubmission(d1, "sebastian", { clip_id: 2, platform: "tiktok", campaign_id: CAMPAIGN, published_at_ms: NOW - 5 * 60 * 1000, post_url: "https://www.tiktok.com/@x/video/2" });
    await createCampaignSubmission(d1, "sebastian", { clip_id: 3, platform: "tiktok", campaign_id: CAMPAIGN, published_at_ms: NOW - 40 * 60 * 1000, post_url: "https://www.tiktok.com/@x/video/3" });
    await createCampaignSubmission(d1, "sebastian", { clip_id: 4, platform: "tiktok", campaign_id: CAMPAIGN });
    const pending = await listPendingCampaignSubmissions(d1, "sebastian", NOW);
    expect(pending.map((r) => r.clip_id)).toEqual([1, 2]);
    const expired = (await listCampaignSubmissions(d1, "sebastian", { status: "expired" })).map((r) => r.clip_id);
    expect(expired).toEqual([3]);
  });
});

describe("rutas /api/campaign-submissions (con sesión)", () => {
  test("GET ?clip_id lista las filas del clip; sin sesión, 401", async () => {
    const { d1, raw } = makeDb();
    insertClip(raw, 174);
    await createCampaignSubmission(d1, "sebastian", { clip_id: 174, platform: "tiktok", campaign_id: CAMPAIGN });
    const env = makeTestEnv(d1);
    const res = await callApi(env, "GET", "/api/campaign-submissions?clip_id=174");
    expect(res.status).toBe(200);
    expect((res.json.submissions as Array<{ clip_id: number }>).map((s) => s.clip_id)).toEqual([174]);
    const noAuth = await worker.fetch(
      new Request("https://worker.test/api/campaign-submissions?clip_id=174", { method: "GET" }),
      env,
      { waitUntil: () => {}, passThroughOnException: () => {} } as never,
    );
    expect(noAuth.status).toBe(401);
  });

  test("POST crea en pending (201); duplicado, 409 con la fila; clip inexistente, 404", async () => {
    const { d1, raw } = makeDb();
    insertClip(raw, 174);
    const env = makeTestEnv(d1);
    const created = await callApi(env, "POST", "/api/campaign-submissions", {
      clip_id: 174, platform: "tiktok", campaign_id: CAMPAIGN, campaign_name: "Dasgasd3m",
      streamer: "dasgasd3m", gross_rate_per_1k: 2.0, net_rate_per_1k: 1.8,
    });
    expect(created.status).toBe(201);
    expect((created.json.submission as { status: string }).status).toBe("pending");
    const dup = await callApi(env, "POST", "/api/campaign-submissions", {
      clip_id: 174, platform: "tiktok", campaign_id: CAMPAIGN,
    });
    expect(dup.status).toBe(409);
    expect((dup.json.submission as { id: number }).id).toBe((created.json.submission as { id: number }).id);
    const missing = await callApi(env, "POST", "/api/campaign-submissions", {
      clip_id: 999, platform: "tiktok", campaign_id: CAMPAIGN,
    });
    expect(missing.status).toBe(404);
  });

  test("POST rechaza published_at_ms futuro y post_url sin published_at_ms (nunca se inventa)", async () => {
    const { d1, raw } = makeDb();
    insertClip(raw, 174);
    const env = makeTestEnv(d1);
    const future = await callApi(env, "POST", "/api/campaign-submissions", {
      clip_id: 174, platform: "tiktok", campaign_id: CAMPAIGN, published_at_ms: Date.now() + 3600_000,
      post_url: "https://www.tiktok.com/@x/video/1",
    });
    expect(future.status).toBe(400);
    const orphanUrl = await callApi(env, "POST", "/api/campaign-submissions", {
      clip_id: 174, platform: "tiktok", campaign_id: CAMPAIGN, post_url: "https://www.tiktok.com/@x/video/1",
    });
    expect(orphanUrl.status).toBe(400);
    expect((await listCampaignSubmissions(d1, "sebastian", { clipId: 174 })).length).toBe(0);
  });

  test("PATCH marca submitted con submission_id; segundo PATCH, 409 (terminal)", async () => {
    const { d1, raw } = makeDb();
    insertClip(raw, 174);
    const { row } = await createCampaignSubmission(d1, "sebastian", {
      clip_id: 174, platform: "tiktok", campaign_id: CAMPAIGN, published_at_ms: NOW, post_url: "https://www.tiktok.com/@x/video/1",
    });
    const env = makeTestEnv(d1);
    const ok = await callApi(env, "PATCH", `/api/campaign-submissions/${row.id}`, { status: "submitted", submission_id: "sub_abc" });
    expect(ok.status).toBe(200);
    expect((ok.json.submission as { submission_id: string }).submission_id).toBe("sub_abc");
    const again = await callApi(env, "PATCH", `/api/campaign-submissions/${row.id}`, { status: "failed" });
    expect(again.status).toBe(409);
    const missing = await callApi(env, "PATCH", "/api/campaign-submissions/9999", { status: "failed" });
    expect(missing.status).toBe(404);
  });

  test("GET ?pending=1 devuelve la cola en nube (solo publicadas y en ventana)", async () => {
    const { d1 } = makeDb();
    await createCampaignSubmission(d1, "sebastian", { clip_id: 1, platform: "tiktok", campaign_id: CAMPAIGN, published_at_ms: Date.now() - 5 * 60 * 1000, post_url: "https://www.tiktok.com/@x/video/1" });
    await createCampaignSubmission(d1, "sebastian", { clip_id: 2, platform: "tiktok", campaign_id: CAMPAIGN });
    const env = makeTestEnv(d1);
    const res = await callApi(env, "GET", "/api/campaign-submissions?pending=1");
    expect(res.status).toBe(200);
    expect(res.json.pending).toBe(true);
    expect((res.json.submissions as Array<{ clip_id: number }>).map((s) => s.clip_id)).toEqual([1]);
  });
});
