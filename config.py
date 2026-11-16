#!/usr/bin/env python3
"""
config.py — configuración del canal para viral-clip-agent.

Idea tomada de Shortia (backsoul/shortia, Apache 2.0): todo lo que es
del canal —juego, streamer, nicho, hashtags, estilos de subtítulo,
facecam— vive en un archivo de config, no hardcodeado en el código.
La auditoría pedía lo mismo: sacar HASHTAGS_ES/HASHTAGS_EN fijos de
``auto_clips.py`` y poner ``--game``, ``--streamer`` y ``--niche``
en un config.yaml.

Uso:
    from config import load_config, get_hashtags, get_subtitle_style
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
    "clips": {
        "crop": "center",
        "crf": 23,
        "preset": "fast",
        "duration": 60,
        "pre_roll": 20,
        "window": 10,
        "min_separation": 60,
    },
    "subtitles": {
        "default_style": "tiktok",
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


def load_config(path=None):
    """Carga config.yaml (o la ruta dada) sobre los valores por defecto."""
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
