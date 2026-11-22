"""Tests de hashtags a 5 + caption con frase buscable (2026-11-21).

Instagram capó los hashtags a 5 por post (18-dic-2025). La
descripción lleva la frase buscable como texto natural en los
primeros 125 caracteres (lo visible en el feed antes de
"...more"). Cubren clipagent/metadata.py (PLATFORMS,
dedupe_hashtags, caption_first_line, package_for_platforms) y
config.yaml (lista de hashtags por idioma).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.config import get_hashtags, load_config
from clipagent.metadata import (
    PLATFORMS,
    caption_first_line,
    dedupe_hashtags,
    package_for_platforms,
)


class TestHashtagCap(unittest.TestCase):
    def test_instagram_reels_max_5(self):
        self.assertEqual(PLATFORMS["instagram_reels"]["hashtags"], 5)

    def test_todas_las_plataformas_max_5(self):
        for key, rules in PLATFORMS.items():
            self.assertLessEqual(
                rules["hashtags"], 5,
                f"{key} permite {rules['hashtags']} hashtags")

    def test_dedupe_quita_duplicados_y_capa_a_5(self):
        tags = ["#GTA6", "#gta6", "#GTA", "#RockstarGames",
                "#GTA6Clips", "#gaming", "#clips"]
        out = dedupe_hashtags(tags)
        self.assertEqual(len(out), 5)
        self.assertEqual(len({t.lower() for t in out}), 5)
        self.assertNotIn("#gta6", out)  # duplicado de #GTA6
        self.assertEqual(out[0], "#GTA6")

    def test_dedupe_anade_almohadilla_si_falta(self):
        self.assertEqual(dedupe_hashtags(["GTA6", "GTA"]),
                         ["#GTA6", "#GTA"])

    def test_config_hashtags_max_5_por_idioma(self):
        cfg = load_config()
        for lang in ("es", "en"):
            tags = get_hashtags(cfg, lang)
            self.assertLessEqual(len(tags), 5, lang)
            self.assertEqual(len({t.lower() for t in tags}),
                             len(tags), lang)


class TestCaptionFirstLine(unittest.TestCase):
    def test_primera_linea_con_juego_se_queda(self):
        line = caption_first_line(
            "GTA 6: Rubius roba un avión comercial.\nSegunda línea.",
            "GTA 6: Rubius roba un avión comercial",
            "GTA VI", "es")
        self.assertTrue(line.startswith("GTA 6"))
        self.assertLessEqual(len(line), 125)

    def test_sin_frase_buscable_manda_el_titulo(self):
        line = caption_first_line(
            "Mira lo que pasó en el directo de ayer.",
            "GTA 6: Rubius descubre cómo remolcar la moto acuática",
            "GTA VI", "es")
        self.assertTrue(line.startswith("GTA 6"))

    def test_saludo_no_abre_el_caption(self):
        line = caption_first_line(
            "Hola chicos, miren esto.",
            "GTA 6: Speed roba el scooter de una abuela",
            "GTA VI", "es")
        self.assertFalse(line.lower().startswith("hola"))
        self.assertTrue(line.startswith("GTA 6"))

    def test_hashtag_no_abre_el_caption(self):
        line = caption_first_line(
            "#GTA6 lo mejor del directo.",
            "GTA 6: Speed roba el scooter de una abuela",
            "GTA VI", "es")
        self.assertFalse(line.startswith("#"))

    def test_recorta_a_125_caracteres(self):
        long_title = ("GTA 6: Rubius descubre cómo remolcar la moto "
                      "acuática mientras el chat se muere de risa y "
                      "el helicóptero pasa por encima del puente")
        line = caption_first_line("", long_title, "GTA VI", "es")
        self.assertLessEqual(len(line), 125)
        self.assertTrue(line.startswith("GTA 6"))

    def test_gta_vi_y_gta_6_son_el_mismo_juego(self):
        # La frase buscable vale en cualquiera de las dos formas.
        line = caption_first_line(
            "GTA VI: el momento del directo.",
            "GTA 6: Rubius roba un avión",
            "GTA VI", "es")
        self.assertTrue(line.startswith("GTA VI"))


class TestPackageForPlatforms(unittest.TestCase):
    def test_instagram_max_5_hashtags_sin_duplicados(self):
        out = package_for_platforms(
            "GTA 6: Rubius roba un avión",
            "GTA 6: Rubius roba un avión comercial.\nClip completo.",
            ["GTA 6"], "¡ESTAMOS VOLANDO!", lang="es",
            hashtags=["#GTA6", "#gta6", "#GTA", "#RockstarGames",
                      "#GTA6Clips", "#gaming", "#clips"],
            game="GTA VI")
        ig = out["instagram_reels"]
        self.assertLessEqual(len(ig["hashtags"]), 5)
        self.assertEqual(len({h.lower() for h in ig["hashtags"]}),
                         len(ig["hashtags"]))
        # La descripción final no lleva más de 5 hashtags.
        self.assertLessEqual(ig["caption"].count("#"), 5)

    def test_instagram_primera_linea_con_frase_buscable(self):
        out = package_for_platforms(
            "GTA 6: Rubius roba un avión",
            "GTA 6: Rubius roba un avión comercial.\nClip completo.",
            ["GTA 6"], "¡ESTAMOS VOLANDO!", lang="es",
            game="GTA VI")
        first = out["instagram_reels"]["caption"].split("\n")[0]
        self.assertLessEqual(len(first), 125)
        self.assertIn("GTA 6", first)

    def test_youtube_descripcion_max_5_hashtags(self):
        out = package_for_platforms(
            "GTA 6: Rubius roba un avión",
            "GTA 6: Rubius roba un avión comercial.",
            ["GTA 6"], "¡ESTAMOS VOLANDO!", lang="es",
            game="GTA VI")
        self.assertLessEqual(
            len(out["youtube_shorts"]["hashtags"]), 5)


if __name__ == "__main__":
    unittest.main()
