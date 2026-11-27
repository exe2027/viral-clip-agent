// Tests de Ajustes editables (2026-11-18) + escalado automático de
// topes por madurez de la cuenta (0017) + verificación OAuth JSON de
// YouTube. Lógica pura (sin red real: fetch falso) y gates.
import { describe, expect, test, afterEach } from "bun:test";
import { computeMaturity, effectiveSettings, maturityOut, validateSettingsInput, type ApiAccountRow, type EffectiveSettings } from "../src/db";
import { publishGate, type GateContext } from "../src/jobs";
import type { ClipRow } from "../src/db";
import { verifyCredential } from "../src/verify";

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

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

describe("settings efectivos (Ajustes)", () => {
  test("defaults = reglas del usuario", () => {
    const s = effectiveSettings({});
    expect(s.layout).toBe("rdjavi");
    expect(s.subtitle_style).toBe("ref");
    expect(s.min_duration_s).toBe(1); // sin mínimo desde 2026-11-21
    expect(s.max_duration_s).toBe(60);
    expect(s.min_virality).toBe(45); // puerta única 45/80 (P0 #3)
    expect(s.min_seo).toBe(80);
    expect(s.youtube_plan).toBe(6); // orden 2026-11-21 21:09: YouTube a 6
    expect(s.youtube_max).toBe(6);
    expect(s.tiktok_plan).toBe(3);
    expect(s.tiktok_max).toBe(3);
    expect(s.instagram_plan).toBe(3); // orden 2026-11-21 21:09: IG/FB a 3
    expect(s.instagram_max).toBe(3);
    expect(s.facebook_plan).toBe(3);
    expect(s.facebook_max).toBe(3);
    expect(s.resto_plan).toBe(2);
    expect(s.resto_max).toBe(3);
    expect(s.new_account_daily).toBe(3); // orden 2026-11-21: mínimo 3 por plataforma
    expect(s.new_account_daily_youtube).toBe(6); // orden 2026-11-21 21:09: YouTube a 6
    expect(s.tiktok_zernio_daily).toBe(15);
    expect(s.tiktok_zernio_pending_drafts).toBe(5);
    expect(s.spacing_hours).toBe(3);
    expect(s.youtube_spacing_hours).toBe(2); // orden 2026-11-23: YouTube 6/día con 2 h
    expect(s.rolling_24h_max).toBe(3);
    expect(s.rolling_24h_max_youtube).toBe(6); // orden 2026-11-23: rolling YouTube 6
    expect(s.hype_burst).toBe(true);
    expect(s.game).toBe("GTA VI");
    expect(s.thumbnail_style).toBe("gta6");
  });
  test("aliases legacy: split -> plain, simple -> ref", () => {
    const s = effectiveSettings({ layout: "split", subtitle_style: "simple" });
    expect(s.layout).toBe("plain");
    expect(s.subtitle_style).toBe("ref");
  });
  test("el plan nunca supera el techo", () => {
    const s = effectiveSettings({ youtube_plan: 5, youtube_max: 1 });
    expect(s.youtube_plan).toBe(1);
    expect(s.youtube_max).toBe(1);
  });
  test("validateSettingsInput rechaza min>=max y rangos", () => {
    expect(validateSettingsInput({ min_duration_s: 60, max_duration_s: 40 })).toContain("mayor");
    expect(validateSettingsInput({ min_virality: 101 })).toContain("0 y 100");
    expect(validateSettingsInput({ min_seo: -1 })).toContain("0 y 100");
    expect(validateSettingsInput({ youtube_max: -1 })).toContain("mayor o igual a 0");
    expect(validateSettingsInput({ layout: "otro" })).toContain("rdjavi o plain");
    expect(validateSettingsInput({ hype_burst: "si" })).toContain("true o false");
    expect(validateSettingsInput({ min_duration_s: 40, max_duration_s: 60, min_virality: 55 })).toBeNull();
  });
});

describe("escalado automático de topes (madurez)", () => {
  const eff = effectiveSettings({}) as EffectiveSettings;
  const now = Date.now();
  const day = 86_400_000;
  test("cuenta sin publicaciones: nueva, semana 1; YouTube 6/día, resto 3/día (2026-11-21)", () => {
    const yt = computeMaturity({ first_published_at: null, published_count: 0, strikes: 0 }, "youtube", eff, now);
    expect(yt.status).toBe("nueva");
    expect(yt.stage).toBe("semana1");
    expect(yt.effective_daily_limit).toBe(6);
    expect(yt.effective_daily_plan).toBe(6);
    expect(yt.days_since_first_published).toBeNull();
    const tt = computeMaturity({ first_published_at: null, published_count: 0, strikes: 0 }, "tiktok", eff, now);
    expect(tt.status).toBe("nueva");
    expect(tt.effective_daily_limit).toBe(3);
    expect(tt.effective_daily_plan).toBe(3);
  });
  test("14 días + 20 publicaciones: establecida, YT plan 6 máx 6", () => {
    const m = computeMaturity({ first_published_at: now - 14 * day, published_count: 20, strikes: 0 }, "youtube", eff, now);
    expect(m.status).toBe("establecida");
    expect(m.stage).toBe("establecida");
    expect(m.days_since_first_published).toBe(14);
    expect(m.effective_daily_limit).toBe(6);
    expect(m.effective_daily_plan).toBe(6);
  });
  test("14 días + 20 publicaciones en resto: establecida, 3/día máx 3", () => {
    const m = computeMaturity({ first_published_at: now - 14 * day, published_count: 20, strikes: 0 }, "tiktok", eff, now);
    expect(m.status).toBe("establecida");
    expect(m.effective_daily_limit).toBe(3);
    expect(m.effective_daily_plan).toBe(3);
  });
  test("exactamente 13 días: sigue nueva (tiene que ser 14 días)", () => {
    const m = computeMaturity({ first_published_at: now - 13 * day, published_count: 20, strikes: 0 }, "youtube", eff, now);
    expect(m.status).toBe("nueva");
  });
  test("14 días pero solo 19 publicaciones: sigue nueva (semana 3, plan sin máx)", () => {
    const m = computeMaturity({ first_published_at: now - 14 * day, published_count: 19, strikes: 0 }, "youtube", eff, now);
    expect(m.status).toBe("nueva");
    expect(m.stage).toBe("semana3");
  });
  test("con strike vuelve a nueva (3/día resto, 6/día YT) aunque cumpla edad y conteo", () => {
    const tt = computeMaturity({ first_published_at: now - 60 * day, published_count: 50, strikes: 1 }, "tiktok", eff, now);
    expect(tt.status).toBe("nueva");
    expect(tt.effective_daily_limit).toBe(3);
    const yt = computeMaturity({ first_published_at: now - 60 * day, published_count: 50, strikes: 1 }, "youtube", eff, now);
    expect(yt.status).toBe("nueva");
    expect(yt.effective_daily_limit).toBe(6);
  });
});

// Fila Meta + platformOverride (SPEC piso-3-por-dia v2 §4): la
// fila 'meta' sirve a Instagram y a Facebook; sin override su
// plan cae en resto_plan=2 (el bug que dejaba IG/FB en 2/día).
// Con override, el plan/máx se resuelven con la plataforma de
// panel que se evalúa.
describe("maturityOut con fila Meta (piso 3/día §4)", () => {
  const eff = effectiveSettings({}) as EffectiveSettings;
  const now = Date.now();
  const day = 86_400_000;
  function metaRow(over: Partial<ApiAccountRow> = {}): ApiAccountRow {
    return {
      id: 12, tenant_id: "sebastian", platform: "meta", language: "es", label: "Meta ES",
      credential: "", credential_enc: "v1:x", extra: "", extra_enc: "", status: "conectada", note: "",
      created_at: 1, verified_at: 2000, verify_ok: 1, verify_message: "",
      first_published_at: null, published_count: 0, strikes: 0, maturity: "nueva", maturity_checked_day: null,
      ...over,
    };
  }
  test("fila meta sin override: plan 2 (resto) — el bug de §0", () => {
    const m = maturityOut(metaRow(), eff, now);
    expect(m.effective_daily_plan).toBe(2);
  });
  test("fila meta + canal instagram_reels: plan 3 (no 2)", () => {
    const m = maturityOut(metaRow(), eff, now, "instagram");
    expect(m.effective_daily_plan).toBe(3);
    expect(m.effective_daily_limit).toBe(3);
  });
  test("fila meta + canal facebook_reels: plan 3", () => {
    const m = maturityOut(metaRow(), eff, now, "facebook");
    expect(m.effective_daily_plan).toBe(3);
    expect(m.effective_daily_limit).toBe(3);
  });
  test("fila youtube → 6 y fila tiktok → 3 (sin cambio)", () => {
    const yt = maturityOut(metaRow({ platform: "youtube" }), eff, now, "youtube");
    expect(yt.effective_daily_plan).toBe(6);
    const tt = maturityOut(metaRow({ platform: "tiktok" }), eff, now, "tiktok");
    expect(tt.effective_daily_plan).toBe(3);
  });
  test("fila meta ya establecida + canal instagram: plan 3 y etapa establecida (regresión de §0)", () => {
    const row = metaRow({ first_published_at: now - 14 * day, published_count: 20, maturity: "establecida" });
    const m = maturityOut(row, eff, now, "instagram");
    expect(m.status).toBe("establecida");
    expect(m.effective_daily_plan).toBe(3);
  });
});

describe("publishGate lee Ajustes y madurez", () => {
  test("cuenta nueva: el 2º del día frena aunque el plan diga 3", () => {
    const g = gateCtx({ publishedToday: 1, accountDailyLimit: 1, accountDailyPlan: 1 });
    expect(publishGate("youtube_shorts", g)).toContain("tope diario alcanzado");
  });
  test("cuenta establecida YT: 0 del día pasa, 1 cubre el plan (plan 1, techo 2)", () => {
    expect(publishGate("youtube_shorts", gateCtx({ publishedToday: 0, accountDailyLimit: 2, accountDailyPlan: 1 }))).toBeNull();
    expect(publishGate("youtube_shorts", gateCtx({ publishedToday: 1, accountDailyLimit: 2, accountDailyPlan: 1 }))).toContain("plan del día cubierto");
  });
  test("piso de 90 min: spacing_hours ya no manda (inerte desde 2026-11-26)", () => {
    const last = Date.now() - 30 * 60 * 1000; // hace 30 min
    expect(publishGate("tiktok", gateCtx({ lastPublishedAtMs: last, settings: { spacing_hours: 0 } }))).toContain("menos de 90 min");
    expect(publishGate("tiktok", gateCtx({ lastPublishedAtMs: last, settings: { spacing_hours: 2 } }))).toContain("menos de 90 min");
    const old = Date.now() - 100 * 60 * 1000; // hace 100 min: fuera del piso
    expect(publishGate("tiktok", gateCtx({ lastPublishedAtMs: old, settings: { spacing_hours: 2 } }))).toBeNull();
  });
  test("viralidad/SEO mínimos desde settings", () => {
    expect(publishGate("youtube_shorts", gateCtx({ seoScore: 89, settings: { min_seo: 90 } }))).toContain("bajo la puerta");
    expect(publishGate("youtube_shorts", gateCtx({ seoScore: 89, settings: { min_seo: 80 } }))).toBeNull();
  });
});

describe("verify YouTube con JSON OAuth", () => {
  function stubFetch(responses: Array<{ status: number; body: unknown }>) {
    const queue = [...responses];
    globalThis.fetch = (async (input: string | URL | Request) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      void url;
      const next = queue.shift() ?? { status: 500, body: {} };
      return new Response(JSON.stringify(next.body), { status: next.status, headers: { "Content-Type": "application/json" } });
    }) as typeof fetch;
  }
  const OAUTH_JSON = JSON.stringify({
    token: "ya29.viejo", refresh_token: "1//refresh-falso", client_id: "id-falso",
    client_secret: "sec.falso", scopes: ["https://www.googleapis.com/auth/youtube.upload"],
  });
  test("JSON con refresh_token: renueva y comprueba youtube.upload", async () => {
    stubFetch([
      { status: 200, body: { access_token: "ya29.nuevo", expires_in: 3600 } },
      { status: 200, body: { scope: "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube" } },
    ]);
    const out = await verifyCredential("youtube", OAUTH_JSON, {});
    expect(out.ok).toBe(true);
    expect(out.message).toContain("youtube.upload");
  });
  test("JSON con refresh_token inválido: error limpio, no crash", async () => {
    stubFetch([{ status: 400, body: { error: "invalid_grant" } }]);
    const out = await verifyCredential("youtube", OAUTH_JSON, {});
    expect(out.ok).toBe(false);
    expect(out.message).toContain("reautoriza");
  });
  test("JSON sin refresh_token y sin scope upload: rechaza", async () => {
    stubFetch([
      { status: 200, body: { scope: "https://www.googleapis.com/auth/youtube.readonly" } },
    ]);
    const out = await verifyCredential("youtube", JSON.stringify({ token: "ya29.x" }), {});
    expect(out.ok).toBe(false);
  });
});
