"""Tests de clipagent/hookscore.py (port a español de ig-reel).

El scorer SOLO descarta débiles (< 50 a revisión, nunca bloquea)
y NUNCA elige ganadores: los tests afirman rangos y dealbreakers,
no un orden entre títulos decentes.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.hookscore import WEAK_BELOW, run, score_hook


class TestScoreHook(unittest.TestCase):
    def test_titulo_real_rubius_pasa_por_especificidad(self):
        # Título real nuestro: número (6) + nombre propio (Rubius)
        # -> especificidad alta y overall >= 50 (pasa, no es débil).
        score, weakest, flag = score_hook(
            "GTA 6: Rubius descubre cómo remolcar la moto acuática")
        self.assertGreaterEqual(score, WEAK_BELOW)
        self.assertIsNone(flag)
        results, _overall, _verdict, _flags = run(
            "GTA 6: Rubius descubre cómo remolcar la moto acuática")
        self.assertEqual(results["SPECIFICITY"][0], 100.0)

    def test_titulo_real_grefg_pasa(self):
        # "imposible" es marcador de tensión en español.
        score, _weakest, flag = score_hook(
            "GTA 6: TheGrefg y la patada nuclear imposible")
        self.assertGreaterEqual(score, WEAK_BELOW)
        self.assertIsNone(flag)

    def test_titulo_real_auronplay_pasa(self):
        score, _weakest, flag = score_hook(
            "GTA 6: AuronPlay «No es una cinemática»!!")
        self.assertGreaterEqual(score, WEAK_BELOW)
        self.assertIsNone(flag)

    def test_saludo_puntua_bajo_con_dealbreaker(self):
        # Saludo + preámbulo ("hoy les traigo"): débil, con
        # dealbreaker de saludo entre los flags.
        score, weakest, flag = score_hook(
            "Hola chicos, hoy les traigo un clip")
        self.assertLess(score, WEAK_BELOW)
        _results, _o, _v, flags = run(
            "Hola chicos, hoy les traigo un clip")
        self.assertTrue(any("Saludo" in f for f in flags))
        self.assertIsNotNone(flag)

    def test_sin_nada_concreto_puntua_bajo(self):
        # Sin número, sin nombre, nada comprobable.
        score, weakest, flag = score_hook("El mejor momento del juego")
        self.assertLess(score, WEAK_BELOW)
        results, _o, _v, _f = run("El mejor momento del juego")
        self.assertEqual(results["SPECIFICITY"][0], 15.0)

    def test_pedir_atencion_es_dealbreaker(self):
        _score, _w, flag = score_hook("No te muevas, mira esto")
        self.assertIsNotNone(flag)
        self.assertIn("atención", flag)

    def test_preambulo_de_video_es_dealbreaker(self):
        _score, _w, flag = score_hook(
            "En este vídeo te voy a enseñar el mejor truco")
        self.assertIsNotNone(flag)
        self.assertIn("Preámbulo", flag)

    def test_numero_escrito_con_letra_cuenta(self):
        # "tres" es tan concreto como "3" (el original solo
        # contaba dígitos y fallaba aquí).
        _s1, _w, _f = score_hook("GTA 6: tres formas de robar un avión")
        results, _o, _v, _f = run(
            "GTA 6: tres formas de robar un avión")
        self.assertGreater(results["SPECIFICITY"][0], 15.0)

    def test_score_hook_devuelve_tupla(self):
        out = score_hook("GTA 6: Rubius roba un jet")
        self.assertEqual(len(out), 3)
        score, weakest, flag = out
        self.assertIsInstance(score, float)
        self.assertIn(weakest, ("LENGTH", "SPECIFICITY", "STAKES",
                                "FRONTLOAD", "ADDRESS"))
        self.assertTrue(flag is None or isinstance(flag, str))

    def test_titulo_vacio_no_rompe(self):
        score, _w, _f = score_hook("")
        self.assertLess(score, WEAK_BELOW)


if __name__ == "__main__":
    unittest.main()
