"""Proveedor de URL pública del video (fan-out).

Instagram (y TikTok con PULL_FROM_URL) exigen que la plataforma
descargue el video desde una URL HTTPS pública: en el flujo
estándar no se puede subir el archivo local por API. Este módulo
NO sube nada a ningún servicio: solo construye la URL a partir de
una base que el usuario configura y de la ruta relativa del clip
dentro del repo. El usuario debe servir el directorio de clips en
esa base (cualquier hosting estático con HTTPS); sin base
configurada, Instagram queda en draft con el motivo explícito.
"""
import os


def get_public_url(clip_path, cfg=None, base_dir=None):
    """(url, None) o (None, motivo).

    La base sale de config.yaml `public_url.base_url` o de la
    variable CLIP_PUBLIC_BASE_URL (manda el entorno). La URL es
    base + ruta relativa del clip dentro del repo.
    """
    base = ""
    if cfg:
        base = ((cfg.get("public_url") or {}).get("base_url") or "")
        base = str(base).strip()
    if not base:
        base = (os.environ.get("CLIP_PUBLIC_BASE_URL") or "").strip()
    if not base:
        return None, ("sin URL pública configurada: configura "
                      "public_url.base_url en config.yaml o "
                      "CLIP_PUBLIC_BASE_URL en .env (el hosting "
                      "estático que sirva los clips)")
    if not base.startswith("https://"):
        return None, ("public_url.base_url debe empezar por https:// "
                      f"(valor actual: {base!r})")
    root = base_dir or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    try:
        rel = os.path.relpath(os.path.abspath(clip_path), root)
    except ValueError:
        return None, f"el clip está fuera del repo: {clip_path}"
    if rel.startswith(".."):
        return None, f"el clip está fuera del repo: {clip_path}"
    return base.rstrip("/") + "/" + rel.replace(os.sep, "/"), None
