-- 0039_r2_retention.sql — retención 24 h del MP4 en R2 tras publicar en todas (orden usuario 2026-11-21 21:28)
-- Resuelve la excepción del borrado 2026-11-20 10:05 ("BORRADO TRAS PUBLICAR EN TODAS"):
-- cuando un clip se publica en TODAS sus plataformas, el MP4 NO se borra de inmediato: se guarda
-- 24 HORAS en estado "subidos" (r2_delete_after = instante en que vence la retención) y luego se
-- borra de R2. Solo el archivo: D1 conserva registro, links y metadatos (file_deleted=1 marca el
-- borrado; la columna ya existía desde 0002). Si hay un job de publicación pendiente para ese clip
-- (incluido un |repubN por takedown esperando re-subida), NO se borra: el MP4 se conserva para
-- el re-upload. El borrado nunca toca la fila del clip, sus jobs ni sus links.
ALTER TABLE clips ADD COLUMN r2_delete_after INTEGER;
CREATE INDEX IF NOT EXISTS idx_clips_r2_delete_after ON clips(tenant_id, r2_delete_after);
