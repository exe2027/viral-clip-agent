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
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE vod_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ran_at INTEGER NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  findings INTEGER NOT NULL DEFAULT 0
);
