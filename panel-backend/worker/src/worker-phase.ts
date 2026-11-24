// Fase en vivo del cerebro (2026-11-19, corrección del usuario en
// el chat principal): el estado del Worker tiene que mostrar en
// tiempo real qué está haciendo AHORA el cerebro, no solo un piloto
// verde o rojo. El cerebro reporta su fase a POST /api/worker/phase
// en cada etapa (detect: buscando_picos / sin_directos; cut:
// cortando_clip; fallback: modo_vod). "Cargando" es el estado del
// panel mientras pide el estado, no una fase del cerebro.
//
// ATASCADO sigue siendo SOLO el veredicto del validador
// (worker-validate.ts): hay directos activos en GTA VI y aun así no
// llega ningún clip en 60 min. La fase nunca cambia ese veredicto:
// solo dice qué está pasando. Sin fase reciente no se inventa una:
// se muestra la última con su edad, y si tiene más de 20 min se
// marca desactualizada (un ciclo del cerebro son 15 min).
import type { D1Database } from "./cloudflare";
import type { Tenant } from "./auth";
import { logWorkerEvent } from "./db";

export const BRAIN_PHASES = ["buscando_picos", "cortando_clip", "modo_vod", "sin_directos"] as const;
export type BrainPhase = (typeof BRAIN_PHASES)[number];

export const PHASE_LABELS: Record<BrainPhase, string> = {
  buscando_picos: "Buscando picos",
  cortando_clip: "Cortando clip",
  modo_vod: "Modo VOD",
  sin_directos: "Sin directos",
};

/** Un ciclo del cerebro son 15 min, pero una corrida VOD procesa
 *  2 VODs de ~8 min cada uno: un reporte más viejo de 30 min es
 *  desactualizado (el cerebro dejó de reportar o la corrida murió). */
export const PHASE_STALE_MS = 30 * 60 * 1000;
/** Cadencia del cerebro (cron cada 15 min + dispatch del Worker). */
export const BRAIN_CYCLE_MS = 15 * 60 * 1000;

export function parseBrainPhase(v: unknown): BrainPhase | null {
  if (typeof v !== "string") return null;
  const key = v.trim();
  return (BRAIN_PHASES as readonly string[]).includes(key) ? (key as BrainPhase) : null;
}

export function phaseLabel(phase: BrainPhase | null): string {
  return phase ? PHASE_LABELS[phase] : "Sin señal del cerebro";
}

export interface WorkerPhaseRow {
  phase: BrainPhase;
  detail: string;
  run_id: string;
  updated_at: number;
}

export interface WorkerPhaseView extends WorkerPhaseRow {
  label: string;
  ageMin: number;
  stale: boolean;
}

export function phaseView(row: WorkerPhaseRow | null, nowMs: number): WorkerPhaseView | null {
  if (!row) return null;
  const ageMin = Math.max(0, Math.round((nowMs - row.updated_at) / 60_000));
  return { ...row, label: phaseLabel(row.phase), ageMin, stale: nowMs - row.updated_at > PHASE_STALE_MS };
}

export async function getWorkerPhase(db: D1Database, tenant: Tenant): Promise<WorkerPhaseRow | null> {
  try {
    const row = await db
      .prepare("SELECT phase, detail, run_id, updated_at FROM worker_phases WHERE tenant_id = ?")
      .bind(tenant)
      .first<{ phase: string; detail: string; run_id: string; updated_at: number }>();
    if (!row) return null;
    const phase = parseBrainPhase(row.phase);
    if (!phase) return null;
    return { phase, detail: row.detail ?? "", run_id: row.run_id ?? "", updated_at: row.updated_at };
  } catch {
    return null;
  }
}

export async function setWorkerPhase(
  db: D1Database,
  tenant: Tenant,
  phase: BrainPhase,
  detail: string,
  runId: string,
  nowMs: number,
): Promise<void> {
  await db
    .prepare(
      "INSERT INTO worker_phases (tenant_id, phase, detail, run_id, updated_at) VALUES (?, ?, ?, ?, ?) " +
        "ON CONFLICT(tenant_id) DO UPDATE SET phase = excluded.phase, detail = excluded.detail, " +
        "run_id = excluded.run_id, updated_at = excluded.updated_at",
    )
    .bind(tenant, phase, detail, runId, nowMs)
    .run();
}

/** Próxima corrida del cerebro: último barrido + 15 min. null si
 *  nunca hubo un barrido (el Worker aún no disparó al cerebro). */
export function nextBrainRunAt(lastSweepAt: number | null): number | null {
  return lastSweepAt === null || lastSweepAt === undefined ? null : lastSweepAt + BRAIN_CYCLE_MS;
}

/** POST /api/worker/phase: valida el cuerpo y guarda la fase.
 *  Devuelve { status, body } para que index.ts solo lo reenvíe.
 *  Un fallo de D1 es un 500 con motivo, nunca una excepción. */
export async function handleReportPhase(
  db: D1Database,
  tenant: Tenant,
  body: Record<string, unknown> | null | undefined,
  nowMs: number,
): Promise<{ status: number; body: Record<string, unknown> }> {
  const phase = parseBrainPhase(body?.phase);
  if (!phase) {
    return {
      status: 400,
      body: { ok: false, error: `Fase no válida. Usa una de: ${BRAIN_PHASES.join(", ")}.` },
    };
  }
  const detail = typeof body?.detail === "string" ? body.detail.slice(0, 200) : "";
  const runId = typeof body?.run_id === "string" ? body.run_id.slice(0, 64) : "";
  try {
    await setWorkerPhase(db, tenant, phase, detail, runId, nowMs);
  } catch (e) {
    return {
      status: 500,
      body: { ok: false, error: `No se pudo guardar la fase: ${e instanceof Error ? e.message : String(e)}` },
    };
  }
  return { status: 200, body: { ok: true, phase, label: phaseLabel(phase) } };
}

/** Eventos de clip del cerebro (2026-11-19, visibilidad): cada clip
 *  cortado / encolado / descartado llega al panel con su motivo, y
 *  aparece en Actividad reciente. Sin esto un clip cortado que no
 *  se encola es invisible: el usuario no sabe dónde se perdió. */
export const BRAIN_CLIP_EVENT_KINDS = ["clip_cortado", "clip_encolado", "clip_descartado", "corte_fallido", "revalidado"] as const;
export type BrainClipEventKind = (typeof BRAIN_CLIP_EVENT_KINDS)[number];

export function parseBrainClipEventKind(v: unknown): BrainClipEventKind | null {
  if (typeof v !== "string") return null;
  const key = v.trim();
  return (BRAIN_CLIP_EVENT_KINDS as readonly string[]).includes(key) ? (key as BrainClipEventKind) : null;
}

/** POST /api/worker/event: valida el cuerpo y guarda el evento en
 *  worker_events. Devuelve { status, body } para que index.ts solo
 *  lo reenvíe. Un fallo de D1 es un 500 con motivo, nunca una excepción. */
export async function handleReportClipEvent(
  db: D1Database,
  tenant: Tenant,
  body: Record<string, unknown> | null | undefined,
  nowMs: number,
): Promise<{ status: number; body: Record<string, unknown> }> {
  const kind = parseBrainClipEventKind(body?.kind);
  if (!kind) {
    return {
      status: 400,
      body: { ok: false, error: `Tipo no válido. Usa uno de: ${BRAIN_CLIP_EVENT_KINDS.join(", ")}.` },
    };
  }
  const message = typeof body?.message === "string" ? body.message.trim() : "";
  if (!message) {
    return { status: 400, body: { ok: false, error: "message no puede estar vacío." } };
  }
  try {
    // A2: este era el ÚNICO INSERT directo en worker_events fuera de
    // db.ts y se saltaba la dedupe: el cerebro reportando el mismo
    // descarte cada 15 min dejaba una fila por reporte. Ahora pasa
    // por logWorkerEvent (dedupe 6 h + contador agregado). El recorte
    // a 500 caracteres se mantiene.
    await logWorkerEvent(db, kind, message.slice(0, 500), tenant, { nowMsValue: nowMs });
  } catch (e) {
    return {
      status: 500,
      body: { ok: false, error: `No se pudo guardar el evento: ${e instanceof Error ? e.message : String(e)}` },
    };
  }
  return { status: 200, body: { ok: true, kind } };
}
