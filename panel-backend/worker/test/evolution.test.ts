// Tests de evolución neuroevolutiva (0042, 2026-11-23, v1.4.0).
// Contra SQLite REAL (bun:sqlite, mismo motor que D1): las
// migraciones 0041+0042 se aplican desde el archivo y las rutas se
// prueban con el handler de verdad (worker.fetch) + sesión Bearer <redacted>.
// Cubren: población offline (control + mutantes con corte distinto
// real), selección de ganadoras (el fitness REAL manda sobre el
// cerebro), perdedoras que NO se publican, un post = una campaña,
// piso neto $1/1k + fama ≥ 50.000, y que sin datos reales NO se
// inventa fitness (las mutantes quedan candidatas, no perdedoras).
import { describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { D1Database, Env } from "../src/cloudflare";
import { createSessionToken } from "../src/auth";
import worker from "../src/index";
import { SqliteStmt } from "./budget-test-db";
import {
  createCampaignSubmission,
  createEvoGeneration,
  createEvoVariant,
  evoStatusSummary,
  getEvoVariant,
  listEvoVariants,
  recordEvoFitness,
} from "../src/db";
import {
  EVO_POPULATION_SIZE,
  generatePopulation,
  hookFirstTitle,
  isDistinctCut,
  publishBlockReason,
  realFitness,
  selectWinners,
  shortTitle,
  type EvoBaseClip,
  type EvoScoredVariant,
  type EvoVariantParams,
} from "../src/evolution";

const NOW = 1795300000000; // 2026-11-21 ~21:06 -03, fijo
const SECRET = Buffer.from("dddddddddddddddddddddddddddddddd").toString("base64"); // SOLO TEST
const DASGASD3M = "051002dd-5c46-43d0-a446-43f2957ba3a9";
const ARTHURTV = "6e9ca548-f36c-42c2-abc0-61f89b4b53ba";

const BASE: EvoBaseClip = {
  start_sec: 100,
  end_sec: 140,
  title: "Dasgasd3m se ríe: el mejor momento de GTA 6",
  seo_title: "Dasgasd3m se ríe: el mejor momento de GTA 6",
  thumb_text: "SE RÍE SIN PARAR",
  subtitle_style: "rubius",
};

const BASE_PARAMS: EvoVariantParams = {
  start_sec: 100,
  end_sec: 140,
  hook_offset_s: 0,
  title: "Dasgasd3m se ríe: el mejor momento de GTA 6",
  thumb_text: "SE RÍE SIN PARAR",
  subtitle_style: "rubius",
};

function makeDb(): { d1: D1Database; raw: Database } {
  const raw = new Database(":memory:");
  raw.exec(readFileSync(join(import.meta.dir, "..", "..", "migrations", "0041_campaign_submissions.sql"), "utf8"));
  raw.exec(readFileSync(join(import.meta.dir, "..", "..", "migrations", "0042_evolution.sql"), "utf8"));
  raw.exec(
    "CREATE TABLE clips (" +
      "id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, source_id INTEGER, influencer_id INTEGER, " +
      "title TEXT NOT NULL DEFAULT '', start_sec REAL NOT NULL DEFAULT 0, end_sec REAL NOT NULL DEFAULT 0, " +
      "score REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'en_carpeta', subtitle_style TEXT NOT NULL DEFAULT 'rubius', " +
      "seo_title TEXT NOT NULL DEFAULT '', seo_description TEXT NOT NULL DEFAULT '', seo_tags TEXT NOT NULL DEFAULT '', " +
      "thumb_text TEXT NOT NULL DEFAULT '', platforms TEXT NOT NULL DEFAULT '[]', scheduled_for TEXT, published_at INTEGER, " +
      "file_deleted INTEGER NOT NULL DEFAULT 0, r2_delete_after INTEGER, archived INTEGER NOT NULL DEFAULT 0, " +
      "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, captions TEXT NOT NULL DEFAULT '', viral TEXT NOT NULL DEFAULT '', " +
      "seo_overrides TEXT NOT NULL DEFAULT '', preview_seen INTEGER NOT NULL DEFAULT 0, origin TEXT NOT NULL DEFAULT '', " +
      "publish_states TEXT NOT NULL DEFAULT '{}', language TEXT NOT NULL DEFAULT 'es')",
  );
  raw.exec(
    "CREATE TABLE influencers (" +
      "id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, platform TEXT NOT NULL DEFAULT '', " +
      "profile_url TEXT NOT NULL DEFAULT '', language TEXT NOT NULL DEFAULT 'es', permission INTEGER NOT NULL DEFAULT 0, " +
      "active INTEGER NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, " +
      "game TEXT NOT NULL DEFAULT '', permission_date TEXT NOT NULL DEFAULT '', publish_platforms TEXT NOT NULL DEFAULT '')",
  );
  raw.exec("CREATE TABLE settings (tenant_id TEXT PRIMARY KEY, data TEXT NOT NULL DEFAULT '{}')");
  const d1 = {
    prepare: (sql: string) => new SqliteStmt(raw, sql),
    batch: async (stmts: SqliteStmt[]) => {
      const out = [];
      for (const s of stmts) out.push(await s.run());
      return out;
    },
  } as unknown as D1Database;
  return { d1, raw };
}

function insertInfluencer(raw: Database, id: number, name: string): void {
  raw.query(
    "INSERT INTO influencers (id, tenant_id, name, platform, profile_url, language, permission, active, notes, created_at, game, permission_date, publish_platforms) " +
      "VALUES (?, 'sebastian', ?, 'twitch', '', 'en', 1, 1, '', ?, 'GTA VI', '2026-11-23', '')",
  ).run(id, name, NOW);
}

function insertClip(raw: Database, id: number, influencerId: number, viral: string, publishStates = "{}", language = "es"): void {
  raw.query(
    "INSERT INTO clips (id, tenant_id, source_id, influencer_id, title, start_sec, end_sec, score, status, subtitle_style, " +
      "seo_title, seo_description, seo_tags, thumb_text, platforms, scheduled_for, published_at, file_deleted, r2_delete_after, " +
      "archived, created_at, updated_at, captions, viral, seo_overrides, preview_seen, origin, publish_states, language) " +
      "VALUES (?, 'sebastian', NULL, ?, 'Dasgasd3m se ríe', 100, 140, 70, 'en_carpeta', 'rubius', " +
      "'Dasgasd3m se ríe: el mejor momento de GTA 6', '', '', 'SE RÍE SIN PARAR', '[\"tiktok\"]', NULL, NULL, 0, NULL, " +
      "0, ?, ?, '', ?, '', 0, '', ?, ?)",
  ).run(id, influencerId, NOW, NOW, viral, publishStates, language);
}

const VIRAL_OK = JSON.stringify({
  viral_score: 70,
  seo_scores: { tiktok: 90, youtube_shorts: 88 },
  quality_gate: { decision: "aprobado", pasa: true, fallos: [] },
});
const VIRAL_NO_SCORE = JSON.stringify({
  seo_scores: { tiktok: 90 },
  quality_gate: { decision: "aprobado", pasa: true, fallos: [] },
});

function makeTestEnv(d1: D1Database): Env {
  return {
    DB: d1,
    TENANT: "sebastian",
    CLIPS_BUCKET: {} as Env["CLIPS_BUCKET"],
    SESSION_SECRET: SECRET,
    CREDENTIAL_ENCRYPTION_KEY: Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"),
    BRIDGE_TOKEN: "bridge-test",
    GITHUB_TOKEN: "gh-test",
    ALLOWED_EMAIL: "",
    GITHUB_REPO: "exe2027/viral-clip-agent",
  } as Env;
}

async function callApi(env: Env, method: string, path: string, body?: Record<string, unknown>): Promise<{ status: number; json: Record<string, unknown> }> {
  const token = await createSessionToken("sebastian", "sebastian", SECRET);
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await worker.fetch(
    new Request(`https://worker.test${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
    env,
    { waitUntil: () => {}, passThroughOnException: () => {} } as never,
  );
  return { status: res.status, json: (await res.json()) as Record<string, unknown> };
}

describe("evolution.ts puro: población offline, mutaciones pequeñas", () => {
  test("6 variantes: control exacto + 5 mutantes con corte distinto real y duración 15-60 s", () => {
    const pop = generatePopulation(BASE, 1, 12345);
    expect(pop.length).toBe(EVO_POPULATION_SIZE);
    expect(pop[0]?.is_control).toBe(true);
    expect(pop[0]?.params).toEqual(BASE_PARAMS);
    expect(pop[0]?.mutations).toEqual([]);
    for (const d of pop.slice(1)) {
      expect(d.is_control).toBe(false);
      expect(isDistinctCut(BASE_PARAMS, d.params)).toBe(true);
      const dur = d.params.end_sec - d.params.start_sec;
      expect(dur).toBeGreaterThanOrEqual(15);
      expect(dur).toBeLessThanOrEqual(60);
      expect(d.params.subtitle_style).toBe("rubius"); // la plantilla no muta
      expect(d.mutations.length).toBeGreaterThanOrEqual(1);
      expect(d.mutations.length).toBeLessThanOrEqual(2); // mutaciones PEQUEÑAS
    }
  });

  test("determinista: misma semilla, misma población; otra semilla, otra", () => {
    const a = generatePopulation(BASE, 1, 777);
    const b = generatePopulation(BASE, 1, 777);
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
    const c = generatePopulation(BASE, 1, 778);
    expect(JSON.stringify(c)).not.toBe(JSON.stringify(a));
  });

  test("isDistinctCut: solo título/miniatura NO es clip distinto; ±1 s sí", () => {
    expect(isDistinctCut(BASE_PARAMS, { ...BASE_PARAMS, title: "OTRO TÍTULO" })).toBe(false);
    expect(isDistinctCut(BASE_PARAMS, { ...BASE_PARAMS, thumb_text: "OTRO" })).toBe(false);
    expect(isDistinctCut(BASE_PARAMS, { ...BASE_PARAMS, start_sec: 101 })).toBe(true);
    expect(isDistinctCut(BASE_PARAMS, { ...BASE_PARAMS, end_sec: 139 })).toBe(true);
    expect(isDistinctCut(BASE_PARAMS, { ...BASE_PARAMS, start_sec: 100.5 })).toBe(false);
  });

  test("títulos: hook-primero mueve el segmento tras ':'; corto recorta a 7 palabras", () => {
    expect(hookFirstTitle("Dasgasd3m se ríe: el mejor momento de GTA 6")).toBe("el mejor momento de GTA 6 — Dasgasd3m se ríe");
    expect(shortTitle("uno dos tres cuatro cinco seis siete ocho nueve")).toBe("uno dos tres cuatro cinco seis siete");
    expect(shortTitle("corto")).toBe("corto");
  });
});

describe("evolution.ts puro: fitness y selección", () => {
  test("realFitness: vistas aprobadas mandan; si no hay, vistas; sin nada, null (nunca inventado)", () => {
    expect(realFitness(12000, 50000)).toBe(12000);
    expect(realFitness(null, 50000)).toBe(50000);
    expect(realFitness(null, null)).toBeNull();
  });

  test("selectWinners: el fitness REAL manda sobre el cerebro", () => {
    const vs: EvoScoredVariant[] = [
      { index: 0, brain_virality: 90, brain_seo: 95, real_fitness: 100, has_real_data: true },
      { index: 1, brain_virality: 70, brain_seo: 90, real_fitness: 50000, has_real_data: true },
      { index: 2, brain_virality: null, brain_seo: null, real_fitness: null, has_real_data: false },
    ];
    const sel = selectWinners(vs, 1, 45, 80);
    expect(sel.winners.map((w) => w.index)).toEqual([1]); // 50.000 reales > 90 del cerebro
    expect(sel.losers.map((w) => w.index)).toEqual([0]);
    expect(sel.untested.map((w) => w.index)).toEqual([2]);
  });

  test("selectWinners: sin puntaje del cerebro no hay ranking: queda sin probar, NO perdedora", () => {
    const vs: EvoScoredVariant[] = [
      { index: 0, brain_virality: 70, brain_seo: 90, real_fitness: null, has_real_data: false },
      { index: 1, brain_virality: null, brain_seo: null, real_fitness: null, has_real_data: false },
    ];
    const sel = selectWinners(vs, 3, 45, 80);
    expect(sel.winners.map((w) => w.index)).toEqual([0]);
    expect(sel.losers).toEqual([]);
    expect(sel.untested.map((w) => w.index)).toEqual([1]);
  });

  test("selectWinners: bajo 45/80 va a blocked con motivo, no a winner ni loser", () => {
    const vs: EvoScoredVariant[] = [
      { index: 0, brain_virality: 70, brain_seo: 90, real_fitness: null, has_real_data: false },
      { index: 1, brain_virality: 30, brain_seo: 90, real_fitness: null, has_real_data: false },
      { index: 2, brain_virality: 70, brain_seo: 50, real_fitness: null, has_real_data: false },
    ];
    const sel = selectWinners(vs, 3, 45, 80);
    expect(sel.winners.map((w) => w.index)).toEqual([0]);
    expect(sel.blocked.map((w) => w.index)).toEqual([1, 2]);
    expect(sel.losers).toEqual([]);
  });

  test("publishBlockReason: ganadora distinta + elegible pasa; perdedora, no distinta y otra campaña frenan", () => {
    const base = {
      status: "winner" as const, isDistinctFromBase: true, campaignEligible: true,
      campaignEligibilityReason: null, qualityGateReason: null,
      alreadySubmittedToOtherCampaign: false, alreadySubmittedToThisCampaign: false,
    };
    expect(publishBlockReason(base)).toBeNull();
    expect(publishBlockReason({ ...base, status: "loser" })).toContain("solo se publica la ganadora");
    expect(publishBlockReason({ ...base, isDistinctFromBase: false })).toContain("no es un corte distinto");
    expect(publishBlockReason({ ...base, alreadySubmittedToOtherCampaign: true })).toContain("OTRA campaña");
    expect(publishBlockReason({ ...base, alreadySubmittedToThisCampaign: true })).toContain("no se duplica");
    expect(publishBlockReason({ ...base, qualityGateReason: "calidad: sin veredicto" })).toContain("calidad");
  });
});

describe("migración 0042 + helpers D1", () => {
  test("generación duplicada devuelve la misma fila; variantes y fitness real se guardan", async () => {
    const { d1 } = makeDb();
    const first = await createEvoGeneration(d1, "sebastian", { streamer: "dasgasd3m", campaign_id: DASGASD3M, base_clip_id: 174, generation: 1, platform: "tiktok" });
    expect(first.duplicate).toBe(false);
    const again = await createEvoGeneration(d1, "sebastian", { streamer: "dasgasd3m", campaign_id: DASGASD3M, base_clip_id: 174, generation: 1, platform: "tiktok" });
    expect(again.duplicate).toBe(true);
    expect(again.row.id).toBe(first.row.id);
    const v = await createEvoVariant(d1, "sebastian", {
      generation_id: first.row.id, variant_index: 0, params: { ...BASE_PARAMS }, mutations: [], is_control: true,
      brain_virality: 70, brain_seo: 90, brain_fitness: 70,
    });
    expect(v.status).toBe("candidate");
    expect(v.has_real_data).toBe(0);
    const fit = await recordEvoFitness(d1, "sebastian", v.id, { source: "real", approved_views: 12000, views: 40000, retention: 61.5, fitness: 12000, measured_at: NOW });
    expect(fit?.source).toBe("real");
    const updated = await getEvoVariant(d1, v.id, "sebastian");
    expect(updated?.has_real_data).toBe(1);
    expect(updated?.real_fitness).toBe(12000);
    expect(updated?.real_approved_views).toBe(12000);
    const summary = await evoStatusSummary(d1, "sebastian");
    expect(summary.length).toBe(1);
    expect(summary[0]?.has_real_data).toBe(true);
  });
});

describe("rutas /api/evolution (con sesión)", () => {
  test("dry-run gen 1 Dasgasd3m: 201, 6 variantes, ganadora = control, mutantes candidatas, nada publicado", async () => {
    const { d1, raw } = makeDb();
    insertInfluencer(raw, 1, "Dasgasd3m");
    insertClip(raw, 174, 1, VIRAL_OK);
    const env = makeTestEnv(d1);
    const res = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 174, platform: "tiktok" });
    expect(res.status).toBe(201);
    expect(res.json.dry_run).toBe(true);
    expect(res.json.published_nothing).toBe(true);
    expect(res.json.publishable).toBe(true);
    const variants = res.json.variants as Array<{ variant_index: number; status: string; is_control: number; has_real_data: number }>;
    expect(variants.length).toBe(6);
    expect(variants.find((v) => v.variant_index === 0)?.status).toBe("winner");
    expect(variants.find((v) => v.variant_index === 0)?.is_control).toBe(1);
    expect(variants.filter((v) => v.variant_index !== 0).every((v) => v.status === "candidate")).toBe(true);
    expect(variants.filter((v) => v.variant_index !== 0).every((v) => v.has_real_data === 0)).toBe(true);
    expect(variants.some((v) => v.status === "loser")).toBe(false); // sin datos, nadie es perdedora
    // Sin tabla jobs ni envíos: nada publicado ni enviado.
    expect((await listEvoVariants(d1, "sebastian", (res.json.generation as { id: number }).id)).length).toBe(6);
  });

  test("dry-run repetido en la misma generación: duplicate, no duplica variantes", async () => {
    const { d1, raw } = makeDb();
    insertInfluencer(raw, 1, "Dasgasd3m");
    insertClip(raw, 174, 1, VIRAL_OK);
    const env = makeTestEnv(d1);
    const first = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 174 });
    expect(first.status).toBe(201);
    const again = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 174, generation: 1 });
    expect(again.status).toBe(200);
    expect(again.json.duplicate).toBe(true);
    expect((again.json.generation as { id: number }).id).toBe((first.json.generation as { id: number }).id);
    expect((await listEvoVariants(d1, "sebastian", (first.json.generation as { id: number }).id)).length).toBe(6);
  });

  test("dry-run con brain_scores: la mutante con mejor puntaje gana; las de abajo son perdedoras; la de bajo 45/80, blocked", async () => {
    const { d1, raw } = makeDb();
    insertInfluencer(raw, 1, "Dasgasd3m");
    insertClip(raw, 174, 1, VIRAL_OK);
    const env = makeTestEnv(d1);
    const res = await callApi(env, "POST", "/api/evolution/dry-run", {
      clip_id: 174,
      brain_scores: [
        { index: 1, virality: 85, seo: 95 },
        { index: 2, virality: 80, seo: 92 },
        { index: 3, virality: 75, seo: 91 },
        { index: 4, virality: 65, seo: 89 },
        { index: 5, virality: 30, seo: 90 },
      ],
    });
    expect(res.status).toBe(201);
    const variants = res.json.variants as Array<{ variant_index: number; status: string }>;
    // Top 3 por cerebro: 1 (85), 2 (80), 3 (75) + control (70) rankeable: ganan 1, 2, 3.
    const winners = variants.filter((v) => v.status === "winner").map((v) => v.variant_index).sort();
    expect(winners).toEqual([1, 2, 3]);
    expect(variants.find((v) => v.variant_index === 0)?.status).toBe("loser"); // control 70 < 75
    expect(variants.find((v) => v.variant_index === 4)?.status).toBe("loser");
    expect(variants.find((v) => v.variant_index === 5)?.status).toBe("blocked");
    expect((res.json.winner as { variant_index: number }).variant_index).toBe(1);
    expect(res.json.publishable).toBe(true); // mutante ganadora con corte distinto
  });

  test("dry-run rechaza: clip sin viral_score, Bobby Van Riper por fama, BenDaDonnn por piso, plataforma no cubierta", async () => {
    const { d1, raw } = makeDb();
    insertInfluencer(raw, 1, "Dasgasd3m");
    insertInfluencer(raw, 2, "Bobby Van Riper");
    insertInfluencer(raw, 3, "BenDaDonnn");
    insertClip(raw, 174, 1, VIRAL_NO_SCORE);
    insertClip(raw, 175, 2, VIRAL_OK);
    insertClip(raw, 176, 3, VIRAL_OK);
    insertClip(raw, 177, 1, VIRAL_OK);
    const env = makeTestEnv(d1);
    const noScore = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 174 });
    expect(noScore.status).toBe(400);
    expect(String(noScore.json.error)).toContain("Sin puntaje del cerebro");
    const bobby = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 175 });
    expect(bobby.status).toBe(400);
    expect(String(bobby.json.error)).toContain("no es famoso");
    const ben = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 176 });
    expect(ben.status).toBe(400);
    expect(String(ben.json.error)).toContain("bajo el piso");
    const wrongPlatform = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 177, platform: "instagram" });
    expect(wrongPlatform.status).toBe(400);
    expect(String(wrongPlatform.json.error)).toContain("no cubre instagram");
  });

  test("dry-run 409 si el post ya tiene envío a OTRA campaña (un post = una campaña)", async () => {
    const { d1, raw } = makeDb();
    insertInfluencer(raw, 1, "Dasgasd3m");
    insertClip(raw, 174, 1, VIRAL_OK);
    await createCampaignSubmission(d1, "sebastian", { clip_id: 174, platform: "tiktok", campaign_id: ARTHURTV });
    const env = makeTestEnv(d1);
    const res = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 174, platform: "tiktok" });
    expect(res.status).toBe(409);
    expect(String(res.json.error)).toContain("OTRA campaña");
  });

  test("dry-run 409 si el clip base YA se publicó en esa plataforma (no republicar el mismo momento)", async () => {
    const { d1, raw } = makeDb();
    insertInfluencer(raw, 1, "Dasgasd3m");
    insertClip(raw, 174, 1, VIRAL_OK, JSON.stringify({ tiktok: { state: "publicado", scheduledFor: null, publishedAt: "2026-11-23T10:00:00.000Z", note: "", since: "", updatedAt: "" } }));
    const env = makeTestEnv(d1);
    const res = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 174, platform: "tiktok" });
    expect(res.status).toBe(409);
    expect(String(res.json.error)).toContain("ya se publicó en tiktok");
    // En otra plataforma no publicada, sí corre.
    const yt = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 174, platform: "youtube" });
    expect(yt.status).toBe(201);
  });

  test("V5 — dry-run NO da 409 si el clip NO se publicó en esa plataforma (negativo del 409)", async () => {
    const { d1, raw } = makeDb();
    insertInfluencer(raw, 1, "Dasgasd3m");
    insertClip(raw, 174, 1, VIRAL_OK); // publish_states "{}": nada publicado
    const env = makeTestEnv(d1);
    const res = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 174, platform: "tiktok" });
    expect(res.status).toBe(201);
    expect(res.json.published_nothing).toBe(true);
  });

  test("V5 — FALSO POSITIVO CONOCIDO: publicado en D1 pero borrado en la plataforma sigue dando 409", async () => {
    // Documenta el comportamiento actual, NO lo arregla: la fuente
    // de verdad del 409 es solo D1 clips.publish_states (ver el
    // comentario V5 en la ruta dry-run de src/index.ts). La ruta no
    // consulta la plataforma ni jobs.deleted_at (0031: vídeo
    // borrado o privado, probado por la medición), así que una
    // republicación legítima queda bloqueada. Cambiar la fuente de
    // verdad sería cambio de lógica, fuera de esta spec.
    const { d1, raw } = makeDb();
    insertInfluencer(raw, 1, "Dasgasd3m");
    insertClip(raw, 174, 1, VIRAL_OK, JSON.stringify({ tiktok: { state: "publicado", scheduledFor: null, publishedAt: "2026-11-23T10:00:00.000Z", note: "", since: "", updatedAt: "" } }));
    // El vídeo ya no existe en TikTok: job posted con deleted_at
    // (migración 0031). La ruta dry-run no lee esta tabla.
    raw.exec(
      "CREATE TABLE jobs (id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, clip_id INTEGER, platform TEXT NOT NULL, " +
        "language TEXT NOT NULL DEFAULT 'es', state TEXT NOT NULL DEFAULT 'posted', deleted_at INTEGER, posted_day TEXT)",
    );
    raw.query(
      "INSERT INTO jobs (id, tenant_id, clip_id, platform, language, state, deleted_at) VALUES (1, 'sebastian', 174, 'tiktok', 'es', 'posted', ?)",
    ).run(NOW);
    const env = makeTestEnv(d1);
    const res = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 174, platform: "tiktok" });
    expect(res.status).toBe(409);
    expect(String(res.json.error)).toContain("ya se publicó en tiktok");
  });

  test("V5 — ES/EN: el 409 NO distingue idioma dentro de una fila de clip; ES y EN son filas distintas", async () => {
    // Verificado contra el código (2026-11-23): la ruta dry-run no
    // recibe idioma ni lee clip.language; el 409 es
    // parsePublishStates(clip.publish_states)[platform] y
    // publish_states es un espejo por clip SIN idioma (db.ts:3480:
    // la clave es solo la plataforma). Un clip tiene UN solo
    // idioma (clipLanguage: es o en; los jobs heredan el del
    // clip), así que la versión ES y la EN son filas distintas con
    // su propio publish_states: publicar la ES no bloquea el
    // dry-run de la EN, pero dentro de UNA fila el 409 es ciego
    // al idioma — no hay forma de pedir "dry-run EN" de un clip
    // ES ya publicado.
    const { d1, raw } = makeDb();
    insertInfluencer(raw, 1, "Dasgasd3m");
    // Fila ES del momento: ya publicada en TikTok.
    insertClip(raw, 174, 1, VIRAL_OK, JSON.stringify({ tiktok: { state: "publicado", scheduledFor: null, publishedAt: "2026-11-23T10:00:00.000Z", note: "", since: "", updatedAt: "" } }), "es");
    // Fila EN del mismo momento (otro id): sin publicar.
    insertClip(raw, 175, 1, VIRAL_OK, "{}", "en");
    const env = makeTestEnv(d1);
    // La fila ES bloquea su propio dry-run en TikTok...
    const es = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 174, platform: "tiktok" });
    expect(es.status).toBe(409);
    // ...pero NO bloquea el dry-run de la fila EN del mismo momento.
    const en = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 175, platform: "tiktok" });
    expect(en.status).toBe(201);
    // Y la fila ES sí corre en otra plataforma no publicada: el
    // 409 es por (clip, plataforma), sin dimensión de idioma.
    const esYt = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 174, platform: "youtube" });
    expect(esYt.status).toBe(201);
  });

  test("record-real-fitness: anota vistas aprobadas, marca has_real_data; sin dato, 400; variante inexistente, 404", async () => {
    const { d1, raw } = makeDb();
    insertInfluencer(raw, 1, "Dasgasd3m");
    insertClip(raw, 174, 1, VIRAL_OK);
    const env = makeTestEnv(d1);
    const gen = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 174 });
    const winner = gen.json.winner as { id: number };
    const ok = await callApi(env, "POST", "/api/evolution/record-real-fitness", { variant_id: winner.id, approved_views: 12000, views: 40000, retention_pct: 61.5 });
    expect(ok.status).toBe(200);
    expect((ok.json.variant as { has_real_data: number }).has_real_data).toBe(1);
    expect(ok.json.real_fitness).toBe(12000);
    const empty = await callApi(env, "POST", "/api/evolution/record-real-fitness", { variant_id: winner.id });
    expect(empty.status).toBe(400);
    const missing = await callApi(env, "POST", "/api/evolution/record-real-fitness", { variant_id: 99999, views: 10 });
    expect(missing.status).toBe(404);
  });

  test("generación 2 desde la padre: los hijos mutan la ganadora con datos reales; sin ganadora, 400", async () => {
    const { d1, raw } = makeDb();
    insertInfluencer(raw, 1, "Dasgasd3m");
    insertClip(raw, 174, 1, VIRAL_OK);
    const env = makeTestEnv(d1);
    const gen1 = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 174 });
    const gen1Id = (gen1.json.generation as { id: number }).id;
    const winner = gen1.json.winner as { id: number };
    await callApi(env, "POST", "/api/evolution/record-real-fitness", { variant_id: winner.id, approved_views: 12000 });
    const gen2 = await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 174, generation: 2, parent_generation_id: gen1Id });
    expect(gen2.status).toBe(201);
    const variants = gen2.json.variants as Array<{ variant_index: number; status: string; params: EvoVariantParams }>;
    expect(variants.length).toBe(6);
    // El control de la gen 2 es la ganadora padre (mismos params).
    expect(variants[0]?.params.start_sec).toBe(100);
    expect(variants[0]?.params.end_sec).toBe(140);
    expect(variants.slice(1).every((v) => isDistinctCut(variants[0]!.params, v.params))).toBe(true);
  });

  test("GET /api/evolution/status lista generaciones con ganadora; sin sesión, 401", async () => {
    const { d1, raw } = makeDb();
    insertInfluencer(raw, 1, "Dasgasd3m");
    insertClip(raw, 174, 1, VIRAL_OK);
    const env = makeTestEnv(d1);
    await callApi(env, "POST", "/api/evolution/dry-run", { clip_id: 174 });
    const res = await callApi(env, "GET", "/api/evolution/status");
    expect(res.status).toBe(200);
    const gens = res.json.generations as Array<{ streamer: string; generation: number; winner: { variant_index: number } | null; has_real_data: boolean }>;
    expect(gens.length).toBe(1);
    expect(gens[0]?.streamer).toBe("dasgasd3m");
    expect(gens[0]?.winner?.variant_index).toBe(0);
    expect(gens[0]?.has_real_data).toBe(false);
    const noAuth = await worker.fetch(
      new Request("https://worker.test/api/evolution/status", { method: "GET" }),
      env,
      { waitUntil: () => {}, passThroughOnException: () => {} } as never,
    );
    expect(noAuth.status).toBe(401);
  });
});
