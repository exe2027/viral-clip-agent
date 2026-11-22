-- Migración 0007 del panel v17, adaptada a D1.
-- Origen: drizzle/0007_api_accounts_table.sql (sin cambios de esquema, solo D1).
CREATE TABLE `api_accounts` (
  `id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
  `platform` text NOT NULL,
  `language` text NOT NULL DEFAULT 'es',
  `label` text NOT NULL DEFAULT '',
  `credential` text NOT NULL DEFAULT '',
  `extra` text NOT NULL DEFAULT '',
  `status` text NOT NULL DEFAULT 'pendiente',
  `note` text NOT NULL DEFAULT '',
  `created_at` integer NOT NULL
);
