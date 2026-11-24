// TikTok PÚBLICO vía Zernio (su app TikTok for Business ya está
// auditada; la nuestra no, y el Direct Post propio solo permite
// privado). Docs oficiales (leídas 2026-11-18):
//   https://docs.zernio.com/platforms/tiktok
//   https://docs.zernio.com/posts/create-post
//   https://docs.zernio.com/guides/media-uploads
// Flujo por pasos (UN paso por invocación del cron, progreso en
// jobs.resume_data):
//   upload: POST /v1/media/presign → PUT del MP4 a uploadUrl
//   dryrun: POST /v1/posts con dryRun:true (pre-chequeo de límites,
//           no crea nada ni reclama slot; en cuentas Business
//           siempre devuelve canPublish:true)
//   post:   POST /v1/posts publishNow:true + Idempotency-Key = job id
//   poll:   GET /v1/posts/{zernioPostId} hasta platformPostUrl
// Credenciales (api_accounts, como las demás, cifradas en D1):
//   credential_enc = Zernio API key; extra_enc = {"via":"zernio",
//   "account_id":"<id de cuenta Zernio>"}.
// posted exige platformPostUrl (la URL pública del vídeo). Sin ella,
// processing con reintento por GET. Errores de capacidad/permisos
// (401/403, 402 saldo, "at capacity") son draft con el motivo
// exacto, NUNCA un posted fingido. Solo 429/5xx/red son error
// reintentable.
// NOTA memoria: el PUT a Zernio sube el MP4 entero en una sola
// petición, así que este paso sí lo carga en memoria (arrayBuffer
// desde R2); los clips son de 40–60 s. El Direct Post propio sigue
// subiendo por chunks de 5 MB (ver tiktok.ts).

import type { Env } from "../cloudflare";
import { effectiveSettings, getSettings } from "../db";
import { errorMessage, httpJson, isRetryableStatus } from "./http";
import { captionMarkerReason, hasOwnClipMarker } from "./idempotency";
import type { PublishContext, PublishResult, ResumeData } from "./index";

const API = "https://zernio.com/api/v1";

/** Día America/Santiago (YYYY-MM-DD), igual que jobs.santiagoDayKey
 *  (duplicado aquí para no importar jobs.ts desde el adaptador). */
function todayKey(d = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Santiago", year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}

interface ZernioResume extends ResumeData {
  step: "upload" | "dryrun" | "post" | "poll";
  publicUrl?: string;
  zernioPostId?: string;
}

function zernioError(payload: Record<string, unknown>, fallback: string): string {
  const err = payload.error;
  if (err && typeof err === "object") {
    const m = (err as Record<string, unknown>).message;
    if (typeof m === "string" && m) return m;
    const code = (err as Record<string, unknown>).code;
    if (typeof code === "string" && code) return code;
  }
  if (typeof err === "string" && err) return err;
  if (typeof payload.message === "string" && payload.message) return payload.message;
  if (typeof payload.code === "string" && payload.code) return payload.code;
  return fallback;
}

function isCapacityError(payload: Record<string, unknown>): boolean {
  return /capacity/i.test(zernioError(payload, ""));
}

/** Extrae platformPostUrl de una respuesta de POST/GET /v1/posts:
 *  viene en el nivel superior o en post.platforms[0]. */
function findPlatformPostUrl(payload: Record<string, unknown>): string | undefined {
  if (typeof payload.platformPostUrl === "string") return payload.platformPostUrl;
  const post = payload.post;
  if (post && typeof post === "object") {
    const p = post as Record<string, unknown>;
    if (typeof p.platformPostUrl === "string") return p.platformPostUrl;
    const platforms = p.platforms;
    if (Array.isArray(platforms)) {
      for (const entry of platforms) {
        if (entry && typeof entry === "object") {
          const e = entry as Record<string, unknown>;
          if (typeof e.platformPostUrl === "string" && e.platformPostUrl) return e.platformPostUrl;
        }
      }
    }
  }
  return undefined;
}

/** Id del post Zernio (post.id / post._id / postId), si lo hay. */
function findPostId(payload: Record<string, unknown>): string | undefined {
  const post = payload.post;
  if (post && typeof post === "object") {
    const p = post as Record<string, unknown>;
    if (typeof p.id === "string") return p.id;
    if (typeof p._id === "string") return p._id;
  }
  if (typeof payload.postId === "string") return payload.postId;
  if (typeof payload.id === "string") return payload.id;
  return undefined;
}

/** Motivo de fallo de la entrada TikTok en platforms[] (207 o GET). */
function findPlatformError(payload: Record<string, unknown>): string | undefined {
  const post = payload.post;
  if (post && typeof post === "object") {
    const platforms = (post as Record<string, unknown>).platforms;
    if (Array.isArray(platforms)) {
      for (const entry of platforms) {
        if (entry && typeof entry === "object") {
          const e = entry as Record<string, unknown>;
          if (typeof e.error === "string" && e.error) return e.error;
          if (typeof e.errorMessage === "string" && e.errorMessage) return e.errorMessage;
        }
      }
    }
  }
  return undefined;
}

function postBody(ctx: PublishContext, accountId: string, publicUrl: string, extra: Record<string, unknown>): Record<string, unknown> {
  return {
    // Sin marca [clip:<id>] en el caption (2026-11-23): era visible
    // en público. Solo se garantiza el límite de 2200 caracteres.
    // V4: la marca propia se rechaza ANTES, en las ramas dryrun y
    // post de zernioTiktokStep (hasOwnClipMarker): aquí el caption
    // llega ya validado, nunca con [clip:<id-del-clip>].
    content: ctx.job.caption.slice(0, 2200),
    platforms: [{ platform: "tiktok", accountId }],
    mediaItems: [{ type: "video", url: publicUrl }],
    tiktokSettings: {
      privacy_level: "PUBLIC_TO_EVERYONE",
      content_preview_confirmed: true,
      express_consent_given: true,
    },
    ...extra,
  };
}

export async function zernioTiktokStep(ctx: PublishContext, env: Env): Promise<PublishResult> {
  const apiKey = ctx.creds.tiktok;
  if (!apiKey) return { mode: "draft", reason: "sin clave Zernio en Cuentas. Pégala en la cuenta TikTok (vía Zernio) y verifica." };
  const accountId = ctx.creds.tiktokAccountId;
  if (!accountId) return { mode: "draft", reason: "falta el account_id de Zernio en extra ({\"via\":\"zernio\",\"account_id\":\"...\"})." };
  const resume = (ctx.job.resumeData ?? {}) as ZernioResume;
  const step = resume.step ?? "upload";
  const auth = { Authorization: `Bearer ${apiKey}` };
  const idempotencyKey = String(ctx.job.id);

  // Paso 1: presign + PUT del MP4 completo a Zernio.
  if (step === "upload") {
    const total = ctx.r2Size ?? 0;
    if (!total) return { mode: "error", reason: "no se pudo medir el MP4 en R2", retryable: true };
    const filename = (ctx.job.r2_key ?? `clip-${ctx.job.clip_id ?? ctx.job.id}.mp4`).split("/").pop() ?? `clip-${ctx.job.id}.mp4`;
    const presign = await httpJson("POST", `${API}/media/presign`, auth, { filename, contentType: "video/mp4", size: total });
    const uploadUrl = presign.json.uploadUrl as string | undefined;
    const publicUrl = presign.json.publicUrl as string | undefined;
    if (presign.status === 401 || presign.status === 403) {
      return { mode: "draft", reason: `Zernio rechazó la clave (${presign.status}): pégala de nuevo en Cuentas.` };
    }
    if (presign.status !== 200 || !uploadUrl || !publicUrl) {
      return { mode: "error", reason: `Zernio presign falló: ${zernioError(presign.json, `HTTP ${presign.status}`)}`, retryable: isRetryableStatus(presign.status) };
    }
    const obj = await env.CLIPS_BUCKET.get(ctx.job.r2_key as string);
    if (!obj) return { mode: "error", reason: "el MP4 ya no está en R2", retryable: false };
    const bytes = new Uint8Array(await obj.arrayBuffer());
    const put = await httpJson("PUT", uploadUrl, { "Content-Type": "video/mp4", "Content-Length": String(bytes.length) }, bytes, 120_000);
    if (put.status !== 200 && put.status !== 201) {
      return { mode: "error", reason: `Zernio rechazó la subida del MP4: ${zernioError(put.json, `HTTP ${put.status}`)}`, retryable: isRetryableStatus(put.status) };
    }
    return { mode: "processing", resumeData: { step: "dryrun", publicUrl }, reason: "MP4 subido a Zernio, pre-chequeo pendiente" };
  }

  const publicUrl = resume.publicUrl;
  if (!publicUrl) return { mode: "error", reason: "falta la URL del MP4 en Zernio (resume perdido); se vuelve a subir", retryable: true };

  // Paso 2: dryRun — pre-chequeo de límites sin crear nada.
  if (step === "dryrun") {
    // V4 — el caption con la marca propia se rechaza ANTES de
    // enviarlo a Zernio (el límite de 2200 caracteres no cambia,
    // ver postBody). Sin este chequeo el dryRun enviaría la marca
    // a la plataforma aunque no cree nada.
    if (hasOwnClipMarker(ctx.job.caption, ctx.job.clip_id)) {
      return { mode: "error", reason: captionMarkerReason(ctx.job.clip_id as number), retryable: false };
    }
    const r = await httpJson("POST", `${API}/posts`, auth, postBody(ctx, accountId, publicUrl, { dryRun: true }));
    if (r.status === 401 || r.status === 403) {
      return { mode: "draft", reason: `Zernio rechazó la clave (${r.status}): pégala de nuevo en Cuentas.` };
    }
    if (r.status === 200) {
      if (r.json.canPublish === false) {
        const entries = Array.isArray(r.json.tiktok) ? r.json.tiktok : [];
        const detail = entries
          .map((e) => (e && typeof e === "object" ? ((e as Record<string, unknown>).reason ?? (e as Record<string, unknown>).message ?? (e as Record<string, unknown>).error) : undefined))
          .filter((x): x is string => typeof x === "string" && !!x)
          .join("; ");
        return { mode: "draft", reason: `Zernio dryRun: TikTok no puede publicar ahora${detail ? ` (${detail})` : ""}.` };
      }
      return { mode: "processing", resumeData: { step: "post", publicUrl }, reason: "dryRun ok, publicando" };
    }
    if (r.status === 400 || r.status === 402 || isCapacityError(r.json)) {
      return { mode: "draft", reason: `Zernio no deja publicar: ${zernioError(r.json, `HTTP ${r.status}`)}` };
    }
    return { mode: "error", reason: `Zernio dryRun falló: ${zernioError(r.json, `HTTP ${r.status}`)}`, retryable: isRetryableStatus(r.status) };
  }

  // Paso 3: publicar ahora (Idempotency-Key = job id: reintentos seguros).
  if (step === "post") {
    // V4 — igual que en dryRun: la marca propia no viaja a
    // Zernio. El chequeo va antes de los topes para fallar rápido
    // sin tocar D1; un caption marcado es defecto del dato, no
    // falta de cupo.
    if (hasOwnClipMarker(ctx.job.caption, ctx.job.clip_id)) {
      return { mode: "error", reason: captionMarkerReason(ctx.job.clip_id as number), retryable: false };
    }
    // Topes Zernio desde Ajustes (defaults 15/día y 5 borradores
    // pendientes): se comprueban ANTES de publicar, no después.
    // Son topes de la cuenta Zernio, distintos del tope diario de
    // TikTok del panel (resto_max 3/día, más estricto, ya aplicado
    // por publishGate). POR CUENTA (orden 2026-11-23): TikTok ES y
    // EN son cuentas Zernio distintas, cada una con su propio tope;
    // los conteos llevan filtro por idioma (el del job), igual que
    // el gate (0019). Un fallo de D1 no frena la publicación.
    try {
      const eff = effectiveSettings(await getSettings(env.DB, ctx.job.tenant_id));
      const postedToday = await env.DB.prepare(
        "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = 'tiktok' AND language = ? AND state = 'posted' AND posted_day = ?",
      ).bind(ctx.job.tenant_id, ctx.job.language, todayKey()).first<{ n: number }>();
      if ((postedToday?.n ?? 0) >= eff.tiktok_zernio_daily) {
        return { mode: "draft", reason: `Tope Zernio diario alcanzado: ${postedToday?.n ?? 0}/${eff.tiktok_zernio_daily} publicaciones TikTok ${ctx.job.language} hoy.` };
      }
      const drafts = await env.DB.prepare(
        "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = 'tiktok' AND language = ? AND state = 'draft'",
      ).bind(ctx.job.tenant_id, ctx.job.language).first<{ n: number }>();
      if ((drafts?.n ?? 0) >= eff.tiktok_zernio_pending_drafts) {
        return { mode: "draft", reason: `Demasiados borradores TikTok ${ctx.job.language} pendientes en Zernio: ${drafts?.n ?? 0}/${eff.tiktok_zernio_pending_drafts}. Resuelve o cancela borradores antes de publicar otro.` };
      }
    } catch {
      // Sin topes verificables se sigue: el dryRun ya frena si
      // Zernio no puede publicar.
    }
    const r = await httpJson(
      "POST",
      `${API}/posts`,
      { ...auth, "Idempotency-Key": idempotencyKey },
      postBody(ctx, accountId, publicUrl, { publishNow: true, isDraft: false }),
    );
    if (r.status === 401 || r.status === 403) {
      return { mode: "draft", reason: `Zernio rechazó la clave (${r.status}): pégala de nuevo en Cuentas.` };
    }
    if (r.status === 402 || isCapacityError(r.json)) {
      return { mode: "draft", reason: `Zernio no deja publicar: ${zernioError(r.json, `HTTP ${r.status}`)}` };
    }
    if (r.status === 400) {
      const existing = (r.json.existingPostId ?? (r.json.error && typeof r.json.error === "object" ? (r.json.error as Record<string, unknown>).existingPostId : undefined)) as string | undefined;
      if (existing) return { mode: "processing", resumeData: { step: "poll", publicUrl, zernioPostId: existing }, reason: "Zernio ya tenía este post (duplicado); se verifica su URL" };
      return { mode: "draft", reason: `Zernio rechazó la publicación: ${zernioError(r.json, `HTTP ${r.status}`)}` };
    }
    if (r.status === 409) {
      return { mode: "processing", resumeData: { step: "post", publicUrl }, reason: "Zernio aún procesa el post anterior (idempotency_conflict); se reintenta" };
    }
    if (r.status === 201 || r.status === 202 || r.status === 200 || r.status === 207) {
      const url = findPlatformPostUrl(r.json);
      const postId = findPostId(r.json);
      if (url) {
        return { mode: "posted", platformId: postId ?? url, evidenceUrl: url };
      }
      const platformErr = findPlatformError(r.json);
      if (platformErr) {
        return { mode: "draft", reason: `TikTok rechazó la publicación vía Zernio: ${platformErr}` };
      }
      if (postId) {
        return { mode: "processing", resumeData: { step: "poll", publicUrl, zernioPostId: postId }, reason: "Zernio aceptó el post pero aún no entrega la URL pública; se verifica" };
      }
      return { mode: "error", reason: `Zernio respondió ${r.status} sin postId ni URL: ${errorMessage(r.json, "respuesta vacía")}`, retryable: true };
    }
    return { mode: "error", reason: `Zernio publicar falló: ${zernioError(r.json, `HTTP ${r.status}`)}`, retryable: isRetryableStatus(r.status) };
  }

  // Paso 4: GET /v1/posts/{id} hasta que aparezca platformPostUrl.
  const postId = resume.zernioPostId;
  if (!postId) return { mode: "error", reason: "falta el id del post Zernio (resume perdido); se vuelve a publicar", retryable: true };
  const r = await httpJson("GET", `${API}/posts/${encodeURIComponent(postId)}`, auth);
  if (r.status === 401 || r.status === 403) {
    return { mode: "draft", reason: `Zernio rechazó la clave (${r.status}): pégala de nuevo en Cuentas.` };
  }
  if (r.status !== 200) {
    return { mode: "error", reason: `Zernio no devolvió el post: ${zernioError(r.json, `HTTP ${r.status}`)}`, retryable: isRetryableStatus(r.status) };
  }
  const url = findPlatformPostUrl(r.json);
  if (url) {
    return { mode: "posted", platformId: postId, evidenceUrl: url };
  }
  const post = (r.json.post ?? {}) as Record<string, unknown>;
  const status = typeof post.status === "string" ? post.status : "";
  const platformErr = findPlatformError(r.json);
  if (status === "failed") {
    return { mode: "draft", reason: `TikTok rechazó la publicación vía Zernio: ${platformErr ?? "estado failed sin motivo"}` };
  }
  if (status === "partial" && platformErr) {
    return { mode: "draft", reason: `TikTok rechazó la publicación vía Zernio: ${platformErr}` };
  }
  return { mode: "processing", resumeData: { step: "poll", publicUrl, zernioPostId: postId }, reason: `Zernio sigue procesando el post (${status || "sin estado"})` };
}
