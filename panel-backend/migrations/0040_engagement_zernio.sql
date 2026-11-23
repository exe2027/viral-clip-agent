-- 0040: engagement por Zernio (2026-11-23, v5.3.0, orden del usuario).
-- SOLO Zernio por API oficial: nada de navegador, nada de scraping,
-- nada fuera de Zernio. Adentro = Comments API (leer/responder/likear
-- comentarios en TUS posts). Afuera = Like post donde Zernio lo
-- soporta (Facebook, YouTube, Instagram limited release; TikTok NO,
-- devuelve 400) y solo sobre objetivos que el usuario aporta con su
-- URL (Zernio no expone descubrir los posts de terceros ni seguir:
-- no hay endpoint de follow para IG/FB/YT/TikTok y GET
-- /v1/accounts/{id}/posts solo lista los posts de TU cuenta).
-- Los topes viven en settings (effectiveSettings) y se cuentan POR
-- plataforma+idioma+dia (America/Santiago), como el fix Zernio
-- 2026-11-23 (commit 034286b).
CREATE TABLE IF NOT EXISTS engagement_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id TEXT NOT NULL,
  day TEXT NOT NULL,
  platform TEXT NOT NULL,
  language TEXT NOT NULL DEFAULT 'es',
  account_id TEXT,
  action TEXT NOT NULL,
  target_post_id TEXT,
  comment_id TEXT,
  status TEXT NOT NULL,
  reason TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_engagement_log_day
  ON engagement_log(tenant_id, day, platform, language, action);
CREATE TABLE IF NOT EXISTS engagement_replied (
  tenant_id TEXT NOT NULL,
  comment_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  replied_at INTEGER NOT NULL,
  PRIMARY KEY (tenant_id, comment_id)
);
CREATE TABLE IF NOT EXISTS engagement_targets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  language TEXT NOT NULL DEFAULT 'es',
  streamer TEXT NOT NULL,
  post_url TEXT NOT NULL,
  post_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  comment_text TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  reason TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_engagement_targets_pending
  ON engagement_targets(tenant_id, status, created_at);
