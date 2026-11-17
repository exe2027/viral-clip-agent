#!/usr/bin/env python3
"""
worker.py — el único worker de viral-clip-agent (bloque 1 del tribunal).

Saca jobs de la cola FIFO (jobqueue.py), uno a la vez, y ejecuta
``auto_clips.py`` (sin LLM, sin tokens) con el link del job. Cuando
termina, el clip + manifiesto quedan en ``ready_for_review``: nada se
publica desde aquí.

Reglas duras:
  * 1 solo worker: se toma un lock exclusivo (worker.lock, fcntl). Si
    ya hay otro corriendo, este sale sin tocar la cola.
  * Kill-switch: si aparece el archivo ``queue.kill``, el worker se
    pausa INMEDIATAMENTE —incluso a mitad de un job, matando el
    auto_clips en curso— y el job vuelve a ``queued`` sin gastar un
    intento. Se reanuda borrando el archivo.
  * Reintentos: máx 3 y SOLO para fallos transitorios (red/descarga).
    Un fallo permanente va a ``failed`` con el log. Tras 3 intentos
    transitorios, el job va a ``dead``.
  * Antes de correr auto_clips, en Kick resuelve los metadatos del VOD
    (título, categoría, fecha) y los guarda en el job: el semáforo los
    necesita para decidir si el juego es GTA 6 y si el VOD es posterior
    al 19 nov 2026. Si el VOD ya no existe en Kick, es fallo permanente.
  * Si ya hay un ``manifest.json`` en la carpeta de salida (una corrida
    anterior con anotaciones a mano), se respalda a ``*.bak`` antes de
    que auto_clips lo regenere.

Uso:
    python worker.py              # procesa la cola hasta vaciarla y sale
    python worker.py --once       # procesa un solo job y sale
    python worker.py --max-jobs 2 --top-n 1
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.service import (kill_process_group,
                           single_instance_lock)  # noqa: E402
from jobqueue import (  # noqa: E402
    HERE, MAX_ATTEMPTS, Queue, QueueError,
    check_semaphore, classify_error, kill_switch_active,
)

AUTO_CLIPS = os.path.join(HERE, "auto_clips.py")
WORKER_LOCK = os.path.join(HERE, "worker.lock")
DEFAULT_TIMEOUT_S = 7200  # 2 h por job (un VOD de Twitch puede tardar)
POLL_S = 1.0


def _log(msg: str):
    print(f"[worker {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _backup_manifests(out_dir: str):
    """Respalda manifest.json/.md y titles.txt si ya existen (no se pisan)."""
    for name in ("manifest.json", "manifest.md", "titles.txt"):
        src = os.path.join(out_dir, name)
        if os.path.isfile(src):
            dst = src + ".bak"
            shutil.copy2(src, dst)
            _log(f"Respaldo: {src} -> {dst}")


def _resolve_kick_metadata(job: dict):
    """Título, categoría y fecha de un VOD de Kick (para el semáforo).

    Devuelve dict con vod_title/vod_category/vod_start_time (ISO) o
    levanta QueueError('permanent:...') / QueueError('transient:...').
    """
    try:
        from kick_downloader.core import KickError, resolve_vod
    except ImportError as e:
        raise QueueError(f"transient: no se puede importar kick_downloader ({e})")
    import re
    m = re.search(r"kick\.com/([^/]+)/videos/([0-9a-fA-F-]{36})", job["url"])
    if not m:
        raise QueueError("permanent: URL de Kick no reconocible")
    try:
        meta = resolve_vod(m.group(1).lower(), m.group(2).lower())
    except KickError as e:
        text = str(e)
        if ("No encontré el VOD" in text or "borrado o expirado" in text
                or "no es una lista" in text):
            raise QueueError(f"permanent: {text}")
        raise QueueError(f"transient: {text}")
    except Exception as e:  # red, timeout, JSON roto...
        raise QueueError(f"transient: {e}")
    start = meta.get("start_time")
    return {
        "vod_title": meta.get("title"),
        "vod_category": meta.get("category"),
        "vod_start_time": start.isoformat() if start else None,
    }


def _validate_manifest(out_dir: str):
    """Comprueba el manifiesto que dejó auto_clips.

    Devuelve (manifest_dict, problemas: list[str]). Si no hay
    manifiesto o no hay clips, es fallo permanente.
    """
    path = os.path.join(out_dir, "manifest.json")
    if not os.path.isfile(path):
        return None, ["sin manifiesto: auto_clips no dejó manifest.json"]
    try:
        with open(path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return None, [f"manifest.json ilegible: {e}"]
    clips = manifest.get("clips") or []
    problems = []
    if not clips and manifest.get("status") != "sin_momentos_virales":
        problems.append("sin clips generados (el manifiesto viene vacío)")
    for c in clips:
        rel = c.get("file", "")
        if rel and not os.path.isfile(os.path.join(out_dir, rel)):
            problems.append(f"falta el archivo del clip {c.get('n')}: {rel}")
    return manifest, problems


def _run_auto_clips(job: dict, top_n: int, timeout_s: int):
    """Ejecuta auto_clips.py para el job. Devuelve (returncode, log_path).

    Si aparece el kill-switch a mitad de la corrida, mata el proceso
    y levanta KillSwitchRaised.
    """
    out_dir = job["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    _backup_manifests(out_dir)
    cmd = [sys.executable, "-u", AUTO_CLIPS, job["url"], "--lang", job["lang"],
           "--out", out_dir, "--top-n", str(top_n)]
    chat_path = os.path.join(out_dir, "chat.json")
    if os.path.isfile(chat_path):
        cmd += ["--chat", chat_path]  # reutiliza el chat ya descargado
        _log(f"Chat ya descargado, se reutiliza: {chat_path}")
    log_path = os.path.join(out_dir, "worker.log")
    _log(f"$ {' '.join(cmd)}")
    with open(log_path, "a", encoding="utf-8") as logf:
        logf.write(f"\n=== worker job #{job['id']} "
                   f"{datetime.now().isoformat(timespec='seconds')} ===\n")
        logf.write(f"$ {' '.join(cmd)}\n")
        logf.flush()
        proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                                cwd=HERE, start_new_session=True)
        start = time.monotonic()
        while True:
            try:
                rc = proc.wait(timeout=POLL_S)
                return rc, log_path
            except subprocess.TimeoutExpired:
                if kill_switch_active():
                    _log("Kill-switch detectado a mitad del job: "
                         "matando auto_clips...")
                    kill_process_group(proc)
                    raise KillSwitchRaised()
                if time.monotonic() - start > timeout_s:
                    _log(f"Timeout ({timeout_s}s): matando auto_clips...")
                    kill_process_group(proc)
                    raise TimeoutError(
                        f"auto_clips superó el timeout de {timeout_s}s")


class KillSwitchRaised(Exception):
    """El kill-switch apareció durante el job."""


def _read_tail(path: str, chars: int = 2000) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - chars))
            return f.read()
    except OSError:
        return ""


def process_job(q: Queue, job: dict, top_n: int = 5,
                timeout_s: int = DEFAULT_TIMEOUT_S):
    """Procesa un job: queued -> processing -> ready_for_review/failed/dead.

    Devuelve el estado final del job.
    """
    job_id = job["id"]
    started = time.monotonic()
    q.set_state(job_id, "processing")
    q.log(job_id, "processing_started",
          f"worker pid={os.getpid()} top_n={top_n}")
    _log(f"Job #{job_id}: {job['vod_id']} ({job['platform']}, "
         f"lang={job['lang']}) -> processing")

    try:
        # 1. Metadatos del VOD (Kick): el semáforo los necesita.
        if job["platform"] == "kick":
            try:
                meta = _resolve_kick_metadata(job)
            except QueueError as e:
                msg = str(e)
                kind = ("transient" if msg.startswith("transient:")
                        else "permanent")
                return _fail(q, job, msg.split(":", 1)[1].strip(), kind,
                             started)
            q.set_state(job_id, "processing", **meta)
            _log(f"  VOD: {meta.get('vod_title', '')[:60]!r} | "
                 f"categoría={meta.get('vod_category')} | "
                 f"fecha={meta.get('vod_start_time')}")

        # 2. Correr auto_clips (sin LLM, sin tokens).
        try:
            rc, log_path = _run_auto_clips(job, top_n, timeout_s)
        except KillSwitchRaised:
            q.set_state(job_id, "queued")
            q.log(job_id, "paused_by_kill_switch",
                  "auto_clips interrumpido; el job vuelve a queued sin "
                  "gastar intento")
            _log(f"Job #{job_id}: pausado por kill-switch, vuelve a queued")
            raise
        except TimeoutError as e:
            return _fail_timeout(q, job, str(e), started)

        if rc != 0:
            tail = _read_tail(log_path)
            kind = classify_error(tail)
            return _fail(q, job,
                         f"auto_clips salió con código {rc}: "
                         f"{tail.strip()[-500:]}", kind, started)

        # 3. Validar lo que dejó auto_clips.
        manifest, problems = _validate_manifest(job["out_dir"])
        if problems:
            return _fail(q, job, "; ".join(problems), "permanent", started)

        # 3b. Sin momentos virales: auto_clips terminó bien (código 0)
        # pero ningún pico pasó el filtro previo. No es un fallo ni
        # gasta reintentos: el job sale de la cola de revisión como
        # rechazado, con el motivo explícito en el log y en `error`
        # (los descartados quedan en manifest.json). No va a
        # ready_for_review porque no hay nada que revisar.
        if manifest.get("status") == "sin_momentos_virales":
            n_desc = len(manifest.get("descartados") or [])
            msg = ("sin momentos virales: ningún pico pasó el filtro "
                   f"previo ({n_desc} descartado(s); ver 'descartados' "
                   "en manifest.json)")
            q.set_state(job_id, "rejected", error=msg, error_kind="no_viral")
            q.log(job_id, "no_viral_moments", msg)
            _log(f"Job #{job_id}: sin momentos virales — {msg}")
            return "rejected"

        manifest_path = os.path.join(job["out_dir"], "manifest.json")
        fresh = q.get_job(job_id)
        reds = check_semaphore(fresh | {"manifest_path": manifest_path},
                               manifest=manifest)
        q.set_state(job_id, "ready_for_review",
                    manifest_path=manifest_path, red_flags=reds,
                    error=None, error_kind=None)
        elapsed = time.monotonic() - started
        q.log(job_id, "ready_for_review",
              f"{len(manifest['clips'])} clip(s) en {elapsed:.0f}s; "
              f"rojos del semáforo: {len(reds)}")
        _log(f"Job #{job_id}: ready_for_review — "
             f"{len(manifest['clips'])} clip(s), {len(reds)} rojo(s), "
             f"{elapsed:.0f}s")
        for r in reds:
            _log(f"  rojo: {r}")
        return "ready_for_review"
    except KillSwitchRaised:
        raise
    except Exception as e:  # no dejar el job colgado en processing
        return _fail(q, job, f"error inesperado del worker: {e}",
                     "permanent", started)


def _fail_timeout(q: Queue, job: dict, error: str, started: float):
    """Fallo por timeout: MÁXIMO 1 reintento por timeout.

    Un timeout no es un fallo transitorio cualquiera: si el mismo job
    ya tuvo un timeout antes (contado en su job_log), el segundo va
    directo a 'dead' en vez de reencolarse para siempre.
    """
    job_id = job["id"]
    attempts = job["attempts"] + 1
    elapsed = time.monotonic() - started
    timeouts = q.count_log(job_id, "timeout") + 1
    if timeouts > 1:
        q.set_state(job_id, "dead", attempts=attempts,
                    error=error, error_kind="timeout")
        q.log(job_id, "timeout",
              f"2º timeout en {elapsed:.0f}s: {error[:300]}")
        q.log(job_id, "dead",
              "timeout repetido (máx. 1 reintento por timeout)")
        _log(f"Job #{job_id}: dead (2º timeout, {attempts} intento(s)) — "
             f"{error[:200]}")
        return "dead"
    q.set_state(job_id, "queued", attempts=attempts,
                error=error, error_kind="timeout")
    q.log(job_id, "timeout",
          f"timeout en {elapsed:.0f}s (reintento 1/1 por timeout): "
          f"{error[:300]}")
    _log(f"Job #{job_id}: timeout, vuelve a queued "
         f"(reintento 1/1 por timeout, {attempts} intento(s)) — "
         f"{error[:200]}")
    return "queued"


def _fail(q: Queue, job: dict, error: str, kind: str, started: float):
    """Registra un fallo: reintento (transitorio, <3) o failed/dead."""
    job_id = job["id"]
    attempts = job["attempts"] + 1
    elapsed = time.monotonic() - started
    if kind == "transient" and attempts < MAX_ATTEMPTS:
        q.set_state(job_id, "queued", attempts=attempts,
                    error=error, error_kind=kind)
        q.log(job_id, "retry_queued",
              f"fallo transitorio ({attempts}/{MAX_ATTEMPTS}): {error[:300]}")
        _log(f"Job #{job_id}: fallo transitorio, vuelve a queued "
             f"({attempts}/{MAX_ATTEMPTS}) — {error[:200]}")
        return "queued"
    state = "dead" if kind == "transient" else "failed"
    q.set_state(job_id, state, attempts=attempts,
                error=error, error_kind=kind)
    q.log(job_id, state,
          f"{kind} tras {attempts} intento(s) en {elapsed:.0f}s: "
          f"{error[:300]}")
    _log(f"Job #{job_id}: {state} ({kind}, {attempts} intento(s)) — "
         f"{error[:200]}")
    return state


def run_worker(db_path: str, once: bool = False, max_jobs: int | None = None,
               top_n: int = 5, timeout_s: int = DEFAULT_TIMEOUT_S) -> int:
    """Bucle principal. Devuelve 0 si terminó sin error, 1 si se pausó."""
    if kill_switch_active():
        _log("Kill-switch ACTIVO (queue.kill existe): el worker no arranca. "
             "Bórralo para reanudar.")
        return 1
    q = Queue(db_path)
    try:
        stale = q.requeue_stale_processing()
        if stale:
            _log(f"Recuperados {stale} job(s) en processing de un worker "
                 "muerto: vuelven a queued.")
        done = 0
        while True:
            if kill_switch_active():
                _log("Kill-switch detectado: pausa. Bórralo para reanudar.")
                return 1
            job = q.next_queued()
            if job is None:
                _log("Cola vacía: nada que procesar.")
                return 0
            try:
                process_job(q, job, top_n=top_n, timeout_s=timeout_s)
            except KillSwitchRaised:
                return 1
            done += 1
            if once or (max_jobs is not None and done >= max_jobs):
                return 0
    finally:
        q.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Worker único de la cola: procesa jobs con auto_clips.py.")
    p.add_argument("--db", default=os.path.join(HERE, "queue.db"))
    p.add_argument("--once", action="store_true",
                   help="Procesa un solo job y sale")
    p.add_argument("--max-jobs", type=int, default=None)
    p.add_argument("--top-n", type=int, default=5,
                   help="Clips por VOD (se pasa a auto_clips.py)")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S,
                   help="Segundos máx. por job (default 7200)")
    args = p.parse_args(argv)

    try:
        with single_instance_lock(WORKER_LOCK):
            _log(f"Worker arrancado (pid={os.getpid()}, db={args.db})")
            return run_worker(args.db, once=args.once,
                              max_jobs=args.max_jobs, top_n=args.top_n,
                              timeout_s=args.timeout)
    except RuntimeError:
        _log("Ya hay un worker corriendo (worker.lock bloqueado). "
             "Solo puede haber 1.")
        return 2


if __name__ == "__main__":
    # DEPRECADO (Fase 5.2): usa el CLI único. Se mantiene
    # como wrapper para no romper cron/scripts viejos.
    import sys as _sys
    print("aviso: worker.py está deprecado; usa `clipagent worker`",
          file=_sys.stderr)
    raise SystemExit(main())
