// Regresión 2026-11-19 (REAL, job 17 clip 23): la subida a Facebook
// se atascaba en bucle — el chunk 2 (final) devolvía 412 en 3 sesiones
// seguidas porque nuestro offset local (8388608) no coincidía con el
// del servidor. El end_offset del chunk 1 no es de fiar: la única
// fuente fiable es GET /{video-id}?fields=status →
// uploading_phase.bytes_transfered (Meta). El paso ahora reconcilia
// con el servidor antes de cada chunk con offset > 0, y ante un 412
// retoma desde el offset del servidor en vez de reiniciar a ciegas.
// Además, un chunk corto de R2 ya no se sube (desincronizaría la
// sesión): es error reintentable.
import { afterEach, describe, expect, test } from "bun:test";
import { facebookStep } from "../src/publish/facebook";
import type { Env } from "../src/cloudflare";
import type { PublishContext } from "../src/publish/index";

const realFetch = globalThis.fetch;
const TOTAL = 9 * 1024 * 1024; // 9 MB: chunk 1 = 8 MB, chunk 2 = 1 MB
const CHUNK = 8 * 1024 * 1024;

afterEach(() => {
  globalThis.fetch = realFetch;
});

function jsonResponse(status: number, obj: unknown): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

class FakeR2Range {
  constructor(
    private data: Uint8Array,
    private truncateTo?: number,
  ) {}
  async get(_key: string, opts?: { range?: { offset: number; length?: number } }): Promise<{ body: ReadableStream } | null> {
    const off = opts?.range?.offset ?? 0;
    const len = opts?.range?.length ?? this.data.byteLength - off;
    const want = this.truncateTo !== undefined ? Math.min(len, this.truncateTo) : len;
    const slice = this.data.slice(off, off + want);
    return { body: new ReadableStream({ start(c) { c.enqueue(slice); c.close(); } }) };
  }
  async head(_key: string): Promise<{ size: number } | null> {
    return { size: this.data.byteLength };
  }
}

interface Call {
  url: string;
  offsetHeader?: string;
  bodyLen: number;
}

function installFetch(handlers: {
  statusQueue: number[];
  rupload: (offset: string) => { status: number; body: Record<string, unknown> };
}): { ruploadCalls: Call[]; statusCalls: number } {
  const ruploadCalls: Call[] = [];
  let statusCalls = 0;
  const statusQueue = [...handlers.statusQueue];
  globalThis.fetch = (async (url: unknown, init?: unknown) => {
    const u = String(url);
    const headers = ((init as RequestInit | undefined)?.headers ?? {}) as Record<string, string>;
    const body = (init as RequestInit | undefined)?.body as Uint8Array | undefined;
    if (u.includes("rupload.facebook.com")) {
      const offset = headers["offset"] ?? "";
      ruploadCalls.push({ url: u, offsetHeader: offset, bodyLen: body?.byteLength ?? 0 });
      const r = handlers.rupload(offset);
      return jsonResponse(r.status, r.body);
    }
    if (u.includes("graph.facebook.com") && u.includes("fields=status")) {
      statusCalls += 1;
      const v = statusQueue.shift();
      if (v === undefined) return jsonResponse(200, { status: {} });
      return jsonResponse(200, { status: { uploading_phase: { bytes_transfered: v } } });
    }
    throw new Error(`fetch sin mock: ${u}`);
  }) as typeof fetch;
  return { ruploadCalls, get statusCalls() { return statusCalls; } };
}

function makeCtx(resumeData: Record<string, unknown>, bucket: FakeR2Range): { ctx: PublishContext; env: Env } {
  const ctx = {
    job: { resumeData, r2_key: "clips/sebastian/23/test.mp4" },
    creds: { facebook: "PAGE_TOKEN", pageId: "999" },
    r2Size: TOTAL,
  } as unknown as PublishContext;
  const env = { CLIPS_BUCKET: bucket } as unknown as Env;
  return { ctx, env };
}

describe("facebook: reconciliación del offset con el servidor", () => {
  test("412 en el chunk 2 con servidor en 0: retoma desde 0 con la misma sesión", async () => {
    const bucket = new FakeR2Range(new Uint8Array(TOTAL));
    const { ctx, env } = makeCtx({ step: "upload", videoId: "VID1", offset: CHUNK, totalBytes: TOTAL }, bucket);
    installFetch({
      // 1ª consulta (antes del chunk): el servidor dice 8388608 → se
      // intenta el chunk 2. 2ª consulta (tras el 412): dice 0.
      statusQueue: [CHUNK, 0],
      rupload: (offset) => (offset === String(CHUNK) ? { status: 412, body: {} } : { status: 206, body: { end_offset: CHUNK } }),
    });
    const r = await facebookStep(ctx, env);
    expect(r.mode).toBe("processing");
    if (r.mode !== "processing") throw new Error("modo inesperado");
    expect(r.resumeData).toMatchObject({ step: "upload", videoId: "VID1", offset: 0, totalBytes: TOTAL });
  });

  test("412 en el chunk 2 con servidor en 8388608: reintenta el chunk 2", async () => {
    const bucket = new FakeR2Range(new Uint8Array(TOTAL));
    const { ctx, env } = makeCtx({ step: "upload", videoId: "VID1", offset: CHUNK, totalBytes: TOTAL }, bucket);
    installFetch({
      statusQueue: [CHUNK, CHUNK],
      rupload: () => ({ status: 412, body: {} }),
    });
    const r = await facebookStep(ctx, env);
    expect(r.mode).toBe("processing");
    if (r.mode !== "processing") throw new Error("modo inesperado");
    expect(r.resumeData).toMatchObject({ step: "upload", videoId: "VID1", offset: CHUNK });
  });

  test("antes del chunk el servidor manda: local 8388608, servidor 0 → el POST lleva offset 0", async () => {
    const bucket = new FakeR2Range(new Uint8Array(TOTAL));
    const { ctx, env } = makeCtx({ step: "upload", videoId: "VID1", offset: CHUNK, totalBytes: TOTAL }, bucket);
    const { ruploadCalls } = installFetch({
      statusQueue: [0],
      rupload: () => ({ status: 206, body: { end_offset: CHUNK } }),
    });
    const r = await facebookStep(ctx, env);
    expect(r.mode).toBe("processing");
    expect(ruploadCalls).toHaveLength(1);
    expect(ruploadCalls[0].offsetHeader).toBe("0");
    expect(ruploadCalls[0].bodyLen).toBe(CHUNK);
    if (r.mode !== "processing") throw new Error("modo inesperado");
    expect(r.resumeData).toMatchObject({ offset: CHUNK });
  });

  test("servidor con todos los bytes: pasa a finish sin subir nada", async () => {
    const bucket = new FakeR2Range(new Uint8Array(TOTAL));
    const { ctx, env } = makeCtx({ step: "upload", videoId: "VID1", offset: CHUNK, totalBytes: TOTAL }, bucket);
    const { ruploadCalls } = installFetch({
      statusQueue: [TOTAL],
      rupload: () => ({ status: 206, body: { end_offset: CHUNK } }),
    });
    const r = await facebookStep(ctx, env);
    expect(r.mode).toBe("processing");
    expect(ruploadCalls).toHaveLength(0);
    if (r.mode !== "processing") throw new Error("modo inesperado");
    expect(r.resumeData).toMatchObject({ step: "finish", videoId: "VID1" });
  });

  test("R2 corto: no se sube el chunk, error reintentable", async () => {
    // La 2ª mitad del rango no llega: R2 devuelve solo 100 bytes.
    const bucket = new FakeR2Range(new Uint8Array(TOTAL), 100);
    const { ctx, env } = makeCtx({ step: "upload", videoId: "VID1", offset: 0, totalBytes: TOTAL }, bucket);
    const { ruploadCalls } = installFetch({
      statusQueue: [],
      rupload: () => ({ status: 206, body: { end_offset: CHUNK } }),
    });
    const r = await facebookStep(ctx, env);
    expect(r.mode).toBe("error");
    if (r.mode !== "error") throw new Error("modo inesperado");
    expect(r.retryable).toBe(true);
    expect(r.reason).toContain("100 bytes");
    expect(ruploadCalls).toHaveLength(0);
  });

  test("412 deja constancia en el log del panel con el cuerpo de rupload y el offset del servidor", async () => {
    const bucket = new FakeR2Range(new Uint8Array(TOTAL));
    const logged: { sql: string; args: unknown[] }[] = [];
    const fakeDb = {
      prepare: (sql: string) => ({
        bind: (...args: unknown[]) => ({
          run: async () => {
            logged.push({ sql, args });
          },
        }),
      }),
    };
    const { ctx } = makeCtx({ step: "upload", videoId: "VID1", offset: CHUNK, totalBytes: TOTAL }, bucket);
    (ctx.job as unknown as Record<string, unknown>).tenant_id = "sebastian";
    const env = { CLIPS_BUCKET: bucket, DB: fakeDb } as unknown as Env;
    installFetch({
      statusQueue: [CHUNK, 0],
      rupload: () => ({ status: 412, body: { error: { message: "offset mismatch", code: 123 } } }),
    });
    const r = await facebookStep(ctx, env);
    expect(r.mode).toBe("processing");
    const evt = logged.find((l) => l.sql.includes("worker_events"));
    expect(evt).toBeDefined();
    const msg = String(evt?.args[3] ?? "");
    expect(msg).toContain("chunk 412");
    expect(msg).toContain("offset mismatch");
    expect(msg).toContain("servidor: http 200 offset 0");
  });

  test("camino feliz multi-chunk: chunk 1 y chunk 2 hasta finish", async () => {
    const bucket = new FakeR2Range(new Uint8Array(TOTAL));
    // Chunk 1 (offset 0, sin reconciliación).
    const first = makeCtx({ step: "upload", videoId: "VID1", offset: 0, totalBytes: TOTAL }, bucket);
    installFetch({
      statusQueue: [],
      rupload: () => ({ status: 206, body: { end_offset: CHUNK } }),
    });
    const r1 = await facebookStep(first.ctx, first.env);
    expect(r1.mode).toBe("processing");
    if (r1.mode !== "processing") throw new Error("modo inesperado");
    expect(r1.resumeData).toMatchObject({ offset: CHUNK });
    // Chunk 2 (offset 8388608, el servidor confirma): 200 → finish.
    const second = makeCtx({ step: "upload", videoId: "VID1", offset: CHUNK, totalBytes: TOTAL }, bucket);
    const { ruploadCalls } = installFetch({
      statusQueue: [CHUNK],
      rupload: (offset) => {
        expect(offset).toBe(String(CHUNK));
        return { status: 200, body: {} };
      },
    });
    const r2 = await facebookStep(second.ctx, second.env);
    expect(r2.mode).toBe("processing");
    expect(ruploadCalls).toHaveLength(1);
    expect(ruploadCalls[0].bodyLen).toBe(TOTAL - CHUNK);
    if (r2.mode !== "processing") throw new Error("modo inesperado");
    expect(r2.resumeData).toMatchObject({ step: "finish", videoId: "VID1" });
  });
});
