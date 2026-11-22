// Facebook Reels por Graph API (Reels Publishing API).
// Docs: https://developers.facebook.com/docs/video-api/
// Solo a Página (nunca perfil personal): Page access token con
// pages_manage_posts. Flujo en 3 fases: start → subir por chunks a
// rupload.facebook.com (8 MB, reanudable por offset) → finish con
// video_state=PUBLISHED. Requisitos: MP4 9:16, 3-90 s.

import type { Env } from "../cloudflare";
import { logWorkerEvent } from "../db";
import { errorMessage, httpJson, isRetryableStatus } from "./http";
import type { PublishContext, PublishResult, ResumeData } from "./index";

const GRAPH_VERSION = "v26.0";
// rupload responde 206 Partial Content en cada chunk aceptado
// (con end_offset); 200 solo al completar. Cada POST lleva los
// bytes restantes (offset → EOF): trozos fijos de 8 MB los rechaza
// Meta con "Partial request (did not match length of file)" (job 8
// clip 11, 2026-11-20, 2 sesiones nuevas). Un POST de 20 MB se cayó
// una vez a mitad de subida ("Network connection lost", 2026-11-18),
// pero el archivo entero en un POST es el único camino que acepta
// rupload para Reels.
export const FB_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024;
const CHUNK = FB_UPLOAD_CHUNK_BYTES;

interface FbResume extends ResumeData {
  step: "start" | "upload" | "finish";
  videoId?: string;
  offset?: number;
  totalBytes?: number;
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

/** Offset REAL de una sesión de Reels en el servidor de Facebook
 *  (Meta: GET /{video-id}?fields=status → uploading_phase con
 *  bytes_transfered). Es la única fuente fiable para retomar: el
 *  end_offset que devuelve rupload tras un chunk puede no coincidir
 *  con lo que el servidor guardó de verdad. Nunca lanza. */
async function getServerOffset(token: string, videoId: string, host: string): Promise<{ offset: number | null; http: number; err: string }> {
  try {
    const params = new URLSearchParams({ fields: "status", access_token: token });
    const r = await httpJson("GET", `${host}/${videoId}?${params.toString()}`);
    if (r.status !== 200) return { offset: null, http: r.status, err: r.text.slice(0, 200) };
    const up = (r.json.status as Record<string, unknown> | undefined)?.uploading_phase as Record<string, unknown> | undefined;
    const raw = up?.bytes_transfered ?? up?.bytes_transferred;
    const n = typeof raw === "number" ? raw : Number(raw);
    if (Number.isFinite(n) && n >= 0) return { offset: n, http: 200, err: "" };
    return { offset: null, http: 200, err: `sin bytes_transfered en: ${r.text.slice(0, 200)}` };
  } catch (e) {
    return { offset: null, http: 0, err: String(e).slice(0, 200) };
  }
}

export async function facebookStep(ctx: PublishContext, env: Env): Promise<PublishResult> {
  const token = ctx.creds.facebook;
  if (!token) return { mode: "draft", reason: "sin Page access token en Cuentas." };
  const pageId = ctx.creds.pageId;
  if (!pageId) return { mode: "draft", reason: "sin page_id configurado (Cuentas → Facebook)." };
  const host = `https://graph.facebook.com/${GRAPH_VERSION}`;
  const resume = (ctx.job.resumeData ?? {}) as FbResume;

  if (!resume.step || resume.step === "start") {
    const params = new URLSearchParams({ upload_phase: "start", access_token: token });
    const r = await httpJson("POST", `${host}/${pageId}/video_reels?${params.toString()}`);
    if (r.status === 200 && typeof r.json.video_id === "string") {
      const total = ctx.r2Size ?? 0;
      if (!total) return { mode: "error", reason: "no se pudo medir el MP4 en R2", retryable: true };
      return { mode: "processing", resumeData: { step: "upload", videoId: r.json.video_id, offset: 0, totalBytes: total }, reason: "sesión abierta, subiendo por chunks" };
    }
    return { mode: "error", reason: `Facebook no abrió la subida: ${errorMessage(r.json, `HTTP ${r.status}`)}`, retryable: isRetryableStatus(r.status) };
  }

  if (resume.step === "upload") {
    const total = resume.totalBytes ?? 0;
    let offset = resume.offset ?? 0;
    // Reconciliación con el offset REAL de Facebook (2026-11-19,
    // job 17 clip 23: el chunk 2 412aba en 3 sesiones seguidas porque
    // nuestro offset local (8388608) no coincidía con el del
    // servidor — el end_offset del chunk 1 no es de fiar. Meta
    // documenta bytes_transfered como el offset de resume).
    if (offset > 0) {
      const srv = await getServerOffset(token, resume.videoId as string, host);
      if (srv.offset !== null) {
        if (srv.offset >= total) {
          return { mode: "processing", resumeData: { step: "finish", videoId: resume.videoId }, reason: "Facebook ya tiene todos los bytes, publicando" };
        }
        if (srv.offset !== offset) {
          await logWorkerEvent(env.DB, "aviso", `FB clip #${ctx.job.clip_id}: offset local ${offset}, servidor ${srv.offset} → se retoma desde ${srv.offset} (sesión ${resume.videoId})`, ctx.job.tenant_id).catch(() => {});
        }
        offset = srv.offset;
      }
    }
    // Meta espera en cada POST los bytes restantes (offset → EOF), no
    // trozos fijos: un trozo de 8 MB en el chunk 0 lo rechaza con
    // "Partial request (did not match length of file)" (job 8 clip 11,
    // 2026-11-20 22:23 y 22:40, 2 sesiones nuevas). La guía de Reels
    // manda el archivo entero en un solo POST y reanuda desde el
    // offset con el resto. Para un MP4 de 24 MB es un solo POST.
    const end = total;
    const obj = await env.CLIPS_BUCKET.get(ctx.job.r2_key as string, { range: { offset, length: end - offset } });
    if (!obj) return { mode: "error", reason: "el MP4 ya no está en R2", retryable: false };
    const bytes = await readStream(obj.body, end - offset);
    // Un chunk corto desincroniza la sesión (el servidor guarda
    // menos bytes de los que creemos): nunca se sube a ciegas.
    if (bytes.length !== end - offset) {
      return { mode: "error", reason: `R2 devolvió ${bytes.length} bytes, se esperaban ${end - offset} (offset ${offset}/${total})`, retryable: true };
    }
    const url = `https://rupload.facebook.com/video-upload/${GRAPH_VERSION}/${resume.videoId}`;
    const r = await httpJson("POST", url, { Authorization: `OAuth ${token}`, "Content-Type": "application/octet-stream", offset: String(offset), file_size: String(total) }, bytes, 120_000);
    // rupload: 206 = chunk aceptado (end_offset), 200 = completo.
    // 412 = el offset guardado ya no coincide con el de Facebook:
    // se pregunta al servidor cuál es su offset real y se retoma
    // desde ahí (si no responde, sesión nueva desde cero, como
    // antes).
    if (r.status === 412) {
      const srv = await getServerOffset(token, resume.videoId as string, host);
      await logWorkerEvent(env.DB, "aviso", `FB clip #${ctx.job.clip_id}: chunk 412 en ${offset}/${total} (sesión ${resume.videoId}); rupload: ${r.text.slice(0, 200)}; servidor: http ${srv.http} offset ${srv.offset ?? "?"} ${srv.err.slice(0, 200)}`, ctx.job.tenant_id).catch(() => {});
      if (srv.offset !== null && srv.offset >= total) {
        return { mode: "processing", resumeData: { step: "finish", videoId: resume.videoId }, reason: "Facebook ya tiene todos los bytes, publicando" };
      }
      if (srv.offset !== null) {
        return { mode: "processing", resumeData: { step: "upload", videoId: resume.videoId, offset: srv.offset, totalBytes: total }, reason: `offset desincronizado (servidor: ${srv.offset}/${total}), retomando desde ahí` };
      }
      return { mode: "processing", resumeData: {}, reason: "offset desincronizado con Facebook, reiniciando sesión" };
    }
    if (r.status !== 200 && r.status !== 206) {
      return { mode: "error", reason: `Facebook rechazó el chunk: ${errorMessage(r.json, `HTTP ${r.status}`)}`, retryable: isRetryableStatus(r.status) };
    }
    // Un 206 con cuerpo de error (p. ej. PartialRequestError) no es
    // un chunk aceptado: antes se trataba como éxito y se avanzaba el
    // offset a ciegas, y el chunk siguiente 412aba ("Maximum accepted
    // offset: 0") y mataba la sesión.
    const dbg = (r.json.debug_info ?? {}) as Record<string, unknown>;
    if (r.json.error || dbg.type) {
      return { mode: "error", reason: `Facebook rechazó el chunk: ${errorMessage(r.json, typeof dbg.message === "string" ? dbg.message : `HTTP ${r.status}`)}`, retryable: true };
    }
    if (r.json.end_offset === undefined) {
      // Sin end_offset no se avanza a ciegas: se pregunta al servidor
      // cuánto guardó de verdad; si no lo dice, error reintentable
      // (el ciclo reintenta el mismo chunk en vez de 412ar).
      const srv = await getServerOffset(token, resume.videoId as string, host);
      if (srv.offset !== null && srv.offset > offset) {
        await logWorkerEvent(env.DB, "aviso", `FB clip #${ctx.job.clip_id}: rupload sin end_offset en el chunk ${offset}/${total} (sesión ${resume.videoId}); servidor dice ${srv.offset}`, ctx.job.tenant_id).catch(() => {});
        if (srv.offset >= total) {
          return { mode: "processing", resumeData: { step: "finish", videoId: resume.videoId }, reason: "Facebook ya tiene todos los bytes, publicando" };
        }
        return { mode: "processing", resumeData: { ...resume, offset: srv.offset }, reason: `subido ${srv.offset}/${total} bytes (confirmado por el servidor)` };
      }
      return { mode: "error", reason: `rupload no confirmó los bytes del chunk ${offset}/${total} (sin end_offset)`, retryable: true };
    }
    const newOffset = Number(r.json.end_offset);
    if (!Number.isFinite(newOffset) || newOffset <= offset) {
      return { mode: "error", reason: `rupload no avanzó el offset (${offset}/${total} bytes)`, retryable: true };
    }
    if (newOffset < total) {
      return { mode: "processing", resumeData: { ...resume, offset: newOffset }, reason: `subido ${newOffset}/${total} bytes` };
    }
    return { mode: "processing", resumeData: { step: "finish", videoId: resume.videoId }, reason: "chunks completos, publicando" };
  }

  const params = new URLSearchParams({ upload_phase: "finish", video_id: resume.videoId as string, video_state: "PUBLISHED", description: ctx.job.caption, access_token: token });
  const r = await httpJson("POST", `${host}/${pageId}/video_reels?${params.toString()}`);
  // finish devuelve 200 aunque el body no traiga id: el video_id
  // de la fase start YA es el id del reel publicado.
  if (r.status === 200 && !r.json.error) {
    const id = (typeof r.json.id === "string" && r.json.id) || (resume.videoId as string);
    return { mode: "posted", platformId: id, evidenceUrl: `https://www.facebook.com/reel/${id}/` };
  }
  return { mode: "error", reason: `Facebook no publicó: ${errorMessage(r.json, `HTTP ${r.status}`)}`, retryable: isRetryableStatus(r.status) };
}
