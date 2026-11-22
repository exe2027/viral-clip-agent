// Retención 24 h del MP4 en R2 tras publicar en todas (orden del
// usuario 2026-11-21 21:28; resuelve la excepción del borrado
// 2026-11-20 10:05 "BORRADO TRAS PUBLICAR EN TODAS").
//
// Regla:
// - Al completarse TODAS las plataformas de un clip (sin jobs de
//   publicación pendientes y al menos una publicada), el MP4 se
//   guarda 24 h en estado "subidos": clips.r2_delete_after =
//   instante en que vence la retención. El plazo se fija UNA vez
//   (nunca se reinicia: el UPDATE exige r2_delete_after IS NULL).
// - Vencido el plazo, el ciclo borra el MP4 de R2 (SOLO el
//   archivo; D1 conserva registro, links y metadatos) y marca
//   clips.file_deleted=1 (columna que ya existía desde 0002).
// - Si hay un job de publicación pendiente para ese clip
//   (incluido un |repubN por takedown esperando re-subida), NO se
//   borra: el MP4 se conserva para el re-upload. La comprobación
//   se repite justo antes de borrar, por si llegó un repub entre
//   el listado y el borrado.
// - El borrado NUNCA toca la fila del clip, sus jobs ni sus links.
//
// La fuente de "dónde fue subido / dónde falta" son los jobs
// (state + evidence_url por clip+plataforma): no se duplica en
// otra tabla, se expone por API (GET /api/clips/:id) con el
// desglose por plataforma.
//
// Defensa (patrón del repo): las bases sin la columna 0039
// (tests mínimos, bases viejas) degradan a "retención no
// disponible" en vez de tumbar el ciclo o el posted.

import type { D1Database, Env } from "./cloudflare";
import type { Tenant } from "./auth";
import { getClip, logWorkerEvent, parsePlatforms, type ClipRow, type PanelPlatform } from "./db";

/** Retención del MP4 en R2 tras completar todas las plataformas: 24 h. */
export const R2_RETENTION_MS = 24 * 60 * 60 * 1000;

/** Estados de job que cuentan como "pendiente de publicar" (falta
 *  subirse, o esperando re-subida tras un takedown). */
const PENDING_JOB_STATES = ["scheduled", "processing", "draft", "awaiting_captcha"];

export type PublishBreakdownEntry = {
  platform: PanelPlatform;
  /** posted = subido (con evidence_url); pending = falta subirse;
   *  failed = fallido terminal; not_enqueued = sin job. */
  state: "posted" | "pending" | "failed" | "not_enqueued";
  evidence_url: string | null;
  /** Estado del job vigente (el pendiente si lo hay). */
  job_state: string | null;
  /** true = ya se subió en esta plataforma Y hay otro job
   *  pendiente para ella: takedown esperando re-subida. */
  awaiting_reupload: boolean;
};

export type ClipRetention = {
  /** subidos = completo y MP4 en R2 (en retención 24 h);
   *  borrado = el MP4 ya se borró de R2 (retención vencida);
   *  sin_mp4 = el clip no tiene MP4 en R2; en_cola = aún no
   *  completó todas sus plataformas. */
  state: "subidos" | "borrado" | "sin_mp4" | "en_cola";
  /** Instante (epoch-ms) en que vence la retención; null = sin plazo. */
  delete_after: number | null;
  /** true = algún video bajado esperando re-subida. */
  awaiting_reupload: boolean;
  uploaded: PanelPlatform[];
  pending: PanelPlatform[];
};

export interface RetentionSweepResult {
  checked: number;
  deleted: number;
  skipped: number;
  errors: string[];
}

/** Desglose por plataforma de un clip, desde sus jobs de
 *  publicación (la única fuente: state + evidence_url). */
export async function clipPublishBreakdown(db: D1Database, clipId: number, tenant: Tenant): Promise<PublishBreakdownEntry[]> {
  try {
    const clip = await getClip(db, clipId, tenant);
    if (!clip) return [];
    const platforms = parsePlatforms(clip.platforms);
    if (platforms.length === 0) return [];
    const res = await db
      .prepare(
        "SELECT platform, state, evidence_url, updated_at FROM jobs " +
          "WHERE tenant_id = ? AND clip_id = ? AND kind = 'publish' ORDER BY updated_at ASC",
      )
      .bind(tenant, clipId)
      .all<{ platform: string; state: string; evidence_url: string | null; updated_at: number }>();
    const jobs = res.results ?? [];
    return platforms.map((platform) => {
      const mine = jobs.filter((j) => j.platform === platform);
      const posted = mine.filter((j) => j.state === "posted");
      const pending = mine.filter((j) => PENDING_JOB_STATES.includes(j.state));
      const failed = mine.filter((j) => j.state === "failed");
      const lastPosted = posted.at(-1) ?? null;
      const state: PublishBreakdownEntry["state"] =
        posted.length > 0 ? "posted" : pending.length > 0 ? "pending" : failed.length > 0 ? "failed" : "not_enqueued";
      return {
        platform,
        state,
        evidence_url: lastPosted?.evidence_url ?? null,
        job_state: pending.at(0)?.state ?? (posted.length > 0 ? "posted" : failed.length > 0 ? "failed" : null),
        awaiting_reupload: posted.length > 0 && pending.length > 0,
      };
    });
  } catch {
    return [];
  }
}

/** Resumen de retención de un clip (puro): estado "subidos" y
 *  dónde se subió / dónde falta, desde la fila + el desglose. */
export function clipRetentionSummary(clip: ClipRow, breakdown: PublishBreakdownEntry[]): ClipRetention {
  const uploaded = breakdown.filter((b) => b.state === "posted").map((b) => b.platform);
  const pending = breakdown.filter((b) => b.state === "pending").map((b) => b.platform);
  const awaiting_reupload = breakdown.some((b) => b.awaiting_reupload);
  const delete_after = typeof clip.r2_delete_after === "number" ? clip.r2_delete_after : null;
  if (clip.file_deleted === 1) return { state: "borrado", delete_after, awaiting_reupload, uploaded, pending };
  if (!clip.video_r2_key) return { state: "sin_mp4", delete_after, awaiting_reupload, uploaded, pending };
  if (clip.status === "publicado") return { state: "subidos", delete_after, awaiting_reupload, uploaded, pending };
  return { state: "en_cola", delete_after, awaiting_reupload, uploaded, pending };
}

/** Fija el plazo de borrado (ahora + 24 h) si el clip ya completó
 *  TODAS sus plataformas: al menos un job posted y NINGÚN job de
 *  publicación pendiente. Solo si aún no tiene plazo (nunca se
 *  reinicia) y el MP4 sigue en R2. Devuelve true si lo fijó. */
export async function stampRetentionDeadline(db: D1Database, clipId: number, tenant: Tenant, now = Date.now()): Promise<boolean> {
  try {
    const clip = await getClip(db, clipId, tenant);
    if (!clip || clip.file_deleted === 1 || !clip.video_r2_key) return false;
    if (typeof clip.r2_delete_after === "number" && clip.r2_delete_after > 0) return false;
    const counts = await db
      .prepare(
        "SELECT " +
          "SUM(CASE WHEN state = 'posted' THEN 1 ELSE 0 END) AS posted, " +
          "SUM(CASE WHEN state IN ('scheduled','processing','draft','awaiting_captcha') THEN 1 ELSE 0 END) AS pending " +
          "FROM jobs WHERE tenant_id = ? AND clip_id = ? AND kind = 'publish'",
      )
      .bind(tenant, clipId)
      .first<{ posted: number | null; pending: number | null }>();
    if (!counts || (counts.posted ?? 0) < 1 || (counts.pending ?? 0) > 0) return false;
    const res = await db
      .prepare(
        "UPDATE clips SET r2_delete_after = ?, updated_at = ? " +
          "WHERE id = ? AND tenant_id = ? AND r2_delete_after IS NULL AND file_deleted = 0",
      )
      .bind(now + R2_RETENTION_MS, now, clipId, tenant)
      .run();
    return (res.meta?.changes ?? 0) > 0;
  } catch {
    // Base sin la columna 0039 (o tabla mínima de test): sin retención.
    return false;
  }
}

/** Relleno para completados que no pasaron por markPosted (volcado
 *  del diario WAL y reconciliaciones manuales, que marcan posted
 *  con UPDATE directo): fija el plazo a los clips con
 *  status='publicado' que aún no lo tienen. Devuelve cuántos fijó. */
export async function backfillRetentionDeadlines(db: D1Database, tenant: Tenant, now = Date.now()): Promise<number> {
  let stamped = 0;
  try {
    const rows = await db
      .prepare(
        "SELECT id FROM clips WHERE tenant_id = ? AND archived = 0 AND status = 'publicado' " +
          "AND file_deleted = 0 AND video_r2_key IS NOT NULL AND video_r2_key <> '' " +
          "AND r2_delete_after IS NULL ORDER BY published_at DESC LIMIT 20",
      )
      .bind(tenant)
      .all<{ id: number }>();
    for (const row of rows.results ?? []) {
      if (await stampRetentionDeadline(db, row.id, tenant, now)) stamped++;
    }
  } catch {
    // Sin columna 0039: nada que rellenar.
  }
  return stamped;
}

/** Borra de R2 los MP4 con la retención vencida y SIN ningún job de
 *  publicación pendiente (un repub pendiente = takedown esperando
 *  re-subida → se conserva). Solo borra el archivo y marca
 *  file_deleted=1; nunca toca la fila del clip, sus jobs ni sus
 *  links. Un evento de Worker resume el barrido cuando borra algo. */
export async function sweepExpiredRetentions(
  env: Pick<Env, "DB" | "CLIPS_BUCKET">,
  tenant: Tenant,
  now = Date.now(),
): Promise<RetentionSweepResult> {
  const result: RetentionSweepResult = { checked: 0, deleted: 0, skipped: 0, errors: [] };
  let rows: Array<{ id: number; video_r2_key: string }> = [];
  try {
    const res = await env.DB.prepare(
      "SELECT id, video_r2_key FROM clips WHERE tenant_id = ? AND archived = 0 AND file_deleted = 0 " +
        "AND r2_delete_after IS NOT NULL AND r2_delete_after <= ? ORDER BY r2_delete_after ASC LIMIT 20",
    )
      .bind(tenant, now)
      .all<{ id: number; video_r2_key: string }>();
    rows = res.results ?? [];
  } catch (e) {
    result.errors.push(`listado de retenciones vencidas: ${e instanceof Error ? e.message : String(e)}`);
    return result;
  }
  result.checked = rows.length;
  for (const row of rows) {
    try {
      // Re-chequeo justo antes de borrar: un repub que llegó
      // después del listado (takedown) retiene el MP4.
      const pending = await env.DB.prepare(
        "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND clip_id = ? AND kind = 'publish' " +
          "AND state IN ('scheduled','processing','draft','awaiting_captcha')",
      )
        .bind(tenant, row.id)
        .first<{ n: number }>();
      if ((pending?.n ?? 0) > 0) {
        result.skipped++;
        continue;
      }
      // R2 delete es idempotente: si el objeto ya no está, no falla.
      await env.CLIPS_BUCKET.delete(row.video_r2_key);
      await env.DB.prepare(
        "UPDATE clips SET file_deleted = 1, updated_at = ? WHERE id = ? AND tenant_id = ? AND file_deleted = 0",
      )
        .bind(now, row.id, tenant)
        .run();
      result.deleted++;
    } catch (e) {
      result.errors.push(`clip ${row.id}: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  if (result.deleted > 0) {
    await logWorkerEvent(
      env.DB,
      "r2_cleanup",
      `Retención 24 h R2: ${result.deleted} MP4 borrado(s) de R2 (registro y links conservados en D1)` +
        (result.skipped > 0 ? `, ${result.skipped} retenido(s) por re-publicación pendiente` : "") +
        ".",
      tenant,
    ).catch(() => undefined);
  }
  return result;
}
