// popup.js — solo UI. No pide contraseñas, no hace clicks en páginas.
// Todo envío pasa por el service worker (background.js) y por el MCP oficial.
// La puerta del Worker (salud + clip en D1) se consulta en background.js.
import { KNOWN_CAMPAIGNS, getCampaignBrief, netRatePer1k, rankCampaigns, formatUsd } from "./lib.js";

const $ = (id) => document.getElementById(id);

// Selector ordenado por el ranking dinámico (tarifa NETA x viralidad,
// solo streamers famosos — regla 2026-11-23 18:50), no por una
// campaña fija: la primera opción es la mejor del día (23 nov 2026).
// Las no famosas salen AL FINAL marcadas (Bobby Van Riper: 734
// seguidores) y nunca se auto-asignan. La tarifa que se muestra es
// la NETA (bruta x 0,9, comisión del 10%).
const campaignSelect = $("campaignId");
for (const c of rankCampaigns(KNOWN_CAMPAIGNS, { today: new Date().toISOString().slice(0, 10), includeUnfamous: true })) {
  const opt = document.createElement("option");
  opt.value = c.id;
  const net = netRatePer1k(c).toFixed(2);
  opt.textContent = c.excludedReason === "not_famous"
    ? `${c.name} — $${net}/1k NETO · NO FAMOSA (${c.followers} seguidores) — excluida del auto-envío`
    : `${c.name} — $${net}/1k NETO · ${c.creators} creadores · quedan $${c.budgetRemaining} · score ${c.score.toFixed(2)}`;
  campaignSelect.appendChild(opt);
}

function showBrief(campaignId) {
  const el = $("brief");
  if (!el) return;
  const brief = getCampaignBrief(campaignId);
  if (brief.incomplete && !brief.disclosure && !brief.allowedMaterial) {
    el.textContent = "Sin ficha de brief: leer el brief entero en Discover antes de enviar. Ninguna campaña entrega clips listos: hay que cortar tu propio edit del stream/VOD permitido.";
    return;
  }
  const parts = [];
  if (brief.disclosure) parts.push(`Disclosure: ${brief.disclosure}`);
  if (brief.hashtags && brief.hashtags.length) parts.push(`Hashtags: ${brief.hashtags.join(" ")}`);
  if (brief.mentions && brief.mentions.length) parts.push(`Menciones: ${brief.mentions.join(" ")}`);
  if (brief.allowedMaterial) parts.push(`Material permitido: ${brief.allowedMaterial}`);
  if (brief.materialNote) parts.push(brief.materialNote);
  if (brief.expiresAt) parts.push(`Caduca: ${brief.expiresAt}`);
  if (brief.incomplete) parts.push("Brief INCOMPLETO: leerlo entero en Discover el día del envío.");
  el.textContent = parts.join(" ");
}

campaignSelect.addEventListener("change", () => showBrief(campaignSelect.value));
showBrief(campaignSelect.value);

function send(msg) {
  return new Promise((resolve) => chrome.runtime.sendMessage(msg, resolve));
}

function workerLine(w) {
  if (!w || !w.checkedAt) return "Worker: sin consultar todavía.";
  if (w.running === true) return `Worker (${w.tenant}): CORRIENDO — base ${w.base || "?"}`;
  if (w.running === false) return `Worker (${w.tenant}): PARADO — avisar. Envío en pausa, reintento cada 5 min. ${w.error || ""}`;
  return `Worker (${w.tenant}): sin respuesta. ${w.error || ""}`;
}

function workersLine(workers) {
  if (!workers) return "seba / kaez: sin consultar todavía.";
  const one = (t) => {
    const w = workers[t];
    if (!w || w.running === null || w.running === undefined) return `${t}: sin consultar`;
    return `${t}: ${w.running === true ? "CORRIENDO" : "PARADO — avisar"}`;
  };
  return `${one("seba")} · ${one("kaez")} — ambos activos por defecto.`;
}

function clipLine(c) {
  if (!c || !c.checkedAt) return "Clip en D1: sin consultar todavía.";
  if (c.error) return `Clip ${c.clipId || "?"} en D1: no consultado — ${c.error}`;
  // v1.3.0, puerta corregida: 'publicado en la red' NO es 'enviado a
  // Content Rewards'. El duplicado lo decide la fila en
  // campaign_submissions (vía Worker), no publish_breakdown posted.
  if (c.platformState === "posted") return `Clip ${c.clipId} en D1: publicado en ${c.platform} (eso NO es un envío: el duplicado lo decide campaign_submissions).`;
  const subs = c.submissions
    ? (c.submissions.available
      ? `Chequeo de submissions en D1: disponible (${c.submissions.rows?.length ?? 0} filas para este clip/plataforma).`
      : `Chequeo de submissions en D1: ruta aún no desplegada en el Worker — fallback a registro local.`)
    : "Chequeo de submissions en D1: sin datos.";
  return `Clip ${c.clipId} en D1: ${c.clipStatus || "?"} · ${c.platform}: ${c.platformState || "?"} · retención ${c.retentionState || "?"}. ${subs}`;
}

async function refresh() {
  const { state } = await send({ type: "AVC_GET_STATE" });
  const subs = (state && state.submissions) || [];
  const connected = "Sin cuentas verificadas desde la extensión — ver en contentrewards.com/c/settings";
  $("status").textContent = `Cuentas en Content Rewards: ${connected}. Envíos registrados: ${subs.length}.`;
  const last = subs[subs.length - 1];
  $("last").textContent = last
    ? `Último envío: clip ${last.clipId} (${last.platform}) → ${last.campaignName} — ${last.status}${last.blockedBy ? ` (${last.blockedBy})` : ""}`
    : "Último envío: ninguno todavía.";
  const history = $("history");
  history.innerHTML = "";
  for (const s of subs.slice(-8).reverse()) {
    const li = document.createElement("li");
    li.textContent = `Clip ${s.clipId} · ${s.tenant || "?"} · ${s.platform} · ${s.campaignName} · ${s.status}${s.blockedBy ? ` · ${s.blockedBy}` : ""}${s.submissionId ? ` · submission ${s.submissionId}` : ""}`;
    history.appendChild(li);
  }
  if (state && state.workers) {
    const el = $("workersStatus");
    if (el) el.textContent = workersLine(state.workers);
  }
  if (state && $("autoSetupStatus")) {
    const a = state.autoSetup;
    $("autoSetupStatus").textContent = a && a.version
      ? `Auto-configuración web v${a.version} cargada (${a.campaignCount ?? "?"} campañas, ${new Date(a.fetchedAt).toLocaleString("es-CL")}) — si la web no responde se usan los valores del ZIP.`
      : "Auto-configuración web: sin cargar todavía (se descarga sola al instalar; si falla se usan los valores del ZIP).";
  }
  if (state && state.worker) {
    if ($("tenant").value !== state.worker.tenant) $("tenant").value = state.worker.tenant;
    $("workerStatus").textContent = workerLine(state.worker);
    $("workerCheckedAt").textContent = state.worker.checkedAt
      ? `Última consulta al worker: ${new Date(state.worker.checkedAt).toLocaleString("es-CL")}`
      : "—";
  }
  if (state) $("clipStatus").textContent = clipLine(state.clipCheck);
  // Estado de claves POR TENANT (v1.3.0): nada se comparte entre
  // seba y kaez. Las claves viejas sin sufijo migraron a seba en
  // background.js; aquí solo se leen para mostrar el estado.
  const keys = await chrome.storage.local.get(["apiKey_seba", "apiKey_kaez", "panelToken_seba", "panelToken_kaez", "apiKey", "panelToken"]);
  const activeTenant = ($("tenant").value || "seba").trim();
  const apiKey = keys[`apiKey_${activeTenant}`] || (activeTenant === "seba" ? keys.apiKey : null);
  const panelToken = keys[`panelToken_${activeTenant}`] || (activeTenant === "seba" ? keys.panelToken : null);
  const keyState = (t) => `API key ${t}: ${keys[`apiKey_${t}`] || (t === "seba" && keys.apiKey) ? "guardada" : "FALTA"} · token panel ${t}: ${keys[`panelToken_${t}`] || (t === "seba" && keys.panelToken) ? "guardado" : "FALTA"}`;
  const keysEl = $("keysStatus");
  if (keysEl) keysEl.textContent = `Tenant activo: ${activeTenant}. ${keyState("seba")} — ${keyState("kaez")}. Sin API key de kaez sus envíos quedan en espera (needs_api_key); nadie inventa su cuenta.`;
  $("apiKey").placeholder = apiKey ? `API key de ${activeTenant} guardada en este Chrome` : `API key de ${activeTenant}: pégala aquí`;
  $("panelToken").placeholder = panelToken ? `Token del panel de ${activeTenant} guardado en este Chrome` : `Token del panel de ${activeTenant}: pégalo aquí`;
}

$("connect").addEventListener("click", () => {
  // OAuth oficial, en pestaña nueva. El usuario autoriza con su sesión.
  chrome.tabs.create({ url: "https://contentrewards.com/c/settings" });
});

$("checkWorker").addEventListener("click", async () => {
  const res = await send({
    type: "AVC_CHECK_WORKER",
    tenant: $("tenant").value,
    clipId: $("clipId").value.trim(),
    platform: $("platform").value,
  });
  if (res && res.worker) {
    $("workerStatus").textContent = workerLine(res.worker);
    if (res.clipCheck) $("clipStatus").textContent = clipLine(res.clipCheck);
  }
  if (res && !res.ok && res.reason) $("workerStatus").textContent += ` — ${res.reason}`;
  await refresh();
});

$("tenant").addEventListener("change", async () => {
  await send({ type: "AVC_CHECK_WORKER", tenant: $("tenant").value });
  await refresh();
});

$("submit").addEventListener("click", async () => {
  const ageMin = Number($("ageMin").value || 0);
  const result = await send({
    type: "AVC_SUBMIT_CLIP",
    clipId: $("clipId").value.trim(),
    platform: $("platform").value,
    postUrl: $("postUrl").value.trim(),
    campaignId: $("campaignId").value,
    campaignName: $("campaignId").selectedOptions[0]?.textContent || "",
    publishedAtMs: Date.now() - ageMin * 60000,
    confirmed: $("confirm").checked,
  });
  $("result").textContent = result.ok
    ? "Enviado a Content Rewards. Revisa el estado en la campaña."
    : `No se envió: ${result.reason || "error desconocido"}`;
  await refresh();
});

$("saveKey").addEventListener("click", async () => {
  const apiKey = $("apiKey").value.trim();
  if (!apiKey) return;
  // v1.3.0: la API key se guarda PARA EL TENANT ACTIVO (seba/kaez),
  // nunca compartida. La clave vieja sin sufijo era de seba.
  const tenant = ($("tenant").value || "seba").trim();
  await chrome.storage.local.set({ [`apiKey_${tenant}`]: apiKey });
  await chrome.storage.local.get("avc_cr_v1").then(async ({ avc_cr_v1: state }) => {
    if (state) { state.apiKeySet = true; await chrome.storage.local.set({ avc_cr_v1: state }); }
  });
  $("apiKey").value = "";
  await refresh();
});

$("clearKey").addEventListener("click", async () => {
  const tenant = ($("tenant").value || "seba").trim();
  await chrome.storage.local.remove(`apiKey_${tenant}`);
  await refresh();
});

$("savePanelToken").addEventListener("click", async () => {
  const panelToken = $("panelToken").value.trim();
  if (!panelToken) return;
  // v1.3.0: el token del panel se guarda PARA EL TENANT ACTIVO.
  const tenant = ($("tenant").value || "seba").trim();
  await chrome.storage.local.set({ [`panelToken_${tenant}`]: panelToken });
  $("panelToken").value = "";
  await refresh();
});

$("clearPanelToken").addEventListener("click", async () => {
  const tenant = ($("tenant").value || "seba").trim();
  await chrome.storage.local.remove(`panelToken_${tenant}`);
  await refresh();
});

function analyticsLines(label, s) {
  if (!s || !s.available) return [`${label}: sin datos todavía.`];
  const lines = [
    `${label}: ganado total ${formatUsd(s.totalEarnedUsd)} · sin retirar: no disponible por API, ver en la web · retirado: no disponible por API, ver en la web`,
    `${label}: vistas aprobadas ${s.approvedViews.toLocaleString("es-CL")} · enviados ${s.sent} · con vistas ${s.withViews} · pendientes ${s.pending} · fallidos ${s.failed} · caducados ${s.expired} · filas D1: ${s.rows}`,
  ];
  for (const c of s.byCampaign) {
    lines.push(`${label} · ${c.campaignName} (${c.marketplace}): enviados ${c.sent} · pendientes ${c.pending} · fallidos ${c.failed} · vistas ${c.views.toLocaleString("es-CL")} · ${formatUsd(c.amountUsd)}`);
  }
  return lines;
}

async function refreshAnalytics() {
  const statusEl = $("analyticsStatus");
  const box = $("analytics");
  if (!statusEl || !box) return;
  statusEl.textContent = "Analíticas: consultando D1…";
  const res = await send({ type: "AVC_GET_ANALYTICS" });
  const a = res && res.analytics;
  box.innerHTML = "";
  if (!a || !a.available) {
    statusEl.textContent = `Analíticas: sin datos todavía — ${(a && a.reason) || (res && res.reason) || "la tabla campaign_submissions (D1) no respondió"}. No son ceros reales.`;
    if (res && res.errors) {
      for (const t of ["seba", "kaez"]) {
        if (res.errors[t]) {
          const p = document.createElement("p");
          p.className = "note";
          p.textContent = `${t}: ${res.errors[t]}`;
          box.appendChild(p);
        }
      }
    }
    return;
  }
  statusEl.textContent = `Analíticas actualizadas: ${res.fetchedAt ? new Date(res.fetchedAt).toLocaleString("es-CL") : ""}. ${a.total.balanceNote || ""}`;
  const lines = [
    ...analyticsLines("TOTAL", a.total),
    ...analyticsLines("seba", a.tenants.seba),
    ...analyticsLines("kaez", a.tenants.kaez),
  ];
  for (const line of lines) {
    const p = document.createElement("p");
    p.className = "note";
    p.textContent = line;
    box.appendChild(p);
  }
  const note = document.createElement("p");
  note.className = "note";
  note.textContent = a.total.acceptedNote || "";
  box.appendChild(note);
}

$("refreshAnalytics").addEventListener("click", refreshAnalytics);


refresh();
