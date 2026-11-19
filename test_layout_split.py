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

from clipagent.rdjavi import cam_cover_crop
from clipagent.render import (DEFAULTS, _detect_faces_at, _face_cam_box,
                    _face_union_box, _punch_for_box, _split_params,
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
        # Variante "plain" (split simple): streamer ARRIBA (600 px),
        # gameplay ABAJO (1320 px, la mayor parte). La variante por
        # defecto para GTA 6 es RDJavi (ver test_layout_referencia).
        params = {"box": [4.0, 26.0, 25.0, 25.5], "game_x0": 31.0,
                  "game_h": 1320, "cam_h": 600, "split_variant": "plain"}
        f, = segment_filter_graph("0:v", "vout", "split", params,
                                  2560, 1440, dict(DEFAULTS)),
        self.assertIn("vstack", f)
        self.assertIn("scale=1080:1320", f)  # gameplay abajo, mayor parte
        self.assertIn("scale=1080:600", f)   # streamer arriba, más chico
        self.assertIn("[sp_cv][sp_gv]vstack", f)  # cámara primero (arriba)
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


SEG2 = "runs/2928823801-verify/segments/seg_02_t07h03m00s.mp4"

SEG7775 = "runs/2932415021-p7775/segments/seg_01_t02h09m15s.mp4"


@unittest.skipUnless(os.path.exists(SEG7775), "falta el segmento de p7775")
@unittest.skipUnless(HAS_CV2, "sin detector Haar (cv2 4.11 en .venv)")
class TestFaceCamBoxRubius7775(unittest.TestCase):
    # Regresión 2026-11-18 (port v2 al automático): la webcam de
    # Rubius en p7775 está ABAJO a la izquierda (561:365:0:626 en
    # 2560x1440) y Haar solo la ve en 2 de los 5 instantes que
    # muestreaba _face_cam_box (17, 22 s): devolvía None y el clip
    # caía a "center" (recorte del juego siguiendo una cara del
    # juego), así que el camino automático nunca usaba el split v2.
    # Con 8 instantes la ve en 17, 22 y 32 s (la cara del juego en
    # 27 s la descarta el filtro de mediana) y la caja sale.
    def test_face_cam_box_encuentra_webcam_abajo_izquierda(self):
        box = _face_cam_box(SEG7775, 48.02, 0.0, 2560, 1440,
                            dict(DEFAULTS))
        self.assertIsNotNone(
            box, "la cara de Rubius no dio caja de webcam")
        x0, y0, w, h = box
        # La caja cubre la cara de Rubius: su centro cae dentro de
        # la webcam real [0, 626, 561x365].
        cx, cy = x0 + w // 2, y0 + h // 2
        self.assertGreaterEqual(cx, 0)
        self.assertLessEqual(cx, 561)
        self.assertGreaterEqual(cy, 626)
        self.assertLessEqual(cy, 991)
        # La caja es la UNIÓN de las caras estables + margen
        # (420x358), no la refinada 172x208 (bordes interiores)
        # ni la expansión 2,8x sobre una sola cara (313x313): esa
        # expansión dejaba la cara fuera del encuadre cuando el
        # streamer movía la cabeza (en el render automático la
        # cara solo se veía en t=15-21).
        self.assertGreaterEqual(w, 250)
        self.assertGreaterEqual(h, 250)
        self.assertLessEqual(w, 700)
        self.assertLessEqual(h, 700)

    def test_face_cam_box_cover_y_punch_contienen_las_caras(self):
        # Regresión 2026-11-19 (usuario: "no se le ve la cara
        # siempre al streamer"): la cara viaja ~130 px a la
        # derecha y ~90 px abajo durante el clip y el cover crop
        # 3,45x la sacaba del encuadre. La ventana de cover tiene
        # que contener ENTERAS las caras detectadas (si no, la
        # cara se corta o no se ve). El punch-in, en cambio, tiene
        # que estar DESACTIVADO en p7775: el viaje de la cara en
        # 16-26 s (264x260) no cabe en el cover ni con zoom 1,05x,
        # y con el punch-in viejo (1,30x centrado en la caja) la
        # cara se cortaba a t=24-25 mirando las cartas.
        cfg = dict(DEFAULTS)
        box, punch = _face_cam_box(SEG7775, 48.02, 0.0, 2560, 1440,
                                   cfg, with_punch=True)
        self.assertIsNotNone(box)
        self.assertIs(punch, False)
        bx, by, bw, bh = box
        cover = cam_cover_crop(f"{bw}:{bh}:{bx}:{by}")
        cw, ch, cx0, cy0 = (int(v) for v in cover.split(":"))
        for t in (17, 22, 32):
            found = _detect_faces_at(SEG7775, float(t), 2560, 1440,
                                     cfg)
            self.assertTrue(found, f"sin cara en t={t}")
            fx, fy, fw, fh = max(found, key=lambda b: b[2] * b[3])
            self.assertTrue(
                fx >= cx0 and fy >= cy0
                and fx + fw <= cx0 + cw and fy + fh <= cy0 + ch,
                f"cara t={t} {(fx, fy, fw, fh)} fuera del cover "
                f"{cover}")


class TestPunchForBox(unittest.TestCase):
    # _punch_for_box es pura: no necesita vídeo ni cv2.
    def test_cara_quieta_mantiene_punch(self):
        # Clip tranquilo: viaje chico, centrado en el cover.
        # Caja 400x400 -> cover 400x260 (y 70-330): la ventana
        # se capa a 1,13x porque el viaje está cerca del borde
        # superior del cover y tiene que llegar desde ese borde
        # hasta el extremo opuesto del viaje.
        kept = [(17.0, (100, 100, 50, 50)),
                (22.0, (110, 110, 50, 50)),
                (25.0, (105, 105, 50, 50))]
        self.assertEqual(_punch_for_box([0, 0, 400, 400], kept,
                                        (16, 26)),
                         {"t0": 16.0, "t1": 26.0, "zoom": 1.13,
                          "cx": 130, "cy": 130})

    def test_viaje_grande_desactiva_punch(self):
        # p7775: viaje 264x260, cover 420x272 -> zoom 0,95x < 1,12.
        kept = [(17.0, (132, 680, 128, 128)),
                (22.0, (156, 692, 112, 112)),
                (25.0, (268, 792, 128, 128))]
        self.assertIs(_punch_for_box([56, 596, 420, 400], kept,
                                     (16, 26)), False)

    def test_sin_caras_en_la_ventana_desactiva_punch(self):
        kept = [(30.0, (100, 100, 50, 50))]
        self.assertIs(_punch_for_box([0, 0, 400, 400], kept,
                                     (16, 26)), False)

    def test_punch_desactivado_en_cfg_sin_decision(self):
        kept = [(17.0, (100, 100, 50, 50))]
        self.assertIsNone(_punch_for_box([0, 0, 400, 400], kept,
                                         None))


class TestFaceUnionBox(unittest.TestCase):
    # _face_union_box es pura: no necesita vídeo ni cv2.
    def test_union_cubre_viaje_mas_margen(self):
        kept = [(17.0, (100, 100, 50, 50)),
                (22.0, (120, 110, 50, 50)),
                (32.0, (200, 180, 60, 60))]
        # Unión [100,100,160,140] + margen 0,6x50=30 por lado.
        self.assertEqual(_face_union_box(kept, 1000, 1000),
                         [70, 70, 220, 200])

    def test_piso_2_8x_cuando_la_cara_apenas_se_mueve(self):
        kept = [(1.0, (500, 500, 100, 100)),
                (2.0, (505, 505, 100, 100)),
                (3.0, (500, 500, 100, 100))]
        # Unión 105x105 + margen no llega al piso: la caja es
        # 2,8x la cara mediana (280x280) centrada en la unión.
        self.assertEqual(_face_union_box(kept, 1000, 1000),
                         [412, 412, 280, 280])

    def test_recorta_al_fotograma(self):
        kept = [(1.0, (0, 0, 100, 100)),
                (2.0, (10, 10, 100, 100)),
                (3.0, (0, 0, 100, 100))]
        box = _face_union_box(kept, 1000, 1000)
        x0, y0, w, h = box
        self.assertGreaterEqual(x0, 0)
        self.assertGreaterEqual(y0, 0)
        self.assertLessEqual(x0 + w, 1000)
        self.assertLessEqual(y0 + h, 1000)


@unittest.skipUnless(os.path.exists(SEG2), "falta el segmento 2 de IlloJuan")
class TestFacecamBoxForzado(unittest.TestCase):
    # Regresión 2026-11-18 (clip 02, pico 25400 s = 07h03m20s): la
    # detección automática no vio la webcam (cara chica y oscura en
    # el borde azul, t=5/20/35) y el clip caía a "center"; además el
    # camino forzado facecam_box devolvía "pip" con la caja en px
    # originales sin convertir (el grafo la reescalaba como muestra
    # 160x90: 12x fuera de sitio) y el PiP tapaba el juego. Con la
    # caja medida [42, 306, 306, 315] en 1920x1080, decide_layout
    # debe devolver split vertical (streamer arriba, gameplay
    # abajo), igual que un pip detectado.
    def test_facecam_box_forzado_da_split_no_pip(self):
        cfg = dict(DEFAULTS)
        cfg["facecam_box"] = [42, 306, 306, 315]
        d = decide_layout(SEG2, 40.0, start_offset=0.0, cfg=cfg)
        layouts = {s["layout"] for s in d["segments"]}
        self.assertEqual(layouts, {"split"},
                         f"layouts={layouts}: facecam_box debe dar split, no pip ni center")
        seg = d["segments"][0]
        self.assertEqual(seg["params"]["game_h"], 1320)
        self.assertEqual(seg["params"]["cam_h"], 600)
        # Caja convertida a muestra (160x90): [3.5, 25.5, 25.5, 26.25].
        bx, by, bw, bh = seg["params"]["box"]
        self.assertAlmostEqual(bx, 42 * 160 / 1920, delta=1.0)
        self.assertAlmostEqual(by, 306 * 90 / 1080, delta=1.0)
        self.assertAlmostEqual(bw, 306 * 160 / 1920, delta=1.0)
        self.assertAlmostEqual(bh, 315 * 90 / 1080, delta=1.0)
        # La ventana de juego empieza a la derecha de la cámara.
        self.assertGreaterEqual(seg["params"]["game_x0"], bx + bw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
