#!/usr/bin/env python3
"""Pruebas de 3.7: estado por plataforma (conectada / borrador /
pausada) con motivo, para el panel.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import status


def _rows(**over):
    base = {
        "youtube_shorts": {"platform": "youtube_shorts",
                           "status": "OK", "cause": "token válido"},
        "tiktok": {"platform": "tiktok", "status": "falta paso",
                   "cause": "sin token"},
        "instagram_reels": {"platform": "instagram_reels",
                            "status": "token vencido",
                            "cause": "el token caducó"},
        "facebook_reels": {"platform": "facebook_reels",
                           "status": "sin permisos",
                           "cause": "falta pages_manage_posts"},
    }
    base.update(over)
    return list(base.values())


class TestPlatformStates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_estados_desde_doctor(self):
        st = status.platform_states(self.base, _rows())
        self.assertEqual(st["youtube_shorts"]["state"], "conectada")
        self.assertEqual(st["tiktok"]["state"], "borrador")
        self.assertIn("sin token", st["tiktok"]["motivo"])
        self.assertEqual(st["instagram_reels"]["state"], "borrador")
        self.assertEqual(st["facebook_reels"]["state"], "borrador")

    def test_pausa_explicita_manda_sobre_doctor(self):
        status.pause_platform("youtube_shorts",
                              "circuit breaker: 3 fallos seguidos",
                              self.base)
        st = status.platform_states(self.base, _rows())
        self.assertEqual(st["youtube_shorts"]["state"], "pausada")
        self.assertIn("circuit breaker",
                      st["youtube_shorts"]["motivo"])
        # Reanudar es explícito: vuelve a conectada.
        self.assertTrue(status.resume_platform("youtube_shorts",
                                              self.base))
        st = status.platform_states(self.base, _rows())
        self.assertEqual(st["youtube_shorts"]["state"], "conectada")

    def test_sin_doctor_no_inventa_conectada(self):
        st = status.platform_states(self.base)
        for platform in status.PLATFORMS:
            self.assertIn(st[platform]["state"],
                          ("borrador", "pausada"))
            self.assertNotEqual(st[platform]["state"], "conectada")

    def test_pausa_persiste_en_disco(self):
        status.pause_platform("tiktok", "pausa manual", self.base)
        paused, reason = status.is_paused("tiktok", self.base)
        self.assertTrue(paused)
        self.assertEqual(reason, "pausa manual")
        self.assertFalse(status.is_paused("youtube_shorts", self.base)[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
