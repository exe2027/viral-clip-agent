// Cerebro genérico: token de un solo uso + credenciales desde el panel.
//
// Modelo (2026-11-19, el panel es la ÚNICA interfaz del usuario):
// GitHub solo aloja el código y los workflows. El usuario nunca
// toca GitHub: no hay secretos por tenant ni environments por
// tenant. El cerebro obtiene lo que necesita DEL PANEL:
//   1. El Worker emite un token de un solo uso (tabla brain_tokens,
//      migración 0032) con alcance a UN tenant y 30 min de vida, y
//      lo mete en el client_payload del repository_dispatch a GitHub
//      (dispatchCerebro, en index.ts).
//   2. El cerebro lo canjea UNA vez en POST /api/brain/session por
//      una sesión body.sig (sub "cerebro", 12 h). El token canjeado
//      queda marcado (used_at) y no vale una segunda vez.
//   3. Con esa sesión, el cerebro llama a GET /api/bridge/credentials
//      y recibe las credenciales descifradas de ESE tenant (y solo
//      de ese tenant): Twitch, Gemini, YouTube OAuth, Zernio, Meta.
//      Si una credencial falta en el panel (p. ej. kaez no tiene
//      cuenta Twitch), viene null y el cerebro sigue con el
//      degradado visible de siempre: la solución es añadirla EN EL
//      PANEL, nunca en GitHub.
//   4. El throttle del monitoreo en vivo es por tenant y en D1
//      (fase reportada por el cerebro, worker_phases): la consulta
//      a GitHub veía las corridas de TODOS los tenants y un run de
//      sebastian frenaba los dispatches de kaez.

import type { D1Database, Env } from "./cloudflare";
import { isTenant, tokenFingerprint, type Tenant } from "./auth";
import { decryptCredential } from "./crypto";
import { listApiAccounts } from "./db";

/** Vida del token de un solo uso: 30 min (el run arranca minutos
 *  después del dispatch; si caduca, el Worker vuelve a disparar
 *  en el siguiente ciclo). */
export const BRAIN_TOKEN_TTL_MS = 30 * 60 * 1000;

/** Vida de la sesión del cerebro: 12 h (cubre la corrida diaria
 *  más larga y las colas de GitHub; la sesión de usuario sigue
 *  en 30 días). */
export const BRAIN_SESSION_TTL_MS = 12 * 3600 * 1000;

/** Fases que indican una corrida en vivo en curso para el tenant. */
const LIVE_PHASES = ["buscando_picos", "cortando_clip", "modo_vod"] as const;

/** Ventana para considerar una fase "en curso": 25 min sin fase
 *  nueva = la corrida terminó o se atascó (el dispatch vuelve a
 *  intentarlo; el grupo de concurrencia por tenant del workflow
 *  impide dos corridas en paralelo para el mismo tenant). */
const LIVE_IN_FLIGHT_WINDOW_MS = 25 * 60 * 1000;

function b64urlEncode(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

interface BrainTokenRow {
  token_hash: string;
  tenant_id: string;
  expires_at: number;
  used_at: number | null;
}

/** Emite un token de un solo uso para el tenant y devuelve el
 *  valor en claro (solo se guarda su huella SHA-256 en D1, como
 *  revoked_tokens). Si la tabla no existe (migración 0032 sin
 *  aplicar), levanta: el llamador NO dispara sin token (un run
 *  sin token no podría autenticarse y fallaría igual). */
export async function issueBrainToken(db: D1Database, tenant: Tenant): Promise<string> {
  const raw = b64urlEncode(crypto.getRandomValues(new Uint8Array(32)));
  const hash = await tokenFingerprint(raw);
  const now = Date.now();
  await db
    .prepare(
      "INSERT INTO brain_tokens (token_hash, tenant_id, expires_at, used_at, created_at) VALUES (?, ?, ?, NULL, ?)",
    )
    .bind(hash, tenant, now + BRAIN_TOKEN_TTL_MS, now)
    .run();
  return raw;
}

/** Canjea un token de un solo uso: valida que exista, que el
 *  tenant sea válido Y sea `expectedTenant` (el de env.TENANT),
 *  que no haya caducado y que no esté usado, y lo marca como usado
 *  EN UNA SOLA operación (el WHERE con used_at IS NULL hace
 *  atómico el canje: dos canjes simultáneos solo dejan pasar uno).
 *  Devuelve el tenant o levanta. Un canje rechazado por tenant NO
 *  consume el token: el router sin estado prueba primero en un
 *  Worker y luego en el otro con el mismo token (ver router.ts). */
export async function redeemBrainToken(db: D1Database, token: string, expectedTenant: Tenant): Promise<Tenant> {
  const hash = await tokenFingerprint(token.trim());
  const row = await db
    .prepare("SELECT token_hash, tenant_id, expires_at, used_at FROM brain_tokens WHERE token_hash = ?")
    .bind(hash)
    .first<BrainTokenRow>();
  if (!row) throw new Error("Token del cerebro inválido.");
  if (!isTenant(row.tenant_id)) throw new Error("Token del cerebro con tenant inválido.");
  if (row.tenant_id !== expectedTenant) throw new Error("Token del cerebro de otro tenant.");
  if (row.expires_at <= Date.now()) throw new Error("Token del cerebro caducado: vuelve a disparar desde el panel.");
  if (row.used_at !== null) throw new Error("Token del cerebro ya canjeado.");
  const res = await db
    .prepare("UPDATE brain_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL")
    .bind(Date.now(), hash)
    .run();
  if ((res.meta.changes ?? 0) !== 1) throw new Error("Token del cerebro ya canjeado.");
  return row.tenant_id as Tenant;
}

// ---------------------------------------------------------------------------
// Credenciales del tenant (descifradas solo aquí, en memoria).
// Formato guardado (api_accounts, columnas *_enc, AES-GCM por tenant):
// - twitch:  credential_enc = client_secret; extra_enc = {"client_id"}.
// - gemini:  credential_enc = API key.
// - youtube: extra_enc = {"client_id","client_secret","refresh_token"}
//   (o la credencial misma como JSON del token, como en verify.ts).
// - tiktok vía Zernio: credential_enc = Zernio API key;
//   extra_enc = {"via":"zernio","account_id":"<id cuenta Zernio>"}.
//   Sin via=zernio: Direct Post propio (access token en credential).
// - meta:    credential_enc = Page access token;
//   extra_enc = {"ig_user_id","page_id"}.
// ---------------------------------------------------------------------------

export interface YoutubeOAuth {
  client_id: string;
  client_secret: string;
  refresh_token: string;
}

export interface ZernioCreds {
  api_key: string;
  account_id: string;
}

export interface BrainCredentials {
  twitch: { client_id: string; client_secret: string } | null;
  gemini: { api_key: string } | null;
  youtube: { es: YoutubeOAuth | null; en: YoutubeOAuth | null };
  zernio: { es: ZernioCreds | null; en: ZernioCreds | null };
  tiktok_direct: { es: { access_token: string } | null; en: { access_token: string } | null };
  meta: { page_token: string; ig_user_id: string; page_id: string } | null;
  /** Base pública de R2 (var del Worker, no secreta): la usa el
   *  cerebro para las URLs públicas de los clips en R2. */
  clip_public_base_url: string;
}

function parseJson(text: string): Record<string, unknown> {
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function str(v: unknown): string | undefined {
  return typeof v === "string" && v ? v : undefined;
}

function youtubeFrom(credential: string, extraJson: Record<string, unknown>): YoutubeOAuth | null {
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
  return { client_id: clientId, client_secret: clientSecret, refresh_token: refreshToken };
}

/** Credenciales descifradas del tenant (o null por plataforma si
 *  falta en el panel). Nunca se lee ni se descifra nada de otro
 *  tenant: listApiAccounts ya filtra por tenant_id. */
export async function resolveBrainCredentials(env: Env, tenant: Tenant): Promise<BrainCredentials> {
  const out: BrainCredentials = {
    twitch: null,
    gemini: null,
    youtube: { es: null, en: null },
    zernio: { es: null, en: null },
    tiktok_direct: { es: null, en: null },
    meta: null,
    clip_public_base_url: (env.CLIP_PUBLIC_BASE_URL ?? "").trim(),
  };
  const accounts = await listApiAccounts(env.DB, tenant);
  const key = env.CREDENTIAL_ENCRYPTION_KEY;
  for (const row of accounts) {
    if (!row.credential_enc && !row.extra_enc) continue;
    const credential = row.credential_enc
      ? await decryptCredential(row.credential_enc, key, tenant).catch(() => "")
      : "";
    const extra = row.extra_enc ? await decryptCredential(row.extra_enc, key, tenant).catch(() => "") : "";
    const extraJson = parseJson(extra);
    const lang = row.language === "en" ? "en" : "es";
    if (row.platform === "twitch") {
      const clientId = str(extraJson.client_id);
      if (clientId && credential) out.twitch = { client_id: clientId, client_secret: credential };
    } else if (row.platform === "gemini") {
      if (credential) out.gemini = { api_key: credential };
    } else if (row.platform === "youtube") {
      // YouTube vale con extra solo (los tres campos OAuth) o con
      // el JSON del token en la credencial; youtubeFrom decide.
      out.youtube[lang] = youtubeFrom(credential, extraJson);
    } else if (row.platform === "tiktok") {
      if (extraJson.via === "zernio") {
        const accountId = str(extraJson.account_id);
        if (accountId && credential) out.zernio[lang] = { api_key: credential, account_id: accountId };
      } else if (credential) {
        out.tiktok_direct[lang] = { access_token: credential.split("\n")[0]?.trim() ?? "" };
      }
    } else if (row.platform === "meta") {
      if (credential) {
        out.meta = {
          page_token: credential,
          ig_user_id: str(extraJson.ig_user_id) ?? "",
          page_id: str(extraJson.page_id) ?? "",
        };
      }
    }
  }
  return out;
}

/** ¿Se emitió un token del cerebro para este tenant hace menos
 *  de `withinMs`? Es una LECTURA (no escribe): el throttle de
 *  dispatch que sobrevive a un fallo de escrituras en D1. Los
 *  throttles basados en escrituras (worker_state
 *  .last_live_dispatch_at, eventos en worker_events) no persisten
 *  cuando la cuota diaria se agota: el UPDATE falla, el Worker
 *  cree que no disparó nunca y emite un token + un dispatch por
 *  minuto (medido 2026-11-20: 60/hora en el bucle de fallo). La
 *  tabla brain_tokens, en cambio, se lee aunque las escrituras
 *  fallen: si ya hay un token reciente, su corrida está en curso
 *  o en cola y no hace falta otro. Falla cerrado a false (se
 *  dispara igual): un fallo de lectura nunca frena el vivo. */
export async function hasRecentBrainToken(db: D1Database, tenant: Tenant, withinMs: number, nowMsValue: number): Promise<boolean> {
  try {
    const row = await db
      .prepare("SELECT created_at FROM brain_tokens WHERE tenant_id = ? ORDER BY created_at DESC LIMIT 1")
      .bind(tenant)
      .first<{ created_at: number }>();
    return !!row && nowMsValue - row.created_at < withinMs;
  } catch {
    return false;
  }
}

/** ¿Hay una corrida en vivo en curso para ESTE tenant? La fase la
 *  reporta el propio cerebro (worker_phases, por tenant): una fase
 *  de vivo reciente significa que su corrida sigue trabajando. Sin
 *  fila o con fase vieja/terminal, no hay corrida en curso. Nunca
 *  levanta: un fallo de D1 se trata como "sin corrida" y el
 *  llamador decide (el throttle de 15 min ya protege). */
export async function liveRunInFlight(db: D1Database, tenant: Tenant, nowMs: number): Promise<boolean> {
  try {
    const row = await db
      .prepare("SELECT phase, updated_at FROM worker_phases WHERE tenant_id = ?")
      .bind(tenant)
      .first<{ phase: string; updated_at: number }>();
    if (!row) return false;
    if (!(LIVE_PHASES as readonly string[]).includes(row.phase)) return false;
    return row.updated_at > 0 && nowMs - row.updated_at < LIVE_IN_FLIGHT_WINDOW_MS;
  } catch {
    return false;
  }
}
