// TikTok Direct Post por Content Posting API.
// Docs: https://developers.tiktok.com/doc/content-posting-api-get-started
// Flujo: creator_info (opciones de privacidad) → init FILE_UPLOAD →
// PUT por chunks de 5 MB → status/fetch hasta PUBLISH_COMPLETE.
// Sin auditoría de la app, TikTok solo permite publicar en privado
// (error unaudited_client_can_only_post_to_private_accounts): en ese
// caso el resultado es draft con el motivo, NUNCA un posted privado
// fingido como público. El post_id público solo llega tras la
// moderación; posted exige ese post_id (evidence_url).

import type { Env } from "../cloudflare";
import { evidenceUrl } from "../db";
import { errorMessage, httpJson, isRetryableStatus } from "./http";
import { appendClipMarker } from "./idempotency";
import type { PublishContext, PublishResult, ResumeData } from "./index";

const API = "https://open.tiktokapis.com";
const CHUNK = 5 * 1024 * 1024;

interface TiktokResume extends ResumeData {
  step: "creator" | "init" | "upload" | "status";
  publishId?: string;
  uploadUrl?: string;
  chunkSize?: number;
  bytesUploaded?: number;
  totalBytes?: number;
  privacyLevel?: string;
}

async function readStream(stream: ReadableStream, expected: number): Promise<Uint8Array> {
  const reader = stream.getReader();
  const parts: Uint8Array[] = [];
  let n = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (value) {
      parts.push(value);
      n += value.length;
      if (n >= expected) break;
    }
  }
  const out = new Uint8Array(n);
  let off = 0;
  for (const p of parts) {
    out.set(p, off);
    off += p.length;
  }
  return out;
}

export async function tiktokStep(ctx: PublishContext, env: Env): Promise<PublishResult> {
  const token = ctx.creds.tiktok;
  if (!token) return { mode: "draft", reason: "sin token de TikTok en Cuentas. Pégalo y verifica la cuenta." };
  const resume = (ctx.job.resumeData ?? {}) as TiktokResume;
  const auth = { Authorization: `Bearer ${token}` };

  // Paso 1: creator_info (privacidad permitida para esta cuenta).
  if (!resume.step || resume.step === "creator") {
    const r = await httpJson("POST", `${API}/v2/post/publish/creator_info/query/`, auth, {});
    const data = (r.json.data ?? {}) as Record<string, unknown>;
    const err = (r.json.error ?? {}) as Record<string, unknown>;
    if (r.status !== 200 || !data || Object.keys(data).length === 0) {
      if (err.code === "access_token_invalid" || err.code === "scope_not_authorized") {
        return { mode: "draft", reason: `token sin permisos (${String(err.code)}): reautorizar con el scope video.publish` };
      }
      return { mode: "error", reason: `TikTok creator_info falló: ${errorMessage(r.json, `HTTP ${r.status}`)}`, retryable: isRetryableStatus(r.status) };
    }
    const options = (data.privacy_level_options ?? []) as string[];
    const level = options.includes("PUBLIC_TO_EVERYONE") ? "PUBLIC_TO_EVERYONE" : (options[0] ?? "SELF_ONLY");
    return { mode: "processing", resumeData: { step: "init", privacyLevel: level }, reason: "creator_info ok" };
  }

  // Paso 2: init FILE_UPLOAD.
  if (resume.step === "init") {
    const total = ctx.r2Size ?? 0;
    if (!total) return { mode: "error", reason: "no se pudo medir el MP4 en R2", retryable: true };
    const chunkSize = Math.min(CHUNK, total);
    const r = await httpJson(
      "POST",
      `${API}/v2/post/publish/video/init/`,
      auth,
      {
        post_info: { title: ctx.job.clip_id ? appendClipMarker(ctx.job.caption, ctx.job.clip_id, 150) : ctx.job.caption.slice(0, 150), privacy_level: resume.privacyLevel ?? "SELF_ONLY", disable_duet: false, disable_comment: false, disable_stitch: false, video_cover_timestamp_ms: 1000 },
        source_info: { source: "FILE_UPLOAD", video_size: total, chunk_size: chunkSize, total_chunk_count: Math.ceil(total / chunkSize) },
      },
    );
    const data = (r.json.data ?? {}) as Record<string, unknown>;
    const err = (r.json.error ?? {}) as Record<string, unknown>;
    if (err.code === "unaudited_client_can_only_post_to_private_accounts") {
      return { mode: "draft", reason: "app sin auditar: TikTok solo permite publicar en privado (SELF_ONLY) hasta pasar la auditoría. Queda en borrador." };
    }
    if (r.status === 200 && typeof data.publish_id === "string") {
      return {
        mode: "processing",
        resumeData: { step: "upload", publishId: data.publish_id, uploadUrl: data.upload_url as string, chunkSize, bytesUploaded: 0, totalBytes: total, privacyLevel: resume.privacyLevel },
        reason: "init ok, subiendo por chunks",
      };
    }
    if (err.code === "access_token_invalid" || err.code === "scope_not_authorized") {
      return { mode: "draft", reason: `token sin permisos (${String(err.code)}): reautorizar con el scope video.publish` };
    }
    return { mode: "error", reason: `TikTok init falló: ${errorMessage(r.json, `HTTP ${r.status}`)}`, retryable: isRetryableStatus(r.status) };
  }

  // Paso 3: subir el siguiente chunk (PUT con Content-Range).
  if (resume.step === "upload") {
    const total = resume.totalBytes ?? 0;
    const uploaded = resume.bytesUploaded ?? 0;
    const size = resume.chunkSize ?? CHUNK;
    const end = Math.min(uploaded + size, total);
    if (!resume.uploadUrl) return { mode: "error", reason: "TikTok no devolvió upload_url en el init", retryable: true };
    const obj = await env.CLIPS_BUCKET.get(ctx.job.r2_key as string, { range: { offset: uploaded, length: end - uploaded } });
    if (!obj) return { mode: "error", reason: "el MP4 ya no está en R2", retryable: false };
    const bytes = await readStream(obj.body, end - uploaded);
    const r = await httpJson("PUT", resume.uploadUrl, { "Content-Type": "video/mp4", "Content-Length": String(bytes.length), "Content-Range": `bytes ${uploaded}-${end - 1}/${total}` }, bytes, 120_000);
    if (r.status !== 200 && r.status !== 201 && r.status !== 206) {
      return { mode: "error", reason: `TikTok rechazó el chunk: ${errorMessage(r.json, `HTTP ${r.status}`)}`, retryable: isRetryableStatus(r.status) };
    }
    if (end < total) {
      return { mode: "processing", resumeData: { ...resume, bytesUploaded: end }, reason: `subido ${end}/${total} bytes` };
    }
    return { mode: "processing", resumeData: { step: "status", publishId: resume.publishId }, reason: "chunks completos, esperando a TikTok" };
  }

  // Paso 4: status/fetch hasta PUBLISH_COMPLETE (con post_id).
  const r = await httpJson("POST", `${API}/v2/post/publish/status/fetch/`, auth, { publish_id: resume.publishId });
  const data = (r.json.data ?? {}) as Record<string, unknown>;
  if (r.status !== 200 || !data.status) {
    return { mode: "error", reason: `TikTok status falló: ${errorMessage(r.json, `HTTP ${r.status}`)}`, retryable: isRetryableStatus(r.status) };
  }
  const status = data.status as string;
  if (status === "PUBLISH_COMPLETE") {
    const postId = data.post_id as string | undefined;
    if (!postId) return { mode: "processing", resumeData: { step: "status", publishId: resume.publishId }, reason: "TikTok publicó pero aún no entrega el post_id (moderación); se retoma" };
    return { mode: "posted", platformId: postId, evidenceUrl: evidenceUrl("tiktok", postId, ctx.tiktokHandle) };
  }
  if (status === "FAILED") {
    return { mode: "error", reason: `TikTok rechazó la publicación: ${String(data.fail_reason ?? "sin motivo")}`, retryable: false };
  }
  return { mode: "processing", resumeData: { step: "status", publishId: resume.publishId }, reason: `TikTok sigue procesando (${status})` };
}
