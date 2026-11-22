// Puerta de calidad B4 (2026-11-20, "que no vuelva a pasar"):
// NINGÚN job de publicación nace para un clip que no haya pasado
// la validación final del cerebro (cara + formato + Gemini sobre
// el vídeo final renderizado, ver viral-clip-agent/clipagent/
// final_review.py). La auditoría de la Cola encontró 121 clips
// bloqueados defectuosos (76%, sin cara y formato mal): esta
// puerta existe para que eso no vuelva a ocurrir.
//
// DÓNDE VIVE EL VEREDICTO: en clips.viral.quality_gate (JSON),
// que el cerebro manda en POST /api/clips y el panel guarda con
// parseViralInput/mergeViralJson (ver db.ts). NO es una columna
// nueva: una migración exigiría aplicarla en D1 antes de que el
// Worker pudiera leerla, y con la cuota agotada (104.610/100.000)
// no se puede aplicar ni probar nada contra la D1 real ahora; el
// JSON funciona con el esquema que ya existe. TAMPOCO se reutiliza
// publish_blocked: ese campo ya significa "sin subtítulos
// completos" y publishGate, el rescate por gritos y el Editor
// dependen de ese significado; mezclar dos bloqueos distintos en
// un bit rompería el motivo "sin subtítulos" y el flujo de
// rescate. quality_gate es un objeto aparte, con su propio motivo.
//
// ÚNICA FUENTE en el Worker de los 6 criterios, las 5 decisiones
// y el umbral de alerta del 30%: este módulo. db.ts solo valida
// la FORMA del objeto (nunca su significado) para no duplicar
// literales ni crear un ciclo de imports con este módulo.
//
// Reglas del gate (qualityGateForClip):
// - quality_gate.pasa === true (aprobado u observado) -> pasa.
// - decision "rechazado" (fallo DURO: cara/formato) -> bloquea
//   SIEMPRE, también a mano: un clip con defecto verificado no se
//   publica por ningún camino. El desbloqueo humano es re-renderizar
//   o re-validar, no un botón que lo ignore.
// - sin veredicto (pendiente/desactivada, o clip anterior a B4 sin
//   quality_gate) -> desde el refuerzo del validador (2026-11-21,
//   lección scooter: un clip no-rubius publicó por el panel sin
//   pasar por el gate de fotogramas) bloquea TAMBIÉN a mano: un
//   clip sin validar no se publica por ningún camino. La única
//   vía es validar el clip (el cerebro, validate_all) o que un
//   humano vea el preview (preview_seen=1, evidencia de revisión
//   humana, que sigue pasando). Antes la manual pasaba con aviso;
//   ese bypass ya no existe.
// - veredicto ilegible (JSON roto) -> como sin veredicto.

import { parseViralJson, type ClipRow } from "./db";

/** Los 6 criterios de la puerta B4, con su etiqueta en español
 *  para los motivos. El detalle por criterio (qué mide cada uno y
 *  cuál es duro) vive en el cerebro (quality_gate.py); aquí solo
 *  se lee el veredicto ya calculado. */
export const QUALITY_CRITERIA = [
  "formato_9_16",
  "webcam",
  "hook",
  "titulo",
  "miniatura",
  "audio",
] as const;
export type QualityCriterion = (typeof QUALITY_CRITERIA)[number];

export const QUALITY_CRITERION_LABELS: Record<QualityCriterion, string> = {
  formato_9_16: "formato 9:16",
  webcam: "webcam presente",
  hook: "hook",
  titulo: "título",
  miniatura: "miniatura",
  audio: "audio",
};

/** Decisiones del veredicto (las mismas que final_review.py). */
export const QUALITY_DECISIONS = [
  "aprobado",
  "rechazado",
  "observado",
  "pendiente_revision",
  "desactivada",
] as const;
export type QualityDecision = (typeof QUALITY_DECISIONS)[number];

/** Umbral de alerta B4: si los descartes por calidad de un día
 *  superan este % de los clips considerados, se avisa (ver
 *  maybeAlertQualityDiscards en jobs.ts). */
export const QUALITY_DISCARD_ALERT_PCT = 30;
/** Muestra mínima para alertar: con menos clips considerados el
 *  % no significa nada (1 de 1 sería 100%) y solo haría ruido. */
export const QUALITY_ALERT_MIN_SAMPLE = 5;

export interface QualityGateVerdict {
  decision: QualityDecision;
  /** true = pasa; false = no pasa; null = sin veredicto
   *  (desactivada). */
  pasa: boolean | null;
  /** Criterios que fallan (subset de QUALITY_CRITERIA). */
  fallos?: string[];
  /** Criterios duros que fallan (los que bloquean también a mano). */
  fallos_duros?: string[];
  motivo?: string;
  criterios?: Record<string, { pasa: boolean; duro: boolean; motivo?: string }>;
}

export interface QualityGateCheck {
  /** null = pasa; motivo "calidad: ..." = frena. */
  reason: string | null;
  /** true = frena también la publicación manual (rechazado). */
  hard: boolean;
}

/** Lee el veredicto de clips.viral.quality_gate. null = sin
 *  veredicto (clip anterior a B4, o viral vacío/roto). Un objeto
 *  con forma inválida también es null: qualityGateForClip lo
 *  distingue por la presencia de la clave y lo trata como "sin
 *  veredicto válido" (bloqueo blando, nunca aprobado). */
export function parseQualityGate(clip: ClipRow): QualityGateVerdict | null {
  const viral = parseViralJson(clip.viral);
  const raw = (viral as Record<string, unknown>).quality_gate;
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const v = raw as Record<string, unknown>;
  if (typeof v.decision !== "string" || !(QUALITY_DECISIONS as readonly string[]).includes(v.decision)) return null;
  if (v.pasa !== true && v.pasa !== false && v.pasa !== null) return null;
  const fallos = Array.isArray(v.fallos) ? v.fallos.filter((f): f is string => typeof f === "string") : [];
  return {
    decision: v.decision as QualityDecision,
    pasa: v.pasa as boolean | null,
    fallos,
    fallos_duros: Array.isArray(v.fallos_duros)
      ? v.fallos_duros.filter((f): f is string => typeof f === "string")
      : [],
    motivo: typeof v.motivo === "string" ? v.motivo : "",
    criterios: typeof v.criterios === "object" && v.criterios !== null
      ? (v.criterios as QualityGateVerdict["criterios"])
      : undefined,
  };
}

/** ¿Trae el clip una clave quality_gate (aunque sea ilegible)? */
export function hasQualityGate(clip: ClipRow): boolean {
  const viral = parseViralJson(clip.viral);
  return (viral as Record<string, unknown>).quality_gate !== undefined;
}

function failedLabels(v: QualityGateVerdict): string {
  const names = (v.fallos ?? []).filter((f) =>
    (QUALITY_CRITERIA as readonly string[]).includes(f),
  );
  if (names.length === 0) return "";
  return names
    .map((f) => QUALITY_CRITERION_LABELS[f as QualityCriterion] ?? f)
    .join(", ");
}

/** La puerta B4 sobre un clip. Pura: no toca D1, así se prueba
 *  sin mocks. Ver el comentario del módulo para las reglas. */
export function qualityGateForClip(clip: ClipRow): QualityGateCheck {
  const v = parseQualityGate(clip);
  if (v) {
    if (v.pasa === true) return { reason: null, hard: false };
    if (v.decision === "rechazado") {
      const detalle = failedLabels(v);
      const motivo = v.motivo || (detalle ? `fallan: ${detalle}` : "validación final rechazada");
      return { reason: `calidad: ${motivo}`, hard: true };
    }
    // pendiente_revision / desactivada: sin veredicto válido.
    const motivo = v.motivo || `validación ${v.decision}`;
    return { reason: `calidad: sin veredicto válido (${motivo})`, hard: false };
  }
  if (hasQualityGate(clip)) {
    return { reason: "calidad: veredicto de calidad ilegible (JSON roto)", hard: false };
  }
  // Clip anterior a B4: sin veredicto. preview_seen=1 ES evidencia
  // de validación (cerebro tras final_review "aprobado", o humano
  // que vio el preview): pasa. Sin él, no se publica por ningún
  // camino desde el refuerzo del validador (2026-11-21): hay que
  // validar el clip (cerebro, validate_all) o ver el preview.
  if (clip.preview_seen === 1) return { reason: null, hard: false };
  return {
    reason: "calidad: sin veredicto de calidad (clip sin validar: validar en el Editor o re-renderizar)",
    hard: false,
  };
}
