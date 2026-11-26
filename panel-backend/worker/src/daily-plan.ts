// Plan diario de publicación (reglas 2026-11-19, salida §9 del
// prompt de reglas). GET /api/daily-plan devuelve este JSON por
// tenant y fecha (America/Santiago):
//   { fecha, publicaciones: [{clip_id, plataforma, cuenta,
//     hora_local, titulo, descripcion, hashtags, motivo}],
//     en_cola: [clip_id], alertas: [...] }
// Antes de devolverlo se verifica que cumple las secciones 2
// (topes), 3 (calentamiento) y 4 (espaciado): lo que no cumple
// se elimina del plan y pasa a la cola.
//
// El constructor puro (buildDailyPlan) no toca D1: se prueba sin
// mocks. buildDailyPlanForTenant carga cuentas, madurez, pausas,
// contadores y Cola desde D1 y llama al constructor puro.

import type { D1Database } from "./cloudflare";
import type { Tenant } from "./auth";
import {
  accountPausedUntilMs,
  capHashtags,
  clipLanguage,
  effectiveSettings,
  getSettings,
  isCampaignStreamer,
  isRestDayEffective,
  listApiAccounts,
  listQueueClips,
  maturityOut,
  MIN_PUBLISH_SPACING_MINUTES,
  parsePlatforms,
  randomPublishDelayMs,
  restWeekdayEffective,
  santiagoDayKeyMs,
  santiagoWallToUtcMs,
  santiagoWeekKey,
  WEEKDAY_NAMES_ES,
  type EffectiveSettings,
  type PanelPlatform,
  type WarmupStage,
} from "./db";

export interface DailyPlanAccountInput {
  id: number;
  /** Plataforma del panel (youtube_shorts, tiktok, ...). */
  platform: PanelPlatform;
  /** Clave API (youtube | tiktok | instagram | facebook). */
  apiPlatform: string;
  language: "es" | "en";
  label: string;
  stage: WarmupStage;
  /** Ritmo normal vigente (ya con calentamiento aplicado). */
  plan: number;
  /** Máximo duro vigente (solo establecida lo alcanza). */
  max: number;
  daysSinceFirst: number | null;
  strikes: number;
  publishedToday: number;
  publishedLast24h: number;
  hasCredential: boolean;
  pausedUntilMs: number | null;
  restDay: boolean;
  restWeekday: number;
  /** Regla 6: >30 días sin restricción + cola viral alta. */
  canEscalate: boolean;
  /** Última publicación real de esta plataforma+idioma (ms
   *  epoch, null si no hay): el plan encadena desde ahí con
   *  azar(90–180 min) (SPEC quitar-franjas v2). */
  lastPublishedAtMs: number | null;
}

export interface DailyPlanClipInput {
  id: number;
  title: string;
  description: string;
  hashtags: string[];
  score: number;
  language: "es" | "en";
  platforms: PanelPlatform[];
  /** Streamer del clip (resuelto en buildDailyPlanForTenant desde
   *  influencers); null = sin asignar. */
  streamer?: string | null;
  /** true = el streamer tiene campaña activa >= $1/1k NETO
   *  (isCampaignStreamer): el clip tiene prioridad de campaña. */
  campaign?: boolean;
}

export interface DailyPlanPublication {
  clip_id: number;
  plataforma: string;
  cuenta: string;
  hora_local: string;
  titulo: string;
  descripcion: string;
  hashtags: string[];
  motivo: string;
}

export interface DailyPlan {
  fecha: string;
  publicaciones: DailyPlanPublication[];
  en_cola: number[];
  alertas: string[];
}

export interface DailyPlanInput {
  fecha: string;
  accounts: DailyPlanAccountInput[];
  queue: DailyPlanClipInput[];
  eff: EffectiveSettings;
  nowMs: number;
  /** Generador de azar para las horas (SPEC quitar-franjas v2):
   *  Math.random() por defecto; los tests pasan uno falso. */
  rng?: () => number;
}

function hhmmToMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return (h ?? 0) * 60 + (m ?? 0);
}

function minutesToHhmm(minutes: number): string {
  const hh = String(Math.floor(minutes / 60)).padStart(2, "0");
  const mm = String(Math.floor(minutes % 60)).padStart(2, "0");
  return `${hh}:${mm}`;
}

export function buildDailyPlan(input: DailyPlanInput): DailyPlan {
  const { fecha, accounts, queue, eff, nowMs } = input;
  const rng = input.rng ?? Math.random;
  const alertas: string[] = [];
  const publicaciones: DailyPlanPublication[] = [];
  const plannedClipPlatform = new Map<string, number>(); // `${clipId}|${panelPlatform}` -> minutos
  const plannedClipTimes = new Map<number, number[]>(); // clipId -> minutos planificados (todas las plataformas)
  const usedClipAccount = new Set<string>(); // `${clipId}|${accountId}` (nunca 2 veces en la misma cuenta)
  const plannedIds = new Set<number>();

  for (const acc of accounts) {
    const label = `${acc.label} (${acc.apiPlatform === "youtube" ? "YouTube" : acc.apiPlatform === "tiktok" ? "TikTok" : acc.apiPlatform === "instagram" ? "Instagram" : "Facebook"} · ${acc.language === "en" ? "EN" : "ES"})`;
    if (!acc.hasCredential) {
      alertas.push(`Cuenta ${label} sin credencial verificada: no se programa nada en ella.`);
      continue;
    }
    if (acc.pausedUntilMs !== null && nowMs < acc.pausedUntilMs) {
      alertas.push(`Cuenta ${label} en pausa por spam/límite hasta ${new Date(acc.pausedUntilMs).toISOString().slice(0, 16).replace("T", " ")} UTC: no se programa (pausa de 48 h).`);
      continue;
    }
    if (acc.restDay) {
      alertas.push(`Cuenta ${label}: hoy es su día libre (${WEEKDAY_NAMES_ES[acc.restWeekday] ?? ""}, 1 por semana, rotativo): no se programa.`);
      continue;
    }
    // Capacidad del día: ritmo normal (o máximo duro si se cumple
    // la regla 6), menos lo ya publicado hoy, con techo del
    // rolling 24 h (nunca >3 por cuenta en 24 h móviles; 6 en
    // YouTube, decisión 2026-11-23).
    const target = acc.canEscalate && acc.stage === "establecida" ? acc.max : acc.plan;
    let capacity = Math.max(0, target - acc.publishedToday);
    const rollingMax = acc.apiPlatform === "youtube"
      ? (eff.rolling_24h_max_youtube ?? eff.rolling_24h_max)
      : eff.rolling_24h_max;
    capacity = Math.min(capacity, Math.max(0, rollingMax - acc.publishedLast24h));
    if (acc.publishedToday > 0 && capacity === 0) {
      alertas.push(`Cuenta ${label}: plan del día cubierto (${acc.publishedToday}/${target}).`);
      continue;
    }
    if (capacity === 0) {
      alertas.push(`Cuenta ${label}: sin capacidad hoy (rolling 24 h o plan cubierto).`);
      continue;
    }
    if (acc.stage !== "establecida") {
      const stageLabel = acc.stage === "semana1" ? "semana 1 de calentamiento" : acc.stage === "semana2" ? "semana 2 de calentamiento" : "semana 3 de calentamiento";
      alertas.push(`Cuenta ${label} en ${stageLabel} (máx ${target}/día; ${acc.daysSinceFirst ?? 0} días, ${acc.strikes} strikes).`);
    } else if (acc.canEscalate && target === acc.max && acc.max > acc.plan) {
      alertas.push(`Cuenta ${label} a ritmo máximo (${acc.max}/día): >30 días sin restricciones y cola con viral alto.`);
    }
    // Asignar los mejores clips de la Cola que puedan salir por
    // esta cuenta (idioma + plataforma), en orden de Cola. Un clip
    // se asigna COMO MUCHO a una cuenta al día (regla 7: repartir
    // los clips entre plataformas, no mandar todo a una sola; el
    // mismo clip en 2 plataformas el mismo día queda para el
    // día siguiente, donde el gate exige ≥15 min entre ambas).
    let assigned = 0;
    // PRIORIDAD DE CAMPAÑA (2026-11-23, orden del usuario 18:37):
    // los clips de streamers con campaña >= $1/1k NETO ocupan
    // PRIMERO hasta campaign_priority_share_pct % de los slots de
    // esta cuenta (default 60%: con capacidad 3, 2 slots; con
    // capacidad 6, 4). El resto de slots se reparte después en
    // orden normal de Cola — y los clips de campaña que no cupieron
    // en la reserva también compiten ahí, así que la reserva es un
    // mínimo garantizado, no un techo. Esto solo cambia el ORDEN
    // dentro de la capacidad: topes, espaciamiento, calentamiento
    // y umbrales 45/80 no se tocan (la Cola ya solo trae clips que
    // los pasan). Con pct=0 no hay prioridad (plan como antes).
    const pct = eff.campaign_priority_share_pct ?? 60;
    const campaignReserve = Math.max(0, Math.min(capacity, Math.round((capacity * pct) / 100)));
    let campaignAssigned = 0;
    // CURSOR DE HORAS (SPEC quitar-franjas v2): el plan encadena
    // las publicaciones de esta cuenta con azar(90–180 min) desde
    // su última publicación real (o desde las 00:00 si no hay).
    // El plan es una vista, no el programador — el auto-enqueue
    // es quien pone el next_run_at real — pero las horas que
    // muestra siguen la misma regla.
    const dayStartMs = santiagoWallToUtcMs(fecha, "00:00");
    let cursorMin = acc.lastPublishedAtMs !== null && acc.lastPublishedAtMs !== undefined
      ? Math.max(0, Math.ceil((acc.lastPublishedAtMs + randomPublishDelayMs(rng) - dayStartMs) / 60000))
      : 0;
    const tryAssign = (clip: DailyPlanClipInput, campaignPass: boolean): boolean => {
      if (assigned >= capacity) return false;
      if (plannedIds.has(clip.id)) return false;
      if (clip.language !== acc.language) return false;
      if (!clip.platforms.includes(acc.platform)) return false;
      if (usedClipAccount.has(`${clip.id}|${acc.id}`)) return false;
      if (plannedClipPlatform.has(`${clip.id}|${acc.platform}`)) return false;
      // Hora aleatoria (SPEC quitar-franjas v2): el cursor avanza
      // 90–180 min por publicación. Si la hora cae fuera del día,
      // el clip pasa a la cola.
      const minutes = cursorMin;
      if (minutes >= 24 * 60) return false;
      const hora = minutesToHhmm(minutes);
      // Mismo clip en distintas plataformas: mínimo 15 min entre
      // una y otra (regla 4). Se verifica igual antes de devolver
      // el plan.
      const otherTimes = plannedClipTimes.get(clip.id) ?? [];
      if (otherTimes.some((t) => Math.abs(t - minutes) < eff.cross_platform_spacing_minutes)) return false;
      // Piso 90 min en la misma cuenta (SPEC quitar-franjas v2):
      // el cursor ya lo garantiza (cada paso es ≥90 min), pero se
      // verifica igual antes de devolver el plan.
      const myTimes = publicaciones.filter((p) => p.cuenta === label).map((p) => hhmmToMinutes(p.hora_local));
      if (myTimes.some((t) => Math.abs(t - minutes) < MIN_PUBLISH_SPACING_MINUTES)) return false;
      publicaciones.push({
        clip_id: clip.id,
        plataforma: acc.apiPlatform,
        cuenta: label,
        hora_local: hora,
        titulo: clip.title,
        descripcion: clip.description,
        hashtags: capHashtags(clip.hashtags, acc.apiPlatform, eff),
        motivo: `${campaignPass ? `Campaña ${clip.streamer ?? ""} (prioridad ${pct}%) · ` : ""}Top de la Cola (score ${clip.score}) · ${label} · ${hora} · ${acc.stage === "establecida" ? (target === acc.max && acc.max > acc.plan ? `ritmo máximo ${target}/día` : `ritmo normal ${target}/día`) : `calentamiento ${acc.stage} (${target}/día)`}`,
      });
      // La próxima publicación de esta cuenta sale 90–180 min
      // después de esta (azar por publicación).
      cursorMin = minutes + Math.round(randomPublishDelayMs(rng) / 60000);
      plannedClipPlatform.set(`${clip.id}|${acc.platform}`, minutes);
      plannedClipTimes.set(clip.id, [...otherTimes, minutes]);
      usedClipAccount.add(`${clip.id}|${acc.id}`);
      plannedIds.add(clip.id);
      assigned++;
      if (campaignPass) campaignAssigned++;
      return true;
    };
    // Pasada 1: solo campaña, hasta la reserva. Pasada 2: resto de
    // la Cola en orden (incluidos los de campaña que sobraron).
    for (const clip of queue) {
      if (campaignAssigned >= campaignReserve || assigned >= capacity) break;
      if (!clip.campaign) continue;
      tryAssign(clip, true);
    }
    for (const clip of queue) {
      if (assigned >= capacity) break;
      tryAssign(clip, false);
    }
    if (campaignAssigned > 0) {
      alertas.push(`Cuenta ${label}: ${campaignAssigned}/${assigned} slots con prioridad de campaña (${pct}% reservado a streamers con campaña >= $1/1k neto).`);
    }
    if (assigned === 0) {
      alertas.push(`Cuenta ${label}: sin clips en Cola para su idioma/plataforma hoy.`);
    }
  }

  const enCola = queue.filter((c) => !plannedIds.has(c.id)).map((c) => c.id);
  return { fecha, publicaciones, en_cola: enCola, alertas };
}

/** Carga los datos del tenant desde D1 y construye el plan.
 *  `fecha` es YYYY-MM-DD (America/Santiago); si no viene o no
 *  vale, es hoy. Nunca publica nada: solo calcula el plan. */
export async function buildDailyPlanForTenant(
  db: D1Database,
  tenant: Tenant,
  fechaRaw: string | null,
  nowMs = Date.now(),
): Promise<DailyPlan> {
  const today = santiagoDayKeyMs(nowMs);
  const fecha = fechaRaw && /^\d{4}-\d{2}-\d{2}$/.test(fechaRaw) ? fechaRaw : today;
  const settings = await getSettings(db, tenant);
  const eff = effectiveSettings(settings);
  const rows = await listApiAccounts(db, tenant);
  const queue = await listQueueClips(db, tenant);
  const topScore = queue.length > 0 ? Math.max(...queue.map((c) => c.score)) : 0;

  const accounts: DailyPlanAccountInput[] = [];
  for (const acc of rows) {
    const mat = maturityOut(acc, eff);
    // Una fila "meta" cubre Instagram Y Facebook: el plan se hace
    // por canal (plataforma del panel + idioma), no por fila.
    const panelPlatforms: PanelPlatform[] = acc.platform === "youtube"
      ? ["youtube_shorts"]
      : acc.platform === "tiktok"
        ? ["tiktok"]
        : acc.platform === "meta"
          ? ["instagram_reels", "facebook_reels"]
          : [];
    for (const panel of panelPlatforms) {
      const apiPlatform = panel === "youtube_shorts" ? "youtube" : panel === "tiktok" ? "tiktok" : panel === "instagram_reels" ? "instagram" : "facebook";
      const publishedTodayRow = await db.prepare(
        "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' AND posted_day = ?",
      )
        .bind(tenant, panel, acc.language === "en" ? "en" : "es", fecha)
        .first<{ n: number }>()
        .catch(() => null);
      const publishedLast24hRow = await db.prepare(
        "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' AND updated_at > ?",
      )
        .bind(tenant, panel, acc.language === "en" ? "en" : "es", nowMs - 24 * 3600 * 1000)
        .first<{ n: number }>()
        .catch(() => null);
      const lastPublishedRow = await db.prepare(
        "SELECT updated_at FROM jobs WHERE tenant_id = ? AND platform = ? AND language = ? AND state = 'posted' ORDER BY updated_at DESC LIMIT 1",
      )
        .bind(tenant, panel, acc.language === "en" ? "en" : "es")
        .first<{ updated_at: number }>()
        .catch(() => null);
      const seed = `acc-${acc.id}`;
      accounts.push({
        id: acc.id,
        platform: panel,
        apiPlatform,
        language: acc.language === "en" ? "en" : "es",
        label: acc.label,
        stage: mat.stage,
        plan: mat.effective_daily_plan,
        max: mat.effective_daily_max,
        daysSinceFirst: mat.days_since_first_published,
        strikes: mat.strikes,
        publishedToday: publishedTodayRow?.n ?? 0,
        publishedLast24h: publishedLast24hRow?.n ?? 0,
        lastPublishedAtMs: lastPublishedRow?.updated_at ?? null,
        hasCredential: acc.verify_ok === 1 && !!(acc.credential_enc || acc.credential),
        pausedUntilMs: accountPausedUntilMs(settings, acc.id),
        restDay: isRestDayEffective(seed, nowMs, settings, acc.id),
        restWeekday: restWeekdayEffective(seed, santiagoWeekKey(nowMs), settings, acc.id),
        // Regla 6 (aproximación medible en D1): >30 días sin
        // restricción + cola con viral alto (>=80). La tercera
        // pata (alcance de las últimas 7 subiendo) vive en
        // clip_stats y no se calcula aquí: si el alcance cae, la
        // bajada la marca el aviso de analíticas, no el plan.
        canEscalate: (mat.days_since_first_published ?? 0) > 30 && mat.strikes === 0 && topScore >= 80,
      });
    }
  }

  const clips = queue.map((c) => ({
    id: c.id,
    title: c.seo_title || c.title,
    description: c.seo_description || c.seo_title || c.title,
    hashtags: (c.seo_tags || "").split(/[,#\s]+/).map((t) => t.trim()).filter(Boolean),
    score: c.score,
    language: clipLanguage(c),
    platforms: parsePlatforms(c.platforms),
    // Prioridad de campaña (0041): el streamer viene resuelto en
    // listQueueClips desde influencers; la campaña es POR TENANT
    // (la lista de influencers es por tenant) y solo cuenta si la
    // tarifa neta es >= $1/1k (BenDaDonnn, $0,90 neto, queda fuera).
    streamer: c.streamer ?? null,
    campaign: isCampaignStreamer(c.streamer ?? null),
  }));

  return buildDailyPlan({ fecha, accounts, queue: clips, eff, nowMs });
}
