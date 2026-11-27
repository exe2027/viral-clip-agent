#!/usr/bin/env python3
"""Recopilación larga de YouTube (14 min) — SPEC v3 2026-11-26.

Un vídeo por tenant (sebastián y kaez), solo YouTube, solo español,
con los mejores momentos ya validados de la Cola y los ya publicados
(enmienda 14:24: los publicados entran aunque su `pasa` no sea 1 en
D1, porque ya pasaron las puertas al publicarse). Lo hace el programa,
nunca a mano (regla del usuario 2026-11-24).

Pipeline (spec §2-§5):
  1. Pre-chequeo del pool (metadata D1, tras dedupe, con la fórmula
     completa): si suma < 13:30, se PARA ese tenant y se informa.
  2. Pre-chequeo de credenciales YouTube (tokeninfo + scope
     youtube.upload) ANTES de descargar y renderizar.
  3. Descarga de cada MP4 por GET /api/clips/:id/video
     (PanelBridge.download_video). PROHIBIDO: /api/public-video,
     wrangler r2, API directa de R2.
  4. Ensamblado ffmpeg: cold open 12 s del #1, countdown de peor a
     mejor con etiqueta #N, revelación del #1 completo al final,
     xfade 0,3 s, audio loudnorm, H.264+AAC 1080x1920.
  5. Verificación ffprobe 13:30-14:30 del archivo final.
  6. Subida con la función NUEVA publish_longform (contrato propio).
     PROHIBIDO: YouTubeShortsPublisher.publish(), dry_run(),
     validate_package(), check_limits().

Punto de entrada:
    python -m clipagent.compilation --tenant <sebastian|kaez> [--dry-run]
    python -m clipagent.compilation --combinado [--dry-run]

Modo combinado (spec §2b, enmienda 14:40): pool de ambas colas
juntas, dedupe combinado y reparto alternado en DOS vídeos
disjuntos (A -> canal ES de seba, B -> canal ES de kaez). Cada
vídeo tiene su propio pre-chequeo, su propio render y su propio
informe en runs/compilation-2026-11-26/combinado-a|b/.

La recopilación NO aparece en la Cola/tracking del panel: el informe
JSON en runs/compilation-2026-11-26/<tenant>/report.json es el único
registro. La guarda de re-ejecución impide subir dos veces sin
--force.
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- constantes
_MESES_ES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago",
             "sep", "oct", "nov", "dic")
SANTIAGO_TZ = ZoneInfo("America/Santiago")


def compilation_title(fecha=None):
    """Título de la recopilación del día (spec CircleCI §4.6, B4).

    Se calcula en cada corrida desde la fecha del día en
    America/Santiago: "Los momentos más divertidos de GTA 6 |
    27 nov" (día + mes abreviado en español, minúscula).
    `fecha` es un date (tests); sin él, hoy en Santiago.
    """
    if fecha is None:
        fecha = datetime.now(SANTIAGO_TZ).date()
    elif isinstance(fecha, datetime):
        fecha = fecha.date()
    return (f"Los momentos más divertidos de GTA 6 | "
            f"{fecha.day} {_MESES_ES[fecha.month - 1]}")
TAGS = ["GTA 6", "GTA VI", "momentos divertidos", "recopilación",
        "gameplay español", "clips GTA 6", "humor gaming",
        "Rockstar Games", "clips gaming", "stream clips"]
CATEGORY_ID = "20"  # Gaming
LANGUAGE = "es"
PRIVACY = "public"
YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.upload"

MIN_TOTAL_S = 810.0    # 13:30 — mínimo del archivo final (ffprobe)
TARGET_TOTAL_S = 840.0  # 14:00 — objetivo del corte
HARD_TOTAL_S = 870.0   # 14:30 — tope duro del archivo final (ffprobe)
TEASER_S = 12.0        # cold open: primeros 12 s del clip #1
XFADE_S = 0.3          # transición entre segmentos
LABEL_FADE_S = 0.5     # fade in/out de la etiqueta #N

MAX_TITLE_CHARS = 60
MAX_DESC_CHARS = 5000
MAX_TAGS = 15

RUNS_DIRNAME = os.path.join("runs", "compilation-2026-11-26")
TENANTS = ("sebastian", "kaez")
# §2b: vídeo A -> canal ES de seba, vídeo B -> canal ES de kaez.
VIDEO_A_TENANT = "sebastian"
VIDEO_B_TENANT = "kaez"

POOL_QUERY = (
    "SELECT id, title, score, status, language, video_r2_key, "
    "publish_blocked, archived, start_sec, end_sec, influencer_id, "
    "source_id, source_key, created_at, viral FROM clips "
    "WHERE tenant_id=? AND status IN ('en_carpeta','publicado') "
    "AND language='es' "
    "AND archived=0 AND publish_blocked=0 AND video_r2_key<>''"
)


class CompilationError(Exception):
    """Error del pipeline de recopilación (mensaje listo para informar)."""


class PoolInsufficientError(CompilationError):
    """El pool filtrado no llega a 13:30: no se construye nada."""


# ------------------------------------------------------------------ pool
def parse_viral(clip):
    """El campo viral llega como dict (API) o como string JSON (D1)."""
    viral = clip.get("viral")
    if isinstance(viral, dict):
        return viral
    if isinstance(viral, str) and viral.strip():
        try:
            parsed = json.loads(viral)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def quality_passes(clip):
    """Veredicto de calidad válido: viral.quality_gate.pasa es True."""
    gate = parse_viral(clip).get("quality_gate") or {}
    return gate.get("pasa") is True


def clip_duration(clip):
    """Duración por metadata: end_sec - start_sec (segundos)."""
    try:
        return float(clip["end_sec"]) - float(clip["start_sec"])
    except (KeyError, TypeError, ValueError):
        return None


def filter_pool(clips):
    """Filtro duro (spec §2, enmienda 14:24). Devuelve (aptos,
    rechazados).

    Cada rechazo es (clip, motivo). El quality_gate se evalúa en
    Python porque vive dentro del JSON de `viral`. Los borradores
    ('en_carpeta') exigen veredicto de calidad válido; los ya
    publicados ('publicado') se aceptan aunque su `pasa` no sea 1
    en D1 (ya pasaron las puertas al publicarse: el informe anota
    cuántos entraron por esta vía).
    """
    kept, rejected = [], []
    for clip in clips:
        if clip.get("status") not in ("en_carpeta", "publicado"):
            rejected.append((clip, "status fuera de "
                             "('en_carpeta','publicado')"))
        elif clip.get("status") == "en_carpeta" \
                and not quality_passes(clip):
            rejected.append((clip, "sin veredicto de calidad válido"))
        elif not (clip.get("video_r2_key") or "").strip():
            rejected.append((clip, "sin video_r2_key"))
        elif clip.get("publish_blocked"):
            rejected.append((clip, "publish_blocked"))
        elif clip.get("archived"):
            rejected.append((clip, "archived"))
        elif (clip.get("language") or "") != "es":
            rejected.append((clip, "language != es"))
        elif clip_duration(clip) is None or clip_duration(clip) <= 0:
            rejected.append((clip, "sin duración válida"))
        else:
            kept.append(clip)
    return kept, rejected


def sort_pool(clips):
    """score DESC, desempate por created_at más reciente."""
    return sorted(
        clips,
        key=lambda c: (c.get("score") or 0, c.get("created_at") or 0),
        reverse=True,
    )


def windows_overlap(a_start, a_end, b_start, b_end):
    """Dos ventanas se solapan si se cruzan (extremos excluidos)."""
    return a_start < b_end and b_start < a_end


def normalize_title(title):
    """Título normalizado para el dedupe de reserva: minúsculas,
    sin puntuación, espacios colapsados."""
    text = (title or "").lower()
    text = re.sub(r"[^a-z0-9áéíóúñ ]", "", text)
    return " ".join(text.split())


def _dedupe_key(clip, use_influencer):
    if use_influencer:
        return ("inf", clip.get("influencer_id"))
    return ("titulo", normalize_title(clip.get("title")))


def dedupe_pool(clips):
    """El mismo momento no entra dos veces (spec §2, v3).

    Clave (influencer_id + ventana solapada). Si influencer_id no
    está poblado en el pool, clave de reserva (título normalizado +
    ventana solapada). PROHIBIDO dedupar por (título + duración)
    sola y PROHIBIDO usar source_id/source_key (valen 0/'' en todo
    el pool real). Devuelve (conservados, descartados).
    """
    use_influencer = all(
        clip.get("influencer_id") not in (None, "", 0) for clip in clips
    ) if clips else True
    kept, dropped = [], []
    for clip in sort_pool(clips):
        key = _dedupe_key(clip, use_influencer)
        duplicate = False
        for other in kept:
            if _dedupe_key(other, use_influencer) != key:
                continue
            if windows_overlap(clip["start_sec"], clip["end_sec"],
                               other["start_sec"], other["end_sec"]):
                duplicate = True
                break
        if duplicate:
            dropped.append(clip)
        else:
            kept.append(clip)
    return kept, dropped


def estimate_total(durations):
    """Fórmula completa (spec §2, v3).

    total = Σ duraciones + 12 s teaser − 0,3 s × nº transiciones.
    Los segmentos son teaser + N clips = N+1, así que las
    transiciones son N (una por clip seleccionado).
    """
    durations = list(durations)
    return sum(durations) + TEASER_S - XFADE_S * len(durations)


def select_clips(pool):
    """Corte (spec §2). Añade clips en orden (score DESC) mientras
    el total estimado < 13:30; si el siguiente empuja el total por
    encima de 14:00, intenta sustituirlo por el siguiente más corto
    que quepa; si no hay sustituto, para. Tope duro 14:30.

    Devuelve (seleccionados, total_estimado_s). Si el total queda
    < 13:30, el llamador debe parar el tenant (pool insuficiente).
    """
    ordered = sort_pool(pool)
    selected, total = [], 0.0
    i = 0
    while i < len(ordered):
        if estimate_total([clip_duration(c) for c in selected]) >= MIN_TOTAL_S:
            break
        clip = ordered[i]
        dur = clip_duration(clip)
        trial = estimate_total(
            [clip_duration(c) for c in selected] + [dur])
        if trial <= TARGET_TOTAL_S:
            selected.append(clip)
            total = trial
            i += 1
            continue
        # Por encima de 14:00: buscar el siguiente más corto que
        # quepa en 14:00.
        substitute = None
        for j in range(i + 1, len(ordered)):
            dur_j = clip_duration(ordered[j])
            if estimate_total(
                    [clip_duration(c) for c in selected] + [dur_j]
                    ) <= TARGET_TOTAL_S:
                substitute = j
                break
        if substitute is not None:
            selected.append(ordered.pop(substitute))
            total = estimate_total([clip_duration(c) for c in selected])
            i += 1
            continue
        # Sin sustituto que quepa en 14:00: si el clip original cabe
        # en el tope duro de 14:30 (banda de verificación §7), se
        # añade igual — parar aquí dejaría el total bajo 13:30
        # (con clips de 40 s, 20 dan 806 s y 21 dan 845,7 s: no hay
        # valor intermedio).
        if trial <= HARD_TOTAL_S:
            selected.append(clip)
            total = trial
            i += 1
            continue
        # Ni el original cabe en 14:30: último intento, un sustituto
        # que quepa en 14:30; si no hay, se para.
        substitute = None
        for j in range(i + 1, len(ordered)):
            dur_j = clip_duration(ordered[j])
            if estimate_total(
                    [clip_duration(c) for c in selected] + [dur_j]
                    ) <= HARD_TOTAL_S:
                substitute = j
                break
        if substitute is None:
            break
        selected.append(ordered.pop(substitute))
        total = estimate_total([clip_duration(c) for c in selected])
        i += 1
    return selected, total


def split_alternating(pool):
    """Reparto alternado (§2b): orden global por score DESC; vídeo
    A = rangos impares (1, 3, 5…), vídeo B = rangos pares
    (2, 4, 6…). Devuelve (A, B). Ambos mezclan ambas colas y
    quedan disjuntos por construcción: ningún clip va a los dos.
    """
    ordered = sort_pool(pool)
    return ordered[0::2], ordered[1::2]


def clip_filename(clip):
    """Nombre del MP4 descargado: con el tenant de origen cuando
    lo hay (el clip 8 de seba y el clip 8 de kaez son distintos).
    """
    tenant = clip.get("tenant_origen")
    if tenant:
        return f"{tenant}_{clip['id']}.mp4"
    return f"{clip['id']}.mp4"


# -------------------------------------------------------------- segmentos
def build_segments(selected):
    """Estructura viral (spec §3).

    Devuelve la lista de segmentos en orden de reproducción:
      - teaser: primeros 12 s del clip #1 (sin etiqueta).
      - countdown: el resto, de peor a mejor por score, con etiqueta
        `#N · <título>` donde #1 es el mejor (la revelación) y el
        peor mostrado lleva #N.
      - reveal: el clip #1 completo al final.
    Cada segmento: {kind, clip, duration, label, chapter}.
    """
    if not selected:
        return []
    ordered = sort_pool(selected)
    best = ordered[0]
    rest = ordered[1:]
    n = len(ordered)
    segments = [{
        "kind": "teaser",
        "clip": best,
        "duration": min(TEASER_S, clip_duration(best)),
        "label": None,
        "chapter": "El mejor momento",
    }]
    # Countdown de peor a mejor: el primero mostrado es el peor
    # (etiqueta #N) y el último antes de la revelación es el #2.
    countdown = list(reversed(rest))
    for pos, clip in enumerate(countdown):
        rank = n - pos
        segments.append({
            "kind": "countdown",
            "clip": clip,
            "duration": clip_duration(clip),
            "label": f"#{rank} · {clip.get('title') or ''}",
            "chapter": f"#{rank} · {clip.get('title') or ''}",
        })
    segments.append({
        "kind": "reveal",
        "clip": best,
        "duration": clip_duration(best),
        "label": None,
        "chapter": f"#1 · {best.get('title') or ''}",
    })
    return segments


def format_timestamp(seconds):
    """M:SS o H:MM:SS para los capítulos de YouTube."""
    seconds = max(0, int(math.floor(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def chapter_timestamps(segments):
    """Timestamps de capítulo con el solape del xfade aplicado:
    cada segmento empieza 0,3 s antes de que termine el anterior.
    El teaser dura 12 s, así que el capítulo 2 empieza en 11,7 s
    (>= 10 s, mínimo de YouTube)."""
    out = []
    start = 0.0
    for idx, seg in enumerate(segments):
        out.append((start, seg["chapter"]))
        start += seg["duration"]
        if idx < len(segments) - 1:
            start -= XFADE_S
    return out


def build_description(segments):
    """Descripción con capítulos (spec §4): texto corto + timestamps
    generados de la lista final (>= 3 capítulos, primero 0:00)."""
    lines = [
        "Los momentos más divertidos de GTA 6 en una sola recopilación: "
        "risas, fails y reacciones de los directos.",
        "",
        "Capítulos:",
    ]
    for seconds, title in chapter_timestamps(segments):
        lines.append(f"{format_timestamp(seconds)} {title}")
    lines += [
        "",
        "Todos los clips son de directos de GTA 6.",
    ]
    return "\n".join(lines)


# -------------------------------------------------------------- validación
def validate_longform_inputs(video_path, title, description, tags,
                             token_path=None):
    """Contrato propio de publish_longform (spec §5), sin tocar el
    validador de Shorts. Devuelve [(ok, detalle), ...]."""
    checks = []
    if not video_path or not os.path.isfile(video_path):
        checks.append((False, f"archivo no existe: {video_path}"))
    else:
        try:
            duration = probe_duration(video_path)
            checks.append((duration is not None and duration > 0,
                           f"ffprobe lee el archivo "
                           f"({duration:.1f} s)" if duration else
                           "ffprobe no lee el archivo"))
        except CompilationError as e:
            checks.append((False, f"ffprobe falla: {e}"))
    checks.append((len(title or "") <= MAX_TITLE_CHARS,
                   f"título {len(title or '')} caracteres "
                   f"(máx. {MAX_TITLE_CHARS})"))
    checks.append((len(description or "") <= MAX_DESC_CHARS,
                   f"descripción {len(description or '')} caracteres "
                   f"(máx. {MAX_DESC_CHARS})"))
    checks.append((len(tags or []) <= MAX_TAGS,
                   f"{len(tags or [])} tags (máx. {MAX_TAGS})"))
    if token_path is None:
        from . import credentials as _creds
        token_path = _creds.token_path("youtube_shorts")
    token_ok = False
    if os.path.isfile(token_path):
        try:
            with open(token_path, encoding="utf-8") as f:
                doc = json.load(f)
            token_ok = bool(doc.get("refresh_token"))
        except (ValueError, OSError):
            token_ok = False
    checks.append((token_ok,
                   "token OAuth presente en tokens/youtube_shorts.json"
                   if token_ok else
                   "falta token OAuth (tokens/youtube_shorts.json)"))
    return checks


# ------------------------------------------------------------------- ffprobe
def probe_duration(path):
    """Duración real de un MP4 por ffprobe (segundos)."""
    cmd = ["ffprobe", "-v", "error", "-show_entries",
           "format=duration", "-of",
           "default=noprint_wrappers=1:nokey=1", path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CompilationError(f"ffprobe no corre sobre {path}: {e}")
    if out.returncode != 0:
        raise CompilationError(
            f"ffprobe falla sobre {path}: {out.stderr.strip()[:200]}")
    try:
        return float(out.stdout.strip())
    except ValueError:
        raise CompilationError(
            f"ffprobe no devuelve duración para {path}")


def probe_streams(path):
    """(tiene_vídeo, tiene_audio) por ffprobe."""
    cmd = ["ffprobe", "-v", "error", "-show_entries",
           "stream=codec_type", "-of", "csv=p=0", path]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    kinds = {line.strip() for line in out.stdout.splitlines()}
    return ("video" in kinds, "audio" in kinds)


# ------------------------------------------------------- credenciales YT
def check_youtube_credentials(token_path=None):
    """Pre-chequeo ANTES de descargar y renderizar (spec §5):
    refresca el token OAuth y verifica por tokeninfo que el scope
    youtube.upload está concedido. Devuelve (ok, detalle)."""
    if token_path is None:
        from . import credentials as _creds
        token_path = _creds.token_path("youtube_shorts")
    if not os.path.isfile(token_path):
        return False, ("sin token OAuth en "
                       f"{token_path} (materializar con "
                       "clipagent.panel_credentials primero)")
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        return False, ("falta google-api-python-client: no se puede "
                       "verificar el token")
    try:
        creds = Credentials.from_authorized_user_file(
            token_path, [YOUTUBE_SCOPE])
        creds.refresh(Request())
    except Exception as e:  # noqa: BLE001 - el motivo va al informe
        return False, f"el token OAuth no refresca: {e}"
    if not creds.token:
        return False, "el token OAuth no entregó access_token"
    url = ("https://oauth2.googleapis.com/tokeninfo?access_token="
           + urllib.parse.quote(creds.token))
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            info = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError) as e:
        return False, f"tokeninfo no responde: {e}"
    scopes = (info.get("scope") or "").split()
    if YOUTUBE_SCOPE not in scopes:
        return False, ("tokeninfo sin scope youtube.upload "
                       f"(scopes: {info.get('scope') or 'ninguno'})")
    return True, ("token OAuth válido, scope youtube.upload concedido "
                  f"(expira en {info.get('expires_in')} s)")


def token_path_for_tenant(tenant, base_dir=None):
    """Token OAuth propio del tenant (modo combinado, §2b).

    Devuelve la ruta tokens/youtube_shorts_<tenant>.json si el
    archivo existe, si no None. El archivo compartido
    tokens/youtube_shorts.json NO se usa en modo combinado: lo
    materializa panel_credentials para el tenant del entorno y no
    identifica a qué canal pertenece; subir con él el vídeo del
    otro tenant violaría la prohibición de §2b (cada vídeo solo
    a su tenant).
    """
    from . import credentials as _creds
    path = os.path.join(_creds.tokens_dir(base_dir),
                        f"youtube_shorts_{tenant}.json")
    return path if os.path.isfile(path) else None


# ------------------------------------------------------------- publicación
def _build_youtube_service(token_path):
    """Un servicio YouTube v3 desde el token OAuth del tenant.

    Se construye UNA sola vez por tenant y se comparte entre la
    guarda anti-duplicado y la subida (M11): no se autentica dos
    veces.
    """
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(
        token_path, [YOUTUBE_SCOPE])
    return build("youtube", "v3", credentials=creds)


def ya_subido_hoy(youtube_service, title):
    """Guarda anti-duplicado (spec CircleCI §4.6, B3): YouTube
    como fuente de verdad.

    Lista los vídeos del canal de destino (uploads playlist del
    canal, maxResults=50 por página) paginando hasta encontrar
    un vídeo anterior a hoy. Si alguno tiene el MISMO título
    (el efectivamente usado en esta corrida) y fue subido HOY
    (America/Santiago, no UTC), devuelve {"video_id", "url"} con
    el vídeo existente; si no, None. La playlist viene de más
    reciente a más antiguo, así que al ver un vídeo anterior a
    hoy ya no puede haber otro de hoy después.
    """
    today = datetime.now(SANTIAGO_TZ).date()
    channels = youtube_service.channels().list(
        part="contentDetails", mine=True).execute()
    items = (channels or {}).get("items") or []
    if not items:
        return None
    uploads = ((items[0].get("contentDetails") or {})
               .get("relatedPlaylists") or {}).get("uploads")
    if not uploads:
        return None
    page_token = None
    while True:
        resp = youtube_service.playlistItems().list(
            part="snippet", playlistId=uploads, maxResults=50,
            pageToken=page_token).execute()
        for item in (resp or {}).get("items") or []:
            snippet = item.get("snippet") or {}
            published = snippet.get("publishedAt") or ""
            try:
                when = datetime.fromisoformat(
                    published.replace("Z", "+00:00"))
            except ValueError:
                continue
            if when.tzinfo is None:
                continue
            pub_date = when.astimezone(SANTIAGO_TZ).date()
            if pub_date < today:
                return None
            if pub_date == today and snippet.get("title") == title:
                video_id = ((snippet.get("resourceId") or {})
                            .get("videoId"))
                if video_id:
                    return {
                        "video_id": video_id,
                        "url": ("https://www.youtube.com/watch?v="
                                f"{video_id}"),
                    }
        page_token = (resp or {}).get("nextPageToken")
        if not page_token:
            return None


def publish_longform(video_path, title, description, tags,
                     privacy="public", token_path=None,
                     youtube_service=None):
    """Subida NUEVA de vídeo largo (spec §5).

    Contrato propio (ver validate_longform_inputs); reutiliza solo
    Credentials.from_authorized_user_file, build("youtube", "v3"),
    MediaFileUpload(resumable=True) y videos().insert. PROHIBIDO
    usar YouTubeShortsPublisher.publish(), dry_run(),
    validate_package() y check_limits() — este módulo no los
    importa ni los llama. Devuelve {"video_id", "url"} con URL
    https://www.youtube.com/watch?v={id}.
    """
    checks = validate_longform_inputs(video_path, title, description,
                                      tags, token_path)
    failed = [detail for ok, detail in checks if not ok]
    if failed:
        raise CompilationError(
            "publish_longform: contrato no pasa: " + "; ".join(failed))
    if youtube_service is None:
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
        except ImportError:
            raise CompilationError(
                "falta google-api-python-client: pip install "
                "google-api-python-client google-auth-oauthlib")
        if token_path is None:
            from . import credentials as _creds
            token_path = _creds.token_path("youtube_shorts")
        creds = Credentials.from_authorized_user_file(
            token_path, [YOUTUBE_SCOPE])
        youtube = build("youtube", "v3", credentials=creds)
    else:
        from googleapiclient.http import MediaFileUpload
        youtube = youtube_service
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": list(tags or [])[:MAX_TAGS],
            "categoryId": CATEGORY_ID,
            "defaultLanguage": LANGUAGE,
        },
        "status": {"privacyStatus": privacy,
                   "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(video_path, mimetype="video/mp4",
                            resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body,
                                      media_body=media)
    response = request.execute()
    video_id = response.get("id")
    if not video_id:
        raise CompilationError(
            f"videos.insert sin id: {str(response)[:200]}")
    return {"video_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}"}


# ------------------------------------------------------------- ensamblado
def find_font():
    """Fuente para drawtext (etiquetas #N). DejaVu Sans si existe."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _segment_filter(seg, fontfile, label_path):
    """Filtros de normalización de un segmento: escala 1080x1920,
    30 fps, etiqueta #N con fade in/out 0,5 s (si la tiene),
    loudnorm. Devuelve (video_filter, audio_filter, trim)."""
    trim = ""
    if seg["kind"] == "teaser":
        trim = f"trim=0:{seg['duration']:.3f},setpts=PTS-STARTPTS,"
    video = (f"{trim}scale=1080:1920:force_original_aspect_ratio="
             "increase,crop=1080:1920,fps=30")
    if seg.get("label") and fontfile and label_path:
        # La etiqueta hace fade in/out 0,5 s con la expresión alpha
        # de drawtext (sin filtro fade: no exige canal alfa).
        dur = seg["duration"]
        alpha = (f"if(lt(t,{LABEL_FADE_S}),t/{LABEL_FADE_S},"
                 f"if(lt(t,{max(0.0, dur - LABEL_FADE_S):.3f}),1,"
                 f"({dur:.3f}-t)/{LABEL_FADE_S}))")
        video += (f",drawtext=fontfile='{fontfile}':"
                  f"textfile='{label_path}':fontsize=64:"
                  "fontcolor=white:borderw=3:bordercolor=black@0.8:"
                  "box=1:boxcolor=black@0.6:boxborderw=20:"
                  f"x=(w-text_w)/2:y=h-260:alpha='{alpha}'")
    video += ",format=yuv420p"
    audio = "aresample=48000,loudnorm=I=-16:TP=-1.5:LRA=11"
    return video, audio


def _run(cmd, what):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=3600)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CompilationError(f"ffmpeg no corre ({what}): {e}")
    if proc.returncode != 0:
        raise CompilationError(
            f"ffmpeg falla ({what}): {proc.stderr.strip()[-500:]}")
    return proc


def render_compilation(segments, clips_dir, out_path, work_dir,
                       ffmpeg="ffmpeg", on_progress=None):
    """Ensambla la recopilación (spec §3) con ffmpeg.

    Fase 1: normaliza cada segmento a un intermedio (H.264+AAC
    1080x1920, 30 fps, etiqueta #N, loudnorm). Fase 2: encadena
    los intermedios con xfade 0,3 s (vídeo) y acrossfade 0,3 s
    (audio). Devuelve out_path. `on_progress` (opcional) recibe
    una línea por segmento y por fase, para que el driver de
    CircleCI no quede en silencio durante el render (B1).
    """
    os.makedirs(work_dir, exist_ok=True)
    fontfile = find_font()
    intermediates = []
    for idx, seg in enumerate(segments):
        if on_progress is not None:
            on_progress(f"render: segmento {idx + 1}/{len(segments)} "
                        f"(clip {seg['clip']['id']})")
        clip = seg["clip"]
        src = seg["clip"].get("_local_path") or os.path.join(
            clips_dir, f"{clip['id']}.mp4")
        if not os.path.isfile(src):
            raise CompilationError(
                f"falta el MP4 del clip {clip['id']}: {src}")
        inter = os.path.join(work_dir, f"seg_{idx:02d}.mp4")
        label_path = None
        if seg.get("label"):
            label_path = os.path.join(work_dir, f"label_{idx:02d}.txt")
            with open(label_path, "w", encoding="utf-8") as f:
                f.write(seg["label"])
        video_f, audio_f = _segment_filter(seg, fontfile, label_path)
        has_video, has_audio = probe_streams(src)
        if not has_video:
            raise CompilationError(
                f"el clip {clip['id']} no tiene pista de vídeo")
        cmd = [ffmpeg, "-y", "-v", "error", "-i", src]
        if not has_audio:
            cmd += ["-f", "lavfi", "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000"]
            audio_src = "[1:a]"
        else:
            audio_src = "[0:a]"
        cmd += ["-filter_complex",
                f"[0:v]{video_f}[v];{audio_src}{audio_f}[a]",
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                inter]
        _run(cmd, f"normalizar segmento {idx} (clip {clip['id']})")
        intermediates.append(inter)
    if len(intermediates) == 1:
        os.replace(intermediates[0], out_path)
        if on_progress is not None:
            on_progress("render: un solo segmento, sin xfade")
        return out_path
    if on_progress is not None:
        on_progress(f"render: xfade de {len(intermediates)} segmentos")
    # Fase 2: xfade en cadena. Offset del xfade i (une el segmento
    # i+1): Σ duraciones 0..i − 0,3×(i+1).
    durations = [seg["duration"] for seg in segments]
    inputs = []
    for inter in intermediates:
        inputs += ["-i", inter]
    graph = []
    v_prev, a_prev = "0:v", "0:a"
    offset = durations[0] - XFADE_S
    for i in range(1, len(intermediates)):
        v_out = f"vx{i}"
        a_out = f"ax{i}"
        last = i == len(intermediates) - 1
        graph.append(
            f"[{v_prev}][{i}:v]xfade=transition=fade:"
            f"duration={XFADE_S}:offset={offset:.3f}"
            f"[{'vout' if last else v_out}]")
        graph.append(
            f"[{a_prev}][{i}:a]acrossfade=d={XFADE_S}:c1=tri:c2=tri"
            f"[{'aout' if last else a_out}]")
        v_prev, a_prev = v_out, a_out
        offset += durations[i] - XFADE_S
    cmd = ([ffmpeg, "-y", "-v", "error"] + inputs
           + ["-filter_complex", ";".join(graph),
              "-map", "[vout]", "-map", "[aout]",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
              "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
              out_path])
    _run(cmd, "xfade de la recopilación")
    return out_path


# ------------------------------------------------------------- carga pool
def load_pool_d1(tenant):
    """Pool por D1 SOLO LECTURA (fallback cuando el bridge no
    alcanza): ejecuta el SELECT por el wrapper wrangler durable
    del tenant (~/workspace/gta6-clips/wr_seba.py / wr_kaez.py).
    Nunca escribe en D1."""
    repo_root = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    repo_parent = os.path.dirname(repo_root)
    wrappers = {"sebastian": ("wr_seba.py", "clips-avc", "sebastian"),
                "kaez": ("wr_kaez.py", "clips-avc-kaez", "kaez")}
    script, db, tenant_id = wrappers[tenant]
    wrapper = os.path.join(repo_parent, script)
    if not os.path.isfile(wrapper):
        raise CompilationError(
            f"sin wrapper D1 para {tenant}: falta {wrapper}")
    cmd = [sys.executable, wrapper, "d1", "execute", db, "--remote",
           "--command", POOL_QUERY.replace("?", f"'{tenant_id}'", 1)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CompilationError(f"D1 no responde ({tenant}): {e}")
    if proc.returncode != 0:
        raise CompilationError(
            f"D1 falla ({tenant}): {proc.stderr.strip()[-300:]}")
    text = proc.stdout
    start = text.find("[")
    if start < 0:
        raise CompilationError(
            f"D1 devuelve salida sin JSON ({tenant})")
    try:
        payload = json.loads(text[start:])
    except ValueError as e:
        raise CompilationError(f"D1 devuelve JSON roto ({tenant}): {e}")
    return payload[0]["results"]


def load_pool(tenant, bridge=None, pool=None):
    """Pool de la Cola: bridge (GET /api/clips) si hay sesión, si
    no D1 solo lectura. `pool` inyecta filas (tests / --pool-json)."""
    if pool is not None:
        return pool
    if bridge is not None:
        return bridge.list_clips()
    return load_pool_d1(tenant)


# -------------------------------------------------------------- descarga
def _download_with_substitution(selected, reserve, clips_dir, bridges,
                                on_progress=None):
    """Descarga los MP4 por el bridge (spec §3) con sustitución
    por fallo: si un clip falla, se descarta y se toma el
    siguiente por score de la reserva que quepa en 14:00.

    `on_progress` (opcional) recibe una línea por descarga, para
    que el driver de CircleCI no quede en silencio (B1).

    `bridges` es dict tenant -> PanelBridge: un vídeo combinado
    mezcla clips de ambos tenants y cada MP4 se baja del Worker
    de SU tenant (el clip conserva `tenant_origen`). Si falta el
    bridge del tenant del clip, levanta CompilationError. Si el
    clip no trae `tenant_origen` (modo un tenant) y hay un solo
    bridge, se usa ese. Devuelve (selected_final, downloaded,
    descartados_descarga); `downloaded` se indexa por
    (tenant_origen, id) y cada clip descargado lleva `_local_path`.
    """
    downloaded = {}
    descartados_descarga = []
    pending = list(selected)
    current_selected = list(selected)
    while pending:
        clip = pending.pop(0)
        tenant = clip.get("tenant_origen")
        bridge = bridges.get(tenant) if tenant else None
        if bridge is None and len(bridges) == 1:
            bridge = next(iter(bridges.values()))
        key = (tenant, clip["id"])
        dest = os.path.join(clips_dir, clip_filename(clip))
        if on_progress is not None:
            on_progress(f"descargando clip {clip['id']} "
                        f"({tenant or 'sin tenant'})")
        if bridge is None:
            raise CompilationError(
                f"sin sesión del panel ({tenant or 'sin tenant'}): "
                "la descarga exige bridge (PANEL_BRAIN_TOKEN o "
                "PANEL_USERNAME/PANEL_PASSWORD); D1 solo mide el "
                "pool, no descarga")
        try:
            bridge.download_video(clip["id"], dest)
            clip["_local_path"] = dest
            downloaded[key] = dest
        except Exception as e:  # noqa: BLE001 - motivo al informe
            descartados_descarga.append(
                {"id": clip["id"], "tenant_origen": tenant,
                 "motivo": str(e)[:200]})
            current = [c for c in current_selected
                       if (c.get("tenant_origen"), c["id"])
                       in downloaded]
            current_total = estimate_total(
                [clip_duration(c) for c in current])
            current_selected = current
            if current_total >= MIN_TOTAL_S:
                # Lo descargado ya llega a 13:30: no hace falta
                # sustituto, se sigue con lo que hay.
                continue
            substitute = None
            for cand in reserve:
                trial_total = estimate_total(
                    [clip_duration(c) for c in current]
                    + [clip_duration(cand)])
                if trial_total <= TARGET_TOTAL_S:
                    substitute = cand
                    break
            if substitute is None:
                raise CompilationError(
                    f"descarga del clip {clip['id']} falló y no hay "
                    "sustituto que quepa en 14:00; no se sigue sin "
                    "material suficiente")
            reserve.remove(substitute)
            current_selected = current + [substitute]
            pending.append(substitute)
    final = sort_pool([c for c in current_selected
                       if (c.get("tenant_origen"), c["id"])
                       in downloaded])
    return final, downloaded, descartados_descarga


def _precheck_dict(tenant, pool_base, aptos, rechazados, kept,
                   descartados_dedupe, selected, total_est):
    """Pre-chequeo (spec §2): mismas claves en ambos modos."""
    return {
        "tenant": tenant,
        "pool_base": pool_base,
        "aptos": len(aptos),
        "borradores_aptos": len(
            [c for c in aptos if c.get("status") == "en_carpeta"]),
        "publicados_aptos": len(
            [c for c in aptos if c.get("status") == "publicado"]),
        "rechazados": len(rechazados),
        "tras_dedupe": len(kept),
        "descartados_dedupe": [c["id"] for c in descartados_dedupe],
        "seleccionados": len(selected),
        "publicados_en_seleccion": len(
            [c for c in selected if c.get("status") == "publicado"]),
        "suma_metadata_s": round(
            sum(clip_duration(c) for c in kept), 1),
        "total_estimado_s": round(total_est, 1),
        "minimo_s": MIN_TOTAL_S,
        "pasa": total_est >= MIN_TOTAL_S,
    }


# -------------------------------------------------------------- orquesta
def _report_path(base_dir, tenant):
    return os.path.join(base_dir, RUNS_DIRNAME, tenant, "report.json")


def _precheck_path(base_dir, tenant):
    return os.path.join(base_dir, RUNS_DIRNAME, tenant, "precheck.json")


def run_compilation(tenant, dry_run=False, force=False, base_dir=None,
                    bridge=None, pool=None, work_dir=None):
    """Una corrida por tenant. Devuelve el informe (dict).

    Orden: guarda de re-ejecución -> pool -> pre-chequeo (PARA si
    < 13:30) -> credenciales -> descarga (con sustitución por
    fallo) -> ffprobe real -> render -> verificación ffprobe ->
    subida (salvo --dry-run) -> informe.
    """
    if tenant not in TENANTS:
        raise CompilationError(
            f"tenant inválido: {tenant!r} (sebastian|kaez)")
    base = base_dir or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    runs_tenant = os.path.join(base, RUNS_DIRNAME, tenant)
    os.makedirs(runs_tenant, exist_ok=True)
    report_path = _report_path(base, tenant)
    if os.path.isfile(report_path) and not force:
        with open(report_path, encoding="utf-8") as f:
            existing = json.load(f)
        if existing.get("video_id"):
            raise CompilationError(
                f"ya existe una recopilación subida para {tenant} "
                f"({existing['video_id']}); usa --force para subir "
                "otra (no se suben 2 el mismo día al mismo canal)")
    work = work_dir or os.path.join(runs_tenant, "work")
    clips_dir = os.path.join(work, "clips")
    os.makedirs(clips_dir, exist_ok=True)

    # 1. Pool y pre-chequeo (spec §2): con metadata D1, tras dedupe.
    raw_pool = load_pool(tenant, bridge=bridge, pool=pool)
    aptos, rechazados = filter_pool(raw_pool)
    kept, descartados_dedupe = dedupe_pool(aptos)
    selected, total_est = select_clips(kept)
    precheck = _precheck_dict(tenant, len(raw_pool), aptos, rechazados,
                              kept, descartados_dedupe, selected,
                              total_est)
    with open(_precheck_path(base, tenant), "w",
              encoding="utf-8") as f:
        json.dump(precheck, f, indent=2, ensure_ascii=False)
    if not precheck["pasa"]:
        raise PoolInsufficientError(
            f"pool insuficiente ({tenant}): {len(kept)} clips tras "
            f"dedupe, Σ={precheck['suma_metadata_s']} s, total "
            f"estimado {total_est:.1f} s < {MIN_TOTAL_S:.0f} s "
            f"(13:30); faltan "
            f"{MIN_TOTAL_S - total_est:.0f} s. No se construye un "
            "vídeo corto en silencio.")

    # 2. Credenciales ANTES de descargar y renderizar (spec §5).
    ok, detail = check_youtube_credentials()
    if not ok:
        raise CompilationError(
            f"credenciales YouTube no válidas ({tenant}): {detail}")

    # 3. Descarga por el bridge; si un clip falla, se descarta y se
    # toma el siguiente por score de la reserva (pool dedupado).
    if bridge is None:
        raise CompilationError(
            f"sin sesión del panel ({tenant}): la descarga exige "
            "bridge (PANEL_BRAIN_TOKEN o PANEL_USERNAME/"
            "PANEL_PASSWORD); D1 solo mide el pool, no descarga")
    for clip in kept:
        clip.setdefault("tenant_origen", tenant)
    reserve = [c for c in kept if c not in selected]
    selected, downloaded, descartados_descarga = (
        _download_with_substitution(
            list(selected), list(reserve), clips_dir,
            {tenant: bridge}))

    # 4. Duraciones reales por ffprobe; si el total real sale del
    # rango 13:30-14:30, se para antes de renderizar.
    real_durations = {}
    for clip in selected:
        real_durations[clip["id"]] = probe_duration(
            downloaded[(clip.get("tenant_origen"), clip["id"])])
    total_real = estimate_total(
        [real_durations[c["id"]] for c in selected])
    if not (MIN_TOTAL_S <= total_real <= HARD_TOTAL_S):
        raise CompilationError(
            f"total real por ffprobe {total_real:.1f} s fuera de "
            f"13:30-14:30 ({tenant}); estimado por metadata era "
            f"{total_est:.1f} s")

    # 5. Render + verificación del archivo final.
    segments = build_segments(selected)
    out_path = os.path.join(runs_tenant, f"recopilacion-{tenant}.mp4")
    render_compilation(segments, clips_dir, out_path, work)
    final_duration = probe_duration(out_path)
    if not (MIN_TOTAL_S <= final_duration <= HARD_TOTAL_S):
        raise CompilationError(
            f"archivo final dura {final_duration:.1f} s, fuera de "
            f"13:30-14:30 ({tenant})")

    description = build_description(segments)
    title = compilation_title()
    report = {
        "tenant": tenant,
        "dry_run": dry_run,
        "titulo": title,
        "descripcion": description,
        "tags": TAGS,
        "clips": [{"id": c["id"], "titulo": c.get("title"),
                   "status": c.get("status"),
                   "duracion_s": round(real_durations[c["id"]], 1),
                   "score": c.get("score")} for c in selected],
        "publicados_incluidos": len(
            [c for c in selected if c.get("status") == "publicado"]),
        "descartados_descarga": descartados_descarga,
        "descartados_dedupe": [c["id"] for c in descartados_dedupe],
        "duracion_final_s": round(final_duration, 1),
        "video_id": None,
        "url": None,
    }
    if dry_run:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return report

    # 6. Subida (salvo --dry-run).
    checks = validate_longform_inputs(out_path, title, description,
                                      TAGS)
    failed = [d for ok, d in checks if not ok]
    if failed:
        raise CompilationError(
            "contrato publish_longform no pasa: " + "; ".join(failed))
    result = publish_longform(out_path, title, description, TAGS,
                              privacy=PRIVACY)
    report["video_id"] = result["video_id"]
    report["url"] = result["url"]
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return report


def _combined_report(label, dest_tenant, dry_run, selected,
                       real_durations=None, title=None):
    """Esqueleto del informe de un vídeo combinado (§2b): lista de
    clips con tenant de origen, como exige la spec."""
    return {
        "video": label,
        "tenant": dest_tenant,
        "dry_run": dry_run,
        "titulo": title if title is not None else compilation_title(),
        "descripcion": "",
        "tags": TAGS,
        "clips": [{"id": c["id"],
                   "tenant_origen": c.get("tenant_origen"),
                   "titulo": c.get("title"),
                   "status": c.get("status"),
                   "duracion_s": round(
                       (real_durations or {}).get(c["id"])
                       or clip_duration(c) or 0, 1),
                   "score": c.get("score")} for c in selected],
        "publicados_incluidos": len(
            [c for c in selected if c.get("status") == "publicado"]),
        "descartados_descarga": [],
        "descartados_dedupe": [],
        "duracion_final_s": None,
        "video_id": None,
        "url": None,
        "estado": "fallido",
        "bloqueo": None,
        "credenciales": {"ok": False, "detalle": ""},
    }


def _build_combined_video(label, dest_tenant, selected, reserve,
                          runs_video_dir, bridges, dry_run, base_dir,
                          force=False, on_progress=None):
    """Un vídeo del modo combinado (§2b). Siempre devuelve el
    informe: los fallos quedan registrados (estado + bloqueo) y
    NO levantan, para que el otro vídeo siga su curso. Solo el
    pool insuficiente levanta, y lo decide el llamador antes de
    llamar aquí (con el pre-chequeo de ambos vídeos).

    Orden por vídeo: credenciales (se registran, no abortan: sin
    token el MP4 se construye igual y se marca bloqueado) ->
    descarga (con sustitución, solo de su reserva: la disjunción
    se mantiene) -> ffprobe real -> render -> verificación ->
    guarda anti-duplicado en YouTube -> subida solo si el token
    propio del tenant es válido y el vídeo no está ya subido hoy.
    `on_progress` se reenvía a descarga y render (B1).
    """
    os.makedirs(runs_video_dir, exist_ok=True)
    report_path = os.path.join(runs_video_dir, "report.json")
    out_path = os.path.join(
        runs_video_dir, f"recopilacion-combinado-{label.lower()}.mp4")
    work = os.path.join(runs_video_dir, "work")
    clips_dir = os.path.join(work, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    # Título de ESTA corrida (B4): se calcula una sola vez y es
    # el que comparan la guarda y la subida.
    title = compilation_title()
    report = _combined_report(label, dest_tenant, dry_run, selected,
                              title=title)
    if os.path.isfile(report_path) and not force:
        try:
            with open(report_path, encoding="utf-8") as f:
                existing = json.load(f)
        except (ValueError, OSError):
            existing = {}
        if existing.get("video_id"):
            report["bloqueo"] = (
                f"ya existe una recopilación subida para el vídeo "
                f"{label} ({existing['video_id']}); usa --force "
                "para subir otra (no se suben 2 el mismo día al "
                "mismo canal)")
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            return report

    # 1. Credenciales ANTES de descargar y renderizar (spec §5):
    # se registran, no abortan.
    token_path = token_path_for_tenant(dest_tenant, base_dir)
    if token_path is None:
        cred_ok, cred_detail = False, (
            "sin token propio del tenant "
            f"(tokens/youtube_shorts_{dest_tenant}.json); el archivo "
            "compartido tokens/youtube_shorts.json no identifica a "
            "qué canal pertenece y está prohibido subir el vídeo de "
            "un tenant a la cuenta del otro")
    else:
        cred_ok, cred_detail = check_youtube_credentials(token_path)
    report["credenciales"] = {"ok": cred_ok, "detalle": cred_detail}

    # 2. Descarga con sustitución (solo de su reserva).
    try:
        final_selected, downloaded, descartados_descarga = (
            _download_with_substitution(
                list(selected), list(reserve), clips_dir, bridges,
                on_progress=on_progress))
    except CompilationError as e:
        report["bloqueo"] = f"descarga: {e}"
        report["descartados_descarga"] = []
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return report
    report["descartados_descarga"] = descartados_descarga
    report["clips"] = _combined_report(
        label, dest_tenant, dry_run, final_selected,
        title=title)["clips"]

    # 3. Duraciones reales por ffprobe.
    try:
        real_durations = {}
        for clip in final_selected:
            real_durations[clip["id"]] = probe_duration(
                downloaded[(clip.get("tenant_origen"), clip["id"])])
        total_real = estimate_total(
            [real_durations[c["id"]] for c in final_selected])
        if not (MIN_TOTAL_S <= total_real <= HARD_TOTAL_S):
            raise CompilationError(
                f"total real por ffprobe {total_real:.1f} s fuera de "
                f"13:30-14:30 ({dest_tenant})")
    except CompilationError as e:
        report["bloqueo"] = f"ffprobe: {e}"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return report
    report["clips"] = _combined_report(
        label, dest_tenant, dry_run, final_selected,
        real_durations, title=title)["clips"]

    # 4. Render + verificación del archivo final.
    try:
        segments = build_segments(final_selected)
        render_compilation(segments, clips_dir, out_path, work,
                           on_progress=on_progress)
        final_duration = probe_duration(out_path)
        if not (MIN_TOTAL_S <= final_duration <= HARD_TOTAL_S):
            raise CompilationError(
                f"archivo final dura {final_duration:.1f} s, fuera de "
                f"13:30-14:30 ({dest_tenant})")
    except CompilationError as e:
        report["bloqueo"] = f"render: {e}"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return report
    report["duracion_final_s"] = round(final_duration, 1)
    report["descripcion"] = build_description(segments)
    if dry_run:
        report["estado"] = "construido_sin_subir"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return report
    if not cred_ok:
        report["estado"] = "construido_sin_subir"
        report["bloqueo"] = f"credenciales: {cred_detail}"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return report

    # 5. Guarda anti-duplicado (B3) + subida, con UN solo
    # servicio de YouTube por tenant (M11). La guarda vive aquí,
    # justo antes de publish_longform: si el vídeo con este
    # título ya se subió hoy, no se sube y se marca ya_subido
    # con la URL existente. Si la guarda no puede comprobar
    # YouTube (red, credenciales), se sigue a la subida: la
    # guarda es best-effort, no un bloqueo.
    youtube_service = None
    existing = None
    try:
        youtube_service = _build_youtube_service(token_path)
        existing = ya_subido_hoy(youtube_service, title)
    except Exception as e:  # noqa: BLE001 - motivo al log
        print(f"[compilation] vídeo {label}: la guarda "
              f"anti-duplicado no pudo comprobar YouTube ({e}); "
              "se sigue a la subida", flush=True)
        youtube_service = None
        existing = None
    if existing is not None:
        report["video_id"] = existing["video_id"]
        report["url"] = existing["url"]
        report["estado"] = "ya_subido"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return report
    try:
        checks = validate_longform_inputs(
            out_path, title, report["descripcion"], TAGS, token_path)
        failed = [d for ok, d in checks if not ok]
        if failed:
            raise CompilationError(
                "contrato publish_longform no pasa: "
                + "; ".join(failed))
        result = publish_longform(out_path, title,
                                  report["descripcion"], TAGS,
                                  privacy=PRIVACY,
                                  token_path=token_path,
                                  youtube_service=youtube_service)
    except CompilationError as e:
        report["estado"] = "fallido"
        report["bloqueo"] = f"subida: {e}"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return report
    report["video_id"] = result["video_id"]
    report["url"] = result["url"]
    report["estado"] = "subido"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return report


def run_combined_compilation(dry_run=False, force=False, base_dir=None,
                             pools=None, bridges=None, on_progress=None):
    """Dos vídeos disjuntos con el pool combinado (spec §2b).

    `pools` inyecta {tenant: filas} (tests / --pool-json); si no,
    D1 solo lectura por tenant. `bridges` es dict tenant ->
    PanelBridge para las descargas. `on_progress` (opcional) se
    reenvía a cada vídeo (descarga y render, B1). Devuelve
    {"video_a": ..., "video_b": ..., "pool_combinado": N, ...}. Si
    el pool no da para dos vídeos ≥13:30, levanta
    PoolInsufficientError con los segundos/clips que faltan para
    el segundo (no se construye un solo vídeo en silencio).
    """
    base = base_dir or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    bridges = bridges or {}

    # 1. Pool combinado: ambas D1, mismos filtros de §2; cada clip
    # conserva su tenant de origen.
    combined = []
    for tenant in TENANTS:
        if pools is not None:
            raw = pools.get(tenant, [])
        else:
            raw = load_pool_d1(tenant)
        for clip in raw:
            row = dict(clip)
            row["tenant_origen"] = tenant
            combined.append(row)
    aptos, rechazados = filter_pool(combined)
    kept, descartados_dedupe = dedupe_pool(aptos)
    sub_a, sub_b = split_alternating(kept)

    # 2. Pre-chequeo por vídeo (cada uno ≥13:30 por la fórmula).
    plan = []
    for label, dest_tenant, sub in (
            ("A", VIDEO_A_TENANT, sub_a),
            ("B", VIDEO_B_TENANT, sub_b)):
        selected, total_est = select_clips(sub)
        precheck = _precheck_dict(
            dest_tenant, len(combined), aptos, rechazados, kept,
            descartados_dedupe, selected, total_est)
        precheck["video"] = label
        precheck["pool_combinado"] = len(combined)
        runs_video_dir = os.path.join(
            base, RUNS_DIRNAME, f"combinado-{label.lower()}")
        os.makedirs(runs_video_dir, exist_ok=True)
        with open(os.path.join(runs_video_dir, "precheck.json"), "w",
                  encoding="utf-8") as f:
            json.dump(precheck, f, indent=2, ensure_ascii=False)
        plan.append((label, dest_tenant, sub, selected, total_est,
                     runs_video_dir))
    shortfalls = [(label, total_est) for
                  label, _, _, _, total_est, _ in plan
                  if total_est < MIN_TOTAL_S]
    if shortfalls:
        detail = "; ".join(
            f"vídeo {label}: total estimado {tot:.1f} s < 810 s, "
            f"faltan {MIN_TOTAL_S - tot:.0f} s "
            f"(~{math.ceil((MIN_TOTAL_S - tot) / 40)} clips de 40 s)"
            for label, tot in shortfalls)
        raise PoolInsufficientError(
            f"pool combinado insuficiente: {detail}. No se construye "
            "un solo vídeo en silencio.")

    # 3. Construir cada vídeo; el fallo de uno no para al otro.
    reports = {}
    for label, dest_tenant, sub, selected, _, runs_video_dir in plan:
        reserve = [c for c in sub if c not in selected]
        reports[label] = _build_combined_video(
            label, dest_tenant, selected, reserve, runs_video_dir,
            bridges, dry_run, base, force=force,
            on_progress=on_progress)
    return {
        "video_a": reports["A"],
        "video_b": reports["B"],
        "pool_combinado": len(combined),
        "aptos": len(aptos),
        "tras_dedupe": len(kept),
        "descartados_dedupe": [c["id"] for c in descartados_dedupe],
    }


def main(argv=None):
    """CLI: python -m clipagent.compilation --tenant <t> [--dry-run]
    o python -m clipagent.compilation --combinado [--dry-run]."""
    parser = argparse.ArgumentParser(
        description="Recopilación 14 min YouTube (spec v3).")
    parser.add_argument("--tenant", required=False,
                        choices=list(TENANTS))
    parser.add_argument("--combinado", action="store_true",
                        help="dos vídeos disjuntos con el pool "
                        "combinado de ambas colas (spec §2b)")
    parser.add_argument("--dry-run", action="store_true",
                        help="construye sin subir")
    parser.add_argument("--force", action="store_true",
                        help="permite una segunda subida (guarda off)")
    parser.add_argument("--pool-json", default=None,
                        help="filas del pool en JSON (medición offline; "
                        "en --combinado, objeto {tenant: filas})")
    args = parser.parse_args(argv)
    if args.combinado and args.tenant:
        parser.error("--combinado no admite --tenant")
    if not args.combinado and not args.tenant:
        parser.error("falta --tenant o --combinado")
    if args.combinado:
        return _main_combinado(args)
    pool = None
    if args.pool_json:
        with open(args.pool_json, encoding="utf-8") as f:
            pool = json.load(f)
    bridge = None
    if pool is None:
        try:
            from .panel_bridge import PanelBridge
            bridge = PanelBridge.from_env()
        except Exception as e:  # noqa: BLE001 - sin bridge, D1 manda
            print(f"[compilation] sin bridge ({e}); pool por D1",
                  flush=True)
    try:
        report = run_compilation(args.tenant, dry_run=args.dry_run,
                                 force=args.force, bridge=bridge,
                                 pool=pool)
    except PoolInsufficientError as e:
        print(f"[compilation] PARADO: {e}", file=sys.stderr, flush=True)
        return 2
    except CompilationError as e:
        print(f"[compilation] ERROR: {e}", file=sys.stderr, flush=True)
        return 3
    print(f"[compilation] tenant={args.tenant} "
          f"clips={len(report['clips'])} "
          f"duración={report['duracion_final_s']} s "
          f"dry_run={report['dry_run']} "
          f"url={report['url'] or 'sin subir'}", flush=True)
    return 0


def _main_combinado(args):
    """CLI del modo combinado (§2b)."""
    pools = None
    if args.pool_json:
        with open(args.pool_json, encoding="utf-8") as f:
            pools = json.load(f)
    bridges = {}
    if pools is None:
        try:
            from .panel_bridge import PanelBridge
            bridge = PanelBridge.from_env()
        except Exception as e:  # noqa: BLE001 - sin bridge, D1 manda
            print(f"[compilation] sin bridge ({e}); pool por D1",
                  flush=True)
            bridge = None
        if bridge is not None and getattr(bridge, "tenant", None):
            bridges[bridge.tenant] = bridge
    try:
        combined = run_combined_compilation(
            dry_run=args.dry_run, force=args.force, bridges=bridges,
            pools=pools)
    except PoolInsufficientError as e:
        print(f"[compilation] PARADO: {e}", file=sys.stderr, flush=True)
        return 2
    except CompilationError as e:
        print(f"[compilation] ERROR: {e}", file=sys.stderr, flush=True)
        return 3
    failed = []
    for label in ("A", "B"):
        report = combined["video_a" if label == "A" else "video_b"]
        print(f"[compilation] vídeo {label} tenant={report['tenant']} "
              f"estado={report['estado']} "
              f"clips={len(report['clips'])} "
              f"duración={report['duracion_final_s']} s "
              f"dry_run={report['dry_run']} "
              f"url={report['url'] or 'sin subir'} "
              f"bloqueo={report['bloqueo'] or '-'}", flush=True)
        if report["estado"] == "fallido":
            failed.append(label)
    return 3 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
