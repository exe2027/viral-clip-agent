"""Layout GTA 6 personalizado — variante RDJavi (referencia del usuario).

Referencia: Instagram reel https://www.instagram.com/reel/Df4sy4bxlmr/
de RDJavi, desglose exacto fotograma a fotograma (2026-11-18).
DJ Mariio (Df4uyMVjDwq) está APARCADO por orden del usuario
2026-11-18: no se usa como layout.

Geometría RDJavi sobre 1080x1920:
  * Zona superior 0-730: fondo desenfocado de la propia webcam.
  * Webcam nítida centrada 580x590 en (250,190), casi 1:1, esquinas
    redondeadas, solape 50 px sobre el gameplay.
  * Gameplay 730-1770 (1080x1040, ~55-56%), sin marco.
  * Banda inferior 1770-1920: gameplay difuminado + barra atardecer
    Vice City (rosa/violeta/naranja) + handle del canal.
  * Subtítulos: una línea centrada en y≈1690 (MarginV 230), letra
    chica 32 px fina blanca con contorno negro, sin fondo ni
    resaltado palabra por palabra (estilo "ref").
"""
import os
import shutil
import subprocess
import unittest

from clipagent.render import (
    CHANNEL_HANDLE,
    DEFAULTS,
    RDJAVI_BAND_H,
    RDJAVI_BAND_Y0,
    RDJAVI_CAM_H,
    RDJAVI_CAM_W,
    RDJAVI_CAM_X,
    RDJAVI_CAM_Y,
    RDJAVI_GAME_H,
    RDJAVI_TOP_H,
    SUNSET_COLORS,
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


class TestGrafoRdjavi(unittest.TestCase):
    def test_geometria_exacta_en_el_grafo(self):
        f = _rdjavi_graph()
        # Zona superior: fondo desenfocado 1080x730.
        self.assertIn(f"scale=1080:{RDJAVI_TOP_H}", f)
        self.assertIn("boxblur=", f)
        # Webcam nítida 580x590, centrada en (250,190), con esquinas
        # redondeadas (geq) y solape sobre el gameplay.
        self.assertIn(f"scale={RDJAVI_CAM_W}:{RDJAVI_CAM_H}", f)
        self.assertIn(f"overlay={RDJAVI_CAM_X}:{RDJAVI_CAM_Y}", f)
        self.assertIn("alpha_expr=", f)
        # Gameplay 1080x1040 y banda inferior 1080x150.
        self.assertIn(f"scale=1080:{RDJAVI_GAME_H}", f)
        self.assertIn(f"scale=1080:{RDJAVI_BAND_H}", f)
        # Barra atardecer Vice City (rosa/violeta/naranja) en la
        # costura gameplay/banda (y=1764) y handle del canal.
        for color in SUNSET_COLORS:
            self.assertIn(color.lstrip("#").lower(), f.lower())
        self.assertIn(CHANNEL_HANDLE, f)
        self.assertIn("drawtext=", f)
        self.assertIn(f"overlay=0:{RDJAVI_BAND_Y0 - 6}", f)
        # Lienzo final 1080x1920: 730 + 1040 + 150.
        self.assertEqual(RDJAVI_TOP_H + RDJAVI_GAME_H + RDJAVI_BAND_H, 1920)
        # Sin cámara en esquina sobre el juego (no hay PiP).
        self.assertNotIn("pip", f.lower())

    def test_punch_in_solo_cuando_se_pide(self):
        f = _rdjavi_graph()
        self.assertNotIn("between(t,", f)
        f2 = _rdjavi_graph(punch_in={"t0": 5.0, "t1": 7.0, "zoom": 1.25})
        self.assertIn("between(t,5.0,7.0)", f2)


@unittest.skipUnless(shutil.which("ffmpeg"), "falta ffmpeg")
class TestRenderRdjaviReal(unittest.TestCase):
    """Render real (sintético) de la variante RDJavi: 1080x1920."""

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
