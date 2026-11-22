"""Tests de la plantilla RUBIUS (clipagent/rubius.py).

Receta: camara 1080x840 arriba, gameplay 840-1920 sin bandas,
banner amarillo transitorio (103-967, 811-1099) y subtitulos
palabra por palabra blancos sobre el gameplay (y 900-1020).
"""

import os
import subprocess

import pytest

from clipagent import rubius
from clipagent.config import (
    TEMPLATE_REQUIRED_KEYS,
    get_subtitle_style,
    load_config,
    template_for_variant,
)
from clipagent.render import (
    _split_rubius_graph,
    resolve_split_variant,
    segment_filter_graph,
)

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.yaml")
SRT_P3220 = os.path.join(
    HERE, "runs", "2932415021-p3220", "clips", "clip_01_t53m20s_s14.3_r7h4.srt"
)


def _real_config():
    return load_config(CONFIG_PATH)


def test_split_banner_title():
    assert rubius.split_banner_title('GTA 6: El chat: "Cinema"') == (
        "EL CHAT",
        "CINEMA",
    )
    assert rubius.split_banner_title("GTA 6: Se cae un avión entero") == (
        "SE CAE UN",
        "AVIÓN ENTERO",
    )
    assert rubius.split_banner_title("") == (None, None)
    assert rubius.split_banner_title("Cinema") == ("CINEMA", None)
    # Sin "GTA 6" delante y sin dos puntos colgando en la línea 1.
    line1, _line2 = rubius.split_banner_title("GTA 6: El chat: Cinema")
    assert "GTA" not in line1
    assert not line1.endswith(":")


def test_load_rubius_recipe_real_config():
    recipe = rubius.load_rubius_recipe(_real_config())
    assert recipe["cam"] == {"x": 0, "y": 0, "w": 1080, "h": 840}
    assert recipe["game"] == {"y0": 840, "y1": 1920}
    assert recipe["title_banner"] == {
        "x0": 103,
        "x1": 967,
        "y0": 811,
        "y1": 1099,
    }
    assert recipe["subs_zone"] == {"y0": 900, "y1": 1020}
    assert recipe["subs_over_game"] is True
    assert recipe["cam_full_width"] is True
    assert recipe["blurred_bg"] is False
    # Las constantes del módulo replican la receta.
    assert (rubius.CAM_W, rubius.CAM_H) == (
        recipe["cam"]["w"],
        recipe["cam"]["h"],
    )
    assert rubius.GAME_Y == recipe["game"]["y0"]
    assert rubius.GAME_H == recipe["game"]["y1"] - recipe["game"]["y0"]
    assert (
        rubius.BANNER_X0,
        rubius.BANNER_X1,
        rubius.BANNER_Y0,
        rubius.BANNER_Y1,
    ) == (
        recipe["title_banner"]["x0"],
        recipe["title_banner"]["x1"],
        recipe["title_banner"]["y0"],
        recipe["title_banner"]["y1"],
    )
    assert (rubius.SUBS_MARGIN_V, rubius.SUBS_MARGIN_V + 120) == (
        recipe["subs_zone"]["y0"],
        recipe["subs_zone"]["y1"],
    )


def test_load_rubius_recipe_falla_fuerte():
    from clipagent.validate import ConfigError

    with pytest.raises(ConfigError):
        rubius.load_rubius_recipe({})
    with pytest.raises(ConfigError):
        rubius.load_rubius_recipe({"templates": {}})
    bad = {"templates": {"rubius": {"cam": {"x": 0}}}}
    with pytest.raises(ConfigError) as exc:
        rubius.load_rubius_recipe(bad)
    assert "templates.rubius" in str(exc.value)
    # Todas las claves requeridas están en la receta real.
    recipe = rubius.load_rubius_recipe(_real_config())
    for key in TEMPLATE_REQUIRED_KEYS["rubius"]:
        assert key in recipe


def test_split_banner_title_largo_completo():
    """Corrección del usuario 2026-11-19: el título sale COMPLETO
    en el banner, nunca cortado. 'Risa imparable por un
    asesinato' se parte en dos líneas equilibradas a 64 px."""
    assert rubius.split_banner_title("Risa imparable por un asesinato") == (
        "RISA IMPARABLE",
        "POR UN ASESINATO",
    )
    lines, size = rubius._banner_fit("Risa imparable por un asesinato")
    assert size == 64
    assert " ".join(lines) == "RISA IMPARABLE POR UN ASESINATO"
    # Cada línea cabe en el ancho útil del banner (760 px).
    # La fuente se resuelve por la misma búsqueda que el render
    # (_ARIAL_BLACK_PATHS: ~/.fonts, /usr/share/fonts); la ruta
    # absoluta /home/hatch no existe en CI (OSError).
    font = rubius._banner_font(size)
    for ln in lines:
        assert font.getlength(ln) <= rubius.BANNER_MAX_W


def test_make_banner_png_titulo_largo_completo(tmp_path):
    """El banner del título largo muestra las dos líneas enteras:
    negro arriba y rojo abajo, dentro del banner."""
    out = str(tmp_path / "banner_largo.png")
    rubius.make_banner_png("Risa imparable por un asesinato", out)
    assert os.path.isfile(out)
    from PIL import Image
    import numpy as np

    rgba = np.array(Image.open(out).convert("RGBA")).astype(int)
    opaque = rgba[:, :, 3] > 128
    r, g, b = rgba[:, :, 0], rgba[:, :, 1], rgba[:, :, 2]
    black = opaque & (r < 60) & (g < 60) & (b < 60)
    red = opaque & (r > 150) & (g < 100) & (b < 100)
    assert int(black.sum()) > 500
    assert int(red.sum()) > 500
    black_rows = np.where(black.any(axis=1))[0]
    red_rows = np.where(red.any(axis=1))[0]
    assert black_rows.max() < red_rows.max()
    # El texto queda dentro del banner (no desborda por abajo).
    assert red_rows.max() < rubius.BANNER_H


def test_make_banner_png(tmp_path):
    out = str(tmp_path / "banner.png")
    rubius.make_banner_png('GTA 6: El chat: "Cinema"', out)
    assert os.path.isfile(out)
    from PIL import Image
    import numpy as np

    rgba = np.array(Image.open(out).convert("RGBA")).astype(int)
    assert rgba.shape[0] == rubius.BANNER_H
    assert rgba.shape[1] == rubius.BANNER_W
    # Solo píxeles opacos: fuera del rectángulo redondeado el fondo
    # es transparente (alfa 0) y al convertir a RGB se vuelve negro.
    opaque = rgba[:, :, 3] > 128
    r, g, b = rgba[:, :, 0], rgba[:, :, 1], rgba[:, :, 2]
    # Relleno amarillo presente y borde blanco presente.
    yellow = opaque & (r > 200) & (g > 180) & (b < 120)
    assert int(yellow.sum()) > 50000
    white = opaque & (r > 240) & (g > 240) & (b > 240)
    assert int(white.sum()) > 1000
    # Las dos líneas: negro arriba y rojo abajo.
    black = opaque & (r < 60) & (g < 60) & (b < 60)
    red = opaque & (r > 150) & (g < 100) & (b < 100)
    assert int(black.sum()) > 500
    assert int(red.sum()) > 500
    # El negro está arriba del rojo (línea 1 negra, línea 2 roja).
    black_rows = np.where(black.any(axis=1))[0]
    red_rows = np.where(red.any(axis=1))[0]
    assert black_rows.max() < red_rows.max()
    assert black_rows.min() < red_rows.min()
    # El bloque de texto queda centrado en el banner (márgenes
    # arriba/abajo parecidos, no cargado abajo).
    assert 30 <= black_rows.min() <= 90
    assert 200 <= red_rows.max() <= 280


def test_split_block_words():
    words = rubius.split_block_words(1.12, 1.98, "¿Cuál será el botón")
    assert [w for w, _s, _e in words] == ["¿Cuál", "será", "el", "botón"]
    # Contiguo y con el tramo total del bloque.
    assert words[0][1] == pytest.approx(1.12)
    assert words[-1][2] == pytest.approx(1.98)
    for (_w, s, e), (_w2, s2, _e2) in zip(words, words[1:]):
        assert e == pytest.approx(s2)
    # Piso de legibilidad: toma de las largas del mismo bloque.
    words = rubius.split_block_words(0.0, 1.0, "a bb cccc")
    shares = [round(e - s, 2) for _w, s, e in words]
    assert shares == [0.22, 0.29, 0.49]
    assert rubius.split_block_words(0.0, 1.0, "") == []
    assert rubius.split_block_words(0.0, 1.0, "   ") == []


def test_make_word_ass_real_srt():
    if not os.path.isfile(SRT_P3220):
        pytest.skip("no está el SRT de p3220 en el repo")
    from clipagent.transcribe import parse_srt

    blocks = parse_srt(SRT_P3220)
    assert len(blocks) == 32
    ass = rubius.make_word_ass(blocks, max_end=48.0)
    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass
    # Arial Black 100 px reales -> Fontsize 141; contorno 10 -> 14;
    # Alignment 8 (arriba) y MarginV 900 (y 900-1020).
    assert ",141," in ass
    assert ",14," in ass
    assert ",8,10,10,900,1" in ass
    # Blanca con contorno negro (sin amarillo: rubius no es willyrex).
    assert "&H00FFFFFF" in ass
    assert "&H00000000" in ass
    assert "&H00FFFF00" not in ass
    dialogues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    # Una palabra por evento; el texto es el del SRT, sin inventar.
    # Con banner (default) las 8 primeras palabras no se emiten:
    # caen en la ventana del banner (0,5-3,5 s) y taparian el
    # titulo ("de" sobre "CINEMA" en la muestra del clip 7).
    assert len(dialogues) == 94
    assert dialogues[0].split(",")[1] == "0:00:04.78"
    assert "gente?" in dialogues[0]
    sin_banner = rubius.make_word_ass(blocks, max_end=48.0, banner_show=None)
    assert (
        len(
            [
                ln
                for ln in sin_banner.splitlines()
                if ln.startswith("Dialogue:")
            ]
        )
        == 102
    )
    assert "¿Cuál" in sin_banner
    # Ningún evento fuera del clip.
    for ln in dialogues:
        start = ln.split(",")[1]
        assert start < "0:00:48.00"


def test_make_word_ass_sin_palabras_bajo_el_banner():
    """La palabra dicha mientras el banner esta visible no se
    emite: en la muestra del clip 7 la palabra "de" se imprimio
    ENCIMA de "CINEMA" (el banner ocupa y 811-1099 y la zona de
    subtitulos y 900-1020 cae dentro). En el reel de referencia
    no hay palabra sobre el banner."""
    ass = rubius.make_word_ass(
        [(0.0, 0.4, "cero"), (1.0, 2.0, "dentro"), (4.0, 5.0, "fuera")]
    )
    assert "cero" in ass
    assert "dentro" not in ass
    assert "fuera" in ass


def test_make_word_ass_sin_banner_no_suprime_palabras():
    """Sin banner (banner_show=None) ninguna palabra se suprime."""
    ass = rubius.make_word_ass(
        [(0.0, 0.4, "cero"), (1.0, 2.0, "dentro"), (4.0, 5.0, "fuera")],
        banner_show=None,
    )
    assert "cero" in ass
    assert "dentro" in ass
    assert "fuera" in ass


def test_make_word_ass_from_transcript():
    transcript = {
        "segments": [
            {"start": 1.0, "end": 3.0, "text": "hola mundo"},
            {"start": 5.0, "end": 5.5, "text": ""},
        ]
    }
    ass = rubius.make_word_ass_from_transcript(transcript)
    dialogues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    # Las dos palabras caen en la ventana del banner (1,0-3,0 s
    # dentro de 0,5-3,5 s): con banner no se emiten ninguna.
    assert len(dialogues) == 0
    sin_banner = rubius.make_word_ass_from_transcript(
        transcript, banner_show=None
    )
    dialogues = [
        ln for ln in sin_banner.splitlines() if ln.startswith("Dialogue:")
    ]
    assert len(dialogues) == 2
    assert "hola" in dialogues[0] and "mundo" in dialogues[1]


def test_build_filter_geometria():
    filt, vlabel = rubius.build_filter(
        "561:365:0:0",
        "365:365:90:0",
        (16, 26),
        48.0,
        "subs.ass",
        "banner.png",
        30,
        # Geometria, no efectos: con los efectos por defecto el
        # banner se desliza y el vlabel final es [vimp]; eso lo
        # cubren test_build_filter_effects_* (rama).
        shake=False, impact=False, banner_slide=False, fade_in=False,
    )
    # Cámara 1080x840 arriba y gameplay cuadrado abajo, sin bandas.
    assert "scale=1080:840" in filt
    assert "crop=1440:1440:560:0" in filt
    assert "pad=1080:1920:0:840" in filt
    assert "overlay=0:0" in filt
    # Banner a caballo del corte, solo en BANNER_SHOW.
    assert "overlay=103:811" in filt
    assert "between(t,0.5,3.5)" in filt
    # Punch-in 1,18x en 16-26 s y ASS al final.
    assert "enable='gte(t,16)*lt(t,26)'" in filt
    assert "ass=subs.ass" in filt
    assert vlabel == "[vsub]"
    # Sin banner ni ASS: ni entrada [1:v] ni quemado.
    filt2, vlabel2 = rubius.build_filter(
        "561:365:0:0", "365:365:90:0", None, 48.0, None, None, 30,
        shake=False, impact=False, banner_slide=False, fade_in=False,
    )
    assert "[1:v]" not in filt2
    assert "ass=" not in filt2
    assert vlabel2 == "[step2]"


def test_build_render_cmd_entradas():
    cmd = rubius.build_render_cmd(
        "seg.mp4", 0, 48.0, "subs.ass", "out.mp4", "banner.png"
    )
    # Banner como segunda entrada en loop; audio del segmento.
    assert cmd.count("-i") == 2
    assert "-map" in cmd and "0:a" in cmd
    cmd2 = rubius.build_render_cmd(
        "seg.mp4", 0, 48.0, "subs.ass", "out.mp4", None
    )
    assert cmd2.count("-i") == 1


def test_resolve_split_variant_rubius():
    cfg = _real_config()
    # Default de config.yaml: templates.default = rubius.
    assert cfg["templates"]["default"] == "rubius"
    assert resolve_split_variant(cfg) == "rubius"
    assert resolve_split_variant({"split_variant": "rubius"}) == "rubius"
    assert resolve_split_variant({"split_variant": "rdjavi"}) == "rdjavi"
    assert resolve_split_variant({"split_variant": "plain"}) == "plain"
    assert resolve_split_variant({"split_variant": "djmariio"}) == "rdjavi"
    # Sin sección templates: se carga config.yaml en disco (única
    # fuente, mismo patrón que load_validator_config); hoy el
    # default es rubius. Sin defaults silenciosos a rdjavi.
    assert resolve_split_variant({}) == "rubius"
    assert resolve_split_variant({"templates": {}}) == "rubius"


def test_template_y_estilo_rubius_en_config():
    cfg = _real_config()
    assert template_for_variant("rubius") == "rubius"
    assert template_for_variant("rdjavi") == "willyrex"
    assert cfg["subtitles"]["default_style"] == "rubius"
    style = get_subtitle_style(cfg, "rubius")
    assert style["font_color"] == "#FFFFFF"
    assert style["fontsize"] == 100
    assert style["margin_v"] == 900
    assert style["border_width"] == 10


def test_split_rubius_graph_pipeline():
    params = {"box": [0, 0, 35, 23], "split_variant": "rubius"}
    graph = _split_rubius_graph("0:v", "vout", params, 2560, 1440, {})
    assert "scale=1080:840" in graph
    assert "crop=1440:1440:560:0" in graph
    assert "pad=1080:1920:0:840" in graph
    assert "format=yuv420p[vout]" in graph
    # El dispatch de segment_filter_graph llega al grafo rubius.
    graph2 = segment_filter_graph(
        "0:v", "vout", "split", params, 2560, 1440, {}
    )
    assert "scale=1080:840" in graph2
    # Y el de willyrex sigue intacto.
    params_w = {"box": [0, 0, 35, 23], "split_variant": "rdjavi"}
    graph3 = segment_filter_graph(
        "0:v", "vout", "split", params_w, 2560, 1440, {}
    )
    assert "scale=1080:702" in graph3


# ---- Tests de la rama circleci-project-setup (efectos extra) ----
# No duplican los de main: cubren el punch-zoom marcado, el shake,
# el impacto, el banner deslizante, el fundido y el whoosh, mas el
# e2e sintetico que demuestra que el grafo con efectos CORRE.


def test_word_ass_one_dialogue_per_word(tmp_path):
    blocks = [(5.0, 6.0, "vamos alla"), (8.0, 9.0, "gol")]
    ass = rubius.make_word_ass(blocks, max_end=12.0)
    assert ass.count("Dialogue:") == 3  # vamos, alla, gol
    assert "vamos" in ass and "gol" in ass


def test_build_filter_effects_default_on():
    filt, vlabel = rubius.build_filter(
        "561:365:0:0", "365:365:90:0", (16, 26), 48.0,
        "subs.ass", "banner.png", 30)
    assert vlabel == "[vimp]"
    assert "fade=t=in:st=0:d=0.3" in filt
    assert "sin(2*PI*9*t)" in filt  # shake
    assert "eq=brightness=" in filt  # impacto
    assert "if(between(t,0.5,0.7)" in filt  # banner deslizante


def test_build_filter_effects_can_be_disabled():
    filt, vlabel = rubius.build_filter(
        "561:365:0:0", "365:365:90:0", (16, 26), 48.0,
        "subs.ass", "banner.png", 30,
        shake=False, impact=False, banner_slide=False, fade_in=False)
    assert "fade=t=in" not in filt
    assert "sin(2*PI*9*t)" not in filt
    assert "eq=brightness=" not in filt
    assert "overlay=103:811:" in filt  # banner fijo
    assert vlabel == "[vsub]"


def test_build_filter_punch_zoom_strong():
    filt_default, _ = rubius.build_filter(
        "561:365:0:0", "365:365:90:0", (16, 26), 48.0,
        "subs.ass", None, 30)
    filt_strong, _ = rubius.build_filter(
        "561:365:0:0", "365:365:90:0", (16, 26), 48.0,
        "subs.ass", None, 30, punch_zoom=1.30)
    assert filt_default != filt_strong
    # El crop del punch con 1,30 es más cerrado que con 1,18.
    assert "crop=" in filt_strong


def test_build_render_cmd_is_argv():
    cmd = rubius.build_render_cmd(
        "seg.mp4", 0, 12.0, "subs.ass", "out.mp4",
        banner_png="banner.png")
    assert cmd[0] == "ffmpeg"
    assert "-filter_complex" in cmd
    assert cmd[-1] == "out.mp4"


def _make_synthetic_segment(tmp_path, duration=12.0, payoff=6.0):
    """Segmento 1920x1080 testsrc + seno con un segundo entero de
    silencio en el payoff (para que decide_sfx ponga el boom:
    el dip se mide en el segundo del payoff) + SRT a mano."""
    seg = tmp_path / "segment.mp4"
    # Audio: seno -20 dB con silencio de 6,0 a 7,0 s (dip >= 6 dB).
    # Video: testsrc2 + una caja blanca que barre el fotograma
    # (overlay, x = mod(t*400, 1920), y=400 dentro de la caja de
    # cámara del e2e): entre fotogramas a 2 s la caja se mueve 800 px,
    # así que la zona de cámara cambia mucho (banda_viva del gate)
    # con margen, sin depender del jitter de codificación. Sin ella
    # los diffs quedaban en 1,1-2,6, al borde del umbral 2,0 (0,4-0,8
    # según la corrida). La caja no toca el banner (y 811+) ni los
    # subtítulos (abajo). OJO: la caja va con `overlay`, NO con
    # `drawbox` (en este ffmpeg las expresiones de drawbox con `t`/`n`
    # no dibujan nada / fallan); y la coma del mod va escapada `\,`
    # dentro de -filter_complex.
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30:duration={duration}",
        "-f", "lavfi", "-i",
        f"sine=frequency=440:duration={duration}",
        "-f", "lavfi", "-i",
        f"color=white:size=200x200:duration={duration}",
        "-filter_complex",
        "[0:v][2:v]overlay=x='mod(t*400\\,1920)':y=400[v];"
        "[1:a]volume='if(between(t,6.0,7.0),0,1)':eval=frame[a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-t", str(duration), str(seg),
    ]
    subprocess.run(cmd, check=True)
    srt = tmp_path / "segment.srt"
    # Bloques densos y a mitad de vuelo en los instantes muestreados
    # por el gate (t=5,7,9,11): el bloque de 1-2 s se suprime por el
    # banner y no se pone (crearía un instante activo sin palabra).
    srt.write_text(
        "1\n00:00:04,200 --> 00:00:05,800\nvamos alla\n\n"
        "2\n00:00:06,500 --> 00:00:07,600\nque locura\n\n"
        "3\n00:00:08,400 --> 00:00:09,700\ngolazo total\n\n"
        "4\n00:00:10,200 --> 00:00:11,400\nincreible\n",
        encoding="utf-8")
    return str(seg), str(srt)


def _make_ass(tmp_path, srt, duration=12.0):
    """ASS palabra por palabra desde el SRT (render() recibe un
    ASS, no un SRT: libass no lee SRT)."""
    from clipagent.transcribe import parse_srt
    blocks = parse_srt(srt)
    ass_text = rubius.make_word_ass(
        blocks, max_end=duration, banner_show=rubius.BANNER_SHOW)
    ass_path = tmp_path / "subs_rubius.ass"
    ass_path.write_text(ass_text, encoding="utf-8")
    return str(ass_path)


def test_render_e2e_synthetic_with_effects(tmp_path):
    """El grafo con shake/impact/slide/fade CORRE en ffmpeg y el
    MP4 final es 1080x1920 con la duración pedida."""
    seg, srt = _make_synthetic_segment(tmp_path)
    cfg = _real_config()
    out = tmp_path / "clip_rubius.mp4"
    # render() no genera el banner: se hace aquí, como el driver.
    banner = tmp_path / "banner.png"
    rubius.make_banner_png("GTA 6: Prueba sintética", str(banner))
    ass = _make_ass(tmp_path, srt)
    dt, final = rubius.render(
        seg, 0, 12.0, ass, str(out),
        banner_png=str(banner), cam_box="800:450:560:315",
        cam_square="450:450:735:315", punch_in=None,
        game_crop="1080:1080:420:0",
        cfg=cfg, sfx_srt=srt, payoff_s=6.0,
        punch_zoom=1.30, shake=True, impact=True,
        banner_slide=True, fade_in=True, whoosh=False,
    )
    assert os.path.isfile(final) and os.path.getsize(final) > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-show_entries", "format=duration",
         "-of", "csv=p=0", final],
        capture_output=True, text=True, check=True)
    lines = [ln for ln in probe.stdout.strip().splitlines() if ln]
    assert lines[0].strip() == "1080,1920"
    assert abs(float(lines[1]) - 12.0) < 0.5
    assert dt > 0


def test_render_whoosh_goes_through_apply_sfx(tmp_path, monkeypatch):
    """El whoosh se pide en la MISMA mezcla SFX que boom/riser
    (extra_events de apply_sfx), no en un segundo pase."""
    seen = {}

    def fake_apply_sfx(video, **kw):
        seen.update(kw)
        return video, {"enabled": True, "events": kw.get("extra_events", [])}

    monkeypatch.setattr("clipagent.workers.apply_sfx", fake_apply_sfx)
    seg, srt = _make_synthetic_segment(tmp_path)
    cfg = _real_config()
    out = tmp_path / "clip_rubius.mp4"
    banner = tmp_path / "banner.png"
    rubius.make_banner_png("GTA 6: Prueba sintética", str(banner))
    ass = _make_ass(tmp_path, srt)
    rubius.render(
        seg, 0, 12.0, ass, str(out), banner_png=str(banner),
        cam_box="800:450:560:315", cam_square="450:450:735:315",
        punch_in=(5.0, 8.0), game_crop="1080:1080:420:0",
        cfg=cfg, sfx_srt=srt, payoff_s=6.0,
        whoosh=True,
    )
    extra = seen.get("extra_events") or []
    assert any(e["effect"] == "whoosh" for e in extra)
    # Sin punch-in no hay whoosh (no hay momento que barrer).
    seen.clear()
    rubius.render(
        seg, 0, 12.0, ass, str(tmp_path / "clip2.mp4"),
        banner_png=str(banner), cam_box="800:450:560:315",
        cam_square="450:450:735:315", punch_in=None,
        game_crop="1080:1080:420:0",
        cfg=cfg, sfx_srt=srt, payoff_s=6.0, whoosh=True,
    )
    assert seen.get("extra_events") == []
