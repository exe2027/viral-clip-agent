-- Migración 0010 del panel v17, adaptada a D1.
-- Origen: drizzle/0010_per_platform_publish_states.sql (sin cambios de esquema, solo D1).
ALTER TABLE `clips` ADD COLUMN `publish_states` text DEFAULT '{}' NOT NULL;
