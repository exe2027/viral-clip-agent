#!/usr/bin/env python3
"""
config.py — configuración del canal para viral-clip-agent.

Todo lo que es del canal —juego, streamer, nicho, hashtags, estilos
de subtítulo, facecam— vive en un archivo de config, no hardcodeado
en el código. Por eso existen --game, --streamer y --niche en
config.yaml y no HASHTAGS_ES/HASHTAGS_EN fijos en auto_clips.py.

Uso:
    from .config import load_config, get_hashtags, get_subtitle_style
    cfg = load_config()                      # busca config.yaml
    cfg = load_config("mi-canal.yaml")
    tags = get_hashtags(cfg, "es")           # lista de hashtags

Si PyYAML no está instalado, se usa un parser mínimo para el
subconjunto de YAML que genera este proyecto (mapas anidados,
listas con "- " y escalares); si no hay config.yaml, se usan los
valores por defecto de abajo.
"""

import os

DEFAULT_CONFIG = {
    "channel": {
        "name": "El Clip de GTA 6",
        "handle": "@elclipdegta6",
        "game": "GTA 6",
        "streamer": "",
        "niche": "clips de directos",
        "language": "es",
    },
    "hashtags": {
        "es": ["#GTA6", "#GTA6Clips", "#ElClipDeGTA6", "#GTA", "#RockstarGames", "#gaming"],
        "en": ["#GTA6", "#GTA6Clips", "#GTA", "#RockstarGames", "#gaming", "#clips"],
    },
    # Herramientas externas (rutas). Vacío = buscar en el PATH.
    # twitch_downloader_cli: ruta a TwitchDownloaderCLI si no está
    # en el PATH; también vale la variable TWITCH_DL_CLI (manda).
    "tools": {
        "twitch_downloader_cli": "",
    },
    # Juego y lanzamiento (Fase 2.3): la fecha se compara en la zona
    # indicada; un VOD de un juego que no está en "games" es rojo
    # del semáforo y el watcher lo salta. Para añadir otro juego,
    # basta agregar una entrada con sus patrones de categoría.
    "game": {
        "release_date": "2026-11-19",
        "timezone": "America/Santiago",
    },
    "games": [
        {"name": "GTA 6", "id": "gta6",
         "patterns": ["gta 6", "gta vi", "gta6", "grand theft auto vi",
                      "grand theft auto 6"]},
    ],
    "clips": {
        "crop": "center",
        "crf": 23,
        "preset": "fast",
        # Cortes variables (ya no 60 s fijos): objetivo 30 s, rango
        # 21-40 s, tope duro 60 s. Ver config.yaml.
        "duration": 30,
        "target_duration_s": 30,
        "min_duration_s": 21,
        "max_duration_s": 40,
        "precise_cut": True,
        "pre_roll": 20,
        "window": 10,
        "min_separation": 60,
    },
    "subtitles": {
        "default_style": "tiktok",
        "subtitle_lang": "auto",  # auto | es | en
        "translate": True,        # traducir a inglés lo que no sea español
    },
    # Política de traducción (regla del usuario, 2026-11-16):
    # es -> transcribe-only; cualquier otro idioma -> translate-to-en.
    "subtitle_policy": {
        "es": "transcribe-only",
        "others": "translate-to-en",
    },
    "subtitle_styles": {
        "tiktok": {
            "font_color": "#FFFFFF", "border_color": "#000000",
            "border_width": 3, "fontsize": 48, "bold": True,
            "margin_v": 140, "max_chars": 20, "max_duration": 2.0,
            "box": False,
        },
        "highlight": {
            "font_color": "#FFFF00", "border_color": "#000000",
            "border_width": 2, "fontsize": 52, "bold": True,
            "margin_v": 140, "max_chars": 20, "max_duration": 2.0,
            "box": True,
        },
        "karaoke": {
            "font_color": "#FFFF00", "border_color": "#000000",
            "border_width": 3, "fontsize": 56, "bold": True,
            "margin_v": 140, "max_chars": 8, "max_duration": 1.0,
            "box": False,
        },
    },
    # Facecam opcional (auditoría: layout vertical con facecam, fase
    # siguiente). x, y, w, h en píxeles del vídeo original; null = sin facecam.
    "facecam_box": None,
    # Auto-publicación (bloque 10 de la revisión interna, 2026-11-16; ver
    # AUTOPUBLISH.md y counter.py). Umbrales a validar por el usuario.
    "autopublish": {
        "threshold": 15,            # clips seguidos sin editar y sin fallos
        "min_days_per_phase": 3,    # mínimo 3-5 días por fase
        "min_clips_per_phase": 10,  # mínimo 10 clips por fase
        "veto_window_minutes": {    # fase 3: ventana de veto por plataforma
            "youtube": 30,
            "default": 120,         # TikTok / Instagram / Facebook
        },
        # Sin "whitelist" aquí: la lista blanca canónica es
        # influencers.yaml + allowed_streamers.yaml (la lee
        # publisher.load_whitelist(); jobqueue.py usa además la clave
        # "allowlist" de nivel superior en config.yaml).
        "metrics_green": False,     # true solo con métricas en verde (revisión manual)
        # Modo de arranque (6.7): "gradual" conserva las fases y el
        # contador (nada automático hasta la fase 4); "full" publica
        # según la política 6.3 sin pasar por las fases.
        "mode": "gradual",
    },
    # Umbrales duros del sistema de puntaje (6B.2): los valores por
    # defecto son los de priority.py (90/50). Si se bajan, queda
    # registrado con fecha en scoring_changes.log.
    "scoring": {
        "seo_gate": 90,
        "viral_min": 50,
        "audio_weight": 1.0,
        "audio_weight_kick": 1.5,
    },
    # Aprobación automática por política (6.3): sin humano por clip.
    # min_band: null = sin requisito de banda; descartar | revisar |
    # prioritario | urgente (bandas de viral_score).
    "auto_approve": {
        "enabled": True,
        "min_band": None,
    },
    # Metadata del paquete (7.5): crédito al streamer en la descripción.
    "metadata": {
        "credit_streamer": True,
    },
    # Directos en vivo (9.2).
    "live": {
        "cooldown_minutes": 10,
        "max_clips_per_stream": 5,
        "segment_minutes": 5,
    },
    # Circuit breaker por plataforma (6.4): N fallos seguidos o un
    # rechazo por copyright/strike pausan SOLO esa plataforma.
    "breaker": {
        "max_consecutive_failures": 3,
    },
}


def _merge(base, override):
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _parse_simple_yaml(text):
    """Parser mínimo para el subconjunto de YAML de config.yaml.

    Soporta mapas anidados por indentación, listas "- item" y
    escalares (strings con/sin comillas, ints, floats, bool, null).
    """
    def scalar(raw):
        raw = raw.strip()
        if raw in ("null", "~", ""):
            return None
        if raw in ("true", "True"):
            return True
        if raw in ("false", "False"):
            return False
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            return raw[1:-1]
        try:
            return int(raw)
        except ValueError:
            pass
        try:
            return float(raw)
        except ValueError:
            pass
        return raw

    lines = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, raw.strip()))
    pos = [0]

    def parse_block(indent):
        if pos[0] >= len(lines):
            return None
        if lines[pos[0]][1].startswith("- "):
            items = []
            while pos[0] < len(lines) and lines[pos[0]][0] == indent and lines[pos[0]][1].startswith("- "):
                items.append(scalar(lines[pos[0]][1][2:]))
                pos[0] += 1
            return items
        obj = {}
        while pos[0] < len(lines) and lines[pos[0]][0] == indent and not lines[pos[0]][1].startswith("- "):
            _ind, content = lines[pos[0]]
            if ":" not in content:
                pos[0] += 1
                continue
            key, _, value = content.partition(":")
            pos[0] += 1
            if value.strip():
                obj[key.strip()] = scalar(value)
            elif pos[0] < len(lines) and lines[pos[0]][0] > indent:
                obj[key.strip()] = parse_block(lines[pos[0]][0])
            else:
                obj[key.strip()] = None
        return obj

    return parse_block(lines[0][0]) if lines else {}


def load_dotenv(path=None):
    """Carga un .env sin pisar el entorno real.

    Delegado en clipagent.credentials (Fase 3.1): la única puerta
    de acceso a secretos. Se mantiene este nombre por compatibilidad.
    """
    from .credentials import load_dotenv as _load_dotenv
    return _load_dotenv(path)


def load_config(path=None):
    """Carga config.yaml (o la ruta dada) sobre los valores por defecto."""
    load_dotenv()
    cfg = DEFAULT_CONFIG
    if path is None:
        candidates = [
            os.path.join(os.getcwd(), "config.yaml"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml"),
        ]
        path = next((p for p in candidates if os.path.isfile(p)), None)
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        try:
            import yaml
            data = yaml.safe_load(text) or {}
        except ImportError:
            data = _parse_simple_yaml(text)
        cfg = _merge(DEFAULT_CONFIG, data)
    # Fase 5.4: la config se valida al arrancar; un error nombra la
    # clave exacta (ConfigError). Las claves desconocidas solo avisan.
    from .validate import validate_config, warn_unknown
    warn_unknown(validate_config(cfg, source=path or "config.yaml"))
    return cfg


def get_hashtags(cfg, lang="es"):
    """Hashtags del canal para ese idioma (de config.yaml, no del código)."""
    tags = (cfg.get("hashtags") or {}).get(lang)
    if not tags:
        tags = (cfg.get("hashtags") or {}).get("es", [])
    return list(tags)


def get_subtitle_style(cfg, name=None):
    """Estilo de subtítulo por nombre (default: subtitles.default_style)."""
    styles = cfg.get("subtitle_styles") or {}
    default = (cfg.get("subtitles") or {}).get("default_style", "tiktok")
    name = name or default
    style = dict(styles.get(name) or styles.get(default) or styles.get("tiktok") or {})
    style["name"] = name
    return style


def list_subtitle_styles(cfg):
    return list((cfg.get("subtitle_styles") or {}).keys())


def get_subtitle_policy(cfg):
    """Política de traducción de subtítulos de config.yaml.

    Devuelve {"es": "transcribe-only", "others": "translate-to-en"}.
    """
    policy = (cfg.get("subtitle_policy") or {})
    return {
        "es": policy.get("es", "transcribe-only"),
        "others": policy.get("others", "translate-to-en"),
    }


def resolve_subtitle_task(detected_lang, subtitle_lang="auto",
                          translate=None, policy=None):
    """Decide la tarea de faster-whisper según la política de idiomas.

    Regla del usuario (2026-11-16): español -> solo transcribe, sin
    traducir; cualquier otro idioma -> traducir a inglés.

    Devuelve (task, subtitle_language):
      * task: "transcribe" o "translate" (a inglés, lo único que
        Whisper traduce).
      * subtitle_language: idioma de los subtítulos resultantes
        ("es" si se transcribe español, "en" si se traduce, o el
        idioma detectado si no se traduce).
    subtitle_lang: "auto" (aplica la política), "es" (fuerza
    subtítulos en español: solo vale si el audio es español, Whisper
    no traduce HACIA español) o "en" (fuerza salida en inglés,
    traduce si el audio no es inglés; es una anulación explícita
    del usuario).
    translate: True/False fuerza o bloquea la traducción; None usa
    la política (others=translate-to-en -> True).
    """
    policy = policy or {"es": "transcribe-only", "others": "translate-to-en"}
    lang = (detected_lang or "").lower()
    # Regla dura del usuario: el español NUNCA se traduce, ni con
    # --subtitle-lang en ni con --translate explícitos.
    if lang == "es":
        return "transcribe", "es"
    if subtitle_lang == "es":
        # Whisper solo traduce a inglés, nunca a español: si el audio
        # no es español, se transcribe en su idioma (no se inventa
        # una traducción al español).
        return "transcribe", lang
    if subtitle_lang == "en":
        return ("transcribe", "en") if lang == "en" else ("translate", "en")
    # auto: aplica la política
    if lang == "en":
        # Ya está en inglés: no hay nada que traducir.
        return "transcribe", "en"
    if translate is False:
        return "transcribe", lang
    if translate is True or policy.get("others") == "translate-to-en":
        return "translate", "en"
    return "transcribe", lang


def get_layout_config(cfg=None):
    """Config de layout responsive (layout.py) desde config.yaml.

    Mezcla los DEFAULTS de layout.py con la sección ``layout`` de
    config.yaml. ``facecam_box`` de nivel superior (legado) también
    se respeta: si está puesto, manda sobre la detección.
    """
    cfg = cfg or load_config()
    try:
        from .render import DEFAULTS as _LAYOUT_DEFAULTS
    except ImportError:
        _LAYOUT_DEFAULTS = {"mode": "auto", "sample_every_s": 5,
                            "min_segment_s": 5, "pip_width": 480,
                            "pip_corner": "top-right", "pip_margin": 24}
    merged = dict(_LAYOUT_DEFAULTS)
    merged.update((cfg or {}).get("layout") or {})
    if (cfg or {}).get("facecam_box") and not merged.get("facecam_box"):
        merged["facecam_box"] = cfg["facecam_box"]
    return merged


def get_autopublish_config(cfg=None):
    """Config de auto-publicación (counter.py) desde config.yaml.

    Mezcla los defaults de ``autopublish`` con la sección
    ``autopublish`` de config.yaml. Devuelve un dict con:
    threshold, min_days_per_phase, min_clips_per_phase,
    veto_window_minutes {youtube, default}, metrics_green.
    (La lista blanca NO vive aquí: la canónica es influencers.yaml +
    allowed_streamers.yaml, leída por publisher.load_whitelist().)
    """
    cfg = cfg or load_config()
    merged = dict(DEFAULT_CONFIG["autopublish"])
    merged.update((cfg or {}).get("autopublish") or {})
    veto = dict(DEFAULT_CONFIG["autopublish"]["veto_window_minutes"])
    veto.update(((cfg or {}).get("autopublish") or {}).get("veto_window_minutes") or {})
    merged["veto_window_minutes"] = veto
    return merged
