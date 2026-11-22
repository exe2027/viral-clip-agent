// Tests del validador de suministro del Worker (2026-11-18):
// "en marcha" no basta: con 10 streamers ES en el panel (y 8 EN
// más en el cerebro, 18 en total) es casi imposible que no haya un
// live o VOD, así que Cola vacía + sin clips en 6 h = atascado.
// Función pura + mensaje de Telegram + collectWorkerSupply con D1
// falso mínimo (solo las 5 consultas que usa collect).
import { describe, expect, test } from "bun:test";
import {
  collectWorkerSupply,
  validateWorkerSupply,
  type WorkerSupplyInput,
} from "../src/worker-validate";
import { buildWorkerStuckAlertMessage } from "../src/telegram";

const NOW = Date.parse("2026-11-19T01:00:00Z");
const H = 3_600_000;

function input(over: Partial<WorkerSupplyInput> = {}): WorkerSupplyInput {
  return {
    running: true,
    startedAt: NOW - 24 * H,
    lastLiveDispatchAt: NOW - 10 * 60 * 1000,
    activeInfluencers: 10,
    activeEs: 10,
    activeEn: 0,
    liveEsGta6: null,
    queueCount: 0,
    lastClipAt: NOW - 1 * H,
    failedJobs24h: 0,
    ...over,
  };
}

describe("validateWorkerSupply", () => {
  test("parado: stopped, no es fallo", () => {
    const v = validateWorkerSupply(input({ running: false }), NOW);
    expect(v.status).toBe("stopped");
    expect(v.reason).toContain("parado");
  });

  test("en marcha sin streamers activos: blocked", () => {
    const v = validateWorkerSupply(input({ activeInfluencers: 0, activeEs: 0, activeEn: 0 }), NOW);
    expect(v.status).toBe("blocked");
    expect(v.reason).toContain("sin streamers activos");
  });

  test("cola con clips viejos: stuck, la Cola vieja no tapa el estancamiento", () => {
    const v = validateWorkerSupply(input({ queueCount: 3, lastClipAt: NOW - 2 * H }), NOW);
    expect(v.status).toBe("stuck");
    expect(v.reason).toContain("3 en Cola");
    expect(v.reason).toContain("viejos");
  });

  test("cola con clips y último hace 30 min: ok", () => {
    const v = validateWorkerSupply(input({ queueCount: 3, lastClipAt: NOW - 30 * 60 * 1000 }), NOW);
    expect(v.status).toBe("ok");
  });

  test("clip hace 1 h y cola vacía: ok, se está alimentando", () => {
    const v = validateWorkerSupply(input({ lastClipAt: NOW - 1 * H }), NOW);
    expect(v.status).toBe("ok");
  });

  test("sin clips en 2 h, cola vacía, 10 streamers: stuck con el motivo", () => {
    const v = validateWorkerSupply(input({ lastClipAt: NOW - 2 * H }), NOW);
    expect(v.status).toBe("stuck");
    expect(v.reason).toContain("2 h");
    expect(v.reason).toContain("10 streamer");
    expect(v.reason).toContain("18 streamers");
  });

  test("nunca llegó un clip: stuck, no ok por falta de datos", () => {
    const v = validateWorkerSupply(input({ lastClipAt: null }), NOW);
    expect(v.status).toBe("stuck");
    expect(v.reason).toContain("nunca ha llegado");
  });

  test("sin dispatch en vivo en 3 h y sin clips recientes: stuck por dispatch", () => {
    const v = validateWorkerSupply(
      input({ lastClipAt: NOW - 2 * H, lastLiveDispatchAt: NOW - 3 * H }),
      NOW,
    );
    expect(v.status).toBe("stuck");
    expect(v.reason).toContain("dispatch");
  });

  test("clip reciente manda sobre un dispatch viejo: ok", () => {
    const v = validateWorkerSupply(
      input({ lastClipAt: NOW - 30 * 60 * 1000, lastLiveDispatchAt: NOW - 3 * H }),
      NOW,
    );
    expect(v.status).toBe("ok");
  });

  test("justo en el límite (60 min): ok; 61 min: stuck", () => {
    expect(validateWorkerSupply(input({ lastClipAt: NOW - 60 * 60 * 1000 }), NOW).status).toBe("ok");
    expect(validateWorkerSupply(input({ lastClipAt: NOW - 61 * 60 * 1000 }), NOW).status).toBe("stuck");
  });

  // Sin directos ES (2026-11-19): falsa alarma de las 02:00, el
  // cerebro corría en verde cada 15 min con todos los ES offline.
  test("sin clips en 2 h pero ningún ES en directo en GTA VI: no_live_es, no stuck", () => {
    const v = validateWorkerSupply(input({ lastClipAt: NOW - 2 * H, liveEsGta6: 0 }), NOW);
    expect(v.status).toBe("no_live_es");
    expect(v.reason).toContain("Sin directos ES");
    expect(v.reason).toContain("último barrido");
    expect(v.reason).toContain("próximo");
  });

  test("sin directos ES y nunca llegó un clip: no_live_es, no stuck", () => {
    const v = validateWorkerSupply(input({ lastClipAt: null, liveEsGta6: 0 }), NOW);
    expect(v.status).toBe("no_live_es");
    expect(v.reason).toContain("Sin directos ES");
  });

  test("con ES en directo en GTA VI y sin clips en 2 h: stuck (el atasco queda solo para eso)", () => {
    const v = validateWorkerSupply(input({ lastClipAt: NOW - 2 * H, liveEsGta6: 2 }), NOW);
    expect(v.status).toBe("stuck");
    expect(v.reason).toContain("2 h");
  });

  test("directos sin comprobar (null): stuck, no se afirma que no hay directos", () => {
    const v = validateWorkerSupply(input({ lastClipAt: NOW - 2 * H, liveEsGta6: null }), NOW);
    expect(v.status).toBe("stuck");
  });

  test("sin dispatch en 3 h y sin directos ES: stuck, el dispatch parado manda", () => {
    const v = validateWorkerSupply(
      input({ lastClipAt: NOW - 2 * H, lastLiveDispatchAt: NOW - 3 * H, liveEsGta6: 0 }),
      NOW,
    );
    expect(v.status).toBe("stuck");
    expect(v.reason).toContain("dispatch");
  });

  test("clip reciente manda sobre no tener directos: ok", () => {
    const v = validateWorkerSupply(input({ lastClipAt: NOW - 30 * 60 * 1000, liveEsGta6: 0 }), NOW);
    expect(v.status).toBe("ok");
  });
});

describe("buildWorkerStuckAlertMessage", () => {
  test("lleva el motivo y la hora en Santiago, sin inventar causa", () => {
    const text = buildWorkerStuckAlertMessage("Atascado: Cola vacía y sin clips nuevos en 7 h.", NOW);
    expect(text).toContain("atascado");
    expect(text).toContain("sin clips nuevos en 7 h");
    expect(text).toContain("Santiago");
    expect(text.length).toBeLessThanOrEqual(4000);
  });
});

// --- D1 falso mínimo para collectWorkerSupply ---
function fakeDb(opts: {
  running: boolean;
  lastDispatchAt: number | null;
  languages: string[];
  queueCount: number;
  lastClipAt: number | null;
  failed: number;
}) {
  return {
    prepare(sql: string) {
      return {
        bind(..._params: unknown[]) {
          return {
            async first() {
              if (sql.includes("FROM worker_state"))
                return { running: opts.running ? 1 : 0, started_at: NOW - 24 * H, last_live_dispatch_at: opts.lastDispatchAt };
              if (sql.includes("COUNT(*)") && sql.includes("FROM clips"))
                return { n: opts.queueCount };
              if (sql.includes("MAX(created_at)")) return { lastClipAt: opts.lastClipAt };
              if (sql.includes("FROM jobs")) return { n: opts.failed };
              return null;
            },
            async all() {
              if (sql.includes("FROM influencers"))
                return { results: opts.languages.map((language) => ({ language })) };
              return { results: [] };
            },
            async run() {
              return { results: [], success: true, meta: {} };
            },
          };
        },
      };
    },
  } as unknown as import("../src/cloudflare").D1Database;
}

describe("collectWorkerSupply", () => {
  test("10 ES activos, cola vacía, último clip hace 7 h: stuck", async () => {
    const v = await collectWorkerSupply(
      fakeDb({ running: true, lastDispatchAt: NOW - 10 * 60 * 1000, languages: Array(10).fill("es"), queueCount: 0, lastClipAt: NOW - 7 * H, failed: 0 }),
      "sebastian",
      NOW,
    );
    expect(v.status).toBe("stuck");
    expect(v.activeInfluencers).toBe(10);
    expect(v.activeEs).toBe(10);
    expect(v.queueCount).toBe(0);
    expect(v.hoursSinceLastClip).toBe(7);
  });

  test("misma situación pero con último clip hace 30 min y 2 en cola: ok", async () => {
    const v = await collectWorkerSupply(
      fakeDb({ running: true, lastDispatchAt: NOW - 10 * 60 * 1000, languages: Array(10).fill("es"), queueCount: 2, lastClipAt: NOW - 30 * 60 * 1000, failed: 0 }),
      "sebastian",
      NOW,
    );
    expect(v.status).toBe("ok");
    expect(v.queueCount).toBe(2);
  });

  test("2 clips en cola pero último hace 7 h: stuck, la Cola vieja no tapa", async () => {
    const v = await collectWorkerSupply(
      fakeDb({ running: true, lastDispatchAt: NOW - 10 * 60 * 1000, languages: Array(10).fill("es"), queueCount: 2, lastClipAt: NOW - 7 * H, failed: 0 }),
      "sebastian",
      NOW,
    );
    expect(v.status).toBe("stuck");
  });

  // La comprobación de directos es perezosa: solo cuando el
  // veredicto solo con D1 sería stuck y el dispatch está al día.
  test("mismo atasco pero sin ES en directo: no_live_es (no gasta la alerta de atascado)", async () => {
    const v = await collectWorkerSupply(
      fakeDb({ running: true, lastDispatchAt: NOW - 10 * 60 * 1000, languages: Array(10).fill("es"), queueCount: 0, lastClipAt: NOW - 7 * H, failed: 0 }),
      "sebastian",
      NOW,
      { checkLive: async () => 0 },
    );
    expect(v.status).toBe("no_live_es");
    expect(v.liveEsGta6).toBe(0);
    expect(v.reason).toContain("Sin directos ES");
  });

  test("mismo atasco pero con 2 ES en directo en GTA VI: stuck", async () => {
    const v = await collectWorkerSupply(
      fakeDb({ running: true, lastDispatchAt: NOW - 10 * 60 * 1000, languages: Array(10).fill("es"), queueCount: 0, lastClipAt: NOW - 7 * H, failed: 0 }),
      "sebastian",
      NOW,
      { checkLive: async () => 2 },
    );
    expect(v.status).toBe("stuck");
    expect(v.liveEsGta6).toBe(2);
  });

  test("directos sin comprobar (null): stuck, como antes del cambio", async () => {
    const v = await collectWorkerSupply(
      fakeDb({ running: true, lastDispatchAt: NOW - 10 * 60 * 1000, languages: Array(10).fill("es"), queueCount: 0, lastClipAt: NOW - 7 * H, failed: 0 }),
      "sebastian",
      NOW,
      { checkLive: async () => null },
    );
    expect(v.status).toBe("stuck");
    expect(v.liveEsGta6).toBe(null);
  });

  test("con clip reciente no se comprueban los directos (checkLive ni se llama)", async () => {
    let called = false;
    const v = await collectWorkerSupply(
      fakeDb({ running: true, lastDispatchAt: NOW - 10 * 60 * 1000, languages: Array(10).fill("es"), queueCount: 2, lastClipAt: NOW - 30 * 60 * 1000, failed: 0 }),
      "sebastian",
      NOW,
      {
        checkLive: async () => {
          called = true;
          return 0;
        },
      },
    );
    expect(v.status).toBe("ok");
    expect(called).toBe(false);
  });

  test("con dispatch parado no se comprueban los directos: stuck por dispatch", async () => {
    let called = false;
    const v = await collectWorkerSupply(
      fakeDb({ running: true, lastDispatchAt: NOW - 3 * H, languages: Array(10).fill("es"), queueCount: 0, lastClipAt: NOW - 7 * H, failed: 0 }),
      "sebastian",
      NOW,
      {
        checkLive: async () => {
          called = true;
          return 0;
        },
      },
    );
    expect(v.status).toBe("stuck");
    expect(v.reason).toContain("dispatch");
    expect(called).toBe(false);
  });
});
