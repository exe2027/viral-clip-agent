#!/usr/bin/env python3
"""Plantilla RDJavi (GTA 6 / Vice City) — render de clips verticales 1080x1920.

Un solo pase de ffmpeg, fondo desenfocado PRE-RENDERIZADO (una imagen
fija por clip, cacheada) y subtitulos simples quemados en el mismo pase.
Objetivo: un clip de ~48 s en pocos minutos, no en horas.

Layout (reels de referencia Df4sy4bxlmr y Df4uyMVjDwq, decision del
usuario 2026-11-18: estilo RDJavi TOTAL, DJ Mariio olvidado):
  * Streamer ARRIBA centrado casi 1:1: 583x595 px (x=248, y=0),
    ~54% ancho, ~31% alto, sobre fondo desenfocado morado/magenta +
    gradiente atardecer Vice City (rosa-violeta-naranja).
  * Gameplay ABAJO: y=749..1786 (39%-93%), 1080x1037, recorte 1:1.
  * Barra negra abajo: y=1786..1920.
  * Cortes duros; camara fija con un punch-in (zoom ~1,18x) en la
    reaccion (por defecto 16-26 s del clip).
  * Subtitulos SIMPLES estilo RDJavi: una linea centrada, Noto Sans
    Condensed 42, blanca con contorno negro, sin fondo, sin karaoke,
    MarginV 200, en la parte baja del gameplay (ver make_simple_ass
    o un .ass ya hecho; se queman en el mismo pase).

Por que es rapido (vs el render viejo de 4 pases):
  * 1 solo pase: layout + punch-in + subtitulos + audio en una
    invocacion; sin renders por tramo ni concat de MP4 ni segundo
    pase de quemado.
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
        --start 0 --duration 48.01 --ass rdjavi/subs_simple.ass \\
        --out clips/clip_rdjavi_subs.mp4 [--bg-frame 20] [--fps 30]
        [--preset veryfast] [--punch-in 16 26] [--no-punch-in]
"""
import argparse
import os
import subprocess
import sys
import time

# Geometria fija de la plantilla (1080x1920).
W, H = 1080, 1920
CAM_W, CAM_H, CAM_X, CAM_Y = 583, 595, 248, 0
BG_H = 749          # fondo desenfocado: y 0..749
GAME_Y, GAME_H = 749, 1037   # gameplay: y 749..1786
GAME_CROP = "1440:1440:620:0"  # recorte 1:1 centrado (x=620 evita el HUD izq.)
DEFAULT_CAM_BOX = "561:365:0:0"  # caja PiP del streamer en el segmento
DEFAULT_CAM_SQUARE = "365:365:90:0"  # recorte cuadrado (cara centrada)
PUNCH_ZOOM = 1.18


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


def punch_crop(square, zoom):
    """Recorte cuadrado con zoom para el punch-in, centrado en la cara."""
    w, h, x, y = (int(v) for v in square.replace(":", "x").split("x"))
    nw = int(w / zoom)
    nh = int(h / zoom)
    nx = x + (w - nw) // 2
    ny = y + (h - nh) // 2
    return f"{nw}:{nh}:{nx}:{ny}"


def build_filter(cam_square, punch_in, duration, ass_path, fps,
                 game_crop=GAME_CROP):
    """Grafo de un solo pase: cam (con punch-in) + fondo fijo + juego."""
    p0, p1 = punch_in if punch_in else (None, None)
    parts = []
    # fps=30 PRIMERO: todo el grafo trabaja a 30 fps (antes el fps
    # estaba al final y el grafo entero corria a 60 fps: el doble
    # de trabajo y de tamano).
    if p0 is not None:
        zc = punch_crop(cam_square, PUNCH_ZOOM)
        parts.append(
            f"[0:v]fps={fps},split=2[cam_src][game_src];"
            f"[cam_src]split=3[ca][cb][cc];"
            f"[ca]trim=start=0:end={p0},setpts=PTS-STARTPTS,"
            f"crop={cam_square},scale={CAM_W}:{CAM_H},setsar=1[ca2];"
            f"[cb]trim=start={p0}:end={p1},setpts=PTS-STARTPTS,"
            f"crop={zc},scale={CAM_W}:{CAM_H},setsar=1[cb2];"
            f"[cc]trim=start={p1}:end={duration},setpts=PTS-STARTPTS,"
            f"crop={cam_square},scale={CAM_W}:{CAM_H},setsar=1[cc2];"
            f"[ca2][cb2][cc2]concat=n=3:v=1:a=0[cam];")
    else:
        parts.append(
            f"[0:v]fps={fps},split=2[cam_src][game_src];"
            f"[cam_src]crop={cam_square},scale={CAM_W}:{CAM_H},setsar=1[cam];")
    parts.append(
        f"[game_src]crop={game_crop},scale={W}:{GAME_H},setsar=1[game];"
        f"color=c=black:s={W}x{H}:r={fps}[base];"
        f"[base][1:v]overlay=0:0[step1];"
        f"[step1][cam]overlay={CAM_X}:{CAM_Y}[step2];"
        f"[step2][game]overlay=0:{GAME_Y}[vout];")
    if ass_path:
        parts.append(f"[vout]ass={ass_path}[vsub]")
        vlabel = "[vsub]"
    else:
        vlabel = "[vout]"
    return "".join(parts), vlabel


def render(segment, start, duration, ass_path, out_path, bg_png,
           cam_square=DEFAULT_CAM_SQUARE, punch_in=(16, 26),
           fps=30, preset="veryfast", crf=23, game_crop=GAME_CROP):
    """Render RDJavi en un solo pase. Devuelve los segundos que tardo."""
    filt, vlabel = build_filter(cam_square, punch_in, duration, ass_path,
                                fps, game_crop)
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-ss", str(start), "-t", str(duration), "-i", segment,
           "-loop", "1", "-t", str(duration), "-i", bg_png,
           "-filter_complex", filt,
           "-map", vlabel, "-map", "0:a",
           "-c:a", "copy",
           "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
    return _run(cmd, "render RDJavi")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--segment", required=True)
    p.add_argument("--start", type=float, default=0)
    p.add_argument("--duration", type=float, required=True)
    p.add_argument("--ass", default=None, help="ASS de subtitulos simples")
    p.add_argument("--out", required=True)
    p.add_argument("--bg", default=None, help="PNG de fondo ya hecho")
    p.add_argument("--gradient", default=None, help="PNG gradiente Vice City")
    p.add_argument("--bg-frame", type=float, default=20,
                   help="Segundo del clip para el fondo (default 20)")
    p.add_argument("--cam-box", default=DEFAULT_CAM_BOX)
    p.add_argument("--cam-square", default=DEFAULT_CAM_SQUARE)
    p.add_argument("--game-crop", default=GAME_CROP,
                   help="Recorte 1:1 del gameplay (default 1440:1440:620:0 "
                        "para segmentos 2560x1440; 1080:1080:465:0 si el "
                        "segmento se pre-transcodifico a 1920x1080)")
    p.add_argument("--punch-in", type=float, nargs=2, default=[16, 26])
    p.add_argument("--no-punch-in", action="store_true")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--preset", default="veryfast")
    p.add_argument("--crf", type=int, default=23)
    args = p.parse_args(argv)

    workdir = os.path.dirname(os.path.abspath(args.out))
    bg = args.bg
    if not bg:
        if not args.gradient:
            raise SystemExit("--bg o --gradient para hacer el fondo")
        bg = os.path.join(workdir, "bg.png")
        t0 = time.time()
        make_bg_still(args.segment, args.start + args.bg_frame,
                      args.cam_box, args.gradient, bg)
        print(f"fondo pre-renderizado: {time.time()-t0:.1f} s -> {bg}")
    punch = None if args.no_punch_in else tuple(args.punch_in)
    t0 = time.time()
    dt = render(args.segment, args.start, args.duration, args.ass,
                args.out, bg, cam_square=args.cam_square, punch_in=punch,
                fps=args.fps, preset=args.preset, crf=args.crf,
                game_crop=args.game_crop)
    print(f"render RDJavi: {dt:.1f} s ({args.duration:.1f} s de clip, "
          f"{args.duration/dt:.2f}x) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
