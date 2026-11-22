-- Migración 0012 (NUEVA): una sola instalación multi-tenant.
--
-- Decisión del usuario 17 nov 2026, 15:05 y 15:33: UNA sola página,
-- https://avc.pages.dev, con login al entrar. Cada persona entra con
-- su login y ve solo su panel, sus cuentas, su cola y sus
-- credenciales. NO un avc-colega.pages.dev separado. AVC = Agent
-- Viral Clip.
--
-- Antes: una instalación por persona (panel-sebastian /
-- panel-colega, cada una con su D1, su R2 y su PANEL_PASSWORD_HASH).
-- Ahora: un solo Worker (avc), una D1 (clips-avc) y un R2
-- (clips-avc). El aislamiento ya no es por despliegue sino por
-- tenant_id en cada fila + login por usuario en panel_users.
--
-- Reglas de aislamiento (las impone el código, no solo el SQL):
--   * Toda consulta de datos de usuario filtra por tenant_id = el
--     tenant de la sesión (nunca por env.TENANT_NAME: ya no existe).
--   * Las claves de R2 llevan prefijo clips/<tenant>/... (r2.ts).
--   * Las credenciales se cifran con una clave derivada por tenant
--     (HKDF sobre CREDENTIAL_ENCRYPTION_KEY + tenant, crypto.ts):
--     aunque la D1 sea compartida, un tenant no puede descifrar las
--     credenciales del otro con su propia clave derivada.
--   * El login ya no es PANEL_PASSWORD_HASH (secreto único por
--     despliegue): es username + contraseña contra panel_users
--     (password_hash PBKDF2, mismo formato que antes). Los usuarios
--     se crean con scripts/seed-users.sql (hashes generados con
--     scripts/gen-password-hash.mjs, nunca por chat).
--   * El cron del Worker procesa los jobs de AMBOS tenants, uno por
--     uno (runWorkerCycle por tenant), y el dispatch a GitHub usa
--     panel-start-<tenant> / panel-stop-<tenant> según corresponda.
--
-- Las filas migradas de app.db heredan tenant_id='sebastian' por
-- defecto; scripts/migrate-appdb-to-d1.py escribe el tenant_id
-- correcto (--tenant) en cada INSERT, así que una migración nueva
-- ya queda bien etiquetada.

-- Login por usuario (panel_users ya existía como lista informativa).
ALTER TABLE panel_users ADD COLUMN username TEXT NOT NULL DEFAULT '';
ALTER TABLE panel_users ADD COLUMN password_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE panel_users ADD COLUMN tenant TEXT NOT NULL DEFAULT 'sebastian';
CREATE UNIQUE INDEX panel_users_username ON panel_users (username) WHERE username <> '';

-- tenant_id en todas las tablas con datos de usuario.
ALTER TABLE clips ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'sebastian';
CREATE INDEX clips_tenant ON clips (tenant_id);
ALTER TABLE api_accounts ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'sebastian';
CREATE INDEX api_accounts_tenant ON api_accounts (tenant_id);
ALTER TABLE jobs ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'sebastian';
CREATE INDEX jobs_tenant ON jobs (tenant_id);
ALTER TABLE notifications ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'sebastian';
CREATE INDEX notifications_tenant ON notifications (tenant_id);
ALTER TABLE clip_events ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'sebastian';
CREATE INDEX clip_events_tenant ON clip_events (tenant_id);
ALTER TABLE clip_stats ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'sebastian';
CREATE INDEX clip_stats_tenant ON clip_stats (tenant_id);
ALTER TABLE clip_previews ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'sebastian';
CREATE INDEX clip_previews_tenant ON clip_previews (tenant_id);
ALTER TABLE sources ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'sebastian';
CREATE INDEX sources_tenant ON sources (tenant_id);
ALTER TABLE influencers ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'sebastian';
CREATE INDEX influencers_tenant ON influencers (tenant_id);
ALTER TABLE vod_checks ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'sebastian';
CREATE INDEX vod_checks_tenant ON vod_checks (tenant_id);
ALTER TABLE worker_events ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'sebastian';
CREATE INDEX worker_events_tenant ON worker_events (tenant_id);

-- settings y worker_state: una fila por tenant (id=1 ya no vale como
-- clave única). El código hace upsert por tenant_id.
ALTER TABLE settings ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'sebastian';
CREATE UNIQUE INDEX settings_tenant ON settings (tenant_id);
ALTER TABLE worker_state ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'sebastian';
CREATE UNIQUE INDEX worker_state_tenant ON worker_state (tenant_id);
