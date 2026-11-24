// lib.js — funciones puras, sin chrome.* salvo que se pase explícitamente.
// Se pueden probar con Node: `node --test` no, con `node test-lib.mjs` (ver validate.sh).
// Reglas duras del proyecto, codificadas aquí y no solo en el README:
//   1. Un post = una campaña, una sola vez.
//   2. Envío solo dentro de los 30 minutos desde la publicación.
//   3. Mismo video en 4 redes dentro de la MISMA campaña: sí (cada link es
//      un envío distinto, con su propio postUrl). Mismo post en dos
//      marketplaces/campañas: NO.

export const SUBMIT_WINDOW_MS = 30 * 60 * 1000; // 30 minutos
/** Auto-configuración servida desde la web del panel (v1.3.0):
 * https://avc-em9.pages.dev/extension/auto-setup.json — la extensión
 * la descarga al instalarse y cada hora, sin copiar archivos a mano.
 * Si la web no responde, se usan los valores de este archivo (los
 * mismos por defecto): nunca se deja de funcionar por eso. */
export const AUTO_SETUP_URL = "https://avc-em9.pages.dev/extension/auto-setup.json";

/** Valida un auto-setup.json descargado: objeto, version, campaigns
 * array y rules objeto. Devuelve { ok, version, campaignCount } o
 * { ok: false, error }. Nunca lanza. */
export function parseAutoSetup(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) return { ok: false, error: "auto-setup no es un objeto" };
  if (typeof data.version !== "string" || !data.version) return { ok: false, error: "auto-setup sin version" };
  if (!Array.isArray(data.campaigns)) return { ok: false, error: "auto-setup sin campaigns" };
  if (!data.rules || typeof data.rules !== "object") return { ok: false, error: "auto-setup sin rules" };
  return { ok: true, version: data.version, campaignCount: data.campaigns.length };
}

export const MCP_URL = "https://mcp.contentrewards.com/mcp";
export const EXTENSION_VERSION = "1.4.0";

// v1.3.0 (2026-11-23 18:32, login por tenant): tarifa NETA, no bruta.
// Content Rewards cobra 10% de comisión: neta = bruta x 0,9. El piso
// del usuario es $1/1k NETO: BenDaDonnn ($1,00 bruto = $0,90 neto)
// queda BAJO el piso y NO se auto-asigna ni da prioridad.
export const CONTENT_REWARDS_COMMISSION = 0.1;
export const NET_FLOOR_PER_1K_USD = 1.0;

// REGLA DE FAMA (2026-11-23 18:50): ver `isFamousCampaign` más
// abajo (única fuente: >= 50.000 seguidores verificados; sin dato =
// no famoso). No duplicar el criterio aquí.

/** Tarifa neta por cada 1.000 vistas (bruta x 0,9). */
export function netRatePer1k(campaign) {
  if (!campaign || typeof campaign.rate !== "number") return 0;
  return campaign.rate * (1 - CONTENT_REWARDS_COMMISSION);
}

/** ¿La campaña llega al piso de $1/1k NETO? */
export function meetsNetFloor(campaign) {
  return netRatePer1k(campaign) >= NET_FLOOR_PER_1K_USD;
}

// Campañas verificadas en vivo el 23 nov 2026. Son valores por defecto para
// el selector del popup; el usuario puede escribir otro campaignId.
// Tarifas brutas, antes de la comisión del 10% de Content Rewards.
// v1.2.0 (decisiones del usuario, 2026-11-23 18:19):
//   - Decisión 2: NO hay una campaña fija. El selector es DINÁMICO:
//     el ranking elige la mejor del día por tarifa x viralidad
//     (nº creadores, presupuesto restante, actividad) y se re-verifica
//     la tarifa/presupuesto el día del envío. A una campaña agotada
//     (remaining <= 0) NO se envía.
//   - Decisión 4: ES y EN a la vez: ninguna campaña se filtra por idioma.
//     Los campos `streamers` son los streamers virales de cada campaña
//     (decisión 3, plan en README; la lista de influencers del Worker
//     NO se toca en esta tarea).
//   - `verifiedAt` es el radar del 23 nov 2026; si el día del envío
//     la tarifa/presupuesto no se ha re-verificado, el selector lo marca
//     como `stale` y hay que re-verificarlo en Discover antes de enviar.
export const KNOWN_CAMPAIGNS = [
  { id: "051002dd-5c46-43d0-a446-43f2957ba3a9", name: "Dasgasd3m x RUCKUS | GTA 6", streamer: "Dasgasd3m", rate: 2.0, platforms: ["tiktok", "youtube"], creators: 65, budgetRemaining: 897, budgetTotal: 1000, verifiedAt: "2026-11-23", minViews: 10000, capPerClip: 150, followers: 62500, followersPlatform: "kick" },
  { id: "6e9ca548-f36c-42c2-abc0-61f89b4b53ba", name: "ArthurTV — GTA Clips", streamer: "ArthurTV", rate: 1.5, platforms: ["tiktok"], creators: 0, budgetRemaining: 5000, budgetTotal: 5000, verifiedAt: "2026-11-23", minViews: 10000, capPerClip: 300, followers: 500000, followersPlatform: "youtube" },
  { id: "f583b069-ced7-49e5-9ed3-2d3a13d6df0c", name: "BOBBY VAN RIPER GTA VI", streamer: "Bobby Van Riper", rate: 1.5, platforms: ["instagram", "tiktok", "youtube"], creators: 23, budgetRemaining: 765, budgetTotal: 1000, verifiedAt: "2026-11-23", minViews: 1500, capPerClip: 150, followers: 734, followersPlatform: "twitch" },
  { id: "39753a1a-f1f3-491b-a8f6-0cfee0af0c97", name: "Dylan Page GTA VI Marathon", streamer: "Dylan Page", rate: 1.2, platforms: ["instagram", "tiktok", "x", "youtube"], creators: 7, budgetRemaining: 4815, budgetTotal: 5000, verifiedAt: "2026-11-23", minViews: 3333, capPerClip: 250, followers: 1170000, followersPlatform: "youtube" },
  { id: "bb66f9eb-b90e-4dd0-8cee-9f3cd566c9a0", name: "BenDaDonnn x Puffco GTA 6", streamer: "BenDaDonnn", rate: 1.0, platforms: ["facebook", "instagram", "tiktok", "x", "youtube"], creators: 104, budgetRemaining: 8978, budgetTotal: 10000, verifiedAt: "2026-11-23", minViews: 10000, capPerClip: 400, followers: 645690, followersPlatform: "twitch" },
];

// REGLA DEL USUARIO 2026-11-23 18:50 (también en MEMORY.md): aunque
// sea campaña, SOLO influencers FAMOSOS, no cualquier persona. Una
// campaña que pague mucho pero de un streamer desconocido NO sirve.
// Criterio (única fuente, igual en el Worker db.ts): >= 50.000
// seguidores/suscriptores verificables en ALGUNA plataforma (la de la
// campaña o otra: ArthurTV pasa por YouTube >500k + IG 428k aunque en
// Twitch tenga solo ~20k). Seguidores desconocidos (null) = NO famoso:
// el filtro falla cerrado, nunca a favor. La tracción de la campaña
// (creadores, clips aprobados) es señal secundaria y NO compensa el
// listón: Bobby Van Riper paga $1,50 pero tiene 734 seguidores en
// Twitch y 30 espectadores medios — sus clips promedian 4,1k vistas
// (26 clips, 108k en total, heistclips), así que el tope de $150 es
// inalcanzable en la práctica y la tarifa alta es una trampa.
export const FAME_MIN_FOLLOWERS = 50000;

/** ¿El streamer de la campaña es famoso? (regla 18:50). */
export function isFamousCampaign(campaign) {
  if (!campaign || typeof campaign.followers !== "number") return false;
  return campaign.followers >= FAME_MIN_FOLLOWERS;
}

// Ficha de brief por campaña (v1.3.0): lo que hay que aplicar ANTES
// de renderizar/publicar el clip, no después. Solo lo verificado en
// fuentes públicas; lo demás queda `incomplete: true` y hay que
// leerlo en Discover (o MCP) el día del envío, nunca inventarlo.
// `disclosure`: qué hay que declarar/mostrar; `hashtags`: obligatorios;
// `mentions`: menciones obligatorias; `allowedMaterial`: qué material
// vale; `expiresAt`: cuándo caduca la campaña (null = sin fecha vista).
// MATERIAL (pregunta del usuario 2026-11-23 18:49): NINGUNA campaña
// entrega clips listos para subir. Todas entregan brief + stream/VOD
// permitido y hay que CORTAR TU PROPIO EDIT del stream: `material:
// "brief_only"` en todas. Un clip ya hecho y subido tal cual lo
// rechazan por duplicado / no original: Content Rewards rechaza el
// contenido reutilizado, y un mismo post solo se acepta una vez en
// una campaña. Nuestro cerebro ya corta solo de los streams en vivo,
// así que los streamers de campaña van al live scan, no se busca
// clip por clip a mano. No se descarga material sin verificar el brief.
export const CAMPAIGN_BRIEFS = {
  "051002dd-5c46-43d0-a446-43f2957ba3a9": {
    disclosure: "Cuenta dedicada a GTA 6 (la campaña exige cuentas específicas de GTA 6; se recomiendan cuentas de clips del streamer).",
    hashtags: [], mentions: [],
    allowedMaterial: "Momentos divertidos y entretenidos de GTA 6 de Dasgasd3m (Dom).",
    material: "brief_only",
    materialNote: "Cortar tu propio edit de sus streams/VOD de Kick (no se aceptan links de Kick: guardar el momento con el botón de clip de Kick y cortar de ahí). La campaña NO entrega clips listos.",
    expiresAt: null, minViews: 10000, capPerClip: 150,
    sourceNote: "Brief público leído 2026-11-20 (Clyper). Re-verificar tarifa/presupuesto en Discover el día del envío.",
  },
  "6e9ca548-f36c-42c2-abc0-61f89b4b53ba": {
    disclosure: "Sin disclosure visto en el radar: verificar en Discover antes de enviar.",
    hashtags: [], mentions: [],
    allowedMaterial: "Clips de los vídeos de gameplay GTA de ArthurTV.",
    material: "brief_only",
    materialNote: "Cortar tu propio edit de sus VODs de Twitch / vídeos de YouTube. La campaña NO entrega clips listos.",
    expiresAt: null, minViews: 10000, capPerClip: 300,
    sourceNote: "Campaña nueva del 22 nov 2026 (Content Rewards). Brief incompleto: leer el brief entero en Discover el día del envío.",
    incomplete: true,
  },
  "f583b069-ced7-49e5-9ed3-2d3a13d6df0c": {
    disclosure: "Sin disclosure visto en el radar: verificar en Discover antes de enviar.",
    hashtags: [], mentions: [],
    allowedMaterial: "Contenido GTA VI de Bobby Van Riper.",
    material: "brief_only",
    materialNote: "Cortar tu propio edit de sus streams de Twitch. La campaña NO entrega clips listos. Además EXCLUIDO del ranking por fama (regla 18:50: 734 seguidores).",
    expiresAt: null, minViews: 1500, capPerClip: 150,
    sourceNote: "Brief incompleto: leer el brief entero en Discover el día del envío.",
    incomplete: true,
  },
  "39753a1a-f1f3-491b-a8f6-0cfee0af0c97": {
    disclosure: "El clip DEBE mostrar a Dylan en pantalla.",
    hashtags: ["#DylanPage"], mentions: [],
    allowedMaterial: "Solo el maratón de 120 h de GTA 6 en YouTube (no otros vídeos suyos).",
    material: "brief_only",
    materialNote: "Cortar tu propio edit del maratón en vivo/VOD de YouTube (anotar timestamps a mano mientras se ve en directo). La campaña NO entrega clips listos.",
    expiresAt: null, minViews: 3333, capPerClip: 250,
    sourceNote: "La campaña termina cuando termina el maratón. Tarifa re-verificada el 22 nov ($1,20; era $1,00 el 19-20 nov).",
  },
  "bb66f9eb-b90e-4dd0-8cee-9f3cd566c9a0": {
    disclosure: "El clip DEBE mostrar a BenDaDonnn usando productos Puffco mientras juega a GTA 6.",
    hashtags: [], mentions: [],
    allowedMaterial: "BenDaDonnn con Puffco jugando a GTA 6 (no vale GTA 6 sin Puffco).",
    material: "brief_only",
    materialNote: "Cortar tu propio edit de un vídeo/broadcast suyo de GTA 6 (YouTube @BenDaDonnLive o Twitch). La campaña NO entrega clips listos.",
    expiresAt: null, minViews: 10000, capPerClip: 400,
    sourceNote: "Bajo el piso neto ($0,90): sin auto-asignación. Brief público leído 2026-11-20 (Clyper).",
  },
};

/** Ficha de brief de una campaña (o { incomplete: true } si no hay). */
export function getCampaignBrief(campaignId) {
  const brief = CAMPAIGN_BRIEFS[campaignId];
  if (!brief) return { incomplete: true, disclosure: "", hashtags: [], mentions: [], allowedMaterial: "", material: "brief_only", materialNote: "Sin ficha: leer el brief en Discover antes de enviar; ninguna campaña entrega clips listos.", expiresAt: null, minViews: null, capPerClip: null, sourceNote: "Sin ficha: leer el brief en Discover antes de enviar." };
  return { ...brief };
}

/** Una campaña está agotada si no le queda presupuesto. A esa NO se envía. */
export function isCampaignExhausted(campaign) {
  return !campaign || Number(campaign.budgetRemaining) <= 0;
}

/**
 * Puntuación dinámica: tarifa NETA x viralidad x presupuesto restante.
 * Viralidad = 1 + log10(1 + creadores): un campaña con muchos creadores
 * pesa más, pero una campaña nueva (0 creadores, presupuesto intacto)
 * no queda en cero, solo más baja. El factor de presupuesto restante
 * (remaining/total, 0..1) baja a las campañas casi agotadas.
 * Con el radar del 23 nov (NETO): Dasgasd3m ~4,55 > Dylan Page
 * ~1,98 > ArthurTV ~1,35. BenDaDonnn ($0,90 neto) no entra en el
 * ranking: está bajo el piso (ver rankCampaigns). Bobby Van Riper
 * ($1,35 neto) TAMPOCO entra: 734 seguidores, bajo el listón de
 * fama de 50.000 (regla 2026-11-23 18:50, ver isFamousCampaign).
 */
export function campaignScore(campaign) {
  if (!campaign || typeof campaign.rate !== "number") return 0;
  const creators = Math.max(0, Number(campaign.creators) || 0);
  const virality = 1 + Math.log10(1 + creators);
  const remainingFactor = campaign.budgetTotal > 0
    ? Math.max(0, Math.min(1, Number(campaign.budgetRemaining) / Number(campaign.budgetTotal)))
    : 1;
  return netRatePer1k(campaign) * virality * remainingFactor;
}

/**
 * Ranking dinámico (decisión 2). Filtra por plataforma si se pasa,
 * descarta campañas agotadas, las que están bajo el piso NETO de
 * $1/1k (BenDaDonnn no se auto-asigna) Y las de streamers NO
 * famosos (regla 2026-11-23 18:50: Bobby Van Riper, 734 seguidores,
 * no entra al ranking aunque pague $1,50). Con
 * `includeUnfamous: true` las no famosas vuelven AL FINAL con
 * `excludedReason: "not_famous"` (solo para mostrarlas en el popup,
 * nunca para auto-asignar). Devuelve las campañas ordenadas por
 * puntuación descendente, cada una con su `score`, su `netRate` y
 * `stale` (true si `verifiedAt` no es hoy: hay que re-verificar
 * tarifa/presupuesto en Discover el día del envío antes de enviar).
 */
export function rankCampaigns(campaigns, { platform = null, today = null, includeUnfamous = false } = {}) {
  const famous = (campaigns || [])
    .filter((c) => c && !isCampaignExhausted(c))
    .filter((c) => meetsNetFloor(c))
    .filter((c) => isFamousCampaign(c))
    .filter((c) => !platform || (c.platforms || []).includes(platform))
    .map((c) => ({ ...c, netRate: netRatePer1k(c), score: campaignScore(c), stale: today ? c.verifiedAt !== today : false }));
  famous.sort((a, b) => b.score - a.score);
  if (!includeUnfamous) return famous;
  const unfamous = (campaigns || [])
    .filter((c) => c && !isCampaignExhausted(c))
    .filter((c) => meetsNetFloor(c))
    .filter((c) => !isFamousCampaign(c))
    .filter((c) => !platform || (c.platforms || []).includes(platform))
    .map((c) => ({ ...c, netRate: netRatePer1k(c), score: campaignScore(c), stale: today ? c.verifiedAt !== today : false, excludedReason: "not_famous" }));
  unfamous.sort((a, b) => b.score - a.score);
  return [...famous, ...unfamous];
}

/** Mejor campaña del día para una plataforma (o null si no hay ninguna viable). */
export function selectBestCampaign(campaigns, { platform = null, today = null } = {}) {
  const ranked = rankCampaigns(campaigns, { platform, today });
  return ranked.length ? ranked[0] : null;
}

/** Normaliza un nombre de streamer para comparar ("Bobby Van Riper"
 *  y "bobby_vanriper" son el mismo). */
export function normalizeStreamerName(name) {
  return String(name || "").trim().toLowerCase().replace(/[^a-z0-9]/g, "");
}

/** ¿El clip es de ESE streamer de la campaña? Si no coincide, NO se
 *  auto-asigna: un clip de Kai Cenat no cobra en la campaña de
 *  Dasgasd3m aunque pague más. */
export function matchCampaignStreamer(campaign, clipStreamer) {
  if (!campaign || !clipStreamer) return false;
  return normalizeStreamerName(campaign.streamer) === normalizeStreamerName(clipStreamer);
}

/** Mejor campaña del día PARA ESTE CLIP: filtra por plataforma y por
 *  streamer del clip. Sin streamer del clip no se auto-asigna nada:
 *  { campaign: null, reason: "sin_streamer" }. Sin coincidencia:
 *  { campaign: null, reason: "no_match" }. */
export function selectBestCampaignForClip(campaigns, { platform = null, clipStreamer = null, today = null } = {}) {
  if (!clipStreamer || !String(clipStreamer).trim()) return { campaign: null, reason: "sin_streamer" };
  const ranked = rankCampaigns(campaigns, { platform, today })
    .filter((c) => matchCampaignStreamer(c, clipStreamer));
  if (!ranked.length) return { campaign: null, reason: "no_match" };
  return { campaign: ranked[0], reason: "" };
}

// ---------------------------------------------------------------------------
// MULTI-MARKETPLACE (2026-11-23 18:48): ranking POR TENANT Y POR
// MARKETPLACE, sin duplicar un mismo clip en dos marketplaces.
// Verificado en vivo el 23 nov 2026 (ver README, tabla comparativa):
// solo Content Rewards tiene API/MCP para envío automático; TODO lo
// demás es "solo web, envío manual" (sin endpoint inventado).
// REGLA DURA: UN clip = UN marketplace (la asignación elige uno;
// dentro de ese marketplace, varias plataformas sí, como en CR).
// La asignación vive en `assignments` del state con `marketplace`;
// `marketplaceAssignmentKey` es POR TENANT+CLIP (sin plataforma):
// un clip asignado a Clipster no se re-asigna a Content Rewards.
// ---------------------------------------------------------------------------

// Registro de marketplaces. `commission`: fracción que la plataforma
// descuenta de lo que cobra el clipper (null = no publicada, no se
// puede verificar el piso neto). `api`: "mcp" solo Content Rewards;
// "none" = solo web, envío manual. `material`: qué entrega la campaña
// (brief / fuente autorizada / archivos) — NINGUNA entrega clips
// listos para subir tal cual en GTA 6: hay que cortar tu propio edit
// del stream/fuente permitido (ver README "¿Las campañas traen los
// clips?"). `chile`: "sí" solo cuando la plataforma lo publica o
// paga vía Whop (Chile en su lista); si no, "sin_verificar".
export const MARKETPLACES = [
  { id: "content_rewards", name: "Content Rewards (Whop)", url: "https://contentrewards.com/c/discover", commission: 0.1, commissionNote: "10% de cada pago (términos CR).", minWithdraw: "$1 (saldo Whop)", minViewsPerClip: "según campaña (1.500-10.000)", platforms: ["tiktok", "instagram", "youtube", "facebook", "x"], api: "mcp", apiUrl: "https://mcp.contentrewards.com/mcp", submitMode: "mcp", chile: "sí", chileNote: "Whop paga en Chile (lista de 200+ países, docs.whop.com).", material: "Brief + fuente autorizada por campaña: carpeta de archivos, links de YouTube o VOD del stream nombrado en el brief. Hay que cortar tu propio edit del stream permitido; algunas campañas de marca comparten clips verticales listos (excepción, no la norma en GTA 6).", gta6Today: "21 abiertas hoy (OpenClip 23 nov 21:00 UTC); top Dasgasd3m $2/1k bruto = $1,80 neto.", verifiedAt: "2026-11-23" },
  { id: "vyro", name: "Vyro (MrBeast)", url: "https://vyro.com/campaigns", commission: 0, commissionNote: "Vyro no lista comisión para el clipper; Whop puede cobrar al retirar.", minWithdraw: "$10 (help) / $25 (términos), 1 vez cada 7 días, a billetera Whop", minViewsPerClip: 5000, platforms: ["tiktok", "instagram", "youtube", "x"], api: "none", submitMode: "manual_web", chile: "sí", chileNote: "Chile NO está entre los 28 países sin pago (vyro.com/help, 30 oct 2026).", material: "Brief + material del cliente para descargar; hay que cortar tu propio edit y subirlo por web.", gta6Today: "0 campañas GTA 6 hoy (tablero 26 oct: 12 campañas, ninguna GTA; OpenClip 23 nov: 0 en Vyro).", verifiedAt: "2026-11-23" },
  { id: "clipster", name: "Clipster", url: "https://www.clipster.com", commission: 0.05, commissionNote: "5% de cada retiro.", minWithdraw: "$50", minViewsPerClip: "mediana 2.000 (por campaña)", platforms: ["tiktok", "instagram", "youtube"], api: "none", submitMode: "manual_web", chile: "sin_verificar", chileNote: "Retiro PayPal / USDT / SEPA / IMPS; Chile no confirmado.", material: "Brief + fuente autorizada por campaña; hay que cortar tu propio edit. En GTA 6 hoy solo hay edits de MÚSICA (no gameplay de streamers).", gta6Today: "20 abiertas hoy según OpenClip; las visibles en ClipCPM son música a $1,00/1k máx (neto $0,95 < piso).", verifiedAt: "2026-11-23" },
  { id: "clipping_net", name: "CLIPPING (clipping.net)", url: "https://clipping.net", commission: null, commissionNote: "La FAQ dice que puede aplicar 'team commission'; % no publicado.", minWithdraw: "por ciclo de campaña (sin botón de retiro)", minViewsPerClip: 1000, platforms: ["tiktok", "instagram", "youtube", "x"], api: "none", submitMode: "manual_web", chile: "sin_verificar", chileNote: "Paga PayPal / USDT / USDC por campaña; Chile no confirmado.", material: "Brief (bounty) + fuente autorizada; hay que cortar tu propio edit.", gta6Today: "Sin campaña GTA 6 verificada hoy.", verifiedAt: "2026-11-23" },
  { id: "clipboom", name: "ClipBoom", url: "https://clipboom.io/en", commission: 0.05, commissionNote: "5% sobre el bruto de cada pago (términos ap. 8.2).", minWithdraw: "€150, 2 veces al mes (PayPal / banco / Stripe)", minViewsPerClip: "según campaña", platforms: ["tiktok", "instagram", "youtube"], api: "none", submitMode: "manual_web", chile: "sin_verificar", chileNote: "Plataforma en español, pago en euros; Chile no confirmado.", material: "Brief + contenido del creador/marca dentro de la campaña; hay que cortar tu propio edit.", gta6Today: "Ninguna campaña GTA 6 encontrada hoy.", verifiedAt: "2026-11-23" },
  { id: "clipeamos", name: "Clipeamos", url: "https://www.clipeamos.com", commission: 0, commissionNote: "0% al clipper; solo 2,9% del proveedor de pagos al retirar.", minWithdraw: "$50, automático 2 ciclos/mes (día 1 y 15), también USDC", minViewsPerClip: "según campaña (ej. 1.000)", platforms: ["tiktok", "instagram", "youtube"], api: "none", submitMode: "manual_web", chile: "sin_verificar", chileNote: "Plataforma española (creadores ES); Chile no confirmado.", material: "Brief + contenido de la campaña; hay que cortar tu propio edit.", gta6Today: "Ninguna campaña GTA 6 encontrada hoy. CPM típico 0,30-0,60 €; altas de $3-4/1k en otoño 2026.", verifiedAt: "2026-11-23" },
  { id: "lulosa", name: "Lulosa", url: "https://lulosa.com", commission: null, commissionNote: "La web dice que se aplican 'rate, cap and fee'; % no publicado.", minWithdraw: "$20 (o $1 si el saldo tiene 90+ días)", minViewsPerClip: "según campaña", platforms: ["tiktok", "instagram", "youtube"], api: "none", submitMode: "manual_web", chile: "sin_verificar", chileNote: "Enfoque música latina / LATAM; Chile no confirmado.", material: "Brief de música + material aprobado; hay que cortar tu propio edit (o filmar propio en 'Influencers').", gta6Today: "Ninguna (solo música latina). Media nov 2026: $1,10/1k.", verifiedAt: "2026-11-23" },
  { id: "quantyclips", name: "QuantyClips", url: "https://www.quantyclips.com/en", commission: null, commissionNote: "No publica tarifa por clip ni comisión al clipper.", minWithdraw: "$55", minViewsPerClip: "sin publicar", platforms: ["tiktok", "instagram", "youtube"], api: "none", submitMode: "manual_web", chile: "sin_verificar", chileNote: "Enfoque LATAM; Chile no confirmado.", material: "SIN VERIFICAR: no publica tarifa por clip ni material de campaña.", gta6Today: "Ninguna campaña GTA 6 encontrada hoy.", verifiedAt: "2026-11-23" },
  { id: "ssemble", name: "Ssemble Clip Rewards", url: "https://app.ssemble.com/clip-rewards", commission: null, commissionNote: "Tablero que enlaza a Content Rewards: NO es un marketplace de pago aparte; sus campañas se cobran en CR (misma comisión 10%).", minWithdraw: "n/a (se cobra en Content Rewards)", minViewsPerClip: "según campaña", platforms: ["tiktok", "instagram", "youtube"], api: "none", submitMode: "manual_web", chile: "sin_verificar", chileNote: "No se suma al ranking como marketplace propio.", material: "Muestra los vídeos propios de la campaña para clipar desde ahí; hay que cortar tu propio edit y enviar el link en Content Rewards.", gta6Today: "iShowSpeed GTA 6 ($1/1k) CERRADA el 21 nov; Tomo GTA VI $2/1k visible aquí (campaña CR, ver candidatas).", verifiedAt: "2026-11-23" },
  { id: "clipstake", name: "Clipstake", url: "https://app.clipstake.com", commission: null, commissionNote: "Marketplace nuevo (visto vía ClipCPM 23 nov); comisión no publicada.", minWithdraw: "sin verificar", minViewsPerClip: "sin verificar", platforms: ["instagram"], api: "none", submitMode: "manual_web", chile: "sin_verificar", chileNote: "Sin verificar.", material: "Sin verificar: solo se vio 1 campaña GTA 6 (logo, Instagram).", gta6Today: "DonBet Logo Campaign (GTA6) $0,20/1k, solo Instagram — bajo el piso.", verifiedAt: "2026-11-23" },
  { id: "clippers_media", name: "Clippers Media", url: "https://clippersmedia.com/", commission: null, commissionNote: "Sin comisión publicada.", minWithdraw: "sin verificar", minViewsPerClip: "sin verificar", platforms: ["tiktok", "instagram", "youtube", "x"], api: "none", submitMode: "manual_web", chile: "sin_verificar", chileNote: "Música latina; Chile no confirmado.", material: "Brief + fuente; hay que cortar tu propio edit.", gta6Today: "Ninguna GTA 6: solo música latina ($2-5/1k según la web; sus campañas de ejemplo son ilustrativas, no reales).", verifiedAt: "2026-11-23" },
];

/** Tarifa NETA por 1.000 vistas en un marketplace (null si la comisión no es pública: no se puede verificar el piso). */
export function netRateForMarketplace(grossRate, marketplaceId) {
  const mp = MARKETPLACES.find((m) => m.id === marketplaceId);
  if (!mp || typeof grossRate !== "number") return null;
  if (typeof mp.commission !== "number") return null;
  return grossRate * (1 - mp.commission);
}

/** ¿La tarifa BRUTA de ese marketplace llega al piso de $1/1k NETO? */
export function meetsNetFloorForMarketplace(grossRate, marketplaceId) {
  const net = netRateForMarketplace(grossRate, marketplaceId);
  return net !== null && net >= NET_FLOOR_PER_1K_USD;
}

// Campañas GTA 6 de Clipster verificadas el 23 nov 2026 en ClipCPM
// (https://clipcpm.com/gta-6). OJO: son EDITS DE MÚSICA con GTA 6 en
// el título, no gameplay de streamers GTA. Con la comisión del 5% al
// retirar, $1,00 bruto = $0,95 neto: NINGUNA pasa el piso de $1/1k
// neto. Están aquí para que el ranking las evalúe y las descarte con
// motivo, no para enviar a ellas.
export const CLIPSTER_GTA6_CAMPAIGNS = [
  { marketplace: "clipster", name: "RITMO SALSA [EDITS]", streamer: null, rate: 1.0, platforms: ["tiktok"], budgetRemaining: 9600, budgetTotal: 11000, verifiedAt: "2026-11-23", kind: "music" },
  { marketplace: "clipster", name: "MY JEALOUSY [GTA 6 EDITS]", streamer: null, rate: 1.0, platforms: ["tiktok"], budgetRemaining: 2400, budgetTotal: 2400, verifiedAt: "2026-11-23", kind: "music" },
  { marketplace: "clipster", name: "MONTAGEM CALMO - Phonknight [EDITS]", streamer: null, rate: 1.0, platforms: ["tiktok", "youtube"], budgetRemaining: 1600, budgetTotal: 4400, verifiedAt: "2026-11-23", kind: "music" },
  { marketplace: "clipster", name: "DON'T BELIEVE IT [EDITS]", streamer: null, rate: 1.0, platforms: ["tiktok"], budgetRemaining: 1300, budgetTotal: 2400, verifiedAt: "2026-11-23", kind: "music" },
  { marketplace: "clipster", name: "Deep In Paris - LXNDER [EDITS]", streamer: null, rate: 1.0, platforms: ["tiktok"], budgetRemaining: 1200, budgetTotal: 2000, verifiedAt: "2026-11-23", kind: "music" },
  { marketplace: "clipster", name: "Rein Me In [GTA6 EDITS]", streamer: null, rate: 1.0, platforms: ["tiktok"], budgetRemaining: 1000, budgetTotal: 3200, verifiedAt: "2026-11-23", kind: "music" },
  { marketplace: "clipster", name: "Lord Kebun", streamer: null, rate: 0.75, platforms: ["tiktok", "instagram", "youtube"], budgetRemaining: 2100, budgetTotal: 4100, verifiedAt: "2026-11-23", kind: "music" },
];

// Candidatas de Content Rewards verificadas el 23 nov 2026 en
// ClipCPM (https://clipcpm.com/gta-6) que pasan el piso NETO pero
// SIN UUID público: el UUID NO se inventa, se toma de Discover/MCP
// el día del envío. NO entran al ranking automático hasta tener UUID
// y fama verificada (seguidores desconocidos = no famoso, falla
// cerrado). Tomo tiene ADEMÁS tarifa en conflicto ($2,00 en Ssemble
// vs $1,50 en OpenClip el 19 nov): verificar en la página antes de
// entrar. Los pools (Lacy $12.500, Kai Cenat $10.000, Clix $5.000,
// YourRAGE $5.000) NO son tarifa por 1k y NO entran al ranking.
export const CANDIDATE_CAMPAIGNS_NO_ID = [
  { marketplace: "content_rewards", name: "Lauren Hamden GTA 6 Clipping Campaign", streamer: "Lauren Hamden", rate: 1.5, platforms: ["instagram"], budgetRemaining: 4200, budgetTotal: 5000, verifiedAt: "2026-11-23", followers: null, missing: "campaign UUID (tomar de Discover/MCP) + seguidores para fama" },
  { marketplace: "content_rewards", name: "GTA VI Lucia vs Fanvue AI Lucia", streamer: "Lucia", rate: 1.5, platforms: ["instagram", "tiktok"], budgetRemaining: 5000, budgetTotal: 5000, verifiedAt: "2026-11-23", followers: null, missing: "campaign UUID (tomar de Discover/MCP) + seguidores para fama" },
  { marketplace: "content_rewards", name: "Tomo GTA VI", streamer: "Tomo", rate: 2.0, rateConflict: "Ssemble dice $2,00/1k; OpenClip 19 nov decía $1,50/1k. Verificar en la página.", platforms: ["instagram", "tiktok", "youtube"], budgetRemaining: null, budgetTotal: 1000, verifiedAt: "2026-11-23", followers: null, missing: "campaign UUID + tarifa real + seguidores para fama" },
];

/** Puntuación multi-marketplace: tarifa NETA x viralidad x presupuesto restante (misma fórmula que campaignScore, con neta ya calculada). */
export function marketplaceScore(netRate, creators, budgetRemaining, budgetTotal) {
  if (typeof netRate !== "number") return 0;
  const virality = 1 + Math.log10(1 + Math.max(0, Number(creators) || 0));
  const remainingFactor = budgetTotal > 0
    ? Math.max(0, Math.min(1, Number(budgetRemaining) / Number(budgetTotal)))
    : 1;
  return netRate * virality * remainingFactor;
}

/**
 * Ranking multi-marketplace. `entries`: [{ marketplace, id?, name,
 * streamer, rate (bruta), platforms, creators, budgetRemaining,
 * budgetTotal, verifiedAt, followers? }]. Filtra: agotadas, bajo el
 * piso NETO del marketplace (comisión desconocida = no verificable =
 * fuera), plataforma, y streamers NO famosos (regla 18:50; las de
 * música sin streamer quedan fuera del auto-ranking). Ordena por
 * score descendente. Cada entrada sale con `netRate`, `score` y
 * `stale`. NO inventa UUID ni endpoints: lo manual es manual.
 */
export function rankMarketplaceCampaigns(entries, { platform = null, today = null } = {}) {
  const list = (entries || [])
    .filter((e) => e && Number(e.budgetRemaining) !== 0)
    .filter((e) => meetsNetFloorForMarketplace(e.rate, e.marketplace))
    .filter((e) => !platform || (e.platforms || []).includes(platform))
    .filter((e) => e.streamer && isFamousCampaign(e))
    .map((e) => {
      const netRate = netRateForMarketplace(e.rate, e.marketplace);
      return { ...e, netRate, score: marketplaceScore(netRate, e.creators, e.budgetRemaining, e.budgetTotal), stale: today ? e.verifiedAt !== today : false };
    });
  list.sort((a, b) => b.score - a.score);
  return list;
}

/** Mejor marketplace + campaña PARA ESTE CLIP (un solo marketplace por clip). */
export function selectMarketplaceForClip(entries, { platform = null, clipStreamer = null, today = null } = {}) {
  if (!clipStreamer || !String(clipStreamer).trim()) return { entry: null, reason: "sin_streamer" };
  const ranked = rankMarketplaceCampaigns(entries, { platform, today })
    .filter((e) => matchCampaignStreamer(e, clipStreamer));
  if (!ranked.length) return { entry: null, reason: "no_match" };
  return { entry: ranked[0], reason: "" };
}

/** Clave de asignación POR TENANT+CLIP (sin plataforma): UN
 *  marketplace por clip. Un clip asignado a un marketplace no se
 *  re-asigna a otro (dentro del mismo sí: varias plataformas). */
export function marketplaceAssignmentKey({ tenant = "seba", clipId }) {
  return `${String(tenant || "seba").trim()}|${String(clipId).trim()}`;
}

/** ¿Este clip ya tiene marketplace asignado? `assignments` es el
 *  mapa clipId -> { marketplace, ... } del state (o un mapa con
 *  claves `marketplaceAssignmentKey`). */
export function alreadyAssignedMarketplace(assignments, { tenant = "seba", clipId }) {
  if (!assignments || typeof assignments !== "object") return null;
  const byKey = assignments[marketplaceAssignmentKey({ tenant, clipId })];
  if (byKey && typeof byKey === "object" && byKey.marketplace) return byKey.marketplace;
  const byClip = assignments[String(clipId)];
  if (byClip && typeof byClip === "object" && byClip.marketplace) return byClip.marketplace;
  return null;
}

// ---------------------------------------------------------------------------
// ANALÍTICAS (2026-11-23 18:48): totales desde campaign_submissions en
// D1 (GET /api/campaign-submissions del Worker, sin clip_id = todas
// las filas del tenant) + saldo del marketplace por su API/MCP oficial.
// NUNCA se inventan números: si la ruta D1 no responde (404 = aún no
// desplegada, o sin token), el resumen es { available: false } y el
// popup muestra "sin datos todavía" con el motivo, NO ceros como si
// fueran reales. El saldo sin retirar / retirado NO lo da ninguna API
// verificada (el MCP de Content Rewards no expone saldo; el resto no
// tiene API): siempre null con `balanceNote` ("no disponible por API,
// ver en la web"). D1 NO registra aceptado/rechazado del marketplace:
// `withViews` (submitted con views > 0) es el proxy honesto y así se
// etiqueta; `failed`/`expired` son los rechazos/caducados que D1 sí
// conoce. `amount_usd`/`views` se actualizan por PATCH del Worker;
// null = aún sin actualizar (suma como 0, sin inventar).
// ---------------------------------------------------------------------------
export const ANALYTICS_BALANCE_NOTE = "Saldo sin retirar y retirado: no disponible por API en ningún marketplace verificado (el MCP de Content Rewards no expone saldo; Vyro/Clipster/otros no tienen API). Ver en la web (Whop / Content Rewards).";
export const ANALYTICS_NO_D1_NOTE = "Sin datos todavía: la tabla campaign_submissions (D1) no respondió (ruta aún no desplegada en este Worker o sin token del panel). No son ceros reales.";

/** Resume UNA lista de filas D1 (o { available: false } si no hay filas). */
export function summarizeAnalytics(rows) {
  if (!Array.isArray(rows)) return { available: false, reason: ANALYTICS_NO_D1_NOTE };
  const byCampaignMap = new Map();
  const s = {
    available: true, rows: rows.length,
    sent: 0, pending: 0, failed: 0, expired: 0, withViews: 0,
    approvedViews: 0, totalEarnedUsd: 0,
    unwithdrawnUsd: null, withdrawnUsd: null, balanceNote: ANALYTICS_BALANCE_NOTE,
    acceptedNote: "D1 no registra aceptado/rechazado del marketplace: 'con vistas' = enviados con views > 0 registradas.",
    byCampaign: [],
  };
  for (const r of rows) {
    if (!r || typeof r !== "object") continue;
    const status = r.status;
    if (status === "submitted") s.sent += 1;
    else if (status === "pending") s.pending += 1;
    else if (status === "failed") s.failed += 1;
    else if (status === "expired") s.expired += 1;
    const views = typeof r.views === "number" && Number.isFinite(r.views) ? r.views : 0;
    const amount = typeof r.amount_usd === "number" && Number.isFinite(r.amount_usd) ? r.amount_usd : 0;
    if (status === "submitted") {
      s.approvedViews += views;
      s.totalEarnedUsd += amount;
      if (views > 0) s.withViews += 1;
    }
    const key = `${r.marketplace || "content_rewards"}|${r.campaign_id || "?"}|${r.campaign_name || r.campaign_id || "?"}`;
    const prev = byCampaignMap.get(key) || { campaignId: r.campaign_id || null, campaignName: r.campaign_name || r.campaign_id || "?", marketplace: r.marketplace || "content_rewards", sent: 0, pending: 0, failed: 0, expired: 0, views: 0, amountUsd: 0 };
    if (status === "submitted") prev.sent += 1;
    else if (status === "pending") prev.pending += 1;
    else if (status === "failed") prev.failed += 1;
    else if (status === "expired") prev.expired += 1;
    if (status === "submitted") { prev.views += views; prev.amountUsd += amount; }
    byCampaignMap.set(key, prev);
  }
  s.byCampaign = [...byCampaignMap.values()].sort((a, b) => b.amountUsd - a.amountUsd || b.views - a.views);
  s.totalEarnedUsd = Math.round(s.totalEarnedUsd * 100) / 100;
  return s;
}

/** Resume por tenant + total. `perTenant`: { seba: rows|null, kaez: rows|null }. */
export function summarizeAllAnalytics(perTenant) {
  const tenants = {};
  let totalRows = [];
  let anyAvailable = false;
  for (const t of ["seba", "kaez"]) {
    const rows = perTenant && perTenant[t];
    const summary = summarizeAnalytics(rows);
    tenants[t] = summary;
    if (summary.available) { anyAvailable = true; totalRows = totalRows.concat(rows); }
  }
  return {
    available: anyAvailable,
    reason: anyAvailable ? null : ANALYTICS_NO_D1_NOTE,
    tenants,
    total: anyAvailable ? summarizeAnalytics(totalRows) : { available: false, reason: ANALYTICS_NO_D1_NOTE },
  };
}

/** USD con 2 decimales, o "—" si no es número. */
export function formatUsd(value) {
  return typeof value === "number" && Number.isFinite(value) ? `$${value.toFixed(2)}` : "—";
}

export function submissionKey({ tenant = "seba", postUrl, campaignId }) {
  // v1.2.0, decisión 7: el duplicado es POR TENANT. Un clip de seba no
  // bloquea uno de kaez y viceversa: la clave incluye el tenant.
  return `${String(tenant || "seba").trim()}|${String(postUrl).trim()}|${String(campaignId).trim()}`;
}

/** Clave de asignación POR TENANT + CLIP + PLATAFORMA (v1.3.0):
 *  la asignación ya no es por clip solo: el mismo clip en TikTok
 *  y en YouTube son dos envíos distintos, cada uno con su campaña
 *  (dentro de la MISMA campaña que paga, un link por plataforma). */
export function assignmentKey({ tenant = "seba", clipId, platform }) {
  return `${String(tenant || "seba").trim()}|${String(clipId).trim()}|${String(platform || "").trim().toLowerCase()}`;
}

export function alreadySubmitted(submissions, { tenant = "seba", postUrl, campaignId }) {
  const key = submissionKey({ tenant, postUrl, campaignId });
  return (submissions || []).some(
    (s) => submissionKey(s) === key && s.status !== "rejected"
  );
}

/** ¿Un registro bloqueado se puede reintentar? (v1.3.0: TODOS los
 *  bloqueos reparables, no solo worker_down.) Reparable = algo que
 *  puede resolverse solo sin que el usuario haga nada: worker parado,
 *  chequeo del clip fallido, sin base, error del MCP o de red. NO
 *  reintentables: duplicado, fuera de ventana, sin API key (la pega
 *  el usuario), sin sesión del panel (la pega el usuario), campaña
 *  sin coincidencia de streamer, clip aún no publicado (lo vuelve a
 *  disparar el evento de publicación nuevo) y publishedAtMs ausente
 *  (sin instante real no hay ventana que medir). */
export const RETRYABLE_BLOCK_CODES = [
  "worker_down", "clip_check_failed", "no_worker_base",
  "mcp_error", "worker_error", "submissions_unavailable",
];
export function isRetryableBlocked(record, nowMs = Date.now()) {
  return !!record && record.status === "blocked"
    && RETRYABLE_BLOCK_CODES.includes(record.blockedBy)
    && isWithinWindow(record.publishedAtMs, nowMs);
}

export function isWithinWindow(publishedAtMs, nowMs = Date.now()) {
  const age = nowMs - Number(publishedAtMs);
  return age >= 0 && age <= SUBMIT_WINDOW_MS;
}

export function minutesLeft(publishedAtMs, nowMs = Date.now()) {
  return Math.max(0, Math.round((SUBMIT_WINDOW_MS - (nowMs - Number(publishedAtMs))) / 60000));
}

// Valida un envío antes de tocar la red. Devuelve { ok: true } o { ok: false, reason }.
// v1.2.0: `tenant` (seba/kaez, decisión 7) forma parte de la clave de
// duplicado; `auto` indica envío automático (decisión 5): la confirmación
// la da la campaña asignada por el selector, no un click manual.
export function validateSubmission(submissions, { tenant = "seba", postUrl, campaignId, platform, publishedAtMs, confirmed, auto = false }) {
  if (!confirmed && !auto) return { ok: false, reason: "Falta la confirmación: un post = una campaña, una sola vez." };
  if (!postUrl || !/^https:\/\//.test(String(postUrl).trim())) {
    return { ok: false, reason: "Falta el link público del clip ya publicado (https://…)." };
  }
  if (!campaignId) return { ok: false, reason: "Falta la campaña." };
  if (!platform) return { ok: false, reason: "Falta la plataforma." };
  // v1.3.0: sin instante REAL de publicación no se envía. Nunca se
  // inventa con Date.now(): sin publishedAtMs no se puede comprobar
  // la ventana de 30 min, así que el envío queda bloqueado.
  if (publishedAtMs === undefined || publishedAtMs === null || !Number.isFinite(Number(publishedAtMs)) || Number(publishedAtMs) <= 0) {
    return { ok: false, reason: "Falta el instante real de publicación (publishedAtMs): sin él no se puede comprobar la ventana de 30 min. Nunca se inventa." };
  }
  const campaign = KNOWN_CAMPAIGNS.find((c) => c.id === campaignId);
  if (campaign && !campaign.platforms.includes(platform)) {
    return { ok: false, reason: `Esa campaña no acepta ${platform}.` };
  }
  if (campaign && !meetsNetFloor(campaign)) {
    return { ok: false, reason: `Campaña bajo el piso neto: ${campaign.name} paga $${netRatePer1k(campaign).toFixed(2)}/1k neto (bruto $${campaign.rate.toFixed(2)} menos 10%), bajo el piso de $1/1k neto. No se auto-asigna.` };
  }
  // Regla 2026-11-23 18:50 (solo famosos): ni manual ni automático.
  if (campaign && !isFamousCampaign(campaign)) {
    return { ok: false, reason: `Streamer no famoso: ${campaign.name} es de ${campaign.streamer} (${campaign.followers ?? "?"} seguidores, bajo el listón de 50.000). Una campaña que pague mucho pero de un desconocido no sirve.` };
  }
  if (campaign && isCampaignExhausted(campaign)) {
    return { ok: false, reason: `Campaña agotada: a ${campaign.name} no le queda presupuesto. El selector dinámico elige otra.` };
  }
  if (alreadySubmitted(submissions, { tenant, postUrl, campaignId })) {
    return { ok: false, reason: "Doble envío bloqueado: ese post ya se envió a esa campaña." };
  }
  if (!isWithinWindow(publishedAtMs)) {
    return { ok: false, reason: "Fuera de ventana: pasaron más de 30 minutos desde la publicación. No se envía tarde." };
  }
  return { ok: true };
}

// Payload JSON-RPC para el MCP oficial de Content Rewards.
// Herramienta: create_my_submission (scope my_submissions:write).
// La API key va en el header Authorization, nunca en el cuerpo ni en un log.
export function buildMcpPayload({ campaignId, postUrl, platform }) {
  return {
    jsonrpc: "2.0",
    id: `avc-${Date.now()}`,
    method: "tools/call",
    params: {
      name: "create_my_submission",
      arguments: { campaignId, postUrl, platform },
    },
  };
}

// ---------------------------------------------------------------------------
// v1.3.0 — parseo de la respuesta JSON-RPC de create_my_submission.
// El MCP puede devolver JSON plano o SSE (líneas "data: {...}").
// Un 200 con `error` es un FALLO, no un éxito: antes se trataba
// cualquier 200 como enviado. Aquí se extrae además el
// submission_id (result.submissionId | submission_id | id, o dentro
// de result.content[0].text como JSON) para guardarlo en D1.
// SOLO código + stubs: la verificación en vivo contra el MCP real
// queda PENDIENTE (no se envía nada real para probar).
// ---------------------------------------------------------------------------
/** Extrae el submission_id de un result JSON-RPC (varias formas). */
export function extractSubmissionId(result) {
  if (!result || typeof result !== "object") return null;
  const direct = result.submissionId ?? result.submission_id ?? result.id ?? result.submission?.id ?? null;
  if (typeof direct === "string" && direct.trim()) return direct.trim();
  // tools/call suele devolver { content: [{ type: "text", text: "{...json...}" }] }.
  const content = Array.isArray(result.content) ? result.content : [];
  for (const item of content) {
    if (!item || typeof item.text !== "string") continue;
    try {
      const parsed = JSON.parse(item.text);
      if (parsed && typeof parsed === "object") {
        const inner = parsed.submissionId ?? parsed.submission_id ?? parsed.id ?? parsed.submission?.id ?? null;
        if (typeof inner === "string" && inner.trim()) return inner.trim();
      }
    } catch { /* el texto no es JSON: no hay id que extraer */ }
  }
  return null;
}

/** Parsea el CUERPO de una respuesta MCP (JSON o SSE). */
export function parseMcpResponse(text) {
  if (typeof text !== "string" || !text.trim()) {
    return { ok: false, error: "Respuesta MCP vacía." };
  }
  const candidates = [];
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t === "[DONE]") continue;
    const payload = t.startsWith("data:") ? t.slice(5).trim() : t;
    if (payload.startsWith("{")) candidates.push(payload);
  }
  for (const raw of candidates) {
    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      continue;
    }
    if (!data || typeof data !== "object" || data.jsonrpc !== "2.0") continue;
    if (data.error) {
      const msg = data.error && typeof data.error.message === "string"
        ? data.error.message
        : JSON.stringify(data.error);
      return { ok: false, error: `MCP error${data.error.code !== undefined ? ` ${data.error.code}` : ""}: ${msg}` };
    }
    if ("result" in data) {
      const result = data.result;
      if (result && typeof result === "object" && result.error) {
        return { ok: false, error: `MCP error en result: ${typeof result.error === "string" ? result.error : JSON.stringify(result.error)}` };
      }
      return { ok: true, submissionId: extractSubmissionId(result), result };
    }
  }
  return { ok: false, error: "Respuesta MCP sin resultado JSON-RPC (ni result ni error)." };
}

/** Parsea una respuesta HTTP del MCP: status no-2xx = fallo; 2xx con
 *  error JSON-RPC = fallo (no éxito). */
export function parseMcpHttpResponse(status, text) {
  if (typeof status !== "number" || status < 200 || status >= 300) {
    return { ok: false, error: `MCP HTTP ${status}: ${String(text || "").slice(0, 200)}` };
  }
  return parseMcpResponse(text);
}

// ---------------------------------------------------------------------------
// v2 — conexión a D1 VÍA EL WORKER (nunca directa desde el navegador).
// La D1 no se accede desde Chrome: solo el Worker la lee, con sesión.
// Rutas reales (panel-backend/worker/src/index.ts):
//   GET /api/health            (index.ts:1921-1924, SIN auth) ->
//     { ok, app: "avc", tenant, worker_running: <boolean>, time }
//   GET /api/clips/:id         (index.ts:2732-2740, CON sesión) ->
//     { ok, clip, publish_breakdown, retention }
//     publish_breakdown: [{ platform, state: "posted"|"pending"|
//     "failed"|"not_enqueued", evidence_url, ... }] (r2-retention.ts:77-110)
//     retention.state: "subidos"|"en_cola"|"sin_mp4"|"borrado"
//   GET /api/worker/state      (index.ts:2402, CON sesión, solo lectura)
// Rutas PROHIBIDAS para la extensión (paran o cambian el worker):
//   POST /api/worker/start (index.ts:2458), POST /api/worker/stop
//   (index.ts:2465), POST /api/worker/run-once (index.ts:2475),
//   POST /api/worker/phase (index.ts:2445), POST /api/worker/event
//   (index.ts:2453). La extensión SOLO hace GETs al Worker, nunca
//   llama a ninguna de estas rutas: un chequeo de salud es de lectura,
//   no apaga nada.
// ---------------------------------------------------------------------------

// Origen del panel (donde vive workers.json, archivo público que el
// propio panel lee en frontend/dist/app.js:30-43). La base del Worker
// del tenant activo sale SIEMPRE de ese mapa en tiempo de ejecución,
// nunca de una URL hardcodeada en el código.
export const PANEL_ORIGIN = "https://avc-em9.pages.dev";
export const WORKERS_JSON_URL = `${PANEL_ORIGIN}/workers.json`;

/** Rutas del Worker que la extensión tiene PROHIBIDO llamar. */
export const FORBIDDEN_WORKER_PATHS = [
  "/api/worker/start",
  "/api/worker/stop",
  "/api/worker/run-once",
  "/api/worker/phase",
  "/api/worker/event",
];

/** GETs al Worker que la extensión SÍ puede hacer (solo lectura). */
export const ALLOWED_WORKER_GETS = ["/api/health", "/api/worker/state", "/api/campaign-submissions"];

/**
 * ¿Está prohibida esta llamada al Worker? La extensión solo lee el
 * Worker, con UNA excepción (v1.3.0): POST/PATCH a
 * /api/campaign-submissions, que es escritura MEDIADA POR EL WORKER
 * (el Worker escribe en D1 con sesión; el navegador nunca toca D1
 * directo) y solo para registrar el envío en la cola en nube y
 * marcarlo submitted. Cualquier otra escritura (POST/PUT/PATCH/
 * DELETE) y las rutas de start/stop/run-once/phase/event siguen
 * prohibidas.
 */
export function isForbiddenWorkerCall(url, method = "GET") {
  let path = "";
  try {
    path = new URL(String(url)).pathname;
  } catch {
    path = String(url).split("?")[0];
  }
  const m = String(method).toUpperCase();
  if (FORBIDDEN_WORKER_PATHS.includes(path)) return true;
  if (path === "/api/campaign-submissions") {
    // Lectura + registro de envío (cola en nube) + marcar enviado.
    return !(m === "GET" || m === "POST" || m === "PATCH");
  }
  if (/^\/api\/campaign-submissions\/\d+$/.test(path)) {
    return m !== "PATCH";
  }
  if (m !== "GET") return true;
  if (path === "/api/health" || path === "/api/worker/state") return false;
  if (/^\/api\/clips\/\d+$/.test(path)) return false;
  if (path === "/api/clips") return false;
  // Cualquier otra ruta del Worker: no se toca desde la extensión.
  return true;
}

/** Base del Worker de un tenant desde el mapa de workers.json. */
export function resolveWorkerBase(workersMap, tenant) {
  if (!workersMap || typeof workersMap !== "object") return null;
  const base = workersMap[tenant];
  return typeof base === "string" && base.trim() ? base.trim().replace(/\/+$/, "") : null;
}

/** Interpreta GET /api/health. worker_running es BOOLEAN (index.ts:1924). */
export function parseHealth(data) {
  if (!data || typeof data !== "object") return { running: false, tenant: null, error: "Respuesta de salud inválida." };
  return {
    running: data.worker_running === true,
    tenant: typeof data.tenant === "string" ? data.tenant : null,
    time: typeof data.time === "string" ? data.time : null,
    error: data.worker_running === true ? null : "worker_running no es true.",
  };
}

/**
 * Interpreta GET /api/clips/:id para una plataforma.
 * Devuelve null si la respuesta no trae clip. `notFound` se marca
 * aparte en worker.js (HTTP 404), no aquí.
 */
export function parseClipState(data, platform) {
  if (!data || typeof data !== "object" || !data.clip || typeof data.clip !== "object") return null;
  const breakdown = Array.isArray(data.publish_breakdown) ? data.publish_breakdown : [];
  const entry = breakdown.find((b) => b && b.platform === platform) || null;
  const retention = data.retention && typeof data.retention === "object" ? data.retention : null;
  return {
    clipId: data.clip.id ?? null,
    clipStatus: typeof data.clip.status === "string" ? data.clip.status : null,
    platformState: entry ? entry.state : "not_enqueued",
    evidenceUrl: entry && typeof entry.evidence_url === "string" ? entry.evidence_url : null,
    uploadedPlatforms: Array.isArray(retention?.uploaded) ? retention.uploaded : [],
    retentionState: retention && typeof retention.state === "string" ? retention.state : null,
  };
}

// --- Chequeo de submissions: EN EL WORKER (0041) -----------------------
// La tabla campaign_submissions YA EXISTE en el código del Worker
// (migración 0041 + rutas GET/POST/PATCH /api/campaign-submissions,
// mismo repo), pero AÚN NO ESTÁ DESPLEGADA en los Workers vivos:
// hasta que el coordinador despliegue con wr_v2.py, la ruta
// devuelve 404 y el chequeo usa el registro local como fallback
// (anotado en el resultado como submissionsChecked:
// "local_fallback"). `exists` = la ruta RESPONDE en ese Worker;
// `codeReady` = el código existe en el repo.
export const CAMPAIGN_SUBMISSIONS_PENDING = {
  table: "campaign_submissions",
  proposedRoute: "/api/campaign-submissions",
  exists: false,
  codeReady: true,
  deployed: false,
  note: "Código listo en el repo (0041); pendiente de despliegue en el Worker. Sin ruta viva, el chequeo es local + estado del clip en D1.",
};

/**
 * Puerta del Worker antes de enviar. Pura, testeable en Node.
 * Orden: salud -> sesión/clip en D1 -> publicado (requisito) ->
 * duplicado en campaign_submissions -> local.
 * Falla CERRADA: sin salud, sin sesión o sin clip en D1, NO se envía.
 *
 * CORRECCIÓN v1.3.0 (bug lib.js:293-299): "publicado en la red" NO
 * es "enviado a Content Rewards". Antes, un clip con
 * publish_breakdown posted se bloqueaba como duplicado: con eso
 * NINGÚN clip publicado se podía enviar nunca. Ahora es al revés:
 * posted es el REQUISITO (sin link publicado no hay nada que
 * enviar; si aún no está publicado, code "not_published_yet") y el
 * duplicado lo decide SOLO la fila en campaign_submissions para
 * tenant+clip+plataforma+campaña (status pending/submitted). Si el
 * Worker aún no tiene la ruta desplegada (submissionsAvailable
 * false), el chequeo cae al registro local y queda anotado como
 * "local_fallback": no se inventa que se consultó D1.
 */
export function decideWorkerGate({ tenant = "seba", health, clipState, clipNotFound, noPanelSession, localSubmissions, submissions = null, submissionsAvailable = false, postUrl, campaignId, platform }) {
  if (!health || health.error || health.running !== true) {
    return { ok: false, code: "worker_down", reason: "Worker parado o sin respuesta: envío en pausa. La extensión no apaga el worker, solo lee su estado; el worker tiene que estar activo 24/7 y la extensión lo reintenta cada 5 min cuando vuelve. El popup lo muestra en rojo como \"worker parado — avisar\"." };
  }
  if (noPanelSession) {
    return { ok: false, code: "no_panel_session", reason: "Sin sesión del panel: pega el token de sesión del panel en el popup para consultar el clip en D1. Sin ese chequeo no se envía (así no se duplica)." };
  }
  if (clipNotFound) {
    return { ok: false, code: "clip_not_in_d1", reason: "Ese clip no está en la base de datos (D1) de este tenant. No se envía un clip que el sistema no conoce." };
  }
  if (!clipState) {
    return { ok: false, code: "clip_check_failed", reason: "No se pudo leer el estado del clip en D1. Envío bloqueado hasta poder consultarlo." };
  }
  const published = clipState.platformState === "posted"
    || (clipState.clipStatus === "publicado" && clipState.uploadedPlatforms.includes(platform));
  if (!published) {
    return { ok: false, code: "not_published_yet", reason: `El clip aún no está publicado en ${platform} según D1 (${clipState.platformState}): no hay link que enviar. Se envía cuando el Worker lo marque posted con su evidence_url.` };
  }
  if (submissionsAvailable) {
    const dup = (submissions || []).find((s) => s && (s.status === "pending" || s.status === "submitted"));
    if (dup) {
      return { ok: false, code: "duplicate_submission", reason: `Doble envío bloqueado: en D1 ya hay un envío ${dup.status === "submitted" ? "enviado" : "pendiente"} de este clip en ${platform} a esta campaña (campaign_submissions). Un post = una campaña, una sola vez.` };
    }
  }
  if (alreadySubmitted(localSubmissions || [], { tenant, postUrl, campaignId })) {
    return { ok: false, code: "duplicate_local", reason: "Doble envío bloqueado: ese post ya se envió a esa campaña (registro local, de este tenant)." };
  }
  return {
    ok: true,
    code: "ok",
    reason: "",
    clipCheck: {
      clipStatus: clipState.clipStatus,
      platformState: clipState.platformState,
      retentionState: clipState.retentionState,
      submissionsAvailable,
      submissionsChecked: submissionsAvailable ? "worker" : "local_fallback",
    },
  };
}
