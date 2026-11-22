// Tests del endpoint MCP (2026-11-21, regla 14:17: el MCP es el
// respaldo cuando el panel se traba). Solo la ruta de auth: sin
// auth → 401, token malo → 401, sesión del panel → 200, BRIDGE_TOKEN
// → 200. Sin red, sin D1 real: un D1 falso mínimo (solo la
// consulta de tokens revocados que usa authorizeMcp).
import { describe, expect, test } from "bun:test";
import { createSessionToken } from "../src/auth";
import type { Env } from "../src/cloudflare";
import worker from "../src/index";

const SECRET = Buffer.from("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb").toString("base64"); // SOLO TEST
const BRIDGE = "puente-de-prueba"; // SOLO TEST

type Row = Record<string, unknown>;

class FakeD1 {
  tables: Record<string, Row[]> = {};
  prepare(sql: string) {
    const db = this;
    return {
      bind(...values: unknown[]) {
        return {
          async first<T>(): Promise<T | null> {
            // SELECT token_hash FROM revoked_tokens WHERE token_hash = ?
            if (/revoked_tokens/.test(sql)) {
              const row = (db.tables["revoked_tokens"] ?? []).find((r) => r.token_hash === values[0]);
              return (row as T | undefined) ?? null;
            }
            return null;
          },
          async all<T>(): Promise<{ results: T[]; success: boolean; meta: Record<string, unknown> }> {
            return { results: [] as T[], success: true, meta: {} };
          },
          async run(): Promise<{ success: boolean; meta: Record<string, unknown> }> {
            return { success: true, meta: {} };
          },
        };
      },
    };
  }
}

function makeEnv(): Env {
  return {
    DB: new FakeD1() as unknown as Env["DB"],
    TENANT: "sebastian",
    SESSION_SECRET: SECRET,
    CREDENTIAL_ENCRYPTION_KEY: Buffer.from("0123456789abcdef0123456789abcdef").toString("base64"),
    BRIDGE_TOKEN: BRIDGE,
    GITHUB_TOKEN: "",
    ALLOWED_EMAIL: "",
    GITHUB_REPO: "exe2027/viral-clip-agent",
  } as Env;
}

const ctx = { waitUntil: () => {}, passThroughOnException: () => {} } as never;

async function callMcp(env: Env, headers: Record<string, string> = {}): Promise<{ status: number; json: Record<string, unknown> }> {
  const res = await worker.fetch(new Request("https://worker.test/mcp", { method: "GET", headers }), env, ctx);
  return { status: res.status, json: (await res.json()) as Record<string, unknown> };
}

describe("GET /mcp — auth (respaldo del panel, 14:17)", () => {
  test("sin auth → 401", async () => {
    const r = await callMcp(makeEnv());
    expect(r.status).toBe(401);
    expect(String(r.json.error)).toMatch(/MCP no autorizado/);
  });

  test("Bearer malo → 401", async () => {
    const r = await callMcp(makeEnv(), { Authorization: "Bearer token-inventado" });
    expect(r.status).toBe(401);
  });

  test("X-Bridge-Token malo → 401", async () => {
    const r = await callMcp(makeEnv(), { "X-Bridge-Token": "token-inventado" });
    expect(r.status).toBe(401);
  });

  test("sesión del panel (Bearer) → 200 con la lista de herramientas", async () => {
    const token = await createSessionToken("seba", "sebastian", SECRET);
    const r = await callMcp(makeEnv(), { Authorization: `Bearer ${token}` });
    expect(r.status).toBe(200);
    expect(r.json.service).toBe("avc-panel-mcp");
    expect(r.json.auth).toBe("session");
    expect(r.json.tools).toContain("retry_job");
    expect(r.json.tools).toContain("list_jobs");
  });

  test("BRIDGE_TOKEN (Bearer) → 200", async () => {
    const r = await callMcp(makeEnv(), { Authorization: `Bearer ${BRIDGE}` });
    expect(r.status).toBe(200);
    expect(r.json.auth).toBe("bridge");
  });

  test("BRIDGE_TOKEN (X-Bridge-Token) → 200", async () => {
    const r = await callMcp(makeEnv(), { "X-Bridge-Token": BRIDGE });
    expect(r.status).toBe(200);
    expect(r.json.auth).toBe("bridge");
  });
});
