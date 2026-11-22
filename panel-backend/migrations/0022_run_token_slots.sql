-- Migración 0022 (2026-11-18): jobs.run_token + jobs.slot_day + publish_slots.
--
-- Auditoría run-2, NEEDS-VALIDATION §3 y §4:
-- §3 (lease/run token): el lease de 300 s del ciclo puede vencer a
-- mitad de paso y otro ciclo re-reclama el mismo job; sin token de
-- corrida, el apply del perdedor pisa al ganador (doble markPosted
-- y doble published_count). run_token (16 bytes aleatorios, hex) se
-- escribe en el reclamo del ciclo y cada apply lo exige en su UPDATE.
-- §4 (TOCTOU del gate): dos jobs distintos de la misma plataforma
-- e idioma pasaban el re-chequeo del gate en el mismo ciclo porque
-- el conteo solo veía publicados. publish_slots es el contador
-- atómico por (tenant, plataforma, idioma, día): reservar un cupo
-- antes del adaptador lo hace visible al siguiente job del mismo
-- ciclo y al siguiente ciclo del cron. jobs.slot_day marca el job
-- que tiene el cupo (reutilizable en pasos multi-step).
--
-- Backfill: ningún job viejo tiene token ni cupo; ambos quedan NULL
-- y el código trata NULL como "sin token" (comportamiento antiguo,
-- p. ej. jobs del puente) hasta su próximo reclamo del ciclo.
ALTER TABLE jobs ADD COLUMN run_token TEXT;
ALTER TABLE jobs ADD COLUMN slot_day TEXT;
CREATE TABLE IF NOT EXISTS publish_slots (
  tenant_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  language TEXT NOT NULL,
  day TEXT NOT NULL,
  reserved_count INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (tenant_id, platform, language, day)
);
