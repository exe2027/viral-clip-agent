"""Una sola lista de streamers (Fase 4.1).

`streamers.yaml` es la ÚNICA lista: la leen el watcher, la cola y
el publicador. Las tres listas viejas (config.yaml/allowlist,
influencers.yaml, allowed_streamers.yaml) quedan como respaldo de
solo lectura si streamers.yaml no existe; no se editan más.

REGLA DEL USUARIO: estar en la lista = permiso ya dado. Solo
`denegado` / `revocado` excluyen. `evidencia_url` es opcional.
"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STREAMERS_FILE = os.path.join(BASE_DIR, "streamers.yaml")

HEADER = """# streamers.yaml — la ÚNICA lista de streamers (Fase 4.1).
#
# REGLA DEL USUARIO (2026-11-16): estar en esta lista = permiso ya
# dado por el hecho de que el usuario lo subió. NO hay ficha de
# permiso que registrar ni contacto previo. Solo `denegado` /
# `revocado` excluyen al streamer (el usuario lo sacó de la lista).
# `evidencia_url` es OPCIONAL: un enlace donde quedó el permiso,
# si lo hay.
#
"""

PLATFORMS = ("twitch", "kick")
PUBLISH_PLATFORMS = ("youtube", "tiktok", "instagram", "facebook")
EXCLUDED_STATES = ("denegado", "revocado")


def _norm(entry):
    """Normaliza una entrada a la forma canónica."""
    name = str(entry.get("name") or entry.get("usuario") or "").strip().lower()
    platform = str(entry.get("platform") or entry.get("plataforma")
                    or "twitch").strip().lower()
    publish = entry.get("publish_platforms")
    if publish is None:
        alcance = entry.get("alcance") or {}
        publish = alcance.get("plataformas_publicacion")
    if publish is None:
        publish = list(PUBLISH_PLATFORMS)
    return {
        "name": name,
        "platform": platform,
        "channel_url": str(entry.get("channel_url")
                           or entry.get("canal_original") or "").strip(),
        "game": str(entry.get("game") or entry.get("juego") or "").strip(),
        "language": str(entry.get("language") or entry.get("idioma")
                        or "es").strip().lower() or "es",
        "estado": str(entry.get("estado") or "pendiente").strip().lower(),
        "fecha_permiso": str(entry.get("fecha_permiso")
                             or entry.get("granted") or "").strip(),
        "evidencia_url": str(entry.get("evidencia_url") or "").strip(),
        "publish_platforms": [str(p).strip().lower() for p in publish],
        "notas": str(entry.get("notas") or entry.get("notes") or "").strip(),
    }


def _load_yaml(path):
    try:
        import yaml
    except ImportError:
        return {}
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _legacy_streamers(base_dir):
    """Las tres listas viejas, fusionadas (respaldo si no existe
    streamers.yaml)."""
    merged = {}
    data = _load_yaml(os.path.join(base_dir, "influencers.yaml"))
    for entry in data.get("influencers") or []:
        norm = _norm(entry)
        if norm["name"]:
            merged[norm["name"]] = norm
    data = _load_yaml(os.path.join(base_dir, "allowed_streamers.yaml"))
    for entry in data.get("streamers") or []:
        norm = _norm(entry)
        if norm["name"]:
            merged.setdefault(norm["name"], norm)
    try:
        from .config import load_config
        cfg = load_config(os.path.join(base_dir, "config.yaml")) or {}
        for name in (cfg.get("allowlist") or []):
            name = str(name).strip().lower()
            if name and name not in merged:
                merged[name] = _norm({"name": name,
                                      "fecha_permiso": "2026-11-16"})
    except Exception:
        pass
    return list(merged.values())


def load_streamers(path=None, base_dir=None):
    """La lista única, normalizada. [] si no hay ninguna lista."""
    base = base_dir or BASE_DIR
    path = path or os.path.join(base, "streamers.yaml")
    data = _load_yaml(path)
    entries = data.get("streamers")
    if entries is None:
        return _legacy_streamers(base)
    out = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        norm = _norm(entry)
        if norm["name"]:
            out.append(norm)
    return out


def get_streamer(name, streamers=None, base_dir=None):
    """Una entrada por nombre (minúsculas), o None."""
    name = (name or "").strip().lower()
    for entry in (streamers if streamers is not None
                  else load_streamers(base_dir=base_dir)):
        if entry["name"] == name:
            return entry
    return None


def is_allowed(name, streamers=None, base_dir=None):
    """True si está en la lista y su estado no es denegado/revocado."""
    entry = get_streamer(name, streamers, base_dir)
    if entry is None:
        return False
    return entry["estado"] not in EXCLUDED_STATES


def _save(streamers, path):
    import yaml
    with open(path, "w", encoding="utf-8") as f:
        f.write(HEADER)
        yaml.safe_dump({"streamers": streamers}, f, allow_unicode=True,
                       sort_keys=False)


def add_streamer(entry, path=None, base_dir=None):
    """Agrega un streamer. Devuelve (ok, mensaje).

    Valida: nombre (slug) y plataforma twitch|kick. Si ya existe,
    no se duplica (se devuelve su estado actual).
    """
    base = base_dir or BASE_DIR
    path = path or os.path.join(base, "streamers.yaml")
    norm = _norm(entry)
    if not norm["name"]:
        return False, "falta el nombre del streamer"
    if norm["platform"] not in PLATFORMS:
        return False, (f"plataforma desconocida: {norm['platform']!r} "
                       "(twitch o kick)")
    current = load_streamers(path, base)
    if any(e["name"] == norm["name"] for e in current):
        return False, f"{norm['name']} ya está en la lista"
    current.append(norm)
    _save(current, path)
    return True, f"{norm['name']} agregado a streamers.yaml"


def remove_streamer(name, path=None, base_dir=None):
    """Quita un streamer de la lista. Devuelve True si existía.

    Quitarlo bloquea nuevas subidas aunque haya clips aprobados:
    publisher.check_whitelist relee la lista en cada publicación.
    """
    base = base_dir or BASE_DIR
    path = path or os.path.join(base, "streamers.yaml")
    name = (name or "").strip().lower()
    current = load_streamers(path, base)
    kept = [e for e in current if e["name"] != name]
    if len(kept) == len(current):
        return False
    _save(kept, path)
    return True
