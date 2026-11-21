#!/usr/bin/env python3
"""Gate cuadro por cuadro para renders rubius (CircleCI).

Espejo compacto del ``template_gate`` de main (clipagent/
final_review.py, camino rubius / subs_style ``word_white``): las
mismas comprobaciones y los mismos umbrales calibrados
(config.yaml ``validator.*``, con los valores de main como
fallback), sin portar las ~1400 líneas de la evolución de
final_review.py en main. El job de render de CircleCI lo corre
sobre el MP4 final: si no pasa, el job falla y NO se guarda el
artefacto.

Comprobaciones (todas sobre fotogramas muestreados en TODO el
clip, cada ``face_sample_every_s`` segundos, tope 24):
  * dimensiones: 1080x1920.
  * gameplay: presente (luma media > 12 y desviación > 5) en
    >= 80% de los fotogramas.
  * sin_bandas: sin banda negra arriba del gameplay ni abajo del
    todo en >= 80%.
  * titulo_banner: banner amarillo TRANSITORIO en t=0,5-3,5 s
    (amarillo >= 30% de su zona + texto blanco y negro/rojo).
  * subtitulos_blancos: blancos en la zona de subtítulos en
    >= 50% de los instantes con subtítulo (según el SRT).
  * subtitulos_una_palabra: ningún fotograma con texto en los
    dos bordes de la zona a la vez (una palabra centrada nunca
    toca los dos bordes; una línea completa sí).
  * banda_viva: la zona de cámara cambia entre fotogramas
    (no congelada) en >= 50% de los pares.
  * sfx_presente: algún efecto del pack SFX en el audio
    (correlación normalizada >= 0,5).
  * fuente_ariblk: Arial Black disponible en el entorno.

Sin SRT, la presencia de subtítulos queda débil (se registra,
no se inventa): subtitulos_blancos y subtitulos_una_palabra
pasan como "no verificable". La cámara a ancho completo no se
mide aquí (necesita el eslabón de bordes de main): en el render
rubius la cámara SIEMPRE es a ancho completo por construcción.

Uso:
    python -m clipagent.render_gate --clip clip.mp4 --srt clip.srt
    python -m clipagent.render_gate --clip clip.mp4 --config config.yaml
"""

import argparse
import json
import os
import subprocess
import sys

# Umbrales calibrados en main (final_review.py): scooter vs 52h.
# FUENTE ÚNICA: config.yaml (validator.*); estas constantes son
# el fallback cuando no hay config (valen lo mismo que config.yaml
# y hay que mantenerlas sincronizadas si cambia la calibración).
_GATE_MAX_FRAMES = 24
_GATE_BLACK_LUMA = 40.0
_GATE_GAME_LUMA_MIN = 12.0
_GATE_SUB_PX = 300
_GATE_LIVE_DIFF = 2.0
_YELLOW = ((200, 255), (200, 255), (0, 90))
_WHITE = ((200, 255), (200, 255), (200, 255))
_BLACK = ((0, 60), (0, 60), (0, 60))
_RED = ((180, 255), (0, 90), (0, 90))
_RUBIUS_BANNER_T0 = 0.5
_RUBIUS_BANNER_T1 = 3.5
_RUBIUS_BANNER_YELLOW_FRAC = 0.30
_RUBIUS_SUBS_EDGE_W = 150
_RUBIUS_SUBS_EDGE_COV = 0.10
_SFX_MIN_NCC = 0.5

_SFX_TEMPLATE_CACHE = {}


def _frame_array(path):
    """Fotograma JPG -> array numpy RGB (H, W, 3)."""
    try:
        from PIL import Image
        import numpy as np
        with Image.open(path) as im:
            return np.asarray(im.convert("RGB"))
    except ImportError:
        import cv2
        import numpy as np
        img = cv2.imread(str(path))
        if img is None:
            raise OSError(f"fotograma ilegible: {path}")
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def _luma(arr):
    """Luminancia de un array RGB."""
    return (0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1]
            + 0.114 * arr[:, :, 2])


def _count_color(arr, ranges):
    """Nº de píxeles dentro de los rangos (R, G, B)."""
    import numpy as np
    mask = np.ones(arr.shape[:2], dtype=bool)
    for ch, (lo, hi) in enumerate(ranges):
        mask &= (arr[:, :, ch] >= lo) & (arr[:, :, ch] <= hi)
    return int(mask.sum())


def _banner_yellow_frac(zone):
    """Fracción de píxeles amarillos en la zona del banner."""
    total = int(zone.shape[0]) * int(zone.shape[1])
    if total == 0:
        return 0.0
    return _count_color(zone, _YELLOW) / total


def _subs_edge_coverages(arr, zy0, zy1, edge_w=None):
    """Cobertura blanca en los bordes izquierdo/derecho de la zona
    de subtítulos (fracción de columnas con texto)."""
    zone = arr[zy0:zy1]
    white = ((zone[:, :, 0] >= 200) & (zone[:, :, 1] >= 200)
             & (zone[:, :, 2] >= 200))
    colcount = white.sum(axis=0)
    has = colcount >= 5
    w = int(arr.shape[1])
    edge = min(edge_w if edge_w is not None else _RUBIUS_SUBS_EDGE_W,
               w // 2)
    if edge <= 0:
        return 0.0, 0.0
    return float(has[:edge].mean()), float(has[-edge:].mean())


def _ncc_best(x, y, sr):
    """Mejor correlación cruzada normalizada de la plantilla y en x.

    Devuelve (mejor_corr, lag_s). FFT con numpy, sin scipy.
    """
    import numpy as np
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    m = len(y)
    if m == 0 or len(x) < m:
        return 0.0, 0.0
    n = len(x) + m
    nfft = 1 << int(np.ceil(np.log2(n)))
    X = np.fft.rfft(x, nfft)
    Y = np.fft.rfft(y[::-1], nfft)
    full = np.fft.irfft(X * Y, nfft)
    corr = full[m - 1:m - 1 + (len(x) - m + 1)]
    x2 = np.concatenate([[0.0], np.cumsum(x ** 2)])
    win = x2[m:] - x2[:-m]
    y_e = float(np.sum(y ** 2))
    denom = np.sqrt(np.maximum(win * y_e, 1e-12))
    ncc = corr / denom
    i = int(np.argmax(ncc))
    return float(ncc[i]), i / sr


def _sfx_template(effect, sr=16000):
    """Ventana audible de un efecto del pack, decodificada (16 kHz)."""
    from .audio import decode_audio
    from .sfx_mix import EFFECTS, sfx_path

    if effect in _SFX_TEMPLATE_CACHE:
        return _SFX_TEMPLATE_CACHE[effect]
    info = EFFECTS[effect]
    tpl, _ = decode_audio(sfx_path(effect))
    a = int(round(info["loud_offset"] * sr))
    b = int(round((info["loud_offset"] + info["loud_duration"]) * sr))
    out = (tpl[a:b], sr)
    _SFX_TEMPLATE_CACHE[effect] = out
    return out


def detect_sfx_in_clip(clip_path, min_ncc=None):
    """¿Se oye algún SFX del pack en el audio del clip?

    Correlación cruzada normalizada de cada efecto del pack (su
    ventana audible) contra el audio del clip: el SFX mezclado es
    el propio archivo con ganancia, así que la correlación en su
    segundo es ~1 (52h: boom 0.994, riser 0.979); sin SFX, el
    mejor asset queda <= 0.22 (scooter). Nunca levanta: sin audio
    o sin decodificar, no encontrado con el motivo.
    """
    threshold = (_SFX_MIN_NCC if min_ncc is None else float(min_ncc))
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", str(clip_path)],
            capture_output=True, text=True, timeout=30)
        has_audio = "audio" in (probe.stdout or "")
    except Exception:  # noqa: BLE001 - sin ffprobe no hay audio
        has_audio = False
    if not has_audio:
        return {"found": False, "effect": None, "corr": 0.0,
                "lag_s": None,
                "motivo": "sin pista de audio: SFX no verificable"}
    try:
        from .audio import decode_audio
        audio, sr = decode_audio(str(clip_path))
    except Exception as exc:  # noqa: BLE001 - el audio no tumba
        return {"found": False, "effect": None, "corr": 0.0,
                "lag_s": None,
                "motivo": f"audio no decodificable: {exc}"}
    if len(audio) == 0:
        return {"found": False, "effect": None, "corr": 0.0,
                "lag_s": None, "motivo": "audio vacío"}
    try:
        from .sfx_mix import EFFECTS
    except Exception as exc:  # noqa: BLE001
        return {"found": False, "effect": None, "corr": 0.0,
                "lag_s": None,
                "motivo": f"pack SFX no disponible: {exc}"}
    best = {"effect": None, "corr": 0.0, "lag_s": None}
    for effect in EFFECTS:
        try:
            template, _sr = _sfx_template(effect, sr)
        except Exception:  # noqa: BLE001 - un asset roto no tumba
            continue
        if len(template) == 0 or len(audio) < len(template):
            continue
        corr, lag = _ncc_best(audio, template, sr)
        if corr > best["corr"]:
            best = {"effect": effect, "corr": corr, "lag_s": lag}
    found = best["corr"] >= threshold
    best["found"] = found
    best["motivo"] = "" if found else (
        "sin SFX del pack en el audio "
        f"(mejor NCC {best['corr']:.2f}"
        + (f" con {best['effect']}" if best.get("effect") else "")
        + f", mínimo {threshold:.2f})")
    return best


def _srt_active_times(srt_path):
    """(inicio, fin) en segundos de cada entrada de un .srt, o []."""
    if not srt_path or not os.path.isfile(str(srt_path)):
        return []
    try:
        with open(srt_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []

    def _to_s(ts):
        parts = ts.strip().replace(",", ".").split(":")
        try:
            if len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + float(s)
            if len(parts) == 2:
                m, s = parts
                return int(m) * 60 + float(s)
        except ValueError:
            return None
        return None

    spans = []
    for line in text.splitlines():
        if "-->" not in line:
            continue
        a, b = line.split("-->", 1)
        start, end = _to_s(a), _to_s(b)
        if start is not None and end is not None and end > start:
            spans.append((start, end))
    return spans


def _is_active(ts, spans, margin=0.5):
    """True si el instante cae dentro de algún tramo de subtítulo."""
    return any(s - margin <= ts <= e + margin for s, e in spans)


def _get_duration(clip_path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-of", "csv=p=0", str(clip_path)],
        capture_output=True, text=True, timeout=30)
    try:
        return float((out.stdout or "").strip())
    except ValueError:
        return 0.0


def _get_dimensions(clip_path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0",
         str(clip_path)],
        capture_output=True, text=True, timeout=30)
    try:
        w, h = (out.stdout or "").strip().split(",")
        return int(w), int(h)
    except ValueError:
        return None, None


def extract_gate_frames(clip_path, out_dir, every_s, max_frames=None):
    """Fotogramas muestreados en TODO el clip.

    Un fotograma cada ``every_s`` segundos, con tope
    ``max_frames``. Devuelve [{"path", "ts"}]. Lanza
    FileNotFoundError si no hay clip y RuntimeError si ffmpeg falla.
    """
    clip_path = str(clip_path)
    if not os.path.isfile(clip_path):
        raise FileNotFoundError(f"clip no encontrado: {clip_path}")
    duration = _get_duration(clip_path)
    if duration <= 0:
        raise RuntimeError(f"sin duración medible: {clip_path}")
    times = []
    t = 1.0
    while t < duration - 0.5:
        times.append(round(t, 2))
        t += float(every_s)
    if not times:
        times = [max(0.5, duration / 2.0)]
    if max_frames is not None:
        times = times[:int(max_frames)]
    os.makedirs(out_dir, exist_ok=True)
    frames = []
    for i, ts in enumerate(times, start=1):
        path = os.path.join(out_dir, f"gate_{i:02d}_{ts:.1f}s.jpg")
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", str(ts),
               "-i", clip_path, "-frames:v", "1", path]
        subprocess.run(cmd, check=True, timeout=60)
        frames.append({"path": path, "ts": ts})
    return frames


def _arial_black_available():
    """(True/False/None, detalle): Arial Black en fontconfig.

    None = no verificable en este entorno (sin fc-list): el
    chequeo pasa como débil, nunca tumba el clip por eso.
    """
    try:
        out = subprocess.run(
            ["fc-list"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None, "sin fc-list en este entorno"
    if out.returncode != 0:
        return None, "fc-list falló"
    for line in (out.stdout or "").splitlines():
        if "arial black" in line.lower():
            return True, line.strip()[:120]
    return False, "Arial Black no aparece en fc-list"


def _default_recipe():
    """Receta rubius desde las constantes de rubius.py (sin config)."""
    from .rubius import (
        BANNER_X0, BANNER_X1, BANNER_Y0, BANNER_Y1,
        CAM_H, CAM_W, CAM_X, CAM_Y, GAME_Y,
    )
    return {
        "cam": {"x": CAM_X, "y": CAM_Y, "w": CAM_W, "h": CAM_H},
        "game": {"y0": GAME_Y, "y1": 1920},
        "title_banner": {"x0": BANNER_X0, "x1": BANNER_X1,
                         "y0": BANNER_Y0, "y1": BANNER_Y1},
        "subs_zone": {"y0": 900, "y1": 1020},
        "subs_style": "word_white",
    }


def _validator_cfg(cfg):
    """Umbrales del gate: config.yaml validator.* o calibración."""
    vcfg = (cfg or {}).get("validator") or {}
    return {
        "face_sample_every_s": float(
            vcfg.get("face_sample_every_s", 2)),
        "rubius_banner_t0": float(
            vcfg.get("rubius_banner_t0", _RUBIUS_BANNER_T0)),
        "rubius_banner_t1": float(
            vcfg.get("rubius_banner_t1", _RUBIUS_BANNER_T1)),
        "rubius_banner_yellow_frac": float(
            vcfg.get("rubius_banner_yellow_frac",
                     _RUBIUS_BANNER_YELLOW_FRAC)),
        "rubius_subs_edge_w": int(
            vcfg.get("rubius_subs_edge_w", _RUBIUS_SUBS_EDGE_W)),
        "rubius_subs_edge_cov": float(
            vcfg.get("rubius_subs_edge_cov", _RUBIUS_SUBS_EDGE_COV)),
        "sfx_min_ncc": float(
            vcfg.get("sfx_min_ncc", _SFX_MIN_NCC)),
    }


def render_gate(clip_path, srt_path=None, cfg=None, out_dir=None):
    """Gate cuadro por cuadro de un render rubius.

    Devuelve {pasa, checks, fallos, motivo}. ``checks`` lleva por
    chequeo {pasa, medicion, motivo}. Sin SRT, la presencia de
    subtítulos queda débil (se registra, no se inventa).
    """
    from .rubius import load_rubius_recipe

    out_dir = out_dir or os.path.join(
        os.path.dirname(str(clip_path)), "render_gate")
    checks = {}

    def _check(name, ok, medicion, motivo=""):
        checks[name] = {"pasa": bool(ok), "medicion": medicion,
                        "motivo": motivo if not ok else ""}
        return bool(ok)

    vcfg = _validator_cfg(cfg)
    try:
        recipe = load_rubius_recipe(cfg) if cfg else _default_recipe()
    except Exception as exc:  # noqa: BLE001 - sin receta no hay gate
        return {"pasa": False, "checks": checks, "fallos": ["receta"],
                "motivo": f"sin receta rubius: {exc}"}
    if recipe.get("subs_style") != "word_white":
        return {"pasa": False, "checks": checks,
                "fallos": ["subs_style"],
                "motivo": "la receta no es rubius (subs_style "
                          f"{recipe.get('subs_style')!r})"}
    try:
        frames = extract_gate_frames(
            clip_path, out_dir,
            every_s=vcfg["face_sample_every_s"],
            max_frames=_GATE_MAX_FRAMES)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        return {"pasa": False, "checks": checks,
                "fallos": ["sin_fotogramas"],
                "motivo": f"sin fotogramas para el gate: {exc}"}
    spans = _srt_active_times(srt_path)
    arrays = []
    for fr in frames:
        try:
            arrays.append((fr["ts"], _frame_array(fr["path"])))
        except (OSError, ValueError):
            continue
    if not arrays:
        return {"pasa": False, "checks": checks,
                "fallos": ["sin_fotogramas"],
                "motivo": "ningún fotograma legible para el gate"}
    # 1. Dimensiones 1080x1920.
    w0, h0 = _get_dimensions(clip_path)
    _check("dimensiones", (w0, h0) == (1080, 1920),
           f"{w0}x{h0}", "el clip no es 1080x1920")

    cam = recipe["cam"]
    gy0, gy1 = (int(recipe["game"]["y0"]),
                int(recipe["game"]["y1"]))
    banner = recipe["title_banner"]
    bx0, bx1 = int(banner["x0"]), int(banner["x1"])
    by0, by1 = int(banner["y0"]), int(banner["y1"])
    zy0, zy1 = (int(recipe["subs_zone"]["y0"]),
                int(recipe["subs_zone"]["y1"]))
    active = [(ts, a) for ts, a in arrays
              if not spans or _is_active(ts, spans)]

    def _share(pred):
        if not arrays:
            return 0.0
        return sum(1 for _ts, a in arrays if pred(a)) / len(arrays)

    # 2. Gameplay presente (no vacío/negro).
    game_ok = _share(
        lambda a: _luma(a[gy0:gy1]).mean() > _GATE_GAME_LUMA_MIN
        and _luma(a[gy0:gy1]).std() > 5.0)
    _check("gameplay", game_ok >= 0.8, f"presente {game_ok:.0%}",
           "gameplay vacío o negro")

    # 3. Sin bandas negras arriba del gameplay ni abajo del todo.
    def _is_bar(a, y0, y1):
        zone = _luma(a[y0:y1])
        return float((zone < _GATE_BLACK_LUMA).mean()) > 0.9
    top_ok = _share(lambda a: not _is_bar(a, gy0, gy0 + 100))
    bottom_ok = _share(lambda a: not _is_bar(a, gy1 - 120, gy1))
    _check("sin_bandas", top_ok >= 0.8 and bottom_ok >= 0.8,
           f"arriba {top_ok:.0%}, abajo {bottom_ok:.0%} sin banda negra",
           "hay banda negra (no es el corte limpio rubius)")

    # 4. Título en banner amarillo TRANSITORIO (t=0,5-3,5 s).
    t0 = vcfg["rubius_banner_t0"]
    t1 = vcfg["rubius_banner_t1"]
    yellow_frac = vcfg["rubius_banner_yellow_frac"]
    edge_w = vcfg["rubius_subs_edge_w"]
    edge_cov = vcfg["rubius_subs_edge_cov"]
    window = [(ts, a) for ts, a in arrays if t0 <= ts <= t1]
    pool = window if window else arrays
    banner_frames = 0
    best_frac = 0.0
    for _ts, a in pool:
        zone = a[by0:by1, bx0:bx1]
        frac = _banner_yellow_frac(zone)
        best_frac = max(best_frac, frac)
        if (frac >= yellow_frac
                and _count_color(zone, _WHITE) > 100
                and (_count_color(zone, _BLACK) > 50
                     or _count_color(zone, _RED) > 50)):
            banner_frames += 1
    _check("titulo_banner", banner_frames >= 1,
           f"banner en {banner_frames}/{len(pool)} fotogramas de "
           f"la ventana (amarillo máx. {best_frac:.0%})",
           f"sin banner amarillo de título en t={t0}-{t1} s")

    # 5. Subtítulos blancos palabra por palabra sobre el gameplay.
    if spans and active:
        white_share = sum(
            1 for _ts, a in active
            if _count_color(a[zy0:zy1, 350:730], _WHITE)
            > _GATE_SUB_PX) / len(active)
        _check("subtitulos_blancos", white_share >= 0.5,
               f"blancos en {white_share:.0%} de los instantes con "
               "subtítulo",
               "sin subtítulos blancos sobre el gameplay")
    else:
        _check("subtitulos_blancos", True,
               "sin SRT: presencia no verificada (débil)", "")
    if spans and active:
        both = 0
        for _ts, a in active:
            left, right = _subs_edge_coverages(a, zy0, zy1, edge_w=edge_w)
            if left >= edge_cov and right >= edge_cov:
                both += 1
        _check("subtitulos_una_palabra", both == 0,
               f"{both} fotogramas con texto en ambos bordes",
               "subtítulos de línea completa (no palabra por palabra)")
    else:
        _check("subtitulos_una_palabra", True,
               "sin SRT: presencia no verificada (débil)", "")

    # 6. Zona de cámara viva (no congelada).
    if len(arrays) > 1:
        import numpy as np
        cam_y0 = int(cam["y"])
        cam_y1 = cam_y0 + int(cam["h"])
        cam_x0 = int(cam["x"])
        cam_x1 = cam_x0 + int(cam["w"])
        diffs = []
        for (_t1, a1), (_t2, a2) in zip(arrays, arrays[1:]):
            diffs.append(float(np.abs(
                _luma(a1[cam_y0:cam_y1, cam_x0:cam_x1])
                - _luma(a2[cam_y0:cam_y1, cam_x0:cam_x1])).mean()))
        live_share = sum(1 for d in diffs if d > _GATE_LIVE_DIFF) \
            / len(diffs)
        _check("banda_viva", live_share >= 0.5,
               f"viva en {live_share:.0%} de los pares",
               "zona de cámara congelada")
    else:
        _check("banda_viva", True, "un solo fotograma", "")

    # 7. SFX del pack en el audio (solo rubius: la receta lleva
    # SFX por defecto; un render sin SFX no es rubius).
    sfx = detect_sfx_in_clip(clip_path, min_ncc=vcfg["sfx_min_ncc"])
    if sfx["found"]:
        _check("sfx_presente", True,
               f"{sfx['effect']} (NCC {sfx['corr']:.2f} en "
               f"t={sfx['lag_s']:.1f}s)", "")
    else:
        _check("sfx_presente", False,
               f"mejor NCC {sfx['corr']:.2f}",
               sfx.get("motivo") or "sin SFX del pack en el audio")

    # 8. Fuente Arial Black disponible en el entorno.
    font_ok, font_detail = _arial_black_available()
    if font_ok is None:
        _check("fuente_ariblk", True,
               f"no verificable: {font_detail}", "")
    else:
        _check("fuente_ariblk", bool(font_ok), font_detail,
               "fuente Arial Black no disponible en este entorno")

    fallos = [k for k, v in checks.items() if not v["pasa"]]
    motivo = ""
    if fallos:
        motivo = "gate rubius: " + ", ".join(
            f"{k} ({checks[k]['motivo'] or checks[k]['medicion']})"
            for k in fallos)
    return {"pasa": not fallos, "checks": checks, "fallos": fallos,
            "motivo": motivo}


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Gate cuadro por cuadro de un render rubius.")
    p.add_argument("--clip", required=True,
                   help="MP4 final del render rubius")
    p.add_argument("--srt", default=None,
                   help="SRT del clip (sin él, los subtítulos "
                   "quedan como débil, no verificados)")
    p.add_argument("--config", default=None,
                   help="config.yaml (receta rubius + umbrales "
                   "validator; por defecto la del repo)")
    p.add_argument("--out-dir", default=None,
                   help="Directorio de los fotogramas muestreados")
    p.add_argument("--json", action="store_true",
                   help="Solo imprime el JSON del resultado")
    args = p.parse_args(argv)

    cfg = None
    if args.config or os.path.isfile("config.yaml"):
        from .config import load_config
        cfg = load_config(args.config)
    res = render_gate(args.clip, srt_path=args.srt, cfg=cfg,
                      out_dir=args.out_dir)
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        for name, chk in res["checks"].items():
            mark = "OK  " if chk["pasa"] else "FAIL"
            print(f"{mark} {name}: {chk['medicion']}"
                  + (f" -- {chk['motivo']}" if chk["motivo"] else ""))
        print(f"gate rubius: {'PASA' if res['pasa'] else 'NO PASA'}"
              + (f" -- {res['motivo']}" if res["motivo"] else ""))
    return 0 if res["pasa"] else 1


if __name__ == "__main__":
    sys.exit(main())
