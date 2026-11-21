#!/usr/bin/env python3
"""Detector de webcam por bordes fijos + quietud interior.

Una webcam de directo es un rectángulo pequeño con un borde recto
que NO se mueve nunca, sobre un juego que sí se mueve. Este módulo
la encuentra por esas dos cosas, sin modelo de caras y sin llamar a
ninguna IA:

* Fotogramas repartidos por el vídeo, mirados en pequeño (320x180):
  para cada columna y cada fila se cuenta cuánto cambia la imagen
  en esa línea, y se toma la mediana entre fotogramas, así un
  momento movido no inventa una línea. Las líneas que destacan son
  los bordes. Cualquier rectángulo que forman esos bordes (y los
  bordes del propio fotograma) es un candidato.
* La imagen de dentro tiene que estar casi quieta. Esto es lo que
  distingue una webcam de los paneles del propio juego, que también
  tienen bordes rectos pero dejan ver el juego moviéndose detrás.
  En la práctica medida: una webcam real da ~4 de movimiento
  mediano, mientras minimapa, columna de chat, panel de HUD y
  marcador dan 33-50.
* Los bordes se miden otra vez sobre un fotograma a tamaño completo,
  al píxel, antes de devolver la caja.

Solo numpy + ffmpeg, que el proyecto ya exige. Sin modelo que
descargar, sin clave, sin salida a internet. Si no hay ffmpeg, no
hay numpy o el archivo no se lee, devuelve None y el pipeline
sigue con el comportamiento anterior (nunca levanta).

Uso: ``detect(path)`` -> {"x", "y", "w", "h", "corner", "score",
"conf"} en píxeles del vídeo original, o None. "conf" es "high"
cuando la imagen de dentro apenas se mueve mientras el juego de
alrededor corre; solo una caja "high" se acepta como rescate en
``render.detect_vod_facecam`` (antes que el fallback por caras).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

try:
    import numpy as np
except Exception:  # noqa: BLE001 - sin numpy no hay detector, no error
    np = None

W, H = 320, 180            # la imagen se mira en pequeño: solo importan las líneas
MAX_FRAMES = 24            # fotogramas repartidos por el vídeo
MIN_SIZE = 0.12            # lo más pequeña que puede ser una webcam, en fracción del ancho/alto
MAX_SIZE = 0.60            # y lo más grande
ASPECT_MIN = 0.55          # lo más estrecho: un móvil en vertical es ~0,56
ASPECT_MAX = 3.20          # lo más ancho: un banner es más ancho que esto
STILL_FRAC = 0.35          # cuánto del movimiento del juego puede tener una webcam
STILL_MIN = 6.0            # y el suelo, para un juego que de por sí casi no se mueve
STILL_SHARE = 0.40         # como mucho esta parte de la webcam puede moverse, o es juego.
                           # Calibrado 2026-11-19 en el clip 23: la webcam real mueve el
                           # 38,5 % de sus píxeles por encima del corte (el streamer se
                           # mueve, hay banner y logo dentro de la caja), así que 0,30
                           # la rechazaba y daba 0 candidatos. Con 0,40 la caja sale
                           # IoU 0,87 contra la manual y el nº de candidatos es 11
                           # (con 0,45 ya son 54: más riesgo de falsos positivos).
EDGE_FLUSH = 0.02          # un lado tan cerca del borde del fotograma ES el borde
EDGE = 2.2                 # cuánto más brillante que la línea media debe ser un borde


def _frames(path: str | Path, count: int = MAX_FRAMES):
    """El vídeo como unos pocos fotogramas en gris, repartidos."""
    if np is None:
        return np.zeros((0, H, W), dtype=np.uint8) if np is not None else []
    try:
        dur = _duration(path)
        rate = max(count / dur, 0.02) if dur > 0 else 0.2
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path),
             "-vf", f"fps={rate:.4f},scale={W}:{H},format=gray",
             "-frames:v", str(count), "-f", "rawvideo", "-"],
            capture_output=True, timeout=180)
    except Exception:  # noqa: BLE001 - sin ffmpeg o archivo ilegible: no hay webcam, no es un fallo
        return np.zeros((0, H, W), dtype=np.uint8)
    raw = proc.stdout or b""
    size = W * H
    n = len(raw) // size
    if n < 4:
        return np.zeros((0, H, W), dtype=np.uint8)
    return np.frombuffer(raw[:n * size], dtype=np.uint8).reshape(n, H, W)


def _duration(path: str | Path) -> float:
    try:
        proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                               "-of", "default=nw=1:nk=1", str(path)],
                              capture_output=True, text=True, timeout=60)
        return float((proc.stdout or "").strip() or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def _profiles(frames):
    """(líneas verticales, líneas horizontales): cuánto cambia en cada columna y cada fila.

    La mediana entre fotogramas, así un momento movido no inventa una línea."""
    if len(frames) < 4:
        return np.zeros(W), np.zeros(H)
    f = frames.astype(np.int16)
    gx = np.median(np.abs(np.diff(f, axis=2)).sum(axis=1), axis=0)   # cambio en cada borde de columna
    gy = np.median(np.abs(np.diff(f, axis=1)).sum(axis=2), axis=0)   # cambio en cada borde de fila
    return gx, gy


def _peaks(profile, limit: int):
    """Las líneas que destacan: (dónde, cuánto)."""
    if len(profile) < 3:
        return []
    med = float(np.median(profile)) or 1.0
    out = []
    for i in range(1, len(profile) - 1):
        if profile[i] >= profile[i - 1] and profile[i] > profile[i + 1] and profile[i] > EDGE * med:
            out.append((i, float(profile[i] / med)))
    return sorted(out, key=lambda p: -p[1])[:limit]


def _size(path: str | Path):
    proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                           "-show_entries", "stream=width,height", "-of", "default=nw=1", str(path)],
                          capture_output=True, text=True, timeout=60)
    w = h = 0
    for line in (proc.stdout or "").splitlines():
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        try:
            if k.strip() == "width":
                w = int(v.strip())
            elif k.strip() == "height":
                h = int(v.strip())
        except ValueError:
            continue
    return w, h


def _stillness(frames):
    """Cuánto cambia cada píxel entre fotogramas.

    Una webcam apenas cambia: una persona sentada en una habitación
    se mueve poco. El juego cambia en cada fotograma."""
    return frames.astype(np.float32).std(axis=0)


def _plausible(x0, y0, x1, y1, still, light, game, share=STILL_SHARE):
    """¿Podría este rectángulo ser una webcam? Por forma, quietud y luz.

    Devuelve cuánto se mueve, o None. Nada aquí dice en qué esquina
    está: una webcam puede estar en una esquina, en un borde, o con
    margen por todos lados."""
    bw, bh = (x1 - x0) / W, (y1 - y0) / H
    if not (MIN_SIZE <= bw <= MAX_SIZE and MIN_SIZE <= bh <= MAX_SIZE):
        return None
    # medido en píxeles: una imagen 4:3 es 1,33, un móvil en vertical ~0,56
    aspect = (bw * W) / max(1e-6, bh * H)
    if not (ASPECT_MIN <= aspect <= ASPECT_MAX):
        return None
    a, b, c, d = int(x0), int(y0), int(x1), int(y1)
    inside = still[b:d, a:c]
    if not inside.size:
        return None
    cut = max(STILL_MIN, STILL_FRAC * game)
    moving = float(np.median(inside))
    if moving > cut:
        return None                    # cambia con el juego: es el juego
    if float((inside > cut).mean()) > share:
        return None                    # buena parte se mueve: se ve el juego a través
    if float(light[b:d, a:c].mean()) < 0.30 * float(light.mean()):
        return None                    # un agujero negro, no una imagen
    return moving


def _local(vpeaks, hpeaks, still, light):
    """Todos los rectángulos que pueden formar las líneas fuertes y que aún parecen cámara.

    Devuelve el mejor primero: aquel cuyo borde más débil es el más
    fuerte, y de esos el más grande."""
    game = float(np.median(still))
    xs = sorted({0.0, float(W)} | {float(x) for x, _ in vpeaks[:10]})
    ys = sorted({0.0, float(H)} | {float(y) for y, _ in hpeaks[:10]})
    vs = {float(x): s for x, s in vpeaks}
    hs = {float(y): s for y, s in hpeaks}
    out = []
    for i, x0 in enumerate(xs):
        wx = [vs[v] for v in (x0,) if v in vs]
        for x1 in xs[i + 1:]:
            wscore = wx + [vs[v] for v in (x1,) if v in vs]
            for j, y0 in enumerate(ys):
                hy0 = [hs[v] for v in (y0,) if v in hs]
                for y1 in ys[j + 1:]:
                    score = wscore + hy0 + [hs[v] for v in (y1,) if v in hs]
                    if len(score) < 2:
                        continue       # una caja necesita dos bordes propios, o es solo el fotograma
                    moving = _plausible(x0, y0, x1, y1, still, light, game)
                    if moving is None:
                        continue
                    key = (min(score), (x1 - x0) * (y1 - y0))
                    out.append({"key": key, "x": x0, "y": y0, "x1": x1, "y1": y1,
                                "score": round(min(score), 2), "moving": moving})
    return sorted(out, key=lambda c: c["key"], reverse=True)


def _full_frame(path: str | Path):
    """Un fotograma en gris a tamaño completo, de la mitad del vídeo: donde se miden los bordes."""
    try:
        w, h = _size(path)
    except Exception:  # noqa: BLE001
        return None
    if not w or not h:
        return None
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, _duration(path) / 2.0):.1f}", "-i", str(path),
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True, timeout=120).stdout or b""
    except Exception:  # noqa: BLE001
        return None
    if len(raw) < w * h:
        return None
    return np.frombuffer(raw[:w * h], dtype=np.uint8).reshape(h, w).astype(np.float32)


def _best(profile, approx: float, span: float) -> float:
    """El cambio más fuerte cerca de una línea aproximada."""
    lo, hi = max(0, int(approx - span)), min(len(profile), int(approx + span) + 1)
    seg = profile[lo:hi]
    return float(lo + seg.argmax()) if len(seg) else float(approx)


def _snap(path: str | Path, box, w: int, h: int, span: float = 0.0):
    """Una caja de la rejilla pequeña, puesta sobre los bordes reales de la imagen.

    Un borde encontrado en pequeño queda a uno o dos píxeles; un lado
    a un pelo del borde del fotograma ES el borde."""
    frame = _full_frame(path)
    if frame is None:
        return None
    fh, fw = frame.shape
    col = np.abs(np.diff(frame, axis=1)).sum(axis=0)
    row = np.abs(np.diff(frame, axis=0)).sum(axis=1)
    # La rejilla pequeña es un cuarto de la imagen, así que un borde
    # encontrado en ella queda a unos píxeles; eso es `spread`.
    spread = max(4.0, span * max(fw, fh), 1.5 * max(fw / W, fh / H))

    def line(profile, approx, edge, extent):
        if abs(approx - edge) <= EDGE_FLUSH * extent:
            return float(edge)
        return _best(profile, approx, spread)

    x0, y0, x1, y1 = box
    return (line(col, x0 * fw / W, 0, fw), line(row, y0 * fh / H, 0, fh),
            line(col, x1 * fw / W, fw, fw), line(row, y1 * fh / H, fh, fh))


def _corner(x0, y0, x1, y1, w, h) -> str:
    """En qué parte del fotograma está la caja: esquina, borde o centro."""
    near = EDGE_FLUSH
    left, right = x0 <= w * near, x1 >= w * (1 - near)
    top, bottom = y0 <= h * near, y1 >= h * (1 - near)
    if left and top:
        return "top-left"
    if right and top:
        return "top-right"
    if left and bottom:
        return "bottom-left"
    if right and bottom:
        return "bottom-right"
    if left or right:
        return "right" if right else "left"
    if top or bottom:
        return "bottom" if bottom else "top"
    return "middle"


def _finish(path: str | Path, found: dict, w: int, h: int, still) -> dict | None:
    """La caja en píxeles del vídeo original, con lo seguro que es que sea una cámara."""
    snapped = _snap(path, (found["x"], found["y"], found["x1"], found["y1"]), w, h)
    if not snapped:
        return None
    x0, y0, x1, y1 = snapped
    if x1 - x0 < w * MIN_SIZE or y1 - y0 < h * MIN_SIZE:
        return None
    inside = still[int(found["y"]):int(found["y1"]), int(found["x"]):int(found["x1"])]
    game = float(np.median(still))
    moving = float(np.median(inside)) if inside.size else 99.0
    # seguro cuando la imagen de dentro apenas se mueve mientras el juego de alrededor corre
    conf = "high" if moving <= max(3.0, 0.25 * game) else "low"
    return {"x": int(round(x0)), "y": int(round(y0)), "w": int(round(x1 - x0)), "h": int(round(y1 - y0)),
            "corner": _corner(x0, y0, x1, y1, w, h), "score": found.get("score", 0.0), "conf": conf}


def detect(path: str | Path) -> dict | None:
    """La caja de la webcam en el vídeo original, en píxeles, o None.

    La caja es x, y, ancho, alto, con la confianza en "score" y lo
    seguro que es que sea una cámara en "conf". Sin modelo, sin IA,
    sin nada fuera del archivo: así corren los tests y así funciona
    un trabajo sin IA configurada."""
    if np is None:
        return None
    try:
        frames = _frames(path)
    except Exception:  # noqa: BLE001 - nunca tumba el trabajo
        return None
    if len(frames) < 4:
        return None
    try:
        w, h = _size(path)
    except Exception:  # noqa: BLE001
        return None
    if not w or not h:
        return None
    still = _stillness(frames)
    light = frames.astype(np.float32).mean(axis=0)
    vx, hy = _profiles(frames)
    vpeaks, hpeaks = _peaks(vx, 40), _peaks(hy, 40)
    found = _local(vpeaks, hpeaks, still, light) if (vpeaks and hpeaks) else []
    if not found:
        return None
    return _finish(path, found[0], w, h, still)
