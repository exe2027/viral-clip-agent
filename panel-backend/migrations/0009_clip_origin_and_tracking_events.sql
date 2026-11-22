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
