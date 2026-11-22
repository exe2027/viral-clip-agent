-- Migración 0021 (2026-11-18): jobs.claimed_by — quién tiene el job.
--
-- El ciclo del Worker solo reanuda un job 'processing' si lo
-- reclamó él mismo (claimed_by = 'worker') y ya venció su
-- next_run_at. Un job en manos del puente (claimed_by = 'bridge',
-- puesto por GET /api/jobs/next) nunca lo reanuda el ciclo, así
-- que no hay doble ejecución (hallazgo M4).
--
-- Backfill: los jobs 'processing' que ya existen los dejó el
-- ciclo (run-once / cron), nunca el puente, así que son 'worker'.
ALTER TABLE jobs ADD COLUMN claimed_by TEXT;
UPDATE jobs SET claimed_by = 'worker' WHERE state = 'processing';
