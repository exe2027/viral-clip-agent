#!/usr/bin/env python3
"""Pruebas de 3.8: errores de API simulados y su tratamiento.

- Token vencido y scope faltante: ver test_platforms.py.
- Rechazo por copyright: cuarentena inmediata, sin reintento.
- Límite diario alcanzado: check_limits lo bloquea.
- Error 5xx transitorio: reintentable, nunca cuarentena directa.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clipagent.publish as publisher
from clipagent.publish import PublishQueue, check_limits, handle_rejection

CHILE = timezone(timedelta(hours=-3))


def _queue(tmp):
    return PublishQueue(os.path.join(tmp, "pq.json"))


class TestHandleRejection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_copyright_a_cuarentena_sin_reintento(self):
        q = _queue(self.tmp.name)
        for error in ("403 copyright claim", "Content ID match",
                      "strike por reutilización"):
            clip = f"/tmp/clip_{abs(hash(error)) % 999}.mp4"
            result = handle_rejection(q, clip, "youtube_shorts", error)
            self.assertEqual(result["action"], "quarantined", error)
            item = q._find(clip, "youtube_shorts")
            self.assertEqual(item["state"], "quarantined")

    def test_formato_un_reintento_y_luego_cuarentena(self):
        q = _queue(self.tmp.name)
        clip = "/tmp/clip_formato.mp4"
        first = handle_rejection(q, clip, "tiktok",
                                 "400 título demasiado largo")
        self.assertEqual(first["action"], "retry_once")
        self.assertEqual(q._find(clip, "tiktok")["state"], "failed")
        second = handle_rejection(q, clip, "tiktok",
                                  "400 título demasiado largo")
        self.assertEqual(second["action"], "quarantined")
        self.assertEqual(q._find(clip, "tiktok")["state"], "quarantined")

    def test_error_5xx_transitorio_no_es_cuarentena_directa(self):
        from clipagent.platforms import _error_result
        result = _error_result(503, {"error": {"code": 1,
                                               "message": "caído"}})
        self.assertTrue(result["retryable"])
        q = _queue(self.tmp.name)
        clip = "/tmp/clip_500.mp4"
        first = handle_rejection(q, clip, "youtube_shorts",
                                 "500 Internal Server Error")
        self.assertEqual(first["action"], "retry_once")


class TestCheckLimits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _now(self, hour):
        return datetime(2026, 11, 17, hour, 0, tzinfo=CHILE)

    def test_tope_diario_bloquea(self):
        q = _queue(self.tmp.name)
        with mock.patch.object(publisher, "_now_chile",
                               return_value=self._now(20)):
            for i in range(5):  # techo de youtube_shorts = 5
                item = q.enqueue(f"/tmp/c{i}.mp4", "youtube_shorts")
                q.mark_published(item, f"vid{i}")
            ok, msg = check_limits(q, "youtube_shorts")
            self.assertFalse(ok)
            self.assertIn("tope diario", msg)

    def test_plan_cubierto_bloquea_antes_del_techo(self):
        q = _queue(self.tmp.name)
        with mock.patch.object(publisher, "_now_chile",
                               return_value=self._now(20)):
            for i in range(3):  # plan de youtube_shorts = 3
                item = q.enqueue(f"/tmp/c{i}.mp4", "youtube_shorts")
                q.mark_published(item, f"vid{i}")
            ok, msg = check_limits(q, "youtube_shorts")
            self.assertFalse(ok)
            self.assertIn("plan del día cubierto", msg)

    def test_espaciamiento_bloquea(self):
        q = _queue(self.tmp.name)
        with mock.patch.object(publisher, "_now_chile",
                               return_value=self._now(20)):
            item = q.enqueue("/tmp/c0.mp4", "tiktok")
            q.mark_published(item, "vid0")
            # La última subida fue "ahora" (published_at = now del
            # mock): el espaciamiento de 2 h bloquea.
            ok, msg = check_limits(q, "tiktok")
            self.assertFalse(ok)
            self.assertIn("espaciamiento", msg)

    def test_fuera_de_franja_bloquea(self):
        q = _queue(self.tmp.name)
        with mock.patch.object(publisher, "_now_chile",
                               return_value=self._now(10)):
            ok, msg = check_limits(q, "tiktok")
            self.assertFalse(ok)
            self.assertIn("fuera de franja", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
