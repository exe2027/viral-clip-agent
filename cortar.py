#!/usr/bin/env python3
"""cortar.py — CLI estable del paso de corte (Fase 2: un orquestador
Go puede llamar a este comando sin tocar vídeo/IA).

Corta un clip vertical 9:16 con layout responsive (juego arriba,
cámara abajo) a partir de un vídeo fuente. Idempotente: si la salida
ya existe, no vuelve a llamar a ffmpeg.

Uso:
    python cortar.py --in video.mp4 --start 11710 --duration 60 \\
        --out clips/clip_01.mp4 [--layout auto|stack|pip|center]
        [--crf 23] [--json]

Salida: JSON con {output, reused, start_sec, duration, layout}.
Sin secretos, sin red: solo ffmpeg local.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.workers import StepError, cut_clip  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Corta un clip vertical 9:16.")
    p.add_argument("--in", dest="video", required=True,
                   help="Vídeo fuente")
    p.add_argument("--start", type=float, required=True,
                   help="Inicio del clip en segundos (en el vídeo fuente)")
    p.add_argument("--duration", type=float, default=60,
                   help="Duración del clip en segundos (default: 60)")
    p.add_argument("--out", dest="output", required=True,
                   help="MP4 de salida")
    p.add_argument("--layout", default="auto",
                   choices=["auto", "stack", "pip", "center"],
                   help="Modo de layout (default: auto)")
    p.add_argument("--crf", type=int, default=23)
    p.add_argument("--json", action="store_true",
                   help="Imprime solo el JSON del resultado")
    args = p.parse_args(argv)
    try:
        result = cut_clip(args.video, args.start, args.duration,
                          args.output, layout_mode=args.layout,
                          crf=args.crf)
    except StepError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"Cortado: {result['output']}"
              + (" (reutilizado, sin re-cortar)"
                 if result.get("reused") else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
