// Regresión 2026-11-18 (REAL TEST clip 11, job 8 Facebook):
// el POST de un chunk a rupload.facebook.com se cayó a mitad de
// lectura del cuerpo ("Network connection lost") y el paso entero
// se marcó como "error interno del Worker" sin motivo útil.
// 1) httpJson ya no deja escapar esa excepción: la convierte en
//    status 0 reintentable con el motivo en el body.
// 2) El chunk de subida a Facebook es de 8 MB (antes 20 MB): un
//    cuerpo menor tiene menos probabilidad de cortarse a mitad.
import { afterEach, describe, expect, test } from "bun:test";
import { httpJson } from "../src/publish/http";
import { FB_UPLOAD_CHUNK_BYTES } from "../src/publish/facebook";

const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
});

describe("httpJson con cuerpo roto", () => {
  test("un cuerpo que se corta a mitad no lanza: devuelve status 0 reintentable", async () => {
    const broken = new Response(
      new ReadableStream({
        start(c) {
          c.enqueue(new TextEncoder().encode('{"ok":true}'));
        },
        pull(c) {
          c.error(new Error("Network connection lost."));
        },
      }),
    );
    globalThis.fetch = (async () => broken) as typeof fetch;
    const r = await httpJson("POST", "https://rupload.facebook.com/video-upload/v26.0/123");
    expect(r.status).toBe(0);
    expect(r.text).toContain("Network connection lost");
  });

  test("un fetch que falla de entrada sigue devolviendo status 0", async () => {
    globalThis.fetch = (async () => {
      throw new Error("Network connection lost.");
    }) as typeof fetch;
    const r = await httpJson("POST", "https://graph.facebook.com/v26.0/1/video_reels");
    expect(r.status).toBe(0);
    expect(r.text).toContain("Network connection lost");
  });
});

describe("chunk de subida a Facebook", () => {
  test("8 MB, no 20 MB", () => {
    expect(FB_UPLOAD_CHUNK_BYTES).toBe(8 * 1024 * 1024);
  });
});
