#!/usr/bin/env python3
"""Plantilla RUBIUS (GTA 6) — render de clips verticales 1080x1920.

Espejo de clipagent/rdjavi.py (plantilla willyrex v2) para la
plantilla rubius: corte limpio sin bandas, titulo en banner
amarillo transitorio y subtitulos palabra por palabra SOBRE el
gameplay. Default para renders nuevos desde 2026-11-19 (decision
del usuario: TODOS los clips se re-hacen a rubius).

Receta (medida por pixeles del reel de referencia, escalada a
1080x1920; el banner se midio fotograma a fotograma: visible
solo t=1-2 s del reel, ~2 s al inicio):
  * Camara ARRIBA a ANCHO COMPLETO: 1080x840 px (y=0..840),
    COVER del rectangulo PiP detectado por clip (params["box"],
    nunca un default silencioso: la caja varia por clip).
    Sin banda, sin fondo desenfocado, sin esquinas redondeadas.
  * Gameplay ABAJO: y=840..1920 (1080x1080), recorte cuadrado
    1440:1440 centrado (1440:1440:560:0 sobre 2560x1440: el
    minimapa abajo-izquierda queda fuera), corte limpio, SIN
    bandas. Zoom SUAVE 1,15x en el payoff (18-24 s, rampa de
    2 s, anclado abajo, como rdjavi).
  * TITULO en BANNER AMARILLO redondeado con borde blanco,
    x 103-967, y 811-1099 (a caballo del corte camara/gameplay),
    dos lineas centradas: linea 1 NEGRA, linea 2 ROJA, Arial
    Black. TRANSITORIO: solo t=0,5-3,5 s del clip (en el reel
    aparece al inicio, ~2 s), no todo el clip. El banner es un
    PNG pre-renderizado (PIL) que se compone con overlay
    enable=between(t,0.5,3.5).
  * SUBTITULOS palabra por palabra, UNA palabra a la vez,
    BLANCOS con contorno negro grueso, Arial Black 100 px
    reales, centrados, y 900-1020 (parte superior del gameplay),
    solo mientras se habla. Los subtitulos sobre el gameplay
    solo son correctos en rubius: en rdjavi/willyrex estan
    PROHIBIDOS (van en la barra negra 1740-1920). Las palabras
    salen de la transcripcion real (SRT o .transcript.json):
    nunca se inventa texto. La duracion de cada bloque se
    reparte entre sus palabras en proporcion a su longitud,
    con un piso de legibilidad (ver split_block_words).
  * Punch-in 1,18x en la reaccion (16-26 s del clip por
    defecto), centrado en la cara, con doble rama de camara +
    overlay enable (opcion C, como rdjavi). Con --punch-zoom
    1.30 es el marcado que usa el job de CircleCI.
  * Efectos extra (2026-11-21, pedido del usuario): shake de
    camara +-5 px a 9 Hz en el payoff, destello de brillo en
    el payoff, banner que entra deslizandose, fundido desde
    negro al inicio y whoosh de sonido al arrancar el punch-in
    (en la misma mezcla SFX que el boom/riser). Todos van por
    defecto; se apagan con --no-shake, --no-impact,
    --no-banner-slide, --no-fade-in, --no-whoosh.

Config de una sola fuente (regla del proyecto): la geometria
vive en config.yaml (seccion ``templates.rubius`` + estilo
``subtitle_styles.rubius``) y se carga al inicio de cada
entrada con load_rubius_recipe(); un valor requerido que
falte FALLA FUERTE (ConfigError con el nombre del valor),
nunca default silencioso. Las constantes de este modulo
replican la receta para el grafo y se validan contra ella
en los tests.

Uso (muestra del clip 7, p3220):
    python -m clipagent.rubius --segment runs/2932415021-p3220/segments/seg_01_t53m20s.mp4 \\
        --start 0 --duration 48 --srt runs/.../clip_01_t53m20s_s14.3_r7h4.srt \\
        --title 'GTA 6: El chat: "Cinema"' \\
        --cam-box 561:365:0:0 --cam-square 365:365:90:0 \\
        --out runs/rubius-sample/clip_7_rubius.mp4
"""

import argparse
import json
import os
import sys
import time

from .config import TEMPLATE_REQUIRED_KEYS
from .rdjavi import (
    DEFAULT_CAM_BOX,
    DEFAULT_CAM_SQUARE,
    _ARIAL_BLACK_PATHS,
    _ass_time,
    _run,
    ass_font_size,
    cam_cover_crop,
    clean_title_prefix,
    face_center,
    punch_cover_crop,
)
from .validate import ConfigError

# Geometria fija de la plantilla (1080x1920). Replica la receta
# de config.yaml (templates.rubius); los tests las comparan.
W, H = 1080, 1920
CAM_W, CAM_H, CAM_X, CAM_Y = 1080, 840, 0, 0
GAME_Y, GAME_H = 840, 1080  # gameplay: y 840..1920 (cuadrado)
GAME_BOTTOM = GAME_Y + GAME_H  # 1920: no hay banda inferior
GAME_CROP = "1440:1440:560:0"  # recorte 1:1 centrado (minimapa fuera)
# Banner del titulo (a caballo del corte 840): 864x288.
BANNER_X0, BANNER_X1, BANNER_Y0, BANNER_Y1 = 103, 967, 811, 1099
BANNER_W = BANNER_X1 - BANNER_X0  # 864
BANNER_H = BANNER_Y1 - BANNER_Y0  # 288
BANNER_FILL = "#FDD227"  # amarillo medido del reel (mediana 253,210,39)
BANNER_BORDER = "#FFFFFF"  # borde blanco medido
BANNER_LINE1 = "#000000"  # linea 1 negra
BANNER_LINE2 = "#DB1F41"  # linea 2 roja (mediana 219,31,65)
BANNER_RADIUS = 28
BANNER_BORDER_W = 6
BANNER_SHOW = (0.5, 3.5)  # transitorio: solo al inicio del clip
BANNER_FONT_SIZES = (104, 96, 88, 80, 72, 64, 56, 48, 44, 40)
BANNER_MAX_W = 760  # ancho util del banner (864 - 2x52)
BANNER_LINE_GAP = 20
# Punch-in de la webcam en la reaccion (16-26 s): zoom 1,18x
# (menos marcado que el 1,30x de willyrex), centrado en la cara.
PUNCH_ZOOM = 1.18
PUNCH_WINDOW = (16, 26)
# Punch-in MARCADO (2026-11-21, pedido del usuario: mas efectos):
# el job de CircleCI pasa 1.30 por defecto.
PUNCH_ZOOM_STRONG = 1.30
# Shake de camara en el momento fuerte (payoff): vaiven de +-5 px
# a 9 Hz durante 1,2 s desde el payoff, con caida lineal.
SHAKE_AMP_PX = 5
SHAKE_FREQ_HZ = 9
SHAKE_DUR_S = 1.2
# Impacto: destello de brillo en el payoff (0,15 s).
IMPACT_BRIGHTNESS = 0.35
IMPACT_DUR_S = 0.15
# Transiciones: el banner entra deslizandose 60 px en 0,2 s y el
# clip arranca con fundido desde negro de 0,3 s.
BANNER_SLIDE_PX = 60
BANNER_SLIDE_S = 0.2
FADE_IN_S = 0.3
# Whoosh de sonido al arrancar el punch-in (efecto "whoosh" del
# pack SFX, barrido que pega en su ultimo 0,5 s). Se mezcla en el
# MISMO pase SFX que el boom/riser (ver apply_sfx extra_events).
WHOOSH_AT_PUNCH = True
# Zoom SUAVE del gameplay en el payoff: 1,15x con rampa de 2 s
# (sube 18->20, aguanta 20->22, baja 22->24), anclado abajo.
GAME_ZOOM_FACTOR = 1.15
GAME_ZOOM_WINDOW = (18, 24)
# Subtitulos palabra por palabra sobre el gameplay.
SUBS_FONT = "Arial Black"
SUBS_FONT_SIZE = 100  # px reales (medido: "QUE" 234 px -> ~100)
SUBS_OUTLINE = 10  # px reales (contorno negro grueso)
SUBS_ALIGN = 8  # arriba, centrado
SUBS_MARGIN_V = 900  # zona y 900-1020 (parte superior del gameplay)
SUBS_MIN_WORD_S = 0.22  # piso de legibilidad por palabra


def load_rubius_recipe(cfg=None):
    """Receta rubius desde config.yaml (UNICA fuente).

    Exige la seccion ``templates.rubius`` con todas las claves
    de TEMPLATE_REQUIRED_KEYS["rubius"]; si falta algo, falla
    fuerte (ConfigError con el nombre del valor), nunca default
    silencioso. Devuelve el dict de la receta.
    """
    cfg = cfg or {}
    templates = cfg.get("templates")
    if not isinstance(templates, dict):
        raise ConfigError(
            "rubius: falta la sección 'templates' en config.yaml"
        )
    recipe = templates.get("rubius")
    if not isinstance(recipe, dict):
        raise ConfigError(
            "rubius: falta la receta 'templates.rubius' en config.yaml"
        )
    for key in TEMPLATE_REQUIRED_KEYS["rubius"]:
        if key not in recipe:
            raise ConfigError(
                f"rubius: falta 'templates.rubius.{key}' en config.yaml"
            )
    return recipe


def _banner_fit(title, font_sizes=BANNER_FONT_SIZES):
    """Líneas (MAYÚSCULAS) y tamaño del banner con el título COMPLETO.

    Única fuente del ajuste: rdjavi.fit_title_lines (misma función
    que la banda willyrex). El título se pasa en mayúsculas porque
    así se dibuja (las mayúsculas son más anchas: el ajuste tiene
    que medir lo que se ve). Sin comillas: en el banner no van.
    Devuelve ([línea1, línea2], tamaño) o ([], None) si no hay
    título. La línea 1 pierde un ':' final (separador, no texto).
    """
    from .rdjavi import fit_title_lines

    upper = (
        clean_title_prefix(title or "")
        .upper()
        .replace('"', "")
        .replace("“", "")
        .replace("”", "")
        .strip()
    )
    lines, size = fit_title_lines(
        upper,
        max_w=BANNER_MAX_W,
        sizes=font_sizes,
        max_lines=2,
        max_h=BANNER_H,
        line_gap=BANNER_LINE_GAP,
    )
    if not lines:
        return [], None
    if len(lines) > 1:
        lines[0] = lines[0].rstrip(":")
    return lines, size


def split_banner_title(title):
    """Título en DOS líneas equilibradas para el banner.

    Limpia el prefijo 'GTA 6' y las comillas; el ajuste (tamaño
    y reparto) lo hace fit_title_lines: el título sale COMPLETO,
    nunca cortado. Devuelve (línea1, línea2) en MAYÚSCULAS, o
    (palabra, None) si hay una sola palabra, o (None, None) si
    no hay título.
    Ej: 'GTA 6: El chat: "Cinema"' -> ('EL CHAT', 'CINEMA').
    """
    lines, _size = _banner_fit(title)
    if not lines:
        return None, None
    if len(lines) == 1:
        return lines[0], None
    return lines[0], lines[1]


def _banner_font(size):
    """Arial Black real (PIL) a un tamano dado."""
    from PIL import ImageFont

    for path in _ARIAL_BLACK_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    raise RuntimeError("no se encontró Arial Black (ver _ARIAL_BLACK_PATHS)")


def make_banner_png(title, out_png, font_sizes=BANNER_FONT_SIZES):
    """PNG del banner amarillo (864x288, esquinas redondeadas).

    Fondo transparente fuera del banner; relleno amarillo con
    borde blanco de 6 px; dos líneas centradas (negra arriba,
    roja abajo) con el tamaño que decide _banner_fit: el título
    sale COMPLETO, nunca cortado. Devuelve la ruta del PNG.
    """
    from PIL import Image, ImageDraw

    lines, size = _banner_fit(title, font_sizes)
    if not lines:
        raise ValueError("título vacío: no se puede hacer el banner")
    font = _banner_font(size)
    img = Image.new("RGBA", (BANNER_W, BANNER_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        [
            BANNER_BORDER_W // 2,
            BANNER_BORDER_W // 2,
            BANNER_W - BANNER_BORDER_W // 2 - 1,
            BANNER_H - BANNER_BORDER_W // 2 - 1,
        ],
        radius=BANNER_RADIUS,
        fill=BANNER_FILL,
        outline=BANNER_BORDER,
        width=BANNER_BORDER_W,
    )
    cap_h = int(size * 0.716)
    total = len(lines) * cap_h + (len(lines) - 1) * BANNER_LINE_GAP
    y = (BANNER_H - total) // 2
    colors = (BANNER_LINE1, BANNER_LINE2)
    for i, ln in enumerate(lines):
        # Ancla "mt": y es el tope de la caja del texto (cap top en
        # mayúsculas). Con "ma" (ascender) el texto caía ~40 px más
        # abajo: la altura del ascender de Arial Black supera la
        # altura de caja y el banner quedaba cargado abajo.
        d.text((BANNER_W // 2, y), ln, font=font, fill=colors[i], anchor="mt")
        y += cap_h + BANNER_LINE_GAP
    img.save(out_png)
    return out_png


def split_block_words(start, end, text, min_word_s=SUBS_MIN_WORD_S):
    """Reparte la duracion de un bloque SRT entre sus palabras.

    Cada palabra recibe una parte proporcional a su longitud
    (misma matematica que el karaoke de srt_to_karaoke_ass):
    una palabra corta en un bloque rapido no se alarga sola,
    el bloque manda. Piso de legibilidad: una palabra por
    debajo de ``min_word_s`` toma tiempo de las palabras mas
    largas DEL MISMO BLOQUE (el tramo total no cambia y nunca
    se invade la palabra siguiente). Devuelve
    [(palabra, inicio, fin)] en segundos.
    """
    words = (text or "").split()
    if not words:
        return []
    span = max(0.0, float(end) - float(start))
    total = sum(len(w) for w in words)
    shares = [
        span * len(w) / total if total else span / len(words) for w in words
    ]
    for _ in range(len(words)):
        short = [i for i, s in enumerate(shares) if s < min_word_s]
        if not short:
            break
        donors = [i for i, s in enumerate(shares) if s > min_word_s]
        if not donors:
            break
        i = short[0]
        need = min_word_s - shares[i]
        j = max(donors, key=lambda k: shares[k])
        give = min(need, shares[j] - min_word_s)
        if give <= 0:
            break
        shares[i] += give
        shares[j] -= give
    out = []
    t = float(start)
    for word, share in zip(words, shares):
        out.append((word, round(t, 3), round(t + share, 3)))
        t += share
    return out


def make_word_ass(
    blocks,
    duration=None,
    font_size=SUBS_FONT_SIZE,
    outline=SUBS_OUTLINE,
    max_end=None,
    banner_show=BANNER_SHOW,
):
    """ASS palabra por palabra (UNA palabra a la vez, blanca).

    Estilo rubius: Arial Black 100 px reales, contorno negro
    grueso (10), BLANCA, centrada ARRIBA (Alignment 8,
    MarginV 900: y 900-1020, parte superior del gameplay).
    Un evento Dialogue por palabra, con los tiempos del
    reparto de split_block_words. Las llaves se neutralizan:
    en ASS serian etiquetas de anulacion. Sin titulo: el
    banner es un PNG aparte (ver make_banner_png), no va en
    el ASS.

    Sin solape con el banner (2026-11-19, medido en el reel
    de referencia y en la muestra del clip 7): el banner
    amarillo ocupa y 811-1099 y la zona de subtitulos y
    900-1020 cae DENTRO de el, asi que una palabra dicha
    mientras el banner esta visible se imprimiria ENCIMA del
    titulo (en la muestra: "de" sobre "CINEMA"). En el reel
    de referencia no hay palabra sobre el banner: el
    streamer no habla en esos primeros segundos. Aqui las
    palabras cuyo intervalo se solapa con la ventana del
    banner (banner_show, por defecto BANNER_SHOW 0,5-3,5 s)
    NO se emiten: el banner es el gancho y las primeras
    palabras pierden el subtitulo ~3 s sin que se pierda
    comprension. Con banner_show=None (sin banner) no se
    suprime ninguna palabra.
    """
    from .transcribe import hex_to_ass_color

    white = hex_to_ass_color("#FFFFFF", 1.0)
    black = hex_to_ass_color("#000000", 1.0)
    transparent = hex_to_ass_color("#000000", 0.0)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, "
        "SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
        "MarginV, Encoding\n"
        # ``font_size`` y ``outline`` son pixeles REALES; el ASS
        # lleva Fontsize/Outline convertidos (ver ASS_FONT_SCALE).
        f"Style: Subs,{SUBS_FONT},{ass_font_size(font_size)},"
        f"{white},{white},"
        f"{black},{transparent},-1,0,0,0,100,100,0,0,1,"
        f"{ass_font_size(outline)},"
        f"0,{SUBS_ALIGN},10,10,{SUBS_MARGIN_V},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )
    lines = [header]
    for start, end, text in blocks:
        if max_end is not None and start >= max_end:
            continue
        if end <= 0:
            continue
        start = max(0.0, start)
        if max_end is not None:
            end = min(end, max_end)
        if end <= start:
            continue
        for word, ws, we in split_block_words(start, end, text):
            clean = (
                word.replace("{", "(")
                .replace("}", ")")
                .replace("\\N", " ")
                .strip()
            )
            if not clean:
                continue
            if banner_show is not None:
                b0, b1 = banner_show
                if ws < b1 and we > b0:
                    continue  # palabra bajo el banner: no tapar el titulo
            lines.append(
                f"Dialogue: 0,{_ass_time(ws)},{_ass_time(we)},Subs,,"
                f"0,0,0,,{clean}\n"
            )
    return "".join(lines)


def make_word_ass_from_transcript(transcript, duration=None, **kw):
    """ASS palabra por palabra desde un .transcript.json.

    Los segmentos del JSON (start/end/text) se tratan como
    bloques SRT: el reparto por palabra lo hace
    split_block_words (los tiempos de palabra crudos de
    Whisper son demasiado cortos para leerse: mediana 0,18 s).
    """
    blocks = []
    for seg in (transcript or {}).get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        blocks.append((float(seg["start"]), float(seg["end"]), text))
    return make_word_ass(blocks, duration=duration, **kw)


def _game_filter(game_crop, game_zoom):
    """Filtro de la rama del gameplay: recorte fijo + zoom SUAVE
    en el payoff (scale con eval=frame + crop con ventana
    animada). Misma tecnica que rdjavi._game_filter (el crop
    animado no funciona: w/h se congelan en la configuracion);
    aqui el lienzo del juego es 1080x1080 (GAME_H), no 940.
    El zoom es 1,15x centrado en horizontal y anclado abajo:
    el borde inferior del recorte no se mueve (no se recorta
    la zona de dialogo del juego). Sin game_zoom, recorte fijo
    + escala estatica."""
    if not game_zoom:
        return f"crop={game_crop},scale={W}:{GAME_H},setsar=1"
    z0, z1 = (float(v) for v in game_zoom)
    f = (
        f"if(between(t,{z0:g},{z0 + 2:g}),(t-{z0:g})/2,"
        f"if(between(t,{z0 + 2:g},{z1 - 2:g}),1,"
        f"if(between(t,{z1 - 2:g},{z1:g}),({z1:g}-t)/2,0)))"
    )
    k = GAME_ZOOM_FACTOR - 1  # 0.15
    x_coef = W * k / 2  # 81: centra la ventana en horizontal
    y_coef = GAME_H * k  # 162: ancla la ventana abajo
    return (
        f"crop={game_crop},"
        f"scale=w='{W}*(1+{k:g}*{f})':h='{GAME_H}*(1+{k:g}*{f})':eval=frame,"
        f"crop={W}:{GAME_H}:'{x_coef:g}*{f}':'{y_coef:g}*{f}',setsar=1"
    )


def build_filter(
    cam_box,
    cam_square,
    punch_in,
    duration,
    ass_path,
    banner_png,
    fps,
    game_crop=GAME_CROP,
    game_zoom=GAME_ZOOM_WINDOW,
    punch_zoom=PUNCH_ZOOM,
    shake=True,
    shake_amp=SHAKE_AMP_PX,
    impact=True,
    payoff_s=20.0,
    banner_slide=True,
    fade_in=True,
):
    """Grafo de un solo pase, plantilla rubius (opcion C).

    [0:v] se parte en camara y gameplay; la camara es COVER a
    1080x840 (doble rama + overlay enable para el punch-in
    ``punch_zoom``); el gameplay es el recorte cuadrado con zoom suave
    y se padea a 1080x1920 en y=840; el banner PNG (entrada
    [1:v]) se compone a caballo del corte solo en BANNER_SHOW
    (con ``banner_slide`` entra deslizandose 60 px en 0,2 s);
    el ASS palabra por palabra se quema al final. Despues del
    quemado, si se piden: fundido desde negro al inicio
    (``fade_in``, 0,3 s), shake de +-``shake_amp`` px a 9 Hz
    durante 1,2 s desde el payoff (``shake``) y destello de
    brillo de 0,15 s en el payoff (``impact``). Sin banner
    no hay entrada [1:v] ni overlay. Sin ASS no hay quemado.
    Devuelve (filtro, etiqueta_de_salida).
    """
    cover = cam_cover_crop(cam_box, CAM_W, CAM_H)
    p0, p1 = punch_in if punch_in else (None, None)
    if p0 is not None:
        cx, cy = face_center(cam_square)
        zc = punch_cover_crop(cover, cx, cy, zoom=punch_zoom)
        head = (
            f"[0:v]fps={fps},split=2[cam_src][game_src];"
            f"[cam_src]split=2[cam_n][cam_z];"
            f"[cam_n]crop={cover},scale={CAM_W}:{CAM_H},setsar=1[camN];"
            f"[cam_z]crop={zc},scale={CAM_W}:{CAM_H},setsar=1[camZ];"
        )
        cam_overlay = (
            f"[step1][camN]overlay={CAM_X}:{CAM_Y}:"
            f"enable='lt(t,{p0})+gte(t,{p1})'[step2a];"
            f"[step2a][camZ]overlay={CAM_X}:{CAM_Y}:"
            f"enable='gte(t,{p0})*lt(t,{p1})'[step2];"
        )
    else:
        head = (
            f"[0:v]fps={fps},split=2[cam_src][game_src];"
            f"[cam_src]crop={cover},scale={CAM_W}:{CAM_H},setsar=1[cam];"
        )
        cam_overlay = f"[step1][cam]overlay={CAM_X}:{CAM_Y}[step2];"
    game_filt = _game_filter(game_crop, game_zoom)
    parts = [
        head,
        f"[game_src]{game_filt}[game];"
        f"[game]pad={W}:{H}:0:{GAME_Y}:black[step1];",
        cam_overlay,
    ]
    vlabel = "[step2]"
    if banner_png:
        b0, b1 = BANNER_SHOW
        if banner_slide:
            # Desliza hacia abajo 60 px en los primeros 0,2 s de
            # la ventana y queda fijo: y pasa de 751 a 811.
            y_expr = (
                f"'if(between(t,{b0:g},{b0 + BANNER_SLIDE_S:g}),"
                f"{BANNER_Y0}-{BANNER_SLIDE_PX}"
                f"*({b0 + BANNER_SLIDE_S:g}-t)"
                f"/{BANNER_SLIDE_S:g},{BANNER_Y0})'"
            )
        else:
            y_expr = str(BANNER_Y0)
        parts.append(
            f"[step2][1:v]overlay={BANNER_X0}:{y_expr}:"
            f"enable='between(t,{b0:g},{b1:g})'[step3];"
        )
        vlabel = "[step3]"
    if ass_path:
        parts.append(f"{vlabel}ass={ass_path}[vsub]")
        vlabel = "[vsub]"
    # Efectos de salida (2026-11-21): cada uno es una cadena de
    # filtros que consume la etiqueta anterior; se unen con ";"
    # (sin el separador, ffmpeg lee "[vfade][vfade]scale" como
    # basura tras el filtro).
    post = []
    if fade_in:
        post.append(f"{vlabel}fade=t=in:st=0:d={FADE_IN_S:g}[vfade]")
        vlabel = "[vfade]"
    if shake:
        # Shake: se escala el cuadro +5 px por lado y se recorta
        # con una ventana que va y viene (sin/cos a 9 Hz) solo en
        # [payoff-0,2, payoff+1,0] con caida lineal: fuera de esa
        # ventana el recorte es el centro exacto (sin movimiento).
        a = float(payoff_s) - 0.2
        b = a + SHAKE_DUR_S
        gate = (
            f"max(0,1-(t-{a:g})/{SHAKE_DUR_S:g})"
            f"*between(t,{a:g},{b:g})"
        )
        post.append(
            f"{vlabel}scale={W + 2 * SHAKE_AMP_PX}:"
            f"{H + 2 * SHAKE_AMP_PX}[vbig];"
            f"[vbig]crop={W}:{H}:"
            f"'{SHAKE_AMP_PX}+{shake_amp:g}*sin(2*PI*{SHAKE_FREQ_HZ}*t)"
            f"*({gate})':"
            f"'{SHAKE_AMP_PX}+{shake_amp:g}*cos(2*PI*{SHAKE_FREQ_HZ}*t)"
            f"*({gate})'[vshake]"
        )
        vlabel = "[vshake]"
    if impact:
        # Destello de brillo en el payoff: +0,35 durante 0,15 s.
        p = float(payoff_s)
        post.append(
            f"{vlabel}eq=brightness='"
            f"{IMPACT_BRIGHTNESS:g}*between(t,{p:g},{p + IMPACT_DUR_S:g})"
            f"'[vimp]"
        )
        vlabel = "[vimp]"
    if post:
        # El ASS no lleva ";" final (era el último); la cadena de
        # efectos sí necesita el separador antes de su primera
        # etiqueta de entrada. El resto de partes ya terminan en ";".
        sep = "" if parts[-1].endswith(";") else ";"
        parts.append(sep + ";".join(post))
    return "".join(parts), vlabel


def build_render_cmd(
    segment,
    start,
    duration,
    ass_path,
    out_path,
    banner_png=None,
    cam_box=DEFAULT_CAM_BOX,
    cam_square=DEFAULT_CAM_SQUARE,
    punch_in=PUNCH_WINDOW,
    fps=30,
    preset="veryfast",
    crf=23,
    game_crop=GAME_CROP,
    game_zoom=GAME_ZOOM_WINDOW,
    punch_zoom=PUNCH_ZOOM,
    shake=True,
    shake_amp=SHAKE_AMP_PX,
    impact=True,
    payoff_s=20.0,
    banner_slide=True,
    fade_in=True,
):
    """Comando ffmpeg del render Rubius en un solo pase.

    La entrada ya lleva -t duration y la salida tambien: el -t
    de salida es el seguro por si alguna fuente del grafo no
    termina. El banner entra en loop como segunda entrada
    (solo si se pasa banner_png).
    """
    filt, vlabel = build_filter(
        cam_box,
        cam_square,
        punch_in,
        duration,
        ass_path,
        banner_png,
        fps,
        game_crop,
        game_zoom,
        punch_zoom=punch_zoom,
        shake=shake,
        shake_amp=shake_amp,
        impact=impact,
        payoff_s=payoff_s,
        banner_slide=banner_slide,
        fade_in=fade_in,
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-ss",
        str(start),
        "-t",
        str(duration),
        "-i",
        segment,
    ]
    if banner_png:
        cmd += ["-loop", "1", "-t", str(duration), "-i", banner_png]
    cmd += [
        "-filter_complex",
        filt,
        "-map",
        vlabel,
        "-map",
        "0:a",
        "-c:a",
        "copy",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-t",
        str(duration),
        out_path,
    ]
    return cmd


def render(
    segment,
    start,
    duration,
    ass_path,
    out_path,
    banner_png=None,
    cam_box=DEFAULT_CAM_BOX,
    cam_square=DEFAULT_CAM_SQUARE,
    punch_in=PUNCH_WINDOW,
    fps=30,
    preset="veryfast",
    crf=23,
    game_crop=GAME_CROP,
    game_zoom=GAME_ZOOM_WINDOW,
    watermark=None,
    cfg=None,
    sfx_transcript=None,
    sfx_srt=None,
    payoff_s=20.0,
    punch_zoom=PUNCH_ZOOM,
    shake=True,
    shake_amp=SHAKE_AMP_PX,
    impact=True,
    banner_slide=True,
    fade_in=True,
    whoosh=WHOOSH_AT_PUNCH,
):
    """Render Rubius en un solo pase. Devuelve (segundos, ruta_final).

    ``watermark``: dict {source|url, opacity, size_px, position}
    o ruta/URL suelta (ver clipagent/watermark.py). Se compone en
    un pase final sobre el render; si falla, el render sigue sin
    marca. ``cfg`` (config.yaml cargada): si su seccion ``sfx``
    esta activada, se mezcla SFX sobre el MP4 final con
    workers.apply_sfx (mismo paso 6a del camino automatico:
    boom/riser en el payoff + lift de volumen); si la mezcla
    falla, el clip sigue sin SFX. Sin cfg no hay SFX.

    Efectos extra (2026-11-21): ``punch_zoom`` (zoom del punch-in,
    1,30 marcado), ``shake`` (vaiven en el payoff), ``impact``
    (destello en el payoff), ``banner_slide`` (el banner entra
    deslizandose), ``fade_in`` (fundido desde negro) y ``whoosh``
    (barrido de sonido al arrancar el punch-in, en la MISMA mezcla
    SFX que el boom/riser). Todos van por defecto; se apagan con
    False (CLI: --no-shake, --no-impact, --no-banner-slide,
    --no-fade-in, --no-whoosh, --punch-zoom).
    """
    cmd = build_render_cmd(
        segment,
        start,
        duration,
        ass_path,
        out_path,
        banner_png,
        cam_box=cam_box,
        cam_square=cam_square,
        punch_in=punch_in,
        fps=fps,
        preset=preset,
        crf=crf,
        game_crop=game_crop,
        game_zoom=game_zoom,
        punch_zoom=punch_zoom,
        shake=shake,
        shake_amp=shake_amp,
        impact=impact,
        payoff_s=payoff_s,
        banner_slide=banner_slide,
        fade_in=fade_in,
    )
    dt = _run(cmd, "render Rubius")
    if watermark:
        from .watermark import maybe_apply_watermark

        wm = (
            watermark
            if isinstance(watermark, dict)
            else {"enabled": True, "source": watermark}
        )
        maybe_apply_watermark(out_path, {"watermark": wm})
    final = out_path
    if cfg is not None:
        from .workers import apply_sfx

        extra = []
        if whoosh and punch_in:
            # Whoosh al arrancar el punch-in: solo si cabe entero
            # antes de los ultimos 2 s (cola del loop) y fuera del
            # hook (0-3 s). Va en la misma mezcla que boom/riser.
            p0 = float(punch_in[0])
            if p0 >= 3.0 and p0 + 2.0 <= float(duration) - 2.0:
                extra.append(
                    {"effect": "whoosh", "start": p0, "duration": 2.0,
                     "reason": f"whoosh al arrancar el punch-in s={p0:g}"}
                )
        final, _sfx_info = apply_sfx(
            out_path,
            transcript_path=sfx_transcript,
            srt_path=sfx_srt,
            payoff_s=float(payoff_s),
            cfg=cfg,
            extra_events=extra,
        )
    return dt, final


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--segment", required=True)
    p.add_argument("--start", type=float, default=0)
    p.add_argument("--duration", type=float, required=True)
    p.add_argument(
        "--ass",
        default=None,
        help="ASS palabra por palabra ya hecho (si no se "
        "pasa, se genera desde --srt o --transcript)",
    )
    p.add_argument(
        "--srt",
        default=None,
        help="SRT del clip: se genera el ASS rubius desde "
        "sus bloques (reparto por palabra)",
    )
    p.add_argument(
        "--transcript",
        default=None,
        help=".transcript.json de faster-whisper (alternativa al SRT)",
    )
    p.add_argument(
        "--title",
        default=None,
        help="Titulo del banner amarillo (dos lineas: "
        "negra arriba, roja abajo)",
    )
    p.add_argument(
        "--no-banner",
        action="store_true",
        help="Sin banner aunque se pase --title",
    )
    p.add_argument("--out", required=True)
    p.add_argument(
        "--config",
        default=None,
        help="config.yaml (receta rubius + SFX; por defecto la del repo)",
    )
    # Sin default silencioso: los defaults son la caja de p3220
    # (webcam arriba a la izquierda) y usarlos en otro clip
    # RECORTA JUEGO en la banda de la webcam, no al streamer
    # (mismo aviso que rdjavi.py, bug 2026-11-18 20:47).
    p.add_argument("--cam-box", default=None)
    p.add_argument("--cam-square", default=None)
    p.add_argument(
        "--game-crop",
        default=GAME_CROP,
        help="Recorte 1:1 del gameplay (default 1440:1440:560:0 "
        "para segmentos 2560x1440)",
    )
    p.add_argument("--punch-in", type=float, nargs=2, default=[16, 26])
    p.add_argument("--no-punch-in", action="store_true")
    p.add_argument(
        "--punch-zoom",
        type=float,
        default=PUNCH_ZOOM,
        help="Zoom del punch-in de la webcam (default 1,18; 1,30 "
        "es el marcado que usa el job de CircleCI)",
    )
    p.add_argument(
        "--no-shake",
        action="store_true",
        help="Sin shake de camara en el payoff",
    )
    p.add_argument(
        "--shake-amp",
        type=float,
        default=SHAKE_AMP_PX,
        help="Amplitud del shake en px (default 5)",
    )
    p.add_argument(
        "--no-impact",
        action="store_true",
        help="Sin destello de brillo en el payoff",
    )
    p.add_argument(
        "--no-banner-slide",
        action="store_true",
        help="El banner aparece fijo, sin deslizarse",
    )
    p.add_argument(
        "--no-fade-in",
        action="store_true",
        help="Sin fundido desde negro al inicio",
    )
    p.add_argument(
        "--no-whoosh",
        action="store_true",
        help="Sin whoosh de sonido al arrancar el punch-in",
    )
    p.add_argument(
        "--payoff",
        type=float,
        default=20.0,
        help="Segundo del momento fuerte (boom/shake/impacto, "
        "default 20)",
    )
    p.add_argument(
        "--game-zoom",
        type=float,
        nargs=2,
        default=[18, 24],
        help="Ventana del zoom suave del gameplay en el payoff "
        "(default 18 24)",
    )
    p.add_argument(
        "--no-game-zoom",
        action="store_true",
        help="Sin zoom del gameplay en el payoff",
    )
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--preset", default="veryfast")
    p.add_argument("--crf", type=int, default=23)
    p.add_argument(
        "--watermark",
        nargs="?",
        const="default",
        default=None,
        help="Marca de agua: ruta local o URL de un PNG. Sin "
        "valor usa el logo BITSONER de colores del repo "
        "(arriba a la derecha, 320 px, 20%%). Sin este "
        "flag no se aplica marca.",
    )
    p.add_argument("--watermark-opacity", type=float, default=20)
    p.add_argument("--watermark-size", type=int, default=320)
    p.add_argument(
        "--watermark-position",
        default="top-right",
        choices=[
            "center",
            "top-left",
            "top-right",
            "bottom-left",
            "bottom-right",
        ],
    )
    p.add_argument(
        "--no-sfx",
        action="store_true",
        help="Sin mezcla SFX aunque config.yaml la active",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo imprime el comando ffmpeg, no renderiza",
    )
    args = p.parse_args(argv)

    cam_box = args.cam_box or DEFAULT_CAM_BOX
    cam_square = args.cam_square or DEFAULT_CAM_SQUARE
    if args.cam_box is None or args.cam_square is None:
        print(
            "AVISO: --cam-box/--cam-square no especificados: "
            "usando los de p3220 (webcam arriba a la izquierda, "
            f"cam-box {DEFAULT_CAM_BOX} / cam-square "
            f"{DEFAULT_CAM_SQUARE}). Si la webcam de este clip "
            "esta en otro sitio, pasa los valores medidos "
            "(p7775: --cam-box 561:365:0:626 --cam-square "
            "365:365:90:626); si no, la banda de la webcam "
            "muestra JUEGO, no al streamer.",
            file=sys.stderr,
        )
    print(f"cam-box {cam_box} / cam-square {cam_square}")

    cfg = None
    cfg_path = args.config
    if cfg_path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        cfg_path = os.path.join(here, "..", "config.yaml")
    if os.path.isfile(cfg_path):
        from .config import load_config

        cfg = load_config(cfg_path)
        load_rubius_recipe(cfg)
    elif not args.no_sfx:
        print(
            f"AVISO: no existe {cfg_path}: sin receta validada ni SFX.",
            file=sys.stderr,
        )

    workdir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(workdir, exist_ok=True)
    # El banner se decide ANTES que el ASS: las palabras que caen
    # bajo el banner no se emiten (ver make_word_ass); sin banner
    # no se suprime ninguna (banner_show=None).
    banner_png = None
    if args.title and not args.no_banner:
        banner_png = os.path.join(workdir, "banner_rubius.png")
        make_banner_png(args.title, banner_png)
        print(f"banner: {split_banner_title(args.title)} -> {banner_png}")
    banner_show = BANNER_SHOW if banner_png else None
    ass_path = args.ass
    if not ass_path:
        if not args.srt and not args.transcript:
            raise SystemExit("--ass, --srt o --transcript (subtítulos)")
        from .transcribe import parse_srt

        if args.srt:
            blocks = parse_srt(args.srt)
            ass_text = make_word_ass(
                blocks, max_end=args.duration, banner_show=banner_show
            )
        else:
            with open(args.transcript, encoding="utf-8") as f:
                transcript = json.load(f)
            ass_text = make_word_ass_from_transcript(
                transcript, max_end=args.duration, banner_show=banner_show
            )
        ass_path = os.path.join(workdir, "subs_rubius.ass")
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_text)
        print(
            f"ASS rubius: {ass_text.count('Dialogue:')} palabras -> {ass_path}"
        )
    punch = None if args.no_punch_in else tuple(args.punch_in)
    game_zoom = None if args.no_game_zoom else tuple(args.game_zoom)
    watermark = None
    if args.watermark:
        from .watermark import DEFAULT_WATERMARK_PNG

        src = (
            DEFAULT_WATERMARK_PNG
            if args.watermark == "default"
            else args.watermark
        )
        watermark = {
            "enabled": True,
            "source": src,
            "opacity": args.watermark_opacity,
            "size_px": args.watermark_size,
            "position": args.watermark_position,
        }
    sfx_cfg = None if args.no_sfx else cfg
    cmd = build_render_cmd(
        args.segment,
        args.start,
        args.duration,
        ass_path,
        args.out,
        banner_png,
        cam_box=cam_box,
        cam_square=cam_square,
        punch_in=punch,
        fps=args.fps,
        preset=args.preset,
        crf=args.crf,
        game_crop=args.game_crop,
        game_zoom=game_zoom,
        punch_zoom=args.punch_zoom,
        shake=not args.no_shake,
        shake_amp=args.shake_amp,
        impact=not args.no_impact,
        payoff_s=args.payoff,
        banner_slide=not args.no_banner_slide,
        fade_in=not args.no_fade_in,
    )
    if args.dry_run:
        print(" ".join(cmd))
        return 0
    t0 = time.time()
    dt, final = render(
        args.segment,
        args.start,
        args.duration,
        ass_path,
        args.out,
        banner_png,
        cam_box=cam_box,
        cam_square=cam_square,
        punch_in=punch,
        fps=args.fps,
        preset=args.preset,
        crf=args.crf,
        game_crop=args.game_crop,
        game_zoom=game_zoom,
        watermark=watermark,
        cfg=sfx_cfg,
        sfx_transcript=args.transcript,
        sfx_srt=args.srt,
        payoff_s=args.payoff,
        punch_zoom=args.punch_zoom,
        shake=not args.no_shake,
        shake_amp=args.shake_amp,
        impact=not args.no_impact,
        banner_slide=not args.no_banner_slide,
        fade_in=not args.no_fade_in,
        whoosh=not args.no_whoosh,
    )
    print(
        f"render Rubius: {dt:.1f} s ({args.duration:.1f} s de clip, "
        f"{args.duration / max(dt, 0.01):.2f}x) -> {final} "
        f"(total {time.time() - t0:.1f} s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
