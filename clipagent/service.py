"""Servicio del sistema (Fase 5.1 / 6.1): gestión de procesos.

Contiene lo que antes vivía en worker.py y que comparten el worker
y el orquestador: matar el árbol entero de un hijo y el lock de
instancia única por flock. Sin cambios de lógica: solo mudanza.
"""
import fcntl
import os
import signal
import subprocess
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
