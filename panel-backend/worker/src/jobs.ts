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
  effectiveSettings,
  getApiAccountByPlatform,
  getClip,
  getSettings,
  getTelegramLinkByTenant,
  isRestDayEffective,
  listClipStats,
  listQueueClips,
  boostBonusByClip,
  EVENT_DEDUPE_WINDOW_MS,
  lastIdenticalNotificationAt,
  logClipEvent,
  logWorkerEvent,
  maturityOut,
  nextPlatformHourMs,
  parsePlatforms,
  queueClipCompare,
  recordEventCounter,
  refreshAccountMaturity,
  santiagoDayKeyMs,
  saveSettings,
  slotWindowBoundsMs,
  updateClipPublishState,
  updateClipPublishStates,
  withAccountPause,
  type ClipRow,
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
import { runZernioEngagement } from "./publish/zernio-engagement";
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
import { releasePublishSlot, santiagoDayKey, publishGate, rateLimitGate, seoForPlatform } from "./gate";
export { publishGate, rateLimitGate, santiagoDayKey, seoForPlatform, type GateContext } from "./gate";
// Puerta de calidad B4 (2026-11-20): los umbrales y el veredicto
// viven en quality-gate.ts (única fuente); aquí solo se aplica.
import {
  QUALITY_ALERT_MIN_SAMPLE,
  QUALITY_DISCARD_ALERT_PCT,
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
  // espaciamiento y 1-por-franja TAMBIÉN a manuales); 0 si es
  // automático.
  manual = false,
  // next_run_at del job nuevo (reglas 2026-11-19 + orden 09:25):
  // en automático es el instante de encolado, que ya está dentro
  // de una franja (el gate con checkSlot lo comprobó, también en
  // manual desde la orden 09:25); en manual es null (corre en
  // cuanto el ciclo lo toma, dentro de la franja); en manual
  // PROGRAMADO es el instante pedido (el ciclo lo toma al llegar
  // la hora).
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
  // que dos enqueues del mismo clip para el mismo día/franja
  // colisionen como deben (dedup) y una republicación use el
  // sufijo |repubN del día correcto.
  dayKey?: string,
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
        await db
          .prepare(
            "UPDATE jobs SET state = 'scheduled', api_account_id = ?, title = ?, caption = ?, r2_key = ?, attempts = 0, retryable = 1, last_error = NULL, next_run_at = ?, updated_at = ?, run_token = ?, slot_day = ?, manual = ? WHERE id = ? AND tenant_id = ?",
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
     *  ese instante y el gate se evalúa EN ESE INSTANTE (franja,
     *  tope diario, calentamiento, espaciamiento y 1-por-franja
     *  para ese día/franja, lo mejor que se pueda calcular al
     *  agendar; el re-chequeo del ciclo vuelve a aplicar todo al
     *  ejecutar). Si no viene, publicación inmediata (ahora). */
    scheduledForMs?: number | null;
    /** Republicación explícita (P0 #5, auditoría #11): publicar
     *  DE NUEVO el mismo clip en la misma plataforma el mismo
     *  día (la plataforma borró el vídeo). Solo salta la puerta
     *  "mismo clip hoy"; el resto sigue frenando igual y el job
     *  nuevo lleva la key con sufijo |repubN. */
    republish?: boolean;
    /** Publicar AHORA fuera de franja (orden del usuario
     *  2026-11-22 01:07, "sube vídeos ahora"): salta SOLO la
     *  puerta de franja horaria. Todo lo demás sigue frenando
     *  igual (tope diario, calentamiento, espaciamiento, rolling
     *  24 h, 1 por plataforma y franja, veredicto de calidad,
     *  SEO): es una decisión humana explícita de adelantar la
     *  hora, no de saltarse las reglas anti-baneo. Solo lo pone
     *  el endpoint /publish con force_now=true; el ciclo y el
     *  auto-enqueue nunca lo usan. */
    forceNow?: boolean;
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
  if (clip.status === "publicado") {
    const reason = "clip ya publicado";
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
  // el gate se evalúa EN EL INSTANTE PROGRAMADO (franja, tope,
  // espaciamiento, 1-por-franja de ESE día/franja); si no, ahora.
  const now = nowMs();
  const scheduledForMs = opts.scheduledForMs ?? null;
  const refMs = scheduledForMs !== null && scheduledForMs > now ? scheduledForMs : now;
  const refDayKey = santiagoDayKeyMs(refMs);
  // PLATAFORMAS DESTINO (regla 14:10, 2026-11-21): por defecto la
  // UNIÓN de las del clip y las de sus cuentas vinculadas en el
  // idioma del clip (una cuenta vinculada que el clip no listaba
  // también recibe el vídeo); un override explícito limita a esa
  // lista. Una plataforma sin cuenta vinculada queda frenada con
  // motivo por el gate (nunca se publica en el canal de otro
  // idioma ni se auto-crea cuenta).
  const linked = await linkedPlatforms(db, tenant, clipLang);
  const targetPlatforms = opts.platforms
    ? opts.platforms.filter((p): p is PanelPlatform => (PANEL_PLATFORMS as readonly string[]).includes(p))
    : [...new Set([...parsePlatforms(clip.platforms), ...linked])];
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
      const m = maturityOut(acc, eff);
      maturityByPlatform[p] = { limit: m.effective_daily_limit, plan: m.effective_daily_plan, stage: m.stage };
    }
  }
  // Gates por plataforma: la que frena queda pendiente con motivo.
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
    // Topes POR plataforma+idioma (0019): solo cuentan las
    // publicaciones de ESE DÍA en este idioma (jobs.language).
    // Al programar a futuro, el día es el del instante
    // programado, no hoy.
    const publishedToday = await db
      .prepare(
        "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' AND posted_day = ?",
      )
      .bind(tenant, platform, clipLang, refDayKey)
      .first<{ n: number }>();
    // Espaciamiento POR plataforma+idioma (0019): la última
    // subida en este idioma; sin publicaciones, sin freno.
    const lastPosted = await db
      .prepare(
        "SELECT updated_at FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' ORDER BY updated_at DESC LIMIT 1",
      )
      .bind(tenant, platform, clipLang)
      .first<{ updated_at: number }>();
    // Contadores nuevos (reglas 2026-11-19): rolling 24 h, mismo
    // clip hoy en esta plataforma, mismo clip en otra plataforma.
    // Al programar a futuro, el rolling se mide en las 24 h
    // anteriores al instante programado.
    const last24h = await db
      .prepare(
        "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' AND updated_at > ?",
      )
      .bind(tenant, platform, clipLang, refMs - 24 * 3600 * 1000)
      .first<{ n: number }>();
    const clipToday = await db
      .prepare(
        "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND clip_id = ? AND platform = ? AND state = 'posted' AND posted_day = ?",
      )
      .bind(tenant, clip.id, platform, refDayKey)
      .first<{ n: number }>();
    const clipOther = await db
      .prepare(
        "SELECT updated_at FROM jobs WHERE tenant_id = ? AND clip_id = ? AND platform <> ? AND state = 'posted' ORDER BY updated_at DESC LIMIT 1",
      )
      .bind(tenant, clip.id, platform)
      .first<{ updated_at: number }>();
    const accRow = accountRowByPlatform[platform];
    const mat = maturityByPlatform[platform];
    // 1 POR PLATAFORMA Y FRANJA (orden 09:25, SIN AMONTONAR):
    // publicadas en esta plataforma+idioma dentro de la franja
    // de referencia (la actual, o la del instante programado).
    // Al programar a futuro, también cuentan los jobs YA
    // AGENDADOS con next_run_at dentro de esa franja: si otro
    // clip ya reservó la franja, este no la puede tomar (el
    // re-chequeo del ciclo lo volvería a frenar al ejecutar, pero
    // al agendar ya se ve).
    const slotBounds = slotWindowBoundsMs(refMs);
    let publishedThisSlot = 0;
    if (slotBounds) {
      // `>` en vez de `>=` (ver recheckPublishGate en gate.ts):
      // los mocks de D1 de los tests no soportan `>=`; con
      // timestamps enteros en ms, `> startMs - 1` es `>= startMs`.
      const slotRow = await db
        .prepare(
          "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' AND updated_at > ? AND updated_at < ?",
        )
        .bind(tenant, platform, clipLang, slotBounds.startMs - 1, slotBounds.endMs)
        .first<{ n: number }>();
      publishedThisSlot = slotRow?.n ?? 0;
      if (scheduledForMs !== null && scheduledForMs > now) {
        const scheduledRow = await db
          .prepare(
            "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'scheduled' AND next_run_at > ? AND next_run_at < ?",
          )
          .bind(tenant, platform, clipLang, slotBounds.startMs - 1, slotBounds.endMs)
          .first<{ n: number }>();
        publishedThisSlot += scheduledRow?.n ?? 0;
      }
    }
    const gateCtx = {
      clip,
      seoScore: seo.score,
      seoOverride: seo.override,
      publishedToday: publishedToday?.n ?? 0,
      lastPublishedAtMs: lastPosted?.updated_at ?? null,
      streamerAllowed: true, // el permiso se verifica en el puente con streamers.yaml
      settings,
      accountDailyLimit: maturityByPlatform[platform]?.limit ?? null,
      accountDailyPlan: maturityByPlatform[platform]?.plan ?? null,
      // Calentamiento (reglas 2026-11-19): si la cuenta no es
      // establecida, su tope de etapa frena TAMBIÉN a mano.
      warmupDailyLimit: mat && mat.stage !== "establecida" ? mat.plan : null,
      publishedLast24h: last24h?.n ?? 0,
      clipPublishedTodayOnPlatform: (clipToday?.n ?? 0) > 0,
      republish: opts.republish === true,
      clipLastPublishedOtherPlatformMs: clipOther?.updated_at ?? null,
      publishedThisSlot,
      // Pausa por spam (48 h, settings account_pauses) y día
      // libre semanal (determinista por cuenta+semana). Al
      // programar a futuro, el día libre es el del instante
      // programado.
      pausedUntilMs: accRow ? accountPausedUntilMs(settings, accRow.id) : null,
      isRestDay: accRow ? isRestDayEffective(`acc-${accRow.id}`, refMs, settings, accRow.id) : false,
      // Franja (reglas 2026-11-19 + orden 09:25 SIN AMONTONAR):
      // la comprueba TAMBIÉN la manual. "Publicar ahora" fuera
      // de franja queda frenado, no encola. Al programar a
      // futuro, la franja que se comprueba es la del instante
      // programado (nowMs = refMs): no se está publicando ahora,
      // solo agendando. EXCEPCIÓN (orden del usuario 2026-11-22
      // 01:07, forceNow): publicar AHORA fuera de franja salta
      // SOLO esta puerta; el resto sigue frenando igual.
      checkSlot: !opts.forceNow,
      nowMs: refMs,
      accountLanguage: accountLanguageByPlatform[platform],
      accountReady: accountReadyByPlatform[platform],
      // Manual (2026-11-19 + orden 09:25 SIN AMONTONAR): ya NO
      // salta tope, espaciamiento ni franja; respeta todas las
      // reglas anti-baneo igual que la automática.
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
    // next_run_at (reglas 2026-11-19 + programación 2026-11-21 +
    // HORARIO POR PLATAFORMA 2026-11-22 01:11): el job automático
    // nace con next_run_at = la próxima hora de SU plataforma
    // (YouTube/IG 12:00, 18:00, 21:00; TikTok/FB 13:00, 19:00,
    // 22:00, hora Chile), no "ahora": así cada plataforma sube
    // a su hora aunque el ciclo corra cada 5 min. El gate con
    // checkSlot ya comprobó la franja (las horas están dentro
    // de franja). El manual inmediato, sin fecha (corre en
    // cuanto el ciclo lo toma); el manual PROGRAMADO, con
    // next_run_at = el instante pedido (el ciclo lo toma cuando
    // llegue la hora, y el re-chequeo vuelve a aplicar todo al
    // ejecutar).
    // Solo se encola ESTA plataforma (la que pasó el gate):
    // enqueuePublishJobs ya no re-encola todas las del clip.
    const nextRunAt = scheduledForMs !== null && scheduledForMs > now
      ? scheduledForMs
      : manual ? null : nextPlatformHourMs(platform, now);
    jobs.push(...(await enqueuePublishJobs(db, clip, accountIdByPlatform, title, caption, r2Key?.r2_key ?? null, manual, nextRunAt, [platform], opts.republish === true, refDayKey)));
  }
  // Cada plataforma que pasó el gate encoló solo su job (ya no hay
  // duplicados por re-encolar todas en cada llamada); el dedup
  // queda por seguridad.
  const seen = new Set<number>();
  const uniqueJobs = jobs.filter((j) => (seen.has(j.id) ? false : (seen.add(j.id), true)));
  return { jobs: uniqueJobs, blocked, warnings };
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

const AUTO_ENQUEUE_MAX_CLIPS_PER_CYCLE = 3;

/** Máx. clips evaluados por ciclo (10): el freno A4 deja SALTAR
 *  clips frenados sin consumir el tope de 3 intentos, así que el
 *  ciclo puede mirar más allá del top-3 atascado; el tope de 10
 *  acota las lecturas por ciclo (la Cola puede tener 150+ clips y
 *  cada evaluación es 1-2 lecturas). */
const AUTO_ENQUEUE_MAX_EVALUATED_PER_CYCLE = 10;

// ---------------------------------------------------------------------------
// Freno del auto-enqueue por clip (A4, 2026-11-20).
// Estado en MEMORIA del Worker (nunca una escritura D1: el freno
// debe sobrevivir a la cuota agotada) + siembra por LECTURA tras
// un reinicio. Un "intento fallido" es un ciclo en el que el clip
// no encoló ningún job (todas sus plataformas frenaron). Tras 3
// intentos fallidos en el día, el clip no se vuelve a intentar
// hasta mañana; entre intentos, el backoff 1/5/15 min (ver
// autoEnqueueAllowed en retry.ts). Un intento que encola jobs
// limpia el contador: el clip progresó.
// Siembra: tras un reinicio (memoria vacía), el último intento de
// hoy se lee de clip_events (MAX(at), paso 'publicado', actor
// 'worker') y se asume 1 intento ya hecho — aproximación
// documentada: tras un reinicio el clip puede sumar como mucho 2
// intentos más ese día (hasta el tope de 3), cada uno con su
// backoff, y la dedupe de eventos de 6 h acota sus escrituras.
// ---------------------------------------------------------------------------
const autoEnqueueState = new Map<number, { dayKey: string; attemptsToday: number; lastAttemptMs: number | null }>();

export function getAutoEnqueueState(clipId: number, todayKey: string): AutoEnqueueAttemptState | null {
  const st = autoEnqueueState.get(clipId);
  if (!st || st.dayKey !== todayKey) return null;
  return { attemptsToday: st.attemptsToday, lastAttemptMs: st.lastAttemptMs };
}

export function recordAutoEnqueueAttempt(clipId: number, now: number, todayKey: string): void {
  const prev = autoEnqueueState.get(clipId);
  const attemptsToday = prev && prev.dayKey === todayKey ? prev.attemptsToday + 1 : 1;
  autoEnqueueState.set(clipId, { dayKey: todayKey, attemptsToday, lastAttemptMs: now });
}

export function clearAutoEnqueueState(clipId: number): void {
  autoEnqueueState.delete(clipId);
}

/** Solo para tests: vacía el estado en memoria del auto-enqueue. */
export function resetAutoEnqueueStateForTests(): void {
  autoEnqueueState.clear();
}

/** Siembra el estado de un clip desde clip_events (lectura). Sin
 *  estado en memoria para hoy y con un intento hoy en D1, deja
 *  { attemptsToday: 1, lastAttemptMs: último intento }. Sin intento
 *  hoy, no deja nada (el clip se intenta sin freno). Nunca levanta:
 *  si la lectura falla, el freno en memoria empieza de cero. */
export async function seedAutoEnqueueStateFromReads(
  db: D1Database,
  tenant: Tenant,
  clipId: number,
  todayKey: string,
  now: number,
): Promise<void> {
  const prev = autoEnqueueState.get(clipId);
  if (prev && prev.dayKey === todayKey) return;
  try {
    const row = await db
      .prepare(
        "SELECT MAX(at) AS last_at FROM clip_events WHERE tenant_id = ? AND clip_id = ? AND step = 'publicado' AND actor = 'worker' AND at >= ?",
      )
      .bind(tenant, clipId, santiagoDayStartMs(now))
      .first<{ last_at: number | null }>();
    if (row?.last_at) {
      autoEnqueueState.set(clipId, { dayKey: todayKey, attemptsToday: 1, lastAttemptMs: row.last_at });
    }
  } catch {
    // Sin siembra: el freno en memoria empieza de cero.
  }
}

export async function autoEnqueueTopClips(env: Env, tenant: Tenant): Promise<AutoEnqueueResult> {
  const none: AutoEnqueueResult = { enabled: false, considered: 0, enqueued: 0, blocked: 0 };
  try {
    const settings = await getSettings(env.DB, tenant);
    if (!effectiveSettings(settings).auto_publish_top) return none;
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
    for (const clip of queue) {
      if (considered >= AUTO_ENQUEUE_MAX_CLIPS_PER_CYCLE || evaluated >= AUTO_ENQUEUE_MAX_EVALUATED_PER_CYCLE) break;
      evaluated++;
      // Job publish vigente: scheduled/processing, o posted hoy.
      // (Un posted de otro día no bloquea: el clip puede salir por
      // las plataformas que le falten, con el gate de cada una.)
      const vigente = await env.DB.prepare(
        "SELECT id, state, posted_day FROM jobs WHERE tenant_id = ? AND kind = 'publish' AND clip_id = ?",
      )
        .bind(tenant, clip.id)
        .all<{ id: number; state: string; posted_day: string | null }>()
        .catch(() => ({ results: [] as Array<{ id: number; state: string; posted_day: string | null }> }));
      const hasVigente = vigente.results.some(
        (j) => j.state === "scheduled" || j.state === "processing" || (j.state === "posted" && j.posted_day === today),
      );
      if (hasVigente) continue;
      // Freno A4 por clip (memoria + siembra por lectura): un clip
      // que ya falló hoy no se reintenta cada minuto. Sin escribir
      // nada: el salto no deja evento (el motivo ya quedó en
      // Tracking en el intento que frenó).
      await seedAutoEnqueueStateFromReads(env.DB, tenant, clip.id, today, Date.now());
      const verdict = autoEnqueueAllowed(
        getAutoEnqueueState(clip.id, today) ?? { attemptsToday: 0, lastAttemptMs: null },
        Date.now(),
      );
      if (!verdict.allowed) continue;
      considered++;
      const r = await tryEnqueueClipPublish(env, tenant, clip, { actor: "worker", logToWorkerEvents: true });
      if (r.jobs.length > 0) {
        clearAutoEnqueueState(clip.id);
        enqueued += r.jobs.length;
        await logWorkerEvent(
          env.DB,
          "auto_publish",
          `Auto-enqueue: clip #${clip.id} encolado en ${r.jobs.map((j) => j.platform).join(", ")} (top de la Cola con boost).`,
          tenant,
        ).catch(() => {});
      } else {
        // 0 jobs = todas las plataformas frenaron: intento fallido.
        recordAutoEnqueueAttempt(clip.id, Date.now(), today);
      }
      blocked += r.blocked.length;
      // Puerta de calidad B4: los bloqueos por calidad cuentan
      // aparte; si hubo al menos uno, se revisa la tasa del día
      // (solo lecturas + aviso si supera el 30%, nunca tumba el
      // ciclo: maybeAlertQualityDiscards nunca levanta).
      qualityBlocked += r.blocked.filter((b) => b.includes(": calidad:")).length;
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

// ---------------------------------------------------------------------------
// Transiciones (posted exige evidence_url: sin URL no hay posted).
// ---------------------------------------------------------------------------
export async function markPosted(db: D1Database, job: JobRow, platformId: string, evidenceUrl: string): Promise<void> {
  if (!evidenceUrl.startsWith("https://")) throw new Error("posted exige evidence_url https");
  // Valla de run_token (0022, §3): si el snapshot trae token y la
  // fila ya cambió de manos, el UPDATE cambia 0 filas y NO se
  // ejecuta ningún efecto secundario (ni published_count, ni clip,
  // ni evento). Sin token (puente, legacy) se mantiene lo antiguo.
  const changed = await fencedJobUpdate(
    db,
    job,
    "state = 'posted', platform_id = ?, evidence_url = ?, posted_day = ?, updated_at = ?",
    [platformId, evidenceUrl, santiagoDayKey(), nowMs()],
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
  // Engagement SOLO por Zernio (2026-11-23, v5.3.0): al final del
  // ciclo, con throttle propio (1 vez/hora, dentro del runner).
  // Apagado por defecto (engagement_enabled=false). Nunca tumba el
  // ciclo: cualquier fallo queda en worker_events / engagement_log.
  // Escritura no esencial: con el freno de presupuesto activo o el
  // circuito 7500 abierto se salta entero.
  if (guard.shouldWrite("analytics") && !isQuotaBreakerOpen(Date.now())) {
    try {
      await runZernioEngagement(env, tenant);
    } catch {
      // runZernioEngagement ya registra su propio fallo.
    }
  }
  return { ran: true, processed, stalled: stalled.length, autoEnqueued: auto.enqueued, gate, retention };
}

async function applyPublishResult(env: Env, db: D1Database, job: JobRow, r: PublishResult): Promise<void> {
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
