#!/usr/bin/env bash
# validate.sh — valida la extensión v1.3.0 sin Chrome: JSON, sintaxis JS y reglas.
set -e
cd "$(dirname "$0")"
echo "== manifest.json =="
python3 -m json.tool manifest.json > /dev/null && echo "JSON válido"
python3 -c "import json; m=json.load(open('manifest.json')); assert m['manifest_version']==3; assert m['version']=='1.3.0', m['version']; assert m['background']['service_worker']=='background.js'; assert not m.get('content_scripts'); assert 'identity' not in m.get('permissions', []), 'identity no se usa (OAuth por pestaña) y se quitó en v1.3.0'; assert any('workers.dev' in h for h in m['host_permissions']), 'faltan hosts workers.dev'; print('MV3 OK v1.3.0, sin content scripts, sin identity, hosts:', m['host_permissions'])"
echo "== sintaxis JS =="
node --check lib.js && echo "lib.js OK"
node --check worker.js && echo "worker.js OK"
node --check background.js && echo "background.js OK"
node --check popup.js && echo "popup.js OK"
echo "== lista de bloqueo: la extensión jamás llama a parar el worker =="
# La única aparición de las rutas prohibidas como literales de código
# es la constante de bloqueo en lib.js. Los comentarios que las
# nombran (background.js) no cuentan: el test busca literales entre
# comillas, que es como se llamaría a una ruta en código.
grep -rn "api/worker/stop" lib.js > /dev/null && echo "stop solo en la constante de bloqueo (lib.js)"
if grep -rn '"/api/worker/stop"\|"/api/worker/start"\|"/api/worker/run-once"' background.js worker.js popup.js; then
  echo "ERROR: una llamada a start/stop/run-once fuera de lib.js"; exit 1
fi
echo "OK: background/worker/popup no llaman a start/stop/run-once"
# v1.3.0: POST/PATCH a /api/campaign-submissions SÍ está permitido
# (escritura mediada por el Worker, con sesión; el navegador nunca
# toca D1 directo). POST a /api/clips/:id sigue prohibido.
if grep -rn '"/api/clips/[0-9]' background.js worker.js popup.js | grep -v "GET\|api/clips/\""; then
  echo "ERROR: escritura a /api/clips/:id fuera de lib.js"; exit 1
fi
echo "OK: nadie escribe a /api/clips/:id (solo GET)"
echo "== reglas v1.3.0 (Node) =="
node --input-type=module -e "
import { validateSubmission, alreadySubmitted, isWithinWindow, buildMcpPayload, decideWorkerGate, isForbiddenWorkerCall, parseHealth, parseClipState, resolveWorkerBase, CAMPAIGN_SUBMISSIONS_PENDING, KNOWN_CAMPAIGNS, CAMPAIGN_BRIEFS, getCampaignBrief, rankCampaigns, selectBestCampaign, selectBestCampaignForClip, campaignScore, isCampaignExhausted, isRetryableBlocked, RETRYABLE_BLOCK_CODES, submissionKey, assignmentKey, netRatePer1k, meetsNetFloor, isFamousCampaign, FAME_MIN_FOLLOWERS, parseMcpResponse, parseMcpHttpResponse, extractSubmissionId, MARKETPLACES, netRateForMarketplace, meetsNetFloorForMarketplace, CLIPSTER_GTA6_CAMPAIGNS, CANDIDATE_CAMPAIGNS_NO_ID, rankMarketplaceCampaigns, selectMarketplaceForClip, marketplaceAssignmentKey, alreadyAssignedMarketplace, summarizeAnalytics, summarizeAllAnalytics, formatUsd, AUTO_SETUP_URL, parseAutoSetup } from './lib.js';
import { checkWorkerGate, checkCampaignSubmissions, listAllSubmissions } from './worker.js';
let __assertFails = 0;
const __assert = console.assert.bind(console);
console.assert = (cond, ...args) => { if (!cond) __assertFails++; __assert(cond, ...args); };
const subs = [];
const now = Date.now();
const base = { postUrl: 'https://www.tiktok.com/@elclipdegta6/video/1', campaignId: '051002dd-5c46-43d0-a446-43f2957ba3a9', platform: 'tiktok', publishedAtMs: now, confirmed: true };
console.assert(validateSubmission(subs, base).ok, 'envío válido debe pasar');
console.assert(!validateSubmission(subs, { ...base, confirmed: false }).ok, 'sin confirmación debe bloquearse');
console.assert(!validateSubmission(subs, { ...base, publishedAtMs: now - 31*60*1000 }).ok, '31 min debe bloquearse (ventana 30)');
console.assert(isWithinWindow(now - 29*60*1000), '29 min debe estar en ventana');
subs.push({ postUrl: base.postUrl, campaignId: base.campaignId, status: 'submitted' });
console.assert(alreadySubmitted(subs, base), 'duplicado debe detectarse');
console.assert(!validateSubmission(subs, base).ok, 'doble envío debe bloquearse');
const p = buildMcpPayload({ campaignId: base.campaignId, postUrl: base.postUrl, platform: 'tiktok' });
console.assert(p.params.name === 'create_my_submission', 'payload MCP debe usar create_my_submission');
// --- v1.3.0: sin publishedAtMs REAL no se envía (nunca Date.now() por defecto) ---
console.assert(!validateSubmission([], { ...base, publishedAtMs: undefined }).ok, 'sin publishedAtMs debe bloquearse');
console.assert(!validateSubmission([], { ...base, publishedAtMs: 0 }).ok, 'publishedAtMs 0 debe bloquearse');
console.assert(!validateSubmission([], { ...base, publishedAtMs: 'no-numerico' }).ok, 'publishedAtMs no numérico debe bloquearse');
// --- v1.3.0: piso NETO y FAMA en validateSubmission ---
console.assert(!validateSubmission([], { ...base, campaignId: 'bb66f9eb-b90e-4dd0-8cee-9f3cd566c9a0' }).ok, 'BenDaDonnn ($0,90 neto) debe bloquearse por piso');
console.assert(!validateSubmission([], { ...base, campaignId: 'f583b069-ced7-49e5-9ed3-2d3a13d6df0c' }).ok, 'Bobby Van Riper (no famoso) debe bloquearse por fama aunque pague $1,50');
// --- v2: puerta del Worker, CORREGIDA v1.3.0 ---
// 'publicado en la red' NO es 'enviado a Content Rewards': posted en
// D1 es el REQUISITO para enviar, no un duplicado. El duplicado lo
// decide la fila en campaign_submissions.
const healthOk = { running: true, tenant: 'sebastian' };
const clipEnCola = { clipStatus: 'aprobado', platformState: 'not_enqueued', uploadedPlatforms: [], retentionState: 'en_cola' };
const clipPosted = { clipStatus: 'publicado', platformState: 'posted', uploadedPlatforms: ['tiktok'], retentionState: 'subidos', evidenceUrl: base.postUrl };
console.assert(!decideWorkerGate({ health: healthOk, clipState: clipEnCola, localSubmissions: [], submissionsAvailable: true, submissions: [], ...base }).ok, 'clip en cola (no publicado) debe bloquearse como not_published_yet');
console.assert(decideWorkerGate({ health: healthOk, clipState: clipEnCola, localSubmissions: [], submissionsAvailable: true, submissions: [], ...base }).code === 'not_published_yet', 'código not_published_yet');
console.assert(decideWorkerGate({ health: healthOk, clipState: clipPosted, localSubmissions: [], submissionsAvailable: true, submissions: [], ...base }).ok, 'clip posted en D1 DEBE PASAR la puerta (es el requisito, no un duplicado)');
console.assert(decideWorkerGate({ health: healthOk, clipState: clipPosted, localSubmissions: [], submissionsAvailable: true, submissions: [], ...base }).clipCheck.submissionsChecked === 'worker', 'con ruta desplegada el chequeo es worker');
console.assert(!decideWorkerGate({ health: { running: false }, clipState: clipPosted, localSubmissions: [], ...base }).ok, 'worker parado debe bloquearse');
console.assert(!decideWorkerGate({ health: null, clipState: clipPosted, localSubmissions: [], ...base }).ok, 'worker sin respuesta debe bloquearse');
console.assert(!decideWorkerGate({ health: healthOk, clipState: null, noPanelSession: true, localSubmissions: [], ...base }).ok, 'sin sesión del panel debe bloquearse (falla cerrada)');
console.assert(!decideWorkerGate({ health: healthOk, clipState: null, clipNotFound: true, localSubmissions: [], ...base }).ok, 'clip no en D1 debe bloquearse');
console.assert(!decideWorkerGate({ health: healthOk, clipState: clipPosted, localSubmissions: [], submissionsAvailable: true, submissions: [{ status: 'submitted' }], ...base }).ok, 'fila submitted en campaign_submissions debe bloquearse como duplicate_submission');
console.assert(decideWorkerGate({ health: healthOk, clipState: clipPosted, localSubmissions: [], submissionsAvailable: true, submissions: [{ status: 'submitted' }], ...base }).code === 'duplicate_submission', 'código duplicate_submission');
console.assert(!decideWorkerGate({ health: healthOk, clipState: clipPosted, localSubmissions: [], submissionsAvailable: true, submissions: [{ status: 'pending' }], ...base }).ok, 'fila pending en campaign_submissions también bloquea');
console.assert(decideWorkerGate({ health: healthOk, clipState: clipPosted, localSubmissions: [], submissionsAvailable: true, submissions: [{ status: 'failed' }], ...base }).ok, 'fila failed NO bloquea (se puede reintentar)');
console.assert(!decideWorkerGate({ health: healthOk, clipState: clipPosted, localSubmissions: subs, submissionsAvailable: false, ...base }).ok, 'sin ruta desplegada, el duplicado local sigue bloqueando (fallback)');
console.assert(decideWorkerGate({ health: healthOk, clipState: clipPosted, localSubmissions: [], submissionsAvailable: false, ...base }).ok, 'sin ruta desplegada y sin duplicado local, pasa con fallback local');
console.assert(decideWorkerGate({ health: healthOk, clipState: clipPosted, localSubmissions: [], submissionsAvailable: false, ...base }).clipCheck.submissionsChecked === 'local_fallback', 'fallback anotado como local_fallback');
// --- lista de bloqueo ---
console.assert(isForbiddenWorkerCall('https://avc.vikingosfoodspa.workers.dev/api/worker/stop', 'POST'), 'POST stop prohibido');
console.assert(isForbiddenWorkerCall('https://avc.vikingosfoodspa.workers.dev/api/worker/start', 'POST'), 'POST start prohibido');
console.assert(isForbiddenWorkerCall('https://avc.vikingosfoodspa.workers.dev/api/worker/run-once', 'POST'), 'POST run-once prohibido');
console.assert(isForbiddenWorkerCall('https://avc.vikingosfoodspa.workers.dev/api/clips/174', 'POST'), 'POST a clips prohibido (solo GET)');
console.assert(!isForbiddenWorkerCall('https://avc.vikingosfoodspa.workers.dev/api/health', 'GET'), 'GET health permitido');
console.assert(!isForbiddenWorkerCall('https://avc.vikingosfoodspa.workers.dev/api/clips/174', 'GET'), 'GET clip permitido');
console.assert(!isForbiddenWorkerCall('https://avc.vikingosfoodspa.workers.dev/api/worker/state', 'GET'), 'GET worker/state permitido');
console.assert(!isForbiddenWorkerCall('https://avc.vikingosfoodspa.workers.dev/api/campaign-submissions', 'GET'), 'GET campaign-submissions permitido (lectura mediada)');
console.assert(!isForbiddenWorkerCall('https://avc.vikingosfoodspa.workers.dev/api/campaign-submissions', 'POST'), 'POST campaign-submissions permitido (registro mediado por el Worker)');
console.assert(!isForbiddenWorkerCall('https://avc.vikingosfoodspa.workers.dev/api/campaign-submissions/12', 'PATCH'), 'PATCH campaign-submissions/:id permitido (marcar submitted)');
console.assert(isForbiddenWorkerCall('https://avc.vikingosfoodspa.workers.dev/api/campaign-submissions', 'DELETE'), 'DELETE campaign-submissions prohibido');
console.assert(isForbiddenWorkerCall('https://avc.vikingosfoodspa.workers.dev/api/campaign-submissions/12', 'POST'), 'POST campaign-submissions/:id prohibido');
// --- health / clip / mapa ---
console.assert(parseHealth({ worker_running: true, tenant: 'sebastian' }).running === true, 'health true debe dar corriendo');
console.assert(parseHealth({ worker_running: false }).running === false, 'health false debe dar parado');
console.assert(parseHealth({ worker_running: 'sí' }).running === false, 'health no booleano no es corriendo');
const cs = parseClipState({ clip: { id: 174, status: 'publicado' }, publish_breakdown: [{ platform: 'tiktok', state: 'posted', evidence_url: base.postUrl }], retention: { state: 'subidos', uploaded: ['tiktok'] } }, 'tiktok');
console.assert(cs.platformState === 'posted' && cs.evidenceUrl === base.postUrl, 'parseClipState debe leer posted + evidence_url');
console.assert(resolveWorkerBase({ seba: 'https://avc.vikingosfoodspa.workers.dev', kaez: 'https://avc-kaez.clipsultravirales768.workers.dev' }, 'kaez') === 'https://avc-kaez.clipsultravirales768.workers.dev', 'base kaez desde workers.json');
console.assert(resolveWorkerBase({}, 'seba') === null, 'sin mapa no hay base');
console.assert(CAMPAIGN_SUBMISSIONS_PENDING.codeReady === true, 'el código de submissions existe en el repo (migración 0041); el despliegue en vivo queda pendiente');
console.assert(CAMPAIGN_SUBMISSIONS_PENDING.exists === false, 'la tabla aún no está desplegada en los Workers vivos');
console.assert((await checkCampaignSubmissions()).available === false, 'checkCampaignSubmissions sin token dice no disponible');
// --- puerta completa con fetch stub (el stub también sirve /api/campaign-submissions) ---
const stubFetch = async (url, init) => {
  if (String(url).endsWith('/api/health')) return { ok: true, status: 200, json: async () => ({ ok: true, tenant: 'sebastian', worker_running: true }) };
  if (String(url).includes('/api/campaign-submissions')) return { ok: true, status: 200, json: async () => ({ ok: true, submissions: [] }) };
  if (String(url).includes('/api/clips/174')) return { ok: true, status: 200, json: async () => ({ ok: true, clip: { id: 174, status: 'publicado' }, publish_breakdown: [{ platform: 'tiktok', state: 'posted', evidence_url: base.postUrl }], retention: { state: 'subidos', uploaded: ['tiktok'] } }) };
  throw new Error('URL no esperada en stub: ' + url);
};
const gateOk = await checkWorkerGate({ base: 'https://avc.vikingosfoodspa.workers.dev', panelToken: 'tok', clipId: 174, platform: 'tiktok', postUrl: base.postUrl, campaignId: base.campaignId, localSubmissions: [], fetchImpl: stubFetch });
console.assert(gateOk.ok, 'puerta completa con stub (clip posted + submissions vacías) debe pasar: ' + JSON.stringify(gateOk));
console.assert(gateOk.submissions.available === true, 'stub con ruta de submissions debe marcarla disponible');
const stubDup = async (url) => {
  if (String(url).endsWith('/api/health')) return { ok: true, status: 200, json: async () => ({ ok: true, tenant: 'sebastian', worker_running: true }) };
  if (String(url).includes('/api/campaign-submissions')) return { ok: true, status: 200, json: async () => ({ ok: true, submissions: [{ id: 1, status: 'submitted' }] }) };
  return { ok: true, status: 200, json: async () => ({ ok: true, clip: { id: 174, status: 'publicado' }, publish_breakdown: [{ platform: 'tiktok', state: 'posted', evidence_url: base.postUrl }], retention: { state: 'subidos', uploaded: ['tiktok'] } }) };
};
const gateDup = await checkWorkerGate({ base: 'https://avc.vikingosfoodspa.workers.dev', panelToken: 'tok', clipId: 174, platform: 'tiktok', postUrl: base.postUrl, campaignId: base.campaignId, localSubmissions: [], fetchImpl: stubDup });
console.assert(!gateDup.ok && gateDup.code === 'duplicate_submission', 'puerta con fila submitted en D1 debe bloquearse como duplicate_submission');
const stubNoRoute = async (url) => {
  if (String(url).endsWith('/api/health')) return { ok: true, status: 200, json: async () => ({ ok: true, tenant: 'sebastian', worker_running: true }) };
  if (String(url).includes('/api/campaign-submissions')) return { ok: false, status: 404, json: async () => ({}) };
  return { ok: true, status: 200, json: async () => ({ ok: true, clip: { id: 174, status: 'publicado' }, publish_breakdown: [{ platform: 'tiktok', state: 'posted', evidence_url: base.postUrl }], retention: { state: 'subidos', uploaded: ['tiktok'] } }) };
};
const gateFallback = await checkWorkerGate({ base: 'https://avc.vikingosfoodspa.workers.dev', panelToken: 'tok', clipId: 174, platform: 'tiktok', postUrl: base.postUrl, campaignId: base.campaignId, localSubmissions: [], fetchImpl: stubNoRoute });
console.assert(gateFallback.ok && gateFallback.submissions.available === false, 'ruta 404: fallback local y pasa sin duplicado local');
const stubStopped = async (url) => ({ ok: true, status: 200, json: async () => ({ ok: true, tenant: 'sebastian', worker_running: false }) });
const gateStopped = await checkWorkerGate({ base: 'https://avc.vikingosfoodspa.workers.dev', panelToken: 'tok', clipId: 174, platform: 'tiktok', postUrl: base.postUrl, campaignId: base.campaignId, localSubmissions: [], fetchImpl: stubStopped });
console.assert(!gateStopped.ok && gateStopped.code === 'worker_down', 'puerta con worker parado debe bloquearse');
// --- v1.3.0: ranking NETO + FAMA (regla 18:50) ---
console.assert(KNOWN_CAMPAIGNS.length === 5, 'ranking: 5 campañas por defecto');
console.assert(FAME_MIN_FOLLOWERS === 50000, 'listón de fama: 50.000 seguidores');
console.assert(isFamousCampaign(KNOWN_CAMPAIGNS[0]), 'Dasgasd3m (62.500) es famoso');
console.assert(isFamousCampaign(KNOWN_CAMPAIGNS[1]), 'ArthurTV (500.000 YT) es famoso');
console.assert(!isFamousCampaign(KNOWN_CAMPAIGNS[2]), 'Bobby Van Riper (734) NO es famoso');
console.assert(isFamousCampaign(KNOWN_CAMPAIGNS[3]), 'Dylan Page (1,17M) es famoso');
console.assert(!isFamousCampaign({}), 'sin seguidores no es famoso (falla cerrado)');
console.assert(netRatePer1k(KNOWN_CAMPAIGNS[0]) === 1.8, 'neta Dasgasd3m $1,80');
console.assert(meetsNetFloor(KNOWN_CAMPAIGNS[0]) && !meetsNetFloor(KNOWN_CAMPAIGNS[4]), 'piso neto: BenDaDonnn $0,90 fuera');
const ranked = rankCampaigns(KNOWN_CAMPAIGNS, { today: '2026-11-23' });
console.assert(ranked.map((r) => r.id).join(',') === '051002dd-5c46-43d0-a446-43f2957ba3a9,39753a1a-f1f3-491b-a8f6-0cfee0af0c97,6e9ca548-f36c-42c2-abc0-61f89b4b53ba', 'ranking neto+fama: Dasgasd3m > Dylan > ArthurTV (Bobby fuera por fama, BenDaDonnn por piso): ' + JSON.stringify(ranked.map(r => r.id)));
console.assert(ranked[0].score > ranked[1].score && ranked[1].score > ranked[2].score, 'ranking: orden descendente por score');
const rankedAll = rankCampaigns(KNOWN_CAMPAIGNS, { today: '2026-11-23', includeUnfamous: true });
console.assert(rankedAll[rankedAll.length - 1].id === 'f583b069-ced7-49e5-9ed3-2d3a13d6df0c' && rankedAll[rankedAll.length - 1].excludedReason === 'not_famous', 'con includeUnfamous, Bobby queda ÚLTIMA con excludedReason not_famous');
console.assert(selectBestCampaign(KNOWN_CAMPAIGNS, { platform: 'tiktok' }).id === '051002dd-5c46-43d0-a446-43f2957ba3a9', 'mejor para TikTok debe ser Dasgasd3m');
console.assert(selectBestCampaign(KNOWN_CAMPAIGNS, { platform: 'facebook' }) === null, 'mejor para Facebook: null (BenDaDonnn es la única y está bajo el piso)');
console.assert(campaignScore(KNOWN_CAMPAIGNS[0]) > campaignScore(KNOWN_CAMPAIGNS[3]), 'score Dasgasd3m > Dylan Page');
console.assert(isCampaignExhausted({ budgetRemaining: 0 }), 'campaña con $0 restante está agotada');
console.assert(!isCampaignExhausted(KNOWN_CAMPAIGNS[0]), 'Dasgasd3m no está agotada');
console.assert(selectBestCampaign([{ ...KNOWN_CAMPAIGNS[0], budgetRemaining: 0 }], {}) === null, 'sin campañas viables (agotada) no se elige ninguna');
console.assert(rankCampaigns(KNOWN_CAMPAIGNS, { today: '2026-11-24' })[0].stale === true, 'tarifa del 23 nov es stale el 24: hay que re-verificar el día del envío');
// --- v1.3.0: asignación por clip (streamer del clip ↔ streamer de la campaña) ---
console.assert(selectBestCampaignForClip(KNOWN_CAMPAIGNS, { platform: 'tiktok', clipStreamer: null }).reason === 'sin_streamer', 'sin streamer del clip no se auto-asigna');
console.assert(selectBestCampaignForClip(KNOWN_CAMPAIGNS, { platform: 'tiktok', clipStreamer: 'Kai Cenat' }).reason === 'no_match', 'clip de Kai Cenat no cobra en la campaña de Dasgasd3m');
console.assert(selectBestCampaignForClip(KNOWN_CAMPAIGNS, { platform: 'tiktok', clipStreamer: 'Dasgasd3m' }).campaign.id === '051002dd-5c46-43d0-a446-43f2957ba3a9', 'clip de Dasgasd3m sí se asigna a su campaña');
console.assert(selectBestCampaignForClip(KNOWN_CAMPAIGNS, { platform: 'tiktok', clipStreamer: 'Bobby Van Riper' }).reason === 'no_match', 'clip de Bobby no se auto-asigna (fuera del ranking por fama)');
console.assert(selectBestCampaignForClip(KNOWN_CAMPAIGNS, { platform: 'youtube', clipStreamer: 'dylan page' }).campaign.id === '39753a1a-f1f3-491b-a8f6-0cfee0af0c97', 'el match normaliza mayúsculas/espacios');
// --- v1.3.0: brief por campaña (material: brief_only, nunca clips listos) ---
console.assert(Object.values(CAMPAIGN_BRIEFS).every((b) => b.material === 'brief_only'), 'todas las fichas dicen brief_only: ninguna campaña entrega clips listos');
const briefDylan = getCampaignBrief('39753a1a-f1f3-491b-a8f6-0cfee0af0c97');
console.assert(briefDylan.hashtags.includes('#DylanPage') && briefDylan.materialNote.includes('maratón'), 'brief Dylan: #DylanPage + maratón');
console.assert(getCampaignBrief('inexistente').incomplete === true, 'sin ficha, brief incompleto (leer en Discover, nunca inventar)');
// --- v1.3.0: MCP — 200 con error = FALLO, no éxito ---
console.assert(parseMcpHttpResponse(200, JSON.stringify({ jsonrpc: '2.0', id: 1, result: { submissionId: 'sub_123' } })).ok, 'MCP 200 con result debe pasar');
console.assert(parseMcpHttpResponse(200, JSON.stringify({ jsonrpc: '2.0', id: 1, result: { submissionId: 'sub_123' } })).submissionId === 'sub_123', 'submissionId se guarda del result');
console.assert(!parseMcpHttpResponse(200, JSON.stringify({ jsonrpc: '2.0', id: 1, error: { code: -32000, message: 'nope' } })).ok, 'MCP 200 con error JSON-RPC es FALLO');
console.assert(!parseMcpHttpResponse(200, JSON.stringify({ jsonrpc: '2.0', id: 1, result: { error: 'duplicate' } })).ok, 'MCP 200 con error dentro de result es FALLO');
console.assert(!parseMcpHttpResponse(500, 'error').ok, 'MCP HTTP 500 es FALLO');
console.assert(parseMcpResponse('data: ' + JSON.stringify({ jsonrpc: '2.0', id: 1, result: { content: [{ type: 'text', text: JSON.stringify({ submissionId: 'sub_sse' }) }] } }) + '\n').submissionId === 'sub_sse', 'SSE data: con submissionId dentro de content[0].text');
console.assert(extractSubmissionId({ content: [{ type: 'text', text: JSON.stringify({ submission_id: 'sub_txt' }) }] }) === 'sub_txt', 'extractSubmissionId lee content[0].text JSON');
console.assert(extractSubmissionId({ submissionId: 'sub_x' }) === 'sub_x', 'extractSubmissionId directo');
console.assert(extractSubmissionId({}) === null, 'sin id, null');
// --- v1.3.0: asignación por tenant+clip+plataforma ---
console.assert(assignmentKey({ tenant: 'seba', clipId: 174, platform: 'tiktok' }) === 'seba|174|tiktok', 'clave de asignación seba|174|tiktok');
console.assert(assignmentKey({ tenant: 'seba', clipId: 174, platform: 'tiktok' }) !== assignmentKey({ tenant: 'seba', clipId: 174, platform: 'youtube' }), 'mismo clip en otra plataforma = otra asignación');
console.assert(assignmentKey({ tenant: 'seba', clipId: 174, platform: 'tiktok' }) !== assignmentKey({ tenant: 'kaez', clipId: 174, platform: 'tiktok' }), 'mismo clip en otro tenant = otra asignación');
// --- v1.3.0: reintentos de TODOS los bloqueos reparables ---
console.assert(RETRYABLE_BLOCK_CODES.join(',') === 'worker_down,clip_check_failed,no_worker_base,mcp_error,worker_error,submissions_unavailable', 'códigos reintentables');
for (const code of RETRYABLE_BLOCK_CODES) {
  console.assert(isRetryableBlocked({ status: 'blocked', blockedBy: code, publishedAtMs: now }, now), code + ' en ventana debe reintentarse');
  console.assert(!isRetryableBlocked({ status: 'blocked', blockedBy: code, publishedAtMs: now - 31*60*1000 }, now), code + ' fuera de 30 min NO se reintenta');
}
console.assert(!isRetryableBlocked({ status: 'blocked', blockedBy: 'duplicate', publishedAtMs: now }, now), 'duplicado NO se reintenta');
console.assert(!isRetryableBlocked({ status: 'blocked', blockedBy: 'no_api_key', publishedAtMs: now }, now), 'sin API key NO se reintenta (lo arregla el usuario)');
console.assert(!isRetryableBlocked({ status: 'blocked', blockedBy: 'not_published_yet', publishedAtMs: now }, now), 'aún no publicado NO se reintenta como bloqueo (lo retoma la cola en nube)');
// --- v1.2.0: duplicado POR TENANT (decisión 7) ---
console.assert(submissionKey({ tenant: 'seba', postUrl: base.postUrl, campaignId: base.campaignId }) !== submissionKey({ tenant: 'kaez', postUrl: base.postUrl, campaignId: base.campaignId }), 'la clave incluye el tenant');
const sebaSubs = [{ postUrl: base.postUrl, campaignId: base.campaignId, tenant: 'seba', status: 'submitted' }];
console.assert(alreadySubmitted(sebaSubs, { tenant: 'seba', postUrl: base.postUrl, campaignId: base.campaignId }), 'mismo tenant + mismo post = duplicado');
console.assert(!alreadySubmitted(sebaSubs, { tenant: 'kaez', postUrl: base.postUrl, campaignId: base.campaignId }), 'clip de seba NO bloquea uno de kaez');
console.assert(!validateSubmission(sebaSubs, { tenant: 'kaez', ...base }).ok === false, 'envío de kaez con el mismo post no debe bloquearse por el de seba');
// --- v1.2.0: auto-envío (decisión 5) ---
console.assert(validateSubmission([], { tenant: 'seba', ...base, confirmed: false, auto: true }).ok, 'auto-envío pasa sin click manual (la asignación es la confirmación)');
console.assert(!validateSubmission([], { tenant: 'seba', ...base, confirmed: false, auto: false }).ok, 'envío manual sin confirmación sigue bloqueándose');
// --- v1.2.0: worker parado bloquea y es reintentable (decisión 6) ---
const blockedDown = { status: 'blocked', blockedBy: 'worker_down', publishedAtMs: now, postUrl: base.postUrl, campaignId: base.campaignId };
console.assert(isRetryableBlocked(blockedDown, now), 'bloqueado por worker parado y en ventana debe reintentarse');
console.assert(!isRetryableBlocked({ ...blockedDown, publishedAtMs: now - 31*60*1000 }, now), 'bloqueado fuera de 30 min NO se reintenta');
console.assert(!isRetryableBlocked({ ...blockedDown, blockedBy: 'duplicate_d1' }, now), 'bloqueado por duplicado NO se reintenta');
// --- multi-marketplace (2026-11-23 18:48): UN clip = UN marketplace ---
console.assert(MARKETPLACES.length === 11, 'registro: 11 marketplaces verificados 23 nov');
console.assert(MARKETPLACES.filter((m) => m.api === 'mcp').length === 1 && MARKETPLACES.find((m) => m.api === 'mcp').id === 'content_rewards', 'solo Content Rewards tiene API/MCP; el resto es solo web, envío manual');
console.assert(netRateForMarketplace(2.0, 'content_rewards') === 1.8, 'CR: $2,00 bruto = $1,80 neto');
console.assert(netRateForMarketplace(1.0, 'clipster') === 0.95, 'Clipster: $1,00 bruto = $0,95 neto (5% al retirar) -> bajo el piso');
console.assert(!meetsNetFloorForMarketplace(1.0, 'clipster'), 'Clipster $1,00 no pasa el piso neto');
console.assert(netRateForMarketplace(2.0, 'quantyclips') === null, 'QuantyClips: comisión no publicada -> neto no verificable (null)');
console.assert(!meetsNetFloorForMarketplace(2.0, 'quantyclips'), 'sin comisión publicada no se puede verificar el piso: fuera');
console.assert(CLIPSTER_GTA6_CAMPAIGNS.length === 7, 'Clipster GTA 6: 7 campañas vistas en ClipCPM 23 nov');
console.assert(rankMarketplaceCampaigns(CLIPSTER_GTA6_CAMPAIGNS).length === 0, 'ninguna campaña Clipster GTA 6 pasa el piso neto (todas $1,00 o menos, música, sin streamer famoso)');
console.assert(CANDIDATE_CAMPAIGNS_NO_ID.length === 3 && CANDIDATE_CAMPAIGNS_NO_ID.every((c) => !c.id), 'candidatas CR sin UUID: no se inventa el id, se toma de Discover/MCP');
const multiEntries = KNOWN_CAMPAIGNS.map((c) => ({ marketplace: 'content_rewards', ...c, rate: c.rate }));
console.assert(rankMarketplaceCampaigns(multiEntries, { today: '2026-11-23' }).map((e) => e.streamer).join(',') === 'Dasgasd3m,Dylan Page,ArthurTV', 'ranking multi-marketplace: Dasgasd3m > Dylan > ArthurTV (Bobby fuera por fama, BenDaDonnn por piso)');
console.assert(selectMarketplaceForClip(multiEntries, { clipStreamer: 'Dasgasd3m' }).entry.marketplace === 'content_rewards', 'clip de Dasgasd3m -> Content Rewards');
console.assert(selectMarketplaceForClip(multiEntries, { clipStreamer: 'Bobby Van Riper' }).reason === 'no_match', 'clip de Bobby no se asigna a ningún marketplace (no famoso)');
console.assert(selectMarketplaceForClip(multiEntries, { clipStreamer: null }).reason === 'sin_streamer', 'sin streamer no se asigna marketplace');
console.assert(marketplaceAssignmentKey({ tenant: 'kaez', clipId: 174 }) === 'kaez|174', 'clave marketplace por tenant+clip (sin plataforma)');
console.assert(alreadyAssignedMarketplace({ 174: { marketplace: 'clipster' } }, { tenant: 'seba', clipId: 174 }) === 'clipster', 'clip ya asignado a Clipster no se re-asigna');
console.assert(alreadyAssignedMarketplace({}, { tenant: 'seba', clipId: 174 }) === null, 'clip sin asignar -> null');
console.assert(alreadyAssignedMarketplace({ 'kaez|174': { marketplace: 'content_rewards' } }, { tenant: 'kaez', clipId: 174 }) === 'content_rewards', 'asignación por clave tenant|clip');
// --- analíticas (2026-11-23 18:48): totales desde D1, nunca inventados ---
const aRows = [
  { tenant_id: 'seba', clip_id: 174, platform: 'tiktok', marketplace: 'content_rewards', campaign_id: 'x', campaign_name: 'Dasgasd3m', status: 'submitted', views: 50000, amount_usd: 90 },
  { tenant_id: 'seba', clip_id: 175, platform: 'youtube', marketplace: 'content_rewards', campaign_id: 'x', campaign_name: 'Dasgasd3m', status: 'submitted', views: null, amount_usd: null },
  { tenant_id: 'seba', clip_id: 176, platform: 'tiktok', marketplace: 'content_rewards', campaign_id: 'y', campaign_name: 'Bobby', status: 'pending', views: null, amount_usd: null },
  { tenant_id: 'seba', clip_id: 177, platform: 'tiktok', marketplace: 'content_rewards', campaign_id: 'y', campaign_name: 'Bobby', status: 'failed', views: 0, amount_usd: 0 },
  { tenant_id: 'seba', clip_id: 178, platform: 'tiktok', marketplace: 'content_rewards', campaign_id: 'y', campaign_name: 'Bobby', status: 'expired', views: 0, amount_usd: 0 },
];
const aSum = summarizeAnalytics(aRows);
console.assert(aSum.available && aSum.rows === 5, 'analíticas: 5 filas');
console.assert(aSum.sent === 2 && aSum.pending === 1 && aSum.failed === 1 && aSum.expired === 1, 'analíticas: enviados/pendientes/fallidos/caducados');
console.assert(aSum.withViews === 1 && aSum.approvedViews === 50000, 'analíticas: vistas aprobadas 50.000, 1 enviado con vistas');
console.assert(aSum.totalEarnedUsd === 90, 'analíticas: total ganado $90 (null suma como 0, sin inventar)');
console.assert(aSum.unwithdrawnUsd === null && aSum.withdrawnUsd === null, 'analíticas: sin retirar/retirado = null (no disponible por API, ver en la web)');
console.assert(aSum.byCampaign.length === 2 && aSum.byCampaign[0].campaignName === 'Dasgasd3m' && aSum.byCampaign[0].amountUsd === 90, 'analíticas: por campaña, Dasgasd3m primero ($90)');
console.assert(summarizeAnalytics(null).available === false, 'analíticas: sin filas D1 -> sin datos todavía, no ceros');
console.assert(summarizeAnalytics([]).available === true && summarizeAnalytics([]).totalEarnedUsd === 0, 'analíticas: tabla vacía (0 filas reales) sí es disponible con ceros reales');
const aAll = summarizeAllAnalytics({ seba: aRows, kaez: null });
console.assert(aAll.available && aAll.total.sent === 2 && aAll.tenants.kaez.available === false, 'analíticas por tenant: seba con datos, kaez sin datos todavía');
console.assert(summarizeAllAnalytics({ seba: null, kaez: null }).available === false, 'analíticas: sin ningún tenant -> sin datos todavía');
console.assert(formatUsd(90) === '\$90.00' && formatUsd(null) === '—', 'formatUsd: número o —, nunca inventado');
// listAllSubmissions con stub: GET sin clip_id devuelve todas las filas; 404 -> no disponible.
const stubAnalytics = async (url) => {
  if (String(url).endsWith('/api/campaign-submissions')) return { ok: true, status: 200, json: async () => ({ ok: true, submissions: aRows }) };
  throw new Error('URL no esperada: ' + url);
};
const allRes = await listAllSubmissions('https://avc.vikingosfoodspa.workers.dev', 'tok', stubAnalytics);
console.assert(allRes.available && allRes.rows.length === 5, 'listAllSubmissions: 5 filas del Worker');
const stub404 = async () => ({ ok: false, status: 404, json: async () => ({}) });
console.assert((await listAllSubmissions('https://avc.vikingosfoodspa.workers.dev', 'tok', stub404)).available === false, 'listAllSubmissions: 404 -> no disponible (ruta aún no desplegada)');
console.assert((await listAllSubmissions('https://avc.vikingosfoodspa.workers.dev', null)).available === false, 'listAllSubmissions: sin token -> no disponible');
if (__assertFails) { console.error('ASSERTS FALLIDOS: ' + __assertFails); process.exit(1); }
console.assert(AUTO_SETUP_URL === 'https://avc-em9.pages.dev/extension/auto-setup.json', 'auto-setup: URL web del panel');
console.assert(parseAutoSetup({ version: '1.3.0', campaigns: [{}, {}], rules: {} }).ok, 'auto-setup valido pasa');
console.assert(!parseAutoSetup(null).ok && !parseAutoSetup({ version: '1.3.0' }).ok, 'auto-setup incompleto se rechaza');
console.log('Reglas OK: v1.3.0 (puerta corregida, ranking neto+fama, brief, MCP, asignación por plataforma, reintentos) + v1.2.0 intactas');
"
echo "== archivos =="
ls -l
echo "Validación completa. Para carga real: chrome://extensions > Modo desarrollador > Cargar descomprimida > esta carpeta."
