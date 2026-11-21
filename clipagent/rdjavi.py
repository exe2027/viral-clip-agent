#!/usr/bin/env python3
"""Plantilla RDJavi (GTA 6 / Vice City) — render de clips verticales 1080x1920.

Un solo pase de ffmpeg, fondo desenfocado PRE-RENDERIZADO (una imagen
fija por clip, cacheada) y subtitulos simples quemados en el mismo pase.
Objetivo: un clip de ~48 s en pocos minutos, no en horas.

Layout (reel de referencia @gta_vi_clips_v "Atropello una gaviota
en GTA 6", decision del usuario 2026-11-18, CORREGIDO 20:13 con
captura: la primera version ponia el titulo sobre la camara y NO
era como Willyrex; REVISION v2 2026-11-18 21:01: la webcam
llenaba poco, el titulo en 2 lineas con "GTA 6" sobraba y la
franja negra era muy alta):
  * Streamer ARRIBA a ANCHO COMPLETO: 1080x702 px (y=0..702),
    COVER del rectangulo PiP (561x365 en p7775), sin barras
    negras a los lados y sin cuadrado centrado: Rubius grande.
    Sobre fondo desenfocado morado/magenta + gradiente
    atardecer Vice City (hoy invisible: la webcam tapa todo
    el ancho; el fondo queda por si la caja cambia).
  * TITULO QUEMADO en la franja negra de ancho completo ENTRE
    la webcam y el gameplay (y=702..800, 98 px): texto blanco
    Arial Black con contorno negro grueso, centrado
    verticalmente en la franja. NUNCA "GTA 6" delante (obvio y
    redundante: se limpia automaticamente) y NUNCA cortado: el
    titulo aparece SIEMPRE COMPLETO (correccion del usuario
    2026-11-19: la v2 lo acortaba por palabras y "Risa
    imparable por un asesinato" salia "Risa imparable por
    un"). El ajuste automatico achica la letra hasta que quepa
    en una linea y, si ni con el minimo cabe, parte en dos
    lineas dentro de la franja. Ver fit_title_lines() /
    add_burned_title().
  * Gameplay ABAJO: y=800..1740 (940 px), recorte 1440x1252
    (aspecto 1080:940, sin el aplastado vertical del 1:1
    viejo). Zoom SUAVE 1,15x en el payoff (18-24 s, rampa de
    2 s, anclado abajo para no recortar la zona de dialogo
    del juego; la carta del payoff queda centrada).
  * Subtitulos AMARILLOS FIJOS ABAJO DEL TODO, en la barra negra
    y=1740..1920 (Alignment 2, MarginV 12): todo el texto amarillo
    Arial Black 64 px con contorno negro grueso (6), como la
    captura de Willyrex (en el ejemplo son mas grandes que los
    56 px viejos). NUNCA sobre el gameplay: ahi van los
    subtitulos propios de GTA 6 y se solaparian (REGLA DURA del
    usuario 2026-11-18 20:13). Ver make_plain_ass(). Se queman
    en el mismo pase. El karaoke palabra por palabra queda en
    segundo plano (no se puede hacer con ese mismo estilo
    amarillo: su resto blanco no es como la captura);
    make_karaoke_ass() sigue disponible para otros usos.
  * Cortes duros; camara con punch-in (zoom 1,30x, mas marcado
    que el 1,18x anterior) en la reaccion (por defecto 16-26 s
    del clip), con doble rama de camara + overlay enable
    (opcion C, 2026-11-18): sin trim ni concat de 3 tramos.

Por que es rapido (vs el render viejo de 4 pases):
  * 1 solo pase: layout + punch-in + subtitulos + audio en una
    invocacion; sin renders por tramo ni concat de MP4 ni segundo
    pase de quemado.
  * La fuente de color de fondo lleva duracion (color:d=DUR) y la
    salida lleva -t DUR: sin duracion el color es una fuente
    infinita y ffmpeg no termina nunca (cola congelada de 193 s
    en p3220, 2026-11-18).
  * Fondo desenfocado pre-renderizado: una imagen fija (fotograma del
    clip desenfocado + gradiente) cacheada en <run>/rdjavi/bg.png;
    el pase principal la usa como entrada en loop, sin gblur por
    fotograma.
  * Sin re-escalados de mas: cada fuente se escala una sola vez.
  * Audio copiado (-c:a copy): el audio de Twitch ya viene AAC
    normalizado; no se aplica loudnorm (lento).
  * preset veryfast (o superfast), fps 30 (los Shorts/Reels van a
    30 fps; el repo antes usaba 60 fps y eso duplicaba el trabajo).
  * El segmento de entrada ya es SOLO el trozo del clip (regla
    2026-11-17: nunca el VOD entero).

Uso:
    python -m clipagent.rdjavi --segment runs/.../segments/seg.mp4 \\
        --start 0 --duration 48.02 --ass rdjavi/subs_simple.ass \\
        --title "Rubius descubre los cromos coleccionables" \\
        --cam-box 561:365:0:626 --cam-square 365:365:90:626 \\
        --out clips/clip_rdjavi_subs.mp4 [--bg-frame 20] [--fps 60]
        [--preset veryfast] [--punch-in 16 26] [--no-punch-in]
        [--game-zoom 18 24] [--no-game-zoom]
        [--watermark [ruta|URL]] [--watermark-opacity 20]
        [--watermark-size 320] [--watermark-position top-right]

Marca de agua (2026-11-18, elección del usuario: logo BITSONER
de colores, arriba a la derecha, 320 px, 20%): --watermark sin
valor usa el logo de colores del repo
(clipagent/assets/watermark_bitsoner_color.png); con ruta o URL
usa ese PNG. Se compone en un pase final sobre el render (el
pase único de la plantilla no se toca). Sin --watermark no se
aplica nada. Si la descarga o el overlay fallan, el render sigue
sin marca (aviso por stderr).
"""
import argparse
import os
import re
import subprocess
import sys
import time

# Geometria fija de la plantilla (1080x1920). Revision v2
# (2026-11-18 21:01, pedido del usuario): la webcam LLENA la
# banda superior de lado a lado (cover crop del rectangulo
# PiP, sin barras negras ni cuadrado centrado: se veia muy
# pequena), el titulo es UNA sola linea con letra mas grande
# y la franja negra se achica.
W, H = 1080, 1920
CAM_W, CAM_H, CAM_X, CAM_Y = 1080, 702, 0, 0
BG_H = 702          # fondo desenfocado: y 0..702 (detras de la webcam;
                    # hoy invisible: la webcam cubre todo el ancho)
GAME_Y, GAME_H = 800, 940    # gameplay: y 800..1740
GAME_BOTTOM = GAME_Y + GAME_H  # 1740: empieza la barra negra inferior
GAME_CROP = "1440:1252:620:94"  # recorte 1080:940 (sin aplastar: el
    # 1:1 viejo escalado a 991 px aplastaba el gameplay ~8% en vertical)
DEFAULT_CAM_BOX = "561:365:0:0"  # caja PiP del streamer en el segmento
DEFAULT_CAM_SQUARE = "365:365:90:0"  # recorte cuadrado (cara centrada);
    # en v2 solo se usa para el CENTRO DE LA CARA del punch-in
# Punch-in de la webcam en la reaccion (por defecto 16-26 s):
# zoom 1,30x, mas marcado que el 1,18x anterior. El recorte se
# calcula centrado en la cara (ver punch_cover_crop); el exacto
# de p3220 (310:310:125:27) era del cuadrado viejo y ya no vale.
PUNCH_ZOOM = 1.30
# Zoom SUAVE del gameplay en el payoff: 1,15x con rampa de 2 s
# (sube 18->20, aguanta 20->22, baja 22->24). Anclado ABAJO: el
# borde inferior del recorte no se mueve, asi no se recorta la
# zona de dialogo del juego; la carta del payoff queda centrada
# (medido en p7775: centro de la carta ~(1354, 795), dentro del
# recorte con zoom 728..1952 x 282..1346).
GAME_ZOOM = (18, 24)
GAME_ZOOM_FACTOR = 1.15

# Subtitulos ABAJO DEL TODO (correccion del usuario 2026-11-18
# 20:13, layout Willyrex): en la barra negra y 1740..1920, debajo
# del gameplay. Alignment 2 = abajo-centrado, MarginV medido desde
# abajo. NUNCA sobre el gameplay: ahi van los subtitulos propios
# de GTA 6 y se solaparian (regla dura). La version anterior los
# ponia al pie de la camara (Alignment 8, MarginV 608) y NO era
# como Willyrex.
SUBS_ALIGN = 2
SUBS_MARGIN_V = 12
# Tipografia: Arial Black GENUINA, LA MISMA del ejemplo del
# usuario (2026-11-18 20:33 y 20:47, captura del reel
# @gta_vi_clips_v). Descargada de cdnfonts (ariblk, Monotype
# Arial Black v2.35) e instalada en ~/.fonts/arial-black.ttf;
# fc-list la resuelve como "Arial Black" (verificado 20:48).
# Ancho a 56 px medido con PIL: las 2 lineas del titulo de
# p7775 son 730/820 px (caben en los 1060 px utiles); la
# Noto Sans Black (900) que se uso antes era solo parecida
# (663/746 px) y el usuario la rechazo. Vale para titulo Y
# subtitulos: los dos usan SUBS_FONT. OJO: la fuente vive en
# la VM; un render en CI/GitHub Actions necesita el mismo
# TTF instalado o libass cae a otra fuente.
#
# Calibracion libass (medido 2026-11-18 21:01, bug que explicaba
# por que los subtitulos no se parecian al ejemplo aunque la
# fuente fuera la misma): libass NO renderiza Fontsize como
# pixeles EM (como PIL/FreeType), sino que hace que el span
# hhea (ascent - descent) de la fuente sea igual a Fontsize.
# Para Arial Black (upm 2048, hhea 2254/-634, span 2888) el
# tamano real es Fontsize * 0,7091: un titulo a Fontsize 64 se
# veia de 45 px reales y unos subtitulos a 56, de 40 px.
# Verificado: Fontsize 90 renderiza 'Rubius descubre los
# cromos' a 980 px de ancho (PIL a 64 px: 990 px); DejaVu Bold
# tiene otro factor (0,859: span 2384/2048), asi que este
# factor es DE ARIAL BLACK, no global. Los tamanos de esta
# plantilla (SUBS_FONT_SIZE, TITLE_FONT_SIZE, OUTLINE) son
# PIXELES REALES; ass_font_size() los convierte a unidades
# ASS al escribir el estilo. Las posiciones (MarginV, bandas)
# NO se escalan: son pixeles reales tal cual.
ASS_FONT_SCALE = 2888 / 2048  # 1.41015625


def ass_font_size(px):
    """Fontsize ASS para que el texto se vea de ``px`` pixeles
    reales (ver ASS_FONT_SCALE)."""
    return max(1, round(px * ASS_FONT_SCALE))


# Tamano de los subtitulos (v2 2026-11-18 21:01): 64 px REALES, no
# 56 px: en la captura del ejemplo los subtitulos son mas
# grandes; a 64 px la linea mas larga de p7775 mide 882 px
# (< 1060 utiles), sin riesgo de envolver a 3 lineas. El ASS
# lleva Fontsize 90 (= 64 * 1,4102, ver ass_font_size).
SUBS_FONT = "Arial Black"
SUBS_FONT_SIZE = 64
SUBS_OUTLINE = 6  # contorno negro grueso, como la captura Willyrex
SUBS_COLOR = "#FFFF00"  # amarillo fijo (todo el texto, no solo la activa)

# Titulo quemado (2026-11-18 19:50, CORREGIDO 20:13, v2 21:01):
# UNA SOLA LINEA en la franja negra de ancho completo ENTRE
# la webcam y el gameplay (y 702..800, 98 px). Blanco, contorno
# grueso (5 px reales). NUNCA "GTA 6" delante (se limpia en
# clean_title_prefix) y NUNCA cortado: el titulo aparece SIEMPRE
# COMPLETO (correccion del usuario 2026-11-19: antes se acortaba
# por palabras). El ajuste automatico (ver fit_title_lines)
# achica la letra por la escalera TITLE_FONT_SIZES hasta que el
# titulo entero quepa en una linea y, si ni con el minimo cabe,
# parte en dos lineas dentro de la franja. Se prueba 68 px
# primero. El ASS lleva Fontsize = px * 1,4102 (ver ass_font_size).
TITLE_FONT_SIZE = 64
TITLE_FONT_SIZES = (68, 64, 60, 56, 52, 48, 44, 40, 36, 32)
TITLE_LINE_GAP = 8  # separacion entre las dos lineas de la banda
TITLE_BAND_Y0 = 702
TITLE_BAND_Y1 = 800
TITLE_BAND_H = TITLE_BAND_Y1 - TITLE_BAND_Y0  # 98
TITLE_MAX_W = 1060  # ancho util (1080 - 2*10 de margenes)
TITLE_OUTLINE = 5
TITLE_LINE_H = TITLE_FONT_SIZE + 12  # alto por linea con interlineado


def _run(cmd, what):
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{what} fallo:\n{r.stderr.strip()[-2000:]}")
    return time.time() - t0


def make_bg_still(segment, frame_t, cam_box, gradient_png, out_png):
    """Fondo pre-renderizado: fotograma del clip -> recorte cam ->
    desenfoque fuerte -> gradiente Vice City. Una sola imagen."""
    tmp = out_png + ".frame.jpg"
    _run(["ffmpeg", "-y", "-v", "error", "-ss", str(frame_t),
          "-i", segment, "-frames:v", "1", "-q:v", "3", tmp],
         "extraer fotograma de fondo")
    _run(["ffmpeg", "-y", "-v", "error", "-i", tmp, "-i", gradient_png,
          "-filter_complex",
          f"[0:v]crop={cam_box},scale=540:352,gblur=sigma=18,"
          f"scale=1080:{BG_H},setsar=1[bg];[bg][1:v]overlay=0:0,format=yuv420p[out]",
          "-map", "[out]", "-frames:v", "1", out_png],
         "fondo desenfocado + gradiente")
    try:
        os.unlink(tmp)
    except OSError:
        pass
    return out_png


def _parse_crop(s):
    """'w:h:x:y' -> (w, h, x, y) enteros."""
    w, h, x, y = (int(v) for v in s.replace(":", "x").split("x"))
    return w, h, x, y


def _fmt_crop(w, h, x, y):
    return f"{int(w)}:{int(h)}:{int(x)}:{int(y)}"


def cam_cover_crop(cam_box, target_w=CAM_W, target_h=CAM_H):
    """Recorte COVER del rectangulo PiP de la webcam a la banda
    superior completa (1080x702): llena de lado a lado, sin
    barras negras. Recorta el lado largo del rectangulo,
    centrado; en p7775 (561x365) solo sobran 1-2 px de alto
    (561:364:0:626). La cara queda donde estaba: el cuadrado
    viejo (365:365:90:626) centraba la cara en x=272, y el
    cover la deja en x~524 de 1080, centrada."""
    w, h, x, y = _parse_crop(cam_box)
    target_aspect = target_w / target_h
    if w / h > target_aspect:
        nw = int(h * target_aspect) // 2 * 2
        return _fmt_crop(nw, h, x + (w - nw) // 2, y)
    nh = int(w / target_aspect) // 2 * 2
    return _fmt_crop(w, nh, x, y + (h - nh) // 2)


def face_center(cam_square):
    """Centro de la cara: centro del recorte cuadrado."""
    w, h, x, y = _parse_crop(cam_square)
    return x + w // 2, y + h // 2


def punch_cover_crop(cover, cx, cy, zoom=PUNCH_ZOOM):
    """Recorte del punch-in: zoom centrado en la cara, siempre
    dentro del recorte cover (nunca fuera de el). En p7775:
    cover 561:364:0:626 + cara (272, 808) + zoom 1,30 ->
    430:280:57:668."""
    w, h, x, y = _parse_crop(cover)
    nw = max(2, int(w / zoom) // 2 * 2)
    nh = max(2, int(h / zoom) // 2 * 2)
    nx = min(max(cx - nw // 2, x), x + w - nw)
    ny = min(max(cy - nh // 2, y), y + h - nh)
    return _fmt_crop(nw, nh, nx, ny)


def _game_filter(game_crop, game_zoom):
    """Filtro de la rama del gameplay: recorte fijo + zoom SUAVE
    en el payoff (scale con eval=frame + crop con ventana
    animada).

    Por que asi y no crop animado (2026-11-18 21:01): el crop
    evalua w/h UNA VEZ en la configuracion (con t=NAN) y los
    congela, asi que un crop=w='1440-216*f' nunca hace zoom
    (probado: MAD 0,0 contra el estatico). Ademas, in_w/in_h
    dentro del crop tambien son de configuracion:
    '(in_w-1080)/2' se congela en 0 y el zoom queda anclado
    arriba a la izquierda (probado: MAD 0,18 en offset (0,0)).
    Lo que SI se evalua por fotograma: w/h del scale con
    eval=frame, y x/y del crop cuando usan 't'. Por eso la
    cadena es: crop fijo -> scale eval=frame -> crop con x/y
    en funcion de t. La rampa usa if(between(...)) y NO
    clip/min: con t=NAN, clip da NAN y el filtro no configura.

    El zoom es 1,15x centrado en horizontal y anclado abajo:
    el borde inferior del recorte no se mueve (no se recorta
    la zona de dialogo del juego) y la carta del payoff queda
    centrada (verificado en still: MAD 0,18 en offset (81,141)
    contra el estatico escalado 1,15x). Sin game_zoom, recorte
    fijo + escala estatica (igual que antes)."""
    if not game_zoom:
        return f"crop={game_crop},scale={W}:{GAME_H},setsar=1"
    z0, z1 = (float(v) for v in game_zoom)
    f = (f"if(between(t,{z0:g},{z0 + 2:g}),(t-{z0:g})/2,"
         f"if(between(t,{z0 + 2:g},{z1 - 2:g}),1,"
         f"if(between(t,{z1 - 2:g},{z1:g}),({z1:g}-t)/2,0)))")
    k = GAME_ZOOM_FACTOR - 1  # 0.15
    x_coef = W * k / 2         # 81: centra la ventana en horizontal
    y_coef = GAME_H * k        # 141: ancla la ventana abajo
    return (
        f"crop={game_crop},"
        f"scale=w='{W}*(1+{k:g}*{f})':h='{GAME_H}*(1+{k:g}*{f})':eval=frame,"
        f"crop={W}:{GAME_H}:'{x_coef:g}*{f}':'{y_coef:g}*{f}',setsar=1"
    )


def build_filter(cam_box, cam_square, punch_in, duration, ass_path, fps,
                 game_crop=GAME_CROP, game_zoom=GAME_ZOOM):
    """Grafo de un solo pase, opcion C (2026-11-18): doble rama de
    camara + overlay enable para el punch-in, sin trim ni concat.

    v2 (2026-11-18 21:01): la webcam es COVER a ancho completo
    (1080x702, sin barras negras); el gameplay lleva zoom suave
    en el payoff (ver _game_zoom_crop). El color de fondo lleva
    duracion (d=duration): sin ella es una fuente infinita y
    ffmpeg no cierra nunca (cola congelada).
    """
    cover = cam_cover_crop(cam_box)
    p0, p1 = punch_in if punch_in else (None, None)
    if p0 is not None:
        cx, cy = face_center(cam_square)
        zc = punch_cover_crop(cover, cx, cy)
        head = (
            f"[0:v]fps={fps},split=2[cam_src][game_src];"
            f"[cam_src]split=2[cam_n][cam_z];"
            f"[cam_n]crop={cover},scale={CAM_W}:{CAM_H},setsar=1[camN];"
            f"[cam_z]crop={zc},scale={CAM_W}:{CAM_H},setsar=1[camZ];")
        cam_overlay = (
            f"[step1][camN]overlay={CAM_X}:{CAM_Y}:"
            f"enable='lt(t,{p0})+gte(t,{p1})'[step2a];"
            f"[step2a][camZ]overlay={CAM_X}:{CAM_Y}:"
            f"enable='gte(t,{p0})*lt(t,{p1})'[step2];")
    else:
        head = (
            f"[0:v]fps={fps},split=2[cam_src][game_src];"
            f"[cam_src]crop={cover},scale={CAM_W}:{CAM_H},setsar=1[cam];")
        cam_overlay = (
            f"[step1][cam]overlay={CAM_X}:{CAM_Y}[step2];")
    game_filt = _game_filter(game_crop, game_zoom)
    parts = [
        head,
        f"[game_src]{game_filt}[game];"
        f"color=c=black:s={W}x{H}:r={fps}:d={duration}[base];"
        f"[base][1:v]overlay=0:0[step1];",
        cam_overlay,
        f"[step2][game]overlay=0:{GAME_Y}[vout];",
    ]
    if ass_path:
        parts.append(f"[vout]ass={ass_path}[vsub]")
        vlabel = "[vsub]"
    else:
        vlabel = "[vout]"
    return "".join(parts), vlabel


def build_render_cmd(segment, start, duration, ass_path, out_path, bg_png,
                     cam_box=DEFAULT_CAM_BOX, cam_square=DEFAULT_CAM_SQUARE,
                     punch_in=(16, 26), fps=30, preset="veryfast", crf=23,
                     game_crop=GAME_CROP, game_zoom=GAME_ZOOM):
    """Comando ffmpeg del render RDJavi en un solo pase.

    La entrada ya lleva -t duration y la salida tambien: el -t de
    salida es el seguro por si alguna fuente del grafo no termina
    (sin el, un color sin duracion alargaba el render a 193 s).
    """
    filt, vlabel = build_filter(cam_box, cam_square, punch_in, duration,
                                ass_path, fps, game_crop, game_zoom)
    return ["ffmpeg", "-y", "-v", "error",
            "-ss", str(start), "-t", str(duration), "-i", segment,
            "-loop", "1", "-t", str(duration), "-i", bg_png,
            "-filter_complex", filt,
            "-map", vlabel, "-map", "0:a",
            "-c:a", "copy",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-t", str(duration), out_path]


def _ass_time(t):
    """Segundos -> H:MM:SS.cs (formato ASS)."""
    t = max(0.0, float(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


_TITLE_PREFIX_RE = re.compile(r"^\s*(GTA\s*6|GTA6)\s*[:\-–—]?\s*",
                               re.IGNORECASE)
_ARIAL_BLACK_PATHS = (
    os.path.expanduser("~/.fonts/arial-black.ttf"),
    "/usr/share/fonts/truetype/arial-black.ttf",
)


def clean_title_prefix(title):
    """Quita un 'GTA 6' inicial ('GTA 6:', 'GTA 6 -', 'GTA6 '):
    es obvio y redundante (regla del usuario 2026-11-18 21:01).
    Solo el prefijo inicial; un 'GTA 6' en medio del titulo
    no se toca."""
    return _TITLE_PREFIX_RE.sub("", title or "").strip()


def _text_width_px(text, size):
    """Ancho del texto en px con Arial Black real (PIL). Sin
    PIL o sin el TTF, estimacion 0,60*size por caracter
    (medido: 'Rubius descubre los cromos' a 64 px son 990 px,
    0,595*size por caracter)."""
    try:
        from PIL import ImageFont
        for path in _ARIAL_BLACK_PATHS:
            if os.path.exists(path):
                return ImageFont.truetype(path, size).getlength(text)
    except Exception:
        pass
    return len(text) * size * 0.60


def _split_title_lines(words, n_lines, size, max_w):
    """Reparte ``words`` en ``n_lines`` líneas equilibradas por ancho.

    Devuelve [línea, ...] si todas caben en ``max_w`` a ``size`` px
    (medido con la fuente real), o None si no hay reparto posible
    (p. ej. una sola palabra más ancha que max_w). Con 1 línea es
    el título entero; con 2, el reparto que minimiza el ancho de
    la línea más ancha; con 3+, empaquetado voraz por palabras.
    """
    if not words:
        return []
    if n_lines <= 1:
        line = " ".join(words)
        if _text_width_px(line, size) <= max_w:
            return [line]
        return None
    if n_lines == 2:
        best = None
        for i in range(1, len(words)):
            line1 = " ".join(words[:i])
            line2 = " ".join(words[i:])
            worst = max(_text_width_px(line1, size),
                        _text_width_px(line2, size))
            if best is None or worst < best[0]:
                best = (worst, [line1, line2])
        if best is not None and best[0] <= max_w:
            return best[1]
        return None
    lines, cur = [], []
    for w in words:
        cand = " ".join(cur + [w])
        if cur and _text_width_px(cand, size) > max_w:
            lines.append(" ".join(cur))
            cur = [w]
        else:
            cur = cur + [w]
    if cur:
        lines.append(" ".join(cur))
    if len(lines) > n_lines:
        return None
    if any(_text_width_px(ln, size) > max_w for ln in lines):
        return None
    return lines


def fit_title_lines(title, max_w=TITLE_MAX_W, sizes=TITLE_FONT_SIZES,
                    max_lines=2, max_h=None, line_gap=TITLE_LINE_GAP):
    """Ajuste AUTOMÁTICO del título (corrección del usuario 2026-11-19).

    El título aparece SIEMPRE COMPLETO, nunca cortado ni con puntos
    suspensivos: mide el texto con la fuente real (Arial Black) y
    reduce el tamaño hasta que quepa en el ancho disponible; si ni
    con el mínimo cabe en una línea, parte en dos líneas dentro del
    espacio disponible. Busca de mayor a menor tamaño; para cada
    tamaño prueba 1 línea, luego 2, ... hasta ``max_lines`` (con
    ``max_h``, el total de líneas tiene que caber en alto).
    Devuelve (líneas, tamaño) o ([], None) si no hay título. Si ni
    con el mínimo cabe en ``max_lines`` líneas, devuelve el título
    entero con el tamaño mínimo y el máximo de líneas (en la práctica
    no ocurre con títulos reales: a 32 px dos líneas aguantan ~60
    caracteres en la banda). Es la ÚNICA fuente del ajuste: la usan
    la banda willyrex (title_ass_lines) y el banner rubius
    (clipagent/rubius.py).
    """
    words = clean_title_prefix(title).split()
    if not words:
        return [], None
    for size in sizes:
        line_h = int(size * 1.15)
        for n in range(1, max_lines + 1):
            if (max_h is not None
                    and n * line_h + (n - 1) * line_gap > max_h):
                continue
            lines = _split_title_lines(words, n, size, max_w)
            if lines:
                return lines, size
    size = sizes[-1]
    lines = (_split_title_lines(words, max_lines, size, max_w)
             or [" ".join(words)])
    return lines, size


def fit_title(title, sizes=TITLE_FONT_SIZES, max_w=TITLE_MAX_W):
    """Título en UNA SOLA LÍNEA, COMPLETO (revisión 2026-11-19):
    limpia el prefijo 'GTA 6' y ACHICA LA LETRA hasta que el título
    entero quepa; nunca acorta por palabras (la v2 2026-11-18
    cortaba: 'Risa imparable por un asesinato' salía 'Risa
    imparable por un'). Devuelve (texto, tamaño) o (None, None).
    Si ni con el mínimo cabe en una línea, devuelve el título
    entero con el tamaño mínimo (el llamador que necesite el
    repliegue a dos líneas usa fit_title_lines).
    Ej: 'GTA 6: Rubius descubre los cromos coleccionables' ->
    ('Rubius descubre los cromos coleccionables', 40)."""
    words = clean_title_prefix(title).split()
    if not words:
        return None, None
    limit = max_w - 2 * TITLE_OUTLINE  # el contorno ensancha el texto
    text = " ".join(words)
    for size in sizes:
        if _text_width_px(text, size) <= limit:
            return text, size
    return text, sizes[-1]


def wrap_title(title, max_chars=None):
    """Título ajustado (revisión 2026-11-19): devuelve las líneas
    del título COMPLETO (una o dos, ver fit_title_lines), nunca
    con 'GTA 6' delante y nunca cortado. Con las medidas de la
    banda (ancho útil y 98 px de alto): primero achica la letra,
    solo después parte en dos líneas. ``max_chars`` es legado
    y ya no se usa: el ajuste es por ancho en px con la fuente
    real."""
    lines, _size = fit_title_lines(
        title, max_w=TITLE_MAX_W - 2 * TITLE_OUTLINE,
        max_lines=2, max_h=TITLE_BAND_H)
    return lines


def title_ass_lines(title, duration,
                    font_size=None):
    """Líneas ASS del título quemado: estilo + evento 0..duration.

    El título va en la franja negra entre la webcam y el gameplay
    (y 702..800), centrado verticalmente en la franja: UNA línea
    si cabe (a 64 px MarginV 714) o DOS líneas si el título es muy
    largo (letra más chica, ver fit_title_lines). Blanco Arial
    Black, contorno negro 5. El texto y el tamaño los decide
    fit_title_lines (sin 'GTA 6' delante, COMPLETO: nunca se
    acorta por palabras). Dos líneas van en un solo evento con
    \\N (salto de línea ASS).
    """
    sizes = (font_size,) if font_size else TITLE_FONT_SIZES
    lines, size = fit_title_lines(
        title, max_w=TITLE_MAX_W - 2 * TITLE_OUTLINE, sizes=sizes,
        max_lines=2, max_h=TITLE_BAND_H)
    if not lines:
        return None, None
    line_h = int(size * 1.15)
    total_h = len(lines) * line_h + (len(lines) - 1) * TITLE_LINE_GAP
    margin_v = TITLE_BAND_Y0 + (TITLE_BAND_H - total_h) // 2
    # ``size`` y TITLE_OUTLINE son pixeles REALES; el ASS lleva
    # Fontsize/Outline convertidos (ver ASS_FONT_SCALE).
    style = (
        f"Style: Title,{SUBS_FONT},{ass_font_size(size)},&H00FFFFFF,"
        f"&H00FFFFFF,&H00000000,&HFF000000,-1,0,0,0,100,100,0,0,"
        f"1,{ass_font_size(TITLE_OUTLINE)},0,8,10,10,{margin_v},1"
    )
    text = "\\N".join(lines)
    event = (f"Dialogue: 0,0:00:00.00,{_ass_time(duration)},Title,,"
             f"0,0,0,,{text}")
    return style, event


def add_burned_title(ass_text, title, duration):
    """Añade el titulo quemado a un ASS ya generado (karaoke).

    Inserta el estilo Title despues del ultimo estilo existente
    y el evento 0..duration al final. Sin titulo, devuelve el
    ASS tal cual. No toca los eventos de subtitulos.

    IDEMPOTENTE (bug 2026-11-18 20:47): si el ASS ya trae un
    estilo Title (p. ej. generado por make_plain_ass con
    title=... y luego --title en el CLI), NO se añade otro.
    Dos eventos Title identicos no se superponen: libass los
    apila por colision y el segundo titulo cae SOBRE el
    gameplay, tapandolo. Un solo titulo, siempre.
    """
    if not title or not ass_text:
        return ass_text
    if "Style: Title," in ass_text:
        return ass_text
    style, event = title_ass_lines(title, duration)
    if style is None:
        return ass_text
    lines = ass_text.rstrip("\n").split("\n")
    style_idx = max((i for i, ln in enumerate(lines)
                     if ln.startswith("Style:")), default=None)
    if style_idx is None:
        return ass_text
    lines.insert(style_idx + 1, style)
    lines.append(event)
    return "\n".join(lines) + "\n"


def make_karaoke_ass(blocks, style, font_name=SUBS_FONT,
                     title=None, duration=None):
    """ASS karaoke ABAJO DEL TODO a partir de bloques SRT.

    Usa el estilo karaoke de la config (default, 2026-11-18) con
    Alignment 2 y MarginV 12: el subtitulo queda en la barra negra
    y 1740..1920, debajo del gameplay, nunca sobre el (correccion
    Willyrex del usuario 2026-11-18 20:13; la version anterior lo
    ponia al pie de la camara, Alignment 8 / MarginV 608). El
    generador base pone Alignment 2 / MarginV 230 (parte baja del
    gameplay); aqui se fuerza la posicion RDJavi.

    Con ``title`` se quema ademas el titulo del clip en la franja
    negra entre la webcam y el gameplay (ver add_burned_title):
    es el comportamiento por defecto de la plantilla desde
    2026-11-18 19:50. ``duration`` es la duracion del clip; si no
    se pasa, se toma el fin del ultimo bloque.
    """
    from .transcribe import srt_to_karaoke_ass
    # Calibración libass (2026-11-18): el karaoke de la plantilla
    # usa Arial Black, así que el Fontsize del ASS lleva el factor
    # ASS_FONT_SCALE (56 px reales -> Fontsize 79); sin él el texto
    # salía a 0,71x (40 px) aunque la fuente fuera la misma.
    ass = srt_to_karaoke_ass(blocks, style, font_name=font_name,
                             alignment=SUBS_ALIGN,
                             margin_v=SUBS_MARGIN_V,
                             font_scale=ASS_FONT_SCALE)
    if title:
        if duration is None:
            ends = [b[1] for b in blocks
                    if len(b) > 1 and isinstance(b[1], (int, float))]
            duration = max(ends) if ends else 0.0
        ass = add_burned_title(ass, title, duration)
    return ass


def make_plain_ass(blocks, title=None, duration=None, max_end=None,
                   font_name=SUBS_FONT, font_size=SUBS_FONT_SIZE):
    """ASS de subtitulos FIJOS amarillos estilo Willyrex.

    Estilo por defecto de la plantilla desde la correccion del
    usuario 2026-11-18 20:13 (captura del reel @gta_vi_clips_v),
    con tamano subido a 64 px en la revision v2 (2026-11-18
    21:01: en el ejemplo los subtitulos son mas grandes que
    los 56 px viejos): todo el texto amarillo 64 px con contorno
    negro grueso (6), Arial Black (la tipografia del ejemplo,
    2026-11-18 20:33 y 20:47), ABAJO DEL TODO en la barra negra
    (Alignment 2, MarginV 12), nunca sobre el gameplay. Sin
    etiquetas \\k: el karaoke palabra por palabra no se puede
    hacer con este estilo (su resto blanco no es como la
    captura), asi que va fijo amarillo.

    ``blocks`` son (start, end, text) como en parse_srt;
    ``max_end`` recorta los bloques fuera del clip. Con
    ``title`` se quema ademas el titulo en la franja (ver
    add_burned_title). Las llaves se neutralizan: en ASS
    serian etiquetas de anulacion.
    """
    from .transcribe import hex_to_ass_color
    yellow = hex_to_ass_color(SUBS_COLOR, 1.0)
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
        # ``font_size`` y SUBS_OUTLINE son pixeles REALES; el ASS
        # lleva Fontsize/Outline convertidos (ver ASS_FONT_SCALE).
        f"Style: Subs,{font_name},{ass_font_size(font_size)},"
        f"{yellow},{yellow},"
        f"{black},{transparent},-1,0,0,0,100,100,0,0,1,"
        f"{ass_font_size(SUBS_OUTLINE)},"
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
        clean = (text.replace("{", "(").replace("}", ")")
                 .replace("\\N", " ").strip())
        if not clean:
            continue
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Subs,,"
            f"0,0,0,,{clean}\n"
        )
    ass = "".join(lines)
    if title:
        if duration is None:
            ends = [b[1] for b in blocks
                    if len(b) > 1 and isinstance(b[1], (int, float))]
            duration = max(ends) if ends else 0.0
        ass = add_burned_title(ass, title, duration)
    return ass


def render(segment, start, duration, ass_path, out_path, bg_png,
           cam_box=DEFAULT_CAM_BOX, cam_square=DEFAULT_CAM_SQUARE,
           punch_in=(16, 26), fps=30, preset="veryfast", crf=23,
           game_crop=GAME_CROP, game_zoom=GAME_ZOOM, watermark=None):
    """Render RDJavi en un solo pase. Devuelve los segundos que tardo.

    ``watermark``: dict {source|url, opacity, size_px, position}
    o ruta/URL suelta (ver clipagent/watermark.py). Se compone en
    un pase final sobre el render, sin tocar el grafo de un solo
    pase; si falla, el render sigue sin marca. Sin watermark, el
    render es exactamente el de siempre.
    """
    cmd = build_render_cmd(segment, start, duration, ass_path, out_path,
                           bg_png, cam_box=cam_box, cam_square=cam_square,
                           punch_in=punch_in, fps=fps, preset=preset,
                           crf=crf, game_crop=game_crop,
                           game_zoom=game_zoom)
    dt = _run(cmd, "render RDJavi")
    if watermark:
        from .watermark import maybe_apply_watermark
        wm = watermark if isinstance(watermark, dict) else {
            "enabled": True, "source": watermark}
        maybe_apply_watermark(out_path, {"watermark": wm})
    return dt


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--segment", required=True)
    p.add_argument("--start", type=float, default=0)
    p.add_argument("--duration", type=float, required=True)
    p.add_argument("--ass", default=None, help="ASS de subtitulos simples")
    p.add_argument("--title", default=None,
                   help="Titulo a quemar en la franja negra entre la "
                        "webcam y el gameplay (por defecto de la "
                        "plantilla; ver --no-title)")
    p.add_argument("--no-title", action="store_true",
                   help="No quemar titulo aunque se pase --title")
    p.add_argument("--out", required=True)
    p.add_argument("--bg", default=None, help="PNG de fondo ya hecho")
    p.add_argument("--gradient", default=None, help="PNG gradiente Vice City")
    p.add_argument("--bg-frame", type=float, default=20,
                   help="Segundo del clip para el fondo (default 20)")
    # Sin default silencioso: los defaults son la caja de p3220
    # (webcam arriba a la izquierda) y usarlos en otro clip
    # RECORTA JUEGO en la banda de la webcam, no al streamer
    # (bug 2026-11-18 20:47 en p7775: el re-render _black se hizo
    # sin --cam-square y la webcam salio juego). Si no se pasan,
    # se usan los de p3220 PERO con un aviso bien visible.
    p.add_argument("--cam-box", default=None)
    p.add_argument("--cam-square", default=None)
    p.add_argument("--game-crop", default=GAME_CROP,
                   help="Recorte 1:1 del gameplay (default 1440:1440:620:0 "
                        "para segmentos 2560x1440; 1080:1080:465:0 si el "
                        "segmento se pre-transcodifico a 1920x1080)")
    p.add_argument("--punch-in", type=float, nargs=2, default=[16, 26])
    p.add_argument("--no-punch-in", action="store_true")
    p.add_argument("--game-zoom", type=float, nargs=2, default=[18, 24],
                   help="Ventana del zoom suave del gameplay en el payoff "
                        "(default 18 24)")
    p.add_argument("--no-game-zoom", action="store_true",
                   help="Sin zoom del gameplay en el payoff")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--preset", default="veryfast")
    p.add_argument("--crf", type=int, default=23)
    p.add_argument("--watermark", nargs="?", const="default", default=None,
                   help="Marca de agua: ruta local o URL de un PNG. Sin "
                        "valor usa el logo BITSONER de colores del repo "
                        "(arriba a la derecha, 320 px, 20%%). Sin este "
                        "flag no se aplica marca.")
    p.add_argument("--watermark-opacity", type=float, default=20,
                   help="Opacidad de la marca 0-100 (default 20)")
    p.add_argument("--watermark-size", type=int, default=320,
                   help="Diámetro de la marca en px (default 320)")
    p.add_argument("--watermark-position", default="top-right",
                   choices=["center", "top-left", "top-right",
                            "bottom-left", "bottom-right"],
                   help="Posición de la marca (default top-right)")
    args = p.parse_args(argv)

    # Los defaults son la caja de p3220 (webcam arriba a la
    # izquierda). Si no se pasaron, avisar FUERTE: en p7775 la
    # webcam esta abajo a la izquierda (561:365:0:626 /
    # 365:365:90:626) y el default recorta juego en la banda
    # de la webcam (bug 2026-11-18 20:47).
    cam_box = args.cam_box or DEFAULT_CAM_BOX
    cam_square = args.cam_square or DEFAULT_CAM_SQUARE
    if args.cam_box is None or args.cam_square is None:
        print("AVISO: --cam-box/--cam-square no especificados: "
              "usando los de p3220 (webcam arriba a la izquierda, "
              f"cam-box {DEFAULT_CAM_BOX} / cam-square "
              f"{DEFAULT_CAM_SQUARE}). Si la webcam de este clip "
              "esta en otro sitio, pasa los valores medidos "
              "(p7775: --cam-box 561:365:0:626 --cam-square "
              "365:365:90:626); si no, la banda de la webcam "
              "muestra JUEGO, no al streamer.",
              file=sys.stderr)
    print(f"cam-box {cam_box} / cam-square {cam_square}")

    workdir = os.path.dirname(os.path.abspath(args.out))
    bg = args.bg
    if not bg:
        if not args.gradient:
            raise SystemExit("--bg o --gradient para hacer el fondo")
        bg = os.path.join(workdir, "bg.png")
        t0 = time.time()
        make_bg_still(args.segment, args.start + args.bg_frame,
                      cam_box, args.gradient, bg)
        print(f"fondo pre-renderizado: {time.time()-t0:.1f} s -> {bg}")
    ass_path = args.ass
    if args.title and not args.no_title:
        # Titulo quemado por defecto: se inyecta en una copia del
        # ASS (el original no se toca) y se quema en el mismo pase.
        with open(args.ass, encoding="utf-8") as f:
            base_ass = f.read()
        titled = add_burned_title(base_ass, args.title, args.duration)
        ass_path = os.path.join(workdir, "subs_title.ass")
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(titled)
        print(f"titulo quemado: {wrap_title(args.title)!r}")
    punch = None if args.no_punch_in else tuple(args.punch_in)
    game_zoom = None if args.no_game_zoom else tuple(args.game_zoom)
    watermark = None
    if args.watermark:
        from .watermark import DEFAULT_WATERMARK_PNG
        src = (DEFAULT_WATERMARK_PNG if args.watermark == "default"
               else args.watermark)
        watermark = {"enabled": True, "source": src,
                     "opacity": args.watermark_opacity,
                     "size_px": args.watermark_size,
                     "position": args.watermark_position}
    t0 = time.time()
    dt = render(args.segment, args.start, args.duration, ass_path,
                args.out, bg, cam_box=cam_box, cam_square=cam_square,
                punch_in=punch, fps=args.fps, preset=args.preset,
                crf=args.crf, game_crop=args.game_crop,
                game_zoom=game_zoom, watermark=watermark)
    print(f"render RDJavi: {dt:.1f} s ({args.duration:.1f} s de clip, "
          f"{args.duration/dt:.2f}x) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
