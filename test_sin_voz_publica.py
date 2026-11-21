#!/usr/bin/env python3
"""Regla 2026-11-19 18:41: sin voz + pasa puertas (45/80) -> el
clip se publica igual SIN subtítulos, y el título SÍ se quema.

Nunca se inventa texto: con Whisper sin voz, subtitle_clip deja
el SRT vacío y quema solo el banner amarillo del título (plantilla
rubius), sin una sola palabra de subtítulo sobre el gameplay.
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402

from clipagent.config import load_config  # noqa: E402


def _video(path, duration=2):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i",
         f"color=c=black:s=1080x1920:r=30:d={duration}",
         "-f", "lavfi", "-i", f"sine=frequency=440:d={duration}",
         "-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", path],
        check=True)


def _frame(video, t, w=540, h=960):
    """Un fotograma como array RGB (w x h)."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", video,
         "-frames:v", "1", "-vf", f"scale={w}:{h}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.uint8).reshape(h, w, 3)


def _count_white(arr, y0, y1):
    r = arr[y0:y1, :, 0].astype(int)
    g = arr[y0:y1, :, 1].astype(int)
    b = arr[y0:y1, :, 2].astype(int)
    return int(((r > 180) & (g > 180) & (b > 180)).sum())


def _count_yellow(arr, y0, y1):
    r = arr[y0:y1, :, 0].astype(int)
    g = arr[y0:y1, :, 1].astype(int)
    b = arr[y0:y1, :, 2].astype(int)
    return int(((r > 180) & (g > 180) & (b < 120)).sum())


def _count_white_rect(arr, x0, x1, y0, y1):
    """Píxeles blancos en un rectángulo (x0,x1,y0,y1)."""
    r = arr[y0:y1, x0:x1, 0].astype(int)
    g = arr[y0:y1, x0:x1, 1].astype(int)
    b = arr[y0:y1, x0:x1, 2].astype(int)
    return int(((r > 180) & (g > 180) & (b > 180)).sum())


@unittest.skipUnless(os.system("which ffmpeg > /dev/null 2>&1") == 0,
                     "falta ffmpeg")
class TestSinVozPublica(unittest.TestCase):
    # Coordenadas en el fotograma escalado a 540x960 (factor 0,5):
    # banner amarillo del título 811-1099 -> 405-549; zona de
    # subtítulos palabra por palabra 900-1020 -> 450-510.
    BANNER = (405, 549)
    SUBS = (450, 510)
    # En x se recorta al centro (150-390): el borde blanco del
    # banner cruza la zona de subtítulos por los laterales y no
    # es un subtítulo; las palabras van centradas.
    SUBS_X = (150, 390)

    def test_sin_voz_quema_solo_titulo_sin_inventar_texto(self):
        from clipagent.workers import subtitle_clip
        import clipagent.workers as workers

        with tempfile.TemporaryDirectory() as td:
            video = os.path.join(td, "clip.mp4")
            _video(video)
            out = os.path.join(td, "clip_subs.mp4")
            orig = workers._do_transcribe
            workers._do_transcribe = lambda *a, **k: (
                {"no_speech": True, "word_count": 0,
                 "speech_ratio": 0.0}, None)
            try:
                res = subtitle_clip(
                    video, burn=out, cfg=load_config(), lang="es",
                    title="GTA 6: Risa imparable")
            finally:
                workers._do_transcribe = orig
            self.assertTrue(res["no_speech"], res)
            self.assertFalse(res["publish_blocked"], res)
            self.assertFalse(
                res["needs_human_transcription"], res)
            # SRT vacío: ni una palabra inventada.
            with open(res["srt"], encoding="utf-8") as f:
                self.assertEqual(f.read().strip(), "")
            # El quemado existe: el título sí va en el vídeo.
            self.assertIn("burned", res)
            self.assertTrue(os.path.isfile(out))
            frame = _frame(out, 1)
            self.assertGreater(
                _count_yellow(frame, *self.BANNER), 100,
                "el título no se quemó en el banner 811-1099")
            self.assertEqual(
                _count_white_rect(frame, *self.SUBS_X, *self.SUBS), 0,
                "hay texto blanco en la zona de subtítulos: "
                "se inventó un subtítulo")


if __name__ == "__main__":
    unittest.main(verbosity=2)
