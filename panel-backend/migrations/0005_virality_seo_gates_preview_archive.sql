-- Migración 0005 del panel v17, adaptada a D1.
-- Origen: drizzle/0005_virality_seo_gates_preview_archive.sql (sin cambios de esquema, solo D1).
ALTER TABLE `clips` ADD COLUMN `viral` text DEFAULT '{}' NOT NULL;
ALTER TABLE `clips` ADD COLUMN `seo_overrides` text DEFAULT '[]' NOT NULL;
ALTER TABLE `clips` ADD COLUMN `preview_seen` integer DEFAULT 0 NOT NULL;
ALTER TABLE `clips` ADD COLUMN `archived` integer DEFAULT 0 NOT NULL;
