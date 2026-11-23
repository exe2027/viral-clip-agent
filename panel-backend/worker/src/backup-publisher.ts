// Publicador de respaldo en $0 — B5 (2026-11-20).
//
// Segundo publicador, Worker aparte, cron cada 15 min. No sustituye
// al publicador principal: solo rescata lo que el principal no
// publicó. Reglas duras (encargo anti-recaída 2026-11-20):
//   - $0, nada de pago. SIN escrituras en D1 (solo lecturas; la
//     cuota está agotada, 104.610/100.000, reset 21:00 -03). Las
//     escrituras van SOLO al diario WAL en R2 (intención/resultado)
//     y al estado de resume propio, también en R2.
//   - SIN deploys, SIN commits: el código queda en el árbol.
//   - Cero duplicados: un solo publicador a la vez + B2 antes de
//     subir (diario + plataforma + idempotency_key).
//   - Nada manual para el usuario. No toca el cutover 21:00.
//
// Cómo decide qué rescatar (dos fuentes, ambas de solo lectura):
//   1. El diario B1 (R2, wal/<tenant>/<fecha>.jsonl): intenciones
//      sin resultado posted/already_published. Cubre el caso "el
//      principal empezó a subir y se cayó a mitad" — la intención
//      ya está en el diario y la comprobación B2 detecta si el
//      vídeo llegó a la plataforma.
//   2. La tabla jobs en D1 (SELECT): jobs publish en
//      scheduled/processing sin resultado en el diario. Cubre el
//      caso "el principal nunca llegó a empezar" — la intención
//      B1 se escribe en el dispatch, así que un job nunca
//      reclamado no tiene intención y solo se ve en D1.
//
// Regla de un solo publicador (la ventana de 15 minutos):
//   La "hora" de un job es su next_run_at (el lease del reclamo:
//   el principal lo renueva en cada paso — processing pone
//   next_run_at = ahora + 10 s — así que un job vivo siempre tiene
//   la hora en el futuro cercano) o su created_at si next_run_at
//   es null (jobs manuales). El respaldo NUNCA toca un job dentro
//   de los 15 minutos posteriores a su hora: esa es la ventana del
//   principal. Solo actúa cuando la hora quedó 15+ min atrás, es
//   decir, cuando el principal no reclamó ni avanzó el job.
//   Si el principal vuelve, su propio ciclo reclama el job (lease
//   fresco) y el respaldo lo vuelve a saltar por la ventana.
//
// Antes de subir, re-comprobación B2 (checkDuplicateBeforeUpload,
// reutilizada, no reinventada): diario primero, plataforma
// después (YouTube/Instagram/Facebook por la marca [clip:<id>];
// TikTok sin listado: diario + idempotency_key). Si ya existe,
// no se sube: se deja resultado already_published en el diario.
//
// Si se cae a mitad de una subida, la siguiente corrida NO
// duplica: la intención ya está en el diario, el resultado no, y
// la comprobación B2 encuentra el vídeo en la plataforma (o el
// diario, si el resultado sí llegó). El estado de resume de una
// subida multi-step vive en R2 (backup-state/<tenant>/<job>.json,
// propio del respaldo, NO en el diario y NO en D1): un paso por
// corrida, como el principal (un paso por tick de cron).
//
// Por qué NO se llama a dispatchPublishStep: ese camino re-chequea
// el gate y reserva el cupo, y ambas cosas ESCRIBEN en D1. El
// respaldo llama al adaptador de plataforma directamente, con un
// Env cuya D1 es de solo lectura (asReadOnlyDb): cualquier
// escritura accidental de un adaptador lanza en vez de escribir.
// El job ya pasó los gates al encolarse; el respaldo no los
// re-evalúa (no es su trabajo: es rescatar, no re-decidir).
//
// Visibilidad: el respaldo no escribe en D1, así que el panel no
// ve el posted hasta que el ciclo del principal vuelque el diario
// (flushWal, jobs.ts): el volcado marca el job posted en D1 cuando
// la cuota vuelve. Si el principal está caído días, el panel
// muestra el job pendiente aunque ya esté publicado — la verdad
// está en el diario y B2 impide duplicarlo.
//
// Límites por corrida: máx. 5 jobs (BACKUP_CONFIG), un paso por
// job. Un rescate típico tarda 2-4 corridas (30-60 min).
// Un job con 3 errores seguidos en el diario deja de intentarse
// (lo revisa el humano o el principal); un error reciente tiene
// enfriamiento de 60 min.

import type { D1Database, Env, ExecutionContext, R2Bucket, ScheduledEvent } from "./cloudflare";
import { requireTenant, type Tenant } from "./auth";
import { clipLanguage, getClip, listSocialAccounts, type PanelPlatform } from "./db";
import { santiagoDayKey } from "./gate";
import type { JobRow } from "./jobs";
import { appendWalEntry, readWalEntries, type WalEntry } from "./wal";
import { checkDuplicateBeforeUpload, type FetchJson } from "./publish/idempotency";
import { resolveCreds, type PublishContext, type PublishResult, type ResumeData } from "./publish/index";
import { youtubeStep } from "./publish/youtube";
import { tiktokStep } from "./publish/tiktok";
import { zernioTiktokStep } from "./publish/zernio-tiktok";
import { instagramStep } from "./publish/instagram";
import { facebookStep } from "./publish/facebook";

/** Configuración del respaldo, en UN solo sitio (single-source):
 *  ningún otro archivo copia estos valores. Si un valor cambia,
 *  cambia aquí y los tests lo ven (usan BACKUP_CONFIG, no un
 *  literal copiado). Sin defaults silenciosos: requireTenant
 *  falla en voz alta sin TENANT y el ciclo exige DB y bucket. */
export const BACKUP_CONFIG = {
  /** Ventana del publicador principal tras la hora programada. */
  MAIN_WINDOW_MS: 15 * 60 * 1000,
  /** Días del diario que se leen (hoy + anteriores, Santiago). */
  WAL_LOOKBACK_DAYS: 3,
  /** Enfriamiento tras un resultado error en el diario. */
  ERROR_COOLDOWN_MS: 60 * 60 * 1000,
  /** Errores seguidos en el diario antes de dejar de intentarlo. */
  MAX_CONSECUTIVE_ERRORS: 3,
  /** Jobs avanzados por corrida (un paso por job). */
  MAX_JOBS_PER_RUN: 5,
  /** Candidatos de D1 por corrida (tope de seguridad). */
  MAX_CANDIDATES: 50,
  /** Prefijo R2 del estado de resume propio del respaldo. */
  BACKUP_STATE_PREFIX: "backup-state",
} as const;

// ---------------------------------------------------------------------------
// D1 de solo lectura (la garantía de "SIN escrituras en D1").
// ---------------------------------------------------------------------------

/** Envoltura D1 que deja pasar SELECTs (y WITH de lectura) y lanza
 *  ante cualquier otra cosa. Es una valla, no un control de
 *  acceso: el código del respaldo no escribe en D1 por diseño, y
 *  si un adaptador futuro intentara escribir, esto lo haría fallar
 *  en voz alta en vez de gastar cuota. Los logs de eventos de los
 *  adaptadores (logWorkerEvent) van con .catch(() => {}) en su
 *  propio código, así que un INSERT bloqueado ahí no tumba la
 *  subida. */
export function asReadOnlyDb(db: D1Database): D1Database {
  const isRead = (sql: string): boolean => {
    const t = sql.trim().toUpperCase();
    return t.startsWith("SELECT") || t.startsWith("WITH");
  };
  const blocked = (what: string): never => {
    throw new Error(`B5: escritura D1 bloqueada (${what}): el publicador de respaldo solo lee D1`);
  };
  return {
    prepare: (sql: string) => {
      if (!isRead(sql)) blocked(`prepare ${sql.slice(0, 80)}`);
      return db.prepare(sql);
    },
    batch: async () => blocked("batch"),
    exec: async () => blocked("exec"),
  } as D1Database;
}

// ---------------------------------------------------------------------------
// Estado de resume propio (R2, no D1, no diario).
// ---------------------------------------------------------------------------

export interface BackupState {
  v: 1;
  job_id: number;
  /** resume_data del adaptador, serializado (igual que jobs.resume_data). */
  resume_data: string | null;
  updated_at: string;
}

export function backupStateKey(tenant: Tenant, jobId: number): string {
  return `${BACKUP_CONFIG.BACKUP_STATE_PREFIX}/${tenant}/${jobId}.json`;
}

async function readBackupState(bucket: R2Bucket, tenant: Tenant, jobId: number): Promise<BackupState | null> {
  try {
    const obj = await bucket.get(backupStateKey(tenant, jobId));
    if (!obj) return null;
    const parsed = JSON.parse(new TextDecoder().decode(await obj.arrayBuffer())) as BackupState;
    return parsed && parsed.v === 1 && parsed.job_id === jobId ? parsed : null;
  } catch {
    return null;
  }
}

async function saveBackupState(bucket: R2Bucket, tenant: Tenant, jobId: number, resumeData: ResumeData): Promise<void> {
  const state: BackupState = { v: 1, job_id: jobId, resume_data: JSON.stringify(resumeData), updated_at: new Date().toISOString() };
  await bucket.put(backupStateKey(tenant, jobId), JSON.stringify(state), { httpMetadata: { contentType: "application/json" } }).catch(() => undefined);
}

async function clearBackupState(bucket: R2Bucket, tenant: Tenant, jobId: number): Promise<void> {
  await bucket.delete(backupStateKey(tenant, jobId)).catch(() => undefined);
}

function parseResumeData(raw: string | null | undefined): ResumeData | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as ResumeData;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Hora programada y ventana del principal.
// ---------------------------------------------------------------------------

/** Hora programada de un job: next_run_at (lease del reclamo) o
 *  created_at si es null (jobs manuales, next_run_at null = ya). */
export function jobScheduledMs(job: JobRow): number {
  return job.next_run_at ?? job.created_at;
}

/** ¿Está el job dentro de la ventana del publicador principal?
 *  Dentro = aún no llegó su hora, o llegó hace menos de 15 min.
 *  Fuera = su hora quedó 15+ min atrás sin que el principal lo
 *  reclamara o avanzara: ahí sí lo toca el respaldo. */
export function isWithinMainWindow(job: JobRow, nowMs: number): boolean {
  const hora = jobScheduledMs(job);
  return nowMs < hora || nowMs - hora < BACKUP_CONFIG.MAIN_WINDOW_MS;
}

/** Días del diario que cubre el respaldo (hoy + anteriores,
 *  America/Santiago). */
export function walDayKeys(nowMs: number, days = BACKUP_CONFIG.WAL_LOOKBACK_DAYS): string[] {
  const out: string[] = [];
  for (let i = days - 1; i >= 0; i--) {
    const d = santiagoDayKey(new Date(nowMs - i * 24 * 3600 * 1000));
    if (!out.includes(d)) out.push(d);
  }
  return out;
}

/** Entradas del diario de los días cubiertos (solo lectura R2). */
export async function loadBackupWalEntries(env: Env, tenant: Tenant, nowMs: number): Promise<WalEntry[]> {
  const entries: WalEntry[] = [];
  for (const day of walDayKeys(nowMs)) {
    entries.push(...(await readWalEntries(env, tenant, day)));
  }
  return entries;
}

// ---------------------------------------------------------------------------
// Pendientes: qué intenciones no tienen resultado, qué jobs no
// tienen intención. Puro sobre las entradas + las filas de D1.
// ---------------------------------------------------------------------------

export interface PendingCandidate {
  jobId: number;
  /** "wal": el principal empezó (hay intención); "d1": nunca empezó. */
  source: "wal" | "d1";
  intentAt: string | null;
}

/** ¿Hay en estas entradas un resultado posted/already_published
 *  para este job? Misma semántica que walFindPosted (por job, por
 *  clip+plataforma o por idempotency_key). */
export function walPostedFor(
  entries: WalEntry[],
  jobId: number,
  clipId: number | null,
  platform: PanelPlatform | null,
  idempotencyKey: string | null,
): WalEntry | null {
  for (const e of entries) {
    if (e.type !== "result" || (e.outcome !== "posted" && e.outcome !== "already_published")) continue;
    if (!e.url || !e.platform_id) continue;
    const byJob = e.job_id === jobId;
    const byKey = !!idempotencyKey && !!e.idempotency_key && e.idempotency_key === idempotencyKey;
    const byClip = clipId !== null && platform !== null && e.clip_id === clipId && e.platform === platform;
    if (byJob || byKey || byClip) return e;
  }
  return null;
}

/** Último resultado error de este job en estas entradas (o null). */
export function walLastErrorFor(entries: WalEntry[], jobId: number): WalEntry | null {
  let last: WalEntry | null = null;
  for (const e of entries) {
    if (e.type === "result" && e.outcome === "error" && e.job_id === jobId) {
      if (!last || e.at > last.at) last = e;
    }
  }
  return last;
}

/** Nº de resultados error de este job en estas entradas. */
export function walErrorCountFor(entries: WalEntry[], jobId: number): number {
  let n = 0;
  for (const e of entries) {
    if (e.type === "result" && e.outcome === "error" && e.job_id === jobId) n++;
  }
  return n;
}

/** Candidatos pendientes: intenciones sin resultado posted, más
 *  jobs scheduled/processing en D1 sin resultado en el diario.
 *  Un job con resultado posted no es candidato por ninguna vía. */
export function resolvePendingCandidates(entries: WalEntry[], d1jobs: JobRow[]): PendingCandidate[] {
  const out = new Map<number, PendingCandidate>();
  const intents = new Map<number, WalEntry>();
  for (const e of entries) {
    if (e.type !== "intent") continue;
    const prev = intents.get(e.job_id);
    if (!prev || e.at > prev.at) intents.set(e.job_id, e);
  }
  for (const [jobId, intent] of intents) {
    if (walPostedFor(entries, jobId, intent.clip_id, intent.platform, intent.idempotency_key)) continue;
    out.set(jobId, { jobId, source: "wal", intentAt: intent.at });
  }
  for (const row of d1jobs) {
    if (out.has(row.id)) continue;
    if (walPostedFor(entries, row.id, row.clip_id, row.platform, row.idempotency_key)) continue;
    out.set(row.id, { jobId: row.id, source: "d1", intentAt: null });
  }
  return [...out.values()];
}

/** Jobs publish en scheduled/processing (solo lectura D1). */
async function listRescuableJobs(db: D1Database, tenant: Tenant): Promise<JobRow[]> {
  const res = await db
    .prepare(
      "SELECT * FROM jobs WHERE tenant_id = ? AND kind = 'publish' AND state IN ('scheduled','processing') AND platform IS NOT NULL ORDER BY next_run_at ASC LIMIT ?",
    )
    .bind(tenant, BACKUP_CONFIG.MAX_CANDIDATES)
    .all<JobRow>()
    .catch(() => null);
  return res?.results ?? [];
}

// ---------------------------------------------------------------------------
// Un paso de subida (el adaptador, sin gate ni reserva: eso
// escribiría en D1). Inyectable en tests con clientes mockeados.
// ---------------------------------------------------------------------------

export type BackupUploadStep = (ctx: PublishContext, env: Env) => Promise<PublishResult>;

export async function defaultBackupUploadStep(ctx: PublishContext, env: Env): Promise<PublishResult> {
  switch (ctx.job.platform) {
    case "youtube_shorts":
      return youtubeStep(ctx, env);
    case "tiktok":
      return ctx.creds.tiktokVia === "zernio" ? zernioTiktokStep(ctx, env) : tiktokStep(ctx, env);
    case "instagram_reels":
      return instagramStep(ctx, env);
    case "facebook_reels":
      return facebookStep(ctx, env);
    default:
      return { mode: "draft", reason: `plataforma desconocida: ${String(ctx.job.platform)}` };
  }
}

// ---------------------------------------------------------------------------
// La corrida (cron cada 15 min).
// ---------------------------------------------------------------------------

export interface BackupJobAttempt {
  jobId: number;
  clipId: number | null;
  platform: PanelPlatform | null;
  action: "posted" | "already_published" | "in_progress" | "draft" | "error" | "skipped";
  reason?: string;
  url?: string;
}

export interface BackupCycleResult {
  tenant: Tenant;
  at: string;
  candidates: number;
  attempted: number;
  posted: number;
  alreadyPublished: number;
  inProgress: number;
  drafts: number;
  errors: number;
  skipped: number;
  attempts: BackupJobAttempt[];
}

export interface RunBackupOptions {
  now?: number;
  /** Paso de subida (por defecto, los adaptadores reales). */
  uploadStep?: BackupUploadStep;
  /** Cliente HTTP de la comprobación B2 (por defecto, httpJson). */
  fetchJson?: FetchJson;
}

export async function runBackupCycle(env: Env, opts: RunBackupOptions = {}): Promise<BackupCycleResult> {
  const tenant = requireTenant(env);
  if (!env.DB || !env.CLIPS_BUCKET) {
    throw new Error("B5: faltan los bindings DB o CLIPS_BUCKET en el Worker de respaldo (revisar wrangler.backup.*.toml)");
  }
  const nowMs = opts.now ?? Date.now();
  const uploadStep = opts.uploadStep ?? defaultBackupUploadStep;
  const result: BackupCycleResult = {
    tenant,
    at: new Date(nowMs).toISOString(),
    candidates: 0,
    attempted: 0,
    posted: 0,
    alreadyPublished: 0,
    inProgress: 0,
    drafts: 0,
    errors: 0,
    skipped: 0,
    attempts: [],
  };
  const skip = (jobId: number, clipId: number | null, platform: PanelPlatform | null, reason: string): void => {
    result.skipped++;
    result.attempts.push({ jobId, clipId, platform, action: "skipped", reason });
  };

  const entries = await loadBackupWalEntries(env, tenant, nowMs);
  const d1jobs = await listRescuableJobs(env.DB, tenant);
  const candidates = resolvePendingCandidates(entries, d1jobs);
  result.candidates = candidates.length;

  // Hora programada más antigua primero; tope por corrida.
  const jobs: JobRow[] = [];
  for (const c of candidates) {
    const row = await env.DB.prepare("SELECT * FROM jobs WHERE id = ? AND tenant_id = ?")
      .bind(c.jobId, tenant)
      .first<JobRow>()
      .catch(() => null);
    if (!row) {
      skip(c.jobId, null, null, "el job ya no existe en D1 (solo queda su intención en el diario)");
      continue;
    }
    jobs.push(row);
  }
  jobs.sort((a, b) => jobScheduledMs(a) - jobScheduledMs(b));
  const dayKey = santiagoDayKey(new Date(nowMs));

  for (const job of jobs.slice(0, BACKUP_CONFIG.MAX_JOBS_PER_RUN)) {
    const base = { jobId: job.id, clipId: job.clip_id, platform: job.platform };
    // 1. Solo scheduled/processing se rescatan (un failed/draft en
    //    D1 ya lo decidió el principal; un posted no se toca).
    if (job.state !== "scheduled" && job.state !== "processing") {
      skip(job.id, job.clip_id, job.platform, `el job está en estado ${job.state} en D1`);
      continue;
    }
    if (job.kind !== "publish" || !job.platform || !job.clip_id) {
      skip(job.id, job.clip_id, job.platform, "job sin plataforma/clip o no es publish");
      continue;
    }
    // 2. Ventana del principal: si la hora es reciente o futura,
    //    el principal aún puede tenerlo (lease fresco).
    if (isWithinMainWindow(job, nowMs)) {
      skip(job.id, job.clip_id, job.platform, `dentro de la ventana del publicador principal (hora ${new Date(jobScheduledMs(job)).toISOString()})`);
      continue;
    }
    // 3. Errores: 3 seguidos = parar; error reciente = enfriar 60 min.
    const errorCount = walErrorCountFor(entries, job.id);
    if (errorCount >= BACKUP_CONFIG.MAX_CONSECUTIVE_ERRORS) {
      skip(job.id, job.clip_id, job.platform, `${errorCount} errores seguidos en el diario: lo revisa el humano o el principal`);
      continue;
    }
    const lastError = walLastErrorFor(entries, job.id);
    if (lastError && nowMs - Date.parse(lastError.at) < BACKUP_CONFIG.ERROR_COOLDOWN_MS) {
      skip(job.id, job.clip_id, job.platform, "error reciente en el diario: enfriamiento de 60 min");
      continue;
    }
    // 4. El clip ya no se puede publicar (archivado o publicado).
    const clip = await getClip(env.DB, job.clip_id, tenant).catch(() => null);
    if (!clip) {
      skip(job.id, job.clip_id, job.platform, "el clip no existe en D1");
      continue;
    }
    if (clip.archived || clip.status === "publicado") {
      skip(job.id, job.clip_id, job.platform, "clip archivado o ya publicado");
      continue;
    }
    if (!job.r2_key) {
      skip(job.id, job.clip_id, job.platform, "el clip no tiene MP4 en R2");
      continue;
    }
    // 5. Credenciales (solo lectura D1, descifradas en memoria).
    const lang = clipLanguage(clip);
    const creds = await resolveCreds(env, job.platform, tenant, lang, job.api_account_id ?? undefined).catch(() => null);
    if (!creds) {
      skip(job.id, job.clip_id, job.platform, "no se pudieron resolver las credenciales (D1)");
      continue;
    }
    // 6. B2 — re-comprobación antes de subir: diario + plataforma.
    //    Si ya existe, NO se sube: resultado already_published.
    const dup = await checkDuplicateBeforeUpload(env, job, creds, opts.fetchJson, new Date(nowMs)).catch(() => null);
    if (dup?.duplicate && dup.platformId && dup.evidenceUrl) {
      await appendWalEntry(env, tenant, {
        type: "result",
        clip_id: job.clip_id,
        platform: job.platform,
        account_id: job.api_account_id ?? null,
        job_id: job.id,
        idempotency_key: job.idempotency_key,
        day: dayKey,
        outcome: "already_published",
        platform_id: dup.platformId,
        url: dup.evidenceUrl,
      });
      await clearBackupState(env.CLIPS_BUCKET, tenant, job.id);
      result.alreadyPublished++;
      result.attempts.push({ ...base, action: "already_published", url: dup.evidenceUrl, reason: `ya publicado (fuente: ${dup.source})` });
      continue;
    }
    // 7. Un paso del adaptador. El Env del adaptador lleva la D1
    //    de solo lectura: ni el adaptador ni sus logs escriben.
    const head = await env.CLIPS_BUCKET.head(job.r2_key).catch(() => null);
    const state = await readBackupState(env.CLIPS_BUCKET, tenant, job.id);
    const tiktokHandle =
      job.platform === "tiktok"
        ? ((await listSocialAccounts(env.DB, tenant).catch(() => [])).find((a) => a.platform === "tiktok" && a.language === lang)?.handle ?? null)
        : null;
    const ctx: PublishContext = {
      job: {
        ...job,
        resumeData: state?.resume_data ? parseResumeData(state.resume_data) : parseResumeData(job.resume_data),
        // Sin marca [clip:<id>] (2026-11-23): el caption viaja tal
        // cual, igual que en el principal. La comprobación B2 solo
        // LEE la marca en posts viejos que ya la llevan.
        caption: job.caption,
      },
      creds,
      r2Size: head?.size ?? null,
      tiktokHandle,
    };
    const adapterEnv: Env = { ...env, DB: asReadOnlyDb(env.DB) };
    let step: PublishResult;
    result.attempted++;
    try {
      step = await uploadStep(ctx, adapterEnv);
    } catch (e) {
      const reason = `el paso del adaptador lanzó: ${e instanceof Error ? e.message : String(e)}`.slice(0, 500);
      await appendWalEntry(env, tenant, {
        type: "result",
        clip_id: job.clip_id,
        platform: job.platform,
        account_id: job.api_account_id ?? null,
        job_id: job.id,
        idempotency_key: job.idempotency_key,
        day: dayKey,
        outcome: "error",
        error: reason,
      });
      result.errors++;
      result.attempts.push({ ...base, action: "error", reason });
      continue;
    }
    switch (step.mode) {
      case "posted":
        await appendWalEntry(env, tenant, {
          type: "result",
          clip_id: job.clip_id,
          platform: job.platform,
          account_id: job.api_account_id ?? null,
          job_id: job.id,
          idempotency_key: job.idempotency_key,
          day: dayKey,
          outcome: "posted",
          platform_id: step.platformId,
          url: step.evidenceUrl,
        });
        await clearBackupState(env.CLIPS_BUCKET, tenant, job.id);
        result.posted++;
        result.attempts.push({ ...base, action: "posted", url: step.evidenceUrl });
        break;
      case "processing":
      case "awaiting_captcha":
        // Paso en vuelo: el resume queda en R2 para la siguiente
        // corrida (no en D1, no en el diario). Sin resultado WAL:
        // la subida sigue en curso, no terminó. (awaiting_captcha
        // no trae resumeData: no hay nada que guardar.)
        if (step.mode === "processing" && step.resumeData) await saveBackupState(env.CLIPS_BUCKET, tenant, job.id, step.resumeData);
        result.inProgress++;
        result.attempts.push({ ...base, action: "in_progress", reason: step.reason });
        break;
      case "draft":
        // Como el principal: un draft no deja resultado en el
        // diario (no empezó a subir). Se reintenta en la próxima
        // corrida; el chequeo B2 con credenciales vacías no llama
        // a la plataforma (unavailable), así que no quema cuota.
        result.drafts++;
        result.attempts.push({ ...base, action: "draft", reason: step.reason });
        break;
      case "error":
        await appendWalEntry(env, tenant, {
          type: "result",
          clip_id: job.clip_id,
          platform: job.platform,
          account_id: job.api_account_id ?? null,
          job_id: job.id,
          idempotency_key: job.idempotency_key,
          day: dayKey,
          outcome: "error",
          error: (step.reason ?? "error sin motivo").slice(0, 500),
        });
        result.errors++;
        result.attempts.push({ ...base, action: "error", reason: step.reason });
        break;
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// Entrada del Worker de respaldo (cron cada 15 min, sin fetch: este
// Worker no sirve HTTP, solo rescata). Si requireTenant o los
// bindings fallan, la corrida falla en voz alta (nunca con un
// default silencioso que publique en el tenant equivocado).
// ---------------------------------------------------------------------------

export default {
  async scheduled(event: ScheduledEvent, env: Env, _ctx: ExecutionContext): Promise<void> {
    await runBackupCycle(env, { now: event.scheduledTime });
  },
};
