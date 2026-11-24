// Puerta del ciclo "cuando toca" (A3, 2026-11-20, anti-recaída D1).
//
// El cron del Worker sigue cada minuto (es el despertador), pero el
// ciclo del publicador ya no trabaja cada minuto: al inicio se LEE
// qué hay pendiente y si no toca nada se sale temprano SIN escribir
// nada — ni siquiera un evento "nada que hacer". Esa era la fuga:
// el ciclo reevaluaba cada minuto los mismos clips frenados y cada
// freno escribía 1 evento en clip_events + 1 en worker_events
// (~22.000 escrituras/día, 98% repetidas; ver
// runs/d1-writes-2026-11-20/por-que-se-gasto-d1.md).
//
// Las lecturas que deciden (todas lecturas; leer no cuenta en la
// cuota D1):
//   (a) la próxima publicación programada: el next_run_at mínimo
//       entre los jobs scheduled/processing del tenant (un
//       next_run_at NULL vence ya, como en dueJobs);
//   (b) si un dispatch del cerebro está pendiente: freno por
//       lectura (last_live_dispatch_at en worker_state +
//       hasRecentBrainToken + fase en curso en worker_phases).
//       OJO: la comprobación de directos en vivo (Twitch) NO se
//       usa para suprimir el dispatch: la corrida live del
//       cerebro tiene fallback a VOD cuando nadie está en directo
//       (viral-clip-agent/.github/workflows/cerebro-live.yml:50-53:
//       "si nadie está en vivo en GTA VI en ningún idioma, cambia
//       solo a modo VOD y corta de los VOD más recientes"). Un 0
//       en vivo NO es "no hay trabajo": suprimirlo mataría ese
//       fallback. El dispatch se decide solo por freno + fase.
//   (c) si el dispatch diario toca (09:00 Santiago, dedupe por
//       lectura en settings, misma clave que maybeDispatchDaily);
//   (d) si el diario de escritura previa (WAL de B1, en R2) tiene
//       algo pendiente de volcar (lectura R2, nunca D1).
// Además, la revisión periódica (como máximo cada 10-15 min) reevalúa
// lo programado: la hora de la última revisión se lee de
// worker_state.last_cycle_review_at (migración 0037) y se marca con
// UNA escritura cuando el ciclo completo corre de verdad.
//
// Regla de fallo: ante cualquier fallo de lectura, fail-open (se
// corre el ciclo / se dispara como antes). Perder una publicación
// por una lectura fallida sería peor que un ciclo de más: el
// comportamiento antiguo era correr siempre, así que fallar hacia
// él nunca rompe nada.
//
// Single-source: TODOS los intervalos del ciclo y los dispatches
// viven aquí. index.ts los importa; ningún otro módulo los define.

import type { D1Database, Env } from "./cloudflare";
import type { Tenant } from "./auth";
import { hasRecentBrainToken, liveRunInFlight } from "./brain";
import { getSettings, hasRecentWorkerEvent } from "./db";
import { santiagoParts } from "./telegram";
import { walHasPending } from "./wal";

/** Revisión periódica de lo programado: 12 min (dentro de la
 *  ventana 10-15 min del encargo; un solo valor, aquí). */
export const CYCLE_REVIEW_INTERVAL_MS = 12 * 60 * 1000;

/** "Vence en el próximo minuto": un job con next_run_at dentro de
 *  esta ventana ya despierta el ciclo (el cron es cada minuto, así
 *  que el job se ejecuta como mucho 1 min antes de su hora). */
export const CYCLE_DUE_WINDOW_MS = 60 * 1000;

/** Throttle del dispatch en vivo: como mucho 1 panel-live cada
 *  15 min por tenant (antes en index.ts; movido aquí para que la
 *  puerta y el dispatch lean el mismo valor). */
export const LIVE_DISPATCH_MIN_INTERVAL_MS = 15 * 60 * 1000;

/** Throttle del vigilante: como mucho 1 panel-watchdog cada
 *  10 min por tenant (antes en index.ts). */
export const WATCHDOG_DISPATCH_MIN_INTERVAL_MS = 10 * 60 * 1000;

/** Prefijo del evento que deja un dispatch panel-watchdog enviado
 *  (antes en index.ts): el throttle se lee de worker_events. */
export const WATCHDOG_DISPATCHED_PREFIX = "Dispatch panel-watchdog enviado";

/** Clave en settings del día del último dispatch diario enviado
 *  (antes en index.ts): el dedupe del dispatch 09:00 es por lectura. */
export const BRAIN_DAILY_SENT_DAY_KEY = "brain_daily_dispatched_day";

/** Clave en settings del resumen de la última validación diaria
 *  COMPLETA de cuentas (validación diaria 2026-11-23): objeto
 *  { day: "YYYY-MM-DD" (Chile), n_ok, n_invalid, checked_at: ms }.
 *  Solo se escribe en corrida completa (sin transitorios); una
 *  corrida parcial no marca el día. */
export const ACCOUNT_VALIDATION_DAY_KEY = "last_account_validation";

/** Hora de Santiago a partir de la cual toca la validación diaria
 *  de cuentas (primera pasada del ciclo >= 15:00 Chile; en invierno
 *  la primera pasada real es a las 17:00, el mecanismo >= sigue
 *  robusto). */
export const ACCOUNT_VALIDATION_HOUR_SANTIAGO = 15;

/** ¿Toca la validación diaria de cuentas? Primera pasada del ciclo
 *  a partir de las 15:00 Santiago y el día aún sin marcar en
 *  settings (misma clave y misma lectura que
 *  maybeValidateAccountsDaily; esa función vuelve a comprobarlo
 *  antes de validar). Solo lectura. Fail-open dentro de la ventana:
 *  si no se puede leer, que maybeValidateAccountsDaily decida. */
export async function isAccountValidationDue(db: D1Database, tenant: Tenant, nowMsValue: number): Promise<boolean> {
  const { day, hour } = santiagoParts(new Date(nowMsValue));
  if (hour < ACCOUNT_VALIDATION_HOUR_SANTIAGO) return false;
  try {
    const settings = await getSettings(db, tenant);
    const last = settings[ACCOUNT_VALIDATION_DAY_KEY] as { day?: string } | string | undefined;
    const lastDay = typeof last === "string" ? last : last?.day;
    return lastDay !== day;
  } catch {
    return true;
  }
}

export type CycleWakeReason = "due-job" | "review" | "journal" | "none";

export interface CycleGateDecision {
  /** El ciclo del publicador debe correr (hay trabajo de cola). */
  run: boolean;
  reason: CycleWakeReason;
  /** Próxima publicación programada (ms epoch) o null si no hay
   *  jobs scheduled/processing pendientes. */
  nextDueAtMs: number | null;
  /** La revisión periódica toca (última revisión hace >= 12 min o nunca). */
  reviewDue: boolean;
  /** Un dispatch del cerebro está pendiente (el freno lo permite). */
  liveDue: boolean;
  /** Un dispatch del vigilante está pendiente. */
  watchdogDue: boolean;
  /** El dispatch diario de las 09:00 toca. */
  dailyDue: boolean;
  /** El diario de escritura previa (R2, B1) tiene algo pendiente de volcar. */
  journalPending: boolean;
}

/** Decisión vacía: Worker parado (o sin nada que evaluar). Ningún
 *  dispatch, ninguna revisión, ningún ciclo. */
export function idleCycleGate(): CycleGateDecision {
  return {
    run: false,
    reason: "none",
    nextDueAtMs: null,
    reviewDue: false,
    liveDue: false,
    watchdogDue: false,
    dailyDue: false,
    journalPending: false,
  };
}

/** Próxima publicación programada: el next_run_at mínimo entre los
 *  jobs scheduled/processing del tenant. Un next_run_at NULL vence
 *  ya (dueJobs lo trata como vencido), así que cuenta como 0.
 *  null = no hay jobs pendientes. Solo lectura. Fail-open: sin
 *  lectura no se puede afirmar que no hay trabajo, así que se
 *  devuelve 0 (vence ya) y el ciclo corre como antes. */
export async function nextDueJobAt(db: D1Database, tenant: Tenant): Promise<number | null> {
  try {
    const res = await db
      .prepare("SELECT next_run_at FROM jobs WHERE tenant_id = ? AND state IN ('scheduled','processing')")
      .bind(tenant)
      .all<{ next_run_at: number | null }>();
    let min: number | null = null;
    for (const r of res.results ?? []) {
      const v = r.next_run_at ?? 0;
      if (min === null || v < min) min = v;
    }
    return min;
  } catch {
    return 0;
  }
}

/** Hora (ms epoch) de la última revisión completa del ciclo, leída
 *  de worker_state (migración 0037). 0 = nunca (o columna aún sin
 *  migrar: antes de aplicar la 0037 la revisión siempre toca, es
 *  decir, el ciclo corre como antes — fail-open, sin ahorro pero
 *  sin rotura — hasta que la migración entra con el deploy). */
export async function lastCycleReviewAt(db: D1Database, tenant: Tenant): Promise<number> {
  try {
    const row = await db
      .prepare("SELECT last_cycle_review_at FROM worker_state WHERE tenant_id = ?")
      .bind(tenant)
      .first<{ last_cycle_review_at: number | null }>();
    return row?.last_cycle_review_at ?? 0;
  } catch {
    return 0;
  }
}

/** Marca la revisión: UNA escritura en worker_state cuando el ciclo
 *  completo corre de verdad (el ciclo que corre ya reevaluó la Cola
 *  entera, así que cuenta como revisión). Nunca lanza: si la columna
 *  aún no existe o no hay fila, la próxima lectura dirá 0 y se
 *  revisará de nuevo. */
export async function markCycleReview(db: D1Database, tenant: Tenant, nowMsValue: number): Promise<void> {
  try {
    await db
      .prepare("UPDATE worker_state SET last_cycle_review_at = ? WHERE tenant_id = ?")
      .bind(nowMsValue, tenant)
      .run();
  } catch {
    // Sin columna o sin fila: se revisa de nuevo en el siguiente ciclo.
  }
}

/** ¿Toca el dispatch diario? 09:00 Santiago y el día aún sin
 *  marcar en settings (misma clave y misma lectura que
 *  maybeDispatchDaily; esa función vuelve a comprobarlo antes de
 *  disparar). Solo lectura. Fail-open dentro de la hora 09: si no
 *  se puede leer, que maybeDispatchDaily decida. */
export async function isDailyDispatchDue(db: D1Database, tenant: Tenant, nowMsValue: number): Promise<boolean> {
  const { day, hour } = santiagoParts(new Date(nowMsValue));
  if (hour !== 9) return false;
  try {
    const settings = await getSettings(db, tenant);
    return settings[BRAIN_DAILY_SENT_DAY_KEY] !== day;
  } catch {
    return true;
  }
}

/** ¿Está pendiente un dispatch del cerebro en vivo? Solo lecturas,
 *  en orden de barato a caro:
 *   1. throttle en worker_state (último dispatch hace <15 min);
 *   2. freno por lectura: token reciente en brain_tokens;
 *   3. corrida en curso (fase en worker_phases).
 *  NO se consulta Twitch aquí (ver cabecera: un 0 en vivo no es
 *  "no hay trabajo" por el fallback VOD de la corrida live).
 *  Fail-open: sin lectura, se dispara como antes. */
export async function isLiveDispatchDue(env: Env, tenant: Tenant, nowMsValue: number): Promise<boolean> {
  try {
    let last = 0;
    try {
      const row = await env.DB.prepare(
        "SELECT last_live_dispatch_at FROM worker_state WHERE tenant_id = ?",
      )
        .bind(tenant)
        .first<{ last_live_dispatch_at: number | null }>();
      last = row?.last_live_dispatch_at ?? 0;
    } catch {
      return true;
    }
    if (last > 0 && nowMsValue - last < LIVE_DISPATCH_MIN_INTERVAL_MS) return false;
    if (await hasRecentBrainToken(env.DB, tenant, LIVE_DISPATCH_MIN_INTERVAL_MS, nowMsValue)) return false;
    if (await liveRunInFlight(env.DB, tenant, nowMsValue)) return false;
    return true;
  } catch {
    return true;
  }
}

/** ¿Está pendiente un dispatch del vigilante? Espejo del throttle
 *  de maybeDispatchWatchdog: si ya se envió uno hace <10 min, no
 *  toca. Solo lectura (hasRecentWorkerEvent falla cerrado a false,
 *  así que ante un fallo de lectura se dispara, como antes). */
export async function isWatchdogDispatchDue(db: D1Database, tenant: Tenant, nowMsValue: number): Promise<boolean> {
  return !(await hasRecentWorkerEvent(db, tenant, "github_dispatch", WATCHDOG_DISPATCHED_PREFIX, WATCHDOG_DISPATCH_MIN_INTERVAL_MS, nowMsValue));
}

/** ¿Hay un diario de escritura previa pendiente de volcar? Lectura
 *  de R2 (B1, walHasPending), nunca D1. Si el diario tiene entradas
 *  sin volcar, el ciclo debe correr para que flushWal las vuelque. */
export async function pendingWriteJournal(env: Env, tenant: Tenant, nowMsValue: number): Promise<boolean> {
  return walHasPending(env, tenant, new Date(nowMsValue));
}

/** La puerta completa: decide con solo lecturas si el ciclo del
 *  publicador corre y si algún dispatch está pendiente. El llamador
 *  (runWorkerCycle) sale temprano y en silencio cuando run es false;
 *  el scheduled handler usa liveDue/watchdogDue/dailyDue para llamar
 *  a los dispatches aunque el publicador no corriera. */
export async function cycleGate(env: Env, tenant: Tenant, nowMsValue: number): Promise<CycleGateDecision> {
  const nextDueAtMs = await nextDueJobAt(env.DB, tenant);
  const lastReview = await lastCycleReviewAt(env.DB, tenant);
  const reviewDue = nowMsValue - lastReview >= CYCLE_REVIEW_INTERVAL_MS;
  const journalPending = await pendingWriteJournal(env, tenant, nowMsValue);
  const dueSoon = nextDueAtMs !== null && nextDueAtMs <= nowMsValue + CYCLE_DUE_WINDOW_MS;
  const liveDue = await isLiveDispatchDue(env, tenant, nowMsValue);
  const watchdogDue = await isWatchdogDispatchDue(env.DB, tenant, nowMsValue);
  const dailyDue = await isDailyDispatchDue(env.DB, tenant, nowMsValue);
  const run = dueSoon || reviewDue || journalPending;
  const reason: CycleWakeReason = dueSoon ? "due-job" : journalPending ? "journal" : reviewDue ? "review" : "none";
  return { run, reason, nextDueAtMs, reviewDue, liveDue, watchdogDue, dailyDue, journalPending };
}
