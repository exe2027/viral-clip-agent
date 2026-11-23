// worker.js — habla con el Worker del tenant activo (D1 vía Worker).
// La D1 NUNCA se accede directa desde el navegador: solo el Worker
// la lee, con sesión del panel. Este módulo hace GETs de lectura y,
// desde v1.3.0, POST/PATCH solo a /api/campaign-submissions (escritura
// MEDIADA POR EL WORKER con sesión: registrar el envío en la cola en
// nube y marcarlo submitted; la lista de bloqueo en lib.js lo hace
// cumplir antes de tocar la red).
//
// Flujo antes de CUALQUIER envío a Content Rewards:
//   1. workers.json (panel) -> base del Worker del tenant activo.
//      Nunca hardcodeada: sale del mapa en tiempo de ejecución.
//   2. GET {base}/api/health (sin auth) -> worker_running debe ser true.
//      Si el Worker está parado o no responde, NO se envía.
//   3. GET {base}/api/clips/:id (con token de sesión del panel) ->
//      estado del clip en D1 + publish_breakdown por plataforma.
//      El clip tiene que estar PUBLICADO en esa plataforma (posted):
//      sin link publicado no hay nada que enviar.
//   4. GET {base}/api/campaign-submissions?clip_id=&platform=&campaign_id=
//      (con token) -> si existe fila pending/submitted para
//      tenant+clip+plataforma+campaña, NO se reenvía. Si la ruta aún
//      no está desplegada (404), el chequeo cae al registro local
//      (fallback anotado, nunca inventado).
//
// Sin token de sesión del panel el paso 3 no se puede hacer y la
// puerta falla CERRADA (no se envía). El token lo pega el usuario en
// el popup (como la API key) y vive solo en chrome.storage.local.
// La extensión NO lee el localStorage del panel (otro origen) y no
// tiene content scripts: no hay forma automática y silenciosa de
// tomar la sesión, y así debe ser.

import {
  WORKERS_JSON_URL,
  CAMPAIGN_SUBMISSIONS_PENDING,
  decideWorkerGate,
  isForbiddenWorkerCall,
  parseClipState,
  parseHealth,
  resolveWorkerBase,
} from "./lib.js";

/** fetch inyectable (tests en Node pasan un stub; en Chrome es el global). */
function doFetch(fetchImpl, url, init) {
  const f = fetchImpl || globalThis.fetch;
  return f(url, init);
}

/**
 * GET con lista de bloqueo. Rechaza cualquier llamada prohibida
 * ANTES de tocar la red: métodos distintos de GET y rutas de
 * start/stop/run-once/phase/event. Esta es la garantía de que la
 * extensión jamás apaga (ni enciende, ni reinicia) el worker.
 */
export async function workerFetch(base, path, { method = "GET", token = null, fetchImpl = null } = {}) {
  const url = `${String(base).replace(/\/+$/, "")}${path}`;
  if (isForbiddenWorkerCall(url, method)) {
    throw new Error(`Llamada al Worker bloqueada por la lista de bloqueo: ${method} ${path}. La extensión solo hace GETs de lectura.`);
  }
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  return doFetch(fetchImpl, url, { method, headers, cache: "no-store" });
}

/** Lee el mapa usuario -> base del Worker desde el panel (público). */
export async function fetchWorkersMap(fetchImpl = null) {
  const res = await doFetch(fetchImpl, WORKERS_JSON_URL, { cache: "no-store" });
  if (!res.ok) throw new Error(`workers.json devolvió HTTP ${res.status}.`);
  const map = await res.json();
  if (!map || typeof map !== "object" || Array.isArray(map) || Object.keys(map).length === 0) {
    throw new Error("workers.json vacío o inválido.");
  }
  return map;
}

/**
 * Base del Worker del tenant activo. Siempre desde workers.json.
 * Si la lectura falla y hay una base guardada de antes, se usa esa
 * (y se avisa con `fromSavedBase: true`); si no, no hay base.
 */
export async function getWorkerBase({ tenant, savedBase = null, fetchImpl = null } = {}) {
  try {
    const map = await fetchWorkersMap(fetchImpl);
    const base = resolveWorkerBase(map, tenant);
    if (base) return { base, tenant, fromSavedBase: false, error: null };
    return { base: null, tenant, fromSavedBase: false, error: `Tenant "${tenant}" sin base en workers.json.` };
  } catch (err) {
    if (savedBase) return { base: savedBase, tenant, fromSavedBase: true, error: null };
    return { base: null, tenant, fromSavedBase: false, error: String(err && err.message ? err.message : err) };
  }
}

/** GET /api/health (sin auth). Devuelve { running, tenant, time, error }. */
export async function checkHealth(base, fetchImpl = null) {
  try {
    const res = await workerFetch(base, "/api/health", { fetchImpl });
    if (!res.ok) return { running: false, tenant: null, error: `Salud: HTTP ${res.status}.` };
    return parseHealth(await res.json());
  } catch (err) {
    return { running: false, tenant: null, error: `Worker sin respuesta: ${String(err && err.message ? err.message : err)}` };
  }
}

/**
 * GET /api/clips/:id (con sesión del panel). Devuelve
 * { clipState, notFound, unauthorized, error }.
 */
export async function checkClip(base, token, clipId, platform, fetchImpl = null) {
  if (!token) return { clipState: null, notFound: false, unauthorized: false, error: "no_panel_session" };
  const id = Number(clipId);
  if (!Number.isInteger(id) || id <= 0) return { clipState: null, notFound: false, unauthorized: false, error: "clip_id no numérico: no se puede consultar D1." };
  try {
    const res = await workerFetch(base, `/api/clips/${id}`, { token, fetchImpl });
    if (res.status === 401) return { clipState: null, notFound: false, unauthorized: true, error: "Sesión del panel inválida o caducada (401)." };
    if (res.status === 404) return { clipState: null, notFound: true, unauthorized: false, error: "Clip no encontrado en D1 (404)." };
    if (!res.ok) return { clipState: null, notFound: false, unauthorized: false, error: `Clip: HTTP ${res.status}.` };
    return { clipState: parseClipState(await res.json(), platform), notFound: false, unauthorized: false, error: null };
  } catch (err) {
    return { clipState: null, notFound: false, unauthorized: false, error: String(err && err.message ? err.message : err) };
  }
}

/**
 * Chequeo de submissions (0041, v1.3.0). GET real a la ruta del
 * Worker; si la ruta aún no está desplegada (404/405 o red caída),
 * devuelve available: false con fallback local — nunca inventa que
 * consultó D1. `rows` son las filas de ese clip+plataforma+campaña.
 */
export async function checkCampaignSubmissions(base, token, { clipId, platform, campaignId, fetchImpl = null } = {}) {
  if (!token) return { available: false, rows: [], error: "no_panel_session", fallback: "local" };
  const id = Number(clipId);
  if (!Number.isInteger(id) || id <= 0) return { available: false, rows: [], error: "clip_id no numérico: no se puede consultar D1.", fallback: "local" };
  const qs = new URLSearchParams({ clip_id: String(id) });
  if (platform) qs.set("platform", platform);
  if (campaignId) qs.set("campaign_id", campaignId);
  try {
    const res = await workerFetch(base, `/api/campaign-submissions?${qs.toString()}`, { token, fetchImpl });
    if (res.status === 404 || res.status === 405) {
      return { available: false, rows: [], error: `Ruta aún no desplegada en este Worker (HTTP ${res.status}).`, fallback: "local" };
    }
    if (res.status === 401) return { available: false, rows: [], error: "Sesión del panel inválida o caducada (401).", fallback: "local" };
    if (!res.ok) return { available: false, rows: [], error: `Submissions: HTTP ${res.status}.`, fallback: "local" };
    const data = await res.json();
    return { available: true, rows: Array.isArray(data.submissions) ? data.submissions : [], error: null, fallback: null };
  } catch (err) {
    return { available: false, rows: [], error: String(err && err.message ? err.message : err), fallback: "local" };
  }
}

/**
 * Cola en nube: pendientes ya publicadas de este tenant
 * (GET /api/campaign-submissions?pending=1). Lo que la extensión
 * retoma al reabrir Chrome tras un cierre: la cola vive en D1, no
 * en el Chrome. 404 = ruta aún no desplegada -> lista vacía.
 */
export async function listPendingSubmissions(base, token, fetchImpl = null) {
  if (!token) return { available: false, rows: [], error: "no_panel_session" };
  try {
    const res = await workerFetch(base, "/api/campaign-submissions?pending=1", { token, fetchImpl });
    if (res.status === 404 || res.status === 405) return { available: false, rows: [], error: `Ruta aún no desplegada (HTTP ${res.status}).` };
    if (!res.ok) return { available: false, rows: [], error: `Pendientes: HTTP ${res.status}.` };
    const data = await res.json();
    return { available: true, rows: Array.isArray(data.submissions) ? data.submissions : [], error: null };
  } catch (err) {
    return { available: false, rows: [], error: String(err && err.message ? err.message : err) };
  }
}

/**
 * Analíticas: TODAS las filas de campaign_submissions de este tenant
 * (GET /api/campaign-submissions sin clip_id, con sesión). Fuente de
 * las analíticas del popup (vía summarizeAnalytics en lib.js). 404 =
 * ruta aún no desplegada -> available: false (el popup muestra "sin
 * datos todavía", nunca ceros inventados). Sin token: igual, sin
 * inventar.
 */
export async function listAllSubmissions(base, token, fetchImpl = null) {
  if (!token) return { available: false, rows: null, error: "no_panel_session" };
  try {
    const res = await workerFetch(base, "/api/campaign-submissions", { token, fetchImpl });
    if (res.status === 404 || res.status === 405) return { available: false, rows: null, error: `Ruta aún no desplegada en este Worker (HTTP ${res.status}).` };
    if (res.status === 401) return { available: false, rows: null, error: "Sesión del panel inválida o caducada (401)." };
    if (!res.ok) return { available: false, rows: null, error: `Analíticas: HTTP ${res.status}.` };
    const data = await res.json();
    return { available: true, rows: Array.isArray(data.submissions) ? data.submissions : [], error: null };
  } catch (err) {
    return { available: false, rows: null, error: String(err && err.message ? err.message : err) };
  }
}

/**
 * Registra el envío en la cola en nube (POST /api/campaign-submissions,
 * con sesión). `publishedAtMs` es el instante REAL de publicación;
 * si es null, la fila nace sin publicar (aún no enviable: markPosted
 * la rellena). 409 = ya existe (duplicado): se devuelve la fila.
 * 404 = ruta aún no desplegada: no es un error fatal, el envío sigue
 * con el registro local (available: false).
 */
export async function registerCampaignSubmission(base, token, payload, fetchImpl = null) {
  if (!token) return { available: false, row: null, duplicate: false, error: "no_panel_session" };
  const url = `${String(base).replace(/\/+$/, "")}/api/campaign-submissions`;
  if (isForbiddenWorkerCall(url, "POST")) {
    return { available: false, row: null, duplicate: false, error: "Llamada al Worker bloqueada por la lista de bloqueo: POST /api/campaign-submissions." };
  }
  try {
    const res = await doFetch(fetchImpl, url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(payload),
    });
    if (res.status === 404 || res.status === 405) return { available: false, row: null, duplicate: false, error: `Ruta aún no desplegada (HTTP ${res.status}).` };
    if (res.status === 401) return { available: false, row: null, duplicate: false, error: "Sesión del panel inválida o caducada (401)." };
    const data = await res.json().catch(() => ({}));
    if (res.status === 409) return { available: true, row: data.submission ?? null, duplicate: true, error: null };
    if (!res.ok) return { available: true, row: null, duplicate: false, error: data.error || `Registro: HTTP ${res.status}.` };
    return { available: true, row: data.submission ?? null, duplicate: false, error: null };
  } catch (err) {
    return { available: false, row: null, duplicate: false, error: String(err && err.message ? err.message : err) };
  }
}

/** Marca un envío como submitted/failed (PATCH /:id, con sesión). */
export async function updateCampaignSubmission(base, token, id, patch, fetchImpl = null) {
  if (!token) return { available: false, row: null, error: "no_panel_session" };
  const numId = Number(id);
  if (!Number.isInteger(numId) || numId <= 0) return { available: false, row: null, error: "id de envío no numérico." };
  const url = `${String(base).replace(/\/+$/, "")}/api/campaign-submissions/${numId}`;
  if (isForbiddenWorkerCall(url, "PATCH")) {
    return { available: false, row: null, error: "Llamada al Worker bloqueada por la lista de bloqueo: PATCH /api/campaign-submissions/:id." };
  }
  try {
    const res = await doFetch(fetchImpl, url, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(patch),
    });
    if (res.status === 404 || res.status === 405) return { available: false, row: null, error: `Ruta aún no desplegada (HTTP ${res.status}).` };
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return { available: true, row: null, error: data.error || `Actualización: HTTP ${res.status}.` };
    return { available: true, row: data.submission ?? null, error: null };
  } catch (err) {
    return { available: false, row: null, error: String(err && err.message ? err.message : err) };
  }
}

/**
 * Puerta completa antes de enviar. Devuelve { ok, code, reason,
 * health, clipCheck, submissions }.
 * `panelToken` es el token de sesión del panel (Bearer); sin él,
 * la puerta falla cerrada.
 */
export async function checkWorkerGate({
  base,
  panelToken = null,
  clipId,
  platform,
  postUrl,
  campaignId,
  localSubmissions = [],
  fetchImpl = null,
} = {}) {
  const health = await checkHealth(base, fetchImpl);
  const clip = panelToken
    ? await checkClip(base, panelToken, clipId, platform, fetchImpl)
    : { clipState: null, notFound: false, unauthorized: false, error: "no_panel_session" };
  const submissions = panelToken
    ? await checkCampaignSubmissions(base, panelToken, { clipId, platform, campaignId, fetchImpl })
    : { available: false, rows: [], error: "no_panel_session", fallback: "local" };
  const gate = decideWorkerGate({
    health,
    clipState: clip.clipState,
    clipNotFound: clip.notFound,
    noPanelSession: !panelToken || clip.unauthorized,
    localSubmissions,
    submissions: submissions.rows,
    submissionsAvailable: submissions.available,
    postUrl,
    campaignId,
    platform,
  });
  // Si decideWorkerGate bloqueó por "sin sesión" pero el clip devolvió
  // 401, el motivo exacto es la sesión caducada.
  if (!gate.ok && clip.unauthorized && gate.code === "no_panel_session") {
    gate.reason = "Sesión del panel inválida o caducada (401): vuelve a pegar el token de sesión del panel en el popup. Sin ese chequeo no se envía.";
  }
  return {
    ...gate,
    health: { running: health.running, tenant: health.tenant, error: health.error || null },
    clipCheck: gate.clipCheck || null,
    clipError: clip.error,
    submissions: {
      available: submissions.available,
      rows: submissions.rows,
      error: submissions.error,
      fallback: submissions.fallback || null,
      table: CAMPAIGN_SUBMISSIONS_PENDING.table,
      route: CAMPAIGN_SUBMISSIONS_PENDING.proposedRoute,
    },
  };
}
