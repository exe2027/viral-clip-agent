// Prioridad de campaña en el plan diario (2026-11-23, orden del
// usuario 18:37): los clips de streamers con campaña >= $1/1k NETO
// Y famosos (regla 18:50) ocupan primero
// campaign_priority_share_pct % de los slots de cada cuenta
// (default 60%), sin dejar a cero a los demás y sin tocar topes,
// espaciamiento ni umbrales 45/80. Constructor puro (buildDailyPlan):
// sin D1, sin red.
import { describe, expect, test } from "bun:test";
import { effectiveSettings, santiagoWallToUtcMs, validateSettingsInput } from "../src/db";
import { buildDailyPlan, type DailyPlanAccountInput, type DailyPlanClipInput } from "../src/daily-plan";
import { campaignForStreamer, isCampaignStreamer, isFamousCampaignStreamer } from "../src/db";

const now = santiagoWallToUtcMs("2026-11-23", "13:00");

function acc(over: Partial<DailyPlanAccountInput> = {}): DailyPlanAccountInput {
  return {
    id: 1, platform: "tiktok", apiPlatform: "tiktok", language: "en", label: "TikTok EN",
    stage: "establecida", plan: 3, max: 3, daysSinceFirst: 40, strikes: 0,
    publishedToday: 0, publishedLast24h: 0, hasCredential: true,
    pausedUntilMs: null, restDay: false, restWeekday: 0, canEscalate: false, ...over,
  };
}

function clipInput(id: number, score: number, over: Partial<DailyPlanClipInput> = {}): DailyPlanClipInput {
  return {
    id, title: `Clip ${id}`, description: `Desc ${id}`,
    hashtags: ["a", "b", "c"], score, language: "en",
    platforms: ["tiktok", "instagram_reels", "facebook_reels", "youtube_shorts"],
    streamer: null, campaign: false, ...over,
  };
}

describe("mapa streamer -> campaña (fuente única TS)", () => {
  test("neta >= $1/1k Y famoso: Dasgasd3m, ArthurTV, Dylan sí; Bobby (734 seguidores, regla 18:50) y BenDaDonnn ($0,90) no", () => {
    expect(isCampaignStreamer("Dasgasd3m")).toBe(true);
    expect(isCampaignStreamer("ArthurTV")).toBe(true);
    expect(isCampaignStreamer("Bobby Van Riper")).toBe(false);
    expect(isCampaignStreamer("Dylan Page")).toBe(true);
    expect(isCampaignStreamer("BenDaDonnn")).toBe(false);
    expect(isCampaignStreamer("Rubius")).toBe(false);
    expect(isCampaignStreamer(null)).toBe(false);
    expect(campaignForStreamer("dasgasd3m")?.netRatePer1k).toBe(1.8);
    expect(campaignForStreamer("bendadonnn")?.netRatePer1k).toBe(0.9);
    expect(campaignForStreamer("bobbyvanriper")?.followers).toBe(734);
    expect(isFamousCampaignStreamer("Bobby Van Riper")).toBe(false);
    expect(isFamousCampaignStreamer("Dasgasd3m")).toBe(true);
    expect(isFamousCampaignStreamer("ArthurTV")).toBe(true);
  });
  test("Bobby Van Riper (neta $1,35 pero NO famoso) NO tiene prioridad aunque venga marcado", () => {
    expect(isCampaignStreamer("Bobby Van Riper")).toBe(false);
    const eff = effectiveSettings({});
    const plan = buildDailyPlan({
      fecha: "2026-11-23",
      accounts: [acc()],
      queue: [
        clipInput(1, 95),
        clipInput(7, 91, { streamer: "Bobby Van Riper", campaign: false }),
      ],
      eff,
      nowMs: now,
    });
    expect(plan.publicaciones.map((p) => p.clip_id)).toEqual([1, 7]);
    expect(plan.publicaciones[1]?.motivo ?? "").not.toContain("Campaña");
  });
  test("settings: campaign_priority_share_pct default 60, rango 0-100", () => {
    expect(effectiveSettings({}).campaign_priority_share_pct).toBe(60);
    expect(effectiveSettings({ campaign_priority_share_pct: 0 }).campaign_priority_share_pct).toBe(0);
    expect(effectiveSettings({ campaign_priority_share_pct: 150 }).campaign_priority_share_pct).toBe(100);
    expect(validateSettingsInput({ campaign_priority_share_pct: 101 })).toContain("0 y 100");
    expect(validateSettingsInput({ campaign_priority_share_pct: 60 })).toBeNull();
  });
});

describe("buildDailyPlan — prioridad de campaña", () => {
  test("con 60% y capacidad 3, un clip de campaña con MENOS score va primero (2 slots) y el resto no queda a cero", () => {
    const eff = effectiveSettings({});
    const plan = buildDailyPlan({
      fecha: "2026-11-23",
      accounts: [acc()],
      queue: [
        clipInput(1, 95),
        clipInput(2, 90),
        clipInput(3, 70, { streamer: "Dasgasd3m", campaign: true }),
        clipInput(4, 65, { streamer: "ArthurTV", campaign: true }),
        clipInput(5, 60),
      ],
      eff,
      nowMs: now,
    });
    const tt = plan.publicaciones.filter((p) => p.plataforma === "tiktok");
    expect(tt).toHaveLength(3);
    // Reserva 60% de 3 = 2 slots: clips 3 y 4 (campaña) primero,
    // aunque tengan menos score; el 3er slot es el top restante (1).
    expect(tt.map((p) => p.clip_id)).toEqual([3, 4, 1]);
    expect(tt[0]?.motivo).toContain("Campaña Dasgasd3m");
    expect(tt[0]?.motivo).toContain("prioridad 60%");
    expect(plan.en_cola).toEqual([2, 5]);
    expect(plan.alertas.join(" ")).toContain("prioridad de campaña");
  });

  test("pct=0: sin prioridad, el orden es el de la Cola (por score)", () => {
    const eff = effectiveSettings({ campaign_priority_share_pct: 0 });
    const plan = buildDailyPlan({
      fecha: "2026-11-23",
      accounts: [acc()],
      queue: [
        clipInput(1, 95),
        clipInput(3, 70, { streamer: "Dasgasd3m", campaign: true }),
      ],
      eff,
      nowMs: now,
    });
    expect(plan.publicaciones.map((p) => p.clip_id)).toEqual([1, 3]);
  });

  test("pct=100: todos los slots primero a campaña; sin clips de campaña, plan idéntico al de antes", () => {
    const eff100 = effectiveSettings({ campaign_priority_share_pct: 100 });
    const plan = buildDailyPlan({
      fecha: "2026-11-23",
      accounts: [acc()],
      queue: [clipInput(1, 95), clipInput(3, 70, { streamer: "Dasgasd3m", campaign: true })],
      eff: eff100,
      nowMs: now,
    });
    expect(plan.publicaciones.map((p) => p.clip_id)).toEqual([3, 1]);
    const eff = effectiveSettings({});
    const plain = buildDailyPlan({
      fecha: "2026-11-23",
      accounts: [acc()],
      queue: [clipInput(1, 95), clipInput(2, 90), clipInput(5, 60)],
      eff,
      nowMs: now,
    });
    expect(plain.publicaciones.map((p) => p.clip_id)).toEqual([1, 2, 5]);
  });

  test("la reserva es un mínimo, no un techo: si sobran huecos, la campaña puede tomar más por orden normal", () => {
    const eff = effectiveSettings({});
    const plan = buildDailyPlan({
      fecha: "2026-11-23",
      accounts: [acc()],
      queue: [
        clipInput(3, 96, { streamer: "Dasgasd3m", campaign: true }),
        clipInput(4, 94, { streamer: "ArthurTV", campaign: true }),
        clipInput(5, 92, { streamer: "Dylan Page", campaign: true }),
        clipInput(1, 50),
      ],
      eff,
      nowMs: now,
    });
    // Capacidad 3: reserva 2, pero en la pasada 2 el clip 5 (92)
    // gana al clip 1 (50) por orden de Cola.
    expect(plan.publicaciones.map((p) => p.clip_id)).toEqual([3, 4, 5]);
    expect(plan.en_cola).toEqual([1]);
  });

  test("BenDaDonnn (bajo piso neto) NO tiene prioridad aunque venga marcado: solo vale isCampaignStreamer", () => {
    // buildDailyPlan confía en el flag `campaign` que pone
    // buildDailyPlanForTenant con isCampaignStreamer; aquí se
    // verifica que ese flag es false para BenDaDonnn.
    expect(isCampaignStreamer("BenDaDonnn")).toBe(false);
    const eff = effectiveSettings({});
    const plan = buildDailyPlan({
      fecha: "2026-11-23",
      accounts: [acc()],
      queue: [
        clipInput(1, 95),
        clipInput(6, 70, { streamer: "BenDaDonnn", campaign: false }),
      ],
      eff,
      nowMs: now,
    });
    expect(plan.publicaciones.map((p) => p.clip_id)).toEqual([1, 6]);
    expect(plan.publicaciones[1]?.motivo ?? "").not.toContain("Campaña");
  });

  test("la prioridad no rompe los topes: capacidad 3 sigue siendo 3", () => {
    const eff = effectiveSettings({});
    const plan = buildDailyPlan({
      fecha: "2026-11-23",
      accounts: [acc()],
      queue: [
        clipInput(3, 96, { streamer: "Dasgasd3m", campaign: true }),
        clipInput(4, 94, { streamer: "ArthurTV", campaign: true }),
        clipInput(5, 92, { streamer: "Dylan Page", campaign: true }),
        clipInput(7, 91, { streamer: "Dasgasd3m", campaign: true }),
        clipInput(1, 50),
      ],
      eff,
      nowMs: now,
    });
    expect(plan.publicaciones).toHaveLength(3);
  });
});
