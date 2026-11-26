#!/usr/bin/env python3
"""Tests del linter de caption como puerta (adopción 2 de 5,
2026-11-26; COMPARACION.md §2).

Port de los 9 chequeos de ig-caption/caption.py (el docstring
original dice «eight» pero analyse() registra 9) a nuestra tabla
por plataforma, + la puerta en package_clip (FIX = retención
R3.4, REVIEW = sigue con avisos, READY = sigue normal) + la lista
de 15 hashtags genéricos como prohibidos + la regresión del tope
IG a 5 (metadata.py:2054 llevaba hashtags[:10]).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import llm
from clipagent.caption_lint import (
    FILLER_TAGS,
    HOLD_REASON_CAPTION_LINT,
    analyse_caption,
    keywords_for_game,
    lint_package,
)
from clipagent.metadata import package_for_platforms
from test_metadata_gemini import GEMINI_OK, FakeLLM, _package


def _check(result, name):
    for c in result["checks"]:
        if c["check"] == name:
            return c
    raise AssertionError(f"sin chequeo {name}: {result['checks']}")


def _status(result, name):
    return _check(result, name)["status"]


# Un caption IG válido de referencia: primera línea con juego +
# nombre, pregunta de cierre (1 ask), hashtags de contenido.
GOOD_IG = ("GTA 6: Rubius roba un avión. ¿Qué crees que pasó?\n\n"
           "#GTA6 #Rubius #Momento #Clips #Shorts")


class TestAnalyseCaptionIG(unittest.TestCase):
    def test_caption_bueno_ready(self):
        res = analyse_caption(GOOD_IG, keywords=["GTA 6"])
        self.assertEqual(res["verdict"], "READY")
        for c in res["checks"]:
            self.assertEqual(c["status"], "PASS", c)

    def test_length_fail_mas_de_2200(self):
        res = analyse_caption("GTA 6: Rubius " + "x" * 2200)
        self.assertEqual(_status(res, "LENGTH"), "FAIL")
        self.assertEqual(res["verdict"], "FIX")

    def test_length_pass_en_el_limite(self):
        text = "GTA 6: Rubius " + "x" * (2200 - len("GTA 6: Rubius "))
        self.assertEqual(len(text), 2200)
        res = analyse_caption(text)
        self.assertEqual(_status(res, "LENGTH"), "PASS")

    def test_first_line_en_blanco_fail(self):
        # El texto se hace strip() antes: solo un caption vacío
        # (o solo espacios) abre en blanco.
        self.assertEqual(_status(analyse_caption(""), "FIRST LINE"),
                         "FAIL")
        self.assertEqual(_status(analyse_caption("   \n  "),
                                 "FIRST LINE"), "FAIL")

    def test_first_line_hashtag_fail(self):
        res = analyse_caption("#GTA6 lo mejor del directo.")
        self.assertEqual(_status(res, "FIRST LINE"), "FAIL")

    def test_first_line_mencion_fail(self):
        res = analyse_caption("@rubius mira esto.")
        self.assertEqual(_status(res, "FIRST LINE"), "FAIL")

    def test_first_line_mas_de_125_warn(self):
        long_first = ("GTA 6: Rubius descubre cómo remolcar la moto "
                      "acuática mientras el chat se muere de risa y "
                      "el helicóptero pasa por encima del puente")
        res = analyse_caption(long_first + "\n\n#GTA6")
        self.assertEqual(_status(res, "FIRST LINE"), "WARN")
        self.assertEqual(res["verdict"], "REVIEW")

    def test_hook_sin_nada_concreto_warn(self):
        res = analyse_caption("mira esto, no vas a creer lo que pasó "
                              "en el directo de ayer")
        self.assertEqual(_status(res, "HOOK IS CONCRETE"), "WARN")

    def test_hook_con_numero_o_nombre_pass(self):
        res = analyse_caption("GTA 6: Rubius roba un avión.")
        self.assertEqual(_status(res, "HOOK IS CONCRETE"), "PASS")

    def test_hook_con_nombre_acentuado_pass(self):
        # CONCRETE_RE admite mayúscula acentuada (el original no);
        # el nombre va a mitad de línea (el (?<!^) del original
        # excluye la primera palabra de la línea).
        res = analyse_caption("En el directo, Álex descubre el salto.")
        self.assertEqual(_status(res, "HOOK IS CONCRETE"), "PASS")

    def test_hashtags_mas_de_5_fail(self):
        res = analyse_caption("GTA 6: Rubius roba un avión.\n\n"
                              "#GTA6 #Rubius #Momento #Clips #Shorts #Extra")
        self.assertEqual(_status(res, "HASHTAGS"), "FAIL")

    def test_hashtags_5_de_contenido_pass(self):
        res = analyse_caption(GOOD_IG)
        self.assertEqual(_status(res, "HASHTAGS"), "PASS")

    def test_tag_placement_en_ventana_warn(self):
        # Caption largo (>125) con el tag dentro de lo visible.
        res = analyse_caption("GTA 6: Rubius roba un avión comercial "
                              "#GTA6 en pleno directo "
                              + "y el chat no para de reírse " * 3)
        self.assertEqual(_status(res, "TAG PLACEMENT"), "WARN")

    def test_tag_placement_debajo_del_fold_pass(self):
        # Primera línea de >125 car.: los tags empiezan después
        # de la ventana visible (la FIRST LINE da WARN, pero el
        # chequeo de colocación es PASS).
        first = "GTA 6: Rubius " + "x" * 120
        self.assertGreater(len(first), 125)
        res = analyse_caption(first + "\n\n#GTA6 #Rubius")
        self.assertEqual(_status(res, "TAG PLACEMENT"), "PASS")

    def test_tag_placement_sin_fold_pass(self):
        # Caption corto: no hay «debajo del fold».
        res = analyse_caption("GTA 6: Rubius roba un avión #GTA6")
        self.assertEqual(_status(res, "TAG PLACEMENT"), "PASS")

    def test_links_warn(self):
        res = analyse_caption("GTA 6: Rubius roba un avión. "
                              "Míralo en https://example.com/clip")
        self.assertEqual(_status(res, "LINKS"), "WARN")

    def test_sin_links_pass(self):
        self.assertEqual(_status(analyse_caption(GOOD_IG), "LINKS"),
                         "PASS")

    def test_one_ask_cero_warn(self):
        res = analyse_caption("GTA 6: Rubius roba un avión comercial.")
        self.assertEqual(_status(res, "ONE ASK"), "WARN")

    def test_one_ask_uno_pass_es(self):
        res = analyse_caption("GTA 6: Rubius roba un avión. "
                              "¿Qué crees que acaba de pasar?")
        self.assertEqual(_status(res, "ONE ASK"), "PASS")

    def test_one_ask_uno_pass_en(self):
        res = analyse_caption("GTA 6: Rubius steals a plane. "
                              "What would you do?")
        self.assertEqual(_status(res, "ONE ASK"), "PASS")

    def test_one_ask_dos_warn(self):
        res = analyse_caption("GTA 6: Rubius roba un avión. "
                              "¿Qué opinas? Guarda este clip.")
        self.assertEqual(_status(res, "ONE ASK"), "WARN")

    def test_emoji_densidad_alta_warn(self):
        res = analyse_caption("😂😂😂😂😂 mira esto")
        self.assertEqual(_status(res, "EMOJI"), "WARN")

    def test_emoji_uno_en_texto_largo_pass(self):
        res = analyse_caption("GTA 6: Rubius roba un avión comercial "
                              "en pleno directo. 🎬 Clip de rubius.")
        self.assertEqual(_status(res, "EMOJI"), "PASS")

    def test_search_terms_todas_pass(self):
        res = analyse_caption(GOOD_IG, keywords=["GTA 6"])
        self.assertEqual(_status(res, "SEARCH TERMS"), "PASS")

    def test_search_terms_alguna_falta_warn(self):
        res = analyse_caption(GOOD_IG,
                              keywords=["GTA 6", "Fortnite"])
        self.assertEqual(_status(res, "SEARCH TERMS"), "WARN")

    def test_search_terms_ninguna_fail(self):
        res = analyse_caption(GOOD_IG, keywords=["Fortnite"])
        self.assertEqual(_status(res, "SEARCH TERMS"), "FAIL")

    def test_search_terms_forma_compacta_hashtag_pass(self):
        # «GTA 6» vale como «#GTA6»: en un clip solo-cams el juego
        # solo vive en los hashtags (el título y la descripción no
        # pueden nombrarlo, arreglo 3).
        res = analyse_caption(
            "El chat reacciona a lo que acaba de pasar #GTA6",
            keywords=[["GTA 6", "gta 6", "gta vi"]])
        self.assertEqual(_status(res, "SEARCH TERMS"), "PASS")

    def test_search_terms_sin_keywords_se_salta(self):
        res = analyse_caption(GOOD_IG)
        names = [c["check"] for c in res["checks"]]
        self.assertNotIn("SEARCH TERMS", names)
        self.assertEqual(len(res["checks"]), 8)


class TestFillerTagsProhibidos(unittest.TestCase):
    def test_los_15_son_fail(self):
        self.assertEqual(len(FILLER_TAGS), 15)
        for tag in sorted(FILLER_TAGS):
            res = analyse_caption(
                f"GTA 6: Rubius roba un avión.\n\n#GTA6 {tag}")
            self.assertEqual(_status(res, "HASHTAGS"), "FAIL", tag)
            self.assertEqual(res["verdict"], "FIX", tag)

    def test_hashtag_de_contenido_no_es_fail(self):
        res = analyse_caption("GTA 6: Rubius roba un avión.\n\n"
                              "#GTA6 #Rubius")
        self.assertEqual(_status(res, "HASHTAGS"), "PASS")

    def test_filler_aunque_no_pase_el_tope_es_fail(self):
        # El original daba WARN con genéricos sueltos; aquí es FAIL.
        res = analyse_caption("GTA 6: Rubius roba un avión.\n\n#viral")
        self.assertEqual(_status(res, "HASHTAGS"), "FAIL")


class TestPorPlataforma(unittest.TestCase):
    def test_youtube_15_hashtags_pass_16_fail(self):
        tags15 = " ".join(f"#Tag{i}" for i in range(15))
        res = analyse_caption(f"GTA 6: Rubius roba un avión.\n\n{tags15}",
                              platform="youtube_shorts")
        self.assertEqual(_status(res, "HASHTAGS"), "PASS")
        tags16 = " ".join(f"#Tag{i}" for i in range(16))
        res = analyse_caption(f"GTA 6: Rubius roba un avión.\n\n{tags16}",
                              platform="youtube_shorts")
        self.assertEqual(_status(res, "HASHTAGS"), "FAIL")

    def test_youtube_primera_linea_60_no_40(self):
        # Título real de 47 car.: con cut 40 daría WARN siempre.
        title = "GTA 6: Rubius reacciona a lo que acaba de pasar"
        self.assertEqual(len(title), 47)
        res = analyse_caption(title, platform="youtube_shorts")
        self.assertEqual(_status(res, "FIRST LINE"), "PASS")
        title61 = title + " ahora mismo!!"
        self.assertGreater(len(title61), 60)
        res = analyse_caption(title61, platform="youtube_shorts")
        self.assertEqual(_status(res, "FIRST LINE"), "WARN")

    def test_youtube_limite_5000(self):
        res = analyse_caption("GTA 6: Rubius " + "x" * 5000,
                              platform="youtube_shorts")
        self.assertEqual(_status(res, "LENGTH"), "FAIL")

    def test_tiktok_mas_de_150_fail(self):
        res = analyse_caption("GTA 6: Rubius " + "x" * 140,
                              platform="tiktok")
        self.assertEqual(_status(res, "LENGTH"), "FAIL")

    def test_tiktok_6_hashtags_fail(self):
        res = analyse_caption("GTA 6: Rubius #GTA6 #A #B #C #D #E",
                              platform="tiktok")
        self.assertEqual(_status(res, "HASHTAGS"), "FAIL")

    def test_tiktok_tags_en_ventana_no_es_warn(self):
        # El caption entero cabe: sin fold, TAG PLACEMENT es PASS.
        res = analyse_caption("GTA 6: Rubius roba un avión "
                              "#GTA6 #Rubius", platform="tiktok")
        self.assertEqual(_status(res, "TAG PLACEMENT"), "PASS")

    def test_facebook_misma_ventana_que_ig(self):
        long_first = ("GTA 6: Rubius descubre cómo remolcar la moto "
                      "acuática mientras el chat se muere de risa y "
                      "el helicóptero pasa por encima del puente")
        res = analyse_caption(long_first, platform="facebook_reels")
        self.assertEqual(_status(res, "FIRST LINE"), "WARN")


class TestKeywordsForGame(unittest.TestCase):
    def test_gta_vi_y_gta_6_son_el_mismo_termino(self):
        groups = keywords_for_game("GTA VI")
        self.assertEqual(len(groups), 1)
        self.assertIn("GTA 6", groups[0])
        res = analyse_caption("GTA VI: Rubius roba un avión.",
                              keywords=groups)
        self.assertEqual(_status(res, "SEARCH TERMS"), "PASS")
        res = analyse_caption("GTA 6: Rubius roba un avión.",
                              keywords=groups)
        self.assertEqual(_status(res, "SEARCH TERMS"), "PASS")

    def test_sin_juego_no_hay_keywords(self):
        self.assertEqual(keywords_for_game(None), [])
        self.assertEqual(keywords_for_game("desconocido"), [])
        self.assertEqual(keywords_for_game(""), [])


class TestLintPackage(unittest.TestCase):
    def _platforms(self):
        # Valores reales del fixture GEMINI_OK: título de 47 car.
        # y primera línea de 79, así el prefijo de FB (130) deja
        # los tags debajo del fold y el paquete puede dar READY.
        return package_for_platforms(
            "GTA 6: Rubius reacciona a lo que acaba de pasar",
            "Rubius reacciona a un momento inesperado en GTA 6. "
            "¿Qué crees que acaba de pasar?",
            ["GTA 6"], "REACCIÓN INESPERADA", lang="es",
            hashtags=["#GTA6", "#Rubius", "#Reaccion", "#Momento",
                      "#Shorts"],
            game="GTA VI")

    def test_paquete_real_sin_fails(self):
        res = lint_package(self._platforms(),
                           keywords=keywords_for_game("GTA VI"))
        self.assertEqual(res["fails"], [])
        self.assertIn(res["verdict"], ("READY", "REVIEW"))

    def test_global_fix_si_una_plataforma_falla(self):
        plats = self._platforms()
        plats["instagram_reels"]["caption"] = (
            "GTA 6: Rubius roba un avión.\n\n#viral")
        res = lint_package(plats, keywords=keywords_for_game("GTA VI"))
        self.assertEqual(res["verdict"], "FIX")
        self.assertTrue(any("instagram_reels" in f for f in res["fails"]))

    def test_global_ready_si_todo_pass(self):
        plats = self._platforms()
        # TikTok (título + hashtags) no lleva ask: se le pone la
        # pregunta en el título para el caso READY puro.
        plats["tiktok"]["caption"] = (
            "GTA 6: Rubius reacciona a lo que acaba de pasar, "
            "¿qué crees? #GTA6 #Rubius")
        res = lint_package(plats, keywords=keywords_for_game("GTA VI"))
        self.assertEqual(res["verdict"], "READY", res)

    def test_fails_y_warns_listados(self):
        plats = self._platforms()
        plats["tiktok"]["caption"] = "GTA 6: Rubius roba un avión #GTA6"
        res = lint_package(plats, keywords=keywords_for_game("GTA VI"))
        self.assertEqual(res["verdict"], "REVIEW")
        self.assertEqual(res["fails"], [])
        self.assertTrue(any("tiktok: ONE ASK" in w for w in res["warns"]))


class TestPuertaPackageClip(unittest.TestCase):
    def test_paquete_real_sigue_con_aviso_registrado(self):
        pkg = _package(FakeLLM(GEMINI_OK))
        self.assertEqual(pkg["pkg_source"], "gemini")
        self.assertIsNotNone(pkg["best_title"])
        self.assertFalse(pkg["needs_human_review"])
        lint = pkg.get("caption_lint")
        self.assertIsNotNone(lint)
        self.assertIn(lint["verdict"], ("READY", "REVIEW"))
        self.assertEqual(lint["fails"], [])
        # El aviso típico: el caption de TikTok no lleva ask.
        self.assertIn("tiktok", lint["platforms"])

    def test_fix_retiene_con_hold_reason_propio(self):
        # Un hashtag prohibido colado en el caption de IG (el
        # humanizador ya borra los del campo hashtags, así que el
        # FAIL se simula sobre el caption construido): FIX ->
        # retención R3.4 con el motivo propio del lint.
        from clipagent.metadata import _apply_caption_lint

        pkg = _package(FakeLLM(GEMINI_OK))
        self.assertEqual(pkg["pkg_source"], "gemini")
        pkg["platforms"]["instagram_reels"]["caption"] += " #viral"
        _apply_caption_lint(pkg, "GTA VI")
        self.assertEqual(pkg["pkg_source"], "retained")
        self.assertIsNone(pkg["best_title"])
        self.assertTrue(pkg["needs_human_review"])
        self.assertEqual(pkg["hold_reason"], HOLD_REASON_CAPTION_LINT)
        lint = pkg["caption_lint"]
        self.assertEqual(lint["verdict"], "FIX")
        self.assertTrue(any("HASHTAGS" in f for f in lint["fails"]))
        # Las plataformas se reconstruyen sin título (nada que
        # publicar con el título retenido).
        self.assertIsNone(
            pkg["platforms"]["youtube_shorts"]["title"])

    def test_fix_sobre_paquete_retenido_no_tapa_el_motivo(self):
        # Sin best_title (ya retenido por otro motivo), la puerta
        # no linta: SKIPPED y el hold_reason original intacto.
        from clipagent.metadata import _apply_caption_lint

        pkg = _package(FakeLLM(GEMINI_OK))
        pkg["best_title"] = None
        pkg["hold_reason"] = "otro motivo"
        _apply_caption_lint(pkg, "GTA VI")
        self.assertEqual(pkg["caption_lint"]["verdict"], "SKIPPED")
        self.assertEqual(pkg["hold_reason"], "otro motivo")

    def test_review_sigue_y_lleva_los_avisos(self):
        pkg = _package(FakeLLM(GEMINI_OK))
        lint = pkg["caption_lint"]
        if lint["verdict"] == "REVIEW":
            self.assertIsNotNone(pkg["best_title"])
            self.assertTrue(lint["warns"])
            self.assertFalse(pkg["needs_human_review"])

    def test_paquete_solo_cams_sigue_sin_juego_en_titulo(self):
        # Regresión (falso positivo encontrado 2026-11-26): el clip
        # solo-cams lleva el juego SOLO en los hashtags (#GTA6);
        # el título y la descripción no pueden nombrarlo (arreglo
        # 3). La puerta no lo retiene.
        reaction = {
            "title": "El chat reacciona a lo que acaba de pasar",
            "description": "Rubius reacciona a un momento inesperado. "
                           "¿Qué crees que acaba de pasar?",
            "hashtags": ["#GTA6", "#Clips", "#Directo", "#StreamClips"],
            "thumbnail_text": "REACCIÓN INESPERADA",
        }
        pkg = _package(FakeLLM(reaction), visible="cams-only")
        self.assertEqual(pkg["pkg_source"], "gemini")
        self.assertIsNotNone(pkg["best_title"])
        self.assertFalse(pkg["needs_human_review"])
        self.assertEqual(pkg["caption_lint"]["fails"], [])

    def test_sin_gemini_skipped_sin_tapar_el_motivo(self):
        class SilentLLM(llm.LLMClient):
            def available(self):
                return True

            def generate(self, prompt):
                return ""

        pkg = _package(SilentLLM())
        self.assertIsNone(pkg["best_title"])
        self.assertTrue(pkg["needs_human_review"])
        self.assertEqual(pkg["caption_lint"]["verdict"], "SKIPPED")
        self.assertNotEqual(pkg["hold_reason"],
                            HOLD_REASON_CAPTION_LINT)


class TestRegresionTopeIG(unittest.TestCase):
    def test_campo_hashtags_ig_igual_que_el_caption_5(self):
        out = package_for_platforms(
            "GTA 6: Rubius roba un avión",
            "GTA 6: Rubius roba un avión comercial.\nClip completo.",
            ["GTA 6"], "¡ESTAMOS VOLANDO!", lang="es",
            hashtags=["#GTA6", "#gta6", "#GTA", "#RockstarGames",
                      "#GTA6Clips", "#gaming", "#clips"],
            game="GTA VI")
        ig = out["instagram_reels"]
        self.assertEqual(len(ig["hashtags"]), 5)
        caption_tags = [w for w in ig["caption"].split() if w.startswith("#")]
        self.assertEqual(ig["hashtags"], caption_tags)
        self.assertLessEqual(ig["caption"].count("#"), 5)


if __name__ == "__main__":
    unittest.main()
