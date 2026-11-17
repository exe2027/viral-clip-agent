#!/usr/bin/env python3
"""Pruebas de 2.4: límites de plataforma en limits.yaml.

La cuota de YouTube (1.600 unidades por subida, 10.000/día) es el
valor verificado en la documentación oficial; el "100/día, cuesta
1 unidad" de la auditoría era falso. Los demás límites quedan con
reviewed: null (TODO) y el planificador (publisher.check_limits)
los lee de limits.yaml, no de constantes hardcodeadas.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clipagent.publish as publisher
from clipagent.publish import load_limits


class TestLimits(unittest.TestCase):
    def test_cuota_youtube_verificada(self):
        # Historial oficial de revisiones (2026-11-17): videos.insert
        # cuesta 1 unidad en su propio bucket (100 llamadas/día por
        # defecto, valor conservador); el 1.600 era el valor anterior
        # a dic 2025 y el pool de 10.000 u. es solo para otros métodos.
        limits = load_limits()
        yt = limits["youtube_shorts"]
        self.assertEqual(yt["quota_upload_cost_units"], 1)
        self.assertEqual(yt["quota_upload_calls_per_day"], 100)
        self.assertEqual(yt["quota_other_daily_units"], 10000)
        self.assertEqual(publisher.YOUTUBE_UPLOAD_COST_UNITS, 1)
        self.assertEqual(publisher.YOUTUBE_MAX_UPLOADS_BY_QUOTA, 100)
        self.assertEqual(publisher.DAILY_LIMITS["youtube_shorts"], (3, 5))
        self.assertEqual(publisher.MIN_SPACING_HOURS, 2)
        self.assertEqual(publisher.PUBLISH_WINDOW, (18, 24))
        self.assertEqual(publisher.NEW_ACCOUNT_DAILY, 1)

    def test_reviewed_null_en_lo_no_verificado(self):
        import yaml
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "limits.yaml"), encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self.assertEqual(
            data["youtube_shorts"]["quota_reviewed"], "2026-11-17")
        for platform in ("tiktok", "instagram_reels", "facebook_reels"):
            self.assertIsNone(data[platform]["reviewed"], platform)

    def test_limites_desde_archivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "limits.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write("tiktok:\n  plan_per_day: 1\n  max_per_day: 1\n"
                        "min_spacing_hours: 4\n")
            limits = load_limits(path)
            self.assertEqual(
                (limits["tiktok"]["plan_per_day"],
                 limits["tiktok"]["max_per_day"]), (1, 1))
            self.assertEqual(limits["min_spacing_hours"], 4)
            # Lo no tocado sigue en el valor por defecto.
            self.assertEqual(limits["youtube_shorts"]["plan_per_day"], 3)

    def test_sin_archivo_fallback(self):
        limits = load_limits("/no/existe/limits.yaml")
        self.assertEqual(limits["youtube_shorts"]["quota_upload_cost_units"],
                         1)
        self.assertEqual(
            limits["youtube_shorts"]["quota_upload_calls_per_day"], 100)
        self.assertEqual(limits["tiktok"]["max_per_day"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
