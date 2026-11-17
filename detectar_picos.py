#!/usr/bin/env python3
"""detectar_picos.py — CLI estable del paso de picos (Fase 2: un
orquestador Go puede llamar a este comando sin tocar vídeo/IA).

Lee un chat.json (Twitch/Kick), puntúa las ventanas de 10 s y
devuelve los picos con su score viral. No descarga nada, no toca
vídeo: solo chat -> picos.

Uso:
    python detectar_picos.py chat.json [--top-n 5] [--lang both]
        [--out picos.json] [--json]

Salida: JSON con {peaks, discarded, median, n_windows}.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.workers import StepError, detect_peaks  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Detecta picos en un chat.")
    p.add_argument("chat", help="chat.json del VOD")
    p.add_argument("--top-n", type=int, default=5)
    p.add_argument("--lang", default="both")
    p.add_argument("--out", help="Guarda el JSON en este archivo")
    p.add_argument("--json", action="store_true",
                   help="Imprime solo el JSON del resultado")
    args = p.parse_args(argv)
    try:
        result = detect_peaks(args.chat, top_n=args.top_n,
                              lang=args.lang)
    except StepError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    if args.json or args.out:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"Picos: {len(result['peaks'])} "
              f"(mediana {result['median']}, "
              f"{result['n_windows']} ventanas)")
        for peak in result["peaks"]:
            print(f"  - {peak['start']}s: score {peak['score']} "
                  f"({peak['multiple']}x, viral "
                  f"{peak.get('viral_total')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
