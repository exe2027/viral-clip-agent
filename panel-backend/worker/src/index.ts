// Router del Worker: API del panel + cron.
// Experiencia de producto: login → configurar (Cuentas / subtítulos /
// ajustes) → UN solo botón Iniciar en la pestaña Worker → todo
// automático: el Worker dispara el workflow del cerebro en GitHub
// Actions (detección, corte, subtítulos, publicación) y el cron del
// Worker cada 5 min avanza la cola, sin que el usuario pulse nada
// por clip. Solo Cloudflare + manual queda como respaldo degradado
// (ver COMPUTE.md).
// Si algo se atasca, queda en Tracking con motivo y hora
// y aparece como notificación visible en el panel (Telegram solo si
// el usuario lo configura después en Ajustes).
//
// Auth: todas las rutas /api/* exigen sesión (cookie firmada),
// salvo /api/health y /api/auth/login. Las rutas /api/jobs/* del
// puente exigen X-Bridge-Token (secreto BRIDGE_TOKEN), no sesión.

import type { Env, ExecutionContext, ScheduledEvent } from "./cloudflare";
import { clearSessionCookie, createOAuthState, createSessionToken, extractSessionToken, requireTenant, sessionCookieHeader, tokenFingerprint, verifyOAuthState, verifyPassword, verifySessionToken, type SessionPayload, type Tenant } from "./auth";
import { decryptCredential, encryptCredential, maskCredential } from "./crypto";
import { BRAIN_SESSION_TTL_MS, hasRecentBrainToken, issueBrainToken, liveRunInFlight, redeemBrainToken, resolveBrainCredentials } from "./brain";
// Presupuesto D1 + monitoreo (A1/A6, 2026-11-20): el guardián
// frena los dispatches automáticos frecuentes cuando el consumo
// pasa el 70% del tope operativo; el monitor horario y el informe
// diario 20:50 corren desde el cron (ver scheduled()).
import { createBudgetGuard, currentBudgetGuard } from "./budget";
import { maybeRunBudgetMonitor, maybeSendDailyBudgetReport } from "./budget-monitor";
import { maybeValidateAccountsDaily, validateAccountsDaily } from "./daily-validation";
import {
  createApiAccount,
  createCampaignSubmission,
  createClip,
  createClipStat,
  createEvoGeneration,
  createEvoVariant,
  createInfluencer,
  createTelegramLinkCode,
  createWatermarkLogo,
  deleteInfluencer,
  deleteTelegramLinkCode,
  deleteWatermarkLogo,
  createTelegramStopConfirmation,
  deleteTelegramStopConfirmation,
  findTelegramLinkCode,
  getApiAccount,
  getApiAccountByPlatform,
  getCampaignSubmission,
  getClip,
  getEvoGeneration,
  getEvoGenerationById,
  getEvoVariant,
  getInfluencer,
  getLatestVodCheck,
  getLoginUser,
  getSettings,
  getTelegramLinkByChat,
  getTelegramLinkByTenant,
  getTelegramStopConfirmation,
  getWatermarkLogo,
  getSocialAccount,
  getSource,
  GTA_VI_GAME,
  clipLanguage,
  campaignForStreamer,
  campaignSubmissionOut,
  CAMPAIGN_SUBMISSION_STATUSES,
  effectiveSettings,
  evoGenerationOut,
  evoStatusSummary,
  evoVariantOut,
  extractBaseSettings,
  isCampaignStreamer,
  isGtaViGame,
  isActivatableInfluencer,
  isPruneHour,
  isTokenRevoked,
  linkTelegramChat,
  listApiAccounts,
  listAnalyticsStatus,
  listCampaignSubmissions,
  listClipPublishStates,
  listClipStats,
  listClipStatsForClip,
  listEvoGenerations,
  listEvoVariants,
  listPostedJobs,
  listInfluencers,
  listJobs,
  listPanelUsers,
  listPendingCampaignSubmissions,
  listPublishedClips,
  listQueueClips,
  listWatermarkLogos,
  listSocialAccounts,
  listSources,
  logClipEvent,
  logWorkerEvent,
  hasRecentWorkerEvent,
  markTelegramSent,
  maturityOut,
  mergeViralJson,
  moveQueueClip,
  normalizeHandle,
  parsePlatforms,
  parsePublishStates,
  parseClipLanguage,
  parseViralInput,
  parseViralJson,
  platformMax,
  platformPlan,
  pruneOldEvents,
  recordEvoFitness,
  refreshAccountMaturity,
  restWeekdayEffective,
  revokeToken,
  santiagoWeekKey,
  saveSettings,
  spacingHoursFor,
  daysPerWeekFor,
  tiktokHandleFromEvidence,
  toApiPlatform,
  unlinkTelegramChat,
  unlinkTelegramTenant,
  isAccountVerificationCurrent,
  updateApiAccountVerification,
  updateCampaignSubmission,
  updateClip,
  updateEvoGeneration,
  updateEvoVariant,
  updateInfluencer,
  validateSettingsInput,
  accountPausedUntilMs,
  isRestDayEffective,
  isWithinPublishSlot,
  PUBLISH_SLOT_WINDOWS,
  WEEKDAY_NAMES_ES,
  PANEL_PLATFORMS,
  type ClipRow,
  type ClipStatRow,
  type InfluencerRow,
  type ApiAccountRow,
  type SocialAccountRow,
  type JobStateFilter,
  type NewClip,
  type NewClipStat,
  type NewInfluencer,
  type PanelPlatform,
  type PlatformKey,
} from "./db";
import {
  claimJob,
  enqueuePublishJobs,
  alertQuotaExhausted,
  isWorkerRunning,
  markFailed,
  markPosted,
  notify,
  publishGate,
  runWorkerCycle,
  santiagoDayKey,
  seoForPlatform,
  setWorkerRunning,
  tryEnqueueClipPublish,
  type JobRow,
} from "./jobs";
import { dispatchRetryAllowed, isD1QuotaError, isQuotaBreakerOpen, noteDispatchFailure, tripQuotaBreaker } from "./retry";
import { GOOGLE_SCOPES, META_SCOPES } from "./publish/oauth-scopes";
import {
  ENGAGEMENT_PLATFORMS,
  extractNativePostId,
  getEngagementStatus,
  listTargets,
  type ZernioPlatform,
} from "./publish/zernio-engagement";
import { invalidateProbeCache, probeNativeScopes, runEngagement } from "./publish/native-engagement";
import { clipPublishBreakdown, clipRetentionSummary } from "./r2-retention";
// Intervalos del ciclo y los dispatches (A3, 2026-11-20): viven en
// cycle-gate.ts (single-source); aquí solo se importan.
import {
  BRAIN_DAILY_SENT_DAY_KEY,
  LIVE_DISPATCH_MIN_INTERVAL_MS,
  REVALIDATE_INTERVAL_MS,
  REVALIDATE_LAST_KEY,
  WATCHDOG_DISPATCH_MIN_INTERVAL_MS,
  WATCHDOG_DISPATCHED_PREFIX,
} from "./cycle-gate";
import { buildDailyPlanForTenant } from "./daily-plan";
import {
  r2KeyForClip,
  r2KeyForWatermark,
  signWatermarkUrl,
  uploadClipMp4,
  uploadWatermarkImage,
  verifySignedVideoUrl,
  verifySignedWatermarkUrl,
  WATERMARK_KEY_RE,
  WATERMARK_MAX_BYTES,
} from "./r2";
import {
  botConfigured,
  BRIDGE_NOTIFY_MAX_CHARS,
  buildAnalyticsMessage,
  buildQueueMessage,
  buildStatusMessage,
  buildTestMessage,
  buildTopMessage,
  buildWorkerStoppedAlertMessage,
  buildWorkerStuckAlertMessage,
  TELEGRAM_WORKER_STUCK_ALERT_KEY,
  generateLinkCode,
  helpMessage,
  isStopConfirmation,
  linkedMessage,
  linkInstructionsMessage,
  LINK_CODE_TTL_MS,
  maskChatId,
  parseLinkCode,
  parseTelegramCommand,
  parseTelegramUpdate,
  santiagoParts,
  sendTelegram,
  sha256Hex,
  stopConfirmMessage,
  STOP_CONFIRM_TTL_MS,
  telegramConfigured,
  TELEGRAM_WORKER_LAST_RUNNING_KEY,
  unlinkedMessage,
  TELEGRAM_SENT_DAY_KEY,
} from "./telegram";
import { verifyCredential } from "./verify";
import { httpJson } from "./publish/http";
import { collectWorkerSupply, STUCK_ALERT_MIN_INTERVAL_MS } from "./worker-validate";
import { getWorkerPhase, handleReportClipEvent, handleReportPhase, nextBrainRunAt, phaseView } from "./worker-phase";
import { checkInfluencersLive } from "./live-check";
import { handleMcpRequest } from "./mcp";
import { qualityGateForClip } from "./quality-gate";
import {
  EVO_KEEP_WINNERS,
  EVO_POPULATION_SIZE,
  generatePopulation,
  isDistinctCut,
  passesBrainGates,
  publishBlockReason,
  realFitness,
  selectWinners,
  type EvoScoredVariant,
  type EvoVariantParams,
} from "./evolution";

const json = (data: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json", ...headers } });

const err = (message: string, status = 400) => json({ ok: false, error: message }, status);

async function readJson(req: Request): Promise<Record<string, unknown>> {
  try {
    return (await req.json()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

async function requireSession(req: Request, env: Env): Promise<SessionPayload | null> {
  // Cookie o Authorization: Bearer <token> (Brave iOS bloquea la
  // cookie cross-site; el Bearer es la vía alternativa). La sesión
  // solo vale si su tenant es el de ESTE Worker (env.TENANT):
  // una sesión válida del otro tenant da 401 aquí.
  // B1 (Frente 2): un token revocado por logout no vale aunque su
  // firma HMAC siga siendo válida: se comprueba su huella en
  // revoked_tokens (0015). Se prueba cada vía por separado: una
  // cookie revocada no invalida un Bearer nuevo válido, ni al revés.
  const tenant = requireTenant(env);
  const cookie = req.headers.get("Cookie");
  const auth = req.headers.get("Authorization");
  const cookieMatch = /(?:^|;\s*)panel-session=([^;]+)/.exec(cookie ?? "");
  const bearerMatch = /^Bearer\s+(.+)$/i.exec((auth ?? "").trim());
  for (const raw of [cookieMatch?.[1] ?? null, bearerMatch?.[1] ?? null]) {
    if (!raw) continue;
    const payload = await verifySessionToken(raw, env.SESSION_SECRET, tenant);
    if (!payload) continue;
    if (await isRevoked(env, raw)) continue;
    return payload;
  }
  return null;
}

/** ¿Está revocado este token? Un fallo de D1 no cierra la sesión:
 *  sin D1 nada más funciona tampoco, y la migración 0015 se aplica
 *  antes de desplegar este código. */
async function isRevoked(env: Env, token: string): Promise<boolean> {
  try {
    return await isTokenRevoked(env.DB, await tokenFingerprint(token));
  } catch {
    return false;
  }
}

/** Tenant del puente (X-Bridge-Tenant): obligatorio y el de ESTE
 *  Worker (env.TENANT). Un header con el otro tenant no vale. */
function requireBridgeTenant(req: Request, env: Env): Tenant | null {
  const tenant = requireTenant(env);
  const t = req.headers.get("X-Bridge-Tenant") ?? "";
  return t === tenant ? tenant : null;
}

function requireBridge(req: Request, env: Env): boolean {
  const token = req.headers.get("X-Bridge-Token");
  return !!env.BRIDGE_TOKEN && !!token && token === env.BRIDGE_TOKEN;
}

// ---------------------------------------------------------------------------
// OAuth del asistente de onboarding (2026-11-18, ONBOARDING-2026-11-18.md).
// Redirect URI del panel (la que hay que dar de alta en cada consola):
//   https://avc.vikingosfoodspa.workers.dev/api/oauth/<provider>/callback
// El cliente OAuth NO existe todavía: el cliente de Google actual es
// de escritorio (no admite redirect web) y la app Meta está en
// Desarrollo (solo usuarios con rol/tester). Sin cliente configurado
// (secretos GOOGLE_CLIENT_ID/SECRET, META_APP_ID/SECRET), el botón
// Conectar dice exactamente qué falta; nunca un "Conectada" falso.
// Zernio no admite OAuth con redirect al panel: su conexión es por
// dashboard (zernio.com) y aquí solo se pega su API key + account id.
// ---------------------------------------------------------------------------
const FRONT_URL = "https://avc-em9.pages.dev";
const WORKER_URL = "https://avc.vikingosfoodspa.workers.dev";
// Scopes OAuth: fuente única en ./publish/oauth-scopes.ts (v5.4.0:
// se añadió youtube.force-ssl + instagram_manage_comments +
// pages_manage_engagement para el engagement nativo; solo afectan a
// la próxima autorización, no invalidan los tokens actuales).
const ZERNIO_CONNECT_URL = "https://zernio.com/dashboard/connections";
const TWITCH_CONSOLE_URL = "https://dev.twitch.tv/console/apps";
const GEMINI_KEYS_URL = "https://aistudio.google.com/apikey";

function oauthRedirectUri(provider: string): string {
  return `${WORKER_URL}/api/oauth/${provider}/callback`;
}

function oauthConfigured(env: Env, provider: string): boolean {
  if (provider === "google") return !!(env.GOOGLE_CLIENT_ID || "").trim() && !!(env.GOOGLE_CLIENT_SECRET || "").trim();
  if (provider === "meta") return !!(env.META_APP_ID || "").trim() && !!(env.META_APP_SECRET || "").trim();
  // Zernio/Twitch/Gemini no usan OAuth del panel: siempre "listos"
  // para su vía (dashboard o pegado), nunca bloqueados por un cliente.
  return true;
}

function oauthMissingMessage(provider: string): string {
  if (provider === "google") {
    return "Falta crear el cliente OAuth de Google (tipo Aplicación web) en Google Cloud Console con la redirect URI https://avc.vikingosfoodspa.workers.dev/api/oauth/google/callback, y guardar su ID y secreto como secretos GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET del Worker. El cliente actual es de escritorio y no vale para la web.";
  }
  if (provider === "meta") {
    return "Falta configurar el OAuth de Meta: en developers.facebook.com, app Clipdelgta6, añade la redirect URI https://avc.vikingosfoodspa.workers.dev/api/oauth/meta/callback y guarda el App ID y el App Secret como secretos META_APP_ID y META_APP_SECRET del Worker. La app está en Desarrollo: solo entran usuarios con rol o tester.";
  }
  return "Proveedor OAuth desconocido.";
}

/** Guarda (o crea) la cuenta API de un tenant con su credencial
 *  cifrada. La credencial nunca se devuelve ni se registra.
 *
 *  DOS COLAS (0019): la cuenta se crea en el idioma que se pasa.
 *  OAuth y onboarding no llevan clip (no hay idioma del clip en
 *  ese flujo): conectan la cuenta principal, en español (default
 *  "es"). Las cuentas en inglés se crean en Cuentas (POST
 *  /api/accounts acepta language). La ruta de publicación NUNCA
 *  auto-crea cuentas: si falta la cuenta en el idioma del clip, el
 *  gate frena con "sin cuenta … en inglés/español" (ver POST
 *  /publish). */
async function upsertAccountCredential(
  env: Env,
  tenant: Tenant,
  platform: string,
  label: string,
  credential: string,
  extraObj: Record<string, unknown> | null,
  language: "es" | "en" = "es",
): Promise<{ id: number; platform: string; label: string }> {
  let acc = await getApiAccountByPlatform(env.DB, tenant, platform, language);
  if (!acc) acc = await createApiAccount(env.DB, tenant, { platform, language, label });
  const credentialEnc = await encryptCredential(credential, env.CREDENTIAL_ENCRYPTION_KEY, tenant);
  const extraEnc = extraObj ? await encryptCredential(JSON.stringify(extraObj), env.CREDENTIAL_ENCRYPTION_KEY, tenant) : "";
  // V2 (0043): la credencial cambió: se sella credential_changed_at
  // y se invalida la caché de la sonda, para que el veredicto
  // guardado no se reutilice como confirmación con la credencial
  // vieja. Lo usa también el callback OAuth (google/meta).
  await env.DB.prepare("UPDATE api_accounts SET credential = '', credential_enc = ?, extra = '', extra_enc = ?, status = 'pendiente', credential_changed_at = ? WHERE id = ? AND tenant_id = ?")
    .bind(credentialEnc, extraEnc, Date.now(), acc.id, tenant)
    .run();
  await invalidateProbeCache(env.DB, tenant);
  return { id: acc.id, platform: acc.platform, label: acc.label };
}

/** Validez de la cuenta API subyacente a una fila social (GET
 *  /api/social-accounts, spec 2026-11-23 §3.3): el join vive en el
 *  servidor, el front no lo hace. YouTube y TikTok mapean a su fila
 *  del mismo idioma; Instagram y Facebook comparten la ÚNICA fila
 *  meta (mismo idioma si existe, si no cualquier fila meta). El
 *  campo que manda "válida" es verification_current, no verify_ok:
 *  un OK viejo con credencial cambiada es el falso positivo V2. */
function apiVerificationForSocial(
  social: SocialAccountRow,
  apiAccounts: ApiAccountRow[],
): { api_account_id: number; verification_current: boolean; verify_ok: number | null; verify_message: string; verified_at: number | null } | null {
  const platform = social.platform === "instagram" || social.platform === "facebook" ? "meta" : social.platform;
  const lang = (social.language || "es").toLowerCase();
  const row =
    apiAccounts.find((a) => a.platform === platform && (a.language || "es").toLowerCase() === lang) ??
    apiAccounts.find((a) => a.platform === platform);
  if (!row) return null;
  return {
    api_account_id: row.id,
    verification_current: isAccountVerificationCurrent(row),
    verify_ok: row.verify_ok,
    verify_message: row.verify_message,
    verified_at: row.verified_at,
  };
}

/** Verifica DE VERDAD una cuenta API contra su plataforma y guarda
 *  el resultado en D1 (mismo criterio que POST /verify). */
async function verifyApiAccount(
  env: Env,
  tenant: Tenant,
  acc: { id: number; platform: string; label: string; credential_enc: string; extra_enc: string },
): Promise<{ ok: boolean; message: string }> {
  if (!acc.credential_enc) return { ok: false, message: "Sin credencial cifrada: pégala primero en Cuentas." };
  const credential = await decryptCredential(acc.credential_enc, env.CREDENTIAL_ENCRYPTION_KEY, tenant);
  let extraJson: Record<string, unknown> = {};
  if (acc.extra_enc) {
    try {
      extraJson = JSON.parse(await decryptCredential(acc.extra_enc, env.CREDENTIAL_ENCRYPTION_KEY, tenant)) as Record<string, unknown>;
    } catch {
      extraJson = {};
    }
  }
  const outcome = await verifyCredential(acc.platform, credential, extraJson);
  await updateApiAccountVerification(env.DB, acc.id, tenant, outcome.ok, outcome.ok ? "conectada" : "error", outcome.message);
  if (!outcome.ok) {
    // B7 (v3): cuenta desconectada / credencial fallida. El aviso
    // va al panel y, con env, por Telegram a SU chat vinculado
    // (notify v2). Nunca expone el token: solo plataforma/etiqueta
    // y el motivo de la verificación, sin la credencial.
    await notify(env.DB, { kind: "credential", message: `La cuenta ${acc.label} dejó de verificar: ${outcome.message}` }, tenant, env);
  }
  return outcome;
}

/** Redirige al front con el resultado del OAuth (?oauth=&ok=&msg=). */
function oauthReturn(provider: string, ok: boolean, message: string): Response {
  const url = `${FRONT_URL}/?oauth=${encodeURIComponent(provider)}&ok=${ok ? "1" : "0"}&msg=${encodeURIComponent(message.slice(0, 300))}#onboarding`;
  return new Response(null, { status: 302, headers: { Location: url } });
}

// Dispara el workflow del cerebro en GitHub (vía principal:
// Cloudflare + GitHub Actions, decisión 17 nov 2026 "Todo
// automático"). El evento es panel-start, panel-daily o panel-live
// (tipos FIJOS: el tenant NO va en el tipo, va en el
// client_payload, así un tenant nuevo no exige tocar el workflow).
// Junto al evento viaja un token de un solo uso para ese tenant
// (brain_tokens, 0032): el cerebro lo canjea en
// POST /api/brain/session por una sesión de 12 h y con ella pide
// sus credenciales a GET /api/bridge/credentials. Sin token no se
// dispara (un run sin token no podría autenticarse). El cron del
// workflow ya no existe: la única fuente de triggers es este
// dispatch (2026-11-19, cerebro genérico). Si no hay GITHUB_TOKEN
// configurado, no se dispara nada y el Worker sigue funcionando
// con el flag D1 (el cron de GitHub queda en espera); el aviso
// queda en worker_events para que el panel lo muestre. Nunca rompe
// el Iniciar/Detener ni el cron: un fallo de red o un 4xx de
// GitHub solo queda registrado. Devuelve true solo si GitHub
// aceptó el dispatch (2xx); false si no había token, no se pudo
// emitir el token del cerebro o GitHub lo rechazó.
async function dispatchCerebro(env: Env, eventType: string, tenant: Tenant): Promise<boolean> {
  // Un Worker = un tenant: nunca se dispara (ni se emite token del
  // cerebro) para otro tenant. Falla en voz alta: el único llamador
  // de producción es el cron con env.TENANT.
  if (tenant !== requireTenant(env)) {
    throw new Error(`dispatchCerebro para ${tenant} en un Worker de ${env.TENANT}.`);
  }
  const token = (env.GITHUB_TOKEN || "").trim();
  const repo = (env.GITHUB_REPO || "exe2027/viral-clip-agent").trim();
  if (!token) {
    await logWorkerEvent(env.DB, "github_dispatch",
      `Sin GITHUB_TOKEN: no se disparó ${eventType} a GitHub; el panel sigue con el flag D1.`, tenant);
    return false;
  }
  // A4: circuito 7500 — con la cuota agotada no se emite token ni
  // se dispara: el token es una escritura que moriría con 7500 y
  // la corrida del cerebro no podría registrar nada en D1. Se
  // reanuda solo al liberarse la cuota (el circuito se cierra en
  // el reset de las 00:00 UTC). Sin escribir el motivo (eso también
  // es una escritura): el panel lo muestra con la alerta única.
  if (isQuotaBreakerOpen(Date.now())) return false;
  // A4: freno de dispatch fallido (solo automáticos). Un dispatch
  // que falló sin emitir token no engancha el throttle de 15 min
  // de abajo, así que este freno (backoff 1/5/15 + tope 3/día) es
  // el que impide reintentarlo cada minuto.
  const automatic = eventType === "panel-live" || eventType === "panel-watchdog";
  if (automatic && !dispatchRetryAllowed(eventType, Date.now())) return false;
  // Freno de presupuesto (A1, 2026-11-20): con el consumo por
  // encima del 70% del tope operativo, los dispatches
  // automáticos frecuentes (panel-live, panel-watchdog) no se
  // emiten: cada dispatch es un token nuevo en D1 + una corrida
  // que escribe. panel-start (el usuario pulsa Iniciar) y
  // panel-daily (1/día) NUNCA se frenan aquí (misma regla que el
  // throttle de 15 min: ver skill d1-write-discipline). Sin
  // guardián vigente (o viejo), se dispara como siempre.
  if (eventType === "panel-live" || eventType === "panel-watchdog") {
    const guard = currentBudgetGuard();
    if (guard && !guard.shouldWrite("dispatch")) return false;
  }
  // Throttle por lectura (2026-11-20, cuota D1): SOLO para los
  // dispatches automáticos frecuentes (panel-live, panel-watchdog).
  // Si ya se emitió un token hace menos de 15 min, su corrida
  // está en curso o en cola: no se emite otro token ni se dispara
  // (cada token es 1 fila + 1 dispatch a GitHub + 1 corrida; en
  // el bucle de fallo de 2026-11-20 eso era 60/hora). El chequeo
  // es una lectura de brain_tokens (ver hasRecentBrainToken): los
  // throttles por escritura no persisten cuando la cuota se agota.
  // panel-start (el usuario pulsa Iniciar / el vigilante reinicia)
  // y panel-daily (1/día) NUNCA se frenan aquí.
  if (eventType === "panel-live" || eventType === "panel-watchdog") {
    if (await hasRecentBrainToken(env.DB, tenant, LIVE_DISPATCH_MIN_INTERVAL_MS, Date.now())) {
      await logWorkerEvent(env.DB, "github_dispatch",
        `Dispatch ${eventType} omitido para ${tenant}: ya se emitió un token del cerebro hace menos de 15 min (su corrida está en curso o en cola).`, tenant).catch(() => {});
      return false;
    }
  }
  let brainToken = "";
  try {
    brainToken = await issueBrainToken(env.DB, tenant);
  } catch (e) {
    // A4: si la emisión murió por la cuota (7500), se dispara el
    // circuito y se intenta UNA alerta; los demás errores quedan
    // registrados como antes.
    if (isD1QuotaError(e)) {
      tripQuotaBreaker(Date.now());
      await alertQuotaExhausted(env.DB, tenant, env).catch(() => {});
    }
    await logWorkerEvent(env.DB, "github_dispatch",
      `No se pudo emitir el token del cerebro para ${eventType} (${tenant}): ${e instanceof Error ? e.message : String(e)}. No se dispara (sin token el cerebro no puede autenticarse).`, tenant).catch(() => {});
    return false;
  }
  // Multi-tenant (2026-11-21, bug kaez): el panel de cada tenant dispara
  // `panel-start`, pero el workflow genérico (cerebro.yml) está fijado al
  // Worker de sebastian y canjea el token ahí → 401 "Token del cerebro
  // inválido" para cualquier otro tenant. Cada tenant tiene su workflow
  // propio (`panel-start-<tenant>`, p. ej. cerebro-kaez.yml escucha
  // `panel-start-kaez`). Para sebastian el evento sigue siendo `panel-start`
  // (el genérico lo escucha); para el resto se sufija con el tenant.
  const effectiveEventType = eventType === "panel-start" && tenant !== "sebastian" ? `panel-start-${tenant}` : eventType;
  try {
    const res = await fetch(`https://api.github.com/repos/${repo}/dispatches`, {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "User-Agent": "clips-worker",
      },
      body: JSON.stringify({ event_type: effectiveEventType, client_payload: { tenant, brain_token: brainToken } }),
    });
    await logWorkerEvent(env.DB, "github_dispatch",
      res.ok
        ? `Dispatch ${effectiveEventType} enviado a GitHub (${repo}).`
        : `Dispatch ${effectiveEventType} falló: GitHub devolvió HTTP ${res.status}.`, tenant).catch(() => {});
    // A4: un dispatch rechazado cuenta como fallo para el freno
    // de reintento (solo automáticos). Solo 2xx cuenta como
    // enviado: un 4xx/5xx NO marca el día en maybeDispatchDaily
    // (se reintenta en el siguiente ciclo, con el freno).
    if (!res.ok && automatic) noteDispatchFailure(eventType, Date.now());
    return res.ok;
  } catch (e) {
    if (automatic) noteDispatchFailure(eventType, Date.now());
    await logWorkerEvent(env.DB, "github_dispatch",
      `Dispatch ${effectiveEventType} falló: ${e instanceof Error ? e.message : String(e)}.`, tenant).catch(() => {});
    return false;
  }
}

// Throttle del monitoreo en vivo (2026-11-18, corregido 19:46): como
// mucho 1 dispatch panel-live cada 15 min por tenant. Una
// corrida de cerebro-live dura ~11-13 min (setup + sesión de 10 min)
// y el workflow corre cada 15 min: con 10 min y cancel-in-progress
// la corrida 44381513042 se canceló a los ~9 min por el siguiente
// trigger y NINGUNA terminaba nunca. Además hay DOS fuentes de
// triggers (el cron del workflow y este dispatch), así que antes de
// disparar se consulta si ya hay una corrida en curso o en cola
// (hasLiveRunInFlight): si la hay, se salta este ciclo. El workflow
// ya no cancela corridas en curso (cancel-in-progress: false).
// (LIVE_DISPATCH_MIN_INTERVAL_MS vive en cycle-gate.ts, A3.)
// Anti-spam del aviso de corrida en curso (2026-11-19): el Worker
// corre cada minuto y una corrida larga dejaba decenas de avisos
// idénticos en Actividad reciente. Solo se registra si el último
// aviso igual tiene más de 15 min.
const LIVE_SKIP_LOG_MIN_INTERVAL_MS = 15 * 60 * 1000;
const LIVE_SKIP_PREFIX = "Hay una corrida de cerebro-live";

/** ¿Hay una corrida de cerebro-live en curso para ESTE tenant?
 *  Se mira D1, no GitHub: la consulta a la API de GitHub veía las
 *  corridas de TODOS los tenants y una corrida de sebastian frenaba
 *  los dispatches de kaez (y al revés). La fase la reporta el propio
 *  cerebro (worker_phases, por tenant). Devuelve "busy" (hay
 *  corrida en curso), "clear" (no hay ninguna) o "unknown" (D1
 *  falló: ante la duda no se suma otra corrida a la cola; el motivo
 *  queda registrado). Exportada para tests. */
export async function liveRunStatus(env: Env, tenant: Tenant): Promise<"busy" | "clear" | "unknown"> {
  try {
    return (await liveRunInFlight(env.DB, tenant, Date.now())) ? "busy" : "clear";
  } catch (e) {
    await logWorkerEvent(env.DB, "live_dispatch",
      `No se pudo consultar la fase en vivo de ${tenant}: ${e instanceof Error ? e.message : String(e)}. No se dispara este ciclo.`, tenant);
    return "unknown";
  }
}

/** Dispara panel-live si toca: solo si el Worker de ese tenant
 *  está en marcha (el llamador ya lo comprobó con runWorkerCycle),
 *  pasaron >= 15 min desde el último dispatch Y no hay una corrida
 *  de cerebro-live en curso para ESE tenant (fase en D1, ver
 *  liveRunStatus). Si la hay (o no se pudo consultar), se salta
 *  este ciclo y se reintenta en el siguiente: la cola de GitHub no
 *  crece sin límite. El tenant va en el client_payload del
 *  dispatch, no en el tipo de evento (tipos fijos: panel-live).
 *  No rompe el ciclo: cualquier fallo de D1 queda registrado y el
 *  dispatch se intenta igual (sin throttle) antes que perder el
 *  barrido en vivo. */
async function maybeDispatchLive(env: Env, tenant: Tenant): Promise<void> {
  const now = Date.now();
  let last = 0;
  try {
    const row = await env.DB.prepare(
      "SELECT last_live_dispatch_at FROM worker_state WHERE tenant_id = ?",
    ).bind(tenant).first<{ last_live_dispatch_at: number | null }>();
    last = row?.last_live_dispatch_at ?? 0;
  } catch (e) {
    // Columna aún sin migrar (o D1 sin fila): se dispara igual y el
    // motivo queda en worker_events; la migración 0020 lo arregla.
    await logWorkerEvent(env.DB, "live_dispatch",
      `No se pudo leer last_live_dispatch_at: ${e instanceof Error ? e.message : String(e)}. Se dispara sin throttle.`, tenant);
  }
  if (last > 0 && now - last < LIVE_DISPATCH_MIN_INTERVAL_MS) return;
  const status = await liveRunStatus(env, tenant);
  if (status === "busy") {
    // Anti-spam (2026-11-19): sin esto cada minuto dejaba un aviso
    // idéntico mientras duraba la corrida (37 en la mañana del 19).
    if (!(await hasRecentWorkerEvent(env.DB, tenant, "live_dispatch", LIVE_SKIP_PREFIX, LIVE_SKIP_LOG_MIN_INTERVAL_MS, now))) {
      await logWorkerEvent(env.DB, "live_dispatch",
        `Hay una corrida de cerebro-live en curso para ${tenant}: no se dispara panel-live este ciclo (reintento en el siguiente).`, tenant);
    }
    return;
  }
  if (status === "unknown") {
    // Fallo al consultar D1: no se dispara este ciclo (el motivo
    // exacto ya quedó registrado por liveRunStatus).
    return;
  }
  const attempted = await dispatchCerebro(env, "panel-live", tenant);
  if (!attempted) return;
  try {
    await env.DB.prepare(
      "UPDATE worker_state SET last_live_dispatch_at = ? WHERE tenant_id = ?",
    ).bind(now, tenant).run();
  } catch (e) {
    await logWorkerEvent(env.DB, "live_dispatch",
      `Dispatch enviado pero no se pudo guardar last_live_dispatch_at: ${e instanceof Error ? e.message : String(e)}.`, tenant);
  }
}

// Throttle del vigilante (2026-11-19): como mucho 1 dispatch
// panel-watchdog cada 10 min por tenant. El vigilante corre en
// ~1-2 min y su workflow tiene timeout de 10 min, así que un
// dispatch enviado hace <10 min significa que su corrida sigue
// en curso o en cola: ese mismo evento hace de throttle Y de
// guarda de corrida en curso a la vez. No hay columna
// last_watchdog_dispatch_at en worker_state (eso exigiría una
// migración D1): se lee el último evento "Dispatch panel-watchdog
// enviado" que el propio dispatchCerebro ya deja en worker_events
// (kind "github_dispatch", mismo patrón anti-spam que
// hasRecentWorkerEvent usa para cerebro-live). Un dispatch
// FALLIDO deja otro mensaje ("Dispatch panel-watchdog falló"), así
// que no frena el reintento del ciclo siguiente.
// (WATCHDOG_DISPATCH_MIN_INTERVAL_MS y WATCHDOG_DISPATCHED_PREFIX
// viven en cycle-gate.ts, A3.)
const WATCHDOG_SKIP_PREFIX = "Hay una corrida de cerebro-watchdog";

/** Dispara panel-watchdog si toca: solo si el Worker de ese tenant
 *  está en marcha (el llamador ya lo comprobó con runWorkerCycle),
 *  pasaron >= 10 min desde el último dispatch enviado Y no hay
 *  una corrida de cerebro-watchdog en curso/en cola para ESE tenant
 *  (mismo evento de worker_events: un dispatch de hace <10 min
 *  sigue dentro del timeout de 10 min del workflow). Espejo de
 *  maybeDispatchLive con el mismo patrón; no toca su lógica. */
async function maybeDispatchWatchdog(env: Env, tenant: Tenant): Promise<void> {
  const now = Date.now();
  // hasRecentWorkerEvent falla cerrado a false si D1 falla: en
  // ese caso se dispara sin throttle antes que perder la vigilancia.
  const recent = await hasRecentWorkerEvent(env.DB, tenant, "github_dispatch", WATCHDOG_DISPATCHED_PREFIX, WATCHDOG_DISPATCH_MIN_INTERVAL_MS, now);
  if (recent) {
    // Anti-spam (mismo patrón que cerebro-live): sin esto cada
    // minuto dejaría un aviso idéntico mientras dura la corrida.
    if (!(await hasRecentWorkerEvent(env.DB, tenant, "watchdog_dispatch", WATCHDOG_SKIP_PREFIX, WATCHDOG_DISPATCH_MIN_INTERVAL_MS, now))) {
      await logWorkerEvent(env.DB, "watchdog_dispatch",
        `Hay una corrida de cerebro-watchdog en curso para ${tenant}: no se dispara panel-watchdog este ciclo (reintento en el siguiente).`, tenant);
    }
    return;
  }
  await dispatchCerebro(env, "panel-watchdog", tenant);
}

// --- Influencers: validación y serialización (regla de lanzamiento) ---
const INFLUENCER_PLATFORMS = ["twitch", "youtube", "kick"] as const;
const INFLUENCER_LANGUAGES = ["es", "en"] as const;

/** Serializa una fila de influencers: `handle`/`url` son los nombres
 *  que usa la API (la BD guarda `name`/`profile_url`); `permission`
 *  y `active` salen como booleanos. `game` es la categoría
 *  ("GTA VI"), `permission_date` la fecha del permiso y
 *  `publish_platforms` la lista de plataformas de publicación. */
function influencerOut(r: InfluencerRow) {
  let publishPlatforms: string[] = ["youtube", "tiktok", "instagram", "facebook"];
  try {
    const parsed: unknown = JSON.parse(r.publish_platforms || "[]");
    if (Array.isArray(parsed) && parsed.every((p) => typeof p === "string")) publishPlatforms = parsed as string[];
  } catch {
    publishPlatforms = ["youtube", "tiktok", "instagram", "facebook"];
  }
  return {
    id: r.id,
    handle: r.name,
    name: r.name,
    platform: r.platform,
    url: r.profile_url,
    profile_url: r.profile_url,
    language: r.language,
    permission: r.permission === 1,
    active: r.active === 1,
    notes: r.notes,
    created_at: r.created_at,
    game: r.game || GTA_VI_GAME,
    permission_date: r.permission_date || "",
    publish_platforms: publishPlatforms,
  };
}

/** Plataformas de publicación válidas (claves API, no del panel). */
const PUBLISH_PLATFORMS = ["youtube", "tiktok", "instagram", "facebook"] as const;

function parsePublishPlatforms(v: unknown): string[] | string {
  if (v === undefined) return ["youtube", "tiktok", "instagram", "facebook"];
  if (!Array.isArray(v) || v.length === 0) return "publish_platforms debe ser una lista con al menos una plataforma.";
  const list: string[] = [];
  for (const p of v) {
    if (typeof p !== "string" || !(PUBLISH_PLATFORMS as readonly string[]).includes(p.trim().toLowerCase())) {
      return "Plataforma inválida en publish_platforms: usa youtube, tiktok, instagram o facebook.";
    }
    list.push(p.trim().toLowerCase());
  }
  return [...new Set(list)];
}

function parsePermissionDate(v: unknown): string | null {
  if (v === undefined) return "";
  if (typeof v !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(v.trim())) return null;
  return v.trim();
}

/** Acepta booleanos JSON (true/false) o 0/1; cualquier otra cosa no
 *  es un valor válido para permission/active. */
function asBool01(v: unknown): number | null {
  if (v === true || v === 1) return 1;
  if (v === false || v === 0) return 0;
  return null;
}

function validUrl(v: string): boolean {
  return v === "" || v.startsWith("http://") || v.startsWith("https://");
}

/** Valida el cuerpo de POST /api/influencers. Devuelve el objeto
 *  listo para crear, o el mensaje de error (string) con 400. */
function parseInfluencerInput(body: Record<string, unknown>): NewInfluencer | string {
  const name = typeof body.handle === "string" ? body.handle.trim() : typeof body.name === "string" ? body.name.trim() : "";
  if (!name) return "Falta el handle del influencer.";
  if (name.length > 100) return "El handle no puede superar 100 caracteres.";
  const platform = typeof body.platform === "string" ? body.platform.trim().toLowerCase() : "";
  if (!(INFLUENCER_PLATFORMS as readonly string[]).includes(platform)) {
    return "Plataforma inválida: usa twitch, youtube o kick.";
  }
  const profileUrl = typeof body.url === "string" ? body.url.trim() : typeof body.profile_url === "string" ? body.profile_url.trim() : "";
  if (!validUrl(profileUrl)) return "La URL del perfil debe empezar por http:// o https://.";
  const language = typeof body.language === "string" ? body.language.trim().toLowerCase() : "es";
  if (!(INFLUENCER_LANGUAGES as readonly string[]).includes(language)) {
    return "Idioma inválido: usa es o en.";
  }
  const permission = body.permission === undefined ? 0 : asBool01(body.permission);
  if (permission === null) return "permission debe ser true o false.";
  const active = body.active === undefined ? 1 : asBool01(body.active);
  if (active === null) return "active debe ser true o false.";
  const notes = typeof body.notes === "string" ? body.notes : "";
  if (notes.length > 2000) return "Las notas no pueden superar 2000 caracteres.";
  // Categoría (0018): este panel es solo GTA VI; sin game se pone
  // GTA VI. La fecha del permiso y las plataformas son opcionales
  // (defaults: "" y las 4).
  if (body.game !== undefined && (typeof body.game !== "string" || !isGtaViGame(body.game))) {
    return "Categoría no permitida: solo GTA VI.";
  }
  const permissionDate = parsePermissionDate(body.permission_date);
  if (permissionDate === null) return "permission_date debe ser una fecha YYYY-MM-DD.";
  const platforms = parsePublishPlatforms(body.publish_platforms);
  if (typeof platforms === "string") return platforms;
  return { name, platform, profile_url: profileUrl, language, permission, active, notes, game: GTA_VI_GAME, permission_date: permissionDate, publish_platforms: JSON.stringify(platforms) };
}

/** Valida el cuerpo de PATCH /api/influencers/:id. Solo los campos
 *  presentes se actualizan; un campo presente pero inválido es 400. */
function parseInfluencerPatch(body: Record<string, unknown>): Partial<NewInfluencer> | string {
  const patch: Partial<NewInfluencer> = {};
  if (body.handle !== undefined || body.name !== undefined) {
    const name = typeof body.handle === "string" ? body.handle.trim() : typeof body.name === "string" ? body.name.trim() : "";
    if (!name) return "Falta el handle del influencer.";
    if (name.length > 100) return "El handle no puede superar 100 caracteres.";
    patch.name = name;
  }
  if (body.platform !== undefined) {
    const platform = typeof body.platform === "string" ? body.platform.trim().toLowerCase() : "";
    if (!(INFLUENCER_PLATFORMS as readonly string[]).includes(platform)) {
      return "Plataforma inválida: usa twitch, youtube o kick.";
    }
    patch.platform = platform;
  }
  if (body.url !== undefined || body.profile_url !== undefined) {
    const profileUrl = typeof body.url === "string" ? body.url.trim() : typeof body.profile_url === "string" ? body.profile_url.trim() : "";
    if (!validUrl(profileUrl)) return "La URL del perfil debe empezar por http:// o https://.";
    patch.profile_url = profileUrl;
  }
  if (body.language !== undefined) {
    const language = typeof body.language === "string" ? body.language.trim().toLowerCase() : "";
    if (!(INFLUENCER_LANGUAGES as readonly string[]).includes(language)) {
      return "Idioma inválido: usa es o en.";
    }
    patch.language = language;
  }
  if (body.permission !== undefined) {
    const permission = asBool01(body.permission);
    if (permission === null) return "permission debe ser true o false.";
    patch.permission = permission;
  }
  if (body.active !== undefined) {
    const active = asBool01(body.active);
    if (active === null) return "active debe ser true o false.";
    patch.active = active;
  }
  if (body.notes !== undefined) {
    if (typeof body.notes !== "string") return "notes debe ser texto.";
    if (body.notes.length > 2000) return "Las notas no pueden superar 2000 caracteres.";
    patch.notes = body.notes;
  }
  if (body.game !== undefined) {
    if (typeof body.game !== "string" || !isGtaViGame(body.game)) {
      return "Categoría no permitida: solo GTA VI.";
    }
    patch.game = GTA_VI_GAME;
  }
  if (body.permission_date !== undefined) {
    const d = parsePermissionDate(body.permission_date);
    if (d === null) return "permission_date debe ser una fecha YYYY-MM-DD.";
    patch.permission_date = d;
  }
  if (body.publish_platforms !== undefined) {
    const platforms = parsePublishPlatforms(body.publish_platforms);
    if (typeof platforms === "string") return platforms;
    patch.publish_platforms = JSON.stringify(platforms);
  }
  return patch;
}

// --- Editor / Analíticas / Canales (fase 2, Dev 2) ---
const SUBTITLE_STYLES = ["simple", "tiktok", "highlight", "karaoke"] as const; // "simple" = RDJavi (def. 2026-11-18)
const STAT_WINDOWS = ["24h", "48h", "7d"] as const;
const CHANNEL_PLATFORMS: PlatformKey[] = ["youtube", "tiktok", "instagram", "facebook"];
// Pasos canónicos del pipeline de tracking (diseño fase 2,
// diseno-canales-tracking-usuarios.md §2.5): el front mapea por
// igualdad exacta con esta lista; "aviso" no es un paso del
// pipeline y va a "Otros eventos".
const TRACKING_STEPS: string[] = [
  "detectado",
  "pico",
  "admision",
  "cola_corte",
  "cortando",
  "subtitulos",
  "paquete",
  "revision",
  "decision",
  "cola_publicacion",
  "publicado",
  "aviso",
] as const;
const API_TO_PANEL: Record<PlatformKey, PanelPlatform> = {
  youtube: "youtube_shorts",
  tiktok: "tiktok",
  instagram: "instagram_reels",
  facebook: "facebook_reels",
};
// Topes diarios y plan (2026-11-19): YA NO son constantes fijas.
// Viven en Ajustes (D1 settings por tenant, defaults por
// plataforma TikTok 2/3, Instagram 1/2, YouTube 1/2, Facebook 1/2,
// calentamiento 1/día, 3 h entre subidas — 4 h en YouTube) y el
// Worker los lee con effectiveSettings en publishGate, /api/channels
// y /api/bridge/settings. La madurez de la cuenta (0017, reglas
// 2026-11-19: nueva si <14 días o <20 publicaciones) puede
// bajarlos a 1/día. FRANJAS HORARIAS (reglas 2026-11-19, hora
// Chile): la publicación automática solo sale 12:00-14:00,
// 18:00-20:00 o 21:00-23:00. La decisión 2026-11-18 "sin franja
// horaria, se publica a cualquier hora" queda REVOCADA.
const PUBLISH_WINDOW = "franjas Chile 12:00–14:00, 18:00–20:00, 21:00–23:00";

/** Normaliza la plataforma de una medición a clave API
 *  (youtube | tiktok | instagram | facebook); acepta también los
 *  nombres del panel (youtube_shorts, instagram_reels…). */
function normalizeStatPlatform(v: unknown): PlatformKey | null {
  if (typeof v !== "string") return null;
  const s = v.trim().toLowerCase();
  if (s === "youtube" || s === "youtube_shorts") return "youtube";
  if (s === "tiktok") return "tiktok";
  if (s === "instagram" || s === "instagram_reels") return "instagram";
  if (s === "facebook" || s === "facebook_reels") return "facebook";
  return null;
}

/** Mediana de una lista de números; null si no hay ninguno (sin
 *  medición: nunca 0). */
function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) return sorted[mid] ?? null;
  const a = sorted[mid - 1];
  const b = sorted[mid];
  if (a === undefined || b === undefined) return null;
  return (a + b) / 2;
}

/** Serializa una medición: `subscribers` es alias de `new_subs`
 *  (la columna se llama new_subs; el formulario habla de
 *  "nuevos seguidores"). */
function statOut(r: ClipStatRow) {
  return {
    id: r.id,
    clip_id: r.clip_id,
    platform: r.platform,
    views: r.views,
    retention_pct: r.retention_pct,
    shares: r.shares,
    saves: r.saves,
    comments: r.comments,
    new_subs: r.new_subs,
    subscribers: r.new_subs,
    window_label: r.window_label,
    recorded_at: r.recorded_at,
  };
}

/** Serializa un clip para la API: `viral` sale parseado (objeto, no
 *  string) y se exponen arriba los campos de puntuación IA
 *  (contrato 2026-11-18): seo_scores, viral_breakdown, hype_score,
 *  priority, score_kind y thumb_text. Sin dato, null = "sin medir"
 *  en el front; nunca se inventa un número. */
function clipOut(c: ClipRow) {
  const viral = parseViralJson(c.viral);
  return {
    ...c,
    viral,
    thumb_text: c.thumb_text ?? "",
    score_kind: viral.score_kind ?? null,
    viral_score: typeof viral.viral_score === "number" ? viral.viral_score : null,
    viral_breakdown: viral.viral_breakdown ?? null,
    hype_score: typeof viral.hype_score === "number" ? viral.hype_score : null,
    priority: typeof viral.priority === "number" ? viral.priority : null,
    seo_scores: viral.seo_scores ?? null,
  };
}

/** Valida el cuerpo de POST /api/clips/:id/stats. Solo se guarda lo
 *  pegado: los opcionales ausentes quedan NULL (sin medir), nunca 0. */
function parseStatInput(body: Record<string, unknown>): Omit<NewClipStat, "clip_id"> | string {
  const platform = normalizeStatPlatform(body.platform);
  if (!platform) return "Plataforma inválida: usa youtube, tiktok, instagram o facebook.";
  if (typeof body.views !== "number" || !Number.isInteger(body.views) || body.views < 0) {
    return "Escribe las vistas: un número entero mayor o igual a 0.";
  }
  const retentionRaw = body.retention !== undefined ? body.retention : body.retention_pct;
  let retention: number | null = null;
  if (retentionRaw !== undefined && retentionRaw !== null) {
    if (typeof retentionRaw !== "number" || !Number.isFinite(retentionRaw) || retentionRaw < 0 || retentionRaw > 100) {
      return "La retención va de 0 a 100 %.";
    }
    retention = retentionRaw;
  }
  const optInt = (v: unknown, label: string): number | null | string => {
    if (v === undefined || v === null) return null;
    if (typeof v !== "number" || !Number.isInteger(v) || v < 0) return `${label} debe ser un número entero mayor o igual a 0.`;
    return v;
  };
  const shares = optInt(body.shares, "shares");
  if (typeof shares === "string") return shares;
  const saves = optInt(body.saves, "saves");
  if (typeof saves === "string") return saves;
  const comments = optInt(body.comments, "comments");
  if (typeof comments === "string") return comments;
  const subs = optInt(body.subscribers !== undefined ? body.subscribers : body.new_subs, "subscribers");
  if (typeof subs === "string") return subs;
  const windowLabel = body.window === undefined ? "7d" : typeof body.window === "string" ? body.window.trim() : "";
  if (!(STAT_WINDOWS as readonly string[]).includes(windowLabel)) {
    return "Ventana inválida: usa 24h, 48h o 7d.";
  }
  let recordedAt = Date.now();
  if (body.recorded_at !== undefined && body.recorded_at !== null) {
    if (typeof body.recorded_at === "number" && Number.isFinite(body.recorded_at)) {
      recordedAt = Math.trunc(body.recorded_at);
    } else if (typeof body.recorded_at === "string") {
      const ms = Date.parse(body.recorded_at);
      if (!Number.isFinite(ms)) return "Fecha de la medición inválida.";
      recordedAt = ms;
    } else {
      return "Fecha de la medición inválida.";
    }
  }
  return { platform, views: body.views, retention_pct: retention, shares, saves, comments, new_subs: subs, window_label: windowLabel, recorded_at: recordedAt };
}

interface ClipPatchResult {
  columns: Record<string, string | number | null>;
  duration: number | null; // segundos; null = no se cambia
}

/** Valida el cuerpo de PATCH /api/clips/:id. Solo los campos
 *  presentes se actualizan; un campo presente pero inválido es 400.
 *  La duración se valida sin mínimo ni rango prohibido (regla
 *  40-60 / 25-35 eliminada el 2026-11-21): solo el tope de 60 s.
 *  M2 (Frente 2): `seo_override: {platform, enabled}` activa o
 *  quita el override SEO de una plataforma. Solo vale con SEO
 *  medido entre 70 y 89: con 90 o más no hace falta (ya pasa la
 *  puerta) y con menos de 70 es demasiado bajo para anular. Sin
 *  medición no hay nada que anular. */
function parseClipPatch(body: Record<string, unknown>, clip?: ClipRow, rawSettings: Record<string, unknown> = {}): ClipPatchResult | string {
  const eff = effectiveSettings(rawSettings);
  const columns: Record<string, string | number | null> = {};
  let duration: number | null = null;
  if (body.subtitle_style !== undefined) {
    const s = typeof body.subtitle_style === "string" ? body.subtitle_style.trim().toLowerCase() : "";
    // "ref" es el nombre de Ajustes para el estilo simple RDJavi;
    // en el clip se guarda como "simple" (nombre de la columna).
    const mapped = s === "ref" ? "simple" : s;
    if (!(SUBTITLE_STYLES as readonly string[]).includes(mapped)) {
      return "Estilo de subtítulos inválido: usa simple (o ref, el simple RDJavi), tiktok, highlight o karaoke.";
    }
    columns.subtitle_style = mapped;
  }
  // Regla 2026-11-17 22:10: SIEMPRE subtítulos completos. El repo
  // marca publish_blocked / needs_human_transcription /
  // subtitles_complete al subtitular; el usuario puede quitar el
  // bloqueo solo tras poner los subtítulos completos.
  for (const key of ["publish_blocked", "needs_human_transcription", "subtitles_complete"] as const) {
    if (body[key] !== undefined) {
      const v = body[key];
      if (v !== 0 && v !== 1 && v !== true && v !== false) return `${key} debe ser 0 o 1.`;
      columns[key] = v === 1 || v === true ? 1 : 0;
    }
  }
  // Gate de categoría GTA VI: el juego solo puede ser GTA VI.
  if (body.game !== undefined) {
    if (typeof body.game !== "string" || !isGtaViGame(body.game)) {
      return "Categoría no permitida: solo GTA VI.";
    }
    columns.game = GTA_VI_GAME;
  }
  // Idioma del clip (0019, DOS COLAS SEPARADAS): "es" | "en".
  // Corregir el idioma mueve el clip de cola; cualquier otro valor
  // es 400. Cambiar el idioma NO re-publica nada: los jobs ya
  // encolados guardan su propio idioma y el Worker los frena si
  // su cuenta ya no coincide (ver publish/index.ts).
  if (body.language !== undefined) {
    const lang = parseClipLanguage(body.language);
    if (lang !== "es" && lang !== "en") return lang;
    columns.language = lang;
  }
  const textField = (key: string, max: number, label: string): string | null => {
    if (body[key] === undefined) return null;
    const v = body[key];
    if (typeof v !== "string") return `${label} debe ser texto.`;
    if (v.length > max) return `${label} no puede superar ${max} caracteres.`;
    columns[key] = v;
    return null;
  };
  const seoTitleErr = textField("seo_title", 200, "seo_title");
  if (seoTitleErr) return seoTitleErr;
  const seoDescriptionErr = textField("seo_description", 5000, "seo_description");
  if (seoDescriptionErr) return seoDescriptionErr;
  if (body.seo_tags !== undefined) {
    if (typeof body.seo_tags === "string") {
      if (body.seo_tags.length > 2000) return "seo_tags no puede superar 2000 caracteres.";
      columns.seo_tags = body.seo_tags;
    } else if (Array.isArray(body.seo_tags) && body.seo_tags.every((t) => typeof t === "string")) {
      const joined = (body.seo_tags as string[]).join(", ");
      if (joined.length > 2000) return "seo_tags no puede superar 2000 caracteres.";
      columns.seo_tags = joined;
    } else {
      return "seo_tags debe ser texto o una lista de textos.";
    }
  }
  if (body.thumbnail_text !== undefined || body.thumb_text !== undefined) {
    const v = body.thumbnail_text !== undefined ? body.thumbnail_text : body.thumb_text;
    if (typeof v !== "string") return "thumbnail_text debe ser texto.";
    if (v.length > 200) return "thumbnail_text no puede superar 200 caracteres.";
    columns.thumb_text = v;
  }
  if (body.target_platforms !== undefined || body.platforms !== undefined) {
    const raw = body.target_platforms !== undefined ? body.target_platforms : body.platforms;
    if (!Array.isArray(raw)) return "target_platforms debe ser una lista de plataformas.";
    const list: string[] = [];
    for (const p of raw) {
      if (typeof p !== "string" || !(PANEL_PLATFORMS as readonly string[]).includes(p.trim())) {
        return "Plataforma inválida en target_platforms: usa youtube_shorts, tiktok, instagram_reels o facebook_reels.";
      }
      list.push(p.trim());
    }
    columns.platforms = JSON.stringify(list);
  }
  if (body.schedule !== undefined || body.scheduled_for !== undefined) {
    const raw = body.schedule !== undefined ? body.schedule : body.scheduled_for;
    if (raw === null || raw === "") {
      columns.scheduled_for = null;
    } else if (typeof raw === "string") {
      if (!Number.isFinite(Date.parse(raw.trim()))) return "schedule debe ser una fecha válida (ISO 8601) o vacío para quitarla.";
      columns.scheduled_for = raw.trim();
    } else {
      return "schedule debe ser una fecha (ISO 8601) o vacío para quitarla.";
    }
  }
  if (body.duration !== undefined) {
    const d = body.duration;
    if (typeof d !== "number" || !Number.isFinite(d) || !Number.isInteger(d)) {
      return "La duración debe ser un número entero de segundos.";
    }
    if (d < eff.min_duration_s) return `La duración está por debajo del mínimo de ${eff.min_duration_s} s.`;
    if (d > eff.max_duration_s) return `La duración supera el tope de ${eff.max_duration_s} s.`;
    duration = d;
  }
  if (body.seo_override !== undefined) {
    if (!clip) return "seo_override necesita el clip.";
    const o = body.seo_override;
    if (typeof o !== "object" || o === null || Array.isArray(o)) {
      return "seo_override debe ser {platform, enabled}.";
    }
    const platform = typeof (o as Record<string, unknown>).platform === "string"
      ? ((o as Record<string, unknown>).platform as string).trim()
      : "";
    if (!(PANEL_PLATFORMS as readonly string[]).includes(platform)) {
      return "Plataforma inválida en seo_override: usa youtube_shorts, tiktok, instagram_reels o facebook_reels.";
    }
    const enabled = (o as Record<string, unknown>).enabled;
    if (typeof enabled !== "boolean") return "seo_override.enabled debe ser true o false.";
    const panel = platform as PanelPlatform;
    const { score } = seoForPlatform(clip, panel);
    if (enabled) {
      if (score === null) return `Sin medición SEO en ${platform}: no hay nada que anular.`;
      if (score >= 90) return `SEO ${score} en ${platform}: ya pasa la puerta de 90, no necesita override.`;
      if (score < 70) return `SEO ${score} en ${platform}: demasiado bajo para override (solo 70–89).`;
    }
    let current: string[] = [];
    try {
      const parsed = JSON.parse(clip.seo_overrides || "[]") as unknown;
      if (Array.isArray(parsed)) {
        current = parsed
          .map((v) => (typeof v === "string" ? v : typeof v === "object" && v !== null ? (v as { platform?: string }).platform ?? "" : ""))
          .filter((v): v is string => typeof v === "string" && v !== "");
      }
    } catch {
      current = [];
    }
    const next = enabled
      ? [...new Set([...current, panel])]
      : current.filter((p) => p !== panel);
    columns.seo_overrides = JSON.stringify(next);
  }
  // Puntuación IA (contrato 2026-11-18): `viral` o los campos sueltos
  // (seo_scores, viral_score, viral_breakdown, hype_score, priority,
  // score_kind) se fusionan con el JSON guardado, no lo reemplazan
  // entero: afinar el SEO no borra el hype ni la prioridad.
  if (body.viral !== undefined || body.seo_scores !== undefined || body.viral_score !== undefined || body.viral_breakdown !== undefined || body.hype_score !== undefined || body.priority !== undefined || body.score_kind !== undefined) {
    const parsed = parseViralInput(body);
    if (typeof parsed === "string") return parsed;
    columns.viral = mergeViralJson(clip ? clip.viral : "{}", parsed);
  }
  return { columns, duration };
}

/** Valida el cuerpo de POST /api/clips (crear clip en la Cola).
 *  Duración sin mínimo ni rango prohibido (regla 40-60 / 25-35
 *  eliminada el 2026-11-21, para siempre). platforms es
 *  obligatoria (al menos una de las 4 del panel). publish_blocked
 *  se deriva de subtitles_complete (regla 2026-11-17 22:10: sin
 *  subtítulos completos no se publica): solo nace desbloqueado si
 *  el que lo encola declara subtitles_complete=1 (MP4 ya quemado).
 *  Excepción — rescate por gritos (2026-11-18, decisión del
 *  usuario): viralidad revisada 45-49 + 2+ s de gritos + SEO >= 90
 *  entra BLOQUEADO (publish_blocked=1) para revisión humana; la
 *  puerta 50 de publicación no se toca.
 *  Devuelve el objeto listo para crear, o el mensaje de error. */
export function parseClipInput(body: Record<string, unknown>, rawSettings: Record<string, unknown> = {}): NewClip | string {
  const eff = effectiveSettings(rawSettings);
  const title = typeof body.title === "string" ? body.title.trim() : "";
  if (!title) return "Falta el título del clip.";
  if (title.length > 200) return "El título no puede superar 200 caracteres.";
  // Idioma del clip (0019, DOS COLAS SEPARADAS): "es" | "en".
  // Sin idioma, "es" (clips heredados). Otro valor es 400: el
  // cerebro debe mandar es o en, nunca inventar un tercero.
  const language = parseClipLanguage(body.language);
  if (language !== "es" && language !== "en") return language;
  const startSec = body.start_sec;
  const endSec = body.end_sec;
  if (typeof startSec !== "number" || !Number.isInteger(startSec) || startSec < 0) {
    return "start_sec debe ser un número entero mayor o igual a 0 (segundo del VOD).";
  }
  if (typeof endSec !== "number" || !Number.isInteger(endSec) || endSec <= startSec) {
    return "end_sec debe ser un número entero mayor que start_sec.";
  }
  // Gate de categoría GTA VI (decisión del usuario 2026-11-18):
  // este panel es SOLO GTA VI. Sin categoría, o con otro juego
  // (GTA V, GTA 5...), el clip no entra en la Cola.
  const gameRaw = body.game;
  if (typeof gameRaw !== "string" || !gameRaw.trim()) {
    return "Falta la categoría: este panel es solo GTA VI. Pasa game: \"GTA VI\".";
  }
  if (!isGtaViGame(gameRaw)) {
    return `Categoría no permitida: solo GTA VI. Recibido: "${gameRaw.trim()}".`;
  }
  const duration = endSec - startSec;
  // Sin mínimo ni rango prohibido desde 2026-11-21; mínimo/máximo
  // desde Ajustes (defaults 1–60 s). Los mensajes dicen los valores
  // vigentes.
  if (duration < eff.min_duration_s) return `La duración está por debajo del mínimo de ${eff.min_duration_s} s.`;
  if (duration > eff.max_duration_s) return `La duración supera el tope de ${eff.max_duration_s} s.`;
  if (!Array.isArray(body.platforms) || body.platforms.length === 0) {
    return "platforms debe ser una lista con al menos una plataforma.";
  }
  const platforms: string[] = [];
  for (const p of body.platforms) {
    if (typeof p !== "string" || !(PANEL_PLATFORMS as readonly string[]).includes(p.trim())) {
      return "Plataforma inválida en platforms: usa youtube_shorts, tiktok, instagram_reels o facebook_reels.";
    }
    platforms.push(p.trim());
  }
  const textOrError = (key: string, max: number, label: string): { value: string } | { error: string } => {
    if (body[key] === undefined) return { value: "" };
    const v = body[key];
    if (typeof v !== "string") return { error: `${label} debe ser texto.` };
    if (v.length > max) return { error: `${label} no puede superar ${max} caracteres.` };
    return { value: v };
  };
  const seoTitleR = textOrError("seo_title", 200, "seo_title");
  if ("error" in seoTitleR) return seoTitleR.error;
  const seoTitle = seoTitleR.value;
  const seoDescriptionR = textOrError("seo_description", 5000, "seo_description");
  if ("error" in seoDescriptionR) return seoDescriptionR.error;
  const seoDescription = seoDescriptionR.value;
  const reasonR = textOrError("reason", 2000, "reason");
  if ("error" in reasonR) return reasonR.error;
  const reason = reasonR.value;
  let seoTags = "";
  if (body.seo_tags !== undefined) {
    if (typeof body.seo_tags === "string") {
      if (body.seo_tags.length > 2000) return "seo_tags no puede superar 2000 caracteres.";
      seoTags = body.seo_tags;
    } else if (Array.isArray(body.seo_tags) && body.seo_tags.every((t) => typeof t === "string")) {
      const joined = (body.seo_tags as string[]).join(", ");
      if (joined.length > 2000) return "seo_tags no puede superar 2000 caracteres.";
      seoTags = joined;
    } else {
      return "seo_tags debe ser texto o una lista de textos.";
    }
  }
  let thumbText = "";
  if (body.thumbnail_text !== undefined || body.thumb_text !== undefined) {
    const v = body.thumbnail_text !== undefined ? body.thumbnail_text : body.thumb_text;
    if (typeof v !== "string") return "thumbnail_text debe ser texto.";
    if (v.length > 200) return "thumbnail_text no puede superar 200 caracteres.";
    thumbText = v;
  }
  // Estilo por defecto desde Ajustes (default "ref" = simple RDJavi
  // en el clip; en la columna del clip se guarda "simple").
  let subtitleStyle: string = eff.subtitle_style === "ref" ? "simple" : eff.subtitle_style;
  if (body.subtitle_style !== undefined) {
    const s = typeof body.subtitle_style === "string" ? body.subtitle_style.trim().toLowerCase() : "";
    const mapped = s === "ref" ? "simple" : s;
    if (!(SUBTITLE_STYLES as readonly string[]).includes(mapped)) {
      return "Estilo de subtítulos inválido: usa simple (o ref, el simple RDJavi), tiktok, highlight o karaoke.";
    }
    subtitleStyle = mapped;
  }
  // source_id / influencer_id llegan como número o como texto
  // numérico ("2928823801", como en la ficha del clip): ambos valen.
  // Un handle ("rubius") NO es un id: se resuelve en el handler
  // de POST /api/clips antes de llegar aquí.
  const optIntId = (key: string): number | null | string => {
    if (body[key] === undefined || body[key] === null) return null;
    const v = body[key];
    if (typeof v === "number" && Number.isInteger(v) && v >= 0) return v;
    if (typeof v === "string" && /^\d+$/.test(v.trim())) return Number(v.trim());
    return `${key} debe ser un número entero mayor o igual a 0.`;
  };
  const sourceId = optIntId("source_id");
  if (typeof sourceId === "string") return sourceId;
  const influencerId = optIntId("influencer_id");
  if (typeof influencerId === "string") return influencerId;
  // Puntuación IA (contrato 2026-11-18): seo_scores, viral_score,
  // viral_breakdown, hype_score, priority, score_kind o el objeto
  // `viral` entero. Sin estos campos el clip nace sin medir. Se
  // parsea ANTES de la puerta de viralidad: el rescate la necesita.
  const viralParsed = parseViralInput(body);
  if (typeof viralParsed === "string") return viralParsed;
  // RESCATE por gritos del streamer (2026-11-18, decisión del
  // usuario): un clip con viralidad revisada 45-49, con 2+ segundos
  // de gritos del streamer y SEO >= 90, entra a la Cola BLOQUEADO
  // para revisión humana, nunca auto-publicable. El cerebro lo marca
  // con rescue: true y manda la viralidad revisada (viral_score) y
  // los seo_scores medidos; el panel lo re-verifica todo, no se fía
  // de la marca sola. Sin rescate, la puerta 45 sigue intacta.
  // OJO: `score` es la PRIORIDAD de YouTube que manda el cerebro,
  // no la viralidad (medido 2026-11-18: prioridad 45-46 con viralidad
  // revisada 48,3-49,0, y el 400 los tiraba a los dos).
  // El rescate cubre la banda de 5 puntos JUSTO DEBAJO del mínimo
  // (antes 45–49 con mínimo 50; ahora 40–44 con mínimo 45, puerta
  // única P0 #3): se deriva del mínimo para que el rango nunca se
  // quede vacío si la puerta cambia.
  const RESCUE_VIRAL_MIN = eff.min_virality - 5;
  const isRescueClaim = body.rescue === true;
  const rescueShouts = typeof body.rescue_shouts === "number" && Number.isInteger(body.rescue_shouts) ? body.rescue_shouts : 0;
  const rescueSeoMax = viralParsed.seo_scores && Object.keys(viralParsed.seo_scores).length > 0
    ? Math.max(...Object.values(viralParsed.seo_scores))
    : null;
  const rescueViral = typeof viralParsed.viral_score === "number" ? viralParsed.viral_score : null;
  const isRescue = isRescueClaim
    && rescueViral !== null && rescueViral >= RESCUE_VIRAL_MIN && rescueViral < eff.min_virality
    && rescueShouts >= 2
    && rescueSeoMax !== null && rescueSeoMax >= eff.min_seo;
  if (isRescueClaim && !isRescue) {
    return `Rescate no válido: necesita viralidad revisada ${RESCUE_VIRAL_MIN}–${eff.min_virality - 1} (recibida ${rescueViral ?? "sin medir"}), 2+ segundos de gritos (recibidos ${rescueShouts}) y SEO máximo >= ${eff.min_seo} (recibido ${rescueSeoMax ?? "sin medir"}).`;
  }
  let score = 0;
  if (body.score !== undefined) {
    if (typeof body.score !== "number" || !Number.isInteger(body.score) || body.score < 0) {
      return "score debe ser un número entero mayor o igual a 0.";
    }
    // Viralidad mínima desde Ajustes (default 45): un clip que no
    // llega no entra en la Cola, salvo rescate válido (arriba).
    if (body.score < eff.min_virality && !isRescue) {
      return `Viralidad ${body.score}: bajo el mínimo de ${eff.min_virality} en Ajustes. Busca un momento más fuerte.`;
    }
    score = body.score;
  }
  let scheduledFor: string | null = null;
  if (body.schedule !== undefined || body.scheduled_for !== undefined) {
    const raw = body.schedule !== undefined ? body.schedule : body.scheduled_for;
    if (raw === null || raw === "") {
      scheduledFor = null;
    } else if (typeof raw === "string") {
      if (!Number.isFinite(Date.parse(raw.trim()))) return "schedule debe ser una fecha válida (ISO 8601) o vacío.";
      scheduledFor = raw.trim();
    } else {
      return "schedule debe ser una fecha (ISO 8601) o vacío.";
    }
  }
  const asBool01Clip = (key: string): number | string => {
    const v = body[key];
    if (v === undefined) return 0;
    if (v === 1 || v === true) return 1;
    if (v === 0 || v === false) return 0;
    return `${key} debe ser 0 o 1.`;
  };
  const subtitlesComplete = asBool01Clip("subtitles_complete");
  if (typeof subtitlesComplete === "string") return subtitlesComplete;
  const needsHuman = asBool01Clip("needs_human_transcription");
  if (typeof needsHuman === "string") return needsHuman;
  // Puerta SEO en la creación (2026-11-18, parte del panel): si el
  // POST trae seo_scores medidos (sueltos o dentro de `viral`) y el
  // MÁXIMO es < min_seo de Ajustes (default 80), el clip no entra
  // en la Cola salvo override explícito (seo_override: true). Sin
  // seo_scores (clip heredado sin medir) NO se rechaza por SEO: la
  // puerta de viralidad de arriba decide, y el SEO se mide luego
  // (PATCH) o lo frena el gate de publicación.
  if (viralParsed.seo_scores && Object.keys(viralParsed.seo_scores).length > 0) {
    const maxSeo = Math.max(...Object.values(viralParsed.seo_scores));
    const seoOverride = body.seo_override === true;
    if (maxSeo < eff.min_seo && !seoOverride) {
      return `SEO máximo ${maxSeo}: bajo el mínimo de ${eff.min_seo} en Ajustes. Busca un momento con mejor SEO o pasa seo_override: true para forzarlo.`;
    }
  }
  let viralJson = Object.keys(viralParsed).length ? mergeViralJson("{}", viralParsed) : "{}";
  if (isRescue) {
    viralJson = mergeViralJson(viralJson, { rescue: { viral_revisado: rescueViral as number, shouts: rescueShouts } });
  }
  return {
    source_id: sourceId,
    influencer_id: influencerId,
    title,
    start_sec: startSec,
    end_sec: endSec,
    score,
    reason,
    subtitle_style: subtitleStyle,
    seo_title: seoTitle,
    seo_description: seoDescription,
    seo_tags: seoTags,
    thumb_text: thumbText,
    platforms: JSON.stringify(platforms),
    scheduled_for: scheduledFor,
    // Rescatado: nace bloqueado aunque tenga subtítulos completos;
    // solo el usuario lo desbloquea tras revisarlo (PATCH
    // publish_blocked). Sin rescate, como siempre: bloqueado solo
    // si no hay subtítulos completos.
    publish_blocked: isRescue ? 1 : (subtitlesComplete === 1 ? 0 : 1),
    needs_human_transcription: needsHuman,
    game: GTA_VI_GAME,
    // Idioma (0019): decide la cola (español / inglés) y las
    // cuentas en las que se puede publicar (ver publishGate).
    language,
    // origin lo pasa el que encola ('live' = cortador en vivo);
    // cualquier otro valor es VOD (ver db.createClip).
    origin: body.origin === "live" ? "live" : "vod",
    subtitles_complete: subtitlesComplete,
    viral: viralJson,
  };
}

/** Hito viral honesto (B6, v3): el hito se detecta cuando se
 *  registra una medición manual (POST /api/clips/:id/stats). Las
 *  mediciones AUTOMÁTICAS (collectAnalytics) guardan en la misma
 *  tabla pero aún no disparan el hito: hueco conocido, no un
 *  "no hay medición automática". Si las vistas superan 1.000 y
 *  luego 10.000 en esa plataforma, el bot felicita al chat
 *  vinculado con la medición REAL registrada, nunca inventada.
 *  Dedup por hito: una fila en notifications por (clip, plataforma,
 *  umbral); si ya se avisó ese hito, no se repite aunque se registre
 *  otra medición mayor. Nunca expone tokens ni credenciales. */
async function maybeNotifyViralMilestone(
  env: Env,
  tenant: Tenant,
  clip: ClipRow,
  stat: ClipStatRow,
): Promise<void> {
  try {
    const views = stat.views;
    if (typeof views !== "number") return;
    const label =
      ({ youtube: "YouTube", tiktok: "TikTok", instagram: "Instagram", facebook: "Facebook" })[stat.platform] ?? stat.platform;
    const title = (clip.title ?? "").trim() || "Clip sin título";
    for (const threshold of [1000, 10000]) {
      if (views < threshold) continue;
      const needle = `superó las ${threshold.toLocaleString("es-CL")} vistas en ${label}`;
      const existing = await env.DB.prepare(
        "SELECT message FROM notifications WHERE tenant_id = ? AND kind = 'milestone' AND clip_id = ?",
      )
        .bind(tenant, clip.id)
        .all<{ message: string }>();
      if (existing.results.some((r) => r.message.includes(needle))) continue;
      await notify(
        env.DB,
        { kind: "milestone", clipId: clip.id, message: `¡Hito viral! El clip #${clip.id} «${title}» llegó a ${views.toLocaleString("es-CL")} vistas en ${label} (${needle}).` },
        tenant,
        env,
      );
    }
  } catch {
    // Que la felicitación falle no tumba el guardado de la medición.
  }
}

/** Analíticas de un tenant (lo mismo que GET /api/analytics): medianas
 *  SOLO con mediciones reales (sin medición, null = "sin medición",
 *  nunca 0), ranking por canal por vistas y publicados sin
 *  medición. La comparten el endpoint y el resumen diario de
 *  Telegram, para que ambos digan exactamente lo mismo.
 *
 *  CANALES (0030, 2026-11-19): un canal es plataforma+idioma (8:
 *  YouTube ES/EN, TikTok ES/EN, Instagram ES/EN, Facebook ES/EN),
 *  cada uno con SU handle real de la tabla accounts (migración
 *  0030) y SUS cifras. Antes se agrupaba solo por plataforma y se
 *  contaban los publicados desde clips.publish_states: un clip EN
 *  publicado contaba como ES y el resumen se titulaba "Canales ES"
 *  con 11/4/2/2 mezclando idiomas (YT 7 ES + 4 EN, TikTok 3 ES +
 *  1 EN). Ahora los publicados se cuentan desde los jobs
 *  state='posted' (fuente de verdad, con platform_id y
 *  evidence_url; un clip cuenta una vez por canal) y las
 *  mediciones se atribuyen al idioma del clip. `platforms` se
 *  conserva como agregado por plataforma (suma ES+EN) para no
 *  romper el panel viejo; lo nuevo vive en `channels`.
 *  Cada canal lleva además `auto_status`: el último resultado de
 *  la medición AUTOMÁTICA (analytics_status, migración 0028) — ok,
 *  sin_credencial, sin_permiso o error, con motivo y hora de la
 *  última medición real — SIN tocar los números manuales: un
 *  "sin_permiso" nunca se convierte en ceros. Sin fila, null =
 *  "aún no se ha intentado medir". Sin handle verificado en
 *  accounts, `handle` es null ("sin handle registrado"): nunca se
 *  inventa ni se hardcodea uno. */
/** Analíticas por canal (plataforma+idioma) y por plataforma.
 *  Exportada para los tests (analytics-auto.test.ts). */
export async function computeAnalytics(env: Env, tenant: Tenant) {
  const stats = await listClipStats(env.DB, tenant);
  const clips = await listClipPublishStates(env.DB, tenant);
  const posted = await listPostedJobs(env.DB, tenant);
  const social = await listSocialAccounts(env.DB, tenant).catch(() => []);
  const autoStatusRows = await listAnalyticsStatus(env.DB, tenant).catch(() => []);
  const autoStatusByPlatform = new Map(
    autoStatusRows.map((r) => [r.platform, { status: r.status, reason: r.reason, last_measured_at: r.last_measured_at }]),
  );
  const clipById = new Map(clips.map((c) => [c.id, c]));
  const clipLang = (clipId: number): "es" | "en" => (clipById.get(clipId)?.language === "en" ? "en" : "es");
  // Handle real por canal desde la tabla accounts; fallback solo
  // para TikTok: el @ de la evidence_url del último job posted de
  // ese canal (dato de D1, nunca un literal).
  const handleByChannel = new Map<string, string>();
  for (const a of social) {
    const h = (a.handle ?? "").trim();
    if (!h) continue;
    const lang = a.language === "en" ? "en" : "es";
    handleByChannel.set(`${a.platform}|${lang}`, h);
  }
  for (const j of posted) {
    const api = toApiPlatform(j.platform as PanelPlatform);
    const key = `${api}|${j.language === "en" ? "en" : "es"}`;
    if (handleByChannel.has(key)) continue;
    if (api !== "tiktok") continue;
    const h = tiktokHandleFromEvidence(j.evidence_url);
    if (h) handleByChannel.set(key, h);
  }
  // Publicados por canal desde jobs posted (DISTINCT por clip).
  const publishedByChannel = new Map<string, Set<number>>();
  for (const j of posted) {
    const api = toApiPlatform(j.platform as PanelPlatform);
    const key = `${api}|${j.language === "en" ? "en" : "es"}`;
    const set = publishedByChannel.get(key) ?? new Set<number>();
    set.add(j.clip_id);
    publishedByChannel.set(key, set);
  }
  // Última medición por (clip, plataforma); las huérfanas (clip
  // que ya no existe en este tenant) no entran en ningún cálculo.
  const latest = new Map<string, ClipStatRow>();
  for (const s of stats) {
    if (!clipById.has(s.clip_id)) continue;
    const platform = normalizeStatPlatform(s.platform) ?? s.platform;
    const key = `${s.clip_id}|${platform}`;
    const prev = latest.get(key);
    if (!prev || s.recorded_at > prev.recorded_at || (s.recorded_at === prev.recorded_at && s.id > prev.id)) {
      latest.set(key, s);
    }
  }
  const measuredKeys = new Set(latest.keys());
  const channels = CHANNEL_PLATFORMS.flatMap((p) =>
    (["es", "en"] as const).map((lang) => {
      const rows = [...latest.values()].filter(
        (s) => normalizeStatPlatform(s.platform) === p && clipLang(s.clip_id) === lang,
      );
      const views = rows.map((s) => s.views).filter((v): v is number => typeof v === "number");
      const retention = rows.map((s) => s.retention_pct).filter((v): v is number => typeof v === "number");
      const ranking = [...rows]
        .sort((a, b) => (b.views ?? -1) - (a.views ?? -1) || (b.retention_pct ?? -1) - (a.retention_pct ?? -1) || b.recorded_at - a.recorded_at)
        .map((s) => ({ title: clipById.get(s.clip_id)?.title ?? null, language: lang, ...statOut(s) }));
      // auto_status SOLO si el canal existe (cuenta con handle o
      // publicados). analytics_status se guarda por plataforma, no
      // por canal: sin este filtro, un canal sin cuenta heredaba el
      // error del otro idioma (2026-11-19: IG EN y FB EN mostraban
      // el error 400 / sin_permiso del ES sin tener cuenta ni jobs).
      const channelKey = `${p}|${lang}`;
      const channelExists =
        handleByChannel.has(channelKey) || (publishedByChannel.get(channelKey)?.size ?? 0) > 0;
      return {
        platform: p,
        language: lang,
        handle: handleByChannel.get(channelKey) ?? null,
        median_views: median(views),
        median_retention: median(retention),
        measured_clips: rows.length,
        published_clips: publishedByChannel.get(channelKey)?.size ?? 0,
        has_data: rows.length > 0,
        auto_status: channelExists ? (autoStatusByPlatform.get(p) ?? null) : null,
        ranking,
      };
    }),
  );
  // Agregado por plataforma (compatibilidad): suma ES+EN. El
  // auto_status agregado es el del canal ES si existe, si no el EN.
  const platforms = CHANNEL_PLATFORMS.map((p) => {
    const pair = channels.filter((c) => c.platform === p);
    const rows = pair.flatMap((c) => c.ranking);
    const views = rows.map((s) => s.views).filter((v): v is number => typeof v === "number");
    const retention = rows.map((s) => s.retention_pct).filter((v): v is number => typeof v === "number");
    const ranking = [...rows].sort(
      (a, b) => (b.views ?? -1) - (a.views ?? -1) || (b.retention_pct ?? -1) - (a.retention_pct ?? -1) || b.recorded_at - a.recorded_at,
    );
    return {
      platform: p,
      median_views: median(views),
      median_retention: median(retention),
      measured_clips: pair.reduce((n, c) => n + c.measured_clips, 0),
      published_clips: pair.reduce((n, c) => n + c.published_clips, 0),
      has_data: pair.some((c) => c.has_data),
      auto_status: pair[0]?.auto_status ?? pair[1]?.auto_status ?? null,
      ranking,
    };
  });
  const unmeasured: { clip_id: number; title: string; platform: PlatformKey; language: "es" | "en" }[] = [];
  const seenUnmeasured = new Set<string>();
  for (const j of posted) {
    const api = toApiPlatform(j.platform as PanelPlatform);
    const key = `${j.clip_id}|${api}`;
    if (measuredKeys.has(key) || seenUnmeasured.has(key)) continue;
    seenUnmeasured.add(key);
    unmeasured.push({
      clip_id: j.clip_id,
      title: clipById.get(j.clip_id)?.title ?? "",
      platform: api,
      language: j.language === "en" ? "en" : "es",
    });
  }
  return { platforms, channels, unmeasured };
}

/** Resumen diario de analíticas por Telegram (09:00 Santiago):
 *  el tenant recibe SOLO sus analíticas en SU chat vinculado.
 *  09:00 America/Santiago, solo si el Worker está en marcha, el
 *  bot está configurado Y ese tenant tiene un chat vinculado
 *  (telegram_links). computeAnalytics() filtra por tenant, así que
 *  un envío nunca mezcla datos. Deduplicado por tenant+día en
 *  telegram_links.last_sent_day (la marca vieja en settings se
 *  respeta una vez por compatibilidad con la v1). Si falta algo, se
 *  salta en silencio; un fallo de envío no para la cola (se
 *  reintenta en el minuto siguiente dentro de la hora 09:00-09:59,
 *  y queda en worker_events para verlo en el panel). Exportada
 *  para los tests (telegram-link.test.ts). */
export async function maybeSendDailyTelegram(env: Env, tenant: Tenant, nowMs: number): Promise<void> {
  if (!botConfigured(env)) return;
  const { day, hour } = santiagoParts(new Date(nowMs));
  if (hour !== 9) return;
  try {
    const link = await getTelegramLinkByTenant(env.DB, tenant);
    if (!link) return; // Sin vínculo, no se envía nada a nadie.
    const settings = await getSettings(env.DB, tenant);
    if (link.last_sent_day === day || settings[TELEGRAM_SENT_DAY_KEY] === day) return;
    const { channels } = await computeAnalytics(env, tenant);
    const sent = await sendTelegram(env, link.chat_id, buildAnalyticsMessage(channels, day));
    if (!sent.ok) {
      await logWorkerEvent(env.DB, "telegram", `Resumen diario no enviado (${day}): ${sent.error ?? `HTTP ${sent.status}`}.`, tenant);
      return;
    }
    await markTelegramSent(env.DB, link.chat_id, day);
    await saveSettings(env.DB, tenant, { ...settings, [TELEGRAM_SENT_DAY_KEY]: day });
    await logWorkerEvent(env.DB, "telegram", `Resumen diario de analíticas enviado (${day}).`, tenant);
  } catch (e) {
    // Que Telegram falle no para la cola ni el resto del ciclo.
    try {
      await logWorkerEvent(env.DB, "telegram", `Resumen diario falló (${day}): ${e instanceof Error ? e.message : String(e)}`, tenant);
    } catch {
      // Sin D1 no hay nada que hacer.
    }
  }
}

/** Dispatch diario del cerebro (2026-11-19, cerebro genérico):
 *  a las 09:00 Santiago, UNA vez al día por tenant en marcha, el
 *  Worker dispara panel-daily (tipo fijo; el tenant va en el
 *  client_payload) para la pasada diaria de VODs. Sustituye al cron
 *  09:00 del workflow cerebro-sebastian.yml (eliminado: el cron del
 *  workflow no tenía token para autenticarse en el panel). Dedupe
 *  por día en settings (clave brain_daily_dispatched_day), mismo
 *  patrón que el resumen de Telegram: si el dispatch falla (sin
 *  GITHUB_TOKEN o sin token), NO se marca el día y se reintenta en
 *  el próximo ciclo mientras sigan siendo las 9. Exportada para
 *  tests. */
export async function maybeDispatchDaily(env: Env, tenant: Tenant, nowMs: number): Promise<void> {
  const { day, hour } = santiagoParts(new Date(nowMs));
  if (hour !== 9) return;
  try {
    const settings = await getSettings(env.DB, tenant);
    if (settings[BRAIN_DAILY_SENT_DAY_KEY] === day) return;
    const ok = await dispatchCerebro(env, "panel-daily", tenant);
    if (!ok) return;
    await saveSettings(env.DB, tenant, { ...settings, [BRAIN_DAILY_SENT_DAY_KEY]: day });
    await logWorkerEvent(env.DB, "github_dispatch", `Dispatch diario panel-daily enviado a GitHub (${day}).`, tenant);
  } catch (e) {
    // Que el dispatch diario falle no para la cola ni el resto del ciclo.
    try {
      await logWorkerEvent(env.DB, "github_dispatch", `Dispatch diario falló (${day}): ${e instanceof Error ? e.message : String(e)}`, tenant);
    } catch {
      // Sin D1 no hay nada que hacer.
    }
  }
}

/** Revalidación automática (spec 2026-11-24, auto-seo): cada 6 h
 *  se dispara `panel-revalidate` al workflow cerebro-revalidate,
 *  que revalida los clips pendientes y rellena el SEO que falta
 *  en la Cola. La cadencia se marca en settings
 *  (`last_revalidate_at_ms`); sin marca, la primera pasada tras
 *  el deploy dispara (la puerta `revalidateDue` del ciclo ya lo
 *  evaluó con solo lecturas). Un fallo nunca para la cola ni el
 *  resto del ciclo. Exportada para los tests. */
export async function maybeDispatchRevalidate(env: Env, tenant: Tenant, nowMs: number): Promise<void> {
  try {
    const settings = await getSettings(env.DB, tenant);
    const last = typeof settings[REVALIDATE_LAST_KEY] === "number" ? (settings[REVALIDATE_LAST_KEY] as number) : 0;
    if (last > 0 && nowMs - last < REVALIDATE_INTERVAL_MS) return;
    const ok = await dispatchCerebro(env, "panel-revalidate", tenant);
    if (!ok) return;
    await saveSettings(env.DB, tenant, { ...settings, [REVALIDATE_LAST_KEY]: nowMs });
    await logWorkerEvent(env.DB, "github_dispatch", `Dispatch panel-revalidate enviado a GitHub.`, tenant);
  } catch (e) {
    // Que el dispatch de revalidación falle no para la cola ni el resto del ciclo.
    try {
      await logWorkerEvent(env.DB, "github_dispatch", `Dispatch revalidate falló: ${e instanceof Error ? e.message : String(e)}`, tenant);
    } catch {
      // Sin D1 no hay nada que hacer.
    }
  }
}

/** Caducidad nocturna de eventos (A2, 2026-11-20, cuota D1): a las
 *  03:00 America/Santiago, una vez al día por tenant, borra eventos
 *  de más de 14 días en lotes de 500 (tope 5.000/noche) y solo si el
 *  consumo estimado del día está por debajo del 70% (ver
 *  pruneOldEvents en db.ts). Corre aunque el Worker esté parado
 *  (r.ran false): un Worker parado no genera escrituras nuevas, así
 *  que su presupuesto está frío y la limpieza no compite con nada.
 *  Un fallo nunca para la cola ni el resto del ciclo. Exportada
 *  para los tests, como maybeSendDailyTelegram/maybeDispatchDaily. */
export async function maybePruneOldEvents(env: Env, tenant: Tenant, nowMs: number): Promise<void> {
  if (!isPruneHour(nowMs)) return;
  try {
    const r = await pruneOldEvents(env.DB, tenant, nowMs);
    if (!r.ran) {
      if (r.reason === "over_budget_gate") {
        await logWorkerEvent(
          env.DB,
          "mantenimiento",
          `Caducidad de eventos omitida hoy: consumo estimado ${r.estimatedWritesToday ?? "?"} escrituras, por encima del hito del 70%.`,
          tenant,
        );
      }
      return;
    }
    const total = Object.values(r.deleted).reduce((a, b) => a + b, 0);
    if (total > 0) {
      const parts = Object.entries(r.deleted)
        .filter(([, n]) => n > 0)
        .map(([t, n]) => `${t}: ${n}`)
        .join(", ");
      await logWorkerEvent(
        env.DB,
        "mantenimiento",
        `Caducidad de eventos: ${total} filas de más de 14 días borradas (${parts}).`,
        tenant,
      );
    }
  } catch {
    // Que la caducidad falle no para la cola ni el resto del ciclo.
  }
}

/** Último estado conocido del Worker para la alerta v3.1: se
 *  guarda en settings del tenant (clave
 *  telegram_worker_last_running, 1/0). El panel no la borra al
 *  guardar Ajustes (effectiveSettings conserva claves
 *  desconocidas). Sin marca, null: primera observación. */
async function getTelegramLastWorkerRunning(env: Env, tenant: Tenant): Promise<boolean | null> {
  const settings = await getSettings(env.DB, tenant).catch(() => ({} as Record<string, unknown>));
  const v = settings[TELEGRAM_WORKER_LAST_RUNNING_KEY];
  return v === 1 ? true : v === 0 ? false : null;
}

async function setTelegramLastWorkerRunning(env: Env, tenant: Tenant, running: boolean): Promise<void> {
  const settings = await getSettings(env.DB, tenant).catch(() => ({} as Record<string, unknown>));
  await saveSettings(env.DB, tenant, { ...settings, [TELEGRAM_WORKER_LAST_RUNNING_KEY]: running ? 1 : 0 });
}

/** Encender el Worker desde Telegram: MISMO camino que el botón
 *  Iniciar del panel (POST /api/worker/start): setWorkerRunning +
 *  dispatch del cerebro en GitHub (panel-start). Si ya
 *  estaba en marcha, no hace nada: no se re-dispara el workflow.
 *  La marca de último estado se pone a 1 aquí mismo, para que el
 *  cron no vea una "transición" fantasma. Exportada para tests. */
export async function startWorkerFromTelegram(env: Env, tenant: Tenant): Promise<string> {
  if (await isWorkerRunning(env.DB, tenant)) {
    return "🟢 El Worker ya estaba en marcha: la cola avanza sola.";
  }
  await setWorkerRunning(env.DB, tenant, true, "Worker iniciado desde Telegram: la cola avanza sola a partir de ahora.");
  await dispatchCerebro(env, "panel-start", tenant);
  await setTelegramLastWorkerRunning(env, tenant, true).catch(() => {});
  return "🟢 Worker en marcha: la cola avanza sola a partir de ahora.";
}

/** Parar el Worker desde Telegram: MISMO camino que el botón
 *  Parar del panel (POST /api/worker/stop): setWorkerRunning (el
 *  flag D1 es la fuente de verdad; ya NO se dispara panel-stop: el
 *  workflow no tiene nada que hacer al parar). Solo la llama quien
 *  ya pasó la confirmación en dos pasos (ver handleTelegramCommand).
 *  La marca de último estado se pone a 0 aquí mismo: ESTA parada es
 *  voluntaria y no debe generar la alerta de desactivación
 *  inesperada. Exportada para tests. */
export async function stopWorkerFromTelegram(env: Env, tenant: Tenant): Promise<string> {
  if (!(await isWorkerRunning(env.DB, tenant))) {
    return "🔴 El Worker ya estaba parado: la cola queda en pausa.";
  }
  await setWorkerRunning(env.DB, tenant, false, "Worker parado desde Telegram: la cola queda en pausa (nada se pierde).");
  await setTelegramLastWorkerRunning(env, tenant, false).catch(() => {});
  return "🔴 Worker parado: la cola queda en pausa (nada se pierde). Para encenderlo, escribe /iniciar.";
}

/** Alerta de desactivación inesperada (v3.1): la llama el cron
 *  cada minuto por tenant, corra o no el ciclo (un Worker parado
 *  no tiene ciclo que avise). Compara el estado real de
 *  worker_state con el último estado conocido:
 *  - Sin marca (primera vez): inicializa en silencio, sin avisar
 *    de un estado que ya era así.
 *  - Transición 1→0: avisa UNA vez a su chat vinculado ("El Worker
 *    se desactivó a las HH:MM"), sin inventar la causa, y actualiza
 *    la marca: el minuto siguiente ya no es transición y no repite.
 *  - Transición 0→1 o sin cambio: solo actualiza la marca.
 *  La parada desde Telegram no llega aquí como transición: /parar
 *  ya puso la marca a 0. La parada desde el panel sí avisa (puede
 *  ser otro usuario o un error). Nunca rompe el cron: cualquier
 *  fallo se traga en silencio. Exportada para tests. */
export async function maybeAlertWorkerStopped(env: Env, tenant: Tenant, nowMs: number = Date.now()): Promise<void> {
  try {
    const running = await isWorkerRunning(env.DB, tenant).catch(() => null);
    if (running === null) return;
    const last = await getTelegramLastWorkerRunning(env, tenant);
    if (last === null) {
      await setTelegramLastWorkerRunning(env, tenant, running).catch(() => {});
      return;
    }
    if (last === running) return;
    await setTelegramLastWorkerRunning(env, tenant, running).catch(() => {});
    if (!last || running) return; // Solo 1→0 avisa; 0→1 es buena noticia, sin mensaje.
    await logWorkerEvent(env.DB, "aviso", "El Worker se desactivó de forma inesperada (aviso enviado por Telegram).", tenant).catch(() => {});
    if (!botConfigured(env)) return;
    const link = await getTelegramLinkByTenant(env.DB, tenant).catch(() => null);
    if (!link) return; // Sin vínculo, el aviso vive solo en el panel.
    const sent = await sendTelegram(env, link.chat_id, buildWorkerStoppedAlertMessage(nowMs)).catch(() => null);
    if (sent && !sent.ok) {
      await logWorkerEvent(env.DB, "aviso", `No se pudo enviar la alerta de Worker parado por Telegram (${sent.error ?? `HTTP ${sent.status}`}).`, tenant).catch(() => {});
    }
  } catch {
    // Que la alerta falle no tumba el cron ni la cola.
  }
}

/** Alerta de Worker atascado (validador de suministro, 2026-11-18):
 *  la llama el cron cada minuto por tenant. Si el validador dice
 *  ok, stopped o no_live_es (sin directos ES: espera, no atasco),
 *  pone la marca de última alerta a 0 (el próximo atasco vuelve a
 *  avisar). Si dice blocked o stuck, avisa UNA vez por episodio:
 *  como mucho 1 vez cada 6 h mientras siga atascado.
 *  Desde 2026-11-19 la alerta DEJA DE SER UNA PREGUNTA: el
 *  vigilante (cerebro) es quien diagnostica y avisa por Telegram
 *  vía POST /api/bridge/notify, y cada aviso suyo queda en
 *  worker_events con kind "watchdog". Esta alerta solo se envía
 *  por Telegram si el vigilante YA reportó un fallo persistente
 *  (evento watchdog reciente, dentro de la misma ventana de 6 h
 *  del dedupe) y el mensaje incluye su último diagnóstico tal
 *  cual. Sin diagnóstico del vigilante, el aviso vive SOLO en el
 *  panel (worker_events, kind "aviso") y Telegram queda pendiente
 *  de su confirmación: el validador solo sabe que no llegan clips,
 *  no por qué. El dedupe de 1 vez cada 6 h por episodio se
 *  conserva en ambos casos. Sin vínculo de Telegram, el aviso vive
 *  solo en el panel. Nunca rompe el cron. */
export async function maybeAlertWorkerStuck(env: Env, tenant: Tenant, nowMs: number = Date.now()): Promise<void> {
  try {
    const validation = await collectWorkerSupply(env.DB, tenant, nowMs, { env }).catch(() => null);
    if (!validation) return;
    const settings = await getSettings(env.DB, tenant).catch(() => ({} as Record<string, unknown>));
    if (validation.status === "ok" || validation.status === "stopped" || validation.status === "no_live_es") {
      if (settings[TELEGRAM_WORKER_STUCK_ALERT_KEY]) {
        await saveSettings(env.DB, tenant, { ...settings, [TELEGRAM_WORKER_STUCK_ALERT_KEY]: 0 }).catch(() => {});
      }
      return;
    }
    const lastAlert = typeof settings[TELEGRAM_WORKER_STUCK_ALERT_KEY] === "number" ? (settings[TELEGRAM_WORKER_STUCK_ALERT_KEY] as number) : 0;
    if (lastAlert > 0 && nowMs - lastAlert < STUCK_ALERT_MIN_INTERVAL_MS) return;
    await saveSettings(env.DB, tenant, { ...settings, [TELEGRAM_WORKER_STUCK_ALERT_KEY]: nowMs }).catch(() => {});
    // Diagnóstico del vigilante (kind "watchdog", lo deja
    // POST /api/bridge/notify): solo se avisa por Telegram si el
    // vigilante ya confirmó el fallo persistente. Sin él, el aviso
    // queda solo en el panel y Telegram espera su confirmación.
    const watchdogRow = await env.DB.prepare(
      "SELECT at, message FROM worker_events WHERE tenant_id = ? AND kind = 'watchdog' ORDER BY at DESC LIMIT 1",
    ).bind(tenant).first<{ at: number; message: string }>().catch(() => null);
    const watchdogDiagnosis = watchdogRow && nowMs - watchdogRow.at < STUCK_ALERT_MIN_INTERVAL_MS
      ? watchdogRow.message
      : "";
    await logWorkerEvent(env.DB, "aviso",
      watchdogDiagnosis
        ? `Validador del Worker: ${validation.reason} Diagnóstico del vigilante: ${watchdogDiagnosis}`
        : `Validador del Worker: ${validation.reason} (sin diagnóstico del vigilante todavía: la alerta de Telegram queda pendiente de su confirmación)`,
      tenant).catch(() => {});
    if (!watchdogDiagnosis) return;
    if (!botConfigured(env)) return;
    const link = await getTelegramLinkByTenant(env.DB, tenant).catch(() => null);
    if (!link) return;
    const sent = await sendTelegram(env, link.chat_id, buildWorkerStuckAlertMessage(validation.reason, nowMs, watchdogDiagnosis)).catch(() => null);
    if (sent && !sent.ok) {
      await logWorkerEvent(env.DB, "aviso", `No se pudo enviar la alerta de Worker atascado por Telegram (${sent.error ?? `HTTP ${sent.status}`}).`, tenant).catch(() => {});
    }
  } catch {
    // Que la alerta falle no tumba el cron ni la cola.
  }
}

/** Router de comandos Telegram v3 (solo para chats vinculados;
 *  el tenant sale SIEMPRE del vínculo por chat_id, nunca del
 *  texto). Comandos solo de lectura, más ordenar la Cola (idea 49):
 *  /resumen, /cola, /estado, /top [n], /ayuda, /primero|/subir|
 *  /bajar|/auto <id>. Un chat no vinculado nunca llega aquí: el
 *  webhook le responde solo con las instrucciones. Texto libre o
 *  comando desconocido: /ayuda. Exportada para los tests. */
export async function handleTelegramCommand(env: Env, chatId: string, tenant: Tenant, text: string): Promise<string> {
  const cmd = parseTelegramCommand(text);
  const day = santiagoParts(new Date()).day;
  // Confirmación pendiente de /parar (v3.1): si hay una vigente
  // en este chat, este mensaje es la respuesta a la pregunta.
  // SÍ (o /parar sí) ejecuta la parada; cualquier otro texto la
  // cancela y se procesa como comando normal (así /resumen sigue
  // funcionando y un segundo /parar vuelve a preguntar). Caducada:
  // se borra y se procesa normal. De otro tenant: se borra.
  const pending = await getTelegramStopConfirmation(env.DB, chatId).catch(() => null);
  if (pending) {
    const mine = pending.tenant_id === tenant;
    const expired = pending.expires_at <= Date.now();
    await deleteTelegramStopConfirmation(env.DB, chatId).catch(() => {});
    if (mine && !expired && (isStopConfirmation(text) || (cmd?.name === "parar" && isStopConfirmation(cmd.arg)))) {
      return await stopWorkerFromTelegram(env, tenant);
    }
    // Si era /parar de nuevo, cae en el switch y vuelve a preguntar.
  }
  if (!cmd) return helpMessage();
  switch (cmd.name) {
    case "iniciar":
      return await startWorkerFromTelegram(env, tenant);
    case "parar": {
      if (!(await isWorkerRunning(env.DB, tenant))) {
        return "🔴 El Worker ya estaba parado: la cola queda en pausa.";
      }
      await createTelegramStopConfirmation(env.DB, chatId, tenant, Date.now() + STOP_CONFIRM_TTL_MS);
      return stopConfirmMessage();
    }
    case "start":
      return `Este chat está vinculado a ${tenant}. Cada día a las 09:00 (Santiago) recibirás aquí solo SUS analíticas.\n\n${helpMessage()}`;
    case "resumen": {
      const { channels } = await computeAnalytics(env, tenant);
      return buildAnalyticsMessage(channels, day);
    }
    case "cola": {
      const clips = await listQueueClips(env.DB, tenant);
      return buildQueueMessage(clips);
    }
    case "top": {
      const n = /^\d+$/.test(cmd.arg) ? Number(cmd.arg) : 5;
      const { channels } = await computeAnalytics(env, tenant);
      return buildTopMessage(channels, n);
    }
    case "estado": {
      const row = await env.DB.prepare("SELECT running, started_at, last_note FROM worker_state WHERE tenant_id = ?")
        .bind(tenant)
        .first<{ running: number; started_at: number | null; last_note: string }>()
        .catch(() => null);
      const failed = await listJobs(env.DB, tenant, 100, "failed").catch(() => []);
      const lastEvent = await env.DB.prepare("SELECT at, kind, message FROM worker_events WHERE tenant_id = ? ORDER BY at DESC LIMIT 1")
        .bind(tenant)
        .first<{ at: number; kind: string; message: string }>()
        .catch(() => null);
      const validation = await collectWorkerSupply(env.DB, tenant, Date.now(), { env }).catch(() => null);
      // Aceptación automática (2026-11-19): /estado dice si los
      // clips se publican solos o esperan aprobación manual.
      const settings = await getSettings(env.DB, tenant).catch(() => null);
      const base = buildStatusMessage({
        running: row?.running === 1,
        startedAt: row?.started_at ?? null,
        lastNote: row?.last_note ?? "",
        failedCount: failed.length,
        lastEvent,
        tenant,
        autoPublish: settings ? effectiveSettings(settings).auto_publish_top : null,
      });
      return validation ? `${base}\nValidador: ${validation.reason}` : base;
    }
    case "ayuda":
      return helpMessage();
    case "primero":
    case "subir":
    case "bajar":
    case "auto": {
      if (!/^\d+$/.test(cmd.arg)) {
        return `Uso: /${cmd.name} <id> — el id es el número del clip que muestra /cola (por ejemplo /${cmd.name} 42).`;
      }
      const action = cmd.name === "primero" ? "first" : cmd.name === "subir" ? "up" : cmd.name === "bajar" ? "down" : "auto";
      const moved = await moveQueueClip(env.DB, Number(cmd.arg), tenant, action);
      if (!moved.ok) return moved.message;
      const clips = await listQueueClips(env.DB, tenant);
      return `${moved.message}\n\n${buildQueueMessage(clips)}`;
    }
    default:
      return helpMessage();
  }
}

async function handleFetch(req: Request, env: Env, _ctx: ExecutionContext): Promise<Response> {
    const url = new URL(req.url);
    const path = url.pathname;
    const method = req.method;

    // --- Salud (sin auth; la usa health-check.sh y el deploy) ---
    // Un Worker = un tenant: solo informa de ESTE tenant.
    if (path === "/api/health" && method === "GET") {
      const tenant = requireTenant(env);
      const running = await isWorkerRunning(env.DB, tenant).catch(() => false);
      return json({ ok: true, app: "avc", tenant, worker_running: running, time: new Date().toISOString() });
    }

    // --- Vídeo por URL firmada (sin sesión; la firma manda) ---
    // GET /api/public-video?key=&exp=&sig= : sirve UN objeto R2
    // solo con firma HMAC válida y sin caducar (15 min, ver r2.ts).
    // La emite solo el paso de Instagram en el momento de publicar;
    // el bucket es privado y no hay dominio público r2.dev. Sin
    // firma, o caducada, 403: nunca se sirven bytes por adivinar
    // la key. HEAD devuelve solo las cabeceras (la usa Meta).
    if (path === "/api/public-video" && (method === "GET" || method === "HEAD")) {
      const key = url.searchParams.get("key") ?? "";
      const exp = Number(url.searchParams.get("exp") ?? "");
      const sig = url.searchParams.get("sig") ?? "";
      if (!/^clips\/[a-z]+\/\d+\//.test(key) || !(await verifySignedVideoUrl(env, key, exp, sig))) {
        return err("URL de vídeo no válida o caducada.", 403);
      }
      const obj = await env.CLIPS_BUCKET.get(key);
      if (!obj) return err("El MP4 ya no está en R2.", 404);
      const headers: Record<string, string> = { "Content-Type": obj.httpMetadata?.contentType ?? "video/mp4" };
      if (typeof obj.size === "number") headers["Content-Length"] = String(obj.size);
      if (method === "HEAD") return new Response(null, { headers });
      return new Response(obj.body, { headers });
    }

    // --- Imagen de marca de agua por URL firmada (sin sesión) ---
    // GET /api/public-asset?key=&exp=&sig= : sirve UN logo R2 solo
    // con firma HMAC válida y sin caducar (60 min, ver r2.ts). La
    // emite solo el puente de Ajustes (/api/bridge/settings) cuando
    // la marca está activada y hay logo activo del tenant; el
    // cerebro la descarga al renderizar. Mismo esquema HMAC que el
    // vídeo, distinto prefijo de key (watermarks/<tenant>/).
    if (path === "/api/public-asset" && (method === "GET" || method === "HEAD")) {
      const key = url.searchParams.get("key") ?? "";
      const exp = Number(url.searchParams.get("exp") ?? "");
      const sig = url.searchParams.get("sig") ?? "";
      if (!WATERMARK_KEY_RE.test(key) || !(await verifySignedWatermarkUrl(env, key, exp, sig))) {
        return err("URL de imagen no válida o caducada.", 403);
      }
      const obj = await env.CLIPS_BUCKET.get(key);
      if (!obj) return err("La imagen ya no está en R2.", 404);
      const headers: Record<string, string> = { "Content-Type": obj.httpMetadata?.contentType ?? "image/png" };
      if (typeof obj.size === "number") headers["Content-Length"] = String(obj.size);
      if (method === "HEAD") return new Response(null, { headers });
      return new Response(obj.body, { headers });
    }

    // --- Webhook de Telegram (v2, sin sesión: Telegram no la tiene;
    //  el secreto del header manda). POST /api/telegram/webhook.
    //  Fail-closed: sin TELEGRAM_WEBHOOK_SECRET configurado, o con
    //  un header distinto, 403 siempre. El bot NUNCA pide la
    //  contraseña del panel ni entrega analíticas a un chat no
    //  vinculado: solo vincula por código, desvincula o responde
    //  instrucciones. Va ANTES del gate de sesión de abajo.
    if (path === "/api/telegram/webhook" && method === "POST") {
      const secret = (env.TELEGRAM_WEBHOOK_SECRET || "").trim();
      const got = req.headers.get("X-Telegram-Bot-Api-Secret-Token") ?? "";
      if (!secret || got !== secret) return err("Webhook no autorizado.", 403);
      if (!botConfigured(env)) return json({ ok: true });
      const update = parseTelegramUpdate(await readJson(req));
      if (!update) return json({ ok: true });
      const reply = async (text: string): Promise<void> => {
        await sendTelegram(env, update.chatId, text).catch(() => {});
      };
      const text = update.text.trim();
      if (text === "/unlink") {
        await unlinkTelegramChat(env.DB, update.chatId).catch(() => {});
        await deleteTelegramStopConfirmation(env.DB, update.chatId).catch(() => {});
        await reply(unlinkedMessage());
        return json({ ok: true });
      }
      const code = parseLinkCode(text);
      if (code) {
        const row = await findTelegramLinkCode(env.DB, await sha256Hex(code)).catch(() => null);
        // Un código de otro tenant (D1 con filas ajenas) no vale
        // aquí y no se borra: no es de este Worker.
        if (row && row.tenant_id !== requireTenant(env)) {
          await reply(
            "Ese código no vale o caducó (dura 10 minutos). Genera uno nuevo en tu panel: Ajustes → Telegram → «Generar código».",
          );
          return json({ ok: true });
        }
        if (!row || row.expires_at <= Date.now()) {
          if (row) await deleteTelegramLinkCode(env.DB, row.code_hash).catch(() => {});
          await reply(
            "Ese código no vale o caducó (dura 10 minutos). Genera uno nuevo en tu panel: Ajustes → Telegram → «Generar código».",
          );
          return json({ ok: true });
        }
        await linkTelegramChat(env.DB, update.chatId, row.tenant_id, Date.now());
        await deleteTelegramLinkCode(env.DB, row.code_hash);
        await reply(linkedMessage(row.tenant_id));
        return json({ ok: true });
      }
      // /start sin código, o cualquier otro mensaje: si el chat
      // está vinculado A ESTE tenant, van los comandos v3 (router);
      // si no (sin vínculo, o vínculo de otro tenant), solo
      // instrucciones, NUNCA analíticas ni datos de nadie.
      const existing = await getTelegramLinkByChat(env.DB, update.chatId).catch(() => null);
      if (!existing || existing.tenant_id !== requireTenant(env)) {
        await reply(linkInstructionsMessage());
        return json({ ok: true });
      }
      await reply(await handleTelegramCommand(env, update.chatId, existing.tenant_id, text).catch(() => helpMessage()));
      return json({ ok: true });
    }

    // --- Auth: login por usuario (username + contraseña, panel_users).
    // El hash vive en D1 (sembrado con scripts/seed-users.sql); nunca
    // por chat ni en el repo. Cada usuario ve solo su tenant.
    if (path === "/api/auth/login" && method === "POST") {
      const body = await readJson(req);
      const username = typeof body.username === "string" ? body.username.trim() : "";
      const password = typeof body.password === "string" ? body.password : "";
      const user = username ? await getLoginUser(env.DB, username).catch(() => null) : null;
      if (!user || !password || !(await verifyPassword(password, user.password_hash))) {
        return err("Usuario o contraseña incorrectos.", 401);
      }
      // Un Worker = un tenant: el usuario existe pero es del OTRO
      // tenant -> mismo 401 genérico (sin revelar que existe).
      if (user.tenant !== requireTenant(env)) {
        return err("Usuario o contraseña incorrectos.", 401);
      }
      // El token se devuelve ADEMÁS de la cookie (mismo valor
      // body.sig, generado una sola vez): Brave iOS bloquea la
      // cookie cross-site y el front reenvía el token como
      // Authorization: Bearer. La cookie se mantiene tal cual.
      const token = await createSessionToken(user.username, user.tenant, env.SESSION_SECRET);
      const cookie = sessionCookieHeader(token);
      return json({ ok: true, tenant: user.tenant, token }, 200, { "Set-Cookie": cookie });
    }
    if (path === "/api/auth/logout" && method === "POST") {
      // B1 (Frente 2): el logout REVOCA el token en D1 (tabla
      // revoked_tokens, 0015). Sin esto el Bearer seguía valiendo
      // 30 días aunque la cookie se borrara. Se revoca el token que
      // llegue (cookie o Bearer); si no llega ninguno, igual se
      // limpia la cookie. Un fallo al revocar no impide el logout.
      const raw = extractSessionToken(req.headers.get("Cookie"), req.headers.get("Authorization"));
      if (raw) {
        try {
          const payload = await verifySessionToken(raw, env.SESSION_SECRET, requireTenant(env));
          // Sin payload válido no se puede atribuir el token a
          // ningún tenant: no se revoca nada (nunca se inventa
          // "sebastian" como tenant del token).
          if (payload) {
            await revokeToken(env.DB, await tokenFingerprint(raw), payload.sub, payload.tenant, payload.exp);
          }
        } catch {
          // Logout best-effort: la cookie se limpia igual.
        }
      }
      return json({ ok: true }, 200, { "Set-Cookie": clearSessionCookie() });
    }
    if (path === "/api/auth/me" && method === "GET") {
      const session = await requireSession(req, env);
      if (!session) return err("Sin sesión.", 401);
      return json({ ok: true, tenant: session.tenant, sub: session.sub });
    }

    // --- Puente publisher-bridge (token, no sesión de usuario) ---
    // Mecanismo ALTERNATIVO dirigido por el Worker (ver COMPUTE.md).
    // La vía principal de cómputo es la cadena del cerebro en GitHub
    // Actions (cerebro-*.yml, cron 09:00).
    if (path === "/api/jobs/next" && method === "GET") {
      if (!requireBridge(req, env)) return err("Bridge no autorizado.", 401);
      const bTenant = requireBridgeTenant(req, env);
      if (!bTenant) return err("Bridge sin tenant (header X-Bridge-Tenant: <tenant>).", 401);
      const kind = url.searchParams.get("kind") ?? undefined;
      const row = await env.DB.prepare(
        "SELECT * FROM jobs WHERE tenant_id = ? AND state = 'scheduled' AND (next_run_at IS NULL OR next_run_at <= ?) " +
          (kind ? "AND kind = ? " : "") +
          "ORDER BY created_at ASC LIMIT 1",
      )
        .bind(...(kind ? [bTenant, Date.now(), kind] : [bTenant, Date.now()]))
        .first<JobRow>();
      if (!row) return json({ ok: true, job: null });
      // Reclamo ATÓMICO (M4): solo el claimant cuya UPDATE
      // condicional cambia 1 fila se lleva el job; el otro recibe
      // null. Se relee la fila tras reclamar.
      const claimed = await claimJob(env.DB, row.id, bTenant);
      if (!claimed) return json({ ok: true, job: null });
      const fresh = await env.DB.prepare("SELECT * FROM jobs WHERE id = ? AND tenant_id = ?").bind(row.id, bTenant).first<JobRow>();
      return json({ ok: true, job: fresh ?? row });
    }
    // Descarga del MP4 de un job (solo puente, con token): el puente
    // necesita los bytes para subir por API con clipagent.
    const jobVideoMatch = /^\/api\/jobs\/(\d+)\/video$/.exec(path);
    if (jobVideoMatch && method === "GET") {
      if (!requireBridge(req, env)) return err("Bridge no autorizado.", 401);
      const bTenant = requireBridgeTenant(req, env);
      if (!bTenant) return err("Bridge sin tenant (header X-Bridge-Tenant: <tenant>).", 401);
      const job = await env.DB.prepare("SELECT * FROM jobs WHERE id = ? AND tenant_id = ?").bind(Number(jobVideoMatch[1]), bTenant).first<JobRow>();
      if (!job?.r2_key) return err("Job sin MP4 en R2.", 404);
      const obj = await env.CLIPS_BUCKET.get(job.r2_key);
      if (!obj) return err("El MP4 ya no está en R2.", 404);
      return new Response(obj.body, { headers: { "Content-Type": obj.httpMetadata?.contentType ?? "video/mp4" } });
    }
    const completeMatch = /^\/api\/jobs\/(\d+)\/complete$/.exec(path);
    if (completeMatch && method === "POST") {
      if (!requireBridge(req, env)) return err("Bridge no autorizado.", 401);
      const bTenant = requireBridgeTenant(req, env);
      if (!bTenant) return err("Bridge sin tenant (header X-Bridge-Tenant: <tenant>).", 401);
      const body = await readJson(req);
      const jobId = Number(completeMatch[1]);
      const job = await env.DB.prepare("SELECT * FROM jobs WHERE id = ? AND tenant_id = ?").bind(jobId, bTenant).first<JobRow>();
      if (!job) return err("Job no encontrado.", 404);
      const state = body.state;
      if (state === "posted" && typeof body.evidence_url === "string" && body.evidence_url.startsWith("https://")) {
        await markPosted(env.DB, job, String(body.platform_id ?? ""), body.evidence_url);
        return json({ ok: true });
      }
      if (state === "failed") {
        await markFailed(env.DB, job, String(body.error ?? "fallo sin motivo"), body.retryable === true, env);
        return json({ ok: true });
      }
      if (state === "draft") {
        await env.DB.prepare("UPDATE jobs SET state = 'draft', last_error = ?, updated_at = ? WHERE id = ? AND tenant_id = ?")
          .bind(String(body.error ?? "draft"), Date.now(), jobId, bTenant)
          .run();
        return json({ ok: true });
      }
      if (state === "processing") {
        await env.DB.prepare("UPDATE jobs SET state = 'processing', resume_data = ?, next_run_at = ?, updated_at = ? WHERE id = ? AND tenant_id = ?")
          .bind(typeof body.resume_data === "string" ? body.resume_data : job.resume_data, Date.now() + 60_000, Date.now(), jobId, bTenant)
          .run();
        return json({ ok: true });
      }
      return err("state inválido: usa posted (con evidence_url https), failed, draft o processing.", 400);
    }

    // --- Canje del token de un solo uso del cerebro ---
    // POST /api/brain/session {token} -> {ok, token, tenant,
    // expires_at}. El Worker emite el token en cada dispatch a
    // GitHub (tabla brain_tokens, 0032); el cerebro lo canjea UNA
    // vez por una sesión body.sig (sub "cerebro", 12 h). Sin sesión
    // de usuario y sin secretos en GitHub: el token es el único
    // secreto que viaja al runner, muere al canjearse y caduca a
    // los 30 min. Un segundo canje del mismo token da 401.
    if (path === "/api/brain/session" && method === "POST") {
      const body = await readJson(req);
      const raw = typeof body.token === "string" ? body.token : "";
      if (!raw) return err("Falta el token del cerebro.", 400);
      let tenant: Tenant;
      try {
        tenant = await redeemBrainToken(env.DB, raw, requireTenant(env));
      } catch (e) {
        return err(e instanceof Error ? e.message : "Token del cerebro inválido.", 401);
      }
      const token = await createSessionToken("cerebro", tenant, env.SESSION_SECRET, BRAIN_SESSION_TTL_MS);
      return json({ ok: true, token, tenant, expires_at: Date.now() + BRAIN_SESSION_TTL_MS });
    }

    // --- Puente de configuración para el cerebro ---
    // GET /api/bridge/settings?tenant=sebastian con
    // Authorization: Bearer <BRIDGE_TOKEN> (el secreto BRIDGE_TOKEN
    // del Worker; nunca se lee ni se imprime, solo se compara).
    // Devuelve {"settings": {...}} con los valores EFECTIVOS
    // (defaults incluidos): layout, subtitle_style, duración,
    // viralidad/SEO mínimos, topes, categoría, miniatura, hype y
    // marca de agua. El cerebro lo lee en cada decisión; sin sesión
    // de usuario.
    //
    // Marca de agua: el cerebro no recibe solo el id del logo
    // (no puede leer R2), sino watermark_url: una URL firmada
    // (60 min, /api/public-asset) del logo ACTIVO del tenant, más
    // watermark_enabled/_opacity/_size_px/_position. Si la marca
    // está activada pero no hay logo activo resoluble (borrado, o
    // el objeto ya no está en R2), el puente la apaga SOLO en esta
    // respuesta (sin tocar D1) para que el render no falle por un
    // logo que no existe. Contrato completo hacia el render en
    // DECISIONS.md ("Marca de agua").
    if (path === "/api/bridge/settings" && method === "GET") {
      const auth = req.headers.get("Authorization") ?? "";
      const bearer = /^Bearer\s+(.+)$/i.exec(auth.trim())?.[1] ?? "";
      // Dos vías: el secreto compartido BRIDGE_TOKEN (compatibilidad)
      // o una sesión válida (la del cerebro, canjeada del token de
      // un solo uso). Con sesión, el tenant sale de la sesión y debe
      // coincidir con ?tenant= si viene: nunca se lee otro tenant.
      const tenantRaw = url.searchParams.get("tenant") ?? "";
      let tenantParam: Tenant;
      const workerTenant = requireTenant(env);
      if (env.BRIDGE_TOKEN && bearer && bearer === env.BRIDGE_TOKEN) {
        if (tenantRaw !== workerTenant) {
          return err(`Bridge: ?tenant= debe ser ${workerTenant}.`, 400);
        }
        tenantParam = workerTenant;
      } else {
        const session = await requireSession(req, env);
        if (!session) return err("Bridge no autorizado.", 401);
        if (tenantRaw && tenantRaw !== session.tenant) {
          return err("Bridge: el tenant no coincide con la sesión.", 403);
        }
        tenantParam = session.tenant;
      }
      const raw = await getSettings(env.DB, tenantParam);
      const eff = effectiveSettings(raw);
      let watermarkUrl: string | null = null;
      let watermarkEnabled = eff.watermark_enabled;
      if (watermarkEnabled && eff.watermark_logo_id) {
        const logo = await getWatermarkLogo(env.DB, eff.watermark_logo_id, tenantParam);
        const head = logo ? await env.CLIPS_BUCKET.head(logo.r2_key) : null;
        if (logo && head) {
          watermarkUrl = await signWatermarkUrl(env, logo.r2_key);
          if (!watermarkUrl) watermarkEnabled = false;
        } else {
          watermarkEnabled = false;
        }
      }
      return json({ ok: true, settings: { ...eff, watermark_enabled: watermarkEnabled, watermark_url: watermarkUrl } });
    }

    // --- Puente de credenciales para el cerebro ---
    // GET /api/bridge/credentials -> credenciales descifradas DEL
    // TENANT DE LA SESIÓN (2026-11-19, cerebro genérico). Solo una
    // sesión válida (la del cerebro, canjeada del token de un solo
    // uso): el tenant sale de la sesión, nunca de un parámetro, así
    // un tenant no puede pedir las credenciales de otro. Devuelve
    // {twitch, gemini, youtube:{es,en}, zernio:{es,en},
    // tiktok_direct:{es,en}, meta, clip_public_base_url} (nulos lo
    // que falte; kaez sin twitch => twitch null y el cerebro usa el
    // camino público GQL: la solución es añadir la cuenta EN EL
    // PANEL, no en GitHub). NUNCA se registra ni se cachea fuera
    // de la respuesta: el cerebro las materializa en el runner y
    // las enmascara en los logs.
    if (path === "/api/bridge/credentials" && method === "GET") {
      const session = await requireSession(req, env);
      if (!session) return err("Bridge no autorizado.", 401);
      const creds = await resolveBrainCredentials(env, session.tenant);
      return json({ ok: true, tenant: session.tenant, ...creds });
    }

    // --- Aviso del vigilante por Telegram ---
    // POST /api/bridge/notify {message} -> {ok: true} (2026-11-19,
    // activación del vigilante). La usa el cerebro
    // (clipagent.watchdog, método notify_telegram) cuando tras
    // intentarlo todo sigue atascado: manda su diagnóstico al chat
    // de Telegram vinculado AL TENANT DE LA SESIÓN (la misma
    // configuración que usa la alerta de atascado; sin vínculo no
    // hay a quién avisar y se devuelve ok:false sin inventar chat
    // IDs ni tokens). El mensaje se recorta a
    // BRIDGE_NOTIFY_MAX_CHARS (2000) y se guarda ADEMÁS en
    // worker_events con kind "watchdog": así el diagnóstico queda
    // en Actividad reciente del panel y la alerta de atascado
    // (maybeAlertWorkerStuck) puede leerlo y citarlo. El log no
    // depende de que Telegram funcione: un fallo de envío no borra
    // el diagnóstico. Sesión válida obligatoria, igual que el
    // resto de /api/bridge/*; el tenant sale de la sesión, nunca
    // del cuerpo.
    if (path === "/api/bridge/notify" && method === "POST") {
      const session = await requireSession(req, env);
      if (!session) return err("Bridge no autorizado.", 401);
      const body = await readJson(req);
      const raw = typeof body?.message === "string" ? body.message.trim() : "";
      if (!raw) return err("message no puede estar vacío.", 400);
      const message = raw.length > BRIDGE_NOTIFY_MAX_CHARS
        ? `${raw.slice(0, BRIDGE_NOTIFY_MAX_CHARS - 1)}…`
        : raw;
      await logWorkerEvent(env.DB, "watchdog", message, session.tenant).catch(() => {});
      if (!botConfigured(env)) return json({ ok: false, error: "Telegram no configurado." }, 503);
      const link = await getTelegramLinkByTenant(env.DB, session.tenant).catch(() => null);
      if (!link) return json({ ok: false, error: "Sin chat de Telegram vinculado para este tenant." }, 503);
      const sent = await sendTelegram(env, link.chat_id, message).catch(() => null);
      if (!sent || !sent.ok) {
        return json({ ok: false, error: sent?.error ?? `Telegram respondió HTTP ${sent?.status ?? 0}.` }, 502);
      }
      return json({ ok: true });
    }

    // --- Callback OAuth del onboarding (sin sesión: el state
    // firmado lleva proveedor + tenant y caduca en 10 min) ---
    const oauthCallbackMatch = /^\/api\/oauth\/(google|meta)\/callback$/.exec(path);
    if (oauthCallbackMatch && method === "GET") {
      const provider = oauthCallbackMatch[1] as string;
      const state = await verifyOAuthState(url.searchParams.get("state"), env.SESSION_SECRET, requireTenant(env));
      if (!state || state.provider !== provider) {
        return oauthReturn(provider, false, "El estado OAuth no es válido o caducó (10 min). Vuelve a pulsar Conectar.");
      }
      const tenant = state.tenant;
      // La cuenta a la que va el token: viene en el state (la puso /start
      // desde la tarjeta pulsada). Sin ella, legado: español (una sola
      // cuenta por plataforma). Si la cuenta ya no existe o es de otra
      // plataforma, se rechaza: nunca se guarda el token en otra cuenta.
      let language: "es" | "en" = "es";
      if (state.accountId !== undefined) {
        const wantPlatform = provider === "google" ? "youtube" : "meta";
        const target = await getApiAccount(env.DB, state.accountId, tenant);
        if (!target || target.platform !== wantPlatform) {
          return oauthReturn(provider, false, "La cuenta de este OAuth ya no existe. Vuelve a pulsar Conectar en su tarjeta.");
        }
        language = target.language === "en" ? "en" : "es";
      }
      const denied = url.searchParams.get("error");
      if (denied) {
        return oauthReturn(provider, false, `Conexión cancelada en ${provider === "google" ? "Google" : "Meta"} (${denied}).`);
      }
      const code = url.searchParams.get("code") ?? "";
      if (!code) return oauthReturn(provider, false, `${provider === "google" ? "Google" : "Meta"} no devolvió ningún código. Vuelve a intentarlo.`);
      try {
        if (provider === "google") {
          const clientId = (env.GOOGLE_CLIENT_ID || "").trim();
          const clientSecret = (env.GOOGLE_CLIENT_SECRET || "").trim();
          if (!clientId || !clientSecret) return oauthReturn(provider, false, oauthMissingMessage(provider));
          const body = new URLSearchParams({
            code, client_id: clientId, client_secret: clientSecret,
            redirect_uri: oauthRedirectUri(provider), grant_type: "authorization_code",
          });
          const tok = await httpJson("POST", "https://oauth2.googleapis.com/token", { "Content-Type": "application/x-www-form-urlencoded" }, body.toString());
          const access = typeof tok.json.access_token === "string" ? tok.json.access_token : "";
          const refresh = typeof tok.json.refresh_token === "string" ? tok.json.refresh_token : "";
          if (tok.status !== 200 || !access) {
            return oauthReturn(provider, false, "Google no dio el token: vuelve a pulsar Conectar.");
          }
          if (!refresh) {
            return oauthReturn(provider, false, "Google no devolvió refresh_token: quita el acceso en tu cuenta de Google y vuelve a conectar.");
          }
          const credential = JSON.stringify({
            token: access, refresh_token: refresh, token_uri: "https://oauth2.googleapis.com/token",
            client_id: clientId, client_secret: clientSecret, scopes: GOOGLE_SCOPES.split(" "),
          });
          const saved = await upsertAccountCredential(env, tenant, "youtube", "YouTube", credential,
            { client_id: clientId, client_secret: clientSecret, refresh_token: refresh }, language);
          const acc = await getApiAccount(env.DB, saved.id, tenant);
          const outcome = acc ? await verifyApiAccount(env, tenant, acc) : { ok: false, message: "No se pudo guardar la cuenta." };
          return oauthReturn(provider, outcome.ok, outcome.ok ? "YouTube conectado por Google." : outcome.message);
        }
        // Meta: código → token corto → token largo → token de Página.
        const appId = (env.META_APP_ID || "").trim();
        const appSecret = (env.META_APP_SECRET || "").trim();
        if (!appId || !appSecret) return oauthReturn(provider, false, oauthMissingMessage(provider));
        const short = await httpJson("GET",
          `https://graph.facebook.com/v26.0/oauth/access_token?client_id=${encodeURIComponent(appId)}&redirect_uri=${encodeURIComponent(oauthRedirectUri(provider))}&client_secret=${encodeURIComponent(appSecret)}&code=${encodeURIComponent(code)}`);
        const shortToken = typeof short.json.access_token === "string" ? short.json.access_token : "";
        if (short.status !== 200 || !shortToken) {
          return oauthReturn(provider, false, "Meta no dio el token: vuelve a pulsar Conectar.");
        }
        const long = await httpJson("GET",
          `https://graph.facebook.com/v26.0/oauth/access_token?grant_type=fb_exchange_token&client_id=${encodeURIComponent(appId)}&client_secret=${encodeURIComponent(appSecret)}&fb_exchange_token=${encodeURIComponent(shortToken)}`);
        const longToken = typeof long.json.access_token === "string" ? long.json.access_token : shortToken;
        const pages = await httpJson("GET", `https://graph.facebook.com/v26.0/me/accounts?access_token=${encodeURIComponent(longToken)}`);
        const list = Array.isArray(pages.json.data) ? pages.json.data as Array<Record<string, unknown>> : [];
        const page = list[0];
        const pageToken = typeof page?.access_token === "string" ? page.access_token : "";
        const pageId = typeof page?.id === "string" ? page.id : "";
        if (!pageToken || !pageId) {
          return oauthReturn(provider, false, "Meta no devolvió ninguna Página: crea o reclama una Página de Facebook y vuelve a conectar.");
        }
        let igUserId = "";
        const ig = await httpJson("GET", `https://graph.facebook.com/v26.0/${encodeURIComponent(pageId)}?fields=instagram_business_account&access_token=${encodeURIComponent(pageToken)}`);
        const igAcc = ig.json.instagram_business_account as Record<string, unknown> | undefined;
        if (igAcc && typeof igAcc.id === "string") igUserId = igAcc.id;
        const saved = await upsertAccountCredential(env, tenant, "meta", "Meta", pageToken,
          { ig_user_id: igUserId, page_id: pageId }, language);
        const acc = await getApiAccount(env.DB, saved.id, tenant);
        const outcome = acc ? await verifyApiAccount(env, tenant, acc) : { ok: false, message: "No se pudo guardar la cuenta." };
        return oauthReturn(provider, outcome.ok, outcome.ok ? "Meta conectado." : outcome.message);
      } catch (e) {
        return oauthReturn(provider, false, `Falló la conexión con ${provider}: ${e instanceof Error ? e.message : String(e)}`);
      }
    }

    // --- Servidor MCP (2026-11-21): /mcp expone las operaciones
    // del panel como herramientas (list_jobs, get_job, get_clip,
    // list_clips, get_settings, retry_job, verify_url) para que un
    // agente las maneje por herramientas en vez de por navegador.
    // Auth propia (BRIDGE_TOKEN o sesión) dentro de
    // handleMcpRequest; va ANTES del bloque de sesión porque /mcp
    // no empieza por /api/ y el puente no usa sesión de usuario.
    if (path === "/mcp") {
      return handleMcpRequest(req, env, _ctx);
    }

    // --- Resto: exige sesión; el tenant viene de la sesión ---
    if (!path.startsWith("/api/")) return err("No encontrado.", 404);
    const session = await requireSession(req, env);
    if (!session) return err("Inicia sesión primero.", 401);
    const tenant: Tenant = session.tenant;

    // --- Worker: UN solo botón Iniciar / Parar por panel ---
    if (path === "/api/worker/state" && method === "GET") {
      const row = await env.DB.prepare("SELECT running, started_at, last_note, last_live_dispatch_at FROM worker_state WHERE tenant_id = ?").bind(tenant).first<{
        running: number;
        started_at: number | null;
        last_note: string;
        last_live_dispatch_at: number | null;
      }>();
      const activity = await env.DB.prepare("SELECT at, kind, message FROM worker_events WHERE tenant_id = ? ORDER BY at DESC LIMIT 50").bind(tenant).all<{
        at: number;
        kind: string;
        message: string;
      }>();
      const validation = await collectWorkerSupply(env.DB, tenant, Date.now(), { env }).catch(() => null);
      // Fase en vivo del cerebro (2026-11-19): qué está haciendo
      // AHORA + último barrido, último clip y próxima corrida. La
      // fase no cambia el veredicto del validador (ATASCADO sigue
      // siendo solo: directos activos y sin clip en 60 min).
      const nowMs = Date.now();
      const phaseRow = await getWorkerPhase(env.DB, tenant);
      const lastSweepAt = row?.last_live_dispatch_at ?? null;
      return json({
        ok: true,
        running: row?.running === 1,
        startedAt: row?.started_at ?? null,
        lastNote: row?.last_note ?? "",
        activity: activity.results,
        validation,
        phase: phaseView(phaseRow, nowMs),
        lastSweepAt,
        lastClipAt: validation?.lastClipAt ?? null,
        nextRunAt: nextBrainRunAt(lastSweepAt),
      });
    }
    // Validador de suministro (2026-11-18): ¿de verdad llegan clips
    // a la Cola con el Worker en marcha, o solo dice "en marcha"?
    if (path === "/api/worker/validate" && method === "GET") {
      const validation = await collectWorkerSupply(env.DB, tenant, Date.now(), { env });
      return json({ ok: true, ...validation });
    }
    // Fase en vivo del cerebro (2026-11-19): el cerebro reporta qué
    // está haciendo AHORA (buscando_picos / cortando_clip / modo_vod
    // / sin_directos). Sesión del panel (el cerebro entra como
    // muse-cerebro); el tenant viene de la sesión, nunca del cuerpo.
    if (path === "/api/worker/phase" && method === "POST") {
      const body = await readJson(req);
      const res = await handleReportPhase(env.DB, tenant, body, Date.now());
      return json(res.body, res.status);
    }
    // Evento de clip del cerebro (2026-11-19, visibilidad): el cerebro
    // reporta cada clip cortado / encolado / descartado con su motivo.
    // Misma sesión que /api/worker/phase; el tenant viene de la sesión.
    if (path === "/api/worker/event" && method === "POST") {
      const body = await readJson(req);
      const res = await handleReportClipEvent(env.DB, tenant, body, Date.now());
      return json(res.body, res.status);
    }
    if (path === "/api/worker/start" && method === "POST") {
      await setWorkerRunning(env.DB, tenant, true, "Worker iniciado desde el panel: la cola avanza sola a partir de ahora.");
      // Vía principal: además del flag D1, dispara el workflow del
      // cerebro en GitHub. Sin bloquear la respuesta (waitUntil).
      _ctx.waitUntil(dispatchCerebro(env, "panel-start", tenant));
      return json({ ok: true, running: true });
    }
    if (path === "/api/worker/stop" && method === "POST") {
      await setWorkerRunning(env.DB, tenant, false, "Worker parado desde el panel: la cola queda en pausa (nada se pierde).");
      // Sin dispatch: el flag D1 es la fuente de verdad y el
      // workflow no tiene nada que hacer al parar.
      return json({ ok: true, running: false });
    }
    // Avance manual de un ciclo (para probar sin esperar al cron).
    // Hace lo mismo que el cron: ciclo + dispatch en vivo si toca.
    // force=true: un humano pidiendo un ciclo lo tiene (la puerta
    // A3 no frena el avance manual).
    if (path === "/api/worker/run-once" && method === "POST") {
      const result = await runWorkerCycle(env, tenant, { force: true });
      if (result.ran) await maybeDispatchLive(env, tenant);
      // Kaez (2026-11-20): el Worker viejo es el único que puede
      // descifrar sus credenciales (base vieja, clave vieja); su ciclo
      // corre aquí porque el Worker nuevo no descifra. Solo manual
      // (el cron de avc está vacío); sin dispatch en vivo para kaez.
      const kaezResult = await runWorkerCycle(env, "kaez", { force: true });
      return json({ ok: true, ...result, kaez: kaezResult });
    }

    // --- Asistente de onboarding (2026-11-18, ONBOARDING-2026-11-18.md) ---
    // Para un usuario nuevo: copia las REGLAS COMUNES
    // (DEFAULT_PANEL_SETTINGS) como base (solo settings, nunca
    // credenciales), conecta SUS cuentas por OAuth o pegado, verifica
    // cada una de verdad y deja el Worker listo para Iniciar (parado
    // por defecto: nadie lo arranca por él). Desde 2026-11-19 la base
    // NO son las reglas de otro tenant (hallazgo ALTO 5).
    if (path === "/api/onboarding/status" && method === "GET") {
      const ownRaw = await getSettings(env.DB, tenant);
      const accounts = await listApiAccounts(env.DB, tenant);
      const eff = effectiveSettings(ownRaw);
      const byPlatform = (p: string) => accounts.find((a) => a.platform === p);
      const step = (p: string) => {
        const a = byPlatform(p);
        return {
          platform: p,
          exists: !!a,
          verified: a?.verify_ok === 1,
          status: a?.status ?? "sin_crear",
          verify_message: a?.verify_message ?? "",
          has_credential: !!(a?.credential_enc || a?.credential),
        };
      };
      const steps = ["youtube", "tiktok", "meta", "twitch", "gemini"].map(step);
      const settingsCopied = Object.keys(ownRaw).length > 0;
      const complete = settingsCopied && steps.every((s) => s.verified);
      return json({
        ok: true,
        onboarding: {
          settings_copied: settingsCopied,
          settings: eff,
          worker_running: await isWorkerRunning(env.DB, tenant),
          accounts: steps,
          oauth: {
            google: { mode: "oauth", configured: oauthConfigured(env, "google"), redirect_uri: oauthRedirectUri("google"), missing: oauthConfigured(env, "google") ? "" : oauthMissingMessage("google") },
            meta: { mode: "oauth", configured: oauthConfigured(env, "meta"), redirect_uri: oauthRedirectUri("meta"), missing: oauthConfigured(env, "meta") ? "" : oauthMissingMessage("meta") },
            zernio: { mode: "dashboard", configured: true, connect_url: ZERNIO_CONNECT_URL, missing: "" },
            twitch: { mode: "paste", configured: true, console_url: TWITCH_CONSOLE_URL, missing: "" },
            gemini: { mode: "paste", configured: true, console_url: GEMINI_KEYS_URL, missing: "" },
          },
          complete,
        },
      });
    }
    if (path === "/api/onboarding/copy-settings" && method === "POST") {
      const body = await readJson(req);
      const ownRaw = await getSettings(env.DB, tenant);
      const force = body.force === true;
      if (!force && Object.keys(ownRaw).length > 0) {
        return json({ ok: true, already: true, copied: false, settings: effectiveSettings(ownRaw) });
      }
      // La base son los DEFAULTS COMUNES (allowlist: sin secretos
      // ni credenciales; telegram_* y claves desconocidas quedan
      // fuera). Un tenant nuevo nace con las reglas comunes, NO
      // con las de otro tenant: leer "sebastian" como base era el
      // hallazgo ALTO 5 de la auditoría multi-tenant (2026-11-19).
      const base = extractBaseSettings(effectiveSettings({}));
      const merged = force ? { ...ownRaw, ...base } : base;
      await saveSettings(env.DB, tenant, effectiveSettings(merged));
      return json({ ok: true, already: false, copied: true, settings: effectiveSettings(await getSettings(env.DB, tenant)) });
    }
    // Twitch (Client ID/Secret) y Gemini (API key): solo pegado, con
    // link exacto en el front. Se guardan cifrados por tenant como
    // cualquier cuenta API y se verifican de verdad con /verify.
    if (path === "/api/onboarding/credentials" && method === "POST") {
      const body = await readJson(req);
      const platform = typeof body.platform === "string" ? body.platform.trim().toLowerCase() : "";
      if (platform !== "twitch" && platform !== "gemini") {
        return err("platform inválida: usa twitch o gemini (YouTube/Meta van por OAuth, TikTok por Zernio).", 400);
      }
      const credential = typeof body.credential === "string" ? body.credential.trim() : "";
      if (!credential) return err("Falta la credencial.", 400);
      let extraObj: Record<string, unknown> | null = null;
      if (platform === "twitch") {
        const rawExtra = body.extra;
        const parsed = typeof rawExtra === "string"
          ? (() => { try { return JSON.parse(rawExtra) as Record<string, unknown>; } catch { return null; } })()
          : typeof rawExtra === "object" && rawExtra !== null ? rawExtra as Record<string, unknown> : null;
        const clientId = typeof parsed?.client_id === "string" ? (parsed.client_id as string).trim() : "";
        if (!clientId) return err("Falta el Client ID de Twitch: pégalo en el campo Client ID (va en Extra).", 400);
        extraObj = { client_id: clientId };
      }
      const label = platform === "twitch" ? "Twitch" : "Gemini";
      const saved = await upsertAccountCredential(env, tenant, platform, label, credential, extraObj);
      return json({ ok: true, account: saved });
    }
    // Verifica DE VERDAD cada cuenta con credencial del tenant y
    // devuelve el resultado por plataforma. No publica nada.
    if (path === "/api/onboarding/verify-all" && method === "POST") {
      const accounts = await listApiAccounts(env.DB, tenant);
      const results: Array<{ platform: string; label: string; ok: boolean; message: string }> = [];
      for (const acc of accounts) {
        if (!acc.credential_enc && !acc.credential) {
          results.push({ platform: acc.platform, label: acc.label, ok: false, message: "Sin credencial: conéctala primero." });
          continue;
        }
        const outcome = await verifyApiAccount(env, tenant, acc);
        results.push({ platform: acc.platform, label: acc.label, ok: outcome.ok, message: outcome.message });
      }
      return json({ ok: true, results, all_ok: results.length > 0 && results.every((r) => r.ok) });
    }
    // OAuth: estado del botón Conectar (configurado o qué falta).
    const oauthStatusMatch = /^\/api\/oauth\/(google|meta|zernio|twitch|gemini)\/status$/.exec(path);
    if (oauthStatusMatch && method === "GET") {
      const provider = oauthStatusMatch[1] as string;
      if (provider === "zernio") {
        return json({ ok: true, provider, mode: "dashboard", configured: true, connect_url: ZERNIO_CONNECT_URL, missing: "" });
      }
      if (provider === "twitch") {
        return json({ ok: true, provider, mode: "paste", configured: true, console_url: TWITCH_CONSOLE_URL, missing: "" });
      }
      if (provider === "gemini") {
        return json({ ok: true, provider, mode: "paste", configured: true, console_url: GEMINI_KEYS_URL, missing: "" });
      }
      const configured = oauthConfigured(env, provider);
      return json({
        ok: true, provider, mode: "oauth", configured,
        redirect_uri: oauthRedirectUri(provider),
        missing: configured ? "" : oauthMissingMessage(provider),
      });
    }
    // OAuth: inicio (navegación top-level con cookie de sesión).
    // Sin cliente configurado, 400 con el motivo exacto (el front
    // lo comprueba antes con /status y no navega).
    const oauthStartMatch = /^\/api\/oauth\/(google|meta)\/start$/.exec(path);
    if (oauthStartMatch && method === "GET") {
      const provider = oauthStartMatch[1] as string;
      if (!oauthConfigured(env, provider)) return err(oauthMissingMessage(provider), 400);
      // La cuenta a la que va el token: la tarjeta pulsada (?account=<id>).
      // Sin ella, legado (una sola cuenta por plataforma). Una cuenta
      // inexistente, de otro tenant o de otra plataforma es 400: nunca
      // se arranca un OAuth que acabaría guardando en otra cuenta.
      let accountId: number | undefined;
      const accountParam = url.searchParams.get("account");
      if (accountParam !== null) {
        const parsed = Number(accountParam);
        if (!Number.isInteger(parsed) || parsed <= 0) return err("Cuenta OAuth no válida.", 400);
        const wantPlatform = provider === "google" ? "youtube" : "meta";
        const acc = await getApiAccount(env.DB, parsed, tenant);
        if (!acc || acc.platform !== wantPlatform) return err("Cuenta OAuth no válida.", 400);
        accountId = parsed;
      }
      const state = await createOAuthState(provider, tenant, env.SESSION_SECRET, accountId);
      const redirect = oauthRedirectUri(provider);
      if (provider === "google") {
        const u = new URL("https://accounts.google.com/o/oauth2/v2/auth");
        u.searchParams.set("client_id", (env.GOOGLE_CLIENT_ID || "").trim());
        u.searchParams.set("redirect_uri", redirect);
        u.searchParams.set("response_type", "code");
        u.searchParams.set("scope", GOOGLE_SCOPES);
        u.searchParams.set("access_type", "offline");
        u.searchParams.set("prompt", "consent");
        u.searchParams.set("state", state);
        return new Response(null, { status: 302, headers: { Location: u.toString() } });
      }
      const u = new URL("https://www.facebook.com/v26.0/dialog/oauth");
      u.searchParams.set("client_id", (env.META_APP_ID || "").trim());
      u.searchParams.set("redirect_uri", redirect);
      u.searchParams.set("scope", META_SCOPES);
      u.searchParams.set("state", state);
      return new Response(null, { status: 302, headers: { Location: u.toString() } });
    }

    // --- Notificaciones (avisos visibles en el panel) ---
    if (path === "/api/notifications" && method === "GET") {
      const rows = await env.DB.prepare("SELECT * FROM notifications WHERE tenant_id = ? ORDER BY created_at DESC LIMIT 50").bind(tenant).all();
      return json({ ok: true, notifications: rows.results });
    }
    const readMatch = /^\/api\/notifications\/(\d+)\/read$/.exec(path);
    if (readMatch && method === "POST") {
      await env.DB.prepare("UPDATE notifications SET read_at = ? WHERE id = ? AND tenant_id = ?").bind(Date.now(), Number(readMatch[1]), tenant).run();
      return json({ ok: true });
    }

    // --- Cola / clips (lectura) ---
    // GET /api/clips — Cola (en_carpeta/aprobado/programado).
    // ?status=publicado lista los ya publicados (sección "Subidos"
    // de la Cola, retención 24 h, 0039): ahí se ve por clip dónde
    // se subió, dónde falta y si espera re-subida.
    if (path === "/api/clips" && method === "GET") {
      if (url.searchParams.get("status") === "publicado") {
        const clips = await listPublishedClips(env.DB, tenant);
        return json({ ok: true, clips: clips.map(clipOut) });
      }
      const clips = await listQueueClips(env.DB, tenant);
      return json({ ok: true, clips: clips.map(clipOut) });
    }
    // --- Cola / clips (crear): POST /api/clips (2026-11-18) ---
    // Antes no había forma de meter un clip en la Cola desde fuera:
    // el Worker solo leía D1 y el cerebro nunca escribió en ella.
    // El clip nace en 'en_carpeta' (visible en la Cola), con la
    // duración validada por la regla de oro (40–60 s, nunca 25–35)
    // y publish_blocked derivado de subtitles_complete. El MP4 se
    // sube después con PUT /api/clips/:id/video; la publicación con
    // POST /api/clips/:id/publish (con sus puertas/gates).
    if (path === "/api/clips" && method === "POST") {
      const body = await readJson(req);
      // La ficha del clip trae influencer_id como handle ("rubius"):
      // se resuelve al id numérico del influencer de este tenant.
      // Un handle que no existe es 400 con el motivo, nunca un id
      // inventado ni un null silencioso.
      if (typeof body.influencer_id === "string" && !/^\d+$/.test(body.influencer_id.trim())) {
        const handle = body.influencer_id.trim();
        const found = (await listInfluencers(env.DB, tenant)).find((r) => normalizeHandle(r.name) === normalizeHandle(handle));
        if (!found) return err(`influencer_id: no hay ningún influencer con el handle "${handle}" en este panel.`, 400);
        body.influencer_id = found.id;
      }
      const parsed = parseClipInput(body, await getSettings(env.DB, tenant));
      if (typeof parsed === "string") {
        // Rechazo al encolar (viralidad/SEO/duración...): que quede
        // en Actividad con el motivo exacto. Sin este registro, un
        // clip rechazado es invisible: el cerebro lo corta, lo
        // renderiza, el panel lo tira con 400 y nadie ve por qué la
        // Cola no crece (medido 2026-11-18 22:43: 2 clips renderizados
        // y 0 encolados por Viralidad 45/46 < 50). No rompe el 400.
        await logWorkerEvent(
          env.DB,
          "rechazo",
          `Clip rechazado al encolar («${String(body.title ?? "").slice(0, 60) || "sin título"}»): ${parsed}`,
          tenant,
        ).catch(() => {});
        return err(parsed, 400);
      }
      // Freno de cuota D1 (v5.1, 2026-11-21): POST /api/clips no tenía
      // guardián de presupuesto — el cerebro podía crear clips al 99%
      // de cuota y quemar el límite diario (causa del 2026-11-20:
      // el cerebro cada minuto sin credenciales, ~60.000 escrituras).
      // La creación de clips es "bulk-job": se frena en "restricted"
      // (70%) y "essential-only" (90%). El contador se cachea 5 min
      // en memoria y el guardián es fail-open si no se puede leer.
      const guard = await createBudgetGuard(env, env.DB, tenant, Date.now());
      if (!guard.shouldWrite("bulk-job")) {
        const pct = guard.governingRatio === null ? null : Math.round(guard.governingRatio * 100);
        return err(
          pct === null
            ? "Cuota D1 alta: creación de clips pausada hasta el reset de las 21:00."
            : `Cuota D1 al ${pct}% (nivel ${guard.level}): creación de clips pausada hasta el reset de las 21:00.`,
          429,
        );
      }
      const clip = await createClip(env.DB, tenant, parsed);
      await logClipEvent(env.DB, clip.id, "detectado", session.sub,
        `Clip creado en la Cola: ${clip.title} (${clip.start_sec}–${clip.end_sec} s, ${parsePlatforms(clip.platforms).join(", ")}).`,
        false, tenant);
      return json({ ok: true, clip: clipOut(clip) }, 201);
    }
    const clipMatch = /^\/api\/clips\/(\d+)$/.exec(path);
    if (clipMatch && method === "GET") {
      const clip = await getClip(env.DB, Number(clipMatch[1]), tenant);
      if (!clip) return err("Clip no encontrado.", 404);
      // Retención 24 h (0039): desglose por plataforma desde los
      // jobs (dónde se subió con su link, dónde falta, si espera
      // re-subida por takedown) + resumen de retención ("subidos").
      const breakdown = await clipPublishBreakdown(env.DB, clip.id, tenant);
      return json({ ok: true, clip: clipOut(clip), publish_breakdown: breakdown, retention: clipRetentionSummary(clip, breakdown) });
    }
    const trackingMatch = /^\/api\/clips\/(\d+)\/tracking$/.exec(path);
    if (trackingMatch && method === "GET") {
      const clip = await getClip(env.DB, Number(trackingMatch[1]), tenant);
      if (!clip) return err("Clip no encontrado.", 404);
      const events = await env.DB.prepare("SELECT * FROM clip_events WHERE clip_id = ? AND tenant_id = ? ORDER BY at ASC").bind(Number(trackingMatch[1]), tenant).all();
      return json({ ok: true, events: events.results });
    }
    // --- Tracking (Frente 2, M3): registrar un paso ---
    // POST /api/tracking/step: el cerebro (o el panel) registra un
    // paso del pipeline sin escribir directo en D1. El clip debe
    // ser del tenant de la sesión. `step` usa las claves canónicas
    // del diseño de tracking; `estado` es completado | fallido |
    // en_curso (por defecto completado); `message` es el motivo.
    if (path === "/api/tracking/step" && method === "POST") {
      const body = await readJson(req);
      const clipId = body.clip_id;
      if (typeof clipId !== "number" || !Number.isInteger(clipId) || clipId <= 0) {
        return err("clip_id debe ser el id numérico del clip.", 400);
      }
      const clip = await getClip(env.DB, clipId, tenant);
      if (!clip) return err("Clip no encontrado.", 404);
      const step = typeof body.step === "string" ? body.step.trim() : "";
      if (!TRACKING_STEPS.includes(step)) {
        return err(`step inválido: usa ${TRACKING_STEPS.join(", ")}.`, 400);
      }
      const estado = body.estado === undefined ? "completado" : typeof body.estado === "string" ? body.estado.trim() : "";
      if (!["completado", "fallido", "en_curso"].includes(estado)) {
        return err("estado inválido: usa completado, fallido o en_curso.", 400);
      }
      const failed = estado === "fallido" || body.failed === true;
      if (body.failed !== undefined && body.failed !== true && body.failed !== false) {
        return err("failed debe ser true o false.", 400);
      }
      const message = body.message === undefined ? "" : typeof body.message === "string" ? body.message : null;
      if (message === null) return err("message debe ser texto.", 400);
      if (message.length > 2000) return err("message no puede superar 2000 caracteres.", 400);
      const actor = body.actor === undefined ? session.sub : typeof body.actor === "string" ? body.actor.trim() : null;
      if (actor === null) return err("actor debe ser texto.", 400);
      if (!actor) return err("actor no puede estar vacío.", 400);
      if (actor.length > 100) return err("actor no puede superar 100 caracteres.", 400);
      const at = Date.now();
      await logClipEvent(env.DB, clipId, step, actor, message, failed, tenant);
      return json({ ok: true, event: { clip_id: clipId, step, estado, actor, message, failed, at } }, 201);
    }

    // --- Editor (fase 2, Dev 2): preview visto + edición del clip ---
    // POST /api/clips/:id/preview-seen levanta el freno de publishGate
    // ("falta ver el preview en el Editor"): sin este endpoint ningún
    // clip con preview_seen=0 podía publicarse. Registra el evento en
    // Tracking con actor = usuario de la sesión (paso "revision").
    const previewSeenMatch = /^\/api\/clips\/(\d+)\/preview-seen$/.exec(path);
    if (previewSeenMatch && method === "POST") {
      const clipId = Number(previewSeenMatch[1]);
      const clip = await getClip(env.DB, clipId, tenant);
      if (!clip) return err("Clip no encontrado.", 404);
      if (clip.preview_seen === 1) return json({ ok: true, clip: clipOut(clip), already_seen: true });
      const updated = await updateClip(env.DB, clipId, tenant, { preview_seen: 1 });
      if (!updated) return err("Clip no encontrado.", 404);
      await logClipEvent(env.DB, clipId, "revision", session.sub, "Preview visto en el Editor.", false, tenant);
      return json({ ok: true, clip: clipOut(updated), already_seen: false });
    }
    // PATCH /api/clips/:id: edita SOLO subtitle_style, seo_title,
    // seo_description, seo_tags, thumbnail_text (columna thumb_text),
    // target_platforms (columna platforms), schedule (scheduled_for),
    // duration (end_sec = start_sec + duration), language (columna
    // language, "es" | "en": mueve el clip de cola, 0019) y seo_override
    // (M2: {platform, enabled}, solo con SEO 70–89). Cambiar la duración
    // invalida el preview guardado (preview_seen vuelve a 0).
    if (clipMatch && method === "PATCH") {
      const clipId = Number(clipMatch[1]);
      const clip = await getClip(env.DB, clipId, tenant);
      if (!clip) return err("Clip no encontrado.", 404);
      const body = await readJson(req);
      const parsed = parseClipPatch(body, clip, await getSettings(env.DB, tenant));
      if (typeof parsed === "string") return err(parsed, 400);
      if (Object.keys(parsed.columns).length === 0 && parsed.duration === null) {
        return err("No hay campos válidos para actualizar: usa subtitle_style, seo_title, seo_description, seo_tags, thumbnail_text, target_platforms, schedule, duration, game, language, seo_override, viral, seo_scores, hype_score, priority o score_kind.", 400);
      }
      const columns = { ...parsed.columns };
      let previewReset = false;
      if (parsed.duration !== null) {
        const oldDuration = clip.end_sec - clip.start_sec;
        if (parsed.duration !== oldDuration) {
          columns.end_sec = clip.start_sec + parsed.duration;
          columns.preview_seen = 0;
          previewReset = clip.preview_seen === 1;
        }
      }
      const updated = await updateClip(env.DB, clipId, tenant, columns);
      if (!updated) return err("Clip no encontrado.", 404);
      // Preview opcional (v3.1): la nota solo exige volver a verlo
      // si require_preview está activado; si no, verlo es opcional.
      const requirePreview = previewReset ? effectiveSettings(await getSettings(env.DB, tenant)).require_preview : false;
      return json({
        ok: true,
        clip: clipOut(updated),
        preview_reset: previewReset,
        ...(previewReset
          ? { preview_note: requirePreview
            ? "La duración cambió: el preview guardado deja de valer; hay que ver el preview otra vez antes de publicar."
            : "La duración cambió: el preview guardado deja de valer. Verlo es opcional: no bloquea la publicación." }
          : {}),
      });
    }

    // --- Analíticas (fase 2, Dev 2): mediciones reales de clip_stats ---
    // GET /api/analytics: medianas SOLO con mediciones reales (sin
    // medición, null = "sin medición", nunca 0), ranking por
    // plataforma por vistas y publicados sin medición.
    if (path === "/api/analytics" && method === "GET") {
      const { platforms, channels, unmeasured } = await computeAnalytics(env, tenant);
      return json({ ok: true, platforms, channels, unmeasured });
    }
    // --- Telegram v2 (2026-11-18): resumen diario de analíticas
    // DESDE LA WEB, multi-tenant. El Worker envía; Muse no
    // interviene. TELEGRAM_BOT_TOKEN va con `wrangler secret put`,
    // nunca en el repo. El chat ya NO es un secreto global: cada
    // tenant vincula SU chat con un código de un solo uso generado
    // aquí, con sesión iniciada (POST /api/telegram/link-code), y
    // lo confirma en el bot con /link <código>. El estado nunca
    // revela el token ni el chat_id completo, solo si el bot está
    // configurado, si hay vínculo y el chat enmascarado.
    if (path === "/api/telegram/status" && method === "GET") {
      const link = await getTelegramLinkByTenant(env.DB, tenant);
      const settings = await getSettings(env.DB, tenant);
      const lastSent =
        link?.last_sent_day ??
        (typeof settings[TELEGRAM_SENT_DAY_KEY] === "string" ? (settings[TELEGRAM_SENT_DAY_KEY] as string) : null);
      return json({
        ok: true,
        configured: telegramConfigured(env),
        linked: !!link,
        chat_id_masked: link ? maskChatId(link.chat_id) : null,
        last_sent_day: lastSent,
      });
    }
    if (path === "/api/telegram/link-code" && method === "POST") {
      // El código en claro solo lo ve esta sesión: en D1 queda
      // solo su hash SHA-256, caduca en 10 minutos y se borra al
      // usarse. Un solo código vigente por tenant.
      const code = generateLinkCode();
      const now = Date.now();
      await createTelegramLinkCode(env.DB, tenant, await sha256Hex(code), now + LINK_CODE_TTL_MS, now);
      return json({ ok: true, code, expires_at: now + LINK_CODE_TTL_MS });
    }
    if (path === "/api/telegram/unlink" && method === "POST") {
      await unlinkTelegramTenant(env.DB, tenant);
      await logWorkerEvent(env.DB, "telegram", "Telegram desvinculado desde Ajustes.", tenant);
      return json({ ok: true, linked: false });
    }
    if (path === "/api/telegram/test" && method === "POST") {
      const { day, hour, minute } = santiagoParts(new Date());
      const { channels } = await computeAnalytics(env, tenant);
      const totalPublished = channels.reduce((n, p) => n + p.published_clips, 0);
      const totalMeasured = channels.reduce((n, p) => n + p.measured_clips, 0);
      const text = totalPublished === 0 && totalMeasured === 0
        ? buildTestMessage(day, hour, minute)
        : buildAnalyticsMessage(channels, day);
      const link = await getTelegramLinkByTenant(env.DB, tenant);
      if (!link) {
        return json({
          ok: false,
          error: "Telegram no está vinculado: genera un código en Ajustes → Telegram y escríbelo en el bot con /link <código>.",
          preview: text,
        }, 400);
      }
      if (!botConfigured(env)) {
        return json({
          ok: false,
          error: "Telegram no está configurado: falta TELEGRAM_BOT_TOKEN como secreto del Worker (`wrangler secret put TELEGRAM_BOT_TOKEN`).",
          preview: text,
        }, 400);
      }
      const sent = await sendTelegram(env, link.chat_id, text);
      if (!sent.ok) {
        return json({ ok: false, error: `No se pudo enviar a Telegram: ${sent.error ?? `HTTP ${sent.status}`}`, preview: text }, 502);
      }
      await logWorkerEvent(env.DB, "telegram", `Prueba de Telegram enviada (${day}).`, tenant);
      return json({ ok: true, sent: true, preview: text });
    }
    // POST /api/clips/:id/stats registra una medición MANUAL
    // (respaldo: la automática mide sola cada 6 h); GET
    // /api/clips/:id/stats la lista. Nunca se inventa ni se estima
    // ningún valor.
    const clipStatsMatch = /^\/api\/clips\/(\d+)\/stats$/.exec(path);
    if (clipStatsMatch && method === "POST") {
      const clipId = Number(clipStatsMatch[1]);
      const clip = await getClip(env.DB, clipId, tenant);
      if (!clip) return err("Clip no encontrado.", 404);
      const body = await readJson(req);
      const parsed = parseStatInput(body);
      if (typeof parsed === "string") return err(parsed, 400);
      const row = await createClipStat(env.DB, tenant, clipId, parsed);
      // Hito viral (B6, v3): felicita en el chat vinculado si la
      // medición pegada supera 1.000 / 10.000 vistas (con dedup).
      await maybeNotifyViralMilestone(env, tenant, clip, row);
      return json({ ok: true, stat: statOut(row) }, 201);
    }
    if (clipStatsMatch && method === "GET") {
      const clipId = Number(clipStatsMatch[1]);
      const clip = await getClip(env.DB, clipId, tenant);
      if (!clip) return err("Clip no encontrado.", 404);
      const rows = await listClipStatsForClip(env.DB, clipId, tenant);
      return json({ ok: true, stats: rows.map(statOut) });
    }

    // --- Canales/Salud (fase 2, Dev 2) ---
    // Estado de la cuenta API real (mismos 4 estados que
    // GET /api/accounts; Meta unificada para Instagram y Facebook),
    // cuota diaria contada de publicaciones reales de hoy en
    // publish_states (tope fijo del Worker) y publicados total y
    // últimos 7 días, también de publish_states. La salud por canal
    // no existe en el Worker: "sin_datos", nunca un OK inventado.
    if (path === "/api/channels" && method === "GET") {
      const accounts = await listApiAccounts(env.DB, tenant);
      const clips = await listClipPublishStates(env.DB, tenant);
      // Topes desde Ajustes (ya no fijos): el canal muestra el tope
      // vigente, y la madurez de la cuenta puede bajarlo a 1/día.
      const eff = effectiveSettings(await getSettings(env.DB, tenant));
      const todayKey = santiagoDayKey();
      const last7 = new Set<string>();
      for (let i = 0; i < 7; i++) last7.add(santiagoDayKey(new Date(Date.now() - i * 86400000)));
      // DOS COLAS (0019): un canal por PLATAFORMA + IDIOMA (8
      // entradas: YouTube ES/EN, TikTok ES/EN, Instagram ES/EN,
      // Facebook ES/EN). Cada una lleva SU cuenta, SU cuota de hoy
      // y SUS publicados: la cuota de YouTube en español no se
      // mezcla con la de YouTube en inglés (clips.language filtra
      // los publicados; los topes vienen de la madurez de ESA
      // cuenta, que ya es por plataforma+idioma).
      const channels = CHANNEL_PLATFORMS.flatMap((p) => (["es", "en"] as const).map((lang) => {
        const panel = API_TO_PANEL[p];
        const apiAccountPlatform = p === "instagram" || p === "facebook" ? "meta" : p;
        const acc = accounts.find((a) => a.platform === apiAccountPlatform && (a.language === lang || (lang === "es" && !a.language)));
        const accountStatus = !acc
          ? "sin_crear"
          : !(acc.credential_enc || acc.credential)
            ? "sin_credencial"
            : acc.verify_ok === 1
              ? "conectada"
              : "no_conectada";
        let publishedTotal = 0;
        let publishedLast7d = 0;
        let usedToday = 0;
        for (const c of clips) {
          if (clipLanguage(c) !== lang) continue;
          const entry = parsePublishStates(c.publish_states)[panel];
          if (entry?.state !== "publicado") continue;
          publishedTotal++;
          if (!entry.publishedAt) continue;
          const ms = Date.parse(entry.publishedAt);
          if (!Number.isFinite(ms)) continue;
          const day = santiagoDayKey(new Date(ms));
          if (day === todayKey) usedToday++;
          if (last7.has(day)) publishedLast7d++;
        }
        const mat = acc ? maturityOut(acc, eff) : null;
        const cap = mat ? mat.effective_daily_limit : platformMax(eff, p);
        const plan = mat ? mat.effective_daily_plan : platformPlan(eff, p);
        return {
          platform: p,
          language: lang,
          account_status: accountStatus,
          account: acc
            ? {
                id: acc.id,
                label: acc.label,
                language: acc.language,
                verified_at: acc.verified_at,
                verify_message: acc.verify_message,
                verification_current: isAccountVerificationCurrent(acc),
                has_credential: !!(acc.credential_enc || acc.credential),
              }
            : null,
          daily_cap: cap,
          daily_cap_fixed: false,
          daily_plan: plan,
          daily_plan_fixed: false,
          maturity: mat
            ? {
                status: mat.stored_status,
                stage: mat.stage,
                days_since_first_published: mat.days_since_first_published,
                published_count: mat.published_count,
                strikes: mat.strikes,
                warmup_remaining_days: mat.warmup_remaining_days,
                warmup_remaining_pubs: mat.warmup_remaining_pubs,
              }
            : null,
          used_today: usedToday,
          remaining_today: Math.max(0, cap - usedToday),
          published_total: publishedTotal,
          published_last_7d: publishedLast7d,
          publish_window: PUBLISH_WINDOW,
          publish_window_fixed: false,
          // Franjas 2026-11-19 (hora Chile): la automática solo
          // sale en estas ventanas; el espaciamiento es 3 h (4 h
          // YouTube) y el rolling 24 h nunca pasa de 3 por cuenta.
          publish_slots: PUBLISH_SLOT_WINDOWS,
          min_spacing_hours: spacingHoursFor(eff, p),
          rolling_24h_max: eff.rolling_24h_max,
          days_per_week: daysPerWeekFor(eff, p),
          health: "sin_datos",
          health_note: "Sin datos de salud para este canal. El Worker aún no expone la salud por plataforma.",
        };
      }));
      return json({ ok: true, channels });
    }

    // --- Publicar ahora / Programar (encola; el Worker o el puente lo sube) ---
    // El encolado con gates vive en tryEnqueueClipPublish (jobs.ts):
    // ES EL MISMO camino que usa el auto-enqueue de tops medidos,
    // para que el botón y el automático nunca discrepen. Desde la
    // orden 09:25 (2026-11-21, SIN AMONTONAR) "Publicar ahora" ya
    // NO salta ningún freno anti-baneo: respeta franja, tope
    // diario, espaciamiento, cross-platform 15 min, 1-por-franja,
    // calentamiento y rolling igual que la automática. Desde el
    // refuerzo del validador (2026-11-21) TAMPOCO salta el
    // veredicto de calidad: un clip sin validar no se publica por
    // ningún camino (hay que validarlo o ver el preview). Solo
    // sigue saltando el día libre (planificación, no freno de
    // seguridad). Los avisos vuelven en `warnings` para que el
    // front los muestre.
    //
    // PROGRAMACIÓN (regla 2026-11-21 14:17: "la programación vive
    // en el panel, no en mis crons"): el body acepta `scheduled_for`
    // (ISO 8601) y `platforms` (opcional). Con `scheduled_for`
    // futuro, los jobs nacen con next_run_at = ese instante y el
    // gate se evalúa EN ESE INSTANTE (la franja que se comprueba
    // es la del instante pedido, no la actual); el ciclo los toma
    // cuando llegue la hora y el re-chequeo vuelve a aplicar todo
    // al ejecutar. Sin `platforms`, las plataformas son la unión
    // de las del clip y las de sus cuentas vinculadas (regla
    // 14:10); con `platforms`, solo esas. El `scheduled_for` del
    // clip se guarda como registro visible de lo programado; la
    // fuente de verdad de la ejecución es el next_run_at del job.
    // REPUBLICACIÓN (P0 #5): el body acepta `republish: true`
    // para publicar DE NUEVO el mismo clip en la misma plataforma
    // el mismo día (la plataforma borró el vídeo): solo salta la
    // puerta "mismo clip hoy" y el job nuevo lleva key |repubN.
    const publishMatch = /^\/api\/clips\/(\d+)\/publish$/.exec(path);
    if (publishMatch && method === "POST") {
      const clipId = Number(publishMatch[1]);
      const clip = await getClip(env.DB, clipId, tenant);
      if (!clip) return err("Clip no encontrado.", 404);
      if (clip.archived) return err("Clip archivado: no se publica.", 400);
      if (clip.status === "publicado") return err("Este clip ya está publicado.", 400);
      const pubBody = await readJson(req);
      // Plataformas explícitas (opcional): solo esas.
      let platforms: PanelPlatform[] | undefined;
      if (pubBody.platforms !== undefined && pubBody.platforms !== null) {
        if (!Array.isArray(pubBody.platforms)) return err("platforms debe ser una lista (youtube_shorts, tiktok, instagram_reels, facebook_reels).", 400);
        const list = (pubBody.platforms as unknown[]).map((p) => String(p).trim());
        for (const p of list) {
          if (!(PANEL_PLATFORMS as readonly string[]).includes(p)) return err(`Plataforma inválida en platforms: ${p}. Usa youtube_shorts, tiktok, instagram_reels o facebook_reels.`, 400);
        }
        platforms = list as PanelPlatform[];
      }
      // Instante programado (opcional): ISO 8601, futuro y dentro
      // de una franja válida (12:00-14:00, 18:00-20:00, 21:00-23:00
      // hora Chile). Sin él, publicación inmediata.
      let scheduledForMs: number | null = null;
      let scheduledForIso: string | null = null;
      if (pubBody.scheduled_for !== undefined && pubBody.scheduled_for !== null && pubBody.scheduled_for !== "") {
        if (typeof pubBody.scheduled_for !== "string") return err("scheduled_for debe ser una fecha ISO 8601.", 400);
        const ms = Date.parse(pubBody.scheduled_for.trim());
        if (!Number.isFinite(ms)) return err("scheduled_for debe ser una fecha válida (ISO 8601).", 400);
        if (ms <= Date.now()) return err("scheduled_for debe ser una fecha futura.", 400);
        if (!isWithinPublishSlot(ms)) return err("scheduled_for debe caer dentro de una franja válida (12:00–14:00, 18:00–20:00 o 21:00–23:00, hora de Chile).", 400);
        scheduledForMs = ms;
        scheduledForIso = new Date(ms).toISOString();
      }
      // Republicación explícita (P0 #5, auditoría #11): publicar
      // DE NUEVO el mismo clip en la misma plataforma el mismo
      // día (la plataforma borró el vídeo, como el 168 en FB).
      // Solo salta la puerta "mismo clip hoy"; el resto sigue
      // frenando igual y el job nuevo lleva la key con sufijo
      // |repubN. Sin este flag, un clip ya publicado hoy en esa
      // plataforma se devuelve tal cual (dedup).
      const republish = pubBody.republish === true;
      // Publicar AHORA fuera de franja (orden del usuario
      // 2026-11-22 01:07, "sube vídeos ahora"): force_now=true
      // salta SOLO la puerta de franja; el resto de puertas
      // anti-baneo (tope, calentamiento, espaciamiento, rolling,
      // 1 por plataforma, calidad, SEO) sigue frenando igual.
      const forceNow = pubBody.force_now === true;
      const { jobs, blocked, warnings } = await tryEnqueueClipPublish(env, tenant, clip, { actor: session.sub, manual: true, platforms, scheduledForMs, republish, forceNow });
      if (jobs.length === 0) {
        return err(`Ninguna plataforma pudo encolarse: ${blocked.join(" · ")}`, 400);
      }
      // El scheduled_for del clip es el registro visible de lo
      // programado (la ejecución la manda el next_run_at del job).
      if (scheduledForIso !== null) {
        await env.DB.prepare("UPDATE clips SET scheduled_for = ?, updated_at = ? WHERE id = ? AND tenant_id = ?")
          .bind(scheduledForIso, Date.now(), clipId, tenant)
          .run();
      }
      return json({ ok: true, jobs: jobs.map((j) => ({ id: j.id, platform: j.platform, state: j.state, next_run_at: j.next_run_at })), blocked, warnings, manual: true, scheduled_for: scheduledForIso, republish });
    }

    // --- Subida del MP4 a R2 ---
    const videoMatch = /^\/api\/clips\/(\d+)\/video$/.exec(path);
    if (videoMatch && method === "PUT") {
      const clipId = Number(videoMatch[1]);
      const clip = await getClip(env.DB, clipId, tenant);
      if (!clip) return err("Clip no encontrado.", 404);
      const contentType = req.headers.get("Content-Type") ?? "";
      if (!contentType.startsWith("video/")) return err("El archivo debe ser video/mp4.", 400);
      const filename = url.searchParams.get("filename") || `clip_${clipId}.mp4`;
      const key = r2KeyForClip(clipId, filename, tenant);
      const size = await uploadClipMp4(env, key, req.body as ReadableStream, contentType);
      if (size > 128 * 1024 * 1024) return err("El video supera 128 MB (límite del Worker).", 400);
      // 0016: la key queda guardada en el clip. Sin esto el MP4
      // quedaba huérfano en R2: POST /publish no lo encontraba y el
      // job nacía sin r2_key (draft "el clip no tiene MP4 en R2").
      await env.DB.prepare("UPDATE clips SET video_r2_key = ?, updated_at = ? WHERE id = ? AND tenant_id = ?")
        .bind(key, Date.now(), clipId, tenant)
        .run();
      // La subida ya no devuelve ninguna URL pública: el MP4 se
      // ve/descarga solo por GET /api/clips/:id/video (con sesión)
      // o por URL firmada en el paso de publicación. El bucket es
      // privado y la key lleva un componente aleatorio (ver r2.ts).
      return json({ ok: true, r2_key: key, size, video_url: `/api/clips/${clipId}/video` });
    }
    if (videoMatch && method === "GET") {
      const clipId = Number(videoMatch[1]);
      const clip = await getClip(env.DB, clipId, tenant);
      if (!clip) return err("Clip no encontrado.", 404);
      // Como en POST /publish: primero la key del clip (0016), con
      // fallback a jobs para clips subidos antes de ella.
      const row = clip.video_r2_key
        ? { r2_key: clip.video_r2_key }
        : await env.DB.prepare("SELECT r2_key FROM jobs WHERE clip_id = ? AND tenant_id = ? AND r2_key IS NOT NULL LIMIT 1")
            .bind(clipId, tenant)
            .first<{ r2_key: string }>();
      if (!row) return err("Este clip no tiene MP4 en R2.", 404);
      const obj = await env.CLIPS_BUCKET.get(row.r2_key);
      if (!obj) return err("El MP4 ya no está en R2.", 404);
      return new Response(obj.body, { headers: { "Content-Type": obj.httpMetadata?.contentType ?? "video/mp4" } });
    }

    // --- Logos de marca de agua (2026-11-18, migración 0025) ---
    // GET /api/watermarks: lista los logos DEL tenant (nunca los de
    // otro), con su vista previa y cuál es el activo.
    // POST /api/watermarks?filename=: sube un logo PNG/JPG (≤5 MB).
    // GET /api/watermarks/:id/image: sirve el logo con sesión.
    // DELETE /api/watermarks/:id: borra fila + objeto R2; si era el
    // activo, limpia watermark_logo_id en los Ajustes.
    if (path === "/api/watermarks" && method === "GET") {
      const logos = await listWatermarkLogos(env.DB, tenant);
      const activeId = effectiveSettings(await getSettings(env.DB, tenant)).watermark_logo_id;
      return json({
        ok: true,
        logos: logos.map((l) => ({
          id: l.id,
          filename: l.filename,
          content_type: l.content_type,
          size: l.size,
          created_at: l.created_at,
          is_active: l.id === activeId,
          preview_url: `/api/watermarks/${l.id}/image`,
        })),
      });
    }
    if (path === "/api/watermarks" && method === "POST") {
      const contentType = ((req.headers.get("Content-Type") ?? "").split(";")[0] ?? "").trim().toLowerCase();
      if (contentType !== "image/png" && contentType !== "image/jpeg") {
        return err("El logo debe ser PNG o JPG.", 400);
      }
      // El tamaño declarado se rechaza antes de leer el cuerpo; el
      // real se comprueba después (sin Content-Length, manda este).
      const declared = Number(req.headers.get("Content-Length") ?? "");
      if (Number.isFinite(declared) && declared > WATERMARK_MAX_BYTES) {
        return err("El logo supera 5 MB.", 400);
      }
      const buf = await req.arrayBuffer();
      if (buf.byteLength === 0) return err("El archivo está vacío.", 400);
      if (buf.byteLength > WATERMARK_MAX_BYTES) return err("El logo supera 5 MB.", 400);
      // Bytes mágicos: el Content-Type lo pone quien sube; el
      // contenido manda (PNG 89 50 4E 47, JPEG FF D8 FF).
      const bytes = new Uint8Array(buf);
      const isPng = bytes.length >= 4 && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47;
      const isJpeg = bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff;
      if (!isPng && !isJpeg) return err("El archivo no es un PNG ni un JPG válido.", 400);
      const filename = url.searchParams.get("filename") || (isPng ? "logo.png" : "logo.jpg");
      const key = r2KeyForWatermark(filename, tenant);
      const storedType = isPng ? "image/png" : "image/jpeg";
      const size = await uploadWatermarkImage(env, key, buf, storedType);
      const row = await createWatermarkLogo(env.DB, tenant, key, filename, storedType, size, Date.now());
      return json({
        ok: true,
        logo: {
          id: row.id, filename: row.filename, content_type: row.content_type,
          size: row.size, created_at: row.created_at, is_active: false,
          preview_url: `/api/watermarks/${row.id}/image`,
        },
      });
    }
    const watermarkImageMatch = /^\/api\/watermarks\/(\d+)\/image$/.exec(path);
    if (watermarkImageMatch && method === "GET") {
      const logo = await getWatermarkLogo(env.DB, Number(watermarkImageMatch[1]), tenant);
      if (!logo) return err("Logo no encontrado.", 404);
      const obj = await env.CLIPS_BUCKET.get(logo.r2_key);
      if (!obj) return err("El logo ya no está en R2.", 404);
      return new Response(obj.body, { headers: { "Content-Type": logo.content_type, "Cache-Control": "private, max-age=3600" } });
    }
    const watermarkDeleteMatch = /^\/api\/watermarks\/(\d+)$/.exec(path);
    if (watermarkDeleteMatch && method === "DELETE") {
      const id = Number(watermarkDeleteMatch[1]);
      const logo = await getWatermarkLogo(env.DB, id, tenant);
      if (!logo) return err("Logo no encontrado.", 404);
      await env.CLIPS_BUCKET.delete(logo.r2_key);
      await deleteWatermarkLogo(env.DB, id, tenant);
      const settings = await getSettings(env.DB, tenant);
      if (effectiveSettings(settings).watermark_logo_id === id) {
        await saveSettings(env.DB, tenant, { ...settings, watermark_logo_id: null });
      }
      return json({ ok: true });
    }

    // --- Cuentas (credenciales enmascaradas; nunca el valor) ---
    // POST /api/accounts (Frente 2, M5): crear una cuenta API desde
    // el panel. Antes solo existían las migradas desde el panel
    // anterior. La fila nace sin credencial (status 'pendiente'):
    // la credencial se pega después y se verifica de verdad.
    // Meta unifica Instagram y Facebook (un solo token de Página).
    if (path === "/api/accounts" && method === "POST") {
      const body = await readJson(req);
      const platform = typeof body.platform === "string" ? body.platform.trim().toLowerCase() : "";
      // 2026-11-19: twitch faltaba aquí aunque verify.ts y
      // /api/influencers/live esperan una fila twitch (la credencial
      // de Twitch es el paso 3 del onboarding y sin ella el Worker
      // no puede saber quién está en directo).
      // 2026-11-21 (bug kaez): gemini también faltaba — es cuenta API
      // como Twitch (solo pegado, sin OAuth) y ya existe como fila en
      // api_accounts vía onboarding. Allowlist ÚNICA: el front
      // (API_PROVIDERS en app.js) ofrece exactamente estos mismos 5.
      const API_PLATFORMS = ["youtube", "tiktok", "meta", "twitch", "gemini"];
      if (!API_PLATFORMS.includes(platform)) {
        return err("Plataforma inválida: usa youtube, tiktok, meta, twitch o gemini (Meta cubre Instagram y Facebook).", 400);
      }
      const language = body.language === undefined ? "es" : typeof body.language === "string" ? body.language.trim().toLowerCase() : "";
      if (!["es", "en"].includes(language)) return err("Idioma inválido: usa es o en.", 400);
      const label = body.label === undefined || body.label === ""
        ? { youtube: "YouTube", tiktok: "TikTok", meta: "Meta", twitch: "Twitch", gemini: "Gemini" }[platform] as string
        : typeof body.label === "string"
          ? body.label.trim()
          : null;
      if (label === null) return err("label debe ser texto.", 400);
      if (label.length > 100) return err("label no puede superar 100 caracteres.", 400);
      const existing = await getApiAccountByPlatform(env.DB, tenant, platform, language);
      if (existing) return err(`Ya existe una cuenta ${platform} · ${language} en este panel.`, 409);
      const account = await createApiAccount(env.DB, tenant, { platform, language, label });
      return json({
        ok: true,
        account: {
          id: account.id,
          platform: account.platform,
          language: account.language,
          label: account.label,
          status: account.status,
          note: account.note,
          verified_at: account.verified_at,
          verify_ok: account.verify_ok,
          verify_message: account.verify_message,
          has_credential: false,
          credential_masked: "",
        },
      }, 201);
    }
    if (path === "/api/accounts" && method === "GET") {
      const accounts = await listApiAccounts(env.DB, tenant);
      const rawSettings = await getSettings(env.DB, tenant);
      const eff = effectiveSettings(rawSettings);
      // Recálculo diario de madurez (no-op si ya se hizo hoy).
      await refreshAccountMaturity(env.DB, tenant, eff).catch(() => undefined);
      const fresh = await listApiAccounts(env.DB, tenant);
      const now = Date.now();
      return json({
        ok: true,
        accounts: (fresh.length ? fresh : accounts).map((a) => {
          const m = maturityOut(a, eff);
          const seed = `acc-${a.id}`;
          const restDay = restWeekdayEffective(seed, santiagoWeekKey(now), rawSettings, a.id);
          return {
            id: a.id,
            platform: a.platform,
            language: a.language,
            label: a.label,
            status: a.status,
            note: a.note,
            verified_at: a.verified_at,
            verify_ok: a.verify_ok,
            verify_message: a.verify_message,
            credential_changed_at: a.credential_changed_at ?? null,
            // V2: false si la verificación es anterior al último
            // cambio de credencial (falso positivo: "Conectada" con
            // la credencial anterior). Ver isAccountVerificationCurrent.
            verification_current: isAccountVerificationCurrent(a),
            has_credential: !!(a.credential_enc || a.credential),
            credential_masked: a.credential_enc ? "cifrada en D1" : a.credential ? "texto plano legacy: volver a pegar" : "",
            // Escalado automático de topes (0017, reglas 2026-11-19):
            // estado guardado + etapa de calentamiento + datos.
            maturity: {
              status: m.stored_status,
              stage: m.stage,
              days_since_first_published: m.days_since_first_published,
              published_count: m.published_count,
              strikes: m.strikes,
              first_published_at: m.first_published_at,
              warmup_remaining_days: m.warmup_remaining_days,
              warmup_remaining_pubs: m.warmup_remaining_pubs,
              effective_daily_limit: m.effective_daily_limit,
              effective_daily_plan: m.effective_daily_plan,
              effective_daily_max: m.effective_daily_max,
            },
            // Día libre semanal (regla 7, determinista por
            // cuenta+semana) y pausa por spam/límite (regla 6,
            // 48 h, en settings account_pauses).
            rest_day_weekday: restDay,
            rest_day_name: WEEKDAY_NAMES_ES[restDay] ?? "",
            is_rest_day_today: isRestDayEffective(seed, now, rawSettings, a.id),
            paused_until_ms: accountPausedUntilMs(rawSettings, a.id),
          };
        }),
      });
    }
    // PATCH /api/accounts/:id — strikes (sanciones) y nota/etiqueta.
    // strikes > 0 devuelve la cuenta a NUEVA (1/día) en el próximo
    // recálculo diario; strikes = 0 la deja volver a establecerse
    // cuando cumpla >30 días y >=10 publicaciones. Solo el usuario
    // o su agente pone strikes (cuando la plataforma sanciona): el
    // Worker nunca inventa una sanción.
    const accountPatchMatch = /^\/api\/accounts\/(\d+)$/.exec(path);
    if (accountPatchMatch && method === "PATCH") {
      const id = Number(accountPatchMatch[1]);
      const account = await getApiAccount(env.DB, id, tenant);
      if (!account) return err("Cuenta no encontrada.", 404);
      const body = await readJson(req);
      const sets: string[] = [];
      const binds: unknown[] = [];
      if (body.strikes !== undefined) {
        if (typeof body.strikes !== "number" || !Number.isInteger(body.strikes) || body.strikes < 0 || body.strikes > 99) {
          return err("strikes debe ser un entero entre 0 y 99.", 400);
        }
        sets.push("strikes = ?");
        binds.push(body.strikes);
      }
      if (body.label !== undefined) {
        if (typeof body.label !== "string" || !body.label.trim() || body.label.trim().length > 100) {
          return err("label debe ser texto no vacío de hasta 100 caracteres.", 400);
        }
        sets.push("label = ?");
        binds.push(body.label.trim());
      }
      if (body.note !== undefined) {
        if (typeof body.note !== "string" || body.note.length > 2000) {
          return err("note debe ser texto de hasta 2000 caracteres.", 400);
        }
        sets.push("note = ?");
        binds.push(body.note);
      }
      if (sets.length === 0) return err("Nada que actualizar: usa strikes, label o note.", 400);
      binds.push(id, tenant);
      await env.DB.prepare(`UPDATE api_accounts SET ${sets.join(", ")} WHERE id = ? AND tenant_id = ?`).bind(...binds).run();
      const updated = await getApiAccount(env.DB, id, tenant);
      const eff = effectiveSettings(await getSettings(env.DB, tenant));
      return json({ ok: true, account: updated ? { id: updated.id, platform: updated.platform, label: updated.label, maturity: maturityOut(updated, eff) } : null });
    }
    const credMatch = /^\/api\/accounts\/(\d+)\/credential$/.exec(path);
    if (credMatch && method === "POST") {
      const id = Number(credMatch[1]);
      const account = await getApiAccount(env.DB, id, tenant);
      if (!account) return err("Cuenta no encontrada.", 404);
      const body = await readJson(req);
      const credential = typeof body.credential === "string" ? body.credential : "";
      const extra = typeof body.extra === "string" ? body.extra : "";
      if (!credential) return err("Falta la credencial.", 400);
      // Cifrado con clave derivada por tenant (HKDF): la D1 es
      // compartida pero cada tenant solo descifra lo suyo.
      const credentialEnc = await encryptCredential(credential, env.CREDENTIAL_ENCRYPTION_KEY, tenant);
      const extraEnc = extra ? await encryptCredential(extra, env.CREDENTIAL_ENCRYPTION_KEY, tenant) : "";
      // Se cifra y se borra el texto plano legacy en la misma escritura.
      // V2 (0043): se sella credential_changed_at y se invalida la
      // caché de la sonda (ver upsertAccountCredential).
      await env.DB.prepare("UPDATE api_accounts SET credential = '', credential_enc = ?, extra = '', extra_enc = ?, status = 'pendiente', credential_changed_at = ? WHERE id = ? AND tenant_id = ?")
        .bind(credentialEnc, extraEnc, Date.now(), id, tenant)
        .run();
      await invalidateProbeCache(env.DB, tenant);
      return json({ ok: true, masked: maskCredential(credential) });
    }
    const verifyMatch = /^\/api\/accounts\/(\d+)\/verify$/.exec(path);
    if (verifyMatch && method === "POST") {
      const id = Number(verifyMatch[1]);
      const account = await getApiAccount(env.DB, id, tenant);
      if (!account) return err("Cuenta no encontrada.", 404);
      if (!account.credential_enc) {
        return err("Sin credencial cifrada: pégala primero en Cuentas.", 400);
      }
      // TikTok vía Zernio necesita el extra (via + account_id) para
      // verificar contra Zernio en vez de contra open.tiktokapis.com.
      const outcome = await verifyApiAccount(env, tenant, account);
      return json({ ok: outcome.ok, status: outcome.ok ? "conectada" : "error", message: outcome.message, verifiedAt: new Date().toISOString() });
    }
    // POST /api/accounts/validate-daily — validación diaria FORZADA
    // (spec 2026-11-23, §3.5): ejecuta el mismo camino diario
    // (puerta ignorada, mismos flips y transitorios) y devuelve el
    // resumen. Exige sesión de panel; marca el día (la automática
    // posterior salta) y corre aunque el Worker esté parado (es una
    // acción manual, no va por la puerta del ciclo).
    if (path === "/api/accounts/validate-daily" && method === "POST") {
      // La sesión ya la exige el gate global de handleFetch (401
      // "Inicia sesión primero."): aquí solo corre la validación.
      const result = await validateAccountsDaily(env, tenant, Date.now(), { force: true });
      return json({
        ok: true,
        ran: result.ran,
        reason: result.reason,
        partial: result.partial,
        summary: result.summary,
        checked: result.checked,
        valid: result.valid,
        invalid: result.invalid,
        transient: result.transient,
      });
    }

    // --- Cuentas sociales (tabla accounts, migración 0002 + tenant_id en 0013) ---
    // Solo lectura: la tabla no guarda credenciales, así que no hay
    // verificación real posible. POST .../verify responde 501, nunca
    // un éxito fingido.
    if (path === "/api/social-accounts" && method === "GET") {
      const accounts = await listSocialAccounts(env.DB, tenant);
      const apiAccounts = await listApiAccounts(env.DB, tenant);
      return json({
        ok: true,
        accounts: accounts.map((a) => {
          const v = apiVerificationForSocial(a, apiAccounts);
          return {
            id: a.id,
            platform: a.platform,
            language: a.language,
            label: a.label,
            handle: a.handle,
            status: a.status,
            note: a.note,
            sort: a.sort,
            // Validez de la cuenta API subyacente (join en el
            // servidor, spec 2026-11-23 §3.3): la fila social no
            // guarda credencial, pero el panel muestra si su cuenta
            // API está vigente. null = sin cuenta API en este
            // idioma/plataforma.
            api_account_id: v?.api_account_id ?? null,
            verification_current: v?.verification_current ?? null,
            verify_ok: v?.verify_ok ?? null,
            verify_message: v?.verify_message ?? null,
            verified_at: v?.verified_at ?? null,
          };
        }),
      });
    }
    const socialVerifyMatch = /^\/api\/social-accounts\/(\d+)\/verify$/.exec(path);
    if (socialVerifyMatch && method === "POST") {
      const account = await getSocialAccount(env.DB, Number(socialVerifyMatch[1]), tenant);
      if (!account) return err("Cuenta social no encontrada.", 404);
      return err(
        "verificación no disponible: las cuentas sociales no guardan credencial en el Worker, así que no hay nada que verificar contra la plataforma. La verificación real vive en Cuentas API.",
        501,
      );
    }

    // --- Influencers (tabla influencers) ---
    // Puerta de activación (10 del lanzamiento, orden del usuario
    // 2026-11-18 15:30, AMPLIADA 2026-11-23 con los 4 virales de
    // campaña FAMOSOS: Dasgasd3m, ArthurTV, Dylan Page, BenDaDonnn;
    // Bobby Van Riper queda fuera por la regla 2026-11-23 18:50,
    // solo famosos: 734 seguidores en Twitch): esos 14 pueden estar
    // activos con permiso. Activar a otro se rechaza con 400 y el
    // motivo; nunca se activa en silencio ni se deja activo "por
    // defecto".
    if (path === "/api/influencers" && method === "GET") {
      const rows = await listInfluencers(env.DB, tenant);
      return json({ ok: true, influencers: rows.map(influencerOut) });
    }
    // GET /api/influencers/live: ¿quién está en directo AHORA?
    // Lógica en worker/src/live-check.ts (la usa también el
    // validador de suministro): Twitch Helix + Kick API pública,
    // con el juego del directo (solo GTA VI vale). Solo lectura:
    // no publica nada.
    if (path === "/api/influencers/live" && method === "GET") {
      const entries = await checkInfluencersLive(env.DB, env, tenant);
      const live = entries.map((e) => ({
        id: e.id, handle: e.handle, platform: e.platform,
        live: e.live, game: e.game, title: e.title, viewers: e.viewers, reason: e.reason,
      }));
      return json({ ok: true, live });
    }
    if (path === "/api/influencers" && method === "POST") {
      const body = await readJson(req);
      const parsed = parseInfluencerInput(body);
      if (typeof parsed === "string") return err(parsed, 400);
      if (parsed.active === 1 && !isActivatableInfluencer(parsed.name)) {
        return err(
          `Solo los 10 del lanzamiento + los 4 virales de campaña famosos pueden estar activos con permiso (lanzamiento: Rubius, IlloJuan, AuronPlay, Ibai, TheGrefg, Xokas, DjMaRiiO, Knekro, RDJavi, Westcol; campaña: Dasgasd3m, ArthurTV, Dylan Page, BenDaDonnn; Bobby Van Riper queda fuera por fama: 734 seguidores, regla 2026-11-23 18:50): "${parsed.name}" no se puede activar. Se puede crear desactivado.`,
          400,
        );
      }
      const row = await createInfluencer(env.DB, tenant, parsed);
      return json({ ok: true, influencer: influencerOut(row) }, 201);
    }
    const influencerMatch = /^\/api\/influencers\/(\d+)$/.exec(path);
    if (influencerMatch && method === "PATCH") {
      const id = Number(influencerMatch[1]);
      const row = await getInfluencer(env.DB, id, tenant);
      if (!row) return err("Influencer no encontrado.", 404);
      const body = await readJson(req);
      const parsed = parseInfluencerPatch(body);
      if (typeof parsed === "string") return err(parsed, 400);
      const nextName = parsed.name ?? row.name;
      const nextActive = parsed.active ?? row.active;
      if (nextActive === 1 && !isActivatableInfluencer(nextName)) {
        return err(
          `Solo los 10 del lanzamiento + los 4 virales de campaña famosos pueden estar activos con permiso (lanzamiento: Rubius, IlloJuan, AuronPlay, Ibai, TheGrefg, Xokas, DjMaRiiO, Knekro, RDJavi, Westcol; campaña: Dasgasd3m, ArthurTV, Dylan Page, BenDaDonnn; Bobby Van Riper queda fuera por fama: 734 seguidores, regla 2026-11-23 18:50): "${nextName}" no se puede activar.`,
          400,
        );
      }
      const updated = await updateInfluencer(env.DB, id, tenant, parsed);
      if (!updated) return err("Influencer no encontrado.", 404);
      return json({ ok: true, influencer: influencerOut(updated) });
    }
    if (influencerMatch && method === "DELETE") {
      const id = Number(influencerMatch[1]);
      const row = await getInfluencer(env.DB, id, tenant);
      if (!row) return err("Influencer no encontrado.", 404);
      await deleteInfluencer(env.DB, id, tenant);
      return json({ ok: true });
    }

    // --- Fuentes / VODs (tablas sources y vod_checks; solo lectura) ---
    // La revisión de VODs nuevos la hace GitHub Actions (cerebro,
    // cron 09:00), no el Worker: aquí no hay endpoint de chequeo.
    // GET /api/sources incluye la última revisión registrada por el
    // cerebro (last_vod_check: null si aún no hay ninguna).
    if (path === "/api/sources" && method === "GET") {
      const sources = await listSources(env.DB, tenant);
      const lastVodCheck = await getLatestVodCheck(env.DB, tenant);
      return json({ ok: true, sources, last_vod_check: lastVodCheck });
    }
    const sourceMatch = /^\/api\/sources\/(\d+)$/.exec(path);
    if (sourceMatch && method === "GET") {
      const source = await getSource(env.DB, Number(sourceMatch[1]), tenant);
      if (!source) return err("Fuente no encontrada.", 404);
      return json({ ok: true, source });
    }

    // --- Usuarios del panel (tabla panel_users) ---
    // M1 (Frente 2): cada usuario ve SOLO su propio tenant. Antes
    // se devolvían AMBOS usuarios (seba y chacal) a cualquiera con
    // sesión; ahora la lista filtra por el tenant de la sesión.
    // Solo username, tenant, status y created_at; NUNCA password_hash
    // (la consulta en db.ts ni siquiera lo selecciona).
    if (path === "/api/panel-users" && method === "GET") {
      return json({ ok: true, users: await listPanelUsers(env.DB, tenant) });
    }

    // --- Jobs del usuario (Frente 2, M13): GET /api/jobs ---
    // Lectura legible por sesión (el puente usa /api/jobs/next con
    // bridge token; esto es la vista del usuario en el panel).
    // ?state= filtra por estado; ?limit= tope (1–100, por defecto 50).
    if (path === "/api/jobs" && method === "GET") {
      const states: JobStateFilter[] = ["scheduled", "processing", "posted", "failed", "draft", "awaiting_captcha"];
      const stateRaw = url.searchParams.get("state") ?? "";
      const state = (states as string[]).includes(stateRaw) ? (stateRaw as JobStateFilter) : undefined;
      if (stateRaw && !state) return err("state inválido: usa scheduled, processing, posted, failed, draft o awaiting_captcha.", 400);
      const limitRaw = Number(url.searchParams.get("limit") ?? "50");
      const limit = Number.isFinite(limitRaw) ? Math.min(100, Math.max(1, Math.trunc(limitRaw))) : 50;
      const jobs = await listJobs(env.DB, tenant, limit, state);
      return json({ ok: true, jobs });
    }

    // --- Plan diario de publicación (reglas 2026-11-19, §9) ---
    // GET /api/daily-plan?fecha=YYYY-MM-DD (default: hoy,
    // America/Santiago). Devuelve {fecha, publicaciones, en_cola,
    // alertas} verificando topes, calentamiento y espaciamiento
    // antes de devolverlo. Solo calcula el plan: no encola ni
    // publica nada. La fecha fuera de formato es 400.
    if (path === "/api/daily-plan" && method === "GET") {
      const fechaRaw = url.searchParams.get("fecha");
      if (fechaRaw !== null && !/^\d{4}-\d{2}-\d{2}$/.test(fechaRaw)) {
        return err("fecha inválida: usa YYYY-MM-DD (America/Santiago).", 400);
      }
      const plan = await buildDailyPlanForTenant(env.DB, tenant, fechaRaw);
      return json({ ok: true, ...plan });
    }

    // --- Engagement v2 (2026-11-23, v5.4.0): vía NATIVA para
    // YouTube/Instagram/Facebook (tokens OAuth del panel) + Zernio
    // SOLO para TikTok. Sin follow en ninguna vía, sin TikTok hacia
    // afuera (Like post devuelve 400) y sin descubrir posts de
    // terceros: los objetivos de afuera se aportan con su URL y el
    // streamer tiene que estar en la lista de influencers activos
    // con permiso. El kill-switch es engagement_enabled en Ajustes
    // (default OFF). El status dispara la sonda de scopes si no hay
    // una cacheada de la última hora: la primera apertura de la
    // sección Engagement comprueba los permisos reales.
    if (path === "/api/engagement/status" && method === "GET") {
      await probeNativeScopes(env, tenant).catch(() => undefined);
      return json({ ok: true, ...(await getEngagementStatus(env, tenant)) });
    }
    // Sonda de scopes en vivo (forzada): por plataforma+idioma,
    // con la lista concedida y lo que falta. Sin exponer tokens.
    if (path === "/api/engagement/scopes" && method === "GET") {
      const probe = await probeNativeScopes(env, tenant, { force: true });
      return json({ ok: true, ...probe });
    }
    if (path === "/api/engagement/targets" && method === "GET") {
      return json({ ok: true, targets: await listTargets(env.DB, tenant) });
    }
    if (path === "/api/engagement/targets" && method === "POST") {
      const body = await readJson(req);
      const platform = typeof body.platform === "string" ? body.platform : "";
      if (!(ENGAGEMENT_PLATFORMS as string[]).includes(platform)) return err("platform debe ser youtube, tiktok, instagram o facebook.", 400);
      const language = body.language === undefined || body.language === "es" ? "es" : body.language === "en" ? "en" : "";
      if (!language) return err("language debe ser es o en.", 400);
      const streamer = typeof body.streamer === "string" ? body.streamer.trim().toLowerCase() : "";
      if (!streamer) return err("streamer es obligatorio.", 400);
      const allowed = (await listInfluencers(env.DB, tenant)).find(
        (r) => r.name.trim().toLowerCase() === streamer && r.active === 1 && r.permission === 1,
      );
      if (!allowed) return err(`streamer "${streamer}" no está en la lista de influencers activos con permiso.`, 400);
      const postUrl = typeof body.post_url === "string" ? body.post_url.trim() : "";
      const postId = postUrl ? extractNativePostId(platform as ZernioPlatform, postUrl) : null;
      if (!postId) return err("post_url no es una URL válida de un post en esa plataforma.", 400);
      const kind = body.kind === "like" || body.kind === "comment" ? body.kind : "";
      if (!kind) return err("kind debe ser like o comment (follow no lo expone Zernio).", 400);
      if (platform === "tiktok" && kind === "like") return err("TikTok hacia afuera no lo cubre Zernio: Like post devuelve 400 en TikTok.", 400);
      let commentText: string | null = null;
      if (kind === "comment") {
        commentText = typeof body.comment_text === "string" ? body.comment_text.trim() : "";
        if (commentText.length < 10 || commentText.length > 280) return err("comment_text debe tener entre 10 y 280 caracteres y ser específico del clip.", 400);
        if (/^(🔥+|👏+|❤️+|♥+|genial!?|buenísimo!?|qué bueno!?)$/i.test(commentText)) return err("comment_text no puede ser genérico (solo emojis o una palabra).", 400);
      }
      const now = Date.now();
      const res = await env.DB.prepare(
        "INSERT INTO engagement_targets (tenant_id, platform, language, streamer, post_url, post_id, kind, comment_text, status, reason, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?)",
      ).bind(tenant, platform, language, streamer, postUrl, postId, kind, commentText, now, now).run();
      return json({ ok: true, id: Number(res.meta.last_row_id), post_id: postId });
    }
    if (path === "/api/engagement/run" && method === "POST") {
      // INICIAR manual: salta solo la franja y el throttle de 1 h;
      // NO salta topes, kill-switch ni la sonda de scopes.
      const summary = await runEngagement(env, tenant, { force: true });
      return json({ ok: true, ...summary });
    }

    // --- Envíos a marketplaces de clipping (tabla campaign_submissions, 0041) ---
    // Registro + cola en nube de los envíos a Content Rewards.
    // Solo lectura/escritura VÍA WORKER con sesión del panel: la
    // extensión (y el panel) nunca escriben directo en D1.
    // GET /api/campaign-submissions?clip_id=<n>[&platform=&campaign_id=&status=]
    //   lista las filas de ese clip (la puerta anti-duplicado de la
    //   extensión). ?pending=1 devuelve la cola en nube: pendientes
    //   ya publicadas, las más urgentes primero (antes rellena los
    //   publicados por WAL y caduca lo vencido, ver
    //   listPendingCampaignSubmissions).
    // POST: registra la asignación en `pending` (409 si ya existe
    //   esa tenant+clip+plataforma+campaña). published_at_ms es el
    //   instante REAL de publicación: si aún no se publicó, va
    //   vacío (NULL) y el envío no puede salir; el Worker lo
    //   rellena en markPosted. Nunca se acepta un published_at_ms
    //   futuro ni inventado por el cliente.
    // PATCH /:id: marca submitted (con submission_id del
    //   marketplace), failed o expired. `submitted` es terminal.
    const SUBMISSION_PLATFORMS = ["youtube", "tiktok", "instagram", "facebook", "x"] as const;
    if (path === "/api/campaign-submissions" && method === "GET") {
      const clipRaw = url.searchParams.get("clip_id");
      const clipId = clipRaw === null ? undefined : Number(clipRaw);
      if (clipRaw !== null && (!Number.isInteger(clipId) || (clipId as number) <= 0)) {
        return err("clip_id debe ser el id numérico del clip.", 400);
      }
      const platform = url.searchParams.get("platform") ?? undefined;
      if (platform !== undefined && !(SUBMISSION_PLATFORMS as readonly string[]).includes(platform)) {
        return err("platform debe ser youtube, tiktok, instagram, facebook o x.", 400);
      }
      const campaignId = url.searchParams.get("campaign_id") ?? undefined;
      if (campaignId !== undefined && !campaignId.trim()) return err("campaign_id no puede ser vacío.", 400);
      const status = url.searchParams.get("status") ?? undefined;
      if (status !== undefined && !(CAMPAIGN_SUBMISSION_STATUSES as readonly string[]).includes(status)) {
        return err(`status debe ser ${CAMPAIGN_SUBMISSION_STATUSES.join(", ")}.`, 400);
      }
      if (url.searchParams.get("pending") === "1") {
        const rows = await listPendingCampaignSubmissions(env.DB, tenant);
        return json({ ok: true, pending: true, submissions: rows.map(campaignSubmissionOut) });
      }
      const rows = await listCampaignSubmissions(env.DB, tenant, { clipId, platform, campaignId, status });
      return json({ ok: true, submissions: rows.map(campaignSubmissionOut) });
    }
    if (path === "/api/campaign-submissions" && method === "POST") {
      const body = await readJson(req);
      const clipId = body.clip_id;
      if (typeof clipId !== "number" || !Number.isInteger(clipId) || clipId <= 0) {
        return err("clip_id debe ser el id numérico del clip.", 400);
      }
      const clip = await getClip(env.DB, clipId, tenant);
      if (!clip) return err("Clip no encontrado.", 404);
      const platform = typeof body.platform === "string" ? body.platform.trim().toLowerCase() : "";
      if (!(SUBMISSION_PLATFORMS as readonly string[]).includes(platform)) {
        return err("platform debe ser youtube, tiktok, instagram, facebook o x.", 400);
      }
      const campaignId = typeof body.campaign_id === "string" ? body.campaign_id.trim() : "";
      if (!campaignId) return err("campaign_id es obligatorio.", 400);
      const publishedAtMs = body.published_at_ms ?? null;
      if (publishedAtMs !== null && (typeof publishedAtMs !== "number" || !Number.isFinite(publishedAtMs) || publishedAtMs <= 0 || publishedAtMs > Date.now() + 60000)) {
        return err("published_at_ms debe ser el instante real de publicación (epoch-ms, no futuro). Sin publicar, déjalo vacío: lo rellena el Worker al publicar.", 400);
      }
      const postUrl = typeof body.post_url === "string" && body.post_url.trim() ? body.post_url.trim() : null;
      if (postUrl !== null && !postUrl.startsWith("https://")) return err("post_url debe ser https.", 400);
      if ((postUrl === null) !== (publishedAtMs === null)) {
        return err("post_url y published_at_ms van juntos: ambos o ninguno (el link solo existe si se publicó).", 400);
      }
      const numOrNull = (v: unknown): number | null =>
        typeof v === "number" && Number.isFinite(v) ? v : null;
      const { row, duplicate } = await createCampaignSubmission(env.DB, tenant, {
        clip_id: clipId,
        platform,
        marketplace: typeof body.marketplace === "string" && body.marketplace.trim() ? body.marketplace.trim() : "content_rewards",
        campaign_id: campaignId,
        campaign_name: typeof body.campaign_name === "string" ? body.campaign_name : null,
        streamer: typeof body.streamer === "string" ? body.streamer : (clip.streamer ?? null),
        gross_rate_per_1k: numOrNull(body.gross_rate_per_1k),
        net_rate_per_1k: numOrNull(body.net_rate_per_1k),
        min_views: numOrNull(body.min_views),
        cap_per_clip: numOrNull(body.cap_per_clip),
        post_url: postUrl,
        published_at_ms: publishedAtMs,
      });
      if (duplicate) {
        return json({ ok: false, error: "Ese post ya tiene un envío registrado para esa campaña (tenant+clip+plataforma+campaña). No se duplica.", submission: campaignSubmissionOut(row) }, 409);
      }
      return json({ ok: true, submission: campaignSubmissionOut(row) }, 201);
    }
    const submissionMatch = /^\/api\/campaign-submissions\/(\d+)$/.exec(path);
    if (submissionMatch && method === "PATCH") {
      const id = Number(submissionMatch[1]);
      const body = await readJson(req);
      const patch: { status?: string; submission_id?: string | null; views?: number | null; amount_usd?: number | null; error?: string | null; post_url?: string | null; published_at_ms?: number | null } = {};
      if (body.status !== undefined) {
        if (typeof body.status !== "string" || !(CAMPAIGN_SUBMISSION_STATUSES as readonly string[]).includes(body.status)) {
          return err(`status debe ser ${CAMPAIGN_SUBMISSION_STATUSES.join(", ")}.`, 400);
        }
        patch.status = body.status;
      }
      if (body.submission_id !== undefined) {
        if (body.submission_id !== null && typeof body.submission_id !== "string") return err("submission_id debe ser texto o null.", 400);
        patch.submission_id = body.submission_id;
      }
      if (body.views !== undefined) {
        if (body.views !== null && (typeof body.views !== "number" || !Number.isFinite(body.views))) return err("views debe ser un número o null.", 400);
        patch.views = body.views;
      }
      if (body.amount_usd !== undefined) {
        if (body.amount_usd !== null && (typeof body.amount_usd !== "number" || !Number.isFinite(body.amount_usd))) return err("amount_usd debe ser un número o null.", 400);
        patch.amount_usd = body.amount_usd;
      }
      if (body.error !== undefined) {
        if (body.error !== null && typeof body.error !== "string") return err("error debe ser texto o null.", 400);
        patch.error = body.error;
      }
      if (body.post_url !== undefined) {
        if (body.post_url !== null && (typeof body.post_url !== "string" || !body.post_url.startsWith("https://"))) return err("post_url debe ser https o null.", 400);
        patch.post_url = body.post_url;
      }
      if (body.published_at_ms !== undefined) {
        if (body.published_at_ms !== null && (typeof body.published_at_ms !== "number" || !Number.isFinite(body.published_at_ms) || body.published_at_ms <= 0 || body.published_at_ms > Date.now() + 60000)) {
          return err("published_at_ms debe ser el instante real de publicación (epoch-ms, no futuro).", 400);
        }
        patch.published_at_ms = body.published_at_ms;
      }
      let updated;
      try {
        updated = await updateCampaignSubmission(env.DB, tenant, id, patch);
      } catch (e) {
        return err(e instanceof Error ? e.message : String(e), 400);
      }
      if (!updated) {
        const existing = await getCampaignSubmission(env.DB, id, tenant);
        if (!existing) return err("Envío no encontrado.", 404);
        return err("Envío ya registrado como enviado (submitted): no se puede cambiar.", 409);
      }
      return json({ ok: true, submission: campaignSubmissionOut(updated) });
    }

    // --- Evolución neuroevolutiva (tablas evo_*, migración 0042) ---
    // DRY-RUN: genera la población OFFLINE y selecciona la ganadora.
    // NO publica nada, NO crea jobs ni clips, NO envía a ninguna
    // campaña: la ganadora queda lista para que el cerebro la
    // renderice con sus params (start/end/título/miniatura) por la
    // vía de autopublicación ya existente. Solo 1 ganadora por
    // momento/plataforma; las perdedoras no se publican ni se
    // reenvían a otra campaña. Sin datos reales, NUNCA se inventa
    // un fitness: la generación 1 publica la línea base (control)
    // y las mutantes quedan como candidatas sin probar.
    // GET /api/evolution/status — resumen solo lectura (panel).
    // POST /api/evolution/dry-run — genera + selecciona + persiste.
    // POST /api/evolution/record-real-fitness — anota vistas
    // aprobadas/vistas/retención REALES de una variante (24-48 h);
    // el fitness real manda en la siguiente generación.
    const EVO_API_TO_SEO: Record<string, string> = {
      youtube: "youtube_shorts",
      tiktok: "tiktok",
      instagram: "instagram_reels",
      facebook: "facebook_reels",
    };
    if (path === "/api/evolution/status" && method === "GET") {
      const generations = await evoStatusSummary(env.DB, tenant);
      return json({ ok: true, generations });
    }
    if (path === "/api/evolution/dry-run" && method === "POST") {
      const body = await readJson(req);
      const clipId = body.clip_id;
      if (typeof clipId !== "number" || !Number.isInteger(clipId) || clipId <= 0) {
        return err("clip_id debe ser el id numérico del clip.", 400);
      }
      const clip = await getClip(env.DB, clipId, tenant);
      if (!clip) return err("Clip no encontrado.", 404);
      // Streamer: del influencer del clip; si no tiene, del body.
      let handle: string | null = null;
      if (clip.influencer_id !== null && clip.influencer_id !== undefined) {
        const inf = await getInfluencer(env.DB, clip.influencer_id, tenant);
        if (inf) handle = normalizeHandle(inf.name);
      }
      if (!handle && typeof body.streamer === "string" && body.streamer.trim()) handle = normalizeHandle(body.streamer);
      if (!handle) return err("El clip no tiene streamer asignado (ni influencer ni streamer en el body).", 400);
      const campaign = campaignForStreamer(handle);
      if (!campaign) return err(`Sin campaña: ${handle} no tiene campaña en Content Rewards.`, 400);
      if (campaign.followers < 50000) {
        return err(`Sin campaña: ${handle} no es famoso (${campaign.followers} seguidores < 50.000). Solo influencers famosos.`, 400);
      }
      if (campaign.netRatePer1k < 1.0) {
        return err(`Sin campaña: ${handle} paga $${campaign.netRatePer1k.toFixed(2)}/1k neto, bajo el piso de $1/1k neto.`, 400);
      }
      if (!isCampaignStreamer(handle)) return err(`Sin campaña: ${handle} no pasa el filtro fama + tarifa neta.`, 400);
      const platform = typeof body.platform === "string" && body.platform.trim()
        ? body.platform.trim().toLowerCase()
        : campaign.platforms[0]!;
      if (!campaign.platforms.includes(platform)) {
        return err(`La campaña de ${handle} no cubre ${platform} (cubre: ${campaign.platforms.join(", ")}).`, 400);
      }
      // Puertas del cerebro (45/80) + calidad B4, intactas.
      const settings = effectiveSettings(await getSettings(env.DB, tenant).catch(() => ({} as Record<string, unknown>)));
      const viral = parseViralJson(clip.viral);
      const virality = typeof viral.viral_score === "number" ? viral.viral_score : null;
      const seoKey = EVO_API_TO_SEO[platform];
      const seo = seoKey && viral.seo_scores && typeof viral.seo_scores[seoKey as keyof typeof viral.seo_scores] === "number"
        ? (viral.seo_scores[seoKey as keyof typeof viral.seo_scores] as number)
        : null;
      if (virality === null) return err("Sin puntaje del cerebro: el clip no tiene viral_score (no se inventa).", 400);
      if (seo === null) return err(`Sin puntaje SEO del cerebro para ${platform} (no se inventa).`, 400);
      if (!passesBrainGates(virality, seo, settings.min_virality, settings.min_seo)) {
        return err(`Puertas 45/80: viralidad ${virality}, SEO ${seo} en ${platform} (mínimos ${settings.min_virality}/${settings.min_seo}).`, 400);
      }
      const quality = qualityGateForClip(clip);
      if (quality.reason) return err(quality.reason, 400);
      // RIESGO BAN (ver EVOLUTION-0042.md §8): la evolución corre
      // ANTES de publicar. Si el clip base YA se publicó en esa
      // plataforma, generar variantes sería republicar el mismo
      // momento: TikTok lo marca "unoriginal/reused" (fuera del
      // For You), Instagram deja de recomendar la cuenta que
      // repostea, y YouTube lo cuenta como contenido repetitivo.
      // Se bloquea con motivo exacto, no se genera nada.
      // V5 — FUENTE DE VERDAD DEL 409 (SPEC validators, Goldfish 9):
      // D1 clips.publish_states[platform].state === "publicado".
      // NO es la plataforma ni los jobs. publish_states es un
      // espejo por clip SIN idioma (db.ts:3480): la clave es solo
      // la plataforma, así que el 409 NO distingue ES/EN dentro
      // de una misma fila de clip (ES y EN son filas distintas,
      // cada una con su propio publish_states; ver test ES/EN en
      // evolution.test.ts). FALSO POSITIVO CONOCIDO (documentado,
      // no arreglado aquí): un clip "publicado en D1 pero borrado
      // en la plataforma" sigue dando 409 — la ruta no consulta
      // la plataforma ni jobs.deleted_at (0031). Cambiar la
      // fuente de verdad sería cambio de lógica, fuera de esta
      // spec.
      const panelKey = EVO_API_TO_SEO[platform];
      if (panelKey) {
        const entry = parsePublishStates(clip.publish_states)[panelKey];
        if (entry && entry.state === "publicado") {
          return err(`El clip base ya se publicó en ${platform}: la evolución corre ANTES de publicar, no republica el mismo momento.`, 409);
        }
      }
      // Un post = UNA campaña: si ya tiene envío a otra campaña (o
      // a esta), no se genera nada.
      const existingSubs = await listCampaignSubmissions(env.DB, tenant, { clipId, platform });
      const other = existingSubs.find((s) => s.campaign_id !== campaign.campaignId);
      if (other) return err(`Este post ya tiene un envío a OTRA campaña (${other.campaign_name ?? other.campaign_id}): un post = una campaña.`, 409);
      if (existingSubs.length > 0) return err("Este post ya tiene un envío registrado a esta campaña (no se duplica).", 409);
      // Generación: la pedida, o la siguiente libre para el clip.
      let generation: number;
      if (body.generation !== undefined) {
        if (typeof body.generation !== "number" || !Number.isInteger(body.generation) || body.generation < 1) {
          return err("generation debe ser un entero mayor o igual a 1.", 400);
        }
        generation = body.generation;
      } else {
        generation = 1;
        while (await getEvoGeneration(env.DB, tenant, clipId, generation)) generation++;
      }
      const existingGen = await getEvoGeneration(env.DB, tenant, clipId, generation);
      if (existingGen) {
        const rows = await listEvoVariants(env.DB, tenant, existingGen.id);
        return json({ ok: true, duplicate: true, generation: evoGenerationOut(existingGen), variants: rows.map(evoVariantOut), published_nothing: true });
      }
      // Padre (generación anterior): sus ganadoras son la base de
      // los hijos. Sin ganadora con datos, no hay siguiente gen.
      let parentParams: EvoVariantParams[] = [];
      let parentGenerationId: number | null = null;
      if (body.parent_generation_id !== undefined) {
        if (typeof body.parent_generation_id !== "number" || !Number.isInteger(body.parent_generation_id) || body.parent_generation_id <= 0) {
          return err("parent_generation_id debe ser el id numérico de la generación padre.", 400);
        }
        const parent = await getEvoGenerationById(env.DB, body.parent_generation_id, tenant);
        if (!parent) return err("Generación padre no encontrada.", 404);
        if (parent.base_clip_id !== clipId) return err("La generación padre es de otro clip.", 400);
        const parentRows = await listEvoVariants(env.DB, tenant, parent.id);
        const parentWinners = parentRows.filter((v) => v.status === "winner" || v.status === "published");
        if (parentWinners.length === 0) return err("La generación padre aún no tiene ganadora (sin datos reales ni puntaje del cerebro).", 400);
        parentParams = parentWinners.map((v) => {
          try {
            return JSON.parse(v.params_json) as EvoVariantParams;
          } catch {
            return null;
          }
        }).filter((p): p is EvoVariantParams => p !== null);
        if (parentParams.length === 0) return err("La ganadora padre no tiene params guardados.", 400);
        parentGenerationId = parent.id;
      }
      // Puntajes del cerebro POR VARIANTE (solo el cerebro puede
      // aportarlos, tras validar el render de esa variante). Sin
      // ellos, solo el control tiene puntaje (el del clip base) y
      // las mutantes quedan sin probar.
      const brainScores = new Map<number, { virality: number; seo: number }>();
      if (body.brain_scores !== undefined) {
        if (!Array.isArray(body.brain_scores)) return err("brain_scores debe ser un array [{index, virality, seo}].", 400);
        for (const s of body.brain_scores as unknown[]) {
          if (typeof s !== "object" || s === null) return err("brain_scores: cada entrada debe ser {index, virality, seo}.", 400);
          const e = s as Record<string, unknown>;
          if (typeof e.index !== "number" || !Number.isInteger(e.index) || e.index < 0 || e.index >= EVO_POPULATION_SIZE) {
            return err(`brain_scores: index debe ser entero entre 0 y ${EVO_POPULATION_SIZE - 1}.`, 400);
          }
          if (typeof e.virality !== "number" || e.virality < 0 || e.virality > 100 || typeof e.seo !== "number" || e.seo < 0 || e.seo > 100) {
            return err("brain_scores: virality y seo deben ser números entre 0 y 100.", 400);
          }
          brainScores.set(e.index, { virality: e.virality, seo: e.seo });
        }
      }
      const base = {
        start_sec: clip.start_sec,
        end_sec: clip.end_sec,
        title: clip.title,
        seo_title: clip.seo_title,
        thumb_text: clip.thumb_text,
        subtitle_style: clip.subtitle_style,
      };
      const drafts = generatePopulation(base, generation, clipId * 31 + generation, parentParams);
      const { row: gen } = await createEvoGeneration(env.DB, tenant, {
        streamer: handle,
        campaign_id: campaign.campaignId,
        base_clip_id: clipId,
        generation,
        parent_generation_id: parentGenerationId,
        platform,
      });
      // Puntajes: el control hereda los del clip base; las mutantes,
      // solo los aportados en brain_scores.
      const scored: EvoScoredVariant[] = drafts.map((d) => {
        const bs = d.is_control ? { virality, seo } : brainScores.get(d.index) ?? null;
        return {
          index: d.index,
          brain_virality: bs ? bs.virality : null,
          brain_seo: bs ? bs.seo : null,
          real_fitness: null,
          has_real_data: false,
        };
      });
      const sel = selectWinners(scored, EVO_KEEP_WINNERS, settings.min_virality, settings.min_seo);
      const winnerIdx = new Set(sel.winners.map((w) => w.index));
      const loserIdx = new Set(sel.losers.map((w) => w.index));
      const blockedIdx = new Map(sel.blocked.map((w) => [w.index, `puertas 45/80: viralidad ${w.brain_virality}, SEO ${w.brain_seo}`] as const));
      const saved: Array<Record<string, unknown>> = [];
      for (const d of drafts) {
        const bs = scored[d.index]!;
        const status = winnerIdx.has(d.index) ? "winner" : loserIdx.has(d.index) ? "loser" : blockedIdx.has(d.index) ? "blocked" : "candidate";
        const row = await createEvoVariant(env.DB, tenant, {
          generation_id: gen.id,
          variant_index: d.index,
          parent_variant_id: null,
          params: { ...d.params } as unknown as Record<string, unknown>,
          mutations: d.mutations as unknown as unknown[],
          is_control: d.is_control,
          brain_virality: bs.brain_virality,
          brain_seo: bs.brain_seo,
          brain_fitness: bs.brain_virality,
        });
        if (bs.brain_virality !== null) {
          await recordEvoFitness(env.DB, tenant, row.id, {
            source: "brain",
            virality: bs.brain_virality,
            seo: bs.brain_seo,
            fitness: bs.brain_virality,
          });
        }
        const updated = await updateEvoVariant(env.DB, tenant, row.id, {
          status,
          blocked_reason: blockedIdx.get(d.index)?.[1] ?? null,
        });
        saved.push(evoVariantOut(updated ?? row));
      }
      await updateEvoGeneration(env.DB, tenant, gen.id, { status: "winner_ready" });
      const winnerRow = saved.find((v) => v.status === "winner") ?? null;
      const winnerParams = winnerRow ? (winnerRow.params as EvoVariantParams) : null;
      // El control ES el clip base (no un corte nuevo): publicarlo
      // es publicar el clip base, así que no exige corte distinto.
      // Una mutante ganadora sí: si su corte es igual al base, no
      // es un clip distinto y no se publica.
      const winnerIsControl = winnerRow ? winnerRow.is_control === 1 : false;
      const publishReason = winnerRow && winnerParams
        ? publishBlockReason({
            status: "winner",
            isDistinctFromBase: winnerIsControl || isDistinctCut(
              { start_sec: clip.start_sec, end_sec: clip.end_sec, hook_offset_s: 0, title: clip.seo_title || clip.title, thumb_text: clip.thumb_text, subtitle_style: clip.subtitle_style },
              winnerParams,
            ),
            campaignEligible: true,
            campaignEligibilityReason: null,
            qualityGateReason: null,
            alreadySubmittedToOtherCampaign: false,
            alreadySubmittedToThisCampaign: false,
          })
        : "sin ganadora";
      return json({
        ok: true,
        dry_run: true,
        published_nothing: true,
        generation: evoGenerationOut({ ...(await getEvoGenerationById(env.DB, gen.id, tenant))!, status: "winner_ready" }),
        campaign: { id: campaign.campaignId, name: campaign.campaignName, streamer: handle, net_rate_per_1k: campaign.netRatePer1k, platform },
        winner: winnerRow,
        publishable: publishReason === null,
        publish_block_reason: publishReason,
        variants: saved,
        note: "Nada publicado ni enviado. La ganadora queda lista para que el cerebro la renderice con sus params; las mutantes sin datos reales quedan como candidatas, no como perdedoras.",
      }, 201);
    }
    if (path === "/api/evolution/record-real-fitness" && method === "POST") {
      const body = await readJson(req);
      const variantId = body.variant_id;
      if (typeof variantId !== "number" || !Number.isInteger(variantId) || variantId <= 0) {
        return err("variant_id debe ser el id numérico de la variante.", 400);
      }
      const approved = body.approved_views ?? null;
      const views = body.views ?? null;
      const retention = body.retention_pct ?? null;
      if (approved !== null && (typeof approved !== "number" || !Number.isInteger(approved) || approved < 0)) {
        return err("approved_views debe ser un entero mayor o igual a 0.", 400);
      }
      if (views !== null && (typeof views !== "number" || !Number.isInteger(views) || views < 0)) {
        return err("views debe ser un entero mayor o igual a 0.", 400);
      }
      if (retention !== null && (typeof retention !== "number" || retention < 0 || retention > 100)) {
        return err("retention_pct debe ser un número entre 0 y 100.", 400);
      }
      if (approved === null && views === null) {
        return err("Falta el dato real: approved_views o views (nunca se inventa).", 400);
      }
      const variant = await getEvoVariant(env.DB, variantId, tenant);
      if (!variant) return err("Variante no encontrada.", 404);
      const fitness = realFitness(approved, views);
      const row = await recordEvoFitness(env.DB, tenant, variantId, {
        source: "real",
        approved_views: approved,
        views,
        retention,
        fitness,
      });
      if (!row) return err("No se pudo registrar el fitness.", 400);
      const updated = await getEvoVariant(env.DB, variantId, tenant);
      return json({ ok: true, variant: updated ? evoVariantOut(updated) : null, real_fitness: fitness, has_real_data: true });
    }

    // --- Ajustes (todo lo que el usuario toca: cuentas, subtítulos, límites) ---
    if (path === "/api/settings" && method === "GET") {
      // Devuelve los ajustes EFECTIVOS (guardados + defaults): lo que
      // ve el formulario es lo que aplica el Worker.
      return json({ ok: true, settings: effectiveSettings(await getSettings(env.DB, tenant)) });
    }
    if (path === "/api/settings" && method === "PUT") {
      const body = await readJson(req);
      const current = await getSettings(env.DB, tenant);
      const merged = { ...current, ...body };
      // Validación con 400 (min<max, 0-100, topes>=0): lo inválido
      // se rechaza, no se corrige en silencio. Las claves
      // desconocidas se conservan.
      const invalid = validateSettingsInput(merged);
      if (invalid) return err(invalid, 400);
      await saveSettings(env.DB, tenant, effectiveSettings(merged));
      return json({ ok: true, settings: effectiveSettings(await getSettings(env.DB, tenant)) });
    }

    return err("No encontrado.", 404);
}

/** Limpia el caché de /api/jobs (solo tests; el caché §2.3 se retiró
 *  en el cutover 21:00 tras crear el índice
 *  jobs(tenant_id, state, posted_day): se conserva el export para
 *  no romper el import del test). */
export function clearJobsCache(): void {
  // Sin caché que limpiar.
}

export default {
  async fetch(req: Request, env: Env, _ctx: ExecutionContext): Promise<Response> {
    // CORS: el front vive en Pages (avc-em9.pages.dev) y llama a este
    // Worker cross-origin con credentials:include. Sin estos headers
    // el navegador bloquea todas las llamadas. Solo se permite el
    // origen del front, nunca "*".
    const FRONT_ORIGIN = "https://avc-em9.pages.dev";
    const corsHeaders = (): Record<string, string> =>
      req.headers.get("Origin") === FRONT_ORIGIN
        ? {
            "Access-Control-Allow-Origin": FRONT_ORIGIN,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
            Vary: "Origin",
          }
        : {};
    if (req.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }
    // CSRF (hallazgo M3, 2026-11-18): la cookie de sesión es
    // SameSite=None porque el front vive cross-origin en Pages.
    // Una petición no-GET autenticada SOLO por la cookie (sin
    // Bearer ni X-Bridge-Token) debe venir del front: Origin
    // (o Referer si no hay Origin) tiene que ser
    // https://avc-em9.pages.dev, o se rechaza con 403 ANTES de
    // que corra ningún handler (ni siquiera el logout, que
    // revoca el token). Los clientes Bearer (front en Brave iOS,
    // PanelBridge) y el puente (X-Bridge-Token) no pasan por
    // aquí: una petición cross-site simple no puede poner esas
    // cabeceras. GET/HEAD son seguros y quedan fuera.
    if (req.method !== "GET" && req.method !== "HEAD") {
      const cookie = req.headers.get("Cookie") ?? "";
      const hasCookie = /(?:^|;\s*)panel-session=/.test(cookie);
      const hasBearer = /^Bearer\s+/i.test((req.headers.get("Authorization") ?? "").trim());
      const hasBridge = !!req.headers.get("X-Bridge-Token");
      if (hasCookie && !hasBearer && !hasBridge) {
        const originOf = (v: string | null): string | null => {
          if (!v) return null;
          try {
            return new URL(v).origin;
          } catch {
            return null;
          }
        };
        const got = originOf(req.headers.get("Origin")) ?? originOf(req.headers.get("Referer"));
        if (got !== FRONT_ORIGIN) {
          const res = json({ ok: false, error: "Origen no permitido." }, 403);
          const headers = new Headers(res.headers);
          for (const [k, v] of Object.entries(corsHeaders())) headers.set(k, v);
          return new Response(res.body, { status: res.status, headers });
        }
      }
    }
    // Fail loudly con CORS (2026-11-21, bug kaez): sin este try/catch,
    // cualquier excepción del handler (p. ej. D1 7500 por cuota
    // agotada al guardar una credencial) salía como 500 pelado SIN
    // cabeceras CORS y el navegador lo mostraba como
    // «Failed to fetch», sin el motivo real. Ahora el error llega al
    // panel como JSON con CORS, con el motivo legible.
    let res: Response;
    try {
      res = await handleFetch(req, env, _ctx);
    } catch (e) {
      const raw = e instanceof Error ? e.message : "Error interno del Worker.";
      const quota = /7500|row write limit/i.test(raw);
      res = json(
        {
          ok: false,
          error: quota
            ? "Cuota diaria de escrituras D1 agotada (error 7500): nada se puede guardar hasta el reseteo de hoy a las 21:00 (hora de Chile). El panel no está roto: vuelve a intentarlo después de las 21:00."
            : raw,
        },
        quota ? 503 : 500,
      );
    }
    const headers = new Headers(res.headers);
    for (const [k, v] of Object.entries(corsHeaders())) headers.set(k, v);
    return new Response(res.body, { status: res.status, headers });
  },

  async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    // Cron cada 5 min (ver wrangler.toml): UN ciclo para EL tenant
    // de este Worker (env.TENANT). Un Worker = un tenant: no hay
    // loop por tenants; el Worker de kaez no toca nada de sebastian
    // ni al revés. Además, si el Worker está en marcha, dispara el
    // monitoreo en vivo (panel-live, con throttle de 15 min y sin
    // disparar si ya hay una corrida en curso para ese tenant, en
    // maybeDispatchLive): sin esto el cerebro nunca barre los
    // directos en vivo (fallo real 2026-11-18).
    const tenant = requireTenant(env);
    ctx.waitUntil(
      (async () => {
        // Alerta v3.1: si el Worker se desactivó de la nada, avisar
        // a su chat vinculado. Corre SIEMPRE, corra o no el ciclo:
        // un Worker parado no tiene ciclo que avise. Solo avisa
        // en la transición 1→0 y una sola vez (la marca vive en
        // settings); la parada desde Telegram ya actualizó la
        // marca, así que no duplica. Nunca rompe el ciclo.
        await maybeAlertWorkerStopped(env, tenant, event.scheduledTime);
        // Monitoreo del presupuesto D1 (A6, 2026-11-20): chequeo
        // horario (cada hora en punto) + informe diario 20:50.
        // Solo lecturas (analítica GraphQL + D1); nunca escriben
        // en D1 y nunca rompen el ciclo: corren ANTES del ciclo y
        // aunque el Worker esté parado (es cuando más falta saber
        // el consumo). Sin token de Telegram, degradan a una sola
        // línea de log con dedupe.
        await maybeRunBudgetMonitor(env, tenant, event.scheduledTime).catch(() => {});
        await maybeSendDailyBudgetReport(env, tenant, event.scheduledTime).catch(() => {});
        const r = await runWorkerCycle(env, tenant);
        if (r.ran && (r.processed > 0 || r.stalled > 0)) {
          await logWorkerEvent(env.DB, "ciclo", `Ciclo automático: ${r.processed} jobs procesados, ${r.stalled} atascados recuperados.`, tenant);
        }
        // A3 (2026-11-20): el ciclo del publicador puede salir
        // temprano sin correr (r.ran false) y eso NO apaga los
        // dispatches: cada uno tiene su propia puerta, ya evaluada
        // por cycleGate con solo lecturas (r.gate.liveDue /
        // watchdogDue / dailyDue). Si toca, se llama igual; cada
        // maybe* vuelve a comprobar su freno antes de disparar.
        // Si el Worker está parado, runWorkerCycle devuelve la
        // puerta vacía (todo false) y aquí no se dispara nada,
        // como antes.
        if (r.ran || r.gate.liveDue) await maybeDispatchLive(env, tenant);
        // Vigilante (2026-11-19): mismo sitio que panel-live —
        // dispara panel-watchdog con throttle de 10 min y sin
        // disparar si ya hay una corrida del vigilante en curso
        // para ese tenant (maybeDispatchWatchdog). Solo si el
        // Worker de ese tenant está en marcha (r.ran) o si la
        // puerta dice que el dispatch toca (r.gate.watchdogDue).
        if (r.ran || r.gate.watchdogDue) await maybeDispatchWatchdog(env, tenant);
        // Validador de suministro: en marcha pero sin clips en
        // Cola durante horas = atascado (avisa 1 vez cada 6 h).
        await maybeAlertWorkerStuck(env, tenant, event.scheduledTime);
        // Resumen diario de analíticas por Telegram (v2,
        // 09:00 Santiago, deduplicado por día dentro de
        // maybeSendDailyTelegram: el tenant recibe SOLO sus
        // analíticas en SU chat vinculado). Solo corre si el
        // Worker de ese tenant está en marcha (r.ran) o si la
        // puerta dice que toca el dispatch diario (r.gate.dailyDue).
        if (r.ran || r.gate.dailyDue) await maybeSendDailyTelegram(env, tenant, event.scheduledTime);
        // Pasada diaria del cerebro (09:00 Santiago, una vez al
        // día por tenant en marcha): dispara panel-daily al
        // workflow genérico. Solo si el Worker está en marcha
        // (r.ran) o si la puerta dice que toca (r.gate.dailyDue).
        if (r.ran || r.gate.dailyDue) await maybeDispatchDaily(env, tenant, event.scheduledTime);
        // Validación diaria de cuentas (spec 2026-11-23): primera
        // pasada >= 15:00 Chile, una vez al día, DESPUÉS del ciclo
        // publicador y con su propio catch: nunca bloquea el corte
        // (la puerta y el estado del Worker se comprueban dentro de
        // maybeValidateAccountsDaily).
        await maybeValidateAccountsDaily(env, tenant, event.scheduledTime).catch(() => {});
        // Revalidación automática (spec 2026-11-24, auto-seo): cada
        // 6 h se dispara panel-revalidate al workflow
        // cerebro-revalidate (rellena SEO + revalida pendientes).
        // Solo si el Worker está en marcha (r.ran) o la puerta
        // dice que toca (r.gate.revalidateDue); con su propio
        // catch: nunca bloquea el ciclo.
        if (r.ran || r.gate.revalidateDue) await maybeDispatchRevalidate(env, tenant, event.scheduledTime).catch(() => {});
        // Caducidad nocturna de eventos (A2, 03:00 Santiago):
        // borra eventos de más de 14 días en lotes pequeños, solo
        // si el día va frío (<70% del límite). Corre aunque el
        // Worker esté parado: un Worker parado no escribe nada
        // nuevo y la limpieza no compite con nada.
        await maybePruneOldEvents(env, tenant, event.scheduledTime);
      })(),
    );
  },
};
