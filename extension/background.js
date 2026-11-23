// background.js — service worker (Manifest V3, type: module).
// v1.3.0 (decisiones 2026-11-23 18:19 + 18:32, login por tenant):
// envío AUTOMÁTICO (decisión 5) — AVC_CLIP_PUBLISHED sin campaña
// asignada recibe la mejor del día del selector dinámico (decisión 2)
// que COINCIDA con el streamer del clip, y se envía solo. Alarma
// cada 5 min: salud de AMBOS tenants + reintento de TODOS los
// bloqueos reparables dentro de la ventana + retoma de la cola en
// nube (filas pending en D1) si Chrome estuvo cerrado.
// Puerta del Worker (v2, corregida v1.3.0): salud + clip en D1
// (tiene que estar PUBLICADO en esa plataforma: posted es el
// requisito, no el duplicado) + fila en campaign_submissions (el
// duplicado real). La extensión SOLO escribe en el Worker en
// /api/campaign-submissions (registro en la cola en nube y marcar
// submitted); jamás llama a /api/worker/start, /api/worker/stop,
// /api/worker/run-once, /api/worker/phase ni /api/worker/event: la
// lista de bloqueo está en lib.js y workerFetch la hace cumplir.
//
// LOGIN POR TENANT (v1.3.0): API key de Content Rewards POR TENANT
// (apiKey_seba / apiKey_kaez) y token de sesión del panel POR TENANT
// (panelToken_seba / panelToken_kaez), en chrome.storage.local. Nada
// se comparte entre seba y kaez: la sesión de seba da 401 en kaez
// (TTL 30 días) y cada uno envía con SU clave. Las claves viejas
// (apiKey / panelToken sin sufijo) migran a seba al leerlas.
// KAEZ PENDIENTE: kaez aún no tiene cuenta propia de Content Rewards
// (nadie la inventa): su login queda preparado, pero sin su API key
// sus envíos quedan en needs_api_key hasta que él pegue la suya.
//
// Qué NO hace: no publica, no pide ni guarda contraseñas de redes
// sociales, no hace login, no hace clicks en páginas, no scrapea,
// no reenvía. Sin token de sesión del panel la puerta falla CERRADA
// (no se envía): sin consultar D1 no se puede garantizar que no se
// duplica. Sin publishedAtMs REAL no se envía: nunca se inventa con
// Date.now().
//
// Si Chrome/PC se cierra, el service worker se pausa; los envíos
// pendientes viven en D1 (campaign_submissions, cola en nube) y se
// retoman al reabrir Chrome.

import {
  MCP_URL,
  KNOWN_CAMPAIGNS,
  validateSubmission,
  buildMcpPayload,
  submissionKey,
  assignmentKey,
  minutesLeft,
  selectBestCampaignForClip,
  getCampaignBrief,
  parseMcpHttpResponse,
  AUTO_SETUP_URL,
  parseAutoSetup,
  isRetryableBlocked,
  isWithinWindow,
  summarizeAllAnalytics,
} from "./lib.js";
import {
  checkWorkerGate,
  getWorkerBase,
  checkHealth,
  registerCampaignSubmission,
  updateCampaignSubmission,
  listPendingSubmissions,
  listAllSubmissions,
} from "./worker.js";

const STORE_KEY = "avc_cr_v1";

async function loadState() {
  const out = await chrome.storage.local.get(STORE_KEY);
  return out[STORE_KEY] || {
    submissions: [],
    assignments: {},
    apiKeySet: false,
    lastCheck: null,
    // v1.2.0, decisión 7: AMBOS tenants activos por defecto (seba y kaez).
    // `worker` es el tenant activo en el popup; `workers` guarda la salud
    // de cada tenant (decisión 6: worker 24/7, reintento cada 5 min).
    worker: { tenant: "seba", base: null, running: null, checkedAt: null, error: null },
    workers: {
      seba: { tenant: "seba", base: null, running: null, checkedAt: null, error: null },
      kaez: { tenant: "kaez", base: null, running: null, checkedAt: null, error: null },
    },
    clipCheck: null,
  };
}

async function saveState(state) {
  await chrome.storage.local.set({ [STORE_KEY]: state });
}

// ---- Credenciales POR TENANT (v1.3.0) -------------------------------------
// Claves con sufijo de tenant; las viejas sin sufijo migran a seba.
async function getApiKey(tenant) {
  const out = await chrome.storage.local.get([`apiKey_${tenant}`, "apiKey"]);
  const key = out[`apiKey_${tenant}`] || (tenant === "seba" ? out.apiKey : null);
  return typeof key === "string" && key.trim() ? key.trim() : null;
}

async function getPanelToken(tenant) {
  const out = await chrome.storage.local.get([`panelToken_${tenant}`, "panelToken"]);
  const token = out[`panelToken_${tenant}`] || (tenant === "seba" ? out.panelToken : null);
  return typeof token === "string" && token.trim() ? token.trim() : null;
}

async function getSavedBase() {
  const out = await chrome.storage.local.get("workerBase");
  return typeof out.workerBase === "string" && out.workerBase.trim() ? out.workerBase.trim() : null;
}

// ---- Auto-configuración desde la web (v1.3.0) ---------------------------
// Al instalarse y cada hora (en la alarma de 5 min, con throttle),
// descarga https://avc-em9.pages.dev/extension/auto-setup.json y
// guarda solo su versión y su fecha: los valores efectivos ya son
// los mismos en lib.js, así que si la web no responde nada se rompe
// (best-effort). La extensión NO se pasa por el chat como archivos:
// se descarga el ZIP desde la web y ya viene configurada.
async function refreshAutoSetup(state, fetchImpl = fetch) {
  const last = state.autoSetup?.fetchedAt ? Date.parse(state.autoSetup.fetchedAt) : 0;
  if (Date.now() - last < 60 * 60 * 1000) return state.autoSetup || null;
  try {
    const res = await fetchImpl(AUTO_SETUP_URL);
    if (!res || !res.ok) return state.autoSetup || null;
    const parsed = parseAutoSetup(await res.json());
    if (!parsed.ok) return state.autoSetup || null;
    state.autoSetup = { version: parsed.version, campaignCount: parsed.campaignCount, fetchedAt: new Date().toISOString(), source: AUTO_SETUP_URL };
    await saveState(state);
    return state.autoSetup;
  } catch {
    return state.autoSetup || null;
  }
}

// ---- Puerta del Worker (v2, corregida v1.3.0) ----------------------------
// Resuelve la base desde workers.json, consulta salud + clip en D1 +
// submissions en D1 y devuelve la puerta. Guarda el estado del worker
// en el state para que el popup lo muestre. Si no hay base, no pasa.
async function runWorkerGate(state, { tenant, clipId, platform, postUrl, campaignId }) {
  const { base, error } = await getWorkerBase({ tenant, savedBase: await getSavedBase() });
  if (!base) {
    state.worker = { ...state.worker, tenant, base: null, running: null, checkedAt: new Date().toISOString(), error: error || "Sin base del Worker (workers.json no legible)." };
    await saveState(state);
    return { ok: false, code: "no_worker_base", reason: `Sin base del Worker: ${state.worker.error} No se envía sin consultar D1.` };
  }
  const panelToken = await getPanelToken(tenant);
  const gate = await checkWorkerGate({
    base,
    panelToken,
    clipId,
    platform,
    postUrl,
    campaignId,
    tenant,
    localSubmissions: state.submissions,
  });
  state.worker = {
    tenant,
    base,
    running: gate.health.running,
    checkedAt: new Date().toISOString(),
    error: gate.health.error || null,
  };
  // Salud por tenant (decisión 7): el chequeo de un tenant no pisa al otro.
  state.workers = state.workers || {};
  state.workers[tenant] = { ...state.worker };
  state.clipCheck = gate.clipCheck
    ? { ...gate.clipCheck, clipId, platform, checkedAt: state.worker.checkedAt, submissions: gate.submissions }
    : { clipId, platform, checkedAt: state.worker.checkedAt, error: gate.clipError || gate.reason, submissions: gate.submissions };
  await chrome.storage.local.set({ workerBase: base });
  await saveState(state);
  return gate;
}

// ---- Envío por MCP oficial -----------------------------------------------
// POST https://mcp.contentrewards.com/mcp, Authorization: Bearer <apiKey>.
// La respuesta se parsea con parseMcpHttpResponse: un 200 con `error`
// es un FALLO, no un éxito; el submission_id se guarda en D1.
async function submitViaMcp(apiKey, { campaignId, postUrl, platform }) {
  const res = await fetch(MCP_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify(buildMcpPayload({ campaignId, postUrl, platform })),
  });
  const text = await res.text();
  const parsed = parseMcpHttpResponse(res.status, text);
  if (!parsed.ok) throw new Error(parsed.error);
  return parsed; // { ok: true, submissionId, result }
}

async function doSubmit({ clipId, platform, postUrl, campaignId, campaignName, publishedAtMs, confirmed, tenant = null, auto = false, clipStreamer = null }) {
  const state = await loadState();
  const activeTenant = tenant || state.worker.tenant || "seba";
  // v1.3.0: sin publishedAtMs REAL no se envía. Nunca Date.now().
  if (publishedAtMs === undefined || publishedAtMs === null || !Number.isFinite(Number(publishedAtMs)) || Number(publishedAtMs) <= 0) {
    const record = { key: submissionKey({ tenant: activeTenant, postUrl, campaignId }), tenant: activeTenant, clipId, platform, postUrl, campaignId, campaignName: campaignName || campaignId, publishedAtMs: null, status: "blocked", blockedBy: "missing_published_at", error: "Falta el instante real de publicación (publishedAtMs): sin él no se puede comprobar la ventana de 30 min. Nunca se inventa.", createdAt: Date.now() };
    const fresh = await loadState();
    fresh.submissions.push(record);
    await saveState(fresh);
    return { ok: false, reason: record.error, gate: "missing_published_at" };
  }
  const check = validateSubmission(state.submissions, { tenant: activeTenant, postUrl, campaignId, platform, publishedAtMs, confirmed, auto });
  if (!check.ok) return { ok: false, reason: check.reason };

  // v2 — puerta del Worker ANTES de tocar Content Rewards.
  const gate = await runWorkerGate(state, { tenant: activeTenant, clipId, platform, postUrl, campaignId });
  if (!gate.ok) {
    const record = { key: submissionKey({ tenant: activeTenant, postUrl, campaignId }), tenant: activeTenant, clipId, platform, postUrl, campaignId, campaignName: campaignName || campaignId, publishedAtMs, status: "blocked", blockedBy: gate.code, error: gate.reason, createdAt: Date.now(), minutesLeft: minutesLeft(publishedAtMs) };
    const fresh = await loadState();
    fresh.submissions.push(record);
    await saveState(fresh);
    return { ok: false, reason: gate.reason, gate: gate.code };
  }

  const apiKey = await getApiKey(activeTenant);
  if (!apiKey) {
    // Sin API key no se inventa nada: se deja pendiente y se abre la vía manual.
    const record = { key: submissionKey({ tenant: activeTenant, postUrl, campaignId }), tenant: activeTenant, clipId, platform, postUrl, campaignId, campaignName: campaignName || campaignId, publishedAtMs, status: "needs_api_key", createdAt: Date.now(), minutesLeft: minutesLeft(publishedAtMs) };
    const fresh = await loadState();
    fresh.submissions.push(record);
    await saveState(fresh);
    return { ok: false, reason: `Falta la API key de Content Rewards de ${activeTenant} (se pega en el popup, una por tenant). Envío dejado en pendiente, sin enviar.`, record };
  }

  // Cola en nube: registra el envío en D1 (pending) ANTES de tocar el
  // MCP, con el publishedAtMs real. Si la ruta aún no está desplegada
  // (404), se sigue con el registro local y queda anotado.
  const base = state.worker.base;
  const panelToken = await getPanelToken(activeTenant);
  const campaign = KNOWN_CAMPAIGNS.find((c) => c.id === campaignId) || null;
  let submissionRowId = null;
  let cloudQueue = "unavailable";
  if (base && panelToken) {
    const reg = await registerCampaignSubmission(base, panelToken, {
      clip_id: Number(clipId),
      platform,
      marketplace: "content_rewards",
      campaign_id: campaignId,
      campaign_name: campaignName || campaignId,
      streamer: clipStreamer || (campaign ? campaign.streamer : null),
      gross_rate_per_1k: campaign ? campaign.rate : null,
      net_rate_per_1k: campaign ? campaign.rate * 0.9 : null,
      min_views: campaign ? campaign.minViews ?? null : null,
      cap_per_clip: campaign ? campaign.capPerClip ?? null : null,
      post_url: postUrl,
      published_at_ms: Number(publishedAtMs),
    });
    if (reg.available) {
      cloudQueue = "registered";
      if (reg.row && typeof reg.row.id === "number") submissionRowId = reg.row.id;
      if (reg.duplicate) cloudQueue = "already_registered";
    }
  }

  try {
    const mcp = await submitViaMcp(apiKey, { campaignId, postUrl, platform });
    const record = { key: submissionKey({ tenant: activeTenant, postUrl, campaignId }), tenant: activeTenant, clipId, platform, postUrl, campaignId, campaignName: campaignName || campaignId, publishedAtMs, status: "submitted", submissionId: mcp.submissionId || null, submissionRowId, cloudQueue, createdAt: Date.now(), minutesLeft: minutesLeft(publishedAtMs), workerCheckedAt: state.worker.checkedAt, clipStatus: state.clipCheck?.clipStatus ?? null };
    const fresh = await loadState();
    fresh.submissions.push(record);
    await saveState(fresh);
    // Marca submitted en D1 (best-effort: si falla, el envío YA salió;
    // queda anotado como syncWarning, no se reenvía por esto).
    if (base && panelToken && submissionRowId !== null) {
      const upd = await updateCampaignSubmission(base, panelToken, submissionRowId, { status: "submitted", submission_id: mcp.submissionId || null });
      if (!upd.available || upd.error) {
        const s2 = await loadState();
        const last = s2.submissions[s2.submissions.length - 1];
        if (last && last.key === record.key) last.syncWarning = upd.error || "sin ruta de submissions";
        await saveState(s2);
      }
    }
    return { ok: true, record };
  } catch (err) {
    const record = { key: submissionKey({ tenant: activeTenant, postUrl, campaignId }), tenant: activeTenant, clipId, platform, postUrl, campaignId, campaignName: campaignName || campaignId, publishedAtMs, status: "failed", blockedBy: "mcp_error", submissionRowId, cloudQueue, error: String(err && err.message ? err.message : err), createdAt: Date.now() };
    const fresh = await loadState();
    fresh.submissions.push(record);
    await saveState(fresh);
    return { ok: false, reason: `El MCP devolvió un error: ${record.error}`, record };
  }
}

// ---- Consulta del worker sin enviar (popup: "Consultar worker ahora") ----
async function checkWorkerNow({ tenant, clipId, platform }) {
  const state = await loadState();
  if (tenant && tenant !== state.worker.tenant) {
    state.worker.tenant = tenant;
    await saveState(state);
  }
  const { base, error, fromSavedBase } = await getWorkerBase({ tenant: state.worker.tenant, savedBase: await getSavedBase() });
  if (!base) {
    state.worker = { ...state.worker, base: null, running: null, checkedAt: new Date().toISOString(), error: error || "Sin base del Worker." };
    await saveState(state);
    return { ok: false, reason: state.worker.error, worker: state.worker, clipCheck: state.clipCheck };
  }
  const panelToken = await getPanelToken(state.worker.tenant);
  const gate = await checkWorkerGate({
    base,
    panelToken,
    clipId: clipId || null,
    platform: platform || "tiktok",
    postUrl: "",
    campaignId: "",
    tenant: state.worker.tenant,
    localSubmissions: state.submissions,
  });
  // checkWorkerGate con postUrl/campaignId vacíos no es un envío: solo
  // nos interesa su salud + clip; el "gate" de envío no aplica aquí.
  state.worker = { tenant: state.worker.tenant, base, running: gate.health.running, checkedAt: new Date().toISOString(), error: gate.health.error || null, fromSavedBase: !!fromSavedBase };
  state.workers = state.workers || {};
  state.workers[state.worker.tenant] = { ...state.worker };
  if (clipId && panelToken) {
    state.clipCheck = gate.clipCheck
      ? { ...gate.clipCheck, clipId, platform, checkedAt: state.worker.checkedAt, submissions: gate.submissions }
      : { clipId, platform, checkedAt: state.worker.checkedAt, error: gate.clipError || null, submissions: gate.submissions };
  }
  await chrome.storage.local.set({ workerBase: base });
  await saveState(state);
  return { ok: gate.health.running === true, reason: gate.health.error || (gate.health.running ? "" : "Worker parado."), worker: state.worker, clipCheck: state.clipCheck, submissionsAvailable: gate.submissions.available };
}

// ---- Analíticas (2026-11-23 18:48) ----------------------------------------
// Lee campaign_submissions de AMBOS Workers (uno por tenant, cada uno
// con SU token de sesión) y resume con summarizeAllAnalytics (lib.js).
// Sin token o con ruta no desplegada (404), ese tenant sale
// available: false: el popup muestra "sin datos todavía" con el
// motivo, nunca ceros inventados. Solo lectura (GET).
async function getAnalytics() {
  const perTenant = {};
  for (const tenant of ["seba", "kaez"]) {
    try {
      const { base, error } = await getWorkerBase({ tenant, savedBase: null });
      if (!base) { perTenant[tenant] = null; continue; }
      const token = await getPanelToken(tenant);
      const res = await listAllSubmissions(base, token);
      perTenant[tenant] = res.available ? res.rows : null;
      if (!res.available) perTenant[`${tenant}Error`] = res.error || error || null;
    } catch (err) {
      perTenant[tenant] = null;
      perTenant[`${tenant}Error`] = String(err && err.message ? err.message : err);
    }
  }
  const summary = summarizeAllAnalytics(perTenant);
  return { ok: true, analytics: summary, errors: { seba: perTenant.sebaError || null, kaez: perTenant.kaezError || null }, fetchedAt: new Date().toISOString() };
}

// ---- Mensajes: panel AVC, popup y Muse ------------------------------------
// Tipos:
//   { type: "AVC_CLIP_PUBLISHED", clipId, platform, postUrl, campaignId?, campaignName?, publishedAtMs, clipStreamer?, tenant? }
//     -> si hay campaignId asignada, intenta el envío (puerta Worker + 30 min).
//   { type: "AVC_ASSIGN_CAMPAIGN", clipId, platform, campaignId, campaignName, clipStreamer?, tenant? }
//   { type: "AVC_SUBMIT_CLIP", ...mismo payload que doSubmit, confirmed: true }
//   { type: "AVC_CHECK_WORKER", tenant?, clipId?, platform? } -> salud + clip en D1, sin enviar.
//   { type: "AVC_GET_STATE" } -> { state }
//   { type: "AVC_GET_ANALYTICS" } -> { analytics } (por tenant + total, desde D1)
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    try {
      if (msg.type === "AVC_GET_STATE") {
        sendResponse({ ok: true, state: await loadState() });
        return;
      }
      if (msg.type === "AVC_GET_ANALYTICS") {
        // Analíticas por tenant + total, desde campaign_submissions
        // en D1 (GET /api/campaign-submissions de cada Worker).
        // Si la ruta no está desplegada o no hay token, cada tenant
        // sale available: false y el popup muestra "sin datos
        // todavía" con el motivo, nunca ceros inventados.
        sendResponse(await getAnalytics());
        return;
      }
      if (msg.type === "AVC_CHECK_WORKER") {
        sendResponse(await checkWorkerNow(msg));
        return;
      }
      if (msg.type === "AVC_ASSIGN_CAMPAIGN") {
        const state = await loadState();
        const tenant = msg.tenant || state.worker.tenant || "seba";
        const key = assignmentKey({ tenant, clipId: msg.clipId, platform: msg.platform });
        // UN clip = UN marketplace: la asignación guarda el marketplace
        // (por defecto Content Rewards, el único con envío automático).
        state.assignments[key] = { campaignId: msg.campaignId, campaignName: msg.campaignName || msg.campaignId, clipStreamer: msg.clipStreamer || null, marketplace: msg.marketplace || "content_rewards", assignedAt: Date.now() };
        await saveState(state);
        sendResponse({ ok: true });
        return;
      }
      if (msg.type === "AVC_CLIP_PUBLISHED") {
        // v1.3.0, decisión 5 — ENVÍO AUTOMÁTICO: no espera ningún click.
        // Sin publishedAtMs REAL no se envía (nunca Date.now()).
        if (msg.publishedAtMs === undefined || msg.publishedAtMs === null || !Number.isFinite(Number(msg.publishedAtMs)) || Number(msg.publishedAtMs) <= 0) {
          sendResponse({ ok: false, reason: "Falta publishedAtMs real en AVC_CLIP_PUBLISHED: el panel/Worker debe mandar el instante en que el job pasó a posted. Sin él no se envía.", gate: "missing_published_at" });
          return;
        }
        const state = await loadState();
        const tenant = msg.tenant || state.worker.tenant || "seba";
        const key = assignmentKey({ tenant, clipId: msg.clipId, platform: msg.platform });
        let assignment = state.assignments[key];
        if (!assignment) {
          // Selector dinámico (decisión 2) + streamer del clip (v1.3.0):
          // sin streamer no se auto-asigna; si no coincide, no se envía.
          const { campaign, reason } = selectBestCampaignForClip(KNOWN_CAMPAIGNS, {
            platform: msg.platform,
            clipStreamer: msg.clipStreamer || null,
            today: new Date().toISOString().slice(0, 10),
          });
          if (!campaign) {
            const why = reason === "sin_streamer"
              ? "Sin streamer del clip no se auto-asigna campaña: el mensaje AVC_CLIP_PUBLISHED debe traer clipStreamer."
              : `Ninguna campaña viable hoy para ${msg.clipStreamer || "ese streamer"} en ${msg.platform} (todas agotadas, bajo el piso neto o no la aceptan): no se envía.`;
            sendResponse({ ok: false, reason: why });
            return;
          }
          const brief = getCampaignBrief(campaign.id);
          assignment = { campaignId: campaign.id, campaignName: campaign.name, assignedAt: Date.now(), autoAssigned: true, score: campaign.score, netRate: campaign.netRate, stale: campaign.stale, briefIncomplete: !!brief.incomplete, clipStreamer: msg.clipStreamer || null, marketplace: "content_rewards" };
          state.assignments[key] = assignment;
          await saveState(state);
        } else if (assignment.clipStreamer && msg.clipStreamer && assignment.clipStreamer !== msg.clipStreamer) {
          sendResponse({ ok: false, reason: `La campaña asignada es de ${assignment.clipStreamer} pero el clip es de ${msg.clipStreamer}: no coincide, no se envía.` });
          return;
        }
        const result = await doSubmit({
          clipId: msg.clipId, platform: msg.platform, postUrl: msg.postUrl,
          campaignId: assignment.campaignId, campaignName: assignment.campaignName,
          publishedAtMs: msg.publishedAtMs, confirmed: true, auto: true,
          tenant,
          clipStreamer: msg.clipStreamer || assignment.clipStreamer || null,
        });
        sendResponse(result);
        return;
      }
      if (msg.type === "AVC_SUBMIT_CLIP") {
        sendResponse(await doSubmit(msg));
        return;
      }
      sendResponse({ ok: false, reason: "Mensaje desconocido." });
    } catch (err) {
      sendResponse({ ok: false, reason: String(err && err.message ? err.message : err) });
    }
  })();
  return true; // respuesta asíncrona
});

// ---- Alarma cada 5 min (decisión 6: worker 24/7) ------------------------
// Solo lectura de salud (GET /api/health) de AMBOS tenants (seba y kaez),
// nunca enciende ni apaga nada. Después:
//   (a) reintenta los envíos bloqueados por códigos REPARABLES
//       (v1.3.0: worker_down, clip_check_failed, no_worker_base,
//       mcp_error, worker_error, submissions_unavailable) dentro de
//       la ventana de 30 min, una vez por alarma;
//   (b) retoma la COLA EN NUBE: filas pending en D1 (publicadas,
//       en ventana) que no tienen registro local enviado: si Chrome
//       estuvo cerrado, la cola en D1 es lo que sobrevive.
chrome.alarms.create("avc-cr-retry", { periodInMinutes: 5 });
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== "avc-cr-retry") return;
  try {
    const state = await loadState();
    state.workers = state.workers || {};
    for (const tenant of ["seba", "kaez"]) {
      const { base } = await getWorkerBase({ tenant, savedBase: null });
      if (!base) continue;
      const health = await checkHealth(base);
      state.workers[tenant] = { tenant, base, running: health.running, checkedAt: new Date().toISOString(), error: health.error || null };
      if (tenant === state.worker.tenant) state.worker = { ...state.workers[tenant] };
    }
    state.lastCheck = new Date().toISOString();
    await saveState(state);
    await refreshAutoSetup(state);
    // (a) Reintento de envíos bloqueados por códigos reparables.
    const pending = (state.submissions || []).filter((r) => isRetryableBlocked(r, Date.now()));
    for (const record of pending) {
      const fresh = await loadState();
      // Quitar el bloqueado viejo antes de reintentar: si vuelve a
      // bloquearse, doSubmit añade uno nuevo; si pasa, añade el enviado.
      fresh.submissions = fresh.submissions.filter((s) => s.key !== record.key || s.status !== "blocked");
      await saveState(fresh);
      await doSubmit({
        clipId: record.clipId, platform: record.platform, postUrl: record.postUrl,
        campaignId: record.campaignId, campaignName: record.campaignName,
        publishedAtMs: record.publishedAtMs, confirmed: true, auto: true,
        tenant: record.tenant || "seba",
      });
    }
    // (b) Cola en nube: pendientes en D1 sin registro local.
    for (const tenant of ["seba", "kaez"]) {
      const base = state.workers[tenant]?.base;
      const panelToken = await getPanelToken(tenant);
      const apiKey = await getApiKey(tenant);
      if (!base || !panelToken || !apiKey) continue;
      const { available, rows } = await listPendingSubmissions(base, panelToken);
      if (!available) continue;
      const local = (await loadState()).submissions || [];
      for (const row of rows) {
        if (!row || !row.post_url || !row.published_at_ms) continue;
        if (!isWithinWindow(row.published_at_ms, Date.now())) continue;
        const key = submissionKey({ tenant, postUrl: row.post_url, campaignId: row.campaign_id });
        if (local.some((s) => s.key === key && (s.status === "submitted" || s.status === "blocked" || s.status === "failed"))) continue;
        await doSubmit({
          clipId: row.clip_id, platform: row.platform, postUrl: row.post_url,
          campaignId: row.campaign_id, campaignName: row.campaign_name || row.campaign_id,
          publishedAtMs: row.published_at_ms, confirmed: true, auto: true,
          tenant, clipStreamer: row.streamer || null,
        });
      }
    }
  } catch {
    // La alarma nunca rompe nada: el próximo chequeo lo reintenta.
  }
});

chrome.runtime.onInstalled.addListener(async () => {
  const state = await loadState();
  state.lastCheck = new Date().toISOString();
  await saveState(state);
  await refreshAutoSetup(state);
});
