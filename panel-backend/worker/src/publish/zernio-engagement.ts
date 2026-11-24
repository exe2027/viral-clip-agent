// Engagement automático v1 — SOLO Zernio por API oficial (2026-11-23,
// v5.3.0, orden del usuario "hagámoslo por Zernio y SÓLO eso").
//
// Lo que Zernio expone (docs.zernio.com, leído 2026-11-23):
//   Adentro (tus posts):
//     GET  /v1/inbox/comments?platform=  lista posts con comentarios
//     GET  /v1/inbox/comments/{postId}?accountId=  hilo de un post
//     POST /v1/inbox/comments/{postId} {accountId, message, commentId?}
//          reply a un comentario (con commentId) o comentario nuevo
//          en el post (sin commentId)
//     POST /v1/inbox/comments/{postId}/{commentId}/like  like a un
//          comentario (TikTok solo Business; Instagram limited release)
//   Afuera (posts de otros):
//     POST /v1/inbox/posts/{postId}/like {accountId}  like a un post.
//          Soporta Facebook, YouTube, Instagram (limited release) y
//          LinkedIn/X/Bluesky. TikTok, Threads y Pinterest devuelven
//          400: NO hay like a posts de TikTok por API.
//   Cuentas: GET /v1/accounts?platform=  cuentas CONECTADAS (las
//     tuyas). GET /v1/accounts/{id}/posts lista solo TUS posts.
//
// Lo que Zernio NO expone y queda FUERA de v1 (se reporta como
// pendiente, no se implementa por otro camino):
//   - Follow: no hay endpoint de follow para Instagram, Facebook,
//     YouTube ni TikTok (solo POST /v1/twitter/follow, sin cuenta
//     de X en el proyecto). No se sigue a nadie.
//   - Descubrir los últimos posts de un streamer: Zernio no lista
//     posts de terceros. Los objetivos de afuera los aporta el
//     usuario con su URL (POST /api/engagement/targets); sin
//     objetivos en cola, afuera no hace nada.
//   - Comentar en posts de otros sin URL: mismo bloqueo (falta el
//     postId nativo). Solo con objetivo aportado y texto específico.
//   - TikTok hacia afuera: like devuelve 400 PLATFORM_LIMITATION;
//     los objetivos TikTok se marcan skipped sin llamar a Zernio.
//   - Moderar (hide/delete): fuera de alcance; nunca se oculta ni
//     borra un comentario.
//
// Reglas duras: $0; topes POR plataforma+idioma+día (Santiago),
// kill-switch engagement_enabled (default OFF, en Ajustes); nunca
// se responde dos veces al mismo comentario (engagement_replied);
// nunca se responde a tus propios comentarios; nunca un comentario
// genérico ("🔥🔥"); pausa entre escrituras; si algo falla, queda
// en engagement_log / worker_events y la pasada sigue; nunca tumba
// el ciclo de publicación.
//
// Clave: se reutiliza resolveCreds("tiktok") — la Zernio API key
// guardada es la de la fila TikTok via=zernio de cada idioma. Sin
// fila Zernio en ese idioma, ese idioma queda sin_clave_zernio.
// Las cuentas de FB/IG/YT tienen que estar conectadas EN ZERNIO
// (GET /v1/connect/{platform}); las OAuth nativas del panel
// (Meta/Google) NO sirven aquí: si una plataforma no aparece en
// GET /v1/accounts, queda sin_conexion_zernio.

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
import { resolveCreds } from "./index";

const API = "https://zernio.com/api/v1";
export const ENGAGEMENT_MIN_INTERVAL_MS = 3600_000; // 1 pasada/hora
/** Ventana de caché de la sonda de scopes (native-engagement.ts):
 *  1 h. Un veredicto guardado dentro de esta ventana se reutiliza
 *  sin llamar a Google/Meta. */
export const PROBE_CACHE_MS = 3600_000;
const MAX_POSTS_PER_RUN = 10;
const MAX_COMMENTS_PER_POST = 10;
const MAX_TARGETS_PER_RUN = 10;

export type ZernioPlatform = "youtube" | "tiktok" | "instagram" | "facebook";
export const ENGAGEMENT_PLATFORMS: ZernioPlatform[] = ["youtube", "tiktok", "instagram", "facebook"];

// --- Modo seguro (2026-11-23, investigación ban/shadowban) ---
// Hecho: responder comentarios PROPIOS por API oficial a bajo
// volumen es gestión de comunidad permitida (YouTube: solo en tu
// canal; Meta: solo reactivo, nunca iniciar; TikTok: solo vía app
// de Business). Lo que las tres plataformas sancionan como spam /
// fake engagement es lo de AFUERA: comentarios y likes templados
// en posts de terceros, a volumen, y cualquier bot por navegador.
// Por eso las primeras 24 h tras la primera pasada el engagement
// corre en modo seguro: 2 respuestas y 2 likes a comentarios por
// plataforma+idioma+día, y CERO hacia afuera. Pasadas las 24 h,
// mandan los topes configurados (10/10/5/1). No hay nada que
// configurar: la primera pasada marca engagement_first_run_at en
// settings y el modo sale solo. TikTok vía Zernio es la vía menos
// verificada (no consta que Zernio sea TikTok Marketing Partner y
// TikTok no documenta webhook oficial de auto-respuesta a
// comentarios públicos), así que el modo seguro es el que más lo
// protege: 2 respuestas el primer día, solo a comentarios reales
// en posts propios.
export const ENGAGEMENT_SAFE_WINDOW_MS = 24 * 3600_000;
export const ENGAGEMENT_SAFE_REPLY_MAX = 2;
export const ENGAGEMENT_SAFE_LIKE_MAX = 2;

export interface EngagementCaps {
  reply: number;
  like: number;
  outwardLike: number;
  outwardComment: number;
  safeMode: boolean;
}

export function engagementCaps(
  settings: Record<string, unknown>,
  eff: { engagement_reply_daily_max: number; engagement_like_daily_max: number; engagement_outward_like_daily_max: number; engagement_outward_comment_daily_max: number },
  nowMs: number,
): EngagementCaps {
  const first = typeof settings.engagement_first_run_at === "number" ? (settings.engagement_first_run_at as number) : null;
  const safe = first === null || nowMs - first < ENGAGEMENT_SAFE_WINDOW_MS;
  if (!safe) {
    return { reply: eff.engagement_reply_daily_max, like: eff.engagement_like_daily_max, outwardLike: eff.engagement_outward_like_daily_max, outwardComment: eff.engagement_outward_comment_daily_max, safeMode: false };
  }
  return {
    reply: Math.min(ENGAGEMENT_SAFE_REPLY_MAX, eff.engagement_reply_daily_max),
    like: Math.min(ENGAGEMENT_SAFE_LIKE_MAX, eff.engagement_like_daily_max),
    outwardLike: 0,
    outwardComment: 0,
    safeMode: true,
  };
}

/** Marca la primera pasada en settings (si aún no está marcada).
 *  La usan los runners al guardar su estado; el orquestador la
 *  preserva en su guardado combinado. */
export function firstRunAt(settings: Record<string, unknown>, nowMs: number): number {
  return typeof settings.engagement_first_run_at === "number" ? (settings.engagement_first_run_at as number) : nowMs;
}
export type EngagementAction = "reply" | "like_comment" | "outward_like" | "outward_comment";

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

function hashStr(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
}

// --- Respuestas: varias, específicas del clip, nunca genéricas. ---
// {title} se sustituye por el título del clip cuando se conoce;
// si no se conoce, se usa la variante sin título (mismo índice).
export const REPLIES_ES: Array<{ withTitle: string; plain: string }> = [
  { withTitle: "¡Gracias por verlo! Ese momento de «{title}» quedó brutal, ¿cuál fue tu parte favorita?", plain: "¡Gracias por verlo! Ese momento quedó brutal, ¿cuál fue tu parte favorita?" },
  { withTitle: "¡Ese clip de «{title}» es de mis favoritos! El chat enloqueció en ese momento.", plain: "¡Ese clip es de mis favoritos! El chat enloqueció en ese momento." },
  { withTitle: "¡Totalmente! En «{title}» pasó justo lo que nadie esperaba. ¿Lo viste en directo?", plain: "¡Totalmente! Ahí pasó justo lo que nadie esperaba. ¿Lo viste en directo?" },
  { withTitle: "¡Gracias! «{title}» salió del directo, tal cual, sin guion. Se vienen más clips así.", plain: "¡Gracias! Ese clip salió del directo, tal cual, sin guion. Se vienen más clips así." },
  { withTitle: "¡Jajaja sí! La reacción en «{title}» lo dice todo. ¿Qué otro momento quieres ver clippeado?", plain: "¡Jajaja sí! La reacción lo dice todo. ¿Qué otro momento quieres ver clippeado?" },
  { withTitle: "¡Gracias por comentar! «{title}» es GTA 6 en estado puro. ¿De qué streamer quieres el próximo clip?", plain: "¡Gracias por comentar! Esto es GTA 6 en estado puro. ¿De qué streamer quieres el próximo clip?" },
  { withTitle: "¡Ese instante de «{title}» valió todo el directo! Gracias por estar por aquí.", plain: "¡Ese instante valió todo el directo! Gracias por estar por aquí." },
  { withTitle: "¡Sí! En «{title}» ni el streamer se lo creía. Estate atento, que subimos clips todos los días.", plain: "¡Sí! Ni el streamer se lo creía. Estate atento, que subimos clips todos los días." },
];
export const REPLIES_EN: Array<{ withTitle: string; plain: string }> = [
  { withTitle: "Thanks for watching! That moment in «{title}» was insane — what was your favorite part?", plain: "Thanks for watching! That moment was insane — what was your favorite part?" },
  { withTitle: "That clip from «{title}» is one of my favorites! Chat went crazy at that moment.", plain: "That clip is one of my favorites! Chat went crazy at that moment." },
  { withTitle: "Right?! In «{title}» exactly what nobody expected happened. Did you catch it live?", plain: "Right?! Exactly what nobody expected happened there. Did you catch it live?" },
  { withTitle: "Thanks! «{title}» came straight from the stream, unscripted. More clips like this coming.", plain: "Thanks! That clip came straight from the stream, unscripted. More clips like this coming." },
  { withTitle: "Haha yes! The reaction in «{title}» says it all. What other moment should we clip next?", plain: "Haha yes! The reaction says it all. What other moment should we clip next?" },
  { withTitle: "Thanks for commenting! «{title}» is pure GTA 6. Which streamer should we clip next?", plain: "Thanks for commenting! This is pure GTA 6. Which streamer should we clip next?" },
  { withTitle: "That instant in «{title}» made the whole stream worth it! Thanks for stopping by.", plain: "That instant made the whole stream worth it! Thanks for stopping by." },
  { withTitle: "Yes! In «{title}» even the streamer couldn't believe it. We post clips every day, stay tuned.", plain: "Yes! Even the streamer couldn't believe it. We post clips every day, stay tuned." },
];

/** Elige la respuesta por hash del commentId: determinista (el mismo
 *  comentario siempre cae en la misma plantilla) y variada entre
 *  comentarios. `exclude` evita repetir la plantilla anterior en la
 *  misma pasada. */
export function pickReply(lang: ClipLanguage, title: string | null, commentId: string, exclude = -1): { text: string; index: number } {
  const list = lang === "en" ? REPLIES_EN : REPLIES_ES;
  let idx = hashStr(commentId) % list.length;
  if (idx === exclude) idx = (idx + 1) % list.length;
  const t = list[idx] as { withTitle: string; plain: string };
  return { text: title ? t.withTitle.replace("{title}", title) : t.plain, index: idx };
}

/** ¿Hay que saltar este comentario? Vacío, con enlace (spam típico)
 *  o demasiado largo para responder con sentido. */
export function shouldSkipComment(text: unknown): string | null {
  if (typeof text !== "string" || text.trim().length < 2) return "comentario vacío";
  if (text.length > 500) return "comentario demasiado largo";
  if (/https?:\/\/|www\./i.test(text)) return "comentario con enlace";
  return null;
}

/** ¿Es un comentario nuestro? El autor coincide con uno de nuestros
 *  handles (sin @, minúsculas) o el comentario ya trae una respuesta
 *  nuestra (replies con nuestro username). */
export function isOwnComment(comment: Record<string, unknown>, ownUsernames: string[]): boolean {
  const from = (comment.from ?? {}) as Record<string, unknown>;
  const name = typeof from.username === "string" ? from.username.replace(/^@/, "").toLowerCase() : "";
  if (name && ownUsernames.includes(name)) return true;
  const replies = Array.isArray(comment.replies) ? comment.replies : [];
  for (const r of replies) {
    const rf = ((r as Record<string, unknown>).from ?? {}) as Record<string, unknown>;
    const rn = typeof rf.username === "string" ? rf.username.replace(/^@/, "").toLowerCase() : "";
    if (rn && ownUsernames.includes(rn)) return true;
  }
  return false;
}

/** Id nativo del post desde su URL pública (para like/comment por
 *  Zernio, que acepta el id nativo). null si la URL no es de un post. */
export function extractNativePostId(platform: ZernioPlatform, url: string): string | null {
  let m: RegExpExecArray | null;
  if (platform === "tiktok") {
    m = /tiktok\.com\/@[^/]+\/video\/(\d+)/.exec(url);
    return m ? (m[1] as string) : null;
  }
  if (platform === "youtube") {
    m = /youtube\.com\/shorts\/([A-Za-z0-9_-]{6,})/.exec(url) ?? /[?&]v=([A-Za-z0-9_-]{6,})/.exec(url) ?? /youtu\.be\/([A-Za-z0-9_-]{6,})/.exec(url);
    return m ? (m[1] as string) : null;
  }
  if (platform === "instagram") {
    m = /instagram\.com\/(?:reel|p)\/([A-Za-z0-9_-]+)/.exec(url);
    return m ? (m[1] as string) : null;
  }
  m = /facebook\.com\/(?:reel|videos?|[^/]+\/posts?)\/(\d+)/.exec(url);
  return m ? (m[1] as string) : null;
}

function zernioError(payload: Record<string, unknown>, fallback: string): string {
  const err = payload.error;
  if (err && typeof err === "object") {
    const e = err as Record<string, unknown>;
    if (typeof e.message === "string" && e.message) return e.message;
    if (typeof e.code === "string" && e.code) return e.code;
  }
  if (typeof err === "string" && err) return err;
  if (typeof payload.message === "string" && payload.message) return payload.message;
  if (typeof payload.code === "string" && payload.code) return payload.code;
  return fallback;
}

// --- D1: log, dedupe y objetivos (tablas migración 0040) ---

export async function countEngagementToday(
  db: D1Database, tenant: Tenant, day: string, platform: ZernioPlatform, language: ClipLanguage, action: EngagementAction,
): Promise<number> {
  const row = await db
    .prepare(
      "SELECT COUNT(*) AS n FROM engagement_log WHERE tenant_id = ? AND day = ? AND platform = ? AND language = ? AND action = ? AND status = 'ok'",
    )
    .bind(tenant, day, platform, language, action)
    .first<{ n: number }>()
    .catch(() => null);
  return row?.n ?? 0;
}

export async function logEngagement(
  db: D1Database, tenant: Tenant, day: string, platform: ZernioPlatform, language: ClipLanguage,
  action: EngagementAction, status: string, opts: { accountId?: string | null; targetPostId?: string | null; commentId?: string | null; reason?: string | null; nowMs?: number } = {},
): Promise<void> {
  await db
    .prepare(
      "INSERT INTO engagement_log (tenant_id, day, platform, language, account_id, action, target_post_id, comment_id, status, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )
    .bind(tenant, day, platform, language, opts.accountId ?? null, action, opts.targetPostId ?? null, opts.commentId ?? null, status, opts.reason ?? null, opts.nowMs ?? Date.now())
    .run()
    .catch(() => undefined);
}

export async function hasReplied(db: D1Database, tenant: Tenant, commentId: string): Promise<boolean> {
  const row = await db
    .prepare("SELECT comment_id FROM engagement_replied WHERE tenant_id = ? AND comment_id = ?")
    .bind(tenant, commentId)
    .first<{ comment_id: string }>()
    .catch(() => null);
  return row !== null;
}

export async function markReplied(db: D1Database, tenant: Tenant, commentId: string, platform: ZernioPlatform, nowMs: number): Promise<void> {
  await db
    .prepare("INSERT OR IGNORE INTO engagement_replied (tenant_id, comment_id, platform, replied_at) VALUES (?, ?, ?, ?)")
    .bind(tenant, commentId, platform, nowMs)
    .run()
    .catch(() => undefined);
}

export interface EngagementTargetRow {
  id: number;
  platform: string;
  language: string;
  streamer: string;
  post_url: string;
  post_id: string;
  kind: string;
  comment_text: string | null;
  status: string;
  reason: string | null;
  created_at: number;
}

export async function listPendingTargets(db: D1Database, tenant: Tenant, limit = MAX_TARGETS_PER_RUN): Promise<EngagementTargetRow[]> {
  const res = await db
    .prepare("SELECT * FROM engagement_targets WHERE tenant_id = ? AND status = 'pending' ORDER BY created_at ASC LIMIT ?")
    .bind(tenant, limit)
    .all<EngagementTargetRow>()
    .catch(() => ({ results: [] as EngagementTargetRow[] }));
  return res.results;
}

export async function listTargets(db: D1Database, tenant: Tenant, limit = 50): Promise<EngagementTargetRow[]> {
  const res = await db
    .prepare("SELECT * FROM engagement_targets WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?")
    .bind(tenant, limit)
    .all<EngagementTargetRow>()
    .catch(() => ({ results: [] as EngagementTargetRow[] }));
  return res.results;
}

export async function markTarget(db: D1Database, tenant: Tenant, id: number, status: string, reason: string | null, nowMs: number): Promise<void> {
  await db
    .prepare("UPDATE engagement_targets SET status = ?, reason = ?, updated_at = ? WHERE id = ? AND tenant_id = ?")
    .bind(status, reason, nowMs, id, tenant)
    .run()
    .catch(() => undefined);
}

// --- Cliente Zernio (solo estos endpoints; nada más) ---

export async function zernioListAccounts(apiKey: string): Promise<Array<{ id: string; platform: string; username?: string }>> {
  const r = await httpJson("GET", `${API}/accounts`, { Authorization: `Bearer ${apiKey}` });
  if (r.status !== 200) throw new Error(`Zernio cuentas: HTTP ${r.status}: ${zernioError(r.json, "sin detalle")}`);
  const arr = Array.isArray(r.json.accounts) ? r.json.accounts : [];
  return arr
    .map((a) => {
      const o = a as Record<string, unknown>;
      const id = typeof o._id === "string" ? o._id : typeof o.id === "string" ? o.id : "";
      const platform = typeof o.platform === "string" ? o.platform : "";
      const username = typeof o.username === "string" ? o.username : typeof o.accountUsername === "string" ? o.accountUsername : undefined;
      return { id, platform, username };
    })
    .filter((a) => a.id && a.platform);
}

interface InboxRow {
  id: string;
  platform: string;
  accountId: string;
  commentCount: number;
}

export async function zernioListInbox(apiKey: string, platform: ZernioPlatform): Promise<InboxRow[]> {
  const r = await httpJson("GET", `${API}/inbox/comments?platform=${platform}`, { Authorization: `Bearer ${apiKey}` });
  if (r.status !== 200) throw new Error(`Zernio inbox ${platform}: HTTP ${r.status}: ${zernioError(r.json, "sin detalle")}`);
  const rows = Array.isArray(r.json.data) ? r.json.data : Array.isArray(r.json.rows) ? r.json.rows : [];
  return (rows as Array<Record<string, unknown>>)
    .map((o) => ({
      id: typeof o.id === "string" ? o.id : typeof o._id === "string" ? o._id : "",
      platform: typeof o.platform === "string" ? o.platform : platform,
      accountId: typeof o.accountId === "string" ? o.accountId : "",
      commentCount: typeof o.commentCount === "number" ? o.commentCount : 0,
    }))
    .filter((o) => o.id);
}

export async function zernioGetComments(apiKey: string, postId: string, accountId: string): Promise<Array<Record<string, unknown>>> {
  const r = await httpJson("GET", `${API}/inbox/comments/${encodeURIComponent(postId)}?accountId=${encodeURIComponent(accountId)}`, { Authorization: `Bearer ${apiKey}` });
  if (r.status !== 200) throw new Error(`Zernio comentarios: HTTP ${r.status}: ${zernioError(r.json, "sin detalle")}`);
  const comments = Array.isArray(r.json.comments) ? r.json.comments : Array.isArray(r.json.data) ? r.json.data : [];
  return comments as Array<Record<string, unknown>>;
}

async function zernioReply(apiKey: string, postId: string, accountId: string, message: string, commentId?: string): Promise<void> {
  const body: Record<string, unknown> = { accountId, message };
  if (commentId) body.commentId = commentId;
  const r = await httpJson("POST", `${API}/inbox/comments/${encodeURIComponent(postId)}`, { Authorization: `Bearer ${apiKey}` }, body);
  if (r.status !== 200 && r.status !== 201) throw new Error(`Zernio responder: HTTP ${r.status}: ${zernioError(r.json, "sin detalle")}`);
}

async function zernioLikeComment(apiKey: string, postId: string, accountId: string, commentId: string): Promise<void> {
  const r = await httpJson("POST", `${API}/inbox/comments/${encodeURIComponent(postId)}/${encodeURIComponent(commentId)}/like`, { Authorization: `Bearer ${apiKey}` }, { accountId });
  if (r.status !== 200 && r.status !== 201) throw new Error(`Zernio like comentario: HTTP ${r.status}: ${zernioError(r.json, "sin detalle")}`);
}

async function zernioLikePost(apiKey: string, postId: string, accountId: string): Promise<void> {
  const r = await httpJson("POST", `${API}/inbox/posts/${encodeURIComponent(postId)}/like`, { Authorization: `Bearer ${apiKey}` }, { accountId });
  if (r.status !== 200 && r.status !== 201) throw new Error(`Zernio like post: HTTP ${r.status}: ${zernioError(r.json, "sin detalle")}`);
}

/** Títulos de clips publicados, por id nativo de plataforma, para
 *  personalizar la respuesta. Solo jobs posted (listPostedJobs).
 *  Lo usan la pasada Zernio y la nativa. */
export async function loadClipTitles(db: D1Database, tenant: Tenant): Promise<Map<string, string>> {
  const posted = await listPostedJobs(db, tenant).catch(() => []);
  const clipTitles = new Map<string, string>();
  for (const j of posted) {
    if (!clipTitles.has(j.platform_id)) {
      const clip = await db.prepare("SELECT title FROM clips WHERE id = ? AND tenant_id = ?").bind(j.clip_id, tenant).first<{ title: string }>().catch(() => null);
      if (clip?.title) clipTitles.set(j.platform_id, clip.title);
    }
  }
  return clipTitles;
}

export interface EngagementEventRow {
  id: number;
  day: string;
  platform: string;
  language: string;
  action: string;
  target_post_id: string | null;
  comment_id: string | null;
  status: string;
  reason: string | null;
  created_at: number;
}

/** Últimos eventos de engagement_log (para la sección del panel). */
export async function listEngagementEvents(db: D1Database, tenant: Tenant, limit = 20): Promise<EngagementEventRow[]> {
  const res = await db
    .prepare("SELECT * FROM engagement_log WHERE tenant_id = ? ORDER BY created_at DESC, id DESC LIMIT ?")
    .bind(tenant, limit)
    .all<EngagementEventRow>()
    .catch(() => ({ results: [] as EngagementEventRow[] }));
  return res.results;
}

// --- Estado para el panel ---

export interface EngagementStatus {
  enabled: boolean;
  reply_daily_max: number;
  like_daily_max: number;
  outward_like_daily_max: number;
  outward_comment_daily_max: number;
  /** Modo seguro: true las primeras 24 h tras la primera pasada
   *  (topes 2/2/0/0). Ver engagementCaps. */
  safe_mode: boolean;
  safe_caps: { reply: number; like: number; outward_like: number; outward_comment: number };
  last_run_at: number | null;
  last_status: Record<string, unknown> | null;
  today: Record<string, Record<string, number>>;
  pending_targets: number;
  note: string;
  /** Estado de la vía nativa por plataforma+idioma (sonda de scopes
   *  guardada en settings.engagement_scopes; sin sonda aún, todos
   *  en sin_sonda). TikTok no tiene vía nativa: su estado es
   *  sin_veredicto (la sonda lo declara explícito; el estado real
   *  está en Cuentas, vía Zernio). */
  native: Record<string, { via: string; state: string; missing_scopes: string[]; granted_count: number; checked_at: number | null; error: string | null }>;
  scopes_checked_at: number | null;
  /** true si la sonda guardada sigue dentro de su ventana de caché
   *  de 1 h (misma ventana que PROBE_CACHE_MS): el veredicto puede
   *  ser anterior al último cambio de credenciales. */
  scopes_cached: boolean;
  events: EngagementEventRow[];
}

export async function getEngagementStatus(env: Env, tenant: Tenant): Promise<EngagementStatus> {
  const db = env.DB;
  const settings = await getSettings(db, tenant).catch(() => ({} as Record<string, unknown>));
  const eff = effectiveSettings(settings);
  const day = santiagoDayKey();
  const today: Record<string, Record<string, number>> = {};
  for (const p of ENGAGEMENT_PLATFORMS) {
    for (const lang of ["es", "en"] as ClipLanguage[]) {
      const replies = await countEngagementToday(db, tenant, day, p, lang, "reply");
      const likes = await countEngagementToday(db, tenant, day, p, lang, "like_comment");
      const outLikes = await countEngagementToday(db, tenant, day, p, lang, "outward_like");
      const outComments = await countEngagementToday(db, tenant, day, p, lang, "outward_comment");
      if (replies + likes + outLikes + outComments > 0) today[`${p}|${lang}`] = { replies, likes, outward_likes: outLikes, outward_comments: outComments };
    }
  }
  const pending = await listPendingTargets(db, tenant, 1000);
  const lastStatus = (settings.engagement_last_status ?? null) as Record<string, unknown> | null;
  const stored = (settings.engagement_scopes ?? {}) as Record<string, { state?: string; granted_scopes?: string[]; missing_scopes?: string[]; checked_at?: number | null; error?: string | null }>;
  const scopesCheckedAt = typeof settings.engagement_scopes_at === "number" ? (settings.engagement_scopes_at as number) : null;
  const native: EngagementStatus["native"] = {};
  for (const p of ["youtube", "instagram", "facebook"] as const) {
    for (const lang of ["es", "en"] as const) {
      const rec = stored[`${p}|${lang}`];
      native[`${p}|${lang}`] = {
        via: "nativa",
        state: rec?.state ?? "sin_sonda",
        missing_scopes: rec?.missing_scopes ?? [],
        granted_count: Array.isArray(rec?.granted_scopes) ? (rec.granted_scopes as string[]).length : 0,
        checked_at: typeof rec?.checked_at === "number" ? (rec.checked_at as number) : null,
        error: typeof rec?.error === "string" ? (rec.error as string) : null,
      };
    }
  }
  for (const lang of ["es", "en"] as const) {
    // V1: TikTok declara sin_veredicto (la sonda no lo cubre). Si
    // hay sonda guardada se lee de ahí; si no, el mismo veredicto
    // por defecto. Nunca "zernio" como estado: eso no es un
    // veredicto y el panel lo mostraba como si lo fuera.
    const rec = stored[`tiktok|${lang}`];
    native[`tiktok|${lang}`] = {
      via: "zernio",
      state: rec?.state ?? "sin_veredicto",
      missing_scopes: rec?.missing_scopes ?? [],
      granted_count: Array.isArray(rec?.granted_scopes) ? (rec.granted_scopes as string[]).length : 0,
      checked_at: typeof rec?.checked_at === "number" ? (rec.checked_at as number) : null,
      error: typeof rec?.error === "string" ? (rec.error as string) : "la sonda no cubre TikTok: el estado real está en Cuentas (vía Zernio)",
    };
  }
  const events = await listEngagementEvents(db, tenant, 20);
  const caps = engagementCaps(settings, eff, Date.now());
  return {
    enabled: eff.engagement_enabled,
    reply_daily_max: eff.engagement_reply_daily_max,
    like_daily_max: eff.engagement_like_daily_max,
    outward_like_daily_max: eff.engagement_outward_like_daily_max,
    outward_comment_daily_max: eff.engagement_outward_comment_daily_max,
    safe_mode: caps.safeMode,
    safe_caps: { reply: caps.reply, like: caps.like, outward_like: caps.outwardLike, outward_comment: caps.outwardComment },
    last_run_at: typeof settings.engagement_last_run_at === "number" ? (settings.engagement_last_run_at as number) : null,
    last_status: lastStatus,
    today,
    pending_targets: pending.length,
    note: "Vía nativa (YouTube, Instagram y Facebook con tus tokens OAuth) + Zernio solo para TikTok. Sin follow en ninguna vía, sin TikTok hacia afuera (su like devuelve 400) y sin descubrir posts de terceros: los objetivos de afuera se aportan con su URL. Si una plataforma marca sin_permiso, re-autoriza en Cuentas para conceder el scope que falta.",
    native,
    scopes_checked_at: scopesCheckedAt,
    scopes_cached: scopesCheckedAt !== null && Date.now() - scopesCheckedAt < PROBE_CACHE_MS,
    events,
  };
}

// --- Pasada de engagement ---

/** Conteo de lo encontrado en una pasada, por plataforma+idioma
 *  (clave `${platform}|${lang}`). V3 (validadores 2026-11-23): sin
 *  este conteo un 0/0 no se puede interpretar. `comments` = comentarios
 *  listados en bruto (antes de filtros: vacíos, propios, ya
 *  respondidos o tope); `postsChecked` = posts cuyo listado se
 *  intentó; `postsFailed` = posts (o el inbox entero, = 1) cuyo
 *  listado falló. 0/0 con comments=0 es "sin trabajo"; 0/0 con
 *  comments>0 y 0 acciones es fallo y debe decir por qué (skipped);
 *  postsFailed>0 con comments=0 es "no se pudo listar", no "sin
 *  comentarios". Vive agregado en el summary y en
 *  engagement_last_status: nunca una fila por comentario (D1). */
export interface EngagementFound {
  comments: number;
  postsChecked: number;
  postsFailed: number;
}

export type EngagementFoundMap = Record<string, EngagementFound>;

export function mergeFoundMaps(a: EngagementFoundMap, b: EngagementFoundMap): EngagementFoundMap {
  const out: EngagementFoundMap = {};
  for (const [k, v] of [...Object.entries(a), ...Object.entries(b)]) {
    const prev = out[k] ?? { comments: 0, postsChecked: 0, postsFailed: 0 };
    out[k] = { comments: prev.comments + v.comments, postsChecked: prev.postsChecked + v.postsChecked, postsFailed: prev.postsFailed + v.postsFailed };
  }
  return out;
}

export interface EngagementRunSummary {
  ran: boolean;
  reason?: string;
  replies: number;
  commentLikes: number;
  outwardLikes: number;
  outwardComments: number;
  skipped: Array<{ what: string; reason: string }>;
  perLanguage: Record<string, string>;
  /** V3: lo encontrado por plataforma+idioma (ver EngagementFound). */
  found: EngagementFoundMap;
}

export async function runZernioEngagement(
  env: Env,
  tenant: Tenant,
  opts: { force?: boolean; nowMs?: number; sleepMs?: number; platforms?: ZernioPlatform[]; saveStatus?: boolean } = {},
): Promise<EngagementRunSummary> {
  // v5.4.0: el orquestador (runEngagement) limita Zernio a TikTok;
  // IG/FB/YT van por vía nativa. Llamado directo, sigue cubriendo
  // las 4 plataformas como en v5.3.0. saveStatus:false lo usa el
  // orquestador para guardar un único estado combinado.
  const db = env.DB;
  const now = opts.nowMs ?? Date.now();
  const day = santiagoDayKey(new Date(now));
  const summary: EngagementRunSummary = { ran: false, replies: 0, commentLikes: 0, outwardLikes: 0, outwardComments: 0, skipped: [], perLanguage: {}, found: {} };
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
  // Títulos de clips publicados, por id nativo, para personalizar la
  // respuesta. Si no se encuentra, la respuesta va sin título.
  const clipTitles = await loadClipTitles(db, tenant);
  for (const lang of ["es", "en"] as ClipLanguage[]) {
    try {
      // La clave Zernio vive en la fila TikTok via=zernio de ESE
      // idioma (resolveCreds: por plataforma+idioma, sin fallback
      // al otro idioma, como en publicación).
      const creds = await resolveCreds(env, "tiktok", tenant, lang);
      if (!creds.tiktok) {
        summary.perLanguage[lang] = "sin_clave_zernio: sin cuenta TikTok via Zernio en este idioma";
        continue;
      }
      const apiKey = creds.tiktok;
      let accounts: Array<{ id: string; platform: string; username?: string }>;
      try {
        accounts = await zernioListAccounts(apiKey);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        summary.perLanguage[lang] = `sin_permiso_zernio: ${msg}`;
        summary.skipped.push({ what: `cuentas ${lang}`, reason: msg });
        continue;
      }
      const byPlatform = new Map<string, { id: string; username?: string }>();
      for (const a of accounts) if (!byPlatform.has(a.platform)) byPlatform.set(a.platform, { id: a.id, username: a.username });
      const ownNames = accounts.map((a) => (a.username ?? "").replace(/^@/, "").toLowerCase()).filter(Boolean);
      const langStatus: string[] = [];
      // --- Adentro: comentarios en TUS posts ---
      const runPlatforms = opts.platforms ?? ENGAGEMENT_PLATFORMS;
      for (const platform of runPlatforms) {
        const acc = byPlatform.get(platform);
        if (!acc) {
          langStatus.push(`${platform}: sin_conexion_zernio`);
          continue;
        }
        // V3: conteo de lo encontrado (en bruto, antes de filtros).
        const zFound = { comments: 0, postsChecked: 0, postsFailed: 0 };
        summary.found[`${platform}|${lang}`] = zFound;
        let rows: InboxRow[];
        try {
          rows = await zernioListInbox(apiKey, platform);
        } catch (e) {
          // El listado entero falló: no es "sin comentarios", es
          // "no se pudo listar" (postsFailed 1 = el inbox).
          zFound.postsFailed = 1;
          langStatus.push(`${platform}: inbox falló (${e instanceof Error ? e.message : String(e)})`);
          summary.skipped.push({ what: `inbox ${platform} ${lang}`, reason: e instanceof Error ? e.message : String(e) });
          continue;
        }
        // Solo filas de NUESTRA cuenta en Zernio: el inbox agrega
        // todas las cuentas del perfil; una fila de otra cuenta no
        // es un post nuestro.
        const mine = rows.filter((r) => r.accountId === acc.id).slice(0, MAX_POSTS_PER_RUN);
        let lastTemplate = -1;
        for (const row of mine) {
          zFound.postsChecked++;
          if (row.commentCount === 0) continue;
          let comments: Array<Record<string, unknown>>;
          try {
            comments = await zernioGetComments(apiKey, row.id, acc.id);
          } catch (e) {
            zFound.postsFailed++;
            summary.skipped.push({ what: `hilo ${platform} ${row.id}`, reason: e instanceof Error ? e.message : String(e) });
            continue;
          }
          zFound.comments += comments.length;
          for (const c of comments.slice(0, MAX_COMMENTS_PER_POST)) {
            const commentId = typeof c.id === "string" ? c.id : typeof c._id === "string" ? c._id : "";
            if (!commentId) continue;
            const text = typeof c.text === "string" ? c.text : typeof c.message === "string" ? c.message : "";
            const skip = shouldSkipComment(text);
            if (skip) continue;
            if (isOwnComment(c, ownNames)) continue;
            if (await hasReplied(db, tenant, commentId)) continue;
            const repliesToday = await countEngagementToday(db, tenant, day, platform, lang, "reply");
            if (repliesToday >= caps.reply) {
              summary.skipped.push({ what: `${platform} ${lang}`, reason: `tope respuestas ${repliesToday}/${caps.reply}${caps.safeMode ? " (modo seguro)" : ""}` });
              break;
            }
            // Título del clip si el hilo trae el id nativo del post.
            const nativeIds = [c, row].flatMap((o) => {
              const vals: string[] = [];
              for (const k of ["platformPostId", "videoId", "mediaId", "postId"]) {
                const v = (o as Record<string, unknown>)[k];
                if (typeof v === "string" && v) vals.push(v);
              }
              return vals;
            });
            const title = nativeIds.map((id) => clipTitles.get(id)).find(Boolean) ?? null;
            const picked = pickReply(lang, title, commentId, lastTemplate);
            lastTemplate = picked.index;
            try {
              await zernioReply(apiKey, row.id, acc.id, picked.text, commentId);
            } catch (e) {
              const msg = e instanceof Error ? e.message : String(e);
              await logEngagement(db, tenant, day, platform, lang, "reply", "error", { accountId: acc.id, targetPostId: row.id, commentId, reason: msg, nowMs: now });
              if (/401|403|PLATFORM_LIMITATION|PLATFORM_BETA_RESTRICTED/i.test(msg)) break;
              continue;
            }
            await markReplied(db, tenant, commentId, platform, now);
            await logEngagement(db, tenant, day, platform, lang, "reply", "ok", { accountId: acc.id, targetPostId: row.id, commentId, nowMs: now });
            summary.replies++;
            // Like al comentario: best effort (IG limited release
            // puede dar 403). No deshace la respuesta si falla.
            try {
              const likesToday = await countEngagementToday(db, tenant, day, platform, lang, "like_comment");
              if (likesToday < caps.like) {
                await zernioLikeComment(apiKey, row.id, acc.id, commentId);
                await logEngagement(db, tenant, day, platform, lang, "like_comment", "ok", { accountId: acc.id, targetPostId: row.id, commentId, nowMs: now });
                summary.commentLikes++;
              }
            } catch (e) {
              await logEngagement(db, tenant, day, platform, lang, "like_comment", "skipped", { accountId: acc.id, targetPostId: row.id, commentId, reason: e instanceof Error ? e.message : String(e), nowMs: now });
            }
            await pace();
          }
        }
      }
      // --- Afuera: solo objetivos aportados con URL ---
      const targets = await listPendingTargets(db, tenant);
      for (const t of targets) {
        if (t.language !== lang) continue;
        // Con platforms limitado (v2: solo TikTok), los objetivos de
        // las demás plataformas los atiende la pasada nativa: se
        // dejan PENDING, no se tocan.
        if (!(opts.platforms ?? ENGAGEMENT_PLATFORMS).includes(t.platform as ZernioPlatform)) continue;
        const platform = t.platform as ZernioPlatform;
        const acc = byPlatform.get(platform);
        if (!acc) {
          await markTarget(db, tenant, t.id, "skipped", "sin_conexion_zernio: la cuenta no está conectada en Zernio", now);
          summary.skipped.push({ what: `objetivo #${t.id}`, reason: "sin_conexion_zernio" });
          continue;
        }
        if (platform === "tiktok") {
          // Like post TikTok devuelve 400 en Zernio; comentar en
          // posts de terceros tampoco lo cubre la Comments API para
          // TikTok fuera del inbox propio. No se llama a Zernio.
          await markTarget(db, tenant, t.id, "skipped", "tiktok_sin_like_zernio: Zernio Like post devuelve 400 en TikTok", now);
          summary.skipped.push({ what: `objetivo #${t.id}`, reason: "tiktok_sin_like_zernio" });
          continue;
        }
        if (t.kind === "like") {
          const n = await countEngagementToday(db, tenant, day, platform, lang, "outward_like");
          if (n >= caps.outwardLike) {
            summary.skipped.push({ what: `objetivo #${t.id}`, reason: `tope likes afuera ${n}/${caps.outwardLike}${caps.safeMode ? " (modo seguro: afuera apagado)" : ""}` });
            continue;
          }
          try {
            await zernioLikePost(apiKey, t.post_id, acc.id);
            await logEngagement(db, tenant, day, platform, lang, "outward_like", "ok", { accountId: acc.id, targetPostId: t.post_id, nowMs: now });
            await markTarget(db, tenant, t.id, "done", null, now);
            summary.outwardLikes++;
            await pace();
          } catch (e) {
            const msg = e instanceof Error ? e.message : String(e);
            await logEngagement(db, tenant, day, platform, lang, "outward_like", "error", { accountId: acc.id, targetPostId: t.post_id, reason: msg, nowMs: now });
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
            await zernioReply(apiKey, t.post_id, acc.id, t.comment_text.trim());
            await logEngagement(db, tenant, day, platform, lang, "outward_comment", "ok", { accountId: acc.id, targetPostId: t.post_id, nowMs: now });
            await markTarget(db, tenant, t.id, "done", null, now);
            summary.outwardComments++;
            await pace();
          } catch (e) {
            const msg = e instanceof Error ? e.message : String(e);
            await logEngagement(db, tenant, day, platform, lang, "outward_comment", "error", { accountId: acc.id, targetPostId: t.post_id, reason: msg, nowMs: now });
            await markTarget(db, tenant, t.id, "skipped", msg, now);
            summary.skipped.push({ what: `objetivo #${t.id}`, reason: msg });
          }
        }
      }
      summary.perLanguage[lang] = langStatus.length > 0 ? langStatus.join("; ") : "ok";
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      summary.perLanguage[lang] = `error: ${msg}`;
      summary.skipped.push({ what: `pasada ${lang}`, reason: msg });
    }
  }
  summary.ran = true;
  if (opts.saveStatus !== false) {
    const status = {
      via: opts.platforms ? "zernio-limitado" : "zernio",
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
      `Engagement Zernio${caps.safeMode ? " (modo seguro 2/2/0/0)" : ""}: ${summary.replies} respuestas, ${summary.commentLikes} likes a comentarios, ${summary.outwardLikes} likes afuera, ${summary.outwardComments} comentarios afuera. ES: ${summary.perLanguage.es ?? "?"} | EN: ${summary.perLanguage.en ?? "?"}`,
      tenant,
    ).catch(() => undefined);
  }
  return summary;
}
