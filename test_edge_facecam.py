#!/usr/bin/env python3
"""Pruebas del rescate por bordes fijos de webcam (2026-11-19, clip 23).

El detector por celdas estables no encontró la cámara del clip 23
y el voto por proximidad daba 0 hits. El detector por bordes fijos
(clipagent/edge_facecam.py, solo numpy + ffmpeg, sin modelo ni IA)
debe encontrarla: caja [1536, 114, 384, 353] sobre 1920x1080,
IoU 0,87 contra la manual [1525, 103, 386, 386], conf "high".
Ver runs/clip23-facecam-test/verify_edge_facecam.json.

Calibración: STILL_SHARE es 0,40 (no 0,30): la webcam real del
clip 23 mueve el 38,5 % de sus píxeles por encima del corte, así
que 0,30 la rechazaba y daba 0 candidatos.

Los tests con el vídeo real se saltan si no está el segmento
``seg.mp4`` (no forma parte del repo: es el tramo 5240-5300 del VOD
2932646051, descargado aparte). Los sintéticos no necesitan vídeo
real: se generan con ffmpeg (fondo de ruido en movimiento + caja
quieta).
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.edge_facecam import detect

SEG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "runs", "clip23-facecam-test", "seg.mp4")
SEG = os.path.normpath(SEG)
MANUAL = [1525, 103, 386, 386]  # caja completa medida a mano (clip 23)
SRC_W, SRC_H = 1920, 1080


def _iou(a, b):
    ax2, ay2 = a[0] + a[2], a[1] + a[3]
    bx2, by2 = b[0] + b[2], b[1] + b[3]
    iw = max(0, min(ax2, bx2) - max(a[0], b[0]))
    ih = max(0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union else 0.0


class TestEdgeFacecamSynthetic(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="edge-facecam-")
        self.noise = "nullsrc=s=1280x720:r=25,noise=alls=24:allf=t"

    def _make(self, name, extra, where="0:0"):
        """Vídeo con fondo en movimiento y una caja quieta dentro."""
        path = os.path.join(self.work, name)
        subprocess.run(["ffmpeg", "-y", "-v", "error",
                        "-f", "lavfi", "-i", self.noise,
                        "-f", "lavfi", "-i", extra,
                        "-filter_complex", f"[0:v][1:v]overlay={where}[v]",
                        "-map", "[v]", "-frames:v", "100",
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", path], check=True)
        return path

    def test_webcam_arriba_izquierda(self):
        cam = self._make("cam.mp4", "color=c=0xC08050:size=300x170:rate=25")
        box = detect(cam)
        self.assertIsNotNone(box)
        self.assertEqual(box["corner"], "top-left")
        self.assertAlmostEqual(box["w"], 300, delta=15)
        self.assertAlmostEqual(box["h"], 170, delta=15)
        self.assertEqual(box["conf"], "high")

    def test_webcam_abajo_derecha(self):
        cam = self._make("cam_br.mp4", "color=c=0xC08050:size=300x170:rate=25",
                         where="980:500")
        box = detect(cam)
        self.assertIsNotNone(box)
        self.assertEqual(box["corner"], "bottom-right")
        self.assertAlmostEqual(box["x"], 980, delta=15)

    def test_sin_webcam_no_encuentra_nada(self):
        plain = os.path.join(self.work, "plain.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error",
                        "-f", "lavfi", "-i", self.noise, "-frames:v", "100",
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", plain], check=True)
        self.assertIsNone(detect(plain))

    def test_archivo_ilegible_no_levanta(self):
        self.assertIsNone(detect(os.path.join(self.work, "nope.mp4")))


@unittest.skipUnless(os.path.isfile(SEG), "sin seg.mp4 del clip 23")
class TestEdgeFacecamClip23(unittest.TestCase):
    def test_clip23_iou_contra_manual(self):
        box = detect(SEG)
        self.assertIsNotNone(box)
        self.assertEqual(box["conf"], "high")
        got = [box["x"], box["y"], box["w"], box["h"]]
        self.assertGreaterEqual(_iou(got, MANUAL), 0.80)

    def test_detect_vod_facecam_usa_edge(self):
        from clipagent.render import detect_vod_facecam
        det = detect_vod_facecam(SEG, 59.925, cfg={})
        self.assertEqual(det["method"], "edge")
        self.assertIsNotNone(det["facecam_box"])
        x, y, w, h = det["facecam_box"]
        full = [round(x * SRC_W / 160), round(y * SRC_H / 90),
                round(w * SRC_W / 160), round(h * SRC_H / 90)]
        self.assertGreaterEqual(_iou(full, MANUAL), 0.80)

    def test_edge_apagado_cae_a_yunet_o_none(self):
        from clipagent.render import detect_vod_facecam
        det = detect_vod_facecam(SEG, 59.925,
                                 cfg={"edge_facecam_fallback": False})
        self.assertNotEqual(det["method"], "edge")
        self.assertIn(det["method"], ("yunet", "none"))


if __name__ == "__main__":
    unittest.main()
