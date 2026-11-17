"""Renovación automática de tokens, antes de que venzan (Fase 3.6).

`refresh_all()` se ejecuta al arrancar el servicio y periódicamente:
renueva cada token que vence en menos de 7 días (o ya venció). Si
un refresco falla, esa plataforma pasa a borrador y se notifica
(ver `notify`); nunca se pierde un clip por un token vencido: el
clip queda en la cola de reintento, no se descarta.

Renovación por plataforma (sin librerías nuevas, solo urllib):
- YouTube: POST oauth2.googleapis.com/token con grant_type=
  refresh_token (client_id/secret de client_secrets.json).
- TikTok: POST open.tiktokapis.com/v2/oauth/token/ con el
  refresh_token (ver platforms.TikTokAdapter).
- Twitch: se pide otro App Access Token igual (client credentials).
- Instagram: canje del token corto por uno de larga duración
  (60 días) en la Graph API (fb_exchange_token).
- Facebook: el Page access token derivado de un token de usuario
  de larga duración NO caduca por diseño; si el guardado trae
  expires_at, no se renueva solo: hay que reautorizar con setup.
"""
import json
import time
import urllib.parse

from . import credentials as _creds
from .platforms import GRAPH_VERSION, TikTokAdapter, TwitchAdapter, _http

REFRESH_WITHIN_S = 7 * 24 * 3600  # renovar si vence en menos de 7 días


def refresh_due(platform, base_dir=None, within_s=REFRESH_WITHIN_S,
                now=None):
    """True si el token vence dentro de `within_s` (o ya venció).
    False si no hay token (eso es 'falta paso', no 'renovar')."""
    token = _creds.load_token(platform, base_dir)
    if token is None:
        return False
    expires_at = token.get("expires_at")
    if expires_at is None:
        return bool(token.get("refresh_token"))
    return float(expires_at) - (now if now is not None else time.time()) <= within_s


def _save_merged(platform, old, new, base_dir):
    merged = dict(old)
    merged.update(new)
    if "expires_in" in new:
        merged["expires_at"] = int(time.time()) + int(new["expires_in"])
    _creds.save_token(platform, merged, base_dir)
    return merged


def refresh_youtube(base_dir=None, request_fn=None):
    """Renueva el access token de YouTube con el refresh_token."""
    token = _creds.load_token("youtube_shorts", base_dir) or {}
    refresh = token.get("refresh_token")
    if not refresh:
        return False, "sin refresh_token: reautorizar con setup"
    try:
        with open(_creds.client_secrets_path(base_dir),
                   encoding="utf-8") as f:
            secrets = json.load(f)
        installed = secrets.get("installed", secrets)
        client_id = installed.get("client_id")
        client_secret = installed.get("client_secret")
    except (OSError, ValueError):
        return False, "sin client_secrets.json: reautorizar con setup"
    body = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh,
        "grant_type": "refresh_token"}).encode()
    status, payload = _http(
        "POST", "https://oauth2.googleapis.com/token",
        {"Content-Type": "application/x-www-form-urlencoded"},
        body, request_fn=request_fn)
    if status != 200 or "access_token" not in payload:
        return False, (f"Google rechazó el refresco (HTTP {status}): "
                       "reautorizar con setup")
    _save_merged("youtube_shorts", token, payload, base_dir)
    return True, "token renovado"


def refresh_instagram(base_dir=None, request_fn=None):
    """Canjea el token de Instagram por uno de larga duración
    (60 días). Necesita META_APP_ID y META_APP_SECRET en .env."""
    token = _creds.load_token("instagram_reels", base_dir) or {}
    short = token.get("access_token")
    app_id = _creds.get_secret("META_APP_ID")
    app_secret = _creds.get_secret("META_APP_SECRET")
    if not short:
        return False, "sin token: reautorizar con setup"
    if not app_id or not app_secret:
        return False, "falta META_APP_ID/META_APP_SECRET en .env"
    params = {"grant_type": "fb_exchange_token",
              "client_id": app_id, "client_secret": app_secret,
              "fb_exchange_token": short}
    url = (f"https://graph.facebook.com/{GRAPH_VERSION}/oauth/"
           f"access_token?{urllib.parse.urlencode(params)}")
    status, payload = _http("GET", url, request_fn=request_fn)
    if status != 200 or "access_token" not in payload:
        return False, (f"Meta rechazó el canje (HTTP {status}): "
                       "reautorizar con setup")
    _save_merged("instagram_reels", token, payload, base_dir)
    return True, "token de larga duración guardado (60 días)"


def refresh_platform(platform, base_dir=None, request_fn=None):
    """Renueva el token de una plataforma. Devuelve (ok, mensaje)."""
    if platform == "youtube_shorts":
        return refresh_youtube(base_dir, request_fn)
    if platform == "tiktok":
        ok = TikTokAdapter(base_dir, request_fn).refresh_token()
        return ok, ("token renovado" if ok else
                    "no se pudo renovar: reautorizar con setup")
    if platform == "twitch":
        token = TwitchAdapter(base_dir, request_fn).app_token()
        return (token is not None), (
            "token de app renovado" if token else
            "sin credenciales de Twitch en .env")
    if platform == "instagram_reels":
        return refresh_instagram(base_dir, request_fn)
    if platform == "facebook_reels":
        return False, ("el Page access token no caduca por diseño "
                       "(derivado de un token de larga duración); si "
                       "el guardado trae expires_at, reautorizar "
                       "con setup")
    return False, f"plataforma desconocida: {platform}"


def refresh_all(base_dir=None, request_fn=None, notify=None):
    """Renueva todos los tokens que vencen pronto.

    Devuelve {plataforma: (ok, mensaje)}. Cada fallo llama a
    notify(mensaje) si se pasó, para que el servicio ponga esa
    plataforma en borrador y avise (nunca se pierde un clip: queda
    en la cola de reintento).
    """
    results = {}
    for platform in ("youtube_shorts", "tiktok", "twitch",
                     "instagram_reels", "facebook_reels"):
        if not refresh_due(platform, base_dir):
            continue
        ok, msg = refresh_platform(platform, base_dir, request_fn)
        results[platform] = (ok, msg)
        if not ok and notify is not None:
            notify(f"[{platform}] no se pudo renovar el token: {msg}. "
                   "La plataforma pasa a borrador; los clips quedan "
                   "en la cola de reintento.")
    return results
