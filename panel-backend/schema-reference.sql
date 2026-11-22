-- schema.sql — esquema D1 completo del panel (0001-0011 concatenadas).
-- Generado desde migrations/; no editar a mano, regenerar.

-- ===== 0001_initial.sql =====
-- Migración 0001 del panel v17, adaptada a D1.
-- Origen: drizzle/0001_initial.sql (sin cambios de esquema, solo D1).
CREATE TABLE entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

-- ===== 0002_panel_tables_sources_clips_influencers_accounts_settings.sql =====
-- Migración 0002 del panel v17, adaptada a D1.
-- Origen: drizzle/0002_panel_tables_sources_clips_influencers_accounts_settings.sql (sin cambios de esquema, solo D1).
DROP TABLE entries;
CREATE TABLE settings (
  id INTEGER PRIMARY KEY,
  data TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  label TEXT NOT NULL,
  handle TEXT NOT NULL,
  status TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE influencers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  platform TEXT NOT NULL,
  profile_url TEXT NOT NULL,
  language TEXT NOT NULL DEFAULT 'es',
  permission INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  notes TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL
);
CREATE TABLE sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  platform TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  streamer TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pendiente',
  progress INTEGER NOT NULL DEFAULT 0,
  status_note TEXT NOT NULL DEFAULT '',
  influencer_id INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE clips (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER,
  influencer_id INTEGER,
  title TEXT NOT NULL,
  start_label TEXT NOT NULL DEFAULT '',
  start_sec INTEGER NOT NULL DEFAULT 0,
  end_sec INTEGER NOT NULL DEFAULT 0,
  score INTEGER NOT NULL DEFAULT 0,
  score_kind TEXT NOT NULL DEFAULT 'ia',
  reason TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'sugerido',
  subtitle_style TEXT NOT NULL DEFAULT 'tiktok',
  layout_mode TEXT NOT NULL DEFAULT 'auto',
  seo_title TEXT NOT NULL DEFAULT '',
  seo_description TEXT NOT NULL DEFAULT '',
  seo_tags TEXT NOT NULL DEFAULT '',
  thumb_text TEXT NOT NULL DEFAULT '',
  platforms TEXT NOT NULL DEFAULT '[]',
  scheduled_for TEXT,
  published_at INTEGER,
  file_deleted INTEGER NOT NULL DEFAULT 0,
  r2_delete_after INTEGER, -- 0039: vencimiento de la retención 24 h del MP4 en R2 ("subidos")
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE vod_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ran_at INTEGER NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  findings INTEGER NOT NULL DEFAULT 0
);

-- ===== 0003_clip_previews_table.sql =====
-- Migración 0003 del panel v17, adaptada a D1.
-- Origen: drizzle/0003_clip_previews_table.sql (sin cambios de esquema, solo D1).
CREATE TABLE clip_previews (
  clip_id INTEGER PRIMARY KEY,
  status TEXT NOT NULL,
  error TEXT NOT NULL DEFAULT '',
  updated_at INTEGER NOT NULL
);

-- ===== 0004_clip_captions_and_preview_progress.sql =====
-- Migración 0004 del panel v17, adaptada a D1.
-- Origen: drizzle/0004_clip_captions_and_preview_progress.sql (sin cambios de esquema, solo D1).
ALTER TABLE `clips` ADD COLUMN `captions` text DEFAULT '{}' NOT NULL;
ALTER TABLE `clip_previews` ADD COLUMN `progress` integer DEFAULT 0 NOT NULL;
ALTER TABLE `clip_previews` ADD COLUMN `step` text DEFAULT '' NOT NULL;

-- ===== 0005_virality_seo_gates_preview_archive.sql =====
-- Migración 0005 del panel v17, adaptada a D1.
-- Origen: drizzle/0005_virality_seo_gates_preview_archive.sql (sin cambios de esquema, solo D1).
ALTER TABLE `clips` ADD COLUMN `viral` text DEFAULT '{}' NOT NULL;
ALTER TABLE `clips` ADD COLUMN `seo_overrides` text DEFAULT '[]' NOT NULL;
ALTER TABLE `clips` ADD COLUMN `preview_seen` integer DEFAULT 0 NOT NULL;
ALTER TABLE `clips` ADD COLUMN `archived` integer DEFAULT 0 NOT NULL;

-- ===== 0006_phase_2_access_worker_limits_analytics.sql =====
-- Migración 0006 del panel v17, adaptada a D1.
-- Origen: drizzle/0006_phase_2_access_worker_limits_analytics.sql (sin cambios de esquema, solo D1).
CREATE TABLE panel_users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'dueno',
  status TEXT NOT NULL DEFAULT 'activo',
  note TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL
);
CREATE TABLE worker_state (
  id INTEGER PRIMARY KEY,
  running INTEGER NOT NULL DEFAULT 0,
  started_at INTEGER,
  last_note TEXT NOT NULL DEFAULT '',
  updated_at INTEGER NOT NULL
);
CREATE TABLE clip_stats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  clip_id INTEGER NOT NULL,
  platform TEXT NOT NULL,
  views INTEGER,
  retention_pct REAL,
  shares INTEGER,
  saves INTEGER,
  comments INTEGER,
  new_subs INTEGER,
  window_label TEXT NOT NULL DEFAULT '7d',
  recorded_at INTEGER NOT NULL
);
ALTER TABLE `clips` ADD COLUMN `game` text DEFAULT '' NOT NULL;
ALTER TABLE `clips` ADD COLUMN `ai_titled` integer DEFAULT 0 NOT NULL;
ALTER TABLE `sources` ADD COLUMN `game` text DEFAULT '' NOT NULL;

-- ===== 0007_api_accounts_table.sql =====
-- Migración 0007 del panel v17, adaptada a D1.
-- Origen: drizzle/0007_api_accounts_table.sql (sin cambios de esquema, solo D1).
CREATE TABLE `api_accounts` (
  `id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
  `platform` text NOT NULL,
  `language` text NOT NULL DEFAULT 'es',
  `label` text NOT NULL DEFAULT '',
  `credential` text NOT NULL DEFAULT '',
  `extra` text NOT NULL DEFAULT '',
  `status` text NOT NULL DEFAULT 'pendiente',
  `note` text NOT NULL DEFAULT '',
  `created_at` integer NOT NULL
);

-- ===== 0008_worker_activity_log_and_api_verification.sql =====
-- Migración 0008 del panel v17, adaptada a D1.
-- Origen: drizzle/0008_worker_activity_log_and_api_verification.sql (sin cambios de esquema, solo D1).
CREATE TABLE `worker_events` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`at` integer NOT NULL,
	`kind` text NOT NULL,
	`message` text NOT NULL
);
ALTER TABLE `api_accounts` ADD `verified_at` integer;
ALTER TABLE `api_accounts` ADD `verify_ok` integer;
ALTER TABLE `api_accounts` ADD `verify_message` text DEFAULT '' NOT NULL;

-- ===== 0009_clip_origin_and_tracking_events.sql =====
-- Migración 0009 del panel v17, adaptada a D1.
-- Origen: drizzle/0009_clip_origin_and_tracking_events.sql (sin cambios de esquema, solo D1).
ALTER TABLE `clips` ADD COLUMN `origin` text DEFAULT 'vod' NOT NULL;
CREATE TABLE `clip_events` (
  `id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
  `clip_id` integer NOT NULL,
  `at` integer NOT NULL,
  `step` text NOT NULL,
  `actor` text NOT NULL,
  `message` text NOT NULL,
  `failed` integer DEFAULT 0 NOT NULL
);

-- ===== 0010_per_platform_publish_states.sql =====
-- Migración 0010 del panel v17, adaptada a D1.
-- Origen: drizzle/0010_per_platform_publish_states.sql (sin cambios de esquema, solo D1).
ALTER TABLE `clips` ADD COLUMN `publish_states` text DEFAULT '{}' NOT NULL;

-- ===== 0011_jobs_notifications_encrypted_credentials.sql =====
-- Migración 0011 (NUEVA, solo en el backend independiente).
-- Jobs genéricos del Worker + notificaciones + credenciales cifradas.
-- El Worker, una vez iniciado (botón Iniciar), avanza estos jobs solo.
-- `posted` exige evidence_url: la impone el código (jobs.markPosted),
-- no un CHECK (D1 no valida URLs), pero el estado posted sin URL es
-- un bug, no un estado válido.

-- Jobs: una fila por (clip, plataforma, kind). kind cubre todo el
-- pipeline: vod_check → cut → subtitle → package → publish → metrics.
-- En el plan principal (solo Cloudflare) el Worker ejecuta vod_check
-- (Helix por HTTP) y publish (pasos de API por cron); cut/subtitle
-- son manuales (el usuario sube el MP4 9:16) y el puente de GitHub
-- Actions es solo una alternativa (ver COMPUTE.md).
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL DEFAULT 'publish',
  clip_id INTEGER,
  platform TEXT,
  api_account_id INTEGER,
  state TEXT NOT NULL DEFAULT 'scheduled',
  idempotency_key TEXT,
  resume_data TEXT,
  platform_id TEXT,
  evidence_url TEXT,
  title TEXT NOT NULL DEFAULT '',
  caption TEXT NOT NULL DEFAULT '',
  r2_key TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  retryable INTEGER NOT NULL DEFAULT 1,
  next_run_at INTEGER,
  posted_day TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX jobs_idempotency_key ON jobs (idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX jobs_due ON jobs (state, next_run_at, created_at);
CREATE INDEX jobs_clip_platform ON jobs (clip_id, platform);
CREATE INDEX jobs_posted_day ON jobs (platform, state, posted_day);

-- Notificaciones visibles en el panel (Tracking + campana).
-- Telegram solo si el usuario lo configura en Ajustes
-- (settings.telegram_bot_token + telegram_chat_id); sin configurar,
-- el aviso vive solo aquí.
CREATE TABLE notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  clip_id INTEGER,
  message TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  read_at INTEGER
);
CREATE INDEX notifications_unread ON notifications (read_at, created_at);

-- Sesiones por servidor (reserva): hoy la sesión es una cookie
-- firmada sin estado (auth.ts); esta tabla existe para poder
-- invalidar sesiones desde el servidor si hace falta en el futuro.
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  user_label TEXT NOT NULL DEFAULT 'owner',
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0
);

-- Credenciales cifradas (AES-256-GCM, formato v1:iv:cipher).
-- Las columnas legacy `credential` / `extra` quedan y el código ya
-- no las lee: la migración de datos las deja VACÍAS.
ALTER TABLE api_accounts ADD COLUMN credential_enc TEXT NOT NULL DEFAULT '';
ALTER TABLE api_accounts ADD COLUMN extra_enc TEXT NOT NULL DEFAULT '';

-- Índices que faltaban para Tracking / Analíticas.
CREATE INDEX clip_events_clip_at ON clip_events (clip_id, at);
CREATE INDEX clip_stats_clip_platform ON clip_stats (clip_id, platform);


-- ===== 0013_accounts_tenant.sql =====
-- Migración 0013: tenant_id en accounts (cuentas sociales). La 0012
-- no la cubrió; sin esta columna no hay aislamiento por tenant en
-- GET /api/social-accounts.
ALTER TABLE accounts ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'sebastian';
CREATE INDEX accounts_tenant ON accounts (tenant_id);


-- ===== 0015_revoked_tokens.sql =====
-- Migración 0015: revocación de tokens de sesión (Frente 2, B1).
-- Huella SHA-256 del token revocado (nunca el token en claro).
CREATE TABLE revoked_tokens (
  token_hash TEXT PRIMARY KEY,
  username TEXT NOT NULL DEFAULT '',
  tenant_id TEXT NOT NULL DEFAULT 'sebastian',
  revoked_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX revoked_tokens_expires ON revoked_tokens (expires_at);
