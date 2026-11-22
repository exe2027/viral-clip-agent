-- Migración 0019 (2026-11-18): DOS COLAS SEPARADAS — idioma del clip.
--
-- Los vídeos en inglés se suben a los canales en inglés y los de
-- español a los de español: son colas diferentes. Hasta ahora los
-- clips NO tenían idioma y la cuenta se resolvía solo por
-- plataforma, así que un clip en inglés podía acabar publicándose
-- en el canal en español.
--
-- clips.language: "es" | "en" (default "es": los clips heredados
-- sin idioma son españoles, nunca se inventa un "en"). El cerebro
-- lo manda en POST /api/clips (contrato 2026-11-18, ver
-- DEPLOYED-2026-11-18-dos-colas.md); PATCH /api/clips/:id lo
-- corrige (validado es/en).
--
-- jobs.language: copia del idioma del clip al encolar (lo pone
-- enqueuePublishJobs). Es lo que permite contar los topes POR
-- plataforma+idioma sin JOIN: YouTube ES 3/día es independiente
-- de YouTube EN 3/día (plan, techo, espaciamiento y madurez — la
-- madurez ya era por cuenta y la cuenta ya es por plataforma+
-- idioma). Un job sin idioma (filas viejas) cuenta como "es".
ALTER TABLE clips ADD COLUMN language TEXT NOT NULL DEFAULT 'es';
ALTER TABLE jobs ADD COLUMN language TEXT NOT NULL DEFAULT 'es';
CREATE INDEX jobs_posted_day_lang ON jobs (platform, language, state, posted_day);
