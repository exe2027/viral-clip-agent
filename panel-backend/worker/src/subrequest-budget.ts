// Presupuesto de subrequests por invocación (P0, 2026-11-27).
//
// El plan Free de Cloudflare Workers limita las subrequests por
// invocación (~50). El cron del Worker corre cada 5 min y en UNA
// invocación encadena: el ciclo publicador (que al final hace
// collectAnalytics 6 h + runEngagement 1 h) + maybeRunBudgetMonitor
// + dispatch live + watchdog + telegram diario + dispatch diario +
// revalidate + validación diaria de cuentas. A las ~09:00 (UTC)
// coinciden el engagement horario, el telegram diario, el dispatch
// diario y los dispatches, y el 27-11-2026 el ciclo chocó con
// "Too many subrequests by single Worker invocation" en engagement
// v2, telegram y github_dispatch (el envío diario salió a las
// 09:05, en la siguiente invocación). No se quita funcionalidad:
// lo pesado que no cabe en esta invocación queda DEBIDO y corre en
// la siguiente (5 min después) o en la siguiente hora.
//
// Reglas:
// - HeavySlot: en cada ciclo corre SOLO UNA de las dos tareas
//   pesadas post-ciclo (analíticas 6 h o engagement 1 h), alternando
//   por hora UTC par/impar. La diferida corre en el ciclo siguiente
//   (retraso < 1 h); cada una conserva su propio throttle interno.
// - HeavyDispatchBudget: máx 2 dispatches pesados por invocación
//   (live, watchdog, telegram diario, dispatch diario, revalidate),
//   en orden de prioridad. El resto queda debido y corre en la
//   siguiente invocación de cron (5 min después).

/** Tareas pesadas post-ciclo que se alternan por hora UTC. */
export type HeavySlot = "analytics" | "engagement";

/** Qué tarea pesada post-ciclo toca en este instante.
 *  Hora UTC par -> analíticas; impar -> engagement. */
export function heavySlotForCycle(nowMs: number): HeavySlot {
  const hourUtc = new Date(nowMs).getUTCHours();
  return hourUtc % 2 === 0 ? "analytics" : "engagement";
}

/** Nombres de los dispatches pesados post-ciclo, en orden de
 *  prioridad (el primero es el que más importa que salga en esta
 *  invocación). */
export const HEAVY_DISPATCH_PRIORITY = [
  "live",
  "watchdog",
  "telegram_daily",
  "dispatch_daily",
  "revalidate",
] as const;

export type HeavyDispatchName = (typeof HEAVY_DISPATCH_PRIORITY)[number];

/** Máx. de dispatches pesados por invocación (2: deja hueco de
 *  subrequests para el ciclo publicador + el monitor). */
export const HEAVY_DISPATCH_MAX_PER_INVOCATION = 2;

/** Presupuesto de dispatches pesados para UNA invocación de cron.
 *  Pide permiso por nombre, en orden de prioridad; los que no
 *  caben quedan debidos para la siguiente invocación (5 min). */
export class HeavyDispatchBudget {
  private used = 0;
  private readonly max: number;

  constructor(max: number = HEAVY_DISPATCH_MAX_PER_INVOCATION) {
    this.max = max;
  }

  /** true si este dispatch puede correr en esta invocación. */
  allow(_name: HeavyDispatchName): boolean {
    if (this.used >= this.max) return false;
    this.used += 1;
    return true;
  }

  get spent(): number {
    return this.used;
  }
}
