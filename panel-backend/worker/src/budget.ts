// Presupuesto de escrituras D1 con tope duro + guardián (A1,
// encargo anti-recaída 2026-11-20).
//
// El 2026-11-20 la cuenta llegó a 104.610 escrituras de 100.000 y
// todo se paró (cerebro, publicador y panel) hasta el reset de las
// 21:00. La causa no fue el trabajo real (~1.500 filas/día) sino
// la migración (23.531 de una vez), un Worker nuevo disparando el
// cerebro cada minuto sin credenciales (~60.000) y el publicador
// registrando lo mismo cada minuto (~22.000/día, 98% repetido).
// Ver runs/d1-writes-2026-11-20/por-que-se-gasto-d1.md.
//
// Este módulo es la ÚNICA fuente de los números del presupuesto
// (single-source: un valor, un sitio, sin defaults silenciosos):
//   - Límite de Cloudflare: 100.000 escrituras/día POR CUENTA
//     (todas las D1 juntas; una base por usuario NO multiplica
//     la cuota). Reset a medianoche UTC = 21:00 -03.
//   - Reparto: operación normal 50.000 + reserva intocable
//     30.000 (reclamar job, marcar subido/fallido) + margen
//     20.000 (migraciones/imprevistos). 50.000+30.000+20.000 =
//     100.000 por construcción (D1_MARGIN se deriva, no se
//     escribe a mano).
//   - Por tenant: ≤3.500/día (medido tras el arreglo: ~2.500-3.500).
//
// Hitos (fracción del TOPE OPERATIVO, 50.000 — no del límite de
// la cuenta; el freno tiene que saltar ANTES de tocar la reserva):
//   - 50% (25.000) → aviso por Telegram.
//   - 70% (35.000) → se detiene todo lo no esencial (logs,
//     métricas, eventos informativos, creación masiva de jobs,
//     dispatches automáticos frecuentes).
//   - 90% (45.000) → solo lo esencial (reclamar y marcar jobs)
//     + alerta.
// El guardián (shouldWrite) se consulta antes de cada escritura
// no esencial; lo esencial NUNCA se frena.
//
// EL CONTADOR NO ESCRIBE EN D1. Se lee de la API de analíticas
// de Cloudflare (GraphQL, la misma que usa el panel de
// Cloudflare para las métricas D1: d1AnalyticsAdaptiveGroups /
// d1QueriesAdaptiveGroups, sum { rowsWritten }), con fallback a
// contar las filas de hoy en las tablas de eventos (una lectura).
// El contador se cachea EN MEMORIA (5 min) por isolate: nunca
// en D1, nunca en settings. Si todo falla, el guardián es
// fail-open (deja escribir): un contador roto no puede parar la
// publicación; el monitor (budget-monitor.ts) avisa de que el
// contador no está disponible.
//
// Secretos: el token de la API de analíticas es un secreto de
// Worker (CLOUDFLARE_API_TOKEN, `wrangler secret put`, permiso
// "Account Analytics Read"); el account ID es una var
// (CLOUDFLARE_ACCOUNT_ID). Ninguno de los dos se imprime nunca:
// ni en logs, ni en errores, ni en respuestas. Si faltan, el
// contador cae al fallback por eventos (no se rompe nada).

import type { D1Database, Env } from "./cloudflare";
import type { Tenant } from "./auth";
// isD1QuotaError vive en retry.ts (A4, única fuente) y wal.ts lo
// re-exporta (B1 lo usa para el volcado del diario): una sola
// definición en todo el Worker. Se casa por el código 7500 y, como
// respaldo, por D1 + límite/cuota (el texto exacto del error D1 no
// se pudo verificar sin escribir en D1, cuota agotada al
// implementar esto). Se importa DESDE retry.ts, no desde wal.ts:
// wal.ts importa gate.ts, que importa db.ts, que importa este
// módulo — importar desde wal.ts cerraría un ciclo db→budget→
// wal→gate→db y las consts de arriba caerían en TDZ cuando db.ts
// evaluara sus alias top-level (D1_DAILY_WRITE_LIMIT).
// retry.ts no importa nada: este módulo es acíclico.
import { isD1QuotaError } from "./retry";

// ---------------------------------------------------------------------------
// Presupuesto (ÚNICA fuente; ver cabecera).
// ---------------------------------------------------------------------------

/** Límite de Cloudflare: escrituras/día POR CUENTA (todas las D1). */
export const D1_DAILY_ACCOUNT_LIMIT = 100_000;
/** Tope por tenant y día (objetivo medido tras el arreglo). */
export const D1_TENANT_DAILY_LIMIT = 3_500;
/** Tope global de operación normal: por encima, algo va mal. */
export const D1_NORMAL_OPERATION_CAP = 50_000;
/** Reserva intocable: reclamar jobs y marcarlos subido/fallido. */
export const D1_ESSENTIAL_RESERVE = 30_000;
/** Margen (migraciones/imprevistos). Derivado: nunca se escribe
 *  a mano para que el reparto siempre sume el límite. */
export const D1_MARGIN = D1_DAILY_ACCOUNT_LIMIT - D1_NORMAL_OPERATION_CAP - D1_ESSENTIAL_RESERVE; // 20.000

/** Hitos sobre el tope operativo (D1_NORMAL_OPERATION_CAP). */
export const BUDGET_NOTICE_RATIO = 0.5; // 25.000 escrituras: aviso
export const BUDGET_RESTRICTED_RATIO = 0.7; // 35.000: sin no esencial
export const BUDGET_ESSENTIAL_ONLY_RATIO = 0.9; // 45.000: solo esencial

/** TTL de la caché en memoria del contador (por isolate). */
export const BUDGET_COUNTER_CACHE_MS = 5 * 60 * 1000;
/** Edad máxima de un guardián en vigor: pasado este tiempo sin
 *  que el ciclo lo refresque, se trata como ausente (fail-open).
 *  Cubre el caso Worker parado con un guardián viejo restrictivo. */
export const BUDGET_GUARD_MAX_AGE_MS = 15 * 60 * 1000;

// ---------------------------------------------------------------------------
// Bases D1 de la cuenta (nombres ↔ IDs). Fuente de los IDs:
// wrangler.toml / wrangler.sebastian.toml / wrangler.kaez.toml.
// La analítica GraphQL solo devuelve databaseId: este mapa es lo
// que pone el nombre legible en el informe diario. Un ID
// desconocido se enseña como db:<8 primeros>, nunca se inventa
// un nombre.
// ---------------------------------------------------------------------------
export const D1_DATABASE_NAMES: Record<string, string> = {
  "5cf19675-f905-4e49-b8f2-96ae0810fafd": "clips-avc",
  "ee97f58d-0b56-4617-9976-fcf0022edb54": "clips-avc-sebastian",
  "7ce9af3c-110b-4ffe-8224-29ef0a3d78fa": "clips-avc-kaez",
};

/** Nombre legible de una base por su ID (o db:<prefijo>). */
export function d1DatabaseName(databaseId: string): string {
  return D1_DATABASE_NAMES[databaseId] ?? `db:${databaseId.slice(0, 8)}`;
}

/** ID de la base propia de un tenant (clips-avc-<tenant>), o null
 *  si no está en el mapa: en ese caso el contador del tenant es
 *  desconocido y solo manda el ratio global. */
export function tenantDatabaseId(tenant: Tenant): string | null {
  const name = `clips-avc-${tenant}`;
  for (const [id, n] of Object.entries(D1_DATABASE_NAMES)) {
    if (n === name) return id;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Nivel y tipos de escritura.
// ---------------------------------------------------------------------------

export type BudgetLevel = "ok" | "notice" | "restricted" | "essential-only";

/** Clase de escritura. Solo "essential" sobrevive al freno del
 *  70%/90%: reclamar un job y marcarlo (subido/fallido/borrador).
 *  Todo lo demás es no esencial: logs, métricas, eventos
 *  informativos, creación masiva de jobs, dispatches automáticos
 *  frecuentes y recogida de analíticas. */
export type BudgetWriteKind =
  | "essential"
  | "log"
  | "metric"
  | "event"
  | "bulk-job"
  | "dispatch"
  | "analytics"
  | "other";

export type BudgetCounterSource = "analytics" | "events-fallback" | "unavailable";

export interface DatabaseWrites {
  databaseId: string;
  rowsWritten: number;
}

/** Escrituras de hoy (día UTC, el del reset de la cuota). */
export interface DailyWrites {
  /** Escrituras de la base propia del tenant (null si no se
   *  puede saber: en fallback solo se ven las filas de eventos
   *  de ESTA base, que es una cota inferior; en analíticas, si
   *  el ID de la base no está en el mapa). */
  tenantWrites: number | null;
  /** Escrituras de TODA la cuenta (null si no se puede saber:
   *  en fallback no se ven las demás bases). */
  globalWrites: number | null;
  /** Desglose por base (vacío en fallback: solo se conoce el
   *  total propio, sin desglose por base). */
  perDatabase: DatabaseWrites[];
  source: BudgetCounterSource;
  fetchedAtMs: number;
}

export interface BudgetState {
  level: BudgetLevel;
  tenantWrites: number | null;
  globalWrites: number | null;
  /** Ratio del tenant contra su tope (null si no se sabe). */
  tenantRatio: number | null;
  /** Ratio global contra el tope operativo (null si no se sabe). */
  globalRatio: number | null;
  /** El ratio que manda: el mayor de los dos conocidos. */
  governingRatio: number | null;
  source: BudgetCounterSource;
  evaluatedAtMs: number;
}

export interface BudgetGuard extends BudgetState {
  /** ¿Se puede hacer esta escritura ahora? "essential" siempre
   *  es true. Lo no esencial es true solo en "ok" y "notice". */
  shouldWrite(kind: BudgetWriteKind): boolean;
}

// Día UTC (el día de la cuota: reset 00:00 UTC = 21:00 -03).
// ---------------------------------------------------------------------------

/** "YYYY-MM-DD" UTC de un instante. */
export function utcDayKey(nowMs: number): string {
  return new Date(nowMs).toISOString().slice(0, 10);
}

/** Inicio del día UTC (ms epoch) de un instante. */
export function utcDayStartMs(nowMs: number): number {
  const d = new Date(nowMs);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
}

// ---------------------------------------------------------------------------
// Evaluación del nivel (pura, testeable).
// ---------------------------------------------------------------------------

/** Nivel a partir de los contadores. Manda el peor ratio: el del
 *  tenant contra su tope (3.500) o el global contra el tope
 *  operativo (50.000). Sin ningún contador conocido, "ok"
 *  (fail-open: un contador roto no para la publicación). */
export function evaluateBudget(
  tenantWrites: number | null,
  globalWrites: number | null,
  source: BudgetCounterSource,
  nowMs: number,
): BudgetState {
  const tenantRatio = tenantWrites === null ? null : tenantWrites / D1_TENANT_DAILY_LIMIT;
  const globalRatio = globalWrites === null ? null : globalWrites / D1_NORMAL_OPERATION_CAP;
  const governingRatio =
    tenantRatio === null ? globalRatio : globalRatio === null ? tenantRatio : Math.max(tenantRatio, globalRatio);
  let level: BudgetLevel = "ok";
  if (governingRatio !== null) {
    if (governingRatio >= BUDGET_ESSENTIAL_ONLY_RATIO) level = "essential-only";
    else if (governingRatio >= BUDGET_RESTRICTED_RATIO) level = "restricted";
    else if (governingRatio >= BUDGET_NOTICE_RATIO) level = "notice";
  }
  return { level, tenantWrites, globalWrites, tenantRatio, globalRatio, governingRatio, source, evaluatedAtMs: nowMs };
}

/** Guardián a partir de un estado: el shouldWrite es la única
 *  puerta. Lo esencial nunca se frena. */
export function budgetGuardFromState(state: BudgetState): BudgetGuard {
  return {
    ...state,
    shouldWrite(kind: BudgetWriteKind): boolean {
      if (kind === "essential") return true;
      return state.level === "ok" || state.level === "notice";
    },
  };
}

// ---------------------------------------------------------------------------
// Contador por analíticas de Cloudflare (GraphQL).
// ---------------------------------------------------------------------------

const CLOUDFLARE_GRAPHQL_URL = "https://api.cloudflare.com/graphql";

/** Query GraphQL (misma que usa el panel de Cloudflare):
 *  escrituras de hoy por base, agrupadas por databaseId.
 *  `date` es el día UTC; el filtro exige accountTag. */
function dailyWritesQuery(): string {
  return `query D1DailyWrites($accountTag: string!, $day: Date!) {
  viewer {
    accounts(filter: { accountTag: $accountTag }) {
      d1AnalyticsAdaptiveGroups(
        limit: 10000
        filter: { date_geq: $day, date_leq: $day }
      ) {
        sum { rowsWritten }
        dimensions { date databaseId }
      }
    }
  }
}`;
}

interface AnalyticsGroupsResponse {
  data?: {
    viewer?: {
      accounts?: Array<{
        d1AnalyticsAdaptiveGroups?: Array<{
          sum?: { rowsWritten?: number };
          dimensions?: { databaseId?: string };
        }>;
      }>;
    };
  };
  errors?: Array<{ message?: string }>;
}

/** Lee el consumo de hoy desde la API de analíticas. Levanta si
 *  falta el token o el account ID, si la API responde error, o si
 *  la respuesta no trae la forma esperada. El token viaja SOLO en
 *  el header Authorization y NUNCA se incluye en ningún error. */
export async function fetchDailyWritesFromAnalytics(
  env: Env,
  tenant: Tenant,
  nowMs: number,
  fetchFn: typeof fetch = fetch,
): Promise<DailyWrites> {
  const token = (env.CLOUDFLARE_API_TOKEN ?? "").trim();
  const accountTag = (env.CLOUDFLARE_ACCOUNT_ID ?? "").trim();
  if (!token || !accountTag) {
    throw new Error("Sin CLOUDFLARE_API_TOKEN o CLOUDFLARE_ACCOUNT_ID: no se puede leer la analítica D1.");
  }
  const day = utcDayKey(nowMs);
  const res = await fetchFn(CLOUDFLARE_GRAPHQL_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({
      query: dailyWritesQuery(),
      variables: { accountTag, day },
    }),
  });
  if (!res.ok) throw new Error(`La API de analíticas de Cloudflare devolvió HTTP ${res.status}.`);
  const payload = (await res.json()) as AnalyticsGroupsResponse;
  if (payload.errors?.length) {
    throw new Error(`La API de analíticas de Cloudflare devolvió errores: ${payload.errors[0]?.message ?? "sin detalle"}`.slice(0, 200));
  }
  const groups = payload.data?.viewer?.accounts?.[0]?.d1AnalyticsAdaptiveGroups;
  if (!Array.isArray(groups)) throw new Error("La API de analíticas no devolvió d1AnalyticsAdaptiveGroups.");
  const perDatabase: DatabaseWrites[] = [];
  let globalWrites = 0;
  for (const g of groups) {
    const id = g.dimensions?.databaseId;
    const rows = typeof g.sum?.rowsWritten === "number" ? g.sum.rowsWritten : 0;
    if (!id) continue;
    perDatabase.push({ databaseId: id, rowsWritten: rows });
    globalWrites += rows;
  }
  const ownId = tenantDatabaseId(tenant);
  const own = ownId ? perDatabase.find((p) => p.databaseId === ownId) : undefined;
  return {
    tenantWrites: own ? own.rowsWritten : null,
    globalWrites,
    perDatabase: perDatabase.sort((a, b) => b.rowsWritten - a.rowsWritten),
    source: "analytics",
    fetchedAtMs: nowMs,
  };
}

/** Top de consultas por escrituras hoy (para el informe diario:
 *  "top 3 fuentes"). Agrupa por texto de query (los parámetros
 *  ligados NO se capturan: nunca llevan secretos) y suma las
 *  filas escritas en todas las bases. Levanta si no se puede
 *  leer; el llamador decide si degrada. */
export async function fetchTopWriteQueries(
  env: Env,
  nowMs: number,
  limit = 3,
  fetchFn: typeof fetch = fetch,
): Promise<Array<{ query: string; rowsWritten: number }>> {
  const token = (env.CLOUDFLARE_API_TOKEN ?? "").trim();
  const accountTag = (env.CLOUDFLARE_ACCOUNT_ID ?? "").trim();
  if (!token || !accountTag) throw new Error("Sin CLOUDFLARE_API_TOKEN o CLOUDFLARE_ACCOUNT_ID.");
  const day = utcDayKey(nowMs);
  const res = await fetchFn(CLOUDFLARE_GRAPHQL_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({
      query: `query D1TopWriteQueries($accountTag: string!, $day: Date!) {
  viewer {
    accounts(filter: { accountTag: $accountTag }) {
      d1QueriesAdaptiveGroups(
        limit: 100
        filter: { date_geq: $day, date_leq: $day }
      ) {
        sum { rowsWritten }
        dimensions { query databaseId }
      }
    }
  }
}`,
      variables: { accountTag, day },
    }),
  });
  if (!res.ok) throw new Error(`La API de analíticas de Cloudflare devolvió HTTP ${res.status}.`);
  const payload = (await res.json()) as {
    data?: { viewer?: { accounts?: Array<{ d1QueriesAdaptiveGroups?: Array<{ sum?: { rowsWritten?: number }; dimensions?: { query?: string } }> }> } };
    errors?: Array<{ message?: string }>;
  };
  if (payload.errors?.length) throw new Error("La API de analíticas devolvió errores.");
  const groups = payload.data?.viewer?.accounts?.[0]?.d1QueriesAdaptiveGroups;
  if (!Array.isArray(groups)) throw new Error("La API de analíticas no devolvió d1QueriesAdaptiveGroups.");
  const byQuery = new Map<string, number>();
  for (const g of groups) {
    const q = (g.dimensions?.query ?? "").trim();
    if (!q) continue;
    const rows = typeof g.sum?.rowsWritten === "number" ? g.sum.rowsWritten : 0;
    byQuery.set(q, (byQuery.get(q) ?? 0) + rows);
  }
  return [...byQuery.entries()]
    .map(([query, rowsWritten]) => ({ query, rowsWritten }))
    .sort((a, b) => b.rowsWritten - a.rowsWritten)
    .slice(0, Math.max(1, limit));
}

/** ¿Hubo errores 7500 (cuota D1 agotada) hoy? Se lee la
 *  dimensión `error` de d1QueriesAdaptiveGroups. Devuelve el
 *  número de consultas con error 7500 (0 = ninguna). Levanta si
 *  no se puede leer; el llamador decide si degrada. */
export async function fetchDailyQuotaErrors(
  env: Env,
  nowMs: number,
  fetchFn: typeof fetch = fetch,
): Promise<number> {
  const token = (env.CLOUDFLARE_API_TOKEN ?? "").trim();
  const accountTag = (env.CLOUDFLARE_ACCOUNT_ID ?? "").trim();
  if (!token || !accountTag) throw new Error("Sin CLOUDFLARE_API_TOKEN o CLOUDFLARE_ACCOUNT_ID.");
  const day = utcDayKey(nowMs);
  const res = await fetchFn(CLOUDFLARE_GRAPHQL_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({
      query: `query D1DailyErrors($accountTag: string!, $day: Date!) {
  viewer {
    accounts(filter: { accountTag: $accountTag }) {
      d1QueriesAdaptiveGroups(
        limit: 100
        filter: { date_geq: $day, date_leq: $day }
      ) {
        count
        dimensions { error }
      }
    }
  }
}`,
      variables: { accountTag, day },
    }),
  });
  if (!res.ok) throw new Error(`La API de analíticas de Cloudflare devolvió HTTP ${res.status}.`);
  const payload = (await res.json()) as {
    data?: { viewer?: { accounts?: Array<{ d1QueriesAdaptiveGroups?: Array<{ count?: number; dimensions?: { error?: string } }> }> } };
    errors?: Array<{ message?: string }>;
  };
  if (payload.errors?.length) throw new Error("La API de analíticas devolvió errores.");
  const groups = payload.data?.viewer?.accounts?.[0]?.d1QueriesAdaptiveGroups;
  if (!Array.isArray(groups)) throw new Error("La API de analíticas no devolvió d1QueriesAdaptiveGroups.");
  let quotaErrors = 0;
  for (const g of groups) {
    if (isD1QuotaError(g.dimensions?.error ?? "")) quotaErrors += typeof g.count === "number" ? g.count : 0;
  }
  return quotaErrors;
}

// ---------------------------------------------------------------------------
// Contador por fallback: filas de hoy en las tablas de eventos.
// ---------------------------------------------------------------------------

/** Fallback cuando la analítica no se puede leer (sin token, sin
 *  red, API caída): UNA lectura que cuenta las filas de hoy en
 *  worker_events + clip_events de ESTA base. Es una COTA INFERIOR
 *  del tenant (no cuenta jobs, clips, tokens ni settings) y NO
 *  sabe nada de las demás bases: globalWrites queda null y el
 *  nivel se decide solo por el ratio del tenant. Mejor que nada:
 *  un bucle de eventos (el escritor nº 1 medido, ~22.000/día)
 *  sí se ve aquí. Nunca levanta: si la lectura falla, el
 *  contador queda "unavailable" y el guardián es fail-open. */
export async function countTodayWritesFromEvents(
  db: D1Database,
  tenant: Tenant,
  nowMs: number,
): Promise<DailyWrites> {
  const dayStart = utcDayStartMs(nowMs);
  const row = await db
    .prepare(
      "SELECT (SELECT COUNT(*) FROM worker_events WHERE tenant_id = ? AND at >= ?) " +
        "+ (SELECT COUNT(*) FROM clip_events WHERE tenant_id = ? AND at >= ?) AS writes_today",
    )
    .bind(tenant, dayStart, tenant, dayStart)
    .first<{ writes_today: number }>();
  return {
    tenantWrites: typeof row?.writes_today === "number" ? row.writes_today : 0,
    globalWrites: null,
    perDatabase: [],
    source: "events-fallback",
    fetchedAtMs: nowMs,
  };
}

// ---------------------------------------------------------------------------
// Contador con caché en memoria (nunca en D1).
// ---------------------------------------------------------------------------

let cachedWrites: { day: string; tenant: Tenant; value: DailyWrites } | null = null;

/** Lee el consumo de hoy: analítica (cacheada 5 min en memoria)
 *  con fallback a eventos. NUNCA levanta y NUNCA escribe en D1:
 *  si todo falla, devuelve source "unavailable" con los dos
 *  contadores en null (el guardián es fail-open). */
export async function readDailyWrites(
  env: Env,
  db: D1Database,
  tenant: Tenant,
  nowMs: number,
  fetchFn: typeof fetch = fetch,
): Promise<DailyWrites> {
  const day = utcDayKey(nowMs);
  if (cachedWrites && cachedWrites.day === day && cachedWrites.tenant === tenant) {
    if (nowMs - cachedWrites.value.fetchedAtMs < BUDGET_COUNTER_CACHE_MS) return cachedWrites.value;
  }
  let value: DailyWrites;
  try {
    value = await fetchDailyWritesFromAnalytics(env, tenant, nowMs, fetchFn);
  } catch {
    try {
      value = await countTodayWritesFromEvents(db, tenant, nowMs);
    } catch {
      value = { tenantWrites: null, globalWrites: null, perDatabase: [], source: "unavailable", fetchedAtMs: nowMs };
    }
  }
  cachedWrites = { day, tenant, value };
  return value;
}

/** Limpia la caché del contador (solo tests). */
export function clearBudgetCounterCache(): void {
  cachedWrites = null;
}

/** Crea el guardián del ciclo: lee el contador (cacheado) y
 *  evalúa el nivel. NUNCA levanta: ante cualquier fallo devuelve
 *  un guardián fail-open (level "ok", source "unavailable"). */
export async function createBudgetGuard(
  env: Env,
  db: D1Database,
  tenant: Tenant,
  nowMs: number,
  fetchFn: typeof fetch = fetch,
): Promise<BudgetGuard> {
  try {
    const writes = await readDailyWrites(env, db, tenant, nowMs, fetchFn);
    return budgetGuardFromState(evaluateBudget(writes.tenantWrites, writes.globalWrites, writes.source, nowMs));
  } catch {
    return budgetGuardFromState(
      evaluateBudget(null, null, "unavailable", nowMs),
    );
  }
}

// ---------------------------------------------------------------------------
// Guardián vigente del ciclo (memoria del isolate, nunca D1).
// ---------------------------------------------------------------------------

let currentGuard: BudgetGuard | null = null;

/** Lo fija runWorkerCycle al empezar cada ciclo (un Worker = un
 *  tenant, así que un solo guardián vigente basta). Las funciones
 *  de log lo consultan cuando no reciben uno explícito: así el
 *  freno cubre TODOS los puntos de escritura no esencial sin
 *  tener que pasar el guardián por cada llamada. */
export function setCurrentBudgetGuard(guard: BudgetGuard | null): void {
  currentGuard = guard;
}

/** Guardián vigente, o null si no hay o si tiene más de 15 min
 *  sin refrescar (fail-open: un guardián viejo no frena nada). */
export function currentBudgetGuard(nowMs: number = Date.now()): BudgetGuard | null {
  if (!currentGuard) return null;
  if (nowMs - currentGuard.evaluatedAtMs > BUDGET_GUARD_MAX_AGE_MS) return null;
  return currentGuard;
}

/** Limpia el guardián vigente (solo tests). */
export function clearCurrentBudgetGuard(): void {
  currentGuard = null;
}
