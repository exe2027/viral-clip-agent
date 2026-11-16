#!/usr/bin/env python3
"""
auto_clips.py — automatiza TODO el flujo de clips de GTA 6.

Un solo comando, desde el VOD hasta los clips listos para publicar:

    python auto_clips.py 1234567890
    python auto_clips.py https://www.twitch.tv/videos/1234567890 --lang es

Qué hace solo, sin que tengas que hacer nada a mano:
  1. Descarga el chat (chat.json) y el VOD (vod.mp4) con TwitchDownloaderCLI.
  2. Detecta los mejores momentos con clips_chat_v2.py (risas + hype, ES + EN).
  3. Corta los clips en vertical 1080x1920.
  4. Empaqueta cada clip para viral con viral_package.py, que combina
     youtube-agent-skill: yt-viral (ranking por múltiplo sobre la mediana),
     yt-shorts (primera línea nueva, texto en pantalla, loop),
     yt-package (título + miniatura como una unidad, ≤60/40 car.) y
     yt-seo (descripción, tags ≤15, 3 búsquedas). Genera el manifiesto
     (manifest.json / manifest.md / titles.txt) por clip.
  5. Deja todo ordenado en runs/<vod-id>/ : chat.json, vod.mp4, clips/, manifiesto.

Si el chat o el video ya están descargados, los reutiliza (no vuelve a bajarlos).
Con --dry-run solo descarga el chat y lista los picos, sin bajar el video ni cortar.

Para probar sin Twitch, usa archivos locales:
    python auto_clips.py --chat test_chat.json --video test_vod.mp4 --out runs/prueba

Requisitos: Python 3.10+, TwitchDownloaderCLI y FFmpeg en el PATH.
Sin credenciales, sin red salvo la descarga de Twitch.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict

from clips_chat_v2 import (
    cut_vertical_clip,
    fmt_ts,
    get_video_duration,
    score_message,
    score_windows,
    select_top_peaks,
)
from viral_package import package_clip

HASHTAGS_ES = "#GTA6 #GTA6Clips #ElClipDeGTA6 #GTA #RockstarGames #gaming"
HASHTAGS_EN = "#GTA6 #GTA6Clips #GTA #RockstarGames #gaming #clips"


def parse_vod_id(value: str) -> str:
    """Acepta un ID ('1234567890') o una URL de Twitch."""
    m = re.search(r"videos/(\d+)", value)
    if m:
        return m.group(1)
    if re.fullmatch(r"\d+", value.strip()):
        return value.strip()
    raise ValueError(f"No reconozco el VOD: {value!r}. Usa el ID o la URL twitch.tv/videos/<id>")


def run(cmd, what: str):
    print(f"$ {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"Error en {what}:\n{r.stderr.strip()[-2000:]}", file=sys.stderr)
        raise SystemExit(1)
    return r


def download_chat(vod_id: str, chat_path: str):
    if os.path.isfile(chat_path):
        print(f"Chat ya descargado, se reutiliza: {chat_path}")
        return
    run(["TwitchDownloaderCLI", "chatdownload", "--id", vod_id,
         "-o", chat_path, "-E"], "descarga del chat")


def download_video(vod_id: str, video_path: str):
    if os.path.isfile(video_path):
        print(f"VOD ya descargado, se reutiliza: {video_path}")
        return
    run(["TwitchDownloaderCLI", "videodownload", "--id", vod_id,
         "-o", video_path], "descarga del VOD")


def sample_messages(chat_path: str, window_start: int, window_size: int = 10, limit: int = 3):
    """Mensajes de risa/hype de esa ventana, para sugerir el título."""
    with open(chat_path, encoding="utf-8") as f:
        data = json.load(f)
    examples = []
    for c in data.get("comments", []):
        off = c.get("content_offset_seconds")
        if off is None:
            continue
        if window_start <= int(off) < window_start + window_size:
            body = (c.get("message", {}).get("body", "") or "").strip()
            if not body:
                continue
            _, laugh, hype = score_message(body)
            if laugh or hype:
                examples.append(body)
            if len(examples) >= limit:
                break
    return examples


def suggest_titles(detail, lang: str):
    """Título sugerido según si el pico fue más de risas o de hype."""
    risas, hype = detail["risas"], detail["hype"]
    if lang == "en":
        if hype >= risas:
            return "WHAT A PLAY 😱 GTA 6", "Insane moment in GTA 6 — full clip"
        return "They couldn't believe it 😂 GTA 6", "Chat lost it in GTA 6 — best moment"
    # es / both: título en español, que es el canal principal
    if hype >= risas:
        return "QUÉ JUGADA 😱 GTA 6", "El mejor momento del directo de GTA 6"
    return "No se lo creyeron 😂 GTA 6", "El chat se murió de risa en GTA 6"


def main() -> int:
    p = argparse.ArgumentParser(description="Automatiza todo: descarga, detecta, corta y titula los clips de un VOD de Twitch.")
    p.add_argument("vod", nargs="?", help="ID del VOD o URL twitch.tv/videos/<id>")
    p.add_argument("--chat", help="Usar un chat.json local en vez de descargarlo")
    p.add_argument("--video", help="Usar un vod.mp4 local en vez de descargarlo")
    p.add_argument("--out", help="Carpeta de salida (default: runs/<vod-id>)")
    p.add_argument("--top-n", type=int, default=5)
    p.add_argument("--window", type=int, default=10)
    p.add_argument("--min-separation", type=int, default=60)
    p.add_argument("--pre-roll", type=int, default=20)
    p.add_argument("--duration", type=int, default=60)
    p.add_argument("--lang", choices=["es", "en", "both"], default="both")
    p.add_argument("--crop", choices=["center", "left", "right"], default="center")
    p.add_argument("--crf", type=int, default=23)
    p.add_argument("--preset", default="fast")
    p.add_argument("--dry-run", action="store_true", help="Solo descarga el chat y lista picos, sin video ni cortes")
    p.add_argument("--no-viral", action="store_true", help="No genera el empaquetado viral (títulos, miniatura, SEO)")
    p.add_argument("--delete-vod", action="store_true", help="Borra el vod.mp4 después de cortar, para ahorrar espacio")
    args = p.parse_args()

    if not args.vod and not args.chat:
        print("Error: pasa el VOD (ID o URL) o --chat <chat.json>.", file=sys.stderr)
        return 1
    if not args.dry_run and not args.video and not args.vod:
        print("Error: para cortar hace falta el VOD: pasa el ID/URL o --video.", file=sys.stderr)
        return 1

    vod_id = None
    if args.vod:
        try:
            vod_id = parse_vod_id(args.vod)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    out_dir = args.out or (f"runs/{vod_id}" if vod_id else "runs/local")
    clips_dir = os.path.join(out_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)

    chat_path = args.chat or os.path.join(out_dir, "chat.json")
    video_path = args.video or (os.path.join(out_dir, "vod.mp4") if vod_id else None)

    # TwitchDownloaderCLI solo hace falta si hay que descargar algo.
    # Con --chat/--video locales no se pide (bug corregido de la v1 de auto_clips).
    needs_download = bool(vod_id and (not args.chat or (not args.video and not args.dry_run)))
    if needs_download and shutil.which("TwitchDownloaderCLI") is None:
        print("Error: TwitchDownloaderCLI no está en el PATH.", file=sys.stderr)
        return 1
    if shutil.which("ffmpeg") is None and not args.dry_run:
        print("Error: FFmpeg no está en el PATH.", file=sys.stderr)
        return 1

    # 1. Descargar (o reutilizar)
    if args.chat:
        chat_path = args.chat
    elif vod_id:
        download_chat(vod_id, chat_path)
    if not os.path.isfile(chat_path):
        print(f"Error: no existe el chat: {chat_path}", file=sys.stderr)
        return 1

    # 2. Detectar picos
    scores, detail = score_windows(chat_path, window_size=args.window, lang=args.lang)
    if not scores:
        print("No se encontraron mensajes con marcas de tiempo en el chat.")
        return 0
    peaks = select_top_peaks(scores, top_n=args.top_n, min_separation=args.min_separation)
    print(f"Chat: {sum(d['msgs'] for d in detail.values())} mensajes — {len(peaks)} picos")
    for i, (ts, score) in enumerate(peaks, 1):
        d = detail[ts]
        print(f"  {i}. {fmt_ts(ts)} — {score} pts ({d['msgs']} msgs, {d['risas']} risas, {d['hype']} hype)")

    if args.dry_run:
        print("Dry-run: no se descargó el video ni se cortó nada.")
        return 0

    if args.video:
        video_path = args.video
    elif vod_id:
        download_video(vod_id, video_path)
    if not video_path or not os.path.isfile(video_path):
        print(f"Error: no existe el video: {video_path}", file=sys.stderr)
        return 1

    duration = get_video_duration(video_path)
    if duration:
        print(f"VOD: {fmt_ts(int(duration))} ({duration:.0f}s)")

    # 3. Cortar + 4. Titular y manifiesto
    manifest = {"vod_id": vod_id, "lang": args.lang, "clips": []}
    for idx, (peak_ts, score) in enumerate(peaks, 1):
        clip_start = max(0, peak_ts - args.pre_roll)
        clip_dur = args.duration
        if duration and clip_start + clip_dur > duration:
            clip_dur = max(1, int(duration - clip_start))
        d = detail[peak_ts]
        fname = f"clip_{idx:02d}_t{fmt_ts(clip_start)}_s{score}_r{d['risas']}h{d['hype']}.mp4"
        out_file = os.path.join(clips_dir, fname)
        title_es, desc_es = suggest_titles(d, "es")
        title_en, desc_en = suggest_titles(d, "en")
        examples = sample_messages(chat_path, peak_ts, window_size=args.window)
        viral = None if args.no_viral else package_clip(
            idx, peak_ts, score, d["risas"], d["hype"],
            median_score=None, all_scores=list(scores.values()),
            lang=args.lang, examples=examples,
        )
        print(f"[{idx}/{len(peaks)}] {fmt_ts(peak_ts)} ({score} pts) -> {fname}")
        print(f"    Título ES: {title_es} | EN: {title_en}")
        if viral:
            print(f"    Viral: {viral['viral_multiple']}x la mediana | {viral['best_title']} | Miniatura: {viral['thumbnail_text']}")
        try:
            cut_vertical_clip(video_path, clip_start, clip_dur, out_file,
                              crf=args.crf, preset=args.preset, crop=args.crop)
        except subprocess.CalledProcessError as e:
            print(f"[{idx}] Falló el corte: {e}", file=sys.stderr)
            continue
        manifest["clips"].append({
            "n": idx, "file": f"clips/{fname}", "peak": peak_ts,
            "start": clip_start, "duration": clip_dur, "score": score,
            "risas": d["risas"], "hype": d["hype"],
            "title_es": title_es, "desc_es": desc_es,
            "title_en": title_en, "desc_en": desc_en,
            "hashtags_es": HASHTAGS_ES, "hashtags_en": HASHTAGS_EN,
            "chat_examples": examples,
            "viral": viral,
        })

    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    lines = [f"# Clips — VOD {vod_id or 'local'}", ""]
    for c in manifest["clips"]:
        lines += [f"## Clip {c['n']} — {c['file']}",
                  f"- Pico: {fmt_ts(c['peak'])} | {c['score']} pts ({c['risas']} risas, {c['hype']} hype)",
                  f"- ES: {c['title_es']} — {c['desc_es']} {c['hashtags_es']}",
                  f"- EN: {c['title_en']} — {c['desc_en']} {c['hashtags_en']}"]
        v = c.get("viral")
        if v:
            lines += [f"- Viral: {v['viral_multiple']}x la mediana | Fórmula: {v['formula']} ({v['formula_desc']})",
                      f"- Título viral: {v['best_title']} | Miniatura: {v['thumbnail_text']} (no repite el título)",
                      f"- Hook (1ª línea): {v['hook_first_line']} | Texto en pantalla 2 s: {v['onscreen_first_2s']} | Loop: {v['loop_point']}",
                      f"- Descripción: {v['description_first_2_lines']}",
                      f"- Tags: {', '.join(v['tags'])}",
                      f"- Búsquedas a ganar: {', '.join(v['search_queries'])}",
                      f"- ¿Lo subo así, o lo cambio?"]
            plats = v.get("platforms", {}) if v else {}
            if plats:
                lines.append("- Por plataforma (misma idea, paquete distinto):")
                for pk in ("youtube_shorts", "tiktok", "instagram_reels", "facebook_reels"):
                    pp = plats.get(pk)
                    if not pp:
                        continue
                    cap = pp["caption"].replace("\n", " / ")
                    lines.append(
                        f"  - {pp['platform']}: {cap} | Subtítulos: {pp['subtitles']} | "
                        f"Miniatura: {pp['thumbnail']} | Subida: {pp['publish_mode']} | Métrica: {pp['metric']}"
                    )
        lines += [""]
    with open(os.path.join(out_dir, "manifest.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(os.path.join(out_dir, "titles.txt"), "w", encoding="utf-8") as f:
        for c in manifest["clips"]:
            v = c.get("viral")
            best = v["best_title"] if v else c["title_es"]
            f.write(f"Clip {c['n']} viral: {best} | Miniatura: {v['thumbnail_text'] if v else ''}\n")
            f.write(f"Clip {c['n']} ES: {c['title_es']}\nClip {c['n']} EN: {c['title_en']}\n")

    if args.delete_vod and not args.video and video_path and os.path.isfile(video_path):
        os.remove(video_path)
        print("VOD borrado (--delete-vod).")

    print(f"\nListo: {len(manifest['clips'])} clips en {clips_dir}/")
    print(f"Manifiesto: {out_dir}/manifest.md  |  Títulos: {out_dir}/titles.txt")
    print("Lo único que queda a mano es subirlos a TikTok / Reels / Shorts — "
          "eso no se puede automatizar sin que cada plataforma te pida entrar y confirmar en tu teléfono.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
