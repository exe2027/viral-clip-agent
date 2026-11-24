// Engagement v2 — VÍA NATIVA (2026-11-23, v5.4.0).
//
// Adentro y afuera para YouTube, Instagram y Facebook con los tokens
// OAuth YA guardados en api_accounts (resolveCreds, como los
// adaptadores de publicación). TikTok NO tiene vía nativa: sigue
// solo por Zernio (runZernioEngagement, limitado a TikTok por el
// orquestador runEngagement de este módulo). Nada de lo de Zernio se
// quita: solo deja de usarse para IG/FB/YT, donde nunca estuvo
// conectado de verdad (las OAuth nativas no sirven en Zernio).
//
// REGLA DE ORO: nunca se llama a un endpoint de comentar/likear sin
// haber comprobado ANTES que el scope exacto está concedido. La
// sonda (probeNativeScopes) lee la lista real de scopes del token:
//   YouTube: tokeninfo con el access token renovado por refresh_token.
//   Meta: debug_token, SOLO si META_APP_ID/SECRET están configurados
//     en el Worker; si no, el estado es sin_sonda (no se inventa) y
//     la pasada no llama a nada de Meta.
// Si falta un permiso, la pasada registra sin_permiso con el scope
// exacto que falta (engagement_log, reason) y el panel muestra
// "re-autorizar en Cuentas". Los objetivos de afuera con sin_permiso
// quedan PENDING (no se queman): tras re-autorizar, la siguiente
// pasada los intenta.
//
// Se reutilizan las reglas v1 (zernio-engagement.ts): topes por
// plataforma+idioma+día (Santiago), plantillas ES+EN que rotan,
// nunca 2 veces al mismo comentario (engagement_replied), nunca a
// comentarios propios, pausa 8-15 s entre escrituras, kill-switch
// engagement_enabled, todo en engagement_log con reason. Sin
// navegador, sin scraping, sin follow en ninguna vía.

import type { D1Database, Env } from "../cloudflare";
import type { Tenant } from "../auth";
import {
  effectiveSettings,
  getSettings,
  listPostedJobs,
  logWorkerEvent,
  saveSettings,
  type ClipLanguage,
} from "../db";
import { santiagoDayKey } from "../gate";
import { httpJson } from "./http";
import { resolveCreds, type PublishCreds } from "./index";
import type { PanelPlatform } from "../db";
import { refreshAccessToken } from "./youtube";
import {
  FACEBOOK_ENGAGE_SCOPE,
  INSTAGRAM_LIKE_SCOPE,
  INSTAGRAM_REPLY_SCOPE,
  NATIVE_REPLY_SCOPE,
  NATIVE_REQUIRED_SCOPES,
  YOUTUBE_COMMENT_SCOPE,
} from "./oauth-scopes";
import {
  countEngagementToday,
  engagementCaps,
  ENGAGEMENT_MIN_INTERVAL_MS,
  firstRunAt,
  hasReplied,
  isOwnComment,
  listPendingTargets,
  loadClipTitles,
  logEngagement,
  markReplied,
  markTarget,
  mergeFoundMaps,
  pickReply,
  PROBE_CACHE_MS,
  runZernioEngagement,
  shouldSkipComment,
  type EngagementAction,
  type EngagementRunSummary,
  type ZernioPlatform,
} from "./zernio-engagement";

const GRAPH_VERSION = "v26.0";
const GRAPH = `https://graph.facebook.com/${GRAPH_VERSION}`;
const YT_API = "https://www.googleapis.com/youtube/v3";
const MAX_POSTS_PER_RUN = 10;
const MAX_COMMENTS_PER_POST = 10;

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

/** resolveCreds que nunca lanza: sin credencial devuelve {}.
 *  "instagram_reels"/"facebook_reels" resuelven a la misma fila
 *  meta (platform "meta" en api_accounts). */
async function safeCreds(env: Env, platform: PanelPlatform, tenant: Tenant, lang: ClipLanguage): Promise<PublishCreds> {
  return resolveCreds(env, platform, tenant, lang).catch(() => ({} as PublishCreds));
}

export type NativePlatform = "youtube" | "instagram" | "facebook";
export const NATIVE_PLATFORMS: NativePlatform[] = ["youtube", "instagram", "facebook"];

export type ProbeState = "ok" | "sin_permiso" | "sin_conexion" | "sin_sonda" | "sonda_fallida" | "sin_veredicto";

/** "sin_veredicto": la sonda NO cubre esa plataforma (TikTok, solo
 *  vía Zernio). No es un OK ni un fallo: el estado real está en
 *  Cuentas. Se declara explícito para que la casilla nunca quede
 *  vacía ni implique OK (V1, spec validadores 2026-11-23). */

export interface StoredScopeProbe {
  state: ProbeState;
  granted_scopes: string[];
  missing_scopes: string[];
  checked_at: number | null;
  error?: string | null;
}

export type ProbeMap = Record<string, StoredScopeProbe>;

function probeKey(platform: string, language: ClipLanguage): string {
  return `${platform}|${language}`;
}

function emptyProbe(state: ProbeState, missing: string[], error?: string | null): StoredScopeProbe {
  return { state, granted_scopes: [], missing_scopes: missing, checked_at: null, error: error ?? null };
}

function missingOf(required: string[], granted: string[]): string[] {
  return required.filter((s) => !granted.includes(s));
}

function graphError(payload: Record<string, unknown>, fallback: string): string {
  const err = (payload.error ?? {}) as Record<string, unknown>;
  if (typeof err.message === "string" && err.message) return err.message;
  if (typeof payload.message === "string" && payload.message) return payload.message;
  return fallback;
}

function ytError(payload: Record<string, unknown>, fallback: string): string {
  const err = (payload.error ?? {}) as Record<string, unknown>;
  if (typeof err.message === "string" && err.message) return err.message;
  if (typeof payload.message === "string" && payload.message) return payload.message;
  return fallback;
}

// --- Sonda de scopes (nunca expone tokens: solo guarda la lista) ---

export interface ScopeProbeResult {
  probes: ProbeMap;
  checked_at: number;
  meta_configured: boolean;
  /** true si el resultado viene de la caché de 1 h (sin llamar a
   *  ninguna API); false si es fresco. Un veredicto cacheado tras
   *  un cambio de credenciales NO vale como confirmación: la caché
   *  se invalida al cambiar credenciales (V2). */
  cached: boolean;
}

function storedProbes(settings: Record<string, unknown>): { probes: ProbeMap; at: number | null } {
  const raw = settings.engagement_scopes;
  const probes: ProbeMap = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as ProbeMap) : {};
  const at = typeof settings.engagement_scopes_at === "number" ? (settings.engagement_scopes_at as number) : null;
  return { probes, at };
}

/** Lee la sonda guardada en settings (sin llamar a ninguna API).
 *  La usa el panel (vía getEngagementStatus) para pintar el estado. */
export function readStoredProbes(settings: Record<string, unknown>): { probes: ProbeMap; at: number | null } {
  return storedProbes(settings);
}

export async function probeNativeScopes(
  env: Env,
  tenant: Tenant,
  opts: { force?: boolean; nowMs?: number } = {},
): Promise<ScopeProbeResult> {
  const db = env.DB;
  const now = opts.nowMs ?? Date.now();
  const settings = await getSettings(db, tenant).catch(() => ({} as Record<string, unknown>));
  const stored = storedProbes(settings);
  if (!opts.force && stored.at && now - stored.at < PROBE_CACHE_MS && Object.keys(stored.probes).length > 0) {
    return { probes: stored.probes, checked_at: stored.at, meta_configured: metaAppConfigured(env), cached: true };
  }
  const probes: ProbeMap = {};
  const metaConfigured = metaAppConfigured(env);
  for (const lang of ["es", "en"] as ClipLanguage[]) {
    // TikTok: la sonda no tiene vía nativa (solo Zernio) y nunca
    // debe dejar la casilla vacía ni implicar OK. Veredicto
    // explícito al inicio de la vuelta (antes de los `continue` de
    // YT/Meta): sin_veredicto, el estado real está en Cuentas.
    probes[probeKey("tiktok", lang)] = {
      state: "sin_veredicto",
      granted_scopes: [],
      missing_scopes: [],
      checked_at: now,
      error: "la sonda no cubre TikTok: el estado real está en Cuentas (vía Zernio)",
    };
    // --- YouTube: tokeninfo con el token renovado por refresh_token ---
    const ytCreds = await safeCreds(env, "youtube_shorts", tenant, lang);
    if (!ytCreds.youtube) {
      probes[probeKey("youtube", lang)] = emptyProbe("sin_conexion", [YOUTUBE_COMMENT_SCOPE], "sin cuenta YouTube en este idioma");
    } else {
      const access = await refreshAccessToken(ytCreds.youtube).catch(() => null);
      if (!access) {
        probes[probeKey("youtube", lang)] = emptyProbe("sonda_fallida", [YOUTUBE_COMMENT_SCOPE], "no se pudo renovar el access token de YouTube");
      } else {
        const ti = await httpJson("GET", `https://oauth2.googleapis.com/tokeninfo?access_token=${encodeURIComponent(access)}`).catch(() => null);
        if (!ti || ti.status !== 200) {
          probes[probeKey("youtube", lang)] = emptyProbe("sonda_fallida", [YOUTUBE_COMMENT_SCOPE], `tokeninfo HTTP ${ti?.status ?? 0}`);
        } else {
          const granted = typeof ti.json.scope === "string" ? ti.json.scope.split(" ").filter(Boolean) : [];
          const missing = missingOf(NATIVE_REQUIRED_SCOPES.youtube as string[], granted);
          probes[probeKey("youtube", lang)] = {
            state: missing.length === 0 ? "ok" : "sin_permiso",
            granted_scopes: granted,
            missing_scopes: missing,
            checked_at: now,
            error: missing.length === 0 ? null : `falta ${missing.join(", ")}: re-autorizar en Cuentas`,
          };
        }
      }
    }
    // --- Meta: debug_token (solo con App ID/Secret configurados) ---
    const metaCreds = await safeCreds(env, "instagram_reels", tenant, lang);
    const metaToken = metaCreds.instagram ?? metaCreds.facebook ?? "";
    if (!metaToken) {
      probes[probeKey("instagram", lang)] = emptyProbe("sin_conexion", NATIVE_REQUIRED_SCOPES.instagram as string[], "sin cuenta Meta en este idioma");
      probes[probeKey("facebook", lang)] = emptyProbe("sin_conexion", [FACEBOOK_ENGAGE_SCOPE], "sin cuenta Meta en este idioma");
      continue;
    }
    if (!metaConfigured) {
      const err = "META_APP_ID/SECRET no configurados en el Worker: debug_token no se puede llamar y los scopes de Meta no se conocen";
      probes[probeKey("instagram", lang)] = emptyProbe("sin_sonda", NATIVE_REQUIRED_SCOPES.instagram as string[], err);
      probes[probeKey("facebook", lang)] = emptyProbe("sin_sonda", [FACEBOOK_ENGAGE_SCOPE], err);
      continue;
    }
    const appToken = `${(env.META_APP_ID ?? "").trim()}|${(env.META_APP_SECRET ?? "").trim()}`;
    const dbg = await httpJson(
      "GET",
      `${GRAPH}/debug_token?input_token=${encodeURIComponent(metaToken)}&access_token=${encodeURIComponent(appToken)}`,
    ).catch(() => null);
    const data = (dbg?.json.data ?? {}) as Record<string, unknown>;
    const scopes = data.scopes;
    if (!dbg || dbg.status !== 200 || !Array.isArray(scopes)) {
      const err = dbg ? graphError(dbg.json, `debug_token HTTP ${dbg.status}`) : "debug_token sin respuesta";
      probes[probeKey("instagram", lang)] = emptyProbe("sonda_fallida", NATIVE_REQUIRED_SCOPES.instagram as string[], err);
      probes[probeKey("facebook", lang)] = emptyProbe("sonda_fallida", [FACEBOOK_ENGAGE_SCOPE], err);
      continue;
    }
    // V1 (validadores 2026-11-23): los scopes solos NO bastan para
    // un OK. Un token de OTRA app o expirado con los scopes puestos
    // daba OK antes de este cambio (falso positivo). Se comprueba:
    //   app_id de debug_token == META_APP_ID configurado,
    //   is_valid !== false,
    //   expires_at == 0 (no expira) o en el futuro (segundos UNIX).
    const tokenAppId = typeof data.app_id === "string" ? data.app_id : "";
    const expiresAt = typeof data.expires_at === "number" ? data.expires_at : 0;
    const metaMismatch =
      tokenAppId !== (env.META_APP_ID ?? "").trim()
        ? `token Meta de otra app (app_id ${tokenAppId || "sin app_id"} distinto del configurado ${env.META_APP_ID ?? ""})`
        : data.is_valid === false
          ? "debug_token: el token Meta no es válido"
          : expiresAt > 0 && expiresAt * 1000 <= now
            ? "token Meta expirado según debug_token"
            : null;
    if (metaMismatch) {
      probes[probeKey("instagram", lang)] = emptyProbe("sonda_fallida", NATIVE_REQUIRED_SCOPES.instagram as string[], metaMismatch);
      probes[probeKey("facebook", lang)] = emptyProbe("sonda_fallida", [FACEBOOK_ENGAGE_SCOPE], metaMismatch);
      continue;
    }
    const granted = (scopes as unknown[]).filter((s): s is string => typeof s === "string");
    const igMissing = missingOf(NATIVE_REQUIRED_SCOPES.instagram as string[], granted);
    probes[probeKey("instagram", lang)] = {
      state: granted.includes(INSTAGRAM_REPLY_SCOPE) ? "ok" : "sin_permiso",
      granted_scopes: granted,
      missing_scopes: igMissing,
      checked_at: now,
      error: granted.includes(INSTAGRAM_REPLY_SCOPE)
        ? (igMissing.length > 0 ? `falta ${igMissing.join(", ")} para likes afuera` : null)
        : `falta ${igMissing.join(", ")}: re-autorizar en Cuentas`,
    };
    const fbMissing = missingOf([FACEBOOK_ENGAGE_SCOPE], granted);
    probes[probeKey("facebook", lang)] = {
      state: fbMissing.length === 0 ? "ok" : "sin_permiso",
      granted_scopes: granted,
      missing_scopes: fbMissing,
      checked_at: now,
      error: fbMissing.length === 0 ? null : `falta ${fbMissing.join(", ")}: re-autorizar en Cuentas`,
    };
  }
  await saveSettings(db, tenant, { ...settings, engagement_scopes: probes, engagement_scopes_at: now }).catch(() => undefined);
  return { probes, checked_at: now, meta_configured: metaConfigured, cached: false };
}

/** Invalida la caché de la sonda (V2): al cambiar una credencial,
 *  el veredicto guardado deja de valer y la próxima sonda debe ser
 *  fresca. Se llama desde los puntos que escriben credenciales
 *  (upsertAccountCredential y POST /api/accounts/:id/credential). */
export async function invalidateProbeCache(db: D1Database, tenant: Tenant): Promise<void> {
  const settings = await getSettings(db, tenant).catch(() => ({} as Record<string, unknown>));
  await saveSettings(db, tenant, { ...settings, engagement_scopes: null, engagement_scopes_at: null }).catch(() => undefined);
}

export function metaAppConfigured(env: Env): boolean {
  return !!(env.META_APP_ID ?? "").trim() && !!(env.META_APP_SECRET ?? "").trim();
}

// --- Clientes nativos (solo se llaman con el scope comprobado) ---

interface NativeComment {
  id: string;
  text: string;
  from: { username: string };
  replies: Array<{ from: { username: string } }>;
}

function normComment(id: string, text: string, author: string, replyAuthors: string[]): NativeComment {
  return { id, text, from: { username: author }, replies: replyAuthors.map((a) => ({ from: { username: a } })) };
}

async function ytListThreads(access: string, videoId: string): Promise<NativeComment[]> {
  const r = await httpJson("GET", `${YT_API}/commentThreads?part=snippet,replies&videoId=${encodeURIComponent(videoId)}&maxResults=10&order=time`, { Authorization: `Bearer ${access}` });
  if (r.status !== 200) throw new Error(`YouTube hilos: HTTP ${r.status}: ${ytError(r.json, "sin detalle")}`);
  const items = Array.isArray(r.json.items) ? r.json.items : [];
  const out: NativeComment[] = [];
  for (const it of items) {
    const o = it as Record<string, unknown>;
    const sn = (o.snippet ?? {}) as Record<string, unknown>;
    const top = (sn.topLevelComment ?? {}) as Record<string, unknown>;
    const topSn = (top.snippet ?? {}) as Record<string, unknown>;
    const id = typeof top.id === "string" ? top.id : typeof o.id === "string" ? o.id : "";
    const text = typeof topSn.textDisplay === "string" ? topSn.textDisplay : "";
    const author = typeof topSn.authorDisplayName === "string" ? topSn.authorDisplayName : "";
    const repliesObj = (o.replies ?? {}) as Record<string, unknown>;
    const replyComments = Array.isArray(repliesObj.comments) ? repliesObj.comments : [];
    const replyAuthors = (replyComments as Array<Record<string, unknown>>).map((c) => {
      const cs = (c.snippet ?? {}) as Record<string, unknown>;
      return typeof cs.authorDisplayName === "string" ? cs.authorDisplayName : "";
    }).filter(Boolean);
    if (id) out.push(normComment(id, text, author, replyAuthors));
  }
  return out;
}

async function ytReply(access: string, parentCommentId: string, text: string): Promise<void> {
  const r = await httpJson("POST", `${YT_API}/comments?part=snippet`, { Authorization: `Bearer ${access}` }, { snippet: { parentId: parentCommentId, textOriginal: text } });
  if (r.status !== 200 && r.status !== 201) throw new Error(`YouTube responder: HTTP ${r.status}: ${ytError(r.json, "sin detalle")}`);
}

async function ytLikeComment(access: string, commentId: string): Promise<void> {
  const r = await httpJson("POST", `${YT_API}/comments/rate?id=${encodeURIComponent(commentId)}&rating=like`, { Authorization: `Bearer ${access}` });
  if (r.status !== 200 && r.status !== 204) throw new Error(`YouTube like comentario: HTTP ${r.status}: ${ytError(r.json, "sin detalle")}`);
}

async function ytLikeVideo(access: string, videoId: string): Promise<void> {
  const r = await httpJson("POST", `${YT_API}/videos/rate?id=${encodeURIComponent(videoId)}&rating=like`, { Authorization: `Bearer ${access}` });
  if (r.status !== 200 && r.status !== 204) throw new Error(`YouTube like vídeo: HTTP ${r.status}: ${ytError(r.json, "sin detalle")}`);
}

async function ytCommentVideo(access: string, videoId: string, text: string): Promise<void> {
  const r = await httpJson("POST", `${YT_API}/commentThreads?part=snippet`, { Authorization: `Bearer ${access}` }, { snippet: { videoId, topLevelComment: { snippet: { textOriginal: text } } } });
  if (r.status !== 200 && r.status !== 201) throw new Error(`YouTube comentar: HTTP ${r.status}: ${ytError(r.json, "sin detalle")}`);
}

async function ytOwnChannelTitle(access: string): Promise<string | null> {
  try {
    const r = await httpJson("GET", `${YT_API}/channels?part=snippet&mine=true`, { Authorization: `Bearer ${access}` });
    if (r.status !== 200) return null;
    const items = Array.isArray(r.json.items) ? r.json.items : [];
    const sn = ((items[0] as Record<string, unknown> | undefined)?.snippet ?? {}) as Record<string, unknown>;
    return typeof sn.title === "string" ? sn.title : null;
  } catch {
    return null;
  }
}

async function igListComments(token: string, mediaId: string): Promise<NativeComment[]> {
  const fields = "id,text,username,timestamp,replies{id,username,text}";
  const r = await httpJson("GET", `${GRAPH}/${encodeURIComponent(mediaId)}/comments?fields=${encodeURIComponent(fields)}&limit=10&access_token=${encodeURIComponent(token)}`);
  if (r.status !== 200) throw new Error(`Instagram comentarios: HTTP ${r.status}: ${graphError(r.json, "sin detalle")}`);
  const data = Array.isArray(r.json.data) ? r.json.data : [];
  return (data as Array<Record<string, unknown>>).map((c) => {
    const repliesObj = (c.replies ?? {}) as Record<string, unknown>;
    const replyRows = Array.isArray(repliesObj.data) ? repliesObj.data : [];
    const authors = (replyRows as Array<Record<string, unknown>>).map((x) => (typeof x.username === "string" ? x.username : "")).filter(Boolean);
    return normComment(
      typeof c.id === "string" ? c.id : "",
      typeof c.text === "string" ? c.text : "",
      typeof c.username === "string" ? c.username : "",
      authors,
    );
  }).filter((c) => c.id);
}

async function igReply(token: string, commentId: string, text: string): Promise<void> {
  const params = new URLSearchParams({ message: text, access_token: token });
  const r = await httpJson("POST", `${GRAPH}/${encodeURIComponent(commentId)}/replies?${params.toString()}`);
  if (r.status !== 200 && r.status !== 201) throw new Error(`Instagram responder: HTTP ${r.status}: ${graphError(r.json, "sin detalle")}`);
}

async function igLikeComment(token: string, commentId: string): Promise<void> {
  const r = await httpJson("POST", `${GRAPH}/${encodeURIComponent(commentId)}/likes?access_token=${encodeURIComponent(token)}`);
  if (r.status !== 200 && r.status !== 201) throw new Error(`Instagram like comentario: HTTP ${r.status}: ${graphError(r.json, "sin detalle")}`);
}

async function igLikeMedia(token: string, mediaId: string): Promise<void> {
  const r = await httpJson("POST", `${GRAPH}/${encodeURIComponent(mediaId)}/likes?access_token=${encodeURIComponent(token)}`);
  if (r.status !== 200 && r.status !== 201) throw new Error(`Instagram like post: HTTP ${r.status}: ${graphError(r.json, "sin detalle")}`);
}

async function igCommentMedia(token: string, mediaId: string, text: string): Promise<void> {
  const params = new URLSearchParams({ message: text, access_token: token });
  const r = await httpJson("POST", `${GRAPH}/${encodeURIComponent(mediaId)}/comments?${params.toString()}`);
  if (r.status !== 200 && r.status !== 201) throw new Error(`Instagram comentar: HTTP ${r.status}: ${graphError(r.json, "sin detalle")}`);
}

async function igOwnUsername(token: string, igUserId: string): Promise<string | null> {
  try {
    const r = await httpJson("GET", `${GRAPH}/${encodeURIComponent(igUserId)}?fields=username&access_token=${encodeURIComponent(token)}`);
    if (r.status !== 200) return null;
    return typeof r.json.username === "string" ? (r.json.username as string) : null;
  } catch {
    return null;
  }
}

async function fbListComments(token: string, postId: string): Promise<NativeComment[]> {
  const fields = "id,message,from{id,name},comments{id,from{id,name}}";
  const r = await httpJson("GET", `${GRAPH}/${encodeURIComponent(postId)}/comments?fields=${encodeURIComponent(fields)}&limit=10&access_token=${encodeURIComponent(token)}`);
  if (r.status !== 200) throw new Error(`Facebook comentarios: HTTP ${r.status}: ${graphError(r.json, "sin detalle")}`);
  const data = Array.isArray(r.json.data) ? r.json.data : [];
  return (data as Array<Record<string, unknown>>).map((c) => {
    const from = (c.from ?? {}) as Record<string, unknown>;
    const commentsObj = (c.comments ?? {}) as Record<string, unknown>;
    const replyRows = Array.isArray(commentsObj.data) ? commentsObj.data : [];
    const authors = (replyRows as Array<Record<string, unknown>>).map((x) => {
      const f = (x.from ?? {}) as Record<string, unknown>;
      return typeof f.name === "string" ? f.name : "";
    }).filter(Boolean);
    return normComment(
      typeof c.id === "string" ? c.id : "",
      typeof c.message === "string" ? c.message : "",
      typeof from.name === "string" ? from.name : "",
      authors,
    );
  }).filter((c) => c.id);
}

async function fbReply(token: string, commentId: string, text: string): Promise<void> {
  const params = new URLSearchParams({ message: text, access_token: token });
  const r = await httpJson("POST", `${GRAPH}/${encodeURIComponent(commentId)}/comments?${params.toString()}`);
  if (r.status !== 200 && r.status !== 201) throw new Error(`Facebook responder: HTTP ${r.status}: ${graphError(r.json, "sin detalle")}`);
}

async function fbLikeComment(token: string, commentId: string): Promise<void> {
  const r = await httpJson("POST", `${GRAPH}/${encodeURIComponent(commentId)}/likes?access_token=${encodeURIComponent(token)}`);
  if (r.status !== 200 && r.status !== 201) throw new Error(`Facebook like comentario: HTTP ${r.status}: ${graphError(r.json, "sin detalle")}`);
}

async function fbLikePost(token: string, postId: string): Promise<void> {
  const r = await httpJson("POST", `${GRAPH}/${encodeURIComponent(postId)}/likes?access_token=${encodeURIComponent(token)}`);
  if (r.status !== 200 && r.status !== 201) throw new Error(`Facebook like post: HTTP ${r.status}: ${graphError(r.json, "sin detalle")}`);
}

async function fbCommentPost(token: string, postId: string, text: string): Promise<void> {
  const params = new URLSearchParams({ message: text, access_token: token });
  const r = await httpJson("POST", `${GRAPH}/${encodeURIComponent(postId)}/comments?${params.toString()}`);
  if (r.status !== 200 && r.status !== 201) throw new Error(`Facebook comentar: HTTP ${r.status}: ${graphError(r.json, "sin detalle")}`);
}

async function fbOwnPageName(token: string, pageId: string): Promise<string | null> {
  try {
    const r = await httpJson("GET", `${GRAPH}/${encodeURIComponent(pageId)}?fields=name&access_token=${encodeURIComponent(token)}`);
    if (r.status !== 200) return null;
    return typeof r.json.name === "string" ? (r.json.name as string) : null;
  } catch {
    return null;
  }
}

// --- Pasada nativa ---

export interface NativeEngagementSummary extends EngagementRunSummary {
  probes: ProbeMap;
}

export async function runNativeEngagement(
  env: Env,
  tenant: Tenant,
  opts: { force?: boolean; nowMs?: number; sleepMs?: number; saveStatus?: boolean } = {},
): Promise<NativeEngagementSummary> {
  const db = env.DB;
  const now = opts.nowMs ?? Date.now();
  const day = santiagoDayKey(new Date(now));
  const summary: NativeEngagementSummary = { ran: false, replies: 0, commentLikes: 0, outwardLikes: 0, outwardComments: 0, skipped: [], perLanguage: {}, probes: {}, found: {} };
  const settings = await getSettings(db, tenant).catch(() => ({} as Record<string, unknown>));
  const eff = effectiveSettings(settings);
  if (!eff.engagement_enabled) return { ...summary, reason: "engagement apagado en Ajustes (engagement_enabled=false)" };
  const lastRun = typeof settings.engagement_last_run_at === "number" ? (settings.engagement_last_run_at as number) : 0;
  if (!opts.force && now - lastRun < ENGAGEMENT_MIN_INTERVAL_MS) {
    return { ...summary, reason: "throttle: la pasada de engagement corre como mucho 1 vez por hora" };
  }
  // Modo seguro las primeras 24 h: topes 2/2/0/0 (ver engagementCaps).
  const caps = engagementCaps(settings, eff, now);
  const pace = async () => {
    if (opts.sleepMs !== undefined) await sleep(opts.sleepMs);
    else await sleep(8000 + Math.floor(Math.random() * 7000));
  };
  const probeResult = await probeNativeScopes(env, tenant, { nowMs: now }).catch(() => null);
  const probes = probeResult?.probes ?? {};
  summary.probes = probes;
  const clipTitles = await loadClipTitles(db, tenant);
  const posted = await listPostedJobs(db, tenant).catch(() => []);

  // Registra sin_permiso / sin_sonda / sin_conexion UNA vez por
  // plataforma+idioma y NO llama a ningún endpoint de comentar.
  const gateFailed = async (platform: NativePlatform, lang: ClipLanguage, action: EngagementAction): Promise<StoredScopeProbe | null> => {
    const p = probes[probeKey(platform, lang)];
    if (!p) return null;
    if (p.state === "ok") return null;
    const missing = p.missing_scopes.length > 0 ? p.missing_scopes.join(", ") : (NATIVE_REPLY_SCOPE[platform] ?? "");
    const reason =
      p.state === "sin_conexion"
        ? `sin_conexion: sin cuenta ${platform} en ${lang}`
        : p.state === "sin_sonda"
          ? `sin_sonda: scopes de ${platform} sin comprobar (${p.error ?? "falta configurar la app Meta en el Worker"})`
          : p.state === "sonda_fallida"
            ? `sonda_fallida: ${p.error ?? "no se pudo comprobar el scope"}`
            : `sin_permiso: falta scope ${missing} (re-autorizar en Cuentas)`;
    await logEngagement(db, tenant, day, platform, lang, action, "skipped", { reason, nowMs: now });
    summary.skipped.push({ what: `${platform} ${lang}`, reason });
    return p;
  };

  for (const lang of ["es", "en"] as ClipLanguage[]) {
    const langStatus: string[] = [];
    // --- YouTube nativo ---
    {
      const platform: NativePlatform = "youtube";
      const probe = probes[probeKey(platform, lang)];
      const creds = await safeCreds(env, "youtube_shorts", tenant, lang);
      if (!creds.youtube) {
        langStatus.push("youtube: sin_conexion");
      } else if (probe && probe.state !== "ok") {
        await gateFailed(platform, lang, "reply");
        langStatus.push(`youtube: ${probe.state}${probe.missing_scopes.length ? ` (falta ${probe.missing_scopes.join(", ")})` : ""}`);
      } else {
        const access = await refreshAccessToken(creds.youtube).catch(() => null);
        if (!access) {
          langStatus.push("youtube: no se pudo renovar el token");
        } else {
          const ownTitle = await ytOwnChannelTitle(access);
          const ownNames = ownTitle ? [ownTitle.replace(/^@/, "").toLowerCase()] : [];
          const mine = posted.filter((j) => j.platform === "youtube_shorts" && j.language === lang && j.platform_id).slice(0, MAX_POSTS_PER_RUN);
          // V3: conteo de lo encontrado (en bruto, antes de filtros).
          const ytFound = { comments: 0, postsChecked: 0, postsFailed: 0 };
          summary.found[`${platform}|${lang}`] = ytFound;
          let lastTemplate = -1;
          for (const job of mine) {
            const videoId = job.platform_id as string;
            let threads: NativeComment[];
            ytFound.postsChecked++;
            try {
              threads = await ytListThreads(access, videoId);
            } catch (e) {
              ytFound.postsFailed++;
              summary.skipped.push({ what: `hilo youtube ${videoId}`, reason: e instanceof Error ? e.message : String(e) });
              continue;
            }
            ytFound.comments += threads.length;
            for (const c of threads.slice(0, MAX_COMMENTS_PER_POST)) {
              const skip = shouldSkipComment(c.text);
              if (skip) continue;
              if (isOwnComment(c as unknown as Record<string, unknown>, ownNames)) continue;
              if (await hasReplied(db, tenant, c.id)) continue;
              const repliesToday = await countEngagementToday(db, tenant, day, platform, lang, "reply");
              if (repliesToday >= caps.reply) {
                summary.skipped.push({ what: `${platform} ${lang}`, reason: `tope respuestas ${repliesToday}/${caps.reply}${caps.safeMode ? " (modo seguro)" : ""}` });
                break;
              }
              const title = clipTitles.get(videoId) ?? null;
              const picked = pickReply(lang, title, c.id, lastTemplate);
              lastTemplate = picked.index;
              try {
                await ytReply(access, c.id, picked.text);
              } catch (e) {
                const msg = e instanceof Error ? e.message : String(e);
                await logEngagement(db, tenant, day, platform, lang, "reply", "error", { targetPostId: videoId, commentId: c.id, reason: msg, nowMs: now });
                if (/401|403/i.test(msg)) break;
                continue;
              }
              await markReplied(db, tenant, c.id, platform, now);
              await logEngagement(db, tenant, day, platform, lang, "reply", "ok", { targetPostId: videoId, commentId: c.id, nowMs: now });
              summary.replies++;
              try {
                const likesToday = await countEngagementToday(db, tenant, day, platform, lang, "like_comment");
                if (likesToday < caps.like) {
                  await ytLikeComment(access, c.id);
                  await logEngagement(db, tenant, day, platform, lang, "like_comment", "ok", { targetPostId: videoId, commentId: c.id, nowMs: now });
                  summary.commentLikes++;
                }
              } catch (e) {
                await logEngagement(db, tenant, day, platform, lang, "like_comment", "skipped", { targetPostId: videoId, commentId: c.id, reason: e instanceof Error ? e.message : String(e), nowMs: now });
              }
              await pace();
            }
          }
          langStatus.push("youtube: ok (nativa)");
        }
      }
    }
    // --- Instagram nativo ---
    {
      const platform: NativePlatform = "instagram";
      const probe = probes[probeKey(platform, lang)];
      const creds = await safeCreds(env, "instagram_reels", tenant, lang);
      if (!creds.instagram || !creds.igUserId) {
        langStatus.push("instagram: sin_conexion");
      } else if (probe && probe.state !== "ok") {
        await gateFailed(platform, lang, "reply");
        langStatus.push(`instagram: ${probe.state}${probe.missing_scopes.length ? ` (falta ${probe.missing_scopes.join(", ")})` : ""}`);
      } else {
        const token = creds.instagram;
        const ownName = await igOwnUsername(token, creds.igUserId);
        const ownNames = ownName ? [ownName.replace(/^@/, "").toLowerCase()] : [];
        const mine = posted.filter((j) => j.platform === "instagram_reels" && j.language === lang && j.platform_id).slice(0, MAX_POSTS_PER_RUN);
        // V3: conteo de lo encontrado (en bruto, antes de filtros).
        const igFound = { comments: 0, postsChecked: 0, postsFailed: 0 };
        summary.found[`${platform}|${lang}`] = igFound;
        let lastTemplate = -1;
        for (const job of mine) {
          const mediaId = job.platform_id as string;
          let comments: NativeComment[];
          igFound.postsChecked++;
          try {
            comments = await igListComments(token, mediaId);
          } catch (e) {
            igFound.postsFailed++;
            summary.skipped.push({ what: `hilo instagram ${mediaId}`, reason: e instanceof Error ? e.message : String(e) });
            continue;
          }
          igFound.comments += comments.length;
          for (const c of comments.slice(0, MAX_COMMENTS_PER_POST)) {
            const skip = shouldSkipComment(c.text);
            if (skip) continue;
            if (isOwnComment(c as unknown as Record<string, unknown>, ownNames)) continue;
            if (await hasReplied(db, tenant, c.id)) continue;
            const repliesToday = await countEngagementToday(db, tenant, day, platform, lang, "reply");
            if (repliesToday >= caps.reply) {
              summary.skipped.push({ what: `${platform} ${lang}`, reason: `tope respuestas ${repliesToday}/${caps.reply}${caps.safeMode ? " (modo seguro)" : ""}` });
              break;
            }
            const title = clipTitles.get(mediaId) ?? null;
            const picked = pickReply(lang, title, c.id, lastTemplate);
            lastTemplate = picked.index;
            try {
              await igReply(token, c.id, picked.text);
            } catch (e) {
              const msg = e instanceof Error ? e.message : String(e);
              await logEngagement(db, tenant, day, platform, lang, "reply", "error", { targetPostId: mediaId, commentId: c.id, reason: msg, nowMs: now });
              if (/401|403/i.test(msg)) break;
              continue;
            }
            await markReplied(db, tenant, c.id, platform, now);
            await logEngagement(db, tenant, day, platform, lang, "reply", "ok", { targetPostId: mediaId, commentId: c.id, nowMs: now });
            summary.replies++;
            try {
              const likesToday = await countEngagementToday(db, tenant, day, platform, lang, "like_comment");
              if (likesToday < caps.like) {
                await igLikeComment(token, c.id);
                await logEngagement(db, tenant, day, platform, lang, "like_comment", "ok", { targetPostId: mediaId, commentId: c.id, nowMs: now });
                summary.commentLikes++;
              }
            } catch (e) {
              await logEngagement(db, tenant, day, platform, lang, "like_comment", "skipped", { targetPostId: mediaId, commentId: c.id, reason: e instanceof Error ? e.message : String(e), nowMs: now });
            }
            await pace();
          }
        }
        langStatus.push("instagram: ok (nativa)");
      }
    }
    // --- Facebook nativo ---
    {
      const platform: NativePlatform = "facebook";
      const probe = probes[probeKey(platform, lang)];
      const creds = await safeCreds(env, "facebook_reels", tenant, lang);
      if (!creds.facebook || !creds.pageId) {
        langStatus.push("facebook: sin_conexion");
      } else if (probe && probe.state !== "ok") {
        await gateFailed(platform, lang, "reply");
        langStatus.push(`facebook: ${probe.state}${probe.missing_scopes.length ? ` (falta ${probe.missing_scopes.join(", ")})` : ""}`);
      } else {
        const token = creds.facebook;
        const ownName = await fbOwnPageName(token, creds.pageId);
        const ownNames = ownName ? [ownName.replace(/^@/, "").toLowerCase()] : [];
        const mine = posted.filter((j) => j.platform === "facebook_reels" && j.language === lang && j.platform_id).slice(0, MAX_POSTS_PER_RUN);
        // V3: conteo de lo encontrado (en bruto, antes de filtros).
        const fbFound = { comments: 0, postsChecked: 0, postsFailed: 0 };
        summary.found[`${platform}|${lang}`] = fbFound;
        let lastTemplate = -1;
        for (const job of mine) {
          const postId = job.platform_id as string;
          let comments: NativeComment[];
          fbFound.postsChecked++;
          try {
            comments = await fbListComments(token, postId);
          } catch (e) {
            fbFound.postsFailed++;
            summary.skipped.push({ what: `hilo facebook ${postId}`, reason: e instanceof Error ? e.message : String(e) });
            continue;
          }
          fbFound.comments += comments.length;
          for (const c of comments.slice(0, MAX_COMMENTS_PER_POST)) {
            const skip = shouldSkipComment(c.text);
            if (skip) continue;
            if (isOwnComment(c as unknown as Record<string, unknown>, ownNames)) continue;
            if (await hasReplied(db, tenant, c.id)) continue;
            const repliesToday = await countEngagementToday(db, tenant, day, platform, lang, "reply");
            if (repliesToday >= caps.reply) {
              summary.skipped.push({ what: `${platform} ${lang}`, reason: `tope respuestas ${repliesToday}/${caps.reply}${caps.safeMode ? " (modo seguro)" : ""}` });
              break;
            }
            const title = clipTitles.get(postId) ?? null;
            const picked = pickReply(lang, title, c.id, lastTemplate);
            lastTemplate = picked.index;
            try {
              await fbReply(token, c.id, picked.text);
            } catch (e) {
              const msg = e instanceof Error ? e.message : String(e);
              await logEngagement(db, tenant, day, platform, lang, "reply", "error", { targetPostId: postId, commentId: c.id, reason: msg, nowMs: now });
              if (/401|403/i.test(msg)) break;
              continue;
            }
            await markReplied(db, tenant, c.id, platform, now);
            await logEngagement(db, tenant, day, platform, lang, "reply", "ok", { targetPostId: postId, commentId: c.id, nowMs: now });
            summary.replies++;
            try {
              const likesToday = await countEngagementToday(db, tenant, day, platform, lang, "like_comment");
              if (likesToday < caps.like) {
                await fbLikeComment(token, c.id);
                await logEngagement(db, tenant, day, platform, lang, "like_comment", "ok", { targetPostId: postId, commentId: c.id, nowMs: now });
                summary.commentLikes++;
              }
            } catch (e) {
              await logEngagement(db, tenant, day, platform, lang, "like_comment", "skipped", { targetPostId: postId, commentId: c.id, reason: e instanceof Error ? e.message : String(e), nowMs: now });
            }
            await pace();
          }
        }
        langStatus.push("facebook: ok (nativa)");
      }
    }
    // --- Afuera nativo: solo objetivos aportados, solo con scope ---
    // TikTok se deja PENDING para Zernio (no se toca aquí). Con
    // sin_permiso / sin_conexion / sin_sonda el objetivo también
    // queda PENDING (no se quema): tras re-autorizar, la siguiente
    // pasada lo intenta.
    const targets = await listPendingTargets(db, tenant);
    for (const t of targets) {
      if (t.language !== lang) continue;
      if (t.platform === "tiktok") continue;
      const platform = t.platform as NativePlatform;
      const probe = probes[probeKey(platform, lang)];
      const needsLikeScope =
        platform === "instagram" && t.kind === "like" ? INSTAGRAM_LIKE_SCOPE : (NATIVE_REPLY_SCOPE[platform] ?? "");
      const granted = probe?.granted_scopes ?? [];
      if (!probe || probe.state !== "ok" || (needsLikeScope && !granted.includes(needsLikeScope))) {
        const missing = needsLikeScope && !granted.includes(needsLikeScope) ? needsLikeScope : (probe?.missing_scopes ?? [needsLikeScope]).join(", ");
        const reason =
          !probe || probe.state === "sin_conexion"
            ? `sin_conexion: sin cuenta ${platform} en ${lang} (objetivo #${t.id} queda pendiente)`
            : probe.state === "sin_sonda"
              ? `sin_sonda: scopes de ${platform} sin comprobar (objetivo #${t.id} queda pendiente)`
              : probe.state === "sonda_fallida"
                ? `sonda_fallida: ${probe.error ?? "sin detalle"} (objetivo #${t.id} queda pendiente)`
                : `sin_permiso: falta scope ${missing} (objetivo #${t.id} queda pendiente; re-autorizar en Cuentas)`;
        await logEngagement(db, tenant, day, platform, lang, t.kind === "like" ? "outward_like" : "outward_comment", "skipped", { targetPostId: t.post_id, reason, nowMs: now });
        summary.skipped.push({ what: `objetivo #${t.id}`, reason });
        continue;
      }
      if (t.kind === "like") {
        const n = await countEngagementToday(db, tenant, day, platform, lang, "outward_like");
        if (n >= caps.outwardLike) {
          summary.skipped.push({ what: `objetivo #${t.id}`, reason: `tope likes afuera ${n}/${caps.outwardLike}${caps.safeMode ? " (modo seguro: afuera apagado)" : ""}` });
          continue;
        }
        try {
          if (platform === "youtube") {
            const creds = await resolveCreds(env, "youtube_shorts", tenant, lang);
            const access = creds.youtube ? await refreshAccessToken(creds.youtube).catch(() => null) : null;
            if (!access) throw new Error("no se pudo renovar el access token de YouTube");
            await ytLikeVideo(access, t.post_id);
          } else if (platform === "instagram") {
            const creds = await resolveCreds(env, "instagram_reels", tenant, lang);
            if (!creds.instagram) throw new Error("sin token de Instagram");
            await igLikeMedia(creds.instagram, t.post_id);
          } else {
            const creds = await resolveCreds(env, "facebook_reels", tenant, lang);
            if (!creds.facebook) throw new Error("sin token de Facebook");
            await fbLikePost(creds.facebook, t.post_id);
          }
          await logEngagement(db, tenant, day, platform, lang, "outward_like", "ok", { targetPostId: t.post_id, nowMs: now });
          await markTarget(db, tenant, t.id, "done", null, now);
          summary.outwardLikes++;
          await pace();
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          await logEngagement(db, tenant, day, platform, lang, "outward_like", "error", { targetPostId: t.post_id, reason: msg, nowMs: now });
          await markTarget(db, tenant, t.id, "skipped", msg, now);
          summary.skipped.push({ what: `objetivo #${t.id}`, reason: msg });
        }
      } else if (t.kind === "comment") {
        const n = await countEngagementToday(db, tenant, day, platform, lang, "outward_comment");
        if (n >= caps.outwardComment) {
          summary.skipped.push({ what: `objetivo #${t.id}`, reason: `tope comentarios afuera ${n}/${caps.outwardComment}${caps.safeMode ? " (modo seguro: afuera apagado)" : ""}` });
          continue;
        }
        if (!t.comment_text || t.comment_text.trim().length < 10) {
          await markTarget(db, tenant, t.id, "skipped", "comentario afuera sin texto específico", now);
          continue;
        }
        try {
          if (platform === "youtube") {
            const creds = await resolveCreds(env, "youtube_shorts", tenant, lang);
            const access = creds.youtube ? await refreshAccessToken(creds.youtube).catch(() => null) : null;
            if (!access) throw new Error("no se pudo renovar el access token de YouTube");
            await ytCommentVideo(access, t.post_id, t.comment_text.trim());
          } else if (platform === "instagram") {
            const creds = await resolveCreds(env, "instagram_reels", tenant, lang);
            if (!creds.instagram) throw new Error("sin token de Instagram");
            await igCommentMedia(creds.instagram, t.post_id, t.comment_text.trim());
          } else {
            const creds = await resolveCreds(env, "facebook_reels", tenant, lang);
            if (!creds.facebook) throw new Error("sin token de Facebook");
            await fbCommentPost(creds.facebook, t.post_id, t.comment_text.trim());
          }
          await logEngagement(db, tenant, day, platform, lang, "outward_comment", "ok", { targetPostId: t.post_id, nowMs: now });
          await markTarget(db, tenant, t.id, "done", null, now);
          summary.outwardComments++;
          await pace();
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          await logEngagement(db, tenant, day, platform, lang, "outward_comment", "error", { targetPostId: t.post_id, reason: msg, nowMs: now });
          await markTarget(db, tenant, t.id, "skipped", msg, now);
          summary.skipped.push({ what: `objetivo #${t.id}`, reason: msg });
        }
      }
    }
    summary.perLanguage[lang] = langStatus.join("; ");
  }
  summary.ran = true;
  if (opts.saveStatus !== false) {
    const status = {
      via: "nativa",
      at: now,
      replies: summary.replies,
      commentLikes: summary.commentLikes,
      outwardLikes: summary.outwardLikes,
      outwardComments: summary.outwardComments,
      perLanguage: summary.perLanguage,
      skipped: summary.skipped.slice(0, 10),
      found: summary.found,
    };
    await saveSettings(db, tenant, { ...settings, engagement_first_run_at: firstRunAt(settings, now), engagement_last_run_at: now, engagement_last_status: status }).catch(() => undefined);
    await logWorkerEvent(
      db,
      "engagement",
      `Engagement nativo${caps.safeMode ? " (modo seguro 2/2/0/0)" : ""}: ${summary.replies} respuestas, ${summary.commentLikes} likes a comentarios, ${summary.outwardLikes} likes afuera, ${summary.outwardComments} comentarios afuera. ES: ${summary.perLanguage.es ?? "?"} | EN: ${summary.perLanguage.en ?? "?"}`,
      tenant,
    ).catch(() => undefined);
  }
  return summary;
}


// --- Orquestador v2: nativa (YT/IG/FB) + Zernio (solo TikTok) ---

export interface CombinedEngagementSummary extends EngagementRunSummary {
  native: NativeEngagementSummary;
  zernio: EngagementRunSummary;
}

export async function runEngagement(
  env: Env,
  tenant: Tenant,
  opts: { force?: boolean; nowMs?: number; sleepMs?: number } = {},
): Promise<CombinedEngagementSummary> {
  const db = env.DB;
  const now = opts.nowMs ?? Date.now();
  const settings = await getSettings(db, tenant).catch(() => ({} as Record<string, unknown>));
  const eff = effectiveSettings(settings);
  const emptyNative: NativeEngagementSummary = { ran: false, replies: 0, commentLikes: 0, outwardLikes: 0, outwardComments: 0, skipped: [], perLanguage: {}, probes: {}, found: {} };
  const emptyZernio: EngagementRunSummary = { ran: false, replies: 0, commentLikes: 0, outwardLikes: 0, outwardComments: 0, skipped: [], perLanguage: {}, found: {} };
  const combined: CombinedEngagementSummary = {
    ran: false, replies: 0, commentLikes: 0, outwardLikes: 0, outwardComments: 0, skipped: [], perLanguage: {}, found: {},
    native: emptyNative, zernio: emptyZernio,
  };
  if (!eff.engagement_enabled) return { ...combined, reason: "engagement apagado en Ajustes (engagement_enabled=false)" };
  const lastRun = typeof settings.engagement_last_run_at === "number" ? (settings.engagement_last_run_at as number) : 0;
  if (!opts.force && now - lastRun < ENGAGEMENT_MIN_INTERVAL_MS) {
    return { ...combined, reason: "throttle: la pasada de engagement corre como mucho 1 vez por hora" };
  }
  // La nativa NO guarda last_run (saveStatus:false) para que Zernio
  // vea el mismo throttle; el orquestador guarda el combinado.
  const native = await runNativeEngagement(env, tenant, { ...opts, saveStatus: false }).catch((e) => ({
    ...emptyNative, reason: `error nativa: ${e instanceof Error ? e.message : String(e)}`,
  }));
  const zernio = await runZernioEngagement(env, tenant, { ...opts, saveStatus: false, platforms: ["tiktok"] }).catch((e) => ({
    ...emptyZernio, reason: `error zernio: ${e instanceof Error ? e.message : String(e)}`,
  }));
  combined.native = native;
  combined.zernio = zernio;
  combined.replies = native.replies + zernio.replies;
  combined.commentLikes = native.commentLikes + zernio.commentLikes;
  combined.outwardLikes = native.outwardLikes + zernio.outwardLikes;
  combined.outwardComments = native.outwardComments + zernio.outwardComments;
  combined.skipped = [...native.skipped, ...zernio.skipped];
  for (const lang of ["es", "en"] as ClipLanguage[]) {
    const parts = [native.perLanguage[lang], zernio.perLanguage[lang]].filter(Boolean);
    if (parts.length > 0) combined.perLanguage[lang] = parts.join(" | ");
  }
  combined.ran = true;
  combined.found = mergeFoundMaps(native.found, zernio.found);
  const status = {
    via: "nativa+zernio",
    at: now,
    replies: combined.replies,
    commentLikes: combined.commentLikes,
    outwardLikes: combined.outwardLikes,
    outwardComments: combined.outwardComments,
    perLanguage: combined.perLanguage,
    skipped: combined.skipped.slice(0, 10),
    found: combined.found,
  };
  // Preserva la marca de primera pasada (los sub-runners ya la
  // estamparon; el snapshot `settings` es previo a la pasada).
  await saveSettings(db, tenant, { ...settings, engagement_first_run_at: firstRunAt(settings, now), engagement_last_run_at: now, engagement_last_status: status }).catch(() => undefined);
  await logWorkerEvent(
    db,
    "engagement",
    `Engagement v2 (nativa YT/IG/FB + Zernio TikTok): ${combined.replies} respuestas, ${combined.commentLikes} likes a comentarios, ${combined.outwardLikes} likes afuera, ${combined.outwardComments} comentarios afuera. ES: ${combined.perLanguage.es ?? "?"} | EN: ${combined.perLanguage.en ?? "?"}`,
    tenant,
  ).catch(() => undefined);
  return combined;
}
