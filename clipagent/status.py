"""Estado por plataforma, visible en el panel (Fase 3.7).

Estados:
- conectada: doctor dice OK (o no hay nada que conectar, como Kick).
- borrador: falta un paso, el token venció o faltan permisos (R5:
  la plataforma cae a borrador y el sistema sigue con las demás).
- pausada: pausa explícita (circuit breaker 6.4 o el usuario en
  el panel). Reanudar es acción explícita (6.4).

Las pausas viven en platform_state.json (estado local, gitignored).
El estado de conexión viene de `doctor` (o de `compute_state` si
no se pasa).
"""
import json
import os
import time

from . import accounts as _accounts

STATE_FILE = "platform_state.json"
PLATFORMS = ("youtube_shorts", "tiktok", "instagram_reels",
             "facebook_reels")


def _state_path(base_dir=None):
    base = base_dir or _accounts.BASE_DIR
    return os.path.join(base, STATE_FILE)


def _load_state(base_dir=None):
    path = _state_path(base_dir)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state, base_dir=None):
    path = _state_path(base_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def pause_platform(platform, reason, base_dir=None):
    """Pausa explícita de una plataforma (circuit breaker o panel)."""
    state = _load_state(base_dir)
    state[platform] = {"paused": True, "reason": reason,
                       "since": time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                              time.localtime())}
    _save_state(state, base_dir)


def resume_platform(platform, base_dir=None):
    """Reanuda una plataforma pausada (acción explícita)."""
    state = _load_state(base_dir)
    if platform in state:
        del state[platform]
        _save_state(state, base_dir)
        return True
    return False


def is_paused(platform, base_dir=None):
    entry = _load_state(base_dir).get(platform) or {}
    return bool(entry.get("paused")), entry.get("reason", "")


def platform_states(base_dir=None, doctor_rows=None):
    """{plataforma: {"state": conectada|borrador|pausada, "motivo"}}.

    doctor_rows es la lista de filas de doctor.run_doctor (o None,
    en cuyo caso se usa el estado calculado sin sondas).
    """
    if doctor_rows is None:
        computed = _accounts.compute_state(base_dir)
        doctor_rows = [
            {"platform": p,
             "status": ("OK" if computed[p]["status"] == "green"
                        else "falta paso"),
             "cause": computed[p]["checks"][0][2]
             if computed[p]["checks"] else ""}
            for p in PLATFORMS]
    out = {}
    for row in doctor_rows:
        platform = row["platform"]
        if platform not in PLATFORMS:
            continue
        paused, reason = is_paused(platform, base_dir)
        if paused:
            out[platform] = {"state": "pausada", "motivo": reason}
        elif row["status"] == "OK":
            out[platform] = {"state": "conectada",
                             "motivo": row.get("cause", "")}
        else:
            out[platform] = {"state": "borrador",
                             "motivo": (f"{row['status']}: "
                                        f"{row.get('cause', '')}")}
    for platform in PLATFORMS:
        out.setdefault(platform, {"state": "borrador",
                                  "motivo": "sin verificar"})
    return out
