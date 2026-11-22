-- Migración 0018 (2026-11-18): categoría y permiso por influencer.
--
-- El panel es solo GTA VI por regla, pero el usuario quiere VERLO por
-- influencer en la pestaña Influencers. Los 10 del lanzamiento
-- (Rubius, IlloJuan, AuronPlay, Ibai, TheGrefg, Xokas, DjMaRiiO,
-- Knekro, RDJavi, Westcol) llevan game "GTA VI".
--
-- game: categoría del influencer (siempre "GTA VI" en este panel).
-- permission_date: fecha del permiso para clipear (YYYY-MM-DD):
--   2026-11-16 para IlloJuan, 2026-11-18 para los otros 9.
-- publish_platforms: JSON con las plataformas donde se publican sus
--   clips: ["youtube","tiktok","instagram","facebook"].
ALTER TABLE influencers ADD COLUMN game TEXT NOT NULL DEFAULT 'GTA VI';
ALTER TABLE influencers ADD COLUMN permission_date TEXT NOT NULL DEFAULT '';
ALTER TABLE influencers ADD COLUMN publish_platforms TEXT NOT NULL DEFAULT '["youtube","tiktok","instagram","facebook"]';
