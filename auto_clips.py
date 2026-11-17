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



from clips_chat_v2 import (
    cut_vertical_clip,
    fmt_ts,
    get_video_duration,
    sample_messages,
    score_windows,
    select_top_peaks,
)
from viral_package import package_clip

try:
    # Score SEO por plataforma del paquete (seo_score.py).
    from viral_package import score_package_seo
    from seo_score import score_all_platforms
    seo_mod = True
except ImportError:
    score_package_seo = None
    score_all_platforms = None
    seo_mod = False

try:
    # Títulos honestos (arreglos 3-4): validar/degradar títulos legacy,
    # derivar lo visible del layout y normalizar el nombre del juego.
    from viral_package import (
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
    from layout import cut_clip_with_layout, layout_summary
    layout_mod = True
except ImportError:
    cut_clip_with_layout = None
    layout_summary = None
    layout_mod = False

try:
    # Bloque 6: audio como segunda señal + recorte preciso por frase.
    from audio import audio_verdict
    audio_mod = True
except ImportError:
    audio_verdict = None
    audio_mod = False

try:
    from precise_cut import detect_silences, refine_cut
    precise_mod = True
except ImportError:
    detect_silences = None
    refine_cut = None
    precise_mod = False

try:
    # Ficha del momento (bloque 2 de la revisión interna): evidencia del clip
    # (chat, streamer, juego, reacción) para títulos honestos.
    from moment_card import build_moment_card
    moment_mod = True
except ImportError:
    build_moment_card = None
    moment_mod = False

try:
    from config import get_layout_config, load_config
except ImportError:
    get_layout_config = None
    load_config = None

try:
    # Score de viralidad 0-100 + señales MVP (7 componentes, banda,
    # filtro previo). Distinto del score de chat: no se mezclan.
    from viral_score import compute_signals, filter_passed, score_clip
    viral_score_mod = True
except ImportError:
    compute_signals = None
    filter_passed = None
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


TWITCH_CLI_DEFAULT = os.path.expanduser(
    "~/workspace/tools/twitchdownloader/TwitchDownloaderCLI")


def twitch_cli() -> str:
    """Ruta a TwitchDownloaderCLI: del PATH si está, si no la ruta
    conocida de este equipo (v1.56.5, NO está en el PATH)."""
    found = shutil.which("TwitchDownloaderCLI")
    if found:
        return found
    if os.path.isfile(TWITCH_CLI_DEFAULT):
        return TWITCH_CLI_DEFAULT
    return "TwitchDownloaderCLI"


def twitch_cli_available() -> bool:
    cli = twitch_cli()
    return shutil.which(cli) is not None or os.path.isfile(cli)


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
    seleccionados = [(peak_ts, score, multiple)] en orden cronológico;
    descartados = [{"peak", "score", "multiple", "rank", "motivos"}].
    La mediana tiene suelo 1.0: con mediana 0 un pico con puntos
    queda a score/1 x (si no, por división por cero ningún pico
    pasaría y un chat casi vacío no daría clips nunca).
    """
    floor = max(median_score or 0.0, 1.0)
    selected = []
    discarded = []
    for peak_ts, score in peaks:
        multiple = round(score / floor, 2)
        rank = rank_map.get(peak_ts)
        pasa, motivos = filter_passed(detail[peak_ts], multiple, rank=rank)
        if pasa:
            selected.append((peak_ts, score, multiple))
        else:
            discarded.append({"peak": peak_ts, "score": score,
                              "multiple": multiple, "rank": rank,
                              "motivos": motivos})
    return selected, discarded


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
        for ch in vid.get("chapters") or []:
            if not isinstance(ch, dict):
                continue
            try:
                start = (ch.get("startMilliseconds") or 0) / 1000.0
                length = (ch.get("lengthMilliseconds") or 0) / 1000.0
            except (TypeError, ValueError):
                continue
            if start <= peak_ts < start + length:
                game = ch.get("gameDisplayName") or ch.get("description")
                game_source = f"capítulo de Twitch: {game}"
                break
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
    p.add_argument("--pre-roll", type=int, default=20)
    p.add_argument("--duration", type=int, default=None,
                   help="Duración forzada del clip en segundos (default: "
                        "config clips.target_duration_s, 30 s; el corte "
                        "variable usa target/min/max de config.yaml)")
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
    p.add_argument("--delete-vod", action="store_true", help="Sin efecto: ya no se baja el VOD entero (se guardan solo segmentos por pico). Se mantiene el flag para no romper llamadas viejas")
    p.add_argument("--force-chat", action="store_true", help="Forzar la descarga del chat aunque ya exista un chat.json válido (por defecto se reutiliza: la descarga tarda ~18 min en un VOD de 3 h)")
    p.add_argument("--audio-fallback", action="store_true", help="En Kick, si el chat viene vacío, buscar picos por audio transcrito (muestreo)")
    p.add_argument("--audio-check", action="store_true",
                   help="Bloque 6: RMS por segundo en la ventana del candidato y veredicto "
                        "confirmar/sospechar (chat alto + audio plano = sospechar spam); "
                        "se corta igual, solo se marca en el manifiesto")
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

    # Cortes variables (config.yaml, sección clips): objetivo 30 s,
    # rango 21-40 s, tope duro 60 s. --duration fuerza la duración;
    # si no, manda target_duration_s. El segmento descargado cubre
    # pre-roll + max(target, max_duration_s) para que el corte
    # preciso tenga margen para afinar.
    cfg = load_config() if load_config else {}
    clip_cfg = (cfg or {}).get("clips") or {}
    target_dur = args.duration or int(clip_cfg.get("target_duration_s") or 30)
    max_dur = args.duration or int(clip_cfg.get("max_duration_s") or 40)
    min_dur = 1 if args.duration else int(clip_cfg.get("min_duration_s") or 21)
    use_precise = (args.precise_cut if args.precise_cut is not None
                   else bool(clip_cfg.get("precise_cut", True)))
    seg_len = args.pre_roll + max(target_dur, max_dur)
    human_scores = {}
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

    chat_path = args.chat or os.path.join(out_dir, "chat.json")
    # Sin --video ya no hay vod.mp4: Twitch corta de un segmento por
    # pico (ver el bucle). video_path solo vive con --video local.
    video_path = args.video

    # TwitchDownloaderCLI solo hace falta para Twitch. Kick usa su API + ffmpeg.
    needs_download = bool(vod_id and (not args.chat or (not args.video and not args.dry_run)))
    if needs_download and platform == "twitch" and not twitch_cli_available():
        print("Error: TwitchDownloaderCLI no está en el PATH ni en "
              "~/workspace/tools/twitchdownloader/.", file=sys.stderr)
        return 1
    if shutil.which("ffmpeg") is None and not args.dry_run:
        print("Error: FFmpeg no está en el PATH.", file=sys.stderr)
        return 1

    # 1. Descargar el chat (o reutilizar)
    if args.chat:
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
            download_chat(vod_id, chat_path, force=args.force_chat)
    if not os.path.isfile(chat_path):
        print(f"Error: no existe el chat: {chat_path}", file=sys.stderr)
        return 1
    # Con --chat local en Kick igual hacen falta los metadatos (source HLS,
    # duración) para bajar los segmentos.
    if platform == "kick" and kick_meta is None and args.vod:
        slug, uuid = kick_mod.parse_kick_url(args.vod)
        kick_meta = kick_mod.resolve_vod(slug, uuid)

    # 2. Detectar picos (por chat; en Kick, fallback por audio si viene vacío)
    scores, detail = score_windows(chat_path, window_size=args.window, lang=args.lang)
    total_msgs = sum(d["msgs"] for d in detail.values())
    audio_peaks = []
    if not scores or total_msgs == 0:
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
            median = statistics.median(scores.values())
            ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
            rank_map = {ts: i + 1 for i, (ts, _s) in enumerate(ranked)}
            print(f"Score de viralidad (BETA, sin entrada humana):")
            for ts, score in peaks:
                multiple = score / median if median > 0 else None
                vs = score_clip(detail[ts], multiple, rank=rank_map.get(ts),
                                human=(human_scores.get("all")
                                       or human_scores.get(str(ts))))
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
    # usan para el múltiplo viral (x veces la mediana).
    median_score = statistics.median(scores.values()) if scores else None
    # Puesto de cada pico en el VOD (1 = el mejor): lo usa el filtro
    # previo del score de viralidad (solo top-5).
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    rank_map = {ts: i + 1 for i, (ts, _s) in enumerate(ranked)}

    # 3. Filtro previo: los picos que no pasan (múltiplo < 3x, solo
    # emote-spam, sin mensajes, fuera del top-5) NO se cortan ni se
    # encolan; quedan registrados en el manifiesto como descartados.
    # --top-n es un MÁXIMO, no una cuota.
    if viral_score_mod:
        peaks, discarded = filter_candidates(peaks, detail, rank_map,
                                             median_score)
    else:
        print("Aviso: sin viral_score.py, no se aplica el filtro previo; "
              "se cortan todos los picos.", file=sys.stderr)
        floor = max(median_score or 0.0, 1.0)
        peaks = [(ts, s, round(s / floor, 2)) for ts, s in peaks]
        discarded = []
    for ts, s, m in peaks:
        print(f"  Filtro: {fmt_ts(ts)} — {s} pts ({m}x la mediana) pasa")
    for dc in discarded:
        print(f"  Descartado: {fmt_ts(dc['peak'])} — {dc['score']} pts "
              f"({dc['multiple']}x) — {'; '.join(dc['motivos'])}")

    # 4. Cortar + titular y manifiesto
    manifest = {"vod_id": vod_id, "platform": platform,
                "source_url": args.vod, "lang": args.lang,
                "seo_overrides": [], "clips": [],
                "descartados": discarded,
                "status": "ok" if peaks else "sin_momentos_virales"}
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
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-ss", str(cut_start),
                     "-i", video_path, "-t", str(seg_len), "-c", "copy",
                     tmp_window], check=True)
                window_path = tmp_window
            peak_rel = peak_ts - window_start_abs
            transcript = None
            try:
                from subtitles import transcribe_audio
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
        # Corte con layout responsive (regla del proyecto): decide por
        # fotogramas muestreados (stack / pip / center, por tramos si
        # cambia dentro del clip). Sin layout.py, recorte centrado.
        layout_decision = None
        try:
            if layout_mod:
                layout_cfg = get_layout_config() if get_layout_config else {}
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
            viral_score = score_clip(detail[peak_ts], multiple,
                                     rank=rank_map.get(peak_ts),
                                     human=human, audio=audio_in)
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
                signals = compute_signals(
                    detail=detail[peak_ts], peak_ts=peak_ts,
                    clip_start=clip_start_abs, clip_duration=cut_dur,
                    transcript=transcript_clip, video_path=out_file,
                    lang=args.lang if args.lang in ("es", "en") else "es")
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
            "start": cut_start, "duration": cut_dur, "score": score,
            "risas": d["risas"], "hype": d["hype"],
            "title_es": title_es, "desc_es": desc_es,
            "title_en": title_en, "desc_en": desc_en,
            "hashtags_es": HASHTAGS_ES, "hashtags_en": HASHTAGS_EN,
            "chat_examples": examples,
            "viral": viral,
            "viral_score": viral_score,
            "signals": signals,
            "seo_scores": seo_scores,
            "moment_card": moment_card,
            "visible": visible,
            "visible_reason": visible_reason,
            "legacy_titles": legacy_titles,
        }
        if audio_info is not None:
            clip_entry["audio"] = audio_info
        if precise_info is not None:
            clip_entry["precise_cut"] = precise_info
        if layout_decision is not None:
            clip_entry["layout"] = {
                "mode": layout_decision.get("mode"),
                "segments": layout_decision.get("segments", []),
            }
        manifest["clips"].append(clip_entry)
        # Manifiesto tras CADA clip (atómico): si el job muere en el
        # clip 3 de 5, los clips 1-2 ya están guardados en disco.
        write_manifest_atomic(out_dir, manifest)

    write_manifest_atomic(out_dir, manifest)
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
                      f"- ¿Lo subo así, o lo cambio?"]
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
