#!/usr/bin/env python3
"""Test de humo end-to-end (sin red) del prompt final.

Vídeo sintético de 120 s (ffmpeg testsrc) + chat sintético con 1
pico real => detect -> render -> manifiesto -> publicación
simulada, pasando por los filtros de puntaje (6B). Sin red: el
vídeo y el chat son locales; la subida se simula con el publicador
mockeado (la política decide por viralidad/SEO de verdad).

El clip es de 40 s (objetivo del usuario 2026-11-17, mínimo 36 s,
tope 60 s, pre-roll 20 s): un clip más corto lo rechaza bien la
puerta de duración, así que el fixture simula un clip válido para
que el flujo completo se pruebe de verdad.
"""
import copy
import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auto_clips import main
from clipagent.config import DEFAULT_CONFIG
from clipagent.publish import PublishQueue, publish_one


def _chat(path):
    # Chat de 120 s con fondo disperso (1 msg cada 5 s) y una ráfaga
    # de 25 risas a los 5 s: la mediana de ventanas queda baja y el
    # pico pasa el filtro (múltiplo >= 3x). El vídeo dura 120 s, así
    # que el pico cae dentro del vídeo y hay margen para un clip de
    # 40 s con pre-roll de 20 s.
    comments = []
    for t in range(0, 120, 5):
        comments.append({"content_offset_seconds": t,
                         "message": {"body": "hola"},
                         "commenter": {"name": f"fondo{t}"}})
    for j in range(25):
        comments.append({"content_offset_seconds": 5 + j % 5,
                         "message": {"body": "jajaja qué risa"},
                         "commenter": {"name": f"pico{j}"}})
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"comments": comments}, f)
    return path


def _video(path, dur_s=120):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=duration={dur_s}:size=640x360:rate=30",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur_s}",
         "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", path], check=True)
    return path


class TestSmoke(unittest.TestCase):
    def test_detect_render_manifiesto_publicacion_simulada(self):
        with tempfile.TemporaryDirectory() as tmp:
            chat = _chat(os.path.join(tmp, "chat.json"))
            video = _video(os.path.join(tmp, "vod.mp4"))
            out_dir = os.path.join(tmp, "runs")
            argv = ["auto_clips.py", "--chat", chat, "--video", video,
                    "--out", out_dir, "--top-n", "1", "--min-separation",
                    "20", "--window", "10", "--duration", "40",
                    "--pre-roll", "20"]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(main(), 0)
            with open(os.path.join(out_dir, "manifest.json"),
                      encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(manifest["status"], "ok")
            self.assertEqual(len(manifest["clips"]), 1)
            clip = manifest["clips"][0]
            # El clip pasó por los filtros de puntaje: trae viralidad
            # y SEO por plataforma, y el archivo existe en disco.
            self.assertIn("viral_score", clip)
            self.assertIn("total", clip["viral_score"])
            self.assertIn("seo_scores", clip)
            self.assertIn("youtube_shorts", clip["seo_scores"])
            clip_path = os.path.join(out_dir, clip["file"])
            self.assertTrue(os.path.isfile(clip_path))
            self.assertGreater(os.path.getsize(clip_path), 0)
            # Publicación simulada: se encola y la política decide
            # por los puntajes reales (publicar o retener, nunca un
            # fallo del pipeline).
            q = PublishQueue(os.path.join(tmp, "pq.json"))
            item = q.enqueue(
                clip_path, "youtube_shorts", vod_id="v1",
                moment_key=["v1", int(clip["start"]) // 10],
                game="GTA 6")
            cfg = copy.deepcopy(DEFAULT_CONFIG)
            cfg["autopublish"]["mode"] = "full"
            fake_pub = mock.Mock()
            fake_pub.publish.return_value = {
                "ok": True, "video_id": "smoke",
                "url": "https://www.youtube.com/shorts/smoke"}
            now = datetime.datetime(2026, 11, 17, 20, 0,
                                    tzinfo=datetime.timezone(
                                        datetime.timedelta(hours=-3)))
            with mock.patch("clipagent.publish._now_chile",
                           return_value=now), \
                    mock.patch("clipagent.publish.get_publisher",
                               return_value=fake_pub), \
                    mock.patch("clipagent.publish.check_limits",
                               return_value=(True, "ok")), \
                    mock.patch("clipagent.accounts.is_new_account",
                               return_value=False):
                result = publish_one(q, item, cfg=cfg, live=True)
            self.assertIn(result["action"],
                          ("published", "retained", "discarded", "draft"))
            self.assertNotEqual(result["action"], "failed")
            # La decisión menciona el puntaje que la causó.
            self.assertTrue(
                "viralidad" in result["reason"]
                or "SEO" in result["reason"]
                or "draft" in result["reason"],
                result["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
