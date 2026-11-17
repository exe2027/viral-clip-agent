"""CLI único `clipagent` (Fase 3.3: nace con setup/doctor; la
Fase 5 lo completa con start/stop/status/add/queue/worker/watch/
publish). Uso: python -m clipagent <comando> [opciones].
"""
import argparse
import sys


def build_parser():
    parser = argparse.ArgumentParser(
        prog="clipagent",
        description="Clips virales de directos: detectar, cortar, "
                    "subtitular y publicar (con red de seguridad).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser(
        "setup", help="Asistente interactivo para conectar plataformas")
    p_setup.add_argument("platform", nargs="?", default=None,
                         help="plataforma (youtube_shorts, tiktok, "
                         "instagram_reels, facebook_reels, twitch, kick); "
                         "vacío = todas")
    p_setup.add_argument("--non-interactive", action="store_true",
                         help="solo muestra el plan, sin lanzar OAuth")

    sub.add_parser("doctor", help="Comprueba todas las conexiones "
                   "(solo lectura, sin publicar nada)")

    p_add = sub.add_parser(
        "add", help="Agrega un streamer desde su link a streamers.yaml")
    p_add.add_argument("link", help="twitch.tv/<canal>, kick.com/<canal> "
                       "o link de VOD")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "setup":
        from .setup import run as run_setup
        platforms = [args.platform] if args.platform else None
        return run_setup(platforms=platforms,
                         non_interactive=args.non_interactive)
    if args.command == "doctor":
        from .doctor import run_doctor
        return run_doctor()
    if args.command == "add":
        from .links import add_link
        ok, msg = add_link(args.link)
        print(msg)
        return 0 if ok else 1
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
