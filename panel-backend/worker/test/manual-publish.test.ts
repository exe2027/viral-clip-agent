// Tests de publicación MANUAL (2026-11-19, botón "Publicar ahora"),
// actualizados a la orden 09:25 del 2026-11-21 (SIN AMONTONAR) y al
// refuerzo del validador (2026-11-21): la manual YA NO salta ningún
// freno anti-baneo (franja, tope diario, espaciamiento,
// cross-platform, 1-por-franja, calentamiento, rolling) ni el
// veredicto de calidad BLANDO (sin veredicto válido: un clip sin
// validar no se publica por ningún camino). Solo sigue saltando el
// día libre (planificación, no freno de seguridad). La calidad
// (idioma, subtítulos, duración, categoría, SEO, cuenta) sigue
// frenando igual.
import { describe, expect, test } from "bun:test";
import { publishGate, rateLimitGate, type GateContext } from "../src/jobs";
import type { ClipRow } from "../src/db";

// Instantes fijos en noviembre 2026 (Chile en horario de verano,
// UTC-3): 10:00 Santiago = 13:00 UTC (fuera de franja);
// 13:00 Santiago = 16:00 UTC (dentro de la franja de mañana).
const FUERA_DE_FRANJA_MS = Date.UTC(2026, 10, 21, 13, 0, 0);
const DENTRO_DE_FRANJA_MS = Date.UTC(2026, 10, 21, 16, 0, 0);

function clip(over: Partial<ClipRow> = {}): ClipRow {
  return {
    id: 1, tenant_id: "sebastian", source_id: null, influencer_id: null, title: "t", start_sec: 0, end_sec: 40,
    score: 80, status: "aprobado", subtitle_style: "tiktok", seo_title: "", seo_description: "",
    seo_tags: "", platforms: '["youtube_shorts"]', scheduled_for: null, published_at: null,
    file_deleted: 0, created_at: Date.now(), updated_at: Date.now(), captions: "{}",
    viral: '{"platforms":{"youtube_shorts":{"score":95}}}', seo_overrides: "[]",
    preview_seen: 1, archived: 0, origin: "vod", publish_states: "{}", game: "GTA VI",
    publish_blocked: 0, needs_human_transcription: 0, subtitles_complete: 1,
    video_r2_key: "", ...over,
  } as ClipRow;
}

function gateCtx(over: Partial<GateContext> = {}): GateContext {
  return {
    clip: clip(), seoScore: 95, seoOverride: false, publishedToday: 0,
    lastPublishedAtMs: null, streamerAllowed: true, settings: {}, ...over,
  };
}

describe("publicación manual: los frenos TAMBIÉN frenan (orden 09:25, SIN AMONTONAR)", () => {
  test("tope diario lleno + manual → FRENA (ya no lo salta)", () => {
    expect(publishGate("youtube_shorts", gateCtx({ publishedToday: 6, manual: true }))).toMatch(/tope diario/);
    expect(publishGate("youtube_shorts", gateCtx({ publishedToday: 6 }))).toMatch(/tope diario/);
  });

  test("plan del día cubierto + manual → FRENA", () => {
    const ctx = gateCtx({ publishedToday: 3, settings: { youtube_plan: 3, youtube_max: 6 }, manual: true });
    expect(publishGate("youtube_shorts", ctx)).toMatch(/plan del día cubierto/);
    expect(publishGate("youtube_shorts", gateCtx({ publishedToday: 3, settings: { youtube_plan: 3, youtube_max: 6 } }))).toMatch(/plan del día cubierto/);
  });

  test("espaciamiento reciente + manual → FRENA", () => {
    const last = Date.now() - 30 * 60 * 1000; // hace 30 min, menos de 4 h en YouTube
    expect(publishGate("youtube_shorts", gateCtx({ lastPublishedAtMs: last, manual: true }))).toMatch(/espaciamiento/);
    expect(publishGate("youtube_shorts", gateCtx({ lastPublishedAtMs: last }))).toMatch(/espaciamiento/);
  });

  test("fuera de franja + manual → FRENA (checkSlot también en manual)", () => {
    const ctx = gateCtx({ manual: true, checkSlot: true, nowMs: FUERA_DE_FRANJA_MS });
    expect(publishGate("youtube_shorts", ctx)).toMatch(/fuera de franja/);
    const dentro = gateCtx({ manual: true, checkSlot: true, nowMs: DENTRO_DE_FRANJA_MS });
    expect(publishGate("youtube_shorts", dentro)).toBeNull();
  });

  test("1 por plataforma y franja + manual → FRENA si ya se publicó 1 en esta franja", () => {
    expect(publishGate("youtube_shorts", gateCtx({ manual: true, publishedThisSlot: 1 }))).toMatch(/1 por plataforma y franja/);
    expect(publishGate("youtube_shorts", gateCtx({ manual: true, publishedThisSlot: 0 }))).toBeNull();
    expect(publishGate("youtube_shorts", gateCtx({ publishedThisSlot: 1 }))).toMatch(/1 por plataforma y franja/);
  });

  test("mismo clip en otra plataforma hace 5 min + manual → FRENA (cross-platform 15 min)", () => {
    const other = Date.now() - 5 * 60 * 1000;
    expect(publishGate("youtube_shorts", gateCtx({ manual: true, clipLastPublishedOtherPlatformMs: other }))).toMatch(/mismo clip en otra plataforma/);
  });

  test("SEO bajo + manual → SIGUE frenando", () => {
    expect(publishGate("youtube_shorts", gateCtx({ seoScore: 70, manual: true }))).toMatch(/SEO 70/);
  });

  test("sin subtítulos (publish_blocked) + manual → SIGUE frenando", () => {
    const reason = publishGate("youtube_shorts", gateCtx({ clip: clip({ publish_blocked: 1 }), manual: true }));
    expect(reason).toMatch(/subtítulos/);
  });

  // Regla 40-60 / 25-35 ELIMINADA 2026-11-21: 30 s + manual ya pasa.
  test("clip de 30 s + manual → ya no frena por duración", () => {
    const reason = publishGate("youtube_shorts", gateCtx({ clip: clip({ end_sec: 30 }), manual: true }));
    expect(reason).toBe(null);
  });

  test("categoría GTA V + manual → SIGUE frenando", () => {
    const reason = publishGate("youtube_shorts", gateCtx({ clip: clip({ game: "GTA V" }), manual: true }));
    expect(reason).toMatch(/categoría/);
  });
});

describe("rateLimitGate: el aviso del freno saltado", () => {
  test("tope lleno → devuelve el motivo; sin freno → null", () => {
    expect(rateLimitGate("youtube_shorts", gateCtx({ publishedToday: 6 }))).toMatch(/tope diario alcanzado: 6\/6/);
    expect(rateLimitGate("youtube_shorts", gateCtx({ publishedToday: 0 }))).toBeNull();
  });

  test("plan cubierto → motivo con el techo", () => {
    const reason = rateLimitGate("youtube_shorts", gateCtx({ publishedToday: 3, settings: { youtube_plan: 3, youtube_max: 6 } }));
    expect(reason).toMatch(/plan del día cubierto: 3\/3 en youtube_shorts \(techo 6\)/);
  });

  test("espaciamiento reciente → motivo; 0 h → null (TikTok usa spacing_hours)", () => {
    const last = Date.now() - 30 * 60 * 1000;
    expect(rateLimitGate("tiktok", gateCtx({ lastPublishedAtMs: last }))).toMatch(/espaciamiento/);
    expect(rateLimitGate("tiktok", gateCtx({ lastPublishedAtMs: last, settings: { spacing_hours: 0 } }))).toBeNull();
  });
});
