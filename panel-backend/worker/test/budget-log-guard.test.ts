// Tests del guardián aplicado a los logs (A1, 2026-11-20):
// logWorkerEvent y logClipEvent no escriben nada no esencial
// cuando el guardián vigente está en "restricted" o
// "essential-only", y escriben como siempre sin guardián o en
// "ok"/"notice". El guardián se pasa explícito o se toma el
// vigente del ciclo (setCurrentBudgetGuard). Sin D1 real:
// SQLite local.
import { describe, expect, test } from "bun:test";
import {
  budgetGuardFromState,
  clearCurrentBudgetGuard,
  currentBudgetGuard,
  evaluateBudget,
  setCurrentBudgetGuard,
} from "../src/budget";
import { logClipEvent, logWorkerEvent } from "../src/db";
import { countRows, makeBudgetDb } from "./budget-test-db";

const NOW = Date.now();
const MIN = 60_000;

function restrictedGuard() {
  return budgetGuardFromState(evaluateBudget(100, 35_000, "analytics", NOW));
}

function okGuard() {
  return budgetGuardFromState(evaluateBudget(100, 1_000, "analytics", NOW));
}

describe("logWorkerEvent con guardián", () => {
  test("con guardián restricted, no escribe el log", async () => {
    const { d1, raw } = makeBudgetDb();
    await logWorkerEvent(d1, "ciclo", "Ciclo automático: 2 jobs procesados.", "sebastian", { budget: restrictedGuard() });
    expect(countRows(raw, "worker_events")).toBe(0);
  });

  test("con guardián ok, escribe como siempre", async () => {
    const { d1, raw } = makeBudgetDb();
    await logWorkerEvent(d1, "ciclo", "Ciclo automático: 2 jobs procesados.", "sebastian", { budget: okGuard() });
    expect(countRows(raw, "worker_events")).toBe(1);
  });

  test("sin guardián explícito, usa el vigente del ciclo", async () => {
    const { d1, raw } = makeBudgetDb();
    setCurrentBudgetGuard(restrictedGuard());
    try {
      await logWorkerEvent(d1, "auto_publish", "Clip #102 frenado en tiktok: tope diario alcanzado.", "sebastian");
      expect(countRows(raw, "worker_events")).toBe(0);
      clearCurrentBudgetGuard();
      await logWorkerEvent(d1, "auto_publish", "Clip #102 frenado en tiktok: tope diario alcanzado.", "sebastian");
      expect(countRows(raw, "worker_events")).toBe(1);
    } finally {
      clearCurrentBudgetGuard();
    }
  });

  test("un guardián viejo (más de 15 min) no frena nada (fail-open)", async () => {
    const { d1, raw } = makeBudgetDb();
    const old = budgetGuardFromState(evaluateBudget(100, 45_000, "analytics", NOW - 16 * MIN));
    setCurrentBudgetGuard(old);
    try {
      expect(currentBudgetGuard(NOW)).toBeNull();
      await logWorkerEvent(d1, "ciclo", "Ciclo automático: 2 jobs procesados.", "sebastian");
      expect(countRows(raw, "worker_events")).toBe(1);
    } finally {
      clearCurrentBudgetGuard();
    }
  });
});

describe("logClipEvent con guardián", () => {
  test("con guardián essential-only, no escribe el evento de clip", async () => {
    const { d1, raw } = makeBudgetDb();
    const guard = budgetGuardFromState(evaluateBudget(100, 45_000, "analytics", NOW));
    await logClipEvent(d1, 102, "publicado", "worker", "Publicación frenada en tiktok: tope diario.", true, "sebastian", { budget: guard });
    expect(countRows(raw, "clip_events")).toBe(0);
  });

  test("con guardián notice (50%), el evento sí se escribe", async () => {
    const { d1, raw } = makeBudgetDb();
    const guard = budgetGuardFromState(evaluateBudget(100, 25_000, "analytics", NOW));
    await logClipEvent(d1, 102, "publicado", "worker", "Publicación frenada en tiktok: tope diario.", true, "sebastian", { budget: guard });
    expect(countRows(raw, "clip_events")).toBe(1);
  });
});
