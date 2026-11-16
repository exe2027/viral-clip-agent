#!/usr/bin/env python3
"""
Twitch Auto Clips v2 — clips_chat_v2.py

Detecta los mejores momentos de un directo de Twitch a partir del chat
(chat.json de TwitchDownloader) y los recorta en vertical 1080x1920,
listos para TikTok, Reels o Shorts.

Qué cambia respecto a la v1:
  * Español + inglés: la v1 solo puntuaba risas en inglés
    (lol, lmao, kekw, lul, xd...). La v2 suma risas en español
    (jaja, jeje, jiji, jojo, qué risa, me muero, me parto...)
    y frases de hype en ambos idiomas (qué jugada, brutal, pog, clutch...).
  * Anti falsos positivos: los tokens cortos (xd, lol, gg, w) solo
    cuentan como palabra completa, no dentro de otra palabra.
  * Todo configurable por línea de comandos: nº de clips, ventana,
    separación, pre-roll, duración, idioma, recorte y calidad.
  * Más robusto: valida archivos, FFmpeg/ffprobe, no se cae si un
    clip falla, ajusta el último clip si el VOD es más corto.
  * --dry-run: lista los picos sin cortar nada, para revisar antes.

Uso:
  TwitchDownloaderCLI chatdownload --id <vod-id> -o chat.json -E
  TwitchDownloaderCLI videodownload --id <vod-id> -o vod.mp4

  python clips_chat_v2.py chat.json --video vod.mp4 --out clips
  python clips_chat_v2.py chat.json --video vod.mp4 --dry-run
  python clips_chat_v2.py chat.json --video vod.mp4 --top-n 3 --duration 30 --lang es

Requisitos: Python 3.10+, TwitchDownloaderCLI y FFmpeg en el PATH.
Sin dependencias externas, sin credenciales, sin red.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict

# ---------------------------------------------------------------------------
# Tokens — español e inglés
# RISA = +1 extra, HYPE = +1 extra, mensaje normal = 1 punto.
# Un mensaje con risa + hype vale 3. Así un "JAJAJA QUÉ JUGADA" pesa más
# que un simple "hola".
# ---------------------------------------------------------------------------

# Risas en español: jaja, jeje, jiji, jojo, jajaja, jejeje...
# El patrón regex de abajo caza también las variantes alargadas
# (jaaaajaja, jejejeje, jijiji...) que la lista no podría enumerar.
LAUGH_ES = (
    "jaja", "jeje", "jiji", "jojo", "juju",
    "jajaja", "jejeje", "jijiji", "jojojo",
    "qué risa", "que risa", "qué risaa",
    "me muero", "me muero de risa", "me parto", "me parto de risa",
    "no puedo", "no puedo más", "me cago de risa", "qué bueno",
    "😂", "🤣", "😹", "💀", "😆",
)
LAUGH_EN = (
    "lol", "lmao", "rofl", "lmfao",
    "haha", "hehe", "hahaha", "hehehe",
    "kekw", "lul", "lulw", "omegalul",
    "😂", "🤣", "😹", "💀", "😆",
)
# Patrón para risas alargadas / repetidas: jajaja, jejeje, jaajaa, jiji...
LAUGH_REPEATED_RE = re.compile(r"\b(?:j[aeiou]){2,}[aeiou]*\b|\b(?:ha){2,}\b|\b(?:he){2,}\b")

# Hype / jugada buena — en español e inglés. Esto es lo que la v1 no tenía:
# un pico de "QUÉ JUGADA / POG / CLUTCH" no es risa, pero es clip.
HYPE_ES = (
    "qué jugada", "que jugada", "qué grande", "que grande",
    "brutal", "increíble", "increible", "insano", "tremendo",
    "épico", "epico", "no lo puedo creer", "no te creo",
    "vamos", "dale", "vamooos", "dalee",
    "qué gol", "que gol", "golazo", "qué pedazo", "qué locura", "que locura",
    "noooo", "nooo", "madre mía", "madre mia", "dios mío", "dios mio",
    "impresionante", "espectacular", "una locura",
)
HYPE_EN = (
    "pog", "pogchamp", "poggers", "hype", "insane", "crazy",
    "clutch", "god", "lets go", "let's go", "gg", "wtf", "omg",
    "wow", "what a play", "no way", "unreal", "clip it", "clipit",
    "w", "w in the chat",
)

# Tokens cortos que exigen palabra completa: sin esto, "xd" contaría
# dentro de "extra" y "lol" dentro de "lollipop". Se chequean con \b.
SHORT_TOKENS = {"xd", "lol", "gg", "w", "lul", "kekw", "pog"}


def _contains_token(text: str, token: str) -> bool:
    """True si el token aparece como palabra/frase, no dentro de otra."""
    if token in SHORT_TOKENS:
        return re.search(r"\b" + re.escape(token) + r"\b", text) is not None
    # Frases y tokens largos: substring, pero con límites de palabra
    # cuando el token es una sola palabra.
    if " " in token or token in ("😂", "🤣", "😹", "💀", "😆"):
        return token in text
    return re.search(r"\b" + re.escape(token) + r"\b", text) is not None


def score_message(text: str, lang: str = "both") -> tuple[int, bool, bool]:
    """Devuelve (puntos, es_risa, es_hype) para un mensaje del chat."""
    lowered = text.lower()
    is_laugh = False
    is_hype = False

    if lang in ("es", "both"):
        if any(_contains_token(lowered, t) for t in LAUGH_ES):
            is_laugh = True
        if any(_contains_token(lowered, t) for t in HYPE_ES):
            is_hype = True
    if lang in ("en", "both"):
        if any(_contains_token(lowered, t) for t in LAUGH_EN):
            is_laugh = True
        if any(_contains_token(lowered, t) for t in HYPE_EN):
            is_hype = True

    # Risas alargadas que no están en la lista: jajajaja, jejejeje, hahaha...
    if not is_laugh and LAUGH_REPEATED_RE.search(lowered):
        is_laugh = True

    points = 1 + (1 if is_laugh else 0) + (1 if is_hype else 0)
    return points, is_laugh, is_hype


def score_windows(chat_path: str, window_size: int = 10, lang: str = "both"):
    """Agrupa el chat en ventanas y devuelve puntaje + detalle por ventana."""
    with open(chat_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    comments = data.get("comments", [])
    scores: dict[int, int] = defaultdict(int)
    detail: dict[int, dict] = defaultdict(lambda: {"msgs": 0, "risas": 0, "hype": 0})

    for comment in comments:
        offset = comment.get("content_offset_seconds")
        if offset is None:
            continue
        body = comment.get("message", {}).get("body", "") or ""
        window_start = (int(offset) // window_size) * window_size
        points, is_laugh, is_hype = score_message(body, lang=lang)
        scores[window_start] += points
        detail[window_start]["msgs"] += 1
        if is_laugh:
            detail[window_start]["risas"] += 1
        if is_hype:
            detail[window_start]["hype"] += 1

    return scores, detail


def select_top_peaks(scores, top_n: int = 5, min_separation: int = 60):
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    selected: list[tuple[int, int]] = []
    for timestamp, score in ranked:
        if all(abs(timestamp - ts) >= min_separation for ts, _ in selected):
            selected.append((timestamp, score))
            if len(selected) == top_n:
                break
    return sorted(selected)  # en orden cronológico para cortar


def get_video_duration(video_path: str):
    """Duración en segundos con ffprobe, o None si no se puede saber."""
    if shutil.which("ffprobe") is None:
        return None
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, check=True,
        )
        return float(r.stdout.strip())
    except Exception:
        return None


def fmt_ts(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}h{m:02d}m{s:02d}s" if h else f"{m:02d}m{s:02d}s"


def cut_vertical_clip(video_path, start_sec, duration, output_path,
                      width=1080, height=1920, crf=23, preset="fast",
                      crop="center"):
    # Recorte vertical: center (defecto), left o right.
    # En GTA el minimapa suele estar a un lado: si el recorte centrado
    # te lo corta, prueba --crop left o --crop right.
    x_expr = {"center": "(iw-ih*9/16)/2", "left": "0",
              "right": "iw-ih*9/16"}.get(crop, "(iw-ih*9/16)/2")
    cmd = [
        "ffmpeg", "-y", "-ss", str(start_sec), "-i", video_path,
        "-t", str(duration),
        "-vf", f"crop=ih*9/16:ih:x={x_expr},scale={width}:{height}",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-c:a", "aac", "-b:a", "128k", output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Extrae los mejores clips verticales desde un chat.json y vod.mp4 de Twitch (v2: español + inglés)."
    )
    p.add_argument("chat", help="Ruta al chat.json de TwitchDownloader")
    p.add_argument("--video", help="Ruta al vod.mp4 (obligatorio salvo --dry-run)")
    p.add_argument("--out", default="clips", help="Carpeta de salida (default: clips)")
    p.add_argument("--top-n", type=int, default=5, help="Nº de clips (default: 5)")
    p.add_argument("--window", type=int, default=10, help="Ventana en segundos (default: 10)")
    p.add_argument("--min-separation", type=int, default=60, help="Separación mínima entre picos (default: 60)")
    p.add_argument("--pre-roll", type=int, default=20, help="Segundos antes del pico (default: 20)")
    p.add_argument("--duration", type=int, default=60, help="Duración de cada clip (default: 60)")
    p.add_argument("--lang", choices=["es", "en", "both"], default="both",
                   help="Idioma a puntuar: es, en o both (default: both)")
    p.add_argument("--crop", choices=["center", "left", "right"], default="center",
                   help="Posición del recorte vertical (default: center)")
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1920)
    p.add_argument("--crf", type=int, default=23, help="Calidad x264, menor = mejor (default: 23)")
    p.add_argument("--preset", default="fast", help="Preset x264 (default: fast)")
    p.add_argument("--dry-run", action="store_true", help="Solo lista los picos, sin cortar")
    args = p.parse_args()

    if not os.path.isfile(args.chat):
        print(f"Error: no existe el chat: {args.chat}", file=sys.stderr)
        return 1
    if not args.dry_run:
        if not args.video:
            print("Error: falta --video (o usa --dry-run para solo listar picos).", file=sys.stderr)
            return 1
        if not os.path.isfile(args.video):
            print(f"Error: no existe el video: {args.video}", file=sys.stderr)
            return 1
        if shutil.which("ffmpeg") is None:
            print("Error: FFmpeg no está en el PATH.", file=sys.stderr)
            return 1

    try:
        scores, detail = score_windows(args.chat, window_size=args.window, lang=args.lang)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error: chat.json no válido: {e}", file=sys.stderr)
        return 1

    if not scores:
        print("No se encontraron mensajes con marcas de tiempo en el chat.")
        return 0

    peaks = select_top_peaks(scores, top_n=args.top_n, min_separation=args.min_separation)
    if not peaks:
        print("No se encontraron picos.")
        return 0

    total_msgs = sum(d["msgs"] for d in detail.values())
    print(f"Chat: {total_msgs} mensajes, {len(scores)} ventanas de {args.window}s, idioma: {args.lang}")
    print("Picos elegidos:")
    for i, (ts, score) in enumerate(peaks, 1):
        d = detail[ts]
        print(f"  {i}. {fmt_ts(ts)} — {score} pts ({d['msgs']} msgs, "
              f"{d['risas']} risas, {d['hype']} hype)")

    if args.dry_run:
        print("Dry-run: no se cortó nada.")
        return 0

    duration = get_video_duration(args.video)
    if duration:
        print(f"VOD: {fmt_ts(int(duration))} ({duration:.0f}s)")

    os.makedirs(args.out, exist_ok=True)
    ok, failed = 0, 0
    for idx, (peak_ts, score) in enumerate(peaks, 1):
        clip_start = max(0, peak_ts - args.pre_roll)
        clip_dur = args.duration
        if duration and clip_start + clip_dur > duration:
            clip_dur = max(1, int(duration - clip_start))
            if clip_dur <= 0:
                print(f"[{idx}] Pico {fmt_ts(peak_ts)} fuera del VOD, se salta.")
                continue
        d = detail[peak_ts]
        out_file = os.path.join(
            args.out, f"clip_{idx:02d}_t{fmt_ts(clip_start)}_s{score}_r{d['risas']}h{d['hype']}.mp4"
        )
        print(f"[{idx}/{len(peaks)}] Pico {fmt_ts(peak_ts)} ({score} pts) -> "
              f"{clip_dur}s desde {fmt_ts(clip_start)} en {out_file}")
        try:
            cut_vertical_clip(args.video, clip_start, clip_dur, out_file,
                              width=args.width, height=args.height,
                              crf=args.crf, preset=args.preset, crop=args.crop)
            ok += 1
        except subprocess.CalledProcessError as e:
            failed += 1
            print(f"[{idx}] Falló el corte: {e.stderr.strip().splitlines()[-1] if e.stderr else e}", file=sys.stderr)

    print(f"Listo: {ok} clips en {args.out}/" + (f", {failed} fallidos." if failed else ""))
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
