"""Zoom de gameplay parametrizado por altura (spec §3.4/§10).

Una sola función `_game_filter`, altura como parámetro:
- rubius (default, game_h=1080): y_coef = 162, x_coef = 81.
- sin webcam (game_h=1920): y_coef = 288, x_coef = 81 (el ancho
  no cambia). Nunca una copia con el 288 hardcodeado.
"""
import unittest

from clipagent.rubius import _game_filter


class TestGameFilterGameH(unittest.TestCase):
    def test_default_es_rubius_y_coef_162(self):
        out = _game_filter("1440:1440:560:0", (18.0, 24.0))
        self.assertIn("'162*if(", out)
        self.assertIn("'81*if(", out)
        self.assertIn("scale=w='1080*(1+0.15*", out)
        self.assertIn("h='1080*(1+0.15*", out)

    def test_game_h_1920_y_coef_288(self):
        out = _game_filter("810:1440:875:0", (18.0, 24.0), game_h=1920)
        self.assertIn("'288*if(", out)
        self.assertIn("'81*if(", out)
        self.assertIn("scale=w='1080*(1+0.15*", out)
        self.assertIn("h='1920*(1+0.15*", out)
        self.assertIn("crop=1080:1920:", out)

    def test_sin_zoom_escala_a_la_altura(self):
        self.assertEqual(
            _game_filter("1440:1440:560:0", None),
            "crop=1440:1440:560:0,scale=1080:1080,setsar=1")
        self.assertEqual(
            _game_filter("810:1440:875:0", None, game_h=1920),
            "crop=810:1440:875:0,scale=1080:1920,setsar=1")


if __name__ == "__main__":
    unittest.main()
