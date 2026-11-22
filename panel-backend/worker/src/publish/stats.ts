// Medición AUTOMÁTICA de analíticas (2026-11-19): el Worker mide
// los clips publicados llamando a la API de cada plataforma y
// guarda las cifras reales en clip_stats (misma tabla y mismas
// reglas que las mediciones manuales de respaldo: lo que una API no
// devuelve queda NULL, nunca 0; likes/reach no tienen columna y NO
// se inventan).
//
// window_label: las métricas de estas APIs son LIFETIME
// (acumuladas desde la publicación), no de una ventana. Se guardan
// con window_label "7d", la misma etiqueta por defecto que usa el
// formulario manual (parseStatInput), para no romper los cálculos
// que agrupan por ventana. NO es una ventana de 7 días: es el
// acumulado total a la fecha de recorded_at.
//
// Recogida (collectAnalytics): la llama runWorkerCycle dentro del
// ciclo del Worker, como mucho 1 vez cada 6 h por tenant y como
// mucho 1 medición cada 24 h por (clip, plataforma). Si una
// plataforma falla, queda en worker_events y en analytics_status,
// esa plataforma queda en "sin medición" y las demás siguen:
// medir nunca tumba la cola. Reintentos solo transitorios (429/5xx,
// máx 3 con backoff); 401/403-permiso NO se reintentan.
//
// Credenciales: se reutilizan las cifradas ya guardadas en
// api_accounts vía resolveCreds (nunca se escriben tokens en chat,
// logs, documentos ni código). YouTube renueva su access token con
// el refresh_token guardado; TikTok usa el access token guardado
// tal cual (dura 24 h y el panel no guarda refresh_token).
//
// Bloqueadores conocidos (2026-11-19, no se fuerzan):
// - Instagram/Facebook: META_SCOPES no incluía los scopes de
//   insights hasta este cambio; el token actual seguirá fallando
//   (código Meta 200/10) hasta que el usuario re-conecte Meta.
// - TikTok: el token actual solo tiene video.publish; video/query/
//   exige video.list + Login Kit + aprobación Display API.

import type { Env } from "../cloudflare";
import type { Tenant } from "../auth";
import {
  createClipStat,
  getClip,
  getSettings,
  listClipStats,
  logWorkerEvent,
  markJobDeleted,
  saveAnalyticsStatus,
  saveSettings,
  type AnalyticsAutoStatusValue,
  type ClipRow,
  type ClipStatRow,
} from "../db";
import { errorMessage, httpJson, isRetryableStatus, type HttpResult } from "./http";
import { resolveCreds, type PublishCreds } from "./index";
import { refreshAccessToken, type YoutubeCreds } from "./youtube";

const GRAPH_VERSION = "v26.0";
const GRAPH_HOST = `https://graph.facebook.com/${GRAPH_VERSION}`;
const COLLECT_MIN_INTERVAL_MS = 6 * 3600_000; // como mucho 1 pasada cada 6 h por tenant
const PER_CLIP_MIN_INTERVAL_MS = 24 * 3600_000; // como mucho 1 medición cada 24 h por (clip, plataforma)

export type ApiPlatformKey = "youtube" | "tiktok" | "instagram" | "facebook";

export type PanelPlatformKey = "youtube_shorts" | "tiktok" | "instagram_reels" | "facebook_reels";

const API_TO_PANEL: Record<ApiPlatformKey, PanelPlatformKey> = {
  youtube: "youtube_shorts",
  tiktok: "tiktok",
  instagram: "instagram_reels",
  facebook: "facebook_reels",
};

const PANEL_TO_API: Record<string, ApiPlatformKey | null> = {
  youtube_shorts: "youtube",
  tiktok: "tiktok",
  instagram_reels: "instagram",
  facebook_reels: "facebook",
};

/** Error de permiso: la API dice que el token no tiene el scope
 *  necesario. NO se reintenta (reintentar no lo arregla) y se
 *  clasifica como `sin_permiso` con el motivo literal. */
export class StatsPermissionError extends Error {}

/** Sin credencial guardada para esa plataforma: se clasifica como
 *  `sin_credencial`, distinto de `sin_permiso` (que es un token
 *  que existe pero sin el scope). */
export class StatsNoCredentialError extends Error {}

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

/** GET/POST con reintentos SOLO transitorios (429/5xx/red, máx 3
 *  con backoff 0,5 s / 1 s). Un 401/403 o un 400 vuelve tal cual
 *  en el primer intento. */
async function fetchJsonRetry(
  method: string,
  url: string,
  headers: Record<string, string> = {},
  body?: unknown,
): Promise<HttpResult> {
  let last: HttpResult | null = null;
  for (let attempt = 1; attempt <= 3; attempt++) {
    const r = await httpJson(method, url, headers, body);
    last = r;
    if (r.status === 200) return r;
    if (!isRetryableStatus(r.status)) return r;
    if (attempt < 3) await sleep(500 * 2 ** (attempt - 1));
  }
  return last as HttpResult;
}

function metaError(json: Record<string, unknown>): { code: number | null; message: string } {
  const e = (json.error ?? {}) as Record<string, unknown>;
  return {
    code: typeof e.code === "number" ? e.code : null,
    message: typeof e.message === "string" ? e.message : "",
  };
}

/** ¿Es un error de permiso de Meta (falta scope)? Código 200/10
 *  ("Permissions error") o HTTP 401/403. */
function isMetaPermission(status: number, json: Record<string, unknown>): boolean {
  if (status === 401 || status === 403) return true;
  const { code } = metaError(json);
  return code === 200 || code === 10;
}

// ---------------------------------------------------------------------------
// YouTube: Data API v3, 1 unidad de cuota por llamada, hasta 50 ids.
// Scope youtube.readonly (ya en GOOGLE_SCOPES). Retención, shares,
// saves y new_subs: NULL (la Data API no los da; la Analytics API
// v2 sería fase aparte, no se implementa aquí). likes se pide pero
// NO se guarda: clip_stats no tiene columna likes.
// ---------------------------------------------------------------------------
export interface YoutubeMeasurement {
  views: number;
  comments: number | null;
}

export async function fetchYoutubeStats(
  creds: YoutubeCreds,
  videoIds: string[],
): Promise<Map<string, YoutubeMeasurement>> {
  const access = await refreshAccessToken(creds);
  if (!access) {
    throw new StatsPermissionError("YouTube: no se pudo renovar el access token con el refresh_token guardado: reautorizar OAuth en Cuentas.");
  }
  const out = new Map<string, YoutubeMeasurement>();
  for (let i = 0; i < videoIds.length; i += 50) {
    const batch = videoIds.slice(i, i + 50);
    const url =
      "https://www.googleapis.com/youtube/v3/videos?part=statistics&id=" +
      batch.map(encodeURIComponent).join(",") +
      "&fields=items(id,statistics(viewCount,likeCount,commentCount))";
    const r = await fetchJsonRetry("GET", url, { Authorization: `Bearer ${access}` });
    if (r.status === 401 || r.status === 403) {
      throw new StatsPermissionError(
        `YouTube: permiso denegado (HTTP ${r.status}): el token concedido no incluye youtube.readonly; reautorizar OAuth en Cuentas.`,
      );
    }
    if (r.status !== 200) {
      throw new Error(`YouTube: HTTP ${r.status}: ${errorMessage(r.json, "sin detalle")}`);
    }
    const items = (r.json.items ?? []) as Array<Record<string, unknown>>;
    for (const it of items) {
      const st = (it.statistics ?? {}) as Record<string, unknown>;
      const views = Number(st.viewCount);
      if (!Number.isFinite(views)) continue;
      const comments = Number(st.commentCount);
      out.set(String(it.id), { views, comments: Number.isFinite(comments) ? comments : null });
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Instagram: Graph API, nodo /{media_id}/insights. Sin
// crossposted_views ni facebook_views: fallan si el reel no se
// cruzó a Facebook y tumban TODA la petición. Retención derivada:
// ig_reels_avg_watch_time (ms) / duración del clip (ms), tope 100.
// new_subs: NULL (la API no lo da por media).
// ---------------------------------------------------------------------------
const IG_METRICS =
  "views,reach,likes,comments,saved,shares,total_interactions,ig_reels_avg_watch_time,ig_reels_video_view_total_time,reels_skip_rate";

export interface InstagramMeasurement {
  views: number;
  retention_pct: number | null;
  shares: number | null;
  saves: number | null;
  comments: number | null;
}

export async function fetchInstagramStats(
  token: string,
  mediaId: string,
  durationMs: number,
): Promise<InstagramMeasurement> {
  const url = `${GRAPH_HOST}/${encodeURIComponent(mediaId)}/insights?metric=${IG_METRICS}&access_token=${encodeURIComponent(token)}`;
  const r = await fetchJsonRetry("GET", url);
  if (r.status !== 200) {
    const { code, message } = metaError(r.json);
    if (isMetaPermission(r.status, r.json)) {
      throw new StatsPermissionError(
        `Instagram: sin permiso de insights (código Meta ${code ?? r.status}): falta el scope instagram_manage_insights; re-conectar Meta en Cuentas con los permisos nuevos.`,
      );
    }
    // El "does not exist" de Graph API es ambiguo: también sale cuando
    // el token actual no puede ver el medio (p. ej. publicado antes de
    // re-conectar Meta: el media 18484769662119306 del clip 7 existe en
    // público y el token post-revinculación no lo ve). Por eso NUNCA se
    // marca borrado por este error (ver measureGroup): solo se salta el
    // clip y se sigue midiendo el resto. La aclaración va en el motivo
    // para no leerlo como "el post no existe".
    const hint =
      r.status === 400 && message.includes("does not exist")
        ? " (el objeto no existe o el token actual no puede verlo; p. ej. un medio publicado antes de re-conectar Meta)"
        : "";
    throw new Error(`Instagram: HTTP ${r.status}: ${message || errorMessage(r.json, "sin detalle")}${hint}`);
  }
  const data = (r.json.data ?? []) as Array<{ name?: unknown; values?: Array<{ value?: unknown }> }>;
  const byName = new Map<string, number>();
  for (const d of data) {
    if (typeof d.name !== "string") continue;
    const v = d.values?.[0]?.value;
    if (typeof v === "number") byName.set(d.name, v);
  }
  const num = (name: string): number | null => {
    const v = byName.get(name);
    return typeof v === "number" ? v : null;
  };
  const views = num("views");
  if (views === null) throw new Error("Instagram: la respuesta de insights no trae views.");
  const avgWatchMs = num("ig_reels_avg_watch_time");
  const retention = avgWatchMs !== null && durationMs > 0 ? Math.min(100, (avgWatchMs / durationMs) * 100) : null;
  return { views, retention_pct: retention, shares: num("shares"), saves: num("saved"), comments: num("comments") };
}

// ---------------------------------------------------------------------------
// Facebook: Graph API, nodo /{video_id}/video_insights, period=lifetime.
// OJO: si Meta rechaza UNA métrica del conjunto, cae toda la
// petición: ante un fallo no-permiso se reintenta con el conjunto
// mínimo (vistas + completas). Retención derivada:
// complete_views / views, tope 100. saves: NULL. Comentarios por el
// nodo del vídeo (?fields=comments.limit(0).summary(true)) y
// shares por ?fields=shares; si esos dos fallan, quedan NULL sin
// tumbar la medición.
// ---------------------------------------------------------------------------
const FB_METRICS_FULL =
  "total_video_views,total_video_complete_views,total_video_avg_time_watched,total_video_view_total_time,total_video_reactions_by_type_total,blue_reels_play_count,fb_reels_total_plays,fb_reels_replay_count";
const FB_METRICS_MIN = "total_video_views,total_video_complete_views";

export interface FacebookMeasurement {
  views: number;
  retention_pct: number | null;
  shares: number | null;
  saves: number | null;
  comments: number | null;
}

async function fbVideoInsights(token: string, videoId: string, metrics: string): Promise<Map<string, number>> {
  const url =
    `${GRAPH_HOST}/${encodeURIComponent(videoId)}/video_insights` +
    `?metric=${metrics}&period=lifetime&access_token=${encodeURIComponent(token)}`;
  const r = await fetchJsonRetry("GET", url);
  if (r.status !== 200) {
    const { code, message } = metaError(r.json);
    if (isMetaPermission(r.status, r.json)) {
      throw new StatsPermissionError(
        `Facebook: sin permiso de insights (código Meta ${code ?? r.status}): falta el scope read_insights; re-conectar Meta en Cuentas con los permisos nuevos.`,
      );
    }
    throw new Error(`Facebook: HTTP ${r.status}: ${message || errorMessage(r.json, "sin detalle")}`);
  }
  const data = (r.json.data ?? []) as Array<{ name?: unknown; values?: Array<{ value?: unknown }> }>;
  const out = new Map<string, number>();
  for (const d of data) {
    if (typeof d.name !== "string") continue;
    const v = d.values?.[0]?.value;
    if (typeof v === "number") out.set(d.name, v);
  }
  return out;
}

export async function fetchFacebookStats(token: string, videoId: string): Promise<FacebookMeasurement> {
  let insights: Map<string, number>;
  try {
    insights = await fbVideoInsights(token, videoId, FB_METRICS_FULL);
  } catch (e) {
    if (e instanceof StatsPermissionError) throw e;
    // Una métrica rechazada tumba toda la petición: conjunto mínimo.
    insights = await fbVideoInsights(token, videoId, FB_METRICS_MIN);
  }
  const views = insights.get("total_video_views");
  if (views === undefined) throw new Error("Facebook: la respuesta de video_insights no trae total_video_views.");
  const complete = insights.get("total_video_complete_views");
  const retention = complete !== undefined && views > 0 ? Math.min(100, (complete / views) * 100) : null;
  let comments: number | null = null;
  let shares: number | null = null;
  try {
    const r = await fetchJsonRetry(
      "GET",
      `${GRAPH_HOST}/${encodeURIComponent(videoId)}?fields=comments.limit(0).summary(true),shares&access_token=${encodeURIComponent(token)}`,
    );
    if (r.status === 200) {
      const summary = (r.json.comments as Record<string, unknown> | undefined)?.summary as Record<string, unknown> | undefined;
      const total = summary?.total_count;
      if (typeof total === "number") comments = total;
      const sh = (r.json.shares ?? {}) as Record<string, unknown>;
      if (typeof sh.count === "number") shares = sh.count;
    }
  } catch {
    // Comentarios/shares opcionales: quedan NULL sin tumbar la medición.
  }
  return { views, retention_pct: retention, shares, saves: null, comments };
}

// ---------------------------------------------------------------------------
// TikTok: Display API, POST /v2/video/query/. Exige scope video.list
// + Login Kit + aprobación Display API. El token actual solo tiene
// video.publish: hoy devuelve 401 scope_not_authorized y eso se
// clasifica como sin_permiso (NO se reintenta, NO se inventa nada).
// El access token dura 24 h y el panel no guarda refresh_token.
// ---------------------------------------------------------------------------
export interface TikTokMeasurement {
  views: number;
  comments: number | null;
  shares: number | null;
}

export async function fetchTikTokStats(token: string, videoIds: string[]): Promise<Map<string, TikTokMeasurement>> {
  const out = new Map<string, TikTokMeasurement>();
  for (let i = 0; i < videoIds.length; i += 100) {
    const batch = videoIds.slice(i, i + 100);
    const r = await fetchJsonRetry(
      "POST",
      "https://open.tiktokapis.com/v2/video/query/?fields=id,view_count,like_count,comment_count,share_count",
      { Authorization: `Bearer ${token}` },
      { filters: { video_ids: batch } },
    );
    const err = (r.json.error ?? {}) as Record<string, unknown>;
    if (err.code === "scope_not_authorized") {
      throw new StatsPermissionError(
        "TikTok: sin permiso — falta reautorizar con video.list (scope video.list + Login Kit + aprobación Display API). El token actual solo tiene video.publish.",
      );
    }
    if (typeof err.code === "string" && err.code) {
      throw new Error(`TikTok: ${String(err.code)}: ${String(err.message ?? "sin detalle")}`);
    }
    if (r.status !== 200) {
      throw new Error(`TikTok: HTTP ${r.status}: ${errorMessage(r.json, "sin detalle")}`);
    }
    const videos = ((r.json.data as Record<string, unknown> | undefined)?.videos ?? []) as Array<Record<string, unknown>>;
    for (const v of videos) {
      const views = Number(v.view_count);
      if (!Number.isFinite(views) || typeof v.id !== "string") continue;
      const comments = Number(v.comment_count);
      const shares = Number(v.share_count);
      out.set(v.id, {
        views,
        comments: Number.isFinite(comments) ? comments : null,
        shares: Number.isFinite(shares) ? shares : null,
      });
    }
  }
  return out;
}

/** Id real del vídeo de TikTok desde la URL pública guardada en
 *  jobs.evidence_url (.../video/<id>). En vía Zernio el
 *  platform_id del job NO es el id de TikTok: es la URL o el id de
 *  Zernio, así que la evidencia es la única fuente fiable. */
export function extractTikTokVideoId(evidenceUrl: string | null | undefined): string | null {
  if (!evidenceUrl) return null;
  const m = /\/video\/(\d+)/.exec(evidenceUrl);
  return m ? (m[1] as string) : null;
}

// ---------------------------------------------------------------------------
// Recogida programada (la llama runWorkerCycle dentro del ciclo).
// ---------------------------------------------------------------------------
interface PostedTarget {
  clipId: number;
  panelPlatform: PanelPlatformKey;
  apiPlatform: ApiPlatformKey;
  platformId: string;
  evidenceUrl: string | null;
  language: "es" | "en";
}

export interface CollectSummary {
  ran: boolean;
  collected: number;
  skippedRecent: number;
  statuses: Array<{ platform: ApiPlatformKey; status: AnalyticsAutoStatusValue; reason: string }>;
}

const emptySummary = (): CollectSummary => ({ ran: false, collected: 0, skippedRecent: 0, statuses: [] });

export async function collectAnalytics(env: Env, tenant: Tenant, now = Date.now()): Promise<CollectSummary> {
  const db = env.DB;
  try {
    const settings = await getSettings(db, tenant).catch(() => ({} as Record<string, unknown>));
    const lastRun = typeof settings["analytics_last_collected_at"] === "number" ? (settings["analytics_last_collected_at"] as number) : 0;
    if (now - lastRun < COLLECT_MIN_INTERVAL_MS) return emptySummary();
    // Último job posted por (clip, plataforma): platform_id vive
    // SOLO en jobs (clips.publish_states no lo guarda). (0031): los
    // jobs con deleted_at son vídeos que ya no existen en la
    // plataforma: no se miden ni se cuentan. El filtro es en JS, no
    // en SQL: los dobles de D1 de los tests no modelan `IS NULL`.
    const posted = await db
      .prepare(
        "SELECT clip_id, platform, platform_id, evidence_url, language, deleted_at FROM jobs " +
          "WHERE tenant_id = ? AND kind = 'publish' AND state = 'posted' AND platform_id IS NOT NULL ORDER BY updated_at DESC",
      )
      .bind(tenant)
      .all<{ clip_id: number; platform: string; platform_id: string; evidence_url: string | null; language: string; deleted_at?: number | null }>();
    const targets = new Map<string, PostedTarget>();
    for (const j of posted.results) {
      if (j.deleted_at) continue;
      const api = PANEL_TO_API[j.platform] ?? null;
      if (!api) continue;
      const key = `${j.clip_id}|${j.platform}`;
      if (targets.has(key)) continue;
      targets.set(key, {
        clipId: j.clip_id,
        panelPlatform: j.platform as PanelPlatformKey,
        apiPlatform: api,
        platformId: j.platform_id,
        evidenceUrl: j.evidence_url,
        language: j.language === "en" ? "en" : "es",
      });
    }
    if (targets.size === 0) return emptySummary();
    // Última medición por (clip, plataforma): si es de hace menos
    // de 24 h (manual o automática), no se vuelve a medir.
    const allStats = await listClipStats(db, tenant).catch(() => [] as ClipStatRow[]);
    const latestByKey = new Map<string, number>();
    for (const s of allStats) {
      const key = `${s.clip_id}|${s.platform}`;
      const prev = latestByKey.get(key);
      if (prev === undefined || s.recorded_at > prev) latestByKey.set(key, s.recorded_at);
    }
    const fresh: PostedTarget[] = [];
    let skippedRecent = 0;
    for (const t of targets.values()) {
      const last = latestByKey.get(`${t.clipId}|${t.apiPlatform}`);
      if (last !== undefined && now - last < PER_CLIP_MIN_INTERVAL_MS) {
        skippedRecent++;
        continue;
      }
      fresh.push(t);
    }
    const summary: CollectSummary = { ran: true, collected: 0, skippedRecent, statuses: [] };
    // Agrupar por (plataforma, idioma): la credencial se resuelve
    // por plataforma+idioma (resolveCreds).
    const groups = new Map<string, PostedTarget[]>();
    for (const t of fresh) {
      const key = `${t.apiPlatform}|${t.language}`;
      const list = groups.get(key) ?? [];
      list.push(t);
      groups.set(key, list);
    }
    const statusByPlatform = new Map<ApiPlatformKey, { status: AnalyticsAutoStatusValue; reason: string; measured: boolean }>();
    const clipCache = new Map<number, ClipRow | null>();
    const getClipCached = async (clipId: number): Promise<ClipRow | null> => {
      if (!clipCache.has(clipId)) clipCache.set(clipId, await getClip(db, clipId, tenant).catch(() => null));
      return clipCache.get(clipId) ?? null;
    };
    for (const [, group] of groups) {
      const api = group[0]?.apiPlatform as ApiPlatformKey;
      const lang = group[0]?.language ?? "es";
      const panel = API_TO_PANEL[api];
      try {
        const creds: PublishCreds = await resolveCreds(env, panel, tenant, lang);
        const res = await measureGroup(env, tenant, api, group, creds, getClipCached, now);
        summary.collected += res.measured;
        const prev = statusByPlatform.get(api);
        if (res.measured > 0) {
          // Medidos y saltados: el estado es ok (se midió) pero el
          // motivo deja constancia del clip que no se pudo medir.
          const skipNote =
            res.skipped.length > 0
              ? ` ${res.skipped.length} sin medir: ${res.skipped[0]?.reason ?? ""}`
              : "";
          statusByPlatform.set(api, { status: "ok", reason: `${res.measured} clip(s) medidos.${skipNote}`, measured: true });
        } else if (res.skipped.length > 0 && (!prev || !prev.measured)) {
          // Nada medido y algo falló por clip: el error queda visible
          // en el motivo (antes tumbaba el grupo entero; ahora solo
          // este motivo, y el resto de clips sí se miden).
          statusByPlatform.set(api, { status: "error", reason: res.skipped[0]?.reason ?? "Sin medición.", measured: false });
        } else if (!prev) {
          statusByPlatform.set(api, { status: "ok", reason: "Sin clips nuevos que medir en esta pasada.", measured: false });
        }
      } catch (e) {
        const reason = e instanceof Error ? e.message : String(e);
        const status: AnalyticsAutoStatusValue =
          e instanceof StatsPermissionError ? "sin_permiso" : e instanceof StatsNoCredentialError ? "sin_credencial" : "error";
        const prev = statusByPlatform.get(api);
        // Si el otro idioma de esta plataforma ya midió bien, ese
        // "ok" manda: un idioma sin permiso no borra la medición
        // del otro.
        if (!prev || !prev.measured) {
          statusByPlatform.set(api, { status, reason, measured: false });
        }
        await logWorkerEvent(db, "analytics", `Medición automática ${api} falló: ${reason}`, tenant).catch(() => {});
      }
    }
    // Sin credencial: measureGroup lanza StatsNoCredentialError y
    // se clasifica como `sin_credencial` (ver catch de arriba).
    for (const [api, st] of statusByPlatform) {
      const lastMeasured = st.measured ? now : null;
      await saveAnalyticsStatus(db, tenant, api, st.status, st.reason, lastMeasured).catch(() => {});
      summary.statuses.push({ platform: api, status: st.status, reason: st.reason });
    }
    await saveSettings(db, tenant, { ...settings, analytics_last_collected_at: now }).catch(() => {});
    return summary;
  } catch (e) {
    // Que medir falle nunca tumba el ciclo ni la cola.
    await logWorkerEvent(
      db,
      "analytics",
      `Recogida automática de analíticas falló: ${e instanceof Error ? e.message : String(e)}`,
      tenant,
    ).catch(() => {});
    return emptySummary();
  }
}

/** Resultado de medir un grupo: cuántos se midieron y cuáles no se
 *  pudieron medir (sin tumbar el grupo). Un clip "saltado" es un
 *  medio que la API no pudo cargar (p. ej. Instagram 400): se deja
 *  constancia en el motivo del estado y se sigue con el resto. Un
 *  vídeo borrado NO es un salto: se marca con deleted_at (0031) y
 *  deja de contar como publicado. */
export interface MeasureGroupResult {
  measured: number;
  skipped: Array<{ clipId: number; reason: string }>;
}

/** Mide un grupo (plataforma+idioma) y guarda en clip_stats.
 *  Lanza StatsPermissionError si el token no tiene el permiso (no
 *  se reintenta). Un medio que falla NO tumba el grupo: se salta ese
 *  clip y se mide el resto (2026-11-19: el media 18484769662119306
 *  del clip 7 tumbaba TODA la medición de Instagram y el clip 15
 *  solo se midió porque iba antes en updated_at DESC). */
async function measureGroup(
  env: Env,
  tenant: Tenant,
  api: ApiPlatformKey,
  group: PostedTarget[],
  creds: PublishCreds,
  getClipCached: (clipId: number) => Promise<ClipRow | null>,
  now: number,
): Promise<MeasureGroupResult> {
  const db = env.DB;
  const skipped: Array<{ clipId: number; reason: string }> = [];
  if (api === "youtube") {
    if (!creds.youtube) {
      throw new StatsNoCredentialError("YouTube: sin credencial OAuth guardada en Cuentas (client + refresh token).");
    }
    const ids = group.map((g) => g.platformId);
    const res = await fetchYoutubeStats(creds.youtube, ids);
    let n = 0;
    for (const g of group) {
      const m = res.get(g.platformId);
      // La Data API devuelve 200 y OMITE los ids que ya no existen
      // (borrado o privado): ese vídeo ya no es un publicado.
      if (!m) {
        await markJobDeleted(db, tenant, g.clipId, g.panelPlatform, now);
        continue;
      }
      await createClipStat(db, tenant, g.clipId, {
        platform: "youtube",
        views: m.views,
        retention_pct: null,
        shares: null,
        saves: null,
        comments: m.comments,
        new_subs: null,
        window_label: "7d",
        recorded_at: now,
      });
      n++;
    }
    return { measured: n, skipped };
  }
  if (api === "instagram") {
    if (!creds.instagram || !creds.igUserId) {
      throw new StatsNoCredentialError("Instagram: sin token de Meta o sin ig_user_id en Cuentas.");
    }
    let n = 0;
    for (const g of group) {
      try {
        const clip = await getClipCached(g.clipId);
        const durationMs = clip ? Math.max(0, (clip.end_sec - clip.start_sec) * 1000) : 0;
        const m = await fetchInstagramStats(creds.instagram, g.platformId, durationMs);
        await createClipStat(db, tenant, g.clipId, {
          platform: "instagram",
          views: m.views,
          retention_pct: m.retention_pct,
          shares: m.shares,
          saves: m.saves,
          comments: m.comments,
          new_subs: null,
          window_label: "7d",
          recorded_at: now,
        });
        n++;
      } catch (e) {
        // Un medio que no carga (400 ambiguo: borrado o token sin
        // visión, nunca se marca borrado) no tumba el grupo.
        skipped.push({ clipId: g.clipId, reason: e instanceof Error ? e.message : String(e) });
      }
    }
    return { measured: n, skipped };
  }
  if (api === "facebook") {
    if (!creds.facebook) {
      throw new StatsNoCredentialError("Facebook: sin Page access token en Cuentas.");
    }
    let n = 0;
    for (const g of group) {
      try {
        const m = await fetchFacebookStats(creds.facebook, g.platformId);
        await createClipStat(db, tenant, g.clipId, {
          platform: "facebook",
          views: m.views,
          retention_pct: m.retention_pct,
          shares: m.shares,
          saves: m.saves,
          comments: m.comments,
          new_subs: null,
          window_label: "7d",
          recorded_at: now,
        });
        n++;
      } catch (e) {
        skipped.push({ clipId: g.clipId, reason: e instanceof Error ? e.message : String(e) });
      }
    }
    return { measured: n, skipped };
  }
  // TikTok: la credencial guardada puede ser la clave de Zernio
  // (vía zernio), que NO es un access token de TikTok: sin token
  // directo no hay a qué llamar.
  if (creds.tiktokVia === "zernio") {
    throw new StatsPermissionError(
      "TikTok: la cuenta guardada es vía Zernio (su clave no es un access token de TikTok); la medición directa exige reautorizar TikTok con video.list.",
    );
  }
  if (!creds.tiktok) {
    throw new StatsNoCredentialError("TikTok: sin access token guardado en Cuentas.");
  }
  const ids = group.map((g) => extractTikTokVideoId(g.evidenceUrl) ?? g.platformId);
  const res = await fetchTikTokStats(creds.tiktok, ids);
  let n = 0;
  for (const g of group) {
    const id = extractTikTokVideoId(g.evidenceUrl) ?? g.platformId;
    const m = res.get(id);
    // La query devuelve 200 y OMITE los ids que ya no existen: ese
    // vídeo ya no es un publicado (igual que YouTube, 0031).
    if (!m) {
      await markJobDeleted(db, tenant, g.clipId, g.panelPlatform, now);
      continue;
    }
    await createClipStat(db, tenant, g.clipId, {
      platform: "tiktok",
      views: m.views,
      retention_pct: null,
      shares: m.shares,
      saves: null,
      comments: m.comments,
      new_subs: null,
      window_label: "7d",
      recorded_at: now,
    });
    n++;
  }
  return { measured: n, skipped };
}

