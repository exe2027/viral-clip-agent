"""Checkpoint por job (Fase 5.5): etapas idempotentes.

Cada corrida de auto_clips.py guarda en runs/<vod>/state.json qué
etapas completó. Si el job muere (timeout del worker, kill-switch,
caída) y se reintenta, reanuda desde la última etapa completa en
vez de rehacerlo todo: el chat ya descargado no se vuelve a bajar y
los clips ya cortados se reutilizan desde el manifiesto anterior.

Granularidad:
- `chat`: el chat.json ya está en runs/<vod>/ (solo cuando el chat
  vive en el out_dir, no con --chat externo).
- `detect`: solo observabilidad (detectar es barato y se recalcula).
- por clip (`peak_<ts>`): el clip está completo (corte + ficha +
  paquete + entrada en el manifiesto). La clave es el pico, no el
  índice: si cambia --top-n, los picos que siguen valen igual.
- `manifest`: manifiesto final escrito.

Un clip "hecho" solo se reutiliza si su archivo existe en disco y
el manifiesto anterior trae su entrada: el estado solo no basta.
"""
import json
import os

STATE_FILE = "state.json"
MANIFEST_FILE = "manifest.json"


def _state_path(out_dir):
    return os.path.join(out_dir, STATE_FILE)


def load_state(out_dir):
    """Estado del checkpoint ({} si no existe o está corrupto)."""
    try:
        with open(_state_path(out_dir), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(out_dir, state):
    """Escritura atómica (temporal + os.replace), como el manifiesto."""
    os.makedirs(out_dir, exist_ok=True)
    tmp_path = _state_path(out_dir) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, _state_path(out_dir))
    return _state_path(out_dir)


def clip_key(peak_ts):
    return f"peak_{peak_ts}"


def stage_done(state, name):
    return bool((state.get("stages") or {}).get(name, {}).get("done"))


def mark_stage(state, name, **detail):
    stages = state.setdefault("stages", {})
    entry = stages.setdefault(name, {})
    entry["done"] = True
    entry.update(detail)
    return state


def clip_done(state, peak_ts):
    return bool((state.get("clips") or {}).get(clip_key(peak_ts),
                                               {}).get("done"))


def mark_clip_done(state, peak_ts, fname):
    clips = state.setdefault("clips", {})
    clips[clip_key(peak_ts)] = {"done": True, "file": f"clips/{fname}"}
    return state


def load_manifest(out_dir):
    """Manifiesto anterior (None si no existe o está corrupto)."""
    try:
        with open(os.path.join(out_dir, MANIFEST_FILE),
                  encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def resume_entry(manifest, peak_ts):
    """Entrada del clip de ese pico en el manifiesto anterior."""
    if not manifest:
        return None
    for entry in manifest.get("clips") or []:
        if entry.get("peak") == peak_ts:
            return entry
    return None
