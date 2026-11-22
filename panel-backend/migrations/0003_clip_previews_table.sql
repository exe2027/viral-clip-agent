-- Migración 0003 del panel v17, adaptada a D1.
-- Origen: drizzle/0003_clip_previews_table.sql (sin cambios de esquema, solo D1).
CREATE TABLE clip_previews (
  clip_id INTEGER PRIMARY KEY,
  status TEXT NOT NULL,
  error TEXT NOT NULL DEFAULT '',
  updated_at INTEGER NOT NULL
);
