#!/usr/bin/env python3
"""Pruebas de 6.4: circuit breaker por plataforma.
"""
import copy
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import breaker
from clipagent.config import DEFAULT_CONFIG
from clipagent.publish import PublishQueue, publish_loop


def cfg():
    c = copy.deepcopy(DEFAULT_CONFIG)
    c["autopublish"]["mode"] = "full"
    return c


class TestBreaker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_tres_fallos_seguidos_disparan(self):
        for i in (1, 2):
            r = breaker.record_failure("tiktok", f"error 500 #{i}",
                                       base_dir=self.base, cfg=cfg())
            self.assertFalse(r["tripped"])
        r = breaker.record_failure("tiktok", "error 500 #3",
                                   base_dir=self.base, cfg=cfg())
        self.assertTrue(r["tripped"])
        self.assertIn("3 fallos", r["reason"])
        tripped, reason = breaker.is_tripped("tiktok", self.base)
        self.assertTrue(tripped)
        # Solo esa plataforma: las demás siguen.
        self.assertFalse(breaker.is_tripped("youtube_shorts",
                                            self.base)[0])

    def test_copyright_dispara_de_inmediato(self):
        r = breaker.record_failure("youtube_shorts",
                                   "403 Content ID claim",
                                   base_dir=self.base, cfg=cfg())
        self.assertTrue(r["tripped"])
        self.assertIn("copyright", r["reason"].lower())

    def test_exito_resetea_el_contador(self):
        breaker.record_failure("tiktok", "500", base_dir=self.base,
                               cfg=cfg())
        breaker.record_failure("tiktok", "500", base_dir=self.base,
                               cfg=cfg())
        breaker.record_success("tiktok", self.base)
        r = breaker.record_failure("tiktok", "500", base_dir=self.base,
                                   cfg=cfg())
        self.assertFalse(r["tripped"])  # vuelve a contar desde 1

    def test_reset_explicito_reanuda(self):
        breaker.record_failure("tiktok", "copyright claim",
                               base_dir=self.base, cfg=cfg())
        self.assertTrue(breaker.is_tripped("tiktok", self.base)[0])
        breaker.reset("tiktok", self.base)
        self.assertFalse(breaker.is_tripped("tiktok", self.base)[0])
        state = breaker.load_state(self.base)
        self.assertEqual(state["tiktok"]["consecutive_failures"], 0)

    def test_pausa_visible_en_status(self):
        from clipagent.status import is_paused
        breaker.record_failure("tiktok", "copyright", base_dir=self.base,
                               cfg=cfg())
        paused, reason = is_paused("tiktok", self.base)
        self.assertTrue(paused)
        self.assertIn("copyright", reason)
        breaker.reset("tiktok", self.base)
        self.assertFalse(is_paused("tiktok", self.base)[0])


class TestBreakerLoop(unittest.TestCase):
    def test_el_bucle_salta_la_plataforma_disparada(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = PublishQueue(os.path.join(tmp, "pq.json"))
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
                                   "hashtags_es": "#GTA6",
                                   "visible": True}]}
            with open(os.path.join(run_dir, "manifest.json"), "w",
                      encoding="utf-8") as f:
                json.dump(manifest, f)
            q.enqueue(clip_path, "youtube_shorts", vod_id="v1",
                      moment_key=["v1", 8])
            # El publicador falla 3 veces seguidas (500): el breaker
            # dispara y la 4ª pasada ya no lo intenta.
            calls = {"n": 0}

            def fake_publish_direct(platform, path, queue=None,
                                    live=False, **kwargs):
                calls["n"] += 1
                return {"ok": False, "reason": "500 error del servidor"}

            with mock.patch("clipagent.publish.publish_direct",
                            side_effect=fake_publish_direct), \
                    mock.patch("clipagent.publish.check_limits",
                               return_value=(True, "ok")), \
                    mock.patch("clipagent.breaker._base_dir",
                               return_value=tmp):
                stats = publish_loop(q, live=True, once=True, cfg=cfg())
                # 1ª pasada: 1 intento -> failed (handle_rejection lo
                # deja en failed, sin reintento ciego).
                self.assertEqual(calls["n"], 1)
                self.assertEqual(stats["failed"], 1)
                # Re-encolar y fallar 2 veces más para llegar a 3.
                for _ in range(2):
                    item = q._find(clip_path, "youtube_shorts")
                    q.set_state(item, "queued", "reintento de prueba")
                    publish_loop(q, live=True, once=True, cfg=cfg())
                self.assertEqual(calls["n"], 3)
                self.assertTrue(breaker.is_tripped(
                    "youtube_shorts", tmp)[0])
                # 4ª pasada: el bucle ya no llama al publicador.
                item = q._find(clip_path, "youtube_shorts")
                q.set_state(item, "queued", "reintento de prueba")
                publish_loop(q, live=True, once=True, cfg=cfg())
                self.assertEqual(calls["n"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
