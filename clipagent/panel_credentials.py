#!/usr/bin/env python3
"""Bootstrap de credenciales del cerebro desde el panel (2026-11-19).

El cerebro genérico NO usa secretos por tenant en GitHub ni
environments por tenant: el usuario solo toca el panel
(https://avc-em9.pages.dev), donde ya conecta sus cuentas. En
cada dispatch el Worker mete un token de un solo uso en el
client_payload; este módulo lo canjea (vía PanelBridge) y pide
las credenciales descifradas del tenant a
GET /api/bridge/credentials.

Uso en el workflow (primer paso, antes de cualquier otro):
    python -m clipagent.panel_credentials
con PANEL_BRAIN_TOKEN + PANEL_TENANT en el entorno (los pone el
workflow desde github.event.client_payload). En local vale el
login de servicio (PANEL_USERNAME/PANEL_PASSWORD) como fallback.

Lo que materializa (nombres que ya lee el resto del cerebro):
  - TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET (o nada si el tenant
    no tiene Twitch en el panel: el escaneo sigue por GQL público;
    la solución es añadir la cuenta EN EL PANEL, no en GitHub)
  - GEMINI_API_KEY
  - ZERNIO_API_KEY / ZERNIO_TIKTOK_ACCOUNT_ID (ES) y las variantes
    _EN (reservadas: la selección por idioma de Zernio aún no
    existe en el código; se dejan puestas para cuando llegue)
  - IG_USER_ID / FACEBOOK_PAGE_ID
  - CLIP_PUBLIC_BASE_URL
  - tokens/youtube_shorts.json (ES, formato authorized-user) +
    client_secrets.json en la raíz (legado) +
    tokens/youtube_en_token.json (EN, si existe)

Seguridad: cada valor se enmascara en los logs de GitHub
(::add-mask::) y se escribe en $GITHUB_ENV para los pasos
siguientes; por stdout solo salen NOMBRES (qué hay y qué falta),
nunca valores. Los archivos de tokens quedan 0600 (save_token).
Si no hay token del cerebro ni login, sale con error claro
("dispara desde el panel con Iniciar") en vez de seguir a ciegas.
"""
import json
import os
import sys

YOUTUBE_TOKEN_URI = "https://oauth2.googleapis.com/token"
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


class CredentialError(Exception):
    pass


def fetch_brain_credentials(bridge):
    """GET /api/bridge/credentials vía el bridge (sesión canjeada).

    Devuelve el dict tal cual lo entrega el panel. Levanta
    CredentialError si la respuesta no trae credenciales.
    """
    res = bridge.brain_credentials()
    if not isinstance(res, dict) or not res.get("ok"):
        raise CredentialError(
            f"el panel no devolvió credenciales: {str(res)[:200]}")
    return res


def _mask(value):
    """Registra el valor como secreto en los logs de Actions."""
    if value and "\n" not in str(value):
        print(f"::add-mask::{value}", flush=True)


def _set_env(name, value, github_env_path=None):
    """Pone la variable en este proceso y, si hay $GITHUB_ENV,
    la deja para los pasos siguientes del workflow."""
    if not value:
        return False
    os.environ[name] = value
    _mask(value)
    path = github_env_path or os.environ.get("GITHUB_ENV")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    return True


def _youtube_token_doc(oauth):
    """Token authorized-user para Credentials.from_authorized_user_file."""
    return {
        "token": "",
        "refresh_token": oauth["refresh_token"],
        "token_uri": YOUTUBE_TOKEN_URI,
        "client_id": oauth["client_id"],
        "client_secret": oauth["client_secret"],
        "scopes": YOUTUBE_SCOPES,
        "expiry": "1970-01-01T00:00:00Z",
    }


def _youtube_client_secrets_doc(oauth):
    return {"installed": {
        "client_id": oauth["client_id"],
        "client_secret": oauth["client_secret"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": YOUTUBE_TOKEN_URI,
        "redirect_uris": ["http://localhost"],
    }}


def materialize_credentials(creds, base_dir=None):
    """Materializa las credenciales del panel en entorno + archivos.

    Devuelve un resumen {nombre: bool} (qué hay y qué falta) para
    imprimirlo sin valores. No levanta si falta una credencial:
    el cerebro sigue degradado y avisa; solo falla el canje.
    """
    from . import credentials as _creds

    base = base_dir or _creds.BASE_DIR
    github_env = os.environ.get("GITHUB_ENV")
    summary = {}

    twitch = creds.get("twitch") or {}
    summary["twitch"] = bool(twitch.get("client_id")
                             and twitch.get("client_secret"))
    if summary["twitch"]:
        _set_env("TWITCH_CLIENT_ID", twitch["client_id"], github_env)
        _set_env("TWITCH_CLIENT_SECRET", twitch["client_secret"], github_env)

    gemini = creds.get("gemini") or {}
    summary["gemini"] = _set_env(
        "GEMINI_API_KEY", gemini.get("api_key") or "", github_env)

    zernio = creds.get("zernio") or {}
    z_es = zernio.get("es") or {}
    summary["zernio_es"] = bool(z_es.get("api_key")
                                and z_es.get("account_id"))
    if summary["zernio_es"]:
        _set_env("ZERNIO_API_KEY", z_es["api_key"], github_env)
        _set_env("ZERNIO_TIKTOK_ACCOUNT_ID", z_es["account_id"], github_env)
    z_en = zernio.get("en") or {}
    summary["zernio_en"] = bool(z_en.get("api_key")
                                and z_en.get("account_id"))
    if summary["zernio_en"]:
        _set_env("ZERNIO_API_KEY_EN", z_en["api_key"], github_env)
        _set_env("ZERNIO_TIKTOK_ACCOUNT_ID_EN", z_en["account_id"], github_env)

    meta = creds.get("meta") or {}
    summary["meta"] = bool(meta.get("ig_user_id")
                          and meta.get("page_id"))
    if summary["meta"]:
        _set_env("IG_USER_ID", meta["ig_user_id"], github_env)
        _set_env("FACEBOOK_PAGE_ID", meta["page_id"], github_env)
    # El page_token de Meta lo usa el Worker al publicar, no el
    # cerebro: no se materializa (no hay variable que lo lea).
    if meta.get("page_token"):
        _mask(meta["page_token"])

    summary["clip_public_base_url"] = _set_env(
        "CLIP_PUBLIC_BASE_URL",
        creds.get("clip_public_base_url") or "", github_env)

    youtube = creds.get("youtube") or {}
    yt_es = youtube.get("es") or {}
    summary["youtube_es"] = False
    if yt_es.get("client_id") and yt_es.get("client_secret") \
            and yt_es.get("refresh_token"):
        _creds.save_token("youtube_shorts",
                          _youtube_token_doc(yt_es), base)
        cs_path = os.path.join(base, "client_secrets.json")
        with open(cs_path, "w", encoding="utf-8") as f:
            json.dump(_youtube_client_secrets_doc(yt_es), f, indent=2)
        os.chmod(cs_path, 0o600)
        for v in (yt_es["client_id"], yt_es["client_secret"],
                  yt_es["refresh_token"]):
            _mask(v)
        summary["youtube_es"] = True
    yt_en = youtube.get("en") or {}
    summary["youtube_en"] = False
    if yt_en.get("client_id") and yt_en.get("client_secret") \
            and yt_en.get("refresh_token"):
        _creds.save_token("youtube_en_token",
                          _youtube_token_doc(yt_en), base)
        for v in (yt_en["client_id"], yt_en["client_secret"],
                  yt_en["refresh_token"]):
            _mask(v)
        summary["youtube_en"] = True

    tiktok_direct = creds.get("tiktok_direct") or {}
    summary["tiktok_direct_es"] = bool((tiktok_direct.get("es") or {}).get("access_token"))
    summary["tiktok_direct_en"] = bool((tiktok_direct.get("en") or {}).get("access_token"))
    return summary


def print_summary(summary, tenant):
    """Resumen por stdout: solo NOMBRES (qué hay / qué falta)."""
    print(f"[panel-credentials] tenant={tenant}", flush=True)
    for name in ("twitch", "gemini", "youtube_es", "youtube_en",
                 "zernio_es", "zernio_en", "meta", "clip_public_base_url",
                 "tiktok_direct_es", "tiktok_direct_en"):
        print(f"[panel-credentials]   {name}: "
              f"{'sí' if summary.get(name) else 'FALTA (añadir en el panel)'}",
              flush=True)


def main(argv=None):
    """Entry point del paso bootstrap del workflow."""
    from .panel_bridge import PanelBridge, PanelError

    bridge = PanelBridge.from_env()
    if bridge is None:
        print("[panel-credentials] ERROR: sin PANEL_BRAIN_TOKEN ni "
              "PANEL_SESSION_TOKEN (sesión del panel). En detect, "
              "dispara el workflow desde el panel (botón Iniciar): "
              "el dispatch lleva el token de un solo uso. En cut/vod, "
              "la sesión llega por el artefacto panel-session de "
              "esta corrida: si falta, falló su descarga.",
              file=sys.stderr, flush=True)
        return 1
    try:
        creds = fetch_brain_credentials(bridge)
    except (PanelError, CredentialError) as e:
        print(f"[panel-credentials] ERROR: no se pudieron pedir las "
              f"credenciales al panel: {e}", file=sys.stderr, flush=True)
        return 1
    # La sesión canjeada (12 h) se exporta para los pasos siguientes
    # del MISMO job ($GITHUB_ENV). NO se pasa a otros jobs por
    # output ($GITHUB_OUTPUT): GitHub VACÍA un output cuyo valor
    # fue registrado con ::add-mask:: (verificado 2026-11-19 con
    # un workflow de prueba: MASKED EMPTY / PLAIN OK, y el orden
    # no importa — el vaciado pasa al consumirlo, no al escribirlo).
    # La sesión viaja entre jobs por ARTEFACTO (panel-session):
    # detect la sube y cut/vod la descargan (ver cerebro-live.yml).
    # El valor se enmascara antes de salir por ningún canal (nunca
    # se imprime en claro).
    session_token = getattr(bridge, "_token", None)
    if session_token:
        _set_env("PANEL_SESSION_TOKEN", session_token)
    summary = materialize_credentials(creds)
    print_summary(summary, creds.get("tenant") or bridge.tenant or "?")
    missing = [k for k, v in summary.items() if not v]
    if missing:
        print("[panel-credentials] AVISO: faltan en el panel: "
              + ", ".join(missing) + ". El cerebro sigue degradado; "
              "añade esas cuentas EN EL PANEL (nunca en GitHub).",
              flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
