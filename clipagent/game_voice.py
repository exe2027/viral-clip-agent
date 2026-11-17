#!/usr/bin/env python3
"""game_voice.py — voz del juego (idea de Uri).

Origen: clipper-directos de Uri Cortina (``SKILL.md`` y
``scripts/resolve.py``): cuando habla EL JUEGO (tráiler, escena),
el clip pasa a juego a pantalla completa, el subtítulo es propio
(cursiva, etiqueta "JUEGO") y esos tramos NO cuentan en la
comprobación final de subtítulos contra la transcripción del
streamer — una discrepancia larga (> 2,5 s) es música o el juego
hablando debajo, nunca se corta.

Aquí, sin Groq y sin Claude Code de pago ($0, local):
  * ``normalize_game_voice``: valida tramos [inicio, fin] (de un
    LLM, del usuario o de la detección automática): los recorta a
    la duración del clip, fusiona solapados y descarta los
    inválidos. Nunca inventa tramos: sin entrada, lista vacía.
  * ``detect_game_voice``: estimación LOCAL a partir de la
    transcripción del streamer + RMS por segundo: un tramo es voz
    del juego si el streamer calla >= 2 s SEGUIDOS mientras el
    audio sigue activo (RMS > -45 dBFS, hay sonido de juego) y el
    juego se mueve. Sin RMS no se estima nada a ciegas (un
    silencio sin audio activo puede ser solo una pausa). La salida
    va marcada ``estimated: True`` y sin texto: las palabras del
    juego NO se inventan; si hay transcripción de ese tramo, se
    subtitula con ella y con la etiqueta JUEGO, si no, sin
    subtítulo.
  * ``split_subtitle_blocks`` / ``words_outside_game_voice``:
    separan los bloques/palabras del streamer de los del juego,
    para el estilo propio y para excluir los tramos de juego de la
    comprobación final de subtítulos del streamer.

Regla del proyecto que se mantiene: layout juego arriba / cámara
abajo (split); el modo "game" solo pone el juego a pantalla
completa DURANTE el tramo de voz del juego, y vuelve a split al
terminar (ver render.apply_game_voice).
"""

# El streamer calla >= 2 s mientras suena el juego: como Uri
# (juego a pantalla completa si "calla >= 2 s mientras pasa algo
# con ruido y movimiento").
SILENCE_S = 2.0
# Segundo con audio activo: RMS por encima de esto hay sonido
# (mismo umbral que audio.ACTIVE_DBFS).
ACTIVE_DBFS = -45.0
# Tramos más cortos que esto son ruido, no voz del juego.
MIN_SPAN_S = 1.0
# Hueco máximo para fusionar dos tramos estimados vecinos.
MERGE_GAP_S = 0.5


def normalize_game_voice(spans, clip_duration, game_lang=None,
                         source="manual"):
    """Valida y normaliza tramos de voz del juego.

    ``spans``: lista de [inicio, fin] o {"start", "end"} (segundos
    relativos al clip). ``clip_duration``: duración del clip.
    Devuelve [{"start", "end", "lang", "source", "estimated"}]:
    recortados a [0, clip_duration], fusionados si se solapan o
    quedan a < 0,5 s, y sin tramos inválidos (< 1 s tras recortar
    o con fin <= inicio). ``source`` es "manual", "llm" o
    "estimada"; ``estimated`` es True solo para la detección
    automática. Sin tramos válidos devuelve [].
    """
    if clip_duration is None or clip_duration <= 0:
        return []
    cleaned = []
    for span in spans or []:
        try:
            if isinstance(span, dict):
                start, end = float(span["start"]), float(span["end"])
                lang = span.get("lang") or span.get("game_lang") or game_lang
                src = span.get("source") or source
            else:
                start, end = float(span[0]), float(span[1])
                lang, src = game_lang, source
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        start = max(0.0, start)
        end = min(float(clip_duration), end)
        if end - start < MIN_SPAN_S:
            continue
        cleaned.append({"start": round(start, 2), "end": round(end, 2),
                        "lang": lang, "source": src,
                        "estimated": src == "estimada"})
    cleaned.sort(key=lambda s: s["start"])
    merged = []
    for span in cleaned:
        if merged and span["start"] - merged[-1]["end"] <= MERGE_GAP_S:
            merged[-1]["end"] = max(merged[-1]["end"], span["end"])
        else:
            merged.append(dict(span))
    return merged


def _words_by_second(transcript, clip_duration):
    """Cobertura de palabras del streamer por segundo (0/1).

    Devuelve una lista de bool por segundo del clip: True si hay
    al menos una palabra del streamer cubriendo ese segundo.
    """
    n = max(0, int(clip_duration))
    covered = [False] * n
    for seg in (transcript or {}).get("segments", []):
        for word in seg.get("words", []):
            try:
                start, end = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            for sec in range(max(0, int(start)), min(n, int(end) + 1)):
                if start <= sec + 1 and end >= sec:
                    covered[sec] = True
    return covered


def detect_game_voice(transcript, audio_rms=None, clip_duration=None,
                      silence_s=SILENCE_S, active_dbfs=ACTIVE_DBFS,
                      game_lang=None, motion_per_second=None,
                      motion_threshold=3.0):
    """Estima tramos de voz del juego (local, sin inventar texto).

    ``transcript``: transcripción faster-whisper del clip (con
    palabras y tiempos). ``audio_rms``: [(segundo, dbfs), ...] de
    audio.rms_per_second (relativo al clip). Sin RMS no se estima
    nada: un silencio sin audio activo es solo una pausa del
    streamer, no voz del juego.

    Un tramo es voz del juego si el streamer calla >= ``silence_s``
    seguidos Y el audio sigue activo en esos segundos (hay sonido
    de juego: tráiler, escena, efectos) y, si se pasa
    ``motion_per_second`` (serie de motion.measure_motion), el juego
    se mueve en esos segundos (diferencia media > umbral). Devuelve
    la lista normalizada con source "estimada" (estimated True) y
    sin texto: las palabras del juego no se inventan.
    """
    if transcript is None or clip_duration is None or clip_duration <= 0:
        return []
    if not audio_rms:
        return []
    covered = _words_by_second(transcript, clip_duration)
    active = {}
    for sec, dbfs in audio_rms:
        try:
            active[int(sec)] = float(dbfs) > active_dbfs
        except (TypeError, ValueError):
            continue
    n = len(covered)
    spans = []
    run_start = None
    for sec in range(n + 1):
        is_game = (sec < n and not covered[sec]
                   and active.get(sec, False))
        if is_game and motion_per_second is not None:
            diff = (motion_per_second[sec]
                    if sec < len(motion_per_second) else 0.0)
            is_game = diff > motion_threshold
        if is_game and run_start is None:
            run_start = sec
        if not is_game and run_start is not None:
            if sec - run_start >= silence_s:
                spans.append([float(run_start), float(sec)])
            run_start = None
    return normalize_game_voice(spans, clip_duration,
                                game_lang=game_lang, source="estimada")


def split_subtitle_blocks(blocks, game_voice):
    """Separa bloques SRT [(start, end, text)] en streamer / juego.

    Un bloque es del juego si su centro cae dentro de un tramo de
    voz del juego. Devuelve (streamer_blocks, game_blocks): los del
    juego llevan el estilo propio (cursiva + etiqueta JUEGO, ver
    transcribe.srt_to_karaoke_ass) y NO cuentan en la comprobación
    final de subtítulos del streamer.
    """
    spans = normalize_game_voice(
        game_voice, max((end for _s, end, _t in blocks), default=0)
        or None) if blocks else []
    streamer, game = [], []
    for block in blocks:
        start, end, text = block
        mid = (start + end) / 2.0
        if any(s["start"] <= mid < s["end"] for s in spans):
            game.append(block)
        else:
            streamer.append(block)
    return streamer, game


def words_outside_game_voice(transcript, game_voice):
    """Palabras de la transcripción FUERA de los tramos de juego.

    Es la lista que usa la comprobación final de subtítulos: un
    subtítulo del streamer que nadie oye se marca REVISAR; lo que
    se oye dentro de un tramo de voz del juego no se comprueba
    contra la voz del streamer (es el juego hablando, como Uri:
    una discrepancia larga ahí es música o juego debajo, nunca un
    error del subtítulo del streamer).
    """
    spans = game_voice or []
    if spans and isinstance(spans[0], (list, tuple)):
        try:
            max_end = max(float(w["end"]) for seg in
                          (transcript or {}).get("segments", [])
                          for w in seg.get("words", []))
        except (KeyError, TypeError, ValueError):
            max_end = None
        spans = normalize_game_voice(spans, max_end) if max_end else []
    words = []
    for seg in (transcript or {}).get("segments", []):
        for word in seg.get("words", []):
            try:
                mid = (float(word["start"]) + float(word["end"])) / 2.0
            except (KeyError, TypeError, ValueError):
                continue
            if any(s["start"] <= mid < s["end"] for s in spans):
                continue
            words.append(word)
    return words
