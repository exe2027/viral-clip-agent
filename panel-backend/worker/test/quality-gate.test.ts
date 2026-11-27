// Tests de la puerta de calidad B4 (2026-11-20): ningún job nace
// para un clip sin validar o defectuoso. Sin red, sin D1 real: la
// lógica pura se prueba directa y el auto-enqueue con un D1 falso
// en memoria (solo las consultas que usa ese camino).
import { describe, expect, test } from "bun:test";
import type { D1Database, Env } from "../src/cloudflare";
import {
  isRestDayEffective,
  mergeViralJson,
  parseViralInput,
  type ClipRow,
  type PanelPlatform,
} from "../src/db";
import {
  autoEnqueueTopClips,
  idempotencyKey,
  linkedPlatforms,
  maybeAlertQualityDiscards,
  publishGate,
  resetAutoEnqueueStateForTests,
  resetQualityAlertStateForTests,
  tryEnqueueClipPublish,
  type GateContext,
} from "../src/jobs";
import {
  QUALITY_ALERT_MIN_SAMPLE,
  QUALITY_DISCARD_ALERT_PCT,
  qualityGateForClip,
  qualityGateUnmeasured,
} from "../src/quality-gate";
import { resetQuotaBreakerState } from "../src/retry";

const TENANT = "sebastian";
/** Mediodía en Santiago (12:30 -03). Se parchea Date.now en los tests de
 *  auto-enqueue; el resto usa la hora real. */
const SLOT_MS = Date.UTC(2026, 10, 20, 15, 30, 0);

function approvedGate(): string {
  return JSON.stringify({
    platforms: { youtube_shorts: { score: 95 } },
    quality_gate: {
      decision: "aprobado",
      pasa: true,
      fallos: [],
      motivo: "",
    },
  });
}

function rejectedGate(): string {
  return JSON.stringify({
    platforms: { youtube_shorts: { score: 95 } },
    quality_gate: {
      decision: "rechazado",
      pasa: false,
      fallos: ["webcam", "formato_9_16"],
      fallos_duros: ["webcam", "formato_9_16"],
      motivo: "calidad: webcam presente (sin cara verificada), formato 9:16 (el clip no es 1080x1920)",
    },
  });
}

function clip(over: Partial<ClipRow> = {}): ClipRow {
  return {
    id: 1, tenant_id: TENANT, source_id: null, influencer_id: null, title: "t", start_sec: 0, end_sec: 40,
    score: 80, status: "aprobado", subtitle_style: "tiktok", seo_title: "", seo_description: "",
    seo_tags: "", platforms: '["youtube_shorts"]', scheduled_for: null, published_at: null,
    file_deleted: 0, created_at: Date.now(), updated_at: Date.now(), captions: "{}",
    viral: approvedGate(), seo_overrides: "[]",
    preview_seen: 0, archived: 0, origin: "vod", publish_states: "{}", game: "GTA VI",
    publish_blocked: 0, needs_human_transcription: 0, subtitles_complete: 1,
    video_r2_key: "", language: "es", ...over,
  } as ClipRow;
}

function gateCtx(over: Partial<GateContext> = {}): GateContext {
  return {
    clip: clip(), seoScore: 95, seoOverride: false, publishedToday: 0,
    lastPublishedAtMs: null, streamerAllowed: true, settings: {}, ...over,
  };
}

// ---------------------------------------------------------------------------
// D1 falso en memoria (solo el camino auto-enqueue + alerta).
// ---------------------------------------------------------------------------
type Row = Record<string, unknown>;

function splitTop(s: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let quote = "";
  let cur = "";
  for (const ch of s) {
    if (quote) {
      cur += ch;
      if (ch === quote) quote = "";
      continue;
    }
    if (ch === "'" || ch === '"') quote = ch;
    if (ch === "(") depth++;
    if (ch === ")") depth--;
    if (ch === "," && depth === 0) {
      out.push(cur.trim());
      cur = "";
      continue;
    }
    cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

function literal(v: string): unknown {
  if (v === "?") return undefined;
  if (/^NULL$/i.test(v)) return null;
  const str = /^'(.*)'$/s.exec(v);
  if (str) return str[1];
  if (/^-?\d+$/.test(v)) return Number(v);
  if (/^-?\d+\.\d+$/.test(v)) return Number(v);
  return v;
}

class FakeD1 {
  tables: Record<string, Row[]> = {};

  seed(table: string, row: Row): void {
    (this.tables[table] ??= []).push({ ...row });
  }

  rows(table: string): Row[] {
    return this.tables[table] ?? [];
  }

  prepare(sql: string): {
    bind: (...values: unknown[]) => {
      first: <T>() => Promise<T | null>;
      all: <T>() => Promise<{ results: T[]; success: boolean; meta: Record<string, unknown> }>;
      run: () => Promise<{ success: boolean; meta: { changes?: number; last_row_id?: number } }>;
    };
  } {
    const db = this;
    return {
      bind(...values: unknown[]) {
        return {
          async first<T>(): Promise<T | null> {
            const rows = db.select(sql, values);
            return (rows[0] as T | undefined) ?? null;
          },
          async all<T>(): Promise<{ results: T[]; success: boolean; meta: Record<string, unknown> }> {
            return { results: db.select(sql, values) as T[], success: true, meta: {} };
          },
          async run(): Promise<{ success: boolean; meta: { changes?: number; last_row_id?: number } }> {
            return db.write(sql, values);
          },
        };
      },
    };
  }

  private whereFn(where: string, params: unknown[]): (r: Row) => boolean {
    const conds = splitTop(where.replace(/\s+AND\s+/gi, "§")).map((c) => c.trim());
    // splitTop no parte por AND dentro de paréntesis; separamos a mano:
    const parts: string[] = [];
    for (const c of conds) parts.push(...c.split("§").map((x) => x.trim()).filter(Boolean));
    let pi = 0;
    const tests: ((r: Row) => boolean)[] = [];
    for (const c of parts) {
      const mEq = /^(\w+)\s*=\s*\?$/.exec(c);
      const mNeq = /^(\w+)\s*<>\s*\?$/.exec(c);
      const mGt = /^(\w+)\s*>\s*\?$/.exec(c);
      const mGte = /^(\w+)\s*>=\s*\?$/.exec(c);
      const mLt = /^(\w+)\s*<\s*\?$/.exec(c);
      const mEqLit = /^(\w+)\s*=\s*'([^']*)'$/.exec(c);
      const mEqNum = /^(\w+)\s*=\s*(-?\d+)$/.exec(c);
      const mNotNull = /^(\w+)\s+IS\s+NOT\s+NULL$/i.exec(c);
      const mIn = /^(\w+)\s+IN\s*\(([^)]+)\)$/i.exec(c);
      const mLike = /^(\w+)\s+LIKE\s+'([^']*)'$/i.exec(c);
      if (mEq) {
        const col = mEq[1] as string;
        const val = params[pi++];
        tests.push((r) => r[col] === val);
      } else if (mNeq) {
        const col = mNeq[1] as string;
        const val = params[pi++];
        tests.push((r) => r[col] !== val);
      } else if (mGt) {
        const col = mGt[1] as string;
        const val = params[pi++] as number;
        tests.push((r) => (r[col] as number) > val);
      } else if (mGte) {
        const col = mGte[1] as string;
        const val = params[pi++] as number;
        tests.push((r) => (r[col] as number) >= val);
      } else if (mLt) {
        const col = mLt[1] as string;
        const val = params[pi++] as number;
        tests.push((r) => (r[col] as number) < val);
      } else if (mEqLit) {
        const col = mEqLit[1] as string;
        const lit = mEqLit[2] as string;
        tests.push((r) => r[col] === lit);
      } else if (mEqNum) {
        const col = mEqNum[1] as string;
        const num = Number(mEqNum[2]);
        tests.push((r) => r[col] === num);
      } else if (mNotNull) {
        const col = mNotNull[1] as string;
        tests.push((r) => r[col] !== null && r[col] !== undefined);
      } else if (mIn) {
        const col = mIn[1] as string;
        const vals = splitTop(mIn[2] as string).map((s) => literal(s));
        tests.push((r) => vals.includes(r[col]));
      } else if (mLike) {
        const col = mLike[1] as string;
        const pattern = mLike[2] as string;
        const parts = pattern.split("%");
        tests.push((r) => {
          const s = r[col];
          if (typeof s !== "string") return false;
          let idx = 0;
          for (let i = 0; i < parts.length; i++) {
            const part = parts[i] as string;
            if (!part) continue;
            const found = s.indexOf(part, idx);
            if (found === -1) return false;
            if (i === 0 && !pattern.startsWith("%") && found !== 0) return false;
            idx = found + part.length;
          }
          if (!pattern.endsWith("%")) {
            const last = parts[parts.length - 1] as string;
            if (last && !s.endsWith(last)) return false;
          }
          return true;
        });
      } else {
        throw new Error(`WHERE no soportado en el mock: ${c}`);
      }
    }
    return (r) => tests.every((t) => t(r));
  }

  select(sql: string, params: unknown[]): Row[] {
    const flat = sql.replace(/\s+/g, " ").trim();
    // DISTINCT clip_id (alerta B4).
    const distinct = /^SELECT DISTINCT (\w+) FROM (\w+)(?: WHERE (.+?))?$/.exec(flat);
    if (distinct) {
      const [, col, table, where] = distinct as [string, string, string, string | undefined];
      let rows = [...(this.tables[table] ?? [])];
      if (where) rows = rows.filter(this.whereFn(where, params));
      const seen = new Set<unknown>();
      const out: Row[] = [];
      for (const r of rows) {
        if (!seen.has(r[col])) {
          seen.add(r[col]);
          out.push({ [col]: r[col] });
        }
      }
      return out;
    }
    const m = /^SELECT (.+?) FROM (\w+)(?: WHERE (.+?))?(?: ORDER BY (.+?))?(?: LIMIT (\d+))?$/.exec(flat);
    if (!m) throw new Error(`SELECT no soportado en el mock: ${sql}`);
    const [, colsRaw, table, where, orderBy, limitRaw] = m as [string, string, string, string | undefined, string | undefined, string | undefined];
    let rows = [...(this.tables[table] ?? [])];
    if (where) rows = rows.filter(this.whereFn(where, params));
    if (orderBy) {
      const desc = /\bDESC\b/i.test(orderBy);
      const col = orderBy.replace(/\b(ASC|DESC)\b/gi, "").trim().split(",")[0]?.trim() as string;
      rows = [...rows].sort((a, b) => {
        const av = a[col] as number | string | null;
        const bv = b[col] as number | string | null;
        if (av === bv) return 0;
        if (av == null) return 1;
        if (bv == null) return -1;
        const cmp = av < bv ? -1 : 1;
        return desc ? -cmp : cmp;
      });
    }
    if (limitRaw) rows = rows.slice(0, Number(limitRaw));
    const cols = colsRaw.trim();
    if (cols === "*") return rows;
    const countMatch = /^COUNT\(\*\) AS (\w+)$/i.exec(cols);
    if (countMatch) return [{ [countMatch[1] as string]: rows.length }];
    const maxMatch = /^MAX\((\w+)\) AS (\w+)$/i.exec(cols);
    if (maxMatch) {
      const vals = rows.map((r) => r[maxMatch[1] as string]).filter((v) => typeof v === "number") as number[];
      return [{ [maxMatch[2] as string]: vals.length ? Math.max(...vals) : null }];
    }
    const wanted = splitTop(cols);
    return rows.map((r) => {
      const out: Row = {};
      for (const c of wanted) out[c] = r[c];
      return out;
    });
  }

  write(sql: string, params: unknown[]): { success: boolean; meta: { changes?: number; last_row_id?: number } } {
    const flat = sql.replace(/\s+/g, " ").trim();
    const insert = /^INSERT INTO (\w+) \(([^)]+)\) VALUES \(([^)]+)\)(?: ON CONFLICT.*)?$/.exec(flat);
    if (insert) {
      const [, table, colsRaw, valsRaw] = insert as [string, string, string];
      const cols = splitTop(colsRaw);
      const vals = splitTop(valsRaw);
      const rows = (this.tables[table] ??= []);
      const id = rows.reduce((max, r) => Math.max(max, Number(r.id ?? 0)), 0) + 1;
      const row: Row = { id };
      let pi = 0;
      cols.forEach((c, i) => {
        const v = vals[i] as string;
        row[c] = v === "?" ? params[pi++] : literal(v);
      });
      rows.push(row);
      return { success: true, meta: { changes: 1, last_row_id: id } };
    }
    const update = /^UPDATE (\w+) SET (.+?) WHERE (.+)$/.exec(flat);
    if (update) {
      const [, table, setRaw, where] = update as [string, string, string];
      const rows = this.tables[table] ?? [];
      const sets = splitTop(setRaw);
      // Los SET consumen parámetros en orden; el WHERE, después.
      const setVals: Array<{ col: string; val: unknown }> = [];
      let pi = 0;
      for (const s of sets) {
        const mQ = /^(\w+)\s*=\s*\?$/.exec(s);
        const mLit = /^(\w+)\s*=\s*'([^']*)'$/.exec(s);
        const mNull = /^(\w+)\s*=\s*NULL$/i.exec(s);
        const mNum = /^(\w+)\s*=\s*(-?\d+)$/.exec(s);
        if (mQ) setVals.push({ col: mQ[1] as string, val: params[pi++] });
        else if (mLit) setVals.push({ col: mLit[1] as string, val: mLit[2] });
        else if (mNull) setVals.push({ col: mNull[1] as string, val: null });
        else if (mNum) setVals.push({ col: mNum[1] as string, val: Number(mNum[2]) });
        else throw new Error(`SET no soportado en el mock: ${s}`);
      }
      const rest = params.slice(pi);
      const pred = this.whereFn(where, rest);
      let changes = 0;
      for (const r of rows) {
        if (!pred(r)) continue;
        for (const { col, val } of setVals) r[col] = val;
        changes++;
      }
      return { success: true, meta: { changes } };
    }
    throw new Error(`Escritura no soportada en el mock: ${sql}`);
  }
}

function makeEnv(db: FakeD1): Env {
  return { DB: db as unknown as D1Database } as Env;
}

/** Cuenta de YouTube ES verificada, en un día que NO es su día
 *  libre (se busca el id cuyo descanso no cae hoy). Sin cuenta
 *  lista, el gate frenaría por "sin cuenta" antes que por calidad. */
function seedAccount(db: FakeD1, nowMs: number): number {
  for (let id = 1; id <= 7; id++) {
    if (!isRestDayEffective(`acc-${id}`, nowMs, {}, id)) {
      db.seed("api_accounts", {
        id, tenant_id: TENANT, platform: "youtube", language: "es",
        label: "YT ES", verify_ok: 1, credential_enc: "v1:falsa",
        credential: "", maturity: "nueva", maturity_checked_day: null,
        first_published_at: null, published_count: 0, strikes: 0,
        created_at: nowMs,
      });
      return id;
    }
  }
  throw new Error("sin id de cuenta fuera de su día libre");
}

function seedSettings(db: FakeD1): void {
  db.seed("settings", {
    tenant_id: TENANT,
    data: JSON.stringify({ auto_publish_top: true }),
    updated_at: Date.now(),
  });
}

function seedClip(db: FakeD1, c: ClipRow): void {
  db.seed("clips", { ...c });
}

/** Parchea Date.now al mediodía; devuelve restaurar. */
function patchNow(ms: number): () => void {
  const real = Date.now;
  Date.now = () => ms;
  return () => {
    Date.now = real;
  };
}

describe("qualityGateForClip (puro)", () => {
  test("clip validado (aprobado) → pasa", () => {
    expect(qualityGateForClip(clip())).toEqual({ reason: null, hard: false });
  });
  test("clip validado (observado, pasa true) → pasa", () => {
    const c = clip({
      viral: JSON.stringify({
        quality_gate: { decision: "observado", pasa: true, fallos: ["hook"], motivo: "observación" },
      }),
    });
    expect(qualityGateForClip(c)).toEqual({ reason: null, hard: false });
  });
  test("clip sin validar (sin veredicto, preview_seen 0) → frena, blando", () => {
    const c = clip({ viral: '{"platforms":{"youtube_shorts":{"score":95}}}', preview_seen: 0 });
    const q = qualityGateForClip(c);
    expect(q.reason).toMatch(/calidad: sin veredicto/);
    expect(q.hard).toBe(false);
  });
  test("clip legacy con preview_seen=1 → pasa (evidencia de validación)", () => {
    const c = clip({ viral: '{"platforms":{"youtube_shorts":{"score":95}}}', preview_seen: 1 });
    expect(qualityGateForClip(c)).toEqual({ reason: null, hard: false });
  });
  test("clip defectuoso (rechazado) → frena, DURO", () => {
    const c = clip({ viral: rejectedGate() });
    const q = qualityGateForClip(c);
    expect(q.reason).toMatch(/calidad:/);
    expect(q.reason).toMatch(/webcam/);
    expect(q.hard).toBe(true);
  });
  test("pendiente (sin Gemini) → frena, blando", () => {
    const c = clip({
      viral: JSON.stringify({
        quality_gate: { decision: "pendiente_revision", pasa: false, fallos: [], motivo: "sin Gemini" },
      }),
    });
    const q = qualityGateForClip(c);
    expect(q.reason).toMatch(/calidad: sin veredicto válido/);
    expect(q.hard).toBe(false);
  });
  test("P0: pendiente sin hard_pasa (veredicto viejo) → frena con el motivo del juez, no el genérico", () => {
    const c = clip({
      viral: JSON.stringify({
        quality_gate: { decision: "pendiente_revision", pasa: false, fallos: [], motivo: "Gemini rechazó la API key" },
      }),
    });
    const q = qualityGateForClip(c);
    expect(q.reason).toMatch(/calidad: sin veredicto válido \(juez no disponible: Gemini rechazó la API key — veredicto no medido\)/);
    expect(q.hard).toBe(false);
    expect(q.unmeasured).toBeUndefined();
    expect(qualityGateUnmeasured(c)).toBeNull();
  });
  test("P0: pendiente + hard_pasa true → PASA marcado como no medido (fallo abierto del juez)", () => {
    const c = clip({
      viral: JSON.stringify({
        quality_gate: { decision: "pendiente_revision", pasa: true, hard_pasa: true, fallos: [], motivo: "Gemini no responde: 503" },
      }),
    });
    const q = qualityGateForClip(c);
    expect(q.reason).toBeNull();
    expect(q.hard).toBe(false);
    expect(q.unmeasured).toBe(true);
    expect(q.unmeasuredReason).toBe("Gemini no responde: 503");
    expect(qualityGateUnmeasured(c)).toBe("Gemini no responde: 503");
  });
  test("P0: pendiente + hard_pasa false → frena: la medida determinista tampoco pasó", () => {
    const c = clip({
      viral: JSON.stringify({
        quality_gate: { decision: "pendiente_revision", pasa: false, hard_pasa: false, fallos: ["webcam"], motivo: "Gemini no responde: 503" },
      }),
    });
    const q = qualityGateForClip(c);
    expect(q.reason).toMatch(/calidad: sin veredicto válido \(juez no disponible: Gemini no responde: 503 — la medida determinista tampoco pasó\)/);
    expect(q.hard).toBe(false);
    expect(qualityGateUnmeasured(c)).toBeNull();
  });
  test("P0: parseViralInput conserva hard_pasa del veredicto", () => {
    const r = parseViralInput({ viral: { quality_gate: { decision: "pendiente_revision", pasa: false, hard_pasa: true, motivo: "x" } } });
    expect(typeof r).not.toBe("string");
    expect((r as { quality_gate?: { hard_pasa?: boolean } }).quality_gate?.hard_pasa).toBe(true);
    const bad = parseViralInput({ viral: { quality_gate: { decision: "pendiente_revision", pasa: false, hard_pasa: "sí" } } });
    expect(typeof bad).toBe("string");
    expect(bad as string).toMatch(/hard_pasa/);
  });
  test("veredicto ilegible → frena, blando (nunca aprobado)", () => {
    const c = clip({ viral: '{"quality_gate": {"decision": "invento", "pasa": true}}' });
    const q = qualityGateForClip(c);
    expect(q.reason).toMatch(/calidad: veredicto de calidad ilegible/);
    expect(q.hard).toBe(false);
  });
});

describe("viral.quality_gate (db.ts: ida y vuelta)", () => {
  test("parseViralInput acepta quality_gate anidado y suelto", () => {
    const qg = { decision: "aprobado", pasa: true, fallos: [], motivo: "" };
    const nested = parseViralInput({ viral: { quality_gate: qg } });
    expect(typeof nested).not.toBe("string");
    expect((nested as { quality_gate?: unknown }).quality_gate).toEqual(qg);
    const loose = parseViralInput({ quality_gate: qg });
    expect(typeof loose).not.toBe("string");
    expect((loose as { quality_gate?: unknown }).quality_gate).toEqual(qg);
  });
  test("parseViralInput rechaza quality_gate sin pasa", () => {
    const r = parseViralInput({ viral: { quality_gate: { decision: "aprobado" } } });
    expect(typeof r).toBe("string");
    expect(r as string).toMatch(/pasa/);
  });
  test("mergeViralJson guarda el veredicto y lo reemplaza en el re-render", () => {
    const first = mergeViralJson("{}", { quality_gate: { decision: "rechazado", pasa: false, fallos: ["webcam"], motivo: "sin cara" } });
    expect(JSON.parse(first).quality_gate.decision).toBe("rechazado");
    const second = mergeViralJson(first, { quality_gate: { decision: "aprobado", pasa: true, fallos: [], motivo: "" } });
    const parsed = JSON.parse(second);
    expect(parsed.quality_gate.decision).toBe("aprobado");
    expect(parsed.quality_gate.fallos).toEqual([]);
  });
});

describe("viral.plantilla (spec rubius sin webcam, db.ts)", () => {
  test("parseViralInput acepta los 4 nombres conocidos", () => {
    for (const name of ["rdjavi", "willyrex", "rubius", "rubius_sin_webcam"]) {
      const r = parseViralInput({ viral: { plantilla: name } });
      expect(typeof r).not.toBe("string");
      expect((r as { plantilla?: string }).plantilla).toBe(name);
    }
  });
  test("parseViralInput rechaza un nombre desconocido y un no-string", () => {
    const r = parseViralInput({ viral: { plantilla: "clakea" } });
    expect(typeof r).toBe("string");
    expect(r as string).toMatch(/plantilla/);
    const r2 = parseViralInput({ viral: { plantilla: 7 } });
    expect(typeof r2).toBe("string");
    expect(r2 as string).toMatch(/plantilla/);
  });
  test("mergeViralJson conserva base.plantilla sin mención y la reemplaza con mención", () => {
    const first = mergeViralJson("{}", { plantilla: "rubius" });
    expect(JSON.parse(first).plantilla).toBe("rubius");
    const kept = mergeViralJson(first, { viral_score: 60 });
    expect(JSON.parse(kept).plantilla).toBe("rubius");
    const replaced = mergeViralJson(first, { plantilla: "rubius_sin_webcam" });
    expect(JSON.parse(replaced).plantilla).toBe("rubius_sin_webcam");
  });
  test("POST → GET: mergeViralJson desde vacío guarda la plantilla (round-trip del JSON)", () => {
    const parsed = parseViralInput({ viral: { plantilla: "rubius_sin_webcam", viral_score: 60 } });
    expect(typeof parsed).not.toBe("string");
    const stored = mergeViralJson("{}", parsed as never);
    const back = JSON.parse(stored);
    expect(back.plantilla).toBe("rubius_sin_webcam");
    expect(back.viral_score).toBe(60);
  });
});

describe("publishGate + calidad B4", () => {
  test("clip sin validar → frena en automático", () => {
    const c = clip({ viral: '{"platforms":{"youtube_shorts":{"score":95}}}', preview_seen: 0 });
    expect(publishGate("youtube_shorts", gateCtx({ clip: c }))).toMatch(/calidad: sin veredicto/);
  });
  test("clip sin validar → FRENA TAMBIÉN a mano (refuerzo del validador, 2026-11-21: sin veredicto no publica por ningún camino)", () => {
    const c = clip({ viral: '{"platforms":{"youtube_shorts":{"score":95}}}', preview_seen: 0 });
    expect(publishGate("youtube_shorts", gateCtx({ clip: c, manual: true }))).toMatch(/calidad: sin veredicto/);
  });
  test("clip defectuoso → frena TAMBIÉN a mano", () => {
    const c = clip({ viral: rejectedGate() });
    expect(publishGate("youtube_shorts", gateCtx({ clip: c }))).toMatch(/calidad:/);
    expect(publishGate("youtube_shorts", gateCtx({ clip: c, manual: true }))).toMatch(/calidad:/);
  });
  test("clip validado → pasa (auto y manual)", () => {
    expect(publishGate("youtube_shorts", gateCtx())).toBeNull();
    expect(publishGate("youtube_shorts", gateCtx({ manual: true }))).toBeNull();
  });
});

describe("tryEnqueueClipPublish + auto-enqueue B4 (D1 falso)", () => {
  test("clip sin validar → 0 jobs en automático", async () => {
    const db = new FakeD1();
    seedSettings(db);
    seedAccount(db, Date.now());
    seedClip(db, clip({ id: 10, viral: '{"platforms":{"youtube_shorts":{"score":95}}}', preview_seen: 0 }));
    const restore = patchNow(SLOT_MS);
    try {
      const r = await tryEnqueueClipPublish(makeEnv(db), TENANT, db.rows("clips")[0] as unknown as ClipRow, { actor: "worker", logToWorkerEvents: true });
      expect(r.jobs).toEqual([]);
      expect(r.blocked.join(" ")).toMatch(/calidad: sin veredicto/);
      expect(db.rows("jobs")).toEqual([]);
    } finally {
      restore();
    }
  });

  test("clip defectuoso → 0 jobs también a mano", async () => {
    const db = new FakeD1();
    seedSettings(db);
    seedAccount(db, Date.now());
    seedClip(db, clip({ id: 11, viral: rejectedGate() }));
    const r = await tryEnqueueClipPublish(makeEnv(db), TENANT, db.rows("clips")[0] as unknown as ClipRow, { manual: true });
    expect(r.jobs).toEqual([]);
    expect(r.blocked.join(" ")).toMatch(/calidad:/);
    expect(db.rows("jobs")).toEqual([]);
  });

  test("clip sin validar a mano → 0 jobs (refuerzo del validador: sin veredicto no publica ni a mano)", async () => {
    const db = new FakeD1();
    seedSettings(db);
    seedAccount(db, Date.now());
    seedClip(db, clip({ id: 12, viral: '{"platforms":{"youtube_shorts":{"score":95}}}', preview_seen: 0 }));
    const r = await tryEnqueueClipPublish(makeEnv(db), TENANT, db.rows("clips")[0] as unknown as ClipRow, { manual: true });
    expect(r.jobs).toEqual([]);
    expect(r.blocked.join(" ")).toMatch(/calidad: sin veredicto/);
    expect(db.rows("jobs")).toEqual([]);
  });

  test("manual fuera de la vieja franja → encola (sin franjas desde 2026-11-26); con última publicación hace 30 min → frena por el piso de 90 min", async () => {
    // SPEC quitar-franjas v2: forceNow murió con las franjas (ya no
    // hay puerta de franja que saltar). La manual a cualquier hora
    // encola; solo el piso de 90 min entre publicaciones frena.
    const FUERA_MS = Date.UTC(2026, 10, 20, 18, 30, 0); // 15:30 Santiago, fuera de la vieja franja
    const db = new FakeD1();
    seedSettings(db);
    seedAccount(db, FUERA_MS);
    seedClip(db, clip({ id: 13, viral: approvedGate() }));
    const restore = patchNow(FUERA_MS);
    try {
      const r = await tryEnqueueClipPublish(makeEnv(db), TENANT, db.rows("clips")[0] as unknown as ClipRow, { manual: true });
      expect(r.jobs.length).toBeGreaterThan(0);
      expect(r.blocked).toEqual([]);
    } finally {
      restore();
    }
    // Con una subida posted hace 30 min en YT ES, la misma manual
    // frena por el piso de 90 min.
    const db2 = new FakeD1();
    seedSettings(db2);
    const accId = seedAccount(db2, FUERA_MS);
    seedClip(db2, clip({ id: 13, viral: approvedGate() }));
    db2.seed("jobs", {
      id: 90, tenant_id: TENANT, kind: "publish", clip_id: 99, platform: "youtube_shorts",
      api_account_id: accId, state: "posted", platform_id: "YT9", evidence_url: "https://example.com/yt9",
      language: "es", posted_day: "2026-11-20", attempts: 0, last_error: null, retryable: 1,
      next_run_at: null, created_at: FUERA_MS - 30 * 60_000, updated_at: FUERA_MS - 30 * 60_000,
    });
    const restore2 = patchNow(FUERA_MS);
    try {
      const r2 = await tryEnqueueClipPublish(makeEnv(db2), TENANT, db2.rows("clips")[0] as unknown as ClipRow, { manual: true });
      expect(r2.jobs).toEqual([]);
      expect(r2.blocked.join(" ")).toMatch(/menos de 90 min/);
    } finally {
      restore2();
    }
  });

  test("auto-enqueue: validado → job; sin validar y defectuoso → 0 jobs", async () => {
    const db = new FakeD1();
    seedSettings(db);
    seedAccount(db, SLOT_MS);
    resetAutoEnqueueStateForTests();
    resetQuotaBreakerState();
    resetQualityAlertStateForTests();
    seedClip(db, clip({ id: 20, viral: approvedGate() }));
    seedClip(db, clip({ id: 21, viral: '{"platforms":{"youtube_shorts":{"score":95}}}', preview_seen: 0 }));
    seedClip(db, clip({ id: 22, viral: rejectedGate() }));
    const restore = patchNow(SLOT_MS);
    try {
      const r = await autoEnqueueTopClips(makeEnv(db), TENANT);
      expect(r.enabled).toBe(true);
      expect(r.enqueued).toBe(1);
      // B4.1 (spec 2026-11-27): el rechazado (clip 22) ya NO
      // cuenta como bloqueado por calidad — se salta antes de
      // evaluar, sin evento ni intento A4. Solo el sin veredicto
      // (clip 21) frena por calidad en el gate.
      expect((r.qualityBlocked ?? 0)).toBeGreaterThanOrEqual(1);
      const jobClipIds = db.rows("jobs").map((j) => j.clip_id);
      expect(jobClipIds).toEqual([20]);
    } finally {
      restore();
    }
  });
});

describe("alerta del 30% de descartes (B4)", () => {
  function seedDay(db: FakeD1, blockedIds: number[], jobClipIds: number[]): void {
    const now = Date.now();
    for (const id of blockedIds) {
      db.seed("clip_events", {
        clip_id: id, tenant_id: TENANT, at: now, step: "publicado",
        actor: "worker", message: "Publicación frenada en youtube_shorts: calidad: sin veredicto de calidad (clip anterior a la puerta B4).", failed: 1,
      });
    }
    for (const id of jobClipIds) {
      db.seed("jobs", {
        clip_id: id, tenant_id: TENANT, kind: "publish", platform: "youtube_shorts",
        state: "scheduled", created_at: now, updated_at: now,
      });
    }
  }

  test("4/8 descartados (50%) → avisa una vez; la segunda es 'already'", async () => {
    const db = new FakeD1();
    resetQualityAlertStateForTests();
    seedDay(db, [1, 2, 3, 4], [5, 6, 7, 8]);
    const first = await maybeAlertQualityDiscards(db as unknown as D1Database, TENANT, makeEnv(db));
    expect(first).toBe("sent");
    expect(db.rows("notifications")).toHaveLength(1);
    expect(String(db.rows("notifications")[0]?.message)).toMatch(/4\/8/);
    expect(String(db.rows("notifications")[0]?.message)).toMatch(/50%/);
    expect(String(db.rows("notifications")[0]?.message)).toMatch(new RegExp(`${QUALITY_DISCARD_ALERT_PCT}%`));
    const second = await maybeAlertQualityDiscards(db as unknown as D1Database, TENANT, makeEnv(db));
    expect(second).toBe("already");
    expect(db.rows("notifications")).toHaveLength(1);
  });

  test("1/6 descartados (17%) → no avisa", async () => {
    const db = new FakeD1();
    resetQualityAlertStateForTests();
    seedDay(db, [1], [2, 3, 4, 5, 6]);
    expect(await maybeAlertQualityDiscards(db as unknown as D1Database, TENANT, makeEnv(db))).toBe("not_due");
    expect(db.rows("notifications")).toEqual([]);
  });

  test("2/3 descartados (67%) pero muestra < 5 → no avisa", async () => {
    const db = new FakeD1();
    resetQualityAlertStateForTests();
    seedDay(db, [1, 2], [3]);
    expect(QUALITY_ALERT_MIN_SAMPLE).toBe(5);
    expect(await maybeAlertQualityDiscards(db as unknown as D1Database, TENANT, makeEnv(db))).toBe("not_due");
    expect(db.rows("notifications")).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Reglas 14:10 y 14:17 (2026-11-21).
// ---------------------------------------------------------------------------
describe("reglas 14:10 (todas las cuentas vinculadas) y 14:17 (programación en el panel)", () => {
  // Hora programada 18:30 de Santiago del 20-nov-2026 (21:30 UTC).
  const SLOT_18H_MS = Date.UTC(2026, 10, 20, 21, 30, 0);
  // 15:30 Santiago (18:30 UTC): antes fuera de franja, hoy hora válida.
  const NO_SLOT_MS = Date.UTC(2026, 10, 20, 18, 30, 0);

  // Cuentas ES vinculadas. Ids 1, 3 y 4: el id 2 descansa el
  // viernes 2026-11-20 (isRestDayEffective) y el gate frenaría.
  function seedLinkedAccount(db: FakeD1, id: number, platform: string, language: string, over: Row = {}): void {
    db.seed("api_accounts", {
      id, tenant_id: TENANT, platform, language,
      label: `${platform} ${language}`, verify_ok: 1, credential_enc: "v1:falsa",
      credential: "", maturity: "nueva", maturity_checked_day: null,
      first_published_at: null, published_count: 0, strikes: 0,
      created_at: SLOT_MS, ...over,
    });
  }

  function seedAllEs(db: FakeD1): void {
    seedLinkedAccount(db, 1, "youtube", "es");
    seedLinkedAccount(db, 3, "tiktok", "es");
    seedLinkedAccount(db, 4, "meta", "es");
  }

  test("linkedPlatforms: 3 cuentas ES vinculadas → las 4 plataformas ES", async () => {
    const db = new FakeD1();
    seedAllEs(db);
    expect(await linkedPlatforms(db as unknown as D1Database, TENANT, "es")).toEqual(
      ["youtube_shorts", "tiktok", "instagram_reels", "facebook_reels"],
    );
  });

  test("linkedPlatforms: cuenta sin verificar o sin credencial no cuenta", async () => {
    const db = new FakeD1();
    seedLinkedAccount(db, 1, "youtube", "es", { verify_ok: 0 });
    seedLinkedAccount(db, 3, "tiktok", "es", { credential_enc: "", credential: "" });
    expect(await linkedPlatforms(db as unknown as D1Database, TENANT, "es")).toEqual([]);
  });

  test("linkedPlatforms: EN no mezcla con ES", async () => {
    const db = new FakeD1();
    seedLinkedAccount(db, 1, "youtube", "en");
    seedLinkedAccount(db, 3, "tiktok", "en");
    expect(await linkedPlatforms(db as unknown as D1Database, TENANT, "es")).toEqual([]);
    expect(await linkedPlatforms(db as unknown as D1Database, TENANT, "en")).toEqual(["youtube_shorts", "tiktok"]);
  });

  test("unión 14:10: clip con solo youtube_shorts pero 4 cuentas vinculadas → 4 jobs", async () => {
    const db = new FakeD1();
    seedSettings(db);
    seedAllEs(db);
    const c = clip({ platforms: '["youtube_shorts"]' });
    seedClip(db, c);
    const restore = patchNow(SLOT_MS);
    try {
      const { jobs, blocked } = await tryEnqueueClipPublish(makeEnv(db), TENANT, c, { actor: "t", manual: true });
      expect(jobs.map((j) => j.platform).sort()).toEqual(
        ["facebook_reels", "instagram_reels", "tiktok", "youtube_shorts"],
      );
      expect(blocked).toEqual([]);
    } finally { restore(); }
  });

  test("unión 14:10: plataforma del clip sin cuenta vinculada → frena con motivo, las demás salen", async () => {
    const db = new FakeD1();
    seedSettings(db);
    seedLinkedAccount(db, 1, "youtube", "es");
    const c = clip({ platforms: '["youtube_shorts","tiktok"]' });
    seedClip(db, c);
    const restore = patchNow(SLOT_MS);
    try {
      const { jobs, blocked } = await tryEnqueueClipPublish(makeEnv(db), TENANT, c, { actor: "t", manual: true });
      expect(jobs.map((j) => j.platform)).toEqual(["youtube_shorts"]);
      expect(blocked.join(" ")).toMatch(/sin cuenta TikTok en español/);
    } finally { restore(); }
  });

  test("override 14:10: platforms explícito limita a esa lista aunque haya más cuentas", async () => {
    const db = new FakeD1();
    seedSettings(db);
    seedAllEs(db);
    const c = clip({ platforms: '["youtube_shorts"]' });
    seedClip(db, c);
    const restore = patchNow(SLOT_MS);
    try {
      const { jobs } = await tryEnqueueClipPublish(makeEnv(db), TENANT, c, {
        actor: "t", manual: true, platforms: ["youtube_shorts"] as PanelPlatform[],
      });
      expect(jobs.map((j) => j.platform)).toEqual(["youtube_shorts"]);
    } finally { restore(); }
  });

  test("programación 14:17: scheduled_for en hora futura → jobs con ese next_run_at", async () => {
    const db = new FakeD1();
    seedSettings(db);
    seedAllEs(db);
    const c = clip({ platforms: '["youtube_shorts"]' });
    seedClip(db, c);
    const restore = patchNow(SLOT_MS); // 12:30 Santiago
    try {
      const { jobs, blocked } = await tryEnqueueClipPublish(makeEnv(db), TENANT, c, {
        actor: "t", manual: true, platforms: ["youtube_shorts"] as PanelPlatform[], scheduledForMs: SLOT_18H_MS,
      });
      expect(blocked).toEqual([]);
      expect(jobs).toHaveLength(1);
      expect(jobs[0]?.next_run_at).toBe(SLOT_18H_MS);
      expect(jobs[0]?.state).toBe("scheduled");
    } finally { restore(); }
  });

  test("programación 14:17: scheduled_for fuera de la vieja franja → agenda (sin franjas desde 2026-11-26)", async () => {
    const db = new FakeD1();
    seedSettings(db);
    seedAllEs(db);
    const c = clip({ platforms: '["youtube_shorts"]' });
    seedClip(db, c);
    const restore = patchNow(SLOT_MS);
    try {
      const { jobs, blocked } = await tryEnqueueClipPublish(makeEnv(db), TENANT, c, {
        actor: "t", manual: true, platforms: ["youtube_shorts"] as PanelPlatform[], scheduledForMs: NO_SLOT_MS,
      });
      expect(blocked).toEqual([]);
      expect(jobs).toHaveLength(1);
      expect(jobs[0]?.next_run_at).toBe(NO_SLOT_MS);
      expect(jobs[0]?.state).toBe("scheduled");
    } finally { restore(); }
  });

  test("programación 14:17: misma hora que otro job programado → se adelanta 90 min (piso entre publicaciones, sin 1-por-franja)", async () => {
    const db = new FakeD1();
    seedSettings(db);
    seedAllEs(db);
    const c = clip({ platforms: '["youtube_shorts"]' });
    seedClip(db, c);
    // Otro clip ya programado a las 18:30 para YT ES.
    db.seed("jobs", {
      id: 1, clip_id: 99, tenant_id: TENANT, kind: "publish", platform: "youtube_shorts",
      api_account_id: 1, language: "es", state: "scheduled", next_run_at: SLOT_18H_MS,
      created_at: SLOT_MS, updated_at: SLOT_MS, caption: "", r2_key: "", day: "2026-11-20",
    });
    const restore = patchNow(SLOT_MS);
    try {
      const { jobs, blocked } = await tryEnqueueClipPublish(makeEnv(db), TENANT, c, {
        actor: "t", manual: true, platforms: ["youtube_shorts"] as PanelPlatform[], scheduledForMs: SLOT_18H_MS,
      });
      expect(blocked).toEqual([]);
      expect(jobs).toHaveLength(1);
      // 18:30 + 90 min (piso desde el job programado pendiente).
      expect(jobs[0]?.next_run_at).toBe(SLOT_18H_MS + 90 * 60_000);
      expect(jobs[0]?.state).toBe("scheduled");
    } finally { restore(); }
  });

  test("P0 #5 (|repubN): sin republish, un clip ya publicado hoy frena por 'mismo clip hoy'", async () => {
    const db = new FakeD1();
    seedSettings(db);
    seedLinkedAccount(db, 3, "tiktok", "es", {
      first_published_at: SLOT_MS - 40 * 86400_000, published_count: 20,
      maturity: "establecida", maturity_checked_day: "2026-11-20",
    });
    const c = clip({ platforms: '["tiktok"]' });
    seedClip(db, c);
    const baseKey = await idempotencyKey(1, "tiktok", 3, "2026-11-20");
    db.seed("jobs", {
      id: 1, clip_id: 1, tenant_id: TENANT, kind: "publish", platform: "tiktok",
      api_account_id: 3, language: "es", state: "posted", idempotency_key: baseKey,
      posted_day: "2026-11-20", created_at: SLOT_MS, updated_at: SLOT_MS,
      caption: "", r2_key: "",
    });
    const restore = patchNow(SLOT_18H_MS); // 18:30, 6 h después (piso 90 min OK)
    try {
      const { jobs, blocked } = await tryEnqueueClipPublish(makeEnv(db), TENANT, c, {
        actor: "t", manual: true, platforms: ["tiktok"] as PanelPlatform[],
      });
      expect(jobs).toEqual([]);
      expect(blocked.join(" ")).toMatch(/ya se publicó hoy en tiktok/);
    } finally { restore(); }
  });

  test("P0 #5 (|repubN): con republish=true, el job nuevo lleva la key |repub1 (no choca con la UNIQUE)", async () => {
    const db = new FakeD1();
    seedSettings(db);
    seedLinkedAccount(db, 3, "tiktok", "es", {
      first_published_at: SLOT_MS - 40 * 86400_000, published_count: 20,
      maturity: "establecida", maturity_checked_day: "2026-11-20",
    });
    const c = clip({ platforms: '["tiktok"]' });
    seedClip(db, c);
    const baseKey = await idempotencyKey(1, "tiktok", 3, "2026-11-20");
    db.seed("jobs", {
      id: 1, clip_id: 1, tenant_id: TENANT, kind: "publish", platform: "tiktok",
      api_account_id: 3, language: "es", state: "posted", idempotency_key: baseKey,
      posted_day: "2026-11-20", created_at: SLOT_MS, updated_at: SLOT_MS,
      caption: "", r2_key: "",
    });
    const restore = patchNow(SLOT_18H_MS);
    try {
      const { jobs, blocked } = await tryEnqueueClipPublish(makeEnv(db), TENANT, c, {
        actor: "t", manual: true, platforms: ["tiktok"] as PanelPlatform[], republish: true,
      });
      expect(blocked).toEqual([]);
      expect(jobs).toHaveLength(1);
      expect(jobs[0]?.state).toBe("scheduled");
      expect(jobs[0]?.idempotency_key).not.toBe(baseKey);
      expect(jobs[0]?.idempotency_key).toBe(
        await idempotencyKey(1, "tiktok", 3, "2026-11-20|repub1"),
      );
    } finally { restore(); }
  });

  test("P0 #5 (|repubN): si la key |repub1 ya está ocupada (programada), usa |repub2", async () => {
    const db = new FakeD1();
    seedSettings(db);
    seedLinkedAccount(db, 3, "tiktok", "es", {
      first_published_at: SLOT_MS - 40 * 86400_000, published_count: 20,
      maturity: "establecida", maturity_checked_day: "2026-11-20",
    });
    const c = clip({ platforms: '["tiktok"]' });
    seedClip(db, c);
    const baseKey = await idempotencyKey(1, "tiktok", 3, "2026-11-20");
    const repub1Key = await idempotencyKey(1, "tiktok", 3, "2026-11-20|repub1");
    db.seed("jobs", {
      id: 1, clip_id: 1, tenant_id: TENANT, kind: "publish", platform: "tiktok",
      api_account_id: 3, language: "es", state: "posted", idempotency_key: baseKey,
      posted_day: "2026-11-20", created_at: SLOT_MS, updated_at: SLOT_MS,
      caption: "", r2_key: "",
    });
    // Una republicación ya programada (18:30) con la key |repub1.
    db.seed("jobs", {
      id: 2, clip_id: 1, tenant_id: TENANT, kind: "publish", platform: "tiktok",
      api_account_id: 3, language: "es", state: "scheduled", idempotency_key: repub1Key,
      next_run_at: SLOT_18H_MS, created_at: SLOT_MS, updated_at: SLOT_MS,
      caption: "", r2_key: "",
    });
    // Tercera franja del día (21:30) para respetar el espaciamiento (2 h).
    const restore = patchNow(Date.UTC(2026, 10, 21, 0, 30, 0));
    try {
      const { jobs } = await tryEnqueueClipPublish(makeEnv(db), TENANT, c, {
        actor: "t", manual: true, platforms: ["tiktok"] as PanelPlatform[], republish: true,
      });
      expect(jobs).toHaveLength(1);
      expect(jobs[0]?.idempotency_key).toBe(
        await idempotencyKey(1, "tiktok", 3, "2026-11-20|repub2"),
      );
    } finally { restore(); }
  });
});
