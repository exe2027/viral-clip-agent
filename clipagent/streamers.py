"""Lista de streamers por tenant (Fase 4.1, genérica 2026-11-19).

FUENTE DE VERDAD: el panel (GET /api/influencers del tenant).
`load_streamers()` la pide al panel cuando hay bridge o
PANEL_TENANT en el entorno (el cerebro en GitHub Actions); si el
panel falla, se levanta (nunca se cae en silencio al
streamers.yaml del repo, que es la lista de OTRO tenant).
`streamers.yaml` queda como respaldo local/dev (sin PANEL_TENANT
ni bridge) y para herramientas con `path` explícito.

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

PLATFORMS = ("twitch", "kick", "youtube", "facebook")
# youtube es plataforma válida de streamer (identidad/VOD, ej.
# IShowSpeed), pero el monitoreo EN VIVO del watcher solo cubre
# twitch/kick: un streamer youtube se salta en el barrido en vivo
# con nota visible (ver watcher.py), hasta que haya watcher de
# YouTube Live.
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
        # Interruptor del panel (6.2): False = no se vigila ni se
        # publican sus clips nuevos. Sin la clave, activo.
        "enabled": bool(entry.get("enabled", True)),
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


def _from_panel(rows):
    """Mapea GET /api/influencers a entradas canónicas.

    El panel devuelve {handle, name, platform, profile_url,
    language, permission, active, game, permission_date,
    publish_platforms, notes}. `estado` es "concedido" solo con
    permiso y activo; `enabled` igual (sin permiso o inactivo no
    se vigila ni se publican sus clips nuevos)."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        name = str(r.get("handle") or r.get("name") or "").strip().lower()
        if not name:
            continue
        permission = bool(r.get("permission"))
        active = bool(r.get("active"))
        publish = r.get("publish_platforms") or list(PUBLISH_PLATFORMS)
        out.append({
            "name": name,
            "platform": str(r.get("platform") or "twitch").strip().lower(),
            "channel_url": str(r.get("profile_url") or r.get("url") or "").strip(),
            "game": str(r.get("game") or "").strip(),
            "language": str(r.get("language") or "es").strip().lower() or "es",
            "estado": "concedido" if (permission and active) else "pendiente",
            "fecha_permiso": str(r.get("permission_date") or "").strip(),
            "evidencia_url": "",
            "publish_platforms": [str(p).strip().lower() for p in publish],
            "notas": str(r.get("notes") or "").strip(),
            "enabled": bool(active and permission),
        })
    return out


def load_streamers(path=None, base_dir=None, bridge=None):
    """La lista única, normalizada. [] si no hay ninguna lista.

    Panel primero (2026-11-19, cerebro genérico): con bridge
    explícito, o con PANEL_TENANT en el entorno (el bridge se
    construye de PANEL_BRAIN_TOKEN o del login), la lista sale de
    GET /api/influencers del tenant. Si el panel falla, se levanta:
    nunca se cae en silencio al streamers.yaml del repo (la lista
    de otro tenant). Si PANEL_TENANT está definido pero no hay
    credenciales del panel, también se levanta (una corrida de un
    tenant sin forma de leer SU lista es un error de
    configuración, no un caso para leer la lista de otro).
    Sin PANEL_TENANT ni bridge (local / tests), se lee
    streamers.yaml como antes. Un `path` explícito siempre gana
    (herramientas locales y tests con fixtures).
    """
    if path is None and bridge is None and os.environ.get("PANEL_TENANT"):
        from .panel_bridge import PanelBridge
        bridge = PanelBridge.from_env()
        if bridge is None:
            raise RuntimeError(
                "PANEL_TENANT está definido pero no hay credenciales "
                "del panel (ni PANEL_BRAIN_TOKEN ni login): no se "
                "puede leer la lista de influencers del tenant y no "
                "se cae al streamers.yaml del repo (sería la lista "
                "de otro tenant). Dispara desde el panel con Iniciar.")
    if bridge is not None:
        return _from_panel(bridge.list_influencers())
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


def get_streamer(name, streamers=None, base_dir=None, platform="twitch"):
    """Una entrada por nombre (minúsculas), o None.

    ``platform`` (2026-11-24, corte YouTube): con dos entradas
    del mismo nombre (p. ej. Rubius en twitch y en youtube), se
    devuelve la de esa plataforma. El valor por defecto
    ("twitch") preserva el comportamiento de los llamadores
    existentes; si no hay entrada con esa plataforma, se cae al
    PRIMER match por nombre (comportamiento de antes).
    """
    name = (name or "").strip().lower()
    entries = (streamers if streamers is not None
               else load_streamers(base_dir=base_dir))
    first = None
    for entry in entries:
        if entry["name"] == name:
            if first is None:
                first = entry
            if (entry.get("platform") or "twitch").strip().lower() \
                    == platform:
                return entry
    return first


def is_allowed(name, streamers=None, base_dir=None, platform="twitch"):
    """True si está en la lista y su estado no es denegado/revocado.

    ``platform`` se pasa a ``get_streamer`` (misma persona, dos
    entradas: la de Twitch y la de YouTube se permiten por
    separado).
    """
    entry = get_streamer(name, streamers, base_dir, platform=platform)
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

    Valida: nombre (slug) y plataforma
    twitch|kick|youtube|facebook (2026-11-25, spec entrada
    Facebook §3.3: "facebook" es plataforma válida de streamer).
    Si ya existe, no se duplica (se devuelve su estado actual).
    """
    base = base_dir or BASE_DIR
    path = path or os.path.join(base, "streamers.yaml")
    norm = _norm(entry)
    if not norm["name"]:
        return False, "falta el nombre del streamer"
    if norm["platform"] not in PLATFORMS:
        return False, (f"plataforma desconocida: {norm['platform']!r} "
                       "(twitch, kick, youtube o facebook)")
    current = load_streamers(path, base)
    if any(e["name"] == norm["name"] for e in current):
        return False, f"{norm['name']} ya está en la lista"
    current.append(norm)
    _save(current, path)
    return True, f"{norm['name']} agregado a streamers.yaml"


def set_enabled(name, enabled, path=None, base_dir=None):
    """Interruptor del panel (6.2): activa/desactiva un streamer.

    Desactivado no se vigila (watcher) ni se publican sus clips
    nuevos (la política los retiene). El permiso no se revoca:
    quitarlo de la lista es otra acción (remove_streamer).
    Devuelve True si existía.
    """
    base = base_dir or BASE_DIR
    path = path or os.path.join(base, "streamers.yaml")
    name = (name or "").strip().lower()
    current = load_streamers(path, base)
    found = False
    for e in current:
        if e["name"] == name:
            e["enabled"] = bool(enabled)
            found = True
    if found:
        _save(current, path)
    return found


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
