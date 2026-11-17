#!/usr/bin/env python3
"""kick_downloader.core — lógica de descarga de Kick.

Adaptado de viral-clip-agent/kick.py (2026-11-16), misma lógica y
mismos endpoints, sin inventar ninguno nuevo:

  * Metadatos del VOD:  GET https://kick.com/api/v1/video/{uuid-legacy}
    y listado del canal: GET https://kick.com/api/v2/channels/{slug}/videos
    (ese listado SÍ incluye los VODs, con `source` = master.m3u8,
    `start_time`, `duration` en ms y `video.uuid` legacy).
  * El UUID de la URL (kick.com/<canal>/videos/<uuid>) es un UUIDv7: sus
    primeros 48 bits son la fecha de creación en ms. El API v1 solo acepta
    el UUIDv4 legacy, así que se resuelve buscando en el listado el VOD
    cuyo start_time esté a ≤5 s de la fecha que trae el UUIDv7.
  * Chat replay: NO hay endpoint público documentado, pero SÍ existe uno
    sin documentar que usa la propia web de Kick:
        GET https://web.kick.com/api/v1/chat/{channel_id}/history
            ?start_time=2026-11-15T20:00:00.000Z
    Devuelve los mensajes de los 5 segundos siguientes a start_time.
    Para un VOD de 10h27m son ~7.525 ventanas; se descargan en paralelo
    (8 workers, con reintentos: el endpoint se cae a veces con
    IncompleteRead / conexión cerrada, no es rate-limit).
  * Video: el `source` del VOD es un master.m3u8 (HLS, Amazon IVS).
    Solo se baja el segmento que hace falta con ffmpeg
    (`-ss <inicio> -i <variante-720p> -t 60 -c copy`), nunca el VOD entero.

Por qué NO yt-dlp: el extractor kick:vod de yt-dlp está roto desde
julio de 2026 (el cambio de URLs de Kick lo dejó en 404 / "0 bytes read",
issue yt-dlp#17284). La ruta m3u8 directa es la que recomienda la propia
comunidad.

Sin credenciales, sin inicio de sesión. Solo se lee lo público.
Solo stdlib + ffmpeg externo.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

KICK_VIDEO_API = "https://kick.com/api/v1/video/{uuid}"
KICK_CHANNEL_VIDEOS_API = "https://kick.com/api/v2/channels/{slug}/videos"
KICK_CHAT_API = "https://web.kick.com/api/v1/chat/{channel_id}/history"

CHAT_WINDOW_SECS = 5
DEFAULT_WORKERS = 8
DEFAULT_DELAY_S = 0.0  # con workers en paralelo el delay no hace falta

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


class KickError(Exception):
    pass


# ---------------------------------------------------------------------------
# HTTP con reintentos (el API de Kick se cae a veces a mitad de respuesta)
# ---------------------------------------------------------------------------

def http_json(url, params=None, timeout=30, retries=4):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise KickError(f"404 Not Found: {url}")
            last_err = e
        except Exception as e:  # IncompleteRead, Remote end closed, timeout...
            last_err = e
        time.sleep(min(2 ** attempt, 8))
    # Fallback: curl es más robusto que urllib con los servidores de Kick
    # (HTTP/2, conexiones cortadas). Si curl también falla, ahí sí se rinde.
    try:
        r = subprocess.run(
            ["curl", "-s", "--compressed", "--max-time", str(timeout),
             "-A", HEADERS["User-Agent"], "-H", "Accept: application/json", url],
            capture_output=True, text=True, timeout=timeout + 10)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except Exception as e:
        last_err = e
    raise KickError(f"Request failed after {retries} attempts: {url} ({last_err})")


def http_text(url, timeout=30, retries=3):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last_err = e
            time.sleep(min(2 ** attempt, 4))
    raise KickError(f"Request failed after {retries} attempts: {url} ({last_err})")


# ---------------------------------------------------------------------------
# URLs y fechas
# ---------------------------------------------------------------------------

def parse_kick_url(url: str):
    """kick.com/<canal>/videos/<uuid> -> (canal, uuid)."""
    m = re.search(r"kick\.com/([^/?#]+)/videos/([0-9a-f-]{36})", url)
    if not m:
        raise KickError(
            f"No reconozco la URL de Kick: {url!r}. "
            "Usa https://kick.com/<canal>/videos/<uuid>"
        )
    return m.group(1), m.group(2)


def is_uuidv7(uuid: str) -> bool:
    return len(uuid) > 14 and uuid[14] == "7"


def uuidv7_time_ms(uuid: str) -> int:
    """Los primeros 48 bits de un UUIDv7 son ms desde epoch."""
    return int(uuid.replace("-", "")[:12], 16)


def parse_kick_datetime(s: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S+00:00",
                "%Y-%m-%dT%H:%M:%S.%f+00:00"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise KickError(f"No puedo leer la fecha de Kick: {s!r}")


# ---------------------------------------------------------------------------
# Metadatos del VOD
# ---------------------------------------------------------------------------

def resolve_vod(slug: str, uuid: str) -> dict:
    """Resuelve un VOD de Kick y devuelve sus metadatos normalizados.

    Devuelve: channel, channel_id, title, start_time (datetime),
    duration_s, source (master.m3u8), views, language, category,
    live_stream_id, legacy_uuid, url_uuid.
    """
    listing = http_json(KICK_CHANNEL_VIDEOS_API.format(slug=slug))
    if not isinstance(listing, list):
        raise KickError(f"El listado de vídeos de {slug!r} no es una lista.")

    target = None
    if is_uuidv7(uuid):
        target = datetime.fromtimestamp(uuidv7_time_ms(uuid) / 1000, tz=timezone.utc)

    item = None
    if not is_uuidv7(uuid):
        # UUID legacy: buscarlo directo en el listado.
        for it in listing:
            if (it.get("video") or {}).get("uuid") == uuid:
                item = it
                break
    if item is None and target is not None:
        # UUIDv7: el VOD cuyo start_time esté más cerca de la fecha del UUID.
        best_diff = None
        for it in listing:
            start = it.get("start_time")
            if not start:
                continue
            try:
                diff = abs((parse_kick_datetime(start) - target).total_seconds())
            except KickError:
                continue
            if best_diff is None or diff < best_diff:
                best_diff, item = diff, it
        if item is None or best_diff is None or best_diff > 5:
            raise KickError(
                f"No encontré el VOD {uuid} en los últimos vídeos de {slug!r} "
                "(el listado de Kick solo trae los últimos ~30). "
                "Puede estar borrado o expirado: Kick borra los VODs a los "
                "7 días (30 días si el canal es verificado)."
            )

    if item is None:
        raise KickError(f"No encontré el VOD {uuid} en el canal {slug!r}.")

    legacy_uuid = (item.get("video") or {}).get("uuid")
    meta = None
    if legacy_uuid:
        try:
            meta = http_json(KICK_VIDEO_API.format(uuid=legacy_uuid))
        except KickError:
            meta = None  # el listado ya trae casi todo; seguir con eso
    livestream = (meta or {}).get("livestream") or {}
    channel = livestream.get("channel") or {}

    duration_ms = item.get("duration") or livestream.get("duration") or 0
    # La duración de Kick a veces viene en ms y a veces en s.
    duration_s = duration_ms / 1000.0 if duration_ms > 3 * 24 * 3600 else float(duration_ms)

    start_raw = item.get("start_time") or livestream.get("start_time") or ""
    try:
        start_time = parse_kick_datetime(start_raw)
    except KickError:
        start_time = None

    categories = item.get("categories") or []
    return {
        "platform": "kick",
        "url_uuid": uuid,
        "legacy_uuid": legacy_uuid,
        "live_stream_id": item.get("id"),
        "channel": channel.get("slug") or channel.get("username") or slug,
        "channel_id": item.get("channel_id") or livestream.get("channel_id") or channel.get("id"),
        "title": item.get("session_title") or livestream.get("session_title") or "",
        "start_time": start_time,
        "duration_s": duration_s,
        "source": item.get("source") or (meta or {}).get("source") or "",
        "views": item.get("views") or (meta or {}).get("views") or 0,
        "language": item.get("language") or "",
        "category": categories[0].get("name") if categories else "",
    }


# ---------------------------------------------------------------------------
# Chat replay (API sin documentar de web.kick.com)
# ---------------------------------------------------------------------------

def _fetch_chat_window(channel_id, start_dt):
    ts = start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    # Ventanas de chat: reintentos baratos (una ventana perdida son solo
    # 5 s de chat; no vale la pena el backoff largo de http_json).
    last_err = None
    for attempt in range(2):
        try:
            data = http_json(KICK_CHAT_API.format(channel_id=channel_id),
                             params={"start_time": ts}, retries=2)
            return (data.get("data") or {}).get("messages") or []
        except KickError as e:
            last_err = e
            time.sleep(1)
    raise KickError(f"Ventana {ts} sin chat tras reintentos: {last_err}")


def download_chat(meta, out_chat_json, out_raw_ndjson=None,
                  workers=DEFAULT_WORKERS, progress=True):
    """Descarga el chat completo del VOD, ventana a ventana (5 s).

    Guarda chat.json en el MISMO formato que TwitchDownloader, para que
    clips_chat_v2.score_windows() lo pueda puntuar sin cambios:
    {"comments": [{"content_offset_seconds": float,
                   "message": {"body": str},
                   "commenter": {"name": str}}]}
    Devuelve (n_mensajes, ruta_chat_json).
    """
    channel_id = meta["channel_id"]
    start = meta["start_time"]
    duration = int(meta["duration_s"])
    if not channel_id or not start or duration <= 0:
        raise KickError("Faltan channel_id, start_time o duration para bajar el chat.")

    total_windows = (duration + CHAT_WINDOW_SECS - 1) // CHAT_WINDOW_SECS
    windows = [start + timedelta(seconds=CHAT_WINDOW_SECS * i)
               for i in range(total_windows)]
    if progress:
        print(f"Chat Kick: {total_windows} ventanas de {CHAT_WINDOW_SECS}s "
              f"({duration // 3600}h{(duration % 3600) // 60:02d}m), "
              f"{workers} workers en paralelo...")

    # Reanudación: cada ventana completada se guarda en el NDJSON y en
    # <chat.json>.progress.json. Si el proceso muere (o lo matan), la
    # siguiente corrida salta las ventanas ya hechas en vez de empezar de 0.
    os.makedirs(os.path.dirname(out_chat_json) or ".", exist_ok=True)
    progress_path = out_chat_json + ".progress.json"
    done_windows = set()
    if os.path.isfile(progress_path):
        try:
            done_windows = set(json.load(open(progress_path, encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            done_windows = set()
    todo = [w for w in windows if w.strftime("%Y-%m-%dT%H:%M:%S.000Z") not in done_windows]
    if progress and done_windows:
        print(f"  Reanudando: {len(done_windows)} ventanas ya descargadas, "
              f"faltan {len(todo)}.")
    if out_raw_ndjson and not os.path.isfile(out_raw_ndjson):
        open(out_raw_ndjson, "w", encoding="utf-8").close()

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch_chat_window, channel_id, w): w for w in todo}
        raw_f = open(out_raw_ndjson, "a", encoding="utf-8") if out_raw_ndjson else None
        try:
            for fut in as_completed(futs):
                done += 1
                w = futs[fut]
                if progress and done % 500 == 0:
                    print(f"  {done}/{len(todo)} ventanas nuevas...")
                try:
                    msgs = fut.result()
                except KickError as e:
                    print(f"  Aviso: ventana {w} sin chat: {e}", file=sys.stderr)
                    msgs = []
                if raw_f:
                    for msg in msgs:
                        raw_f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                    raw_f.flush()
                done_windows.add(w.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
                if done % 100 == 0:
                    with open(progress_path, "w", encoding="utf-8") as pf:
                        json.dump(sorted(done_windows), pf)
        finally:
            if raw_f:
                raw_f.close()
    with open(progress_path, "w", encoding="utf-8") as pf:
        json.dump(sorted(done_windows), pf)

    # Reconstruir chat.json desde el NDJSON (incluye corridas anteriores).
    messages, seen = [], set()
    if out_raw_ndjson and os.path.isfile(out_raw_ndjson):
        with open(out_raw_ndjson, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                mid = msg.get("id")
                if mid and mid in seen:
                    continue
                if mid:
                    seen.add(mid)
                messages.append(msg)
    messages.sort(key=lambda m: m.get("created_at", ""))
    comments = []
    for msg in messages:
        try:
            offset = (parse_kick_datetime(msg.get("created_at", "")) - start).total_seconds()
        except KickError:
            continue
        if offset < 0 or offset > duration:
            continue
        body = msg.get("content") or ""
        comments.append({
            "content_offset_seconds": offset,
            "message": {"body": body},
            "commenter": {"name": (msg.get("sender") or {}).get("username", "")},
        })

    with open(out_chat_json, "w", encoding="utf-8") as f:
        json.dump({"comments": comments, "video": {"id": meta.get("live_stream_id")}},
                  f, ensure_ascii=False)
    print(f"Chat Kick: {len(comments)} mensajes en {out_chat_json}")
    return len(comments), out_chat_json


# ---------------------------------------------------------------------------
# Escaneo rápido de picos (el chat completo de un VOD largo es demasiado
# lento: 10h27m son ~7.525 ventanas, ~50 min. El usuario lo canceló por eso.)
# Fase 1: una ventana de 5 s cada scan_every_s (defecto 300 s) en todo el
# VOD (~2 min para 10h). Fase 2: chat denso (cada 5 s) solo en los
# ±region_half_s de las top_regions muestras con más mensajes. El chat.json
# resultante cubre SOLO esas regiones: los picos son reales, pero la
# mediana se calcula solo dentro de las regiones escaneadas.
#
# NOTA: esta función viene de viral-clip-agent/kick.py y ahora vive
# también en kickdownloaderCLI, para que el vendorizado no diverja.
# ---------------------------------------------------------------------------

def scan_chat_peaks(meta, out_chat_json, out_raw_ndjson=None,
                    workers=DEFAULT_WORKERS + 4, scan_every_s=300,
                    region_half_s=300, top_regions=5):
    """Escaneo rápido: devuelve (n_mensajes, ruta_chat_json).

    El chat.json que escribe cubre solo las regiones densas, no todo el
    VOD — ver el comentario de arriba.
    """
    channel_id = meta["channel_id"]
    start = meta["start_time"]
    duration = int(meta["duration_s"])
    if not channel_id or not start or duration <= 0:
        raise KickError("Faltan channel_id, start_time o duration para el escaneo.")

    # Fase 1: muestreo grueso en todo el VOD.
    sample_offsets = list(range(0, duration, scan_every_s))
    print(f"Escaneo rápido Kick: {len(sample_offsets)} muestras cada "
          f"{scan_every_s}s en {duration // 3600}h{(duration % 3600) // 60:02d}m...")
    counts = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch_chat_window, channel_id,
                          start + timedelta(seconds=t)): t for t in sample_offsets}
        for fut in as_completed(futs):
            try:
                counts[futs[fut]] = len(fut.result())
            except KickError:
                counts[futs[fut]] = 0
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    print("  Muestras con más chat (5 s): " +
          ", ".join(f"{t // 60}m={n}" for t, n in ranked[:top_regions]))

    # Fase 2: chat denso alrededor de las mejores regiones.
    dense = set()
    for center, _ in ranked[:top_regions]:
        lo = max(0, center - region_half_s)
        hi = min(duration, center + region_half_s)
        dense.update(range(lo, hi, CHAT_WINDOW_SECS))
    dense = sorted(dense)
    print(f"  Chat denso: {len(dense)} ventanas de {CHAT_WINDOW_SECS}s "
          f"alrededor de {top_regions} regiones...")
    os.makedirs(os.path.dirname(out_chat_json) or ".", exist_ok=True)
    messages, seen = [], set()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch_chat_window, channel_id,
                          start + timedelta(seconds=t)): t for t in dense}
        for fut in as_completed(futs):
            try:
                for msg in fut.result():
                    mid = msg.get("id")
                    if mid and mid in seen:
                        continue
                    if mid:
                        seen.add(mid)
                    messages.append(msg)
            except KickError:
                pass
    messages.sort(key=lambda m: m.get("created_at", ""))
    comments = []
    for msg in messages:
        try:
            offset = (parse_kick_datetime(msg.get("created_at", "")) - start).total_seconds()
        except KickError:
            continue
        if offset < 0 or offset > duration:
            continue
        comments.append({
            "content_offset_seconds": offset,
            "message": {"body": msg.get("content") or ""},
            "commenter": {"name": (msg.get("sender") or {}).get("username", "")},
        })
    with open(out_chat_json, "w", encoding="utf-8") as f:
        json.dump({"comments": comments, "video": {"id": meta.get("live_stream_id")},
                   "scan": {"mode": "fast", "scan_every_s": scan_every_s,
                            "region_half_s": region_half_s,
                            "top_regions": top_regions}},
                  f, ensure_ascii=False)
    if out_raw_ndjson:
        with open(out_raw_ndjson, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    print(f"Escaneo Kick: {len(comments)} mensajes en {out_chat_json} "
          f"(solo regiones densas, no el VOD entero)")
    return len(comments), out_chat_json


# ---------------------------------------------------------------------------
# Video: solo el segmento que hace falta (HLS)
# ---------------------------------------------------------------------------

def pick_variant(master_url: str, max_height: int = 720) -> str:
    """Elige la variante ≤720p del master.m3u8 (menos MB por segmento)."""
    text = http_text(master_url)
    variants = []  # (alto, url)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            m = re.search(r"RESOLUTION=\d+x(\d+)", line)
            if m and i + 1 < len(lines):
                variants.append((int(m.group(1)), lines[i + 1].strip()))
    if not variants:
        return master_url
    base = master_url.rsplit("/", 1)[0] + "/"
    fits = [v for v in variants if v[0] <= max_height]
    chosen = max(fits) if fits else min(variants)
    url = chosen[1]
    if not url.startswith("http"):
        url = urllib.parse.urljoin(base, url)
    return url


def download_segment(master_url: str, start_sec: float, duration: float,
                     out_path: str, max_height: int = 720,
                     ffmpeg_path: str = "ffmpeg") -> str:
    """Baja SOLO [start_sec, start_sec+duration] del VOD con ffmpeg.

    ffmpeg con -ss antes de -i salta los segmentos HLS anteriores sin
    descargarlos enteros: un segmento de 60 s a 720p son ~40 MB, no los
    ~10 GB del VOD completo.
    """
    if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
        print(f"Segmento ya descargado, se reutiliza: {out_path}")
        return out_path
    variant = pick_variant(master_url, max_height=max_height)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    # Re-encode (no -c copy): con copia el segmento empieza en el
    # keyframe anterior al inicio pedido (hasta un segmento HLS
    # antes) y el corte posterior, que asume que el segmento empieza
    # exactamente en start_sec, queda desfasado; peak_rel, hook y
    # subtítulos heredan ese desfase (ver auto_clips.extract_window,
    # medido 2026-11-17: -3.0 s con GOP de 5 s). El re-encode es un
    # pase extra por clip, del mismo orden que el corte final.
    cmd = [ffmpeg_path, "-v", "error", "-ss", str(int(start_sec)), "-i", variant,
           "-t", str(int(duration)),
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
           "-c:a", "aac", "-y", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(out_path):
        raise KickError(f"No se pudo bajar el segmento: {r.stderr.strip()[-500:]}")
    return out_path


# ---------------------------------------------------------------------------
# Puntuación local de texto (fallback de sample_audio_peaks)
# ---------------------------------------------------------------------------

_LAUGH_TOKENS = ("jaja", "jeje", "jiji", "jojo", "lol", "lmao", "rofl",
                 "kekw", "lul", "xd", "qué risa", "me muero", "me parto")
_HYPE_TOKENS = ("qué jugada", "brutal", "increíble", "insano", "tremendo",
                "épico", "vamos", "dale", "qué grande", "golazo",
                "pog", "clutch", "insane", "god", "lets go", "gg", "clip it", "wow")


def _score_message_local(text: str):
    """(puntos, es_risa, es_hype) aproximado, sin dependencias externas."""
    low = text.lower()
    laugh = any(t in low for t in _LAUGH_TOKENS)
    hype = any(t in low for t in _HYPE_TOKENS)
    points = 1 + (1 if laugh else 0) + (1 if hype else 0)
    return points, laugh, hype


# ---------------------------------------------------------------------------
# Fallback sin chat: picos por audio transcrito (muestreo)
# ---------------------------------------------------------------------------

def sample_audio_peaks(master_url: str, duration_s: float, out_dir: str,
                       sample_every_s: int = 900, sample_len_s: int = 30,
                       model: str = "tiny"):
    """Muestrea el audio del VOD cada sample_every_s, lo transcribe con
    faster-whisper (local, .venv) y puntúa la transcripción con las reglas
    de risas/hype. Devuelve [(inicio_s, puntos, texto)] ordenado por puntos.

    Es un MUESTREO (no cubre todo el VOD): los picos son reales pero
    aproximados. Solo se usa cuando el chat de Kick viene vacío.
    """
    # Puntuación local de risas/hype (aproximada, mismos tokens que
    # clips_chat_v2 de viral-clip-agent, sin depender de ese proyecto).
    score_message = _score_message_local

    # faster-whisper vive en .venv (el Python del sistema no lo tiene).
    # Vendorizado: el .venv está en la raíz del proyecto (padre de
    # kick_downloader/), no dentro del paquete; mirar en los dos sitios.
    _here = os.path.dirname(os.path.abspath(__file__))
    fw_python = sys.executable
    for _cand in (os.path.join(_here, ".venv", "bin", "python"),
                  os.path.join(_here, "..", ".venv", "bin", "python")):
        if os.path.isfile(_cand):
            fw_python = _cand
            break

    variant = pick_variant(master_url, max_height=360)
    os.makedirs(out_dir, exist_ok=True)
    results = []
    t = 0
    while t < duration_s:
        wav = os.path.join(out_dir, f"sample_{int(t)}.wav")
        cmd = ["ffmpeg", "-v", "error", "-ss", str(int(t)), "-i", variant,
               "-t", str(sample_len_s), "-vn", "-ac", "1", "-ar", "16000",
               "-y", wav]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.isfile(wav):
            t += sample_every_s
            continue
        # faster-whisper 1.2.1 + PyAV 19: pasarle la ruta del WAV falla
        # (av.open ya no acepta metadata_errors). Igual que subtitles.py:
        # leer el WAV con el módulo wave de la stdlib y pasar un array numpy.
        script = (
            "import json, sys, wave\n"
            "import numpy as np\n"
            "from faster_whisper import WhisperModel\n"
            "with wave.open(sys.argv[1], 'rb') as wf:\n"
            "    frames = wf.readframes(wf.getnframes())\n"
            "audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0\n"
            "m = WhisperModel(%r, device='cpu', compute_type='int8')\n"
            "segs, info = m.transcribe(audio, beam_size=1, vad_filter=True)\n"
            "print(json.dumps({'lang': info.language, 'text': ' '.join(s.text for s in segs)}, ensure_ascii=False))\n"
            % model
        )
        r = subprocess.run([fw_python, "-c", script, wav],
                           capture_output=True, text=True, timeout=300)
        text = ""
        if r.returncode == 0:
            try:
                text = json.loads(r.stdout.strip() or "{}").get("text", "")
            except json.JSONDecodeError:
                text = ""
        points, laugh, hype = score_message(text)
        # Puntuar por palabras: cada palabra de risa/hype suma.
        words = text.split()
        extra = sum(1 for w in words
                    if score_message(w)[1] or score_message(w)[2])
        total = points + extra
        results.append((int(t), total, text[:200]))
        print(f"  muestra {t // 60}m: {total} pts — {text[:100]!r}")
        try:
            os.remove(wav)
        except OSError:
            pass
        t += sample_every_s
    return sorted(results, key=lambda x: x[1], reverse=True)
