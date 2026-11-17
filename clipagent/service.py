"""Servicio del sistema (Fase 5.1 / 5.2 / 6.1): gestión de procesos.

Contiene lo que antes vivía en worker.py y que comparten el worker
y el orquestador: matar el árbol entero de un hijo, el lock de
instancia única por flock y el supervisor de `clipagent start`
(watcher + worker; el bucle del publicador se suma en 6.3, cuando
exista la política de auto-publicación).
"""
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager


def kill_process_group(proc):
    """Mata el árbol entero del hijo (auto_clips + sus ffmpeg).

    El hijo corre en su propio grupo de procesos (start_new_session):
    SIGTERM al grupo, 10 s de gracia, luego SIGKILL al grupo. Matar
    solo el padre dejaría huérfanos los ffmpeg de los cortes.
    """
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()


@contextmanager
def single_instance_lock(lock_path):
    """Lock de instancia única por flock (no bloqueante).

    Levanta RuntimeError si ya hay otra instancia. El archivo NO se
    borra al salir: solo se suelta el flock. Borrarlo tras soltarlo
    es una carrera (otro proceso puede tomarlo entre el unlock y el
    remove, y entonces se borra un lock en uso). El lock real es el
    flock, no la existencia del archivo.
    """
    lock_f = open(lock_path, "w")
    try:
        fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_f.close()
        raise RuntimeError(
            f"ya hay una instancia corriendo (lock bloqueado: {lock_path}). "
            "Solo puede haber 1.")
    try:
        yield
    finally:
        fcntl.flock(lock_f, fcntl.LOCK_UN)
        lock_f.close()


# ---------------------------------------------------------------------------
# Supervisor de `clipagent start` (Fase 5.2; la resiliencia llega en 6.1)
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE_PID_FILE = os.path.join(REPO_ROOT, "service.pid")
KILL_SWITCH_PATH = os.path.join(REPO_ROOT, "queue.kill")

# Hijos del servicio (6.1/6.3): watcher, worker y el bucle del
# publicador (con --live: publica de verdad según la política 6.3;
# en modo gradual, nada se publica hasta la fase 4 de counter.py).
DEFAULT_CHILDREN = [
    ("watcher", [sys.executable, "watcher.py", "--loop", "--live"]),
    ("worker", [sys.executable, "worker.py"]),
    ("publisher", [sys.executable, "-m", "clipagent", "publish",
                   "loop", "--live"]),
]

# Reinicios: si un hijo muere más de MAX_RESTARTS veces en
# RESTART_WINDOW_S, se deja de reiniciarlo (bucle de caídas).
MAX_RESTARTS = 5
RESTART_WINDOW_S = 600
RESTART_DELAY_S = 5


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _kill_group(pid, timeout=5):
    """SIGTERM al grupo del pid, luego SIGKILL si sigue vivo."""
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.2)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _read_pid_file(pid_file=SERVICE_PID_FILE):
    try:
        with open(pid_file, encoding="utf-8") as f:
            data = json.load(f)
        return data
    except (OSError, ValueError):
        return None


def _write_pid_file(children, pid_file=SERVICE_PID_FILE):
    data = {"supervisor_pid": os.getpid(),
            "children": {name: p.pid for name, p in children.items()}}
    with open(pid_file, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def service_running(pid_file=SERVICE_PID_FILE):
    """True si el supervisor del PID file sigue vivo."""
    data = _read_pid_file(pid_file)
    return bool(data and _pid_alive(data.get("supervisor_pid", 0)))


def recover_stale_jobs(db_path=None):
    """Jobs colgados en 'processing' de un worker que murió.

    Se llama al arrancar el servicio, antes de levantar el worker
    (ningún worker vivo los está procesando): vuelven a 'queued'
    sin gastar intento. Devuelve cuántos se recuperaron.
    """
    from jobqueue import Queue
    q = Queue(db_path) if db_path else Queue()
    try:
        return q.requeue_stale_processing()
    finally:
        q.close()


def run_supervisor(children_spec=None, pid_file=SERVICE_PID_FILE,
                   stop_event=None, restart_delay=RESTART_DELAY_S,
                   max_restarts=MAX_RESTARTS):
    """Bucle del supervisor: levanta los hijos y reinicia los caídos.

    Devuelve {nombre: nº de reinicios} al parar. Cada hijo corre en
    su propio grupo de procesos (start_new_session=True) para poder
    matarlo entero con killpg. `stop_event` (threading.Event) permite
    pararlo desde tests; en producción se para con SIGINT/SIGTERM.
    """
    import threading
    if stop_event is None:
        stop_event = threading.Event()
    children = {}
    restarts = {}
    crash_times = {}

    def spawn(name, argv):
        proc = subprocess.Popen(argv, cwd=REPO_ROOT,
                                start_new_session=True)
        children[name] = proc
        return proc

    spec = children_spec if children_spec is not None else DEFAULT_CHILDREN
    for name, argv in spec:
        spawn(name, argv)
        restarts[name] = 0
        crash_times[name] = []
    _write_pid_file(children, pid_file)

    def _stop_handler(signum, frame):
        stop_event.set()

    # signal.signal solo funciona en el hilo principal: en tests el
    # supervisor corre en un hilo y se para con stop_event.
    in_main_thread = threading.current_thread() is threading.main_thread()
    old_int = old_term = None
    if in_main_thread:
        old_int = signal.signal(signal.SIGINT, _stop_handler)
        old_term = signal.signal(signal.SIGTERM, _stop_handler)
    try:
        while not stop_event.is_set():
            time.sleep(1)
            if os.path.exists(KILL_SWITCH_PATH):
                # El usuario tocó queue.kill a mano: parada de
                # emergencia; el supervisor sale y mata a los hijos.
                print("[supervisor] kill-switch activo (queue.kill): "
                      "parando todo el árbol.")
                break
            if not children:
                # Todos los hijos descartados por demasiadas caídas:
                # no queda nada que supervisar.
                print("[supervisor] sin hijos vivos, el servicio se para")
                break
            for name, argv in spec:
                proc = children.get(name)
                if proc is None:
                    continue  # hijo descartado por demasiadas caídas
                if proc.poll() is not None:
                    now = time.time()
                    crash_times[name] = [t for t in crash_times[name]
                                         if now - t < RESTART_WINDOW_S]
                    crash_times[name].append(now)
                    if len(crash_times[name]) > max_restarts:
                        print(f"[supervisor] {name}: demasiadas caídas "
                              f"({max_restarts} en 10 min), no se reinicia")
                        del children[name]
                        continue
                    restarts[name] += 1
                    print(f"[supervisor] {name} cayó (rc={proc.returncode}), "
                          f"reinicio {restarts[name]} en {restart_delay}s")
                    time.sleep(restart_delay)
                    if stop_event.is_set():
                        break
                    spawn(name, argv)
                    _write_pid_file(children, pid_file)
    finally:
        if in_main_thread:
            signal.signal(signal.SIGINT, old_int)
            signal.signal(signal.SIGTERM, old_term)
        for name, proc in list(children.items()):
            if proc.poll() is None:
                _kill_group(proc.pid)
        try:
            os.remove(pid_file)
        except OSError:
            pass
    return restarts


def start_service(children_spec=None, pid_file=SERVICE_PID_FILE):
    """Arranca el servicio en primer plano (bloquea hasta parar).

    Devuelve 0 al parar limpio, 2 si ya hay un servicio corriendo.
    Si existe el kill-switch (queue.kill) creado a mano, no arranca:
    es el freno de emergencia del usuario (6.1).
    """
    if os.path.exists(KILL_SWITCH_PATH):
        print("kill-switch activo (queue.kill): el servicio no arranca. "
              "Bórralo a mano si quieres arrancar de nuevo.")
        return 2
    if service_running(pid_file):
        print("el servicio ya está corriendo "
              f"(ver {os.path.basename(pid_file)}). Usa `clipagent stop`.")
        return 2
    # Jobs colgados del worker anterior (crash/reinicio): vuelven a
    # la cola sin gastar intento, antes de levantar el worker nuevo.
    recovered = recover_stale_jobs()
    if recovered:
        print(f"servicio: {recovered} job(s) colgado(s) recuperados "
              "a la cola.")
    # Sesión propia: el supervisor es líder de su grupo, así que
    # `clipagent stop` puede matar su grupo sin tocar la terminal
    # desde la que se lanzó.
    try:
        os.setsid()
    except OSError:
        pass
    print(f"servicio arrancado (pid={os.getpid()}); "
          "Ctrl+C o `clipagent stop` para parar todo el árbol.")
    run_supervisor(children_spec, pid_file)
    print("servicio parado.")
    return 0


def stop_service(pid_file=SERVICE_PID_FILE, wait_s=30):
    """Para todo el árbol: kill-switch, SIGTERM a los grupos y SIGKILL.

    Usa queue.kill para que el worker aborte el job en curso (1.4:
    el worker mata auto_clips al ver el kill-switch) y luego mata
    los grupos del supervisor y de cada hijo. El kill-switch se
    quita al final: `stop` es una acción explícita del operador, no
    una emergencia; el freno manual sigue siendo crear queue.kill.
    Devuelve 0 si paró algo, 2 si no había servicio corriendo.
    """
    data = _read_pid_file(pid_file)
    if not data or not _pid_alive(data.get("supervisor_pid", 0)):
        print("no hay servicio corriendo.")
        return 2
    supervisor_pid = data["supervisor_pid"]
    child_pids = list((data.get("children") or {}).values())
    # 1) kill-switch: el worker aborta el job en curso limpio.
    with open(KILL_SWITCH_PATH, "w", encoding="utf-8") as f:
        f.write("parada por `clipagent stop`\n")
    time.sleep(3)
    # 2) SIGTERM al grupo del supervisor (sale limpio y mata a sus
    # hijos) y, por seguridad, a cada hijo que quede vivo.
    _kill_group(supervisor_pid)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if not _pid_alive(supervisor_pid):
            break
        time.sleep(0.5)
    for pid in child_pids:
        if _pid_alive(pid):
            _kill_group(pid)
    try:
        os.remove(pid_file)
    except OSError:
        pass
    try:
        os.remove(KILL_SWITCH_PATH)
    except OSError:
        pass
    print("servicio parado (todo el árbol).")
    return 0


def service_status(pid_file=SERVICE_PID_FILE):
    """Estado del servicio, la cola y las plataformas (dict)."""
    from jobqueue import Queue
    data = _read_pid_file(pid_file)
    running = bool(data and _pid_alive(data.get("supervisor_pid", 0)))
    children = {}
    if data:
        for name, pid in (data.get("children") or {}).items():
            children[name] = {"pid": pid, "vivo": _pid_alive(pid)}
    q = Queue()
    try:
        counts = q.counts()
    finally:
        q.close()
    from .status import platform_states
    return {"corriendo": running,
            "supervisor_pid": data.get("supervisor_pid") if data else None,
            "hijos": children,
            "cola": counts,
            "plataformas": platform_states()}

