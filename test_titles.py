#!/usr/bin/env python3
"""Regresión del bug del título EN (2026-11-16) + títulos-verdad
(2026-11-26): el manifiesto del clip de IlloJuan (VOD GTA V)
llevaba title_en "WHAT A PLAY 😱 GTA 6" y desc_en
"Insane moment in GTA 6".

Causa original: suggest_titles() hardcodeaba "GTA 6" en ES y EN, y
honest_title() devolvía el título mentiroso tal cual cuando la
validación fallaba sin degradación posible (gameplay visible, juego
equivocado). Regla: ningún título puede prometer GTA 6 (ni ningún
juego) sin verificar el juego real del clip; si el juego es otro,
el título nombra ese juego; si no se conoce, no nombra ninguno.

Conducta nueva (spec títulos-verdad + enmienda 2026-11-26):
suggest_titles() ya no genera plantillas: devuelve (None, None)
como señal de retención. honest_title() devuelve (None, True,
motivo) cuando el título miente o no hay título: NUNCA un título
seguro de reglas ni una degradación a reacción. El único título
que sale de honest_title() es el mismo que entró, cuando pasa el
check.

Uso: python3 test_titles.py
"""
import unittest

from auto_clips import honest_title, suggest_titles

DETAIL = {"risas": 6, "hype": 44}
# Ficha del momento del clip de IlloJuan: juego real GTA V.
CARD_GTA_V = {
    "game": "GTA V",
    "game_source": "capítulo de Twitch: Grand Theft Auto V",
    "streamer": "IlloJuan",
    "transcript_text": "ganar la partida",
    "chat_top": [{"text": "NOOOOO"}, {"text": "nooo por favor"}],
    "risas": 6,
    "hype": 44,
    "chat_msgs_peak_10s": 44,
    "payoff_ts_clip": 5.0,
    "clip_duration_s": 21.0,
}


class TestSuggestTitlesRetention(unittest.TestCase):
    """suggest_titles ya no genera títulos: retención (None, None)."""

    def test_es_returns_none(self):
        title, desc = suggest_titles(DETAIL, "es", game="GTA V")
        self.assertIsNone(title)
        self.assertIsNone(desc)

    def test_en_returns_none(self):
        title, desc = suggest_titles(DETAIL, "en", game="GTA V")
        self.assertIsNone(title)
        self.assertIsNone(desc)

    def test_gta_6_returns_none(self):
        title, _ = suggest_titles(DETAIL, "en", game="GTA 6")
        self.assertIsNone(title)

    def test_no_game_returns_none(self):
        title, desc = suggest_titles(DETAIL, "en", game=None)
        self.assertIsNone(title)
        self.assertIsNone(desc)
        title_es, desc_es = suggest_titles(DETAIL, "es", game=None)
        self.assertIsNone(title_es)
        self.assertIsNone(desc_es)


class TestHonestTitleNoLyingTitle(unittest.TestCase):
    def test_lying_gta_6_title_on_gta_v_clip_is_retained(self):
        title, degraded, why = honest_title(
            "WHAT A PLAY 😱 GTA 6", CARD_GTA_V, None, 5.0, "en")
        self.assertIsNone(title)
        self.assertTrue(degraded)
        self.assertIn("retención", why)

    def test_lying_gta_6_title_es_on_gta_v_clip_is_retained(self):
        title, degraded, why = honest_title(
            "QUÉ JUGADA 😱 GTA 6", CARD_GTA_V, None, 5.0, "es")
        self.assertIsNone(title)
        self.assertTrue(degraded)
        self.assertIn("retención", why)

    def test_no_title_is_retained(self):
        title, degraded, why = honest_title(
            None, CARD_GTA_V, None, 5.0, "es")
        self.assertIsNone(title)
        self.assertTrue(degraded)
        self.assertIn("retención", why)

    def test_honest_gta_v_title_passes_unchanged(self):
        title, degraded, _why = honest_title(
            "GTA V: Ganar la partida y el chat explotó",
            CARD_GTA_V, None, 5.0, "es")
        self.assertEqual(title, "GTA V: Ganar la partida y el chat explotó")
        self.assertFalse(degraded)


if __name__ == "__main__":
    unittest.main(verbosity=2)
