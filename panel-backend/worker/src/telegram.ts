// Telegram (2026-11-18): resumen diario de analíticas DESDE LA WEB.
// El Worker envía a Telegram con la Bot API; Muse nunca interviene.
// Secreto global del bot: TELEGRAM_BOT_TOKEN (wrangler secret put,
// nunca en el repo ni en wrangler.toml). Sin él, no se envía nada.
// El token NUNCA se registra en logs ni se devuelve en ninguna
// respuesta.
//
// v2 multi-tenant (2026-11-18): la v1 guardaba UN solo chat global
// (TELEGRAM_CHAT_ID) y solo enviaba a sebastian: si otro tenant
// entraba al mismo bot, podía recibir datos ajenos. En v2 cada chat
// de Telegram se vincula a UN tenant con un código de un solo uso
// generado DENTRO del panel con sesión iniciada (tablas
// telegram_links / telegram_link_codes, migración 0023). El código
// nunca se guarda en claro, solo su hash SHA-256, caduca a los 10
// minutos y se borra al usarse. NUNCA se pide la contraseña del
// panel dentro de Telegram: un chat de Telegram no es lugar para
// credenciales y un bot falso podría robarlas.
//
// Regla dura (igual que /api/analytics): solo mediciones REALES
// en clip_stats; sin medición se escribe "sin medición", nunca 0
// ni un número inventado. La medición es AUTOMÁTICA (el Worker
// mide solo cada 6 h por API; ver worker/src/publish/stats.ts) y
// el registro manual en el panel queda solo como RESPALDO cuando
// la automática falla. El resumen enseña por canal el estado de
// esa medición automática (auto_status), nunca "pega a mano" como
// camino normal: si no hay datos, el motivo exacto está en el
// mensaje (sin permiso, sin credencial, error o aún no intentada).

import type { Env } from "./cloudflare";

/** Límite de Telegram por mensaje: 4096 caracteres. Se deja margen. */
export const TELEGRAM_MAX_CHARS = 4000;

/** Límite del aviso del vigilante por POST /api/bridge/notify
 *  (2026-11-19): el cerebro manda su diagnóstico como texto
 *  plano; más de 2000 caracteres se recorta, nunca se rechaza
 *  por largo salvo mensaje vacío. */
export const BRIDGE_NOTIFY_MAX_CHARS = 2000;

/** Clave en settings (D1) con el último día enviado (YYYY-MM-DD
 *  America/Santiago): el cron corre cada minuto, así que sin esta
 *  marca el resumen se reenviaría 60 veces en la hora de las 09:00.
 *  v2: la marca vive en telegram_links.last_sent_day; esta clave se
 *  conserva solo como compatibilidad con la v1. */
export const TELEGRAM_SENT_DAY_KEY = "telegram_analytics_sent_day";

/** Validez del código de vinculación: 10 minutos, un solo uso. */
export const LINK_CODE_TTL_MS = 10 * 60 * 1000;
export const LINK_CODE_LENGTH = 6;

/** Validez de la confirmación pendiente de /parar: 5 minutos, un
 *  solo uso (tabla telegram_stop_confirmations, migración 0026). */
export const STOP_CONFIRM_TTL_MS = 5 * 60 * 1000;

/** Clave en settings (D1) con el último estado conocido del Worker
 *  (1 en marcha, 0 parado) por tenant: el cron la compara con el
 *  estado real y solo avisa en la transición 1→0. Se guarda con
 *  getSettings/saveSettings tal cual; effectiveSettings conserva
 *  las claves desconocidas, así que el panel no la borra al
 *  guardar Ajustes. Sin marca (primera vez), se inicializa en
 *  silencio: no se avisa de un estado que ya era así. */
export const TELEGRAM_WORKER_LAST_RUNNING_KEY = "telegram_worker_last_running";

/** Clave en settings (D1) con el instante (ms epoch) de la última
 *  alerta de Worker atascado enviada por Telegram. El cron la
 *  compara y repite como mucho 1 vez cada 6 h mientras siga
 *  atascado; cuando el validador vuelve a ok se pone a 0, así el
 *  próximo atasco vuelve a avisar. Sin marca (primera vez), avisa. */
export const TELEGRAM_WORKER_STUCK_ALERT_KEY = "telegram_worker_stuck_alerted_at";

/** Estado de la medición automática de un canal (tabla
 *  analytics_status, migración 0028): lo expone /api/analytics y
 *  lo enseña /resumen. Sin fila, null = "aún no se ha intentado
 *  medir". */
export interface AnalyticsAutoStatus {
  status: string;
  reason: string;
  last_measured_at: number | null;
}

/** Resumen por CANAL (plataforma+idioma) tal como lo devuelve
 *  /api/analytics. Un canal es YouTube ES, YouTube EN, etc.: 8 en
 *  total, cada uno con SU handle real (tabla accounts, migración
 *  0030) y SU estado de medición automática. `language` ausente =
 *  agregado histórico por plataforma (compatibilidad con los
 *  resúmenes viejos de 4 plataformas); el código nuevo siempre lo
 *  pone. Sin handle verificado, `handle` es null y el mensaje dice
 *  "sin handle registrado": nunca se inventa ni se hardcodea uno. */
export interface AnalyticsPlatformSummary {
  platform: string;
  language?: string;
  handle?: string | null;
  median_views: number | null;
  median_retention: number | null;
  measured_clips: number;
  published_clips: number;
  has_data: boolean;
  auto_status?: AnalyticsAutoStatus | null;
  ranking: { title: string | null; views: number | null; retention_pct: number | null }[];
}

const PLATFORM_LABEL: Record<string, string> = {
  youtube: "YouTube",
  tiktok: "TikTok",
  instagram: "Instagram",
  facebook: "Facebook",
};

const LANGUAGE_LABEL: Record<string, string> = {
  es: "ES",
  en: "EN",
};

/** Etiqueta legible del estado de medición automática. */
export function autoStatusLabel(status: string): string {
  switch (status) {
    case "ok":
      return "al día";
    case "sin_credencial":
      return "sin credencial";
    case "sin_permiso":
      return "sin permiso";
    case "error":
      return "error";
    default:
      return status;
  }
}

/** Línea de medición automática de un canal: qué pasó en el
 *  último intento y por qué no hay datos, con el motivo literal
 *  recortado (el motivo completo está en el panel: Analíticas).
 *  Nunca dice "pega a mano": el registro manual es solo respaldo. */
export function autoStatusLine(p: AnalyticsPlatformSummary): string {
  const st = p.auto_status ?? null;
  if (!st) return "Medición automática: aún no intentada — se mide sola cada 6 h.";
  if (st.status === "ok") return "Medición automática: al día.";
  const reason = (st.reason ?? "").trim().replace(/\s+/g, " ");
  const short = reason.length > 160 ? `${reason.slice(0, 157)}…` : reason;
  return short
    ? `Medición automática: ${autoStatusLabel(st.status)} — ${short}`
    : `Medición automática: ${autoStatusLabel(st.status)}.`;
}

/** ¿Está configurado el bot? Solo exige el token: el chat ya no es
 *  un secreto global, se vincula por tenant (v2). */
export function botConfigured(env: Env): boolean {
  return !!(env.TELEGRAM_BOT_TOKEN || "").trim();
}

/** Nombre histórico de v1 (exigía además TELEGRAM_CHAT_ID, el chat
 *  único global, eliminado en v2). Se mantiene para no tocar las
 *  llamadas que solo preguntan "¿puede Telegram enviar?". */
export function telegramConfigured(env: Env): boolean {
  return botConfigured(env);
}

/** Día/hora en America/Santiago para una fecha dada. */
export function santiagoParts(date: Date): { day: string; hour: number; minute: number } {
  const parts = new Map(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/Santiago",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(date)
      .map((p) => [p.type, p.value] as const),
  );
  return {
    day: `${parts.get("year")}-${parts.get("month")}-${parts.get("day")}`,
    hour: Number(parts.get("hour")),
    minute: Number(parts.get("minute")),
  };
}

function fmtViews(n: number | null): string {
  if (n === null) return "sin medición";
  return Math.round(n).toLocaleString("es-CL");
}

function fmtRetention(n: number | null): string {
  if (n === null) return "sin medición";
  const r = Math.round(n * 10) / 10;
  return `${String(r).replace(".", ",")} %`;
}

/** Mensaje diario, en español, ≤4000 caracteres. Por CANAL
 *  (plataforma+idioma, 8): handle real de la cuenta, publicados,
 *  medidos, mediana de vistas / retención (o "sin medición") y el
 *  top clip con título + vistas, más la línea de medición
 *  automática con el motivo exacto cuando no hay datos. Sin datos
 *  en ningún canal, lo dice claro en vez de inventar. Nunca
 *  hardcodea un handle ni manda a pegar las vistas a mano. */
export function buildAnalyticsMessage(platforms: AnalyticsPlatformSummary[], dayKey: string): string {
  const lines: string[] = [`📊 Analíticas GTA 6 — ${dayKey}`, ""];
  let anyPublished = false;
  let anyMeasured = false;
  // Agrupar por idioma (ES primero, EN después); las entradas sin
  // idioma (agregados viejos) van al final sin encabezado de idioma.
  const groups = new Map<string, AnalyticsPlatformSummary[]>();
  for (const p of platforms) {
    const key = p.language === "es" || p.language === "en" ? p.language : "";
    const list = groups.get(key) ?? [];
    list.push(p);
    groups.set(key, list);
  }
  const order = ["es", "en", ""].filter((k) => groups.has(k));
  for (const lang of order) {
    if (lang) lines.push(lang === "es" ? "ES · Español" : "EN · English");
    for (const p of groups.get(lang) ?? []) {
      anyPublished ||= p.published_clips > 0;
      anyMeasured ||= p.measured_clips > 0;
      const label = PLATFORM_LABEL[p.platform] ?? p.platform;
      const langSuffix = !lang && p.language ? ` ${LANGUAGE_LABEL[p.language] ?? p.language}` : "";
      const handle = (p.handle ?? "").trim();
      lines.push(`${label}${langSuffix} · ${handle ? handle : "sin handle registrado"}`);
      lines.push(`  Publicados: ${p.published_clips} · Medidos: ${p.measured_clips}`);
      if (p.measured_clips === 0) {
        lines.push("  Sin medición todavía.");
      } else {
        lines.push(`  Mediana vistas: ${fmtViews(p.median_views)} · Retención: ${fmtRetention(p.median_retention)}`);
        const top = p.ranking.find((r) => typeof r.views === "number");
        if (top) {
          const title = (top.title ?? "Clip sin título").trim() || "Clip sin título";
          lines.push(`  Top: «${title}» — ${fmtViews(top.views)} vistas`);
        }
      }
      lines.push(`  ${autoStatusLine(p)}`);
      lines.push("");
    }
  }
  if (!anyPublished && !anyMeasured) {
    lines.push("Todavía no hay publicaciones ni mediciones: el resumen llegará cuando haya datos.");
  }
  const text = lines.join("\n").trimEnd();
  if (text.length <= TELEGRAM_MAX_CHARS) return text;
  return `${text.slice(0, TELEGRAM_MAX_CHARS - 1)}…`;
}

/** Texto corto de prueba, solo cuando no hay publicaciones ni
 *  mediciones: distingue "el bot funciona" de "hay analíticas". */
export function buildTestMessage(dayKey: string, hour: number, minute: number): string {
  const hh = String(hour).padStart(2, "0");
  const mm = String(minute).padStart(2, "0");
  return (
    `🔔 Prueba Telegram AVC — ${dayKey} ${hh}:${mm} (Santiago)\n` +
    "El bot funciona. Todavía no hay publicaciones ni mediciones en el panel."
  );
}

/** Detecta un fallo por límite de frecuencia en last_error:
 *  es el caso B5 del informe: no se reintenta, se espera. */
export function isRateLimitText(text: string): boolean {
  return /frecuencia|rate limit|429/.test(text);
}

/** Lista de comandos de la v3 (para /ayuda y para el chat no
 *  vinculado que ya está vinculado: qué puede pedir). */
export function helpMessage(): string {
  return (
    "Comandos del bot:\n" +
    "/resumen — analíticas de tus canales, cuando quieras\n" +
    "/cola — cuántos vídeos hay en cola y los 5 primeros\n" +
    "/top — a cuáles les ha ido mejor (solo medidos)\n" +
    "/estado — ¿está todo andando?\n" +
    "/primero <id> — ese clip, primero de su cola\n" +
    "/subir <id> y /bajar <id> — mover un clip un puesto\n" +
    "/auto <id> — ese clip vuelve al orden automático\n" +
    "/iniciar — encender el Worker (la cola avanza sola)\n" +
    "/parar — parar el Worker (pide confirmar con SÍ)\n" +
    "/ayuda — esta lista\n" +
    "/unlink — dejar de recibir analíticas\n" +
    "Cada día a las 09:00 (Santiago) llega el resumen solo."
  );
}

/** Pregunta de confirmación de /parar: detener el Worker para
 *  TODA la publicación, así que nunca se ejecuta en un solo
 *  mensaje. La respuesta válida es SÍ (o /parar sí) en el mismo
 *  chat antes de 5 minutos; cualquier otro texto cancela. */
export function stopConfirmMessage(): string {
  return (
    "⚠️ ¿Seguro? Esto detiene TODA la publicación: la cola queda en pausa hasta que escribas /iniciar.\n" +
    "Responde SÍ para confirmar (vale 5 minutos). Cualquier otra cosa cancela la parada."
  );
}

/** ¿Es este texto un "SÍ" a la pregunta de /parar? Se acepta con
 *  o sin acento, en mayúsculas o minúsculas, y nada más: un "sí,
 *  pero..." no es confirmación. Sin confirmación pendiente, un SÍ
 *  suelto no hace nada. */
export function isStopConfirmation(text: string): boolean {
  const norm = text.trim().toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  return norm === "si";
}

/** Alerta de desactivación inesperada (v3.1): solo dice que se
 *  desactivó y a qué hora (Santiago). No inventa la causa: puede
 *  ser el panel, otro usuario o un error. */
export function buildWorkerStoppedAlertMessage(whenMs: number): string {
  const when = new Date(whenMs).toLocaleString("es-CL", {
    timeZone: "America/Santiago",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  return (
    `⚠️ El Worker se desactivó a las ${when} (Santiago).\n` +
    "La cola queda en pausa: nada se publica hasta que lo enciendas con /iniciar. " +
    "Si no fuiste tú, revisa el panel: Ajustes y Tracking."
  );
}

/** Alerta de Worker atascado (validador de suministro, 2026-11-18):
 *  dice que está en marcha pero no llegan clips, con el motivo
 *  exacto del validador (horas sin clip, dispatch, streamers).
 *  `reason` ya viene redactado por validateWorkerSupply; aquí solo
 *  se le pone la hora y el qué hacer. Nunca inventa la causa.
 *  Desde 2026-11-19 el vigilante es quien diagnostica: si trae
 *  `watchdogDiagnosis` (último diagnóstico del vigilante leído de
 *  worker_events, kind "watchdog"), se incluye tal cual tras el
 *  motivo del validador. Sin diagnóstico no se inventa ninguno. */
export function buildWorkerStuckAlertMessage(reason: string, whenMs: number, watchdogDiagnosis?: string): string {
  const when = new Date(whenMs).toLocaleString("es-CL", {
    timeZone: "America/Santiago",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  const diagnosis = (watchdogDiagnosis ?? "").trim();
  const text =
    `⚠️ Worker atascado a las ${when} (Santiago).\n` +
    `${reason}\n` +
    (diagnosis ? `Diagnóstico del vigilante: ${diagnosis}\n` : "") +
    "Revisa el panel: Worker → Actividad y Tracking. /estado para ver si sigue así.";
  return text.length <= TELEGRAM_MAX_CHARS ? text : `${text.slice(0, TELEGRAM_MAX_CHARS - 1)}…`;
}

/** Comando que entiende el webhook (v3): el nombre, en minúsculas
 *  y sin el sufijo @bot, y el argumento tal cual. /top admite un
 *  número opcional; /primero, /subir, /bajar y /auto exigen un id.
 *  El texto libre no es comando. */
export interface TelegramCommand {
  name: string;
  arg: string;
}

export function parseTelegramCommand(text: string): TelegramCommand | null {
  const m = /^\/([a-záéíóú]+)(?:@\S+)?(?:\s+(.*))?$/.exec(text.trim().toLowerCase());
  if (!m) return null;
  const known = ["resumen", "cola", "top", "estado", "ayuda", "primero", "subir", "bajar", "auto", "iniciar", "parar", "unlink", "link", "start"];
  const name = m[1]!;
  if (!known.includes(name)) return null;
  return { name, arg: (m[2] ?? "").trim() };
}

/** Vista mínima de un clip de la Cola para formatear /cola: solo
 *  los campos que el mensaje enseña, sin credenciales ni R2. */
export interface QueueClipView {
  id: number;
  title: string;
  score: number;
  origin: string;
  language: string;
  preview_seen: number;
  publish_blocked: number;
  viral: string;
  manual_rank?: number | null;
}

function fmtDecimal(n: number): string {
  return String(Math.round(n * 10) / 10).replace(".", ",");
}

/** Parsea clips.viral (JSON) sin depender de db.ts: si está vacío
 *  o roto, {}. Solo se leen viral_score y seo_scores. */
function parseViralJsonLocal(raw: string | null | undefined): {
  viral_score?: number;
  seo_scores?: Partial<Record<string, number>>;
} {
  if (!raw) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
    return parsed as { viral_score?: number; seo_scores?: Partial<Record<string, number>> };
  } catch {
    return {};
  }
}

function queueClipLine(c: QueueClipView, position: number): string {
  const viral = parseViralJsonLocal(c.viral);
  const title = (c.title ?? "").trim().slice(0, 40) || "Clip sin título";
  const parts: string[] = [`${position}. #${c.id} «${title}»`];
  parts.push(c.origin === "live" ? "LIVE" : "VOD");
  parts.push(`score ${c.score}`);
  if (typeof viral.viral_score === "number") parts.push(`viral ${fmtDecimal(viral.viral_score)}`);
  const seoScores = Object.values(viral.seo_scores ?? {}).filter((v): v is number => typeof v === "number");
  if (seoScores.length) parts.push(`SEO ${Math.max(...seoScores)}`);
  if (typeof c.manual_rank === "number") parts.push("📌 orden manual");
  if (c.preview_seen !== 1) parts.push("⚠️ falta preview");
  if (c.publish_blocked === 1) parts.push("⚠️ sin subtítulos");
  return parts.join(" · ");
}

/** /cola (v3, diseño §4.3): cuántos hay, LIVE/VOD, los 5 primeros
 *  con score, viralidad, SEO y freno visible. Sin clips, lo dice. */
export function buildQueueMessage(clips: QueueClipView[]): string {
  if (clips.length === 0) return "🎬 Cola vacía. No hay clips pendientes de publicar.";
  const live = clips.filter((c) => c.origin === "live").length;
  const lines: string[] = [`🎬 Cola: ${clips.length} clips (LIVE ${live} · VOD ${clips.length - live})`];
  for (let i = 0; i < Math.min(5, clips.length); i++) lines.push(queueClipLine(clips[i]!, i + 1));
  if (clips.length > 5) lines.push(`Y ${clips.length - 5} más en cola. Ver en el panel: https://avc-em9.pages.dev`);
  const text = lines.join("\n");
  return text.length <= TELEGRAM_MAX_CHARS ? text : `${text.slice(0, TELEGRAM_MAX_CHARS - 1)}…`;
}

/** /top (v3): ranking de clips medidos por vistas, todas las
 *  plataformas juntas. Sin medición en ningún lado, lo dice en vez
 *  de inventar un ranking. */
export function buildTopMessage(platforms: AnalyticsPlatformSummary[], limit = 5): string {
  const rows = platforms
    .flatMap((p) =>
      p.ranking
        .filter((r) => typeof r.views === "number")
        .map((r) => ({
          title: r.title,
          views: r.views as number,
          platform: `${PLATFORM_LABEL[p.platform] ?? p.platform}${p.language ? ` ${LANGUAGE_LABEL[p.language] ?? p.language}` : ""}`,
        })),
    )
    .sort((a, b) => b.views - a.views)
    .slice(0, Math.max(1, Math.min(10, limit)));
  if (rows.length === 0) {
    return "🏆 Top por vistas (medido)\nSin medición todavía — la medición automática aún no trae datos; el estado por plataforma está en el panel (Analíticas).";
  }
  const lines = ["🏆 Top por vistas (medido)"];
  rows.forEach((r, i) => {
    lines.push(`${i + 1}. «${((r.title ?? "Clip sin título").trim() || "Clip sin título").slice(0, 40)}» — ${r.platform} — ${fmtViews(r.views)} vistas`);
  });
  return lines.join("\n");
}

/** /estado (v3): Worker en marcha o parado, fallidos pendientes y
 *  el último evento, sin inventar nada. */
export function buildStatusMessage(input: {
  running: boolean;
  startedAt: number | null;
  lastNote: string;
  failedCount: number;
  lastEvent: { at: number; kind: string; message: string } | null;
  tenant: string;
  /** auto_publish_top del tenant (2026-11-19, aceptación automática):
   *  true = los clips que pasan los umbrales se publican solos;
   *  false = esperan aprobación manual; null = no se pudo leer
   *  el ajuste (la línea no se muestra, nunca se inventa). */
  autoPublish: boolean | null;
}): string {
  const lines: string[] = [input.running ? "🟢 Worker en marcha" : "🔴 Worker parado"];
  if (input.running && input.startedAt) {
    lines.push(`Iniciado: ${new Date(input.startedAt).toLocaleString("es-CL", { timeZone: "America/Santiago" })} (Santiago)`);
  }
  if (input.lastNote) lines.push(`Nota: ${input.lastNote.slice(0, 200)}`);
  if (input.autoPublish === true) {
    lines.push("Aceptación automática: activada — los clips que pasan los umbrales se publican solos, sin esperar aprobación.");
  } else if (input.autoPublish === false) {
    lines.push("Aceptación automática: desactivada — los clips esperan aprobación manual.");
  }
  lines.push(`Fallidos pendientes: ${input.failedCount}`);
  if (input.lastEvent) {
    lines.push(`Último evento: ${input.lastEvent.kind} — ${input.lastEvent.message.slice(0, 200)}`);
  }
  lines.push(`Telegram vinculado a ${input.tenant}. Comandos: /ayuda`);
  return lines.join("\n");
}

// --- Vinculación por código (v2) ----------------------------------------

/** Código de 6 dígitos, con ceros a la izquierda permitidos. */
export function generateLinkCode(): string {
  const bytes = new Uint8Array(LINK_CODE_LENGTH);
  crypto.getRandomValues(bytes);
  return [...bytes].map((b) => String(b % 10)).join("");
}

/** Hash SHA-256 en hex del código: es lo único que se guarda en D1.
 *  El código en claro solo existe en la respuesta al panel (sesión)
 *  y en el mensaje que el usuario escribe al bot. */
export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Chat enmascarado para el estado: solo los últimos 4 dígitos. */
export function maskChatId(chatId: string): string {
  const digits = chatId.replace(/[^0-9]/g, "");
  return digits.length > 4 ? `•••${digits.slice(-4)}` : "•••";
}

/** Actualización entrante del webhook: solo importan chat y texto. */
export interface TelegramUpdate {
  chatId: string;
  text: string;
}

export function parseTelegramUpdate(body: unknown): TelegramUpdate | null {
  if (!body || typeof body !== "object") return null;
  const msg = (body as { message?: unknown }).message;
  if (!msg || typeof msg !== "object") return null;
  const chat = (msg as { chat?: unknown }).chat;
  const text = (msg as { text?: unknown }).text;
  if (!chat || typeof chat !== "object") return null;
  const id = (chat as { id?: unknown }).id;
  if (typeof id !== "number" && typeof id !== "string") return null;
  return { chatId: String(id), text: typeof text === "string" ? text : "" };
}

/** "/link 123456" o "/start 123456" (deep-link de Telegram). */
export function parseLinkCode(text: string): string | null {
  const m = /^\/(?:link|start)\s+(\d{6})\s*$/.exec(text.trim());
  return m ? m[1]! : null;
}

/** Lo único que recibe un chat NO vinculado: instrucciones, nunca
 *  analíticas ni datos de nadie. La contraseña del panel no se pide
 *  jamás por Telegram. */
export function linkInstructionsMessage(): string {
  return (
    "Para recibir TUS analíticas, vincula este chat desde tu panel: entra a https://avc-em9.pages.dev, " +
    "inicia sesión, ve a Ajustes → Telegram y pulsa «Generar código». Después escribe aquí /link <código>. " +
    "Nunca te pediré tu contraseña por Telegram."
  );
}

export function linkedMessage(tenant: string): string {
  return (
    `Vinculado: a partir de ahora recibirás aquí el resumen diario de analíticas de tu cuenta (${tenant}), ` +
    "cada día a las 09:00 (Santiago). Para desvincular, escribe /unlink."
  );
}

export function unlinkedMessage(): string {
  return "Desvinculado: ya no recibirás analíticas en este chat. Puedes volver a vincularlo cuando quieras desde Ajustes → Telegram en tu panel.";
}

/** Envía un texto por la Bot API a UN chat concreto. `fetchFn`
 *  existe solo para los tests (en producción es el fetch global
 *  del Worker). Nunca registra el token: ni en el error ni en
 *  ningún log. El chat_id va en el cuerpo, nunca en la URL. */
export async function sendTelegram(
  env: Env,
  chatId: string,
  text: string,
  fetchFn: typeof fetch = fetch,
): Promise<{ ok: boolean; status: number; error?: string }> {
  const token = (env.TELEGRAM_BOT_TOKEN || "").trim();
  if (!token || !chatId.trim()) return { ok: false, status: 0, error: "Telegram no configurado." };
  try {
    const res = await fetchFn(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text }),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      return {
        ok: false,
        status: res.status,
        error: `Telegram respondió ${res.status}${body ? `: ${body.slice(0, 200)}` : ""}`,
      };
    }
    return { ok: true, status: res.status };
  } catch (e) {
    return { ok: false, status: 0, error: e instanceof Error ? e.message : String(e) };
  }
}
