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
from .quality_gate import QualityDiscardTracker

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

HELD_TITLE = "Sin título — revisión pendiente"
HOLD_REASON = "Sin título honesto: retención para revisión"


def _transcript_text_from_sub(sub):
    """Texto de la transcripción de un resultado de subtitle_clip.

    Lee el .transcript.json si existe; si no, el SRT. "" si no hay
    transcripción (sin voz o transcripción fallida)."""
    sub = sub or {}
    tpath = sub.get("transcript")
    if not tpath and sub.get("srt"):
        tpath = os.path.splitext(sub["srt"])[0] + ".transcript.json"
    if tpath and os.path.isfile(tpath):
        try:
            with open(tpath, encoding="utf-8") as f:
                data = json.load(f)
            parts = [str(s.get("text") or "")
                     for s in data.get("segments") or []]
            text = " ".join(p for p in parts if p).strip()
            if text:
                return text
        except (OSError, ValueError):
            pass
    if sub.get("srt") and os.path.isfile(sub["srt"]):
        try:
            from .final_review import transcript_text_for_srt
            return transcript_text_for_srt(sub["srt"]) or ""
        except Exception:  # noqa: BLE001 - sin texto, sin paquete
            return ""
    return ""


def _transcript_dict_from_sub(sub):
    """Transcripción cruda de un resultado de subtitle_clip.

    Lee el .transcript.json (faster-whisper, con timestamps
    palabra por palabra) para la validación del gancho hablado
    (``clipagent.beats``). None si no existe o no parsea: el
    llamador sigue sin beats, nunca tumba el corte.
    """
    sub = sub or {}
    tpath = sub.get("transcript")
    if not tpath and sub.get("srt"):
        tpath = os.path.splitext(sub["srt"])[0] + ".transcript.json"
    if tpath and os.path.isfile(tpath):
        try:
            with open(tpath, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("segments"):
                return data
        except (OSError, ValueError):
            pass
    return None


def _timed_segments_from_sub(sub):
    """Segmentos {start, end, text} de la transcripción (para el
    tramo del payoff ±20 s en el prompt, R1.1). [] sin ellos."""
    sub = sub or {}
    tpath = sub.get("transcript")
    if not tpath and sub.get("srt"):
        tpath = os.path.splitext(sub["srt"])[0] + ".transcript.json"
    if tpath and os.path.isfile(tpath):
        try:
            from .moment_card import parse_transcript_json
            segs, _lang = parse_transcript_json(tpath)
            if segs:
                return segs
        except Exception:  # noqa: BLE001 - sin tiempos, sin tramo
            pass
    if sub.get("srt") and os.path.isfile(sub["srt"]):
        try:
            from .moment_card import parse_srt
            return parse_srt(sub["srt"])
        except Exception:  # noqa: BLE001 - sin tiempos, sin tramo
            return []
    return []


def _recent_titles(bridge, days=30):
    """(recent_titles, dedupe_verified) desde el panel (R4.1).

    Sin bridge no hay lectura: (None, False). Si la lectura
    falla, fail-open SOLO en dedupe: (None, False); la veracidad
    nunca depende de esta lectura.
    """
    if bridge is None:
        return None, False
    try:
        return bridge.list_titles(days=days), True
    except Exception as e:  # noqa: BLE001 - dedupe no verificado
        print(f"[dedupe] list_titles falló ({e}): dedupe no "
              f"verificado", flush=True)
        return None, False


def _call_package_clip(**kwargs):
    """package_clip con la interfaz fija R4.1 (recent_titles,
    dedupe_verified). Vive en workers.call_package_clip; aquí
    solo se re-exporta para los tests del reordenamiento."""
    from .workers import call_package_clip
    return call_package_clip(**kwargs)


# Caché por proceso de los packs de patrones (spec v3 §3.2):
# una corrida carga cada informe de idioma una vez; None también
# se cachea (sin informe válido no se reintenta leerlo).
_PATTERN_PACK_CACHE = {}


def _pattern_pack_for_lang(lang, base_dir=None):
    """Pack vigente para el idioma del clip, o None.

    Sin informe válido (o con muestra < 5 u obsoleto) devuelve
    None y el paquete se construye como hoy, sin pack. Nunca
    levanta.
    """
    lang = lang if lang in ("es", "en") else "es"
    if lang not in _PATTERN_PACK_CACHE:
        try:
            from reel_study import load_pattern_pack
            base = base_dir or os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))
            _PATTERN_PACK_CACHE[lang] = load_pattern_pack(lang, base)
        except Exception:
            _PATTERN_PACK_CACHE[lang] = None
    return _PATTERN_PACK_CACHE[lang]


def _definitive_package(n, peak_ts, score, detail, median_score,
                        lang, streamer, rec_start, category, visible,
                        layout, chat_path, sub, bridge, vod_id):
    """Paquete DEFINITIVO post-transcripción (R1.1, §6.1).

    Construye la ficha del momento CON la transcripción (SRT +
    .transcript.json de la llamada 1 de subtitle_clip, texto
    completo, sin truncar) y llama a package_clip con
    register=True y la lista de títulos recientes del panel.
    Devuelve (pkg, transcript_text, dedupe_verified); pkg es {}
    si package_clip levanta (el llamador retiene, nunca inventa
    título de reglas).
    """
    from .ingest import sample_messages
    from .moment_card import build_moment_card
    d = detail or {}
    transcript_text = _transcript_text_from_sub(sub)
    try:
        card = build_moment_card(
            clip_path=None, chat_path=chat_path,
            srt_path=(sub or {}).get("srt"),
            transcript_path=(sub or {}).get("transcript"),
            streamer=streamer or "", game="GTA 6",
            game_source="categoría verificada (live)",
            language=lang if lang in ("es", "en") else "es",
            peak_ts_vod=peak_ts, clip_start_vod=rec_start,
            clip_duration_s=CLIP_DURATION_S, score=score,
            risas=d.get("risas", 0), hype=d.get("hype", 0),
            median_score=median_score, vod_id=vod_id,
            category=category)
    except Exception as e:  # noqa: BLE001 - ficha mínima, no tumba
        print(f"[paquete] ficha del momento falló ({e})",
              flush=True)
        card = {"game": "GTA 6", "streamer": streamer or "",
                "language": lang, "transcript_text": transcript_text,
                "risas": d.get("risas", 0), "hype": d.get("hype", 0)}
    if not (card.get("transcript_text") or "").strip() \
            and transcript_text:
        card["transcript_text"] = transcript_text
    card["transcript_segments_timed"] = _timed_segments_from_sub(sub)
    recent_titles, dedupe_verified = _recent_titles(bridge)
    examples = []
    if chat_path:
        try:
            examples = sample_messages(chat_path, int(peak_ts),
                                       window_size=60)
        except Exception:  # noqa: BLE001 - sin ejemplos, no tumba
            examples = []
    try:
        pkg = _call_package_clip(
            n=n, peak_ts=peak_ts, score=score,
            risas=d.get("risas", 0), hype=d.get("hype", 0),
            median_score=median_score, all_scores=[],
            lang=lang, examples=examples, moment_card=card,
            game="GTA 6", streamer=streamer, vod_id=vod_id,
            register=True, visible=visible, layout=layout,
            recent_titles=recent_titles,
            dedupe_verified=dedupe_verified,
            reel_patterns=_pattern_pack_for_lang(lang))
    except Exception as e:  # noqa: BLE001 - sin paquete, retención
        print(f"[paquete] package_clip falló ({e}): retención",
              flush=True)
        pkg = {}
    return pkg or {}, (card.get("transcript_text") or ""), \
        dedupe_verified


def _publication_package(reviewed, detector, peak_ts, score, multiple,
                         detail, median_score, lang, streamer, rec_start,
                         category, visible):
    """Paquete de publicación (título Gemini + descripción + tags +
    miniatura) para el quemado y el panel.

    NOTA (spec títulos-verdad, §6.1): el paso 5b del cutter YA NO
    usa esta función — el paquete definitivo se construye
    post-transcripción con `_definitive_package`. Esta función
    queda para la revisión pre-corte (review_peak) y sus tests: su
    título es PROVISIONAL (sin transcripción) y nunca debe
    filtrarse al record ni al panel cuando existe título
    definitivo.

    Reutiliza el paquete pre-corte de la revisión si trae
    best_title; si no (camino directo, o paquete pre-corte sin
    construir), lo construye ahora con pre_corte_package (los
    mismos datos que usaba el paso 7). Si no se puede construir,
    {} (el llamador cae al título de reglas): el clip NUNCA muere
    porque falle Gemini. Sin detector (tests) el paquete se
    construye sin chat.

    Devuelve el dict tal cual lo usa el paso 7 (best_title,
    description, tags, hashtags, thumbnail_text, pkg_source).
    """
    pub_pkg = (reviewed or {}).get("package") or {}
    if pub_pkg.get("best_title"):
        return pub_pkg
    try:
        from .live import _chat_json_path
        from .workers import CLIP_DURATION_S, pre_corte_package
        chat_path = (_chat_json_path(detector.messages)
                     if detector is not None else None)
        pub_pkg_full, _pub_seo = pre_corte_package(
            chat_path, peak_ts, score, multiple, detail,
            median_score=median_score, all_scores=[],
            lang=lang, game="GTA 6", streamer=streamer,
            clip_start=rec_start, duration=CLIP_DURATION_S,
            category=category, visible=visible)
        return {
            "best_title": pub_pkg_full.get("best_title"),
            "description": pub_pkg_full.get(
                "description_first_2_lines"),
            "tags": pub_pkg_full.get("tags") or [],
            "hashtags": pub_pkg_full.get("hashtags") or [],
            "thumbnail_text": pub_pkg_full.get("thumbnail_text"),
            "pkg_source": pub_pkg_full.get("pkg_source"),
        }
    except Exception:  # noqa: BLE001 - paquete de reglas
        return {}


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
        # Puerta de calidad B4: contador diario de descartes +
        # alerta del 30% (estado local en out_dir, nunca en D1).
        self._quality_tracker = QualityDiscardTracker(
            os.path.join(self.out_dir, "quality_gate_daily.json"))
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

    def _rerender_after_gate(self, final_review, final_clip, sub,
                             work_dir, video_for_cut, cut_start,
                             title, n, hype_s=None, plantilla=None):
        """UN re-render tras un fallo de la puerta dura (2026-11-19).

        Re-corta el mismo tramo con la cara forzada (facecam_box de
        la cadena face-crop -> bordes -> YuNet en el segmento
        fuente) + re-quema subtítulos y título (reutiliza el SRT ya
        transcrito) + SFX por defecto (zoom en los momentos de hype
        ya lo lleva el re-corte, mismo hype_s), y vuelve a validar
        el vídeo re-hecho. Devuelve (final_review, final_clip, sub)
        del re-render si salió, o la revisión original (con
        needs_human_review=True) si no se pudo re-hacer o sigue
        fallando: el llamador NO encola y deja el motivo visible
        para revisión humana. Nunca levanta salvo ConfigError (falta
        la config del gate: falla en voz alta, no se captura).
        """
        from .final_review import (
            find_facecam_box_in_source, transcript_text_for_srt,
            validate_final_clip)
        from .validate import ConfigError
        from .workers import apply_sfx, cut_clip, subtitle_clip
        hard = final_review.get("hard_failures") or []
        motivo = final_review.get("motivo") or ""
        face_box = None
        # Rescate de layout (spec §2.2B, formato sin webcam): el
        # fallo de plantilla por duplicación o cámara contaminada
        # (§6.3) también dispara el rescate — antes solo lo hacía
        # "cara" en hard, y en los 10 EN cara no estaba en los
        # fallos duros. La decisión sobre el segmento fuente dice
        # si el re-render es rubius con caja forzada o sin webcam.
        layout_fallo = ("plantilla" in hard
                        and ("layout" in motivo
                             or "cámara contaminada" in motivo))
        sin_webcam = False
        if "cara" in hard:
            found = find_facecam_box_in_source(
                video_for_cut, CLIP_DURATION_S, self.cfg)
            if found:
                face_box = found["box"]
            elif not layout_fallo:
                review = dict(final_review)
                review["needs_human_review"] = True
                review["motivo"] = (
                    (final_review.get("motivo") or "")
                    + " — sin cara detectable en la fuente: "
                    "revisión humana")
                return review, final_clip, sub
            # Sin caja y con fallo de layout: cae al rescate de
            # layout de abajo (quizá no hay cara en la fuente).
        if face_box is None and layout_fallo:
            from .template_decision import (
                decide_template_for_segment)
            try:
                _dec = decide_template_for_segment(
                    video_for_cut, CLIP_DURATION_S, cfg=self.cfg)
            except Exception:  # noqa: BLE001 - sin decisión,
                # revisión humana, como hoy.
                _dec = ("rubius", None, "ambiguo")
            if _dec[0] == "rubius_sin_webcam":
                sin_webcam = True
                plantilla = "rubius_sin_webcam"
            elif len(_dec) == 3 and _dec[2] == "ambiguo":
                review = dict(final_review)
                review["needs_human_review"] = True
                review["motivo"] = (
                    (motivo or "")
                    + " — sin decisión concluyente sobre la fuente: "
                    "revisión humana")
                return review, final_clip, sub
            elif _dec[1]:
                face_box = _dec[1]
            else:
                review = dict(final_review)
                review["needs_human_review"] = True
                review["motivo"] = (
                    (motivo or "")
                    + " — rubius sin caja en la fuente: "
                    "revisión humana")
                return review, final_clip, sub
        cfg2 = dict(self.cfg or {})
        if face_box is not None:
            cfg2["facecam_box"] = face_box
        raw2 = os.path.join(work_dir, f"clip_{n:02d}_r1.mp4")
        try:
            cut_clip(video_for_cut, cut_start, CLIP_DURATION_S,
                     raw2,
                     layout_mode="game" if sin_webcam else "auto",
                     cfg=cfg2, hype_s=hype_s)
        except Exception:  # noqa: BLE001 - sin re-render, no tumba
            review = dict(final_review)
            review["needs_human_review"] = True
            return review, final_clip, sub
        burned2 = os.path.join(work_dir, f"clip_{n:02d}_r1_subs.mp4")
        try:
            sub2 = subtitle_clip(
                raw2, srt=sub.get("srt"), burn=burned2, cfg=cfg2,
                lang=self.lang, title=title)
        except Exception:  # noqa: BLE001 - sin re-render, no tumba
            review = dict(final_review)
            review["needs_human_review"] = True
            return review, final_clip, sub
        final2 = burned2 if os.path.isfile(burned2) else raw2
        _sfx2 = None
        try:
            # Tras un re-render, borrar el archivo _sfx antes de
            # re-mezclar (spec títulos-verdad, §6.1): apply_sfx
            # reutiliza el existente y dejaría el audio viejo.
            _base, _ext = os.path.splitext(final2)
            try:
                os.remove(f"{_base}_sfx{_ext}")
            except OSError:
                pass
            final2, _sfx2 = apply_sfx(
                final2, transcript_path=sub2.get("transcript"),
                srt_path=sub2.get("srt"), payoff_s=float(CLIP_PRE_S),
                cfg=cfg2)
        except Exception:  # noqa: BLE001 - el SFX nunca tumba
            pass
        self._report_event(
            "clip_rehecho",
            f"{self.streamer}: «{title}» re-hecho tras la puerta "
            f"dura ({final_review.get('rerender_reason') or ''})")
        try:
            review2 = validate_final_clip(
                final2, title,
                transcript_text=transcript_text_for_srt(
                    sub2.get("srt")),
                srt_path=sub2.get("srt"),
                out_dir=os.path.join(work_dir, "final_review_r1"),
                cfg=self.cfg, gemini_fn=self.final_review_fn,
                plantilla=plantilla,
                sfx_events=(_sfx2 or {}).get("events"))
        except ConfigError:
            raise
        except Exception:  # noqa: BLE001 - sin veredicto, no tumba
            review = dict(final_review)
            review["needs_human_review"] = True
            return review, final_clip, sub
        review2 = dict(review2)
        review2["rerendered_from"] = final_clip
        review2["rerender_facecam_box"] = face_box
        if review2.get("decision") == "rechazado":
            review2["needs_human_review"] = True
        return review2, final2, sub2

    # -- el corte de UN pico -------------------------------------------
    def process_peak(self, peak):
        """Pico (ts, score, multiple, detail) -> clip + cola + publish.

        Devuelve el registro del clip, o None si se descartó (con
        el motivo en self.skipped).
        """
        from .workers import (CLIP_DURATION_S, cut_clip,
                              ensure_shorts_fifth, package_clip_step,
                              subtitle_clip, write_held_package)
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
        # 4b. Segundos de hype para el zoom (2026-11-19): gritos
        # cerca del pico en el audio de la ventana (sin
        # transcripción: el pico lo eligió el hype del chat, así
        # que el salto cerca de él es la reacción del streamer).
        # Sin saltos, el pico: la ventana va sobre él.
        hype_s = [float(CLIP_PRE_S)]
        try:
            from .render import hype_seconds_from_audio
            spikes = hype_seconds_from_audio(
                video_for_cut, float(cut_start) + CLIP_PRE_S,
                radius_s=10.0)
            if spikes:
                hype_s = sorted(
                    float(s) - float(cut_start) for s in spikes)
                if float(CLIP_PRE_S) not in hype_s:
                    hype_s = sorted(hype_s + [float(CLIP_PRE_S)])
        except Exception:
            pass
        raw_clip = os.path.join(work_dir, f"clip_{n:02d}.mp4")
        # Pre-chequeo §2.2A (spec rubius sin webcam): la decisión
        # sobre el segmento fuente se toma ANTES del primer render.
        # Si la fuente no tiene facecam, se corta directo como
        # rubius_sin_webcam (layout "game", gameplay a pantalla
        # completa); si es ambiguo, rubius normal (la puerta de
        # cara decide después). La decisión se guarda: hasta que
        # el render devuelva la clave "plantilla" en la decisión
        # (§9.1, render.py), el validador usa la decidida aquí.
        layout_mode = "auto"
        plantilla_decidida = None
        try:
            from .template_decision import (
                decide_template_for_segment)
            _dec = decide_template_for_segment(
                video_for_cut, CLIP_DURATION_S, cfg=self.cfg)
            if _dec[0] == "rubius_sin_webcam":
                layout_mode = "game"
                plantilla_decidida = "rubius_sin_webcam"
        except Exception:  # noqa: BLE001 - sin decisión, rubius
            layout_mode = "auto"
        cut = cut_clip(video_for_cut, cut_start, CLIP_DURATION_S,
                       raw_clip, layout_mode=layout_mode, cfg=self.cfg,
                       hype_s=hype_s)
        layout = cut.get("layout") or {}
        # Plantilla del clip (2026-11-19): la que produce el render
        # (variante "rdjavi" -> willyrex v2); si el render declara
        # "plantilla", manda. Sin ella, el validador usa el default
        # de config.yaml (rubius).
        from .config import template_for_variant
        plantilla = (layout.get("plantilla") or plantilla_decidida
                     or template_for_variant(
                         layout.get("split_variant")))
        # 5b. TRANSCRIPCIÓN ANTES DEL PAQUETE (spec títulos-verdad
        # §6.1, R1.1): el título se decide DESPUÉS de transcribir.
        # Llamada 1 a subtitle_clip con burn=None: transcribe el
        # audio, escribe el SRT y el .transcript.json, sin quemar
        # nada todavía. Si la transcripción falla, no hay título
        # honesto posible desde el vídeo: retención (§11), nunca
        # título de reglas (build_title está eliminado).
        visible = _visible_from_layout(layout)
        burned = os.path.join(work_dir, f"clip_{n:02d}_subs.mp4")
        try:
            sub1 = subtitle_clip(raw_clip, burn=None, cfg=self.cfg,
                                 lang=self.lang)
        except Exception as exc:  # noqa: BLE001 - sin transcripción
            print(f"[live] transcripción falló ({exc}): retención "
                  f"para revisión", flush=True)
            sub1 = None
        transcription_failed = sub1 is None
        no_speech = bool((sub1 or {}).get("no_speech"))
        # 5c. Paquete DEFINITIVO con la transcripción (ficha CON
        # transcript_text completo; register=True; dedupe contra el
        # panel). El título provisional pre-corte NO se usa aquí:
        # ni el de reviewed.package ni el de pre_corte_package.
        from .live import _chat_json_path
        _detector = getattr(self, "detector", None)
        chat_path = (_chat_json_path(_detector.messages)
                     if _detector is not None else None)
        pub_pkg, _transcript_text, dedupe_verified = \
            _definitive_package(
                n, peak_ts, score, detail,
                median_score=(float(score) / float(multiple)
                              if multiple and float(multiple) > 0
                              else None),
                lang=self.lang, streamer=self.streamer,
                rec_start=rec_start, category=category,
                visible=visible, layout=layout, chat_path=chat_path,
                sub=sub1, bridge=self.bridge, vod_id=self.vod_id)
        best_title = (pub_pkg or {}).get("best_title")
        # Un clip SIN VOZ con transcript no_speech NO es retención
        # automática: si package_clip dio título honesto, sigue la
        # regla sin-voz (se publica sin subtítulos si pasa las
        # puertas). La retención es solo cuando no hay título
        # honesto (best None) o no hubo transcripción que lo
        # anclara.
        needs_human_review = bool(
            transcription_failed or not best_title
            or (pub_pkg or {}).get("needs_human_review"))
        # Humanizador (adopción 1, 2026-11-26): si el paquete trae su
        # propio motivo de retención (FLAGGED por marca de IA), ese es
        # el hold_reason que viaja al panel; si no, el genérico R3.4.
        hold_reason = ((pub_pkg or {}).get("hold_reason")
                       if needs_human_review else None) or HOLD_REASON
        if needs_human_review:
            print(f"RETENIDO {self.streamer} pico {peak_ts:.0f}s: "
                  f"{hold_reason}", flush=True)
            title = HELD_TITLE
            burned_title = ""
            pkg_source = "held"
            thumbnail_text = None
        else:
            title = best_title
            burned_title = title
            pkg_source = (pub_pkg or {}).get("pkg_source")
            thumbnail_text = (pub_pkg or {}).get("thumbnail_text")
        # 6. Quemado conjunto (título + subtítulos, misma pasada):
        # llamada 2 a subtitle_clip reutilizando el SRT de la
        # llamada 1 (sin re-transcribir). Con retención, title=None
        # (burn_subtitles no dibuja banner con title falsy) y sin
        # SRT no hay quemado: el clip final es el corte crudo.
        if sub1 is not None:
            sub = subtitle_clip(
                raw_clip, srt=sub1.get("srt"), burn=burned,
                cfg=self.cfg, lang=self.lang,
                title=(burned_title or None))
            final_clip = burned if os.path.isfile(burned) else raw_clip
        else:
            sub = {"srt": None, "transcript": None, "no_speech": True}
            final_clip = raw_clip
        no_speech = bool(sub.get("no_speech"))
        # 6a. SFX por defecto (2026-11-19): boom/riser en el payoff
        # (pico en CLIP_PRE_S) + lift de volumen, sobre el MP4
        # final y antes de la validación final (el validador ve el
        # archivo que se publica). Si la mezcla falla, el clip
        # sigue sin SFX: nunca lo tumba. También sin voz
        # verificable: desde 2026-11-19 esos clips se publican
        # igual (muchas veces es un grito, no silencio) y el boom
        # va sobre el dip de silencio, no sobre el grito.
        sfx_info = {}
        from .workers import apply_sfx
        final_clip, sfx_info = apply_sfx(
            final_clip, transcript_path=sub.get("transcript"),
            srt_path=sub.get("srt"), payoff_s=float(CLIP_PRE_S),
            cfg=self.cfg)
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
        # SEO o la validación final. El descarte por sin voz (si
        # el clip no pasa las puertas) se reporta en el paso 8,
        # cuando se conocen viralidad y SEO.
        self._report_event(
            "clip_cortado",
            f"{self.streamer}: «{title}» cortado (40 s, viral auto "
            f"{viral_auto:.1f})"
            + (" — RETENIDO: sin título honesto"
               if needs_human_review else ""))
        # 7. Paquete SEO + prioridad (intocable). El paquete de
        # publicación ya se construyó en el paso 5c (después de la
        # transcripción) para que el título quemado sea el del
        # paquete verificado; aquí solo se reutiliza. El SEO
        # post-corte se mide sobre el paquete de PUBLICACIÓN (título
        # Gemini + descripción + tags + miniatura), NO sobre el
        # título de reglas sin tags: antes se puntuaba solo ese
        # título y el SEO colapsaba 92 -> 49, bloqueando publicar
        # TODOS los clips en vivo (2026-11-19, calibración). Lo
        # visible es el layout real del corte (no None): la
        # coherencia pasa de 80 a 100 cuando el juego está
        # verificado y se ve.
        # Si hubo revisión, la viralidad post-corte usa sus valores
        # REALES (human Gemini + audio medido), no la estimación.
        if reviewed is not None:
            viral = _score_clip(detail, multiple,
                                human=reviewed.get("human"),
                                audio=reviewed.get("audio"))
        else:
            viral = _score_clip(detail, multiple)
        viral_total = viral.get("total")
        # pub_title es el título quemado en el paso 6 (el del
        # paquete verificado cuando existe, o HELD_TITLE retenido):
        # vídeo, panel y plataformas usan el MISMO.
        pub_title = title
        if needs_human_review:
            # Paquete mínimo retenido (R3.4): package_clip_step NO
            # se llama con título vacío (levanta StepError); seo={}
            # -> statuses={} -> ningún reporte de descarte. El clip
            # retenido NUNCA se descarta: queda en la Cola
            # BLOQUEADO.
            pkg = write_held_package(final_clip, HELD_TITLE,
                                     dedupe_verified=dedupe_verified)
            package = finalize_package(pkg["package_path"], viral_total,
                                       multiple, cfg=self.cfg)
        else:
            pkg = package_clip_step(
                final_clip, pub_title,
                description=(pub_pkg.get("description")
                             or pub_pkg.get("description_first_2_lines")
                             or ""),
                tags=pub_pkg.get("tags") or [],
                hashtags=ensure_shorts_fifth(pub_pkg.get("hashtags")),
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
        # Puerta sin voz (regla 2026-11-19 18:41): sin voz + alguna
        # plataforma publicable -> el clip se publica igual SIN
        # subtítulos (subtitles_complete=0, honesto); sin voz y sin
        # ninguna plataforma publicable -> BLOQUEADO, como antes.
        _gates_ok = any(s[0] in ("publicable", "publicable_override")
                        for s in statuses.values())
        # R3.4: la retención por título también bloquea (el `blocked`
        # actual solo cubría sin-voz + puertas).
        blocked = bool((no_speech and not _gates_ok)
                       or needs_human_review)
        record = {
            "clip_id": clip_id, "streamer": self.streamer,
            "platform": self.platform, "vod_id": self.vod_id,
            "peak_ts": peak_ts, "start_sec": int(rec_start),
            "end_sec": int(rec_start + CLIP_DURATION_S),
            "duration_s": CLIP_DURATION_S,
            "plantilla": plantilla,
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
            "title": pub_title, "burned_title": burned_title,
            # 2026-11-19: el quemado y la publicación usan el MISMO
            # título (pub_title == burned_title por construcción,
            # salvo retención: pub_title es el marcador HELD_TITLE
            # y burned_title es "" — no se quema banner); el origen
            # del paquete queda trazado (gemini/rules/held) y el
            # texto de miniatura viaja al panel con el record.
            "pkg_source": pkg_source,
            "thumbnail_text": thumbnail_text,
            "clip": final_clip, "hype_s": hype_s, "sfx": sfx_info,
            "package_path": pkg["package_path"],
            "subtitles_complete": not no_speech,
            "publish_blocked": blocked,
            "no_speech": no_speech,
            # R3.4/R4.1: retención y dedupe viajan en el record
            # (viral.dedupe_verified visible en D1/panel; el
            # hold_reason lo consume _enqueue_panel).
            "needs_human_review": needs_human_review,
            "dedupe_verified": dedupe_verified,
            "viral": {"dedupe_verified": dedupe_verified,
                      **({"hold_reason": hold_reason}
                         if needs_human_review else {})},
            "seo": seo, "publish_status": {
                p: {"status": s[0], "reason": s[1]}
                for p, s in statuses.items()},
            "at": datetime.now(timezone.utc).isoformat(),
        }
        if blocked and not needs_human_review:
            # Sin voz y bajo las puertas: el descarte se reporta
            # aquí (antes que el de SEO del paso 9, que
            # _report_discard_once suprime por ser el mismo clip).
            # Un retenido NO se reporta como descartado: nunca se
            # descarta, y su RETENIDO ya quedó en el log y en el
            # evento de encolado.
            self._report_discard_once(
                clip_id,
                f"{self.streamer}: «{pub_title}» descartado — sin "
                f"voz verificable y bajo las puertas (viralidad "
                f"{viral_total}, SEO {_seo_summary(seo)}): queda en "
                f"Cola BLOQUEADO, sin subtítulos")
        # 8b. Validador final DEFINITIVO (2026-11-19, final_review.py):
        # puerta DURA de cara + plantilla sobre el MP4 final (la cara
        # se verifica en todo el clip y la receta Willyrex en píxeles)
        # + juez Gemini. La puerta dura NO la anula el modo
        # observación: si falla, UN re-render con la cara forzada
        # (facecam_box de la cadena en el segmento fuente) + SFX, y
        # se vuelve a validar. Si tras re-hacerlo sigue sin cara, el
        # clip queda para revisión humana con motivo visible y NO se
        # encola (nunca bloqueado en silencio). La config del gate se
        # carga en validate_final_clip: si falta, ConfigError y el
        # run falla en voz alta (no se captura aquí).
        # Rechazado (tras el re-render, si lo hubo) -> no se encola
        # nada. Pendiente (sin Gemini) -> solo Cola del panel, jobs
        # en draft.
        from .final_review import (
            transcript_text_for_srt, validate_final_clip)
        from .validate import ConfigError
        try:
            final_review = validate_final_clip(
                final_clip, burned_title,
                transcript_text=transcript_text_for_srt(sub.get("srt")),
                srt_path=sub.get("srt"),
                out_dir=os.path.join(work_dir, "final_review"),
                cfg=self.cfg, gemini_fn=self.final_review_fn,
                plantilla=plantilla,
                sfx_events=(sfx_info or {}).get("events"))
        except ConfigError:
            raise
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
        if final_review.get("needs_rerender"):
            final_review, final_clip, sub = self._rerender_after_gate(
                final_review, final_clip, sub, work_dir,
                video_for_cut, cut_start, burned_title, n, hype_s,
                plantilla=plantilla)
            record["clip"] = final_clip
            package["clip"] = final_clip
            record["rerendered"] = bool(
                final_review.get("rerendered_from"))
        record["final_review"] = final_review
        package["final_review"] = {
            "decision": final_review.get("decision"),
            "pasa": final_review.get("pasa"),
            "fallos": final_review.get("fallos") or [],
            "motivo": final_review.get("motivo") or "",
            "resumen": final_review.get("resumen") or "",
            "scores": final_review.get("scores") or {},
            "frames_n": final_review.get("frames_n") or 0,
            "hard_pasa": final_review.get("hard_pasa"),
            "needs_human_review": bool(
                final_review.get("needs_human_review")),
        }
        # 8c. Puerta de calidad B4 (2026-11-20): los 6 criterios
        # obligatorios sobre el veredicto anterior (formato 9:16,
        # webcam, hook, título, miniatura, audio). El veredicto
        # viaja en el record, en el paquete y al panel (viral.
        # quality_gate) para que el Worker no cree jobs de clips
        # sin validar o defectuosos. Sin veredicto no hay
        # publicable: el clip va en borrador (ver paso 9). El
        # contador diario alimenta la alerta del 30% (Telegram si
        # está configurado, si no stderr): nunca tumba el corte.
        from .quality_gate import build_quality_gate
        try:
            quality_gate = build_quality_gate(
                final_review,
                thumbnail_text=record.get("thumbnail_text"),
                no_speech=bool(no_speech),
                transcript=_transcript_dict_from_sub(sub))
        except Exception as exc:  # noqa: BLE001 - sin veredicto,
            # a revisión humana, nunca aprobado.
            quality_gate = {
                "decision": "pendiente_revision", "pasa": False,
                "criterios": {}, "fallos": [], "fallos_duros": [],
                "motivo": f"puerta de calidad sin ejecutar: {exc}"}
        record["quality_gate"] = quality_gate
        record["quality_blocked"] = quality_gate.get("pasa") is False
        package["quality_gate"] = quality_gate
        _beats = quality_gate.get("beats") or {}
        if _beats.get("status") == "ok":
            _issues = _beats.get("issues") or []
            print(f"BEATS gancho hablado {_beats.get('hook_end_s')}s "
                  f"({'ok' if not _issues else ', '.join(_issues)}): "
                  + "; ".join(_beats.get("notes") or []),
                  flush=True)
        try:
            _q_alert = self._quality_tracker.record(quality_gate)
        except Exception:  # noqa: BLE001 - la alerta no tumba nada
            _q_alert = None
        if _q_alert:
            try:
                from .notify import notify as _default_notify
                (self.notify_fn or _default_notify)(
                    "calidad_descartes", _q_alert)
            except Exception:  # noqa: BLE001 - la alerta no tumba
                pass
        with open(pkg["package_path"], "w", encoding="utf-8") as f:
            json.dump(package, f, ensure_ascii=False, indent=2)
        self._record(record)
        # 9. Panel web (Cola LIVE) + publicación automática.
        fr_decision = (final_review or {}).get("decision")
        if fr_decision == "rechazado" and not needs_human_review:
            human = bool(final_review.get("needs_human_review"))
            reason = (f"validación final: "
                      f"{final_review.get('motivo')}")
            self.skipped.append({
                "ts": peak_ts,
                "reason": reason,
                "viral_total": viral_total,
                "final_review": final_review.get("fallos"),
                "needs_human_review": human,
                "at": datetime.now(timezone.utc).isoformat()})
            self._report_discard_once(
                clip_id,
                f"{self.streamer}: «{pub_title}» "
                f"{'revisión humana — ' if human else 'descartado — '}"
                f"{reason} (viralidad {viral_total})")
            return record
        if fr_decision == "rechazado" and needs_human_review:
            # Retenido: el veredicto de vídeo viaja en el record,
            # pero el clip SÍ llega al panel BLOQUEADO — un
            # retenido nunca se descarta.
            print(f"RETENIDO {self.streamer} pico {peak_ts:.0f}s: "
                  f"validación final rechazado "
                  f"({final_review.get('motivo') or ''}); llega al "
                  f"panel BLOQUEADO", flush=True)
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
            # Puerta de calidad B4: sin veredicto que pase no hay
            # publicable — el clip va en borrador para revisión
            # humana (antes solo la pendiente de Gemini forzaba
            # draft; ahora también la desactivada y la observada
            # sin pasa). Un "rechazado" no llega aquí (retorno
            # temprano arriba) y _enqueue_publish lo descarta
            # igual por si se llama desde otro camino.
            self._enqueue_publish(
                record, panel_clip_id,
                force_draft=(quality_gate.get("pasa") is not True))
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
            pkg_data = {}
            try:
                with open(record["package_path"],
                          encoding="utf-8") as f:
                    pkg = json.load(f)
                pkg_data = pkg
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
            # 2026-11-19: el texto de miniatura viaja al panel
            # (columna thumb_text): sin él la miniatura queda
            # vacía en todos los clips. Solo si hay texto: el
            # panel rechaza thumbnail_text null con 400.
            if record.get("thumbnail_text"):
                payload["thumbnail_text"] = record["thumbnail_text"]
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
            # R3.4: el dict `viral` se construye SIEMPRE en este
            # punto (aunque el record no traiga todavía
            # quality_gate): con quality_gate si lo hay, con
            # hold_reason si el clip está retenido y con
            # dedupe_verified si se conoce. NUNCA se sobrescribe
            # quality_gate.decision. El PATCH SEO posterior se
            # salta solo cuando no hay seo (record.get("seo")
            # vacío, caso retenido).
            _viral = dict(record.get("viral") or {})
            if record.get("quality_gate"):
                _viral["quality_gate"] = record["quality_gate"]
            if record.get("needs_human_review"):
                _viral["hold_reason"] = (
                    (record.get("viral") or {}).get("hold_reason")
                    or HOLD_REASON)
            # Spec §8: la plantilla del render viaja al panel
            # dentro de viral (el Worker la guarda; sin ella la
            # píldora no ve nada). Solo si hay plantilla decidida:
            # un None no se manda (el merge del Worker conservaría
            # el valor anterior).
            if record.get("plantilla"):
                _viral["plantilla"] = record["plantilla"]
            if _viral:
                payload["viral"] = _viral
            clip = self.bridge.create_clip(payload)
            panel_id = clip.get("id")
            # SEO al panel por PATCH (2026-11-24, auto-seo): el POST
            # no lleva seo_scores en clips normales (la puerta SEO
            # del POST tiraría los de máximo < 80, que hoy entran
            # como borrador); el PATCH no tiene puerta. El rescate
            # ya lo mandó en el POST y no se duplica. Si el PATCH
            # falla, el clip ya está en la Cola: se registra y se
            # sigue (el loop de revalidación lo rellena después).
            if panel_id and not _is_rescue and record.get("seo"):
                try:
                    from .metadata import seo_inputs_from_package
                    _viral = {"seo_source": "corte"}
                    if pkg_data:
                        _viral["seo_inputs"] = seo_inputs_from_package(
                            pkg_data, self.lang)
                    self.bridge.update_clip(panel_id, {
                        "seo_scores": record["seo"],
                        "viral": _viral,
                    })
                except Exception as _seo_exc:  # noqa: BLE001 - el
                    # clip ya está en la Cola; el loop lo rellena.
                    self._report_event(
                        "seo_sin_enviar",
                        f"{self.streamer}: «{record['title']}» SEO "
                        f"sin enviar al panel ({_seo_exc})")
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
            # + upload_video con éxito, con viralidad y SEO. Un
            # retenido se anuncia como RETENIDO con su motivo: la
            # retención nunca es silenciosa.
            if panel_id:
                self._report_event(
                    "clip_encolado",
                    f"{self.streamer}: «{record['title']}» encolado "
                    f"(viralidad {record.get('viral_total')}; SEO "
                    f"{_seo_summary(record.get('seo'))})"
                    + (" — RETENIDO: " + (
                        (record.get("viral") or {}).get("hold_reason")
                        or HOLD_REASON)
                       if record.get("needs_human_review") else ""))
            for step, msg in (
                    ("detectado", f"LIVE {self.streamer}: pico a "
                     f"{record['peak_ts']:.0f}s"),
                    ("pico", f"hype {record.get('hype_score')} "
                     f"viralidad {record.get('viral_total')}"),
                    ("cortando", f"clip 40 s {record['start_sec']}-"
                     f"{record['end_sec']}"),
                    ("subtitulos", "subtítulos completos quemados"
                     if record["subtitles_complete"] else
                     ("sin voz verificable: se publica igual sin "
                      "subtítulos (pasa puertas)"
                      if not record["publish_blocked"] else
                      "sin voz verificable: BLOQUEADO (bajo puertas)")),
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
        eso, por defecto (auto_publish=False), el clip queda en la
        Cola como borrador para revisión humana. Con
        auto_publish=True y veredicto que pasa, se encola en el
        Worker con PanelBridge.publish_clip (publicación
        automática, MÁSTER PROMPT FINAL). El resto (bloqueado_seo /
        descartado) no se encola nunca.

        PUBLICADOR ÚNICO (P0 #8, #64, 2026-11-21): el cerebro ya
        NO crea jobs locales de publicar (los ejecutaba el publish
        loop --live por adapters directos y duplicaban con el
        Worker). El clip ya está en la Cola del panel
        (_enqueue_panel): la única salida es el Worker. Sin puente
        o sin clip en el panel no se publica nada (nunca por
        adapters directos): el clip queda en la Cola y el ciclo
        del Worker lo auto-encola cuando toque. Devuelve
        [(platform, worker_job_id, "api")] por plataforma encolada.
        """
        if record.get("publish_blocked"):
            return []
        from .quality_gate import publish_mode_for_quality
        # Puerta de calidad B4: un "rechazado" no encola nada (ni
        # en borrador: el clip defectuoso no es publicable y el
        # re-hecho lo trae de vuelta validado). El modo sale de
        # publish_mode_for_quality (única fuente: "api" solo con
        # veredicto que pasa y auto_publish encendido); el
        # force_draft del llamador solo puede bajar a borrador,
        # nunca subir a "api" (p. ej. validación pendiente).
        _qg = record.get("quality_gate") or {}
        if _qg.get("decision") == "rechazado":
            return []
        mode = publish_mode_for_quality(
            _qg or None, self.auto_publish)
        if force_draft:
            mode = "draft"
        if mode != "api":
            # Draft: el clip ya está en la Cola del panel; no se
            # crea ningún job local (el publish loop --live está
            # retirado) y no se llama a publish_clip.
            return []
        if self.bridge is None or panel_clip_id is None:
            # Sin puente o sin clip en el panel: no se publica
            # (nunca por adapters directos); el clip queda en la
            # Cola y el ciclo del Worker lo auto-encola.
            return []
        try:
            result = self.bridge.publish_clip(panel_clip_id)
        except Exception:  # noqa: BLE001 - el panel no tumba el corte
            # El clip ya está en la Cola: el ciclo del Worker lo
            # auto-encola en la próxima revisión.
            return []
        jobs = (result or {}).get("jobs") or []
        created = []
        for platform, st in (record.get("publish_status") or {}).items():
            if st.get("status") != "publicable":
                continue
            job = next((j for j in jobs
                        if j.get("platform") == platform), None)
            if job is not None:
                created.append((platform, job.get("id"), "api"))
        return created

    # -- modo en vivo ----------------------------------------------------
    def run_live(self, duration_s=1500, tick_s=15):
        """Sesión en vivo completa: verificar vivo + categoría,
        grabar, escuchar IRC, cortar al detectar picos."""
        from .streamers import is_allowed
        if not is_allowed(self.streamer, platform=self.platform):
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
