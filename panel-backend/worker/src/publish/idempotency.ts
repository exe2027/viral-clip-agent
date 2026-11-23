// Idempotencia de publicación — B2 (2026-11-20).
//
// Cero duplicados, tres patas:
//   1. idempotency_key = sha256(clip|plataforma|cuenta|día) en jobs
//      (jobs.idempotencyKey, UNIQUE desde la migración 0011): el
//      enqueue nunca crea dos jobs con la misma clave.
//   2. El diario WAL (wal.ts): antes de subir se lee; si ya hay un
//      resultado posted para este clip+plataforma (o esta clave),
//      no se sube.
//   3. La plataforma misma (SOLO LECTURA, legacy): se listan los
//      últimos vídeos de la cuenta y se busca la marca [clip:<id>]
//      en la descripción. Desde 2026-11-23 la marca YA NO se añade
//      a captions nuevos (era visible en público, p. ej. el reel
//      del clip 168 en Instagram): solo se lee para posts viejos
//      que ya la llevan. Para captions nuevos la garantía es WAL +
//      idempotency_key; sin marca en plataforma la comprobación
//      devuelve not_found y NO frena la subida.
//
// La comprobación en plataforma es una LECTURA de la API de cada
// plataforma (no escribe nada, no cuenta contra la cuota D1).
// Cobertura real (verificado contra el código de publish/):
//   - YouTube: SÍ — Data API v3 (channels.list → uploads +
//     playlistItems.list). Coste 2 unidades.
//   - Instagram: SÍ — Graph API, GET /{ig-user-id}/media.
//   - Facebook: SÍ — Graph API, GET /{page-id}/videos.
//   - TikTok: NO — el token Direct Post solo tiene video.publish
//     (video.list exige scope video.list + Login Kit + aprobación
//     Display API; ver publish/stats.ts) y Zernio no expone un
//     listado de posts en el código/docs usados (solo GET
//     /v1/posts/{id}). En TikTok la garantía recae en el diario +
//     idempotency_key.
// Si el listado falla (red, token, permiso), la comprobación
// devuelve "unavailable" y NO frena la subida: el diario + la
// clave siguen cubriendo el duplicado. Nunca se finge un "no
// existe" cuando no se pudo comprobar.

import type { Env } from "../cloudflare";
import type { PublishCreds } from "./index";
import type { JobRow } from "../jobs";
import type { PanelPlatform } from "../db";
import { refreshAccessToken } from "./youtube";
import { httpJson } from "./http";
import { walFindPosted } from "../wal";
import { santiagoDayKey } from "../gate";

/** Marca que identifica un clip en la descripción de la plataforma.
 *  LEGACY / SOLO LECTURA (2026-11-23): ya no se añade a captions
 *  nuevos (era visible en público). Se conserva para detectar
 *  posts viejos que ya la llevan (p. ej. clip 168 en Instagram).
 *  appendClipMarker se eliminó: ningún código debe volver a
 *  añadir la marca a un caption. */
export function clipMarker(clipId: number): string {
  return `[clip:${clipId}]`;
}

/** ¿Contiene el texto la marca de este clip? Solo lectura. */
export function containsClipMarker(text: string | null | undefined, clipId: number): boolean {
  return typeof text === "string" && text.includes(clipMarker(clipId));
}

export type FetchJson = (
  url: string,
  headers?: Record<string, string>,
) => Promise<{ status: number; json: Record<string, unknown> }>;

async function defaultFetchJson(url: string, headers?: Record<string, string>): Promise<{ status: number; json: Record<string, unknown> }> {
  const r = await httpJson("GET", url, headers ?? {});
  return { status: r.status, json: r.json };
}

export type PlatformCheckStatus = "found" | "not_found" | "unavailable";

export interface PlatformCheck {
  status: PlatformCheckStatus;
  platformId?: string;
  evidenceUrl?: string;
  /** Motivo cuando status es "unavailable" (no se pudo comprobar). */
  reason?: string;
}

const notFound = (): PlatformCheck => ({ status: "not_found" });
const unavailable = (reason: string): PlatformCheck => ({ status: "unavailable", reason });

function str(v: unknown): string | undefined {
  return typeof v === "string" && v ? v : undefined;
}

function itemsOf(payload: Record<string, unknown>): Record<string, unknown>[] {
  const items = payload.items ?? payload.data;
  return Array.isArray(items) ? (items as Record<string, unknown>[]) : [];
}

/** YouTube: últimos vídeos del canal (uploads) por descripción.
 *  Necesita el OAuth de subida (el mismo que usa youtubeStep). */
export async function checkYoutubeAlreadyPublished(
  creds: PublishCreds,
  clipId: number,
  fetchJson: FetchJson = defaultFetchJson,
): Promise<PlatformCheck> {
  const yt = creds.youtube;
  if (!yt) return unavailable("sin OAuth de YouTube configurado");
  let access: string | null;
  try {
    access = await refreshAccessToken(yt);
  } catch (e) {
    return unavailable(`no se pudo renovar el access token: ${e instanceof Error ? e.message : String(e)}`);
  }
  if (!access) return unavailable("no se pudo renovar el access token de YouTube");
  const auth = { Authorization: `Bearer ${access}` };
  try {
    const ch = await fetchJson(
      "https://www.googleapis.com/youtube/v3/channels?part=contentDetails&mine=true",
      auth,
    );
    if (ch.status !== 200) return unavailable(`channels.list devolvió HTTP ${ch.status}`);
    const playlistId = ((): string | undefined => {
      const items = ch.json.items as Array<Record<string, unknown>> | undefined;
      const details = items?.[0]?.contentDetails as Record<string, unknown> | undefined;
      const lists = details?.relatedPlaylists as Record<string, unknown> | undefined;
      return str(lists?.uploads);
    })();
    if (!playlistId) return unavailable("channels.list no devolvió la lista de uploads");
    const pl = await fetchJson(
      `https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&playlistId=${encodeURIComponent(playlistId)}&maxResults=25`,
      auth,
    );
    if (pl.status !== 200) return unavailable(`playlistItems.list devolvió HTTP ${pl.status}`);
    for (const item of itemsOf(pl.json)) {
      const sn = item.snippet as Record<string, unknown> | undefined;
      if (!sn) continue;
      if (containsClipMarker(str(sn.description), clipId)) {
        const videoId = str((sn.resourceId as Record<string, unknown> | undefined)?.videoId);
        if (!videoId) continue;
        return { status: "found", platformId: videoId, evidenceUrl: `https://www.youtube.com/shorts/${videoId}` };
      }
    }
    return notFound();
  } catch (e) {
    return unavailable(`error de red/API: ${e instanceof Error ? e.message : String(e)}`);
  }
}

/** Instagram: últimos media de la cuenta por caption. */
export async function checkInstagramAlreadyPublished(
  creds: PublishCreds,
  clipId: number,
  fetchJson: FetchJson = defaultFetchJson,
): Promise<PlatformCheck> {
  const token = creds.instagram;
  const igUserId = creds.igUserId;
  if (!token || !igUserId) return unavailable("sin token de Instagram o sin ig_user_id");
  try {
    const r = await fetchJson(
      `https://graph.facebook.com/v26.0/${encodeURIComponent(igUserId)}/media?fields=id,caption,permalink&limit=25&access_token=${encodeURIComponent(token)}`,
    );
    if (r.status !== 200) return unavailable(`Graph media devolvió HTTP ${r.status}`);
    for (const item of itemsOf(r.json)) {
      if (containsClipMarker(str(item.caption), clipId)) {
        const id = str(item.id);
        if (!id) continue;
        return { status: "found", platformId: id, evidenceUrl: str(item.permalink) ?? `https://www.instagram.com/reel/${id}/` };
      }
    }
    return notFound();
  } catch (e) {
    return unavailable(`error de red/API: ${e instanceof Error ? e.message : String(e)}`);
  }
}

/** Facebook: últimos vídeos de la Página por descripción. */
export async function checkFacebookAlreadyPublished(
  creds: PublishCreds,
  clipId: number,
  fetchJson: FetchJson = defaultFetchJson,
): Promise<PlatformCheck> {
  const token = creds.facebook;
  const pageId = creds.pageId;
  if (!token || !pageId) return unavailable("sin Page access token o sin page_id");
  try {
    const r = await fetchJson(
      `https://graph.facebook.com/v26.0/${encodeURIComponent(pageId)}/videos?fields=id,description,permalink_url&limit=25&access_token=${encodeURIComponent(token)}`,
    );
    if (r.status !== 200) return unavailable(`Graph videos devolvió HTTP ${r.status}`);
    for (const item of itemsOf(r.json)) {
      if (containsClipMarker(str(item.description), clipId)) {
        const id = str(item.id);
        if (!id) continue;
        return { status: "found", platformId: id, evidenceUrl: str(item.permalink_url) ?? `https://www.facebook.com/reel/${id}/` };
      }
    }
    return notFound();
  } catch (e) {
    return unavailable(`error de red/API: ${e instanceof Error ? e.message : String(e)}`);
  }
}

/** TikTok: SIN comprobación en plataforma (documentado arriba):
 *  el token Direct Post no tiene video.list y Zernio no expone
 *  listado. La garantía recae en el diario + idempotency_key. */
export function checkTiktokAlreadyPublished(): PlatformCheck {
  return unavailable(
    "TikTok no permite listar vídeos con el token actual (falta scope video.list) y Zernio no expone listado de posts: la garantía recae en el diario + idempotency_key",
  );
}

/** Comprobación en plataforma según el job. Nunca lanza. */
export async function checkPlatformAlreadyPublished(
  platform: PanelPlatform,
  creds: PublishCreds,
  clipId: number,
  fetchJson: FetchJson = defaultFetchJson,
): Promise<PlatformCheck> {
  try {
    switch (platform) {
      case "youtube_shorts":
        return await checkYoutubeAlreadyPublished(creds, clipId, fetchJson);
      case "instagram_reels":
        return await checkInstagramAlreadyPublished(creds, clipId, fetchJson);
      case "facebook_reels":
        return await checkFacebookAlreadyPublished(creds, clipId, fetchJson);
      case "tiktok":
        return checkTiktokAlreadyPublished();
    }
  } catch (e) {
    return unavailable(`error inesperado: ${e instanceof Error ? e.message : String(e)}`);
  }
}

export interface DuplicateCheck {
  duplicate: boolean;
  platformId?: string;
  evidenceUrl?: string;
  /** Dónde se encontró: diario o plataforma. */
  source: "wal" | "platform" | null;
  /** Resultado de la comprobación en plataforma (si se llegó a ella). */
  platformCheck?: PlatformCheckStatus;
  platformReason?: string;
}

/** Comprobación previa a subir (B2): diario primero, plataforma
 *  después. Se llama SOLO en el primer paso de un job fresco
 *  (sin resume_data): una subida multi-step en curso no se
 *  re-comprueba en cada chunk. Si el listado no está disponible
 *  (p. ej. TikTok), duplicate=false salvo que el diario lo
 *  encuentre: la subida sigue y el diario + la clave cubren el
 *  duplicado. Nunca lanza. */
export async function checkDuplicateBeforeUpload(
  env: Env,
  job: JobRow,
  creds: PublishCreds,
  fetchJson: FetchJson = defaultFetchJson,
  now = new Date(),
): Promise<DuplicateCheck> {
  const none: DuplicateCheck = { duplicate: false, source: null };
  if (!job.clip_id || !job.platform) return none;
  const today = santiagoDayKey(now);
  const yesterday = santiagoDayKey(new Date(now.getTime() - 24 * 3600 * 1000));
  const dayKeys = yesterday === today ? [today] : [yesterday, today];
  // 1. El diario (R2, lectura): cubre subidas cuyo registro en D1
  //    aún no llegó (cuota agotada) y cualquier plataforma.
  try {
    const found = await walFindPosted(env, job.tenant_id, job.clip_id, job.platform, dayKeys, job.idempotency_key);
    if (found) {
      return { duplicate: true, platformId: found.platformId, evidenceUrl: found.evidenceUrl, source: "wal" };
    }
  } catch {
    // El diario no se pudo leer: se sigue con la plataforma.
  }
  // 2. La plataforma (lectura de su API).
  const check = await checkPlatformAlreadyPublished(job.platform, creds, job.clip_id, fetchJson);
  if (check.status === "found" && check.platformId && check.evidenceUrl) {
    return { duplicate: true, platformId: check.platformId, evidenceUrl: check.evidenceUrl, source: "platform", platformCheck: "found" };
  }
  return { ...none, platformCheck: check.status, platformReason: check.reason };
}

/** Días del diario que cubre la comprobación (hoy + ayer). Solo
 *  para tests: la lógica vive en checkDuplicateBeforeUpload. */
export function walDayKeysForCheck(now = new Date()): string[] {
  const today = santiagoDayKey(now);
  const yesterday = santiagoDayKey(new Date(now.getTime() - 24 * 3600 * 1000));
  return yesterday === today ? [today] : [yesterday, today];
}
