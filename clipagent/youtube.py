"""Fuente YouTube para el cerebro (2026-11-21, orden del usuario:
"Debemos tener de YouTube también").

Hoy el watcher solo soporta kick/twitch y salta a ishowspeed
(YouTube) con motivo visible; el fallback VOD no tiene
``youtube_check``. Este módulo es la rama mínima propuesta en el
corte de Speed (``runs/speed-52h-save-2026-11-21/REPORT.md`` §6,
probada a mano en ese run, no aplicada aún):

- ``youtube_check``: ``yt-dlp --flat-playlist`` en JSON sobre
  las pestañas ``/streams`` (los directos pasados = los VODs)
  y ``/videos`` del canal (sin API key, $0), con la misma forma
  ``(live, vods)`` que ``kick_check`` / Twitch, para que el
  watcher y el fallback VOD la consuman sin tocar su lógica.
- Verificación del juego por regex de título + canal: YouTube no
  trae categoría Helix, así que el título es la única señal
  (``youtube_vod_is_gta6`` / ``youtube_game_from_title``). Ante la
  duda se procesa (fail open, como el pre-chequeo del fallback):
  la puerta precisa sigue siendo el juego del manifiesto por clip.
- ``download_youtube_segment``: solo el segmento necesario con
  ``yt-dlp --download-sections`` (probado en el run de Speed:
  ``-f 298+140 --download-sections '*05:29:00-05:34:00'`` sobre
  ``OIeLbmyraRQ``), nunca el VOD entero.

Riesgos conocidos (no se esconden):
- El bot-check de YouTube es intermitente (en el run de Speed
  bloqueó los intentos 1-2 y el 3º funcionó): las dos funciones
  reintentan con backoff (3 intentos). Cookies como respaldo
  quedan fuera de este cambio.
- Los directos de YouTube se parten en varias partes (YouTube
  corta a 24 h): el maratón de Speed son 3 VODs (``qSyo5d-dVzA``,
  ``hwSg39yIBGU``, ``OIeLbmyraRQ``); el listado los trae a los 3
  y el fallback los trata como VODs independientes.
- ``live`` es el directo en curso (dict con la forma del
  watcher: id, title, game, viewers, started_at) o None si no
  hay. Se detecta por la pestaña /live (resuelve al vídeo en
  curso; verificado en vivo 2026-11-21: sin directo yt-dlp sale
  con error "The channel is not currently live", con directo
  devuelve su info con ``live_status: is_live`` y
  ``concurrent_view_count``) y, si esa pestaña falla, por la
  entrada is_live del listado de /streams. Un fallo de la
  pestaña /live no tumba el chequeo: live=None y el listado de
  VODs sigue. Un directo en curso que aparezca en el listado
  viene marcado ``live=True`` en su VOD para que los llamadores
  lo salten como "en curso".
- Esto NO corta clips de YouTube solo: ``auto_clips.py`` es
  Twitch/Kick (chat + TwitchDownloaderCLI) y no sabe bajar de
  YouTube. Con el flag activado, el fallback LISTA los VODs de
  YouTube y el watcher los VE, pero ``run_auto_clips`` sobre una
  URL de YouTube falla y el VOD se salta sin clips. El corte
  automático desde YouTube (segmento + picos por heatmap en vez
  de chat) es trabajo posterior, no este cambio.

Activación: apagado por defecto. ``watcher.run_once`` y
``vod_fallback.run`` / ``latest_vod_for`` solo llaman a
``youtube_check`` con ``youtube_enabled=True`` (CLI ``--youtube``
en ``watcher.py``, ``clipagent vod-fallback`` y ``clipagent
watch``). Sin el flag, el comportamiento es el de hoy: aviso
visible y se salta el influencer.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

# Formato probado en el run de Speed (1280x720 60 fps + audio);
# si 298+140 no existe para un VOD, cae a bestvideo <=720p + audio.
YOUTUBE_FORMAT = "298+140/bv[height<=720]+ba/b"
# yt-dlp usa certifi por defecto y en esta VM el proxy de salida
# presenta una CA que solo vive en el almacén del sistema
# (curl y urllib la aceptan, certifi no): sin
# --compat-options no-certifi, TODO yt-dlp falla con
# CERTIFICATE_VERIFY_FAILED. En CI (sin proxy) el almacén del
# sistema son las raíces normales, así que el flag es inocuo
# allí. NUNCA --no-check-certificates (eso quitaría la
# verificación).
YTDLP_BASE_OPTS = ["--compat-options", "no-certifi", "--no-warnings"]
BOT_CHECK_MARKERS = (
    "sign in to confirm",
    "are a bot",
    "bot check",
    "unable to extract",
    "http error 429",
    "too many requests",
)
MAX_ATTEMPTS = 3
# Backoff entre reintentos (segundos): 5 s, 10 s.
RETRY_BACKOFF_S = (5, 10)

# Tokens del título que mencionan GTA VI (misma lista que el
# pre-chequeo de vod_fallback._title_mentions_gta6: "GTA 6",
# "GTA VI", "GTA6", "Grand Theft Auto VI").
_GTA6_TITLE_TOKENS = ("gtavi", "gta6", "grandtheftautovi")


class YoutubeError(Exception):
    """YouTube no respondió o no se pudo leer/descargar (tras
    reintentos). Nunca tumba al llamador: el watcher lo convierte
    en aviso y el fallback salta el VOD."""


def is_youtube_url(url):
    """¿Es una URL de YouTube (vídeo o canal)?

    Host en (youtube.com, www.youtube.com, m.youtube.com,
    youtu.be). Es el predicado del dispatch de ``run()`` en
    vod_fallback: una URL de YouTube va por el cortador de
    YouTube, cualquier otra por run_auto_clips (Twitch/Kick).
    """
    from urllib.parse import urlparse

    try:
        host = (urlparse(url or "").hostname or "").lower()
    except (TypeError, ValueError):
        return False
    return host in ("youtube.com", "www.youtube.com",
                    "m.youtube.com", "youtu.be")


def format_hms(seconds):
    """Segundos -> 'HH:MM:SS' (el formato de --download-sections).
    Acepta horas > 24 (un VOD de YouTube puede durar 24 h)."""
    total = int(seconds)
    if total < 0:
        raise ValueError(f"segundos negativos: {seconds!r}")
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def channel_videos_url(channel_url):
    """URL de la pestaña de vídeos de un canal.

    Acepta la URL del canal (``https://www.youtube.com/@Handle``,
    ``/channel/UC...``) o el handle solo (``@Handle``,
    ``Handle``)."""
    raw = (channel_url or "").strip().rstrip("/")
    if not raw:
        raise YoutubeError("sin channel_url de YouTube")
    if raw.startswith("@") or ("/" not in raw and "." not in raw):
        raw = f"https://www.youtube.com/@{raw.lstrip('@')}"
    if raw.endswith("/videos"):
        return raw
    for tab in ("/streams", "/shorts", "/live"):
        if raw.endswith(tab):
            raw = raw[: -len(tab)]
            break
    return raw + "/videos"


def _ytdlp_cmd():
    """Prefijo para llamar a yt-dlp: binario si existe, si no el
    módulo (``python -m yt_dlp``). Sin ninguno, error claro en vez
    de un fallo raro del subprocess."""
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    try:
        import yt_dlp  # noqa: F401
    except ImportError as e:
        raise YoutubeError(
            "yt-dlp no está instalado (ni binario ni módulo "
            "yt_dlp): sin él no hay fuente YouTube"
        ) from e
    return [sys.executable, "-m", "yt_dlp"]


def _default_runner(cmd):
    """Ejecuta el comando y devuelve CompletedProcess (stdout /
    stderr como texto). Es el runner por defecto; los tests
    inyectan uno falso."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600)


def _run_with_retries(cmd, runner, sleep, retries=MAX_ATTEMPTS):
    """Ejecuta ``cmd`` hasta ``retries`` intentos (bot-check
    intermitente de YouTube). Devuelve el CompletedProcess del
    intento que salió con código 0, o el último si ninguno salió.
    """
    do_sleep = sleep or time.sleep
    last = None
    for attempt in range(1, retries + 1):
        last = runner(cmd)
        if last.returncode == 0:
            return last
        if attempt < retries:
            do_sleep(
                RETRY_BACKOFF_S[attempt - 1]
                if attempt - 1 < len(RETRY_BACKOFF_S)
                else RETRY_BACKOFF_S[-1]
            )
    return last


def _parse_upload_date(value):
    """Fecha de subida -> datetime UTC, o None.

    El --flat-playlist trae ``timestamp`` (epoch) o
    ``upload_date`` ('YYYYMMDD'); en las pestañas de canal de
    esta versión de yt-dlp los dos vienen vacíos casi siempre,
    así que None es normal: el orden del listado (más reciente
    primero) es lo que ordena los VODs.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        # timestamp (epoch). OJO: no probar fromtimestamp con un
        # str numérico ("20261120" es un epoch válido de 1970 y
        # taparía el parseo de upload_date).
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            pass
    try:
        return datetime.strptime(str(value), "%Y%m%d").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return None


def _channel_from_url(url):
    """'@IShowSpeed' desde una URL de canal/uploader."""
    raw = (url or "").strip().rstrip("/")
    if not raw:
        return ""
    return raw.split("/")[-1].split("?")[0]


def _entry_to_vod(entry, channel=""):
    """Una entrada del --flat-playlist -> dict VOD con la forma del
    watcher (misma que kick_check / Twitch): platform, vod_id,
    url, title, category, category_source, start_time,
    duration_s, views, live.

    ``channel`` es el handle del canal pedido (el listado viene
    de ese canal): las entradas del --flat-playlist traen
    uploader_url/channel_url en None casi siempre, así que sin
    este parámetro el VOD quedaría sin canal y la verificación
    por canal de ``youtube_vod_is_gta6`` no podría aplicar.
    """
    vid = str(entry.get("id") or "").strip()
    cats = entry.get("categories") or []
    category = str(cats[0]) if cats else ""
    live_status = str(entry.get("live_status") or "")
    try:
        duration_s = int(entry.get("duration") or 0)
    except (TypeError, ValueError):
        duration_s = 0
    try:
        views = int(entry.get("view_count") or 0)
    except (TypeError, ValueError):
        views = 0
    # El --flat-playlist no trae "channel"/"uploader" en las
    # pestañas de canal: el canal viene en uploader_url /
    # channel_url ("https://www.youtube.com/@IShowSpeed"), y en
    # vivo vienen en None: entonces manda el canal pedido (el
    # listado viene de ese canal).
    channel = (
        _channel_from_url(entry.get("uploader_url"))
        or _channel_from_url(entry.get("channel_url"))
        or entry.get("channel")
        or entry.get("uploader")
        or channel
        or ""
    )
    return {
        "platform": "youtube",
        "vod_id": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "title": entry.get("title") or "",
        "category": category,
        "category_source": "youtube" if category else "",
        # timestamp (epoch) o upload_date; casi siempre None en
        # pestañas de canal (el orden del listado manda).
        "start_time": _parse_upload_date(
            entry.get("timestamp")
            if entry.get("timestamp") is not None
            else entry.get("upload_date")
        ),
        "duration_s": duration_s,
        "views": views,
        # En curso (o por empezar) no es un VOD terminado: los
        # llamadores lo saltan como "en curso" y lo reintentan.
        # "was_live" es un directo YA terminado: VOD válido.
        "live": live_status in ("is_live", "is_upcoming"),
        "channel": channel,
    }


def _fetch_tab(url, runner, sleep, retries):
    """Entradas del --flat-playlist de una pestaña del canal
    (``.../videos`` o ``.../streams``). Sin entradas, lista
    vacía. Con bot-check o sin respuesta tras los reintentos,
    levanta YoutubeError."""
    cmd = _ytdlp_cmd() + YTDLP_BASE_OPTS + ["--flat-playlist", "-J", url]
    proc = _run_with_retries(
        cmd, runner or _default_runner, sleep, retries=retries
    )
    if proc.returncode != 0:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip()
        raise YoutubeError(
            f"yt-dlp --flat-playlist falló sobre {url} "
            f"(código {proc.returncode}): {tail[-300:]}"
        )
    try:
        data = json.loads(proc.stdout or "")
    except ValueError as e:
        raise YoutubeError(
            f"yt-dlp devolvió JSON inválido sobre {url}: {e}"
        ) from e
    entries = data.get("entries") or []
    if not isinstance(entries, list):
        raise YoutubeError(f"yt-dlp devolvió 'entries' no-lista sobre {url}")
    return entries


def _entry_to_live(entry):
    """Una entrada de directo en curso -> dict live con la forma
    del watcher (misma que kick_check / Twitch): id, title, game,
    viewers, started_at.

    ``game`` se resuelve desde el título (YouTube no trae
    categoría Helix: ``youtube_game_from_title``, definido abajo
    en este módulo y resuelto en tiempo de llamada); ``viewers``
    es el concurrente si lo trae (pestaña /live) o el view_count
    del listado; ``started_at`` es el timestamp (epoch) si lo
    trae. Devuelve None si la entrada no trae id."""
    vid = str(entry.get("id") or "").strip()
    if not vid:
        return None
    title = entry.get("title") or ""
    raw_viewers = entry.get("concurrent_view_count")
    if raw_viewers is None:
        raw_viewers = entry.get("view_count") or 0
    try:
        viewers = int(raw_viewers)
    except (TypeError, ValueError):
        viewers = 0
    start = _parse_upload_date(
        entry.get("timestamp")
        if entry.get("timestamp") is not None
        else entry.get("upload_date")
    )
    return {
        "id": vid,
        "title": title,
        "game": youtube_game_from_title(title) or "",
        "viewers": viewers,
        "started_at": start.isoformat() if start else "",
    }


# "The channel is not currently live": la pestaña /live sin
# directo. Es una respuesta DEFINITIVA (no un fallo): no se
# reintenta.
_LIVE_NOT_LIVE_MARKER = "not currently live"


def _fetch_live_tab(base, runner, sleep, retries):
    """Directo en curso de la pestaña /live del canal, o None.

    Devuelve la ENTRADA cruda (dict de vídeo o de listado), no
    el dict live: el llamador la convierte con _entry_to_live
    (y la añade al listado con _entry_to_vod si no está).

    Verificado en vivo 2026-11-21 (sin red en los tests):
    - Canal en vivo: la pestaña resuelve al vídeo en curso y
      yt-dlp devuelve su info (``live_status: is_live``,
      ``concurrent_view_count``, ``timestamp``). No trae
      ``entries``: es un dict de vídeo, no una lista.
    - Canal sin directo: yt-dlp sale con error "The channel is
      not currently live" (respuesta definitiva: se devuelve
      None sin gastar reintentos).
    - Bot-check / red: se reintenta (MAX_ATTEMPTS) y si sigue
      fallando se devuelve None: sin dato de directo, NO es un
      error fatal (el directo se detecta igual por /streams si
      está ahí; el listado de VODs no se toca).
    """
    url = base[: -len("/videos")] + "/live"
    cmd = _ytdlp_cmd() + YTDLP_BASE_OPTS + ["--flat-playlist", "-J", url]
    run = runner or _default_runner
    do_sleep = sleep or time.sleep
    last = None
    for attempt in range(1, retries + 1):
        last = run(cmd)
        if last.returncode == 0:
            break
        tail = ((last.stderr or "") + (last.stdout or "")).strip()
        if _LIVE_NOT_LIVE_MARKER in tail.lower():
            return None
        if attempt < retries:
            do_sleep(
                RETRY_BACKOFF_S[attempt - 1]
                if attempt - 1 < len(RETRY_BACKOFF_S)
                else RETRY_BACKOFF_S[-1]
            )
    if last is None or last.returncode != 0:
        return None
    try:
        data = json.loads(last.stdout or "")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    # La pestaña /live resuelve a UN vídeo (dict, sin entries).
    if data.get("id") and "entries" not in data:
        status = str(data.get("live_status") or "")
        is_live_flag = data.get("is_live")
        if status == "is_live" or is_live_flag is True:
            return data
        # Sin live_status explícito y sin is_live=False: la
        # pestaña /live solo existe si hay directo en curso.
        if status == "" and is_live_flag is not False:
            return data
        return None
    # Por si alguna versión devuelve lista: la entrada en curso.
    for entry in data.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("live_status") or "") == "is_live":
            return entry
    return None


def youtube_check(channel_url, runner=None, sleep=None, retries=MAX_ATTEMPTS,
                  include_shorts_tab=False):
    """Devuelve (live, [vods]) para un canal de YouTube.

    ``channel_url`` es la URL del canal o el handle (ver
    ``channel_videos_url``). ``live`` es el directo en curso
    (dict id/title/game/viewers/started_at, misma forma que
    kick_check / Twitch) o None si no hay: se detecta por la
    pestaña /live y, si falla, por la entrada is_live de
    /streams. Se leen DOS pestañas para los VODs: primero
    ``/streams`` (los directos pasados = los VODs; verificado en
    vivo 2026-11-21: el maratón de Speed solo está ahí, la
    pestaña /videos solo trae subidas normales) y después
    ``/videos``, sin duplicar IDs. Los VODs salen en el orden
    del listado (más reciente primero); sin fecha en las
    entradas, ese orden es lo único que ordena. Sin vídeos,
    lista vacía (no es error). Con bot-check o sin respuesta
    tras los reintentos, levanta YoutubeError.

    Con ``include_shorts_tab`` se lee además la pestaña
    ``/shorts`` (la fuente viral la necesita: /videos solo trae
    11 Shorts de Speed y ninguno es GTA 6, mientras /shorts trae
    239 con "I BEAT GTA 6!" de 4 M vistas a la cabeza, verificado
    en vivo 2026-11-21). Las entradas de /shorts solo traen
    id/título/view_count (sin duración, fecha ni categoría) y
    llevan ``from_shorts_tab: True``: el llamador NO puede
    filtrar por duración ni edad con ellas y tiene que verificar
    la duración con la metadata completa (ver
    viral_source.enrich_candidate). Por defecto NO se lee (el
    watcher no quiere VODs sin duración ni fecha).
    """
    base = channel_videos_url(channel_url)
    streams_url = base[: -len("/videos")] + "/streams"
    shorts_url = base[: -len("/videos")] + "/shorts"
    handle = _channel_from_url(channel_url)
    vods = []
    seen = set()
    streams_live_entry = None
    tab_urls = [streams_url, base]
    if include_shorts_tab:
        tab_urls.append(shorts_url)
    for url in tab_urls:
        for entry in _fetch_tab(url, runner, sleep, retries):
            if not isinstance(entry, dict):
                continue
            vid = str(entry.get("id") or "").strip()
            if not vid or vid in seen:
                continue
            seen.add(vid)
            if (
                streams_live_entry is None
                and url == streams_url
                and str(entry.get("live_status") or "") == "is_live"
            ):
                streams_live_entry = entry
            vod = _entry_to_vod(entry, channel=handle)
            if url == shorts_url:
                vod["from_shorts_tab"] = True
            vods.append(vod)
    # Directo en curso: la pestaña /live manda; si no trae nada
    # (sin directo, o bot-check), la entrada is_live de /streams.
    live_entry = _fetch_live_tab(base, runner, sleep, retries)
    if live_entry is None:
        live_entry = streams_live_entry
    live = None
    if live_entry is not None:
        live = _entry_to_live(live_entry)
        # El VOD del directo tiene que estar en el listado para
        # que el watcher lo registre como vod_en_curso: si /live
        # lo trae y /streams no, se añade (sin duplicar).
        if live is not None and live["id"] not in seen:
            vods.append(_entry_to_vod(live_entry, channel=handle))
            seen.add(live["id"])
    _min = datetime.min.replace(tzinfo=timezone.utc)
    vods.sort(
        key=lambda v: (v["start_time"] is not None, v["start_time"] or _min),
        reverse=True,
    )
    return live, vods


def _title_mentions_gta6(title):
    """¿El título menciona GTA VI? Misma regla que el pre-chequeo
    de vod_fallback (subcadena sobre el título compactado)."""
    compact = "".join(ch for ch in (title or "").lower() if ch.isalnum())
    return any(tok in compact for tok in _GTA6_TITLE_TOKENS)


def _norm_channel(value):
    """Handle comparable: minúsculas, sin '@', sin ruta ni query
    (``@IShowSpeed`` == ``ishowspeed`` == URL del canal)."""
    v = (value or "").strip().lower()
    if "/@" in v:
        v = v.split("/@", 1)[1]
    return v.split("?")[0].split("/")[0].lstrip("@")


def youtube_vod_is_gta6(vod, expected_channel=""):
    """¿Este VOD de YouTube es GTA VI (título + canal)?

    True si el título menciona GTA VI (o la categoría, si la
    trae, es GTA VI). Si se pasa ``expected_channel`` y el VOD
    trae canal y no coincide, False (el VOD no es de ese canal,
    aunque el título mencione GTA VI). Sin canal en el VOD no se
    veta por canal: el listado ya viene del canal pedido
    (fail open). Sin mención en el título ni categoría GTA VI,
    False.
    """
    vod = vod or {}
    if expected_channel:
        got = _norm_channel(vod.get("channel") or vod.get("uploader") or "")
        if got and got != _norm_channel(expected_channel):
            return False
    if _title_mentions_gta6(vod.get("title") or ""):
        return True
    category = (vod.get("category") or "").strip()
    if not category:
        return False
    from .category import is_gta6

    return bool(is_gta6(category))


def youtube_game_from_title(title, games=None):
    """ID del juego en config (``games:``) si el título menciona
    GTA VI, o None. Es el fallback del watcher: YouTube no trae
    categoría Helix, así que sin esto ``detect_game`` devuelve
    None y el VOD se salta como juego_no_permitido aunque el
    título sea "BEATING GTA 6". Solo se usa para plataforma
    youtube (en twitch/kick la categoría manda, como siempre)."""
    if not _title_mentions_gta6(title or ""):
        return None
    if games is None:
        try:
            from jobqueue import load_games_config

            games, _, _ = load_games_config()
        except Exception:  # noqa: BLE001 - sin config no hay juego
            return None
    from .category import is_gta6

    for game in games or []:
        for key in ("id", "name"):
            value = game.get(key) or ""
            if value and is_gta6(value):
                return game.get("id") or value
    return None


def download_youtube_segment(
    video_url,
    start_s,
    end_s,
    out_path,
    format=YOUTUBE_FORMAT,
    retries=MAX_ATTEMPTS,
    runner=None,
    sleep=None,
):
    """Baja solo el segmento [start_s, end_s) de un VOD de YouTube
    con ``yt-dlp --download-sections`` (probado en el run de
    Speed: ``'*05:31:46-05:32:34'`` sobre ``OIeLbmyraRQ``).

    Devuelve la ruta del archivo. Reintenta con backoff ante el
    bot-check intermitente; tras los reintentos levanta
    YoutubeError. Nunca baja el VOD entero.
    """
    if end_s <= start_s:
        raise ValueError(f"segmento vacío o invertido: {start_s}-{end_s}")
    sections = f"*{format_hms(start_s)}-{format_hms(end_s)}"
    cmd = (
        _ytdlp_cmd()
        + YTDLP_BASE_OPTS
        + [
            "-f",
            format,
            "--download-sections",
            sections,
            "-o",
            str(out_path),
            "--no-progress",
            video_url,
        ]
    )
    proc = _run_with_retries(
        cmd, runner or _default_runner, sleep, retries=retries
    )
    if proc.returncode != 0 or not os.path.isfile(out_path):
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip()
        raise YoutubeError(
            f"yt-dlp --download-sections falló sobre {video_url} "
            f"({sections}, código {proc.returncode}): "
            f"{tail[-300:]}"
        )
    return str(out_path)
