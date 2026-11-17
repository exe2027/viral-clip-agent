"""Adaptadores de publicación por plataforma (Fase 3.4).

Cada adaptador lleva en su docstring el enlace a la documentación
oficial vigente (verificado 2026-11-17) y traduce los errores de la
API a un resultado común: {"ok", "mode": "api"|"draft",
"reason", ...}. Una plataforma sin credenciales o con error NUNCA
rompe el resto: cae a "draft" (R5).

HTTP con urllib (stdlib): sin dependencias nuevas. Cada llamada
acepta ``request_fn`` para inyectar un transporte falso en tests.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import credentials as _creds

GRAPH_VERSION = "v26.0"


def _http(method, url, headers=None, body=None, timeout=30,
          request_fn=None):
    """(status, dict). request_fn(method, url, headers, body) es el
    transporte inyectable de los tests; por defecto urllib."""
    if request_fn is not None:
        return request_fn(method, url, headers or {}, body)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8") if isinstance(
            body, (dict, list)) else body
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data is not None and "Content-Type" not in (headers or {}):
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(raw) if raw else {"error": raw}
        except json.JSONDecodeError:
            return e.code, {"error": raw}
    except Exception as e:  # red caída, timeout...
        return 0, {"error": str(e)}


def _draft(reason, **extra):
    """Resultado común: no se publica por API, queda en borrador."""
    out = {"ok": False, "mode": "draft", "reason": reason}
    out.update(extra)
    return out


def _error_result(status, payload, retryable_statuses=(429, 500, 502, 503, 504)):
    """Clasifica un error de API: transitorio (reintentar) o final."""
    err = payload.get("error") or {}
    code = err.get("code") if isinstance(err, dict) else None
    message = (err.get("message") if isinstance(err, dict)
               else str(payload.get("error") or payload))
    return {"ok": False, "mode": "error", "status": status,
            "error_code": code, "reason": message or f"HTTP {status}",
            "retryable": status in retryable_statuses}


class YouTubeAdapter:
    """YouTube Shorts por Data API v3.

    Docs oficiales:
    - Subida: https://developers.google.com/youtube/v3/docs/videos/insert
    - Cuota: https://developers.google.com/youtube/data/revision_history
      (videos.insert: 1 unidad en su propio bucket, 100 llamadas/día
      por defecto; thumbnails.set: 50 unidades del pool compartido)
    - Miniaturas: https://developers.google.com/youtube/v3/docs/thumbnails/set
      (llamada separada; exige canal verificado, si no 403)
    - OAuth: scope https://www.googleapis.com/auth/youtube.upload,
      refresh token en tokens/youtube_shorts.json.
    La subida en sí la hace publisher.YouTubePublisher (que ya
    existe); este adaptador añade la miniatura tras subir.
    """

    def __init__(self, base_dir=None):
        self.base_dir = base_dir

    def set_thumbnail(self, video_id, image_path):
        """thumbnails.set tras la subida. Devuelve {"ok": True} o el
        motivo por el que no se pudo (canal sin verificar -> 403,
        sin librería -> draft). No rompe la publicación: el video
        ya está subido."""
        if not os.path.isfile(image_path):
            return {"ok": False, "reason": f"miniatura no existe: {image_path}"}
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
        except ImportError:
            return _draft("falta google-api-python-client")
        from publisher import YouTubePublisher
        _creds.migrate_legacy_youtube_token(self.base_dir)
        token_file = _creds.token_path("youtube_shorts", self.base_dir)
        if not os.path.isfile(token_file):
            return _draft("sin token de YouTube en tokens/")
        creds = Credentials.from_authorized_user_file(
            token_file, [YouTubePublisher.SCOPE])
        youtube = build("youtube", "v3", credentials=creds)
        try:
            media = MediaFileUpload(image_path)
            youtube.thumbnails().set(videoId=video_id,
                                     media_body=media).execute()
        except Exception as e:
            return {"ok": False, "reason": f"thumbnails.set falló: {e}",
                    "hint": "el canal debe estar verificado para "
                            "miniaturas personalizadas"}
        return {"ok": True, "video_id": video_id}


class TikTokAdapter:
    """TikTok por Content Posting API.

    Docs oficiales (verificado 2026-11-17, leído vía guías que
    citan las páginas de developers.tiktok.com):
    - https://developers.tiktok.com/doc/content-posting-api-get-started
    - Direct Post: POST /v2/post/publish/video/init/, scope
      video.publish; Upload (borrador en inbox): scope video.upload.
    - Sin auditoría de la app, todo lo publicado es PRIVADO
      (SELF_ONLY) y solo a cuentas privadas (error
      unaudited_client_can_only_post_to_private_accounts): se
      detecta y se cae a borrador, no a error.
    - Límite: 6 peticiones/min por token; ~15 publicaciones/día
      por cuenta (Direct Post); Upload: máx. 5 borradores pendientes.
    """

    API = "https://open.tiktokapis.com"

    def __init__(self, base_dir=None, request_fn=None):
        self.base_dir = base_dir
        self.request_fn = request_fn

    def _token(self):
        token = _creds.load_token("tiktok", self.base_dir)
        return (token or {}).get("access_token")

    def refresh_token(self):
        """Renueva el access token con el refresh_token guardado.
        Devuelve True si quedó guardado el nuevo token."""
        token = _creds.load_token("tiktok", self.base_dir) or {}
        refresh = token.get("refresh_token")
        client_key = _creds.get_secret("TIKTOK_CLIENT_KEY")
        client_secret = _creds.get_secret("TIKTOK_CLIENT_SECRET")
        if not refresh or not client_key or not client_secret:
            return False
        body = urllib.parse.urlencode({
            "client_key": client_key, "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh}).encode()
        status, payload = _http(
            "POST", f"{self.API}/v2/oauth/token/",
            {"Content-Type": "application/x-www-form-urlencoded"},
            body, request_fn=self.request_fn)
        if status != 200 or "access_token" not in payload:
            return False
        payload["expires_at"] = int(time.time()) + int(
            payload.get("expires_in", 86400))
        _creds.save_token("tiktok", payload, self.base_dir)
        return True

    def init_direct_post(self, video_url, title, privacy_level="SELF_ONLY"):
        """Inicia un Direct Post. Sin token -> draft. Sin auditoría
        (error 403 unaudited_...) -> draft con el motivo."""
        access = self._token()
        if not access:
            return _draft("sin token de TikTok en tokens/")
        payload = {
            "post_info": {"title": title,
                          "privacy_level": privacy_level,
                          "disable_duet": False, "disable_comment": False,
                          "disable_stitch": False,
                          "video_cover_timestamp_ms": 1000},
            "source_info": {"source": "PULL_FROM_URL",
                            "video_url": video_url},
        }
        status, resp = _http(
            "POST", f"{self.API}/v2/post/publish/video/init/",
            {"Authorization": f"Bearer {access}"}, payload,
            request_fn=self.request_fn)
        data = resp.get("data") or {}
        error = resp.get("error") or {}
        if status == 200 and data.get("publish_id"):
            return {"ok": True, "mode": "api",
                    "publish_id": data["publish_id"]}
        code = error.get("code")
        if code == "unaudited_client_can_only_post_to_private_accounts":
            return _draft("app sin auditar: TikTok solo permite publicar "
                          "en privado (SELF_ONLY) hasta pasar la auditoría",
                          error_code=code)
        if status in (401, 403) and code in (
                "access_token_invalid", "scope_not_authorized"):
            return _draft(f"token sin permisos ({code}): reautorizar "
                          "con el scope video.publish", error_code=code)
        return _error_result(status, resp)


class InstagramAdapter:
    """Instagram Reels por Graph API (Instagram API con Facebook Login).

    Docs oficiales:
    - https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/
    - Flujo: crear contenedor POST /{ig-user-id}/media
      (media_type=REELS, video_url pública) -> consultar estado
      GET /{container-id}?fields=status_code (EXPIRED/ERROR/
      FINISHED/IN_PROGRESS/PUBLISHED; Meta recomienda 1 vez/min,
      máx. ~5 min) -> publicar POST /{ig-user-id}/media_publish.
    - Solo cuentas Profesionales (Business/Creator); con Facebook
      Login la cuenta debe estar vinculada a una Página de Facebook.
    - Límite: 50 publicaciones por API en 24 h (consultar
      /{ig-user-id}/content_publishing_limit). El video lo descarga
      Meta desde una URL HTTPS pública: sin URL pública no hay
      subida por API (queda en borrador).
    """

    def __init__(self, base_dir=None, request_fn=None):
        self.base_dir = base_dir
        self.request_fn = request_fn
        self.host = f"https://graph.facebook.com/{GRAPH_VERSION}"

    def _token(self):
        token = _creds.load_token("instagram_reels", self.base_dir)
        return (token or {}).get("access_token")

    def create_container(self, ig_user_id, video_url, caption,
                         cover_url=None):
        """Crea el contenedor del Reel. Sin token o sin URL pública
        -> draft."""
        access = self._token()
        if not access:
            return _draft("sin token de Instagram en tokens/")
        if not (video_url or "").startswith("https://"):
            return _draft("Instagram exige una URL HTTPS pública del "
                          "video (Meta lo descarga); el archivo local "
                          "no se puede subir por API")
        params = {"media_type": "REELS", "video_url": video_url,
                  "caption": caption, "access_token": access}
        if cover_url:
            params["cover_url"] = cover_url
        url = (f"{self.host}/{ig_user_id}/media?"
               + urllib.parse.urlencode(params))
        status, resp = _http("POST", url, request_fn=self.request_fn)
        if status == 200 and resp.get("id"):
            return {"ok": True, "mode": "api",
                    "container_id": resp["id"]}
        return _error_result(status, resp)

    def container_status(self, container_id):
        """status_code del contenedor: FINISHED listo para publicar."""
        access = self._token()
        if not access:
            return _draft("sin token de Instagram en tokens/")
        url = (f"{self.host}/{container_id}?fields=status_code"
               f"&access_token={access}")
        status, resp = _http("GET", url, request_fn=self.request_fn)
        if status == 200:
            return {"ok": True, "status_code": resp.get("status_code")}
        return _error_result(status, resp)

    def publish(self, ig_user_id, container_id):
        """Publica un contenedor FINISHED. Devuelve el id del Reel."""
        access = self._token()
        if not access:
            return _draft("sin token de Instagram en tokens/")
        params = {"creation_id": container_id, "access_token": access}
        url = (f"{self.host}/{ig_user_id}/media_publish?"
               + urllib.parse.urlencode(params))
        status, resp = _http("POST", url, request_fn=self.request_fn)
        if status == 200 and resp.get("id"):
            return {"ok": True, "mode": "api", "media_id": resp["id"]}
        return _error_result(status, resp)


class FacebookAdapter:
    """Facebook Reels por Graph API (Reels Publishing API).

    Docs oficiales:
    - https://developers.facebook.com/docs/video-api/
    - Solo a Página (nunca perfil personal): Page access token con
      pages_manage_posts, pages_read_engagement, pages_show_list.
    - Flujo en 3 fases: POST /{page-id}/video_reels
      (upload_phase=start) -> subir el archivo a
      rupload.facebook.com/video-upload/{video-id} -> POST
      /{page-id}/video_reels (upload_phase=finish,
      video_state=PUBLISHED, description).
    - Requisitos del video: MP4, 9:16, 1080x1920 recomendado
      (mín. 540x960), 3 a 90 segundos, 24 a 60 fps. Límite: 30
      Reels por API por Página en 24 h.
    """

    def __init__(self, base_dir=None, request_fn=None):
        self.base_dir = base_dir
        self.request_fn = request_fn
        self.host = f"https://graph.facebook.com/{GRAPH_VERSION}"
        self.upload_host = "https://rupload.facebook.com"

    def _token(self):
        token = _creds.load_token("facebook_reels", self.base_dir)
        return (token or {}).get("access_token")

    def start_upload(self, page_id):
        """Fase 1: abre la sesión y devuelve el video_id."""
        access = self._token()
        if not access:
            return _draft("sin Page access token en tokens/")
        url = (f"{self.host}/{page_id}/video_reels?"
               + urllib.parse.urlencode(
                   {"upload_phase": "start", "access_token": access}))
        status, resp = _http("POST", url, request_fn=self.request_fn)
        if status == 200 and resp.get("video_id"):
            return {"ok": True, "mode": "api",
                    "video_id": resp["video_id"]}
        return _error_result(status, resp)

    def finish_upload(self, page_id, video_id, description):
        """Fase 3: publica el Reel ya subido."""
        access = self._token()
        if not access:
            return _draft("sin Page access token en tokens/")
        params = {"upload_phase": "finish", "video_id": video_id,
                  "video_state": "PUBLISHED", "description": description,
                  "access_token": access}
        url = (f"{self.host}/{page_id}/video_reels?"
               + urllib.parse.urlencode(params))
        status, resp = _http("POST", url, request_fn=self.request_fn)
        if status == 200 and resp.get("id"):
            return {"ok": True, "mode": "api", "post_id": resp["id"]}
        return _error_result(status, resp)


class TwitchAdapter:
    """Twitch Helix para el watcher (client credentials).

    Docs oficiales:
    - https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#client-credentials-grant-flow
    - POST https://id.twitch.tv/oauth2/token con
      client_id + client_secret + grant_type=client_credentials.
      El App Access Token dura ~60 días y NO tiene refresh_token:
      cuando vence se pide otro igual. Cada llamada Helix lleva
      Authorization: Bearer + Client-Id.
    - Sin credenciales, el watcher usa GQL como respaldo (no
      oficial, puede romperse) y lo avisa.
    """

    TOKEN_URL = "https://id.twitch.tv/oauth2/token"
    VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"

    def __init__(self, base_dir=None, request_fn=None):
        self.base_dir = base_dir
        self.request_fn = request_fn

    def app_token(self):
        """Token de app vigente (del almacén o recién pedido)."""
        token = _creds.load_token("twitch", self.base_dir)
        if token and not _creds.token_expired(token):
            return token.get("access_token")
        client_id = _creds.get_secret("TWITCH_CLIENT_ID")
        client_secret = _creds.get_secret("TWITCH_CLIENT_SECRET")
        if not client_id or not client_secret:
            return None
        body = urllib.parse.urlencode({
            "client_id": client_id, "client_secret": client_secret,
            "grant_type": "client_credentials"}).encode()
        status, payload = _http(
            "POST", self.TOKEN_URL,
            {"Content-Type": "application/x-www-form-urlencoded"},
            body, request_fn=self.request_fn)
        if status != 200 or "access_token" not in payload:
            return None
        payload["expires_at"] = int(time.time()) + int(
            payload.get("expires_in", 3600))
        _creds.save_token("twitch", payload, self.base_dir)
        return payload["access_token"]

    def validate(self):
        """GET oauth2/validate: (ok, client_id, expires_in)."""
        token = self.app_token()
        if not token:
            return {"ok": False, "reason": "sin credenciales de Twitch"}
        status, payload = _http(
            "GET", self.VALIDATE_URL,
            {"Authorization": f"OAuth {token}"},
            request_fn=self.request_fn)
        if status == 200:
            return {"ok": True, "client_id": payload.get("client_id"),
                    "expires_in": payload.get("expires_in")}
        return {"ok": False, "reason": f"HTTP {status}",
                "detail": payload}


def kick_notes():
    """Kick: qué se usa y sus riesgos (Fase 3.4f).

    - API pública oficial: https://docs.kick.com/ — base
      https://api.kick.com/public/v1 (OAuth 2.1 con PKCE). Cubre
      usuarios, canales, directos, eventos; NO cubre el historial
      de chat.
    - Chat en vivo: websocket Pusher que usa la propia web de Kick
      (wss://ws-<cluster>.pusher.com, canal chatrooms.<id>.v2).
      NO es parte de la API oficial: es ingeniería inversa tolerada
      por el ecosistema, puede cambiar sin aviso y no tiene soporte.
      El chat de un directo terminado desaparece: no hay replay.
    - Chat de VODs: el proyecto descarga el chat con
      kick_downloader (misma vía no oficial) y lo guarda local.
    """
    return {
        "official_api": "https://api.kick.com/public/v1",
        "docs": "https://docs.kick.com/",
        "chat": "websocket Pusher no oficial (chatrooms.<id>.v2)",
        "risks": "sin API oficial de chat: el websocket puede cambiar "
                 "sin aviso; el chat pasado no se puede recuperar",
    }
