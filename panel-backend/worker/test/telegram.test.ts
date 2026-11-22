// Tests del resumen diario de analíticas por Telegram (2026-11-18):
// buildAnalyticsMessage (función pura), sendTelegram (fetch simulado)
// y los endpoints /api/telegram/status y /api/telegram/test.
// Sin red, sin D1 real: D1 falso mínimo con solo las consultas que
// usan estos caminos (settings, clip_stats, clips, revoked_tokens,
// worker_events). La sesión se crea de verdad con createSessionToken.
import { afterEach, describe, expect, test } from "bun:test";
import { createSessionToken } from "../src/auth";
import type { Env } from "../src/cloudflare";
import worker from "../src/index";
import {
  buildAnalyticsMessage,
  buildTestMessage,
  santiagoParts,
  sendTelegram,
  type AnalyticsPlatformSummary,
} from "../src/telegram";

const SECRET = Buffer.from("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb").toString("base64"); // SOLO TEST
const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
});

function platform(over: Partial<AnalyticsPlatformSummary> = {}): AnalyticsPlatformSummary {
  return {
    platform: "youtube",
    median_views: null,
    median_retention: null,
    measured_clips: 0,
    published_clips: 0,
    has_data: false,
    ranking: [],
    ...over,
  };
}

function allPlatforms(): AnalyticsPlatformSummary[] {
  return ["youtube", "tiktok", "instagram", "facebook"].map((p) => platform({ platform: p }));
}

describe("buildAnalyticsMessage", () => {
  test("sin mediciones: dice 'sin medición', nunca 0 ni inventos, sin mandar a pegar a mano", () => {
    const text = buildAnalyticsMessage(allPlatforms(), "2026-11-18");
    expect(text).toContain("📊 Analíticas GTA 6 — 2026-11-18");
    expect(text).not.toContain("Canales ES · @elclipdegta6");
    expect(text).not.toContain("a mano");
    for (const name of ["YouTube", "TikTok", "Instagram", "Facebook"]) {
      expect(text).toContain(name);
    }
    expect(text).toContain("Sin medición todavía");
    expect(text).toContain("Medición automática: aún no intentada");
    expect(text).not.toContain("Mediana vistas: 0");
    expect(text).not.toContain("Retención: 0");
    expect(text).toContain("Todavía no hay publicaciones ni mediciones");
  });

  test("8 canales ES/EN con su handle y su estado de medición automática (0030)", () => {
    const channels: AnalyticsPlatformSummary[] = [
      { ...platform({ platform: "youtube" }), language: "es", handle: "@elclipdegta6", published_clips: 7, measured_clips: 1, median_views: 65, median_retention: null, has_data: true, auto_status: { status: "sin_permiso", reason: "YouTube: permiso denegado (HTTP 403): el token concedido no incluye youtube.readonly; reautorizar OAuth en Cuentas.", last_measured_at: null }, ranking: [{ title: "IlloJuan borra GTA 6 sin querer en directo", views: 65, retention_pct: null }] },
      { ...platform({ platform: "youtube" }), language: "en", handle: "@gta6clips", published_clips: 4, measured_clips: 0, auto_status: { status: "sin_permiso", reason: "YouTube: permiso denegado (HTTP 403).", last_measured_at: null } },
      { ...platform({ platform: "tiktok" }), language: "es", handle: "@elclipdegta6____bits", published_clips: 3, measured_clips: 0, auto_status: { status: "sin_permiso", reason: "TikTok: la cuenta guardada es vía Zernio.", last_measured_at: null } },
      { ...platform({ platform: "tiktok" }), language: "en", handle: "@elclipdegta6en___", published_clips: 1, measured_clips: 0, auto_status: null },
      { ...platform({ platform: "instagram" }), language: "es", handle: "@elmegaclipgta6", published_clips: 2, measured_clips: 1, median_views: 145, median_retention: 48.8, has_data: true, auto_status: { status: "error", reason: "Instagram: HTTP 400: Unsupported get request.", last_measured_at: null }, ranking: [{ title: "IlloJuan borra GTA 6 sin querer en directo", views: 145, retention_pct: 48.8 }] },
      { ...platform({ platform: "instagram" }), language: "en", handle: null, published_clips: 0, measured_clips: 0, auto_status: null },
      { ...platform({ platform: "facebook" }), language: "es", handle: "Elclipdegta6", published_clips: 2, measured_clips: 0, auto_status: { status: "sin_permiso", reason: "Facebook: sin permiso de insights (código Meta 200).", last_measured_at: null } },
      { ...platform({ platform: "facebook" }), language: "en", handle: null, published_clips: 0, measured_clips: 0, auto_status: null },
    ];
    const text = buildAnalyticsMessage(channels, "2026-11-19");
    expect(text).toContain("ES · Español");
    expect(text).toContain("EN · English");
    for (const h of ["@elclipdegta6", "@gta6clips", "@elclipdegta6____bits", "@elclipdegta6en___", "@elmegaclipgta6", "Elclipdegta6"]) {
      expect(text).toContain(h);
    }
    // ES y EN no se mezclan: YT ES 7, YT EN 4 (antes el resumen
    // decía "Canales ES" con 11).
    expect(text).toContain("YouTube · @elclipdegta6\n  Publicados: 7 · Medidos: 1");
    expect(text).toContain("YouTube · @gta6clips\n  Publicados: 4 · Medidos: 0");
    expect(text).toContain("TikTok · @elclipdegta6____bits\n  Publicados: 3 · Medidos: 0");
    expect(text).toContain("Mediana vistas: 65");
    expect(text).toContain("Mediana vistas: 145");
    expect(text).toContain("Top: «IlloJuan borra GTA 6 sin querer en directo» — 65 vistas");
    // Sin handle verificado: se dice, no se inventa.
    expect(text).toContain("Instagram · sin handle registrado");
    expect(text).toContain("Facebook · sin handle registrado");
    // La medición automática enseña su motivo, no manda a mano.
    expect(text).toContain("Medición automática: sin permiso — YouTube: permiso denegado (HTTP 403)");
    expect(text).toContain("Medición automática: aún no intentada");
    expect(text).not.toContain("a mano");
    expect(text).not.toContain("Canales ES · @elclipdegta6");
  });

  test("con mediciones: medianas y top clip por vistas", () => {
    const yt = platform({
      platform: "youtube",
      median_views: 400,
      median_retention: 55.5,
      measured_clips: 2,
      published_clips: 3,
      has_data: true,
      ranking: [
        { title: "Clip uno", views: 500, retention_pct: 60 },
        { title: "Clip dos", views: 300, retention_pct: 40 },
      ],
    });
    const text = buildAnalyticsMessage([yt, ...allPlatforms().slice(1)], "2026-11-18");
    expect(text).toContain("Publicados: 3 · Medidos: 2");
    expect(text).toContain("Mediana vistas: 400");
    expect(text).toContain("Retención: 55,5 %");
    expect(text).toContain("Top: «Clip uno» — 500 vistas");
    expect(text).not.toContain("Todavía no hay publicaciones");
  });

  test("el top salta las mediciones sin vistas (null)", () => {
    const yt = platform({
      platform: "youtube",
      median_views: null,
      median_retention: 70,
      measured_clips: 2,
      published_clips: 2,
      has_data: true,
      ranking: [
        { title: "Sin vistas", views: null, retention_pct: 70 },
        { title: "Con vistas", views: 120, retention_pct: 50 },
      ],
    });
    const text = buildAnalyticsMessage([yt], "2026-11-18");
    expect(text).toContain("Top: «Con vistas» — 120 vistas");
  });

  test("título larguísimo: el mensaje se trunca a ≤4000 caracteres", () => {
    const yt = platform({
      platform: "youtube",
      median_views: 1,
      median_retention: 1,
      measured_clips: 1,
      published_clips: 1,
      has_data: true,
      ranking: [{ title: "A".repeat(5000), views: 1, retention_pct: 1 }],
    });
    const text = buildAnalyticsMessage([yt], "2026-11-18");
    expect(text.length).toBeLessThanOrEqual(4000);
    expect(text.endsWith("…")).toBe(true);
  });
});

describe("santiagoParts y buildTestMessage", () => {
  test("2026-11-18T12:00Z son las 09:00 en Santiago", () => {
    expect(santiagoParts(new Date("2026-11-18T12:00:00Z"))).toEqual({ day: "2026-11-18", hour: 9, minute: 0 });
  });

  test("el mensaje de prueba lleva fecha y hora de Santiago", () => {
    const text = buildTestMessage("2026-11-18", 9, 5);
    expect(text).toContain("Prueba Telegram AVC — 2026-11-18 09:05 (Santiago)");
    expect(text).toContain("El bot funciona");
  });
});

describe("sendTelegram", () => {
  function envWithSecrets(): Env {
    return {
      TELEGRAM_BOT_TOKEN: "TOKEN123", // SOLO TEST, no es real
    } as Env;
  }

  test("POST a la Bot API con chat_id y texto; el token no va en el cuerpo", async () => {
    const calls: { url: string; body: string }[] = [];
    const stub = (async (url: string, init: { body?: string }) => {
      calls.push({ url, body: init.body ?? "" });
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as unknown as typeof fetch;
    const r = await sendTelegram(envWithSecrets(), "987", "Hola", stub);
    expect(r).toEqual({ ok: true, status: 200 });
    expect(calls.length).toBe(1);
    expect(calls[0]?.url).toBe("https://api.telegram.org/botTOKEN123/sendMessage");
    const body = JSON.parse(calls[0]?.body ?? "{}") as Record<string, unknown>;
    expect(body.chat_id).toBe("987");
    expect(body.text).toBe("Hola");
    expect(calls[0]?.body).not.toContain("TOKEN123");
  });

  test("sin secretos no llama a la red", async () => {
    let called = false;
    const stub = (async () => {
      called = true;
      return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch;
    const r = await sendTelegram({} as Env, "987", "Hola", stub);
    expect(r.ok).toBe(false);
    expect(called).toBe(false);
  });

  test("Telegram 403: ok false con el estado, sin lanzar", async () => {
    const stub = (async () =>
      new Response(JSON.stringify({ ok: false, description: "Forbidden: bot was blocked" }), { status: 403 })) as unknown as typeof fetch;
    const r = await sendTelegram(envWithSecrets(), "987", "Hola", stub);
    expect(r.ok).toBe(false);
    expect(r.status).toBe(403);
    expect(r.error).toContain("403");
  });
});

// --- D1 falso mínimo (solo lo que usan los caminos de Telegram) ---
type Row = Record<string, unknown>;

class FakeD1 {
  tables: Record<string, Row[]> = {};

  seed(table: string, row: Row): void {
    (this.tables[table] ??= []).push({ ...row });
  }

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
    const m = /^SELECT\s+(.+?)\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+?))?(?:\s+ORDER BY\s+(.+?))?\s*$/is.exec(sql);
    if (!m) throw new Error(`SELECT no soportado en el mock: ${sql}`);
    const [, colsRaw, table, where, orderBy] = m as [string, string, string, string | undefined, string | undefined];
    let rows = [...(this.tables[table as string] ?? [])];
    if (where) {
      // Condiciones unidas por AND: `col = ?` (consume un
      // parámetro en orden) o `col IS NOT NULL`. Lo necesitan
      // listPostedJobs (0030) y otras consultas con varios filtros.
      const conds = where.split(/\s+AND\s+/i).map((c) => c.trim());
      let pi = 0;
      for (const c of conds) {
        const eq = /^(\w+)\s*=\s*\?$/.exec(c);
        const isNotNull = /^(\w+)\s+IS\s+NOT\s+NULL$/i.exec(c);
        const eqLit = /^(\w+)\s*=\s*'([^']*)'$/.exec(c);
        if (eq) {
          const col = eq[1] as string;
          const val = params[pi++];
          rows = rows.filter((r) => r[col] === val);
        } else if (isNotNull) {
          const col = isNotNull[1] as string;
          rows = rows.filter((r) => r[col] !== null && r[col] !== undefined);
        } else if (eqLit) {
          const col = eqLit[1] as string;
          const lit = eqLit[2] as string;
          rows = rows.filter((r) => r[col] === lit);
        } else {
          throw new Error(`WHERE no soportado en el mock: ${where}`);
        }
      }
    }
    if (orderBy) {
      const desc = orderBy.toUpperCase().includes("DESC");
      const col = orderBy.split(/\s+/)[0] as string;
      rows = [...rows].sort((a, b) => {
        const av = a[col] as number;
        const bv = b[col] as number;
        if (av === bv && table === "clip_stats") return (b.id as number) - (a.id as number);
        if (av === bv) return 0;
        return desc ? bv - av : av - bv;
      });
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
    if (!insert) throw new Error(`Escritura no soportada en el mock: ${sql}`);
    const [, table, colsRaw] = insert as [string, string, string];
    const cols = colsRaw.split(",").map((c) => c.trim());
    const rows = (this.tables[table as string] ??= []);
    const id = rows.reduce((max, r) => Math.max(max, Number(r.id ?? 0)), 0) + 1;
    const row: Row = { id };
    cols.forEach((c, i) => {
      row[c] = params[i];
    });
    rows.push(row);
    return { results: [], success: true, meta: { changes: 1, last_row_id: id } };
  }
}

class FakeStmt {
  constructor(
    private db: FakeD1,
    private sql: string,
    private params: unknown[] = [],
  ) {}
  bind(...values: unknown[]): FakeStmt {
    return new FakeStmt(this.db, this.sql, values);
  }
  async first<T = Record<string, unknown>>(): Promise<T | null> {
    const rows = this.db.runSelect(this.sql, this.params);
    return (rows[0] as T | undefined) ?? null;
  }
  async all<T = Record<string, unknown>>(): Promise<{ results: T[]; success: boolean; meta: Record<string, unknown> }> {
    return { results: this.db.runSelect(this.sql, this.params) as T[], success: true, meta: {} };
  }
  async run(): Promise<{ results: never[]; success: boolean; meta: { changes?: number; last_row_id?: number } }> {
    return this.db.runWrite(this.sql, this.params);
  }
}

function makeEnv(db: FakeD1, telegram?: { token: string }): Env {
  return {
    DB: db as unknown as Env["DB"],
    TENANT: "sebastian",
    CLIPS_BUCKET: {} as Env["CLIPS_BUCKET"],
    SESSION_SECRET: SECRET,
    CREDENTIAL_ENCRYPTION_KEY: Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"),
    BRIDGE_TOKEN: "bridge",
    GITHUB_TOKEN: "gh",
    TELEGRAM_BOT_TOKEN: telegram?.token,
    ALLOWED_EMAIL: "",
    GITHUB_REPO: "exe2027/viral-clip-agent",
  };
}

const ctx = { waitUntil: () => {}, passThroughOnException: () => {} };

async function tokenFor(username: string, tenant: "sebastian" | "kaez"): Promise<string> {
  return createSessionToken(username, tenant, SECRET);
}

async function call(
  env: Env,
  method: string,
  path: string,
  opts: { token?: string; body?: Record<string, unknown> } = {},
): Promise<{ status: number; json: Record<string, unknown> }> {
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
  return { status: res.status, json: (await res.json()) as Record<string, unknown> };
}

function clipRow(over: Row = {}): Row {
  return {
    id: 1,
    tenant_id: "sebastian",
    title: "Clip de prueba",
    publish_states: JSON.stringify({
      youtube_shorts: { state: "publicado", scheduledFor: null, publishedAt: new Date().toISOString(), note: "", since: "", updatedAt: "" },
    }),
    ...over,
  };
}

describe("GET /api/telegram/status", () => {
  test("sin sesión responde 401", async () => {
    const env = makeEnv(new FakeD1());
    expect((await call(env, "GET", "/api/telegram/status")).status).toBe(401);
  });

  test("con sesión y sin secretos: configured false, sin revelar nada", async () => {
    const env = makeEnv(new FakeD1());
    const r = await call(env, "GET", "/api/telegram/status", { token: await tokenFor("seba", "sebastian") });
    expect(r.status).toBe(200);
    expect(r.json.configured).toBe(false);
    expect(r.json.last_sent_day).toBeNull();
    expect(JSON.stringify(r.json)).not.toContain("TOKEN");
  });

  test("con secretos: configured true, sin vínculo todavía", async () => {
    const env = makeEnv(new FakeD1(), { token: "TOKEN123" });
    const r = await call(env, "GET", "/api/telegram/status", { token: await tokenFor("seba", "sebastian") });
    expect(r.status).toBe(200);
    expect(r.json.configured).toBe(true);
    expect(r.json.linked).toBe(false);
    expect(r.json.chat_id_masked).toBeNull();
    expect(JSON.stringify(r.json)).not.toContain("TOKEN123");
  });
});

describe("POST /api/telegram/test", () => {
  test("sin sesión responde 401", async () => {
    const env = makeEnv(new FakeD1());
    expect((await call(env, "POST", "/api/telegram/test", { body: {} })).status).toBe(401);
  });

  test("sin vínculo: 400 con el motivo y la vista previa del mensaje", async () => {
    const env = makeEnv(new FakeD1());
    const r = await call(env, "POST", "/api/telegram/test", { token: await tokenFor("seba", "sebastian"), body: {} });
    expect(r.status).toBe(400);
    expect(String(r.json.error)).toContain("vinculado");
    expect(String(r.json.preview)).toContain("Prueba Telegram AVC");
  });

  test("con secretos y datos reales: envía el resumen con la mediana", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow({ id: 1, title: "Clip uno" }));
    db.seed("clips", clipRow({ id: 2, title: "Clip dos" }));
    db.seed("clip_stats", { id: 1, tenant_id: "sebastian", clip_id: 1, platform: "youtube", views: 500, retention_pct: 60, shares: null, saves: null, comments: null, new_subs: null, window_label: "24h", recorded_at: 2000 });
    db.seed("clip_stats", { id: 2, tenant_id: "sebastian", clip_id: 2, platform: "youtube", views: 300, retention_pct: 40, shares: null, saves: null, comments: null, new_subs: null, window_label: "7d", recorded_at: 1500 });
    db.seed("telegram_links", { chat_id: "987", tenant_id: "sebastian", linked_at: 1000, last_sent_day: null });
    const env = makeEnv(db, { token: "TOKEN123" });
    const calls: string[] = [];
    globalThis.fetch = (async (_url: string, init: { body?: string }) => {
      calls.push(init.body ?? "");
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as unknown as typeof fetch;
    const r = await call(env, "POST", "/api/telegram/test", { token: await tokenFor("seba", "sebastian"), body: {} });
    expect(r.status).toBe(200);
    expect(r.json.sent).toBe(true);
    expect(String(r.json.preview)).toContain("Mediana vistas: 400");
    expect(String(r.json.preview)).toContain("Top: «Clip uno» — 500 vistas");
    expect(calls.length).toBe(1);
    const sent = JSON.parse(calls[0] ?? "{}") as Record<string, unknown>;
    expect(sent.chat_id).toBe("987");
    expect(sent.text).toBe(r.json.preview);
  });

  test("con secretos pero Telegram falla: 502 y no se marca como enviado", async () => {
    const db = new FakeD1();
    db.seed("telegram_links", { chat_id: "987", tenant_id: "sebastian", linked_at: 1000, last_sent_day: null });
    const env = makeEnv(db, { token: "TOKEN123" });
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ ok: false, description: "Forbidden" }), { status: 403 })) as unknown as typeof fetch;
    const r = await call(env, "POST", "/api/telegram/test", { token: await tokenFor("seba", "sebastian"), body: {} });
    expect(r.status).toBe(502);
    expect(String(r.json.error)).toContain("403");
    expect(String(r.json.preview)).toContain("Prueba Telegram AVC");
  });
});

describe("GET /api/analytics por canal (0030)", () => {
  test("8 canales: publicados desde jobs posted, handle desde accounts, medidos por idioma del clip", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow({ id: 1, title: "Clip uno", language: "es" }));
    db.seed("clips", clipRow({ id: 2, title: "Clip two", language: "en" }));
    db.seed("jobs", { id: 1, tenant_id: "sebastian", kind: "publish", clip_id: 1, platform: "youtube_shorts", language: "es", state: "posted", platform_id: "aaa", evidence_url: "https://www.youtube.com/shorts/aaa", updated_at: 3000 });
    db.seed("jobs", { id: 2, tenant_id: "sebastian", kind: "publish", clip_id: 2, platform: "youtube_shorts", language: "en", state: "posted", platform_id: "bbb", evidence_url: "https://www.youtube.com/shorts/bbb", updated_at: 3001 });
    db.seed("jobs", { id: 3, tenant_id: "sebastian", kind: "publish", clip_id: 1, platform: "tiktok", language: "es", state: "posted", platform_id: "zz", evidence_url: "https://www.tiktok.com/@elclipdegta6____bits/video/777", updated_at: 3002 });
    // Un draft no cuenta como publicado.
    db.seed("jobs", { id: 4, tenant_id: "sebastian", kind: "publish", clip_id: 2, platform: "tiktok", language: "en", state: "draft", platform_id: null, evidence_url: null, updated_at: 3003 });
    db.seed("clip_stats", { id: 1, tenant_id: "sebastian", clip_id: 1, platform: "youtube", views: 500, retention_pct: 60, shares: null, saves: null, comments: null, new_subs: null, window_label: "7d", recorded_at: 2000 });
    db.seed("accounts", { id: 1, tenant_id: "sebastian", platform: "youtube", language: "es", label: "YouTube ES", handle: "@elclipdegta6", status: "conectada", note: "", sort: 1 });
    db.seed("accounts", { id: 2, tenant_id: "sebastian", platform: "youtube", language: "en", label: "YouTube EN", handle: "@gta6clips", status: "conectada", note: "", sort: 2 });
    db.seed("accounts", { id: 3, tenant_id: "sebastian", platform: "tiktok", language: "es", label: "TikTok ES", handle: "@elclipdegta6____bits", status: "conectada", note: "", sort: 3 });
    db.seed("accounts", { id: 4, tenant_id: "sebastian", platform: "instagram", language: "es", label: "Instagram ES", handle: "@elmegaclipgta6", status: "conectada", note: "", sort: 5 });
    db.seed("analytics_status", { tenant_id: "sebastian", platform: "youtube", status: "sin_permiso", reason: "YouTube: permiso denegado (HTTP 403).", last_measured_at: null, updated_at: 1000 });
    const env = makeEnv(db);
    const r = await call(env, "GET", "/api/analytics", { token: await tokenFor("seba", "sebastian") });
    expect(r.status).toBe(200);
    const channels = r.json.channels as Record<string, unknown>[];
    expect(channels.length).toBe(8);
    const ch = (platform: string, language: string) =>
      channels.find((c) => c.platform === platform && c.language === language) as Record<string, unknown>;
    expect(ch("youtube", "es").published_clips).toBe(1);
    expect(ch("youtube", "es").measured_clips).toBe(1);
    expect(ch("youtube", "es").median_views).toBe(500);
    expect(ch("youtube", "es").handle).toBe("@elclipdegta6");
    expect(ch("youtube", "en").published_clips).toBe(1);
    expect(ch("youtube", "en").measured_clips).toBe(0);
    expect(ch("youtube", "en").handle).toBe("@gta6clips");
    expect(ch("tiktok", "es").published_clips).toBe(1);
    expect(ch("tiktok", "es").handle).toBe("@elclipdegta6____bits");
    expect(ch("tiktok", "en").published_clips).toBe(0);
    expect(ch("instagram", "es").published_clips).toBe(0);
    expect(ch("instagram", "es").handle).toBe("@elmegaclipgta6");
    // Sin fila en accounts: null, nunca un handle inventado.
    expect(ch("instagram", "en").handle).toBeNull();
    expect(ch("facebook", "en").handle).toBeNull();
    // auto_status viaja por canal y por plataforma.
    expect((ch("youtube", "es").auto_status as Record<string, unknown>).status).toBe("sin_permiso");
    const platforms = r.json.platforms as Record<string, unknown>[];
    const yt = platforms.find((p) => p.platform === "youtube") as Record<string, unknown>;
    expect(yt.published_clips).toBe(2);
    expect(yt.measured_clips).toBe(1);
    // Sin medición: el clip EN de YouTube y el ES de TikTok.
    const unmeasured = r.json.unmeasured as Record<string, unknown>[];
    expect(unmeasured.some((u) => u.clip_id === 2 && u.platform === "youtube" && u.language === "en")).toBe(true);
    expect(unmeasured.some((u) => u.clip_id === 1 && u.platform === "tiktok" && u.language === "es")).toBe(true);
    expect(unmeasured.some((u) => u.clip_id === 1 && u.platform === "youtube")).toBe(false);
  });
});
