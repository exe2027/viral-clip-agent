"""Layout GTA 6 personalizado — variante RDJavi v2 (plantilla
2026-11-18 21:01, clipagent/rdjavi.py, portada al camino
automático 2026-11-18).

Referencia: Instagram reel https://www.instagram.com/reel/Df4sy4bxlmr/
de RDJavi, desglose exacto fotograma a fotograma (2026-11-18),
revisado con la captura de Willyrex. DJ Mariio (Df4uyMVjDwq)
está APARCADO por orden del usuario 2026-11-18: no se usa como
layout.

Geometría v2 sobre 1080x1920:
  * Webcam ARRIBA a ANCHO COMPLETO 1080x702 (y 0-702): COVER del
    rectángulo PiP detectado por clip, sin barras negras, sin
    esquinas redondeadas y sin fondo desenfocado (la webcam tapa
    todo el ancho).
  * Franja negra del TÍTULO 702-800 (98 px): una sola línea, sin
    "GTA 6" delante (se quema en el paso de subtítulos).
  * Gameplay 800-1740 (1080x940), con zoom suave 1,15x en el
    payoff (18-24 s del clip) y punch-in de la webcam 1,30x en la
    reacción (16-26 s).
  * Barra negra de SUBTÍTULOS 1740-1920 (180 px): subtítulos
    amarillos fijos abajo del todo, nunca sobre el gameplay.
  * Sin handle del canal ni barra atardecer (geometría vieja
    retirada con la v2).
"""
import shutil
import subprocess
import unittest

from clipagent.render import (
    DEFAULTS,
    RDJAVI_BAND_H,
    RDJAVI_BAND_Y0,
    RDJAVI_CAM_H,
    RDJAVI_CAM_W,
    RDJAVI_CAM_X,
    RDJAVI_CAM_Y,
    RDJAVI_GAME_H,
    RDJAVI_GAME_Y0,
    RDJAVI_TITLE_H,
    RDJAVI_TITLE_Y0,
    RDJAVI_TOP_H,
    _split_params,
    detect_framing,
    resolve_split_variant,
    segment_filter_graph,
)

FACE_BOX = [50.0, 10.0, 60.0, 60.0]      # plano medio, solo la cara
SETUP_BOX = [5.0, 10.0, 140.0, 40.0]    # plano ancho, setup (aspecto 3,5)


def _rdjavi_graph(box=FACE_BOX, **over):
    params = {"box": list(box), "game_x0": 20.0,
              "game_h": 1320, "cam_h": 600,
              "split_variant": "rdjavi", "framing": "face",
              "punch_in": None}
    params.update(over)
    return segment_filter_graph("0:v", "vout", "split", params,
                                1280, 720, dict(DEFAULTS))


class TestVarianteRdjaviDefault(unittest.TestCase):
    def test_auto_resuelve_rdjavi_para_gta6(self):
        self.assertEqual(resolve_split_variant({"split_variant": "auto"}),
                         "rdjavi")
        self.assertEqual(resolve_split_variant({}), "rdjavi")
        self.assertEqual(resolve_split_variant({"split_variant": "rdjavi"}),
                         "rdjavi")

    def test_plain_sigue_disponible_como_fallback(self):
        self.assertEqual(resolve_split_variant({"split_variant": "plain"}),
                         "plain")

    def test_djmariio_aparcado_resuelve_rdjavi(self):
        # Orden del usuario 2026-11-18: DJ Mariio olvidado por ahora.
        self.assertEqual(resolve_split_variant({"split_variant": "djmariio"}),
                         "rdjavi")


class TestDeteccionEncuadre(unittest.TestCase):
    def test_cara_plano_medio_es_face(self):
        self.assertEqual(detect_framing(FACE_BOX, dict(DEFAULTS)), "face")

    def test_setup_plano_ancho_es_setup(self):
        self.assertEqual(detect_framing(SETUP_BOX, dict(DEFAULTS)), "setup")

    def test_sin_caja_es_face_por_defecto_gta6(self):
        self.assertEqual(detect_framing(None, dict(DEFAULTS)), "face")

    def test_split_params_guarda_framing_y_variante(self):
        p = _split_params(FACE_BOX, dict(DEFAULTS))
        self.assertEqual(p["framing"], "face")
        self.assertEqual(p["split_variant"], "rdjavi")
        p2 = _split_params(SETUP_BOX, dict(DEFAULTS))
        self.assertEqual(p2["framing"], "setup")
        # Aunque el encuadre sea setup, la variante sigue siendo
        # RDJavi mientras DJ Mariio esté aparcado.
        self.assertEqual(p2["split_variant"], "rdjavi")


class TestGrafoRdjaviV2(unittest.TestCase):
    def test_geometria_exacta_v2_en_el_grafo(self):
        f = _rdjavi_graph()
        # Webcam a ancho completo 1080x702 en (0,0): COVER del PiP,
        # sin barras negras, sin esquinas redondeadas (geq) ni fondo
        # desenfocado (boxblur): la webcam tapa todo el ancho.
        self.assertIn(f"scale={RDJAVI_CAM_W}:{RDJAVI_CAM_H}", f)
        self.assertIn(f"overlay={RDJAVI_CAM_X}:{RDJAVI_CAM_Y}", f)
        self.assertNotIn("boxblur=", f)
        self.assertNotIn("alpha_expr=", f)
        self.assertNotIn("drawtext=", f)
        # Gameplay 1080x940 con pad negro: el juego va a y=800 y la
        # franja del título (702-800) y la barra de subtítulos
        # (1740-1920) quedan en negro.
        self.assertIn("crop=1080:940", f)
        self.assertIn(f"pad=1080:1920:0:{RDJAVI_GAME_Y0}:black", f)
        # Sin handle del canal ni barra atardecer (geometría vieja).
        self.assertNotIn("ELCLIPDEGTA6", f)
        self.assertNotIn("gradients=", f)
        # Lienzo final 1080x1920: 702 + 98 + 940 + 180.
        self.assertEqual(RDJAVI_TOP_H + RDJAVI_TITLE_H + RDJAVI_GAME_H
                         + RDJAVI_BAND_H, 1920)
        self.assertEqual(RDJAVI_TITLE_Y0, 702)
        self.assertEqual(RDJAVI_GAME_Y0, 800)
        self.assertEqual(RDJAVI_BAND_Y0, 1740)
        # Sin cámara en esquina sobre el juego (no hay PiP).
        self.assertNotIn("pip", f.lower())

    def test_punch_in_y_game_zoom_por_defecto_v2(self):
        # Defaults v2: punch-in de la webcam 16-26 s y zoom suave
        # del gameplay 18-24 s (el pico va en el segundo 20 del
        # clip por construcción).
        f = _rdjavi_graph()
        self.assertIn("enable='lt(t,16)+gte(t,26)'", f)
        self.assertIn("enable='gte(t,16)*lt(t,26)'", f)
        self.assertIn("between(t,18,20)", f)
        self.assertIn("between(t,22,24)", f)
        # Explícito manda sobre el default.
        f2 = _rdjavi_graph(punch_in={"t0": 5.0, "t1": 7.0, "zoom": 1.25})
        self.assertIn("enable='gte(t,5)*lt(t,7)'", f2)
        self.assertNotIn("gte(t,16)*lt(t,26)", f2)
        # False desactiva el punch-in (queda una sola rama de cámara).
        f3 = _rdjavi_graph(punch_in=False)
        self.assertNotIn("camZ", f3)
        self.assertNotIn("gte(t,16)*lt(t,26)", f3)

    def test_sin_caja_no_hay_default_silencioso_en_el_grafo(self):
        # La caja del grafo es la que se pasa: dos cajas distintas
        # (p3220 arriba-izquierda, p7775 abajo-izquierda) dan dos
        # recortes distintos, nunca el mismo default.
        f_arriba = segment_filter_graph(
            "0:v", "vout", "split",
            {"box": [0.0, 0.0, 35.06, 22.81], "game_x0": 20.0,
             "game_h": 1320, "cam_h": 600, "split_variant": "rdjavi",
             "framing": "face", "punch_in": False},
            2560, 1440, dict(DEFAULTS))
        f_abajo = segment_filter_graph(
            "0:v", "vout", "split",
            {"box": [0.0, 39.12, 35.06, 22.81], "game_x0": 20.0,
             "game_h": 1320, "cam_h": 600, "split_variant": "rdjavi",
             "framing": "face", "punch_in": False},
            2560, 1440, dict(DEFAULTS))
        self.assertIn("crop=561:364:0:0", f_arriba)
        self.assertIn("crop=561:364:0:626", f_abajo)
        self.assertNotEqual(f_arriba, f_abajo)


@unittest.skipUnless(shutil.which("ffmpeg"), "falta ffmpeg")
class TestRenderRdjaviReal(unittest.TestCase):
    """Render real (sintético) de la variante RDJavi v2: 1080x1920."""

    def test_rdjavi_y_plain_renderizan_1080x1920(self):
        src = "/tmp/test-rdjavi-src.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "testsrc2=size=1280x720:rate=30:duration=2",
                        src], check=True)
        for variant in ("rdjavi", "plain"):
            graph = segment_filter_graph(
                "0:v", "vout", "split",
                {"box": list(FACE_BOX), "game_x0": 20.0, "game_h": 1320,
                 "cam_h": 600, "split_variant": variant, "framing": "face",
                 "punch_in": None},
                1280, 720, dict(DEFAULTS))
            out = f"/tmp/test-rdjavi-{variant}.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src,
                            "-filter_complex", graph, "-map", "[vout]",
                            "-frames:v", "10", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", out], check=True)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0",
                 out], capture_output=True, text=True, check=True)
            self.assertEqual(probe.stdout.strip(), "1080,1920", variant)


if __name__ == "__main__":
    unittest.main()
