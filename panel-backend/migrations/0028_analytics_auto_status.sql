-- Migración 0028 (2026-11-19): estado de la medición automática de
-- analíticas por plataforma.
--
-- collectAnalytics (worker/src/publish/stats.ts) mide los clips
-- publicados llamando a la API de cada plataforma y guarda las
-- cifras en clip_stats. Esta tabla guarda, por tenant+plataforma,
-- el ÚLTIMO resultado de ese intento: si la plataforma mide (ok),
-- si no hay credencial (sin_credencial), si el token no tiene el
-- permiso de insights (sin_permiso) o si falló por otra cosa
-- (error), con el motivo literal y la hora de la última medición
-- real. GET /api/analytics la expone como `auto_status` por
-- plataforma SIN tocar los números manuales de clip_stats: un
-- "sin_permiso" nunca se convierte en ceros.
-- `sin_medicion` no se guarda: es el estado por defecto cuando no
-- hay fila (la plataforma aún no se ha intentado medir).
CREATE TABLE IF NOT EXISTS analytics_status (
  tenant_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  last_measured_at INTEGER,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (tenant_id, platform)
);
