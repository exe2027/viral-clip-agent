// Gates de publicación (movidos desde jobs.ts 2026-11-18, hallazgo M1):
// publishGate vive aquí para que tanto jobs.ts (enqueue/ciclo) como
// publish/index.ts (dispatch, re-chequeo en ejecución) lo usen sin
// importación circular. La regla NO cambia: mismos motivos, mismos
// topes. El re-chequeo en ejecución (recheckPublishGate) lee el clip
// actual y los contadores frescos por plataforma+idioma.

import type { D1Database, Env } from "./cloudflare";
import type { Tenant } from "./auth";
import {
  accountPausedUntilMs,
  clipLanguage,
  effectiveSettings,
  getApiAccountByPlatform,
  getClip,
  getInfluencer,
  getSettings,
  isGtaViGame,
  isRestDayEffective,
  isWithinPublishSlot,
  languageName,
  logWorkerEvent,
  maturityOut,
  nextPlatformHourMs,
  nextSlotStartMs,
  panelPlatformLabel,
  parseViralJson,
  platformMax,
  platformPlan,
  PLATFORM_SCHEDULE,
  santiagoDayKeyMs,
  santiagoWallToUtcMs,
  slotWindowBoundsMs,
  spacingHoursFor,
  toApiPlatform,
  type ClipLanguage,
  type ClipRow,
  type PanelPlatform,
} from "./db";
import { qualityGateForClip } from "./quality-gate";
import { nextRetryDelayMs, retriesToday } from "./retry";

export type { ClipLanguage };

const nowMs = () => Date.now();

/** Día en America/Santiago (YYYY-MM-DD), para cupos e idempotencia. */
export function santiagoDayKey(d = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Santiago", year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}

// ---------------------------------------------------------------------------
// Gates de publicación (mismos que el panel v17 + repo limits.yaml).
// Devuelven null si pasa, o el motivo si frena (que queda en Tracking).
// ---------------------------------------------------------------------------
export interface GateContext {
  clip: ClipRow;
  seoScore: number | null; // null = sin medir (no bloquea por SEO, sí por resto)
  seoOverride: boolean;
  publishedToday: number;
  lastPublishedAtMs: number | null;
  streamerAllowed: boolean;
  settings: Record<string, unknown>;
  // Tope vigente por MADUREZ de la cuenta (calentamiento 0017,
  // reglas 2026-11-19): si viene, manda sobre el plan/techo de
  // Ajustes. Semana 1/2 = 1/día en todas las plataformas;
  // semana 3 = plan normal sin máx; establecida = plan y máx.
  // Sin cuenta (null) se usan los topes de Ajustes tal cual.
  accountDailyLimit?: number | null;
  accountDailyPlan?: number | null;
  /** Tope de CALENTAMIENTO (reglas 2026-11-19): cuando la cuenta
   *  NO es establecida, este es su tope duro (1/día en semana
   *  1/2, plan en semana 3). A diferencia del plan/techo, este
   *  freno NO lo salta la publicación manual: desde la orden
   *  09:25 (2026-11-21, SIN AMONTONAR) la manual no salta
   *  NINGÚN freno anti-baneo. null = establecida. */
  warmupDailyLimit?: number | null;
  /** Publicaciones de esta cuenta en las últimas 24 h móviles
   *  (rolling, no día calendario). Nunca >3 por cuenta (regla 2):
   *  frena SIEMPRE, también a mano. */
  publishedLast24h?: number;
  /** El mismo clip ya se publicó hoy en ESTA plataforma:
   *  nunca se repite el mismo clip en la misma cuenta el mismo
   *  día (regla 5). Frena siempre, también a mano. EXCEPTO
   *  republicación explícita (P0 #5, auditoría #11): con
   *  `republish: true` (la plataforma borró el vídeo, como el
   *  168 en FB) esta puerta se salta y el resto sigue frenando
   *  igual; el job nuevo lleva key |repubN (ver jobs.ts). */
  clipPublishedTodayOnPlatform?: boolean;
  /** El mismo clip ya se publicó en ESTA plataforma EN OTRO DÍA
   *  (F1, spec 2026-11-24): el gate solo miraba posted_day=hoy
   *  y por eso el 102 se duplicó en TikTok (jobs 132/133 en el
   *  mismo día, y un posted de otro día tampoco frenaba nada).
   *  Con `republish=true` se salta igual que la puerta de hoy.
   *  `clipLastPublishedOnPlatformDay` es el `posted_day` del
   *  posted más reciente en esa plataforma (para el motivo). */
  clipPublishedEverOnPlatform?: boolean;
  clipLastPublishedOnPlatformDay?: string | null;
  /** Republicación explícita (P0 #5, auditoría #11): el usuario
   *  (o el cerebro) pide publicar DE NUEVO el mismo clip en la
   *  misma plataforma el mismo día porque la plataforma borró
   *  el vídeo. Solo salta la puerta "mismo clip hoy"; el resto
   *  (franja, tope, espaciamiento, calentamiento, rolling,
   *  calidad) sigue frenando igual. El job nuevo lleva la key
   *  con sufijo |repubN (ver enqueuePublishJobs). */
  republish?: boolean;
  /** Última publicación de ESTE clip en OTRA plataforma (ms):
   *  mismo clip en distintas plataformas, mínimo 15 min entre
   *  una y otra (regla 4). Desde la orden 09:25 (2026-11-21,
   *  SIN AMONTONAR) frena TAMBIÉN a mano, no solo como aviso. */
  clipLastPublishedOtherPlatformMs?: number | null;
  /** Pausa por spam/límite (regla 6, 48 h, en settings
   *  account_pauses): ms epoch hasta el que está en pausa, o
   *  null. Frena SIEMPRE, también a mano: nunca se esquiva un
   *  límite cambiando de método. */
  pausedUntilMs?: number | null;
  /** Día libre semanal de la cuenta (regla 7, determinista):
   *  frena solo la publicación automática. La manual es una
   *  decisión humana explícita y puede publicar en el día libre
   *  (el día libre es planificación, no un freno de seguridad;
   *  documentado en el informe 2026-11-19). */
  isRestDay?: boolean;
  /** Comprobar la franja horaria (reglas 2026-11-19): la
   *  publicación solo sale 12:00-14:00, 18:00-20:00 o 21:00-23:00
   *  (hora Chile). Desde la orden 09:25 (2026-11-21, SIN
   *  AMONTONAR) la comprueba TAMBIÉN la manual: "Publicar ahora"
   *  fuera de franja queda frenado. Solo la pone quien encola;
   *  el re-chequeo en ejecución NO la comprueba (un job ya
   *  encolado dentro de franja no se queda varado por reanudarse
   *  fuera de ella, multi-step). */
  checkSlot?: boolean;
  /** Instante de referencia para franja/pausa/rolling (tests y
   *  llamadas con fecha congelada). Default: Date.now(). */
  nowMs?: number;
  /** Publicaciones de esta cuenta en ESTA plataforma+idioma
   *  dentro de la franja actual (regla 09:25, 2026-11-21, SIN
   *  AMONTONAR): como mucho 1 por plataforma y franja. Frena
   *  SIEMPRE, también a mano. undefined = quien llama no lo
   *  contó (tests viejos). */
  publishedThisSlot?: number;
  /** Publicación MANUAL (2026-11-19, botón "Publicar ahora"):
   *  decisión humana explícita. Desde la orden 09:25 del
   *  2026-11-21 (SIN AMONTONAR) la manual ya NO salta franja,
   *  tope diario, espaciamiento ni cross-platform: respeta
   *  TODAS las reglas anti-baneo igual que la automática. Desde
   *  el refuerzo del validador (2026-11-21) TAMPOCO salta el
   *  veredicto de calidad BLANDO (sin veredicto válido): un clip
   *  sin validar no se publica por ningún camino. Lo único que
   *  sigue saltando la manual es el día libre (planificación, no
   *  freno de seguridad). */
  manual?: boolean;
  // PUERTA DE IDIOMA (0019, DOS COLAS SEPARADAS 2026-11-18): el
  // idioma de la cuenta resuelta para esta plataforma.
  // - undefined = quien llama no resolvió cuenta: no se comprueba
  //   el idioma aquí (tests viejos y llamadas sin cuenta).
  // - null = no hay cuenta en el idioma del clip: bloqueado con
  //   "sin cuenta <plataforma> en <idioma>".
  // - "es" | "en" distinto del idioma del clip: bloqueado con
  //   "idioma: el clip es en X pero la cuenta es en Y".
  // accountReady=false = la cuenta existe pero no tiene credencial
  // verificada: bloqueado hasta pegarla y verificarla.
  accountLanguage?: ClipLanguage | null;
  accountReady?: boolean;
}

/** Tope diario EFECTIVO (el plan) para una plataforma, con los
 *  mismos números que publishGate: techo y plan POR PLATAFORMA
 *  (reglas 2026-11-19: TikTok 2/3, Instagram 1/2, YouTube 1/2,
 *  Facebook 1/2) y madurez de la cuenta (calentamiento). Extraído
 *  de publishGate (2026-11-18, migración 0022) para que la reserva
 *  de cupo (reservePublishSlot) y el gate nunca discrepen: si el
 *  gate frena en N, la reserva frena en N. */
export function effectiveDailyPlan(
  platform: PanelPlatform,
  settings: Record<string, unknown>,
  accountDailyLimit?: number | null,
  accountDailyPlan?: number | null,
): number {
  const s = effectiveSettings(settings ?? {});
  const maxBase = platformMax(s, toApiPlatform(platform));
  const planBase = platformPlan(s, toApiPlatform(platform));
  const max = accountDailyLimit !== undefined && accountDailyLimit !== null
    ? Math.min(accountDailyLimit, maxBase) : maxBase;
  return Math.min(
    accountDailyPlan !== undefined && accountDailyPlan !== null ? accountDailyPlan : planBase,
    planBase, max,
  );
}

export function publishGate(platform: PanelPlatform, g: GateContext): string | null {
  const clip = g.clip;
  if (clip.archived) return "clip archivado";
  // PUERTA DE IDIOMA (0019): un clip EN nunca se publica en un
  // canal ES y viceversa. Va antes que el resto de puertas: ni
  // siquiera se evalúa la duración o el SEO de un clip que no
  // puede salir por este canal. El motivo dice el idioma del clip
  // y el de la cuenta, para que el usuario sepa qué cuenta falta.
  const clipLang = clipLanguage(clip);
  // Ajustes del usuario (Ajustes → CONFIGURACIÓN, D1): duración,
  // SEO mínimo y topes diarios se leen de aquí, no de valores fijos.
  // Defaults: 1–60 s (sin mínimo desde 2026-11-21), puerta única
  // 45/80 (P0 #3), techo YT 6 / resto 3 (YT 6/día desde 2026-11-23).
  const s = effectiveSettings(g.settings ?? {});
  if (g.accountLanguage !== undefined) {
    const label = panelPlatformLabel(platform);
    if (g.accountLanguage === null) {
      return `sin cuenta ${label} en ${languageName(clipLang)}: crea la cuenta en Cuentas y pega su credencial`;
    }
    if (g.accountLanguage !== clipLang) {
      return `idioma: el clip es en ${languageName(clipLang)} pero la cuenta ${label} es en ${languageName(g.accountLanguage)}: nunca se publica en el canal del otro idioma`;
    }
    if (g.accountReady === false) {
      return `cuenta ${label} en ${languageName(clipLang)} sin credencial verificada: pega la credencial en Cuentas y verifícala`;
    }
  }
  // Regla del usuario 2026-11-17 22:10: SIEMPRE subtítulos,
  // COMPLETOS. Sin subtítulos el clip no se publica.
  // Rescate por gritos (2026-11-18): un clip rescatado nace
  // bloqueado y su freno tiene motivo propio, no el de subtítulos
  // (puede tenerlos completos y seguir bloqueado por viralidad
  // bajo el mínimo, puerta única 45/80 desde P0 #3).
  if (clip.publish_blocked === 1) {
    try {
      const viral = parseViralJson(clip.viral);
      // Retención por título (2026-11-26, spec títulos R3.4/I5):
      // el motivo guardado manda, para que el ciclo registre el
      // mismo motivo que muestra la píldora del panel.
      const hold = (viral.hold_reason ?? "").trim();
      if (hold) return `retenido para revisión: ${hold} — corrige el título/descripción y desbloquéalo en el Editor`;
      const rescue = viral.rescue;
      if (rescue) return `rescatado por gritos (viralidad ${rescue.viral_revisado} < ${s.min_virality}): bloqueado hasta revisión humana — desbloquéalo en el Editor`;
    } catch { /* sin viral medido: cae al freno de subtítulos */ }
  }
  if (clip.publish_blocked === 1) return "sin subtítulos completos: necesita transcripción humana";
  // PUERTA DE CALIDAD B4 (2026-11-20): ningún job nace para un
  // clip sin validar o defectuoso (ver quality-gate.ts: el
  // veredicto vive en viral.quality_gate). Va antes que duración y
  // SEO para que el motivo sea el real (un clip sin cara no es un
  // problema de SEO). Desde el refuerzo del validador (2026-11-21,
  // lección scooter: un clip no-rubius publicó por el panel sin
  // pasar por el gate de fotogramas), la puerta es DURA para
  // TODOS: un "rechazado" frena siempre, y SIN veredicto válido
  // tampoco se publica ni a mano. La única vía es validar el
  // clip (el cerebro, validate_all) o que un humano vea el
  // preview (preview_seen=1, evidencia de revisión humana). Es
  // el mismo punto para el enqueue y para el re-chequeo en
  // ejecución (recheckPublishGate llama aquí): la puerta nunca
  // se decide solo al encolar.
  const quality = qualityGateForClip(clip);
  if (quality.reason) return quality.reason;
  // (La definición de `s` está arriba, antes de la puerta de
  // idioma: la usa también el mensaje del rescate.)
  // Preview OPCIONAL (2026-11-18, v3.1, require_preview, por
  // defecto false): el preview solo frena si el usuario activó
  // "Exigir ver el preview antes de publicar" en Ajustes. Sin
  // activar, un clip sin preview visto publica igual; el preview
  // sigue disponible en el Editor como casilla informativa.
  if (s.require_preview && !clip.preview_seen) return "falta ver el preview en el Editor";
  const dur = clip.end_sec - clip.start_sec;
  // Sin mínimo ni rango prohibido desde 2026-11-21 (regla 40-60 /
  // 25-35 eliminada por el usuario, para siempre). Solo queda el
  // tope de 60 s (mínimo/máximo desde Ajustes, defaults 1–60).
  if (dur > 0 && (dur < s.min_duration_s || dur > s.max_duration_s)) return `duración ${dur}s fuera de ${s.min_duration_s}–${s.max_duration_s} s`;
  // Gate de categoría GTA VI (decisión del usuario 2026-11-18):
  // solo se publica GTA VI. Un clip sin categoría declarada
  // (filas viejas) tampoco pasa: hay que ponerle game "GTA VI".
  if (!isGtaViGame(clip.game ?? "")) {
    return clip.game ? `categoría: solo GTA VI (este clip es ${clip.game})` : "categoría: sin categoría GTA VI declarada";
  }
  if (!g.streamerAllowed) return "el streamer no tiene permiso marcado";
  if (g.seoScore !== null && !g.seoOverride && g.seoScore < s.min_seo) return `SEO ${g.seoScore}: bajo la puerta de ${s.min_seo} en ${platform}`;
  const now = g.nowMs ?? nowMs();
  // PAUSA POR SPAM/LÍMITE (regla 6, 48 h): frena SIEMPRE, también
  // a mano. Nunca se esquiva un límite cambiando de método.
  if (g.pausedUntilMs !== undefined && g.pausedUntilMs !== null && now < g.pausedUntilMs) {
    return `cuenta en pausa por spam/límite hasta ${new Date(g.pausedUntilMs).toISOString()}: no se publica ni a mano (pausa de 48 h)`;
  }
  // ROLLING 24 h (regla 2): nunca más de 3 por cuenta en 24 h
  // móviles (6 en YouTube, decisión 2026-11-23: YouTube es
  // 6/día). Techo duro: frena SIEMPRE, también a mano.
  const rollingMax = platform === "youtube_shorts"
    ? (s.rolling_24h_max_youtube ?? s.rolling_24h_max)
    : s.rolling_24h_max;
  if (g.publishedLast24h !== undefined && g.publishedLast24h >= rollingMax) {
    return `tope rolling 24 h alcanzado: ${g.publishedLast24h}/${rollingMax} en ${platform} (últimas 24 h)`;
  }
  // MISMO CLIP, MISMA PLATAFORMA, MISMO DÍA (regla 5): no se
  // repite el mismo clip en una misma cuenta. Frena siempre,
  // SALVO republicación explícita (P0 #5): con republish=true
  // el usuario pide publicar de nuevo porque la plataforma
  // borró el vídeo; el resto de puertas sigue frenando igual.
  if (g.clipPublishedTodayOnPlatform && !g.republish) {
    return `este clip ya se publicó hoy en ${platform}: no se repite el mismo clip en la misma cuenta el mismo día`;
  }
  // POSTED-EVER (F1, spec 2026-11-24): el mismo clip ya salió
  // en ESTA plataforma otro día. Frena siempre, también a mano,
  // salvo republicación explícita (la plataforma borró el vídeo).
  // Sin esto, el auto-enqueue re-creaba el job de TikTok del 102
  // y la manual también podía duplicarlo.
  if (g.clipPublishedEverOnPlatform && !g.republish) {
    const day = g.clipLastPublishedOnPlatformDay ?? "otro día";
    return `este clip ya se publicó en ${platform} el ${day}: no se repite en la misma cuenta (usa republicar si la plataforma borró el vídeo)`;
  }
  // CALENTAMIENTO (reglas 2026-11-19): el tope de la etapa (1/día
  // en semana 1/2, plan en semana 3) NO lo salta "Publicar ahora":
  // desde la orden 09:25 la manual no salta ningún freno.
  if (g.warmupDailyLimit !== undefined && g.warmupDailyLimit !== null && g.publishedToday >= g.warmupDailyLimit) {
    return `calentamiento: ${g.publishedToday}/${g.warmupDailyLimit} hoy en ${platform} (cuenta nueva: ni a mano se supera el tope de su etapa)`;
  }
  // DÍA LIBRE SEMANAL (regla 7): solo frena la automática.
  if (!g.manual && g.isRestDay) {
    return `día libre de la cuenta en ${platform}: hoy no publica (1 día libre por semana, rotativo)`;
  }
  // FRANJA HORARIA (reglas 2026-11-19; REVOCA la decisión
  // 2026-11-18 "sin franja"): la publicación solo sale
  // 12:00-14:00, 18:00-20:00 o 21:00-23:00 (hora Chile). Desde
  // la orden 09:25 (2026-11-21, SIN AMONTONAR) la comprueba
  // TAMBIÉN la manual. Solo cuando quien encola lo pide
  // (checkSlot): el re-chequeo en ejecución no la comprueba y
  // la manual en ejecución tampoco (un job encolado dentro de
  // franja no se queda varado por reanudarse fuera de ella).
  if (g.checkSlot && !isWithinPublishSlot(now)) {
    return `fuera de franja: la publicación solo sale 12:00–14:00, 18:00–20:00 o 21:00–23:00 (hora de Chile)`;
  }
  // 1 POR PLATAFORMA Y FRANJA (orden 09:25, 2026-11-21, SIN
  // AMONTONAR): como mucho una publicación por plataforma en
  // cada franja. Frena SIEMPRE, también a mano. Es lo que
  // impide que 3 YouTube salgan a las 09:11 en la misma franja.
  if (g.publishedThisSlot !== undefined && g.publishedThisSlot >= 1) {
    return `ya se publicó 1 en ${platform} en esta franja: como mucho 1 por plataforma y franja`;
  }
  // FRENOS (tope diario + espaciamiento + cross-platform 15 min):
  // desde la orden 09:25 (2026-11-21, SIN AMONTONAR) rigen
  // TAMBIÉN la manual. Antes la manual los saltaba y el llamador
  // solo registraba el aviso con rateLimitGate; eso es lo que
  // amontonó 3 YouTube a las 09:11.
  const rate = rateLimitGate(platform, g);
  if (rate) return rate;
  return null;
}

/** Solo los FRENOS (tope diario + espaciamiento + cross-platform
 *  15 min) de publishGate, sin las puertas de calidad: lo usa
 *  publishGate (automático y manual, desde la orden 09:25) y
 *  quien quiera saber QUÉ freno aplicaría sin encolar. Devuelve
 *  el motivo o null si ningún freno aplica. */
export function rateLimitGate(platform: PanelPlatform, g: GateContext): string | null {
  const s = effectiveSettings(g.settings ?? {});
  // Topes diarios desde Ajustes (techo y plan POR PLATAFORMA,
  // reglas 2026-11-19) y madurez de la cuenta (calentamiento):
  // una cuenta en semana 1/2 publica 1/día; en semana 3, el plan
  // sin máx; establecida, plan y máx. Sin cuenta, Ajustes.
  // DOS COLAS (0019): publishedToday y lastPublishedAtMs ya vienen
  // contados POR plataforma+idioma (quien llama filtra los jobs por
  // jobs.language = idioma del clip): YouTube ES 1/día es
  // independiente de YouTube EN 1/día, y lo mismo el espaciamiento.
  const maxBase = platformMax(s, toApiPlatform(platform));
  const max = g.accountDailyLimit !== undefined && g.accountDailyLimit !== null
    ? Math.min(g.accountDailyLimit, maxBase) : maxBase;
  const plan = effectiveDailyPlan(platform, g.settings ?? {}, g.accountDailyLimit, g.accountDailyPlan);
  if (g.publishedToday >= max) return `tope diario alcanzado: ${g.publishedToday}/${max} en ${platform}`;
  if (g.publishedToday >= plan) return `plan del día cubierto: ${g.publishedToday}/${plan} en ${platform} (techo ${max})`;
  const now = g.nowMs ?? nowMs();
  // Espaciamiento POR CUENTA (regla 4): 3 h en general, 4 h en
  // YouTube; mínimo absoluto 5 min (nunca 2 vídeos al mismo
  // minuto). spacing_hours=0 lo apaga (excepción documentada).
  const spacingH = spacingHoursFor(s, toApiPlatform(platform));
  const spacingMs = spacingH * 3600 * 1000;
  if (spacingMs > 0 && g.lastPublishedAtMs && now - g.lastPublishedAtMs < spacingMs) {
    return `espaciamiento: menos de ${spacingH} h desde la última subida en esta plataforma`;
  }
  if (g.lastPublishedAtMs && now - g.lastPublishedAtMs < s.min_spacing_minutes * 60 * 1000) {
    return `espaciamiento: menos de ${s.min_spacing_minutes} min desde la última subida en esta plataforma`;
  }
  // MISMO CLIP EN DISTINTAS PLATAFORMAS (regla 4): mínimo 15 min
  // entre una y otra (ideal 12-24 h; el plan diario lo separa en
  // franjas distintas). Desde la orden 09:25 frena TAMBIÉN a mano.
  const crossMin = s.cross_platform_spacing_minutes;
  if (crossMin > 0 && g.clipLastPublishedOtherPlatformMs) {
    const gapMin = (now - g.clipLastPublishedOtherPlatformMs) / 60000;
    if (gapMin < crossMin) {
      return `mismo clip en otra plataforma hace ${Math.max(0, Math.floor(gapMin))} min: mínimo ${crossMin} min entre plataformas`;
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Clasificación transitorio/duro de los motivos del gate (F2, spec
// 2026-11-24). ÚNICA FUENTE: publishGate, rateLimitGate,
// recheckPublishGate, qualityGateForClip y reservePublishSlot
// siguen devolviendo `string | null`; la clasificación vive solo
// aquí, en esta tabla. Un motivo no reconocido es DURO
// (fail-closed): un freno que no sabemos clasificar va a draft,
// nunca se reprograma solo. La tabla se consulta en orden, de más
// específica a menos específica (el "cupo no reservado" va antes
// que el "tope diario" genérico, porque empieza igual).
// ---------------------------------------------------------------------------

/** Cómo se reprograma un job frenado por un motivo transitorio. */
export type GateBlockRetryKind = "spacing" | "cross15" | "slotEnd" | "nextSlot" | "rolling" | "tomorrow" | "backoff" | null;

export interface GateBlockRule {
  /** Regex que casa el motivo (sin `$` = prefijo: el motivo puede
   *  llevar variables después; con `$` = cadena completa). */
  match: RegExp;
  /** true = el tiempo lo resuelve solo (reprogramar); false =
   *  necesita acción humana o cambio del sistema (draft). */
  transient: boolean;
  retry: GateBlockRetryKind;
}

export const GATE_BLOCKS: GateBlockRule[] = [
  // --- Transitorios (el tiempo los resuelve solo) ---
  // El "cupo no reservado" VA ANTES que el "tope diario" genérico:
  // empieza igual y es más específico (backoff, no mañana).
  { match: /^tope diario alcanzado: cupo no reservado en \S+ tras 3 intentos$/, transient: true, retry: "backoff" },
  { match: /^reserva de cupo perdida: el job cambió de manos antes de publicar$/, transient: true, retry: "backoff" },
  { match: /^tope de reintentos por hoy:/, transient: true, retry: "backoff" },
  { match: /^espaciamiento: menos de \d+ h desde la última subida en esta plataforma$/, transient: true, retry: "spacing" },
  { match: /^espaciamiento: menos de \d+ min desde la última subida en esta plataforma$/, transient: true, retry: "spacing" },
  { match: /^mismo clip en otra plataforma hace \d+ min: mínimo \d+ min entre plataformas$/, transient: true, retry: "cross15" },
  { match: /^ya se publicó 1 en \S+ en esta franja: como mucho 1 por plataforma y franja$/, transient: true, retry: "slotEnd" },
  { match: /^fuera de franja:/, transient: true, retry: "nextSlot" },
  { match: /^tope rolling 24 h alcanzado: \d+\/\d+ en \S+ \(últimas 24 h\)$/, transient: true, retry: "rolling" },
  { match: /^tope diario alcanzado: \d+\/\d+ en \S+$/, transient: true, retry: "tomorrow" },
  { match: /^plan del día cubierto: \d+\/\d+ en \S+ \(techo \d+\)$/, transient: true, retry: "tomorrow" },
  { match: /^calentamiento: \d+\/\d+ hoy en \S+ \(cuenta nueva: ni a mano se supera el tope de su etapa\)$/, transient: true, retry: "tomorrow" },
  { match: /^día libre de la cuenta en \S+: hoy no publica \(1 día libre por semana, rotativo\)$/, transient: true, retry: "tomorrow" },
  // --- Duros (acción humana o cambio del sistema; van a draft) ---
  { match: /^clip archivado$/, transient: false, retry: null },
  { match: /^clip ya publicado$/, transient: false, retry: null },
  { match: /^clip no encontrado$/, transient: false, retry: null },
  { match: /^el clip no tiene MP4 en R2: súbelo desde el Editor antes de publicar\.$/, transient: false, retry: null },
  { match: /^el job apunta a una cuenta que ya no existe: vuelve a encolar el clip desde Publicar\.$/, transient: false, retry: null },
  { match: /^sin cuenta /, transient: false, retry: null },
  { match: /^idioma: el clip es en /, transient: false, retry: null },
  { match: /^cuenta .+ sin credencial verificada: pega la credencial en Cuentas y verifícala$/, transient: false, retry: null },
  { match: /^rescatado por gritos \(viralidad /, transient: false, retry: null },
  { match: /^sin subtítulos completos: necesita transcripción humana$/, transient: false, retry: null },
  { match: /^calidad:/, transient: false, retry: null },
  { match: /^falta ver el preview en el Editor$/, transient: false, retry: null },
  { match: /^duración \d+s fuera de \d+–\d+ s$/, transient: false, retry: null },
  { match: /^categoría: solo GTA VI/, transient: false, retry: null },
  { match: /^categoría: sin categoría GTA VI declarada$/, transient: false, retry: null },
  { match: /^el streamer no tiene permiso marcado$/, transient: false, retry: null },
  { match: /^streamer desactivado en el panel$/, transient: false, retry: null },
  { match: /^SEO \d+: bajo la puerta de \d+ en \S+$/, transient: false, retry: null },
  { match: /^cuenta en pausa por spam\/límite hasta /, transient: false, retry: null },
  { match: /^este clip ya se publicó hoy en /, transient: false, retry: null },
  { match: /^este clip ya se publicó en /, transient: false, retry: null },
];

/** Clasifica un motivo del gate. Motivo no reconocido = duro
 *  (fail-closed): nunca se reprograma solo algo que no sabemos
 *  que el tiempo resuelve. */
export function classifyGateBlock(reason: string): { transient: boolean; retry: GateBlockRetryKind } {
  for (const rule of GATE_BLOCKS) {
    if (rule.match.test(reason)) return { transient: rule.transient, retry: rule.retry };
  }
  return { transient: false, retry: null };
}

/** Calcula el `next_run_at` al que reprogramar un job frenado por
 *  un motivo transitorio (F2). Re-consulta lo que necesita (la
 *  función recibe `env`+`job`, no depende de que el re-chequeo le
 *  pase los contadores — el re-chequeo los descarta tras llamar a
 *  `publishGate`). Solo lee subidas `posted` reales, NUNCA reservas
 *  de cupo: `lastPublishedAtMs` del re-chequeo está contaminado por
 *  `max(lastPosted, slot.updated_at)` y una reserva que nunca
 *  publicó no es una subida. Fallbacks si no se puede calcular:
 *  las reglas de hora (`nextSlot`, `slotEnd`, `tomorrow`) se
 *  calculan solo con el horario (sin lecturas D1); las de contador
 *  (`spacing`, `cross15`, `rolling`) caen a
 *  `now + nextRetryDelayMs(retriesUsedToday)`. */
export async function retryAtForGateBlock(env: Env, job: GateJob, reason: string, now: number): Promise<number> {
  const { retry } = classifyGateBlock(reason);
  const db = env.DB;
  const platform = job.platform;
  // Idioma del job (para contar por plataforma+idioma): el de la
  // fila si viene, si no el del clip.
  let language = job.language ?? null;
  if (!language && job.clip_id) {
    const clip = await getClip(db, job.clip_id, job.tenant_id).catch(() => null);
    language = clip ? clipLanguage(clip) : "es";
  }
  language ??= "es";
  const backoffFallback = async (): Promise<number> => {
    let used = 0;
    try {
      const row = await db.prepare("SELECT attempts, updated_at FROM jobs WHERE id = ? AND tenant_id = ?")
        .bind(job.id, job.tenant_id)
        .first<{ attempts: number; updated_at: number }>();
      if (row) used = retriesToday(row, santiagoDayKeyMs(now));
    } catch {
      // Sin fila legible: 0 usados (el peldaño más corto).
    }
    return now + (nextRetryDelayMs(used) ?? 60_000);
  };
  switch (retry) {
    case "spacing": {
      const lastPosted = await db.prepare(
        "SELECT MAX(updated_at) AS last_at FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted'",
      )
        .bind(job.tenant_id, platform, language)
        .first<{ last_at: number | null }>()
        .catch(() => null);
      if (!lastPosted?.last_at) return backoffFallback();
      const settings = await getSettings(db, job.tenant_id).catch(() => ({} as Record<string, unknown>));
      const eff = effectiveSettings(settings);
      // "menos de {h} h" o "menos de {min} min": el motivo lleva
      // el espaciamiento vigente; si no se lee, el de Ajustes.
      const hMatch = /^espaciamiento: menos de (\d+) h /.exec(reason);
      const mMatch = /^espaciamiento: menos de (\d+) min /.exec(reason);
      const spacingMs = hMatch
        ? Number(hMatch[1]) * 3600 * 1000
        : mMatch
          ? Number(mMatch[1]) * 60 * 1000
          : (platform ? spacingHoursFor(eff, toApiPlatform(platform)) * 3600 * 1000 : eff.min_spacing_minutes * 60 * 1000);
      return lastPosted.last_at + spacingMs;
    }
    case "cross15": {
      if (!job.clip_id) return backoffFallback();
      const lastOther = await db.prepare(
        "SELECT MAX(updated_at) AS last_at FROM jobs WHERE tenant_id = ? AND clip_id = ? AND platform <> ? AND state IN ('posted','processing')",
      )
        .bind(job.tenant_id, job.clip_id, platform)
        .first<{ last_at: number | null }>()
        .catch(() => null);
      if (!lastOther?.last_at) return backoffFallback();
      const settings = await getSettings(db, job.tenant_id).catch(() => ({} as Record<string, unknown>));
      const eff = effectiveSettings(settings);
      const crossMatch = /mínimo (\d+) min entre plataformas/.exec(reason);
      const crossMin = crossMatch ? Number(crossMatch[1]) : eff.cross_platform_spacing_minutes;
      return lastOther.last_at + crossMin * 60 * 1000;
    }
    case "slotEnd": {
      const bounds = slotWindowBoundsMs(now);
      if (bounds) return bounds.endMs;
      // Sin franja actual: próxima hora de hueco con now + 61 min
      // (no nextPlatformHourMs(now): dentro de la ventana de 60
      // min devuelve nowMs y el job re-vencería de inmediato).
      return platform ? nextPlatformHourMs(platform, now + 61 * 60 * 1000) : nextSlotStartMs(now);
    }
    case "nextSlot": {
      return nextSlotStartMs(now);
    }
    case "rolling": {
      const oldest = await db.prepare(
        "SELECT MIN(updated_at) AS oldest_at FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' AND updated_at > ?",
      )
        .bind(job.tenant_id, platform, language, now - 24 * 3600 * 1000)
        .first<{ oldest_at: number | null }>()
        .catch(() => null);
      if (!oldest?.oldest_at) return backoffFallback();
      return oldest.oldest_at + 24 * 3600 * 1000;
    }
    case "tomorrow": {
      const hours = platform ? (PLATFORM_SCHEDULE[platform] ?? PLATFORM_SCHEDULE.youtube_shorts!) : PLATFORM_SCHEDULE.youtube_shorts!;
      const tomorrow = santiagoDayKeyMs(now + 24 * 3600 * 1000);
      return santiagoWallToUtcMs(tomorrow, hours[0]!);
    }
    case "backoff":
    default: {
      return backoffFallback();
    }
  }
}

/** SEO por plataforma desde clips.viral / seo_overrides (JSON del panel).
 *  Se lee viral.platforms[platform].score (forma legacy) y, si no
 *  está, viral.seo_scores[platform] (contrato IA 2026-11-18).
 *  seo_overrides se guarda como lista de plataformas (["youtube_shorts"]);
 *  se aceptan también objetos {platform} por compatibilidad con filas
 *  antiguas. */
export function seoForPlatform(clip: ClipRow, platform: PanelPlatform): { score: number | null; override: boolean } {
  try {
    const viral = JSON.parse(clip.viral || "{}") as Record<string, unknown>;
    const platforms = (viral.platforms ?? {}) as Record<string, { score?: number }>;
    const seoScores = (viral.seo_scores ?? {}) as Record<string, number>;
    const score = platforms[platform]?.score ?? seoScores[platform] ?? null;
    const overrides = JSON.parse(clip.seo_overrides || "[]") as unknown;
    const list = Array.isArray(overrides) ? overrides : [];
    const override = list.some((o) => (typeof o === "string" ? o === platform : typeof o === "object" && o !== null && (o as { platform?: string }).platform === platform));
    return { score: typeof score === "number" ? score : null, override };
  } catch {
    return { score: null, override: false };
  }
}

/** Job mínimo que necesita el re-chequeo (JobRow lo satisface). */
export interface GateJob {
  id?: number;
  kind: string;
  clip_id: number | null;
  platform: PanelPlatform | null;
  api_account_id: number | null;
  tenant_id: Tenant;
  /** Idioma del job (jobs.language, DOS COLAS 0019): para contar
   *  por plataforma+idioma en retryAtForGateBlock. Si no viene,
   *  se resuelve del clip. */
  language?: string | null;
  // Cupo que el job ya tiene (jobs.slot_day, migración 0022): si
  // es el día de hoy, su propia reserva no cuenta como publicada
  // por otro job ni como última subida (ver recheckPublishGate).
  slot_day?: string | null;
  // Publicación MANUAL (2026-11-19, jobs.manual, migración 0029):
  // el job se encoló a mano desde "Publicar ahora": el re-chequeo
  // y la reserva saltan tope+espaciamiento como en el enqueue.
  manual?: number | null;
}

/** Re-chequeo del gate EN EJECUCIÓN (hallazgo M1, 2026-11-18):
 *  lo llama dispatchPublishStep justo antes del adaptador de la
 *  plataforma. Lee la fila ACTUAL del clip y los contadores FRESCOS
 *  por plataforma+idioma (publicados hoy, última subida), con la
 *  misma resolución de cuenta y madurez que POST /publish. Devuelve
 *  null si pasa, o el motivo si frena (el job va a draft, no se
 *  publica). El gate de enqueue queda solo como aviso temprano:
 *  nunca más decide solo. Las reglas de publishGate no cambian. */
export async function recheckPublishGate(env: Env, job: GateJob): Promise<string | null> {
  if (job.kind !== "publish" || !job.platform) return null;
  const db = env.DB;
  const clip = job.clip_id ? await getClip(db, job.clip_id, job.tenant_id) : null;
  if (!clip) return "clip no encontrado";
  // CHEQUEO DE INFLUENCER ACTIVO (spec 2026-11-24, §3.5): en
  // producción no había ningún chequeo de influencer activo al
  // publicar — desactivar una entrada NO frenaba lo ya encolado.
  // Ahora se resuelve clip.influencer_id → fila de influencers;
  // si permission=0 o active=0, el job queda bloqueado/retenido
  // con el motivo (a draft con retryable=0, vía markDraft en
  // jobs.ts; al reactivar, el clip se re-encola a mano). Aplica a
  // todos los clips, no solo a los de YouTube. Va ANTES del resto
  // de puertas para que el motivo sea este y no otro (p. ej. la
  // falta de cuenta). Clip sin influencer_id (columna admite
  // null, clips viejos existen) → el chequeo PASA (fail-open con
  // log), nunca bloquea. Fila borrada → también fail-open.
  let streamerAllowed = true;
  if (clip.influencer_id !== null && clip.influencer_id !== undefined) {
    const inf = await getInfluencer(db, clip.influencer_id, job.tenant_id).catch(() => null);
    if (inf) {
      streamerAllowed = inf.permission === 1 && inf.active === 1;
      if (!streamerAllowed) return "streamer desactivado en el panel";
    }
  } else {
    await logWorkerEvent(
      db,
      "gate",
      `Clip #${clip.id} sin influencer_id: el chequeo de streamer desactivado pasa (fail-open, clips viejos).`,
      job.tenant_id,
    ).catch(() => {});
  }
  const lang = clipLanguage(clip);
  const settings = await getSettings(db, job.tenant_id);
  const eff = effectiveSettings(settings);
  const seo = seoForPlatform(clip, job.platform);
  const day = santiagoDayKey();
  // Topes POR plataforma+idioma (0019): solo cuentan las
  // publicaciones de hoy en este idioma (jobs.language).
  const publishedRow = await db.prepare(
    "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' AND posted_day = ?",
  )
    .bind(job.tenant_id, job.platform, lang, day)
    .first<{ n: number }>();
  // Espaciamiento POR plataforma+idioma (0019): la última subida
  // en este idioma; sin publicaciones, sin freno.
  const lastPosted = await db.prepare(
    "SELECT updated_at FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' ORDER BY updated_at DESC LIMIT 1",
  )
    .bind(job.tenant_id, job.platform, lang)
    .first<{ updated_at: number }>();
  // RESERVAS (migración 0022, NEEDS-VALIDATION §4): un cupo
  // reservado por otro job del mismo ciclo (o de un ciclo anterior
  // que aún no marcó posted) cuenta como publicado para el tope y
  // como última subida para el espaciamiento. La reserva PROPIA no
  // cuenta: un job multi-step que reanuda (YouTube por chunks,
  // contenedor de Instagram, fases de Facebook) no se frena a sí
  // mismo con su propio cupo.
  const slot = await readPublishSlot(db, job.tenant_id, job.platform, lang, day);
  const reserved = slot?.reserved_count ?? 0;
  const holdsOwn = job.slot_day === day;
  const reservedOthers = Math.max(0, reserved - (holdsOwn ? 1 : 0));
  const publishedToday = (publishedRow?.n ?? 0) + reservedOthers;
  const lastPublishedAtMs = reservedOthers > 0 && slot && slot.updated_at > 0
    ? Math.max(lastPosted?.updated_at ?? 0, slot.updated_at)
    : lastPosted?.updated_at ?? null;
  // Cuenta POR PLATAFORMA + IDIOMA DEL CLIP, como en POST /publish.
  const want = job.platform === "youtube_shorts" ? "youtube" : job.platform === "tiktok" ? "tiktok" : "meta";
  const acc = await getApiAccountByPlatform(db, job.tenant_id, want, lang);
  const mat = acc ? maturityOut(acc, eff) : null;
  // Contadores nuevos (reglas 2026-11-19): rolling 24 h, mismo
  // clip hoy en esta plataforma, mismo clip en otra plataforma.
  const now = Date.now();
  const last24hRow = await db.prepare(
    "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' AND updated_at > ?",
  )
    .bind(job.tenant_id, job.platform, lang, now - 24 * 3600 * 1000)
    .first<{ n: number }>();
  const clipTodayRow = await db.prepare(
    "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND clip_id = ? AND platform = ? AND state = 'posted' AND posted_day = ?",
  )
    .bind(job.tenant_id, job.clip_id, job.platform, day)
    .first<{ n: number }>();
  // MISMO CLIP EN OTRA PLATAFORMA (F2b, spec 2026-11-24): `state
  // IN ('posted','processing')`, no solo posted. Los jobs del
  // ciclo se despachan en paralelo por grupo y cada grupo reclama
  // su job (state='processing' + run_token) ANTES de re-chequear:
  // un hermano en `processing` cuenta como "última subida del
  // clip en otra plataforma" a su `updated_at`, o dos plataformas
  // del mismo clip publicarían a la vez violando los 15 min.
  const clipOtherRow = await db.prepare(
    "SELECT updated_at FROM jobs WHERE tenant_id = ? AND clip_id = ? AND platform <> ? AND state IN ('posted','processing') ORDER BY updated_at DESC LIMIT 1",
  )
    .bind(job.tenant_id, job.clip_id, job.platform)
    .first<{ updated_at: number }>();
  // POSTED-EVER (F1, spec 2026-11-24): el mismo clip ya salió en
  // ESTA plataforma otro día. Sin filtro de día: el `posted_day`
  // del posted más reciente, para el motivo del gate.
  const clipEverRow = await db.prepare(
    "SELECT posted_day FROM jobs WHERE tenant_id = ? AND clip_id = ? AND platform = ? AND state = 'posted' ORDER BY updated_at DESC LIMIT 1",
  )
    .bind(job.tenant_id, job.clip_id, job.platform)
    .first<{ posted_day: string | null }>();
  // 1 POR PLATAFORMA Y FRANJA (orden 09:25, SIN AMONTONAR):
  // publicadas en ESTA plataforma+idioma dentro de la franja
  // actual. Se lee fresco justo antes del adaptador, así que un
  // segundo job del mismo ciclo ya no pasa si el primero publicó.
  const slotBounds = slotWindowBoundsMs(now);
  let publishedThisSlot = 0;
  if (slotBounds) {
    // El intervalo es [startMs, endMs). Se escribe con `>` y
    // `<` (no `>=`) porque los mocks de D1 de los tests no
    // soportan `>=`; con timestamps enteros en ms,
    // `updated_at > startMs - 1` es exactamente `>= startMs`.
    const slotRow = await db.prepare(
      "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' AND updated_at > ? AND updated_at < ?",
    )
      .bind(job.tenant_id, job.platform, lang, slotBounds.startMs - 1, slotBounds.endMs)
      .first<{ n: number }>();
    publishedThisSlot = slotRow?.n ?? 0;
  }
  // Pausa por spam (settings account_pauses) y día libre
  // (determinista por cuenta+semana): frenan aquí igual que en
  // el enqueue. La FRANJA no se re-chequea en ejecución: un job
  // encolado dentro de franja no se queda varado por reanudarse
  // fuera de ella (multi-step); la franja se aplica al encolar.
  const pausedUntilMs = acc ? accountPausedUntilMs(settings, acc.id) : null;
  const restDay = acc ? isRestDayEffective(`acc-${acc.id}`, now, settings, acc.id) : false;
  return publishGate(job.platform, {
    clip,
    seoScore: seo.score,
    seoOverride: seo.override,
    publishedToday,
    lastPublishedAtMs,
    streamerAllowed, // el permiso se verifica arriba, contra la fila del influencer del clip
    settings,
    accountDailyLimit: mat?.effective_daily_limit ?? null,
    accountDailyPlan: mat?.effective_daily_plan ?? null,
    // Calentamiento: la manual no lo salta; el re-chequeo lo
    // aplica a manuales y automáticos por igual.
    warmupDailyLimit: mat && mat.stage !== "establecida" ? mat.effective_daily_plan : null,
    publishedLast24h: last24hRow?.n ?? 0,
    clipPublishedTodayOnPlatform: (clipTodayRow?.n ?? 0) > 0,
    clipPublishedEverOnPlatform: clipEverRow !== null,
    clipLastPublishedOnPlatformDay: clipEverRow?.posted_day ?? null,
    clipLastPublishedOtherPlatformMs: clipOtherRow?.updated_at ?? null,
    publishedThisSlot,
    pausedUntilMs,
    isRestDay: restDay,
    accountLanguage: acc ? (acc.language === "en" ? "en" : "es") : null,
    accountReady: acc ? acc.verify_ok === 1 && !!(acc.credential_enc || acc.credential) : false,
    // Manual (2026-11-19): el job nació de "Publicar ahora". Desde
    // la orden 09:25 (SIN AMONTONAR) el re-chequeo aplica tope,
    // espaciamiento y 1-por-franja también a manuales. La franja
    // en sí no se re-chequea (el job se encoló dentro de ella).
    manual: job.manual === 1,
  });
}

// ---------------------------------------------------------------------------
// Cupos diarios atómicos (migración 0022, NEEDS-VALIDATION §4).
// La reserva es la primitiva que cierra el TOCTOU del gate: el
// re-chequeo lee publicados, pero un cupo reservado por otro job
// del mismo ciclo (o de un ciclo anterior que aún no marcó posted)
// ya cuenta como publicado. D1-visible (tabla publish_slots), no
// en memoria: dos invocaciones del cron solapadas no pueden
// intercalar una reserva. El contador es por día (clave day), así
// que una fuga solo vive hasta que cambia el día.
// ---------------------------------------------------------------------------

export interface SlotJob {
  id: number;
  tenant_id: Tenant;
  platform: PanelPlatform | null;
  language: string;
  run_token?: string | null;
  slot_day?: string | null;
  // Publicación MANUAL (2026-11-19, jobs.manual, migración 0029):
  // el job se encoló a mano. Desde la orden 09:25 (SIN AMONTONAR)
  // la reserva SÍ frena por tope diario y por 1-por-franja, como
  // a cualquier job; el cupo se toma igual para que la publicación
  // manual cuente en el uso del día de los jobs que vengan después.
  manual?: number | null;
}

interface SlotRow {
  reserved_count: number;
  updated_at: number;
}

/** D1 siempre devuelve meta.changes numérico; los dobles de test
 *  que no modelan changes devuelven undefined y se tratan como
 *  éxito (nunca como fracaso): la primitiva atómica real es el
 *  predicado de valor esperado del UPDATE, no este conteo. */
function updateChanged(res: { meta: { changes?: number } }): boolean {
  return res.meta.changes === undefined || res.meta.changes >= 1;
}

export async function readPublishSlot(
  db: D1Database,
  tenant: Tenant,
  platform: PanelPlatform,
  language: string,
  day: string,
): Promise<SlotRow | null> {
  const row = await db
    .prepare("SELECT reserved_count, updated_at FROM publish_slots WHERE tenant_id = ? AND platform = ? AND language = ? AND day = ?")
    .bind(tenant, platform, language, day)
    .first<{ reserved_count: unknown; updated_at: unknown }>();
  if (!row) return null;
  return {
    reserved_count: typeof row.reserved_count === "number" ? row.reserved_count : 0,
    updated_at: typeof row.updated_at === "number" ? row.updated_at : 0,
  };
}

/** Tope (plan) vigente para un job, con la misma resolución de
 *  cuenta y madurez que recheckPublishGate. */
export async function effectiveDailyPlanForJob(
  db: D1Database,
  tenant: Tenant,
  platform: PanelPlatform,
  language: string,
): Promise<number> {
  const settings = await getSettings(db, tenant);
  const eff = effectiveSettings(settings);
  const want = platform === "youtube_shorts" ? "youtube" : platform === "tiktok" ? "tiktok" : "meta";
  const acc = await getApiAccountByPlatform(db, tenant, want, language);
  const mat = acc ? maturityOut(acc, eff) : null;
  return effectiveDailyPlan(platform, settings, mat?.effective_daily_limit ?? null, mat?.effective_daily_plan ?? null);
}

async function decrementPublishSlot(
  db: D1Database,
  tenant: Tenant,
  platform: PanelPlatform,
  language: string,
  day: string,
): Promise<void> {
  for (let attempt = 0; attempt < 3; attempt++) {
    const slot = await readPublishSlot(db, tenant, platform, language, day);
    if (!slot || slot.reserved_count <= 0) return;
    const res = await db
      .prepare(
        "UPDATE publish_slots SET reserved_count = ?, updated_at = ? WHERE tenant_id = ? AND platform = ? AND language = ? AND day = ? AND reserved_count = ?",
      )
      .bind(slot.reserved_count - 1, nowMs(), tenant, platform, language, day, slot.reserved_count)
      .run();
    if (updateChanged(res)) return;
  }
  // Si tras 3 intentos no se pudo decrementar, el cupo queda
  // reservado hasta que cambia el día (la clave es por día): fuga
  // acotada a un día, sin efecto en días siguientes ni en otros
  // tenants/plataformas/idiomas.
}

/** Reserva un cupo diario para el job antes del adaptador.
 *  Si el job ya tiene el cupo de hoy (slot_day == day, caso
 *  multi-step: YouTube por chunks, contenedor de Instagram,
 *  fases de Facebook) no toma un segundo cupo. Si no hay cupo,
 *  devuelve el motivo en el wording del gate. */
export async function reservePublishSlot(db: D1Database, job: SlotJob): Promise<{ ok: true } | { ok: false; reason: string }> {
  if (!job.platform) return { ok: true };
  const day = santiagoDayKey();
  if (job.slot_day === day) return { ok: true };
  const limit = await effectiveDailyPlanForJob(db, job.tenant_id, job.platform, job.language);
  for (let attempt = 0; attempt < 3; attempt++) {
    const slot = await readPublishSlot(db, job.tenant_id, job.platform, job.language, day);
    const posted = await db
      .prepare(
        "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' AND posted_day = ?",
      )
      .bind(job.tenant_id, job.platform, job.language, day)
      .first<{ n: number }>();
    const postedCount = posted?.n ?? 0;
    const reserved = slot?.reserved_count ?? 0;
    // ROLLING 24 h (regla 2, techo duro): nunca más de 3 por
    // cuenta en 24 h móviles (6 en YouTube, decisión 2026-11-23).
    // Frena la reserva incluso a mano: "Publicar ahora" salta
    // tope diario + espaciamiento, no el rolling. El gate ya lo
    // comprueba al encolar; aquí es la segunda valla para dos
    // ciclos solapados.
    const eff = effectiveSettings(await getSettings(db, job.tenant_id));
    const rollingMax = job.platform === "youtube_shorts"
      ? (eff.rolling_24h_max_youtube ?? eff.rolling_24h_max)
      : eff.rolling_24h_max;
    const rolling = await db
      .prepare(
        "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' AND updated_at > ?",
      )
      .bind(job.tenant_id, job.platform, job.language, nowMs() - 24 * 3600 * 1000)
      .first<{ n: number }>();
    if ((rolling?.n ?? 0) >= rollingMax) {
      return { ok: false, reason: `tope rolling 24 h alcanzado: ${rolling?.n ?? 0}/${rollingMax} en ${job.platform} (últimas 24 h)` };
    }
    // 1 POR PLATAFORMA Y FRANJA (orden 09:25, SIN AMONTONAR):
    // última valla antes del adaptador: si ya se publicó 1 en
    // esta plataforma+idioma dentro de la franja actual, el cupo
    // no se toma (el re-chequeo ya lo frenó; esto cubre el caso
    // en que otro job publicó entre el re-chequeo y la reserva).
    const slotBounds = slotWindowBoundsMs(nowMs());
    if (slotBounds) {
      // `>` en vez de `>=` (ver recheckPublishGate): los mocks
      // de D1 de los tests no soportan `>=`; con timestamps
      // enteros en ms, `> startMs - 1` es exactamente `>= startMs`.
      const slotPosted = await db
        .prepare(
          "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' AND updated_at > ? AND updated_at < ?",
        )
        .bind(job.tenant_id, job.platform, job.language, slotBounds.startMs - 1, slotBounds.endMs)
        .first<{ n: number }>();
      if ((slotPosted?.n ?? 0) >= 1) {
        return { ok: false, reason: `ya se publicó 1 en ${job.platform} en esta franja: como mucho 1 por plataforma y franja` };
      }
    }
    // Tope diario (orden 09:25, SIN AMONTONAR): frena TAMBIÉN a
    // mano; el cupo se toma igual (abajo), para que cuente en el día.
    if (postedCount + reserved >= limit) {
      return { ok: false, reason: `tope diario alcanzado: ${postedCount + reserved}/${limit} en ${job.platform}` };
    }
    if (!slot) {
      try {
        await db
          .prepare("INSERT INTO publish_slots (tenant_id, platform, language, day, reserved_count, updated_at) VALUES (?, ?, ?, ?, ?, ?)")
          .bind(job.tenant_id, job.platform, job.language, day, 0, nowMs())
          .run();
      } catch {
        // Violación de unicidad en D1 real (otro ciclo creó la
        // fila a la vez): re-leer y seguir como UPDATE.
      }
      continue;
    }
    const res = await db
      .prepare(
        "UPDATE publish_slots SET reserved_count = ?, updated_at = ? WHERE tenant_id = ? AND platform = ? AND language = ? AND day = ? AND reserved_count = ?",
      )
      .bind(reserved + 1, nowMs(), job.tenant_id, job.platform, job.language, day, reserved)
      .run();
    if (!updateChanged(res)) continue; // otro ciclo ganó la carrera: re-leer y reintentar
    // Cupo tomado: marcar el job que lo tiene, con su run_token,
    // para que solo ese reclamo lo suelte después. Si el job ya
    // cambió de manos, devolver el cupo de inmediato.
    if (job.run_token) {
      const marked = await db
        .prepare("UPDATE jobs SET slot_day = ? WHERE id = ? AND tenant_id = ? AND run_token = ?")
        .bind(day, job.id, job.tenant_id, job.run_token)
        .run();
      if (!updateChanged(marked)) {
        await decrementPublishSlot(db, job.tenant_id, job.platform, job.language, day);
        return { ok: false, reason: "reserva de cupo perdida: el job cambió de manos antes de publicar" };
      }
    }
    return { ok: true };
  }
  return { ok: false, reason: `tope diario alcanzado: cupo no reservado en ${job.platform} tras 3 intentos` };
}

/** Suelta el cupo de un job, solo si la fila actual sigue en
 *  manos del mismo reclamo (mismo run_token) y con el cupo de
 *  hoy. Llamar ANTES de la transición que cierra el job
 *  (posted/draft/error/awaiting_captcha); en "processing" NO se
 *  suelta: el siguiente paso reusa el mismo cupo. */
export async function releasePublishSlot(db: D1Database, job: SlotJob): Promise<void> {
  if (!job.platform || !job.run_token) return;
  const day = santiagoDayKey();
  const row = await db
    .prepare("SELECT run_token, slot_day FROM jobs WHERE id = ? AND tenant_id = ?")
    .bind(job.id, job.tenant_id)
    .first<{ run_token: unknown; slot_day: unknown }>();
  if (!row || row.run_token !== job.run_token || row.slot_day !== day) return;
  await db
    .prepare("UPDATE jobs SET slot_day = ? WHERE id = ? AND tenant_id = ? AND run_token = ?")
    .bind(null, job.id, job.tenant_id, job.run_token)
    .run();
  await decrementPublishSlot(db, job.tenant_id, job.platform, job.language, day);
}
