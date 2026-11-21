"""Tests de la plantilla RUBIUS (clipagent/rubius.py) en la rama
circleci-project-setup.

Receta: camara 1080x840 arriba, gameplay 840-1920 sin bandas,
banner amarillo transitorio (103-967, 811-1099) y subtitulos
palabra por palabra blancos sobre el gameplay (y 900-1020).
Efectos extra (2026-11-21): punch-in marcado (1,30), shake en
el payoff, destello de impacto, banner deslizante, fundido de
entrada y whoosh al arrancar el punch-in.

El e2e sintético construye un segmento 1920x1080 con testsrc +
audio con un dip de silencio en el payoff (para que decide_sfx
ponga el boom) y un SRT hecho a mano: demuestra que el grafo
con los efectos nuevos CORRE en ffmpeg (shake/impact/slide/fade
son expresiones que se rompen fácil) y que el MP4 final es
1080x1920 con la duración pedida.
"""

import os
import subprocess

from clipagent import rubius
from clipagent.config import TEMPLATE_REQUIRED_KEYS, load_config

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.yaml")


def _real_config():
    return load_config(CONFIG_PATH)


def test_split_banner_title():
    assert rubius.split_banner_title('GTA 6: El chat: "Cinema"') == (
        "EL CHAT",
        "CINEMA",
    )


def test_recipe_loads_from_config():
    cfg = _real_config()
    recipe = rubius.load_rubius_recipe(cfg)
    assert recipe["subs_style"] == "word_white"
    assert recipe["cam"] == {"x": 0, "y": 0, "w": 1080, "h": 840}
    for key in TEMPLATE_REQUIRED_KEYS["rubius"]:
        assert key in recipe


def test_make_banner_png(tmp_path):
    out = tmp_path / "banner.png"
    rubius.make_banner_png("GTA 6: Prueba de banner", str(out))
    assert out.is_file() and out.stat().st_size > 0
    from PIL import Image
    with Image.open(out) as im:
        assert im.size == (rubius.BANNER_W, rubius.BANNER_H)


def test_word_ass_one_dialogue_per_word(tmp_path):
    blocks = [(5.0, 6.0, "vamos alla"), (8.0, 9.0, "gol")]
    ass = rubius.make_word_ass(blocks, max_end=12.0)
    assert ass.count("Dialogue:") == 3  # vamos, alla, gol
    assert "vamos" in ass and "gol" in ass


def test_word_ass_skips_words_under_banner():
    # Palabras en la ventana del banner (0,5-3,5) no se emiten:
    # taparían el título.
    blocks = [(1.0, 2.0, "hola mundo"), (5.0, 6.0, "vamos")]
    ass = rubius.make_word_ass(blocks, max_end=12.0)
    assert "hola" not in ass and "mundo" not in ass
    assert "vamos" in ass


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
