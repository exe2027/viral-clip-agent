#!/usr/bin/env python3
"""Driver del job ``render`` de CircleCI: de una URL a un MP4 rubius validado.

Pasos (sin publicar, sin D1, sin panel, sin re-renders en bucle):
  1. Descarga la fuente con yt-dlp (YouTube/Instagram/Twitch) y
     corta el tramo [start_sec, end_sec] con ffmpeg. Si la fuente
     ya es un MP4 directo, se descarga y se corta igual.
  2. Detecta la caja de la webcam con render.decide_layout (o usa
     --cam-box/--cam-square explícitos). Sin cámara detectable y
     sin caja explícita, FALLA FUERTE: la plantilla rubius exige
     cámara arriba y juego abajo; un juego sin cámara no se
     renderiza a rubius en silencio.
  3. Transcribe el tramo con faster-whisper (tiny, local) -> SRT
     (+ .transcript.json para la decisión SFX). O usa --srt.
  4. Renderiza con rubius.render (banner amarillo, subtítulos
     palabra por palabra, punch-in, zoom del gameplay, shake,
     impacto, fundido, banner deslizante + SFX boom/riser/whoosh)
     y valida el MP4 final con render_gate (cuadro por cuadro).
  5. Escribe el MP4 final + un JSON de resultado en --out-dir.
     Si el gate no pasa, sale con código 1 y NO se publica nada:
     el artefacto solo se guarda cuando el gate pasa (lo decide
     el job de CircleCI mirando el código de salida).

Sin secretos: las credenciales que necesite (Gemini, panel) se
materializan desde el panel como hace el piloto de validación;
este driver no lee ni escribe secretos, solo la fuente pública.

Uso (lo que corre el job de CircleCI):
    python -m clipagent.circle_render \
        --source-url https://www.youtube.com/watch?v=XXXX \
        --streamer Rubius --title 'GTA 6: Rubius descubre...' \
        --start 223 --end 281 --lang es --out-dir out/
"""

import argparse
import json
import os
import subprocess
import sys


def _run(cmd, what):
    """Corre un comando; si falla, levanta con el comando visible."""
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        tail = (res.stderr or res.stdout or "")[-2000:]
        raise RuntimeError(f"{what} falló ({res.returncode}): {tail}")
    return res


def _probe_size(video_path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0",
         str(video_path)],
        capture_output=True, text=True, timeout=30)
    try:
        w, h = (out.stdout or "").strip().split(",")
        return int(w), int(h)
    except ValueError:
        raise RuntimeError(f"sin dimensiones medibles: {video_path}")


def _probe_duration(video_path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True, timeout=30)
    try:
        return float((out.stdout or "").strip())
    except ValueError:
        raise RuntimeError(f"sin duración medible: {video_path}")


def download_source(source_url, start, end, workdir):
    """Descarga la fuente y corta el tramo [start, end].

    Devuelve la ruta del segmento. yt-dlp con --download-sections
    (solo el tramo, no el VOD entero) y --compat-options no-certifi
    (regla del proyecto: nunca --no-check-certificates). Si la URL
    es un MP4 directo, se baja con curl y se corta con ffmpeg.
    """
    os.makedirs(workdir, exist_ok=True)
    segment = os.path.join(workdir, "segment.mp4")
    if source_url.lower().split("?")[0].endswith(".mp4"):
        tmp = os.path.join(workdir, "source.mp4")
        _run(["curl", "-fsSL", "-o", tmp, source_url],
             "descarga del MP4 directo")
        _cut(tmp, start, end, segment)
        return segment
    fmt = "bv*[height<=1080]+ba/b[height<=1080]/b"
    _run([
        "yt-dlp", "--compat-options", "no-certifi",
        "--no-warnings", "--no-progress",
        "-f", fmt,
        "--download-sections", f"*{start}-{end}",
        "--force-keyframes-at-cuts",
        "-o", segment, source_url,
    ], "descarga con yt-dlp")
    if not os.path.isfile(segment) or os.path.getsize(segment) == 0:
        raise RuntimeError("yt-dlp no dejó el segmento "
                           f"(¿URL válida? {source_url})")
    return segment


def _cut(src, start, end, out):
    dur = end - start
    _run(["ffmpeg", "-y", "-v", "error", "-ss", str(start),
          "-t", str(dur), "-i", src, "-c", "copy", out],
         "corte del tramo")


def detect_cam_box(segment, duration, cfg=None):
    """Caja de la webcam en px del vídeo original.

    Devuelve (cam_box, cam_square) en formato "w:h:x:y" para
    rubius.py. La detección es render.decide_layout sobre el
    segmento: el primer tramo split trae params["box"] en coords
    de muestra (160x90) y se escala al tamaño real. El cuadrado
    es el box hecho cuadrado centrado (para centrar el punch-in
    en la cara). Lanza RuntimeError si no hay cámara: sin cámara
    no hay render rubius (falla fuerte, nunca default silencioso).
    """
    from .render import SAMPLE_H, SAMPLE_W, decide_layout

    sw, sh = _probe_size(segment)
    decision = decide_layout(segment, duration, cfg=cfg)
    box_s = None
    for seg in decision.get("segments", []):
        if seg.get("layout") == "split" and seg.get("params", {}).get("box"):
            box_s = seg["params"]["box"]
            break
    if not box_s:
        raise RuntimeError(
            "sin cámara detectable en el tramo (layouts: "
            + ", ".join(
                f"{s.get('layout')}" for s in decision.get("segments", []))
            + "): la plantilla rubius exige cámara arriba y juego "
              "abajo; pasa --cam-box/--cam-square explícitos o usa "
              "otra fuente con webcam")
    bx, by, bw, bh = [float(v) for v in box_s[:4]]
    x = bx * sw / SAMPLE_W
    y = by * sh / SAMPLE_H
    w = bw * sw / SAMPLE_W
    h = bh * sh / SAMPLE_H
    cam_box = f"{int(round(w))}:{int(round(h))}:{int(round(x))}:{int(round(y))}"
    side = min(w, h)
    sx = x + (w - side) / 2
    sy = y + (h - side) / 2
    cam_square = (f"{int(round(side))}:{int(round(side))}:"
                  f"{int(round(sx))}:{int(round(sy))}")
    return cam_box, cam_square


def transcribe_segment(segment, lang, workdir, srt_path=None,
                       model="tiny"):
    """Transcribe el tramo con faster-whisper -> SRT + JSON.

    Devuelve (srt_path, transcript_path). Usa el CLI de
    transcribe.py (--no-burn: solo genera el SRT, sin quemarlo).
    """
    srt_path = srt_path or os.path.join(workdir, "segment.srt")
    transcript_path = os.path.join(workdir, "segment.transcript.json")
    _run([
        sys.executable, "-m", "clipagent.transcribe", segment,
        "--srt", srt_path, "--transcript", transcript_path,
        "--no-burn", "--lang", lang, "--model", model,
    ], "transcripción con faster-whisper")
    if not os.path.isfile(srt_path) or os.path.getsize(srt_path) == 0:
        raise RuntimeError("la transcripción no dejó SRT "
                           "(¿hay voz en el tramo?)")
    return srt_path, transcript_path


def circle_render(source_url, streamer, title, start, end, lang,
                  out_dir, config_path=None, cam_box=None,
                  cam_square=None, punch_zoom=None, shake=True,
                  shake_amp=None, impact=True, banner_slide=True,
                  fade_in=True, whoosh=True, payoff=None,
                  model="tiny", srt_path=None):
    """Render completo: descarga -> corta -> transcribe -> rubius -> gate.

    Devuelve el dict de resultado (también se escribe como
    result.json en out_dir). Lanza RuntimeError ante cualquier
    fallo de entrada o de render; si el GATE no pasa, devuelve
    el resultado con ok=False (el llamador decide el código de
    salida: el job de CircleCI falla en ese caso).
    """
    from .config import load_config
    from .render_gate import render_gate
    from .rubius import PUNCH_ZOOM_STRONG, SHAKE_AMP_PX, render

    start = float(start)
    end = float(end)
    if end <= start:
        raise RuntimeError(f"end ({end}) debe ser mayor que start ({start})")
    if end - start > 60:
        raise RuntimeError(f"el tramo dura {end - start:.1f} s: el tope "
                           "es 60 s por clip")
    os.makedirs(out_dir, exist_ok=True)
    workdir = os.path.join(out_dir, "work")
    os.makedirs(workdir, exist_ok=True)

    cfg = load_config(config_path) if config_path else load_config()
    duration = end - start

    segment = download_source(source_url, start, end, workdir)
    sw, sh = _probe_size(segment)
    if sh > sw:
        raise RuntimeError(
            f"la fuente es vertical ({sw}x{sh}): el render rubius "
            "parte de un VOD 16:9 con la webcam en un rincón; usa "
            "una URL de directo/VOD horizontal")
    # El recorte 1:1 del gameplay depende del tamaño real de la
    # fuente (la receta 1440:1440:560:0 es para 2560x1440; la
    # descarga se capa a 1080p, así que ahí es 1080:1080:420:0).
    game_crop = f"{sh}:{sh}:{(sw - sh) // 2}:0"
    if cam_box is None or cam_square is None:
        auto_box, auto_square = detect_cam_box(segment, duration, cfg)
        cam_box = cam_box or auto_box
        cam_square = cam_square or auto_square
    print(f"cam-box {cam_box} / cam-square {cam_square}")

    if srt_path:
        transcript_path = None
    else:
        srt_path, transcript_path = transcribe_segment(
            segment, lang, workdir, model=model)
    print(f"SRT: {srt_path}")

    if payoff is None:
        payoff = 20.0 if duration >= 24 else max(4.0, duration - 4.0)
    punch_zoom = PUNCH_ZOOM_STRONG if punch_zoom is None else punch_zoom
    shake_amp = SHAKE_AMP_PX if shake_amp is None else shake_amp
    game_zoom = (payoff - 2.0, payoff + 4.0) if duration >= payoff + 4.0 else None
    # El banner se genera aquí (render() recibe el PNG ya hecho) y
    # el ASS palabra por palabra se genera desde el SRT (render()
    # recibe un ASS, no un SRT: libass no lee SRT).
    from .rubius import BANNER_SHOW, make_banner_png, make_word_ass
    from .transcribe import parse_srt
    banner_path = os.path.join(workdir, "banner_rubius.png")
    make_banner_png(title, banner_path)
    blocks = parse_srt(srt_path)
    ass_text = make_word_ass(blocks, max_end=duration,
                             banner_show=BANNER_SHOW)
    ass_path = os.path.join(workdir, "subs_rubius.ass")
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_text)
    print(f"ASS rubius: {ass_text.count('Dialogue:')} palabras -> {ass_path}")
    out_mp4 = os.path.join(out_dir, "clip_rubius.mp4")
    _dt, final = render(
        segment, 0, duration, ass_path, out_mp4,
        banner_png=banner_path, cam_box=cam_box, cam_square=cam_square,
        punch_in=(16.0, 26.0) if duration >= 26 else None,
        game_crop=game_crop,
        game_zoom=game_zoom,
        cfg=cfg, sfx_transcript=transcript_path, sfx_srt=srt_path,
        payoff_s=float(payoff), punch_zoom=float(punch_zoom),
        shake=shake, shake_amp=float(shake_amp), impact=impact,
        banner_slide=banner_slide, fade_in=fade_in, whoosh=whoosh,
    )
    gate = render_gate(final, srt_path=srt_path, cfg=cfg,
                       out_dir=os.path.join(out_dir, "gate"))
    print(f"gate rubius: {'PASA' if gate['pasa'] else 'NO PASA'}"
          + (f" -- {gate['motivo']}" if gate["motivo"] else ""))
    return {"ok": bool(gate["pasa"]), "out_mp4": final,
            "duration": duration, "cam_box": cam_box,
            "cam_square": cam_square, "payoff_s": float(payoff),
            "gate": gate}


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Render rubius desde una URL (driver CircleCI).")
    p.add_argument("--source-url", required=True,
                   help="URL del vídeo fuente (YouTube/Instagram/Twitch o MP4 directo)")
    p.add_argument("--streamer", required=True)
    p.add_argument("--title", required=True,
                   help="Título del banner amarillo")
    p.add_argument("--start", type=float, required=True)
    p.add_argument("--end", type=float, required=True)
    p.add_argument("--lang", default="es", choices=["es", "en"])
    p.add_argument("--out-dir", required=True)
    p.add_argument("--config", default=None)
    p.add_argument("--srt", default=None,
                   help="SRT ya hecho (si no se pasa, se transcribe)")
    p.add_argument("--cam-box", default=None)
    p.add_argument("--cam-square", default=None)
    p.add_argument("--punch-zoom", type=float, default=None,
                   help="Zoom del punch-in (default: 1,30 marcado)")
    p.add_argument("--no-shake", action="store_true")
    p.add_argument("--shake-amp", type=float, default=None)
    p.add_argument("--no-impact", action="store_true")
    p.add_argument("--no-banner-slide", action="store_true")
    p.add_argument("--no-fade-in", action="store_true")
    p.add_argument("--no-whoosh", action="store_true")
    p.add_argument("--payoff", type=float, default=None,
                   help="Segundo del momento fuerte (default: 20 o duración-4)")
    p.add_argument("--model", default="tiny",
                   help="Modelo faster-whisper (default: tiny)")
    args = p.parse_args(argv)

    try:
        res = circle_render(
            args.source_url, args.streamer, args.title,
            args.start, args.end, args.lang, args.out_dir,
            config_path=args.config, cam_box=args.cam_box,
            cam_square=args.cam_square, punch_zoom=args.punch_zoom,
            shake=not args.no_shake, shake_amp=args.shake_amp,
            impact=not args.no_impact,
            banner_slide=not args.no_banner_slide,
            fade_in=not args.no_fade_in, whoosh=not args.no_whoosh,
            payoff=args.payoff, model=args.model, srt_path=args.srt,
        )
    except RuntimeError as exc:
        print(f"circle_render: ERROR: {exc}", file=sys.stderr)
        return 2
    result_path = os.path.join(args.out_dir, "result.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
