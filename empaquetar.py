#!/usr/bin/env python3
"""empaquetar.py — CLI estable del paso de empaquetado (Fase 2: un
orquestador Go puede llamar a este comando sin tocar vídeo/IA).

Puntúa un paquete (título, descripción, tags, hashtags) con el SEO
por plataforma y calcula la prioridad de cada plataforma con la
fórmula aprobada (viralidad^0,5 x SEO^0,3 x chat_norm^0,2). No
inventa títulos: el título viene del manifiesto o de la revisión
humana. Escribe el paquete en JSON (atómico).

Uso:
    python empaquetar.py clip.mp4 --title "GTA 6: ..." \\
        [--description "..."] [--tags gta6 clips] [--hashtags "#GTA6"]
        [--thumbnail-text "NO PUEDE SER"] [--out clip.package.json]
        [--virality 78] [--chat-multiple 13] [--json]

Sin secretos, sin red, sin vídeo: solo texto -> puntajes.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.workers import StepError, package_clip_step  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Empaqueta un clip (SEO + "
                                            "prioridad por plataforma).")
    p.add_argument("clip", help="MP4 del clip")
    p.add_argument("--title", required=True, help="Título del clip")
    p.add_argument("--description", default="")
    p.add_argument("--tags", nargs="*", default=[])
    p.add_argument("--hashtags", nargs="*", default=[])
    p.add_argument("--thumbnail-text", default=None)
    p.add_argument("--out", help="JSON del paquete "
                   "(default: <clip>.package.json)")
    p.add_argument("--virality", type=float, default=None,
                   help="Score de viralidad 0-100 (del paso picos)")
    p.add_argument("--chat-multiple", type=float, default=None,
                   help="Múltiplo pico/mediana del chat")
    p.add_argument("--json", action="store_true",
                   help="Imprime solo el JSON del resultado")
    args = p.parse_args(argv)
    try:
        result = package_clip_step(
            args.clip, args.title, description=args.description,
            tags=args.tags, hashtags=args.hashtags,
            thumbnail_text=args.thumbnail_text, out_path=args.out)
        if args.virality is not None and args.chat_multiple is not None:
            from clipagent.priority import priority as priority_fn
            from clipagent.seo_score import score_all_platforms
            seo = score_all_platforms({
                "title": args.title, "description": args.description,
                "tags": args.tags, "hashtags": args.hashtags,
                "thumbnail_text": args.thumbnail_text})
            result["priority"] = {
                plat: priority_fn(args.virality, seo[plat]["score"],
                                  args.chat_multiple)["priority"]
                for plat in ("youtube_shorts", "tiktok",
                             "instagram_reels", "facebook_reels")}
    except StepError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"Paquete: {result['package_path']}")
        print(f"  SEO: {result['seo']}")
        print(f"  Prioridad: {result['priority']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
