"""Almacén único de credenciales (Fase 3.1).

REGLA: este módulo es la ÚNICA puerta de acceso a secretos. Ningún
otro módulo lee archivos de credenciales directamente: ni .env, ni
tokens/, ni client_secrets.json, ni token.json.

- Secretos estáticos (client id/secret, API keys): variables de
  entorno o archivo .env (gitignored, nunca versionado).
- Tokens OAuth: carpeta tokens/ (gitignored) con permisos 0700 y
  cada archivo 0600. Un archivo por plataforma:
  tokens/youtube_shorts.json, tokens/tiktok.json,
  tokens/instagram_reels.json, tokens/facebook_reels.json,
  tokens/twitch.json.
- Legado: client_secrets.json y token.json en la raíz (YouTube,
  flujo de escritorio) se siguen leyendo, pero `setup` los migra
  a tokens/ y este módulo es quien los toca.
"""
import json
import os
import stat

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKENS_DIRNAME = "tokens"

# Plataformas con token OAuth guardado en tokens/.
PLATFORMS_WITH_TOKEN = ("youtube_shorts", "tiktok", "instagram_reels",
                        "facebook_reels", "twitch")

# Secretos estáticos que solo viven en entorno/.env (nunca en YAML).
STATIC_SECRETS = ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET",
                  "GEMINI_API_KEY", "GOOGLE_API_KEY",
                  "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET",
                  "META_APP_ID", "META_APP_SECRET",
                  "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


def tokens_dir(base_dir=None):
    """Carpeta tokens/ (la crea con 0700 si no existe)."""
    path = os.path.join(base_dir or BASE_DIR, TOKENS_DIRNAME)
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def token_path(platform, base_dir=None):
    """Ruta de tokens/<plataforma>.json."""
    return os.path.join(tokens_dir(base_dir), f"{platform}.json")


def load_token(platform, base_dir=None):
    """Token guardado de una plataforma, o None si no existe.

    Devuelve el dict tal cual se guardó (p. ej. {"access_token",
    "refresh_token", "expires_at", ...}). Nunca levanta por un
    archivo corrupto: devuelve None y deja constancia en el log.
    """
    path = token_path(platform, base_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_token(platform, data, base_dir=None):
    """Guarda un token (escritura atómica, archivo 0600).

    Devuelve la ruta escrita.
    """
    path = token_path(platform, base_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def delete_token(platform, base_dir=None):
    """Borra el token de una plataforma. True si existía."""
    path = token_path(platform, base_dir)
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


def token_expired(token, now=None):
    """True si el token tiene expires_at pasado (o no lo trae y no
    hay refresh_token con el que renovarlo)."""
    import time
    if not token:
        return True
    expires_at = token.get("expires_at")
    if expires_at is None:
        return not token.get("refresh_token")
    return float(expires_at) <= (now if now is not None else time.time())


def get_secret(name, default=None):
    """Un secreto estático, del entorno (o .env ya cargado)."""
    load_dotenv()
    return os.environ.get(name, default)


def load_dotenv(path=None):
    """Carga un .env sin pisar el entorno real.

    Solo rellena variables que aún no existen: un export real
    siempre manda sobre el .env. Devuelve la ruta del primer .env
    cargado, o None. Misma semántica que el load_dotenv() que
    vivía en config.py (al que reemplaza).
    """
    candidates = []
    if path:
        candidates.append(path)
    candidates.append(os.path.join(os.getcwd(), ".env"))
    candidates.append(os.path.join(BASE_DIR, ".env"))
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        return candidate
    return None


def client_secrets_path(base_dir=None):
    """client_secrets.json de YouTube (flujo de escritorio, legado)."""
    return os.path.join(base_dir or BASE_DIR, "client_secrets.json")


def legacy_token_path(base_dir=None):
    """token.json de YouTube en la raíz (legado, antes de tokens/)."""
    return os.path.join(base_dir or BASE_DIR, "token.json")


def migrate_legacy_youtube_token(base_dir=None):
    """Mueve token.json (raíz) a tokens/youtube_shorts.json.

    Devuelve la ruta nueva, o None si no había nada que migrar.
    El archivo viejo se borra solo después de guardar el nuevo.
    """
    base = base_dir or BASE_DIR
    legacy = legacy_token_path(base)
    if not os.path.isfile(legacy):
        return None
    if load_token("youtube_shorts", base) is not None:
        return token_path("youtube_shorts", base)
    try:
        with open(legacy, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    new_path = save_token("youtube_shorts", data, base)
    try:
        os.remove(legacy)
    except OSError:
        pass
    return new_path


def token_file_mode_ok(path):
    """True si el archivo existe y sus permisos son 0600 o más
    restrictivos (sin permiso de grupo/otros)."""
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return False
    return mode & 0o077 == 0
