#!/usr/bin/env python3
"""Regresión del bug del título EN (2026-11-16): el manifiesto del
clip de IlloJuan (VOD GTA V) llevaba title_en "WHAT A PLAY 😱 GTA 6"
y desc_en "Insane moment in GTA 6".

Causa: suggest_titles() hardcodeaba "GTA 6" en ES y EN, y
honest_title() devolvía el título mentiroso tal cual cuando la
validación fallaba sin degradación posible (gameplay visible, juego
equivocado). Regla: ningún título puede prometer GTA 6 (ni ningún
juego) sin verificar el juego real del clip; si el juego es otro,
el título nombra ese juego; si no se conoce, no nombra ninguno.

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
    "payoff_ts_clip": 5.0,
    "clip_duration_s": 21.0,
}


class TestSuggestTitlesGame(unittest.TestCase):
    def test_en_gta_v_names_gta_v_not_gta_6(self):
        title, desc = suggest_titles(DETAIL, "en", game="GTA V")
        self.assertIn("GTA V", title)
        self.assertNotIn("GTA 6", title)
        self.assertNotIn("GTA 6", desc)
        self.assertIn("GTA V", desc)

    def test_es_gta_v_names_gta_v_not_gta_6(self):
        title, desc = suggest_titles(DETAIL, "es", game="GTA V")
        self.assertIn("GTA V", title)
        self.assertNotIn("GTA 6", title)
        self.assertNotIn("GTA 6", desc)

    def test_en_gta_6_only_when_game_is_gta_6(self):
        title, _ = suggest_titles(DETAIL, "en", game="GTA 6")
        self.assertIn("GTA 6", title)

    def test_no_game_names_no_game(self):
        title, desc = suggest_titles(DETAIL, "en", game=None)
        self.assertNotIn("GTA", title)
        self.assertNotIn("GTA", desc)
        title_es, desc_es = suggest_titles(DETAIL, "es", game=None)
        self.assertNotIn("GTA", title_es)
        self.assertNotIn("GTA", desc_es)


class TestHonestTitleNoLyingTitle(unittest.TestCase):
    def test_lying_gta_6_title_on_gta_v_clip_is_replaced(self):
        title, degraded, _why = honest_title(
            "WHAT A PLAY 😱 GTA 6", CARD_GTA_V, None, 5.0, "en")
        self.assertNotIn("GTA 6", title)
        self.assertTrue(degraded)

    def test_lying_gta_6_title_es_on_gta_v_clip_is_replaced(self):
        title, degraded, _why = honest_title(
            "QUÉ JUGADA 😱 GTA 6", CARD_GTA_V, None, 5.0, "es")
        self.assertNotIn("GTA 6", title)
        self.assertTrue(degraded)

    def test_honest_gta_v_title_passes_unchanged(self):
        title, degraded, _why = honest_title(
            "GTA V: Ganar la partida y el chat explotó",
            CARD_GTA_V, None, 5.0, "es")
        self.assertEqual(title, "GTA V: Ganar la partida y el chat explotó")
        self.assertFalse(degraded)


if __name__ == "__main__":
    unittest.main(verbosity=2)
