// Frenos anti-recaída A4 (2026-11-20, encargo "que no vuelva a pasar").
//
// ÚNICA FUENTE de los umbrales de reintento del Worker: backoff,
// tope por día, error 7500 y circuito de cuota. Ningún otro archivo
// del Worker duplica estos literales (jobs.ts los re-exporta).
//
// El problema que arregla: el 19-11 el auto-enqueue reintentó los
// mismos clips ~400 veces en un día (~15.000 escrituras) y el
// Worker nuevo disparó el cerebro cada minuto (~60.000 escrituras)
// porque sus frenos vivían en ESCRITURAS a D1 que con la cuota
// agotada no persistían. Por eso el estado de estos frenos NUNCA
// depende solo de una escritura:
//
//   - Backoff y tope por job/día: se derivan de la fila del job
//     (attempts + updated_at, leídos) — ver retriesToday().
//   - Auto-enqueue: memoria del Worker + siembra por lectura de
//     `auto_enqueue_attempts` (migración 0045, ver jobs.ts:
//     `seedAutoEnqueueStateFromReads` lee el intento persistido,
//     no `clip_events`).
//   - Circuito 7500: memoria del Worker (tripQuotaBreaker) +
//     lectura de jobs.last_error / worker_events (hasRecentQuotaError).
//
// Qué pasa si el Worker reinicia (isolate frío): la memoria se
// pierde. El freno se reconstruye por lectura en el siguiente ciclo:
//   - jobs: attempts + updated_at siguen en D1 (las filas se leyeron,
//     no se escribieron), así que el tope por día sigue vigente.
//   - auto-enqueue: la siembra por lectura lee el intento
//     persistido en `auto_enqueue_attempts` (migración 0045): si
//     hay una fila de hoy, el estado se reconstruye con sus
//     intentos reales (no se asume 1 desde los eventos); si no la
//     hay, el clip parte de 0. Al publicar, la fila se BORRA
//     (`clearAutoEnqueueAttemptPersisted`), así que un éxito no
//     resucita intentos viejos.
//   - circuito 7500: la lectura de last_error/worker_events lo
//     re-dispara si hay un 7500 reciente (< 1 h); si no hay rastro
//     legible (el propio 7500 impidió registrarlo), el primer
//     intento de escritura tras el reinicio vuelve a dar 7500 y el
//     circuito se dispara en memoria con ese único intento fallido
//     (un intento fallido no consume cuota: la escritura rechazada
//     no cuenta como fila escrita).
//
// Días: hay DOS "días" distintos y es a propósito.
//   - Día de Santiago (America/Santiago): el tope de reintentos por
//     job y el día de la alerta. Es el mismo día que posted_day y la
//     idempotency_key del resto del panel.
//   - Día de cuota D1: la cuota se libera a las 00:00 UTC = 21:00 -03.
//     El circuito 7500 se abre hasta ese reset (nextD1ResetMs), no
//     hasta medianoche de Santiago: si se abriera hasta medianoche
//     de Santiago, el Worker seguiría parado 3 h después de liberarse
//     la cuota (21:00 -> 00:00).

/** Backoff entre reintentos, en minutos: 1.º reintento a 1 min,
 *  2.º a 5, 3.º a 15, 4.º a 60. Con el tope de 3 reintentos/día el
 *  4.º peldaño (60 min) nunca se programa el mismo día: queda
 *  reservado en la tabla para que, si el tope se sube algún día,
 *  el backoff ya lo contemple sin tocar más código. */
export const RETRY_BACKOFF_MINUTES = [1, 5, 15, 60] as const;

/** Lo mismo en milisegundos (lo que usan next_run_at y los tests). */
export const RETRY_BACKOFF_MS: readonly number[] = RETRY_BACKOFF_MINUTES.map((m) => m * 60 * 1000);

/** Tope de reintentos por job y día (Santiago). Agotado, el job
 *  queda parado hasta el día siguiente: se marca failed terminal y
 *  la Cola lo puede volver a encolar mañana. */
export const MAX_RETRIES_PER_DAY = 3;

/** Alias histórico (jobs.ts lo usaba como MAX_TRANSIENT_RETRIES). */
export const MAX_TRANSIENT_RETRIES = MAX_RETRIES_PER_DAY;

/** Código de error de Cloudflare D1 cuando la cuota diaria de
 *  escrituras (100.000/cuenta) está agotada. */
export const D1_QUOTA_ERROR_CODE = 7500;

/** Ventana para la detección del 7500 por lectura: un 7500 visto
 *  en jobs.last_error o worker_events hace menos de 1 h re-dispara
 *  el circuito tras un reinicio del Worker. */
export const QUOTA_ERROR_READ_WINDOW_MS = 60 * 60 * 1000;

/** Entre dos intentos de alerta 7500 fallidos: 1 h. Un intento de
 *  alerta que no llegó a ningún canal (ni panel ni Telegram) puede
 *  reintentarse pasado ese tiempo; uno que sí llegó no se repite
 *  en el día. */
export const QUOTA_ALERT_RETRY_MIN_MS = 60 * 60 * 1000;

const SANTIAGO_TZ = "America/Santiago";

// ---------------------------------------------------------------------------
// Día de Santiago (mismo día que posted_day / idempotency_key)
// ---------------------------------------------------------------------------

/** Día YYYY-MM-DD en America/Santiago del instante dado. */
export function santiagoDayKeyOf(ms: number): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: SANTIAGO_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(ms));
}

/** Inicio del día de Santiago (ms epoch) que contiene `nowMs`.
 *  Se calcula restando la hora local desde medianoche; en los dos
 *  días de cambio de hora del año el día dura 23/25 h y el resultado
 *  se desvía hasta 1 h (solo afecta a ventanas de freno, y en el
 *  sentido conservador: el freno dura algo más, nunca menos). */
export function santiagoDayStartMs(nowMs: number): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: SANTIAGO_TZ,
    hourCycle: "h23",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(new Date(nowMs));
  const get = (t: string): number => Number(parts.find((p) => p.type === t)?.value ?? 0);
  const sinceMidnightMs = ((get("hour") * 60 + get("minute")) * 60 + get("second")) * 1000 + (nowMs % 1000);
  return nowMs - sinceMidnightMs;
}

/** Próxima medianoche de Santiago (ms epoch) tras `nowMs`. */
export function nextSantiagoMidnightMs(nowMs: number): number {
  return santiagoDayStartMs(nowMs) + 24 * 3600 * 1000;
}

/** Próxima liberación de la cuota D1: 00:00 UTC tras `nowMs`
 *  (= 21:00 -03 del día anterior en Santiago). */
export function nextD1ResetMs(nowMs: number): number {
  const d = new Date(nowMs);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + 1, 0, 0, 0, 0);
}

// ---------------------------------------------------------------------------
// Backoff y tope por día (funciones puras, testeables sin D1)
// ---------------------------------------------------------------------------

/** ¿Es un error de cuota D1 agotada? Acepta Error, string o
 *  cualquier valor: basta con que mencione el 7500, o que sea un
 *  error D1 de cuota/límite. Falla cerrado a false. Nota: el texto
 *  exacto del error D1 no se pudo verificar sin escribir en D1
 *  (cuota agotada al implementar esto): se casa por el código 7500
 *  y por las palabras límite/cuota como respaldo. */
export function isD1QuotaError(err: unknown): boolean {
  const text =
    err instanceof Error
      ? `${err.message} ${((err as unknown as Record<string, unknown>).code ?? "") as string}`
      : String(err ?? "");
  if (/\b7500\b/.test(text)) return true;
  return /d1/i.test(text) && /quota|cuota|l[ií]mite|limit|exceeded/i.test(text);
}

/** Retraso del reintento N (1 = primer reintento), en ms; null si
 *  N está fuera de la tabla. 1 -> 1 min, 2 -> 5, 3 -> 15, 4 -> 60. */
export function retryDelayMs(retryNumber: number): number | null {
  if (!Number.isInteger(retryNumber) || retryNumber < 1) return null;
  const minutes = RETRY_BACKOFF_MINUTES[retryNumber - 1];
  return minutes === undefined ? null : minutes * 60 * 1000;
}

/** ¿Cabe otro reintento hoy? `retriesToday` = reintentos ya usados
 *  hoy por ese job. */
export function canRetryToday(retriesToday: number): boolean {
  return retriesToday < MAX_RETRIES_PER_DAY;
}

/** Reintentos ya usados hoy por un job, derivados SOLO de su fila
 *  (lectura): si su última actualización es de hoy, attempts son
 *  de hoy; si es de otro día, hoy lleva 0. Es conservador: si el
 *  job se reclamó hoy (updated_at de hoy) con intentos de ayer
 *  aún en el contador, esos cuentan como de hoy — el freno para
 *  antes, nunca después. */
export function retriesToday(job: { attempts: number; updated_at: number }, todayKey: string): number {
  return santiagoDayKeyOf(job.updated_at) === todayKey ? job.attempts : 0;
}

/** Retraso antes del siguiente reintento cuando ya se usaron
 *  `retriesUsedToday` hoy: 0 usados -> 1 min, 1 -> 5, 2 -> 15.
 *  Con 3 usados el tope ya frena (canRetryToday false) y esto
 *  devuelve el peldaño reservado de 60 min. */
export function nextRetryDelayMs(retriesUsedToday: number): number | null {
  return retryDelayMs(retriesUsedToday + 1);
}

// ---------------------------------------------------------------------------
// Circuito 7500 (memoria del Worker + lectura de D1)
// ---------------------------------------------------------------------------

let quotaTrippedAtMs: number | null = null;
let quotaAlertSentDayKey: string | null = null;
let quotaAlertLastAttemptMs: number | null = null;

/** Dispara el circuito: desde ahora, los bucles no esenciales no
 *  se intentan hasta el reset de cuota. Solo memoria (no escribe). */
export function tripQuotaBreaker(nowMs: number): void {
  quotaTrippedAtMs = nowMs;
}

/** ¿Está abierto el circuito? Se cierra solo al llegar el reset
 *  de cuota (00:00 UTC); al cerrarse se olvida el disparo. */
export function isQuotaBreakerOpen(nowMs: number): boolean {
  if (quotaTrippedAtMs === null) return false;
  if (nowMs >= nextD1ResetMs(quotaTrippedAtMs)) {
    quotaTrippedAtMs = null;
    return false;
  }
  return true;
}

/** ¿Toca intentar la alerta única del 7500? No si ya se envió una
 *  hoy; tampoco si el último intento (fallido) fue hace menos de
 *  1 h. `todayKey` es el día de Santiago. */
export function quotaAlertDue(nowMs: number, todayKey: string): boolean {
  if (quotaAlertSentDayKey === todayKey) return false;
  if (quotaAlertLastAttemptMs !== null && nowMs - quotaAlertLastAttemptMs < QUOTA_ALERT_RETRY_MIN_MS) return false;
  return true;
}

/** Registra un intento de alerta: `sent` true solo si llegó a
 *  algún canal (panel o Telegram). */
export function noteQuotaAlertAttempt(nowMs: number, sent: boolean, todayKey: string): void {
  quotaAlertLastAttemptMs = nowMs;
  if (sent) quotaAlertSentDayKey = todayKey;
}

/** ¿Hay un error 7500 reciente legible en D1? Lee (nunca escribe):
 *  el último last_error de jobs con 7500 o el último evento del
 *  Worker que lo mencione, dentro de la ventana. Falla cerrado a
 *  false: un fallo de lectura nunca frena el ciclo. */
export async function hasRecentQuotaError(
  db: import("./cloudflare").D1Database,
  tenant: string,
  withinMs: number,
  nowMs: number,
): Promise<boolean> {
  try {
    const job = await db
      .prepare("SELECT updated_at FROM jobs WHERE tenant_id = ? AND last_error LIKE '%7500%' ORDER BY updated_at DESC LIMIT 1")
      .bind(tenant)
      .first<{ updated_at: number }>();
    if (job && nowMs - job.updated_at < withinMs) return true;
    const ev = await db
      .prepare("SELECT at FROM worker_events WHERE tenant_id = ? AND message LIKE '%7500%' ORDER BY at DESC LIMIT 1")
      .bind(tenant)
      .first<{ at: number }>();
    return !!ev && nowMs - ev.at < withinMs;
  } catch {
    return false;
  }
}

/** ¿Está parado todo lo no esencial? Memoria primero; si no hay
 *  disparo en memoria, la lectura de un 7500 reciente lo dispara
 *  (reconstrucción tras reinicio del Worker). */
export async function checkQuotaBreaker(
  db: import("./cloudflare").D1Database,
  tenant: string,
  nowMs: number,
): Promise<boolean> {
  if (isQuotaBreakerOpen(nowMs)) return true;
  if (await hasRecentQuotaError(db, tenant, QUOTA_ERROR_READ_WINDOW_MS, nowMs)) {
    tripQuotaBreaker(nowMs);
    return true;
  }
  return false;
}

/** Solo para tests: vacía el estado en memoria del circuito. */
export function resetQuotaBreakerState(): void {
  quotaTrippedAtMs = null;
  quotaAlertSentDayKey = null;
  quotaAlertLastAttemptMs = null;
}

// ---------------------------------------------------------------------------
// Freno de dispatch fallido (panel-live / panel-watchdog)
// ---------------------------------------------------------------------------

const dispatchFailures = new Map<string, { dayKey: string; count: number; lastMs: number }>();

/** Registra un dispatch fallido (GitHub no aceptó / red) para ese
 *  tipo de evento. Solo memoria; el día es el de Santiago. */
export function noteDispatchFailure(eventType: string, nowMs: number): void {
  const today = santiagoDayKeyOf(nowMs);
  const prev = dispatchFailures.get(eventType);
  if (prev && prev.dayKey === today) {
    dispatchFailures.set(eventType, { dayKey: today, count: prev.count + 1, lastMs: nowMs });
  } else {
    dispatchFailures.set(eventType, { dayKey: today, count: 1, lastMs: nowMs });
  }
}

/** ¿Se puede volver a intentar un dispatch de este tipo ahora?
 *  Mismo backoff y tope que los jobs: tras 1 fallo espera 1 min,
 *  tras 2 espera 5, tras 3 no se reintenta hoy. Sin fallos hoy,
 *  siempre se puede. (El throttle de token de 15 min del dispatch
 *  ya es más estricto en el camino normal; este freno cubre el
 *  caso en que el fallo no emitió token y el throttle no engancha.) */
export function dispatchRetryAllowed(eventType: string, nowMs: number): boolean {
  const st = dispatchFailures.get(eventType);
  if (!st) return true;
  if (st.dayKey !== santiagoDayKeyOf(nowMs)) return true;
  if (st.count >= MAX_RETRIES_PER_DAY) return false;
  const delay = retryDelayMs(st.count) ?? 0;
  return nowMs - st.lastMs >= delay;
}

/** Solo para tests: vacía el estado en memoria de fallos de dispatch. */
export function resetDispatchFailureState(): void {
  dispatchFailures.clear();
}

// ---------------------------------------------------------------------------
// Freno del auto-enqueue (decisión pura; el estado vive en jobs.ts)
// ---------------------------------------------------------------------------

export interface AutoEnqueueAttemptState {
  /** Intentos fallidos de hoy para este clip (0 jobs encolados). */
  attemptsToday: number;
  /** Último intento (ms epoch) o null si nunca se intentó. */
  lastAttemptMs: number | null;
}

/** ¿Se puede intentar el auto-enqueue de este clip ahora?
 *  Tope 3 intentos fallidos/día; entre intentos, el backoff de la
 *  tabla (tras 1 fallo 1 min, tras 2 fallos 5 min). Un intento que
 *  encola jobs no cuenta como fallo: el contador se limpia. */
export function autoEnqueueAllowed(
  state: AutoEnqueueAttemptState,
  nowMs: number,
): { allowed: boolean; reason: string | null } {
  if (state.attemptsToday >= MAX_RETRIES_PER_DAY) {
    return { allowed: false, reason: `tope de ${MAX_RETRIES_PER_DAY} intentos fallidos hoy` };
  }
  if (state.lastAttemptMs !== null && state.attemptsToday > 0) {
    const delay = retryDelayMs(state.attemptsToday) ?? 0;
    const waited = nowMs - state.lastAttemptMs;
    if (waited < delay) {
      return { allowed: false, reason: `backoff: faltan ${Math.ceil((delay - waited) / 60000)} min` };
    }
  }
  return { allowed: true, reason: null };
}
