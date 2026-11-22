// Tests del Worker (bun test test/). Sin red, sin D1: cubren la
// lógica pura (cifrado, idempotencia, gates). Los adaptadores de
// subida se prueban contra las APIs reales solo con credenciales
// del usuario (no aquí).
import { describe, expect, test } from "bun:test";
import { createSessionCookie, hashPassword, verifyPassword, verifySessionCookie } from "../src/auth";
import { decryptCredential, deriveTenantKey, encryptCredential, maskCredential } from "../src/crypto";
import { idempotencyKey, publishGate, santiagoDayKey, type GateContext } from "../src/jobs";
import type { ClipRow } from "../src/db";

const KEY = Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"); // 32 bytes, SOLO TEST

function clip(over: Partial<ClipRow> = {}): ClipRow {
  return {
    id: 1, tenant_id: "sebastian", source_id: null, influencer_id: null, title: "t", start_sec: 0, end_sec: 40,
    score: 80, status: "aprobado", subtitle_style: "tiktok", seo_title: "", seo_description: "",
    seo_tags: "", platforms: '["youtube_shorts"]', scheduled_for: null, published_at: null,
    file_deleted: 0, created_at: Date.now(), updated_at: Date.now(), captions: "{}",
    viral: '{"platforms":{"youtube_shorts":{"score":95}}}', seo_overrides: "[]",
    preview_seen: 1, archived: 0, origin: "vod", publish_states: "{}", game: "GTA VI",
    publish_blocked: 0, needs_human_transcription: 0, subtitles_complete: 0,
    ...over,
  } as ClipRow;
}

function gateCtx(over: Partial<GateContext> = {}): GateContext {
  return {
    clip: clip(), seoScore: 95, seoOverride: false, publishedToday: 0,
    lastPublishedAtMs: null, streamerAllowed: true, settings: {}, ...over,
  };
}

describe("cifrado de credenciales (AES-256-GCM)", () => {
  test("roundtrip", async () => {
    const enc = await encryptCredential("AIzaFALSO-1234", KEY);
    expect(enc.startsWith("v1:")).toBe(true);
    expect(await decryptCredential(enc, KEY)).toBe("AIzaFALSO-1234");
  });
  test("dos cifrados del mismo valor difieren (IV aleatorio)", async () => {
    const a = await encryptCredential("x", KEY);
    const b = await encryptCredential("x", KEY);
    expect(a).not.toBe(b);
  });
  test("máscara solo muestra los últimos 4", () => {
    expect(maskCredential("AIzaFALSO-1234")).toBe("****1234");
    expect(maskCredential("")).toBe("");
  });
});

describe("cifrado por tenant (instalación única avc)", () => {
  test("cada tenant descifra solo lo suyo", async () => {
    const enc = await encryptCredential("token-sebastian", KEY, "sebastian");
    expect(await decryptCredential(enc, KEY, "sebastian")).toBe("token-sebastian");
    await expect(decryptCredential(enc, KEY, "kaez")).rejects.toThrow();
    const encKaez = await encryptCredential("token-kaez", KEY, "kaez");
    expect(await decryptCredential(encKaez, KEY, "kaez")).toBe("token-kaez");
    await expect(decryptCredential(encKaez, KEY, "sebastian")).rejects.toThrow();
  });
  test("la clave derivada difiere por tenant", async () => {
    const a = await deriveTenantKey(KEY, "sebastian");
    const b = await deriveTenantKey(KEY, "kaez");
    expect(a).not.toBe(b);
  });
  test("sin tenant, compatibilidad con el formato anterior", async () => {
    const enc = await encryptCredential("x", KEY);
    expect(await decryptCredential(enc, KEY)).toBe("x");
  });
});

describe("sesión por tenant (un Worker = un tenant)", () => {
  const SECRET = Buffer.from("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa").toString("base64");
  test("la sesión lleva usuario y tenant, y se verifica", async () => {
    const cookie = await createSessionCookie("sebastian", "sebastian", SECRET);
    const session = await verifySessionCookie(cookie, SECRET, "sebastian");
    expect(session?.sub).toBe("sebastian");
    expect(session?.tenant).toBe("sebastian");
  });
  test("una sesión válida del otro tenant no vale en este Worker", async () => {
    const cookie = await createSessionCookie("kaez", "kaez", SECRET);
    expect(await verifySessionCookie(cookie, SECRET, "sebastian")).toBeNull();
    expect((await verifySessionCookie(cookie, SECRET, "kaez"))?.tenant).toBe("kaez");
  });
  test("una sesión con tenant inventado no vale", async () => {
    // Cookie firmada a mano con tenant fuera de la lista.
    const { b64urlEncode } = await import("../src/crypto");
    const body = b64urlEncode(new TextEncoder().encode(JSON.stringify({ sub: "x", tenant: "otro", exp: Date.now() + 1000 })));
    const key = await crypto.subtle.importKey("raw", Buffer.from(SECRET, "base64"), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
    const value = `${body}.${b64urlEncode(new Uint8Array(sig))}`;
    expect(await verifySessionCookie(`panel-session=${value}`, SECRET, "sebastian")).toBeNull();
  });
});

describe("login por contraseña (PBKDF2)", () => {
  test("roundtrip y rechazo", async () => {
    const hash = await hashPassword("contraseña-de-prueba");
    expect(await verifyPassword("contraseña-de-prueba", hash)).toBe(true);
    expect(await verifyPassword("otra", hash)).toBe(false);
  });
});

describe("anti-duplicado", () => {
  test("misma clave para mismo clip/plataforma/cuenta/día", async () => {
    const a = await idempotencyKey(7, "tiktok", 3, "2026-11-17");
    const b = await idempotencyKey(7, "tiktok", 3, "2026-11-17");
    expect(a).toBe(b);
    expect(a).toHaveLength(64);
  });
  test("cambia con la plataforma o el día", async () => {
    const a = await idempotencyKey(7, "tiktok", 3, "2026-11-17");
    const b = await idempotencyKey(7, "youtube_shorts", 3, "2026-11-17");
    const c = await idempotencyKey(7, "tiktok", 3, "2026-11-18");
    expect(a).not.toBe(b);
    expect(a).not.toBe(c);
  });
  test("día de Santiago con formato YYYY-MM-DD", () => {
    expect(santiagoDayKey(new Date("2026-11-17T12:00:00-03:00"))).toBe("2026-11-17");
  });
});

describe("gates de publicación", () => {
  test("SEO bajo 80 frena sin override (puerta única 45/80, P0 #3)", () => {
    const reason = publishGate("youtube_shorts", gateCtx({ seoScore: 79 }));
    expect(reason).toMatch(/SEO 79/);
  });
  test("SEO bajo 80 con override pasa el gate SEO", () => {
    const reason = publishGate("youtube_shorts", gateCtx({ seoScore: 79, seoOverride: true }));
    expect(reason === null || !reason.includes("SEO")).toBe(true);
  });
  test("tope diario frena", () => {
    const reason = publishGate("youtube_shorts", gateCtx({ publishedToday: 6 }));
    expect(reason).toMatch(/tope diario/);
  });
  test("preview opcional (v3.1): sin preview NO frena por defecto; con require_preview activado sí", () => {
    // Por defecto (require_preview false): el preview no visto no frena.
    // B4: el clip lleva veredicto de calidad aprobado, así que lo
    // que se prueba aquí es solo el preview, no la puerta de calidad.
    const validated = '{"platforms":{"youtube_shorts":{"score":95}},"quality_gate":{"decision":"aprobado","pasa":true,"fallos":[],"motivo":""}}';
    expect(publishGate("youtube_shorts", gateCtx({ clip: clip({ preview_seen: 0, viral: validated }) }))).toBe(null);
    // Con la casilla activada en Ajustes: vuelve el comportamiento viejo.
    const reason = publishGate("youtube_shorts", gateCtx({ clip: clip({ preview_seen: 0, viral: validated }), settings: { require_preview: true } }));
    expect(reason).toMatch(/preview/);
    // Con la casilla activada y el preview visto: pasa.
    expect(publishGate("youtube_shorts", gateCtx({ clip: clip({ preview_seen: 1, viral: validated }), settings: { require_preview: true } }))).toBe(null);
  });
  test("sin permiso del streamer frena", () => {
    const reason = publishGate("youtube_shorts", gateCtx({ streamerAllowed: false }));
    expect(reason).toMatch(/permiso/);
  });
  test("sin subtítulos completos frena (regla 2026-11-17)", () => {
    const reason = publishGate("youtube_shorts", gateCtx({ clip: clip({ publish_blocked: 1, needs_human_transcription: 1 }) }));
    expect(reason).toMatch(/subtítulos/);
  });
  // Regla 40-60 / 25-35 ELIMINADA 2026-11-21: 25, 30 y 35 s ya
  // no frenan por duración.
  test("sin mínimo ni rango prohibido: clip de 25, 30 y 35 s no frena por duración", () => {
    for (const end of [25, 30, 35]) {
      const reason = publishGate("youtube_shorts", gateCtx({ clip: clip({ end_sec: end }) }));
      expect(reason === null || (!reason.includes("duración") && !reason.includes("regla de oro"))).toBe(true);
    }
  });
  test("duración fuera de 1–60 frena (70 s); 20 s y 38 s ya pasan", () => {
    expect(publishGate("youtube_shorts", gateCtx({ clip: clip({ end_sec: 20 }) }))).toBe(null);
    expect(publishGate("youtube_shorts", gateCtx({ clip: clip({ end_sec: 38 }) }))).toBe(null);
    expect(publishGate("youtube_shorts", gateCtx({ clip: clip({ end_sec: 70 }) }))).toMatch(/1–60/);
  });
  test("ajustes: duración, SEO y topes se leen de Ajustes (no de valores fijos)", () => {
    const base = { clip: clip({ start_sec: 100, end_sec: 150 }) }; // 50 s
    // Duración 45–55 desde Ajustes: un clip de 40 s frena, uno de 50 pasa.
    expect(publishGate("youtube_shorts", gateCtx({ ...base, clip: clip({ start_sec: 100, end_sec: 140 }), settings: { min_duration_s: 45, max_duration_s: 55 } }))).toMatch(/fuera de 45/);
    expect(publishGate("youtube_shorts", gateCtx({ ...base, settings: { min_duration_s: 45, max_duration_s: 55 } }))).toBe(null);
    // SEO mínimo 80: un SEO de 85 pasa (con la puerta 80 por defecto, un SEO de 75 frenaría).
    expect(publishGate("youtube_shorts", gateCtx({ ...base, seoScore: 85, settings: { min_seo: 80 } }))).toBe(null);
    expect(publishGate("youtube_shorts", gateCtx({ ...base, seoScore: 75 }))).toMatch(/bajo la puerta de 80/);
    // Tope diario TikTok 1 desde Ajustes: con 1 publicado hoy, frena.
    expect(publishGate("tiktok", gateCtx({ ...base, publishedToday: 1, settings: { daily_limit_tiktok: 1 } }))).toMatch(/tope diario/);
    expect(publishGate("tiktok", gateCtx({ ...base, publishedToday: 0, settings: { daily_limit_tiktok: 1 } }))).toBe(null);
    // Sin rango prohibido desde 2026-11-21: con mínimo 20 en
    // Ajustes, un clip de 30 s pasa.
    expect(publishGate("youtube_shorts", gateCtx({ clip: clip({ start_sec: 0, end_sec: 30 }), settings: { min_duration_s: 20, max_duration_s: 60 } }))).toBe(null);
  });
  test("categoría: solo GTA VI pasa; GTA V o sin categoría frena", () => {
    expect(publishGate("youtube_shorts", gateCtx({ clip: clip({ game: "GTA V" }) }))).toMatch(/categoría/);
    expect(publishGate("youtube_shorts", gateCtx({ clip: clip({ game: "" }) }))).toMatch(/categoría/);
    expect(publishGate("youtube_shorts", gateCtx({ clip: clip({ game: "GTA VI" }) }))).toBe(null);
  });
  test("duración 1–60 no frena por duración", () => {
    for (const end of [25, 30, 40, 50, 60]) {
      const reason = publishGate("youtube_shorts", gateCtx({ clip: clip({ end_sec: end }) }));
      expect(reason === null || (!reason.includes("duración") && !reason.includes("regla de oro"))).toBe(true);
    }
  });
});
