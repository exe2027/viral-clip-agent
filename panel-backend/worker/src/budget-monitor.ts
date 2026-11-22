// Monitoreo automático gratuito del presupuesto D1 (A6,
// encargo anti-recaída 2026-11-20).
//
// - Lee cada hora el consumo del día por base (la misma lectura
//   del contador de budget.ts, cacheada en memoria, no en D1).
// - Alertas por Telegram (bot gratuito) en los hitos 50%, 70% y
//   90% del tope operativo, y ante cualquier error 7500 (cuota
//   agotada). Una vez por hito y día: el dedupe vive EN MEMORIA
//   (nunca en D1 — una marca en D1 sería una escritura más, y
//   con la cuota agotada no persistiría; además el isolate se
//   recicla y una alerta duplicada tras un reciclaje es benigna
//   frente a romper el presupuesto).
// - Informe diario automático a las 20:50 (Santiago, justo antes
//   del reset de las 21:00): escrituras por base, top 3 fuentes,
//   publicaciones hechas, pendientes y fallidas — compuesto solo
//   desde lecturas.
//
// El envío por Telegram lee TELEGRAM_BOT_TOKEN del entorno
// (secreto de Worker, pendiente de B8-decisión). Si falta el
// token o el tenant no tiene chat vinculado, degrada a UNA sola
// entrada de log con dedupe (nunca rompe el ciclo, nunca imprime
// el secreto). El chat es el vinculado del tenant
// (telegram_links): un tenant nunca recibe datos de otro.
//
// $0: Telegram Bot API y la API de analíticas de Cloudflare son
// gratuitas. Ninguna función de este archivo escribe en D1.

import type { D1Database, Env } from "./cloudflare";
import type { Tenant } from "./auth";
import {
  D1_DAILY_ACCOUNT_LIMIT,
  D1_NORMAL_OPERATION_CAP,
  D1_TENANT_DAILY_LIMIT,
  createBudgetGuard,
  d1DatabaseName,
  fetchDailyQuotaErrors,
  fetchTopWriteQueries,
  readDailyWrites,
  utcDayStartMs,
  type BudgetGuard,
  type DailyWrites,
} from "./budget";
import { getTelegramLinkByTenant, santiagoDayKeyMs } from "./db";
import { botConfigured, santiagoParts, sendTelegram, TELEGRAM_MAX_CHARS } from "./telegram";

/** Ventana del informe diario (Santiago): 20:50–20:59. */
export const DAILY_BUDGET_REPORT_HOUR = 20;
export const DAILY_BUDGET_REPORT_MINUTE_FROM = 50;

/** Prefijo de las entradas de log del monitor (una sola línea
 *  por alerta degradada; el token nunca aparece). */
const LOG_PREFIX = "[d1-budget]";

// ---------------------------------------------------------------------------
// Dedupe de alertas EN MEMORIA (nunca en D1).
// ---------------------------------------------------------------------------

const sentAlerts = new Map<string, number>();

/** Limpia entradas de hace más de 48 h para que el mapa no crezca
 *  sin límite en un isolate de larga vida. */
function pruneAlertState(nowMs: number): void {
  const cutoff = nowMs - 48 * 3600 * 1000;
  for (const [key, at] of sentAlerts) {
    if (at < cutoff) sentAlerts.delete(key);
  }
}

function alertSent(key: string, nowMs: number): boolean {
  pruneAlertState(nowMs);
  return sentAlerts.has(key);
}

function markAlertSent(key: string, nowMs: number): void {
  pruneAlertState(nowMs);
  sentAlerts.set(key, nowMs);
}

/** Limpia el estado de alertas (solo tests). */
export function resetBudgetAlertState(): void {
  sentAlerts.clear();
}

// ---------------------------------------------------------------------------
// Envío con degradación a log.
// ---------------------------------------------------------------------------

export interface BudgetAlertResult {
  sent: boolean;
  /** "telegram" | "log" (degradado) | "duplicate" (ya avisado hoy). */
  via: "telegram" | "log" | "duplicate";
  reason?: string;
}

/** Envía una alerta de presupuesto al chat vinculado del tenant.
 *  `key` es única por alerta y día (p. ej. "milestone-70:2026-11-20"):
 *  una alerta con la misma key solo se envía una vez. Sin token
 *  de Telegram o sin chat vinculado, degrada a UNA entrada de log
 *  (primera línea del mensaje + motivo). Nunca levanta, nunca
 *  imprime el token, nunca escribe en D1. */
export async function notifyBudgetAlert(
  env: Env,
  tenant: Tenant,
  key: string,
  message: string,
  nowMs: number,
  fetchFn: typeof fetch = fetch,
): Promise<BudgetAlertResult> {
  if (alertSent(key, nowMs)) return { sent: false, via: "duplicate", reason: "Ya avisado hoy." };
  // Se marca ANTES de enviar: si Telegram falla no se reintenta
  // cada minuto (el informe diario de las 20:50 lleva los números
  // igualmente); el fallo queda en una sola línea de log.
  markAlertSent(key, nowMs);
  const firstLine = message.split("\n")[0]?.trim() || "Alerta de presupuesto D1.";
  if (!botConfigured(env)) {
    console.log(`${LOG_PREFIX} Telegram sin configurar (falta TELEGRAM_BOT_TOKEN); alerta no enviada [${key}]: ${firstLine}`);
    return { sent: false, via: "log", reason: "Telegram no configurado." };
  }
  let chatId: string | null = null;
  try {
    const link = await getTelegramLinkByTenant(env.DB, tenant);
    chatId = link?.chat_id ?? null;
  } catch {
    chatId = null;
  }
  if (!chatId) {
    console.log(`${LOG_PREFIX} Sin chat de Telegram vinculado para ${tenant}; alerta no enviada [${key}]: ${firstLine}`);
    return { sent: false, via: "log", reason: "Sin chat vinculado." };
  }
  try {
    const sent = await sendTelegram(env, chatId, message, fetchFn);
    if (!sent.ok) {
      console.log(`${LOG_PREFIX} Telegram falló [${key}]: ${sent.error ?? `HTTP ${sent.status}`}. ${firstLine}`);
      return { sent: false, via: "log", reason: sent.error ?? `HTTP ${sent.status}` };
    }
    return { sent: true, via: "telegram" };
  } catch (e) {
    console.log(`${LOG_PREFIX} Telegram falló [${key}]: ${e instanceof Error ? e.message : String(e)}. ${firstLine}`);
    return { sent: false, via: "log", reason: e instanceof Error ? e.message : String(e) };
  }
}

// ---------------------------------------------------------------------------
// Hitos: qué alerta toca según el nivel (puro, testeable).
// ---------------------------------------------------------------------------

export type BudgetMilestone = "notice-50" | "restricted-70" | "essential-90";

export const MILESTONE_LABEL: Record<BudgetMilestone, string> = {
  "notice-50": "50% del tope operativo",
  "restricted-70": "70% del tope operativo",
  "essential-90": "90% del tope operativo",
};

/** Hito más alto que corresponde a un nivel (null en "ok"). */
export function milestoneForLevel(level: BudgetGuard["level"]): BudgetMilestone | null {
  if (level === "essential-only") return "essential-90";
  if (level === "restricted") return "restricted-70";
  if (level === "notice") return "notice-50";
  return null;
}

/** Texto de la alerta de hito (sin secretos, sin inventar: solo
 *  los números del contador). */
export function buildMilestoneAlertMessage(
  tenant: Tenant,
  milestone: BudgetMilestone,
  guard: BudgetGuard,
  dayKey: string,
): string {
  const ratio = guard.governingRatio;
  const pct = ratio === null ? "?" : `${Math.round(ratio * 100)} %`;
  const tenantPart = guard.tenantWrites === null
    ? "escrituras del tenant: sin dato"
    : `escrituras del tenant: ${guard.tenantWrites.toLocaleString("es-CL")} / ${D1_TENANT_DAILY_LIMIT.toLocaleString("es-CL")}`;
  const globalPart = guard.globalWrites === null
    ? "escrituras de la cuenta: sin dato"
    : `escrituras de la cuenta: ${guard.globalWrites.toLocaleString("es-CL")} / ${D1_NORMAL_OPERATION_CAP.toLocaleString("es-CL")} (tope operativo; límite de la cuenta ${D1_DAILY_ACCOUNT_LIMIT.toLocaleString("es-CL")})`;
  const action =
    milestone === "notice-50"
      ? "Aviso: el consumo va por la mitad del tope operativo. Revisa qué está escribiendo hoy."
      : milestone === "restricted-70"
        ? "Freno activo: a partir de ahora solo se escribe lo esencial (reclamar jobs y marcarlos). Los logs, las métricas y la creación masiva de jobs quedan parados hasta el reset de las 21:00."
        : "SOLO LO ESENCIAL: queda la reserva para reclamar jobs y marcarlos. Si el consumo sigue, la cuenta llega al límite de 100.000 y todo se para hasta las 21:00.";
  const sourceNote = guard.source === "analytics"
    ? "Fuente: analítica de Cloudflare."
    : guard.source === "events-fallback"
      ? "Fuente: conteo de eventos en D1 (cota inferior; la analítica no se pudo leer)."
      : "Fuente: contador no disponible (sin analítica ni conteo de eventos).";
  return (
    `⚠️ Presupuesto D1 — ${MILESTONE_LABEL[milestone]} (${tenant}, ${dayKey})\n` +
    `Consumo: ${pct} del tope operativo.\n` +
    `${tenantPart}\n${globalPart}\n${action}\n${sourceNote}`
  );
}

/** Texto de la alerta de errores 7500. */
export function buildQuotaErrorAlertMessage(tenant: Tenant, quotaErrors: number, dayKey: string): string {
  return (
    `🚨 Errores 7500 en D1 (${tenant}, ${dayKey})\n` +
    `Hoy ya hubo ${quotaErrors} ${quotaErrors === 1 ? "consulta" : "consultas"} rechazadas por cuota diaria agotada (error 7500).\n` +
    "La publicación sigue solo con lo esencial hasta el reset de las 21:00. Revisa el panel: el freno del 70%/90% debería estar activo."
  );
}

// ---------------------------------------------------------------------------
// Chequeo por hora (lo llama el cron; nunca rompe el ciclo).
// ---------------------------------------------------------------------------

export interface BudgetMonitorSummary {
  level: BudgetGuard["level"];
  tenantWrites: number | null;
  globalWrites: number | null;
  source: DailyWrites["source"];
  alertsSent: string[];
  quotaErrors: number | null;
}

let lastMonitorHourKey: string | null = null;

/** Limpia la marca de hora del monitor (solo tests). */
export function resetBudgetMonitorHour(): void {
  lastMonitorHourKey = null;
}

/** Chequeo horario: lee el contador, avisa del hito más alto
 *  alcanzado (una vez por hito y día) y de cualquier error 7500
 *  (una vez al día). Corre cuando el minuto de Santiago es 0
 *  (una vez por hora) y nunca levanta. */
export async function maybeRunBudgetMonitor(
  env: Env,
  tenant: Tenant,
  nowMs: number,
  fetchFn: typeof fetch = fetch,
): Promise<BudgetMonitorSummary | null> {
  try {
    const parts = santiagoParts(new Date(nowMs));
    if (parts.minute !== 0) return null;
    const hourKey = `${parts.day} ${String(parts.hour).padStart(2, "0")}`;
    if (lastMonitorHourKey === hourKey) return null;
    lastMonitorHourKey = hourKey;
    const guard = await createBudgetGuard(env, env.DB, tenant, nowMs, fetchFn);
    const alertsSent: string[] = [];
    const dayKey = parts.day;
    const milestone = milestoneForLevel(guard.level);
    if (milestone) {
      const key = `milestone-${milestone}:${dayKey}`;
      const res = await notifyBudgetAlert(
        env,
        tenant,
        key,
        buildMilestoneAlertMessage(tenant, milestone, guard, dayKey),
        nowMs,
        fetchFn,
      );
      if (res.sent) alertsSent.push(key);
    }
    let quotaErrors: number | null = null;
    try {
      quotaErrors = await fetchDailyQuotaErrors(env, nowMs, fetchFn);
    } catch {
      quotaErrors = null; // Sin analítica no se pueden ver los 7500.
    }
    if (quotaErrors !== null && quotaErrors > 0) {
      const key = `quota-7500:${dayKey}`;
      const res = await notifyBudgetAlert(
        env,
        tenant,
        key,
        buildQuotaErrorAlertMessage(tenant, quotaErrors, dayKey),
        nowMs,
        fetchFn,
      );
      if (res.sent) alertsSent.push(key);
    }
    if (guard.source === "unavailable") {
      console.log(`${LOG_PREFIX} Contador de escrituras no disponible para ${tenant} (${dayKey}): sin analítica ni conteo de eventos. El guardián sigue en fail-open.`);
    }
    return {
      level: guard.level,
      tenantWrites: guard.tenantWrites,
      globalWrites: guard.globalWrites,
      source: guard.source,
      alertsSent,
      quotaErrors,
    };
  } catch (e) {
    // El monitoreo nunca rompe el ciclo ni el resto del cron.
    console.log(`${LOG_PREFIX} Chequeo horario falló: ${e instanceof Error ? e.message : String(e)}`);
    return null;
  }
}

// ---------------------------------------------------------------------------
// Informe diario 20:50.
// ---------------------------------------------------------------------------

export interface DailyPublishStats {
  postedToday: number;
  pending: number;
  failedToday: number;
}

/** Publicaciones de hoy, pendientes y fallidas (SOLO lecturas):
 *  - hechas hoy: jobs posted con posted_day = hoy (Santiago);
 *  - pendientes: jobs en scheduled/processing/awaiting_captcha;
 *  - fallidas hoy: jobs failed con updated_at >= inicio del día
 *    UTC. Una sola consulta. */
export async function readDailyPublishStats(
  db: D1Database,
  tenant: Tenant,
  nowMs: number,
): Promise<DailyPublishStats> {
  const day = santiagoDayKeyMs(nowMs);
  const dayStart = utcDayStartMs(nowMs);
  const row = await db
    .prepare(
      "SELECT " +
        "(SELECT COUNT(*) FROM jobs WHERE tenant_id = ? AND state = 'posted' AND posted_day = ?) AS posted_today, " +
        "(SELECT COUNT(*) FROM jobs WHERE tenant_id = ? AND state IN ('scheduled', 'processing', 'awaiting_captcha')) AS pending, " +
        "(SELECT COUNT(*) FROM jobs WHERE tenant_id = ? AND state = 'failed' AND updated_at >= ?) AS failed_today",
    )
    .bind(tenant, day, tenant, tenant, dayStart)
    .first<{ posted_today: number; pending: number; failed_today: number }>();
  return {
    postedToday: row?.posted_today ?? 0,
    pending: row?.pending ?? 0,
    failedToday: row?.failed_today ?? 0,
  };
}

function fmtWrites(n: number | null, cap: number): string {
  if (n === null) return "sin dato";
  return `${n.toLocaleString("es-CL")} / ${cap.toLocaleString("es-CL")} (${Math.round((n / cap) * 100)} %)`;
}

/** Informe diario (puro, testeable). Sin secretos, sin inventar:
 *  si un dato no se pudo leer, dice "sin dato". */
export function buildDailyBudgetReport(input: {
  tenant: Tenant;
  dayKey: string;
  writes: DailyWrites;
  topQueries: Array<{ query: string; rowsWritten: number }>;
  stats: DailyPublishStats;
  quotaErrors: number | null;
}): string {
  const { tenant, dayKey, writes, topQueries, stats, quotaErrors } = input;
  const lines: string[] = [
    `📊 Presupuesto D1 — informe diario (${tenant}, ${dayKey})`,
    "",
    `Tenant: ${fmtWrites(writes.tenantWrites, D1_TENANT_DAILY_LIMIT)}`,
    `Cuenta (tope operativo): ${fmtWrites(writes.globalWrites, D1_NORMAL_OPERATION_CAP)}`,
    `Límite de la cuenta: ${D1_DAILY_ACCOUNT_LIMIT.toLocaleString("es-CL")} escrituras/día (reset 21:00).`,
    "",
  ];
  if (writes.perDatabase.length > 0) {
    lines.push("Por base:");
    for (const p of writes.perDatabase) {
      lines.push(`  ${d1DatabaseName(p.databaseId)}: ${p.rowsWritten.toLocaleString("es-CL")}`);
    }
    lines.push("");
  } else if (writes.source === "events-fallback") {
    lines.push("Por base: sin desglose (la analítica no se pudo leer; el total del tenant es una cota inferior contando eventos).");
    lines.push("");
  }
  if (topQueries.length > 0) {
    lines.push("Top 3 fuentes de escrituras:");
    topQueries.slice(0, 3).forEach((t, i) => {
      const q = t.query.replace(/\s+/g, " ").trim();
      lines.push(`  ${i + 1}. ${q.length > 120 ? `${q.slice(0, 117)}…` : q} — ${t.rowsWritten.toLocaleString("es-CL")}`);
    });
    lines.push("");
  } else {
    lines.push("Top 3 fuentes: sin dato (la analítica no se pudo leer).");
    lines.push("");
  }
  lines.push("Publicación hoy:");
  lines.push(`  Publicadas: ${stats.postedToday} · Pendientes: ${stats.pending} · Fallidas hoy: ${stats.failedToday}`);
  lines.push("");
  if (quotaErrors !== null) {
    lines.push(quotaErrors > 0 ? `⚠️ Errores 7500 hoy: ${quotaErrors}.` : "Errores 7500 hoy: 0.");
  } else {
    lines.push("Errores 7500 hoy: sin dato (la analítica no se pudo leer).");
  }
  lines.push(
    writes.source === "analytics"
      ? "Fuente: analítica de Cloudflare."
      : writes.source === "events-fallback"
        ? "Fuente: conteo de eventos en D1 (cota inferior)."
        : "Fuente: contador no disponible.",
  );
  const text = lines.join("\n").trimEnd();
  return text.length <= TELEGRAM_MAX_CHARS ? text : `${text.slice(0, TELEGRAM_MAX_CHARS - 1)}…`;
}

/** Envía el informe diario a las 20:50–20:59 (Santiago), una vez
 *  por día (dedupe en memoria dentro de notifyBudgetAlert). Corre
 *  lo llame quien lo llame: fuera de la ventana no hace nada.
 *  Nunca levanta ni rompe el ciclo. Si Telegram no está
 *  disponible, la alerta degrada a una sola entrada de log (ver
 *  notifyBudgetAlert). Si componer el informe falla (D1 caída),
 *  se reintenta en el minuto siguiente mientras siga la ventana
 *  20:50–20:59; pasado ese rato, el informe de mañana cubre el
 *  hueco y el chequeo horario sigue vigilando los hitos. */
export async function maybeSendDailyBudgetReport(
  env: Env,
  tenant: Tenant,
  nowMs: number,
  fetchFn: typeof fetch = fetch,
): Promise<BudgetAlertResult | null> {
  try {
    const parts = santiagoParts(new Date(nowMs));
    if (parts.hour !== DAILY_BUDGET_REPORT_HOUR || parts.minute < DAILY_BUDGET_REPORT_MINUTE_FROM) return null;
    const dayKey = parts.day;
    const key = `daily-report:${dayKey}`;
    if (alertSent(key, nowMs)) return { sent: false, via: "duplicate", reason: "Ya enviado hoy." };
    const writes = await readDailyWrites(env, env.DB, tenant, nowMs, fetchFn);
    const stats = await readDailyPublishStats(env.DB, tenant, nowMs);
    let topQueries: Array<{ query: string; rowsWritten: number }> = [];
    try {
      topQueries = await fetchTopWriteQueries(env, nowMs, 3, fetchFn);
    } catch {
      topQueries = [];
    }
    let quotaErrors: number | null = null;
    try {
      quotaErrors = await fetchDailyQuotaErrors(env, nowMs, fetchFn);
    } catch {
      quotaErrors = null;
    }
    const message = buildDailyBudgetReport({ tenant, dayKey, writes, topQueries, stats, quotaErrors });
    return await notifyBudgetAlert(env, tenant, key, message, nowMs, fetchFn);
  } catch (e) {
    console.log(`${LOG_PREFIX} Informe diario falló: ${e instanceof Error ? e.message : String(e)}`);
    return { sent: false, via: "log", reason: e instanceof Error ? e.message : String(e) };
  }
}
