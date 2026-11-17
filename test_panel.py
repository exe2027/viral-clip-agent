#!/usr/bin/env python3
"""Pruebas de 6.2: panel web local (solo localhost, stdlib).
"""
import http.client
import os
import sys
import tempfile
import threading
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import panel


class TestPanel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_dir = os.getcwd()
        os.chdir(self.tmp.name)
        # Repo mínimo: streamers.yaml con un streamer.
        with open(os.path.join(self.tmp.name, "streamers.yaml"), "w",
                  encoding="utf-8") as f:
            f.write("streamers:\n"
                    "  - name: illojuan\n"
                    "    platform: twitch\n"
                    "    game: GTA 6\n"
                    "    language: es\n"
                    "    estado: concedido\n")
        # El panel lee paths relativos a la raíz del módulo; para
        # los tests solo se verifica el HTTP y el toggle/add.
        self.server = panel.make_server(0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        os.chdir(self.orig_dir)
        self.tmp.cleanup()

    def _get(self, path="/"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        r = conn.getresponse()
        body = r.read().decode("utf-8", "ignore")
        conn.close()
        return r.status, body

    def _post(self, path, params):
        body = urllib.parse.urlencode(params)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", path, body,
                     {"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse()
        r.read()
        conn.close()
        return r.status, r.getheader("Location")

    def test_solo_localhost(self):
        host, port = self.server.server_address
        self.assertEqual(host, "127.0.0.1")
        self.assertGreater(port, 0)

    def test_dashboard_carga(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("viral-clip-agent", body)
        self.assertIn("Streamers", body)
        self.assertIn("Plataformas", body)
        self.assertIn("Retenidos", body)
        self.assertIn("Errores recientes", body)

    def test_ruta_desconocida_404(self):
        status, _ = self._get("/no-existe")
        self.assertEqual(status, 404)


class TestPanelToggle(unittest.TestCase):
    """El interruptor escribe enabled en streamers.yaml."""

    def test_set_enabled_ida_y_vuelta(self):
        from clipagent import streamers as st
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "streamers.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write("streamers:\n"
                        "  - name: illojuan\n"
                        "    platform: twitch\n")
            self.assertTrue(st.set_enabled("illojuan", False,
                                           path=path))
            entry = st.get_streamer("illojuan", base_dir=tmp)
            self.assertFalse(entry["enabled"])
            self.assertTrue(st.set_enabled("illojuan", True, path=path))
            self.assertTrue(st.get_streamer("illojuan",
                                            base_dir=tmp)["enabled"])
            self.assertFalse(st.set_enabled("nadie", False, path=path))

    def test_watcher_salta_desactivados(self):
        import watcher
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "streamers.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write("streamers:\n"
                        "  - name: illojuan\n"
                        "    platform: twitch\n"
                        "    game: GTA 6\n"
                        "    language: es\n"
                        "    estado: concedido\n"
                        "    enabled: false\n")
            with mock.patch(
                    "clipagent.streamers.BASE_DIR", tmp):
                names = [i["usuario"]
                         for i in watcher.load_influencers()]
            self.assertNotIn("illojuan", names)


class TestPublishDisabledStreamer(unittest.TestCase):
    def test_clip_de_streamer_desactivado_queda_retenido(self):
        import copy
        import json
        from clipagent.config import DEFAULT_CONFIG
        from clipagent.publish import PublishQueue, publish_one
        with tempfile.TemporaryDirectory() as tmp:
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
            q = PublishQueue(os.path.join(tmp, "pq.json"))
            item = q.enqueue(clip_path, "youtube_shorts", vod_id="v1",
                             moment_key=["v1", 8], streamer="illojuan")
            cfg = copy.deepcopy(DEFAULT_CONFIG)
            cfg["autopublish"]["mode"] = "full"
            with mock.patch(
                    "clipagent.streamers.BASE_DIR", tmp), \
                    mock.patch(
                        "clipagent.streamers.get_streamer",
                        return_value={"name": "illojuan",
                                      "enabled": False}):
                r = publish_one(q, item, cfg=cfg, live=True)
            self.assertEqual(r["action"], "retained")
            self.assertIn("desactivado", r["reason"])


class TestPanelScores(unittest.TestCase):
    def test_render_muestra_puntajes_y_desglose(self):
        # 6B.6: por clip y plataforma: viralidad, SEO, prioridad,
        # desglose de la fórmula, motivo y posición en la cola.
        from clipagent.panel import _render
        data = {
            "service": {"corriendo": False, "supervisor_pid": None,
                        "cola": {"queued": 0}},
            "platforms": {"youtube_shorts": {"state": "conectada",
                                             "motivo": ""}},
            "breaker": {"youtube_shorts": (False, None),
                        "tiktok": (False, None),
                        "instagram_reels": (False, None),
                        "facebook_reels": (False, None)},
            "streamers": [],
            "queue_counts": {},
            "jobs": [],
            "published": [],
            "retained": [],
            "drafts": [],
            "scores": {"youtube_shorts": [{
                "virality": 78, "seo": 95, "chat_multiple": 13,
                "queue_position": 1, "status": "publicable",
                "status_reason": "SEO 95 >= 90",
                "left_behind": None, "auto_archive": False,
                "_item": {"clip": "runs/v1/clips/b.mp4"},
                "priority": {"priority": 85.7, "virality": 78.0,
                             "seo": 95.0, "chat_normalized": 93.0,
                             "age_days": 0, "age_penalty": 0,
                             "weights": {"viral": 0.5, "seo": 0.3,
                                         "chat": 0.2}}},
                {"virality": 51, "seo": 84, "chat_multiple": 6.44,
                 "queue_position": 2, "status": "bloqueado_seo",
                 "status_reason": "SEO 84 < 90 sin override",
                 "left_behind": "SEO 84 < 90 sin override",
                 "auto_archive": False,
                 "_item": {"clip": "runs/v1/clips/a.mp4"},
                 "priority": {"priority": 60.8, "virality": 51.0,
                              "seo": 84.0, "chat_normalized": 58.1,
                              "age_days": 0, "age_penalty": 0,
                              "weights": {"viral": 0.5, "seo": 0.3,
                                          "chat": 0.2}}}],
                "tiktok": [], "instagram_reels": [],
                "facebook_reels": []},
            "errors": [],
        }
        body = _render(data)
        self.assertIn("Cola de publicación", body)
        self.assertIn("85.7", body)
        self.assertIn("60.8", body)
        self.assertIn("V78^0.5", body)
        self.assertIn("bloqueado_seo", body)
        self.assertIn("SEO 84 &lt; 90 sin override", body)


class TestPanelOverride(unittest.TestCase):
    def test_override_de_usuario_queda_en_el_manifiesto(self):
        import json
        from clipagent.panel import register_override_for_clip
        with tempfile.TemporaryDirectory() as tmp:
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
                                       "youtube_shorts": {"score": 85}},
                                   "viral": {"viral_multiple": 8.0}}]}
            with open(os.path.join(run_dir, "manifest.json"), "w",
                      encoding="utf-8") as f:
                json.dump(manifest, f)
            ok, msg = register_override_for_clip(
                clip_path, "youtube_shorts", "título revisado a mano")
            self.assertTrue(ok, msg)
            with open(os.path.join(run_dir, "manifest.json"),
                      encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(len(saved["seo_overrides"]), 1)
            self.assertEqual(saved["seo_overrides"][0]["platform"],
                             "youtube_shorts")
            self.assertEqual(saved["seo_overrides"][0]["score"], 85)
            self.assertIn("revisado a mano",
                          saved["seo_overrides"][0]["motivo"])

    def test_override_sin_clip_devuelve_false(self):
        from clipagent.panel import register_override_for_clip
        ok, _ = register_override_for_clip("/no/existe.mp4",
                                           "youtube_shorts", "x")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
