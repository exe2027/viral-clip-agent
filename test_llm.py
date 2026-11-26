#!/usr/bin/env python3
"""Pruebas de 3.4g: interfaz LLMClient con proveedor intercambiable
y fallback al scoring actual cuando no hay clave.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import llm


class TestLLMClient(unittest.TestCase):
    def test_null_devuelve_vacio(self):
        self.assertEqual(llm.NullLLMClient().generate("hola"), "")

    def test_gemini_sin_clave_devuelve_vacio(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            self.assertEqual(llm.GeminiLLMClient().generate("hola"), "")

    def test_gemini_con_respuesta_falsa(self):
        def fake(method, url, headers, body):
            return 200, {"candidates": [{"content": {"parts": [
                {"text": "[{\"start\": 10}]"}]}}]}
        client = llm.GeminiLLMClient(api_key="k", request_fn=fake)
        self.assertEqual(client.generate("elige"), "[{\"start\": 10}]")

    def test_gemini_error_http_devuelve_vacio(self):
        def fake(method, url, headers, body):
            return 500, {"error": {"message": "caído"}}
        client = llm.GeminiLLMClient(api_key="k", request_fn=fake)
        self.assertEqual(client.generate("elige"), "")

    def test_get_llm_client_segun_env(self):
        self.assertIsInstance(llm.get_llm_client("none"),
                              llm.NullLLMClient)
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}):
            self.assertIsInstance(llm.get_llm_client("gemini"),
                                  llm.GeminiLLMClient)
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GEMINI_API_KEY", None)
            self.assertIsInstance(llm.get_llm_client(),
                                  llm.NullLLMClient)


class TestMakeSeoPackage(unittest.TestCase):
    """Primer paquete SEO con el LLM (título, descripción, hashtags,
    miniatura) desde la ficha del momento."""

    CARD = {"game": "GTA 6", "game_label": "GTA 6", "streamer": "rubius",
            "dominant_reaction": "risas", "risas": 40,
            "chat_msgs_peak_10s": 30,
            "what": "el streamer reacciona a un momento inesperado",
            "transcript_text": "No me lo puedo creer, esto es increíble",
            "quote_chat": {"text": "JAJAJAJA", "count": 5},
            "quote_streamer": {"text": "No me lo puedo creer"}}

    def _fake(self, response):
        class Fake(llm.LLMClient):
            def available(self):
                return True

            def generate(self, prompt):
                return response

        return Fake()

    def test_paquete_json_valido(self):
        import json
        payload = json.dumps({
            "title": "GTA 6: Rubius y el momento que hizo reír al chat",
            "description": "Rubius reacciona a un momento inesperado "
                           "en GTA 6. El chat no paraba de reírse.",
            "hashtags": ["#GTA6", "#GTA6Clips", "#ElClipDeGTA6", "#GTA",
                         "#RockstarGames"],
            "thumbnail_text": "NO PUEDE SER"})
        out = llm.make_seo_package(self._fake(payload))(self.CARD)
        self.assertEqual(out["title"],
                         "GTA 6: Rubius y el momento que hizo reír al chat")
        self.assertIn("GTA 6", out["description"])
        self.assertEqual(out["hashtags"],
                         ["#GTA6", "#GTA6Clips", "#ElClipDeGTA6", "#GTA",
                          "#RockstarGames"])
        self.assertEqual(out["thumbnail_text"], "NO PUEDE SER")

    def test_json_entre_cercas_markdown(self):
        payload = ("```json\n{\"title\": \"GTA 6: El chat no daba crédito\", "
                   "\"description\": \"Momento en GTA 6.\", "
                   "\"hashtags\": [\"#GTA6\"], "
                   "\"thumbnail_text\": \"MIRA ESTO\"}\n```")
        out = llm.make_seo_package(self._fake(payload))(self.CARD)
        self.assertEqual(out["title"], "GTA 6: El chat no daba crédito")
        self.assertEqual(out["thumbnail_text"], "MIRA ESTO")

    def test_hashtags_sin_almohadilla_se_completan(self):
        import json
        payload = json.dumps({
            "title": "GTA 6: El chat no daba crédito",
            "description": "Momento en GTA 6.",
            "hashtags": ["GTA6", "Clips"],
            "thumbnail_text": "MIRA ESTO"})
        out = llm.make_seo_package(self._fake(payload))(self.CARD)
        self.assertEqual(out["hashtags"], ["#GTA6", "#Clips"])

    def test_miniatura_se_recorta_a_4_palabras(self):
        import json
        payload = json.dumps({
            "title": "GTA 6: El chat no daba crédito",
            "description": "Momento en GTA 6.",
            "hashtags": ["#GTA6"],
            "thumbnail_text": "NO TE LO VAS A CREER AMIGO"})
        out = llm.make_seo_package(self._fake(payload))(self.CARD)
        self.assertEqual(out["thumbnail_text"], "NO TE LO VAS")

    def test_json_invalido_devuelve_none(self):
        self.assertIsNone(
            llm.make_seo_package(self._fake("esto no es json"))(self.CARD))

    def test_respuesta_vacia_devuelve_none(self):
        self.assertIsNone(
            llm.make_seo_package(self._fake(""))(self.CARD))

    def test_sin_llm_devuelve_none(self):
        self.assertIsNone(
            llm.make_seo_package(llm.NullLLMClient())(self.CARD))

    def test_prompt_lleva_la_ficha_no_plantillas(self):
        seen = {}

        class Spy(llm.LLMClient):
            def available(self):
                return True

            def generate(self, prompt):
                seen["prompt"] = prompt
                return "{}"

        llm.make_seo_package(Spy())(self.CARD)
        self.assertIn("rubius", seen["prompt"])
        self.assertIn("GTA 6", seen["prompt"])
        self.assertIn("No me lo puedo creer", seen["prompt"])


class TestPatternPackPrompt(unittest.TestCase):
    """El pack alimenta el prompt como guía de estilo (spec v3
    §3.2); sin pack el prompt no cambia."""

    PACK = {"version": 2, "generated_at": "2026-11-26", "lang": "es",
            "sample_n": 6,
            "top_hook_types": [
                {"hook_type": "question", "template": "¿Viste {what}?"},
                {"hook_type": "superlative",
                 "template": "GTA 6: {what} que nadie se creyó"}],
            "top_hook_words": ["viste", "robo"],
            "median_caption_chars": 42, "hook_texts": []}

    def _spy(self):
        seen = {}

        class Spy(llm.LLMClient):
            def available(self):
                return True

            def generate(self, prompt):
                seen["prompt"] = prompt
                return "{}"

        return Spy(), seen

    def test_prompt_con_pack_lleva_guia_de_estilo(self):
        spy, seen = self._spy()
        llm.make_seo_package(
            spy, lang="es",
            pattern_pack=self.PACK)(TestMakeSeoPackage.CARD)
        self.assertIn("Guía de estilo", seen["prompt"])
        self.assertIn("¿Viste {what}?", seen["prompt"])
        self.assertIn("la ficha del momento manda", seen["prompt"])
        self.assertIn("no copies estos textos", seen["prompt"])

    def test_prompt_sin_pack_no_lleva_guia(self):
        spy, seen = self._spy()
        llm.make_seo_package(spy, lang="es")(TestMakeSeoPackage.CARD)
        self.assertNotIn("Guía de estilo", seen["prompt"])

    def test_prompt_en_con_pack(self):
        spy, seen = self._spy()
        pack_en = dict(self.PACK, lang="en",
                       top_hook_types=[
                           {"hook_type": "question",
                            "template": "Did you see {what}?"}])
        llm.make_seo_package(spy, lang="en",
                             pattern_pack=pack_en)(TestMakeSeoPackage.CARD)
        self.assertIn("Style guide", seen["prompt"])
        self.assertIn("Did you see {what}?", seen["prompt"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
