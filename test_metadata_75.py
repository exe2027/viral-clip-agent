#!/usr/bin/env python3
"""Pruebas de 7.5: metadata única por clip y plataforma, desde la
transcripción real; longitudes validadas; sin títulos repetidos;
juego y streamer de config/VOD (nunca hardcodeados); crédito al
streamer (configurable); debe poder alcanzar SEO >= 90.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.metadata import (package_clip, score_package_seo,
                                validate_metadata_lengths)

CARD = {
    'streamer': 'illojuan', 'game': 'GTA 6', 'game_label': 'GTA 6',
    'game_source': 'categoría de Twitch', 'evidence': 'high',
    'evidence_notes': ['cita del streamer'], 'evidence_tokens': ['GTA 6'],
    'transcript_text': 'No me lo puedo creer, esto es increíble, mira lo '
                       'que acaba de pasar en GTA 6',
    'what': 'el streamer reacciona a un momento inesperado en GTA 6',
    'what_key': 'reaccion', 'dominant_reaction': 'risas',
    'risas': 40, 'hype': 5, 'viral_multiple': 8.0,
    'payoff_ts_clip': 20.0, 'clip_duration_s': 30,
    'unique_users_peak_30s': 25, 'chat_msgs_peak_10s': 30,
    'quote_chat': {'text': 'JAJAJAJA qué acaba de pasar'},
    'quote_streamer': {'text': 'No me lo puedo creer'},
    'chat_top': [{'text': 'jajaja'}, {'text': 'qué acaba de pasar'}],
    'language': 'es', 'vod_id': 'v1',
}


def _package(**overrides):
    card = dict(CARD)
    card.update(overrides.pop("card", {}))
    return package_clip(1, 100, 90.0, 40, 5, 10.0, [90.0] * 10, lang='es',
                        examples=['JAJAJA'], moment_card=card, game='GTA 6',
                        streamer='illojuan', vod_id='v1',
                        visible='gameplay', **overrides)


class TestMetadata75(unittest.TestCase):
    def test_paquete_alcanza_seo_90(self):
        pkg = _package()
        scores = score_package_seo(pkg)
        for platform, result in scores.items():
            self.assertGreaterEqual(
                result["score"], 90, f"{platform}: {result}")

    def test_titulo_de_largo_ideal(self):
        pkg = _package()
        self.assertTrue(40 <= len(pkg["best_title"]) <= 55,
                        pkg["best_title"])
        self.assertLessEqual(len(pkg["best_title"]), 60)

    def test_descripcion_con_credito_y_cta(self):
        pkg = _package()
        desc = pkg["description_first_2_lines"]
        self.assertIn("illojuan", desc)
        self.assertIn("Clip de illojuan", desc)
        self.assertIn("Suscríbete", desc)
        self.assertIn("¿Te lo esperabas?", desc)

    def test_sin_credito_si_se_apaga_en_config(self):
        pkg = _package(credit_streamer=False)
        self.assertNotIn("Clip de illojuan",
                         pkg["description_first_2_lines"])

    def test_sin_streamer_no_hay_credito(self):
        card = dict(CARD)
        card["streamer"] = "desconocido"
        pkg = package_clip(1, 100, 90.0, 40, 5, 10.0, [90.0] * 10,
                           lang='es', examples=['JAJAJA'],
                           moment_card=card, game='GTA 6',
                           streamer=None, vod_id='v1',
                           visible='gameplay')
        self.assertNotIn("Clip de ",
                         pkg["description_first_2_lines"])

    def test_miniatura_no_repite_el_titulo(self):
        pkg = _package()
        from clipagent.seo_score import _words
        thumb_words = set(_words(pkg["thumbnail_text"]))
        title_words = set(_words(pkg["best_title"]))
        self.assertFalse(thumb_words & title_words,
                         f"thumb {pkg['thumbnail_text']!r} repite "
                         f"título {pkg['best_title']!r}")

    def test_longitudes_validadas(self):
        pkg = _package()
        self.assertEqual(pkg["length_issues"], [])
        self.assertLessEqual(len(pkg["best_title"]), 60)
        self.assertLessEqual(len(pkg["description_first_2_lines"]), 1000)
        self.assertLessEqual(len(pkg["hook_first_line"]), 80)
        self.assertLessEqual(len(pkg["tags"]), 15)
        for platform, plat in pkg["platforms"].items():
            self.assertLessEqual(len(plat["hashtags"]), 15, platform)

    def test_validate_reporta_descripcion_larga(self):
        # El validador solo reporta; el recorte (si hace falta) ya lo
        # hizo package_clip antes de construir los paquetes.
        pkg = {"best_title": "t", "description_first_2_lines": "x" * 1500,
               "hook_first_line": "h", "hashtags": [], "tags": []}
        issues = validate_metadata_lengths(pkg)
        self.assertTrue(any("descripción" in i for i in issues))
        self.assertEqual(len(pkg["description_first_2_lines"]), 1500)

    def test_titulos_unicos_entre_clips(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            seen_path = os.path.join(tmp, "titles_seen.json")
            pkg1 = _package(seen_path=seen_path, register=True)
            pkg2 = _package(seen_path=seen_path, register=True)
            self.assertNotEqual(pkg1["best_title"], pkg2["best_title"])

    def test_juego_y_streamer_del_vod_no_hardcodeados(self):
        card = dict(CARD)
        card["streamer"] = "coker"
        card["game"] = "GTA V RP"
        card["game_label"] = "GTA V RP"
        card["transcript_text"] = "esto es GTA V RP, no GTA 6"
        card["what"] = "el streamer reacciona en GTA V RP"
        pkg = package_clip(1, 100, 90.0, 40, 5, 10.0, [90.0] * 10,
                           lang='es', examples=['JAJAJA'],
                           moment_card=card, game='GTA V RP',
                           streamer='coker', vod_id='v1',
                           visible='gameplay')
        self.assertIn("coker", pkg["description_first_2_lines"])
        self.assertNotIn("GTA 6", pkg["best_title"])
        self.assertEqual(pkg["game"], "GTA V RP")


if __name__ == "__main__":
    unittest.main(verbosity=2)
