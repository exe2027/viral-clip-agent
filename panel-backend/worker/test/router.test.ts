// Tests del router sin estado (worker/src/router.ts, "un Worker por
// usuario", 2026-11-19). Sin red real (fetch con mock), sin D1, sin
// secretos: el router solo decodifica el tenant del token y reenvía.
import { afterEach, describe, expect, test } from "bun:test";
import router, { type RouterEnv } from "../src/router";

const ENV: RouterEnv = {
  WORKER_SEBASTIAN_URL: "https://avc-sebastian.test",
  WORKER_KAEZ_URL: "https://avc-kaez.test",
};

const ctx = { waitUntil: () => {}, passThroughOnException: () => {} } as never;

function sessionToken(tenant: string): string {
  const body = Buffer.from(JSON.stringify({ sub: "cerebro", tenant, exp: Date.now() + 3_600_000 })).toString("base64url");
  return `${body}.firma-no-verificada-por-el-router`;
}

function oauthState(tenant: string): string {
  const body = Buffer.from(JSON.stringify({ provider: "google", tenant, exp: Date.now() + 600_000 })).toString("base64url");
  return `${body}.firma-no-verificada-por-el-router`;
}

interface Call {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: string;
}

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

function stubFetch(handler: (call: Call) => Response | Promise<Response>): Call[] {
  const calls: Call[] = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const headers: Record<string, string> = {};
    if (init?.headers instanceof Headers) init.headers.forEach((v, k) => { headers[k] = v; });
    else if (init?.headers) Object.assign(headers, init.headers);
    let body = "";
    if (init?.body != null) {
      body = typeof init.body === "string" ? init.body : await new Response(init.body as BodyInit).text();
    }
    const call: Call = { url, method: init?.method ?? "GET", headers, body };
    calls.push(call);
    return handler(call);
  }) as typeof fetch;
  return calls;
}

const jsonRes = (data: unknown, status = 200): Response =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

async function callRouter(
  method: string,
  path: string,
  opts: { token?: string; body?: string; headers?: Record<string, string> } = {},
): Promise<{ status: number; json: Record<string, unknown> | null; text: string; headers: Headers }> {
  const headers: Record<string, string> = { ...(opts.headers ?? {}) };
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await router.fetch(
    new Request(`https://avc.vikingosfoodspa.workers.dev${path}`, {
      method,
      headers,
      body: opts.body,
    }),
    ENV,
    ctx,
  );
  const text = await res.text();
  let parsed: Record<string, unknown> | null = null;
  try {
    parsed = JSON.parse(text) as Record<string, unknown>;
  } catch {
    parsed = null;
  }
  return { status: res.status, json: parsed, text, headers: res.headers };
}

describe("GET /api/health (propio del router)", () => {
  test("devuelve modo router sin tocar ningún Worker destino", async () => {
    const calls = stubFetch(() => jsonRes({ ok: true }));
    const r = await callRouter("GET", "/api/health");
    expect(r.status).toBe(200);
    expect(r.json?.mode).toBe("router");
    expect(r.json?.app).toBe("avc-router");
    expect(r.json?.tenants).toEqual(["sebastian", "kaez"]);
    expect(calls.length).toBe(0);
  });
});

describe("POST /api/brain/session (probing sebastian -> kaez)", () => {
  test("token de kaez: falla en sebastian (401) y canjea en kaez (200); sebastian recibe el body una sola vez", async () => {
    const calls = stubFetch((c) => {
      if (c.url.startsWith("https://avc-sebastian.test")) return jsonRes({ ok: false, error: "Token del cerebro inválido." }, 401);
      return jsonRes({ ok: true, token: "sesion-kaez", tenant: "kaez", expires_at: 123 });
    });
    const body = JSON.stringify({ token: "token-de-kaez" });
    const r = await callRouter("POST", "/api/brain/session", { body });
    expect(r.status).toBe(200);
    expect(r.json?.tenant).toBe("kaez");
    expect(r.json?.token).toBe("sesion-kaez");
    expect(calls.length).toBe(2);
    expect(calls[0]?.url).toBe("https://avc-sebastian.test/api/brain/session");
    expect(calls[0]?.body).toBe(body);
    expect(calls[1]?.url).toBe("https://avc-kaez.test/api/brain/session");
    expect(calls[1]?.body).toBe(body);
  });

  test("token de sebastian: canjea en el primer intento y no se prueba kaez", async () => {
    const calls = stubFetch((c) => {
      if (c.url.startsWith("https://avc-sebastian.test")) return jsonRes({ ok: true, token: "sesion-seba", tenant: "sebastian" });
      return jsonRes({ ok: false, error: "no debería llegar aquí" }, 401);
    });
    const r = await callRouter("POST", "/api/brain/session", { body: JSON.stringify({ token: "token-de-seba" }) });
    expect(r.status).toBe(200);
    expect(r.json?.tenant).toBe("sebastian");
    expect(calls.length).toBe(1);
  });

  test("token inválido en ambos: devuelve el error del PRIMER intento (sebastian)", async () => {
    const calls = stubFetch((c) => {
      if (c.url.startsWith("https://avc-sebastian.test")) return jsonRes({ ok: false, error: "Token del cerebro inválido." }, 401);
      return jsonRes({ ok: false, error: "otro error" }, 401);
    });
    const r = await callRouter("POST", "/api/brain/session", { body: JSON.stringify({ token: "no-existe" }) });
    expect(r.status).toBe(401);
    expect(r.json?.error).toBe("Token del cerebro inválido.");
    expect(calls.length).toBe(2);
  });

  test("sin token: 400 sin probar ningún Worker", async () => {
    const calls = stubFetch(() => jsonRes({ ok: true }));
    const r = await callRouter("POST", "/api/brain/session", { body: JSON.stringify({}) });
    expect(r.status).toBe(400);
    expect(calls.length).toBe(0);
  });
});

describe("rutas con sesión (tenant del Bearer)", () => {
  test("GET /api/bridge/credentials con sesión de kaez va a kaez con path, query y Authorization intactos", async () => {
    const calls = stubFetch(() => jsonRes({ ok: true, tenant: "kaez" }));
    const token = sessionToken("kaez");
    const r = await callRouter("GET", "/api/bridge/credentials?x=1", { token });
    expect(r.status).toBe(200);
    expect(calls.length).toBe(1);
    expect(calls[0]?.url).toBe("https://avc-kaez.test/api/bridge/credentials?x=1");
    expect(calls[0]?.headers.authorization).toBe(`Bearer ${token}`);
    expect(r.headers.get("x-avc-router")).toBe("avc-router");
  });

  test("sin sesión: 401 sin reenviar", async () => {
    const calls = stubFetch(() => jsonRes({ ok: true }));
    const r = await callRouter("GET", "/api/bridge/credentials");
    expect(r.status).toBe(401);
    expect(calls.length).toBe(0);
  });

  test("sesión sin tenant válido: 401 sin reenviar", async () => {
    const calls = stubFetch(() => jsonRes({ ok: true }));
    const r = await callRouter("GET", "/api/clips", { token: sessionToken("colega") });
    expect(r.status).toBe(401);
    expect(calls.length).toBe(0);
  });

  test("PUT /api/clips/:id/video reenvía el cuerpo en streaming sin cargarlo en memoria", async () => {
    const calls = stubFetch(() => jsonRes({ ok: true }));
    const r = await callRouter("PUT", "/api/clips/123/video", {
      token: sessionToken("sebastian"),
      body: "MP4BYTES",
      headers: { "Content-Type": "video/mp4" },
    });
    expect(r.status).toBe(200);
    expect(calls.length).toBe(1);
    expect(calls[0]?.url).toBe("https://avc-sebastian.test/api/clips/123/video");
    expect(calls[0]?.method).toBe("PUT");
    expect(calls[0]?.body).toBe("MP4BYTES");
  });

  test("/api/bridge/* sin sesión pero con ?tenant= válido usa ese tenant (la auth la hace el destino)", async () => {
    const calls = stubFetch(() => jsonRes({ ok: false, error: "Bridge no autorizado." }, 401));
    const r = await callRouter("GET", "/api/bridge/settings?tenant=kaez");
    expect(calls.length).toBe(1);
    expect(calls[0]?.url).toBe("https://avc-kaez.test/api/bridge/settings?tenant=kaez");
    expect(r.status).toBe(401);
  });
});

describe("GET /api/oauth/:provider/callback (tenant del state)", () => {
  test("state de kaez va a kaez con la query completa", async () => {
    const calls = stubFetch(() => new Response("<html>ok</html>", { status: 200, headers: { "Content-Type": "text/html" } }));
    const state = oauthState("kaez");
    const r = await callRouter("GET", `/api/oauth/google/callback?code=abc&state=${encodeURIComponent(state)}`);
    expect(r.status).toBe(200);
    expect(calls.length).toBe(1);
    expect(calls[0]?.url).toContain("https://avc-kaez.test/api/oauth/google/callback?code=abc&state=");
    expect(r.text).toContain("ok");
  });

  test("sin state: 400 sin reenviar", async () => {
    const calls = stubFetch(() => jsonRes({ ok: true }));
    const r = await callRouter("GET", "/api/oauth/meta/callback?code=abc");
    expect(r.status).toBe(400);
    expect(calls.length).toBe(0);
  });
});

describe("POST /api/auth/login (mapa usuario->Worker)", () => {
  test("usuario kaez va al Worker de kaez y la respuesta (incluida Set-Cookie) pasa tal cual", async () => {
    const calls = stubFetch(() =>
      new Response(JSON.stringify({ ok: true, tenant: "kaez", token: "t" }), {
        status: 200,
        headers: { "Content-Type": "application/json", "Set-Cookie": "panel-session=t; HttpOnly" },
      }),
    );
    const r = await callRouter("POST", "/api/auth/login", {
      body: JSON.stringify({ username: "kaez", password: "secreto" }),
    });
    expect(r.status).toBe(200);
    expect(calls.length).toBe(1);
    expect(calls[0]?.url).toBe("https://avc-kaez.test/api/auth/login");
    expect(calls[0]?.body).toBe(JSON.stringify({ username: "kaez", password: "secreto" }));
    expect(r.headers.get("set-cookie")).toContain("panel-session=t");
  });

  test("usuario desconocido: 401 genérico sin reenviar (no revela si existe)", async () => {
    const calls = stubFetch(() => jsonRes({ ok: true }));
    const r = await callRouter("POST", "/api/auth/login", {
      body: JSON.stringify({ username: "nadie", password: "x" }),
    });
    expect(r.status).toBe(401);
    expect(r.json?.error).toBe("Usuario o contraseña incorrectos.");
    expect(calls.length).toBe(0);
  });
});

describe("rutas fuera del tráfico del cerebro", () => {
  test("ruta no cubierta: 404 sin reenviar", async () => {
    const calls = stubFetch(() => jsonRes({ ok: true }));
    const r = await callRouter("GET", "/api/auth/me", { token: sessionToken("sebastian") });
    expect(r.status).toBe(404);
    expect(calls.length).toBe(0);
  });
});
