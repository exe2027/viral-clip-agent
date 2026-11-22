// Tests del monitoreo automático (A6, 2026-11-20): chequeo
// horario, alertas por hito con dedupe, informe diario 20:50 y
// degradación a log cuando no hay Telegram. Sin red real ni D1
// real: GraphQL y Telegram mockeados, SQLite local.
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import type { Env } from "../src/cloudflare";
import { clearBudgetCounterCache, evaluateBudget, budgetGuardFromState } from "../src/budget";
import {
  buildDailyBudgetReport,
  buildMilestoneAlertMessage,
  maybeRunBudgetMonitor,
  maybeSendDailyBudgetReport,
  milestoneForLevel,
  notifyBudgetAlert,
  readDailyPublishStats,
  resetBudgetAlertState,
  resetBudgetMonitorHour,
} from "../src/budget-monitor";
import { linkTelegramChat } from "../src/db";
import { makeBudgetDb } from "./budget-test-db";

const NOON_SANTIAGO = Date.UTC(2026, 10, 20, 15, 0, 0); // 12:00 -03
const REPORT_TIME = Date.UTC(2026, 10, 20, 23, 50, 0); // 20:50 -03
const MIN = 60_000;

let logLines: string[] = [];
const realLog = console.log;

beforeEach(() => {
  clearBudgetCounterCache();
  resetBudgetAlertState();
  resetBudgetMonitorHour();
  logLines = [];
  console.log = (...args: unknown[]) => {
    logLines.push(args.map(String).join(" "));
  };
});

afterEach(() => {
  console.log = realLog;
});

function envWithTelegram(d1?: import("../src/cloudflare").D1Database): Env {
  return {
    DB: d1,
    TELEGRAM_BOT_TOKEN: "test-bot-token",
    CLOUDFLARE_API_TOKEN: "test-cf-token",
    CLOUDFLARE_ACCOUNT_ID: "test-account",
  } as unknown as Env;
}

/** Stub combinado: GraphQL (escrituras/top/errores) + Telegram. */
function combinedStub(opts: {
  globalWrites?: number;
  tenantWrites?: number;
  quotaErrors?: number;
  telegramOk?: boolean;
}): { fetchFn: typeof fetch; telegramCalls: () => number; telegramTexts: () => string[]; graphqlCalls: () => number } {
  let telegramCalls = 0;
  let graphqlCalls = 0;
  const texts: string[] = [];
  const fetchFn = (async (url: unknown, init: unknown) => {
    const u = String(url);
    if (u.includes("api.telegram.org")) {
      telegramCalls++;
      const body = JSON.parse(String((init as { body?: string }).body ?? "{}")) as { text?: string };
      texts.push(body.text ?? "");
      if (opts.telegramOk === false) {
        return { ok: false, status: 403, text: async () => "Forbidden: bot was blocked", json: async () => ({}) };
      }
      return { ok: true, status: 200, json: async () => ({ ok: true }), text: async () => "{}" };
    }
    graphqlCalls++;
    const body = JSON.parse(String((init as { body?: string }).body ?? "{}")) as { query?: string };
    const q = body.query ?? "";
    if (q.includes("d1AnalyticsAdaptiveGroups")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          data: {
            viewer: {
              accounts: [
                {
                  d1AnalyticsAdaptiveGroups: [
                    { sum: { rowsWritten: opts.tenantWrites ?? 3_000 }, dimensions: { date: "2026-11-20", databaseId: "ee97f58d-0b56-4617-9976-fcf0022edb54" } },
                    { sum: { rowsWritten: (opts.globalWrites ?? 36_000) - (opts.tenantWrites ?? 3_000) }, dimensions: { date: "2026-11-20", databaseId: "5cf19675-f905-4e49-b8f2-96ae0810fafd" } },
                  ],
                },
              ],
            },
          },
        }),
      };
    }
    if (q.includes("dimensions { query")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          data: {
            viewer: {
              accounts: [
                {
                  d1QueriesAdaptiveGroups: [
                    { sum: { rowsWritten: 5_000 }, dimensions: { query: "INSERT INTO worker_events (tenant_id, at, kind, message) VALUES (?, ?, ?, ?)" } },
                    { sum: { rowsWritten: 4_000 }, dimensions: { query: "INSERT INTO clip_events (clip_id, tenant_id, at, step, actor, message, failed) VALUES (?, ?, ?, ?, ?, ?, ?, ?)" } },
                    { sum: { rowsWritten: 100 }, dimensions: { query: "UPDATE jobs SET state = ? WHERE id = ?" } },
                  ],
                },
              ],
            },
          },
        }),
      };
    }
    if (q.includes("dimensions { error }")) {
      const n = opts.quotaErrors ?? 0;
      return {
        ok: true,
        status: 200,
        json: async () => ({
          data: {
            viewer: {
              accounts: [
                {
                  d1QueriesAdaptiveGroups:
                    n > 0
                      ? [{ count: n, dimensions: { error: "D1_ERROR: rows written limit exceeded (7500)" } }]
                      : [{ count: 0, dimensions: { error: "" } }],
                },
              ],
            },
          },
        }),
      };
    }
    return { ok: false, status: 400, json: async () => ({}) };
  }) as unknown as typeof fetch;
  return { fetchFn, telegramCalls: () => telegramCalls, telegramTexts: () => texts, graphqlCalls: () => graphqlCalls };
}

async function linkChat(d1: import("../src/cloudflare").D1Database, chatId = "987654"): Promise<void> {
  await linkTelegramChat(d1, chatId, "sebastian", NOON_SANTIAGO);
}

describe("milestoneForLevel + buildMilestoneAlertMessage", () => {
  test("ok no tiene hito; notice/restricted/essential-only sí", () => {
    expect(milestoneForLevel("ok")).toBeNull();
    expect(milestoneForLevel("notice")).toBe("notice-50");
    expect(milestoneForLevel("restricted")).toBe("restricted-70");
    expect(milestoneForLevel("essential-only")).toBe("essential-90");
  });

  test("el mensaje lleva los números y nunca el token", () => {
    const guard = budgetGuardFromState(evaluateBudget(3_000, 36_000, "analytics", NOON_SANTIAGO));
    const msg = buildMilestoneAlertMessage("sebastian", "restricted-70", guard, "2026-11-20");
    expect(msg).toContain("70%");
    expect(msg).toContain("3.000");
    expect(msg).toContain("36.000");
    expect(msg).toContain("sebastian");
    expect(msg).not.toContain("test-bot-token");
    expect(msg).toContain("solo se escribe lo esencial");
  });
});

describe("notifyBudgetAlert — dedupe y degradación", () => {
  test("la misma key solo se envía una vez al día", async () => {
    const { d1 } = makeBudgetDb();
    await linkChat(d1);
    const env = envWithTelegram(d1);
    const stub = combinedStub({});
    const r1 = await notifyBudgetAlert(env, "sebastian", "milestone-restricted-70:2026-11-20", "Aviso 70%", NOON_SANTIAGO, stub.fetchFn);
    const r2 = await notifyBudgetAlert(env, "sebastian", "milestone-restricted-70:2026-11-20", "Aviso 70%", NOON_SANTIAGO, stub.fetchFn);
    expect(r1.sent).toBe(true);
    expect(r1.via).toBe("telegram");
    expect(r2.sent).toBe(false);
    expect(r2.via).toBe("duplicate");
    expect(stub.telegramCalls()).toBe(1);
  });

  test("sin TELEGRAM_BOT_TOKEN, degrada a una sola línea de log (sin el secreto)", async () => {
    const { d1 } = makeBudgetDb();
    await linkChat(d1);
    const env = { CLOUDFLARE_API_TOKEN: "test-cf-token", CLOUDFLARE_ACCOUNT_ID: "test-account" } as unknown as Env;
    const stub = combinedStub({});
    const r = await notifyBudgetAlert(env, "sebastian", "milestone-notice-50:2026-11-20", "Aviso 50%\nSegunda línea.", NOON_SANTIAGO, stub.fetchFn);
    expect(r.sent).toBe(false);
    expect(r.via).toBe("log");
    expect(stub.telegramCalls()).toBe(0);
    expect(logLines.length).toBe(1);
    expect(logLines[0]).toContain("[d1-budget]");
    expect(logLines[0]).toContain("Telegram sin configurar");
    expect(logLines[0]).not.toContain("test-cf-token");
  });

  test("sin chat vinculado, también degrada a log (una sola vez)", async () => {
    const { d1 } = makeBudgetDb();
    const env = envWithTelegram(d1);
    const stub = combinedStub({});
    const r1 = await notifyBudgetAlert(env, "sebastian", "milestone-notice-50:2026-11-20", "Aviso 50%", NOON_SANTIAGO, stub.fetchFn);
    const r2 = await notifyBudgetAlert(env, "sebastian", "milestone-notice-50:2026-11-20", "Aviso 50%", NOON_SANTIAGO + 60 * MIN, stub.fetchFn);
    expect(r1.via).toBe("log");
    expect(r2.via).toBe("duplicate");
    expect(logLines.length).toBe(1);
    expect(logLines[0]).toContain("Sin chat de Telegram vinculado");
  });
});

describe("maybeRunBudgetMonitor — chequeo horario", () => {
  test("a las 12:00 en punto con 36.000 globales, avisa del hito 70% por Telegram", async () => {
    const { d1 } = makeBudgetDb();
    await linkChat(d1);
    const stub = combinedStub({ globalWrites: 36_000, tenantWrites: 3_000 });
    const summary = await maybeRunBudgetMonitor(envWithTelegram(d1), "sebastian", NOON_SANTIAGO, stub.fetchFn);
    expect(summary?.level).toBe("restricted");
    expect(summary?.globalWrites).toBe(36_000);
    expect(summary?.alertsSent).toEqual(["milestone-restricted-70:2026-11-20"]);
    expect(stub.telegramCalls()).toBe(1);
    expect(stub.telegramTexts()[0]).toContain("70%");
  });

  test("fuera de la hora en punto (12:30), no hace nada", async () => {
    const { d1 } = makeBudgetDb();
    await linkChat(d1);
    const stub = combinedStub({ globalWrites: 36_000 });
    const summary = await maybeRunBudgetMonitor(envWithTelegram(d1), "sebastian", NOON_SANTIAGO + 30 * MIN, stub.fetchFn);
    expect(summary).toBeNull();
    expect(stub.telegramCalls()).toBe(0);
    expect(stub.graphqlCalls()).toBe(0);
  });

  test("dos llamadas en la misma hora: la segunda no repite", async () => {
    const { d1 } = makeBudgetDb();
    await linkChat(d1);
    const stub = combinedStub({ globalWrites: 36_000 });
    await maybeRunBudgetMonitor(envWithTelegram(d1), "sebastian", NOON_SANTIAGO, stub.fetchFn);
    const second = await maybeRunBudgetMonitor(envWithTelegram(d1), "sebastian", NOON_SANTIAGO, stub.fetchFn);
    expect(second).toBeNull();
    expect(stub.telegramCalls()).toBe(1);
  });

  test("con errores 7500 hoy, avisa también del 7500 (una vez al día)", async () => {
    const { d1 } = makeBudgetDb();
    await linkChat(d1);
    const stub = combinedStub({ globalWrites: 10_000, tenantWrites: 500, quotaErrors: 12 });
    const summary = await maybeRunBudgetMonitor(envWithTelegram(d1), "sebastian", NOON_SANTIAGO, stub.fetchFn);
    expect(summary?.level).toBe("ok");
    expect(summary?.quotaErrors).toBe(12);
    expect(summary?.alertsSent).toEqual(["quota-7500:2026-11-20"]);
    expect(stub.telegramTexts()[0]).toContain("7500");
  });

  test("sin token de Telegram, el chequeo no rompe: degrada a log y sigue", async () => {
    const { d1 } = makeBudgetDb();
    await linkChat(d1);
    const env = { DB: d1, CLOUDFLARE_API_TOKEN: "x", CLOUDFLARE_ACCOUNT_ID: "y" } as unknown as Env;
    const stub = combinedStub({ globalWrites: 36_000 });
    const summary = await maybeRunBudgetMonitor(env, "sebastian", NOON_SANTIAGO, stub.fetchFn);
    expect(summary?.level).toBe("restricted");
    expect(logLines.length).toBe(1);
    expect(logLines[0]).toContain("[d1-budget]");
  });
});

describe("readDailyPublishStats + buildDailyBudgetReport", () => {
  function seedJobs(raw: import("bun:sqlite").Database): void {
    const ins = (state: string, postedDay: string | null, updatedAt: number) => {
      raw.query("INSERT INTO jobs (tenant_id, kind, clip_id, platform, state, posted_day, updated_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)").run("sebastian", "publish", 1, "tiktok", state, postedDay, updatedAt, updatedAt);
    };
    const dayStartUtc = Date.UTC(2026, 10, 20, 0, 0, 0);
    ins("posted", "2026-11-20", REPORT_TIME);
    ins("posted", "2026-11-20", REPORT_TIME);
    ins("posted", "2026-11-19", dayStartUtc - MIN); // ayer: no cuenta
    ins("scheduled", null, REPORT_TIME);
    ins("processing", null, REPORT_TIME);
    ins("awaiting_captcha", null, REPORT_TIME);
    ins("failed", null, REPORT_TIME); // fallida hoy
    ins("failed", null, dayStartUtc - MIN); // fallida ayer: no cuenta
    ins("draft", null, REPORT_TIME); // no cuenta en ningún lado
  }

  test("publicadas hoy, pendientes y fallidas hoy desde lecturas", async () => {
    const { d1, raw } = makeBudgetDb();
    seedJobs(raw);
    const stats = await readDailyPublishStats(d1, "sebastian", REPORT_TIME);
    expect(stats).toEqual({ postedToday: 2, pending: 3, failedToday: 1 });
  });

  test("el informe lleva por base, top 3, publicación y 7500", () => {
    const msg = buildDailyBudgetReport({
      tenant: "sebastian",
      dayKey: "2026-11-20",
      writes: {
        tenantWrites: 3_000,
        globalWrites: 36_000,
        perDatabase: [
          { databaseId: "ee97f58d-0b56-4617-9976-fcf0022edb54", rowsWritten: 3_000 },
          { databaseId: "5cf19675-f905-4e49-b8f2-96ae0810fafd", rowsWritten: 33_000 },
        ],
        source: "analytics",
        fetchedAtMs: REPORT_TIME,
      },
      topQueries: [
        { query: "INSERT INTO worker_events (tenant_id, at, kind, message) VALUES (?, ?, ?, ?)", rowsWritten: 5_000 },
        { query: "INSERT INTO clip_events (clip_id, tenant_id, at, step, actor, message, failed) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rowsWritten: 4_000 },
      ],
      stats: { postedToday: 2, pending: 3, failedToday: 1 },
      quotaErrors: 0,
    });
    expect(msg).toContain("clips-avc-sebastian: 3.000");
    expect(msg).toContain("clips-avc: 33.000");
    expect(msg).toContain("INSERT INTO worker_events");
    expect(msg).toContain("Publicadas: 2 · Pendientes: 3 · Fallidas hoy: 1");
    expect(msg).toContain("Errores 7500 hoy: 0.");
    expect(msg.length).toBeLessThanOrEqual(4000);
  });

  test("sin datos, el informe dice 'sin dato' en vez de inventar", () => {
    const msg = buildDailyBudgetReport({
      tenant: "sebastian",
      dayKey: "2026-11-20",
      writes: { tenantWrites: null, globalWrites: null, perDatabase: [], source: "unavailable", fetchedAtMs: REPORT_TIME },
      topQueries: [],
      stats: { postedToday: 0, pending: 0, failedToday: 0 },
      quotaErrors: null,
    });
    expect(msg).toContain("sin dato");
    expect(msg).toContain("contador no disponible");
  });
});

describe("maybeSendDailyBudgetReport — 20:50", () => {
  test("a las 20:49 no hace nada; a las 20:50 envía el informe", async () => {
    const { d1, raw } = makeBudgetDb();
    await linkChat(d1);
    raw.query("INSERT INTO jobs (tenant_id, kind, clip_id, platform, state, posted_day, updated_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)").run("sebastian", "publish", 1, "tiktok", "posted", "2026-11-20", REPORT_TIME, REPORT_TIME);
    const stub = combinedStub({ globalWrites: 36_000, tenantWrites: 3_000 });
    const early = await maybeSendDailyBudgetReport(envWithTelegram(d1), "sebastian", REPORT_TIME - MIN, stub.fetchFn);
    expect(early).toBeNull();
    const sent = await maybeSendDailyBudgetReport(envWithTelegram(d1), "sebastian", REPORT_TIME, stub.fetchFn);
    expect(sent?.sent).toBe(true);
    expect(sent?.via).toBe("telegram");
    expect(stub.telegramCalls()).toBe(1);
    expect(stub.telegramTexts()[0]).toContain("informe diario");
    expect(stub.telegramTexts()[0]).toContain("Publicadas: 1");
  });

  test("a las 20:55 del mismo día no reenvía; al día siguiente sí", async () => {
    const { d1 } = makeBudgetDb();
    await linkChat(d1);
    const stub = combinedStub({ globalWrites: 10_000, tenantWrites: 500 });
    await maybeSendDailyBudgetReport(envWithTelegram(d1), "sebastian", REPORT_TIME, stub.fetchFn);
    const dup = await maybeSendDailyBudgetReport(envWithTelegram(d1), "sebastian", REPORT_TIME + 5 * MIN, stub.fetchFn);
    expect(dup?.via).toBe("duplicate");
    expect(stub.telegramCalls()).toBe(1);
    const nextDay = await maybeSendDailyBudgetReport(envWithTelegram(d1), "sebastian", REPORT_TIME + 24 * 3600 * 1000, stub.fetchFn);
    expect(nextDay?.sent).toBe(true);
    expect(stub.telegramCalls()).toBe(2);
  });

  test("a las 21:00 ya no envía (ventana 20:50–20:59)", async () => {
    const { d1 } = makeBudgetDb();
    await linkChat(d1);
    const stub = combinedStub({});
    const late = await maybeSendDailyBudgetReport(envWithTelegram(d1), "sebastian", REPORT_TIME + 10 * MIN, stub.fetchFn);
    expect(late).toBeNull();
    expect(stub.telegramCalls()).toBe(0);
  });

  test("sin Telegram, el informe degrada a una línea de log y no rompe", async () => {
    const { d1 } = makeBudgetDb();
    await linkChat(d1);
    const env = { DB: d1, CLOUDFLARE_API_TOKEN: "x", CLOUDFLARE_ACCOUNT_ID: "y" } as unknown as Env;
    const stub = combinedStub({});
    const r = await maybeSendDailyBudgetReport(env, "sebastian", REPORT_TIME, stub.fetchFn);
    expect(r?.via).toBe("log");
    expect(logLines.length).toBe(1);
    expect(logLines[0]).toContain("[d1-budget]");
  });
});
