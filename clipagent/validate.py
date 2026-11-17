"""Validación de config.yaml al arrancar (Fase 5.4).

Sin dependencias nuevas (ni pydantic ni jsonschema): el esquema es
pequeño (~40 claves) y el repo evita dependencias pesadas. El
validador es declarativo: cada regla nombra la clave exacta en el
error, para que el usuario sepa qué arreglar sin adivinar.

Uso: load_config() valida la config fusionada (con los valores por
defecto) y levanta ConfigError si algo está mal. Las claves
ausentes no son error (las cubre el valor por defecto); las claves
desconocidas de nivel superior solo avisan por stderr.
"""
import sys
from datetime import date

PRESETS = {"ultrafast", "superfast", "veryfast", "faster", "fast",
           "medium", "slow", "slower", "veryslow"}
LAYOUT_MODES = {"auto", "stack", "split", "pip", "center"}
PIP_CORNERS = {"top-right", "top-left", "bottom-right", "bottom-left"}
SUBTITLE_LANGS = {"auto", "es", "en"}
POLICY_VALUES = {"transcribe-only", "translate-to-en"}
AUTOPUBLISH_MODES = {"gradual", "full"}

KNOWN_TOP_KEYS = {
    "channel", "hashtags", "tools", "game", "games", "allowlist",
    "clips", "subtitles", "subtitle_policy", "subtitle_styles",
    "layout", "facecam_box", "watcher", "reel_study", "autopublish",
    # Llegan en fases posteriores (6.3, 6B): se validan entonces.
    "auto_approve", "scoring", "breaker", "metadata", "live",
    # Fan-out: se validan abajo.
    "fanout", "public_url", "platform_accounts",
}


class ConfigError(ValueError):
    """Config inválida: el mensaje nombra la clave exacta."""


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return (_is_int(v) or isinstance(v, float)) and not isinstance(v, bool)


class _Checker:
    def __init__(self):
        self.errors = []

    def err(self, path, msg):
        self.errors.append(f"{path}: {msg}")

    def get(self, cfg, path):
        """(existe, valor) siguiendo un camino con puntos."""
        cur = cfg
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return False, None
            cur = cur[part]
        return True, cur

    def check(self, cfg, path, kind, required=False, choices=None,
              minimum=None, maximum=None):
        found, v = self.get(cfg, path)
        if not found:
            if required:
                self.err(path, "falta (clave obligatoria)")
            return None
        if kind == "str":
            if not isinstance(v, str):
                self.err(path, f"debe ser texto, es {type(v).__name__}")
                return None
        elif kind == "int":
            if not _is_int(v):
                self.err(path, f"debe ser entero, es {type(v).__name__}")
                return None
        elif kind == "num":
            if not _is_num(v):
                self.err(path, f"debe ser número, es {type(v).__name__}")
                return None
        elif kind == "bool":
            if not isinstance(v, bool):
                self.err(path, f"debe ser true/false, es {type(v).__name__}")
                return None
        elif kind == "list":
            if not isinstance(v, list):
                self.err(path, f"debe ser lista, es {type(v).__name__}")
                return None
        elif kind == "dict":
            if not isinstance(v, dict):
                self.err(path, f"debe ser un mapa, es {type(v).__name__}")
                return None
        if choices is not None and v not in choices:
            self.err(path, f"debe ser uno de {sorted(choices)}, es {v!r}")
        if minimum is not None and _is_num(v) and v < minimum:
            self.err(path, f"debe ser >= {minimum}, es {v}")
        if maximum is not None and _is_num(v) and v > maximum:
            self.err(path, f"debe ser <= {maximum}, es {v}")
        return v

    def check_str_list(self, cfg, path):
        v = self.check(cfg, path, "list")
        if v is not None:
            for i, item in enumerate(v):
                if not isinstance(item, str):
                    self.err(f"{path}[{i}]",
                             f"debe ser texto, es {type(item).__name__}")


def validate_config(cfg, source="config.yaml"):
    """Valida la config fusionada. Levanta ConfigError con TODOS los
    errores (cada uno nombra su clave exacta). Devuelve la lista de
    avisos (claves desconocidas de nivel superior)."""
    if not isinstance(cfg, dict):
        raise ConfigError(f"{source}: la config debe ser un mapa, "
                          f"es {type(cfg).__name__}")
    c = _Checker()

    # channel / hashtags / tools
    c.check(cfg, "channel", "dict")
    for k in ("name", "handle", "game", "streamer", "niche"):
        c.check(cfg, f"channel.{k}", "str")
    c.check(cfg, "channel.language", "str", choices={"es", "en"})
    c.check(cfg, "hashtags", "dict")
    for lang in ("es", "en"):
        c.check_str_list(cfg, f"hashtags.{lang}")
    c.check(cfg, "tools", "dict")
    c.check(cfg, "tools.twitch_downloader_cli", "str")

    # game / games
    c.check(cfg, "game", "dict")
    release = c.check(cfg, "game.release_date", "str")
    if release is not None:
        try:
            date.fromisoformat(release)
        except ValueError:
            c.err("game.release_date",
                  f"debe ser fecha YYYY-MM-DD, es {release!r}")
    tz = c.check(cfg, "game.timezone", "str")
    if tz is not None:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(tz)
        except Exception:
            c.err("game.timezone", f"zona horaria desconocida: {tz!r}")
    games = c.check(cfg, "games", "list")
    if games is not None:
        for i, g in enumerate(games):
            if not isinstance(g, dict):
                c.err(f"games[{i}]", "debe ser un mapa")
                continue
            for k in ("name", "id"):
                if not isinstance(g.get(k), str):
                    c.err(f"games[{i}].{k}", "debe ser texto")
            pats = g.get("patterns")
            if not isinstance(pats, list) or not pats or \
                    not all(isinstance(p, str) for p in pats):
                c.err(f"games[{i}].patterns",
                      "debe ser una lista no vacía de textos")
    c.check_str_list(cfg, "allowlist")

    # clips
    c.check(cfg, "clips", "dict")
    c.check(cfg, "clips.crop", "str")
    c.check(cfg, "clips.crf", "int", minimum=0, maximum=51)
    c.check(cfg, "clips.preset", "str", choices=PRESETS)
    c.check(cfg, "clips.duration", "int", minimum=1, maximum=60)
    target = c.check(cfg, "clips.target_duration_s", "int",
                     minimum=1, maximum=60)
    minimum_d = c.check(cfg, "clips.min_duration_s", "int",
                        minimum=1, maximum=60)
    maximum_d = c.check(cfg, "clips.max_duration_s", "int",
                        minimum=1, maximum=60)
    if None not in (minimum_d, target, maximum_d):
        if not minimum_d <= target <= maximum_d:
            c.err("clips.min_duration_s/clips.target_duration_s/"
                  "clips.max_duration_s",
                  f"debe cumplirse min <= objetivo <= max, "
                  f"es {minimum_d} <= {target} <= {maximum_d}")
    c.check(cfg, "clips.precise_cut", "bool")
    for k in ("pre_roll", "window", "min_separation"):
        c.check(cfg, f"clips.{k}", "int", minimum=0)

    # subtitles
    c.check(cfg, "subtitles", "dict")
    c.check(cfg, "subtitles.default_style", "str")
    c.check(cfg, "subtitles.subtitle_lang", "str",
            choices=SUBTITLE_LANGS)
    c.check(cfg, "subtitles.translate", "bool")
    c.check(cfg, "subtitle_policy", "dict")
    for k in ("es", "others"):
        c.check(cfg, f"subtitle_policy.{k}", "str",
                choices=POLICY_VALUES)
    styles = c.check(cfg, "subtitle_styles", "dict")
    if styles is not None:
        for name, st in styles.items():
            if not isinstance(st, dict):
                c.err(f"subtitle_styles.{name}", "debe ser un mapa")
                continue
            for k in ("fontsize", "margin_v", "max_chars"):
                c.check(cfg, f"subtitle_styles.{name}.{k}", "int",
                        minimum=1)
            c.check(cfg, f"subtitle_styles.{name}.border_width",
                    "int", minimum=0)
            c.check(cfg, f"subtitle_styles.{name}.max_duration",
                    "num", minimum=0.1)
            for k in ("bold", "box"):
                c.check(cfg, f"subtitle_styles.{name}.{k}", "bool")
            for k in ("font_color", "border_color"):
                c.check(cfg, f"subtitle_styles.{name}.{k}", "str")
    default_style = (cfg.get("subtitles") or {}).get("default_style")
    if styles is not None and default_style and \
            default_style not in styles:
        c.err("subtitles.default_style",
              f"no existe en subtitle_styles: {default_style!r}")

    # layout / facecam_box
    c.check(cfg, "layout", "dict")
    c.check(cfg, "layout.mode", "str", choices=LAYOUT_MODES)
    for k in ("sample_every_s", "min_segment_s", "scan_every_s"):
        c.check(cfg, f"layout.{k}", "int", minimum=1)
    for k in ("pip_width", "pip_margin", "blur_radius"):
        c.check(cfg, f"layout.{k}", "int", minimum=0)
    c.check(cfg, "layout.pip_corner", "str", choices=PIP_CORNERS)
    c.check(cfg, "layout.scan_enabled", "bool")
    c.check(cfg, "layout.game", "str")
    zones = c.check(cfg, "layout.safe_zones", "list") \
        if (cfg.get("layout") or {}).get("safe_zones") is not None else None
    if zones is not None:
        for i, z in enumerate(zones):
            if not isinstance(z, list) or len(z) != 4 or \
                    not all(_is_num(x) and 0 <= x <= 1 for x in z):
                c.err(f"layout.safe_zones[{i}]",
                      "debe ser [x0, y0, x1, y1] con valores 0..1")
    box = (cfg.get("facecam_box")
           if "facecam_box" in cfg else None)
    if box is not None:
        if not isinstance(box, list) or len(box) != 4 or \
                not all(_is_int(x) and x >= 0 for x in box):
            c.err("facecam_box",
                  "debe ser null o [x, y, w, h] con enteros >= 0")

    # watcher / reel_study
    c.check(cfg, "watcher", "dict")
    c.check(cfg, "watcher.poll_minutes", "int", minimum=1)
    c.check(cfg, "watcher.max_new_vods_per_streamer_per_day",
            "int", minimum=1)
    c.check(cfg, "watcher.state_file", "str")
    c.check(cfg, "reel_study", "dict")
    c.check(cfg, "reel_study.enabled", "bool")
    c.check(cfg, "reel_study.report_file", "str")
    for k in ("max_urls_per_run", "min_sample"):
        c.check(cfg, f"reel_study.{k}", "int", minimum=0)

    # autopublish
    c.check(cfg, "autopublish", "dict")
    for k in ("threshold", "min_days_per_phase", "min_clips_per_phase"):
        c.check(cfg, f"autopublish.{k}", "int", minimum=0)
    veto = c.check(cfg, "autopublish.veto_window_minutes", "dict")
    if veto is not None:
        for k, v in veto.items():
            if not _is_int(v) or v < 0:
                c.err(f"autopublish.veto_window_minutes.{k}",
                      f"debe ser entero >= 0, es {v!r}")
    c.check(cfg, "autopublish.metrics_green", "bool")
    c.check(cfg, "autopublish.mode", "str", choices=AUTOPUBLISH_MODES)
    # Umbrales duros (6B.2) y auto-aprobación (6.3).
    c.check(cfg, "scoring", "dict")
    c.check(cfg, "scoring.seo_gate", "int", minimum=0, maximum=100)
    c.check(cfg, "scoring.viral_min", "int", minimum=0, maximum=100)
    c.check(cfg, "scoring.audio_weight", "num", minimum=0, maximum=5)
    c.check(cfg, "scoring.audio_weight_kick", "num", minimum=0, maximum=5)
    c.check(cfg, "auto_approve", "dict")
    c.check(cfg, "auto_approve.enabled", "bool")
    c.check(cfg, "metadata", "dict")
    c.check(cfg, "metadata.credit_streamer", "bool")
    c.check(cfg, "live", "dict")
    c.check(cfg, "live.cooldown_minutes", "num", minimum=0, maximum=120)
    c.check(cfg, "live.max_clips_per_stream", "int", minimum=1, maximum=50)
    c.check(cfg, "live.segment_minutes", "int", minimum=5, maximum=10)
    if (cfg.get("auto_approve") or {}).get("min_band") is not None:
        c.check(cfg, "auto_approve.min_band", "str",
                choices={"descartar", "revisar", "prioritario", "urgente"})
    c.check(cfg, "breaker", "dict")
    c.check(cfg, "breaker.max_consecutive_failures", "int", minimum=1)
    # Fan-out: interruptor, base de URL pública e IDs de cuenta.
    c.check(cfg, "fanout", "dict")
    c.check(cfg, "fanout.enabled", "bool")
    c.check(cfg, "public_url", "dict")
    c.check(cfg, "public_url.base_url", "str")
    c.check(cfg, "platform_accounts", "dict")
    for p in ("instagram_reels", "facebook_reels"):
        c.check(cfg, f"platform_accounts.{p}", "dict")
    c.check(cfg, "platform_accounts.instagram_reels.ig_user_id", "str")
    c.check(cfg, "platform_accounts.facebook_reels.page_id", "str")

    warnings = [f"clave desconocida de nivel superior: {k!r} "
                f"(se ignora; revisa si es un error de tipeo)"
                for k in cfg if k not in KNOWN_TOP_KEYS]

    if c.errors:
        raise ConfigError(
            f"{source}: config inválida:\n" +
            "\n".join(f"  - {e}" for e in c.errors))
    return warnings


def warn_unknown(warnings):
    for w in warnings:
        print(f"aviso de config: {w}", file=sys.stderr)
