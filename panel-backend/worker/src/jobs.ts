// Cola de jobs del Worker: el usuario solo toca Configuración y el
// botón Iniciar; todo lo demás (jobs, reintentos, anti-duplicado,
// prioridades) es interno y automático. Vía principal: SOLO
// Cloudflare (ver COMPUTE.md). El corte/subtítulos son manuales
// (el usuario sube el MP4 9:16); GitHub Actions es solo alternativa.
//
// Estados de un job: scheduled → processing → posted | failed,
// más draft (no se puede subir por API: falta algo, con motivo) y
// awaiting_captcha (solo TikTok por navegador, futuro).
// `posted` EXIGE evidence_url: sin URL pública no hay posted.
//
// Anti-duplicado: idempotency_key = sha256(clip|platform|cuenta|fecha)
// UNIQUE. Un job en processing o posted con la misma clave nunca se
// vuelve a subir.
// Reintentos: solo fallos transitorios (5xx, rate limit, red), máx. 3,
// con backoff (1, 5, 15, 60 min, máx. 3 reintentos/día; A4). Un fallo final (copyright, formato
// tras 1 reintento) va a failed + aviso, nunca reintento ciego.
// Atascos: un job en processing más de 30 min se marca stalled,
// se registra en Tracking (clip_events / worker_events) y se crea
// una notificación visible en el panel. Nunca silencio.

import type { D1Database, Env } from "./cloudflare";
import type { Tenant } from "./auth";
import {
  SPAM_PAUSE_HOURS,
  PANEL_PLATFORMS,
  accountPausedUntilMs,
  clipLanguage,
  DAILY_FLOOR_PER_PLATFORM,
  effectiveSettings,
  getApiAccountByPlatform,
  getClip,
  getSettings,
  getTelegramLinkByTenant,
  isRestDayEffective,
  listApiAccounts,
  listClipStats,
  listPublishedClips,
  listQueueClips,
  boostBonusByClip,
  EVENT_DEDUPE_WINDOW_MS,
  lastIdenticalNotificationAt,
  logClipEvent,
  logWorkerEvent,
  maturityOut,
  parsePlatforms,
  platformMax,
  queueClipCompare,
  randomPublishDelayMs,
  MAX_PUBLISH_SPACING_MS,
  MIN_PUBLISH_SPACING_MS,
  recordEventCounter,
  refreshAccountMaturity,
  santiagoDayKeyMs,
  santiagoWallToUtcMs,
  saveSettings,
  stampCampaignSubmissionPosted,
  toApiPlatform,
  updateClipPublishState,
  updateClipPublishStates,
  withAccountPause,
  type ClipRow,
  type EffectiveSettings,
  type PanelPlatform,
  type PlatformPublishEntry,
} from "./db";
import { botConfigured, isRateLimitText, sendTelegram } from "./telegram";
import { dispatchPublishStep, type PublishResult } from "./publish/index";
import { appendWalEntry, flushWal, isD1QuotaError } from "./wal";
// Guardián de presupuesto D1 (A1, 2026-11-20): el ciclo crea el
// guardián una vez (contador cacheado en memoria) y lo deja
// vigente para que logWorkerEvent/logClipEvent frenen lo no
// esencial sin pasarlo por cada llamada.
import { createBudgetGuard, setCurrentBudgetGuard } from "./budget";
import { collectAnalytics } from "./publish/stats";
import { runEngagement } from "./publish/native-engagement";
import {
  backfillRetentionDeadlines,
  stampRetentionDeadline,
  sweepExpiredRetentions,
  type RetentionSweepResult,
} from "./r2-retention";
// Frenos anti-recaída A4 (2026-11-20): los umbrales viven en
// retry.ts (única fuente); aquí solo se re-exportan los nombres
// históricos para que los tests y gate.ts no cambien.
import {
  MAX_RETRIES_PER_DAY,
  MAX_TRANSIENT_RETRIES,
  RETRY_BACKOFF_MS as RETRY_BACKOFF_MS_SRC,
  RETRY_BACKOFF_MINUTES as RETRY_BACKOFF_MINUTES_SRC,
  autoEnqueueAllowed,
  checkQuotaBreaker,
  isQuotaBreakerOpen,
  noteQuotaAlertAttempt,
  quotaAlertDue,
  retriesToday,
  nextRetryDelayMs,
  santiagoDayStartMs,
  tripQuotaBreaker,
  type AutoEnqueueAttemptState,
} from "./retry";
export { MAX_RETRIES_PER_DAY, RETRY_BACKOFF_MINUTES_SRC as RETRY_BACKOFF_MINUTES, RETRY_BACKOFF_MS_SRC as RETRY_BACKOFF_MS };
export { MAX_TRANSIENT_RETRIES };
import { cycleGate, idleCycleGate, markCycleReview, type CycleGateDecision } from "./cycle-gate";
// Gates (publishGate, seoForPlatform, santiagoDayKey): viven en
// gate.ts desde 2026-11-18 (hallazgo M1) para que el dispatch
// pueda re-chequear sin importación circular. Se re-exportan para
// que index.ts y los tests sigan importando desde aquí.
import { classifyGateBlock, effectiveDailyPlan, recheckPublishGate, releasePublishSlot, santiagoDayKey, publishGate, rateLimitGate, seoForPlatform } from "./gate";
export { classifyGateBlock, publishGate, rateLimitGate, santiagoDayKey, seoForPlatform, type GateContext } from "./gate";
// Puerta de calidad B4 (2026-11-20): los umbrales y el veredicto
// viven en quality-gate.ts (única fuente); aquí solo se aplica.
import {
  QUALITY_ALERT_MIN_SAMPLE,
  QUALITY_DISCARD_ALERT_PCT,
  qualityGateForClip,
} from "./quality-gate";

export type JobKind = "vod_check" | "cut" | "subtitle" | "package" | "publish" | "metrics";
export type JobState = "scheduled" | "processing" | "posted" | "failed" | "draft" | "awaiting_captcha";

export interface JobRow {
  id: number;
  tenant_id: Tenant;
  kind: JobKind;
  clip_id: number | null;
  platform: PanelPlatform | null;
  api_account_id: number | null;
  state: JobState;
  idempotency_key: string | null;
  resume_data: string | null; // JSON: publish_id, container_id, session URL, bytes subidos…
  platform_id: string | null;
  evidence_url: string | null;
  title: string;
  caption: string;
  r2_key: string | null;
  // 0019 (DOS COLAS): idioma del clip al encolar ("es" | "en").
  // Los topes se cuentan por plataforma+idioma con esta columna.
  language: string;
  posted_day: string | null; // YYYY-MM-DD America/Santiago (cupo diario)
  attempts: number;
  last_error: string | null;
  retryable: number;
  next_run_at: number | null;
  created_at: number;
  updated_at: number;
  // 0021: quién tiene el job ('worker' | 'bridge' | null).
  claimed_by?: string | null;
  // 0022: token de la corrida que reclamó el job (16 bytes
  // aleatorios, hex). Los apply lo exigen en su UPDATE: el apply
  // de un reclamo viejo no pisa al reclamo vigente.
  run_token?: string | null;
  // 0022: día del cupo diario que tiene este job (jobs.slot_day).
  slot_day?: string | null;
  // 0029: publicación MANUAL (2026-11-19): 1 si el job nació del
  // botón "Publicar ahora" (salta tope+espaciamiento en el gate,
  // el re-chequeo y la reserva); 0 si nació del auto-enqueue.
  manual?: number | null;
}

const STALL_AFTER_MS = 30 * 60 * 1000; // 30 min en processing = atascado

/** ¿Es un error de spam o de límite de publicaciones (regla 8)?
 *  Estos NO se reintentan el mismo día: se detiene esa cuenta,
 *  se pausa 48 h y se reprograma al día siguiente. Un 429, un 5xx
 *  o un fallo de red son TEMPORALES y sí reintentan con backoff:
 *  por eso "429" solo no clasifica como spam, y tampoco un
 *  "rate limit" genérico sin mencionar publicaciones (p. ej. un
 *  límite de lectura de la API). */
export function isSpamLimitError(reason: string): boolean {
  const r = reason.toLowerCase();
  return (
    r.includes("spam") ||
    r.includes("too_many_posts") ||
    r.includes("too many posts") ||
    r.includes("demasiadas publicaciones") ||
    r.includes("límite de publicaciones") ||
    r.includes("limite de publicaciones") ||
    r.includes("spam_risk_too_many_posts") ||
    r.includes("shadowban") ||
    r.includes("cuenta restringida") ||
    r.includes("restringida por") ||
    r.includes("aviso de la plataforma") ||
    r.includes("posting limit") ||
    r.includes("publish limit") ||
    r.includes("post limit")
  );
}

/** Pausa una cuenta 48 h por spam/límite (regla 6): se guarda en
 *  settings (account_pauses, sin migración — ver db.ts), se
 *  registra en worker_events y se avisa por Telegram si hay
 *  vínculo (vía notify, que ya hace ambas cosas). El clip queda
 *  para reprogramar al día siguiente: el job va a failed
 *  NO reintentable y la Cola lo puede volver a encolar mañana
 *  (el gate lo frena mientras dure la pausa). Nunca se esquiva
 *  cambiando de cuenta, IP o método. */
export async function pauseAccountForSpam(
  db: D1Database,
  tenant: Tenant,
  accountId: number | null,
  reason: string,
  env?: Env,
  now = Date.now(),
): Promise<number | null> {
  if (!accountId) return null;
  const until = now + SPAM_PAUSE_HOURS * 3600 * 1000;
  const settings = await getSettings(db, tenant);
  await saveSettings(db, tenant, withAccountPause(settings, accountId, until, now));
  await notify(db, {
    kind: "failed",
    message: `Cuenta #${accountId} en pausa 48 h por spam/límite (${reason}). No se reintenta hoy; se reprograma mañana.`,
  }, tenant, env);
  return until;
}

const nowMs = () => Date.now();

/** Token de corrida (migración 0022, NEEDS-VALIDATION §3): 16
 *  bytes aleatorios en hex. Se genera dentro del reclamo del
 *  ciclo y se guarda en jobs.run_token; cada apply lo exige en su
 *  UPDATE, así que el apply de un reclamo ya superado (lease
 *  vencido, otro ciclo re-reclamó) no cambia la fila ni dispara
 *  efectos secundarios. */
export function newRunToken(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** ¿Cambió la fila el UPDATE? D1 siempre devuelve meta.changes
 *  numérico; los dobles de test que no lo modelan devuelven
 *  undefined y se tratan como éxito. Un 0 con run_token presente
 *  significa "otro reclamo ganó": el llamador NO ejecuta los
 *  efectos secundarios (published_count, clip, avisos). */
function updateChanged(res: { meta: { changes?: number } }): boolean {
  return res.meta.changes === undefined || res.meta.changes >= 1;
}

/** UPDATE de apply con valla de run_token: si el snapshot trae
 *  token, la fila solo cambia si sigue en manos de ese reclamo;
 *  sin token (puente, filas legacy) se mantiene el
 *  comportamiento antiguo sin valla. Devuelve si cambió la fila. */
async function fencedJobUpdate(
  db: D1Database,
  job: JobRow,
  setSql: string,
  setBinds: unknown[],
): Promise<boolean> {
  const token = job.run_token ?? null;
  const sql = token
    ? `UPDATE jobs SET ${setSql} WHERE id = ? AND tenant_id = ? AND run_token = ?`
    : `UPDATE jobs SET ${setSql} WHERE id = ? AND tenant_id = ?`;
  const binds = token ? [...setBinds, job.id, job.tenant_id, token] : [...setBinds, job.id, job.tenant_id];
  const res = await db.prepare(sql).bind(...binds).run();
  return updateChanged(res);
}

export async function idempotencyKey(clipId: number, platform: PanelPlatform, accountId: number, dayKey: string): Promise<string> {
  const raw = `${clipId}|${platform}|${accountId}|${dayKey}`;
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(raw));
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// ---------------------------------------------------------------------------
// Notificaciones: visibles en el panel SIEMPRE; por Telegram SOLO
// por la v2 multi-tenant (2026-11-18, migración 0023): el chat sale
// de telegram_links por tenant y el token es el secreto
// TELEGRAM_BOT_TOKEN del entorno. La v1 leía
// settings.telegram_bot_token/chat_id, claves que effectiveSettings
// excluye explícitamente ("NUNCA secretos"): ese camino estaba
// MUERTO (H1) y se borró. Sin env (llamadas de test o de código
// sin Worker) o sin vínculo, el aviso vive solo en el panel.
// Solo kinds failed/stall/captcha/credential/milestone se envían por
// Telegram; "info" (borradores) no. Dedup (2026-11-19): el MISMO
// aviso (mismo tenant, kind y mensaje) no se repite en 6 h: cada
// verificación fallida repetida (pulsar Verificar dos veces,
// re-guardar la credencial) mandaba el mismo mensaje por Telegram
// y dejaba otra fila en el panel (155 mensajes sin leer). Si el
// mensaje cambia (otra cuenta, otro motivo) sí se avisa, porque es
// información nueva. Si el envío falla, queda en worker_events y no
// se reintenta en bucle: que Telegram falle no tumba el aviso.
// ---------------------------------------------------------------------------
export interface NotifyInput {
  kind: "stall" | "captcha" | "credential" | "failed" | "milestone" | "info";
  clipId?: number | null;
  message: string;
}

/** Ventana de deduplicación de avisos (A2): la misma que la dedupe
 *  de eventos (EVENT_DEDUPE_WINDOW_MS, db.ts). Antes era una
 *  constante duplicada en este archivo con el mismo valor 6 h:
 *  un valor, un sitio (single-source config). Se conserva el nombre
 *  para no romper importadores externos. */
export const NOTIFY_DEDUP_WINDOW_MS = EVENT_DEDUPE_WINDOW_MS;

export async function notify(db: D1Database, input: NotifyInput, tenant: Tenant, env?: Env): Promise<void> {
  const now = nowMs();
  // Dedup entre llamadas: si el mismo aviso ya se guardó hace menos
  // de la ventana, no se repite (ni fila en el panel ni Telegram).
  // Falla abierto: si la consulta falla, el aviso se envía igual.
  // A2: la comprobación vive en db.ts (lastIdenticalNotificationAt,
  // misma ventana que el resto de eventos, con LIMIT 1: la consulta
  // anterior traía TODAS las filas idénticas) y el aviso suprimido
  // deja una muestra horaria en event_counters.
  const last = await lastIdenticalNotificationAt(db, tenant, input.kind, input.message);
  if (typeof last === "number" && now - last < NOTIFY_DEDUP_WINDOW_MS) {
    await recordEventCounter(db, "notifications", tenant, input.kind, input.message, last, now);
    return;
  }
  await db
    .prepare("INSERT INTO notifications (tenant_id, kind, clip_id, message, created_at) VALUES (?, ?, ?, ?, ?)")
    .bind(tenant, input.kind, input.clipId ?? null, input.message, now)
    .run();
  if (input.clipId) {
    // En Tracking, un hito es buena noticia, no un fallo: solo
    // failed/stall (y los avisos de alerta antiguos) marcan failed.
    await logClipEvent(db, input.clipId, "aviso", "sistema", input.message, input.kind !== "info" && input.kind !== "milestone", tenant);
  } else {
    await logWorkerEvent(db, "aviso", input.message, tenant);
  }
  // Telegram v2: solo avisos de alerta, con env y vínculo.
  if (input.kind === "info" || !env || !botConfigured(env)) return;
  const link = await getTelegramLinkByTenant(db, tenant).catch(() => null);
  if (!link) return; // Sin vínculo, no se envía nada a nadie.
  let text = `🎬 Clips GTA 6: ${input.message}`;
  if (input.kind === "failed" && isRateLimitText(input.message)) {
    text += "\nNo reintentes hasta que pase el enfriamiento: es un límite de frecuencia, no un fallo de tu clip.";
  }
  try {
    const sent = await sendTelegram(env, link.chat_id, text);
    if (!sent.ok) {
      await logWorkerEvent(db, "aviso", `No se pudo enviar el aviso por Telegram (${sent.error ?? `HTTP ${sent.status}`}). El aviso sigue visible en el panel.`, tenant);
    }
  } catch {
    // Que Telegram falle no tumba el aviso del panel.
    await logWorkerEvent(db, "aviso", "No se pudo enviar el aviso por Telegram. El aviso sigue visible en el panel.", tenant);
  }
}

// ---------------------------------------------------------------------------
// Alerta única de cuota D1 agotada (A4, 2026-11-20).
// Una sola alerta por día de Santiago (dedupe en memoria, ver
// retry.ts: quotaAlertDue). Bajo el 7500 la escritura del aviso en
// el panel MUERE por la propia cuota (notify INSERTa en
// notifications), así que el aviso va por dos vías, en orden:
//   1. notify() (panel + Telegram si la escritura pasa — p. ej.
//      si la cuota se liberó entre el 7500 y la alerta);
//   2. Telegram directo (solo lectura de telegram_links + fetch:
//      no escribe en D1, así que sobrevive a la cuota agotada).
// Si ninguna vía entrega, devuelve "failed" y se puede reintentar
// pasado 1 h (QUOTA_ALERT_RETRY_MIN_MS); si alguna entrega,
// devuelve "sent" y no se repite en el día. Nunca levanta: una
// alerta que falla no tumba el ciclo ni el job.
// ---------------------------------------------------------------------------
export async function alertQuotaExhausted(
  db: D1Database,
  tenant: Tenant,
  env?: Env,
  now = nowMs(),
): Promise<"sent" | "already" | "failed"> {
  const todayKey = santiagoDayKey();
  if (!quotaAlertDue(now, todayKey)) return "already";
  const message =
    "Cuota D1 agotada (error 7500): los bucles automáticos están parados hasta las 21:00 (-03). Nada se reintentó; se reanudan solos al liberarse la cuota.";
  let sent = false;
  try {
    await notify(db, { kind: "failed", message }, tenant, env);
    sent = true;
  } catch {
    // La escritura del aviso murió por la cuota: Telegram directo.
  }
  if (!sent && env && botConfigured(env)) {
    try {
      const link = await getTelegramLinkByTenant(db, tenant);
      if (link) {
        const res = await sendTelegram(env, link.chat_id, `🎬 Clips GTA 6: ${message}`);
        sent = res.ok;
      }
    } catch {
      sent = false;
    }
  }
  noteQuotaAlertAttempt(now, sent, todayKey);
  return sent ? "sent" : "failed";
}

// ---------------------------------------------------------------------------
// Alerta de descartes por calidad B4 (2026-11-20).
// Si los descartes por la puerta de calidad de HOY (día de
// Santiago) superan QUALITY_DISCARD_ALERT_PCT (30%) de los clips
// considerados, se avisa UNA vez al día: el render o el validador
// están fallando en serie y la Cola se va a secar. Definiciones
// (solo lecturas para calcularlas; la única escritura es el aviso
// mismo, y solo si toca):
// - descartados hoy: clips distintos con un evento "Publicación
//   frenada ...: calidad: ..." hoy (el motivo del gate deja esa
//   marca en clip_events; ver publishGate + quality-gate.ts).
// - considerados hoy: descartados + clips distintos con un job
//   publish creado hoy (los que pasaron la puerta y se encolaron).
// - muestra mínima QUALITY_ALERT_MIN_SAMPLE (5): con menos, el %
//   no significa nada.
// Dedupe: en memoria, un aviso por día de Santiago (mismo patrón
// que la alerta de cuota A4); notify() añade su propia dedupe de
// 6 h por mensaje idéntico. Vías como en alertQuotaExhausted:
// notify() (panel + Telegram) y, si la escritura muere (cuota),
// Telegram directo (solo lectura + fetch, no escribe en D1).
// Nunca levanta: una alerta que falla no tumba el ciclo.
// ---------------------------------------------------------------------------
let qualityAlertSentDayKey = "";

/** Solo para tests: vacía la dedupe en memoria de la alerta. */
export function resetQualityAlertStateForTests(): void {
  qualityAlertSentDayKey = "";
}

export async function maybeAlertQualityDiscards(
  db: D1Database,
  tenant: Tenant,
  env?: Env,
  now = nowMs(),
): Promise<"sent" | "already" | "not_due" | "failed"> {
  try {
    const todayKey = santiagoDayKey(new Date(now));
    if (qualityAlertSentDayKey === todayKey) return "already";
    const dayStart = santiagoDayStartMs(now);
    const blockedRows = await db
      .prepare(
        "SELECT DISTINCT clip_id FROM clip_events WHERE tenant_id = ? AND step = 'publicado' AND at >= ? AND message LIKE '%calidad:%'",
      )
      .bind(tenant, dayStart)
      .all<{ clip_id: number }>()
      .catch(() => null);
    const jobRows = await db
      .prepare(
        "SELECT DISTINCT clip_id FROM jobs WHERE tenant_id = ? AND kind = 'publish' AND created_at >= ?",
      )
      .bind(tenant, dayStart)
      .all<{ clip_id: number }>()
      .catch(() => null);
    if (!blockedRows || !jobRows) return "failed";
    const blocked = new Set<number>();
    for (const r of blockedRows.results ?? []) {
      if (typeof r.clip_id === "number") blocked.add(r.clip_id);
    }
    const considered = new Set<number>(blocked);
    for (const r of jobRows.results ?? []) {
      if (typeof r.clip_id === "number") considered.add(r.clip_id);
    }
    if (considered.size < QUALITY_ALERT_MIN_SAMPLE) return "not_due";
    const pct = (blocked.size / considered.size) * 100;
    if (pct <= QUALITY_DISCARD_ALERT_PCT) return "not_due";
    const message =
      `Calidad: ${blocked.size}/${considered.size} clips descartados hoy por la puerta de calidad ` +
      `(${Math.round(pct)}%, umbral ${QUALITY_DISCARD_ALERT_PCT}%) — revisar el render/validador antes de que se seque la Cola.`;
    // Dedupe durable (además de la de memoria): el mensaje lleva
    // los contadores, así que cambia a cada descarte y la dedupe
    // de notify() por mensaje idéntico no lo frenaría. Si hoy ya
    // se avisó por calidad (mismo prefijo), no se repite.
    const already = await db
      .prepare(
        "SELECT created_at FROM notifications WHERE tenant_id = ? AND kind = 'failed' AND message LIKE 'Calidad: % descartados hoy%' AND created_at >= ? LIMIT 1",
      )
      .bind(tenant, dayStart)
      .first<{ created_at: number }>()
      .catch(() => null);
    if (already) {
      qualityAlertSentDayKey = todayKey;
      return "already";
    }
    let sent = false;
    try {
      await notify(db, { kind: "failed", message }, tenant, env);
      sent = true;
    } catch {
      // La escritura del aviso murió (cuota): Telegram directo.
    }
    if (!sent && env && botConfigured(env)) {
      try {
        const link = await getTelegramLinkByTenant(db, tenant);
        if (link) {
          const res = await sendTelegram(env, link.chat_id, `🎬 Clips GTA 6: ${message}`);
          sent = res.ok;
        }
      } catch {
        sent = false;
      }
    }
    if (sent) qualityAlertSentDayKey = todayKey;
    return sent ? "sent" : "failed";
  } catch {
    return "failed";
  }
}

// ---------------------------------------------------------------------------
// Encolar publicación (lo que hace el botón Publicar y el Worker
// automático): un job por plataforma, idempotente.
// ---------------------------------------------------------------------------
export async function enqueuePublishJobs(
  db: D1Database,
  clip: ClipRow,
  accountIdByPlatform: Partial<Record<PanelPlatform, number>>,
  title: string,
  caption: string,
  r2Key: string | null,
  // Manual (2026-11-19, migración 0029): el job nace marcado
  // manual=1 cuando lo encola "Publicar ahora" (desde la orden
  // 09:25, 2026-11-21, el re-chequeo y la reserva aplican tope,
  // piso 90 min y rolling TAMBIÉN a manuales); 0 si es
  // automático.
  manual = false,
  // next_run_at del job nuevo (SPEC quitar-franjas v2,
  // 2026-11-26): lo calcula tryEnqueueClipPublish — en
  // automático es la última publicación + azar(90–180 min); en
  // manual es null (corre en cuanto el ciclo lo toma); en manual
  // PROGRAMADO es el instante pedido (ajustado al piso de 90 min
  // si hace falta).
  nextRunAtMs: number | null = null,
  // Plataformas a encolar (regla 14:10 + programación): solo
  // estas, no todas las del clip. Por defecto (sin pasarla),
  // todas las del clip (compatibilidad). tryEnqueueClipPublish
  // pasa solo la plataforma que pasó el gate, para que una
  // plataforma que frena no reciba job por arrastre de otra que
  // sí pasó.
  platforms?: PanelPlatform[],
  // Republicación explícita (P0 #5, auditoría #11): si el job
  // con la key base ya está `posted`, el clip ya salió hoy en
  // esta plataforma y un nuevo enqueue es una REPUBLICACIÓN:
  // la key nueva lleva el sufijo |repubN (se prueba repub1,
  // repub2... hasta encontrar una key libre) para no chocar
  // con la UNIQUE. Sin este flag, un job `posted` se devuelve
  // tal cual (dedup: no se publica dos veces por accidente).
  republish = false,
  // Día (YYYY-MM-DD Santiago) de la key de idempotencia. Por
  // defecto, hoy. tryEnqueueClipPublish pasa el día del instante
  // de referencia (el programado, si se agenda a futuro): la key
  // pertenece al día de la PUBLICACIÓN, no al del enqueue, para
  // que dos enqueues del mismo clip para el mismo día colisionen
  // como deben (dedup) y una republicación use el sufijo |repubN
  // del día correcto.
  dayKey?: string,
  // Salida opcional de motivos bloqueados (F1.4, spec 2026-11-24):
  // si un revive se niega por tope de reintentos, el motivo se
  // añade aquí (sin el prefijo "<platform>: ", que lo pone el
  // llamador al volcarlo a `blocked`).
  blockedOut?: string[],
): Promise<JobRow[]> {
  const created: JobRow[] = [];
  const day = dayKey ?? santiagoDayKey();
  // DOS COLAS (0019): el job guarda el idioma del clip. Es lo que
  // permite contar los topes por plataforma+idioma (ver POST
  // /publish) y lo que el Worker usa para no cruzar idiomas al
  // resolver credenciales (ver publish/index.ts).
  const language = clipLanguage(clip);
  // A2: los cambios de publish_states de todas las plataformas se
  // aplican en UN solo UPDATE al final (updateClipPublishStates),
  // no uno por plataforma. El finally cubre el caso de que el bucle
  // falle a mitad: los jobs ya creados quedan con su estado.
  const publishPatches: Array<{
    platform: PanelPlatform;
    patch: Partial<PlatformPublishEntry> & { state: PlatformPublishEntry["state"] };
  }> = [];
  try {
  for (const platform of platforms ?? parsePlatforms(clip.platforms)) {
    const accountId = accountIdByPlatform[platform] ?? 0;
    let key = await idempotencyKey(clip.id, platform, accountId, day);
    let existing = await db
      .prepare("SELECT * FROM jobs WHERE tenant_id = ? AND idempotency_key = ? AND state IN ('scheduled','processing','posted','draft','failed')")
      .bind(clip.tenant_id, key)
      .first<JobRow>();
    // P0 #5 (|repubN): si el job con la key base ya está `posted`
    // y esto es una republicación explícita, la key base chocaría
    // con la UNIQUE. Se prueba |repub1, |repub2... hasta encontrar
    // una key libre (el job nuevo es una publicación nueva, no un
    // duplicado del que ya salió).
    if (existing && existing.state === "posted" && republish) {
      for (let n = 1; n <= 10; n++) {
        const repubKey = await idempotencyKey(clip.id, platform, accountId, `${day}|repub${n}`);
        const repubExisting = await db
          .prepare("SELECT * FROM jobs WHERE tenant_id = ? AND idempotency_key = ? AND state IN ('scheduled','processing','posted','draft','failed')")
          .bind(clip.tenant_id, repubKey)
          .first<JobRow>();
        if (!repubExisting) {
          key = repubKey;
          existing = null;
          break;
        }
      }
    }
    if (existing) {
      // Re-publicar un job en draft/failed lo re-encola en vez de
      // insertar un duplicado con el mismo idempotency_key (eso
      // reventaba POST /publish con 500 por la UNIQUE).
      if (existing.state === "draft" || existing.state === "failed") {
        // F1.4 (spec 2026-11-24): el revive NO toca `attempts`
        // (conserva el valor guardado: el UPDATE anterior lo
        // ponía a 0 y borraba el conteo de reintentos del día,
        // burlando el tope de F2.5). Y si el job ya agotó sus 3
        // reintentos hoy, no se revive: queda en draft y el
        // llamador lo cuenta como bloqueado con el motivo de tope.
        if (retriesToday(existing, day) >= MAX_RETRIES_PER_DAY) {
          blockedOut?.push(`tope de reintentos por hoy: ${retriesToday(existing, day)}/${MAX_RETRIES_PER_DAY}`);
          continue;
        }
        await db
          .prepare(
            "UPDATE jobs SET state = 'scheduled', api_account_id = ?, title = ?, caption = ?, r2_key = ?, retryable = 1, last_error = NULL, next_run_at = ?, updated_at = ?, run_token = ?, slot_day = ?, manual = ? WHERE id = ? AND tenant_id = ?",
          )
          .bind(accountId || null, title, caption, r2Key, nowMs(), nowMs(), null, null, manual ? 1 : 0, existing.id, clip.tenant_id)
          .run();
        const row = await db.prepare("SELECT * FROM jobs WHERE id = ? AND tenant_id = ?").bind(existing.id, clip.tenant_id).first<JobRow>();
        if (row) created.push(row);
        publishPatches.push({
          platform,
          patch: {
            state: "programado",
            scheduledFor: new Date().toISOString(),
            note: "En cola de publicación automática.",
          },
        });
        continue;
      }
      created.push(existing);
      continue;
    }
    const res = await db
      .prepare(
        "INSERT INTO jobs (tenant_id, kind, clip_id, platform, api_account_id, state, idempotency_key, title, caption, r2_key, language, attempts, retryable, next_run_at, created_at, updated_at, manual) " +
          "VALUES (?, 'publish', ?, ?, ?, 'scheduled', ?, ?, ?, ?, ?, 0, 1, ?, ?, ?, ?)",
      )
      .bind(clip.tenant_id, clip.id, platform, accountId || null, key, title, caption, r2Key, language, nextRunAtMs, nowMs(), nowMs(), manual ? 1 : 0)
      .run();
    const row = await db.prepare("SELECT * FROM jobs WHERE id = ? AND tenant_id = ?").bind(res.meta.last_row_id, clip.tenant_id).first<JobRow>();
    if (row) created.push(row);
    publishPatches.push({
      platform,
      patch: {
        state: "programado",
        scheduledFor: new Date().toISOString(),
        note: "En cola de publicación automática.",
      },
    });
  }
  } finally {
    // El estado de publicación es un espejo: si falla, los jobs ya
    // están creados y el llamador lo verá; nunca rompe el enqueue.
    if (publishPatches.length > 0) {
      await updateClipPublishStates(db, clip.id, publishPatches, clip.tenant_id).catch(() => undefined);
    }
  }
  return created;
}

// ---------------------------------------------------------------------------
// CUENTAS VINCULADAS (regla del usuario 2026-11-21 14:10: "a las
// cuentas que tenga vinculadas le subes vídeos"). Al publicar para
// un tenant, se publica en TODAS sus cuentas vinculadas (conectadas
// y verificadas) en el idioma del clip, no en un subconjunto fijo:
// las plataformas destino se resuelven desde las cuentas vinculadas
// del tenant en ese idioma (verify_ok=1 y credencial), no desde una
// lista fija. La cuenta de Meta sirve a Instagram y a Facebook.
// ---------------------------------------------------------------------------
export async function linkedPlatforms(
  db: D1Database,
  tenant: Tenant,
  language: string,
): Promise<PanelPlatform[]> {
  const out: PanelPlatform[] = [];
  const ready = async (platform: string): Promise<boolean> => {
    const acc = await getApiAccountByPlatform(db, tenant, platform, language);
    return !!acc && acc.verify_ok === 1 && !!(acc.credential_enc || acc.credential);
  };
  if (await ready("youtube")) out.push("youtube_shorts");
  if (await ready("tiktok")) out.push("tiktok");
  if (await ready("meta")) out.push("instagram_reels", "facebook_reels");
  return out;
}

// ---------------------------------------------------------------------------
// Encolar publicación con gates (lo que hace el botón Publicar).
// Es EL MISMO camino para el botón y para el auto-enqueue: la
// resolución de cuentas por plataforma+idioma, los topes, el
// espaciamiento, el SEO y el resto de publishGate se evalúan aquí,
// con los contadores frescos. Cada plataforma que frena queda en
// `blocked` con el motivo LITERAL del gate y se registra en
// clip_events (y, si lo pide el llamador, también en
// worker_events): ningún freno es silencioso.
// ---------------------------------------------------------------------------
export interface EnqueuePublishResult {
  jobs: JobRow[];
  blocked: string[];
  /** Frenos que se SALTARON por publicar a mano (2026-11-19):
   *  un motivo por plataforma ("tope diario..." /
   *  "espaciamiento..."). Vacío si no se saltó nada o si el
   *  enqueue fue automático. El front lo muestra como aviso. */
  warnings: string[];
  /** Instante programado EFECTIVO (ms epoch): el pedido, o el
   *  ajustado hacia adelante si violaba el piso de 90 min (SPEC
   *  quitar-franjas v2). null si no se programó a futuro. El
   *  endpoint lo devuelve para informar el ajuste. */
  scheduledForMs?: number | null;
}

// ---------------------------------------------------------------------------
// Proyección rolling (B3), tope diario proyectado (B3.6) y ancla de
// 24 h (D2) — spec 2026-11-27. Un job no nace para rebotar en
// ejecución: el planificador proyecta lo que habrá publicado o
// programado cuando llegue su hora, y si no cabe, no se crea.
// ---------------------------------------------------------------------------

/** Tope rolling 24 h vigente para una plataforma (mismo cálculo
 *  que el gate: YouTube usa su propio tope). */
function rollingMaxFor(platform: PanelPlatform, eff: EffectiveSettings): number {
  return platform === "youtube_shorts"
    ? (eff.rolling_24h_max_youtube ?? eff.rolling_24h_max)
    : eff.rolling_24h_max;
}

/** Ventana rolling proyectada en un instante C: publicados con
 *  `updated_at` en (C-24 h, C] + programados/en curso con
 *  `next_run_at` en (C-24 h, C]. Un `next_run_at` NULL es "para
 *  ahora": cuenta (si `now` cae en la ventana) y su instante es
 *  `now`. Devuelve el conteo, su desglose y el instante más viejo
 *  de la ventana (V), para empujar a V+24 h. */
async function projectedRolling(
  db: D1Database,
  tenant: Tenant,
  platform: PanelPlatform,
  language: string,
  candidate: number,
  now: number,
): Promise<{ count: number; posted: number; scheduled: number; oldest: number | null }> {
  const from = candidate - 24 * 3600 * 1000;
  const posted = await db.prepare(
    "SELECT COUNT(*) AS n, MIN(updated_at) AS oldest_at FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' AND updated_at > ? AND updated_at <= ?",
  )
    .bind(tenant, platform, language, from, candidate)
    .first<{ n: number; oldest_at: number | null }>()
    .catch(() => null);
  const sched = await db.prepare(
    "SELECT COUNT(*) AS n, MIN(next_run_at) AS oldest_at FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state IN ('scheduled','processing') AND next_run_at > ? AND next_run_at <= ?",
  )
    .bind(tenant, platform, language, from, candidate)
    .first<{ n: number; oldest_at: number | null }>()
    .catch(() => null);
  const nullSched = await db.prepare(
    "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state IN ('scheduled','processing') AND next_run_at IS NULL",
  )
    .bind(tenant, platform, language)
    .first<{ n: number }>()
    .catch(() => null);
  // Un NULL es "para ahora": solo cuenta si `now` cae en la
  // ventana (el job publicará en el próximo ciclo, ~now).
  const nullCounts = (nullSched?.n ?? 0) > 0 && candidate - now < 24 * 3600 * 1000;
  const postedN = posted?.n ?? 0;
  const schedN = (sched?.n ?? 0) + (nullCounts ? (nullSched?.n ?? 0) : 0);
  const instants = [posted?.oldest_at ?? null, sched?.oldest_at ?? null, nullCounts ? now : null]
    .filter((v): v is number => v !== null);
  return { count: postedN + schedN, posted: postedN, scheduled: schedN, oldest: instants.length > 0 ? Math.min(...instants) : null };
}

/** Proyección B3 (spec 2026-11-27): empuja el candidato a V+24 h
 *  (V = instante más viejo de la ventana proyectada) mientras la
 *  ventana siga llena, máx 3 empujes. Un candidato empujado fuera
 *  del día actual de Santiago no se crea (no-derrame): el
 *  candidato INICIAL sí puede caer pasada la medianoche. Si no
 *  cabe, devuelve el motivo de bloqueo con el literal exacto del
 *  gate (el conteo es el de la evaluación que frenó la
 *  colocación: pre-empuje en el no-derrame, P(C') tras 3
 *  empujes). Si cabe, `projected` es P(C') para el gate. */
async function projectRollingCandidate(
  db: D1Database,
  tenant: Tenant,
  platform: PanelPlatform,
  language: string,
  candidate: number,
  rollingMax: number,
  now: number,
): Promise<{ candidate: number; blocked: string | null; projected: number; posted: number; scheduled: number }> {
  const todayKey = santiagoDayKeyMs(now);
  let c = candidate;
  for (let push = 0; push < 3; push++) {
    const w = await projectedRolling(db, tenant, platform, language, c, now);
    if (w.count < rollingMax) return { candidate: c, blocked: null, projected: w.count, posted: w.posted, scheduled: w.scheduled };
    // Ventana llena: empujar a V+24 h. Sin V el conteo no puede
    // bajar solo con el tiempo: no se crea.
    if (w.oldest === null) {
      return { candidate: c, blocked: `tope rolling 24 h alcanzado: ${w.count}/${rollingMax} en ${platform} (últimas 24 h)`, projected: w.count, posted: w.posted, scheduled: w.scheduled };
    }
    const next = w.oldest + 24 * 3600 * 1000;
    if (santiagoDayKeyMs(next) !== todayKey) {
      // No-derrame: el empuje sacó el candidato del día actual;
      // el conteo que frena es el de ANTES del empuje.
      return { candidate: c, blocked: `tope rolling 24 h alcanzado: ${w.count}/${rollingMax} en ${platform} (últimas 24 h)`, projected: w.count, posted: w.posted, scheduled: w.scheduled };
    }
    c = next;
  }
  // Tras 3 empujes, una última evaluación en C'.
  const w = await projectedRolling(db, tenant, platform, language, c, now);
  if (w.count < rollingMax) return { candidate: c, blocked: null, projected: w.count, posted: w.posted, scheduled: w.scheduled };
  return { candidate: c, blocked: `tope rolling 24 h alcanzado: ${w.count}/${rollingMax} en ${platform} (últimas 24 h)`, projected: w.count, posted: w.posted, scheduled: w.scheduled };
}

/** Tope diario proyectado B3.6 (spec 2026-11-27): publicados de
 *  hoy (posted_day = día del candidato) + programados/en curso
 *  con next_run_at en (now, C']. Si llega al techo o al plan, no
 *  se crea el job, con el literal exacto del gate (techo antes
 *  que plan, como el gate). */
async function projectedDailyBlock(
  db: D1Database,
  tenant: Tenant,
  platform: PanelPlatform,
  language: string,
  candidate: number,
  now: number,
  max: number,
  plan: number,
): Promise<string | null> {
  const dayKey = santiagoDayKeyMs(candidate);
  const posted = await db.prepare(
    "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' AND posted_day = ?",
  )
    .bind(tenant, platform, language, dayKey)
    .first<{ n: number }>()
    .catch(() => null);
  const sched = await db.prepare(
    "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state IN ('scheduled','processing') AND next_run_at > ? AND next_run_at <= ?",
  )
    .bind(tenant, platform, language, now, candidate)
    .first<{ n: number }>()
    .catch(() => null);
  const projected = (posted?.n ?? 0) + (sched?.n ?? 0);
  if (projected >= max) return `tope diario alcanzado: ${projected}/${max} en ${platform}`;
  if (projected >= plan) return `plan del día cubierto: ${projected}/${plan} en ${platform} (techo ${max})`;
  return null;
}

/** Ancla D2 (orden del usuario 2026-11-27 06:43, ventana 24 h):
 *  el primer job del día del tenant se ancla a ~00:00 (inicio del
 *  día + azar 0–30 min), no deriva con el retardo 90–180 min desde
 *  la última subida de ayer. Solo rama automática (no manual ni
 *  programado). Si el día del candidato ya tiene algún job
 *  (publicado o programado, cualquier plataforma), no hay ancla.
 *  El ancla respeta el piso de 90 min desde la última subida y
 *  nunca cae en el pasado. */
async function anchorFirstJobOfDay(
  db: D1Database,
  tenant: Tenant,
  candidate: number,
  lastPostedAtMs: number | null,
  now: number,
  rng: () => number,
): Promise<number> {
  const dayKey = santiagoDayKeyMs(candidate);
  const dayStart = santiagoWallToUtcMs(dayKey, "00:00");
  const dayEnd = dayStart + 24 * 3600 * 1000;
  const postedToday = await db.prepare(
    "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND state = 'posted' AND posted_day = ?",
  )
    .bind(tenant, dayKey)
    .first<{ n: number }>()
    .catch(() => null);
  if ((postedToday?.n ?? 0) > 0) return candidate;
  const schedToday = await db.prepare(
    "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND state IN ('scheduled','processing') AND next_run_at >= ? AND next_run_at < ?",
  )
    .bind(tenant, dayStart, dayEnd)
    .first<{ n: number }>()
    .catch(() => null);
  if ((schedToday?.n ?? 0) > 0) return candidate;
  const jitter = Math.floor(rng() * 30) * 60 * 1000;
  let anchored = dayStart + jitter;
  if (lastPostedAtMs !== null) anchored = Math.max(anchored, lastPostedAtMs + MIN_PUBLISH_SPACING_MS);
  anchored = Math.max(anchored, now);
  return anchored;
}
export async function tryEnqueueClipPublish(
  env: Env,
  tenant: Tenant,
  clip: ClipRow,
  opts: {
    actor?: string;
    logToWorkerEvents?: boolean;
    manual?: boolean;
    /** Plataformas destino explícitas (override): si se pasan,
     *  son las únicas que se encolan (cada una frena con motivo
     *  si no hay cuenta vinculada en ese idioma). Si no se pasan,
     *  las plataformas son la UNIÓN de las del clip y las de sus
     *  cuentas vinculadas (regla 14:10). */
    platforms?: PanelPlatform[];
    /** Instante programado (ms epoch, ISO 8601 en el endpoint):
     *  si viene y es futuro, los jobs nacen con next_run_at =
     *  ese instante (ajustado al piso de 90 min si hace falta, ver
     *  más abajo) y el gate se evalúa EN ESE INSTANTE (tope
     *  diario, calentamiento y piso 90 min para ese día, lo mejor
     *  que se pueda calcular al agendar; el re-chequeo del ciclo
     *  vuelve a aplicar todo al ejecutar). Si no viene,
     *  publicación inmediata (ahora). */
    scheduledForMs?: number | null;
    /** Republicación explícita (P0 #5, auditoría #11): publicar
     *  DE NUEVO el mismo clip en la misma plataforma el mismo
     *  día (la plataforma borró el vídeo). Solo salta la puerta
     *  "mismo clip hoy"; el resto sigue frenando igual y el job
     *  nuevo lleva la key con sufijo |repubN. */
    republish?: boolean;
    /** Generador de azar para el retardo 90–180 min (SPEC
     *  quitar-franjas v2): Math.random() por defecto; los tests
     *  pasan un RNG falso para probar determinísticamente. */
    rng?: () => number;
  } = {},
): Promise<EnqueuePublishResult> {
  const db = env.DB;
  const actor = opts.actor ?? "usuario";
  if (clip.archived) {
    const reason = "clip archivado";
    await logClipEvent(db, clip.id, "publicado", actor, `Publicación frenada: ${reason}.`, true, tenant);
    if (opts.logToWorkerEvents) await logWorkerEvent(db, "auto_publish", `Clip #${clip.id} frenado: ${reason}.`, tenant);
    return { jobs: [], blocked: [reason], warnings: [] };
  }
  const settings = await getSettings(db, tenant);
  const eff = effectiveSettings(settings);
  // DOS COLAS (0019): la cuenta se resuelve POR PLATAFORMA +
  // IDIOMA DEL CLIP. Un clip en inglés solo puede salir por la
  // cuenta en inglés; si no hay cuenta en ese idioma (o no
  // tiene credencial verificada), la plataforma queda frenada
  // con el motivo — NUNCA se publica en el canal del otro
  // idioma y NUNCA se auto-crea una cuenta aquí.
  const clipLang = clipLanguage(clip);
  // Instante de referencia para el gate: si se programa a futuro,
  // el gate se evalúa EN EL INSTANTE PROGRAMADO (tope diario,
  // calentamiento y piso 90 min de ESE día); si no, ahora.
  const now = nowMs();
  const scheduledForMs = opts.scheduledForMs ?? null;
  let refMs = scheduledForMs !== null && scheduledForMs > now ? scheduledForMs : now;
  let refDayKey = santiagoDayKeyMs(refMs);
  // PLATAFORMAS DESTINO (regla 14:10, 2026-11-21): por defecto la
  // UNIÓN de las del clip y las de sus cuentas vinculadas en el
  // idioma del clip (una cuenta vinculada que el clip no listaba
  // también recibe el vídeo); un override explícito limita a esa
  // lista. Una plataforma sin cuenta vinculada queda frenada con
  // motivo por el gate (nunca se publica en el canal de otro
  // idioma ni se auto-crea cuenta).
  const linked = await linkedPlatforms(db, tenant, clipLang);
  let targetPlatforms = opts.platforms
    ? opts.platforms.filter((p): p is PanelPlatform => (PANEL_PLATFORMS as readonly string[]).includes(p))
    : [...new Set([...parsePlatforms(clip.platforms), ...linked])];
  // F1b.1 (spec 2026-11-24): un clip sellado como `publicado`
  // vuelve a evaluarse para las plataformas vinculadas que le
  // faltan respecto a su lista (linked − listed): se sigue
  // adelante SOLO con esas. Si no le falta ninguna, "clip ya
  // publicado" como hoy.
  if (clip.status === "publicado") {
    const listed = parsePlatforms(clip.platforms);
    const missing = linked.filter((p) => !listed.includes(p));
    const wanted = opts.platforms ? opts.platforms.filter((p) => missing.includes(p)) : missing;
    if (wanted.length === 0) {
      const reason = "clip ya publicado";
      await logClipEvent(db, clip.id, "publicado", actor, `Publicación frenada: ${reason}.`, true, tenant);
      if (opts.logToWorkerEvents) await logWorkerEvent(db, "auto_publish", `Clip #${clip.id} frenado: ${reason}.`, tenant);
      return { jobs: [], blocked: [reason], warnings: [] };
    }
    targetPlatforms = wanted;
  }
  // Escalado de topes (0017): recálculo diario (no-op si ya se
  // hizo hoy); el gate usa el estado guardado de cada cuenta.
  await refreshAccountMaturity(db, tenant, eff).catch(() => undefined);
  const accountIdByPlatform: Partial<Record<PanelPlatform, number>> = {};
  const maturityByPlatform: Partial<Record<PanelPlatform, { limit: number; plan: number; stage: string }>> = {};
  const accountLanguageByPlatform: Partial<Record<PanelPlatform, "es" | "en" | null>> = {};
  const accountReadyByPlatform: Partial<Record<PanelPlatform, boolean>> = {};
  const accountRowByPlatform: Partial<Record<PanelPlatform, { id: number }>> = {};
  for (const p of targetPlatforms) {
    const want = p === "youtube_shorts" ? "youtube" : p === "tiktok" ? "tiktok" : "meta";
    const acc = await getApiAccountByPlatform(db, tenant, want, clipLang);
    if (!acc) {
      accountLanguageByPlatform[p] = null;
      accountReadyByPlatform[p] = false;
      continue;
    }
    accountRowByPlatform[p] = acc;
    accountLanguageByPlatform[p] = acc.language === "en" ? "en" : "es";
    const ready = acc.verify_ok === 1 && !!(acc.credential_enc || acc.credential);
    accountReadyByPlatform[p] = ready;
    if (ready) {
      accountIdByPlatform[p] = acc.id;
      // SPEC piso-3-por-dia v2 §4: la madurez se resuelve con la
      // plataforma de panel (la fila Meta sirve a IG y FB).
      const m = maturityOut(acc, eff, Date.now(), toApiPlatform(p));
      maturityByPlatform[p] = { limit: m.effective_daily_limit, plan: m.effective_daily_plan, stage: m.stage };
    }
  }
  // MANUAL PROGRAMADO (SPEC quitar-franjas v2): sin franjas, se
  // acepta cualquier hora futura. Si la hora pedida viola el piso
  // de 90 min de alguna plataforma (contra su última publicación
  // o contra otro job ya agendado de la misma plataforma+idioma),
  // se mueve hacia adelante al primer instante válido para TODAS
  // las plataformas y se informa el ajuste en el resultado
  // (scheduledForMs). Sin esto, el gate frenaría esa plataforma
  // en refMs y la dejaría fuera.
  let effectiveScheduledForMs = scheduledForMs;
  if (scheduledForMs !== null && scheduledForMs > now) {
    let floor = scheduledForMs;
    for (const p of targetPlatforms) {
      const lastP = await db.prepare(
        "SELECT updated_at FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' ORDER BY updated_at DESC LIMIT 1",
      ).bind(tenant, p, clipLang).first<{ updated_at: number }>();
      if (lastP?.updated_at) floor = Math.max(floor, lastP.updated_at + MIN_PUBLISH_SPACING_MS);
      const pendP = await db.prepare(
        "SELECT MAX(next_run_at) AS max_at FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'scheduled' AND next_run_at IS NOT NULL",
      ).bind(tenant, p, clipLang).first<{ max_at: number | null }>();
      if (pendP?.max_at) floor = Math.max(floor, pendP.max_at + MIN_PUBLISH_SPACING_MS);
    }
    effectiveScheduledForMs = floor;
    refMs = effectiveScheduledForMs;
    refDayKey = santiagoDayKeyMs(refMs);
  }
  // Gates por plataforma: la que frena queda pendiente con motivo.
  // ORDEN (spec 2026-11-27, B3 + D2): primero el candidato C
  // (con ancla D2 si es el primer job del día), luego la
  // proyección rolling (B3) y el tope diario proyectado (B3.6), y
  // SOLO entonces el gate, evaluado en refMs = C' con el conteo
  // rolling proyectado. Así un job no nace para rebotar en
  // ejecución: si no cabe, no se crea.
  const jobs: JobRow[] = [];
  const blocked: string[] = [];
  const warnings: string[] = [];
  const manual = opts.manual ?? false;
  // (La puerta de calidad B4 ya no tiene aviso manual: desde el
  // refuerzo del validador, 2026-11-21, un clip sin veredicto
  // válido no se publica ni a mano, así que no hay "publicado
  // pese a no tener veredicto" que avisar. El bloqueo queda en
  // `blocked` por plataforma, con el motivo del gate.)
  for (const platform of targetPlatforms) {
    const seo = seoForPlatform(clip, platform);
    const rng = opts.rng ?? Math.random;
    // Última subida real (para el candidato y el ancla D2).
    const lastPosted = await db
      .prepare(
        "SELECT updated_at FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' ORDER BY updated_at DESC LIMIT 1",
      )
      .bind(tenant, platform, clipLang)
      .first<{ updated_at: number }>();
    const lastPostedAtMs = lastPosted?.updated_at ?? null;
    // --- 1. Candidato C ---
    let candidate: number;
    if (effectiveScheduledForMs !== null && effectiveScheduledForMs > now) {
      // Manual programado: el instante pedido (ya ajustado al
      // piso de 90 min arriba).
      candidate = effectiveScheduledForMs;
    } else if (manual) {
      // Manual inmediato: C = ahora (el job nace con
      // next_run_at = null y corre en cuanto el ciclo lo toma).
      candidate = now;
    } else {
      // Automático (SPEC quitar-franjas v2, opción A): última
      // publicación + azar(90–180 min) de SU plataforma+idioma
      // (nunca antes de ahora).
      candidate = lastPostedAtMs !== null ? lastPostedAtMs + randomPublishDelayMs(rng) : now;
      candidate = Math.max(candidate, now);
      // D2: ancla del primer job del día a ~00:00.
      candidate = await anchorFirstJobOfDay(db, tenant, candidate, lastPostedAtMs, now, rng);
      // (1) Invariante del programador: no crear un job pendiente
      // a menos de 90 min de otro pendiente de la misma
      // plataforma+idioma.
      const pending = await db.prepare(
        "SELECT MAX(next_run_at) AS max_at FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'scheduled' AND next_run_at IS NOT NULL",
      ).bind(tenant, platform, clipLang).first<{ max_at: number | null }>();
      if (pending?.max_at) candidate = Math.max(candidate, pending.max_at + MIN_PUBLISH_SPACING_MS);
      // (2) Escalonado entre plataformas del mismo clip en esta
      // llamada: ≥15 min desde el último job creado aquí.
      const lastCreatedAt = jobs.length > 0 ? jobs[jobs.length - 1]!.next_run_at : null;
      if (lastCreatedAt) candidate = Math.max(candidate, lastCreatedAt + 15 * 60 * 1000);
    }
    // --- 2. Proyección rolling B3 ---
    const proj = await projectRollingCandidate(db, tenant, platform, clipLang, candidate, rollingMaxFor(platform, eff), now);
    if (proj.blocked) {
      blocked.push(`${platform}: ${proj.blocked}`);
      await logClipEvent(db, clip.id, "publicado", actor, `Publicación frenada en ${platform}: ${proj.blocked}. (proyectado: ${proj.posted} posted + ${proj.scheduled} programados)`, true, tenant);
      if (opts.logToWorkerEvents) {
        await logWorkerEvent(db, "auto_publish", `Clip #${clip.id} frenado en ${platform}: ${proj.blocked}. (proyectado: ${proj.posted} posted + ${proj.scheduled} programados)`, tenant);
      }
      continue;
    }
    candidate = proj.candidate;
    // --- 3. Tope diario proyectado B3.6 ---
    const accountDailyLimit = maturityByPlatform[platform]?.limit ?? null;
    const accountDailyPlan = maturityByPlatform[platform]?.plan ?? null;
    const maxBase = platformMax(eff, toApiPlatform(platform));
    const max = accountDailyLimit !== null ? Math.min(accountDailyLimit, maxBase) : maxBase;
    const plan = effectiveDailyPlan(platform, settings, accountDailyLimit, accountDailyPlan);
    const dailyBlock = await projectedDailyBlock(db, tenant, platform, clipLang, candidate, now, max, plan);
    if (dailyBlock) {
      blocked.push(`${platform}: ${dailyBlock}`);
      await logClipEvent(db, clip.id, "publicado", actor, `Publicación frenada en ${platform}: ${dailyBlock}.`, true, tenant);
      if (opts.logToWorkerEvents) {
        await logWorkerEvent(db, "auto_publish", `Clip #${clip.id} frenado en ${platform}: ${dailyBlock}.`, tenant);
      }
      continue;
    }
    // --- 4. Gate, evaluado en refMs = C' ---
    // Al programar a futuro, el día es el del instante
    // programado, no hoy; la key de idempotencia pertenece al día
    // de la PUBLICACIÓN, no al del enqueue.
    refMs = candidate;
    refDayKey = santiagoDayKeyMs(candidate);
    // Topes POR plataforma+idioma (0019): solo cuentan las
    // publicaciones de ESE DÍA en este idioma (jobs.language).
    const publishedToday = await db
      .prepare(
        "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' AND posted_day = ?",
      )
      .bind(tenant, platform, clipLang, refDayKey)
      .first<{ n: number }>();
    const clipToday = await db
      .prepare(
        "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND clip_id = ? AND platform = ? AND state = 'posted' AND posted_day = ?",
      )
      .bind(tenant, clip.id, platform, refDayKey)
      .first<{ n: number }>();
    // POSTED-EVER (F1, spec 2026-11-24): el mismo clip ya salió
    // en ESTA plataforma otro día (sin filtro de día): el gate
    // solo miraba posted_day=hoy y por eso el 102 se duplicó en
    // TikTok. El `posted_day` del posted más reciente va al motivo.
    const clipEver = await db
      .prepare(
        "SELECT posted_day FROM jobs WHERE tenant_id = ? AND clip_id = ? AND platform = ? AND state = 'posted' ORDER BY updated_at DESC LIMIT 1",
      )
      .bind(tenant, clip.id, platform)
      .first<{ posted_day: string | null }>();
    // MISMO CLIP EN OTRA PLATAFORMA (F2b, spec 2026-11-24):
    // `state IN ('posted','processing')`, no solo posted: un
    // hermano en `processing` cuenta como última subida del clip
    // en otra plataforma (los grupos del ciclo se despachan en
    // paralelo y cada uno reclama antes de re-chequear).
    const clipOther = await db
      .prepare(
        "SELECT updated_at FROM jobs WHERE tenant_id = ? AND clip_id = ? AND platform <> ? AND state IN ('posted','processing') ORDER BY updated_at DESC LIMIT 1",
      )
      .bind(tenant, clip.id, platform)
      .first<{ updated_at: number }>();
    const accRow = accountRowByPlatform[platform];
    const mat = maturityByPlatform[platform];
    const gateCtx = {
      clip,
      seoScore: seo.score,
      seoOverride: seo.override,
      publishedToday: publishedToday?.n ?? 0,
      lastPublishedAtMs: lastPostedAtMs,
      streamerAllowed: true, // el permiso se verifica en el puente con streamers.yaml
      settings,
      accountDailyLimit,
      accountDailyPlan,
      // Calentamiento (reglas 2026-11-19): si la cuenta no es
      // establecida, su tope de etapa frena TAMBIÉN a mano.
      warmupDailyLimit: mat && mat.stage !== "establecida" ? mat.plan : null,
      // Rolling PROYECTADO (B3): lo que habrá en la ventana
      // cuando llegue C', no solo lo publicado hasta ahora.
      publishedLast24h: proj.projected,
      clipPublishedTodayOnPlatform: (clipToday?.n ?? 0) > 0,
      clipPublishedEverOnPlatform: clipEver !== null,
      clipLastPublishedOnPlatformDay: clipEver?.posted_day ?? null,
      republish: opts.republish === true,
      clipLastPublishedOtherPlatformMs: clipOther?.updated_at ?? null,
      // Pausa por spam (48 h, settings account_pauses) y día
      // libre semanal (determinista por cuenta+semana). Al
      // programar a futuro, el día libre es el del instante
      // programado.
      pausedUntilMs: accRow ? accountPausedUntilMs(settings, accRow.id) : null,
      isRestDay: accRow ? isRestDayEffective(`acc-${accRow.id}`, refMs, settings, accRow.id) : false,
      nowMs: refMs,
      accountLanguage: accountLanguageByPlatform[platform],
      accountReady: accountReadyByPlatform[platform],
      // Manual (2026-11-19 + orden 09:25 SIN AMONTONAR): ya NO
      // salta tope ni piso 90 min; respeta todas las reglas
      // anti-baneo igual que la automática.
      manual,
    };
    const gate = publishGate(platform, gateCtx);
    if (gate) {
      blocked.push(`${platform}: ${gate}`);
      await logClipEvent(db, clip.id, "publicado", actor, `Publicación frenada en ${platform}: ${gate}.`, true, tenant);
      if (opts.logToWorkerEvents) {
        await logWorkerEvent(db, "auto_publish", `Clip #${clip.id} frenado en ${platform}: ${gate}.`, tenant);
      }
      continue;
    }
    // --- 5. Crear el job ---
    const title = clip.seo_title || clip.title;
    const caption = clip.seo_description || clip.seo_title || clip.title;
    // La key del MP4 vive en el clip desde la 0016 (la guarda el
    // PUT /video); el fallback a jobs cubre clips subidos antes
    // de ella. Sin key, el job nace sin r2_key y el Worker lo
    // deja en draft ("el clip no tiene MP4 en R2").
    const r2Key = clip.video_r2_key
      ? { r2_key: clip.video_r2_key }
      : await db
          .prepare("SELECT r2_key FROM jobs WHERE clip_id = ? AND tenant_id = ? AND r2_key IS NOT NULL LIMIT 1")
          .bind(clip.id, tenant)
          .first<{ r2_key: string }>();
    // next_run_at: el candidato proyectado C'. El manual inmediato
    // nace con null (corre en cuanto el ciclo lo toma); el manual
    // PROGRAMADO, con el instante pedido (o su empuje por B3, si
    // el tope rolling lo movió). Solo se encola ESTA plataforma
    // (la que pasó el gate): enqueuePublishJobs ya no re-encola
    // todas las del clip.
    let nextRunAt: number | null;
    if (effectiveScheduledForMs !== null && effectiveScheduledForMs > now) {
      nextRunAt = candidate;
    } else if (manual) {
      nextRunAt = null;
    } else {
      nextRunAt = candidate;
    }
    const blockedOut: string[] = [];
    jobs.push(...(await enqueuePublishJobs(db, clip, accountIdByPlatform, title, caption, r2Key?.r2_key ?? null, manual, nextRunAt, [platform], opts.republish === true, refDayKey, blockedOut)));
    // F1.4: un revive negado por tope de reintentos cuenta como
    // bloqueado con el motivo de tope (con el prefijo de la
    // plataforma, como los frenos del gate).
    for (const b of blockedOut) blocked.push(`${platform}: ${b}`);
  }
  // Cada plataforma que pasó el gate encoló solo su job (ya no hay
  // duplicados por re-encolar todas en cada llamada); el dedup
  // queda por seguridad.
  const seen = new Set<number>();
  const uniqueJobs = jobs.filter((j) => (seen.has(j.id) ? false : (seen.add(j.id), true)));
  return { jobs: uniqueJobs, blocked, warnings, scheduledForMs: effectiveScheduledForMs };
}

// ---------------------------------------------------------------------------
// Auto-enqueue de tops medidos (2026-11-19, auto-boost).
// Detrás del ajuste auto_publish_top (default OFF, en Ajustes): sin
// encenderlo, esto no hace nada. Cuando está encendido, toma la Cola
// ya ordenada con el boost por tops medidos y encola solos los
// primeros clips sin job publish vigente (máx. 3 por ciclo), pasando
// EXACTAMENTE el mismo publishGate que el botón Publicar (vía
// tryEnqueueClipPublish): plan/techo/espaciamiento por
// plataforma+idioma, SEO, preview, duración, idioma. Cada freno se
// registra con el motivo LITERAL del gate en clip_events +
// worker_events, nunca en silencio. No toca los umbrales 50/90, ni
// la viralidad/SEO, ni el orden manual.
// Se llama en runWorkerCycle DESPUÉS del chequeo isWorkerRunning:
// es el único punto donde /parar lo detiene.
// ---------------------------------------------------------------------------
export interface AutoEnqueueResult {
  enabled: boolean;
  considered: number;
  enqueued: number;
  blocked: number;
  /** Bloqueos por la puerta de calidad B4 en este ciclo. */
  qualityBlocked?: number;
}

/** Motivos de déficit del piso 3/día (SPEC piso-3-por-dia v2 §2). */
export type FloorBlockMotive = "sin clips" | "rolling" | "calentamiento" | "calidad" | "pausa" | "día libre" | "sin cuenta";

/**
 * Clasifica una entrada de `blocked` ("<plataforma>: <motivo>") en
 * una de las 7 categorías de motivo del pase del piso (§2), o null
 * cuando es un bloqueo transitorio (p. ej. "menos de 90 min" o
 * "15 min entre plataformas") que no explica un déficit: el
 * trabajo se reintenta en el siguiente ciclo.
 */
export function classifyFloorBlockReason(blockedEntry: string): FloorBlockMotive | null {
  const m = blockedEntry.toLowerCase();
  if (m.includes("calentamiento")) return "calentamiento";
  if (m.includes("rolling")) return "rolling";
  if (m.includes("calidad")) return "calidad";
  if (m.includes("día libre")) return "día libre";
  if (m.includes("pausa") || m.includes("spam")) return "pausa";
  if (m.includes("sin cuenta") || m.includes("sin credencial") || m.includes("no hay cuenta")) return "sin cuenta";
  return null;
}

/** Motivo dominante: el más frecuente; desempate por primer visto; sin razones clasificadas → "sin clips". */
export function dominantFloorMotive(blocked: string[]): FloorBlockMotive {
  const counts = new Map<FloorBlockMotive, number>();
  const seen: FloorBlockMotive[] = [];
  for (const entry of blocked) {
    const m = classifyFloorBlockReason(entry);
    if (m === null) continue;
    if (!counts.has(m)) seen.push(m);
    counts.set(m, (counts.get(m) ?? 0) + 1);
  }
  let best: FloorBlockMotive = "sin clips";
  let bestN = 0;
  for (const m of seen) {
    const n = counts.get(m) ?? 0;
    if (n > bestN) { best = m; bestN = n; }
  }
  return best;
}

/** Estado de un canal durante el pase del piso (§2). */
interface FloorChannelState {
  key: string;
  platform: PanelPlatform;
  language: "es" | "en";
  floor: number;
  postedToday: number;
  scheduledToday: number;
  lastPostedAtMs: number | null;
  pendingMaxAtMs: number | null;
  /** Clips ya intentados para este canal en este pase (no se reintentan: el gate no cambia dentro del ciclo). */
  tried: Set<number>;
  blocked: string[];
}

/**
 * Canales con evento de piso ya escrito hoy (SPEC piso-3-por-dia
 * v2 §2): un evento por canal y día, no uno por ciclo (el ciclo
 * corre cada 5 min y el evento es una señal para la revisión
 * diaria, no un log por intento; además cada Worker tiene un
 * presupuesto de 3.500 eventos/día en D1).
 */
const floorEventLoggedDays = new Set<string>();
export function resetFloorEventLogForTests(): void {
  floorEventLoggedDays.clear();
}

const AUTO_ENQUEUE_MAX_CLIPS_PER_CYCLE = 5;

/** Máx. clips escaneados por ciclo (60, F4, spec 2026-11-24):
 *  clips visitados, incluidos los saltados por hasVigente o por
 *  A4; acota las lecturas D1 por ciclo aunque los saltos sean
 *  gratis (no consuman `evaluated`). */
const AUTO_ENQUEUE_MAX_SCANNED_PER_CYCLE = 60;

/** Máx. clips evaluados por ciclo (10): el freno A4 deja SALTAR
 *  clips frenados sin consumir el tope de 3 intentos, así que el
 *  ciclo puede mirar más allá del top-3 atascado; el tope de 10
 *  acota las lecturas por ciclo (la Cola puede tener 150+ clips y
 *  cada evaluación es 1-2 lecturas). */
const AUTO_ENQUEUE_MAX_EVALUATED_PER_CYCLE = 10;

// ---------------------------------------------------------------------------
// Freno del auto-enqueue por clip (A4, 2026-11-20).
// Freno del auto-enqueue por clip (A4, 2026-11-20; persistencia
// D1 2026-11-26, migración 0045).
// Estado en MEMORIA del Worker (rápido, sobrevive a la cuota
// agotada) + PERSISTENCIA en D1 (tabla auto_enqueue_attempts):
// cada intento fallido hace upsert (attempts + 1) y la siembra tras
// un reinicio lee el valor REAL, no 1. Antes de 0045 el contador
// vivía solo en memoria y la siembra asumía 1 desde clip_events:
// al reciclarse el isolate, los mismos clips con bloqueo duro se
// re-evaluaban eternamente sin llegar nunca a 3 y el escaneo no
// avanzaba (seba 2026-11-26: 0 jobs creados en el día).
// Un "intento fallido" es un ciclo en el que el clip no encoló
// ningún job (todas sus plataformas frenaron). Tras 3 intentos
// fallidos en el día, el clip no se vuelve a intentar hasta mañana;
// entre intentos, el backoff 1/5/15 min (ver autoEnqueueAllowed en
// retry.ts). Un intento que encola jobs limpia el contador (memoria
// + fila D1): el clip progresó.
// La escritura D1 es best-effort (try/catch, nunca levanta): si D1
// falla (p. ej. cuota 7500), el freno en memoria sigue valiendo
// ese ciclo. Coste: 1 escritura por clip frenado y ciclo (máx
// 5/ciclo).
// ---------------------------------------------------------------------------
const autoEnqueueState = new Map<number, { dayKey: string; attemptsToday: number; lastAttemptMs: number | null }>();

export function getAutoEnqueueState(clipId: number, todayKey: string): AutoEnqueueAttemptState | null {
  const st = autoEnqueueState.get(clipId);
  if (!st || st.dayKey !== todayKey) return null;
  return { attemptsToday: st.attemptsToday, lastAttemptMs: st.lastAttemptMs };
}

/** Registra un intento fallido: memoria + upsert en D1
 *  (auto_enqueue_attempts, migración 0045). UPDATE primero y, si
 *  no hay fila, INSERT (se evita ON CONFLICT: mismo patrón que
 *  db.ts). Si otro ciclo insertó la fila entre medias, el INSERT
 *  falla por la PK y se reintenta el UPDATE una vez. Nunca levanta:
 *  si D1 falla, el freno en memoria sigue vigente este ciclo. */
export async function recordAutoEnqueueAttempt(
  db: D1Database,
  tenant: Tenant,
  clipId: number,
  now: number,
  todayKey: string,
): Promise<void> {
  const prev = autoEnqueueState.get(clipId);
  const attemptsToday = prev && prev.dayKey === todayKey ? prev.attemptsToday + 1 : 1;
  autoEnqueueState.set(clipId, { dayKey: todayKey, attemptsToday, lastAttemptMs: now });
  try {
    const updated = await db
      .prepare(
        "UPDATE auto_enqueue_attempts SET attempts = attempts + 1, last_at = ? WHERE tenant_id = ? AND clip_id = ? AND day = ?",
      )
      .bind(now, tenant, clipId, todayKey)
      .run();
    if ((updated.meta?.changes ?? 0) === 0) {
      try {
        await db
          .prepare(
            "INSERT INTO auto_enqueue_attempts (tenant_id, clip_id, day, attempts, last_at) VALUES (?, ?, ?, 1, ?)",
          )
          .bind(tenant, clipId, todayKey, now)
          .run();
      } catch {
        // Carrera: otro ciclo insertó la fila entre el UPDATE y el
        // INSERT; el intento cuenta igual.
        await db
          .prepare(
            "UPDATE auto_enqueue_attempts SET attempts = attempts + 1, last_at = ? WHERE tenant_id = ? AND clip_id = ? AND day = ?",
          )
          .bind(now, tenant, clipId, todayKey)
          .run()
          .catch(() => {});
      }
    }
  } catch {
    // Sin persistencia: el freno en memoria sigue vigente.
  }
}

export function clearAutoEnqueueState(clipId: number): void {
  autoEnqueueState.delete(clipId);
}

/** Limpieza persistida del contador (éxito: el clip progresó).
 *  Borra la fila de hoy en auto_enqueue_attempts para que una
 *  siembra posterior no resucite intentos ya limpiados. Nunca
 *  levanta (best-effort, como el registro). */
export async function clearAutoEnqueueAttemptPersisted(
  db: D1Database,
  tenant: Tenant,
  clipId: number,
  todayKey: string,
): Promise<void> {
  try {
    await db
      .prepare("DELETE FROM auto_enqueue_attempts WHERE tenant_id = ? AND clip_id = ? AND day = ?")
      .bind(tenant, clipId, todayKey)
      .run();
  } catch {
    // Sin limpieza persistida: la memoria ya está limpia y la
    // siembra de un contador viejo es conservadora (1 intento
    // menos ese día), nunca un bloqueo nuevo.
  }
}

/** Solo para tests: vacía el estado en memoria del auto-enqueue. */
export function resetAutoEnqueueStateForTests(): void {
  autoEnqueueState.clear();
}

/** Siembra el estado de un clip desde auto_enqueue_attempts
 *  (migración 0045). Sin estado en memoria para hoy y con fila hoy
 *  en D1, deja { attemptsToday: valor real, lastAttemptMs }. Sin
 *  fila hoy, no deja nada (el clip se intenta sin freno). Nunca
 *  levanta: si la lectura falla, el freno en memoria empieza de
 *  cero. */
export async function seedAutoEnqueueStateFromReads(
  db: D1Database,
  tenant: Tenant,
  clipId: number,
  todayKey: string,
  _now: number,
): Promise<void> {
  const prev = autoEnqueueState.get(clipId);
  if (prev && prev.dayKey === todayKey) return;
  try {
    const row = await db
      .prepare(
        "SELECT attempts, last_at FROM auto_enqueue_attempts WHERE tenant_id = ? AND clip_id = ? AND day = ?",
      )
      .bind(tenant, clipId, todayKey)
      .first<{ attempts: number; last_at: number | null }>();
    if (row && row.attempts > 0) {
      autoEnqueueState.set(clipId, { dayKey: todayKey, attemptsToday: row.attempts, lastAttemptMs: row.last_at });
    }
  } catch {
    // Sin siembra: el freno en memoria empieza de cero.
  }
}

export async function autoEnqueueTopClips(env: Env, tenant: Tenant): Promise<AutoEnqueueResult> {
  const none: AutoEnqueueResult = { enabled: false, considered: 0, enqueued: 0, blocked: 0 };
  try {
    const settings = await getSettings(env.DB, tenant);
    const eff = effectiveSettings(settings);
    if (!eff.auto_publish_top) return none;
    // A4: circuito 7500 — con la cuota agotada ni se mira la Cola:
    // cada intento escribiría eventos que morirían con 7500.
    if (await checkQuotaBreaker(env.DB, tenant, Date.now())) {
      return { enabled: true, considered: 0, enqueued: 0, blocked: 0 };
    }
    const queue = await listQueueClips(env.DB, tenant);
    const today = santiagoDayKey();
    let considered = 0;
    let enqueued = 0;
    let blocked = 0;
    let qualityBlocked = 0;
    let evaluated = 0;
    let scanned = 0;
    // Intenta encolar un clip (F1/F1b/F3/F4): calcula las
    // plataformas que aún necesitan trabajo, respeta el freno A4
    // y llama a tryEnqueueClipPublish con ese conjunto como
    // override. Devuelve true si encoló al menos un job.
    const attemptClip = async (clip: ClipRow, countEvaluated: boolean): Promise<boolean> => {
      // B4.1 (spec 2026-11-27): un clip con veredicto rechazado
      // FIRME no se evalúa: ni considered/evaluated, ni intento
      // A4, ni evento de freno. El salto va ANTES de la siembra
      // A4, del chequeo A4 y del cálculo de `need`.
      if (qualityGateForClip(clip).hard === true) return false;
      const need = await platformsNeedingWork(env.DB, tenant, clip);
      if (need.length === 0) return false;
      // Freno A4 por clip (memoria + siembra por lectura): un clip
      // que ya falló hoy no se reintenta cada minuto. Sin escribir
      // nada: el salto no deja evento (el motivo ya quedó en
      // Tracking en el intento que frenó).
      await seedAutoEnqueueStateFromReads(env.DB, tenant, clip.id, today, Date.now());
      const verdict = autoEnqueueAllowed(
        getAutoEnqueueState(clip.id, today) ?? { attemptsToday: 0, lastAttemptMs: null },
        Date.now(),
      );
      if (!verdict.allowed) return false;
      // F4: solo los clips que llegan a tryEnqueueClipPublish
      // consumen presupuesto de evaluación (los saltos por
      // hasVigente y por A4 son gratis).
      considered++;
      if (countEvaluated) evaluated++;
      const r = await tryEnqueueClipPublish(env, tenant, clip, { actor: "worker", logToWorkerEvents: true, platforms: need });
      blocked += r.blocked.length;
      // Puerta de calidad B4: los bloqueos por calidad cuentan
      // aparte; si hubo al menos uno, se revisa la tasa del día
      // (solo lecturas + aviso si supera el 30%, nunca tumba el
      // ciclo: maybeAlertQualityDiscards nunca levanta).
      qualityBlocked += r.blocked.filter((b) => b.includes(": calidad:")).length;
      if (r.jobs.length > 0) {
        clearAutoEnqueueState(clip.id);
        await clearAutoEnqueueAttemptPersisted(env.DB, tenant, clip.id, today);
        enqueued += r.jobs.length;
        await logWorkerEvent(
          env.DB,
          "auto_publish",
          `Auto-enqueue: clip #${clip.id} encolado en ${r.jobs.map((j) => j.platform).join(", ")} (top de la Cola con boost).`,
          tenant,
        ).catch(() => {});
        return true;
      }
      // F3: 0 jobs = todas las plataformas frenaron: intento
      // fallido A4 SOLO si al menos un motivo es duro (los
      // transitorios — espaciamiento, 15 min, 1-por-franja, fuera
      // de franja — no aparcan el clip: se re-evalúa en el
      // siguiente ciclo).
      if (hasHardBlock(r.blocked)) await recordAutoEnqueueAttempt(env.DB, tenant, clip.id, Date.now(), today);
      return false;
    };
    for (const clip of queue) {
      if (considered >= AUTO_ENQUEUE_MAX_CLIPS_PER_CYCLE || evaluated >= AUTO_ENQUEUE_MAX_EVALUATED_PER_CYCLE || scanned >= AUTO_ENQUEUE_MAX_SCANNED_PER_CYCLE) break;
      scanned++;
      await attemptClip(clip, true);
    }
    // F1b.2 (spec 2026-11-24): clips sellados como `publicado`
    // con plataformas que les faltan (seba 15, 173; kaez 4).
    // Después del bucle principal y solo si `considered` no llegó
    // al tope: se escanean los 5 más recientes con la misma
    // lógica por plataforma de F1, acotado a 2 clips sellados
    // por ciclo.
    if (considered < AUTO_ENQUEUE_MAX_CLIPS_PER_CYCLE) {
      const sealed = await listPublishedClips(env.DB, tenant, 5).catch(() => [] as ClipRow[]);
      let sealedDone = 0;
      for (const clip of sealed) {
        if (considered >= AUTO_ENQUEUE_MAX_CLIPS_PER_CYCLE || sealedDone >= 2) break;
        if (await attemptClip(clip, false)) sealedDone++;
      }
    }
    // PASE DEL PISO 3/DÍA (SPEC piso-3-por-dia v2 §2): después del
    // bucle principal y del pase de sellados, y solo con
    // presupuesto de ciclo en los TRES contadores, se cubre el
    // piso de cada canal (publicados + programados de hoy < piso
    // efectivo) encolando desde la Cola en orden. Comparte
    // presupuesto (no lo amplía) y no relaja ninguna puerta: el
    // gate decide cada job igual que en el bucle principal.
    // NOTA DE DISEÑO: la spec describe el pase por canal ("para
    // cada canal, recorre la Cola"); el contador `scanned` se
    // incrementa una vez por clip (cuando se calcula su `need`,
    // cacheado entre canales), no una por par (clip, canal): un
    // clip visitado para 3 canales es 1 clip visitado, no 3, y
    // así el pase cabe en el presupuesto compartido de 60.
    if (considered < AUTO_ENQUEUE_MAX_CLIPS_PER_CYCLE && evaluated < AUTO_ENQUEUE_MAX_EVALUATED_PER_CYCLE && scanned < AUTO_ENQUEUE_MAX_SCANNED_PER_CYCLE) {
      const nowMs = Date.now();
      const dayStartMs = santiagoWallToUtcMs(today, "00:00");
      const dayEndMs = santiagoWallToUtcMs(santiagoDayKeyMs(nowMs + 86400000), "00:00");
      // Canales vivos: filas api_accounts → plataformas de panel,
      // con piso efectivo y contadores de hoy. Se saltan SIN
      // evento los canales sin credencial, en pausa o en día
      // libre (el plan ya los alerta) y los de piso 0.
      const channels: FloorChannelState[] = [];
      for (const row of await listApiAccounts(env.DB, tenant)) {
        const panels: PanelPlatform[] = row.platform === "youtube"
          ? ["youtube_shorts"]
          : row.platform === "tiktok"
            ? ["tiktok"]
            : row.platform === "meta"
              ? ["instagram_reels", "facebook_reels"]
              : [];
        const lang = row.language === "en" ? "en" : "es";
        if (!(row.verify_ok === 1 && !!(row.credential_enc || row.credential))) continue;
        const pausedUntil = accountPausedUntilMs(settings, row.id);
        if (pausedUntil !== null && nowMs < pausedUntil) continue;
        if (isRestDayEffective(`acc-${row.id}`, nowMs, settings, row.id)) continue;
        for (const panel of panels) {
          const mat = maturityOut(row, eff, nowMs, toApiPlatform(panel));
          const floor = Math.min(DAILY_FLOOR_PER_PLATFORM, mat.effective_daily_plan);
          if (floor <= 0) continue;
          channels.push({
            key: `${panel}|${lang}`,
            platform: panel,
            language: lang,
            floor,
            postedToday: 0,
            scheduledToday: 0,
            lastPostedAtMs: null,
            pendingMaxAtMs: null,
            tried: new Set<number>(),
            blocked: [],
          });
        }
      }
      // Contadores de hoy por canal en UNA lectura: publicados
      // (posted_day = hoy, con su última hora para la cota de
      // derrame) y programados (next_run_at dentro de hoy).
      const jobRows = await env.DB.prepare(
        "SELECT platform, language, state, posted_day, next_run_at, updated_at FROM jobs WHERE tenant_id = ? AND kind = 'publish' AND state IN ('posted','scheduled','processing')",
      )
        .bind(tenant)
        .all<{ platform: string; language: string; state: string; posted_day: string | null; next_run_at: number | null; updated_at: number }>()
        .catch(() => ({ results: [] as Array<{ platform: string; language: string; state: string; posted_day: string | null; next_run_at: number | null; updated_at: number }> }));
      for (const ch of channels) {
        for (const j of jobRows.results) {
          if (j.platform !== ch.platform || j.language !== ch.language) continue;
          if (j.state === "posted") {
            if (j.posted_day === today) ch.postedToday++;
            if (j.updated_at > (ch.lastPostedAtMs ?? 0)) ch.lastPostedAtMs = j.updated_at;
          } else if (j.next_run_at !== null && j.next_run_at >= dayStartMs && j.next_run_at < dayEndMs) {
            ch.scheduledToday++;
          }
          if (j.state === "scheduled" && j.next_run_at !== null) {
            ch.pendingMaxAtMs = Math.max(ch.pendingMaxAtMs ?? 0, j.next_run_at);
          }
        }
      }
      // Orden: mayor déficit primero; empate YT → TT → IG → FB;
      // ES antes que EN.
      const platformRank = (p: PanelPlatform): number => (p === "youtube_shorts" ? 0 : p === "tiktok" ? 1 : p === "instagram_reels" ? 2 : 3);
      channels.sort((a, b) => {
        const da = a.floor - (a.postedToday + a.scheduledToday);
        const db = b.floor - (b.postedToday + b.scheduledToday);
        if (db !== da) return db - da;
        if (platformRank(a.platform) !== platformRank(b.platform)) return platformRank(a.platform) - platformRank(b.platform);
        return (a.language === "es" ? 0 : 1) - (b.language === "es" ? 0 : 1);
      });
      const needCache = new Map<number, PanelPlatform[]>();
      // Clausura gemela de attemptClip, con platforms=[canal] y
      // la cota de no-derrame. Comparte los contadores del ciclo
      // y el freno A4.
      const attemptClipForChannel = async (clip: ClipRow, ch: FloorChannelState): Promise<boolean> => {
        // B4.1 (spec 2026-11-27): como en attemptClip — un
        // rechazado firme no se evalúa (ni `tried`, ni `need`, ni
        // A4, ni evento).
        if (qualityGateForClip(clip).hard === true) return false;
        if (ch.tried.has(clip.id)) return false;
        ch.tried.add(clip.id);
        let need = needCache.get(clip.id);
        if (need === undefined) {
          scanned++;
          need = await platformsNeedingWork(env.DB, tenant, clip);
          needCache.set(clip.id, need);
        }
        if (!need.includes(ch.platform)) return false;
        if (clipLanguage(clip) !== ch.language) return false;
        // SIN DERRAME (§2): si el next_run_at calculado caería
        // fuera de hoy, no se crea (el ciclo de mañana lo cubre).
        // Cota conservadora con el retardo máximo (180 min):
        // último publicado + 180, último programado + 90, ahora.
        const bound = Math.max(
          nowMs,
          ch.lastPostedAtMs !== null ? ch.lastPostedAtMs + MAX_PUBLISH_SPACING_MS : 0,
          ch.pendingMaxAtMs !== null ? ch.pendingMaxAtMs + MIN_PUBLISH_SPACING_MS : 0,
        );
        if (bound >= dayEndMs) return false;
        await seedAutoEnqueueStateFromReads(env.DB, tenant, clip.id, today, Date.now());
        const verdict = autoEnqueueAllowed(
          getAutoEnqueueState(clip.id, today) ?? { attemptsToday: 0, lastAttemptMs: null },
          Date.now(),
        );
        if (!verdict.allowed) return false;
        considered++;
        evaluated++;
        const r = await tryEnqueueClipPublish(env, tenant, clip, { actor: "worker", logToWorkerEvents: true, platforms: [ch.platform] });
        ch.blocked.push(...r.blocked);
        blocked += r.blocked.length;
        qualityBlocked += r.blocked.filter((b) => b.includes(": calidad:")).length;
        if (r.jobs.length > 0) {
          clearAutoEnqueueState(clip.id);
          await clearAutoEnqueueAttemptPersisted(env.DB, tenant, clip.id, today);
          enqueued += r.jobs.length;
          for (const j of r.jobs) {
            ch.scheduledToday++;
            if (j.next_run_at !== null && j.next_run_at !== undefined) {
              ch.pendingMaxAtMs = Math.max(ch.pendingMaxAtMs ?? 0, j.next_run_at);
            }
          }
          await logWorkerEvent(
            env.DB,
            "auto_publish",
            `Auto-enqueue (piso ${ch.floor}/día): clip #${clip.id} encolado en ${ch.platform} (${ch.language}).`,
            tenant,
          ).catch(() => {});
          return true;
        }
        if (hasHardBlock(r.blocked)) await recordAutoEnqueueAttempt(env.DB, tenant, clip.id, Date.now(), today);
        return false;
      };
      for (const ch of channels) {
        while (ch.postedToday + ch.scheduledToday < ch.floor) {
          if (considered >= AUTO_ENQUEUE_MAX_CLIPS_PER_CYCLE || evaluated >= AUTO_ENQUEUE_MAX_EVALUATED_PER_CYCLE || scanned >= AUTO_ENQUEUE_MAX_SCANNED_PER_CYCLE) break;
          let progressed = false;
          for (const clip of queue) {
            if (ch.postedToday + ch.scheduledToday >= ch.floor) break;
            if (considered >= AUTO_ENQUEUE_MAX_CLIPS_PER_CYCLE || evaluated >= AUTO_ENQUEUE_MAX_EVALUATED_PER_CYCLE || scanned >= AUTO_ENQUEUE_MAX_SCANNED_PER_CYCLE) break;
            if (clipLanguage(clip) !== ch.language) continue;
            if (await attemptClipForChannel(clip, ch)) progressed = true;
          }
          // Una pasada completa por la Cola sin encolar nada para
          // este canal no se repite: el gate no cambia dentro del
          // ciclo (los jobs creados para otros canales solo pueden
          // endurecerlo, nunca aflojarlo).
          if (!progressed) break;
        }
      }
      // Evento de déficit por canal que sigue corto (un evento por
      // canal y día; la deduplicación vive en floorEventLoggedDays).
      for (const ch of channels) {
        const have = ch.postedToday + ch.scheduledToday;
        if (have >= ch.floor) continue;
        const eventKey = `${tenant}|${today}|${ch.key}`;
        if (floorEventLoggedDays.has(eventKey)) continue;
        floorEventLoggedDays.add(eventKey);
        const motivo = dominantFloorMotive(ch.blocked);
        await logWorkerEvent(
          env.DB,
          "auto_publish",
          `Piso ${ch.floor}/día no cubierto en ${ch.platform} ${ch.language}: ${ch.postedToday}/${ch.floor} publicados hoy, motivo: ${motivo}.`,
          tenant,
        ).catch(() => {});
      }
    }
    if (qualityBlocked > 0) {
      await maybeAlertQualityDiscards(env.DB, tenant, env).catch(() => "failed" as const);
    }
    return { enabled: true, considered, enqueued, blocked, qualityBlocked };
  } catch (e) {
    // Que el auto-enqueue falle nunca tumba el ciclo ni la cola.
    await logWorkerEvent(
      env.DB,
      "auto_publish",
      `Auto-enqueue falló: ${e instanceof Error ? e.message : String(e)}`,
      tenant,
    ).catch(() => {});
    return { enabled: true, considered: 0, enqueued: 0, blocked: 0 };
  }
}

/** Plataformas que aún necesitan trabajo para un clip (F1, spec
 *  2026-11-24): la unión de las del clip y sus cuentas vinculadas,
 *  menos las que ya tienen un job `scheduled`/`processing` o un
 *  `posted` (de hoy o de otro día: posted-ever). Vacío = el clip
 *  se salta sin consumir presupuesto (ver F4). */
async function platformsNeedingWork(
  db: D1Database,
  tenant: Tenant,
  clip: ClipRow,
): Promise<PanelPlatform[]> {
  const vigente = await db.prepare(
    "SELECT id, platform, state, posted_day FROM jobs WHERE tenant_id = ? AND kind = 'publish' AND clip_id = ?",
  )
    .bind(tenant, clip.id)
    .all<{ id: number; platform: string; state: string; posted_day: string | null }>()
    .catch(() => ({ results: [] as Array<{ id: number; platform: string; state: string; posted_day: string | null }> }));
  const linked = await linkedPlatforms(db, tenant, clipLanguage(clip));
  const listed = parsePlatforms(clip.platforms);
  return [...new Set([...listed, ...linked])].filter(
    (p) => !vigente.results.some(
      (j) => j.platform === p && (j.state === "scheduled" || j.state === "processing" || j.state === "posted"),
    ),
  );
}

/** ¿Hay al menos un bloqueo DURO en `blocked`? (F3, spec
 *  2026-11-24): las entradas son "<platform>: <motivo>"; se quita
 *  el prefijo de la plataforma y se clasifica con la tabla única
 *  de gate.ts. Solo un bloqueo duro cuenta como intento fallido
 *  A4; los transitorios (espaciamiento, 15 min, 1-por-franja,
 *  fuera de franja) no aparcan el clip. */
function hasHardBlock(blocked: string[]): boolean {
  return blocked.some((b) => {
    const reason = b.includes(": ") ? b.slice(b.indexOf(": ") + 2) : b;
    return !classifyGateBlock(reason).transient;
  });
}

/** Jobs listos para correr (scheduled y vencidos, o reintentos vencidos).
 * El worker vacía el segmento LIVE primero (diseño §4): el job hereda
 * el segmento de su clip (LEFT JOIN: un job sin clip, p. ej. vod_check,
 * cuenta como VOD). Dentro de cada segmento, el orden es el de la
 * Cola: manual_rank, prioridad (con boost por tops medidos, ver
 * queueClipCompare) y fecha; si no se puede calcular el boost (sin
 * tabla clip_stats en bases mínimas), queda el FIFO por created_at
 * de antes. Se piden 3x el límite y se recorta tras ordenar, para
 * que el boost pueda subir un job que el FIFO dejaría fuera. */
export async function dueJobs(db: D1Database, tenant: Tenant, limit = 5): Promise<JobRow[]> {
  const res = await db
    .prepare(
      "SELECT jobs.* FROM jobs LEFT JOIN clips ON clips.id = jobs.clip_id AND clips.tenant_id = jobs.tenant_id " +
        "WHERE jobs.tenant_id = ? AND jobs.state IN ('scheduled','processing') AND (jobs.next_run_at IS NULL OR jobs.next_run_at <= ?) " +
        "ORDER BY CASE WHEN clips.origin = 'live' THEN 0 ELSE 1 END, jobs.created_at ASC LIMIT ?",
    )
    .bind(tenant, nowMs(), limit * 3)
    .all<JobRow>();
  const rows = res.results;
  try {
    const clips = new Map<number, ClipRow>();
    for (const job of rows) {
      if (job.clip_id === null || clips.has(job.clip_id)) continue;
      const clip = await getClip(db, job.clip_id, tenant).catch(() => null);
      if (clip) clips.set(job.clip_id, clip);
    }
    const boost = boostBonusByClip(await listClipStats(db, tenant).catch(() => []));
    rows.sort((a, b) => {
      const ca = a.clip_id === null ? undefined : clips.get(a.clip_id);
      const cb = b.clip_id === null ? undefined : clips.get(b.clip_id);
      if (ca && cb) {
        const cmp = queueClipCompare(ca, cb, boost);
        if (cmp !== 0) return cmp;
      } else if (ca || cb) {
        const segA = ca?.origin === "live" ? 0 : 1;
        const segB = cb?.origin === "live" ? 0 : 1;
        if (segA !== segB) return segA - segB;
      }
      return a.created_at - b.created_at;
    });
  } catch {
    // Sin boost: queda el orden FIFO de la SQL.
  }
  return rows.slice(0, limit);
}

/** Jobs en processing más de 30 min: atascados → aviso + reintento.
 *  El reintento cuenta en el tope A4 (3/día): cada reencolado suma
 *  1 a attempts, con el backoff 1/5/15 min; agotado el tope, el job
 *  va a failed terminal (sin reintento hoy) en vez de reencolarse
 *  cada 30 min hasta mañana. */
export async function detectStalledJobs(db: D1Database, tenant: Tenant, env?: Env): Promise<JobRow[]> {
  const res = await db
    .prepare("SELECT * FROM jobs WHERE tenant_id = ? AND state = 'processing' AND updated_at <= ?")
    .bind(tenant, nowMs() - STALL_AFTER_MS)
    .all<JobRow>();
  const stalled = res.results;
  const today = santiagoDayKey();
  for (const job of stalled) {
    const doneToday = retriesToday(job, today);
    if (doneToday >= MAX_TRANSIENT_RETRIES) {
      await markFailed(
        db,
        job,
        `Job #${job.id} (${job.kind}${job.platform ? `/${job.platform}` : ""}) atascado en processing más de 30 min y con el tope de ${MAX_TRANSIENT_RETRIES} reintentos de hoy alcanzado: no se reintenta hasta mañana.`,
        false,
        env,
      ).catch(() => {});
      continue;
    }
    const msg = `Job #${job.id} (${job.kind}${job.platform ? `/${job.platform}` : ""}) atascado en processing más de 30 min: se reintenta solo.`;
    // El cupo del atascado se suelta antes de devolverlo a
    // scheduled (si no, el contador quedaría reservado todo el
    // día); al volver a scheduled se limpian token y cupo.
    await releasePublishSlot(db, job);
    const delay = nextRetryDelayMs(doneToday) ?? RETRY_BACKOFF_MS_SRC[0] ?? 60 * 1000;
    await db
      .prepare("UPDATE jobs SET state = 'scheduled', attempts = ?, next_run_at = ?, updated_at = ?, run_token = ?, slot_day = ? WHERE id = ? AND tenant_id = ?")
      .bind(doneToday + 1, nowMs() + delay, nowMs(), null, null, job.id, tenant)
      .run();
    await notify(db, { kind: "stall", clipId: job.clip_id, message: msg }, tenant, env);
  }
  return stalled;
}

/** Barrido de drafts transitorios (F6, spec 2026-11-24). Hasta 5
 *  jobs en `draft` de HOY cuyo `last_error` clasifique como
 *  transitorio se re-evalúan con el re-chequeo de ejecución; si
 *  pasa, vuelven a `scheduled` con `next_run_at=now` (escalonados
 *  15 min por clip, como en F1.3) y solo si no agotaron sus 3
 *  reintentos hoy. Si siguen frenando, no se tocan. Nunca toca
 *  motivos duros (calidad, sin cuenta, streamer desactivado,
 *  spam) ni jobs de días anteriores (esos los re-encola el
 *  auto-enqueue mañana si procede). No revive un job cuyo clip
 *  ya publicó hoy en esa plataforma: el propio re-chequeo lo
 *  frena con "ya se publicó hoy" (duro). Devuelve cuántos revivió. */
export async function sweepTransientDrafts(env: Env, tenant: Tenant, now: number): Promise<number> {
  const db = env.DB;
  const today = santiagoDayKeyMs(now);
  const dayStart = santiagoDayStartMs(now);
  const res = await db
    .prepare("SELECT * FROM jobs WHERE tenant_id = ? AND kind = 'publish' AND state = 'draft' AND updated_at >= ? ORDER BY updated_at ASC LIMIT 5")
    .bind(tenant, dayStart)
    .all<JobRow>()
    .catch(() => ({ results: [] as JobRow[] }));
  let revived = 0;
  const staggerByClip = new Map<number, number>();
  for (const job of res.results) {
    const raw = typeof job.last_error === "string" ? job.last_error : "";
    // El prefijo lo añade dispatchPublishStep (publish/index.ts):
    // se quita antes de clasificar.
    const reason = raw.startsWith("gate de publicación: ") ? raw.slice("gate de publicación: ".length) : raw;
    if (!classifyGateBlock(reason).transient) continue;
    // Tope de F2.5: si ya agotó sus 3 reintentos hoy, el barrido
    // no lo reviviría en bucle.
    if (retriesToday(job, today) >= MAX_RETRIES_PER_DAY) continue;
    const r = await recheckPublishGate(env, job).catch(() => ({ reason: "sweep: re-chequeo no disponible", lastPublishedAtMs: null }));
    if (r.reason) continue;
    const clipId = job.clip_id ?? 0;
    const idx = staggerByClip.get(clipId) ?? 0;
    staggerByClip.set(clipId, idx + 1);
    const changed = await fencedJobUpdate(
      db,
      job,
      "state = 'scheduled', next_run_at = ?, retryable = 1, last_error = ?, updated_at = ?, run_token = ?, slot_day = ?",
      [now + idx * 15 * 60 * 1000, null, now, null, null],
    ).catch(() => false);
    if (changed) revived++;
  }
  return revived;
}

// ---------------------------------------------------------------------------
// Transiciones (posted exige evidence_url: sin URL no hay posted).
// ---------------------------------------------------------------------------
export async function markPosted(db: D1Database, job: JobRow, platformId: string, evidenceUrl: string): Promise<void> {
  if (!evidenceUrl.startsWith("https://")) throw new Error("posted exige evidence_url https");
  // Valla de run_token (0022, §3): si el snapshot trae token y la
  // fila ya cambió de manos, el UPDATE cambia 0 filas y NO se
  // ejecuta ningún efecto secundario (ni published_count, ni clip,
  // ni evento). Sin token (puente, legacy) se mantiene lo antiguo.
  // postedAtMs es EL instante real de publicación: el mismo que
  // queda en jobs.updated_at y el que alimenta la ventana de 30 min
  // de Content Rewards (0041). Se calcula UNA vez y se reusa abajo.
  const postedAtMs = nowMs();
  const changed = await fencedJobUpdate(
    db,
    job,
    "state = 'posted', platform_id = ?, evidence_url = ?, posted_day = ?, updated_at = ?",
    [platformId, evidenceUrl, santiagoDayKey(), postedAtMs],
  );
  if (!changed) return;
  // 0017: una publicación real cuenta para la madurez de la cuenta
  // (first_published_at + published_count). Sin api_account_id no
  // hay cuenta a la que contársela.
  if (job.api_account_id) {
    await db
      .prepare(
        "UPDATE api_accounts SET published_count = published_count + 1, " +
          "first_published_at = COALESCE(first_published_at, ?) WHERE id = ? AND tenant_id = ?",
      )
      .bind(nowMs(), job.api_account_id, job.tenant_id)
      .run();
  }
  if (job.clip_id && job.platform) {
    await updateClipPublishState(db, job.clip_id, job.platform, {
      state: "publicado",
      publishedAt: new Date().toISOString(),
      note: "",
    }, job.tenant_id);
    await logClipEvent(db, job.clip_id, "publicado", "worker", `Publicado en ${job.platform}: ${evidenceUrl}`, false, job.tenant_id);
  }
  // Retención 24 h del MP4 en R2 (0039, orden 2026-11-21 21:28):
  // si este posted completa TODAS las plataformas del clip, se
  // fija el plazo de borrado (ahora + 24 h). Best-effort: nunca
  // tumba el posted (stampRetentionDeadline traga su propio fallo).
  if (job.clip_id) {
    await stampRetentionDeadline(db, job.clip_id, job.tenant_id).catch(() => false);
  }
  // DISPARADOR Content Rewards (0041, 2026-11-23): ESTE es el punto
  // real de publicación — no POST /api/clips/:id/publish (que solo
  // ENCOLA el job) ni el panel. Cuando el job pasa a posted con su
  // evidence_url, la fila pending de ese clip+plataforma recibe el
  // post_url y el publishedAtMs REALES y queda enviable 30 min.
  // ¿Por qué aquí y no en el panel? El panel no sabe cuándo publica
  // la plataforma: el ciclo publica después, por API, y solo el
  // Worker ve el evidence_url real. El Worker no puede empujar el
  // evento a la extensión (sin canal push): el disparo es la fila
  // en D1, que la extensión lee por GET (cola en nube). Best-effort
  // y sin tocar el posted: si la tabla 0041 no existe, no pasa nada.
  // Hueco conocido: el volcado del diario WAL (wal.ts) marca posted
  // con UPDATE directo sin pasar por aquí; lo cubre el relleno
  // backfillCampaignSubmissionsPosted en la lectura de pendientes.
  if (job.clip_id && job.platform) {
    await stampCampaignSubmissionPosted(db, job.tenant_id, job.clip_id, job.platform, evidenceUrl, postedAtMs).catch(() => 0);
  }
}

export async function markFailed(db: D1Database, job: JobRow, reason: string, retryable: boolean, env?: Env): Promise<void> {
  const now = nowMs();
  const today = santiagoDayKey();
  // A4 — error 7500 (cuota D1 agotada): parar de inmediato. El job
  // va a failed terminal SIN reintento (un reintento escribiría de
  // nuevo contra una cuota agotada), se dispara el circuito para
  // que los demás bucles paren y se intenta UNA sola alerta. El
  // UPDATE mismo puede fallar por la cuota: se traga, el freno ya
  // está en memoria y el job se re-encola mañana por la Cola.
  if (isD1QuotaError(reason)) {
    tripQuotaBreaker(now);
    await alertQuotaExhausted(db, job.tenant_id, env, now).catch(() => {});
    try {
      await fencedJobUpdate(
        db,
        job,
        "state = 'failed', attempts = ?, last_error = ?, retryable = 0, updated_at = ?, run_token = ?, slot_day = ?",
        [retriesToday(job, today) + 1, `${reason} (cuota D1 agotada: sin reintento hasta mañana)`, now, null, null],
      );
    } catch {
      // La propia escritura murió por la cuota.
    }
    return;
  }
  // A4 — backoff 1/5/15/60 + tope 3 reintentos por job y día
  // (Santiago). `attempts` se cuenta por día (derivado de la fila:
  // ver retriesToday): el primer fallo de un día nuevo vuelve a
  // contar desde 1, así que el contador se resetea solo al cambiar
  // de día. Agotado el tope, el job queda parado hasta mañana:
  // failed terminal y la Cola lo puede volver a encolar mañana.
  const doneToday = retriesToday(job, today);
  const attempts = doneToday + 1;
  if (retryable && doneToday < MAX_TRANSIENT_RETRIES) {
    const delay = nextRetryDelayMs(doneToday) ?? RETRY_BACKOFF_MS_SRC[0] ?? 60 * 1000;
    // Vuelve a scheduled: se limpian token y cupo (0022).
    const changed = await fencedJobUpdate(
      db,
      job,
      "state = 'scheduled', attempts = ?, last_error = ?, retryable = 1, next_run_at = ?, updated_at = ?, run_token = ?, slot_day = ?",
      [attempts, reason, now + delay, now, null, null],
    );
    if (!changed) return;
    if (job.clip_id) {
      await logClipEvent(db, job.clip_id, "publicado", "worker", `Fallo transitorio en ${job.platform ?? job.kind} (${attempts}/${MAX_TRANSIENT_RETRIES} hoy): ${reason}. Reintento automático en ${Math.round(delay / 60000)} min.`, true, job.tenant_id);
    }
    return;
  }
  // Fallo terminal: tope del día agotado o fallo no reintentable.
  // Se limpian token y cupo (0022).
  const changed = await fencedJobUpdate(
    db,
    job,
    "state = 'failed', attempts = ?, last_error = ?, retryable = 0, updated_at = ?, run_token = ?, slot_day = ?",
    [attempts, doneToday >= MAX_TRANSIENT_RETRIES ? `${reason} (tope de ${MAX_TRANSIENT_RETRIES} reintentos de hoy alcanzado: parado hasta mañana)` : reason, now, null, null],
  );
  if (!changed) return;
  if (job.clip_id && job.platform) {
    await updateClipPublishState(db, job.clip_id, job.platform, { state: "fallido", note: reason }, job.tenant_id);
  }
  await notify(db, {
    kind: "failed",
    clipId: job.clip_id,
    message: `Falló la subida en ${job.platform ?? job.kind} (clip #${job.clip_id ?? "?"}): ${reason}. Revisar en Tracking.`,
  }, job.tenant_id, env);
}

export async function markDraft(db: D1Database, job: JobRow, reason: string, env?: Env): Promise<void> {
  // Draft terminal: se limpian token y cupo (0022).
  const changed = await fencedJobUpdate(
    db,
    job,
    "state = 'draft', last_error = ?, retryable = 0, updated_at = ?, run_token = ?, slot_day = ?",
    [reason, nowMs(), null, null],
  );
  if (!changed) return;
  if (job.clip_id && job.platform) {
    await updateClipPublishState(db, job.clip_id, job.platform, { state: "pendiente", note: reason }, job.tenant_id);
  }
  await notify(db, {
    kind: "info",
    clipId: job.clip_id,
    message: `Borrador en ${job.platform ?? job.kind} (clip #${job.clip_id ?? "?"}): ${reason}`,
  }, job.tenant_id, env);
}

// ---------------------------------------------------------------------------
// El ciclo automático del Worker: lo llama el cron cada 5 min, SOLO si
// el Worker está iniciado (worker_state.running = 1). Avanza la cola
// sin que el usuario pulse nada: detecta atascados, toma jobs
// vencidos y ejecuta un paso de publicación por job.
// ---------------------------------------------------------------------------
export async function isWorkerRunning(db: D1Database, tenant: Tenant): Promise<boolean> {
  const row = await db.prepare("SELECT running FROM worker_state WHERE tenant_id = ?").bind(tenant).first<{ running: number }>();
  return row?.running === 1;
}

export async function setWorkerRunning(db: D1Database, tenant: Tenant, running: boolean, note: string): Promise<void> {
  await db
    .prepare(
      "INSERT INTO worker_state (tenant_id, running, started_at, last_note, updated_at) VALUES (?, ?, ?, ?, ?) " +
        "ON CONFLICT(tenant_id) DO UPDATE SET running = excluded.running, started_at = excluded.started_at, last_note = excluded.last_note, updated_at = excluded.updated_at",
    )
    .bind(tenant, running ? 1 : 0, running ? nowMs() : null, note, nowMs())
    .run();
  await logWorkerEvent(db, running ? "inicio" : "parada", note, tenant);
}

/** Reclamo ATÓMICO de un job (hallazgo M4, 2026-11-18): la
 *  transición scheduled → processing es una sola UPDATE
 *  condicional. Devuelve true solo si este claimant ganó la
 *  carrera (D1 meta.changes == 1); si otro claimant (puente o
 *  cron) ya lo tomó, devuelve false y el llamador NO lo ejecuta. */
export async function claimJob(db: D1Database, id: number, tenant: Tenant): Promise<boolean> {
  const res = await db
    .prepare("UPDATE jobs SET state = 'processing', updated_at = ? WHERE id = ? AND tenant_id = ? AND state = 'scheduled'")
    .bind(nowMs(), id, tenant)
    .run();
  return (res.meta.changes ?? 0) === 1;
}

/** Reclamo del ciclo para un job 'scheduled' (marca quién lo
 *  tiene: el ciclo). El puente sigue usando claimJob.
 *  El reclamo pone next_run_at 300 s en el futuro (lease): mientras
 *  el paso corre, otro ciclo (cron + run-once a la vez) ve el job
 *  como no vencido y no lo toma. Sin lease, dos ciclos podían
 *  ejecutar el mismo chunk a la vez y Facebook 412aba el segundo.
 *  300 s (no 150 s): un chunk de 20 MB a Facebook puede tardar
 *  más de 150 s entre leer R2 y el POST a rupload; con 150 s el
 *  lease vencía a mitad de paso, otro ciclo re-reclamaba el job
 *  y re-subía el mismo chunk, lo que desincronizaba la sesión
 *  (412 → sesión nueva) en bucle y terminó con Facebook limitando
 *  la Página por spam (clip 7, job 4, 2026-11-18). */
export async function claimJobForCycle(db: D1Database, id: number, tenant: Tenant): Promise<boolean> {
  const res = await db
    .prepare("UPDATE jobs SET state = 'processing', claimed_by = 'worker', run_token = ?, next_run_at = ?, updated_at = ? WHERE id = ? AND tenant_id = ? AND state = 'scheduled'")
    .bind(newRunToken(), nowMs() + 300_000, nowMs(), id, tenant)
    .run();
  return (res.meta.changes ?? 0) === 1;
}

/** Reanudar un job 'processing' que el ciclo ya tiene: solo si
 *  lo reclamó el ciclo (claimed_by = 'worker') y ya venció su
 *  next_run_at. Un job en manos del puente (claimed_by =
 *  'bridge' o NULL sin next_run_at) NUNCA se reanuda aquí, así
 *  que no hay doble ejecución. Atómico: changes == 1 gana. */
export async function resumeJobForCycle(db: D1Database, id: number, tenant: Tenant): Promise<boolean> {
  const res = await db
    .prepare("UPDATE jobs SET state = 'processing', run_token = ?, next_run_at = ?, updated_at = ? WHERE id = ? AND tenant_id = ? AND state = 'processing' AND claimed_by = 'worker' AND next_run_at <= ?")
    .bind(newRunToken(), nowMs() + 300_000, nowMs(), id, tenant, nowMs())
    .run();
  return (res.meta.changes ?? 0) === 1;
}

// B1 — Despacho con diario: la intención ya la escribió
// dispatchPublishStep antes de subir; aquí solo se añade el
// RESULTADO final (posted / already_published / error) al diario.
// Los pasos intermedios (processing, draft) no dejan resultado:
// la subida sigue en curso o no empezó, y el volcado solo
// reconcilia resultados finales. Nunca lanza: si R2 falla, el
// registro en D1 por el camino normal sigue valiendo.
export async function dispatchPublishStepWithWal(env: Env, job: JobRow): Promise<PublishResult> {
  const result = await dispatchPublishStep(env, job);
  if (result.mode === "posted") {
    await appendWalEntry(env, job.tenant_id, {
      type: "result",
      clip_id: job.clip_id,
      platform: job.platform,
      account_id: job.api_account_id ?? null,
      job_id: job.id,
      idempotency_key: job.idempotency_key,
      day: santiagoDayKey(),
      outcome: result.alreadyPublished ? "already_published" : "posted",
      platform_id: result.platformId,
      url: result.evidenceUrl,
    });
  } else if (result.mode === "error") {
    await appendWalEntry(env, job.tenant_id, {
      type: "result",
      clip_id: job.clip_id,
      platform: job.platform,
      account_id: job.api_account_id ?? null,
      job_id: job.id,
      idempotency_key: job.idempotency_key,
      day: santiagoDayKey(),
      outcome: "error",
      error: result.reason,
    });
  }
  return result;
}

export async function runWorkerCycle(
  env: Env,
  tenant: Tenant,
  opts?: { force?: boolean },
): Promise<{ ran: boolean; processed: number; stalled: number; autoEnqueued: number; gate: CycleGateDecision; retention?: RetentionSweepResult | null }> {
  const db = env.DB;
  if (!(await isWorkerRunning(db, tenant))) return { ran: false, processed: 0, stalled: 0, autoEnqueued: 0, gate: idleCycleGate() };
  // A3 (2026-11-20) — "cuando toca", no cada minuto: el cron sigue
  // cada minuto, pero el ciclo solo trabaja si la puerta dice que
  // hay algo que hacer (un job que vence en el próximo minuto, la
  // revisión periódica de 12 min, o diario pendiente de volcar).
  // La salida temprana es SILENCIOSA: ni siquiera un evento "nada
  // que hacer" (eso era lo que gastaba la cuota). La puerta trae
  // además si algún dispatch está pendiente (live/watchdog/daily):
  // el scheduled handler los dispara aunque el publicador no
  // corriera. force=true es solo para el avance manual
  // (/api/worker/run-once): un humano pidiendo un ciclo lo tiene.
  const gate = await cycleGate(env, tenant, nowMs());
  if (!gate.run && !opts?.force) {
    return { ran: false, processed: 0, stalled: 0, autoEnqueued: 0, gate };
  }
  // El ciclo completo corre: es una revisión (reevalúa la Cola
  // entera), así que se marca la hora — UNA escritura en
  // worker_state, la que hace que la próxima revisión toque dentro
  // de 12 min y no en el minuto siguiente. A4: es solo
  // contabilidad; si la escritura muere por la cuota (7500), el
  // ciclo SIGUE (publicar por el diario B1 no depende de ella) y
  // la próxima revisión simplemente vuelve a tocar.
  await markCycleReview(db, tenant, nowMs()).catch(() => undefined);
  // B1 — VOLCADO DEL DIARIO (R2 → D1, un solo lote por día): si
  // la cuota volvió, los jobs subidos mientras D1 estaba caída se
  // marcan posted/failed aquí. Ante un 7500 el volcado se retira
  // sin reintentar hasta el siguiente ciclo (freno de A4). Nunca
  // tumba el ciclo: cualquier fallo queda para el ciclo siguiente.
  await flushWal(env, tenant).catch(() => undefined);
  // Guardián de presupuesto (A1, 2026-11-20): se crea una vez
  // por ciclo (el contador se cachea 5 min en memoria) y queda
  // vigente para todo el ciclo: los logs lo consultan solos y
  // aquí se frenan el auto-enqueue masivo, el recálculo de
  // madurez y la recogida de analíticas cuando no toca escribir
  // nada no esencial. Lo esencial (reclamar jobs y marcarlos)
  // nunca se frena: eso lo garantiza shouldWrite("essential").
  // El guardián nunca levanta: sin contador, es fail-open. El
  // volcado del diario (arriba) va ANTES: marcar posted es
  // esencial y no se frena.
  const guard = await createBudgetGuard(env, db, tenant, Date.now());
  setCurrentBudgetGuard(guard);
  // Auto-enqueue de tops medidos (2026-11-19): DESPUÉS del chequeo
  // isWorkerRunning — es el único punto donde /parar lo detiene —
  // y ANTES de procesar los jobs, para que lo encolado en este
  // ciclo ya se ejecute en este mismo ciclo. Detrás del ajuste
  // auto_publish_top (default OFF): apagado, no hace nada. Con el
  // freno de presupuesto activo, no se crea ningún job masivo:
  // el ciclo solo termina lo que ya estaba encolado.
  const auto = guard.shouldWrite("bulk-job")
    ? await autoEnqueueTopClips(env, tenant).catch(() => ({ enabled: false, considered: 0, enqueued: 0, blocked: 0 }))
    : { enabled: false, considered: 0, enqueued: 0, blocked: 0 };
  // Escalado de topes (0017): el recálculo de madurez corre como
  // mucho UNA vez al día por cuenta (maturity_checked_day); el resto
  // de días es un no-op barato. Sin sesión: solo D1 + settings.
  // Es una escritura de settings (no esencial): con el freno
  // activo se salta; el gate usa el estado guardado.
  if (guard.shouldWrite("other")) {
    try {
      await refreshAccountMaturity(db, tenant, effectiveSettings(await getSettings(db, tenant)));
    } catch {
      // Que el recálculo falle no para la cola: el gate usa el estado
      // guardado y el error queda en el ciclo siguiente.
    }
  }
  // A4: si la detección de atascados muere por la cuota (7500),
  // se dispara el circuito y se sigue con la publicación: un
  // UPDATE de atascado no puede tumbar el ciclo (B1 publica por
  // el diario aunque D1 no escriba). Otro fallo sí sube.
  let stalled: JobRow[] = [];
  try {
    stalled = await detectStalledJobs(db, tenant, env);
  } catch (e) {
    if (isD1QuotaError(e)) {
      tripQuotaBreaker(Date.now());
      await alertQuotaExhausted(db, tenant, env).catch(() => {});
    } else {
      throw e;
    }
  }
  // F6 (spec 2026-11-24): barrido de drafts transitorios. En cada
  // ciclo (no en cada tick del cron), hasta 5 jobs en `draft` de
  // HOY cuyo `last_error` clasifique como transitorio se
  // re-evalúan con el re-chequeo de ejecución; si pasa, vuelven a
  // `scheduled` con `next_run_at=now` (escalonados 15 min por
  // clip, como en F1.3) y solo si no agotaron sus 3 reintentos
  // hoy. Si siguen frenando, no se tocan. Nunca toca motivos
  // duros (calidad, sin cuenta, streamer desactivado, spam) ni
  // jobs de días anteriores (esos los re-encola el auto-enqueue
  // mañana si procede). Cura los drafts creados antes del
  // despliegue de F2 y cualquier draft transitorio futuro.
  await sweepTransientDrafts(env, tenant, nowMs()).catch(() => undefined);
  const jobs = await dueJobs(db, tenant, 5);
  let processed = 0;
  // Paralelismo POR GRUPO (0022, NEEDS-VALIDATION §4): grupos
  // distintos (plataforma+idioma) corren en paralelo — las 4
  // plataformas suben a la vez — pero dentro de un grupo los jobs
  // van EN SERIE, para que el segundo vea el publicado/reserva
  // del primero y el gate no se burle. Los jobs sin plataforma
  // (vod_check/cut/...) se agrupan por kind.
  const groups = new Map<string, JobRow[]>();
  for (const job of jobs) {
    const key = job.platform ? `${job.platform}|${job.language}` : `kind:${job.kind}`;
    const list = groups.get(key) ?? [];
    list.push(job);
    groups.set(key, list);
  }
  await Promise.all([...groups.values()].map(async (groupJobs) => {
    for (const job of groupJobs) {
      // Reclamo atómico (M4): scheduled lo toma el ciclo; un job
      // 'processing' solo se reanuda si ya era del ciclo y venció.
      // Si el puente u otro ciclo ya lo tomó, se salta sin
      // ejecutarlo dos veces.
      const claimed = (await claimJobForCycle(db, job.id, tenant)) || (await resumeJobForCycle(db, job.id, tenant));
      if (!claimed) continue;
      // Re-leer tras reclamar: el dispatch/apply llevan el
      // run_token fresco (y el slot_day si ya tiene cupo).
      const fresh = await db.prepare("SELECT * FROM jobs WHERE id = ? AND tenant_id = ?").bind(job.id, tenant).first<JobRow>();
      const snap: JobRow = { ...(fresh ?? job), state: "processing" };
      try {
        const result: PublishResult = await dispatchPublishStepWithWal(env, snap);
        await applyPublishResult(env, db, snap, result);
        processed++;
      } catch (e) {
        // B1 — CUOTA D1 AGOTADA (7500): el diario ya tiene la
        // intención y, si hubo subida, el resultado; el volcado
        // reconcilia el job cuando D1 vuelve. No se marca failed
        // (eso también escribiría en D1 y fallaría) ni se suelta
        // nada que el volcado no pueda reponer: el job queda en
        // processing y detectStalledJobs lo re-encola; si ya se
        // subió, el volcado lo marca posted y no se duplica.
        // A4: además se dispara el circuito (para los bucles no
        // esenciales del resto del ciclo) y se intenta UNA alerta.
        if (isD1QuotaError(e)) {
          tripQuotaBreaker(Date.now());
          await alertQuotaExhausted(db, tenant, env).catch(() => {});
          continue;
        }
        // El dispatch lanzó: soltar el cupo antes de marcar el
        // fallo, para no dejarlo reservado todo el día.
        await releasePublishSlot(db, snap);
        await markFailed(db, snap, `error interno del Worker: ${e instanceof Error ? e.message : String(e)}`, true, env);
      }
    }
  }));
  // Retención 24 h del MP4 en R2 (0039, orden 2026-11-21 21:28):
  // (a) fija el plazo a los clips completos que aún no lo tienen
  // — cubre el volcado del diario WAL y las reconciliaciones
  // manuales, que marcan posted con UPDATE directo y no pasan por
  // markPosted —; (b) borra de R2 los MP4 con el plazo vencido y
  // sin re-publicación pendiente (un repub pendiente = takedown
  // esperando re-subida → se conserva). Mantenimiento no esencial:
  // con el freno de presupuesto activo (o el circuito 7500
  // abierto) se salta entero — el plazo solo se alarga, nunca se
  // borra antes de tiempo — y nunca tumba el ciclo.
  let retention: RetentionSweepResult | null = null;
  if (guard.shouldWrite("other") && !isQuotaBreakerOpen(Date.now())) {
    try {
      await backfillRetentionDeadlines(db, tenant);
      retention = await sweepExpiredRetentions(env, tenant);
    } catch {
      // La limpieza nunca tumba el ciclo.
    }
  }
  // Medición automática de analíticas (2026-11-19): al final del
  // ciclo, con throttle propio (1 vez cada 6 h por tenant, 1
  // medición cada 24 h por clip+plataforma). Nunca tumba el ciclo:
  // cualquier fallo queda en worker_events / analytics_status.
  // Es escritura no esencial (métricas): con el freno de
  // presupuesto activo se salta el ciclo entero. A4: con el
  // circuito 7500 abierto, también se salta (sus escrituras
  // morirían con 7500 y no aportan a publicar).
  if (guard.shouldWrite("analytics") && !isQuotaBreakerOpen(Date.now())) {
    try {
      await collectAnalytics(env, tenant);
    } catch {
      // collectAnalytics ya registra su propio fallo; aquí solo se
      // garantiza que no suba.
    }
  }
  // Engagement v2 (2026-11-23, v5.4.0): vía nativa YT/IG/FB +
  // Zernio solo TikTok, al final del ciclo, con throttle propio
  // (1 vez/hora, dentro del runner). Apagado por defecto
  // (engagement_enabled=false). Nunca tumba el ciclo: cualquier
  // fallo queda en worker_events / engagement_log. Escritura no
  // esencial: con el freno de presupuesto activo o el circuito
  // 7500 abierto se salta entero.
  if (guard.shouldWrite("analytics") && !isQuotaBreakerOpen(Date.now())) {
    try {
      await runEngagement(env, tenant);
    } catch {
      // runEngagement ya registra su propio fallo.
    }
  }
  return { ran: true, processed, stalled: stalled.length, autoEnqueued: auto.enqueued, gate, retention };
}

/** B2.5 (spec 2026-11-27): un job que lleva >24 h esperando deja
 *  UN evento `espera larga` por job y día. Best-effort: la lectura
 *  previa evita repetirlo ese día aunque cambie el motivo (el
 *  dedupe del log es por mensaje exacto, así que no bastaría); si
 *  la lectura o la escritura fallan, el ciclo sigue igual. */
async function logLongWaitOnce(db: D1Database, job: JobRow, reason: string, env: Env): Promise<void> {
  const dayStart = santiagoDayStartMs(Date.now());
  const prior = await db.prepare(
    "SELECT id FROM worker_events WHERE tenant_id = ? AND kind = 'auto_publish' AND message LIKE ? AND at >= ? LIMIT 1",
  )
    .bind(job.tenant_id, `espera larga: job #${job.id} %`, dayStart)
    .first<{ id: number }>()
    .catch(() => null);
  if (prior) return;
  await logWorkerEvent(db, "auto_publish", `espera larga: job #${job.id} lleva >24 h esperando por ${reason}`, job.tenant_id);
}

export async function applyPublishResult(env: Env, db: D1Database, job: JobRow, r: PublishResult): Promise<void> {
  switch (r.mode) {
    case "posted":
      // El cupo se suelta ANTES de marcar posted: el publicado ya
      // cuenta en el gate y la liberación exige el run_token
      // vigente, que markPosted no toca.
      await releasePublishSlot(db, job);
      await markPosted(db, job, r.platformId as string, r.evidenceUrl as string);
      break;
    case "processing":
      // Paso multi-step en vuelo: NO se suelta el cupo ni se
      // limpia el token — el siguiente dispatch reusa el mismo
      // cupo y la misma corrida. Valla de run_token: si otro
      // ciclo re-reclamó, este apply no pisa su resume_data.
      await fencedJobUpdate(
        db,
        job,
        "state = 'processing', resume_data = ?, next_run_at = ?, updated_at = ?",
        [r.resumeData ? JSON.stringify(r.resumeData) : job.resume_data, nowMs() + 10_000, nowMs()],
      );
      break;
    case "draft":
      await releasePublishSlot(db, job);
      await markDraft(db, job, r.reason ?? "sin motivo", env);
      break;
    case "retry": {
      // Freno TRANSITORIO en ejecución (F2, spec 2026-11-24): el
      // job no va a draft, se reprograma a `retryAtMs`. El cupo se
      // suelta ANTES de la transición (como el caso "draft") y se
      // limpian run_token/slot_day (como markDraft). Las ESPERAS
      // (B2.2, spec 2026-11-27) NO consumen intentos — el job solo
      // espera a que pase el tiempo (min90, spacing, cross15,
      // rolling, tomorrow) — así un piso no quema los 3 reintentos
      // en 15 min: `attempts` se escribe con `retriesToday(job,
      // todayKey)` (sin +1). Solo los FALLOS reales (`backoff`,
      // `null`) consumen: +1. La válvula a draft solo se abre para
      // fallos reales: si ya se agotaron los 3 reintentos de hoy
      // POR FALLOS, va a markDraft con el mensaje nuevo; una espera
      // con los 3 fallos ya gastados se reprograma igual (el tiempo
      // la resuelve, no un reintento). `attempts` arrastra el conteo
      // de ayer y solo `retriesToday` resetea por día.
      await releasePublishSlot(db, job);
      const todayKey = santiagoDayKey();
      const { retry: blockKind } = classifyGateBlock(r.reason ?? "");
      const isWait = blockKind !== null && blockKind !== "backoff";
      if (!isWait && retriesToday(job, todayKey) >= MAX_RETRIES_PER_DAY) {
        await markDraft(db, job, `gate de publicación: ${r.reason} (tope de reintentos por hoy agotado por fallos previos: ${MAX_RETRIES_PER_DAY}/día)`, env);
        break;
      }
      const changed = await fencedJobUpdate(
        db,
        job,
        "state = 'scheduled', next_run_at = ?, retryable = 1, last_error = ?, attempts = ?, updated_at = ?, run_token = ?, slot_day = ?",
        [r.retryAtMs, `gate de publicación: ${r.reason}`, isWait ? retriesToday(job, todayKey) : retriesToday(job, todayKey) + 1, nowMs(), null, null],
      );
      if (!changed) break;
      // B2.5: espera larga — el job lleva >24 h esperando (creado
      // hace más de un día y aún sin publicar): un evento, una vez
      // por job y día, para que el usuario lo vea en Tracking.
      if (isWait && job.created_at && nowMs() - job.created_at > 24 * 3600 * 1000) {
        await logLongWaitOnce(db, job, r.reason ?? "", env).catch(() => {});
      }
      // Sin notify: un reintento transitorio es rutinario (como
      // markFailed); el motivo queda en last_error y el job en
      // scheduled con su próximo intento.
      break;
    }
    case "awaiting_captcha": {
      await releasePublishSlot(db, job);
      const changed = await fencedJobUpdate(
        db,
        job,
        "state = 'awaiting_captcha', last_error = ?, updated_at = ?",
        [r.reason ?? "captcha", nowMs()],
      );
      if (!changed) break;
      await notify(db, { kind: "captcha", clipId: job.clip_id, message: `TikTok pide captcha (clip #${job.clip_id ?? "?"}): ${r.reason ?? ""} Resuélvelo en el navegador y el job se retoma solo.` }, job.tenant_id, env);
      break;
    }
    case "error":
      // A4 — 7500 (cuota D1 agotada): markFailed lo deja en failed
      // terminal sin reintento, dispara el circuito e intenta UNA
      // alerta; no se suelta el cupo antes (esa escritura también
      // moriría con 7500 y el volcado B1 lo reconcilia). El resto
      // del caso no cambia.
      if (isD1QuotaError(r.reason ?? "")) {
        await markFailed(db, job, r.reason ?? "error sin motivo", false, env).catch(() => {});
        break;
      }
      await releasePublishSlot(db, job);
      // SPAM/LÍMITE (regla 8): detener esa cuenta, pausarla 48 h
      // y reprogramar al día siguiente. NO se reintenta el mismo
      // día (retryable=false aunque el adaptador dijera otra
      // cosa): markFailed lo deja en failed terminal y la Cola
      // lo puede volver a encolar mañana, cuando la pausa ya no
      // frene el gate. Un error temporal (red, 429, 5xx) sigue el
      // backoff 1/5/15/60 con máx 3 reintentos/día (ver markFailed).
      if (isSpamLimitError(r.reason ?? "")) {
        await pauseAccountForSpam(db, job.tenant_id, job.api_account_id ?? null, r.reason ?? "spam/límite", env);
        await markFailed(db, job, `${r.reason ?? "error sin motivo"} (spam/límite: cuenta en pausa 48 h; se reprograma mañana, sin reintento hoy)`, false, env);
      } else {
        await markFailed(db, job, r.reason ?? "error sin motivo", r.retryable ?? false, env);
      }
      break;
  }
}
