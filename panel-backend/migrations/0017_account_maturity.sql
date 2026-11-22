-- Migración 0017 (2026-11-18): escalado automático de topes por
-- antigüedad/actividad de la cuenta (orden del usuario).
--
-- Criterio (documentado en ESCALADO-TOPES-2026-11-18.md): una cuenta
-- pasa de NUEVA a ESTABLECIDA cuando tiene MÁS de 30 días desde su
-- primera publicación Y al menos 10 publicaciones sin sanciones
-- (strikes = 0). Al pasar, sus topes suben solos de 1/día a los
-- normales (YouTube plan 3/día máx 5, resto plan 2/día máx 3). Si hay
-- sanción (strikes > 0), vuelve a NUEVA (1/día) hasta que los
-- strikes se pongan a 0 y se vuelva a cumplir el criterio.
--
-- first_published_at: ms epoch de la primera publicación real
--   (lo pone el Worker en markPosted; NULL = aún no ha publicado).
-- published_count: publicaciones reales acumuladas (jobs posted).
-- strikes: sanciones de la plataforma (lo pone el usuario/agente
--   con PATCH /api/accounts/:id {strikes}; 0 = sin sanciones).
-- maturity: estado vigente ("nueva" | "establecida"), recalculado
--   por el Worker como mucho UNA vez al día (ver maturity_checked_day).
-- maturity_checked_day: día America/Santiago (YYYY-MM-DD) del último
--   recálculo; el cron */5 solo recalcula si el día cambió.
ALTER TABLE api_accounts ADD COLUMN first_published_at INTEGER;
ALTER TABLE api_accounts ADD COLUMN published_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE api_accounts ADD COLUMN strikes INTEGER NOT NULL DEFAULT 0;
ALTER TABLE api_accounts ADD COLUMN maturity TEXT NOT NULL DEFAULT 'nueva';
ALTER TABLE api_accounts ADD COLUMN maturity_checked_day TEXT;
