-- Migración 0001 del panel v17, adaptada a D1.
-- Origen: drizzle/0001_initial.sql (sin cambios de esquema, solo D1).
CREATE TABLE entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
