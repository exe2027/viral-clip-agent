// Tipos mínimos de Cloudflare para compilar sin depender de que
// @cloudflare/workers-types esté instalado. Si el paquete está
// instalado, sus tipos mandan; este archivo solo cubre lo que el
// Worker usa (D1, R2, cron). No inventa APIs: solo las que existen.

export interface D1Result<T = Record<string, unknown>> {
  results: T[];
  success: boolean;
  meta: { changes?: number; last_row_id?: number | string };
}

export interface D1PreparedStatement {
  bind(...values: unknown[]): D1PreparedStatement;
  first<T = Record<string, unknown>>(
    colName?: string,
  ): Promise<T | null>;
  run(): Promise<D1Result>;
  all<T = Record<string, unknown>>(): Promise<D1Result<T>>;
}

export interface D1Database {
  prepare(query: string): D1PreparedStatement;
  batch<T = Record<string, unknown>>(
    statements: D1PreparedStatement[],
  ): Promise<D1Result<T>[]>;
  exec(query: string): Promise<D1Result>;
}

export interface R2ObjectBody {
  body: ReadableStream;
  httpMetadata?: { contentType?: string };
  size: number;
  arrayBuffer(): Promise<ArrayBuffer>;
}

export interface R2Bucket {
  put(
    key: string,
    value: ReadableStream | ArrayBuffer | string,
    options?: { httpMetadata?: { contentType?: string } },
  ): Promise<{ key: string; size: number }>;
  get(key: string, options?: { range?: { offset: number; length: number } }): Promise<R2ObjectBody | null>;
  delete(key: string): Promise<void>;
  head(key: string): Promise<{ size: number } | null>;
}

export interface ExecutionContext {
  waitUntil(promise: Promise<unknown>): void;
  passThroughOnException(): void;
}

export interface ScheduledEvent {
  cron: string;
  scheduledTime: number;
}

export interface Env {
  DB: D1Database;
  CLIPS_BUCKET: R2Bucket;
  // Secretos (wrangler secret put; NUNCA en el repo ni en wrangler.toml):
  // El login YA NO usa PANEL_PASSWORD_HASH: cada usuario tiene su
  // hash en D1 (panel_users.password_hash, sembrado con
  // scripts/seed-users.sql). SESSION_SECRET firma las cookies; el
  // payload lleva el tenant y una sesión solo vale en el Worker de
  // SU tenant (verifySessionToken exige payload.tenant === env.TENANT).
  SESSION_SECRET: string; // HMAC de la cookie de sesión (base64)
  CREDENTIAL_ENCRYPTION_KEY: string; // AES-256-GCM maestro, 32 bytes en base64;
  // de él se deriva una clave por tenant (HKDF, crypto.ts).
  BRIDGE_TOKEN: string; // token del publisher-bridge (header X-Bridge-Token)
  GITHUB_TOKEN: string; // secreto: dispara repository_dispatch al repo
  CIRCLECI_API_TOKEN?: string; // secreto: dispara el pipeline de CircleCI (recopilación larga, spec 2026-11-26 §5.3)
  // Telegram: resumen diario de analíticas (2026-11-18). Secretos
  // (wrangler secret put, nunca en el repo ni en wrangler.toml).
  // Sin token, el envío diario se salta en silencio.
  // v2 multi-tenant: el chat ya NO es un secreto global (se vincula
  // por tenant con un código, migración 0023). El webhook exige el
  // secreto del header X-Telegram-Bot-Api-Secret-Token: sin él, 403.
  TELEGRAM_BOT_TOKEN?: string;
  TELEGRAM_WEBHOOK_SECRET?: string;
  // Analítica D1 para el presupuesto de escrituras (A1/A6,
  // 2026-11-20): el contador lee el consumo del día desde la API
  // GraphQL de Cloudflare (la misma que usa el panel de
  // Cloudflare). CLOUDFLARE_API_TOKEN es un secreto (wrangler
  // secret put, permiso "Account Analytics Read"; NUNCA en el
  // repo ni en wrangler.toml, nunca se imprime). Sin él, el
  // contador cae al fallback por eventos. CLOUDFLARE_ACCOUNT_ID
  // es una var (el id de la cuenta; mismo valor que en
  // scripts/deploy_with_token.py).
  CLOUDFLARE_API_TOKEN?: string;
  CLOUDFLARE_ACCOUNT_ID?: string;
  // OAuth del asistente de onboarding (2026-11-18). Secretos
  // (wrangler secret put; NUNCA en el repo): se crean en cada
  // consola con la redirect URI del panel (ver ONBOARDING-2026-11-18.md).
  // Sin ellos, el botón Conectar dice que falta crear el cliente;
  // nunca se muestra un "Conectada" falso.
  GOOGLE_CLIENT_ID?: string;
  GOOGLE_CLIENT_SECRET?: string;
  META_APP_ID?: string;
  META_APP_SECRET?: string;
  // del cerebro (vía principal). Vacío = sin dispatch (solo flag D1).
  // Necesita permiso actions:write / repo sobre GITHUB_REPO.
  // Configuración (vars, no secretas):
  /** Tenant ÚNICO que sirve este Worker (var TENANT, "sebastian" o
   *  "kaez"). Un Worker = un tenant: si falta o no es uno de los
   *  dos, requireTenant (auth.ts) falla en voz alta y el Worker no
   *  sirve a nadie. Nunca hay default silencioso. */
  TENANT: string;
  ALLOWED_EMAIL: string; // vacío = login por contraseña; con valor = allowlist Google (futuro)
  GITHUB_REPO: string; // var: "exe2027/viral-clip-agent"
  CIRCLECI_PROJECT_SLUG: string; // var: "gh/exe2027/viral-clip-agent" (recopilación larga, spec 2026-11-26 §5.3)
  CLIP_PUBLIC_BASE_URL?: string; // var pública: dominio r2.dev de clips-avc (2026-11-19, cerebro genérico)
  /** URL pública de ESTE Worker (var por despliegue): la usa
   *  signVideoUrl para firmar /api/public-video. DEBE ser la del
   *  Worker que firma (su SESSION_SECRET es distinto por tenant);
   *  si falta, r2.ts cae a WORKER_VIDEO_BASE (avc/sebastian).
   *  Sin ella, Instagram falla en cualquier tenant que no sea
   *  sebastian (kaez, 2026-11-22). */
  WORKER_PUBLIC_URL?: string;
}
