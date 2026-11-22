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
