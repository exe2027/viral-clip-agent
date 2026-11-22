-- 0034_event_counters.sql — contadores agregados de eventos repetidos
-- (A2, 2026-11-20, cuota D1: 100.000 escrituras/día por cuenta).
--
-- El dedupe de 6 h (0033 + db.ts logWorkerEvent/logClipEvent) ya evita
-- guardar un evento idéntico cada minuto, pero el evento suprimido
-- desaparecía sin dejar rastro de cuántas veces se repitió. Esta tabla
-- guarda UNA fila por (tenant, origen, tipo, mensaje, ventana de 6 h)
-- con el rango horario real de actividad, en vez de una fila por
-- ocurrencia: la ruta de eventos repetidos la actualiza como mucho
-- una vez por hora y mensaje (EVENT_COUNTER_SAMPLE_MS), nunca una
-- vez por ocurrencia.
--
-- SEMÁNTICA HONESTA DEL CONTADOR (léase antes de mostrar "N veces"):
-- `count` cuenta MUESTRAS HORARIAS con actividad (1–6 por ventana),
-- NO ocurrencias exactas. Contar cada ocurrencia costaría 1 escritura
-- por ocurrencia y anularía el ahorro de la dedupe; el muestreo horario
-- es el compromiso: la primera ocurrencia de la ventana crea la fila
-- (count=1, first_at = hora del evento original) y cada hora con
-- actividad suma 1 y actualiza last_at. El resumen para el panel es
-- "activo entre HH:MM y HH:MM (N muestras/hora)", nunca "N veces"
-- salvo que el conteo sea exacto. Si algún día se necesita el número
-- exacto de ocurrencias, la única forma es 1 UPSERT por ocurrencia
-- (ver informe A2 en runs/anti-recaida-2026-11-20/A2.md).
-- La ventana es la misma que la dedupe (6 h, EVENT_DEDUPE_WINDOW_MS):
-- window_start = floor(now / 6h) * 6h (ms epoch, alineada a UTC).
CREATE TABLE IF NOT EXISTS event_counters (
  tenant_id TEXT NOT NULL,
  source TEXT NOT NULL, -- 'worker_events' | 'clip_events' | 'notifications'
  kind TEXT NOT NULL, -- kind (worker_events/notifications) o step (clip_events)
  message TEXT NOT NULL,
  window_start INTEGER NOT NULL, -- ms epoch, inicio de la ventana de 6 h
  count INTEGER NOT NULL DEFAULT 1, -- nº de muestras horarias con actividad (ver arriba)
  first_at INTEGER NOT NULL, -- ms epoch, primera ocurrencia de la ventana
  last_at INTEGER NOT NULL, -- ms epoch, última muestra de la ventana
  PRIMARY KEY (tenant_id, source, kind, message, window_start)
);
-- La caducidad (pruneOldEvents, 14 días) borra por antigüedad de la
-- ventana: sin este índice sería un escaneo completo por lote.
CREATE INDEX IF NOT EXISTS idx_event_counters_window
  ON event_counters(window_start);
-- Dedupe de notifications (jobs.ts notify): sin índice, cada aviso
-- escanearía la tabla entera del tenant para buscar el último
-- idéntico (mismo caso que la 0033 para worker/clip_events).
CREATE INDEX IF NOT EXISTS idx_notifications_dedupe
  ON notifications(tenant_id, kind, message, created_at);
