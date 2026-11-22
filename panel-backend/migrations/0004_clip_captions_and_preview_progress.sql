-- Migración 0004 del panel v17, adaptada a D1.
-- Origen: drizzle/0004_clip_captions_and_preview_progress.sql (sin cambios de esquema, solo D1).
ALTER TABLE `clips` ADD COLUMN `captions` text DEFAULT '{}' NOT NULL;
ALTER TABLE `clip_previews` ADD COLUMN `progress` integer DEFAULT 0 NOT NULL;
ALTER TABLE `clip_previews` ADD COLUMN `step` text DEFAULT '' NOT NULL;
