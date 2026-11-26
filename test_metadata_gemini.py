#!/usr/bin/env python3
"""Pruebas de Gemini primero (2026-11-18, actualizado 2026-11-26).

El primer paquete SEO (título, descripción, hashtags, miniatura)
lo escribe Gemini desde la ficha; si no está disponible o lo que
devuelve no valida, RETENCIÓN (enmienda aprobada 2026-11-26: el
fallback de reglas está eliminado — sin título Gemini válido no
hay título). El gate SEO >= 90 y retry_best no se tocan.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import llm
from clipagent.metadata import (package_clip, score_package_seo,
                                seo_inputs_from_package)

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
    "title": "GTA 6: Rubius reacciona a lo que acaba de pasar",
    "description": "Rubius reacciona a un momento inesperado en GTA 6. "
                   "¿Qué crees que acaba de pasar?",
    "hashtags": ["#GTA6", "#Rubius", "#Reaccion", "#Momento", "#Shorts"],
    # Miniatura del momento (regla endurecida 2026-11-18): nombra
    # el momento ("reacción inesperada"), no una exclamación
    # genérica como "NO PUEDE SER".
    "thumbnail_text": "REACCIÓN INESPERADA",
}


def _assert_retained(self, pkg):
    """Conducta nueva (enmienda 2026-11-26): sin título Gemini
    válido no hay título de reglas — retención para revisión."""
    self.assertEqual(pkg["pkg_source"], "retained")
    self.assertIsNone(pkg["best_title"])
    self.assertTrue(pkg["needs_human_review"])
    self.assertEqual(pkg["description"], "")
    self.assertEqual(pkg["description_first_2_lines"], "")
    self.assertEqual(pkg["hashtags"], [])
    self.assertEqual(pkg["thumbnail_text"], "")


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
        # La descripción sigue llevando crédito al streamer, y el
        # cierre es la pregunta generada por clip (la CTA fija ya
        # no existe, enmienda 2026-11-26).
        self.assertIn("Clip de rubius", pkg["description_first_2_lines"])
        self.assertIn("¿Qué crees que acaba de pasar?",
                      pkg["description_first_2_lines"])
        self.assertNotIn("Suscríbete", pkg["description_first_2_lines"])
        self.assertNotIn("¿Te lo esperabas?",
                         pkg["description_first_2_lines"])

    def test_gemini_seo_medido_en_el_paquete(self):
        pkg = _package(FakeLLM(GEMINI_OK))
        self.assertIn("gemini_seo", pkg)
        self.assertEqual(set(pkg["gemini_seo"]),
                         {"youtube_shorts", "tiktok", "instagram_reels",
                          "facebook_reels"})
        # gemini_package es el paquete de Gemini (más la marca de
        # dedupe que añade package_clip, no parte de la fixture).
        for field in ("title", "description", "hashtags",
                      "thumbnail_text"):
            self.assertEqual(pkg["gemini_package"][field],
                             GEMINI_OK[field])
        # El paquete Gemini de la fixture llega al gate en las 4.
        scores = score_package_seo(pkg)
        for platform, result in scores.items():
            self.assertGreaterEqual(result["score"], 90,
                                    f"{platform}: {result}")

    def test_gemini_caido_retencion(self):
        _assert_retained(self, _package(SilentLLM()))
        self.assertNotIn("gemini_seo", _package(SilentLLM()))

    def test_sin_llm_retencion(self):
        _assert_retained(self, _package(llm.NullLLMClient()))

    def test_respuesta_invalida_retencion(self):
        _assert_retained(self, _package(FakeLLM("esto no es json")))

    def test_titulo_vacio_retencion(self):
        bad = dict(GEMINI_OK, title="")
        _assert_retained(self, _package(FakeLLM(bad)))

    def test_titulo_sin_gta6_retencion(self):
        bad = dict(GEMINI_OK,
                   title="El momento que hizo reír a todo el chat")
        _assert_retained(self, _package(FakeLLM(bad)))

    def test_miniatura_generica_retencion(self):
        # "NO PUEDE SER" es una exclamación genérica (2026-11-18,
        # regla endurecida): Gemini no puede devolverla.
        bad = dict(GEMINI_OK, thumbnail_text="NO PUEDE SER")
        pkg = _package(FakeLLM(bad))
        _assert_retained(self, pkg)

    def test_miniatura_que_repite_el_titulo_retencion(self):
        # La miniatura repite palabras de contenido del título
        # ("reacciona a lo que acaba de pasar"): Gemini no puede
        # devolverla.
        bad = dict(GEMINI_OK,
                   thumbnail_text="REACCIONA A LO QUE ACABA DE PASAR")
        _assert_retained(self, _package(FakeLLM(bad)))

    def test_titulo_mentiroso_retencion(self):
        # "GTA 6" con un sustantivo sin evidencia en la ficha.
        bad = dict(GEMINI_OK, title="GTA 6: El penal que nadie se creyó")
        pkg = _package(FakeLLM(bad))
        _assert_retained(self, pkg)
        self.assertNotEqual(pkg["best_title"], bad["title"])

    def test_cams_only_titulo_de_reaccion_sin_juego(self):
        reaction = {
            "title": "El chat reacciona a lo que acaba de pasar",
            "description": "Rubius reacciona a un momento inesperado. "
                           "¿Qué crees que acaba de pasar?",
            "hashtags": ["#GTA6", "#Clips", "#Directo", "#StreamClips"],
            "thumbnail_text": "REACCIÓN INESPERADA",
        }
        pkg = _package(FakeLLM(reaction), visible="cams-only")
        self.assertEqual(pkg["pkg_source"], "gemini")
        self.assertEqual(pkg["best_title"], reaction["title"])
        self.assertNotIn("GTA", pkg["best_title"])

    def test_cams_only_titulo_con_juego_retencion(self):
        _assert_retained(self, _package(FakeLLM(GEMINI_OK),
                                        visible="cams-only"))

    def test_paquete_retenido_sigue_exponiendo_los_mismos_campos(self):
        pkg = _package(SilentLLM())
        for field in ("best_title", "title", "description",
                      "description_first_2_lines", "tags", "hashtags",
                      "thumbnail_text", "visible", "platforms",
                      "pkg_source"):
            self.assertIn(field, pkg, f"falta {field}")
        self.assertEqual(pkg["platforms"]["youtube_shorts"]["title"],
                         pkg["best_title"])


def _pack(hook_texts, lang="es"):
    """Pack v3 sintético (spec §3.2) con el corpus de la guardia."""
    return {"version": 2, "generated_at": "2026-11-26", "lang": lang,
            "sample_n": 6,
            "top_hook_types": [{"hook_type": "question",
                                "template": "¿Viste {what}?"}],
            "top_hook_words": ["viste"], "median_caption_chars": 42,
            "hook_texts": list(hook_texts)}


class CountingFakeLLM(FakeLLM):
    """FakeLLM que cuenta las llamadas a generate()."""

    def __init__(self, payloads):
        super().__init__(payloads[0] if len(payloads) == 1 else payloads)
        self.payloads = list(payloads)
        self.calls = 0

    def generate(self, prompt):
        self.calls += 1
        payload = self.payloads[min(self.calls - 1,
                                    len(self.payloads) - 1)]
        if isinstance(payload, str):
            return payload
        return json.dumps(payload)


class TestAnticopyGuard(unittest.TestCase):
    """Guardia anti-copia (spec v3 §3.3): dentro de
    _gemini_first_package, tras validate_title_final."""

    def test_guardia_rechaza_titulo_identico(self):
        pack = _pack([GEMINI_OK["title"]])
        pkg = _package(FakeLLM(GEMINI_OK), reel_patterns=pack)
        _assert_retained(self, pkg)

    def test_guardia_solo_un_reintento_sin_pack(self):
        # El título copia una hook_text en los dos intentos: el
        # reintento sin pack también se rechaza y el clip queda
        # retenido, con exactamente 2 llamadas a Gemini (no bucle).
        pack = _pack([GEMINI_OK["title"]])
        client = CountingFakeLLM([GEMINI_OK])
        pkg = _package(client, reel_patterns=pack)
        _assert_retained(self, pkg)
        self.assertEqual(client.calls, 2)

    def test_guardia_segundo_rechazo_retiene(self):
        # El reintento trae OTRO título que también copia una
        # hook_text distinta: también se rechaza y no hay tercer
        # intento.
        second = dict(GEMINI_OK,
                      title="GTA 6: ¿Viste lo que pasó en el directo?")
        pack = _pack([GEMINI_OK["title"], second["title"]])
        client = CountingFakeLLM([GEMINI_OK, second])
        pkg = _package(client, reel_patterns=pack)
        _assert_retained(self, pkg)
        self.assertEqual(client.calls, 2)

    def test_guardia_pasa_titulo_de_plantilla_y_momento(self):
        pack = _pack(["¿Viste lo que pasó en el directo?"])
        pkg = _package(FakeLLM(GEMINI_OK), reel_patterns=pack)
        self.assertEqual(pkg["pkg_source"], "gemini")
        self.assertEqual(pkg["best_title"], GEMINI_OK["title"])
        self.assertEqual(pkg["pattern_pack_id"], "v2:2026-11-26:es")
        self.assertEqual(pkg["title_pattern_types"], ["question"])
        inputs = seo_inputs_from_package(pkg)
        self.assertEqual(inputs["pattern_pack_id"], "v2:2026-11-26:es")
        self.assertEqual(inputs["title_pattern_types"], ["question"])

    def test_pack_de_otro_idioma_se_ignora(self):
        pack = _pack(["¿Viste lo que pasó en el directo?"], lang="en")
        pkg = _package(FakeLLM(GEMINI_OK), reel_patterns=pack)
        self.assertEqual(pkg["pkg_source"], "gemini")
        self.assertEqual(pkg["best_title"], GEMINI_OK["title"])
        self.assertIsNone(pkg["pattern_pack_id"])
        self.assertEqual(pkg["title_pattern_types"], [])

    def test_lang_both_con_pack_es_usa_el_pack(self):
        pack = _pack(["¿Viste lo que pasó en el directo?"])
        pkg = package_clip(1, 100, 90.0, 40, 5, 10.0, [90.0] * 10,
                           lang="both", examples=["JAJAJA"],
                           moment_card=dict(CARD), game="GTA 6",
                           streamer="rubius", vod_id="v1",
                           visible="gameplay", llm_client=FakeLLM(GEMINI_OK),
                           reel_patterns=pack)
        self.assertEqual(pkg["pkg_source"], "gemini")
        self.assertEqual(pkg["pattern_pack_id"], "v2:2026-11-26:es")

    def test_vod_pasa_el_pack(self):
        from clipagent.workers import vod_package_from_transcript
        pack = _pack(["¿Viste lo que pasó en el directo?"])
        viral, _card = vod_package_from_transcript(
            "No me lo puedo creer, esto es increíble, mira lo que "
            "acaba de pasar en GTA 6",
            streamer="rubius", lang="es", risas=40, hype=5,
            audio_multiple_value=8.0, game="GTA 6",
            llm_client=FakeLLM(GEMINI_OK), reel_patterns=pack)
        self.assertEqual(viral["best_title"], GEMINI_OK["title"])
        self.assertEqual(viral["pattern_pack_id"], "v2:2026-11-26:es")
        self.assertEqual(viral["title_pattern_types"], ["question"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
