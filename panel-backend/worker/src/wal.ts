// Diario de escritura previa (WAL) en R2 — B1 (2026-11-20).
//
// Cuando la cuota D1 está agotada (error 7500), publicar dependía de
// escribir en D1 y el día entero se perdía. Este diario invierte el
// orden: publicar primero, registrar después.
//
//   1. Antes de subir un clip: se añade una entrada de intención.
//   2. Se sube el clip con el token (los tokens se LEEN de D1; las
//      lecturas siguen funcionando con la cuota agotada).
//   3. Después de la subida: se añade el resultado (URL o error).
//   4. Cuando D1 vuelve a aceptar escrituras, el volcado (flushWal,
//      en el ciclo del Worker) vuelca el diario a D1 en UN solo lote
//      (db.batch) y marca los jobs como subidos.
//
// Las escrituras a R2 NO cuentan contra la cuota D1. El diario es
// solo-agregar: las claves son wal/<tenant>/<fecha>.jsonl (una línea
// JSON por entrada) y nunca se sobrescribe una entrada; el volcado
// solo añade un marcador wal/<tenant>/<fecha>.flushed.json cuando
// el lote entró entero en D1.
//
// R2 no tiene append nativo: añadir es leer-modificar-escribir
// (GET + PUT) con reintento (máx. WAL_APPEND_MAX_ATTEMPTS). Dos
// ciclos no ejecutan el mismo job (reclamo atómico scheduled →
// processing), así que dos escrituras concurrentes sobre el mismo
// archivo diario son raras (grupos distintos en paralelo); si una
// se pierde, la garantía no cae: la comprobación en plataforma y
// la idempotency_key (B2) siguen frenando el duplicado, y el
// volcado es idempotente (solo marca jobs aún no posted).
//
// El diario NUNCA tumba la publicación: appendWalEntry no lanza
// (devuelve false) y el volcado ante un 7500 se retira sin
// reintentar hasta el siguiente ciclo (el freno de A4).

import type { D1Database, Env } from "./cloudflare";
import type { Tenant } from "./auth";
import type { PanelPlatform } from "./db";
import { santiagoDayKey } from "./gate";

/** Intentos de añadir una entrada antes de rendirse (re-leyendo
 *  el archivo cada vez: otra escritura concurrente no se pisa). */
export const WAL_APPEND_MAX_ATTEMPTS = 3;

/** Prefijo de todas las claves del diario en el bucket. */
export const WAL_PREFIX = "wal";

export type WalOutcome = "posted" | "already_published" | "error";

export interface WalEntry {
  /** Versión del formato de línea (1). */
  v: 1;
  /** "intent" antes de subir; "result" después. */
  type: "intent" | "result";
  /** Instante ISO de la entrada. */
  at: string;
  tenant: Tenant;
  clip_id: number | null;
  platform: PanelPlatform | null;
  /** Id de la cuenta (api_accounts.id) que publica. */
  account_id: number | null;
  job_id: number;
  /** Clave de idempotencia del job (sha256 clip|plataforma|
   *  cuenta|día) cuando el job la trae; null en jobs legacy. */
  idempotency_key: string | null;
  /** Día America/Santiago (YYYY-MM-DD) de la subida. */
  day: string;
  /** Solo en result: qué pasó. */
  outcome?: WalOutcome;
  /** Solo en result posted/already_published. */
  platform_id?: string;
  /** Solo en result posted/already_published. */
  url?: string;
  /** Solo en result error. */
  error?: string;
}

/** Clave del diario de un tenant y un día: wal/<tenant>/<fecha>.jsonl */
export function walKey(tenant: Tenant, dayKey: string): string {
  return `${WAL_PREFIX}/${tenant}/${dayKey}.jsonl`;
}

/** Clave del marcador de volcado: existe solo si el lote entero
 *  entró en D1 (ver flushWalDay). */
export function walFlushedKey(tenant: Tenant, dayKey: string): string {
  return `${WAL_PREFIX}/${tenant}/${dayKey}.flushed.json`;
}

interface FlushedMarker {
  flushed_entries: number;
  flushed_at: string;
  posted: number;
}

async function readR2Text(env: Env, key: string): Promise<string | null> {
  try {
    const obj = await env.CLIPS_BUCKET.get(key);
    if (!obj) return null;
    const buf = await obj.arrayBuffer();
    return new TextDecoder().decode(buf);
  } catch {
    return null;
  }
}

/** Lee las entradas del diario de un día. Las líneas rotas se
 *  saltan (nunca tumban la lectura). Vacío si no hay diario. */
export async function readWalEntries(env: Env, tenant: Tenant, dayKey: string): Promise<WalEntry[]> {
  const text = await readR2Text(env, walKey(tenant, dayKey));
  if (!text) return [];
  const out: WalEntry[] = [];
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    try {
      const e = JSON.parse(t) as WalEntry;
      if (e && typeof e === "object" && (e.type === "intent" || e.type === "result")) out.push(e);
    } catch {
      // Línea rota: se salta, el resto del diario sigue valiendo.
    }
  }
  return out;
}

/** Añade una entrada al diario (solo-agregar). Nunca lanza:
 *  si R2 falla tras los reintentos, devuelve false y la
 *  publicación sigue (el registro en D1 llega por el camino
 *  normal; el diario es el respaldo, no un freno). */
export async function appendWalEntry(
  env: Env,
  tenant: Tenant,
  entry: Omit<WalEntry, "v" | "at" | "tenant"> & { at?: string },
): Promise<boolean> {
  const full: WalEntry = {
    v: 1,
    at: entry.at ?? new Date().toISOString(),
    tenant,
    ...entry,
  };
  const key = walKey(tenant, full.day);
  const line = `${JSON.stringify(full)}\n`;
  for (let attempt = 0; attempt < WAL_APPEND_MAX_ATTEMPTS; attempt++) {
    try {
      const existing = (await readR2Text(env, key)) ?? "";
      await env.CLIPS_BUCKET.put(key, existing + line, { httpMetadata: { contentType: "application/x-ndjson" } });
      return true;
    } catch {
      // Reintento re-leyendo: otra escritura concurrente no se pisa.
    }
  }
  return false;
}

/** ¿Hay en el diario un resultado posted/already_published para
 *  este clip+plataforma (o para esta idempotency_key)? Se lee
 *  ANTES de cada subida (ver checkDuplicateBeforeUpload). */
export async function walFindPosted(
  env: Env,
  tenant: Tenant,
  clipId: number,
  platform: PanelPlatform,
  dayKeys: string[],
  idempotencyKey?: string | null,
): Promise<{ platformId: string; evidenceUrl: string; idempotencyKey: string | null } | null> {
  for (const day of dayKeys) {
    const entries = await readWalEntries(env, tenant, day).catch(() => [] as WalEntry[]);
    for (const e of entries) {
      if (e.type !== "result" || (e.outcome !== "posted" && e.outcome !== "already_published")) continue;
      if (!e.url || !e.platform_id) continue;
      const byKey = idempotencyKey && e.idempotency_key && e.idempotency_key === idempotencyKey;
      const byClip = e.clip_id === clipId && e.platform === platform;
      if (byKey || byClip) {
        return { platformId: e.platform_id, evidenceUrl: e.url, idempotencyKey: e.idempotency_key };
      }
    }
  }
  return null;
}

/** ¿Es un error de cuota D1 agotada (7500)? El volcado y el ciclo
 *  lo usan para retirarse sin reintentar: ante un 7500 no se
 *  insiste hasta el siguiente ciclo (freno de A4). Re-exporta la
 *  única fuente (retry.ts, A4): no duplicar el literal 7500 aquí.
 *  El texto exacto del error D1 no se pudo verificar sin escribir
 *  en D1 (cuota agotada al implementar esto): se casa por el código
 *  7500 y por las palabras límite/cuota como respaldo. (El import
 *  local es necesario: el re-export solo no crea binding y
 *  flushWalDay usa el nombre directamente.) */
import { isD1QuotaError } from "./retry";
export { isD1QuotaError };

export interface FlushResult {
  day: string;
  /** Jobs marcados como posted en este volcado. */
  flushed: number;
  /** true si no se hizo nada porque el marcador ya cubre el diario. */
  skipped: boolean;
  /** true si D1 devolvió 7500: no se reintentó; toca de nuevo en
   *  el siguiente ciclo. */
  deferred: boolean;
  reason?: string;
}

/** Vuelca UN día del diario a D1 en un solo lote (db.batch).
 *  Solo se reconcilian los resultados posted/already_published:
 *  cada uno marca su job como posted con platform_id y
 *  evidence_url (el UPDATE solo toca jobs aún no posted, así que
 *  re-volcar es inocuo). Los errores quedan en el diario como
 *  auditoría: NO se marcan failed aquí — el ciclo normal ya los
 *  marca (con su backoff) cuando D1 vuelve, y un failed terminal
 *  desde el diario mataría reintentos legítimos.
 *  Si el lote falla con 7500, se retira SIN reintentar y SIN
 *  escribir el marcador: el siguiente ciclo lo intenta de nuevo.
 *  Si el lote entra, se escribe el marcador con el nº de entradas
 *  volcadas (R2, no D1). */
export async function flushWalDay(env: Env, tenant: Tenant, dayKey: string): Promise<FlushResult> {
  const entries = await readWalEntries(env, tenant, dayKey);
  const markerText = await readR2Text(env, walFlushedKey(tenant, dayKey));
  if (markerText) {
    try {
      const marker = JSON.parse(markerText) as FlushedMarker;
      if (typeof marker.flushed_entries === "number" && marker.flushed_entries >= entries.length) {
        return { day: dayKey, flushed: 0, skipped: true, deferred: false };
      }
    } catch {
      // Marcador roto: se vuelve a volcar (los UPDATEs son idempotentes).
    }
  }
  // Un UPDATE por job distinto con resultado posted (el primero
  // que llegó manda: un job no se publica dos veces el mismo día).
  const byJob = new Map<number, WalEntry>();
  for (const e of entries) {
    if (e.type !== "result" || (e.outcome !== "posted" && e.outcome !== "already_published")) continue;
    if (!e.url || !e.platform_id) continue;
    if (!byJob.has(e.job_id)) byJob.set(e.job_id, e);
  }
  if (byJob.size === 0) {
    // Nada que volcar: se marca igual para no releer el diario
    // en cada ciclo hasta que lleguen entradas nuevas.
    await env.CLIPS_BUCKET.put(
      walFlushedKey(tenant, dayKey),
      JSON.stringify({ flushed_entries: entries.length, flushed_at: new Date().toISOString(), posted: 0 } satisfies FlushedMarker),
      { httpMetadata: { contentType: "application/json" } },
    ).catch(() => undefined);
    return { day: dayKey, flushed: 0, skipped: false, deferred: false };
  }
  const db: D1Database = env.DB;
  const now = Date.now();
  const stmts = [...byJob.values()].map((e) =>
    db
      .prepare(
        "UPDATE jobs SET state = 'posted', platform_id = ?, evidence_url = ?, posted_day = ?, wal_reconciled = 1, updated_at = ? " +
          "WHERE id = ? AND tenant_id = ? AND state IN ('scheduled','processing','draft')",
      )
      .bind(e.platform_id ?? null, e.url ?? null, dayKey, now, e.job_id, tenant),
  );
  try {
    await db.batch(stmts);
  } catch (e) {
    if (isD1QuotaError(e)) {
      // Freno de A4: retirarse sin reintentar hasta el siguiente ciclo.
      return { day: dayKey, flushed: 0, skipped: false, deferred: true, reason: e instanceof Error ? e.message : String(e) };
    }
    throw e;
  }
  await env.CLIPS_BUCKET.put(
    walFlushedKey(tenant, dayKey),
    JSON.stringify({ flushed_entries: entries.length, flushed_at: new Date().toISOString(), posted: byJob.size } satisfies FlushedMarker),
    { httpMetadata: { contentType: "application/json" } },
  ).catch(() => undefined);
  return { day: dayKey, flushed: byJob.size, skipped: false, deferred: false };
}

/** Volcado del diario en el ciclo del Worker: hoy y ayer
 *  (America/Santiago; una subida cerca de medianoche puede caer
 *  en el archivo del día anterior). Nunca lanza: cualquier fallo
 *  que no sea 7500 queda para el ciclo siguiente sin tumbar la cola. */
export async function flushWal(env: Env, tenant: Tenant, now = new Date()): Promise<FlushResult[]> {
  const today = santiagoDayKey(now);
  const yesterday = santiagoDayKey(new Date(now.getTime() - 24 * 3600 * 1000));
  const days = yesterday === today ? [today] : [yesterday, today];
  const out: FlushResult[] = [];
  for (const day of days) {
    try {
      out.push(await flushWalDay(env, tenant, day));
    } catch {
      out.push({ day, flushed: 0, skipped: false, deferred: false, reason: "volcado falló (no 7500); se reintenta en el siguiente ciclo" });
    }
  }
  return out;
}

/** ¿Hay algo pendiente de volcar en el diario? (A3: la puerta del
 *  ciclo la usa para decidir si el ciclo debe correr aunque no haya
 *  jobs vencidos ni toque revisión.)
 *  Solo lecturas R2: hay pendiente si hoy o ayer hay entradas y el
 *  marcador de volcado no las cubre (sin marcador, con menos
 *  entradas volcadas que las que hay, o con el marcador roto).
 *  Nunca lanza: ante cualquier fallo, false (el ciclo lo reintentará
 *  en su revisión periódica; el volcado nunca se pierde, solo se
 *  retrasa como mucho 12 min). */
export async function walHasPending(env: Env, tenant: Tenant, now = new Date()): Promise<boolean> {
  try {
    const today = santiagoDayKey(now);
    const yesterday = santiagoDayKey(new Date(now.getTime() - 24 * 3600 * 1000));
    for (const day of new Set([yesterday, today])) {
      const entries = await readWalEntries(env, tenant, day);
      if (entries.length === 0) continue;
      const markerText = await readR2Text(env, walFlushedKey(tenant, day));
      if (!markerText) return true;
      try {
        const marker = JSON.parse(markerText) as FlushedMarker;
        if (typeof marker.flushed_entries !== "number" || marker.flushed_entries < entries.length) return true;
      } catch {
        return true;
      }
    }
    return false;
  } catch {
    return false;
  }
}
