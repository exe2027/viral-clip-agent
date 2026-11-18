"""Miniaturas estilo GTA 6 / Vice City (orden del usuario 2026-11-18).

Texto de miniatura y elección del fotograma por Gemini con estética
GTA 6 / Vice City: neón rosa-violeta-naranja, tipografía condensada,
palmeras y escenario del juego.
"""
import shutil
import subprocess
import unittest
from pathlib import Path

from clipagent.metadata import THUMBNAIL_STYLE, THUMBNAIL_TEXTS, _best_thumbnail_text
from clipagent.vision import _build_prompt


def _words(text):
    return [w for w in str(text).replace("¡", "").replace("!", "").split() if w]


class TestTextosMiniaturaGta6(unittest.TestCase):
    def test_textos_cortos_y_con_sabor_gta6(self):
        self.assertIn("VICE CITY", THUMBNAIL_TEXTS)
        self.assertIn("¡QUÉ LOCURA!", THUMBNAIL_TEXTS)
        for text in THUMBNAIL_TEXTS:
            # La puntuación SEO premia <=3 palabras (+40). Las
            # mayúsculas son estilo del canal (<=2 en MAYÚSCULAS
            # +30; con 3 solo +10, no es una regla dura).
            self.assertLessEqual(len(_words(text)), 3, text)
            self.assertEqual(text, text.upper(), text)

    def test_estilo_gta6_definido(self):
        self.assertEqual(THUMBNAIL_STYLE["name"], "gta6")
        self.assertEqual(THUMBNAIL_STYLE["font_family"], "Barlow Condensed")
        self.assertIn("#FF2E88", THUMBNAIL_STYLE["colors"])
        self.assertIn("#7B2FFF", THUMBNAIL_STYLE["colors"])
        self.assertIn("#FF9E00", THUMBNAIL_STYLE["colors"])
        self.assertIn("palmeras", THUMBNAIL_STYLE["elements"])

    def test_mejor_texto_sigue_eligiendo_de_la_lista(self):
        best = _best_thumbnail_text("VICE CITY", "Rubius se ríe en Vice City")
        self.assertIn(best, THUMBNAIL_TEXTS)


class TestPromptGeminiGta6(unittest.TestCase):
    def test_prompt_pide_estetica_vice_city(self):
        prompt = _build_prompt("Rubius se ríe en Vice City", game="GTA VI")
        self.assertIn("GTA 6", prompt)
        self.assertIn("Vice City", prompt)
        self.assertIn("neón", prompt)
        self.assertIn("rosa/violeta/naranja", prompt)
        self.assertIn("tercera persona", prompt)
        self.assertIn("tipografía condensada", prompt)


@unittest.skipUnless(shutil.which("ffmpeg"), "falta ffmpeg")
class TestQuemadoTextoMiniatura(unittest.TestCase):
    def test_thumbnail_with_text_renderiza_1080x1920(self):
        from clipagent.thumbnails import (
            THUMBNAIL_TEXT_FONTFILE,
            THUMBNAIL_TEXT_FONTFILE_FALLBACK,
            thumbnail_with_text,
        )
        if not (Path(THUMBNAIL_TEXT_FONTFILE).exists()
                or Path(THUMBNAIL_TEXT_FONTFILE_FALLBACK).exists()):
            self.skipTest("falta la fuente condensada")
        src = "/tmp/test-thumb-src.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=0x2a1a4a:size=1080x1920:duration=0.1",
                        "-frames:v", "1", src], check=True)
        out = thumbnail_with_text(src, "VICE CITY", "/tmp/test-thumb-gta6.jpg")
        self.assertTrue(Path(out["path"]).exists())
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0",
             out["path"]], capture_output=True, text=True, check=True)
        self.assertEqual(probe.stdout.strip(), "1080,1920")

    def test_texto_fuera_de_lista_se_rechaza(self):
        from clipagent.thumbnails import thumbnail_with_text
        with self.assertRaises(ValueError):
            thumbnail_with_text("/tmp/test-thumb-src.jpg", "TEXTO INVENTADO")


if __name__ == "__main__":
    unittest.main()
