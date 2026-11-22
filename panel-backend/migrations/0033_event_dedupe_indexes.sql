-- 0033_event_dedupe_indexes.sql — índices para la dedupe de eventos
-- (2026-11-20, cuota D1).
--
-- La dedupe (worker/src/db.ts logWorkerEvent/logClipEvent) busca el
-- último evento idéntico antes de insertar; sin índice sería un
-- escaneo completo de worker_events/clip_events en cada log. El
-- throttle de dispatch por lectura (hasRecentBrainToken) busca el
-- último token del tenant: mismo caso en brain_tokens.
CREATE INDEX IF NOT EXISTS idx_worker_events_dedupe
  ON worker_events(tenant_id, kind, message, at);
CREATE INDEX IF NOT EXISTS idx_clip_events_dedupe
  ON clip_events(clip_id, tenant_id, step, message, at);
CREATE INDEX IF NOT EXISTS idx_brain_tokens_tenant_created
  ON brain_tokens(tenant_id, created_at);
