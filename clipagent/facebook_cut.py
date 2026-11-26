"""Cortador automático de clips desde Facebook (2026-11-25,
SPEC-2026-11-25-entrada-facebook.md §3.1, v1 aprobada).

El programa solo, nunca Muse a mano: dado un reel de Facebook
GTA VI, busca los momentos por PICOS DE AUDIO, corta los top-N,
los renderiza con la plantilla rubius, los valida y deja el mismo
manifiesto que ``run_youtube_clips`` produce, para que
``clip_passes_gates`` y ``burn_and_enqueue`` lo consuman sin
cambios. Espejo de ``clipagent/youtube_cut.py`` donde aplica.

Diferencias con YouTube (declaradas en la spec, no accidentes):
- Bandas de duración del reel de entrada: < 15 s -> ningún clip;
  15-60 s -> el reel entero es el candidato (sin detección de
  picos, directo a render/validar); > 60 s -> audio completo en
  URL única (o muestreo si supera 50 MB) y ventanas top-N de
  40-60 s alrededor de los picos RMS.
- El audio del candidato de 15-60 s se extrae del vídeo ya
  descargado (``extract_audio_from_video``: ffmpeg -vn a wav),
  sin segunda descarga; YouTube baja bestaudio aparte.
- Puerta de pico: buckets RMS de 1 s en la banda 15-60 s (con
  buckets de 10 s un reel de 18,85 s tiene solo 2 buckets y el
  múltiplo es 2*máx/(máx+mín) < 2,0 por construcción: ningún
  reel normal pasaría); de 10 s en la banda > 60 s. La mediana
  es de TODOS los buckets de 1 s, no solo de la ventana.
- El título del -J de Facebook es texto de engagement («31K
  views · 894 reactions | #gta6 ...»), NO el título del reel:
  el título del clip sale de ``clipagent.metadata.package_clip``
  con la ficha del momento CON la transcripción (spec
  títulos-verdad 2026-11-25, §6.4), y el SEO es
  ``metadata.score_package_seo``.
- Verificación de GTA VI: ``facebook_clearly_not_gta6`` por
  hashtags de la descripción, con chequeo por transcripción
  cuando no hay hashtags; fail closed («juego no verificado»).
  ``moment_card.game`` / ``viral["game"]`` son "GTA VI" SOLO
  cuando la verificación pasa (nunca hardcodeado).
- ``source_key`` = "fb:<reel_id>", ``vod_id`` = id numérico del
  reel como string, ``score_kind`` = "facebook_audio".

El cortador ESCRIBE ``out_dir/manifest.json`` a disco (como
``youtube_cut.py``): sin él el clip cae en ``no_score`` y nunca
se publica.
"""

import json
import os
import re
import subprocess
import time
from urllib.parse import parse_qs, urlparse

from .audio import rms_per_second
from .youtube import (
    MAX_ATTEMPTS,
    RETRY_BACKOFF_S,
    YTDLP_BASE_OPTS,
    _default_runner,
    _run_with_retries,
    _ytdlp_cmd,
    format_hms,
)
from .youtube_cut import (
    AUDIO_SAMPLE_S,
    CLIP_TARGET_DUR_S,
    RMS_BUCKET_S,
    AudioTooLarge,
    _llm_select_candidates,
    _sample_starts,
    audio_multiple,
    audio_peak_gate,
    build_audio_input,
    download_audio,
    hype_points,
    plan_audio_samples,
    rms_buckets_10s,
    transcript_text,
    transcript_window_text,
)
from .detect import score_clip

# Selector de formato verificado (§1 de la spec): primero el mp4
# progresivo de Facebook (hd = 720x1280 H.264 + AAC; sd si no hay
# hd) y solo si no hay progresivo, merge bestvideo <=1080 +
# bestaudio. H.264 es el códec probado de la cadena de render;
# el AV1 queda como fallback.
FACEBOOK_FORMAT = "hd/sd/bv*[height<=1080]+ba/b[height<=1080]/b"
# Bandas de duración del reel de entrada (§3.1, distintas de
# YouTube a propósito: el suelo 15 s es el min_duration_s de la
# sección viral_source de config.yaml).
FB_MIN_DUR_S = 15
FB_MAX_DUR_S = 60
# Buckets RMS: de 1 s en la banda 15-60 s (la puerta es
# alcanzable), de 10 s en la banda > 60 s (como YouTube).
FB_BUCKET_S_SHORT = 1
FB_BUCKET_S_LONG = RMS_BUCKET_S
# Puerta de pico de audio (§3.4 de la spec de YouTube): misma
# calibración inicial que YouTube (2,0).
AUDIO_MULTIPLE_MIN = 2.0

_GTA6_TITLE_TOKENS = ("gtavi", "gta6", "grandtheftautovi")

# Prefijos que se quitan al normalizar el nombre del autor
# («Clips de Noni» -> «noni»).
_FACEBOOK_NAME_PREFIXES = ("clips de ", "clips ", "clipsde")


class FacebookError(Exception):
    """Facebook no respondió o no se pudo leer/descargar (tras
    reintentos). Nunca tumba al llamador: el fallback salta el
    reel."""


def _log(msg):
    print(f"[facebook-cut] {msg}", flush=True)


# ---------------------------------------------------------------------------
# URL y metadatos (yt-dlp, un solo -J, con reintento)
# ---------------------------------------------------------------------------

def is_facebook_url(url):
    """¿Es una URL de Facebook (reel)?

    Host en (facebook.com, www.facebook.com, m.facebook.com,
    fb.watch) y ruta con /reel/, /share/r/ o /watch/ (con v=),
    o fb.watch con cualquier ruta (un enlace fb.watch/<código>
    tiene ruta /<código> y no pasaría la regla de rutas: el host
    manda en ese caso). Es el predicado del dispatch de ``run()``
    en vod_fallback.
    """
    try:
        parsed = urlparse(url or "")
        host = (parsed.hostname or "").lower()
    except (TypeError, ValueError):
        return False
    if host == "fb.watch":
        return True
    if host not in ("facebook.com", "www.facebook.com",
                    "m.facebook.com"):
        return False
    path = parsed.path or ""
    if "/reel/" in path or "/share/r/" in path:
        return True
    if path == "/watch" or path.startswith("/watch/"):
        return bool(parse_qs(parsed.query or "").get("v"))
    return False


def fetch_facebook_metadata(reel_url, runner=None, sleep=None):
    """Metadatos con UN solo ``yt-dlp -J``: id, duración, autor,
    descripción (los hashtags), vistas.

    El id del reel se toma del ``id`` del -J, NO de parsear la
    URL: el enlace /share/r/ no contiene el id y el -J lo
    resuelve. Reintenta (3 intentos con backoff) cuando el
    intento devuelve código != 0 O stdout vacío O JSON inválido
    O JSON sin campo ``id``: el predicado de reintento es
    «código 0 Y stdout parsea como JSON con ``id```, no solo el
    código (el fallo transitorio observado sobre /share/r/ fue
    salida vacía). NUNCA --no-check-certificate.
    """
    cmd = (_ytdlp_cmd() + YTDLP_BASE_OPTS
           + ["--skip-download", "-J", reel_url])
    run = runner or _default_runner
    do_sleep = sleep or time.sleep
    last = None
    data = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        last = run(cmd)
        data = None
        if last is not None and last.returncode == 0:
            try:
                parsed = json.loads(last.stdout or "")
            except ValueError:
                parsed = None
            if isinstance(parsed, dict) and parsed.get("id"):
                data = parsed
        if data is not None:
            break
        if attempt < MAX_ATTEMPTS:
            do_sleep(
                RETRY_BACKOFF_S[attempt - 1]
                if attempt - 1 < len(RETRY_BACKOFF_S)
                else RETRY_BACKOFF_S[-1]
            )
    if data is None:
        tail = ""
        if last is not None:
            tail = ((last.stderr or "") + (last.stdout or "")).strip()
        raise FacebookError(
            f"yt-dlp -J falló sobre {reel_url} "
            f"(código {last.returncode if last is not None else '?'}, "
            f"sin JSON con id tras {MAX_ATTEMPTS} intentos): "
            f"{tail[-300:]}")
    try:
        duration = int(data.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    return {
        "id": str(data.get("id")),
        "duration": duration,
        "uploader": data.get("uploader") or "",
        "uploader_id": str(data.get("uploader_id") or ""),
        "description": data.get("description") or "",
        "views": data.get("view_count") or 0,
    }


def download_facebook_segment(video_url, start_s, end_s, out_path,
                              format=FACEBOOK_FORMAT,
                              retries=MAX_ATTEMPTS, runner=None,
                              sleep=None):
    """Baja solo el segmento [start_s, end_s) de un reel de
    Facebook con ``yt-dlp --download-sections`` (espejo de
    ``download_youtube_segment``, con el formato de Facebook).

    Devuelve la ruta del archivo. Reintenta con backoff; tras
    los reintentos levanta FacebookError. Nunca baja el reel
    entero salvo cuando el segmento ES el reel entero (banda
    15-60 s).
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
        raise FacebookError(
            f"yt-dlp --download-sections falló sobre {video_url} "
            f"({sections}, código {proc.returncode}): "
            f"{tail[-300:]}"
        )
    return str(out_path)


def extract_audio_from_video(video_path, out_wav, runner=None):
    """Extrae el audio de un vídeo ya descargado a WAV
    (``ffmpeg -vn``, sin segunda descarga).

    Es el camino de la banda 15-60 s: el candidato es el reel
    entero y su audio sale del segmento descargado (distinto del
    camino YouTube, que baja bestaudio aparte). Si el vídeo es
    AV1, ffmpeg lo decodifica.
    """
    cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vn",
           "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le",
           str(out_wav)]
    run = runner or (lambda c: subprocess.run(
        c, capture_output=True, text=True, timeout=300))
    proc = run(cmd)
    if proc.returncode != 0 or not os.path.isfile(out_wav):
        tail = ((getattr(proc, "stderr", "") or "")
                + (getattr(proc, "stdout", "") or "")).strip()
        raise FacebookError(
            f"ffmpeg -vn falló sobre {video_path} "
            f"(código {proc.returncode}): {tail[-300:]}")
    return str(out_wav)


# ---------------------------------------------------------------------------
# Verificación de GTA VI (hashtags y transcripción, fail closed)
# ---------------------------------------------------------------------------

def _hashtags(description):
    """Hashtags de una descripción (sin la #)."""
    return re.findall(r"#([^\s#]+)", description or "")


def _description_mentions_gta6(description):
    """¿Algún hashtag de la descripción casa con un token GTA?"""
    for tag in _hashtags(description):
        compact = "".join(ch for ch in tag.lower() if ch.isalnum())
        if any(tok in compact for tok in _GTA6_TITLE_TOKENS):
            return True
    return False


def _text_mentions_gta6(text):
    """¿Un texto (transcripción) menciona GTA VI? Misma regla
    compacta que el pre-chequeo de vod_fallback."""
    compact = "".join(ch for ch in (text or "").lower()
                      if ch.isalnum())
    return any(tok in compact for tok in _GTA6_TITLE_TOKENS)


def facebook_clearly_not_gta6(meta):
    """¿El reel claramente NO es GTA VI (pre-chequeo)?

    Devuelve el motivo o None (misma convención que
    ``vod_clearly_not_gta6``: None = no se puede descartar por
    aquí). (1) Si la descripción trae hashtags y alguno casa
    con un token GTA -> verificado (None). (2) Si trae hashtags
    y ninguno casa -> se salta (motivo). (3) Si no trae hashtags
    -> None: el chequeo pasa a la transcripción tras transcribir
    el candidato (lo hace el cortador); (4) si ninguna señal
    confirma, el cortador falla cerrado («juego no verificado»).
    """
    description = (meta or {}).get("description") or ""
    tags = _hashtags(description)
    if not tags:
        return None
    if _description_mentions_gta6(description):
        return None
    return "no es GTA VI: hashtags sin GTA"


# ---------------------------------------------------------------------------
# Resolución del autor (espejo de _resolve_single_youtube_influencer)
# ---------------------------------------------------------------------------

def _normalize_facebook_author(value):
    """Nombre comparable del autor: minúsculas, sin el prefijo
    ``clips de `` / ``clips `` / ``clipsde`` y sin
    no-alfanuméricos («Clips de Noni» -> «noni»)."""
    v = (value or "").strip().lower()
    for prefix in _FACEBOOK_NAME_PREFIXES:
        if v.startswith(prefix):
            v = v[len(prefix):]
            break
    return "".join(ch for ch in v if ch.isalnum())


def _resolve_single_facebook_influencer(meta, streamers):
    """Idioma e influencer de una URL única: (1) match por
    ``uploader_id`` contenido en el ``profile_url`` (o
    ``channel_url`` normalizado) de una entrada
    ``platform=facebook``; (2) si no, normalizar el nombre del
    autor y comparar con los nombres normalizados de las
    entradas ``platform=facebook``; (3) si no casa con ninguna,
    fail closed: devuelve (None, nombre_visible) — el llamador
    no encola y reporta el evento ``clip_descartado``. El
    permiso es por plataforma: la entrada de Twitch del mismo
    nombre NO autoriza Facebook. Devuelve (lang, name)."""
    meta = meta or {}
    uploader_id = str(meta.get("uploader_id") or "").strip()
    author = (meta.get("uploader") or "").strip()
    for entry in (streamers or []):
        if (entry.get("platform") or "") != "facebook":
            continue
        profile = (entry.get("profile_url")
                   or entry.get("channel_url") or "")
        if uploader_id and uploader_id in str(profile):
            lang = (entry.get("language") or "es").lower()
            return (lang if lang in ("es", "en") else "es",
                    entry["name"])
    want = _normalize_facebook_author(author)
    if want:
        for entry in (streamers or []):
            if (entry.get("platform") or "") != "facebook":
                continue
            if _normalize_facebook_author(entry.get("name")) == want:
                lang = (entry.get("language") or "es").lower()
                return (lang if lang in ("es", "en") else "es",
                        entry["name"])
    _log(f"URL única: autor {author or uploader_id!r} sin entrada "
         "platform=facebook; fail closed (no se encola).")
    return None, author or uploader_id or "desconocido"


# ---------------------------------------------------------------------------
# Títulos + SEO (paquete viral del manifiesto)
# ---------------------------------------------------------------------------

def _resolve_recent_titles(bridge):
    """(recent_titles, dedupe_verified) desde el panel (R4.1).

    Sin bridge no hay lectura: (None, False). Si la lectura
    falla, fail-open solo en dedupe. El bridge lo pasa el
    llamador (run_facebook_clips); sin él el VOD sigue con
    dedupe no verificado.
    """
    if bridge is None:
        return None, False
    try:
        return bridge.list_titles(days=30), True
    except Exception as e:  # noqa: BLE001 - dedupe no verificado
        _log(f"list_titles falló ({e}): dedupe no verificado")
        return None, False


def build_facebook_package(video_description, transcript_txt,
                           streamer, lang, risas, hype,
                           audio_multiple_value, gta_verified,
                           bridge=None, llm_client=None):
    """Paquete viral + SEO de un clip de Facebook.

    Espejo de ``build_youtube_package`` sin título de vídeo (en
    Facebook NO hay título útil: el del -J es texto de
    engagement): el título, la descripción, los hashtags y la
    miniatura salen de ``package_clip`` con la ficha del momento
    CON la transcripción (checks R1–R4, dedupe contra el panel
    si hay bridge), nunca de ``suggest_titles``. Sin transcripción
    no hay título honesto: paquete retenido (best_title None,
    needs_human_review True), nunca título de reglas.
    ``moment_card.game`` / ``viral["game"]`` son "GTA VI" SOLO
    cuando ``gta_verified`` es True: sin verificación no se
    emite el juego, así la puerta de ``clip_passes_gates`` deja
    de ser tautológica. Devuelve {"viral", "title_es",
    "title_en", "seo_scores", "moment_card"}; "viral" trae además
    needs_human_review, dedupe_verified y pkg_source.
    """
    from .workers import vod_package_from_transcript

    from .metadata import score_package_seo

    streamer = (streamer or "").strip().lstrip("@")
    game = "GTA 6" if gta_verified else None
    recent_titles, dedupe_verified = _resolve_recent_titles(bridge)
    viral, moment_card = vod_package_from_transcript(
        transcript_txt, streamer, lang, risas, hype,
        audio_multiple_value, game=game,
        recent_titles=recent_titles,
        dedupe_verified=dedupe_verified,
        llm_client=llm_client)
    if gta_verified:
        viral["game"] = "GTA VI"
        moment_card = {"game": "GTA VI",
                       "title": viral["best_title"],
                       "streamer": streamer}
    else:
        viral.pop("game", None)
        moment_card = {"title": viral["best_title"],
                       "streamer": streamer}
    if viral.get("needs_human_review") or not viral.get("best_title"):
        return {"viral": viral, "title_es": "", "title_en": "",
                "desc_es": "", "desc_en": "",
                "seo_scores": score_package_seo(viral, lang=lang),
                "moment_card": moment_card}
    other = "en" if lang != "en" else "es"
    viral_other, _mc = vod_package_from_transcript(
        transcript_txt, streamer, other, risas, hype,
        audio_multiple_value, game=game,
        recent_titles=recent_titles,
        dedupe_verified=dedupe_verified,
        llm_client=llm_client)
    if lang == "en":
        title_en = viral["best_title"]
        title_es = viral_other.get("best_title") or title_en
        desc_en = viral["description"]
        desc_es = viral_other.get("description") or desc_en
    else:
        title_es = viral["best_title"]
        title_en = viral_other.get("best_title") or title_es
        desc_es = viral["description"]
        desc_en = viral_other.get("description") or desc_es
    seo_scores = score_package_seo(viral, lang=lang)
    return {
        "viral": viral,
        "title_es": title_es,
        "title_en": title_en,
        "desc_es": desc_es,
        "desc_en": desc_en,
        "seo_scores": seo_scores,
        "moment_card": moment_card,
    }


# ---------------------------------------------------------------------------
# Viralidad (mismo adaptador que YouTube)
# ---------------------------------------------------------------------------

def score_facebook_clip(rms_list, transcript, audio_multiple_value):
    """Viralidad de un clip de Facebook con el adaptador de
    YouTube: ``score_clip(detail=None, chat_multiple=None,
    audio=audio, platform=None)`` (chat estimado en 5,0; total
    45 + valor_audio, 47-55). Sin RMS medible (sin pista de
    audio) levanta ValueError: el clip se descarta, no se
    estima. Devuelve (viral_score, viral_breakdown) con
    ``viral_breakdown["audio_multiple"]``.
    """
    audio = build_audio_input(rms_list, transcript)
    if (audio.get("stats") or {}).get("std_db") is None:
        raise ValueError("sin audio medido (sin pista de audio)")
    viral_score = score_clip(detail=None, chat_multiple=None,
                             audio=audio, platform=None)
    breakdown = {
        "audio_multiple": round(float(audio_multiple_value), 2),
        "audio_component": viral_score["components"]["audio"]["value"],
        "chat_component_estimated": 5.0,
    }
    return viral_score, breakdown


# ---------------------------------------------------------------------------
# Defaults inyectables (render / validación / transcripción / RMS)
# ---------------------------------------------------------------------------

def _default_transcribe(path, model="tiny"):
    from .transcribe import transcribe_audio

    transcript, _info = transcribe_audio(path, model_name=model)
    return transcript or {"segments": []}


def _default_render(seg_path, clip_path, duration, cfg):
    from .config import get_layout_config
    from .render import cut_clip_with_layout

    layout_cfg = get_layout_config(cfg or {})
    return cut_clip_with_layout(
        seg_path, 0, duration, clip_path,
        layout_mode="auto", cfg=layout_cfg)


def _default_validate(clip_path, title, transcript_txt, cfg):
    from .final_review import validate_final_clip

    return validate_final_clip(
        clip_path, title, transcript_text=transcript_txt,
        srt_path="", cfg=cfg or {})


def _default_layout(seg_path, duration, cfg):
    try:
        from .render import detect_vod_facecam

        det = detect_vod_facecam(seg_path, duration, cfg or {})
        if isinstance(det, dict):
            return {"vod_id": "", "facecam_box": det.get("facecam_box"),
                    "method": det.get("method"),
                    "hits": det.get("hits"), "samples": det.get("samples")}
    except Exception:  # noqa: BLE001 - sin layout, cada clip decide
        pass
    return {"vod_id": "", "facecam_box": None, "method": None,
            "hits": 0, "samples": 0}


def _fmt_ts(seconds):
    """Segundos -> '03h14m50s' (mismo formato que los segmentos de
    auto_clips: segments/seg_{n:02d}_t*.mp4)."""
    total = int(seconds)
    return (f"{total // 3600}h{(total % 3600) // 60:02d}m"
            f"{total % 60:02d}s")


def _write_manifest(out_dir, manifest):
    """Escribe out_dir/manifest.json de forma atómica (igual que
    auto_clips.write_manifest_atomic)."""
    os.makedirs(out_dir, exist_ok=True)
    tmp_path = os.path.join(out_dir, ".manifest.json.tmp")
    final_path = os.path.join(out_dir, "manifest.json")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, final_path)
    return final_path


def _peak_second(buckets, start, end):
    """Segundo del bucket RMS máximo dentro de [start, end), o el
    punto medio de la ventana si no hay buckets."""
    in_window = [(t, v) for t, v in (buckets or [])
                 if start <= t < end]
    if not in_window:
        return float(start + (end - start) / 2)
    return float(max(in_window, key=lambda tv: tv[1])[0])


# ---------------------------------------------------------------------------
# El cortador
# ---------------------------------------------------------------------------

def run_facebook_clips(reel_url, out_dir, top_n=2, lang="es",
                       single_url=True, llm_select=False,
                       metadata_fn=None, audio_fn=None,
                       sample_fn=None, transcribe_fn=None,
                       rms_fn=None, segment_fn=None,
                       render_fn=None, validate_fn=None,
                       titles_fn=None, layout_fn=None,
                       runner=None, bridge=None, llm_client=None):
    """Corta clips de un reel de Facebook (contrato §3.1).

    Devuelve el manifiesto (dict) o None si no se pudo ni leer
    el reel. Escribe SIEMPRE ``out_dir/manifest.json`` a disco
    cuando hay metadatos (aunque no salga ningún clip: el
    manifiesto documenta el motivo en ``descartados``).
    ``bridge`` (PanelBridge, opcional): si se pasa, el dedupe de
    títulos se lee del panel (R4.1); si no, dedupe_verified=False.

    Cada clip trae: start, duration, file (MP4 final
    renderizado), viral_score (dict de score_clip), seo_scores
    (motor local), moment_card {game, title}, transcript (audio
    real), source "facebook", viral {best_title, description,
    tags, thumbnail_text, viral_multiple}, title_es/title_en,
    source_key ("fb:<reel_id>"), vod_id (id numérico del reel),
    vod_layout, n (índice del segmento; ``_rerender_vod_clip``
    busca segments/seg_{n:02d}_t*.mp4), viral_breakdown
    {audio_multiple}, hype_score y score_kind "facebook_audio".
    Los segmentos descargados quedan en segments/ con el
    nombrado segments/seg_{n:02d}_t*.mp4.
    """
    lang = lang if lang in ("es", "en") else "es"
    if not is_facebook_url(reel_url):
        _log(f"no es URL de Facebook: {reel_url}")
        return None
    try:
        meta = (metadata_fn or fetch_facebook_metadata)(reel_url)
    except Exception as exc:  # noqa: BLE001 - sin metadatos no hay corte
        _log(f"sin metadatos de {reel_url}: {exc}")
        return None
    reel_id = str(meta.get("id") or "")
    if not reel_id:
        _log(f"sin id de reel en {reel_url}")
        return None
    duration = int(meta.get("duration") or 0)
    streamer = ((meta.get("uploader") or "").strip().lstrip("@")
                or reel_id)
    os.makedirs(out_dir, exist_ok=True)
    segments_dir = os.path.join(out_dir, "segments")
    clips_dir = os.path.join(out_dir, "clips")
    os.makedirs(segments_dir, exist_ok=True)
    os.makedirs(clips_dir, exist_ok=True)
    manifest = {
        "vod_id": reel_id,
        "vod_url": reel_url,
        "source": "facebook",
        "lang": lang,
        "title": "",
        "duration": duration,
        "views": meta.get("views") or 0,
        "likes": 0,
        "clips": [],
        "descartados": [],
        "vod_layout": None,
    }
    # Pre-chequeo GTA VI por hashtags de la descripción: solo se
    # salta el reel si trae hashtags y ninguno es GTA (fail
    # closed por hashtags); sin hashtags la decisión pasa a la
    # transcripción, más abajo.
    skip = facebook_clearly_not_gta6(meta)
    if skip:
        _log(f"reel {reel_id} se salta ({skip})")
        manifest["descartados"].append(
            {"reason": skip, "vod_id": reel_id})
        _write_manifest(out_dir, manifest)
        return manifest
    gta_verified = _description_mentions_gta6(
        meta.get("description") or "")
    if duration < FB_MIN_DUR_S:
        # Banda < 15 s: ningún clip posible (resultado
        # documentado, no fallo).
        reason = (f"reel de {duration}s < {FB_MIN_DUR_S}s: "
                  "ningún clip posible")
        _log(reason)
        manifest["descartados"].append(
            {"reason": reason, "vod_id": reel_id})
        _write_manifest(out_dir, manifest)
        return manifest
    try:
        from .config import load_config
        cfg = load_config()
    except Exception:  # noqa: BLE001 - sin config, defaults
        cfg = {}
    transcribe = transcribe_fn or _default_transcribe
    rms_measure = rms_fn or rms_per_second
    download_segment = segment_fn or download_facebook_segment
    render = render_fn or _default_render
    validate = validate_fn or _default_validate
    titles = titles_fn or build_facebook_package
    layout_detect = layout_fn or _default_layout

    # --- Audio -> muestras (inicio, texto, puntos, buckets) -------
    # Una muestra: {"start": t, "text": str, "points": int,
    # "risas": int, "hype": int, "buckets": [(t, lineal)],
    # "transcript": ..., "whole_video": bool}.
    samples = []
    all_buckets = []
    audio_missing = False
    whole_seg_path = None
    try:
        if duration <= FB_MAX_DUR_S:
            # Banda 15-60 s: el reel entero es el candidato (sin
            # detección de picos, directo a render/validar). El
            # audio se extrae del vídeo ya descargado (ffmpeg
            # -vn), sin segunda descarga. Buckets RMS de 1 s.
            seg_name = f"seg_01_t{_fmt_ts(0)}.mp4"
            seg_path = os.path.join(segments_dir, seg_name)
            download_segment(reel_url, 0, duration, seg_path)
            whole_seg_path = seg_path
            wav = os.path.join(out_dir, "full_audio.wav")
            extract_audio_from_video(seg_path, wav)
            try:
                transcript = transcribe(wav)
                rms = rms_measure(wav)
            except Exception as exc:  # noqa: BLE001 - sin audio
                _log(f"audio completo sin transcripción/RMS ({exc})")
                manifest["descartados"].append(
                    {"reason": f"audio sin transcripción/RMS: {exc}",
                     "vod_id": reel_id})
                _write_manifest(out_dir, manifest)
                return manifest
            text = transcript_text(transcript)
            points, risas, hype = hype_points(text, lang)
            if not rms:
                audio_missing = True
            else:
                all_buckets = rms_buckets_10s(
                    rms, offset_s=0, bucket_s=FB_BUCKET_S_SHORT)
                samples.append({"start": 0, "text": text,
                                "points": points, "risas": risas,
                                "hype": hype,
                                "buckets": list(all_buckets),
                                "transcript": transcript,
                                "whole_video": True})
        else:
            plan = plan_audio_samples(duration, single_url)
            if plan["mode"] == "full":
                try:
                    wav = os.path.join(out_dir, "full_audio.wav")
                    (audio_fn or download_audio)(
                        reel_url, wav, runner=runner)
                except AudioTooLarge:
                    _log("audio completo > 50 MB: se usa muestreo")
                    plan = {"mode": "sample",
                            "starts": _sample_starts(duration)}
                    wav = None
                else:
                    try:
                        transcript = transcribe(wav)
                        rms = rms_measure(wav)
                    except Exception as exc:  # noqa: BLE001
                        _log(f"audio completo sin transcripción/RMS "
                             f"({exc})")
                        manifest["descartados"].append(
                            {"reason": "audio sin transcripción/RMS: "
                                      f"{exc}",
                             "vod_id": reel_id})
                        _write_manifest(out_dir, manifest)
                        return manifest
                    if not rms:
                        audio_missing = True
                    else:
                        all_buckets = rms_buckets_10s(
                            rms, offset_s=0,
                            bucket_s=FB_BUCKET_S_LONG)
                        # Ventanas de 30 s sobre el audio completo.
                        t = 0
                        while t < duration:
                            text = transcript_window_text(
                                transcript, t, t + AUDIO_SAMPLE_S)
                            points, risas, hype = hype_points(
                                text, lang)
                            samples.append({
                                "start": t, "text": text,
                                "points": points, "risas": risas,
                                "hype": hype,
                                "buckets": [
                                    (bt, v) for bt, v in all_buckets
                                    if t <= bt < t + AUDIO_SAMPLE_S],
                                "transcript": transcript,
                                "whole_video": False})
                            t += AUDIO_SAMPLE_S
            if plan["mode"] == "sample":
                for t in plan["starts"]:
                    wav = os.path.join(
                        out_dir, f"sample_{int(t)}.wav")
                    try:
                        (sample_fn or download_audio)(
                            reel_url, wav, start_s=t,
                            end_s=t + AUDIO_SAMPLE_S, runner=runner)
                    except AudioTooLarge:
                        continue
                    except Exception as exc:  # noqa: BLE001 - una
                        # muestra fallida no tumba el reel
                        _log(f"muestra {t}s falló ({exc}); se sigue")
                        continue
                    try:
                        transcript = transcribe(wav)
                        rms = rms_measure(wav)
                    except Exception as exc:  # noqa: BLE001
                        _log(f"muestra {t}s sin transcripción/RMS "
                             f"({exc}); se sigue")
                        continue
                    finally:
                        try:
                            os.unlink(wav)
                        except OSError:
                            pass
                    if not rms:
                        continue
                    buckets = rms_buckets_10s(
                        rms, offset_s=t, bucket_s=FB_BUCKET_S_LONG)
                    all_buckets.extend(buckets)
                    text = transcript_text(transcript)
                    points, risas, hype = hype_points(text, lang)
                    samples.append({"start": t, "text": text,
                                    "points": points, "risas": risas,
                                    "hype": hype, "buckets": buckets,
                                    "transcript": transcript,
                                    "whole_video": False})
    except Exception as exc:  # noqa: BLE001 - sin audio no hay corte
        _log(f"audio de {reel_id} falló: {exc}")
        manifest["descartados"].append(
            {"reason": f"audio falló: {exc}", "vod_id": reel_id})
        _write_manifest(out_dir, manifest)
        return manifest
    if audio_missing or not samples:
        # Sin pista de audio en el reel -> descarte con motivo
        # (no hay RMS ni transcripción que medir).
        reason = ("sin pista de audio medible en el reel: "
                  "descarte")
        _log(reason)
        manifest["descartados"].append(
            {"reason": reason, "vod_id": reel_id})
        _write_manifest(out_dir, manifest)
        return manifest
    all_buckets.sort()
    # Verificación de GTA VI por transcripción (caso sin
    # hashtags): si ninguna señal confirma GTA VI, fail closed.
    if not gta_verified:
        all_text = " ".join(s.get("text") or "" for s in samples)
        if _text_mentions_gta6(all_text):
            gta_verified = True
        else:
            reason = ("juego no verificado: sin hashtags GTA en la "
                      "descripción ni mención de GTA VI en la "
                      "transcripción")
            _log(reason)
            manifest["descartados"].append(
                {"reason": reason, "vod_id": reel_id})
            _write_manifest(out_dir, manifest)
            return manifest

    # --- Candidatos: ventana 40-60 s por muestra -------------------
    candidates = []
    for sample in samples:
        if sample.get("whole_video"):
            start = 0
            dur = duration
            window_end = duration
        else:
            start = max(0, sample["start"] - 10)
            dur = CLIP_TARGET_DUR_S
            if start + dur > duration:
                start = max(0, duration - dur)
            window_end = start + dur
        # El pico de audio es el bucket máximo dentro de la
        # ventana candidata; la mediana, de todos los buckets
        # muestreados del reel.
        mult, _peak, _med = audio_multiple(
            all_buckets, start, window_end)
        candidates.append({
            "start": int(start), "duration": int(dur),
            "points": sample["points"], "risas": sample["risas"],
            "hype": sample["hype"], "text": sample["text"],
            "audio_multiple": mult, "sample_start": sample["start"],
            "transcript": sample["transcript"],
            "peak": _peak_second(all_buckets, start, window_end),
            "seg_path": (whole_seg_path
                         if sample.get("whole_video") else None),
        })
    # Selección LLM (opcional): marca los candidatos que el LLM
    # elige como momento (segunda vía de la puerta).
    llm_chosen = set()
    if llm_select and candidates:
        llm_candidates = [{
            "start": c["start"], "end": c["start"] + c["duration"],
            "transcript": c["transcript"],
            "chat_score": c["points"],
            "chat_multiple": c["audio_multiple"],
            "risas": c["risas"], "hype": c["hype"],
        } for c in candidates]
        llm_chosen = _llm_select_candidates(llm_candidates)
    passing = []
    for cand in candidates:
        ok, why = audio_peak_gate(
            cand["audio_multiple"], cand["points"],
            llm_selected=cand["start"] in llm_chosen)
        cand["llm_selected"] = cand["start"] in llm_chosen
        if ok:
            passing.append(cand)
        else:
            manifest["descartados"].append({
                "reason": f"puerta de pico: {why}",
                "start": cand["start"], "vod_id": reel_id})
    passing.sort(key=lambda c: (c["points"], c["audio_multiple"]),
                 reverse=True)
    # Sin solape entre candidatos: dos ventanas que se solapan
    # son el mismo momento (quedarse con la mejor).
    chosen = []
    for cand in passing:
        if any(abs(cand["start"] - other["start"]) < FB_MIN_DUR_S
               for other in chosen):
            continue
        chosen.append(cand)
        if len(chosen) >= top_n:
            break
    if not chosen:
        _log(f"reel {reel_id}: ningún candidato pasó la puerta "
             "de pico de audio")
        _write_manifest(out_dir, manifest)
        return manifest

    # --- Corte + render + validación por candidato -----------------
    for idx, cand in enumerate(chosen, 1):
        start = cand["start"]
        dur = cand["duration"]
        seg_name = f"seg_{idx:02d}_t{_fmt_ts(start)}.mp4"
        seg_path = os.path.join(segments_dir, seg_name)
        if cand.get("seg_path"):
            # Banda 15-60 s: el segmento ya se descargó (es el
            # reel entero); se reutiliza, no se vuelve a bajar.
            seg_path = cand["seg_path"]
        else:
            try:
                download_segment(reel_url, start, start + dur,
                                 seg_path)
            except Exception as exc:  # noqa: BLE001 - un segmento
                _log(f"segmento {idx} falló ({exc}); se sigue")
                manifest["descartados"].append({
                    "reason": f"descarga del segmento falló: {exc}",
                    "start": start, "vod_id": reel_id})
                continue
        fname = (f"clip_{idx:02d}_t{_fmt_ts(start)}"
                 f"_s{cand['points']}_r{cand['risas']}"
                 f"h{cand['hype']}.mp4")
        clip_path = os.path.join(clips_dir, fname)
        try:
            render(seg_path, clip_path, dur, cfg)
        except Exception as exc:  # noqa: BLE001 - sin render no hay clip
            _log(f"render {idx} falló ({exc}); se sigue")
            manifest["descartados"].append({
                "reason": f"render falló: {exc}",
                "start": start, "vod_id": reel_id})
            continue
        if not os.path.isfile(clip_path):
            manifest["descartados"].append({
                "reason": "render sin archivo",
                "start": start, "vod_id": reel_id})
            continue
        # Transcripción precisa del segmento (mismo motor que los
        # subtítulos): los subtítulos salen del audio real, nunca
        # inventados. El RMS del segmento alimenta score_clip.
        try:
            seg_transcript = transcribe(seg_path)
            seg_rms = rms_measure(seg_path)
        except Exception as exc:  # noqa: BLE001
            _log(f"segmento {idx} sin transcripción/RMS ({exc}); "
                 "se sigue sin él")
            seg_transcript, seg_rms = {"segments": []}, []
        seg_text = transcript_text(seg_transcript)
        try:
            viral_score, breakdown = score_facebook_clip(
                seg_rms, seg_transcript, cand["audio_multiple"])
        except ValueError as exc:
            _log(f"segmento {idx} sin audio medible ({exc}); se salta")
            manifest["descartados"].append({
                "reason": str(exc), "start": start,
                "vod_id": reel_id})
            continue
        pkg = titles(video_description=meta.get("description") or "",
                     transcript_txt=seg_text, streamer=streamer,
                     lang=lang, risas=cand["risas"], hype=cand["hype"],
                     audio_multiple_value=cand["audio_multiple"],
                     gta_verified=gta_verified, bridge=bridge,
                     llm_client=llm_client)
        viral = pkg["viral"]
        # Retención (spec títulos-verdad, §6.4): sin título honesto
        # el clip NO entra en el manifiesto (queda registrado en
        # descartados con el motivo, nunca con título de reglas).
        if viral.get("needs_human_review") or not viral.get(
                "best_title"):
            _log(f"clip {idx}: sin título honesto: retención para "
                 f"revisión")
            manifest["descartados"].append({
                "reason": "sin título honesto: retención para "
                          "revisión",
                "start": start, "vod_id": reel_id})
            _write_manifest(out_dir, manifest)
            continue
        title = viral.get("best_title") or ""
        # Validador final sobre el render (mismo que Twitch). Un
        # veredicto «rechazado» no se guarda sin revisar los
        # fotogramas: el clip rechazado no entra en el manifiesto.
        try:
            verdict = validate(clip_path, title, seg_text, cfg)
        except Exception:  # noqa: BLE001 - sin veredicto, pendiente
            verdict = {"decision": "pendiente_revision"}
        if (verdict or {}).get("decision") == "rechazado":
            _log(f"clip {idx}: validación final rechazado "
                 f"({(verdict or {}).get('motivo') or ''}); "
                 "revisión humana, no se guarda")
            manifest["descartados"].append({
                "reason": "validación final: "
                          f"{(verdict or {}).get('motivo') or ''}",
                "start": start, "vod_id": reel_id})
            continue
        clip_entry = {
            "n": idx,
            "file": f"clips/{fname}",
            "peak": float(cand["peak"]),
            "start": int(start),
            "duration": int(dur),
            "score": int(cand["points"]),
            "risas": int(cand["risas"]),
            "hype": int(cand["hype"]),
            "title_es": pkg["title_es"],
            "title_en": pkg["title_en"],
            "desc_es": pkg.get("desc_es") or "",
            "desc_en": pkg.get("desc_en") or "",
            "viral": viral,
            "viral_score": viral_score,
            "viral_breakdown": breakdown,
            "hype_score": int(cand["points"]),
            "score_kind": "facebook_audio",
            "seo_scores": pkg["seo_scores"],
            "moment_card": pkg["moment_card"],
            "transcript": seg_transcript,
            "source": "facebook",
            "source_key": f"fb:{reel_id}",
            "vod_id": reel_id,
            "vod_layout": None,
        }
        manifest["clips"].append(clip_entry)
        _write_manifest(out_dir, manifest)
    # Layout del VOD (facecam): lo lee run() para el log. Si no se
    # puede detectar, queda con facecam_box None y cada clip
    # decide su layout solo (como en Twitch).
    if manifest["clips"]:
        seg_files = sorted(
            f for f in os.listdir(segments_dir)
            if f.endswith(".mp4"))
        if seg_files:
            layout = layout_detect(
                os.path.join(segments_dir, seg_files[0]),
                manifest["clips"][0]["duration"], cfg)
            layout["vod_id"] = reel_id
            manifest["vod_layout"] = layout
            for clip in manifest["clips"]:
                clip["vod_layout"] = layout
        _write_manifest(out_dir, manifest)
    _log(f"reel {reel_id}: {len(manifest['clips'])} clip(s) "
         f"de {len(chosen)} candidato(s)")
    return manifest
