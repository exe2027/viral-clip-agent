// Tests Worker de la spec de títulos (2026-11-25/26, aprobada
// 2026-11-26): grupos §8.11 (engagement, parte Worker/YouTube) y
// §8.12 (YouTube update con mock), más R4.1 (GET /api/bridge/titles),
// R3.4/I5 (retención por título: POST publish_blocked, PATCH
// desbloqueo, gate prefiere hold_reason) e I1 (verify muestra la
// falta de youtube.force-ssl).
// Sin red real (fetch con cola), sin D1 real (bun:sqlite en memoria
// + SqliteStmt, o worker.fetch con esa misma D1), sin secretos
// reales: las credenciales son inventadas.
import { afterEach, describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { createSessionToken } from "../src/auth";
import { encryptCredential } from "../src/crypto";
import type { D1Database, Env } from "../src/cloudflare";
import { mergeViralJson, parseViralInput } from "../src/db";
import { publishGate, type GateContext } from "../src/gate";
import worker, { parseClipInput } from "../src/index";
import { hasYoutubeUpdateScope, updateVideoTitle } from "../src/publish/youtube";
import { verifyCredential } from "../src/verify";
import { SqliteStmt } from "./budget-test-db";

const KEY = Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"); // SOLO TEST
const SECRET = Buffer.from("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb").toString("base64"); // SOLO TEST
const NOW = Date.now(); // instante real: el handler calcula la ventana con Date.now()
const DAY = 86_400_000;

// ---------------------------------------------------------------------------
// D1 sqlite en memoria
// ---------------------------------------------------------------------------

function makeDb(): { d1: D1Database; raw: Database } {
  const raw = new Database(":memory:");
  raw.exec(
    "CREATE TABLE settings (tenant_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at INTEGER NOT NULL)",
  );
  raw.exec(
    "CREATE TABLE api_accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, platform TEXT NOT NULL, " +
      "language TEXT NOT NULL DEFAULT 'es', label TEXT NOT NULL DEFAULT '', credential TEXT NOT NULL DEFAULT '', " +
      "credential_enc TEXT NOT NULL DEFAULT '', extra TEXT NOT NULL DEFAULT '', extra_enc TEXT NOT NULL DEFAULT '', " +
      "status TEXT NOT NULL DEFAULT 'pendiente', note TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, " +
      "verified_at INTEGER, verify_ok INTEGER, verify_message TEXT NOT NULL DEFAULT '')",
  );
  raw.exec(
    "CREATE TABLE clips (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, source_id INTEGER, " +
      "influencer_id INTEGER, title TEXT NOT NULL, start_sec INTEGER NOT NULL DEFAULT 0, end_sec INTEGER NOT NULL DEFAULT 0, " +
      "score INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'en_carpeta', " +
      "subtitle_style TEXT NOT NULL DEFAULT '', seo_title TEXT NOT NULL DEFAULT '', seo_description TEXT NOT NULL DEFAULT '', " +
      "seo_tags TEXT NOT NULL DEFAULT '', thumb_text TEXT NOT NULL DEFAULT '', platforms TEXT NOT NULL DEFAULT '[]', " +
      "scheduled_for TEXT, published_at INTEGER, file_deleted INTEGER NOT NULL DEFAULT 0, " +
      "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, captions TEXT NOT NULL DEFAULT '{}', " +
      "viral TEXT NOT NULL DEFAULT '{}', seo_overrides TEXT NOT NULL DEFAULT '[]', preview_seen INTEGER NOT NULL DEFAULT 0, " +
      "archived INTEGER NOT NULL DEFAULT 0, origin TEXT NOT NULL DEFAULT 'vod', publish_states TEXT NOT NULL DEFAULT '{}', " +
      "publish_blocked INTEGER NOT NULL DEFAULT 0, needs_human_transcription INTEGER NOT NULL DEFAULT 0, " +
      "subtitles_complete INTEGER NOT NULL DEFAULT 0, video_r2_key TEXT NOT NULL DEFAULT '', game TEXT NOT NULL DEFAULT '', " +
      "language TEXT NOT NULL DEFAULT 'es', source_key TEXT NOT NULL DEFAULT '')",
  );
  const d1 = { prepare: (sql: string) => new SqliteStmt(raw, sql) } as unknown as D1Database;
  return { d1, raw };
}

function fakeEnv(d1: D1Database): Env {
  return {
    DB: d1,
    TENANT: "sebastian",
    SESSION_SECRET: SECRET,
    CREDENTIAL_ENCRYPTION_KEY: KEY,
  } as unknown as Env;
}

const ctx = { waitUntil: () => {}, passThroughOnException: () => {} };

async function call(
  env: Env,
  method: string,
  path: string,
  opts: { token?: string; body?: Record<string, unknown> } = {},
): Promise<{ status: number; json: Record<string, unknown> }> {
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
  return { status: res.status, json: (await res.json()) as Record<string, unknown> };
}

async function seedClip(
  raw: Database,
  over: { id: number; title: string; language?: string; created_at?: number; publish_blocked?: number; viral?: string; seo_title?: string; seo_description?: string; thumb_text?: string; subtitles_complete?: number },
): Promise<void> {
  raw
    .query(
      "INSERT INTO clips (id, tenant_id, title, language, created_at, updated_at, publish_blocked, viral, seo_title, seo_description, thumb_text, subtitles_complete) " +
        "VALUES (?1, 'sebastian', ?2, ?3, ?4, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
    )
    .run(
      over.id,
      over.title,
      over.language ?? "es",
      over.created_at ?? NOW,
      over.publish_blocked ?? 0,
      over.viral ?? "{}",
      over.seo_title ?? "",
      over.seo_description ?? "",
      over.thumb_text ?? "",
      over.subtitles_complete ?? 1,
    );
}

// ---------------------------------------------------------------------------
// fetch con cola (para updateVideoTitle y verifyCredential)
// ---------------------------------------------------------------------------

interface SeenRequest {
  url: string;
  method: string;
  body: string;
}

let seen: SeenRequest[] = [];
const realFetch = globalThis.fetch;

function stubFetch(responses: Array<{ status: number; body: unknown }>) {
  seen = [];
  const queue = [...responses];
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

// ---------------------------------------------------------------------------
// §8.12 — YouTube update (mock)
// ---------------------------------------------------------------------------

async function seedYoutubeAccount(raw: Database, language = "es"): Promise<number> {
  const cred = await encryptCredential(
    JSON.stringify({ token: "ya29.viejo", refresh_token: "rt-test", client_id: "cid-test", client_secret: "csec.test" }),
    KEY,
    "sebastian",
  );
  const info = raw
    .query(
      "INSERT INTO api_accounts (tenant_id, platform, language, label, credential_enc, extra_enc, status, created_at) " +
        "VALUES ('sebastian', 'youtube', ?1, 'YT', ?2, '', 'ok', ?3) RETURNING id",
    )
    .get(language, cred, NOW) as { id: number };
  return info.id;
}

const SNIPPET = {
  title: "GTA 6: La misión de Brian: saltar el banco",
  description: "Rubius sigue la misión principal: el trabajo de Brian para saltar el banco.",
  tags: ["GTA6", "Rubius", "Atraco", "MisionBrian", "Shorts"],
  categoryId: "20",
};

describe("YouTube updateVideoTitle (§8.12)", () => {
  test("hasYoutubeUpdateScope: exacto youtube o force-ssl; upload solo no basta", () => {
    expect(hasYoutubeUpdateScope(["https://www.googleapis.com/auth/youtube"])).toBe(true);
    expect(hasYoutubeUpdateScope(["https://www.googleapis.com/auth/youtube.force-ssl"])).toBe(true);
    expect(
      hasYoutubeUpdateScope([
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.force-ssl",
      ]),
    ).toBe(true);
    expect(hasYoutubeUpdateScope(["https://www.googleapis.com/auth/youtube.upload"])).toBe(false);
    expect(hasYoutubeUpdateScope(["https://www.googleapis.com/auth/youtube.readonly"])).toBe(false);
    expect(hasYoutubeUpdateScope([])).toBe(false);
  });

  test("videos.update envía el snippet completo (título + descripción + tags + categoryId)", async () => {
    const { d1, raw } = makeDb();
    const accountId = await seedYoutubeAccount(raw, "es");
    stubFetch([
      { status: 200, body: { access_token: "ya29.nuevo", expires_in: 3600 } },
      {
        status: 200,
        body: { scope: "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.force-ssl", expires_in: 3600 },
      },
      { status: 200, body: { id: "s3GZe8C74gY" } },
    ]);
    const out = await updateVideoTitle(fakeEnv(d1), accountId, "s3GZe8C74gY", SNIPPET);
    expect(out.ok).toBe(true);
    expect(seen).toHaveLength(3);
    expect(seen[0]?.url).toContain("oauth2.googleapis.com/token");
    expect(seen[1]?.url).toContain("oauth2.googleapis.com/tokeninfo");
    const put = seen[2] as SeenRequest;
    expect(put.method).toBe("PUT");
    expect(put.url).toBe("https://www.googleapis.com/youtube/v3/videos?part=snippet");
    const body = JSON.parse(put.body) as { id: string; snippet: Record<string, unknown> };
    expect(body.id).toBe("s3GZe8C74gY");
    expect(body.snippet.title).toBe(SNIPPET.title);
    expect(body.snippet.description).toBe(SNIPPET.description);
    expect(body.snippet.tags).toEqual(SNIPPET.tags);
    expect(body.snippet.categoryId).toBe("20");
  });

  test("scope youtube genérico también vale para videos.update", async () => {
    const { d1, raw } = makeDb();
    const accountId = await seedYoutubeAccount(raw, "es");
    stubFetch([
      { status: 200, body: { access_token: "ya29.nuevo", expires_in: 3600 } },
      { status: 200, body: { scope: "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube", expires_in: 3600 } },
      { status: 200, body: { id: "s3GZe8C74gY" } },
    ]);
    const out = await updateVideoTitle(fakeEnv(d1), accountId, "s3GZe8C74gY", SNIPPET);
    expect(out.ok).toBe(true);
    expect(seen.some((r) => r.url.includes("/youtube/v3/videos"))).toBe(true);
  });

  test("sin youtube.force-ssl: error claro missing_scope SIN llamar a videos.update", async () => {
    const { d1, raw } = makeDb();
    const accountId = await seedYoutubeAccount(raw, "es");
    stubFetch([
      { status: 200, body: { access_token: "ya29.nuevo", expires_in: 3600 } },
      { status: 200, body: { scope: "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly", expires_in: 3600 } },
    ]);
    const out = await updateVideoTitle(fakeEnv(d1), accountId, "s3GZe8C74gY", SNIPPET);
    expect(out.ok).toBe(false);
    expect(out.missingScope).toBe(true);
    expect(out.reason).toContain("missing_scope: youtube.force-ssl");
    expect(out.reason).toContain("re-autoriza YouTube en el panel");
    expect(seen.some((r) => r.url.includes("/youtube/v3/videos"))).toBe(false);
    expect(seen).toHaveLength(2);
  });

  test("sin cuenta YouTube: error claro sin llamar a Google", async () => {
    const { d1 } = makeDb();
    stubFetch([]);
    const out = await updateVideoTitle(fakeEnv(d1), 999, "s3GZe8C74gY", SNIPPET);
    expect(out.ok).toBe(false);
    expect(seen).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// I1 — verify.ts muestra la falta de youtube.force-ssl
// ---------------------------------------------------------------------------

describe("verify YouTube: check explícito de force-ssl (I1)", () => {
  const OAUTH_JSON = JSON.stringify({
    token: "ya29.viejo",
    refresh_token: "rt-test",
    client_id: "cid-test",
    client_secret: "csec.test",
  });

  test("JSON con upload pero sin force-ssl: verifica ok pero avisa que falta re-autorizar", async () => {
    stubFetch([
      { status: 200, body: { access_token: "ya29.nuevo", expires_in: 3600 } },
      { status: 200, body: { scope: "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly", expires_in: 3600 } },
    ]);
    const out = await verifyCredential("youtube", OAUTH_JSON, {});
    // ok:true a propósito: un ok:false pondría verify_ok=0 y gate.ts
    // frenaría la subida, que hoy funciona (ver verify.ts).
    expect(out.ok).toBe(true);
    expect(out.message).toContain("youtube.upload");
    expect(out.message).toContain("Falta youtube.force-ssl");
    expect(out.message).toContain("re-autorizar YouTube en el panel");
  });

  test("JSON con upload + force-ssl: verifica ok sin aviso (mensaje sin cambio)", async () => {
    stubFetch([
      { status: 200, body: { access_token: "ya29.nuevo", expires_in: 3600 } },
      { status: 200, body: { scope: "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.force-ssl", expires_in: 3600 } },
    ]);
    const out = await verifyCredential("youtube", OAUTH_JSON, {});
    expect(out.ok).toBe(true);
    expect(out.message).toBe("YouTube aceptó el token OAuth (scope youtube.upload, renovado con refresh_token).");
    expect(out.message).not.toContain("Falta youtube.force-ssl");
  });

  test("token suelto con solo upload: verifica ok pero avisa", async () => {
    stubFetch([{ status: 200, body: { scope: "https://www.googleapis.com/auth/youtube.upload", expires_in: 3600 } }]);
    const out = await verifyCredential("youtube", "ya29.suelto", {});
    expect(out.ok).toBe(true);
    expect(out.message).toContain("Falta youtube.force-ssl");
  });
});

// ---------------------------------------------------------------------------
// R4.1 — GET /api/bridge/titles
// ---------------------------------------------------------------------------

describe("GET /api/bridge/titles (R4.1)", () => {
  test("tenant de la sesión, ventana days, sin tope de 50", async () => {
    const { d1, raw } = makeDb();
    const env = fakeEnv(d1);
    await seedClip(raw, { id: 1, title: "GTA 6: Título reciente ES", language: "es", created_at: NOW - 5 * DAY });
    await seedClip(raw, { id: 2, title: "GTA 6: Título de hoy EN", language: "en", created_at: NOW - DAY / 2, seo_description: "Today's description in GTA 6.", thumb_text: "Today's thumb" });
    await seedClip(raw, { id: 3, title: "GTA 6: Título de hace 40 días", language: "es", created_at: NOW - 40 * DAY });
    await seedClip(raw, { id: 4, title: "GTA 6: Título de hace 60 días", language: "es", created_at: NOW - 60 * DAY });
    const token = await createSessionToken("sebastian", "sebastian", SECRET);
    const r30 = await call(env, "GET", "/api/bridge/titles", { token });
    expect(r30.status).toBe(200);
    const titles30 = r30.json.titles as Array<{ title: string; language: string; created_at: number; seo_description: string; thumb_text: string }>;
    expect(titles30).toHaveLength(2);
    expect(titles30[0]?.title).toBe("GTA 6: Título de hoy EN");
    expect(titles30[0]?.language).toBe("en");
    expect(typeof titles30[0]?.created_at).toBe("number");
    // R4.4: la descripción y la miniatura viajan con el título.
    expect(titles30[0]?.seo_description).toBe("Today's description in GTA 6.");
    expect(titles30[0]?.thumb_text).toBe("Today's thumb");
    expect(titles30[1]?.seo_description).toBe("");
    expect(titles30[1]?.thumb_text).toBe("");
    const r90 = await call(env, "GET", "/api/bridge/titles?days=90", { token });
    expect((r90.json.titles as unknown[])).toHaveLength(4);
    const r1 = await call(env, "GET", "/api/bridge/titles?days=1", { token });
    expect((r1.json.titles as unknown[])).toHaveLength(1);
  });

  test("sin sesión da 401; ?tenant= de otro tenant se ignora", async () => {
    const { d1, raw } = makeDb();
    const env = fakeEnv(d1);
    await seedClip(raw, { id: 1, title: "GTA 6: Título reciente ES", created_at: NOW - DAY });
    const noAuth = await call(env, "GET", "/api/bridge/titles");
    expect(noAuth.status).toBe(401);
    const token = await createSessionToken("sebastian", "sebastian", SECRET);
    const r = await call(env, "GET", "/api/bridge/titles?tenant=kaez", { token });
    expect(r.status).toBe(200);
    expect(r.json.tenant).toBe("sebastian");
    expect((r.json.titles as unknown[])).toHaveLength(1);
  });

  test("days fuera de rango o no entero da 400", async () => {
    const { d1 } = makeDb();
    const env = fakeEnv(d1);
    const token = await createSessionToken("sebastian", "sebastian", SECRET);
    for (const q of ["days=0", "days=91", "days=abc", "days=1.5"]) {
      const r = await call(env, "GET", `/api/bridge/titles?${q}`, { token });
      expect(r.status).toBe(400);
    }
  });
});

// ---------------------------------------------------------------------------
// R3.4/I5 — retención por título: POST, PATCH, gate, db
// ---------------------------------------------------------------------------

describe("retención por título (R3.4/I5)", () => {
  function postBody(over: Record<string, unknown> = {}): Record<string, unknown> {
    return {
      title: "Sin título — revisión pendiente",
      start_sec: 100,
      end_sec: 140,
      score: 80,
      platforms: ["youtube_shorts"],
      subtitles_complete: 1,
      game: "GTA VI",
      ...over,
    };
  }

  test("parseClipInput: con hold_reason nace bloqueado aunque tenga subtítulos completos", () => {
    const parsed = parseClipInput(postBody({ viral: { hold_reason: "Sin título honesto: retención para revisión" } }));
    expect(typeof parsed).not.toBe("string");
    if (typeof parsed === "string") return;
    expect(parsed.publish_blocked).toBe(1);
    expect(JSON.parse(parsed.viral).hold_reason).toBe("Sin título honesto: retención para revisión");
  });

  test("parseClipInput: sin hold_reason y con subtítulos completos nace desbloqueado", () => {
    const parsed = parseClipInput(postBody());
    expect(typeof parsed).not.toBe("string");
    if (typeof parsed === "string") return;
    expect(parsed.publish_blocked).toBe(0);
  });

  test("parseViralInput/mergeViralJson: hold_reason se guarda y null lo borra", () => {
    const parsed = parseViralInput({ viral: { hold_reason: "Sin título honesto: retención para revisión" } });
    expect(typeof parsed).not.toBe("string");
    expect((parsed as { hold_reason?: string }).hold_reason).toBe("Sin título honesto: retención para revisión");
    expect(parseViralInput({ viral: { hold_reason: "x".repeat(501) } })).toMatch(/500/);
    const merged = JSON.parse(mergeViralJson(JSON.stringify({ hold_reason: "viejo", viral_score: 50 }), { hold_reason: null }));
    expect(merged.hold_reason).toBeUndefined();
    expect(merged.viral_score).toBe(50);
    const kept = JSON.parse(mergeViralJson(JSON.stringify({ hold_reason: "viejo" }), { viral_score: 60 }));
    expect(kept.hold_reason).toBe("viejo");
  });

  test("PATCH: desbloquear un retenido sin título nuevo da 400", async () => {
    const { d1, raw } = makeDb();
    const env = fakeEnv(d1);
    await seedClip(raw, {
      id: 1,
      title: "Sin título — revisión pendiente",
      publish_blocked: 1,
      viral: JSON.stringify({ hold_reason: "Sin título honesto: retención para revisión" }),
    });
    const token = await createSessionToken("sebastian", "sebastian", SECRET);
    const r = await call(env, "PATCH", "/api/clips/1", { token, body: { publish_blocked: 0 } });
    expect(r.status).toBe(400);
    expect(String(r.json.error)).toContain("título nuevo");
  });

  test("PATCH: con título SEO nuevo desbloquea y limpia hold_reason", async () => {
    const { d1, raw } = makeDb();
    const env = fakeEnv(d1);
    await seedClip(raw, {
      id: 1,
      title: "Sin título — revisión pendiente",
      publish_blocked: 1,
      viral: JSON.stringify({ hold_reason: "Sin título honesto: retención para revisión", viral_score: 55 }),
    });
    const token = await createSessionToken("sebastian", "sebastian", SECRET);
    const r = await call(env, "PATCH", "/api/clips/1", {
      token,
      body: { seo_title: "GTA 6: La misión de Brian: saltar el banco", publish_blocked: 0 },
    });
    expect(r.status).toBe(200);
    const clip = (r.json.clip ?? {}) as { publish_blocked: number; viral: { hold_reason?: string; viral_score?: number } };
    expect(clip.publish_blocked).toBe(0);
    expect(clip.viral.hold_reason).toBeUndefined();
    expect(clip.viral.viral_score).toBe(55);
  });

  test("PATCH: con title nuevo desbloquea y limpia hold_reason; el mismo título no vale", async () => {
    const { d1, raw } = makeDb();
    const env = fakeEnv(d1);
    await seedClip(raw, {
      id: 1,
      title: "Sin título — revisión pendiente",
      publish_blocked: 1,
      viral: JSON.stringify({ hold_reason: "Sin título honesto: retención para revisión" }),
    });
    const token = await createSessionToken("sebastian", "sebastian", SECRET);
    const same = await call(env, "PATCH", "/api/clips/1", {
      token,
      body: { title: "Sin título — revisión pendiente", publish_blocked: 0 },
    });
    expect(same.status).toBe(400);
    const r = await call(env, "PATCH", "/api/clips/1", {
      token,
      body: { title: "GTA 6: La misión de Brian: saltar el banco", publish_blocked: 0 },
    });
    expect(r.status).toBe(200);
    const clip = (r.json.clip ?? {}) as { publish_blocked: number; viral: { hold_reason?: string } };
    expect(clip.publish_blocked).toBe(0);
    expect(clip.viral.hold_reason).toBeUndefined();
  });

  test("PATCH: cambiar solo el título limpia hold_reason pero sigue bloqueado", async () => {
    const { d1, raw } = makeDb();
    const env = fakeEnv(d1);
    await seedClip(raw, {
      id: 1,
      title: "Sin título — revisión pendiente",
      publish_blocked: 1,
      viral: JSON.stringify({ hold_reason: "Sin título honesto: retención para revisión" }),
    });
    const token = await createSessionToken("sebastian", "sebastian", SECRET);
    const r = await call(env, "PATCH", "/api/clips/1", {
      token,
      body: { title: "GTA 6: La misión de Brian: saltar el banco" },
    });
    expect(r.status).toBe(200);
    const clip = (r.json.clip ?? {}) as { publish_blocked: number; viral: { hold_reason?: string } };
    expect(clip.publish_blocked).toBe(1);
    expect(clip.viral.hold_reason).toBeUndefined();
  });

  test("PATCH: title entra en el allowlist (R8.3) en un clip no retenido", async () => {
    const { d1, raw } = makeDb();
    const env = fakeEnv(d1);
    await seedClip(raw, { id: 1, title: "Título viejo" });
    const token = await createSessionToken("sebastian", "sebastian", SECRET);
    const r = await call(env, "PATCH", "/api/clips/1", {
      token,
      body: { title: "GTA 6: Grefg: el mejor easter egg está en la moto", seo_title: "GTA 6: Grefg: el mejor easter egg está en la moto" },
    });
    expect(r.status).toBe(200);
    const clip = (r.json.clip ?? {}) as { title: string; seo_title: string };
    expect(clip.title).toBe("GTA 6: Grefg: el mejor easter egg está en la moto");
    expect(clip.seo_title).toBe("GTA 6: Grefg: el mejor easter egg está en la moto");
  });

  test("gate prefiere hold_reason antes del motivo de subtítulos", () => {
    const ctxFor = (viral: string): GateContext =>
      ({
        clip: {
          archived: 0,
          language: "es",
          publish_blocked: 1,
          viral,
          subtitles_complete: 1,
        },
        seoScore: 95,
        seoOverride: false,
        publishedToday: 0,
        lastPublishedAtMs: null,
        streamerAllowed: true,
        settings: {},
      }) as unknown as GateContext;
    const held = publishGate("youtube_shorts", ctxFor(JSON.stringify({ hold_reason: "Sin título honesto: retención para revisión" })));
    expect(held).toContain("Sin título honesto: retención para revisión");
    expect(held).not.toContain("sin subtítulos completos");
    const plain = publishGate("youtube_shorts", ctxFor("{}"));
    expect(plain).toContain("sin subtítulos completos");
    const rescued = publishGate(
      "youtube_shorts",
      ctxFor(JSON.stringify({ rescue: { viral_revisado: 48, shouts: 3 } })),
    );
    expect(rescued).toContain("rescatado por gritos");
  });
});
