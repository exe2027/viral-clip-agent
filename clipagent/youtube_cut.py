"""Cortador automático de clips desde YouTube (2026-11-24,
SPEC-2026-11-24-corte-automatico-youtube.md §3.1-§3.4, v8 aprobada).

El programa solo, nunca Muse a mano: dado un vídeo de YouTube
GTA VI, busca los momentos por PICOS DE AUDIO (muestreo de audio
transcrito + RMS, §3.2), corta los top-N, los renderiza con la
plantilla rubius, los valida y deja el mismo manifiesto que
``run_auto_clips`` produce para Twitch, para que
``clip_passes_gates`` y ``burn_and_enqueue`` lo consuman sin
cambios.

Piezas reutilizadas (no reinventadas):
- metadatos + pre-chequeo GTA VI: ``clipagent.youtube``
  (``yt-dlp -J``, ``youtube_vod_is_gta6``,
  ``download_youtube_segment``).
- muestreo: mismo esquema que ``sample_audio_peaks`` de Kick
  (muestras de 30 s, faster-whisper tiny, tokens de risas/hype
  de ``clipagent.ingest`` — los mismos que ``clips_chat_v2``).
- RMS/stats: ``clipagent.audio`` (``rms_per_second``,
  ``audio_stats``, ``transcript_words``).
- viralidad: ``clipagent.detect.score_clip`` con el adaptador de
  §3.3 (``detail=None, chat_multiple=None``; el componente chat
  queda estimado en 5,0 por el propio código; el total es
  45 + valor_audio, 47-55).
- títulos: ``clipagent.metadata.package_clip`` con la ficha del
  momento CON la transcripción (spec títulos-verdad 2026-11-25,
  §6.4): el título, la descripción, los hashtags y la miniatura
  salen del momento concreto del segmento, nunca de plantillas;
  sin transcripción el clip queda retenido para revisión.
- SEO: ``clipagent.metadata.score_package_seo`` (motor local).
- render: ``clipagent.render.cut_clip_with_layout`` (rubius) +
  ``workers.subtitle_clip`` / ``workers.apply_sfx`` (estos dos
  últimos los aplica ``burn_and_enqueue``, como en Twitch) y
  validador final ``final_review.validate_final_clip``.

Calibración de la puerta de pico (§3.4, valores MEDIDOS en el
vídeo de aceptación y en el VOD de Speed; si 2,0 no separa,
se ajusta con estos números en la mano):
- RMS en amplitud lineal: un grito frente a conversación normal
  da típicamente 2-5x (NO es el múltiplo de chat: los 1,19x/1,87x
  de ``detect.py`` son de chat, no de audio, y no calibran esto).
- Umbral inicial: ``audio_multiple >= 2,0`` + (hype > 0 o LLM).
- Fixtures deterministas en test_youtube_cut.py:
  conversación -30 dBFS con pico -20 dBFS -> lineal
  10^(-20/20)/10^(-30/20) = 3,16x (pasa); conversación plana
  -30 dBFS sin pico -> 1,0x (no pasa); silencio (mediana 0) ->
  1,0 y descarte.

Bandas de duración del vídeo de entrada (§3.8):
- < 40 s -> ningún clip posible (resultado documentado, no fallo).
- 40-60 s -> el vídeo entero es el candidato (sin detección).
- >= 60 s -> muestreo de §3.2.

El cortador ESCRIBE ``out_dir/manifest.json`` a disco (como
``auto_clips.py``): en el momento de publicar ``_clip_scores``
lee el manifiesto DEL DISCO y sin él el clip cae en ``no_score``
y nunca se publica.
"""

import json
import math
import os
import re
import statistics
from urllib.parse import parse_qs, urlparse

from .audio import audio_stats, rms_per_second, transcript_words
from .detect import score_clip
from .heatmap import peaks_from_heatmap_data
from .ingest import (
    EMOJIS,
    HYPE_EN_PHRASES,
    HYPE_EN_WORDS,
    HYPE_ES_PHRASES,
    HYPE_ES_WORDS,
    LAUGH_EN_PHRASES,
    LAUGH_EN_WORDS,
    LAUGH_ES_PHRASES,
    LAUGH_ES_WORDS,
)
from .youtube import (
    YTDLP_BASE_OPTS,
    YoutubeError,
    _default_runner,
    _run_with_retries,
    _ytdlp_cmd,
    download_youtube_segment,
    format_hms,
    is_youtube_url,
)

# Muestreo de audio (§3.2).
AUDIO_SAMPLE_S = 30
FULL_AUDIO_MAX_S = 1800  # <= 30 min -> audio completo
FULL_AUDIO_MAX_MB = 50
SAMPLE_INTERVAL_SHORT_S = 120  # vídeo < 1 h
SAMPLE_INTERVAL_LONG_S = 300  # vídeo >= 1 h
MAX_SAMPLES = 24
# Ventana del candidato alrededor del pico de audio.
CLIP_TARGET_DUR_S = 50
CLIP_MIN_DUR_S = 40
CLIP_MAX_DUR_S = 60
# Puerta de pico de audio (§3.4): calibración inicial.
AUDIO_MULTIPLE_MIN = 2.0
# Buckets RMS de 10 s para el múltiplo (amplitud lineal).
RMS_BUCKET_S = 10

_LAUGH_WORDS = frozenset(
    set(LAUGH_ES_WORDS) | set(LAUGH_EN_WORDS))
_HYPE_WORDS = frozenset(
    set(HYPE_ES_WORDS) | set(HYPE_EN_WORDS))
_LAUGH_PHRASES = frozenset(
    set(LAUGH_ES_PHRASES) | set(LAUGH_EN_PHRASES))
_HYPE_PHRASES = frozenset(
    set(HYPE_ES_PHRASES) | set(HYPE_EN_PHRASES))
_WORD_RE = re.compile(r"\w+", re.UNICODE)


class AudioTooLarge(Exception):
    """El audio completo supera 50 MB: no es un fallo, es el
    camino de muestreo (§3.2)."""


def _log(msg):
    print(f"[youtube-cut] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Utilidades puras (testeables sin red ni ffmpeg)
# ---------------------------------------------------------------------------

def video_id_from_url(url):
    """Id del vídeo desde una URL de YouTube (watch?v=, youtu.be,
    /shorts/, /embed/, /live/). "" si no se puede extraer."""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        return parsed.path.strip("/").split("/")[0].split("?")[0]
    if "youtube.com" in host:
        query = parse_qs(parsed.query or "")
        if query.get("v"):
            return query["v"][0]
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in (
                "shorts", "embed", "live", "v"):
            return parts[1]
    return ""


def _sample_starts(duration):
    """Inicios de muestra para el camino de muestreo: intervalo
    120 s (< 1 h) / 300 s (>= 1 h), máximo 24 muestras
    repartidas uniformemente (t_i = (i+0,5) x duración/24)."""
    interval = (SAMPLE_INTERVAL_SHORT_S if duration < 3600
                else SAMPLE_INTERVAL_LONG_S)
    n = int(math.ceil(duration / interval))
    if n > MAX_SAMPLES:
        return [int((i + 0.5) * duration / MAX_SAMPLES)
                for i in range(MAX_SAMPLES)]
    return [int(i * interval) for i in range(n)
            if i * interval < duration]


def plan_audio_samples(duration_s, single_url=False):
    """Plan de muestreo de audio (§3.2).

    Devuelve {"mode": "full"|"sample", "starts": [...]}:
    - URL única o vídeo <= 30 min -> "full" (audio completo,
      bestaudio <= 50 MB; si supera, el llamador cae a "sample").
    - Barrido, vídeo < 1 h -> muestras cada 120 s.
    - Barrido, vídeo >= 1 h -> muestras cada 300 s, máximo 24
      repartidas UNIFORMEMENTE por toda la duración
      (t_i = (i+0,5) x duración/24, no las primeras 24).
    """
    duration = float(duration_s or 0)
    if duration <= 0:
        return {"mode": "sample", "starts": []}
    if single_url or duration <= FULL_AUDIO_MAX_S:
        return {"mode": "full", "starts": []}
    return {"mode": "sample", "starts": _sample_starts(duration)}


def hype_points(text, lang="es"):
    """(puntos, risas, hype) de un texto transcrito.

    Mismos tokens que ``clips_chat_v2`` / ``clipagent.ingest``
    (risas + hype): cada palabra/frase/emoji de risa o hype
    suma 1. ``lang`` se acepta por simetría con el resto del
    pipeline; los tokens de los dos idiomas se comprueban
    siempre (la transcripción puede mezclarlos).
    """
    low = (text or "").lower()
    words = _WORD_RE.findall(low)
    risas = sum(1 for w in words if w in _LAUGH_WORDS)
    hype = sum(1 for w in words if w in _HYPE_WORDS)
    for phrase in _LAUGH_PHRASES:
        if phrase in low:
            risas += low.count(phrase)
    for phrase in _HYPE_PHRASES:
        if phrase in low:
            hype += low.count(phrase)
    for emoji in EMOJIS:
        if emoji in (text or ""):
            risas += (text or "").count(emoji)
    return risas + hype, risas, hype


def rms_buckets_10s(rms_list, offset_s=0, bucket_s=RMS_BUCKET_S):
    """RMS por segundo (dBFS) -> buckets de 10 s en amplitud
    lineal (media de los segundos del bucket).

    ``rms_list``: [(segundo_relativo, dbfs), ...] de
    ``rms_per_second``; ``offset_s`` es el inicio absoluto del
    audio en el vídeo. Devuelve [(inicio_bucket, rms_lineal)]
    ordenado. La curva para el múltiplo es distinta de la
    estadística por segundo para ``audio_component`` (dB, 1 s):
    ambas se calculan del mismo audio, no se confunden.
    """
    buckets = {}
    for rel_s, dbfs in (rms_list or []):
        try:
            second = float(rel_s) + float(offset_s)
            db = float(dbfs)
        except (TypeError, ValueError):
            continue
        linear = 10.0 ** (db / 20.0) if db > -90.0 else 0.0
        key = int(second // bucket_s) * bucket_s
        buckets.setdefault(key, []).append(linear)
    return sorted((t, sum(v) / len(v)) for t, v in buckets.items())


def audio_multiple(buckets, window_start, window_end):
    """Múltiplo de audio de una ventana candidata (§3.3).

    pico / mediana, con el pico = bucket RMS máximo DENTRO de la
    ventana candidata y la mediana = mediana de TODOS los
    buckets muestreados del VOD (los huecos entre muestras se
    excluyen: solo se usan los buckets muestreados). Si la
    mediana es 0 (silencio) o no hay buckets en la ventana, el
    múltiplo es 1,0 y el candidato se descarta por la puerta
    de §3.4. Devuelve (múltiplo, pico, mediana).
    """
    all_vals = [v for _, v in (buckets or [])]
    in_window = [v for t, v in (buckets or [])
                 if window_start <= t < window_end]
    if not all_vals or not in_window:
        return 1.0, 0.0, 0.0
    median = float(statistics.median(all_vals))
    peak = max(in_window)
    if median <= 0:
        return 1.0, peak, 0.0
    return peak / median, peak, median


def build_audio_input(rms_list, transcript):
    """Entrada de audio para ``score_clip`` (§3.3, bloqueador B4
    de la vuelta 3): ``{"stats": stats, "words": N}`` donde N es
    un NÚMERO.

    ``transcript_words`` devuelve una LISTA de palabras;
    ``audio_component`` espera un número (hace ``words == 0`` y
    ``words >= 50``): pasar la lista revienta con TypeError en
    cada clip. Por eso aquí va ``len(...)`` (como hace
    ``audio_verdict`` y ``review.py:290``).
    """
    stats = audio_stats(rms_list)
    return {"stats": stats,
            "words": len(transcript_words(transcript))}


def score_youtube_clip(rms_list, transcript, audio_multiple_value):
    """Viralidad de un clip de YouTube con el adaptador de §3.3.

    Llama a ``score_clip`` con ``detail=None,
    chat_multiple=None`` (el componente chat queda estimado en
    5,0 por el propio código) y el audio medido. El total es
    45 + valor_audio, con valor_audio en [2, 10] (plano -> 2, no
    0): el total es siempre 47-55. Sin RMS medible (sin pista de
    audio) levanta ValueError: el clip se descarta, no se estima.
    Devuelve (viral_score, viral_breakdown) con
    ``viral_breakdown["audio_multiple"]`` (el múltiplo vive por
    separado: ``score_clip`` no lo recibe).
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


def audio_peak_gate(audio_multiple_value, hype_pts, llm_selected=False):
    """Puerta de pico de audio (§3.4, solo YouTube; sustituye al
    ``filter_passed`` de chat, que será false sin detail y NO se
    usa como puerta).

    Pasa solo si ``audio_multiple >= 2,0`` Y (``hype_points > 0``
    o el LLM lo selecciona como momento). Devuelve
    (pasa, motivo).
    """
    if (audio_multiple_value or 0.0) < AUDIO_MULTIPLE_MIN:
        return False, (f"pico de audio {audio_multiple_value:.2f}x "
                       f"< {AUDIO_MULTIPLE_MIN:.1f}x")
    if hype_pts > 0 or llm_selected:
        return True, ""
    return False, "sin risas/hype en la muestra ni selección LLM"


def transcript_text(transcript):
    """Texto plano de una transcripción faster-whisper."""
    parts = []
    for seg in (transcript or {}).get("segments", []):
        text = (seg or {}).get("text") or ""
        if text.strip():
            parts.append(text.strip())
    return " ".join(parts)


def transcript_window_text(transcript, start_s, end_s):
    """Texto de los segmentos que solapan [start_s, end_s)."""
    parts = []
    for seg in (transcript or {}).get("segments", []):
        try:
            s0 = float(seg.get("start") or 0)
            s1 = float(seg.get("end") or s0)
        except (TypeError, ValueError):
            continue
        if s1 > start_s and s0 < end_s:
            text = (seg or {}).get("text") or ""
            if text.strip():
                parts.append(text.strip())
    return " ".join(parts)


# ---------------------------------------------------------------------------
# yt-dlp: metadatos, audio completo y muestras (bestaudio, nunca vídeo)
# ---------------------------------------------------------------------------

def fetch_youtube_metadata(vod_url, runner=None, sleep=None):
    """Metadatos con UN solo ``yt-dlp -J``: id, duración, título,
    canal, descripción, heatmap (si existe), vistas y likes."""
    cmd = (_ytdlp_cmd() + YTDLP_BASE_OPTS
           + ["--skip-download", "-J", vod_url])
    proc = _run_with_retries(cmd, runner or _default_runner, sleep)
    if proc.returncode != 0:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip()
        raise YoutubeError(
            f"yt-dlp -J falló sobre {vod_url} "
            f"(código {proc.returncode}): {tail[-300:]}")
    try:
        data = json.loads(proc.stdout or "")
    except ValueError as e:
        raise YoutubeError(
            f"yt-dlp devolvió JSON inválido sobre {vod_url}: {e}"
        ) from e
    if not isinstance(data, dict) or not data.get("id"):
        raise YoutubeError(f"yt-dlp -J sin id de vídeo sobre {vod_url}")
    try:
        duration = int(data.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    return {
        "id": str(data.get("id")),
        "duration": duration,
        "title": data.get("title") or "",
        "channel_url": (data.get("channel_url")
                        or data.get("uploader_url") or ""),
        "channel": (data.get("channel") or data.get("uploader") or ""),
        "description": data.get("description") or "",
        "heatmap": data.get("heatmap") or [],
        "views": data.get("view_count") or 0,
        "likes": data.get("like_count") or 0,
        "categories": data.get("categories") or [],
        "category": ((data.get("categories") or [None])[0] or ""),
    }


def _audio_cmd(vod_url, out_wav, start_s=None, end_s=None):
    """yt-dlp bestaudio -> WAV (muestra o audio completo).

    En el camino de muestreo solo se bajan muestras de 30 s de
    audio; en el camino <= 30 min se baja el audio completo
    (bestaudio, <= 50 MB), nunca el vídeo.
    """
    cmd = (_ytdlp_cmd() + YTDLP_BASE_OPTS
           + ["-f", "bestaudio", "-x", "--audio-format", "wav",
              "--max-filesize", f"{FULL_AUDIO_MAX_MB}M"])
    if start_s is not None and end_s is not None:
        sections = f"*{format_hms(start_s)}-{format_hms(end_s)}"
        cmd += ["--download-sections", sections]
    cmd += ["-o", str(out_wav), "--no-progress", vod_url]
    return cmd


def download_audio(vod_url, out_wav, start_s=None, end_s=None,
                   runner=None, sleep=None):
    """Baja audio (muestra de 30 s o completo) a WAV.

    Levanta ``AudioTooLarge`` si el audio supera 50 MB
    (stderr con «max-filesize» o tamaño tras la descarga): el
    llamador cae al camino de muestreo, no falla. Cualquier
    otro fallo de yt-dlp sigue el camino normal de reintentos
    (``YoutubeError``).
    """
    cmd = _audio_cmd(vod_url, out_wav, start_s, end_s)
    proc = _run_with_retries(cmd, runner or _default_runner, sleep)
    err = (proc.stderr or "") + (proc.stdout or "")
    if proc.returncode != 0:
        low = err.lower()
        if "max-filesize" in low or "larger than max" in low:
            raise AudioTooLarge(
                f"audio supera {FULL_AUDIO_MAX_MB} MB "
                f"({vod_url}): se usa muestreo")
        raise YoutubeError(
            f"yt-dlp bestaudio falló sobre {vod_url} "
            f"(código {proc.returncode}): {err.strip()[-300:]}")
    if not os.path.isfile(out_wav):
        raise YoutubeError(
            f"yt-dlp bestaudio no dejó archivo en {out_wav}")
    try:
        size = os.path.getsize(out_wav)
    except OSError:
        size = 0
    if size > FULL_AUDIO_MAX_MB * 1024 * 1024:
        try:
            os.unlink(out_wav)
        except OSError:
            pass
        raise AudioTooLarge(
            f"audio descargado supera {FULL_AUDIO_MAX_MB} MB "
            f"({size} bytes): se usa muestreo")
    return str(out_wav)


# ---------------------------------------------------------------------------
# Títulos + SEO (paquete viral del manifiesto)
# ---------------------------------------------------------------------------

def _resolve_recent_titles(bridge):
    """(recent_titles, dedupe_verified) desde el panel (R4.1).

    Sin bridge no hay lectura: (None, False). Si la lectura
    falla, fail-open solo en dedupe. El bridge lo pasa el
    llamador (run_youtube_clips / run_facebook_clips); sin él el
    VOD sigue con dedupe no verificado.
    """
    if bridge is None:
        return None, False
    try:
        return bridge.list_titles(days=30), True
    except Exception as e:  # noqa: BLE001 - dedupe no verificado
        _log(f"list_titles falló ({e}): dedupe no verificado")
        return None, False


def build_youtube_package(video_title, video_description,
                          transcript_txt, streamer, lang, risas,
                          hype, audio_multiple_value, bridge=None,
                          llm_client=None):
    """Paquete viral + SEO de un clip de YouTube.

    Camino endurecido (spec títulos-verdad, §6.4): el título, la
    descripción, los hashtags y la miniatura salen de
    ``package_clip`` con la ficha del momento CON la
    transcripción (checks R1–R4, dedupe contra el panel si hay
    bridge), nunca de ``suggest_titles``. El título del vídeo
    (metadato del creador) ya NO fija el título del clip: el
    título del clip nombra el momento concreto de ESE segmento;
    el título del vídeo queda en el manifiesto (nivel VOD).
    Sin transcripción no hay título honesto: paquete retenido
    (best_title None, needs_human_review True), nunca título de
    reglas. Devuelve {"viral", "title_es", "title_en", "seo_scores",
    "moment_card"}; "viral" trae además needs_human_review,
    dedupe_verified y pkg_source.
    """
    from .workers import vod_package_from_transcript

    from .metadata import score_package_seo

    streamer = (streamer or "").strip().lstrip("@") or "rubius"
    recent_titles, dedupe_verified = _resolve_recent_titles(bridge)
    viral, moment_card = vod_package_from_transcript(
        transcript_txt, streamer, lang, risas, hype,
        audio_multiple_value, game="GTA 6",
        recent_titles=recent_titles,
        dedupe_verified=dedupe_verified,
        llm_client=llm_client)
    viral["game"] = "GTA VI"
    moment_card = {"game": "GTA VI", "title": viral["best_title"],
                   "streamer": streamer}
    if viral.get("needs_human_review") or not viral.get("best_title"):
        return {"viral": viral, "title_es": "", "title_en": "",
                "desc_es": "", "desc_en": "",
                "seo_scores": score_package_seo(viral, lang=lang),
                "moment_card": moment_card}
    # Título/descripción del otro idioma: mismo momento, mismo
    # camino endurecido (sin título de reglas tampoco ahí).
    other = "en" if lang != "en" else "es"
    viral_other, _mc = vod_package_from_transcript(
        transcript_txt, streamer, other, risas, hype,
        audio_multiple_value, game="GTA 6",
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
# Defaults inyectables (render / validación / transcripción / RMS)
# ---------------------------------------------------------------------------

def _default_transcribe(path, model="tiny"):
    from .transcribe import transcribe_audio

    transcript, _info = transcribe_audio(path, model_name=model)
    return transcript or {"segments": []}


def _acepta_kw(fn, nombre):
    """True si fn acepta el kwarg `nombre` (o **kwargs).

    Las funciones inyectadas (tests, otros caminos) pueden tener
    la firma vieja sin el kwarg: en ese caso el llamador no lo
    pasa (compatibilidad), en vez de romper con TypeError — que
    el bucle tragaría como "render falló" / "sin veredicto".
    """
    try:
        import inspect
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return (nombre in params
            or any(p.kind == inspect.Parameter.VAR_KEYWORD
                   for p in params.values()))


def _default_render(seg_path, clip_path, duration, cfg,
                    layout_mode="auto"):
    """Render por defecto: mismo nombre y semántica de layout_mode
    que cut_clip_with_layout ("auto" decide por fotogramas;
    "game" es gameplay a pantalla completa, formato rubius sin
    webcam, spec §9.1)."""
    from .config import get_layout_config
    from .render import cut_clip_with_layout

    layout_cfg = get_layout_config(cfg or {})
    return cut_clip_with_layout(
        seg_path, 0, duration, clip_path,
        layout_mode=layout_mode, cfg=layout_cfg)


def _default_validate(clip_path, title, transcript_txt, cfg,
                      plantilla=None):
    """Validación por defecto: plantilla decidida (§2.1); sin ella
    el validador usa el default de config.yaml (rubius)."""
    from .final_review import validate_final_clip

    return validate_final_clip(
        clip_path, title, transcript_text=transcript_txt,
        srt_path="", cfg=cfg or {}, plantilla=plantilla)


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


def _llm_select_candidates(candidates):
    """Selección LLM de los candidatos (§3.4 b, --llm-select).

    ``candidates``: [{start, end, transcript, chat_score,
    chat_multiple, risas, hype}]. Nunca levanta: sin LLM o si
    falla, devuelve [] (la puerta se decide solo por hype).
    Devuelve el conjunto de inicios seleccionados.
    """
    if not candidates:
        return set()
    try:
        from .llm import get_llm_client
        from .select import select_moments

        client = get_llm_client()
        selections, used = select_moments(candidates, client)
        if not used or not selections:
            return set()
        chosen = set()
        for sel in selections:
            start = (sel or {}).get("start")
            if start is not None:
                chosen.add(int(start))
        return chosen
    except Exception:  # noqa: BLE001 - sin LLM no se bloquea
        return set()


# ---------------------------------------------------------------------------
# El cortador
# ---------------------------------------------------------------------------

def run_youtube_clips(vod_url, out_dir, top_n=2, lang="es",
                      single_url=False, llm_select=False,
                      metadata_fn=None, audio_fn=None,
                      sample_fn=None, transcribe_fn=None,
                      rms_fn=None, segment_fn=None,
                      render_fn=None, validate_fn=None,
                      titles_fn=None, layout_fn=None,
                      runner=None, bridge=None, llm_client=None):
    """Corta clips de un vídeo de YouTube (contrato §3.1).

    Devuelve el manifiesto (dict) o None si no se pudo ni leer
    el vídeo. Escribe SIEMPRE ``out_dir/manifest.json`` a disco
    cuando hay metadatos (aunque no salga ningún clip: el
    manifiesto documenta el motivo en ``descartados``).
    ``bridge`` (PanelBridge, opcional): si se pasa, el dedupe de
    títulos se lee del panel (R4.1); si no, dedupe_verified=False.

    Cada clip trae: start, duration, file (MP4 final
    renderizado), viral_score (dict de score_clip), seo_scores
    (motor local), moment_card {game, title}, transcript (audio
    real), source "youtube", viral {best_title, description,
    tags, thumbnail_text, viral_multiple}, title_es/title_en,
    source_key (id del vídeo), vod_id, vod_layout, n (índice del
    segmento; ``_rerender_vod_clip`` busca
    segments/seg_{n:02d}_t*.mp4), viral_breakdown
    {audio_multiple}, hype_score y score_kind "youtube_audio".
    Los segmentos descargados quedan en segments/ con el
    nombrado segments/seg_{n:02d}_t*.mp4.
    """
    lang = lang if lang in ("es", "en") else "es"
    if not is_youtube_url(vod_url):
        _log(f"no es URL de YouTube: {vod_url}")
        return None
    try:
        meta = (metadata_fn or fetch_youtube_metadata)(vod_url)
    except Exception as exc:  # noqa: BLE001 - sin metadatos no hay corte
        _log(f"sin metadatos de {vod_url}: {exc}")
        return None
    video_id = str(meta.get("id") or video_id_from_url(vod_url) or "")
    if not video_id:
        _log(f"sin id de vídeo en {vod_url}")
        return None
    duration = int(meta.get("duration") or 0)
    streamer = ((meta.get("channel") or "").strip().lstrip("@")
                or video_id)
    os.makedirs(out_dir, exist_ok=True)
    segments_dir = os.path.join(out_dir, "segments")
    clips_dir = os.path.join(out_dir, "clips")
    os.makedirs(segments_dir, exist_ok=True)
    os.makedirs(clips_dir, exist_ok=True)
    manifest = {
        "vod_id": video_id,
        "vod_url": vod_url,
        "source": "youtube",
        "lang": lang,
        "title": meta.get("title") or "",
        "duration": duration,
        "views": meta.get("views") or 0,
        "likes": meta.get("likes") or 0,
        "clips": [],
        "descartados": [],
        "vod_layout": None,
    }
    # Pre-chequeo de título GTA VI (fail open): solo se salta el
    # vídeo si CLARAMENTE no es GTA VI (título sin mención y
    # categoría conocida que no es GTA VI); ante la duda se
    # procesa y la puerta precisa es el juego del manifiesto.
    from .vod_fallback import vod_clearly_not_gta6

    skip = vod_clearly_not_gta6({
        "title": meta.get("title") or "",
        "category": meta.get("category") or ""})
    if skip:
        _log(f"vídeo {video_id} se salta ({skip})")
        manifest["descartados"].append(
            {"reason": skip, "vod_id": video_id})
        _write_manifest(out_dir, manifest)
        return manifest
    if duration < CLIP_MIN_DUR_S:
        # Banda < 40 s: ningún clip posible (resultado
        # documentado, no fallo).
        reason = (f"vídeo de {duration}s < {CLIP_MIN_DUR_S}s: "
                  "ningún clip posible")
        _log(reason)
        manifest["descartados"].append(
            {"reason": reason, "vod_id": video_id})
        _write_manifest(out_dir, manifest)
        return manifest
    try:
        from .config import load_config
        cfg = load_config()
    except Exception:  # noqa: BLE001 - sin config, defaults
        cfg = {}
    transcribe = transcribe_fn or _default_transcribe
    rms_measure = rms_fn or rms_per_second
    download_segment = segment_fn or download_youtube_segment
    render = render_fn or _default_render
    validate = validate_fn or _default_validate
    _render_kw = _acepta_kw(render, "layout_mode")
    _validate_kw = _acepta_kw(validate, "plantilla")
    titles = titles_fn or build_youtube_package
    layout_detect = layout_fn or _default_layout

    # --- Muestreo de audio (§3.2) -> muestras (inicio, texto,
    # puntos, buckets RMS) -----------------------------------------
    # Una muestra: {"start": t, "text": str, "points": int,
    # "risas": int, "hype": int, "buckets": [(t, lineal)]}.
    samples = []
    all_buckets = []
    audio_missing = False
    try:
        if duration <= CLIP_MAX_DUR_S:
            # Banda 40-60 s: el vídeo entero es el candidato
            # (sin detección de picos, directo a render/validar).
            wav = os.path.join(out_dir, "full_audio.wav")
            (audio_fn or download_audio)(vod_url, wav, runner=runner)
            try:
                transcript = transcribe(wav)
                rms = rms_measure(wav)
            except Exception as exc:  # noqa: BLE001 - sin audio
                # medible no hay corte, pero tampoco se tumba
                _log(f"audio completo sin transcripción/RMS ({exc})")
                manifest["descartados"].append(
                    {"reason": f"audio sin transcripción/RMS: {exc}",
                     "vod_id": video_id})
                _write_manifest(out_dir, manifest)
                return manifest
            text = transcript_text(transcript)
            points, risas, hype = hype_points(text, lang)
            if not rms:
                audio_missing = True
            else:
                all_buckets = rms_buckets_10s(rms, offset_s=0)
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
                        vod_url, wav, runner=runner)
                except AudioTooLarge:
                    _log("audio completo > 50 MB: se usa muestreo")
                    plan = {"mode": "sample",
                            "starts": _sample_starts(duration)}
                    wav = None
                else:
                    try:
                        transcript = transcribe(wav)
                        rms = rms_measure(wav)
                    except Exception as exc:  # noqa: BLE001 - sin
                        # audio medible no hay corte, pero no se tumba
                        _log(f"audio completo sin transcripción/RMS "
                             f"({exc})")
                        manifest["descartados"].append(
                            {"reason": "audio sin transcripción/RMS: "
                                      f"{exc}",
                             "vod_id": video_id})
                        _write_manifest(out_dir, manifest)
                        return manifest
                    if not rms:
                        audio_missing = True
                    else:
                        all_buckets = rms_buckets_10s(rms, offset_s=0)
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
                            vod_url, wav, start_s=t,
                            end_s=t + AUDIO_SAMPLE_S, runner=runner)
                    except AudioTooLarge:
                        continue
                    except Exception as exc:  # noqa: BLE001 - una
                        # muestra fallida no tumba el VOD
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
                    buckets = rms_buckets_10s(rms, offset_s=t)
                    all_buckets.extend(buckets)
                    text = transcript_text(transcript)
                    points, risas, hype = hype_points(text, lang)
                    samples.append({"start": t, "text": text,
                                    "points": points, "risas": risas,
                                    "hype": hype, "buckets": buckets,
                                    "transcript": transcript,
                                    "whole_video": False})
    except YoutubeError as exc:
        _log(f"audio de {video_id} falló: {exc}")
        manifest["descartados"].append(
            {"reason": f"audio falló: {exc}", "vod_id": video_id})
        _write_manifest(out_dir, manifest)
        return manifest
    if audio_missing or not samples:
        # Sin pista de audio en el vídeo -> descarte con motivo
        # (no hay RMS ni transcripción que medir).
        reason = ("sin pista de audio medible en el vídeo: "
                  "descarte")
        _log(reason)
        manifest["descartados"].append(
            {"reason": reason, "vod_id": video_id})
        _write_manifest(out_dir, manifest)
        return manifest
    all_buckets.sort()

    # --- Candidatos: ventana 40-60 s por muestra -------------------
    # Heatmap (solo señal opcional de apoyo): si existe, sus picos
    # afinan la ventana del candidato; si no está (caso del vídeo
    # de aceptación), el muestreo de audio decide solo.
    heat_peaks, _heat_reason = peaks_from_heatmap_data(meta)
    heat_ts = [p["t"] for p in heat_peaks]

    def _nearest_heat(t):
        if not heat_ts:
            return None
        best = min(heat_ts, key=lambda h: abs(h - t))
        return best if abs(best - t) <= 60 else None

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
        # muestreados del VOD.
        mult, _peak, _med = audio_multiple(
            all_buckets, start, window_end)
        near_heat = _nearest_heat(start + dur // 2)
        if near_heat is not None:
            start = max(0, min(near_heat - 10, duration - dur))
            window_end = start + dur
            mult, _peak, _med = audio_multiple(
                all_buckets, start, window_end)
        candidates.append({
            "start": int(start), "duration": int(dur),
            "points": sample["points"], "risas": sample["risas"],
            "hype": sample["hype"], "text": sample["text"],
            "audio_multiple": mult, "sample_start": sample["start"],
            "transcript": sample["transcript"],
        })
    # Selección LLM (opcional): marca los candidatos que el LLM
    # elige como momento (segunda vía de la puerta §3.4).
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
                "start": cand["start"], "vod_id": video_id})
    passing.sort(key=lambda c: (c["points"], c["audio_multiple"]),
                 reverse=True)
    # Sin solape entre candidatos: dos ventanas que se solapan
    # son el mismo momento (quedarse con la mejor).
    chosen = []
    for cand in passing:
        if any(abs(cand["start"] - other["start"]) < CLIP_MIN_DUR_S
               for other in chosen):
            continue
        chosen.append(cand)
        if len(chosen) >= top_n:
            break
    if not chosen:
        _log(f"vídeo {video_id}: ningún candidato pasó la puerta "
             "de pico de audio")
        _write_manifest(out_dir, manifest)
        return manifest

    # --- Corte + render + validación por candidato -----------------
    for idx, cand in enumerate(chosen, 1):
        start = cand["start"]
        dur = cand["duration"]
        seg_name = f"seg_{idx:02d}_t{_fmt_ts(start)}.mp4"
        seg_path = os.path.join(segments_dir, seg_name)
        try:
            download_segment(vod_url, start, start + dur, seg_path)
        except Exception as exc:  # noqa: BLE001 - un segmento no
            _log(f"segmento {idx} falló ({exc}); se sigue")
            manifest["descartados"].append({
                "reason": f"descarga del segmento falló: {exc}",
                "start": start, "vod_id": video_id})
            continue
        fname = (f"clip_{idx:02d}_t{_fmt_ts(start)}"
                 f"_s{cand['points']}_r{cand['risas']}"
                 f"h{cand['hype']}.mp4")
        clip_path = os.path.join(clips_dir, fname)
        # Decisión §2.1 (spec rubius sin webcam): sobre el segmento
        # descargado, antes del corte; sin facecam -> layout "game"
        # y validación contra rubius_sin_webcam.
        layout_mode = "auto"
        plantilla = None
        try:
            from .template_decision import decide_template_for_segment
            _dec = decide_template_for_segment(seg_path, dur, cfg=cfg)
            if _dec[0] == "rubius_sin_webcam":
                layout_mode = "game"
                plantilla = "rubius_sin_webcam"
        except Exception:  # noqa: BLE001 - sin decisión, auto
            pass
        try:
            render(seg_path, clip_path, dur, cfg,
                   **({"layout_mode": layout_mode}
                      if _render_kw else {}))
        except Exception as exc:  # noqa: BLE001 - sin render no hay clip
            _log(f"render {idx} falló ({exc}); se sigue")
            manifest["descartados"].append({
                "reason": f"render falló: {exc}",
                "start": start, "vod_id": video_id})
            continue
        if not os.path.isfile(clip_path):
            manifest["descartados"].append({
                "reason": "render sin archivo",
                "start": start, "vod_id": video_id})
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
            viral_score, breakdown = score_youtube_clip(
                seg_rms, seg_transcript, cand["audio_multiple"])
        except ValueError as exc:
            _log(f"segmento {idx} sin audio medible ({exc}); se salta")
            manifest["descartados"].append({
                "reason": str(exc), "start": start,
                "vod_id": video_id})
            continue
        pkg = titles(video_title=meta.get("title") or "",
                     video_description=meta.get("description") or "",
                     transcript_txt=seg_text, streamer=streamer,
                     lang=lang, risas=cand["risas"], hype=cand["hype"],
                     audio_multiple_value=cand["audio_multiple"],
                     bridge=bridge, llm_client=llm_client)
        viral = pkg["viral"]
        # Retención (spec títulos-verdad, §6.4): sin título honesto
        # el clip NO entra en el manifiesto (queda registrado en
        # descartados con el motivo, nunca con título de reglas).
        if viral.get("needs_human_review") or not viral.get(
                "best_title"):
            _reason = viral.get("hold_reason") or (
                "sin título honesto: retención para revisión")
            _log(f"clip {idx}: {_reason}")
            manifest["descartados"].append({
                "reason": _reason,
                "start": start, "vod_id": video_id})
            _write_manifest(out_dir, manifest)
            continue
        title = viral.get("best_title") or ""
        # Validador final sobre el render (mismo que Twitch). Un
        # veredicto «rechazado» no se guarda sin revisar los
        # fotogramas: el clip rechazado no entra en el manifiesto.
        try:
            verdict = validate(clip_path, title, seg_text, cfg,
                               **({"plantilla": plantilla}
                                  if _validate_kw else {}))
        except Exception:  # noqa: BLE001 - sin veredicto, pendiente
            verdict = {"decision": "pendiente_revision"}
        if (verdict or {}).get("decision") == "rechazado":
            _log(f"clip {idx}: validación final rechazado "
                 f"({(verdict or {}).get('motivo') or ''}); "
                 "revisión humana, no se guarda")
            manifest["descartados"].append({
                "reason": "validación final: "
                          f"{(verdict or {}).get('motivo') or ''}",
                "start": start, "vod_id": video_id})
            continue
        peak_ts = cand["sample_start"] + AUDIO_SAMPLE_S // 2
        clip_entry = {
            "n": idx,
            "file": f"clips/{fname}",
            "peak": float(peak_ts),
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
            "score_kind": "youtube_audio",
            "seo_scores": pkg["seo_scores"],
            "moment_card": pkg["moment_card"],
            "transcript": seg_transcript,
            "source": "youtube",
            "source_key": video_id,
            "vod_id": video_id,
            "vod_layout": None,
        }
        # Spec §9.3: la plantilla decidida (§2.1) viaja en el
        # manifiesto; vod_fallback la lee y valida con ella.
        if plantilla:
            clip_entry["plantilla"] = plantilla
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
            layout["vod_id"] = video_id
            manifest["vod_layout"] = layout
            for clip in manifest["clips"]:
                clip["vod_layout"] = layout
        _write_manifest(out_dir, manifest)
    _log(f"vídeo {video_id}: {len(manifest['clips'])} clip(s) "
         f"de {len(chosen)} candidato(s)")
    return manifest
