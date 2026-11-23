-- 0042: evolución neuroevolutiva de clips para campañas que pagan
-- (Content Rewards, 2026-11-23, v1.4.0). A/B testing evolutivo,
-- NO caos: las variantes se generan OFFLINE en el cerebro y NO se
-- publican; solo sale 1 ganadora por momento/plataforma, por la vía
-- de autopublicación ya existente. Las mutaciones son PEQUEÑAS
-- (inicio/fin, hook, título, miniatura) DENTRO de la misma
-- plantilla rubius, mismo nicho GTA 6 y mismos streamers, para
-- mantener consistencia de cuenta: el algoritmo premia
-- retención/novedad dentro de un nicho y penaliza contenido
-- duplicado/repetitivo, así que publicar muchas variantes casi
-- iguales sería lo contraproducente. Ver
-- panel-backend/EVOLUTION-0042.md.
--
-- Un post = UNA campaña (regla del usuario): la ganadora no se
-- publica si el clip ya tiene envío a otra campaña (lo comprueba
-- la ruta dry-run contra campaign_submissions, 0041).
-- Solo cambiar un efecto o el estilo de subtítulos NO hace un clip
-- distinto: cada variante es un corte distinto REAL (start_sec o
-- end_sec difieren >= 1 s del control; lo comprueba
-- isDistinctCut en evolution.ts). El estilo de subtítulos queda
-- fijo en el del clip base (rubius): no es eje de mutación.
-- Fitness en dos fases: (a) cerebro (viralidad/SEO del cerebro,
-- puertas 45/80 intactas); (b) REAL a 24-48 h (vistas aprobadas en
-- Content Rewards y vistas/retención de analíticas). El fitness
-- real manda sobre el del cerebro en la siguiente generación. Sin
-- datos reales, la variante queda marcada has_real_data=0 y NUNCA
-- se inventa un fitness: la generación 1 publica la línea base
-- (control) y las mutantes quedan como candidatas sin probar, NO
-- como perdedoras (perdedora exige datos reales o veredicto del
-- validador). Los veredictos rechazados del validador NO se
-- guardan sin revisar fotogramas (regla existente).
CREATE TABLE IF NOT EXISTS evo_generations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id TEXT NOT NULL,
  streamer TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  base_clip_id INTEGER NOT NULL,
  generation INTEGER NOT NULL,
  parent_generation_id INTEGER,
  platform TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'winner_ready', 'done')),
  blocked_reason TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (tenant_id, base_clip_id, generation)
);
CREATE INDEX IF NOT EXISTS idx_evo_generations_latest
  ON evo_generations(tenant_id, base_clip_id, generation DESC);
CREATE INDEX IF NOT EXISTS idx_evo_generations_streamer
  ON evo_generations(tenant_id, streamer, created_at DESC);

CREATE TABLE IF NOT EXISTS evo_variants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id TEXT NOT NULL,
  generation_id INTEGER NOT NULL REFERENCES evo_generations(id),
  variant_index INTEGER NOT NULL,
  parent_variant_id INTEGER,
  clip_id INTEGER,
  params_json TEXT NOT NULL,
  mutations_json TEXT NOT NULL DEFAULT '[]',
  is_control INTEGER NOT NULL DEFAULT 0,
  brain_virality REAL,
  brain_seo REAL,
  brain_fitness REAL,
  real_approved_views INTEGER,
  real_views INTEGER,
  real_retention REAL,
  real_fitness REAL,
  has_real_data INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'winner', 'loser', 'published', 'blocked')),
  blocked_reason TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (generation_id, variant_index)
);
CREATE INDEX IF NOT EXISTS idx_evo_variants_generation
  ON evo_variants(generation_id, variant_index);
CREATE INDEX IF NOT EXISTS idx_evo_variants_status
  ON evo_variants(tenant_id, status);

CREATE TABLE IF NOT EXISTS evo_fitness (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id TEXT NOT NULL,
  variant_id INTEGER NOT NULL REFERENCES evo_variants(id),
  source TEXT NOT NULL CHECK (source IN ('brain', 'real')),
  virality REAL,
  seo REAL,
  approved_views INTEGER,
  views INTEGER,
  retention REAL,
  fitness REAL,
  measured_at INTEGER NOT NULL,
  UNIQUE (variant_id, source, measured_at)
);
CREATE INDEX IF NOT EXISTS idx_evo_fitness_variant
  ON evo_fitness(variant_id, source);
