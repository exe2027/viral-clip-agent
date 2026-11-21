#!/usr/bin/env python3
"""Pruebas del fallback YuNet de webcam (2026-11-19, clip 23 TheGrefg).

El detector por celdas estables no encontró la cámara del clip 23
(cuadro casi cuadrado, bordes redondeados, interior en movimiento) y
``detect_vod_facecam`` daba method "none". YuNet (vendoreado) debe
encontrarla: caja [1539, 117, 357, 369] sobre 1920x1080, IoU 0,884
contra la manual [1525, 103, 386, 386]. Ver
``runs/clip23-facecam-test/INFORME.md``.

Los tests con el vídeo real se saltan si no está el segmento
``seg.mp4`` (no forma parte del repo: es el tramo 5240-5300 del VOD
2932646051, descargado aparte).
"""
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import cv2  # noqa: F401
    _HAS_CV2 = True
except Exception:  # noqa: BLE001
    _HAS_CV2 = False

import clipagent.yunet_facecam as yunet
from clipagent.yunet_facecam import validate_cam_box, yunet_facecam_box

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


def _frac(box_px):
    return [box_px[0] / SRC_W, box_px[1] / SRC_H,
            box_px[2] / SRC_W, box_px[3] / SRC_H]


class TestValidateCamBox(unittest.TestCase):
    def test_caja_thegrefg_valida(self):
        box = _frac([1539, 117, 357, 369])
        face = _frac([1669, 235, 96, 133])
        self.assertTrue(validate_cam_box(box, face))

    def test_esquina_inferior_rechazada(self):
        # Misma caja abajo: ahí están minimapa y HUD, no la webcam.
        box = [0.80, 0.60, 0.19, 0.34]
        face = [0.87, 0.65, 0.05, 0.12]
        self.assertFalse(validate_cam_box(box, face))

    def test_caja_centrada_rechazada(self):
        box = [0.40, 0.10, 0.19, 0.34]
        face = [0.47, 0.22, 0.05, 0.12]
        self.assertFalse(validate_cam_box(box, face))

    def test_area_mayor_45_rechazada(self):
        box = [0.05, 0.05, 0.80, 0.70]
        face = [0.40, 0.20, 0.05, 0.12]
        self.assertFalse(validate_cam_box(box, face))

    def test_aspecto_extremo_rechazado(self):
        box = [0.80, 0.10, 0.19, 0.06]  # aspecto 3,2: tira, no webcam
        face = [0.87, 0.11, 0.05, 0.04]
        self.assertFalse(validate_cam_box(box, face))

    def test_cara_fuera_de_la_caja_rechazada(self):
        box = _frac([1539, 117, 357, 369])
        face = _frac([200, 200, 96, 133])  # cara en otra esquina
        self.assertFalse(validate_cam_box(box, face))


@unittest.skipUnless(_HAS_CV2, "sin cv2 no hay fallback YuNet")
@unittest.skipUnless(yunet.model_available(),
                     "falta el modelo YuNet vendoreado")
@unittest.skipUnless(os.path.isfile(SEG),
                     "falta runs/clip23-facecam-test/seg.mp4")
class TestYunetClip23(unittest.TestCase):
    def test_yunet_encuentra_camara_clip23(self):
        res = yunet_facecam_box(SEG, 7.57, 47.57)
        self.assertIsNotNone(res, "YuNet no encontró la cámara del 23")
        iou = _iou(res["box"], MANUAL)
        self.assertGreaterEqual(iou, 0.80,
                                f"IoU {iou:.3f} < 0,80; caja {res['box']}")

    def test_detect_vod_facecam_rescata_con_yunet(self):
        # Desde 2026-11-19 el rescate por bordes fijos va ANTES que
        # YuNet: con cfg={} el método es "edge". YuNet sigue siendo
        # el rescate final y se prueba con el borde apagado abajo.
        from clipagent.render import detect_vod_facecam
        det = detect_vod_facecam(SEG, 59.925, cfg={})
        self.assertEqual(det["method"], "edge",
                         f"method={det['method']}, caja={det['facecam_box']}")
        self.assertIsNotNone(det["facecam_box"])
        # La caja viene en coords de muestra (160x90): pasar a px.
        b = det["facecam_box"]
        box_px = [b[0] * SRC_W / 160, b[1] * SRC_H / 90,
                  b[2] * SRC_W / 160, b[3] * SRC_H / 90]
        self.assertGreaterEqual(_iou(box_px, MANUAL), 0.80)

    def test_detect_vod_facecam_con_borde_apagado_rescata_con_yunet(self):
        from clipagent.render import detect_vod_facecam
        det = detect_vod_facecam(SEG, 59.925,
                                 cfg={"edge_facecam_fallback": False})
        self.assertEqual(det["method"], "yunet",
                         f"method={det['method']}, caja={det['facecam_box']}")
        self.assertIsNotNone(det["facecam_box"])
        b = det["facecam_box"]
        box_px = [b[0] * SRC_W / 160, b[1] * SRC_H / 90,
                  b[2] * SRC_W / 160, b[3] * SRC_H / 90]
        self.assertGreaterEqual(_iou(box_px, MANUAL), 0.80)

    def test_decide_layout_usa_yunet_si_cara_falla(self):
        # _face_cam_box ya rescata el 23 por su cuenta: para probar
        # el cableado YuNet se simula que la cara no da caja.
        import clipagent.render as render
        with mock.patch.object(
                render, "_face_cam_box",
                return_value=(None, None)):
            d = render.decide_layout(SEG, 40.0, start_offset=7.0, cfg={})
        layouts = {s["layout"] for s in d["segments"]}
        self.assertEqual(layouts, {"split"},
                         f"segmentos: {d['segments']}")
        self.assertTrue(any("YuNet" in s["reason"]
                            for s in d["segments"]),
                        f"razones: {[s['reason'] for s in d['segments']]}")

    def test_video_sin_cara_devuelve_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            video = os.path.join(tmp, "sin_cara.mp4")
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                 "-i", "testsrc=duration=10:size=640x360:rate=30",
                 "-c:v", "libx264", "-preset", "ultrafast",
                 "-pix_fmt", "yuv420p", video], check=True)
            self.assertIsNone(yunet_facecam_box(video, 0, 10))


class TestYunetSinModelo(unittest.TestCase):
    def test_sin_modelo_devuelve_none_sin_descargar(self):
        # El fallback nunca descarga en runtime: con el modelo
        # ausente debe dar None, no intentar bajarlo ni levantar.
        with mock.patch.object(yunet, "MODEL_PATH",
                               yunet.MODEL_PATH.parent / "no_existe.onnx"):
            self.assertFalse(yunet.model_available())
            self.assertIsNone(yunet_facecam_box(SEG, 0, 10))

    def test_modo_face_no_se_usa_como_webcam(self):
        # Bodila devuelve mode "face" (recorte alrededor de la cara)
        # cuando no hay rectángulo limpio: eso NO es una webcam.
        fake = {"mode": "face",
                "box": [0.80, 0.10, 0.19, 0.34],
                "face": [0.87, 0.22, 0.05, 0.12],
                "confidence": 0.7, "reason": "recorte de cara"}
        with mock.patch.object(yunet, "find_cam", return_value=fake):
            with mock.patch.object(yunet, "model_available",
                                   return_value=True):
                self.assertIsNone(yunet_facecam_box(SEG, 0, 10))


if __name__ == "__main__":
    unittest.main()
