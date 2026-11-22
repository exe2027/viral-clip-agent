-- Migración 0023 (2026-11-18): Telegram multi-tenant (v2).
--
-- La v1 guardaba UN solo chat global (TELEGRAM_CHAT_ID como secreto)
-- y solo enviaba a sebastian: cualquier usuario que entrara al mismo
-- bot compartido podía recibir datos ajenos. La v2 vincula cada chat
-- de Telegram a UN tenant con un código de un solo uso generado
-- DENTRO del panel con sesión iniciada. Nunca se pide la contraseña
-- del panel dentro de Telegram (un bot falso podría robarla).
--
-- telegram_links: un chat pertenece a un solo tenant y un tenant a
-- un solo chat; vincular mueve el chat y reemplaza el anterior.
-- last_sent_day deduplica el resumen diario por tenant+día (la marca
-- vieja telegram_analytics_sent_day en settings se respeta una vez
-- por compatibilidad con la v1).
-- telegram_link_codes: el código NUNCA se guarda en claro, solo su
-- hash SHA-256; caduca a los 10 minutos y se borra al usarse.
CREATE TABLE IF NOT EXISTS telegram_links (
  chat_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  linked_at INTEGER NOT NULL,
  last_sent_day TEXT
);
CREATE TABLE IF NOT EXISTS telegram_link_codes (
  code_hash TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);
