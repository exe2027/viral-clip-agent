-- 0038_cutover_indexes.sql — índices que faltan (cutover 21:00 2026-11-20)
CREATE INDEX IF NOT EXISTS idx_jobs_tenant_state_posted
  ON jobs(tenant_id, state, posted_day);
CREATE INDEX IF NOT EXISTS idx_worker_events_tenant_at
  ON worker_events(tenant_id, at);
CREATE INDEX IF NOT EXISTS idx_clip_events_tenant_at
  ON clip_events(tenant_id, at);
