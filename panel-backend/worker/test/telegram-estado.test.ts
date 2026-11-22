// Tests de /estado (2026-11-19, aceptación automática): el mensaje
// dice si los clips se publican solos o esperan aprobación manual,
// y nunca inventa el estado si el ajuste no se pudo leer.
import { describe, expect, test } from "bun:test";
import { buildStatusMessage } from "../src/telegram";

const base = {
  running: true,
  startedAt: 1795090000000,
  lastNote: "",
  failedCount: 0,
  lastEvent: null,
  tenant: "sebastian",
} as const;

describe("buildStatusMessage: aceptación automática", () => {
  test("autoPublish true: dice que se publican solos, sin esperar aprobación", () => {
    const msg = buildStatusMessage({ ...base, autoPublish: true });
    expect(msg).toContain("Aceptación automática: activada");
    expect(msg).toContain("se publican solos, sin esperar aprobación");
    expect(msg).not.toContain("esperan aprobación manual");
  });

  test("autoPublish false: dice que esperan aprobación manual", () => {
    const msg = buildStatusMessage({ ...base, autoPublish: false });
    expect(msg).toContain("Aceptación automática: desactivada");
    expect(msg).toContain("esperan aprobación manual");
  });

  test("autoPublish null: no inventa ninguna línea de aceptación", () => {
    const msg = buildStatusMessage({ ...base, autoPublish: null });
    expect(msg).not.toContain("Aceptación automática");
    expect(msg).toContain("🟢 Worker en marcha");
    expect(msg).toContain("Fallidos pendientes: 0");
  });
});
