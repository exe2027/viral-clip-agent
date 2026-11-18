#!/usr/bin/env python3
"""Ajustes del panel leídos por el cerebro (2026-11-18).

El usuario cambia la configuración desde la pestaña Ajustes del
panel web (https://avc-em9.pages.dev): se guarda en D1 por tenant
y el cerebro la lee por el endpoint puente del Worker, sin editar
código:

    GET {worker_url}/api/bridge/settings?tenant={tenant}
    Authorization: Bearer <BRIDGE_TOKEN>

Respuesta: {"settings": {...}} con los campos de PANEL_SETTINGS
(abajo). Sin las variables de entorno PANEL_WORKER_URL y
PANEL_BRIDGE_TOKEN, el cerebro usa solo config.yaml (comportamiento
de siempre); con ellas, los Ajustes mandan sobre config.yaml.

Campos (mismos nombres en D1, en el Worker y aquí):
  layout: "rdjavi" | "plain" (default "rdjavi" para GTA 6)
  subtitle_style: "ref" | "karaoke" | "tiktok" | "highlight" (default "karaoke", 2026-11-18)
  min_duration_s / max_duration_s: 40 / 60
  min_virality: 50, min_seo: 90
  youtube_plan_per_day: 3, youtube_max_per_day: 5
  other_plan_per_day: 2, other_max_per_day: 3
  new_account_daily: 1
  tiktok_zernio_per_day: 15, tiktok_zernio_pending_drafts_max: 5
  min_spacing_hours: 2
  category: "GTA VI" (el gate exige GTA VI)
  thumbnail_style: "gta6"
  hype_burst: true | false (hype por ráfaga on/off)
  loop_running: true | false (el loop Iniciar/Parar; lo lee el
    Worker, el cerebro solo lo muestra)
"""
import json
import os
import urllib.request

PANEL_SETTINGS_FIELDS = {
    "layout": str,
    "subtitle_style": str,
    "min_duration_s": int,
    "max_duration_s": int,
    "min_virality": int,
    "min_seo": int,
    "youtube_plan_per_day": int,
    "youtube_max_per_day": int,
    "other_plan_per_day": int,
    "other_max_per_day": int,
    "new_account_daily": int,
    "tiktok_zernio_per_day": int,
    "tiktok_zernio_pending_drafts_max": int,
    "min_spacing_hours": int,
    "category": str,
    "thumbnail_style": str,
    "hype_burst": bool,
    "loop_running": bool,
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
    "other_plan_per_day": (0, 15),
    "other_max_per_day": (0, 15),
    "new_account_daily": (0, 5),
    "tiktok_zernio_per_day": (0, 15),
    "tiktok_zernio_pending_drafts_max": (0, 5),
    "min_spacing_hours": (0, 24),
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
    limits = {}
    if clean.get("youtube_plan_per_day") is not None:
        limits.setdefault("youtube_shorts", {})["plan_per_day"] = clean["youtube_plan_per_day"]
    if clean.get("youtube_max_per_day") is not None:
        limits.setdefault("youtube_shorts", {})["max_per_day"] = clean["youtube_max_per_day"]
    for plat in ("tiktok", "instagram_reels", "facebook_reels"):
        if clean.get("other_plan_per_day") is not None:
            limits.setdefault(plat, {})["plan_per_day"] = clean["other_plan_per_day"]
        if clean.get("other_max_per_day") is not None:
            limits.setdefault(plat, {})["max_per_day"] = clean["other_max_per_day"]
    if clean.get("new_account_daily") is not None:
        limits["new_account_daily"] = clean["new_account_daily"]
    if clean.get("tiktok_zernio_per_day") is not None:
        limits.setdefault("tiktok", {})["zernio_per_day"] = clean["tiktok_zernio_per_day"]
    if clean.get("tiktok_zernio_pending_drafts_max") is not None:
        limits.setdefault("tiktok", {})["zernio_pending_drafts_max"] = clean["tiktok_zernio_pending_drafts_max"]
    if clean.get("min_spacing_hours") is not None:
        limits["min_spacing_hours"] = clean["min_spacing_hours"]
    if limits:
        cfg["_panel_limits_override"] = limits
    return cfg


def fetch_panel_settings(worker_url=None, bridge_token=None, tenant="sebastian",
                         timeout=10):
    """Lee los Ajustes del panel por el endpoint puente.

    Devuelve el dict de settings o None (sin red, sin token, error
    HTTP o respuesta inválida: nunca lanza excepción; el cerebro
    sigue con config.yaml).
    """
    worker_url = (worker_url or os.environ.get("PANEL_WORKER_URL") or "").rstrip("/")
    bridge_token = bridge_token or os.environ.get("PANEL_BRIDGE_TOKEN")
    tenant = tenant or os.environ.get("PANEL_TENANT") or "sebastian"
    if not worker_url or not bridge_token:
        return None
    url = f"{worker_url}/api/bridge/settings?tenant={tenant}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {bridge_token}",
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

    Sin PANEL_WORKER_URL/PANEL_BRIDGE_TOKEN devuelve exactamente
    load_config(). Con ellos, aplica los Ajustes encima.
    """
    from .config import load_config
    cfg = load_config()
    settings = fetch_panel_settings()
    if settings:
        cfg = apply_panel_settings(cfg, settings)
    return cfg
