-- Migración 0027 (2026-11-19): fase en vivo del cerebro.
--
-- El validador (worker-validate.ts) dice si la Cola se alimenta o
-- está atascada, pero no dice QUÉ está haciendo el cerebro AHORA.
-- El usuario lo pidió en el chat principal (2026-11-19): el estado
-- del Worker tiene que mostrar en tiempo real en qué está
-- (buscando picos, cortando un clip, modo VOD, esperando sin
-- directos), la hora del último barrido, la del último clip y
-- cuándo corre la próxima vez. El cerebro (GitHub Actions,
-- cerebro-live) reporta su fase a POST /api/worker/phase en cada
-- etapa. Una fila por tenant: el último reporte gana. Si el reporte
-- tiene más de 20 min (un ciclo son 15 min) la fase se muestra
-- como desactualizada; nunca se inventa otra.
CREATE TABLE IF NOT EXISTS worker_phases (
  tenant_id TEXT PRIMARY KEY,
  phase TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  run_id TEXT NOT NULL DEFAULT '',
  updated_at INTEGER NOT NULL
);
