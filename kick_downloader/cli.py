"""KickDownloaderCLI — descarga VODs de Kick: chat, video y metadatos.

Interfaz en espíritu de TwitchDownloaderCLI (lay295/TwitchDownloader):

    KickDownloaderCLI chatdownload --id <id-o-URL> -o chat.json
    KickDownloaderCLI videodownload --id <id-o-URL> -o video.mp4 -b 3600s -e 3660s
    KickDownloaderCLI info --id <id-o-URL>

Solo stdlib + ffmpeg externo. Sin credenciales, sin llaves, sin pago.
"""

import argparse
import json
import os
import re
import sys

from kick_downloader import __version__
from kick_downloader.core import (
    KickError,
    download_chat,
    download_segment,
    parse_kick_url,
    resolve_vod,
)


def parse_time(value: str) -> float:
    """Acepta segundos (3600), 3600s, 60m, 2h, 1:02:03 y 01:02:03."""
    value = value.strip()
    if re.fullmatch(r"\d+(\.\d+)?", value):
        return float(value)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)", value)
    if m:
        num, unit = float(m.group(1)), m.group(2)
        return num / 1000 if unit == "ms" else num * {"s": 1, "m": 60, "h": 3600}[unit]
    parts = value.split(":")
    if len(parts) in (2, 3) and all(p.isdigit() for p in parts):
        nums = [int(p) for p in parts]
        if len(nums) == 2:
            minutes, seconds = nums
            return minutes * 60 + seconds
        hours, minutes, seconds = nums
        return hours * 3600 + minutes * 60 + seconds
    raise argparse.ArgumentTypeError(
        f"tiempo no reconocido: {value!r} (usa 3600, 3600s, 60m, 2h o 1:02:03)")


def parse_quality(value: str) -> int:
    """720p60 / 1080p / best / 480p -> altura máxima en píxeles."""
    value = value.strip().lower()
    if value in ("best", "max", "source"):
        return 1080
    m = re.fullmatch(r"(\d+)p\d*", value)
    if m:
        return int(m.group(1))
    raise argparse.ArgumentTypeError(
        f"calidad no reconocida: {value!r} (usa 360p, 480p, 720p, 720p60, 1080p o best)")


def resolve_id(id_or_url: str):
    """Acepta la URL completa o solo el UUID; devuelve (canal, uuid)."""
    if "kick.com" in id_or_url:
        return parse_kick_url(id_or_url)
    if re.fullmatch(r"[0-9a-f-]{36}", id_or_url):
        raise KickError(
            "Con solo el UUID no sé el canal. Pasa la URL completa: "
            "https://kick.com/<canal>/videos/<uuid>")
    raise KickError(
        f"No reconozco el ID de Kick: {id_or_url!r}. "
        "Usa https://kick.com/<canal>/videos/<uuid>")


def handle_collision(path: str, collision: str) -> str:
    """Devuelve la ruta a escribir según --collision."""
    if not os.path.isfile(path) or collision == "Overwrite":
        return path
    if collision == "Exit":
        raise KickError(f"El archivo ya existe: {path} (--collision Exit)")
    if collision == "Rename":
        base, ext = os.path.splitext(path)
        i = 1
        while os.path.isfile(f"{base}_{i}{ext}"):
            i += 1
        return f"{base}_{i}{ext}"
    raise KickError(f"--collision no reconocido: {collision!r} "
                    "(usa Overwrite, Exit o Rename)")


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def cmd_info(args) -> int:
    slug, uuid = resolve_id(args.id)
    meta = resolve_vod(slug, uuid)
    if args.json:
        out = dict(meta)
        out["start_time"] = meta["start_time"].isoformat() if meta["start_time"] else None
        out["duration_hms"] = fmt_duration(meta["duration_s"])
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"Canal:    {meta['channel']} (id {meta['channel_id']})")
    print(f"Título:   {meta['title']}")
    print(f"Duración: {fmt_duration(meta['duration_s'])} ({int(meta['duration_s'])} s)")
    print(f"Fecha:    {meta['start_time']}")
    print(f"Views:    {meta['views']}")
    print(f"Categoría:{meta['category'] or '-'} | Idioma: {meta['language'] or '-'}")
    print(f"VOD:      https://kick.com/{meta['channel']}/videos/{meta['url_uuid']}")
    print(f"m3u8:     {meta['source']}")
    return 0


def cmd_chatdownload(args) -> int:
    slug, uuid = resolve_id(args.id)
    meta = resolve_vod(slug, uuid)
    print(f"Kick: {meta['channel']} — {meta['title'][:80]}")
    print(f"  VOD: {fmt_duration(meta['duration_s'])} | {meta['views']} views")
    out = handle_collision(args.output, args.collision)
    out_dir = os.path.dirname(out) or "."
    os.makedirs(out_dir, exist_ok=True)
    n, _ = download_chat(
        meta, out,
        out_raw_ndjson=os.path.join(out_dir, "chat_kick_raw.ndjson"),
        workers=args.workers)
    print(f"Chat: {n} mensajes en {out}")
    return 0


def cmd_videodownload(args) -> int:
    slug, uuid = resolve_id(args.id)
    meta = resolve_vod(slug, uuid)
    if not meta["source"]:
        raise KickError("Este VOD no trae URL de video (source vacío).")
    print(f"Kick: {meta['channel']} — {meta['title'][:80]}")
    print(f"  VOD: {fmt_duration(meta['duration_s'])} | {meta['views']} views")
    start = parse_time(args.beginning) if args.beginning else 0.0
    end = parse_time(args.ending) if args.ending else float(meta["duration_s"])
    if end <= start:
        raise KickError(f"--ending ({end}s) tiene que ser mayor que --beginning ({start}s).")
    duration = end - start
    print(f"  Segmento: {fmt_duration(start)} → {fmt_duration(end)} "
          f"({int(duration)} s) a {args.quality}")
    out = handle_collision(args.output, args.collision)
    max_height = parse_quality(args.quality)
    download_segment(meta["source"], start, duration, out, max_height=max_height,
                     ffmpeg_path=args.ffmpeg_path)
    print(f"Video: {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="KickDownloaderCLI",
        description="Descarga VODs de Kick: chat, video y metadatos. "
                    "Sin credenciales, sin llaves. Solo stdlib + ffmpeg.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    pi = sub.add_parser("info", help="Metadatos del VOD (título, canal, duración, views, m3u8).")
    pi.add_argument("-u", "--id", required=True, help="URL kick.com/<canal>/videos/<uuid>")
    pi.add_argument("--json", action="store_true", help="Salida en JSON.")
    pi.set_defaults(func=cmd_info)

    pc = sub.add_parser("chatdownload", help="Descarga el chat del VOD (formato TwitchDownloader).")
    pc.add_argument("-u", "--id", required=True, help="URL kick.com/<canal>/videos/<uuid>")
    pc.add_argument("-o", "--output", required=True, help="Archivo de salida, p. ej. chat.json")
    pc.add_argument("--workers", type=int, default=8,
                    help="Pedidos en paralelo (default 8).")
    pc.add_argument("--collision", default="Overwrite",
                    choices=["Overwrite", "Exit", "Rename"],
                    help="Qué hacer si el archivo ya existe (default Overwrite).")
    pc.set_defaults(func=cmd_chatdownload)

    pv = sub.add_parser("videodownload", help="Descarga un segmento del VOD por HLS (ffmpeg).")
    pv.add_argument("-u", "--id", required=True, help="URL kick.com/<canal>/videos/<uuid>")
    pv.add_argument("-o", "--output", required=True, help="Archivo de salida, p. ej. video.mp4")
    pv.add_argument("-q", "--quality", default="720p60",
                    help="Calidad máxima: 360p, 480p, 720p, 720p60, 1080p o best (default 720p60).")
    pv.add_argument("-b", "--beginning", default=None,
                    help="Inicio del segmento: 3600, 3600s, 60m, 2h o 1:02:03.")
    pv.add_argument("-e", "--ending", default=None,
                    help="Fin del segmento: mismo formato que --beginning.")
    pv.add_argument("--ffmpeg-path", default="ffmpeg",
                    help="Ruta a ffmpeg si no está en el PATH (default ffmpeg).")
    pv.add_argument("--collision", default="Overwrite",
                    choices=["Overwrite", "Exit", "Rename"],
                    help="Qué hacer si el archivo ya existe (default Overwrite).")
    pv.set_defaults(func=cmd_videodownload)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KickError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
