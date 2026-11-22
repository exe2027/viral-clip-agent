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
  getSettings,
  isGtaViGame,
  isRestDayEffective,
  isWithinPublishSlot,
  languageName,
  maturityOut,
  panelPlatformLabel,
  parseViralJson,
  platformMax,
  platformPlan,
  slotWindowBoundsMs,
  spacingHoursFor,
  toApiPlatform,
  type ClipLanguage,
  type ClipRow,
  type PanelPlatform,
} from "./db";
import { qualityGateForClip } from "./quality-gate";

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
  // 45/80 (P0 #3), techo YT 5 / resto 3.
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
      const rescue = parseViralJson(clip.viral).rescue;
      if (rescue) return `rescatado por gritos (viralidad ${rescue.viral_revisado} < ${s.min_virality}): bloqueado hasta revisión humana — desbloquéalo en el Editor`;
    } catch { /* sin rescue medido: cae al freno de subtítulos */ }
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
  // móviles. Techo duro: frena SIEMPRE, también a mano.
  const rollingMax = s.rolling_24h_max;
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
  kind: string;
  clip_id: number | null;
  platform: PanelPlatform | null;
  api_account_id: number | null;
  tenant_id: Tenant;
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
  const clipOtherRow = await db.prepare(
    "SELECT updated_at FROM jobs WHERE tenant_id = ? AND clip_id = ? AND platform <> ? AND state = 'posted' ORDER BY updated_at DESC LIMIT 1",
  )
    .bind(job.tenant_id, job.clip_id, job.platform)
    .first<{ updated_at: number }>();
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
    streamerAllowed: true, // el permiso se verifica en el puente con streamers.yaml
    settings,
    accountDailyLimit: mat?.effective_daily_limit ?? null,
    accountDailyPlan: mat?.effective_daily_plan ?? null,
    // Calentamiento: la manual no lo salta; el re-chequeo lo
    // aplica a manuales y automáticos por igual.
    warmupDailyLimit: mat && mat.stage !== "establecida" ? mat.effective_daily_plan : null,
    publishedLast24h: last24hRow?.n ?? 0,
    clipPublishedTodayOnPlatform: (clipTodayRow?.n ?? 0) > 0,
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
    // cuenta en 24 h móviles. Frena la reserva incluso a mano:
    // "Publicar ahora" salta tope diario + espaciamiento, no el
    // rolling. El gate ya lo comprueba al encolar; aquí es la
    // segunda valla para dos ciclos solapados.
    const eff = effectiveSettings(await getSettings(db, job.tenant_id));
    const rolling = await db
      .prepare(
        "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' AND updated_at > ?",
      )
      .bind(job.tenant_id, job.platform, job.language, nowMs() - 24 * 3600 * 1000)
      .first<{ n: number }>();
    if ((rolling?.n ?? 0) >= eff.rolling_24h_max) {
      return { ok: false, reason: `tope rolling 24 h alcanzado: ${rolling?.n ?? 0}/${eff.rolling_24h_max} en ${job.platform} (últimas 24 h)` };
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
