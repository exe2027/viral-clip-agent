#!/usr/bin/env python3
"""Tests de la plantilla comun RDJavi (clipagent/rdjavi.py).

La plantilla del clip (runs/2932415021-p3220/rdjavi/render_rdjavi.sh,
opcion C) se migro aqui el 2026-11-18. Lo que estos tests fijan:
  * punch-in con doble rama + overlay enable, sin trim ni concat;
  * la fuente de color lleva duracion (sin ella ffmpeg no termina);
  * la salida lleva -t como seguro;
  * el ASS karaoke sale ABAJO DEL TODO en la barra negra
    (Alignment 2, MarginV 12, y 1740..1920), nunca sobre el
    gameplay (correccion Willyrex del usuario 2026-11-18 20:13);
  * el titulo quemado va en la franja negra entre la webcam y el
    gameplay, centrado verticalmente, en UNA SOLA LINEA y sin
    "GTA 6" delante (revision v2 2026-11-18 21:01);
  * la webcam llena la banda superior de lado a lado (cover
    crop, sin barras negras) y el gameplay lleva zoom suave
    en el payoff (revision v2).

Estilo pytest (funciones test_*). En esta VM no hay pytest: se
pueden correr con `python3 test_rdjavi.py`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.rdjavi import (  # noqa: E402
    BG_H,
    CAM_H,
    CAM_W,
    GAME_BOTTOM,
    GAME_H,
    GAME_Y,
    SUBS_ALIGN,
    SUBS_FONT_SIZE,
    SUBS_MARGIN_V,
    TITLE_BAND_H,
    TITLE_BAND_Y0,
    TITLE_BAND_Y1,
    TITLE_FONT_SIZE,
    TITLE_LINE_H,
    _game_filter,
    add_burned_title,
    build_filter,
    build_render_cmd,
    cam_cover_crop,
    clean_title_prefix,
    fit_title,
    make_karaoke_ass,
    make_plain_ass,
    punch_cover_crop,
    wrap_title,
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
    filt, _ = build_filter("561:365:0:0", "365:365:90:0", (16, 26),
                           48.01, "subs.ass", 30)
    assert "enable='lt(t,16)+gte(t,26)'" in filt
    assert "enable='gte(t,16)*lt(t,26)'" in filt
    assert "concat=n=3" not in filt
    assert "trim=start" not in filt


def test_build_filter_color_con_duracion():
    """El color de fondo lleva d=duration: sin el, ffmpeg no cierra
    nunca (cola congelada de 193 s en p3220)."""
    filt, _ = build_filter("561:365:0:0", "365:365:90:0", (16, 26),
                           48.01, "subs.ass", 30)
    assert "color=c=black:s=1080x1920:r=30:d=48.01" in filt


def test_build_filter_cam_cover_a_ancho_completo():
    """Revision v2 (2026-11-18 21:01): la webcam llena la banda
    superior de lado a lado (cover crop del rectangulo PiP,
    1080x702, sin barras negras ni cuadrado centrado). Con el
    recorte por defecto de p3220 el cover es 561:364:0:0 y el
    punch-in 1,30x centrado en la cara es 430:280:57:42 (nunca
    el exacto del cuadrado viejo 310:310:125:27)."""
    filt, _ = build_filter("561:365:0:0", "365:365:90:0", (16, 26),
                           48.01, "subs.ass", 30)
    assert "crop=561:364:0:0" in filt
    assert "crop=430:280:57:42" in filt
    assert "scale=1080:702" in filt
    assert "overlay=0:0" in filt
    assert "crop=310:310:125:27" not in filt
    assert "crop=365:365:90:0" not in filt
    assert CAM_W == 1080
    assert CAM_H == 702


def test_cam_cover_crop_recorta_el_lado_largo():
    """Cover: si la caja es mas ancha que la banda, recorta
    ancho; si es mas alta, recorta alto. Centrado en ambos."""
    assert cam_cover_crop("561:365:0:626") == "561:364:0:626"
    assert cam_cover_crop("800:365:0:0") == "560:365:120:0"
    assert cam_cover_crop("561:500:0:0") == "561:364:0:68"


def test_punch_cover_crop_centrado_en_la_cara():
    """El punch-in es un zoom 1,30x centrado en la cara y
    siempre dentro del recorte cover."""
    assert punch_cover_crop("561:364:0:626", 272, 808) == "430:280:57:668"
    assert punch_cover_crop("561:364:0:0", 272, 182) == "430:280:57:42"


def test_build_render_cmd_t_en_salida():
    """La salida lleva -t duration como seguro (ademas del -t de
    entrada y del -t del loop del fondo)."""
    cmd = build_render_cmd("seg.mp4", 0, 48.01, "subs.ass",
                           "out.mp4", "bg.png")
    assert cmd[-3:] == ["-t", "48.01", "out.mp4"]
    assert cmd.count("-t") == 3


def test_geometria_willyrex_sin_solapes():
    """Layout v2 2026-11-18 21:01: webcam 0..702 a ancho completo,
    franja del titulo 702..800 (98 px, una sola linea),
    gameplay 800..1740, barra de subtitulos 1740..1920. La
    franja termina donde empieza el gameplay y el karaoke de
    2 lineas (tope ~1756) queda debajo del gameplay."""
    assert BG_H == 702
    assert GAME_Y == 800
    assert GAME_H == 940
    assert GAME_BOTTOM == 1740
    assert TITLE_BAND_Y0 == 702
    assert TITLE_BAND_Y1 == GAME_Y
    assert TITLE_BAND_H == 98
    assert SUBS_FONT_SIZE == 64
    assert TITLE_FONT_SIZE == 64
    karaoke_2lineas_tope = 1920 - SUBS_MARGIN_V - 2 * TITLE_LINE_H
    assert karaoke_2lineas_tope > GAME_BOTTOM


def test_make_karaoke_ass_abajo_del_todo():
    """El ASS karaoke de la plantilla va ABAJO DEL TODO en la
    barra negra (Alignment 2, MarginV 12, y 1740..1920), con
    palabra por palabra y la fuente de la plantilla. Nunca sobre
    el gameplay: ahi van los subtitulos propios de GTA 6."""
    ass = make_karaoke_ass([(1.12, 5.18, "hola mundo")], KARAOKE_STYLE)
    assert SUBS_ALIGN == 2
    assert SUBS_MARGIN_V == 12
    assert "Style: Karaoke,Arial Black,56" in ass
    assert ",0,2,10,10,12,1" in ass
    assert "{\\k" in ass
    assert "hola" in ass


def test_clean_title_prefix_quita_gta6_inicial():
    """Nunca 'GTA 6' delante del titulo (regla del usuario
    2026-11-18 21:01): se limpia el prefijo inicial con o sin
    dos puntos/guion, en cualquier capitalizacion. Un 'GTA 6'
    en medio del titulo no se toca."""
    assert clean_title_prefix("GTA 6: Rubius descubre") == "Rubius descubre"
    assert clean_title_prefix("GTA 6 - cromos") == "cromos"
    assert clean_title_prefix("GTA6: cromos") == "cromos"
    assert clean_title_prefix("gta 6: cromos") == "cromos"
    assert clean_title_prefix("Rubius GTA 6 cromos") == "Rubius GTA 6 cromos"
    assert clean_title_prefix("  GTA 6:  cromos  ") == "cromos"


def test_fit_title_una_sola_linea_64px():
    """Revision v2: el titulo de p7775 queda en UNA linea a
    64 px, sin 'GTA 6' y acortado ('Rubius descubre los
    cromos'): a 68 px solo caben 3 palabras, a 64 px caben 4."""
    assert fit_title("GTA 6: Rubius descubre los cromos coleccionables") == (
        "Rubius descubre los cromos", 64)
    assert fit_title("GTA 6: cromos") == ("cromos", 68)
    assert fit_title("") == (None, None)


def test_wrap_title_una_sola_linea_sin_gta6():
    """wrap_title devuelve [linea] unica, nunca dos: el titulo
    de p7775 se acorta a una linea y pierde el 'GTA 6'."""
    assert wrap_title("GTA 6: Rubius descubre los cromos coleccionables") == [
        "Rubius descubre los cromos"]
    assert wrap_title("GTA 6: cromos") == ["cromos"]
    assert wrap_title("") == []


def test_wrap_title_largo_se_acorta_a_una_linea():
    """Titulo imposible en una linea: se acorta por palabras,
    nunca se parte en dos ni se achica la letra."""
    lines = wrap_title("GTA 6 Rubius descubre los cromos coleccionables "
                       "legendarios que se venden por un monton de pasta")
    assert len(lines) == 1
    assert lines[0] == "Rubius descubre los cromos"
    assert "GTA 6" not in lines[0]


def test_add_burned_title_en_franja_entre_webcam_y_gameplay():
    """El titulo quemado va en UNA SOLA LINEA, centrado
    verticalmente en la franja negra entre la webcam y el
    gameplay (y 702..800, MarginV 714 a 64 px), dura todo el
    clip y no toca los eventos karaoke. Sin 'GTA 6' delante."""
    ass = make_karaoke_ass([(1.12, 5.18, "hola mundo")], KARAOKE_STYLE)
    titled = add_burned_title(
        ass, "GTA 6: Rubius descubre los cromos coleccionables", 48.02)
    # 64 px REALES: el ASS lleva Fontsize 90 y Outline 7
    # (= 64/5 px * 1,4102, ver ASS_FONT_SCALE).
    assert "Style: Title,Arial Black,90" in titled
    # 1 linea a 64 px: MarginV = 702 + (98 - 73)//2 = 714.
    assert ",1,7,0,8,10,10,714,1" in titled
    assert ("Dialogue: 0,0:00:00.00,0:00:48.02,Title,,0,0,0,,"
            "Rubius descubre los cromos" in titled)
    assert "\\N" not in titled.split(",Title,,0,0,0,,")[1].split("\n")[0]
    assert "GTA 6" not in titled.split(",Title,,0,0,0,,")[1]
    assert "{\\k" in titled  # karaoke intacto
    assert titled.count("Style: Title,") == 1


def test_make_karaoke_ass_con_titulo_por_defecto():
    """make_karaoke_ass con titulo lo quema (una linea, sin
    'GTA 6'); sin titulo no hay estilo Title."""
    ass = make_karaoke_ass([(1.12, 5.18, "hola mundo")], KARAOKE_STYLE,
                           title="GTA 6: cromos", duration=48.02)
    assert "Style: Title," in ass
    assert "Dialogue: 0,0:00:00.00,0:00:48.02,Title,,0,0,0,,cromos" in ass
    sin = make_karaoke_ass([(1.12, 5.18, "hola mundo")], KARAOKE_STYLE)
    assert "Style: Title," not in sin


def test_make_plain_ass_amarillo_fijo_abajo_del_todo():
    """Subtitulos fijos amarillos estilo Willyrex (correccion
    2026-11-18 20:13, tamano subido a 64 px en la revision v2
    2026-11-18 21:01): todo el texto amarillo, contorno negro
    grueso (6), Alignment 2, MarginV 12, sin etiquetas \\k. El
    karaoke (resto blanco) no es como la captura."""
    ass = make_plain_ass([(1.12, 5.18, "hola mundo"),
                          (6.0, 8.0, "segundo bloque")])
    # 64 px REALES: el ASS lleva Fontsize 90 y Outline 8
    # (= 64/6 px * 1,4102, ver ASS_FONT_SCALE).
    assert "Style: Subs,Arial Black,90,&H0000FFFF,&H0000FFFF" in ass
    assert ",1,8,0,2,10,10,12,1" in ass
    assert "{\\k" not in ass
    assert "Dialogue: 0,0:00:01.12,0:00:05.18,Subs,,0,0,0,,hola mundo" in ass
    # max_end recorta los bloques fuera del clip.
    corto = make_plain_ass([(1.0, 2.0, "dentro"),
                            (50.0, 51.0, "fuera")], max_end=48.04)
    assert "dentro" in corto
    assert "fuera" not in corto
    # Con titulo se quema en la franja, como el karaoke.
    con_titulo = make_plain_ass([(1.12, 5.18, "hola mundo")],
                                title="GTA 6: cromos", duration=48.04)
    assert "Style: Title," in con_titulo
    assert "Dialogue: 0,0:00:00.00,0:00:48.04,Title," in con_titulo
    assert "{\\k" not in con_titulo


def test_fuente_pesada_tipo_arial_black_titulo_y_subtitulos():
    """Tipografia del ejemplo del usuario (2026-11-18 20:33 y
    20:47): Arial Black GENUINA (Monotype v2.35, instalada en
    ~/.fonts) para el TITULO y los SUBTITULOS, los dos. La
    Noto Sans Black anterior era solo parecida y el usuario
    la rechazo: tiene que ser identica a la captura. En la
    revision v2 el titulo y los subtitulos van a 64 px (el
    karaoke sigue a 56 px, que lo fija su estilo)."""
    from clipagent.rdjavi import SUBS_FONT
    assert SUBS_FONT == "Arial Black"
    ass = make_plain_ass(
        [(1.12, 5.18, "hola mundo")],
        title="GTA 6: Rubius descubre los cromos coleccionables",
        duration=48.06)
    # 64 px REALES en titulo y subtitulos (Fontsize ASS 90).
    assert "Style: Subs,Arial Black,90" in ass
    assert "Style: Title,Arial Black,90" in ass
    karaoke = make_karaoke_ass([(1.12, 5.18, "hola mundo")],
                               KARAOKE_STYLE)
    assert "Style: Karaoke,Arial Black,56" in karaoke


def test_ass_font_size_calibracion_libass():
    """Calibracion libass (medido 2026-11-18 21:01): Fontsize
    ASS = px reales * 2888/2048 (span hhea de Arial Black).
    64 px reales -> Fontsize 90 (verificado en still: 980 px
    de ancho, PIL a 64 px: 990 px); 56 -> 79; 68 -> 96."""
    from clipagent.rdjavi import ASS_FONT_SCALE, ass_font_size
    assert ASS_FONT_SCALE == 2888 / 2048
    assert ass_font_size(64) == 90
    assert ass_font_size(56) == 79
    assert ass_font_size(68) == 96
    assert ass_font_size(5) == 7
    assert ass_font_size(6) == 8


def test_add_burned_title_idempotente_no_duplica():
    """Bug 2026-11-18 20:47: ASS que ya trae titulo + --title
    daba DOS eventos Title y libass apilaba el segundo SOBRE
    el gameplay por colision. add_burned_title no añade otro."""
    ass = make_plain_ass(
        [(1.12, 5.18, "hola mundo")],
        title="GTA 6: Rubius descubre los cromos coleccionables",
        duration=48.06)
    assert ass.count("Style: Title,") == 1
    assert ass.count(",Title,,0,0,0,,") == 1
    otra_vez = add_burned_title(
        ass, "GTA 6: Rubius descubre los cromos coleccionables",
        48.06)
    assert otra_vez == ass
    assert otra_vez.count("Style: Title,") == 1
    assert otra_vez.count(",Title,,0,0,0,,") == 1


def test_build_filter_cam_abajo_izquierda_p7775():
    """Bug 2026-11-18 20:47: en p7775 la webcam esta ABAJO a la
    izquierda (cam_box 561:365:0:626, cam_square 365:365:90:626).
    El re-render _black uso el default de p3220 (365:365:90:0)
    y la banda de la webcam mostro JUEGO (motor de lancha,
    valla) en vez de a Rubius. En v2 la webcam es cover a ancho
    completo: el filtro recorta la caja en y=626 (cover
    561:364:0:626) y el punch-in 1,30x centrado en la cara es
    430:280:57:668, NUNCA el exacto de p3220 ni el cuadrado."""
    filt, _ = build_filter("561:365:0:626", "365:365:90:626", (16, 26),
                           48.01, "subs.ass", 30)
    assert "crop=561:364:0:626" in filt
    assert "crop=430:280:57:668" in filt
    assert "scale=1080:702" in filt
    assert "crop=310:310:125:27" not in filt
    assert "crop=365:365:90:626" not in filt
    assert "crop=365:365:90:0" not in filt


def test_game_filter_zoom_suave_en_el_payoff():
    """Revision v2: el gameplay lleva zoom suave 1,15x en el
    payoff (18-24 s, rampa de 2 s) con scale eval=frame + crop
    con ventana animada en funcion de t (81*f en x, 141*f en
    y: centrado en horizontal, anclado abajo). Sin game_zoom,
    recorte fijo + escala estatica. El crop animado NO vale:
    w/h se congelan en la configuracion (t=NAN)."""
    f = _game_filter("1440:1252:620:94", (18, 24))
    assert f.startswith("crop=1440:1252:620:94,scale=")
    assert ":eval=frame," in f
    assert "1080*(1+0.15*if(between(t,18,20)" in f
    assert "crop=1080:940:'81*if(between(t,18,20)" in f
    assert "'141*if(between(t,18,20)" in f
    assert "clip(" not in f
    assert "min(" not in f
    assert _game_filter("1440:1252:620:94", None) == (
        "crop=1440:1252:620:94,scale=1080:940,setsar=1")
    filt, _ = build_filter("561:365:0:626", "365:365:90:626", (16, 26),
                           48.01, "subs.ass", 30)
    assert "eval=frame" in filt
    assert "overlay=0:800" in filt


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
