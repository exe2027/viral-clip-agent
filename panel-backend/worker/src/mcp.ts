// Servidor MCP del panel (2026-11-21): expone las operaciones del
// panel como herramientas MCP en /mcp, para que un agente (Muse por
// conector personalizado, el vigilante) maneje la cola por
// herramientas en vez de por navegador. Sin estado (stateless,
// createMcpHandler de Agents SDK / @modelcontextprotocol/server,
// Streamable HTTP). Solo lectura salvo retry_job.
//
// Auth (se comprueba en handleMcpRequest ANTES de llamar al
// handler, igual que /api/bridge/*): BRIDGE_TOKEN (secreto del
// Worker) por Authorization: Bearer o X-Bridge-Token, o sesión
// del panel (cookie o Bearer). Con BRIDGE_TOKEN se ven ambos
// tenants (sebastian y kaez); con sesión, SOLO el tenant de la
// sesión (el panel nunca muestra los datos del otro tenant).
//
// Gate: retry_job re-encola con tryEnqueueClipPublish (el mismo
// camino que el botón "Publicar ahora" del panel, manual=false:
// respeta franja, espaciamiento, topes, CALENTAMIENTO, rolling
// 24 h y mismo-clip-hoy) y corre UN ciclo. Nunca fuerza un job
// frenado por calentamiento/tope/mismo-clip-hoy: si el gate frena,
// la herramienta devuelve el motivo y no publica nada. Un `posted`
// en D1 no es prueba de que el vídeo esté vivo: verify_url lo
// comprueba en vivo.
//
// RESPALDO CUANDO EL PANEL SE TRABA (regla 2026-11-21 14:17: "si el
// panel se traba, el MCP es el respaldo para hacer las cosas y
// publicar"): un agente con la SESIÓN DEL PANEL (no hace falta
// BRIDGE_TOKEN: la sesión vale como auth, solo para su tenant) puede
// 1) list_jobs para ver qué job quedó atascado (state scheduled o
// processing con next_run_at vencido), 2) retry_job { job_id } para
// re-encolarlo (el gate vuelve a evaluar; si frena, devuelve el
// motivo exacto) y correr un ciclo, 3) verify_url { url } para
// comprobar en vivo que el vídeo publicado existe. La programación
// futura (scheduled_for) vive en el panel (POST /api/clips/:id/publish),
// no en crons de Muse: el MCP no programa, solo repara lo atascado.

import { McpServer } from "@modelcontextprotocol/server";
import { createMcpHandler } from "agents/mcp/server";
import { z } from "zod";
import type { Env, ExecutionContext } from "./cloudflare";
import { isTenant, requireTenant, tokenFingerprint, verifySessionToken, type Tenant } from "./auth";
import {
  effectiveSettings,
  getClip,
  getSettings,
  isTokenRevoked,
  listJobs,
  listQueueClips,
  type JobStateFilter,
} from "./db";
import { runWorkerCycle, santiagoDayKey, tryEnqueueClipPublish, type JobRow } from "./jobs";

const MCP_TENANTS = ["sebastian", "kaez", "both"] as const;
type McpTenantParam = (typeof MCP_TENANTS)[number];

type McpAuth =
  | { kind: "bridge" }
  | { kind: "session"; tenant: Tenant };

function bearerToken(req: Request): string {
  return /^Bearer\s+(.+)$/i.exec((req.headers.get("Authorization") ?? "").trim())?.[1] ?? "";
}

/** Auth de /mcp (misma regla que /api/bridge/settings): secreto
 *  compartido BRIDGE_TOKEN (Bearer o X-Bridge-Token) o sesión del
 *  panel válida (cookie o Bearer) y no revocada. Sin auth, null. */
async function authorizeMcp(req: Request, env: Env): Promise<McpAuth | null> {
  const bearer = bearerToken(req);
  const bridge = req.headers.get("X-Bridge-Token") ?? "";
  if (env.BRIDGE_TOKEN && ((bearer && bearer === env.BRIDGE_TOKEN) || (bridge && bridge === env.BRIDGE_TOKEN))) {
    return { kind: "bridge" };
  }
  // Sesión del panel: fail-closed. Si TENANT no está configurado
  // (p. ej. `wrangler dev` sin --var), no hay sesión válida y se
  // devuelve null (401), nunca un 500 por requireTenant.
  let tenant: Tenant;
  try {
    tenant = requireTenant(env);
  } catch {
    return null;
  }
  const cookie = /(?:^|;\s*)panel-session=([^;]+)/.exec(req.headers.get("Cookie") ?? "")?.[1] ?? "";
  for (const raw of [cookie || null, bearer || null]) {
    if (!raw) continue;
    const payload = await verifySessionToken(raw, env.SESSION_SECRET, tenant);
    if (!payload) continue;
    if (await isTokenRevoked(env.DB, await tokenFingerprint(raw)).catch(() => false)) continue;
    return { kind: "session", tenant: payload.tenant };
  }
  return null;
}

/** Tenants visibles para esta auth y el parámetro pedido. Con
 *  sesión, solo el tenant de la sesión (pedir el otro o "both"
 *  es 403); con BRIDGE_TOKEN, los dos. */
function resolveTenants(auth: McpAuth, requested?: McpTenantParam): Tenant[] {
  const want: McpTenantParam = requested ?? "both";
  if (auth.kind === "session") {
    if (want !== "both" && want !== auth.tenant) {
      throw new Error(`MCP: la sesión es de ${auth.tenant} y no puede ver ${want}.`);
    }
    return [auth.tenant];
  }
  return want === "both" ? ["sebastian", "kaez"] : [want];
}

function textResult(value: unknown): { content: { type: "text"; text: string }[] } {
  return { content: [{ type: "text", text: JSON.stringify(value, null, 2) }] };
}

function errorResult(message: string): { content: { type: "text"; text: string }[]; isError: boolean } {
  return { content: [{ type: "text", text: JSON.stringify({ ok: false, error: message }, null, 2) }], isError: true };
}

function trimJob(j: {
  id: number;
  tenant_id: string;
  clip_id: number | null;
  platform: string | null;
  language?: string;
  state: string;
  title: string;
  attempts: number;
  last_error: string | null;
  evidence_url: string | null;
  posted_day?: string | null;
  created_at: number;
  updated_at: number;
}): Record<string, unknown> {
  return {
    id: j.id,
    tenant: j.tenant_id,
    clip_id: j.clip_id,
    platform: j.platform,
    language: j.language ?? null,
    state: j.state,
    title: j.title,
    attempts: j.attempts,
    last_error: j.last_error,
    evidence_url: j.evidence_url,
    posted_day: j.posted_day ?? null,
    created_at: j.created_at,
    updated_at: j.updated_at,
  };
}

function parsePlatforms(raw: string): string[] {
  try {
    const v: unknown = JSON.parse(raw);
    return Array.isArray(v) ? v.map(String) : [];
  } catch {
    return [];
  }
}

function trimClip(c: {
  id: number;
  tenant_id: string;
  influencer_id: number | null;
  streamer?: string | null;
  title: string;
  seo_title: string;
  status: string;
  score: number;
  language: string;
  origin: string;
  platforms: string;
  start_sec: number;
  end_sec: number;
  publish_blocked: number;
  subtitles_complete: number;
  video_r2_key: string;
  thumb_text: string;
  created_at: number;
  updated_at: number;
}): Record<string, unknown> {
  return {
    id: c.id,
    tenant: c.tenant_id,
    influencer_id: c.influencer_id,
    streamer: c.streamer ?? null,
    title: c.title,
    seo_title: c.seo_title,
    status: c.status,
    score: c.score,
    language: c.language,
    origin: c.origin,
    platforms: parsePlatforms(c.platforms),
    start_sec: c.start_sec,
    end_sec: c.end_sec,
    publish_blocked: c.publish_blocked,
    subtitles_complete: c.subtitles_complete,
    has_mp4: c.video_r2_key !== "",
    video_r2_key: c.video_r2_key,
    thumb_text: c.thumb_text,
    created_at: c.created_at,
    updated_at: c.updated_at,
  };
}

async function getJobById(db: Env["DB"], jobId: number): Promise<JobRow | null> {
  return db.prepare("SELECT * FROM jobs WHERE id = ?").bind(jobId).first<JobRow>();
}

const tenantParam = z.enum(["sebastian", "kaez", "both"]).optional();
const stateParam = z
  .enum(["scheduled", "processing", "posted", "failed", "draft", "awaiting_captcha"])
  .optional();

export function createAvcMcpServer(env: Env, auth: McpAuth): McpServer {
  const server = new McpServer({ name: "avc-panel", version: "0.1.1" });

  server.registerTool(
    "list_jobs",
    {
      title: "Jobs de hoy",
      description:
        "Jobs de publicación de HOY (día America/Santiago) en los tenants pedidos. Solo lectura. Sin estado devuelve todos los estados.",
      inputSchema: z.object({
        tenant: tenantParam.describe("sebastian, kaez o both (por defecto both con BRIDGE_TOKEN; con sesión solo su tenant)"),
        state: stateParam.describe("filtrar por estado"),
        limit: z.number().int().min(1).max(100).optional().describe("máx. jobs (por defecto 20)"),
      }),
    },
    async (args) => {
      try {
        const tenants = resolveTenants(auth, args.tenant);
        const limit = args.limit ?? 20;
        const today = santiagoDayKey();
        const out: Record<string, unknown>[] = [];
        for (const t of tenants) {
          const rows = await listJobs(env.DB, t, 100, args.state as JobStateFilter | undefined);
          for (const r of rows) {
            if (santiagoDayKey(new Date(r.created_at)) !== today) continue;
            out.push(trimJob({ ...r, tenant_id: t }));
          }
        }
        out.sort((a, b) => (b["created_at"] as number) - (a["created_at"] as number));
        return textResult({ ok: true, day: today, tenants, jobs: out.slice(0, limit) });
      } catch (e) {
        return errorResult(e instanceof Error ? e.message : String(e));
      }
    },
  );

  server.registerTool(
    "get_job",
    {
      title: "Detalle de un job",
      description: "Un job por id (cualquier tenant visible). Solo lectura. No devuelve resume_data ni idempotency_key.",
      inputSchema: z.object({ job_id: z.number().int().describe("id del job en D1") }),
    },
    async (args) => {
      try {
        const job = await getJobById(env.DB, args.job_id);
        if (!job) return errorResult(`Job ${args.job_id} no existe.`);
        if (auth.kind === "session" && job.tenant_id !== auth.tenant) {
          return errorResult(`MCP: la sesión es de ${auth.tenant} y el job es de ${job.tenant_id}.`);
        }
        return textResult({
          ok: true,
          job: {
            ...trimJob(job),
            kind: job.kind,
            api_account_id: job.api_account_id,
            platform_id: job.platform_id,
            caption: job.caption,
            retryable: job.retryable,
            manual: job.manual ?? 0,
            next_run_at: job.next_run_at,
          },
        });
      } catch (e) {
        return errorResult(e instanceof Error ? e.message : String(e));
      }
    },
  );

  server.registerTool(
    "get_clip",
    {
      title: "Detalle de un clip",
      description: "Un clip por id. Solo lectura.",
      inputSchema: z.object({
        clip_id: z.number().int().describe("id del clip en D1"),
        tenant: z.enum(["sebastian", "kaez"]).optional().describe("tenant; sin él se busca en los visibles"),
      }),
    },
    async (args) => {
      try {
        const tenants = args.tenant ? resolveTenants(auth, args.tenant) : resolveTenants(auth, "both");
        for (const t of tenants) {
          const clip = await getClip(env.DB, args.clip_id, t);
          if (clip) return textResult({ ok: true, clip: trimClip(clip) });
        }
        return errorResult(`Clip ${args.clip_id} no existe en los tenants visibles.`);
      } catch (e) {
        return errorResult(e instanceof Error ? e.message : String(e));
      }
    },
  );

  server.registerTool(
    "list_clips",
    {
      title: "Cola de clips",
      description: "Clips de la Cola (mismo orden que el panel: LIVE primero, luego por score). Solo lectura.",
      inputSchema: z.object({
        tenant: tenantParam.describe("sebastian, kaez o both (por defecto both con BRIDGE_TOKEN; con sesión solo su tenant)"),
        limit: z.number().int().min(1).max(100).optional().describe("máx. clips (por defecto 20)"),
      }),
    },
    async (args) => {
      try {
        const tenants = resolveTenants(auth, args.tenant);
        const limit = args.limit ?? 20;
        const out: Record<string, unknown>[] = [];
        for (const t of tenants) {
          const rows = await listQueueClips(env.DB, t);
          for (const c of rows) out.push(trimClip(c));
        }
        return textResult({ ok: true, tenants, clips: out.slice(0, limit) });
      } catch (e) {
        return errorResult(e instanceof Error ? e.message : String(e));
      }
    },
  );

  server.registerTool(
    "get_settings",
    {
      title: "Ajustes efectivos",
      description: "Ajustes EFECTIVOS de un tenant (defaults incluidos): topes, espaciamiento, franjas, umbrales. Solo lectura.",
      inputSchema: z.object({
        tenant: z.enum(["sebastian", "kaez"]).describe("tenant"),
      }),
    },
    async (args) => {
      try {
        if (auth.kind === "session" && args.tenant !== auth.tenant) {
          return errorResult(`MCP: la sesión es de ${auth.tenant} y no puede ver ${args.tenant}.`);
        }
        const raw = await getSettings(env.DB, args.tenant);
        return textResult({ ok: true, tenant: args.tenant, settings: effectiveSettings(raw) });
      } catch (e) {
        return errorResult(e instanceof Error ? e.message : String(e));
      }
    },
  );

  server.registerTool(
    "retry_job",
    {
      title: "Reintentar un job atascado",
      description:
        "Reintenta UN job atascado (draft/failed/scheduled sin avanzar): re-evalúa el gate con tryEnqueueClipPublish (mismo camino que 'Publicar ahora', manual=false: respeta franja, espaciamiento, topes, CALENTAMIENTO, rolling 24 h y mismo-clip-hoy) y corre UN ciclo del tenant. Si el gate frena, devuelve el motivo y NO publica nada. Si el job ya está posted, se niega. Re-evalúa TODAS las plataformas del clip (como 'Publicar ahora'); la respuesta dice qué se encoló y qué quedó frenado. Escribe en D1 (jobs, eventos): no usar para probar con la cuota alta.",
      inputSchema: z.object({ job_id: z.number().int().describe("id del job atascado en D1") }),
    },
    async (args) => {
      try {
        const job = await getJobById(env.DB, args.job_id);
        if (!job) return errorResult(`Job ${args.job_id} no existe.`);
        if (!isTenant(job.tenant_id)) return errorResult(`Job ${args.job_id} con tenant desconocido.`);
        const tenant: Tenant = job.tenant_id;
        if (job.state === "posted") {
          return textResult({ ok: false, job_id: job.id, tenant, platform: job.platform, state: "posted", reason: "el job ya está publicado; no se republica el mismo job" });
        }
        if (job.clip_id === null || job.platform === null) {
          return errorResult(`Job ${job.id} no es de publicación (sin clip/plataforma).`);
        }
        const stateBefore = job.state;
        const clip = await getClip(env.DB, job.clip_id, tenant);
        if (!clip) return errorResult(`Clip ${job.clip_id} del job ${job.id} no existe.`);
        const enq = await tryEnqueueClipPublish(env, tenant, clip, { actor: "mcp" });
        const targetBlocked = enq.blocked.filter((b) => b.startsWith(`${job.platform}:`));
        const targetEnqueued = enq.jobs.some((j) => j.platform === job.platform);
        let cycle: { ran: boolean; processed: number; stalled: number } | null = null;
        if (targetEnqueued) {
          const r = await runWorkerCycle(env, tenant, { force: true });
          cycle = { ran: r.ran, processed: r.processed, stalled: r.stalled };
        }
        const after = await getJobById(env.DB, job.id);
        return textResult({
          ok: targetEnqueued,
          job_id: job.id,
          tenant,
          platform: job.platform,
          state_before: stateBefore,
          state_after: after?.state ?? stateBefore,
          evidence_url: after?.evidence_url ?? null,
          enqueued_platforms: enq.jobs.map((j) => j.platform),
          blocked: enq.blocked,
          warnings: enq.warnings,
          target_blocked: targetBlocked,
          cycle,
        });
      } catch (e) {
        return errorResult(e instanceof Error ? e.message : String(e));
      }
    },
  );

  server.registerTool(
    "verify_url",
    {
      title: "Verificar una URL publicada",
      description:
        "Comprueba en vivo que una URL responde (GET, sigue redirecciones, 10 s de límite). Un 'posted' en D1 NO prueba que el vídeo esté vivo: esto es lo que lo verifica. Solo lectura; no publica nada.",
      inputSchema: z.object({ url: z.string().url().describe("URL a comprobar (http/https)") }),
    },
    async (args) => {
      try {
        const u = new URL(args.url);
        if (u.protocol !== "https:" && u.protocol !== "http:") {
          return errorResult("verify_url solo acepta http/https.");
        }
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 10_000);
        try {
          const res = await fetch(args.url, { method: "GET", redirect: "follow", signal: ctrl.signal });
          await res.arrayBuffer().catch(() => undefined);
          return textResult({
            ok: res.ok,
            url: args.url,
            final_url: res.url,
            status: res.status,
            content_type: res.headers.get("content-type"),
            content_length: res.headers.get("content-length"),
          });
        } finally {
          clearTimeout(timer);
        }
      } catch (e) {
        return errorResult(e instanceof Error ? e.message : String(e));
      }
    },
  );

  return server;
}

/** Herramientas expuestas (para GET /mcp con auth). */
export const AVC_MCP_TOOLS = ["list_jobs", "get_job", "get_clip", "list_clips", "get_settings", "retry_job", "verify_url"] as const;

/** Punto de entrada /mcp. Auth primero (401 sin ella); GET con
 *  auth describe el endpoint; POST va al handler MCP. Las
 *  herramientas reciben la auth por closure (createAvcMcpServer
 *  la toma como parámetro del factory). */
export async function handleMcpRequest(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  const auth = await authorizeMcp(req, env);
  if (!auth) {
    return new Response(JSON.stringify({ ok: false, error: "MCP no autorizado: BRIDGE_TOKEN (Bearer o X-Bridge-Token) o sesión del panel." }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }
  if (req.method === "GET") {
    return new Response(
      JSON.stringify({ ok: true, service: "avc-panel-mcp", version: "0.1.1", tools: AVC_MCP_TOOLS, auth: auth.kind }, null, 2),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }
  if (req.method !== "POST") {
    return new Response(JSON.stringify({ ok: false, error: "Método no permitido en /mcp (GET o POST)." }), {
      status: 405,
      headers: { "Content-Type": "application/json" },
    });
  }
  // La auth viaja por closure: el factory se crea por petición
  // con la auth de ESTA petición y cada herramienta la lee del
  // closure de createAvcMcpServer(env, auth).
  const handler = createMcpHandler(() => createAvcMcpServer(env, auth), { route: "/mcp", corsOptions: false });
  return handler(req, env, ctx);
}
