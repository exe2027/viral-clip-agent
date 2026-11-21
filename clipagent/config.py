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
    # La identidad del canal (nombre, handle, idioma, hashtags de
    # marca) NO vive aquí: es de cada tenant y sale de SU panel.
    # El idioma del clip lo decide el streamer (su idioma en el
    # panel). Ver config.yaml.
    "hashtags": {
        "es": ["#GTA6", "#GTA6Clips", "#GTA", "#RockstarGames", "#gaming"],
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
        # REGLAS DE DURACIÓN: la regla 40-60 s con mínimo 40 y rango
        # prohibido 25-35 fue ELIMINADA por el usuario el 2026-11-21
        # ("para siempre, en todo el canal"). Sin mínimo ni rango
        # prohibido; solo queda el tope duro de 60 s.
        # Objetivo 40 s = 20 s pre-roll + 20 s tras el pico (payoff
        # a 20 s dentro del clip). Tope duro 60 s = máximo de un
        # Short de YouTube y tope del proyecto, nunca el default.
        "duration": 40,
        "target_duration_s": 40,
        "min_duration_s": 1,
        "max_duration_s": 60,
        "hard_cap_s": 60,
        "forbidden_duration_range": [],
        "precise_cut": True,
        "pre_roll": 20,
        "window": 10,
        "min_separation": 60,
        # Layout split vertical RDJavi: streamer arriba, gameplay abajo
        # 600 px (1080x1920). Los subtítulos viven en la zona de
        # juego, nunca sobre la cámara (ver subtitle_styles).
        "split_game_h": 1320,
        "split_cam_h": 600,
    },
    "subtitles": {
        # Estilo por defecto "rubius" (plantilla 2026-11-19, decision
        # del usuario: TODOS los clips a rubius): palabra por palabra,
        # blanca, Arial Black 100 px reales, sobre el gameplay
        # (y 900-1020). El rdjavi (fijo amarillo en la barra negra)
        # queda como opción secundaria.
        "default_style": "rubius",
        "subtitle_lang": "auto",  # auto | es | en
        "translate": True,        # traducir a inglés lo que no sea español
        # Regla del usuario 2026-11-17 22:10, matizada 2026-11-19
        # 18:41: SIEMPRE subtítulos, COMPLETOS, cuando hay voz.
        # Sin voz verificable + pasa puertas (45/80) el clip se
        # publica igual sin subtítulos; nunca se inventa texto.
        "required": True,
        "complete": True,
        "publish_no_speech_if_passes_gates": True,
        # Zona de subtítulos (plantilla v2, 2026-11-18): siempre en
        # la barra negra inferior (1740-1920 px, MarginV 12), nunca
        # sobre la webcam (0-702 px) ni sobre el gameplay
        # (800-1740 px, ahí van los subtítulos propios de GTA 6).
        # burn_subtitles() deja el MarginV dentro de la barra.
        "subtitle_zone": "bottom_bar",
        "subtitle_safe_gap_px": 80,
        "subtitle_bottom_reserve_px": 0,
        "subtitle_game_bottom_px": 1740,
    },
    # Política de traducción (regla del usuario, 2026-11-16):
    # es -> transcribe-only; cualquier otro idioma -> translate-to-en.
    "subtitle_policy": {
        "es": "transcribe-only",
        "others": "translate-to-en",
    },
    # Efectos de sonido (2026-11-19, pedido del usuario: SFX por
    # defecto en el render, vivo y VOD). Ver clipagent/sfx_mix.py.
    "sfx": {
        "enabled": True,
        "max_sfx": 2,
        "lift": True,
    },
    "subtitle_styles": {
        # Plantilla v2 (2026-11-18): los estilos rdjavi/ref/tiktok/
        # highlight/karaoke queman en la barra negra inferior
        # (MarginV 12, texto en 1740-1920 px), nunca sobre el
        # gameplay (ahí van los subtítulos propios de GTA 6). La
        # excepción es rubius (default 2026-11-19): palabra por
        # palabra blanca SOBRE el gameplay (MarginV 900).
        "rubius": {
            # Plantilla rubius (default 2026-11-19, decisión del
            # usuario: TODOS los clips a rubius): palabra por
            # palabra, blanca, Arial Black 100 px reales, contorno
            # negro grueso (10), centrada sobre el gameplay
            # (y 900-1020, MarginV 900). El banner del título NO va
            # en este estilo: es un PNG aparte (clipagent/rubius.py).
            "font_color": "#FFFFFF", "border_color": "#000000",
            "border_width": 10, "fontsize": 100, "bold": True,
            "margin_v": 900, "max_chars": 20, "max_duration": 1.0,
            "box": False,
        },
        "rdjavi": {
            # Estilo v2 (default): subtítulos fijos amarillos,
            # Arial Black 64 px reales, contorno negro grueso (6),
            # en la barra negra abajo del todo. Sin karaoke.
            "font_color": "#FFFF00", "border_color": "#000000",
            "border_width": 6, "fontsize": 64, "bold": True,
            "margin_v": 12, "max_chars": 20, "max_duration": 2.0,
            "box": False,
        },
        # Estilo "ref": una línea centrada en la barra negra,
        # letra chica 32 px fina, blanca con contorno negro, sin
        # fondo ni resaltado palabra por palabra.
        "ref": {
            "font_color": "#FFFFFF", "border_color": "#000000",
            "border_width": 2, "fontsize": 32, "bold": False,
            "margin_v": 12, "max_chars": 42, "max_duration": 3.0,
            "box": False,
        },
        "tiktok": {
            "font_color": "#FFFFFF", "border_color": "#000000",
            "border_width": 3, "fontsize": 48, "bold": True,
            "margin_v": 12, "max_chars": 20, "max_duration": 2.0,
            "box": False,
        },
        "highlight": {
            "font_color": "#FFFF00", "border_color": "#000000",
            "border_width": 2, "fontsize": 52, "bold": True,
            "margin_v": 12, "max_chars": 20, "max_duration": 2.0,
            "box": True,
        },
        "karaoke": {
            "font_color": "#FFFFFF", "active_word_color": "#FFFF00",
            "border_color": "#000000",
            "border_width": 4, "fontsize": 56, "bold": True,
            "margin_v": 12, "max_chars": 8, "max_duration": 1.0,
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
    # Señales de Uri (clipper-directos, 2026-11-17): heatmap, % de
    # movimiento y voz del juego. Solo diagnóstico en el manifiesto;
    # NO tocan viralidad, prioridad, filtros duros ni ritmo.
    "signals": {
        "heatmap": {
            "enabled": True,
            "bucket_s": 10,
            "top_n": 15,
            "min_separation_s": 60,
            "max_gap_s": 30,
        },
        "motion": {
            "enabled": True,
            "threshold": 3.0,
            "frozen_max_pct": 10.0,
            "strong_virality_min": 85.0,
        },
        "game_voice": {
            "enabled": True,
            "silence_s": 2.0,
            "active_dbfs": -45.0,
        },
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
    # Fan-out (publicación en las otras 3 al publicar en YouTube):
    # enabled false = cada plataforma solo publica por su propio
    # ítem en la cola, sin propagar desde YouTube.
    "fanout": {
        "enabled": True,
    },
    # URL pública del video (fan-out): Instagram exige que Meta
    # descargue el video desde una URL HTTPS pública; este módulo
    # NO sube nada: la base es el hosting estático que el usuario
    # configura para servir los clips. Vacía = Instagram en draft.
    # También vale la variable CLIP_PUBLIC_BASE_URL (manda el .env).
    "public_url": {
        "base_url": "",
    },
    # IDs de cuenta por plataforma (fan-out): no son secretos, pero
    # no viven en accounts.yaml (que solo guarda handles). Vacíos =
    # draft con el motivo explícito. También valen IG_USER_ID y
    # FACEBOOK_PAGE_ID en .env (mandan sobre config.yaml).
    "platform_accounts": {
        "instagram_reels": {"ig_user_id": ""},
        "facebook_reels": {"page_id": ""},
    },
    # Panel web (regla del usuario: TODO ES PARA LA WEB, NADA
    # LOCAL). El panel vive en Cloudflare (Pages + Worker + D1) y
    # es la única superficie de operación: login por usuario, cada
    # tenant ve solo lo suyo. Sin secretos aquí: las credenciales
    # van en GitHub Secrets / D1 cifrado, nunca en config.
    "panel": {
        "base_url": "https://avc-em9.pages.dev",
        "worker_url": "https://avc.vikingosfoodspa.workers.dev",
    },
}


# REGLAS OBVIAS DEL PROYECTO (decisiones del usuario 2026-11-16/17).
# Canónicas en config.yaml `rules:`; estos valores son el espejo en
# código cuando no hay config.yaml. Si una regla cambia, cambia en
# los dos sitios. Las impone: precise_cut.py (duración), transcribe.py
# (subtítulos), policy.py (puerta de publicación) y validate.py.
#   * Duración: sin mínimo ni rango prohibido (regla 40-60 / 25-35
#     eliminada por el usuario el 2026-11-21, para siempre). Solo
#     queda el tope duro de 60 s.
#   * Pre-roll: 20 s de contexto antes del pico.
#   * Subtítulos: siempre en la zona de juego, nunca sobre la cara
#     del influencer / la webcam (split: juego 0-1320 px, cámara
#     banda inferior 1770-1920 px; MarginV de referencia 230,
#     mínimo 200 (geometría RDJavi, 2026-11-18).
#   * Nunca tapar minimapa ni HUD; layout split vertical.
#   * Nunca afirmar un juego no verificado; nunca quemar subtítulos
#     alucinados (manda la transcripción corregida por el usuario).
RULES_MIN_DURATION_S = 1  # sin mínimo (regla eliminada 2026-11-21)
RULES_TARGET_DURATION_S = 40
RULES_HARD_CAP_S = 60
RULES_FORBIDDEN_DURATION_RANGE = ()  # sin rango prohibido (eliminado 2026-11-21)
RULES_PRE_ROLL_S = 20
RULES_SPLIT_GAME_H = 1320
RULES_SPLIT_CAM_H = 600
RULES_SUBTITLE_SAFE_GAP_PX = 80
# Geometría RDJavi v2 (layout GTA 6 por defecto, port 2026-11-18):
# la cámara está ARRIBA a ancho completo (0-702 px), la franja de
# título es 702-800 px, el gameplay es 800-1740 px y la barra de
# subtítulos es 1740-1920 px. Los subtítulos viven ENTEROS en la
# barra (nunca sobre el gameplay): el MarginV de referencia es 12
# y el mínimo es 0 (el texto puede tocar el borde inferior); el
# máximo lo da el tamaño de letra (ver effective_subtitle_margin_v
# en transcribe.py). La corrección vieja (mínimo 200, referencia
# 230, texto en el gameplay en y≈1690) queda retirada.
RULES_RDJAVI_CAM_BOTTOM = 702
RULES_RDJAVI_GAME_BOTTOM = 1740
RULES_SUBTITLE_BOTTOM_RESERVE_PX = 0
RULES_SUBTITLE_MIN_MARGIN_V = 0
RULES_SUBTITLE_REF_MARGIN_V = 12  # referencia v2: texto en la barra


def clip_duration_ok(duration_s, cfg=None):
    """(ok, motivo) para la duración de un clip según las reglas.

    Sin mínimo ni rango prohibido desde 2026-11-21 (regla 40-60 /
    25-35 eliminada por el usuario, para siempre). Rechaza solo
    > tope duro 60 s.
    """
    if cfg is not None:
        clips = (cfg or {}).get("clips") or {}
        min_d = int(clips.get("min_duration_s") or RULES_MIN_DURATION_S)
        max_d = int(clips.get("max_duration_s") or RULES_HARD_CAP_S)
        fr = clips.get("forbidden_duration_range") or list(
            RULES_FORBIDDEN_DURATION_RANGE)
    else:
        min_d, max_d = RULES_MIN_DURATION_S, RULES_HARD_CAP_S
        fr = list(RULES_FORBIDDEN_DURATION_RANGE)
    if duration_s < min_d:
        return False, (f"duración {duration_s:.1f} s < mínimo {min_d} s")
    if fr and float(fr[0]) <= duration_s <= float(fr[1]):
        return False, (f"duración {duration_s:.1f} s en el rango prohibido "
                       f"{fr[0]}-{fr[1]} s")
    if duration_s > max_d:
        return False, (f"duración {duration_s:.1f} s > tope duro {max_d} s")
    return True, ""


def subtitle_min_margin_v(cfg=None):
    """MarginV mínimo del subtítulo (plantilla v2, 2026-11-18).

    El texto vive en la barra de subtítulos 1740-1920 px: el
    mínimo es 0 (el borde inferior del texto puede tocar y=1920)
    y el máximo lo da el tamaño de letra (el borde superior no
    puede subir de 1740; ver effective_subtitle_margin_v en
    transcribe.py). ``cfg`` queda por compatibilidad.
    """
    return RULES_SUBTITLE_MIN_MARGIN_V


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


TEMPLATE_NAMES = ("rdjavi", "willyrex", "rubius")

# Recetas de plantilla (2026-11-19): claves obligatorias por
# receta en config.yaml (sección `templates`). `cam` y `game`
# las llevan las tres; el resto depende del estilo de subtítulos:
# karaoke (rdjavi v1) exige karaoke_band + subs_band; fixed_yellow
# (willyrex v2) exige title_band + subs_band; word_white (rubius)
# exige title_banner + subs_zone.
TEMPLATE_REQUIRED_KEYS = {
    "rdjavi": ("cam", "karaoke_band", "game", "subs_band",
               "subs_style", "subs_over_game", "cam_full_width",
               "blurred_bg"),
    "willyrex": ("cam", "title_band", "game", "subs_band",
                 "subs_style", "subs_over_game", "cam_full_width",
                 "blurred_bg"),
    "rubius": ("cam", "game", "title_banner", "subs_zone",
               "subs_style", "subs_over_game", "cam_full_width",
               "blurred_bg"),
}

# Variante de render -> plantilla que produce. El render de hoy
# (resolve_split_variant "rdjavi", código rdjavi.py v2) produce la
# plantilla willyrex; "rubius" (clipagent/rubius.py, default desde
# 2026-11-19) produce la plantilla rubius; "plain" no tiene receta
# de validador (el clip se valida contra el default).
TEMPLATE_BY_VARIANT = {
    "rdjavi": "willyrex",
    "rubius": "rubius",
    "plain": None,
}


def template_for_variant(variant):
    """Plantilla que produce una variante de render, o None si no
    tiene receta de validador (el clip cae al default)."""
    return TEMPLATE_BY_VARIANT.get(
        str(variant or "").lower())


def load_validator_config(cfg=None):
    """Config de la puerta dura del validador final (2026-11-19).

    ÚNICA fuente: las secciones ``validator``, ``final_review`` y
    ``templates`` de config.yaml. NO hay defaults silenciosos: si
    falta una sección o una clave, levanta ConfigError nombrando
    la clave exacta y el run para, nunca sigue con un valor
    inventado. Es el fallo que dejó pasar los clips sin cara: el
    modo observación era un default silencioso de "pasa" y el
    camino VOD ni cargaba la config.

    TRES plantillas (2026-11-19 19:07): rdjavi (v1), willyrex
    (v2) y rubius. El default (`templates.default`) es
    rubius (decisión del usuario 2026-11-19 19:09): un clip sin
    plantilla declarada se valida contra rubius.

    Devuelve un dict plano con los valores resueltos (umbrales de
    cara, muestreo, observe_only, max_rerenders) + ``templates``
    (las tres recetas) + ``default_template``. ``cfg`` puede ser
    el dict de load_config(), un dict mínimo en tests, o None/{}
    (en ese caso se carga config.yaml en disco, la única fuente:
    un dict vacío NO es "sin config", es "carga la de disco").
    Si la validación está apagada explícitamente
    (``final_review.enabled`` es False), devuelve una vcfg
    desactivada SIN exigir umbrales ni recetas (una puerta apagada
    no necesita geometría); esa vcfg solo lleva lo que necesita el
    veredicto "desactivada" y NO se pasa a los gates. En cualquier
    otro caso, si falta una sección o una clave, ConfigError.
    """
    from .validate import ConfigError

    # Sin cfg (None o {}): la única fuente es config.yaml en disco
    # (mismo patrón que get_layout_config). Lo que falla en voz
    # alta es que el ARCHIVO no traiga el valor, no que el
    # llamador no pase el dict (eso rompía los pasos que se
    # construyen con cfg={} y los tests de revisión).
    cfg = cfg or load_config()
    review = cfg.get("final_review")
    # Validación apagada explícitamente: no hay veredicto y no
    # hacen falta umbrales ni recetas. La clave tiene que existir
    # y ser False de verdad: si falta, el camino estricto de abajo
    # levanta ConfigError (no se apaga nada en silencio).
    if isinstance(review, dict) and review.get("enabled") is False:
        templates = cfg.get("templates")
        default = ((templates or {}).get("default")
                   if isinstance(templates, dict) else None)
        return {
            "enabled": False,
            "observe_only": bool(review.get("observe_only", False)),
            "templates": (templates
                          if isinstance(templates, dict) else {}),
            "default_template": (
                default if default in TEMPLATE_NAMES else "rubius"),
        }
    section = cfg.get("validator")
    if not isinstance(section, dict):
        raise ConfigError(
            "validator: falta la sección 'validator' en config.yaml "
            "(el validador final no arranca sin sus umbrales; "
            "sin defaults silenciosos)")
    required = (
        "face_sample_every_s", "face_min_frames", "face_min_share",
        "face_min_width_frac", "min_score", "max_rerenders",
        "gemini_hard", "chain",
    )
    missing = [k for k in required if k not in section]
    if missing:
        raise ConfigError(
            "validator: faltan claves en config.yaml: "
            + ", ".join(f"validator.{k}" for k in missing)
            + " (sin defaults silenciosos)")
    chain = list(section.get("chain") or [])
    if chain != ["facecrop", "edge", "yunet"]:
        raise ConfigError(
            "validator.chain: debe ser [facecrop, edge, yunet] en "
            "ese orden (orden del usuario 2026-11-19), "
            f"es {chain!r}")
    # (``review`` ya se leyó arriba; aquí enabled es True o la
    # clave falta: en ambos casos se exige la sección completa.)
    if not isinstance(review, dict):
        raise ConfigError(
            "final_review: falta la sección 'final_review' en "
            "config.yaml (el validador final no arranca sin ella)")
    for key in ("enabled", "observe_only"):
        if key not in review:
            raise ConfigError(
                f"final_review.{key}: falta en config.yaml "
                "(sin defaults silenciosos)")
    templates = cfg.get("templates")
    if not isinstance(templates, dict):
        raise ConfigError(
            "templates: falta la sección 'templates' en config.yaml "
            "(el validador final verifica TRES recetas; sin "
            "defaults silenciosos)")
    default = templates.get("default")
    if default not in TEMPLATE_NAMES:
        raise ConfigError(
            "templates.default: debe ser rdjavi, willyrex o rubius "
            f"(decisión del usuario: rubius), es {default!r}")
    recipes = {}
    for name in TEMPLATE_NAMES:
        recipe = templates.get(name)
        if not isinstance(recipe, dict):
            raise ConfigError(
                f"templates.{name}: falta la receta {name!r} en "
                "config.yaml")
        missing_r = [k for k in TEMPLATE_REQUIRED_KEYS[name]
                     if k not in recipe]
        if missing_r:
            raise ConfigError(
                f"templates.{name}: faltan claves en config.yaml: "
                + ", ".join(f"templates.{name}.{k}"
                            for k in missing_r)
                + " (sin defaults silenciosos)")
        cam = recipe.get("cam") or {}
        game = recipe.get("game") or {}
        for key in ("x", "y", "w", "h"):
            if key not in cam:
                raise ConfigError(
                    f"templates.{name}.cam.{key}: falta en "
                    "config.yaml")
        for key in ("y0", "y1"):
            if key not in game:
                raise ConfigError(
                    f"templates.{name}.game.{key}: falta en "
                    "config.yaml")
        recipes[name] = dict(recipe)
    return {
        "face_sample_every_s": float(
            section["face_sample_every_s"]),
        "face_min_frames": int(section["face_min_frames"]),
        "face_min_share": float(section["face_min_share"]),
        "face_min_width_frac": float(
            section["face_min_width_frac"]),
        "min_score": float(section["min_score"]),
        "max_rerenders": int(section["max_rerenders"]),
        "gemini_hard": bool(section["gemini_hard"]),
        "chain": chain,
        "enabled": bool(review["enabled"]),
        "observe_only": bool(review["observe_only"]),
        "templates": recipes,
        "default_template": default,
    }


def resolve_template(vcfg, plantilla=None):
    """(nombre, receta) de la plantilla a validar.

    ``plantilla`` es la declarada por el clip; sin ella se usa el
    default de config.yaml (rubius). Un nombre desconocido falla
    en voz alta con ConfigError: nunca se valida contra otra
    plantilla en silencio.
    """
    from .validate import ConfigError

    name = (str(plantilla).strip().lower()
            if plantilla else None) or vcfg["default_template"]
    recipe = (vcfg.get("templates") or {}).get(name)
    if recipe is None:
        raise ConfigError(
            f"plantilla {name!r}: no es rdjavi, willyrex ni rubius "
            "(revisa el campo plantilla del clip o "
            "templates.default en config.yaml)")
    return name, recipe


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
