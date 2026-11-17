#!/usr/bin/env python3
"""Pruebas de 1.2: manifiesto incremental y atómico en auto_clips.py.

Sin red: vídeo sintético (ffmpeg testsrc) + chat.json sintético con
5 picos reales sobre ruido de fondo. El corte 3 se simula roto con
KeyboardInterrupt (un fallo que escapa al bucle, como un crash):
el manifiesto en disco debe conservar los clips 1-2.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auto_clips
from auto_clips import main, write_manifest_atomic

PEAKS = (40, 80, 120, 160, 190)


def _chat_sintetico(path, dur_s=200):
    """Ruido de fondo (1 'hola' cada 10 s) + 5 picos de risas."""
    comments = []
    for t in range(0, dur_s, 10):
        comments.append({"content_offset_seconds": t,
                         "message": {"body": "hola"},
                         "commenter": {"name": f"fondo{t}"}})
    for i, peak in enumerate(PEAKS):
        for j in range(25):
            comments.append({"content_offset_seconds": peak + j % 10,
                             "message": {"body": "jajaja qué risa"},
                             "commenter": {"name": f"pico{i}u{j}"}})
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"comments": comments}, f)
    return path


def _video_sintetico(path, dur_s=200):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=duration={dur_s}:size=640x360:rate=30",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur_s}",
         "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", path], check=True)
    return path


class TestWriteManifestAtomic(unittest.TestCase):
    def test_escribe_json_valido_y_no_deja_temporal(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = {"vod_id": "x", "clips": [{"n": 1}]}
            out = write_manifest_atomic(tmp, manifest)
            self.assertEqual(out, os.path.join(tmp, "manifest.json"))
            with open(out, encoding="utf-8") as f:
                self.assertEqual(json.load(f), manifest)
            self.assertFalse(os.path.exists(
                os.path.join(tmp, ".manifest.json.tmp")))

    def test_reemplaza_el_anterior(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_manifest_atomic(tmp, {"clips": [{"n": 1}]})
            write_manifest_atomic(tmp, {"clips": [{"n": 1}, {"n": 2}]})
            with open(os.path.join(tmp, "manifest.json"),
                      encoding="utf-8") as f:
                self.assertEqual(len(json.load(f)["clips"]), 2)


class TestManifiestoIncremental(unittest.TestCase):
    def test_fallo_en_clip_3_conserva_clips_1_y_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            chat = _chat_sintetico(os.path.join(tmp, "chat.json"))
            video = _video_sintetico(os.path.join(tmp, "vod.mp4"))
            out_dir = os.path.join(tmp, "runs")
            real_cut = auto_clips.cut_clip_with_layout
            llamadas = {"n": 0}

            def cortar(*args, **kwargs):
                llamadas["n"] += 1
                if llamadas["n"] == 3:
                    raise KeyboardInterrupt("crash simulado en el clip 3")
                return real_cut(*args, **kwargs)

            argv = ["auto_clips.py", "--chat", chat, "--video", video,
                    "--out", out_dir, "--top-n", "5", "--min-separation", "30",
                    "--window", "10", "--duration", "10",
                    "--pre-roll", "5", "--no-precise-cut", "--no-viral"]
            with mock.patch.object(auto_clips, "cut_clip_with_layout",
                                   side_effect=cortar), \
                 mock.patch.object(sys, "argv", argv), \
                 self.assertRaises(KeyboardInterrupt):
                main()
            manifest_path = os.path.join(out_dir, "manifest.json")
            self.assertTrue(os.path.isfile(manifest_path),
                            "sin manifiesto tras el crash")
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual([c["n"] for c in manifest["clips"]], [1, 2])
            # 7.4: layout.json se escribe antes del bucle (detección
            # una vez por VOD) y sobrevive al crash.
            layout_path = os.path.join(out_dir, "layout.json")
            self.assertTrue(os.path.isfile(layout_path),
                            "sin layout.json tras el crash")
            with open(layout_path, encoding="utf-8") as f:
                layout = json.load(f)
            self.assertIn("method", layout)
            self.assertIn("facecam_box", layout)
            self.assertFalse(os.path.exists(
                os.path.join(out_dir, ".manifest.json.tmp")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
