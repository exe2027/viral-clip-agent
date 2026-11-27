// Cobertura de la tabla única de clasificación de bloqueos del
// gate (F2, spec 2026-11-24): cada motivo que `publishGate` (o
// los caminos de encolar/ejecutar que lo rodean) puede devolver
// está en `GATE_BLOCKS` con su clasificación esperada
// (transitorio/duro + tipo de reintento). Si alguien añade un
// motivo nuevo al gate y no lo da de alta en la tabla, este test
// lo caza: `classifyGateBlock` es fail-closed (motivo no
// reconocido = duro) y el motivo nuevo aparecería aquí como
// "duro" cuando debería ser transitorio (o al revés).
// Los motivos son literales, copiados de gate.ts / jobs.ts /
// publish/index.ts (con valores de ejemplo donde el mensaje lleva
// números o fechas).
import { describe, expect, test } from "bun:test";
import { classifyGateBlock, type GateBlockRetryKind } from "../src/gate";

type Case = { reason: string; transient: boolean; retry: GateBlockRetryKind };

const TRANSIENT: Case[] = [
  // Piso de 90 min (SPEC quitar-franjas v2): el motivo nuevo.
  { reason: "menos de 90 min desde la última subida en esta plataforma", transient: true, retry: "min90" },
  // Espaciamiento viejo por horas: el gate ya no lo produce, pero
  // se mantiene UNA versión como transitorio → min90 (un job viejo
  // frenado por "menos de 2 h" se reprograma a última + azar
  // 90–180, no va a draft; en la versión siguiente se retira).
  { reason: "espaciamiento: menos de 2 h desde la última subida en esta plataforma", transient: true, retry: "min90" },
  { reason: "espaciamiento: menos de 3 h desde la última subida en esta plataforma", transient: true, retry: "min90" },
  // Piso de 5 min (sigue vivo como defensa).
  { reason: "espaciamiento: menos de 15 min desde la última subida en esta plataforma", transient: true, retry: "spacing" },
  // Mismo clip en otra plataforma (15 min).
  { reason: "mismo clip en otra plataforma hace 0 min: mínimo 15 min entre plataformas", transient: true, retry: "cross15" },
  { reason: "mismo clip en otra plataforma hace 14 min: mínimo 15 min entre plataformas", transient: true, retry: "cross15" },
  // 1 por plataforma y franja (retirado; transitorio una versión → min90).
  { reason: "ya se publicó 1 en tiktok en esta franja: como mucho 1 por plataforma y franja", transient: true, retry: "min90" },
  // Fuera de franja (retirado; transitorio una versión → min90).
  { reason: "fuera de franja: la publicación solo sale 12:00–14:00, 18:00–20:00 o 21:00–23:00 (hora de Chile)", transient: true, retry: "min90" },
  // Tope rolling 24 h.
  { reason: "tope rolling 24 h alcanzado: 3/3 en tiktok (últimas 24 h)", transient: true, retry: "rolling" },
  { reason: "tope rolling 24 h alcanzado: 6/6 en youtube_shorts (últimas 24 h)", transient: true, retry: "rolling" },
  // B3 (spec 2026-11-27): el conteo proyectado puede superar el
  // tope (posted + programados): el literal es el mismo y el regex
  // debe casarlo igual.
  { reason: "tope rolling 24 h alcanzado: 4/3 en tiktok (últimas 24 h)", transient: true, retry: "rolling" },
  { reason: "tope rolling 24 h alcanzado: 5/3 en facebook_reels (últimas 24 h)", transient: true, retry: "rolling" },
  // Tope diario / plan cubierto / calentamiento / día libre.
  { reason: "tope diario alcanzado: 3/3 en tiktok", transient: true, retry: "tomorrow" },
  { reason: "plan del día cubierto: 1/1 en tiktok (techo 3)", transient: true, retry: "tomorrow" },
  { reason: "calentamiento: 1/1 hoy en tiktok (cuenta nueva: ni a mano se supera el tope de su etapa)", transient: true, retry: "tomorrow" },
  { reason: "día libre de la cuenta en tiktok: hoy no publica (1 día libre por semana, rotativo)", transient: true, retry: "tomorrow" },
  // Reserva de cupo perdida / cupo no reservado / tope de reintentos.
  { reason: "reserva de cupo perdida: el job cambió de manos antes de publicar", transient: true, retry: "backoff" },
  { reason: "tope diario alcanzado: cupo no reservado en tiktok tras 3 intentos", transient: true, retry: "backoff" },
  { reason: "tope de reintentos por hoy: 3/3", transient: true, retry: "backoff" },
];

const HARD: Case[] = [
  { reason: "clip archivado", transient: false, retry: null },
  { reason: "clip ya publicado", transient: false, retry: null },
  { reason: "clip no encontrado", transient: false, retry: null },
  { reason: "el clip no tiene MP4 en R2: súbelo desde el Editor antes de publicar.", transient: false, retry: null },
  { reason: "el job apunta a una cuenta que ya no existe: vuelve a encolar el clip desde Publicar.", transient: false, retry: null },
  { reason: "sin cuenta YouTube en español: crea la cuenta en Cuentas y pega su credencial", transient: false, retry: null },
  { reason: "idioma: el clip es en español pero la cuenta YouTube es en inglés: nunca se publica en el canal del otro idioma", transient: false, retry: null },
  { reason: "cuenta YouTube en español sin credencial verificada: pega la credencial en Cuentas y verifícala", transient: false, retry: null },
  { reason: "rescatado por gritos (viralidad 30 < 45): bloqueado hasta revisión humana — desbloquéalo en el Editor", transient: false, retry: null },
  { reason: "sin subtítulos completos: necesita transcripción humana", transient: false, retry: null },
  { reason: "calidad: sin veredicto válido (validación pendiente_revision)", transient: false, retry: null },
  { reason: "calidad: sin cara visible", transient: false, retry: null },
  { reason: "falta ver el preview en el Editor", transient: false, retry: null },
  { reason: "duración 75s fuera de 1–60 s", transient: false, retry: null },
  { reason: "categoría: solo GTA VI (este clip es Fortnite)", transient: false, retry: null },
  { reason: "categoría: sin categoría GTA VI declarada", transient: false, retry: null },
  { reason: "el streamer no tiene permiso marcado", transient: false, retry: null },
  { reason: "streamer desactivado en el panel", transient: false, retry: null },
  { reason: "SEO 70: bajo la puerta de 80 en tiktok", transient: false, retry: null },
  { reason: "cuenta en pausa por spam/límite hasta 2026-11-21T00:00:00.000Z: no se publica ni a mano (pausa de 48 h)", transient: false, retry: null },
  { reason: "este clip ya se publicó hoy en tiktok: no se repite el mismo clip en la misma cuenta el mismo día", transient: false, retry: null },
  { reason: "este clip ya se publicó en tiktok el 2026-11-18: no se repite en la misma cuenta (usa republicar si la plataforma borró el vídeo)", transient: false, retry: null },
];

describe("GATE_BLOCKS: cada motivo del gate tiene su clasificación", () => {
  for (const c of [...TRANSIENT, ...HARD]) {
    test(`"${c.reason}" → ${c.transient ? "transitorio" : "duro"}${c.retry ? ` / ${c.retry}` : ""}`, () => {
      expect(classifyGateBlock(c.reason)).toEqual({ transient: c.transient, retry: c.retry });
    });
  }

  test("motivo no reconocido = duro (fail-closed): nunca se reprograma solo lo que no sabemos que el tiempo resuelve", () => {
    expect(classifyGateBlock("un motivo que nadie conoce")).toEqual({ transient: false, retry: null });
    expect(classifyGateBlock("")).toEqual({ transient: false, retry: null });
  });

  test("el 'cupo no reservado' no cae en el 'tope diario' genérico (la regla específica va antes)", () => {
    expect(classifyGateBlock("tope diario alcanzado: cupo no reservado en tiktok tras 3 intentos")).toEqual({
      transient: true,
      retry: "backoff",
    });
    expect(classifyGateBlock("tope diario alcanzado: 3/3 en tiktok")).toEqual({
      transient: true,
      retry: "tomorrow",
    });
  });
});
