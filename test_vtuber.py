#!/usr/bin/env python3
"""Detección de vtuber / PNG tuber (2026-11-17, VOD Rubius 2931565552).

Bug: en el tramo 10332-10372 el avatar es un gato estilo Miku abajo
a la izquierda cuya boca se abre/cierra con la voz. No es una cara
humana (Haar no lo ve: faces_1fps 0 en todo el clip), no toca el
borde superior (el detector de cajas de esquina solo busca arriba)
y el escritorio de fondo es estático, así que el escaneo daba 2 cams
estables al 100% sin costura central y lo trataba como imagen
estática. El clip caía a center y el gato se perdía del recorte.

La corrección (_detect_vtuber_candidates en clipagent/render.py)
es temporal y genérica (nada de "gato Miku" ni coordenadas del VOD):
un parche pequeño en una esquina que cambia fuerte (boca/parpadeo)
mientras el resto de su caja no se mueve y el avatar persiste en
una racha larga de muestras. Una alerta transitoria (avatar que
aparece, se anima y desaparece en 2-3 muestras) no pasa la racha.

Uso: .venv/bin/python -m pytest test_vtuber.py -v
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.render import (  # noqa: E402
    DEFAULTS, _detect_vtuber_candidates, _gray_bytes, _scan_frames,
    decide_layout)

W, H = 160, 90
RUBIUS_SEG = "runs/2931565552-test/segment.mp4"
ILLOJUAN_SEG = "runs/2928823801-verify/segments/seg_01_t03h34m30s.mp4"
HAS_CV2 = False
try:
    import cv2  # noqa: F401
    from clipagent.render import _haar_classifier
    HAS_CV2 = _haar_classifier() is not False
except Exception:
    HAS_CV2 = False


def synth_frame(rects, bg=12):
    """Un fotograma en gris 160x90 como bytes.

    rects: [(x0, y0, x1, y1, valor), ...] en px de muestra.
    """
    buf = bytearray([bg]) * (W * H)
    for (x0, y0, x1, y1, v) in rects:
        for y in range(y0, y1):
            for x in range(x0, x1):
                buf[y * W + x] = v
    return bytes(buf)


def synth_avatar_frames(mouth_states, corner="bottom-left"):
    """Fotogramas sintéticos con un avatar en una esquina.

    El avatar es un rectángulo claro (valor 150) con una boca que
    alterna entre 150 (cerrada) y 122 (abierta): la diferencia por
    celda (28) supera el umbral de cambio fuerte (25) sin mover la
    media del parche más de 30, como la boca real del gato
    (estados 77<->83). mouth_states: un valor por fotograma.
    """
    if corner == "bottom-left":
        body = (0, 50, 40, 90)
        mouth = (20, 60, 40, 80)
    elif corner == "top-right":
        body = (120, 0, 160, 40)
        mouth = (120, 10, 140, 30)
    else:
        raise ValueError(corner)
    frames = []
    for i, mv in enumerate(mouth_states):
        frames.append((float(i * 2), synth_frame([
            (body[0], body[1], body[2], body[3], 150),
            (mouth[0], mouth[1], mouth[2], mouth[3], mv),
            # Un icono estático en otra zona: no debe detectarse.
            (100, 10, 110, 20, 200),
        ])))
    return frames


class TestVtuberSynthetic(unittest.TestCase):
    def test_detects_bottom_left_avatar(self):
        frames = synth_avatar_frames(
            [122, 122, 150, 150, 122, 122, 150, 150, 122, 122])
        cands = _detect_vtuber_candidates(frames, dict(DEFAULTS))
        self.assertEqual(len(cands), 1, f"cands={cands}")
        cand = cands[0]
        self.assertEqual(cand["corner"], "bottom-left")
        # Caja = parche de la boca + 1 celda de margen.
        self.assertEqual(cand["box"], [10, 50, 40, 40])
        self.assertGreaterEqual(cand["present_frac"], 0.9)
        self.assertGreaterEqual(cand["run"], 4)

    def test_detects_top_right_avatar(self):
        # Genérico: cualquier esquina, no solo abajo-izquierda.
        frames = synth_avatar_frames(
            [122, 122, 150, 150, 122, 122, 150, 150, 122, 122],
            corner="top-right")
        cands = _detect_vtuber_candidates(frames, dict(DEFAULTS))
        self.assertEqual(len(cands), 1, f"cands={cands}")
        self.assertEqual(cands[0]["corner"], "top-right")

    def test_static_avatar_is_not_vtuber(self):
        # Avatar siempre igual (boca cerrada): sin parche activo,
        # es fondo estático y NO debe detectarse.
        frames = synth_avatar_frames([150] * 10)
        self.assertEqual(_detect_vtuber_candidates(frames, dict(DEFAULTS)),
                         [])

    def test_transient_overlay_is_not_vtuber(self):
        # Alerta transitoria: el avatar solo está en 2 de 10
        # fotogramas (aparece y desaparece). Pasa el cambio de boca
        # pero no la racha mínima de 4 muestras.
        frames = []
        for i in range(10):
            if i in (2, 3):
                mv = 122 if i == 2 else 150
                frames.append((float(i * 2), synth_frame([
                    (0, 50, 40, 90, 150), (20, 60, 40, 80, mv)])))
            else:
                frames.append((float(i * 2), synth_frame([])))
        self.assertEqual(_detect_vtuber_candidates(frames, dict(DEFAULTS)),
                         [])

    def test_identical_frames_have_no_candidates(self):
        frames = [(float(i * 2), synth_frame([])) for i in range(6)]
        self.assertEqual(_detect_vtuber_candidates(frames, dict(DEFAULTS)),
                         [])


@unittest.skipUnless(os.path.exists(RUBIUS_SEG), "falta el segmento Rubius")
class TestVtuberRubius(unittest.TestCase):
    def test_candidates_are_cat_and_human_cam_not_alert(self):
        frames = [(float(t), _gray_bytes(RUBIUS_SEG, t))
                  for t in range(2, 40, 2)]
        cands = _detect_vtuber_candidates(frames, dict(DEFAULTS))
        by_corner = {c["corner"]: c for c in cands}
        # El gato abajo-izquierda: 11 celdas, presente 58%, racha 11.
        self.assertIn("bottom-left", by_corner)
        cat = by_corner["bottom-left"]
        self.assertEqual(cat["box"], [0, 50, 50, 40])
        self.assertGreaterEqual(cat["present_frac"], 0.5)
        self.assertGreaterEqual(cat["run"], 4)
        # La cámara humana arriba-izquierda la caza la misma
        # heurística (Haar solo la ve esporádicamente): no es un
        # vtuber, pero su caja ES la cámara y el split la usa.
        self.assertIn("top-left", by_corner)
        self.assertEqual(by_corner["top-left"]["box"], [0, 0, 60, 40])
        # La alerta morada abajo-derecha NO es candidata: dura 2-3
        # muestras y no pasa la racha mínima.
        self.assertNotIn("bottom-right", by_corner)

    def test_scan_samples_carry_the_right_box_per_moment(self):
        samples, _ = _scan_frames(RUBIUS_SEG, 40, 0, dict(DEFAULTS))
        by_t = {s["t"]: s for s in samples}
        # Gato presente: t=10 -> caja abajo-izquierda.
        self.assertEqual(by_t[10.0]["vtuber"]["box"], [0, 50, 50, 40])
        self.assertEqual(by_t[10.0]["vtuber"]["corner"], "bottom-left")
        # Cámara humana presente: t=30 -> caja arriba-izquierda.
        self.assertEqual(by_t[30.0]["vtuber"]["box"], [0, 0, 60, 40])
        self.assertEqual(by_t[30.0]["vtuber"]["corner"], "top-left")

    def test_decide_layout_is_split_with_both_boxes(self):
        d = decide_layout(RUBIUS_SEG, 40, start_offset=0, cfg=dict(DEFAULTS))
        layouts = {s["layout"] for s in d["segments"]}
        self.assertEqual(layouts, {"split"},
                         f"layouts={layouts}: el clip debe ser split")
        self.assertEqual(len(d["segments"]), 2)
        first, second = d["segments"]
        # §4.3.1: caja AJUSTADA del candidato (bounding box de
        # _cells sin el margen de 1 celda), no vt["box"] (parche +
        # margen): [0,50,50,40] y [0,0,60,40] eran las viejas.
        self.assertEqual(first["params"]["box"], [0.0, 60.0, 40.0, 30.0])
        self.assertEqual(first["params"]["game_h"], 1320)
        self.assertEqual(first["params"]["cam_h"], 600)
        self.assertIn("vtuber_detected", first["reason"])
        self.assertEqual(second["params"]["box"], [0.0, 0.0, 50.0, 30.0])
        self.assertIn("vtuber_detected", second["reason"])
        # §4.1/§4.3.1: params["avatar"] porta el flag desde la rama
        # vtuber hasta la guarda pip->split (que con avatar=True
        # salta solo la posición). La guarda consume el flag al
        # reconstruir los params del split, así que se afirma en
        # las muestras, no en los segmentos finales.
        vtuber_samples = [s for s in d["samples"]
                          if "vtuber_detected" in s["reason"]]
        self.assertTrue(vtuber_samples)
        for s in vtuber_samples:
            self.assertIs(s["params"].get("avatar"), True,
                          f"muestra t={s['t']} sin flag avatar")


@unittest.skipUnless(os.path.exists(ILLOJUAN_SEG), "falta el segmento IlloJuan")
@unittest.skipUnless(HAS_CV2, "sin detector Haar (cv2 4.11 en .venv)")
class TestVtuberDoesNotBreakHuman(unittest.TestCase):
    def test_illojuan_still_split(self):
        d = decide_layout(ILLOJUAN_SEG, 21.0, start_offset=16.32,
                          cfg=dict(DEFAULTS))
        layouts = {s["layout"] for s in d["segments"]}
        self.assertEqual(layouts, {"split"},
                         f"layouts={layouts}: IlloJuan debe seguir en split")
        self.assertEqual(d["segments"][0]["params"]["game_h"], 1320)
        self.assertEqual(d["segments"][0]["params"]["cam_h"], 600)


class TestNoAvatarStaysCenter(unittest.TestCase):
    def test_static_video_stays_center(self):
        # Vídeo de color plano (sin avatar, sin cara, sin gameplay):
        # sin parche que cumpla la heurística, cae a center.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "static.mp4")
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                 "-i", "color=c=0x204060:s=320x180:r=30:d=6",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", path],
                check=True)
            d = decide_layout(path, 6, start_offset=0, cfg=dict(DEFAULTS))
        layouts = {s["layout"] for s in d["segments"]}
        self.assertEqual(layouts, {"center"},
                         f"layouts={layouts}: sin avatar debe ser center")
        self.assertNotIn("vtuber_detected",
                         " | ".join(s["reason"] for s in d["segments"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
