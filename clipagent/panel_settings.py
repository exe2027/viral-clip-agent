#!/usr/bin/env python3
"""Ajustes del panel leídos por el cerebro (2026-11-18).

El usuario cambia la configuración desde la pestaña Ajustes del
panel web (https://avc-em9.pages.dev): se guarda en D1 por tenant
y el cerebro la lee por el endpoint puente del Worker, sin editar
código:

    GET {worker_url}/api/bridge/settings?tenant={tenant}
    Authorization: Bearer <sesión del cerebro o BRIDGE_TOKEN>

Respuesta: {"settings": {...}} con los campos de PANEL_SETTINGS
(abajo). Sin PANEL_WORKER_URL y sin token (PANEL_SESSION_TOKEN o
PANEL_BRIDGE_TOKEN), el cerebro usa solo config.yaml
(comportamiento de siempre); con ellos, los Ajustes mandan sobre
config.yaml.

Campos (mismos nombres en D1, en el Worker y aquí):
  layout: "rdjavi" | "plain" (default "rdjavi" para GTA 6)
  subtitle_style: "ref" | "karaoke" | "tiktok" | "highlight" (default "karaoke", 2026-11-18)
  min_duration_s / max_duration_s: 1 / 60 (sin mínimo desde 2026-11-21)
  min_virality: 50, min_seo: 90
  youtube_plan_per_day: 1, youtube_max_per_day: 2
  tiktok_plan_per_day: 2, tiktok_max_per_day: 3
  other_plan_per_day: 1, other_max_per_day: 2 (Instagram y Facebook)
  new_account_daily: 1
  tiktok_zernio_per_day: 15, tiktok_zernio_pending_drafts_max: 5
  min_spacing_hours: 3, youtube_spacing_hours: 4
  cross_platform_min_minutes: 15, rolling_24h_max: 3
  warmup_new_if_days: 14, warmup_new_if_publishes: 20
  hashtag_max: 5
  (Topes 2026-11-19: YT 1/2, TikTok 2/3, resto 1/2; la decisión
  2026-11-18 "sin franja" queda revocada y vuelven las franjas
  12-14 / 18-20 / 21-23, que viven en limits.yaml, no en Ajustes.)
  category: "GTA VI" (el gate exige GTA VI)
  thumbnail_style: "gta6"
  hype_burst: true | false (hype por ráfaga on/off)
  loop_running: true | false (el loop Iniciar/Parar; lo lee el
    Worker, el cerebro solo lo muestra)
  Marca de agua (2026-11-18, elección del usuario: logo de
  COLORES, arriba a la derecha, 320 px, 20%):
  watermark_enabled: true | false
  watermark_url: URL firmada HMAC (TTL 60 min) que el puente
    añade SOLO cuando la marca está activada y el logo existe
    en R2; sin logo resoluble el puente apaga la marca en esa
    respuesta sin tocar D1.
  watermark_opacity: 0-100 (%), watermark_size_px: px de
    diámetro, watermark_position: center | top-left | top-right
    | bottom-left | bottom-right
"""
import json
import os
import urllib.request

from .watermark import WATERMARK_POSITIONS

PANEL_SETTINGS_FIELDS = {
    "layout": str,
    "subtitle_style": str,
    "min_duration_s": int,
    "max_duration_s": int,
    "min_virality": int,
    "min_seo": int,
    "youtube_plan_per_day": int,
    "youtube_max_per_day": int,
    "tiktok_plan_per_day": int,
    "tiktok_max_per_day": int,
    "other_plan_per_day": int,
    "other_max_per_day": int,
    "new_account_daily": int,
    "tiktok_zernio_per_day": int,
    "tiktok_zernio_pending_drafts_max": int,
    "min_spacing_hours": int,
    "youtube_spacing_hours": int,
    "cross_platform_min_minutes": int,
    "rolling_24h_max": int,
    "warmup_new_if_days": int,
    "warmup_new_if_publishes": int,
    "hashtag_max": int,
    "category": str,
    "thumbnail_style": str,
    "hype_burst": bool,
    "loop_running": bool,
    "watermark_enabled": bool,
    "watermark_url": str,
    "watermark_opacity": int,
    "watermark_size_px": int,
    "watermark_position": str,
}

# Rangos válidos: fuera de rango, el valor del panel se ignora y
# manda config.yaml (nunca se rompe el cerebro por un Ajuste).
PANEL_SETTINGS_RANGES = {
    "min_duration_s": (1, 300),
    "max_duration_s": (1, 300),
    "min_virality": (0, 100),
    "min_seo": (0, 100),
    "youtube_plan_per_day": (0, 15),
    "youtube_max_per_day": (0, 15),
    "tiktok_plan_per_day": (0, 15),
    "tiktok_max_per_day": (0, 15),
    "other_plan_per_day": (0, 15),
    "other_max_per_day": (0, 15),
    "new_account_daily": (0, 5),
    "tiktok_zernio_per_day": (0, 15),
    "tiktok_zernio_pending_drafts_max": (0, 5),
    "min_spacing_hours": (0, 24),
    "youtube_spacing_hours": (0, 24),
    "cross_platform_min_minutes": (0, 120),
    "rolling_24h_max": (0, 10),
    "warmup_new_if_days": (0, 90),
    "warmup_new_if_publishes": (0, 100),
    "hashtag_max": (0, 15),
    "watermark_opacity": (0, 100),
    "watermark_size_px": (32, 1080),
}


def _coerce(field, value):
    """Convierte y valida un valor del panel; None si no vale."""
    kind = PANEL_SETTINGS_FIELDS.get(field)
    if kind is None or value is None:
        return None
    try:
        if kind is bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "sí", "si", "on")
            return bool(value)
        out = kind(value)
    except (TypeError, ValueError):
        return None
    if field in PANEL_SETTINGS_RANGES:
        lo, hi = PANEL_SETTINGS_RANGES[field]
        if not lo <= out <= hi:
            return None
    return out


def sanitize_panel_settings(settings):
    """Solo campos conocidos, con tipo y rango válidos."""
    clean = {}
    for field, value in (settings or {}).items():
        coerced = _coerce(field, value)
        if coerced is not None:
            clean[field] = coerced
    if (clean.get("min_duration_s") is not None
            and clean.get("max_duration_s") is not None
            and clean["min_duration_s"] > clean["max_duration_s"]):
        clean.pop("min_duration_s")
        clean.pop("max_duration_s")
    if (clean.get("watermark_position") is not None
            and clean["watermark_position"] not in WATERMARK_POSITIONS):
        clean.pop("watermark_position")
    return clean


def apply_panel_settings(cfg, settings):
    """Devuelve una copia de cfg con los Ajustes del panel aplicados.

    layout -> layout.split_variant (solo "rdjavi"/"plain"; "djmariio"
    está aparcado y se resuelve a "rdjavi"). subtitle_style ->
    subtitles.default_style. Duración -> rules.min/max_duration_s y
    clips.min/target_duration_s. Viralidad/SEO -> scoring. Topes ->
    limits.yaml en memoria (vía publish.load_limits, si está
    disponible). Categoría -> rules.category_gta6_only sigue
    mandando; el valor se guarda en rules.category. Miniatura ->
    metadata.THUMBNAIL_STYLE (solo se registra; el render usa el
    estilo "gta6"). Hype -> rules.hype_burst_enabled.
    """
    import copy
    cfg = copy.deepcopy(cfg or {})
    clean = sanitize_panel_settings(settings)
    if not clean:
        return cfg
    layout = clean.get("layout")
    if layout in ("rdjavi", "plain"):
        cfg.setdefault("layout", {})["split_variant"] = layout
    elif layout == "djmariio":
        cfg.setdefault("layout", {})["split_variant"] = "rdjavi"
    style = clean.get("subtitle_style")
    if style in ("ref", "karaoke", "tiktok", "highlight"):
        cfg.setdefault("subtitles", {})["default_style"] = style
    rules = cfg.setdefault("rules", {})
    if clean.get("min_duration_s") is not None:
        rules["min_duration_s"] = clean["min_duration_s"]
        cfg.setdefault("clips", {})["min_duration_s"] = clean["min_duration_s"]
    if clean.get("max_duration_s") is not None:
        rules["max_duration_s"] = clean["max_duration_s"]
    if clean.get("min_virality") is not None:
        cfg.setdefault("scoring", {})["viral_min"] = clean["min_virality"]
    if clean.get("min_seo") is not None:
        cfg.setdefault("scoring", {})["seo_gate"] = clean["min_seo"]
    if clean.get("category"):
        rules["category"] = clean["category"]
    if clean.get("thumbnail_style"):
        rules["thumbnail_style"] = clean["thumbnail_style"]
    if clean.get("hype_burst") is not None:
        rules["hype_burst_enabled"] = clean["hype_burst"]
    if clean.get("loop_running") is not None:
        rules["loop_running"] = clean["loop_running"]
    # Marca de agua -> cfg["watermark"] (la lee watermark.py):
    # se mezcla sobre lo que ya traiga config.yaml; si el panel
    # no manda nada de la marca (Worker viejo), lo de config.yaml
    # queda intacto. watermark_url solo llega cuando la marca está
    # activada y el logo existe en R2 (el puente la añade).
    wm = dict((cfg.get("watermark") or {}))
    if clean.get("watermark_enabled") is not None:
        wm["enabled"] = clean["watermark_enabled"]
    if clean.get("watermark_url"):
        wm["url"] = clean["watermark_url"]
    if clean.get("watermark_opacity") is not None:
        wm["opacity"] = clean["watermark_opacity"]
    if clean.get("watermark_size_px") is not None:
        wm["size_px"] = clean["watermark_size_px"]
    if clean.get("watermark_position"):
        wm["position"] = clean["watermark_position"]
    if wm:
        cfg["watermark"] = wm
    limits = {}
    if clean.get("youtube_plan_per_day") is not None:
        limits.setdefault("youtube_shorts", {})["plan_per_day"] = clean["youtube_plan_per_day"]
    if clean.get("youtube_max_per_day") is not None:
        limits.setdefault("youtube_shorts", {})["max_per_day"] = clean["youtube_max_per_day"]
    # TikTok tiene plan/max propios (2/3); other_* solo aplica a
    # Instagram y Facebook (1/2). Si el panel no manda los de
    # TikTok, other_* sigue valiendo para TikTok como antes.
    if clean.get("tiktok_plan_per_day") is not None:
        limits.setdefault("tiktok", {})["plan_per_day"] = clean["tiktok_plan_per_day"]
    if clean.get("tiktok_max_per_day") is not None:
        limits.setdefault("tiktok", {})["max_per_day"] = clean["tiktok_max_per_day"]
    for plat in ("instagram_reels", "facebook_reels"):
        if clean.get("other_plan_per_day") is not None:
            limits.setdefault(plat, {})["plan_per_day"] = clean["other_plan_per_day"]
        if clean.get("other_max_per_day") is not None:
            limits.setdefault(plat, {})["max_per_day"] = clean["other_max_per_day"]
    if clean.get("tiktok_plan_per_day") is None \
            and clean.get("other_plan_per_day") is not None:
        limits.setdefault("tiktok", {})["plan_per_day"] = clean["other_plan_per_day"]
    if clean.get("tiktok_max_per_day") is None \
            and clean.get("other_max_per_day") is not None:
        limits.setdefault("tiktok", {})["max_per_day"] = clean["other_max_per_day"]
    if clean.get("new_account_daily") is not None:
        limits["new_account_daily"] = clean["new_account_daily"]
    if clean.get("tiktok_zernio_per_day") is not None:
        limits.setdefault("tiktok", {})["zernio_per_day"] = clean["tiktok_zernio_per_day"]
    if clean.get("tiktok_zernio_pending_drafts_max") is not None:
        limits.setdefault("tiktok", {})["zernio_pending_drafts_max"] = clean["tiktok_zernio_pending_drafts_max"]
    if clean.get("min_spacing_hours") is not None:
        limits["min_spacing_hours"] = clean["min_spacing_hours"]
    if clean.get("youtube_spacing_hours") is not None:
        limits["youtube_spacing_hours"] = clean["youtube_spacing_hours"]
    if clean.get("cross_platform_min_minutes") is not None:
        limits["cross_platform_min_minutes"] = clean["cross_platform_min_minutes"]
    if clean.get("rolling_24h_max") is not None:
        limits["rolling_24h_max"] = clean["rolling_24h_max"]
    if clean.get("warmup_new_if_days") is not None:
        limits.setdefault("warmup", {})["new_if_days"] = clean["warmup_new_if_days"]
    if clean.get("warmup_new_if_publishes") is not None:
        limits.setdefault("warmup", {})["new_if_publishes"] = clean["warmup_new_if_publishes"]
    if clean.get("hashtag_max") is not None:
        limits["hashtag_max"] = {"instagram_reels": clean["hashtag_max"],
                                 "default": clean["hashtag_max"]}
    if limits:
        cfg["_panel_limits_override"] = limits
    return cfg


def fetch_panel_settings(worker_url=None, bridge_token=None, tenant=None,
                         timeout=10, session_token=None):
    """Lee los Ajustes del panel por el endpoint puente.

    Devuelve el dict de settings o None (sin red, sin token, error
    HTTP o respuesta inválida: nunca lanza excepción por eso; el
    cerebro sigue con config.yaml).

    Auth (2026-11-19, cerebro genérico): primero la sesión del
    cerebro (PANEL_SESSION_TOKEN, canjeada del token de un solo
    uso) y si no, el secreto compartido PANEL_BRIDGE_TOKEN
    (compatibilidad). Sin PANEL_WORKER_URL ni token no hay panel
    que leer y no hace falta tenant. Con ellos configurados, el
    tenant es obligatorio: si falta PANEL_TENANT o no está en
    tenants.yaml, levanta TenantError y el proceso aborta sin
    tocar la red (regla 2026-11-18: sin tenant por defecto).
    """
    worker_url = (worker_url or os.environ.get("PANEL_WORKER_URL") or "").rstrip("/")
    bridge_token = bridge_token or os.environ.get("PANEL_BRIDGE_TOKEN")
    session_token = session_token or os.environ.get("PANEL_SESSION_TOKEN")
    token = session_token or bridge_token
    if not worker_url or not token:
        return None
    # Sin panel no hace falta tenant (regla 2026-11-18); el import va
    # aqui, despues del retorno temprano, para no exigir el modulo
    # cuando no hay nada que leer.
    from .tenant import require_tenant

    tenant = require_tenant(tenant)
    url = f"{worker_url}/api/bridge/settings?tenant={tenant}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": "viral-clip-agent/panel-settings",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    settings = data.get("settings") if isinstance(data, dict) else None
    return sanitize_panel_settings(settings) if settings else None


def load_config_with_panel():
    """load_config() + Ajustes del panel si hay entorno configurado.

    Sin PANEL_WORKER_URL ni token (PANEL_SESSION_TOKEN o
    PANEL_BRIDGE_TOKEN) devuelve exactamente load_config(). Con
    ellos, aplica los Ajustes encima. Si el panel está configurado
    y falta PANEL_TENANT (o no está en la lista blanca), levanta
    TenantError: el llamador no debe tragarlo con un fallback
    silencioso a config.yaml.
    """
    from .config import load_config
    cfg = load_config()
    settings = fetch_panel_settings()
    if settings:
        cfg = apply_panel_settings(cfg, settings)
    return cfg
