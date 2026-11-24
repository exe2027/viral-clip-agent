-- 0044: clave de origen del clip (corte automático desde YouTube, spec 2026-11-24).
--
-- Problema: `source_id` en D1 es ENTERO y un id de video de YouTube
-- (`NrNdw_Y6_bI`) no cabe: `int()` falla y queda en 0 para TODOS los
-- clips de YouTube, así que el dedup entre corridas nunca detecta el
-- duplicado (`"0" == "NrNdw_Y6_bI"` siempre falso).
--
-- Esta migración añade `source_key` TEXT (id del video de YouTube, o
-- '' para Twitch): es el marcador durable de clip de YouTube (no
-- vacío ⟺ YouTube) y la clave del dedup por (source_key, start_sec).
-- Se aplica en AMBOS tenants (clips-avc y clips-avc-kaez): si falta
-- en kaez, el INSERT de `createClip` (lista fija de columnas) fallaría
-- y kaez no encolaría ningún clip de YouTube.

ALTER TABLE clips ADD COLUMN source_key TEXT DEFAULT '';
