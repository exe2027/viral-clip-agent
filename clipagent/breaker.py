"""Circuit breaker por plataforma (Fase 6.4).

N fallos seguidos, un rechazo por copyright/contenido o un strike
=> la plataforma se pausa SOLO ella (status.pause_platform) y se
notifica; no se reintenta a ciegas. Reanudar es una acción
explícita (panel o `clipagent resume`): el breaker nunca se
rearma solo.

Umbral por defecto: 3 fallos seguidos (config.yaml `breaker:`).
El estado vive en circuit_breaker.json (gitignored, estado local).
"""
import json
import os
from datetime import datetime

from .status import is_paused, pause_platform, resume_platform

STATE_FILE = "circuit_breaker.json"
COPYRIGHT_WORDS = ("copyright", "reutiliz", "reuse", "claim", "strike",
                   "contenido", "content id")
DEFAULT_MAX_FAILURES = 3


def _base_dir(base_dir=None):
    return base_dir or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))


def _state_path(base_dir=None):
    return os.path.join(_base_dir(base_dir), STATE_FILE)


def load_state(base_dir=None):
    try:
        with open(_state_path(base_dir), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state, base_dir=None):
    path = _state_path(base_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def max_failures(cfg=None):
    if cfg is None:
        from .config import load_config
        cfg = load_config()
    value = ((cfg or {}).get("breaker") or {}).get(
        "max_consecutive_failures", DEFAULT_MAX_FAILURES)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return DEFAULT_MAX_FAILURES


def is_tripped(platform, base_dir=None):
    """(True, motivo) si el breaker está disparado en esa plataforma."""
    state = load_state(base_dir)
    entry = state.get(platform) or {}
    if entry.get("tripped"):
        return True, entry.get("reason", "breaker disparado")
    return False, ""


def record_success(platform, base_dir=None):
    """Éxito: el contador de fallos seguidos vuelve a cero."""
    state = load_state(base_dir)
    entry = state.setdefault(platform, {})
    entry["consecutive_failures"] = 0
    entry["last_success"] = datetime.now().isoformat()
    save_state(state, base_dir)


def record_failure(platform, reason="", base_dir=None, kind="error",
                   cfg=None):
    """Un fallo. Devuelve {"tripped": bool, "reason": str}.

    kind="copyright" (o un motivo con palabras de copyright/strike)
    dispara el breaker de inmediato. kind="error" suma un fallo
    seguido y dispara al llegar a max_consecutive_failures.
    Al dispararse, la plataforma queda pausada (3.7).
    """
    state = load_state(base_dir)
    entry = state.setdefault(platform, {})
    entry["consecutive_failures"] = int(
        entry.get("consecutive_failures") or 0) + 1
    entry["last_failure"] = datetime.now().isoformat()
    entry["last_reason"] = reason
    is_copyright = kind == "copyright" or any(
        w in (reason or "").lower() for w in COPYRIGHT_WORDS)
    limit = max_failures(cfg)
    tripped = False
    trip_reason = ""
    if is_copyright:
        tripped = True
        trip_reason = (f"rechazo por copyright/contenido: {reason}")
    elif entry["consecutive_failures"] >= limit:
        tripped = True
        trip_reason = (f"{entry['consecutive_failures']} fallos seguidos "
                       f"(límite {limit}): {reason}")
    if tripped:
        entry["tripped"] = True
        entry["reason"] = trip_reason
        entry["tripped_at"] = datetime.now().isoformat()
        # La pausa vive en el mismo base_dir que el estado del
        # breaker (en tests, el temporal; si no, la raíz del repo).
        pause_platform(platform, trip_reason, _base_dir(base_dir))
    save_state(state, base_dir)
    return {"tripped": tripped, "reason": trip_reason}


def reset(platform, base_dir=None, by="usuario"):
    """Reanudar explícito: quita el trip y el contador, y despausa.

    Solo lo hace una persona (panel o CLI): nunca automático.
    """
    state = load_state(base_dir)
    entry = state.setdefault(platform, {})
    entry["tripped"] = False
    entry["consecutive_failures"] = 0
    entry["reason"] = ""
    entry["reset_by"] = by
    entry["reset_at"] = datetime.now().isoformat()
    save_state(state, base_dir)
    resume_platform(platform, _base_dir(base_dir))
    return True


def platform_paused_reason(platform, base_dir=None):
    """Motivo de pausa (breaker o pausa manual), para el panel."""
    tripped, reason = is_tripped(platform, base_dir)
    if tripped:
        return f"breaker: {reason}"
    paused, reason = is_paused(platform, base_dir)
    if paused:
        return reason
    return ""
