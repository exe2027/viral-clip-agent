#!/usr/bin/env python3
"""
layout.py — layout RESPONSIVE para los clips verticales (REGLA del proyecto).

Regla (usuario, 2026-11-16): el recorte vertical NO puede ser un recorte
ciego al centro. El clip de Coker lo demostró: el directo eran DOS webcams
lado a lado con una franja fina de gameplay arriba y barras negras, y el
recorte centrado tomaba media cara de cada uno + las barras negras:
"no se ve nada". A partir de ahora, cada clip decide su layout por
fotogramas muestreados, antes de cortar:

  * Dos webcams lado a lado -> se APILAN verticalmente, una encima de la
    otra, llenando los 1080x1920 sin barras negras.
  * Una webcam + gameplay -> SPLIT VERTICAL (regla 2026-11-16,
    INVERTIDO 2026-11-18 por el usuario tras ver los reels de
    referencia de RDJavi (Df4sy4bxlmr) y DJ Mariio (Df4uyMVjDwq):
    split-screen con el STREAMER ARRIBA y el GAMEPLAY ABAJO): la
    cámara ARRIBA (~31%, 600 px) siguiendo al streamer (la caja de
    la webcam detectada, con seguimiento por tramo) y el juego
    ABAJO ocupando la mayor parte del frame (~69%, 1320 px), sin
    tapar minimapa ni HUD (la cámara nunca se superpone al juego).
    Si el juego no se ve, la cámara se ACHICA, nunca al revés.
    Proporciones provisionales (600 arriba / 1320 abajo) hasta el
    desglose fotograma a fotograma del reel que está viendo el
    navegador: cuando llegue, se documentan las exactas aquí.
  * Solo gameplay, sin webcam -> recorte vertical centrado normal.
  * Si el layout cambia dentro del clip (p. ej. de cams a gameplay),
    el layout se adapta POR TRAMOS, no un recorte fijo para todo el clip.

Cómo se detecta (sin dependencias nuevas: solo stdlib + ffmpeg):
  * Se muestrean fotogramas en gris a 160x90 cada N segundos.
  * Dos cams: la banda de contenido es una franja apilada (alto <= 65%
    del fotograma, con barras negras arriba/abajo) Y hay una costura
    vertical fuerte justo en el centro (el divisor entre las dos cams).
    Calibrado 2026-11-16 con 3 VODs reales: Coker (dos cams) daba banda
    45/90 y costura 47-55 en el centro; Locones y NoraExplorer
    (gameplay) daban banda completa y costura < 25 en posiciones al azar.
    Umbrales: banda <= 0.65, costura a <= 8% del centro, fuerza >= 30.
  * Una cam (PiP): un rectángulo con borde fuerte en una esquina SUPERIOR
    (solo superiores: abajo están el minimapa y el HUD de GTA, que
    dispararían falsos positivos) y con interior con textura. Detección
    heurística v1: si no está claro, cae a centrado (seguro) y se puede
    forzar con ``facecam_box`` en config.yaml (split vertical) o
    ``--layout pip`` (PiP explícito, único caso en que queda PiP).
  * Si ``facecam_box`` está en config.yaml, manda sobre la detección.

La decisión queda registrada en el manifiesto por clip (qué layout se
usó y por qué) y es configurable en config.yaml (sección ``layout``)
y con ``--layout auto|stack|pip|center`` en auto_clips.py.

Bloque 4 (2026-11-16) — escaneo por tramo, zonas seguras, fondo
borroso y hoja de contacto (todo en este archivo, sin tocar el resto):

  * Escaneo por tramo (``scan_segments``): además de clasificar cada
    fotograma, muestrea 1 fotograma cada 2 s y compara fotogramas
    consecutivos (diferencia de frames por celdas de 10x10 px). Una
    webcam es una región ESTABLE: su fondo no cambia mientras el
    gameplay cambia en todas partes. Cada tramo se clasifica en
    0/1/2 cams, y se marca si la cam es un avatar circular (las
    esquinas de su caja son inestables —se ve el gameplay— mientras
    el centro es estable). La detección se restringe a regiones
    estables, así no confunde caras del juego (que se mueven con la
    cámara) con webcams. Ojo: una imagen ESTÁTICA (menú de pausa,
    pantalla de carga) también es estable en ambas mitades — el
    discriminador es la costura vertical fuerte justo en el centro
    (el divisor entre las dos cams); sin costura, el escaneo lo
    reporta con la advertencia y decide_layout NO lo usa para
    corregir a stack (Locones 0-7 s: 67-72% estables sin costura).
    Si hay OpenCV disponible (cv2 + Haar),
    ``_detect_faces_opencv`` busca rostros y solo acepta los que
    caen dentro de una región estable; si no hay cv2 (caso actual:
    el Python del sistema no lo tiene y el venv solo tiene cv2 5.0
    sin CascadeClassifier), el escaneo por diferencia de frames es
    el método principal. ``decide_layout`` usa el escaneo para
    confirmar o corregir la clasificación por fotograma.
  * Mapa de zonas seguras (``SAFE_ZONES`` / ``get_safe_zones``):
    GTA V tiene el minimapa abajo-izquierda y el HUD de salud/
    arma abajo-derecha: el PiP nunca va abajo y nunca tapa esas
    zonas. GTA 6 no tiene mapa confirmado el día 1: el fallback
    detecta regiones persistentes en las esquinas (un HUD es
    persistente por definición) y se puede fijar a mano con
    ``safe_zones`` en config.yaml; el mapa definitivo se fija
    después de los primeros clips reales de GTA 6.
  * Fondo borroso en stack: al apilar, cada mitad se ajusta por
    contenido (contain) sobre su propio frame escalado y difuminado
    (``boxblur``), en vez de barras negras. Además el recorte de
    cada mitad es ahora la mitad COMPLETA (antes se recortaba al
    centro 9:8 de cada mitad), así el apilado no corta caras por
    los lados.
  * Hoja de contacto (``contact_sheet``): 9 fotogramas del clip
    en una imagen 3x3, cada uno con el rectángulo de recorte que
    el layout usará en ese instante (verde) y las zonas seguras
    (rojo). Quien aprueba ve en 1 imagen si el minimapa o una
    cara quedan fuera.
  * 100% local, $0 (decisión del usuario, 2026-11-16): este bloque
    no usa ninguna API paga ni pide ninguna key — solo stdlib +
    ffmpeg local (y OpenCV opcional, también local, sin red).
    Nada de Gemini, OpenAI, DeepSeek ni ElevenLabs: Gemini pidió
    tarjeta al crear la key, así que no se pone tarjeta ni se crea
    cuenta de facturación. ``layout.py`` no transcribe nada (eso
    es faster-whisper tiny local en ``subtitles.py``) y no sale a
    la red.

Arreglo 6 (2026-11-16) — el recorte SIGUE la cara dentro del tramo:
  * Causa raíz del fallo de Coker (~31 s): el recorte era FIJO por
    tramo por dos motivos. (1) decide_layout tomaba los params de
    la ÚLTIMA muestra del tramo: split_x=68 en muestra (divisor
    real 80 = 640 px: a partir de t=12 el gradiente más fuerte era
    el borde de la cabeza/puerta dentro de la cam izquierda, no el
    divisor) y ese split fijo se aplicaba a los 60 s. Al sentarse
    (~31 s) la persona de la izquierda se mueve hacia el divisor y
    su cara se corta por la derecha del panel superior; un trozo de
    su cam se cuela en el panel inferior. (En el clip v1 el problema
    era peor: cada mitad se recortaba a su centro 9:8 y Coker, a la
    derecha de su cam, quedaba fuera del todo; eso ya estaba
    arreglado en HEAD: cada mitad se recorta COMPLETA.) (2) El
    recorte 'center'/'pip' era una expresión fija ((iw-ih*9/16)/2)
    y _detect_faces_opencv solo se usaba como nota de confirmación
    en el escaneo, nunca para posicionar el recorte.
  * Ahora, por tramo: en 'stack' se sigue el DIVISOR (la cara de
    cada mitad queda dentro porque la mitad se incluye entera):
    cada muestra ancla el split al borde entre las dos cajas
    estables del escaneo (79.5 en Coker, o sea el medio: las cajas
    son las dos mitades por construcción) y se suaviza (mediana de
    3 + límite de velocidad); además los params del tramo son la
    MEDIANA de sus muestras, no la última. En 'center' se sigue la
    CARA con Haar (OpenCV): se muestrea a 640x360 cada 1 s —a
    160x90 Haar no detecta ninguna cara en este material—, se
    asocia por centro más cercano con querencia por la cara seguida
    (idea de SpeakerTracker de OpenShorts) y se suaviza como su
    SmoothedCameraman ("trípode pesado"): zona muerta (no moverse
    mientras la cara está cerca del centro del recorte) + velocidad
    máxima en px/s + sin pasarse del objetivo; sin cara, se mantiene
    el último centro (hold, no salto al centro). El movimiento se
    interpola linealmente entre muestras con una expresión ffmpeg en
    el crop (x en función de t), un solo pase, sin trocear el tramo.
    En 'pip' no hay seguimiento: la cara va en el PiP (posición
    fija) y el fondo es gameplay.
  * Requisitos y límites: el seguimiento de caras necesita OpenCV
    con CascadeClassifier + el XML de Haar. cv2 5.0 (el que traía el
    venv) NO tiene CascadeClassifier ni el XML; funciona con
    opencv-python-headless 4.11 instalado en el .venv. Sin OpenCV,
    o sin caras detectadas (caras tapadas por las manos, de
    espaldas), el recorte queda fijo como antes (comportamiento
    seguro, sin error). Haar es frágil con caras ocluidas o de
    perfil: en Coker a t=20-31 solo detecta a Coker (la otra cara
    está tapada por las manos o agachada); el seguimiento mantiene
    el último centro en esos huecos. No se instaló MediaPipe ni
    YOLO (prohibido por el proyecto: modelos gigantes; OpenShorts
    los usa, aquí no). El ancla del divisor asume cams 50/50 lado a
    lado; un directo con mitades desiguales quedaría con el divisor
    en el medio (límite conocido).

Detección de vtuber / PNG tuber (2026-11-17, VOD Rubius 2931565552):
  * Causa del fallo: en el tramo 10332-10372 el avatar es un gato
    estilo Miku abajo a la izquierda (pelo celeste, orejas,
    audífonos) cuya boca se abre/cierra con la voz. No es una cara
    humana (Haar no lo ve: faces_1fps 0 en todo el clip), no toca
    el borde superior (el detector de cajas de esquina solo busca
    arriba) y el escritorio de fondo es estático, así que el
    escaneo daba 2 cams estables al 100% sin costura central y lo
    trataba como imagen estática. El clip caía a center con
    seguimiento de cara y el gato se perdía del recorte.
  * Heurística (``_detect_vtuber_candidates``): sobre los
    fotogramas del escaneo (160x90, cada 2 s), un parche de celdas
    con cambio fuerte (boca/parpadeo) en una esquina, con la caja
    que lo rodea CASI INMÓVIL en ese mismo par de fotogramas (el
    avatar está quieto; solo se mueve la cara) y presente en una
    racha larga de muestras. La caja es el parche + 1 celda de
    margen (pelo/cuerpo) y debe tocar una esquina y medir 3-35%
    del fotograma. Si no cumple todo, no hay vtuber y el layout
    cae a center como antes. ``decide_layout`` convierte esa caja
    en pip y luego en split (streamer arriba, avatar abajo), igual
    que con una webcam humana; si la caja cambia dentro del clip
    (p. ej. gato abajo-izquierda y luego cámara humana
    arriba-izquierda), los tramos se parten por caja, no solo por
    layout, para no aplicar una caja a un tramo donde no está.
"""

import os
import subprocess

OUT_W, OUT_H = 1080, 1920
SAMPLE_W, SAMPLE_H = 160, 90

DEFAULTS = {
    "mode": "auto",            # auto | stack | pip | center
    "sample_every_s": 5,       # muestrear un fotograma cada N segundos
    "min_segment_s": 3,        # un tramo más corto se fusiona con el vecino
    "pip_width": 480,          # ancho de la webcam en PiP (px, sobre 1080)
    "pip_corner": "top-right", # top-right | top-left (nunca abajo: minimapa/HUD)
    "pip_margin": 24,          # margen de la webcam PiP al borde
    # Split vertical (webcam+gameplay, regla 2026-11-16, INVERTIDO
    # 2026-11-18: streamer ARRIBA, juego ABAJO — reels de referencia
    # RDJavi Df4sy4bxlmr y DJ Mariio Df4uyMVjDwq). Alturas en px
    # sobre 1080x1920 (600+1320=1920).
    "split_game_h": 1320,      # alto del panel de juego (mayor parte)
    "split_cam_h": 600,        # alto del panel de cámara
    "split_cam_small_h": 480,  # cámara achicada si el juego no se ve
    "split_cam_max_w_frac": 0.45,  # caja de cam > 45% del ancho -> achicarla
    # Variante del split (2026-11-18): "auto" (default) resuelve por
    # juego: GTA 6 -> "rdjavi" (reel de referencia de RDJavi,
    # Df4sy4bxlmr, personalizado Vice City). "plain" es el split
    # simple (cámara 600 arriba / juego 1320 abajo). "djmariio"
    # está APARCADO por orden del usuario 2026-11-18: si se pide,
    # se resuelve a "rdjavi" con aviso, no se usa como layout.
    "split_variant": "auto",   # auto | rdjavi | plain | djmariio (aparcado)
    # Detección de encuadre de la webcam (2026-11-18): la caja
    # detectada dice si el streamer muestra SOLO LA CARA (plano
    # medio, RDJavi) o su SETUP en plano ancho (DJ Mariio). Se
    # registra en el manifiesto (params["framing"]); mientras DJ
    # Mariio esté aparcado, el layout sigue siendo RDJavi en los
    # dos casos. Ancho = caja ancha Y que abarque gran parte del
    # frame: aspecto >= 1.8 y ancho >= 60% del frame.
    "framing_wide_aspect": 1.8,
    "framing_wide_width_frac": 0.6,
    # Umbrales de detección (calibrados, ver docstring):
    "band_max_frac": 0.65,     # banda de contenido <= 65% del alto -> letterbox
    "seam_center_tol": 0.08,   # costura a <= 8% del ancho del centro
    "seam_min_strength": 30,   # fuerza mínima de la costura central
    "pip_border_min": 20,      # borde mínimo de la caja PiP
    "pip_border_ratio": 3.0,   # borde >= 3x el gradiente mediano del fotograma
    "pip_min_std": 18,         # textura mínima dentro de la caja PiP
    # Bloque 4: escaneo por tramo (diferencia de frames) y zonas seguras.
    "scan_enabled": True,      # usar scan_segments dentro de decide_layout
    "scan_every_s": 2,         # 1 fotograma cada N s para el escaneo
    "stable_diff_thr": 8,      # dif. media por celda <= thr -> celda estable
    "stable_cell_frac": 0.45,  # >= 45% celdas estables en una mitad -> cam
    "stable_corner_frac": 0.6, # esquina persistente si >= 60% celdas estables
    "blur_radius": 20,         # radio del boxblur del fondo en stack
    "game": "GTA 6",           # juego para el mapa de zonas seguras
    "safe_zones": None,        # override manual: [[x0,y0,x1,y1], ...] en 0..1
    # Vtuber / PNG tuber (2026-11-17, VOD Rubius 2931565552): un avatar
    # reactivo (anime, sin cara humana) no lo ve Haar y no tiene borde
    # de caja en esquina superior, así que ni _detect_corner_box ni
    # _face_cam_box lo detectan y el clip caía a center. Heurística
    # temporal sobre los fotogramas del escaneo (sin dependencias
    # nuevas, sin ML): un parche pequeño en una esquina que cambia
    # fuerte (boca que se abre/cierra, parpadeo) mientras el resto de
    # su caja NO cambia (el avatar está quieto; solo se mueve la
    # cara), presente en una racha larga de muestras. Una alerta
    # (avatar que aparece/desaparece animándose) cambia también su
    # caja entera al aparecer y dura 2-3 muestras: la racha mínima
    # la descarta. Sin parche que cumpla todo, no hay vtuber y el
    # layout sigue cayendo a center como antes.
    "vtuber_enabled": True,   # detectar avatares reactivos en el escaneo
    "vtuber_active_thr": 25,  # dif. media por celda = cambio fuerte
    "vtuber_min_cells": 2,    # parche activo mínimo (celdas 10x10)
    "vtuber_max_cells": 24,   # parche activo máximo (más = gameplay, no cara)
    "vtuber_body_thr": 8,     # el resto de la caja no debe moverse (dif. media)
    "vtuber_state_thr": 30,   # los dos estados del par (boca abierta /
                              # cerrada) tienen brillo parecido; aparecer /
                              # desaparecer lo cambia mucho más (gato 83->28)
    "vtuber_present_tol": 20, # |media del parche - estado| <= tol -> presente
    "vtuber_present_frac": 0.20,  # presente en >= 20% de las muestras
    "vtuber_min_run": 4,      # racha consecutiva mínima (muestras de 2 s)
    "vtuber_margin_cells": 1, # margen alrededor del parche (pelo/cuerpo)
    "vtuber_min_area_frac": 0.03,  # caja entre 3% y 35% del fotograma
    "vtuber_max_area_frac": 0.35,
    "vtuber_min_std": 10,     # textura mínima dentro de la caja
    # Arreglo 6: seguimiento de la cara / del divisor dentro del tramo.
    "track_enabled": True,     # seguir la cara (center) o el divisor (stack)
    "track_every_s": 1,        # muestrear la cara cada N s (layout center)
    "track_w": 640,            # resolución de muestreo para Haar: a 160x90
    "track_h": 360,            # no detecta ninguna cara en este material
    "track_min_neighbors": 5,  # vecinos Haar: 5 quita falsos positivos (t=34)
    "track_min_face": 20,      # lado mínimo de cara en px (a resolución track)
    "track_match_frac": 0.15,  # radio de asociación entre muestras (ancho)
    "track_dead_zone_frac": 0.15,  # zona muerta: fracción del ancho de recorte
    "track_max_speed_frac": 0.03,  # velocidad máx. del centro: fracción del ancho / s
    "track_split_max_speed_frac": 0.01,  # el divisor casi no se mueve
}

# ---------------------------------------------------------------------------
# Layout GTA 6 personalizado — variante RDJavi (reel de referencia
# https://www.instagram.com/reel/Df4sy4bxlmr/, desglose fotograma a
# fotograma del usuario, 2026-11-18). Es el layout POR DEFECTO para
# GTA 6 (el panel es solo GTA 6). DJ Mariio (Df4uyMVjDwq) queda
# APARCADO por orden del usuario 2026-11-18: no se usa como layout.
#
# PLANTILLA v2 (revisión 2026-11-18 21:01, portada al camino
# automático 2026-11-18): la geometría vieja (fondo desenfocado
# 0-730, webcam 580x590 centrada con esquinas redondeadas,
# gameplay 730-1770, banda inferior 1770-1920 con handle del canal
# y barra atardecer) queda RETIRADA: la v2 es la de clipagent/rdjavi.py
# (captura de Willyrex): webcam a ANCHO COMPLETO arriba, título en
# UNA línea en la franja negra, gameplay abajo, subtítulos en la
# barra negra inferior. Sin fondo desenfocado (la webcam tapa todo
# el ancho), sin esquinas redondeadas, sin handle ni barra atardecer.
# ---------------------------------------------------------------------------
# Geometría exacta v2 sobre el lienzo 1080x1920:
RDJAVI_TOP_H = 702        # banda de webcam 0-702 (ancho completo)
RDJAVI_CAM_W = 1080       # webcam: cover del rect PiP a 1080x702
RDJAVI_CAM_H = 702
RDJAVI_CAM_X = 0
RDJAVI_CAM_Y = 0
RDJAVI_TITLE_Y0 = 702     # franja negra del título 702-800 (98 px)
RDJAVI_TITLE_H = 98
RDJAVI_GAME_Y0 = 800      # gameplay 800-1740 (1080x940)
RDJAVI_GAME_H = 940
RDJAVI_BAND_Y0 = 1740     # barra negra de subtítulos 1740-1920 (180 px)
RDJAVI_BAND_H = 180
# Recorte del gameplay (fracciones del fotograma original, las de
# rdjavi.GAME_CROP 1440:1252:620:94 sobre 2560x1440): aspecto
# 1080:940, centrado en vertical y desplazado a la derecha en
# horizontal (en p7775 centra la carta del payoff).
RDJAVI_GAME_CROP_FRACS = (1440 / 2560, 1252 / 1440,
                           620 / 2560, 94 / 1440)  # w, h, x, y
# Punch-in de la webcam en la reacción y zoom suave del gameplay en
# el payoff (defaults v2 de rdjavi.py; el pico va en el segundo 20
# del clip por construcción: corte pico -20 s / +40 s).
RDJAVI_PUNCH_WINDOW = (16, 26)
RDJAVI_PUNCH_ZOOM = 1.30
RDJAVI_GAME_ZOOM_WINDOW = (18, 24)
RDJAVI_GAME_ZOOM_FACTOR = 1.15
# Branding viejo del canal (Vice City: rosa/violeta/naranja
# atardecer): RETIRADO con la v2 (la plantilla no lleva handle ni
# barra atardecer). Las constantes quedan por compatibilidad con
# código que las importe, pero el grafo RDJavi ya no las usa.
CHANNEL_HANDLE = "@ELCLIPDEGTA6 | GTA VI"
SUNSET_BAR_H = 6          # barra atardecer en la costura gameplay/banda
SUNSET_COLORS = ("#FF2E88", "#7B2FFF", "#FF9E00")  # rosa, violeta, naranja
CHANNEL_HANDLE_FONTSIZE = 34
CHANNEL_HANDLE_FONTFILE = ("/usr/share/fonts/truetype/dejavu/"
                           "DejaVuSans-Bold.ttf")


def detect_framing(box_sample, cfg=None):
    """Encuadre de la webcam a partir de su caja detectada.

    box_sample: [x0, y0, w, h] en el fotograma de muestra (160x90),
    o None si no se detectó caja. Devuelve "face" (solo la cara,
    plano medio cabeza y hombros, como RDJavi) o "setup" (el
    streamer muestra su setup en plano ancho: habitación, PC,
    luces, como DJ Mariio). Ancho = caja con aspecto >= 1.8 y que
    abarque >= 60% del ancho del frame. Sin caja, "face" (default
    GTA 6: RDJavi).
    """
    cfg = cfg or {}
    if not box_sample:
        return "face"
    _x0, _y0, bw, bh = [float(v) for v in box_sample[:4]]
    if bh <= 0:
        return "face"
    wide_aspect = float(cfg.get("framing_wide_aspect", 1.8))
    wide_frac = float(cfg.get("framing_wide_width_frac", 0.6))
    if bw / bh >= wide_aspect and bw / SAMPLE_W >= wide_frac:
        return "setup"
    return "face"


def resolve_split_variant(cfg=None, framing=None):
    """Variante del split para un clip de GTA 6.

    "auto" (default) -> "rdjavi" siempre (GTA 6 es el único juego
    del panel; RDJavi es el layout base por orden del usuario).
    "rdjavi" -> "rdjavi". "plain" -> split simple. "djmariio" está
    APARCADO (2026-11-18): se resuelve a "rdjavi" y se avisa, no se
    usa como layout. El encuadre ("framing") se registra en el
    manifiesto pero no cambia la variante mientras DJ Mariio esté
    aparcado.
    """
    cfg = cfg or {}
    variant = str(cfg.get("split_variant", "auto") or "auto").lower()
    if variant == "djmariio":
        print("Layout DJ Mariio aparcado por el usuario (2026-11-18): "
              "se usa RDJavi.")
        return "rdjavi"
    if variant == "plain":
        return "plain"
    return "rdjavi"


def _punch_crop_expr(normal, punched, punch):
    """Valor de recorte con punch-in opcional (zoom puntual).

    Sin punch devuelve el número tal cual. Con punch
    {"t0", "t1", "zoom"} devuelve una expresión ffmpeg
    if(between(t,t0,t1),punched,normal). Los tiempos son relativos
    al TRAMO (los timestamps se resetean por tramo al cortar).
    """
    if not punch:
        return str(int(normal))
    t0 = float(punch.get("t0", 0.0))
    t1 = float(punch.get("t1", t0))
    return f"if(between(t,{t0},{t1}),{int(punched)},{int(normal)})"


def _rounded_cam_alpha_expr(w, h, r):
    """Expresión geq para esquinas redondeadas en la webcam RDJavi.

    Alpha 0 en las esquinas fuera del círculo de radio r centrado
    en cada esquina interior; 255 en el resto.
    """
    return (
        f"if(lt(X,{r})*lt(Y,{r})*gt(hypot({r}-X,{r}-Y),{r}),0,"
        f"if(lt(X,{r})*gt(Y,{h - r - 1})*gt(hypot({r}-X,Y-{h - r - 1}),{r}),0,"
        f"if(gt(X,{w - r - 1})*lt(Y,{r})*gt(hypot(X-{w - r - 1},{r}-Y),{r}),0,"
        f"if(gt(X,{w - r - 1})*gt(Y,{h - r - 1})*"
        f"gt(hypot(X-{w - r - 1},Y-{h - r - 1}),{r}),0,255))))"
    )


# ---------------------------------------------------------------------------
# Mapa de zonas seguras por juego (Bloque 4)
# ---------------------------------------------------------------------------
# Cajas [x0, y0, x1, y1] en fracciones del fotograma (0..1). El PiP y
# los recortes no deben taparlas nunca. GTA V: minimapa abajo-izquierda
# y HUD de salud/arma abajo-derecha (convención Rockstar, verificada en
# los VODs de Coker/Locones: el minimapa vive en esa esquina). GTA 6:
# SIN mapa confirmado el día 1 — el fallback asume la misma convención
# (minimapa abajo-izquierda) y la completa con lo que detecte el
# escaneo (una región persistente en una esquina ES un elemento de HUD)
# y con el override manual ``safe_zones`` de config.yaml. El mapa
# definitivo de GTA 6 se fija después de los primeros clips reales.

SAFE_ZONES = {
    "GTA V": [
        {"name": "minimap", "box": [0.015, 0.70, 0.30, 0.985]},
        {"name": "hud-salud", "box": [0.70, 0.90, 0.985, 0.985]},
    ],
    # GTA 6: asumido (misma convención Rockstar), a confirmar con clips reales.
    "GTA 6": [
        {"name": "minimap?", "box": [0.015, 0.70, 0.30, 0.985]},
    ],
}


def get_safe_zones(game=None, cfg=None, detected=None):
    """Zonas seguras para un juego.

    Prioridad: override manual ``safe_zones`` en config (manda sobre
    todo) > mapa del juego + esquinas persistentes detectadas por el
    escaneo > mapa del juego solo. Devuelve [{name, box}] con box en
    fracciones 0..1.
    """
    cfg = cfg or {}
    manual = cfg.get("safe_zones")
    if manual:
        zones = []
        for i, z in enumerate(manual):
            if isinstance(z, dict):
                zones.append({"name": z.get("name", f"zona-{i}"),
                              "box": [float(v) for v in z["box"]]})
            else:
                zones.append({"name": f"zona-{i}",
                              "box": [float(v) for v in z]})
        return zones
    zones = [dict(z) for z in SAFE_ZONES.get(game or "GTA 6",
                                             SAFE_ZONES["GTA 6"])]
    for d in (detected or []):
        # Una esquina persistente detectada que no esté ya cubierta
        # por el mapa se añade como zona segura (es HUD, no gameplay).
        if not any(_rects_overlap(d["box"], z["box"], 0.5) for z in zones):
            zones.append({"name": d.get("name", "hud-detectado") + "?",
                          "box": list(d["box"])})
    return zones


def _rects_overlap(a, b, min_frac=0.0):
    """True si los rects a y b (fracciones) se solapan >= min_frac de a."""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return False
    inter = (x1 - x0) * (y1 - y0)
    area = (a[2] - a[0]) * (a[3] - a[1])
    return area > 0 and inter / area >= min_frac


def _pip_corner_avoiding_zones(corner, zones, pw_frac, ph_frac, margin_frac):
    """Esquina PiP que no tape ninguna zona segura (solo esquinas sup.).

    pw_frac/ph_frac: tamaño del PiP en fracciones del 1080x1920.
    Si la esquina pedida solapa una zona, prueba la otra superior.
    """
    for cand in (corner, "top-left" if corner == "top-right" else "top-right"):
        if cand == "top-right":
            box = [1 - margin_frac - pw_frac, margin_frac,
                   1 - margin_frac, margin_frac + ph_frac]
        else:
            box = [margin_frac, margin_frac,
                   margin_frac + pw_frac, margin_frac + ph_frac]
        if not any(_rects_overlap(box, z["box"], 0.0) for z in zones):
            return cand
    return corner


# ---------------------------------------------------------------------------
# Muestreo de fotogramas (gris, pequeño, stdlib)
# ---------------------------------------------------------------------------

def _gray_bytes(video_path, t, w=SAMPLE_W, h=SAMPLE_H):
    """Un fotograma en gris como bytes (w*h). None si falla."""
    cmd = ["ffmpeg", "-v", "error", "-ss", str(t), "-i", video_path,
           "-frames:v", "1", "-vf", f"scale={w}:{h},format=gray",
           "-f", "rawvideo", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out if len(out) >= w * h else None


def _grid_from_bytes(b, w=SAMPLE_W, h=SAMPLE_H):
    return [list(b[y * w:(y + 1) * w]) for y in range(h)]

def gray_frame(video_path, t, w=SAMPLE_W, h=SAMPLE_H):
    """Un fotograma en gris como lista de filas (0-255). None si falla."""
    out = _gray_bytes(video_path, t, w, h)
    if out is None:
        return None
    return _grid_from_bytes(out, w, h)


def _row_means(g):
    return [sum(r) / len(r) for r in g]


def _longest_run(means, thr=12):
    """La racha más larga de filas no negras: (y0, y1, n)."""
    best, cur, start, best_start = 0, 0, 0, 0
    for i, m in enumerate(means):
        if m > thr:
            if cur == 0:
                start = i
            cur += 1
            if cur > best:
                best, best_start = cur, start
        else:
            cur = 0
    return best_start, best_start + best - 1, best


def _col_gradients(g, y0, y1):
    """Gradiente horizontal medio por columna, dentro de la banda y0..y1."""
    w = len(g[0])
    grads = []
    for x in range(1, w):
        s = 0
        for y in range(y0, y1 + 1):
            s += abs(g[y][x] - g[y][x - 1])
        grads.append(s / (y1 - y0 + 1))
    return grads


def _region_stats(g, x0, y0, x1, y1):
    """(media, desviación típica) de un rectángulo."""
    vals = [g[y][x] for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    if not vals:
        return 0.0, 0.0
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return mean, var ** 0.5


# ---------------------------------------------------------------------------
# Clasificación de un fotograma
# ---------------------------------------------------------------------------

def classify_frame(g, cfg):
    """Devuelve (layout, params, reason) para un fotograma en gris.

    layout: 'stack' | 'pip' | 'center'.
    params: coordenadas en el fotograma de muestra (160x90).
    """
    h, w = len(g), len(g[0])
    y0, y1, band_h = _longest_run(_row_means(g))
    grads = _col_gradients(g, y0, y1)
    med = sorted(grads)[len(grads) // 2] if grads else 0.0
    seam_x = grads.index(max(grads)) + 1 if grads else w // 2
    seam_strength = max(grads) if grads else 0.0
    center_tol = cfg["seam_center_tol"] * w
    band_frac = band_h / h

    # 1. Dos webcams lado a lado: franja apilada + costura central fuerte.
    if (band_frac <= cfg["band_max_frac"]
            and abs(seam_x - w // 2) <= center_tol
            and seam_strength >= cfg["seam_min_strength"]):
        return ("stack",
                {"band": [0, y0, w, band_h], "split_x": seam_x},
                f"franja de contenido {band_h}/{h} del alto + costura "
                f"central en x={seam_x} (fuerza {seam_strength:.0f})")

    # 2. Una webcam + gameplay: caja con borde en una esquina superior.
    box = _detect_corner_box(g, grads, med, cfg)
    if box is not None:
        return ("pip", {"box": box},
                f"caja con borde en esquina superior {box}")

    # 3. Solo gameplay: recorte centrado normal.
    return ("center", {},
            f"sin franja apilada (banda {band_h}/{h}) ni caja de webcam; "
            f"costura máx. {seam_strength:.0f} en x={seam_x}")


def _edge_score(vals):
    """(media, continuidad) de una lista de gradientes de borde."""
    if not vals:
        return 0.0, 0.0
    mean = sum(vals) / len(vals)
    cont = sum(1 for v in vals if v >= 15) / len(vals)
    return mean, cont


def _detect_corner_box(g, grads, med, cfg):
    """Busca una webcam en una esquina SUPERIOR (abajo están minimapa/HUD).

    Devuelve [x0, y0, w, h, esquina] en el fotograma de muestra, o None.
    Heurística v1: para cada ancho candidato, BUSCA el borde real cerca
    de la posición esperada (el tamaño exacto de la caja no se conoce
    y un error de 2-3 px mata el gradiente al bajar a 160x90) y exige
    que LOS DOS lados interiores sean bordes fuertes y CONTINUOS (una
    webcam real tiene el borde completo; un tejado o un letrero del
    gameplay solo cruza parte del borde). Sin la continuidad, el
    gameplay nocturno de Locones daba un falso positivo en top-left.
    La caja NO es siempre 16:9: la de IlloJuan es casi cuadrada
    (~400x408 en 2560x1440), por eso se prueban varios aspectos
    (16:9, 1:1, 3:4, 4:3). Si no está claro, None (cae a centrado, seguro).
    """
    h, w = len(g), len(g[0])
    # Gradientes precalculados: vgrad[y][x] = |g[y][x]-g[y][x-1]|,
    # hgrad[y][x] = |g[y][x]-g[y-1][x]|.
    vgrad = [[0] * w for _ in range(h)]
    hgrad = [[0] * w for _ in range(h)]
    for y in range(h):
        for x in range(1, w):
            vgrad[y][x] = abs(g[y][x] - g[y][x - 1])
    for y in range(1, h):
        for x in range(w):
            hgrad[y][x] = abs(g[y][x] - g[y - 1][x])
    best = None
    for corner in ("top-left", "top-right"):
        for bw in range(int(0.15 * w), int(0.42 * w) + 1, 2):
            for (aw, ah) in ((16, 9), (1, 1), (3, 4), (4, 3)):
                bh = int(bw * ah / aw)
                if bh > int(h * 0.45) or bh < 8:
                    continue
                _found = _corner_box_edges(g, vgrad, hgrad, corner, bw, bh,
                                           med, cfg)
                if _found is None:
                    continue
                score, box = _found
                if best is None or score > best[0]:
                    best = (score, box, corner)
    if best is None:
        return None
    return best[1] + [best[2]]


def _corner_box_edges(g, vgrad, hgrad, corner, bw, bh, med, cfg):
    """Puntaje y caja de una webcam candidata (bw x bh) en una esquina.

    Devuelve (puntaje, [x0, y0, bw_real, bh_real]) o None. Separa la
    búsqueda de bordes del bucle de aspectos para no repetirla.
    """
    h, w = len(g), len(g[0])
    # Borde vertical interior: buscar el x con más gradiente
    # cerca del esperado (derecha de la caja en TL, izquierda en TR).
    exp_x = bw if corner == "top-left" else w - bw
    v_best = (0.0, 0.0, exp_x)
    for x in range(max(1, exp_x - 4), min(w - 1, exp_x + 4) + 1):
        vals = [vgrad[y][x] for y in range(0, bh)]
        sc, ct = _edge_score(vals)
        if sc > v_best[0]:
            v_best = (sc, ct, x)
    # Borde horizontal interior: buscar el y con más gradiente
    # cerca del borde inferior esperado.
    exp_y = bh - 1
    h_best = (0.0, 0.0, exp_y)
    for y in range(max(1, exp_y - 4), min(h - 1, exp_y + 4) + 1):
        xs = range(0, bw) if corner == "top-left" else range(w - bw, w)
        vals = [hgrad[y][x] for x in xs]
        sc, ct = _edge_score(vals)
        if sc > h_best[0]:
            h_best = (sc, ct, y)
    v_score, v_cont, edge_x = v_best
    h_score, h_cont, edge_y = h_best
    if corner == "top-left":
        x0, x1 = 0, edge_x
    else:
        x0, x1 = edge_x, w - 1
    y0, y1 = 0, edge_y
    bw_real, bh_real = x1 - x0 + 1, y1 - y0 + 1
    _, std = _region_stats(g, x0, y0, x1, y1)
    area_frac = (bw_real * bh_real) / (w * h)
    if (min(v_score, h_score) >= cfg["pip_border_min"]
            and min(v_score, h_score) >= cfg["pip_border_ratio"] * max(med, 1.0)
            and min(v_cont, h_cont) >= 0.75
            and std >= cfg["pip_min_std"]
            and 0.10 <= area_frac <= 0.35):
        return min(v_score, h_score), [x0, y0, bw_real, bh_real]
    return None


# ---------------------------------------------------------------------------
# Escaneo por tramo: regiones estables por diferencia de frames (Bloque 4)
# ---------------------------------------------------------------------------
# Una webcam es una región ESTABLE: el fondo de la habitación no cambia
# entre fotogramas separados 2 s, mientras el gameplay cambia en todas
# partes (cámara en movimiento, coches, HUD animado). Por eso la
# detección se hace sobre CELDAS ESTABLES y no sobre píxeles sueltos:
# una cara del juego se mueve con la cámara y sus celdas no son
# estables, así que no se confunde con una webcam. La rejilla es de
# celdas de 10x10 px sobre el fotograma de muestra (160x90 -> 16x9).

CELL = 10  # px por celda en el fotograma de muestra


def _cell_stability(prev_bytes, cur_bytes, cfg, w=SAMPLE_W, h=SAMPLE_H):
    """Rejilla de celdas estables entre dos fotogramas (True = estable).

    Celda estable si la diferencia media absoluta entre fotogramas
    es <= stable_diff_thr. Devuelve lista de filas de booleanos.
    """
    thr = cfg["stable_diff_thr"]
    rows, cols = h // CELL, w // CELL
    stable = []
    for r in range(rows):
        row = []
        for c in range(cols):
            s = 0
            n = 0
            for y in range(r * CELL, (r + 1) * CELL):
                base = y * w + c * CELL
                for x in range(CELL):
                    s += abs(cur_bytes[base + x] - prev_bytes[base + x])
                    n += 1
            row.append((s / n) <= thr)
        stable.append(row)
    return stable


def _half_stable_box(stable, cols_range, cfg):
    """Caja estable de una mitad: filas con >50% celdas estables.

    Devuelve [x0, y0, x1, y1] en px del fotograma de muestra, o None
    si la fracción de celdas estables es < stable_cell_frac.
    """
    rows = len(stable)
    c0, c1 = cols_range
    total = rows * (c1 - c0)
    n_stable = sum(1 for r in stable for c in range(c0, c1) if r[c])
    if total == 0 or n_stable / total < cfg["stable_cell_frac"]:
        return None, n_stable / total if total else 0.0
    good_rows = [r for r in range(rows)
                 if sum(1 for c in range(c0, c1) if stable[r][c])
                 / (c1 - c0) > 0.5]
    if not good_rows:
        return None, n_stable / total
    return ([c0 * CELL, min(good_rows) * CELL,
             c1 * CELL - 1, (max(good_rows) + 1) * CELL - 1],
            n_stable / total)


def _is_circular_avatar(stable, box):
    """True si la caja es un avatar CIRCULAR, no rectangular.

    En un avatar circular las esquinas de la caja dejan ver el
    gameplay (celdas inestables) mientras el centro es estable; en
    una webcam rectangular las esquinas también son estables (fondo
    de la habitación). Exige >= 3 esquinas inestables + centro estable.
    """
    if box is None:
        return False
    x0, y0, x1, y1 = box
    def _cell(x, y):
        return (min(max(y // CELL, 0), len(stable) - 1),
                min(max(x // CELL, 0), len(stable[0]) - 1))

    corners = [stable[_cell(x0 + 2, y0 + 2)[0]][_cell(x0 + 2, y0 + 2)[1]],
               stable[_cell(x1 - 2, y0 + 2)[0]][_cell(x1 - 2, y0 + 2)[1]],
               stable[_cell(x0 + 2, y1 - 2)[0]][_cell(x0 + 2, y1 - 2)[1]],
               stable[_cell(x1 - 2, y1 - 2)[0]][_cell(x1 - 2, y1 - 2)[1]]]
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    center = stable[_cell(cx, cy)[0]][_cell(cx, cy)[1]]
    return center and sum(1 for v in corners if not v) >= 3


def _classify_cams_from_stability(stable, cfg, frame_bytes=None):
    """Clasifica un instante por sus regiones estables.

    Devuelve (n_cams, avatar_circular, boxes, seam, detalle):
    n_cams 0/1/2, boxes {"left": [...], "right": [...]} en px de
    muestra (160x90), seam True si hay una costura vertical fuerte
    justo en el centro (el divisor entre dos cams). La costura es
    el discriminador contra imágenes ESTÁTICAS: un menú de pausa o
    una pantalla de carga también son estables en ambas mitades
    (Locones 0-7 s daba 67-72% estables), pero no tienen costura
    central; dos webcams lado a lado sí. Sin costura, 2 cams se
    reporta con la advertencia en el detalle y decide_layout NO lo
    usa para corregir a stack.
    """
    cols = len(stable[0])
    mid = cols // 2
    left, lf = _half_stable_box(stable, (0, mid), cfg)
    right, rf = _half_stable_box(stable, (mid, cols), cfg)
    boxes = {}
    if left is not None:
        boxes["left"] = left
    if right is not None:
        boxes["right"] = right
    n_cams = len(boxes)
    avatar = any(_is_circular_avatar(stable, b) for b in boxes.values())
    seam = False
    if n_cams == 2 and frame_bytes is not None:
        g = _grid_from_bytes(frame_bytes)
        y0, y1, _ = _longest_run(_row_means(g))
        grads = _col_gradients(g, y0, y1)
        seam_x = grads.index(max(grads)) + 1 if grads else cols * CELL // 2
        seam = (abs(seam_x - cols * CELL // 2) <= cfg["seam_center_tol"] * cols * CELL
                and max(grads) >= cfg["seam_min_strength"])
    detail = (f"celdas estables izq {lf:.0%} / der {rf:.0%}"
              + ("; avatar circular" if avatar else "")
              + ("; con costura central" if seam else
                 ("; SIN costura central (posible imagen estática)"
                  if n_cams == 2 else "")))
    return n_cams, avatar, boxes, seam, detail


# ---------------------------------------------------------------------------
# Detección de vtuber / PNG tuber (2026-11-17, VOD Rubius 2931565552)
# ---------------------------------------------------------------------------
# Un avatar reactivo (anime, mascota, PNG tuber) no es una cara humana
# (Haar no lo ve) y no tiene por qué tener borde de caja en una esquina
# superior (el de Rubius está abajo a la izquierda, sin borde). Lo que
# SÍ lo distingue de un fondo de pantalla o de una imagen estática es
# temporal: un parche pequeño de su cara cambia fuerte entre fotogramas
# (la boca se abre/cierra con la voz, parpadeo) mientras el resto del
# avatar NO se mueve (el sprite está quieto; solo cambia la cara) y el
# avatar persiste muchos segundos en la misma esquina.
#
# La heurística es genérica (nada de "gato Miku" ni coordenadas del
# VOD): trabaja sobre los fotogramas del escaneo (160x90, cada 2 s) y
# exige TODO esto para aceptar un candidato; si falta algo, no hay
# vtuber y el layout cae a center como antes:
#   1. Parche activo: celdas conexas (10x10 px) con diferencia media
#      >= vtuber_active_thr entre dos fotogramas consecutivos, de
#      vtuber_min_cells a vtuber_max_cells celdas (una cara, no todo
#      el gameplay moviéndose).
#   2. Cuerpo quieto + estados parecidos: en ALGÚN par con cambio
#      fuerte en el parche, el resto de la caja (parche + 1 celda de
#      margen) apenas cambia (dif. media <= vtuber_body_thr) Y los
#      dos estados del parche tienen brillo parecido (dif. de medias
#      <= vtuber_state_thr: boca abierta/cerrada). Esto descarta la
#      aparición / desaparición de un overlay: cuando un avatar
#      APARECE, su caja entera cambia y el brillo del parche salta.
#      También descarta una webcam humana moviéndose entera (esa ya
#      la cubre _face_cam_box).
#   3. Presente en racha: el parche se parece (media de gris cerca
#      de uno de los dos estados del mejor par con cambio: boca
#      abierta o cerrada) en >= vtuber_present_frac de las muestras
#      y en una racha consecutiva >= vtuber_min_run. Una alerta
#      ("Gracias <usuario>!" + avatar morado) aparece, se anima y
#      desaparece en 2-3 muestras: pasa 1 y 2 pero no la racha.
#   4. Esquina + tamaño + textura: la caja toca dos bordes
#      adyacentes, mide 3-35% del fotograma y tiene textura (no es
#      un parche plano de fondo liso).
# Sin dependencias nuevas: solo stdlib sobre los bytes en gris que
# ya muestrea el escaneo. Devuelve cajas en px del fotograma de
# muestra (160x90), listas para _split_params vía pip -> split.


def _cell_diff_grid(prev_bytes, cur_bytes, cfg, w=SAMPLE_W, h=SAMPLE_H):
    """Diferencia media absoluta por celda CELLxCELL entre dos fotogramas."""
    rows, cols = h // CELL, w // CELL
    grid = []
    for r in range(rows):
        row = []
        for c in range(cols):
            s = 0
            for y in range(r * CELL, (r + 1) * CELL):
                base = y * w + c * CELL
                for x in range(CELL):
                    d = cur_bytes[base + x] - prev_bytes[base + x]
                    s += d if d >= 0 else -d
            row.append(s / (CELL * CELL))
        grid.append(row)
    return grid


def _cell_mean(frame_bytes, c, r, w=SAMPLE_W):
    """Media de gris de una celda CELLxCELL (c, r)."""
    s = 0
    for y in range(r * CELL, (r + 1) * CELL):
        base = y * w + c * CELL
        for x in range(CELL):
            s += frame_bytes[base + x]
    return s / (CELL * CELL)


def _patch_mean(frame_bytes, cells):
    """Media de gris de un parche (lista de celdas (r, c))."""
    return sum(_cell_mean(frame_bytes, c, r) for (r, c) in cells) / len(cells)


def _active_clusters(max_grid, thr):
    """Parches conexos (4-vecinos) de celdas con dif. máxima >= thr."""
    rows, cols = len(max_grid), len(max_grid[0])
    seen = [[False] * cols for _ in range(rows)]
    clusters = []
    for r in range(rows):
        for c in range(cols):
            if max_grid[r][c] < thr or seen[r][c]:
                continue
            stack = [(r, c)]
            seen[r][c] = True
            cells = []
            while stack:
                rr, cc = stack.pop()
                cells.append((rr, cc))
                for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nr, nc = rr + dr, cc + dc
                    if (0 <= nr < rows and 0 <= nc < cols
                            and not seen[nr][nc]
                            and max_grid[nr][nc] >= thr):
                        seen[nr][nc] = True
                        stack.append((nr, nc))
            clusters.append(cells)
    return clusters


def _detect_vtuber_candidates(frames, cfg):
    """Candidatos a avatar reactivo en una lista de fotogramas.

    ``frames``: [(t, bytes160x90), ...] en orden temporal (los del
    escaneo). Devuelve [{box, corner, present_frac, run, detail,
    _cells, _states}] con box [x0, y0, w, h] en px de muestra;
    ``_cells``/``_states`` son internos (presencia por fotograma).
    Lista vacía si ningún parche cumple toda la heurística.
    """
    if len(frames) < 3:
        return []
    thr = float(cfg.get("vtuber_active_thr", 25))
    body_thr = float(cfg.get("vtuber_body_thr", 8))
    pair_grids = [_cell_diff_grid(a, b, cfg)
                  for (_t0, a), (_t1, b) in zip(frames, frames[1:])]
    rows, cols = len(pair_grids[0]), len(pair_grids[0][0])
    max_grid = [[max(g[r][c] for g in pair_grids) for c in range(cols)]
                for r in range(rows)]
    margin = int(cfg.get("vtuber_margin_cells", 1))
    candidates = []
    for cells in _active_clusters(max_grid, thr):
        if not (int(cfg.get("vtuber_min_cells", 2)) <= len(cells)
                <= int(cfg.get("vtuber_max_cells", 24))):
            continue
        rs = [r for (r, _c) in cells]
        cs = [c for (_r, c) in cells]
        # Caja: parche + margen, recortada al fotograma.
        r0 = max(0, min(rs) - margin)
        r1 = min(rows - 1, max(rs) + margin)
        c0 = max(0, min(cs) - margin)
        c1 = min(cols - 1, max(cs) + margin)
        box = [c0 * CELL, r0 * CELL,
               (c1 - c0 + 1) * CELL, (r1 - r0 + 1) * CELL]
        area = (box[2] * box[3]) / (SAMPLE_W * SAMPLE_H)
        if not (float(cfg.get("vtuber_min_area_frac", 0.03)) <= area
                <= float(cfg.get("vtuber_max_area_frac", 0.35))):
            continue
        # Esquina: la caja toca dos bordes adyacentes (margen 1 celda).
        touches = {"left": box[0] <= CELL,
                   "right": box[0] + box[2] >= SAMPLE_W - CELL,
                   "top": box[1] <= CELL,
                   "bottom": box[1] + box[3] >= SAMPLE_H - CELL}
        corner = None
        for name, (a, b) in (("top-left", ("top", "left")),
                             ("top-right", ("top", "right")),
                             ("bottom-left", ("bottom", "left")),
                             ("bottom-right", ("bottom", "right"))):
            if touches[a] and touches[b]:
                corner = name
                break
        if corner is None:
            continue
        # Cuerpo quieto: en un par con cambio fuerte en el parche, el
        # resto de la caja apenas cambia (avatar quieto, solo la cara
        # se mueve) Y los dos estados del parche tienen brillo
        # parecido (boca abierta/cerrada: 77<->83 en el gato de
        # Rubius). Sin ese par, es aparición/desaparición, no vtuber:
        # al aparecer, la caja entera cambia y el brillo del parche
        # salta (83->28 el gato, 9->73 la cámara humana).
        box_cells = [(r, c) for r in range(r0, r1 + 1)
                     for c in range(c0, c1 + 1)]
        cellset = set(cells)
        rest = [cell for cell in box_cells if cell not in cellset]
        best = None  # (dif. máx. del parche, (estado_a, estado_b))
        for gi, g in enumerate(pair_grids):
            mx = max(g[r][c] for (r, c) in cells)
            if mx < thr:
                continue
            if rest:
                rest_diff = sum(g[r][c] for (r, c) in rest) / len(rest)
                if rest_diff > body_thr:
                    continue
            ma = _patch_mean(frames[gi][1], cells)
            mb = _patch_mean(frames[gi + 1][1], cells)
            if abs(ma - mb) > float(cfg.get("vtuber_state_thr", 30)):
                continue
            if best is None or mx > best[0]:
                best = (mx, (ma, mb))
        if best is None:
            continue
        mx, states = best
        tol = float(cfg.get("vtuber_present_tol", 20))
        present = [min(abs(_patch_mean(b, cells) - s) for s in states) <= tol
                   for (_t, b) in frames]
        frac = sum(present) / len(present)
        if frac < float(cfg.get("vtuber_present_frac", 0.20)):
            continue
        run = cur = 0
        for p in present:
            cur = cur + 1 if p else 0
            run = max(run, cur)
        if run < int(cfg.get("vtuber_min_run", 4)):
            continue
        # Textura: la caja no es un parche plano de fondo liso.
        _t, fb = next((_t, b) for (_t, b), p in zip(frames, present) if p)
        _mean, std = _region_stats(
            _grid_from_bytes(fb), box[0], box[1],
            box[0] + box[2] - 1, box[1] + box[3] - 1)
        if std < float(cfg.get("vtuber_min_std", 10)):
            continue
        candidates.append({
            "box": box, "corner": corner,
            "cells": len(cells), "present_frac": round(frac, 2),
            "run": run, "max_diff": round(mx, 1),
            "detail": (f"vtuber en {corner}: parche activo de "
                       f"{len(cells)} celdas (dif. máx. {mx:.0f}), "
                       f"presente {frac:.0%} en racha de {run} muestras"),
            "_cells": cells, "_states": states})
    return candidates


def _vtuber_for_frame(frame_bytes, candidates, cfg):
    """El candidato presente en este fotograma, o None.

    Presente = la media del parche está cerca de uno de sus dos
    estados (boca abierta / cerrada). Si hay varios presentes (p.
    ej. avatar + alerta), manda el de mayor present_frac: la
    cámara real persiste más que un overlay transitorio.
    """
    if not candidates:
        return None
    tol = float(cfg.get("vtuber_present_tol", 20))
    best = None
    for cand in candidates:
        m = _patch_mean(frame_bytes, cand["_cells"])
        if min(abs(m - s) for s in cand["_states"]) <= tol:
            if best is None or cand["present_frac"] > best["present_frac"]:
                best = cand
    return best


_HAAR_CLF = None  # None = aún no intentado; False = no disponible


def _haar_classifier():
    """CascadeClassifier Haar, cargado una sola vez (o False si no hay).

    cv2 5.0 NO tiene CascadeClassifier ni trae el XML de Haar (es el
    que traía el .venv); funciona con opencv-python-headless 4.11.
    """
    global _HAAR_CLF
    if _HAAR_CLF is None:
        try:
            import cv2
            clf = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            _HAAR_CLF = False if clf.empty() else clf
        except (ImportError, AttributeError):
            _HAAR_CLF = False
    return _HAAR_CLF


def _detect_faces_opencv(gray_bytes, stable_boxes=None, w=SAMPLE_W,
                         h=SAMPLE_H, min_neighbors=4, min_size=(12, 12)):
    """Rostros por OpenCV (Haar) en un fotograma en gris de w x h.

    Devuelve la lista de rostros [x, y, w, h] en px del fotograma de
    muestreo, o None si no hay OpenCV utilizable en este entorno.
    Si se pasa ``stable_boxes``, solo acepta los rostros que caen
    DENTRO de una región estable (las caras del juego quedan fuera
    por definición: su región no es estable); sin ese filtro (el
    seguimiento de caras, Arreglo 6) devuelve todos: una cara que se
    mueve no vive en una región estable.
    """
    clf = _haar_classifier()
    if clf is False:
        return None
    try:
        import numpy as np
    except ImportError:
        return None
    img = np.frombuffer(gray_bytes, dtype=np.uint8).reshape(h, w)
    faces = clf.detectMultiScale(img, scaleFactor=1.1,
                                 minNeighbors=min_neighbors,
                                 minSize=min_size)
    if len(faces) == 0:
        return []
    all_faces = [[int(x), int(y), int(fw), int(fh)]
                 for (x, y, fw, fh) in faces]
    if not stable_boxes:
        return all_faces
    kept = []
    for (x, y, fw, fh) in all_faces:
        cx, cy = x + fw // 2, y + fh // 2
        if any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3]
               for b in stable_boxes):
            kept.append([x, y, fw, fh])
    return kept


def _detect_faces_at(video_path, t, src_w, src_h, cfg):
    """Rostros en un instante, en px del vídeo original.

    Muestrea en gris a track_w x track_h (640x360 por defecto: a
    160x90 Haar no detecta ninguna cara en este material) y devuelve
    [[x, y, w, h], ...] en px originales, [] si no hay caras, o None
    si no hay detector OpenCV disponible.
    """
    tw, th = int(cfg["track_w"]), int(cfg["track_h"])
    b = _gray_bytes(video_path, t, tw, th)
    if b is None:
        return []
    faces = _detect_faces_opencv(
        b, None, w=tw, h=th,
        min_neighbors=int(cfg["track_min_neighbors"]),
        min_size=(int(cfg["track_min_face"]), int(cfg["track_min_face"])))
    if faces is None:
        return None
    sx, sy = src_w / tw, src_h / th
    return [[round(x * sx), round(y * sy), round(fw * sx), round(fh * sy)]
            for (x, y, fw, fh) in faces]


def extract_gray_frames(video_path, duration, w=SAMPLE_W, h=SAMPLE_H):
    """Todos los fotogramas en gris a 1 fps, en UNA pasada de ffmpeg.

    Devuelve [bytes(w*h), ...] con un elemento por segundo
    (t=0..duration-1). Lista vacía si ffmpeg falla o duration es 0.
    Antes, cada segundo costaba un proceso ffmpeg propio
    (_gray_bytes por segundo); ahora es un solo proceso.
    """
    n = max(0, int(duration))
    if n == 0:
        return []
    cmd = ["ffmpeg", "-v", "error", "-i", video_path,
           "-vf", f"fps=1,scale={w}:{h},format=gray",
           "-frames:v", str(n), "-f", "rawvideo", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    size = w * h
    return [out[i * size:(i + 1) * size] for i in range(n)
            if len(out[i * size:(i + 1) * size]) == size]


def detect_faces_1fps(video_path, clip_duration, cfg):
    """Caras Haar a 1 fps con UNA sola pasada de ffmpeg.

    Devuelve [{"t": t, "faces": n}, ...] (un elemento por segundo)
    o None si no hay detector OpenCV utilizable. Equivale a llamar
    a _detect_faces_at una vez por segundo, pero con un solo
    proceso ffmpeg en vez de uno por segundo (viral_score.py
    llamaba a _detect_faces_at en bucle: un clip de 30 s eran
    30 procesos).
    """
    tw, th = int(cfg["track_w"]), int(cfg["track_h"])
    frames = extract_gray_frames(video_path, clip_duration, tw, th)
    n = max(0, int(clip_duration))
    if not frames:
        return [{"t": t, "faces": 0} for t in range(n)]
    faces = []
    for t, b in enumerate(frames):
        found = _detect_faces_opencv(
            b, None, w=tw, h=th,
            min_neighbors=int(cfg["track_min_neighbors"]),
            min_size=(int(cfg["track_min_face"]),
                      int(cfg["track_min_face"])))
        if found is None:
            return None
        faces.append({"t": t, "faces": len(found)})
    return faces


def _median(vals):
    s = sorted(vals)
    return s[len(s) // 2]


def _collapse_knots(knots):
    """Quita knots intermedios con el mismo valor que el anterior.

    Un tramo quieto solo necesita sus dos extremos; la expresión
    ffmpeg resultante es mucho más corta.
    """
    if len(knots) <= 2:
        return list(knots)
    out = [knots[0]]
    for k in knots[1:-1]:
        if k[1] != out[-1][1]:
            out.append(k)
    out.append(knots[-1])
    return out


def _smooth_centers(samples, crop_w, src_w, cfg):
    """Suaviza [(t, objetivo_x_o_None)] -> knots [(t, centro_x)].

    Trípode pesado (idea de SmoothedCameraman de OpenShorts): zona
    muerta alrededor del centro actual (no moverse si el objetivo
    está dentro), velocidad máxima en px/s hacia el objetivo, sin
    pasarse de él, y clamp al rango válido del recorte. Sin objetivo
    (sin cara en esa muestra) se mantiene el último centro.
    """
    if not samples:
        return []
    lo, hi = crop_w // 2, src_w - crop_w // 2
    if lo > hi:  # recorte más ancho que el vídeo: centrado fijo
        return [(samples[0][0], src_w // 2), (samples[-1][0], src_w // 2)]
    dead = cfg["track_dead_zone_frac"] * crop_w
    max_speed = cfg["track_max_speed_frac"] * src_w
    cur = None
    knots = []
    prev_t = None
    for t, target in samples:
        if cur is None:
            cur = target if target is not None else src_w // 2
            cur = min(max(cur, lo), hi)
            knots.append((float(t), int(round(cur))))
            prev_t = t
            continue
        dt = max(0.0, t - prev_t) if prev_t is not None else 0.0
        prev_t = t
        if target is not None and abs(target - cur) > dead:
            step = max_speed * dt
            diff = target - cur
            move = min(abs(diff), step) * (1 if diff > 0 else -1)
            cur += move
            if (diff > 0 and cur > target) or (diff < 0 and cur < target):
                cur = target
            cur = min(max(cur, lo), hi)
        knots.append((float(t), int(round(cur))))
    return _collapse_knots(knots)


def _smooth_split(samples, src_w, cfg):
    """Suaviza [(t, split_x_o_None)] -> knots [(t, split_x)].

    El divisor es una característica fija de la pantalla, así que el
    suavizado es sobre todo robustez: mediana de 3 muestras (quita
    lecturas aisladas como el 68 de Coker, que era un borde dentro
    de la cam) + límite de velocidad muy bajo + clamp cerca del
    centro. Sin lectura en una muestra, se mantiene el último.
    """
    if not samples:
        return []
    lo, hi = int(0.35 * src_w), int(0.65 * src_w)
    max_speed = cfg["track_split_max_speed_frac"] * src_w
    raw = [v for (_t, v) in samples]
    med = []
    for i, v in enumerate(raw):
        window = [raw[j] for j in (i - 1, i, i + 1)
                  if 0 <= j < len(raw) and raw[j] is not None]
        med.append(sorted(window)[len(window) // 2] if window else None)
    cur = None
    knots = []
    prev_t = None
    for (t, _v), m in zip(samples, med):
        if cur is None:
            cur = m if m is not None else src_w // 2
            cur = min(max(cur, lo), hi)
            knots.append((float(t), int(round(cur))))
            prev_t = t
            continue
        dt = max(0.0, t - prev_t) if prev_t is not None else 0.0
        prev_t = t
        if m is not None:
            step = max_speed * dt
            diff = m - cur
            if diff:
                move = min(abs(diff), step) * (1 if diff > 0 else -1)
                cur += move
                if (diff > 0 and cur > m) or (diff < 0 and cur < m):
                    cur = m
                cur = min(max(cur, lo), hi)
        knots.append((float(t), int(round(cur))))
    return _collapse_knots(knots)


def _track_face_centers(video_path, duration, start_offset, src_w, src_h,
                        cfg):
    """Sigue la cara protagonista de un tramo 'center'.

    Muestrea cada track_every_s, detecta rostros con Haar y asocia
    por centro más cercano con querencia por la cara seguida (si la
    cara seguida sigue ahí, no se cambia aunque otra sea mayor; idea
    de SpeakerTracker de OpenShorts). Devuelve {"kind": "center",
    "knots": [[t, cx], ...]} con t relativo al tramo y cx en px del
    vídeo original, o None si no hay detector o no se detectó ninguna
    cara en todo el tramo (entonces el recorte queda fijo, seguro).
    """
    step = max(1, int(cfg["track_every_s"]))
    times = list(range(0, int(duration) + 1, step))
    if not times or times[-1] < duration:
        times.append(float(duration))
    crop_w = int(src_h * 9 / 16)
    match_r = cfg["track_match_frac"] * src_w
    samples = []  # (t, target_cx_or_None)
    cur_cx = None
    found_any = False
    for t in times:
        faces = _detect_faces_at(video_path, start_offset + t,
                                 src_w, src_h, cfg)
        if faces is None:
            return None  # sin detector OpenCV: recorte fijo
        target = None
        if faces:
            found_any = True
            centers = [(x + fw // 2, fw * fh) for (x, _y, fw, fh) in faces]
            if cur_cx is not None:
                near = [(abs(cx - cur_cx), cx) for (cx, _a) in centers
                        if abs(cx - cur_cx) <= match_r]
                if near:
                    target = min(near)[1]
            if target is None:
                # Sin cara seguida cerca: la mayor, y si empatan la
                # más cercana al centro actual del recorte.
                cur_center = cur_cx if cur_cx is not None else src_w // 2
                target = max(centers,
                             key=lambda ca: (ca[1], -abs(ca[0] - cur_center)))[0]
            cur_cx = target
        samples.append((float(t), target))
    if not found_any:
        return None
    knots = _smooth_centers(samples, crop_w, src_w, cfg)
    return {"kind": "center", "knots": [[t, x] for (t, x) in knots]}


def _split_knots_from_samples(samples, scan_samples, src_w, cfg):
    """Knots del divisor para un tramo 'stack'.

    Por muestra: el borde entre las dos cajas estables del escaneo
    (el divisor real de las dos cams) si hay dos cajas; si no, el
    split_x de classify_frame. Luego mediana + límite de velocidad.
    Los tiempos son relativos al clip; el llamador los pasa a
    relativos al tramo.
    """
    by_t = {}
    if scan_samples:
        for s in scan_samples:
            boxes = s.get("boxes") or {}
            if "left" in boxes and "right" in boxes:
                by_t[s["t"]] = (boxes["left"][2] + boxes["right"][0]) / 2
    raw = []
    for s in samples:
        split = by_t.get(s["t"])
        if split is None:
            p = s.get("params") or {}
            split = p.get("split_x")
        raw.append((float(s["t"]),
                    (float(split) * src_w / SAMPLE_W)
                    if split is not None else None))
    return _smooth_split(raw, src_w, cfg)


def _track_expr(knots):
    """Expresión ffmpeg: interpolación lineal entre knots [(t, x)].

    x(t) vale el primer knot antes de t0 y el último después del
    último knot. Con un solo knot, constante. Números enteros para
    que la expresión sea corta. Se usa dentro de comillas simples
    en el filtro crop (las comas de los if() van entrecomilladas).
    """
    if not knots:
        return None
    if len(knots) == 1:
        return str(int(round(knots[0][1])))
    expr = str(int(round(knots[-1][1])))
    for (t0, x0), (t1, x1) in reversed(list(zip(knots, knots[1:]))):
        dt = t1 - t0
        if dt <= 0:
            continue
        x0i, x1i = int(round(x0)), int(round(x1))
        expr = (f"if(lt(t,{t1:g}),"
                f"{x0i}+({x1i}-{x0i})*(t-{t0:g})/{dt:g},{expr})")
    expr = f"if(lt(t,{knots[0][0]:g}),{int(round(knots[0][1]))},{expr})"
    return expr


def _interp_knots(knots, t):
    """Valor interpolado de knots [(t, x)] en el instante t."""
    if not knots:
        return None
    if t <= knots[0][0]:
        return knots[0][1]
    if t >= knots[-1][0]:
        return knots[-1][1]
    for (t0, x0), (t1, x1) in zip(knots, knots[1:]):
        if t0 <= t <= t1:
            if t1 == t0:
                return x1
            return x0 + (x1 - x0) * (t - t0) / (t1 - t0)
    return knots[-1][1]


def _face_cam_box(video_path, duration, start_offset, src_w, src_h, cfg):
    """Caja de la webcam a partir de la CARA del streamer.

    La detección por bordes de esquina falla cuando la caja no toca
    el borde superior (la de IlloJuan está a ~29% del alto: x 60-460,
    y 412-820 en 2560x1440) y el escaneo por regiones estables falla
    cuando el streamer se mueve mucho hablando. Pero la cara SÍ se
    detecta siempre con Haar en la misma zona: se muestrea la cara
    en 8 instantes, se exige que esté en la misma región en >= 3
    (una cara del juego se mueve con la cámara y no se queda quieta)
    y se refinan los 4 bordes de la caja buscando el gradiente más
    fuerte alrededor de la cara. Si un borde no se encuentra, se
    usa la expansión calibrada (caja ~2,8x la cara, IlloJuan:
    cara 140x140 -> caja 400x408).

    8 instantes (no 5): en p7775 (Rubius, webcam abajo-izquierda
    561:365:0:626) Haar solo ve la cara en 2 de los 5 primeros
    instantes (17, 22 s) y _face_cam_box devolvía None — el clip
    caía a "center" y el camino automático nunca usaba el split
    v2. Con 8 instantes la ve en 17, 22 y 32 s (la cara del juego
    en 27 s la descarta el filtro de mediana) y la caja sale.

    Devuelve [x0, y0, w, h] en px del vídeo original, o None si no
    hay detector OpenCV o no hay cara estable.
    """
    times = [t for t in (2, 7, 12, 17, 22, 27, 32, 37) if t < duration]
    if len(times) < 3:
        # Clip corto: 5 instantes repartidos por la duración, para
        # tener >= 3 muestras de cara aunque dure pocos segundos.
        times = sorted({round(duration * f, 2)
                        for f in (0.1, 0.3, 0.5, 0.7, 0.9)})
    if not times:
        times = [min(2, duration / 2)]
    faces = []
    for t in times:
        found = _detect_faces_at(video_path, start_offset + t,
                                 src_w, src_h, cfg)
        if found:
            faces.append((float(t), found))
    if not faces:
        return None
    # Cara seguida: la mayor de cada muestra; centro mediano.
    picks = []
    for (t, fl) in faces:
        biggest = max(fl, key=lambda b: b[2] * b[3])
        picks.append((t, biggest))
    cxs = sorted(x + w // 2 for (_t, (x, _y, w, _h)) in picks)
    cys = sorted(y + h // 2 for (_t, (_x, y, _w, h)) in picks)
    mcx, mcy = cxs[len(cxs) // 2], cys[len(cys) // 2]
    kept = [(t, b) for (t, b) in picks
            if abs(b[0] + b[2] // 2 - mcx) <= 0.25 * src_w
            and abs(b[1] + b[3] // 2 - mcy) <= 0.25 * src_h]
    if len(kept) < 3:
        return None
    t_med, (fx, fy, fw, fh) = kept[len(kept) // 2]
    # Refinado de bordes en gris a 640x360.
    tw, th = 640, 360
    b = _gray_bytes(video_path, start_offset + t_med, tw, th)
    if b is None:
        return _face_box_expanded(fx, fy, fw, fh, src_w, src_h)
    g = [list(b[y * tw:(y + 1) * tw]) for y in range(th)]
    sx, sy = tw / src_w, th / src_h
    fx0, fy0 = fx * sx, fy * sy
    fx1, fy1 = (fx + fw) * sx, (fy + fh) * sy
    fwc, fhc = fx1 - fx0, fy1 - fy0

    def _v_edge(x_lo, x_hi, y_lo, y_hi):
        best, best_x = 0.0, None
        for x in range(max(1, int(x_lo)), min(tw - 1, int(x_hi)) + 1):
            s = sum(abs(g[y][x] - g[y][x - 1])
                    for y in range(max(0, int(y_lo)), min(th, int(y_hi))))
            if s > best:
                best, best_x = s, x
        return best_x, best

    def _h_edge(y_lo, y_hi, x_lo, x_hi):
        best, best_y = 0.0, None
        for y in range(max(1, int(y_lo)), min(th - 1, int(y_hi)) + 1):
            s = sum(abs(g[y][x] - g[y - 1][x])
                    for x in range(max(0, int(x_lo)), min(tw, int(x_hi))))
            if s > best:
                best, best_y = s, y
        return best_y, best

    # Umbral relativo: el borde debe ser >= 2x el gradiente mediano
    # de su banda de búsqueda; si no, expansión.
    pad_x, pad_y = 1.5 * fwc, 1.5 * fhc
    lx, ls = _v_edge(fx0 - pad_x, fx0, fy0 - 10, fy1 + 10)
    rx, rs = _v_edge(fx1, fx1 + pad_x, fy0 - 10, fy1 + 10)
    ty, ts = _h_edge(fy0 - pad_y, fy0, fx0 - 10, fx1 + 10)
    by, bs = _h_edge(fy1, fy1 + pad_y, fx0 - 10, fx1 + 10)
    med_v = sorted(abs(g[y][x] - g[y][x - 1])
                   for y in range(int(fy0), int(fy1))
                   for x in range(1, tw)) or [0]
    med_v = med_v[len(med_v) // 2] * max(1, int(fy1 - fy0))
    x0 = (lx / sx) if (lx is not None and ls >= 2 * med_v) else None
    x1 = (rx / sx) if (rx is not None and rs >= 2 * med_v) else None
    y0 = (ty / sy) if (ty is not None and ts >= 2 * med_v) else None
    y1 = (by / sy) if (by is not None and bs >= 2 * med_v) else None
    if x0 is None or x1 is None or y0 is None or y1 is None:
        return _face_box_expanded(fx, fy, fw, fh, src_w, src_h)
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(src_w, int(x1)), min(src_h, int(y1))
    if x1 - x0 < fw or y1 - y0 < fh:
        return _face_box_expanded(fx, fy, fw, fh, src_w, src_h)
    # La caja refinada no puede ser apenas mayor que la cara: los
    # bordes encontrados son entonces interiores (silueta, pelo,
    # fondo de la habitación) y no los de la webcam. p7775: la
    # refinada era 172x208 desde una cara de 112x112 (1,5x) y el
    # cover crop del split dejaba la cara cortada por los bordes
    # en el punch-in; la webcam real es 561x365. La expansión
    # 2,8x (cara + hombros) encuadra mejor. IlloJuan no se toca:
    # su refinada es 400x408 desde cara 140 (2,9x).
    if x1 - x0 < 2.0 * fw or y1 - y0 < 2.0 * fh:
        return _face_box_expanded(fx, fy, fw, fh, src_w, src_h)
    return [x0, y0, x1 - x0, y1 - y0]


def _face_box_expanded(fx, fy, fw, fh, src_w, src_h, factor=2.8):
    """Caja de webcam = cara expandida ~2,8x alrededor de su centro."""
    cx, cy = fx + fw // 2, fy + fh // 2
    w, h = int(fw * factor), int(fh * factor)
    x0 = max(0, cx - w // 2)
    y0 = max(0, cy - h // 2)
    x1 = min(src_w, x0 + w)
    y1 = min(src_h, y0 + h)
    return [x0, y0, x1 - x0, y1 - y0]


def _split_params(box_sample, cfg):
    """Params del layout split a partir de la caja de la webcam.

    box_sample: [x0, y0, w, h] en el fotograma de muestra (160x90).
    Devuelve params con la caja, la ventana de juego (game_x0, en
    muestra) y las alturas de los paneles. El streamer va ARRIBA
    y el juego ABAJO; la variante por defecto para GTA 6 es RDJavi
    (plantilla v2, 2026-11-18: webcam a ancho completo 0-702,
    franja de título 702-800, gameplay 800-1740, barra de
    subtítulos 1740-1920). Si la caja de la cámara es muy ancha
    (> 45% del frame, el juego no se vería) la cámara se achica
    a 480 px y el juego crece a 1440 px (split simple). El
    encuadre ("framing": face/setup) se detecta y se guarda en
    params; la variante se resuelve con resolve_split_variant
    (DJ Mariio aparcado: siempre RDJavi para GTA 6).
    (Orden invertido 2026-11-18 por el usuario: antes juego arriba
    y cámara abajo; referencia RDJavi Df4sy4bxlmr.)

    ``punch_in`` / ``game_zoom``: ventanas de zoom de la v2
    (punch-in de la webcam en la reacción, zoom suave del gameplay
    en el payoff). Si no vienen en cfg, el grafo usa los defaults
    v2 (punch 16-26 s zoom 1,30; game zoom 18-24 s 1,15x: el pico
    va en el segundo 20 del clip por construcción). ``False`` los
    desactiva; un dict {"t0","t1","zoom"} (punch) o {"t0","t1"}
    (game zoom) los fija.
    """
    game_h = int(cfg.get("split_game_h", 1320))
    cam_h = int(cfg.get("split_cam_h", 600))
    bx0, _by0, bw, _bh = box_sample[:4]
    bx1 = bx0 + bw
    if bw / SAMPLE_W > float(cfg.get("split_cam_max_w_frac", 0.45)):
        cam_h = int(cfg.get("split_cam_small_h", 480))
        game_h = OUT_H - cam_h
    gw_s = SAMPLE_H * OUT_W / game_h  # ancho de la ventana de juego, en muestra
    cam_cx = bx0 + bw / 2
    if cam_cx < SAMPLE_W / 2:  # cámara a la izquierda: juego a su derecha
        game_x0 = min(bx1 + 2, SAMPLE_W - gw_s)
    else:  # cámara a la derecha: juego a su izquierda
        game_x0 = max(0.0, bx0 - gw_s - 2)
    game_x0 = max(0.0, min(float(game_x0), SAMPLE_W - gw_s))
    framing = detect_framing(box_sample, cfg)
    punch = cfg.get("punch_in")
    punch = punch if (isinstance(punch, dict) or punch is False) else None
    zoom = cfg.get("game_zoom")
    zoom = zoom if (isinstance(zoom, (dict, tuple, list))
                   or zoom is False) else None
    return {"box": [float(v) for v in box_sample[:4]],
            "game_x0": float(game_x0), "game_h": game_h, "cam_h": cam_h,
            "framing": framing,
            "split_variant": resolve_split_variant(cfg, framing),
            "punch_in": punch, "game_zoom": zoom}


def _scan_frames(video_path, duration, start_offset, cfg):
    """Muestrea fotogramas cada scan_every_s y los clasifica por tramo.

    Devuelve (samples, persistent_corners): samples con t, cams,
    avatar, boxes y el fotograma en _frame (bytes, uso interno);
    persistent_corners es la lista de esquinas con HUD persistente
    detectado, en fracciones 0..1, para el mapa de zonas seguras.
    """
    step = max(1, int(cfg["scan_every_s"]))
    times = list(range(2, int(duration), step))
    if not times:
        times = [2]
    frames = []
    for t in times:
        b = _gray_bytes(video_path, start_offset + t)
        if b is not None:
            frames.append((float(t), b))
    if not frames:
        return [], []
    # Vtuber / PNG tuber: candidatos globales del clip (parche activo
    # + cuerpo quieto + racha larga); cada muestra anota el que está
    # presente en SU fotograma, si hay alguno.
    vtuber_candidates = []
    if cfg.get("vtuber_enabled", True):
        try:
            vtuber_candidates = _detect_vtuber_candidates(frames, cfg)
        except Exception:  # noqa: BLE001 - sin vtuber, el resto sigue igual
            vtuber_candidates = []
    samples = []
    corner_hits = {"top-left": 0, "top-right": 0,
                   "bottom-left": 0, "bottom-right": 0}
    n_diffs = 0
    for i, (t, b) in enumerate(frames):
        # Comparar con el fotograma anterior (o el siguiente si es el 1º).
        if i > 0:
            prev = frames[i - 1][1]
        elif len(frames) > 1:
            prev = frames[1][1]
        else:
            prev = None
        if prev is None:
            vt = _vtuber_for_frame(b, vtuber_candidates, cfg)
            samples.append({"t": t, "cams": None, "avatar": False,
                            "boxes": {}, "seam": False,
                            "stable_detail": "único fotograma",
                            "vtuber": ({"box": vt["box"],
                                        "corner": vt["corner"],
                                        "present_frac": vt["present_frac"],
                                        "detail": vt["detail"]}
                                       if vt else None),
                            "_frame": b, "_stable": None})
            continue
        stable = _cell_stability(prev, b, cfg)
        n_cams, avatar, boxes, seam, detail = _classify_cams_from_stability(
            stable, cfg, frame_bytes=b)
        # Refuerzo opcional con rostros (solo si hay cv2): un rostro
        # dentro de una región estable confirma la cam; fuera, se ignora.
        faces = _detect_faces_opencv(b, list(boxes.values()))
        if faces:
            detail += f"; rostros en región estable: {len(faces)}"
        elif faces is not None:
            detail += "; sin rostros en región estable (cv2)"
        vt = _vtuber_for_frame(b, vtuber_candidates, cfg)
        if vt is not None:
            detail += f"; {vt['detail']}"
        samples.append({"t": t, "cams": n_cams, "avatar": avatar,
                        "boxes": boxes, "seam": seam,
                        "stable_detail": detail,
                        "vtuber": ({"box": vt["box"],
                                    "corner": vt["corner"],
                                    "present_frac": vt["present_frac"],
                                    "detail": vt["detail"]}
                                   if vt else None),
                        "_frame": b, "_stable": stable})
        # Esquinas persistentes: solo cuentan los diffs en los que NO
        # hay 2 cams llenando el fotograma (si las cams lo llenan todo,
        # TODAS las esquinas son estables y no son HUD: son las cams).
        # Un HUD es persistente mientras el gameplay cambia alrededor.
        if n_cams < 2:
            n_diffs += 1
            rows, cols = len(stable), len(stable[0])
            corners = {
                "top-left": (range(0, 3), range(0, 4)),
                "top-right": (range(0, 3), range(cols - 4, cols)),
                "bottom-left": (range(rows - 3, rows), range(0, 4)),
                "bottom-right": (range(rows - 3, rows), range(cols - 4, cols)),
            }
            for name, (rs, cs) in corners.items():
                cells = [stable[r][c] for r in rs for c in cs]
                if sum(cells) / len(cells) >= cfg["stable_corner_frac"]:
                    corner_hits[name] += 1
    # Una esquina es HUD persistente si fue estable en >= 80% de los diffs.
    persistent = []
    if n_diffs:
        frac_boxes = {
            "top-left": [0.0, 0.0, 0.25, 0.33],
            "top-right": [0.75, 0.0, 1.0, 0.33],
            "bottom-left": [0.0, 0.67, 0.25, 1.0],
            "bottom-right": [0.75, 0.67, 1.0, 1.0],
        }
        for name, hits in corner_hits.items():
            if hits / n_diffs >= 0.8:
                persistent.append({"name": f"hud-{name}",
                                   "box": frac_boxes[name]})
    return samples, persistent


def _scan_band(boxes):
    """Banda [0, y0, w, h] + split de dos cajas estables (px de muestra)."""
    if "left" not in boxes or "right" not in boxes:
        return None
    y0 = min(boxes["left"][1], boxes["right"][1])
    y1 = max(boxes["left"][3], boxes["right"][3])
    return [0, y0, SAMPLE_W, y1 - y0 + 1]


def _scan_runs(known, step, duration, min_seg):
    """Agrupa muestras de escaneo en tramos por nº de cams."""
    runs = []
    for s in known:
        span_start = max(0.0, s["t"] - step / 2)
        span_end = min(float(duration), s["t"] + step / 2)
        if runs and runs[-1]["cams"] == s["cams"]:
            runs[-1]["end"] = span_end
            runs[-1]["boxes"] = s["boxes"]
            if runs[-1].get("vtuber") is None:
                runs[-1]["vtuber"] = s.get("vtuber")
        else:
            runs.append({"cams": s["cams"], "start": span_start,
                         "end": span_end, "avatar": s["avatar"],
                         "boxes": s["boxes"],
                         "vtuber": s.get("vtuber"),
                         "reason": s["stable_detail"]})
    if not runs:
        return runs
    runs[0]["start"] = 0.0
    runs[-1]["end"] = float(duration)
    merged = _absorb_short_runs(runs, float(min_seg))
    merged[0]["start"] = 0.0
    for i in range(1, len(merged)):
        merged[i]["start"] = merged[i - 1]["end"]
    merged[-1]["end"] = float(duration)
    for r in merged:
        r["start"] = float(r["start"])
        r["end"] = float(r["end"])
    return merged


def scan_segments(video_path, duration, start_offset=0.0, cfg=None):
    """Escaneo por tramo: 0/1/2 cams (y avatar circular) cada 2 s.

    Devuelve {"segments": [{start, end, cams, avatar, boxes, vtuber,
    reason}], "samples": [{t, cams, avatar, boxes, vtuber,
    stable_detail}], "safe_zones_detected": [{name, box}]}. Los tiempos
    son relativos al clip. Un tramo más corto que min_segment_s se
    fusiona con el vecino (misma regla que decide_layout). ``vtuber``
    es la caja del avatar reactivo detectado en el tramo/muestra
    (o None): ver _detect_vtuber_candidates.
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    samples, persistent = _scan_frames(video_path, duration, start_offset, cfg)
    known = [s for s in samples if s["cams"] is not None]
    if not known:
        return {"segments": [{"start": 0.0, "end": float(duration),
                              "cams": None, "avatar": False, "boxes": {},
                              "reason": "no se pudo muestrear ningún fotograma"}],
                "samples": [], "safe_zones_detected": persistent}
    # Suavizado: muestra aislada que difiere de sus dos vecinas
    # (que coinciden) se reasigna (igual que en decide_layout).
    for i in range(1, len(known) - 1):
        if (known[i]["cams"] != known[i - 1]["cams"]
                and known[i]["cams"] != known[i + 1]["cams"]
                and known[i - 1]["cams"] == known[i + 1]["cams"]):
            known[i]["cams"] = known[i - 1]["cams"]
            known[i]["boxes"] = known[i - 1]["boxes"]
            known[i]["stable_detail"] += " (muestra aislada reclasificada)"
    step = max(1, int(cfg["scan_every_s"]))
    merged = _scan_runs(known, step, duration, float(cfg["min_segment_s"]))
    clean = [{k: v for k, v in s.items() if not k.startswith("_")}
             for s in known]
    return {"segments": merged, "samples": clean,
            "safe_zones_detected": persistent}


# ---------------------------------------------------------------------------
# Decisión por clip (muestreo + tramos)
# ---------------------------------------------------------------------------

def apply_game_voice(segments, game_voice, duration, min_run=1.5):
    """Parte los tramos de layout por los tramos de voz del juego.

    Idea de Uri (clipper-directos, layout.py): cuando habla el
    juego, el plano pasa a juego a pantalla completa; al terminar,
    vuelve el layout que había. Aquí el layout del proyecto es
    streamer arriba / gameplay abajo (split RDJavi), así que el tramo de voz
    del juego es "game" (juego a pantalla completa, mismo recorte
    que "center") y el resto conserva su layout.

    ``segments``: tramos de decide_layout (start/end/layout/params/
    reason). ``game_voice``: tramos normalizados de
    game_voice.normalize_game_voice (o lista cruda: se normaliza
    aquí). Tramos "game" más cortos que ``min_run`` (1,5 s, como
    Uri MIN_RUN) se fusionan con el vecino más largo, para no
    parpadear de plano. Devuelve la lista nueva de tramos, sin
    huecos y en orden.
    """
    from .game_voice import normalize_game_voice

    spans = normalize_game_voice(game_voice, duration)
    if not spans or not segments:
        return segments
    out = []
    for seg in segments:
        cuts = [seg["start"], seg["end"]]
        for span in spans:
            for edge in (span["start"], span["end"]):
                if seg["start"] < edge < seg["end"]:
                    cuts.append(edge)
        cuts = sorted(set(cuts))
        for a, b in zip(cuts, cuts[1:]):
            if b - a <= 0:
                continue
            mid = (a + b) / 2.0
            is_game = any(s["start"] <= mid < s["end"] for s in spans)
            piece = dict(seg)
            piece["start"], piece["end"] = float(a), float(b)
            if is_game:
                piece["layout"] = "game"
                piece["params"] = {}
                piece["reason"] = (
                    seg.get("reason", "")
                    + " | voz del juego: juego a pantalla completa "
                      "(tramo estimado)" if any(
                          s.get("estimated") for s in spans
                          if s["start"] <= mid < s["end"])
                    else seg.get("reason", "")
                    + " | voz del juego: juego a pantalla completa")
            out.append(piece)
    # Fusionar tramos "game" más cortos que min_run con el vecino
    # más largo (como Uri: los runs cortos toman el modo del vecino
    # más largo), y re-fusionar vecinos con el mismo layout.
    merged = []
    for piece in out:
        if merged and merged[-1]["layout"] == piece["layout"]:
            merged[-1]["end"] = piece["end"]
        else:
            merged.append(dict(piece))
    changed = True
    while changed and len(merged) > 1:
        changed = False
        for i, piece in enumerate(merged):
            if piece["layout"] != "game":
                continue
            if piece["end"] - piece["start"] >= min_run:
                continue
            neighbours = [merged[j] for j in (i - 1, i + 1)
                          if 0 <= j < len(merged)]
            if not neighbours:
                continue
            winner = max(neighbours,
                         key=lambda n: n["end"] - n["start"])
            piece["layout"] = winner["layout"]
            piece["params"] = dict(winner.get("params") or {})
            piece["reason"] = (piece.get("reason", "")
                               + " | tramo de juego < 1,5 s: fusionado "
                                 "con el vecino más largo")
            changed = True
            break
        if changed:
            fused = []
            for piece in merged:
                if fused and fused[-1]["layout"] == piece["layout"]:
                    fused[-1]["end"] = piece["end"]
                else:
                    fused.append(dict(piece))
            merged = fused
    merged[0]["start"] = 0.0
    for i in range(1, len(merged)):
        merged[i]["start"] = merged[i - 1]["end"]
    merged[-1]["end"] = float(duration)
    return merged


def decide_layout(video_path, duration, start_offset=0.0, cfg=None,
                  game_voice=None):
    """Decide el layout de un clip muestreando fotogramas.

    Devuelve {"mode", "segments": [{start, end, layout, params, reason}],
    "samples": [{t, layout, reason}]}. Los tiempos son relativos al clip.

    ``game_voice`` (opcional): tramos de voz del juego (ver
    game_voice.py; también vale ``cfg["game_voice"]``). Los tramos
    que caen dentro pasan a layout "game" (juego a pantalla
    completa); el resto conserva su layout (split RDJavi:
    streamer arriba, gameplay abajo).
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    # Voz del juego: también vale en los layouts forzados (el modo
    # forzado es el layout base; los tramos de voz del juego pasan
    # a "game" igualmente, como Uri).
    game_voice = (game_voice if game_voice is not None
                  else cfg.get("game_voice"))
    forced = cfg.get("mode", "auto")
    if forced in ("stack", "pip", "center"):
        segments = [{"start": 0.0, "end": float(duration),
                     "layout": forced, "params": {},
                     "reason": f"forzado por config/CLI (--layout {forced})"}]
        if game_voice:
            segments = apply_game_voice(segments, game_voice, duration)
        return {"mode": forced, "segments": segments, "samples": []}
    if cfg.get("facecam_box"):
        # Caja explícita en px del vídeo original (config.yaml):
        # manda sobre la detección y recibe el MISMO trato que un
        # pip detectado: split vertical (regla 2026-11-16, webcam +
        # gameplay nunca en PiP ni en recorte centrado). Antes este
        # camino devolvía layout "pip" con la caja en px originales
        # sin convertir (el grafo la reescalaba como si fueran coords
        # de muestra 160x90: caja 12x fuera de sitio) y el PiP tapaba
        # el juego. La caja se pasa a muestra y _split_params calcula
        # la ventana de juego a la derecha de la cámara.
        src_size = None
        try:
            src_size = _probe_size(video_path)
        except Exception:
            src_size = None
        if src_size is not None:
            sw, sh = src_size
            fx, fy, fw, fh = [float(v)
                              for v in cfg["facecam_box"][:4]]
            box_s = [fx * SAMPLE_W / sw, fy * SAMPLE_H / sh,
                     fw * SAMPLE_W / sw, fh * SAMPLE_H / sh]
            segments = [{"start": 0.0, "end": float(duration),
                         "layout": "split",
                         "params": _split_params(box_s, cfg),
                         "reason": ("facecam_box explícito en config.yaml "
                                    f"{[int(v) for v in cfg['facecam_box'][:4]]} "
                                    "-> split vertical RDJavi: streamer "
                                    "arriba, gameplay abajo (sin PiP en el centro)")}]
            if game_voice:
                segments = apply_game_voice(segments, game_voice, duration)
            return {"mode": "split", "segments": segments, "samples": []}
        # Sin tamaño del vídeo no se puede convertir la caja: caer al
        # auto de abajo en vez de un PiP con coords imposibles.

    # Escaneo por tramo (Bloque 4): muestrea cada 2 s y clasifica
    # 0/1/2 cams por regiones estables. Se reusan sus fotogramas para
    # la clasificación por fotograma (un solo muestreo, no dos).
    scan = None
    scan_samples = None
    if cfg.get("scan_enabled"):
        try:
            scan_samples, persistent = _scan_frames(
                video_path, duration, start_offset, cfg)
            scan = {"persistent_corners": persistent}
        except Exception:
            scan_samples = None

    if scan_samples:
        samples = []
        for s in scan_samples:
            if s.get("_frame") is None or s["cams"] is None:
                continue
            g = _grid_from_bytes(s["_frame"])
            layout, params, reason = classify_frame(g, cfg)
            reason = f"{reason} | escaneo: {s['cams']} cams ({s['stable_detail']})"
            # El escaneo corrige la clasificación por fotograma:
            # 2 cams persistentes CON costura central y sin franja
            # apilada (llenan el fotograma) -> stack con la banda de
            # las cajas estables. Sin costura central no se corrige:
            # puede ser una imagen estática (menú/pausa), no 2 cams.
            if s["cams"] == 2 and s.get("seam") and layout != "stack":
                band = _scan_band(s["boxes"])
                if band is not None:
                    layout = "stack"
                    params = {"band": band, "split_x": SAMPLE_W // 2}
                    reason += " | corregido a stack por escaneo"
            # 1 cam persistente sin caja detectada -> PiP con la caja
            # estable del escaneo (la caja de classify_frame manda
            # si existe: tiene el borde real). La caja debe ser un
            # sub-rectángulo (< 45% del fotograma): si es media
            # pantalla o más, es una imagen estática, no una webcam.
            elif s["cams"] == 1 and layout == "center" and s["boxes"]:
                box = next(iter(s["boxes"].values()))
                area = ((box[2] - box[0] + 1) * (box[3] - box[1] + 1)
                        / (SAMPLE_W * SAMPLE_H))
                if area < 0.45:
                    layout = "pip"
                    params = {"box": [box[0], box[1], box[2] - box[0] + 1,
                                      box[3] - box[1] + 1]}
                    reason += " | corregido a pip por escaneo"
            # Vtuber / PNG tuber: avatar reactivo en una esquina sin
            # cara humana (Haar no lo ve) y sin borde de caja
            # superior. Si está presente en esta muestra y el layout
            # sigue siendo center, esa caja ES la cámara: pip (luego
            # split, juego arriba y avatar abajo). No pisa un stack
            # ni un pip ya detectado por el escaneo.
            if layout == "center" and s.get("vtuber"):
                vt = s["vtuber"]
                layout = "pip"
                params = {"box": [float(v) for v in vt["box"][:4]]}
                reason += (f" | vtuber_detected: {vt['detail']} "
                           f"-> pip (luego split)")
            # Arreglo 6: anclar el divisor al borde entre las dos cajas
            # estables (el divisor real de las dos cams). El split_x de
            # classify_frame es el gradiente máximo y a veces es un
            # borde DENTRO de una cam (Coker: 68 en vez de 80).
            if (layout == "stack" and s["cams"] == 2
                    and "left" in s.get("boxes", {})
                    and "right" in s.get("boxes", {})):
                boundary = (s["boxes"]["left"][2]
                            + s["boxes"]["right"][0]) / 2
                params = dict(params)
                params["split_x"] = boundary
                reason += f" | divisor anclado a cajas estables x={boundary:.0f}"
            samples.append({"t": s["t"], "layout": layout,
                            "params": params, "reason": reason,
                            "cams": s["cams"], "avatar": s["avatar"]})
        scan_segments_out = None
        if samples:
            known = [s for s in scan_samples if s["cams"] is not None]
            scan_segments_out = {
                "segments": _scan_runs(
                    known, max(1, int(cfg["scan_every_s"])),
                    duration, float(cfg["min_segment_s"])),
                "safe_zones_detected": scan["persistent_corners"]}
    else:
        step = max(1, int(cfg["sample_every_s"]))
        times = list(range(2, int(duration), step))
        if not times:
            times = [2]
        samples = []
        for t in times:
            g = gray_frame(video_path, start_offset + t)
            if g is None:
                continue
            layout, params, reason = classify_frame(g, cfg)
            samples.append({"t": float(t), "layout": layout,
                            "params": params, "reason": reason})
        scan_segments_out = None
    if not samples:
        return {"mode": "center",
                "segments": [{"start": 0.0, "end": float(duration),
                              "layout": "center", "params": {},
                              "reason": "no se pudo muestrear ningún fotograma"}],
                "samples": []}

    # Suavizado: una muestra aislada que difiere de SUS DOS vecinas
    # (que coinciden entre sí) es casi seguro un error de detección,
    # no un cambio real: se reasigna al layout de las vecinas.
    for i in range(1, len(samples) - 1):
        prev, cur, nxt = samples[i - 1], samples[i], samples[i + 1]
        if (cur["layout"] != prev["layout"]
                and cur["layout"] != nxt["layout"]
                and prev["layout"] == nxt["layout"]):
            cur["layout"] = prev["layout"]
            cur["params"] = prev["params"]
            cur["reason"] = (f"muestra aislada reclasificada a "
                             f"{prev['layout']} (vecinas coinciden)")

    # Tramos: cada muestra representa [t-step/2, t+step/2); las rachas
    # de muestras con el mismo layout forman un tramo. Un tramo más
    # corto que min_segment_s se fusiona con el vecino más largo.
    # (Sin esto, el tramo duraba 0 s —solo el instante muestreado— y
    # un cambio real de layout a mitad de clip se perdía.)
    min_seg = float(cfg["min_segment_s"])
    step = max(1, int(cfg["sample_every_s"]))
    runs = []
    for s in samples:
        span_start = max(0.0, s["t"] - step / 2)
        span_end = min(float(duration), s["t"] + step / 2)
        # La caja también parte el tramo: dos cámaras distintas en
        # el mismo clip (vtuber abajo-izquierda y luego webcam humana
        # arriba-izquierda) no pueden compartir un tramo con una sola
        # caja; si no, la caja de la última muestra se aplicaría a
        # todo el tramo y la primera cámara desaparecería del recorte.
        box_key = None
        if s["layout"] == "pip":
            box = (s.get("params") or {}).get("box") or []
            box_key = tuple(round(float(v), 1) for v in box[:4])
        if (runs and runs[-1]["layout"] == s["layout"]
                and runs[-1].get("_box_key") == box_key):
            runs[-1]["end"] = span_end
            runs[-1]["params"] = s["params"]
            runs[-1]["_params_list"].append(s["params"])
        else:
            runs.append({"layout": s["layout"], "start": span_start,
                         "end": span_end, "params": s["params"],
                         "reason": s["reason"], "_box_key": box_key,
                         "_params_list": [s["params"]]})
    runs[0]["start"] = 0.0
    runs[-1]["end"] = float(duration)
    merged = _absorb_short_runs(runs, min_seg)
    # Normalizar: sin huecos entre tramos (el tramo absorbido deja
    # su tiempo al vecino; si no, el concat perdería esos segundos).
    merged[0]["start"] = 0.0
    for i in range(1, len(merged)):
        merged[i]["start"] = merged[i - 1]["end"]
    merged[-1]["end"] = float(duration)
    for r in merged:
        r["start"] = float(r["start"])
        r["end"] = float(r["end"])
    # Split vertical (regla 2026-11-16): webcam+gameplay NUNCA va en
    # PiP grande en el centro ni en recorte centrado que siga la
    # cara. Un tramo "pip" se convierte a "split" con su caja, y un
    # tramo "center" con webcam detectada por la cara también. El
    # PiP solo queda si se fuerza explícito (--layout pip).
    src_size = None
    for r in merged:
        if r["layout"] == "pip" and r["params"].get("box"):
            r["params"] = _split_params(r["params"]["box"], cfg)
            r["layout"] = "split"
            r["reason"] += (" | split vertical: juego arriba, cámara "
                            "abajo (sin PiP en el centro)")
        elif r["layout"] == "center":
            if src_size is None:
                try:
                    src_size = _probe_size(video_path)
                except Exception:
                    src_size = None
            if src_size is not None:
                sw, sh = src_size
                fbox = _face_cam_box(
                    video_path, r["end"] - r["start"],
                    start_offset + r["start"], sw, sh, cfg)
                if fbox is not None:
                    box_s = [fbox[0] * SAMPLE_W / sw, fbox[1] * SAMPLE_H / sh,
                             fbox[2] * SAMPLE_W / sw, fbox[3] * SAMPLE_H / sh]
                    r["params"] = _split_params(box_s, cfg)
                    r["layout"] = "split"
                    r["reason"] += (f" | webcam por cara en "
                                    f"{[int(v) for v in fbox]} -> split "
                                    f"vertical: juego arriba, cámara abajo")
    # Voz del juego (idea de Uri): los tramos en los que habla el
    # juego pasan a "game" (juego a pantalla completa); el resto
    # conserva su layout. Se aplica ANTES del seguimiento de cara /
    # divisor (Arreglo 6), que solo sigue tramos stack/center.
    game_voice = game_voice if game_voice is not None else cfg.get("game_voice")
    if game_voice:
        merged = apply_game_voice(merged, game_voice, duration)
    # Arreglo 6: params del tramo = MEDIANA de sus muestras, no la
    # última (una lectura ruidosa de split contaminaba todo el
    # tramo: Coker daba 68 en vez de 80) + seguimiento dentro del
    # tramo (divisor en stack, cara en center).
    for r in merged:
        plist = r.pop("_params_list", None)
        r.pop("_box_key", None)
        if r["layout"] == "stack" and plist:
            splits = [p["split_x"] for p in plist if "split_x" in p]
            bands = [p["band"] for p in plist if "band" in p]
            if splits or bands:
                r["params"] = dict(r["params"])
            if splits:
                r["params"]["split_x"] = _median(splits)
            if bands:
                r["params"]["band"] = [_median([b[i] for b in bands])
                                       for i in range(4)]
        if not cfg.get("track_enabled"):
            continue
        if r["layout"] not in ("stack", "center"):
            continue
        if src_size is None:
            try:
                src_size = _probe_size(video_path)
            except Exception:
                break
        src_w, src_h = src_size
        if r["layout"] == "stack":
            knots = _split_knots_from_samples(samples, scan_samples,
                                              src_w, cfg)
            seg_knots = [(t - r["start"], x) for (t, x) in knots
                         if r["start"] <= t <= r["end"]]
            if len({x for _t, x in seg_knots}) > 1:
                r["params"] = dict(r["params"])
                r["params"]["track"] = {
                    "kind": "split",
                    "knots": [[t, x] for (t, x) in seg_knots]}
                r["reason"] += (f" | divisor seguido "
                                f"({len(seg_knots)} knots)")
        else:  # center
            track = _track_face_centers(
                video_path, r["end"] - r["start"],
                start_offset + r["start"], src_w, src_h, cfg)
            if track is not None:
                r["params"] = dict(r["params"])
                r["params"]["track"] = track
                r["reason"] += (f" | seguimiento de cara "
                                f"({len(track['knots'])} knots)")
    out = {"mode": "auto", "segments": merged, "samples": samples}
    if scan_segments_out is not None:
        out["scan_segments"] = scan_segments_out["segments"]
        out["safe_zones_detected"] = scan_segments_out["safe_zones_detected"]
    return out


def _absorb_short_runs(runs, min_seg):
    """Fusiona el tramo más corto (< min_seg) con su vecino más largo."""
    runs = [dict(r) for r in runs]

    def _merge_params(dst, src):
        # Al absorber un tramo, sus muestras pasan al vecino (para la
        # mediana de params del Arreglo 6; los tramos de escaneo no
        # tienen _params_list y no se ven afectados).
        if "_params_list" in dst or "_params_list" in src:
            dst["_params_list"] = (dst.get("_params_list", [])
                                   + src.get("_params_list", []))

    while len(runs) > 1:
        short = min(range(len(runs)),
                    key=lambda i: runs[i]["end"] - runs[i]["start"])
        if runs[short]["end"] - runs[short]["start"] >= min_seg:
            break
        if short == 0:
            _merge_params(runs[1], runs[0])
            runs[1]["start"] = runs[0]["start"]
            runs.pop(0)
        elif short == len(runs) - 1:
            _merge_params(runs[-2], runs[-1])
            runs[-2]["end"] = runs[-1]["end"]
            runs.pop()
        elif ((runs[short - 1]["end"] - runs[short - 1]["start"])
              >= (runs[short + 1]["end"] - runs[short + 1]["start"])):
            _merge_params(runs[short - 1], runs[short])
            runs[short - 1]["end"] = runs[short]["end"]
            runs.pop(short)
        else:
            _merge_params(runs[short + 1], runs[short])
            runs[short + 1]["start"] = runs[short]["start"]
            runs.pop(short)
    return runs


# ---------------------------------------------------------------------------
# Construcción del filtro ffmpeg por tramo
# ---------------------------------------------------------------------------

def _scale_to_sample(params, src_w, src_h):
    """Pasa coordenadas del fotograma de muestra (160x90) a píxeles reales."""
    sx, sy = src_w / SAMPLE_W, src_h / SAMPLE_H
    out = {}
    if "band" in params:
        x0, y0, bw, bh = params["band"]
        out["band"] = [round(x0 * sx), round(y0 * sy),
                       round(bw * sx), round(bh * sy)]
        out["split_x"] = round(params["split_x"] * sx)
    if "box" in params and len(params["box"]) >= 4:
        x0, y0, bw, bh = params["box"][:4]
        out["box"] = [round(x0 * sx), round(y0 * sy),
                      round(bw * sx), round(bh * sy)]
        out["corner"] = params["box"][4] if len(params["box"]) > 4 else "top-right"
    if "game_x0" in params:  # split: ventana de juego (x en muestra)
        out["game_x0"] = round(float(params["game_x0"]) * sx)
        out["game_h"] = int(params.get("game_h", 1320))
        out["cam_h"] = int(params.get("cam_h", 600))
    return out


def _v2_game_crop(src_w, src_h):
    """Recorte del gameplay v2 ('w:h:x:y'): las fracciones de
    rdjavi.GAME_CROP (1440:1252:620:94 sobre 2560x1440), aspecto
    1080:940. Valores pares (yuv420p) y dentro del fotograma."""
    fw, fh, fx, fy = RDJAVI_GAME_CROP_FRACS
    gw = max(2, int(src_w * fw) // 2 * 2)
    gh = max(2, int(src_h * fh) // 2 * 2)
    gx0 = max(0, min(int(src_w * fx) // 2 * 2, src_w - gw))
    gy0 = max(0, min(int(src_h * fy) // 2 * 2, src_h - gh))
    return f"{gw}:{gh}:{gx0}:{gy0}"


def _split_rdjavi_graph(vin, vout, params, src_w, src_h, cfg,
                        t_offset=0.0):
    """Grafo de filtros de la variante RDJavi v2 (GTA 6, 1080x1920).

    PLANTILLA v2 (revisión 2026-11-18 21:01, clipagent/rdjavi.py),
    portada al camino automático: la geometría vieja (fondo
    desenfocado 0-730, webcam 580x590 centrada con esquinas
    redondeadas, gameplay 730-1770, banda 1770-1920 con handle y
    barra atardecer) queda RETIRADA. Ahora:
      * Webcam ARRIBA a ANCHO COMPLETO 1080x702 (y 0-702): COVER
        del rectángulo PiP detectado por clip (params["box"], nunca
        un default silencioso: la caja varía por clip, p3220
        arriba-izquierda, p7775 abajo-izquierda 561:365:0:626).
        Sin barras negras, sin esquinas redondeadas, sin fondo
        desenfocado (la webcam tapa todo el ancho: el fondo es
        negro liso detrás).
      * Franja negra del TÍTULO 702-800 (98 px): el texto se quema
        después, en el paso de subtítulos (burn_subtitles con
        title=...); el grafo solo deja la franja en negro.
      * Gameplay 800-1740 (1080x940, recorte 1080:940 sin
        aplastar), con zoom SUAVE 1,15x en el payoff (18-24 s del
        clip, rampa de 2 s, anclado abajo para no recortar la zona
        de diálogo del juego) — ver rdjavi._game_filter.
      * Barra negra de SUBTÍTULOS 1740-1920 (180 px): los
        subtítulos se queman después, abajo del todo, nunca sobre
        el gameplay (ahí van los subtítulos propios de GTA 6).
      * Punch-in de la webcam 1,30x en la reacción (16-26 s del
        clip, centrado en la cara = centro de la caja detectada),
        con doble rama + overlay enable (opción C de rdjavi.py).
    El pico va en el segundo 20 del clip por construcción (corte
    pico -20 s / +40 s), así que las ventanas 16-26 y 18-24 son
    relativas al clip; ``t_offset`` las desplaza al tramo (en
    multi-tramo, t es relativo al tramo).

    Sin paso de fondo pre-renderizado ni boxblur por fotograma:
    el lienzo es el gameplay con pad negro (el juego a y=800, negro
    arriba —franja del título— y abajo —barra de subtítulos—) y la
    webcam encima. Un solo grafo, sin fuentes generadas.
    """
    from .rdjavi import _game_filter, cam_cover_crop, punch_cover_crop
    p = _scale_to_sample(params, src_w, src_h)
    bx, by, bw, bh = p["box"]
    cam_box = f"{bw}:{bh}:{bx}:{by}"
    cover = cam_cover_crop(cam_box)
    # Ventanas relativas al tramo (t_offset = inicio del tramo en
    # el clip; 0 en un solo tramo).
    punch = (params or {}).get("punch_in")
    if punch is False:
        punch_win, punch_zoom = None, RDJAVI_PUNCH_ZOOM
    else:
        punch_win = (float((punch or {}).get("t0", RDJAVI_PUNCH_WINDOW[0])),
                     float((punch or {}).get("t1", RDJAVI_PUNCH_WINDOW[1])))
        punch_zoom = float((punch or {}).get("zoom", RDJAVI_PUNCH_ZOOM))
        punch_win = (punch_win[0] - t_offset, punch_win[1] - t_offset)
        if punch_win[1] <= 0:
            punch_win = None
    gz = (params or {}).get("game_zoom")
    if gz is False:
        game_zoom = None
    else:
        if isinstance(gz, (tuple, list)):
            game_zoom = (float(gz[0]), float(gz[1]))
        elif isinstance(gz, dict):
            game_zoom = (float(gz.get("t0", RDJAVI_GAME_ZOOM_WINDOW[0])),
                         float(gz.get("t1", RDJAVI_GAME_ZOOM_WINDOW[1])))
        else:
            game_zoom = RDJAVI_GAME_ZOOM_WINDOW
        game_zoom = (game_zoom[0] - t_offset, game_zoom[1] - t_offset)
        if game_zoom[1] <= 0:
            game_zoom = None
    game_filt = _game_filter(_v2_game_crop(src_w, src_h), game_zoom)
    parts = [f"[{vin}]split=2[cam_src][game_src];"]
    if punch_win is not None:
        cx, cy = bx + bw // 2, by + bh // 2
        zc = punch_cover_crop(cover, cx, cy, zoom=punch_zoom)
        p0, p1 = (f"{v:g}" for v in punch_win)
        parts.append(
            f"[cam_src]split=2[cam_n][cam_z];"
            f"[cam_n]crop={cover},scale={RDJAVI_CAM_W}:{RDJAVI_CAM_H},"
            f"setsar=1[camN];"
            f"[cam_z]crop={zc},scale={RDJAVI_CAM_W}:{RDJAVI_CAM_H},"
            f"setsar=1[camZ];")
        cam_overlay = (
            f"[canvas][camN]overlay={RDJAVI_CAM_X}:{RDJAVI_CAM_Y}:"
            f"enable='lt(t,{p0})+gte(t,{p1})'[step2a];"
            f"[step2a][camZ]overlay={RDJAVI_CAM_X}:{RDJAVI_CAM_Y}:"
            f"enable='gte(t,{p0})*lt(t,{p1})'[step2];")
    else:
        parts.append(
            f"[cam_src]crop={cover},scale={RDJAVI_CAM_W}:{RDJAVI_CAM_H},"
            f"setsar=1[cam];")
        cam_overlay = (
            f"[canvas][cam]overlay={RDJAVI_CAM_X}:{RDJAVI_CAM_Y}[step2];")
    parts.append(
        f"[game_src]{game_filt}[game];"
        f"[game]pad={OUT_W}:{OUT_H}:0:{RDJAVI_GAME_Y0}:black[canvas];")
    parts.append(cam_overlay)
    parts.append(f"[step2]format=yuv420p[{vout}]")
    return "".join(parts)


def segment_filter_graph(vin, vout, layout, params, src_w, src_h, cfg,
                         crop="center", t_offset=0.0):
    """Devuelve (filtro, ) que transforma [vin] en [vout] 1080x1920.

    ``t_offset``: inicio del tramo dentro del clip (0 en un solo
    tramo): las ventanas de zoom de la variante RDJavi v2 (punch-in
    16-26 s, zoom del gameplay 18-24 s) son relativas al clip y se
    desplazan al tramo con este offset.
    """
    p = _scale_to_sample(params, src_w, src_h)
    if layout == "split":
        # Split vertical (regla 2026-11-16, INVERTIDO 2026-11-18):
        # STREAMER ARRIBA y JUEGO ABAJO, sin superponerse nunca.
        # Variante por defecto para GTA 6: RDJavi v2 (plantilla
        # 2026-11-18, clipagent/rdjavi.py): webcam a ancho completo
        # 0-702, franja de título 702-800, gameplay 800-1740 con
        # zoom suave en el payoff, barra de subtítulos 1740-1920.
        # Sin fondo desenfocado, sin handle ni barra atardecer.
        # La cámara es FIJA por tramo salvo el punch-in (zoom
        # puntual sobre la webcam en un momento de reacción) vía
        # params["punch_in"] = {"t0", "t1", "zoom"} (tiempos
        # relativos al clip; el default v2 es 16-26 s, zoom 1,30)
        # y params["game_zoom"] = {"t0", "t1"} (default 18-24 s).
        # Variante "plain": split simple (cámara 600 arriba /
        # juego 1320 abajo).
        variant = (params or {}).get("split_variant") or resolve_split_variant(cfg)
        if variant == "rdjavi":
            return _split_rdjavi_graph(vin, vout, params, src_w, src_h,
                                       cfg, t_offset=t_offset)
        bx, by, bw, bh = p["box"]
        game_h = p.get("game_h", 1320)
        cam_h = p.get("cam_h", 600)
        gw = int(src_h * OUT_W / game_h)
        gx0 = min(max(p.get("game_x0", (src_w - gw) // 2), 0), src_w - gw)
        blur = int(cfg.get("blur_radius", 20))
        punch = (params or {}).get("punch_in")
        zoom = float((punch or {}).get("zoom", 1.25)) if punch else 1.0
        cw = _punch_crop_expr(bw, int(bw / zoom), punch)
        ch = _punch_crop_expr(bh, int(bh / zoom), punch)
        cx = _punch_crop_expr(bx, int(bx + (bw - bw / zoom) / 2), punch)
        cy = _punch_crop_expr(by, int(by + (bh - bh / zoom) / 2), punch)
        return (f"[{vin}]split=2[sp_g][sp_c];"
                f"[sp_g]crop={gw}:{src_h}:{gx0}:0,"
                f"scale={OUT_W}:{game_h},setsar=1[sp_gv];"
                f"[sp_c]crop={cw}:{ch}:{cx}:{cy},split=2[sp_cb][sp_cf];"
                f"[sp_cb]scale={OUT_W}:{cam_h}:force_original_aspect_ratio=increase,"
                f"crop={OUT_W}:{cam_h},boxblur={blur}:2[sp_cbg];"
                f"[sp_cf]scale={OUT_W}:{cam_h}:force_original_aspect_ratio=decrease,"
                f"setsar=1[sp_cfg];"
                f"[sp_cbg][sp_cfg]overlay=(W-w)/2:(H-h)/2[sp_cv];"
                f"[sp_cv][sp_gv]vstack,format=yuv420p[{vout}]")
    if layout == "stack":
        x0, y0, bw, bh = p.get("band", [0, 0, src_w, src_h])
        split = p.get("split_x", src_w // 2)
        blur = int(cfg.get("blur_radius", 20))
        # Arreglo 6: divisor que SIGUE su posición en el tiempo (la
        # expresión se evalúa por fotograma; t es relativo al tramo:
        # en el pase único -ss resetea los timestamps y en el
        # multi-tramo trim+setpts hace lo mismo). Sin track, split
        # fijo (mediana del tramo, ver decide_layout).
        track = (params or {}).get("track")
        if track and track.get("kind") == "split" and track.get("knots"):
            split_expr = _track_expr([(float(t), float(x))
                                      for (t, x) in track["knots"]])
            return (f"[{vin}]split=2[st_l][st_r];"
                    f"[st_l]crop=w='{split_expr}-{x0}':h={bh}:x={x0}:y={y0},"
                    f"split=2[st_lb][st_lf];"
                    f"[st_lb]scale=1080:960:force_original_aspect_ratio=increase,"
                    f"crop=1080:960,boxblur={blur}:2[st_lbg];"
                    f"[st_lf]scale=1080:960:force_original_aspect_ratio=decrease,"
                    f"setsar=1[st_lfg];"
                    f"[st_lbg][st_lfg]overlay=(W-w)/2:(H-h)/2[st_t];"
                    f"[st_r]crop=w='{x0 + bw}-{split_expr}':h={bh}:"
                    f"x='{split_expr}':y={y0},split=2[st_rb][st_rf];"
                    f"[st_rb]scale=1080:960:force_original_aspect_ratio=increase,"
                    f"crop=1080:960,boxblur={blur}:2[st_rbg];"
                    f"[st_rf]scale=1080:960:force_original_aspect_ratio=decrease,"
                    f"setsar=1[st_rfg];"
                    f"[st_rbg][st_rfg]overlay=(W-w)/2:(H-h)/2[st_b];"
                    f"[st_t][st_b]vstack,format=yuv420p[{vout}]")
        # Cada mitad se recorta COMPLETA (ancho entero de la mitad,
        # no un recorte centrado 9:8: así el apilado no corta caras
        # por los lados) y se ajusta por contenido sobre su propio
        # frame difuminado: fondo = la mitad escalada a llenar el
        # panel 1080x960 + boxblur; encima, la mitad nítida ajustada
        # (contain) y centrada. Sin barras negras, sea cual sea el
        # aspecto de la cam.
        lw, rw = split - x0, x0 + bw - split
        return (f"[{vin}]split=2[st_l][st_r];"
                f"[st_l]crop={lw}:{bh}:{x0}:{y0},split=2[st_lb][st_lf];"
                f"[st_lb]scale=1080:960:force_original_aspect_ratio=increase,"
                f"crop=1080:960,boxblur={blur}:2[st_lbg];"
                f"[st_lf]scale=1080:960:force_original_aspect_ratio=decrease,"
                f"setsar=1[st_lfg];"
                f"[st_lbg][st_lfg]overlay=(W-w)/2:(H-h)/2[st_t];"
                f"[st_r]crop={rw}:{bh}:{split}:{y0},split=2[st_rb][st_rf];"
                f"[st_rb]scale=1080:960:force_original_aspect_ratio=increase,"
                f"crop=1080:960,boxblur={blur}:2[st_rbg];"
                f"[st_rf]scale=1080:960:force_original_aspect_ratio=decrease,"
                f"setsar=1[st_rfg];"
                f"[st_rbg][st_rfg]overlay=(W-w)/2:(H-h)/2[st_b];"
                f"[st_t][st_b]vstack,format=yuv420p[{vout}]")
    if layout == "pip":
        if "box" in p:
            bx, by, bw, bh = p["box"]
        else:  # forzado sin detección: esquina superior derecha por defecto
            bw, bh = src_w // 3, (src_w // 3) * 9 // 16
            bx, by = src_w - bw, 0
        pw = int(cfg["pip_width"])
        ph = pw * 9 // 16
        corner = cfg.get("pip_corner", "top-right")
        # Bloque 4: si la esquina pedida tapa una zona segura (minimapa,
        # HUD), probar la otra esquina superior.
        zones = get_safe_zones(cfg.get("game", "GTA 6"), cfg)
        corner = _pip_corner_avoiding_zones(
            corner, zones, pw / OUT_W, ph / OUT_H,
            int(cfg["pip_margin"]) / OUT_W)
        ox = OUT_W - pw - int(cfg["pip_margin"]) if corner == "top-right" \
            else int(cfg["pip_margin"])
        oy = int(cfg["pip_margin"])
        x_expr = {"center": "(iw-ih*9/16)/2", "left": "0",
                  "right": "iw-ih*9/16"}.get(crop, "(iw-ih*9/16)/2")
        return (f"[{vin}]split=2[pp_bg][pp_cam];"
                f"[pp_bg]crop=ih*9/16:ih:x={x_expr},scale={OUT_W}:{OUT_H}[pp_bgv];"
                f"[pp_cam]crop={bw}:{bh}:{bx}:{by},scale={pw}:{ph}:flags=lanczos,"
                f"setsar=1[pp_camv];"
                f"[pp_bgv][pp_camv]overlay={ox}:{oy},format=yuv420p[{vout}]")
    if layout == "game":
        # Voz del juego (idea de Uri): juego a pantalla completa
        # durante el tramo. Mismo recorte que "center" (vertical
        # 9:16 del gameplay, sin webcam encima): la cámara no se
        # superpone al juego y no tapa minimapa ni HUD.
        x_expr = {"center": "(iw-ih*9/16)/2", "left": "0",
                  "right": "iw-ih*9/16"}.get(crop, "(iw-ih*9/16)/2")
        return (f"[{vin}]crop=ih*9/16:ih:x={x_expr},"
                f"scale={OUT_W}:{OUT_H},format=yuv420p[{vout}]")
    # center (defecto seguro). Arreglo 6: si el tramo trae un track
    # de cara, el recorte SIGUE su centro (interpolado entre
    # muestras con la expresión en x; t relativo al tramo).
    track = (params or {}).get("track")
    if track and track.get("kind") == "center" and track.get("knots"):
        cw = (src_h * 9) // 16
        expr = _track_expr([(float(t), float(x))
                            for (t, x) in track["knots"]])
        return (f"[{vin}]crop={cw}:{src_h}:x='{expr}-{cw // 2}',"
                f"scale={OUT_W}:{OUT_H},format=yuv420p[{vout}]")
    x_expr = {"center": "(iw-ih*9/16)/2", "left": "0",
              "right": "iw-ih*9/16"}.get(crop, "(iw-ih*9/16)/2")
    return (f"[{vin}]crop=ih*9/16:ih:x={x_expr},"
            f"scale={OUT_W}:{OUT_H},format=yuv420p[{vout}]")


# ---------------------------------------------------------------------------
# Corte de un clip con layout responsive
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Facecam del VOD, una sola vez (Fase 7.4)
# ---------------------------------------------------------------------------

VOD_LAYOUT_FILE = "layout.json"


def vod_layout_path(out_dir):
    """Ruta de runs/<vod>/layout.json."""
    return os.path.join(out_dir, VOD_LAYOUT_FILE)


def load_vod_layout(out_dir):
    """Layout del VOD guardado, o None si no existe o no valida."""
    import json
    path = vod_layout_path(out_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "facecam_box" not in data:
        return None
    return data


def save_vod_layout(out_dir, data):
    """Guarda runs/<vod>/layout.json (escritura atómica)."""
    import json
    path = vod_layout_path(out_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def detect_vod_facecam(video_path, duration, cfg=None, n_samples=5):
    """Detecta la facecam UNA vez por VOD (7.4).

    Muestrea `n_samples` fotogramas repartidos por el VOD y clasifica
    cada uno; la caja que aparece en al menos 2 muestras (misma
    posición, redondeada) es la facecam del VOD. Devuelve
    {"facecam_box": [x, y, w, h] en el fotograma de muestra (160x90)
    o None, "method": "detected"|"none", "samples": n, "hits": m}.
    Si `facecam_box` ya está en config, no detecta nada (manda
    siempre, ver decide_layout): devuelve method "config".
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    if cfg.get("facecam_box"):
        return {"facecam_box": list(cfg["facecam_box"]),
                "method": "config", "samples": 0, "hits": 0}
    if not duration or duration <= 0 or not os.path.isfile(video_path):
        return {"facecam_box": None, "method": "none",
                "samples": 0, "hits": 0}
    fractions = [0.05, 0.25, 0.5, 0.75, 0.95][:max(1, n_samples)]
    votes = {}
    samples = 0
    for frac in fractions:
        g = gray_frame(video_path, max(0.0, duration * frac))
        if g is None:
            continue
        samples += 1
        try:
            layout, params, _reason = classify_frame(g, cfg)
        except Exception:  # noqa: BLE001 - un fotograma malo no tumba nada
            continue
        if layout != "pip":
            continue
        box = (params or {}).get("box")
        if not box or len(box) < 4:
            continue
        key = tuple(int(round(float(v))) for v in box[:4])
        votes[key] = votes.get(key, 0) + 1
    if not votes:
        return {"facecam_box": None, "method": "none",
                "samples": samples, "hits": 0}
    best, hits = max(votes.items(), key=lambda kv: kv[1])
    if hits < 2:
        return {"facecam_box": None, "method": "none",
                "samples": samples, "hits": hits}
    return {"facecam_box": list(best), "method": "detected",
            "samples": samples, "hits": hits}


def cut_clip_with_layout(video_path, start_sec, duration, output_path,
                         crf=23, preset="fast", crop="center",
                         layout_mode="auto", cfg=None, game_voice=None):
    """Corta un clip vertical 1080x1920 con layout responsive.

    Devuelve la decisión de layout (para el manifiesto). Si el layout
    es uno solo para todo el clip, un solo pase de ffmpeg; si cambia
    por tramos, un pase por tramo + concat.

    ``game_voice``: tramos de voz del juego (ver game_voice.py;
    también vale ``cfg["game_voice"]``). Esos tramos se cortan en
    layout "game" (juego a pantalla completa).
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    if layout_mode is not None:
        # --layout en CLI manda sobre config.yaml; None = usar config.
        cfg["mode"] = layout_mode
    if game_voice is not None:
        cfg["game_voice"] = game_voice
    src_w, src_h = _probe_size(video_path)
    decision = decide_layout(video_path, duration, start_offset=start_sec,
                             cfg=cfg)
    segments = decision["segments"]
    base = ["ffmpeg", "-y", "-v", "error"]
    audio = ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
             "-c:a", "aac", "-b:a", "128k"]
    if len(segments) == 1:
        seg = segments[0]
        graph = segment_filter_graph("0:v", "vout", seg["layout"],
                                     seg["params"], src_w, src_h, cfg, crop)
        cmd = (base + ["-ss", str(start_sec), "-i", video_path,
                       "-t", str(duration),
                       "-filter_complex", graph,
                       "-map", "[vout]", "-map", "0:a"] + audio +
               ["-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", output_path])
    else:
        parts, vlabels, alabels = [], [], []
        for i, seg in enumerate(segments):
            s0 = start_sec + seg["start"]
            s1 = start_sec + seg["end"]
            graph = segment_filter_graph(f"v{i}_in", f"v{i}",
                                         seg["layout"], seg["params"],
                                         src_w, src_h, cfg, crop,
                                         t_offset=float(seg["start"]))
            parts.append(
                f"[0:v]trim=start={s0:.3f}:end={s1:.3f},"
                f"setpts=PTS-STARTPTS[{f'v{i}_in'}];{graph}")
            # Normalizar SAR antes del concat: un tramo split con
            # fondo difuminado puede salir con SAR != 1:1 (el scale
            # con force_original_aspect_ratio=increase lo conserva)
            # y el concat exige SAR identico en todas las entradas
            # (2026-11-18, VOD Rubius 2932415021: SAR 4770:4771 vs
            # 1:1, ffmpeg -22). Sin esto, cualquier clip multi-tramo
            # con split revienta en el concat.
            parts.append(f"[v{i}]setsar=1[v{i}s]")
            vlabels.append(f"[v{i}s]")
            parts.append(
                f"[0:a]atrim=start={s0:.3f}:end={s1:.3f},"
                f"asetpts=PTS-STARTPTS[a{i}]")
            alabels.append(f"[a{i}]")
        n = len(segments)
        parts.append(f"{''.join(vlabels)}concat=n={n}:v=1:a=0[vout]")
        # loudnorm DENTRO del grafo: -af no se puede mezclar con
        # -filter_complex en el mismo stream de audio.
        parts.append(f"{''.join(alabels)}concat=n={n}:v=0:a=1[a_cat]")
        parts.append("[a_cat]loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
        cmd = (base + ["-i", video_path,
                       "-filter_complex", ";".join(parts),
                       "-map", "[vout]", "-map", "[aout]",
                       "-c:a", "aac", "-b:a", "128k"] +
               ["-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", output_path])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg falló cortando {output_path}:\n"
                           f"{r.stderr.strip()[-2000:]}")
    decision["output"] = output_path
    return decision


def _probe_size(video_path):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height",
           "-of", "csv=p=0", video_path]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    w, h = out.strip().split(",")[:2]
    return int(w), int(h)


def layout_summary(decision):
    """Una línea por tramo, para el manifiesto."""
    lines = []
    for seg in decision["segments"]:
        lines.append(
            f"{seg['start']:.0f}-{seg['end']:.0f}s: {seg['layout']} "
            f"({seg['reason']})")
    return "; ".join(lines)


# ---------------------------------------------------------------------------
# Hoja de contacto 3x3 para QA (Bloque 4)
# ---------------------------------------------------------------------------

def _probe_duration(video_path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "csv=p=0", video_path]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def _segment_at(decision, t):
    for seg in decision.get("segments", []):
        if seg["start"] <= t < seg["end"]:
            return seg
    segs = decision.get("segments", [])
    return segs[-1] if segs else {"layout": "center", "params": {},
                                  "reason": "sin decisión"}


def _crop_rects_for_frame(src_w, src_h, layout, params, cfg, t=None):
    """Rectángulos (etiqueta, x, y, w, h en px del vídeo original) que
    el layout usa en ese instante. Son los mismos recortes que aplica
    segment_filter_graph: la hoja de contacto muestra lo que el clip
    final incluirá, no una aproximación. Con track y t (relativo al
    tramo), el rectángulo es el del seguimiento en ese instante
    (Arreglo 6); sin t, el estático de params."""
    p = _scale_to_sample(params, src_w, src_h)
    track = (params or {}).get("track")
    if layout == "game":
        # Voz del juego: solo se ve gameplay a pantalla completa.
        cw = src_h * 9 // 16
        cx = (src_w - cw) // 2
        return [("gameplay", cx, 0, cw, src_h)]
    if layout == "split":
        # Variante RDJavi v2: el gameplay es el recorte 1080:940
        # (_v2_game_crop); la variante "plain" usa la ventana
        # game_x0/game_h. La cara es la caja detectada.
        variant = (params or {}).get("split_variant")
        if variant == "rdjavi" or variant is None:
            fw, fh, fx, fy = RDJAVI_GAME_CROP_FRACS
            gw, gh = int(src_w * fw), int(src_h * fh)
            gx0, gy0 = int(src_w * fx), int(src_h * fy)
            rects = [("gameplay", gx0, gy0, gw, gh)]
        else:
            game_h = p.get("game_h", 1320)
            gw = int(src_h * OUT_W / game_h)
            gx0 = min(max(p.get("game_x0", (src_w - gw) // 2), 0),
                      src_w - gw)
            rects = [("gameplay", gx0, 0, gw, src_h)]
        if "box" in p:
            rects.append(("facecam", *p["box"]))
        return rects
    if layout == "stack":
        x0, y0, bw, bh = p.get("band", [0, 0, src_w, src_h])
        split = p.get("split_x", src_w // 2)
        if track and track.get("kind") == "split" and t is not None:
            knots = [(float(a), float(b)) for (a, b) in track["knots"]]
            split = int(round(_interp_knots(knots, t)))
        return [("cam-izq", x0, y0, split - x0, bh),
                ("cam-der", split, y0, x0 + bw - split, bh)]
    if layout == "pip":
        cw = src_h * 9 // 16
        cx = (src_w - cw) // 2
        rects = [("gameplay", cx, 0, cw, src_h)]
        if "box" in p:
            rects.append(("facecam", *p["box"]))
        return rects
    cw = src_h * 9 // 16
    if track and track.get("kind") == "center" and t is not None:
        knots = [(float(a), float(b)) for (a, b) in track["knots"]]
        cx = int(round(_interp_knots(knots, t))) - cw // 2
    else:
        cx = (src_w - cw) // 2
    return [("recorte", cx, 0, cw, src_h)]


def _drawbox_filter(rects, zones, src_w, src_h):
    """Filtros drawbox: recortes en verde lima, zonas seguras en rojo."""
    parts = []
    for _label, x, y, w, h in rects:
        parts.append(f"drawbox=x={int(x)}:y={int(y)}:w={int(w)}:h={int(h)}"
                     f":color=lime:thickness=4")
    for z in zones:
        zx0, zy0, zx1, zy1 = z["box"]
        parts.append(f"drawbox=x={int(zx0 * src_w)}:y={int(zy0 * src_h)}"
                     f":w={int((zx1 - zx0) * src_w)}"
                     f":h={int((zy1 - zy0) * src_h)}:color=red:thickness=3")
    return ",".join(parts)


def contact_sheet(video_path, output_path, decision=None, duration=None,
                  cfg=None, game=None):
    """Hoja de contacto 3x3: 9 fotogramas con el recorte dibujado.

    Cada celda lleva el rectángulo de recorte que el layout usará en
    ese instante (verde) y las zonas seguras del juego (rojo). Quien
    aprueba ve en 1 imagen si el minimapa o una cara quedan fuera.
    Devuelve {"output", "frames": [{t, layout, rects, safe_zones}]}.
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    src_w, src_h = _probe_size(video_path)
    if duration is None:
        duration = _probe_duration(video_path)
    if decision is None:
        decision = decide_layout(video_path, duration, cfg=cfg)
    zones = get_safe_zones(game or cfg.get("game", "GTA 6"), cfg,
                           decision.get("safe_zones_detected"))
    times = [duration * (i + 0.5) / 9 for i in range(9)]
    # Celda: mantener el aspecto del vídeo (apaisado 480x270,
    # vertical 270x480) para no deformar las caras al revisar.
    if src_w >= src_h:
        tw, th = 480, 270
    else:
        tw, th = 270, 480
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    have_font = os.path.exists(font)
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for t in times:
        cmd += ["-ss", f"{t:.2f}", "-i", video_path]
    filters = []
    layouts = []
    frames_info = []
    for i, t in enumerate(times):
        seg = _segment_at(decision, t)
        rects = _crop_rects_for_frame(src_w, src_h, seg["layout"],
                                      seg.get("params", {}), cfg,
                                      t - seg.get("start", 0.0))
        f = _drawbox_filter(rects, zones, src_w, src_h)
        label = f"t={t:.0f}s {seg['layout']}"
        # La etiqueta se dibuja DESPUÉS de escalar (en coords de la
        # celda): dibujarla antes la encogía a ~7 px y no se leía.
        f += f",scale={tw}:{th}:flags=lanczos,setsar=1"
        if have_font:
            f += (f",drawtext=fontfile={font}:text='{label}':x=8:y=8"
                  f":fontsize=20:fontcolor=white"
                  f":box=1:boxcolor=black@0.6")
        f += f"[v{i}]"
        filters.append(f"[{i}:v]{f}")
        layouts.append(f"{(i % 3) * tw}_{(i // 3) * th}")
        frames_info.append({"t": round(t, 1), "layout": seg["layout"],
                            "rects": [(r[0],) + tuple(int(v) for v in r[1:])
                                      for r in rects],
                            "safe_zones": [z["name"] for z in zones]})
    filters.append(f"{''.join(f'[v{i}]' for i in range(9))}"
                   f"xstack=inputs=9:layout={'|'.join(layouts)}[out]")
    cmd += ["-filter_complex", ";".join(filters),
            "-map", "[out]", "-frames:v", "1", "-q:v", "2", output_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg falló generando la hoja de contacto:\n"
                           f"{r.stderr.strip()[-2000:]}")
    return {"output": output_path, "frames": frames_info}
