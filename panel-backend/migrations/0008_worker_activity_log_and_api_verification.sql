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
