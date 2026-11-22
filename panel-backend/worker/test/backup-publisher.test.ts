// Tests del publicador de respaldo B5 (2026-11-20).
//
// Sin red real, sin D1 real, sin subidas reales: R2 falso en
// memoria, D1 sobre SQLite real (bun:sqlite, mismo motor que D1),
// clientes de plataforma mockeados (uploadStep y fetchJson
// inyectados). Lo que se verifica:
//   - la espera de 15 min (dentro de la ventana no se toca; justo
//     al cumplirla, sí),
//   - la re-comprobación B2 antes de subir (diario y plataforma),
//   - la caída a mitad de subida sin duplicado en la siguiente
//     corrida,
//   - que el respaldo NUNCA escribe en D1 (la tabla jobs queda
//     byte a byte igual tras un ciclo que publica),
//   - el resume multi-step en R2 entre corridas.
// Los adaptadores reales no se ejecutan aquí: el paso de subida
// es un mock que registra las llamadas.

import { describe, expect, test, afterEach } from "bun:test";
import { Database } from "bun:sqlite";
import { encryptCredential } from "../src/crypto";
import { santiagoDayKey } from "../src/gate";
import { appendWalEntry } from "../src/wal";
import {
  BACKUP_CONFIG,
  asReadOnlyDb,
  isWithinMainWindow,
  jobScheduledMs,
  runBackupCycle,
  type BackupCycleResult,
} from "../src/backup-publisher";
import type { D1Database, Env, R2Bucket } from "../src/cloudflare";
import type { PublishContext, PublishResult } from "../src/publish/index";
import type { JobRow } from "../src/jobs";

// 2026-11-20 12:00 -03 (15:00 UTC), día Santiago 2026-11-20.
const NOW = Date.parse("2026-11-20T15:00:00Z");
const MIN = 60_000;
const DAY = "2026-11-20";
const KEY_B64 = Buffer.from(new Uint8Array(32)).toString("base64");

// --- Shim D1 sobre bun:sqlite (mismo que event-dedupe.test.ts) ---
class SqliteStmt {
  constructor(
    private db: Database,
    private sql: string,
    private params: unknown[] = [],
  ) {}
  bind(...values: unknown[]): SqliteStmt {
    return new SqliteStmt(this.db, this.sql, values);
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

function makeDb(): { d1: D1Database; raw: Database } {
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
  const d1 = { prepare: (sql: string) => new SqliteStmt(raw, sql) } as unknown as D1Database;
  return { d1, raw };
}

// --- R2 falso en memoria ---
class FakeBucket {
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

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

interface Fixture {
  env: Env;
  raw: Database;
  bucket: FakeBucket;
}

async function makeFixture(): Promise<Fixture> {
  const { d1, raw } = makeDb();
  const bucket = new FakeBucket();
  const env = {
    DB: d1,
    CLIPS_BUCKET: bucket as unknown as R2Bucket,
    TENANT: "sebastian",
    SESSION_SECRET: KEY_B64,
    CREDENTIAL_ENCRYPTION_KEY: KEY_B64,
    BRIDGE_TOKEN: "",
    GITHUB_TOKEN: "",
    ALLOWED_EMAIL: "",
    GITHUB_REPO: "exe2027/viral-clip-agent",
    CLIP_PUBLIC_BASE_URL: "https://pub.example/r2.dev",
  } as unknown as Env;
  // Clip 91 + cuentas (meta ES e youtube ES, cifradas de verdad).
  raw.exec("INSERT INTO clips (id, tenant_id, archived, status, language, title) VALUES (91, 'sebastian', 0, 'aprobado', 'es', 'Clip 91')");
  const metaCred = await encryptCredential("page-token", KEY_B64, "sebastian");
  const metaExtra = await encryptCredential(JSON.stringify({ ig_user_id: "111", page_id: "222" }), KEY_B64, "sebastian");
  raw.exec(
    "INSERT INTO api_accounts (id, tenant_id, platform, language, label, credential, credential_enc, extra, extra_enc, status, verify_ok) " +
      "VALUES (1, 'sebastian', 'meta', 'es', 'Meta ES', '', ?, '', ?, 'conectada', 1)",
    metaCred,
    metaExtra,
  );
  const ytCred = await encryptCredential("dummy-api-key", KEY_B64, "sebastian");
  const ytExtra = await encryptCredential(JSON.stringify({ client_id: "cid", client_secret: "csec", refresh_token: "rtok" }), KEY_B64, "sebastian");
  raw.exec(
    "INSERT INTO api_accounts (id, tenant_id, platform, language, label, credential, credential_enc, extra, extra_enc, status, verify_ok) " +
      "VALUES (2, 'sebastian', 'youtube', 'es', 'YT ES', '', ?, '', ?, 'conectada', 1)",
    ytCred,
    ytExtra,
  );
  // MP4 del clip en R2 (para el head de tamaño).
  await bucket.put("clips/sebastian/91/x.mp4", new Uint8Array(100).buffer as ArrayBuffer);
  return { env, raw, bucket };
}

function seedJob(
  raw: Database,
  over: Partial<JobRow> = {},
): void {
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
    next_run_at: NOW - 30 * MIN,
    created_at: NOW - 60 * MIN,
    updated_at: NOW - 30 * MIN,
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

async function seedWal(env: Env, entries: Array<Record<string, unknown>>): Promise<void> {
  for (const e of entries) {
    await appendWalEntry(env, "sebastian", e as never);
  }
}

function walText(bucket: FakeBucket, day = DAY): string {
  return bucket.text(`wal/sebastian/${day}.jsonl`) ?? "";
}

function walLines(bucket: FakeBucket, day = DAY): Array<Record<string, unknown>> {
  return walText(bucket, day)
    .split("\n")
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l) as Record<string, unknown>);
}

/** Plataforma IG vacía: ningún vídeo reciente. */
const igEmpty = async (): Promise<{ status: number; json: Record<string, unknown> }> => ({ status: 200, json: { data: [] } });

/** Plataforma IG con el vídeo del clip 91 ya publicado (marca). */
const igWithClip91 = async (): Promise<{ status: number; json: Record<string, unknown> }> => ({
  status: 200,
  json: { data: [{ id: "ig1", caption: "GTA 6: reacción\n[clip:91]", permalink: "https://www.instagram.com/p/ig1/" }] },
});

function mockStep(next: PublishResult | ((ctx: PublishContext) => PublishResult | Promise<PublishResult>)): {
  fn: (ctx: PublishContext, env: Env) => Promise<PublishResult>;
  calls: PublishContext[];
} {
  const calls: PublishContext[] = [];
  const fn = async (ctx: PublishContext, _env: Env): Promise<PublishResult> => {
    calls.push(ctx);
    return typeof next === "function" ? await (next as (c: PublishContext) => PublishResult | Promise<PublishResult>)(ctx) : next;
  };
  return { fn, calls };
}

function snapshotJobs(raw: Database): string {
  return JSON.stringify(raw.query("SELECT * FROM jobs ORDER BY id").all());
}

describe("B5 — ventana de 15 minutos (un solo publicador a la vez)", () => {
  test("isWithinMainWindow: dentro de la ventana, justo al cumplirla y hora futura", () => {
    const base = { next_run_at: NOW - 5 * MIN, created_at: NOW - 60 * MIN } as JobRow;
    expect(isWithinMainWindow(base, NOW)).toBe(true);
    expect(isWithinMainWindow({ ...base, next_run_at: NOW - BACKUP_CONFIG.MAIN_WINDOW_MS }, NOW)).toBe(false);
    expect(isWithinMainWindow({ ...base, next_run_at: NOW - BACKUP_CONFIG.MAIN_WINDOW_MS + 1 }, NOW)).toBe(true);
    expect(isWithinMainWindow({ ...base, next_run_at: NOW + 5 * MIN }, NOW)).toBe(true);
    // Manual (next_run_at null): la hora es created_at.
    expect(jobScheduledMs({ next_run_at: null, created_at: NOW - 30 * MIN } as JobRow)).toBe(NOW - 30 * MIN);
    expect(isWithinMainWindow({ next_run_at: null, created_at: NOW - 5 * MIN } as JobRow, NOW)).toBe(true);
  });

  test("job con hora hace 5 min: no se toca (ni se llama al adaptador)", async () => {
    const { env, raw, bucket } = await makeFixture();
    seedJob(raw, { next_run_at: NOW - 5 * MIN });
    const step = mockStep({ mode: "posted", platformId: "x", evidenceUrl: "https://example/x" });
    const r = await runBackupCycle(env, { now: NOW, uploadStep: step.fn, fetchJson: igEmpty });
    expect(step.calls.length).toBe(0);
    expect(r.skipped).toBe(1);
    expect(r.attempts[0]?.reason).toMatch(/ventana del publicador principal/);
    expect(bucket.puts.filter((k) => k.startsWith("wal/")).length).toBe(0);
  });

  test("job con hora hace 15 min exactos: ya se puede intentar", async () => {
    const { env, raw } = await makeFixture();
    seedJob(raw, { next_run_at: NOW - BACKUP_CONFIG.MAIN_WINDOW_MS });
    const step = mockStep({ mode: "posted", platformId: "ig9", evidenceUrl: "https://www.instagram.com/p/ig9/" });
    const r = await runBackupCycle(env, { now: NOW, uploadStep: step.fn, fetchJson: igEmpty });
    expect(step.calls.length).toBe(1);
    expect(r.posted).toBe(1);
  });

  test("job con hora futura: no se toca", async () => {
    const { env, raw } = await makeFixture();
    seedJob(raw, { next_run_at: NOW + 10 * MIN });
    const step = mockStep({ mode: "posted", platformId: "x", evidenceUrl: "https://example/x" });
    const r = await runBackupCycle(env, { now: NOW, uploadStep: step.fn, fetchJson: igEmpty });
    expect(step.calls.length).toBe(0);
    expect(r.skipped).toBe(1);
  });
});

describe("B5 — rescate y resultado en el diario", () => {
  test("intención WAL sin resultado + ventana pasada → se sube y deja resultado posted", async () => {
    const { env, raw, bucket } = await makeFixture();
    seedJob(raw);
    await seedWal(env, [
      { type: "intent", clip_id: 91, platform: "instagram_reels", account_id: 1, job_id: 1, idempotency_key: "k91ig", day: DAY },
    ]);
    const step = mockStep({ mode: "posted", platformId: "ig9", evidenceUrl: "https://www.instagram.com/p/ig9/" });
    const r = await runBackupCycle(env, { now: NOW, uploadStep: step.fn, fetchJson: igEmpty });
    expect(step.calls.length).toBe(1);
    expect(r.posted).toBe(1);
    const results = walLines(bucket).filter((l) => l.type === "result");
    expect(results.length).toBe(1);
    expect(results[0]).toMatchObject({ outcome: "posted", job_id: 1, platform_id: "ig9", url: "https://www.instagram.com/p/ig9/" });
    // El caption que viaja al adaptador lleva la marca [clip:91].
    expect(step.calls[0]?.job.caption).toContain("[clip:91]");
  });

  test("resultado posted en el diario → no se vuelve a intentar (ni se consulta la plataforma)", async () => {
    const { env, raw } = await makeFixture();
    seedJob(raw);
    await seedWal(env, [
      { type: "intent", clip_id: 91, platform: "instagram_reels", account_id: 1, job_id: 1, idempotency_key: "k91ig", day: DAY },
      { type: "result", clip_id: 91, platform: "instagram_reels", account_id: 1, job_id: 1, idempotency_key: "k91ig", day: DAY, outcome: "posted", platform_id: "ig9", url: "https://www.instagram.com/p/ig9/" },
    ]);
    let fetchCalled = false;
    const step = mockStep({ mode: "posted", platformId: "x", evidenceUrl: "https://example/x" });
    const r = await runBackupCycle(env, {
      now: NOW,
      uploadStep: step.fn,
      fetchJson: async () => {
        fetchCalled = true;
        return { status: 200, json: { data: [] } };
      },
    });
    expect(step.calls.length).toBe(0);
    expect(fetchCalled).toBe(false);
    expect(r.candidates).toBe(0);
  });

  test("job en D1 sin intención WAL (el principal nunca empezó) → se rescata", async () => {
    const { env, raw, bucket } = await makeFixture();
    seedJob(raw); // Sin ninguna entrada en el diario.
    const step = mockStep({ mode: "posted", platformId: "ig9", evidenceUrl: "https://www.instagram.com/p/ig9/" });
    const r = await runBackupCycle(env, { now: NOW, uploadStep: step.fn, fetchJson: igEmpty });
    expect(step.calls.length).toBe(1);
    expect(r.posted).toBe(1);
    expect(walLines(bucket).some((l) => l.type === "result" && l.outcome === "posted")).toBe(true);
  });

  test("job en draft en D1 → no se rescata", async () => {
    const { env, raw } = await makeFixture();
    seedJob(raw, { state: "draft" });
    const step = mockStep({ mode: "posted", platformId: "x", evidenceUrl: "https://example/x" });
    const r = await runBackupCycle(env, { now: NOW, uploadStep: step.fn, fetchJson: igEmpty });
    expect(step.calls.length).toBe(0);
    expect(r.candidates).toBe(0);
  });

  test("intención WAL sin fila en D1 → se salta sin tumbar el ciclo", async () => {
    const { env, bucket } = await makeFixture();
    await seedWal(env, [
      { type: "intent", clip_id: 91, platform: "instagram_reels", account_id: 1, job_id: 999, idempotency_key: "k999", day: DAY },
    ]);
    const step = mockStep({ mode: "posted", platformId: "x", evidenceUrl: "https://example/x" });
    const r: BackupCycleResult = await runBackupCycle(env, { now: NOW, uploadStep: step.fn, fetchJson: igEmpty });
    expect(step.calls.length).toBe(0);
    expect(r.skipped).toBe(1);
    expect(r.attempts[0]?.reason).toMatch(/ya no existe en D1/);
    expect(bucket.text("wal/sebastian/2026-11-20.jsonl")).toContain("job_id\":999");
  });
});

describe("B5 — re-comprobación B2 antes de subir", () => {
  test("la plataforma ya tiene el vídeo (marca) → no se sube, resultado already_published", async () => {
    const { env, raw, bucket } = await makeFixture();
    seedJob(raw);
    await seedWal(env, [
      { type: "intent", clip_id: 91, platform: "instagram_reels", account_id: 1, job_id: 1, idempotency_key: "k91ig", day: DAY },
    ]);
    const step = mockStep({ mode: "posted", platformId: "x", evidenceUrl: "https://example/x" });
    const r = await runBackupCycle(env, { now: NOW, uploadStep: step.fn, fetchJson: igWithClip91 });
    expect(step.calls.length).toBe(0);
    expect(r.alreadyPublished).toBe(1);
    const results = walLines(bucket).filter((l) => l.type === "result");
    expect(results[0]).toMatchObject({ outcome: "already_published", platform_id: "ig1", url: "https://www.instagram.com/p/ig1/" });
  });

  test("YouTube: la plataforma ya tiene el vídeo por la marca → no se sube", async () => {
    const { env, raw, bucket } = await makeFixture();
    seedJob(raw, { platform: "youtube_shorts", api_account_id: 2, idempotency_key: "k91yt" });
    await seedWal(env, [
      { type: "intent", clip_id: 91, platform: "youtube_shorts", account_id: 2, job_id: 1, idempotency_key: "k91yt", day: DAY },
    ]);
    // El refresh del token va por fetch global; el listado por fetchJson inyectado.
    globalThis.fetch = (async (url: unknown) => {
      if (String(url).includes("oauth2.googleapis.com/token")) {
        return new Response(JSON.stringify({ access_token: "tok" }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      throw new Error(`fetch no esperado: ${String(url)}`);
    }) as typeof fetch;
    const fetchJson = async (url: string): Promise<{ status: number; json: Record<string, unknown> }> => {
      if (url.includes("/channels?")) {
        return { status: 200, json: { items: [{ contentDetails: { relatedPlaylists: { uploads: "UU123" } } }] } };
      }
      if (url.includes("/playlistItems?")) {
        return {
          status: 200,
          json: { items: [{ snippet: { description: "GTA 6: reacción\n[clip:91]", resourceId: { videoId: "abc123" } } }] },
        };
      }
      throw new Error(`fetchJson no esperado: ${url}`);
    };
    const step = mockStep({ mode: "posted", platformId: "x", evidenceUrl: "https://example/x" });
    const r = await runBackupCycle(env, { now: NOW, uploadStep: step.fn, fetchJson });
    expect(step.calls.length).toBe(0);
    expect(r.alreadyPublished).toBe(1);
    expect(walLines(bucket).filter((l) => l.type === "result")[0]).toMatchObject({
      outcome: "already_published",
      url: "https://www.youtube.com/shorts/abc123",
    });
  });
});

describe("B5 — caída a mitad de subida sin duplicado", () => {
  test("el adaptador sube pero el proceso muere antes del resultado: la siguiente corrida lo encuentra en la plataforma y no duplica", async () => {
    const { env, raw, bucket } = await makeFixture();
    seedJob(raw);
    await seedWal(env, [
      { type: "intent", clip_id: 91, platform: "instagram_reels", account_id: 1, job_id: 1, idempotency_key: "k91ig", day: DAY },
    ]);
    // La plataforma falsa: el vídeo aparece ahí cuando el mock "sube".
    const platformVideos: Array<{ id: string; caption: string }> = [];
    const fetchJson = async (): Promise<{ status: number; json: Record<string, unknown> }> => ({
      status: 200,
      json: { data: platformVideos.map((v) => ({ id: v.id, caption: v.caption, permalink: `https://www.instagram.com/p/${v.id}/` })) },
    });
    // Corrida 1: el mock registra el vídeo en la plataforma y LUEGO
    // lanza (muerte del proceso antes de escribir el resultado).
    const crash = mockStep(async () => {
      platformVideos.push({ id: "ig1", caption: "GTA 6: reacción\n[clip:91]" });
      throw new Error("Worker muerto a mitad de subida (simulado)");
    });
    const r1 = await runBackupCycle(env, { now: NOW, uploadStep: crash.fn, fetchJson });
    expect(crash.calls.length).toBe(1);
    expect(r1.errors).toBe(1);
    expect(platformVideos.length).toBe(1);
    // La corrida 1 dejó un error en el diario (auditoría), no un posted.
    expect(walLines(bucket).filter((l) => l.type === "result" && l.outcome === "posted").length).toBe(0);
    // Corrida 2, 61 min después (pasado el enfriamiento de 60 min):
    // B2 encuentra el vídeo en la plataforma y NO vuelve a subir.
    const step2 = mockStep({ mode: "posted", platformId: "ig2", evidenceUrl: "https://www.instagram.com/p/ig2/" });
    const r2 = await runBackupCycle(env, { now: NOW + 61 * MIN, uploadStep: step2.fn, fetchJson });
    expect(step2.calls.length).toBe(0);
    expect(r2.alreadyPublished).toBe(1);
    expect(platformVideos.length).toBe(1);
    expect(walLines(bucket).filter((l) => l.type === "result" && l.outcome === "already_published").length).toBe(1);
  });

  test("error reciente en el diario → enfriamiento de 60 min (no se reintenta en la misma hora)", async () => {
    const { env, raw } = await makeFixture();
    seedJob(raw);
    await seedWal(env, [
      { type: "intent", clip_id: 91, platform: "instagram_reels", account_id: 1, job_id: 1, idempotency_key: "k91ig", day: DAY },
      { type: "result", clip_id: 91, platform: "instagram_reels", account_id: 1, job_id: 1, idempotency_key: "k91ig", day: DAY, outcome: "error", error: "5xx", at: new Date(NOW - 30 * MIN).toISOString() },
    ]);
    const step = mockStep({ mode: "posted", platformId: "x", evidenceUrl: "https://example/x" });
    const r = await runBackupCycle(env, { now: NOW, uploadStep: step.fn, fetchJson: igEmpty });
    expect(step.calls.length).toBe(0);
    expect(r.attempts[0]?.reason).toMatch(/enfriamiento/);
    // 61 min después del error, el enfriamiento ya pasó.
    const r2 = await runBackupCycle(env, { now: NOW + 31 * MIN, uploadStep: step.fn, fetchJson: igEmpty });
    expect(step.calls.length).toBe(1);
    expect(r2.posted).toBe(1);
  });

  test("3 errores seguidos en el diario → no se reintenta (lo revisa el humano)", async () => {
    const { env, raw } = await makeFixture();
    seedJob(raw);
    const entries: Array<Record<string, unknown>> = [
      { type: "intent", clip_id: 91, platform: "instagram_reels", account_id: 1, job_id: 1, idempotency_key: "k91ig", day: DAY },
    ];
    for (const h of [4, 3, 2]) {
      entries.push({
        type: "result",
        clip_id: 91,
        platform: "instagram_reels",
        account_id: 1,
        job_id: 1,
        idempotency_key: "k91ig",
        day: DAY,
        outcome: "error",
        error: "fallo",
        at: new Date(NOW - h * 60 * MIN).toISOString(),
      });
    }
    await seedWal(env, entries);
    const step = mockStep({ mode: "posted", platformId: "x", evidenceUrl: "https://example/x" });
    const r = await runBackupCycle(env, { now: NOW, uploadStep: step.fn, fetchJson: igEmpty });
    expect(step.calls.length).toBe(0);
    expect(r.attempts[0]?.reason).toMatch(/3 errores seguidos/);
  });
});

describe("B5 — resume multi-step entre corridas (R2, no D1)", () => {
  test("processing guarda el resume en R2; la siguiente corrida lo reusa y al publicar lo borra", async () => {
    const { env, raw, bucket } = await makeFixture();
    seedJob(raw, { platform: "youtube_shorts", api_account_id: 2, idempotency_key: "k91yt" });
    const resume = { step: "upload", sessionUrl: "https://sess", bytesUploaded: 0, totalBytes: 100 };
    const step1 = mockStep({ mode: "processing", resumeData: resume, reason: "sesión creada" });
    const r1 = await runBackupCycle(env, { now: NOW, uploadStep: step1.fn, fetchJson: igEmpty });
    expect(r1.inProgress).toBe(1);
    const saved = bucket.text("backup-state/sebastian/1.json");
    expect(saved).toContain("https://sess");
    expect(walLines(bucket).filter((l) => l.type === "result").length).toBe(0);
    // Corrida 2: el mock recibe el resume guardado y publica.
    const step2 = mockStep({ mode: "posted", platformId: "abc", evidenceUrl: "https://www.youtube.com/shorts/abc" });
    const r2 = await runBackupCycle(env, { now: NOW + 15 * MIN, uploadStep: step2.fn, fetchJson: igEmpty });
    expect(step2.calls.length).toBe(1);
    expect(step2.calls[0]?.job.resumeData).toEqual(resume);
    expect(r2.posted).toBe(1);
    expect(bucket.text("backup-state/sebastian/1.json")).toBeNull();
    expect(walLines(bucket).filter((l) => l.type === "result" && l.outcome === "posted").length).toBe(1);
  });
});

describe("B5 — la D1 no se escribe nunca", () => {
  test("asReadOnlyDb: las lecturas pasan, las escrituras lanzan", async () => {
    const { d1, raw } = makeDb();
    seedJob(raw);
    const ro = asReadOnlyDb(d1);
    const row = await ro.prepare("SELECT * FROM jobs WHERE id = ?").bind(1).first<JobRow>();
    expect(row?.id).toBe(1);
    expect(() => ro.prepare("UPDATE jobs SET state = 'posted' WHERE id = 1")).toThrow(/escritura D1 bloqueada/);
    expect(() => ro.prepare("INSERT INTO jobs (id) VALUES (2)")).toThrow(/escritura D1 bloqueada/);
    expect(() => ro.prepare("DELETE FROM jobs WHERE id = 1")).toThrow(/escritura D1 bloqueada/);
    await expect(ro.batch([])).rejects.toThrow(/escritura D1 bloqueada/);
    await expect(ro.exec("SELECT 1")).rejects.toThrow(/escritura D1 bloqueada/);
    expect(raw.query("SELECT COUNT(*) AS n FROM jobs").get() as { n: number }).toEqual({ n: 1 });
  });

  test("un ciclo completo que publica deja la D1 byte a byte igual", async () => {
    const { env, raw } = await makeFixture();
    seedJob(raw);
    const before = snapshotJobs(raw);
    const step = mockStep({ mode: "posted", platformId: "ig9", evidenceUrl: "https://www.instagram.com/p/ig9/" });
    const r = await runBackupCycle(env, { now: NOW, uploadStep: step.fn, fetchJson: igEmpty });
    expect(r.posted).toBe(1);
    expect(snapshotJobs(raw)).toBe(before);
  });

  test("sin TENANT el ciclo falla en voz alta (nunca un default silencioso)", async () => {
    const { env } = await makeFixture();
    const noTenant = { ...env, TENANT: "" } as unknown as Env;
    await expect(runBackupCycle(noTenant, { now: NOW })).rejects.toThrow();
  });

  test("santiagoDayKey del día de la corrida: el diario se escribe en el archivo de hoy", async () => {
    expect(santiagoDayKey(new Date(NOW))).toBe("2026-11-20");
  });
});
