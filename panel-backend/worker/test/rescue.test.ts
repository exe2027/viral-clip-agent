// Tests del rescate por gritos (2026-11-18, decisión del usuario):
// un clip con viralidad revisada 40-44 (banda de 5 puntos justo
// debajo del mínimo 45, puerta única P0 #3), 2+ s de gritos del
// streamer y SEO >= 80 entra a la Cola BLOQUEADO para revisión
// humana, nunca auto-publicable. Sin rescate, la puerta 45 sigue
// intacta, y un rescate que no califica se rechaza con el motivo
// exacto en vez de colarse. publishGate nombra el freno como
// rescate, no como falta de subtítulos.
import { describe, expect, test } from "bun:test";
import { parseClipInput } from "../src/index";
import { publishGate } from "../src/gate";
import type { ClipRow } from "../src/db";

const SEO_OK = {
  youtube_shorts: 94,
  tiktok: 92,
  instagram_reels: 91,
  facebook_reels: 90,
};

function base(over: Record<string, unknown> = {}) {
  return {
    title: "Rescate test",
    start_sec: 100,
    end_sec: 140,
    game: "GTA VI",
    platforms: ["youtube_shorts", "tiktok", "instagram_reels", "facebook_reels"],
    language: "es",
    subtitles_complete: 1,
    ...over,
  };
}

describe("parseClipInput: rescate por gritos", () => {
  test("score 44 sin rescate: sigue rechazado por la puerta 45", () => {
    const r = parseClipInput(base({ score: 44 }), {});
    expect(typeof r).toBe("string");
    expect(r as string).toContain("bajo el mínimo de 45");
  });

  test("rescate válido (viral 43,5, 17 s de gritos, SEO >= 80): entra bloqueado", () => {
    const r = parseClipInput(
      base({ score: 43, rescue: true, rescue_shouts: 17, viral_score: 43.5, seo_scores: SEO_OK }),
      {},
    );
    expect(typeof r).toBe("object");
    const clip = r as Exclude<typeof r, string>;
    expect(clip.publish_blocked).toBe(1);
    expect(clip.score).toBe(43);
    const viral = JSON.parse(clip.viral as string) as Record<string, unknown>;
    expect(viral.viral_score).toBe(43.5);
    expect(viral.seo_scores).toEqual(SEO_OK);
    const rescue = viral.rescue as { viral_revisado: number; shouts: number };
    expect(rescue.viral_revisado).toBe(43.5);
    expect(rescue.shouts).toBe(17);
  });

  test("rescate con viralidad 39: no califica, se rechaza explicando el rescate", () => {
    const r = parseClipInput(
      base({ score: 44, rescue: true, rescue_shouts: 17, viral_score: 39, seo_scores: SEO_OK }),
      {},
    );
    expect(typeof r).toBe("string");
    expect(r as string).toContain("Rescate no válido");
  });

  test("rescate con SEO máximo 79: no califica, el SEO no se salta", () => {
    const r = parseClipInput(
      base({
        score: 43, rescue: true, rescue_shouts: 17, viral_score: 43.5,
        seo_scores: { youtube_shorts: 79, tiktok: 78, instagram_reels: 77, facebook_reels: 76 },
      }),
      {},
    );
    expect(typeof r).toBe("string");
    expect(r as string).toContain("Rescate no válido");
  });

  test("rescate con 1 s de gritos: no califica (mínimo 2 s)", () => {
    const r = parseClipInput(
      base({ score: 43, rescue: true, rescue_shouts: 1, viral_score: 43.5, seo_scores: SEO_OK }),
      {},
    );
    expect(typeof r).toBe("string");
    expect(r as string).toContain("Rescate no válido");
  });

  test("clip normal con score 55 y subtítulos: entra desbloqueado", () => {
    const r = parseClipInput(base({ score: 55, viral_score: 55, seo_scores: SEO_OK }), {});
    expect(typeof r).toBe("object");
    expect((r as Exclude<typeof r, string>).publish_blocked).toBe(0);
  });
});

describe("publishGate: freno del rescate", () => {
  function clip(viral: string, publishBlocked: number): ClipRow {
    return {
      id: 1, tenant_id: "sebastian", archived: 0, status: "en_carpeta",
      title: "Rescate", start_sec: 100, end_sec: 140, score: 45,
      game: "GTA VI", language: "es", publish_blocked: publishBlocked,
      needs_human_transcription: 0, subtitles_complete: 1,
      viral, platforms: JSON.stringify(["youtube_shorts"]),
      created_at: 0, updated_at: 0,
    } as unknown as ClipRow;
  }

  const ctx = (c: ClipRow) => ({
    clip: c,
    seoScore: 94,
    seoOverride: false,
    publishedToday: 0,
    lastPublishedAtMs: null,
    streamerAllowed: true,
    settings: {},
  });

  test("rescatado bloqueado: el motivo dice rescate, no subtítulos", () => {
    const gate = publishGate(
      "youtube_shorts",
      ctx(clip(JSON.stringify({ rescue: { viral_revisado: 43.5, shouts: 17 } }), 1)),
    );
    expect(gate).toContain("rescatado");
    expect(gate).toContain("43.5");
  });

  test("bloqueado sin rescate: sigue siendo el freno de subtítulos", () => {
    const gate = publishGate("youtube_shorts", ctx(clip("{}", 1)));
    expect(gate).toContain("sin subtítulos completos");
  });

  test("rescatado desbloqueado por el usuario: ya no frena por rescate", () => {
    // B4: el clip desbloqueado lleva veredicto de calidad aprobado;
    // lo que se prueba aquí es que el rescate ya no frena, no la
    // puerta de calidad (sin veredicto frenaría por "sin veredicto").
    const gate = publishGate(
      "youtube_shorts",
      ctx(clip(JSON.stringify({ rescue: { viral_revisado: 43.5, shouts: 17 }, quality_gate: { decision: "aprobado", pasa: true, fallos: [], motivo: "" } }), 0)),
    );
    expect(gate).toBeNull();
  });
});
