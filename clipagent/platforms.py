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
import uuid

from . import credentials as _creds

GRAPH_VERSION = "v27.0"


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
        from .publish import YouTubePublisher
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

    # --- Direct Post completo (fan-out): FILE_UPLOAD + polling ---
    # Docs oficiales (verificado 2026-11-17):
    # - Creator info (obligatorio antes de cada post):
    #   https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post
    # - Media transfer (chunks 5-64 MB, 1-1000 chunks):
    #   https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide
    # - Estado: POST /v2/post/publish/status/fetch/ (PROCESSING_*
    #   -> PUBLISH_COMPLETE / FAILED; el post_id público solo llega
    #   tras la moderación).
    # Con FILE_UPLOAD no hace falta URL pública del video: se suben
    # los bytes. La publicación es automática al completar el
    # procesado; no hay llamada "publish" separada.
    CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB por chunk (mínimo del API)
    POLL_INTERVAL_S = 10
    MAX_POLLS = 12  # ~2 min; si sigue procesando, se retoma luego

    def creator_info(self):
        """Opciones de privacidad y duración máxima del creador.

        La doc lo exige antes de cada post: el privacy_level del
        init debe ser una de las opciones devueltas. Sin token o
        sin permisos -> draft; error de red/5xx -> error
        reintentable."""
        access = self._token()
        if not access:
            return _draft("sin token de TikTok en tokens/")
        status, resp = _http(
            "POST", f"{self.API}/v2/post/publish/creator_info/query/",
            {"Authorization": f"Bearer {access}"}, {},
            request_fn=self.request_fn)
        data = resp.get("data") or {}
        error = resp.get("error") or {}
        if status == 200 and data:
            return {"ok": True, "mode": "api", **data}
        code = error.get("code")
        if status in (401, 403) and code in (
                "access_token_invalid", "scope_not_authorized"):
            return _draft(f"token sin permisos ({code}): reautorizar "
                          "con el scope video.publish", error_code=code)
        return _error_result(status, resp)

    def publish_status(self, publish_id):
        """Estado de una publicación: PROCESSING_*, PUBLISH_COMPLETE
        o FAILED (con fail_reason)."""
        access = self._token()
        if not access:
            return _draft("sin token de TikTok en tokens/")
        status, resp = _http(
            "POST", f"{self.API}/v2/post/publish/status/fetch/",
            {"Authorization": f"Bearer {access}"},
            {"publish_id": publish_id}, request_fn=self.request_fn)
        data = resp.get("data") or {}
        if status == 200 and data.get("status"):
            out = {"ok": True, "mode": "api",
                   "status": data["status"], "publish_id": publish_id}
            if data.get("post_id"):
                out["post_id"] = data["post_id"]
            if data.get("fail_reason"):
                out["fail_reason"] = data["fail_reason"]
            return out
        return _error_result(status, resp)

    def _init_file_upload(self, title, video_size, privacy_level):
        """Init con FILE_UPLOAD: devuelve publish_id y upload_url."""
        access = self._token()
        chunk_size = min(self.CHUNK_SIZE, video_size)
        total = (video_size + chunk_size - 1) // chunk_size
        payload = {
            "post_info": {"title": title,
                          "privacy_level": privacy_level,
                          "disable_duet": False, "disable_comment": False,
                          "disable_stitch": False,
                          "video_cover_timestamp_ms": 1000},
            "source_info": {"source": "FILE_UPLOAD",
                            "video_size": video_size,
                            "chunk_size": chunk_size,
                            "total_chunk_count": total},
        }
        status, resp = _http(
            "POST", f"{self.API}/v2/post/publish/video/init/",
            {"Authorization": f"Bearer {access}"}, payload,
            request_fn=self.request_fn)
        data = resp.get("data") or {}
        error = resp.get("error") or {}
        if status == 200 and data.get("publish_id"):
            return {"ok": True, "mode": "api",
                    "publish_id": data["publish_id"],
                    "upload_url": data.get("upload_url"),
                    "chunk_size": chunk_size}
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

    def _upload_chunks(self, upload_url, clip_path, video_size,
                       chunk_size):
        """PUT secuenciales con Content-Range al upload_url del init.
        206 por chunk, 201 al completar."""
        offset = 0
        with open(clip_path, "rb") as f:
            while offset < video_size:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                end = offset + len(chunk) - 1
                headers = {"Content-Type": "video/mp4",
                           "Content-Length": str(len(chunk)),
                           "Content-Range":
                               f"bytes {offset}-{end}/{video_size}"}
                status, resp = _http(
                    "PUT", upload_url, headers, chunk,
                    request_fn=self.request_fn)
                if status not in (200, 201, 206):
                    return _error_result(status, resp)
                offset += len(chunk)
        if offset < video_size:
            return {"ok": False, "mode": "error",
                    "reason": "subida incompleta a TikTok "
                              f"({offset}/{video_size} bytes)",
                    "retryable": True}
        return {"ok": True}

    def publish_video(self, clip_path, title, privacy_level=None,
                      resume_publish_id=None, sleep_fn=None,
                      max_polls=None):
        """Direct Post completo desde el disco (FILE_UPLOAD).

        creator_info -> init -> PUT por chunks -> polling de estado
        hasta PUBLISH_COMPLETE. Si sigue procesando tras max_polls,
        devuelve mode "processing" con el publish_id para retomar
        en el siguiente ciclo sin volver a subir (resume). Un
        FAILED de TikTok es error final (no reintentable); un 5xx
        es reintentable. `sleep_fn` se inyecta en tests.
        """
        sleep = sleep_fn or time.sleep
        max_polls = self.MAX_POLLS if max_polls is None else max_polls
        if not os.path.isfile(clip_path):
            return {"ok": False, "reason":
                    f"archivo no existe: {clip_path}"}
        if not self._token():
            return _draft("sin token de TikTok en tokens/")
        size = os.path.getsize(clip_path)
        publish_id = resume_publish_id
        if publish_id is None:
            info = self.creator_info()
            if not info.get("ok"):
                return info
            options = info.get("privacy_level_options") or []
            level = privacy_level
            if level is None:
                if "PUBLIC_TO_EVERYONE" in options:
                    level = "PUBLIC_TO_EVERYONE"
                elif options:
                    level = options[0]
                else:
                    level = "SELF_ONLY"
            if options and level not in options:
                return _draft(
                    f"privacidad {level} no permitida por TikTok "
                    f"para esta cuenta (opciones: {', '.join(options)})")
            init = self._init_file_upload(title, size, level)
            if not init.get("ok"):
                return init
            publish_id = init["publish_id"]
            if not init.get("upload_url"):
                return {"ok": False, "mode": "error",
                        "reason": "TikTok no devolvió upload_url "
                                  "en el init", "retryable": True}
            up = self._upload_chunks(init["upload_url"], clip_path,
                                     size, init["chunk_size"])
            if not up.get("ok"):
                return up
        for _ in range(max_polls):
            st = self.publish_status(publish_id)
            if not st.get("ok"):
                return st
            status = st.get("status")
            if status == "PUBLISH_COMPLETE":
                out = {"ok": True, "mode": "api",
                       "publish_id": publish_id}
                if st.get("post_id"):
                    out["post_id"] = st["post_id"]
                return out
            if status == "FAILED":
                return {"ok": False, "mode": "error",
                        "status": "FAILED",
                        "reason": "TikTok rechazó la publicación: "
                                  f"{st.get('fail_reason') or 'sin motivo'}",
                        "retryable": False}
            sleep(self.POLL_INTERVAL_S)
        return {"ok": False, "mode": "processing",
                "publish_id": publish_id, "retryable": True,
                "reason": "TikTok sigue procesando el video; "
                          "se retoma en el siguiente ciclo"}


class ZernioAdapter:
    """Publicación vía Zernio API (agregador; TikTok por su vía).

    Docs oficiales (verificado 2026-11-18, https://zernio.com/llms.txt,
    https://docs.zernio.com/platforms/tiktok y
    https://docs.zernio.com/posts/create-post):
    - Base: https://zernio.com/api/v1, Bearer ZERNIO_API_KEY.
    - Media: POST /v1/media/presign {filename, contentType} ->
      {uploadUrl, publicUrl}; PUT de los bytes a uploadUrl.
    - Post: POST /v1/posts {content, platforms: [{platform:
      "tiktok", accountId}], mediaItems: [{type: "video", url}],
      tiktokSettings: {...}, isDraft, publishNow}.
    - tiktokSettings exige content_preview_confirmed y
      express_consent_given en true; en cuentas conectadas por la
      app TikTok for Business (todas las conexiones nuevas) el
      video directo es SOLO PUBLIC_TO_EVERYONE: cualquier otro
      nivel se rechaza, y la única ruta no-pública es el borrador
      al Creator Inbox (tiktokSettings.draft: true).
    - dryRun: true (campo del body, solo TikTok, verificado
      2026-11-18 en la doc de Create post) pre-chequea si el post
      podría publicarse bajo los límites diarios sin crear nada
      ni reclamar slot. En cuentas Business siempre devuelve
      canPublish: true (confirma que el body está bien formado).
      (Corrección: el dry_run LOCAL de este adaptador existía
      porque antes se creía que POST /v1/posts no tenía dryRun;
      la doc actual sí lo tiene y se usa en can_publish.)
    - publishNow: true publica en la misma petición y devuelve
      platformPostUrl. Idempotency-Key (UUID por post lógico)
      hace seguros los reintentos.

    Publicación DIRECTA en público (2026-11-18, orden del
    usuario: 100% automático): publish_public() sube el video,
    pre-chequea con dryRun y publica con draft=False
    (publishNow True, isDraft False). Solo cae a borrador si
    faltan clave/cuenta/URL, si el dryRun dice que no se puede
    publicar, o si la API falla. La auditoría propia de TikTok
    NO hace falta para esta vía: la app auditada es la de
    Zernio (TikTok for Business). Sin clave o sin cuenta ->
    draft con el motivo (R5: nunca rompe el resto).
    La clave se lee de ZERNIO_API_KEY y la cuenta de
    ZERNIO_TIKTOK_ACCOUNT_ID (entorno o .env, vía credentials).
    """

    API = "https://zernio.com/api/v1"

    def __init__(self, base_dir=None, request_fn=None, api_key=None,
                 account_id=None):
        self.base_dir = base_dir
        self.request_fn = request_fn
        self._api_key = api_key
        self._account_id = account_id

    def _key(self):
        if self._api_key:
            return self._api_key
        return _creds.get_secret("ZERNIO_API_KEY")

    def _account(self):
        if self._account_id:
            return self._account_id
        return _creds.get_secret("ZERNIO_TIKTOK_ACCOUNT_ID")

    def _auth(self):
        return {"Authorization": f"Bearer {self._key()}"}

    def _post_payload(self, video_url, title, account_id, draft):
        """Cuerpo de POST /v1/posts para un video de TikTok."""
        settings = {
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "allow_comment": True, "allow_duet": True,
            "allow_stitch": True,
            "commercial_content_type": "none",
            "content_preview_confirmed": True,
            "express_consent_given": True,
            "media_type": "video", "auto_add_music": False,
            "video_made_with_ai": False,
        }
        if draft:
            settings["draft"] = True
        return {
            "content": title,
            "platforms": [{"platform": "tiktok",
                           "accountId": account_id}],
            "mediaItems": [{"type": "video", "url": video_url}],
            "tiktokSettings": settings,
            "isDraft": draft,
            "publishNow": not draft,
        }

    def upload_media(self, clip_path):
        """Presign + PUT: devuelve la publicUrl del video, o el
        motivo por el que no se pudo (draft/error, nunca excepción).
        """
        if not self._key():
            return _draft("sin ZERNIO_API_KEY en el entorno "
                          "(secreto ZERNIO_API_KEY)")
        if not os.path.isfile(clip_path):
            return {"ok": False, "reason":
                    f"archivo no existe: {clip_path}"}
        status, resp = _http(
            "POST", f"{self.API}/media/presign", self._auth(),
            {"filename": os.path.basename(clip_path),
             "contentType": "video/mp4"},
            request_fn=self.request_fn)
        upload_url = resp.get("uploadUrl")
        public_url = resp.get("publicUrl")
        if status != 200 or not upload_url or not public_url:
            return _error_result(status, resp)
        with open(clip_path, "rb") as f:
            data = f.read()
        status, resp = _http(
            "PUT", upload_url, {"Content-Type": "video/mp4"}, data,
            request_fn=self.request_fn)
        if status not in (200, 201, 204):
            return _error_result(status, resp)
        return {"ok": True, "mode": "api", "public_url": public_url}

    def _idempotency_key(self, video_url, title, account):
        """UUID determinista por post lógico (cuenta + URL + título):
        reintentar con la misma clave devuelve el post original en
        Zernio en vez de duplicarlo (ventana 24 h)."""
        raw = f"zernio:{account}:{video_url}:{title}"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))

    def can_publish(self, video_url, title, account_id=None):
        """Pre-chequeo REAL con Zernio: POST /v1/posts con
        dryRun: true (solo TikTok). No crea nada, no reclama slot
        y se puede repetir. Devuelve {"ok", "can_publish", ...}.
        En cuentas Business (la nuestra) siempre es true si el
        body está bien formado; en cuentas de developer app
        TikTok, false cuando se alcanzó el tope diario."""
        key = self._key()
        if not key:
            return {"ok": False, "can_publish": False,
                    "reason": "sin ZERNIO_API_KEY en el entorno "
                              "(secreto ZERNIO_API_KEY)"}
        account = account_id or self._account()
        if not account:
            return {"ok": False, "can_publish": False,
                    "reason": "sin cuenta de TikTok en Zernio "
                              "(ZERNIO_TIKTOK_ACCOUNT_ID)"}
        if not (video_url or "").startswith("https://"):
            return {"ok": False, "can_publish": False,
                    "reason": "Zernio exige una URL HTTPS pública "
                              "del video (mediaItems.url)"}
        payload = self._post_payload(video_url, title, account,
                                     draft=False)
        payload["dryRun"] = True
        status, resp = _http(
            "POST", f"{self.API}/posts", self._auth(), payload,
            request_fn=self.request_fn)
        if status == 200:
            can = resp.get("canPublish")
            if can is None:
                tiktok = resp.get("tiktok")
                if isinstance(tiktok, list) and tiktok:
                    can = tiktok[0].get("canPublish")
            if can is None:
                can = True
            return {"ok": True, "can_publish": bool(can),
                    "detail": resp}
        err = _error_result(status, resp)
        err["can_publish"] = False
        return err

    def create_post(self, video_url, title, account_id=None,
                    draft=True, dry_run=False):
        """POST /v1/posts con un video ya subido (URL pública).

        dry_run=True no toca la red: valida clave/cuenta/URL y
        devuelve el payload que se mandaría (mode "dry-run").
        draft=False publica en público en la misma petición
        (publishNow True); la respuesta trae platformPostUrl.
        """
        key = self._key()
        if not key:
            return _draft("sin ZERNIO_API_KEY en el entorno "
                          "(secreto ZERNIO_API_KEY)")
        account = account_id or self._account()
        if not account:
            return _draft("sin cuenta de TikTok en Zernio "
                          "(ZERNIO_TIKTOK_ACCOUNT_ID)")
        if not (video_url or "").startswith("https://"):
            return _draft("Zernio exige una URL HTTPS pública del "
                          "video (mediaItems.url)")
        payload = self._post_payload(video_url, title, account, draft)
        if dry_run:
            return {"ok": True, "mode": "dry-run",
                    "reason": "dry-run local: no se llamó a la API "
                              "de Zernio; para el pre-chequeo real "
                              "contra Zernio usar can_publish() "
                              "(dryRun: true en POST /v1/posts)",
                    "payload": payload}
        headers = dict(self._auth())
        headers["Idempotency-Key"] = self._idempotency_key(
            video_url, title, account)
        status, resp = _http(
            "POST", f"{self.API}/posts", headers, payload,
            request_fn=self.request_fn)
        if status in (200, 201) and resp.get("id"):
            return {"ok": True, "mode": "api",
                    "post_id": resp["id"],
                    "draft": draft,
                    "platform_post_url": resp.get("platformPostUrl"),
                    "detail": resp}
        if status in (200, 201):
            post = resp.get("post") if isinstance(
                resp.get("post"), dict) else {}
            return {"ok": True, "mode": "api", "draft": draft,
                    "post_id": resp.get("postId") or post.get("id"),
                    "platform_post_url":
                        resp.get("platformPostUrl")
                        or post.get("platformPostUrl"),
                    "detail": resp}
        return _error_result(status, resp)

    def publish_video(self, clip_path, title, account_id=None,
                      draft=True, dry_run=False):
        """Video completo por Zernio: presign + PUT + POST /v1/posts.

        draft=True (por defecto en este primitivo): queda como
        borrador en Zernio, no sale en público. draft=False:
        publica en público (publishNow True) — es lo que usa
        publish_public() tras el pre-chequeo. dry_run=True:
        valida todo sin red.
        """
        if dry_run:
            if not self._key():
                return _draft("sin ZERNIO_API_KEY en el entorno "
                              "(secreto ZERNIO_API_KEY)")
            if not (account_id or self._account()):
                return _draft("sin cuenta de TikTok en Zernio "
                              "(ZERNIO_TIKTOK_ACCOUNT_ID)")
            if not os.path.isfile(clip_path):
                return {"ok": False, "reason":
                        f"archivo no existe: {clip_path}"}
            return self.create_post(
                "https://example.com/dry-run.mp4", title,
                account_id=account_id, draft=draft, dry_run=True)
        up = self.upload_media(clip_path)
        if not up.get("ok"):
            return up
        return self.create_post(up["public_url"], title,
                                account_id=account_id, draft=draft)

    def publish_public(self, clip_path, title, account_id=None):
        """Publicación DIRECTA en público por Zernio (vía
        automática 2026-11-18): presign + PUT, pre-chequeo dryRun
        contra Zernio y POST /v1/posts con draft=False
        (publishNow True, PUBLIC_TO_EVERYONE por la app TikTok
        for Business de Zernio).

        Si el pre-chequeo dice que no se puede publicar, o falta
        clave/cuenta, devuelve mode "draft" con el motivo y NO
        publica nada. Si Zernio publica, devuelve ok + post_id +
        platform_post_url (URL pública del video en TikTok).
        """
        if not self._key():
            return _draft("sin ZERNIO_API_KEY en el entorno "
                          "(secreto ZERNIO_API_KEY)")
        if not (account_id or self._account()):
            return _draft("sin cuenta de TikTok en Zernio "
                          "(ZERNIO_TIKTOK_ACCOUNT_ID)")
        if not os.path.isfile(clip_path):
            return {"ok": False, "reason":
                    f"archivo no existe: {clip_path}"}
        up = self.upload_media(clip_path)
        if not up.get("ok"):
            return up
        pre = self.can_publish(up["public_url"], title,
                               account_id=account_id)
        if not pre.get("ok") or not pre.get("can_publish"):
            return _draft(
                "Zernio dice que TikTok no puede publicar ahora: "
                f"{pre.get('reason') or pre.get('detail') or 'sin motivo'}",
                preflight=pre)
        return self.create_post(up["public_url"], title,
                                account_id=account_id, draft=False)


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

    # --- Publicación completa (fan-out): contenedor -> esperar ->
    # publicar ---. El contenedor caduca a las 24 h sin publicar;
    # status_code: IN_PROGRESS / FINISHED / PUBLISHED / ERROR /
    # EXPIRED. Meta recomienda consultar 1 vez/min, máx. ~5 min.
    POLL_INTERVAL_S = 15
    MAX_POLLS = 10  # ~2,5 min; si sigue en proceso, se retoma luego

    def publish_reel(self, ig_user_id, video_url, caption,
                     cover_url=None, resume_container_id=None,
                     sleep_fn=None, max_polls=None):
        """Reel completo: crear contenedor -> esperar FINISHED ->
        publicar. Sin ig_user_id, sin token o sin URL pública ->
        draft con el motivo. Si sigue IN_PROGRESS tras max_polls,
        devuelve mode "processing" con el container_id para retomar
        sin crear otro contenedor. `sleep_fn` se inyecta en tests.
        """
        sleep = sleep_fn or time.sleep
        max_polls = self.MAX_POLLS if max_polls is None else max_polls
        if not ig_user_id:
            return _draft("sin ig_user_id de Instagram configurado "
                          "(platform_accounts.instagram_reels.ig_user_id "
                          "o IG_USER_ID)")
        if not self._token():
            return _draft("sin token de Instagram en tokens/")
        if not (video_url or "").startswith("https://"):
            return _draft("sin URL pública configurada: Instagram "
                          "exige una URL HTTPS pública del video "
                          "(ver public_url.base_url en config.yaml)")
        container_id = resume_container_id
        recreated = False
        if container_id is None:
            created = self.create_container(
                ig_user_id, video_url, caption, cover_url)
            if not created.get("ok"):
                return created
            container_id = created["container_id"]
        for _ in range(max_polls):
            st = self.container_status(container_id)
            if not st.get("ok"):
                return st
            code = st.get("status_code")
            if code == "FINISHED":
                return self.publish(ig_user_id, container_id)
            if code == "PUBLISHED":
                return {"ok": True, "mode": "api",
                        "container_id": container_id,
                        "note": "el contenedor ya estaba publicado"}
            if code == "ERROR":
                return {"ok": False, "mode": "error",
                        "reason": "Instagram: el contenedor quedó "
                                  "en ERROR (video no procesable)",
                        "retryable": False}
            if code == "EXPIRED":
                if resume_container_id is not None and not recreated:
                    recreated = True
                    created = self.create_container(
                        ig_user_id, video_url, caption, cover_url)
                    if not created.get("ok"):
                        return created
                    container_id = created["container_id"]
                    continue
                return {"ok": False, "mode": "error",
                        "reason": "Instagram: el contenedor caducó "
                                  "(24 h sin publicar)",
                        "retryable": False}
            sleep(self.POLL_INTERVAL_S)
        return {"ok": False, "mode": "processing",
                "container_id": container_id, "retryable": True,
                "reason": "Instagram sigue procesando el contenedor; "
                          "se retoma en el siguiente ciclo"}


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

    # --- Subida por chunks (fan-out): fase 2 que faltaba ---
    # Docs oficiales (verificado 2026-11-17 vía guías que citan la
    # guía "Reels Publishing" de developers.facebook.com):
    #   POST https://rupload.facebook.com/video-upload/v{versión}/
    #   {video_id}, header Authorization: OAuth {token} (OAuth, no
    #   Bearer), headers offset y file_size, body binario. Reanudable
    #   por offset: la respuesta trae start_offset/end_offset.
    #   También acepta file_url con una URL pública en vez de bytes.
    CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB por chunk

    def upload_file(self, video_id, clip_path, chunk_size=None):
        """Fase 2: sube el archivo por chunks a rupload.

        Devuelve {"ok": True} al completar. Si la respuesta no
        avanza el offset, es error (no se reintenta a ciegas)."""
        access = self._token()
        if not access:
            return _draft("sin Page access token en tokens/")
        if not os.path.isfile(clip_path):
            return {"ok": False, "reason":
                    f"archivo no existe: {clip_path}"}
        chunk_size = chunk_size or self.CHUNK_SIZE
        size = os.path.getsize(clip_path)
        url = (f"{self.upload_host}/video-upload/"
               f"{GRAPH_VERSION}/{video_id}")
        offset = 0
        with open(clip_path, "rb") as f:
            while offset < size:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                headers = {"Authorization": f"OAuth {access}",
                           "Content-Type": "application/octet-stream",
                           "offset": str(offset),
                           "file_size": str(size)}
                status, resp = _http(
                    "POST", url, headers, chunk,
                    request_fn=self.request_fn)
                if status != 200:
                    return _error_result(status, resp)
                new_offset = resp.get("end_offset")
                if new_offset is None:
                    new_offset = offset + len(chunk)
                try:
                    new_offset = int(new_offset)
                except (TypeError, ValueError):
                    new_offset = offset + len(chunk)
                if new_offset <= offset:
                    return {"ok": False, "mode": "error",
                            "reason": "rupload no avanzó el offset "
                                      f"({offset}/{size} bytes)",
                            "retryable": True}
                offset = new_offset
        if offset < size:
            return {"ok": False, "mode": "error",
                    "reason": "subida incompleta a Facebook "
                              f"({offset}/{size} bytes)",
                    "retryable": True}
        return {"ok": True}

    def publish_reel(self, page_id, clip_path, description):
        """Reel completo: start -> subida por chunks -> finish.

        Sin page_id o sin token -> draft con el motivo. Los errores
        de la API se clasifican (5xx reintentable, resto final)."""
        if not page_id:
            return _draft("sin page_id de Facebook configurado "
                          "(platform_accounts.facebook_reels.page_id "
                          "o FACEBOOK_PAGE_ID)")
        start = self.start_upload(page_id)
        if not start.get("ok"):
            return start
        up = self.upload_file(start["video_id"], clip_path)
        if not up.get("ok"):
            return up
        return self.finish_upload(page_id, start["video_id"],
                                  description)

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
        if not _creds.get_secret("TWITCH_CLIENT_ID"):
            return {"ok": False, "reason": "sin credenciales de Twitch"}
        token = self.app_token()
        if not token:
            return {"ok": False,
                    "reason": "no se pudo pedir el token de app "
                              "(id.twitch.tv no respondió)"}
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
