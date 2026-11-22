// AVC Panel — front estatico que habla con el Worker del tenant del usuario.
// Ruteo por usuario (un Worker por usuario, decisión 2026-11-19):
// workers.json (junto a este archivo) mapea usuario de login -> URL base
// del Worker de su tenant. Sin workers.json no hay API: se falla fuerte y
// nunca se vuelve en silencio al Worker único viejo.
// La base del Worker se guarda junto al token de sesión (localStorage)
// para que al recargar el panel siga pegando al mismo Worker.
let API = "";
let WORKERS_MAP = null; // null = workers.json aún no cargado o falló
let WORKERS_ERROR = "";
const WORKER_BASE_KEY = "avc-worker-base";
const getWorkerBase = () => { try { return localStorage.getItem(WORKER_BASE_KEY) || ""; } catch { return ""; } };
const setWorkerBase = (u) => { try { if (u) localStorage.setItem(WORKER_BASE_KEY, u); else localStorage.removeItem(WORKER_BASE_KEY); } catch {} };
// Base efectiva: lanza si aún no hay Worker seleccionado (sin sesión o
// sin login), para no disparar nunca un fetch contra una URL vacía.
const apiBase = () => {
  if (!API) throw new Error("Sin Worker seleccionado: entra con tu usuario.");
  return API;
};
// Usuario canónico en workers.json: exacto primero, si no insensible a
// mayúsculas (el Worker compara el username exacto). null = desconocido.
function resolveWorkerUser(username) {
  if (!WORKERS_MAP) return null;
  if (Object.prototype.hasOwnProperty.call(WORKERS_MAP, username)) return username;
  const lower = username.toLowerCase();
  return Object.keys(WORKERS_MAP).find((k) => k.toLowerCase() === lower) || null;
}
async function loadWorkersMap() {
  try {
    const res = await fetch("workers.json", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const map = await res.json();
    if (!map || typeof map !== "object" || Array.isArray(map) || !Object.keys(map).length) throw new Error("mapa vacío o inválido");
    WORKERS_MAP = map;
    // Restaurar la base guardada en el login anterior, solo si sigue
    // en el mapa (un tenant retirado olvida su base vieja).
    const saved = getWorkerBase();
    if (saved && Object.values(map).includes(saved)) API = saved;
    else if (saved) setWorkerBase("");
    return true;
  } catch (e) {
    WORKERS_MAP = null;
    WORKERS_ERROR = "No se pudo cargar la lista de Workers (workers.json)" + (e && e.message ? ": " + e.message : "") + ". El panel no puede saber a qué Worker conectar.";
    return false;
  }
}
const $ = (id) => document.getElementById(id);
let me = null;
let selectedClipId = null;
let selectedClipTitle = "";
let selectedClipPlatforms = [];
let currentBlocked = [];
let workerKnown = false;
let workerRunning = false;
let workerBusy = false;
let uploadXhr = null;
let lastSettings = {};
let settingsDirty = false;
let settingsJsonTouched = false;
// Marca de agua: el logo activo no es un campo del formulario; vive
// aquí (lo pone "Usar esta" en la lista de logos) y formToSettings
// lo mete en el PUT. null = sin logo activo.
let watermarkActiveId = null;
let currentTab = "worker";
let workerPollTimer = null;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (ms) => {
  if (!ms) return "—";
  try { return new Date(Number(ms)).toLocaleString("es-CL", { timeZone: "America/Santiago" }); } catch { return String(ms); }
};
const parsePlatforms = (p) => {
  if (Array.isArray(p)) return p;
  try { const v = JSON.parse(p || "[]"); return Array.isArray(v) ? v : []; } catch { return []; }
};
const reducedMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const isNetworkError = (e) => /failed to fetch|networkerror|network error|load failed|offline/i.test(e && e.message ? e.message : "");

let msgTimer = null;
function showMsg(text, kind) {
  const el = $("msg");
  if (msgTimer) { clearTimeout(msgTimer); msgTimer = null; }
  if (!text) { el.innerHTML = ""; el.removeAttribute("role"); return; }
  const k = kind || "info";
  const role = k === "err" ? "alert" : "status";
  const cls = k === "err" ? "err" : (k === "ok" ? "ok" : "info");
  el.setAttribute("role", role);
  el.innerHTML = `<div class="msg ${cls}"><div class="msg-row"><span>${esc(text)}</span>${k === "err" ? `<button type="button" class="msg-close" id="msgClose" aria-label="Cerrar mensaje">×</button>` : ""}</div></div>`;
  const close = $("msgClose");
  if (close) close.addEventListener("click", () => showMsg("", "info"));
  if (k !== "err") msgTimer = setTimeout(() => { el.innerHTML = ""; }, 5000);
}
function setFieldError(id, text) {
  const el = $(id);
  if (!el) return;
  if (text) { el.textContent = text; el.hidden = false; } else { el.textContent = ""; el.hidden = true; }
}
function setLoading(btn, loading, loadingText) {
  if (!btn) return;
  if (loading) {
    if (!btn.dataset.label) btn.dataset.label = btn.textContent.trim();
    btn.disabled = true;
    btn.textContent = loadingText;
  } else {
    btn.disabled = false;
    if (btn.dataset.label) btn.textContent = btn.dataset.label;
    delete btn.dataset.label;
  }
}
function skeletons(n, lines) {
  let out = "";
  for (let i = 0; i < n; i++) out += `<div class="skeleton" style="height:${lines || 56}px" aria-hidden="true"></div>`;
  return out;
}

// Sesión por token (Brave iOS bloquea la cookie cross-site): el Worker devuelve
// el token en el JSON del login y acepta Authorization: Bearer <token>.
const SESSION_KEY = "avc-session";
const getSessionToken = () => { try { return localStorage.getItem(SESSION_KEY) || ""; } catch { return ""; } };
const setSessionToken = (t) => { try { if (t) localStorage.setItem(SESSION_KEY, t); else localStorage.removeItem(SESSION_KEY); } catch {} };
const authHeaders = () => { const t = getSessionToken(); return t ? { Authorization: "Bearer " + t } : {}; };

async function api(path, opts) {
  const res = await fetch(apiBase() + path, {
    credentials: "include",
    ...(opts || {}),
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(opts && opts.headers ? opts.headers : {}) },
  });
  const text = await res.text();
  let data = null;
  try { data = JSON.parse(text); } catch { data = { ok: false, error: text || ("HTTP " + res.status) }; }
  if (res.status === 401) { showLogin(); throw new Error("Sesión caducada, entra de nuevo."); }
  if (!res.ok || data.ok === false) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}
async function apiRaw(path, opts) {
  const res = await fetch(apiBase() + path, { credentials: "include", ...(opts || {}), headers: { ...authHeaders(), ...(opts && opts.headers ? opts.headers : {}) } });
  if (res.status === 401) { showLogin(); throw new Error("Sesión caducada, entra de nuevo."); }
  if (!res.ok) {
    let m = "HTTP " + res.status;
    try { const d = await res.clone().json(); if (d.error) m = d.error; } catch {}
    throw new Error(m);
  }
  return res;
}

function showLoginUI() {
  me = null;
  $("app").classList.add("hidden");
  $("loginBox").classList.remove("hidden");
  $("logoutBtn").classList.add("hidden");
  $("workerLight").classList.add("hidden");
  $("whoLine").textContent = "sin sesión";
  const pw = $("password");
  if (pw) { pw.type = "password"; }
  const t = $("passwordToggle");
  if (t) { t.textContent = "Mostrar"; t.setAttribute("aria-pressed", "false"); }
  $("healthLine").textContent = "Sin sesión: entra con tu usuario.";
}
// Sin sesión no hay Worker: se olvidan token y base juntos; el próximo
// login vuelve a resolver la base por el usuario escrito.
function showLogin() {
  setSessionToken("");
  setWorkerBase("");
  API = "";
  showLoginUI();
}
function showApp() {
  $("loginBox").classList.add("hidden");
  $("app").classList.remove("hidden");
  $("logoutBtn").classList.remove("hidden");
  $("workerLight").classList.remove("hidden");
  $("whoLine").textContent = me.sub;
}

function setWorkerLight(state, label) {
  const btn = $("workerLight");
  const txt = $("workerLightText");
  const dot = btn.querySelector(".dot");
  dot.className = "dot" + (state === "ok" ? " ok" : state === "warn" ? " warn" : state === "bad" ? " bad" : "");
  txt.textContent = label;
  btn.setAttribute("aria-label", "Worker: " + label.toLowerCase() + ". Ir a la pestaña Worker");
}
async function checkHealth() {
  try {
    const h = await api("/api/health");
    const running = typeof h.worker_running === "boolean" ? h.worker_running : (h.worker_running ? Object.values(h.worker_running).some(Boolean) : false);
    $("healthLine").innerHTML = `API <span class="ok">conectada</span> · Worker: ${running ? "en marcha" : "parado"}`;
    setWorkerLight(running ? "ok" : "warn", running ? "En marcha" : "Parado");
  } catch (e) {
    $("healthLine").innerHTML = `<span class="bad">API no responde</span>`;
    setWorkerLight("bad", "API caída");
  }
}

function clearLoginErrors() {
  setFieldError("username-error", "");
  setFieldError("password-error", "");
  $("username").removeAttribute("aria-invalid");
  $("password").removeAttribute("aria-invalid");
  showLoginMsg("");
}
function showLoginMsg(text) {
  $("loginMsg").innerHTML = text ? `<div class="msg err">${esc(text)}</div>` : "";
}
async function doLogin(ev) {
  if (ev) ev.preventDefault();
  const btn = $("loginBtn");
  if (btn.disabled) return;
  clearLoginErrors();
  $("loginHint").textContent = "";
  const username = $("username").value.trim();
  const password = $("password").value;
  let firstBad = null;
  if (!username) { setFieldError("username-error", "Escribe tu usuario."); $("username").setAttribute("aria-invalid", "true"); firstBad = firstBad || $("username"); }
  if (!password) { setFieldError("password-error", "Escribe tu contraseña."); $("password").setAttribute("aria-invalid", "true"); firstBad = firstBad || $("password"); }
  if (firstBad) { firstBad.focus(); return; }
  // Resolver el Worker del usuario ANTES de enviar ninguna petición:
  // el login va al Worker de su tenant, nunca al Worker único viejo.
  if (!WORKERS_MAP) {
    showLoginMsg("No se pudo cargar la lista de Workers (workers.json). Recarga la página y vuelve a intentarlo.");
    return;
  }
  const canonicalUser = resolveWorkerUser(username);
  if (!canonicalUser) {
    showLoginMsg("Usuario desconocido: «" + username + "» no tiene Worker asignado. Revisa el usuario o pide que te creen uno.");
    $("username").focus();
    return;
  }
  API = WORKERS_MAP[canonicalUser];
  setWorkerBase(API);
  setLoading(btn, true, "Entrando…");
  let loginOk = false;
  try {
    const loginRes = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username: canonicalUser, password }) });
    loginOk = true;
    if (loginRes && loginRes.token) setSessionToken(loginRes.token);
    me = await api("/api/auth/me");
    showApp();
    await loadTab("worker");
    await loadNotifications();
    await maybeOpenOnboarding();
  } catch (e) {
    if (isNetworkError(e)) {
      showLoginMsg("No se pudo conectar con el panel. Comprueba tu conexión y vuelve a intentarlo.");
    } else if (loginOk) {
      showLoginMsg("La sesión no se pudo guardar en este navegador. Prueba con Shields desactivados o en Safari/Chrome.");
    } else {
      showLoginMsg("Usuario o contraseña incorrectos. Revisa ambos y vuelve a intentarlo.");
      $("loginHint").textContent = "Si acabas de crear el panel y aún no tienes usuario, pide que te creen uno.";
    }
    $("password").value = "";
    $("username").focus();
  } finally {
    setLoading(btn, false);
  }
}

// --- Tabs con hash ---
const TAB_NAMES = ["worker", "onboarding", "cola", "subidos", "cuentas", "editor", "canales", "tracking", "analiticas", "usuarios", "influencers", "fuentes", "avisos", "ajustes"];
function switchTab(name, fromHash) {
  if (!TAB_NAMES.includes(name)) name = "worker";
  if (name !== "ajustes" && currentTab === "ajustes" && settingsDirty) {
    if (!window.confirm("¿Salir sin guardar los ajustes?")) {
      document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === currentTab));
      return;
    }
    settingsDirty = false;
    $("settingsDirty").hidden = true;
  }
  currentTab = name;
  document.querySelectorAll("#tabs button").forEach((b) => {
    const on = b.dataset.tab === name;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  TAB_NAMES.forEach((t) => $("tab-" + t).classList.toggle("hidden", t !== name));
  if (!fromHash && window.location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  loadTab(name);
}
async function loadTab(name) {
  try {
    if (name !== "editor") stopEditorVideo();
    if (name === "worker") { await loadWorker(); await checkOnboardingBanner(); }
    if (name === "onboarding") await loadOnboarding();
    if (name === "cola") await loadClips();
    if (name === "subidos") await loadPublished();
    if (name === "cuentas") { switchCuentasSubtab("apis", false); await loadSocialAccounts(); await loadAccounts(); }
    if (name === "editor") await loadEditor();
    if (name === "canales") await loadChannels();
    if (name === "tracking") await loadTracking();
    if (name === "analiticas") await loadAnaliticas();
    if (name === "usuarios") await loadPanelUsers();
    if (name === "influencers") await loadInfluencers();
    if (name === "fuentes") await loadFuentes();
    if (name === "avisos") await loadNotifications();
    if (name === "ajustes") { await loadSettings(); refreshCfgWorker(); }
  } catch (e) { showMsg(e.message, "err"); }
}
window.addEventListener("hashchange", () => {
  const name = window.location.hash.replace("#", "");
  if (TAB_NAMES.includes(name) && name !== currentTab) switchTab(name, true);
});

// --- Worker ---
function workerButtonsDisabled(start, stop, runOnce, refresh) {
  $("btnStart").disabled = start;
  $("btnStop").disabled = stop;
  $("btnRunOnce").disabled = runOnce;
  $("btnRefreshW").disabled = refresh;
}
function renderWorkerState(state, startedAt, note) {
  const dot = $("wDot");
  if (state === "running") {
    dot.className = "dot lg ok";
    $("wRunning").innerHTML = '<span class="pill ok">EN MARCHA</span>';
    $("wStarted").textContent = startedAt ? "desde " + fmtDate(startedAt) : "";
    workerButtonsDisabled(true, false, false, false);
    setWorkerLight("ok", "En marcha");
  } else if (state === "stopped") {
    dot.className = "dot lg warn";
    $("wRunning").innerHTML = '<span class="pill warn">PARADO</span>';
    $("wStarted").textContent = "";
    workerButtonsDisabled(false, true, false, false);
    setWorkerLight("warn", "Parado");
  } else {
    dot.className = "dot lg bad";
    $("wRunning").innerHTML = '<span class="pill bad">SIN CONEXIÓN</span>';
    $("wStarted").textContent = "";
    workerButtonsDisabled(true, true, true, false);
    setWorkerLight("bad", "API caída");
  }
  $("wNote").textContent = note || "";
}
async function loadWorker() {
  const list = $("wActivity");
  if (!workerKnown) {
    $("wRunning").textContent = "Comprobando…";
    list.setAttribute("aria-busy", "true");
    list.innerHTML = skeletons(3, 44);
    workerButtonsDisabled(true, true, true, true);
  }
  try {
    const s = await api("/api/worker/state");
    workerKnown = true;
    workerRunning = !!s.running;
    renderWorkerState(s.running ? "running" : "stopped", s.startedAt, s.lastNote);
    // Validador de suministro (2026-11-18): "en marcha" no basta.
    // Si está atascado o bloqueado, el piloto pasa a ATASCADO y
    // la nota muestra el motivo exacto (horas sin clip, dispatch).
    // Sin directos ES (2026-11-19) el piloto pasa a EN ESPERA
    // (ámbar): no hay material en vivo, no es un atasco.
    if (s.validation) {
      const v = s.validation;
      $("wNote").textContent = (s.lastNote ? s.lastNote + " — " : "") + "Validador: " + (v.reason || "");
      if (v.status === "stuck" || v.status === "blocked") {
        $("wDot").className = "dot lg bad";
        $("wRunning").innerHTML = '<span class="pill bad">ATASCADO</span>';
        setWorkerLight("bad", "Atascado: sin clips");
      } else if (v.status === "no_live_es") {
        $("wDot").className = "dot lg warn";
        $("wRunning").innerHTML = '<span class="pill warn">EN ESPERA</span>';
        setWorkerLight("warn", "En espera: sin directos ES");
      } else if (v.status === "ok" && s.phase && !s.phase.stale) {
        // En marcha y al día: el piloto dice qué está haciendo el
        // cerebro AHORA (la fase), no solo "En marcha".
        setWorkerLight("ok", s.phase.label);
      }
    }
    // Fase en vivo del cerebro (2026-11-19): qué está haciendo
    // AHORA + último barrido, último clip y próxima corrida. La
    // fase nunca tapa el veredicto: ATASCADO lo decide solo el
    // validador (arriba). Sin fase, se dice que aún no reportó;
    // con fase vieja, se dice hace cuánto (nunca se inventa una).
    const ph = s.phase;
    if (ph) {
      const age = ph.ageMin === 0 ? "ahora mismo" : `hace ${ph.ageMin} min`;
      const staleTxt = ph.stale ? " (sin señal reciente)" : "";
      $("wPhase").textContent = `${ph.label}${ph.detail ? " · " + ph.detail : ""} — ${age}${staleTxt}`;
    } else {
      $("wPhase").textContent = "Sin señal del cerebro todavía: aún no reportó qué está haciendo.";
    }
    const fmtTime = (ms) => ms ? fmtDate(ms) : "—";
    $("wTimes").textContent = `Último barrido: ${fmtTime(s.lastSweepAt)} · Último clip: ${fmtTime(s.lastClipAt)} · Próxima corrida: ${s.nextRunAt ? "~" + fmtTime(s.nextRunAt) : "—"}`;
    // Último clip (2026-11-19, visibilidad): el último evento de clip
    // (cortado / encolado / descartado / corte fallido) con su motivo
    // y hora. La actividad ya trae los eventos; si no hay ninguno,
    // se dice claro en vez de inventar uno.
    const clipEv = (s.activity || []).find((a) =>
      String(a.kind || "").startsWith("clip_") || String(a.kind || "") === "corte_fallido");
    $("wLastClip").textContent = clipEv
      ? `Último clip: ${fmtDate(clipEv.at)} — ${clipEv.message}`
      : "Último clip: todavía no hay ningún clip cortado en esta sesión.";
    // Refresco en vivo (60 s) mientras se mira esta pestaña: el
    // cerebro reporta cada pocos minutos y el estado se actualiza
    // solo, sin tener que pulsar Actualizar.
    clearTimeout(workerPollTimer);
    workerPollTimer = setTimeout(() => { if (currentTab === "worker") loadWorker(); }, 60000);
    list.setAttribute("aria-busy", "false");
    const acts = s.activity || [];
    list.innerHTML = acts.length ? acts.map((a, i) =>
      `<li><span class="dot ${i === 0 ? (s.running ? "ok" : "") : ""}" aria-hidden="true"></span><time datetime="${new Date(Number(a.at)).toISOString()}">${fmtDate(a.at)}</time><span class="tl-kind">${esc(a.kind)}</span> <span class="tl-msg">— ${esc(a.message)}</span></li>`).join("")
      : `<li class="empty-state"><b>Sin actividad todavía.</b><br><span class="dim">Cuando el Worker haga algo, aparecerá aquí con su fecha y hora. Ejecuta un ciclo o inicia el Worker para ver la primera actividad.</span></li>`;
    await loadJobs();
  } catch (e) {
    list.setAttribute("aria-busy", "false");
    renderWorkerState("offline", null, $("wNote").textContent);
    if (workerKnown) showMsg(e.message, "err");
    else showMsg(e.message, "err");
  }
}
// --- Jobs recientes (M13): lectura por sesión, solo el tenant ---
async function loadJobs() {
  const list = $("jobList");
  list.setAttribute("aria-busy", "true");
  try {
    const data = await api("/api/jobs?limit=10");
    const jobs = data.jobs || [];
    list.setAttribute("aria-busy", "false");
    list.innerHTML = jobs.length ? jobs.map((j) =>
      `<li><span class="dot" aria-hidden="true"></span><time>${fmtDate(j.created_at)}</time><span class="tl-kind">${esc(j.kind || "")}${j.platform ? " / " + esc(j.platform) : ""}</span> <span class="tl-msg">— ${esc(j.state || "")}${j.last_error ? ": " + esc(j.last_error) : ""}</span></li>`).join("")
      : `<li class="empty-state"><b>Sin jobs todavía.</b><br><span class="dim">Cuando se encole una publicación, aparecerá aquí.</span></li>`;
  } catch (e) {
    list.setAttribute("aria-busy", "false");
    list.innerHTML = `<li><b>No se pudieron cargar los jobs.</b><br><span class="dim">${esc(e.message)}</span></li>`;
  }
}
async function workerAction(path, okText, btn, loadingText) {
  if (workerBusy) return;
  workerBusy = true;
  const btns = [$("btnStart"), $("btnStop"), $("btnRunOnce"), $("btnRefreshW"), $("btnStartCfg"), $("btnStopCfg")];
  const labels = btns.map((b) => b.innerHTML);
  btns.forEach((b) => { b.disabled = true; });
  if (btn) btn.innerHTML = esc(loadingText);
  try {
    const r = await api(path, { method: "POST" });
    showMsg(okText + (r && r.processed !== undefined ? ` (${r.processed} jobs procesados, ${r.stalled || 0} atascados).` : ""), "info");
    await loadWorker();
  } catch (e) { showMsg(e.message, "err"); }
  finally {
    workerBusy = false;
    btns.forEach((b, i) => { b.innerHTML = labels[i]; });
    if (workerKnown) workerButtonsDisabled(!workerRunning, workerRunning, false, false);
    else workerButtonsDisabled(true, true, true, true);
  }
}
async function refreshWorker() {
  if (workerBusy) return;
  const btn = $("btnRefreshW");
  setLoading(btn, true, "Actualizando…");
  try { await loadWorker(); } catch (e) { showMsg(e.message, "err"); }
  finally { setLoading(btn, false); }
}

// --- Cola ---
const STATUS_MAP = {
  pending: ["Pendiente", "neutral"], draft: ["Pendiente", "neutral"], new: ["Pendiente", "neutral"],
  queued: ["En cola", "warn"], approved: ["En cola", "warn"], scheduled: ["En cola", "warn"],
  publishing: ["Publicando", "warn"], processing: ["Publicando", "warn"],
  published: ["Publicado", "ok"],
  failed: ["Fallido", "bad"], error: ["Fallido", "bad"],
  blocked: ["Frenado", "warn"],
};
const PLAT_STATE_MAP = {
  pending: ["Pendiente", "neutral"], draft: ["Pendiente", "neutral"], not_started: ["Pendiente", "neutral"],
  queued: ["En cola", "warn"], scheduled: ["En cola", "warn"],
  publishing: ["Publicando", "warn"], processing: ["Publicando", "warn"], uploading: ["Publicando", "warn"],
  published: ["Publicado", "ok"], done: ["Publicado", "ok"], success: ["Publicado", "ok"],
  failed: ["Fallido", "bad"], error: ["Fallido", "bad"],
  blocked: ["Frenado", "warn"], held: ["Frenado", "warn"], skipped: ["Frenado", "warn"],
};
const PLATFORMS_ORDER = ["youtube", "tiktok", "instagram", "facebook"];
const PLATFORM_LABEL = { youtube: "YouTube", tiktok: "TikTok", instagram: "Instagram", facebook: "Facebook" };
const PLATFORM_DOT = { youtube: "youtube", tiktok: "tiktok", instagram: "instagram", facebook: "facebook", meta: "instagram-pink", twitch: "twitch", gemini: "gemini" };
function statusPill(status) {
  const m = STATUS_MAP[String(status || "").toLowerCase()];
  if (m) return `<span class="pill ${m[1]}">${m[0]}</span>`;
  return `<span class="pill neutral">${esc(status)}</span>`;
}
// Idioma del clip (0019, DOS COLAS SEPARADAS 2026-11-18): "es" |
// "en". Sin idioma (filas viejas) es "es". La píldora ES/EN es
// visible en cada tarjeta y fila de la Cola y en el Editor.
function clipLanguage(c) { return String((c && c.language) || "es").toLowerCase() === "en" ? "en" : "es"; }
function langName(l) { return String(l).toLowerCase() === "en" ? "Inglés" : "Español"; }
function langPill(c) {
  const l = clipLanguage(c);
  return `<span class="pill ${l === "en" ? "violet" : "ok"}" title="Idioma del clip: ${langName(l)}">${l.toUpperCase()}</span>`;
}
// Bloqueo visible en la Cola (2026-11-19): un clip con
// publish_blocked=1 no se publica; la lista lo muestra con su motivo
// en vez de esconderlo en el detalle. Sin voz = necesita
// transcripción humana.
function blockedPill(c) {
  if (Number(c.publish_blocked) !== 1) return "";
  const human = Number(c.needs_human_transcription) === 1;
  return `<span class="pill bad" title="${human ? "Sin subtítulos completos: necesita transcripción humana" : "Publicar está bloqueado"}">${human ? "Bloqueado · sin voz" : "Bloqueado"}</span>`;
}
function platStatePill(raw) {
  const m = PLAT_STATE_MAP[String(raw || "").toLowerCase()];
  if (m) return `<span class="pill ${m[1]}">${m[0]}</span>`;
  return `<span class="pill neutral">${esc(raw)}</span>`;
}
function parseStates(c) {
  try { return JSON.parse(c.publish_states || "{}") || {}; } catch { return {}; }
}
function scoreClass(score) {
  if (score === null || score === undefined) return "";
  if (score < 50) return "bad";
  if (score < 90) return "warn";
  return "ok";
}
// Puntuación IA (contrato 2026-11-18): la API devuelve `viral` ya
// parseado + seo_scores/hype_score/priority/score_kind arriba.
// Clips viejos o sin datos devuelven null = "sin medir", nunca un
// número inventado. Acepta también `viral` como string (filas
// viejas) y la forma legacy viral.platforms[key].score.
function parseViral(c) {
  const v = c && c.viral;
  if (v && typeof v === "object") return v;
  if (typeof v === "string") { try { const p = JSON.parse(v); return (p && typeof p === "object") ? p : {}; } catch { return {}; } }
  return {};
}
function seoScoreFor(c, key) {
  const v = parseViral(c);
  if (v.seo_scores && typeof v.seo_scores[key] === "number") return v.seo_scores[key];
  const plats = v.platforms || {};
  const p = plats[key];
  return p && typeof p.score === "number" ? p.score : null;
}
function viralNum(c, key) {
  const v = parseViral(c);
  if (typeof v[key] === "number") return v[key];
  if (typeof c[key] === "number") return c[key];
  return null;
}
const SEO_CHIP_LABEL = { youtube_shorts: "YT", tiktok: "TT", instagram_reels: "IG", facebook_reels: "FB" };
const SEO_CHIP_KEYS = ["youtube_shorts", "tiktok", "instagram_reels", "facebook_reels"];
// Chips SIEMPRE visibles en la Cola: SEO por plataforma (4),
// viralidad, prioridad e hype. Sin dato: "sin medir".
function scoreChipsHtml(c) {
  const seo = SEO_CHIP_KEYS.map((k) => {
    const s = seoScoreFor(c, k);
    return `<span class="pill ${s === null ? "neutral" : scoreClass(s)}" title="SEO ${SEO_CHIP_LABEL[k]}">${SEO_CHIP_LABEL[k]}: ${s === null ? "sin medir" : s}</span>`;
  }).join("");
  const vir = viralNum(c, "viral_score") ?? (typeof c.score === "number" ? c.score : null);
  const pri = viralNum(c, "priority");
  const hype = viralNum(c, "hype_score");
  const kind = (parseViral(c).score_kind || c.score_kind) === "ia";
  const rescue = (parseViral(c) || {}).rescue || null;
  return `<div class="score-chips" role="group" aria-label="Puntuación IA">${seo}<span class="pill ${vir === null ? "neutral" : scoreClass(vir)}">Viralidad: ${vir === null ? "sin medir" : vir}</span><span class="pill ${pri === null ? "neutral" : ""}">Prioridad: ${pri === null ? "sin medir" : pri}</span><span class="pill neutral">Hype: ${hype === null ? "sin medir" : hype}</span>${kind ? `<span class="pill violet">IA · Rellenado por IA</span>` : ""}${rescue ? `<span class="pill sunset">Rescatado · viralidad ${rescue.viral_revisado} (bloqueado)</span>` : ""}</div>`;
}
function scoreBar(c) {
  const score = c.score;
  const pct = (score === null || score === undefined) ? 0 : Math.max(0, Math.min(100, Number(score)));
  const cls = scoreClass(score);
  const label = "Viralidad";
  const val = (score === null || score === undefined) ? "—" : String(score);
  const breakdown = breakdownData(c);
  const bdBtn = breakdown.length
    ? `<button type="button" class="ghost" data-breakdown="${c.id}" aria-expanded="false">Ver desglose</button>` : "";
  const bdRows = breakdown.length
    ? `<div class="priority-breakdown" id="bd-${c.id}" hidden>${breakdown.map((b) => `<div class="bd-row"><span>${esc(b.label)}</span><span>${esc(String(b.value))}</span></div>`).join("")}</div>` : "";
  return `<div class="priority-bar-wrap"><div class="priority-bar-label"><span class="lbl">${label}</span><span class="val">${val}</span></div><div class="priority-bar" role="img" aria-label="${label}: ${val}"><div class="priority-fill ${cls}" style="width:${pct}%"></div></div>${bdBtn}${bdRows}</div>`;
}
function breakdownData(c) {
  const out = [];
  const v = parseViral(c);
  const vir = typeof v.viral_score === "number" ? v.viral_score : (typeof c.score === "number" ? c.score : null);
  if (vir !== null) out.push({ label: "Viralidad", value: vir });
  const pri = viralNum(c, "priority");
  if (pri !== null) out.push({ label: "Prioridad", value: pri });
  const hype = viralNum(c, "hype_score");
  if (hype !== null) out.push({ label: "Hype", value: hype });
  const bd = v.viral_breakdown;
  if (bd && typeof bd === "object") {
    for (const [k, val] of Object.entries(bd)) {
      if (typeof val === "number") out.push({ label: "Desglose · " + k, value: val });
    }
  }
  SEO_CHIP_KEYS.forEach((k) => {
    const s = seoScoreFor(c, k);
    if (s !== null) out.push({ label: "SEO " + SEO_CHIP_LABEL[k], value: s });
  });
  return out;
}
function platformMatrix(c, variant) {
  const plats = parsePlatforms(c.platforms);
  const states = parseStates(c);
  const names = [...plats];
  Object.keys(states).forEach((k) => { if (!names.includes(k)) names.push(k); });
  if (!names.length) return `<p class="dim">Sin plataformas asignadas</p>`;
  const ordered = PLATFORMS_ORDER.filter((p) => names.map((n) => String(n).toLowerCase()).includes(p))
    .map((p) => names.find((n) => String(n).toLowerCase() === p));
  names.forEach((n) => { if (!ordered.includes(n)) ordered.push(n); });
  return `<div class="platform-matrix${variant ? " " + variant : ""}">${ordered.map((p) => {
    const key = String(p).toLowerCase();
    const st = states[p] || states[key];
    const raw = st ? (st.state || st) : "pending";
    const label = PLATFORM_LABEL[key] || String(p);
    return `<span class="platform-chip"><span class="dot ${PLATFORM_DOT[key] || ""}" aria-hidden="true"></span>${esc(label)} ${platStatePill(raw)}</span>`;
  }).join("")}</div>`;
}
function clipTitleOf(c) { return c.title || c.seo_title || ("Clip " + c.id); }
// Retención 24 h del MP4 en R2 (0039, orden 2026-11-21 21:28): el
// clip en "subidos" muestra dónde se subió (con link), dónde falta
// y si está esperando re-subida (takedown). Los datos vienen de
// GET /api/clips/:id (publish_breakdown + retention). Sin datos de
// publicación no se muestra nada: la matriz de plataformas ya cubre
// el estado por plataforma.
const RETENTION_PLAT_LABEL = { youtube_shorts: "YouTube", tiktok: "TikTok", instagram_reels: "Instagram", facebook_reels: "Facebook" };
function retentionHtml(data) {
  const bd = (data && data.publish_breakdown) || [];
  const ret = data && data.retention;
  const posted = bd.filter((b) => b.state === "posted");
  const pending = bd.filter((b) => b.state === "pending");
  const failed = bd.filter((b) => b.state === "failed");
  if (!posted.length && !pending.length && !failed.length && !ret) return "";
  const platName = (p) => esc(RETENTION_PLAT_LABEL[p] || p);
  const up = posted.map((b) => b.evidence_url ? `<a href="${esc(b.evidence_url)}" target="_blank" rel="noopener">${platName(b.platform)}</a>` : platName(b.platform)).join(", ");
  const miss = pending.map((b) => platName(b.platform) + (b.awaiting_reupload ? " (esperando re-subida)" : "")).join(", ");
  const fail = failed.map((b) => platName(b.platform)).join(", ");
  let line = "";
  if (ret) {
    if (ret.state === "subidos") {
      line = `<p><span class="pill ok">Subidos</span> MP4 en R2${ret.delete_after ? ` · se borra el ${fmtDate(ret.delete_after)} (24 h tras completar)` : " · sin plazo fijado todavía (se fija en el próximo ciclo)"}</p>`;
    } else if (ret.state === "borrado") {
      line = `<p><span class="pill neutral">MP4 borrado</span> La retención de 24 h venció y el archivo se borró de R2. El registro y los links se conservan.</p>`;
    } else if (ret.state === "sin_mp4") {
      line = `<p><span class="pill neutral">Sin MP4 en R2</span></p>`;
    }
  }
  if (ret && ret.awaiting_reupload) {
    line += `<p><span class="pill warn">Esperando re-subida</span> Bajaron un video y el MP4 se conserva para re-subirlo (no se borra).</p>`;
  }
  const rows = [];
  if (up) rows.push(`Subido en: <b>${up}</b>`);
  if (miss) rows.push(`Falta subirse en: <b>${miss}</b>`);
  if (fail) rows.push(`Fallido en: <b>${fail}</b>`);
  if (!line && !rows.length) return "";
  return `<div class="retention-block" aria-label="Subidos y retención del MP4">${line}${rows.length ? `<p>${rows.join("<br>")}</p>` : ""}</div>`;
}
// Píldora "Subidos" en tarjeta/fila (retención 24 h, 0039): clip
// publicado con el MP4 aún en R2, o ya borrado tras vencer.
function retentionPill(c) {
  if (String(c.status || "").toLowerCase() !== "publicado") return "";
  if (Number(c.file_deleted) === 1) return `<span class="pill neutral" title="El MP4 ya se borró de R2 (retención de 24 h vencida)">MP4 borrado</span>`;
  if (!c.video_r2_key) return "";
  return `<span class="pill ok" title="Publicado en todas sus plataformas; el MP4 se guarda 24 h en R2 antes de borrarse">Subidos · MP4 en R2</span>`;
}
function clipCardHtml(c, open) {
  const score = c.score;
  return `<article class="clip-card${open ? " open" : ""}" data-card="${c.id}">
    <div class="clip-card-top">
      <div class="clip-thumb" aria-hidden="true"><span class="thumb-empty">Sin miniatura</span><span class="score-ring ${scoreClass(score)}">${score ?? "—"}</span></div>
      <div class="clip-card-body">
        <h3>${esc(clipTitleOf(c))}</h3>
        ${c.seo_title && c.seo_title !== c.title ? `<p class="dim">${esc(c.seo_title)}</p>` : ""}
        ${c.seo_description ? `<p class="dim clip-desc">${esc(c.seo_description)}</p>` : ""}
        <div class="clip-meta">${statusPill(c.status)}${retentionPill(c)}${blockedPill(c)}${langPill(c)}${c.streamer ? `<span>${esc(c.streamer)}</span>` : ""}${c.game ? `<span class="pill ${c.game === "GTA VI" ? "ok" : "bad"}">${esc(c.game)}</span>` : `<span class="pill bad">Sin categoría</span>`}${open ? `<span class="pill ok">Abierto</span>` : ""}<span>Creado ${fmtDate(c.created_at)}</span></div>
      </div>
    </div>
    ${platformMatrix(c, "cols-2")}
    ${scoreChipsHtml(c)}
    ${scoreBar(c)}
    <div class="row mt-3"><button type="button" class="ghost btn-block" data-clip="${c.id}">Ver detalle</button><button type="button" class="ghost btn-block" data-seo-clip="${c.id}">Ver SEO y puntuación</button><button type="button" class="ghost btn-block" data-queue-preview="${c.id}">▶ Ver preview</button></div>
    <div class="queue-preview-frame" data-queue-frame="${c.id}" hidden></div>
  </article>`;
}
function clipRowHtml(c) {
  const score = c.score;
  return `<tr><td><button type="button" class="ghost clip-title-btn" data-clip="${c.id}"><b>${esc(clipTitleOf(c))}</b></button>${c.seo_title && c.seo_title !== c.title ? `<br><span class="dim">${esc(c.seo_title)}</span>` : ""}${c.seo_description ? `<br><span class="dim clip-desc">${esc(c.seo_description)}</span>` : ""}<br><span class="dim">Categoría: ${esc(c.game || "sin categoría")}</span><br>${platformMatrix(c)}${scoreChipsHtml(c)}<br><button type="button" class="ghost" data-queue-preview="${c.id}">▶ Ver preview</button><div class="queue-preview-frame" data-queue-frame="${c.id}" hidden></div></td><td>${esc(c.streamer || "—")}</td><td>${statusPill(c.status)}${retentionPill(c) ? `<br>${retentionPill(c)}` : ""}${blockedPill(c) ? `<br>${blockedPill(c)}` : ""}<br>${langPill(c)}</td><td class="num">${score ?? "—"}</td><td>${esc(parsePlatforms(c.platforms).join(", "))}</td><td>${fmtDate(c.created_at)}</td></tr>`;
}
function bindBreakdownButtons() {
  document.querySelectorAll("[data-breakdown]").forEach((b) => b.addEventListener("click", () => {
    const id = b.dataset.breakdown;
    const bd = $("bd-" + id);
    if (!bd) return;
    const open = bd.hidden;
    bd.hidden = !open;
    b.setAttribute("aria-expanded", open ? "true" : "false");
    b.textContent = open ? "Ocultar desglose" : "Ver desglose";
  }));
}
function bindClipButtons() {
  document.querySelectorAll("[data-clip]").forEach((b) => b.addEventListener("click", () => selectClip(Number(b.dataset.clip))));
  document.querySelectorAll("[data-seo-clip]").forEach((b) => b.addEventListener("click", () => openEditorSeo(Number(b.dataset.seoClip))));
  document.querySelectorAll("[data-queue-preview]").forEach((b) => b.addEventListener("click", () => toggleQueuePreview(Number(b.dataset.queuePreview), b)));
  bindBreakdownButtons();
}
// Preview con frame de plataforma en la Cola (2026-11-19, pedido
// del usuario): al pulsar "Ver preview" se despliega ahí mismo, en
// la tarjeta/fila, un marco que imita la app de cada plataforma —
// pestañas TikTok / Instagram / YouTube / Facebook, cabecera con la
// cuenta, el vídeo 9:16 reproducible y el pie con título,
// descripción, hashtags y texto de miniatura — como se va a ver
// publicado. El vídeo sigue cargándose solo al pulsar (blob URL con
// sesión, como el Editor): 11 clips no cargan 11 vídeos a la vez.
// Cada clip tiene DOS marcos (tabla + tarjeta); el botón abre el de
// su propio contenedor, nunca el global.
const queuePreviewUrls = new Map();
const queueClipsById = new Map();
const QP_SHORT = { youtube_shorts: "youtube", tiktok: "tiktok", instagram_reels: "instagram", facebook_reels: "facebook" };
// Guía de límites que se muestra en cada pestaña (reglas de
// publicación 2026-11-19 + límites de cada plataforma).
const QP_LIMIT_HINT = {
  youtube: "Título ≤60 caracteres · hashtags 3–5",
  tiktok: "Caption ≤150 caracteres · hashtags 3–5",
  instagram: "Hashtags máx 5 · caption ≤2.200 caracteres",
  facebook: "Hashtags 3–5",
};
let previewHandlesPromise = null;
function loadPreviewHandles() {
  if (!previewHandlesPromise) {
    previewHandlesPromise = api("/api/social-accounts")
      .then((data) => {
        const map = {};
        (data.accounts || []).forEach((a) => {
          const key = String(a.platform || "").toLowerCase();
          if (a.handle) map[key] = a.handle;
        });
        return map;
      })
      .catch(() => ({}));
  }
  return previewHandlesPromise;
}
function qpPlatforms(c) {
  const out = [];
  parsePlatforms(c.platforms).forEach((p) => {
    const key = String(p).toLowerCase();
    const short = QP_SHORT[key] || key;
    if (PLATFORM_LABEL[short] && !out.some((o) => o.short === short)) out.push({ key, short });
  });
  return out.length ? out : [{ key: "tiktok", short: "tiktok" }];
}
function qpFrameHtml(c) {
  const plats = qpPlatforms(c);
  const title = c.seo_title || c.title || ("Clip " + c.id);
  const desc = c.seo_description || "";
  const tags = c.seo_tags || "";
  return `<div class="qp-tabs" role="tablist" aria-label="Vista previa por plataforma">${plats.map((p, i) =>
    `<button type="button" role="tab" class="qp-tab${i === 0 ? " active" : ""}" data-qp-tab="${esc(p.short)}" data-qp-key="${esc(p.key)}" aria-selected="${i === 0}"><span class="dot ${PLATFORM_DOT[p.short] || ""}" aria-hidden="true"></span>${esc(PLATFORM_LABEL[p.short])}</button>`).join("")}</div>
  <div class="qp-phone">
    <div class="qp-head"><span class="dot ${PLATFORM_DOT[plats[0].short] || ""}" data-qp-dot aria-hidden="true"></span><div class="qp-head-text"><b data-qp-platform>${esc(PLATFORM_LABEL[plats[0].short])}</b><span data-qp-handle></span></div><span class="qp-follow">Seguir</span></div>
    <div class="qp-screen"><video class="queue-video" data-queue-video="${c.id}" controls playsinline preload="none"></video>
      <div class="qp-rail" aria-hidden="true">
        <span class="qp-rail-btn"><svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20.5s-8-4.7-9.8-9.3C.9 7.7 3 4.5 6.4 4.5c2 0 3.4 1 4.3 2.4l1.3 1.9 1.3-1.9c.9-1.4 2.3-2.4 4.3-2.4 3.4 0 5.5 3.2 4.2 6.7-1.8 4.6-9.8 9.3-9.8 9.3z"/></svg><i>Me gusta</i></span>
        <span class="qp-rail-btn"><svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-8 8H4l2-3a8 8 0 1 1 15-5z"/></svg><i>Comentar</i></span>
        <span class="qp-rail-btn"><svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12v7a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-7"/><path d="M16 6l-4-4-4 4"/><path d="M12 2v13"/></svg><i>Compartir</i></span>
      </div>
    </div>
    <div class="qp-caption"><b data-qp-cap-handle></b> <span>${esc(title)}</span>${desc ? `<br><span>${esc(desc)}</span>` : ""}${tags ? `<br><span class="qp-tags">${esc(tags)}</span>` : ""}</div>
  </div>
  <p class="qp-hint" data-qp-hint>${esc(QP_LIMIT_HINT[plats[0].short] || "")}</p>
  <p class="qp-meta" data-qp-meta></p>`;
}
function qpFrameHandles(frame) {
  try { return JSON.parse(frame.dataset.handles || "{}"); } catch { return {}; }
}
function qpMetaText(c, short, key, durationSec) {
  const score = seoScoreFor(c, key);
  const title = c.seo_title || c.title || "";
  const desc = c.seo_description || "";
  const tags = (c.seo_tags || "").split(/[\s,]+/).filter(Boolean).length;
  const subs = c.subtitles_complete === 1 ? "Subtítulos quemados en el vídeo"
    : (c.needs_human_transcription === 1 || c.publish_blocked === 1 ? "Sin subtítulos: necesita transcripción humana" : "Subtítulos: sin confirmar");
  const parts = [];
  if (durationSec) parts.push("Duración: " + durationSec + " s");
  parts.push("SEO " + PLATFORM_LABEL[short] + ": " + (score === null ? "sin medir" : score));
  parts.push("Título " + title.length + " car.");
  parts.push("Descripción " + desc.length + " car.");
  parts.push("Tags: " + tags);
  parts.push(subs);
  if (c.thumb_text) parts.push("Miniatura: «" + c.thumb_text + "»");
  // El panel guarda UN solo juego de título/descripción/tags por
  // clip: lo que cambia por plataforma es el marco, el handle y el
  // SEO, no los textos. Se dice tal cual, sin inventar versiones.
  if (qpPlatforms(c).length > 1) parts.push("Título y descripción iguales en todas las plataformas (un solo juego por clip)");
  return parts.join(" · ");
}
function qpRenderTab(frame) {
  const id = Number(frame.dataset.queueFrame);
  const c = queueClipsById.get(id);
  if (!c) return;
  const tab = frame.querySelector(".qp-tab.active") || frame.querySelector("[data-qp-tab]");
  if (!tab) return;
  const short = tab.dataset.qpTab;
  const key = tab.dataset.qpKey;
  const handles = qpFrameHandles(frame);
  const handle = handles[short] || "";
  const dot = frame.querySelector("[data-qp-dot]");
  if (dot) dot.className = "dot " + (PLATFORM_DOT[short] || "");
  const platformEl = frame.querySelector("[data-qp-platform]");
  if (platformEl) platformEl.textContent = PLATFORM_LABEL[short] || short;
  const handleEl = frame.querySelector("[data-qp-handle]");
  if (handleEl) handleEl.textContent = handle;
  const capHandle = frame.querySelector("[data-qp-cap-handle]");
  if (capHandle) capHandle.textContent = handle || (PLATFORM_LABEL[short] || short);
  const hint = frame.querySelector("[data-qp-hint]");
  if (hint) hint.textContent = QP_LIMIT_HINT[short] || "";
  const meta = frame.querySelector("[data-qp-meta]");
  if (meta) meta.textContent = qpMetaText(c, short, key, frame.dataset.durationSec ? Number(frame.dataset.durationSec) : null);
}
function qpSwitchTab(frame, tabBtn) {
  frame.querySelectorAll(".qp-tab").forEach((t) => {
    const active = t === tabBtn;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", active ? "true" : "false");
  });
  qpRenderTab(frame);
}
function qpCloseFrame(frame) {
  const v = frame.querySelector("[data-queue-video]");
  if (v) v.pause();
  frame.hidden = true;
  document.querySelectorAll(`[data-queue-preview="${frame.dataset.queueFrame}"]`).forEach((b) => { b.textContent = "▶ Ver preview"; });
}
async function toggleQueuePreview(id, btn) {
  const scope = btn ? btn.closest("article, tr") : null;
  const frame = (scope && scope.querySelector(`[data-queue-frame="${id}"]`))
    || document.querySelector(`[data-queue-frame="${id}"]`);
  if (!frame) return;
  if (!frame.hidden) { qpCloseFrame(frame); return; }
  // Al abrir un preview, cerrar los demás: que no suenen dos audios
  // a la vez y que su botón vuelva a "Ver preview".
  document.querySelectorAll("[data-queue-frame]:not([hidden])").forEach((f) => { if (f !== frame) qpCloseFrame(f); });
  const clip = queueClipsById.get(id);
  if (!clip) { showMsg("No se pudo cargar el clip para el preview.", "err"); return; }
  if (!frame.dataset.built) {
    frame.innerHTML = qpFrameHtml(clip);
    frame.dataset.built = "1";
    frame.querySelectorAll("[data-qp-tab]").forEach((t) => t.addEventListener("click", () => qpSwitchTab(frame, t)));
    const video = frame.querySelector("[data-queue-video]");
    if (video) video.addEventListener("loadedmetadata", () => {
      if (Number.isFinite(video.duration)) {
        frame.dataset.durationSec = String(Math.round(video.duration));
        qpRenderTab(frame);
      }
    });
    loadPreviewHandles().then((handles) => {
      frame.dataset.handles = JSON.stringify(handles);
      qpRenderTab(frame);
    });
    // Duración del clip (start/end) hasta que el vídeo cargue.
    if (typeof clip.end_sec === "number" && typeof clip.start_sec === "number" && clip.end_sec > clip.start_sec) {
      frame.dataset.durationSec = String(Math.round(clip.end_sec - clip.start_sec));
    }
    qpRenderTab(frame);
  }
  const video = frame.querySelector(`[data-queue-video="${id}"]`);
  if (!video) return;
  if (!queuePreviewUrls.has(id)) {
    if (btn) { btn.disabled = true; btn.textContent = "Cargando preview…"; }
    try {
      const res = await fetch(API + "/api/clips/" + id + "/video", { credentials: "include", headers: authHeaders() });
      if (res.status === 401) { showLogin(); showMsg("Sesión caducada, entra de nuevo.", "err"); return; }
      if (res.status === 404) { showMsg("Este clip aún no tiene MP4 en el panel.", "err"); return; }
      if (!res.ok) { showMsg("No se pudo cargar el vídeo: HTTP " + res.status, "err"); return; }
      queuePreviewUrls.set(id, URL.createObjectURL(await res.blob()));
    } catch (e) { showMsg("No se pudo cargar el vídeo: " + e.message, "err"); return; }
    finally { if (btn) btn.disabled = false; }
  }
  video.src = queuePreviewUrls.get(id);
  frame.hidden = false;
  if (btn) btn.textContent = "Ocultar preview";
  try { await video.play(); } catch {}
}
function revokeQueuePreviews() {
  for (const url of queuePreviewUrls.values()) URL.revokeObjectURL(url);
  queuePreviewUrls.clear();
}
// Abre el Editor en la sección "SEO y puntuación (IA)" del clip.
async function openEditorSeo(id) {
  await selectClip(id, { noScroll: true });
  switchTab("editor");
  const card = $("edSeoCard");
  if (card) card.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
}
function queueSectionHtml(heading, count, clips, emptyNote, live) {
  const body = clips.length
    ? `<div class="clip-table-wrap"><table><caption class="visually-hidden">${esc(heading)}</caption><thead><tr><th scope="col">Clip</th><th scope="col">Streamer</th><th scope="col">Estado</th><th scope="col" class="num">Viralidad</th><th scope="col">Plataformas</th><th scope="col">Creado</th></tr></thead><tbody>${clips.map(clipRowHtml).join("")}</tbody></table></div>` +
      `<div class="clip-cards">${clips.map((c) => clipCardHtml(c, c.id === selectedClipId)).join("")}</div>`
    : `<p class="dim">${esc(emptyNote)}</p>`;
  return `<section class="queue-segment" aria-label="${esc(heading)}"><h3>${live ? `<span class="live-dot" aria-hidden="true"></span>` : ""}${esc(heading)} <span class="dim">(${count})</span></h3>${body}</section>`;
}
async function loadPublished() {
  // Pestaña "Subidos" (orden del usuario 2026-11-22 01:07): clips ya
  // publicados con su MP4 en R2 o ya borrado (retención 24 h, 0039).
  // Antes era una sección al final de COLA; ahora es pestaña propia.
  // Los publicados se registran en queueClipsById para que su
  // preview funcione.
  const list = $("publishedList");
  list.setAttribute("aria-busy", "true");
  list.innerHTML = skeletons(3, 120);
  try {
    const pub = await api("/api/clips?status=publicado");
    const published = pub.clips || [];
    published.forEach((c) => queueClipsById.set(c.id, c));
    list.setAttribute("aria-busy", "false");
    list.innerHTML = queueSectionHtml("Subidos · retención 24 h", published.length, published, "Sin clips subidos todavía.", false);
    bindClipButtons();
  } catch (e) {
    list.setAttribute("aria-busy", "false");
    list.innerHTML = `<p><b>No se pudieron cargar los subidos.</b></p><p class="dim">${esc(e.message)}</p><button type="button" id="retryPublished">Reintentar</button>`;
    $("retryPublished").addEventListener("click", loadPublished);
    showMsg(e.message, "err");
  }
}
async function loadClips() {
  const list = $("clipList");
  list.setAttribute("aria-busy", "true");
  revokeQueuePreviews();
  list.innerHTML = skeletons(3, 120);
  try {
    const data = await api("/api/clips");
    const clips = data.clips || [];
    // El preview con frame necesita los datos del clip (título,
    // descripción, tags, SEO por plataforma) sin otra llamada.
    queueClipsById.clear();
    clips.forEach((c) => queueClipsById.set(c.id, c));
    list.setAttribute("aria-busy", "false");
    if (!clips.length) {
      $("clipDetailCard").classList.add("hidden");
      list.innerHTML = `<div class="empty-state"><h3>La cola está vacía</h3><p class="dim">Los clips aparecen aquí cuando el cerebro los detecta o cuando se migran del panel anterior.</p><div class="row"><button type="button" id="emptyGoWorker">Ir al Worker</button><button type="button" class="ghost" id="emptyRefreshClips">Actualizar</button></div></div>`;
      $("emptyGoWorker").addEventListener("click", () => switchTab("worker"));
      $("emptyRefreshClips").addEventListener("click", loadClips);
      bindClipButtons();
      return;
    }
    // DOS COLAS SEPARADAS (0019, 2026-11-18): "Cola en Español" y
    // "Cola en Inglés", cada una con sus segmentos LIVE/VOD y sus
    // contadores. Un clip aparece SOLO en su cola (por su idioma);
    // la API ya devuelve el orden LIVE→VOD dentro de cada cola.
    const es = clips.filter((c) => clipLanguage(c) === "es");
    const en = clips.filter((c) => clipLanguage(c) === "en");
    const langQueueHtml = (lang, list) => {
      const live = list.filter((c) => c.origin === "live");
      const vod = list.filter((c) => c.origin !== "live");
      return `<section class="queue-lang" aria-label="Cola en ${langName(lang)}"><h2>Cola en ${langName(lang)} <span class="dim">(${list.length})</span></h2>` +
        queueSectionHtml(`En vivo (LIVE) · ${langName(lang)}`, live.length, live, "Sin clips en vivo todavía.", true) +
        queueSectionHtml(`VOD · ${langName(lang)}`, vod.length, vod, "Sin clips de VOD todavía.", false) +
        `</section>`;
    };
    list.innerHTML = langQueueHtml("es", es) + langQueueHtml("en", en);
    bindClipButtons();
  } catch (e) {
    list.setAttribute("aria-busy", "false");
    list.innerHTML = `<p><b>No se pudo cargar la cola.</b></p><p class="dim">${esc(e.message)}</p><button type="button" id="retryClips">Reintentar</button>`;
    $("retryClips").addEventListener("click", loadClips);
    showMsg(e.message, "err");
  }
}
function renderBrakes() {
  const box = $("clipBrakes");
  const list = $("brakeList");
  const note = $("brakeNote");
  if (!currentBlocked.length) { box.classList.add("hidden"); note.hidden = true; return; }
  box.classList.remove("hidden");
  note.hidden = true;
  list.innerHTML = currentBlocked.map((b) => `<div class="brake-chip">${esc(b)}</div>`).join("");
}
async function selectClip(id, opts) {
  // El detalle vive en la pestaña COLA: si se pulsa un clip desde
  // SUBIDOS, ir a COLA para que el detalle se vea.
  if (typeof currentTab !== "undefined" && currentTab === "subidos") switchTab("cola");
  selectedClipId = id;
  currentBlocked = (opts && opts.keepBrakes) ? currentBlocked : [];
  const card = $("clipDetailCard");
  card.classList.remove("hidden");
  card.setAttribute("aria-busy", "true");
  $("clipTitle").textContent = "Cargando clip…";
  $("clipDetail").innerHTML = skeletons(3, 20);
  $("clipTracking").innerHTML = skeletons(3, 44);
  try {
    const data = await api("/api/clips/" + id);
    const c = data.clip;
    selectedClipTitle = clipTitleOf(c);
    selectedClipPlatforms = parsePlatforms(c.platforms).map((p) => PLATFORM_LABEL[String(p).toLowerCase()] || String(p));
    card.setAttribute("aria-busy", "false");
    $("clipTitle").textContent = selectedClipTitle;
    const ring = $("clipScoreRing");
    ring.textContent = c.score ?? "—";
    ring.className = "score-ring lg " + scoreClass(c.score);
    $("clipDetail").innerHTML =
      `<p>${statusPill(c.status)} ${langPill(c)} · Idioma: <b>${langName(clipLanguage(c))} (${clipLanguage(c).toUpperCase()})</b> · Categoría: <b>${esc(c.game || "sin categoría")}</b> · Puntos: <b>${c.score ?? "—"}</b> · Plataformas: ${esc(parsePlatforms(c.platforms).join(", "))}</p>` +
      scoreChipsHtml(c) +
      scoreBar(c) +
      platformMatrix(c) +
      retentionHtml(data) +
      (c.thumb_text ? `<p>Texto de miniatura: <b>${esc(c.thumb_text)}</b></p>` : "") +
      (c.seo_description ? `<p>${esc(c.seo_description)}</p>` : "") +
      (c.seo_tags ? `<p class="dim">Tags: ${esc(c.seo_tags)}</p>` : "");
    renderBrakes();
    bindBreakdownButtons();
    resetUploadBlock();
    const tr = await api("/api/clips/" + id + "/tracking");
    $("clipTracking").setAttribute("aria-busy", "false");
    const evs = tr.events || [];
    $("clipTracking").innerHTML = evs.length ? evs.map((e, i) =>
      `<li><span class="dot ${e.failed ? "bad" : "ok"}" aria-hidden="true"></span><time datetime="${new Date(Number(e.at)).toISOString()}">${fmtDate(e.at)}</time><span class="tl-kind">${esc(e.step)}</span>${e.failed ? ' <span class="pill bad">Fallo</span>' : ""}<br><span class="tl-msg">${e.actor ? esc(e.actor) + " — " : ""}${esc(e.message)}</span>${i === evs.length - 1 ? '<br><span class="dim">Último paso</span>' : ""}</li>`).join("")
      : `<li class="dim">Sin seguimiento todavía.</li>`;
    if (!opts || !opts.noScroll) {
      card.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
    }
    $("clipTitle").focus({ preventScroll: true });
  } catch (e) {
    card.setAttribute("aria-busy", "false");
    card.classList.add("hidden");
    selectedClipId = null;
    showMsg(e.message, "err");
  }
}
async function publishSelected() {
  if (!selectedClipId) return;
  const names = selectedClipPlatforms.join(", ");
  const extra = currentBlocked.length ? "\nHay frenos activos en este clip (ver arriba). Publicar igualmente los dejará registrados." : "";
  const ok = window.confirm(`¿Publicar este clip?\n\nSe va a publicar «${selectedClipTitle}» en: ${names || "las plataformas del clip"}. Se encolan los trabajos y el Worker los procesa según los límites diarios.${extra}`);
  if (!ok) return;
  const btn = $("btnPublish");
  const up = $("btnUpload");
  setLoading(btn, true, "Publicando…");
  up.disabled = true;
  try {
    const r = await api("/api/clips/" + selectedClipId + "/publish", { method: "POST" });
    currentBlocked = r.blocked || [];
    const warnings = r.warnings || [];
    showMsg("Encolado: " + (r.jobs || []).map((j) => j.platform + " (" + j.state + ")").join(", ") + (currentBlocked.length ? " · Frenado: " + currentBlocked.join(" · ") : "") + (warnings.length ? " · Publicado a mano pese al freno: " + warnings.join(" · ") : ""), "info");
    await selectClip(selectedClipId, { keepBrakes: true, noScroll: true });
    if (!currentBlocked.length) {
      $("clipBrakes").classList.remove("hidden");
      $("brakeList").innerHTML = "";
      $("brakeNote").hidden = false;
    }
  } catch (e) { showMsg(e.message, "err"); }
  finally { setLoading(btn, false); up.disabled = false; }
}

// --- Subida MP4 (XHR, progreso real) ---
const MAX_MB = 128;
function resetUploadBlock() {
  $("videoFile").value = "";
  setFieldError("uploadError", "");
  $("uploadInfo").hidden = true;
  $("uploadProgress").classList.add("hidden");
  $("uploadProgressFill").style.width = "0";
  $("uploadStatus").hidden = true;
  $("uploadResult").hidden = true;
  $("uploadResult").innerHTML = "";
  $("btnCancelUpload").classList.add("hidden");
  $("btnUpload").disabled = false;
}
function validateVideoFile() {
  const f = $("videoFile").files[0];
  const btn = $("btnUpload");
  setFieldError("uploadError", "");
  $("uploadInfo").hidden = true;
  if (!f) { btn.disabled = false; return null; }
  const mb = Math.round(f.size / 1024 / 1024);
  if (f.type !== "video/mp4" && !(f.type || "").startsWith("video/")) {
    setFieldError("uploadError", "Ese archivo no es un vídeo MP4. Elige un archivo .mp4.");
    btn.disabled = true;
    return null;
  }
  if (f.size > MAX_MB * 1024 * 1024) {
    setFieldError("uploadError", `El archivo pesa ${mb} MB y el máximo es 128 MB. Elige uno más ligero.`);
    btn.disabled = true;
    return null;
  }
  $("uploadInfo").textContent = `${f.name} · ${mb} MB`;
  $("uploadInfo").hidden = false;
  btn.disabled = false;
  return f;
}
function uploadSelected() {
  if (!selectedClipId) return;
  const f = $("videoFile").files[0];
  if (!f) { setFieldError("uploadError", "Elige un archivo MP4 primero."); $("videoFile").focus(); return; }
  if (!validateVideoFile()) return;
  const btn = $("btnUpload");
  const mbTotal = Math.round(f.size / 1024 / 1024);
  setLoading(btn, true, "Subiendo…");
  $("btnCancelUpload").classList.remove("hidden");
  $("uploadProgress").classList.remove("hidden");
  $("uploadProgress").classList.remove("indeterminate");
  $("uploadStatus").hidden = false;
  $("uploadStatus").textContent = `Subiendo ${f.name} (${mbTotal} MB)… 0 %`;
  const xhr = new XMLHttpRequest();
  uploadXhr = xhr;
  xhr.open("PUT", API + "/api/clips/" + selectedClipId + "/video?filename=" + encodeURIComponent(f.name), true);
  xhr.withCredentials = true;
  xhr.setRequestHeader("Content-Type", f.type || "video/mp4");
  { const h = authHeaders(); if (h.Authorization) xhr.setRequestHeader("Authorization", h.Authorization); }
  xhr.upload.onprogress = (ev) => {
    if (!ev.lengthComputable) return;
    const pct = Math.round((ev.loaded / ev.total) * 100);
    const mbUp = Math.round(ev.loaded / 1024 / 1024);
    $("uploadProgressFill").style.width = pct + "%";
    $("uploadStatus").textContent = `${mbUp} de ${mbTotal} MB · ${pct} %`;
  };
  xhr.onload = () => {
    uploadXhr = null;
    $("btnCancelUpload").classList.add("hidden");
    setLoading(btn, false);
    if (xhr.status === 401) { showLogin(); showMsg("Sesión caducada, entra de nuevo.", "err"); return; }
    if (xhr.status < 200 || xhr.status >= 300) {
      let m = "HTTP " + xhr.status;
      try { const d = JSON.parse(xhr.responseText); if (d.error) m = d.error; } catch {}
      setFieldError("uploadError", m);
      showMsg(m, "err");
      return;
    }
    let d = {};
    try { d = JSON.parse(xhr.responseText); } catch {}
    $("uploadProgressFill").style.width = "100%";
    $("uploadStatus").textContent = `${mbTotal} de ${mbTotal} MB · 100 %`;
    const res = $("uploadResult");
    res.hidden = false;
    res.innerHTML = `<p><span class="pill ok">MP4 listo</span></p><p class="mono">MP4 guardado en R2: ${esc(d.r2_key || "")}</p><p><a href="${esc(API + (d.video_url || ""))}" target="_blank" rel="noopener">Ver el vídeo en el Editor</a> <button type="button" class="ghost" id="btnCopyUrl">Copiar</button></p>`;
    const copy = $("btnCopyUrl");
    if (copy) copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(API + (d.video_url || "")); copy.textContent = "Copiado"; setTimeout(() => { copy.textContent = "Copiar"; }, 2000); } catch {}
    });
    showMsg("MP4 subido.", "ok");
  };
  xhr.onerror = () => {
    uploadXhr = null;
    $("btnCancelUpload").classList.add("hidden");
    setLoading(btn, false);
    setFieldError("uploadError", "No se pudo subir el MP4. Comprueba tu conexión y reintenta.");
    showMsg("No se pudo subir el MP4.", "err");
  };
  xhr.onabort = () => {
    uploadXhr = null;
    $("btnCancelUpload").classList.add("hidden");
    setLoading(btn, false);
    $("uploadProgress").classList.add("hidden");
    showMsg("Subida cancelada.", "info");
  };
  xhr.send(f);
}

// --- Crear clip a mano (Cola) ---
// El endpoint POST /api/clips ya existe (lo usa el cerebro): crea el
// clip en la Cola con sus validaciones (título, idioma es/en, GTA VI,
// duración 1–60 s desde Ajustes, viralidad ≥45, SEO). Los trabajos NO
// se crean aquí: los crea "Publicar ahora" (POST /api/clips/:id/publish)
// con sus puertas (falta de MP4, SEO, calentamiento, topes). El MP4
// opcional de este formulario usa el mismo PUT /api/clips/:id/video
// que el botón "Subir MP4 a R2" del detalle del clip.
const CREATE_CLIP_PLATFORMS = [
  { key: "youtube_shorts", accountPlatform: "youtube", label: "YouTube Shorts" },
  { key: "tiktok", accountPlatform: "tiktok", label: "TikTok" },
  { key: "instagram_reels", accountPlatform: "meta", label: "Instagram" },
  { key: "facebook_reels", accountPlatform: "meta", label: "Facebook" },
];
let createClipLoaded = false;
let createClipAccounts = [];
let createClipInfluencers = [];

function createClipPlatformInput(p) { return $("cc-plat-" + p); }

async function loadCreateClipForm() {
  if (createClipLoaded) { refreshCreateClipPlatforms(); return; }
  createClipLoaded = true;
  try {
    const inf = await api("/api/influencers");
    createClipInfluencers = inf.influencers || [];
  } catch (e) { createClipInfluencers = []; }
  try {
    const acc = await api("/api/accounts");
    createClipAccounts = acc.accounts || [];
  } catch (e) { createClipAccounts = []; }
  const sel = $("cc-influencer");
  if (sel) {
    sel.innerHTML = `<option value="">— Sin streamer —</option>` + createClipInfluencers.map((i) => `<option value="${i.id}">${esc(i.name || i.handle || ("#" + i.id))}${i.language ? " (" + esc(String(i.language).toUpperCase()) + ")" : ""}</option>`).join("");
  }
  refreshCreateClipPlatforms();
}

function refreshCreateClipPlatforms() {
  const lang = ($("cc-language") && $("cc-language").value) || "es";
  const notes = [];
  for (const p of CREATE_CLIP_PLATFORMS) {
    const input = createClipPlatformInput(p.key);
    if (!input) continue;
    // Sin cuentas cargadas (error de red): no bloquear nada; la
    // puerta de publicación frena la plataforma sin cuenta.
    if (!createClipAccounts.length) { input.disabled = false; continue; }
    const ok = createClipAccounts.some((a) => a.platform === p.accountPlatform && (a.language === lang || !a.language) && a.status === "conectada" && a.verify_ok);
    input.disabled = !ok;
    if (!ok) {
      input.checked = false;
      notes.push(p.label + ": sin cuenta conectada en " + (lang === "es" ? "español" : "inglés"));
    }
  }
  const hint = $("cc-plat-hint");
  if (hint) hint.textContent = notes.length ? notes.join(" · ") + "." : "Todas las plataformas tienen cuenta conectada en este idioma.";
}

function validateCreateClipVideo() {
  const f = $("cc-video").files[0];
  setFieldError("cc-video-error", "");
  $("cc-video-info").hidden = true;
  if (!f) return null;
  const mb = Math.round(f.size / 1024 / 1024);
  if (f.type !== "video/mp4" && !(f.type || "").startsWith("video/")) {
    setFieldError("cc-video-error", "Ese archivo no es un vídeo MP4. Elige un archivo .mp4.");
    return null;
  }
  if (f.size > MAX_MB * 1024 * 1024) {
    setFieldError("cc-video-error", `El archivo pesa ${mb} MB y el máximo es 128 MB. Elige uno más ligero.`);
    return null;
  }
  $("cc-video-info").textContent = `${f.name} · ${mb} MB`;
  $("cc-video-info").hidden = false;
  return f;
}

function uploadMp4ForClip(clipId, file) {
  return new Promise((resolve, reject) => {
    const mbTotal = Math.round(file.size / 1024 / 1024);
    const prog = $("cc-uploadProgress");
    const fill = $("cc-uploadProgressFill");
    const status = $("cc-uploadStatus");
    prog.classList.remove("hidden");
    status.hidden = false;
    status.textContent = `Subiendo ${file.name} (${mbTotal} MB)… 0 %`;
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", API + "/api/clips/" + clipId + "/video?filename=" + encodeURIComponent(file.name), true);
    xhr.withCredentials = true;
    xhr.setRequestHeader("Content-Type", file.type || "video/mp4");
    { const h = authHeaders(); if (h.Authorization) xhr.setRequestHeader("Authorization", h.Authorization); }
    xhr.upload.onprogress = (ev) => {
      if (!ev.lengthComputable) return;
      const pct = Math.round((ev.loaded / ev.total) * 100);
      fill.style.width = pct + "%";
      status.textContent = `${Math.round(ev.loaded / 1024 / 1024)} de ${mbTotal} MB · ${pct} %`;
    };
    xhr.onload = () => {
      if (xhr.status === 401) { showLogin(); reject(new Error("Sesión caducada, entra de nuevo.")); return; }
      if (xhr.status < 200 || xhr.status >= 300) {
        let m = "HTTP " + xhr.status;
        try { const d = JSON.parse(xhr.responseText); if (d.error) m = d.error; } catch {}
        reject(new Error(m)); return;
      }
      let d = {};
      try { d = JSON.parse(xhr.responseText); } catch {}
      fill.style.width = "100%";
      status.textContent = `${mbTotal} de ${mbTotal} MB · 100 %`;
      resolve(d);
    };
    xhr.onerror = () => reject(new Error("No se pudo subir el MP4. Comprueba tu conexión y reintenta."));
    xhr.onabort = () => reject(new Error("Subida cancelada."));
    xhr.send(file);
  });
}

async function submitCreateClip(ev) {
  ev.preventDefault();
  const btn = $("cc-submit");
  setFieldError("cc-title-error", "");
  setFieldError("cc-platforms-error", "");
  setFieldError("cc-duration-error", "");
  setFieldError("cc-score-error", "");
  setFieldError("cc-error", "");
  const res = $("cc-result");
  res.hidden = true;
  const title = $("cc-title").value.trim();
  if (!title) { setFieldError("cc-title-error", "Falta el título del clip."); $("cc-title").focus(); return; }
  if (title.length > 200) { setFieldError("cc-title-error", "El título no puede superar 200 caracteres."); return; }
  const platforms = CREATE_CLIP_PLATFORMS.map((p) => p.key).filter((k) => { const i = createClipPlatformInput(k); return i && i.checked && !i.disabled; });
  if (!platforms.length) { setFieldError("cc-platforms-error", "Elige al menos una plataforma con cuenta conectada."); return; }
  const startSec = Number($("cc-start-sec").value);
  const endSec = Number($("cc-end-sec").value);
  if (!Number.isInteger(startSec) || startSec < 0) { setFieldError("cc-duration-error", "El inicio debe ser un número entero mayor o igual a 0."); return; }
  if (!Number.isInteger(endSec) || endSec <= startSec) { setFieldError("cc-duration-error", "El fin debe ser un número entero mayor que el inicio."); return; }
  if (endSec - startSec > 60) { setFieldError("cc-duration-error", "La duración supera el tope de 60 s."); return; }
  const score = Number($("cc-score").value);
  if (!Number.isInteger(score) || score < 0 || score > 100) { setFieldError("cc-score-error", "La viralidad debe ser un número entero entre 0 y 100."); return; }
  if (score < 45) { setFieldError("cc-score-error", "Viralidad " + score + ": bajo el mínimo de 45 para entrar en la Cola. Busca un momento más fuerte."); return; }
  const file = validateCreateClipVideo();
  if ($("cc-video").files[0] && !file) return;
  const body = {
    title,
    language: $("cc-language").value,
    platforms,
    start_sec: startSec,
    end_sec: endSec,
    score,
    game: "GTA VI",
    subtitles_complete: $("cc-subs").checked ? 1 : 0,
    origin: "vod",
  };
  const infId = $("cc-influencer").value;
  if (infId) body.influencer_id = Number(infId);
  const sourceId = $("cc-source-id").value.trim();
  if (sourceId !== "") body.source_id = Number(sourceId);
  const sourceUrl = $("cc-source-url").value.trim();
  if (sourceUrl) body.reason = sourceUrl;
  const seoTitle = $("cc-seo-title").value.trim();
  if (seoTitle) body.seo_title = seoTitle;
  const seoDesc = $("cc-seo-description").value.trim();
  if (seoDesc) body.seo_description = seoDesc;
  const seoTags = $("cc-seo-tags").value.trim();
  if (seoTags) body.seo_tags = seoTags;
  const thumb = $("cc-thumb-text").value.trim();
  if (thumb) body.thumbnail_text = thumb;
  setLoading(btn, true, "Creando…");
  try {
    const r = await api("/api/clips", { method: "POST", body: JSON.stringify(body) });
    const clip = r.clip;
    let msg = `Clip #${clip.id} creado en la Cola.`;
    if (file) {
      try {
        await uploadMp4ForClip(clip.id, file);
        msg += " MP4 subido a R2.";
      } catch (e) {
        msg += " El MP4 no se pudo subir (" + e.message + "): súbelo desde el detalle del clip.";
      }
    }
    res.hidden = false;
    res.textContent = msg;
    showMsg(msg, "ok");
    $("createClipForm").reset();
    $("cc-uploadProgress").classList.add("hidden");
    $("cc-uploadProgressFill").style.width = "0";
    $("cc-uploadStatus").hidden = true;
    refreshCreateClipPlatforms();
    await loadClips();
    await selectClip(clip.id);
  } catch (e) {
    setFieldError("cc-error", e.message);
    showMsg(e.message, "err");
  } finally {
    setLoading(btn, false);
  }
}

// --- Cuentas: sub-pestañas sociales / APIs ---
const SOCIAL_PLATFORMS = [
  { key: "youtube", label: "YouTube", dot: "youtube", metaNote: "" },
  { key: "tiktok", label: "TikTok", dot: "tiktok", metaNote: "" },
  { key: "instagram", label: "Instagram", dot: "instagram", metaNote: "Se conecta junto con Facebook mediante un solo token de Página de Meta. Si el panel no ve Instagram aunque en Meta diga conectada, re-vincula Instagram a la Página y genera el token DESPUÉS." },
  { key: "facebook", label: "Facebook", dot: "facebook", metaNote: "Se conecta junto con Instagram mediante un solo token de Página de Meta. Usa el token LARGO (el corto vence en 1 hora) y guárdalo junto con el Extra." },
];
function socialStatusPill(status) {
  const s = String(status || "").toLowerCase();
  if (s.includes("conect")) return `<span class="pill ok">Conectada</span>`;
  if (s.includes("sin conectar") || s === "sin_conectar" || s === "") return `<span class="pill neutral">Sin conectar</span>`;
  return `<span class="pill neutral">${esc(status)}</span>`;
}
function renderSocialAccounts(accounts) {
  const list = $("socialList");
  if (!list) return;
  if (!accounts.length) {
    list.innerHTML = `<div class="empty-state"><h3>Sin cuentas sociales todavía</h3><p class="dim">Las cuentas sociales aparecen aquí cuando se migran desde el panel anterior. Desde este panel no se puede conectar una cuenta nueva.</p></div>`;
    return;
  }
  const byKey = {};
  SOCIAL_PLATFORMS.forEach((p) => { byKey[p.key] = p; });
  list.innerHTML = accounts.map((a) => {
    const key = String(a.platform || "").toLowerCase();
    const p = byKey[key] || { key, label: a.label || a.platform || key, dot: "", metaNote: "" };
    return `<article class="card account-card" id="social-card-${esc(key)}-${a.id}">
    <div class="account-head">
      <span class="platform-mark" aria-hidden="true"><span class="dot ${p.dot}"></span></span>
      <div class="account-title"><h3>${esc(a.label || p.label)}</h3>${a.handle ? `<p class="account-sub">${esc(a.handle)}</p>` : `<p class="account-sub">${esc(p.label)}</p>`}</div>
      ${socialStatusPill(a.status)}
    </div>
    ${p.metaNote ? `<p class="dim">${esc(p.metaNote)}</p>` : ""}
    ${a.note ? `<p class="dim">${esc(a.note)}</p>` : ""}
    <p class="field-error" id="social-${a.id}-error" role="alert" hidden></p>
    <div class="row account-actions">
      <button type="button" class="ghost" data-verify-social="${a.id}">Verificar</button>
    </div>
  </article>`;
  }).join("");
  list.querySelectorAll("[data-verify-social]").forEach((b) => b.addEventListener("click", async () => {
    const id = b.dataset.verifySocial;
    setLoading(b, true, "Verificando…");
    try {
      await api("/api/social-accounts/" + id + "/verify", { method: "POST" });
      showMsg("Verificación: conectada.", "info");
    } catch (e) {
      // El Worker responde 501: no hay credencial que verificar en
      // cuentas sociales. Se muestra su mensaje tal cual, sin fingir éxito.
      setFieldError("social-" + id + "-error", e.message);
      showMsg(e.message, "err");
    } finally { setLoading(b, false); }
  }));
}
async function loadSocialAccounts() {
  const list = $("socialList");
  if (!list) return;
  list.setAttribute("aria-busy", "true");
  list.innerHTML = skeletons(4, 120);
  try {
    const data = await api("/api/social-accounts");
    list.setAttribute("aria-busy", "false");
    renderSocialAccounts(data.accounts || []);
  } catch (e) {
    list.setAttribute("aria-busy", "false");
    list.innerHTML = `<p><b>No se pudieron cargar las cuentas sociales.</b></p><p class="dim">${esc(e.message)}</p><button type="button" id="retrySocial">Reintentar</button>`;
    $("retrySocial").addEventListener("click", loadSocialAccounts);
    showMsg(e.message, "err");
  }
}
function switchCuentasSubtab(name, focusPanel) {
  const showSocial = name === "social";
  $("subtab-social").hidden = showSocial ? false : true;
  $("subtab-apis").hidden = showSocial ? true : false;
  $("subtabbtn-social").classList.toggle("active", showSocial);
  $("subtabbtn-social").setAttribute("aria-selected", showSocial ? "true" : "false");
  $("subtabbtn-apis").classList.toggle("active", !showSocial);
  $("subtabbtn-apis").setAttribute("aria-selected", showSocial ? "false" : "true");
  if (focusPanel) $(showSocial ? "subtab-social" : "subtab-apis").focus({ preventScroll: true });
}
const INF_PLATFORM_LABEL = { twitch: "Twitch", youtube: "YouTube", kick: "Kick" };
const INF_PLATFORM_DOT = { twitch: "twitch", youtube: "youtube", kick: "kick" };
function influencerCardHtml(inf) {
  const key = String(inf.platform || "").toLowerCase();
  const label = INF_PLATFORM_LABEL[key] || inf.platform || key;
  const lang = String(inf.language || "es").toLowerCase() === "en" ? "Inglés" : "Español";
  const game = inf.game || "GTA VI";
  const platforms = Array.isArray(inf.publish_platforms) ? inf.publish_platforms : [];
  return `<article class="card account-card" id="inf-card-${inf.id}" aria-labelledby="inf-title-${inf.id}">
    <div class="account-head">
      <span class="platform-mark" aria-hidden="true"><span class="dot ${INF_PLATFORM_DOT[key] || ""}"></span></span>
      <div class="account-title">
        <h3 id="inf-title-${inf.id}">${esc(inf.handle)}</h3>
        <p class="account-sub">${esc(label)} · ${esc(lang)}</p>
      </div>
      <span class="pill violet">${esc(game)}</span>
    </div>
    ${inf.url ? `<p><a href="${esc(inf.url)}" target="_blank" rel="noopener">${esc(inf.url)}</a></p>` : ""}
    ${inf.notes ? `<p class="dim">${esc(inf.notes)}</p>` : ""}
    <div class="pill-row">
      <span class="pill ${INF_PLATFORM_DOT[key] ? "" : "neutral"}"><span class="dot ${INF_PLATFORM_DOT[key] || ""}" aria-hidden="true"></span> ${esc(label)}</span>
      <span class="pill ${inf.permission ? "ok" : "neutral"}">Permiso: ${inf.permission ? "sí" : "no"}</span>
      <span class="pill ${inf.active ? "ok" : "neutral"}">Activo: ${inf.active ? "sí" : "no"}</span>
      <span class="pill neutral" id="inf-live-${inf.id}" role="status">En directo: comprobando…</span>
      ${inf.handle.toLowerCase() === "rubius" ? `<span class="pill sunset">En lanzamiento</span>` : ""}
    </div>
    ${inf.permission_date ? `<p class="dim">Permiso: ${esc(inf.permission_date)} · Publica en: ${esc(platforms.join(", ") || "—")}</p>` : ""}
    <p class="field-error" id="inf-${inf.id}-error" role="alert" hidden></p>
    <div class="toggle-row">
      <span id="inf-toggle-permission-label-${inf.id}">Permiso para clipear</span>
      <button type="button" class="toggle-switch${inf.permission ? " on" : ""}" id="inf-toggle-permission-${inf.id}" role="switch" aria-checked="${inf.permission ? "true" : "false"}" aria-labelledby="inf-toggle-permission-label-${inf.id}" data-inf-toggle="permission" data-inf-id="${inf.id}"><span class="toggle-knob" aria-hidden="true"></span></button>
    </div>
    <div class="toggle-row">
      <span id="inf-toggle-active-label-${inf.id}">Activo en la cola</span>
      <button type="button" class="toggle-switch${inf.active ? " on" : ""}" id="inf-toggle-active-${inf.id}" role="switch" aria-checked="${inf.active ? "true" : "false"}" aria-labelledby="inf-toggle-active-label-${inf.id}" data-inf-toggle="active" data-inf-id="${inf.id}"><span class="toggle-knob" aria-hidden="true"></span></button>
    </div>
    <div class="row">
      <button type="button" class="ghost" data-inf-delete="${inf.id}">Eliminar</button>
    </div>
    <p class="dim">10 influencers activos con permiso para GTA VI: Rubius, IlloJuan, AuronPlay, Ibai, TheGrefg, Xokas, DjMaRiiO, Knekro, RDJavi y Westcol. Cualquier otro influencer queda desactivado.</p>
  </article>`;
}
function bindInfluencerCards() {
  document.querySelectorAll("[data-inf-toggle]").forEach((b) => b.addEventListener("click", async () => {
    const id = b.dataset.infId;
    const field = b.dataset.infToggle;
    const next = b.getAttribute("aria-checked") !== "true";
    b.disabled = true;
    setFieldError("inf-" + id + "-error", "");
    try {
      await api("/api/influencers/" + id, { method: "PATCH", body: JSON.stringify({ [field]: next }) });
      await loadInfluencers();
    } catch (e) {
      // El Worker devuelve 400 con el motivo de la regla de
      // lanzamiento (10 activos) cuando se intenta activar a alguien
      // que no es del lanzamiento: se muestra tal cual, sin cambiar nada.
      b.disabled = false;
      setFieldError("inf-" + id + "-error", e.message);
      showMsg(e.message, "err");
    }
  }));
  document.querySelectorAll("[data-inf-delete]").forEach((b) => b.addEventListener("click", async () => {
    const id = b.dataset.infId;
    const card = $("inf-card-" + id);
    const name = card ? (card.querySelector("h3") || {}).textContent : ("#" + id);
    if (!window.confirm(`¿Eliminar el influencer «${name}»?\n\nSe borra de la lista del Worker. Esta acción no se puede deshacer.`)) return;
    b.disabled = true;
    try {
      await api("/api/influencers/" + id, { method: "DELETE" });
      showMsg(`Influencer «${name}» eliminado.`, "info");
      await loadInfluencers();
    } catch (e) {
      b.disabled = false;
      setFieldError("inf-" + id + "-error", e.message);
      showMsg(e.message, "err");
    }
  }));
}
async function loadInfluencers() {
  const list = $("influencerList");
  const empty = $("influencerEmpty");
  if (list) { list.setAttribute("aria-busy", "true"); list.innerHTML = skeletons(2, 180); }
  try {
    const data = await api("/api/influencers");
    const infs = data.influencers || [];
    if (list) {
      list.setAttribute("aria-busy", "false");
      list.innerHTML = infs.map(influencerCardHtml).join("");
      bindInfluencerCards();
      refreshInfluencerLive(infs);
    }
    if (empty) empty.hidden = infs.length > 0;
    enableInfluencerForm(true);
  } catch (e) {
    if (list) {
      list.setAttribute("aria-busy", "false");
      list.innerHTML = `<p><b>No se pudo cargar la lista de influencers.</b></p><p class="dim">${esc(e.message)}</p><button type="button" id="retryInfluencers">Reintentar</button>`;
      $("retryInfluencers").addEventListener("click", loadInfluencers);
    }
    if (empty) empty.hidden = true;
    showMsg(e.message, "err");
  }
}
// Estado en vivo/offline (GET /api/influencers/live): actualiza la
// píldora de cada tarjeta. "Sin verificar" solo sale cuando el
// Worker dice que no pudo comprobarlo (sin credenciales de Twitch):
// nunca se inventa un vivo ni un offline.
async function refreshInfluencerLive(infs) {
  let data = null;
  try {
    data = await api("/api/influencers/live");
  } catch (e) {
    (infs || []).forEach((inf) => {
      const el = $("inf-live-" + inf.id);
      if (el) { el.textContent = "En directo: sin verificar"; el.className = "pill neutral"; }
    });
    return;
  }
  const byId = new Map((data.live || []).map((x) => [x.id, x]));
  (infs || []).forEach((inf) => {
    const el = $("inf-live-" + inf.id);
    if (!el) return;
    const st = byId.get(inf.id);
    if (!st || st.live === null || st.live === undefined) {
      el.textContent = "En directo: sin verificar";
      el.className = "pill neutral";
      el.title = st?.reason || "";
      return;
    }
    if (st.live) {
      el.textContent = "● En directo" + (st.viewers ? ` · ${st.viewers} espectadores` : "");
      el.className = "pill ok";
      el.title = st.title || "";
    } else {
      el.textContent = "Offline";
      el.className = "pill neutral";
    }
  });
}
function enableInfluencerForm(on) {
  ["inf-name", "inf-platform", "inf-profile-url", "inf-language", "inf-permission", "inf-active", "inf-notes", "infSubmit"].forEach((id) => {
    const el = $(id);
    if (el) el.disabled = !on;
  });
}
async function saveInfluencer(ev) {
  if (ev) ev.preventDefault();
  const btn = $("infSubmit");
  if (btn.disabled) return;
  setFieldError("inf-name-error", "");
  const name = $("inf-name").value.trim();
  const platform = $("inf-platform").value.trim().toLowerCase();
  const url = $("inf-profile-url").value.trim();
  const language = $("inf-language").value.trim().toLowerCase().startsWith("ingl") ? "en" : "es";
  const permission = $("inf-permission").checked;
  const active = $("inf-active").checked;
  const notes = $("inf-notes").value.trim();
  if (!name) {
    setFieldError("inf-name-error", "Escribe el nombre o handle.");
    $("inf-name").setAttribute("aria-invalid", "true");
    $("inf-name").focus();
    return;
  }
  setLoading(btn, true, "Añadiendo…");
  try {
    const r = await api("/api/influencers", { method: "POST", body: JSON.stringify({ handle: name, platform, url: url || undefined, language, permission, active, notes: notes || undefined }) });
    showMsg(`Influencer «${r.influencer.handle}» añadido.`, "ok");
    $("influencerForm").reset();
    $("inf-active").checked = true;
    await loadInfluencers();
  } catch (e) {
    // 400 del Worker: validación o regla de lanzamiento, con el
    // motivo tal cual. No se crea nada.
    setFieldError("inf-name-error", e.message);
    $("inf-name").setAttribute("aria-invalid", "true");
    showMsg(e.message, "err");
  } finally { setLoading(btn, false); }
}
const SOURCE_STATUS = {
  pendiente: ["Pendiente", "neutral"], pending: ["Pendiente", "neutral"],
  revisando: ["Revisando", "violet"], revisando_vod: ["Revisando", "violet"], processing: ["Revisando", "violet"],
  procesada: ["Procesada", "ok"], procesado: ["Procesada", "ok"], completada: ["Procesada", "ok"], done: ["Procesada", "ok"],
  error: ["Con error", "bad"], fallida: ["Con error", "bad"], failed: ["Con error", "bad"],
};
function sourceStatusPill(status) {
  const m = SOURCE_STATUS[String(status || "").toLowerCase()];
  if (m) return `<span class="pill ${m[1]}">${m[0]}</span>`;
  return `<span class="pill neutral">${esc(status || "Pendiente")}</span>`;
}
function sourceCardHtml(s) {
  const key = String(s.platform || "").toLowerCase();
  const label = PLATFORM_LABEL[key] || s.platform || key;
  const title = s.title || s.url || ("Fuente " + s.id);
  const progress = typeof s.progress === "number" ? Math.max(0, Math.min(100, s.progress)) : null;
  return `<article class="card source-card" id="src-card-${s.id}">
    <div class="account-head">
      <span class="platform-mark" aria-hidden="true"><span class="dot ${PLATFORM_DOT[key] || ""}"></span></span>
      <div class="account-title">
        <h3>${esc(title)}</h3>
        ${s.url && s.title ? `<p class="account-sub">${esc(s.url)}</p>` : ""}
      </div>
      ${sourceStatusPill(s.status)}
    </div>
    <p class="dim">${esc(label)}${s.streamer ? " · " + esc(s.streamer) : ""}${s.game ? " · " + esc(s.game) : ""}</p>
    ${progress !== null ? `<div class="priority-bar-wrap"><div class="priority-bar-label"><span class="lbl">Progreso</span><span class="val">${progress} %</span></div><div class="priority-bar" role="img" aria-label="Progreso: ${progress} %"><div class="priority-fill" style="width:${progress}%"></div></div></div>` : ""}
    <p class="dim">Actualizada ${fmtDate(s.updated_at)}</p>
    <div class="source-detail" id="src-detail-${s.id}" hidden></div>
    <div class="row">
      <button type="button" class="ghost" data-src-detail="${s.id}" aria-expanded="false">Ver detalle</button>
    </div>
  </article>`;
}
async function loadFuentes() {
  const list = $("sourceList");
  const empty = $("sourceEmpty");
  const checkEl = $("vodCheckEmpty");
  if (list) { list.setAttribute("aria-busy", "true"); list.innerHTML = skeletons(2, 120); }
  try {
    const data = await api("/api/sources");
    const sources = data.sources || [];
    const last = data.last_vod_check || null;
    if (checkEl) {
      if (!last) {
        checkEl.textContent = "Sin revisiones todavía.";
      } else {
        checkEl.innerHTML = `<b>Fecha:</b> ${esc(fmtDate(last.ran_at))}<br><b>Resumen:</b> ${esc(last.summary || "—")}<br><b>VODs encontrados:</b> ${last.findings === null || last.findings === undefined ? "—" : esc(String(last.findings))}`;
      }
    }
    if (list) {
      list.setAttribute("aria-busy", "false");
      if (!sources.length) {
        list.innerHTML = `<div class="empty-state" id="sourceEmpty"><h3>Sin fuentes todavía</h3><p class="dim">Aún no hay fuentes procesadas. Cuando el Worker tenga endpoint, aquí verás cada VOD con su estado del pipeline.</p></div>`;
      } else {
        list.innerHTML = sources.map(sourceCardHtml).join("");
        list.querySelectorAll("[data-src-detail]").forEach((b) => b.addEventListener("click", async () => {
          const id = b.dataset.srcDetail;
          const box = $("src-detail-" + id);
          const open = box.hidden;
          if (!open) { box.hidden = true; b.setAttribute("aria-expanded", "false"); b.textContent = "Ver detalle"; return; }
          box.hidden = false;
          b.setAttribute("aria-expanded", "true");
          b.textContent = "Ocultar detalle";
          box.innerHTML = `<p class="dim">Cargando detalle…</p>`;
          try {
            const d = await api("/api/sources/" + id);
            const s = d.source;
            box.innerHTML = `<dl class="channel-rows">
              <div><dt>URL</dt><dd>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url)}</a>` : "—"}</dd></div>
              <div><dt>Estado</dt><dd>${esc(s.status || "pendiente")}</dd></div>
              <div><dt>Nota</dt><dd>${esc(s.status_note || "—")}</dd></div>
              <div><dt>Juego</dt><dd>${esc(s.game || "—")}</dd></div>
              <div><dt>Influencer</dt><dd>${s.influencer_id === null || s.influencer_id === undefined ? "—" : esc(String(s.influencer_id))}</dd></div>
              <div><dt>Creada</dt><dd>${fmtDate(s.created_at)}</dd></div>
              <div><dt>Actualizada</dt><dd>${fmtDate(s.updated_at)}</dd></div>
            </dl>`;
          } catch (e) {
            box.innerHTML = `<p class="dim">No se pudo cargar el detalle: ${esc(e.message)}</p>`;
            showMsg(e.message, "err");
          }
        }));
      }
    }
    if (empty && sources.length) empty.hidden = true;
  } catch (e) {
    if (list) {
      list.setAttribute("aria-busy", "false");
      list.innerHTML = `<p><b>No se pudieron cargar las fuentes.</b></p><p class="dim">${esc(e.message)}</p><button type="button" id="retryFuentes">Reintentar</button>`;
      $("retryFuentes").addEventListener("click", loadFuentes);
    }
    showMsg(e.message, "err");
  }
}

// --- Editor ---
let editorClip = null;
let editorVideoObjectUrl = null;
let editorVideoHasMp4 = false;
const parseJsonObj = (v) => {
  if (!v) return null;
  if (typeof v === "object") return v;
  try { const p = JSON.parse(v); return (p && typeof p === "object") ? p : null; } catch { return null; }
};
const parseJsonArr = (v) => { const p = parseJsonObj(v); return Array.isArray(p) ? p : []; };
const fmtClock = (s) => {
  if (s === null || s === undefined || isNaN(s)) return "—";
  const t = Math.max(0, Math.floor(Number(s)));
  return Math.floor(t / 60) + ":" + String(t % 60).padStart(2, "0");
};
function durationBand(dur) {
  if (dur === null || dur === undefined || isNaN(Number(dur)) || Number(dur) <= 0) return ["neutral", "Sin duración", "Sin duración guardada en el clip."];
  const d = Number(dur);
  if (d >= 40 && d <= 60) return ["ok", "En rango", "Dentro del objetivo: mínimo 40 s, objetivo 40 s, tope 60 s."];
  if (d >= 25 && d <= 35) return ["bad", "Prohibido", "Regla de oro: nunca 30 segundos. El rango 25–35 s está prohibido."];
  // Fuera de 40–60 s el Worker frena la publicación (publishGate):
  // ya no existe la franja 15–90 s.
  return ["bad", "El Worker lo frena", "Fuera de 40–60 s: el Worker frena la publicación por duración."];
}
function stopEditorVideo() {
  const v = $("edVideo");
  if (v) { try { v.pause(); } catch {} v.removeAttribute("src"); try { v.load(); } catch {} }
  if (editorVideoObjectUrl) { URL.revokeObjectURL(editorVideoObjectUrl); editorVideoObjectUrl = null; }
  editorVideoHasMp4 = false;
}
function setEditorControlsEnabled(on) {
  $("edPlayPause").disabled = !on;
  $("edMute").disabled = !on;
}
function renderEditorBrakes(blocked) {
  const box = $("edBrakes");
  if (!blocked || !blocked.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<h3>Frenos</h3>` + blocked.map((b) => `<div class="brake-chip">${esc(b)}</div>`).join("");
}
function renderEditor(c) {
  editorClip = c;
  selectedClipTitle = clipTitleOf(c);
  selectedClipPlatforms = parsePlatforms(c.platforms).map((p) => PLATFORM_LABEL[String(p).toLowerCase()] || String(p));
  $("edTitle").textContent = selectedClipTitle;
  const seen = Number(c.preview_seen) === 1;
  const pill = $("edPreviewPill");
  pill.className = "pill " + (seen ? "ok" : "warn");
  pill.textContent = seen ? "Preview vista" : "Preview no vista";
  const dur = (typeof c.end_sec === "number" && typeof c.start_sec === "number") ? c.end_sec - c.start_sec : null;
  const band = durationBand(dur);
  $("edDurationValue").textContent = (dur === null || dur <= 0) ? "—" : dur + " s";
  const dp = $("edDurationPill");
  dp.className = "pill " + band[0];
  dp.textContent = band[1];
  $("edDurationNote").textContent = band[2];
  const caps = parseJsonArr(c.captions);
  const capTexts = caps.map((l) => (typeof l === "string" ? l : (l && (l.text || l.caption)) || "")).filter(Boolean);
  // Regla 2026-11-17 22:10: SIEMPRE subtítulos completos, sin ellos no se publica.
  const subsBlocked = Number(c.publish_blocked) === 1;
  // Rescate por gritos (2026-11-18): el clip entró bloqueado por
  // viralidad < 50, no por subtítulos; el freno se nombra como es.
  const rescue = (parseJsonObj(c.viral) || {}).rescue || null;
  $("edSubtitleBand").textContent = rescue
    ? `Rescatado por gritos (viralidad ${rescue.viral_revisado}): bloqueado hasta que lo revises.`
    : (subsBlocked ? "Sin subtítulos completos: necesita transcripción humana." : (capTexts.length ? capTexts.join(" ") : "Sin subtítulos guardados"));
  const style = String(c.subtitle_style || "");
  $("edSubtitleStyle").innerHTML =
    (style ? `<span class="pill violet">${esc(style)}</span>` : `<span class="dim">Sin estilo guardado</span>`) +
    (capTexts.length ? `<ul>${capTexts.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : `<p class="dim">Sin subtítulos guardados.</p>`) +
    `<p class="dim">Layout: ${esc(c.layout_mode || "automático")}</p>`;
  document.querySelectorAll("#edStyleOptions button").forEach((b) => b.classList.toggle("active", b.dataset.edStyle === style));
  const viral = parseJsonObj(c.viral);
  const overrides = parseJsonObj(c.seo_overrides);
  const states = parseStates(c);
  const plats = parsePlatforms(c.platforms);
  const ordered = PLATFORMS_ORDER.filter((p) => plats.map((x) => String(x).toLowerCase()).includes(p)).map((p) => plats.find((x) => String(x).toLowerCase() === p));
  plats.forEach((p) => { if (!ordered.includes(p)) ordered.push(p); });
  $("edSeoList").innerHTML = ordered.length ? ordered.map((p) => {
    const key = String(p).toLowerCase();
    const label = PLATFORM_LABEL[key] || String(p);
    const vScore = seoScoreFor(c, key);
    const hasOverride = overrides ? (Array.isArray(overrides) ? overrides.map(String).includes(key) : Object.prototype.hasOwnProperty.call(overrides, key)) : false;
    const st = states[p] || states[key];
    const raw = st ? (st.state || st) : "pending";
    return `<div class="ed-seo-card"><h3><span class="dot ${PLATFORM_DOT[key] || ""}" aria-hidden="true"></span>${esc(label)}</h3>
      <p><b>${esc(c.seo_title || c.title || "Sin título SEO")}</b></p>
      ${c.seo_description ? `<p>${esc(c.seo_description)}</p>` : `<p class="dim">Sin descripción</p>`}
      ${c.seo_tags ? `<p class="dim">Tags: ${esc(c.seo_tags)}</p>` : `<p class="dim">Sin tags</p>`}
      <div class="pill-row">${vScore === null ? `<span class="pill neutral">SEO sin medir</span>` : `<span class="pill neutral">SEO: ${vScore}</span>`}${hasOverride ? `<span class="pill sunset">Override SEO</span>` : ""}${platStatePill(raw)}</div>
      ${vScore !== null && vScore >= 70 && vScore < 90 ? `<button type="button" class="ghost" data-seo-override="${esc(key)}" data-seo-enabled="${hasOverride ? "false" : "true"}">${hasOverride ? "Quitar override SEO" : "Activar override SEO"}</button><p class="dim">Override solo con SEO 70–89: anula la puerta de 90 en esta plataforma.</p>` : ""}</div>`;
  }).join("") : `<p class="dim">Sin plataformas asignadas</p>`;
  $("edSeoList").querySelectorAll("[data-seo-override]").forEach((b) => b.addEventListener("click", async () => {
    const platform = b.dataset.seoOverride;
    const enabled = b.dataset.seoEnabled === "true";
    b.disabled = true;
    try {
      await patchEditorClip({ seo_override: { platform, enabled } });
      showMsg(enabled ? "Override SEO activado." : "Override SEO quitado.", "info");
    } catch (e) { showMsg(e.message, "err"); }
  }));
  // SEO y puntuación (IA): insignia, desglose, prioridad y miniatura.
  const aiBadge = $("edAiBadge");
  if (aiBadge) {
    const kind = (parseViral(c).score_kind || c.score_kind) === "ia";
    aiBadge.innerHTML = kind ? `<span class="pill violet">IA · Rellenado automáticamente por IA (Gemini/brain)</span>` : `<span class="pill neutral">Sin datos de IA todavía</span>`;
  }
  const bdBox = $("edScoreBreakdown");
  if (bdBox) {
    const rows = breakdownData(c);
    bdBox.innerHTML = rows.length ? rows.map((b) => `<div class="bd-row"><span>${esc(b.label)}</span><span>${esc(String(b.value))}</span></div>`).join("") : `<p class="dim">Sin puntuación medida todavía.</p>`;
  }
  const priBox = $("edPriorityValue");
  if (priBox) {
    const pri = viralNum(c, "priority");
    priBox.textContent = pri === null ? "sin medir" : String(pri);
  }
  const thumbInput = $("edThumbText");
  if (thumbInput) thumbInput.value = c.thumb_text || "";
  const markBtn = $("edMarkPreview");
  markBtn.disabled = seen;
  // Preview opcional (v3.1): la casilla sigue disponible, pero solo
  // bloquea si require_preview está activado en Ajustes.
  const requirePreview = !!(lastSettings && lastSettings.require_preview === true);
  $("edMarkPreviewNote").textContent = seen
    ? "Preview visto en el Editor. Ya puedes publicar."
    : (requirePreview
      ? "Pulsa el botón cuando hayas visto el preview en este Editor."
      : "Opcional: pulsa el botón cuando hayas visto el preview en este Editor (no bloquea la publicación).");
  const pub = $("edPublish");
  pub.disabled = (requirePreview && !seen) || subsBlocked;
  const subsComplete = Number(c.subtitles_complete) === 1;
  $("edPublishNote").textContent = (rescue && subsBlocked)
    ? (subsComplete
      ? `Publicar está bloqueado: clip rescatado (viralidad ${rescue.viral_revisado} < 50). Revísalo y desbloquéalo abajo para poder publicarlo.`
      : `Publicar está bloqueado: clip rescatado (viralidad ${rescue.viral_revisado} < 50) y sin subtítulos completos: necesita transcripción humana antes de desbloquearlo.`)
    : (subsBlocked ? "Publicar está bloqueado: sin subtítulos completos, necesita transcripción humana." : (seen ? "Preview vista: puedes publicar." : (requirePreview ? "Publicar está bloqueado: falta ver el preview en el Editor." : "Preview no vista: no hace falta verlo para publicar (la exigencia está desactivada en Ajustes).")));
  // Desbloqueo del rescate: solo el usuario, tras revisar el clip
  // en el Editor, quita el freno (PATCH publish_blocked=0). Solo
  // aparece si los subtítulos están completos: un rescatado sin voz
  // sigue bloqueado por subtítulos, no se puede colar por aquí.
  const unblockBtn = $("edUnblockRescue");
  if (unblockBtn) {
    unblockBtn.classList.toggle("hidden", !(rescue && subsBlocked && subsComplete));
    unblockBtn.onclick = async () => {
      unblockBtn.disabled = true;
      try {
        await patchEditorClip({ publish_blocked: 0 });
        showMsg("Rescatado desbloqueado: ya puedes publicarlo.", "info");
      } catch (e) { showMsg(e.message, "err"); }
      unblockBtn.disabled = false;
    };
  }
  const durInput = $("edDurationInput");
  if (durInput) durInput.value = (dur === null || dur <= 0) ? "" : String(dur);
  const seoTitle = $("edSeoTitle");
  if (seoTitle) seoTitle.value = c.seo_title || "";
  const seoDesc = $("edSeoDescription");
  if (seoDesc) seoDesc.value = c.seo_description || "";
  const seoTags = $("edSeoTags");
  if (seoTags) seoTags.value = c.seo_tags || "";
  // Categoría GTA VI (gate 2026-11-18): este panel es solo GTA VI.
  const catVal = $("edCategoryValue");
  if (catVal) {
    const g = c.game || "";
    catVal.innerHTML = g ? `<span class="pill ${g === "GTA VI" ? "ok" : "bad"}">${esc(g)}</span>` : `<span class="pill bad">Sin categoría</span>`;
  }
  const catInput = $("edCategoryInput");
  if (catInput) catInput.value = c.game || "";
  document.querySelectorAll("#edStyleOptions button").forEach((b) => { b.disabled = false; });
  renderEditorBrakes([]);
}
async function patchEditorClip(body) {
  if (!selectedClipId || !editorClip) return null;
  const r = await api("/api/clips/" + selectedClipId, { method: "PATCH", body: JSON.stringify(body) });
  renderEditor(r.clip);
  if (r.preview_reset) {
    // La duración cambió: el preview guardado deja de valer.
    // (El servidor manda la nota exacta según require_preview.)
    showMsg(r.preview_note || "La duración cambió: el preview guardado deja de valer.", "info");
  }
  return r;
}
async function markPreviewSeen() {
  if (!selectedClipId || !editorClip) return;
  const btn = $("edMarkPreview");
  if (btn.disabled) return;
  setLoading(btn, true, "Marcando…");
  try {
    const r = await api("/api/clips/" + selectedClipId + "/preview-seen", { method: "POST" });
    renderEditor(r.clip);
    showMsg(r.already_seen ? "El preview ya estaba marcado como visto." : "Preview marcado como visto. Ya puedes publicar.", "ok");
  } catch (e) { showMsg(e.message, "err"); }
  finally {
    setLoading(btn, false);
    // setLoading re-habilita el botón; si el preview ya quedó visto,
    // el botón vuelve a deshabilitado (lo decide renderEditor).
    if (editorClip && Number(editorClip.preview_seen) === 1) btn.disabled = true;
  }
}
async function saveEditorDuration() {
  const input = $("edDurationInput");
  const btn = $("edSaveDuration");
  if (!input || !btn) return;
  const n = Number(input.value);
  if (!Number.isInteger(n) || n <= 0) {
    setFieldError("edDuration-error", "Escribe la duración en segundos (entero mayor que 0).");
    input.setAttribute("aria-invalid", "true");
    input.focus();
    return;
  }
  setFieldError("edDuration-error", "");
  input.removeAttribute("aria-invalid");
  setLoading(btn, true, "Guardando…");
  try {
    const r = await patchEditorClip({ duration: n });
    if (r && !r.preview_reset) showMsg("Duración guardada: " + n + " s.", "ok");
  } catch (e) {
    // 400 del Worker con el motivo de la regla de oro (25–35 s
    // prohibido, mínimo 40 s, tope 60 s): se muestra tal cual.
    showMsg(e.message, "err");
  } finally { setLoading(btn, false); }
}
async function saveEditorSeo() {
  const btn = $("edSaveSeo");
  if (!btn) return;
  setLoading(btn, true, "Guardando…");
  try {
    await patchEditorClip({
      seo_title: $("edSeoTitle").value,
      seo_description: $("edSeoDescription").value,
      seo_tags: $("edSeoTags").value,
      thumbnail_text: $("edThumbText").value,
    });
    showMsg("Título y SEO guardados.", "ok");
  } catch (e) { showMsg(e.message, "err"); }
  finally { setLoading(btn, false); }
}
async function saveEditorCategory() {
  const btn = $("edSaveCategory");
  const input = $("edCategoryInput");
  if (!input || !btn) return;
  const v = input.value.trim();
  if (!v) { showMsg("Escribe la categoría (solo GTA VI).", "err"); input.focus(); return; }
  setLoading(btn, true, "Guardando…");
  try {
    await patchEditorClip({ game: v });
    showMsg("Categoría guardada: " + v + ".", "ok");
  } catch (e) { showMsg(e.message, "err"); }
  finally { setLoading(btn, false); }
}
async function saveEditorStyle(style) {
  try {
    await patchEditorClip({ subtitle_style: style });
    showMsg("Estilo de subtítulos guardado: " + style + ".", "ok");
  } catch (e) { showMsg(e.message, "err"); }
}
async function loadEditorVideo(id) {
  const video = $("edVideo");
  const noVideo = $("edNoVideo");
  stopEditorVideo();
  video.hidden = true;
  noVideo.classList.remove("hidden");
  setEditorControlsEnabled(false);
  $("edPlayPause").textContent = "Reproducir";
  $("edMute").textContent = "Silencio";
  $("edTime").textContent = "— / —";
  let res;
  try {
    res = await fetch(API + "/api/clips/" + id + "/video", { credentials: "include", headers: authHeaders() });
  } catch (e) {
    showMsg("No se pudo cargar el vídeo: " + e.message, "err");
    return;
  }
  if (res.status === 401) { showLogin(); showMsg("Sesión caducada, entra de nuevo.", "err"); return; }
  if (res.status === 404) return;
  if (!res.ok) { showMsg("No se pudo cargar el vídeo: HTTP " + res.status, "err"); return; }
  try {
    const blob = await res.blob();
    editorVideoObjectUrl = URL.createObjectURL(blob);
    video.src = editorVideoObjectUrl;
    video.hidden = false;
    noVideo.classList.add("hidden");
    editorVideoHasMp4 = true;
    setEditorControlsEnabled(true);
  } catch (e) { showMsg("No se pudo cargar el vídeo: " + e.message, "err"); }
}
async function loadEditor() {
  const empty = $("edEmpty");
  const main = $("edMain");
  stopEditorVideo();
  if (!selectedClipId) {
    editorClip = null;
    main.classList.add("hidden");
    empty.classList.remove("hidden");
    $("edRetry").classList.add("hidden");
    return;
  }
  empty.classList.add("hidden");
  main.classList.remove("hidden");
  $("edTitle").textContent = "Cargando clip…";
  try {
    // Preview opcional (v3.1): el botón Publicar del Editor depende
    // de require_preview; si el usuario no abrió Ajustes en esta
    // sesión, lastSettings está vacío y el botón quedaría mal. Se
    // cargan los ajustes (sin tocar el formulario) antes del clip;
    // si falla, manda el default (no exigir) y el gate del servidor
    // decide igual al publicar.
    try {
      const sData = await api("/api/settings");
      lastSettings = sData.settings || {};
    } catch { /* sin ajustes, el default manda */ }
    const data = await api("/api/clips/" + selectedClipId);
    renderEditor(data.clip);
    loadEditorVideo(selectedClipId);
  } catch (e) {
    editorClip = null;
    main.classList.add("hidden");
    empty.classList.remove("hidden");
    $("edRetry").classList.remove("hidden");
    showMsg(e.message, "err");
  }
}
async function publishFromEditor() {
  if (!selectedClipId || !editorClip) return;
  // Sin retorno silencioso por preview no visto (2026-11-19): el
  // botón ya se desactiva solo cuando la exigencia está activa y
  // el preview no se vio; si está habilitado, publicar es válido.
  const names = selectedClipPlatforms.join(", ");
  if (!window.confirm(`¿Publicar este clip?\n\nSe va a publicar «${selectedClipTitle}» en: ${names || "las plataformas del clip"}. Se encolan los trabajos y el Worker los procesa según los límites diarios.`)) return;
  const btn = $("edPublish");
  setLoading(btn, true, "Publicando…");
  try {
    const r = await api("/api/clips/" + selectedClipId + "/publish", { method: "POST" });
    const blocked = r.blocked || [];
    const warnings = r.warnings || [];
    showMsg("Encolado: " + (r.jobs || []).map((j) => j.platform + " (" + j.state + ")").join(", ") + (blocked.length ? " · Frenado: " + blocked.join(" · ") : "") + (warnings.length ? " · Publicado a mano pese al freno: " + warnings.join(" · ") : ""), "info");
    await loadEditor();
    renderEditorBrakes(blocked);
  } catch (e) { showMsg(e.message, "err"); }
  finally { setLoading(btn, false); }
}

// --- Analíticas ---
function setPasteDateDefault() {
  const d = $("anPasteDate");
  if (d && !d.value) {
    try { d.value = new Date().toLocaleDateString("en-CA", { timeZone: "America/Santiago" }); } catch { d.value = new Date().toISOString().slice(0, 10); }
  }
}
const fmtNum = (n) => (typeof n === "number" ? n.toLocaleString("es-CL") : "—");
// Estado de la medición automática (0030): mismo vocabulario que
// /resumen de Telegram. Sin fila, aún no intentada.
function autoStatusText(st) {
  if (!st) return "Medición automática: aún no intentada — se mide sola cada 6 h.";
  const label = { ok: "al día", sin_credencial: "sin credencial", sin_permiso: "sin permiso", error: "error" }[st.status] || st.status;
  if (st.status === "ok") return "Medición automática: al día.";
  const reason = String(st.reason || "").trim().replace(/\s+/g, " ");
  return reason ? `Medición automática: ${label} — ${esc(reason.slice(0, 160))}` : `Medición automática: ${label}.`;
}
function renderAnalyticsSummary(platforms, channels) {
  const totalPublished = platforms.reduce((acc, p) => acc + (p.published_clips || 0), 0);
  platforms.forEach((p) => {
    const key = String(p.platform || "").toLowerCase();
    const card = $("anSummary-" + key);
    if (!card) return;
    const label = PLATFORM_LABEL[key] || p.platform;
    const hasData = p.has_data || (p.measured_clips || 0) > 0;
    // Con canales (0030): cada tarjeta muestra ES y EN por
    // separado, con SU handle real y SU estado de medición
    // automática. Sin canales (respuesta vieja), el agregado.
    const pair = Array.isArray(channels) ? channels.filter((c) => String(c.platform || "").toLowerCase() === key) : [];
    if (pair.length) {
      const channelHtml = pair.map((c) => {
        const lang = c.language === "en" ? "EN" : "ES";
        const handle = (c.handle || "").trim() || "sin handle registrado";
        return `<p><b>${lang} · ${esc(handle)}</b> <span class="dim">— ${c.measured_clips || 0} medidos · ${c.published_clips || 0} publicados</span><br>` +
          `<span class="an-big">${c.median_views === null || c.median_views === undefined ? "—" : fmtNum(c.median_views)}</span> <span class="dim">Mediana de vistas${c.median_views === null || c.median_views === undefined ? " (sin medición)" : ""}</span> · ` +
          `<span class="an-big">${c.median_retention === null || c.median_retention === undefined ? "—" : fmtNum(c.median_retention) + " %"}</span> <span class="dim">Retención</span><br>` +
          `<span class="dim">${autoStatusText(c.auto_status)}</span></p>`;
      }).join("");
      card.innerHTML = `<div class="channel-head"><span class="dot ${PLATFORM_DOT[key] || ""}" aria-hidden="true"></span><h3>${esc(label)}</h3></div>
      <p class="dim">${hasData ? "Con mediciones" : "Sin mediciones todavía"}</p>${channelHtml}`;
      return;
    }
    card.innerHTML = `<div class="channel-head"><span class="dot ${PLATFORM_DOT[key] || ""}" aria-hidden="true"></span><h3>${esc(label)}</h3></div>
      <p class="dim">${hasData ? "Con mediciones" : "Sin mediciones todavía"}</p>
      <p><span class="an-big">${p.median_views === null || p.median_views === undefined ? "—" : fmtNum(p.median_views)}</span> <span class="dim">Mediana de vistas${p.median_views === null || p.median_views === undefined ? " (sin medición)" : ""}</span></p>
      <p><span class="an-big">${p.median_retention === null || p.median_retention === undefined ? "—" : fmtNum(p.median_retention) + " %"}</span> <span class="dim">Mediana de retención${p.median_retention === null || p.median_retention === undefined ? " (sin medición)" : ""}</span></p>
      <p class="dim">${p.measured_clips || 0} clips medidos · ${p.published_clips || 0} publicados</p>
      <p class="dim">${autoStatusText(p.auto_status)}</p>`;
  });
  return totalPublished;
}
function renderAnalyticsRanking(platforms) {
  platforms.forEach((p) => {
    const key = String(p.platform || "").toLowerCase();
    const block = $("anRanking-" + key);
    if (!block) return;
    const label = PLATFORM_LABEL[key] || p.platform;
    const rows = p.ranking || [];
    if (!rows.length) {
      block.innerHTML = `<h3>Ranking — ${esc(label)}</h3><p class="dim">Sin mediciones todavía en ${esc(label)}.</p>`;
      return;
    }
    block.innerHTML = `<h3>Ranking — ${esc(label)}</h3>
      <div class="clip-table-wrap"><table><caption class="visually-hidden">Ranking ${esc(label)}</caption><thead><tr><th scope="col">#</th><th scope="col">Clip</th><th scope="col" class="num">Vistas</th><th scope="col" class="num">Retención</th><th scope="col">Guardado</th></tr></thead><tbody>
      ${rows.map((r, i) => `<tr><td class="num">${i + 1}</td><td><b>${esc(r.title || ("Clip " + r.clip_id))}</b>${r.language ? ` <span class="dim">· ${r.language === "en" ? "EN" : "ES"}</span>` : ""}${(r.shares !== null && r.shares !== undefined) || (r.saves !== null && r.saves !== undefined) || (r.comments !== null && r.comments !== undefined) || (r.new_subs !== null && r.new_subs !== undefined) ? `<br><span class="dim">${r.shares ?? "—"} compartidos · ${r.saves ?? "—"} guardados · ${r.comments ?? "—"} comentarios · ${r.new_subs ?? "—"} seguidores nuevos</span>` : ""}</td><td class="num">${fmtNum(r.views)}</td><td class="num">${r.retention_pct === null || r.retention_pct === undefined ? "—" : fmtNum(r.retention_pct) + " %"}</td><td>${fmtDate(r.recorded_at)}</td></tr>`).join("")}
      </tbody></table></div>`;
  });
}
function renderAnalyticsUnmeasured(unmeasured, totalPublished) {
  const box = $("anUnmeasured");
  if (!box) return;
  const head = `<h3>Sin medición</h3><p class="dim">Estos clips ya están publicados pero la medición automática aún no trae sus vistas ni su retención. Si la automática falla en una plataforma, registra la cifra abajo como respaldo.</p>`;
  if (!totalPublished) {
    box.innerHTML = head + `<p class="dim">Sin clips publicados todavía.</p>`;
    return;
  }
  if (!unmeasured.length) {
    box.innerHTML = head + `<p class="dim">Todos los clips publicados tienen medición.</p>`;
    return;
  }
  box.innerHTML = head + unmeasured.map((u) => {
    const key = String(u.platform || "").toLowerCase();
    return `<div class="user-row"><div class="user-text"><p class="user-name">${esc(u.title || ("Clip " + u.clip_id))}</p><p class="dim user-meta">${esc(PLATFORM_LABEL[key] || u.platform)}${u.language ? ` · ${u.language === "en" ? "EN" : "ES"}` : ""}</p></div><span class="pill neutral">Sin medición</span></div>`;
  }).join("");
}
async function loadAnaliticas() {
  setPasteDateDefault();
  const sel = $("anPasteClip");
  const form = $("anPasteForm");
  const fields = form.querySelectorAll("input, select");
  const empty = $("anEmpty");
  // Clips para el selector del formulario (sin clips, todo deshabilitado).
  let clips = [];
  try {
    const data = await api("/api/clips");
    clips = data.clips || [];
  } catch (e) {
    sel.innerHTML = `<option value="">Sin clips</option>`;
    fields.forEach((f) => { f.disabled = true; });
    $("anPasteSave").disabled = true;
    showMsg(e.message, "err");
    return;
  }
  if (!clips.length) {
    sel.innerHTML = `<option value="">Sin clips</option>`;
    fields.forEach((f) => { f.disabled = true; });
    $("anPasteSave").disabled = true;
  } else {
    sel.innerHTML = clips.map((c) => `<option value="${c.id}">${esc(clipTitleOf(c))}</option>`).join("");
    fields.forEach((f) => { f.disabled = false; });
    $("anPasteSave").disabled = false;
  }
  // Resumen real: medianas, ranking y publicados sin medición.
  try {
    const an = await api("/api/analytics");
    const platforms = an.platforms || [];
    const unmeasured = an.unmeasured || [];
    const totalPublished = renderAnalyticsSummary(platforms, an.channels || []);
    renderAnalyticsRanking(platforms);
    renderAnalyticsUnmeasured(unmeasured, totalPublished);
    const anyData = platforms.some((p) => p.has_data) || unmeasured.length > 0;
    if (empty) empty.hidden = anyData;
  } catch (e) {
    if (empty) empty.hidden = false;
    showMsg(e.message, "err");
  }
}
function statOptInt(input, label) {
  const v = input.value.trim();
  if (v === "") return null;
  const n = Number(v);
  if (!Number.isInteger(n) || n < 0) throw new Error(label + " debe ser un número entero mayor o igual a 0.");
  return n;
}
async function saveStat(ev) {
  if (ev) ev.preventDefault();
  const btn = $("anPasteSave");
  if (btn.disabled) return;
  setFieldError("anPasteClip-error", "");
  setFieldError("anPasteViews-error", "");
  setFieldError("anPasteRetention-error", "");
  const clipId = Number($("anPasteClip").value);
  if (!clipId) {
    setFieldError("anPasteClip-error", "Elige un clip primero.");
    $("anPasteClip").focus();
    return;
  }
  let body;
  try {
    const viewsRaw = $("anPasteViews").value.trim();
    if (viewsRaw === "" || !Number.isInteger(Number(viewsRaw)) || Number(viewsRaw) < 0) {
      setFieldError("anPasteViews-error", "Escribe las vistas.");
      $("anPasteViews").setAttribute("aria-invalid", "true");
      $("anPasteViews").focus();
      return;
    }
    body = { platform: $("anPastePlatform").value, views: Number(viewsRaw), window: $("anPasteWindow").value };
    const retentionRaw = $("anPasteRetention").value.trim();
    if (retentionRaw !== "") {
      const r = Number(retentionRaw);
      if (!Number.isFinite(r) || r < 0 || r > 100) {
        setFieldError("anPasteRetention-error", "La retención va de 0 a 100 %.");
        $("anPasteRetention").setAttribute("aria-invalid", "true");
        $("anPasteRetention").focus();
        return;
      }
      body.retention = r;
    }
    const shares = statOptInt($("anPasteShares"), "Compartidos");
    if (shares !== null) body.shares = shares;
    const saves = statOptInt($("anPasteSaves"), "Guardados");
    if (saves !== null) body.saves = saves;
    const comments = statOptInt($("anPasteComments"), "Comentarios");
    if (comments !== null) body.comments = comments;
    const subs = statOptInt($("anPasteSubs"), "Nuevos seguidores");
    if (subs !== null) body.subscribers = subs;
    const dateRaw = $("anPasteDate").value.trim();
    if (dateRaw) {
      const ms = Date.parse(dateRaw);
      if (Number.isFinite(ms)) body.recorded_at = ms;
    }
  } catch (e) {
    showMsg(e.message, "err");
    return;
  }
  setLoading(btn, true, "Guardando…");
  try {
    await api("/api/clips/" + clipId + "/stats", { method: "POST", body: JSON.stringify(body) });
    showMsg("Medición guardada.", "ok");
    $("anPasteForm").reset();
    setPasteDateDefault();
    await loadAnaliticas();
  } catch (e) {
    // 400 del Worker con el motivo (vistas, retención, plataforma o
    // ventana): se muestra tal cual, sin guardar nada.
    showMsg(e.message, "err");
  } finally { setLoading(btn, false); }
}

// --- Canales ---
// DOS COLAS (0019, 2026-11-18): un canal por PLATAFORMA + IDIOMA
// (8 entradas). La API devuelve `language` por canal; sin él, "es".
const CHANNEL_DEFS = [
  { key: "youtube|es", platform: "youtube", language: "es", label: "YouTube · Español", dot: "youtube" },
  { key: "youtube|en", platform: "youtube", language: "en", label: "YouTube · Inglés", dot: "youtube" },
  { key: "tiktok|es", platform: "tiktok", language: "es", label: "TikTok · Español", dot: "tiktok" },
  { key: "tiktok|en", platform: "tiktok", language: "en", label: "TikTok · Inglés", dot: "tiktok" },
  { key: "instagram|es", platform: "instagram", language: "es", label: "Instagram · Español", dot: "instagram" },
  { key: "instagram|en", platform: "instagram", language: "en", label: "Instagram · Inglés", dot: "instagram" },
  { key: "facebook|es", platform: "facebook", language: "es", label: "Facebook · Español", dot: "facebook" },
  { key: "facebook|en", platform: "facebook", language: "en", label: "Facebook · Inglés", dot: "facebook" },
];
const CHANNEL_ACCOUNT_STATE = {
  conectada: ["Conectada", "ok"],
  sin_credencial: ["Sin credencial", "bad"],
  no_conectada: ["No conectada", "warn"],
  sin_crear: ["Sin crear", "neutral"],
};
function channelAccountHtml(ch) {
  const st = CHANNEL_ACCOUNT_STATE[ch.account_status] || [ch.account_status || "Sin datos", "neutral"];
  if (ch.account_status === "sin_crear" || !ch.account) {
    return `<span class="pill ${st[1]}">${st[0]}</span> <span class="dim">Sin cuenta API todavía. Se crea al migrar los datos del panel anterior o desde el Worker.</span>`;
  }
  const acc = ch.account;
  return `<span class="pill ${st[1]}">${st[0]}</span> <span class="dim">${esc(acc.verify_message || "")}${acc.verified_at ? " · verificado: " + fmtDate(acc.verified_at) : ""}${acc.label ? " · " + esc(acc.label) : ""}</span>`;
}
const fmtAnyDate = (v) => {
  if (v === null || v === undefined || v === "") return "—";
  const d = new Date(v);
  if (isNaN(d.getTime())) return "—";
  try { return d.toLocaleString("es-CL", { timeZone: "America/Santiago" }); } catch { return String(v); }
};
let channelsLoading = false;
async function loadChannels() {
  if (channelsLoading) return;
  channelsLoading = true;
  const summary = $("channelSummary");
  const list = $("channelList");
  const btn = $("btnRefreshChannels");
  summary.setAttribute("aria-busy", "true");
  list.setAttribute("aria-busy", "true");
  summary.innerHTML = skeletons(1, 60);
  list.innerHTML = skeletons(4, 180);
  setLoading(btn, true, "Actualizando…");
  let health = null, worker = null, channels = [];
  let healthErr = null, workerErr = null, chErr = null;
  try { health = await api("/api/health"); } catch (e) { healthErr = e; }
  try { worker = await api("/api/worker/state"); } catch (e) { workerErr = e; }
  try { const d = await api("/api/channels"); channels = d.channels || []; } catch (e) { chErr = e; }
  summary.setAttribute("aria-busy", "false");
  list.setAttribute("aria-busy", "false");
  setLoading(btn, false);
  channelsLoading = false;
  if (healthErr && workerErr && chErr) {
    summary.innerHTML = `<p><b>No se pudo cargar la salud de los canales</b></p><p class="dim">${esc(healthErr.message)}</p>`;
    list.innerHTML = `<button type="button" id="retryChannels">Reintentar</button>`;
    $("retryChannels").addEventListener("click", loadChannels);
    $("channelUpdatedAt").textContent = "Última comprobación: sin datos";
    return;
  }
  const running = (health && typeof health.worker_running === "boolean") ? health.worker_running : (health && health.worker_running ? Object.values(health.worker_running).some(Boolean) : (worker ? !!worker.running : false));
  const pillCls = healthErr ? "bad" : (running ? "ok" : "warn");
  const pillTxt = healthErr ? "API CAÍDA" : (running ? "EN MARCHA" : "PARADO");
  const line = healthErr ? "API no responde" : `API conectada · Worker: ${running ? "en marcha" : "parado"}`;
  summary.innerHTML = `<div class="worker-status-line"><span class="dot lg ${healthErr ? "bad" : (running ? "ok" : "warn")}" aria-hidden="true"></span><span class="pill ${pillCls}">${pillTxt}</span></div><p class="dim">${esc(line)}</p>`;
  $("channelUpdatedAt").textContent = health && health.time ? "Actualizado: " + fmtAnyDate(health.time) : "Última comprobación: sin datos";
  if (chErr) {
    list.innerHTML = `<p><b>No se pudieron cargar los canales.</b></p><p class="dim">${esc(chErr.message)}</p><button type="button" id="retryChannels">Reintentar</button>`;
    $("retryChannels").addEventListener("click", loadChannels);
    return;
  }
  const channelKey = (ch) => `${String(ch.platform || "").toLowerCase()}|${String(ch.language || "es").toLowerCase()}`;
  const byKey = {};
  CHANNEL_DEFS.forEach((c) => { byKey[c.key] = c; });
  const ordered = [...channels];
  CHANNEL_DEFS.forEach((c) => { if (!ordered.some((ch) => channelKey(ch) === c.key)) ordered.push({ platform: c.platform, language: c.language }); });
  list.innerHTML = ordered.map((ch) => {
    const key = channelKey(ch);
    const domKey = key.replace("|", "-");
    const def = byKey[key] || { key, label: `${ch.platform || key} · ${langName(ch.language || "es")}`, dot: "" };
    const hasData = ch.daily_cap !== undefined;
    return `<article class="card channel-card" data-channel="${esc(key)}" aria-labelledby="channel-title-${esc(domKey)}">
      <div class="channel-head"><span class="dot ${def.dot}" aria-hidden="true"></span><h3 id="channel-title-${esc(domKey)}">${esc(def.label)}</h3><span class="pill neutral">Sin datos</span></div>
      <dl class="channel-rows">
        <div><dt>Cuenta API</dt><dd>${hasData ? channelAccountHtml(ch) : `<span class="dim">No se pudieron cargar las cuentas API.</span>`}</dd></div>
        <div><dt>Cuota de hoy</dt><dd>${hasData ? `Usada hoy: ${ch.used_today} · Restante: ${ch.remaining_today} · Tope: ${ch.daily_cap} al día (por idioma: ES y EN tienen su propio tope)` : "Usada hoy: sin datos · Restante: sin datos · Tope: sin datos"}</dd></div>
        <div><dt>Publicados</dt><dd>${hasData ? `Total: ${ch.published_total} · Últimos 7 días: ${ch.published_last_7d}` : "Total: sin datos · Últimos 7 días: sin datos"}</dd></div>
        <div><dt>Ventana de publicación</dt><dd>${hasData ? `${esc(ch.publish_window)} (hora de Chile) · Espaciamiento mínimo: ${ch.min_spacing_hours} h entre subidas · Rolling 24 h: máx ${ch.rolling_24h_max ?? 3} por cuenta` : "Franjas Chile 12:00–14:00, 18:00–20:00, 21:00–23:00 · Espaciamiento mínimo: 3 h (YouTube 4 h) entre subidas · Rolling 24 h: máx 3 por cuenta"}</dd></div>
      </dl>
      <p class="dim channel-note">${hasData ? esc(ch.health_note || "Sin datos de salud para este canal. El Worker aún no expone la salud por plataforma.") : "Sin datos de salud para este canal. Cuando el Worker exponga la salud por plataforma, aparecerá aquí la última comprobación, los errores y la última publicación."}</p>
      <div class="row"><button type="button" class="ghost" data-goto-cuentas>Ir a Cuentas</button></div>
    </article>`;
  }).join("");
  list.querySelectorAll("[data-goto-cuentas]").forEach((b) => b.addEventListener("click", () => switchTab("cuentas")));
}

// --- Tracking ---
const TRACKING_STEPS = [
  { key: "detectado", label: "Detectado" },
  { key: "pico", label: "Pico detectado" },
  { key: "admision", label: "Admisión a la cola" },
  { key: "cola_corte", label: "En cola de corte" },
  { key: "cortando", label: "Cortando" },
  { key: "subtitulos", label: "Subtítulos" },
  { key: "paquete", label: "Paquete viral y scores" },
  { key: "revision", label: "Listo para revisión" },
  { key: "decision", label: "Aprobado / rechazado" },
  { key: "cola_publicacion", label: "En cola de publicación" },
  { key: "publicado", label: "Publicado / fallido" },
];
let trackingClips = [];
let trackingFilter = "all";
let trackingSelectedId = null;
function trackingStepIndex(e) {
  const s = String(e.step || "");
  if (s === "publicado") {
    const m = String(e.message || "");
    if (m.startsWith("Publicación frenada")) return 9;
    return 10;
  }
  return TRACKING_STEPS.findIndex((st) => st.key === s);
}
function trackingOriginOf(c) {
  return String(c.origin || "").toLowerCase() === "live" ? "live" : "vod";
}
function renderTrackingOptions() {
  const sel = $("trackingClipSelect");
  const filtered = trackingClips.filter((c) => trackingFilter === "all" || trackingOriginOf(c) === trackingFilter);
  if (!filtered.length) {
    sel.innerHTML = `<option value="">Sin clips de este origen</option>`;
    sel.disabled = true;
    trackingSelectedId = null;
    return;
  }
  sel.disabled = false;
  sel.innerHTML = filtered.map((c) => `<option value="${c.id}">${esc(clipTitleOf(c))}</option>`).join("");
  if (!filtered.some((c) => c.id === trackingSelectedId)) trackingSelectedId = filtered[0].id;
  sel.value = String(trackingSelectedId);
}
function renderTrackingEmpty() {
  const hasClips = trackingClips.length > 0;
  $("trackingClipTitle").textContent = hasClips ? "Sin clips de este origen" : "Sin clips para seguir";
  $("trackingClipOrigin").innerHTML = "";
  $("trackingClipStatus").innerHTML = "";
  $("trackingProgress").textContent = hasClips ? "Elige un clip para ver su seguimiento." : "";
  $("trackingTimeline").innerHTML = hasClips ? "" : `<li class="empty-state"><b>Sin clips para seguir</b><br><span class="dim">Los clips aparecen aquí cuando el cerebro los detecta o cuando se migran del panel anterior.</span><br><button type="button" class="ghost" id="trackingGoWorker">Ir al Worker</button></li>`;
  $("trackingOtherEvents").classList.add("hidden");
  const go = $("trackingGoWorker");
  if (go) go.addEventListener("click", () => switchTab("worker"));
}
async function loadTracking() {
  const sel = $("trackingClipSelect");
  sel.setAttribute("aria-busy", "true");
  try {
    const data = await api("/api/clips");
    trackingClips = data.clips || [];
    sel.setAttribute("aria-busy", "false");
    renderTrackingOptions();
    if (trackingSelectedId) await loadTrackingTimeline(trackingSelectedId);
    else renderTrackingEmpty();
  } catch (e) {
    sel.setAttribute("aria-busy", "false");
    showMsg(e.message, "err");
    renderTrackingEmpty();
  }
}
async function loadTrackingTimeline(id) {
  trackingSelectedId = id;
  const tl = $("trackingTimeline");
  tl.setAttribute("aria-busy", "true");
  tl.innerHTML = skeletons(3, 44);
  $("trackingOtherEvents").classList.add("hidden");
  try {
    const data = await api("/api/clips/" + id);
    const c = data.clip;
    const tr = await api("/api/clips/" + id + "/tracking");
    const events = tr.events || [];
    tl.setAttribute("aria-busy", "false");
    $("trackingClipTitle").textContent = clipTitleOf(c);
    const origin = String(c.origin || "").toLowerCase();
    $("trackingClipOrigin").innerHTML = origin === "live" ? `<span class="pill sunset">LIVE</span>` : origin === "vod" ? `<span class="pill violet">VOD</span>` : "";
    $("trackingClipStatus").innerHTML = statusPill(c.status);
    const byStep = TRACKING_STEPS.map(() => []);
    const others = [];
    events.forEach((e) => {
      const i = trackingStepIndex(e);
      if (i >= 0) byStep[i].push(e); else others.push(e);
    });
    let lastStepWithData = -1;
    for (let i = events.length - 1; i >= 0; i--) { const s = trackingStepIndex(events[i]); if (s >= 0) { lastStepWithData = s; break; } }
    const lastEventMapped = events.length ? trackingStepIndex(events[events.length - 1]) >= 0 : false;
    const terminal = ["published", "failed"].includes(String(c.status || "").toLowerCase());
    let withRecord = 0;
    tl.innerHTML = TRACKING_STEPS.map((st, i) => {
      const evs = byStep[i];
      if (!evs.length) {
        return `<li class="tracking-step"><span class="dot" aria-hidden="true"></span><span class="tracking-step-num">${i + 1}</span><div><p><b>${esc(st.label)}</b> <span class="pill neutral">Sin registro</span></p><p class="dim">Sin hora</p><p class="dim">Sin registro todavía.</p></div></li>`;
      }
      withRecord++;
      const last = evs[evs.length - 1];
      let pillCls = "ok", pillTxt = "Completado";
      if (last.failed) { pillCls = "bad"; pillTxt = "Fallido"; }
      else if (i === lastStepWithData && !terminal) { pillCls = "warn"; pillTxt = "En curso"; }
      return `<li class="tracking-step"><span class="dot ${pillCls}" aria-hidden="true"></span><span class="tracking-step-num">${i + 1}</span><div><p><b>${esc(st.label)}</b> <span class="pill ${pillCls}">${pillTxt}</span></p><p class="dim"><time>${fmtDate(last.at)}</time>${last.actor ? " · " + esc(last.actor) : ""}</p><p class="tl-msg">${esc(last.message)}</p>${(i === lastStepWithData && lastEventMapped) ? `<p class="dim">Último paso</p>` : ""}</div></li>`;
    }).join("");
    $("trackingProgress").textContent = withRecord === 0 ? "Sin seguimiento todavía." : `Paso ${withRecord} de 11 con registro`;
    if (others.length) {
      $("trackingOtherEvents").classList.remove("hidden");
      $("trackingOtherList").innerHTML = others.map((e, i) => {
        const dt = e.at ? new Date(Number(e.at)).toISOString() : "";
        return `<li><span class="dot ${e.failed ? "bad" : "ok"}" aria-hidden="true"></span><time${dt ? ` datetime="${dt}"` : ""}>${fmtDate(e.at)}</time><span class="tl-kind">${esc(e.step)}</span>${e.failed ? ' <span class="pill bad">Fallo</span>' : ""}<br><span class="tl-msg">${e.actor ? esc(e.actor) + " — " : ""}${esc(e.message)}</span>${(i === others.length - 1 && !lastEventMapped) ? '<br><span class="dim">Último paso</span>' : ""}</li>`;
      }).join("");
    }
    $("trackingClipTitle").focus({ preventScroll: true });
  } catch (e) {
    tl.setAttribute("aria-busy", "false");
    tl.innerHTML = `<li><b>No se pudo cargar el seguimiento</b><br><span class="dim">${esc(e.message)}</span><br><button type="button" id="retryTracking">Reintentar</button></li>`;
    $("retryTracking").addEventListener("click", () => loadTrackingTimeline(id));
    showMsg(e.message, "err");
  }
}

// --- Usuarios ---
async function loadPanelUsers() {
  const list = $("panelUserList");
  list.setAttribute("aria-busy", "true");
  list.innerHTML = skeletons(2, 56);
  try {
    const data = await api("/api/panel-users");
    const users = data.users || [];
    list.setAttribute("aria-busy", "false");
    if (!users.length) {
      list.innerHTML = `<div class="empty-state"><h3>Sin gestión de usuarios todavía</h3><p class="dim">Este panel aún no puede listar los usuarios. La creación y la desactivación se hacen fuera del panel, en el Worker.</p><button type="button" class="ghost" id="retryPanelUsers">Actualizar</button></div>`;
      $("retryPanelUsers").addEventListener("click", loadPanelUsers);
      return;
    }
    // Solo username + estado + marca de sesión actual. El tenant
    // interno, el rol, las fechas y cualquier nombre real no se
    // muestran nunca.
    list.innerHTML = users.map((u) => {
      const st = String(u.status || "").toLowerCase();
      const active = st === "activo" || st === "active" || st === "1" || st === "enabled";
      const initials = String(u.username || "").slice(0, 2).toUpperCase();
      const isMe = me && me.sub && String(u.username) === String(me.sub);
      return `<div class="user-row">
        <span class="user-avatar" aria-hidden="true">${esc(initials)}</span>
        <div class="user-text">
          <p class="user-name">${esc(u.username)}</p>
          ${isMe ? `<p class="dim user-meta">Sesión actual</p>` : ""}
        </div>
        <span class="pill ${active ? "ok" : "neutral"}">${active ? "Activo" : esc(u.status || "Sin estado")}</span>
      </div>`;
    }).join("");
  } catch (e) {
    list.setAttribute("aria-busy", "false");
    list.innerHTML = `<p><b>No se pudieron cargar los usuarios.</b></p><p class="dim">${esc(e.message)}</p><button type="button" id="retryPanelUsers">Reintentar</button>`;
    $("retryPanelUsers").addEventListener("click", loadPanelUsers);
    showMsg(e.message, "err");
  }
}

// --- Cuentas ---
// ÚNICA FUENTE de providers de Cuentas > APIs (2026-11-21, bug kaez:
// Twitch no aparecía en ningún sitio porque cada lista era una copia
// hardcodeada distinta — grupos, formulario de añadir, placeholders).
// El Worker acepta exactamente estos 5 en POST /api/accounts
// (API_PLATFORMS en worker/src/index.ts) y twitch/gemini en
// /api/onboarding/credentials; si se añade un provider, se añade
// AQUÍ y en ese allowlist, y todo lo demás (grupos, placeholders,
// selector de añadir) se deriva de esta lista.
const API_PROVIDERS = [
  { id: "youtube", label: "YouTube" },
  { id: "tiktok", label: "TikTok" },
  { id: "meta", label: "Meta" },
  { id: "twitch", label: "Twitch" },
  { id: "gemini", label: "Gemini" },
];
const API_GROUP_ORDER = API_PROVIDERS.map((p) => p.id);
const API_GROUP_LABEL = Object.fromEntries(API_PROVIDERS.map((p) => [p.id, p.label]));
const CRED_HINT = {
  youtube: "JSON del token OAuth (una línea: token, refresh_token, client_id, client_secret) o API key (empieza por AIza).",
  tiktok: "API key de Zernio (empieza por zk_). La cuenta TikTok se publica por Zernio.",
  meta: "Token LARGO de la Página (el corto vence en 1 hora). Si el panel no ve la cuenta de Instagram aunque en Meta diga conectada, el token es anterior a la vinculación: re-vincula Instagram a la Página y genera el token DESPUÉS.",
  instagram: "Token LARGO de la Página (el corto vence en 1 hora). Si el panel no ve la cuenta de Instagram aunque en Meta diga conectada, el token es anterior a la vinculación: re-vincula Instagram a la Página y genera el token DESPUÉS.",
  facebook: "Token LARGO de la Página (el corto vence en 1 hora). Si el panel no ve la cuenta de Instagram aunque en Meta diga conectada, el token es anterior a la vinculación: re-vincula Instagram a la Página y genera el token DESPUÉS.",
  twitch: "Client Secret de Twitch (el Client ID va en Extra, en JSON). Se crea en dev.twitch.tv/console/apps.",
  gemini: "API key de Gemini (aistudio.google.com/apikey).",
};
const EXTRA_HINT = {
  youtube: "Opcional: JSON OAuth {\"client_id\":\"…\",\"client_secret\":\"…\",\"refresh_token\":\"…\"} (si la credencial no es el JSON del token).",
  tiktok: "Obligatorio para Zernio: {\"via\":\"zernio\",\"account_id\":\"<id de la cuenta TikTok en Zernio>\"}.",
  meta: "Obligatorio para Meta: IDs de Instagram y de Página, en JSON. Guárdalo JUNTO con el token en la misma operación: guardar solo el token borra el Extra.",
  instagram: "Obligatorio para Meta: IDs de Instagram y de Página, en JSON. Guárdalo JUNTO con el token en la misma operación: guardar solo el token borra el Extra.",
  facebook: "Obligatorio para Meta: IDs de Instagram y de Página, en JSON. Guárdalo JUNTO con el token en la misma operación: guardar solo el token borra el Extra.",
  twitch: "Obligatorio para Twitch: {\"client_id\":\"<Client ID de tu app en dev.twitch.tv>\"}.",
  gemini: "Sin Extra: solo la API key.",
};
function apiGroupOf(a) {
  const p = String(a.platform || "").toLowerCase();
  if (p === "meta" || p === "instagram" || p === "facebook") return "meta";
  if (p === "youtube") return "youtube";
  if (p === "tiktok") return "tiktok";
  return p || "meta";
}
function apiAccountCardHtml(a) {
  const st = accountState(a);
  const platKey = String(a.platform || "").toLowerCase();
  const oauthGroup = apiGroupOf(a);
  const oauthProvider = oauthGroup === "meta" ? "meta" : oauthGroup === "youtube" ? "google" : "";
  const oauthLabel = oauthProvider === "meta" ? "Conectar con Meta" : oauthProvider === "google" ? "Conectar con Google" : "";
  const dotCls = PLATFORM_DOT[platKey] || "";
  const legacy = String(a.credential_masked || "").includes("legacy");
  return `<article class="card account-card" aria-labelledby="acc-title-${a.id}">
    <div class="account-head">
      <span class="platform-mark" aria-hidden="true"><span class="dot ${dotCls}"></span></span>
      <div class="account-title">
        <h3 id="acc-title-${a.id}">${esc(a.label || a.platform)}</h3>
        <p class="account-sub">${esc(a.platform)}${a.language ? " · " + esc(langName(a.language)) + " (" + esc(String(a.language).toLowerCase()) + ")" : ""}</p>
      </div>
      <span class="pill ${st.pill}">${st.label}</span>
    </div>
    <p class="account-status">${accountStatusLine(a)}</p>
    ${a.maturity ? `<p class="dim">Escalado: ${esc(a.maturity.status || "nueva")} · ${typeof a.maturity.days_since_first_published === "number" ? a.maturity.days_since_first_published + " días desde la primera publicación" : "sin publicaciones aún"} · ${a.maturity.published_count ?? 0} publicaciones · ${a.maturity.strikes ?? 0} strikes · tope vigente ${a.maturity.effective_daily_limit ?? "–"}/día</p>` : ""}
    ${legacy ? `<p class="dim">Vuelve a pegarla para cifrarla.</p>` : ""}
    ${a.note ? `<p class="dim">${esc(a.note)}</p>` : ""}
    <p class="field-error" id="cred-${a.id}-error" role="alert" hidden></p>
    <label for="cred-${a.id}">Credencial (se cifra al guardar, nunca se muestra)</label>
    <div class="input-with-toggle">
      <input id="cred-${a.id}" type="password" autocomplete="current-password" placeholder="Pega aquí el token / API key" aria-describedby="cred-${a.id}-hint cred-${a.id}-error">
      <button type="button" class="ghost input-toggle" data-toggle-cred="${a.id}" aria-label="Mostrar credencial" aria-pressed="false">Mostrar</button>
    </div>
    <p class="field-hint" id="cred-${a.id}-hint">Se cifra en el Worker al guardar. Pegar no muestra el valor guardado.</p>
    <p class="field-hint">${esc(CRED_HINT[platKey] || "")}</p>
    <label for="extra-${a.id}">Extra (opcional)</label>
    <input id="extra-${a.id}" placeholder='${platKey === "tiktok" ? "{\"via\":\"zernio\",\"account_id\":\"…\"}" : "{\"ig_user_id\":\"…\",\"page_id\":\"…\"}"}' aria-describedby="extra-${a.id}-hint extra-${a.id}-error" spellcheck="false">
    <p class="field-hint" id="extra-${a.id}-hint">${esc(EXTRA_HINT[platKey] || "Solo para Meta/Instagram: IDs de Instagram y de Página, en JSON.")}</p>
    <p class="field-error" id="extra-${a.id}-error" role="alert" hidden></p>
    <div class="row account-actions">
      ${oauthProvider ? `<button type="button" data-oauth="${oauthProvider}" data-account="${a.id}">${oauthLabel}</button>` : ""}
      <button type="button" data-save="${a.id}">Guardar credencial</button>
      <button type="button" data-verify="${a.id}" class="ghost">Verificar ahora</button>
      <button type="button" class="danger" data-delete="${a.id}" disabled>Eliminar</button>
    </div>
  </article>`;
}
function apiPlaceholderHtml(platform, language) {
  const label = API_GROUP_LABEL[platform] || platform;
  return `<article class="card account-card api-placeholder" id="api-placeholder-${platform}-${language}">
    <div class="account-head">
      <span class="platform-mark" aria-hidden="true"><span class="dot ${PLATFORM_DOT[platform] || ""}"></span></span>
      <div class="account-title"><h3>${esc(label)} · ${esc(langName(language))} (${esc(language)})</h3></div>
      <span class="pill neutral">Sin crear</span>
    </div>
    <p class="dim">Esta cuenta aún no existe. Créala y después pega su credencial abajo.</p>
    <div class="row account-actions">
      <button type="button" data-create-api="${esc(platform)}" data-create-lang="${esc(language)}">Crear cuenta</button>
    </div>
  </article>`;
}
function renderApiGroups(accs) {
  const groups = {};
  accs.forEach((a) => {
    const g = apiGroupOf(a);
    (groups[g] = groups[g] || []).push(a);
  });
  const langRank = (l) => (String(l).toLowerCase() === "es" ? 0 : String(l).toLowerCase() === "en" ? 1 : 2);
  Object.values(groups).forEach((arr) => arr.sort((x, y) => langRank(x.language) - langRank(y.language)));
  const order = [...API_GROUP_ORDER, ...Object.keys(groups).filter((g) => !API_GROUP_ORDER.includes(g))];
  // Solo YouTube/TikTok/Meta se publican por idioma (es/en). Twitch y
  // Gemini son una sola cuenta por tenant (la crea el onboarding en
  // "es"): su placeholder es único, no por idioma.
  const expected = [["youtube", "es"], ["youtube", "en"], ["tiktok", "es"], ["tiktok", "en"], ["meta", "es"], ["meta", "en"]];
  const singleExpected = ["twitch", "gemini"];
  // 2026-11-21 (bug kaez): se renderizan TODOS los grupos de
  // API_PROVIDERS aunque no tengan cuentas — antes un provider sin
  // fila (Twitch) no aparecía en ningún sitio y no había dónde
  // guardarlo. El placeholder trae el botón Crear cuenta (funciona).
  return order.map((g) => {
    const label = API_GROUP_LABEL[g] || g;
    const cards = (groups[g] || []).map(apiAccountCardHtml).join("");
    const have = new Set((groups[g] || []).map((a) => String(a.language || "").toLowerCase()));
    const placeholders = expected.filter(([p, lang]) => p === g && !have.has(lang)).map(([, lang]) => apiPlaceholderHtml(g, lang)).join("")
      + (singleExpected.includes(g) && !have.size ? apiPlaceholderHtml(g, "es") : "");
    return `<section class="api-group" id="api-group-${g}"><h3>${esc(label)}</h3><div class="api-cards">${cards}${placeholders}</div></section>`;
  }).join("");
}
function accountState(a) {
  if (a.verify_ok === 1) return { pill: "ok", label: "Conectada" };
  if (!a.has_credential) return { pill: "bad", label: "Sin credencial" };
  return { pill: "warn", label: "No conectada" };
}
function accountStatusLine(a) {
  const st = accountState(a);
  if (st.label === "Conectada") {
    return `${esc(a.verify_message || "conectada")}${a.verified_at ? " · verificado: " + fmtDate(a.verified_at) : ""} · credencial: ${a.has_credential ? "guardada (" + esc(a.credential_masked || "") + ")" : "no guardada"}`;
  }
  if (st.label === "Sin credencial") {
    return "Sin credencial guardada. Pega el token o la API key abajo y pulsa Guardar credencial.";
  }
  const base = a.verify_message
    ? esc(a.verify_message)
    : "Credencial guardada, pero la última verificación falló o aún no se ha verificado. Pulsa Verificar ahora.";
  return `${base}${a.verified_at ? " · verificado: " + fmtDate(a.verified_at) : ""} · credencial: guardada (${esc(a.credential_masked || "")})`;
}
async function loadAccounts() {
  const list = $("accountList");
  list.setAttribute("aria-busy", "true");
  list.innerHTML = skeletons(2, 180);
  try {
    const data = await api("/api/accounts");
    const accs = data.accounts || [];
    list.setAttribute("aria-busy", "false");
    if (!accs.length) {
      list.innerHTML = `<div class="empty-state"><h3>Sin cuentas todavía</h3><p class="dim">Crea una con el formulario de arriba, o al migrar los datos del panel anterior. Cuando haya una, aparecerá aquí para pegar su credencial y verificarla.</p><button type="button" class="ghost" id="retryAccounts">Actualizar</button></div>` +
        API_PROVIDERS.map((p) => `<section class="api-group" id="api-group-${p.id}"><h3>${esc(p.label)}</h3></section>`).join("");
      $("retryAccounts").addEventListener("click", loadAccounts);
      return;
    }
    list.innerHTML = renderApiGroups(accs);
    document.querySelectorAll("[data-create-api]").forEach((b) => b.addEventListener("click", async () => {
      setLoading(b, true, "Creando…");
      try {
        await api("/api/accounts", { method: "POST", body: JSON.stringify({ platform: b.dataset.createApi, language: b.dataset.createLang || "es", label: "" }) });
        showMsg("Cuenta creada. Ahora pega su credencial y verifícala.", "info");
        await loadAccounts();
      } catch (e) { showMsg(e.message, "err"); }
      finally { setLoading(b, false); }
    }));
    document.querySelectorAll("[data-toggle-cred]").forEach((b) => b.addEventListener("click", () => {
      const input = $("cred-" + b.dataset.toggleCred);
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      b.textContent = show ? "Ocultar" : "Mostrar";
      b.setAttribute("aria-pressed", show ? "true" : "false");
      b.setAttribute("aria-label", show ? "Ocultar credencial" : "Mostrar credencial");
    }));
    document.querySelectorAll("[data-save]").forEach((b) => b.addEventListener("click", async () => {
      const id = b.dataset.save;
      const credInput = $("cred-" + id);
      const extraInput = $("extra-" + id);
      setFieldError("cred-" + id + "-error", "");
      setFieldError("extra-" + id + "-error", "");
      credInput.removeAttribute("aria-invalid");
      if (!credInput.value.trim()) {
        setFieldError("cred-" + id + "-error", "Pega la credencial primero.");
        credInput.setAttribute("aria-invalid", "true");
        credInput.focus();
        return;
      }
      if (extraInput.value.trim()) {
        try { JSON.parse(extraInput.value); }
        catch (e) {
          setFieldError("extra-" + id + "-error", "El campo Extra no es JSON válido. Revisa las comillas y las llaves.");
          extraInput.setAttribute("aria-invalid", "true");
          extraInput.focus();
          return;
        }
      }
      setLoading(b, true, "Guardando…");
      try {
        const r = await api("/api/accounts/" + id + "/credential", { method: "POST", body: JSON.stringify({ credential: credInput.value, extra: extraInput.value }) });
        showMsg("Credencial guardada (" + (r.masked || "") + "). Ahora pulsa Verificar.", "info");
        await loadAccounts();
      } catch (e) {
        setFieldError("cred-" + id + "-error", e.message);
        credInput.setAttribute("aria-invalid", "true");
        showMsg(e.message, "err");
      } finally { setLoading(b, false); }
    }));
    document.querySelectorAll("[data-oauth]").forEach((b) => b.addEventListener("click", () => connectOAuth(b.dataset.oauth, b.dataset.account)));
    document.querySelectorAll("[data-verify]").forEach((b) => b.addEventListener("click", async () => {
      const id = b.dataset.verify;
      setLoading(b, true, "Verificando…");
      try {
        const r = await api("/api/accounts/" + id + "/verify", { method: "POST" });
        showMsg("Verificación: " + (r.ok ? "conectada" : "error") + " — " + (r.message || ""), r.ok ? "info" : "err");
        if (!r.ok) setFieldError("cred-" + id + "-error", r.message || "La verificación falló.");
        await loadAccounts();
      } catch (e) {
        if (/sin credencial/i.test(e.message)) {
          setFieldError("cred-" + id + "-error", "Sin credencial guardada. Pégala primero y pulsa Guardar credencial.");
          $("cred-" + id).focus();
        } else {
          setFieldError("cred-" + id + "-error", e.message);
        }
        showMsg(e.message, "err");
      } finally { setLoading(b, false); }
    }));
  } catch (e) {
    list.setAttribute("aria-busy", "false");
    list.innerHTML = `<p><b>No se pudieron cargar las cuentas.</b></p><p class="dim">${esc(e.message)}</p><button type="button" id="retryAccounts">Reintentar</button>`;
    $("retryAccounts").addEventListener("click", loadAccounts);
    showMsg(e.message, "err");
  }
}

// --- Avisos ---
async function loadNotifications() {
  const list = $("notifList");
  list.setAttribute("aria-busy", "true");
  list.innerHTML = skeletons(3, 56);
  try {
    const data = await api("/api/notifications");
    const items = data.notifications || [];
    const unread = items.filter((n) => !n.read_at).length;
    const badge = $("unreadBadge");
    if (unread === 0) { badge.hidden = true; badge.textContent = ""; }
    else { badge.hidden = false; badge.textContent = unread > 9 ? "9+" : String(unread); }
    $("tabbtn-avisos").setAttribute("aria-label", unread ? `Avisos, ${unread} sin leer` : "Avisos");
    $("notifCount").textContent = unread === 0 ? "Todo al día. Sin avisos sin leer." : unread === 1 ? "1 aviso sin leer" : `${unread} avisos sin leer`;
    list.setAttribute("aria-busy", "false");
    if (!items.length) {
      list.innerHTML = `<div class="empty-state"><h3>Sin avisos</h3><p class="dim">Todo al día. Los avisos de publicaciones, fallos y credenciales aparecerán aquí.</p><button type="button" class="ghost" id="retryNotifs">Actualizar</button></div>`;
      $("retryNotifs").addEventListener("click", loadNotifications);
      return;
    }
    list.innerHTML = items.map((n) =>
      `<div class="notif-row ${n.read_at ? "notif-read" : "notif-unread"}">
        <div class="notif-text">${n.read_at ? "" : '<span class="dot" aria-hidden="true"></span>'}<div><p class="notif-message">${esc(n.message)}</p><p class="notif-meta">${fmtDate(n.created_at)}${n.kind ? " · " + esc(n.kind) : ""}</p></div></div>
        ${n.read_at ? "" : `<button type="button" class="ghost notif-read-btn" data-read="${n.id}">Marcar leída</button>`}
      </div>`).join("");
    document.querySelectorAll("[data-read]").forEach((b) => b.addEventListener("click", async () => {
      setLoading(b, true, "Marcando…");
      try {
        await api("/api/notifications/" + b.dataset.read + "/read", { method: "POST" });
        await loadNotifications();
        const next = document.querySelector(".notif-read-btn");
        if (next) next.focus(); else $("notifHeading").setAttribute("tabindex", "-1"), $("notifHeading").focus();
      } catch (e) {
        setLoading(b, false);
        showMsg("No se pudo marcar el aviso como leído. Reintenta.", "err");
      }
    }));
  } catch (e) {
    list.setAttribute("aria-busy", "false");
    list.innerHTML = `<p><b>No se pudieron cargar los avisos.</b></p><p class="dim">${esc(e.message)}</p><button type="button" id="retryNotifs">Reintentar</button>`;
    $("retryNotifs").addEventListener("click", loadNotifications);
    showMsg(e.message, "err");
  }
}

// --- Ajustes ---
const LIMIT_IDS = ["set-limit-youtube", "set-limit-tiktok", "set-limit-instagram", "set-limit-facebook"];
const LIMIT_KEYS = { "set-limit-youtube": "daily_limit_youtube_shorts", "set-limit-tiktok": "daily_limit_tiktok", "set-limit-instagram": "daily_limit_instagram", "set-limit-facebook": "daily_limit_facebook" };
function timeValue(v) {
  if (typeof v !== "string") return "";
  if (v === "24:00") return "";
  return /^\d{2}:\d{2}$/.test(v) ? v : "";
}
function fillSettingsForm(s) {
  const en = s.subtitles_enabled;
  $("set-subtitles-enabled").value = en === true ? "true" : en === false ? "false" : "";
  $("set-subtitle-style").value = ["ref", "simple", "tiktok", "highlight", "karaoke"].includes(s.subtitle_style) ? (s.subtitle_style === "simple" ? "ref" : s.subtitle_style) : "";
  $("set-layout").value = ["rdjavi", "plain", "split"].includes(s.layout) ? (s.layout === "split" ? "plain" : s.layout) : "";
  $("set-min-duration").value = (typeof s.min_duration_s === "number") ? String(s.min_duration_s) : "";
  $("set-max-duration").value = (typeof s.max_duration_s === "number") ? String(s.max_duration_s) : "";
  $("set-min-virality").value = (typeof s.min_virality === "number") ? String(s.min_virality) : "";
  $("set-min-seo").value = (typeof s.min_seo === "number") ? String(s.min_seo) : "";
  $("set-game").value = "GTA VI";
  $("set-thumbnail-style").value = ["gta6", "gameplay", "webcam"].includes(s.thumbnail_style) ? s.thumbnail_style : "";
  LIMIT_IDS.forEach((id) => {
    const k = LIMIT_KEYS[id];
    $("" + id).value = (typeof s[k] === "number") ? String(s[k]) : "";
  });
  $("set-plan-youtube").value = (typeof s.youtube_plan === "number") ? String(s.youtube_plan) : "";
  $("set-plan-tiktok").value = (typeof s.tiktok_plan === "number") ? String(s.tiktok_plan) : "";
  $("set-plan-instagram").value = (typeof s.instagram_plan === "number") ? String(s.instagram_plan) : "";
  $("set-plan-facebook").value = (typeof s.facebook_plan === "number") ? String(s.facebook_plan) : "";
  $("set-plan-resto").value = (typeof s.resto_plan === "number") ? String(s.resto_plan) : "";
  $("set-new-account").value = (typeof s.new_account_daily === "number") ? String(s.new_account_daily) : "";
  $("set-new-account-youtube").value = (typeof s.new_account_daily_youtube === "number") ? String(s.new_account_daily_youtube) : "";
  $("set-spacing").value = (typeof s.spacing_hours === "number") ? String(s.spacing_hours) : "";
  $("set-spacing-youtube").value = (typeof s.youtube_spacing_hours === "number") ? String(s.youtube_spacing_hours) : "";
  $("set-zernio-daily").value = (typeof s.tiktok_zernio_daily === "number") ? String(s.tiktok_zernio_daily) : "";
  $("set-zernio-drafts").value = (typeof s.tiktok_zernio_pending_drafts === "number") ? String(s.tiktok_zernio_pending_drafts) : "";
  const hb = s.hype_burst;
  $("set-hype-burst").value = hb === true ? "true" : hb === false ? "false" : "";
  const rp = s.require_preview;
  $("set-require-preview").value = rp === true ? "true" : rp === false ? "false" : "";
  const apt = s.auto_publish_top;
  $("set-auto-publish-top").value = apt === true ? "true" : apt === false ? "false" : "";
  const wmEn = s.watermark_enabled;
  $("set-watermark-enabled").value = wmEn === true ? "true" : wmEn === false ? "false" : "";
  $("set-watermark-opacity").value = (typeof s.watermark_opacity === "number") ? String(s.watermark_opacity) : "";
  $("set-watermark-size").value = (typeof s.watermark_size_px === "number") ? String(s.watermark_size_px) : "";
  $("set-watermark-position").value = ["center", "top-left", "top-right", "bottom-left", "bottom-right"].includes(s.watermark_position) ? s.watermark_position : "";
  watermarkActiveId = (typeof s.watermark_logo_id === "number") ? s.watermark_logo_id : null;
  LIMIT_IDS.forEach((id) => {
    const k = LIMIT_KEYS[id];
    $("" + id).value = (typeof s[k] === "number") ? String(s[k]) : "";
  });
  // La franja horaria ya no se edita: son las 3 ventanas fijas
  // (12:00–14:00, 18:00–20:00, 21:00–23:00, hora Chile).
  // Telegram v2: el token y el chat ya no se pegan aquí. El vínculo
  // es por código (ver loadTelegramLink); lo viejo (telegram_bot_token
  // / telegram_chat_id en settings) queda ignorado por el Worker.
}
function formToSettings() {
  const out = { ...lastSettings };
  const en = $("set-subtitles-enabled").value;
  if (en !== "") out.subtitles_enabled = en === "true";
  const style = $("set-subtitle-style").value;
  if (style !== "") out.subtitle_style = style;
  const layout = $("set-layout").value;
  if (layout !== "") out.layout = layout;
  const numField = (id, key) => { const v = $(id).value.trim(); if (v !== "") out[key] = Number(v); };
  numField("set-min-duration", "min_duration_s");
  numField("set-max-duration", "max_duration_s");
  numField("set-min-virality", "min_virality");
  numField("set-min-seo", "min_seo");
  const thumb = $("set-thumbnail-style").value;
  if (thumb !== "") out.thumbnail_style = thumb;
  LIMIT_IDS.forEach((id) => {
    const v = $(id).value.trim();
    if (v !== "") out[LIMIT_KEYS[id]] = Number(v);
  });
  numField("set-plan-youtube", "youtube_plan");
  numField("set-plan-tiktok", "tiktok_plan");
  numField("set-plan-instagram", "instagram_plan");
  numField("set-plan-facebook", "facebook_plan");
  numField("set-plan-resto", "resto_plan");
  numField("set-new-account", "new_account_daily");
  numField("set-new-account-youtube", "new_account_daily_youtube");
  numField("set-spacing", "spacing_hours");
  numField("set-spacing-youtube", "youtube_spacing_hours");
  numField("set-zernio-daily", "tiktok_zernio_daily");
  numField("set-zernio-drafts", "tiktok_zernio_pending_drafts");
  const hb = $("set-hype-burst").value;
  if (hb !== "") out.hype_burst = hb === "true";
  const rp = $("set-require-preview").value;
  if (rp !== "") out.require_preview = rp === "true";
  const apt = $("set-auto-publish-top").value;
  if (apt !== "") out.auto_publish_top = apt === "true";
  const wmEn = $("set-watermark-enabled").value;
  if (wmEn !== "") out.watermark_enabled = wmEn === "true";
  numField("set-watermark-opacity", "watermark_opacity");
  numField("set-watermark-size", "watermark_size_px");
  const wmPos = $("set-watermark-position").value;
  if (wmPos !== "") out.watermark_position = wmPos;
  out.watermark_logo_id = watermarkActiveId;
  return out;
}
function markSettingsDirty() {
  settingsDirty = true;
  $("settingsDirty").hidden = false;
}
// --- Telegram v2: vinculación por código (multi-tenant) ---
// El bot nunca pide la contraseña: el código se genera aquí, con
// sesión iniciada, y se confirma en el bot con /link <código>.
let telegramWired = false;
async function loadTelegramLink() {
  const statusEl = $("telegramLinkStatus");
  const codeBox = $("telegramLinkCodeBox");
  const unlinkBtn = $("btnTelegramUnlink");
  if (!statusEl) return;
  setFieldError("set-telegram-error", "");
  try {
    const data = await api("/api/telegram/status");
    if (!data.configured) {
      statusEl.textContent = "Bot sin configurar todavía (falta el token en el Worker).";
    } else if (data.linked) {
      statusEl.textContent = "Vinculado a este chat de Telegram" + (data.chat_id_masked ? " (" + data.chat_id_masked + ")" : "") + ". El resumen diario llega a las 09:00 (Santiago). En el bot puedes pedir /resumen, /cola, /top, /estado y /ayuda, y ordenar la Cola con /primero, /subir, /bajar y /auto.";
    } else {
      statusEl.textContent = "Sin vincular: genera un código y escríbelo en el bot.";
    }
    if (codeBox) codeBox.hidden = true;
    if (unlinkBtn) unlinkBtn.hidden = !data.linked;
  } catch (e) {
    statusEl.textContent = "No se pudo comprobar el estado de Telegram: " + e.message;
  }
}
function wireTelegramButtons() {
  if (telegramWired) return;
  telegramWired = true;
  const linkBtn = $("btnTelegramLink");
  if (linkBtn) linkBtn.addEventListener("click", async () => {
    setFieldError("set-telegram-error", "");
    linkBtn.disabled = true;
    try {
      const data = await api("/api/telegram/link-code", { method: "POST", body: JSON.stringify({}) });
      $("telegramLinkCode").textContent = data.code;
      $("telegramLinkCodeBox").hidden = false;
      $("telegramLinkStatus").textContent = "Código generado: vale 10 minutos y solo se puede usar una vez.";
    } catch (e) {
      setFieldError("set-telegram-error", e.message);
    } finally {
      linkBtn.disabled = false;
    }
  });
  const testBtn = $("btnTelegramTest");
  if (testBtn) testBtn.addEventListener("click", async () => {
    setFieldError("set-telegram-error", "");
    testBtn.disabled = true;
    try {
      await api("/api/telegram/test", { method: "POST", body: JSON.stringify({}) });
      showMsg("Prueba enviada a tu Telegram vinculado.", "ok");
      await loadTelegramLink();
    } catch (e) {
      setFieldError("set-telegram-error", e.message);
    } finally {
      testBtn.disabled = false;
    }
  });
  const unlinkBtn = $("btnTelegramUnlink");
  if (unlinkBtn) unlinkBtn.addEventListener("click", async () => {
    if (!window.confirm("¿Desvincular Telegram? Dejarás de recibir el resumen diario hasta que vincules otro chat.")) return;
    setFieldError("set-telegram-error", "");
    try {
      await api("/api/telegram/unlink", { method: "POST", body: JSON.stringify({}) });
      showMsg("Telegram desvinculado.", "ok");
      await loadTelegramLink();
    } catch (e) {
      setFieldError("set-telegram-error", e.message);
    }
  });
}
async function loadSettings() {
  const form = $("settingsForm");
  form.setAttribute("aria-busy", "true");
  try {
    const data = await api("/api/settings");
    lastSettings = data.settings || {};
    $("settingsJson").value = JSON.stringify(lastSettings, null, 2);
    fillSettingsForm(lastSettings);
    settingsJsonTouched = false;
    settingsDirty = false;
    $("settingsDirty").hidden = true;
  } catch (e) {
    showMsg("No se pudieron cargar los ajustes: " + e.message, "err");
  } finally {
    form.setAttribute("aria-busy", "false");
  }
  loadMaturity();
  wireTelegramButtons();
  loadTelegramLink();
  wireWatermarkButtons();
  loadWatermarks();
}

// --- Marca de agua (2026-11-18): logos por tenant ---
// La lista se carga al abrir Ajustes; subir y borrar pegan a
// /api/watermarks con sesión. "Usar esta" solo marca el logo activo
// en memoria y deja el cambio pendiente de Guardar ajustes, como el
// resto del formulario.
let watermarkWired = false;
async function loadWatermarks() {
  const list = $("watermarkList");
  if (!list) return;
  try {
    const data = await api("/api/watermarks");
    const logos = data.logos || [];
    if (!logos.length) {
      list.innerHTML = `<p class="dim">Sin logos todavía. Sube uno en PNG o JPG.</p>`;
      return;
    }
    list.innerHTML = logos.map((l) => {
      const kb = l.size >= 1048576 ? (l.size / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(l.size / 1024)) + " KB";
      return `<div class="watermark-item"><img src="${esc(l.preview_url)}" alt="Logo ${esc(l.filename)}"><div class="user-text"><p class="user-name">${esc(l.filename)}</p><p class="dim user-meta">${esc(kb)}</p></div>${l.is_active ? `<span class="pill ok">Activa</span>` : ""}<button type="button" class="ghost" data-wm-use="${l.id}" ${l.is_active ? "disabled" : ""}>Usar esta</button><button type="button" class="ghost danger" data-wm-del="${l.id}">Eliminar</button></div>`;
    }).join("");
    list.querySelectorAll("[data-wm-use]").forEach((b) => b.addEventListener("click", () => {
      watermarkActiveId = Number(b.getAttribute("data-wm-use"));
      lastSettings.watermark_logo_id = watermarkActiveId;
      markSettingsDirty();
      loadWatermarks();
    }));
    list.querySelectorAll("[data-wm-del]").forEach((b) => b.addEventListener("click", async () => {
      const id = Number(b.getAttribute("data-wm-del"));
      if (!window.confirm("¿Eliminar este logo? Si era el activo, la marca de agua quedará sin logo.")) return;
      setFieldError("watermark-upload-error", "");
      try {
        await api("/api/watermarks/" + id, { method: "DELETE" });
        if (watermarkActiveId === id) { watermarkActiveId = null; lastSettings.watermark_logo_id = null; }
        showMsg("Logo eliminado.", "ok");
        await loadWatermarks();
      } catch (e) {
        setFieldError("watermark-upload-error", e.message);
      }
    }));
  } catch (e) {
    list.innerHTML = `<p><b>No se pudieron cargar los logos.</b></p><p class="dim">${esc(e.message)}</p>`;
  }
}
function wireWatermarkButtons() {
  if (watermarkWired) return;
  watermarkWired = true;
  const btn = $("btnWatermarkUpload");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const input = $("watermarkUpload");
    const files = input ? Array.from(input.files || []) : [];
    setFieldError("watermark-upload-error", "");
    if (!files.length) { setFieldError("watermark-upload-error", "Elige al menos un logo PNG o JPG."); return; }
    const badType = files.find((f) => f.type !== "image/png" && f.type !== "image/jpeg");
    if (badType) { setFieldError("watermark-upload-error", "Solo PNG o JPG: " + badType.name + " no vale."); return; }
    const big = files.find((f) => f.size > 5 * 1024 * 1024);
    if (big) { setFieldError("watermark-upload-error", "El logo supera 5 MB: " + big.name + "."); return; }
    btn.disabled = true;
    try {
      for (const f of files) {
        await apiRaw("/api/watermarks?filename=" + encodeURIComponent(f.name), { method: "POST", headers: { "Content-Type": f.type }, body: f });
      }
      input.value = "";
      showMsg(files.length === 1 ? "Logo subido." : files.length + " logos subidos.", "ok");
      await loadWatermarks();
    } catch (e) {
      setFieldError("watermark-upload-error", e.message);
    } finally {
      btn.disabled = false;
    }
  });
}

// --- Escalado automático por cuenta (0017) ---
// Estado guardado por cuenta: nueva/establecida, días desde la
// primera publicación, publicaciones acumuladas y tope vigente hoy.
async function loadMaturity() {
  const list = $("maturityList");
  if (!list) return;
  list.setAttribute("aria-busy", "true");
  try {
    const data = await api("/api/accounts");
    const accs = data.accounts || [];
    list.setAttribute("aria-busy", "false");
    if (!accs.length) {
      list.innerHTML = `<p class="dim">Sin cuentas todavía. Crea una en Cuentas y aparecerá aquí con su estado.</p>`;
      return;
    }
    list.innerHTML = accs.map((a) => {
      const m = a.maturity || {};
      const st = m.status || "nueva";
      const stage = m.stage || (st === "establecida" ? "establecida" : "semana1");
      const stageLabel = { semana1: "semana 1", semana2: "semana 2", semana3: "semana 3", establecida: "establecida" }[stage] || stage;
      const days = typeof m.days_since_first_published === "number" ? m.days_since_first_published : null;
      const label = (a.label || a.platform || "") + (a.language ? " · " + a.language : "");
      const line = `${label} · ${st} (${stageLabel}) · ${m.effective_daily_limit ?? "–"}/día (plan ${m.effective_daily_plan ?? "–"}, máx ${m.effective_daily_max ?? "–"})`;
      const warmup = st === "establecida" ? "calentamiento completo"
        : `faltan ${m.warmup_remaining_days ?? "–"} días y ${m.warmup_remaining_pubs ?? "–"} publicaciones para establecerse`;
      const rest = a.is_rest_day_today ? ` · HOY es su día libre (${esc(a.rest_day_name || "")})` : (a.rest_day_name ? ` · día libre: ${esc(a.rest_day_name)}` : "");
      const paused = typeof a.paused_until_ms === "number" ? ` · EN PAUSA por spam/límite hasta ${esc(new Date(a.paused_until_ms).toLocaleString("es-CL"))}` : "";
      const detail = `${days === null ? "sin publicaciones aún" : days + (days === 1 ? " día" : " días") + " desde la primera publicación"} · ${m.published_count ?? 0} publicaciones · ${m.strikes ?? 0} strikes · ${warmup}${rest}${paused}`;
      return `<div class="user-row"><div class="user-text"><p class="user-name">${esc(line)}</p><p class="dim user-meta">${esc(detail)}</p></div><span class="pill ${st === "establecida" ? "ok" : "neutral"}">${esc(st)}</span></div>`;
    }).join("");
  } catch (e) {
    list.setAttribute("aria-busy", "false");
    list.innerHTML = `<p><b>No se pudo cargar el estado de las cuentas.</b></p><p class="dim">${esc(e.message)}</p>`;
  }
}
// --- Plan de hoy (reglas 2026-11-19, GET /api/daily-plan) ---
// Qué se publica hoy, en qué cuenta y a qué hora (hora Chile),
// con topes, calentamiento, espaciamiento y franjas ya
// verificados por el Worker. Solo lectura: no encola nada.
async function loadDailyPlan() {
  const box = $("dailyPlan");
  const dateEl = $("dailyPlanDate");
  const btn = $("btnDailyPlan");
  if (!box || !dateEl || !btn) return;
  const fecha = dateEl.value || undefined;
  box.setAttribute("aria-busy", "true");
  box.innerHTML = `<p class="dim">Calculando el plan…</p>`;
  setLoading(btn, true, "Calculando…");
  try {
    const q = fecha ? `?fecha=${encodeURIComponent(fecha)}` : "";
    const d = await api("/api/daily-plan" + q);
    const pubs = d.publicaciones || [];
    const cola = d.en_cola || [];
    const alertas = d.alertas || [];
    box.innerHTML =
      `<p><b>Plan del ${esc(d.fecha || fecha || "")}</b> · ${pubs.length} publicacion${pubs.length === 1 ? "" : "es"} · ${cola.length} clip${cola.length === 1 ? "" : "s"} en cola</p>` +
      (pubs.length
        ? `<div>${pubs.map((p) => `<div class="user-row"><div class="user-text"><p class="user-name">${esc(p.hora_local || "")} · ${esc(p.plataforma || "")} · ${esc(p.cuenta || "")} · clip #${esc(String(p.clip_id ?? ""))}</p><p class="dim user-meta">${esc(p.titulo || "")}${p.hashtags && p.hashtags.length ? ` · ${esc(p.hashtags.map((h) => "#" + h).join(" "))}` : ""}</p><p class="dim user-meta">${esc(p.motivo || "")}</p></div></div>`).join("")}</div>`
        : `<p class="dim">Hoy no se programa nada: revisa las alertas.</p>`) +
      (alertas.length ? `<h3>Alertas</h3><ul>${alertas.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>` : "") +
      (cola.length ? `<p class="dim">En cola: ${cola.map((id) => "#" + esc(String(id))).join(", ")}</p>` : "");
  } catch (e) {
    box.innerHTML = `<p><b>No se pudo calcular el plan.</b></p><p class="dim">${esc(e.message)}</p>`;
  } finally {
    box.setAttribute("aria-busy", "false");
    setLoading(btn, false);
  }
}
function jsonErrorLine(text, err) {
  const m = /position (\d+)/i.exec(err.message || "");
  if (!m) return "";
  const pos = Number(m[1]);
  const line = text.slice(0, pos).split("\n").length;
  return ` Error cerca de la línea ${line}.`;
}
async function saveSettings(ev) {
  if (ev) ev.preventDefault();
  const btn = $("btnSaveSettings");
  if (btn.disabled) return;
  setFieldError("set-limit-error", "");
  setFieldError("set-plan-error", "");
  setFieldError("set-zernio-error", "");
  setFieldError("set-duration-error", "");
  setFieldError("set-threshold-error", "");
  setFieldError("set-telegram-error", "");
  setFieldError("set-watermark-error", "");
  setFieldError("watermark-upload-error", "");
  setFieldError("settingsJson-error", "");
  let firstBad = null;
  const bad = (el, msg) => { el.setAttribute("aria-invalid", "true"); if (!firstBad) firstBad = el; };
  LIMIT_IDS.forEach((id) => {
    const v = $(id).value.trim();
    if (v !== "") {
      const n = Number(v);
      if (!Number.isInteger(n) || n < 0 || n > 10) {
        setFieldError("set-limit-error", "El límite debe ser un número entero entre 0 y 10.");
        bad($(id), "");
      } else $(id).removeAttribute("aria-invalid");
    } else $(id).removeAttribute("aria-invalid");
  });
  const numOrNull = (id) => { const v = $(id).value.trim(); if (v === "") return null; const n = Number(v); return Number.isInteger(n) ? n : NaN; };
  const minD = numOrNull("set-min-duration");
  const maxD = numOrNull("set-max-duration");
  const badNum = (id, msg) => { setFieldError("set-duration-error", msg); bad($(id), ""); };
  if (Number.isNaN(minD) || (minD !== null && (minD < 1 || minD > 300))) badNum("set-min-duration", "El mínimo debe ser un entero entre 1 y 300 segundos.");
  else if (Number.isNaN(maxD) || (maxD !== null && (maxD < 1 || maxD > 300))) badNum("set-max-duration", "El máximo debe ser un entero entre 1 y 300 segundos.");
  else if (minD !== null && maxD !== null && maxD <= minD) badNum("set-max-duration", "El máximo debe ser mayor que el mínimo.");
  const minV = numOrNull("set-min-virality");
  const minS = numOrNull("set-min-seo");
  if (Number.isNaN(minV) || (minV !== null && (minV < 0 || minV > 100))) { setFieldError("set-threshold-error", "La viralidad mínima debe ser un entero entre 0 y 100."); bad($("set-min-virality"), ""); }
  else if (Number.isNaN(minS) || (minS !== null && (minS < 0 || minS > 100))) { setFieldError("set-threshold-error", "El SEO mínimo debe ser un entero entre 0 y 100."); bad($("set-min-seo"), ""); }
  const planBad = (id, msg) => { setFieldError("set-plan-error", msg); bad($(id), ""); };
  const planYt = numOrNull("set-plan-youtube");
  const planTt = numOrNull("set-plan-tiktok");
  const planIg = numOrNull("set-plan-instagram");
  const planFb = numOrNull("set-plan-facebook");
  const planRest = numOrNull("set-plan-resto");
  const newAcc = numOrNull("set-new-account");
  const spacing = numOrNull("set-spacing");
  const spacingYt = numOrNull("set-spacing-youtube");
  if (Number.isNaN(planYt) || (planYt !== null && (planYt < 0 || planYt > 10))) planBad("set-plan-youtube", "El plan de YouTube debe ser un entero entre 0 y 10.");
  else if (Number.isNaN(planTt) || (planTt !== null && (planTt < 0 || planTt > 10))) planBad("set-plan-tiktok", "El plan de TikTok debe ser un entero entre 0 y 10.");
  else if (Number.isNaN(planIg) || (planIg !== null && (planIg < 0 || planIg > 10))) planBad("set-plan-instagram", "El plan de Instagram debe ser un entero entre 0 y 10.");
  else if (Number.isNaN(planFb) || (planFb !== null && (planFb < 0 || planFb > 10))) planBad("set-plan-facebook", "El plan de Facebook debe ser un entero entre 0 y 10.");
  else if (Number.isNaN(planRest) || (planRest !== null && (planRest < 0 || planRest > 10))) planBad("set-plan-resto", "El plan del resto debe ser un entero entre 0 y 10.");
  else if (Number.isNaN(newAcc) || (newAcc !== null && (newAcc < 0 || newAcc > 10))) planBad("set-new-account", "El tope de cuentas nuevas debe ser un entero entre 0 y 10.");
  else if (Number.isNaN(spacing) || (spacing !== null && (spacing < 0 || spacing > 24))) planBad("set-spacing", "Las horas entre publicaciones deben ser un entero entre 0 y 24.");
  else if (Number.isNaN(spacingYt) || (spacingYt !== null && (spacingYt < 0 || spacingYt > 24))) planBad("set-spacing-youtube", "Las horas entre publicaciones en YouTube deben ser un entero entre 0 y 24.");
  const zDaily = numOrNull("set-zernio-daily");
  const zDrafts = numOrNull("set-zernio-drafts");
  if (Number.isNaN(zDaily) || (zDaily !== null && (zDaily < 0 || zDaily > 100))) { setFieldError("set-zernio-error", "Las publicaciones Zernio al día deben ser un entero entre 0 y 100."); bad($("set-zernio-daily"), ""); }
  else if (Number.isNaN(zDrafts) || (zDrafts !== null && (zDrafts < 0 || zDrafts > 100))) { setFieldError("set-zernio-error", "Los borradores Zernio pendientes deben ser un entero entre 0 y 100."); bad($("set-zernio-drafts"), ""); }
  const wmOp = numOrNull("set-watermark-opacity");
  const wmSize = numOrNull("set-watermark-size");
  if (Number.isNaN(wmOp) || (wmOp !== null && (wmOp < 0 || wmOp > 100))) { setFieldError("set-watermark-error", "La opacidad debe ser un entero entre 0 y 100."); bad($("set-watermark-opacity"), ""); }
  else if (Number.isNaN(wmSize) || (wmSize !== null && (wmSize < 50 || wmSize > 1000))) { setFieldError("set-watermark-error", "El tamaño debe ser un entero entre 50 y 1000 px."); bad($("set-watermark-size"), ""); }
  let parsed = null;
  const advancedOpen = $("settingsAdvanced").open;
  if (advancedOpen && settingsJsonTouched) {
    try { parsed = JSON.parse($("settingsJson").value); }
    catch (e) {
      setFieldError("settingsJson-error", "El JSON no es válido: " + e.message + ". Revisa la zona indicada." + jsonErrorLine($("settingsJson").value, e));
      $("settingsJson").setAttribute("aria-invalid", "true");
      if (!firstBad) firstBad = $("settingsJson");
    }
  }
  if (firstBad) { firstBad.focus(); return; }
  const payload = parsed !== null ? parsed : formToSettings();
  setLoading(btn, true, "Guardando ajustes…");
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
    lastSettings = payload;
    $("settingsJson").value = JSON.stringify(payload, null, 2);
    fillSettingsForm(payload);
    settingsJsonTouched = false;
    settingsDirty = false;
    $("settingsDirty").hidden = true;
    $("settingsSavedAt").textContent = "Guardado: " + fmtDate(Date.now());
    showMsg("Ajustes guardados.", "ok");
  } catch (e) {
    showMsg("Ajustes no guardados: " + e.message, "err");
  } finally {
    setLoading(btn, false);
  }
}

// --- Onboarding (asistente para un usuario nuevo, 2026-11-18) ---
// Paso 1: copiar la configuración base (solo reglas, nunca
// credenciales). Paso 2: conectar por OAuth (Google/Meta) o por
// Zernio (dashboard + pegado). Paso 3: Twitch y Gemini pegados.
// Paso 4: verificar cada cuenta de verdad. El Worker queda parado
// hasta que el usuario pulse Iniciar.
let onboardingState = null;
const ONB_LABEL = { youtube: "YouTube", tiktok: "TikTok (Zernio)", meta: "Meta", twitch: "Twitch", gemini: "Gemini" };
async function loadOnboarding() {
  const statusEl = $("onbStatus");
  try {
    const data = await api("/api/onboarding/status");
    onboardingState = data.onboarding;
    renderOnboarding();
  } catch (e) {
    if (statusEl) statusEl.textContent = "No se pudo cargar el estado: " + e.message;
  }
}
function renderOnboarding() {
  const o = onboardingState;
  if (!o) return;
  $("onbStatus").textContent = o.complete
    ? "Onboarding completo: configuración copiada y cuentas verificadas."
    : "Onboarding incompleto: sigue los pasos de abajo.";
  const steps = o.accounts || [];
  $("onbChecklist").innerHTML =
    `<div class="user-row"><div class="user-text"><p class="user-name">Configuración base</p><p class="dim user-meta">Reglas de sebastian: layout, duración, topes, viralidad/SEO</p></div><span class="pill ${o.settings_copied ? "ok" : "neutral"}">${o.settings_copied ? "Copiada" : "Pendiente"}</span></div>` +
    steps.map((s) =>
      `<div class="user-row"><div class="user-text"><p class="user-name">${esc(ONB_LABEL[s.platform] || s.platform)}</p><p class="dim user-meta">${esc(s.verify_message || (s.has_credential ? "Credencial guardada, sin verificar" : "Sin conectar"))}</p></div><span class="pill ${s.verified ? "ok" : "neutral"}">${s.verified ? "Conectada" : "Pendiente"}</span></div>`
    ).join("");
  $("copyBaseStatus").textContent = o.settings_copied ? "Configuración base ya copiada." : "";
  const g = o.oauth.google;
  const m = o.oauth.meta;
  $("oauthGoogleStatus").textContent = g.configured
    ? "Cliente OAuth de Google listo: pulsa Conectar con Google."
    : "Falta crear el cliente OAuth: " + (g.missing || "");
  $("oauthMetaStatus").textContent = m.configured
    ? "Cliente OAuth de Meta listo: pulsa Conectar con Meta."
    : "Falta configurar el OAuth: " + (m.missing || "");
  $("btnOAuthGoogle").disabled = !g.configured;
  $("btnOAuthMeta").disabled = !m.configured;
}
async function maybeOpenOnboarding() {
  // Al entrar, si el onboarding no está completo se abre solo el
  // asistente (salvo que el usuario pidiera otra pestaña por hash).
  try {
    const data = await api("/api/onboarding/status");
    if (!data.onboarding.complete) {
      const wanted = window.location.hash.replace("#", "");
      if (!wanted || wanted === "worker") switchTab("onboarding");
      return true;
    }
  } catch {}
  return false;
}
async function checkOnboardingBanner() {
  const b = $("onboardingBanner");
  if (!b) return;
  try {
    const d = await api("/api/onboarding/status");
    b.classList.toggle("hidden", !!d.onboarding.complete);
  } catch {
    b.classList.add("hidden");
  }
}
async function connectOAuth(provider, accountId) {
  try {
    const s = await api("/api/oauth/" + provider + "/status");
    if (!s.configured) {
      showMsg(s.missing || "OAuth no configurado.", "err");
      await loadOnboarding();
      return;
    }
    // La cuenta es la tarjeta pulsada: el token vuelve a ESA cuenta,
    // nunca a otra del mismo proveedor (ver /api/oauth/*/callback).
    const suffix = accountId ? "?account=" + encodeURIComponent(accountId) : "";
    window.location.href = API + "/api/oauth/" + provider + "/start" + suffix;
  } catch (e) {
    showMsg(e.message, "err");
  }
}
async function savePastedCredential(platform, credential, extra, btn, statusEl, errEl) {
  setLoading(btn, true, "Guardando…");
  try {
    const r = await api("/api/onboarding/credentials", { method: "POST", body: JSON.stringify({ platform, credential, extra }) });
    const v = await api("/api/accounts/" + r.account.id + "/verify", { method: "POST" });
    showMsg(ONB_LABEL[platform] + ": " + (v.message || ""), v.ok ? "ok" : "err");
    if (statusEl) statusEl.textContent = v.message || "";
    await loadOnboarding();
  } catch (e) {
    if (errEl) setFieldError(errEl, e.message);
    showMsg(e.message, "err");
  } finally {
    setLoading(btn, false);
  }
}

// --- Listeners ---
$("loginForm").addEventListener("submit", doLogin);
$("username").addEventListener("input", () => { setFieldError("username-error", ""); $("username").removeAttribute("aria-invalid"); });
$("password").addEventListener("input", () => { setFieldError("password-error", ""); $("password").removeAttribute("aria-invalid"); });
$("passwordToggle").addEventListener("click", () => {
  const pw = $("password");
  const show = pw.type === "password";
  pw.type = show ? "text" : "password";
  $("passwordToggle").textContent = show ? "Ocultar" : "Mostrar";
  $("passwordToggle").setAttribute("aria-pressed", show ? "true" : "false");
});
$("logoutBtn").addEventListener("click", async () => { try { await api("/api/auth/logout", { method: "POST" }); } catch {} setSessionToken(""); showLogin(); });
$("workerLight").addEventListener("click", () => switchTab("worker"));
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
$("subtabbtn-social").addEventListener("click", () => switchCuentasSubtab("social", true));
$("subtabbtn-apis").addEventListener("click", () => switchCuentasSubtab("apis", true));
// El selector de plataforma del formulario se deriva de API_PROVIDERS
// (única fuente): index.html no lleva <option> hardcodeadas.
$("newApiPlatform").innerHTML = API_PROVIDERS.map((p) => `<option value="${esc(p.id)}">${esc(p.label)}${p.id === "meta" ? " (Instagram y Facebook)" : ""}</option>`).join("");
// M5: crear cuenta API desde el panel (POST /api/accounts).
$("apiAddForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("btnAddApiAccount");
  setFieldError("newApiLabel-error", "");
  setLoading(btn, true, "Creando…");
  try {
    const r = await api("/api/accounts", { method: "POST", body: JSON.stringify({ platform: $("newApiPlatform").value, language: $("newApiLanguage").value, label: $("newApiLabel").value.trim() }) });
    showMsg("Cuenta creada: " + (r.account.label || r.account.platform) + ". Ahora pega su credencial y verifícala.", "info");
    $("newApiLabel").value = "";
    await loadAccounts();
  } catch (err) { showMsg(err.message, "err"); }
  finally { setLoading(btn, false); }
});
$("btnStart").addEventListener("click", () => workerAction("/api/worker/start", "Worker iniciado: la cola avanza sola.", $("btnStart"), "Iniciando…"));
$("btnStop").addEventListener("click", () => {
  if (!workerRunning) return;
  if (!window.confirm("¿Parar el Worker?\n\nLa cola queda en pausa y nada se pierde. El cron de cada 5 minutos deja de avanzar la cola y no se publica nada nuevo hasta que vuelvas a pulsar Iniciar. Los clips ya publicados no se borran.")) { $("btnStop").focus(); return; }
  workerAction("/api/worker/stop", "Worker parado: la cola queda en pausa.", $("btnStop"), "Parando…");
});
$("btnRunOnce").addEventListener("click", () => workerAction("/api/worker/run-once", "Ciclo ejecutado.", $("btnRunOnce"), "Ejecutando ciclo…"));
$("btnRefreshW").addEventListener("click", refreshWorker);
$("btnPublish").addEventListener("click", publishSelected);
$("btnUpload").addEventListener("click", uploadSelected);
// Plan de hoy (reglas 2026-11-19): fecha por defecto = hoy en
// America/Santiago; al pulsar se calcula con GET /api/daily-plan.
$("dailyPlanDate").value = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Santiago", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
$("btnDailyPlan").addEventListener("click", loadDailyPlan);
// Crear clip a mano (Cola): el formulario se carga (influencers +
// cuentas) la primera vez que se abre; el idioma re-filtra las
// plataformas por cuenta conectada.
$("createClipDetails").addEventListener("toggle", () => { if ($("createClipDetails").open) loadCreateClipForm(); });
$("createClipForm").addEventListener("submit", submitCreateClip);
$("cc-language").addEventListener("change", refreshCreateClipPlatforms);
$("cc-video").addEventListener("change", validateCreateClipVideo);
$("videoFile").addEventListener("change", validateVideoFile);
$("btnCancelUpload").addEventListener("click", () => { if (uploadXhr) uploadXhr.abort(); });
$("btnCloseDetail").addEventListener("click", () => {
  const id = selectedClipId;
  $("clipDetailCard").classList.add("hidden");
  selectedClipId = null;
  currentBlocked = [];
  const btn = id ? document.querySelector(`[data-clip="${id}"]`) : null;
  if (btn) btn.focus();
});
// --- Fase 2: Editor / Canales / Tracking / Analíticas ---
$("btnOpenEditor").addEventListener("click", () => { if (selectedClipId) switchTab("editor"); });
$("edGoCola").addEventListener("click", () => switchTab("cola"));
$("edRetry").addEventListener("click", loadEditor);
document.querySelectorAll("[data-goto-cola]").forEach((b) => b.addEventListener("click", () => switchTab("cola")));
$("edPlayPause").addEventListener("click", () => { const v = $("edVideo"); if (!editorVideoHasMp4) return; if (v.paused) v.play(); else v.pause(); });
$("edMute").addEventListener("click", () => { const v = $("edVideo"); v.muted = !v.muted; $("edMute").textContent = v.muted ? "Sonido" : "Silencio"; });
$("edSafeToggle").addEventListener("click", () => {
  const g = $("edSafeGuides");
  const show = g.classList.contains("hidden");
  g.classList.toggle("hidden", !show);
  $("edSafeToggle").textContent = show ? "Ocultar zonas seguras" : "Ver zonas seguras";
  $("edSafeToggle").setAttribute("aria-pressed", show ? "true" : "false");
});
$("edPublish").addEventListener("click", publishFromEditor);
$("edMarkPreview").addEventListener("click", markPreviewSeen);
$("edSaveDuration").addEventListener("click", saveEditorDuration);
$("edSaveCategory").addEventListener("click", saveEditorCategory);
$("edSaveSeo").addEventListener("click", saveEditorSeo);
document.querySelectorAll("#edStyleOptions button").forEach((b) => b.addEventListener("click", () => saveEditorStyle(b.dataset.edStyle)));
$("edVideo").addEventListener("loadedmetadata", () => { const v = $("edVideo"); $("edTime").textContent = fmtClock(v.currentTime) + " / " + fmtClock(v.duration); });
$("edVideo").addEventListener("timeupdate", () => { const v = $("edVideo"); $("edTime").textContent = fmtClock(v.currentTime) + " / " + fmtClock(v.duration); });
$("edVideo").addEventListener("play", () => { $("edPlayPause").textContent = "Pausa"; });
$("edVideo").addEventListener("pause", () => { $("edPlayPause").textContent = "Reproducir"; });
$("edVideo").addEventListener("ended", () => { $("edPlayPause").textContent = "Reproducir"; });
$("anGoPaste").addEventListener("click", () => {
  $("anPasteForm").scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
  const f = $("anPasteClip").disabled ? $("anPasteViews") : $("anPasteClip");
  if (f && !f.disabled) f.focus({ preventScroll: true });
});
$("anPasteForm").addEventListener("submit", saveStat);
$("influencerForm").addEventListener("submit", saveInfluencer);
$("btnRefreshChannels").addEventListener("click", loadChannels);
$("trackingClipSelect").addEventListener("change", () => { const id = Number($("trackingClipSelect").value); if (id) loadTrackingTimeline(id); });
document.querySelectorAll("[data-tracking-filter]").forEach((b) => b.addEventListener("click", async () => {
  trackingFilter = b.dataset.trackingFilter;
  document.querySelectorAll("[data-tracking-filter]").forEach((x) => { const on = x === b; x.classList.toggle("active", on); x.setAttribute("aria-pressed", on ? "true" : "false"); });
  const prev = trackingSelectedId;
  renderTrackingOptions();
  if (trackingSelectedId && trackingSelectedId !== prev) await loadTrackingTimeline(trackingSelectedId);
  else if (!trackingSelectedId) renderTrackingEmpty();
}));
$("btnRefreshTracking").addEventListener("click", async () => {
  const btn = $("btnRefreshTracking");
  setLoading(btn, true, "Actualizando…");
  try { await loadTracking(); } catch (e) { showMsg(e.message, "err"); }
  finally { setLoading(btn, false); }
});
$("settingsForm").addEventListener("submit", saveSettings);
async function refreshCfgWorker() {
  const el = $("cfgWorkerState");
  if (!el) return;
  try {
    const s = await api("/api/worker/state");
    el.textContent = s.running ? "Worker: en marcha — la cola avanza sola aunque te desloguees." : "Worker: parado — pulsa Iniciar para que todo se haga automático.";
    $("btnStartCfg").disabled = !!s.running;
    $("btnStopCfg").disabled = !s.running;
  } catch (e) { el.textContent = "Worker: no se pudo leer el estado."; }
}
$("btnStartCfg").addEventListener("click", async () => { await workerAction("/api/worker/start", "Worker iniciado: la cola avanza sola.", $("btnStartCfg"), "Iniciando…"); refreshCfgWorker(); });
$("btnStopCfg").addEventListener("click", async () => {
  if (!window.confirm("¿Parar el Worker?\n\nLa cola queda en pausa y nada se pierde.")) return;
  await workerAction("/api/worker/stop", "Worker parado: la cola queda en pausa.", $("btnStopCfg"), "Parando…");
  refreshCfgWorker();
});
$("settingsForm").addEventListener("input", markSettingsDirty);
$("settingsForm").addEventListener("change", markSettingsDirty);
$("settingsJson").addEventListener("input", () => { settingsJsonTouched = true; });
$("settingsAdvanced").addEventListener("toggle", () => {
  if ($("settingsAdvanced").open) $("settingsJson").value = JSON.stringify(formToSettings(), null, 2);
});
$("btnCopyJson").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("settingsJson").value); showMsg("JSON copiado.", "info"); } catch { showMsg("No se pudo copiar el JSON.", "err"); }
});

// --- Onboarding: listeners ---
$("btnCopyBase").addEventListener("click", async () => {
  const btn = $("btnCopyBase");
  setLoading(btn, true, "Copiando…");
  try {
    const r = await api("/api/onboarding/copy-settings", { method: "POST", body: JSON.stringify({}) });
    showMsg(r.already ? "La configuración base ya estaba copiada." : "Configuración base copiada.", "ok");
    await loadOnboarding();
  } catch (e) {
    showMsg(e.message, "err");
  } finally {
    setLoading(btn, false);
  }
});
$("btnOAuthGoogle").addEventListener("click", () => connectOAuth("google"));
$("btnOAuthMeta").addEventListener("click", () => connectOAuth("meta"));
$("btnZernioConnect").addEventListener("click", () => window.open("https://zernio.com/dashboard/connections", "_blank", "noopener"));
$("btnZernioSave").addEventListener("click", async () => {
  const btn = $("btnZernioSave");
  const key = $("onbZernioKey").value.trim();
  const accId = $("onbZernioAccount").value.trim();
  setFieldError("onbZernio-error", "");
  if (!key || !accId) {
    setFieldError("onbZernio-error", "Pega la API key de Zernio y el ID de la cuenta TikTok.");
    return;
  }
  setLoading(btn, true, "Guardando…");
  try {
    const list = await api("/api/accounts");
    let t = (list.accounts || []).find((a) => a.platform === "tiktok" && a.language === "es");
    if (!t) {
      const created = await api("/api/accounts", { method: "POST", body: JSON.stringify({ platform: "tiktok", language: "es", label: "TikTok" }) });
      t = created.account;
    }
    await api("/api/accounts/" + t.id + "/credential", { method: "POST", body: JSON.stringify({ credential: key, extra: JSON.stringify({ via: "zernio", account_id: accId }) }) });
    const v = await api("/api/accounts/" + t.id + "/verify", { method: "POST" });
    showMsg("TikTok: " + (v.message || ""), v.ok ? "ok" : "err");
    $("zernioStatus").textContent = v.message || "";
    await loadOnboarding();
  } catch (e) {
    setFieldError("onbZernio-error", e.message);
    showMsg(e.message, "err");
  } finally {
    setLoading(btn, false);
  }
});
$("btnTwitchSave").addEventListener("click", async () => {
  const cid = $("onbTwitchClientId").value.trim();
  const sec = $("onbTwitchSecret").value.trim();
  setFieldError("onbTwitch-error", "");
  if (!cid || !sec) {
    setFieldError("onbTwitch-error", "Pega el Client ID y el Client Secret de Twitch.");
    return;
  }
  await savePastedCredential("twitch", sec, JSON.stringify({ client_id: cid }), $("btnTwitchSave"), $("twitchStatus"), "onbTwitch-error");
});
$("btnGeminiSave").addEventListener("click", async () => {
  const key = $("onbGeminiKey").value.trim();
  setFieldError("onbGemini-error", "");
  if (!key) {
    setFieldError("onbGemini-error", "Pega la API key de Gemini.");
    return;
  }
  await savePastedCredential("gemini", key, "", $("btnGeminiSave"), $("geminiStatus"), "onbGemini-error");
});
$("btnVerifyAll").addEventListener("click", async () => {
  const btn = $("btnVerifyAll");
  setLoading(btn, true, "Verificando…");
  try {
    const r = await api("/api/onboarding/verify-all", { method: "POST", body: JSON.stringify({}) });
    const rows = r.results || [];
    $("verifyAllResults").innerHTML = rows.length
      ? rows.map((x) => `<p>${x.ok ? "✓" : "✗"} ${esc(ONB_LABEL[x.platform] || x.platform)}: ${esc(x.message || "")}</p>`).join("") +
        `<p><b>${r.all_ok ? "Todo verificado." : "Falta alguna cuenta: revisa los motivos de arriba."}</b></p>`
      : "<p>Sin cuentas todavía: conecta al menos una.</p>";
    showMsg(r.all_ok ? "Todas las cuentas verificadas." : "Alguna cuenta no verificó: revisa los motivos.", r.all_ok ? "ok" : "err");
    await loadOnboarding();
  } catch (e) {
    showMsg(e.message, "err");
  } finally {
    setLoading(btn, false);
  }
});
$("btnGoWorker").addEventListener("click", () => switchTab("worker"));
$("btnGoOnboarding").addEventListener("click", () => switchTab("onboarding"));

// --- Init: hash de pestaña + ruteo por usuario ---
const initialTab = window.location.hash.replace("#", "");
if (TAB_NAMES.includes(initialTab)) currentTab = initialTab;
(async () => {
  // Primero el mapa usuario -> Worker: sin él no se envía ninguna
  // petición. Si no carga, error claro y login bloqueado (fail loudly).
  const mapOk = await loadWorkersMap();
  if (!mapOk) {
    showLoginUI();
    const btn = $("loginBtn");
    if (btn) btn.disabled = true;
    showLoginMsg(WORKERS_ERROR + " Recarga la página para reintentarlo.");
    return;
  }
  // Sin base guardada no hay sesión que restaurar: pedir usuario.
  if (!API) { showLogin(); return; }
  checkHealth();
  try {
    const m = await api("/api/auth/me");
    me = m; showApp();
    switchTab(currentTab, true);
    loadNotifications();
    // Vuelta de un OAuth (?oauth=google&ok=1&msg=...): enseñar el
    // resultado y abrir el asistente; la query se limpia.
    const qp = new URLSearchParams(window.location.search);
    if (qp.get("oauth")) {
      const ok = qp.get("ok") === "1";
      const msg = qp.get("msg") || "";
      history.replaceState(null, "", "#onboarding");
      showMsg((ok ? "Conectado: " : "No se pudo conectar: ") + msg, ok ? "ok" : "err");
      switchTab("onboarding");
      return;
    }
    await maybeOpenOnboarding();
  } catch { showLogin(); }
})();
