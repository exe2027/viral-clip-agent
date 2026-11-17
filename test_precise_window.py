#!/usr/bin/env python3
"""Pruebas de 2.1: la ventana temporal empieza exactamente donde se pide.

Reproducción (2026-11-17, vídeo con GOP de 5 s): con
``-ss 33 -i ... -c copy`` el primer paquete de vídeo tiene PTS -3.0
(la ventana empieza en el keyframe de 30 s, no a los 33 s). Con
re-encode (extract_window) el primer PTS es 0.0 y la duración es
exacta: peak_rel, hook y subtítulos quedan alineados.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auto_clips import extract_window


def _video_gop5(path, dur_s=120):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=duration={dur_s}:size=640x360:rate=30",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur_s}",
         "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "ultrafast",
         "-g", "150", "-keyint_min", "150", "-sc_threshold", "0",
         "-c:a", "aac", "-shortest", path], check=True)
    return path


def _first_video_pts(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "packet=pts_time", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True)
    return float(r.stdout.splitlines()[0].strip().rstrip(","))


def _duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


class TestVentanaExacta(unittest.TestCase):
    def test_c_copy_empieza_antes_reproduce_el_bug(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _video_gop5(os.path.join(tmp, "gop5.mp4"))
            out = os.path.join(tmp, "w_copy.mp4")
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", "33", "-i", src,
                 "-t", "20", "-c", "copy", out],
                check=True, capture_output=True)
            # La ventana pedida a 33 s empieza en realidad a 30 s.
            self.assertAlmostEqual(_first_video_pts(out), -3.0, places=1)

    def test_extract_window_empieza_exacto(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _video_gop5(os.path.join(tmp, "gop5.mp4"))
            out = os.path.join(tmp, "w_enc.mp4")
            extract_window(src, 33, 20, out)
            self.assertAlmostEqual(_first_video_pts(out), 0.0, places=1)
            self.assertAlmostEqual(_duration(out), 20.0, places=1)


class TestKickDownloadSegment(unittest.TestCase):
    def test_sin_c_copy_y_con_reencode(self):
        from kick_downloader import core
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "seg.mp4")
            seen = {}

            def fake_run(cmd, **kwargs):
                seen["cmd"] = cmd
                open(out, "wb").close()
                return mock.Mock(returncode=0, stderr="")

            with mock.patch.object(core, "pick_variant",
                                   return_value="https://x/master.m3u8"), \
                 mock.patch.object(core.subprocess, "run",
                                   side_effect=fake_run):
                core.download_segment("https://x/master.m3u8", 33, 20, out)
            cmd = seen["cmd"]
            self.assertNotIn("copy", cmd)
            self.assertIn("-c:v", cmd)
            self.assertIn("libx264", cmd)
            self.assertIn("ultrafast", cmd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
