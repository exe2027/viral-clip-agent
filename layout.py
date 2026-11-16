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
}


# ---------------------------------------------------------------------------
# Muestreo de fotogramas (gris, pequeño, stdlib)
# ---------------------------------------------------------------------------

def gray_frame(video_path, t, w=SAMPLE_W, h=SAMPLE_H):
    """Un fotograma en gris como lista de filas (0-255). None si falla."""
    cmd = ["ffmpeg", "-v", "error", "-ss", str(t), "-i", video_path,
           "-frames:v", "1", "-vf", f"scale={w}:{h},format=gray",
           "-f", "rawvideo", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    if len(out) < w * h:
        return None
    return [list(out[y * w:(y + 1) * w]) for y in range(h)]


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
    return {"mode": "auto", "segments": merged, "samples": samples}


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
        x0, y0, bw, bh = p["band"]
        split = p["split_x"]
        # Cada mitad: recorte con aspecto 9/8 (el del panel 1080x960),
        # centrado horizontalmente en su mitad.
        cw = min(split - x0, x0 + bw - split)
        cw = min(cw, int(bh * 9 / 8))
        ch = bh
        lx = x0 + (split - x0 - cw) // 2
        rx = split + (x0 + bw - split - cw) // 2
        return (f"[{vin}]split=2[st_l][st_r];"
                f"[st_l]crop={cw}:{ch}:{lx}:{y0},scale=1080:960:flags=lanczos,"
                f"setsar=1[st_t];"
                f"[st_r]crop={cw}:{ch}:{rx}:{y0},scale=1080:960:flags=lanczos,"
                f"setsar=1[st_b];"
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
