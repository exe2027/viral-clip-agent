// Tests de puntuación IA (2026-11-18): contrato de campos de
// POST/PATCH /api/clips (seo_scores, viral_breakdown, hype_score,
// priority, score_kind) y su serialización en GET (parseado, con
// null = "sin medir"). Sin dato no se inventa ningún número.
import { describe, expect, test } from "bun:test";
import { createSessionToken } from "../src/auth";
import type { Env } from "../src/cloudflare";
import worker from "../src/index";
import { seoForPlatform } from "../src/jobs";
import type { ClipRow } from "../src/db";

const SECRET = Buffer.from("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb").toString("base64"); // SOLO TEST
const KEY = Buffer.from("0123456789abcdef0123456789abcdef").toString("base64");

type Row = Record<string, unknown>;

class FakeStmt {
  constructor(private db: FakeD1, private sql: string, private params: unknown[] = []) {}
  bind(...values: unknown[]): FakeStmt {
    return new FakeStmt(this.db, this.sql, values);
  }
  async first<T = Record<string, unknown>>(): Promise<T | null> {
    return (this.db.runSelect(this.sql, this.params)[0] as T | undefined) ?? null;
  }
  async all<T = Record<string, unknown>>(): Promise<{ results: T[]; success: boolean; meta: Record<string, unknown> }> {
    return { results: this.db.runSelect(this.sql, this.params) as T[], success: true, meta: {} };
  }
  async run(): Promise<{ results: never[]; success: boolean; meta: { changes?: number; last_row_id?: number } }> {
    return this.db.runWrite(this.sql, this.params);
  }
}

class FakeD1 {
  tables: Record<string, Row[]> = {};
  prepare(sql: string): FakeStmt {
    return new FakeStmt(this, sql);
  }
  async batch(): Promise<never[]> {
    return [];
  }
  async exec(): Promise<{ results: never[]; success: boolean; meta: Record<string, unknown> }> {
    return { results: [], success: true, meta: {} };
  }
  runSelect(sql: string, params: unknown[]): Row[] {
    const m = /^SELECT\s+(.+?)\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+?))?(?:\s+ORDER BY\s+.+?)?\s*$/is.exec(sql);
    if (!m) throw new Error(`SELECT no soportado en el mock: ${sql}`);
    const [, colsRaw, table, where] = m as [string, string, string, string | undefined];
    let rows = [...(this.tables[table as string] ?? [])];
    if (where) {
      const conds = where.split(/\s+AND\s+/i).map((c) => c.trim());
      let pi = 0;
      for (const c of conds) {
        const eq = /^(\w+)\s*=\s*\?$/.exec(c);
        const lit = /^(\w+)\s*=\s*(\d+)$/.exec(c);
        const inList = /^(\w+)\s+IN\s*\(([^)]+)\)$/i.exec(c);
        if (eq) {
          const col = eq[1] as string;
          const val = params[pi++];
          rows = rows.filter((r) => r[col] === val);
        } else if (lit) {
          const col = lit[1] as string;
          const val = Number(lit[2]);
          rows = rows.filter((r) => r[col] === val);
        } else if (inList) {
          const col = inList[1] as string;
          const vals = (inList[2] as string).split(",").map((x) => x.trim().replace(/^'|'$/g, ""));
          rows = rows.filter((r) => vals.includes(String(r[col])));
        } else {
          throw new Error(`WHERE no soportado en el mock: ${c}`);
        }
      }
    }
    const cols = (colsRaw as string).trim();
    if (cols === "*") return rows;
    const wanted = cols.split(",").map((c) => c.trim());
    return rows.map((r) => {
      const out: Row = {};
      for (const c of wanted) out[c] = r[c];
      return out;
    });
  }
  runWrite(sql: string, params: unknown[]): { results: never[]; success: boolean; meta: { changes?: number; last_row_id?: number } } {
    const insert = /^INSERT INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)\s*$/is.exec(sql);
    if (insert) {
      const [, table, colsRaw] = insert as [string, string, string];
      const cols = colsRaw.split(",").map((c) => c.trim());
      const rows = (this.tables[table as string] ??= []);
      const id = rows.reduce((max, r) => Math.max(max, Number(r.id ?? 0)), 0) + 1;
      const row: Row = { id };
      cols.forEach((c, i) => {
        row[c] = params[i];
      });
      // D1 pone archived=0 por defecto (columna con DEFAULT 0); el
      // mock lo replica para que listQueueClips no filtre el clip.
      if (table === "clips" && row.archived === undefined) row.archived = 0;
      rows.push(row);
      return { results: [], success: true, meta: { changes: 1, last_row_id: id } };
    }
    const update = /^UPDATE\s+(\w+)\s+SET\s+(.+?)\s+WHERE\s+(.+)$/is.exec(sql);
    if (update) {
      const [, table, setRaw, where] = update as [string, string, string];
      const parts = setRaw.split(",").map((p) => p.trim());
      const setCols: string[] = [];
      const setVals: unknown[] = [];
      let pi = 0;
      for (const part of parts) {
        const ph = /^(\w+)\s*=\s*\?$/.exec(part);
        if (!ph) throw new Error(`SET no soportado en el mock: ${part}`);
        setCols.push(ph[1] as string);
        setVals.push(params[pi++]);
      }
      const whereParams = params.slice(pi);
      const rows = this.tables[table as string] ?? [];
      const conds = where.split(/\s+AND\s+/i).map((c) => c.trim());
      let wpi = 0;
      const tests: Array<(r: Row) => boolean> = [];
      for (const c of conds) {
        const eq = /^(\w+)\s*=\s*\?$/.exec(c);
        if (!eq) throw new Error(`WHERE no soportado en el mock: ${c}`);
        const col = eq[1] as string;
        const val = whereParams[wpi++];
        tests.push((r) => r[col] === val);
      }
      let changes = 0;
      for (const r of rows) {
        if (!tests.every((t) => t(r))) continue;
        setCols.forEach((c, i) => {
          r[c] = setVals[i];
        });
        changes++;
      }
      return { results: [], success: true, meta: { changes } };
    }
    throw new Error(`Escritura no soportada en el mock: ${sql}`);
  }
}

function makeEnv(db: FakeD1): Env {
  return {
    DB: db as unknown as Env["DB"],
    TENANT: "sebastian",
    CLIPS_BUCKET: {} as Env["CLIPS_BUCKET"],
    SESSION_SECRET: SECRET,
    CREDENTIAL_ENCRYPTION_KEY: KEY,
    BRIDGE_TOKEN: "bridge-test",
    GITHUB_TOKEN: "",
    ALLOWED_EMAIL: "",
    GITHUB_REPO: "exe2027/viral-clip-agent",
  };
}

const ctx = { waitUntil: () => {}, passThroughOnException: () => {} };

async function call(env: Env, method: string, path: string, opts: { token?: string; body?: Record<string, unknown> } = {}) {
  const headers: Record<string, string> = {};
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await worker.fetch(
    new Request(`https://worker.test${path}`, {
      method,
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    }),
    env,
    ctx as never,
  );
  const text = await res.text();
  return { status: res.status, json: JSON.parse(text) as Record<string, unknown> };
}

const P3220_BODY = {
  title: 'GTA 6: El chat: "Cinema"',
  start_sec: 3200,
  end_sec: 3248,
  score: 54,
  game: "GTA VI",
  influencer_id: 2,
  platforms: ["youtube_shorts", "tiktok", "instagram_reels", "facebook_reels"],
  subtitles_complete: 1,
  subtitle_style: "simple",
  seo_title: 'GTA 6: El chat: "Cinema"',
  seo_description: "El chat se murió de risa en GTA 6",
  seo_tags: "#GTA6 #GTA6Clips #ElClipDeGTA6 #GTA #RockstarGames #gaming",
  thumb_text: "LA REACCIÓN",
  viral_score: 53.8,
  viral_breakdown: { chat: 5.9, hype: 5.9 },
  hype_score: 5.9,
  priority: 87.3,
  seo_scores: { youtube_shorts: 92, tiktok: 94, instagram_reels: 94, facebook_reels: 94 },
};

describe("POST /api/clips con puntuación IA (contrato p3220)", () => {
  test("201 y GET devuelve seo_scores, hype, prioridad y score_kind ia", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const r = await call(env, "POST", "/api/clips", { token: tok, body: P3220_BODY });
    expect(r.status).toBe(201);
    const clip = r.json.clip as Record<string, unknown>;
    expect(clip.thumb_text).toBe("LA REACCIÓN");
    expect(clip.score_kind).toBe("ia");
    expect(clip.priority).toBe(87.3);
    expect(clip.hype_score).toBe(5.9);
    expect(clip.seo_scores).toEqual({ youtube_shorts: 92, tiktok: 94, instagram_reels: 94, facebook_reels: 94 });
    expect((clip.viral as Record<string, unknown>).platforms).toEqual({
      youtube_shorts: { score: 92 },
      tiktok: { score: 94 },
      instagram_reels: { score: 94 },
      facebook_reels: { score: 94 },
    });
    const list = await call(env, "GET", "/api/clips", { token: tok });
    expect(list.status).toBe(200);
    const clips = list.json.clips as Array<Record<string, unknown>>;
    expect(clips.length).toBe(1);
    expect(clips[0]?.seo_scores).toEqual({ youtube_shorts: 92, tiktok: 94, instagram_reels: 94, facebook_reels: 94 });
  });

  test("seo_scores fuera de 0–100: 400", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const r = await call(env, "POST", "/api/clips", {
      token: tok,
      body: { ...P3220_BODY, seo_scores: { youtube_shorts: 150 } },
    });
    expect(r.status).toBe(400);
    expect(String(r.json.error)).toContain("seo_scores");
  });

  test("sin datos de puntuación: score_kind null y seo_scores null (sin medir)", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const { viral_score: _v, viral_breakdown: _b, hype_score: _h, priority: _p, seo_scores: _s, ...plain } = P3220_BODY;
    const r = await call(env, "POST", "/api/clips", { token: tok, body: plain });
    expect(r.status).toBe(201);
    const clip = r.json.clip as Record<string, unknown>;
    expect(clip.score_kind).toBeNull();
    expect(clip.seo_scores).toBeNull();
    expect(clip.priority).toBeNull();
    expect(clip.hype_score).toBeNull();
  });
});

describe("POST /api/clips: puerta SEO en la creación (2026-11-18)", () => {
  test("seo_scores con máximo 79 (< 80): 400 (puerta única 45/80, P0 #3)", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const r = await call(env, "POST", "/api/clips", {
      token: tok,
      body: { ...P3220_BODY, seo_scores: { youtube_shorts: 75, tiktok: 79 } },
    });
    expect(r.status).toBe(400);
    expect(String(r.json.error)).toContain("SEO máximo 79");
    expect(String(r.json.error)).toContain("80");
  });

  test("seo_scores con máximo 88 (>= 80): 201", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const r = await call(env, "POST", "/api/clips", {
      token: tok,
      body: { ...P3220_BODY, seo_scores: { youtube_shorts: 85, tiktok: 88 } },
    });
    expect(r.status).toBe(201);
  });

  test("seo_scores con máximo 94 (>= 90): 201", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const r = await call(env, "POST", "/api/clips", {
      token: tok,
      body: { ...P3220_BODY, seo_scores: { youtube_shorts: 92, tiktok: 94 } },
    });
    expect(r.status).toBe(201);
  });

  test("seo_scores con máximo exactamente 80: 201 (el mínimo pasa)", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const r = await call(env, "POST", "/api/clips", {
      token: tok,
      body: { ...P3220_BODY, seo_scores: { youtube_shorts: 80 } },
    });
    expect(r.status).toBe(201);
  });

  test("sin seo_scores (clip heredado sin medir): no es 400 por SEO", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const { seo_scores: _s, ...plain } = P3220_BODY;
    const r = await call(env, "POST", "/api/clips", { token: tok, body: plain });
    expect(r.status).toBe(201);
  });

  test("seo_scores bajos + seo_override: true: 201 (override explícito)", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const r = await call(env, "POST", "/api/clips", {
      token: tok,
      body: { ...P3220_BODY, seo_scores: { youtube_shorts: 85, tiktok: 88 }, seo_override: true },
    });
    expect(r.status).toBe(201);
  });

  test("seo_scores bajos dentro de viral: también 400", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const { seo_scores: _s, ...rest } = P3220_BODY;
    const r = await call(env, "POST", "/api/clips", {
      token: tok,
      body: { ...rest, viral: { seo_scores: { youtube_shorts: 75, tiktok: 79 } } },
    });
    expect(r.status).toBe(400);
    expect(String(r.json.error)).toContain("SEO máximo 79");
  });
});

describe("PATCH /api/clips afina la puntuación (fusión, no reemplazo)", () => {
  test("PATCH seo_scores + thumb_text fusiona con lo guardado", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const created = await call(env, "POST", "/api/clips", { token: tok, body: P3220_BODY });
    const id = (created.json.clip as Record<string, unknown>).id as number;
    const r = await call(env, "PATCH", `/api/clips/${id}`, {
      token: tok,
      body: { seo_scores: { youtube_shorts: 88 }, thumb_text: "EL MOMENTO" },
    });
    expect(r.status).toBe(200);
    const clip = r.json.clip as Record<string, unknown>;
    expect(clip.thumb_text).toBe("EL MOMENTO");
    expect(clip.seo_scores).toEqual({ youtube_shorts: 88, tiktok: 94, instagram_reels: 94, facebook_reels: 94 });
    expect(clip.hype_score).toBe(5.9); // no se borra al afinar el SEO
    expect(clip.priority).toBe(87.3);
  });

  test("score_kind inválido: 400", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const created = await call(env, "POST", "/api/clips", { token: tok, body: P3220_BODY });
    const id = (created.json.clip as Record<string, unknown>).id as number;
    const r = await call(env, "PATCH", `/api/clips/${id}`, { token: tok, body: { score_kind: "robot" } });
    expect(r.status).toBe(400);
  });
});

describe("seoForPlatform lee seo_scores (contrato IA)", () => {
  test("seo_scores como fuente cuando no hay viral.platforms", () => {
    const clip = { viral: JSON.stringify({ seo_scores: { tiktok: 94 } }) } as ClipRow;
    expect(seoForPlatform(clip, "tiktok").score).toBe(94);
    expect(seoForPlatform(clip, "youtube_shorts").score).toBeNull();
  });
  test("la forma legacy viral.platforms sigue valiendo", () => {
    const clip = { viral: JSON.stringify({ platforms: { youtube_shorts: { score: 92 } } }) } as ClipRow;
    expect(seoForPlatform(clip, "youtube_shorts").score).toBe(92);
  });
});

const SEO_INPUTS = {
  hashtags: ["#GTA6", "#GTA6Clips", "#GTA", "#RockstarGames"],
  visible: "cams+gameplay",
  game_verified: true,
  keyword: "GTA 6",
  lang: "es",
};

describe("seo_inputs/seo_source viajan en viral (auto-seo 2026-11-24)", () => {
  test("POST con viral.seo_inputs + seo_source: GET los devuelve", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const r = await call(env, "POST", "/api/clips", {
      token: tok,
      body: { ...P3220_BODY, viral: { seo_inputs: SEO_INPUTS, seo_source: "corte" } },
    });
    expect(r.status).toBe(201);
    const viral = (r.json.clip as Record<string, unknown>).viral as Record<string, unknown>;
    expect(viral.seo_inputs).toEqual(SEO_INPUTS);
    expect(viral.seo_source).toBe("corte");
    expect(viral.seo_scores).toEqual({ youtube_shorts: 92, tiktok: 94, instagram_reels: 94, facebook_reels: 94 });
  });

  test("PATCH con uno solo preserva el otro y el resto de viral", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const created = await call(env, "POST", "/api/clips", {
      token: tok,
      body: { ...P3220_BODY, viral: { seo_inputs: SEO_INPUTS, seo_source: "corte" } },
    });
    const id = (created.json.clip as Record<string, unknown>).id as number;
    const r = await call(env, "PATCH", `/api/clips/${id}`, {
      token: tok,
      body: { viral: { seo_source: "backfill_exacto" } },
    });
    expect(r.status).toBe(200);
    const viral = (r.json.clip as Record<string, unknown>).viral as Record<string, unknown>;
    expect(viral.seo_source).toBe("backfill_exacto");
    expect(viral.seo_inputs).toEqual(SEO_INPUTS);
    expect(viral.seo_scores).toEqual({ youtube_shorts: 92, tiktok: 94, instagram_reels: 94, facebook_reels: 94 });
  });

  test("seo_source inválido: 400; seo_inputs sin hashtags: 400", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const badSource = await call(env, "POST", "/api/clips", {
      token: tok,
      body: { ...P3220_BODY, viral: { seo_source: "a_mano" } },
    });
    expect(badSource.status).toBe(400);
    const badInputs = await call(env, "POST", "/api/clips", {
      token: tok,
      body: { ...P3220_BODY, viral: { seo_inputs: { hashtags: "no-lista" } } },
    });
    expect(badInputs.status).toBe(400);
  });
});

describe("puerta SEO del POST intacta (auto-seo 2026-11-24)", () => {
  test("POST normal con máximo SEO < 80: 400 (el clip normal no manda SEO en el POST, va por PATCH)", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const r = await call(env, "POST", "/api/clips", {
      token: tok,
      body: { ...P3220_BODY, seo_scores: { youtube_shorts: 70, tiktok: 65, instagram_reels: 60, facebook_reels: 55 } },
    });
    expect(r.status).toBe(400);
    expect(String(r.json.error)).toContain("SEO máximo 70");
  });

  test("POST sin seo_scores: 201 (entra a la Cola, el SEO se mide luego por PATCH)", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const { seo_scores: _s, ...plain } = P3220_BODY;
    const r = await call(env, "POST", "/api/clips", { token: tok, body: plain });
    expect(r.status).toBe(201);
  });

  test("POST rescate con máximo SEO < 80: 400 (el rescate exige SEO >= 80)", async () => {
    const env = makeEnv(new FakeD1());
    const tok = await createSessionToken("seba", "sebastian", SECRET);
    const r = await call(env, "POST", "/api/clips", {
      token: tok,
      body: {
        ...P3220_BODY,
        rescue: true,
        rescue_shouts: 3,
        viral_score: 48,
        seo_scores: { youtube_shorts: 70, tiktok: 65, instagram_reels: 60, facebook_reels: 55 },
      },
    });
    expect(r.status).toBe(400);
    expect(String(r.json.error)).toContain("SEO");
  });
});
