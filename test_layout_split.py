#!/usr/bin/env python3
"""Regresión del layout split (2026-11-16, corrección del usuario al
ver el clip de IlloJuan runs/2928823801-verify/clips/
clip_01_t16s_s20.92_r6h44_precise.mp4):

"Esto igual lo tienes que arreglar porque solo veo la cámara pero no
veo el juego, tienes que poder centrar las cosas bien".

Causa: la webcam de IlloJuan (~[64, 412, 400x408] en 2560x1440) no
toca el borde superior, así que el detector de cajas de esquina no
la veía, y el escaneo por regiones estables daba 0 cams (el streamer
se mueve hablando). El recorte centrado seguía la cara y la cámara
llenaba el clip. Regla nueva: webcam+gameplay -> split vertical,
juego arriba (~69%) y cámara abajo (~31%), sin superponerse; si el
juego no se ve, la cámara se achica, nunca al revés. Sin PiP grande
en el centro por defecto.

Uso: python3 test_layout_split.py (necesita el .venv con cv2 4.11
para la detección por cara; sin cv2 los tests de detección se saltan).
"""
import os
import subprocess
import unittest

from clipagent.render import (DEFAULTS, _face_cam_box, _split_params,
                    cut_clip_with_layout, decide_layout, segment_filter_graph)

SEG = "runs/2928823801-verify/segments/seg_01_t03h34m30s.mp4"
HAS_CV2 = False
try:
    import cv2  # noqa: F401
    from clipagent.render import _haar_classifier
    HAS_CV2 = _haar_classifier() is not False
except Exception:
    HAS_CV2 = False


@unittest.skipUnless(os.path.exists(SEG), "falta el segmento de IlloJuan")
@unittest.skipUnless(HAS_CV2, "sin detector Haar (cv2 4.11 en .venv)")
class TestSplitIlloJuan(unittest.TestCase):
    def test_face_cam_box_finds_illojuan_cam(self):
        box = _face_cam_box(SEG, 21.0, 16.32, 2560, 1440, dict(DEFAULTS))
        self.assertIsNotNone(box, "la cara estable no dio caja de webcam")
        x0, y0, w, h = box
        # Caja real medida por gradiente: [60, 412, 400x408] aprox.
        self.assertLess(abs(x0 - 60), 120, f"x0={x0}")
        self.assertLess(abs(y0 - 412), 120, f"y0={y0}")
        self.assertLess(abs(w - 400), 120, f"w={w}")
        self.assertLess(abs(h - 408), 120, f"h={h}")

    def test_decide_layout_is_split_not_center(self):
        d = decide_layout(SEG, 21.0, start_offset=16.32, cfg=dict(DEFAULTS))
        layouts = {s["layout"] for s in d["segments"]}
        self.assertEqual(layouts, {"split"},
                         f"layouts={layouts}: el clip debe ser split, no center")
        seg = d["segments"][0]
        self.assertEqual(seg["params"]["game_h"], 1320)
        self.assertEqual(seg["params"]["cam_h"], 600)

    def test_split_filter_has_game_on_top_cam_below(self):
        params = {"box": [4.0, 26.0, 25.0, 25.5], "game_x0": 31.0,
                  "game_h": 1320, "cam_h": 600}
        f, = segment_filter_graph("0:v", "vout", "split", params,
                                  2560, 1440, dict(DEFAULTS)),
        self.assertIn("vstack", f)
        self.assertIn("scale=1080:1320", f)  # juego arriba, mayor parte
        self.assertIn("scale=1080:600", f)   # cámara abajo, más chica
        self.assertNotIn("overlay=", f.split("vstack")[0].replace(
            "overlay=(W-w)/2:(H-h)/2", ""))  # la cámara no se superpone al juego


class TestSplitParams(unittest.TestCase):
    def test_wide_cam_shrinks(self):
        # Caja de cámara de > 45% del ancho: se achica a 480, juego 1440.
        p = _split_params([10.0, 10.0, 80.0, 40.0], dict(DEFAULTS))
        self.assertEqual(p["cam_h"], 480)
        self.assertEqual(p["game_h"], 1440)

    def test_game_window_avoids_left_cam(self):
        p = _split_params([4.0, 26.0, 25.0, 25.5], dict(DEFAULTS))
        self.assertEqual(p["game_h"], 1320)
        self.assertEqual(p["cam_h"], 600)
        # La ventana de juego empieza a la derecha de la cámara.
        self.assertGreaterEqual(p["game_x0"], 4.0 + 25.0)


@unittest.skipUnless(os.path.exists(SEG), "falta el segmento de IlloJuan")
class TestSplitCut(unittest.TestCase):
    def test_cut_is_1080x1920(self):
        out = "/tmp/test-split-cut.mp4"
        d = cut_clip_with_layout(SEG, 16.32, 5.0, out, layout_mode="auto")
        self.assertEqual({s["layout"] for s in d["segments"]}, {"split"})
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", out],
            capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(probe, "1080,1920")


if __name__ == "__main__":
    unittest.main(verbosity=2)
