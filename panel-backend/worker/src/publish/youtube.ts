// YouTube Shorts por Data API v3 — subida resumable por pasos.
// Docs: https://developers.google.com/youtube/v3/docs/videos/insert
// Costo: videos.insert = 1 unidad en su propio bucket (100 llamadas/día
// por defecto); el plan del proyecto (3-5/día) queda muy por debajo.
// Flujo: refresh OAuth → crear sesión resumable → subir chunks →
// la última respuesta 200 trae el video id → evidence_url.
// El Worker guarda { sessionUrl, bytesUploaded, totalBytes } en
// resume_data y avanza un chunk por invocación del cron.

import type { Env } from "../cloudflare";
import { errorMessage, httpJson, isRetryableStatus } from "./http";
import type { PublishContext, PublishResult, ResumeData } from "./index";

const SCOPE = "https://www.googleapis.com/auth/youtube.upload";

export interface YoutubeCreds {
  accessToken: string;
  refreshToken: string;
  clientId: string;
  clientSecret: string;
}

export async function refreshAccessToken(creds: YoutubeCreds): Promise<string | null> {
  const body = new URLSearchParams({
    client_id: creds.clientId,
    client_secret: creds.clientSecret,
    refresh_token: creds.refreshToken,
    grant_type: "refresh_token",
  });
  const r = await httpJson("POST", "https://oauth2.googleapis.com/token", { "Content-Type": "application/x-www-form-urlencoded" }, body.toString());
  const token = r.json.access_token;
  return typeof token === "string" ? token : null;
}

interface YoutubeResume extends ResumeData {
  step: "session" | "upload";
  sessionUrl?: string;
  bytesUploaded?: number;
  totalBytes?: number;
}

export async function youtubeStep(ctx: PublishContext, env: Env): Promise<PublishResult> {
  const creds = ctx.creds.youtube;
  if (!creds) return { mode: "draft", reason: "sin OAuth de YouTube configurado (client + refresh token). Pégalo en Cuentas." };
  const resume = (ctx.job.resumeData ?? {}) as YoutubeResume;
  const access = await refreshAccessToken(creds);
  if (!access) return { mode: "draft", reason: "no se pudo renovar el access token de YouTube: reautorizar OAuth en Cuentas." };

  // Paso 1: crear la sesión resumable.
  if (resume.step !== "upload" || !resume.sessionUrl) {
    const metadata = {
      snippet: {
        title: ctx.job.title.slice(0, 100),
        description: ctx.job.caption,
        tags: [],
        categoryId: "20",
      },
      status: { privacyStatus: "public", selfDeclaredMadeForKids: false },
    };
    const r = await httpJson(
      "POST",
      "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
      { Authorization: `Bearer ${access}`, "Content-Type": "application/json", "X-Upload-Content-Type": "video/mp4" },
      metadata,
    );
    if (r.status !== 200) {
      return { mode: "error", reason: `YouTube no abrió la sesión resumable: ${errorMessage(r.json, `HTTP ${r.status}`)}`, retryable: isRetryableStatus(r.status) };
    }
    const sessionUrl = r.headers.get("location");
    if (!sessionUrl) return { mode: "error", reason: "YouTube no devolvió la URL de sesión resumable", retryable: true };
    const total = ctx.r2Size ?? 0;
    if (!total) return { mode: "error", reason: "no se pudo medir el MP4 en R2", retryable: true };
    return {
      mode: "processing",
      resumeData: { step: "upload", sessionUrl, bytesUploaded: 0, totalBytes: total },
      reason: "sesión resumable creada, subiendo por chunks",
    };
  }

  // Paso 2: subir el siguiente chunk (5 MB) desde R2 por rango.
  const total = resume.totalBytes ?? 0;
  const uploaded = resume.bytesUploaded ?? 0;
  const CHUNK = 20 * 1024 * 1024;
  const end = Math.min(uploaded + CHUNK, total);
  const obj = await env.CLIPS_BUCKET.get(ctx.job.r2_key as string, {
    range: { offset: uploaded, length: end - uploaded },
  });
  if (!obj) return { mode: "error", reason: "el MP4 ya no está en R2", retryable: false };
  const bytes = await readStream(obj.body, end - uploaded);
  const r = await httpJson(
    "PUT",
    resume.sessionUrl,
    {
      "Content-Type": "video/mp4",
      "Content-Length": String(bytes.length),
      "Content-Range": `bytes ${uploaded}-${end - 1}/${total}`,
    },
    bytes,
    120_000,
  );
  if (r.status === 308) {
    // Incompleto: YouTube dice hasta dónde llegó en el header Range.
    const range = r.headers.get("range") ?? "";
    const m = /bytes=0-(\d+)/.exec(range);
    const next = m ? Number(m[1]) + 1 : end;
    return { mode: "processing", resumeData: { step: "upload", sessionUrl: resume.sessionUrl, bytesUploaded: next, totalBytes: total }, reason: `subido ${next}/${total} bytes` };
  }
  if (r.status === 200 || r.status === 201) {
    const videoId = r.json.id;
    if (typeof videoId !== "string") return { mode: "error", reason: "YouTube aceptó la subida pero no devolvió video id", retryable: true };
    return { mode: "posted", platformId: videoId, evidenceUrl: `https://www.youtube.com/shorts/${videoId}` };
  }
  return { mode: "error", reason: `YouTube rechazó el chunk: ${errorMessage(r.json, `HTTP ${r.status}`)}`, retryable: isRetryableStatus(r.status) };
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

export { SCOPE };
