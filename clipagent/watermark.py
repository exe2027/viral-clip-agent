#!/usr/bin/env python3
"""Marca de agua en los clips (2026-11-18).

El usuario eligió su logo BITSONER de colores, arriba a la derecha,
320 px y 20% de opacidad: esos son los defaults del renderer.

El panel web expone la configuración por el puente
GET /api/bridge/settings (ver panel_settings.py): cuando la marca
está activada, la respuesta trae ``watermark_url`` (URL firmada
HMAC, TTL 60 min) y el cerebro la descarga y la compone como paso
final de overlay con ffmpeg. Sin panel (o con la marca apagada),
no se aplica nada.

Dos caminos la usan:
  * Automático (en vivo y VOD): transcribe.burn_subtitles() la
    aplica al final del quemado de subtítulos, que es el último
    paso de vídeo del pipeline (workers.subtitle_clip le pasa el
    cfg completo; el corte previo solo recibe cfg["layout"], así
    que ahí NO se puede ver). Si la descarga o el overlay fallan,
    el clip sigue sin marca: nunca se rompe un clip por la marca.
  * Manual (plantilla RDJavi): ``python -m clipagent.rdjavi
    --watermark [ruta|URL]`` (sin valor usa el logo de colores
    del repo, clipagent/assets/watermark_bitsoner_color.png).

La opacidad es 0-100 (%) en el panel, en config.yaml y en el CLI.
"""
import os
import subprocess
import sys
import tempfile
import urllib.request

WATERMARK_POSITIONS = ("center", "top-left", "top-right",
                       "bottom-left", "bottom-right")

# Elección del usuario (2026-11-18 22:32): logo de COLORES, arriba
# a la derecha, 320 px, 20%.
WATERMARK_DEFAULT_OPACITY = 20
WATERMARK_DEFAULT_SIZE_PX = 320
WATERMARK_DEFAULT_POSITION = "top-right"
WATERMARK_MARGIN_PX = 24
WATERMARK_MAX_BYTES = 5 * 1024 * 1024  # como el panel (5 MB)

DEFAULT_WATERMARK_PNG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "assets",
    "watermark_bitsoner_color.png")

WATERMARK_DEFAULTS = {
    "enabled": False,
    "source": None,
    "opacity": WATERMARK_DEFAULT_OPACITY,
    "size_px": WATERMARK_DEFAULT_SIZE_PX,
    "position": WATERMARK_DEFAULT_POSITION,
}


class WatermarkError(Exception):
    """La fuente de la marca no se pudo resolver (ruta/URL)."""


def _clamp(value, lo, hi, default):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:  # NaN
        return default
    return max(lo, min(hi, out))


def watermark_xy(video_w, video_h, logo_w, logo_h,
                 position=WATERMARK_DEFAULT_POSITION,
                 margin=WATERMARK_MARGIN_PX):
    """Esquina superior izquierda del logo ya escalado.

    El logo cabe en un cuadro de size_px x size_px; las esquinas
    llevan un margen para no pegarse al borde. Posición inválida
    -> arriba a la derecha (el default del usuario).
    """
    if position == "center":
        return (video_w - logo_w) // 2, (video_h - logo_h) // 2
    if position == "top-left":
        return margin, margin
    if position == "bottom-left":
        return margin, video_h - logo_h - margin
    if position == "bottom-right":
        return video_w - logo_w - margin, video_h - logo_h - margin
    return video_w - logo_w - margin, margin  # top-right y resto


def _probe_size(path):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height",
           "-of", "csv=p=0", path]
    out = subprocess.run(cmd, capture_output=True, text=True,
                         check=True).stdout
    w, h = out.strip().split(",")[:2]
    return int(w), int(h)


def _has_audio(path):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "a:0",
           "-show_entries", "stream=index", "-of", "csv=p=0", path]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return bool(out.strip())


def _is_png_or_jpeg(data):
    return (data[:8] == b"\x89PNG\r\n\x1a\n"
            or data[:3] == b"\xff\xd8\xff")


def resolve_watermark_source(source, cache_dir=None):
    """Ruta local o URL -> ruta local de un PNG/JPEG.

    URL http(s)/file: se descarga (máx. 5 MB, 15 s) a un temporal
    y se verifica por bytes mágicos. Lanza WatermarkError si no
    se puede resolver; el llamador decide si es fatal.
    """
    if not source:
        raise WatermarkError("sin fuente de marca de agua")
    source = str(source).strip()
    if os.path.isfile(source):
        return source
    if "://" not in source:
        raise WatermarkError(f"logo no existe: {source}")
    try:
        req = urllib.request.Request(source, headers={
            "User-Agent": "viral-clip-agent/watermark"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read(WATERMARK_MAX_BYTES + 1)
    except Exception as e:
        raise WatermarkError(f"no se pudo descargar {source}: {e}")
    if len(data) > WATERMARK_MAX_BYTES:
        raise WatermarkError(f"logo mayor de 5 MB: {source}")
    if not _is_png_or_jpeg(data):
        raise WatermarkError(f"no es PNG/JPEG: {source}")
    suffix = ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
    fd, path = tempfile.mkstemp(prefix="watermark-", suffix=suffix,
                                dir=cache_dir)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def apply_watermark(video_path, output_path, source,
                    opacity=WATERMARK_DEFAULT_OPACITY,
                    size_px=WATERMARK_DEFAULT_SIZE_PX,
                    position=WATERMARK_DEFAULT_POSITION,
                    cache_dir=None):
    """Compone el logo sobre el vídeo con ffmpeg (overlay con alfa).

    El logo se escala para caber en un cuadro de size_px x size_px
    sin deformarlo y se pone con la opacidad dada (colorchannelmixer
    aa: multiplica el alfa real del PNG). Vídeo re-codificado
    (libx264 veryfast crf 23), audio copiado. Lanza WatermarkError
    o RuntimeError si algo falla.
    """
    png = resolve_watermark_source(source, cache_dir=cache_dir)
    try:
        vw, vh = _probe_size(video_path)
    except Exception:
        vw, vh = 1080, 1920
    lw, lh = _probe_size(png)
    scale = size_px / max(lw, lh)
    dw = max(2, int(round(lw * scale)) // 2 * 2)
    dh = max(2, int(round(lh * scale)) // 2 * 2)
    x, y = watermark_xy(vw, vh, dw, dh, position)
    frac = _clamp(opacity, 0, 100, WATERMARK_DEFAULT_OPACITY) / 100.0
    filt = (f"[1:v]scale={dw}:{dh}:flags=lanczos,format=rgba,"
            f"colorchannelmixer=aa={frac:.3f}[wm];"
            f"[0:v][wm]overlay={x}:{y}:format=yuv420[vout]")
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-i", video_path, "-i", png,
           "-filter_complex", filt,
           "-map", "[vout]"]
    if _has_audio(video_path):
        cmd += ["-map", "0:a", "-c:a", "copy"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            output_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg falló aplicando la marca de agua "
                           f"en {output_path}:\n"
                           f"{r.stderr.strip()[-2000:]}")
    return output_path


def watermark_from_cfg(cfg):
    """Normaliza cfg["watermark"] -> dict o None.

    cfg["watermark"]: enabled (bool), url|source (ruta o URL),
    opacity (0-100, %; un float 0-1 se lee como fracción),
    size_px, position (una de WATERMARK_POSITIONS). Sin enabled
    o sin fuente resoluble -> None, salvo que exista el logo por
    defecto del repo (entonces enabled sin fuente usa ese logo).
    """
    raw = (cfg or {}).get("watermark") or {}
    if not isinstance(raw, dict):
        return None
    enabled = raw.get("enabled")
    if enabled is None:
        return None
    enabled = bool(enabled) if not isinstance(enabled, str) else \
        enabled.strip().lower() in ("1", "true", "sí", "si", "on")
    if not enabled:
        return None
    source = raw.get("url") or raw.get("source")
    if not source and os.path.isfile(DEFAULT_WATERMARK_PNG):
        source = DEFAULT_WATERMARK_PNG
    if not source:
        return None
    opacity = raw.get("opacity", WATERMARK_DEFAULT_OPACITY)
    if isinstance(opacity, float) and 0 < opacity <= 1:
        opacity = opacity * 100
    position = raw.get("position", WATERMARK_DEFAULT_POSITION)
    if position not in WATERMARK_POSITIONS:
        position = WATERMARK_DEFAULT_POSITION
    return {
        "enabled": True,
        "source": str(source),
        "opacity": int(_clamp(opacity, 0, 100,
                              WATERMARK_DEFAULT_OPACITY)),
        "size_px": int(_clamp(raw.get("size_px",
                                      WATERMARK_DEFAULT_SIZE_PX),
                              32, 1080, WATERMARK_DEFAULT_SIZE_PX)),
        "position": position,
    }


def maybe_apply_watermark(video_path, cfg, cache_dir=None):
    """Aplica la marca en el sitio si cfg la activa.

    Re-codifica el vídeo en un temporal y lo reemplaza (atómico).
    Devuelve True si se aplicó, False si está apagada, no hay
    fuente o falló (aviso por stderr, nunca excepción: un clip
    no se rompe por la marca de agua).
    """
    wm = watermark_from_cfg(cfg)
    if wm is None:
        return False
    tmp = video_path + ".wm.tmp.mp4"
    try:
        apply_watermark(video_path, tmp, wm["source"],
                        opacity=wm["opacity"], size_px=wm["size_px"],
                        position=wm["position"], cache_dir=cache_dir)
    except Exception as e:
        print(f"AVISO: marca de agua no aplicada ({e}): el clip "
              f"sigue sin marca", file=sys.stderr)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
    os.replace(tmp, video_path)
    print(f"marca de agua aplicada ({wm['position']}, "
          f"{wm['size_px']} px, {wm['opacity']}%): {video_path}")
    return True
