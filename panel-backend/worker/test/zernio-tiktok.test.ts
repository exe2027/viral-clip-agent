// Tests del paso TikTok vía Zernio (worker/src/publish/zernio-tiktok.ts)
// y del dispatch por via (worker/src/publish/index.ts), con el
// transporte HTTP falso (globalThis.fetch). Sin red, sin D1 real, sin
// secretos reales: la clave Zernio y el account_id son inventados.
import { describe, expect, test, afterEach } from "bun:test";
import { encryptCredential } from "../src/crypto";
import type { Env } from "../src/cloudflare";
import type { JobRow } from "../src/jobs";
import { dispatchPublishStep, type PublishContext } from "../src/publish/index";
import { zernioTiktokStep } from "../src/publish/zernio-tiktok";
import { verifyCredential } from "../src/verify";

const KEY = Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"); // 32 bytes, SOLO TEST
const API_KEY = "zk_.test";
const ACCOUNT_ID = "acc_tiktok_123";
const VIDEO_URL = "https://www.tiktok.com/@elclipdegta6/video/987654321";
const MP4 = new Uint8Array([0, 0, 0, 24, 102, 116, 121, 112]); // bytes falsos

interface SeenRequest {
  url: string;
  method: string;
  headers: Record<string, string>;
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
    const headers: Record<string, string> = {};
    const h = init?.headers;
    if (h && typeof h === "object" && !(h instanceof Headers)) Object.assign(headers, h as Record<string, string>);
    if (h instanceof Headers) h.forEach((v, k) => (headers[k] = v));
    let body = "";
    if (typeof init?.body === "string") body = init.body;
    else if (init?.body instanceof Uint8Array) body = `<${init.body.length} bytes>`;
    seen.push({ url, method: init?.method ?? "GET", headers, body });
    const next = queue.shift() ?? { status: 500, body: { error: "sin respuesta en la cola del test" } };
    return new Response(JSON.stringify(next.body), { status: next.status, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
}

afterEach(() => {
  globalThis.fetch = realFetch;
});

function job(over: Partial<JobRow> = {}): JobRow {
  return {
    id: 42, tenant_id: "sebastian", kind: "publish", clip_id: 7, platform: "tiktok",
    api_account_id: 3, state: "processing", idempotency_key: null, resume_data: null,
    platform_id: null, evidence_url: null, title: "clip", caption: "GTA 6 #clip",
    r2_key: "clips/clip-7.mp4", posted_day: null, attempts: 0, last_error: null,
    retryable: 0, next_run_at: null, created_at: Date.now(), updated_at: Date.now(),
    ...over,
  } as JobRow;
}

function ctx(over: Partial<PublishContext> = {}): PublishContext {
  return {
    job: { ...job(), resumeData: null },
    creds: { tiktok: API_KEY, tiktokVia: "zernio", tiktokAccountId: ACCOUNT_ID },
    r2Size: MP4.length,
    ...over,
  };
}

function fakeEnv(): Env {
  return {
    CLIPS_BUCKET: {
      get: async () => ({ arrayBuffer: async () => MP4.buffer.slice(0) as ArrayBuffer, body: new ReadableStream(), size: MP4.length }),
      head: async () => ({ size: MP4.length }),
    },
  } as unknown as Env;
}

const PRESIGN_OK = { status: 200, body: { uploadUrl: "https://upload.zernio.test/put", publicUrl: "https://media.zernio.com/temp/clip-7.mp4" } };
const PUT_OK = { status: 200, body: {} };
const DRYRUN_OK = { status: 200, body: { dryRun: true, canPublish: true, tiktok: [{ accountId: ACCOUNT_ID, canPublish: true }] } };

describe("paso Zernio TikTok: flujo completo", () => {
  test("upload → dryRun → post 201 con platformPostUrl = posted", async () => {
    const env = fakeEnv();
    stubFetch([PRESIGN_OK, PUT_OK]);
    const up = await zernioTiktokStep(ctx(), env);
    expect(up.mode).toBe("processing");
    if (up.mode !== "processing") throw new Error("esperaba processing");
    expect((up.resumeData as Record<string, unknown>).step).toBe("dryrun");
    expect((up.resumeData as Record<string, unknown>).publicUrl).toBe("https://media.zernio.com/temp/clip-7.mp4");
    // El PUT no lleva Authorization (va al storage, no a la API).
    expect(seen[1].url).toBe("https://upload.zernio.test/put");
    expect(seen[1].headers.Authorization).toBeUndefined();
    expect(seen[1].body).toBe(`<${MP4.length} bytes>`);

    stubFetch([DRYRUN_OK]);
    const dry = await zernioTiktokStep(ctx({ job: { ...job(), resumeData: up.resumeData } }), env);
    expect(dry.mode).toBe("processing");
    if (dry.mode !== "processing") throw new Error("esperaba processing");
    expect((dry.resumeData as Record<string, unknown>).step).toBe("post");
    const dryBody = JSON.parse(seen[0].body) as Record<string, unknown>;
    expect(dryBody.dryRun).toBe(true);
    expect((dryBody.tiktokSettings as Record<string, unknown>).privacy_level).toBe("PUBLIC_TO_EVERYONE");
    expect((dryBody.tiktokSettings as Record<string, unknown>).content_preview_confirmed).toBe(true);
    expect((dryBody.tiktokSettings as Record<string, unknown>).express_consent_given).toBe(true);

    stubFetch([{ status: 201, body: { post: { id: "zpost_1", platforms: [{ platform: "tiktok", platformPostUrl: VIDEO_URL }] } } }]);
    const posted = await zernioTiktokStep(ctx({ job: { ...job(), resumeData: dry.resumeData } }), env);
    expect(posted).toEqual({ mode: "posted", platformId: "zpost_1", evidenceUrl: VIDEO_URL });
    const postBody = JSON.parse(seen[0].body) as Record<string, unknown>;
    expect(postBody.publishNow).toBe(true);
    expect(postBody.isDraft).toBe(false);
    expect(seen[0].headers["Idempotency-Key"]).toBe("42");
    expect(seen[0].headers.Authorization).toBe(`Bearer ${API_KEY}`);
  });

  test("201/202 sin URL → processing con zernioPostId; el GET la recupera", async () => {
    const env = fakeEnv();
    const resume = { step: "post", publicUrl: "https://media.zernio.com/temp/clip-7.mp4" };
    stubFetch([{ status: 202, body: { postId: "zpost_2", post: { id: "zpost_2", status: "publishing" } } }]);
    const mid = await zernioTiktokStep(ctx({ job: { ...job(), resumeData: resume } }), env);
    expect(mid.mode).toBe("processing");
    if (mid.mode !== "processing") throw new Error("esperaba processing");
    expect((mid.resumeData as Record<string, unknown>).step).toBe("poll");
    expect((mid.resumeData as Record<string, unknown>).zernioPostId).toBe("zpost_2");

    stubFetch([{ status: 200, body: { post: { id: "zpost_2", status: "published", platforms: [{ platform: "tiktok", platformPostUrl: VIDEO_URL }] } } }]);
    const done = await zernioTiktokStep(ctx({ job: { ...job(), resumeData: mid.resumeData } }), env);
    expect(done).toEqual({ mode: "posted", platformId: "zpost_2", evidenceUrl: VIDEO_URL });
    expect(seen[0].url).toBe("https://zernio.com/api/v1/posts/zpost_2");
    expect(seen[0].method).toBe("GET");
  });

  test("dryRun canPublish false → draft con el motivo", async () => {
    const env = fakeEnv();
    const resume = { step: "dryrun", publicUrl: "https://media.zernio.com/temp/clip-7.mp4" };
    stubFetch([{ status: 200, body: { dryRun: true, canPublish: false, tiktok: [{ accountId: ACCOUNT_ID, canPublish: false, reason: "daily limit reached" }] } }]);
    const r = await zernioTiktokStep(ctx({ job: { ...job(), resumeData: resume } }), env);
    expect(r.mode).toBe("draft");
    if (r.mode !== "draft") throw new Error("esperaba draft");
    expect(r.reason).toMatch(/daily limit reached/);
  });

  test("401 en el POST → draft (clave inválida, no reintentable)", async () => {
    const env = fakeEnv();
    const resume = { step: "post", publicUrl: "https://media.zernio.com/temp/clip-7.mp4" };
    stubFetch([{ status: 401, body: { error: { code: "unauthorized", message: "Invalid API key" } } }]);
    const r = await zernioTiktokStep(ctx({ job: { ...job(), resumeData: resume } }), env);
    expect(r.mode).toBe("draft");
    if (r.mode !== "draft") throw new Error("esperaba draft");
    expect(r.reason).toMatch(/401/);
  });

  test("error de capacidad → draft con el motivo exacto", async () => {
    const env = fakeEnv();
    const resume = { step: "post", publicUrl: "https://media.zernio.com/temp/clip-7.mp4" };
    stubFetch([{ status: 400, body: { error: { code: "platform_error", message: "TikTok direct posting is at capacity right now" } } }]);
    const r = await zernioTiktokStep(ctx({ job: { ...job(), resumeData: resume } }), env);
    expect(r.mode).toBe("draft");
    if (r.mode !== "draft") throw new Error("esperaba draft");
    expect(r.reason).toMatch(/at capacity right now/);
  });

  test("500 en presign → error reintentable", async () => {
    const env = fakeEnv();
    stubFetch([{ status: 500, body: { error: "boom" } }]);
    const r = await zernioTiktokStep(ctx(), env);
    expect(r.mode).toBe("error");
    if (r.mode !== "error") throw new Error("esperaba error");
    expect(r.retryable).toBe(true);
  });

  test("sin account_id en extra → draft pidiendo el account_id", async () => {
    const env = fakeEnv();
    const r = await zernioTiktokStep(ctx({ creds: { tiktok: API_KEY, tiktokVia: "zernio" } }), env);
    expect(r.mode).toBe("draft");
    if (r.mode !== "draft") throw new Error("esperaba draft");
    expect(r.reason).toMatch(/account_id/);
  });
});

describe("dispatch por via", () => {
  async function fakeDbWithAccount(extra: string) {
    const credentialEnc = await encryptCredential(API_KEY, KEY, "sebastian");
    const extraEnc = extra ? await encryptCredential(extra, KEY, "sebastian") : "";
    const row = {
      id: 3, tenant_id: "sebastian", platform: "tiktok", language: "es", label: "TikTok ES",
      credential: "", credential_enc: credentialEnc, extra: "", extra_enc: extraEnc,
      status: "conectada", note: "", created_at: 0, verified_at: null, verify_ok: 1, verify_message: "",
      first_published_at: null, published_count: 0, strikes: 0, maturity: "nueva", maturity_checked_day: null,
    };
    // Clip válido para que el re-chequeo del gate (M1) pase: 48 s,
    // GTA VI, SEO 95, preview visto, sin bloquear.
    const clip = {
      id: 7, tenant_id: "sebastian", source_id: null, influencer_id: null, title: "clip",
      start_sec: 0, end_sec: 48, score: 80, status: "aprobado", subtitle_style: "tiktok",
      seo_title: "", seo_description: "", seo_tags: "", thumb_text: "",
      platforms: '["tiktok"]', scheduled_for: null, published_at: null, file_deleted: 0,
      created_at: 1000, updated_at: 1000, captions: "{}",
      viral: '{"platforms":{"tiktok":{"score":95}}}', seo_overrides: "[]",
      preview_seen: 1, archived: 0, origin: "vod", publish_states: "{}",
      publish_blocked: 0, needs_human_transcription: 0, subtitles_complete: 1,
      video_r2_key: "clips/clip-7.mp4", game: "GTA VI", language: "es",
    };
    const db = {
      // El re-chequeo del gate pregunta por clip, settings, conteos
      // y cuenta: cada consulta devuelve su fila (o vacío).
      prepare: (sql: string) => ({
        bind: () => ({
          all: async () => ({ results: sql.includes("FROM api_accounts") ? [row] : [] }),
          first: async () => {
            if (sql.includes("FROM clips")) return clip;
            if (sql.includes("COUNT(*)")) return { n: 0 };
            if (sql.includes("FROM settings")) return null;
            if (sql.includes("FROM jobs")) return null;
            return row;
          },
          run: async () => ({ success: true, meta: {} }),
        }),
      }),
    };
    return db as unknown as Env["DB"];
  }

  test("via=zernio llama a Zernio (presign), no a open.tiktokapis.com", async () => {
    const db = await fakeDbWithAccount(JSON.stringify({ via: "zernio", account_id: ACCOUNT_ID }));
    const env = { ...fakeEnv(), DB: db, CREDENTIAL_ENCRYPTION_KEY: KEY } as unknown as Env;
    stubFetch([PRESIGN_OK, PUT_OK]);
    const r = await dispatchPublishStep(env, job());
    expect(r.mode).toBe("processing");
    expect(seen[0].url).toBe("https://zernio.com/api/v1/media/presign");
  });

  test("sin via llama al Direct Post propio (creator_info)", async () => {
    const db = await fakeDbWithAccount("");
    const env = { ...fakeEnv(), DB: db, CREDENTIAL_ENCRYPTION_KEY: KEY } as unknown as Env;
    stubFetch([{ status: 200, body: { data: { privacy_level_options: ["SELF_ONLY"] }, error: { code: "ok" } } }]);
    const r = await dispatchPublishStep(env, job());
    expect(r.mode).toBe("processing");
    expect(seen[0].url).toBe("https://open.tiktokapis.com/v2/post/publish/creator_info/query/");
  });
});

describe("verificación TikTok vía Zernio", () => {
  test("health healthy → conectada", async () => {
    stubFetch([{ status: 200, body: { accountId: ACCOUNT_ID, status: "healthy", tokenStatus: { valid: true } } }]);
    const out = await verifyCredential("tiktok", API_KEY, { via: "zernio", account_id: ACCOUNT_ID });
    expect(out.ok).toBe(true);
    expect(seen[0].url).toBe(`https://zernio.com/api/v1/accounts/${ACCOUNT_ID}/health`);
  });

  test("health error → no conectada, con el motivo", async () => {
    stubFetch([{ status: 200, body: { accountId: ACCOUNT_ID, status: "error", issues: [{ message: "token expired, reconnect" }] } }]);
    const out = await verifyCredential("tiktok", API_KEY, { via: "zernio", account_id: ACCOUNT_ID });
    expect(out.ok).toBe(false);
    expect(out.message).toMatch(/token expired/);
  });

  test("clave zk_ sin extra usa la lista de cuentas", async () => {
    stubFetch([{ status: 200, body: { accounts: [{ platform: "tiktok", status: "connected", username: "elclipdegta6" }] } }]);
    const out = await verifyCredential("tiktok", API_KEY, {});
    expect(out.ok).toBe(true);
    expect(seen[0].url).toBe("https://zernio.com/api/v1/accounts?platform=tiktok");
  });

  test("sin via y sin prefijo zk_ sigue verificando contra TikTok directo", async () => {
    stubFetch([{ status: 200, body: { data: { open_id: "x" }, error: { code: "ok" } } }]);
    const out = await verifyCredential("tiktok", "act.token-directo", {});
    expect(out.ok).toBe(true);
    expect(seen[0].url).toContain("open.tiktokapis.com");
  });
});

describe("verificación YouTube por tokeninfo (scope upload)", () => {
  test("token solo youtube.upload → conectada sin llamar a channels", async () => {
    stubFetch([{ status: 200, body: { scope: "https://www.googleapis.com/auth/youtube.upload", expires_in: 3600 } }]);
    const out = await verifyCredential("youtube", "ya29.token-subida", {});
    expect(out.ok).toBe(true);
    expect(out.message).toMatch(/youtube\.upload/);
    expect(seen[0].url).toContain("oauth2.googleapis.com/tokeninfo");
    expect(seen).toHaveLength(1);
  });

  test("tokeninfo 400 → fallback a channels; si channels da 200, conectada", async () => {
    stubFetch([
      { status: 400, body: { error: "invalid_token" } },
      { status: 200, body: { items: [{ id: "UCxxx" }] } },
    ]);
    const out = await verifyCredential("youtube", "ya29.token-readonly", {});
    expect(out.ok).toBe(true);
    expect(seen[1].url).toContain("youtube/v3/channels");
  });

  test("tokeninfo sin scope youtube y channels 403 → no conectada", async () => {
    stubFetch([
      { status: 200, body: { scope: "https://www.googleapis.com/auth/drive.readonly", expires_in: 3600 } },
      { status: 403, body: { error: { code: 403, message: "forbidden" } } },
    ]);
    const out = await verifyCredential("youtube", "ya29.token-otro-scope", {});
    expect(out.ok).toBe(false);
  });
});

// Topes Zernio POR CUENTA (orden 2026-11-23): TikTok ES y EN son
// cuentas Zernio distintas, cada una con su propio tope diario (15)
// y de borradores pendientes (5). Los conteos llevan filtro por
// idioma (el del job): un ES al tope no frena un EN, y viceversa.
describe("topes Zernio por idioma (ES y EN cuentan por separado)", () => {
  const POST_RESUME = { step: "post", publicUrl: "https://media.zernio.com/temp/clip-7.mp4" };
  const POST_201 = { status: 201, body: { post: { id: "zpost_9", platforms: [{ platform: "tiktok", platformPostUrl: VIDEO_URL }] } } };

  // D1 mínima: settings vacíos (defaults 15/día, 5 borradores) y
  // conteos por idioma para las dos consultas de topes.
  function fakeDb(posted: Record<string, number>, drafts: Record<string, number>) {
    return {
      prepare: (sql: string) => ({
        bind: (...params: unknown[]) => ({
          first: async () => {
            if (sql.includes("FROM settings")) return { data: "{}" };
            if (sql.includes("state = 'posted'")) return { n: posted[String(params[1])] ?? 0 };
            if (sql.includes("state = 'draft'")) return { n: drafts[String(params[1])] ?? 0 };
            return null;
          },
        }),
      }),
    };
  }

  function envWithDb(db: unknown): Env {
    return { ...fakeEnv(), DB: db } as unknown as Env;
  }

  function postCtx(language: string): PublishContext {
    return ctx({ job: { ...job(), language, resumeData: POST_RESUME } });
  }

  test("ES al tope (15/día) NO frena un EN: el post sale", async () => {
    const env = envWithDb(fakeDb({ es: 15, en: 0 }, { es: 5, en: 0 }));
    stubFetch([POST_201]);
    const out = await zernioTiktokStep(postCtx("en"), env);
    expect(out).toEqual({ mode: "posted", platformId: "zpost_9", evidenceUrl: VIDEO_URL });
  });

  test("EN al tope (15/día) SÍ frena un EN: draft con el motivo", async () => {
    const env = envWithDb(fakeDb({ es: 0, en: 15 }, { es: 0, en: 0 }));
    stubFetch([POST_201]);
    const out = await zernioTiktokStep(postCtx("en"), env);
    expect(out.mode).toBe("draft");
    if (out.mode !== "draft") throw new Error("esperaba draft");
    expect(out.reason).toMatch(/Tope Zernio diario alcanzado: 15\/15/);
    expect(out.reason).toMatch(/TikTok en hoy/);
    expect(seen).toHaveLength(0); // no se llamó a Zernio
  });

  test("borradores ES al tope (5) NO frenan un EN: el post sale", async () => {
    const env = envWithDb(fakeDb({ es: 0, en: 0 }, { es: 5, en: 0 }));
    stubFetch([POST_201]);
    const out = await zernioTiktokStep(postCtx("en"), env);
    expect(out).toEqual({ mode: "posted", platformId: "zpost_9", evidenceUrl: VIDEO_URL });
  });

  test("borradores EN al tope (5) SÍ frenan un EN: draft con el motivo", async () => {
    const env = envWithDb(fakeDb({ es: 0, en: 0 }, { es: 0, en: 5 }));
    stubFetch([POST_201]);
    const out = await zernioTiktokStep(postCtx("en"), env);
    expect(out.mode).toBe("draft");
    if (out.mode !== "draft") throw new Error("esperaba draft");
    expect(out.reason).toMatch(/Demasiados borradores TikTok en pendientes/);
    expect(seen).toHaveLength(0);
  });
});
