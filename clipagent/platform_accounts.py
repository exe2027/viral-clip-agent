"""IDs de cuenta por plataforma (fan-out).

Instagram necesita el ig_user_id y Facebook el page_id para
publicar por API. No son secretos, pero no viven en accounts.yaml
(que solo guarda handles y nombres): se configuran en config.yaml
`platform_accounts` o por variables de entorno (IG_USER_ID,
FACEBOOK_PAGE_ID; manda el entorno). Vacíos = draft con el motivo
explícito, nunca un valor inventado.
"""
import os


def get_ig_user_id(cfg=None):
    """ig_user_id de Instagram o "" si no está configurado."""
    value = ""
    if cfg:
        entry = (cfg.get("platform_accounts") or {}).get(
            "instagram_reels") or {}
        value = str(entry.get("ig_user_id") or "").strip()
    if not value:
        value = (os.environ.get("IG_USER_ID") or "").strip()
    return value


def get_facebook_page_id(cfg=None):
    """page_id de la Página de Facebook o "" si no está configurado."""
    value = ""
    if cfg:
        entry = (cfg.get("platform_accounts") or {}).get(
            "facebook_reels") or {}
        value = str(entry.get("page_id") or "").strip()
    if not value:
        value = (os.environ.get("FACEBOOK_PAGE_ID") or "").strip()
    return value
