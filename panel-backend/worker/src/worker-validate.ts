// Validador del Worker: ¿de verdad está sacando clips y dejándolos
// en la Cola, o solo dice "en marcha"?
//
// Motivo (2026-11-18, dicho por el usuario): hay 10 streamers en
// español en el panel y 8 más en inglés en el cerebro (18 en total,
// streamers.yaml), así que es casi imposible que no haya un live o
// un VOD. Un Worker con running=1 y la Cola vacía durante horas no
// es "sin material": es un atasco, y hay que decirlo con el motivo
// exacto en vez de dejar el piloto verde encendido.
//
// Flujo constante (2026-11-18 23:01, corrección del usuario: "esos
// 3 clips los sacó hace rato, debería estar constantemente sacando
// clips"): tener clips viejos en Cola NO es estar al día. El cerebro
// corre cada 15 min y cada corrida dura ~13-25 min, así que 60 min
// sin un clip nuevo son 3-4 corridas sin nada, con streamers en
// vivo: atascado. La Cola vieja no tapa el estancamiento: el motivo
// dice cuántos hay en Cola y hace cuánto llegó el último.
//
// Lo que mira (solo D1, sin inventar nada):
//   - worker_state.running / last_live_dispatch_at (el dispatch
//     en vivo al cerebro toca cada 15 min cuando está en marcha)
//   - influencers activos con permiso en el panel (ES; el cerebro
//     tiene además los 8 EN, por eso la oferta real es aún mayor)
//   - clips en Cola (status en_carpeta/aprobado/programado, no
//     archivados) y último clip creado (MAX created_at)
//   - jobs fallidos en las últimas 24 h (señal, no veredicto)
//   - directos ES en GTA VI AHORA (solo cuando haría falta para
//     decidir un atasco: Twitch Helix + Kick, ver live-check.ts).
//     Sin directos ES no hay atasco aunque la Cola lleve horas sin
//     clips: el cerebro no puede sacar material de donde no hay.
//
// Estados:
//   - stopped: parado a propósito, la Cola queda en pausa (no es fallo)
//   - blocked: en marcha pero sin streamers activos con permiso
//   - no_live_es: en marcha, sin clips nuevos en 60 min, pero
//     NINGÚN streamer ES está en directo en GTA VI ahora mismo:
//     no hay material en vivo que vigilar, así que no es un atasco,
//     es espera. El motivo dice el último barrido y la próxima
//     corrida. Nunca dispara la alerta de atascado.
//   - stuck: en marcha, con directos ES en GTA VI, y sin clips
//     nuevos en 60 min, o sin dispatch en vivo en 2 h cuando
//     tampoco hay clips recientes. Tener clips viejos en Cola NO
//     salva: si el último llegó hace más de 60 min, el flujo está
//     parado aunque la Cola no esté vacía.
//   - ok: llegó un clip en los últimos 60 min
//
// La función pura validateWorkerSupply no toca D1 (testeable sin
// base); collectWorkerSupply la alimenta desde D1 y nunca rompe al
// llamador: si una consulta falla, ese dato queda en null/0 y el
// motivo lo dice.
import type { D1Database } from "./cloudflare";
import type { Tenant } from "./auth";
import { countEsLiveGta6 } from "./live-check";

/** Sin clips nuevos en la Cola en 60 min con el Worker en marcha:
 *  atascado. El cerebro corre cada 15 min; 60 min son 3-4 corridas
 *  sin nada, con streamers en vivo. (Antes 6 h: con 3 clips viejos
 *  en Cola decía ok aunque llevara horas sin sacar uno nuevo.) */
export const NO_NEW_CLIP_STUCK_MS = 60 * 60 * 1000;
/** Sin dispatch en vivo al cerebro en 2 h (toca cada 15 min): atascado. */
export const NO_LIVE_DISPATCH_STUCK_MS = 2 * 60 * 60 * 1000;
/** La alerta de atascado por Telegram se repite como mucho 1 vez cada 6 h. */
export const STUCK_ALERT_MIN_INTERVAL_MS = 6 * 60 * 60 * 1000;

export type WorkerSupplyStatus = "ok" | "stopped" | "blocked" | "stuck" | "no_live_es";

export interface WorkerSupplyInput {
  running: boolean;
  startedAt: number | null;
  lastLiveDispatchAt: number | null;
  /** Streamers activos con permiso en el panel (ES, los del lanzamiento). */
  activeInfluencers: number;
  activeEs: number;
  activeEn: number;
  /** Streamers ES en directo EN GTA VI ahora mismo (null = no se
   *  pudo comprobar: sin credencial de Twitch, API caída...). Solo
   *  se consulta cuando haría falta para decidir un atasco, nunca
   *  en el camino ok/parado/bloqueado. */
  liveEsGta6: number | null;
  /** Clips en Cola (en_carpeta/aprobado/programado, no archivados). */
  queueCount: number;
  /** MAX(created_at) de clips no archivados, o null si nunca hubo uno. */
  lastClipAt: number | null;
  /** Jobs en failed en las últimas 24 h (señal, no veredicto). */
  failedJobs24h: number;
}

export interface WorkerValidation extends WorkerSupplyInput {
  status: WorkerSupplyStatus;
  reason: string;
  checkedAt: number;
  hoursSinceLastClip: number | null;
  hoursSinceLastDispatch: number | null;
}

function hoursBetween(now: number, at: number | null): number | null {
  if (at === null || at === undefined) return null;
  return Math.round(((now - at) / 3_600_000) * 10) / 10;
}

export function validateWorkerSupply(input: WorkerSupplyInput, nowMs: number): WorkerValidation {
  const hoursSinceLastClip = hoursBetween(nowMs, input.lastClipAt);
  const hoursSinceLastDispatch = hoursBetween(nowMs, input.lastLiveDispatchAt);
  const base: WorkerValidation = {
    ...input,
    status: "ok",
    reason: "",
    checkedAt: nowMs,
    hoursSinceLastClip,
    hoursSinceLastDispatch,
  };
  if (!input.running) {
    return { ...base, status: "stopped", reason: "Worker parado: la Cola queda en pausa (nada se pierde)." };
  }
  if (input.activeInfluencers === 0) {
    return {
      ...base,
      status: "blocked",
      reason: "Worker en marcha pero sin streamers activos con permiso: no hay a quién vigilar, así que no puede llegar ningún clip.",
    };
  }
  if (input.queueCount > 0 && input.lastClipAt !== null && nowMs - input.lastClipAt <= NO_NEW_CLIP_STUCK_MS) {
    return {
      ...base,
      status: "ok",
      reason: `Hay ${input.queueCount} clip(s) en Cola y el último llegó hace ${hoursSinceLastClip} h: la Cola se está alimentando.`,
    };
  }
  if (input.lastClipAt !== null && nowMs - input.lastClipAt <= NO_NEW_CLIP_STUCK_MS) {
    return {
      ...base,
      status: "ok",
      reason: `Último clip hace ${hoursSinceLastClip} h: la Cola se está alimentando.`,
    };
  }
  // Cola con clips viejos o vacía, sin clips recientes: el dispatch
  // en vivo es la primera sospecha (toca cada 15 min; 2 h son 8
  // disparos perdidos). Un dispatch parado es un fallo real aunque
  // no haya directos: el barrido tiene que tocar igual.
  if (input.lastLiveDispatchAt !== null && nowMs - input.lastLiveDispatchAt > NO_LIVE_DISPATCH_STUCK_MS) {
    return {
      ...base,
      status: "stuck",
      reason:
        `Atascado: sin clips nuevos en ${hoursSinceLastClip ?? "—"} h ` +
        (input.queueCount > 0 ? `(hay ${input.queueCount} en Cola, pero el último llegó hace ${hoursSinceLastClip} h) ` : `(Cola vacía) `) +
        `y sin dispatch en vivo al cerebro en ${hoursSinceLastDispatch} h ` +
        `(toca cada 15 min). El cerebro no está siendo llamado a barrer los directos, ` +
        `así que no puede llegar ningún clip aunque haya streamers en vivo.`,
    };
  }
  // Sin directos ES en GTA VI ahora mismo (2026-11-19, falsa alarma
  // de las 02:00: el cerebro corría en verde cada 15 min, todos los
  // ES offline, los picos EN se descartaron bien por debajo de los
  // umbrales, y la Cola llevaba 60+ min sin clips → el piloto decía
  // ATASCADO sin estarlo). Sin material en vivo no hay atasco: es
  // espera. El motivo dice el último barrido y la próxima corrida.
  // liveEsGta6 null = no se pudo comprobar (sin credencial de
  // Twitch o API caída): no se afirma que no hay directos y se
  // mantiene el veredicto anterior (stuck).
  if (input.liveEsGta6 === 0) {
    const lastSweepMin =
      input.lastLiveDispatchAt !== null ? Math.max(0, Math.round((nowMs - input.lastLiveDispatchAt) / 60_000)) : null;
    const nextRunMin = lastSweepMin !== null ? Math.max(0, 15 - lastSweepMin) : null;
    return {
      ...base,
      status: "no_live_es",
      reason:
        `Sin directos ES: ningún streamer en español está en directo en GTA VI ahora mismo, ` +
        `así que no hay material en vivo que vigilar. El cerebro sigue barriendo cada 15 min` +
        (lastSweepMin !== null ? ` (último barrido hace ${lastSweepMin} min, próximo en ~${nextRunMin} min)` : ``) +
        ` y avisará cuando alguien entre en directo.` +
        (input.lastClipAt !== null ? ` Último clip encolado hace ${hoursSinceLastClip} h.` : ` Todavía no llegó ningún clip.`),
    };
  }
  if (input.lastClipAt === null) {
    return {
      ...base,
      status: "stuck",
      reason:
        `Atascado: Cola vacía y nunca ha llegado un clip con ${input.activeInfluencers} streamer(s) activo(s) ` +
        `con permiso en el panel (ES ${input.activeEs} · EN ${input.activeEn}). ` +
        `Con 18 streamers entre ES y EN en el cerebro, casi imposible que no haya un live o VOD: ` +
        `revisa el dispatch al cerebro y la credencial de Twitch.`,
    };
  }
  return {
    ...base,
    status: "stuck",
    reason:
      `Atascado: sin clips nuevos en ${hoursSinceLastClip} h con ${input.activeInfluencers} streamer(s) ` +
      `activo(s) con permiso en el panel (ES ${input.activeEs} · EN ${input.activeEn})` +
      (input.queueCount > 0 ? ` (hay ${input.queueCount} en Cola, pero son viejos: el último llegó hace ${hoursSinceLastClip} h)` : ` (Cola vacía)`) +
      `. Con 18 streamers entre ES y EN en el cerebro, casi imposible que no haya un live o VOD: ` +
      `revisa el dispatch al cerebro y la credencial de Twitch.` +
      (input.failedJobs24h > 0 ? ` Hay ${input.failedJobs24h} job(s) fallidos en 24 h.` : ""),
  };
}

/** Lee de D1 todo lo que necesita validateWorkerSupply. Nunca lanza:
 *  si una consulta falla, ese dato queda en null/0 y el motivo del
 *  validador lo refleja (sin ese dato no se puede dar por bueno).
 *
 *  La comprobación de directos es PEREZOSA: solo se hace cuando el
 *  veredicto solo con D1 sería "stuck" (y el dispatch está al día:
 *  un dispatch parado es atasco sin mirar directos). Así el camino
 *  ok/parado/bloqueado no gasta llamadas a Twitch/Kick. Sin env no
 *  se puede comprobar (falta la clave de cifrado de la credencial
 *  de Twitch) y se mantiene el veredicto solo con D1. */
export interface CollectWorkerSupplyOpts {
  env?: { CREDENTIAL_ENCRYPTION_KEY: string };
  /** Inyección para tests: devuelve el conteo ES en GTA VI o null. */
  checkLive?: (db: D1Database, tenant: Tenant) => Promise<number | null>;
}

export async function collectWorkerSupply(
  db: D1Database,
  tenant: Tenant,
  nowMs: number,
  opts: CollectWorkerSupplyOpts = {},
): Promise<WorkerValidation> {
  let running = false;
  let startedAt: number | null = null;
  let lastLiveDispatchAt: number | null = null;
  try {
    const row = await db
      .prepare("SELECT running, started_at, last_live_dispatch_at FROM worker_state WHERE tenant_id = ?")
      .bind(tenant)
      .first<{ running: number; started_at: number | null; last_live_dispatch_at: number | null }>();
    running = row?.running === 1;
    startedAt = row?.started_at ?? null;
    lastLiveDispatchAt = row?.last_live_dispatch_at ?? null;
  } catch {
    // Sin worker_state no se puede afirmar que esté en marcha.
  }
  let activeEs = 0;
  let activeEn = 0;
  try {
    const res = await db
      .prepare("SELECT language FROM influencers WHERE tenant_id = ? AND active = 1 AND permission = 1")
      .bind(tenant)
      .all<{ language: string }>();
    for (const r of res.results ?? []) {
      if ((r.language ?? "es") === "en") activeEn++;
      else activeEs++;
    }
  } catch {
    // Sin lista de influencers, el validador dirá blocked (0 activos).
  }
  let queueCount = 0;
  let lastClipAt: number | null = null;
  try {
    const q = await db
      .prepare(
        "SELECT COUNT(*) AS n FROM clips WHERE tenant_id = ? AND archived = 0 AND status IN ('en_carpeta','aprobado','programado')",
      )
      .bind(tenant)
      .first<{ n: number }>();
    queueCount = q?.n ?? 0;
    const last = await db
      .prepare("SELECT MAX(created_at) AS lastClipAt FROM clips WHERE tenant_id = ? AND archived = 0")
      .bind(tenant)
      .first<{ lastClipAt: number | null }>();
    lastClipAt = last?.lastClipAt ?? null;
  } catch {
    // Sin Cola legible, queueCount 0 + lastClipAt null => stuck si corre.
  }
  let failedJobs24h = 0;
  try {
    const f = await db
      .prepare("SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND state = 'failed' AND updated_at >= ?")
      .bind(tenant, nowMs - 24 * 60 * 60 * 1000)
      .first<{ n: number }>();
    failedJobs24h = f?.n ?? 0;
  } catch {
    failedJobs24h = 0;
  }
  const input: WorkerSupplyInput = {
    running,
    startedAt,
    lastLiveDispatchAt,
    activeInfluencers: activeEs + activeEn,
    activeEs,
    activeEn,
    liveEsGta6: null,
    queueCount,
    lastClipAt,
    failedJobs24h,
  };
  const d1only = validateWorkerSupply(input, nowMs);
  if (d1only.status !== "stuck") return d1only;
  // Sería "stuck" solo con D1. Si el dispatch está parado, es atasco
  // de verdad y no hace falta mirar directos (el barrido ni toca).
  const dispatchStale =
    lastLiveDispatchAt !== null && nowMs - lastLiveDispatchAt > NO_LIVE_DISPATCH_STUCK_MS;
  if (dispatchStale) return d1only;
  // ¿Hay alguien en directo en GTA VI ahora mismo? Sin env no se
  // puede comprobar: se mantiene el veredicto solo con D1.
  let liveEsGta6: number | null = null;
  try {
    if (opts.checkLive) {
      liveEsGta6 = await opts.checkLive(db, tenant);
    } else if (opts.env) {
      liveEsGta6 = await countEsLiveGta6(db, opts.env, tenant);
    }
  } catch {
    liveEsGta6 = null;
  }
  if (liveEsGta6 === null) return d1only;
  return validateWorkerSupply({ ...input, liveEsGta6 }, nowMs);
}
