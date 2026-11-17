-- migrations/0001_step_jobs.sql — tabla de jobs por PASO (workers).
--
-- Misma tabla que `clipagent/jobs.py` (STEP_JOBS_SCHEMA) en queue.db.
-- Compatible con Cloudflare D1: solo SQL estándar (TEXT/INTEGER/REAL,
-- índices normales y uno UNIQUE). Sin ON CONFLICT, sin funciones
-- propias de SQLite, sin triggers. Las fechas son TEXT ISO (UTC).
--
-- Por qué NO se llama `jobs` aquí: en queue.db ya existe una tabla
-- legacy `jobs` (cola de VODs, en jobqueue.py) con otro grano y otro
-- esquema. Esta tabla es `step_jobs`: un job = un paso de un clip en
-- una plataforma. En una D1 nueva (sin la tabla legacy) podría
-- llamarse `jobs`, pero se mantiene el mismo nombre en todos lados
-- para no tener dos esquemas distintos.
--
-- Aplicar en D1:  wrangler d1 execute clips-sebastian --file=migrations/0001_step_jobs.sql
-- Aplicar en local: lo hace solo StepJobs al abrir queue.db.

CREATE TABLE IF NOT EXISTS step_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id TEXT NOT NULL,
    plataforma TEXT,
    paso TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pending',
    intentos INTEGER NOT NULL DEFAULT 0,
    max_intentos INTEGER NOT NULL DEFAULT 3,
    proximo_reintento TEXT,
    clave_anti_duplicado TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error TEXT,
    error_kind TEXT,
    vod_job_id INTEGER,
    prioridad REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_step_jobs_idempotency
    ON step_jobs (clave_anti_duplicado);
CREATE INDEX IF NOT EXISTS idx_step_jobs_due
    ON step_jobs (estado, paso, prioridad, created_at);
CREATE INDEX IF NOT EXISTS idx_step_jobs_clip
    ON step_jobs (clip_id, paso);
CREATE TABLE IF NOT EXISTS step_job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    step_job_id INTEGER NOT NULL REFERENCES step_jobs(id),
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_step_job_events_job
    ON step_job_events (step_job_id);
