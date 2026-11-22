-- Migración 0015: revocación de tokens de sesión (Frente 2, B1).
--
-- La sesión del panel es un token firmado stateless (auth.ts:
-- body.sig HMAC, sin estado en el servidor), así que el logout no
-- invalidaba nada: el Bearer seguía siendo válido hasta su
-- expiración (30 días). Esta tabla guarda la huella SHA-256 del
-- token revocado (nunca el token en claro), con su expiración para
-- poder purgar filas viejas. El Worker consulta esta tabla en cada
-- verificación de sesión (requireSession) y el logout inserta la
-- huella del token que se cierra.
--
-- La tabla `sessions` de la 0011 queda como reserva sin uso: no se
-- toca (sus filas no corresponden a tokens body.sig).

CREATE TABLE revoked_tokens (
  token_hash TEXT PRIMARY KEY,
  username TEXT NOT NULL DEFAULT '',
  tenant_id TEXT NOT NULL DEFAULT 'sebastian',
  revoked_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX revoked_tokens_expires ON revoked_tokens (expires_at);
