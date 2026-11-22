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
