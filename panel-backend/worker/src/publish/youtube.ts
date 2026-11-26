// YouTube Shorts por Data API v3 — subida resumable por pasos.
// Docs: https://developers.google.com/youtube/v3/docs/videos/insert
// Costo: videos.insert = 1 unidad en su propio bucket (100 llamadas/día
// por defecto); el plan del proyecto (3-5/día) queda muy por debajo.
// Flujo: refresh OAuth → crear sesión resumable → subir chunks →
// la última respuesta 200 trae el video id → evidence_url.
// El Worker guarda { sessionUrl, bytesUploaded, totalBytes } en
// resume_data y avanza un chunk por invocación del cron.

import type { Env } from "../cloudflare";
import { decryptCredential } from "../crypto";
import { errorMessage, httpJson, isRetryableStatus } from "./http";
import type { PublishContext, PublishResult, ResumeData } from "./index";
import { YOUTUBE_COMMENT_SCOPE } from "./oauth-scopes";

const SCOPE = "https://www.googleapis.com/auth/youtube.upload";

/** Scopes que acepta videos.update (2026-11-26, spec títulos §6.5
 *  I1): el scope genérico de YouTube o youtube.force-ssl. El de
 *  subida (youtube.upload) NO basta para re-titular. La fuente
 *  única de scopes es oauth-scopes.ts: force-ssl se importa de
 *  ahí; el genérico no está en esa lista y vive aquí.
 *  Verificar contra la doc viva de la API en la implementación:
 *  videos.update acepta youtube y youtube.force-ssl. */
export const YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube";
export const YOUTUBE_UPDATE_SCOPES: readonly string[] = [YOUTUBE_SCOPE, YOUTUBE_COMMENT_SCOPE];

/** ¿Trae la lista de scopes concedidos (tokeninfo, URLs exactas)
 *  alguno de los que acepta videos.update? Comparación EXACTA:
 *  una subcadena no vale ("youtube.upload" no es "youtube"). */
export function hasYoutubeUpdateScope(grantedScopes: string[]): boolean {
  return grantedScopes.some((s) => (YOUTUBE_UPDATE_SCOPES as readonly string[]).includes(s));
}

export interface YoutubeSnippet {
  title: string;
  description: string;
  tags: string[];
  categoryId: string;
}

export interface YoutubeTitleUpdate {
  ok: boolean;
  reason?: string;
  /** true cuando el fallo es falta del scope youtube.force-ssl:
   *  la cuenta necesita re-autorización en el panel (Cuentas →
   *  reconectar). Sin él no hay re-título por API. */
  missingScope?: boolean;
}

/** Resuelve las credenciales OAuth de YouTube de una cuenta por su
 *  id de api_accounts. Espejo de la rama youtube de resolveCreds
 *  (publish/index.ts) — misma fuente (api_accounts), mismo
 *  descifrado, misma precedencia (extra y luego el JSON de la
 *  credencial) — sin importar publish/index.ts para no crear un
 *  ciclo (index.ts ya importa youtubeStep de aquí). */
async function resolveYoutubeCredsByAccount(env: Env, accountId: number): Promise<YoutubeCreds | null> {
  const row = await env.DB.prepare(
    "SELECT tenant_id, platform, language, credential_enc, extra_enc FROM api_accounts WHERE id = ?",
  )
    .bind(accountId)
    .first<{ tenant_id: string; platform: string; language: string; credential_enc: string; extra_enc: string }>()
    .catch(() => null);
  if (!row || row.platform !== "youtube" || !row.credential_enc) return null;
  const tenant = row.tenant_id;
  const key = env.CREDENTIAL_ENCRYPTION_KEY;
  const credential = await decryptCredential(row.credential_enc, key, tenant).catch(() => "");
  const extra = row.extra_enc ? await decryptCredential(row.extra_enc, key, tenant).catch(() => "") : "";
  const parseJson = (text: string): Record<string, unknown> => {
    try {
      return JSON.parse(text) as Record<string, unknown>;
    } catch {
      return {};
    }
  };
  const str = (v: unknown): string | undefined => (typeof v === "string" && v ? v : undefined);
  const extraJson = parseJson(extra);
  let clientId = str(extraJson.client_id);
  let clientSecret = str(extraJson.client_secret);
  let refreshToken = str(extraJson.refresh_token);
  if ((!clientId || !clientSecret || !refreshToken) && credential.trim().startsWith("{")) {
    const fromCred = parseJson(credential.trim());
    clientId = clientId ?? str(fromCred.client_id);
    clientSecret = clientSecret ?? str(fromCred.client_secret);
    refreshToken = refreshToken ?? str(fromCred.refresh_token);
  }
  if (!clientId || !clientSecret || !refreshToken) return null;
  return { accessToken: "", refreshToken, clientId, clientSecret };
}

/** Re-titula un vídeo ya publicado en YouTube (2026-11-26, spec
 *  títulos R8.2): PUT /youtube/v3/videos?part=snippet enviando el
 *  snippet COMPLETO (título + descripción + tags + categoryId); si
 *  no se envía un campo, YouTube lo borra.
 *
 *  CHECK EXPLÍCITO DE SCOPE (I1): antes de llamar a videos.update
 *  se refresca el token y se comprueba con tokeninfo que los scopes
 *  concedidos incluyen youtube o youtube.force-ssl (exactos). Si
 *  falta, devuelve missingScope sin llamar a la API: la cuenta
 *  necesita re-autorización en el panel y eso se ve en su
 *  verificación (verify.ts muestra el aviso). Los tokens concedidos
 *  antes de v5.4.0 casi seguro no traen force-ssl (GOOGLE_SCOPES lo
 *  incluye desde v5.4.0, pero los scopes son por concesión).
 *
 *  NO escribe en D1: solo devuelve el resultado; quien lo llama
 *  actualiza title/seo_title con PATCH /api/clips/:id y verifica
 *  con videos.list. No se ejecuta aquí el re-título de 213/185. */
export async function updateVideoTitle(
  env: Env,
  accountId: number,
  videoId: string,
  snippet: YoutubeSnippet,
): Promise<YoutubeTitleUpdate> {
  const title = snippet.title.trim();
  if (!title) return { ok: false, reason: "El título nuevo no puede estar vacío." };
  if (title.length > 100) return { ok: false, reason: "El título no puede superar 100 caracteres (límite de YouTube)." };
  if (!videoId.trim()) return { ok: false, reason: "Falta el id del vídeo de YouTube." };
  const creds = await resolveYoutubeCredsByAccount(env, accountId);
  if (!creds) {
    return { ok: false, reason: "sin OAuth de YouTube configurado para esa cuenta (client + refresh token). Pégalo en Cuentas." };
  }
  const access = await refreshAccessToken(creds);
  if (!access) {
    return { ok: false, reason: "no se pudo renovar el access token de YouTube: reautorizar OAuth en Cuentas." };
  }
  const ti = await httpJson("GET", `https://oauth2.googleapis.com/tokeninfo?access_token=${encodeURIComponent(access)}`);
  if (ti.status !== 200) {
    return { ok: false, reason: `YouTube no respondió a la comprobación del scope (HTTP ${ti.status}): vuelve a intentarlo sin re-titular.` };
  }
  const granted = typeof ti.json.scope === "string" ? ti.json.scope.split(" ") : [];
  if (!hasYoutubeUpdateScope(granted)) {
    return {
      ok: false,
      missingScope: true,
      reason: "missing_scope: youtube.force-ssl — re-autoriza YouTube en el panel (Cuentas → reconectar). El token concedido no trae el permiso para re-titular vídeos.",
    };
  }
  const r = await httpJson(
    "PUT",
    "https://www.googleapis.com/youtube/v3/videos?part=snippet",
    { Authorization: `Bearer ${access}`, "Content-Type": "application/json" },
    {
      id: videoId.trim(),
      snippet: {
        title,
        description: snippet.description,
        tags: snippet.tags,
        categoryId: snippet.categoryId,
      },
    },
  );
  if (r.status !== 200) {
    return { ok: false, reason: `YouTube rechazó el re-título: ${errorMessage(r.json, `HTTP ${r.status}`)}` };
  }
  return { ok: true };
}

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
