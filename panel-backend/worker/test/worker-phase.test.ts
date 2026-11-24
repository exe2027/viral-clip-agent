// Tests de la fase en vivo del cerebro (2026-11-19):
// el panel tiene que mostrar qué está haciendo AHORA el cerebro
// (Buscando picos / Cortando clip / Modo VOD / Sin directos),
// no solo un piloto verde o rojo. La fase la reporta el cerebro
// a POST /api/worker/phase y se guarda una fila por tenant.
// Reglas: ATASCADO lo decide SOLO el validador (directos activos
// y sin clip en 60 min); la fase nunca cambia ese veredicto.
// Una fase de más de 20 min es desactualizada (un ciclo son 15).
import { describe, expect, test } from "bun:test";
import {
  BRAIN_PHASES,
  PHASE_STALE_MS,
  getWorkerPhase,
  handleReportClipEvent,
  handleReportPhase,
  nextBrainRunAt,
  parseBrainPhase,
  phaseLabel,
  phaseView,
  setWorkerPhase,
} from "../src/worker-phase";
import { collectWorkerSupply } from "../src/worker-validate";

const NOW = Date.parse("2026-11-19T08:00:00Z");
const H = 3_600_000;

// --- D1 falso mínimo: solo la tabla worker_phases ---
function fakePhaseDb() {
  const rows = new Map<string, { phase: string; detail: string; run_id: string; updated_at: number }>();
  const db = {
    rows,
    prepare(sql: string) {
      return {
        bind(...params: unknown[]) {
          return {
            async first() {
              if (sql.includes("FROM worker_phases")) return rows.get(String(params[0])) ?? null;
              return null;
            },
            async run() {
              if (sql.includes("INTO worker_phases")) {
                const [tenant, phase, detail, run_id, updated_at] = params as [string, string, string, string, number];
                rows.set(tenant, { phase, detail, run_id, updated_at });
              }
              return { results: [], success: true, meta: {} };
            },
          };
        },
      };
    },
  } as unknown as import("../src/cloudflare").D1Database;
  return db;
}

describe("parseBrainPhase / phaseLabel", () => {
  test("las 4 fases válidas pasan", () => {
    for (const p of BRAIN_PHASES) expect(parseBrainPhase(p)).toBe(p);
  });

  test("ATASCADO y Cargando no son fases del cerebro", () => {
    expect(parseBrainPhase("ATASCADO")).toBeNull();
    expect(parseBrainPhase("cargando")).toBeNull();
    expect(parseBrainPhase("")).toBeNull();
    expect(parseBrainPhase(null)).toBeNull();
    expect(parseBrainPhase(42)).toBeNull();
  });

  test("etiquetas en español", () => {
    expect(phaseLabel("buscando_picos")).toBe("Buscando picos");
    expect(phaseLabel("cortando_clip")).toBe("Cortando clip");
    expect(phaseLabel("modo_vod")).toBe("Modo VOD");
    expect(phaseLabel("sin_directos")).toBe("Sin directos");
    expect(phaseLabel(null)).toBe("Sin señal del cerebro");
  });
});

describe("phaseView (edad y desactualización)", () => {
  test("fase fresca: edad en minutos, no desactualizada", () => {
    const v = phaseView({ phase: "cortando_clip", detail: "Rubius", run_id: "1", updated_at: NOW - 5 * 60_000 }, NOW);
    expect(v?.label).toBe("Cortando clip");
    expect(v?.ageMin).toBe(5);
    expect(v?.stale).toBe(false);
  });

  test("fase de más de 30 min: desactualizada", () => {
    expect(PHASE_STALE_MS).toBe(30 * 60_000);
    const v = phaseView({ phase: "modo_vod", detail: "", run_id: "1", updated_at: NOW - 31 * 60_000 }, NOW);
    expect(v?.stale).toBe(true);
    expect(v?.ageMin).toBe(31);
  });

  test("sin fila: sin fase", () => {
    expect(phaseView(null, NOW)).toBeNull();
  });
});

describe("nextBrainRunAt", () => {
  test("último barrido + 15 min", () => {
    expect(nextBrainRunAt(NOW - 10 * 60_000)).toBe(NOW + 5 * 60_000);
  });

  test("sin barrido nunca: null", () => {
    expect(nextBrainRunAt(null)).toBeNull();
  });
});

describe("handleReportPhase + getWorkerPhase", () => {
  test("reporte válido: 200 y la fase queda guardada", async () => {
    const db = fakePhaseDb();
    const res = await handleReportPhase(db, "sebastian", { phase: "buscando_picos", detail: "Rubius, IlloJuan", run_id: "444" }, NOW);
    expect(res.status).toBe(200);
    expect(res.body.phase).toBe("buscando_picos");
    expect(res.body.label).toBe("Buscando picos");
    const row = await getWorkerPhase(db, "sebastian");
    expect(row?.phase).toBe("buscando_picos");
    expect(row?.detail).toBe("Rubius, IlloJuan");
    expect(row?.run_id).toBe("444");
    expect(row?.updated_at).toBe(NOW);
  });

  test("el último reporte gana (un tenant, una fase)", async () => {
    const db = fakePhaseDb();
    await handleReportPhase(db, "sebastian", { phase: "buscando_picos" }, NOW - 5 * 60_000);
    await handleReportPhase(db, "sebastian", { phase: "modo_vod", detail: "Rubius" }, NOW);
    const row = await getWorkerPhase(db, "sebastian");
    expect(row?.phase).toBe("modo_vod");
    expect(row?.detail).toBe("Rubius");
  });

  test("fase inválida: 400 y no se guarda nada", async () => {
    const db = fakePhaseDb();
    const res = await handleReportPhase(db, "sebastian", { phase: "ATASCADO" }, NOW);
    expect(res.status).toBe(400);
    expect(await getWorkerPhase(db, "sebastian")).toBeNull();
    const res2 = await handleReportPhase(db, "sebastian", {}, NOW);
    expect(res2.status).toBe(400);
  });

  test("detalle largo se recorta a 200 caracteres", async () => {
    const db = fakePhaseDb();
    await handleReportPhase(db, "sebastian", { phase: "modo_vod", detail: "x".repeat(300) }, NOW);
    const row = await getWorkerPhase(db, "sebastian");
    expect(row?.detail.length).toBe(200);
  });

  test("setWorkerPhase directo + getWorkerPhase", async () => {
    const db = fakePhaseDb();
    await setWorkerPhase(db, "kaez", "sin_directos", "nadie en vivo", "9", NOW);
    expect(await getWorkerPhase(db, "kaez")).toEqual({
      phase: "sin_directos",
      detail: "nadie en vivo",
      run_id: "9",
      updated_at: NOW,
    });
    // Otro tenant no ve la fase de este.
    expect(await getWorkerPhase(db, "sebastian")).toBeNull();
  });

  test("fallo de D1 al leer: null, no rompe", async () => {
    const broken = {
      prepare() {
        return { bind() { return { async first() { throw new Error("D1 caída"); } }; } };
      },
    } as unknown as import("../src/cloudflare").D1Database;
    expect(await getWorkerPhase(broken, "sebastian")).toBeNull();
  });

  test("fallo de D1 al guardar: 500 con motivo", async () => {
    const broken = {
      prepare() {
        return { bind() { return { async run() { throw new Error("D1 caída"); } }; } };
      },
    } as unknown as import("../src/cloudflare").D1Database;
    const res = await handleReportPhase(broken, "sebastian", { phase: "modo_vod" }, NOW);
    expect(res.status).toBe(500);
  });
});

describe("la fase no cambia el veredicto del validador", () => {
  // D1 falso con las 5 consultas de collectWorkerSupply + la tabla
  // de fases (que collect NO lee: la fase es informativa, el
  // atasco lo decide solo D1 + directos en vivo).
  function fakeDbWithPhase(phase: string) {
    const rows = new Map<string, { phase: string; detail: string; run_id: string; updated_at: number }>();
    rows.set("sebastian", { phase, detail: "Rubius", run_id: "1", updated_at: NOW });
    return {
      prepare(sql: string) {
        return {
          bind(..._params: unknown[]) {
            return {
              async first() {
                if (sql.includes("FROM worker_phases")) return rows.get("sebastian") ?? null;
                if (sql.includes("FROM worker_state"))
                  return { running: 1, started_at: NOW - 24 * H, last_live_dispatch_at: NOW - 10 * 60_000 };
                if (sql.includes("COUNT(*)") && sql.includes("FROM clips")) return { n: 0 };
                if (sql.includes("MAX(created_at)")) return { lastClipAt: NOW - 7 * H };
                if (sql.includes("FROM jobs")) return { n: 0 };
                return null;
              },
              async all() {
                if (sql.includes("FROM influencers")) return { results: Array(10).fill({ language: "es" }) };
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

  test("fase fresca 'Cortando clip' + sin clip en 7 h: el validador sigue diciendo stuck (la fase no lo tapa)", async () => {
    const v = await collectWorkerSupply(fakeDbWithPhase("cortando_clip"), "sebastian", NOW, {
      checkLive: async () => 2,
    });
    expect(v.status).toBe("stuck");
  });
});

describe("POST /api/worker/event — eventos de clip del cerebro (visibilidad)", () => {
  // D1 falso mínimo: solo la tabla worker_events.
  function fakeEventDb() {
    const events: { tenant_id: string; at: number; kind: string; message: string }[] = [];
    const db = {
      events,
      prepare(sql: string) {
        return {
          bind(...params: unknown[]) {
            return {
              async first() {
                return null;
              },
              async all() {
                return { results: [], success: true, meta: {} };
              },
              async run() {
                if (sql.includes("INTO worker_events")) {
                  const [tenant_id, at, kind, message] = params as [string, number, string, string];
                  events.push({ tenant_id, at, kind, message });
                }
                return { results: [], success: true, meta: {} };
              },
            };
          },
        };
      },
    } as unknown as import("../src/cloudflare").D1Database;
    return db;
  }

  test("kind válido guarda el evento con su mensaje", async () => {
    const db = fakeEventDb();
    const res = await handleReportClipEvent(db, "sebastian", { kind: "clip_descartado", message: "illojuan: «X» descartado — SEO 49 < 90" }, NOW);
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ ok: true, kind: "clip_descartado" });
    expect(db.events).toHaveLength(1);
    expect(db.events[0]?.kind).toBe("clip_descartado");
    expect(db.events[0]?.tenant_id).toBe("sebastian");
  });

  test("los 5 kinds válidos pasan; cualquier otro es 400", async () => {
    const db = fakeEventDb();
    for (const kind of ["clip_cortado", "clip_encolado", "clip_descartado", "corte_fallido", "revalidado"]) {
      const res = await handleReportClipEvent(db, "sebastian", { kind, message: "x" }, NOW);
      expect(res.status).toBe(200);
    }
    const bad = await handleReportClipEvent(db, "sebastian", { kind: "ATASCADO", message: "x" }, NOW);
    expect(bad.status).toBe(400);
    expect(db.events).toHaveLength(5);
  });

  test("message vacío es 400 y mensaje largo se recorta a 500", async () => {
    const db = fakeEventDb();
    const empty = await handleReportClipEvent(db, "sebastian", { kind: "clip_cortado", message: "   " }, NOW);
    expect(empty.status).toBe(400);
    const long = await handleReportClipEvent(db, "sebastian", { kind: "clip_cortado", message: "y".repeat(600) }, NOW);
    expect(long.status).toBe(200);
    expect(db.events[0]?.message).toHaveLength(500);
  });
});
