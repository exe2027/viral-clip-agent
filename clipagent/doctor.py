"""Doctor de conexiones (Fase 3.5).

`clipagent doctor` comprueba TODAS las conexiones con llamadas de
solo lectura (sin publicar nada) y muestra una tabla: plataforma,
estado (OK / falta paso / token vencido / sin permisos), causa y
cómo arreglarlo. Se ejecuta también al iniciar el servicio (6.1).

Estados:
- OK: la llamada de solo lectura pasó.
- falta paso: falta una credencial o un paso manual (setup).
- token vencido: hay token pero caducó o ya no vale.
- sin permisos: el token no tiene el scope que hace falta.
- sin verificar: no hay forma de comprobarlo sin publicar (Kick:
  no tiene API oficial de chat).
"""
import os
import time

from . import accounts as _accounts
from . import credentials as _creds
from .platforms import GRAPH_VERSION, TwitchAdapter, _http

GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"


def _row(platform, label, status, cause, fix):
    return {"platform": platform, "label": label, "status": status,
            "cause": cause, "fix": fix}


def _token_status(platform, base_dir):
    """(estado, causa) del token guardado, o (None, ...) si no hay."""
    token = _creds.load_token(platform, base_dir)
    if token is None:
        return None, "sin token en tokens/"
    if _creds.token_expired(token):
        return "token vencido", "el token caducó"
    return "ok", ""


def probe_youtube(base_dir=None, request_fn=None):
    label = "YouTube Shorts"
    state, cause = _token_status("youtube_shorts", base_dir)
    if state is None:
        return _row("youtube_shorts", label, "falta paso", cause,
                    "ejecuta: clipagent setup youtube_shorts")
    if state == "token vencido":
        return _row("youtube_shorts", label, "token vencido", cause,
                    "renovar: clipagent setup youtube_shorts "
                    "(el refresh token se usa solo)")
    token = _creds.load_token("youtube_shorts", base_dir)
    access = (token or {}).get("access_token")
    # tokeninfo es solo lectura: valida el token y sus scopes.
    status, payload = _http(
        "GET", "https://oauth2.googleapis.com/tokeninfo?access_token="
        + (access or ""), request_fn=request_fn)
    if status == 200:
        scopes = (payload.get("scope") or "").split()
        if "https://www.googleapis.com/auth/youtube.upload" not in scopes:
            return _row("youtube_shorts", label, "sin permisos",
                        "el token no tiene el scope youtube.upload",
                        "reautorizar con el scope de subida: "
                        "clipagent setup youtube_shorts")
        return _row("youtube_shorts", label, "OK",
                    "token válido con scope youtube.upload",
                    "nada que hacer")
    if status == 400:
        return _row("youtube_shorts", label, "token vencido",
                    "Google rechaza el token (tokeninfo 400)",
                    "renovar: clipagent setup youtube_shorts")
    return _row("youtube_shorts", label, "falta paso",
                f"no se pudo comprobar (HTTP {status}, error transitorio)",
                "reintentar en un momento: clipagent doctor")


def probe_tiktok(base_dir=None, request_fn=None):
    label = "TikTok"
    state, cause = _token_status("tiktok", base_dir)
    if state is None:
        return _row("tiktok", label, "falta paso", cause,
                    "ejecuta: clipagent setup tiktok")
    if state == "token vencido":
        return _row("tiktok", label, "token vencido", cause,
                    "renovar con el refresh_token: clipagent setup tiktok")
    token = _creds.load_token("tiktok", base_dir)
    access = (token or {}).get("access_token")
    status, payload = _http(
        "GET", "https://open.tiktokapis.com/v2/user/info/"
        "?fields=open_id,display_name",
        {"Authorization": f"Bearer {access}"}, request_fn=request_fn)
    if status == 200:
        return _row("tiktok", label, "OK",
                    "user/info pasó (token válido)",
                    "nada que hacer")
    code = (payload.get("error") or {}).get("code")
    if code in ("access_token_invalid",):
        return _row("tiktok", label, "token vencido",
                    f"TikTok rechaza el token ({code})",
                    "renovar: clipagent setup tiktok")
    if code in ("scope_not_authorized",):
        return _row("tiktok", label, "sin permisos",
                    f"falta el scope ({code})",
                    "reautorizar con video.publish: clipagent setup tiktok")
    return _row("tiktok", label, "falta paso",
                f"no se pudo comprobar (HTTP {status}, error transitorio)",
                "reintentar en un momento: clipagent doctor")


def probe_instagram(base_dir=None, request_fn=None):
    label = "Instagram Reels"
    state, cause = _token_status("instagram_reels", base_dir)
    if state is None:
        return _row("instagram_reels", label, "falta paso", cause,
                    "ejecuta: clipagent setup instagram_reels")
    if state == "token vencido":
        return _row("instagram_reels", label, "token vencido", cause,
                    "renovar: clipagent setup instagram_reels")
    token = _creds.load_token("instagram_reels", base_dir)
    access = (token or {}).get("access_token")
    status, payload = _http(
        "GET", f"{GRAPH}/me?fields=id,name&access_token={access}",
        request_fn=request_fn)
    if status == 200:
        return _row("instagram_reels", label, "OK",
                    "token válido (Graph API responde)",
                    "nada que hacer")
    code = (payload.get("error") or {}).get("code")
    if code in (190, 102):
        return _row("instagram_reels", label, "token vencido",
                    f"Meta rechaza el token (error {code})",
                    "renovar: clipagent setup instagram_reels")
    if code in (200, 10):
        return _row("instagram_reels", label, "sin permisos",
                    f"falta un permiso de Meta (error {code})",
                    "reautorizar con instagram_business_content_publish: "
                    "clipagent setup instagram_reels")
    return _row("instagram_reels", label, "falta paso",
                f"no se pudo comprobar (HTTP {status}, error transitorio)",
                "reintentar en un momento: clipagent doctor")


def probe_facebook(base_dir=None, request_fn=None):
    label = "Facebook Reels"
    state, cause = _token_status("facebook_reels", base_dir)
    if state is None:
        return _row("facebook_reels", label, "falta paso", cause,
                    "ejecuta: clipagent setup facebook_reels")
    if state == "token vencido":
        return _row("facebook_reels", label, "token vencido", cause,
                    "renovar: clipagent setup facebook_reels")
    token = _creds.load_token("facebook_reels", base_dir)
    access = (token or {}).get("access_token")
    status, payload = _http(
        "GET", f"{GRAPH}/me/accounts?fields=id,name&access_token={access}",
        request_fn=request_fn)
    if status == 200:
        pages = (payload.get("data") or [])
        if pages:
            names = ", ".join(p.get("name", "?") for p in pages)
            return _row("facebook_reels", label, "OK",
                        f"token válido; Páginas visibles: {names}",
                        "nada que hacer")
        return _row("facebook_reels", label, "sin permisos",
                    "el token no ve ninguna Página",
                    "revisar el rol en tu Página y "
                    "reautorizar: clipagent setup facebook_reels")
    code = (payload.get("error") or {}).get("code")
    if code in (190, 102):
        return _row("facebook_reels", label, "token vencido",
                    f"Meta rechaza el token (error {code})",
                    "renovar: clipagent setup facebook_reels")
    return _row("facebook_reels", label, "falta paso",
                f"no se pudo comprobar (HTTP {status}, error transitorio)",
                "reintentar en un momento: clipagent doctor")


def probe_twitch(base_dir=None, request_fn=None):
    label = "Twitch (watcher)"
    adapter = TwitchAdapter(base_dir, request_fn)
    if not _creds.get_secret("TWITCH_CLIENT_ID"):
        return _row("twitch", label, "falta paso",
                    "sin TWITCH_CLIENT_ID en .env (usa GQL como respaldo)",
                    "registra una app en dev.twitch.tv y pon las claves "
                    "en .env: clipagent setup twitch")
    result = adapter.validate()
    if result.get("ok"):
        return _row("twitch", label, "OK",
                    f"token de app válido (expira en "
                    f"{result.get('expires_in')} s)",
                    "nada que hacer")
    return _row("twitch", label, "token vencido",
                result.get("reason", "no se pudo validar el token"),
                "reintentar: el token se pide solo al arrancar el watcher")


def probe_kick(base_dir=None, request_fn=None):
    return _row("kick", "Kick", "sin verificar",
                "no hay API oficial de chat que comprobar; la descarga "
                "de VODs funciona sin credenciales",
                "nada que hacer")


PROBES = {
    "youtube_shorts": probe_youtube,
    "tiktok": probe_tiktok,
    "instagram_reels": probe_instagram,
    "facebook_reels": probe_facebook,
    "twitch": probe_twitch,
    "kick": probe_kick,
}


def run_doctor(base_dir=None, output=print, request_fn=None):
    """Comprueba todas las conexiones y muestra la tabla.

    Devuelve 0 si todas están OK (o sin verificar, como Kick),
    1 si alguna necesita un paso. Escribe last_verified en
    accounts.yaml.
    """
    rows = []
    for platform, probe in PROBES.items():
        try:
            rows.append(probe(base_dir, request_fn))
        except Exception as e:
            rows.append(_row(platform, platform, "falta paso",
                             f"la comprobación falló: {e}",
                             "reintentar: clipagent doctor"))
    icons = {"OK": "🟢", "falta paso": "🟡", "token vencido": "🔴",
             "sin permisos": "🔴", "sin verificar": "⚪"}
    lines = ["DOCTOR — conexiones (solo lectura, sin publicar nada)"]
    for row in rows:
        icon = icons.get(row["status"], "❓")
        lines.append(f"\n{icon} {row['label']} — {row['status'].upper()}")
        lines.append(f"   Causa: {row['cause']}")
        lines.append(f"   Arreglo: {row['fix']}")
    output("\n".join(lines))
    _write_last_verified(base_dir)
    bad = [r for r in rows if r["status"] in (
        "falta paso", "token vencido", "sin permisos")]
    return 1 if bad else 0


def _write_last_verified(base_dir=None):
    """Guarda la fecha de la comprobación en accounts.yaml.

    Edición quirúrgica de la línea last_verified: reescribir el
    YAML entero con yaml.safe_dump borraría los comentarios del
    archivo, así que solo se reemplaza esa línea (o se añade si
    no existe).
    """
    import re
    base = base_dir or _accounts.BASE_DIR
    path = os.path.join(base, "accounts.yaml")
    if not os.path.isfile(path):
        return
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        new, n = re.subn(r"^last_verified:.*$",
                         f"last_verified: {stamp}", text,
                         count=1, flags=re.MULTILINE)
        if n == 0:
            new = f"last_verified: {stamp}\n" + text
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
    except OSError:
        pass
