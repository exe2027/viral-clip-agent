// Acceso a D1: mismo esquema que app.db (migraciones 0001-0010) +
// 0011 (jobs, notifications, sessions, columnas cifradas) + 0012
// (instalación única multi-tenant: tenant_id en cada fila).
// Consultas con prepared statements; sin ORM para que el Worker no
// dependa de drizzle-orm/d1 en runtime (menos piezas, mismo SQL).
//
// AISLAMIENTO (decisión 17 nov 2026, avc.pages.dev): TODA consulta
// de datos de usuario filtra por tenant_id = el tenant de la sesión.
// Ninguna función de este archivo devuelve filas de otro tenant.

import type { D1Database, D1PreparedStatement } from "./cloudflare";
import type { Tenant } from "./auth";
import { currentBudgetGuard, D1_DAILY_ACCOUNT_LIMIT, type BudgetGuard } from "./budget";

export type PlatformKey = "youtube" | "tiktok" | "instagram" | "facebook";
// Nombres de plataforma en clips.platforms / publish_states (panel v17).
export type PanelPlatform = "youtube_shorts" | "tiktok" | "instagram_reels" | "facebook_reels";

// ---------------------------------------------------------------------------
// IDIOMA DEL CLIP (2026-11-18, DOS COLAS SEPARADAS): "es" | "en".
// Un clip en inglés se publica SOLO en canales en inglés y uno en
// español SOLO en canales en español; son colas diferentes. Los
// clips heredados sin idioma son "es" (default de la migración
// 0019): nunca se inventa un "en".
// ---------------------------------------------------------------------------
export type ClipLanguage = "es" | "en";

/** Normaliza el idioma de un clip: "en" solo si es "en"; cualquier
 *  otro valor (incluidos NULL y filas viejas) es "es". */
export function clipLanguage(clip: { language?: string | null }): ClipLanguage {
  return clip.language === "en" ? "en" : "es";
}

/** Nombre del idioma para mensajes ("español" / "inglés"). */
export function languageName(lang: string): string {
  return lang === "en" ? "inglés" : "español";
}

/** Valida un idioma de entrada (POST/PATCH): default "es" cuando no
 *  viene; cualquier otro valor que no sea es/en es error (400). */
export function parseClipLanguage(v: unknown): ClipLanguage | string {
  if (v === undefined) return "es";
  if (typeof v !== "string") return "Idioma inválido: usa es o en.";
  const lang = v.trim().toLowerCase();
  if (lang !== "es" && lang !== "en") return "Idioma inválido: usa es o en.";
  return lang;
}

/** Nombre de plataforma para mensajes de la puerta de idioma. */
export function panelPlatformLabel(p: PanelPlatform): string {
  switch (p) {
    case "youtube_shorts": return "YouTube";
    case "tiktok": return "TikTok";
    case "instagram_reels": return "Instagram";
    case "facebook_reels": return "Facebook";
  }
}

export const PANEL_PLATFORMS: PanelPlatform[] = [
  "youtube_shorts",
  "tiktok",
  "instagram_reels",
  "facebook_reels",
];

export function toApiPlatform(p: PanelPlatform): PlatformKey {
  switch (p) {
    case "youtube_shorts":
      return "youtube";
    case "tiktok":
      return "tiktok";
    case "instagram_reels":
      return "instagram";
    case "facebook_reels":
      return "facebook";
  }
}

/** URL pública de evidencia por plataforma (solo con id real).
 *  TikTok: el handle sale de la cuenta (tabla accounts); sin handle
 *  NO se inventa uno: la URL sin @usuario
 *  (tiktok.com/video/<id>) también resuelve. Antes hardcodeaba
 *  @elclipdegta6 para los dos idiomas: la evidencia de un clip EN
 *  apuntaba al canal ES. */
export function evidenceUrl(platform: PanelPlatform, platformId: string, handle?: string | null): string {
  switch (platform) {
    case "youtube_shorts":
      return `https://www.youtube.com/shorts/${platformId}`;
    case "tiktok": {
      const h = (handle ?? "").trim().replace(/^@+/, "");
      return h ? `https://www.tiktok.com/@${h}/video/${platformId}` : `https://www.tiktok.com/video/${platformId}`;
    }
    case "instagram_reels":
      return `https://www.instagram.com/reel/${platformId}/`;
    case "facebook_reels":
      return `https://www.facebook.com/reel/${platformId}/`;
  }
}

export interface ClipRow {
  id: number;
  tenant_id: Tenant;
  source_id: number | null;
  influencer_id: number | null;
  // Streamer visible en la Cola (2026-11-19): nombre resuelto en
  // listQueueClips desde influencers; null = sin asignar. No es
  // columna de clips: se rellena al listar, nunca se escribe.
  streamer?: string | null;
  title: string;
  start_sec: number;
  end_sec: number;
  score: number;
  status: string;
  subtitle_style: string;
  seo_title: string;
  seo_description: string;
  seo_tags: string;
  thumb_text: string; // texto de miniatura (columna thumb_text)
  platforms: string; // JSON array
  scheduled_for: string | null;
  published_at: number | null;
  file_deleted: number;
  // 0039: retención 24 h del MP4 en R2 (orden 2026-11-21 21:28).
  // Epoch-ms en que vence la retención ("subidos") tras publicar en
  // todas las plataformas; NULL = sin plazo (no completado aún, o
  // base sin migrar). Opcional: las bases viejas y los fixtures de
  // test sin la columna devuelven undefined y se trata como NULL.
  // El borrado solo borra el archivo R2 y marca file_deleted=1;
  // nunca toca la fila ni sus jobs/links.
  r2_delete_after?: number | null;
  created_at: number;
  updated_at: number;
  captions: string;
  viral: string;
  seo_overrides: string;
  preview_seen: number;
  archived: number;
  origin: string;
  publish_states: string; // JSON {platform: PlatformPublishEntry}
  // Regla 2026-11-17 22:10: SIEMPRE subtítulos completos.
  // publish_blocked=1: sin subtítulos, no se publica (ver jobs.ts).
  publish_blocked: number;
  needs_human_transcription: number;
  subtitles_complete: number;
  // 0016: key R2 del MP4 subido con PUT /api/clips/:id/video
  // (clips/<tenant>/<id>/<nombre>.mp4); "" = sin MP4 subido aún.
  video_r2_key: string;
  // 0024: orden manual de la Cola desde Telegram (v3, idea 49).
  // NULL = automático (por score); con valor, el clip sube dentro
  // de su segmento sin tocar el score ni la fórmula del cerebro.
  manual_rank?: number | null;
  // Categoría del clip (columna clips.game): este panel es SOLO
  // GTA VI (gate 2026-11-18, ver isGtaViGame).
  game: string;
  // Idioma del clip (columna clips.language, migración 0019,
  // DOS COLAS SEPARADAS 2026-11-18): "es" | "en". Decide en qué
  // cola aparece y en qué cuentas se puede publicar.
  language: string;
  // Clave de origen (columna clips.source_key, migración 0044,
  // spec 2026-11-24): id del video de YouTube para clips de
  // YouTube (p. ej. "NrNdw_Y6_bI"), "" para Twitch. `source_id`
  // es entero y un id de YouTube no cabe ahí. No vacío ⟺ YouTube.
  source_key?: string;
}

// ---------------------------------------------------------------------------
// PUNTUACIÓN IA (2026-11-18): el cerebro (Gemini/brain) rellena la
// puntuación al encolar y el panel la ENSEÑA. Contrato de campos —
// el script de encolado del cerebro (enqueue_clip.py) manda estos
// campos en POST /api/clips; PATCH los afina. Nada de esto se
// inventa: sin dato, null = "sin medir" en el front.
//
// Columna clips.viral (JSON) — forma canónica guardada:
// {
//   "score_kind": "ia" | "manual",   // 'ia' = lo rellenó el cerebro/Gemini
//   "viral_score": 53.8,             // viralidad total (decimal; la columna
//                                    // clips.score es el entero redondeado)
//   "viral_breakdown": { "chat": 5.9, "hype": 5.9, ... },
//                                    // componentes de la viralidad, tal cual
//                                    // vengan del cerebro (claves libres)
//   "hype_score": 5.9,               // ráfaga de chat (msgs/10 s vs baseline)
//   "priority": 87.3,                // prioridad del cerebro (fórmula
//                                    // INTOCABLE, vive en el cerebro)
//   "seo_scores": { "youtube_shorts": 92, "tiktok": 94,
//                   "instagram_reels": 94, "facebook_reels": 94 },
//   "platforms": { "youtube_shorts": { "score": 92 }, ... }
//                                    // forma legacy que lee seoForPlatform;
//                                    // se escribe igual que seo_scores
//   "quality_gate": { "decision": "aprobado", "pasa": true,
//                     "fallos": [], "motivo": "" }
//                                    // puerta de calidad B4 (2026-11-20):
//                                    // veredicto del cerebro sobre el
//                                    // vídeo final; publishGate lo lee.
// }
// Campos aceptados en POST/PATCH (además del objeto `viral` entero):
//   seo_scores, viral_score, viral_breakdown, hype_score, priority,
//   score_kind. `score_kind` por defecto es "ia" cuando vienen datos
//   de puntuación y "manual" cuando no vienen.
// ---------------------------------------------------------------------------
export const SEO_PLATFORMS = ["youtube_shorts", "tiktok", "instagram_reels", "facebook_reels"] as const;
export type SeoPlatform = (typeof SEO_PLATFORMS)[number];

export interface QualityGateVerdict {
  decision: string;
  pasa: boolean | null;
  fallos?: string[];
  fallos_duros?: string[];
  motivo?: string;
  criterios?: Record<string, { pasa: boolean; duro: boolean; motivo?: string }>;
}

export interface ViralData {
  score_kind?: "ia" | "manual";
  viral_score?: number;
  viral_breakdown?: Record<string, unknown>;
  hype_score?: number;
  priority?: number;
  seo_scores?: Partial<Record<SeoPlatform, number>>;
  platforms?: Partial<Record<SeoPlatform, { score?: number }>>;
  // Rescate por gritos del streamer (2026-11-18, decisión del
  // usuario): clip con viralidad revisada 45-49 que entró a la Cola
  // BLOQUEADO para revisión humana, nunca auto-publicable. Se guarda
  // aquí para que el Editor y publishGate digan por qué está frenado.
  rescue?: { viral_revisado: number; shouts: number };
  // Puerta de calidad B4 (2026-11-20): veredicto de la validación
  // final del cerebro sobre el vídeo renderizado (6 criterios:
  // formato 9:16, webcam, hook, título, miniatura, audio). Lo
  // manda el cerebro en POST /api/clips; publishGate lo lee para
  // no crear jobs de clips sin validar o defectuosos. El
  // SIGNIFICADO (criterios, decisiones, umbral 30%) vive SOLO en
  // quality-gate.ts; aquí solo se valida la FORMA para no duplicar
  // literales ni crear un ciclo de imports.
  quality_gate?: QualityGateVerdict;
  // Entradas exactas del scorer SEO (2026-11-24, auto-seo): las
  // guarda el cerebro al cortar para que el loop de revalidación
  // pueda reproducir el score del corte sin el paquete. El gate
  // no las lee; solo son trazabilidad del score.
  seo_inputs?: {
    hashtags: string[];
    visible: string | null;
    game_verified: boolean;
    keyword: string;
    lang: string;
  };
  // Procedencia del seo_scores guardado: "corte" (medido al
  // cortar), "backfill_exacto" (reproducido con seo_inputs),
  // "backfill_aproximado" (derivado sin seo_inputs).
  seo_source?: "corte" | "backfill_exacto" | "backfill_aproximado";
  // Retención por título honesto (2026-11-26, spec títulos R3.4):
  // motivo por el que el clip quedó retenido para revisión humana
  // (p. ej. "Sin título honesto: retención para revisión"). Vive
  // aquí, junto al resto del viral, y viaja con el record que ya
  // se escribe (nunca es una escritura D1 aparte). `null` en un
  // patch significa BORRARLO (ver mergeViralJson): el merge de
  // viral no tiene otra forma de quitar una clave.
  hold_reason?: string | null;
}

/** Parsea clips.viral (string JSON) a objeto; si está vacío o roto, {}. */
export function parseViralJson(raw: string | null | undefined): ViralData {
  if (!raw) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
    return parsed as ViralData;
  } catch {
    return {};
  }
}

function isScoreNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 100;
}

/** Valida seo_scores: objeto {plataforma: 0–100}. Claves fuera de
 *  las 4 del panel son 400. Devuelve el objeto limpio o el error. */
export function parseSeoScoresInput(v: unknown): Partial<Record<SeoPlatform, number>> | string {
  if (typeof v !== "object" || v === null || Array.isArray(v)) {
    return "seo_scores debe ser un objeto {plataforma: score 0–100}.";
  }
  const out: Partial<Record<SeoPlatform, number>> = {};
  for (const [key, val] of Object.entries(v as Record<string, unknown>)) {
    const k = key.trim().toLowerCase();
    if (!(SEO_PLATFORMS as readonly string[]).includes(k)) {
      return `Plataforma inválida en seo_scores: usa youtube_shorts, tiktok, instagram_reels o facebook_reels. Recibido: "${key}".`;
    }
    if (!isScoreNumber(val)) {
      return `seo_scores.${k} debe ser un número entre 0 y 100.`;
    }
    out[k as SeoPlatform] = val as number;
  }
  return out;
}

/** Valida la FORMA de un veredicto de calidad B4 (sin juzgar su
 *  significado: eso es de quality-gate.ts). Devuelve el objeto
 *  limpio o el mensaje de error. `decision` es string libre aquí:
 *  un valor fuera de las 5 conocidas no es 400, es un veredicto
 *  ilegible que el gate trata como "sin veredicto válido". */
export function parseQualityGateInput(v: unknown): QualityGateVerdict | string {
  if (typeof v !== "object" || v === null || Array.isArray(v)) {
    return "quality_gate debe ser un objeto {decision, pasa, fallos, motivo}.";
  }
  const q = v as Record<string, unknown>;
  if (typeof q.decision !== "string" || q.decision.trim() === "") {
    return "quality_gate.decision debe ser una cadena no vacía.";
  }
  if (q.pasa !== true && q.pasa !== false && q.pasa !== null) {
    return "quality_gate.pasa debe ser true, false o null.";
  }
  const out: QualityGateVerdict = { decision: q.decision, pasa: q.pasa };
  if (q.fallos !== undefined) {
    if (!Array.isArray(q.fallos) || q.fallos.some((f) => typeof f !== "string")) {
      return "quality_gate.fallos debe ser una lista de cadenas.";
    }
    out.fallos = q.fallos as string[];
  }
  if (q.fallos_duros !== undefined) {
    if (!Array.isArray(q.fallos_duros) || q.fallos_duros.some((f) => typeof f !== "string")) {
      return "quality_gate.fallos_duros debe ser una lista de cadenas.";
    }
    out.fallos_duros = q.fallos_duros as string[];
  }
  if (q.motivo !== undefined) {
    if (typeof q.motivo !== "string") return "quality_gate.motivo debe ser una cadena.";
    out.motivo = q.motivo;
  }
  if (q.criterios !== undefined) {
    if (typeof q.criterios !== "object" || q.criterios === null || Array.isArray(q.criterios)) {
      return "quality_gate.criterios debe ser un objeto.";
    }
    const crit: Record<string, { pasa: boolean; duro: boolean; motivo?: string }> = {};
    for (const [k, entry] of Object.entries(q.criterios as Record<string, unknown>)) {
      if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
        return `quality_gate.criterios.${k} debe ser un objeto {pasa, duro}.`;
      }
      const e = entry as Record<string, unknown>;
      if (typeof e.pasa !== "boolean" || typeof e.duro !== "boolean") {
        return `quality_gate.criterios.${k} debe llevar pasa y duro booleanos.`;
      }
      crit[k] = { pasa: e.pasa, duro: e.duro, ...(typeof e.motivo === "string" ? { motivo: e.motivo } : {}) };
    }
    out.criterios = crit;
  }
  return out;
}

/** Valida los campos de puntuación de un cuerpo POST/PATCH.
 *  Devuelve el parcial listo para fusionar en clips.viral, o el
 *  mensaje de error. `viral` (objeto entero) se fusiona tal cual
 *  con los campos sueltos; los sueltos mandan sobre `viral`. */
export function parseViralInput(body: Record<string, unknown>): Partial<ViralData> | string {
  const out: Partial<ViralData> = {};
  if (body.viral !== undefined) {
    if (typeof body.viral !== "object" || body.viral === null || Array.isArray(body.viral)) {
      return "viral debe ser un objeto con los datos de puntuación.";
    }
    const v = body.viral as Record<string, unknown>;
    if (v.seo_scores !== undefined) {
      const parsed = parseSeoScoresInput(v.seo_scores);
      if (typeof parsed === "string") return parsed;
      out.seo_scores = parsed;
    }
    if (v.score_kind !== undefined) {
      if (v.score_kind !== "ia" && v.score_kind !== "manual") return "score_kind debe ser \"ia\" o \"manual\".";
      out.score_kind = v.score_kind;
    }
    const numField = (key: "viral_score" | "hype_score" | "priority", max: number): string | null => {
      if (v[key] === undefined) return null;
      if (typeof v[key] !== "number" || !Number.isFinite(v[key] as number) || (v[key] as number) < 0 || (v[key] as number) > max) {
        return `${key} debe ser un número entre 0 y ${max}.`;
      }
      out[key] = v[key] as number;
      return null;
    };
    for (const [key, max] of [["viral_score", 100], ["hype_score", 100], ["priority", 100]] as const) {
      const e = numField(key, max);
      if (e) return e;
    }
    if (v.viral_breakdown !== undefined) {
      if (typeof v.viral_breakdown !== "object" || v.viral_breakdown === null || Array.isArray(v.viral_breakdown)) {
        return "viral_breakdown debe ser un objeto con los componentes de la viralidad.";
      }
      out.viral_breakdown = v.viral_breakdown as Record<string, unknown>;
    }
    if (v.quality_gate !== undefined) {
      const parsed = parseQualityGateInput(v.quality_gate);
      if (typeof parsed === "string") return parsed;
      out.quality_gate = parsed;
    }
    if (v.platforms !== undefined) {
      if (typeof v.platforms !== "object" || v.platforms === null || Array.isArray(v.platforms)) {
        return "viral.platforms debe ser un objeto {plataforma: {score}}.";
      }
      const plats: Partial<Record<SeoPlatform, { score?: number }>> = {};
      for (const [key, val] of Object.entries(v.platforms as Record<string, unknown>)) {
        const k = key.trim().toLowerCase();
        if (!(SEO_PLATFORMS as readonly string[]).includes(k)) {
          return `Plataforma inválida en viral.platforms: usa youtube_shorts, tiktok, instagram_reels o facebook_reels.`;
        }
        if (typeof val !== "object" || val === null) return `viral.platforms.${k} debe ser {score}.`;
        const s = (val as Record<string, unknown>).score;
        if (s !== undefined && !isScoreNumber(s)) return `viral.platforms.${k}.score debe ser un número entre 0 y 100.`;
        plats[k as SeoPlatform] = { score: s as number | undefined };
      }
      out.platforms = plats;
    }
    // Entradas del scorer SEO (2026-11-24, auto-seo): se guardan
    // tal cual para reproducir el score del corte en el backfill.
    if (v.seo_inputs !== undefined) {
      if (typeof v.seo_inputs !== "object" || v.seo_inputs === null || Array.isArray(v.seo_inputs)) {
        return "viral.seo_inputs debe ser un objeto con las entradas del scorer SEO.";
      }
      const si = v.seo_inputs as Record<string, unknown>;
      if (!Array.isArray(si.hashtags) || (si.hashtags as unknown[]).some((h) => typeof h !== "string")) {
        return "viral.seo_inputs.hashtags debe ser una lista de strings.";
      }
      if (si.visible !== undefined && si.visible !== null && typeof si.visible !== "string") {
        return "viral.seo_inputs.visible debe ser un string o null.";
      }
      if (typeof si.game_verified !== "boolean") {
        return "viral.seo_inputs.game_verified debe ser booleano.";
      }
      if (typeof si.keyword !== "string" || typeof si.lang !== "string") {
        return "viral.seo_inputs.keyword y lang deben ser strings.";
      }
      out.seo_inputs = {
        hashtags: si.hashtags as string[],
        visible: (si.visible as string | null) ?? null,
        game_verified: si.game_verified as boolean,
        keyword: si.keyword as string,
        lang: si.lang as string,
      };
    }
    if (v.seo_source !== undefined) {
      if (v.seo_source !== "corte" && v.seo_source !== "backfill_exacto" && v.seo_source !== "backfill_aproximado") {
        return "viral.seo_source debe ser corte, backfill_exacto o backfill_aproximado.";
      }
      out.seo_source = v.seo_source;
    }
    // Motivo de retención por título (2026-11-26, spec títulos
    // R3.4): string ≤ 500; `null` lo BORRA (el PATCH de desbloqueo
    // lo manda al poner el título nuevo).
    if (v.hold_reason !== undefined) {
      if (v.hold_reason === null) {
        out.hold_reason = null;
      } else if (typeof v.hold_reason !== "string") {
        return "viral.hold_reason debe ser texto (o null para quitarlo).";
      } else if (v.hold_reason.trim().length > 500) {
        return "viral.hold_reason no puede superar 500 caracteres.";
      } else if (v.hold_reason.trim() !== "") {
        out.hold_reason = v.hold_reason.trim();
      }
    }
  }
  // Campos sueltos (mandan sobre `viral`).
  if (body.seo_scores !== undefined) {
    const parsed = parseSeoScoresInput(body.seo_scores);
    if (typeof parsed === "string") return parsed;
    out.seo_scores = parsed;
  }
  if (body.score_kind !== undefined) {
    if (body.score_kind !== "ia" && body.score_kind !== "manual") return "score_kind debe ser \"ia\" o \"manual\".";
    out.score_kind = body.score_kind;
  }
  const numField = (key: "viral_score" | "hype_score" | "priority", max: number): string | null => {
    if (body[key] === undefined) return null;
    if (typeof body[key] !== "number" || !Number.isFinite(body[key] as number) || (body[key] as number) < 0 || (body[key] as number) > max) {
      return `${key} debe ser un número entre 0 y ${max}.`;
    }
    out[key] = body[key] as number;
    return null;
  };
  for (const [key, max] of [["viral_score", 100], ["hype_score", 100], ["priority", 100]] as const) {
    const e = numField(key, max);
    if (e) return e;
  }
  if (body.viral_breakdown !== undefined) {
    if (typeof body.viral_breakdown !== "object" || body.viral_breakdown === null || Array.isArray(body.viral_breakdown)) {
      return "viral_breakdown debe ser un objeto con los componentes de la viralidad.";
    }
    out.viral_breakdown = body.viral_breakdown as Record<string, unknown>;
  }
  // Veredicto de calidad B4, suelto (manda sobre `viral`).
  if (body.quality_gate !== undefined) {
    const parsed = parseQualityGateInput(body.quality_gate);
    if (typeof parsed === "string") return parsed;
    out.quality_gate = parsed;
  }
  return out;
}

/** Fusiona datos de puntuación en el JSON guardado: los campos
 *  nuevos reemplazan a los viejos (seo_scores y platforms se
 *  fusionan por plataforma); si no hay score_kind, se marca "ia"
 *  cuando vienen datos de puntuación. Devuelve el JSON a guardar. */
export function mergeViralJson(existing: string | null | undefined, patch: Partial<ViralData>): string {
  const base = parseViralJson(existing);
  const next: ViralData = { ...base };
  if (patch.viral_score !== undefined) next.viral_score = patch.viral_score;
  if (patch.hype_score !== undefined) next.hype_score = patch.hype_score;
  if (patch.priority !== undefined) next.priority = patch.priority;
  if (patch.viral_breakdown !== undefined) next.viral_breakdown = patch.viral_breakdown;
  if (patch.seo_scores !== undefined) {
    next.seo_scores = { ...(base.seo_scores ?? {}), ...patch.seo_scores };
    // La forma legacy (viral.platforms) se mantiene en sync para que
    // seoForPlatform y el Editor viejo sigan leyendo el SEO.
    const plats = { ...(base.platforms ?? {}) };
    for (const [k, s] of Object.entries(patch.seo_scores)) {
      plats[k as SeoPlatform] = { score: s };
    }
    next.platforms = plats;
  }
  if (patch.platforms !== undefined) {
    next.platforms = { ...(base.platforms ?? {}), ...patch.platforms };
    const scores = { ...(base.seo_scores ?? {}) };
    for (const [k, v] of Object.entries(patch.platforms)) {
      if (v && typeof v.score === "number") scores[k as SeoPlatform] = v.score;
    }
    next.seo_scores = scores;
  }
  if (patch.score_kind !== undefined) {
    next.score_kind = patch.score_kind;
  } else if (!next.score_kind && (patch.seo_scores !== undefined || patch.viral_score !== undefined || patch.hype_score !== undefined || patch.priority !== undefined || patch.viral_breakdown !== undefined)) {
    next.score_kind = "ia";
  }
  if (patch.rescue !== undefined) next.rescue = patch.rescue;
  // Puerta de calidad B4: el veredicto reemplaza al anterior (no
  // se fusiona por criterio: cada render tiene su propio veredicto).
  if (patch.quality_gate !== undefined) next.quality_gate = patch.quality_gate;
  // Entradas y procedencia del SEO (2026-11-24, auto-seo): se
  // guardan tal cual (reemplazan a las anteriores, como el gate).
  if (patch.seo_inputs !== undefined) next.seo_inputs = patch.seo_inputs;
  if (patch.seo_source !== undefined) next.seo_source = patch.seo_source;
  // Retención por título (2026-11-26, R3.4): un string lo fija;
  // `null` lo BORRA del JSON (el merge no puede borrar claves de
  // otra forma: un patch sin hold_reason lo deja como estaba).
  if (patch.hold_reason !== undefined) {
    if (patch.hold_reason === null) delete next.hold_reason;
    else next.hold_reason = patch.hold_reason;
  }
  return JSON.stringify(next);
}
export const GTA_VI_GAME = "GTA VI";

// ---------------------------------------------------------------------------
// CONFIGURACIÓN del panel (pestaña Ajustes, 2026-11-18): todo lo que el
// usuario cambia solo, sin código, se guarda en la tabla settings (D1,
// por tenant) y el Worker lo APLICA. Antes los campos de Ajustes se
// guardaban pero el Worker usaba valores fijos; desde aquí los valores
// efectivos salen de aquí, con estos defaults (las reglas del usuario).
// ---------------------------------------------------------------------------
export const DEFAULT_PANEL_SETTINGS = {
  // Layout de los clips: "rdjavi" (GTA 6 / Vice City, webcam centrada
  // arriba, fondo desenfocado morado/magenta, gameplay abajo, neón
  // rosa-violeta-naranja) o "plain" (split vertical simple: juego
  // arriba, cámara abajo). Alias legacy aceptado: "split" = "plain".
  layout: "rdjavi",
  // Subtítulos: "ref" (simple RDJavi: una línea blanca con contorno
  // negro, sin karaoke, sin fondo) es el default desde 2026-11-18.
  // Alias legacy aceptado: "simple" = "ref". "karaoke" es el otro
  // estilo de Ajustes; "tiktok"/"highlight" siguen valiendo.
  subtitle_style: "ref",
  subtitles_enabled: true,
  // Preview en el Editor (2026-11-18, v3.1): por defecto NO se
  // exige ver el preview para publicar (false). Si el usuario
  // activa "Exigir ver el preview antes de publicar" en Ajustes,
  // el gate vuelve a frenar con "falta ver el preview en el
  // Editor". El preview sigue disponible como casilla en el
  // Editor, pero sin bloquear.
  require_preview: false,
  // Auto-publicación de tops medidos (2026-11-19): por defecto
  // APAGADA (false). Cuando el usuario la enciende en Ajustes, el
  // ciclo del Worker encola solos los primeros clips de la Cola
  // (ordenada con el boost por tops medidos) que no tengan job
  // publish vigente, pasando EXACTAMENTE el mismo publishGate que
  // el botón Publicar (topes, espaciamiento, SEO, preview...). Sin
  // encenderla, el Worker nunca publica por su cuenta: solo ejecuta
  // los jobs que el usuario encoló.
  auto_publish_top: false,
  // Duración de los clips: sin mínimo ni rango prohibido (regla
  // 40-60 / 25-35 eliminada por el usuario el 2026-11-21, para
  // siempre, en todo el canal). Solo queda el tope de 60 s.
  min_duration_s: 1,
  max_duration_s: 60,
  // Umbrales duros: viralidad y SEO mínimos para publicar.
  // Fuente única 45/80 (orden del usuario 2026-11-19): los
  // mismos que config.yaml scoring del cerebro y que la fila
  // viva de settings (min_virality 45, min_seo 80). Estos
  // defaults solo valen si no hay fila de settings.
  min_virality: 45,
  min_seo: 80,
  // Topes diarios por plataforma (techo; el plan nunca supera el
  // techo). Reglas de publicación 2026-11-19 (reemplazan las del
  // 2026-11-18): TikTok plan 3/máx 3, Instagram 3/3, YouTube 6/6,
  // Facebook 3/3 (orden usuario 2026-11-21 21:09: IG/FB a 3 y
  // YouTube a 6; mínimo 3 POR PLATAFORMA al día). Los topes viven POR PLATAFORMA
  // (tiktok_plan/tiktok_max, ...); resto_plan/resto_max son LEGACY
  // y solo alimentan a las 3 (TikTok/Instagram/Facebook) cuando no
  // hay valor por plataforma (ver effectiveSettings). Los alias
  // daily_limit_* se derivan del máx por plataforma (y viceversa
  // si solo viene el legacy).
  youtube_plan: 6,
  youtube_max: 6,
  // Orden usuario 2026-11-21 20:52: mínimo 3 vídeos POR PLATAFORMA
  // al día (kaez y sebastián, en las 3 franjas). tiktok_plan 2→3.
  tiktok_plan: 3,
  tiktok_max: 3,
  instagram_plan: 3,
  instagram_max: 3,
  facebook_plan: 3,
  facebook_max: 3,
  resto_plan: 2,
  resto_max: 3,
  daily_limit_youtube_shorts: 6,
  daily_limit_tiktok: 3,
  daily_limit_instagram: 3,
  daily_limit_facebook: 3,
  // Cuentas nuevas (calentamiento 2026-11-19): 3 publicaciones/día
  // en TikTok/Instagram/Facebook hasta ser "establecida" (ver
  // computeMaturity: >=14 días Y >=20 publicaciones, sin strikes).
  // Orden usuario 2026-11-21 20:52: mínimo 3 POR PLATAFORMA al día
  // (new_account_daily 1→3). YouTube usa new_account_daily_youtube.
  // Orden usuario 2026-11-21 21:09: YouTube a 6 (plan/máx 6 y
  // new_account_daily_youtube 1→6).
  new_account_daily: 3,
  new_account_daily_youtube: 6,
  // TikTok vía Zernio: tope diario de publicaciones y borradores
  // pendientes que Zernio admite antes de frenar con draft.
  tiktok_zernio_daily: 15,
  tiktok_zernio_pending_drafts: 5,
  // Engagement SOLO por Zernio (2026-11-23, v5.3.0): apagado por
  // defecto. Sin follow (Zernio no lo expone para IG/FB/YT/TikTok),
  // sin TikTok hacia afuera (Like post devuelve 400) y sin
  // descubrir posts de terceros. Topes POR plataforma+idioma+día.
  engagement_enabled: false,
  engagement_reply_daily_max: 10,
  engagement_like_daily_max: 10,
  engagement_outward_like_daily_max: 5,
  engagement_outward_comment_daily_max: 1,
  // Espaciamiento mínimo entre dos subidas en la misma cuenta:
  // 3 h en general, 2 h en YouTube (decisión del usuario
  // 2026-11-23: antes 4 h, pero con 4 h solo cabían 4 huecos al
  // día y YouTube es 6/día; con 2 h caben los 6). Mínimo
  // absoluto: nunca 2 vídeos al mismo minuto (5 min).
  spacing_hours: 3,
  youtube_spacing_hours: 2,
  min_spacing_minutes: 5,
  // Mismo clip en distintas plataformas: mínimo 15 min entre una
  // y otra (ideal 12-24 h; el plan diario lo separa en franjas).
  cross_platform_spacing_minutes: 15,
  // Nunca más de 3 publicaciones por cuenta en 24 h móviles
  // (ventana rolling, no día calendario). Techo duro: lo aplica
  // el gate incluso a la publicación manual. YouTube tiene su
  // propio techo (6, decisión 2026-11-23: YouTube es 6/día).
  rolling_24h_max: 3,
  rolling_24h_max_youtube: 6,
  // Días activos por semana por cuenta (reglas 2026-11-19):
  // TikTok 5-6, resto 4-5. Se guarda el mínimo del rango (el
  // plan diario rota el día libre para llegar a esos días).
  tiktok_days_per_week: 5,
  instagram_days_per_week: 4,
  youtube_days_per_week: 4,
  facebook_days_per_week: 4,
  // Hashtags: máx 5 en Instagram, 3-5 en el resto (techo 5).
  hashtag_max_instagram: 5,
  hashtag_max: 5,
  // Hype por ráfaga: medir el hype por velocidad del chat
  // (mensajes por 10 s/minuto y su pico contra el baseline), no
  // solo por el total de comentarios. Lo consume el cerebro al
  // detectar picos; el Worker solo lo guarda y lo expone.
  hype_burst: true,
  // Categoría: este panel es SOLO GTA VI (decisión 2026-11-18).
  game: GTA_VI_GAME,
  // Miniatura por defecto: "gta6" (neón GTA 6), "gameplay" o "webcam".
  thumbnail_style: "gta6",
  // Prioridad de campaña en el plan diario (2026-11-23, orden del
  // usuario 18:37): % de los slots de CADA cuenta que los clips de
  // streamers con campaña activa >= $1/1k NETO ocupan PRIMERO, antes
  // que el resto de la Cola. 60% propuesto: con 3 slots/día, 2 van
  // primero a campaña y 1 queda siempre para el resto (no se deja a
  // cero a los demás, para no matar el crecimiento). Los clips de
  // campaña que no caben en la reserva compiten después en orden
  // normal de Cola. 0 = sin prioridad; 100 = campaña primero en
  // todos los slots. Vive en settings (POR TENANT, se cambia en
  // Ajustes sin tocar código) y NUNCA cambia topes, espaciamiento
  // ni los umbrales 45/80: solo el ORDEN dentro de la capacidad.
  campaign_priority_share_pct: 60,
  // Marca de agua (2026-11-18): logo semitransparente quemado en el
  // render. Apagada por defecto; se enciende en Ajustes eligiendo un
  // logo subido. Defaults de la prueba del p7775: centrada, ~320 px
  // de diámetro, 20% de opacidad. watermark_logo_id es el id de la
  // fila en watermark_logos (por tenant): NUNCA se copia con la
  // configuración base (ver BASE_SETTINGS_KEYS), porque apunta a
  // logos de otro tenant.
  watermark_enabled: false,
  watermark_logo_id: null,
  watermark_opacity: 20,
  watermark_size_px: 320,
  watermark_position: "center",
} as const;

export type EffectiveSettings = typeof DEFAULT_PANEL_SETTINGS;

function clampInt(v: unknown, def: number, min: number, max: number): number {
  if (typeof v !== "number" || !Number.isFinite(v)) return def;
  return Math.min(max, Math.max(min, Math.round(v)));
}

/** Mezcla los ajustes guardados con los defaults y los valida: lo que
 *  el usuario no tocó (o puso mal) vuelve al default. Las claves que
 *  este helper no conoce (telegram_*, publish_window_*, ...) se
 *  conservan tal cual. Aliases: layout "split" = "plain",
 *  subtitle_style "simple" = "ref"; los topes legacy daily_limit_*
 *  se derivan de youtube_max/resto_max (y viceversa). */
export function effectiveSettings(raw: Record<string, unknown>): EffectiveSettings {
  const min_d = clampInt(raw.min_duration_s, 1, 1, 300);
  let max_d = clampInt(raw.max_duration_s, 60, 1, 300);
  if (max_d <= min_d) max_d = min_d + 1;
  // Topes POR PLATAFORMA (reglas 2026-11-19). Precedencia por
  // plataforma: valor por plataforma (tiktok_max, ...) > legacy
  // resto_max/resto_plan (solo TikTok/Instagram/Facebook) >
  // legacy daily_limit_* > default. Así los Ajustes viejos que
  // solo traen resto_* siguen alimentando a las 3, y un valor
  // por plataforma nunca se pisa por el legacy común.
  const cap = (v: unknown, d: number) => clampInt(v, d, 0, 10);
  const maxFor = (p: "youtube" | "tiktok" | "instagram" | "facebook", defMax: number, legacyKey: string): number => {
    const per = raw[`${p}_max`];
    if (per !== undefined) return clampInt(per, defMax, 0, 10);
    if (p !== "youtube" && raw.resto_max !== undefined) return clampInt(raw.resto_max, defMax, 0, 10);
    return clampInt(raw[legacyKey], defMax, 0, 10);
  };
  const planFor = (p: "youtube" | "tiktok" | "instagram" | "facebook", defPlan: number, max: number): number => {
    const per = raw[`${p}_plan`];
    const base = per !== undefined ? clampInt(per, defPlan, 0, 10)
      : p !== "youtube" && raw.resto_plan !== undefined ? clampInt(raw.resto_plan, defPlan, 0, 10)
      : defPlan;
    // El plan nunca supera su techo.
    return Math.min(base, max);
  };
  const ytMax = maxFor("youtube", 6, "daily_limit_youtube_shorts");
  const ttMax = maxFor("tiktok", 3, "daily_limit_tiktok");
  const igMax = maxFor("instagram", 3, "daily_limit_instagram");
  const fbMax = maxFor("facebook", 3, "daily_limit_facebook");
  const ytPlan = planFor("youtube", 6, ytMax);
  // Orden usuario 2026-11-21 20:52: mínimo 3 vídeos POR PLATAFORMA
  // al día. Default tiktok_plan 2→3 (igual que DEFAULT_PANEL_SETTINGS).
  const ttPlan = planFor("tiktok", 3, ttMax);
  // Orden usuario 2026-11-21 21:09: IG/FB a 3 y YouTube a 6
  // (defaults 1/2→3/3 y 1/2→6/6, igual que DEFAULT_PANEL_SETTINGS).
  const igPlan = planFor("instagram", 3, igMax);
  const fbPlan = planFor("facebook", 3, fbMax);
  // resto_* legacy: lo guardado (o el default) para mostrarlo en
  // Ajustes como "valor común"; no manda si hay valor por plataforma.
  const restPlan = Math.min(clampInt(raw.resto_plan, 2, 0, 10), clampInt(raw.resto_max, 3, 0, 10));
  const restMax = clampInt(raw.resto_max, 3, 0, 10);
  const layoutRaw = String(raw.layout ?? "rdjavi").trim().toLowerCase();
  const layout = layoutRaw === "plain" || layoutRaw === "split" ? "plain" : "rdjavi";
  const styleRaw = String(raw.subtitle_style ?? "ref").trim().toLowerCase();
  const subtitle_style = styleRaw === "simple" || styleRaw === "ref" ? "ref"
    : ["tiktok", "highlight", "karaoke"].includes(styleRaw) ? styleRaw : "ref";
  // El plan nunca supera su techo: si el usuario baja el techo a 1,
  // el plan es 1 (lo aplica también publishGate). planFor ya aplica
  // ese mínimo por plataforma.
  return {
    ...raw,
    layout,
    subtitle_style,
    subtitles_enabled: raw.subtitles_enabled === false ? false : true,
    require_preview: raw.require_preview === true,
    auto_publish_top: raw.auto_publish_top === true,
    min_duration_s: min_d,
    max_duration_s: max_d,
    min_virality: clampInt(raw.min_virality, 45, 0, 100),
    min_seo: clampInt(raw.min_seo, 80, 0, 100),
    youtube_plan: ytPlan,
    youtube_max: ytMax,
    tiktok_plan: ttPlan,
    tiktok_max: ttMax,
    instagram_plan: igPlan,
    instagram_max: igMax,
    facebook_plan: fbPlan,
    facebook_max: fbMax,
    resto_plan: restPlan,
    resto_max: restMax,
    daily_limit_youtube_shorts: ytMax,
    daily_limit_tiktok: ttMax,
    daily_limit_instagram: igMax,
    daily_limit_facebook: fbMax,
    // Orden usuario 2026-11-21 20:52: mínimo 3 vídeos POR PLATAFORMA
    // al día. Default new_account_daily 1→3 (igual que
    // DEFAULT_PANEL_SETTINGS). YouTube usa new_account_daily_youtube
    // (orden 2026-11-21 21:09: default 1→6).
    new_account_daily: clampInt(raw.new_account_daily, 3, 0, 10),
    new_account_daily_youtube: clampInt(raw.new_account_daily_youtube, 6, 0, 10),
    tiktok_zernio_daily: clampInt(raw.tiktok_zernio_daily, 15, 0, 100),
    tiktok_zernio_pending_drafts: clampInt(raw.tiktok_zernio_pending_drafts, 5, 0, 100),
    // Engagement solo-Zernio (2026-11-23, v5.3.0): kill-switch OFF
    // por defecto; los topes son techo por plataforma+idioma+día.
    // Engagement (2026-11-23, fix bug de tipo): la D1 guardó
    // engagement_enabled como número 1 (seed v5.3.0 vía SQL) y el
    // === true lo leía apagado para siempre, en silencio. Se acepta
    // 1 además de true; el PUT /api/settings sigue exigiendo
    // booleano, así que el toggle del panel no cambia.
    engagement_enabled: raw.engagement_enabled === true || raw.engagement_enabled === 1,
    engagement_reply_daily_max: clampInt(raw.engagement_reply_daily_max, 10, 0, 15),
    engagement_like_daily_max: clampInt(raw.engagement_like_daily_max, 10, 0, 20),
    engagement_outward_like_daily_max: clampInt(raw.engagement_outward_like_daily_max, 5, 0, 10),
    engagement_outward_comment_daily_max: clampInt(raw.engagement_outward_comment_daily_max, 1, 0, 2),
    spacing_hours: clampInt(raw.spacing_hours, 3, 0, 24),
    youtube_spacing_hours: clampInt(raw.youtube_spacing_hours, 2, 0, 24),
    min_spacing_minutes: clampInt(raw.min_spacing_minutes, 5, 0, 60),
    cross_platform_spacing_minutes: clampInt(raw.cross_platform_spacing_minutes, 15, 0, 1440),
    rolling_24h_max: clampInt(raw.rolling_24h_max, 3, 0, 10),
    rolling_24h_max_youtube: clampInt(raw.rolling_24h_max_youtube, 6, 0, 10),
    tiktok_days_per_week: clampInt(raw.tiktok_days_per_week, 5, 0, 7),
    instagram_days_per_week: clampInt(raw.instagram_days_per_week, 4, 0, 7),
    youtube_days_per_week: clampInt(raw.youtube_days_per_week, 4, 0, 7),
    facebook_days_per_week: clampInt(raw.facebook_days_per_week, 4, 0, 7),
    hashtag_max_instagram: clampInt(raw.hashtag_max_instagram, 5, 0, 30),
    hashtag_max: clampInt(raw.hashtag_max, 5, 0, 30),
    hype_burst: raw.hype_burst === false ? false : true,
    // La categoría es fija: cualquier otro valor vuelve a GTA VI.
    game: isGtaViGame(raw.game) || raw.game === undefined ? GTA_VI_GAME : GTA_VI_GAME,
    thumbnail_style: ["gta6", "gameplay", "webcam"].includes(String(raw.thumbnail_style)) ? String(raw.thumbnail_style) : "gta6",
    // Marca de agua: lo mal puesto vuelve al default en silencio
    // (el PUT lo rechaza con 400; esto es para lo que ya está
    // guardado). El logo activo debe ser un id entero positivo o
    // null; cualquier otra cosa es "sin logo".
    watermark_enabled: raw.watermark_enabled === true,
    watermark_logo_id: (typeof raw.watermark_logo_id === "number" && Number.isInteger(raw.watermark_logo_id) && raw.watermark_logo_id > 0)
      ? raw.watermark_logo_id : null,
    watermark_opacity: clampInt(raw.watermark_opacity, 20, 0, 100),
    watermark_size_px: clampInt(raw.watermark_size_px, 320, 50, 1000),
    campaign_priority_share_pct: clampInt(raw.campaign_priority_share_pct, 60, 0, 100),
    watermark_position: ["center", "top-left", "top-right", "bottom-left", "bottom-right"].includes(String(raw.watermark_position))
      ? String(raw.watermark_position) : "center",
  } as EffectiveSettings;
}

/** Valida un PUT /api/settings ANTES de guardar: devuelve el mensaje
 *  de error o null si vale. effectiveSettings corrige en silencio lo
 *  mal puesto, pero el PUT debe rechazar lo inválido con 400 para
 *  que el usuario se entere (min<max, 0-100, topes>=0). */
export function validateSettingsInput(body: Record<string, unknown>): string | null {
  const intIn = (v: unknown): number | null => {
    if (typeof v !== "number" || !Number.isFinite(v) || !Number.isInteger(v)) return null;
    return v;
  };
  const minD = body.min_duration_s !== undefined ? intIn(body.min_duration_s) : null;
  const maxD = body.max_duration_s !== undefined ? intIn(body.max_duration_s) : null;
  if (body.min_duration_s !== undefined && (minD === null || minD < 1 || minD > 300)) return "min_duration_s debe ser un entero entre 1 y 300.";
  if (body.max_duration_s !== undefined && (maxD === null || maxD < 1 || maxD > 300)) return "max_duration_s debe ser un entero entre 1 y 300.";
  if (minD !== null && maxD !== null && maxD <= minD) return "max_duration_s debe ser mayor que min_duration_s.";
  for (const k of ["min_virality", "min_seo"] as const) {
    if (body[k] !== undefined) {
      const v = intIn(body[k]);
      if (v === null || v < 0 || v > 100) return `${k} debe ser un entero entre 0 y 100.`;
    }
  }
  for (const k of ["youtube_plan", "youtube_max", "tiktok_plan", "tiktok_max",
    "instagram_plan", "instagram_max", "facebook_plan", "facebook_max",
    "resto_plan", "resto_max", "new_account_daily",
    "new_account_daily_youtube",
    "tiktok_zernio_daily", "tiktok_zernio_pending_drafts",
    "engagement_reply_daily_max", "engagement_like_daily_max",
    "engagement_outward_like_daily_max", "engagement_outward_comment_daily_max",
    "rolling_24h_max", "rolling_24h_max_youtube", "hashtag_max_instagram", "hashtag_max",
    "daily_limit_youtube_shorts", "daily_limit_tiktok", "daily_limit_instagram", "daily_limit_facebook"] as const) {
    if (body[k] !== undefined) {
      const v = intIn(body[k]);
      if (v === null || v < 0 || v > 100) return `${k} debe ser un entero mayor o igual a 0.`;
    }
  }
  for (const k of ["tiktok_days_per_week", "instagram_days_per_week", "youtube_days_per_week", "facebook_days_per_week"] as const) {
    if (body[k] !== undefined) {
      const v = intIn(body[k]);
      if (v === null || v < 0 || v > 7) return `${k} debe ser un entero entre 0 y 7.`;
    }
  }
  if (body.spacing_hours !== undefined) {
    const v = intIn(body.spacing_hours);
    if (v === null || v < 0 || v > 24) return "spacing_hours debe ser un entero entre 0 y 24.";
  }
  if (body.campaign_priority_share_pct !== undefined) {
    const v = intIn(body.campaign_priority_share_pct);
    if (v === null || v < 0 || v > 100) return "campaign_priority_share_pct debe ser un entero entre 0 y 100.";
  }
  if (body.youtube_spacing_hours !== undefined) {
    const v = intIn(body.youtube_spacing_hours);
    if (v === null || v < 0 || v > 24) return "youtube_spacing_hours debe ser un entero entre 0 y 24.";
  }
  if (body.min_spacing_minutes !== undefined) {
    const v = intIn(body.min_spacing_minutes);
    if (v === null || v < 0 || v > 60) return "min_spacing_minutes debe ser un entero entre 0 y 60.";
  }
  if (body.cross_platform_spacing_minutes !== undefined) {
    const v = intIn(body.cross_platform_spacing_minutes);
    if (v === null || v < 0 || v > 1440) return "cross_platform_spacing_minutes debe ser un entero entre 0 y 1440.";
  }
  if (body.layout !== undefined) {
    const l = String(body.layout).trim().toLowerCase();
    if (l !== "rdjavi" && l !== "plain" && l !== "split") return "layout debe ser rdjavi o plain.";
  }
  if (body.subtitle_style !== undefined) {
    const s = String(body.subtitle_style).trim().toLowerCase();
    if (!["ref", "simple", "karaoke", "tiktok", "highlight"].includes(s)) return "subtitle_style debe ser ref o karaoke.";
  }
  if (body.hype_burst !== undefined && typeof body.hype_burst !== "boolean") return "hype_burst debe ser true o false.";
  // Preview opcional (2026-11-18, v3.1): solo true/false.
  if (body.require_preview !== undefined && typeof body.require_preview !== "boolean") return "require_preview debe ser true o false.";
  // Auto-publicación de tops (2026-11-19): solo true/false.
  if (body.auto_publish_top !== undefined && typeof body.auto_publish_top !== "boolean") return "auto_publish_top debe ser true o false.";
  // Engagement solo-Zernio (2026-11-23, v5.3.0): solo true/false y
  // topes enteros dentro de su rango.
  if (body.engagement_enabled !== undefined && typeof body.engagement_enabled !== "boolean") return "engagement_enabled debe ser true o false.";
  for (const [k, max] of [["engagement_reply_daily_max", 15], ["engagement_like_daily_max", 20], ["engagement_outward_like_daily_max", 10], ["engagement_outward_comment_daily_max", 2]] as const) {
    if (body[k] !== undefined) {
      const v = intIn(body[k]);
      if (v === null || v < 0 || v > max) return `${k} debe ser un entero entre 0 y ${max}.`;
    }
  }
  // Marca de agua (2026-11-18).
  if (body.watermark_enabled !== undefined && typeof body.watermark_enabled !== "boolean") return "watermark_enabled debe ser true o false.";
  if (body.watermark_logo_id !== undefined && body.watermark_logo_id !== null) {
    const v = intIn(body.watermark_logo_id);
    if (v === null || v <= 0) return "watermark_logo_id debe ser un id de logo válido o null.";
  }
  if (body.watermark_opacity !== undefined) {
    const v = intIn(body.watermark_opacity);
    if (v === null || v < 0 || v > 100) return "watermark_opacity debe ser un entero entre 0 y 100.";
  }
  if (body.watermark_size_px !== undefined) {
    const v = intIn(body.watermark_size_px);
    if (v === null || v < 50 || v > 1000) return "watermark_size_px debe ser un entero entre 50 y 1000.";
  }
  if (body.watermark_position !== undefined
    && !["center", "top-left", "top-right", "bottom-left", "bottom-right"].includes(String(body.watermark_position))) {
    return "watermark_position debe ser center, top-left, top-right, bottom-left o bottom-right.";
  }
  return null;
}

/** Claves de settings que el asistente de onboarding copia como
 *  configuración base (2026-11-18): SOLO reglas (layout, duración,
 *  topes, estilos...). NUNCA secretos: telegram_bot_token,
 *  telegram_chat_id y cualquier clave desconocida quedan fuera.
 *  Copiar la base no toca credenciales de nadie. */
export const BASE_SETTINGS_KEYS = [
  "layout", "subtitle_style", "subtitles_enabled", "require_preview", "auto_publish_top",
  "min_duration_s", "max_duration_s", "min_virality", "min_seo",
  "youtube_plan", "youtube_max", "tiktok_plan", "tiktok_max",
  "instagram_plan", "instagram_max", "facebook_plan", "facebook_max",
  "resto_plan", "resto_max",
  "daily_limit_youtube_shorts", "daily_limit_tiktok", "daily_limit_instagram", "daily_limit_facebook",
  "new_account_daily", "new_account_daily_youtube", "tiktok_zernio_daily", "tiktok_zernio_pending_drafts",
  "engagement_enabled", "engagement_reply_daily_max", "engagement_like_daily_max",
  "engagement_outward_like_daily_max", "engagement_outward_comment_daily_max",
  "spacing_hours", "youtube_spacing_hours", "min_spacing_minutes",
  "cross_platform_spacing_minutes", "rolling_24h_max", "rolling_24h_max_youtube",
  "tiktok_days_per_week", "instagram_days_per_week", "youtube_days_per_week", "facebook_days_per_week",
  "hashtag_max_instagram", "hashtag_max",
  "hype_burst", "game", "thumbnail_style",
  // Marca de agua: se copian el interruptor y los valores (opacidad,
  // tamaño, posición), pero NUNCA watermark_logo_id: ese id apunta
  // a una fila de watermark_logos del tenant que copia, y el tenant
  // nuevo acabaría usando (o intentando usar) el logo de otro.
  "watermark_enabled", "watermark_opacity", "watermark_size_px", "watermark_position",
] as const;

/** Extrae solo las reglas copiables de unos settings (sin secretos). */
export function extractBaseSettings(raw: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of BASE_SETTINGS_KEYS) {
    if (raw[k] !== undefined) out[k] = raw[k];
  }
  return out;
}

// ---------------------------------------------------------------------------
// REGLAS DE PUBLICACIÓN 2026-11-19 (reemplazan las del 2026-11-18).
// Calentamiento: una cuenta es NUEVA si tiene menos de 14 días
// desde su primera publicación O menos de 20 publicaciones.
// Etapas: semana 1 (0-6 días): máx 1/día; semana 2 (7-13 días):
// máx 1/día diario; semana 3 (≥14 días pero <20 publicaciones):
// ritmo normal de la tabla, sin acceso al máximo duro; establecida
// (≥14 días Y ≥20 publicaciones, sin strikes): plan normal y acceso
// al máximo duro solo si se cumple la regla 6 (ver daily-plan.ts:
// >30 días sin restricción + cola viral alta). Si hay sanción
// (strikes > 0), vuelve a NUEVA (1/día en semana 1/2) hasta que
// los strikes se pongan a 0 y se vuelva a cumplir el criterio.
// El recálculo corre como mucho UNA vez al día por cuenta
// (maturity_checked_day, día America/Santiago); la publicación
// solo usa el estado guardado, nunca un cálculo en caliente.
// La decisión 2026-11-18 "sin franja horaria" queda REVOCADA:
// vuelven las franjas (ver PUBLISH_SLOT_WINDOWS). La madurez vieja
// (30 días + 10 publicaciones) queda reemplazada por esta.
// ---------------------------------------------------------------------------
export const MATURITY_MIN_DAYS = 14;
export const MATURITY_MIN_PUBLISHED = 20;
export const WARMUP_MIN_DAYS = 14;
export const WARMUP_MIN_PUBLISHED = 20;
/** Pausa de una cuenta tras spam/límite (regla 6): 48 h. */
export const SPAM_PAUSE_HOURS = 48;
/** Tope duro rolling: nunca más de 3 por cuenta en 24 h móviles. */
export const ROLLING_24H_MAX_DEFAULT = 3;

export type AccountMaturity = "nueva" | "establecida";
/** Etapa de calentamiento (2026-11-19). */
export type WarmupStage = "semana1" | "semana2" | "semana3" | "establecida";

export interface MaturityInput {
  first_published_at: number | null;
  published_count: number;
  strikes: number;
}

export interface MaturityInfo extends MaturityInput {
  status: AccountMaturity;
  stage: WarmupStage;
  days_since_first_published: number | null;
  /** Días que faltan para salir de semana 1/2 (0 si ya pasó). */
  warmup_remaining_days: number;
  /** Publicaciones que faltan para las 20 (0 si ya las tiene). */
  warmup_remaining_pubs: number;
  effective_daily_limit: number;
  effective_daily_plan: number;
  effective_daily_max: number;
}

/** Plan (ritmo normal) y techo (máximo duro) por plataforma,
 *  desde los settings efectivos (por plataforma; resto_* legacy
 *  ya está resuelto dentro de effectiveSettings). */
export function platformPlan(eff: EffectiveSettings, platform: string): number {
  switch (platform) {
    case "youtube": return eff.youtube_plan;
    case "tiktok": return eff.tiktok_plan;
    case "instagram": return eff.instagram_plan;
    case "facebook": return eff.facebook_plan;
    default: return eff.resto_plan;
  }
}

export function platformMax(eff: EffectiveSettings, platform: string): number {
  switch (platform) {
    case "youtube": return eff.youtube_max;
    case "tiktok": return eff.tiktok_max;
    case "instagram": return eff.instagram_max;
    case "facebook": return eff.facebook_max;
    default: return eff.resto_max;
  }
}

/** Espaciamiento mínimo (horas) entre dos subidas de la misma
 *  cuenta: 4 h en YouTube, 3 h en el resto. */
export function spacingHoursFor(eff: EffectiveSettings, platform: string): number {
  return platform === "youtube" ? eff.youtube_spacing_hours : eff.spacing_hours;
}

/** Días activos por semana de una plataforma (TikTok 5, resto 4). */
export function daysPerWeekFor(eff: EffectiveSettings, platform: string): number {
  switch (platform) {
    case "tiktok": return eff.tiktok_days_per_week;
    case "instagram": return eff.instagram_days_per_week;
    case "youtube": return eff.youtube_days_per_week;
    case "facebook": return eff.facebook_days_per_week;
    default: return 4;
  }
}

/** Tope de hashtags por plataforma (Instagram 5, resto 5). */
export function hashtagMaxFor(eff: EffectiveSettings, platform: string): number {
  return platform === "instagram" ? eff.hashtag_max_instagram : eff.hashtag_max;
}

/** Recorta una lista de hashtags al tope de la plataforma
 *  (regla 5: máx 5 en Instagram, 3-5 en el resto). */
export function capHashtags(tags: string[], platform: string, eff: EffectiveSettings): string[] {
  const clean = tags.map((t) => t.trim()).filter(Boolean);
  return [...new Set(clean)].slice(0, hashtagMaxFor(eff, platform));
}

/** Etapa de calentamiento a partir de días/publicaciones/strikes.
 *  Sin primera publicación (days null) es semana 1. Con strikes
 *  > 0 la cuenta vuelve al tope de 1/día (etapa semana 1/2 según
 *  antigüedad, nunca semana 3 ni establecida) hasta que los
 *  strikes se pongan a 0: subir de nivel exige no tener avisos
 *  ni restricciones (regla 3). */
export function warmupStage(days: number | null, publishedCount: number, strikes: number): WarmupStage {
  if (strikes > 0) return days !== null && days >= 7 ? "semana2" : "semana1";
  if (days === null || days < 7) return "semana1";
  if (days < 14) return "semana2";
  if (publishedCount >= WARMUP_MIN_PUBLISHED) return "establecida";
  return "semana3";
}

export function santiagoDayKeyMs(ms: number): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Santiago", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(ms));
}

// ---------------------------------------------------------------------------
// FRANJAS HORARIAS (reglas 2026-11-19, hora Chile, America/Santiago).
// Mañana 12:00-14:00, tarde 18:00-20:00, noche 21:00-23:00.
// 1 vídeo/día: tarde o noche. 2 o más: mediodía + noche (y tarde
// si son 3). La hora exacta varía ±10 min cada día con jitter
// DETERMINISTA por cuenta+día (hash FNV-1a, no aleatorio sin
// semilla): el mismo día y cuenta siempre dan la misma hora, y
// otro día da otra. La publicación automática solo sale dentro
// de estas franjas (ver gate.ts, checkSlot).
// ---------------------------------------------------------------------------
export interface PublishSlotWindow { label: string; startMin: number; endMin: number }

export const PUBLISH_SLOT_WINDOWS: PublishSlotWindow[] = [
  { label: "mañana", startMin: 12 * 60, endMin: 14 * 60 },
  { label: "tarde", startMin: 18 * 60, endMin: 20 * 60 },
  { label: "noche", startMin: 21 * 60, endMin: 23 * 60 },
  // Madrugada (F0, spec 2026-11-24): la franja nocturna 00:00-01:00
  // cubre el slot YT de las 00:00; el techo automático sube a 37/día.
  // Va al final: slotWindowsForCount desestructura los 3 primeros
  // (mañana/tarde/noche) y slotTimeFor nunca la elige.
  { label: "madrugada", startMin: 0, endMin: 1 * 60 },
];

/** Hash FNV-1a (32 bits) de una cadena: semilla determinista. */
export function hashSeed(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h;
}

/** Jitter determinista en minutos, entre -10 y +10, para una
 *  semilla (cuenta+día+franja). */
export function jitterMinutes(seed: string): number {
  return (hashSeed(seed) % 21) - 10;
}

/** Minutos desde medianoche (hora Chile) de un instante. */
export function santiagoMinutesOfDay(ms: number): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Santiago", hour: "numeric", minute: "numeric", hour12: false,
  }).formatToParts(new Date(ms));
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
  const h = get("hour") % 24;
  return h * 60 + get("minute");
}

/** Franja que contiene un instante, o null si está fuera de franja. */
export function slotWindowContaining(ms: number): PublishSlotWindow | null {
  const m = santiagoMinutesOfDay(ms);
  return PUBLISH_SLOT_WINDOWS.find((w) => m >= w.startMin && m < w.endMin) ?? null;
}

/** ¿Está este instante dentro de una franja de publicación? */
export function isWithinPublishSlot(ms: number): boolean {
  return slotWindowContaining(ms) !== null;
}

/** Límites en ms (UTC) de la franja que contiene un instante, o
 *  null si está fuera de franja. Se calcula por diferencia con
 *  `santiagoMinutesOfDay` (minutos desde medianoche en Chile), así
 *  no depende del offset UTC de la zona (que cambia con el horario
 *  de verano). Regla 09:25 (2026-11-21, SIN AMONTONAR): como mucho
 *  1 publicación por plataforma y franja; el gate cuenta las
 *  publicadas dentro de esta ventana. */
export function slotWindowBoundsMs(ms: number): { startMs: number; endMs: number; label: string } | null {
  const w = slotWindowContaining(ms);
  if (!w) return null;
  const m = santiagoMinutesOfDay(ms);
  const startMs = ms - (m - w.startMin) * 60 * 1000;
  return { startMs, endMs: startMs + (w.endMin - w.startMin) * 60 * 1000, label: w.label };
}

/** Ventanas que tocan a `count` publicaciones en un día:
 *  1 → tarde o noche (la elige el hash de cuenta+día);
 *  2 → mañana + noche; 3 → mañana + tarde + noche. */
export function slotWindowsForCount(count: number, seed: string): PublishSlotWindow[] {
  const [manana, tarde, noche] = PUBLISH_SLOT_WINDOWS as [PublishSlotWindow, PublishSlotWindow, PublishSlotWindow];
  if (count <= 1) return [hashSeed(seed) % 2 === 0 ? tarde : noche];
  if (count === 2) return [manana, noche];
  return [manana, tarde, noche];
}

/** Hora local "HH:MM" de la publicación nº `slotIndex` (0-based)
 *  de un día con `count` publicaciones, para una cuenta (`seed`
 *  = p. ej. `acc-12`). Determinista por (día, cuenta, franja):
 *  hora base por franja (mañana 12:30, tarde 18:10, noche 21:30)
 *  ±10 min de jitter. Las bases están elegidas para que dos
 *  franjas seguidas queden SIEMPRE a ≥3 h (tarde 18:00-18:20 →
 *  noche 21:20-21:40 = 3 h como mínimo; YouTube, con 4 h, nunca
 *  tiene 3 publicaciones al día: con 2 usa mañana+noche, 9 h). */
export function slotTimeFor(dayKey: string, seed: string, slotIndex: number, count: number): string {
  const windows = slotWindowsForCount(count, `${seed}|${dayKey}`);
  const w = windows[Math.min(slotIndex, windows.length - 1)] ?? windows[0]!;
  const baseMin = w.label === "mañana" ? 12 * 60 + 30 : w.label === "tarde" ? 18 * 60 + 10 : 21 * 60 + 30;
  const minute = baseMin + jitterMinutes(`${seed}|${dayKey}|${w.label}`);
  const hh = String(Math.floor(minute / 60)).padStart(2, "0");
  const mm = String(minute % 60).padStart(2, "0");
  return `${hh}:${mm}`;
}

/** Pasa "HH:MM" (hora Chile) + día YYYY-MM-DD a ms epoch.
 *  Resuelve el offset de America/Santiago con Intl (sin suponer
 *  -03:00 fijo: Chile cambia de horario). */
export function santiagoWallToUtcMs(dayKey: string, hhmm: string): number {
  const parts = dayKey.split("-").map(Number);
  const y = parts[0] ?? 1970;
  const mo = parts[1] ?? 1;
  const d = parts[2] ?? 1;
  const hm = hhmm.split(":").map(Number);
  const hh = hm[0] ?? 0;
  const mm = hm[1] ?? 0;
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Santiago", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "numeric", minute: "numeric", second: "numeric", hour12: false,
  });
  // Candidato UTC suponiendo offset 0; el offset real sale de
  // formatear el candidato y medir la diferencia contra la hora
  // de pared pedida. Se itera: guess += (pared pedida - pared
  // formateada) hasta que coincidan (converge en 1-2 pasos).
  const targetAsUtc = Date.UTC(y, mo - 1, d, hh, mm, 0);
  let guess = targetAsUtc;
  for (let i = 0; i < 3; i++) {
    const parts = fmt.formatToParts(new Date(guess));
    const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
    const asUtc = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour") % 24, get("minute"), get("second"));
    const diff = targetAsUtc - asUtc;
    if (diff === 0) break;
    guess += diff;
  }
  return guess;
}

/** Próximo inicio de franja (>= nowMs): hoy si queda alguna,
 *  si no mañana a las 12:00. Para programar next_run_at dentro
 *  de franja. */
export function nextSlotStartMs(nowMs: number): number {
  const day = santiagoDayKeyMs(nowMs);
  for (const w of PUBLISH_SLOT_WINDOWS) {
    const hh = String(Math.floor(w.startMin / 60)).padStart(2, "0");
    const mm = String(w.startMin % 60).padStart(2, "0");
    const start = santiagoWallToUtcMs(day, `${hh}:${mm}`);
    if (start >= nowMs) return start;
  }
  const tomorrow = santiagoDayKeyMs(nowMs + 36 * 3600 * 1000);
  return santiagoWallToUtcMs(tomorrow, "12:00");
}

// ---------------------------------------------------------------------------
// HORARIO POR PLATAFORMA (orden usuario 2026-11-22 01:11):
// a qué hora (Chile) sube cada plataforma, vídeos diferentes
// para kaez y seba (cada uno su cola; nunca el mismo vídeo en
// ambas cuentas). 3 al día por plataforma (mínimo, orden
// 2026-11-21 20:52). YouTube e Instagram al inicio de franja,
// TikTok y Facebook una hora después, para que no salgan todas
// a la vez. El auto-enqueue programa next_run_at a la próxima
// hora de SU plataforma (ver nextPlatformHourMs); la manual
// ("Publicar ahora", force_now) sigue inmediata. Fuente única:
// este mapa (no hay copia en settings; si el usuario cambia
// las horas, se cambia aquí y se redespliega).
// ---------------------------------------------------------------------------
export const PLATFORM_SCHEDULE: Record<string, string[]> = {
  // YouTube 6/día (decisión del usuario 2026-11-23): 6 huecos
  // con 2 h entre ellos (youtube_spacing_hours=2), todos dentro
  // de las franjas del cron (12:00-16:00 y 18:00-01:00 Chile;
  // 16:00 queda fuera porque el cron no corre ahí). La lista va
  // en orden cronológico desde medianoche para que
  // nextPlatformHourMs resuelva bien el hueco 00:00 (mañana
  // 00:00 = esta medianoche).
  youtube_shorts: ["00:00", "12:00", "14:00", "18:00", "20:00", "22:00"],
  tiktok: ["13:00", "19:00", "22:00"],
  instagram_reels: ["12:00", "18:00", "21:00"],
  facebook_reels: ["13:00", "19:00", "22:00"],
};

/** Próxima hora de una plataforma (hora Chile): si estamos
 *  dentro de una hora programada (60 min desde su inicio),
 *  vence AHORA; si no, la próxima hora de hoy; si no queda
 *  ninguna hoy, mañana a la primera. Para programar next_run_at
 *  del auto-enqueue a la hora de SU plataforma. */
export function nextPlatformHourMs(platform: string, nowMs: number): number {
  const hours = PLATFORM_SCHEDULE[platform] ?? PLATFORM_SCHEDULE.youtube_shorts!;
  const day = santiagoDayKeyMs(nowMs);
  for (const hhmm of hours) {
    const start = santiagoWallToUtcMs(day, hhmm);
    if (nowMs >= start && nowMs < start + 60 * 60 * 1000) return nowMs;
    if (start > nowMs) return start;
  }
  // Mañana: +24 h (no +36 h: a partir del mediodía, +36 h cae
  // en pasado mañana). nowMs+24 h siempre tiene el dayKey de
  // mañana.
  const tomorrow = santiagoDayKeyMs(nowMs + 24 * 3600 * 1000);
  return santiagoWallToUtcMs(tomorrow, hours[0]!);
}

// ---------------------------------------------------------------------------
// DÍA LIBRE SEMANAL (regla 7): 1 día libre por semana por cuenta,
// rotando entre cuentas. Determinista (hash cuenta+semana ISO),
// SIN columna nueva: no se guarda nada, se calcula. Semana ISO
// en America/Santiago; el día libre es 0=domingo..6=sábado.
// ---------------------------------------------------------------------------
export function santiagoWeekKey(ms: number): string {
  const dayKey = santiagoDayKeyMs(ms);
  const parts = dayKey.split("-").map(Number);
  const y = parts[0] ?? 1970;
  const mo = parts[1] ?? 1;
  const d = parts[2] ?? 1;
  const date = new Date(Date.UTC(y, mo - 1, d));
  // Semana ISO: el jueves de la semana manda el año.
  const dow = (date.getUTCDay() + 6) % 7; // lunes=0
  const thursday = new Date(date);
  thursday.setUTCDate(date.getUTCDate() - dow + 3);
  const year = thursday.getUTCFullYear();
  const jan1 = new Date(Date.UTC(year, 0, 1));
  const week = Math.floor((thursday.getTime() - jan1.getTime()) / (7 * 86400_000)) + 1;
  return `${year}-W${String(week).padStart(2, "0")}`;
}

/** Día libre (0=domingo..6=sábado) de una cuenta en una semana. */
export function restWeekday(seed: string, weekKey: string): number {
  return hashSeed(`${seed}|${weekKey}`) % 7;
}

/** ¿Es hoy el día libre de esta cuenta? */
export function isRestDay(seed: string, nowMs: number): boolean {
  const weekday = new Date(santiagoDayKeyMs(nowMs) + "T12:00:00Z").getUTCDay();
  return weekday === restWeekday(seed, santiagoWeekKey(nowMs));
}

// ---------------------------------------------------------------------------
// TRASLADO ÚNICO DEL DÍA LIBRE (2026-11-20, cutover 21:00, $0).
// SIN MIGRACIÓN: mismo patrón que `account_pauses` (regla 6): el
// traslado vive en settings (JSON por tenant, clave
// `rest_day_overrides`: { "<accountId>": { "week": "2026-W47",
// "weekday": 0 } }). Solo vale para esa semana ISO: al cambiar de
// semana caduca solo y vuelve el hash. `weekday` 0=domingo..6=sábado.
// Uso: una cuenta que publica en su día libre con un job manual=1
// (el gate ya lo salta, gate.ts:227) traslada su día libre a otro
// día de ESA semana para seguir teniendo 1 día libre/semana.
// Sin override, todo sigue exactamente igual que antes.
// ---------------------------------------------------------------------------
export interface RestDayOverride {
  week: string;
  weekday: number;
}
export type RestDayOverrides = Record<string, RestDayOverride>;

export function getRestDayOverrides(settings: Record<string, unknown>): RestDayOverrides {
  const raw = settings.rest_day_overrides;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out: RestDayOverrides = {};
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (!v || typeof v !== "object" || Array.isArray(v)) continue;
    const o = v as Record<string, unknown>;
    if (typeof o.week !== "string" || typeof o.weekday !== "number") continue;
    if (!Number.isInteger(o.weekday) || o.weekday < 0 || o.weekday > 6) continue;
    out[k] = { week: o.week, weekday: o.weekday };
  }
  return out;
}

/** Día libre vigente de una cuenta en una semana: override si lo
 *  hay para esa semana, si no el hash determinista. */
export function restWeekdayEffective(
  seed: string,
  weekKey: string,
  settings?: Record<string, unknown>,
  accountId?: number | string,
): number {
  if (settings !== undefined && accountId !== undefined) {
    const o = getRestDayOverrides(settings)[String(accountId)];
    if (o && o.week === weekKey) return o.weekday;
  }
  return restWeekday(seed, weekKey);
}

/** ¿Es hoy el día libre vigente de esta cuenta (con override)? */
export function isRestDayEffective(
  seed: string,
  nowMs: number,
  settings?: Record<string, unknown>,
  accountId?: number | string,
): boolean {
  const weekday = new Date(santiagoDayKeyMs(nowMs) + "T12:00:00Z").getUTCDay();
  return weekday === restWeekdayEffective(seed, santiagoWeekKey(nowMs), settings, accountId);
}

/** Devuelve UNOS settings nuevos con el traslado puesto (no muta).
 *  Los overrides de semanas pasadas se limpian al escribir. */
export function withRestDayOverride(
  settings: Record<string, unknown>,
  accountId: number | string,
  weekKey: string,
  weekday: number,
): Record<string, unknown> {
  const overrides = getRestDayOverrides(settings);
  for (const [k, v] of Object.entries(overrides)) {
    if (v.week !== weekKey) delete overrides[k];
  }
  overrides[String(accountId)] = { week: weekKey, weekday };
  return { ...settings, rest_day_overrides: overrides };
}

export const WEEKDAY_NAMES_ES = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];

// ---------------------------------------------------------------------------
// PAUSA POR SPAM/LÍMITE (regla 6): 48 h sin publicar en esa cuenta.
// SIN MIGRACIÓN (decisión 2026-11-19): no hay columna nueva en
// api_accounts; la pausa vive en settings (JSON por tenant,
// clave `account_pauses`: { "<accountId>": pausedUntilMs }).
// Razones: (1) settings ya es JSON por tenant y se conserva en
// PUT /api/settings (effectiveSettings hace spread de lo crudo);
// (2) la pausa caduca sola a las 48 h y no necesita migraciones
// en D1 remota; (3) el gate la lee del mismo settings que ya
// carga. Si algún día hace falta listar pausas en SQL, se migra
// entonces a columna paused_until.
// ---------------------------------------------------------------------------
export type AccountPauses = Record<string, number>;

export function getAccountPauses(settings: Record<string, unknown>): AccountPauses {
  const raw = settings.account_pauses;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out: AccountPauses = {};
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof v === "number" && Number.isFinite(v)) out[k] = v;
  }
  return out;
}

/** ms epoch hasta el que está en pausa una cuenta, o null. */
export function accountPausedUntilMs(settings: Record<string, unknown>, accountId: number | string): number | null {
  const until = getAccountPauses(settings)[String(accountId)];
  return typeof until === "number" ? until : null;
}

/** ¿Está en pausa esta cuenta ahora? (caducada = no). */
export function isAccountPaused(settings: Record<string, unknown>, accountId: number | string, nowMs = Date.now()): boolean {
  const until = accountPausedUntilMs(settings, accountId);
  return until !== null && nowMs < until;
}

/** Devuelve UNOS settings nuevos con la pausa puesta (no muta).
 *  Las pausas caducadas se limpian al escribir. */
export function withAccountPause(
  settings: Record<string, unknown>,
  accountId: number | string,
  untilMs: number,
  nowMs = Date.now(),
): Record<string, unknown> {
  const pauses = getAccountPauses(settings);
  for (const [k, v] of Object.entries(pauses)) {
    if (v <= nowMs) delete pauses[k];
  }
  pauses[String(accountId)] = untilMs;
  return { ...settings, account_pauses: pauses };
}

/** Estado que le toca a una cuenta con estos datos, sin tocar D1.
 *  El tope vigente sale de los settings efectivos (plan/máx POR
 *  PLATAFORMA) y de la etapa de calentamiento: semana 1/2 = 1/día;
 *  semana 3 = plan normal sin acceso al máx; establecida = plan y
 *  máx normales. Con strikes > 0 nunca es establecida. */
export function computeMaturity(
  input: MaturityInput,
  platform: string,
  eff: EffectiveSettings,
  nowMs = Date.now(),
): MaturityInfo {
  const days = input.first_published_at
    ? Math.floor((nowMs - input.first_published_at) / 86_400_000)
    : null;
  const stage = warmupStage(days, input.published_count, input.strikes);
  const established = stage === "establecida";
  const plan = platformPlan(eff, platform);
  const max = platformMax(eff, platform);
  // Tope de cuenta en CALENTAMIENTO (2026-11-19): semana 1 y 2,
  // 1/día en TODAS las plataformas; semana 3, el plan normal
  // (pero sin acceso al máximo duro: el límite ES el plan).
  const newDaily = platform === "youtube" ? eff.new_account_daily_youtube : eff.new_account_daily;
  const warmupPlan = stage === "semana1" || stage === "semana2"
    ? Math.min(newDaily, plan, max)
    : plan;
  const limit = established ? max : warmupPlan;
  return {
    ...input,
    status: established ? "establecida" : "nueva",
    stage,
    days_since_first_published: days,
    warmup_remaining_days: days === null ? WARMUP_MIN_DAYS : Math.max(0, WARMUP_MIN_DAYS - days),
    warmup_remaining_pubs: Math.max(0, WARMUP_MIN_PUBLISHED - input.published_count),
    effective_daily_limit: limit,
    effective_daily_plan: warmupPlan,
    effective_daily_max: max,
  };
}

/** Recalcula la madurez de las cuentas del tenant, como mucho UNA
 *  vez al día por cuenta (maturity_checked_day). Devuelve cuántas
 *  cuentas cambiaron de estado. Lo llaman el cron (vía
 *  runWorkerCycle), POST /publish y GET /api/accounts: en todos los
 *  casos el tope de la puerta es el guardado, no un cálculo nuevo. */
export async function refreshAccountMaturity(
  db: D1Database,
  tenant: Tenant,
  eff: EffectiveSettings,
  nowMs = Date.now(),
): Promise<{ checked: number; changed: number }> {
  const today = santiagoDayKeyMs(nowMs);
  const rows = await db
    .prepare("SELECT id, platform, first_published_at, published_count, strikes, maturity, maturity_checked_day FROM api_accounts WHERE tenant_id = ?")
    .bind(tenant)
    .all<{
      id: number; platform: string; first_published_at: number | null;
      published_count: number; strikes: number; maturity: string; maturity_checked_day: string | null;
    }>();
  let checked = 0;
  let changed = 0;
  // A2 (2026-11-20, cuota D1): los UPDATE de madurez de un ciclo van
  // en UNA sola operación batch, no uno a uno. El log por cuenta que
  // cambia de estado sigue siendo secuencial (tiene su propia
  // lectura de dedupe y no se puede meter en el batch).
  const maturityUpdates: D1PreparedStatement[] = [];
  for (const r of rows.results) {
    if (r.maturity_checked_day === today) continue;
    checked++;
    const info = computeMaturity(
      { first_published_at: r.first_published_at, published_count: r.published_count ?? 0, strikes: r.strikes ?? 0 },
      r.platform, eff, nowMs,
    );
    // El UPDATE siempre cambia algo aquí: maturity_checked_day pasa
    // a hoy (o el estado cambia). Si el día ya es hoy, el `continue`
    // de arriba lo salta: nunca se ejecuta un UPDATE sin cambio.
    maturityUpdates.push(
      db
        .prepare("UPDATE api_accounts SET maturity = ?, maturity_checked_day = ? WHERE id = ? AND tenant_id = ?")
        .bind(info.status, today, r.id, tenant),
    );
    if ((r.maturity || "nueva") !== info.status) {
      changed++;
      await logWorkerEvent(db, "madurez",
        `Cuenta #${r.id} (${r.platform}): ${r.maturity || "nueva"} → ${info.status} (${info.published_count} publicaciones, ${info.days_since_first_published ?? "–"} días, ${info.strikes} strikes; tope vigente ${info.effective_daily_limit}/día).`, tenant);
    }
  }
  if (maturityUpdates.length > 0) await db.batch(maturityUpdates);
  return { checked, changed };
}

/** Info de madurez de una cuenta (para GET /api/accounts y Ajustes):
 *  estado GUARDADO (el que aplica el gate) + datos para mostrar.
 *  La ETAPA (semana1/2/3/establecida) se calcula con los datos
 *  actuales: es informativa y siempre coherente con los topes
 *  del estado guardado (si el cálculo difiere, se verá al
 *  recálculo diario, no antes). */
export function maturityOut(row: ApiAccountRow, eff: EffectiveSettings, nowMs = Date.now()): MaturityInfo & { stored_status: AccountMaturity } {
  const info = computeMaturity(
    { first_published_at: row.first_published_at, published_count: row.published_count ?? 0, strikes: row.strikes ?? 0 },
    row.platform, eff, nowMs,
  );
  const stored = row.maturity === "establecida" ? "establecida" : "nueva";
  // El tope vigente es el del estado GUARDADO (el que aplica el
  // gate hasta el próximo recálculo diario): establecida guardada
  // = plan y máx; nueva guardada = tope de calentamiento según su
  // etapa (semana 1/2: 1/día; semana 3: plan sin máx).
  const plan = platformPlan(eff, row.platform);
  const max = platformMax(eff, row.platform);
  const newDaily = row.platform === "youtube" ? eff.new_account_daily_youtube : eff.new_account_daily;
  const warmupPlan = info.stage === "semana1" || info.stage === "semana2"
    ? Math.min(newDaily, plan, max)
    : plan;
  const limit = stored === "establecida" ? max : warmupPlan;
  return { ...info, status: stored, stored_status: stored, effective_daily_limit: limit, effective_daily_plan: stored === "establecida" ? plan : warmupPlan, effective_daily_max: max };
}
export function isGtaViGame(game: unknown): boolean {
  if (typeof game !== "string") return false;
  const n = game.toLowerCase().replace(/[^a-z0-9]/g, "");
  return n === "gta6" || n === "gtavi" || n === "grandtheftautovi" || n === "grandtheftauto6";
}

export interface PlatformPublishEntry {
  state: "pendiente" | "programado" | "publicado" | "fallido" | "expirado" | "cancelado";
  scheduledFor: string | null;
  publishedAt: string | null;
  note: string;
  since: string;
  updatedAt: string;
}

export function parsePlatforms(raw: string): PanelPlatform[] {
  try {
    const arr: unknown = JSON.parse(raw);
    if (!Array.isArray(arr)) return [];
    return arr.filter((p): p is PanelPlatform => (PANEL_PLATFORMS as string[]).includes(String(p)));
  } catch {
    return [];
  }
}

export function parsePublishStates(raw: string): Record<string, PlatformPublishEntry> {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return parsed as Record<string, PlatformPublishEntry>;
  } catch {
    return {};
  }
}

export interface ApiAccountRow {
  id: number;
  tenant_id: Tenant;
  platform: string;
  language: string;
  label: string;
  credential: string; // legacy, debe quedar vacío tras la migración
  credential_enc: string; // v1:... (AES-GCM) o ""
  extra: string; // legacy
  extra_enc: string;
  status: string;
  note: string;
  created_at: number;
  verified_at: number | null;
  verify_ok: number | null;
  verify_message: string;
  /** 0043: último cambio de credencial (ms UNIX) o null si no hay
   *  cambio registrado (filas anteriores a la migración). */
  credential_changed_at?: number | null;
  // 0017: escalado automático de topes por antigüedad/actividad.
  first_published_at: number | null;
  published_count: number;
  strikes: number;
  maturity: string; // "nueva" | "establecida" (estado guardado)
  maturity_checked_day: string | null;
}

export interface PublishJobRow {
  id: number;
  clip_id: number;
  platform: PanelPlatform;
  api_account_id: number | null;
  state: "scheduled" | "processing" | "posted" | "failed" | "draft" | "awaiting_captcha";
  idempotency_key: string;
  resume_id: string | null;
  platform_id: string | null;
  evidence_url: string | null;
  title: string;
  caption: string;
  r2_key: string | null;
  // 0019 (DOS COLAS): idioma del clip al encolar ("es" | "en").
  language: string;
  attempts: number;
  last_error: string | null;
  retryable: number;
  created_at: number;
  updated_at: number;
}

export interface Settings {
  data: Record<string, unknown>;
}

const nowMs = () => Date.now();

/** ¿Dos valores guardados son el mismo? undefined y null cuentan
 *  como el mismo "vacío" (D1 devuelve NULL donde el código pasa
 *  undefined); los objetos se comparan por su JSON. Fuente única
 *  de la comparación "¿cambió algo?" (A2, 2026-11-20, cuota D1):
 *  un UPDATE que no cambia ningún valor no se ejecuta (ver
 *  isUnchanged y cada helper de escritura que lo usa). */
export function sameValue(a: unknown, b: unknown): boolean {
  const na = a === undefined ? null : a;
  const nb = b === undefined ? null : b;
  if (na === nb) return true;
  if (typeof na === "object" && typeof nb === "object" && na !== null && nb !== null) {
    return JSON.stringify(na) === JSON.stringify(nb);
  }
  return false;
}

/** ¿Todos los valores nuevos ya son los actuales? Solo se comparan
 *  las columnas de negocio que el llamador pasa en `next`: las
 *  marcas de tiempo (updated_at, verified_at) NUNCA entran aquí,
 *  porque siempre cambiarían y ningún UPDATE se saltaría jamás.
 *  `current` es la fila leída (cualquier interfaz de fila). */
export function isUnchanged(current: object, next: Record<string, unknown>): boolean {
  const c = current as Record<string, unknown>;
  return Object.entries(next).every(([k, v]) => sameValue(c[k], v));
}

/** Usuario de login (panel_users, migración 0012). */
export interface LoginUser {
  username: string;
  tenant: Tenant;
  password_hash: string;
}

export async function getLoginUser(db: D1Database, username: string): Promise<LoginUser | null> {
  return db
    .prepare("SELECT username, tenant, password_hash FROM panel_users WHERE username = ? AND status = 'activo'")
    .bind(username)
    .first<LoginUser>();
}

export async function getClip(db: D1Database, clipId: number, tenant: Tenant): Promise<ClipRow | null> {
  return db.prepare("SELECT * FROM clips WHERE id = ? AND tenant_id = ?").bind(clipId, tenant).first<ClipRow>();
}

export async function listQueueClips(db: D1Database, tenant: Tenant): Promise<ClipRow[]> {
  // Cola en 2 segmentos (diseño trazabilidad-live-prioridad §4):
  // LIVE primero, VOD después; dentro de cada segmento, primero
  // los de orden manual (manual_rank, 0024, NULL = automático y va
  // después), luego por puntos (clips.score: la prioridad ya
  // calculada por el cerebro — la fórmula vive allí y NO se toca,
  // aquí solo se ordena) y desempate por fecha. origin = 'live' es
  // el único valor LIVE; cualquier otro ('vod', NULL, desconocido)
  // cae en el segmento VOD.
  // El orden se hace en JS (no en SQL): las bases de test sin la
  // columna manual_rank (o los mocks FakeD1) devuelven undefined
  // y se trata como NULL, y así el orden es idéntico en D1 real,
  // SQLite local y mocks.
  const res = await db
    .prepare(
      "SELECT * FROM clips WHERE tenant_id = ? AND archived = 0 AND status IN ('en_carpeta','aprobado','programado')",
    )
    .bind(tenant)
    .all<ClipRow>();
  // Streamer visible en la Cola (2026-11-19): el nombre del
  // influencer por clips.influencer_id. Segunda consulta barata
  // (18 filas) para no tocar la consulta principal ni su orden.
  // Si la tabla no existe (bases de test mínimas), se sigue sin
  // streamer en vez de tumbar la Cola.
  const streamerById = new Map<number, string>();
  try {
    const infs = await db
      .prepare("SELECT id, name FROM influencers WHERE tenant_id = ?")
      .bind(tenant)
      .all<{ id: number; name: string }>();
    for (const inf of infs.results ?? []) {
      if (typeof inf.id === "number" && typeof inf.name === "string") {
        streamerById.set(inf.id, inf.name);
      }
    }
  } catch {
    // sin tabla influencers: streamer queda null, la Cola igual carga
  }
  for (const row of res.results) {
    row.streamer =
      row.influencer_id === null || row.influencer_id === undefined
        ? null
        : (streamerById.get(row.influencer_id) ?? null);
  }
  // Boost por tops medidos (2026-11-19): los clips con medición
  // reciente suben dentro de su segmento según su bonus (ver
  // boostBonusByClip). Si la lectura de clip_stats falla (tabla
  // ausente en bases de test mínimas, mock sin ella), el boost es
  // vacío y la Cola queda EXACTAMENTE como antes: medir nunca
  // tumba la Cola.
  let boost = new Map<number, number>();
  try {
    boost = boostBonusByClip(await listClipStats(db, tenant));
  } catch {
    boost = new Map();
  }
  return res.results.sort((a, b) => queueClipCompare(a, b, boost));
}

/** Clips publicados (status='publicado', cierre §9: todas las
 *  plataformas en terminal y al menos una publicada), para la
 *  sección "Subidos" de la Cola: ahí se ve por clip dónde se
 *  subió, dónde falta y si espera re-subida (retención 24 h,
 *  0039). Los más recientes primero; sin streamer (la Cola es la
 *  que lo resuelve, aquí solo se lista). */
export async function listPublishedClips(db: D1Database, tenant: Tenant, limit = 50): Promise<ClipRow[]> {
  const res = await db
    .prepare(
      "SELECT * FROM clips WHERE tenant_id = ? AND archived = 0 AND status = 'publicado' " +
        "ORDER BY COALESCE(published_at, updated_at) DESC LIMIT ?",
    )
    .bind(tenant, limit)
    .all<ClipRow>()
    .catch(() => ({ results: [] as ClipRow[] }));
  return res.results;
}

/** Prioridad base de un clip para ordenar la Cola: viral.priority
 *  si existe, o clips.score si no (la prioridad ya calculada por
 *  el cerebro). No se toca aquí: solo se lee. */
export function baseQueuePriority(clip: ClipRow): number {
  const p = parseViralJson(clip.viral).priority;
  return typeof p === "number" && Number.isFinite(p) ? p : clip.score;
}

/** Prioridad efectiva con boost: base + bonus, con tope 100. */
export function effectiveQueuePriority(clip: ClipRow, bonus = 0): number {
  return Math.min(100, baseQueuePriority(clip) + bonus);
}

function medianOf(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) return sorted[mid] ?? null;
  const a = sorted[mid - 1];
  const b = sorted[mid];
  if (a === undefined || b === undefined) return null;
  return (a + b) / 2;
}

/** Normaliza la plataforma de una medición a clave API. */
function normalizeBoostPlatform(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const s = v.trim().toLowerCase();
  if (s === "youtube" || s === "youtube_shorts") return "youtube";
  if (s === "tiktok") return "tiktok";
  if (s === "instagram" || s === "instagram_reels") return "instagram";
  if (s === "facebook" || s === "facebook_reels") return "facebook";
  return null;
}

/** Bonus de ordenación por tops medidos (2026-11-19, auto-boost):
 *  por clip con medición, ratio = mediana_p(views_clip,p /
 *  median_views_p) con la ÚLTIMA medición por (clip, plataforma)
 *  dentro de los últimos 7 días; bonus = 0 si no hay medición o
 *  ratio < 1; si ratio >= 1, bonus = min(10, round(5*log2(ratio))).
 *  La mediana por plataforma se calcula SOLO con mediciones
 *  reales (sin medición no hay mediana, nunca 0). Pura: no toca
 *  D1, así se prueba sin mocks. */
export function boostBonusByClip(stats: ClipStatRow[], nowMs = Date.now()): Map<number, number> {
  const WEEK_MS = 7 * 86400_000;
  const latest = new Map<string, ClipStatRow>();
  for (const s of stats) {
    if (typeof s.views !== "number") continue;
    if (nowMs - s.recorded_at > WEEK_MS) continue;
    const platform = normalizeBoostPlatform(s.platform);
    if (!platform) continue;
    const key = `${s.clip_id}|${platform}`;
    const prev = latest.get(key);
    if (!prev || s.recorded_at > prev.recorded_at || (s.recorded_at === prev.recorded_at && s.id > prev.id)) {
      latest.set(key, { ...s, platform });
    }
  }
  const viewsByPlatform = new Map<string, number[]>();
  for (const s of latest.values()) {
    const list = viewsByPlatform.get(s.platform) ?? [];
    list.push(s.views as number);
    viewsByPlatform.set(s.platform, list);
  }
  const medianByPlatform = new Map<string, number | null>();
  for (const [p, views] of viewsByPlatform) medianByPlatform.set(p, medianOf(views));
  const ratiosByClip = new Map<number, number[]>();
  for (const s of latest.values()) {
    const med = medianByPlatform.get(s.platform);
    if (med === null || med === undefined || med <= 0) continue;
    const list = ratiosByClip.get(s.clip_id) ?? [];
    list.push((s.views as number) / med);
    ratiosByClip.set(s.clip_id, list);
  }
  const out = new Map<number, number>();
  for (const [clipId, ratios] of ratiosByClip) {
    const ratio = medianOf(ratios);
    if (ratio === null || ratio < 1) continue;
    const bonus = Math.min(10, Math.round(5 * Math.log2(ratio)));
    if (bonus > 0) out.set(clipId, bonus);
  }
  return out;
}

/** Comparador de la Cola: segmento, orden manual, score, fecha.
 *  Con `boost` (map clip_id → bonus por tops medidos), un clip con
 *  bonus > 0 se ordena por su prioridad efectiva (base + bonus,
 *  tope 100) en vez de por clips.score; sin bonus, el orden es
 *  EXACTAMENTE el de antes (clips.score). El boost solo reordena
 *  DENTRO de cada segmento (un VOD medido nunca pasa sobre un
 *  LIVE) y el orden manual sigue mandando sobre todo. */
export function queueClipCompare(a: ClipRow, b: ClipRow, boost?: Map<number, number>): number {
  const segA = a.origin === "live" ? 0 : 1;
  const segB = b.origin === "live" ? 0 : 1;
  if (segA !== segB) return segA - segB;
  const rankA = typeof a.manual_rank === "number" ? a.manual_rank : null;
  const rankB = typeof b.manual_rank === "number" ? b.manual_rank : null;
  if (rankA !== null || rankB !== null) {
    if (rankA === null) return 1;
    if (rankB === null) return -1;
    if (rankA !== rankB) return rankA - rankB;
  }
  const bonusA = boost?.get(a.id) ?? 0;
  const bonusB = boost?.get(b.id) ?? 0;
  const scoreA = bonusA > 0 ? effectiveQueuePriority(a, bonusA) : a.score;
  const scoreB = bonusB > 0 ? effectiveQueuePriority(b, bonusB) : b.score;
  if (scoreA !== scoreB) return scoreB - scoreA;
  return b.created_at - a.created_at;
}

export type QueueMoveAction = "first" | "up" | "down" | "auto";

export interface QueueMoveResult {
  ok: boolean;
  clipId: number;
  action: QueueMoveAction;
  /** Posición (1-based) del clip en su segmento tras el movimiento. */
  position: number | null;
  segmentTotal: number;
  manual: boolean;
  message: string;
}

/** Sentencia UPDATE de manual_rank, sin ejecutar: para meter varios
 *  cambios de orden en un solo db.batch (A2, 2026-11-20). */
function setClipManualRankStmt(db: D1Database, clipId: number, tenant: Tenant, rank: number | null): D1PreparedStatement {
  return db
    .prepare("UPDATE clips SET manual_rank = ?, updated_at = ? WHERE id = ? AND tenant_id = ?")
    .bind(rank, nowMs(), clipId, tenant);
}

async function setClipManualRank(db: D1Database, clipId: number, tenant: Tenant, rank: number | null): Promise<void> {
  // A2: sin cambio no hay UPDATE. La lectura es gratis en cuota; si
  // falla, se escribe igual (el orden nunca rompe la Cola).
  const current = await db
    .prepare("SELECT manual_rank FROM clips WHERE id = ? AND tenant_id = ?")
    .bind(clipId, tenant)
    .first<{ manual_rank: number | null }>()
    .catch(() => null);
  if (current && sameValue(current.manual_rank, rank)) return;
  await setClipManualRankStmt(db, clipId, tenant, rank).run();
}

/** Mueve un clip de la Cola desde Telegram (v3, idea 49).
 *  Solo dentro de SU segmento (un VOD nunca pasa por encima de un
 *  LIVE) y solo clips de este tenant (getClip filtra por tenant).
 *  /primero: el menor manual_rank del segmento. /subir y /bajar:
 *  intercambian el puesto con el vecino; si alguno de los dos no
 *  tenía orden manual, se materializa el orden actual del segmento
 *  (posición 1..n) antes de intercambiar, para que el movimiento
 *  sea exactamente un puesto. /auto: vuelve al orden automático
 *  (manual_rank NULL). Nunca toca clips.score ni clips.viral. */
export async function moveQueueClip(
  db: D1Database,
  clipId: number,
  tenant: Tenant,
  action: QueueMoveAction,
): Promise<QueueMoveResult> {
  const clip = await getClip(db, clipId, tenant);
  if (!clip) {
    return { ok: false, clipId, action, position: null, segmentTotal: 0, manual: false, message: `No hay ningún clip #${clipId} en tu cuenta.` };
  }
  const queue = await listQueueClips(db, tenant);
  const segment = clip.origin === "live" ? "live" : "vod";
  const inSegment = queue.filter((c) => (c.origin === "live" ? "live" : "vod") === segment);
  const index = inSegment.findIndex((c) => c.id === clipId);
  if (index === -1) {
    return { ok: false, clipId, action, position: null, segmentTotal: inSegment.length, manual: false, message: `El clip #${clipId} no está en la Cola (ya se publicó, se archivó o está en borrador).` };
  }
  const title = (clip.title ?? "").trim() || "Clip sin título";
  if (action === "auto") {
    if (typeof clip.manual_rank !== "number") {
      return { ok: true, clipId, action, position: index + 1, segmentTotal: inSegment.length, manual: false, message: `El clip #${clipId} «${title}» ya estaba en orden automático.` };
    }
    await setClipManualRank(db, clipId, tenant, null);
    const after = await listQueueClips(db, tenant);
    const pos = after.filter((c) => (c.origin === "live" ? "live" : "vod") === segment).findIndex((c) => c.id === clipId) + 1;
    return { ok: true, clipId, action, position: pos, segmentTotal: inSegment.length, manual: false, message: `El clip #${clipId} «${title}» vuelve al orden automático (posición ${pos} de ${inSegment.length} en ${segment === "live" ? "LIVE" : "VOD"}).` };
  }
  if (action === "first") {
    if (index === 0) {
      return { ok: true, clipId, action, position: 1, segmentTotal: inSegment.length, manual: typeof clip.manual_rank === "number", message: `El clip #${clipId} «${title}» ya es el primero de ${segment === "live" ? "LIVE" : "VOD"}.` };
    }
    const ranks = inSegment.map((c) => c.manual_rank).filter((r): r is number => typeof r === "number");
    const newRank = (ranks.length ? Math.min(...ranks) : 1) - 1;
    await setClipManualRank(db, clipId, tenant, newRank);
    return { ok: true, clipId, action, position: 1, segmentTotal: inSegment.length, manual: true, message: `El clip #${clipId} «${title}» ahora es el primero de ${segment === "live" ? "LIVE" : "VOD"} (orden manual).` };
  }
  // /subir y /bajar: vecino dentro del segmento.
  const neighbourIndex = action === "up" ? index - 1 : index + 1;
  if (neighbourIndex < 0 || neighbourIndex >= inSegment.length) {
    const edge = action === "up" ? "primero" : "último";
    return { ok: true, clipId, action, position: index + 1, segmentTotal: inSegment.length, manual: typeof clip.manual_rank === "number", message: `El clip #${clipId} «${title}» ya es el ${edge} de ${segment === "live" ? "LIVE" : "VOD"}.` };
  }
  const neighbour = inSegment[neighbourIndex]!;
  // A2: todos los cambios de orden del movimiento van en UN solo
  // batch, y solo los que cambian algo. El orden actual ya está
  // cargado en inSegment (manual_rank por clip): no hace falta
  // re-leer fila por fila para comparar.
  const wanted = new Map<number, number>();
  // Materializar el orden actual del segmento si alguno de los dos
  // no tiene manual_rank: posición 1..n según la Cola de ahora.
  const needsMaterialize = typeof clip.manual_rank !== "number" || typeof neighbour.manual_rank !== "number";
  if (needsMaterialize) {
    for (let i = 0; i < inSegment.length; i++) wanted.set(inSegment[i]!.id, i + 1);
  }
  const rankClip = typeof clip.manual_rank === "number" ? clip.manual_rank : index + 1;
  const rankNeighbour = typeof neighbour.manual_rank === "number" ? neighbour.manual_rank : neighbourIndex + 1;
  wanted.set(clipId, rankNeighbour);
  wanted.set(neighbour.id, rankClip);
  const currentRank = new Map(inSegment.map((c) => [c.id, c.manual_rank]));
  const rankStmts = [...wanted.entries()]
    .filter(([id, rank]) => !sameValue(currentRank.get(id), rank))
    .map(([id, rank]) => setClipManualRankStmt(db, id, tenant, rank));
  if (rankStmts.length > 0) await db.batch(rankStmts);
  const newPosition = neighbourIndex + 1;
  return {
    ok: true,
    clipId,
    action,
    position: newPosition,
    segmentTotal: inSegment.length,
    manual: true,
    message: `El clip #${clipId} «${title}» ahora es el ${newPosition}º de ${inSegment.length} en ${segment === "live" ? "LIVE" : "VOD"} (orden manual).`,
  };
}

export interface NewClip {
  source_id: number | null;
  influencer_id: number | null;
  title: string;
  start_sec: number;
  end_sec: number;
  score: number;
  reason: string;
  subtitle_style: string;
  seo_title: string;
  seo_description: string;
  seo_tags: string;
  thumb_text: string;
  platforms: string; // JSON array de PanelPlatform
  scheduled_for: string | null;
  publish_blocked: number;
  needs_human_transcription: number;
  subtitles_complete: number;
  game: string; // categoría: siempre "GTA VI" (gate, ver isGtaViGame)
  // Idioma del clip (0019): "es" | "en". El cerebro lo manda en
  // POST /api/clips; sin él, "es" (clips heredados).
  language: string;
  // 2026-11-18 (cortador en vivo): origin='live' para el segmento
  // LIVE de la Cola; cualquier otro valor cae en el segmento VOD.
  origin?: string;
  // Clave de origen (migración 0044, spec 2026-11-24): id del
  // video de YouTube, o "" para Twitch (ver ClipRow.source_key).
  source_key?: string;
  // Puntuación IA (contrato 2026-11-18): JSON de clips.viral ya
  // fusionado (ver mergeViralJson); "" = sin datos (sin medir).
  viral?: string;
}

// --- Crear clip en la Cola (POST /api/clips, 2026-11-18) ---
// Antes no existía endpoint para crear clips: la Cola solo se
// alimentaba por vías externas sin contrato (el cerebro nunca
// escribió en D1). El clip nace con status 'en_carpeta' para que
// aparezca en GET /api/clips. publish_blocked se deriva de
// subtitles_complete (regla 2026-11-17 22:10: sin subtítulos
// completos no se publica): solo se crea desbloqueado cuando el
// que lo encola declara subtítulos completos (MP4 ya quemado).
// Solo columnas de las migraciones base (0002 + 0012 + 0014): viral
// (0005) se guarda con los datos de puntuación IA si vienen en el
// POST (contrato 2026-11-18); si no vienen, queda "{}" (sin medir)
// y se afina después con PATCH.
export async function createClip(db: D1Database, tenant: Tenant, data: NewClip): Promise<ClipRow> {
  const now = nowMs();
  const origin = data.origin === "live" ? "live" : "vod";
  const language = data.language === "en" ? "en" : "es";
  const res = await db
    .prepare(
      "INSERT INTO clips (tenant_id, source_id, influencer_id, title, start_sec, end_sec, score, reason, status, " +
        "subtitle_style, seo_title, seo_description, seo_tags, thumb_text, platforms, scheduled_for, " +
        "publish_blocked, needs_human_transcription, subtitles_complete, game, origin, viral, language, source_key, created_at, updated_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )
    .bind(
      tenant, data.source_id, data.influencer_id, data.title, data.start_sec, data.end_sec,
      data.score, data.reason, "en_carpeta", data.subtitle_style, data.seo_title, data.seo_description,
      data.seo_tags, data.thumb_text, data.platforms, data.scheduled_for,
      data.publish_blocked, data.needs_human_transcription, data.subtitles_complete, data.game, origin, data.viral ?? "{}", language, data.source_key ?? "", now, now,
    )
    .run();
  const id = Number(res.meta.last_row_id);
  const row = await getClip(db, id, tenant);
  if (!row) throw new Error("No se pudo leer el clip creado.");
  return row;
}

export async function getSettings(db: D1Database, tenant: Tenant): Promise<Record<string, unknown>> {
  const row = await db.prepare("SELECT data FROM settings WHERE tenant_id = ?").bind(tenant).first<{ data: string }>();
  if (!row) return {};
  try {
    return JSON.parse(row.data) as Record<string, unknown>;
  } catch {
    return {};
  }
}

export async function saveSettings(db: D1Database, tenant: Tenant, data: Record<string, unknown>): Promise<void> {
  // A2: sin cambio no hay escritura. El upsert ON CONFLICT escribe
  // aunque data sea idéntica (updated_at siempre cambia): se compara
  // primero con lo guardado (una lectura, gratis en cuota).
  const current = await getSettings(db, tenant).catch(() => ({} as Record<string, unknown>));
  if (JSON.stringify(current) === JSON.stringify(data)) return;
  await db
    .prepare(
      "INSERT INTO settings (tenant_id, data, updated_at) VALUES (?, ?, ?) " +
        "ON CONFLICT(tenant_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
    )
    .bind(tenant, JSON.stringify(data), nowMs())
    .run();
}

// --- Logos de marca de agua (2026-11-18, migración 0025) ----------
// Un logo pertenece a un solo tenant: TODAS las consultas filtran
// por tenant_id. La key R2 vive en la fila; el objeto en el bucket
// es privado y solo se sirve con sesión (/api/watermarks/:id/image)
// o con URL firmada (/api/public-asset) para el render.
export interface WatermarkLogoRow {
  id: number;
  tenant_id: Tenant;
  r2_key: string;
  filename: string;
  content_type: string;
  size: number;
  created_at: number;
}

export async function listWatermarkLogos(db: D1Database, tenant: Tenant): Promise<WatermarkLogoRow[]> {
  const res = await db
    .prepare("SELECT id, tenant_id, r2_key, filename, content_type, size, created_at FROM watermark_logos WHERE tenant_id = ? ORDER BY created_at ASC, id ASC")
    .bind(tenant)
    .all<WatermarkLogoRow>();
  return res.results ?? [];
}

export async function getWatermarkLogo(db: D1Database, id: number, tenant: Tenant): Promise<WatermarkLogoRow | null> {
  return db
    .prepare("SELECT id, tenant_id, r2_key, filename, content_type, size, created_at FROM watermark_logos WHERE id = ? AND tenant_id = ?")
    .bind(id, tenant)
    .first<WatermarkLogoRow>();
}

export async function createWatermarkLogo(
  db: D1Database, tenant: Tenant, r2Key: string, filename: string, contentType: string, size: number, now: number,
): Promise<WatermarkLogoRow> {
  const res = await db
    .prepare("INSERT INTO watermark_logos (tenant_id, r2_key, filename, content_type, size, created_at) VALUES (?, ?, ?, ?, ?, ?)")
    .bind(tenant, r2Key, filename, contentType, size, now)
    .run();
  const id = Number(res.meta.last_row_id);
  const row = await getWatermarkLogo(db, id, tenant);
  if (!row) throw new Error("No se pudo leer el logo creado.");
  return row;
}

/** Borra la fila del tenant. Devuelve true si existía. El objeto
 *  R2 lo borra el llamador (index.ts), con la misma key. */
export async function deleteWatermarkLogo(db: D1Database, id: number, tenant: Tenant): Promise<boolean> {
  const res = await db
    .prepare("DELETE FROM watermark_logos WHERE id = ? AND tenant_id = ?")
    .bind(id, tenant)
    .run();
  return (res.meta.changes ?? 0) > 0;
}

// --- Telegram multi-tenant (v2, 2026-11-18, migración 0023) -----------
// Un chat pertenece a un solo tenant y un tenant a un solo chat.
// El código de vinculación NUNCA se guarda en claro: solo su hash
// SHA-256 (telegram_link_codes.code_hash). AISLAMIENTO: toda
// consulta filtra por tenant o por chat; ninguna función devuelve
// filas de otro tenant.
export interface TelegramLinkRow {
  chat_id: string;
  tenant_id: Tenant;
  linked_at: number;
  last_sent_day: string | null;
}

export interface TelegramLinkCodeRow {
  code_hash: string;
  tenant_id: Tenant;
  expires_at: number;
  created_at: number;
}

/** Guarda un código de vinculación (por su hash): un solo código
 *  vigente por tenant, los anteriores quedan invalidados. */
export async function createTelegramLinkCode(
  db: D1Database,
  tenant: Tenant,
  codeHash: string,
  expiresAt: number,
  now: number,
): Promise<void> {
  await db.prepare("DELETE FROM telegram_link_codes WHERE tenant_id = ?").bind(tenant).run();
  await db
    .prepare("INSERT INTO telegram_link_codes (code_hash, tenant_id, expires_at, created_at) VALUES (?, ?, ?, ?)")
    .bind(codeHash, tenant, expiresAt, now)
    .run();
}

export async function findTelegramLinkCode(db: D1Database, codeHash: string): Promise<TelegramLinkCodeRow | null> {
  return db
    .prepare("SELECT code_hash, tenant_id, expires_at, created_at FROM telegram_link_codes WHERE code_hash = ?")
    .bind(codeHash)
    .first<TelegramLinkCodeRow>();
}

export async function deleteTelegramLinkCode(db: D1Database, codeHash: string): Promise<void> {
  await db.prepare("DELETE FROM telegram_link_codes WHERE code_hash = ?").bind(codeHash).run();
}

/** Vincula un chat a un tenant: si el chat estaba en otro tenant o
 *  el tenant tenía otro chat, el vínculo anterior se reemplaza. El
 *  código que autoriza el movimiento solo lo genera la sesión del
 *  panel, así que moverlo exige ese código. */
export async function linkTelegramChat(db: D1Database, chatId: string, tenant: Tenant, now: number): Promise<void> {
  await db.prepare("DELETE FROM telegram_links WHERE chat_id = ?").bind(chatId).run();
  await db.prepare("DELETE FROM telegram_links WHERE tenant_id = ?").bind(tenant).run();
  await db
    .prepare("INSERT INTO telegram_links (chat_id, tenant_id, linked_at, last_sent_day) VALUES (?, ?, ?, NULL)")
    .bind(chatId, tenant, now)
    .run();
}

export async function unlinkTelegramChat(db: D1Database, chatId: string): Promise<void> {
  await db.prepare("DELETE FROM telegram_links WHERE chat_id = ?").bind(chatId).run();
}

export async function unlinkTelegramTenant(db: D1Database, tenant: Tenant): Promise<void> {
  await db.prepare("DELETE FROM telegram_links WHERE tenant_id = ?").bind(tenant).run();
}

export async function getTelegramLinkByTenant(db: D1Database, tenant: Tenant): Promise<TelegramLinkRow | null> {
  return db
    .prepare("SELECT chat_id, tenant_id, linked_at, last_sent_day FROM telegram_links WHERE tenant_id = ?")
    .bind(tenant)
    .first<TelegramLinkRow>();
}

export async function getTelegramLinkByChat(db: D1Database, chatId: string): Promise<TelegramLinkRow | null> {
  return db
    .prepare("SELECT chat_id, tenant_id, linked_at, last_sent_day FROM telegram_links WHERE chat_id = ?")
    .bind(chatId)
    .first<TelegramLinkRow>();
}

/** Marca el resumen diario como enviado para ese chat y día. */
export async function markTelegramSent(db: D1Database, chatId: string, day: string): Promise<void> {
  // A2: sin cambio no hay UPDATE (el resumen diario ya marcaba el
  // día en settings ADEMÁS de aquí: la doble marca ya no escribe
  // dos veces cuando el día es el mismo).
  const current = await db
    .prepare("SELECT last_sent_day FROM telegram_links WHERE chat_id = ?")
    .bind(chatId)
    .first<{ last_sent_day: string | null }>()
    .catch(() => null);
  if (current && sameValue(current.last_sent_day, day)) return;
  await db.prepare("UPDATE telegram_links SET last_sent_day = ? WHERE chat_id = ?").bind(day, chatId).run();
}

// --- Confirmación pendiente de /parar (v3.1, migración 0026) ---------
// /parar detiene TODA la publicación: exige un SÍ posterior en el
// mismo chat antes de 5 minutos. Una fila por chat, de un solo uso
// (se borra al confirmar, al cancelar o al caducar). El tenant se
// guarda para que un chat re-vinculado a otro tenant no pueda
// confirmar una parada ajena.
export interface TelegramStopConfirmationRow {
  chat_id: string;
  tenant_id: Tenant;
  expires_at: number;
}

export async function createTelegramStopConfirmation(
  db: D1Database,
  chatId: string,
  tenant: Tenant,
  expiresAt: number,
): Promise<void> {
  // A2: el DELETE del código anterior y el INSERT del nuevo van en
  // UNA sola operación batch (son independientes entre sí).
  await db.batch([
    db.prepare("DELETE FROM telegram_stop_confirmations WHERE chat_id = ?").bind(chatId),
    db
      .prepare("INSERT INTO telegram_stop_confirmations (chat_id, tenant_id, expires_at) VALUES (?, ?, ?)")
      .bind(chatId, tenant, expiresAt),
  ]);
}

export async function getTelegramStopConfirmation(db: D1Database, chatId: string): Promise<TelegramStopConfirmationRow | null> {
  return db
    .prepare("SELECT chat_id, tenant_id, expires_at FROM telegram_stop_confirmations WHERE chat_id = ?")
    .bind(chatId)
    .first<TelegramStopConfirmationRow>();
}

export async function deleteTelegramStopConfirmation(db: D1Database, chatId: string): Promise<void> {
  await db.prepare("DELETE FROM telegram_stop_confirmations WHERE chat_id = ?").bind(chatId).run();
}

export async function listApiAccounts(db: D1Database, tenant: Tenant): Promise<ApiAccountRow[]> {
  const res = await db.prepare("SELECT * FROM api_accounts WHERE tenant_id = ? ORDER BY id").bind(tenant).all<ApiAccountRow>();
  return res.results;
}

export async function getApiAccount(db: D1Database, id: number, tenant: Tenant): Promise<ApiAccountRow | null> {
  return db.prepare("SELECT * FROM api_accounts WHERE id = ? AND tenant_id = ?").bind(id, tenant).first<ApiAccountRow>();
}

export async function updateApiAccountVerification(
  db: D1Database,
  id: number,
  tenant: Tenant,
  ok: boolean,
  status: string,
  message: string,
): Promise<void> {
  // A2: sin cambio no hay UPDATE. verified_at siempre cambiaría, así
  // que NO entra en la comparación: solo las columnas de negocio
  // (verify_ok, verify_message, status). Re-verificar una cuenta que
  // ya está igual ya no escribe.
  const current = await db
    .prepare("SELECT verify_ok, verify_message, status FROM api_accounts WHERE id = ? AND tenant_id = ?")
    .bind(id, tenant)
    .first<{ verify_ok: number | null; verify_message: string; status: string }>()
    .catch(() => null);
  if (
    current &&
    isUnchanged(current, { verify_ok: ok ? 1 : 0, verify_message: message, status })
  ) {
    return;
  }
  await db
    .prepare(
      "UPDATE api_accounts SET verify_ok = ?, verified_at = ?, verify_message = ?, status = ? WHERE id = ? AND tenant_id = ?",
    )
    .bind(ok ? 1 : 0, nowMs(), message, status, id, tenant)
    .run();
}

/** V2 (validadores 2026-11-23): ¿es vigente la verificación de esta
 *  cuenta? "Conectada" solo vale si la última verificación es
 *  posterior al último cambio de credencial. Un timestamp viejo con
 *  credencial nueva es FALSO POSITIVO (la verificación es de la
 *  credencial anterior). Sin cambio registrado (null, filas
 *  anteriores a la migración 0043) la verificación vigente se
 *  respeta. Nunca verificada (verified_at null) nunca es vigente.
 *  Que verified_at no se refresque al re-verificar sin cambio es
 *  DISEÑO (ver updateApiAccountVerification, A2), no un falso
 *  positivo: aquí solo se compara contra el cambio de credencial. */
export function isAccountVerificationCurrent(acc: {
  verified_at: number | null;
  verify_ok: number | null;
  credential_changed_at?: number | null;
}): boolean {
  if (acc.verify_ok !== 1) return false;
  if (acc.verified_at === null || acc.verified_at === undefined) return false;
  if (acc.credential_changed_at === null || acc.credential_changed_at === undefined) return true;
  return acc.verified_at >= acc.credential_changed_at;
}

// --- Crear cuenta API (Frente 2, M5): POST /api/accounts ---
// La fila nace sin credencial (status 'pendiente'): la credencial
// se pega después con POST /api/accounts/:id/credential y se
// verifica con POST /api/accounts/:id/verify. Plataformas válidas:
// youtube, tiktok, meta, twitch y gemini (Meta unifica Instagram y
// Facebook; twitch y gemini solo por pegado).
export interface NewApiAccount {
  platform: string;
  language: string;
  label: string;
}

export async function getApiAccountByPlatform(
  db: D1Database,
  tenant: Tenant,
  platform: string,
  language: string,
): Promise<ApiAccountRow | null> {
  return db
    .prepare("SELECT * FROM api_accounts WHERE tenant_id = ? AND platform = ? AND language = ?")
    .bind(tenant, platform, language)
    .first<ApiAccountRow>();
}

export async function createApiAccount(db: D1Database, tenant: Tenant, data: NewApiAccount): Promise<ApiAccountRow> {
  const res = await db
    .prepare(
      "INSERT INTO api_accounts (tenant_id, platform, language, label, credential, extra, status, note, created_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )
    .bind(tenant, data.platform, data.language, data.label, "", "", "pendiente", "", nowMs())
    .run();
  const row = await getApiAccount(db, Number(res.meta.last_row_id), tenant);
  if (!row) throw new Error("No se pudo leer la cuenta creada.");
  return row;
}

// --- Jobs del usuario (Frente 2, M13): GET /api/jobs ---
// Solo lectura, por tenant. `limit` ya validado (1–100) por quien
// llama: se interpola como literal porque D1 no acepta LIMIT ? en
// prepared statements con bind en todas las rutas.
export type JobStateFilter = "scheduled" | "processing" | "posted" | "failed" | "draft" | "awaiting_captcha";

export interface JobListRow {
  id: number;
  kind: string;
  clip_id: number | null;
  platform: string | null;
  state: string;
  title: string;
  attempts: number;
  last_error: string | null;
  platform_id: string | null;
  evidence_url: string | null;
  created_at: number;
  updated_at: number;
}

export async function listJobs(
  db: D1Database,
  tenant: Tenant,
  limit: number,
  state?: JobStateFilter,
): Promise<JobListRow[]> {
  const sql =
    "SELECT id, kind, clip_id, platform, state, title, attempts, last_error, platform_id, evidence_url, created_at, updated_at " +
    "FROM jobs WHERE tenant_id = ?" +
    (state ? " AND state = ?" : "") +
    ` ORDER BY created_at DESC LIMIT ${limit}`;
  const stmt = db.prepare(sql);
  const res = state
    ? await stmt.bind(tenant, state).all<JobListRow>()
    : await stmt.bind(tenant).all<JobListRow>();
  return res.results;
}

// --- Revocación de tokens de sesión (Frente 2, B1; tabla 0015) ---
// Se guarda solo la huella SHA-256 del token (nunca el token).
export async function revokeToken(
  db: D1Database,
  tokenHash: string,
  username: string,
  tenant: Tenant,
  expiresAt: number,
): Promise<void> {
  // Idempotente: un segundo logout con el mismo token no duplica
  // la fila (la huella es PRIMARY KEY).
  if (await isTokenRevoked(db, tokenHash)) return;
  await db
    .prepare("INSERT INTO revoked_tokens (token_hash, username, tenant_id, revoked_at, expires_at) VALUES (?, ?, ?, ?, ?)")
    .bind(tokenHash, username, tenant, nowMs(), expiresAt)
    .run();
  // Purgar huellas ya caducadas (el token revocado ya no sería
  // válido por expiración aunque no estuviera en la lista).
  await db.prepare("DELETE FROM revoked_tokens WHERE expires_at < ?").bind(nowMs()).run();
}

export async function isTokenRevoked(db: D1Database, tokenHash: string): Promise<boolean> {
  const row = await db.prepare("SELECT token_hash FROM revoked_tokens WHERE token_hash = ?").bind(tokenHash).first<{ token_hash: string }>();
  return !!row;
}

// --- Cuentas sociales (tabla accounts, migración 0002 + tenant_id en 0013) ---
// La tabla accounts NO guarda credenciales: solo canal, handle y
// estado. Por eso no hay verificación real aquí (ver index.ts:
// POST /api/social-accounts/:id/verify responde 501).
export interface SocialAccountRow {
  id: number;
  tenant_id: Tenant;
  platform: string;
  label: string;
  handle: string;
  status: string;
  note: string;
  sort: number;
  // 0030: idioma del canal (es/en). Un canal es plataforma+idioma:
  // el handle de YouTube ES no es el de YouTube EN.
  language: string;
}

export async function listSocialAccounts(db: D1Database, tenant: Tenant): Promise<SocialAccountRow[]> {
  const res = await db
    .prepare("SELECT * FROM accounts WHERE tenant_id = ? ORDER BY sort ASC, id ASC")
    .bind(tenant)
    .all<SocialAccountRow>();
  return res.results;
}

export async function getSocialAccount(db: D1Database, id: number, tenant: Tenant): Promise<SocialAccountRow | null> {
  return db.prepare("SELECT * FROM accounts WHERE id = ? AND tenant_id = ?").bind(id, tenant).first<SocialAccountRow>();
}

// --- Influencers (tabla influencers, migración 0002 + tenant_id en 0012) ---
export interface InfluencerRow {
  id: number;
  tenant_id: Tenant;
  name: string;
  platform: string;
  profile_url: string;
  language: string;
  permission: number;
  active: number;
  notes: string;
  created_at: number;
  // 0018: categoría (siempre "GTA VI"), fecha del permiso
  // (YYYY-MM-DD) y plataformas de publicación (JSON).
  game: string;
  permission_date: string;
  publish_platforms: string;
}

/** Regla de lanzamiento (decisión 17 nov 2026, ampliada 18 nov 2026
 *  15:30 por el usuario): los 10 influencers del lanzamiento están
 *  activos con permiso. Rubius es el prioritario; IlloJuan se
 *  REACTIVÓ el 2026-11-18 15:30 (antes pausado, ya no). El handle se
 *  normaliza (sin @, minúsculas) para comparar; cualquier otro
 *  handle no es del lanzamiento y no se puede activar. */
export const LAUNCH_INFLUENCER_HANDLES = [
  "rubius", "illojuan", "auronplay", "ibai", "thegrefg", "xokas",
  "djmariio", "knekro", "rdjavi", "westcol",
] as const;

export function normalizeHandle(handle: string): string {
  // 2026-11-19: también quita los espacios. Sin esto, "Kai Cenat"
  // (así está guardado en influencers) no coincidía con "kaicenat"
  // (así lo manda el cerebro) y el clip moría al encolar con 400
  // ("no hay ningún influencer con el handle kaicenat") aunque la
  // revisión había pasado con 69,1 (corrida 44467542440).
  return handle.trim().replace(/^@+/, "").replace(/\s+/g, "").toLowerCase();
}

export function isLaunchInfluencer(handle: string): boolean {
  return (LAUNCH_INFLUENCER_HANDLES as readonly string[]).includes(
    normalizeHandle(handle));
}

// --- Streamers virales de campaña (2026-11-23, Content Rewards) --------
// Fuente única TS del mapa streamer -> campaña: la usan la puerta de
// activación (isActivatableInfluencer), la prioridad de cola
// (isCampaignStreamer) y la extensión la replica en lib.js
// (KNOWN_CAMPAIGNS). Tarifas BRUTAS del radar 2026-11-23; la NETA es
// bruta x 0,9 (comisión del 10% de Content Rewards) y el piso es
// $1/1k NETO (orden del usuario). BenDaDonnn: $1,00 bruto = $0,90
// neto, BAJO el piso: se puede activar como influencer (es viral,
// 645k seguidores en Twitch) pero su campaña NO da prioridad en
// cola ni se auto-asigna; solo entraría con re-verificación manual.
// Dylan Page: fuente SOLO YouTube (@dylan.page., maratón 120 h):
// el barrido en vivo (twitch/kick) no lo cubre; solo VOD-fallback.
// Bobby Van Riper: handle bobby_vanriper (heistclips + 2 reels lo
// escriben así; UNA fuente lo escribe bobby.vanriper con punto:
// variante sin confirmar, anotada en streamers.yaml). EXCLUIDO por
// la regla del usuario 2026-11-23 18:50 (solo FAMOSOS): 734
// seguidores en Twitch, 30 espectadores medios, pico 74
// (twitchmetrics.net, 23 nov 2026). Su campaña ($1,50 bruta) sigue
// en el mapa como referencia, pero NO da prioridad en cola, NO se
// auto-asigna y NO levanta la puerta de activación: un streamer
// desconocido no entra aunque pague mucho. Si supera 50.000
// seguidores, reactivar (quitar la exclusión en isFamousCampaignStreamer).
export const NET_FLOOR_PER_1K_USD = 1.0;
export const CONTENT_REWARDS_COMMISSION = 0.1;

// REGLA DEL USUARIO 2026-11-23 18:50 (también en MEMORY.md): solo
// influencers FAMOSOS, no cualquier persona. Criterio (única fuente
// TS, replicado en la extensión lib.js): >= 50.000 seguidores/
// suscriptores verificables en ALGUNA plataforma (la de la campaña
// u otra: ArthurTV pasa por YouTube >500k + IG 428k aunque en
// Twitch tenga solo ~20k). La tracción de la campaña (creadores,
// clips aprobados) es señal secundaria y NO compensa el listón.
export const FAME_MIN_FOLLOWERS = 50_000;

export interface CampaignStreamer {
  campaignId: string;
  campaignName: string;
  marketplace: string;
  grossRatePer1k: number;
  netRatePer1k: number;
  minViews: number;
  capPerClip: number;
  platforms: string[];
  /** Seguidores/suscriptores verificables (23 nov 2026). */
  followers: number;
  /** Plataforma donde se contó `followers`. */
  followersPlatform: string;
}

export const CAMPAIGN_STREAMERS: Record<string, CampaignStreamer> = {
  dasgasd3m: {
    campaignId: "051002dd-5c46-43d0-a446-43f2957ba3a9",
    campaignName: "Dasgasd3m x RUCKUS | GTA 6",
    marketplace: "content_rewards",
    grossRatePer1k: 2.0, netRatePer1k: 1.8,
    minViews: 10000, capPerClip: 150,
    platforms: ["tiktok", "youtube"],
    followers: 62500, followersPlatform: "kick",
  },
  arthurtv: {
    campaignId: "6e9ca548-f36c-42c2-abc0-61f89b4b53ba",
    campaignName: "ArthurTV — GTA Clips",
    marketplace: "content_rewards",
    grossRatePer1k: 1.5, netRatePer1k: 1.35,
    minViews: 10000, capPerClip: 300,
    platforms: ["tiktok"],
    followers: 500000, followersPlatform: "youtube",
  },
  bobbyvanriper: {
    campaignId: "f583b069-ced7-49e5-9ed3-2d3a13d6df0c",
    campaignName: "BOBBY VAN RIPER GTA VI",
    marketplace: "content_rewards",
    grossRatePer1k: 1.5, netRatePer1k: 1.35,
    minViews: 1500, capPerClip: 150,
    platforms: ["instagram", "tiktok", "youtube"],
    followers: 734, followersPlatform: "twitch",
  },
  dylanpage: {
    campaignId: "39753a1a-f1f3-491b-a8f6-0cfee0af0c97",
    campaignName: "Dylan Page GTA VI Marathon",
    marketplace: "content_rewards",
    grossRatePer1k: 1.2, netRatePer1k: 1.08,
    minViews: 3333, capPerClip: 250,
    platforms: ["instagram", "tiktok", "x", "youtube"],
    followers: 1170000, followersPlatform: "youtube",
  },
  bendadonnn: {
    campaignId: "bb66f9eb-b90e-4dd0-8cee-9f3cd566c9a0",
    campaignName: "BenDaDonnn x Puffco GTA 6",
    marketplace: "content_rewards",
    grossRatePer1k: 1.0, netRatePer1k: 0.9,
    minViews: 10000, capPerClip: 400,
    platforms: ["facebook", "instagram", "tiktok", "x", "youtube"],
    followers: 645690, followersPlatform: "twitch",
  },
};

/** Handles virales de campaña FAMOSOS (regla 18:50): solo los que
 *  pasan el listón de fama. Bobby Van Riper NO está aquí (734
 *  seguidores): su campaña existe en CAMPAIGN_STREAMERS como
 *  referencia, pero no levanta la puerta ni da prioridad. */
export const VIRAL_CAMPAIGN_HANDLES = Object.keys(CAMPAIGN_STREAMERS)
  .filter((h) => (CAMPAIGN_STREAMERS[h]?.followers ?? 0) >= FAME_MIN_FOLLOWERS);

/** ¿El streamer de campaña es famoso? (regla 18:50). */
export function isFamousCampaignStreamer(handle: string | null | undefined): boolean {
  const c = campaignForStreamer(handle);
  return c !== null && c.followers >= FAME_MIN_FOLLOWERS;
}

/** Campaña de un streamer (o null si no tiene). Nombre normalizado. */
export function campaignForStreamer(handle: string | null | undefined): CampaignStreamer | null {
  if (!handle) return null;
  return CAMPAIGN_STREAMERS[normalizeHandle(handle)] ?? null;
}

/** ¿El streamer tiene campaña activa con tarifa NETA >= $1/1k Y es
 *  famoso (regla 18:50)? La prioridad de cola exige las dos cosas:
 *  dinero Y fama. Bobby Van Riper paga $1,35 neto pero tiene 734
 *  seguidores: NO es campaign streamer. BenDaDonnn es famoso pero
 *  su neta es $0,90: tampoco. */
export function isCampaignStreamer(handle: string | null | undefined): boolean {
  const c = campaignForStreamer(handle);
  return c !== null && c.netRatePer1k >= NET_FLOOR_PER_1K_USD && c.followers >= FAME_MIN_FOLLOWERS;
}

/** Puerta de activación ampliada (2026-11-23): los 10 del lanzamiento
 *  + los 4 virales de campaña FAMOSOS (regla 18:50: Bobby Van Riper
 *  queda fuera por fama). Cualquier otro handle se puede crear
 *  desactivado, pero activarlo sigue siendo 400. */
export function isActivatableInfluencer(handle: string): boolean {
  return isLaunchInfluencer(handle) || VIRAL_CAMPAIGN_HANDLES.includes(normalizeHandle(handle));
}

export async function listInfluencers(db: D1Database, tenant: Tenant): Promise<InfluencerRow[]> {
  const res = await db
    .prepare("SELECT * FROM influencers WHERE tenant_id = ? ORDER BY id ASC")
    .bind(tenant)
    .all<InfluencerRow>();
  return res.results;
}

export async function getInfluencer(db: D1Database, id: number, tenant: Tenant): Promise<InfluencerRow | null> {
  return db.prepare("SELECT * FROM influencers WHERE id = ? AND tenant_id = ?").bind(id, tenant).first<InfluencerRow>();
}

export interface NewInfluencer {
  name: string;
  platform: string;
  profile_url: string;
  language: string;
  permission: number;
  active: number;
  notes: string;
  // 0018: categoría (siempre "GTA VI"), fecha del permiso y
  // plataformas de publicación (JSON).
  game: string;
  permission_date: string;
  publish_platforms: string;
}

export async function createInfluencer(db: D1Database, tenant: Tenant, data: NewInfluencer): Promise<InfluencerRow> {
  const res = await db
    .prepare(
      "INSERT INTO influencers (tenant_id, name, platform, profile_url, language, permission, active, notes, created_at, game, permission_date, publish_platforms) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )
    .bind(tenant, data.name, data.platform, data.profile_url, data.language, data.permission, data.active, data.notes, nowMs(), data.game, data.permission_date, data.publish_platforms)
    .run();
  const id = Number(res.meta.last_row_id);
  const row = await getInfluencer(db, id, tenant);
  if (!row) throw new Error("No se pudo leer el influencer creado.");
  return row;
}

export async function updateInfluencer(
  db: D1Database,
  id: number,
  tenant: Tenant,
  patch: Partial<NewInfluencer>,
): Promise<InfluencerRow | null> {
  const sets: string[] = [];
  const binds: unknown[] = [];
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) continue;
    sets.push(`${key} = ?`);
    binds.push(value);
  }
  if (sets.length === 0) return getInfluencer(db, id, tenant);
  // A2: sin cambio no hay UPDATE. Se lee la fila actual (una
  // lectura, gratis en cuota) y se comparan solo las columnas del
  // patch: un PATCH con los mismos valores ya no escribe.
  const current = await getInfluencer(db, id, tenant);
  if (current) {
    const next: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(patch)) {
      if (value !== undefined) next[key] = value;
    }
    if (isUnchanged(current, next)) return current;
  }
  binds.push(id, tenant);
  await db.prepare(`UPDATE influencers SET ${sets.join(", ")} WHERE id = ? AND tenant_id = ?`).bind(...binds).run();
  return getInfluencer(db, id, tenant);
}

export async function deleteInfluencer(db: D1Database, id: number, tenant: Tenant): Promise<boolean> {
  const res = await db.prepare("DELETE FROM influencers WHERE id = ? AND tenant_id = ?").bind(id, tenant).run();
  return (res.meta.changes ?? 0) > 0;
}

// --- Envíos a marketplaces de clipping (tabla campaign_submissions, 0041) ---
// Registro + cola en nube de los envíos a Content Rewards. Un post =
// UNA campaña, una sola vez: lo garantiza el índice UNIQUE
// (tenant_id, clip_id, platform, campaign_id). Estados: pending
// (asignado o intento bloqueado, esperando envío), submitted
// (enviado, con submission_id), failed, expired (pasaron 30 min
// desde published_at_ms sin enviarse: nunca se envía tarde).
// `published_at_ms` es el instante REAL de publicación (lo fija
// markPosted); NULL = aún no publicado = no se puede enviar.
// Todas las funciones degradan a vacío/0 si la tabla no existe
// (bases sin la 0041): nunca tumban el ciclo ni la lectura.
export const CAMPAIGN_SUBMISSION_STATUSES = ["pending", "submitted", "failed", "expired"] as const;
export type CampaignSubmissionStatus = (typeof CAMPAIGN_SUBMISSION_STATUSES)[number];
/** Ventana de envío: 30 min desde la publicación real. */
export const CAMPAIGN_SUBMIT_WINDOW_MS = 30 * 60 * 1000;

export interface CampaignSubmissionRow {
  id: number;
  tenant_id: Tenant;
  clip_id: number;
  platform: string;
  marketplace: string;
  campaign_id: string;
  campaign_name: string | null;
  streamer: string | null;
  gross_rate_per_1k: number | null;
  net_rate_per_1k: number | null;
  min_views: number | null;
  cap_per_clip: number | null;
  post_url: string | null;
  published_at_ms: number | null;
  status: string;
  submission_id: string | null;
  views: number | null;
  amount_usd: number | null;
  error: string | null;
  created_at: number;
  updated_at: number;
}

export interface NewCampaignSubmission {
  clip_id: number;
  platform: string;
  marketplace?: string;
  campaign_id: string;
  campaign_name?: string | null;
  streamer?: string | null;
  gross_rate_per_1k?: number | null;
  net_rate_per_1k?: number | null;
  min_views?: number | null;
  cap_per_clip?: number | null;
  post_url?: string | null;
  published_at_ms?: number | null;
}

export function campaignSubmissionOut(row: CampaignSubmissionRow): Record<string, unknown> {
  return { ...row };
}

export async function listCampaignSubmissions(
  db: D1Database,
  tenant: Tenant,
  filter: { clipId?: number; platform?: string; campaignId?: string; status?: string } = {},
): Promise<CampaignSubmissionRow[]> {
  try {
    const conds = ["tenant_id = ?"];
    const params: unknown[] = [tenant];
    if (filter.clipId !== undefined) { conds.push("clip_id = ?"); params.push(filter.clipId); }
    if (filter.platform !== undefined) { conds.push("platform = ?"); params.push(filter.platform); }
    if (filter.campaignId !== undefined) { conds.push("campaign_id = ?"); params.push(filter.campaignId); }
    if (filter.status !== undefined) { conds.push("status = ?"); params.push(filter.status); }
    const res = await db
      .prepare(`SELECT * FROM campaign_submissions WHERE ${conds.join(" AND ")} ORDER BY created_at ASC`)
      .bind(...params)
      .all<CampaignSubmissionRow>();
    return res.results ?? [];
  } catch {
    return [];
  }
}

export async function findCampaignSubmission(
  db: D1Database,
  tenant: Tenant,
  clipId: number,
  platform: string,
  campaignId: string,
): Promise<CampaignSubmissionRow | null> {
  const rows = await listCampaignSubmissions(db, tenant, { clipId, platform, campaignId });
  return rows[0] ?? null;
}

export async function getCampaignSubmission(db: D1Database, id: number, tenant: Tenant): Promise<CampaignSubmissionRow | null> {
  try {
    return await db
      .prepare("SELECT * FROM campaign_submissions WHERE id = ? AND tenant_id = ?")
      .bind(id, tenant)
      .first<CampaignSubmissionRow>();
  } catch {
    return null;
  }
}

/** Crea la fila en `pending`. Si ya existe (UNIQUE), devuelve la
 *  existente con `duplicate: true` en vez de fallar: el segundo
 *  intento de registrar el mismo envío no es un error, es el
 *  duplicado que hay que bloquear. */
export async function createCampaignSubmission(
  db: D1Database,
  tenant: Tenant,
  data: NewCampaignSubmission,
): Promise<{ row: CampaignSubmissionRow; duplicate: boolean }> {
  const existing = await findCampaignSubmission(db, tenant, data.clip_id, data.platform, data.campaign_id);
  if (existing) return { row: existing, duplicate: true };
  const now = nowMs();
  const res = await db
    .prepare(
      "INSERT INTO campaign_submissions (tenant_id, clip_id, platform, marketplace, campaign_id, campaign_name, streamer, " +
        "gross_rate_per_1k, net_rate_per_1k, min_views, cap_per_clip, post_url, published_at_ms, status, submission_id, views, amount_usd, error, created_at, updated_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL, NULL, ?, ?)",
    )
    .bind(
      tenant, data.clip_id, data.platform, data.marketplace ?? "content_rewards", data.campaign_id,
      data.campaign_name ?? null, data.streamer ?? null,
      data.gross_rate_per_1k ?? null, data.net_rate_per_1k ?? null,
      data.min_views ?? null, data.cap_per_clip ?? null,
      data.post_url ?? null, data.published_at_ms ?? null, now, now,
    )
    .run();
  const row = await getCampaignSubmission(db, Number(res.meta.last_row_id), tenant);
  if (!row) throw new Error("No se pudo leer el envío creado.");
  return { row, duplicate: false };
}

/** Actualiza una fila. `submitted` es terminal: no se puede cambiar
 *  un envío ya registrado como enviado (evita reescribir el
 *  submission_id). Devuelve null si no existe o si es terminal. */
export async function updateCampaignSubmission(
  db: D1Database,
  tenant: Tenant,
  id: number,
  patch: { status?: string; submission_id?: string | null; views?: number | null; amount_usd?: number | null; error?: string | null; post_url?: string | null; published_at_ms?: number | null },
): Promise<CampaignSubmissionRow | null> {
  const row = await getCampaignSubmission(db, id, tenant);
  if (!row) return null;
  if (row.status === "submitted") return null;
  if (patch.status !== undefined && !(CAMPAIGN_SUBMISSION_STATUSES as readonly string[]).includes(patch.status)) {
    throw new Error(`status inválido: usa ${CAMPAIGN_SUBMISSION_STATUSES.join(", ")}.`);
  }
  const sets: string[] = ["updated_at = ?"];
  const params: unknown[] = [nowMs()];
  const push = (col: string, v: unknown) => { sets.push(`${col} = ?`); params.push(v); };
  if (patch.status !== undefined) push("status", patch.status);
  if (patch.submission_id !== undefined) push("submission_id", patch.submission_id);
  if (patch.views !== undefined) push("views", patch.views);
  if (patch.amount_usd !== undefined) push("amount_usd", patch.amount_usd);
  if (patch.error !== undefined) push("error", patch.error);
  if (patch.post_url !== undefined) push("post_url", patch.post_url);
  if (patch.published_at_ms !== undefined) push("published_at_ms", patch.published_at_ms);
  await db
    .prepare(`UPDATE campaign_submissions SET ${sets.join(", ")} WHERE id = ? AND tenant_id = ?`)
    .bind(...params, id, tenant)
    .run();
  return getCampaignSubmission(db, id, tenant);
}

/** DISPARADOR — lo llama markPosted (jobs.ts) con el instante REAL
 *  en que el job pasó a posted con su evidence_url. Rellena
 *  post_url + published_at_ms en las filas `pending` de ese
 *  clip+plataforma que aún no los tienen (asignación registrada
 *  antes de publicar): a partir de ahí la fila es enviable dentro
 *  de 30 min y la cola en nube la puede retomar. Nunca crea filas
 *  ni toca las ya enviadas. Devuelve cuántas rellenó. */
export async function stampCampaignSubmissionPosted(
  db: D1Database,
  tenant: Tenant,
  clipId: number,
  platform: string,
  evidenceUrl: string,
  postedAtMs: number,
): Promise<number> {
  try {
    const res = await db
      .prepare(
        "UPDATE campaign_submissions SET post_url = ?, published_at_ms = ?, updated_at = ? " +
          "WHERE tenant_id = ? AND clip_id = ? AND platform = ? AND status = 'pending' AND published_at_ms IS NULL",
      )
      .bind(evidenceUrl, postedAtMs, postedAtMs, tenant, clipId, platform)
      .run();
    return res.meta.changes ?? 0;
  } catch {
    return 0;
  }
}

/** Relleno para publicados que NO pasaron por markPosted (volcado
 *  del diario WAL con UPDATE directo, reconciliaciones manuales):
 *  igual que backfillRetentionDeadlines (0039), cruza jobs posted
 *  con filas pending sin published_at_ms. Se llama en la lectura
 *  de pendientes (best-effort), no en el ciclo. */
export async function backfillCampaignSubmissionsPosted(db: D1Database, tenant: Tenant): Promise<number> {
  try {
    const res = await db
      .prepare(
        "UPDATE campaign_submissions SET post_url = " +
          "(SELECT evidence_url FROM jobs WHERE jobs.tenant_id = campaign_submissions.tenant_id AND jobs.clip_id = campaign_submissions.clip_id AND jobs.platform = campaign_submissions.platform AND jobs.kind = 'publish' AND jobs.state = 'posted' ORDER BY jobs.updated_at DESC LIMIT 1), " +
          "published_at_ms = " +
          "(SELECT updated_at FROM jobs WHERE jobs.tenant_id = campaign_submissions.tenant_id AND jobs.clip_id = campaign_submissions.clip_id AND jobs.platform = campaign_submissions.platform AND jobs.kind = 'publish' AND jobs.state = 'posted' ORDER BY jobs.updated_at DESC LIMIT 1), " +
          "updated_at = ? " +
          "WHERE tenant_id = ? AND status = 'pending' AND published_at_ms IS NULL AND EXISTS " +
          "(SELECT 1 FROM jobs WHERE jobs.tenant_id = campaign_submissions.tenant_id AND jobs.clip_id = campaign_submissions.clip_id AND jobs.platform = campaign_submissions.platform AND jobs.kind = 'publish' AND jobs.state = 'posted')",
      )
      .bind(nowMs(), tenant)
      .run();
    return res.meta.changes ?? 0;
  } catch {
    return 0;
  }
}

/** Marca `expired` las pendientes cuya ventana de 30 min ya pasó
 *  (published_at_ms + 30 min < ahora). Nunca se envía tarde. */
export async function expireStaleSubmissions(db: D1Database, tenant: Tenant, now = Date.now()): Promise<number> {
  try {
    const res = await db
      .prepare(
        "UPDATE campaign_submissions SET status = 'expired', updated_at = ? " +
          "WHERE tenant_id = ? AND status = 'pending' AND published_at_ms IS NOT NULL AND published_at_ms + ? < ?",
      )
      .bind(now, tenant, CAMPAIGN_SUBMIT_WINDOW_MS, now)
      .run();
    return res.meta.changes ?? 0;
  } catch {
    return 0;
  }
}

/** Cola en nube: pendientes ya publicadas (con published_at_ms),
 *  las más urgentes primero (las que antes vencen la ventana).
 *  Antes de listar, rellena publicados por otras vías y caduca lo
 *  vencido: la lectura nunca devuelve un envío que ya no se puede
 *  enviar. Sin tabla, lista vacía. */
export async function listPendingCampaignSubmissions(db: D1Database, tenant: Tenant, now = Date.now()): Promise<CampaignSubmissionRow[]> {
  try {
    await backfillCampaignSubmissionsPosted(db, tenant);
    await expireStaleSubmissions(db, tenant, now);
    const res = await db
      .prepare(
        "SELECT * FROM campaign_submissions WHERE tenant_id = ? AND status = 'pending' AND published_at_ms IS NOT NULL ORDER BY published_at_ms ASC",
      )
      .bind(tenant)
      .all<CampaignSubmissionRow>();
    return res.results ?? [];
  } catch {
    return [];
  }
}

// --- Evolución neuroevolutiva (tablas evo_*, migración 0042) -----
// Población OFFLINE: las variantes se generan en el cerebro y NO se
// publican; solo 1 ganadora por momento/plataforma sale por la vía
// de autopublicación ya existente. Ver evolution.ts (doctrina) y
// panel-backend/EVOLUTION-0042.md. Todas las funciones degradan a
// vacío/null/0 si las tablas aún no existen (bases sin la 0042):
// nunca tumban el ciclo ni la lectura.
export const EVO_GENERATION_STATUSES = ["draft", "winner_ready", "done"] as const;
export const EVO_VARIANT_STATUSES_DB = ["candidate", "winner", "loser", "published", "blocked"] as const;

export interface EvoGenerationRow {
  id: number;
  tenant_id: Tenant;
  streamer: string;
  campaign_id: string;
  base_clip_id: number;
  generation: number;
  parent_generation_id: number | null;
  platform: string;
  status: string;
  blocked_reason: string | null;
  created_at: number;
  updated_at: number;
}

export interface EvoVariantRow {
  id: number;
  tenant_id: Tenant;
  generation_id: number;
  variant_index: number;
  parent_variant_id: number | null;
  clip_id: number | null;
  params_json: string;
  mutations_json: string;
  is_control: number;
  brain_virality: number | null;
  brain_seo: number | null;
  brain_fitness: number | null;
  real_approved_views: number | null;
  real_views: number | null;
  real_retention: number | null;
  real_fitness: number | null;
  has_real_data: number;
  status: string;
  blocked_reason: string | null;
  created_at: number;
  updated_at: number;
}

export interface EvoFitnessRow {
  id: number;
  tenant_id: Tenant;
  variant_id: number;
  source: string;
  virality: number | null;
  seo: number | null;
  approved_views: number | null;
  views: number | null;
  retention: number | null;
  fitness: number | null;
  measured_at: number;
}

export function evoGenerationOut(row: EvoGenerationRow): Record<string, unknown> {
  return { ...row };
}

export function evoVariantOut(row: EvoVariantRow): Record<string, unknown> {
  return {
    ...row,
    params: safeJsonParse(row.params_json),
    mutations: safeJsonParse(row.mutations_json),
  };
}

function safeJsonParse(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/** Crea la generación. Si ya existe (UNIQUE tenant+clip+
 *  generación), devuelve la existente con `duplicate: true`. */
export async function createEvoGeneration(
  db: D1Database,
  tenant: Tenant,
  data: { streamer: string; campaign_id: string; base_clip_id: number; generation: number; parent_generation_id?: number | null; platform: string; blocked_reason?: string | null },
): Promise<{ row: EvoGenerationRow; duplicate: boolean }> {
  const existing = await getEvoGeneration(db, tenant, data.base_clip_id, data.generation);
  if (existing) return { row: existing, duplicate: true };
  const now = nowMs();
  const res = await db
    .prepare(
      "INSERT INTO evo_generations (tenant_id, streamer, campaign_id, base_clip_id, generation, parent_generation_id, platform, status, blocked_reason, created_at, updated_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)",
    )
    .bind(tenant, data.streamer, data.campaign_id, data.base_clip_id, data.generation, data.parent_generation_id ?? null, data.platform, data.blocked_reason ?? null, now, now)
    .run();
  const row = await getEvoGenerationById(db, Number(res.meta.last_row_id), tenant);
  if (!row) throw new Error("No se pudo leer la generación creada.");
  return { row, duplicate: false };
}

export async function getEvoGenerationById(db: D1Database, id: number, tenant: Tenant): Promise<EvoGenerationRow | null> {
  try {
    return await db.prepare("SELECT * FROM evo_generations WHERE id = ? AND tenant_id = ?").bind(id, tenant).first<EvoGenerationRow>();
  } catch {
    return null;
  }
}

export async function getEvoGeneration(db: D1Database, tenant: Tenant, baseClipId: number, generation: number): Promise<EvoGenerationRow | null> {
  try {
    return await db
      .prepare("SELECT * FROM evo_generations WHERE tenant_id = ? AND base_clip_id = ? AND generation = ?")
      .bind(tenant, baseClipId, generation)
      .first<EvoGenerationRow>();
  } catch {
    return null;
  }
}

export async function listEvoGenerations(db: D1Database, tenant: Tenant, limit = 10): Promise<EvoGenerationRow[]> {
  try {
    const res = await db
      .prepare("SELECT * FROM evo_generations WHERE tenant_id = ? ORDER BY updated_at DESC, id DESC LIMIT ?")
      .bind(tenant, limit)
      .all<EvoGenerationRow>();
    return res.results ?? [];
  } catch {
    return [];
  }
}

export async function updateEvoGeneration(db: D1Database, tenant: Tenant, id: number, patch: { status?: string; blocked_reason?: string | null }): Promise<EvoGenerationRow | null> {
  const row = await getEvoGenerationById(db, id, tenant);
  if (!row) return null;
  const sets: string[] = ["updated_at = ?"];
  const params: unknown[] = [nowMs()];
  if (patch.status !== undefined) {
    if (!(EVO_GENERATION_STATUSES as readonly string[]).includes(patch.status)) throw new Error(`status inválido: usa ${EVO_GENERATION_STATUSES.join(", ")}.`);
    sets.push("status = ?");
    params.push(patch.status);
  }
  if (patch.blocked_reason !== undefined) {
    sets.push("blocked_reason = ?");
    params.push(patch.blocked_reason);
  }
  await db.prepare(`UPDATE evo_generations SET ${sets.join(", ")} WHERE id = ? AND tenant_id = ?`).bind(...params, id, tenant).run();
  return getEvoGenerationById(db, id, tenant);
}

export async function createEvoVariant(
  db: D1Database,
  tenant: Tenant,
  data: { generation_id: number; variant_index: number; parent_variant_id?: number | null; params: Record<string, unknown>; mutations: unknown[]; is_control: boolean; brain_virality?: number | null; brain_seo?: number | null; brain_fitness?: number | null },
): Promise<EvoVariantRow> {
  const now = nowMs();
  const res = await db
    .prepare(
      "INSERT INTO evo_variants (tenant_id, generation_id, variant_index, parent_variant_id, clip_id, params_json, mutations_json, is_control, brain_virality, brain_seo, brain_fitness, real_approved_views, real_views, real_retention, real_fitness, has_real_data, status, blocked_reason, created_at, updated_at) " +
        "VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 0, 'candidate', NULL, ?, ?)",
    )
    .bind(
      tenant, data.generation_id, data.variant_index, data.parent_variant_id ?? null,
      JSON.stringify(data.params), JSON.stringify(data.mutations), data.is_control ? 1 : 0,
      data.brain_virality ?? null, data.brain_seo ?? null, data.brain_fitness ?? null, now, now,
    )
    .run();
  const row = await getEvoVariant(db, Number(res.meta.last_row_id), tenant);
  if (!row) throw new Error("No se pudo leer la variante creada.");
  return row;
}

export async function getEvoVariant(db: D1Database, id: number, tenant: Tenant): Promise<EvoVariantRow | null> {
  try {
    return await db.prepare("SELECT * FROM evo_variants WHERE id = ? AND tenant_id = ?").bind(id, tenant).first<EvoVariantRow>();
  } catch {
    return null;
  }
}

export async function listEvoVariants(db: D1Database, tenant: Tenant, generationId: number): Promise<EvoVariantRow[]> {
  try {
    const res = await db
      .prepare("SELECT * FROM evo_variants WHERE tenant_id = ? AND generation_id = ? ORDER BY variant_index ASC")
      .bind(tenant, generationId)
      .all<EvoVariantRow>();
    return res.results ?? [];
  } catch {
    return [];
  }
}

export async function updateEvoVariant(
  db: D1Database,
  tenant: Tenant,
  id: number,
  patch: { status?: string; blocked_reason?: string | null; brain_virality?: number | null; brain_seo?: number | null; brain_fitness?: number | null; real_approved_views?: number | null; real_views?: number | null; real_retention?: number | null; real_fitness?: number | null; has_real_data?: number },
): Promise<EvoVariantRow | null> {
  const row = await getEvoVariant(db, id, tenant);
  if (!row) return null;
  if (patch.status !== undefined && !(EVO_VARIANT_STATUSES_DB as readonly string[]).includes(patch.status)) {
    throw new Error(`status inválido: usa ${EVO_VARIANT_STATUSES_DB.join(", ")}.`);
  }
  const sets: string[] = ["updated_at = ?"];
  const params: unknown[] = [nowMs()];
  const push = (col: string, v: unknown) => { sets.push(`${col} = ?`); params.push(v); };
  if (patch.status !== undefined) push("status", patch.status);
  if (patch.blocked_reason !== undefined) push("blocked_reason", patch.blocked_reason);
  if (patch.brain_virality !== undefined) push("brain_virality", patch.brain_virality);
  if (patch.brain_seo !== undefined) push("brain_seo", patch.brain_seo);
  if (patch.brain_fitness !== undefined) push("brain_fitness", patch.brain_fitness);
  if (patch.real_approved_views !== undefined) push("real_approved_views", patch.real_approved_views);
  if (patch.real_views !== undefined) push("real_views", patch.real_views);
  if (patch.real_retention !== undefined) push("real_retention", patch.real_retention);
  if (patch.real_fitness !== undefined) push("real_fitness", patch.real_fitness);
  if (patch.has_real_data !== undefined) push("has_real_data", patch.has_real_data);
  await db.prepare(`UPDATE evo_variants SET ${sets.join(", ")} WHERE id = ? AND tenant_id = ?`).bind(...params, id, tenant).run();
  return getEvoVariant(db, id, tenant);
}

/** Registra una medición de fitness (cerebro o real) y actualiza
 *  la variante. Sin datos no se escribe nada: el llamador valida
 *  que al menos un valor venga informado. */
export async function recordEvoFitness(
  db: D1Database,
  tenant: Tenant,
  variantId: number,
  data: { source: "brain" | "real"; virality?: number | null; seo?: number | null; approved_views?: number | null; views?: number | null; retention?: number | null; fitness?: number | null; measured_at?: number },
): Promise<EvoFitnessRow | null> {
  const variant = await getEvoVariant(db, variantId, tenant);
  if (!variant) return null;
  const now = data.measured_at ?? nowMs();
  const res = await db
    .prepare(
      "INSERT INTO evo_fitness (tenant_id, variant_id, source, virality, seo, approved_views, views, retention, fitness, measured_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )
    .bind(
      tenant, variantId, data.source,
      data.virality ?? null, data.seo ?? null,
      data.approved_views ?? null, data.views ?? null, data.retention ?? null,
      data.fitness ?? null, now,
    )
    .run();
  if (data.source === "real") {
    await updateEvoVariant(db, tenant, variantId, {
      real_approved_views: data.approved_views ?? variant.real_approved_views,
      real_views: data.views ?? variant.real_views,
      real_retention: data.retention ?? variant.real_retention,
      real_fitness: data.fitness ?? variant.real_fitness,
      has_real_data: 1,
    });
  } else {
    await updateEvoVariant(db, tenant, variantId, {
      brain_virality: data.virality ?? variant.brain_virality,
      brain_seo: data.seo ?? variant.brain_seo,
      brain_fitness: data.fitness ?? variant.brain_fitness,
    });
  }
  try {
    return await db.prepare("SELECT * FROM evo_fitness WHERE id = ? AND tenant_id = ?").bind(Number(res.meta.last_row_id), tenant).first<EvoFitnessRow>();
  } catch {
    return null;
  }
}

/** Resumen para el panel (solo lectura): últimas generaciones con
 *  su ganadora, fitness real vs cerebro y motivo de bloqueo. */
export async function evoStatusSummary(db: D1Database, tenant: Tenant): Promise<Array<Record<string, unknown>>> {
  const gens = await listEvoGenerations(db, tenant, 10);
  const out: Array<Record<string, unknown>> = [];
  for (const g of gens) {
    const variants = await listEvoVariants(db, tenant, g.id);
    const winner = variants.find((v) => v.status === "winner" || v.status === "published") ?? null;
    out.push({
      generation_id: g.id,
      streamer: g.streamer,
      campaign_id: g.campaign_id,
      base_clip_id: g.base_clip_id,
      generation: g.generation,
      platform: g.platform,
      status: g.status,
      blocked_reason: g.blocked_reason,
      variants_total: variants.length,
      winners: variants.filter((v) => v.status === "winner").length,
      losers: variants.filter((v) => v.status === "loser").length,
      untested: variants.filter((v) => v.status === "candidate").length,
      has_real_data: variants.some((v) => v.has_real_data === 1),
      winner: winner
        ? {
            variant_id: winner.id,
            variant_index: winner.variant_index,
            brain_virality: winner.brain_virality,
            brain_seo: winner.brain_seo,
            real_fitness: winner.real_fitness,
            real_approved_views: winner.real_approved_views,
            has_real_data: winner.has_real_data === 1,
            mutations: safeJsonParse(winner.mutations_json),
            params: safeJsonParse(winner.params_json),
          }
        : null,
      created_at: g.created_at,
      updated_at: g.updated_at,
    });
  }
  return out;
}

// --- Fuentes / VODs (tablas sources y vod_checks, migraciones 0002/0006 + tenant_id en 0012) ---
// Solo lectura: la detección de VODs nuevos la hace GitHub Actions
// (cerebro, cron 09:00), no el Worker. Estas funciones solo leen lo
// que el cerebro ya escribió en D1.
export interface SourceRow {
  id: number;
  tenant_id: Tenant;
  url: string;
  platform: string;
  title: string;
  streamer: string;
  status: string;
  progress: number;
  status_note: string;
  influencer_id: number | null;
  game: string;
  created_at: number;
  updated_at: number;
}

export async function listSources(db: D1Database, tenant: Tenant): Promise<SourceRow[]> {
  const res = await db
    .prepare("SELECT * FROM sources WHERE tenant_id = ? ORDER BY updated_at DESC, id DESC")
    .bind(tenant)
    .all<SourceRow>();
  return res.results;
}

export async function getSource(db: D1Database, id: number, tenant: Tenant): Promise<SourceRow | null> {
  return db.prepare("SELECT * FROM sources WHERE id = ? AND tenant_id = ?").bind(id, tenant).first<SourceRow>();
}

export interface VodCheckRow {
  id: number;
  tenant_id: Tenant;
  ran_at: number;
  summary: string;
  findings: number;
}

/** Última revisión de VODs del tenant (o null si el cerebro aún no
 *  escribió ninguna). Nunca se inventa: sin fila, null. */
export async function getLatestVodCheck(db: D1Database, tenant: Tenant): Promise<VodCheckRow | null> {
  return db
    .prepare("SELECT * FROM vod_checks WHERE tenant_id = ? ORDER BY ran_at DESC, id DESC LIMIT 1")
    .bind(tenant)
    .first<VodCheckRow>();
}

// --- Usuarios del panel (tabla panel_users, migraciones 0006 + 0012) ---
// Frente 2, M1: cada usuario ve SOLO su propio tenant. La lista ya
// no devuelve a seba y chacal juntos: filtra por tenant = el tenant
// de la sesión (la columna `tenant` de panel_users, no `tenant_id`).
// NUNCA se selecciona password_hash: la consulta solo pide las 4
// columnas públicas.
export interface PanelUserRow {
  username: string;
  tenant: Tenant;
  status: string;
  created_at: number;
}

export async function listPanelUsers(db: D1Database, tenant: Tenant): Promise<PanelUserRow[]> {
  const res = await db
    .prepare("SELECT username, tenant, status, created_at FROM panel_users WHERE username <> '' AND tenant = ? ORDER BY username ASC")
    .bind(tenant)
    .all<PanelUserRow>();
  return res.results;
}


// --- Editor: actualización parcial de un clip (fase 2, Dev 2) ---
// Solo las columnas que el PATCH permite; las claves las construye
// index.ts (nombres de columna internos, nunca del body). Siempre
// actualiza updated_at. Devuelve la fila actualizada o null.
export async function updateClip(
  db: D1Database,
  id: number,
  tenant: Tenant,
  patch: Record<string, string | number | null>,
): Promise<ClipRow | null> {
  const sets: string[] = [];
  const binds: unknown[] = [];
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) continue;
    sets.push(`${key} = ?`);
    binds.push(value);
  }
  // A2: sin cambio no hay UPDATE (mismo patrón que
  // updateInfluencer). updated_at solo se toca cuando algo cambia:
  // si el patch ya es el valor actual, la fila queda intacta.
  const current = await getClip(db, id, tenant);
  if (!current) return null;
  const next: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(patch)) {
    if (value !== undefined) next[key] = value;
  }
  if (isUnchanged(current, next)) return current;
  sets.push("updated_at = ?");
  binds.push(nowMs());
  binds.push(id, tenant);
  await db.prepare(`UPDATE clips SET ${sets.join(", ")} WHERE id = ? AND tenant_id = ?`).bind(...binds).run();
  return getClip(db, id, tenant);
}

// --- Analíticas (tabla clip_stats, migración 0006 + tenant_id en 0012) ---
// Solo mediciones reales (automáticas o manuales de respaldo):
// nunca se inventa ni se estima ningún valor. Un campo sin medir
// queda NULL (sin medir), nunca 0.
export interface ClipStatRow {
  id: number;
  tenant_id: Tenant;
  clip_id: number;
  platform: string; // clave API: youtube | tiktok | instagram | facebook
  views: number | null;
  retention_pct: number | null;
  shares: number | null;
  saves: number | null;
  comments: number | null;
  new_subs: number | null;
  window_label: string; // 24h | 48h | 7d
  recorded_at: number;
}

export interface NewClipStat {
  platform: string;
  views: number;
  retention_pct: number | null;
  shares: number | null;
  saves: number | null;
  comments: number | null;
  new_subs: number | null;
  window_label: string;
  recorded_at: number;
}

export async function listClipStats(db: D1Database, tenant: Tenant): Promise<ClipStatRow[]> {
  const res = await db
    .prepare("SELECT * FROM clip_stats WHERE tenant_id = ? ORDER BY recorded_at DESC, id DESC")
    .bind(tenant)
    .all<ClipStatRow>();
  return res.results;
}

export async function listClipStatsForClip(db: D1Database, clipId: number, tenant: Tenant): Promise<ClipStatRow[]> {
  const res = await db
    .prepare("SELECT * FROM clip_stats WHERE clip_id = ? AND tenant_id = ? ORDER BY recorded_at DESC, id DESC")
    .bind(clipId, tenant)
    .all<ClipStatRow>();
  return res.results;
}

export async function getClipStat(db: D1Database, id: number, tenant: Tenant): Promise<ClipStatRow | null> {
  return db.prepare("SELECT * FROM clip_stats WHERE id = ? AND tenant_id = ?").bind(id, tenant).first<ClipStatRow>();
}

export async function createClipStat(db: D1Database, tenant: Tenant, clipId: number, data: NewClipStat): Promise<ClipStatRow> {
  // A2: una medición idéntica a la última de la misma ventana no es
  // información nueva: se devuelve la fila existente sin INSERT.
  // Las mediciones son pocas al día (una por clip/plataforma/ventana
  // en la recogida automática), así que la lectura previa es barata.
  const latest = await db
    .prepare(
      "SELECT * FROM clip_stats WHERE tenant_id = ? AND clip_id = ? AND platform = ? AND window_label = ? ORDER BY recorded_at DESC, id DESC LIMIT 1",
    )
    .bind(tenant, clipId, data.platform, data.window_label)
    .first<ClipStatRow>()
    .catch(() => null);
  if (
    latest &&
    isUnchanged(latest, {
      views: data.views,
      retention_pct: data.retention_pct,
      shares: data.shares,
      saves: data.saves,
      comments: data.comments,
      new_subs: data.new_subs,
    })
  ) {
    return latest;
  }
  const res = await db
    .prepare(
      "INSERT INTO clip_stats (tenant_id, clip_id, platform, views, retention_pct, shares, saves, comments, new_subs, window_label, recorded_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )
    .bind(
      tenant,
      clipId,
      data.platform,
      data.views,
      data.retention_pct,
      data.shares,
      data.saves,
      data.comments,
      data.new_subs,
      data.window_label,
      data.recorded_at,
    )
    .run();
  const row = await getClipStat(db, Number(res.meta.last_row_id), tenant);
  if (!row) throw new Error("No se pudo leer la medición creada.");
  return row;
}

// --- Estado de la medición automática (tabla analytics_status,
// migración 0028) ------------------------------------------------------
// Último resultado de la recogida automática por plataforma. NO
// guarda cifras: solo si la plataforma mide o por qué no. GET
// /api/analytics lo expone como `auto_status` sin tocar los
// números manuales de clip_stats.
export type AnalyticsAutoStatusValue = "ok" | "sin_credencial" | "sin_permiso" | "error";

export interface AnalyticsAutoStatusRow {
  tenant_id: Tenant;
  platform: string; // clave API: youtube | tiktok | instagram | facebook
  status: AnalyticsAutoStatusValue;
  reason: string;
  last_measured_at: number | null;
  updated_at: number;
}

export async function listAnalyticsStatus(db: D1Database, tenant: Tenant): Promise<AnalyticsAutoStatusRow[]> {
  const res = await db
    .prepare("SELECT * FROM analytics_status WHERE tenant_id = ?")
    .bind(tenant)
    .all<AnalyticsAutoStatusRow>();
  return res.results;
}

export async function saveAnalyticsStatus(
  db: D1Database,
  tenant: Tenant,
  platform: string,
  status: AnalyticsAutoStatusValue,
  reason: string,
  lastMeasuredAt: number | null,
): Promise<void> {
  // UPDATE primero y si no hay fila, INSERT: se evita ON CONFLICT,
  // que los dobles de test no modelan (ver jobs.ts updateChanged).
  // A2: sin cambio no hay escritura: si el estado, el motivo y la
  // última medición ya son los guardados, ni el UPDATE ni el INSERT
  // se ejecutan (updated_at NO entra en la comparación).
  const current = await db
    .prepare("SELECT status, reason, last_measured_at FROM analytics_status WHERE tenant_id = ? AND platform = ?")
    .bind(tenant, platform)
    .first<{ status: string; reason: string; last_measured_at: number | null }>()
    .catch(() => null);
  if (current && isUnchanged(current, { status, reason, last_measured_at: lastMeasuredAt })) return;
  const res = await db
    .prepare(
      "UPDATE analytics_status SET status = ?, reason = ?, last_measured_at = ?, updated_at = ? WHERE tenant_id = ? AND platform = ?",
    )
    .bind(status, reason, lastMeasuredAt, nowMs(), tenant, platform)
    .run();
  if ((res.meta.changes ?? 0) > 0) return;
  await db
    .prepare(
      "INSERT INTO analytics_status (tenant_id, platform, status, reason, last_measured_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
    )
    .bind(tenant, platform, status, reason, lastMeasuredAt, nowMs())
    .run();
}

// --- Canales/Salud: estados de publicación de todos los clips ---
// Solo id, título, idioma y publish_states (JSON): es lo que
// necesitan GET /api/analytics y GET /api/channels para contar
// publicaciones reales por plataforma, sin leer el resto de la
// fila. El idioma (0019) es necesario para contar por canal
// (plataforma+idioma): un publicado en inglés no gasta la cuota
// del canal en español.
export interface ClipPublishStateRow {
  id: number;
  title: string;
  language: string;
  publish_states: string; // JSON {platform: PlatformPublishEntry}
}

export async function listClipPublishStates(db: D1Database, tenant: Tenant): Promise<ClipPublishStateRow[]> {
  const res = await db
    .prepare("SELECT id, title, language, publish_states FROM clips WHERE tenant_id = ? ORDER BY id ASC")
    .bind(tenant)
    .all<ClipPublishStateRow>();
  return res.results;
}

// --- Analíticas (0030): jobs publish posted = fuente de verdad de
// publicados. clips.publish_states es un espejo por clip SIN idioma
// por plataforma (un clip EN publicado contaba como ES) y no guarda
// platform_id ni evidence_url; los jobs sí, por (clip, plataforma,
// idioma) y por tenant. Contar desde jobs evita además contar dos
// veces un clip re-publicado: DISTINCT por clip en quien llama.
// (0031): un job con deleted_at es un vídeo que ya no existe en la
// plataforma (borrado o privado, probado por la medición); no se
// cuenta como publicado ni se vuelve a medir. El filtro es en JS y
// no en SQL a propósito: los dobles de D1 de los tests no modelan
// `IS NULL` (ver saveAnalyticsStatus y su comentario sobre
// ON CONFLICT); el comportamiento es el mismo.
export interface PostedJobRow {
  clip_id: number;
  platform: string; // panel: youtube_shorts | tiktok | instagram_reels | facebook_reels
  language: string;
  platform_id: string;
  evidence_url: string | null;
}

export async function listPostedJobs(db: D1Database, tenant: Tenant): Promise<PostedJobRow[]> {
  const res = await db
    .prepare(
      "SELECT clip_id, platform, language, platform_id, evidence_url, deleted_at FROM jobs " +
        "WHERE tenant_id = ? AND kind = 'publish' AND state = 'posted' AND platform_id IS NOT NULL ORDER BY updated_at DESC",
    )
    .bind(tenant)
    .all<PostedJobRow & { deleted_at?: number | null }>();
  return res.results.filter((j) => !j.deleted_at);
}

/** Marca un job posted como vídeo borrado en la plataforma
 *  (migración 0031). Idempotente: volver a marcarlo no cambia nada.
 *  Solo la medición lo llama, y solo cuando la API devuelve 200 y el
 *  id pedido no viene en la respuesta (YouTube/TikTok). Nunca por un
 *  400 ambiguo de Instagram/Facebook (ver stats.ts). */
export async function markJobDeleted(
  db: D1Database,
  tenant: Tenant,
  clipId: number,
  platform: string,
  nowMs: number,
): Promise<void> {
  // A2: un job ya marcado no se vuelve a marcar. Se lee deleted_at
  // primero (una lectura, gratis en cuota) y se salta el UPDATE si
  // ya está marcado. No se usa `AND deleted_at IS NULL` en el
  // UPDATE: los dobles de D1 de los tests no modelan IS NULL (ver
  // el comentario de listPostedJobs más arriba) y un UPDATE con
  // 0 filas tampoco se factura, pero la lectura previa es el patrón
  // que pide el encargo para todos los UPDATEs.
  const current = await db
    .prepare("SELECT deleted_at FROM jobs WHERE tenant_id = ? AND clip_id = ? AND platform = ? AND state = 'posted'")
    .bind(tenant, clipId, platform)
    .first<{ deleted_at: number | null }>()
    .catch(() => null);
  if (current && current.deleted_at !== null && current.deleted_at !== undefined) return;
  await db
    .prepare(
      "UPDATE jobs SET deleted_at = ? WHERE tenant_id = ? AND clip_id = ? AND platform = ? AND state = 'posted'",
    )
    .bind(nowMs, tenant, clipId, platform)
    .run();
}

/** Handle de TikTok desde la evidence_url de un job posted
 *  (tiktok.com/@handle/video/<id>). Fallback cuando la tabla
 *  accounts aún no tiene fila para ese canal: sale de D1, nunca
 *  de un literal. Sin @ en la URL, null. */
export function tiktokHandleFromEvidence(evidenceUrl: string | null | undefined): string | null {
  if (!evidenceUrl) return null;
  const m = /tiktok\.com\/@([^/]+)\/video\//.exec(evidenceUrl);
  return m ? `@${m[1] as string}` : null;
}

export async function logClipEvent(
  db: D1Database,
  clipId: number,
  step: string,
  actor: string,
  message: string,
  failed = false,
  tenant?: Tenant,
  opts?: { budget?: BudgetGuard; nowMsValue?: number },
): Promise<void> {
  // Guardián de presupuesto (A1, 2026-11-20): en "restricted" o
  // "essential-only" los eventos informativos no se escriben.
  // Sin guardián (o con guardián viejo), se escribe como siempre.
  const guard = opts?.budget ?? currentBudgetGuard();
  if (guard && !guard.shouldWrite("event")) return;
  // El tenant se conoce en casi todas las llamadas (viene del clip o
  // del job); si falta, se resuelve desde el clip para no escribir
  // un evento sin dueño. Sin clip ni tenant, no se registra nada.
  let t = tenant;
  if (!t) {
    const clip = await db.prepare("SELECT tenant_id FROM clips WHERE id = ?").bind(clipId).first<{ tenant_id: Tenant }>();
    t = clip?.tenant_id;
  }
  if (!t) return;
  // Dedupe (2026-11-20, cuota D1) + contador agregado (A2): el evento
  // suprimido no se pierde del todo: deja una muestra horaria en
  // event_counters con el rango de actividad (ver recordEventCounter).
  // La dedupe y el contador comparten UNA lectura (lastAt): un log
  // suprimido cuesta 1 lectura, no 2.
  const now = opts?.nowMsValue ?? nowMs();
  const lastAt = await lastIdenticalClipEventAt(db, clipId, t, step, message);
  if (lastAt !== null && now - lastAt < EVENT_DEDUPE_WINDOW_MS) {
    await recordEventCounter(db, "clip_events", t, step, message, lastAt, now);
    return;
  }
  await db
    .prepare(
      "INSERT INTO clip_events (clip_id, tenant_id, at, step, actor, message, failed) VALUES (?, ?, ?, ?, ?, ?, ?)",
    )
    .bind(clipId, t, now, step, actor, message, failed ? 1 : 0)
    .run();
}

export async function logWorkerEvent(db: D1Database, kind: string, message: string, tenant?: Tenant | null, opts?: { budget?: BudgetGuard; nowMsValue?: number }): Promise<void> {
  // Guardián de presupuesto (A1, 2026-11-20): en "restricted" o
  // "essential-only" los logs no se escriben. Sin guardián (o con
  // guardián viejo), se escribe como siempre. El guardián se pasa
  // explícito o se toma el vigente del ciclo (ver budget.ts:
  // setCurrentBudgetGuard): así el freno cubre todos los puntos
  // de log sin pasar el guardián por cada llamada.
  const guard = opts?.budget ?? currentBudgetGuard();
  if (guard && !guard.shouldWrite("log")) return;
  // Defensa: casi todas las llamadas pasan el tenant; un evento
  // global sin dueño se guarda como "global", NUNCA como
  // "sebastian" (2026-11-19: atribuirle eventos sin dueño
  // contaminaba su actividad). Ningún filtro por tenant casa
  // con "global": esos eventos solo se ven en el log global.
  const t = tenant ?? "global";
  // Dedupe (2026-11-20, cuota D1): el mismo evento repetido en la
  // ventana es el mismo estado, no información nueva. El ciclo
  // automático reevaluaba cada minuto los mismos clips frenados y
  // dejaba ~11.000 filas/día de auto_publish idénticas (medido:
  // 212 mensajes distintos de 10.975 en 24 h). Se guarda la primera
  // ocurrencia y un recordatorio cada ventana; un motivo DISTINTO
  // (otro gate, otro clip) se registra siempre. La lectura es
  // gratis en cuota (las lecturas no cuentan en el límite diario
  // de escrituras); si la lectura falla, se escribe igual (el log
  // nunca rompe el ciclo). A2: el evento suprimido deja una muestra
  // horaria en event_counters (recordEventCounter), con el rango de
  // actividad: el freno sigue siendo visible sin ahogar la base.
  const now = opts?.nowMsValue ?? nowMs();
  const lastAt = await lastIdenticalWorkerEventAt(db, t, kind, message);
  if (lastAt !== null && now - lastAt < EVENT_DEDUPE_WINDOW_MS) {
    await recordEventCounter(db, "worker_events", t, kind, message, lastAt, now);
    return;
  }
  await db.prepare("INSERT INTO worker_events (tenant_id, at, kind, message) VALUES (?, ?, ?, ?)").bind(t, now, kind, message).run();
}

/** Ventana de dedupe de eventos (2026-11-20, cuota D1): única
 *  fuente de este valor en el Worker (las dos funciones de log
 *  viven aquí). 6 h = primer aviso + 4 recordatorios/día por
 *  estado repetido, suficiente para ver que un freno sigue activo
 *  sin ahogar la base. */
export const EVENT_DEDUPE_WINDOW_MS = 6 * 3600 * 1000;

/** Ventana del contador agregado (A2, migración 0034): la misma que
 *  la dedupe, una sola fuente (EVENT_DEDUPE_WINDOW_MS). Una fila de
 *  event_counters cubre exactamente una ventana de dedupe. */
export const EVENT_AGGREGATE_WINDOW_MS = EVENT_DEDUPE_WINDOW_MS;

/** Muestreo del contador (A2): como mucho 1 escritura por hora y
 *  mensaje repetido. Contar cada ocurrencia costaría 1 escritura por
 *  ocurrencia (lo mismo que antes de la dedupe); el muestreo es el
 *  compromiso, documentado en la migración 0034. */
export const EVENT_COUNTER_SAMPLE_MS = 3600 * 1000;

/** Caducidad de eventos (A2): los eventos de más de 14 días se
 *  borran en el proceso nocturno (pruneOldEvents). */
export const EVENT_RETENTION_DAYS = 14;

/** Tamaño de lote de la caducidad (A2): cada DELETE borra como mucho
 *  500 filas. Los DELETE también cuentan en la cuota. */
export const EVENT_PRUNE_BATCH_SIZE = 500;

/** Tope por noche de la caducidad (A2): como mucho 5.000 filas
 *  borradas por tenant y noche (10 lotes). */
export const EVENT_PRUNE_MAX_ROWS_PER_NIGHT = 5000;

/** Hora fija de la caducidad (A2): 03:00 America/Santiago, de noche
 *  y ~6 h después del reset de cuota (21:00 -03). */
export const EVENT_PRUNE_HOUR_SANTIAGO = 3;

/** Límite diario de escrituras D1 POR CUENTA de Cloudflare (A2):
 *  ALIAS de D1_DAILY_ACCOUNT_LIMIT (budget.ts, A1): la fuente única
 *  del valor es budget.ts; aquí solo se re-exporta para que el
 *  código de caducidad lo lea sin importar otro módulo. */
export const D1_DAILY_WRITE_LIMIT = D1_DAILY_ACCOUNT_LIMIT;

/** Hito de la caducidad (A2): el proceso nocturno solo corre si la
 *  estimación de escrituras del día está por debajo del 70% del
 *  límite (70.000). Por encima, se salta ese día: borrar también
 *  cuesta escrituras y un día caliente no se enfría borrando. Se
 *  deriva del límite, nunca se escribe a mano. */
export const EVENT_PRUNE_WRITE_GATE = Math.floor(D1_DAILY_ACCOUNT_LIMIT * 0.7);

/** Inicio (ms epoch) de la ventana de 6 h que contiene `nowMsValue`. */
export function eventWindowStart(nowMsValue: number): number {
  return Math.floor(nowMsValue / EVENT_AGGREGATE_WINDOW_MS) * EVENT_AGGREGATE_WINDOW_MS;
}

export interface EventCounterRow {
  tenant_id: string;
  source: string;
  kind: string;
  message: string;
  window_start: number;
  count: number; // nº de muestras horarias con actividad (ver migración 0034)
  first_at: number;
  last_at: number;
}

/** Contador agregado de un evento repetido (A2, migración 0034).
 *  `firstAt` es la hora del evento original que la dedupe encontró
 *  (el que se guardó); `nowMsValue`, la de esta repetición. La
 *  primera repetición de la ventana crea la fila (count=1) y cada
 *  hora con actividad suma 1: como mucho 6 escrituras por ventana
 *  y mensaje, frente a ~360 ocurrencias (1/min). Nunca lanza: el
 *  contador es informativo y jamás rompe el ciclo. */
export async function recordEventCounter(
  db: D1Database,
  source: "worker_events" | "clip_events" | "notifications",
  tenant: string,
  kind: string,
  message: string,
  firstAt: number,
  nowMsValue: number,
): Promise<void> {
  try {
    const windowStart = eventWindowStart(nowMsValue);
    const row = await db
      .prepare(
        "SELECT count, last_at FROM event_counters WHERE tenant_id = ? AND source = ? AND kind = ? AND message = ? AND window_start = ?",
      )
      .bind(tenant, source, kind, message, windowStart)
      .first<{ count: number; last_at: number }>();
    if (!row) {
      await db
        .prepare(
          "INSERT INTO event_counters (tenant_id, source, kind, message, window_start, count, first_at, last_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
        )
        .bind(tenant, source, kind, message, windowStart, firstAt, nowMsValue)
        .run();
      return;
    }
    if (nowMsValue - row.last_at < EVENT_COUNTER_SAMPLE_MS) return;
    await db
      .prepare(
        "UPDATE event_counters SET count = count + 1, last_at = ? WHERE tenant_id = ? AND source = ? AND kind = ? AND message = ? AND window_start = ?",
      )
      .bind(nowMsValue, tenant, source, kind, message, windowStart)
      .run();
  } catch {
    // El contador nunca rompe el ciclo.
  }
}

/** Resumen legible de un contador para el panel: el mensaje, el
 *  rango horario REAL de actividad (America/Santiago) y el nº de
 *  muestras horarias. Dice "muestras", no "veces": el contador es
 *  muestreado (ver migración 0034). */
export function formatEventCounterSummary(row: EventCounterRow): string {
  const hhmm = (ms: number) => {
    const m = santiagoMinutesOfDay(ms);
    return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  };
  return `${row.message}: activo entre ${hhmm(row.first_at)} y ${hhmm(row.last_at)} (${row.count} ${row.count === 1 ? "muestra" : "muestras"}/hora)`;
}

/** Contadores agregados del tenant desde `sinceMs` (ms epoch), los
 *  más recientes primero. Solo lectura, para el panel. */
export async function listEventCounters(db: D1Database, tenant: Tenant, sinceMs: number): Promise<EventCounterRow[]> {
  const res = await db
    .prepare(
      "SELECT tenant_id, source, kind, message, window_start, count, first_at, last_at FROM event_counters WHERE tenant_id = ? AND window_start >= ? ORDER BY window_start DESC, last_at DESC",
    )
    .bind(tenant, sinceMs)
    .all<EventCounterRow>();
  return res.results ?? [];
}

/** ¿Hay un evento idéntico reciente (mismo tenant + kind +
 *  mensaje)? Falla cerrado a false (se registra igual). */
export async function hasRecentIdenticalWorkerEvent(
  db: D1Database,
  tenant: string,
  kind: string,
  message: string,
  nowMsValue: number,
): Promise<boolean> {
  const at = await lastIdenticalWorkerEventAt(db, tenant, kind, message);
  return at !== null && nowMsValue - at < EVENT_DEDUPE_WINDOW_MS;
}

/** Hora del último evento idéntico (mismo tenant + kind +
 *  mensaje), o null si no hay ninguno. Falla cerrado a null (el
 *  llamador registra igual). La dedupe y el contador agregado
 *  comparten esta única lectura: un log suprimido cuesta 1 lectura,
 *  no 2. */
export async function lastIdenticalWorkerEventAt(
  db: D1Database,
  tenant: string,
  kind: string,
  message: string,
): Promise<number | null> {
  try {
    const row = await db
      .prepare(
        "SELECT at FROM worker_events WHERE tenant_id = ? AND kind = ? AND message = ? ORDER BY at DESC LIMIT 1",
      )
      .bind(tenant, kind, message)
      .first<{ at: number }>();
    return row?.at ?? null;
  } catch {
    return null;
  }
}

/** ¿Hay un evento de clip idéntico reciente (mismo clip + tenant
 *  + step + mensaje)? Falla cerrado a false (se registra igual). */
export async function hasRecentIdenticalClipEvent(
  db: D1Database,
  clipId: number,
  tenant: Tenant,
  step: string,
  message: string,
  nowMsValue: number,
): Promise<boolean> {
  const at = await lastIdenticalClipEventAt(db, clipId, tenant, step, message);
  return at !== null && nowMsValue - at < EVENT_DEDUPE_WINDOW_MS;
}

/** Hora del último evento de clip idéntico (mismo clip + tenant +
 *  step + mensaje), o null si no hay ninguno. Falla cerrado a null.
 *  La dedupe y el contador agregado comparten esta única lectura. */
export async function lastIdenticalClipEventAt(
  db: D1Database,
  clipId: number,
  tenant: Tenant,
  step: string,
  message: string,
): Promise<number | null> {
  try {
    const row = await db
      .prepare(
        "SELECT at FROM clip_events WHERE clip_id = ? AND tenant_id = ? AND step = ? AND message = ? ORDER BY at DESC LIMIT 1",
      )
      .bind(clipId, tenant, step, message)
      .first<{ at: number }>();
    return row?.at ?? null;
  } catch {
    return null;
  }
}

/** ¿Hay un aviso idéntico reciente en notifications (mismo tenant +
 *  kind + mensaje)? Misma ventana de 6 h que el resto de eventos
 *  (A2): notify() usaba su propia ventana duplicada en jobs.ts y
 *  su propia consulta; ahora comparte esta. Falla cerrado a false
 *  (se registra igual). */
export async function hasRecentIdenticalNotification(
  db: D1Database,
  tenant: Tenant,
  kind: string,
  message: string,
  nowMsValue: number,
): Promise<boolean> {
  const at = await lastIdenticalNotificationAt(db, tenant, kind, message);
  return at !== null && nowMsValue - at < EVENT_DEDUPE_WINDOW_MS;
}

/** Hora del último aviso idéntico, o null si no hay ninguno. Falla
 *  cerrado a null. La dedupe y el contador agregado comparten esta
 *  única lectura (índice idx_notifications_dedupe, migración 0034). */
export async function lastIdenticalNotificationAt(
  db: D1Database,
  tenant: Tenant,
  kind: string,
  message: string,
): Promise<number | null> {
  try {
    const row = await db
      .prepare(
        "SELECT created_at FROM notifications WHERE tenant_id = ? AND kind = ? AND message = ? ORDER BY created_at DESC LIMIT 1",
      )
      .bind(tenant, kind, message)
      .first<{ created_at: number }>();
    return row?.created_at ?? null;
  } catch {
    return null;
  }
}

// --- Caducidad de eventos (A2, 2026-11-20, cuota D1) ----------------
// Proceso nocturno, una vez al día a hora fija (03:00 Santiago):
// borra eventos de más de 14 días en lotes pequeños. Los DELETE
// también cuentan en la cuota, así que solo corre si el consumo
// estimado del día está por debajo del hito del 70%.

/** ¿Es la hora fija de la caducidad (A2)? Hora America/Santiago. */
export function isPruneHour(nowMsValue: number): boolean {
  return Math.floor(santiagoMinutesOfDay(nowMsValue) / 60) === EVENT_PRUNE_HOUR_SANTIAGO;
}

/** Estimación de escrituras de hoy del tenant (A2): nº de filas
 *  creadas hoy (00:00 UTC = reset de cuota) en las tablas que más
 *  escriben. Es un PROXY, no el contador real: el contador
 *  autoritativo es el guardián de presupuesto (budget.ts, A1: API
 *  de analíticas de Cloudflare con fallback a contar filas). La
 *  caducidad usa este proxy a propósito: es solo D1 (sin fetch a
 *  la API, sin secretos, sin escrituras) y solo necesita saber si
 *  el día va caliente o frío. No cuenta UPDATEs ni DELETEs
 *  (volumen menor tras la dedupe) y el hito del 70% deja 30.000 de
 *  margen para ellos. */
export async function estimateTodayWrites(db: D1Database, tenant: Tenant, nowMsValue: number): Promise<number> {
  const dayStart = Math.floor(nowMsValue / 86400000) * 86400000;
  const counts: Array<[string, string]> = [
    ["worker_events", "at"],
    ["clip_events", "at"],
    ["notifications", "created_at"],
    ["jobs", "created_at"],
    ["brain_tokens", "created_at"],
    ["clip_stats", "recorded_at"],
  ];
  let total = 0;
  for (const [table, col] of counts) {
    const row = await db
      .prepare(`SELECT COUNT(*) AS n FROM ${table} WHERE tenant_id = ? AND ${col} >= ?`)
      .bind(tenant, dayStart)
      .first<{ n: number }>()
      .catch(() => null);
    total += row?.n ?? 0;
  }
  return total;
}

export interface PruneResult {
  ran: boolean;
  reason?: string;
  deleted: Record<string, number>;
  estimatedWritesToday?: number;
}

/** Caducidad de eventos (A2): borra eventos de más de
 *  EVENT_RETENTION_DAYS días en lotes de EVENT_PRUNE_BATCH_SIZE,
 *  con tope de EVENT_PRUNE_MAX_ROWS_PER_NIGHT por noche y tenant.
 *  Solo corre si la estimación de escrituras del día está por
 *  debajo del hito del 70% (EVENT_PRUNE_WRITE_GATE): los DELETE
 *  también cuentan en la cuota. Un DELETE que no borra nada cuesta
 *  0 escrituras, así que llamar cada minuto dentro de la hora de
 *  caducidad es seguro: la primera pasada vacía lo viejo y las
 *  siguientes no escriben nada. Nunca lanza. */
export async function pruneOldEvents(db: D1Database, tenant: Tenant, nowMsValue: number): Promise<PruneResult> {
  try {
    if (!isPruneHour(nowMsValue)) return { ran: false, reason: "not_prune_hour", deleted: {} };
    const estimated = await estimateTodayWrites(db, tenant, nowMsValue);
    if (estimated >= EVENT_PRUNE_WRITE_GATE) {
      return { ran: false, reason: "over_budget_gate", deleted: {}, estimatedWritesToday: estimated };
    }
    const cutoff = nowMsValue - EVENT_RETENTION_DAYS * 86400000;
    const tables: Array<{ name: string; timeCol: string }> = [
      { name: "worker_events", timeCol: "at" },
      { name: "clip_events", timeCol: "at" },
      { name: "notifications", timeCol: "created_at" },
      { name: "event_counters", timeCol: "window_start" },
    ];
    const deleted: Record<string, number> = {};
    let budget = EVENT_PRUNE_MAX_ROWS_PER_NIGHT;
    for (const t of tables) {
      let n = 0;
      while (budget > 0) {
        const batchSize = Math.min(EVENT_PRUNE_BATCH_SIZE, budget);
        const res = await db
          .prepare(
            `DELETE FROM ${t.name} WHERE rowid IN (SELECT rowid FROM ${t.name} WHERE tenant_id = ? AND ${t.timeCol} < ? LIMIT ?)`,
          )
          .bind(tenant, cutoff, batchSize)
          .run();
        const changed = res.meta.changes ?? 0;
        n += changed;
        budget -= changed;
        if (changed === 0) break;
      }
      deleted[t.name] = n;
    }
    return { ran: true, deleted, estimatedWritesToday: estimated };
  } catch (e) {
    return { ran: false, reason: e instanceof Error ? e.message : String(e), deleted: {} };
  }
}

/** ¿Hay un evento reciente de ese tipo y prefijo de mensaje?
 *  Anti-spam del log (2026-11-19): el Worker corre cada minuto y
 *  una corrida larga dejaba decenas de avisos idénticos ("hay una
 *  corrida en curso"). Falla cerrado a false (se registra igual):
 *  el log nunca rompe el ciclo. */
export async function hasRecentWorkerEvent(
  db: D1Database,
  tenant: Tenant,
  kind: string,
  messagePrefix: string,
  withinMs: number,
  nowMsValue: number,
): Promise<boolean> {
  try {
    const row = await db
      .prepare(
        "SELECT at FROM worker_events WHERE tenant_id = ? AND kind = ? AND message LIKE ? ORDER BY at DESC LIMIT 1",
      )
      .bind(tenant, kind, `${messagePrefix}%`)
      .first<{ at: number }>();
    return !!row && nowMsValue - row.at < withinMs;
  } catch {
    return false;
  }
}

/** publish_states sin updatedAt (que siempre cambia): para comparar
 *  si un parche cambia algo real antes de escribir (A2). */
function stripPublishStateUpdatedAt(
  states: Record<string, PlatformPublishEntry>,
): Record<string, Omit<PlatformPublishEntry, "updatedAt">> {
  const out: Record<string, Omit<PlatformPublishEntry, "updatedAt">> = {};
  for (const [k, v] of Object.entries(states)) {
    out[k] = { state: v.state, scheduledFor: v.scheduledFor, publishedAt: v.publishedAt, note: v.note, since: v.since };
  }
  return out;
}

/** Fusiona parches de publish_states sobre el clip y resuelve el
 *  cierre del clip (§9): status='publicado' + published_at cuando
 *  todas las plataformas están en estado terminal y al menos una
 *  publicó. Puro (no escribe): lo usan updateClipPublishState y
 *  updateClipPublishStates. */
function mergePublishStates(
  clip: ClipRow,
  patches: Array<{ platform: PanelPlatform; patch: Partial<PlatformPublishEntry> & { state: PlatformPublishEntry["state"] } }>,
): { states: Record<string, PlatformPublishEntry>; status: string | null; publishedAt: number | null } {
  const states = parsePublishStates(clip.publish_states);
  for (const { platform, patch } of patches) {
    const prev = states[platform] ?? {
      state: "pendiente" as const,
      scheduledFor: null,
      publishedAt: null,
      note: "",
      since: new Date(clip.created_at).toISOString(),
      updatedAt: new Date(clip.created_at).toISOString(),
    };
    states[platform] = { ...prev, ...patch, updatedAt: new Date().toISOString() };
  }
  const terminal = ["publicado", "fallido", "expirado", "cancelado"];
  const platforms = parsePlatforms(clip.platforms);
  const allTerminal =
    platforms.length > 0 && platforms.every((p) => terminal.includes(states[p]?.state ?? "pendiente"));
  const published = platforms.filter((p) => states[p]?.state === "publicado");
  let status: string | null = null;
  let publishedAt: number | null = null;
  if (allTerminal && published.length > 0) {
    status = "publicado";
    const last = Math.max(
      ...published.map((p) => Date.parse(states[p]?.publishedAt ?? "") || 0),
    );
    if (last > 0) publishedAt = last;
  }
  return { states, status, publishedAt };
}

/** Actualiza clips.publish_states (JSON) y, si procede, el cierre del clip (§9). */
export async function updateClipPublishState(
  db: D1Database,
  clipId: number,
  platform: PanelPlatform,
  patch: Partial<PlatformPublishEntry> & { state: PlatformPublishEntry["state"] },
  tenant: Tenant,
): Promise<void> {
  await updateClipPublishStates(db, clipId, [{ platform, patch }], tenant);
}

/** Igual que updateClipPublishState, pero con VARIAS plataformas en
 *  UN solo UPDATE (A2, batch): encolar un clip en 4 plataformas
 *  escribía 4 veces el mismo JSON de publish_states; ahora 1 vez.
 *  Y si ningún parche cambia nada real, no escribe nada (A2,
 *  salto de UPDATE sin cambios). */
export async function updateClipPublishStates(
  db: D1Database,
  clipId: number,
  patches: Array<{ platform: PanelPlatform; patch: Partial<PlatformPublishEntry> & { state: PlatformPublishEntry["state"] } }>,
  tenant: Tenant,
): Promise<void> {
  if (patches.length === 0) return;
  const clip = await getClip(db, clipId, tenant);
  if (!clip) return;
  const { states, status, publishedAt } = mergePublishStates(clip, patches);
  const before = JSON.stringify(stripPublishStateUpdatedAt(parsePublishStates(clip.publish_states)));
  const after = JSON.stringify(stripPublishStateUpdatedAt(states));
  const newStatus = status ?? clip.status;
  const newPublishedAt = publishedAt ?? clip.published_at;
  if (before === after && newStatus === clip.status && sameValue(newPublishedAt, clip.published_at)) return;
  const sets: string[] = ["publish_states = ?", "updated_at = ?"];
  const binds: unknown[] = [JSON.stringify(states), nowMs()];
  if (status !== null) {
    sets.push("status = 'publicado'");
    if (publishedAt !== null) {
      sets.push("published_at = ?");
      binds.push(publishedAt);
    }
  }
  binds.push(clipId);
  await db.prepare(`UPDATE clips SET ${sets.join(", ")} WHERE id = ?`).bind(...binds).run();
}
