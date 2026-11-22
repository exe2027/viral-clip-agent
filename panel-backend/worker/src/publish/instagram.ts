// Instagram Reels por Graph API.
// Docs: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/
// Flujo: crear contenedor (media_type=REELS, video_url HTTPS pública)
// → consultar estado (FINISHED) → publicar (media_publish).
// Meta descarga el video desde una URL FIRMADA del Worker
// (/api/public-video, 15 min, solo ese objeto): el bucket R2 es
// privado y ya no hay dominio público r2.dev. La URL se firma de
// nuevo en cada intento de crear el contenedor, nunca se guarda.
// El contenedor caduca a las 24 h sin publicar.

import { errorMessage, httpJson, isRetryableStatus } from "./http";
import type { PublishContext, PublishResult, ResumeData } from "./index";
import type { Env } from "../cloudflare";
import { signVideoUrl } from "../r2";

const GRAPH_VERSION = "v26.0";

/** Permalink real del reel: Instagram usa shortcode
 *  (/p/Df5iORvnD_x/), NO el id numérico (/reel/<id>/ da "página
 *  no disponible"). Se lee de Graph tras publicar; si falla, se
 *  usa el /reel/ como último recurso. */
async function igPermalink(host: string, mediaId: string, token: string): Promise<string> {
  const fallback = `https://www.instagram.com/reel/${mediaId}/`;
  try {
    const r = await httpJson("GET", `${host}/${mediaId}?fields=permalink&access_token=${encodeURIComponent(token)}`);
    if (r.status === 200 && typeof r.json.permalink === "string" && r.json.permalink) return r.json.permalink as string;
  } catch {
    // cae al fallback
  }
  return fallback;
}

interface IgResume extends ResumeData {
  step: "container" | "status";
  containerId?: string;
}

export async function instagramStep(ctx: PublishContext, env: Env): Promise<PublishResult> {
  const token = ctx.creds.instagram;
  if (!token) return { mode: "draft", reason: "sin token de Instagram en Cuentas." };
  const igUserId = ctx.creds.igUserId;
  if (!igUserId) return { mode: "draft", reason: "sin ig_user_id configurado (Cuentas → Instagram)." };
  if (!ctx.job.r2_key) return { mode: "draft", reason: "el clip no tiene MP4 en R2." };
  // URL firmada del Worker (15 min, solo este objeto): se firma
  // de nuevo en cada intento, nunca se guarda en resume_data.
  const videoUrl = await signVideoUrl(env, ctx.job.r2_key);
  if (!videoUrl) return { mode: "draft", reason: "no se pudo firmar la URL del video para Instagram (revisar SESSION_SECRET)." };
  const host = `https://graph.facebook.com/${GRAPH_VERSION}`;
  const resume = (ctx.job.resumeData ?? {}) as IgResume;

  if (!resume.step || resume.step === "container") {
    const params = new URLSearchParams({ media_type: "REELS", video_url: videoUrl, caption: ctx.job.caption, access_token: token });
    const r = await httpJson("POST", `${host}/${igUserId}/media?${params.toString()}`);
    if (r.status === 200 && typeof r.json.id === "string") {
      return { mode: "processing", resumeData: { step: "status", containerId: r.json.id }, reason: "contenedor creado, esperando a Meta" };
    }
    return { mode: "error", reason: `Instagram no creó el contenedor: ${errorMessage(r.json, `HTTP ${r.status}`)}`, retryable: isRetryableStatus(r.status) };
  }

  const st = await httpJson("GET", `${host}/${resume.containerId}?fields=status_code&access_token=${encodeURIComponent(token)}`);
  if (st.status !== 200) {
    return { mode: "error", reason: `Instagram no devolvió el estado: ${errorMessage(st.json, `HTTP ${st.status}`)}`, retryable: isRetryableStatus(st.status) };
  }
  const code = st.json.status_code as string;
  if (code === "FINISHED") {
    const params = new URLSearchParams({ creation_id: resume.containerId as string, access_token: token });
    const pub = await httpJson("POST", `${host}/${igUserId}/media_publish?${params.toString()}`);
    if (pub.status === 200 && typeof pub.json.id === "string") {
      return { mode: "posted", platformId: pub.json.id, evidenceUrl: await igPermalink(host, pub.json.id, token) };
    }
    return { mode: "error", reason: `Instagram no publicó: ${errorMessage(pub.json, `HTTP ${pub.status}`)}`, retryable: isRetryableStatus(pub.status) };
  }
  if (code === "ERROR") return { mode: "error", reason: "Instagram: el contenedor quedó en ERROR (video no procesable)", retryable: false };
  if (code === "EXPIRED") return { mode: "error", reason: "Instagram: el contenedor caducó (24 h sin publicar)", retryable: false };
  if (code === "PUBLISHED") {
    return { mode: "posted", platformId: resume.containerId as string, evidenceUrl: await igPermalink(host, resume.containerId as string, token) };
  }
  return { mode: "processing", resumeData: { step: "status", containerId: resume.containerId }, reason: `Instagram sigue procesando (${code ?? "sin estado"})` };
}
