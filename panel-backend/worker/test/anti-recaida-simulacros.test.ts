// Simulacros anti-recaída — Parte C, 2026-11-20.
// Entorno de PRUEBA: SQLite en memoria + diario R2 simulado + plataforma
// mockeada. NUNCA la D1 real, NUNCA la cuota real (el 7500 se FINGE con un
// flag; este archivo no escribe 100.000 filas en ningún sitio).
//
// A4.md, A5.md y B1-B2.md NO existían cuando se escribió este archivo, y
// sus módulos tampoco están en src/. Los simulacros prueban el CONTRATO
// (modelo de referencia en anti-recaida-harness.ts); la integración contra
// el código real queda marcada PENDIENTE abajo (tests en skip).
import { describe, expect, test } from "bun:test";
import {
  DAILY_D1_QUOTA_ROWS,
  JournalPublisher,
  MockD1,
  MockPlatform,
  MockR2Journal,
  QuotaCircuit,
  QuotaExceededError,
  drainJournalToD1,
  idempotencyKey,
  isQuotaError,
  journalKey,
  migrationPrecheck,
  runMigrationIfAllowed,
} from "./anti-recaida-harness";

const TENANT = "sebastian";
const DATE = "2026-11-20";
const NOW = Date.parse("2026-11-20T12:00:00-03:00");

describe("Simulacro 1 — cuota agotada (7500 fingido)", () => {
  test("el 7500 fingido no escribe ninguna fila real (la cuota real no se toca)", async () => {
    const d1 = new MockD1();
    d1.exhausted = true;
    for (let i = 0; i < 5; i++) {
      await expect(
        d1
          .prepare("INSERT INTO publish_log (idempotency_key, clip_id, account, date, platform_post_id, applied_at) VALUES (?, ?, ?, ?, ?, ?)")
          .bind(`k${i}`, 100 + i, "tiktok_es", DATE, null, NOW)
          .run(),
      ).rejects.toThrow("7500");
    }
    // El intento fallido se contabiliza, pero la escritura jamás ocurrió.
    expect(d1.writesAttempted).toBe(5);
    expect(d1.writesSucceeded).toBe(0);
    expect(d1.countPublishLog()).toBe(0);
  });

  test("(a) al aparecer el 7500, los bucles no esenciales se detienen", async () => {
    const d1 = new MockD1();
    const circuit = new QuotaCircuit();
    d1.exhausted = true;
    // Antes del error, todo corre.
    expect(circuit.shouldRunLoop("analytics")).toBe(true);
    expect(circuit.shouldRunLoop("brain-dispatch")).toBe(true);
    expect(circuit.shouldRunLoop("watcher-scan")).toBe(true);
    expect(circuit.shouldRunLoop("seo-retry")).toBe(true);
    // Aparece el error en una escritura cualquiera…
    let caught: unknown;
    try {
      await d1.prepare("INSERT INTO publish_log (idempotency_key, clip_id, account, date, platform_post_id, applied_at) VALUES (?, ?, ?, ?, ?, ?)")
        .bind("k", 1, "tiktok_es", DATE, null, NOW)
        .run();
    } catch (err) {
      caught = err;
    }
    expect(isQuotaError(caught)).toBe(true);
    circuit.onD1Error(caught, NOW);
    expect(circuit.tripped).toBe(true);
    // …y los bucles no esenciales ya no corren, el publicador sí.
    expect(circuit.shouldRunLoop("analytics")).toBe(false);
    expect(circuit.shouldRunLoop("brain-dispatch")).toBe(false);
    expect(circuit.shouldRunLoop("watcher-scan")).toBe(false);
    expect(circuit.shouldRunLoop("seo-retry")).toBe(false);
    expect(circuit.shouldRunLoop("publisher")).toBe(true);
  });

  test("(a2) la alerta por cuota agotada se envía UNA sola vez", () => {
    const circuit = new QuotaCircuit();
    const err = new QuotaExceededError();
    circuit.onD1Error(err, NOW);
    circuit.onD1Error(err, NOW + 1_000);
    circuit.onD1Error(err, NOW + 60_000);
    expect(circuit.alerts).toHaveLength(1);
    expect(circuit.alerts[0]).toContain("7500");
  });

  test("(b) el publicador sigue subiendo con el diario mientras dura el 7500", async () => {
    const d1 = new MockD1();
    const journal = new MockR2Journal();
    const platform = new MockPlatform();
    const circuit = new QuotaCircuit();
    const publisher = new JournalPublisher(journal, platform, TENANT);
    d1.exhausted = true;
    circuit.onD1Error(new QuotaExceededError(), NOW);
    expect(circuit.shouldRunLoop("publisher")).toBe(true);

    const r1 = publisher.publish(91, "tiktok_es", DATE, NOW);
    const r2 = publisher.publish(91, "youtube_es", DATE, NOW);
    expect(r1.duplicate).toBe(false);
    expect(r2.duplicate).toBe(false);
    // La plataforma recibió las 2 subidas…
    expect(platform.publishedCount()).toBe(2);
    // …el diario las registró (intención + resultado por subida)…
    expect(journal.lineCount(TENANT, DATE)).toBe(4);
    expect(journalKey(TENANT, DATE)).toBe("wal/sebastian/2026-11-20.jsonl");
    // …y D1 no recibió NADA (cuota agotada, 0 escrituras exitosas).
    expect(d1.writesSucceeded).toBe(0);
    expect(d1.countPublishLog()).toBe(0);
  });

  test("(c) no hay duplicados: republicar la misma key es no-op", () => {
    const journal = new MockR2Journal();
    const platform = new MockPlatform();
    const publisher = new JournalPublisher(journal, platform, TENANT);
    const first = publisher.publish(91, "tiktok_es", DATE, NOW);
    const again = publisher.publish(91, "tiktok_es", DATE, NOW);
    expect(again.duplicate).toBe(true);
    expect(again.postId).toBe(first.postId);
    expect(platform.publishCalls).toBe(1);
    expect(platform.publishedCount()).toBe(1);
    // La key es clip_id + cuenta + fecha; otra cuenta u otra fecha sí es otra subida.
    expect(idempotencyKey(91, "tiktok_es", DATE)).toBe("91:tiktok_es:2026-11-20");
    publisher.publish(91, "youtube_es", DATE, NOW);
    publisher.publish(91, "tiktok_es", "2026-11-21", NOW);
    expect(platform.publishedCount()).toBe(3);
  });

  test("(d) al volver la cuota, el diario se vuelca bien en D1 (y re-volcar no duplica)", async () => {
    const d1 = new MockD1();
    const journal = new MockR2Journal();
    const platform = new MockPlatform();
    const circuit = new QuotaCircuit();
    const publisher = new JournalPublisher(journal, platform, TENANT);
    d1.exhausted = true;
    circuit.onD1Error(new QuotaExceededError(), NOW);
    publisher.publish(91, "tiktok_es", DATE, NOW);
    publisher.publish(91, "youtube_es", DATE, NOW);
    publisher.publish(117, "tiktok_es", DATE, NOW);

    // Mientras dura el 7500, el volcado no puede escribir: 0 aplicadas.
    await expect(drainJournalToD1(d1, journal, TENANT, DATE, NOW)).rejects.toThrow(
      "7500",
    );
    expect(d1.countPublishLog()).toBe(0);

    // Vuelve la cuota (reset 21:00 -03): se reanudan los bucles y se vuelca.
    d1.exhausted = false;
    circuit.reset();
    expect(circuit.shouldRunLoop("analytics")).toBe(true);
    const first = await drainJournalToD1(d1, journal, TENANT, DATE, NOW);
    expect(first).toEqual({ applied: 3, skipped: 0 });
    expect(d1.countPublishLog()).toBe(3);
    // Segunda pasada: idempotente, 0 aplicadas y 3 saltadas (sin duplicar).
    const second = await drainJournalToD1(d1, journal, TENANT, DATE, NOW);
    expect(second).toEqual({ applied: 0, skipped: 3 });
    expect(d1.countPublishLog()).toBe(3);
  });
});

describe("Simulacro 2 — reinicio del Worker a mitad de una subida", () => {
  test("intención en el diario + post ya en plataforma: NO se vuelve a subir, solo se anota", () => {
    const journal = new MockR2Journal();
    const platform = new MockPlatform();
    const publisher = new JournalPublisher(journal, platform, TENANT);
    // El Worker publica en la plataforma y se reinicia ANTES de anotar.
    expect(() =>
      publisher.publishWithCrashAfterPlatformCall(91, "tiktok_es", DATE, NOW),
    ).toThrow("SIMULATED WORKER RESTART");
    expect(platform.publishedCount()).toBe(1);
    expect(platform.publishCalls).toBe(1);
    expect(journal.hasResult(TENANT, DATE, idempotencyKey(91, "tiktok_es", DATE))).toBeUndefined();
    expect(journal.hasIntent(TENANT, DATE, idempotencyKey(91, "tiktok_es", DATE))).toBe(true);

    // Reinicio: la recuperación ve la intención, pregunta a la plataforma
    // por la key y encuentra el post: anota sin republicar.
    const recovered = publisher.recover(91, "tiktok_es", DATE, NOW + 1_000);
    expect(recovered.republished).toBe(false);
    expect(platform.publishCalls).toBe(1);
    expect(platform.publishedCount()).toBe(1);
    expect(
      journal.hasResult(TENANT, DATE, idempotencyKey(91, "tiktok_es", DATE))
        ?.platform_post_id,
    ).toBe(recovered.postId);
  });

  test("intención en el diario + NADA en plataforma: se publica una sola vez", () => {
    const journal = new MockR2Journal();
    const platform = new MockPlatform();
    const publisher = new JournalPublisher(journal, platform, TENANT);
    // Intención anotada, el Worker muere ANTES de llamar a la plataforma.
    journal.append(TENANT, DATE, {
      idempotency_key: idempotencyKey(117, "tiktok_es", DATE),
      clip_id: 117,
      account: "tiktok_es",
      date: DATE,
      action: "publish",
      status: "intent",
      ts: NOW,
    });
    expect(platform.findByKey(idempotencyKey(117, "tiktok_es", DATE))).toBeNull();
    const recovered = publisher.recover(117, "tiktok_es", DATE, NOW + 1_000);
    expect(recovered.republished).toBe(true);
    expect(platform.publishCalls).toBe(1);
    expect(platform.publishedCount()).toBe(1);
    // Una segunda recuperación ya no republica.
    const again = publisher.recover(117, "tiktok_es", DATE, NOW + 2_000);
    expect(again.republished).toBe(false);
    expect(platform.publishCalls).toBe(1);
  });
});

describe("Simulacro 3 — migración con cálculo previo y rechazo automático", () => {
  test("caso acepta: la estimación cabe en el 20% de la cuota restante", async () => {
    const d1 = new MockD1();
    // Cuota 100.000, usadas 90.000 -> restantes 10.000 -> tope 2.000 filas.
    const pre = migrationPrecheck({ estimatedRows: 1_500, usedToday: 90_000 });
    expect(pre.allowed).toBe(true);
    if (pre.allowed) {
      expect(pre.remaining).toBe(10_000);
      expect(pre.maxAllowed).toBe(2_000);
    }
    const rows = Array.from({ length: 1_500 }, (_, i) => ({
      key: `mig-${i}`,
      clipId: 200 + i,
      account: "tiktok_es",
      date: DATE,
    }));
    const result = await runMigrationIfAllowed(d1, pre, rows, NOW);
    expect(result).toEqual({ ran: true, inserted: 1_500 });
    expect(d1.countPublishLog()).toBe(1_500);
  });

  test("caso acepta en el borde: exactamente el 20% de la restante", () => {
    const pre = migrationPrecheck({ estimatedRows: 2_000, usedToday: 90_000 });
    expect(pre.allowed).toBe(true);
  });

  test("caso rechaza: la estimación supera el 20% de la cuota restante", async () => {
    const d1 = new MockD1();
    // Estimación 3.000 > tope 2.000 (20% de 10.000 restantes).
    const pre = migrationPrecheck({ estimatedRows: 3_000, usedToday: 90_000 });
    expect(pre.allowed).toBe(false);
    if (!pre.allowed) {
      expect(pre.reason).toContain("RECHAZADA");
      expect(pre.maxAllowed).toBe(2_000);
    }
    const rows = Array.from({ length: 3_000 }, (_, i) => ({
      key: `mig-${i}`,
      clipId: 200 + i,
      account: "tiktok_es",
      date: DATE,
    }));
    const before = d1.writesAttempted;
    const result = await runMigrationIfAllowed(d1, pre, rows, NOW);
    expect(result.ran).toBe(false);
    expect(result.inserted).toBe(0);
    // Rechazo automático = CERO escrituras intentadas.
    expect(d1.writesAttempted).toBe(before);
    expect(d1.countPublishLog()).toBe(0);
  });

  test("caso rechaza extremo: cuota casi agotada, cualquier migración se rechaza", () => {
    // Usadas 99.500 -> restantes 500 -> tope 100 filas.
    const pre = migrationPrecheck({ estimatedRows: 101, usedToday: 99_500 });
    expect(pre.allowed).toBe(false);
    expect(migrationPrecheck({ estimatedRows: 100, usedToday: 99_500 }).allowed).toBe(
      true,
    );
    expect(DAILY_D1_QUOTA_ROWS).toBe(100_000);
  });
});

// PENDIENTE — integración contra el código real A4 / A5 / B1-B2.
// Motivo: a 2026-11-20 ~05:50 -03 ni los informes A4.md, A5.md y B1-B2.md
// ni sus módulos existen en el árbol (se escriben en paralelo). Estos tests
// quedan en skip hasta que aterricen; entonces deben re-enlazarse a src/ y
// pasar en verde contra la implementación real, no contra el modelo de
// referencia de anti-recaida-harness.ts.
describe.skip("PENDIENTE — integración con A4/A5/B1-B2 reales (código aún no en el árbol)", () => {
  test("A4 real: el circuito 7500 del Worker detiene los bucles y alerta una vez", () => {
    throw new Error("PENDIENTE: módulo A4 aún no existe en src/");
  });
  test("A5 real: el chequeo previo rechaza la migración en el Worker", () => {
    throw new Error("PENDIENTE: módulo A5 aún no existe en src/");
  });
  test("B1/B2 real: el diario wal/<tenant>/<fecha>.jsonl y el volcado usan la key clip+cuenta+fecha", () => {
    throw new Error("PENDIENTE: módulos B1/B2 aún no existen en src/");
  });
});
