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
  * Una webcam + gameplay -> gameplay a pantalla completa con la webcam
    encima en picture-in-picture (esquina superior, sin tapar el minimapa
    ni el HUD, que en GTA están abajo).
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
    forzar con ``facecam_box`` en config.yaml o ``--layout pip``.
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
}


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
    Si no está claro, None (cae a centrado, seguro).
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
            bh = int(bw * 9 / 16)
            if bh > int(h * 0.45) or bh < 8:
                continue
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
                score = min(v_score, h_score)
                if best is None or score > best[0]:
                    best = (score, [x0, y0, bw_real, bh_real], corner)
    if best is None:
        return None
    return best[1] + [best[2]]


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
    c = lambda x, y: (min(max(y // CELL, 0), len(stable) - 1),
                      min(max(x // CELL, 0), len(stable[0]) - 1))
    corners = [stable[c(x0 + 2, y0 + 2)[0]][c(x0 + 2, y0 + 2)[1]],
               stable[c(x1 - 2, y0 + 2)[0]][c(x1 - 2, y0 + 2)[1]],
               stable[c(x0 + 2, y1 - 2)[0]][c(x0 + 2, y1 - 2)[1]],
               stable[c(x1 - 2, y1 - 2)[0]][c(x1 - 2, y1 - 2)[1]]]
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    center = stable[c(cx, cy)[0]][c(cx, cy)[1]]
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


def _detect_faces_opencv(gray_bytes, stable_boxes):
    """Rostros por OpenCV (Haar), SOLO si cv2 está disponible.

    Devuelve la lista de rostros [x, y, w, h] en px de muestra que
    caen DENTRO de una región estable (las caras del juego quedan
    fuera por definición: su región no es estable), o None si no
    hay OpenCV en este entorno. Es un refuerzo opcional: el método
    principal es la diferencia de frames (sin dependencias).
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    try:
        cascade_path = cv2.data.haarcascades + \
            "haarcascade_frontalface_default.xml"
        clf = cv2.CascadeClassifier(cascade_path)
        if clf.empty():
            return None
    except AttributeError:
        return None  # cv2 sin CascadeClassifier (p. ej. opencv 5 headless)
    img = np.frombuffer(gray_bytes, dtype=np.uint8).reshape(SAMPLE_H, SAMPLE_W)
    faces = clf.detectMultiScale(img, scaleFactor=1.1, minNeighbors=4,
                                 minSize=(12, 12))
    if len(faces) == 0:
        return []
    kept = []
    for (x, y, w, h) in faces:
        cx, cy = x + w // 2, y + h // 2
        if any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3]
               for b in stable_boxes):
            kept.append([int(x), int(y), int(w), int(h)])
    return kept


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
            samples.append({"t": t, "cams": None, "avatar": False,
                            "boxes": {}, "seam": False,
                            "stable_detail": "único fotograma",
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
        samples.append({"t": t, "cams": n_cams, "avatar": avatar,
                        "boxes": boxes, "seam": seam,
                        "stable_detail": detail,
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
        else:
            runs.append({"cams": s["cams"], "start": span_start,
                         "end": span_end, "avatar": s["avatar"],
                         "boxes": s["boxes"],
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

    Devuelve {"segments": [{start, end, cams, avatar, boxes, reason}],
    "samples": [{t, cams, avatar, boxes, stable_detail}],
    "safe_zones_detected": [{name, box}]}. Los tiempos son relativos
    al clip. Un tramo más corto que min_segment_s se fusiona con el
    vecino (misma regla que decide_layout).
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

def decide_layout(video_path, duration, start_offset=0.0, cfg=None):
    """Decide el layout de un clip muestreando fotogramas.

    Devuelve {"mode", "segments": [{start, end, layout, params, reason}],
    "samples": [{t, layout, reason}]}. Los tiempos son relativos al clip.
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    forced = cfg.get("mode", "auto")
    if forced in ("stack", "pip", "center"):
        return {"mode": forced,
                "segments": [{"start": 0.0, "end": float(duration),
                              "layout": forced, "params": {},
                              "reason": f"forzado por config/CLI (--layout {forced})"}],
                "samples": []}
    if cfg.get("facecam_box"):
        return {"mode": "pip",
                "segments": [{"start": 0.0, "end": float(duration),
                              "layout": "pip",
                              "params": {"box": list(cfg["facecam_box"])},
                              "reason": "facecam_box explícito en config.yaml"}],
                "samples": []}

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
        if runs and runs[-1]["layout"] == s["layout"]:
            runs[-1]["end"] = span_end
            runs[-1]["params"] = s["params"]
        else:
            runs.append({"layout": s["layout"], "start": span_start,
                         "end": span_end, "params": s["params"],
                         "reason": s["reason"]})
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
    out = {"mode": "auto", "segments": merged, "samples": samples}
    if scan_segments_out is not None:
        out["scan_segments"] = scan_segments_out["segments"]
        out["safe_zones_detected"] = scan_segments_out["safe_zones_detected"]
    return out


def _absorb_short_runs(runs, min_seg):
    """Fusiona el tramo más corto (< min_seg) con su vecino más largo."""
    runs = [dict(r) for r in runs]
    while len(runs) > 1:
        short = min(range(len(runs)),
                    key=lambda i: runs[i]["end"] - runs[i]["start"])
        if runs[short]["end"] - runs[short]["start"] >= min_seg:
            break
        if short == 0:
            runs[1]["start"] = runs[0]["start"]
            runs.pop(0)
        elif short == len(runs) - 1:
            runs[-2]["end"] = runs[-1]["end"]
            runs.pop()
        elif ((runs[short - 1]["end"] - runs[short - 1]["start"])
              >= (runs[short + 1]["end"] - runs[short + 1]["start"])):
            runs[short - 1]["end"] = runs[short]["end"]
            runs.pop(short)
        else:
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
    return out


def segment_filter_graph(vin, vout, layout, params, src_w, src_h, cfg,
                         crop="center"):
    """Devuelve (filtro, ) que transforma [vin] en [vout] 1080x1920."""
    p = _scale_to_sample(params, src_w, src_h)
    if layout == "stack":
        x0, y0, bw, bh = p.get("band", [0, 0, src_w, src_h])
        split = p.get("split_x", src_w // 2)
        # Cada mitad se recorta COMPLETA (ancho entero de la mitad,
        # no un recorte centrado 9:8: así el apilado no corta caras
        # por los lados) y se ajusta por contenido sobre su propio
        # frame difuminado: fondo = la mitad escalada a llenar el
        # panel 1080x960 + boxblur; encima, la mitad nítida ajustada
        # (contain) y centrada. Sin barras negras, sea cual sea el
        # aspecto de la cam.
        blur = int(cfg.get("blur_radius", 20))
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
    # center (defecto seguro)
    x_expr = {"center": "(iw-ih*9/16)/2", "left": "0",
              "right": "iw-ih*9/16"}.get(crop, "(iw-ih*9/16)/2")
    return (f"[{vin}]crop=ih*9/16:ih:x={x_expr},"
            f"scale={OUT_W}:{OUT_H},format=yuv420p[{vout}]")


# ---------------------------------------------------------------------------
# Corte de un clip con layout responsive
# ---------------------------------------------------------------------------

def cut_clip_with_layout(video_path, start_sec, duration, output_path,
                         crf=23, preset="fast", crop="center",
                         layout_mode="auto", cfg=None):
    """Corta un clip vertical 1080x1920 con layout responsive.

    Devuelve la decisión de layout (para el manifiesto). Si el layout
    es uno solo para todo el clip, un solo pase de ffmpeg; si cambia
    por tramos, un pase por tramo + concat.
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    if layout_mode is not None:
        # --layout en CLI manda sobre config.yaml; None = usar config.
        cfg["mode"] = layout_mode
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
                                         src_w, src_h, cfg, crop)
            parts.append(
                f"[0:v]trim=start={s0:.3f}:end={s1:.3f},"
                f"setpts=PTS-STARTPTS[{f'v{i}_in'}];{graph}")
            parts.append(
                f"[0:a]atrim=start={s0:.3f}:end={s1:.3f},"
                f"asetpts=PTS-STARTPTS[a{i}]")
            vlabels.append(f"[v{i}]")
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


def _crop_rects_for_frame(src_w, src_h, layout, params, cfg):
    """Rectángulos (etiqueta, x, y, w, h en px del vídeo original) que
    el layout usa en ese instante. Son los mismos recortes que aplica
    segment_filter_graph: la hoja de contacto muestra lo que el clip
    final incluirá, no una aproximación."""
    p = _scale_to_sample(params, src_w, src_h)
    if layout == "stack":
        x0, y0, bw, bh = p.get("band", [0, 0, src_w, src_h])
        split = p.get("split_x", src_w // 2)
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
                                      seg.get("params", {}), cfg)
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
