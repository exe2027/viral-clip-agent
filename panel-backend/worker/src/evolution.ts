// Evolución neuroevolutiva de clips para campañas que pagan
// (2026-11-23, v1.4.0, migración 0042). Idea del usuario a partir
// del reel de @hieratico.ia (200 coches, 27 generaciones: se copia
// con pequeños errores a los que llegan más lejos).
//
// DOCTRINA — LEER ANTES DE TOCAR (decisión del usuario 19:36):
// La evolución es A/B testing, NO caos. El algoritmo de cada red
// premia retención y novedad DENTRO de un nicho y penaliza el
// contenido duplicado o repetitivo. Por eso:
//  1. Las variantes se generan OFFLINE en el cerebro y NO se
//     publican. Solo sale 1 ganadora por momento/plataforma, por
//     la vía de autopublicación ya existente (puertas 45/80,
//     límites por plataforma/cuenta y huecos de Chile intactos).
//  2. Las mutaciones son PEQUEÑAS y dentro de la misma plantilla
//     rubius, mismo nicho GTA 6 y mismos streamers: inicio/fin
//     (±1-3 s), hook, título y texto de miniatura. El estilo de
//     subtítulos NO es eje de mutación (queda el del clip base,
//     rubius): solo cambiar un efecto o el estilo de subtítulos
//     NO hace un clip distinto (regla del usuario), así que cada
//     variante es un corte distinto REAL (start o end difieren
//     >= 1 s del control; lo comprueba isDistinctCut).
//  3. Publicar muchas variantes casi iguales sería lo
//     contraproducente: quemaría huecos, parecería duplicado y
//     rompería la consistencia de la cuenta. Las perdedoras no se
//     publican ni se reenvían a otra campaña (un post = UNA
//     campaña que paga).
//  4. Fitness en dos fases: (a) cerebro (viralidad/SEO, puertas
//     45/80); (b) REAL a 24-48 h (vistas aprobadas en Content
//     Rewards, vistas/retención de analíticas). El fitness real
//     manda sobre el del cerebro en la siguiente generación.
//  5. Sin datos reales NUNCA se inventa un fitness: la variante
//     queda has_real_data=0. La generación 1 publica la línea
//     base (control); las mutantes quedan como candidatas SIN
//     PROBAR, no como perdedoras. `loser` exige datos reales o
//     veredicto del validador. Los veredictos rechazados del
//     validador NO se guardan sin revisar fotogramas.
//
// Este módulo es PURO (sin D1, sin red): así se prueba sin mocks.
// La persistencia vive en db.ts (helpers evo_*, que degradan a
// vacío si la tabla 0042 aún no está migrada) y las rutas en
// index.ts (GET /api/evolution/status, POST /api/evolution/dry-run
// y POST /api/evolution/record-real-fitness, con sesión del panel).
// El dry-run NO publica ni envía nada: genera la población,
// selecciona y persiste. El primer envío real sigue pendiente del
// usuario (OAuth, API key y token del panel, cobro en Whop).
// RIESGO BAN/SHADOWBAN: investigado antes de desplegar, ver
// panel-backend/EVOLUTION-0042.md §8 (tabla por parte + mitigación
// + 4 puntos abiertos antes del primer envío real). Resumen: la
// evolución no aumenta el volumen publicado (1 ganadora por
// momento/plataforma) y el dry-run bloquea republicar un momento
// ya publicado en esa plataforma (409).

/** Tamaño de la población por generación (empieza con 6, no 200:
 *  1 control + 5 mutantes). Máximo 8. */
export const EVO_POPULATION_SIZE = 6;
export const EVO_MAX_POPULATION_SIZE = 8;
/** Mejores que se copian con mutaciones para la siguiente generación. */
export const EVO_KEEP_WINNERS = 3;
/** Diferencia mínima de corte (s) para que una variante sea un
 *  clip distinto del control (regla del usuario). */
export const EVO_MIN_CUT_DIFF_SEC = 1;
/** Duración del clip resultante (s): dentro del rango probado del
 *  proyecto (máx. 60 s, sin mínimo antiguo). */
export const EVO_MIN_DURATION_SEC = 15;
export const EVO_MAX_DURATION_SEC = 60;
/** Ejes de mutación permitidos (pequeños, dentro de la plantilla). */
export const EVO_MUTATION_AXES = ["hook_start", "end", "title", "thumb"] as const;
export type EvoMutationAxis = (typeof EVO_MUTATION_AXES)[number];

export const EVO_VARIANT_STATUSES = ["candidate", "winner", "loser", "published", "blocked"] as const;
export type EvoVariantStatus = (typeof EVO_VARIANT_STATUSES)[number];

/** Parámetros de corte de una variante (lo que el cerebro usa
 *  para renderizarla: mismo momento, otro corte). */
export interface EvoVariantParams {
  start_sec: number;
  end_sec: number;
  /** Segundos desde el inicio del clip donde cae el hook. */
  hook_offset_s: number;
  title: string;
  thumb_text: string;
  subtitle_style: string;
}

export interface EvoMutation {
  axis: EvoMutationAxis;
  detail: string;
  parent_variant_id: number | null;
}

/** Clip base (momento ganador) del que nace la población. */
export interface EvoBaseClip {
  start_sec: number;
  end_sec: number;
  title: string;
  seo_title: string;
  thumb_text: string;
  subtitle_style: string;
}

/** Una variante generada (borrador, aún sin persistir). */
export interface EvoVariantDraft {
  index: number;
  params: EvoVariantParams;
  mutations: EvoMutation[];
  is_control: boolean;
  /** Puntaje del cerebro para ESTA variante (null = sin datos:
   *  solo el cerebro puede aportarlo tras validar su render). */
  brain_virality: number | null;
  brain_seo: number | null;
  has_real_data: boolean;
}

/** Variante con fitness, para selección (lo que lee la ruta). */
export interface EvoScoredVariant {
  index: number;
  brain_virality: number | null;
  brain_seo: number | null;
  /** Fitness real: vistas aprobadas en Content Rewards; si no
   *  hay, vistas de la plataforma; null = sin datos reales. */
  real_fitness: number | null;
  has_real_data: boolean;
}

/** PRNG determinista (mulberry32): la misma semilla genera la
 *  misma población, para reproducir una generación en tests y en
 *  el panel sin depender de Math.random. */
export function evoSeededRandom(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

function pick<T>(rnd: () => number, arr: readonly T[]): T {
  return arr[Math.floor(rnd() * arr.length)] as T;
}

/** Título "hook primero": mueve el segmento tras ":" o "—" al
 *  frente. Si no hay segmento, devuelve el título tal cual. */
export function hookFirstTitle(title: string): string {
  const m = /^(.*?)\s*[:—–-]\s*(.+)$/.exec(title.trim());
  if (!m || !m[1] || !m[2]) return title;
  return `${m[2].trim()} — ${m[1].trim()}`;
}

/** Título corto: primeras 7 palabras. */
export function shortTitle(title: string): string {
  const words = title.trim().split(/\s+/);
  return words.length > 7 ? words.slice(0, 7).join(" ") : title;
}

function shortThumb(thumb: string): string {
  const words = thumb.trim().split(/\s+/);
  return words.length > 3 ? words.slice(0, 3).join(" ") : thumb;
}

/** ¿Es `b` un corte distinto REAL de `a`? Solo el corte cuenta:
 *  cambiar solo el título, la miniatura o el estilo de subtítulos
 *  NO hace un clip distinto (regla del usuario). */
export function isDistinctCut(a: EvoVariantParams, b: EvoVariantParams): boolean {
  return (
    Math.abs(a.start_sec - b.start_sec) >= EVO_MIN_CUT_DIFF_SEC ||
    Math.abs(a.end_sec - b.end_sec) >= EVO_MIN_CUT_DIFF_SEC
  );
}

/** Genera la población de una generación. `parents` son los
 *  params de las ganadoras de la generación anterior (vacío en
 *  generación 1: se parte del clip base). La variante 0 es SIEMPRE
 *  el control (línea base sin mutar): es contra lo que se mide
 *  todo. Cada mutante cambia el corte (start o end) + como mucho
 *  un eje de texto: mutaciones pequeñas, atribuibles (si gana una
 *  variante se sabe QUÉ cambió). */
export function generatePopulation(
  base: EvoBaseClip,
  generation: number,
  seed: number,
  parents: EvoVariantParams[] = [],
): EvoVariantDraft[] {
  const rnd = evoSeededRandom(seed * 1000 + generation * 97 + 13);
  const baseParams: EvoVariantParams = {
    start_sec: base.start_sec,
    end_sec: base.end_sec,
    hook_offset_s: 0,
    title: base.seo_title || base.title,
    thumb_text: base.thumb_text,
    subtitle_style: base.subtitle_style,
  };
  const drafts: EvoVariantDraft[] = [
    {
      index: 0,
      params: { ...baseParams },
      mutations: [],
      is_control: true,
      brain_virality: null,
      brain_seo: null,
      has_real_data: false,
    },
  ];
  for (let i = 1; i < EVO_POPULATION_SIZE; i++) {
    const parent = parents.length > 0 ? parents[(i - 1) % parents.length]! : baseParams;
    const params: EvoVariantParams = { ...parent };
    const mutations: EvoMutation[] = [];
    // Eje de corte (OBLIGATORIO: sin él no es un clip distinto).
    const cutAxis = pick(rnd, ["hook_start", "end"] as const);
    if (cutAxis === "hook_start") {
      const delta = pick(rnd, [-2, -1, 1, 2]);
      const next = clamp(parent.start_sec + delta, 0, parent.end_sec - EVO_MIN_DURATION_SEC);
      params.start_sec = next;
      params.hook_offset_s = clamp(parent.hook_offset_s - delta, 0, 3);
      mutations.push({ axis: "hook_start", detail: `inicio ${delta > 0 ? "+" : ""}${delta}s`, parent_variant_id: null });
    } else {
      const delta = pick(rnd, [-3, -2, -1, 1, 2, 3]);
      const dur = clamp(parent.end_sec - parent.start_sec + delta, EVO_MIN_DURATION_SEC, EVO_MAX_DURATION_SEC);
      params.end_sec = parent.start_sec + dur;
      mutations.push({ axis: "end", detail: `fin ${delta > 0 ? "+" : ""}${delta}s (duración ${dur}s)`, parent_variant_id: null });
    }
    // Eje de texto (OPCIONAL, como mucho uno por variante).
    if (rnd() < 0.6) {
      const textAxis = pick(rnd, ["title", "thumb"] as const);
      if (textAxis === "title") {
        const next = rnd() < 0.5 ? hookFirstTitle(parent.title) : shortTitle(parent.title);
        if (next !== parent.title) {
          params.title = next;
          mutations.push({ axis: "title", detail: "título hook-primero/corto", parent_variant_id: null });
        }
      } else {
        const next = rnd() < 0.5 ? shortThumb(parent.thumb_text) : parent.thumb_text.toUpperCase();
        if (next !== parent.thumb_text && next.trim()) {
          params.thumb_text = next;
          mutations.push({ axis: "thumb", detail: "miniatura corta/mayúsculas", parent_variant_id: null });
        }
      }
    }
    // Garantía: si el azar dejó el corte igual al padre, se
    // fuerza +1 s en el inicio (nunca se genera un "distinto"
    // que no lo es).
    if (!isDistinctCut(parent, params)) {
      params.start_sec = clamp(parent.start_sec + 1, 0, parent.end_sec - EVO_MIN_DURATION_SEC);
      params.hook_offset_s = clamp(parent.hook_offset_s - 1, 0, 3);
      mutations.push({ axis: "hook_start", detail: "inicio +1s (garantía de corte distinto)", parent_variant_id: null });
    }
    drafts.push({ index: i, params, mutations, is_control: false, brain_virality: null, brain_seo: null, has_real_data: false });
  }
  return drafts;
}

/** ¿Pasa la variante las puertas del cerebro? Sin puntaje del
 *  cerebro no se puede afirmar: null = "sin datos", no aprobado. */
export function passesBrainGates(
  virality: number | null,
  seo: number | null,
  minVirality: number,
  minSeo: number,
): boolean {
  return virality !== null && seo !== null && virality >= minVirality && seo >= minSeo;
}

/** Fitness real: vistas aprobadas en Content Rewards mandan; si
 *  aún no hay, vistas de la plataforma; sin ninguna, null (nunca
 *  se inventa). */
export function realFitness(approvedViews: number | null, views: number | null): number | null {
  if (approvedViews !== null && approvedViews !== undefined) return approvedViews;
  if (views !== null && views !== undefined) return views;
  return null;
}

function brainRank(v: EvoScoredVariant): [number, number] {
  return [v.brain_virality ?? -1, v.brain_seo ?? -1];
}

/**
 * Selección de ganadoras. Orden: (1) fitness REAL desc — el real
 * manda sobre el cerebro; (2) viralidad del cerebro desc; (3) SEO
 * desc. Solo entran las que pasan las puertas 45/80 con puntaje
 * del cerebro; las que no, van a `blocked` con motivo. Las
 * elegibles que no entran en el top son `losers` SOLO si se
 * pudieron ordenar con datos (reales o del cerebro): sin puntaje
 * del cerebro ni datos reales no hay ranking y quedan como
 * `candidates` (sin probar), nunca como perdedoras inventadas.
 */
export function selectWinners(
  variants: EvoScoredVariant[],
  keep: number,
  minVirality: number,
  minSeo: number,
): { winners: EvoScoredVariant[]; losers: EvoScoredVariant[]; blocked: EvoScoredVariant[]; untested: EvoScoredVariant[] } {
  const blocked: EvoScoredVariant[] = [];
  const rankable: EvoScoredVariant[] = [];
  const untested: EvoScoredVariant[] = [];
  for (const v of variants) {
    if (v.brain_virality === null || v.brain_seo === null) {
      untested.push(v);
      continue;
    }
    if (!passesBrainGates(v.brain_virality, v.brain_seo, minVirality, minSeo)) {
      blocked.push(v);
      continue;
    }
    rankable.push(v);
  }
  rankable.sort((a, b) => {
    const realA = a.real_fitness ?? -1;
    const realB = b.real_fitness ?? -1;
    if (realB !== realA) return realB - realA;
    const [va, sa] = brainRank(a);
    const [vb, sb] = brainRank(b);
    if (vb !== va) return vb - va;
    if (sb !== sa) return sb - sa;
    return a.index - b.index;
  });
  return {
    winners: rankable.slice(0, keep),
    losers: rankable.slice(keep),
    blocked,
    untested,
  };
}

/** Motivo por el que una variante NO se puede publicar (null =
 *  publicable). Todo lo que frena queda con motivo exacto, nunca
 *  en silencio. */
export function publishBlockReason(input: {
  status: EvoVariantStatus;
  isDistinctFromBase: boolean;
  campaignEligible: boolean;
  campaignEligibilityReason: string | null;
  qualityGateReason: string | null;
  alreadySubmittedToOtherCampaign: boolean;
  alreadySubmittedToThisCampaign: boolean;
}): string | null {
  if (input.status !== "winner") return `solo se publica la ganadora (esta variante está ${input.status})`;
  if (!input.isDistinctFromBase) return "la ganadora no es un corte distinto del clip base (mismo inicio/fin: solo cambió texto/efecto)";
  if (!input.campaignEligible) return input.campaignEligibilityReason ?? "campaña no elegible (fama < 50.000 o tarifa neta < $1/1k)";
  if (input.qualityGateReason) return input.qualityGateReason;
  if (input.alreadySubmittedToOtherCampaign) return "este post ya tiene un envío a OTRA campaña que paga (un post = una campaña)";
  if (input.alreadySubmittedToThisCampaign) return "este post ya tiene un envío registrado a esta campaña (no se duplica)";
  return null;
}
