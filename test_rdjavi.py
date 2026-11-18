#!/usr/bin/env python3
"""Tests de la plantilla comun RDJavi (clipagent/rdjavi.py).

La plantilla del clip (runs/2932415021-p3220/rdjavi/render_rdjavi.sh,
opcion C) se migro aqui el 2026-11-18. Lo que estos tests fijan:
  * punch-in con doble rama + overlay enable, sin trim ni concat;
  * la fuente de color lleva duracion (sin ella ffmpeg no termina);
  * la salida lleva -t como seguro;
  * el recorte de zoom exacto medido en p3220;
  * el ASS karaoke sale al pie de la camara (Alignment 8, MarginV 608),
    nunca en el gameplay (regla del usuario 2026-11-18 19:02).

Estilo pytest (funciones test_*). En esta VM no hay pytest: se
pueden correr con `python3 test_rdjavi.py`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.rdjavi import (  # noqa: E402
    SUBS_ALIGN,
    SUBS_MARGIN_V,
    build_filter,
    build_render_cmd,
    make_karaoke_ass,
)
from clipagent.transcribe import srt_to_karaoke_ass  # noqa: E402

KARAOKE_STYLE = {
    "font_color": "#FFFFFF",
    "active_word_color": "#FFFF00",
    "border_color": "#000000",
    "border_width": 4,
    "fontsize": 56,
    "bold": True,
    "margin_v": 230,
    "max_chars": 8,
    "max_duration": 1.0,
    "box": False,
}


def test_build_filter_usa_overlay_enable_sin_concat():
    """Opcion C: punch-in con overlay enable, sin trim ni concat."""
    filt, _ = build_filter("365:365:90:0", (16, 26), 48.01,
                           "subs.ass", 30)
    assert "enable='lt(t,16)+gte(t,26)'" in filt
    assert "enable='gte(t,16)*lt(t,26)'" in filt
    assert "concat=n=3" not in filt
    assert "trim=start" not in filt


def test_build_filter_color_con_duracion():
    """El color de fondo lleva d=duration: sin el, ffmpeg no cierra
    nunca (cola congelada de 193 s en p3220)."""
    filt, _ = build_filter("365:365:90:0", (16, 26), 48.01,
                           "subs.ass", 30)
    assert "color=c=black:s=1080x1920:r=30:d=48.01" in filt


def test_build_filter_crop_zoom_exacto_p3220():
    """Con el recorte por defecto, el zoom es el exacto medido en
    p3220 (310:310:125:27), no el calculado (309:309:118:28)."""
    filt, _ = build_filter("365:365:90:0", (16, 26), 48.01,
                           "subs.ass", 30)
    assert "crop=310:310:125:27" in filt
    assert "crop=365:365:90:0" in filt
    # Con otro recorte (segmento 1080p del workflow) se calcula.
    filt1080, _ = build_filter("274:274:68:0", (16, 26), 48.01,
                               "subs.ass", 30)
    assert "crop=232:232:89:21" in filt1080


def test_build_render_cmd_t_en_salida():
    """La salida lleva -t duration como seguro (ademas del -t de
    entrada y del -t del loop del fondo)."""
    cmd = build_render_cmd("seg.mp4", 0, 48.01, "subs.ass",
                           "out.mp4", "bg.png")
    assert cmd[-3:] == ["-t", "48.01", "out.mp4"]
    assert cmd.count("-t") == 3


def test_make_karaoke_ass_al_pie_de_camara():
    """El ASS karaoke de la plantilla va al pie de la camara
    (Alignment 8, MarginV 608, banda 595..749), con palabra por
    palabra y la fuente de la plantilla."""
    ass = make_karaoke_ass([(1.12, 5.18, "hola mundo")], KARAOKE_STYLE)
    assert SUBS_ALIGN == 8
    assert SUBS_MARGIN_V == 608
    assert "Style: Karaoke,Noto Sans,56" in ass
    assert ",0,8,10,10,608,1" in ass
    assert "{\\k" in ass
    assert "hola" in ass


def test_srt_to_karaoke_ass_default_no_cambia():
    """Sin posicion explicita, el generador sigue como antes:
    Alignment 2 (abajo) con el margin_v del estilo."""
    ass = srt_to_karaoke_ass([(0, 2, "hola chat")], KARAOKE_STYLE)
    assert ",0,2,10,10,230,1" in ass


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"OK {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FALLO {t.__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} OK")
    sys.exit(1 if failed else 0)
