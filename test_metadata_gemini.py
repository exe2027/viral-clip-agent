#!/usr/bin/env python3
"""Pruebas de Gemini primero (2026-11-18): el primer paquete SEO
(título, descripción, hashtags, miniatura) lo escribe Gemini desde
la ficha; si no está disponible o lo que devuelve no valida,
las reglas de siempre. El gate SEO >= 90 y retry_best no se tocan.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import llm
from clipagent.metadata import package_clip, score_package_seo

CARD = {
    'streamer': 'rubius', 'game': 'GTA 6', 'game_label': 'GTA 6',
    'game_source': 'categoría de Twitch', 'evidence': 'high',
    'evidence_notes': ['cita del streamer'], 'evidence_tokens': ['GTA 6'],
    'transcript_text': 'No me lo puedo creer, esto es increíble, mira lo '
                       'que acaba de pasar en GTA 6',
    'what': 'el streamer reacciona a un momento inesperado en GTA 6',
    'what_key': 'reaccion', 'dominant_reaction': 'risas',
    'risas': 40, 'hype': 5, 'viral_multiple': 8.0,
    'payoff_ts_clip': 20.0, 'clip_duration_s': 30,
    'unique_users_peak_30s': 25, 'chat_msgs_peak_10s': 30,
    'quote_chat': {'text': 'JAJAJAJA qué acaba de pasar', 'count': 5},
    'quote_streamer': {'text': 'No me lo puedo creer'},
    'chat_top': [{'text': 'jajaja'}, {'text': 'qué acaba de pasar'}],
    'language': 'es', 'vod_id': 'v1',
}

GEMINI_OK = {
    "title": "GTA 6: Rubius y el momento que hizo reír al chat",
    "description": "Rubius reacciona a un momento inesperado en GTA 6. "
                   "El chat no paraba de reírse en directo.",
    "hashtags": ["#GTA6", "#GTA6Clips", "#ElClipDeGTA6", "#GTA",
                 "#RockstarGames"],
    # Miniatura del momento (regla endurecida 2026-11-18): nombra
    # el momento ("reacción inesperada"), no una exclamación
    # genérica como "NO PUEDE SER".
    "thumbnail_text": "REACCIÓN INESPERADA",
}


class FakeLLM(llm.LLMClient):
    """LLM de pruebas: devuelve el paquete que se le pasa."""

    def __init__(self, payload):
        self.payload = payload

    def available(self):
        return True

    def generate(self, prompt):
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload)


class SilentLLM(llm.LLMClient):
    """LLM que no responde nada útil."""

    def available(self):
        return True

    def generate(self, prompt):
        return ""


def _package(client=None, **overrides):
    card = dict(CARD)
    card.update(overrides.pop("card", {}))
    return package_clip(1, 100, 90.0, 40, 5, 10.0, [90.0] * 10, lang='es',
                        examples=['JAJAJA'], moment_card=card, game='GTA 6',
                        streamer='rubius', vod_id='v1',
                        visible=overrides.pop("visible", 'gameplay'),
                        llm_client=client, **overrides)


class TestGeminiFirstPackage(unittest.TestCase):
    def test_gemini_escribe_el_primer_paquete(self):
        pkg = _package(FakeLLM(GEMINI_OK))
        self.assertEqual(pkg["pkg_source"], "gemini")
        self.assertEqual(pkg["best_title"], GEMINI_OK["title"])
        self.assertEqual(pkg["title"], GEMINI_OK["title"])
        self.assertIn("Rubius reacciona a un momento inesperado en GTA 6",
                      pkg["description_first_2_lines"])
        self.assertEqual(pkg["description"],
                         pkg["description_first_2_lines"])
        self.assertEqual(pkg["hashtags"], GEMINI_OK["hashtags"])
        self.assertEqual(pkg["thumbnail_text"], "REACCIÓN INESPERADA")
        # El título de Gemini queda primero en la lista de candidatos.
        self.assertEqual(pkg["titles"][0]["title"], GEMINI_OK["title"])
        # La descripción sigue llevando crédito y CTA (SEO).
        self.assertIn("Clip de rubius", pkg["description_first_2_lines"])
        self.assertIn("Suscríbete", pkg["description_first_2_lines"])

    def test_gemini_seo_medido_en_el_paquete(self):
        pkg = _package(FakeLLM(GEMINI_OK))
        self.assertIn("gemini_seo", pkg)
        self.assertEqual(set(pkg["gemini_seo"]),
                         {"youtube_shorts", "tiktok", "instagram_reels",
                          "facebook_reels"})
        self.assertEqual(pkg["gemini_package"], GEMINI_OK)
        # El paquete Gemini de la fixture llega al gate en las 4.
        scores = score_package_seo(pkg)
        for platform, result in scores.items():
            self.assertGreaterEqual(result["score"], 90,
                                    f"{platform}: {result}")

    def test_gemini_caido_fallback_a_reglas(self):
        pkg = _package(SilentLLM())
        self.assertEqual(pkg["pkg_source"], "rules")
        self.assertTrue(pkg["best_title"])
        self.assertTrue(pkg["best_title"].startswith("GTA 6:"))
        self.assertNotIn("gemini_seo", pkg)

    def test_sin_llm_fallback_a_reglas(self):
        pkg = _package(llm.NullLLMClient())
        self.assertEqual(pkg["pkg_source"], "rules")
        self.assertTrue(pkg["best_title"])

    def test_respuesta_invalida_fallback_a_reglas(self):
        pkg = _package(FakeLLM("esto no es json"))
        self.assertEqual(pkg["pkg_source"], "rules")
        self.assertTrue(pkg["best_title"])

    def test_titulo_vacio_fallback_a_reglas(self):
        bad = dict(GEMINI_OK, title="")
        pkg = _package(FakeLLM(bad))
        self.assertEqual(pkg["pkg_source"], "rules")
        self.assertTrue(pkg["best_title"])

    def test_titulo_sin_gta6_fallback_a_reglas(self):
        bad = dict(GEMINI_OK,
                   title="El momento que hizo reír a todo el chat")
        pkg = _package(FakeLLM(bad))
        self.assertEqual(pkg["pkg_source"], "rules")
        self.assertTrue(pkg["best_title"].startswith("GTA 6:"))

    def test_miniatura_generica_fallback_a_reglas(self):
        # "NO PUEDE SER" es una exclamación genérica (2026-11-18,
        # regla endurecida): Gemini no puede devolverla.
        bad = dict(GEMINI_OK, thumbnail_text="NO PUEDE SER")
        pkg = _package(FakeLLM(bad))
        self.assertEqual(pkg["pkg_source"], "rules")
        self.assertNotEqual(pkg["thumbnail_text"], "NO PUEDE SER")

    def test_miniatura_que_repite_el_titulo_fallback_a_reglas(self):
        bad = dict(GEMINI_OK, thumbnail_text="HIZO REÍR AL CHAT")
        pkg = _package(FakeLLM(bad))
        self.assertEqual(pkg["pkg_source"], "rules")
        self.assertNotEqual(pkg["thumbnail_text"], "HIZO REÍR AL CHAT")

    def test_titulo_mentiroso_fallback_a_reglas(self):
        # "GTA 6" con un sustantivo sin evidencia en la ficha.
        bad = dict(GEMINI_OK, title="GTA 6: El penal que nadie se creyó")
        pkg = _package(FakeLLM(bad))
        self.assertEqual(pkg["pkg_source"], "rules")
        self.assertNotEqual(pkg["best_title"], bad["title"])

    def test_cams_only_titulo_de_reaccion_sin_juego(self):
        reaction = {
            "title": "El chat no paraba de reírse",
            "description": "Rubius reacciona a un momento inesperado. "
                           "El chat no paraba de reírse en directo.",
            "hashtags": ["#GTA6", "#Clips", "#Directo", "#StreamClips"],
            "thumbnail_text": "REACCIÓN INESPERADA",
        }
        pkg = _package(FakeLLM(reaction), visible="cams-only")
        self.assertEqual(pkg["pkg_source"], "gemini")
        self.assertEqual(pkg["best_title"], reaction["title"])
        self.assertNotIn("GTA", pkg["best_title"])

    def test_cams_only_titulo_con_juego_fallback_a_reglas(self):
        pkg = _package(FakeLLM(GEMINI_OK), visible="cams-only")
        self.assertEqual(pkg["pkg_source"], "rules")
        self.assertNotIn("GTA", pkg["best_title"])

    def test_paquete_por_reglas_sigue_exponiendo_los_mismos_campos(self):
        pkg = _package(SilentLLM())
        for field in ("best_title", "title", "description",
                      "description_first_2_lines", "tags", "hashtags",
                      "thumbnail_text", "visible", "platforms",
                      "pkg_source"):
            self.assertIn(field, pkg, f"falta {field}")
        self.assertEqual(pkg["platforms"]["youtube_shorts"]["title"],
                         pkg["best_title"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
