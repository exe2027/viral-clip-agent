"""Fuente viral: vídeos que YA fueron virales, re-hechos a rubius.

2026-11-21, orden del usuario: la fuente viral NO puede quedar
solo en memoria ni en una tanda manual — tiene que quedar
PROGRAMADA en el proyecto. Este módulo es esa fuente permanente:
descubre Shorts virales de GTA 6 en los canales de YouTube de los
streamers de la lista, los re-hace a rubius real, los valida
fotograma a fotograma con el mismo gate que el resto del pipeline
y los encola vía PanelBridge.

El patrón ya probado a mano es el del scooter (clip 172) y el del
52h de Speed: un reel/vídeo viral (House of Highlights, 375k
likes; VOD de Speed, 20,9 M vistas) -> re-make a rubius real ->
validación fotograma a fotograma -> cola del panel.

Alcance de la v1 (honesto):
- Solo SHORTS (config ``shorts_only``): el vídeo YA es el
  momento (15-60 s), no hay que buscarlo dentro de un VOD largo.
  Un VOD largo viral necesita encontrar el momento (heatmap de
  clipagent/heatmap.py): eso sigue siendo manual en v1.
- Solo YouTube (yt-dlp, $0, sin API key): Instagram necesita la
  sesión del usuario y no corre en Actions.
- Un vídeo va a UNA sola cuenta: los claims (``state_file``) son
  globales por vídeo -> tenant. Nunca el mismo vídeo en la cola
  de sebastian y en la de kaez.
- ES/EN separados por el idioma del streamer (la lista): el clip
  hereda ``language`` del streamer y cae en sus cuentas.
- Sin re-render automático en v1: el primer render ya usa la
  cara detectada en el Short; si la puerta dura pide re-render,
  el candidato queda para revisión humana con el motivo (nunca
  se encola un no-rubius y nunca se bloquea en silencio).
- Las puertas son las mismas del camino VOD (``clip_passes_gates``):
  viralidad >= ``scoring.viral_min`` (45) y SEO mínimo >=
  ``scoring.seo_gate`` (80) en las 4 plataformas. ``--force``
  las salta solo en un dispatch manual (el usuario pidió ese
  vídeo concreto).

Configuración: sección ``viral_source`` de config.yaml, ÚNICA
fuente (``load_viral_config`` falla fuerte si falta una clave).
El workflow es .github/workflows/viral-source.yml (cron diario
10:00 Chile + dispatch manual).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone

from .config import load_config
from .final_review import find_facecam_box_in_source, validate_final_clip
from .jobs import PLATFORMS
from .metadata import game_hashtags_tags
from .rubius import (
    make_banner_png,
    make_word_ass_from_transcript,
    render as rubius_render,
    split_banner_title,
)
from .seo_score import score_all_platforms
from .streamers import load_streamers
from .transcribe import generate_srt, transcribe_audio
from .validate import ConfigError
from .vod_fallback import panel_score
from .youtube import (
    MAX_ATTEMPTS,
    YTDLP_BASE_OPTS,
    YoutubeError,
    _run_with_retries,
    _ytdlp_cmd,
    download_youtube_segment,
    youtube_check,
    youtube_vod_is_gta6,
)

# El título del panel es el del vídeo viral (el gancho que ya
# funcionó). Tope de YouTube Shorts: 100 caracteres.
MAX_TITLE_CHARS = 100
# Payoff del SFX: mismo default que el camino automático
# (burn_and_enqueue): el validador solo exige presencia.
PAYOFF_S = 20.0

_VIRAL_REQUIRED = (
    "enabled", "min_views", "min_likes", "max_age_days",
    "max_candidates", "max_per_run", "shorts_only",
    "min_duration_s", "max_duration_s", "cooldown_days",
    "youtube_channels", "state_file",
)


def load_viral_config(cfg=None):
    """Config de la fuente viral desde config.yaml (única fuente).

    Falla fuerte (ConfigError) si falta la sección o una clave, o
    si un valor no tiene sentido (sin defaults silenciosos: un
    umbral inventado encolaría vídeos que no son virales o
    dejaría fuera los que sí).
    """
    cfg = cfg or load_config()
    section = cfg.get("viral_source")
    if not isinstance(section, dict):
        raise ConfigError(
            "viral_source: falta la sección 'viral_source' en "
            "config.yaml (la fuente viral no arranca sin sus "
            "criterios; sin defaults silenciosos)")
    missing = [k for k in _VIRAL_REQUIRED if k not in section]
    if missing:
        raise ConfigError(
            "viral_source: faltan claves en config.yaml: "
            + ", ".join(f"viral_source.{k}" for k in missing)
            + " (sin defaults silenciosos)")
    scoring = cfg.get("scoring")
    if not isinstance(scoring, dict):
        raise ConfigError(
            "scoring: falta la sección 'scoring' en config.yaml "
            "(la fuente viral necesita viral_min y seo_gate)")
    for key in ("viral_min", "seo_gate"):
        if key not in scoring:
            raise ConfigError(
                f"scoring.{key}: falta en config.yaml "
                "(la fuente viral no arranca sin sus puertas)")
    out = {
        "enabled": bool(section["enabled"]),
        "min_views": int(section["min_views"]),
        "min_likes": int(section["min_likes"]),
        "max_age_days": int(section["max_age_days"]),
        "max_candidates": int(section["max_candidates"]),
        "max_per_run": int(section["max_per_run"]),
        "shorts_only": bool(section["shorts_only"]),
        "min_duration_s": int(section["min_duration_s"]),
        "max_duration_s": int(section["max_duration_s"]),
        "cooldown_days": int(section["cooldown_days"]),
        "youtube_channels": list(section["youtube_channels"] or []),
        "state_file": str(section["state_file"]),
        "viral_min": int(scoring["viral_min"]),
        "seo_gate": int(scoring["seo_gate"]),
    }
    problems = []
    if out["min_views"] <= 0:
        problems.append("min_views debe ser > 0")
    if out["min_likes"] < 0:
        problems.append("min_likes debe ser >= 0")
    if out["max_age_days"] <= 0:
        problems.append("max_age_days debe ser > 0")
    if out["max_candidates"] < 1:
        problems.append("max_candidates debe ser >= 1")
    if out["max_per_run"] < 1:
        problems.append("max_per_run debe ser >= 1")
    if out["min_duration_s"] < 1:
        problems.append("min_duration_s debe ser >= 1")
    if out["max_duration_s"] <= out["min_duration_s"]:
        problems.append("max_duration_s debe ser > min_duration_s")
    if out["cooldown_days"] < 0:
        problems.append("cooldown_days debe ser >= 0")
    if not out["state_file"]:
        problems.append("state_file no puede estar vacío")
    if problems:
        raise ConfigError(
            "viral_source: valores inválidos en config.yaml: "
            + "; ".join(problems))
    return out


def youtube_streamers(streamers):
    """Streamers de la lista con canal de YouTube.

    Un streamer es fuente viral si su ``platform`` es youtube
    (``channel_url`` es el canal) o si trae ``youtube_url``. Solo
    ``denegado`` / ``revocado`` excluyen (regla del usuario:
    estar en la lista = permiso). Devuelve [{name, language,
    channel_url}] con language en minúsculas (\"es\" por defecto).
    """
    out = []
    for st in streamers or []:
        if not isinstance(st, dict):
            continue
        if str(st.get("estado") or "").lower() in ("denegado",
                                                   "revocado"):
            continue
        url = ""
        if str(st.get("platform") or "").lower() == "youtube":
            url = str(st.get("channel_url") or "")
        elif st.get("youtube_url"):
            url = str(st.get("youtube_url") or "")
        if not url or "youtube.com" not in url:
            continue
        name = str(st.get("name") or "").strip()
        if not name:
            continue
        lang = str(st.get("language") or "es").strip().lower()
        out.append({"name": name,
                    "language": lang if lang in ("es", "en") else "es",
                    "channel_url": url})
    return out


def _handle_from_channel_url(url):
    """Handle (@IShowSpeed) de una URL de canal, o \"\"."""
    url = (url or "").strip().rstrip("/")
    if "/@" in url:
        return url.rsplit("/@", 1)[-1].strip().lower()
    return ""


def discover_candidates(yt_streamers, vcfg, runner=None):
    """Shorts virales de GTA 6, rankeados por vistas (desc).

    Por cada streamer: ``youtube_check`` (sus /streams + /videos
    + /shorts, sin duplicar) -> solo GTA VI
    (``youtube_vod_is_gta6``) -> solo Shorts en rango de duración
    -> solo >= min_views. La pestaña /shorts no trae duración:
    sus entradas (``from_shorts_tab``) son Shorts por
    construcción y saltan el filtro de duración aquí; la duración
    real se verifica en ``enrich_candidate`` con la metadata
    completa. Sin fecha en las entradas del --flat-playlist no se
    filtra por edad aquí (el orden del listado es
    reciente-primero); la edad se verifica en
    ``enrich_candidate`` con la metadata completa.
    Devuelve la lista rankeada, capada a max_candidates.
    """
    found = []
    for st in yt_streamers or []:
        try:
            _live, vods = youtube_check(st["channel_url"],
                                        runner=runner,
                                        include_shorts_tab=True)
        except YoutubeError as exc:
            # Un canal caído (bot-check) no tumba la corrida.
            print(f"[viral-source] {st['name']}: sin listado ({exc})",
                  file=sys.stderr)
            continue
        for vod in vods or []:
            if vod.get("live"):
                continue
            if not youtube_vod_is_gta6(
                    vod, expected_channel=_handle_from_channel_url(
                        st["channel_url"])):
                continue
            dur = int(vod.get("duration_s") or 0)
            if (vcfg["shorts_only"]
                    and not vod.get("from_shorts_tab")
                    and not (vcfg["min_duration_s"] <= dur
                             <= vcfg["max_duration_s"])):
                continue
            views = int(vod.get("views") or 0)
            if views < vcfg["min_views"]:
                continue
            found.append({
                "video_id": str(vod.get("vod_id") or ""),
                "url": str(vod.get("url") or ""),
                "title": str(vod.get("title") or ""),
                "views": views,
                "likes": None,
                "duration_s": dur,
                "from_shorts_tab": bool(vod.get("from_shorts_tab")),
                "channel": str(vod.get("channel") or ""),
                "streamer": st["name"],
                "language": st["language"],
                "start_time": vod.get("start_time"),
            })
    found.sort(key=lambda c: (-c["views"], c["video_id"]))
    return found[:vcfg["max_candidates"]]


def fetch_video_metadata(video_url, runner=None, sleep=None):
    """Metadata completa de un vídeo de YouTube (yt-dlp -J).

    Devuelve el dict de yt-dlp o None si no se pudo leer (nunca
    levanta: un vídeo sin metadata se salta con motivo). Trae
    like_count, upload_date, duration, description y tags, que
    el --flat-playlist no trae. Reintenta hasta MAX_ATTEMPTS:
    el bot-check de YouTube es intermitente (verificado
    2026-11-21: el mismo vídeo fallaba 1 de cada 3 fetches
    y pasaba al reintentar) y sin reintento un candidato
    viral se perdería por un fallo transitorio.
    """
    cmd = (_ytdlp_cmd() + YTDLP_BASE_OPTS
           + ["--skip-download", "-J", "--no-playlist", video_url])
    run = runner or _default_runner
    try:
        proc = _run_with_retries(cmd, run, sleep, retries=MAX_ATTEMPTS)
    except Exception:  # noqa: BLE001 - sin metadata no hay candidato
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    try:
        data = json.loads(proc.stdout or "")
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _default_runner(cmd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=120)


def _parse_upload_date(value):
    """upload_date YYYYMMDD (o timestamp) -> datetime UTC o None."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value),
                                          tz=timezone.utc)
        text = str(value).strip()
        if len(text) == 8 and text.isdigit():
            return datetime.strptime(text, "%Y%m%d").replace(
                tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return None


def enrich_candidate(candidate, vcfg, runner=None, now=None, sleep=None):
    """Verifica likes y edad con la metadata completa.

    Devuelve el candidato enriquecido (likes, upload_date,
    duration real, description, tags) o None si no pasa
    min_likes / max_age_days / duración. Sin metadata no hay
    candidato (nunca se encola a ciegas).
    """
    meta = fetch_video_metadata(candidate["url"], runner=runner,
                                sleep=sleep)
    if meta is None:
        return None
    likes = meta.get("like_count")
    try:
        likes = int(likes) if likes is not None else 0
    except (TypeError, ValueError):
        likes = 0
    if likes < vcfg["min_likes"]:
        return None
    uploaded = _parse_upload_date(meta.get("upload_date"))
    if uploaded is not None:
        ref = now or datetime.now(timezone.utc)
        age_days = (ref - uploaded).total_seconds() / 86400.0
        if age_days > vcfg["max_age_days"]:
            return None
    try:
        dur = int(meta.get("duration") or candidate["duration_s"])
    except (TypeError, ValueError):
        dur = int(candidate["duration_s"] or 0)
    if vcfg["shorts_only"] and not (
            vcfg["min_duration_s"] <= dur <= vcfg["max_duration_s"]):
        return None
    enriched = dict(candidate)
    enriched.update({
        "likes": likes,
        "duration_s": dur,
        "upload_date": meta.get("upload_date"),
        "description": str(meta.get("description") or ""),
        "tags": [str(t) for t in (meta.get("tags") or [])
                 if isinstance(t, str)][:20],
        "channel_id": str(meta.get("channel_id") or ""),
        "uploader_id": str(meta.get("uploader_id") or ""),
    })
    return enriched


def viral_total_from_views(views, min_views):
    """Viralidad 0-100 desde las vistas del vídeo original.

    El vídeo ya demostró ser viral con espectadores reales: en
    min_views son 55 (pasa la puerta de 45), cada orden de
    magnitud suma 15 (1 M -> 70, 10 M -> 85, 100 M -> 100).
    Los likes son umbral (min_likes), no puntuación.
    """
    try:
        views = int(views or 0)
        base = int(min_views or 1)
    except (TypeError, ValueError):
        return 0
    if views <= 0 or base <= 0:
        return 0
    total = 55 + 15 * math.log10(max(views, 1) / base)
    return int(round(min(100, max(0, total))))


def load_claims(path):
    """Claims vídeo -> tenant desde el JSON de estado.

    Archivo corrupto o ausente = {} (se empieza de cero; el
    cooldown solo protege dentro de la ventana del archivo).
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def save_claims(path, claims):
    """Guarda los claims (se crea el directorio si hace falta)."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(claims, f, ensure_ascii=False, indent=1,
                  sort_keys=True)


def claim_video(claims, video_id, tenant, cooldown_days, now=None):
    """Reclama un vídeo para un tenant. True si es suyo.

    Un vídeo va a UNA sola cuenta: si otro tenant ya lo reclamó,
    False siempre. Si el mismo tenant ya lo procesó o lo saltó
    dentro del cooldown, False (no se reprocesa). Si no, se
    reclama (status \"claimed\") y True. ``now`` es un datetime
    UTC (tests); si no, ahora.
    """
    ref = now or datetime.now(timezone.utc)
    entry = claims.get(video_id)
    if entry is not None:
        if str(entry.get("tenant") or "") != tenant:
            return False
        if entry.get("status") in ("done", "skipped", "claimed"):
            claimed_at = _parse_upload_date(entry.get("claimed_at"))
            if claimed_at is None:
                try:
                    claimed_at = datetime.fromisoformat(
                        str(entry.get("claimed_at") or ""))
                except ValueError:
                    claimed_at = None
            if claimed_at is not None:
                if claimed_at.tzinfo is None:
                    claimed_at = claimed_at.replace(tzinfo=timezone.utc)
                age_days = (ref - claimed_at).total_seconds() / 86400.0
                if age_days < cooldown_days:
                    return False
    claims[video_id] = {
        "tenant": tenant,
        "claimed_at": ref.isoformat(),
        "status": "claimed",
        "panel_id": None,
    }
    return True


def _clean_title(title):
    """Título del panel: el del vídeo viral, limpio y <=100 chars."""
    text = " ".join(str(title or "").split())
    if len(text) > MAX_TITLE_CHARS:
        text = text[:MAX_TITLE_CHARS].rstrip()
    return text


def _transcript_text(transcript):
    """Texto plano del transcript (para el juez del validador)."""
    parts = []
    for seg in (transcript or {}).get("segments", []):
        text = (seg.get("text") or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def _description_first_lines(description, max_chars=500):
    """Primeras 2 líneas no vacías de la descripción del vídeo."""
    lines = [ln.strip() for ln in str(description or "").splitlines()
             if ln.strip()]
    text = "\n".join(lines[:2])
    return text[:max_chars]


def process_candidate(bridge, candidate, out_dir, cfg, vcfg, tenant,
                      force=False, dry_run=False):
    """Re-hace UN candidato a rubius real y lo encola.

    Descarga el Short entero -> transcribe (transcribe-only,
    nunca se inventa texto) -> ASS palabra por palabra -> cara
    detectada en el Short -> banner amarillo -> render rubius
    (con SFX por defecto vía cfg) -> validación final DEFINITIVA
    (plantilla rubius) -> SEO -> puertas -> PanelBridge.

    Devuelve (panel_id o None, motivo, report). ``panel_id`` es
    \"dry-run\" en dry-run (validado, no se encola). Un candidato
    que falla NUNCA tumba la corrida: el motivo queda en el
    report. ConfigError sí se propaga (falla en voz alta, como
    en el resto del pipeline).
    """
    video_id = str(candidate.get("video_id") or "")
    streamer = str(candidate.get("streamer") or "")
    lang = str(candidate.get("language") or "es")
    if lang not in ("es", "en"):
        lang = "es"
    try:
        duration = int(candidate.get("duration_s") or 0)
    except (TypeError, ValueError):
        duration = 0
    title = _clean_title(candidate.get("title") or "")
    report = {
        "video_id": video_id, "streamer": streamer,
        "language": lang, "url": candidate.get("url"),
        "views": candidate.get("views"),
        "likes": candidate.get("likes"),
        "duration_s": duration, "title": title,
    }
    if not video_id or not title or duration <= 0:
        return None, "candidato incompleto (sin id, título o duración)", report
    cdir = os.path.join(out_dir, video_id)
    os.makedirs(cdir, exist_ok=True)
    try:
        segment = os.path.join(cdir, "segment.mp4")
        download_youtube_segment(candidate["url"], 0, duration,
                                 segment)
        transcript, _info = transcribe_audio(
            segment, language=lang, cfg=cfg)
        no_speech = bool((transcript or {}).get("no_speech"))
        srt_path = os.path.join(cdir, "subs.srt")
        has_srt, _n = generate_srt(
            transcript, 0, duration, srt_path,
            corrections=((cfg.get("subtitles") or {})
                         .get("gta6_corrections")))
        if not has_srt:
            srt_path = ""
        ass_text = make_word_ass_from_transcript(
            transcript, duration=duration)
        ass_path = os.path.join(cdir, "subs.ass")
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_text or "")
        cam_box = find_facecam_box_in_source(segment, duration,
                                             cfg=cfg)
        if cam_box is None:
            return None, ("sin webcam detectable en el vídeo: no se "
                          "puede re-hacer a rubius (la plantilla "
                          "exige la cara arriba)"), report
        banner_path = os.path.join(cdir, "banner.png")
        make_banner_png(title, banner_path)
        out_path = os.path.join(cdir, f"{video_id}_rubius.mp4")
        _dt, final_video = rubius_render(
            segment, 0, duration, ass_path, out_path,
            banner_png=banner_path, cam_box=cam_box,
            cfg=cfg, payoff_s=PAYOFF_S)
        review = validate_final_clip(
            final_video, title,
            transcript_text=_transcript_text(transcript),
            srt_path=srt_path or None, cfg=cfg, plantilla="rubius")
    except ConfigError:
        raise
    except Exception as exc:  # noqa: BLE001 - un candidato no tumba la corrida
        return None, f"re-make falló: {exc}", report
    report["validation"] = {
        k: review.get(k) for k in
        ("decision", "motivo", "fallos", "needs_rerender",
         "needs_human_review") if k in review}
    if review.get("decision") == "rechazado" or review.get(
            "needs_rerender"):
        return None, (
            "validación final: "
            f"{review.get('motivo') or ''} — revisión humana "
            "(v1 no re-renderiza automáticamente)"), report
    # SEO del paquete (título + descripción + tags del vídeo
    # viral, juego verificado: youtube_vod_is_gta6 ya pasó).
    line1, _line2 = split_banner_title(title)
    thumb_text = line1 or ""
    description = _description_first_lines(
        candidate.get("description") or "")
    tags = list(candidate.get("tags") or [])
    pkg = {
        "title": title,
        "description": description,
        "tags": tags,
        "thumbnail_text": thumb_text or None,
        "hashtags": game_hashtags_tags("GTA VI"),
        "game_verified": True,
        "visible": "cams+gameplay",
        "lang": lang,
        "keyword": "GTA 6",
    }
    seo_scores = score_all_platforms(pkg)
    total = viral_total_from_views(candidate.get("views") or 0,
                                   vcfg["min_views"])
    seo_min = min((s or {}).get("score", 0)
                  for s in seo_scores.values())
    report["viral_total"] = total
    report["seo_scores"] = {p: (s or {}).get("score")
                            for p, s in seo_scores.items()}
    gates_ok = (total >= vcfg["viral_min"]
                and seo_min >= vcfg["seo_gate"])
    if not gates_ok and not force:
        return None, (
            f"puertas: viralidad {total} (mín. {vcfg['viral_min']}), "
            f"SEO mínimo {seo_min} (mín. {vcfg['seo_gate']}): "
            "no se encola"), report
    if dry_run:
        return "dry-run", "dry-run: validado, no se encola", report
    if bridge is None:
        return None, "sin bridge del panel: no se puede encolar", report
    seo_txt = ", ".join(
        f"{p} {(seo_scores.get(p) or {}).get('score')}"
        for p in PLATFORMS)
    payload = {
        "title": title,
        "start_sec": 0,
        "end_sec": duration,
        "score": panel_score(total, seo_scores, 1.0),
        "platforms": list(PLATFORMS),
        "influencer_id": streamer,
        # Los IDs de YouTube son texto; el panel guarda
        # source_id entero: 0 + la URL y las vistas en `reason`.
        "source_id": 0,
        "seo_title": title,
        "seo_description": description,
        "seo_tags": tags,
        "subtitles_complete": 0 if no_speech else 1,
        "needs_human_transcription": 0,
        "origin": "viral",
        "game": "GTA VI",
        "language": lang,
        "reason": (f"Viral YouTube ({streamer}, {candidate['url']}, "
                   f"{candidate.get('views') or 0} vistas, "
                   f"{candidate.get('likes') or 0} likes, {lang}) — "
                   f"viralidad {total} — SEO {seo_txt}"
                   + (" — sin subtítulos (sin voz: se publica igual, "
                      "pasa puertas)" if no_speech else
                      " — subtítulos completos")),
    }
    if thumb_text:
        payload["thumbnail_text"] = thumb_text
    try:
        clip = bridge.create_clip(payload)
        bridge.upload_video(clip["id"], final_video)
    except Exception as exc:  # noqa: BLE001 - el fallo queda en el report
        return None, f"encolar en el panel falló: {exc}", report
    return clip["id"], "", report


def _match_streamer_for_url(meta, yt_streamers):
    """Streamer de la lista dueño del canal de un vídeo (o None).

    Compara el handle del canal (uploader_id / channel) con el
    handle de la URL de cada streamer. Sin match no hay permiso
    (estar en la lista = permiso ya dado).
    """
    candidates = set()
    for key in ("uploader_id", "channel", "uploader"):
        value = str(meta.get(key) or "").strip().lower()
        if value:
            candidates.add(value.lstrip("@"))
    for st in yt_streamers or []:
        handle = _handle_from_channel_url(st["channel_url"])
        if handle and handle in candidates:
            return st
        name = st["name"].strip().lower()
        if name and name in candidates:
            return st
    return None


def run_viral_source(bridge, cfg, out_dir, tenant, video_url=None,
                     dry_run=False, force=False, runner=None,
                     streamers_path=None):
    """Una corrida de la fuente viral. Devuelve el report (dict).

    Modo un vídeo (--video-url): procesa ese Short concreto
    (su canal tiene que ser un streamer de la lista). Modo
    descubrimiento (sin URL): lista los canales de YouTube de
    la lista, rankea, enriquece los top y procesa hasta
    max_per_run. En dry-run los claims solo viven en memoria
    (un dry-run no marca vídeos como procesados: bloquearía la
    corrida real durante el cooldown); en live se guardan en
    ``state_file`` tras cada candidato.
    """
    vcfg = load_viral_config(cfg)
    os.makedirs(out_dir, exist_ok=True)
    report = {
        "tenant": tenant,
        "modo": "video" if video_url else "descubrimiento",
        "dry_run": bool(dry_run),
        "descubiertos": 0,
        "enriquecidos": 0,
        "reclamados": 0,
        "procesados": [],
        "encolados": [],
        "saltados": [],
    }
    if not vcfg["enabled"] and not force:
        report["motivo"] = "viral_source.enabled es false en config.yaml"
        return report
    if bridge is not None:
        try:
            streamers = load_streamers(bridge=bridge)
        except Exception as exc:  # noqa: BLE001 - el panel manda; sin lista no hay corrida
            report["motivo"] = f"sin lista de streamers del panel: {exc}"
            return report
    else:
        try:
            if streamers_path:
                streamers = load_streamers(path=streamers_path)
            else:
                streamers = load_streamers()
        except Exception as exc:  # noqa: BLE001 - sin lista no hay corrida
            report["motivo"] = f"sin lista de streamers: {exc}"
            return report
    yt_streamers = youtube_streamers(streamers)
    if vcfg["youtube_channels"]:
        override = [{"name": _handle_from_channel_url(u) or u,
                     "language": "es", "channel_url": u}
                    for u in vcfg["youtube_channels"]]
        yt_streamers = override
    if not yt_streamers:
        report["motivo"] = ("sin streamers con canal de YouTube en "
                            "la lista (ni en viral_source.youtube_channels)")
        return report
    claims_path = os.path.join(out_dir, vcfg["state_file"])
    claims = load_claims(claims_path)
    # En dry-run los claims solo viven en memoria: un dry-run no
    # puede marcar vídeos como procesados (bloquearía la corrida
    # real 7 días) ni subirse como artefacto de estado.
    persist_claims = not dry_run
    candidates = []
    if video_url:
        meta = fetch_video_metadata(video_url, runner=runner)
        if meta is None:
            report["saltados"].append({
                "video_id": video_url,
                "motivo": "sin metadata de YouTube (yt-dlp -J falló)"})
            return report
        st = _match_streamer_for_url(meta, yt_streamers)
        if st is None:
            report["saltados"].append({
                "video_id": str(meta.get("id") or video_url),
                "motivo": ("el canal del vídeo no está en la lista "
                           "de streamers (sin permiso)")})
            return report
        try:
            dur = int(meta.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0
        candidates = [{
            "video_id": str(meta.get("id") or ""),
            "url": video_url,
            "title": str(meta.get("title") or ""),
            "views": int(meta.get("view_count") or 0),
            "likes": None,
            "duration_s": dur,
            "channel": str(meta.get("channel") or ""),
            "streamer": st["name"],
            "language": st["language"],
            "start_time": None,
        }]
        report["descubiertos"] = 1
        enriched = enrich_candidate(candidates[0], vcfg, runner=runner)
        if enriched is None:
            report["saltados"].append({
                "video_id": candidates[0]["video_id"],
                "motivo": ("no pasa min_likes / max_age_days / "
                           "duración de Short")})
            return report
        candidates = [enriched]
        report["enriquecidos"] = 1
    else:
        discovered = discover_candidates(yt_streamers, vcfg,
                                         runner=runner)
        report["descubiertos"] = len(discovered)
        for cand in discovered:
            enriched = enrich_candidate(cand, vcfg, runner=runner)
            if enriched is None:
                report["saltados"].append({
                    "video_id": cand["video_id"],
                    "motivo": ("no pasa min_likes / max_age_days / "
                               "duración de Short")})
                continue
            candidates.append(enriched)
        report["enriquecidos"] = len(candidates)
    for cand in candidates:
        if len(report["encolados"]) >= vcfg["max_per_run"]:
            report["saltados"].append({
                "video_id": cand["video_id"],
                "motivo": f"tope max_per_run={vcfg['max_per_run']} "
                          "de la corrida"})
            continue
        if not claim_video(claims, cand["video_id"], tenant,
                           vcfg["cooldown_days"]):
            owner = (claims.get(cand["video_id"]) or {}).get("tenant")
            report["saltados"].append({
                "video_id": cand["video_id"],
                "motivo": ("ya reclamado por "
                           f"{owner or 'otro tenant'}: un vídeo va "
                           "a UNA sola cuenta")})
            continue
        report["reclamados"] += 1
        if persist_claims:
            save_claims(claims_path, claims)
        panel_id, motivo, cand_report = process_candidate(
            bridge, cand, out_dir, cfg, vcfg, tenant,
            force=force, dry_run=dry_run)
        cand_report["panel_id"] = panel_id
        cand_report["motivo"] = motivo
        report["procesados"].append(cand_report)
        if panel_id is not None:
            claims[cand["video_id"]]["status"] = "done"
            claims[cand["video_id"]]["panel_id"] = panel_id
            report["encolados"].append({
                "video_id": cand["video_id"],
                "panel_id": panel_id})
        else:
            claims[cand["video_id"]]["status"] = "skipped"
        if persist_claims:
            save_claims(claims_path, claims)
    return report


def main(argv=None):
    """CLI: python -m clipagent.viral_source --tenant sebastian ..."""
    parser = argparse.ArgumentParser(
        description="Fuente viral: Shorts virales de GTA 6 -> "
                    "rubius real -> cola del panel.")
    parser.add_argument("--tenant", required=True,
                        help="tenant del panel (sebastian, kaez)")
    parser.add_argument("--out-dir", default=None,
                        help="directorio de la corrida "
                             "(por defecto runs/viral-source-<fecha>)")
    parser.add_argument("--video-url", default=None,
                        help="un Short concreto (dispatch manual)")
    parser.add_argument("--dry-run", action="store_true",
                        help="descubre, re-hace y valida sin encolar")
    parser.add_argument("--force", action="store_true",
                        help="salta las puertas de viralidad/SEO "
                             "(solo dispatch manual)")
    parser.add_argument("--config", default=None,
                        help="ruta a config.yaml (por defecto la del repo)")
    parser.add_argument("--streamers", default=None,
                        help="ruta a streamers.yaml (por defecto la "
                             "del panel si hay bridge, si no la del repo)")
    args = parser.parse_args(argv)
    from .config import load_config as _load_config
    cfg = _load_config(args.config) if args.config else _load_config()
    out_dir = args.out_dir or os.path.join(
        "runs", "viral-source-" + datetime.now(
            timezone.utc).strftime("%Y-%m-%d"))
    bridge = None
    if not args.dry_run:
        from .panel_bridge import PanelBridge
        bridge = PanelBridge.from_env()
        if bridge is None:
            print("[viral-source] sin credenciales del panel "
                  "(PANEL_USERNAME/PANEL_PASSWORD o PANEL_BRAIN_TOKEN); "
                  "usa --dry-run o define el entorno",
                  file=sys.stderr)
            return 2
    else:
        # En dry-run el bridge solo lee (la lista de streamers del
        # panel); si no hay credenciales se usa streamers.yaml.
        try:
            from .panel_bridge import PanelBridge
            bridge = PanelBridge.from_env()
        except Exception as exc:  # noqa: BLE001 - sin panel, lista del repo
            print(f"[viral-source] sin bridge del panel ({exc}); "
                  "lista de streamers.yaml", file=sys.stderr)
            bridge = None
    env_tenant = os.environ.get("PANEL_TENANT", "").strip()
    if env_tenant and env_tenant != args.tenant:
        print("[viral-source] PANEL_TENANT del entorno "
              f"({env_tenant}) no coincide con --tenant "
              f"({args.tenant})", file=sys.stderr)
        return 2
    report = run_viral_source(
        bridge, cfg, out_dir, args.tenant,
        video_url=args.video_url, dry_run=args.dry_run,
        force=args.force, streamers_path=args.streamers)
    report_path = os.path.join(out_dir, "report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1,
                  default=str)
    print(f"[viral-source] tenant={args.tenant} "
          f"modo={report['modo']} dry_run={args.dry_run} "
          f"descubiertos={report['descubiertos']} "
          f"encolados={len(report['encolados'])} "
          f"saltados={len(report['saltados'])}")
    for skip in report["saltados"]:
        print(f"  salto {skip['video_id']}: {skip['motivo']}")
    for done in report["encolados"]:
        print(f"  encolado {done['video_id']}: panel {done['panel_id']}")
    if report.get("motivo"):
        print(f"[viral-source] {report['motivo']}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
