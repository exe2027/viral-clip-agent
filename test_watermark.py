#!/usr/bin/env python3
"""Tests de la marca de agua (clipagent/watermark.py, 2026-11-18).

Elección del usuario: logo BITSONER de colores, arriba a la
derecha, 320 px, 20% de opacidad. Lo que estos tests fijan:
  * la geometría de las 5 posiciones (margen 24 px);
  * cfg["watermark"] se normaliza (posición inválida y rangos
    se corrigen, sin fuente usa el logo por defecto del repo);
  * la fuente se resuelve (ruta local o URL descargada, con
    verificación PNG/JPEG y tope 5 MB);
  * el overlay con ffmpeg pone el logo donde toca sin cambiar
    el tamaño del vídeo;
  * burn_subtitles() la aplica como paso final y rdjavi.render()
    con watermark=... también; sin ella, el vídeo sale intacto.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.watermark import (  # noqa: E402
    DEFAULT_WATERMARK_PNG,
    WATERMARK_DEFAULT_OPACITY,
    WATERMARK_DEFAULT_POSITION,
    WATERMARK_DEFAULT_SIZE_PX,
    WatermarkError,
    apply_watermark,
    maybe_apply_watermark,
    resolve_watermark_source,
    watermark_from_cfg,
    watermark_xy,
)

NEEDS_FFMPEG = unittest.skipUnless(shutil.which("ffmpeg"),
                                   "falta ffmpeg")

LOGO = DEFAULT_WATERMARK_PNG
W, H = 1080, 1920
SIZE = 320
MARGIN = 24


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _make_video(path, with_audio=True, duration=2):
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i",
           f"testsrc=duration={duration}:size={W}x{H}:rate=30"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i",
                f"sine=frequency=440:duration={duration}",
                "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if with_audio:
        cmd += ["-c:a", "aac"]
    cmd += [path]
    subprocess.run(cmd, check=True)


def _frame_box_mean(video, box, t=0.5):
    """Media RGB del cuadro (x0, y0, x1, y1) del fotograma en t.

    Solo ffmpeg + Python estándar (sin PIL: el venv de CI no lo
    tiene): recorta el cuadro y lo vuelca en rawvideo RGB.
    """
    x0, y0, x1, y1 = box
    out = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", video,
         "-frames:v", "1",
         "-vf", f"crop={x1 - x0}:{y1 - y0}:{x0}:{y0}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout
    n = len(out) // 3
    return (sum(out[0::3]) / n, sum(out[1::3]) / n,
            sum(out[2::3]) / n)


def _box_diff(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


class TestWatermarkXY(unittest.TestCase):
    def test_cinco_posiciones(self):
        self.assertEqual(watermark_xy(W, H, SIZE, SIZE, "top-right"),
                         (W - SIZE - MARGIN, MARGIN))
        self.assertEqual(watermark_xy(W, H, SIZE, SIZE, "top-left"),
                         (MARGIN, MARGIN))
        self.assertEqual(watermark_xy(W, H, SIZE, SIZE, "bottom-left"),
                         (MARGIN, H - SIZE - MARGIN))
        self.assertEqual(watermark_xy(W, H, SIZE, SIZE, "bottom-right"),
                         (W - SIZE - MARGIN, H - SIZE - MARGIN))
        self.assertEqual(watermark_xy(W, H, SIZE, SIZE, "center"),
                         ((W - SIZE) // 2, (H - SIZE) // 2))

    def test_posicion_invalida_es_top_right(self):
        self.assertEqual(watermark_xy(W, H, SIZE, SIZE, "arriba"),
                         (W - SIZE - MARGIN, MARGIN))
        self.assertEqual(watermark_xy(W, H, SIZE, SIZE, None),
                         (W - SIZE - MARGIN, MARGIN))

    def test_defaults_son_la_eleccion_del_usuario(self):
        self.assertEqual(WATERMARK_DEFAULT_POSITION, "top-right")
        self.assertEqual(WATERMARK_DEFAULT_SIZE_PX, 320)
        self.assertEqual(WATERMARK_DEFAULT_OPACITY, 20)


class TestWatermarkFromCfg(unittest.TestCase):
    def test_config_del_panel(self):
        wm = watermark_from_cfg({"watermark": {
            "enabled": True, "url": "https://x/logo.png",
            "opacity": 20, "size_px": 320, "position": "top-right"}})
        self.assertEqual(wm, {"enabled": True,
                              "source": "https://x/logo.png",
                              "opacity": 20, "size_px": 320,
                              "position": "top-right"})

    def test_apagada_es_none(self):
        self.assertIsNone(watermark_from_cfg(
            {"watermark": {"enabled": False, "url": "https://x/l.png"}}))
        self.assertIsNone(watermark_from_cfg({}))
        self.assertIsNone(watermark_from_cfg(None))

    def test_posicion_invalida_y_rangos_se_corrigen(self):
        wm = watermark_from_cfg({"watermark": {
            "enabled": True, "source": LOGO, "opacity": 150,
            "size_px": 5000, "position": "arriba"}})
        self.assertEqual(wm["opacity"], 100)
        self.assertEqual(wm["size_px"], 1080)
        self.assertEqual(wm["position"], "top-right")

    def test_opacidad_fraccion_0_1(self):
        wm = watermark_from_cfg({"watermark": {
            "enabled": True, "source": LOGO, "opacity": 0.2}})
        self.assertEqual(wm["opacity"], 20)

    def test_activada_sin_fuente_usa_el_logo_del_repo(self):
        self.assertTrue(os.path.isfile(LOGO))
        wm = watermark_from_cfg({"watermark": {"enabled": True}})
        self.assertEqual(wm["source"], LOGO)


class TestResolveSource(unittest.TestCase):
    def test_ruta_local_pasa_tal_cual(self):
        self.assertEqual(resolve_watermark_source(LOGO), LOGO)

    def test_ruta_inexistente_falla(self):
        with self.assertRaises(WatermarkError):
            resolve_watermark_source("/no/existe/logo.png")

    def test_url_file_descarga_y_verifica_png(self):
        url = "file://" + LOGO
        got = resolve_watermark_source(url)
        try:
            with open(got, "rb") as f:
                self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")
        finally:
            try:
                os.unlink(got)
            except OSError:
                pass

    def test_url_file_no_imagen_falla(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w",
                                         delete=False) as tmp:
            tmp.write("no soy una imagen")
            txt = tmp.name
        try:
            with self.assertRaises(WatermarkError):
                resolve_watermark_source("file://" + txt)
        finally:
            os.unlink(txt)

    def test_url_inaccesible_falla(self):
        with self.assertRaises(WatermarkError):
            resolve_watermark_source("http://127.0.0.1:1/logo.png")


@NEEDS_FFMPEG
class TestApplyWatermark(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wm-test-")
        self.video = os.path.join(self.tmp, "clip.mp4")
        _make_video(self.video)
        self.box = (W - SIZE - MARGIN, MARGIN,
                    W - MARGIN, MARGIN + SIZE)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_overlay_top_right_defaults(self):
        out = os.path.join(self.tmp, "wm.mp4")
        apply_watermark(self.video, out, LOGO)
        self.assertTrue(os.path.isfile(out))
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0", out],
            capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(probe, "1080,1920")

    def test_el_logo_se_ve_arriba_a_la_derecha(self):
        out = os.path.join(self.tmp, "wm.mp4")
        apply_watermark(self.video, out, LOGO)
        base = _frame_box_mean(self.video, self.box)
        marked = _frame_box_mean(out, self.box)
        self.assertGreater(_box_diff(base, marked), 1.0)

    def test_sin_audio_tambien_funciona(self):
        silent = os.path.join(self.tmp, "silencio.mp4")
        _make_video(silent, with_audio=False)
        out = os.path.join(self.tmp, "wm2.mp4")
        apply_watermark(silent, out, LOGO)
        self.assertTrue(os.path.isfile(out))

    def test_maybe_sin_config_no_toca_nada(self):
        before = _sha(self.video)
        self.assertFalse(maybe_apply_watermark(self.video, {}))
        self.assertEqual(_sha(self.video), before)

    def test_maybe_con_fuente_rota_no_toca_nada(self):
        before = _sha(self.video)
        self.assertFalse(maybe_apply_watermark(self.video, {
            "watermark": {"enabled": True,
                          "source": "/no/existe/logo.png"}}))
        self.assertEqual(_sha(self.video), before)

    def test_maybe_aplica_en_el_sitio(self):
        self.assertTrue(maybe_apply_watermark(self.video, {
            "watermark": {"enabled": True, "source": LOGO}}))
        marked = _frame_box_mean(self.video, self.box)
        plain = os.path.join(self.tmp, "plain.mp4")
        _make_video(plain)
        base = _frame_box_mean(plain, self.box)
        self.assertGreater(_box_diff(base, marked), 1.0)


@NEEDS_FFMPEG
class TestBurnSubtitlesWatermark(unittest.TestCase):
    """burn_subtitles() aplica la marca como paso final."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wm-burn-")
        self.video = os.path.join(self.tmp, "clip.mp4")
        _make_video(self.video)
        self.srt = os.path.join(self.tmp, "clip.srt")
        with open(self.srt, "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:01,500\nHola mundo\n")
        self.box = (W - SIZE - MARGIN, MARGIN,
                    W - MARGIN, MARGIN + SIZE)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_quemado_con_marca_y_sin_marca(self):
        from clipagent.transcribe import burn_subtitles
        plain = os.path.join(self.tmp, "plain.mp4")
        marked = os.path.join(self.tmp, "marked.mp4")
        self.assertTrue(burn_subtitles(self.video, self.srt, plain))
        self.assertTrue(burn_subtitles(
            self.video, self.srt, marked,
            config={"watermark": {"enabled": True, "source": LOGO}}))
        base = _frame_box_mean(plain, self.box)
        with_mark = _frame_box_mean(marked, self.box)
        self.assertGreater(_box_diff(base, with_mark), 1.0)


@NEEDS_FFMPEG
class TestRdjaviWatermark(unittest.TestCase):
    """rdjavi.render(watermark=...) la compone tras el pase único."""

    def test_render_aplica_la_marca(self):
        import clipagent.rdjavi as rdjavi
        with tempfile.TemporaryDirectory(prefix="wm-rdjavi-") as tmp:
            src = os.path.join(tmp, "seg.mp4")
            _make_video(src)
            out = os.path.join(tmp, "out.mp4")

            def _fake_run(cmd, what):
                shutil.copyfile(src, cmd[-1])
                return 0.0

            with mock.patch.object(rdjavi, "_run",
                                   side_effect=_fake_run):
                rdjavi.render(src, 0, 2, None, out, None,
                              watermark={"enabled": True,
                                         "source": LOGO})
            self.assertTrue(os.path.isfile(out))
            box = (W - SIZE - MARGIN, MARGIN,
                   W - MARGIN, MARGIN + SIZE)
            base = _frame_box_mean(src, box)
            marked = _frame_box_mean(out, box)
            self.assertGreater(_box_diff(base, marked), 1.0)

    def test_render_sin_watermark_no_la_aplica(self):
        import clipagent.rdjavi as rdjavi
        with tempfile.TemporaryDirectory(prefix="wm-rdjavi-") as tmp:
            src = os.path.join(tmp, "seg.mp4")
            _make_video(src)
            out = os.path.join(tmp, "out.mp4")

            def _fake_run(cmd, what):
                shutil.copyfile(src, cmd[-1])
                return 0.0

            with mock.patch.object(rdjavi, "_run",
                                   side_effect=_fake_run):
                rdjavi.render(src, 0, 2, None, out, None)
            self.assertEqual(_sha(src), _sha(out))


if __name__ == "__main__":
    unittest.main()
