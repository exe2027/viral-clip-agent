// Router SIN ESTADO para el tráfico del cerebro (proyecto "un Worker
// por usuario", decisión 2026-11-19).
//
// Vive en el Worker viejo `avc` (https://avc.vikingosfoodspa.workers.dev),
// cuya ÚNICA razón de seguir existiendo es que el cerebro (GitHub
// Actions, clipagent/panel_bridge.py) lleva PANEL_URL hardcodeado a esa
// URL en .github/workflows/cerebro-live.yml (3 lugares) y el repo del
// cerebro no se puede tocar en este momento. El cron del Worker viejo
// se apaga: este Worker ya NO hace nada por sí mismo, solo reenvía
// cada llamada al Worker propio del tenant que corresponde
// (avc-sebastian / avc-kaez).
//
// SIN ESTADO, por diseño:
//   - No tiene bindings D1 ni R2 (ver wrangler.router.toml).
//   - No tiene secretos: ni SESSION_SECRET ni BRIDGE_TOKEN.
//   - NO verifica firmas: el token de sesión body.sig y el state
//     OAuth se DECODIFICAN (base64url, sin secreto) solo para LEER
//     el `tenant` y elegir el destino. La verificación HMAC la hace
//     el Worker destino, que tiene el mismo SESSION_SECRET. Un token
//     falsificado o caducado se reenvía igual y el destino lo
//     rechaza con 401; el router no es un control de acceso.
//   - NO toca D1: ni lee panel_users (el mapa usuario→tenant va en
//     la var LOGIN_USER_MAP), ni marca tokens, ni registra nada.
//
// Tabla de rutas (ver ROUTER.md para el detalle y las pruebas curl):
//   GET  /api/health                  -> JSON propio del router.
//   POST /api/brain/session           -> probing: prueba sebastian y
//     luego kaez con el MISMO body; devuelve la primera 2xx tal cual.
//     Un intento fallido NO consume el token (ver nota abajo).
//   Rutas con sesión (/api/bridge/*, /api/clips*, /api/tracking/*,
//     /api/worker/*, /api/influencers, /api/jobs, /api/accounts):
//     tenant del Bearer (o de la cookie panel-session); sin sesión
//     o sin tenant -> 401. En /api/bridge/*, si no hay sesión, se
//     acepta ?tenant= como el propio panel (vía BRIDGE_TOKEN).
//   GET  /api/oauth/<google|meta>/callback -> tenant del `state`
//     (b64url-JSON {provider, tenant, exp} firmado).
//   POST /api/auth/login              -> usuario del body según
//     LOGIN_USER_MAP; usuario desconocido -> 401 genérico.
//   Cualquier otra ruta -> 404.
//
// NOTA probing /api/brain/session (seguridad del canje):
//   redeemBrainToken (worker/src/brain.ts) hace SELECT por huella
//   del token y SOLO si la fila existe, el tenant es válido, no ha
//   caducado y used_at IS NULL, ejecuta el UPDATE que marca used_at
//   (atómico, WHERE used_at IS NULL). Un token que NO está en la D1
//   de ese tenant lanza "Token del cerebro inválido." ANTES de
//   cualquier escritura. Cada tenant guarda sus tokens solo en SU
//   D1, así que probar primero en sebastian un token de kaez falla
//   sin consumirlo y el segundo intento en kaez canjea con éxito.
//   El handler HTTP devuelve 401 en ese fallo (index.ts, POST
//   /api/brain/session), que es lo que el router usa para seguir
//   probando. Si AMBOS fallan, el router devuelve el error del
//   primer intento (sebastian) tal cual.

import { b64urlDecode } from "./crypto";
import { isTenant, TENANTS, type Tenant } from "./auth";
import type { ExecutionContext } from "./cloudflare";

/** Vars del router (wrangler.router.toml). Solo URLs y el mapa de
 *  login: nada secreto, nada por tenant salvo la URL pública. */
export interface RouterEnv {
  WORKER_SEBASTIAN_URL: string;
  WORKER_KAEZ_URL: string;
  /** JSON {"usuario":"tenant", ...}. Por defecto, usuario == tenant
   *  (sebastian->sebastian, kaez->kaez; verificado en panel_users:
   *  auth.ts "tenant kaez, usuario kaez" y scripts/seed-users.sql
   *  para sebastian). Debe mantenerse igual que
   *  frontend/dist/workers.json cuando ese archivo exista. */
  LOGIN_USER_MAP?: string;
}

/** Mapa usuario->tenant por defecto (usuario == tenant). */
const DEFAULT_LOGIN_USER_MAP: Record<string, Tenant> = {
  sebastian: "sebastian",
  kaez: "kaez",
};

const ROUTER_NAME = "avc-router";

const json = (data: unknown, status = 200, headers: Record<string, string> = {}): Response =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });

const err = (message: string, status = 400): Response => json({ ok: false, error: message }, status);

/** URLs destino por tenant, desde vars (nunca hardcodeadas). */
function upstreamUrl(env: RouterEnv, tenant: Tenant): string | null {
  const raw = tenant === "sebastian" ? env.WORKER_SEBASTIAN_URL : env.WORKER_KAEZ_URL;
  const url = (raw ?? "").trim().replace(/\/+$/, "");
  return url || null;
}

function loginUserMap(env: RouterEnv): Record<string, Tenant> {
  const raw = (env.LOGIN_USER_MAP ?? "").trim();
  if (!raw) return DEFAULT_LOGIN_USER_MAP;
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const out: Record<string, Tenant> = {};
    for (const [user, tenant] of Object.entries(parsed)) {
      if (typeof tenant === "string" && isTenant(tenant)) out[user] = tenant;
    }
    return Object.keys(out).length > 0 ? out : DEFAULT_LOGIN_USER_MAP;
  } catch {
    return DEFAULT_LOGIN_USER_MAP;
  }
}

/** Decodifica el segmento body de un token body.sig (sesión u OAuth
 *  state) SIN verificar la firma: solo para leer el payload. Null si
 *  no es b64url-JSON válido. */
function decodeTokenBody<T>(token: string | null | undefined): T | null {
  if (!token) return null;
  const body = token.trim().split(".")[0];
  if (!body) return null;
  try {
    return JSON.parse(new TextDecoder().decode(b64urlDecode(body))) as T;
  } catch {
    return null;
  }
}

/** Tenant de la sesión: Authorization: Bearer <body.sig> o cookie
 *  panel-session. Solo se LEE el payload; la firma la verifica el
 *  destino. Null si no hay token o el payload no trae un tenant
 *  válido. */
function sessionTenant(req: Request): Tenant | null {
  const bearer = /^Bearer\s+(.+)$/i.exec((req.headers.get("Authorization") ?? "").trim())?.[1];
  let token: string | null = bearer ?? null;
  if (!token) {
    const cookie = req.headers.get("Cookie") ?? "";
    token = /(?:^|;\s*)panel-session=([^;]+)/.exec(cookie)?.[1] ?? null;
  }
  const payload = decodeTokenBody<{ tenant?: unknown }>(token);
  return typeof payload?.tenant === "string" && isTenant(payload.tenant) ? payload.tenant : null;
}

/** Tenant del `state` OAuth (b64url-JSON {provider, tenant, exp}).
 *  Sin verificar la firma: la verifica el destino. */
function oauthStateTenant(state: string | null): Tenant | null {
  const payload = decodeTokenBody<{ tenant?: unknown }>(state);
  return typeof payload?.tenant === "string" && isTenant(payload.tenant) ? payload.tenant : null;
}

/** Reenvía la petición tal cual al Worker del tenant: mismo método,
 *  path, query, headers y body (en streaming: el PUT del MP4 de
 *  /api/clips/:id/video, hasta 128 MB, NO se carga en memoria).
 *  Devuelve status + body + headers del destino, tal cual. */
async function forward(req: Request, base: string): Promise<Response> {
  const url = new URL(req.url);
  const target = `${base}${url.pathname}${url.search}`;
  const headers = new Headers();
  req.headers.forEach((value, name) => {
    const lower = name.toLowerCase();
    if (lower === "host" || lower === "content-length" || lower === "connection") return;
    headers.append(name, value);
  });
  const method = req.method;
  const init: RequestInit = { method, headers };
  if (method !== "GET" && method !== "HEAD") init.body = req.body;
  let upstream: Response;
  try {
    upstream = await fetch(target, init);
  } catch (e) {
    return err(`Worker ${base} no alcanzable: ${e instanceof Error ? e.message : "fetch falló"}.`, 502);
  }
  return passThrough(upstream);
}

/** Pasa una respuesta del destino tal cual (status + body +
 *  headers), añadiendo solo una cabecera de diagnóstico. */
function passThrough(res: Response): Response {
  const headers = new Headers(res.headers);
  headers.set("x-avc-router", ROUTER_NAME);
  const body = res.status === 204 || res.status === 304 || !res.body ? null : res.body;
  return new Response(body, { status: res.status, headers });
}

/** POST /api/brain/session: el body solo trae {token}, sin tenant.
 *  Prueba los Workers en orden (TENANTS: sebastian, kaez) con el
 *  MISMO body. Un intento fallido no consume el token (ver nota de
 *  cabecera). Primera 2xx -> se devuelve tal cual; si ambas fallan,
 *  se devuelve el error del PRIMER intento tal cual. */
async function routeBrainSession(req: Request, env: RouterEnv): Promise<Response> {
  let rawBody: string;
  try {
    rawBody = await req.text();
  } catch {
    return err("Cuerpo no legible.", 400);
  }
  let token = "";
  try {
    const parsed = JSON.parse(rawBody) as Record<string, unknown>;
    token = typeof parsed.token === "string" ? parsed.token : "";
  } catch {
    token = "";
  }
  if (!token) return err("Falta el token del cerebro.", 400);
  let firstFailure: Response | null = null;
  for (const tenant of TENANTS) {
    const base = upstreamUrl(env, tenant);
    if (!base) continue;
    let res: Response;
    try {
      res = await fetch(`${base}/api/brain/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: rawBody,
      });
    } catch {
      res = new Response(JSON.stringify({ ok: false, error: `Worker ${base} no alcanzable.` }), {
        status: 502,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (res.ok) return passThrough(res);
    if (!firstFailure) firstFailure = res;
  }
  if (firstFailure) return passThrough(firstFailure);
  return err("Sin Workers destino configurados (WORKER_SEBASTIAN_URL / WORKER_KAEZ_URL).", 500);
}

/** ¿Ruta autenticada con sesión? Prefijos y rutas exactas que usa
 *  el cerebro (clipagent/panel_bridge.py). */
function isSessionRoute(path: string): boolean {
  if (
    path.startsWith("/api/bridge/") ||
    path.startsWith("/api/clips") ||
    path.startsWith("/api/tracking/") ||
    path.startsWith("/api/worker/")
  ) {
    return true;
  }
  return path === "/api/influencers" || path === "/api/jobs" || path === "/api/accounts";
}

async function handleFetch(req: Request, env: RouterEnv): Promise<Response> {
  const url = new URL(req.url);
  const path = url.pathname;
  const method = req.method;

  // --- Salud: JSON propio del router, sin reenviar. ---
  if (path === "/api/health" && method === "GET") {
    return json({
      ok: true,
      app: ROUTER_NAME,
      mode: "router",
      tenants: [...TENANTS],
      upstream: {
        sebastian: upstreamUrl(env, "sebastian"),
        kaez: upstreamUrl(env, "kaez"),
      },
      time: new Date().toISOString(),
    });
  }

  // --- Canje del token de un solo uso del cerebro (probing). ---
  if (path === "/api/brain/session" && method === "POST") {
    return routeBrainSession(req, env);
  }

  // --- Callback OAuth: el `state` lleva el tenant; la redirect URI
  // registrada en Google/Meta apunta al Worker viejo y sigue
  // funcionando porque el router reenvía al Worker del tenant. ---
  const oauthMatch = /^\/api\/oauth\/(google|meta)\/callback$/.exec(path);
  if (oauthMatch && method === "GET") {
    const tenant = oauthStateTenant(url.searchParams.get("state"));
    if (!tenant) return err("state OAuth sin tenant válido.", 400);
    const base = upstreamUrl(env, tenant);
    if (!base) return err(`Sin URL destino para el tenant ${tenant}.`, 500);
    return forward(req, base);
  }

  // --- Login: solo fallback del cerebro (su vía principal es el
  // token de un solo uso). El router NO verifica la contraseña: la
  // verifica el Worker destino. Usuario desconocido -> 401 genérico
  // (mismo mensaje que el destino: no se revela si el usuario
  // existe). ---
  if (path === "/api/auth/login" && method === "POST") {
    let username = "";
    let rawBody = "";
    try {
      rawBody = await req.text();
      const parsed = JSON.parse(rawBody) as Record<string, unknown>;
      username = typeof parsed.username === "string" ? parsed.username.trim() : "";
    } catch {
      username = "";
    }
    const tenant = loginUserMap(env)[username];
    if (!tenant) return err("Usuario o contraseña incorrectos.", 401);
    const base = upstreamUrl(env, tenant);
    if (!base) return err(`Sin URL destino para el tenant ${tenant}.`, 500);
    return forward(
      new Request(req.url, { method: "POST", headers: { "Content-Type": "application/json" }, body: rawBody }),
      base,
    );
  }

  // --- Rutas con sesión: el tenant sale del Bearer (o cookie). ---
  if (isSessionRoute(path)) {
    let tenant = sessionTenant(req);
    // /api/bridge/* también acepta ?tenant= con BRIDGE_TOKEN en el
    // panel (index.ts): el router no tiene BRIDGE_TOKEN para
    // comprobarlo, así que solo usa ?tenant= para ELEGIR destino;
    // la autenticación la hace el destino, que rechaza sin sesión
    // ni BRIDGE_TOKEN válidos.
    if (!tenant && path.startsWith("/api/bridge/")) {
      const param = url.searchParams.get("tenant") ?? "";
      if (isTenant(param)) tenant = param;
    }
    if (!tenant) return err("Sin sesión.", 401);
    const base = upstreamUrl(env, tenant);
    if (!base) return err(`Sin URL destino para el tenant ${tenant}.`, 500);
    return forward(req, base);
  }

  // --- Todo lo demás (frontend, /api/auth/me, /api/auth/logout,
  // /api/public-video, onboarding, jobs/next...): el router solo
  // cubre el tráfico del cerebro. El frontend apunta directo a cada
  // Worker por usuario (frontend/dist/workers.json). ---
  return err("Ruta no cubierta por el router avc.", 404);
}

export default {
  async fetch(req: Request, env: RouterEnv, _ctx: ExecutionContext): Promise<Response> {
    return handleFetch(req, env);
  },
  // Sin scheduled(): el cron del Worker viejo muere al desplegar
  // esta configuración (wrangler.router.toml no define crons).
};
