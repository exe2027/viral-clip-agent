// Marca de agua (2026-11-18, migración 0025): defaults, validación
// de Ajustes, aislamiento por tenant en la key R2 y que el logo
// activo NUNCA se copie con la configuración base.
import { describe, expect, test } from "bun:test";
import { effectiveSettings, extractBaseSettings, validateSettingsInput } from "../src/db";
import { r2KeyForWatermark, WATERMARK_MAX_BYTES, WATERMARK_URL_TTL_S } from "../src/r2";

describe("marca de agua: ajustes efectivos", () => {
  test("defaults = apagada, sin logo, centrada, 320 px, 20%", () => {
    const s = effectiveSettings({});
    expect(s.watermark_enabled).toBe(false);
    expect(s.watermark_logo_id).toBe(null);
    expect(s.watermark_opacity).toBe(20);
    expect(s.watermark_size_px).toBe(320);
    expect(s.watermark_position).toBe("center");
  });
  test("lo mal puesto vuelve al default en silencio", () => {
    const s = effectiveSettings({
      watermark_enabled: "sí",
      watermark_logo_id: "x",
      watermark_opacity: 150,
      watermark_size_px: 10,
      watermark_position: "middle",
    });
    expect(s.watermark_enabled).toBe(false);
    expect(s.watermark_logo_id).toBe(null);
    expect(s.watermark_opacity).toBe(100);
    expect(s.watermark_size_px).toBe(50);
    expect(s.watermark_position).toBe("center");
  });
  test("valores buenos se conservan", () => {
    const s = effectiveSettings({
      watermark_enabled: true,
      watermark_logo_id: 7,
      watermark_opacity: 0,
      watermark_size_px: 1000,
      watermark_position: "bottom-right",
    });
    expect(s.watermark_enabled).toBe(true);
    expect(s.watermark_logo_id).toBe(7);
    expect(s.watermark_opacity).toBe(0);
    expect(s.watermark_size_px).toBe(1000);
    expect(s.watermark_position).toBe("bottom-right");
  });
});

describe("marca de agua: validación del PUT", () => {
  test("acepta valores buenos", () => {
    expect(validateSettingsInput({
      watermark_enabled: true, watermark_logo_id: 3,
      watermark_opacity: 20, watermark_size_px: 320, watermark_position: "center",
    })).toBe(null);
    expect(validateSettingsInput({ watermark_logo_id: null })).toBe(null);
  });
  test("rechaza lo inválido con 400", () => {
    expect(validateSettingsInput({ watermark_enabled: "sí" })).toMatch(/true o false/);
    expect(validateSettingsInput({ watermark_logo_id: 0 })).toMatch(/id de logo/);
    expect(validateSettingsInput({ watermark_logo_id: 1.5 })).toMatch(/id de logo/);
    expect(validateSettingsInput({ watermark_opacity: -1 })).toMatch(/0 y 100/);
    expect(validateSettingsInput({ watermark_opacity: 101 })).toMatch(/0 y 100/);
    expect(validateSettingsInput({ watermark_size_px: 49 })).toMatch(/50 y 1000/);
    expect(validateSettingsInput({ watermark_size_px: 1001 })).toMatch(/50 y 1000/);
    expect(validateSettingsInput({ watermark_position: "middle" })).toMatch(/center/);
  });
});

describe("marca de agua: configuración base (onboarding)", () => {
  test("copia interruptor y valores, NUNCA el logo activo", () => {
    const out = extractBaseSettings({
      watermark_enabled: true,
      watermark_logo_id: 9,
      watermark_opacity: 30,
      watermark_size_px: 400,
      watermark_position: "top-right",
    });
    expect(out.watermark_enabled).toBe(true);
    expect(out.watermark_opacity).toBe(30);
    expect(out.watermark_size_px).toBe(400);
    expect(out.watermark_position).toBe("top-right");
    expect("watermark_logo_id" in out).toBe(false);
  });
});

describe("marca de agua: keys R2", () => {
  test("prefijo por tenant + componente aleatorio + nombre saneado", () => {
    const a = r2KeyForWatermark("mi logo!.png", "sebastian");
    const b = r2KeyForWatermark("mi logo!.png", "sebastian");
    expect(a).toMatch(/^watermarks\/sebastian\/[0-9a-f]{16}_mi_logo_\.png$/);
    expect(a).not.toBe(b);
    expect(r2KeyForWatermark("logo.png", "kaez")).toMatch(/^watermarks\/kaez\//);
  });
  test("constantes: 5 MB y URL firmada de 60 min", () => {
    expect(WATERMARK_MAX_BYTES).toBe(5 * 1024 * 1024);
    expect(WATERMARK_URL_TTL_S).toBe(3600);
  });
});
