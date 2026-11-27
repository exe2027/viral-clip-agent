// Tests del presupuesto de subrequests por invocación (P0,
// 2026-11-27): el plan Free choca con "Too many subrequests"
// cuando en una sola invocación coinciden engagement + telegram
// diario + dispatch diario + dispatches. Lo pesado que no cabe
// queda debido y corre en la siguiente invocación; nada se pierde.
import { describe, expect, test } from "bun:test";
import {
  HEAVY_DISPATCH_MAX_PER_INVOCATION,
  HEAVY_DISPATCH_PRIORITY,
  HeavyDispatchBudget,
  heavySlotForCycle,
} from "../src/subrequest-budget";

describe("heavySlotForCycle: analíticas y engagement se alternan por hora UTC", () => {
  test("hora UTC par → analíticas; impar → engagement", () => {
    expect(heavySlotForCycle(Date.UTC(2026, 10, 27, 10, 0, 0))).toBe("analytics");
    expect(heavySlotForCycle(Date.UTC(2026, 10, 27, 11, 0, 0))).toBe("engagement");
    expect(heavySlotForCycle(Date.UTC(2026, 10, 27, 12, 30, 0))).toBe("analytics");
    expect(heavySlotForCycle(Date.UTC(2026, 10, 27, 13, 59, 0))).toBe("engagement");
  });
  test("cada hora toca exactamente una de las dos (nunca las dos juntas)", () => {
    for (let h = 0; h < 24; h++) {
      const slot = heavySlotForCycle(Date.UTC(2026, 10, 27, h, 15, 0));
      expect(slot === "analytics" || slot === "engagement").toBe(true);
    }
  });
});

describe("HeavyDispatchBudget: máx 2 dispatches pesados por invocación", () => {
  test("los 2 primeros pasan, el 3º queda debido para la siguiente invocación", () => {
    const budget = new HeavyDispatchBudget();
    expect(budget.allow("live")).toBe(true);
    expect(budget.allow("watchdog")).toBe(true);
    expect(budget.allow("telegram_daily")).toBe(false);
    expect(budget.allow("dispatch_daily")).toBe(false);
    expect(budget.allow("revalidate")).toBe(false);
    expect(budget.spent).toBe(2);
  });
  test("una invocación nueva vuelve a tener cupo (el debido corre 5 min después)", () => {
    const first = new HeavyDispatchBudget();
    first.allow("live");
    first.allow("watchdog");
    expect(first.allow("telegram_daily")).toBe(false);
    const second = new HeavyDispatchBudget();
    expect(second.allow("telegram_daily")).toBe(true);
    expect(second.allow("dispatch_daily")).toBe(true);
    expect(second.allow("revalidate")).toBe(false);
  });
  test("el tope por invocación es 2 y la prioridad lista los 5 dispatches", () => {
    expect(HEAVY_DISPATCH_MAX_PER_INVOCATION).toBe(2);
    expect([...HEAVY_DISPATCH_PRIORITY]).toEqual([
      "live",
      "watchdog",
      "telegram_daily",
      "dispatch_daily",
      "revalidate",
    ]);
  });
});
