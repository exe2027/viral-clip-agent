#!/usr/bin/env python3
"""subtitular.py — CLI estable del paso de subtítulos (Fase 2: un
orquestador Go puede llamar a este comando sin tocar vídeo/IA).

Transcribe con faster-whisper (local, tiny por defecto), genera el
SRT y opcionalmente lo quema en el vídeo. Idempotente: si el SRT ya
existe, se reutiliza sin re-transcribir. Si el clip no tiene voz,
termina OK marcado como no_speech (no es un fallo del paso), pero
con publish_blocked (regla 2026-11-17 22:10: SIEMPRE subtítulos
completos, sin subtítulos no se publica; lo revisa el usuario).

Uso:
    python subtitular.py clip.mp4 [--srt clip.srt] [--burn clip_subs.mp4]
        [--style karaoke|tiktok|highlight] [--model tiny] [--lang es]
        [--no-transcribe] [--json]

--no-transcribe quema un SRT ya existente sin transcribir (probar
estilos). El estilo por defecto sale de config.yaml
(subtitles.default_style). Sin secretos, sin red.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.workers import (StepError, load_worker_config,  # noqa: E402
                              subtitle_clip)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Transcribe y subtitula un clip (local).")
    p.add_argument("video", help="Clip de entrada")
    p.add_argument("--srt", help="Ruta del SRT (default: <video>.srt)")
    p.add_argument("--burn", help="Vídeo de salida con subtítulos quemados")
    p.add_argument("--style", default=None,
                   help="Estilo: tiktok, highlight, karaoke "
                        "(default: config.yaml)")
    p.add_argument("--model", default="tiny",
                   help="Modelo faster-whisper (default: tiny)")
    p.add_argument("--lang", default=None, help="Idioma fuente (auto)")
    p.add_argument("--no-transcribe", action="store_true",
                   help="Quema el SRT dado sin re-transcribir")
    p.add_argument("--json", action="store_true",
                   help="Imprime solo el JSON del resultado")
    args = p.parse_args(argv)
    cfg = load_worker_config()
    try:
        if args.no_transcribe:
            if not args.srt or not os.path.isfile(args.srt):
                print("Error: --no-transcribe exige un --srt existente",
                      file=sys.stderr)
                return 1
            if not args.burn:
                print("Error: --no-transcribe exige --burn",
                      file=sys.stderr)
                return 1
            from clipagent.transcribe import burn_subtitles
            burn_subtitles(args.video, args.srt, args.burn,
                           style=args.style, config=cfg)
            result = {"video": args.video, "srt": args.srt,
                      "burned": args.burn, "no_speech": False,
                      "reused_srt": True}
        else:
            result = subtitle_clip(
                args.video, srt=args.srt, burn=args.burn,
                style=args.style, cfg=cfg, lang=args.lang,
                model=args.model)
    except StepError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    elif result.get("no_speech"):
        print(f"Sin voz: {args.video} (clip solo gameplay/música, "
              "sin SRT ni quemado).")
    else:
        print(f"Subtítulos: {result['srt']}"
              + (f" quemados en {result['burned']}"
                 if result.get("burned") else "")
              + (" (SRT reutilizado)" if result.get("reused_srt") else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
