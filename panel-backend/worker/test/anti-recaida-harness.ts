// Soporte de pruebas (NO es código de producción) para los simulacros
// anti-recaída — Parte C, 2026-11-20.
//
// MODELO DE REFERENCIA de los contratos A4 / A5 / B1-B2. A esta fecha esos
// módulos NO existen aún en el árbol (tampoco existen los informes
// A4.md, A5.md ni B1-B2.md en runs/anti-recaida-2026-11-20/; otros agentes
// los escriben en paralelo), así que esto implementa la especificación del
// encargo y deja los puntos de adaptación explícitos (marcados ADAPTER).
// Cuando A4/A5/B1-B2 aterricen en src/, los tests de
// anti-recaida-simulacros.test.ts deben re-enlazarse a los módulos reales y
// este archivo queda como doble de pruebas.
//
// REGLAS DURAS que este harness cumple:
// - $0, nada de pago. Entorno de prueba: SQLite local en memoria
//   (bun:sqlite) + diario R2 simulado en memoria. NUNCA la D1 real.
// - El simulacro 1 FINGE el error 7500 con un flag: jamás escribe 100.000
//   filas ni agota la cuota real de la cuenta (una D1 de prueba comparte
//   el límite de 100.000 escrituras/día por cuenta).
// - Sin secretos, sin tokens, sin deploys, sin commits.
import { Database } from "bun:sqlite";

// ---------------------------------------------------------------------------
// Contratos (especificación del encargo)
// ---------------------------------------------------------------------------

/** Cuota D1: 100.000 escrituras/día por cuenta. */
export const DAILY_D1_QUOTA_ROWS = 100_000;

/** A5: una migración se rechaza si estima más del 20% de la cuota restante. */
export const MIGRATION_MAX_FRACTION_OF_REMAINING = 0.2;

/** B1/B2: idempotency_key = clip_id + cuenta + fecha. */
export function idempotencyKey(clipId: number, account: string, date: string): string {
  return `${clipId}:${account}:${date}`;
}

/** B1: ruta del diario en R2. */
export function journalKey(tenant: string, date: string): string {
  return `wal/${tenant}/${date}.jsonl`;
}

/** Error que devuelve D1 cuando se agota la cuota diaria.
 *  ADAPTER: el texto/código exacto del error real de D1 NO está verificado
 *  (verificarlo exigiría escribir en D1, hoy agotada: 104.610/100.000).
 *  A4 debe confirmar el marcador real y ajustar isQuotaError(); el
 *  simulacro usa el código 7500 del encargo. */
export class QuotaExceededError extends Error {
  code = 7500;
  constructor(message = "D1 error 7500: daily row writes quota exceeded") {
    super(message);
    this.name = "QuotaExceededError";
  }
}

/** ADAPTER: detector del error de cuota. Hoy: código 7500 o "7500" en el
 *  mensaje. A4 debe confirmarlo contra el error real de D1. */
export function isQuotaError(err: unknown): boolean {
  if (!err || typeof err !== "object") return false;
  const e = err as { code?: unknown; message?: unknown };
  if (e.code === 7500 || e.code === "7500") return true;
  return typeof e.message === "string" && e.message.includes("7500");
}

// ---------------------------------------------------------------------------
// MockD1 — SQLite en memoria que FINGE el 7500 sin escribir nada real
// ---------------------------------------------------------------------------

const WRITE_SQL = /^\s*(INSERT|UPDATE|DELETE|REPLACE)\b/i;

class MockStmt {
  constructor(
    private db: Database,
    private sql: string,
    private parent: MockD1,
    private params: unknown[] = [],
  ) {}
  bind(...values: unknown[]): MockStmt {
    return new MockStmt(this.db, this.sql, this.parent, values);
  }
  private guardWrite(): void {
    if (WRITE_SQL.test(this.sql)) {
      this.parent.writesAttempted += 1;
      if (this.parent.exhausted) {
        // FINGIDO: no se ejecuta nada, no se escribe ninguna fila.
        throw new QuotaExceededError();
      }
    }
  }
  async first<T = Record<string, unknown>>(): Promise<T | null> {
    const row = this.db.query(this.sql).get(...(this.params as []));
    return (row as T | undefined) ?? null;
  }
  async all<T = Record<string, unknown>>(): Promise<{
    results: T[];
    success: boolean;
    meta: Record<string, unknown>;
  }> {
    return {
      results: this.db.query(this.sql).all(...(this.params as [])) as T[],
      success: true,
      meta: {},
    };
  }
  async run(): Promise<{
    results: never[];
    success: boolean;
    meta: { changes?: number; last_row_id?: number };
  }> {
    this.guardWrite();
    const info = this.db.query(this.sql).run(...(this.params as []));
    this.parent.writesSucceeded += 1;
    return {
      results: [],
      success: true,
      meta: { changes: info.changes, last_row_id: Number(info.lastInsertRowid) },
    };
  }
}

export class MockD1 {
  /** Cuando está a true, TODA escritura lanza el 7500 fingido. */
  exhausted = false;
  writesAttempted = 0;
  writesSucceeded = 0;
  readonly raw: Database;

  constructor() {
    this.raw = new Database(":memory:");
    this.raw.exec(
      "CREATE TABLE publish_log (" +
        "idempotency_key TEXT PRIMARY KEY, clip_id INTEGER NOT NULL, " +
        "account TEXT NOT NULL, date TEXT NOT NULL, " +
        "platform_post_id TEXT, applied_at INTEGER NOT NULL)",
    );
  }

  prepare(sql: string): MockStmt {
    return new MockStmt(this.raw, sql, this);
  }

  countPublishLog(): number {
    return (
      this.raw.query("SELECT COUNT(*) AS n FROM publish_log").get() as { n: number }
    ).n;
  }
}

// ---------------------------------------------------------------------------
// MockR2Journal — diario JSONL wal/<tenant>/<fecha>.jsonl (B1/B2)
// ---------------------------------------------------------------------------

export type JournalLine = {
  idempotency_key: string;
  clip_id: number;
  account: string;
  date: string;
  action: "publish";
  status: "intent" | "published" | "failed";
  platform_post_id?: string;
  ts: number;
};

export class MockR2Journal {
  private objects = new Map<string, string[]>();

  append(tenant: string, date: string, line: JournalLine): void {
    const key = journalKey(tenant, date);
    const lines = this.objects.get(key) ?? [];
    lines.push(JSON.stringify(line));
    this.objects.set(key, lines);
  }

  readLines(tenant: string, date: string): JournalLine[] {
    return (this.objects.get(journalKey(tenant, date)) ?? []).map((l) =>
      JSON.parse(l),
    ) as JournalLine[];
  }

  hasResult(tenant: string, date: string, key: string): JournalLine | undefined {
    return this
      .readLines(tenant, date)
      .find((l) => l.idempotency_key === key && l.status !== "intent");
  }

  hasIntent(tenant: string, date: string, key: string): boolean {
    return this
      .readLines(tenant, date)
      .some((l) => l.idempotency_key === key && l.status === "intent");
  }

  lineCount(tenant: string, date: string): number {
    return this.readLines(tenant, date).length;
  }

  toJsonl(tenant: string, date: string): string {
    return (this.objects.get(journalKey(tenant, date)) ?? []).join("\n");
  }
}

// ---------------------------------------------------------------------------
// MockPlatform — la plataforma (TikTok/YT/IG/FB) simulada
// ---------------------------------------------------------------------------

export class MockPlatform {
  /** post_id por idempotency_key: la plataforma recuerda lo publicado. */
  private posts = new Map<string, string>();
  publishCalls = 0;

  publish(key: string): string {
    this.publishCalls += 1;
    const existing = this.posts.get(key);
    if (existing) return existing;
    const postId = `post_${key.replace(/[^A-Za-z0-9]/g, "_")}`;
    this.posts.set(key, postId);
    return postId;
  }

  findByKey(key: string): string | null {
    return this.posts.get(key) ?? null;
  }

  publishedCount(): number {
    return this.posts.size;
  }
}

// ---------------------------------------------------------------------------
// A4 — Circuito 7500: al aparecer el error, parar bucles no esenciales +
// una sola alerta. El publicador sigue (vía diario).
// ---------------------------------------------------------------------------

export type LoopName =
  | "publisher"
  | "analytics"
  | "brain-dispatch"
  | "watcher-scan"
  | "seo-retry";

/** Único bucle esencial: el publicador. Todo lo demás es no esencial. */
const ESSENTIAL_LOOPS: ReadonlySet<LoopName> = new Set(["publisher"]);

export class QuotaCircuit {
  tripped = false;
  trippedAt: number | null = null;
  /** ADAPTER: destino real de la alerta (Telegram/panel) lo define A4. */
  alerts: string[] = [];

  onD1Error(err: unknown, now = Date.now()): void {
    if (!isQuotaError(err)) return;
    if (this.tripped) return; // una sola alerta, aunque el error se repita
    this.tripped = true;
    this.trippedAt = now;
    this.alerts.push(
      "ALERTA CUOTA D1 AGOTADA (7500): bucles no esenciales detenidos; " +
        "el publicador sigue con el diario wal/. Una sola alerta.",
    );
  }

  shouldRunLoop(loop: LoopName): boolean {
    return !this.tripped || ESSENTIAL_LOOPS.has(loop);
  }

  /** La cuota vuelve (reset 21:00 -03): se reanudan los bucles. */
  reset(): void {
    this.tripped = false;
    this.trippedAt = null;
  }
}

// ---------------------------------------------------------------------------
// A5 — Chequeo previo de migraciones: rechazo si supera el 20% restante
// ---------------------------------------------------------------------------

export type MigrationPrecheck =
  | {
      allowed: true;
      estimatedRows: number;
      remaining: number;
      maxAllowed: number;
    }
  | {
      allowed: false;
      estimatedRows: number;
      remaining: number;
      maxAllowed: number;
      reason: string;
    };

export function migrationPrecheck(opts: {
  estimatedRows: number;
  usedToday: number;
  dailyQuota?: number;
}): MigrationPrecheck {
  const dailyQuota = opts.dailyQuota ?? DAILY_D1_QUOTA_ROWS;
  const remaining = dailyQuota - opts.usedToday;
  const maxAllowed = Math.floor(remaining * MIGRATION_MAX_FRACTION_OF_REMAINING);
  if (opts.estimatedRows <= maxAllowed) {
    return { allowed: true, estimatedRows: opts.estimatedRows, remaining, maxAllowed };
  }
  return {
    allowed: false,
    estimatedRows: opts.estimatedRows,
    remaining,
    maxAllowed,
    reason:
      `Migración RECHAZADA: estima ${opts.estimatedRows} filas, ` +
      `más del 20% de la cuota restante (${maxAllowed} de ${remaining}).`,
  };
}

/** Ejecuta la migración solo si el precheck la acepta; si no, 0 escrituras. */
export async function runMigrationIfAllowed(
  d1: MockD1,
  precheck: MigrationPrecheck,
  rows: Array<{ key: string; clipId: number; account: string; date: string }>,
  now = Date.now(),
): Promise<{ ran: boolean; inserted: number; reason?: string }> {
  if (!precheck.allowed) return { ran: false, inserted: 0, reason: precheck.reason };
  let inserted = 0;
  for (const row of rows) {
    await d1
      .prepare(
        "INSERT OR IGNORE INTO publish_log " +
          "(idempotency_key, clip_id, account, date, platform_post_id, applied_at) " +
          "VALUES (?, ?, ?, ?, NULL, ?)",
      )
      .bind(row.key, row.clipId, row.account, row.date, now)
      .run();
    inserted += 1;
  }
  return { ran: true, inserted };
}

// ---------------------------------------------------------------------------
// B1/B2 — Publicador con diario: intención -> publicar -> anotar resultado
// ---------------------------------------------------------------------------

export class JournalPublisher {
  constructor(
    private journal: MockR2Journal,
    private platform: MockPlatform,
    private tenant = "sebastian",
  ) {}

  /** Publica una vez por idempotency_key; repetir es no-op (sin duplicado). */
  publish(
    clipId: number,
    account: string,
    date: string,
    now = Date.now(),
  ): { postId: string; duplicate: boolean } {
    const key = idempotencyKey(clipId, account, date);
    const existing = this.journal.hasResult(this.tenant, date, key);
    if (existing?.platform_post_id) {
      return { postId: existing.platform_post_id, duplicate: true };
    }
    if (!this.journal.hasIntent(this.tenant, date, key)) {
      this.journal.append(this.tenant, date, {
        idempotency_key: key,
        clip_id: clipId,
        account,
        date,
        action: "publish",
        status: "intent",
        ts: now,
      });
    }
    const postId = this.platform.publish(key);
    this.journal.append(this.tenant, date, {
      idempotency_key: key,
      clip_id: clipId,
      account,
      date,
      action: "publish",
      status: "published",
      platform_post_id: postId,
      ts: now,
    });
    return { postId, duplicate: false };
  }

  /** Simula el reinicio del Worker a mitad de subida: la plataforma ya
   *  publicó, pero el resultado NUNCA se anotó en el diario. */
  publishWithCrashAfterPlatformCall(
    clipId: number,
    account: string,
    date: string,
    now = Date.now(),
  ): never {
    const key = idempotencyKey(clipId, account, date);
    if (!this.journal.hasIntent(this.tenant, date, key)) {
      this.journal.append(this.tenant, date, {
        idempotency_key: key,
        clip_id: clipId,
        account,
        date,
        action: "publish",
        status: "intent",
        ts: now,
      });
    }
    this.platform.publish(key);
    throw new Error("SIMULATED WORKER RESTART: crash tras publicar, antes de anotar");
  }

  /** Recuperación tras reinicio: intención sin resultado.
   *  Si la plataforma ya tiene el post (por la key), se anota el resultado
   *  SIN volver a publicar. Si no lo tiene, se publica una vez. */
  recover(
    clipId: number,
    account: string,
    date: string,
    now = Date.now(),
  ): { postId: string; republished: boolean } {
    const key = idempotencyKey(clipId, account, date);
    const existing = this.journal.hasResult(this.tenant, date, key);
    if (existing?.platform_post_id) {
      return { postId: existing.platform_post_id, republished: false };
    }
    const onPlatform = this.platform.findByKey(key);
    if (onPlatform) {
      this.journal.append(this.tenant, date, {
        idempotency_key: key,
        clip_id: clipId,
        account,
        date,
        action: "publish",
        status: "published",
        platform_post_id: onPlatform,
        ts: now,
      });
      return { postId: onPlatform, republished: false };
    }
    const { postId } = this.publish(clipId, account, date, now);
    return { postId, republished: true };
  }
}

/** Vuelca el diario en D1 cuando vuelve la cuota. Idempotente: una segunda
 *  pasada no duplica filas (UNIQUE idempotency_key + ON CONFLICT DO NOTHING). */
export async function drainJournalToD1(
  d1: MockD1,
  journal: MockR2Journal,
  tenant: string,
  date: string,
  now = Date.now(),
): Promise<{ applied: number; skipped: number }> {
  let applied = 0;
  let skipped = 0;
  for (const line of journal.readLines(tenant, date)) {
    if (line.status !== "published") continue;
    const before = d1.countPublishLog();
    await d1
      .prepare(
        "INSERT INTO publish_log " +
          "(idempotency_key, clip_id, account, date, platform_post_id, applied_at) " +
          "VALUES (?, ?, ?, ?, ?, ?) " +
          "ON CONFLICT(idempotency_key) DO NOTHING",
      )
      .bind(
        line.idempotency_key,
        line.clip_id,
        line.account,
        line.date,
        line.platform_post_id ?? null,
        now,
      )
      .run();
    if (d1.countPublishLog() > before) applied += 1;
    else skipped += 1;
  }
  return { applied, skipped };
}
