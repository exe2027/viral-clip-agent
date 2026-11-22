// Tests del contador de escrituras D1 (A1, 2026-11-20):
// analítica GraphQL de Cloudflare mockeada + fallback a contar
// las filas de hoy en las tablas de eventos + caché en memoria.
// El contador NUNCA escribe en D1: aquí solo se lee SQLite local.
import { describe, expect, test } from "bun:test";
import type { Env } from "../src/cloudflare";
import {
  clearBudgetCounterCache,
  countTodayWritesFromEvents,
  createBudgetGuard,
  fetchDailyWritesFromAnalytics,
  readDailyWrites,
  utcDayStartMs,
} from "../src/budget";
import { isD1QuotaError } from "../src/wal";
import { makeBudgetDb } from "./budget-test-db";

const NOW = Date.UTC(2026, 10, 20, 12, 0, 0); // 2026-11-20 09:00 -03
const MIN = 60_000;

function envWithAnalytics(): Env {
  return {
    CLOUDFLARE_API_TOKEN: "test-cf-token",
    CLOUDFLARE_ACCOUNT_ID: "test-account",
  } as unknown as Env;
}

/** Stub de fetch para la API GraphQL: responde según la query. */
function graphqlStub(
  groups: Array<{ databaseId: string; rowsWritten: number }>,
  opts: { httpStatus?: number; graphQLErrors?: boolean } = {},
): { fetchFn: typeof fetch; calls: () => number } {
  let calls = 0;
  const fetchFn = (async (_url: unknown, _init: unknown) => {
    calls++;
    if (opts.httpStatus && opts.httpStatus !== 200) {
      return { ok: false, status: opts.httpStatus, json: async () => ({}) };
    }
    if (opts.graphQLErrors) {
      return { ok: true, status: 200, json: async () => ({ errors: [{ message: "boom" }] }) };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({
        data: {
          viewer: {
            accounts: [
              {
                d1AnalyticsAdaptiveGroups: groups.map((g) => ({
                  sum: { rowsWritten: g.rowsWritten },
                  dimensions: { date: "2026-11-20", databaseId: g.databaseId },
                })),
              },
            ],
          },
        },
      }),
    };
  }) as unknown as typeof fetch;
  return { fetchFn, calls: () => calls };
}

describe("fetchDailyWritesFromAnalytics (GraphQL mockeada)", () => {
  test("suma por base, separa el tenant y ordena de mayor a menor", async () => {
    const { fetchFn } = graphqlStub([
      { databaseId: "ee97f58d-0b56-4617-9976-fcf0022edb54", rowsWritten: 3_000 },
      { databaseId: "5cf19675-f905-4e49-b8f2-96ae0810fafd", rowsWritten: 500 },
      { databaseId: "7ce9af3c-110b-4ffe-8224-29ef0a3d78fa", rowsWritten: 200 },
    ]);
    const w = await fetchDailyWritesFromAnalytics(envWithAnalytics(), "sebastian", NOW, fetchFn);
    expect(w.source).toBe("analytics");
    expect(w.tenantWrites).toBe(3_000);
    expect(w.globalWrites).toBe(3_700);
    expect(w.perDatabase.map((p) => p.rowsWritten)).toEqual([3_000, 500, 200]);
  });

  test("sin token o sin account ID, levanta (el llamador cae al fallback)", async () => {
    const { fetchFn } = graphqlStub([]);
    await expect(fetchDailyWritesFromAnalytics({} as Env, "sebastian", NOW, fetchFn)).rejects.toThrow();
    await expect(
      fetchDailyWritesFromAnalytics({ CLOUDFLARE_API_TOKEN: "x" } as unknown as Env, "sebastian", NOW, fetchFn),
    ).rejects.toThrow();
  });

  test("HTTP 500 o errores GraphQL, levanta", async () => {
    const bad = graphqlStub([], { httpStatus: 500 });
    await expect(fetchDailyWritesFromAnalytics(envWithAnalytics(), "sebastian", NOW, bad.fetchFn)).rejects.toThrow();
    const gqlErr = graphqlStub([], { graphQLErrors: true });
    await expect(fetchDailyWritesFromAnalytics(envWithAnalytics(), "sebastian", NOW, gqlErr.fetchFn)).rejects.toThrow();
  });

  test("respuesta sin la forma esperada, levanta", async () => {
    const fetchFn = (async () => ({
      ok: true,
      status: 200,
      json: async () => ({ data: { viewer: { accounts: [{}] } } }),
    })) as unknown as typeof fetch;
    await expect(fetchDailyWritesFromAnalytics(envWithAnalytics(), "sebastian", NOW, fetchFn)).rejects.toThrow();
  });
});

describe("countTodayWritesFromEvents (fallback, una lectura)", () => {
  test("cuenta solo las filas de hoy de este tenant", async () => {
    const { d1, raw } = makeBudgetDb();
    const dayStart = utcDayStartMs(NOW);
    const ins = (table: string, tenant: string, at: number) => {
      if (table === "worker_events") {
        raw.query("INSERT INTO worker_events (tenant_id, at, kind, message) VALUES (?, ?, ?, ?)").run(tenant, at, "ciclo", "x");
      } else {
        raw.query("INSERT INTO clip_events (clip_id, tenant_id, at, step, actor, message, failed) VALUES (?, ?, ?, ?, ?, ?, ?)").run(1, tenant, at, "publicado", "worker", "x", 0);
      }
    };
    ins("worker_events", "sebastian", dayStart + MIN);
    ins("worker_events", "sebastian", dayStart + 2 * MIN);
    ins("clip_events", "sebastian", dayStart + 3 * MIN);
    ins("worker_events", "sebastian", dayStart - MIN); // ayer: no cuenta
    ins("worker_events", "kaez", dayStart + MIN); // otro tenant: no cuenta
    const w = await countTodayWritesFromEvents(d1, "sebastian", NOW);
    expect(w.source).toBe("events-fallback");
    expect(w.tenantWrites).toBe(3);
    expect(w.globalWrites).toBeNull(); // en fallback no se ven las demás bases
    expect(w.perDatabase).toEqual([]);
  });
});

describe("readDailyWrites — analítica con fallback y caché en memoria", () => {
  test("usa la analítica cuando responde", async () => {
    clearBudgetCounterCache();
    const { d1 } = makeBudgetDb();
    const { fetchFn } = graphqlStub([
      { databaseId: "ee97f58d-0b56-4617-9976-fcf0022edb54", rowsWritten: 1_000 },
    ]);
    const w = await readDailyWrites(envWithAnalytics(), d1, "sebastian", NOW, fetchFn);
    expect(w.source).toBe("analytics");
    expect(w.tenantWrites).toBe(1_000);
    expect(w.globalWrites).toBe(1_000);
  });

  test("si la analítica falla, cae al conteo de eventos (una lectura)", async () => {
    clearBudgetCounterCache();
    const { d1, raw } = makeBudgetDb();
    raw.query("INSERT INTO worker_events (tenant_id, at, kind, message) VALUES (?, ?, ?, ?)").run("sebastian", NOW, "ciclo", "x");
    const bad = graphqlStub([], { httpStatus: 500 });
    const w = await readDailyWrites(envWithAnalytics(), d1, "sebastian", NOW, bad.fetchFn);
    expect(w.source).toBe("events-fallback");
    expect(w.tenantWrites).toBe(1);
  });

  test("sin token de analítica, va directo al fallback sin llamar a la API", async () => {
    clearBudgetCounterCache();
    const { d1, raw } = makeBudgetDb();
    raw.query("INSERT INTO clip_events (clip_id, tenant_id, at, step, actor, message, failed) VALUES (?, ?, ?, ?, ?, ?, ?)").run(1, "sebastian", NOW, "publicado", "worker", "x", 0);
    let calls = 0;
    const counting = (async () => {
      calls++;
      return { ok: true, status: 200, json: async () => ({}) };
    }) as unknown as typeof fetch;
    const w = await readDailyWrites({} as Env, d1, "sebastian", NOW, counting);
    expect(w.source).toBe("events-fallback");
    expect(calls).toBe(0);
  });

  test("la segunda lectura en 5 min no vuelve a llamar a la API", async () => {
    clearBudgetCounterCache();
    const { d1 } = makeBudgetDb();
    const stub = graphqlStub([
      { databaseId: "ee97f58d-0b56-4617-9976-fcf0022edb54", rowsWritten: 100 },
    ]);
    await readDailyWrites(envWithAnalytics(), d1, "sebastian", NOW, stub.fetchFn);
    await readDailyWrites(envWithAnalytics(), d1, "sebastian", NOW + MIN, stub.fetchFn);
    expect(stub.calls()).toBe(1);
  });

  test("pasados 5 min, vuelve a leer; otro día, también", async () => {
    clearBudgetCounterCache();
    const { d1 } = makeBudgetDb();
    const stub = graphqlStub([
      { databaseId: "ee97f58d-0b56-4617-9976-fcf0022edb54", rowsWritten: 100 },
    ]);
    await readDailyWrites(envWithAnalytics(), d1, "sebastian", NOW, stub.fetchFn);
    await readDailyWrites(envWithAnalytics(), d1, "sebastian", NOW + 6 * MIN, stub.fetchFn);
    expect(stub.calls()).toBe(2);
    await readDailyWrites(envWithAnalytics(), d1, "sebastian", NOW + 24 * 3600 * 1000, stub.fetchFn);
    expect(stub.calls()).toBe(3);
  });

  test("si todo falla (sin analítica y D1 caída), devuelve unavailable sin levantar", async () => {
    clearBudgetCounterCache();
    const throwingDb = {
      prepare: () => {
        throw new Error("D1 caída");
      },
    } as unknown as import("../src/cloudflare").D1Database;
    const bad = graphqlStub([], { httpStatus: 500 });
    const w = await readDailyWrites(envWithAnalytics(), throwingDb, "sebastian", NOW, bad.fetchFn);
    expect(w.source).toBe("unavailable");
    expect(w.tenantWrites).toBeNull();
    expect(w.globalWrites).toBeNull();
  });
});

describe("createBudgetGuard — nunca levanta (fail-open)", () => {
  test("con 25.000 globales, el guardián sale en notice", async () => {
    clearBudgetCounterCache();
    const { d1 } = makeBudgetDb();
    const stub = graphqlStub([
      { databaseId: "ee97f58d-0b56-4617-9976-fcf0022edb54", rowsWritten: 1_000 },
      { databaseId: "5cf19675-f905-4e49-b8f2-96ae0810fafd", rowsWritten: 24_000 },
    ]);
    const g = await createBudgetGuard(envWithAnalytics(), d1, "sebastian", NOW, stub.fetchFn);
    expect(g.level).toBe("notice");
    expect(g.globalWrites).toBe(25_000);
    expect(g.shouldWrite("log")).toBe(true);
  });

  test("con todo roto, el guardián es fail-open y deja escribir", async () => {
    clearBudgetCounterCache();
    const throwingDb = {
      prepare: () => {
        throw new Error("D1 caída");
      },
    } as unknown as import("../src/cloudflare").D1Database;
    const bad = graphqlStub([], { httpStatus: 500 });
    const g = await createBudgetGuard(envWithAnalytics(), throwingDb, "sebastian", NOW, bad.fetchFn);
    expect(g.level).toBe("ok");
    expect(g.source).toBe("unavailable");
    expect(g.shouldWrite("log")).toBe(true);
    expect(g.shouldWrite("essential")).toBe(true);
  });
});

describe("isD1QuotaError (vive en retry.ts, re-exportado por wal.ts)", () => {
  test("casa el 7500 en texto y en Error", () => {
    expect(isD1QuotaError("D1 error 7500: daily write limit")).toBe(true);
    expect(isD1QuotaError(new Error("algo 7500"))).toBe(true);
  });

  test("no casa otros errores", () => {
    expect(isD1QuotaError("D1 error 1000")).toBe(false);
    expect(isD1QuotaError(new Error("timeout"))).toBe(false);
    expect(isD1QuotaError(null)).toBe(false);
  });
});
