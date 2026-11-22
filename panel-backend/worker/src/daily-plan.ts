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
  isRestDayEffective,
  listApiAccounts,
  listQueueClips,
  maturityOut,
  parsePlatforms,
  restWeekdayEffective,
  santiagoDayKeyMs,
  santiagoWeekKey,
  slotTimeFor,
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
}

export interface DailyPlanClipInput {
  id: number;
  title: string;
  description: string;
  hashtags: string[];
  score: number;
  language: "es" | "en";
  platforms: PanelPlatform[];
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
}

function hhmmToMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return (h ?? 0) * 60 + (m ?? 0);
}

export function buildDailyPlan(input: DailyPlanInput): DailyPlan {
  const { fecha, accounts, queue, eff, nowMs } = input;
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
    // rolling 24 h (nunca >3 por cuenta en 24 h móviles).
    const target = acc.canEscalate && acc.stage === "establecida" ? acc.max : acc.plan;
    let capacity = Math.max(0, target - acc.publishedToday);
    capacity = Math.min(capacity, Math.max(0, eff.rolling_24h_max - acc.publishedLast24h));
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
    for (const clip of queue) {
      if (assigned >= capacity) break;
      if (plannedIds.has(clip.id)) continue;
      if (clip.language !== acc.language) continue;
      if (!clip.platforms.includes(acc.platform)) continue;
      if (usedClipAccount.has(`${clip.id}|${acc.id}`)) continue;
      if (plannedClipPlatform.has(`${clip.id}|${acc.platform}`)) continue;
      const hora = slotTimeFor(fecha, `acc-${acc.id}`, assigned, capacity);
      const minutes = hhmmToMinutes(hora);
      // Mismo clip en distintas plataformas: mínimo 15 min entre
      // una y otra (regla 4). Las franjas lo separan por horas,
      // pero se verifica igual antes de devolver el plan.
      const otherTimes = plannedClipTimes.get(clip.id) ?? [];
      if (otherTimes.some((t) => Math.abs(t - minutes) < eff.cross_platform_spacing_minutes)) continue;
      // Espaciamiento en la misma cuenta: 3 h (4 h YouTube). Las
      // ventanas elegidas (mediodía+noche, o las 3) lo cumplen;
      // si no, el clip pasa a la cola.
      const spacingMin = (acc.apiPlatform === "youtube" ? eff.youtube_spacing_hours : eff.spacing_hours) * 60;
      const myTimes = publicaciones.filter((p) => p.cuenta === label).map((p) => hhmmToMinutes(p.hora_local));
      if (myTimes.some((t) => Math.abs(t - minutes) < spacingMin)) continue;
      publicaciones.push({
        clip_id: clip.id,
        plataforma: acc.apiPlatform,
        cuenta: label,
        hora_local: hora,
        titulo: clip.title,
        descripcion: clip.description,
        hashtags: capHashtags(clip.hashtags, acc.apiPlatform, eff),
        motivo: `Top de la Cola (score ${clip.score}) · ${label} · franja ${hora} · ${acc.stage === "establecida" ? (target === acc.max && acc.max > acc.plan ? `ritmo máximo ${target}/día` : `ritmo normal ${target}/día`) : `calentamiento ${acc.stage} (${target}/día)`}`,
      });
      plannedClipPlatform.set(`${clip.id}|${acc.platform}`, minutes);
      plannedClipTimes.set(clip.id, [...otherTimes, minutes]);
      usedClipAccount.add(`${clip.id}|${acc.id}`);
      plannedIds.add(clip.id);
      assigned++;
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
  }));

  return buildDailyPlan({ fecha, accounts, queue: clips, eff, nowMs });
}
