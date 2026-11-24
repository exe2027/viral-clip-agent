// Tests V4 — validador de caption sin marca [clip:N] (SPEC
// validators 2026-11-23, Resolución Goldfish punto 8).
//
// El caption público ya NO lleva [clip:<id>] desde el commit
// 51afff2 (era visible, p. ej. el reel del clip 168). El test de
// regresión del caso 168 YA existe en wal-idempotency.test.ts:285
// y NO se duplica aquí. Esto añade lo nuevo: el helper compartido
// (solo la marca PROPIA), su enganche en los 4 puntos de salida
// (dispatch principal, TikTok Direct, Zernio y respaldo) y el
// escaneo report-only de lo ya publicado.
//
// Criterio de regresión: cada test debe FALLAR si se revierte el
// arreglo. Comprobado con mutación temporal de hasOwnClipMarker
// (return false): los tests de helper y de los 4 puntos pasan a
// rojo; al deshacer, verde (ver reporte del trabajo).
import { describe, expect, test, afterEach } from "bun:test";
import { Database } from "bun:sqlite";
import { encryptCredential } from "../src/crypto";
import type { D1Database, Env, R2Bucket } from "../src/cloudflare";
import type { JobRow } from "../src/jobs";
import { appendWalEntry } from "../src/wal";
import { dispatchPublishStep, type PublishContext, type PublishResult } from "../src/publish/index";
import {
  assertCaptionClean,
  captionMarkerReason,
  hasOwnClipMarker,
  scanCaptionsForOwnMarker,
} from "../src/publish/idempotency";
import { tiktokStep } from "../src/publish/tiktok";
import { zernioTiktokStep } from "../src/publish/zernio-tiktok";
import { runBackupCycle } from "../src/backup-publisher";

// ---------------------------------------------------------------------------
// Helper puro
// ---------------------------------------------------------------------------
describe("V4 — hasOwnClipMarker: solo la marca PROPIA", () => {
  test("marca propia (positivo): [clip:168] en el caption del clip 168", () => {
    expect(hasOwnClipMarker("GTA 6: reacción\n[clip:168]", 168)).toBe(true);
    expect(hasOwnClipMarker("[clip:168]", 168)).toBe(true);
    expect(hasOwnClipMarker("mira [clip:168] al final", 168)).toBe(true);
  });

  test("marca de OTRO clip (negativo): [clip:91] no es la marca del clip 168", () => {
    expect(hasOwnClipMarker("GTA 6: reacción\n[clip:91]", 168)).toBe(false);
    expect(hasOwnClipMarker("[clip:91]", 168)).toBe(false);
  });

  test("texto citado por el streamer (negativo): [clip:999] no es el id propio", () => {
    expect(hasOwnClipMarker("dijo [clip:999] en el directo, jaja", 168)).toBe(false);
    expect(hasOwnClipMarker("el chat puso [clip:999]", 168)).toBe(false);
  });

  test("caption limpio (negativo) y borde [clip:1680] no es [clip:168]", () => {
    expect(hasOwnClipMarker("GTA 6: reacción #gta6", 168)).toBe(false);
    expect(hasOwnClipMarker("", 168)).toBe(false);
    expect(hasOwnClipMarker("[clip:1680]", 168)).toBe(false);
    expect(hasOwnClipMarker("[clip:16]", 168)).toBe(false);
  });

  test("sin caption o sin clip_id no se rechaza nada (no se puede saber la marca propia)", () => {
    expect(hasOwnClipMarker(null, 168)).toBe(false);
    expect(hasOwnClipMarker(undefined, 168)).toBe(false);
    expect(hasOwnClipMarker("[clip:168]", null)).toBe(false);
    expect(hasOwnClipMarker("[clip:168]", undefined)).toBe(false);
  });

  test("assertCaptionClean lanza con la marca propia y no lanza sin ella", () => {
    expect(() => assertCaptionClean("GTA 6\n[clip:168]", 168)).toThrow("[clip:168]");
    expect(() => assertCaptionClean("GTA 6\n[clip:91]", 168)).not.toThrow();
    expect(() => assertCaptionClean("GTA 6 limpio", 168)).not.toThrow();
    expect(captionMarkerReason(168)).toContain("[clip:168]");
  });
});

describe("V4 — scanCaptionsForOwnMarker: solo reporte, sin tocar posts viejos", () => {
  test("mezcla: solo se reportan los captions con SU marca propia", () => {
    const hits = scanCaptionsForOwnMarker([
      { platform: "instagram_reels", clipId: 168, caption: "el reel del clip 168\n[clip:168]" },
      { platform: "tiktok", clipId: 168, caption: "el mismo clip sin marca" },
      { platform: "youtube_shorts", clipId: 91, caption: "clip 91\n[clip:91]" },
      { platform: "facebook_reels", clipId: 91, caption: "clip 91 con marca de otro\n[clip:168]" },
      { platform: "tiktok", clipId: 7, caption: "dijo [clip:999] en el directo" },
      { platform: "instagram_reels", clipId: 7, caption: null },
    ]);
    expect(hits).toHaveLength(2);
    expect(hits[0]).toMatchObject({ platform: "instagram_reels", clipId: 168, marker: "[clip:168]" });
    expect(hits[1]).toMatchObject({ platform: "youtube_shorts", clipId: 91, marker: "[clip:91]" });
  });

  test("sin marcas propias no hay reporte", () => {
    expect(scanCaptionsForOwnMarker([])).toEqual([]);
    expect(
      scanCaptionsForOwnMarker([
        { platform: "tiktok", clipId: 1, caption: "limpio" },
        { platform: "tiktok", clipId: 1, caption: "[clip:2]" },
      ]),
    ).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Transporte HTTP falso (globalThis.fetch), como zernio-tiktok.test.ts
// ---------------------------------------------------------------------------
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

// ---------------------------------------------------------------------------
// Harness dispatch (copiado de zernio-tiktok.test.ts): D1 falsa con
// cuenta TikTok Zernio ES + clip válido para que el gate pase.
// ---------------------------------------------------------------------------
const Z_KEY = Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"); // SOLO TEST
const Z_API_KEY = "zk_.test";
const Z_ACCOUNT_ID = "acc_tiktok_123";
const Z_MP4 = new Uint8Array([0, 0, 0, 24, 102, 116, 121, 112]); // bytes falsos

function zJob(over: Partial<JobRow> = {}): JobRow {
  return {
    id: 42, tenant_id: "sebastian", kind: "publish", clip_id: 7, platform: "tiktok",
    api_account_id: 3, state: "processing", idempotency_key: null, resume_data: null,
    platform_id: null, evidence_url: null, title: "clip", caption: "GTA 6 #clip",
    r2_key: "clips/clip-7.mp4", posted_day: null, attempts: 0, last_error: null,
    retryable: 0, next_run_at: null, created_at: Date.now(), updated_at: Date.now(),
    ...over,
  } as JobRow;
}

function zCtx(over: Partial<PublishContext> = {}): PublishContext {
  return {
    job: { ...zJob(), resumeData: null },
    creds: { tiktok: Z_API_KEY, tiktokVia: "zernio", tiktokAccountId: Z_ACCOUNT_ID },
    r2Size: Z_MP4.length,
    ...over,
  };
}

function zFakeEnv(): Env {
  return {
    CLIPS_BUCKET: {
      get: async () => ({ arrayBuffer: async () => Z_MP4.buffer.slice(0) as ArrayBuffer, body: new ReadableStream(), size: Z_MP4.length }),
      head: async () => ({ size: Z_MP4.length }),
    },
  } as unknown as Env;
}

async function zFakeDbWithAccount(extra: string) {
  const credentialEnc = await encryptCredential(Z_API_KEY, Z_KEY, "sebastian");
  const extraEnc = extra ? await encryptCredential(extra, Z_KEY, "sebastian") : "";
  const row = {
    id: 3, tenant_id: "sebastian", platform: "tiktok", language: "es", label: "TikTok ES",
    credential: "", credential_enc: credentialEnc, extra: "", extra_enc: extraEnc,
    status: "conectada", note: "", created_at: 0, verified_at: null, verify_ok: 1, verify_message: "",
    first_published_at: null, published_count: 0, strikes: 0, maturity: "nueva", maturity_checked_day: null,
  };
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

describe("V4 — punto 1: dispatchPublishStep rechaza la marca propia antes de despachar", () => {
  test("caption con [clip:7] del propio clip 7 → error no reintentable, sin llamar a ninguna plataforma", async () => {
    const db = await zFakeDbWithAccount(JSON.stringify({ via: "zernio", account_id: Z_ACCOUNT_ID }));
    const env = { ...zFakeEnv(), DB: db, CREDENTIAL_ENCRYPTION_KEY: Z_KEY } as unknown as Env;
    stubFetch([]);
    const r = await dispatchPublishStep(env, zJob({ caption: "GTA 6: reacción\n[clip:7]" }));
    expect(r.mode).toBe("error");
    if (r.mode !== "error") throw new Error("esperaba error");
    expect(r.retryable).toBe(false);
    expect(r.reason).toContain("[clip:7]");
    expect(seen).toHaveLength(0); // ni Zernio ni TikTok vieron el caption
  });

  test("caption limpio sigue su curso (control: Direct Post, creator_info)", async () => {
    const db = await zFakeDbWithAccount("");
    const env = { ...zFakeEnv(), DB: db, CREDENTIAL_ENCRYPTION_KEY: Z_KEY } as unknown as Env;
    stubFetch([{ status: 200, body: { data: { privacy_level_options: ["SELF_ONLY"] }, error: { code: "ok" } } }]);
    const r = await dispatchPublishStep(env, zJob());
    expect(r.mode).toBe("processing");
    expect(seen[0].url).toBe("https://open.tiktokapis.com/v2/post/publish/creator_info/query/");
  });

  test("caption con marca de OTRO clip no se rechaza (no es la marca del sistema)", async () => {
    const db = await zFakeDbWithAccount("");
    const env = { ...zFakeEnv(), DB: db, CREDENTIAL_ENCRYPTION_KEY: Z_KEY } as unknown as Env;
    stubFetch([{ status: 200, body: { data: { privacy_level_options: ["SELF_ONLY"] }, error: { code: "ok" } } }]);
    const r = await dispatchPublishStep(env, zJob({ caption: "GTA 6 [clip:999] citado" }));
    expect(r.mode).toBe("processing");
  });
});

// ---------------------------------------------------------------------------
// Punto 2: TikTok Direct Post (tiktokStep, paso init)
// ---------------------------------------------------------------------------
describe("V4 — punto 2: TikTok Direct rechaza la marca propia en el init", () => {
  function directCtx(caption: string): PublishContext {
    return {
      job: { ...zJob({ caption }), resumeData: { step: "init" } },
      creds: { tiktok: "tok-directo" },
      r2Size: 100,
    };
  }

  test("caption con [clip:7] → error no reintentable, sin POST a TikTok (límite 150 intacto)", async () => {
    stubFetch([]);
    const r = await tiktokStep(directCtx("GTA 6: reacción [clip:7]"), zFakeEnv());
    expect(r.mode).toBe("error");
    if (r.mode !== "error") throw new Error("esperaba error");
    expect(r.retryable).toBe(false);
    expect(r.reason).toContain("[clip:7]");
    expect(seen).toHaveLength(0);
  });

  test("caption limpio de 150 caracteres llega al init tal cual (control)", async () => {
    stubFetch([{ status: 200, body: { data: { publish_id: "pid123" }, error: { code: "ok" } } }]);
    const clean = "GTA 6: reacción ".padEnd(150, "x");
    const r = await tiktokStep(directCtx(clean), zFakeEnv());
    expect(r.mode).toBe("processing");
    if (r.mode !== "processing") throw new Error("esperaba processing");
    expect((r.resumeData as Record<string, unknown>).step).toBe("upload");
    const body = JSON.parse(seen[0].body) as Record<string, unknown>;
    expect(((body.post_info as Record<string, unknown>).title as string).length).toBe(150);
  });
});

// ---------------------------------------------------------------------------
// Punto 3: Zernio (dryRun y post)
// ---------------------------------------------------------------------------
describe("V4 — punto 3: Zernio rechaza la marca propia en dryRun y en post", () => {
  const DRYRUN_OK = { status: 200, body: { dryRun: true, canPublish: true, tiktok: [{ accountId: Z_ACCOUNT_ID, canPublish: true }] } };

  test("dryRun con [clip:7] → error, sin POST a Zernio", async () => {
    stubFetch([]);
    const r = await zernioTiktokStep(
      zCtx({ job: { ...zJob({ caption: "GTA 6 [clip:7]" }), resumeData: { step: "dryrun", publicUrl: "https://media.zernio.com/temp/clip-7.mp4" } } }),
      zFakeEnv(),
    );
    expect(r.mode).toBe("error");
    if (r.mode !== "error") throw new Error("esperaba error");
    expect(r.retryable).toBe(false);
    expect(r.reason).toContain("[clip:7]");
    expect(seen).toHaveLength(0);
  });

  test("post con [clip:7] → error, sin POST a Zernio (ni siquiera se consultan los topes)", async () => {
    stubFetch([]);
    const r = await zernioTiktokStep(
      zCtx({ job: { ...zJob({ caption: "GTA 6 [clip:7]" }), resumeData: { step: "post", publicUrl: "https://media.zernio.com/temp/clip-7.mp4" } } }),
      zFakeEnv(),
    );
    expect(r.mode).toBe("error");
    if (r.mode !== "error") throw new Error("esperaba error");
    expect(r.retryable).toBe(false);
    expect(seen).toHaveLength(0);
  });

  test("dryRun limpio sigue su curso (control: el body lleva el caption sin marca)", async () => {
    stubFetch([DRYRUN_OK]);
    const r = await zernioTiktokStep(
      zCtx({ job: { ...zJob(), resumeData: { step: "dryrun", publicUrl: "https://media.zernio.com/temp/clip-7.mp4" } } }),
      zFakeEnv(),
    );
    expect(r.mode).toBe("processing");
    const body = JSON.parse(seen[0].body) as Record<string, unknown>;
    expect(body.content).toBe("GTA 6 #clip");
    expect(body.dryRun).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Punto 4: respaldo (runBackupCycle) — harness copiado de
// backup-publisher.test.ts (D1 SQLite real + R2 falso en memoria).
// ---------------------------------------------------------------------------
const B_NOW = Date.parse("2026-11-20T15:00:00Z"); // 2026-11-20 12:00 -03
const B_MIN = 60_000;
const B_DAY = "2026-11-20";
const B_KEY_B64 = Buffer.from(new Uint8Array(32)).toString("base64");

class BSqliteStmt {
  constructor(
    private db: Database,
    private sql: string,
    private params: unknown[] = [],
  ) {}
  bind(...values: unknown[]): BSqliteStmt {
    return new BSqliteStmt(this.db, this.sql, values);
  }
  async first<T = Record<string, unknown>>(): Promise<T | null> {
    const row = this.db.query(this.sql).get(...(this.params as []));
    return (row as T | undefined) ?? null;
  }
  async all<T = Record<string, unknown>>(): Promise<{ results: T[]; success: boolean; meta: Record<string, unknown> }> {
    return { results: this.db.query(this.sql).all(...(this.params as [])) as T[], success: true, meta: {} };
  }
  async run(): Promise<{ results: never[]; success: boolean; meta: { changes?: number; last_row_id?: number } }> {
    const info = this.db.query(this.sql).run(...(this.params as []));
    return { results: [], success: true, meta: { changes: info.changes, last_row_id: Number(info.lastInsertRowid) } };
  }
}

function bMakeDb(): { d1: D1Database; raw: Database } {
  const raw = new Database(":memory:");
  raw.exec(
    "CREATE TABLE jobs (id INTEGER PRIMARY KEY, tenant_id TEXT, kind TEXT, clip_id INTEGER, platform TEXT, " +
      "api_account_id INTEGER, state TEXT, idempotency_key TEXT, resume_data TEXT, platform_id TEXT, " +
      "evidence_url TEXT, title TEXT, caption TEXT, r2_key TEXT, language TEXT, posted_day TEXT, " +
      "attempts INTEGER, last_error TEXT, retryable INTEGER, next_run_at INTEGER, created_at INTEGER, " +
      "updated_at INTEGER, claimed_by TEXT, run_token TEXT, slot_day TEXT, manual INTEGER)",
  );
  raw.exec("CREATE TABLE clips (id INTEGER PRIMARY KEY, tenant_id TEXT, archived INTEGER, status TEXT, language TEXT, title TEXT)");
  raw.exec(
    "CREATE TABLE api_accounts (id INTEGER PRIMARY KEY, tenant_id TEXT, platform TEXT, language TEXT, label TEXT, " +
      "credential TEXT, credential_enc TEXT, extra TEXT, extra_enc TEXT, status TEXT, note TEXT, created_at INTEGER, " +
      "verified_at INTEGER, verify_ok INTEGER, verify_message TEXT, first_published_at INTEGER, " +
      "published_count INTEGER, strikes INTEGER, maturity TEXT, maturity_checked_day TEXT)",
  );
  raw.exec("CREATE TABLE accounts (id INTEGER PRIMARY KEY, tenant_id TEXT, platform TEXT, language TEXT, handle TEXT, sort INTEGER)");
  const d1 = { prepare: (sql: string) => new BSqliteStmt(raw, sql) } as unknown as D1Database;
  return { d1, raw };
}

class BFakeBucket {
  files = new Map<string, Uint8Array>();
  puts: string[] = [];
  async get(key: string): Promise<{ arrayBuffer(): Promise<ArrayBuffer>; size: number } | null> {
    const b = this.files.get(key);
    if (!b) return null;
    const copy = b.slice();
    return { size: copy.length, arrayBuffer: async () => copy.buffer.slice(copy.byteOffset, copy.byteOffset + copy.byteLength) as ArrayBuffer };
  }
  async put(key: string, value: string | ArrayBuffer): Promise<{ key: string; size: number }> {
    const bytes = typeof value === "string" ? new TextEncoder().encode(value) : new Uint8Array(value);
    this.files.set(key, bytes);
    this.puts.push(key);
    return { key, size: bytes.length };
  }
  async head(key: string): Promise<{ size: number } | null> {
    const b = this.files.get(key);
    return b ? { size: b.length } : null;
  }
  async delete(key: string): Promise<void> {
    this.files.delete(key);
  }
  text(key: string): string | null {
    const b = this.files.get(key);
    return b ? new TextDecoder().decode(b) : null;
  }
}

async function bMakeFixture(): Promise<{ env: Env; raw: Database; bucket: BFakeBucket }> {
  const { d1, raw } = bMakeDb();
  const bucket = new BFakeBucket();
  const env = {
    DB: d1,
    CLIPS_BUCKET: bucket as unknown as R2Bucket,
    TENANT: "sebastian",
    SESSION_SECRET: B_KEY_B64,
    CREDENTIAL_ENCRYPTION_KEY: B_KEY_B64,
    BRIDGE_TOKEN: "",
    GITHUB_TOKEN: "",
    ALLOWED_EMAIL: "",
    GITHUB_REPO: "exe2027/viral-clip-agent",
    CLIP_PUBLIC_BASE_URL: "https://pub.example/r2.dev",
  } as unknown as Env;
  raw.exec("INSERT INTO clips (id, tenant_id, archived, status, language, title) VALUES (91, 'sebastian', 0, 'aprobado', 'es', 'Clip 91')");
  const metaCred = await encryptCredential("page-token", B_KEY_B64, "sebastian");
  const metaExtra = await encryptCredential(JSON.stringify({ ig_user_id: "111", page_id: "222" }), B_KEY_B64, "sebastian");
  raw.exec(
    "INSERT INTO api_accounts (id, tenant_id, platform, language, label, credential, credential_enc, extra, extra_enc, status, verify_ok) " +
      "VALUES (1, 'sebastian', 'meta', 'es', 'Meta ES', '', ?, '', ?, 'conectada', 1)",
    metaCred,
    metaExtra,
  );
  await bucket.put("clips/sebastian/91/x.mp4", new Uint8Array(100).buffer as ArrayBuffer);
  return { env, raw, bucket };
}

function bSeedJob(raw: Database, over: Partial<JobRow> = {}): void {
  const j: JobRow = {
    id: 1,
    tenant_id: "sebastian",
    kind: "publish",
    clip_id: 91,
    platform: "instagram_reels",
    api_account_id: 1,
    state: "scheduled",
    idempotency_key: "k91ig",
    resume_data: null,
    platform_id: null,
    evidence_url: null,
    title: "Clip 91",
    caption: "GTA 6: reacción",
    r2_key: "clips/sebastian/91/x.mp4",
    language: "es",
    posted_day: null,
    attempts: 0,
    last_error: null,
    retryable: 1,
    next_run_at: B_NOW - 30 * B_MIN,
    created_at: B_NOW - 60 * B_MIN,
    updated_at: B_NOW - 30 * B_MIN,
    claimed_by: null,
    run_token: null,
    slot_day: null,
    manual: 0,
    ...over,
  } as JobRow;
  raw.exec(
    "INSERT INTO jobs (id, tenant_id, kind, clip_id, platform, api_account_id, state, idempotency_key, resume_data, " +
      "platform_id, evidence_url, title, caption, r2_key, language, posted_day, attempts, last_error, retryable, " +
      "next_run_at, created_at, updated_at, claimed_by, run_token, slot_day, manual) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    j.id,
    j.tenant_id,
    j.kind,
    j.clip_id,
    j.platform,
    j.api_account_id,
    j.state,
    j.idempotency_key,
    j.resume_data,
    j.platform_id,
    j.evidence_url,
    j.title,
    j.caption,
    j.r2_key,
    j.language,
    j.posted_day,
    j.attempts,
    j.last_error,
    j.retryable,
    j.next_run_at,
    j.created_at,
    j.updated_at,
    j.claimed_by,
    j.run_token,
    j.slot_day,
    j.manual,
  );
}

async function bSeedWal(env: Env, entries: Array<Record<string, unknown>>): Promise<void> {
  for (const e of entries) {
    await appendWalEntry(env, "sebastian", e as never);
  }
}

function bWalLines(bucket: BFakeBucket, day = B_DAY): Array<Record<string, unknown>> {
  const text = bucket.text(`wal/sebastian/${day}.jsonl`) ?? "";
  return text
    .split("\n")
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l) as Record<string, unknown>);
}

const bIgEmpty = async (): Promise<{ status: number; json: Record<string, unknown> }> => ({ status: 200, json: { data: [] } });

function bMockStep(next: PublishResult): {
  fn: (ctx: PublishContext, env: Env) => Promise<PublishResult>;
  calls: PublishContext[];
} {
  const calls: PublishContext[] = [];
  const fn = async (ctx: PublishContext, _env: Env): Promise<PublishResult> => {
    calls.push(ctx);
    return next;
  };
  return { fn, calls };
}

describe("V4 — punto 4: el respaldo rechaza la marca propia sin llamar al adaptador", () => {
  test("job con [clip:91] → error en el diario, el adaptador inyectado NO se llama", async () => {
    const { env, raw, bucket } = await bMakeFixture();
    bSeedJob(raw, { caption: "GTA 6: reacción\n[clip:91]" });
    await bSeedWal(env, [
      { type: "intent", clip_id: 91, platform: "instagram_reels", account_id: 1, job_id: 1, idempotency_key: "k91ig", day: B_DAY },
    ]);
    const step = bMockStep({ mode: "posted", platformId: "ig9", evidenceUrl: "https://www.instagram.com/p/ig9/" });
    const r = await runBackupCycle(env, { now: B_NOW, uploadStep: step.fn, fetchJson: bIgEmpty });
    expect(step.calls.length).toBe(0);
    expect(r.errors).toBe(1);
    expect(r.attempted).toBe(0);
    expect(r.attempts[0]?.action).toBe("error");
    expect(r.attempts[0]?.reason).toContain("[clip:91]");
    const results = bWalLines(bucket).filter((l) => l.type === "result");
    expect(results).toHaveLength(1);
    expect(results[0]).toMatchObject({ outcome: "error", job_id: 1 });
    expect(String(results[0]?.error)).toContain("[clip:91]");
  });
});
