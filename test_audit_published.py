"""Tests de clipagent/audit_published.py (ig-audit, adaptado).

Rankea NUESTROS clips por outlier multiple (vistas / mediana
de NUESTRA cuenta), no por views crudos. Distingue problema de
hook (pocas views) de problema de perfil (views sin follows).
Todo con datos simulados: sin red, sin D1, sin yt-dlp real.
"""

import csv
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import audit_published as ap


def _clip(title, views, url="https://www.youtube.com/shorts/x",
          follows=None, duration_s=30):
    return {"title": title, "url": url, "views": views,
            "duration_s": duration_s, "posted_day": "2026-11-21",
            "follows": follows, "platform": "youtube_shorts"}


class TestComputeOutliers(unittest.TestCase):
    def test_mediana_y_outlier_de_nuestra_cuenta(self):
        clips = [_clip("a", 1000), _clip("b", 2000),
                 _clip("c", 4000), _clip("d", 60000),
                 _clip("e", 50000)]
        median, out = ap.compute_outliers(clips)
        # Mediana de [1000, 2000, 4000, 50000, 60000] = 4000.
        self.assertEqual(median, 4000)
        by_title = {c["title"]: c["outlier"] for c in out}
        self.assertEqual(by_title["c"], 1.0)
        self.assertEqual(by_title["d"], 15.0)

    def test_sin_suficientes_clips_no_hay_mediana(self):
        median, out = ap.compute_outliers(
            [_clip("a", 1000), _clip("b", 2000)])
        self.assertIsNone(median)
        self.assertTrue(all(c["outlier"] is None for c in out))


class TestRankingPorOutlier(unittest.TestCase):
    def test_4x_supera_a_1_2x_aunque_tenga_menos_views(self):
        # Un clip de 4k views con mediana 1k (4x) supera a uno
        # de 60k con mediana 50k (1,2x): el ranking es por
        # outlier, no por views crudos.
        clips = [
            _clip("Clip 60k views, 1.2x", 60000),
            _clip("Clip 4k views, 4x", 4000),
        ]
        clips[0]["outlier"] = 1.2
        clips[1]["outlier"] = 4.0
        report = ap.build_report(clips, 1000, "sebastian",
                                 "youtube_shorts")
        self.assertEqual(report["top5"][0]["title"],
                         "Clip 4k views, 4x")
        self.assertEqual(report["top5"][0]["outlier"], 4.0)
        self.assertEqual(report["top5"][1]["outlier"], 1.2)

    def test_top5_ordenado_por_outlier_desc(self):
        clips = [_clip(f"c{i}", 1000 * (i + 1)) for i in range(7)]
        for i, c in enumerate(clips):
            c["outlier"] = float(i + 1)
        report = ap.build_report(clips, 1000, "sebastian",
                                 "youtube_shorts")
        outliers = [r["outlier"] for r in report["top5"]]
        self.assertEqual(outliers, sorted(outliers, reverse=True))
        self.assertEqual(len(report["top5"]), 5)


class TestDiagnose(unittest.TestCase):
    def test_pocas_views_es_problema_de_hook(self):
        clip = _clip("flojo", 500)
        clip["outlier"] = 0.5
        self.assertEqual(ap.diagnose(clip), "problema de hook")

    def test_views_sin_follows_es_problema_de_perfil(self):
        clip = _clip("viral sin follows", 60000, follows=0)
        clip["outlier"] = 15.0
        self.assertEqual(ap.diagnose(clip), "problema de perfil")

    def test_hit_con_follows_es_hit(self):
        clip = _clip("hit", 60000, follows=120)
        clip["outlier"] = 15.0
        self.assertEqual(ap.diagnose(clip), "hit")

    def test_sin_datos_de_follows_no_inventa_perfil(self):
        # Sin dato de follows no se puede decir que sea perfil:
        # es hit, y el informe dice que el diagnóstico de perfil
        # queda pendiente.
        clip = _clip("hit sin dato", 60000, follows=None)
        clip["outlier"] = 15.0
        self.assertEqual(ap.diagnose(clip), "hit")
        report = ap.build_report([clip], 1000, "sebastian",
                                 "youtube_shorts")
        self.assertFalse(report["has_follows_data"])
        self.assertTrue(any("pendiente" in g
                            for g in report["gaps"]))


class TestRunAuditCSV(unittest.TestCase):
    def _csv(self, rows):
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", encoding="utf-8",
                       newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["url", "title", "platform",
                               "posted_day", "follows"])
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return path

    def test_csv_con_views_simuladas(self):
        path = self._csv([
            {"url": "https://www.youtube.com/shorts/a",
             "title": "GTA 6: Rubius roba un avión",
             "platform": "youtube_shorts",
             "posted_day": "2026-11-21", "follows": ""},
            {"url": "https://www.youtube.com/shorts/b",
             "title": "GTA 6: Speed roba el scooter",
             "platform": "youtube_shorts",
             "posted_day": "2026-11-21", "follows": ""},
            {"url": "https://www.youtube.com/shorts/c",
             "title": "GTA 6: AuronPlay no es cinemática",
             "platform": "youtube_shorts",
             "posted_day": "2026-11-21", "follows": ""},
        ])
        views = {
            "https://www.youtube.com/shorts/a": 4000,
            "https://www.youtube.com/shorts/b": 60000,
            "https://www.youtube.com/shorts/c": 1000,
        }

        def fake_stats(url, runner=None, sleep=None):
            return {"views": views[url], "duration_s": 30,
                    "title": ""}

        try:
            with mock.patch.object(ap, "fetch_youtube_stats",
                                   fake_stats):
                report, motivo = ap.run_audit(
                    "sebastian", csv_path=path)
        finally:
            os.unlink(path)
        self.assertEqual(motivo, "")
        self.assertIsNotNone(report)
        # Mediana de [4000, 60000, 1000] = 4000.
        self.assertEqual(report["median_views"], 4000)
        self.assertEqual(report["top5"][0]["outlier"], 15.0)
        self.assertIn("TikTok/IG/FB", report["gaps"][0])

    def test_sin_d1_ni_csv_lo_dice_sin_fallar_en_silencio(self):
        with mock.patch.object(ap.D1Client, "from_env",
                               return_value=None):
            report, motivo = ap.run_audit("sebastian")
        self.assertIsNone(report)
        self.assertIn("sin token", motivo)
        self.assertIn("--csv", motivo)

    def test_csv_vacio_lo_dice(self):
        path = self._csv([])
        try:
            report, motivo = ap.run_audit("sebastian",
                                          csv_path=path)
        finally:
            os.unlink(path)
        self.assertIsNone(report)
        self.assertIn("sin clips", motivo)


if __name__ == "__main__":
    unittest.main()
