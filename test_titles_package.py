#!/usr/bin/env python3
"""Tests §8 grupo 9 (spec títulos-verdad 2026-11-25) + M5/M7.

Paquete completo: descripción sin la CTA fija y con cierre
generado por clip (pregunta/CTA del momento); hashtags CONTRA EL
MOMENTO (cada tag de contenido nombra un elemento del
momento/transcripción, no el conjunto fijo del juego) + #Shorts
quinto; miniatura con sentido (mín. 2 palabras de contenido, M4);
hook no fijo; onscreen/loop eliminados del paquete (M6).
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import llm
from clipagent.facebook_cut import build_facebook_package
from clipagent.youtube_cut import build_youtube_package

FIXED_CTA_ES = "¿Te lo esperabas? Suscríbete para más clips."
FIXED_CTA_EN = "Did you see that coming? Subscribe for more clips."
FIXED_HOOK_ES = "Mira por qué el chat se murió de risa acá."
FIXED_HOOK_EN = "Watch why chat lost it here."
TRANSCRIPT = ("vamos a seguir con la misión principal, había que "
              "hacerlo de Brian para saltar el banco, eso va a "
              "estar guapo")

CARD = {"game": "GTA 6", "game_label": "GTA 6", "streamer": "rubius",
        "dominant_reaction": "risas", "risas": 40,
        "chat_msgs_peak_10s": 30,
        "what": "la misión principal de Brian para saltar el banco",
        "transcript_text": TRANSCRIPT,
        "quote_chat": {"text": "JAJAJAJA", "count": 5},
        "quote_streamer": {"text": "eso va a estar guapo"}}

MOMENT_CARD = {
    "game": "GTA 6", "game_label": "GTA 6", "streamer": "rubius",
    "language": "es", "transcript_text": TRANSCRIPT,
    "risas": 11, "hype": 1, "dominant_reaction": "risas",
    "what": "la misión principal de Brian para saltar el banco",
    "what_key": "misión",
    "quote_streamer": {"text": "eso va a estar guapo"},
    "quote_chat": None, "chat_top": [],
    "evidence": "medium", "evidence_notes": [],
    "evidence_tokens": ["mision", "principal", "brian", "saltar",
                        "banco", "guapo", "rubius", "gta"],
    "payoff_ts_clip": 20.0, "clip_duration_s": 40,
    "chat_msgs_peak_10s": 30, "unique_users_peak_30s": 10,
    "viral_multiple": 8.0,
}

VERIFIED_TITLE = "GTA 6: La misión de Brian: saltar el banco"
CLICHE_TITLE = "GTA 6: La risa imparable de Rubius"

STOPWORDS = {"en", "el", "la", "de", "los", "las", "un", "una",
             "con", "por", "para", "a", "al", "y", "o", "es"}
MEDIUM_WORDS = {"chat", "directo", "stream", "clip", "vídeo",
                "video", "momento"}


def content_words(text):
    """Palabras de contenido (M4): fuera artículos, preposiciones,
    pronombres y palabras de medio/chat."""
    return [w for w in (text or "").lower().split()
            if w not in STOPWORDS and w not in MEDIUM_WORDS]


def _gemini_pkg(lang):
    if lang == "en":
        return {
            "best_title": "GTA 6: Brian's mission: robbing the bank",
            "description": ("Rubius follows the main mission: "
                            "Brian's job to rob the bank. Do you "
                            "think the heist goes well?"),
            "description_first_2_lines": (
                "Rubius follows the main mission: Brian's job to "
                "rob the bank. Do you think the heist goes well?"),
            "tags": ["GTA 6", "rubius", "gameplay"],
            "hashtags": ["#GTA6", "#Rubius", "#Heist",
                         "#BrianMission"],
            "thumbnail_text": "Brian's heist",
            "pkg_source": "gemini", "needs_human_review": False}
    return {
        "best_title": VERIFIED_TITLE,
        "description": ("Rubius sigue la misión principal: el "
                        "trabajo de Brian para saltar el banco. "
                        "¿Crees que el atraco sale bien?"),
        "description_first_2_lines": (
            "Rubius sigue la misión principal: el trabajo de "
            "Brian para saltar el banco. ¿Crees que el atraco "
            "sale bien?"),
        "tags": ["GTA 6", "rubius", "gameplay"],
        "hashtags": ["#GTA6", "#Rubius", "#Atraco", "#MisionBrian"],
        "thumbnail_text": "El atraco de Brian",
        "pkg_source": "gemini", "needs_human_review": False}


class _FakeLLM(llm.LLMClient):
    def __init__(self, response):
        self.response = response

    def available(self):
        return True

    def generate(self, prompt):
        return self.response


class TestSeoPackagePrompt(unittest.TestCase):
    def test_prompt_es_separa_chat_de_streamer_y_pide_momento(self):
        prompt = llm._seo_package_prompt(CARD, lang="es")
        self.assertIn("NOMBRA el momento concreto", prompt)
        self.assertIn("Reacción del CHAT", prompt)
        self.assertIn("Reacción del STREAMER", prompt)
        self.assertIn("no le atribuyas al streamer", prompt.lower())
        self.assertIn("risa imparable", prompt)
        self.assertIn("pregunta o invitación ligada a ESTE momento",
                      prompt)
        self.assertNotIn(FIXED_CTA_ES, prompt)
        # Clip corto: transcripción entera, sin truncar a 500.
        self.assertIn(TRANSCRIPT, prompt)

    def test_prompt_en_mismo_endurecimiento(self):
        prompt = llm._seo_package_prompt(CARD, lang="en")
        self.assertIn("NAMES the concrete moment", prompt)
        self.assertIn("CHAT reaction", prompt)
        self.assertIn("STREAMER reaction", prompt)
        self.assertIn("couldn't stop laughing", prompt)
        self.assertIn("question or invitation tied to THIS concrete "
                      "moment", prompt)
        self.assertNotIn(FIXED_CTA_EN, prompt)

    def test_transcript_largo_recorta_al_payoff_no_al_inicio(self):
        long_text = ("relleno sin sentido " * 200
                     + "PAYOFF saltar el banco "
                     + "más relleno sin sentido " * 200)
        card = dict(CARD)
        card["transcript_text"] = long_text
        card["transcript_segments_timed"] = [
            {"start": 0.0, "end": 5.0, "text": "relleno sin sentido"},
            {"start": 18.0, "end": 23.0,
             "text": "PAYOFF saltar el banco"},
            {"start": 35.0, "end": 40.0, "text": "más relleno"}]
        card["payoff_ts_clip"] = 20.0
        prompt = llm._seo_package_prompt(card, lang="es")
        self.assertIn("PAYOFF saltar el banco", prompt)
        self.assertLess(len(prompt), len(long_text))

    def test_make_seo_package_preserva_cierre_del_momento(self):
        payload = json.dumps({
            "title": VERIFIED_TITLE,
            "description": ("Rubius sigue la misión principal: el "
                            "trabajo de Brian para saltar el banco. "
                            "¿Crees que el atraco sale bien?"),
            "hashtags": ["#GTA6", "#Rubius", "#Atraco", "#MisionBrian",
                         "#Shorts"],
            "thumbnail_text": "El atraco de Brian"})
        out = llm.make_seo_package(_FakeLLM(payload))(CARD)
        self.assertIsNotNone(out)
        self.assertNotIn(FIXED_CTA_ES, out["description"])
        self.assertIn("¿Crees que el atraco sale bien?",
                      out["description"])
        self.assertEqual(out["hashtags"][-1], "#Shorts")
        self.assertEqual(len(out["hashtags"]), 5)
        self.assertGreaterEqual(
            len(content_words(out["thumbnail_text"])), 2)


class TestCleanTitleQuote(unittest.TestCase):
    def test_titulo_191_conserva_comilla_de_cierre(self):
        # El título del 191 (spec §7.3) termina en la comilla que
        # cierra la cita del streamer: _clean_title no debe
        # comérsela (el .strip("\"'") anterior sí lo hacía).
        title = "GTA 6: Burn asks: 'What was that moan, bro?'"
        self.assertEqual(llm._clean_title(title), title)

    def test_comillas_que_envuelven_todo_el_titulo_si_se_quitan(self):
        self.assertEqual(
            llm._clean_title('"GTA 6: El atraco de Brian"'),
            "GTA 6: El atraco de Brian")
        self.assertEqual(
            llm._clean_title("'GTA 6: El atraco de Brian'"),
            "GTA 6: El atraco de Brian")

    def test_comilla_de_apertura_sin_cierre_no_se_toca(self):
        title = "GTA 6: Burn asks: 'What was that moan, bro?"
        self.assertEqual(llm._clean_title(title), title)


class TestContentWordsRule(unittest.TestCase):
    def test_miniatura_con_sentido_pasa(self):
        self.assertGreaterEqual(
            len(content_words("El atraco de Brian")), 2)
        self.assertGreaterEqual(
            len(content_words("Motor de la moto")), 2)

    def test_fragmento_sin_sentido_no_pasa(self):
        # «om en el chat» (thumb del 185): menos de 2 palabras de
        # contenido.
        self.assertLess(len(content_words("om en el chat")), 2)


class TestVodBuilders(unittest.TestCase):
    def _mock_package_clip(self, held=False):
        def fake(**kw):
            if held:
                return {"best_title": None,
                        "needs_human_review": True,
                        "pkg_source": "retained"}
            return _gemini_pkg(kw.get("lang") or "es")
        return fake

    def test_youtube_paquete_completo_sin_fijos(self):
        with mock.patch("clipagent.metadata.package_clip",
                        side_effect=self._mock_package_clip()), \
                mock.patch("auto_clips.suggest_titles",
                           side_effect=AssertionError(
                               "suggest_titles no debe llamarse")):
            pkg = build_youtube_package(
                "Rubius juega GTA 6", "desc del vídeo", TRANSCRIPT,
                "rubius", "es", 11, 1, 3.31)
        viral = pkg["viral"]
        self.assertEqual(viral["best_title"], VERIFIED_TITLE)
        self.assertTrue(pkg["title_es"])
        self.assertTrue(pkg["title_en"])
        self.assertNotIn(FIXED_CTA_ES, viral["description"])
        self.assertIn("¿Crees que el atraco sale bien?",
                      viral["description"])
        # Hashtags contra el MOMENTO + #Shorts quinto.
        self.assertEqual(viral["hashtags"][-1], "#Shorts")
        self.assertEqual(len(viral["hashtags"]), 5)
        self.assertIn("#Atraco", viral["hashtags"])
        self.assertIn("#MisionBrian", viral["hashtags"])
        # Miniatura con sentido (M4) y sin onscreen/loop (M6).
        self.assertGreaterEqual(
            len(content_words(viral["thumbnail_text"])), 2)
        self.assertNotIn("onscreen_first_2s", viral)
        self.assertNotIn("loop_point", viral)
        self.assertNotIn(FIXED_HOOK_ES,
                         " ".join(str(v) for v in viral.values()))
        self.assertEqual(pkg["moment_card"]["game"], "GTA VI")
        self.assertEqual(
            set(pkg["seo_scores"]),
            {"youtube_shorts", "tiktok", "instagram_reels",
             "facebook_reels"})

    def test_youtube_retenido_sin_titulo_honesto(self):
        with mock.patch("clipagent.metadata.package_clip",
                        side_effect=self._mock_package_clip(held=True)):
            pkg = build_youtube_package(
                "Rubius juega GTA 6", "desc", TRANSCRIPT,
                "rubius", "es", 11, 1, 3.31)
        self.assertIsNone(pkg["viral"]["best_title"])
        self.assertTrue(pkg["viral"]["needs_human_review"])
        self.assertEqual(pkg["title_es"], "")
        self.assertEqual(pkg["title_en"], "")

    def test_youtube_sin_transcripcion_retenido_real(self):
        # Sin mock: el camino real retiene sin transcripción.
        pkg = build_youtube_package("t", "d", "", "rubius", "es",
                                    0, 0, 1.0)
        self.assertIsNone(pkg["viral"]["best_title"])
        self.assertTrue(pkg["viral"]["needs_human_review"])

    def test_facebook_sin_verificar_no_hay_game(self):
        def fake(**kw):
            if (kw.get("game") or None) is None:
                return {"best_title": "Noni y el avión sin "
                                     "combustible",
                        "description": "Noni y el avión sin "
                                       "combustible. ¿Lo viste?",
                        "description_first_2_lines":
                            "Noni y el avión sin combustible. "
                            "¿Lo viste?",
                        "tags": [], "hashtags": ["#Noni", "#Avion"],
                        "thumbnail_text": "Avión sin combustible",
                        "pkg_source": "gemini",
                        "needs_human_review": False}
            return _gemini_pkg(kw.get("lang") or "es")
        with mock.patch("clipagent.metadata.package_clip",
                        side_effect=fake):
            pkg = build_facebook_package(
                "#noni", "hablando normal del juego", "noni", "es",
                0, 0, 1.0, False)
        self.assertNotIn("game", pkg["viral"])
        self.assertNotIn("game", pkg["moment_card"])
        self.assertNotIn("GTA", pkg["viral"]["best_title"])
        self.assertEqual(pkg["viral"]["hashtags"][-1], "#Shorts")

    def test_facebook_verificado_lleva_game_y_shorts(self):
        with mock.patch("clipagent.metadata.package_clip",
                        side_effect=self._mock_package_clip()):
            pkg = build_facebook_package(
                "#gta6 #noni", TRANSCRIPT, "noni", "es", 2, 3, 5.62,
                True)
        self.assertEqual(pkg["viral"]["game"], "GTA VI")
        self.assertEqual(pkg["moment_card"]["game"], "GTA VI")
        self.assertEqual(pkg["viral"]["hashtags"][-1], "#Shorts")
        self.assertNotIn("onscreen_first_2s", pkg["viral"])
        self.assertNotIn("loop_point", pkg["viral"])


class TestMakeSeoVariantChecks(unittest.TestCase):
    def _variant(self, title, moment=None, recent=None):
        variant = llm.make_seo_variant(
            _FakeLLM(title), lang="es", moment_card=moment,
            recent_titles=recent)
        pkg = {"title": "GTA 6: Título viejo del clip",
               "description": "Rubius sigue la misión principal.",
               "tags": [], "thumbnail_text": "El atraco de Brian",
               "hashtags": ["#GTA6"], "lang": "es"}
        return variant(pkg, "title")

    def test_variante_buena_pasa_checks(self):
        out = self._variant(VERIFIED_TITLE, moment=MOMENT_CARD,
                            recent=[{"title": "GTA 6: Otro momento",
                                     "language": "es",
                                     "created_at": "2026-11-25"}])
        self.assertIsNotNone(out)
        self.assertEqual(out["title"], VERIFIED_TITLE)

    def test_variante_duplicada_se_rechaza(self):
        out = self._variant(
            VERIFIED_TITLE, moment=MOMENT_CARD,
            recent=[{"title": VERIFIED_TITLE, "language": "es",
                     "created_at": "2026-11-26"}])
        self.assertIsNone(out)

    def test_variante_cliche_sin_evidencia_se_rechaza(self):
        out = self._variant(CLICHE_TITLE, moment=MOMENT_CARD,
                            recent=[])
        self.assertIsNone(out)

    def test_variante_sin_contexto_mantiene_conducta(self):
        out = self._variant(VERIFIED_TITLE)
        self.assertIsNotNone(out)
        self.assertEqual(out["title"], VERIFIED_TITLE)

    def test_prompts_de_variante_por_idioma_con_cliches(self):
        seen = {}

        class Spy(llm.LLMClient):
            def available(self):
                return True

            def generate(self, prompt):
                seen.setdefault("prompts", []).append(prompt)
                return "x"

        pkg = {"title": "t", "description": "d", "lang": "en"}
        variant = llm.make_seo_variant(Spy(), lang="en")
        variant(pkg, "title")
        prompt = seen["prompts"][-1]
        self.assertIn("English", prompt)
        self.assertIn("couldn't stop laughing", prompt)
        self.assertIn("concrete moment", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
