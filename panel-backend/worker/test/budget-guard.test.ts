// Tests del guardián de presupuesto D1 (A1, 2026-11-20).
//
// Umbrales, transiciones de hito y modo solo-esencial, más la
// aritmética del reparto (un solo módulo de constantes, sin
// valores duplicados). Puro: sin D1, sin red.
import { describe, expect, test } from "bun:test";
import {
  BUDGET_ESSENTIAL_ONLY_RATIO,
  BUDGET_NOTICE_RATIO,
  BUDGET_RESTRICTED_RATIO,
  D1_DAILY_ACCOUNT_LIMIT,
  D1_ESSENTIAL_RESERVE,
  D1_MARGIN,
  D1_NORMAL_OPERATION_CAP,
  D1_TENANT_DAILY_LIMIT,
  budgetGuardFromState,
  d1DatabaseName,
  evaluateBudget,
  tenantDatabaseId,
  type BudgetWriteKind,
} from "../src/budget";

const NOW = Date.now();

const NON_ESSENTIAL: BudgetWriteKind[] = ["log", "metric", "event", "bulk-job", "dispatch", "analytics", "other"];

function guardFor(tenantWrites: number | null, globalWrites: number | null) {
  return budgetGuardFromState(evaluateBudget(tenantWrites, globalWrites, "analytics", NOW));
}

describe("reparto single-source (A1)", () => {
  test("operación normal + reserva + margen = límite de la cuenta", () => {
    expect(D1_NORMAL_OPERATION_CAP + D1_ESSENTIAL_RESERVE + D1_MARGIN).toBe(D1_DAILY_ACCOUNT_LIMIT);
    expect(D1_DAILY_ACCOUNT_LIMIT).toBe(100_000);
    expect(D1_TENANT_DAILY_LIMIT).toBe(3_500);
    expect(D1_NORMAL_OPERATION_CAP).toBe(50_000);
    expect(D1_ESSENTIAL_RESERVE).toBe(30_000);
    expect(D1_MARGIN).toBe(20_000);
  });

  test("los hitos están ordenados y dentro de 0-1", () => {
    expect(BUDGET_NOTICE_RATIO).toBe(0.5);
    expect(BUDGET_RESTRICTED_RATIO).toBe(0.7);
    expect(BUDGET_ESSENTIAL_ONLY_RATIO).toBe(0.9);
    expect(BUDGET_NOTICE_RATIO).toBeLessThan(BUDGET_RESTRICTED_RATIO);
    expect(BUDGET_RESTRICTED_RATIO).toBeLessThan(BUDGET_ESSENTIAL_ONLY_RATIO);
  });
});

describe("evaluateBudget — umbrales sobre el tope operativo (50.000)", () => {
  test("0 escrituras: ok", () => {
    const s = evaluateBudget(0, 0, "analytics", NOW);
    expect(s.level).toBe("ok");
    expect(s.governingRatio).toBe(0);
  });

  test("24.999 global: ok; 25.000 (50%): notice", () => {
    expect(evaluateBudget(100, 24_999, "analytics", NOW).level).toBe("ok");
    const s = evaluateBudget(100, 25_000, "analytics", NOW);
    expect(s.level).toBe("notice");
    expect(s.globalRatio).toBe(0.5);
  });

  test("34.999: notice; 35.000 (70%): restricted", () => {
    expect(evaluateBudget(100, 34_999, "analytics", NOW).level).toBe("notice");
    expect(evaluateBudget(100, 35_000, "analytics", NOW).level).toBe("restricted");
  });

  test("44.999: restricted; 45.000 (90%): essential-only", () => {
    expect(evaluateBudget(100, 44_999, "analytics", NOW).level).toBe("restricted");
    expect(evaluateBudget(100, 45_000, "analytics", NOW).level).toBe("essential-only");
  });

  test("100.000 (límite de la cuenta): essential-only", () => {
    expect(evaluateBudget(3_000, 100_000, "analytics", NOW).level).toBe("essential-only");
  });

  test("transiciones al crecer el consumo", () => {
    const levels = [0, 10_000, 25_000, 30_000, 35_000, 40_000, 45_000, 60_000].map(
      (g) => evaluateBudget(100, g, "analytics", NOW).level,
    );
    expect(levels).toEqual(["ok", "ok", "notice", "notice", "restricted", "restricted", "essential-only", "essential-only"]);
  });
});

describe("evaluateBudget — manda el peor ratio (tenant o global)", () => {
  test("el tenant en su tope (3.500) manda aunque el global vaya bajo", () => {
    const s = evaluateBudget(3_500, 1_000, "analytics", NOW);
    expect(s.level).toBe("essential-only");
    expect(s.tenantRatio).toBe(1);
    expect(s.governingRatio).toBe(1);
  });

  test("el tenant al 50% (1.750) avisa aunque el global vaya bajo", () => {
    const s = evaluateBudget(1_750, 1_000, "analytics", NOW);
    expect(s.level).toBe("notice");
    expect(s.governingRatio).toBe(0.5);
  });

  test("sin contador del tenant, solo manda el global", () => {
    const s = evaluateBudget(null, 40_000, "analytics", NOW);
    expect(s.level).toBe("restricted");
    expect(s.tenantRatio).toBeNull();
    expect(s.governingRatio).toBe(0.8);
  });

  test("sin ningún contador (unavailable): fail-open, ok", () => {
    const s = evaluateBudget(null, null, "unavailable", NOW);
    expect(s.level).toBe("ok");
    expect(s.governingRatio).toBeNull();
  });

  test("en fallback (global desconocido), el nivel sale del tenant", () => {
    expect(evaluateBudget(1_750, null, "events-fallback", NOW).level).toBe("notice"); // 50% del tenant
    expect(evaluateBudget(2_450, null, "events-fallback", NOW).level).toBe("restricted"); // 70% del tenant
    expect(evaluateBudget(1_000, null, "events-fallback", NOW).level).toBe("ok");
  });
});

describe("shouldWrite — modo solo-esencial", () => {
  test("en ok y notice, todo se puede escribir", () => {
    for (const g of [guardFor(0, 0), guardFor(100, 25_000)]) {
      expect(g.shouldWrite("essential")).toBe(true);
      for (const kind of NON_ESSENTIAL) expect(g.shouldWrite(kind)).toBe(true);
    }
  });

  test("en restricted, lo no esencial se detiene y lo esencial sigue", () => {
    const g = guardFor(100, 35_000);
    expect(g.level).toBe("restricted");
    expect(g.shouldWrite("essential")).toBe(true);
    for (const kind of NON_ESSENTIAL) expect(g.shouldWrite(kind)).toBe(false);
  });

  test("en essential-only, solo reclamar y marcar jobs", () => {
    const g = guardFor(100, 45_000);
    expect(g.level).toBe("essential-only");
    expect(g.shouldWrite("essential")).toBe(true);
    for (const kind of NON_ESSENTIAL) expect(g.shouldWrite(kind)).toBe(false);
  });

  test("en fail-open (sin contador), todo se puede escribir", () => {
    const g = guardFor(null, null);
    expect(g.level).toBe("ok");
    expect(g.shouldWrite("essential")).toBe(true);
    for (const kind of NON_ESSENTIAL) expect(g.shouldWrite(kind)).toBe(true);
  });
});

describe("mapa de bases D1", () => {
  test("los tres IDs conocidos tienen nombre", () => {
    expect(d1DatabaseName("5cf19675-f905-4e49-b8f2-96ae0810fafd")).toBe("clips-avc");
    expect(d1DatabaseName("ee97f58d-0b56-4617-9976-fcf0022edb54")).toBe("clips-avc-sebastian");
    expect(d1DatabaseName("7ce9af3c-110b-4ffe-8224-29ef0a3d78fa")).toBe("clips-avc-kaez");
  });

  test("un ID desconocido no inventa nombre", () => {
    expect(d1DatabaseName("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")).toBe("db:aaaaaaaa");
  });

  test("la base propia del tenant sale del nombre clips-avc-<tenant>", () => {
    expect(tenantDatabaseId("sebastian")).toBe("ee97f58d-0b56-4617-9976-fcf0022edb54");
    expect(tenantDatabaseId("kaez")).toBe("7ce9af3c-110b-4ffe-8224-29ef0a3d78fa");
    expect(tenantDatabaseId("global")).toBeNull();
  });
});
