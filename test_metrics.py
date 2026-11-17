#!/usr/bin/env python3
"""Pruebas de 9.1: métricas a 24/48 h (y 7 d) por clip en SQLite,
reporte simple; alimentan recalibrate (6B.7).
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import metrics

NOW = datetime(2026, 11, 17, 20, 0, tzinfo=timezone.utc)


def _db(tmp):
    return os.path.join(tmp, "metrics.db")


def _publish(tmp, clip, platform, video_id, hours_ago):
    published = (NOW - timedelta(hours=hours_ago)).isoformat()
    metrics.record_publication(clip, platform, video_id,
                               published_at=published,
                               db_path=_db(tmp))


class TestMetricsRecord(unittest.TestCase):
    def test_registra_y_reporta(self):
        with tempfile.TemporaryDirectory() as tmp:
            _publish(tmp, "runs/v1/clips/a.mp4", "youtube_shorts",
                     "vid1", 50)
            _publish(tmp, "runs/v1/clips/b.mp4", "youtube_shorts",
                     "vid2", 50)
            conn = sqlite3.connect(_db(tmp))
            conn.execute("UPDATE clip_metrics SET views_24h = 1000, "
                         "views_48h = 3000 WHERE clip LIKE '%a.mp4'")
            conn.execute("UPDATE clip_metrics SET views_24h = 2000, "
                         "views_48h = 4000 WHERE clip LIKE '%b.mp4'")
            conn.commit()
            conn.close()
            rep = metrics.report(_db(tmp))
            yt = rep["youtube_shorts"]
            self.assertEqual(yt["clips"], 2)
            self.assertEqual(yt["mediana_24h"], 1500)
            self.assertEqual(yt["mediana_48h"], 3500)
            self.assertEqual(yt["mejor_clip"]["clip"],
                             "runs/v1/clips/b.mp4")

    def test_sin_datos_reporte_vacio(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(metrics.report(_db(tmp)), {})


def _fake_youtube(views):
    def request(method, url, headers, body):
        assert "youtube" in url
        assert headers.get("Authorization", "").startswith("Bearer ")
        return 200, {"items": [{"statistics": {"viewCount": str(views)}}]}
    return request


class TestCollect(unittest.TestCase):
    def _token_patches(self):
        return [
            mock.patch("clipagent.credentials.load_token",
                       return_value={"access_token": "tok",
                                     "expires_at": 9999999999}),
            mock.patch("clipagent.credentials.token_expired",
                       return_value=False),
        ]

    def test_mide_lo_vencido_y_no_lo_reciente(self):
        with tempfile.TemporaryDirectory() as tmp:
            _publish(tmp, "runs/v1/clips/a.mp4", "youtube_shorts",
                     "vid1", 25)
            _publish(tmp, "runs/v1/clips/b.mp4", "youtube_shorts",
                     "vid2", 5)
            patches = self._token_patches()
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            out = metrics.collect(_db(tmp), now=NOW,
                                  request_fn=_fake_youtube(1234))
            self.assertEqual(out["measured"], 1)
            self.assertEqual(out["pending"], 1)
            conn = sqlite3.connect(_db(tmp))
            row = conn.execute(
                "SELECT views_24h, views_48h FROM clip_metrics "
                "WHERE clip LIKE '%a.mp4'").fetchone()
            self.assertEqual(row, (1234, None))
            row = conn.execute(
                "SELECT views_24h FROM clip_metrics "
                "WHERE clip LIKE '%b.mp4'").fetchone()
            self.assertEqual(row, (None,))
            conn.close()

    def test_mide_48h_y_7d_cuando_toca(self):
        with tempfile.TemporaryDirectory() as tmp:
            _publish(tmp, "runs/v1/clips/a.mp4", "youtube_shorts",
                     "vid1", 200)
            patches = self._token_patches()
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            out = metrics.collect(_db(tmp), now=NOW,
                                  request_fn=_fake_youtube(9999))
            self.assertEqual(out["measured"], 1)
            conn = sqlite3.connect(_db(tmp))
            row = conn.execute(
                "SELECT views_24h, views_48h, views_7d FROM clip_metrics"
            ).fetchone()
            self.assertEqual(row, (9999, 9999, 9999))
            conn.close()

    def test_sin_token_no_mide_y_no_levanta(self):
        with tempfile.TemporaryDirectory() as tmp:
            _publish(tmp, "runs/v1/clips/a.mp4", "youtube_shorts",
                     "vid1", 25)
            with mock.patch("clipagent.credentials.load_token",
                           return_value=None):
                out = metrics.collect(_db(tmp), now=NOW,
                                      request_fn=_fake_youtube(1))
            self.assertEqual(out["measured"], 0)
            self.assertEqual(out["failed"], 1)

    def test_tiktok_sin_api_no_rompe(self):
        with tempfile.TemporaryDirectory() as tmp:
            _publish(tmp, "runs/v1/clips/a.mp4", "tiktok", "tk1", 25)
            out = metrics.collect(_db(tmp), now=NOW,
                                  request_fn=_fake_youtube(1))
            self.assertEqual(out["measured"], 0)
            self.assertEqual(out["failed"], 1)

    def test_error_de_red_no_levanta(self):
        def boom(method, url, headers, body):
            raise ConnectionError("sin red")

        with tempfile.TemporaryDirectory() as tmp:
            _publish(tmp, "runs/v1/clips/a.mp4", "youtube_shorts",
                     "vid1", 25)
            patches = self._token_patches()
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            out = metrics.collect(_db(tmp), now=NOW, request_fn=boom)
            self.assertEqual(out["measured"], 0)
            self.assertEqual(out["failed"], 1)

    def test_fetch_youtube_sin_token_devuelve_none(self):
        with mock.patch("clipagent.credentials.load_token",
                       return_value=None):
            self.assertIsNone(
                metrics.fetch_youtube_views("vid1",
                                            request_fn=_fake_youtube(5)))

    def test_fetch_youtube_404_devuelve_none(self):
        def not_found(method, url, headers, body):
            return 404, {"error": {"message": "not found"}}

        patches = self._token_patches()
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.assertIsNone(
            metrics.fetch_youtube_views("vid1", request_fn=not_found))


if __name__ == "__main__":
    unittest.main(verbosity=2)
