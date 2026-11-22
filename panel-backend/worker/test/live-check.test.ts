// Tests de la comprobación de directos (live-check.ts, 2026-11-19):
// categoría GTA VI, login de Twitch sin espacios y conteo ES en
// GTA VI. La red (Helix/Kick) no se prueba aquí: checkInfluencersLive
// se verifica en vivo contra /api/influencers/live tras el despliegue.
import { describe, expect, test } from "bun:test";
import { normalizeHandle } from "../src/db";
import {
  countLiveEsGta6,
  isGta6Category,
  twitchLogin,
  type LiveCheckEntry,
} from "../src/live-check";

function entry(over: Partial<LiveCheckEntry> = {}): LiveCheckEntry {
  return {
    id: 1,
    handle: "Rubius",
    platform: "twitch",
    language: "es",
    live: false,
    game: null,
    title: null,
    viewers: null,
    reason: "",
    ...over,
  };
}

describe("normalizeHandle quita espacios (2026-11-19)", () => {
  test("\"Kai Cenat\" coincide con \"kaicenat\" (el cerebro manda el handle sin espacios)", () => {
    expect(normalizeHandle("Kai Cenat")).toBe("kaicenat");
    expect(normalizeHandle("kaicenat")).toBe("kaicenat");
    expect(normalizeHandle("Will Neff")).toBe("willneff");
  });
  test("sigue quitando @ y bajando a minúsculas", () => {
    expect(normalizeHandle("@Rubius")).toBe("rubius");
    expect(normalizeHandle("  IlloJuan  ")).toBe("illojuan");
  });
  test("twitchLogin sigue dando el login sin espacios ni caracteres raros", () => {
    expect(twitchLogin("Kai Cenat")).toBe("kaicenat");
    expect(twitchLogin("Will Neff")).toBe("willneff");
  });
});

describe("isGta6Category", () => {
  test("acepta las variantes canónicas (mismo conjunto que clipagent/category.py)", () => {
    for (const g of ["GTA VI", "Grand Theft Auto VI", "GTA 6", "Grand Theft Auto 6", "gta6", "Grand Theft Auto VI (GTA 6)"]) {
      expect(isGta6Category(g)).toBe(true);
    }
  });

  test("acepta el prefijo con sufijo (categoría de Kick 'Grand Theft Auto VI (GTA)')", () => {
    expect(isGta6Category("Grand Theft Auto VI (GTA)")).toBe(true);
    expect(isGta6Category("GTA VI RP")).toBe(true);
  });

  test("rechaza otros juegos y vacío", () => {
    for (const g of ["GTA V", "Just Chatting", "Grand Theft Auto V", "", null, undefined]) {
      expect(isGta6Category(g)).toBe(false);
    }
  });

  test("normaliza mayúsculas y espacios", () => {
    expect(isGta6Category("  grand   theft auto   VI ")).toBe(true);
    expect(isGta6Category("GTA  VI")).toBe(true);
  });
});

describe("twitchLogin", () => {
  test("quita espacios: 'Kai Cenat' es kaicenat (Helix 400 si va con espacio)", () => {
    expect(twitchLogin("Kai Cenat")).toBe("kaicenat");
    expect(twitchLogin("Will Neff")).toBe("willneff");
  });

  test("minúsculas y sin @", () => {
    expect(twitchLogin("@Rubius")).toBe("rubius");
    expect(twitchLogin("IlloJuan")).toBe("illojuan");
  });

  test("deja guiones bajos y números", () => {
    expect(twitchLogin("xQc_2")).toBe("xqc_2");
  });
});

describe("countLiveEsGta6", () => {
  test("0 con todos los ES offline", () => {
    const entries = [
      entry({ handle: "Rubius", live: false }),
      entry({ handle: "IlloJuan", live: false }),
      entry({ handle: "AuronPlay", live: false }),
    ];
    expect(countLiveEsGta6(entries)).toBe(0);
  });

  test("cuenta solo ES en directo EN GTA VI", () => {
    const entries = [
      entry({ handle: "Rubius", live: true, game: "Grand Theft Auto VI", viewers: 100 }),
      entry({ handle: "IlloJuan", live: true, game: "Just Chatting" }),
      entry({ handle: "xQc", language: "en", platform: "kick", live: true, game: "Grand Theft Auto VI" }),
      entry({ handle: "AuronPlay", live: false }),
    ];
    expect(countLiveEsGta6(entries)).toBe(1);
  });

  test("un ES en directo en otro juego no cuenta", () => {
    const entries = [entry({ handle: "Rubius", live: true, game: "GTA V" })];
    expect(countLiveEsGta6(entries)).toBe(0);
  });

  test("un solo ES sin verificar (null) devuelve null: no se afirma que no hay directos", () => {
    const entries = [
      entry({ handle: "Rubius", live: false }),
      entry({ handle: "IlloJuan", live: null, reason: "Twitch Helix devolvió HTTP 400" }),
    ];
    expect(countLiveEsGta6(entries)).toBe(null);
  });

  test("EN sin verificar no bloquea el conteo ES", () => {
    const entries = [
      entry({ handle: "Rubius", live: false }),
      entry({ handle: "xQc", language: "en", platform: "kick", live: null, reason: "No se pudo hablar con Kick." }),
    ];
    expect(countLiveEsGta6(entries)).toBe(0);
  });

  test("plataforma sin comprobación (youtube) en ES devuelve null", () => {
    const entries = [entry({ handle: "StreamerES", platform: "youtube", live: null, reason: "La plataforma youtube no tiene comprobación de directo." })];
    expect(countLiveEsGta6(entries)).toBe(null);
  });
});
