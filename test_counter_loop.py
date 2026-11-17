#!/usr/bin/env python3
"""Pruebas de 6.7: el bucle alimenta el contador (modo gradual).

En modo full el contador no se toca; en gradual, cada publicado
suma published_ok y cada fallo published_fail.
"""
import copy
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.config import DEFAULT_CONFIG
from clipagent.publish import PublishQueue, publish_loop
from counter import load_state


def _setup(tmp):
    run_dir = os.path.join(tmp, "runs", "v1")
    clips_dir = os.path.join(run_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    clip_path = os.path.join(clips_dir, "c1.mp4")
    with open(clip_path, "wb") as f:
        f.write(b"x")
    manifest = {"vod_id": "v1", "seo_overrides": [],
                "clips": [{"peak": 100, "start": 80,
                           "file": "clips/c1.mp4",
                           "viral_score": {"total": 80},
                           "seo_scores": {
                               "youtube_shorts": {"score": 95}},
                           "viral": {"viral_multiple": 8.0},
                           "title_es": "t", "desc_es": "d",
                           "hashtags_es": "#GTA6", "visible": True}]}
    with open(os.path.join(run_dir, "manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f)
    q = PublishQueue(os.path.join(tmp, "pq.json"))
    q.enqueue(clip_path, "youtube_shorts", vod_id="v1",
              moment_key=["v1", 8])
    return q


def _cfg(mode):
    c = copy.deepcopy(DEFAULT_CONFIG)
    c["autopublish"]["mode"] = mode
    return c


class TestCounterLoop(unittest.TestCase):
    def _run_loop(self, tmp, mode, publish_ok=True):
        q = _setup(tmp)
        fake_pub = mock.Mock()
        if publish_ok:
            fake_pub.publish.return_value = {
                "ok": True, "video_id": "xyz",
                "url": "https://www.youtube.com/shorts/xyz"}
        else:
            fake_pub.publish.return_value = {
                "ok": False, "reason": "error 500 del servidor",
                "retryable": True}
        counter_path = os.path.join(tmp, "counter.json")
        now = datetime.datetime(2026, 11, 17, 20, 0,
                                tzinfo=datetime.timezone(
                                    datetime.timedelta(hours=-3)))
        # El gate gradual (fase 1) bloquearía la publicación; se
        # simula fase 4 con el gate abierto. El gate en sí ya tiene
        # sus propios tests (test_policy, tests del counter): aquí
        # solo se mide que el bucle registra el resultado.
        with mock.patch("clipagent.publish._now_chile",
                        return_value=now), \
                mock.patch("clipagent.publish.get_publisher",
                           return_value=fake_pub), \
                mock.patch("clipagent.publish.check_limits",
                           return_value=(True, "ok")), \
                mock.patch("clipagent.accounts.is_new_account",
                           return_value=False), \
                mock.patch("counter.gate_publish",
                           return_value={"allow": True, "mode": "auto",
                                         "reason": "ok",
                                         "veto_minutes": None}):
            stats = publish_loop(q, live=True, once=True,
                                 cfg=_cfg(mode),
                                 counter_path=counter_path)
        return stats, counter_path

    def test_gradual_publicado_suma_al_contador(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats, counter_path = self._run_loop(tmp, "gradual")
            self.assertEqual(stats["published"], 1)
            state = load_state(counter_path)
            self.assertEqual(state["total_published_ok"], 1)
            self.assertEqual(state["phase"]["clips_in_phase"], 1)
            self.assertEqual(state["history"][-1]["event"],
                             "published_ok")

    def test_gradual_fallo_suma_published_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats, counter_path = self._run_loop(tmp, "gradual",
                                                 publish_ok=False)
            self.assertEqual(stats["failed"], 1)
            state = load_state(counter_path)
            self.assertEqual(state["history"][-1]["event"],
                             "published_fail")
            self.assertEqual(state["total_published_ok"], 0)

    def test_full_no_toca_el_contador(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats, counter_path = self._run_loop(tmp, "full")
            self.assertEqual(stats["published"], 1)
            self.assertFalse(os.path.isfile(counter_path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
