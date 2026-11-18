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
    p_recal = sub.add_parser("recalibrate",
                             help="Recalibra los pesos con las métricas (6B.7)")
    p_recal.add_argument("--db", default=None,
                         help="ruta a metrics.db (por defecto, la del repo)")
    p_metrics = sub.add_parser("metrics",
                               help="Métricas de clips publicados (9.1)")
    p_metrics.add_argument("--collect", action="store_true",
                           help="medir lo vencido (24/48 h y 7 d) y mostrar el reporte")
    p_metrics.add_argument("--db", default=None,
                           help="ruta a metrics.db (por defecto, la del repo)")
    p_live = sub.add_parser("live", help="Seguir un directo en vivo (9.2)")
    p_live.add_argument("streamer", help="canal (twitch o kick)")
    p_live.add_argument("--platform", default="twitch",
                        choices=["twitch", "kick"])
    p_live.add_argument("--minutes", type=int, default=60,
                        help="duración de la sesión (por defecto 60)")
    p_live.add_argument("--cut", action="store_true",
                        help="cortador en vivo: graba, corta 40 s, "
                        "subtitula y encola en la Cola LIVE del panel")
    p_live.add_argument("--auto-publish", action="store_true",
                        help="con --cut: publicar por API lo publicable "
                        "(por defecto solo Cola + draft, sin público)")
    p_replay = sub.add_parser(
        "live-replay",
        help="Replay de un VOD como si fuera en vivo (mismo cutter)")
    p_replay.add_argument("--chat", required=True,
                          help="chat.json del VOD (con capítulos)")
    p_replay.add_argument("--video", required=True,
                          help="segmento o VOD descargado")
    p_replay.add_argument("--video-offset", type=float, default=0.0,
                          help="segundo absoluto del VOD donde empieza "
                          "--video (por defecto 0)")
    p_replay.add_argument("--streamer", default="rubius")
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

    p_jobs = sub.add_parser(
        "jobs", help="Tabla step_jobs: listar, encolar, reanudar")
    p_jobs.add_argument("action", nargs="?", default="list",
                        choices=["list", "enqueue", "show", "resume",
                                 "stale"],
                        help="list (default) | enqueue | show | resume "
                        "| stale")
    p_jobs.add_argument("--db", default=None)
    p_jobs.add_argument("--estado", default=None,
                        help="filtra por estado (pending, running, done, "
                        "failed, scheduled, processing, posted, "
                        "esperando_humano)")
    p_jobs.add_argument("--paso", default=None,
                        help="filtra por paso (vod, picos, corte, "
                        "subtitulos, empaquetar, publicar)")
    p_jobs.add_argument("--clip", default=None,
                        help="clip_id (enqueue/show/resume lo usan)")
    p_jobs.add_argument("--plataforma", default=None)
    p_jobs.add_argument("--payload", default="{}",
                        help="JSON del payload (enqueue)")
    p_jobs.add_argument("--prioridad", type=float, default=0.0)
    p_jobs.add_argument("--job-id", type=int, default=None)
    p_jobs.add_argument("--limit", type=int, default=20)

    p_workers = sub.add_parser(
        "workers", help="Supervisor único: Iniciar / estado / un paso")
    p_workers.add_argument("action", nargs="?", default="status",
                           choices=["start", "status", "step"],
                           help="start (Iniciar del panel) | status | "
                           "step (un solo paso, depuración)")
    p_workers.add_argument("--db", default=None)
    p_workers.add_argument("--paso", default=None,
                           help="paso para 'step' (vod, picos, corte, "
                           "subtitulos, empaquetar, publicar)")
    p_workers.add_argument("--once", action="store_true",
                           help="procesa lo debido y sale (cron/tests)")
    p_workers.add_argument("--max-jobs", type=int, default=None)
    p_workers.add_argument("--poll", type=int, default=5,
                           help="segundos entre ticks (default: 5)")

    p_pipeline = sub.add_parser(
        "pipeline", help="Encolar una cadena o una publicación")
    p_pipeline.add_argument("action", choices=["enqueue", "publish",
                                               "bridge"],
                            help="enqueue (cadena vod->...->empaquetar) | "
                            "publish (un clip + plataforma, manual) | "
                            "bridge (cola legacy -> step pipelines)")
    p_pipeline.add_argument("--limit", type=int, default=5,
                            help="jobs legacy a puentear (bridge)")
    p_pipeline.add_argument("--db", default=None)
    p_pipeline.add_argument("--clip", required=False, default=None,
                            help="clip_id (enqueue) o id del clip "
                            "(publish); bridge no lo usa")
    p_pipeline.add_argument("--url", default=None,
                            help="URL del VOD (enqueue)")
    p_pipeline.add_argument("--payload", default="{}",
                            help="JSON del payload (enqueue)")
    p_pipeline.add_argument("--plataforma", default=None,
                            help="plataforma (publish)")
    p_pipeline.add_argument("--clip-path", default=None,
                            help="MP4 del clip (publish)")
    p_pipeline.add_argument("--mode", default="draft",
                            choices=["draft", "dry-run", "api"],
                            help="modo de publicación (default: draft)")
    p_pipeline.add_argument("--prioridad", type=float, default=0.0)

    p_tiktok = sub.add_parser(
        "tiktok-browser",
        help="Agente navegador TikTok (1 clip/día, sesión persistente, "
        "captcha atendido por humano; corre en runner autoalojado)")
    p_tiktok.add_argument("args", nargs=argparse.REMAINDER,
                          help="login | upload | verify | status ...")
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
    if args.command == "recalibrate":
        import json
        from .learning import run_recalibrate
        report = run_recalibrate(args.db)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    if args.command == "metrics":
        import json
        from .metrics import collect, report
        if args.collect:
            print(json.dumps(collect(args.db), indent=2,
                             ensure_ascii=False))
        print(json.dumps(report(args.db), indent=2, ensure_ascii=False))
        return 0
    if args.command == "live":
        if getattr(args, "cut", False):
            from .live_cutter import run_live_cut
            from .panel_bridge import PanelBridge
            bridge = PanelBridge.from_env()
            if bridge is None:
                print("Sin PANEL_USERNAME/PANEL_PASSWORD: el cutter "
                      "sigue solo con la cola del cerebro.")
            result = run_live_cut(
                args.streamer, platform=args.platform,
                minutes=args.minutes, bridge=bridge,
                auto_publish=bool(getattr(args, "auto_publish",
                                          False)))
            print(f"Picos detectados: {len(result['peaks'])}")
            print(f"Clips cortados: {len(result['clips'])}")
            for clip in result["clips"]:
                print(f"  - {clip['peak_ts']:.0f}s "
                      f"({clip['category']}): {clip['title']}")
            print(f"Descartados: {len(result['skipped'])}")
            for skip in result["skipped"]:
                print(f"  - {skip.get('ts')}: {skip.get('reason')}")
            return 0
        from .config import load_config
        from .live import (KickPusherClient, LiveLimits, LiveSession,
                           TwitchIRCClient)
        cfg = (load_config() or {}).get("live") or {}
        limits = LiveLimits(
            cooldown_minutes=cfg.get("cooldown_minutes", 10),
            max_clips_per_stream=cfg.get("max_clips_per_stream", 5))
        session = LiveSession(args.streamer, platform=args.platform,
                              limits=limits)
        if args.platform == "kick":
            source = KickPusherClient(args.streamer)
            ok, msg = source.connect()
            if not ok:
                print(f"No se pudo seguir el directo: {msg}")
                return 1
        else:
            source = TwitchIRCClient([args.streamer])
            source.connect()
        print(f"Siguiendo el directo de {args.streamer} "
              f"({args.platform}) durante {args.minutes} min. "
              "Ctrl+C para parar.")
        try:
            peaks = session.run(source, duration_s=args.minutes * 60)
        except KeyboardInterrupt:
            print("\nSesión detenida por el usuario.")
            peaks = session.peaks
        print(f"Picos detectados: {len(peaks)}")
        for peak in peaks:
            print(f"  - {peak['ts']:.0f}s: {peak['score']} pts "
                  f"({peak['multiple']}x)")
        return 0
    if args.command == "live-replay":
        from .jobs import StepJobs
        from .live_cutter import CategoryGate, LiveCutterSession
        gate = CategoryGate(mode="vod", chat_path=args.chat)
        session = LiveCutterSession(
            args.streamer, platform="twitch",
            stream_id="replay", gate=gate, store=StepJobs(":memory:"),
            dry_run=True)
        peaks, clips, skipped = session.replay(
            args.chat, args.video, video_offset=args.video_offset)
        print(f"Picos detectados: {len(peaks)}")
        print(f"Clips cortados: {len(clips)}")
        for clip in clips:
            print(f"  - {clip['peak_ts']:.0f}s "
                  f"({clip['category']}): {clip['title']} "
                  f"-> {clip['clip']}")
        print(f"Descartados: {len(skipped)}")
        for skip in skipped:
            print(f"  - {skip.get('ts')}: {skip.get('reason')}")
        return 0
    if args.command == "publish":
        from .publish import main as publish_main
        return publish_main(args.args or ["audit"])
    if args.command == "tiktok-browser":
        from .tiktok_browser import main as tiktok_main
        return tiktok_main(args.args or ["status"])
    if args.command == "jobs":
        import json as _json
        from .jobs import StepJobs
        store = StepJobs(args.db) if args.db else StepJobs()
        try:
            if args.action == "list":
                jobs = store.list_jobs(estado=args.estado,
                                       paso=args.paso,
                                       clip_id=args.clip,
                                       limit=args.limit)
                print(f"step_jobs: {store.counts()}")
                for job in jobs:
                    print(f"  #{job['id']} [{job['estado']:<16}] "
                          f"{job['paso']:<11} {job['clip_id']} "
                          f"({job.get('plataforma') or '-'}, "
                          f"intentos={job['intentos']})"
                          + (f" — {job['error'][:120]}"
                             if job.get("error") else ""))
                return 0
            if args.action == "enqueue":
                if not args.clip or not args.paso:
                    print("enqueue exige --clip y --paso",
                          file=sys.stderr)
                    return 2
                job = store.enqueue(
                    args.clip, args.paso, plataforma=args.plataforma,
                    payload=_json.loads(args.payload or "{}"),
                    prioridad=args.prioridad)
                print(f"Encolado step job #{job['id']} "
                      f"({job['paso']}, {job['clip_id']}, "
                      f"{job['estado']})")
                return 0
            if args.action == "show":
                job = store.get(args.job_id) if args.job_id else None
                if job is None:
                    print("show exige --job-id", file=sys.stderr)
                    return 2
                print(_json.dumps(job, indent=2, ensure_ascii=False))
                for ev in store.events(job["id"]):
                    print(f"  {ev['ts']} {ev['event']}: {ev['detail']}")
                return 0
            if args.action == "resume":
                job = store.resume(args.job_id)
                print(f"Job #{job['id']} reanudado -> pending.")
                return 0
            if args.action == "stale":
                n = store.requeue_stale()
                print(f"Recuperados {n} job(s) de un worker muerto.")
                return 0
        finally:
            store.close()
        return 0
    if args.command == "workers":
        from .jobs import StepJobs
        from .workers import run_supervisor, run_worker, workers_status
        store = StepJobs(args.db) if args.db else StepJobs()
        try:
            if args.action == "status":
                import json as _json
                st = workers_status(store)
                print(f"conteos: {st['counts']}")
                print(f"debidos por paso: {st['due_by_step']}")
                print("cómputo por paso (worker = cabe en Cloudflare "
                      "Workers; external = exige cómputo externo):")
                for paso, where in st["compute"].items():
                    print(f"  {paso:<11} {where}")
                if st["stuck"]:
                    print("ATASCADOS (motivo y hora):")
                    for job in st["stuck"]:
                        print(f"  #{job['id']} [{job['estado']}] "
                              f"{job['paso']} {job['clip_id']} — "
                              f"{job.get('error', '')[:120]} "
                              f"({job['updated_at']})")
                else:
                    print("Atascados: ninguno.")
                print(_json.dumps(st, indent=2, ensure_ascii=False,
                                  default=str))
                return 0
            if args.action == "step":
                if not args.paso:
                    print("step exige --paso", file=sys.stderr)
                    return 2
                return run_worker(store, args.paso, once=args.once,
                                  max_jobs=args.max_jobs,
                                  poll_s=args.poll)
            return run_supervisor(store, once=args.once,
                                  max_jobs=args.max_jobs,
                                  poll_s=args.poll)
        finally:
            store.close()
    if args.command == "pipeline":
        import json as _json
        from .jobs import StepJobs
        from .workers import (bridge_legacy_queue, enqueue_pipeline,
                              enqueue_publish)
        store = StepJobs(args.db) if args.db else StepJobs()
        try:
            if args.action == "bridge":
                from jobqueue import Queue
                q = Queue(args.db) if args.db else Queue()
                try:
                    created = bridge_legacy_queue(store, q,
                                                  limit=args.limit)
                finally:
                    q.close()
                print(f"Puenteados {len(created)} VOD(s) legacy a "
                      "step pipelines.")
                for job in created:
                    print(f"  step job #{job['id']} (vod, "
                          f"{job['clip_id']})")
                return 0
            if args.action == "enqueue":
                if not args.clip:
                    print("enqueue exige --clip", file=sys.stderr)
                    return 2
                payload = _json.loads(args.payload or "{}")
                if args.url:
                    payload["url"] = args.url
                job = enqueue_pipeline(store, args.clip, payload,
                                       prioridad=args.prioridad)
                print(f"Cadena encolada: step job #{job['id']} "
                      f"(vod, {job['clip_id']}). El supervisor avanza "
                      "solo hasta empaquetar; TikTok se auto-publica "
                      "desde ahí, el resto es manual.")
                return 0
            if args.action == "publish":
                if not args.clip:
                    print("publish exige --clip", file=sys.stderr)
                    return 2
                if not args.plataforma or not args.clip_path:
                    print("publish exige --plataforma y --clip-path",
                          file=sys.stderr)
                    return 2
                job = enqueue_publish(store, args.clip,
                                      args.plataforma, args.clip_path,
                                      mode=args.mode,
                                      prioridad=args.prioridad)
                print(f"Publicación encolada: step job #{job['id']} "
                      f"({args.plataforma}, modo {args.mode}).")
                return 0
        finally:
            store.close()
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
