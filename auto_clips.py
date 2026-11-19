#!/usr/bin/env python3
"""
auto_clips.py — automatiza TODO el flujo de clips de GTA 6.

Un solo comando, desde el VOD hasta los clips listos para publicar:

    python auto_clips.py 1234567890
    python auto_clips.py https://www.twitch.tv/videos/1234567890 --lang es
    python auto_clips.py https://kick.com/locones/videos/01a1e069-8470-7626-8d5e-21ccb729c509

Qué hace solo, sin que tengas que hacer nada a mano:
  1. Descarga el chat y detecta los mejores momentos con clips_chat_v2.py
     (risas + hype, ES + EN).
     - Twitch: chat con TwitchDownloaderCLI y SOLO el segmento de ~80 s
       de cada pico (videodownload -b <inicio>s -e <fin>s: pre-roll +
       clip), nunca el VOD entero (un VOD de 3 h son ~10 GB; el
       segmento de 80 s, ~30 MB). Con --video local se corta del
       archivo entero como antes.
     - Kick: chat con el API sin documentar de web.kick.com
       (kick_downloader, vendorizado de KickDownloaderCLI) y SOLO el
       segmento de 60 s de cada pico (HLS master.m3u8 + ffmpeg),
       nunca el VOD entero.
  2. Corta los clips en vertical 1080x1920 con LAYOUT RESPONSIVE
     (layout.py, regla del proyecto): decide por fotogramas muestreados
     si son dos webcams lado a lado (se apilan), una webcam + gameplay
     (PiP arriba) o solo gameplay (recorte centrado); si el layout
     cambia dentro del clip, se adapta por tramos. La decisión queda
     en el manifiesto por clip. Los cortes son VARIABLES (ya no 60 s
     fijos): objetivo 30 s, rango 21-40 s, tope duro 60 s
     (config.yaml, clips.target_duration_s). Con --audio-check
     (bloque 6) mide el RMS por segundo en la ventana del candidato
     y marca confirmar/sospechar (chat alto + audio plano = sospechar
     spam); el recorte preciso por frase/silencio (precise_cut.py)
     va activado por defecto (config clips.precise_cut) y afina
     inicio/fin dentro del segmento ya descargado (2-3 s antes del
     pico, fin de frase +1-2 s, tope max_duration_s).
  3. Empaqueta cada clip para viral con viral_package.py, que combina
     el ranking por múltiplo sobre la mediana, la primera línea
     nueva, el texto en pantalla y el loop, el título + miniatura
     como una unidad (≤60/40 car.) y el SEO (descripción, tags ≤15,
     3 búsquedas). Genera el manifiesto (manifest.json / manifest.md
     / titles.txt) por clip. Cada clip lleva además su score de
     viralidad 0-100 (viral_score.py: 7 componentes, banda y filtro
     previo; distinto del score de chat, no se mezclan) y su score
     SEO por plataforma (seo_score.py: YouTube/TikTok/Instagram/
     Facebook, con estado ideal/publicable/override/bloqueado).
  4. Deja todo ordenado en runs/<vod-id>/ : chat.json, segments/ con un
     segmento por pico, clips/ y manifiesto. Ya no se guarda vod.mp4.

Si el chat o un segmento ya están descargados, se reutilizan (el chat
solo si es un JSON válido de ese VOD; con --force-chat se vuelve a
bajar aunque exista).

Títulos honestos: el manifiesto NO se encabeza con las plantillas de
suggest_titles() ("QUÉ JUGADA 😱 GTA 6" miente en un clip solo cams).
Después de cortar, se construye la ficha del momento (moment_card.py:
streamer, juego real del capítulo/categoría, reacción del chat) y el
paquete viral recibe ficha + juego + streamer + vod_id + layout (de
ahí sale `visible`: gameplay, cams+gameplay, cams-only o unknown).
El título del manifiesto es viral["best_title"] en su idioma; el otro
idioma usa la plantilla degradada con validate_legacy_title (en
cams-only, a reacción sin juego ni gameplay). La ficha y el motivo de
cada degradación quedan en manifest.json.
Con --dry-run solo descarga el chat y lista los picos, sin bajar el video ni cortar.
En Kick, si el chat viene vacío, --audio-fallback busca picos por audio
transcrito (muestreo con faster-whisper, ver kick_downloader/core.py).

Cuello de botella del chat de Twitch (diagnóstico, 2026-11-16):
el chat.json de un VOD de ~3 h pesa ~150 MB (162.474 mensajes en la
prueba 2930700035) y su descarga tarda ~18 min. El tiempo NO está en
el parseo local (json.load de 150 MB son segundos) ni en la detección
de picos, sino en la descarga con TwitchDownloaderCLI chatdownload:
la API de Twitch sirve los comentarios paginados y el CLI los pide
página a página, y con -E además baja las imágenes de emotes/badges.
No hay escaneo rápido de chat en Twitch como el de Kick
(kick_downloader.scan_chat_peaks): inventarlo sería reescribir el
protocolo de chat de Twitch. Mitigaciones que SÍ se aplican aquí:
reutilizar chat.json si ya existe y es válido (no re-descargar en
cada corrida) y --force-chat para forzar la descarga. Posibles
mejoras futuras sin probar: quitar -E si no se necesitan las
imágenes incrustadas, o subir -t/--threads (con riesgo de rate
limit por IP, según la ayuda del CLI).

Para probar sin Twitch/Kick, usa archivos locales:
    python auto_clips.py --chat test_chat.json --video test_vod.mp4 --out runs/prueba

Requisitos: Python 3.10+, FFmpeg en el PATH y TwitchDownloaderCLI
(solo para Twitch). Sin credenciales, sin red salvo la descarga.
"""

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys



from clipagent.ingest import (
    cut_vertical_clip,
    fmt_ts,
    get_video_duration,
    sample_messages,
    score_windows,
    select_top_peaks,
)
from clipagent.metadata import package_clip

try:
    # Score SEO por plataforma del paquete (seo_score.py).
    from clipagent.metadata import score_package_seo
    from clipagent.seo_score import score_all_platforms
    seo_mod = True
except ImportError:
    score_package_seo = None
    score_all_platforms = None
    seo_mod = False

try:
    # Títulos honestos (arreglos 3-4): validar/degradar títulos legacy,
    # derivar lo visible del layout y normalizar el nombre del juego.
    from clipagent.metadata import (
        KNOWN_GAMES,
        degrade_title_to_reaction,
        validate_legacy_title,
        visible_from_layout,
    )
    viral_checks_mod = True
except ImportError:
    KNOWN_GAMES = {}
    degrade_title_to_reaction = None
    validate_legacy_title = None
    visible_from_layout = None
    viral_checks_mod = False

try:
    # Kick vendorizado: paquete kick_downloader/ de KickDownloaderCLI
    # (https://github.com/exe2027/kickdownloaderCLI, proyecto aparte, MIT).
    # No depende del kick.py suelto: Twitch funciona sin este paquete.
    from kick_downloader import core as kick_mod
except ImportError:  # Kick opcional: Twitch funciona sin kick_downloader
    kick_mod = None

try:
    # Layout responsive (regla del proyecto, ver layout.py).
    from clipagent.render import cut_clip_with_layout, layout_summary
    layout_mod = True
except ImportError:
    cut_clip_with_layout = None
    layout_summary = None
    layout_mod = False

try:
    # Bloque 6: audio como segunda señal + recorte preciso por frase.
    from clipagent.audio import audio_verdict
    audio_mod = True
except ImportError:
    audio_verdict = None
    audio_mod = False

try:
    from clipagent.precise_cut import detect_silences, refine_cut
    precise_mod = True
except ImportError:
    detect_silences = None
    refine_cut = None
    precise_mod = False

try:
    # Ficha del momento (bloque 2 de la revisión interna): evidencia del clip
    # (chat, streamer, juego, reacción) para títulos honestos.
    from clipagent.moment_card import build_moment_card
    moment_mod = True
except ImportError:
    build_moment_card = None
    moment_mod = False

try:
    from clipagent.config import get_layout_config, load_config
except ImportError:
    get_layout_config = None
    load_config = None

try:
    # Checkpoint por job (Fase 5.5): reanuda desde la última etapa
    # completa (runs/<vod>/state.json). Sin él, no hay reanudación.
    from clipagent.checkpoint import (
        clip_done,
        load_manifest,
        load_state,
        mark_clip_done,
        mark_stage,
        resume_entry,
        save_state,
        stage_done,
    )
    checkpoint_mod = True
except ImportError:
    clip_done = None
    load_manifest = None
    load_state = None
    mark_clip_done = None
    mark_stage = None
    resume_entry = None
    save_state = None
    stage_done = None
    checkpoint_mod = False

try:
    # Pre-filtro rápido de Óscar Boix (2026-11-18): puerta barata
    # antes de Whisper/viralidad/SEO/render. Ver clipagent/prefilter.py.
    from clipagent.prefilter import (
        count_windows,
        find_prefilter_peaks,
        multiples_per_bucket,
        prefilter_gate,
    )
    prefilter_mod = True
except ImportError:
    count_windows = None
    find_prefilter_peaks = None
    multiples_per_bucket = None
    prefilter_gate = None
    prefilter_mod = False

try:
    # Score de viralidad 0-100 + señales MVP (7 componentes, banda,
    # filtro previo). Distinto del score de chat: no se mezclan.
    from clipagent.detect import (audio_weight_for, compute_signals,
                                  filter_passed, filter_passed_relaxed,
                                  score_clip)
    viral_score_mod = True
except ImportError:
    audio_weight_for = None
    compute_signals = None
    filter_passed = None
    filter_passed_relaxed = None
    score_clip = None
    viral_score_mod = False

HASHTAGS_ES = "#GTA6 #GTA6Clips #ElClipDeGTA6 #GTA #RockstarGames #gaming"
HASHTAGS_EN = "#GTA6 #GTA6Clips #GTA #RockstarGames #gaming #clips"


def parse_vod_id(value: str) -> str:
    """Acepta un ID ('1234567890') o una URL de Twitch."""
    m = re.search(r"videos/(\d+)", value)
    if m:
        return m.group(1)
    if re.fullmatch(r"\d+", value.strip()):
        return value.strip()
    raise ValueError(f"No reconozco el VOD: {value!r}. Usa el ID o la URL twitch.tv/videos/<id>")


def detect_platform(value: str) -> str:
    """'kick' si la URL es de kick.com, 'twitch' en el resto (ID o twitch.tv)."""
    return "kick" if "kick.com" in value else "twitch"


def run(cmd, what: str):
    print(f"$ {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"Error en {what}:\n{r.stderr.strip()[-2000:]}", file=sys.stderr)
        raise SystemExit(1)
    return r


def twitch_cli() -> str:
    """Ruta a TwitchDownloaderCLI.

    Precedencia: variable TWITCH_DL_CLI > config.yaml
    (tools.twitch_downloader_cli) > PATH. La ruta fija
    ``~/workspace/...`` de antes no existe en otros equipos y
    fallaba con un error poco claro: ahora, si no está en ningún
    lado, levanta RuntimeError diciendo dónde ponerla.
    """
    env_path = (os.environ.get("TWITCH_DL_CLI") or "").strip()
    if env_path:
        if os.path.isfile(env_path):
            return env_path
        raise RuntimeError(
            f"TWITCH_DL_CLI apunta a {env_path!r} pero ese archivo no "
            "existe. Corrige la variable o quítala.")
    cfg_path = ""
    if load_config:
        try:
            cfg_path = (((load_config() or {}).get("tools") or {})
                        .get("twitch_downloader_cli") or "").strip()
        except Exception:
            cfg_path = ""
    if cfg_path:
        if os.path.isfile(cfg_path):
            return cfg_path
        raise RuntimeError(
            "config.yaml tools.twitch_downloader_cli apunta a "
            f"{cfg_path!r} pero ese archivo no existe. Corrige la "
            "ruta en config.yaml.")
    found = shutil.which("TwitchDownloaderCLI")
    if found:
        return found
    raise RuntimeError(
        "No se encontró TwitchDownloaderCLI: no está en el PATH, ni "
        "en TWITCH_DL_CLI ni en config.yaml "
        "(tools.twitch_downloader_cli). Instálalo o pon su ruta en "
        "uno de esos dos sitios.")


def twitch_cli_available() -> bool:
    try:
        twitch_cli()
    except RuntimeError:
        return False
    return True


def inspect_chat_json(chat_path: str, vod_id: str = None):
    """Lee un chat.json y devuelve (válido, duración_s o None).

    Válido = JSON que parsea, con lista 'comments' y, si se pasa
    vod_id, video.id igual al pedido. Un archivo truncado (descarga
    cortada) o de otro VOD no es válido: hay que volver a bajarlo.
    La duración sale de video.length/end cuando viene (sirve para
    recortar el último clip sin tener el vídeo).
    """
    try:
        if os.path.getsize(chat_path) == 0:
            return False, None
        with open(chat_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False, None
    if not isinstance(data, dict) or not isinstance(data.get("comments"), list):
        return False, None
    duration = None
    vid = data.get("video") or {}
    if isinstance(vid, dict):
        if vod_id is not None and vid.get("id") not in (None, str(vod_id)):
            return False, None
        for key in ("length", "end"):
            val = vid.get(key)
            if isinstance(val, (int, float)) and val > 0:
                duration = float(val)
                break
    return True, duration


def download_chat(vod_id: str, chat_path: str, force: bool = False):
    """Baja el chat con TwitchDownloaderCLI, salvo que ya exista un
    chat.json válido de ese VOD (ver inspect_chat_json) y no se pase
    force=True (--force-chat).

    La descarga es el cuello de botella del flujo en Twitch (~18 min
    para 162.474 mensajes / 151 MB en la prueba 2930700035: la API de
    Twitch sirve los comentarios paginados y el CLI los pide página a
    página). Reutilizar el archivo evita pagar ese coste en cada
    corrida; es la única mejora segura: no existe un escaneo rápido
    de chat en Twitch como el de Kick.
    """
    if not force and os.path.isfile(chat_path):
        valid, _ = inspect_chat_json(chat_path, vod_id)
        if valid:
            print(f"Chat ya descargado, se reutiliza: {chat_path}")
            return
        print(f"Chat existente inválido o de otro VOD, se vuelve a bajar: {chat_path}")
    run([twitch_cli(), "chatdownload", "--id", vod_id,
         "-o", chat_path, "-E"], "descarga del chat")


def parse_peak_ts(value: str) -> float:
    """Momento pedido con --peak: segundos ("6050") o reloj
    ("1:40:50" / "40:50"). Devuelve segundos (float)."""
    text = (value or "").strip()
    if not text:
        raise ValueError("momento vacío")
    if ":" in text:
        parts = text.split(":")
        if len(parts) not in (2, 3) or not all(
                p.strip().isdigit() for p in parts):
            raise ValueError(
                f"{value!r}: usa segundos o reloj H:MM:SS / MM:SS")
        nums = [int(p) for p in parts]
        total = nums[-1] + 60 * nums[-2] + (3600 * nums[-3]
                                            if len(nums) == 3 else 0)
        return float(total)
    try:
        total = float(text)
    except ValueError:
        raise ValueError(f"{value!r}: usa segundos o reloj H:MM:SS / MM:SS")
    if total < 0:
        raise ValueError("el momento no puede ser negativo")
    return total


def chat_window_bounds(peak_sec: float, window_s: int):
    """Ventana de chat alrededor del momento: [pico-ventana,
    pico+ventana], con el inicio recortado a 0 (pico al principio
    del VOD)."""
    begin = max(0, int(peak_sec) - int(window_s))
    end = int(peak_sec) + int(window_s)
    return begin, end


def chat_window_path(out_dir: str, begin: int, end: int) -> str:
    """El archivo de chat de ventana lleva la ventana en el nombre
    (chat_w5930-6170.json): reutilizarlo solo vale para ese mismo
    momento, nunca para otro pico."""
    return os.path.join(out_dir, f"chat_w{begin}-{end}.json")


def download_chat_window(vod_id: str, chat_path: str, peak_sec: float,
                         window_s: int, force: bool = False):
    """Baja SOLO la ventana de chat alrededor del momento
    (chatdownload -b <inicio>s -e <fin>s), nunca el chat entero.

    Regla del usuario 2026-11-17 23:00: del chat, solo la ventana
    alrededor del momento del clip. Un VOD de 4h30 son ~160 MB de
    chat; una ventana de ±120 s son unos pocos MB. Reutiliza el
    archivo si ya existe y es válido para ese VOD (mismo criterio
    que download_chat)."""
    begin, end = chat_window_bounds(peak_sec, window_s)
    if not force and os.path.isfile(chat_path):
        valid, _ = inspect_chat_json(chat_path, vod_id)
        if valid:
            print(f"Chat de ventana ya descargado, se reutiliza: {chat_path}")
            return
        print("Chat de ventana inválido o de otro VOD, se vuelve a "
              f"bajar: {chat_path}")
    run([twitch_cli(), "chatdownload", "--id", str(vod_id),
         "-b", f"{begin}s", "-e", f"{end}s",
         "-o", chat_path, "-E"], "descarga del chat (ventana)")


def download_twitch_segment(vod_id: str, start_sec: float, end_sec: float,
                            out_path: str):
    """Baja SOLO [start_sec, end_sec] del VOD de Twitch con
    TwitchDownloaderCLI videodownload -b <inicio>s -e <fin>s, igual que
    Kick nunca baja el VOD entero: un VOD de 3 h son ~10 GB, el
    segmento de ~80 s (pre-roll + clip) son ~30 MB.

    Reutiliza el archivo si ya existe y no está vacío. Lanza
    RuntimeError si falla (el llamador salta ese pico y sigue con el
    siguiente). El trim es el Exact por defecto del CLI: el segmento
    sale con la duración pedida (80.002 s en la prueba 2930700035).
    """
    if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
        print(f"Segmento ya descargado, se reutiliza: {out_path}")
        return out_path
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    cmd = [twitch_cli(), "videodownload", "--id", str(vod_id),
           "-b", f"{int(start_sec)}s", "-e", f"{int(end_sec)}s",
           "-o", out_path]
    print(f"$ {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if (r.returncode != 0 or not os.path.isfile(out_path)
            or os.path.getsize(out_path) == 0):
        raise RuntimeError(
            f"No se pudo bajar el segmento [{int(start_sec)}s, "
            f"{int(end_sec)}s]: {r.stderr.strip()[-500:]}")
    return out_path


def filter_candidates(peaks, detail, rank_map, median_score):
    """Aplica el filtro previo (viral_score.filter_passed) a los picos.

    ``peaks``: [(peak_ts, score)] de select_top_peaks (--top-n ya es
    un máximo, no una cuota). Devuelve (seleccionados, descartados):
    seleccionados = [(peak_ts, score, multiple)] en orden de score
    (mayor primero, 6B.3);
    descartados = [{"peak", "score", "multiple", "rank", "motivos"}].
    La mediana tiene suelo 1.0: con mediana 0 un pico con puntos
    queda a score/1 x (si no, por división por cero ningún pico
    pasaría y un chat casi vacío no daría clips nunca).
    Salvaguarda anti-cero (como el best_fallback live): si NINGÚN
    pico pasa la puerta principal, se re-evalúan con la vía relajada
    (hype_ratio >= 1,2 + score >= 13); un VOD caliente nunca se
    queda en cero por la puerta barata — la viralidad/SEO posterior
    sigue mandando antes del render.
    """
    floor = max(median_score or 0.0, 1.0)
    selected = []
    discarded = []
    for peak_ts, score in peaks:
        multiple = round(score / floor, 2)
        rank = rank_map.get(peak_ts)
        pasa, motivos = filter_passed(detail[peak_ts], multiple, rank=rank,
                                      score=score)
        if pasa:
            selected.append((peak_ts, score, multiple))
        else:
            discarded.append({"peak": peak_ts, "score": score,
                              "multiple": multiple, "rank": rank,
                              "motivos": motivos})
    if not selected and discarded:
        rescued = []
        still = []
        for dc in discarded:
            pasa, motivos = filter_passed_relaxed(
                detail[dc["peak"]], dc["score"], rank=dc["rank"])
            if pasa:
                rescued.append((dc["peak"], dc["score"], dc["multiple"]))
            else:
                dc["motivos"] = dc["motivos"] + motivos
                still.append(dc)
        if rescued:
            print("  Anti-cero: ningún pico pasó la puerta principal; "
                  f"{len(rescued)} pico(s) pasan por la vía relajada.")
            return rescued, still
    return selected, discarded


def extract_window(video_path, start_sec, duration, out_path):
    """Ventana de análisis con inicio EXACTO (re-encode, no -c copy).

    Con ``-ss X -i ... -c copy`` el archivo empieza en el keyframe
    anterior a X (medido 2026-11-17: GOP de 5 s, ventana pedida a
    33 s, primer paquete con PTS -3.0): peak_rel, el hook y los
    subtítulos quedaban desfasados hasta un GOP entero. Con
    re-encode corto (ultrafast) el primer fotograma es exactamente
    start_sec (primer PTS 0.0). La ventana es solo para análisis
    (transcripción/audio/corte preciso), nunca se publica.
    """
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", str(start_sec),
         "-i", video_path, "-t", str(duration),
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
         "-c:a", "aac", out_path],
        check=True, capture_output=True, text=True)
    return out_path


def write_manifest_atomic(out_dir: str, manifest: dict) -> str:
    """Escribe manifest.json de forma atómica.

    Se vuelca a un temporal en la misma carpeta y se mueve con
    ``os.replace``: si el proceso muere a mitad de un job, el
    manifiesto en disco siempre es un JSON completo (el de los
    clips ya cortados), nunca un archivo a medias.
    """
    tmp_path = os.path.join(out_dir, ".manifest.json.tmp")
    final_path = os.path.join(out_dir, "manifest.json")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, final_path)
    return final_path


def suggest_titles(detail, lang: str, game: str = None):
    """Título sugerido según si el pico fue más de risas o de hype.

    Plantillas legacy: NUNCA van al manifiesto sin validar (ver
    honest_title). "QUÉ JUGADA 😱 GTA 6" miente en un clip solo cams
    (Just Chatting) porque promete gameplay que no se ve, y miente
    en cualquier clip que no sea GTA 6 porque el juego va
    hardcodeado. Por eso el juego se pasa como parámetro y solo se
    nombra si es el juego real del clip; sin juego conocido no se
    nombra ningún juego.
    """
    risas, hype = detail["risas"], detail["hype"]
    label = f" {game}" if game else ""
    if lang == "en":
        if hype >= risas:
            return f"WHAT A PLAY 😱{label}", f"Insane moment in{label} — full clip" if game else "Insane moment — full clip"
        return f"They couldn't believe it 😂{label}", f"Chat lost it in{label} — best moment" if game else "Chat lost it — best moment"
    # es / both: título en español, que es el canal principal
    if hype >= risas:
        return f"QUÉ JUGADA 😱{label}", f"El mejor momento del directo de{label}" if game else "El mejor momento del directo"
    return f"No se lo creyeron 😂{label}", f"El chat se murió de risa en{label}" if game else "El chat se murió de risa"


# Alias mínimos por si viral_package no expone KNOWN_GAMES.
_KNOWN_GAMES_FALLBACK = {
    "gta 6": "GTA 6", "gta vi": "GTA 6",
    "gta v": "GTA V", "gta 5": "GTA V", "gta online": "GTA V",
}


def normalize_game_name(name):
    """Nombre de juego del metadato (capítulo Twitch / categoría Kick)
    -> canónico de viral_package (claves de GAME_ALIASES).

    El check promesa-payoff compara con GAME_ALIASES por nombre
    exacto: 'Grand Theft Auto V' sin normalizar dejaría fuera todos
    los alias ('gta v', 'gta 5') y cualquier título que nombrara el
    juego fallaría el check. Sin coincidencia se devuelve el nombre
    tal cual (el check lo trata con cautela, pero game_label lo
    muestra).
    """
    if not name:
        return None
    low = name.strip().lower()
    aliases = KNOWN_GAMES or _KNOWN_GAMES_FALLBACK
    if low in aliases:
        return aliases[low]
    if low.startswith("grand theft auto "):
        short = "gta " + low[len("grand theft auto "):]
        if short in aliases:
            return aliases[short]
    return name.strip()


def chat_stream_info(chat_path: str, peak_ts: float):
    """(streamer, game, game_source) desde el chat.json de Twitch.

    streamer: chat['streamer']['name'] (o login). game: el juego del
    capítulo donde cae el pico (video.chapters: gameDisplayName),
    normalizado a canónico (ver normalize_game_name), o video.game si
    no hay capítulos. 'Just Chatting' NO es un juego: en ese caso
    game=None (no se nombra ningún juego) y game_source lo dice.
    Sin metadatos: (None, None, '').
    """
    try:
        with open(chat_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None, None, ""
    streamer = None
    st = data.get("streamer") or {}
    if isinstance(st, dict):
        streamer = st.get("name") or st.get("login")
    game = None
    game_source = ""
    vid = data.get("video") or {}
    if isinstance(vid, dict):
        # Capítulo donde cae el pico (clipagent.category: Helix no
        # expone capítulos; el chat.json trae video.chapters con
        # gameDisplayName, el mismo dato del reproductor de Twitch).
        from clipagent.category import category_at
        cat = category_at(vid.get("chapters") or [], peak_ts)
        if cat:
            game = cat
            game_source = f"capítulo de Twitch: {game}"
        if game is None:
            game = vid.get("game")
            if game:
                game_source = "video.game del chat.json"
    if isinstance(game, str) and game.strip().lower() == "just chatting":
        return streamer, None, "capítulo Just Chatting (sin juego)"
    return streamer, normalize_game_name(game), game_source


def honest_title(title: str, moment_card, layout_decision,
                 payoff_ts_clip: float, lang: str):
    """Título legacy pasado por el check promesa-payoff.

    Devuelve (título, degradado, motivo): si pasa, el mismo; si
    miente en un clip solo cams, el degradado a reacción (sin juego
    ni gameplay); si falla por otra cosa y no hay degradación, NUNCA
    se devuelve el título mentiroso tal cual: se usa un título
    seguro (reacción, o el juego real si se conoce) y se marca
    revisar a mano. Sin viral_package no se puede validar: se
    devuelve tal cual con aviso.
    """
    if not viral_checks_mod:
        return title, False, "sin viral_package para validar (se usa tal cual)"
    visible = None
    if visible_from_layout is not None and layout_decision is not None:
        visible = visible_from_layout(
            layout_decision, payoff_ts_clip=payoff_ts_clip).get("visible")
    ok, reason, degraded = validate_legacy_title(
        title, moment_card=moment_card, visible=visible,
        layout=layout_decision, lang=lang)
    if ok:
        return title, False, reason
    if degraded:
        return degraded, True, reason
    # Sin degradación posible (p. ej. gameplay visible pero juego
    # equivocado: "GTA 6" en un clip de GTA V): no se publica el
    # título mentiroso. Título seguro con el juego real, o de
    # reacción sin juego si no se conoce.
    safe = None
    if degrade_title_to_reaction is not None:
        try:
            safe = degrade_title_to_reaction(moment_card, lang=lang)
        except Exception:
            safe = None
    if not safe:
        game = (moment_card or {}).get("game")
        if lang == "en":
            safe = f"{game}: chat went wild" if game else "Chat went wild"
        else:
            safe = f"{game}: el chat explotó" if game else "El chat explotó"
    return safe, True, reason + " (título mentiroso reemplazado por uno seguro: revisar a mano)"


def main() -> int:
    p = argparse.ArgumentParser(description="Automatiza todo: descarga, detecta, corta y titula los clips de un VOD de Twitch o de Kick.")
    p.add_argument("vod", nargs="?", help="ID de Twitch, URL twitch.tv/videos/<id> o URL kick.com/<canal>/videos/<uuid>")
    p.add_argument("--chat", help="Usar un chat.json local en vez de descargarlo")
    p.add_argument("--video", help="Usar un vod.mp4 local en vez de descargarlo (solo Twitch)")
    p.add_argument("--out", help="Carpeta de salida (default: runs/<vod-id>)")
    p.add_argument("--top-n", type=int, default=5,
                   help="MÁXIMO de clips por VOD (no una cuota: solo se "
                   "cortan los picos que pasan el filtro previo)")
    p.add_argument("--window", type=int, default=10)
    p.add_argument("--min-separation", type=int, default=60)
    p.add_argument("--peak", default=None,
                   help="Momento explícito en segundos o reloj (\"6050\" o "
                        "\"1:40:50\"): baja SOLO el chat de la ventana "
                        "alrededor de ese momento (regla 2026-11-17 23:00) "
                        "y corta ese clip, sin detectar picos ni aplicar "
                        "el filtro previo. Solo Twitch.")
    p.add_argument("--chat-window", type=int, default=None,
                   help="Segundos de chat a cada lado del --peak (default: "
                        "config clips.chat_window_s, 120)")
    p.add_argument("--pre-roll", type=int, default=20)
    p.add_argument("--duration", type=int, default=None,
                   help="Duración forzada del clip en segundos (default: "
                        "config clips.target_duration_s, 40 s; el corte "
                        "variable usa target/min/max de config.yaml: "
                        "mínimo 36 s, tope duro 60 s)")
    p.add_argument("--human-scores", help="JSON con la entrada humana del "
                   "score de viralidad por clip: {\"1\": {\"hook\": 8, "
                   "\"claridad\": 9, ...}, \"all\": {...}}; sin él, los "
                   "componentes humanos quedan estimados y el total es BETA")
    p.add_argument("--lang", choices=["es", "en", "both"], default="both")
    p.add_argument("--crop", choices=["center", "left", "right"], default="center")
    p.add_argument("--layout", choices=["auto", "stack", "pip", "center"], default=None,
                   help="Layout del clip: auto (decide por fotogramas, default), o forzar stack/pip/center")
    p.add_argument("--crf", type=int, default=23)
    p.add_argument("--preset", default="fast")
    p.add_argument("--dry-run", action="store_true", help="Solo descarga el chat y lista picos, sin video ni cortes")
    p.add_argument("--no-viral", action="store_true", help="No genera el empaquetado viral (títulos, miniatura, SEO)")
    p.add_argument("--no-prefilter", dest="prefilter", action="store_false",
                   help="Apaga el pre-filtro rápido de Óscar Boix (por "
                        "defecto, los picos que no llegan a 3x la mediana "
                        "en puntos 1/2 no llegan a Whisper/viralidad/SEO/"
                        "render; en chat caliente sin ningún tramo 3x se "
                        "desactiva solo)")
    p.add_argument("--llm-select", dest="llm_select", action="store_true",
                   help="Selección de momentos con LLM tras el corte (7.1): una llamada con todos los candidatos; sin LLM o si falla, se sigue con el chat. El worker la pasa siempre.")
    p.add_argument("--no-llm-select", dest="llm_select", action="store_false",
                   help="No usar el LLM para seleccionar momentos.")
    p.set_defaults(llm_select=False)
    p.add_argument("--delete-vod", action="store_true", help="Sin efecto: ya no se baja el VOD entero (se guardan solo segmentos por pico). Se mantiene el flag para no romper llamadas viejas")
    p.add_argument("--force-chat", action="store_true", help="Forzar la descarga del chat aunque ya exista un chat.json válido (por defecto se reutiliza: la descarga tarda ~18 min en un VOD de 3 h)")
    p.add_argument("--fresh", action="store_true", help="Ignorar el checkpoint (runs/<vod>/state.json) y rehacer todo desde cero")
    p.add_argument("--audio-fallback", action="store_true", help="En Kick, si el chat viene vacío, buscar picos por audio transcrito (muestreo)")
    p.add_argument("--audio-check", action="store_true",
                   help="Bloque 6: RMS por segundo en la ventana del candidato y veredicto "
                        "confirmar/sospechar (chat alto + audio plano = sospechar spam); "
                        "se corta igual, solo se marca en el manifiesto")
    p.add_argument("--no-heatmap", dest="heatmap", action="store_false",
                   default=None,
                   help="No leer el heatmap de YouTube (default: lo que diga "
                        "config.yaml signals.heatmap.enabled, true)")
    p.add_argument("--no-motion", dest="motion", action="store_false",
                   default=None,
                   help="No medir el % de juego en movimiento (default: "
                        "config.yaml signals.motion.enabled, true)")
    p.add_argument("--no-game-voice", dest="game_voice", action="store_false",
                   default=None,
                   help="No detectar voz del juego (default: config.yaml "
                        "signals.game_voice.enabled, true)")
    p.add_argument("--precise-cut", dest="precise_cut", action="store_true",
                   default=None,
                   help="Afinar inicio/fin por frase/silencio dentro del "
                        "segmento ya descargado (default: lo que diga "
                        "config.yaml clips.precise_cut, true)")
    p.add_argument("--no-precise-cut", dest="precise_cut", action="store_false",
                   help="Apaga el recorte preciso: corte fijo de target_duration_s")
    p.add_argument("--workers", type=int, default=12, help="Workers en paralelo para el chat de Kick (default: 12)")
    p.add_argument("--full-chat", action="store_true", help="En Kick, bajar el chat COMPLETO (lento: ~50 min en un VOD de 10h; por defecto escaneo rápido)")
    p.add_argument("--reel-patterns", default=None,
                   help="Informe JSON de reel_study.py (reel_patterns.json). "
                        "Si no se pasa, se usa reel_patterns.json junto a "
                        "auto_clips.py si existe; si no existe, se sigue "
                        "sin estudio de reels. Solo añade sugerencias "
                        "validadas al paquete, nunca cambia best_title.")
    args = p.parse_args()

    if not args.vod and not args.chat:
        print("Error: pasa el VOD (ID o URL de Twitch o Kick) o --chat <chat.json>.", file=sys.stderr)
        return 1
    if not args.dry_run and not args.video and not args.vod:
        print("Error: para cortar hace falta el VOD: pasa el ID/URL o --video.", file=sys.stderr)
        return 1

    # Cortes variables (config.yaml, sección clips): objetivo 40 s,
    # mínimo 40 s, tope duro 60 s (reglas del usuario 2026-11-18: el
    # clip de 21 s era "muy poco"; prohibido 25-35 s, regla de oro).
    # --duration fuerza la duración; si no, manda target_duration_s.
    # El segmento descargado cubre pre-roll + max(target,
    # max_duration_s) para que el corte preciso tenga margen.
    cfg = load_config() if load_config else {}
    # Ajustes del panel (2026-11-18): si el Worker está configurado
    # por entorno (PANEL_WORKER_URL + PANEL_BRIDGE_TOKEN), los
    # Ajustes de la pestaña Ajustes mandan sobre config.yaml. Sin
    # entorno, comportamiento de siempre.
    if os.environ.get("PANEL_WORKER_URL") and os.environ.get("PANEL_BRIDGE_TOKEN"):
        try:
            from clipagent.panel_settings import load_config_with_panel
            from clipagent.tenant import TenantError
            cfg = load_config_with_panel()
        except TenantError:
            raise
        except Exception:
            pass
    clip_cfg = (cfg or {}).get("clips") or {}
    target_dur = args.duration or int(clip_cfg.get("target_duration_s") or 40)
    max_dur = args.duration or int(clip_cfg.get("max_duration_s") or 60)
    min_dur = 1 if args.duration else int(clip_cfg.get("min_duration_s") or 40)
    use_precise = (args.precise_cut if args.precise_cut is not None
                   else bool(clip_cfg.get("precise_cut", True)))
    seg_len = args.pre_roll + max(target_dur, max_dur)
    human_scores = {}
    # Señales de Uri (2026-11-17): heatmap, movimiento y voz del
    # juego. Solo diagnóstico en el manifiesto: no tocan viralidad,
    # prioridad, filtros duros ni ritmo. La CLI apaga cada una; si
    # no, manda config.yaml signals.*.enabled.
    _sig_cfg = (cfg or {}).get("signals") or {}
    use_heatmap = (args.heatmap if args.heatmap is not None
                   else bool(_sig_cfg.get("heatmap", {}).get("enabled", True)))
    use_motion = (args.motion if args.motion is not None
                  else bool(_sig_cfg.get("motion", {}).get("enabled", True)))
    use_game_voice = (args.game_voice if args.game_voice is not None
                      else bool(_sig_cfg.get("game_voice", {}).get("enabled", True)))
    heatmap_cfg = _sig_cfg.get("heatmap", {}) or {}
    motion_cfg = _sig_cfg.get("motion", {}) or {}
    game_voice_cfg = _sig_cfg.get("game_voice", {}) or {}
    if args.human_scores:
        with open(args.human_scores, encoding="utf-8") as f:
            human_scores = json.load(f)

    # Estudio de reels (reel_study.py): opcional, solo añade
    # sugerencias validadas al paquete viral. Sin informe no se
    # inventa nada: se sigue sin estudio de reels.
    reel_patterns = None
    reel_patterns_path = args.reel_patterns or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "reel_patterns.json")
    if reel_patterns_path and os.path.isfile(reel_patterns_path):
        try:
            with open(reel_patterns_path, encoding="utf-8") as f:
                reel_patterns = json.load(f)
            print(f"Estudio de reels: {reel_patterns.get('sample_n', '?')} "
                  f"reels en {reel_patterns_path}")
        except Exception as e:
            print(f"Estudio de reels: no se pudo leer "
                  f"{reel_patterns_path} ({e}); se sigue sin él.",
                  file=sys.stderr)
            reel_patterns = None

    platform = detect_platform(args.vod) if args.vod else "twitch"
    kick_meta = None
    vod_id = None
    if args.vod:
        try:
            if platform == "kick":
                if kick_mod is None:
                    print("Error: falta kick_downloader para URLs de Kick.", file=sys.stderr)
                    return 1
                slug, uuid = kick_mod.parse_kick_url(args.vod)
                vod_id = f"kick-{slug}-{uuid[:8]}"
            else:
                vod_id = parse_vod_id(args.vod)
        except (ValueError, kick_mod.KickError if kick_mod else ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    out_dir = args.out or (f"runs/{vod_id}" if vod_id else "runs/local")
    clips_dir = os.path.join(out_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)

    # Checkpoint (Fase 5.5): reanuda desde la última etapa completa.
    # --fresh ignora el estado anterior y rehace todo.
    state = load_state(out_dir) if checkpoint_mod and not args.fresh else {}
    prev_manifest = (load_manifest(out_dir)
                     if checkpoint_mod and not args.fresh else None)
    if args.fresh:
        print("Checkpoint: --fresh, se rehace todo desde cero.")
    elif state.get("clips"):
        print(f"Checkpoint: {len(state['clips'])} clip(s) ya hechos "
              f"en {out_dir} (se reutilizan si el archivo existe).")

    chat_path = args.chat or os.path.join(out_dir, "chat.json")

    # REGLA DEL USUARIO 2026-11-17 23:00: del chat, solo la ventana
    # alrededor del momento del clip, nunca el chat entero. --peak
    # fija el momento: el chat se baja solo en [pico-ventana,
    # pico+ventana] (config clips.chat_window_s, default 120 s por
    # lado) y ese pico ES el clip, sin detección ni filtro previo
    # (el usuario eligió el momento). Sin --peak, descubrir picos
    # desde cero sigue necesitando el chat completo.
    peak_mode = args.peak is not None
    chat_scope = {"scope": "full", "file": chat_path}
    peak_sec = None
    window_s = None
    if peak_mode:
        if platform == "kick":
            print("Error: --peak solo funciona en Twitch (Kick usa su "
                  "escaneo).", file=sys.stderr)
            return 1
        if args.video:
            print("Error: --peak no se combina con --video (el vídeo "
                  "local no trae chat).", file=sys.stderr)
            return 1
        try:
            peak_sec = parse_peak_ts(args.peak)
        except ValueError as e:
            print(f"Error: --peak inválido: {e}", file=sys.stderr)
            return 1
        window_s = (args.chat_window if args.chat_window is not None
                    else int(clip_cfg.get("chat_window_s") or 120))
        win_begin, win_end = chat_window_bounds(peak_sec, window_s)
        if args.chat:
            chat_scope = {"scope": "window", "peak": peak_sec,
                          "window_s": window_s,
                          "window": [win_begin, win_end],
                          "file": chat_path,
                          "note": "chat local ya descargado; la ventana "
                                  "solo delimita el análisis"}
        else:
            chat_path = chat_window_path(out_dir, win_begin, win_end)
            chat_scope = {"scope": "window", "peak": peak_sec,
                          "window_s": window_s,
                          "window": [win_begin, win_end],
                          "file": chat_path}
        print(f"Modo --peak: momento {fmt_ts(int(peak_sec))} "
              f"({peak_sec:.0f}s), chat solo en ventana "
              f"[{win_begin}s, {win_end}s] (±{window_s}s)")
    # Sin --video ya no hay vod.mp4: Twitch corta de un segmento por
    # pico (ver el bucle). video_path solo vive con --video local.
    video_path = args.video

    # TwitchDownloaderCLI solo hace falta para Twitch. Kick usa su API + ffmpeg.
    needs_download = bool(vod_id and (not args.chat or (not args.video and not args.dry_run)))
    if needs_download and platform == "twitch" and not twitch_cli_available():
        print("Error: TwitchDownloaderCLI no está en el PATH, ni en "
              "TWITCH_DL_CLI ni en config.yaml "
              "(tools.twitch_downloader_cli).", file=sys.stderr)
        return 1
    if shutil.which("ffmpeg") is None and not args.dry_run:
        print("Error: FFmpeg no está en el PATH.", file=sys.stderr)
        return 1

    # 1. Descargar el chat (o reutilizar). Si el checkpoint dice que
    # el chat ya está en runs/<vod>/chat.json, no se vuelve a bajar
    # (la descarga tarda ~18 min en un VOD de 3 h).
    reuse_chat = (checkpoint_mod and not args.chat and vod_id
                  and not args.force_chat and stage_done(state, "chat")
                  and os.path.isfile(chat_path))
    if reuse_chat:
        print("Chat: reutilizado del checkpoint (chat.json ya descargado).")
    elif args.chat:
        chat_path = args.chat
    elif vod_id:
        if platform == "kick":
            slug, uuid = kick_mod.parse_kick_url(args.vod)
            kick_meta = kick_mod.resolve_vod(slug, uuid)
            print(f"Kick: {kick_meta['channel']} — {kick_meta['title'][:80]}")
            print(f"  VOD: {kick_meta['duration_s'] / 3600:.2f}h | "
                  f"{kick_meta['views']} views | {kick_meta['category']} | "
                  f"{kick_meta['language']}")
            if args.full_chat:
                kick_mod.download_chat(
                    kick_meta, chat_path,
                    out_raw_ndjson=os.path.join(out_dir, "chat_kick_raw.ndjson"),
                    workers=args.workers)
            else:
                # Por defecto escaneo rápido: el chat completo de un VOD
                # largo tarda ~50 min (el usuario lo canceló por eso).
                kick_mod.scan_chat_peaks(
                    kick_meta, chat_path,
                    out_raw_ndjson=os.path.join(out_dir, "chat_kick_raw.ndjson"),
                    workers=args.workers)
        else:
            if peak_mode:
                download_chat_window(vod_id, chat_path, peak_sec,
                                     window_s, force=args.force_chat)
            else:
                download_chat(vod_id, chat_path, force=args.force_chat)
    if not os.path.isfile(chat_path):
        print(f"Error: no existe el chat: {chat_path}", file=sys.stderr)
        return 1
    if checkpoint_mod and not args.chat:
        # El chat vive en runs/<vod>/chat.json: la etapa queda marcada
        # (con --chat externo no, ese archivo puede desaparecer).
        mark_stage(state, "chat")
        save_state(out_dir, state)
    # Con --chat local en Kick igual hacen falta los metadatos (source HLS,
    # duración) para bajar los segmentos.
    if platform == "kick" and kick_meta is None and args.vod:
        slug, uuid = kick_mod.parse_kick_url(args.vod)
        kick_meta = kick_mod.resolve_vod(slug, uuid)

    # 2. Detectar picos (por chat; en Kick, fallback por audio si viene vacío)
    scores, detail = score_windows(chat_path, window_size=args.window, lang=args.lang)
    total_msgs = sum(d["msgs"] for d in detail.values())
    audio_peaks = []
    if peak_mode:
        # El momento es explícito: el pico pedido es el clip, sin
        # buscar otros. La rejilla de ventanas es de window/2 (5 s):
        # el pico se ajusta a la ventana que lo contiene. Si la
        # ventana no trae mensajes, se corta igual con score 0.
        step = max(1, args.window // 2)
        peak_ts = int(peak_sec // step) * step
        if peak_ts in scores and detail.get(peak_ts):
            peaks = [(peak_ts, scores[peak_ts])]
            d = detail[peak_ts]
            print(f"Pico pedido: {fmt_ts(peak_ts)} — {scores[peak_ts]} pts "
                  f"({d['msgs']} msgs, {d['risas']} risas, {d['hype']} hype); "
                  f"ventana de chat: {total_msgs} mensajes en total")
        else:
            peaks = [(peak_ts, 0)]
            detail[peak_ts] = {"msgs": 0, "risas": 0, "hype": 0, "users": 0}
            scores[peak_ts] = 0
            print(f"Aviso: sin mensajes de chat en {fmt_ts(peak_ts)} dentro "
                  "de la ventana; se corta igual (momento explícito, "
                  "score 0).", file=sys.stderr)
    elif not scores or total_msgs == 0:
        print("Chat vacío: no hay mensajes con los que detectar picos.")
        if platform == "kick" and args.audio_fallback and kick_meta:
            print("Fallback por audio transcrito (muestreo, ver kick_downloader/core.py)...")
            audio_peaks = kick_mod.sample_audio_peaks(
                kick_meta["source"], kick_meta["duration_s"], out_dir)
            peaks = [(ts, pts) for ts, pts, _ in audio_peaks[:args.top_n]]
            detail = {ts: {"msgs": 0, "risas": 0, "hype": 0} for ts, _ in peaks}
            scores = dict(peaks)
            print(f"Audio: {len(peaks)} picos por transcripción (muestreo)")
            for i, (ts, score) in enumerate(peaks, 1):
                print(f"  {i}. {fmt_ts(ts)} — {score} pts (audio, sin chat)")
        else:
            return 0
    else:
        peaks = select_top_peaks(scores, top_n=args.top_n, min_separation=args.min_separation)
        print(f"Chat: {total_msgs} mensajes — {len(peaks)} picos")
        for i, (ts, score) in enumerate(peaks, 1):
            d = detail[ts]
            print(f"  {i}. {fmt_ts(ts)} — {score} pts ({d['msgs']} msgs, {d['risas']} risas, {d['hype']} hype)")

    if args.dry_run:
        # Score de viralidad en seco (solo componentes automáticos:
        # chat + audio pendiente; los humanos quedan estimados y el
        # total es BETA). No descarga vídeo ni corta nada.
        if viral_score_mod and scores:
            if peak_mode:
                # La mediana es local a la ventana: las ventanas vacías
                # de antes del momento no cuentan (si no, la mediana
                # es 0 y el múltiplo queda en None y revienta el
                # formato del filtro).
                in_window = [s for ts, s in scores.items()
                             if win_begin <= ts <= win_end]
                median = statistics.median(in_window) if in_window else 0
            else:
                median = statistics.median(scores.values())
            ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
            rank_map = {ts: i + 1 for i, (ts, _s) in enumerate(ranked)}
            print("Score de viralidad (BETA, sin entrada humana):")
            for ts, score in peaks:
                # Con mediana <= 0, el múltiplo es el propio score
                # (mismo criterio que viral_multiple en ese caso).
                multiple = score / median if median > 0 else score
                vs = score_clip(detail[ts], multiple, rank=rank_map.get(ts),
                                human=(human_scores.get("all")
                                       or human_scores.get(str(ts))),
                                score=score)
                print(f"  {fmt_ts(ts)} — viral {vs['total']}/100 "
                      f"({vs['band']}) | chat {vs['components']['chat']['value']}/10 "
                      f"({multiple:.1f}x) | filtro: "
                      f"{'pasa' if vs['filter_passed'] else 'no pasa'}")
        print("Dry-run: no se descargó el video ni se cortó nada.")
        return 0

    if platform == "twitch":
        if args.video:
            # VOD local: se corta del archivo entero como antes.
            video_path = args.video
            if not os.path.isfile(video_path):
                print(f"Error: no existe el video: {video_path}", file=sys.stderr)
                return 1
            duration = get_video_duration(video_path)
        else:
            # Sin --video ya NO se baja el VOD entero: cada pico baja
            # solo su segmento de ~80 s (pre-roll + clip) en el bucle,
            # como Kick. La duración, si hace falta para recortar el
            # último clip, sale del chat.json (video.length); si no
            # viene, no se recorta y manda el fin del segmento.
            _, duration = inspect_chat_json(chat_path, vod_id)
            segments_dir = os.path.join(out_dir, "segments")
            os.makedirs(segments_dir, exist_ok=True)
    else:
        # Kick: no se baja el VOD entero; cada pico baja solo su segmento.
        duration = kick_meta["duration_s"] if kick_meta else None
        segments_dir = os.path.join(out_dir, "segments")
        os.makedirs(segments_dir, exist_ok=True)
    if duration:
        print(f"VOD: {fmt_ts(int(duration))} ({duration:.0f}s)")

    # Mediana de los picos: la ficha del momento y el paquete viral la
    # usan para el múltiplo viral (x veces la mediana). Con --peak la
    # mediana es local a la ventana de chat (las ventanas vacías de
    # antes del momento no cuentan).
    if peak_mode:
        in_window = [s for ts, s in scores.items()
                     if win_begin <= ts <= win_end]
        median_score = statistics.median(in_window) if in_window else None
        ranked = sorted(((ts, s) for ts, s in scores.items()
                         if win_begin <= ts <= win_end),
                        key=lambda kv: kv[1], reverse=True)
    else:
        median_score = statistics.median(scores.values()) if scores else None
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    # Puesto de cada pico en el VOD (1 = el mejor): lo usa el filtro
    # previo del score de viralidad (solo top-5).
    rank_map = {ts: i + 1 for i, (ts, _s) in enumerate(ranked)}

    # 2b. Pre-filtro rápido de Óscar Boix (2026-11-18): puerta barata
    # ANTES de Whisper/viralidad/SEO/render. Cuenta 1 punto por
    # mensaje y 2 por reacción en tramos de 10 s; solo los picos con
    # un tramo >= 3x la mediana de sus vecinos (±5 min) llegan al
    # filtro previo y al corte. No se aplica con --peak (momento
    # explícito) ni a los picos por audio de Kick (sin chat), y nunca
    # tumba el job: si no se puede aplicar, se sigue sin él. Si no
    # hay NINGÚN tramo 3x en el VOD (chat caliente), el pre-filtro
    # se desactiva solo y deja pasar todos los picos (ver
    # clipagent/prefilter.py).
    prefilter_summary = {"active": False, "prefilter_peaks": [],
                         "passed": len(peaks), "discarded": 0,
                         "reason": "no aplicado"}
    prefilter_discarded = []
    if (not peak_mode and not audio_peaks and args.prefilter
            and prefilter_mod):
        try:
            from clipagent.ingest import _load_chat
            _pf_cfg = (cfg or {}).get("scoring") or {}
            _pf_min = float(_pf_cfg.get("prefilter_min_multiple", 3.0))
            _pf_msgs = [(off, body)
                        for off, body, _u in _load_chat(chat_path)]
            _pf_points = count_windows(_pf_msgs)
            # La separación del pre-filtro nunca supera la del
            # pipeline (--min-separation): select_top_peaks ya
            # dedupó con esa separación y el pre-filtro no puede
            # tumbar un pico que el pipeline mantuvo (p. ej. picos
            # reales a 50 s con --min-separation 20 y separación
            # de Óscar de 90 s: el de 90 s se perdía).
            _pf_sep = min(args.min_separation,
                          int(_pf_cfg.get("prefilter_separation_s", 90)))
            _pf_peaks = find_prefilter_peaks(
                _pf_points, min_multiple=_pf_min, separation=_pf_sep)
            peaks, prefilter_discarded, prefilter_summary = prefilter_gate(
                peaks, _pf_peaks,
                multiples=multiples_per_bucket(_pf_points),
                detail=detail)
            print(f"Pre-filtro Óscar: {prefilter_summary['reason']}")
        except Exception as e:  # noqa: BLE001 - el pre-filtro nunca tumba el job
            print(f"Pre-filtro Óscar: no se pudo aplicar ({e}); "
                  "se sigue sin él.", file=sys.stderr)
            prefilter_discarded = []

    # 3. Filtro previo: los picos que no pasan (múltiplo < 3x, solo
    # emote-spam, sin mensajes, fuera del top-5) NO se cortan ni se
    # encolan; quedan registrados en el manifiesto como descartados.
    # --top-n es un MÁXIMO, no una cuota. Con --peak no se aplica:
    # el momento es explícito y el filtro es para la cola automática.
    if peak_mode:
        discarded = []
        print("Filtro previo: no se aplica — el momento es explícito "
              "(--peak).")
        # El bucle de abajo espera ternas (ts, score, multiple): en
        # modo --peak los picos venian como pares y reventaba con
        # ValueError (2026-11-18, VOD Rubius 2932415021). El multiple
        # es informativo: score / mediana local de la ventana.
        floor = max(median_score or 0.0, 1.0)
        peaks = [(ts, s, round(s / floor, 2)) for ts, s in peaks]
    elif viral_score_mod:
        peaks, discarded = filter_candidates(peaks, detail, rank_map,
                                             median_score)
        discarded = prefilter_discarded + discarded
    else:
        print("Aviso: sin viral_score.py, no se aplica el filtro previo; "
              "se cortan todos los picos.", file=sys.stderr)
        floor = max(median_score or 0.0, 1.0)
        peaks = [(ts, s, round(s / floor, 2)) for ts, s in peaks]
        discarded = prefilter_discarded
    for ts, s, m in peaks:
        print(f"  Filtro: {fmt_ts(ts)} — {s} pts ({m}x la mediana) pasa")
    for dc in discarded:
        print(f"  Descartado: {fmt_ts(dc['peak'])} — {dc['score']} pts "
              f"({dc['multiple']}x) — {'; '.join(dc['motivos'])}")
    if checkpoint_mod:
        mark_stage(state, "detect",
                   peaks=[ts for ts, _, _ in peaks],
                   descartados=len(discarded))
        save_state(out_dir, state)

    # 4. Cortar + titular y manifiesto
    manifest = {"vod_id": vod_id, "platform": platform,
                "source_url": args.vod, "lang": args.lang,
                "chat": chat_scope,
                "seo_overrides": [], "clips": [],
                "descartados": discarded,
                "prefilter": prefilter_summary,
                "status": "ok" if peaks else "sin_momentos_virales"}
    # Señal de Uri (2026-11-17): heatmap de YouTube ("lo más
    # repetido"), UNA sola lectura de metadatos con yt-dlp para
    # todo el VOD. Sin yt-dlp, sin heatmap o si el VOD no es de
    # YouTube, queda available False y el pipeline sigue solo con
    # el chat: nunca tumba el job.
    heatmap_result = {"available": False, "source": "youtube_heatmap",
                      "peaks": [], "reason": "heatmap desactivado"}
    if use_heatmap and args.vod:
        try:
            from clipagent.heatmap import heatmap_for_url
            heatmap_result = heatmap_for_url(
                args.vod,
                top_n=int(heatmap_cfg.get("top_n", 15)),
                min_separation=int(heatmap_cfg.get("min_separation_s", 60)))
            print(f"Heatmap: {heatmap_result['reason']}")
        except Exception as e:  # noqa: BLE001 - el heatmap nunca tumba el job
            heatmap_result = {"available": False,
                              "source": "youtube_heatmap", "peaks": [],
                              "reason": f"no se pudo leer el heatmap: {e}"}
            print(f"Heatmap: {heatmap_result['reason']}", file=sys.stderr)
    manifest["heatmap"] = heatmap_result
    # 7.1: candidatos para la selección con LLM (una llamada con
    # todos, tras el corte) + datos para recalcular el viral_score
    # con el hook del LLM. Sin LLM, el manifiesto queda igual.
    llm_candidates = []
    llm_inputs = {}
    # 7.4: facecam del VOD, UNA vez, en runs/<vod>/layout.json.
    # Si config trae facecam_box, manda siempre (no se detecta). Si
    # layout.json ya existe, se reutiliza. Si no, se detecta del
    # vídeo completo (--video) o del primer segmento (en el bucle).
    vod_layout = None
    vod_box = None

    def _vod_layout_detect(source, dur, source_label):
        """Detecta la facecam, guarda layout.json y fija vod_box."""
        nonlocal vod_layout, vod_box
        from clipagent.render import detect_vod_facecam, save_vod_layout
        base_cfg = get_layout_config() if get_layout_config else {}
        det = detect_vod_facecam(source, dur, base_cfg)
        vod_layout = {"vod_id": vod_id, "facecam_box": det["facecam_box"],
                      "method": det["method"], "samples": det["samples"],
                      "hits": det["hits"], "source": source_label}
        save_vod_layout(out_dir, vod_layout)
        manifest["vod_layout"] = vod_layout
        vod_box = det["facecam_box"]
        boxes = det.get("boxes") or []
        if vod_box:
            print(f"Layout del VOD: facecam detectada {vod_box} "
                  f"(method={det['method']}, {det['hits']}/"
                  f"{det['samples']} muestras; cajas vistas: {boxes}).")
        else:
            print(f"Layout del VOD: sin facecam estable (mejor grupo "
                  f"{det['hits']}/{det['samples']} muestras; cajas "
                  f"vistas: {boxes}); cada clip decide su layout solo.")
        return vod_layout

    try:
        from clipagent.render import load_vod_layout, save_vod_layout
        vod_layout = load_vod_layout(out_dir)
        if vod_layout is not None:
            vod_box = vod_layout.get("facecam_box")
            manifest["vod_layout"] = vod_layout
            print(f"Layout del VOD: reutilizado de layout.json "
                  f"(method={vod_layout.get('method')}, facecam "
                  f"{vod_box}, {vod_layout.get('hits')}/"
                  f"{vod_layout.get('samples')} muestras).")
        elif (cfg or {}).get("facecam_box"):
            vod_box = list((cfg or {})["facecam_box"])
            vod_layout = {"vod_id": vod_id, "facecam_box": vod_box,
                          "method": "config", "samples": 0, "hits": 0,
                          "source": "config.yaml"}
            save_vod_layout(out_dir, vod_layout)
            manifest["vod_layout"] = vod_layout
            print("Layout del VOD: facecam_box de config.yaml (manda).")
        elif args.video and video_path and os.path.isfile(video_path):
            _dur = None
            try:
                from clipagent.precise_cut import get_video_duration as _gvd
                _dur = _gvd(video_path)
            except Exception:
                _dur = None
            _vod_layout_detect(video_path, _dur, "video")
    except Exception as e:  # noqa: BLE001 - el layout nunca tumba el job
        print(f"Layout del VOD: no se pudo preparar ({e}); "
              "cada clip decide su layout solo.", file=sys.stderr)
    if platform == "kick" and kick_meta:
        manifest["kick"] = {"channel": kick_meta["channel"],
                            "title": kick_meta["title"],
                            "chat_source": "web.kick.com/api/v1/chat (sin documentar)"}
    if not peaks:
        write_manifest_atomic(out_dir, manifest)
        print("Sin momentos virales: ningún pico pasó el filtro previo "
              f"({len(discarded)} descartado(s); ver 'descartados' en "
              "manifest.json). No se cortó nada.")
        return 0
    for idx, (peak_ts, score, multiple) in enumerate(peaks, 1):
        # Checkpoint: clip ya hecho en una corrida anterior (archivo
        # en disco + entrada en el manifiesto anterior) -> se
        # reutiliza la entrada y se saltan descarga, corte y paquete.
        if checkpoint_mod:
            prev_entry = resume_entry(prev_manifest, peak_ts)
            if (prev_entry is not None and clip_done(state, peak_ts)
                    and os.path.isfile(os.path.join(
                        out_dir, prev_entry.get("file", "")))):
                print(f"[{idx}/{len(peaks)}] {fmt_ts(peak_ts)} reutilizado "
                      f"del checkpoint ({prev_entry['file']}).")
                manifest["clips"].append(prev_entry)
                # 7.1: el clip reanudado también es candidato (sin
                # transcripción en esta corrida; sin recompute de
                # score, solo se registra la selección del LLM).
                llm_candidates.append({
                    "start": prev_entry.get("start", 0),
                    "end": prev_entry.get("start", 0)
                           + prev_entry.get("duration", 30),
                    "transcript": None,
                    "chat_score": prev_entry.get("score", 0),
                    "chat_multiple": (prev_entry.get("viral") or {}).get(
                        "viral_multiple", 1.0),
                    "risas": prev_entry.get("risas", 0),
                    "hype": prev_entry.get("hype", 0)})
                write_manifest_atomic(out_dir, manifest)
                continue
        clip_start = max(0, peak_ts - args.pre_roll)
        # Corte variable: objetivo target_dur (30 s); el corte preciso
        # lo ajusta dentro de [min_dur, max_dur]. El segmento cubre
        # pre-roll + max(target, max_dur) para tener margen.
        clip_dur = target_dur
        if duration and clip_start + clip_dur > duration:
            clip_dur = max(1, int(duration - clip_start))
        d = detail[peak_ts]
        fname = f"clip_{idx:02d}_t{fmt_ts(clip_start)}_s{score}_r{d['risas']}h{d['hype']}.mp4"
        out_file = os.path.join(clips_dir, fname)
        examples = sample_messages(chat_path, peak_ts, window_size=args.window)
        if audio_peaks:
            examples = [tx for ts, _, tx in audio_peaks if ts == peak_ts][:1]
        print(f"[{idx}/{len(peaks)}] {fmt_ts(peak_ts)} ({score} pts) -> {fname}")
        # Puerta de render PRE-CORTE (regla del usuario 2026-11-18):
        # el clip SOLO se corta/renderiza si ya pasa las puertas con
        # el paquete provisional (viralidad estimada >= 45 pre-corte,
        # SEO >= 90 en al menos una plataforma). Si no pasa, a
        # descartados SIN descargar el segmento ni cortar nada: no se
        # gasta render en clips que no se van a usar. La puerta nunca
        # tumba el job: si no se puede evaluar, se corta igual y se
        # avisa. Los clips reutilizados del checkpoint (arriba) no
        # pasan por aquí: ya están renderizados, no se gasta nada.
        from clipagent.workers import gate_peak_for_render
        _g_streamer, _g_game, _g_src = None, None, ""
        if platform == "kick" and kick_meta:
            _g_streamer = kick_meta.get("channel")
            _g_game = kick_meta.get("category")
            _g_src = "categoría de Kick"
        else:
            try:
                _g_streamer, _g_game, _g_src = chat_stream_info(
                    chat_path, peak_ts)
            except Exception as e:  # noqa: BLE001 - sin juego no hay
                print(f"    Puerta de render: sin juego/streamer ({e}); "
                      "se sigue sin puerta.", file=sys.stderr)
                _g_streamer, _g_game = None, None
        _g_game = normalize_game_name(_g_game) if _g_game else None
        _gate = None
        try:
            _gate = gate_peak_for_render(
                chat_path, peak_ts, score, multiple, d, median_score,
                list(scores.values()), lang=args.lang, game=_g_game,
                streamer=_g_streamer, vod_id=vod_id, game_source=_g_src,
                clip_start=clip_start, duration=clip_dur, n=idx,
                reel_patterns=reel_patterns)
        except Exception as e:  # noqa: BLE001 - la puerta nunca
            print(f"    Puerta de render: no evaluada ({e}); se corta "
                  "igual.", file=sys.stderr)
        if _gate is not None and not _gate["pasa"]:
            print(f"    Puerta de render: NO pasa ({_gate['motivo']}); "
                  "se descarta sin cortar.", file=sys.stderr)
            manifest["descartados"].append({
                "peak": peak_ts, "score": score, "multiple": multiple,
                "viral_estimate": _gate.get("viral_estimate"),
                "seo_pre_corte": _gate.get("seo"),
                "motivos": [f"puerta de render: {_gate['motivo']}"]})
            write_manifest_atomic(out_dir, manifest)
            continue
        # Fuente del corte: Twitch y Kick bajan solo el segmento de este
        # pico (pre-roll + clip, ~80 s), nunca el VOD entero. Con
        # --video local, Twitch corta del archivo entero como antes.
        # cut_start es relativo a la FUENTE (0 dentro del segmento,
        # absoluto en el VOD entero); source_is_segment lo distingue
        # para el bloque 6 y el corte preciso.
        source_is_segment = False
        if platform == "kick":
            seg_path = os.path.join(segments_dir, f"seg_{idx:02d}_t{fmt_ts(clip_start)}.mp4")
            try:
                kick_mod.download_segment(kick_meta["source"], clip_start, seg_len, seg_path)
                cut_source, cut_start = seg_path, 0
                source_is_segment = True
            except Exception as e:
                print(f"[{idx}] Falló la descarga del segmento Kick: {e}", file=sys.stderr)
                continue
        elif args.video:
            cut_source, cut_start = video_path, clip_start
        else:
            seg_path = os.path.join(segments_dir, f"seg_{idx:02d}_t{fmt_ts(clip_start)}.mp4")
            try:
                download_twitch_segment(vod_id, clip_start, clip_start + seg_len, seg_path)
                cut_source, cut_start = seg_path, 0
                source_is_segment = True
            except Exception as e:
                print(f"[{idx}] Falló la descarga del segmento Twitch: {e}", file=sys.stderr)
                continue
        # 7.4: facecam del VOD, una sola vez (del primer segmento).
        # El box detectado vale para todos los clips; si config trae
        # facecam_box, ese manda (la detección ya lo respeta).
        if vod_layout is None and cut_source \
                and os.path.isfile(cut_source):
            try:
                _det_dur = seg_len if source_is_segment else (duration or seg_len)
                _vod_layout_detect(
                    cut_source, _det_dur,
                    "segmento" if source_is_segment else "video")
            except Exception as e:  # noqa: BLE001 - nunca tumba el job
                print(f"Layout del VOD: detección falló ({e}); "
                      "cada clip decide su layout solo.", file=sys.stderr)
        # Bloque 6 (--audio-check / recorte preciso): segunda señal de
        # audio y recorte preciso por frase, SOLO sobre la ventana ya
        # descargada (el segmento del pico, que empieza en clip_start
        # y deja el pico a pre_roll dentro; con --video local, la
        # ventana de seg_len del VOD, extraída a un temporal). Nunca
        # baja más vídeo. El recorte preciso va activado por defecto
        # (config clips.precise_cut); --no-precise-cut lo apaga.
        cut_dur = clip_dur
        audio_info = None
        precise_info = None
        transcript = None
        window_path = None
        if args.audio_check or use_precise:
            window_path = cut_source
            # El pico dentro de la ventana: pre_roll si la ventana es el
            # segmento (empieza en clip_start), o pico - cut_start si es
            # el VOD entero. Antes, en Kick, window_start_abs era 0 y
            # peak_rel salía el timestamp absoluto (p. ej. 11400 s),
            # fuera de la ventana de 60 s.
            window_start_abs = clip_start if source_is_segment else cut_start
            tmp_window = None
            if platform == "twitch" and args.video:
                tmp_window = os.path.join(
                    out_dir, f".window_{idx:02d}.mp4")
                # Ventana con inicio exacto (ver extract_window): con
                # -c copy empezaba en el keyframe anterior y peak_rel
                # quedaba desfasado hasta un GOP.
                extract_window(video_path, cut_start, seg_len, tmp_window)
                window_path = tmp_window
            peak_rel = peak_ts - window_start_abs
            transcript = None
            try:
                from clipagent.transcribe import transcribe_audio
                transcript, _ = transcribe_audio(
                    window_path,
                    language=None if args.lang == "both" else args.lang)
            except Exception as e:
                print(f"    Audio: no se pudo transcribir ({e}); "
                      f"se sigue con el corte fijo.", file=sys.stderr)
            if args.audio_check:
                if audio_mod:
                    full = audio_verdict(window_path, chat_score=score,
                                         peak_rel=peak_rel,
                                         transcript=transcript,
                                         lang=None if args.lang == "both"
                                         else args.lang)
                    audio_info = {
                        "decision": full["decision"],
                        "reason": full["reason"],
                        "stats": full["stats"],
                        "words": full["words"],
                        "transcription_bonus": full["transcription_bonus"],
                        "transcription_bonus_reason":
                            full["transcription_bonus_reason"],
                    }
                    print(f"    Audio: {full['decision'].upper()} — {full['reason']}")
                    if full["decision"] == "sospechar":
                        print("    Audio: candidato sospechoso (posible spam): "
                              "se corta igual, revisar a mano antes de publicar.")
                    if full["transcription_bonus"]:
                        print(f"    Audio: bonus transcripción "
                              f"+{full['transcription_bonus']:.0%} "
                              f"-> score {score * (1 + full['transcription_bonus']):.1f}")
                else:
                    print("    Audio: falta audio.py, se omite --audio-check.",
                          file=sys.stderr)
            if use_precise:
                if precise_mod:
                    window_dur = get_video_duration(window_path)
                    precise_info = refine_cut(
                        window_dur, peak_rel, transcript=transcript,
                        silences=detect_silences(window_path),
                        max_duration=max_dur, fixed_pre=args.pre_roll,
                        fixed_dur=target_dur)
                    # El corte variable vive en [min_dur, max_dur]:
                    # si la frase afinada queda corta, se amplía el
                    # fin dentro del segmento (el inicio manda).
                    if window_dur and precise_info["duration"] < min_dur:
                        new_end = min(window_dur,
                                      precise_info["start"] + min_dur)
                        if new_end - precise_info["start"] > precise_info["duration"]:
                            precise_info["end"] = round(new_end, 2)
                            precise_info["duration"] = round(
                                new_end - precise_info["start"], 2)
                            precise_info["reason"] += (
                                f"; ampliado al mínimo de {min_dur}s")
                    # refine_cut devuelve inicio/fin relativos a la
                    # ventana: dentro del segmento ese es el offset de
                    # corte; con el VOD entero hay que sumarle el
                    # inicio absoluto de la ventana.
                    if source_is_segment:
                        cut_start = precise_info["start"]
                    else:
                        cut_start = window_start_abs + precise_info["start"]
                    cut_dur = precise_info["duration"]
                    fname = (f"clip_{idx:02d}_t{fmt_ts(int(cut_start))}"
                             f"_s{score}_r{d['risas']}h{d['hype']}"
                             f"_precise.mp4")
                    out_file = os.path.join(clips_dir, fname)
                    print(f"    Corte preciso: {precise_info['start']:.1f}s -> "
                          f"{precise_info['end']:.1f}s "
                          f"({precise_info['duration']:.1f}s) "
                          f"[{precise_info['method']}] — {precise_info['reason']}")
                else:
                    print("    Corte preciso: falta precise_cut.py, se omite.",
                          file=sys.stderr)
            if tmp_window and os.path.isfile(tmp_window):
                os.remove(tmp_window)
        # Señal de Uri (2026-11-17): voz del juego, estimada ANTES
        # del corte para que el layout "game" se aplique al cortar.
        # Se detecta sobre la ventana ya descargada (transcripción
        # + RMS + movimiento): el streamer calla >= 2 s mientras el
        # audio sigue activo y el juego se mueve. Los tramos pasan
        # de coordenadas de ventana a coordenadas de clip (el clip
        # empieza en precise_info["start"] dentro de la ventana, o
        # en 0 sin corte preciso). Sin transcripción no se estima
        # nada a ciegas. La detección es "estimada" y sin texto:
        # las palabras del juego no se inventan.
        game_voice_clip = []
        if use_game_voice and transcript is not None and window_path \
                and os.path.isfile(window_path):
            try:
                from clipagent.audio import rms_per_second as _rms_ps
                from clipagent.game_voice import (
                    detect_game_voice as _detect_gv,
                    normalize_game_voice as _norm_gv)
                from clipagent.motion import measure_motion as _mm_w
                _win_dur = None
                try:
                    from clipagent.precise_cut import (
                        get_video_duration as _gvd_w)
                    _win_dur = _gvd_w(window_path)
                except Exception:
                    _win_dur = None
                _motion_w = _mm_w(
                    window_path, duration=_win_dur,
                    threshold=float(motion_cfg.get("threshold", 3.0)))
                _spans_w = _detect_gv(
                    transcript,
                    audio_rms=_rms_ps(window_path),
                    clip_duration=_win_dur,
                    silence_s=float(game_voice_cfg.get("silence_s", 2.0)),
                    active_dbfs=float(game_voice_cfg.get("active_dbfs", -45.0)),
                    motion_per_second=(_motion_w.get("motion_per_second")
                                       if _motion_w.get("available") else None),
                    motion_threshold=float(motion_cfg.get("threshold", 3.0)))
                _offset = (precise_info["start"]
                           if precise_info is not None else 0.0)
                game_voice_clip = _norm_gv(
                    [[s - _offset, e - _offset] for s, e in
                     [(sp["start"], sp["end"]) for sp in _spans_w]],
                    cut_dur, source="estimada")
                if game_voice_clip:
                    print(f"    Voz del juego (estimada): "
                          f"{len(game_voice_clip)} tramo(s) "
                          f"{[(s['start'], s['end']) for s in game_voice_clip]}")
            except Exception as e:  # noqa: BLE001 - nunca tumba el job
                print(f"    Voz del juego: no se pudo estimar ({e}); "
                      "se sigue sin ella.", file=sys.stderr)
                game_voice_clip = []
        # Corte con layout responsive (regla del proyecto): decide por
        # fotogramas muestreados (stack / pip / center, por tramos si
        # cambia dentro del clip). Sin layout.py, recorte centrado.
        layout_decision = None
        try:
            if layout_mod:
                layout_cfg = get_layout_config() if get_layout_config else {}
                # 7.4: el box del VOD (detectado una vez o de config)
                # manda sobre la detección por clip; config manda
                # sobre el detectado.
                if vod_box and not layout_cfg.get("facecam_box"):
                    layout_cfg["facecam_box"] = vod_box
                if game_voice_clip:
                    # Tramos de voz del juego: esos tramos se cortan
                    # en layout "game" (juego a pantalla completa).
                    layout_cfg["game_voice"] = game_voice_clip
                layout_decision = cut_clip_with_layout(
                    cut_source, cut_start, cut_dur, out_file,
                    crf=args.crf, preset=args.preset, crop=args.crop,
                    layout_mode=args.layout, cfg=layout_cfg)
                print(f"    Layout: {layout_summary(layout_decision)}")
            else:
                cut_vertical_clip(cut_source, cut_start, cut_dur, out_file,
                                  crf=args.crf, preset=args.preset, crop=args.crop)
        except (subprocess.CalledProcessError, RuntimeError) as e:
            print(f"[{idx}] Falló el corte: {e}", file=sys.stderr)
            continue
        # 4. Ficha del momento + paquete viral HONESTO (títulos honestos):
        # la ficha se construye DESPUÉS del corte (necesita el clip y su
        # layout) y se pasa a package_clip con game, streamer, vod_id y
        # layout (de ahí sale `visible`). Antes, package_clip se llamaba
        # sin ficha ni layout y el manifiesto se encabezaba con
        # suggest_titles() sin validar.
        clip_start_abs = clip_start
        if precise_info is not None:
            # El corte preciso mueve el inicio dentro de la ventana
            # (que empieza en clip_start en ambos casos: segmento o
            # VOD entero con --video).
            clip_start_abs = clip_start + precise_info["start"]
        # 7.3: dedup antes de empaquetar. El `start` del manifiesto
        # es ABSOLUTO en el VOD (antes era cut_start, relativo al
        # segmento: dos picos distintos colisionaban en moment_key).
        # Si el clip duplica a otro (ventana solapada o misma huella
        # de 3 frames), va a descartados y no se empaqueta ni se
        # encola; el archivo huérfano se borra para no gastar disco.
        _fp = None
        try:
            from clipagent.dedup import clip_fingerprint, find_duplicate
            _fp = clip_fingerprint(out_file, cut_dur)
            _dup_reason, _dup_entry = find_duplicate(
                out_file, cut_dur, clip_start_abs,
                os.path.dirname(out_dir), vod_id,
                manifest_clips=manifest["clips"],
                # El manifiesto propio nunca es "otro VOD": sin esto,
                # un --fresh descartaba el clip re-cortado como
                # duplicado de su propia entrada anterior.
                exclude_manifest=os.path.join(out_dir,
                                              "manifest.json"))
            if _dup_reason:
                print(f"    Dedup: duplicado ({_dup_reason}); "
                      "no se empaqueta ni se encola.", file=sys.stderr)
                manifest["descartados"].append({
                    "peak": peak_ts, "score": score, "multiple": multiple,
                    "motivos": [f"duplicado: {_dup_reason}"]})
                write_manifest_atomic(out_dir, manifest)
                try:
                    os.remove(out_file)
                except OSError:
                    pass
                continue
        except Exception as e:  # noqa: BLE001 - el dedup nunca tumba el job
            print(f"    Dedup: no se pudo comprobar ({e}); se sigue.",
                  file=sys.stderr)
        payoff_ts_clip = peak_ts - clip_start_abs
        streamer, game, game_source = None, None, ""
        if platform == "kick" and kick_meta:
            streamer = kick_meta.get("channel")
            game = kick_meta.get("category")
            game_source = "categoría de Kick"
            if isinstance(game, str) and game.strip().lower() == "just chatting":
                game, game_source = None, "categoría Just Chatting (sin juego)"
        else:
            streamer, game, game_source = chat_stream_info(chat_path, peak_ts)
        game = normalize_game_name(game)
        moment_card = None
        if moment_mod:
            try:
                moment_card = build_moment_card(
                    clip_path=out_file, chat_path=chat_path,
                    streamer=streamer or "", game=game,
                    game_source=game_source,
                    language=args.lang if args.lang in ("es", "en") else "es",
                    peak_ts_vod=peak_ts, clip_start_vod=clip_start_abs,
                    clip_duration_s=cut_dur, score=score,
                    risas=d["risas"], hype=d["hype"],
                    median_score=median_score, vod_id=vod_id)
            except Exception as e:
                print(f"    Ficha del momento: falló ({e}); "
                      f"se sigue sin ficha.", file=sys.stderr)
        viral = None if args.no_viral else package_clip(
            idx, peak_ts, score, d["risas"], d["hype"],
            median_score=median_score, all_scores=list(scores.values()),
            lang=args.lang, examples=examples, moment_card=moment_card,
            game=game, streamer=streamer, vod_id=vod_id,
            layout=layout_decision, reel_patterns=reel_patterns,
            # 7.5: crédito al streamer en la descripción (config).
            credit_streamer=((cfg or {}).get("metadata") or {}).get(
                "credit_streamer", True),
        )
        visible = None
        visible_reason = None
        if viral:
            visible = viral.get("visible")
            visible_reason = viral.get("visible_reason")
        elif viral_checks_mod and layout_decision is not None:
            info = visible_from_layout(layout_decision,
                                       payoff_ts_clip=payoff_ts_clip)
            visible, visible_reason = info.get("visible"), info.get("reason")
        # Títulos del manifiesto: el viral manda en su idioma (título
        # honesto con check); el otro idioma usa el legacy degradado.
        # suggest_titles() NUNCA va al manifiesto sin validar.
        title_es_raw, desc_es = suggest_titles(d, "es", game=game)
        title_en_raw, desc_en = suggest_titles(d, "en", game=game)
        title_es, degraded_es, why_es = honest_title(
            title_es_raw, moment_card, layout_decision, payoff_ts_clip, "es")
        title_en, degraded_en, why_en = honest_title(
            title_en_raw, moment_card, layout_decision, payoff_ts_clip, "en")
        if viral and viral.get("best_title"):
            if args.lang in ("es", "both"):
                title_es = viral["best_title"]
            elif args.lang == "en":
                title_en = viral["best_title"]
        print(f"    Título ES: {title_es} | EN: {title_en}")
        if degraded_es or degraded_en:
            print(f"    Título legacy degradado (mentía): ES={degraded_es} EN={degraded_en}")
        if viral:
            print(f"    Viral: {viral['viral_multiple']}x la mediana | {viral['best_title']} | Miniatura: {viral['thumbnail_text']} | Visible: {viral.get('visible')}")
            if viral.get("needs_human_review"):
                print("    Viral: sin título que pase el check — revisar a mano antes de publicar.")
        legacy_titles = {
            "es": {"raw": title_es_raw, "final": title_es,
                   "degraded": degraded_es, "reason": why_es},
            "en": {"raw": title_en_raw, "final": title_en,
                   "degraded": degraded_en, "reason": why_en},
        }
        # Score de viralidad 0-100 (viral_score.py: 7 componentes,
        # banda, filtro previo) + señales MVP + score SEO por
        # plataforma. La viralidad es DISTINTA del score de chat
        # ("score" arriba): no se mezclan, van las dos al manifiesto.
        viral_score = None
        signals = None
        seo_scores = None
        motion_result = None
        if viral_score_mod:
            # `multiple` es el del filtro previo (mismo cálculo, con
            # suelo 1.0 en la mediana): no se recalcula aquí.
            human = (human_scores.get("all")
                     or human_scores.get(str(idx))
                     or human_scores.get(f"peak_{peak_ts}"))
            audio_in = None
            if audio_info is not None:
                audio_in = {"stats": audio_info.get("stats"),
                            "words": audio_info.get("words")}
            # 7.2: peso del audio configurable y mayor en Kick.
            _audio_w = (audio_weight_for(platform, cfg)
                        if audio_weight_for else None)
            viral_score = score_clip(detail[peak_ts], multiple,
                                     rank=rank_map.get(peak_ts),
                                     human=human, audio=audio_in,
                                     audio_weight=_audio_w,
                                     platform=platform, score=score)
            print(f"    Viralidad: {viral_score['total']}/100 "
                  f"({viral_score['band']})"
                  + (" [BETA: componentes estimados]" if viral_score["beta"] else "")
                  + f" | chat {viral_score['components']['chat']['value']}/10"
                  + ("" if viral_score["filter_passed"]
                     else " | NO pasa el filtro previo"))
            # La transcripción es relativa a la ventana (segmento);
            # el clip empieza precise_info["start"] dentro de ella.
            # Se desplazan los timestamps al tiempo del clip para
            # que las señales (hook 0-3 s, coincidencia de picos)
            # se midan sobre el clip, no sobre la ventana. Sin
            # corte preciso el clip empieza en la ventana: shift 0.
            transcript_clip = transcript
            if transcript is not None and precise_info is not None:
                shift = precise_info["start"]
                transcript_clip = {"segments": [
                    {**seg,
                     "start": (seg.get("start") or 0) - shift,
                     "end": (seg.get("end") or 0) - shift,
                     "words": [{**w,
                                "start": (w.get("start") or 0) - shift,
                                "end": (w.get("end") or 0) - shift}
                               for w in seg.get("words", [])]}
                    for seg in transcript.get("segments", [])]}
            try:
                # Señales de Uri (2026-11-17): el movimiento se mide
                # sobre el clip ya cortado (1 fps, una pasada de
                # ffmpeg); el heatmap se leyó una vez para todo el
                # VOD; la voz del juego se estimó antes del corte.
                # Todo diagnóstico: no cambia viralidad, prioridad
                # ni filtros duros.
                motion_result = None
                if use_motion and os.path.isfile(out_file):
                    try:
                        from clipagent.motion import measure_motion
                        motion_result = measure_motion(
                            out_file, duration=cut_dur,
                            threshold=float(motion_cfg.get("threshold", 3.0)))
                        if motion_result.get("available"):
                            print(f"    Movimiento: "
                                  f"{motion_result['motion_pct']}% "
                                  f"({motion_result['reason']})")
                    except Exception as e:  # noqa: BLE001 - nunca tumba el job
                        print(f"    Movimiento: no se pudo medir ({e}); "
                              "se sigue sin él.", file=sys.stderr)
                        motion_result = None
                signals = compute_signals(
                    detail=detail[peak_ts], peak_ts=peak_ts,
                    clip_start=clip_start_abs, clip_duration=cut_dur,
                    transcript=transcript_clip, video_path=out_file,
                    lang=args.lang if args.lang in ("es", "en") else "es",
                    heatmap_peaks=(heatmap_result.get("peaks")
                                   if heatmap_result.get("available") else None),
                    motion=motion_result,
                    game_voice=game_voice_clip,
                    virality_total=(viral_score["total"]
                                    if viral_score else None))
            except Exception as e:
                print(f"    Señales MVP: falló ({e}); se sigue sin señales.",
                      file=sys.stderr)
        if seo_mod and viral is not None:
            seo_scores = score_package_seo(
                viral, visible=visible,
                lang=args.lang if args.lang in ("es", "en") else "es")
            for plat, r in seo_scores.items():
                print(f"    SEO {plat}: {r['score']} ({r['status']})"
                      + ("" if r["can_publish"] else " — no publicable tal cual"))
        elif seo_mod:
            # Sin paquete viral: se puntúa el título legacy.
            seo_scores = score_all_platforms({
                "title": title_es, "description": desc_es,
                "tags": [], "thumbnail_text": None,
                "hashtags": HASHTAGS_ES.split(),
                "game_verified": bool(game), "visible": visible,
                "lang": "es"})
        clip_entry = {
            "n": idx, "file": f"clips/{fname}", "peak": peak_ts,
            "start": clip_start_abs, "duration": cut_dur, "score": score,
            "risas": d["risas"], "hype": d["hype"],
            "title_es": title_es, "desc_es": desc_es,
            "title_en": title_en, "desc_en": desc_en,
            "hashtags_es": HASHTAGS_ES, "hashtags_en": HASHTAGS_EN,
            "chat_examples": examples,
            "viral": viral,
            "viral_score": viral_score,
            "signals": signals,
            "seo_scores": seo_scores,
            # Señales de Uri (2026-11-17, solo diagnóstico): tramos
            # de voz del juego (estimados, sin texto inventado) y %
            # de movimiento del clip. No cambian viralidad, prioridad
            # ni filtros duros.
            "game_voice": game_voice_clip,
            "motion": motion_result,
            "moment_card": moment_card,
            "visible": visible,
            "visible_reason": visible_reason,
            "legacy_titles": legacy_titles,
        }
        if audio_info is not None:
            clip_entry["audio"] = audio_info
        if _fp is not None:
            # Huella de 3 frames (7.3) para el dedup de otras corridas.
            clip_entry["fingerprint"] = [f"{h:016x}" for h in _fp]
        if precise_info is not None:
            clip_entry["precise_cut"] = precise_info
        if layout_decision is not None:
            clip_entry["layout"] = {
                "mode": layout_decision.get("mode"),
                "segments": layout_decision.get("segments", []),
            }
        manifest["clips"].append(clip_entry)
        # 7.1: candidato para la selección con LLM (transcripción de
        # la ventana + chat). Los datos para recalcular el viral_score
        # con el hook del LLM van aparte, por pico.
        llm_candidates.append({
            "start": clip_entry["start"],
            "end": clip_entry["start"] + clip_entry["duration"],
            "transcript": transcript,
            "chat_score": score, "chat_multiple": multiple,
            "risas": d["risas"], "hype": d["hype"]})
        if viral_score is not None:
            llm_inputs[peak_ts] = (detail[peak_ts], multiple,
                                   rank_map.get(peak_ts), human, audio_in)
        # Manifiesto tras CADA clip (atómico): si el job muere en el
        # clip 3 de 5, los clips 1-2 ya están guardados en disco.
        write_manifest_atomic(out_dir, manifest)
        if checkpoint_mod:
            mark_clip_done(state, peak_ts, fname)
            save_state(out_dir, state)

    # 7.1: selección con LLM (opcional, una llamada con todos los
    # candidatos). Sin LLM o si falla, el manifiesto queda tal cual.
    if manifest["clips"] and args.llm_select and llm_candidates:
        try:
            from clipagent.llm import get_llm_client
            from clipagent.select import apply_llm_selection, select_moments
            _client = get_llm_client()
            _selections, _used = select_moments(llm_candidates, _client)
            if _used and _selections:
                _n = apply_llm_selection(manifest, _selections, llm_inputs)
                print(f"LLM: {len(_selections)} momento(s) seleccionados, "
                      f"{_n} viral_score(s) actualizados con el hook LLM.")
                write_manifest_atomic(out_dir, manifest)
            elif _used:
                print("LLM: sin selecciones válidas; se sigue con los "
                      "candidatos del chat.")
            else:
                print("LLM: sin LLM disponible; se sigue con los "
                      "candidatos del chat.")
        except Exception as e:  # noqa: BLE001 - nunca tumbar el job
            print(f"LLM: la selección falló ({e}); se sigue con el chat.",
                  file=sys.stderr)

    write_manifest_atomic(out_dir, manifest)
    if checkpoint_mod:
        mark_stage(state, "manifest")
        save_state(out_dir, state)
    lines = [f"# Clips — VOD {vod_id or 'local'}", ""]
    for c in manifest["clips"]:
        lines += [f"## Clip {c['n']} — {c['file']}",
                  f"- Pico: {fmt_ts(c['peak'])} | {c['score']} pts ({c['risas']} risas, {c['hype']} hype)",
                  f"- ES: {c['title_es']} — {c['desc_es']} {c['hashtags_es']}",
                  f"- EN: {c['title_en']} — {c['desc_en']} {c['hashtags_en']}"]
        lay = c.get("layout")
        if lay:
            lines.append(f"- Layout: {layout_summary({'segments': lay['segments']})}" if layout_summary else f"- Layout: {lay['mode']}")
        vis = c.get("visible")
        if vis:
            lines.append(f"- Visible: {vis}" + (f" ({c.get('visible_reason')})" if c.get("visible_reason") else ""))
        leg = c.get("legacy_titles") or {}
        for lg in ("es", "en"):
            info = leg.get(lg) or {}
            if info.get("degraded"):
                lines.append(f"- Título legacy {lg.upper()} degradado: {info['raw']!r} → {info['final']!r} ({info.get('reason', '')})")
        v = c.get("viral")
        if v:
            lines += [f"- Viral: {v['viral_multiple']}x la mediana | Fórmula: {v['formula']} ({v['formula_desc']})",
                      f"- Título viral: {v['best_title']} | Miniatura: {v['thumbnail_text']} (no repite el título)",
                      f"- Hook (1ª línea): {v['hook_first_line']} | Texto en pantalla 2 s: {v['onscreen_first_2s']} | Loop: {v['loop_point']}",
                      f"- Descripción: {v['description_first_2_lines']}",
                      f"- Tags: {', '.join(v['tags'])}",
                      f"- Búsquedas a ganar: {', '.join(v['search_queries'])}",
                      "- ¿Lo subo así, o lo cambio?"]
            plats = v.get("platforms", {}) if v else {}
            if plats:
                lines.append("- Por plataforma (misma idea, paquete distinto):")
                for pk in ("youtube_shorts", "tiktok", "instagram_reels", "facebook_reels"):
                    pp = plats.get(pk)
                    if not pp:
                        continue
                    cap = pp["caption"].replace("\n", " / ")
                    lines.append(
                        f"  - {pp['platform']}: {cap} | Subtítulos: {pp['subtitles']} | "
                        f"Miniatura: {pp['thumbnail']} | Subida: {pp['publish_mode']} | Métrica: {pp['metric']}"
                    )
        vs = c.get("viral_score")
        if vs:
            lines.append(
                f"- Viralidad: {vs['total']}/100 ({vs['band']})"
                + (" [BETA: componentes estimados]" if vs.get("beta") else "")
                + f" | chat {vs['components']['chat']['value']}/10 "
                f"({vs.get('chat_multiple')}x la mediana) | filtro previo: "
                f"{'pasa' if vs.get('filter_passed') else 'no pasa'}")
        ss = c.get("seo_scores") or {}
        if ss:
            lines.append("- SEO: " + " | ".join(
                f"{p}: {r['score']} ({r['status']})" for p, r in ss.items()))
        sig = c.get("signals") or {}
        uri_bits = []
        if sig.get("heatmap_peak_s") is not None:
            uri_bits.append(f"heatmap {sig['heatmap_peak_s']}s "
                            f"(score {sig.get('heatmap_score')})")
        if sig.get("motion_pct") is not None:
            uri_bits.append(f"movimiento {sig['motion_pct']}% "
                            f"({sig.get('motion_flag')})")
        if c.get("game_voice"):
            uri_bits.append("voz del juego: " + ", ".join(
                f"{s['start']}-{s['end']}s" + (" (estimada)" if s.get("estimated") else "")
                for s in c["game_voice"]))
        if uri_bits:
            lines.append("- Señales Uri: " + " | ".join(uri_bits))
        lines += [""]
    with open(os.path.join(out_dir, "manifest.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(os.path.join(out_dir, "titles.txt"), "w", encoding="utf-8") as f:
        for c in manifest["clips"]:
            v = c.get("viral")
            best = v["best_title"] if v else c["title_es"]
            f.write(f"Clip {c['n']} viral: {best} | Miniatura: {v['thumbnail_text'] if v else ''}\n")
            f.write(f"Clip {c['n']} ES: {c['title_es']}\nClip {c['n']} EN: {c['title_en']}\n")

    if args.delete_vod and not args.video and video_path and os.path.isfile(video_path):
        os.remove(video_path)
        print("VOD borrado (--delete-vod).")

    print(f"\nListo: {len(manifest['clips'])} clips en {clips_dir}/")
    print(f"Manifiesto: {out_dir}/manifest.md  |  Títulos: {out_dir}/titles.txt")
    print("Lo único que queda a mano es subirlos a TikTok / Reels / Shorts — "
          "eso no se puede automatizar sin que cada plataforma te pida entrar y confirmar en tu teléfono.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
