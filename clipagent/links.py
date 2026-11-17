"""Entrada única de links (Fase 4.2).

Acepta links de canal (twitch.tv/<canal>, kick.com/<canal>) y
links de VOD (twitch.tv/videos/<id>, kick.com/<canal>/videos/
<uuid>). Extrae plataforma y slug, valida y agrega a
streamers.yaml. Error claro si no se reconoce.

Un link de VOD de Twitch no lleva el canal en la URL: se resuelve
con la API Helix (Get Videos -> broadcaster). Sin credenciales de
Twitch no se puede resolver y se pide el link del canal.
"""
import re

from . import streamers as _streamers

CHANNEL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?(twitch\.tv|kick\.com)/([A-Za-z0-9_]+)"
    r"/?(?:[?#].*)?$")
TWITCH_VOD_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?twitch\.tv/videos/(\d+)/?$")
KICK_VOD_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?kick\.com/([A-Za-z0-9_]+)/videos/"
    r"[0-9a-fA-F-]{36}/?$")


def parse_streamer_link(link):
    """(platform, slug, vod_id|None) o ValueError con el motivo.

    Un VOD de Twitch devuelve (twitch, None, vod_id): el slug se
    resuelve aparte (ver resolve_twitch_vod).
    """
    link = (link or "").strip()
    if not link:
        raise ValueError("link vacío: pega un link de twitch.tv/<canal>, "
                         "kick.com/<canal> o un link de VOD")
    m = TWITCH_VOD_RE.match(link)
    if m:
        return "twitch", None, m.group(1)
    m = KICK_VOD_RE.match(link)
    if m:
        return "kick", m.group(1).lower(), None
    m = CHANNEL_RE.match(link)
    if m:
        host, slug = m.group(1), m.group(2).lower()
        platform = "twitch" if host == "twitch.tv" else "kick"
        return platform, slug, None
    raise ValueError(
        f"no reconozco el link: {link!r}. Usa twitch.tv/<canal>, "
        "kick.com/<canal> o un link de VOD de Twitch/Kick.")


def resolve_twitch_vod(vod_id, base_dir=None, request_fn=None):
    """El canal de un VOD de Twitch por la API Helix.

    Devuelve el slug (login) o None si no se puede resolver (sin
    credenciales o la API no lo devuelve).
    """
    from .platforms import TwitchAdapter, _http
    token = TwitchAdapter(base_dir, request_fn).app_token()
    if not token:
        return None
    from . import credentials as _creds
    client_id = _creds.get_secret("TWITCH_CLIENT_ID")
    status, payload = _http(
        "GET", f"https://api.twitch.tv/helix/videos?id={vod_id}",
        {"Authorization": f"Bearer {token}", "Client-Id": client_id or ""},
        request_fn=request_fn)
    data = (payload.get("data") or [])
    if status != 200 or not data:
        return None
    user_id = data[0].get("user_id")
    if not user_id:
        return None
    status, payload = _http(
        "GET", f"https://api.twitch.tv/helix/users?id={user_id}",
        {"Authorization": f"Bearer {token}", "Client-Id": client_id or ""},
        request_fn=request_fn)
    data = (payload.get("data") or [])
    if status != 200 or not data:
        return None
    return (data[0].get("login") or "").lower() or None


def add_link(link, base_dir=None, resolve_fn=None):
    """Agrega el streamer de un link a streamers.yaml.

    Devuelve (ok, mensaje). Un VOD de Twitch sin credenciales para
    resolverlo da un error claro (no se inventa el canal).
    """
    try:
        platform, slug, vod_id = parse_streamer_link(link)
    except ValueError as e:
        return False, str(e)
    if slug is None:
        resolve = resolve_fn or resolve_twitch_vod
        slug = resolve(vod_id, base_dir)
        if not slug:
            return False, (
                f"del VOD {vod_id} no se puede sacar el canal sin la "
                "API de Twitch: configura TWITCH_CLIENT_ID y "
                "TWITCH_CLIENT_SECRET en .env (clipagent setup twitch) "
                "o pega el link del canal (twitch.tv/<canal>)")
    entry = {"name": slug, "platform": platform,
             "channel_url": (f"https://www.twitch.tv/{slug}" if platform == "twitch"
                             else f"https://kick.com/{slug}"),
             "estado": "concedido",
             "fecha_permiso": _today()}
    return _streamers.add_streamer(entry, base_dir=base_dir)


def _today():
    from datetime import date
    return date.today().isoformat()
