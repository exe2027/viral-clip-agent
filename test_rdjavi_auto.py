"""Port de la plantilla v2 al camino automático (2026-11-18).

Cubre lo que el camino automático hace distinto a la v2 manual:
  * burn_subtitles con style="rdjavi" quema subtítulos fijos
    amarillos en la barra negra 1740-1920 y el título en UNA
    línea en la franja 702-800 (nunca sobre el gameplay).
  * El karaoke calibra su Fontsize para Arial Black (font_scale).
"""
import os
import subprocess
import tempfile
import unittest

import numpy as np

from clipagent.config import load_config
from clipagent.rdjavi import ASS_FONT_SCALE
from clipagent.transcribe import burn_subtitles, srt_to_karaoke_ass

SRT = """1
00:00:00,500 --> 00:00:02,000
hola mundo

2
00:00:02,000 --> 00:00:02,900
segundo bloque
"""


def _black_video(path, duration=3.0):
    """Vídeo negro 1080x1920 con audio (el quemado copia el audio)."""
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


def _count_yellow(arr, y0, y1):
    r = arr[y0:y1, :, 0].astype(int)
    g = arr[y0:y1, :, 1].astype(int)
    b = arr[y0:y1, :, 2].astype(int)
    return int(((r > 180) & (g > 180) & (b < 120)).sum())


def _count_white(arr, y0, y1):
    r = arr[y0:y1, :, 0].astype(int)
    g = arr[y0:y1, :, 1].astype(int)
    b = arr[y0:y1, :, 2].astype(int)
    return int(((r > 200) & (g > 200) & (b > 200)).sum())


@unittest.skipUnless(os.system("which ffmpeg > /dev/null 2>&1") == 0,
                     "falta ffmpeg")
class TestBurnRdjaviAuto(unittest.TestCase):
    # Coordenadas en el fotograma escalado a 540x960 (factor 0,5):
    # barra de subtítulos 1740-1920 -> 870-960; zona de diálogo
    # del juego 1590-1670 -> 795-835; franja del título 702-800
    # -> 351-400.
    BAR = (870, 960)
    DIALOGO = (795, 835)
    TITULO = (351, 400)

    def _burn(self, title=None, style="rdjavi"):
        tmp = tempfile.mkdtemp()
        src = os.path.join(tmp, "src.mp4")
        srt = os.path.join(tmp, "subs.srt")
        out = os.path.join(tmp, "burned.mp4")
        _black_video(src)
        with open(srt, "w", encoding="utf-8") as f:
            f.write(SRT)
        burn_subtitles(src, srt, out, style=style,
                       config=load_config(), title=title)
        return out

    def test_rdajvi_titulo_y_subtitulos_en_sus_bandas(self):
        out = self._burn(title="Rubius descubre los cromos")
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0",
             out], capture_output=True, text=True, check=True)
        self.assertEqual(probe.stdout.strip(), "1080,1920")
        # A t=1,0 el bloque "hola mundo" está visible.
        fr = _frame(out, 1.0)
        # Subtítulos amarillos en la barra, nunca en la zona de
        # diálogo del juego (1590-1670).
        self.assertGreater(_count_yellow(fr, *self.BAR), 50,
                           "sin amarillo en la barra de subtítulos")
        self.assertEqual(_count_yellow(fr, *self.DIALOGO), 0,
                         "amarillo en la zona de diálogo del juego")
        # Título blanco en UNA línea en la franja 702-800.
        self.assertGreater(_count_white(fr, *self.TITULO), 50,
                           "sin título en la franja")
        # Nada del título fuera de la franja (ni en la webcam ni
        # sobre el gameplay).
        self.assertEqual(_count_white(fr, 0, 351), 0,
                         "título sobre la webcam")
        self.assertEqual(_count_white(fr, 400, 795), 0,
                         "título sobre el gameplay")

    def test_rdajvi_sin_titulo_deja_la_franja_negra(self):
        out = self._burn(title=None)
        fr = _frame(out, 1.0)
        self.assertGreater(_count_yellow(fr, *self.BAR), 50,
                           "sin amarillo en la barra de subtítulos")
        self.assertEqual(_count_white(fr, *self.TITULO), 0,
                         "franja con texto sin título")

    def test_karaoke_con_titulo_tambien_lo_quema_en_la_franja(self):
        out = self._burn(title="Rubius descubre los cromos",
                         style="karaoke")
        fr = _frame(out, 1.0)
        self.assertGreater(_count_white(fr, *self.TITULO), 50,
                           "sin título en la franja (karaoke)")
        self.assertGreater(_count_yellow(fr, *self.BAR), 0,
                           "sin palabra activa amarilla en la barra")


class TestKaraokeFontScale(unittest.TestCase):
    def test_font_scale_calibra_arial_black(self):
        style = {"font_color": "#FFFFFF",
                 "active_word_color": "#FFFF00",
                 "border_color": "#000000", "border_width": 4,
                 "fontsize": 56, "bold": True, "margin_v": 12}
        sin = srt_to_karaoke_ass([(1.0, 2.0, "hola mundo")], style,
                                 font_name="Arial Black")
        self.assertIn("Style: Karaoke,Arial Black,56", sin)
        con = srt_to_karaoke_ass([(1.0, 2.0, "hola mundo")], style,
                                 font_name="Arial Black",
                                 font_scale=ASS_FONT_SCALE)
        # 56 px reales -> Fontsize 79 (56 * 1,4102); Outline 4 -> 6.
        self.assertIn("Style: Karaoke,Arial Black,79", con)
        self.assertIn(",1,6,0,2,10,10,12,1", con)


if __name__ == "__main__":
    unittest.main()
