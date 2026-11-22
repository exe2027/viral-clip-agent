-- Migración 0030 (2026-11-19): handles reales por canal en la tabla
-- accounts (cuentas sociales) + idioma por cuenta.
--
-- CAUSA RAÍZ (ANALYTICS-FIX-2026-11-19): /resumen y /api/analytics no
-- tenían de dónde sacar el handle de cada cuenta: la tabla accounts
-- (diseñada para canal + handle, migración 0002) estaba VACÍA y
-- api_accounts solo guarda una etiqueta genérica ("YouTube",
-- "TikTok ES"). Por eso telegram.ts hardcodeaba "Canales ES ·
-- @elclipdegta6" para las 4 plataformas y los 2 idiomas, cuando los
-- handles reales son distintos por canal.
--
-- Esta migración añade `language` a accounts (ES/EN: un canal por
-- plataforma+idioma, como /api/channels) y siembra las 6 cuentas con
-- handle VERIFICADO el 2026-11-19 (fuente por fila en `note`):
-- - YouTube ES @elclipdegta6: existe en youtube.com/@elclipdegta6
--   ("El Clip de GTA 6", verificado en vivo).
-- - YouTube EN @gta6clips: existe en youtube.com/@gta6clips
--   ("GTA6clips", verificado en vivo).
-- - TikTok ES @elclipdegta6____bits: 3 evidence_url de jobs posted
--   en D1 (clips 7, 13, 23).
-- - TikTok EN @elclipdegta6en___: evidence_url del job 19 (clip 12).
-- - Instagram ES @elmegaclipgta6: existe en instagram.com/
--   elmegaclipgta6 (cuenta de creador, verificado en vivo).
-- - Facebook ES "Elclipdegta6": nombre de la Página vinculada a
--   Instagram (confirmado por el usuario 2026-11-19 00:28); no es un
--   @handle, es el nombre de la Página.
-- Instagram EN y Facebook EN NO se siembran: no hay handle verificado
-- (0 publicados EN en ambas); el código muestra "sin handle
-- registrado" en vez de inventar uno. Cuando el usuario confirme
-- esos handles, se añaden con otra migración o desde el panel.
-- La siembra es idempotente (solo si no existe ya esa
-- plataforma+idioma+tenant): re-aplicar no duplica filas.
ALTER TABLE accounts ADD COLUMN language TEXT NOT NULL DEFAULT 'es';

INSERT INTO accounts (tenant_id, platform, label, handle, status, note, sort, language)
SELECT 'sebastian', 'youtube', 'YouTube ES', '@elclipdegta6', 'conectada', 'Handle verificado 2026-11-19 en youtube.com/@elclipdegta6.', 1, 'es'
WHERE NOT EXISTS (SELECT 1 FROM accounts WHERE tenant_id = 'sebastian' AND platform = 'youtube' AND language = 'es');
INSERT INTO accounts (tenant_id, platform, label, handle, status, note, sort, language)
SELECT 'sebastian', 'youtube', 'YouTube EN', '@gta6clips', 'conectada', 'Handle verificado 2026-11-19 en youtube.com/@gta6clips.', 2, 'en'
WHERE NOT EXISTS (SELECT 1 FROM accounts WHERE tenant_id = 'sebastian' AND platform = 'youtube' AND language = 'en');
INSERT INTO accounts (tenant_id, platform, label, handle, status, note, sort, language)
SELECT 'sebastian', 'tiktok', 'TikTok ES', '@elclipdegta6____bits', 'conectada', 'Handle verificado 2026-11-19 en evidence_url de jobs posted D1 (clips 7, 13, 23).', 3, 'es'
WHERE NOT EXISTS (SELECT 1 FROM accounts WHERE tenant_id = 'sebastian' AND platform = 'tiktok' AND language = 'es');
INSERT INTO accounts (tenant_id, platform, label, handle, status, note, sort, language)
SELECT 'sebastian', 'tiktok', 'TikTok EN', '@elclipdegta6en___', 'conectada', 'Handle verificado 2026-11-19 en evidence_url del job 19 (clip 12).', 4, 'en'
WHERE NOT EXISTS (SELECT 1 FROM accounts WHERE tenant_id = 'sebastian' AND platform = 'tiktok' AND language = 'en');
INSERT INTO accounts (tenant_id, platform, label, handle, status, note, sort, language)
SELECT 'sebastian', 'instagram', 'Instagram ES', '@elmegaclipgta6', 'conectada', 'Handle verificado 2026-11-19 en instagram.com/elmegaclipgta6 (cuenta de creador).', 5, 'es'
WHERE NOT EXISTS (SELECT 1 FROM accounts WHERE tenant_id = 'sebastian' AND platform = 'instagram' AND language = 'es');
INSERT INTO accounts (tenant_id, platform, label, handle, status, note, sort, language)
SELECT 'sebastian', 'facebook', 'Facebook ES', 'Elclipdegta6', 'conectada', 'Nombre de la Página vinculada a Instagram, confirmado por el usuario 2026-11-19 00:28. No es un @handle.', 6, 'es'
WHERE NOT EXISTS (SELECT 1 FROM accounts WHERE tenant_id = 'sebastian' AND platform = 'facebook' AND language = 'es');
