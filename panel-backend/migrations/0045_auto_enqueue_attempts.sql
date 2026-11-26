-- 0045: persistencia del contador A4 del auto-enqueue (fix 2026-11-26).
--
-- Problema: el contador de intentos fallidos del auto-enqueue (máx 3
-- por clip y día, retry.ts MAX_RETRIES_PER_DAY) vivía SOLO en el Map
-- en memoria del isolate del Worker (jobs.ts autoEnqueueState) y la
-- siembra tras un reinicio lo re-ponía siempre en 1 leyendo
-- clip_events (seedAutoEnqueueStateFromReads). Al reciclarse el
-- isolate entre ciclos, los mismos clips con bloqueo duro se
-- re-evaluaban eternamente sin llegar nunca a 3: consumían el
-- presupuesto de 5 clips/ciclo y el escaneo no avanzaba (seba,
-- 2026-11-26: 0 jobs creados en el día, clips 57, 102, 47, 14, 194
-- atascados en cabeza de Cola).
--
-- Esta migración crea auto_enqueue_attempts: cada intento fallido
-- hace upsert (attempts + 1) y la siembra lee el valor real. La
-- escritura es best-effort en jobs.ts (try/catch): si D1 falla
-- (p. ej. cuota 7500), el freno en memoria sigue valiendo ese ciclo,
-- como antes. Coste: 1 escritura D1 por clip frenado y ciclo (máx
-- 5/ciclo, ~250/día como mucho; el tope del tenant es 3.500/día).
--
-- Se aplica en AMBOS tenants (clips-avc y clips-avc-kaez): el mismo
-- bug latente afecta a kaez aunque hoy su Cola avance.

CREATE TABLE auto_enqueue_attempts (
  tenant_id TEXT NOT NULL,
  clip_id INTEGER NOT NULL,
  day TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_at INTEGER,
  PRIMARY KEY (tenant_id, clip_id, day)
);
