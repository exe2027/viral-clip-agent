-- Migración 0014: subtítulos SIEMPRE y COMPLETOS (regla del usuario
-- 2026-11-17 22:10).
--
-- Todo clip lleva subtítulos quemados de todo lo que dice el
-- influencer. Un clip sin voz detectada queda BLOQUEADO
-- (publish_blocked=1, needs_human_transcription=1) hasta que el
-- usuario lo transcriba de oído; el publishGate del Worker frena
-- su publicación y el Editor lo muestra como bloqueado.
-- subtitles_complete=1 significa que el SRT cubre el clip entero.
-- Los clips existentes quedan en 0 (sin marcar): el repo marca el
-- flag al subtitular, y el Editor/PATCH pueden ponerlo o quitarlo.

ALTER TABLE clips ADD COLUMN publish_blocked INTEGER NOT NULL DEFAULT 0;
ALTER TABLE clips ADD COLUMN needs_human_transcription INTEGER NOT NULL DEFAULT 0;
ALTER TABLE clips ADD COLUMN subtitles_complete INTEGER NOT NULL DEFAULT 0;
CREATE INDEX clips_publish_blocked ON clips (publish_blocked);
