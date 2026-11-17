"""Estado de las cuentas, calculado desde credenciales reales (3.2).

accounts.yaml YA NO es la fuente de verdad "a mano": solo guarda
identificadores no secretos (handles, nombre de la Página) y la
fecha de la última verificación de `doctor`. Cada chequeo de
compute_state() dice de dónde sale su valor:

- "credenciales": se comprueba en tokens/ o client_secrets.json.
- "api": sonda de solo lectura (la pasa `doctor`, ver 3.5).
- "sin verificar": no se puede saber sin llamar a la API ni sin
  que el usuario lo confirme; vale None (ni pasa ni falla) y el
  estado queda en ámbar, nunca en verde inventado.

``probes`` (opcional) es {plataforma: {nombre_chequeo: bool}} con
los resultados de las sondas de solo lectura. Sin probes, todo lo
que exige una llamada a la API queda en None.
"""
import os

from . import credentials as _creds

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.yaml")

# Identificadores no secretos (lo único que vive en accounts.yaml).
DEFAULT_IDENTIFIERS = {
    "youtube_shorts": {
        "label": "YouTube ES — El Clip de GTA 6 (@elclipdegta6)",
        "handle": "@elclipdegta6",
        "notes": "Canal creado el 2026-11-16.",
    },
    "tiktok": {
        "label": "TikTok ES — El Clip de GTA 6 (@elclipdegta6____bits)",
        "handle": "@elclipdegta6____bits",
        "notes": "Cuenta creada por el usuario el 2026-11-16.",
    },
    "instagram_reels": {
        "label": "Instagram ES — @elclipdegta6",
        "handle": "@elclipdegta6",
        "notes": "Cuenta creada por el usuario el 2026-11-16 y "
                 "vinculada a la Página Elclipdegta6.",
    },
    "facebook_reels": {
        "label": "Facebook — Página Elclipdegta6",
        "page_name": "Elclipdegta6",
        "notes": "Página creada por el usuario el 2026-11-16.",
    },
}

# Piso de estado por plataforma (como antes): YouTube y TikTok
# nunca bajan de ámbar; Instagram y Facebook sí llegan a rojo
# cuando un requisito duro falla.
_AMBER_FLOOR = ("youtube_shorts", "tiktok")


def load_identifiers(base_dir=None):
    """Handles y nombres desde accounts.yaml (solo no secretos)."""
    base = base_dir or BASE_DIR
    out = {k: dict(v) for k, v in DEFAULT_IDENTIFIERS.items()}
    path = os.path.join(base, "accounts.yaml")
    if not os.path.isfile(path):
        return out
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, ImportError):
        return out
    for platform, ident in out.items():
        entry = data.get(platform) or {}
        for key in ("label", "handle", "page_name", "notes",
                    "created_at"):
            if entry.get(key):
                ident[key] = entry[key]
        if data.get("last_verified"):
            ident["last_verified"] = data["last_verified"]
    return out


def account_age_days(platform, base_dir=None, today=None):
    """Días desde que se creó la cuenta (None si no se sabe).

    `created_at` (YYYY-MM-DD) vive en accounts.yaml; sin él, la
    cuenta se trata como antigua (ritmo completo).
    """
    from datetime import date
    ident = load_identifiers(base_dir).get(platform) or {}
    created = ident.get("created_at")
    if not created:
        return None
    try:
        born = date.fromisoformat(str(created)[:10])
    except ValueError:
        return None
    ref = today or date.today()
    return max(0, (ref - born).days)


def is_new_account(platform, base_dir=None, today=None):
    """Cuenta nueva: menos de 7 días desde su creación (6.5).

    La primera semana el ritmo es reducido (NEW_ACCOUNT_DAILY en
    limits.yaml, 1/día).
    """
    age = account_age_days(platform, base_dir, today)
    return age is not None and age < 7


def _token_ok(platform, base_dir):
    """(presente, vigente, detalle) del token de una plataforma."""
    token = _creds.load_token(platform, base_dir)
    if token is None:
        legacy = None
        if platform == "youtube_shorts":
            legacy = os.path.isfile(_creds.legacy_token_path(base_dir))
        return False, f"sin token en tokens/{platform}.json" + (
            " (hay token.json legado en la raíz: migralo con setup)"
            if legacy else "")
    if _creds.token_expired(token):
        return False, "token vencido (renovar con setup o doctor)"
    return True, "token presente y vigente"


def _probe(probes, platform, name):
    """Valor de una sonda (True/False) o None si no se sondeó."""
    if not probes:
        return None
    return (probes.get(platform) or {}).get(name)


def _status(checks, platform):
    """green si todo True; red si algo False (salvo piso ámbar);
    amber si algo quedó sin verificar."""
    values = [c[1] for c in checks]
    if all(v is True for v in values):
        return "green"
    if any(v is False for v in values):
        return "amber" if platform in _AMBER_FLOOR else "red"
    return "amber"


def compute_state(base_dir=None, probes=None):
    """Estado calculado por plataforma.

    Devuelve {plataforma: {"label", "status", "checks": [(nombre,
    pasa|None, detalle)], "notes", "publish_mode"}}. Un chequeo en
    None significa "sin verificar", no "falla".
    """
    base = base_dir or BASE_DIR
    ident = load_identifiers(base)
    result = {}

    # YouTube: OAuth por credenciales; canal y strikes solo por API.
    yt_token_ok, yt_token_detail = _token_ok("youtube_shorts", base)
    yt_secrets = os.path.isfile(_creds.client_secrets_path(base))
    yt_checks = [
        ("canal creado",
         _probe(probes, "youtube_shorts", "channel_created"),
         ident["youtube_shorts"]["handle"] +
         " (sin verificar: hace falta una llamada a la API)"),
        ("canal verificado",
         _probe(probes, "youtube_shorts", "channel_verified"),
         "pendiente de verificar en YouTube Studio o con doctor"),
        ("OAuth configurado (client_secrets.json + token en tokens/)",
         bool(yt_secrets and yt_token_ok),
         yt_token_detail if not (yt_secrets and yt_token_ok)
         else "client_secrets.json + token vigente"),
        ("cuota suficiente para hoy",
         None if not yt_token_ok else True,
         "sin OAuth no se puede consultar la cuota" if not yt_token_ok
         else "bucket de videos.insert: 100 llamadas/día (ver limits.yaml)"),
        ("sin strikes",
         _probe(probes, "youtube_shorts", "no_strikes"),
         "sin verificar: hace falta una llamada a la API"),
    ]
    yt_status = _status(yt_checks, "youtube_shorts")
    result["youtube_shorts"] = {
        "label": ident["youtube_shorts"]["label"],
        "status": yt_status,
        "checks": yt_checks,
        "notes": ident["youtube_shorts"].get("notes", ""),
        "publish_mode": ("API (videos.insert)" if yt_status == "green"
                         else "DRAFT-FIRST hasta completar OAuth y verificación"),
    }

    # TikTok: token + app aprobada y scopes (solo por API o consola).
    tk_token_ok, tk_token_detail = _token_ok("tiktok", base)
    tk_scopes = (_creds.load_token("tiktok", base) or {}).get("scopes")
    tk_checks = [
        ("cuenta creada",
         _probe(probes, "tiktok", "account_created"),
         ident["tiktok"]["handle"] +
         " (sin verificar: hace falta una llamada a la API)"),
        ("app aprobada en TikTok for Developers",
         _probe(probes, "tiktok", "app_approved"),
         "sin app: no hay Direct Post ni borrador por API"),
        ("scopes video.publish + user.info.basic",
         (("video.publish" in (tk_scopes or [])) if tk_scopes else None),
         f"scopes guardados: {tk_scopes or []}"),
        ("token vigente",
         tk_token_ok,
         tk_token_detail),
    ]
    tk_status = _status(tk_checks, "tiktok")
    result["tiktok"] = {
        "label": ident["tiktok"]["label"],
        "status": tk_status,
        "checks": tk_checks,
        "notes": ident["tiktok"].get("notes", ""),
        "publish_mode": ("API Direct Post" if tk_status == "green"
                         else "DRAFT-FIRST (paquete manual) hasta aprobar la app"),
    }

    # Instagram: cuenta Profesional + Page vinculada (solo por API).
    ig_checks = [
        ("cuenta creada",
         _probe(probes, "instagram_reels", "account_created"),
         ident["instagram_reels"]["handle"] +
         " (sin verificar: hace falta una llamada a la API)"),
        ("cuenta Profesional (Creator/Business)",
         _probe(probes, "instagram_reels", "professional"),
         "sin verificar: hace falta una llamada a la API"),
        ("Página de Facebook vinculada",
         _probe(probes, "instagram_reels", "facebook_page_linked"),
         "sin verificar: hace falta una llamada a la API"),
        ("token vigente",
         *_token_ok("instagram_reels", base)),
    ]
    ig_status = _status(ig_checks, "instagram_reels")
    result["instagram_reels"] = {
        "label": ident["instagram_reels"]["label"],
        "status": ig_status,
        "checks": ig_checks,
        "notes": ident["instagram_reels"].get("notes", ""),
        "publish_mode": ("Graph API (contenedor → publicar)"
                         if ig_status == "green"
                         else "BLOQUEADO: sin cuenta verificada no hay publicación por API"),
    }

    # Facebook: Page + rol + token (rol solo por API).
    fb_token_ok, fb_token_detail = _token_ok("facebook_reels", base)
    fb_checks = [
        ("Página creada",
         _probe(probes, "facebook_reels", "page_created"),
         ident["facebook_reels"].get("page_name", "") +
         " (sin verificar: hace falta una llamada a la API)"),
        ("rol admin/editor en la Page",
         _probe(probes, "facebook_reels", "role"),
         "sin verificar: hace falta una llamada a la API"),
        ("Page access token",
         fb_token_ok,
         fb_token_detail),
    ]
    fb_status = _status(fb_checks, "facebook_reels")
    result["facebook_reels"] = {
        "label": ident["facebook_reels"]["label"],
        "status": fb_status,
        "checks": fb_checks,
        "notes": ident["facebook_reels"].get("notes", ""),
        "publish_mode": ("Graph API a Página" if fb_status == "green"
                         else "BLOQUEADO: sin Page verificada no hay publicación por API"),
    }

    # Twitch (watcher): client credentials por .env; el token de app
    # se pide solo y dura ~60 días (no tiene refresh_token).
    tw_id = bool(_creds.get_secret("TWITCH_CLIENT_ID"))
    tw_secret = bool(_creds.get_secret("TWITCH_CLIENT_SECRET"))
    tw_token_ok, tw_token_detail = _token_ok("twitch", base)
    tw_checks = [
        ("TWITCH_CLIENT_ID y TWITCH_CLIENT_SECRET en .env",
         bool(tw_id and tw_secret),
         "sin credenciales el watcher usa GQL como respaldo (no oficial)"),
        ("token de app vigente",
         tw_token_ok if (tw_id and tw_secret) else None,
         tw_token_detail),
    ]
    tw_status = _status(tw_checks, "twitch")
    result["twitch"] = {
        "label": "Twitch — watcher (Helix)",
        "status": tw_status,
        "checks": tw_checks,
        "notes": "Client credentials para el watcher; sin credenciales, GQL como respaldo.",
        "publish_mode": "n/a (no publica; solo vigila)",
    }

    # Kick: no hay nada que conectar para descargar VODs y chat de
    # VODs; el chat en vivo es un websocket no oficial.
    result["kick"] = {
        "label": "Kick — sin API oficial de chat",
        "status": "green",
        "checks": [
            ("descarga de VODs y chat de VODs sin credenciales", True,
             "funciona sin token"),
            ("chat en vivo por websocket Pusher (no oficial)", None,
             "puede romperse sin aviso; no hay credencial que configurar"),
        ],
        "notes": "La API pública (api.kick.com/public/v1) no cubre el chat.",
        "publish_mode": "n/a (no publica; solo origen de clips)",
    }
    return result
