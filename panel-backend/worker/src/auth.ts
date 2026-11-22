// Login por usuario en UN Worker por tenant (decisión 2026-11-19):
// cada Worker (avc-sebastian, avc-kaez) sirve EXACTAMENTE a un
// tenant, el de su var TENANT. Sin base de sesiones: cookie firmada
// con HMAC. El hash de cada contraseña vive en D1
// (panel_users.password_hash, formato pbkdf2$iter$salt_b64$hash_b64,
// generado con scripts/gen-password-hash.mjs) y NUNCA está en el
// repo. El secreto PANEL_PASSWORD_HASH por despliegue YA NO EXISTE:
// lo sustituye el login por usuario.
//   node -e ... (no usar: genera basura)
// Mejor: usar scripts/gen-password-hash.mjs (pide la contraseña por
// stdin, imprime el hash; no la guarda) y poner el hash en
// scripts/seed-users.sql antes de sembrar D1.

import type { Env } from "./cloudflare";
import { b64Decode, b64Encode, b64urlDecode, b64urlEncode } from "./crypto";

const SESSION_COOKIE = "panel-session";
const SESSION_TTL_MS = 30 * 24 * 3600 * 1000; // 30 días
const encoder = new TextEncoder();

/** Tenants conocidos de AVC. Un Worker sirve EXACTAMENTE uno de
 *  ellos (el de su var TENANT); el otro Worker sirve al otro. El
 *  router sin estado (router.ts, Worker viejo `avc`) sí conoce los
 *  dos: reenvía cada petición al Worker del tenant que corresponde.
 *  "colega" se eliminó 2026-11-19: nunca fue un usuario. OJO: un
 *  tenant nuevo NO funciona solo con la fila en panel_users: hay
 *  que añadirlo aquí, si no el login da 200 pero el token no
 *  verifica (isTenant rechaza el tenant y /api/auth/me da 401). */
export const TENANTS = ["sebastian", "kaez"] as const;
export type Tenant = (typeof TENANTS)[number];

export function isTenant(v: string): v is Tenant {
  return (TENANTS as readonly string[]).includes(v);
}

/** Tenant ÚNICO que sirve este Worker: el de su var TENANT.
 *  Falla en voz alta si falta o no es "sebastian"|"kaez": un Worker
 *  sin TENANT válido no sirve a nadie (nunca un default silencioso
 *  que acabara sirviendo al tenant equivocado). Todos los puntos de
 *  frontera (sesión, cerebro, OAuth, login, puente, cron) pasan por
 *  aquí. */
export function requireTenant(env: Pick<Env, "TENANT">): Tenant {
  const t = (env.TENANT ?? "").trim();
  if (!isTenant(t)) {
    throw new Error(`TENANT no configurado o inválido (${JSON.stringify(t)}): debe ser "sebastian" o "kaez".`);
  }
  return t;
}

export interface SessionPayload {
  sub: string; // username en panel_users (p. ej. "sebastian")
  tenant: Tenant;
  exp: number; // ms epoch
}

async function hmacKey(secretB64: string): Promise<CryptoKey> {
  const raw = b64Decode(secretB64.trim());
  return crypto.subtle.importKey("raw", raw.buffer as ArrayBuffer, { name: "HMAC", hash: "SHA-256" }, false, [
    "sign",
    "verify",
  ]);
}

async function sign(data: string, secretB64: string): Promise<string> {
  const key = await hmacKey(secretB64);
  const sig = await crypto.subtle.sign("HMAC", key, encoder.encode(data));
  return b64urlEncode(new Uint8Array(sig));
}

/** Crea el valor del token de sesión body.sig (mismo formato que
 *  lleva la cookie). El login lo devuelve también en el JSON para
 *  navegadores que bloquean la cookie cross-site (Brave iOS):
 *  el front lo guarda y lo reenvía como Authorization: Bearer.
 *  `ttlMs` opcional: la sesión de usuario es 30 días; la sesión
 *  del cerebro (canje del token de un solo uso) es 12 h. */
export async function createSessionToken(username: string, tenant: Tenant, secretB64: string, ttlMs: number = SESSION_TTL_MS): Promise<string> {
  const payload: SessionPayload = { sub: username, tenant, exp: Date.now() + ttlMs };
  const body = b64urlEncode(encoder.encode(JSON.stringify(payload)));
  const sig = await sign(body, secretB64);
  return `${body}.${sig}`;
}

export function sessionCookieHeader(token: string): string {
  return `${SESSION_COOKIE}=${token}; HttpOnly; Secure; SameSite=None; Path=/; Max-Age=${SESSION_TTL_MS / 1000}`;
}

export async function createSessionCookie(username: string, tenant: Tenant, secretB64: string): Promise<string> {
  return sessionCookieHeader(await createSessionToken(username, tenant, secretB64));
}

export function clearSessionCookie(): string {
  return `${SESSION_COOKIE}=; HttpOnly; Secure; SameSite=None; Path=/; Max-Age=0`;
}

/** Verifica un token de sesión body.sig (mismo formato, misma
 *  verificación HMAC y mismo TTL que la cookie). El tenant del
 *  payload DEBE ser `expectedTenant` (el de env.TENANT): una sesión
 *  válida del otro tenant no vale en este Worker. */
export async function verifySessionToken(
  token: string | null | undefined,
  secretB64: string,
  expectedTenant: Tenant,
): Promise<SessionPayload | null> {
  if (!token) return null;
  const [body, sig] = token.trim().split(".");
  if (!body || !sig) return null;
  try {
    const key = await hmacKey(secretB64);
    const ok = await crypto.subtle.verify(
      "HMAC",
      key,
      b64urlDecode(sig).buffer as ArrayBuffer,
      encoder.encode(body),
    );
    if (!ok) return null;
    const payload = JSON.parse(new TextDecoder().decode(b64urlDecode(body))) as SessionPayload;
    if (typeof payload.sub !== "string" || !payload.sub || !isTenant(payload.tenant)) return null;
    if (payload.tenant !== expectedTenant) return null;
    if (typeof payload.exp !== "number" || payload.exp < Date.now()) return null;
    return payload;
  } catch {
    // Token malformado (base64 inválido, JSON inválido, etc.):
    // sesión inválida, no un error 500.
    return null;
  }
}

/** Huella SHA-256 hex de un token de sesión (B1, Frente 2): es lo
 *  que se guarda en revoked_tokens al cerrar sesión. Nunca se
 *  guarda el token en claro en D1. */
export async function tokenFingerprint(token: string): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", encoder.encode(token.trim()));
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// ---------------------------------------------------------------------------
// State OAuth del asistente de onboarding (2026-11-18).
// El flujo OAuth es una navegación top-level (el proveedor redirige
// al callback sin sesión): el `state` lleva proveedor + tenant y va
// firmado con HMAC, con clave DIFERENTE a la de sesión (dominio
// "oauth-state"): un state NO sirve como token de sesión ni al
// revés. Caduca en 10 minutos. Sin tabla en D1: el estado viaja
// firmado en la URL y se verifica en el callback.
// ---------------------------------------------------------------------------
export interface OAuthStatePayload {
  provider: string;
  tenant: Tenant;
  exp: number; // ms epoch
  // Cuenta api_accounts a la que va el token (la tarjeta pulsada).
  // Sin ella, legado: la primera cuenta de ese idioma (español).
  // Nunca se guarda el token en otra cuenta que la elegida.
  accountId?: number;
}

const OAUTH_STATE_TTL_MS = 10 * 60 * 1000; // 10 minutos

async function oauthHmacKey(secretB64: string): Promise<CryptoKey> {
  // Dominio separado: SHA-256(SECRET || "|oauth-state") como clave.
  const raw = b64Decode(secretB64.trim());
  const dom = new Uint8Array([...raw, ...encoder.encode("|oauth-state")]);
  const keyBytes = await crypto.subtle.digest("SHA-256", dom.buffer as ArrayBuffer);
  return crypto.subtle.importKey("raw", keyBytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

export async function createOAuthState(provider: string, tenant: Tenant, secretB64: string, accountId?: number): Promise<string> {
  const payload: OAuthStatePayload = { provider, tenant, exp: Date.now() + OAUTH_STATE_TTL_MS };
  if (accountId !== undefined) {
    if (!Number.isInteger(accountId) || accountId <= 0) throw new Error("accountId OAuth no válido");
    payload.accountId = accountId;
  }
  const body = b64urlEncode(encoder.encode(JSON.stringify(payload)));
  const key = await oauthHmacKey(secretB64);
  const sig = await crypto.subtle.sign("HMAC", key, encoder.encode(body));
  return `${body}.${b64urlEncode(new Uint8Array(sig))}`;
}

export async function verifyOAuthState(
  token: string | null | undefined,
  secretB64: string,
  expectedTenant: Tenant,
): Promise<OAuthStatePayload | null> {
  if (!token) return null;
  const [body, sig] = token.trim().split(".");
  if (!body || !sig) return null;
  try {
    const key = await oauthHmacKey(secretB64);
    const ok = await crypto.subtle.verify(
      "HMAC",
      key,
      b64urlDecode(sig).buffer as ArrayBuffer,
      encoder.encode(body),
    );
    if (!ok) return null;
    const payload = JSON.parse(new TextDecoder().decode(b64urlDecode(body))) as OAuthStatePayload;
    if (typeof payload.provider !== "string" || !payload.provider) return null;
    if (!isTenant(payload.tenant)) return null;
    if (payload.tenant !== expectedTenant) return null;
    if (typeof payload.exp !== "number" || payload.exp < Date.now()) return null;
    if (payload.accountId !== undefined && (!Number.isInteger(payload.accountId) || payload.accountId <= 0)) return null;
    return payload;
  } catch {
    return null;
  }
}

/** Extrae el token de sesión de la cookie o del Bearer, el que
 *  llegue primero (mismo orden que verifySession). Null si no hay
 *  ninguno con formato body.sig. */
export function extractSessionToken(cookieHeader: string | null, authHeader: string | null): string | null {
  if (cookieHeader) {
    const match = /(?:^|;\s*)panel-session=([^;]+)/.exec(cookieHeader);
    if (match?.[1]?.includes(".")) return match[1] as string;
  }
  const bearer = /^Bearer\s+(.+)$/i.exec((authHeader ?? "").trim());
  if (bearer?.[1]?.includes(".")) return bearer[1] as string;
  return null;
}

/** Verifica la cookie de sesión. El tenant del payload debe ser
 *  `expectedTenant` (env.TENANT): la firma HMAC protege el payload
 *  contra manipulación y la comparación contra env.TENANT impide
 *  que una sesión del otro tenant valga aquí. */
export async function verifySessionCookie(
  cookieHeader: string | null,
  secretB64: string,
  expectedTenant: Tenant,
): Promise<SessionPayload | null> {
  if (!cookieHeader) return null;
  const match = /(?:^|;\s*)panel-session=([^;]+)/.exec(cookieHeader);
  if (!match) return null;
  return verifySessionToken(match[1] as string, secretB64, expectedTenant);
}

/** Verifica la sesión por cookie O por header Authorization:
 *  Bearer <token> (mismo token body.sig). Basta con que uno de
 *  los dos sea válido: la cookie cross-site puede ser bloqueada
 *  (Brave iOS) aunque el token Bearer llegue bien. En ambos casos
 *  el tenant debe ser `expectedTenant` (env.TENANT). */
export async function verifySession(
  cookieHeader: string | null,
  authHeader: string | null,
  secretB64: string,
  expectedTenant: Tenant,
): Promise<SessionPayload | null> {
  const byCookie = await verifySessionCookie(cookieHeader, secretB64, expectedTenant);
  if (byCookie) return byCookie;
  const bearer = /^Bearer\s+(.+)$/i.exec((authHeader ?? "").trim());
  if (!bearer) return null;
  return verifySessionToken(bearer[1] as string, secretB64, expectedTenant);
}

/** Verifica una contraseña contra el hash PBKDF2 guardado. */
export async function verifyPassword(password: string, hashLine: string): Promise<boolean> {
  const parts = hashLine.trim().split("$");
  // pbkdf2$iter$salt_b64$hash_b64
  if (parts.length !== 4 || parts[0] !== "pbkdf2") return false;
  const iter = Number(parts[1]);
  const salt = b64Decode(parts[2] as string);
  const expected = b64Decode(parts[3] as string);
  if (!Number.isFinite(iter) || iter < 10_000) return false;
  const derived = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt: salt.buffer as ArrayBuffer, iterations: iter, hash: "SHA-256" },
    await crypto.subtle.importKey("raw", encoder.encode(password), "PBKDF2", false, ["deriveBits"]),
    expected.length * 8,
  );
  const a = new Uint8Array(derived);
  if (a.length !== expected.length) return false;
  // Comparación en tiempo constante.
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= (a[i] as number) ^ (expected[i] as number);
  return diff === 0;
}

/** Genera la línea de hash para panel_users (solo para el script). */
export async function hashPassword(password: string, iterations = 100_000): Promise<string> {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const derived = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt: salt.buffer as ArrayBuffer, iterations, hash: "SHA-256" },
    await crypto.subtle.importKey("raw", encoder.encode(password), "PBKDF2", false, ["deriveBits"]),
    256,
  );
  return `pbkdf2$${iterations}$${b64Encode(salt)}$${b64Encode(new Uint8Array(derived))}`;
}

export { SESSION_COOKIE };
