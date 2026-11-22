// Tests de Telegram v2 multi-tenant (2026-11-18): vinculación por
// código de un solo uso, webhook con secreto, y envío diario por
// tenant sin mezclar datos. Sin red, sin D1 real: D1 falso con las
// consultas que usan estos caminos (settings, clip_stats, clips,
// revoked_tokens, worker_events, telegram_links,
// telegram_link_codes). La sesión se crea de verdad con
// createSessionToken. Los chat_id y códigos aquí son FALSOS, solo
// de test; ningún secreto real aparece en este archivo.
import { afterEach, describe, expect, test } from "bun:test";
import { createSessionToken } from "../src/auth";
import type { Env } from "../src/cloudflare";
import worker, { maybeAlertWorkerStopped, maybeSendDailyTelegram } from "../src/index";
import { detectStalledJobs, markFailed, notify } from "../src/jobs";
import {
  generateLinkCode,
  linkInstructionsMessage,
  maskChatId,
  parseLinkCode,
  parseTelegramUpdate,
  sha256Hex,
} from "../src/telegram";

const SECRET = Buffer.from("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb").toString("base64"); // SOLO TEST
const WEBHOOK_SECRET = "webhook-secreto-de-test"; // SOLO TEST
const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
});

// --- D1 falso (SELECT con AND, INSERT, upsert settings, DELETE, UPDATE) ---
type Row = Record<string, unknown>;

function matchWhere(row: Row, where: string | undefined, params: unknown[]): boolean {
  if (!where) return true;
  const conds = where.trim().split(/\s+AND\s+/i);
  // Los `?` se consumen en orden: las condiciones literales (sin
  // `?`) no consumen parámetro, así que el índice no es el de la
  // condición sino el del `?` que le toca.
  let paramIndex = 0;
  return conds.every((c) => {
    const trimmed = c.trim();
    // `col IN ('a','b')`: lista literal (la usa listQueueClips).
    const inLit = /^(\w+)\s+IN\s*\(([^)]+)\)$/i.exec(trimmed);
    if (inLit) {
      const vals = (inLit[2] as string).split(",").map((v) => v.trim().replace(/^'(.*)'$/, "$1"));
      return vals.includes(String(row[inLit[1] as string]));
    }
    // `col = 'literal'` (p. ej. kind = 'milestone').
    const eqLit = /^(\w+)\s*=\s*'([^']*)'$/.exec(trimmed);
    if (eqLit) return row[eqLit[1] as string] === eqLit[2];
    // `col = 0` (literal numérico, p. ej. archived = 0).
    const eqNum = /^(\w+)\s*=\s*(\d+)$/.exec(trimmed);
    if (eqNum) return row[eqNum[1] as string] === Number(eqNum[2]);
    const m = /^(\w+)\s*(=|<=|>=|<|>)\s*\?$/.exec(trimmed);
    if (!m) throw new Error(`WHERE no soportado en el mock: ${c}`);
    const v = row[m[1] as string] as number;
    const p = params[paramIndex++] as number;
    switch (m[2]) {
      case "=":
        return v === p;
      case "<=":
        return v <= p;
      case ">=":
        return v >= p;
      case "<":
        return v < p;
      case ">":
        return v > p;
      default:
        return false;
    }
  });
}

class FakeD1 {
  tables: Record<string, Row[]> = {};

  seed(table: string, row: Row): void {
    (this.tables[table] ??= []).push({ ...row });
  }

  prepare(sql: string): FakeStmt {
    return new FakeStmt(this, sql);
  }
  // A2: batch ejecuta las sentencias en orden, como la D1 real. Antes
  // era un stub que devolvía [] sin ejecutar nada: el código que
  // agrupa escrituras en batch (createTelegramStopConfirmation,
  // moveQueueClip) parecía funcionar pero no escribía.
  async batch(statements: FakeStmt[]): Promise<Array<{ results: never[]; success: boolean; meta: { changes?: number; last_row_id?: number } }>> {
    const out = [];
    for (const s of statements) out.push(await s.run());
    return out;
  }
  async exec(): Promise<{ results: never[]; success: boolean; meta: Record<string, unknown> }> {
    return { results: [], success: true, meta: {} };
  }

  runSelect(sql: string, params: unknown[]): Row[] {
    const m = /^SELECT\s+(.+?)\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+?))?(?:\s+ORDER BY\s+(.+?))?\s*$/is.exec(sql);
    if (!m) throw new Error(`SELECT no soportado en el mock: ${sql}`);
    const [, colsRaw, table, where, orderBy] = m as [string, string, string, string | undefined, string | undefined];
    let rows = [...(this.tables[table as string] ?? [])].filter((r) => matchWhere(r, where, params));
    if (orderBy) {
      const specs = orderBy.split(",").map((s) => {
        const parts = s.trim().split(/\s+/);
        return { col: parts[0] as string, desc: (parts[1] ?? "").toUpperCase() === "DESC" };
      });
      rows = [...rows].sort((a, b) => {
        for (const { col, desc } of specs) {
          const av = a[col] as number;
          const bv = b[col] as number;
          if (av === bv) continue;
          return desc ? bv - av : av - bv;
        }
        return 0;
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
    const insert = /^INSERT INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)/is.exec(sql);
    if (insert) {
      const [, table, colsRaw] = insert as [string, string, string];
      const cols = colsRaw.split(",").map((c) => c.trim());
      const rows = (this.tables[table as string] ??= []);
      if (/ON CONFLICT/i.test(sql)) {
        // Upsert (settings): la clave de conflicto es la primera columna.
        const existing = rows.find((r) => r[cols[0] as string] === params[0]);
        if (existing) {
          const setPart = /DO UPDATE SET\s+(.+)$/is.exec(sql)?.[1] ?? "";
          for (const assign of setPart.split(",")) {
            const am = /^\s*(\w+)\s*=\s*excluded\.(\w+)\s*$/.exec(assign.trim());
            if (!am) continue;
            existing[am[1] as string] = params[cols.indexOf(am[2] as string)];
          }
          return { results: [], success: true, meta: { changes: 1 } };
        }
      }
      const id = rows.reduce((max, r) => Math.max(max, Number(r.id ?? 0)), 0) + 1;
      const row: Row = { id };
      cols.forEach((c, i) => {
        row[c] = params[i];
      });
      rows.push(row);
      return { results: [], success: true, meta: { changes: 1, last_row_id: id } };
    }
    const del = /^DELETE FROM\s+(\w+)(?:\s+WHERE\s+(.+))?$/is.exec(sql);
    if (del) {
      const [, table, where] = del as [string, string, string | undefined];
      const rows = this.tables[table as string] ?? [];
      const kept = rows.filter((r) => !matchWhere(r, where, params));
      const changes = rows.length - kept.length;
      this.tables[table as string] = kept;
      return { results: [], success: true, meta: { changes } };
    }
    const upd = /^UPDATE\s+(\w+)\s+SET\s+(.+?)\s+WHERE\s+(.+)$/is.exec(sql);
    if (upd) {
      const [, table, setRaw, where] = upd as [string, string, string, string];
      // Las asignaciones pueden ser `col = ?` (consume un
      // parámetro), `col = 'literal'`, `col = NULL` o `col = 0`:
      // los literales NO consumen parámetro, así que el WHERE
      // recibe solo los parámetros restantes, en orden.
      let p = 0;
      const assigns: { col: string; value: unknown }[] = [];
      for (const part of setRaw.split(",")) {
        const t = part.trim();
        let am = /^(\w+)\s*=\s*\?$/.exec(t);
        if (am) {
          assigns.push({ col: am[1] as string, value: params[p++] });
          continue;
        }
        am = /^(\w+)\s*=\s*'([^']*)'$/.exec(t);
        if (am) {
          assigns.push({ col: am[1] as string, value: am[2] });
          continue;
        }
        am = /^(\w+)\s*=\s*NULL$/i.exec(t);
        if (am) {
          assigns.push({ col: am[1] as string, value: null });
          continue;
        }
        am = /^(\w+)\s*=\s*(\d+)$/.exec(t);
        if (am) {
          assigns.push({ col: am[1] as string, value: Number(am[2]) });
          continue;
        }
        throw new Error(`SET no soportado en el mock: ${t}`);
      }
      const rows = this.tables[table as string] ?? [];
      let changes = 0;
      for (const r of rows) {
        if (!matchWhere(r, where, params.slice(p))) continue;
        for (const { col, value } of assigns) r[col] = value;
        changes++;
      }
      return { results: [], success: true, meta: { changes } };
    }
    throw new Error(`Escritura no soportada en el mock: ${sql}`);
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

function makeEnv(db: FakeD1, opts: { token?: string; webhookSecret?: string; over?: Partial<Env> } = {}): Env {
  return {
    DB: db as unknown as Env["DB"],
    TENANT: "sebastian",
    CLIPS_BUCKET: {} as Env["CLIPS_BUCKET"],
    SESSION_SECRET: SECRET,
    CREDENTIAL_ENCRYPTION_KEY: Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"),
    BRIDGE_TOKEN: "bridge",
    GITHUB_TOKEN: "gh",
    TELEGRAM_BOT_TOKEN: opts.token ?? "TOKEN123", // SOLO TEST
    TELEGRAM_WEBHOOK_SECRET: "webhookSecret" in opts ? opts.webhookSecret : WEBHOOK_SECRET,
    ALLOWED_EMAIL: "",
    GITHUB_REPO: "exe2027/viral-clip-agent",
    ...(opts.over ?? {}),
  };
}

const ctx = { waitUntil: () => {}, passThroughOnException: () => {} };

async function tokenFor(username: string, tenant: "sebastian" | "kaez" | "kaez"): Promise<string> {
  return createSessionToken(username, tenant, SECRET);
}

async function call(
  env: Env,
  method: string,
  path: string,
  opts: { token?: string; body?: Record<string, unknown>; headers?: Record<string, string> } = {},
): Promise<{ status: number; json: Record<string, unknown> }> {
  const headers: Record<string, string> = { ...(opts.headers ?? {}) };
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

function clipRow(tenant: string, over: Row = {}): Row {
  return {
    id: 1,
    tenant_id: tenant,
    title: "Clip de prueba",
    language: "es",
    publish_states: JSON.stringify({
      youtube_shorts: { state: "publicado", scheduledFor: null, publishedAt: new Date().toISOString(), note: "", since: "", updatedAt: "" },
    }),
    ...over,
  };
}

function webhookHeaders(): Record<string, string> {
  return { "X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET };
}

function webhookBody(chatId: string, text: string): Record<string, unknown> {
  return { message: { chat: { id: Number(chatId) }, text } };
}

describe("helpers de vinculación (funciones puras)", () => {
  test("sha256Hex: vector conocido, el código nunca se guarda en claro", async () => {
    expect(await sha256Hex("abc")).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    expect(await sha256Hex("123456")).not.toContain("123456");
  });

  test("generateLinkCode: 6 dígitos, varía entre llamadas", () => {
    const codes = new Set(Array.from({ length: 5 }, () => generateLinkCode()));
    for (const c of codes) expect(c).toMatch(/^\d{6}$/);
    expect(codes.size).toBeGreaterThan(1);
  });

  test("maskChatId: solo los últimos 4 dígitos", () => {
    expect(maskChatId("1234567890")).toBe("•••7890");
    expect(maskChatId("12")).toBe("•••");
  });

  test("parseLinkCode: /link y /start con código, nada más", () => {
    expect(parseLinkCode("/link 123456")).toBe("123456");
    expect(parseLinkCode("/start 123456")).toBe("123456");
    expect(parseLinkCode("/start")).toBeNull();
    expect(parseLinkCode("/link abc")).toBeNull();
    expect(parseLinkCode("123456")).toBeNull();
  });

  test("parseTelegramUpdate: solo chat y texto; sin mensaje, null", () => {
    expect(parseTelegramUpdate({ message: { chat: { id: 42 }, text: "hola" } })).toEqual({ chatId: "42", text: "hola" });
    expect(parseTelegramUpdate({})).toBeNull();
    expect(parseTelegramUpdate(null)).toBeNull();
    expect(parseTelegramUpdate({ message: { text: "sin chat" } })).toBeNull();
  });
});

describe("POST /api/telegram/link-code", () => {
  test("sin sesión responde 401", async () => {
    const env = makeEnv(new FakeD1());
    expect((await call(env, "POST", "/api/telegram/link-code", { body: {} })).status).toBe(401);
  });

  test("con sesión: devuelve un código de 6 dígitos y guarda solo su hash", async () => {
    const db = new FakeD1();
    const env = makeEnv(db);
    const r = await call(env, "POST", "/api/telegram/link-code", { token: await tokenFor("seba", "sebastian"), body: {} });
    expect(r.status).toBe(200);
    expect(String(r.json.code)).toMatch(/^\d{6}$/);
    expect(Number(r.json.expires_at)).toBeGreaterThan(Date.now());
    expect(Number(r.json.expires_at) - Date.now()).toBeLessThanOrEqual(10 * 60 * 1000 + 1000);
    const rows = db.tables.telegram_link_codes ?? [];
    expect(rows.length).toBe(1);
    expect(rows[0]?.tenant_id).toBe("sebastian");
    expect(rows[0]?.code_hash).toBe(await sha256Hex(String(r.json.code)));
    expect(JSON.stringify(rows)).not.toContain(String(r.json.code));
  });

  test("un segundo código invalida el primero", async () => {
    const db = new FakeD1();
    const env = makeEnv(db);
    const token = await tokenFor("seba", "sebastian");
    const first = await call(env, "POST", "/api/telegram/link-code", { token, body: {} });
    await call(env, "POST", "/api/telegram/link-code", { token, body: {} });
    expect(db.tables.telegram_link_codes?.length).toBe(1);
    expect(db.tables.telegram_link_codes?.[0]?.code_hash).not.toBe(await sha256Hex(String(first.json.code)));
  });
});

describe("POST /api/telegram/webhook", () => {
  test("sin secreto: 403; con secreto incorrecto: 403", async () => {
    const env = makeEnv(new FakeD1());
    expect((await call(env, "POST", "/api/telegram/webhook", { body: webhookBody("1111111111", "hola") })).status).toBe(403);
    expect(
      (await call(env, "POST", "/api/telegram/webhook", {
        body: webhookBody("1111111111", "hola"),
        headers: { "X-Telegram-Bot-Api-Secret-Token": "incorrecto" },
      })).status,
    ).toBe(403);
  });

  test("sin TELEGRAM_WEBHOOK_SECRET configurado: 403 aunque el header coincida con nada", async () => {
    const env = makeEnv(new FakeD1(), { webhookSecret: undefined });
    expect(
      (await call(env, "POST", "/api/telegram/webhook", { body: webhookBody("1111111111", "hola"), headers: webhookHeaders() }))
        .status,
    ).toBe(403);
  });

  test("flujo completo: /start <código> vincula; el código no se puede reutilizar", async () => {
    const db = new FakeD1();
    const env = makeEnv(db);
    const replies: string[] = [];
    globalThis.fetch = (async (_url: string, init: { body?: string }) => {
      replies.push((JSON.parse(init.body ?? "{}") as Record<string, unknown>).text as string);
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as unknown as typeof fetch;
    const token = await tokenFor("seba", "sebastian");
    const code = (await call(env, "POST", "/api/telegram/link-code", { token, body: {} })).json.code as string;
    const r = await call(env, "POST", "/api/telegram/webhook", {
      body: webhookBody("1111111111", `/start ${code}`),
      headers: webhookHeaders(),
    });
    expect(r.status).toBe(200);
    expect(replies[0]).toContain("Vinculado");
    expect(replies[0]).toContain("sebastian");
    const status = await call(env, "GET", "/api/telegram/status", { token });
    expect(status.json.linked).toBe(true);
    expect(status.json.chat_id_masked).toBe("•••1111");
    // El código se borró al usarse: reutilizarlo no vincula otro chat.
    replies.length = 0;
    await call(env, "POST", "/api/telegram/webhook", {
      body: webhookBody("2222222222", `/link ${code}`),
      headers: webhookHeaders(),
    });
    expect(replies[0]).toMatch(/no vale|caducó/);
    // Una sesión de kaez en el Worker de sebastian no vale (401):
    // el vínculo es de sebastian y este Worker solo sirve a sebastian.
    const other = await call(env, "GET", "/api/telegram/status", { token: await tokenFor("cole", "kaez") });
    expect(other.status).toBe(401);
  });

  test("código expirado no vincula", async () => {
    const db = new FakeD1();
    const env = makeEnv(db);
    db.seed("telegram_link_codes", {
      code_hash: await sha256Hex("999999"),
      tenant_id: "sebastian",
      expires_at: Date.now() - 1000,
      created_at: Date.now() - 11 * 60 * 1000,
    });
    const replies: string[] = [];
    globalThis.fetch = (async (_url: string, init: { body?: string }) => {
      replies.push((JSON.parse(init.body ?? "{}") as Record<string, unknown>).text as string);
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as unknown as typeof fetch;
    const r = await call(env, "POST", "/api/telegram/webhook", {
      body: webhookBody("1111111111", "/link 999999"),
      headers: webhookHeaders(),
    });
    expect(r.status).toBe(200);
    expect(replies[0]).toContain("caducó");
    const status = await call(env, "GET", "/api/telegram/status", { token: await tokenFor("seba", "sebastian") });
    expect(status.json.linked).toBe(false);
  });

  test("código incorrecto no vincula", async () => {
    const db = new FakeD1();
    const env = makeEnv(db);
    const replies: string[] = [];
    globalThis.fetch = (async (_url: string, init: { body?: string }) => {
      replies.push((JSON.parse(init.body ?? "{}") as Record<string, unknown>).text as string);
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as unknown as typeof fetch;
    await call(env, "POST", "/api/telegram/webhook", {
      body: webhookBody("1111111111", "/link 000000"),
      headers: webhookHeaders(),
    });
    expect(replies[0]).toMatch(/no vale|caducó/);
    const status = await call(env, "GET", "/api/telegram/status", { token: await tokenFor("seba", "sebastian") });
    expect(status.json.linked).toBe(false);
  });

  test("código de otro tenant no vincula ni se borra", async () => {
    const db = new FakeD1();
    const env = makeEnv(db);
    db.seed("telegram_link_codes", {
      code_hash: await sha256Hex("123456"),
      tenant_id: "kaez",
      expires_at: Date.now() + 10 * 60_000,
      created_at: Date.now(),
    });
    const replies: string[] = [];
    globalThis.fetch = (async (_url: string, init: { body?: string }) => {
      replies.push((JSON.parse(init.body ?? "{}") as Record<string, unknown>).text as string);
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as unknown as typeof fetch;
    const r = await call(env, "POST", "/api/telegram/webhook", {
      body: webhookBody("1111111111", "/link 123456"),
      headers: webhookHeaders(),
    });
    expect(r.status).toBe(200);
    expect(replies[0]).toMatch(/no vale|caducó/);
    // El código NO se borró (no es de este Worker) y no se vinculó nada.
    expect((db.tables.telegram_link_codes ?? []).length).toBe(1);
    expect(db.tables.telegram_links ?? []).toEqual([]);
  });

  test("chat no vinculado que escribe cualquier cosa: solo instrucciones, nunca datos", async () => {
    const db = new FakeD1();
    db.seed("clips", clipRow("sebastian", { id: 1, title: "Clip secreto" }));
    db.seed("clip_stats", { id: 1, tenant_id: "sebastian", clip_id: 1, platform: "youtube", views: 500, retention_pct: 60, shares: null, saves: null, comments: null, new_subs: null, window_label: "24h", recorded_at: 2000 });
    const env = makeEnv(db);
    const replies: string[] = [];
    globalThis.fetch = (async (_url: string, init: { body?: string }) => {
      replies.push((JSON.parse(init.body ?? "{}") as Record<string, unknown>).text as string);
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as unknown as typeof fetch;
    await call(env, "POST", "/api/telegram/webhook", {
      body: webhookBody("3333333333", "dame mis analíticas"),
      headers: webhookHeaders(),
    });
    expect(replies.length).toBe(1);
    expect(replies[0]).toContain("Ajustes → Telegram");
    expect(replies[0]).not.toContain("Clip secreto");
    expect(replies[0]).not.toContain("500");
    // El mensaje SÍ dice que nunca pedirá la contraseña: eso es la
    // garantía de seguridad, no una petición de credenciales.
    expect(replies[0]).toContain("Nunca te pediré tu contraseña");
    expect(replies[0]).not.toContain("Analíticas GTA 6");
  });

  test("/unlink desvincula y el estado vuelve a linked false", async () => {
    const db = new FakeD1();
    db.seed("telegram_links", { chat_id: "1111111111", tenant_id: "sebastian", linked_at: 1000, last_sent_day: null });
    const env = makeEnv(db);
    const replies: string[] = [];
    globalThis.fetch = (async (_url: string, init: { body?: string }) => {
      replies.push((JSON.parse(init.body ?? "{}") as Record<string, unknown>).text as string);
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as unknown as typeof fetch;
    await call(env, "POST", "/api/telegram/webhook", {
      body: webhookBody("1111111111", "/unlink"),
      headers: webhookHeaders(),
    });
    expect(replies[0]).toContain("Desvinculado");
    const status = await call(env, "GET", "/api/telegram/status", { token: await tokenFor("seba", "sebastian") });
    expect(status.json.linked).toBe(false);
  });
});

describe("POST /api/telegram/unlink (desde Ajustes)", () => {
  test("sin sesión 401; con sesión desvincula solo a su tenant", async () => {
    const db = new FakeD1();
    db.seed("telegram_links", { chat_id: "1111111111", tenant_id: "sebastian", linked_at: 1000, last_sent_day: null });
    db.seed("telegram_links", { chat_id: "2222222222", tenant_id: "kaez", linked_at: 1000, last_sent_day: null });
    const env = makeEnv(db);
    expect((await call(env, "POST", "/api/telegram/unlink", { body: {} })).status).toBe(401);
    const r = await call(env, "POST", "/api/telegram/unlink", { token: await tokenFor("seba", "sebastian"), body: {} });
    expect(r.status).toBe(200);
    expect(r.json.linked).toBe(false);
    // El unlink de sebastian no tocó el vínculo de kaez (sigue en D1).
    expect((db.tables.telegram_links ?? []).some((l) => l.tenant_id === "kaez")).toBe(true);
    expect((db.tables.telegram_links ?? []).some((l) => l.tenant_id === "sebastian")).toBe(false);
    // Y una sesión de kaez en este Worker da 401.
    expect((await call(env, "GET", "/api/telegram/status", { token: await tokenFor("cole", "kaez") })).status).toBe(401);
  });
});

describe("envío diario por tenant (maybeSendDailyTelegram)", () => {
  function seedTenant(db: FakeD1, tenant: string, chatId: string, title: string, views: number): void {
    db.seed("clips", clipRow(tenant, { id: tenant === "sebastian" ? 1 : 2, title }));
    db.seed("clip_stats", {
      id: tenant === "sebastian" ? 1 : 2,
      tenant_id: tenant,
      clip_id: tenant === "sebastian" ? 1 : 2,
      platform: "youtube",
      views,
      retention_pct: 50,
      shares: null,
      saves: null,
      comments: null,
      new_subs: null,
      window_label: "24h",
      recorded_at: 2000,
    });
    db.seed("telegram_links", { chat_id: chatId, tenant_id: tenant, linked_at: 1000, last_sent_day: null });
  }

  test("cada tenant recibe SOLO sus analíticas en SU chat; segundo envío el mismo día no repite", async () => {
    const db = new FakeD1();
    seedTenant(db, "sebastian", "1111111111", "Clip de Seba", 500);
    seedTenant(db, "kaez", "2222222222", "Clip de Kaez", 700);
    const env = makeEnv(db);
    const sent: { chat_id: string; text: string }[] = [];
    globalThis.fetch = (async (_url: string, init: { body?: string }) => {
      sent.push(JSON.parse(init.body ?? "{}") as { chat_id: string; text: string });
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as unknown as typeof fetch;
    const nineAm = Date.parse("2026-11-18T12:00:00Z"); // 09:00 Santiago
    await maybeSendDailyTelegram(env, "sebastian", nineAm);
    await maybeSendDailyTelegram(env, "kaez", nineAm);
    expect(sent.length).toBe(2);
    const toSeba = sent.find((s) => s.chat_id === "1111111111");
    const toKaez = sent.find((s) => s.chat_id === "2222222222");
    expect(toSeba?.text).toContain("Clip de Seba");
    expect(toSeba?.text).toContain("500 vistas");
    expect(toSeba?.text).not.toContain("Clip de Kaez");
    expect(toSeba?.text).not.toContain("700 vistas");
    expect(toKaez?.text).toContain("Clip de Kaez");
    expect(toKaez?.text).toContain("700 vistas");
    expect(toKaez?.text).not.toContain("Clip de Seba");
    // Deduplicado por tenant+día: repetir la llamada no reenvía.
    await maybeSendDailyTelegram(env, "sebastian", nineAm + 60_000);
    await maybeSendDailyTelegram(env, "kaez", nineAm + 60_000);
    expect(sent.length).toBe(2);
  });

  test("sin vínculo no se envía nada; fuera de las 09:00 tampoco", async () => {
    const db = new FakeD1();
    seedTenant(db, "sebastian", "1111111111", "Clip de Seba", 500);
    const env = makeEnv(db);
    let calls = 0;
    globalThis.fetch = (async () => {
      calls++;
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as unknown as typeof fetch;
    // kaez no tiene vínculo: nada.
    await maybeSendDailyTelegram(env, "kaez", Date.parse("2026-11-18T12:00:00Z"));
    expect(calls).toBe(0);
    // sebastian vinculado pero a las 10:00: nada.
    await maybeSendDailyTelegram(env, "sebastian", Date.parse("2026-11-18T13:00:00Z"));
    expect(calls).toBe(0);
  });
});

describe("comandos v3 por webhook (chat vinculado)", () => {
  function queueClip(tenant: string, over: Row = {}): Row {
    return {
      id: 1,
      tenant_id: tenant,
      title: "Clip en cola",
      score: 80,
      status: "en_carpeta",
      archived: 0,
      origin: "vod",
      language: "es",
      preview_seen: 1,
      publish_blocked: 0,
      viral: JSON.stringify({ viral_score: 52.5, seo_scores: { youtube_shorts: 94, tiktok: 90 } }),
      publish_states: "{}",
      created_at: 1000,
      updated_at: 1000,
      manual_rank: null,
      ...over,
    };
  }

  function seedLinkedQueue(db: FakeD1): void {
    db.seed("telegram_links", { chat_id: "1111111111", tenant_id: "sebastian", linked_at: 1000, last_sent_day: null });
    db.seed("clips", queueClip("sebastian", { id: 1, title: "Cromos coleccionables", score: 90, origin: "live", created_at: 1000 }));
    db.seed("clips", queueClip("sebastian", { id: 2, title: "El coche volador", score: 70, origin: "vod", created_at: 2000 }));
    db.seed("clips", queueClip("sebastian", { id: 3, title: "Sin preview todavía", score: 60, origin: "vod", created_at: 3000, preview_seen: 0 }));
    db.seed("clips", queueClip("sebastian", { id: 4, title: "Sin subtítulos", score: 50, origin: "vod", created_at: 4000, publish_blocked: 1 }));
  }

  function captureReplies(): { chat_id: string; text: string }[] {
    const replies: { chat_id: string; text: string }[] = [];
    globalThis.fetch = (async (_url: string, init: { body?: string }) => {
      replies.push(JSON.parse(init.body ?? "{}") as { chat_id: string; text: string });
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as unknown as typeof fetch;
    return replies;
  }

  async function webhook(env: Env, chatId: string, text: string): Promise<void> {
    const r = await call(env, "POST", "/api/telegram/webhook", {
      body: webhookBody(chatId, text),
      headers: webhookHeaders(),
    });
    expect(r.status).toBe(200);
  }

  test("/cola muestra total, LIVE/VOD, score, viralidad, SEO y frenos", async () => {
    const db = new FakeD1();
    seedLinkedQueue(db);
    const env = makeEnv(db);
    const replies = captureReplies();
    await webhook(env, "1111111111", "/cola");
    expect(replies.length).toBe(1);
    const text = replies[0]?.text ?? "";
    expect(text).toContain("Cola: 4 clips (LIVE 1 · VOD 3)");
    expect(text).toContain("#1 «Cromos coleccionables»");
    expect(text).toContain("LIVE");
    expect(text).toContain("score 90");
    expect(text).toContain("viral 52,5");
    expect(text).toContain("SEO 94");
    expect(text).toContain("falta preview");
    expect(text).toContain("sin subtítulos");
    // LIVE primero aunque tenga más score un VOD: el #1 va antes que el #2.
    expect(text.indexOf("#1")).toBeLessThan(text.indexOf("#2"));
  });

  test("/cola vacía lo dice; /resumen y /top usan solo mediciones reales", async () => {
    const db = new FakeD1();
    db.seed("telegram_links", { chat_id: "1111111111", tenant_id: "sebastian", linked_at: 1000, last_sent_day: null });
    const env = makeEnv(db);
    const replies = captureReplies();
    await webhook(env, "1111111111", "/cola");
    expect(replies[0]?.text).toContain("Cola vacía");
    await webhook(env, "1111111111", "/resumen");
    expect(replies[1]?.text).toContain("Analíticas GTA 6");
    expect(replies[1]?.text).toContain("Sin medición todavía");
    await webhook(env, "1111111111", "/top");
    expect(replies[2]?.text).toContain("Sin medición todavía");
    // Con una medición real, /top la muestra y /resumen la mediana.
    db.seed("clips", clipRow("sebastian", { id: 9, title: "Clip medido" }));
    db.seed("clip_stats", { id: 1, tenant_id: "sebastian", clip_id: 9, platform: "youtube", views: 1200, retention_pct: 55, shares: null, saves: null, comments: null, new_subs: null, window_label: "24h", recorded_at: 2000 });
    await webhook(env, "1111111111", "/top");
    expect(replies[3]?.text).toContain("Clip medido");
    expect(replies[3]?.text).toContain("1.200 vistas");
    await webhook(env, "1111111111", "/resumen");
    expect(replies[4]?.text).toContain("Mediana vistas: 1.200");
  });

  test("/estado: Worker en marcha, fallidos pendientes y último evento", async () => {
    const db = new FakeD1();
    db.seed("telegram_links", { chat_id: "1111111111", tenant_id: "sebastian", linked_at: 1000, last_sent_day: null });
    db.seed("worker_state", { tenant_id: "sebastian", running: 1, started_at: Date.parse("2026-11-18T11:00:00Z"), last_note: "Iniciado desde el panel." });
    db.seed("jobs", { id: 7, tenant_id: "sebastian", kind: "publish", clip_id: 1, platform: "youtube", state: "failed", title: "Fallo", attempts: 4, last_error: "boom", platform_id: null, evidence_url: null, created_at: 1000, updated_at: 1000 });
    db.seed("worker_events", { tenant_id: "sebastian", at: 2000, kind: "aviso", message: "Job #7 falló." });
    const env = makeEnv(db);
    const replies = captureReplies();
    await webhook(env, "1111111111", "/estado");
    const text = replies[0]?.text ?? "";
    expect(text).toContain("Worker en marcha");
    expect(text).toContain("Fallidos pendientes: 1");
    expect(text).toContain("Job #7 falló");
  });

  test("/ayuda lista los comandos; texto libre vinculado responde ayuda, no datos ajenos", async () => {
    const db = new FakeD1();
    seedLinkedQueue(db);
    const env = makeEnv(db);
    const replies = captureReplies();
    await webhook(env, "1111111111", "/ayuda");
    expect(replies[0]?.text).toContain("/resumen");
    expect(replies[0]?.text).toContain("/primero");
    await webhook(env, "1111111111", "hola, ¿qué hay?");
    expect(replies[1]?.text).toContain("/resumen");
    expect(replies[1]?.text).not.toContain("Cromos coleccionables");
  });

  test("chat no vinculado que pide /resumen o /cola: solo instrucciones, nunca datos", async () => {
    const db = new FakeD1();
    seedLinkedQueue(db);
    const env = makeEnv(db);
    const replies = captureReplies();
    await webhook(env, "9999999999", "/resumen");
    await webhook(env, "9999999999", "/cola");
    expect(replies.length).toBe(2);
    for (const r of replies) {
      expect(r.text).toContain("Ajustes → Telegram");
      expect(r.text).not.toContain("Cromos coleccionables");
      expect(r.text).not.toContain("Analíticas GTA 6");
    }
  });

  test("cada tenant ve SOLO su cola (cada chat en SU Worker)", async () => {
    const db = new FakeD1();
    seedLinkedQueue(db);
    db.seed("telegram_links", { chat_id: "2222222222", tenant_id: "kaez", linked_at: 1000, last_sent_day: null });
    db.seed("clips", queueClip("kaez", { id: 10, title: "Clip de Kaez", score: 99, origin: "live", created_at: 1000 }));
    // El chat de kaez en el Worker de sebastian solo recibe instrucciones.
    const env = makeEnv(db);
    const replies = captureReplies();
    await webhook(env, "2222222222", "/cola");
    expect(replies[0]?.text).toContain("Ajustes → Telegram");
    expect(replies[0]?.text).not.toContain("Clip de Kaez");
    // En el Worker de kaez, ese chat ve SOLO su cola.
    const envK = makeEnv(db, { over: { TENANT: "kaez" } });
    const repliesK = captureReplies();
    await webhook(envK, "2222222222", "/cola");
    expect(repliesK[0]?.text).toContain("Clip de Kaez");
    expect(repliesK[0]?.text).not.toContain("Cromos coleccionables");
    expect(repliesK[0]?.chat_id).toBe("2222222222");
  });

  test("/primero, /subir, /bajar y /auto mueven la cola y lo muestran", async () => {
    const db = new FakeD1();
    seedLinkedQueue(db);
    const env = makeEnv(db);
    const replies = captureReplies();
    // El #4 (VOD, score 50) pasa a primero de VOD.
    await webhook(env, "1111111111", "/primero 4");
    expect(replies[0]?.text).toContain("ahora es el primero de VOD");
    expect(replies[0]?.text).toContain("📌 orden manual");
    const queueAfterFirst = replies[0]?.text ?? "";
    expect(queueAfterFirst.indexOf("#4")).toBeLessThan(queueAfterFirst.indexOf("#2"));
    // /auto lo devuelve al orden automático (por score: #2 antes que #4).
    await webhook(env, "1111111111", "/auto 4");
    expect(replies[1]?.text).toContain("vuelve al orden automático");
    expect(replies[1]?.text).not.toContain("📌 orden manual");
    // /subir el #4 (último VOD) lo pone por encima del #3.
    await webhook(env, "1111111111", "/subir 4");
    expect(replies[2]?.text).toContain("ahora es el 2º de 3 en VOD");
    // /bajar el #4 lo devuelve abajo.
    await webhook(env, "1111111111", "/bajar 4");
    expect(replies[3]?.text).toContain("ahora es el 3º de 3 en VOD");
    // Sin id, o con id ajeno/inexistente: no se mueve nada.
    await webhook(env, "1111111111", "/primero");
    expect(replies[4]?.text).toContain("Uso: /primero <id>");
    await webhook(env, "1111111111", "/primero 999");
    expect(replies[5]?.text).toContain("No hay ningún clip #999");
    await webhook(env, "1111111111", "/primero 10");
    expect(replies[6]?.text).toContain("No hay ningún clip #10");
  });

  test("/primero no mueve un clip de otro tenant", async () => {
    const db = new FakeD1();
    seedLinkedQueue(db);
    db.seed("clips", queueClip("kaez", { id: 10, title: "Clip de Kaez", score: 99, origin: "live", created_at: 1000 }));
    const env = makeEnv(db);
    const replies = captureReplies();
    await webhook(env, "1111111111", "/primero 10");
    expect(replies[0]?.text).toContain("No hay ningún clip #10");
    const kaezClip = (db.tables.clips ?? []).find((r) => r.id === 10);
    expect(kaezClip?.manual_rank).toBeNull();
  });
});

describe("notify() re-cableado a la v2 (B2/B3/B7)", () => {
  function captureFetch(): { chat_id: string; text: string }[] {
    const sent: { chat_id: string; text: string }[] = [];
    globalThis.fetch = (async (_url: string, init: { body?: string }) => {
      sent.push(JSON.parse(init.body ?? "{}") as { chat_id: string; text: string });
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as unknown as typeof fetch;
    return sent;
  }

  test("fallo final: una fila en el panel y UN envío solo a SU chat", async () => {
    const db = new FakeD1();
    db.seed("telegram_links", { chat_id: "1111111111", tenant_id: "sebastian", linked_at: 1000, last_sent_day: null });
    db.seed("telegram_links", { chat_id: "2222222222", tenant_id: "kaez", linked_at: 1000, last_sent_day: null });
    const env = makeEnv(db);
    const sent = captureFetch();
    await notify(db, { kind: "failed", clipId: 1, message: "Falló la subida en youtube (clip #1): boom. Revisar en Tracking." }, "sebastian", env);
    expect(sent.length).toBe(1);
    expect(sent[0]?.chat_id).toBe("1111111111");
    expect(sent[0]?.text).toContain("Falló la subida en youtube");
    expect(sent[0]?.text).not.toContain("TOKEN123");
    expect((db.tables.notifications ?? []).length).toBe(1);
    expect(db.tables.notifications?.[0]?.tenant_id).toBe("sebastian");
  });

  test("fallo por límite de frecuencia: el aviso dice que NO se reintente", async () => {
    const db = new FakeD1();
    db.seed("telegram_links", { chat_id: "1111111111", tenant_id: "sebastian", linked_at: 1000, last_sent_day: null });
    const env = makeEnv(db);
    const sent = captureFetch();
    await notify(db, { kind: "failed", clipId: 7, message: "Falló la subida en facebook (clip #7): Limitamos la frecuencia con la que puedes publicar. Revisar en Tracking." }, "sebastian", env);
    expect(sent.length).toBe(1);
    expect(sent[0]?.text).toContain("No reintentes hasta que pase el enfriamiento");
  });

  test("info no se envía por Telegram; sin env o sin vínculo tampoco, pero el panel lo guarda", async () => {
    const db = new FakeD1();
    db.seed("telegram_links", { chat_id: "1111111111", tenant_id: "sebastian", linked_at: 1000, last_sent_day: null });
    const env = makeEnv(db);
    const sent = captureFetch();
    await notify(db, { kind: "info", clipId: 1, message: "Borrador: falta algo." }, "sebastian", env);
    await notify(db, { kind: "failed", clipId: 1, message: "Fallo sin env." }, "sebastian");
    await notify(db, { kind: "failed", clipId: 1, message: "Fallo sin vínculo." }, "kaez", env);
    expect(sent.length).toBe(0);
    expect((db.tables.notifications ?? []).length).toBe(3);
  });

  test("B7 credencial: avisa qué cuenta falló sin exponer el token", async () => {
    const db = new FakeD1();
    db.seed("telegram_links", { chat_id: "1111111111", tenant_id: "sebastian", linked_at: 1000, last_sent_day: null });
    const env = makeEnv(db);
    const sent = captureFetch();
    await notify(db, { kind: "credential", message: "La cuenta YouTube ES dejó de verificar: token expirado." }, "sebastian", env);
    expect(sent.length).toBe(1);
    expect(sent[0]?.chat_id).toBe("1111111111");
    expect(sent[0]?.text).toContain("YouTube ES");
    expect(sent[0]?.text).not.toContain("TOKEN123");
  });

  test("dedup 2026-11-19: la misma alerta de credencial dos veces seguidas avisa UNA sola vez", async () => {
    // El bug: pulsar Verificar dos veces (Meta 00:56 y 00:57, 1 min)
    // mandaba el mismo mensaje dos veces y dejaba 155 sin leer.
    const db = new FakeD1();
    db.seed("telegram_links", { chat_id: "1111111111", tenant_id: "sebastian", linked_at: 1000, last_sent_day: null });
    const env = makeEnv(db);
    const sent = captureFetch();
    const msg = "La cuenta Meta dejó de verificar: Meta aceptó el token, pero la cuenta de Instagram guardada no es accesible.";
    await notify(db, { kind: "credential", message: msg }, "sebastian", env);
    await notify(db, { kind: "credential", message: msg }, "sebastian", env);
    expect(sent.length).toBe(1);
    expect((db.tables.notifications ?? []).length).toBe(1);
  });

  test("dedup 2026-11-19: el mismo aviso 7 h después sí se vuelve a avisar", async () => {
    const db = new FakeD1();
    db.seed("telegram_links", { chat_id: "1111111111", tenant_id: "sebastian", linked_at: 1000, last_sent_day: null });
    db.seed("notifications", { tenant_id: "sebastian", kind: "credential", clip_id: null, message: "La cuenta Meta dejó de verificar: token viejo.", created_at: Date.now() - 7 * 60 * 60 * 1000 });
    const env = makeEnv(db);
    const sent = captureFetch();
    await notify(db, { kind: "credential", message: "La cuenta Meta dejó de verificar: token viejo." }, "sebastian", env);
    expect(sent.length).toBe(1);
    expect((db.tables.notifications ?? []).length).toBe(2);
  });

  test("dedup 2026-11-19: un mensaje distinto sí se avisa (es información nueva)", async () => {
    const db = new FakeD1();
    db.seed("telegram_links", { chat_id: "1111111111", tenant_id: "sebastian", linked_at: 1000, last_sent_day: null });
    const env = makeEnv(db);
    const sent = captureFetch();
    await notify(db, { kind: "credential", message: "La cuenta Meta dejó de verificar: token viejo." }, "sebastian", env);
    await notify(db, { kind: "credential", message: "La cuenta Meta dejó de verificar: cuenta desvinculada." }, "sebastian", env);
    expect(sent.length).toBe(2);
    expect((db.tables.notifications ?? []).length).toBe(2);
  });

  test("markFailed terminal avisa una vez; detectStalledJobs avisa y no repite al re-llamar", async () => {
    const db = new FakeD1();
    db.seed("telegram_links", { chat_id: "1111111111", tenant_id: "sebastian", linked_at: 1000, last_sent_day: null });
    db.seed("clips", clipRow("sebastian", { id: 1, title: "Clip uno", publish_states: "{}", created_at: 1000 }));
    db.seed("jobs", { id: 5, tenant_id: "sebastian", kind: "publish", clip_id: 1, platform: "youtube", state: "processing", attempts: 3, last_error: null, retryable: 1, next_run_at: null, created_at: 1000, updated_at: 1000, run_token: null, slot_day: null });
    const env = makeEnv(db);
    const sent = captureFetch();
    const job = (db.tables.jobs ?? [])[0] as unknown as Parameters<typeof markFailed>[1];
    await markFailed(db, job, "error 500 del servidor", false, env);
    expect(sent.length).toBe(1);
    expect(sent[0]?.text).toContain("Falló la subida en youtube");
    // Atascado: processing desde hace más de 30 min.
    db.seed("jobs", { id: 6, tenant_id: "sebastian", kind: "publish", clip_id: 1, platform: "tiktok", state: "processing", attempts: 0, last_error: null, retryable: 1, next_run_at: null, created_at: 1000, updated_at: Date.now() - 31 * 60 * 1000, run_token: null, slot_day: null });
    const stalled = await detectStalledJobs(db, "sebastian", env);
    expect(stalled.length).toBe(1);
    expect(sent.length).toBe(2);
    expect(sent[1]?.text).toContain("atascado");
    // Segunda pasada: el atascado ya volvió a scheduled, no re-avisa.
    const stalledAgain = await detectStalledJobs(db, "sebastian", env);
    expect(stalledAgain.length).toBe(0);
    expect(sent.length).toBe(2);
  });
});

describe("hito viral B6 (medición manual)", () => {
  test("al pegar 1.500 vistas felicita; repetir no duplica; 12.000 avisa el hito de 10.000", async () => {
    const db = new FakeD1();
    db.seed("telegram_links", { chat_id: "1111111111", tenant_id: "sebastian", linked_at: 1000, last_sent_day: null });
    db.seed("clips", clipRow("sebastian", { id: 1, title: "Clip que explota", publish_states: "{}" }));
    const env = makeEnv(db);
    const sent: { chat_id: string; text: string }[] = [];
    globalThis.fetch = (async (_url: string, init: { body?: string }) => {
      sent.push(JSON.parse(init.body ?? "{}") as { chat_id: string; text: string });
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as unknown as typeof fetch;
    const token = await tokenFor("seba", "sebastian");
    const post = (views: number) =>
      call(env, "POST", "/api/clips/1/stats", { token, body: { platform: "youtube", views, window_label: "24h" } });
    // 500 vistas: sin hito.
    expect((await post(500)).status).toBe(201);
    expect(sent.length).toBe(0);
    // 1.500: hito de 1.000, una sola vez.
    expect((await post(1500)).status).toBe(201);
    expect(sent.length).toBe(1);
    expect(sent[0]?.chat_id).toBe("1111111111");
    expect(sent[0]?.text).toContain("¡Hito viral!");
    expect(sent[0]?.text).toContain("1.500 vistas");
    expect(sent[0]?.text).toContain("YouTube");
    // Otra medición mayor pero sin cruzar 10.000: no repite el hito de 1.000.
    expect((await post(2000)).status).toBe(201);
    expect(sent.length).toBe(1);
    // 12.000: hito de 10.000 (el de 1.000 no se repite).
    expect((await post(12000)).status).toBe(201);
    expect(sent.length).toBe(2);
    expect(sent[1]?.text).toContain("10.000 vistas");
    expect((db.tables.notifications ?? []).filter((r) => r.kind === "milestone").length).toBe(2);
  });
});

describe("control del Worker desde Telegram (v3.1)", () => {
  function seedLinked(db: FakeD1, tenant = "sebastian", chatId = "1111111111"): void {
    db.seed("telegram_links", { chat_id: chatId, tenant_id: tenant, linked_at: 1000, last_sent_day: null });
  }

  function seedWorker(db: FakeD1, tenant: string, running: number): void {
    db.seed("worker_state", { tenant_id: tenant, running, started_at: running ? 1000 : null, last_note: running ? "En marcha." : "Parado." });
  }

  function seedMarker(db: FakeD1, tenant: string, value: number): void {
    db.seed("settings", { tenant_id: tenant, data: JSON.stringify({ telegram_worker_last_running: value }), updated_at: 1000 });
  }

  function markerOf(db: FakeD1, tenant: string): number | null {
    const row = (db.tables.settings ?? []).find((r) => r.tenant_id === tenant);
    if (!row) return null;
    try {
      const data = JSON.parse(String(row.data)) as Record<string, unknown>;
      return typeof data.telegram_worker_last_running === "number" ? data.telegram_worker_last_running : null;
    } catch {
      return null;
    }
  }

  function workerRunning(db: FakeD1, tenant: string): number | undefined {
    return (db.tables.worker_state ?? []).find((r) => r.tenant_id === tenant)?.running as number | undefined;
  }

  function captureReplies(): { chat_id?: string; text?: string; event_type?: string }[] {
    const replies: { chat_id?: string; text?: string; event_type?: string }[] = [];
    globalThis.fetch = (async (_url: string, init: { body?: string }) => {
      replies.push(JSON.parse(init.body ?? "{}") as { chat_id?: string; text?: string; event_type?: string });
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as unknown as typeof fetch;
    return replies;
  }

  // Solo los envíos por Telegram (el mock también captura el dispatch a GitHub).
  function tg(replies: { chat_id?: string; text?: string; event_type?: string }[]): { chat_id: string; text: string }[] {
    return replies.filter((r): r is { chat_id: string; text: string } => typeof r.chat_id === "string");
  }

  function dispatches(replies: { event_type?: string }[]): string[] {
    return replies.filter((r) => typeof r.event_type === "string").map((r) => r.event_type as string);
  }

  async function webhook(env: Env, chatId: string, text: string): Promise<void> {
    const r = await call(env, "POST", "/api/telegram/webhook", {
      body: webhookBody(chatId, text),
      headers: webhookHeaders(),
    });
    expect(r.status).toBe(200);
  }

  test("/iniciar enciende el Worker parado y marca el estado", async () => {
    const db = new FakeD1();
    seedLinked(db);
    seedWorker(db, "sebastian", 0);
    const env = makeEnv(db);
    const replies = captureReplies();
    await webhook(env, "1111111111", "/iniciar");
    const msgs = tg(replies);
    expect(msgs.length).toBe(1);
    expect(msgs[0]?.text).toContain("Worker en marcha");
    expect(dispatches(replies)).toEqual(["panel-start"]);
    expect(workerRunning(db, "sebastian")).toBe(1);
    expect(markerOf(db, "sebastian")).toBe(1);
  });

  test("/iniciar cuando ya está en marcha no hace nada (no re-dispara)", async () => {
    const db = new FakeD1();
    seedLinked(db);
    seedWorker(db, "sebastian", 1);
    const env = makeEnv(db);
    const replies = captureReplies();
    await webhook(env, "1111111111", "/iniciar");
    expect(replies[0]?.text).toContain("ya estaba en marcha");
    expect(workerRunning(db, "sebastian")).toBe(1);
    expect((db.tables.worker_events ?? []).filter((r) => r.kind === "github_dispatch").length).toBe(0);
  });

  test("/parar pide confirmación y NO para sin SÍ", async () => {
    const db = new FakeD1();
    seedLinked(db);
    seedWorker(db, "sebastian", 1);
    const env = makeEnv(db);
    const replies = captureReplies();
    await webhook(env, "1111111111", "/parar");
    expect(replies.length).toBe(1);
    expect(replies[0]?.text).toContain("¿Seguro?");
    expect(replies[0]?.text).toContain("SÍ");
    expect(workerRunning(db, "sebastian")).toBe(1);
    const conf = db.tables.telegram_stop_confirmations ?? [];
    expect(conf.length).toBe(1);
    expect(conf[0]?.tenant_id).toBe("sebastian");
    expect(Number(conf[0]?.expires_at)).toBeGreaterThan(Date.now());
  });

  test("/parar + SÍ para el Worker y no genera alerta duplicada", async () => {
    const db = new FakeD1();
    seedLinked(db);
    seedWorker(db, "sebastian", 1);
    seedMarker(db, "sebastian", 1);
    const env = makeEnv(db);
    const replies = captureReplies();
    await webhook(env, "1111111111", "/parar");
    await webhook(env, "1111111111", "SÍ");
    const msgs = tg(replies);
    expect(msgs.length).toBe(2);
    expect(msgs[1]?.text).toContain("Worker parado");
    // Parar ya NO dispara panel-stop: el flag D1 es la fuente de
    // verdad y el workflow no tiene nada que hacer al parar
    // (2026-11-19, cerebro genérico).
    expect(dispatches(replies)).toEqual([]);
    expect(workerRunning(db, "sebastian")).toBe(0);
    expect((db.tables.telegram_stop_confirmations ?? []).length).toBe(0);
    expect(markerOf(db, "sebastian")).toBe(0);
    expect(msgs.some((r) => r.text.includes("se desactivó"))).toBe(false);
    // El cron ya no ve transición: la marca está a 0.
    await maybeAlertWorkerStopped(env, "sebastian");
    expect(tg(replies).length).toBe(2);
  });

  test("/parar + otro texto cancela; un SÍ suelto después no para", async () => {
    const db = new FakeD1();
    seedLinked(db);
    seedWorker(db, "sebastian", 1);
    const env = makeEnv(db);
    const replies = captureReplies();
    await webhook(env, "1111111111", "/parar");
    await webhook(env, "1111111111", "no, mejor no");
    expect(workerRunning(db, "sebastian")).toBe(1);
    expect((db.tables.telegram_stop_confirmations ?? []).length).toBe(0);
    // La respuesta al "no" es la ayuda, no una parada.
    expect(replies[1]?.text).toContain("/resumen");
    await webhook(env, "1111111111", "SÍ");
    expect(workerRunning(db, "sebastian")).toBe(1);
    expect(replies[2]?.text).not.toContain("Worker parado");
  });

  test("confirmación caducada no para", async () => {
    const db = new FakeD1();
    seedLinked(db);
    seedWorker(db, "sebastian", 1);
    db.seed("telegram_stop_confirmations", { chat_id: "1111111111", tenant_id: "sebastian", expires_at: Date.now() - 1000 });
    const env = makeEnv(db);
    const replies = captureReplies();
    await webhook(env, "1111111111", "SÍ");
    expect(workerRunning(db, "sebastian")).toBe(1);
    expect(replies[0]?.text).not.toContain("Worker parado");
  });

  test("/parar sí como respuesta a la pregunta confirma la parada", async () => {
    const db = new FakeD1();
    seedLinked(db);
    seedWorker(db, "sebastian", 1);
    const env = makeEnv(db);
    const replies = captureReplies();
    await webhook(env, "1111111111", "/parar");
    await webhook(env, "1111111111", "/parar sí");
    expect(tg(replies)[1]?.text).toContain("Worker parado");
    expect(workerRunning(db, "sebastian")).toBe(0);
  });

  test("chat no vinculado no controla nada", async () => {
    const db = new FakeD1();
    seedWorker(db, "sebastian", 1);
    const env = makeEnv(db);
    const replies = captureReplies();
    await webhook(env, "9999999999", "/iniciar");
    await webhook(env, "9999999999", "/parar");
    expect(replies.length).toBe(2);
    for (const r of replies) expect(r.text).toContain("Ajustes → Telegram");
    expect(workerRunning(db, "sebastian")).toBe(1);
    expect((db.tables.telegram_stop_confirmations ?? []).length).toBe(0);
  });

  test("un tenant no para el Worker de otro", async () => {
    const db = new FakeD1();
    seedLinked(db, "sebastian", "1111111111");
    seedWorker(db, "sebastian", 1);
    seedWorker(db, "kaez", 1);
    const env = makeEnv(db);
    const replies = captureReplies();
    await webhook(env, "1111111111", "/parar");
    await webhook(env, "1111111111", "SÍ");
    expect(tg(replies)[1]?.text).toContain("Worker parado");
    expect(workerRunning(db, "sebastian")).toBe(0);
    expect(workerRunning(db, "kaez")).toBe(1);
  });

  test("alerta: solo en transición 1→0, una sola vez, solo a SU chat", async () => {
    const db = new FakeD1();
    seedLinked(db, "sebastian", "1111111111");
    seedLinked(db, "kaez", "2222222222");
    seedWorker(db, "sebastian", 0); // Parada desde el panel: la marca sigue en 1.
    seedMarker(db, "sebastian", 1);
    const env = makeEnv(db);
    const replies = captureReplies();
    await maybeAlertWorkerStopped(env, "sebastian");
    expect(replies.length).toBe(1);
    expect(replies[0]?.chat_id).toBe("1111111111");
    expect(replies[0]?.text).toContain("se desactivó");
    expect(replies[0]?.text).toContain("/iniciar");
    expect(markerOf(db, "sebastian")).toBe(0);
    // Segunda pasada del cron: ya no es transición, no repite.
    await maybeAlertWorkerStopped(env, "sebastian");
    expect(replies.length).toBe(1);
  });

  test("alerta: primera observación inicializa en silencio; 0→1 no avisa", async () => {
    const db = new FakeD1();
    seedLinked(db);
    seedWorker(db, "sebastian", 0);
    const env = makeEnv(db);
    const replies = captureReplies();
    await maybeAlertWorkerStopped(env, "sebastian");
    expect(replies.length).toBe(0);
    expect(markerOf(db, "sebastian")).toBe(0);
    // Ahora se enciende: 0→1 actualiza la marca sin avisar.
    (db.tables.worker_state ?? []).find((r) => r.tenant_id === "sebastian")!.running = 1;
    await maybeAlertWorkerStopped(env, "sebastian");
    expect(replies.length).toBe(0);
    expect(markerOf(db, "sebastian")).toBe(1);
  });
});
