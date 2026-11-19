#!/usr/bin/env python3
"""Cortador EN VIVO real (Fase 1, 2026-11-18).

Antes de este módulo, el vivo solo detectaba picos y avisaba
(``clipagent/live.py``: "BLOQUEADO (grabación)"). Este módulo es
el cutter que faltaba: graba el directo mientras pasa, detecta el
pico por ráfaga de chat, corta el clip 40-60 s, le pone subtítulos
completos, lo empaqueta y lo encola en la Cola LIVE del panel, sin
aprobación humana por clip y con las puertas de seguridad intactas.

Flujo por pico (todo automático, cada paso con tracking):

    IRC en vivo -> detector de picos (hype v2 por ráfaga) ->
    GATE DE CATEGORÍA (solo GTA VI, ver abajo) ->
    esperar la cola del vídeo (pico + 20 s) ->
    extraer la ventana [pico-20, pico+20] (40 s) del ring buffer ->
    corte vertical split (render) -> subtítulos quemados completos
    (faster-whisper; sin voz verificable = BLOQUEADO, nunca se
    inventa texto) -> título validado (anti-clickbait) -> paquete
    SEO + prioridad por plataforma (fórmula INTOCABLE) ->
    encolar en el panel (Cola LIVE, origin='live', MP4 en R2) +
    encolar la publicación en el cerebro (mode api solo para las
    plataformas publicables; draft con motivo para el resto).

GATE DE CATEGORÍA (regla del usuario 2026-11-18): el cutter SOLO
corta cuando el influencer está streameando en GTA VI (Grand Theft
Auto VI). Si cambia a Just Chatting u otra categoría, no corta.
La categoría se lee por API oficial (nunca scraping):
  * Twitch en vivo: Helix GET /helix/streams?user_login (game_name)
    y GET /helix/channels?broadcaster_id (``watcher.TwitchHelix``:
    live_category / channel_category; credenciales TWITCH_* ya
    guardadas; sin ellas, fallback a la GQL pública de Twitch).
  * Twitch VOD: capítulos del chat.json (``video.chapters``:
    gameDisplayName por tramo) — fallback cuando el directo ya
    cerró y para el modo replay.
  * Kick en vivo: GET https://kick.com/api/v2/channels/<slug>
    (``clipagent.live.kick_channel_category``, API pública).
La categoría queda registrada en cada clip (ficha, paquete,
motivo del panel y tracking). Sin categoría verificable, NO se
corta (por seguridad: sin verificar GTA VI, no se corta).

Grabación (ring buffer, $0): ``streamlink`` saca la URL HLS del
directo y ``ffmpeg`` la graba en segmentos de 60 s (se guardan
los últimos 12 = 12 min; los viejos se borran). El clip se corta
de esos segmentos: nunca se baja el VOD entero. La latencia HLS
(~10-15 s entre lo que pasa y lo que llega por HLS) se compensa
con ``latency_s`` (12 s por defecto): la ventana de corte es
[pico-20+latencia, pico+20+latencia] en tiempo de grabación.

Dónde corre: GitHub Actions (workflow cerebro-live.yml), el mismo
compute $0 del cerebro. Cloudflare Workers no puede correr
ffmpeg/Whisper. Una corrida cubre hasta ~25 min de directo; el
estado (picos, chat) persiste entre corridas por artefacto, y lo
que el vivo no alcanzó a cortar se recupera del VOD al cerrar
(modo replay con el mismo código).

Límites que NO se tocan: cooldown y tope por directo
(``live:`` en config), filtros duros (viralidad < 50 descartado,
SEO < 90 sin override, duración 40-60 s, rango 25-35 prohibido),
fórmula de prioridad, subtítulos siempre y completos.
"""
import json
import os
import queue
import re
import subprocess
import threading
import time
from datetime import datetime, timezone

from .category import (chapter_category_from_chat, is_gta6)
from .live import LiveLimits, LiveSession

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SEGMENT_S = 60            # cada segmento grabado dura 60 s
KEEP_SEGMENTS = 12        # ring buffer: últimos 12 segmentos (12 min)
CLIP_PRE_S = 20           # pre-roll antes del pico (regla del usuario)
CLIP_DURATION_S = 40      # objetivo del corte (40-60 s, nunca menos)
DEFAULT_LATENCY_S = 12    # latencia HLS Twitch (chat vs vídeo)
CATEGORY_REFRESH_S = 60   # re-leer la categoría como mucho cada 60 s


# ---------------------------------------------------------------------------
# Gate de categoría (GTA VI o no se corta)
# ---------------------------------------------------------------------------

class CategoryGate:
    """Gate de categoría por API oficial, con registro.

    `mode="live"`: lee la categoría en vivo (Helix / Kick API).
    `mode="vod"`: la lee de los capítulos del chat.json.
    `check(ts, chat_path)` -> (ok, category, source): ok solo si
    la categoría verificada es GTA VI. Sin categoría verificable,
    ok=False (no se corta por seguridad) y el motivo queda en el
    log del gate.
    """

    def __init__(self, platform="twitch", streamer=None, mode="live",
                 chat_path=None, twitch_client=None, cfg=None):
        self.platform = (platform or "twitch").lower()
        self.streamer = streamer
        self.mode = mode
        self.chat_path = chat_path
        self.cfg = cfg or {}
        self._twitch_client = twitch_client
        self._last_check = 0.0
        self._last_reading = None
        self.log = []

    def _twitch_live_category(self):
        """(categoría, fuente) en vivo: Helix, fallback GQL."""
        try:
            import watcher as _watcher
            if self._twitch_client is None:
                client, _mode = _watcher.twitch_client(
                    {"twitch_client_id": os.environ.get("TWITCH_CLIENT_ID", ""),
                     "twitch_client_secret": os.environ.get("TWITCH_CLIENT_SECRET", "")})
                self._twitch_client = client
            client = self._twitch_client
            if hasattr(client, "live_category"):
                r = client.live_category(self.streamer)
                return r.get("game_name") or "", r.get("source") or "helix"
            live, _vods = client.check(self.streamer)
            return (live or {}).get("game") or "", "gql-publico"
        except Exception as e:  # noqa: BLE001 - sin lectura, no se corta
            return "", f"sin lectura ({e})"

    def _kick_live_category(self):
        # Como en Twitch: sin lectura, categoría vacía + motivo
        # "sin lectura (...)" (el caller distingue error de API de
        # directo terminado: solo el primero debe fallar la corrida).
        try:
            from .live import kick_channel_category
            cat = kick_channel_category(self.streamer)
        except Exception as e:  # noqa: BLE001 - sin lectura, no se corta
            return "", f"sin lectura ({e})"
        return cat or "", "kick api/v2/channels"

    def refresh(self, force=False):
        """Categoría actual en vivo (con throttle de 60 s)."""
        now = time.time()
        if (not force and self._last_reading is not None
                and now - self._last_check < CATEGORY_REFRESH_S):
            return self._last_reading
        if self.platform == "kick":
            category, source = self._kick_live_category()
        else:
            category, source = self._twitch_live_category()
        reading = {"category": category or "",
                   "source": source, "at": now,
                   "is_gta6": is_gta6(category)}
        self._last_check = now
        self._last_reading = reading
        return reading

    def check(self, ts=None, chat_path=None):
        """(ok, category, source) para un pico en `ts` segundos."""
        if self.mode == "vod":
            path = chat_path or self.chat_path
            category = (chapter_category_from_chat(path, ts)
                        if path else None)
            source = f"capítulos del VOD ({path})" if path \
                else "sin chat.json"
            ok = bool(category) and is_gta6(category)
            entry = {"ts": ts, "category": category or "",
                     "source": source, "ok": ok,
                     "at": time.time()}
            self.log.append(entry)
            return ok, category or "", source
        reading = self.refresh()
        ok = bool(reading["category"]) and reading["is_gta6"]
        entry = {"ts": ts, "category": reading["category"],
                 "source": reading["source"], "ok": ok,
                 "at": reading["at"]}
        self.log.append(entry)
        return ok, reading["category"], reading["source"]


# ---------------------------------------------------------------------------
# Grabación: ring buffer de segmentos (streamlink + ffmpeg)
# ---------------------------------------------------------------------------

def _run(cmd, timeout=None):
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout)


class StreamRecorder:
    """Graba el directo en segmentos de 60 s (ring buffer).

    streamlink -> URL HLS -> ffmpeg -f segment. `start()` lanza
    la grabación; `rel_now()` son los segundos grabados; los
    segmentos viejos se borran (se guardan los últimos 12).
    """

    def __init__(self, streamer, platform="twitch", out_dir=None,
                 segment_s=SEGMENT_S, keep=KEEP_SEGMENTS,
                 streamlink_bin="streamlink", ffmpeg_bin="ffmpeg"):
        self.streamer = streamer
        self.platform = platform
        self.out_dir = out_dir or os.path.join(
            BASE_DIR, "runs", "live", f"{streamer}_rec")
        self.segment_s = segment_s
        self.keep = keep
        self.streamlink_bin = streamlink_bin
        self.ffmpeg_bin = ffmpeg_bin
        self.started_at = None
        self._proc = None
        self._hls_url = None
        self._prune_lock = threading.Lock()

    def stream_url(self):
        """URL del directo (twitch.tv/<canal>)."""
        if self.platform == "kick":
            return f"https://kick.com/{self.streamer}"
        return f"https://www.twitch.tv/{self.streamer}"

    def resolve_hls_url(self, timeout=60):
        """streamlink --stream-url: la URL HLS real del directo."""
        res = _run([self.streamlink_bin, "--stream-url",
                    self.stream_url(), "best"], timeout=timeout)
        url = (res.stdout or "").strip().splitlines()
        url = url[-1].strip() if url else ""
        if res.returncode != 0 or not url.startswith("http"):
            raise RuntimeError(
                f"streamlink no resolvió el directo "
                f"({self.streamer}): {(res.stderr or '')[:200]}")
        self._hls_url = url
        return url

    def start(self):
        """Lanza ffmpeg segmentando. Devuelve True si graba."""
        os.makedirs(self.out_dir, exist_ok=True)
        hls = self.resolve_hls_url()
        pattern = os.path.join(self.out_dir, "seg_%05d.ts")
        cmd = [self.ffmpeg_bin, "-hide_banner", "-loglevel", "error",
               "-i", hls, "-c", "copy", "-f", "segment",
               "-segment_time", str(self.segment_s),
               "-segment_format", "mpegts",
               "-reset_timestamps", "1", pattern]
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.started_at = time.time()
        return True

    def rel_now(self):
        """Segundos grabados (tiempo de grabación)."""
        if self.started_at is None:
            return 0.0
        return time.time() - self.started_at

    def segments(self):
        """[(inicio_rel, ruta)] ordenados, de los .ts en disco."""
        out = []
        if not os.path.isdir(self.out_dir):
            return out
        for name in sorted(os.listdir(self.out_dir)):
            m = re.match(r"seg_(\d+)\.ts$", name)
            if m:
                out.append((int(m.group(1)) * self.segment_s,
                            os.path.join(self.out_dir, name)))
        return out

    def prune(self, pending_starts=()):
        """Borra segmentos viejos (fuera de los últimos `keep`),
        sin tocar los que necesita un corte pendiente."""
        with self._prune_lock:
            segs = self.segments()
            if len(segs) <= self.keep:
                return 0
            drop = segs[:len(segs) - self.keep]
            n = 0
            for start, path in drop:
                if any(start <= p < start + self.segment_s
                       for p in pending_starts):
                    continue
                try:
                    os.remove(path)
                    n += 1
                except OSError:
                    pass
            return n

    def wait_until(self, rel_ts, timeout=120):
        """Espera a que la grabación llegue a `rel_ts`."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError("la grabación se detuvo "
                                   "(ffmpeg/streamlink murió)")
            if self.rel_now() >= rel_ts:
                return True
            time.sleep(1.0)
        raise TimeoutError(f"la grabación no llegó a {rel_ts:.0f}s "
                           f"en {timeout}s")

    def extract_window(self, start, duration, out_path):
        """Concatena los segmentos de [start, start+duration] en
        `out_path` (.ts, copia sin re-codificar). Devuelve
        (out_path, offset_dentro_del_archivo)."""
        segs = self.segments()
        use = [(s, p) for s, p in segs
               if s + self.segment_s > start and s < start + duration]
        if not use:
            raise RuntimeError(f"sin segmentos grabados para "
                               f"[{start:.0f}, {start + duration:.0f}]")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)),
                    exist_ok=True)
        list_path = out_path + ".concat.txt"
        with open(list_path, "w", encoding="utf-8") as f:
            for _s, p in use:
                f.write(f"file '{p}'\n")
        res = _run([self.ffmpeg_bin, "-hide_banner", "-loglevel",
                    "error", "-f", "concat", "-safe", "0", "-i",
                    list_path, "-c", "copy", out_path], timeout=300)
        if res.returncode != 0 or not os.path.isfile(out_path):
            raise RuntimeError(f"ffmpeg no extrajo la ventana: "
                               f"{(res.stderr or '')[:300]}")
        return out_path, max(0.0, start - use[0][0])

    def stop(self):
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=10)
            except Exception:  # noqa: BLE001 - parar nunca tumba nada
                try:
                    self._proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            self._proc = None


class FileRecorder:
    """Adaptador VOD/replay: los cortes salen de un archivo ya
    descargado (segmento o VOD). `offset` es el segundo absoluto
    del VOD donde empieza el archivo (0 si es el VOD entero)."""

    def __init__(self, video_path, offset=0.0, ffmpeg_bin="ffmpeg"):
        self.video_path = video_path
        self.offset = float(offset)
        self.ffmpeg_bin = ffmpeg_bin
        self.started_at = time.time()

    def rel_now(self):
        return 10 ** 9  # el archivo ya está completo

    def wait_until(self, rel_ts, timeout=120):
        return True

    def extract_window(self, start, duration, out_path):
        """Copia [start, start+duration] (tiempo absoluto del VOD)
        a `out_path`. Devuelve (out_path, 0)."""
        rel = max(0.0, start - self.offset)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)),
                    exist_ok=True)
        # Solo vídeo+audio (el mp4 de Twitch trae además un stream
        # de datos bin_data que en .ts duplica programas y rompe el
        # _probe_size de render; el mapa explícito lo deja limpio).
        res = _run([self.ffmpeg_bin, "-hide_banner", "-loglevel",
                    "error", "-ss", str(rel), "-i", self.video_path,
                    "-t", str(duration), "-map", "0:v:0", "-map",
                    "0:a:0?", "-c", "copy", out_path],
                   timeout=300)
        if res.returncode != 0 or not os.path.isfile(out_path):
            raise RuntimeError(f"ffmpeg no extrajo la ventana del "
                               f"VOD: {(res.stderr or '')[:300]}")
        return out_path, 0.0

    def stop(self):
        return None


# ---------------------------------------------------------------------------
# El cutter: pico -> clip -> paquete -> cola LIVE + publicación
# ---------------------------------------------------------------------------

def build_title(detail, layout_decision, payoff_ts_clip=20.0,
                lang="es", game="GTA 6"):
    """Título validado anti-clickbait (mismo camino que el VOD).

    suggest_titles + honest_title de auto_clips.py. Si la
    validación no está disponible o falla, se usa el título
    sugerido tal cual (nunca se inventa otro).
    """
    try:
        import auto_clips as _auto
        raw, _desc = _auto.suggest_titles(detail, lang, game=game)
        try:
            moment_card = {"game": game,
                           "risas": detail.get("risas", 0),
                           "hype": detail.get("hype", 0)}
            title, _degraded, _why = _auto.honest_title(
                raw, moment_card, layout_decision, payoff_ts_clip, lang)
            return title
        except Exception:  # noqa: BLE001 - título sugerido tal cual
            return raw
    except Exception:  # noqa: BLE001 - título seguro de reacción
        return "El chat explotó 😂 GTA 6" if lang != "en" \
            else "Chat went wild 😂 GTA 6"


def finalize_package(package_path, viral_total, chat_multiple, cfg=None):
    """Prioridad + estado por plataforma (misma fórmula que el
    empaquetar del worker, sin reimplementarla). Escribe el
    paquete con priority/status/virality/chat_multiple.

    Los umbrales salen de config.yaml (scoring.seo_gate /
    scoring.viral_min, orden del usuario 2026-11-19: 80/45), no de
    los defectos 90/50 de priority.py (2026-11-19, clips 24/25:
    viral 49 con SEO 86 salía "descartado" aunque pasa 45/80).
    """
    from .policy import load_scoring_config
    from .priority import priority as priority_fn, publish_status
    seo_gate, viral_min = load_scoring_config(cfg)
    with open(package_path, encoding="utf-8") as f:
        package = json.load(f)
    priorities, statuses = {}, {}
    for platform, score in (package.get("seo") or {}).items():
        priorities[platform] = priority_fn(viral_total, score,
                                           chat_multiple)
        status, why = publish_status(viral_total, score,
                                     seo_gate=seo_gate,
                                     viral_min=viral_min)
        statuses[platform] = {"status": status, "reason": why}
    package["priority"] = priorities
    package["publish_status"] = statuses
    package["virality"] = viral_total
    package["chat_multiple"] = chat_multiple
    tmp = package_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(package, f, ensure_ascii=False, indent=2)
    os.replace(tmp, package_path)
    return package


def _lang_for_streamer(name):
    """Idioma del canal de un streamer según streamers.yaml.

    Devuelve "es" o "en" ("es" por defecto si el streamer no está
    en el catálogo o el fichero no se puede leer).
    """
    try:
        from .streamers import load_streamers
        for entry in load_streamers():
            if str(entry.get("name") or "").lower() == str(name).lower():
                lang = str(entry.get("language") or "es").lower()
                return lang if lang in ("es", "en") else "es"
    except Exception:  # noqa: BLE001 - sin catálogo: español.
        pass
    return "es"


def _visible_from_layout(layout):
    """Lo que se ve de verdad en un layout de render.

    ``layout`` es la decisión de render.decide_layout: {"mode",
    "segments": [{"layout": ...}]}. Cualquier tramo con franja de
    webcam (split/stack/pip) -> "cams+gameplay"; si todo es juego
    a pantalla completa (center/game) -> "gameplay". Sin layout
    (o vacío) -> None (la coherencia SEO queda en 80, como antes).
    """
    if not isinstance(layout, dict):
        return None
    segments = layout.get("segments") or []
    if not segments:
        mode = layout.get("mode")
        return ("cams+gameplay" if mode in ("split", "stack", "pip")
                else "gameplay" if mode in ("center", "game") else None)
    for seg in segments:
        if str(seg.get("layout") or "") in ("split", "stack", "pip"):
            return "cams+gameplay"
    return "gameplay"


def _seo_summary(seo):
    """'YT 92 · TT 89 · IG 91 · FB 90' a partir de {plataforma: score}.

    Para los mensajes de evento al panel (2026-11-19): una línea
    con los 4 números SEO post-corte."""
    short = {"youtube_shorts": "YT", "tiktok": "TT",
             "instagram_reels": "IG", "facebook_reels": "FB"}
    return " · ".join(
        f"{short.get(p, p)} {float(v):.0f}"
        for p, v in (seo or {}).items())


class LiveCutterSession(LiveSession):
    """Sesión en vivo que CORTA: LiveSession + recorder + pipeline.

    `recorder`: StreamRecorder (en vivo) o FileRecorder (replay).
    `store`: StepJobs del cerebro (publicación); si es None, se
    crea el default. `bridge`: PanelBridge al panel web (o None:
    sin panel, solo cerebro). `gate`: CategoryGate (GTA VI).
    `dry_run`: detecta y corta, pero no encola publicación ni
    panel (para probar sin subir nada). `auto_publish`: False por
    defecto (orden del usuario 2026-11-18 14:37: los mejores clips
    van a la COLA del panel, sin publicar en público todavía; las
    publicaciones se encolan en modo draft, listas, no publicadas).
    Con auto_publish=True, las plataformas publicables se encolan
    en mode api (publicación automática, MÁSTER PROMPT FINAL).
    """

    def __init__(self, streamer, platform="twitch", stream_id=None,
                 limits=None, notify_fn=None, base_dir=None,
                 recorder=None, gate=None, store=None, bridge=None,
                 out_dir=None, cfg=None, dry_run=False,
                 latency_s=DEFAULT_LATENCY_S, vod_id=None,
                 auto_publish=False, final_review_fn=None, lang=None):
        super().__init__(streamer, platform=platform,
                         stream_id=stream_id, limits=limits,
                         notify_fn=notify_fn, base_dir=base_dir)
        # Idioma del canal (2026-11-19, calibración): sin él todo
        # el camino en vivo era español a la fuerza (Whisper,
        # revisión Gemini, títulos, subtítulos y SEO) aunque el
        # streamer fuera inglés. Si no se pasa, se resuelve por
        # streamers.yaml; si el streamer no está, "es".
        self.lang = (str(lang).lower() if lang else None) or \
            _lang_for_streamer(streamer)
        if self.lang not in ("es", "en"):
            self.lang = "es"
        self.recorder = recorder
        self.gate = gate or CategoryGate(
            platform=platform, streamer=streamer, mode="live", cfg=cfg)
        self.cfg = cfg or {}
        self.dry_run = dry_run
        self.auto_publish = auto_publish
        # Juez visual final (final_review.py): callable(parts) ->
        # texto, inyectable en tests; None = _call_gemini real.
        self.final_review_fn = final_review_fn
        self.latency_s = latency_s
        self.vod_id = vod_id
        self.out_dir = out_dir or os.path.join(
            BASE_DIR, "runs", "live",
            f"{streamer}_{self.stream_id}", "clips")
        os.makedirs(self.out_dir, exist_ok=True)
        self._store = store
        self.bridge = bridge
        self._cut_queue = queue.Queue()
        self._cutter_thread = None
        self._stop_cutter = threading.Event()
        self.cut_clips = []      # registros de clips cortados
        self.skipped = []        # picos descartados (con motivo)
        self._clip_n = 0
        # Revisión automática de los mejores picos (2026-11-18):
        # top 3 por directo con viralidad auto 40-49,9. Cada entrada
        # es el dict que devuelve review.review_peak.
        self._reviewed_peaks = []
        # Eventos de clip ya reportados al panel (2026-11-19): como
        # máximo UN clip_descartado por clip (el primer motivo
        # manda); un clip sin voz, por ejemplo, no vuelve a
        # reportarse por SEO.
        self._discard_reported = set()

    # -- store perezoso (StepJobs) ------------------------------------
    @property
    def store(self):
        if self._store is None:
            from .jobs import StepJobs
            self._store = StepJobs()
        return self._store

    # -- tracking ------------------------------------------------------
    def _track(self, clip_id, step, message, panel_clip_id=None):
        if self.bridge is not None and panel_clip_id:
            try:
                self.bridge.tracking_step(panel_clip_id, step, message)
            except Exception:  # noqa: BLE001 - el tracking no tumba
                pass

    def _report_event(self, kind, message):
        """Evento de clip al panel (2026-11-19). En dry_run no se
        reporta nada. Nunca levanta."""
        if self.dry_run:
            return
        try:
            from .panel_bridge import report_brain_event
            report_brain_event(kind, message, bridge=self.bridge)
        except Exception:  # noqa: BLE001 - el evento es informativo
            pass

    def _report_discard_once(self, clip_id, message):
        """clip_descartado una sola vez por clip (el primer motivo
        manda); los demás descartes del mismo clip solo quedan en
        self.skipped."""
        if clip_id in self._discard_reported:
            return
        self._discard_reported.add(clip_id)
        self._report_event("clip_descartado", message)

    def _record(self, record):
        path = os.path.join(self.out_dir, "live_cuts.json")
        self.cut_clips.append(record)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.cut_clips, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return path

    def _persist_review(self, reviewed, peak_ts):
        """Añade una revisión (pase o descarte) a live_reviews.json.

        2026-11-19, calibración: antes la revisión solo vivía en
        memoria (self._reviewed_peaks) y en el log; los picos
        descartados perdían sus entradas (transcripción,
        fotogramas, paquete, SEO por plataforma) y el embudo no se
        podía auditar después. Nunca tumba el corte.
        """
        try:
            path = os.path.join(self.out_dir, "live_reviews.json")
            reviews = []
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    reviews = json.load(f) or []
            reviews.append(dict(reviewed))
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(reviews, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001 - la persistencia no tumba
            pass

    # -- picos: del detector al hilo de corte --------------------------
    def _on_peak(self, peak, notify):
        # Gate de límites (cooldown/tope) + registro + aviso, igual
        # que LiveSession; si el límite frena, no se corta nada.
        ok, reason = self.limits.allow(self.streamer, self.stream_id)
        if not ok:
            self.skipped.append({"ts": peak[0], "reason": reason,
                                 "at": datetime.now(timezone.utc)
                                 .isoformat()})
            return
        super()._on_peak(peak, notify)
        self._cut_queue.put(peak)

    def _cutter_loop(self):
        while not self._stop_cutter.is_set():
            try:
                peak = self._cut_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self.process_peak(peak)
            except Exception as e:  # noqa: BLE001 - un pico fallido
                # no tumba la sesión: queda registrado con motivo.
                self.skipped.append(
                    {"ts": peak[0], "reason": f"fallo al cortar: {e}",
                     "at": datetime.now(timezone.utc).isoformat()})
                self._report_event(
                    "corte_fallido",
                    f"{self.streamer}: corte fallido en pico "
                    f"{peak[0]:.0f}s ({e})")
            finally:
                self._cut_queue.task_done()

    def start_cutter_thread(self):
        if self._cutter_thread is None:
            self._cutter_thread = threading.Thread(
                target=self._cutter_loop, daemon=True)
            self._cutter_thread.start()

    def stop_cutter_thread(self):
        self._stop_cutter.set()
        if self._cutter_thread is not None:
            self._cutter_thread.join(timeout=5)

    # -- el corte de UN pico -------------------------------------------
    def process_peak(self, peak):
        """Pico (ts, score, multiple, detail) -> clip + cola + publish.

        Devuelve el registro del clip, o None si se descartó (con
        el motivo en self.skipped).
        """
        from .workers import (CLIP_DURATION_S, cut_clip,
                              package_clip_step, subtitle_clip)
        peak_ts, score, multiple, detail = peak
        detail = detail or {}
        # 1. GATE DE CATEGORÍA: solo GTA VI. En vivo se lee la
        # categoría actual; en replay, el capítulo del VOD.
        ok, category, source = self.gate.check(
            peak_ts, chat_path=getattr(self.gate, "chat_path", None))
        if not ok:
            reason = (f"categoría {category!r} ({source}): no es "
                      f"GTA VI, no se corta")
            self.skipped.append(
                {"ts": peak_ts, "reason": reason,
                 "category": category, "source": source,
                 "at": datetime.now(timezone.utc).isoformat()})
            return None
        # 2. PUERTA PRE-RENDER en vivo (2026-11-18): la viralidad
        # AUTO (solo chat, sin human/audio) decide ANTES de gastar
        # render. >= 50: directo. 40-49,9: revisión automática
        # (top 3 por directo, fotogramas + transcripción + Gemini).
        # < 40: se descarta sin revisión ni render. La puerta de
        # publicación sigue en 50 (paso 6 la vuelve a medir).
        from .detect import score_clip as _score_clip
        from .review import decide_render
        viral_auto = float((_score_clip(detail, multiple) or {}).get(
            "total") or 0.0)
        action, motivo_render = decide_render(
            viral_auto, len(self._reviewed_peaks))
        if action == "descartar":
            self.skipped.append(
                {"ts": peak_ts, "reason": motivo_render,
                 "viral_auto": viral_auto,
                 "at": datetime.now(timezone.utc).isoformat()})
            return None
        # 3. Ventana 40 s con pre-roll 20 s (+ latencia HLS en vivo).
        rec_start = max(0.0, peak_ts - CLIP_PRE_S + self.latency_s)
        if self.recorder is not None:
            self.recorder.wait_until(peak_ts + CLIP_PRE_S
                                     + self.latency_s, timeout=180)
        self._clip_n += 1
        n = self._clip_n
        clip_id = (f"live:{self.streamer}:{self.stream_id}"
                   f":p{int(peak_ts)}")
        work_dir = os.path.join(self.out_dir, f"clip_{n:02d}")
        os.makedirs(work_dir, exist_ok=True)
        window_path = os.path.join(work_dir, "window.mp4")
        if self.recorder is not None:
            _w, offset = self.recorder.extract_window(
                rec_start, CLIP_DURATION_S, window_path)
            cut_start = offset
            video_for_cut = window_path
        else:
            raise RuntimeError("sin recorder: no hay vídeo que cortar")
        # 4. REVISIÓN automática (solo picos 40-49,9): fotogramas +
        # transcripción + juez Gemini -> viralidad revisada. Si no
        # pasa 50 real + SEO 90, se descarta SIN render (la ventana
        # ya extraída es solo una copia, no un render).
        reviewed = None
        if action == "revision":
            from .live import _chat_json_path
            from .review import review_peak
            chat_path = _chat_json_path(self.detector.messages)
            reviewed = review_peak(
                window_path, detail, multiple, chat_path, peak_ts,
                score, game="GTA 6", lang=self.lang,
                streamer=self.streamer, cfg=self.cfg)
            reviewed["peak_ts"] = peak_ts
            self._reviewed_peaks.append(reviewed)
            self._persist_review(reviewed, peak_ts)
            # La revisión queda también en el log del job (antes
            # solo vivía en live_cuts.json y no se veía en la
            # salida de la corrida).
            print(f"REVISIÓN pico {peak_ts:.0f}s: "
                  f"{reviewed.get('revision')} — viral auto "
                  f"{viral_auto:.1f} -> revisado "
                  f"{reviewed.get('viral_revisado')} — "
                  f"{'PASA' if reviewed['pasa'] else 'descartado'} "
                  f"({reviewed.get('motivo')})")
            if not reviewed["pasa"]:
                self.skipped.append(
                    {"ts": peak_ts,
                     "reason": f"revisión: {reviewed['motivo']}",
                     "viral_auto": viral_auto,
                     "viral_revisado": reviewed.get("viral_revisado"),
                     "revision": reviewed.get("revision"),
                     "at": datetime.now(timezone.utc).isoformat()})
                self._report_discard_once(
                    clip_id,
                    f"{self.streamer}: pico {peak_ts:.0f}s descartado "
                    f"— revisión: {reviewed['motivo']} (viral auto "
                    f"{viral_auto:.1f} -> revisado "
                    f"{reviewed.get('viral_revisado')})")
                return None
        # 5. Corte vertical split RDJavi (streamer arriba, gameplay abajo).
        raw_clip = os.path.join(work_dir, f"clip_{n:02d}.mp4")
        cut = cut_clip(video_for_cut, cut_start, CLIP_DURATION_S,
                       raw_clip, layout_mode="auto", cfg=self.cfg)
        layout = cut.get("layout") or {}
        # 5b. Título ANTES del quemado (port v2, 2026-11-19): la
        # franja 702-800 se quema en el paso de subtítulos, así que
        # el título tiene que existir ya aquí. Es el título de
        # REGLAS que se quema en el vídeo; el paquete de publicación
        # del paso 7 usa el título Gemini (mejor SEO) sin tocar
        # este (2026-11-19, calibración).
        title = build_title(detail, layout,
                            payoff_ts_clip=CLIP_PRE_S, lang=self.lang,
                            game="GTA 6")
        # 6. Subtítulos quemados COMPLETOS. Sin voz verificable el
        # clip queda BLOQUEADO (nunca se publica, nunca se inventa).
        burned = os.path.join(work_dir, f"clip_{n:02d}_subs.mp4")
        sub = subtitle_clip(raw_clip, burn=burned, cfg=self.cfg,
                            lang=self.lang, title=title)
        final_clip = burned if os.path.isfile(burned) else raw_clip
        blocked = bool(sub.get("publish_blocked"))
        # 6b. Frases hype del streamer en su propia voz (2026-11-18):
        # la revisión ya las midió sobre la misma transcripción; si
        # no hubo revisión, se leen del .transcript.json del subtítulo.
        # Solo se registran (no tocan viralidad, SEO ni puertas).
        streamer_hype = {"count": 0, "distinct": 0, "phrases": []}
        if reviewed is not None and reviewed.get("streamer_hype"):
            streamer_hype = reviewed["streamer_hype"]
        else:
            try:
                from .streamer_hype import (find_streamer_hype,
                                            transcript_text)
                tpath = sub.get("transcript")
                if not tpath and sub.get("srt"):
                    tpath = os.path.splitext(sub["srt"])[0] \
                        + ".transcript.json"
                if tpath and os.path.isfile(tpath):
                    with open(tpath, encoding="utf-8") as f:
                        streamer_hype = find_streamer_hype(
                            transcript_text(json.load(f)))
            except Exception:  # noqa: BLE001 - señal opcional
                pass
        # Clip cortado: el render con subtítulos ya existe; se
        # reporta al panel (2026-11-19) aunque luego lo frenen el
        # SEO o la validación final. Sin voz verificable el clip
        # queda BLOQUEADO (nunca se publica, nunca se inventa):
        # también se reporta el descarte, con el primer motivo.
        self._report_event(
            "clip_cortado",
            f"{self.streamer}: «{title}» cortado (40 s, viral auto "
            f"{viral_auto:.1f})")
        if blocked:
            self._report_discard_once(
                clip_id,
                f"{self.streamer}: «{title}» descartado — sin voz "
                f"verificable: BLOQUEADO (queda en Cola para "
                f"transcripción humana; viral auto {viral_auto:.1f})")
        # 7. Paquete SEO + prioridad (intocable). El SEO post-corte
        # se mide sobre el paquete de PUBLICACIÓN (título Gemini +
        # descripción + tags + miniatura), NO sobre el título de
        # reglas sin tags: antes se puntuaba solo ese título y el
        # SEO colapsaba 92 -> 49, bloqueando publicar TODOS los
        # clips en vivo (2026-11-19, calibración). Si hubo revisión
        # se reutiliza su paquete pre-corte; si no (camino directo),
        # se construye el mismo paquete Gemini ahora. Lo visible es
        # el layout real del corte (no None): la coherencia pasa de
        # 80 a 100 cuando el juego está verificado y se ve.
        # Si hubo revisión, la viralidad post-corte usa sus valores
        # REALES (human Gemini + audio medido), no la estimación.
        if reviewed is not None:
            viral = _score_clip(detail, multiple,
                                human=reviewed.get("human"),
                                audio=reviewed.get("audio"))
        else:
            viral = _score_clip(detail, multiple)
        viral_total = viral.get("total")
        visible = _visible_from_layout(layout)
        pub_pkg = (reviewed or {}).get("package") or {}
        if not pub_pkg.get("best_title"):
            # Sin revisión (camino directo) o con el paquete pre-corte
            # sin construir: se construye el mismo paquete Gemini
            # ahora. Si TAMPOCO se puede (mismo fallo que en la
            # revisión), se cae al comportamiento de antes (título de
            # reglas, sin tags): el clip no muere por el paquete.
            try:
                from .live import _chat_json_path
                from .workers import pre_corte_package
                chat_path = _chat_json_path(self.detector.messages)
                median_score = (float(score) / float(multiple)
                                if multiple and float(multiple) > 0
                                else None)
                pub_pkg_full, _pub_seo = pre_corte_package(
                    chat_path, peak_ts, score, multiple, detail,
                    median_score=median_score, all_scores=[],
                    lang=self.lang, game="GTA 6", streamer=self.streamer,
                    clip_start=rec_start, duration=CLIP_DURATION_S,
                    category=category, visible=visible)
                pub_pkg = {
                    "best_title": pub_pkg_full.get("best_title"),
                    "description": pub_pkg_full.get(
                        "description_first_2_lines"),
                    "tags": pub_pkg_full.get("tags") or [],
                    "hashtags": pub_pkg_full.get("hashtags") or [],
                    "thumbnail_text": pub_pkg_full.get("thumbnail_text"),
                    "pkg_source": pub_pkg_full.get("pkg_source"),
                }
            except Exception:  # noqa: BLE001 - paquete de reglas
                pub_pkg = {}
        pub_title = pub_pkg.get("best_title") or title
        pkg = package_clip_step(
            final_clip, pub_title,
            description=pub_pkg.get("description") or "",
            tags=pub_pkg.get("tags") or [],
            hashtags=pub_pkg.get("hashtags") or [],
            thumbnail_text=pub_pkg.get("thumbnail_text"),
            game_verified=True, visible=visible, lang=self.lang)
        package = finalize_package(pkg["package_path"], viral_total,
                                   multiple, cfg=self.cfg)
        package["category"] = category
        package["game"] = "GTA 6"
        package["category_source"] = source
        package["origin"] = "live"
        package["peak_ts"] = peak_ts
        package["viral_auto"] = viral_auto
        if reviewed is not None:
            package["review"] = {
                "revision": reviewed.get("revision"),
                "viral_revisado": reviewed.get("viral_revisado"),
                "human": reviewed.get("human"),
                "seo_pre_corte": reviewed.get("seo"),
                "motivo": reviewed.get("motivo"),
            }
        with open(pkg["package_path"], "w", encoding="utf-8") as f:
            json.dump(package, f, ensure_ascii=False, indent=2)
        # 8. Puertas duras por plataforma (viralidad/SEO/duración).
        # Los umbrales salen de config.yaml (80/45), no de los
        # defectos 90/50 (2026-11-19, clips 24/25).
        from .policy import load_scoring_config
        from .priority import publish_status
        seo_gate, viral_min = load_scoring_config(self.cfg)
        seo = package.get("seo") or {}
        statuses = {}
        for platform, s in seo.items():
            statuses[platform] = publish_status(viral_total, s,
                                                seo_gate=seo_gate,
                                                viral_min=viral_min)
        record = {
            "clip_id": clip_id, "streamer": self.streamer,
            "platform": self.platform, "vod_id": self.vod_id,
            "peak_ts": peak_ts, "start_sec": int(rec_start),
            "end_sec": int(rec_start + CLIP_DURATION_S),
            "duration_s": CLIP_DURATION_S,
            "category": category, "category_source": source,
            "game": "GTA 6", "origin": "live",
            "score": score, "multiple": multiple,
            "viral_total": viral_total,
            "viral_auto": viral_auto,
            "review": ({"revision": reviewed.get("revision"),
                        "viral_revisado":
                            reviewed.get("viral_revisado"),
                        "human": reviewed.get("human"),
                        "motivo": reviewed.get("motivo"),
                        # Gritos SOLO del streamer (no del gameplay).
                        "shouts": (reviewed.get("audio") or {}).get(
                            "streamer_shout_count", 0),
                        "rescate_gritos": bool(
                            reviewed.get("streamer_shout_rescue"))}
                       if reviewed is not None else None),
            "burst_score": detail.get("burst_score"),
            "hype_score": detail.get("hype_score"),
            "hype_ratio": detail.get("hype_ratio"),
            "streamer_hype": streamer_hype,
            "title": pub_title, "burned_title": title,
            "clip": final_clip,
            "package_path": pkg["package_path"],
            "subtitles_complete": not blocked,
            "publish_blocked": blocked,
            "seo": seo, "publish_status": {
                p: {"status": s[0], "reason": s[1]}
                for p, s in statuses.items()},
            "at": datetime.now(timezone.utc).isoformat(),
        }
        # 8b. Validación final del preview con Gemini (2026-11-18):
        # ve el MP4 final y lo valida (webcam, título, subtítulos,
        # barras negras, coherencia). ENCENDIDA por defecto (orden
        # del usuario 2026-11-18 22:57, 100% automático; se apaga
        # solo con FINAL_REVIEW_ENABLED=0 o cfg enabled=False).
        # Rechazado -> no se encola nada. Pendiente
        # (sin Gemini) -> solo Cola del panel, jobs en draft.
        try:
            from .final_review import (
                maybe_review_final_clip, transcript_text_for_srt)
            final_review = maybe_review_final_clip(
                final_clip, title,
                transcript_text=transcript_text_for_srt(sub.get("srt")),
                out_dir=os.path.join(work_dir, "final_review"),
                cfg=self.cfg, gemini_fn=self.final_review_fn)
        except Exception as exc:  # noqa: BLE001 - sin validación
            # no hay veredicto: a revisión humana, nunca aprobado.
            final_review = {
                "decision": "pendiente_revision", "pasa": False,
                "revision": "no_disponible", "scores": {},
                "motivos": {}, "fallos": [],
                "motivo": f"validación final sin ejecutar: {exc}",
                "resumen": f"validación final sin ejecutar: {exc}",
                "frames_n": 0, "model": "",
                "transcript_words": 0}
        record["final_review"] = final_review
        package["final_review"] = {
            "decision": final_review.get("decision"),
            "pasa": final_review.get("pasa"),
            "fallos": final_review.get("fallos") or [],
            "motivo": final_review.get("motivo") or "",
            "resumen": final_review.get("resumen") or "",
            "scores": final_review.get("scores") or {},
            "frames_n": final_review.get("frames_n") or 0,
        }
        with open(pkg["package_path"], "w", encoding="utf-8") as f:
            json.dump(package, f, ensure_ascii=False, indent=2)
        self._record(record)
        # 9. Panel web (Cola LIVE) + publicación automática.
        fr_decision = (final_review or {}).get("decision")
        if fr_decision == "rechazado":
            self.skipped.append({
                "ts": peak_ts,
                "reason": f"validación final: "
                          f"{final_review.get('motivo')}",
                "viral_total": viral_total,
                "final_review": final_review.get("fallos"),
                "at": datetime.now(timezone.utc).isoformat()})
            self._report_discard_once(
                clip_id,
                f"{self.streamer}: «{pub_title}» descartado — "
                f"validación final: {final_review.get('motivo')} "
                f"(viralidad {viral_total})")
            return record
        # Puerta de publicación (2026-11-19): si ninguna plataforma
        # es publicable y no hubo rescate por gritos, el clip no
        # entra a la ruta de publicación: queda en la Cola como
        # borrador sin jobs (igual que hoy) y se reporta el
        # descarte con el motivo exacto de la puerta (viralidad o
        # SEO, con los 4 números). Sin plataformas medidas no hay
        # descarte que reportar. El umbral sale de config.yaml
        # (scoring), no va cableado: si el listón cambia, el
        # evento lo sigue.
        _rescue = bool((record.get("review") or {}).get(
            "rescate_gritos"))
        if (statuses and not _rescue
                and not any(s[0] in ("publicable",
                                     "publicable_override")
                            for s in statuses.values())):
            if all(s[0] == "descartado" for s in statuses.values()):
                _why = next(iter(statuses.values()))[1]
            else:
                _why = (f"SEO post-corte < {seo_gate} "
                        f"({_seo_summary(seo)}; viralidad "
                        f"{viral_total})")
            self._report_discard_once(
                clip_id,
                f"{self.streamer}: «{pub_title}» descartado — {_why}")
        panel_clip_id = None
        if self.bridge is not None and not self.dry_run:
            panel_clip_id = self._enqueue_panel(record)
        if not self.dry_run:
            self._enqueue_publish(
                record, panel_clip_id,
                force_draft=(fr_decision == "pendiente_revision"))
        return record

    def _enqueue_panel(self, record):
        """Clip -> Cola LIVE del panel (origin='live', MP4 en R2)."""
        if self.bridge is None:
            return None
        try:
            # El score de la Cola es la prioridad de YouTube (la
            # fórmula vive en el cerebro; el panel solo ordena).
            # También se lee la descripción y los tags del paquete
            # para el payload (2026-11-19, calibración: antes la
            # descripción llegaba vacía al panel).
            yt_priority = None
            pkg_desc, pkg_tags = "", []
            try:
                with open(record["package_path"],
                          encoding="utf-8") as f:
                    pkg = json.load(f)
                yt_priority = (pkg.get("priority") or {}).get(
                    "youtube_shorts", {}).get("priority")
                pkg_desc = pkg.get("description") or ""
                pkg_tags = pkg.get("tags") or []
            except (OSError, ValueError):
                yt_priority = None
            _reason = (f"LIVE GTA VI ({record['category']}, "
                       f"{record['category_source']}) — hype "
                       f"{record.get('hype_score')} — viralidad "
                       f"{record.get('viral_total')}")
            _review = record.get("review") or {}
            if _review:
                _reason += (f" — revisión {_review.get('revision')}: "
                            f"{record.get('viral_auto')} -> "
                            f"{_review.get('viral_revisado')}")
            _sh = record.get("streamer_hype") or {}
            if _sh.get("count"):
                _reason += (" — streamer: "
                            + ", ".join(_sh.get("phrases") or []))
            _shouts = (record.get("review") or {}).get("shouts")
            if _shouts:
                _reason += f" — gritos: {_shouts}"
            # RESCATE por gritos (2026-11-18, decisión del usuario):
            # el clip con viralidad revisada 45-49 y 2+ s de gritos
            # entra a la Cola BLOQUEADO para revisión humana. El
            # panel re-verifica la marca (viralidad, gritos, SEO);
            # sin estos campos el POST 400 lo tira como antes.
            _review = record.get("review") or {}
            _is_rescue = bool(_review.get("rescate_gritos"))
            _viral_revisado = _review.get("viral_revisado")
            payload = {
                "title": record["title"],
                "start_sec": record["start_sec"],
                "end_sec": record["end_sec"],
                "score": int(round(yt_priority))
                if yt_priority is not None else 0,
                "platforms": ["youtube_shorts", "tiktok",
                              "instagram_reels", "facebook_reels"],
                "influencer_id": self.streamer,
                "source_id": int(self.vod_id or 0),
                "seo_title": record["title"],
                "seo_description": pkg_desc,
                "seo_tags": pkg_tags,
                "subtitles_complete": 1 if record["subtitles_complete"]
                else 0,
                "needs_human_transcription": 1 if record["publish_blocked"]
                else 0,
                "origin": "live",
                # 2026-11-19 (clips 24/25, Kai Cenat): el payload no
                # llevaba idioma y el panel lo dejaba en "es" por
                # defecto, así que los clips EN iban a la cola ES.
                "language": self.lang,
                "game": "GTA VI",
                "reason": _reason,
            }
            if _is_rescue:
                # Solo en rescate se mandan viralidad real + SEO:
                # sin ellos el panel no puede verificar la marca.
                # En clips normales el payload no cambia (la puerta
                # SEO del POST no debe empezar a tirar clips que
                # antes entraban a la Cola y se frenaban al publicar).
                payload["rescue"] = True
                payload["rescue_shouts"] = int(_shouts or 0)
                payload["viral_score"] = float(_viral_revisado) \
                    if _viral_revisado is not None else float(
                        record.get("viral_total") or 0)
                payload["seo_scores"] = record.get("seo") or {}
            clip = self.bridge.create_clip(payload)
            panel_id = clip.get("id")
            preview_ok = False
            if panel_id and os.path.isfile(record["clip"]):
                self.bridge.upload_video(panel_id, record["clip"])
                # Preview validado automáticamente (2026-11-19): el
                # render ya pasó la validación final del cerebro
                # (paso 8b, Gemini ve el MP4 final). Solo con
                # decisión "aprobado": cualquier otro veredicto
                # (pendiente/rechazado/desactivada/sin registro)
                # NO marca nada y el freno sigue. Si el marcado
                # falla, el clip queda en la Cola con
                # preview_seen=0: nunca tumba el encolado.
                if (record.get("final_review") or {}).get(
                        "decision") == "aprobado":
                    try:
                        self.bridge.preview_seen(panel_id)
                        preview_ok = True
                    except Exception:  # noqa: BLE001 - no tumba
                        pass
            # Encolado en la Cola del panel (2026-11-19): create_clip
            # + upload_video con éxito, con viralidad y SEO.
            if panel_id:
                self._report_event(
                    "clip_encolado",
                    f"{self.streamer}: «{record['title']}» encolado "
                    f"(viralidad {record.get('viral_total')}; SEO "
                    f"{_seo_summary(record.get('seo'))})")
            for step, msg in (
                    ("detectado", f"LIVE {self.streamer}: pico a "
                     f"{record['peak_ts']:.0f}s"),
                    ("pico", f"hype {record.get('hype_score')} "
                     f"viralidad {record.get('viral_total')}"),
                    ("cortando", f"clip 40 s {record['start_sec']}-"
                     f"{record['end_sec']}"),
                    ("subtitulos", "subtítulos completos quemados"
                     if record["subtitles_complete"] else
                     "sin voz verificable: BLOQUEADO"),
                    ("paquete", f"SEO {record.get('seo')}"),
                    ("cola_publicacion",
                     "en Cola LIVE del panel")):
                self._track(record["clip_id"], step, msg, panel_id)
            if preview_ok:
                self._track(
                    record["clip_id"], "revision",
                    "Preview validado automáticamente: validación "
                    "final aprobada", panel_id)
            return panel_id
        except Exception as e:  # noqa: BLE001 - el panel no tumba
            self.skipped.append(
                {"ts": record["peak_ts"],
                 "reason": f"panel: no se pudo encolar ({e})",
                 "at": datetime.now(timezone.utc).isoformat()})
            self._report_event(
                "corte_fallido",
                f"{self.streamer}: «{record['title']}» no se pudo "
                f"encolar en el panel ({e})")
            return None

    def _enqueue_publish(self, record, panel_clip_id=None,
                         force_draft=False):
        """Publicación por plataforma (sin humano por clip).

        Orden del usuario 2026-11-18 14:37 (vigente): los clips van
        a la COLA del panel, sin publicar en público todavía. Por
        eso, por defecto (auto_publish=False), las plataformas
        publicables se encolan en mode draft: listas para publicar,
        no publicadas. Con auto_publish=True se encolan en mode api
        (publicación automática, MÁSTER PROMPT FINAL). El resto
        (bloqueado_seo / descartado) no se encola nunca."""
        if record.get("publish_blocked"):
            return []
        from .workers import enqueue_publish
        # ``force_draft=True`` (validación final pendiente, sin
        # Gemini): aunque auto_publish esté encendido, el clip solo
        # queda en borrador para revisión humana.
        mode = "draft" if force_draft else (
            "api" if self.auto_publish else "draft")
        created = []
        for platform, st in (record.get("publish_status") or {}).items():
            if st.get("status") == "publicable":
                job = enqueue_publish(
                    self.store, record["clip_id"], platform,
                    record["clip"], mode=mode,
                    prioridad=float(record.get("viral_total") or 0))
                created.append((platform, job.get("id"), mode))
        return created

    # -- modo en vivo ----------------------------------------------------
    def run_live(self, duration_s=1500, tick_s=15):
        """Sesión en vivo completa: verificar vivo + categoría,
        grabar, escuchar IRC, cortar al detectar picos."""
        from .streamers import is_allowed
        if not is_allowed(self.streamer):
            raise RuntimeError(
                f"{self.streamer!r} no está en streamers.yaml "
                "(sin permiso: no se sigue)")
        # ¿Está en vivo y en GTA VI? (Helix / Kick API.)
        ok, category, source = self.gate.check()
        if self.gate.mode == "live" and not ok:
            # Directo terminado (o cambiado de juego) entre el
            # detect y el corte: lectura OK con categoría vacía o
            # no GTA VI. Es normal (caso xqc 2026-11-18: el Kick
            # devolvió categoría '' al empezar el corte) -> salida
            # limpia (exit 0, sin picos), no fallo del job. Solo
            # un error de lectura ("sin lectura ...") sigue siendo
            # un fallo real y se eleva.
            if source.startswith("sin lectura"):
                raise RuntimeError(
                    f"no se corta: sin lectura de categoría "
                    f"({source})")
            print(f"Directo terminado o fuera de GTA VI antes de "
                  f"cortar (categoría {category!r} vía {source}): "
                  f"sin sesión, salida limpia.")
            return []
        if self.recorder is None:
            self.recorder = StreamRecorder(self.streamer,
                                           platform=self.platform)
        self.recorder.start()
        self.start_cutter_thread()
        try:
            if self.platform == "kick":
                from .live import KickPusherClient
                source_obj = KickPusherClient(self.streamer)
                ok_conn, msg = source_obj.connect()
                if not ok_conn:
                    raise RuntimeError(f"Kick: {msg}")
            else:
                from .live import TwitchIRCClient
                source_obj = TwitchIRCClient([self.streamer])
                source_obj.connect()
            peaks = super().run(source_obj, duration_s=duration_s,
                                tick_s=tick_s)
        finally:
            self.stop_cutter_thread()
            if self.recorder is not None:
                self.recorder.stop()
        return peaks

    # -- modo replay (VOD): mismo código, sin directo ---------------------
    def replay(self, chat_path, video_path, video_offset=0.0,
               tick_every_s=30):
        """Reproduce un VOD como si fuera en vivo: alimenta el chat
        en orden, el detector dispara picos y el cutter corta del
        archivo. La categoría sale de los capítulos del chat.json.
        Devuelve (picos, clips cortados, descartados)."""
        import json as _json
        self.gate.mode = "vod"
        self.gate.chat_path = chat_path
        self.recorder = FileRecorder(video_path, offset=video_offset)
        self.start_cutter_thread()
        try:
            with open(chat_path, encoding="utf-8") as f:
                data = _json.load(f)
            messages = []
            for c in data.get("comments") or []:
                try:
                    off = float(c.get("content_offset_seconds") or 0)
                except (TypeError, ValueError):
                    continue
                body = (c.get("message") or {}).get("body") or ""
                user = (c.get("commenter") or {}).get("name") or "?"
                messages.append((user, body, off))
            messages.sort(key=lambda m: m[2])
            if not messages:
                return [], [], self.skipped
            t0 = messages[0][2]
            detector_t0 = time.time() - t0
            self.detector.t0 = detector_t0
            last_tick = -1e9
            for user, body, off in messages:
                self.detector.add_message(user, body,
                                          detector_t0 + off)
                if off - last_tick >= tick_every_s:
                    last_tick = off
                    for peak in self.detector.tick():
                        self._on_peak(peak, lambda e, d: None)
            # Último tick al final del chat.
            for peak in self.detector.tick():
                self._on_peak(peak, lambda e, d: None)
            self._cut_queue.join()
            return list(self.peaks), list(self.cut_clips), list(self.skipped)
        finally:
            self.stop_cutter_thread()


def run_live_cut(streamer, platform="twitch", minutes=25,
                 out_dir=None, dry_run=False, bridge=None,
                 cfg=None, auto_publish=False, lang=None):
    """Entrada del CLI / workflow: sesión en vivo con corte.

    ``lang``: idioma del canal ("es"/"en"); si es None se resuelve
    por streamers.yaml en la sesión.
    """
    from .config import load_config
    cfg = cfg or (load_config() or {})
    live_cfg = (cfg.get("live") or {})
    limits = LiveLimits(
        cooldown_minutes=live_cfg.get("cooldown_minutes", 10),
        max_clips_per_stream=live_cfg.get("max_clips_per_stream", 5))
    gate = CategoryGate(platform=platform, streamer=streamer,
                        mode="live", cfg=cfg)
    session = LiveCutterSession(
        streamer, platform=platform, limits=limits, gate=gate,
        out_dir=out_dir, cfg=cfg, dry_run=dry_run, bridge=bridge,
        auto_publish=auto_publish, lang=lang)
    # Fase en vivo (2026-11-19): el panel muestra "Cortando clip".
    # Nunca tumba la sesión: sin credenciales o sin panel, solo
    # queda en el log y se corta igual.
    try:
        from .panel_bridge import report_brain_phase
        report_brain_phase("cortando_clip", streamer, bridge=bridge)
    except Exception:  # noqa: BLE001 - la fase es informativa
        pass
    peaks = session.run_live(duration_s=minutes * 60)
    return {"peaks": peaks, "clips": session.cut_clips,
            "skipped": session.skipped,
            "category_log": gate.log}
