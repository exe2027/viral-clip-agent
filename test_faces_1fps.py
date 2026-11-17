#!/usr/bin/env python3
"""Pruebas de 2.2: caras a 1 fps en una sola pasada de ffmpeg.

Antes, compute_signals lanzaba un ffmpeg por segundo (un clip de
30 s = 30 procesos). Ahora detect_faces_1fps extrae todos los
frames en un solo proceso y da los mismos conteos.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clipagent.render as layout
from clipagent.config import get_layout_config
from clipagent.render import _detect_faces_at, detect_faces_1fps


def _video(path, dur_s=10):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=duration={dur_s}:size=640x360:rate=30",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur_s}",
         "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", path], check=True)
    return path


class TestDetectFaces1Fps(unittest.TestCase):
    def test_un_solo_ffmpeg_y_mismos_conteos(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = _video(os.path.join(tmp, "vod.mp4"))
            cfg = get_layout_config()
            real_run = layout.subprocess.run
            llamadas = {"n": 0}

            def contar(*args, **kwargs):
                llamadas["n"] += 1
                return real_run(*args, **kwargs)

            with mock.patch.object(layout.subprocess, "run",
                                   side_effect=contar):
                faces = detect_faces_1fps(video, 10, cfg)
            self.assertEqual(llamadas["n"], 1)
            self.assertEqual([f["t"] for f in faces], list(range(10)))
            self.assertTrue(all(isinstance(f["faces"], int)
                                for f in faces))
            # Mismos conteos que el camino viejo (un ffmpeg por segundo).
            viejo = []
            for t in range(10):
                found = _detect_faces_at(video, float(t), 640, 360, cfg)
                viejo.append({"t": t, "faces": len(found or [])})
            self.assertEqual(faces, viejo)

    def test_sin_video_no_rompe(self):
        cfg = get_layout_config()
        self.assertEqual(detect_faces_1fps("/no/existe.mp4", 10, cfg),
                         [{"t": t, "faces": 0} for t in range(10)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
