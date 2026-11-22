-- Migración 0026 (2026-11-18): control del Worker desde Telegram (v3.1).
--
-- /parar es peligroso (detiene TODA la publicación), así que exige
-- confirmación en dos pasos: /parar crea una confirmación pendiente
-- y solo un SÍ posterior (o /parar sí) ejecuta la parada. La
-- confirmación vive en D1, no en memoria: el webhook y el cron
-- pueden correr en instancias distintas del Worker. Una fila por
-- chat, de un solo uso (se borra al confirmar o al cancelar) y con
-- expiración de 5 minutos (STOP_CONFIRM_TTL_MS).
-- El último estado conocido del Worker (para la alerta de
-- desactivación inesperada) NO necesita tabla: vive en settings
-- (clave telegram_worker_last_running, 1/0), que effectiveSettings
-- conserva al guardar desde el panel.
CREATE TABLE IF NOT EXISTS telegram_stop_confirmations (
  chat_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);
