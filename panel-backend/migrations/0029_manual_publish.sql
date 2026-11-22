-- 0029: publicación manual (2026-11-19): el job guarda si nació
-- del botón "Publicar ahora" (manual=1) o del auto-enqueue (0).
-- El re-chequeo y la reserva saltan tope+espaciamiento solo para
-- los manuales; los automáticos siguen frenando igual.
ALTER TABLE jobs ADD COLUMN manual INTEGER NOT NULL DEFAULT 0;
