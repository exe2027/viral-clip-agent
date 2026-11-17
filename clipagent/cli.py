"""CLI único `clipagent` (Fase 3.3: nace con setup/doctor; la
Fase 5 lo completa con start/stop/status/add/queue/worker/watch/
publish). Uso: python -m clipagent <comando> [opciones].
"""
import argparse
import os
import sys

# La raíz del repo (donde viven worker.py, watcher.py, jobqueue.py)
# va al path para que `clipagent` funcione también instalado o
# invocado desde otro directorio.
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


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

    p_start = sub.add_parser(
        "start", help="Arranca el servicio (watcher + worker, en "
        "primer plano; Ctrl+C o `clipagent stop` para parar)")
    p_start.add_argument(
        "--child", action="append", default=[],
        help="hijo extra o sustituto: nombre=comando (repetible; "
        "para pruebas o despliegues avanzados)")

    sub.add_parser("stop", help="Para todo el árbol del servicio")
    sub.add_parser("status", help="Estado del servicio, la cola y "
                   "las plataformas")

    p_resume = sub.add_parser(
        "resume", help="Reanuda una plataforma pausada por el breaker "
        "(acción explícita, 6.4)")
    p_resume.add_argument("platform", choices=[
        "youtube_shorts", "tiktok", "instagram_reels", "facebook_reels"])

    p_queue = sub.add_parser("queue", help="Muestra la cola de jobs")
    p_queue.add_argument("--state", default=None,
                         help="filtra por estado (queued, processing, "
                         "done, failed, dead, rejected)")
    p_queue.add_argument("--limit", type=int, default=10)

    p_worker = sub.add_parser("worker", help="Worker único de la cola")
    p_worker.add_argument("--db", default=None)
    p_worker.add_argument("--once", action="store_true")
    p_worker.add_argument("--max-jobs", type=int, default=None)
    p_worker.add_argument("--top-n", type=int, default=5)
    p_worker.add_argument("--timeout", type=int, default=None)

    p_panel = sub.add_parser("panel", help="Panel web local (6.2)")
    p_panel.add_argument("--port", type=int, default=8765,
                         help="puerto local (por defecto 8765)")
    p_watch = sub.add_parser("watch", help="Vigila VODs nuevos")
    p_watch.add_argument("--once", action="store_true", default=True)
    p_watch.add_argument("--loop", action="store_true")
    p_watch.add_argument("--live", action="store_true")
    p_watch.add_argument("--dry-run", action="store_true")
    p_watch.add_argument("--streamer", default=None)
    p_watch.add_argument("--interval-minutes", type=int, default=None)

    p_pub = sub.add_parser("publish", help="Publicación de clips")
    p_pub.add_argument("args", nargs=argparse.REMAINDER,
                       help="argumentos de publisher.py "
                       "(audit, dry-run, publish, status, quarantine...)")
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
    if args.command == "start":
        from .service import start_service
        children = None
        if args.child:
            import shlex
            children = []
            for spec in args.child:
                name, _, cmd = spec.partition("=")
                if not name or not cmd:
                    print(f"--child inválido: {spec!r} "
                          "(formato nombre=comando)")
                    return 2
                children.append((name, shlex.split(cmd)))
        return start_service(children)
    if args.command == "stop":
        from .service import stop_service
        return stop_service()
    if args.command == "resume":
        from .breaker import reset
        reset(args.platform)
        print(f"{args.platform} reanudada (breaker reseteado).")
        return 0
    if args.command == "status":
        from .service import service_status
        import json
        st = service_status()
        print(f"servicio: {'corriendo' if st['corriendo'] else 'detenido'}"
              + (f" (pid {st['supervisor_pid']})"
                 if st["supervisor_pid"] else ""))
        for name, info in st["hijos"].items():
            print(f"  {name}: pid {info['pid']} "
                  f"({'vivo' if info['vivo'] else 'muerto'})")
        print(f"cola: {st['cola']}")
        for name, info in st["plataformas"].items():
            print(f"  {name}: {info['state']} — {info.get('motivo', '')}")
        print(json.dumps(st, indent=2, ensure_ascii=False))
        return 0
    if args.command == "queue":
        from jobqueue import Queue
        q = Queue()
        try:
            print(f"cola: {q.counts()}")
            for job in q.list_jobs(args.state)[:args.limit]:
                print(f"  #{job['id']} [{job['state']}] {job['url']} "
                      f"({job.get('streamer') or '?'}, {job.get('lang')})")
        finally:
            q.close()
        return 0
    if args.command == "worker":
        import worker as _worker
        argv = []
        if args.db:
            argv += ["--db", args.db]
        if args.once:
            argv.append("--once")
        if args.max_jobs is not None:
            argv += ["--max-jobs", str(args.max_jobs)]
        argv += ["--top-n", str(args.top_n)]
        if args.timeout is not None:
            argv += ["--timeout", str(args.timeout)]
        return _worker.main(argv)
    if args.command == "watch":
        import watcher as _watcher
        argv = []
        if args.loop:
            argv.append("--loop")
        elif args.once:
            argv.append("--once")
        if args.live:
            argv.append("--live")
        if args.dry_run:
            argv.append("--dry-run")
        if args.streamer:
            argv += ["--streamer", args.streamer]
        if args.interval_minutes is not None:
            argv += ["--interval-minutes", str(args.interval_minutes)]
        return _watcher.main(argv)
    if args.command == "panel":
        from .panel import run_panel
        return run_panel(args.port)
    if args.command == "publish":
        from .publish import main as publish_main
        return publish_main(args.args or ["audit"])
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
