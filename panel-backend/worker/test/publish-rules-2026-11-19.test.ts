// Reglas de publicación 2026-11-19 (reemplazan las del 2026-11-18):
// topes por plataforma (TikTok 3/3, Instagram 3/3, YouTube 6/6,
// Facebook 3/3; orden usuario 2026-11-21 21:09: IG/FB a 3 y
// YouTube a 6), calentamiento nuevo (<14 días o <20 publicaciones;
// semana 1/2: plan de calentamiento, semana 3: plan, establecida: plan+máx),
// rolling 24 h máx 3 (6 YouTube), día libre semanal determinista,
// backoff 1/5/15/60 (máx 3 reintentos/día, A4) y spam -> pausa 48 h
// sin reintento, y plan diario JSON.
// REVOCADO 2026-11-26 (SPEC quitar-franjas v2, orden del usuario):
// el espaciamiento fijo 3 h (2 h YouTube) y las franjas Chile
// 12:00-14:00 / 18:00-20:00 / 21:00-23:00 con jitter determinista
// mueren. En su lugar: azar 90–180 min entre publicaciones de la
// misma cuenta+plataforma (next_run_at) y piso de 90 min en el
// re-chequeo. spacing_hours / youtube_spacing_hours quedan en
// Ajustes como inertes (compatibilidad) y el gate ya no los lee.
import { describe, expect, test } from "bun:test";
import {
  computeMaturity,
  effectiveSettings,
  getRestDayOverrides,
  isRestDay,
  isRestDayEffective,
  MAX_PUBLISH_SPACING_MINUTES,
  MIN_PUBLISH_SPACING_MINUTES,
  randomPublishDelayMs,
  restWeekday,
  restWeekdayEffective,
  santiagoDayKeyMs,
  santiagoWallToUtcMs,
  santiagoWeekKey,
  validateSettingsInput,
  warmupStage,
  withRestDayOverride,
  type EffectiveSettings,
} from "../src/db";
import { buildDailyPlan, type DailyPlanAccountInput, type DailyPlanClipInput } from "../src/daily-plan";
import { isSpamLimitError, publishGate, RETRY_BACKOFF_MINUTES, MAX_TRANSIENT_RETRIES, type GateContext } from "../src/jobs";
import type { ClipRow } from "../src/db";

function clip(over: Partial<ClipRow> = {}): ClipRow {
  return {
    id: 1, tenant_id: "sebastian", source_id: null, influencer_id: null, title: "t", start_sec: 0, end_sec: 48,
    score: 80, status: "aprobado", subtitle_style: "simple", seo_title: "", seo_description: "",
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

describe("topes por plataforma (reglas 2026-11-19)", () => {
  test("defaults: TikTok 3/3, Instagram 3/3, YouTube 6/6, Facebook 3/3", () => {
    const s = effectiveSettings({});
    expect(s.youtube_plan).toBe(6);
    expect(s.youtube_max).toBe(6);
    expect(s.tiktok_plan).toBe(3);
    expect(s.tiktok_max).toBe(3);
    expect(s.instagram_plan).toBe(3);
    expect(s.instagram_max).toBe(3);
    expect(s.facebook_plan).toBe(3);
    expect(s.facebook_max).toBe(3);
    expect(s.spacing_hours).toBe(3);
    expect(s.youtube_spacing_hours).toBe(2); // orden 2026-11-23: YouTube 6/día con 2 h
    expect(s.min_spacing_minutes).toBe(5);
    expect(s.cross_platform_spacing_minutes).toBe(15);
    expect(s.rolling_24h_max).toBe(3);
    expect(s.rolling_24h_max_youtube).toBe(6); // orden 2026-11-23: rolling YouTube 6
    expect(s.new_account_daily).toBe(3); // orden 2026-11-21: mínimo 3 por plataforma
    expect(s.new_account_daily_youtube).toBe(6); // orden 2026-11-21 21:09: YouTube a 6
  });
  test("legacy resto_* alimenta a las 3 solo si no hay valor por plataforma", () => {
    const s = effectiveSettings({ resto_plan: 1, resto_max: 1 });
    expect(s.tiktok_plan).toBe(1);
    expect(s.instagram_plan).toBe(1);
    expect(s.facebook_plan).toBe(1);
    expect(s.tiktok_max).toBe(1);
    // YouTube no lo toca el resto_*.
    expect(s.youtube_plan).toBe(6);
    expect(s.youtube_max).toBe(6);
    const s2 = effectiveSettings({ resto_plan: 1, resto_max: 1, tiktok_plan: 2, tiktok_max: 3 });
    expect(s2.tiktok_plan).toBe(2);
    expect(s2.tiktok_max).toBe(3);
    expect(s2.instagram_plan).toBe(1);
  });
  test("legacy daily_limit_* sigue valiendo si no hay ni por-plataforma ni resto_*", () => {
    const s = effectiveSettings({ daily_limit_tiktok: 1 });
    expect(s.tiktok_max).toBe(1);
    expect(s.tiktok_plan).toBe(1);
  });
  test("validateSettingsInput acepta las claves nuevas y rechaza rangos", () => {
    expect(validateSettingsInput({ tiktok_plan: 2, tiktok_max: 3, youtube_spacing_hours: 4, rolling_24h_max: 3 })).toBeNull();
    expect(validateSettingsInput({ youtube_spacing_hours: 25 })).toContain("0 y 24");
    expect(validateSettingsInput({ tiktok_days_per_week: 8 })).toContain("0 y 7");
    expect(validateSettingsInput({ rolling_24h_max: -1 })).toContain("mayor o igual a 0");
  });
});

describe("calentamiento nuevo (<14 días o <20 publicaciones)", () => {
  const eff = effectiveSettings({}) as EffectiveSettings;
  const now = Date.now();
  const day = 86_400_000;
  test("cuenta sin publicaciones: semana 1, 3/día resto, 6/día YouTube", () => {
    const yt = computeMaturity({ first_published_at: null, published_count: 0, strikes: 0 }, "youtube", eff, now);
    expect(yt.status).toBe("nueva");
    expect(yt.stage).toBe("semana1");
    expect(yt.effective_daily_limit).toBe(6);
    expect(yt.effective_daily_plan).toBe(6);
    const tt = computeMaturity({ first_published_at: null, published_count: 0, strikes: 0 }, "tiktok", eff, now);
    expect(tt.stage).toBe("semana1");
    expect(tt.effective_daily_limit).toBe(3);
  });
  test("8 días: semana 2, sigue 3/día", () => {
    const m = computeMaturity({ first_published_at: now - 8 * day, published_count: 8, strikes: 0 }, "tiktok", eff, now);
    expect(m.stage).toBe("semana2");
    expect(m.status).toBe("nueva");
    expect(m.effective_daily_limit).toBe(3);
  });
  test("20 días pero solo 5 publicaciones: semana 3, plan normal (3/día TikTok) sin acceso al máx", () => {
    const m = computeMaturity({ first_published_at: now - 20 * day, published_count: 5, strikes: 0 }, "tiktok", eff, now);
    expect(m.stage).toBe("semana3");
    expect(m.status).toBe("nueva");
    expect(m.effective_daily_plan).toBe(3);
    expect(m.effective_daily_limit).toBe(3);
    expect(m.warmup_remaining_pubs).toBe(15);
  });
  test("14 días + 20 publicaciones: establecida, TikTok 3/día máx 3", () => {
    const m = computeMaturity({ first_published_at: now - 14 * day, published_count: 20, strikes: 0 }, "tiktok", eff, now);
    expect(m.stage).toBe("establecida");
    expect(m.status).toBe("establecida");
    expect(m.effective_daily_plan).toBe(3);
    expect(m.effective_daily_limit).toBe(3);
  });
  test("14 días + 20 publicaciones en YouTube: establecida, plan 6 máx 6", () => {
    const m = computeMaturity({ first_published_at: now - 14 * day, published_count: 20, strikes: 0 }, "youtube", eff, now);
    expect(m.status).toBe("establecida");
    expect(m.effective_daily_plan).toBe(6);
    expect(m.effective_daily_limit).toBe(6);
  });
  test("13 días + 20 publicaciones: sigue nueva (tiene que ser 14 días)", () => {
    const m = computeMaturity({ first_published_at: now - 13 * day, published_count: 20, strikes: 0 }, "tiktok", eff, now);
    expect(m.status).toBe("nueva");
    expect(m.stage).toBe("semana2");
  });
  test("con strike vuelve a 3/día aunque cumpla edad y conteo", () => {
    const m = computeMaturity({ first_published_at: now - 60 * day, published_count: 50, strikes: 1 }, "tiktok", eff, now);
    expect(m.status).toBe("nueva");
    expect(m.effective_daily_limit).toBe(3);
    expect(warmupStage(60, 50, 1)).not.toBe("establecida");
  });
});

describe("piso de 90 min y cross-platform 15 min (SPEC quitar-franjas v2, 2026-11-26)", () => {
  test("hace 30 min: TikTok y YouTube frenan (piso 90 min, igual para todas)", () => {
    const last = Date.now() - 30 * 60 * 1000;
    expect(publishGate("tiktok", gateCtx({ lastPublishedAtMs: last }))).toContain("menos de 90 min");
    expect(publishGate("youtube_shorts", gateCtx({ lastPublishedAtMs: last }))).toContain("menos de 90 min");
  });
  test("hace 100 min: TikTok y YouTube pasan (antes frenaban: 3 h y 2 h)", () => {
    const last = Date.now() - 100 * 60 * 1000;
    expect(publishGate("tiktok", gateCtx({ lastPublishedAtMs: last }))).toBeNull();
    expect(publishGate("youtube_shorts", gateCtx({ lastPublishedAtMs: last }))).toBeNull();
  });
  test("hace 2,5 h: pasa en todas (antes TikTok frenaba con 3 h)", () => {
    const last = Date.now() - 2.5 * 3600 * 1000;
    expect(publishGate("tiktok", gateCtx({ lastPublishedAtMs: last }))).toBeNull();
    expect(publishGate("youtube_shorts", gateCtx({ lastPublishedAtMs: last }))).toBeNull();
  });
  test("mismo clip en otra plataforma hace 10 min: frena; hace 20 min: pasa", () => {
    const now = Date.now();
    expect(publishGate("tiktok", gateCtx({ clipLastPublishedOtherPlatformMs: now - 10 * 60 * 1000, nowMs: now }))).toContain("otra plataforma");
    expect(publishGate("tiktok", gateCtx({ clipLastPublishedOtherPlatformMs: now - 20 * 60 * 1000, nowMs: now }))).toBeNull();
  });
  test("mismo clip hoy en la misma plataforma frena incluso a mano", () => {
    expect(publishGate("tiktok", gateCtx({ clipPublishedTodayOnPlatform: true, manual: true }))).toContain("ya se publicó hoy");
  });
});

describe("sin franjas (SPEC quitar-franjas v2, 2026-11-26: revoca las franjas 2026-11-19)", () => {
  const day = "2026-11-19";
  test("10:00, 13:00, 16:00, 19:00, 22:00 y 00:30 pasan: no hay franja que comprobar", () => {
    for (const hhmm of ["10:00", "13:00", "16:00", "19:00", "22:00", "00:30"]) {
      expect(publishGate("tiktok", gateCtx({ nowMs: santiagoWallToUtcMs(day, hhmm) }))).toBeNull();
    }
  });
  test("manual a las 10:00 (fuera de la vieja franja) TAMBIÉN pasa", () => {
    expect(publishGate("tiktok", gateCtx({ manual: true, nowMs: santiagoWallToUtcMs(day, "10:00") }))).toBeNull();
  });
  test("F0 (spec 2026-11-24, vigente sin franjas): el hueco YT 00:00 sigue alcanzable — a las 00:30 el gate pasa", () => {
    expect(publishGate("tiktok", gateCtx({ nowMs: santiagoWallToUtcMs(day, "00:30") }))).toBeNull();
    expect(publishGate("youtube_shorts", gateCtx({ nowMs: santiagoWallToUtcMs(day, "00:30") }))).toBeNull();
  });
});

describe("azar 90–180 min entre publicaciones (SPEC quitar-franjas v2, 2026-11-26)", () => {
  test("randomPublishDelayMs: límites con rng inyectado (0 → 90 min, 1 → 180 min, 0,5 → 135 min)", () => {
    expect(randomPublishDelayMs(() => 0)).toBe(90 * 60_000);
    expect(randomPublishDelayMs(() => 1)).toBe(180 * 60_000);
    expect(randomPublishDelayMs(() => 0.5)).toBe(135 * 60_000);
    expect(MIN_PUBLISH_SPACING_MINUTES).toBe(90);
    expect(MAX_PUBLISH_SPACING_MINUTES).toBe(180);
  });
  test("randomPublishDelayMs: sin rng usa Math.random y cae en [90, 180] min (100 sorteos)", () => {
    for (let i = 0; i < 100; i++) {
      const d = randomPublishDelayMs();
      expect(d).toBeGreaterThanOrEqual(90 * 60_000);
      expect(d).toBeLessThanOrEqual(180 * 60_000);
    }
  });
  test("6 YouTube al día caben en 24 h incluso con azar máximo: 5 huecos × 180 min = 15 h ≤ 24 h", () => {
    expect(5 * MAX_PUBLISH_SPACING_MINUTES).toBeLessThanOrEqual(24 * 60);
  });
  test("3 por plataforma el resto caben: 2 huecos × 180 min = 6 h ≤ 24 h", () => {
    expect(2 * MAX_PUBLISH_SPACING_MINUTES).toBeLessThanOrEqual(24 * 60);
  });
});

describe("día libre determinista", () => {
  test("día libre: mismo hash cuenta+semana siempre; isRestDay coherente", () => {
    const ms = santiagoWallToUtcMs("2026-11-19", "13:00");
    const week = santiagoWeekKey(ms);
    const wd = restWeekday("acc-7", week);
    expect(wd).toBe(restWeekday("acc-7", week));
    expect(wd).toBeGreaterThanOrEqual(0);
    expect(wd).toBeLessThan(7);
    const weekday = new Date(santiagoDayKeyMs(ms) + "T12:00:00Z").getUTCDay();
    expect(isRestDay("acc-7", ms)).toBe(weekday === wd);
  });
  test("traslado único del día libre (2026-11-20): YT ES acc-2, W47 viernes→domingo", () => {
    // Base verificada: acc-2 en 2026-W47 cae en viernes (5).
    expect(restWeekday("acc-2", "2026-W47")).toBe(5);
    const viernes = santiagoWallToUtcMs("2026-11-20", "13:00");
    const domingo = santiagoWallToUtcMs("2026-11-22", "13:00");
    expect(isRestDay("acc-2", viernes)).toBe(true);
    expect(isRestDay("acc-2", domingo)).toBe(false);
    const settings = withRestDayOverride({}, 2, "2026-W47", 0);
    expect(getRestDayOverrides(settings)).toEqual({ "2": { week: "2026-W47", weekday: 0 } });
    expect(restWeekdayEffective("acc-2", "2026-W47", settings, 2)).toBe(0);
    expect(isRestDayEffective("acc-2", viernes, settings, 2)).toBe(false);
    expect(isRestDayEffective("acc-2", domingo, settings, 2)).toBe(true);
    // Sin override, o en otra semana, el hash manda (W48 acc-2 = viernes).
    expect(restWeekdayEffective("acc-2", "2026-W47", {}, 2)).toBe(5);
    expect(restWeekdayEffective("acc-2", "2026-W48", settings, 2)).toBe(5);
    // Override inválido se ignora, no rompe el gate.
    expect(restWeekdayEffective("acc-2", "2026-W47", { rest_day_overrides: { "2": { week: "2026-W47", weekday: 9 } } }, 2)).toBe(5);
  });
});

describe("frenos duros: ni la manual los salta", () => {
  test("rolling 24 h (3) frena incluso a mano", () => {
    expect(publishGate("tiktok", gateCtx({ publishedLast24h: 3, manual: true }))).toContain("rolling 24 h");
    expect(publishGate("tiktok", gateCtx({ publishedLast24h: 2, manual: true }))).toBeNull();
  });
  test("pausa por spam frena incluso a mano", () => {
    expect(publishGate("tiktok", gateCtx({ pausedUntilMs: Date.now() + 3600_000, manual: true }))).toContain("pausa por spam");
    expect(publishGate("tiktok", gateCtx({ pausedUntilMs: Date.now() - 1000, manual: true }))).toBeNull();
  });
  test("tope de calentamiento frena incluso a mano", () => {
    expect(publishGate("tiktok", gateCtx({ publishedToday: 1, warmupDailyLimit: 1, manual: true }))).toContain("calentamiento");
    expect(publishGate("tiktok", gateCtx({ publishedToday: 0, warmupDailyLimit: 1, manual: true }))).toBeNull();
  });
  test("día libre frena la automática pero no la manual", () => {
    expect(publishGate("tiktok", gateCtx({ isRestDay: true }))).toContain("día libre");
    expect(publishGate("tiktok", gateCtx({ isRestDay: true, manual: true }))).toBeNull();
  });
});

describe("errores: backoff temporal y clasificación spam", () => {
  test("backoff 1, 5, 15, 60 min, máx 3 reintentos/día (A4)", () => {
    expect(RETRY_BACKOFF_MINUTES).toEqual([1, 5, 15, 60]);
    expect(MAX_TRANSIENT_RETRIES).toBe(3);
  });
  test("spam/límite clasifica; 429 y red no", () => {
    expect(isSpamLimitError("spam_risk_too_many_posts")).toBe(true);
    expect(isSpamLimitError("demasiadas publicaciones hoy")).toBe(true);
    expect(isSpamLimitError("cuenta restringida por la plataforma")).toBe(true);
    expect(isSpamLimitError("shadowban sospechoso")).toBe(true);
    expect(isSpamLimitError("HTTP 429 Too Many Requests")).toBe(false);
    expect(isSpamLimitError("red: Network connection lost")).toBe(false);
    expect(isSpamLimitError("HTTP 500")).toBe(false);
  });
});

describe("plan diario (GET /api/daily-plan, constructor puro)", () => {
  const eff = effectiveSettings({});
  function acc(over: Partial<DailyPlanAccountInput>): DailyPlanAccountInput {
    return {
      id: 1, platform: "tiktok", apiPlatform: "tiktok", language: "es", label: "TikTok ES",
      stage: "establecida", plan: 2, max: 3, daysSinceFirst: 40, strikes: 0,
      publishedToday: 0, publishedLast24h: 0, hasCredential: true,
      pausedUntilMs: null, restDay: false, restWeekday: 0, canEscalate: false, lastPublishedAtMs: null, ...over,
    };
  }
  function clipInput(id: number, score: number): DailyPlanClipInput {
    return {
      id, title: `Clip ${id}`, description: `Desc ${id}`,
      hashtags: ["a", "b", "c", "d", "e", "f", "g"], score, language: "es",
      platforms: ["tiktok", "instagram_reels", "facebook_reels", "youtube_shorts"],
    };
  }
  const now = santiagoWallToUtcMs("2026-11-19", "13:00");
  test("reparte por cuentas: TikTok 2, Instagram 1; pausa y día libre alertan y no programan", () => {
    const plan = buildDailyPlan({
      fecha: "2026-11-19",
      accounts: [
        acc({ id: 1 }),
        acc({ id: 2, platform: "instagram_reels", apiPlatform: "instagram", label: "IG ES", stage: "semana2", plan: 1, max: 2, daysSinceFirst: 9 }),
        acc({ id: 3, platform: "facebook_reels", apiPlatform: "facebook", label: "FB ES", restDay: true, restWeekday: 4 }),
        acc({ id: 4, platform: "youtube_shorts", apiPlatform: "youtube", label: "YT ES", pausedUntilMs: now + 3600_000 }),
      ],
      queue: [clipInput(1, 90), clipInput(2, 85), clipInput(3, 80), clipInput(4, 75)],
      eff,
      nowMs: now,
    });
    expect(plan.fecha).toBe("2026-11-19");
    const tt = plan.publicaciones.filter((p) => p.plataforma === "tiktok");
    expect(tt).toHaveLength(2);
    expect(tt.map((p) => p.clip_id)).toEqual([1, 2]);
    // 2/día: cadena aleatoria 90–180 min entre publicaciones (sin
    // franjas: ya no hay "mañana 12:xx + noche 21:xx").
    const toMin = (hhmm: string): number => {
      const [h, m] = hhmm.split(":").map(Number);
      return h * 60 + m;
    };
    const ttMin = tt.map((p) => toMin(p.hora_local)).sort((a, b) => a - b);
    const gap = ttMin[1] - ttMin[0];
    expect(gap).toBeGreaterThanOrEqual(90);
    expect(gap).toBeLessThanOrEqual(180);
    const ig = plan.publicaciones.filter((p) => p.plataforma === "instagram");
    expect(ig).toHaveLength(1);
    expect(ig[0]?.clip_id).toBe(3);
    expect(plan.publicaciones.filter((p) => p.plataforma === "facebook")).toHaveLength(0);
    expect(plan.publicaciones.filter((p) => p.plataforma === "youtube")).toHaveLength(0);
    expect(plan.en_cola).toEqual([4]);
    expect(plan.alertas.join(" ")).toContain("semana 2");
    expect(plan.alertas.join(" ")).toContain("día libre");
    expect(plan.alertas.join(" ")).toContain("pausa");
    // Hashtags capados a 5.
    for (const p of plan.publicaciones) expect(p.hashtags.length).toBeLessThanOrEqual(5);
    // Ningún clip repetido en la misma plataforma.
    const keys = plan.publicaciones.map((p) => `${p.clip_id}|${p.plataforma}`);
    expect(new Set(keys).size).toBe(keys.length);
  });
  test("a ritmo máximo (establecida + >30 días + viral alto): TikTok llega a 3", () => {
    const plan = buildDailyPlan({
      fecha: "2026-11-19",
      accounts: [acc({ id: 1, canEscalate: true })],
      queue: [clipInput(1, 90), clipInput(2, 85), clipInput(3, 80), clipInput(4, 75)],
      eff,
      nowMs: now,
    });
    expect(plan.publicaciones).toHaveLength(3);
    expect(plan.en_cola).toEqual([4]);
  });
  test("sin credencial o sin capacidad no programa y alerta", () => {
    const plan = buildDailyPlan({
      fecha: "2026-11-19",
      accounts: [acc({ id: 1, hasCredential: false }), acc({ id: 2, publishedToday: 2, publishedLast24h: 2 })],
      queue: [clipInput(1, 90)],
      eff,
      nowMs: now,
    });
    expect(plan.publicaciones).toHaveLength(0);
    expect(plan.en_cola).toEqual([1]);
    expect(plan.alertas.join(" ")).toContain("sin credencial");
  });
});
