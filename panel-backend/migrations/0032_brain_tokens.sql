-- 0032_brain_tokens.sql — token de un solo uso del cerebro (2026-11-19).
--
-- Modelo genérico multi-tenant: el usuario nunca toca GitHub. El Worker
-- emite un token por dispatch a GitHub (alcance a UN tenant, 30 min) y
-- el cerebro lo canjea UNA vez en POST /api/brain/session por una
-- sesión de 12 h. Solo se guarda la huella SHA-256 del token (como
-- revoked_tokens), nunca el valor en claro. used_at NULL = sin canjear.
CREATE TABLE brain_tokens (
  token_hash TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  used_at INTEGER,
  created_at INTEGER NOT NULL
);
