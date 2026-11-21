"""Recuperación de la fuente VOD de clips en vivo sin ``source_id``.

Los clips cortados en vivo por el cerebro no guardaron el VOD de
origen (``source_id`` 0/NULL en D1): el cortador en vivo graba el
directo y el VOD no quedó registrado. Sin VOD, el pipeline de
re-hecho a rubius (``remake_all.py``) no puede re-hacerlos: 109 de
los 118 bloqueados están en ese caso (2026-11-20).

Método (verificado con datos reales, no asumido):

1. **Corrida que lo cortó.** Cada clip se mapea a su cut-job de
   GitHub Actions (``Cerebro — Live Cutter`` / ``cerebro-live.yml``,
   job ``Cortar en vivo (<streamer>)``) por influencer + ``created_at``
   dentro de la ventana del job. El paso ``Seguir directo`` de ese
   job marca el inicio de la sesión de corte.
2. **T0.** El detector se construye ~10 s después del inicio de ese
   paso (``run_live_cut``: construcción del detector, gate, y
   ``recorder.start()``). T0 = ``step_started_at + 10 s``.
3. **VOD que cubre el clip.** Por el login de Twitch del streamer
   (``streamers.yaml``), la lista de VODs (``yt-dlp --flat-playlist``)
   y el VOD cuyo ``[created_at, created_at + duration]`` cubre el
   ``created_at`` del clip.
4. **Offset candidato.** ``candidato = T0 + start_sec - 12 - VOD.created_at``
   (12 s = latencia HLS asumida por el cortador, ``DEFAULT_LATENCY_S``).
   La latencia HLS real varía por stream (medido: ~10 s noni,
   ~25 s Rubius), así que el candidato tiene un error de ~±15 s.
5. **Offset exacto por audio.** Se baja el tramo de audio del VOD
   alrededor del candidato (``TwitchDownloaderCLI -q audio_only``,
   ~9 s por clip) y el vídeo del clip (panel o R2), y se hace
   correlación cruzada FFT entre ambos audios. El pico da el offset
   exacto (error medido ≤ 0,05 s) con una correlación y un ratio
   frente al segundo pico que separan sin ambigüedad un match real
   de un dudoso.

Calibración (2026-11-20, verificada a mano):

- Clip 7 (VOD conocido 2932415021, start 3200): recuperado 3199,98 s
  (corr 0,988, ratio 48,1) — exacto.
- Clip 155 (noni, en vivo): candidato 40546,0 s, exacto 40543,95 s
  (corr 0,890, ratio 34,8) — error del candidato −2,05 s.
- Clip 158 (Rubius, en vivo): candidato 29549,0 s, exacto 29561,99 s
  (corr 0,993, ratio 31,4) — error del candidato +12,99 s.

Hallazgos que condicionan el diseño:

- ``yt-dlp --download-sections`` NO sirve para Twitch: descarga un
  rango de tiempo distinto al pedido (verificado: corr 0,010). Solo
  se usa yt-dlp para descubrir VODs (metadatos), nunca para bajar
  el segmento. El segmento se baja con TwitchDownloaderCLI.
- ``TwitchDownloaderCLI -q audio_only`` baja solo el audio del VOD
  (3,9 MB por 190 s en ~9 s) y el match da el mismo offset exacto
  que con el vídeo completo (40543,96 s vs 40543,95 s).
- PATCH /api/clips/:id NO acepta ``source_id``/``start_sec``/
  ``end_sec`` (allowlist: subtitle_style, publish_blocked,
  needs_human_transcription, subtitles_complete, game, language,
  seo_title...). La vía de escritura del mapa es la API de D1
  (UPDATE), NO el panel. Este script NO escribe en D1: genera el
  mapa + el SQL, sin ejecutarlo.

Salida:

- ``source-map.json``: ``{id: {source_id, start_sec, end_sec}}``
  (mismo formato que el dry-run de ``remake_all.py``). SOLO entran
  recuperaciones con confianza ALTA; lo dudoso NO entra (nunca se
  re-hace sobre una fuente dudosa).
- ``report.json``: por clip, el veredicto (recuperado / dudoso /
  sin_vod), la confianza, el método, el run/job/VOD usados y el
  motivo. Un clip sin VOD localizable queda ``sin_vod`` definitivo
  y se sigue; nunca se inventa una fuente ni un offset.
- ``update-d1.sql``: los UPDATE generados (NO ejecutados) para
  aplicar el mapa. OJO: D1 en el plan gratis tiene límite diario
  de escrituras (error 7500; se libera a medianoche UTC, 21:00
  -03). Aplicar en lote y, si da 7500, reintentar tras el reset.

Nada de este módulo escribe en D1, sube nada al panel ni toca el
panel-backend. Todo vive en el cerebro (regla del usuario
2026-11-20 00:04).

Invocación (local, verificación de los 109):

  python -m clipagent.recover_vod_source \\
      --from-json runs/vod-recovery-2026-11-20/blocked-live.json \\
      --runs-json runs/vod-recovery-2026-11-20/runs-jobs.json \\
      --clip-video r2 --out-map runs/vod-recovery-2026-11-20/source-map.json \\
      --out-report runs/vod-recovery-2026-11-20/report.json \\
      --out-sql runs/vod-recovery-2026-11-20/update-d1.sql

En Actions (primera corrida real del re-hecho) el vídeo del clip
se baja por el panel (``--clip-video panel``, con la sesión del
bootstrap) y los VODs se descubren igual con yt-dlp (hay que
instalarlo en el workflow: no está en pyproject.toml).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

import numpy as np

# -- Modelo de tiempo del cortador en vivo (live_cutter.py) --------
LATENCY_S = 12.0        # DEFAULT_LATENCY_S: latencia HLS asumida
D_OFFSET_S = 10.0       # detector t0 ≈ inicio del paso + 10 s
JOB_START_FALLBACK_S = 75.0  # sin paso "Seguir directo": job + 75 s
JOB_MARGIN_S = 300      # created_at puede caer hasta 5 min tras el job
SEARCH_HALF_S = 30      # ventana de búsqueda del audio (±30 s)
SEARCH_HALF_WIDE_S = 90  # reintento ancho si el primero no casa
SEARCH_HALF_XWIDE_S = 180  # último reintento (red de seguridad)
CLIP_DURATION_S = 40    # duración del clip (40-60 s; el match usa la real)

# -- Confianza del match de audio (calibrada: buenos ≥0,87/≥31;
#    dudoso 0,018/1,2; clip 14: corr 0,984 pero ratio 2,97 por
#    música/sonido en bucle) ---------------------------------------
CONF_ALTA_CORR = 0.6
CONF_ALTA_RATIO = 8.0
CONF_ALTA_CORR_ABS = 0.85  # corr tan alto es concluyente solo
CONF_MEDIA_CORR = 0.4
CONF_MEDIA_RATIO = 3.0

R2_ACCOUNT = "27afc37225a02fd92557f901c05dabee"
R2_BUCKET = "clips-avc"


class RecoverError(Exception):
    """Fallo de infraestructura del recuperador (no del clip)."""


def _parse_iso(ts):
    """ISO 8601 (con Z) -> epoch segundos (float) o None."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(
            str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _fmt_ts(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


# -- Audio: correlación cruzada FFT ---------------------------------
def load_audio_mono_16k(path):
    """Decodifica un audio/vídeo a mono 16 kHz float32 (ffmpeg)."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "16000",
         "-f", "f32le", "-"],
        capture_output=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RecoverError(
            f"ffmpeg no pudo leer {path}: "
            f"{proc.stderr.decode('utf-8', 'ignore')[:200]}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def match_audio(clip_path, vod_path, vod_seg_start):
    """Correlación cruzada FFT entre el clip y el tramo de VOD.

    Devuelve {lag, correlation, second_best, ratio, offset}: el
    offset VOD exacto del inicio del clip (``vod_seg_start + lag``).
    La correlación se normaliza por la energía LOCAL del VOD en la
    ventana que solapa con el clip (suma acumulada de cuadrados):
    un match perfecto da ~1,0 sea cual sea la longitud del tramo,
    y un audio sin relación da ~0. El ratio es mejor/segundo pico
    (el segundo se busca fuera de ±1 s del mejor, para que un pico
    ancho no se cuente a sí mismo).
    """
    clip = load_audio_mono_16k(clip_path).astype(np.float64)
    vod = load_audio_mono_16k(vod_path).astype(np.float64)
    if len(clip) == 0 or len(vod) == 0:
        raise RecoverError(f"audio vacío: {clip_path} o {vod_path}")
    if len(vod) < len(clip):
        raise RecoverError(
            f"el tramo de VOD ({len(vod) / 16000:.0f}s) es más corto "
            f"que el clip ({len(clip) / 16000:.0f}s)")
    n = 1 << int(np.ceil(np.log2(len(clip) + len(vod))))
    corr = np.fft.irfft(
        np.conj(np.fft.rfft(clip, n)) * np.fft.rfft(vod, n), n)
    # Solo lags con solape completo (el clip cabe entero en el tramo).
    max_lag = len(vod) - len(clip)
    corr = corr[: max_lag + 1]
    clip_e = float(np.sum(clip ** 2))
    if clip_e == 0:
        raise RecoverError(f"audio sin energía: {clip_path}")
    # Energía local del VOD en cada ventana [lag, lag+len(clip)].
    cumsum = np.concatenate([[0.0], np.cumsum(vod ** 2)])
    idx = np.arange(max_lag + 1)
    vod_e = cumsum[idx + len(clip)] - cumsum[idx]
    denom = np.sqrt(clip_e * np.maximum(vod_e, 1e-12))
    corr = corr / denom
    lag_i = int(np.argmax(corr))
    best = float(corr[lag_i])
    # Segundo pico fuera de ±1 s del mejor (16 000 muestras/s).
    masked = corr.copy()
    lo = max(0, lag_i - 16000)
    hi = min(len(masked), lag_i + 16000)
    masked[lo:hi] = 0
    second = float(np.max(np.abs(masked))) if len(masked) else 0.0
    ratio = best / second if second > 0 else float("inf")
    lag = lag_i / 16000.0
    return {
        "lag": lag,
        "correlation": best,
        "second_best": second,
        "ratio": ratio,
        "offset": vod_seg_start + lag,
    }


def confidence(match):
    """'alta' | 'media' | 'dudosa' según corr y ratio del match.

    Un corr >= 0,85 es concluyente por sí solo: la forma de onda
    coincide casi muestra a muestra (el candidato ya acota la
    búsqueda a ±30/90 s, así que no es casualidad). El ratio solo
    importa con corr moderado: un segundo pico alto puede ser
    música o un sonido en bucle que se repite en el directo
    (clip 14: corr 0,984, ratio 2,97 -> alta).
    """
    corr = match["correlation"]
    ratio = match["ratio"]
    if corr >= CONF_ALTA_CORR_ABS:
        return "alta"
    if corr >= CONF_ALTA_CORR and ratio >= CONF_ALTA_RATIO:
        return "alta"
    if corr >= CONF_MEDIA_CORR and ratio >= CONF_MEDIA_RATIO:
        return "media"
    return "dudosa"


# -- Streamer -> login de Twitch ------------------------------------
def twitch_login(streamer, streamers=None):
    """Login de Twitch de un influencer (``streamers.yaml``).

    ``streamers`` es la lista de ``clipagent.streamers.load_streamers``
    (o None para cargarla). El login sale de ``channel_url``; si el
    streamer no está en la lista, se normaliza el nombre (minúsculas,
    sin espacios) y se deja constancia de que es una suposición.
    """
    if streamers is None:
        try:
            from .streamers import load_streamers

            streamers = load_streamers()
        except Exception:  # noqa: BLE001 - sin lista, normalizar
            streamers = []
    name = (streamer or "").strip()
    norm = "".join(name.lower().split())
    for entry in streamers or []:
        if not isinstance(entry, dict):
            continue
        entry_norm = "".join((entry.get("name") or "").lower().split())
        if entry_norm != norm:
            continue
        if (entry.get("platform") or "").lower() != "twitch":
            return None, (
                f"el influencer {name!r} no es de Twitch "
                f"(plataforma {entry.get('platform')!r})"
            )
        url = (entry.get("channel_url") or "").rstrip("/")
        login = url.rsplit("/", 1)[-1].lower() if url else ""
        if login:
            return login, ""
        return None, f"sin channel_url en streamers.yaml para {name!r}"
    return norm, "supuesto por normalización (no está en streamers.yaml)"


# -- Corridas de GitHub (runs-jobs.json) ----------------------------
def fetch_runs_via_gh(desde_iso, hasta_iso, workflow="cerebro-live.yml",
                      repo="exe2027/viral-clip-agent"):
    """Jobs+steps de las corridas cerebro-live en un rango (API gh).

    Devuelve la lista de runs con sus cut-jobs (mismo formato que
    runs-jobs.json). Necesita la CLI ``gh`` autenticada (en Actions
    viene preinstalada con GITHUB_TOKEN).
    """
    def gh(*args):
        proc = subprocess.run(
            ["gh", "api"] + list(args),
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            raise RecoverError(
                f"gh api {' '.join(args)} falló: {proc.stderr[:300]}")
        return json.loads(proc.stdout)

    d0 = datetime.fromisoformat(desde_iso.replace("Z", "+00:00"))
    d1 = datetime.fromisoformat(hasta_iso.replace("Z", "+00:00"))
    runs = []
    page = 1
    while True:
        data = gh(f"repos/{repo}/actions/workflows/{workflow}/runs"
                  f"?per_page=100&page={page}")
        batch = data.get("workflow_runs") or []
        if not batch:
            break
        runs.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    out_runs = []
    for r in runs:
        c = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        if not (d0 <= c <= d1):
            continue
        rid = r["id"]
        jobs = gh(f"repos/{repo}/actions/runs/{rid}/jobs?per_page=30")
        cuts = []
        for j in jobs.get("jobs") or []:
            name = j.get("name") or ""
            if not name.startswith("Cortar en vivo"):
                continue
            step = None
            for s in j.get("steps") or []:
                if s.get("name", "").startswith("Seguir directo"):
                    step = {"started_at": s.get("started_at"),
                            "completed_at": s.get("completed_at"),
                            "conclusion": s.get("conclusion")}
            cuts.append({
                "job_id": j.get("id"),
                "name": name,
                "streamer": name[len("Cortar en vivo ("):-1],
                "conclusion": j.get("conclusion"),
                "started_at": j.get("started_at"),
                "completed_at": j.get("completed_at"),
                "step_seguir": step,
            })
        out_runs.append({
            "run_id": rid,
            "created_at": r.get("created_at"),
            "conclusion": r.get("conclusion"),
            "event": r.get("event"),
            "head_sha": (r.get("head_sha") or "")[:7],
            "cuts": cuts,
        })
    return out_runs


def runs_for_clips(clips):
    """Rango de corridas que cubre los clips (±1 día) + fetch."""
    stamps = []
    for clip in clips:
        try:
            stamps.append(float(clip.get("created_at")) / 1000.0)
        except (TypeError, ValueError):
            continue
    if not stamps:
        raise RecoverError(
            "sin created_at en los clips: no se puede saber qué "
            "corridas buscar")
    desde = _fmt_ts(min(stamps) - 86400)
    hasta = _fmt_ts(max(stamps) + 86400)
    return fetch_runs_via_gh(desde, hasta)


def map_clip_to_job(clip, runs):
    """(run, job) que cortó el clip, o (None, motivo).

    El job es ``Cortar en vivo (<streamer>)`` cuyo ``[started_at,
    completed_at + 300 s]`` contiene el ``created_at`` del clip.
    Si varios encajan, se prefiere el que tiene el paso
    ``Seguir directo`` y contiene el clip dentro de su ventana
    estricta; si sigue habiendo varios, es ambiguo (no se adivina).
    """
    streamer = "".join((clip.get("streamer") or "").lower().split())
    created_ms = clip.get("created_at")
    if created_ms is None:
        return None, "sin created_at en el registro del clip"
    try:
        created = float(created_ms) / 1000.0
    except (TypeError, ValueError):
        return None, f"created_at inválido: {created_ms!r}"
    candidates = []
    for run in runs or []:
        for job in run.get("cuts") or []:
            job_streamer = "".join(
                (job.get("streamer") or "").lower().split())
            if job_streamer != streamer:
                continue
            # La ventana es la del PASO "Seguir directo" (es el que
            # corta), no la del job: el clip no puede existir antes
            # de que el paso empiece a cortar. Sin paso, la del job.
            step = job.get("step_seguir") or {}
            start = _parse_iso(step.get("started_at")) or _parse_iso(
                job.get("started_at"))
            end = _parse_iso(job.get("completed_at"))
            if start is None:
                continue
            if end is None:
                end = start + 3600
            if start <= created <= end + JOB_MARGIN_S:
                strict = start <= created <= end
                has_step = bool(step.get("started_at"))
                candidates.append((run, job, strict, has_step))
    if not candidates:
        return None, (
            f"ningún cut-job de {streamer!r} cubre created_at "
            f"{_fmt_ts(created)}"
        )
    if len(candidates) == 1:
        run, job, _s, _h = candidates[0]
        return (run, job), ""
    strict = [c for c in candidates if c[2]]
    if len(strict) == 1:
        run, job, _s, _h = strict[0]
        return (run, job), ""
    pool = strict or candidates
    with_step = [c for c in pool if c[3]]
    if len(with_step) == 1:
        run, job, _s, _h = with_step[0]
        return (run, job), ""
    runs_ids = sorted({c[0].get("run_id") for c in pool})
    return None, (
        f"ambiguo: {len(pool)} cut-jobs de {streamer!r} cubren "
        f"created_at {_fmt_ts(created)} (runs {runs_ids})"
    )


def t0_from_job(job):
    """T0 (epoch) de la sesión de corte + si es aproximado.

    T0 = inicio del paso 'Seguir directo' + 10 s. Sin el paso,
    T0 = inicio del job + 75 s (aproximado: el setup tarda ~60-90 s).
    """
    step = job.get("step_seguir") or {}
    step_start = _parse_iso(step.get("started_at"))
    if step_start is not None:
        return step_start + D_OFFSET_S, False
    job_start = _parse_iso(job.get("started_at"))
    if job_start is None:
        return None, True
    return job_start + JOB_START_FALLBACK_S, True

# -- Descubrimiento de VODs (yt-dlp, solo metadatos) -----------------
def _ytdlp():
    """Ruta a yt-dlp (PATH o junto al intérprete del venv)."""
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        cand = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            ytdlp = cand
    if not ytdlp:
        raise RecoverError(
            "yt-dlp no está en el PATH: instálalo (pip install yt-dlp)")
    return ytdlp


def _ytdlp_json(url, timeout=120, extra_args=None):
    """``yt-dlp -J <url>`` -> dict (o None si falla o expira).

    Un timeout o un fallo de red devuelve None (no lanza): el
    llamador marca el clip como dudoso (reintentable), no como
    sin VOD. Un timeout NO es "no hay VOD".
    """
    ytdlp = _ytdlp()
    try:
        proc = subprocess.run(
            [ytdlp, "-J", "--no-warnings"] + (extra_args or []) + [url],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def _vod_entries(login):
    """Lista de VODs del canal (id, título) más recientes primero.

    Solo los 15 más recientes (``--playlist-end 15``): el VOD del
    clip es de hace 1-2 días y el canal puede tener cientos de
    VODs (kaicenat: la lista completa tarda >120 s en bajar).
    """
    data = _ytdlp_json(
        f"https://www.twitch.tv/{login}/videos",
        timeout=300, extra_args=["--flat-playlist", "--playlist-end", "15"])
    if not data:
        return None
    entries = data.get("entries") or []
    vods = []
    for e in entries:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        vod_id = str(e["id"]).lstrip("v")
        vods.append({"vod_id": vod_id,
                     "title": e.get("title") or ""})
    return vods


def _vod_meta(vod_id):
    """{created_at (epoch), duration (s), title} de un VOD, o None."""
    data = _ytdlp_json(f"https://www.twitch.tv/videos/{vod_id}")
    if not data:
        return None
    created = data.get("timestamp")
    duration = data.get("duration")
    if created is None or duration is None:
        return None
    # El id numérico (sin la "v" de la lista) es el que va en D1.
    num_id = str(data.get("id") or vod_id).lstrip("v")
    return {"vod_id": num_id,
            "created_at": float(created),
            "duration": float(duration),
            "title": data.get("title") or ""}


def find_covering_vod(login, clip_created_at, cache, max_check=8):
    """VOD cuyo [created_at, created_at+duration] cubre el clip.

    ``cache`` es {login: [vods con created_at/duration]} y se
    actualiza en memoria (el llamador lo persiste). Se miran los
    VODs más recientes primero (el del clip es de hace 1-2 días);
    si ninguno cubre, no hay VOD (expirado o sin cobertura).
    Devuelve (vod, motivo_si_no_hay).
    """
    vods = cache.get(login)
    if vods is None:
        vods = _vod_entries(login)
        if vods is None:
            return None, (
                f"no se pudo listar los VODs de twitch.tv/{login} "
                f"(yt-dlp)"
            )
        cache[login] = vods
    checked = 0
    for vod in vods:
        if checked >= max_check:
            break
        if vod.get("created_at") is None:
            meta = _vod_meta(vod["vod_id"])
            if meta is None:
                vod["created_at"] = -1  # no reintentarlo en este batch
                vod["duration"] = 0
                continue
            vod.update(meta)
        created = vod.get("created_at") or -1
        duration = vod.get("duration") or 0
        if created < 0:
            continue
        checked += 1
        if created - 60 <= clip_created_at <= created + duration + 60:
            return vod, ""
    return None, (
        f"ningún VOD de twitch.tv/{login} cubre "
        f"{_fmt_ts(clip_created_at)} "
        f"(revisados {checked} VODs recientes)"
    )


# -- Descargas -------------------------------------------------------
def _twitch_cli():
    """Ruta a TwitchDownloaderCLI (PATH o el del repo)."""
    found = shutil.which("TwitchDownloaderCLI")
    if found:
        return found
    for cand in (
        os.path.expanduser("~/workspace/tools/twitchdownloader/"
                           "TwitchDownloaderCLI"),
        "/usr/local/bin/TwitchDownloaderCLI",
    ):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    raise RecoverError(
        "TwitchDownloaderCLI no está en el PATH ni en "
        "~/workspace/tools/twitchdownloader/")


def download_vod_audio(vod_id, start_sec, end_sec, out_path):
    """Baja [start_sec, end_sec] del VOD, SOLO audio.

    ``-q audio_only`` es ~9 s por clip (3,9 MB/190 s) y el match
    da el mismo offset exacto que con el vídeo completo. NO usar
    yt-dlp --download-sections: descarga un rango distinto al
    pedido (verificado, corr 0,010).

    ``--collision Overwrite`` es OBLIGATORIO: si el archivo de
    salida ya existe (reintento, corrida anterior matada),
    TwitchDownloaderCLI pregunta [O]verwrite/[R]ename/[E]xit y se
    queda esperando la entrada para siempre (el subproceso no
    tiene stdin). Sin ese flag, un reintento se cuelga.
    """
    cli = _twitch_cli()
    if os.path.exists(out_path):
        os.remove(out_path)
    proc = subprocess.run(
        [cli, "videodownload", "--id", str(vod_id),
         "-b", f"{max(0, int(start_sec))}s",
         "-e", f"{int(end_sec)}s",
         "-q", "audio_only", "--collision", "Overwrite",
         "-o", out_path],
        capture_output=True, text=True, timeout=240,
    )
    if proc.returncode != 0 or not os.path.isfile(out_path):
        raise RecoverError(
            f"TwitchDownloaderCLI audio_only falló (VOD {vod_id} "
            f"[{start_sec}s, {end_sec}s]): "
            f"{(proc.stderr or proc.stdout or '')[-300:]}")
    return out_path


def _curl_get(url, max_time=60):
    """GET con curl y límite duro de reloj (--max-time).

    ``urllib.request.urlopen(timeout=...)`` NO es un límite duro:
    si el servidor/proxy gotea datos, la lectura se puede quedar
    colgada ~10 min (clips 124 y 159: el playlist HLS no llegaba
    nunca). curl --max-time sí corta a los N segundos, pase lo
    que pase. Devuelve los bytes o lanza RecoverError.
    """
    try:
        proc = subprocess.run(
            ["curl", "-s", "-f", "--max-time", str(max_time), url],
            capture_output=True, timeout=max_time + 30)
    except subprocess.TimeoutExpired:
        raise RecoverError(f"curl --max-time {max_time}s agotado: {url}")
    if proc.returncode != 0:
        raise RecoverError(
            f"curl {url} -> rc {proc.returncode}: "
            f"{(proc.stderr or b'')[:200]!r}")
    return proc.stdout


def download_vod_audio_hls(vod_id, start_sec, end_sec, out_path):
    """Fallback HLS por segmentos para VODs ENORMES.

    TwitchDownloaderCLI se cuelga al pedir el info de un VOD de
    40+ h (clip 24: VOD 2932305259, 40,5 h — «A task was
    canceled» en Fetching Video Info). Este fallback NO usa el
    CLI: saca la URL HLS con yt-dlp -g, baja el playlist .m3u8,
    descarga SOLO los segmentos .ts que cubren
    [start_sec, end_sec] y los concatena. Verificado con el
    clip 24: corr 0,981 -> alta (offset 88122,96 s).

    El seg_start se ancla con el OFFSET ACUMULADO del playlist
    (suma de EXTINF desde el primer segmento): esa es la timeline
    de TwitchDownloaderCLI (segundo 0 = primer fotograma del VOD),
    que es la que usará el re-hecho. NO anclar por
    #EXT-X-PROGRAM-DATE-TIME menos created_at: el created_at del
    VOD (yt-dlp) puede ir ~4 s después del primer segmento (clip
    24: 3,7 s), y ese anclaje deja el offset 3,7 s corto. Devuelve
    (out_path, seg_start) donde seg_start es el offset VOD del
    primer segmento descargado.
    """
    ytdlp = _ytdlp()
    proc = subprocess.run(
        [ytdlp, "-g", "-f", "bestaudio", "--no-warnings",
         f"https://www.twitch.tv/videos/{vod_id}"],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RecoverError(
            f"yt-dlp -g falló para el VOD {vod_id}: "
            f"{(proc.stderr or '')[-200:]}")
    hls_url = proc.stdout.strip().splitlines()[0]
    base = hls_url.rsplit("/", 1)[0] + "/"

    try:
        playlist = _curl_get(hls_url, max_time=90).decode(
            "utf-8", "replace")
    except RecoverError as exc:
        raise RecoverError(f"no se pudo bajar el playlist HLS del "
                           f"VOD {vod_id}: {exc}")

    # Parsear segmentos: (nombre, duración, offset acumulado).
    # Los segmentos pueden ser .ts (MPEG-TS) o .mp4 (fMP4, con
    # #EXT-X-MAP init). El offset acumulado es la timeline de
    # TwitchDownloaderCLI (segundo 0 = primer fotograma).
    segs = []
    init_uri = None
    dur = None
    acc = 0.0
    for ln in playlist.splitlines():
        if ln.startswith("#EXT-X-MAP:"):
            m = re.search(r'URI="([^"]+)"', ln)
            if m:
                init_uri = m.group(1)
        elif ln.startswith("#EXTINF:"):
            try:
                dur = float(ln.split(":")[1].split(",")[0])
            except ValueError:
                dur = 10.0
        elif ln.endswith(".ts") or ln.endswith(".mp4"):
            segs.append((ln, dur or 10.0, acc))
            acc += dur or 10.0
            dur = None
    if not segs:
        raise RecoverError(f"playlist HLS del VOD {vod_id} sin segmentos")

    # Segmentos que cubren [start_sec, end_sec] (por offset acumulado)
    first = None
    last = None
    for i, (_name, _d, s0) in enumerate(segs):
        s1 = s0 + _d
        if first is None and s1 > start_sec:
            first = i
        if s0 < end_sec:
            last = i
    if first is None or last is None:
        raise RecoverError(
            f"rango [{start_sec}s, {end_sec}s] fuera del playlist "
            f"del VOD {vod_id} ({len(segs)} segmentos)")
    # Offset acumulado desde el primer segmento (timeline de
    # TwitchDownloaderCLI). vod_created_at no se usa: el created_at
    # puede ir segundos después del primer segmento.
    seg_start = segs[first][2]

    if os.path.exists(out_path):
        os.remove(out_path)
    with open(out_path, "wb") as out:
        # fMP4: el init (#EXT-X-MAP) va primero, si no el archivo
        # no se puede decodificar.
        if init_uri:
            try:
                out.write(_curl_get(base + init_uri, max_time=60))
            except RecoverError as exc:
                raise RecoverError(f"init {init_uri} del VOD {vod_id} "
                                   f"no descargable: {exc}")
        for name, _d, _s in segs[first:last + 1]:
            try:
                out.write(_curl_get(base + name, max_time=60))
            except RecoverError as exc:
                raise RecoverError(f"segmento {name} del VOD {vod_id} "
                                   f"no descargable: {exc}")
    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        raise RecoverError(f"HLS del VOD {vod_id} vacío")
    return out_path, seg_start


def download_clip_video_panel(bridge, clip_id, out_path):
    """Baja el vídeo del clip por el panel (GET /api/clips/:id/video)."""
    bridge.download_video(clip_id, out_path)
    return out_path


def download_clip_video_r2(r2_key, out_path):
    """Baja el vídeo del clip de R2 (solo local: usa el conector
    de Cloudflare de esta VM; en Actions usar --clip-video panel)."""
    try:
        sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
        from dynamic_credentials import add_surrogate_to_request
    except ImportError as exc:
        raise RecoverError(
            "descarga R2 solo disponible en esta VM (falta el "
            f"conector de Cloudflare: {exc})")
    import urllib.request

    url = (f"https://api.cloudflare.com/client/v4/accounts/{R2_ACCOUNT}"
           f"/r2/buckets/{R2_BUCKET}/objects/{r2_key}")
    req = urllib.request.Request(url, method="GET")
    add_surrogate_to_request(req, "custom.cloudflare",
                             allowed_hosts=["api.cloudflare.com"])
    # Reintento: la API de R2 devuelve HTTP 500 transitorios
    # (clips 27-29 de la corrida: 3 seguidos, luego se recupera).
    last_exc = None
    for intento in range(3):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                if resp.status != 200:
                    raise RecoverError(f"R2 {r2_key} -> HTTP {resp.status}")
                with open(out_path, "wb") as f:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
            return out_path
        except RecoverError:
            raise
        except Exception as exc:  # noqa: BLE001 - HTTP 500, timeout...
            last_exc = exc
            if intento < 2:
                import time
                time.sleep(10)
    raise RecoverError(f"R2 {r2_key} -> {last_exc}")


# -- Offset candidato ------------------------------------------------
def candidate_offset(clip, t0, vod):
    """Offset VOD candidato + motivo si es imposible.

    candidato = T0 + start_sec - LATENCY_S - VOD.created_at.
    ``start_sec`` es relativo al recorder (el cortador lo calcula
    con la latencia asumida de 12 s); la latencia real varía, así
    que el candidato tiene un error de ~±15 s que el match de
    audio corrige.
    """
    try:
        start = int(clip.get("start_sec"))
        end = int(clip.get("end_sec"))
    except (TypeError, ValueError):
        return None, "start_sec/end_sec inválidos en el registro"
    if end <= start:
        return None, (
            f"rango inválido en el registro: start_sec={start} "
            f"end_sec={end}")
    duration = end - start
    cand = t0 + start - LATENCY_S - vod["created_at"]
    if cand < 0 or cand + duration > vod["duration"]:
        return None, (
            f"offset candidato {cand:.1f}s fuera del VOD "
            f"{vod['vod_id']} (duración {vod['duration']:.0f}s, "
            f"clip [{start}s, {end}s])"
        )
    return cand, ""


def find_latest_vod_before(login, clip_created_at, cache, max_hours=12):
    """VOD más reciente que terminó antes de ``clip_created_at``.

    Para el clip cuya fila se creó tarde (el directo ya había
    terminado cuando se creó la fila): el VOD es el último que
    terminó antes de la creación (como mucho ``max_hours`` antes).
    Devuelve (vod, motivo_si_no_hay).
    """
    vods = cache.get(login)
    if vods is None:
        vods = _vod_entries(login)
        if vods is None:
            return None, (
                f"no se pudo listar los VODs de twitch.tv/{login} "
                f"(yt-dlp)")
        cache[login] = vods
    best = None
    for vod in vods:
        if vod.get("created_at") is None:
            meta = _vod_meta(vod["vod_id"])
            if meta is None:
                vod["created_at"] = -1
                vod["duration"] = 0
                continue
            vod.update(meta)
        created = vod.get("created_at") or -1
        duration = vod.get("duration") or 0
        if created < 0:
            continue
        ends = created + duration
        if ends <= clip_created_at + 60 and \
                ends >= clip_created_at - max_hours * 3600:
            if best is None or created > best["created_at"]:
                best = vod
        # La lista va de más reciente a más antiguo: si el VOD
        # terminó hace más de max_hours, los siguientes son peores.
        if ends < clip_created_at - max_hours * 3600:
            break
    if best is None:
        return None, (
            f"ningún VOD de twitch.tv/{login} terminó en las "
            f"{max_hours} h antes de {_fmt_ts(clip_created_at)}")
    return best, ""


def _jobs_in_vod(streamer, vod, runs):
    """Cut-jobs del streamer cuya sesión cae dentro del VOD."""
    norm = "".join((streamer or "").lower().split())
    start = vod["created_at"]
    end = vod["created_at"] + vod["duration"]
    jobs = []
    for run in runs or []:
        for job in run.get("cuts") or []:
            if "".join((job.get("streamer") or "").lower().split()) != norm:
                continue
            step = job.get("step_seguir") or {}
            t = _parse_iso(step.get("started_at")) or _parse_iso(
                job.get("started_at"))
            if t is None:
                continue
            if start <= t <= end:
                jobs.append((run, job))
    jobs.sort(key=lambda rj: _parse_iso(
        (rj[1].get("step_seguir") or {}).get("started_at") or
        rj[1].get("started_at") or ""))
    return jobs


# -- Un clip ---------------------------------------------------------
def _build_attempts(clip, ctx, login, created):
    """Intentos (run, job, vod, candidato, t0_aprox) para un clip.

    Camino primario: el cut-job que cubre ``created_at`` + el VOD
    que cubre el clip. Si no hay job que cubra (la fila se creó
    tarde, tras terminar el directo), fallback: el VOD más
    reciente que terminó antes de la creación + TODOS los cut-jobs
    del streamer dentro de ese VOD (cada uno es un candidato; el
    match de audio decide cuál es el bueno). Devuelve
    (intentos, motivo_si_no_hay).
    """
    (run_job, motivo) = map_clip_to_job(clip, ctx["runs"])
    if run_job is not None:
        run, job = run_job
        t0, t0_aprox = t0_from_job(job)
        if t0 is None:
            return [], (
                f"run {run.get('run_id')}: sin inicio de job ni de paso")
        vod, motivo = find_covering_vod(login, created, ctx["vod_cache"])
        if vod is None:
            return [], motivo
        cand, motivo = candidate_offset(clip, t0, vod)
        if cand is None:
            return [], motivo
        return [(run, job, vod, cand, t0_aprox)], ""
    # Fallback: fila creada tarde (o job no listado). Primero el
    # VOD que cubre created_at; si la creación cae fuera de todo
    # VOD (el directo ya había terminado), el VOD más reciente
    # que terminó antes de la creación.
    vod, motivo = find_covering_vod(login, created, ctx["vod_cache"])
    if vod is None:
        vod, motivo = find_latest_vod_before(
            login, created, ctx["vod_cache"])
    if vod is None:
        return [], (
            f"sin cut-job que cubra created_at "
            f"{_fmt_ts(created)} y {motivo}")
    jobs = _jobs_in_vod(clip.get("streamer"), vod, ctx["runs"])
    if not jobs:
        return [], (
            f"sin cut-job que cubra created_at "
            f"{_fmt_ts(created)} y ningún job de "
            f"{clip.get('streamer')!r} dentro del VOD {vod['vod_id']}")
    attempts = []
    for run, job in jobs:
        t0, t0_aprox = t0_from_job(job)
        if t0 is None:
            continue
        cand, _mot = candidate_offset(clip, t0, vod)
        if cand is None:
            continue
        attempts.append((run, job, vod, cand, t0_aprox))
    if not attempts:
        return [], (
            f"sin cut-job que cubra created_at "
            f"{_fmt_ts(created)} y ningún candidato válido dentro "
            f"del VOD {vod['vod_id']}")
    return attempts, ""


def recover_one(clip, ctx):
    """Recupera la fuente VOD de un clip. Devuelve el registro.

    ``ctx``: {runs, vod_cache, streamers, work_dir, clip_video,
    bridge, keep_files}. El registro lleva outcome
    (recuperado/dudoso/sin_vod), confianza, método y motivo.
    Nunca inventa una fuente: sin match de confianza, dudoso o
    sin_vod. Con varios intentos (fallback), se queda con el
    primer match de confianza alta; si ninguno la tiene, el mejor
    (media) queda como dudoso para revisión humana.
    """
    cid = clip.get("id")
    base = {"id": cid, "title": clip.get("title") or "",
            "streamer": clip.get("streamer") or ""}
    work_dir = ctx["work_dir"]
    os.makedirs(work_dir, exist_ok=True)
    clip_path = os.path.join(work_dir, f"clip_{cid}.mp4")
    vod_path = os.path.join(work_dir, f"vod_{cid}.mp4")

    def fail(outcome, motivo, extra=None):
        rec = {**base, "outcome": outcome, "motivo": motivo}
        if extra:
            rec.update(extra)
        return rec

    def cleanup():
        if not ctx.get("keep_files"):
            for p in (clip_path, vod_path):
                try:
                    os.remove(p)
                except OSError:
                    pass

    # 1. Plataforma: solo Twitch tiene descargador de VOD aquí.
    platform = (clip.get("platform") or "").lower()
    if platform and platform != "twitch":
        return fail("sin_vod",
                    f"plataforma {platform!r}: sin descargador de VOD "
                    f"en este recuperador (solo Twitch)")
    # 2. Login de Twitch del streamer.
    login, login_nota = twitch_login(clip.get("streamer"),
                                     ctx.get("streamers"))
    if not login:
        return fail("sin_vod", login_nota)
    # 3. created_at + intentos (primario o fallback).
    try:
        created = float(clip["created_at"]) / 1000.0
    except (TypeError, ValueError):
        return fail("dudoso", "created_at inválido")
    try:
        duration = int(clip["end_sec"]) - int(clip["start_sec"])
    except (TypeError, ValueError):
        return fail("dudoso", "start_sec/end_sec inválidos")
    if duration <= 0:
        return fail("dudoso",
                    f"rango inválido: start_sec={clip.get('start_sec')} "
                    f"end_sec={clip.get('end_sec')}")
    attempts, motivo = _build_attempts(clip, ctx, login, created)
    if not attempts:
        # Sin VOD localizable -> sin_vod; sin job o sin poder
        # listar los VODs (transitorio) -> dudoso (reintentable).
        if "ningún VOD" in motivo:
            return fail("sin_vod", motivo)
        return fail("dudoso", motivo)
    # 4. Vídeo del clip (una sola vez, se reutiliza en intentos).
    try:
        if ctx["clip_video"] == "panel":
            download_clip_video_panel(ctx["bridge"], cid, clip_path)
        else:
            r2_key = clip.get("video_r2_key")
            if not r2_key:
                return fail("dudoso",
                            "sin video_r2_key: no se puede bajar el vídeo "
                            "del clip para el match de audio")
            download_clip_video_r2(r2_key, clip_path)
    except RecoverError as exc:
        return fail("dudoso", f"no se pudo bajar el vídeo del clip: {exc}")
    # 5. Audio del VOD + match por intento (primero ±30 s, luego
    #    ±90 s, luego ±180 s como red de seguridad). El primer
    #    match de confianza alta gana. Si TwitchDownloaderCLI falla
    #    (VOD enorme), se usa el fallback HLS por segmentos.
    best_media = None
    best_dudosa = None
    download_errors = []
    for run, job, vod, cand, t0_aprox in attempts:
        match = None
        for half in (SEARCH_HALF_S, SEARCH_HALF_WIDE_S,
                     SEARCH_HALF_XWIDE_S):
            seg_start = max(0, cand - half)
            seg_end = cand + duration + half
            actual_seg_start = seg_start
            try:
                download_vod_audio(vod["vod_id"], seg_start, seg_end,
                                   vod_path)
            except RecoverError as exc:
                download_errors.append(str(exc))
                try:
                    _p, actual_seg_start = download_vod_audio_hls(
                        vod["vod_id"], seg_start, seg_end, vod_path)
                except RecoverError as exc2:
                    download_errors.append(str(exc2))
                    continue
            try:
                match = match_audio(clip_path, vod_path, actual_seg_start)
                break
            except RecoverError as exc:
                download_errors.append(str(exc))
                continue
        if match is None:
            continue
        conf = confidence(match)
        extra = {
            "confianza": conf,
            "metodo": "audio",
            "run_id": run.get("run_id"),
            "job": job.get("name"),
            "vod_id": int(vod["vod_id"]),
            "candidato": round(cand, 2),
            "offset": round(match["offset"], 2),
            "correlacion": round(match["correlation"], 3),
            "ratio": round(match["ratio"], 2),
            "t0_aproximado": t0_aprox,
            "login_twitch": login,
            "intentos": len(attempts),
        }
        if login_nota:
            extra["login_nota"] = login_nota
        if conf == "alta":
            start = int(round(match["offset"]))
            end = start + duration
            extra.update({
                "source_id": int(vod["vod_id"]),
                "start_sec": start,
                "end_sec": end,
            })
            rec = fail(
                "recuperado",
                f"VOD {vod['vod_id']} [{start}s, {end}s] "
                f"(candidato {cand:.1f}s, "
                f"error {match['offset'] - cand:+.2f}s)",
                extra)
            cleanup()
            return rec
        if conf == "media" and best_media is None:
            best_media = (match, extra, cand)
        elif conf == "dudosa":
            if best_dudosa is None or (
                    match["correlation"] >
                    best_dudosa[0]["correlation"]):
                best_dudosa = (match, extra, cand)
    if best_media is not None:
        _match, extra, _cand = best_media
        rec = fail(
            "dudoso",
            "match de audio con confianza media: no entra en el "
            "mapa (revisar a mano)",
            extra)
        cleanup()
        return rec
    if best_dudosa is not None:
        _match, extra, _cand = best_dudosa
        rec = fail(
            "dudoso",
            "match de audio dudoso (corr/ratio bajos): ningún "
            "intento casó con el audio del clip",
            extra)
        cleanup()
        return rec
    rec = fail(
        "dudoso",
        f"descarga del audio del VOD falló en los "
        f"{len(attempts)} intento(s): "
        f"{download_errors[-1] if download_errors else 'sin detalle'}",
        {"intentos": len(attempts),
         "vod_id": int(attempts[0][2]["vod_id"])})
    cleanup()
    return rec

# -- Batch + salidas ---------------------------------------------------
def _write_json(path, payload):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def build_d1_sql(records):
    """UPDATEs de D1 para el mapa (generados, NO ejecutados).

    PATCH /api/clips/:id no acepta source_id/start_sec/end_sec:
    la vía es la API de D1. OJO con el límite diario de escrituras
    del plan gratis (error 7500; se libera a medianoche UTC).
    """
    lines = [
        "-- Mapa de fuentes VOD recuperadas (generado por "
        "clipagent/recover_vod_source.py; NO ejecutado por el script).",
        "-- Aplicar con la API de D1 (wrangler d1 execute o la API v4).",
        "-- Si da error 7500 (límite diario de escrituras), reintentar",
        "-- tras la medianoche UTC (21:00 -03).",
        "",
    ]
    for rec in records:
        if rec.get("outcome") != "recuperado":
            continue
        lines.append(
            f"UPDATE clips SET source_id = {rec['source_id']}, "
            f"start_sec = {rec['start_sec']}, "
            f"end_sec = {rec['end_sec']} "
            f"WHERE id = {rec['id']}; "
            f"-- {rec.get('streamer')}: {rec.get('title', '')[:60]}")
    return "\n".join(lines) + "\n"


def run_batch(clips, ctx, out_map, out_report, out_sql):
    """Recupera la lista y escribe mapa + reporte + SQL.

    Un clip que falla no tumba el batch: queda registrado y se
    sigue con el siguiente. Devuelve el payload del reporte.
    """
    records = []
    for clip in clips:
        try:
            rec = recover_one(clip, ctx)
        except RecoverError as exc:
            rec = {"id": clip.get("id"),
                   "title": clip.get("title") or "",
                   "streamer": clip.get("streamer") or "",
                   "outcome": "dudoso",
                   "motivo": f"error de infraestructura: {exc}"}
        except Exception as exc:  # noqa: BLE001 - un clip no
            # tumba el batch; queda como dudoso visible.
            rec = {"id": clip.get("id"),
                   "title": clip.get("title") or "",
                   "streamer": clip.get("streamer") or "",
                   "outcome": "dudoso",
                   "motivo": f"error inesperado: {exc}"}
        records.append(rec)
        print(f"clip {rec['id']}: {rec['outcome']} "
              f"({rec.get('confianza', '-')}) {rec['motivo']}",
              flush=True)
        # Guardar la caché de VODs tras cada clip: si el proceso
        # muere, no se pierde el trabajo de yt-dlp.
        if ctx.get("vod_cache_path"):
            _write_json(ctx["vod_cache_path"], ctx["vod_cache"])
    source_map = {}
    for rec in records:
        if rec.get("outcome") == "recuperado":
            source_map[str(rec["id"])] = {
                "source_id": rec["source_id"],
                "start_sec": rec["start_sec"],
                "end_sec": rec["end_sec"],
            }
    summary = {
        "total": len(records),
        "recuperado": sum(1 for r in records
                          if r["outcome"] == "recuperado"),
        "dudoso": sum(1 for r in records if r["outcome"] == "dudoso"),
        "sin_vod": sum(1 for r in records if r["outcome"] == "sin_vod"),
    }
    print(f"resumen: total={summary['total']} "
          f"recuperado={summary['recuperado']} "
          f"dudoso={summary['dudoso']} sin_vod={summary['sin_vod']}",
          flush=True)
    if out_map:
        _write_json(out_map, source_map)
        print(f"mapa: {out_map} ({len(source_map)} entradas)", flush=True)
    if out_sql:
        sql = build_d1_sql(records)
        os.makedirs(os.path.dirname(os.path.abspath(out_sql)),
                    exist_ok=True)
        with open(out_sql, "w", encoding="utf-8") as f:
            f.write(sql)
        print(f"SQL D1 (sin ejecutar): {out_sql}", flush=True)
    payload = {
        "modo": "recover-vod-source",
        "generado_en": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "resumen": summary,
        "clips": records,
    }
    if out_report:
        _write_json(out_report, payload)
        print(f"reporte: {out_report}", flush=True)
    return payload


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    """CLI del recuperador. Exit 0 = batch completo (los fallos
    de clip son datos, no errores); exit 2 = infraestructura."""
    parser = argparse.ArgumentParser(
        description="Recupera la fuente VOD de los clips en vivo "
        "bloqueados sin source_id (mapa para remake_all.py)."
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--from-panel",
        action="store_true",
        help="clips bloqueados desde el panel (necesita sesión)",
    )
    src.add_argument(
        "--from-audit",
        metavar="PATH",
        help="clips bloqueados desde clips.json de la auditoría",
    )
    src.add_argument(
        "--from-json",
        metavar="PATH",
        help="clips desde un JSON con una lista de registros",
    )
    parser.add_argument(
        "--runs-json",
        default=None,
        help="runs-jobs.json ya bajado (si no se da, se baja por la "
        "API de GitHub para el rango de los clips, ±1 día)",
    )
    parser.add_argument(
        "--vod-cache",
        default=None,
        help="JSON de caché de VODs por streamer (se actualiza)",
    )
    parser.add_argument(
        "--clip-video",
        choices=["panel", "r2"],
        default="panel",
        help="de dónde bajar el vídeo del clip (panel: GET "
        "/api/clips/:id/video; r2: solo en esta VM)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="solo metadata (corrida + VOD + candidato), sin "
        "descargar ni hacer match de audio",
    )
    parser.add_argument("--ids", default="",
                        help="solo estos ids, separados por comas")
    parser.add_argument("--limit", type=int, default=0,
                        help="máximo de clips a procesar")
    parser.add_argument(
        "--work-dir",
        default=os.path.join("runs", "recover-vod-work"),
        help="directorio de trabajo (descargas temporales)",
    )
    parser.add_argument("--out-map", default=None,
                        help="source-map.json de salida")
    parser.add_argument("--out-report", default=None,
                        help="report.json de salida")
    parser.add_argument("--out-sql", default=None,
                        help="update-d1.sql de salida (sin ejecutar)")
    parser.add_argument("--keep-files", action="store_true",
                        help="no borrar las descargas temporales")
    args = parser.parse_args(argv)

    # --- clips -------------------------------------------------------
    if args.from_panel:
        from .panel_bridge import PanelBridge
        from .remake_all import load_blocked_clips

        bridge = PanelBridge.from_env()
        if bridge is None:
            print("ERROR: sin credenciales del panel en el entorno "
                  "(PANEL_TENANT + PANEL_SESSION_TOKEN o "
                  "PANEL_BRAIN_TOKEN): corre el paso bootstrap primero.",
                  file=sys.stderr)
            return 2
        try:
            clips = load_blocked_clips(bridge)
        except Exception as exc:  # noqa: BLE001 - sin lista no hay batch
            print(f"ERROR: no se pudo leer la Cola del panel: {exc}",
                  file=sys.stderr)
            return 2
        # load_blocked_clips no trae created_at/video_r2_key: pedir
        # el registro completo de cada clip.
        for clip in clips:
            try:
                full = bridge.get_clip(clip["id"]) or {}
                for key in ("created_at", "video_r2_key", "platform",
                            "influencer_name"):
                    if clip.get(key) in (None, "") and full.get(key):
                        clip[key] = full.get(key)
                if not clip.get("streamer") and full.get("influencer_name"):
                    clip["streamer"] = full.get("influencer_name")
            except Exception:  # noqa: BLE001 - el clip queda dudoso
                pass
    elif args.from_audit:
        records = _load_json(args.from_audit)
        clips = [r for r in records if r.get("publish_blocked")]
    else:
        clips = _load_json(args.from_json)
        if isinstance(clips, dict):
            clips = clips.get("clips") or []
    want = ({int(x) for x in args.ids.split(",") if x.strip()}
            if args.ids.strip() else None)
    if want:
        clips = [c for c in clips if c.get("id") in want]
    if args.limit:
        clips = clips[: args.limit]
    if not clips:
        print("ERROR: sin clips que procesar", file=sys.stderr)
        return 2

    # --- corridas ----------------------------------------------------
    if args.runs_json:
        runs_data = _load_json(args.runs_json)
        runs = runs_data.get("runs") or []
    else:
        try:
            runs = runs_for_clips(clips)
        except RecoverError as exc:
            print(f"ERROR: no se pudieron bajar las corridas: {exc}",
                  file=sys.stderr)
            return 2
        print(f"corridas bajadas por API: {len(runs)}", flush=True)

    # --- caché de VODs ------------------------------------------------
    vod_cache = {}
    if args.vod_cache and os.path.isfile(args.vod_cache):
        vod_cache = _load_json(args.vod_cache) or {}

    try:
        from .streamers import load_streamers

        streamers = load_streamers()
    except Exception:  # noqa: BLE001 - el login se normaliza
        streamers = []

    ctx = {
        "runs": runs,
        "vod_cache": vod_cache,
        "vod_cache_path": args.vod_cache,
        "streamers": streamers,
        "work_dir": args.work_dir,
        "clip_video": args.clip_video,
        "bridge": None,
        "keep_files": args.keep_files,
    }
    if args.clip_video == "panel" and not args.dry_run:
        from .panel_bridge import PanelBridge

        ctx["bridge"] = PanelBridge.from_env()
        if ctx["bridge"] is None:
            print("ERROR: --clip-video panel necesita sesión del panel "
                  "(PANEL_TENANT + PANEL_SESSION_TOKEN o "
                  "PANEL_BRAIN_TOKEN).",
                  file=sys.stderr)
            return 2

    if args.dry_run:
        # Solo metadata: corrida + VOD + candidato, sin descargas.
        # Usa _build_attempts (igual que el run real) para que la
        # cobertura del dry-run sea la del run real (incluye el
        # fallback de fila creada tarde). Un clip que falla no
        # tumba el batch: queda como dudoso y se sigue.
        records = []
        for clip in clips:
            rec = {"id": clip.get("id"),
                   "title": clip.get("title") or "",
                   "streamer": clip.get("streamer") or ""}
            try:
                login, nota = twitch_login(clip.get("streamer"),
                                           streamers)
                rec["login_twitch"] = login or ""
                if nota:
                    rec["login_nota"] = nota
                if not login:
                    rec.update({"outcome": "sin_vod", "motivo": nota})
                    records.append(rec)
                    continue
                try:
                    created = float(clip["created_at"]) / 1000.0
                except (TypeError, ValueError):
                    rec.update({"outcome": "dudoso",
                                "motivo": "created_at inválido"})
                    records.append(rec)
                    continue
                attempts, motivo = _build_attempts(
                    clip, {"runs": runs, "vod_cache": vod_cache},
                    login, created)
                if not attempts:
                    outcome = ("sin_vod" if "ningún VOD" in motivo
                               else "dudoso")
                    rec.update({"outcome": outcome, "motivo": motivo})
                    records.append(rec)
                    continue
                run, job, vod, cand, t0_aprox = attempts[0]
                rec.update({
                    "outcome": "candidato",
                    "run_id": run.get("run_id"),
                    "job": job.get("name"),
                    "vod_id": int(vod["vod_id"]),
                    "candidato": round(cand, 2),
                    "t0_aproximado": t0_aprox,
                    "intentos": len(attempts),
                    "motivo": f"VOD {vod['vod_id']} candidato "
                              f"{cand:.1f}s (sin match de audio: "
                              f"dry-run)",
                })
                records.append(rec)
                print(f"clip {rec['id']}: candidato {rec['motivo']}",
                      flush=True)
            except RecoverError as exc:
                rec.update({"outcome": "dudoso",
                            "motivo": f"error de infraestructura: {exc}"})
                records.append(rec)
            except Exception as exc:  # noqa: BLE001 - un clip no
                # tumba el dry-run; queda como dudoso visible.
                rec.update({"outcome": "dudoso",
                            "motivo": f"error inesperado: {exc}"})
                records.append(rec)
            # Guardar la caché tras cada clip (ver run_batch).
            if args.vod_cache:
                _write_json(args.vod_cache, vod_cache)
        summary = {
            "total": len(records),
            "candidato": sum(1 for r in records
                             if r["outcome"] == "candidato"),
            "dudoso": sum(1 for r in records
                          if r["outcome"] == "dudoso"),
            "sin_vod": sum(1 for r in records
                           if r["outcome"] == "sin_vod"),
        }
        print(f"resumen dry-run: total={summary['total']} "
              f"candidato={summary['candidato']} "
              f"dudoso={summary['dudoso']} sin_vod={summary['sin_vod']}",
              flush=True)
        if args.out_report:
            _write_json(args.out_report, {
                "modo": "recover-vod-source-dry-run",
                "generado_en": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "resumen": summary,
                "clips": records,
            })
            print(f"reporte: {args.out_report}", flush=True)
    else:
        run_batch(clips, ctx, args.out_map, args.out_report, args.out_sql)

    if args.vod_cache:
        _write_json(args.vod_cache, vod_cache)
        print(f"caché VODs: {args.vod_cache}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
