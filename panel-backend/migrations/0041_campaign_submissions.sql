-- 0041: campaign_submissions — envíos a marketplaces de clipping
-- (Content Rewards, 2026-11-23, v1.3.0). Un post = UNA campaña,
-- una sola vez, dentro de 30 min desde la publicación real.
--
-- La tabla es DOS cosas a la vez, y por eso una sola tabla:
--   (a) REGISTRO de envíos: cada envío (o intento) a una campaña
--       queda aquí, con su submission_id del marketplace. Es la
--       fuente de verdad anti-duplicado: la puerta de la extensión
--       bloquea si existe fila para tenant+clip+plataforma+campaña
--       (el estado `posted` de D1 solo dice que el clip se publicó,
--       NO que se envió a Content Rewards — confundirlos era el bug
--       de lib.js:293-299).
--   (b) COLA EN NUBE: la fila nace en `pending` (asignación
--       registrada antes de publicar, o intento bloqueado) y vive en
--       D1, no en el Chrome del usuario. Si Chrome/PC se cierra, la
--       fila sigue ahí: la extensión la retoma al reabrir (alarma de
--       5 min, GET /api/campaign-submissions?pending=1) y el Worker
--       marca `expired` lo que pasa de 30 min sin enviarse, para que
--       nada se envíe tarde. El Worker NO envía al marketplace: no
--       tiene la API key de Content Rewards del tenant (vive solo en
--       el Chrome del usuario); la extensión sigue siendo el único
--       remitente, la cola en D1 es lo que sobrevive sin Chrome.
--
-- Escritura SOLO vía Worker (POST/PATCH /api/campaign-submissions,
-- con sesión del panel): nunca directo a D1 desde el navegador.
-- `published_at_ms` es el instante REAL de publicación (el que
-- fija markPosted en jobs.ts al pasar el job a posted con su
-- evidence_url); NUNCA se inventa con Date.now() en el cliente:
-- si aún no se publicó, es NULL y el envío no puede salir.
-- `net_rate_per_1k` es la tarifa NETA (bruta x 0,9: comisión del
-- 10% de Content Rewards). El piso es $1/1k NETO: BenDaDonnn
-- ($1,00 bruto = $0,90 neto) queda bajo el piso y no se
-- auto-asigna, aunque su fila pueda existir como registro manual.
CREATE TABLE IF NOT EXISTS campaign_submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id TEXT NOT NULL,
  clip_id INTEGER NOT NULL,
  platform TEXT NOT NULL,
  marketplace TEXT NOT NULL DEFAULT 'content_rewards',
  campaign_id TEXT NOT NULL,
  campaign_name TEXT,
  streamer TEXT,
  gross_rate_per_1k REAL,
  net_rate_per_1k REAL,
  min_views INTEGER,
  cap_per_clip REAL,
  post_url TEXT,
  published_at_ms INTEGER,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'submitted', 'failed', 'expired')),
  submission_id TEXT,
  views INTEGER,
  amount_usd REAL,
  error TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_campaign_submissions_unique
  ON campaign_submissions(tenant_id, clip_id, platform, campaign_id);
CREATE INDEX IF NOT EXISTS idx_campaign_submissions_clip
  ON campaign_submissions(tenant_id, clip_id, platform);
CREATE INDEX IF NOT EXISTS idx_campaign_submissions_pending
  ON campaign_submissions(tenant_id, status, published_at_ms);
