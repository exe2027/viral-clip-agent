// R2: MP4s de previews/clips. Un clip = una key por tenant:
//   clips/<tenant>/<clip_id>/<aleatorio>_<nombre>.mp4
// El prefijo por tenant es parte del aislamiento (avc.pages.dev,
// instalación única): dos tenants nunca comparten key aunque el
// clip_id coincida. La key lleva un componente aleatorio (64 bits):
// sin él, la key era predecible a partir del id secuencial del clip.
//
// El bucket es PRIVADO: no hay dominio público r2.dev (ver
// wrangler.toml; el dominio se desactiva en el dashboard/API de R2).
// NADA sirve los bytes desde r2.dev. El único acceso sin sesión es
// la URL FIRMADA del Worker (GET /api/public-video, ver index.ts):
// HMAC-SHA256 con SESSION_SECRET sobre key+expiración, caduca en
// 15 min, se emite SOLO en el paso de publicación de Instagram
// (Meta descarga el video desde ahí) y solo para ese objeto.
// El binding R2 de Workers NO tiene createPresignedUrl (tipos
// @cloudflare/workers-types: R2Bucket solo expone head/get/put/
// multipart/delete/list); la URL firmada del Worker es la
// alternativa verificada, sin credenciales nuevas. El borrado sigue
// la regla §9: solo cuando TODAS las plataformas del clip están en
// terminal.

import type { Env } from "./cloudflare";
import type { Tenant } from "./auth";
import { b64Decode, b64urlEncode } from "./crypto";

/** Caducidad de las URLs firmadas de vídeo (15 min). */
export const VIDEO_URL_TTL_S = 900;

/** Origen del Worker que sirve /api/public-video. */
export const WORKER_VIDEO_BASE = "https://avc.vikingosfoodspa.workers.dev";

/** Prefijo válido de una key de clip (tenant + id numérico). */
const CLIP_KEY_RE = /^clips\/[a-z]+\/\d+\//;

/** Prefijo válido de una key de marca de agua (tenant). */
export const WATERMARK_KEY_RE = /^watermarks\/[a-z]+\//;

export function r2KeyForClip(clipId: number, filename: string, tenant: Tenant): string {
  const safe = filename.replace(/[^A-Za-z0-9._-]/g, "_");
  // 64 bits aleatorios por subida: la key no se puede adivinar
  // a partir del tenant + id secuencial del clip.
  const rand = [...crypto.getRandomValues(new Uint8Array(8))]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return `clips/${tenant}/${clipId}/${rand}_${safe}`;
}

/** Key de un logo de marca de agua: watermarks/<tenant>/<azar>_<nombre>.
 *  Mismo patrón que los clips (componente aleatorio de 64 bits):
 *  dos tenants nunca comparten key aunque el nombre coincida. */
export function r2KeyForWatermark(filename: string, tenant: Tenant): string {
  const safe = filename.replace(/[^A-Za-z0-9._-]/g, "_");
  const rand = [...crypto.getRandomValues(new Uint8Array(8))]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return `watermarks/${tenant}/${rand}_${safe}`;
}

async function videoHmacKey(secretB64: string): Promise<CryptoKey> {
  const raw = b64Decode(secretB64.trim());
  return crypto.subtle.importKey("raw", raw.buffer as ArrayBuffer, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
}

/** Núcleo de firma HMAC (esquema v1:<key>:<exp>) compartido por
 *  vídeo y marca de agua: la única diferencia es qué prefijo de
 *  key acepta cada uno. */
async function signObjectUrl(env: Env, path: string, key: string, ttlS: number, keyRe: RegExp): Promise<string | null> {
  if (!keyRe.test(key)) return null;
  if (!env.SESSION_SECRET) return null;
  try {
    const exp = Math.floor(Date.now() / 1000) + ttlS;
    const data = `v1:${key}:${exp}`;
    const sig = await crypto.subtle.sign("HMAC", await videoHmacKey(env.SESSION_SECRET), new TextEncoder().encode(data));
    // La URL firmada DEBE apuntar al Worker que la firmó: la
    // verificación HMAC usa env.SESSION_SECRET, distinto por
    // tenant. WORKER_VIDEO_BASE hardcodeado a avc (sebastian)
    // rompía Instagram en cualquier otro tenant (kaez): Meta
    // pedía el vídeo a avc, que lo rechazaba por firma inválida
    // → contenedor ERROR "video no procesable" (3 intentos,
    // 2026-11-22). WORKER_PUBLIC_URL es var por despliegue.
    const base = (env.WORKER_PUBLIC_URL ?? "").trim() || WORKER_VIDEO_BASE;
    return `${base}${path}?key=${encodeURIComponent(key)}&exp=${exp}&sig=${b64urlEncode(new Uint8Array(sig))}`;
  } catch {
    return null;
  }
}

async function verifyObjectUrl(env: Env, key: string, exp: number, sig: string, keyRe: RegExp): Promise<boolean> {
  if (!keyRe.test(key)) return false;
  if (!Number.isFinite(exp) || exp <= Math.floor(Date.now() / 1000)) return false;
  if (!env.SESSION_SECRET || !sig) return false;
  try {
    const data = `v1:${key}:${exp}`;
    const expected = await crypto.subtle.sign("HMAC", await videoHmacKey(env.SESSION_SECRET), new TextEncoder().encode(data));
    const expectedB64 = b64urlEncode(new Uint8Array(expected));
    if (sig.length !== expectedB64.length) return false;
    let diff = 0;
    for (let i = 0; i < sig.length; i++) diff |= sig.charCodeAt(i) ^ expectedB64.charCodeAt(i);
    return diff === 0;
  } catch {
    return false;
  }
}

/** URL firmada de un solo objeto, válida ttlS segundos. Solo la
 *  emite el paso de publicación (Instagram); nadie más construye
 *  URLs públicas de R2. Devuelve null si la key o el secreto no
 *  valen. */
export async function signVideoUrl(env: Env, key: string, ttlS = VIDEO_URL_TTL_S): Promise<string | null> {
  return signObjectUrl(env, "/api/public-video", key, ttlS, CLIP_KEY_RE);
}

/** Verifica una URL firmada de /api/public-video: key con prefijo
 *  de clip, sin caducar, y firma HMAC exacta. */
export async function verifySignedVideoUrl(env: Env, key: string, exp: number, sig: string): Promise<boolean> {
  return verifyObjectUrl(env, key, exp, sig, CLIP_KEY_RE);
}

/** Caducidad de las URLs firmadas de marca de agua (60 min): el
 *  cerebro la pide al leer los Ajustes por el puente y descarga el
 *  logo al renderizar; una corrida puede durar más de 15 min, así
 *  que el TTL del vídeo (15 min) se quedaría corto. */
export const WATERMARK_URL_TTL_S = 3600;

/** Tamaño máximo de un logo de marca de agua (5 MB): un logo es
 *  una imagen pequeña; más que eso es un error de archivo. */
export const WATERMARK_MAX_BYTES = 5 * 1024 * 1024;

/** URL firmada de un logo de marca de agua (GET /api/public-asset).
 *  Solo la emite el puente de Ajustes (/api/bridge/settings) cuando
 *  la marca está activada y hay logo activo del tenant. */
export async function signWatermarkUrl(env: Env, key: string, ttlS = WATERMARK_URL_TTL_S): Promise<string | null> {
  return signObjectUrl(env, "/api/public-asset", key, ttlS, WATERMARK_KEY_RE);
}

/** Verifica una URL firmada de /api/public-asset: key con prefijo
 *  de marca de agua, sin caducar, y firma HMAC exacta. */
export async function verifySignedWatermarkUrl(env: Env, key: string, exp: number, sig: string): Promise<boolean> {
  return verifyObjectUrl(env, key, exp, sig, WATERMARK_KEY_RE);
}

export async function uploadClipMp4(env: Env, key: string, body: ReadableStream, contentType = "video/mp4"): Promise<number> {
  const obj = await env.CLIPS_BUCKET.put(key, body, { httpMetadata: { contentType } });
  return obj.size;
}

/** Subida de un logo de marca de agua (imagen pequeña, ≤5 MB: se
 *  sube como ArrayBuffer, no como stream). */
export async function uploadWatermarkImage(env: Env, key: string, body: ArrayBuffer, contentType: string): Promise<number> {
  const obj = await env.CLIPS_BUCKET.put(key, body, { httpMetadata: { contentType } });
  return obj.size;
}
