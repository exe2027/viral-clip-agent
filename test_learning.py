#!/usr/bin/env python3
"""Pruebas de 6B.7: del flujo de métricas (9.1) a recalibrate.

El contrato: metrics.db con tabla clip_metrics (clip, platform,
views_7d). Los puntajes salen del manifiesto de cada clip.
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import learning


def _make_db(tmp, rows):
    path = os.path.join(tmp, "metrics.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE clip_metrics (clip TEXT, platform TEXT, "
                 "views_7d INTEGER, measured_at TEXT)")
    conn.executemany("INSERT INTO clip_metrics VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return path


def _make_manifest(tmp, n):
    """n clips en runs/v1 con puntajes crecientes."""
    run_dir = os.path.join(tmp, "runs", "v1")
    clips_dir = os.path.join(run_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    entries = []
    for i in range(n):
        name = f"c{i}.mp4"
        with open(os.path.join(clips_dir, name), "wb") as f:
            f.write(b"x")
        entries.append({
            "peak": 100 + i, "start": 80 + i * 10, "file": f"clips/{name}",
            "viral_score": {"total": 50 + i},
            "seo_scores": {"youtube_shorts": {"score": 70 + i}},
            "viral": {"viral_multiple": 5.0 + i * 0.2},
            "title_es": "t", "desc_es": "d", "hashtags_es": "#GTA6",
            "visible": True})
    with open(os.path.join(run_dir, "manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump({"vod_id": "v1", "seo_overrides": [],
                   "clips": entries}, f)
    return clips_dir


class TestLearning(unittest.TestCase):
    def test_sin_base_de_datos_no_hay_registros(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                learning.collect_records(os.path.join(tmp, "no.db")), [])

    def test_recolecta_registros_desde_metricas_y_manifiesto(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips_dir = _make_manifest(tmp, 2)
            rows = [(os.path.join(clips_dir, "c0.mp4"), "youtube_shorts",
                     1200, "2026-11-17"),
                    (os.path.join(clips_dir, "c1.mp4"), "youtube_shorts",
                     3400, "2026-11-17")]
            db = _make_db(tmp, rows)
            records = learning.collect_records(db)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["virality"], 50)
            self.assertEqual(records[0]["seo"], 70)
            self.assertEqual(records[0]["views"], 1200)
            self.assertEqual(records[1]["views"], 3400)

    def test_clip_sin_manifiesto_se_ignora(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_manifest(tmp, 1)
            rows = [(os.path.join(tmp, "no-existe.mp4"), "youtube_shorts",
                     500, "2026-11-17")]
            db = _make_db(tmp, rows)
            self.assertEqual(learning.collect_records(db), [])

    def test_menos_de_30_queda_beta(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips_dir = _make_manifest(tmp, 2)
            rows = [(os.path.join(clips_dir, f"c{i}.mp4"),
                     "youtube_shorts", 1000 + i * 100, "2026-11-17")
                    for i in range(2)]
            db = _make_db(tmp, rows)
            report = learning.run_recalibrate(
                db, weights_path=os.path.join(tmp, "weights.json"))
            self.assertEqual(report["status"], "insuficiente")
            self.assertIn("BETA", report["reason"])
            self.assertEqual(report["weights"],
                             {"viral": 0.5, "seo": 0.3, "chat": 0.2})

    def test_con_30_recalibra_y_guarda(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips_dir = _make_manifest(tmp, 30)
            rows = [(os.path.join(clips_dir, f"c{i}.mp4"),
                     "youtube_shorts",
                     1000 + i * 500, "2026-11-17") for i in range(30)]
            db = _make_db(tmp, rows)
            weights_path = os.path.join(tmp, "weights.json")
            report = learning.run_recalibrate(db, weights_path=weights_path)
            self.assertEqual(report["status"], "recalibrated")
            self.assertTrue(os.path.isfile(weights_path))
            with open(weights_path, encoding="utf-8") as f:
                saved = json.load(f)
            for key, old in {"viral": 0.5, "seo": 0.3,
                             "chat": 0.2}.items():
                self.assertLessEqual(abs(saved["weights"][key] - old),
                                     0.25 * old + 1e-9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
