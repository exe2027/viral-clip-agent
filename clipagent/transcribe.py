#!/usr/bin/env python3
r"""
subtitles.py — subtítulos para viral-clip-agent.

Adaptado de openshorts (faris-sait/openshorts, MIT), archivo
``subtitles.py``: transcripción con faster-whisper y quemado con ffmpeg.
Lo que se copió de openshorts:
  * transcribe_audio() con faster-whisper y word_timestamps=True.
  * generate_srt(): agrupa palabras en líneas cortas (máx. 20 caracteres,
    máx. 2 s por bloque), estilo TikTok.
  * burn_subtitles(): quema el SRT con el filtro ``subtitles`` de ffmpeg
    y un estilo ASS (blanco, negrita, borde negro, abajo).
Lo que se cambió respecto a openshorts:
  * Modelo ``tiny`` en vez de ``base``: en una CPU modesta es varias
    veces más rápido y para clips de 60 s la calidad es suficiente.
  * Idioma automático (``language=None``): detecta ES o EN solo, sin
    obligar a pasar el idioma; se informa el idioma detectado y su
    probabilidad.
  * Sin dependencia de cv2/OpenCV para la duración: se usa ffprobe,
    que el proyecto ya usa en clips_chat_v2.py.
  * Estilo adaptado a vertical 1080x1920: PlayResX=1080, PlayResY=1920,
    fontsize 48 (en openshorts era 16 con PlayRes por defecto 288, lo
    que en vertical queda impredecible), MarginV=140 para que el texto
    no choque con la barra de TikTok/Reels.
  * Sin Gemini, sin APIs de pago, sin Docker, sin S3: todo local.
Lo que NO se copió de openshorts (y por qué):
  * Detección de momentos con Gemini a partir de la transcripción:
    viral-clip-agent ya detecta los picos por el chat de Twitch
    (risas x2, múltiplo sobre la mediana), que es su señal principal.
  * Recorte 9:16 con seguimiento de cara (MediaPipe + YOLOv8): pesado
    (ultralytics, mediapipe, opencv, procesado fotograma a fotograma)
    y pensado para vídeos con una persona hablando a cámara. En
    gameplay de GTA 6 no hay cara que seguir y el HUD/minimapa es lo
    que importa; el proyecto ya tiene --crop center/left/right para
    eso. Queda documentado, no implementado.
  * Publicación automática (Upload-Post), actores UGC con IA, doblaje
    con ElevenLabs: de pago y fuera del alcance; el agente escribe y
    el usuario publica.

Estilos de subtítulo — v2.4 (idea de Shortia, backsoul/shortia,
Apache 2.0: plantillas de subtítulo elegibles). Tres estilos en
config.yaml (subtitle_styles), elegibles con --subtitle-style:
  * tiktok: blanco, negrita, borde negro, abajo (el de siempre).
  * highlight: amarillo con caja semitransparente detrás.
  * karaoke: palabra por palabra con cambio de color (ASS real con
    etiquetas \k): la palabra activa va en active_word_color
    (amarillo) y el resto en blanco, fuente grande, negrita, borde
    negro, centrado abajo. El SRT no lleva tiempos por palabra, así
    que cada bloque se reparte entre sus palabras en proporción a su
    longitud (ver srt_to_karaoke_ass). Es el estilo por defecto
    (config.yaml subtitles.default_style, arreglo 5, 2026-11-16:
    el tiktok se veía "muy sobrio").
Lo que se tomó de Shortia y lo que NO: ver README, sección Shortia.

Contexto en pantalla — arreglo 4 (2026-11-16):
en un clip solo cams/reacción, el streamer mira algo fuera de
cámara y el espectador no sabe QUÉ. El clip lleva un texto breve
QUEMADO arriba con el contexto sacado de la transcripción
(p. ej. "reaccionando al tráiler de GTA 3"), solo los primeros
segundos. NUNCA se mete imagen ni vídeo de lo que mira: solo
texto. La extracción vive en viral_package.extract_reaction_context
(medio + tema conocido, o None si no hay contexto claro) y el
quemado aquí: ``burn_subtitles(..., context_text=...)`` o
``burn_context_banner(...)`` solo. Sin ``context_text`` el quemado
de subtítulos es exactamente el de siempre: el banner es opcional
y no toca el filtro de subtítulos.

Política de traducción — v2.5 (regla del usuario, 2026-11-16):
  * Audio en español -> subtítulos SOLO en español, sin traducir
    (faster-whisper task="transcribe", language="es").
  * Audio en CUALQUIER otro idioma (rumano, inglés, etc.) ->
    subtítulos traducidos al inglés (task="translate", lo único
    que Whisper traduce).
  Se detecta el idioma con model.detect_language() y se decide con
  config.resolve_subtitle_task() según subtitle_policy de
  config.yaml (es=transcribe-only, others=translate-to-en).
  --subtitle-lang auto|es|en y --translate/--no-translate permiten
  anular la política desde la CLI.

Requisitos: el venv del proyecto (.venv/) con faster-whisper, y
FFmpeg en el PATH. El Python del sistema NO sirve: instalar
faster-whisper ahí falla por PEP 668 / typing_extensions de Debian.

VAD + anti-alucinación + puerta "sin voz" + escalado — v2.6
(bloque 5 del tribunal, 2026-11-16):
  * faster-whisper con vad_filter=True (Silero VAD, onnxruntime)
    y umbrales anti-alucinación: no_speech_threshold 0,6,
    compression_ratio_threshold 2,4, logprob_threshold -1,0,
    condition_on_previous_text=False (sin contexto previo no se
    encadenan alucinaciones).
  * Puerta "sin voz" (REGLA DEL USUARIO 2026-11-17 22:10: SIEMPRE
    subtítulos, COMPLETOS): si el clip tiene <5 palabras por cada
    60 s o la voz cubre <10% de la duración, NO se genera SRT ni se
    quema nada, PERO el clip queda BLOQUEADO para publicar
    (no_speech=true, needs_human_transcription=true,
    publish_blocked=true) hasta que el usuario lo transcriba de
    oído. Nunca se publica sin subtítulos y nunca se quema texto
    alucinado. El proceso termina con código 0 (el paso de
    transcripción en sí no falla), el bloqueo vive en el transcript
    y en subtitle_clip().
  * Escalado condicional tiny -> base: SOLO si salta un umbral
    objetivo (repetición >=3 veces, <8 palabras/min con voz,
    logprob medio bajo, idioma con prob. <0,5). Nunca base por
    defecto: tiny es varias veces más rápido en CPU.
  * Idioma por tramos: se detecta en trozos de 30 s y se vota por
    mayoría; el empate lo desempata el idioma mayoritario del chat
    (--chat-lang) y si no el fallback de config.yaml (idioma del
    streamer o del canal). Siempre se guardan source_language,
    probabilidad y task en el transcript.
  * Diccionario GTA 6 (~25 términos, config.yaml): va como
    initial_prompt a Whisper y como correcciones deterministas
    post-SRT (gta6_corrections): solo se toca lo que está en el
    mapa, por palabra/frase completa.

Uso:
  .venv/bin/python subtitles.py runs/2929669422/segment_7h12m.mp4
  .venv/bin/python subtitles.py clip.mp4 --srt clip.srt --burn clip_subs.mp4
  .venv/bin/python subtitles.py clip.mp4 --srt clip.srt --no-burn
  .venv/bin/python subtitles.py clip.mp4 --model tiny --lang en --fontsize 48
  .venv/bin/python subtitles.py clip.mp4 --subtitle-style highlight
  .venv/bin/python subtitles.py clip.mp4 --subtitle-style karaoke
  .venv/bin/python subtitles.py clip.mp4 --srt clip.srt --no-transcribe --burn out.mp4
    # quema un SRT ya existente sin re-transcribir (pruebas de estilo)
  .venv/bin/python subtitles.py clip.mp4 --srt clip.srt --no-transcribe --burn out.mp4 --context "reaccionando al tráiler de GTA 3"
    # lo mismo + banner de contexto arriba los primeros 5 s (arreglo 4)
  .venv/bin/python subtitles.py --self-test  # pruebas rápidas del banner de contexto
  .venv/bin/python subtitles.py clip.mp4 --subtitle-lang en   # fuerza salida en inglés
  .venv/bin/python subtitles.py clip.mp4 --no-translate        # nunca traducir
  .venv/bin/python subtitles.py clip.mp4 --chat-lang es        # desempate de idioma
  .venv/bin/python subtitles.py clip.mp4 --no-vad --no-escalate
"""

import argparse
import json
import os
import re
import subprocess
import sys



DEFAULT_MODEL = "tiny"
DEFAULT_MAX_CHARS = 20
DEFAULT_MAX_DURATION = 2.0
DEFAULT_FONTSIZE = 48

# VAD + anti-alucinación (v2.6, bloque 5 del tribunal). Valores por
# defecto = config.yaml (sección subtitles); la config manda.
DEFAULT_NO_SPEECH_THRESHOLD = 0.6
DEFAULT_COMPRESSION_RATIO_THRESHOLD = 2.4
DEFAULT_LOGPROB_THRESHOLD = -1.0
DEFAULT_NO_SPEECH_MIN_WORDS = 5    # por cada 60 s de clip
DEFAULT_NO_SPEECH_MIN_RATIO = 0.10  # cobertura mínima de habla
DEFAULT_ESCALATE_WPM = 8
DEFAULT_ESCALATE_LOGPROB = -0.8
DEFAULT_ESCALATE_LANG_PROB = 0.5
DEFAULT_ESCALATE_MAX_REPEATS = 3
DEFAULT_LANGUAGE_CHUNK_S = 30
SAMPLE_RATE = 16000

# REGLA DE SUBTÍTULOS (decisión del usuario 2026-11-17, geometría
# actualizada 2026-11-18 por el layout RDJavi): los subtítulos NUNCA
# tapan la cara del influencer / la webcam. Layout RDJavi sobre
# 1080x1920: streamer ARRIBA (zona de cámara 0-730 px), gameplay
# ABAJO (730-1770 px), banda inferior 1770-1920 px. Con Alignment=2
# (abajo, centrado) MarginV es la distancia desde el borde inferior:
# la posición de referencia es el borde inferior del texto en
# y≈1690 (87-89%, MarginV 230), una línea centrada, letra chica
# 30-35 px, blanca con contorno negro, sin fondo ni resaltado.
# El texto no puede subir a la zona de cámara (borde superior del
# texto < 730) ni bajar a la banda inferior (borde inferior > 1720).
FRAME_W, FRAME_H = 1080, 1920
SPLIT_GAME_H = 1320   # alto del panel de juego del split simple (fallback)
SPLIT_CAM_H = 600     # alto del panel de cámara del split simple (fallback)
# Geometría RDJavi (layout GTA 6 por defecto, 2026-11-18):
RDJAVI_CAM_BOTTOM = 730    # la cámara ocupa 0-730 px (fondo + webcam)
RDJAVI_GAME_BOTTOM = 1770  # el gameplay termina en 1770 px
SUBTITLE_REF_MARGIN_V = 230  # posición de referencia: texto en y≈1690
SUBTITLE_BOTTOM_RESERVE_PX = 50  # hueco entre el texto y la banda inferior
SUBTITLE_MIN_MARGIN_V = (FRAME_H - RDJAVI_GAME_BOTTOM
                         + SUBTITLE_BOTTOM_RESERVE_PX)  # 200
SUBTITLE_SAFE_GAP_PX = 80


def _text_height(fontsize):
    """Alto aproximado de una línea de subtítulo (1,25x el tamaño)."""
    return int(round(float(fontsize) * 1.25))


def subtitles_overlap_webcam(margin_v, fontsize=32, frame_h=FRAME_H,
                             cam_bottom=RDJAVI_CAM_BOTTOM,
                             game_bottom=RDJAVI_GAME_BOTTOM):
    """True si con ese MarginV el texto invadiría la zona de la cámara
    (arriba) o la banda inferior (abajo).

    Alignment=2: el borde inferior del texto queda en frame_h-margin_v
    y el superior, una línea más arriba. Con MarginV 230 (referencia
    RDJavi) el borde queda en 1690 (parte baja del gameplay, 87-89%);
    con 140 queda en 1780 (dentro de la banda inferior); con 1400
    queda en 520, con el borde superior dentro de la cámara.
    """
    text_bottom = frame_h - margin_v
    text_top = text_bottom - _text_height(fontsize)
    if text_top < cam_bottom:
        return True
    return text_bottom > game_bottom - SUBTITLE_BOTTOM_RESERVE_PX


def effective_subtitle_margin_v(margin_v, config=None, fontsize=32):
    """MarginV efectivo: dentro del rango seguro [mínimo, máximo].

    Mínimo 200: el borde inferior del texto no baja de y=1720 (no
    invade la banda inferior). Máximo: el borde superior del texto
    no sube a la zona de cámara (730 px). Un estilo viejo con
    MarginV 140 se corrige solo a 200 en burn_subtitles(); uno con
    MarginV 1400 baja al máximo. La posición de referencia es 230.
    """
    minimum = SUBTITLE_MIN_MARGIN_V
    if config is not None:
        try:
            from .config import subtitle_min_margin_v
            minimum = subtitle_min_margin_v(config)
        except Exception:
            pass
    maximum = FRAME_H - RDJAVI_CAM_BOTTOM - _text_height(fontsize)
    return min(max(int(margin_v), int(minimum)), int(maximum))


def load_user_corrected_transcript(run_dir, clip_stem):
    """Transcripción corregida por el usuario para un clip, si existe.

    Archivo: <run_dir>/clips/<clip_stem>.transcript_corrected.json
    (campos: text, segments [{start_*, end_*, text}], corrected_by,
    corrected_at). Manda sobre Whisper: si el usuario corrigió las
    palabras, se queman ESAS y nunca las alucinaciones del modelo
    (ej. 2026-11-17: "saloncóra / salocorda" -> "nooo noooo no
    cometas esa locura,"). Devuelve el dict o None si no existe.
    """
    for base in (run_dir, os.path.join(run_dir, "clips")):
        path = os.path.join(base, clip_stem + ".transcript_corrected.json")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    return None


def user_corrected_srt(corrected, srt_path, clip_start_offset=0.0):
    """Escribe un SRT con la transcripción corregida por el usuario.

    Los segmentos del JSON vienen en segundos absolutos del VOD y en
    coordenadas del clip (start_v3_clip_s / start_old_clip_s); se usan
    las coordenadas *_clip_s que correspondan al clip que se quema
    (por defecto las v3 si existen, si no las old_clip). El offset
    desplaza los tiempos si el SRT se quema sobre otro recorte.
    """
    segments = corrected.get("segments") or []
    lines = []
    for i, seg in enumerate(segments, 1):
        start = None
        end = None
        for key in ("start_v3_clip_s", "start_old_clip_s", "start_s"):
            if seg.get(key) is not None:
                start = float(seg[key])
                break
        for key in ("end_v3_clip_s", "end_old_clip_s", "end_s"):
            if seg.get(key) is not None:
                end = float(seg[key])
                break
        if start is None or end is None:
            start = float(seg.get("start_vod_s", 0))
            end = float(seg.get("end_vod_s", start + 1))
        start += clip_start_offset
        end += clip_start_offset

        def _fmt(t):
            ms = int(round(t * 1000))
            return (f"{ms // 3600000:02d}:{(ms // 60000) % 60:02d}:"
                    f"{(ms // 1000) % 60:02d},{ms % 1000:03d}")
        lines.append(f"{i}\n{_fmt(start)} --> {_fmt(end)}\n{seg['text']}\n")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return srt_path


def detect_language_chunks(model, audio, chunk_s=DEFAULT_LANGUAGE_CHUNK_S):
    """Detecta el idioma por tramos de chunk_s segundos.

    Devuelve votos [{"start", "end", "language", "probability"}].
    Un solo detect_language() sobre 60 s de audio ruidoso de juego
    es poco fiable (en v2.5 dio 'en' con prob. 0,13 en audio
    rumano); votar por tramos estabiliza la decisión.
    """
    chunk_len = int(chunk_s * SAMPLE_RATE)
    votes = []
    for i in range(0, len(audio), chunk_len):
        chunk = audio[i:i + chunk_len]
        if len(chunk) < SAMPLE_RATE:  # <1 s: no vota
            continue
        lang, prob, _ = model.detect_language(chunk)
        votes.append({
            "start": round(i / SAMPLE_RATE, 1),
            "end": round(min(len(audio), i + chunk_len) / SAMPLE_RATE, 1),
            "language": lang,
            "probability": round(float(prob), 2),
        })
    return votes


def majority_language(votes, fallback="es", chat_lang=None):
    """Idioma por voto mayoritario entre los tramos.

    Empate: desempata el idioma mayoritario del chat (si está entre
    los empatados); si no, gana el idioma empatado con mayor
    probabilidad media. Sin votos, se usa el fallback de config
    (idioma del streamer o del canal). Devuelve (language,
    probability, tie_break), donde tie_break es None, "chat",
    "probability" o "fallback". La probabilidad es la media de los
    tramos ganadores.
    """
    if not votes:
        return fallback, 0.0, "fallback"
    counts = {}
    for v in votes:
        counts.setdefault(v["language"], []).append(v["probability"])
    best_count = max(len(p) for p in counts.values())
    tied = [lang for lang, p in counts.items() if len(p) == best_count]
    tie_break = None
    if len(tied) > 1:
        if chat_lang and chat_lang in tied:
            winner, tie_break = chat_lang, "chat"
        else:
            # Desempata por probabilidad media: gana el idioma con
            # los tramos más seguros (p. ej. ro 0,94 frente a te
            # 0,33 en un clip casi sin voz), no por orden alfabético.
            winner = max(
                tied, key=lambda lang: sum(counts[lang]) / len(counts[lang]))
            tie_break = "probability"
    else:
        winner = tied[0]
    probs = counts[winner]
    return winner, round(sum(probs) / len(probs), 2), tie_break


def build_initial_prompt(terms):
    """initial_prompt para faster-whisper con el diccionario GTA 6.

    Whisper usa el prompt como contexto: los nombres propios del
    juego (Lucia, Leonida, Vice City) salen bien escritos en vez
    de aproximaciones ("Leonila", "vice city" en minúscula...).
    """
    terms = [t for t in (terms or []) if t]
    if not terms:
        return None
    return "Clip de GTA 6: " + ", ".join(terms) + "."


def apply_gta6_corrections(text, corrections):
    """Correcciones deterministas post-SRT (diccionario GTA 6).

    Reemplazo por palabra/frase completa, insensible a mayúsculas
    (re.IGNORECASE + \\b). Devuelve (texto_corregido, n_cambios).
    Solo toca lo que está en el mapa de config.yaml: si Whisper
    escribió "leonida" en minúscula, queda "Leonida"; si escribió
    otra cosa, no se inventa nada.
    """
    if not corrections or not text:
        return text, 0
    n = 0
    for wrong, right in corrections.items():
        pattern = re.compile(r"\b" + re.escape(str(wrong)) + r"\b",
                             re.IGNORECASE)
        text, k = pattern.subn(str(right), text)
        n += k
    return text, n


def speech_stats(transcript, duration):
    """Palabras y cobertura de habla de una transcripción.

    Devuelve (word_count, speech_seconds, speech_ratio), donde
    speech_ratio es la fracción de la duración cubierta por
    palabras (0,0–1,0). Con VAD activo, el ruido de juego no
    cuenta como habla.
    """
    words = [w for s in transcript.get("segments", [])
             for w in s.get("words", [])]
    speech_s = sum(max(0.0, w["end"] - w["start"]) for w in words)
    ratio = (speech_s / duration) if duration else 0.0
    return len(words), speech_s, ratio


def check_escalation(transcript, duration, lang_prob, sub_cfg):
    """¿La pasada tiny es sospechosa? Devuelve lista de motivos.

    Umbrales objetivos (config.yaml, sección subtitles):
      * repetición: la misma palabra >= N veces seguidas, o el mismo
        texto de segmento >= N veces (bucle típico de alucinación).
      * pocas palabras/min con voz: < N palabras por minuto (y hay
        voz: word_count > 0; el silencio total lo coge la puerta
        "sin voz", no el escalado).
      * logprob medio bajo: media de avg_logprob de los segmentos.
      * idioma con probabilidad baja (< 0,5).
    Si la lista no está vacía, transcribe_audio repite con base.
    Escalar no descarta nada: solo re-transcribe con un modelo
    mejor y se queda con ese resultado.
    """
    from collections import Counter

    sub_cfg = sub_cfg or {}
    reasons = []
    words = [w["word"].strip().lower().strip(".,!?¡¿\"'")
             for s in transcript.get("segments", [])
             for w in s.get("words", [])]
    max_repeats = sub_cfg.get("escalate_max_repeats",
                              DEFAULT_ESCALATE_MAX_REPEATS)
    run, run_word, worst = 1, None, 0
    prev = None
    for w in words:
        if w and w == prev:
            run += 1
            if run > worst:
                worst, run_word = run, w
        else:
            run = 1
        prev = w
    if worst >= max_repeats:
        reasons.append(f"repetición: '{run_word}' x{worst} seguidas")
    seg_texts = [s.get("text", "").strip().lower()
                 for s in transcript.get("segments", [])
                 if s.get("text", "").strip()]
    for text, count in Counter(seg_texts).most_common(1):
        if count >= max_repeats:
            reasons.append(f"segmento repetido x{count}: '{text[:40]}'")
    word_count, _, _ = speech_stats(transcript, duration)
    wpm = word_count / (duration / 60.0) if duration else 0.0
    min_wpm = sub_cfg.get("escalate_min_words_per_min",
                          DEFAULT_ESCALATE_WPM)
    if 0 < word_count and wpm < min_wpm:
        reasons.append(f"pocas palabras/min: {wpm:.1f} (< {min_wpm})")
    logprobs = [s["avg_logprob"] for s in transcript.get("segments", [])
                if "avg_logprob" in s]
    if logprobs:
        mean_lp = sum(logprobs) / len(logprobs)
        if mean_lp < sub_cfg.get("escalate_logprob",
                                 DEFAULT_ESCALATE_LOGPROB):
            reasons.append(f"logprob medio bajo: {mean_lp:.2f}")
    if word_count > 0 and lang_prob < sub_cfg.get(
            "escalate_lang_prob", DEFAULT_ESCALATE_LANG_PROB):
        reasons.append(f"idioma poco fiable: prob. {lang_prob:.2f}")
    return reasons


def _transcribe_with_model(model, audio, detected, task, sub_cfg, vad=True):
    """Una pasada de transcripción con VAD + anti-alucinación.

    Parámetros faster-whisper (v2.6):
      * vad_filter=True: Silero VAD descarta el ruido de juego sin
        voz antes de transcribir (requiere onnxruntime en el venv).
      * no_speech_threshold / compression_ratio_threshold /
        logprob_threshold: segmentos sospechosos se descartan en
        vez de inventar palabras.
      * condition_on_previous_text=False: sin contexto previo no se
        encadenan alucinaciones de un segmento al siguiente.
      * initial_prompt: diccionario GTA 6 de config.yaml.
    Devuelve el transcript (dict) con segmentos, palabras,
    avg_logprob por segmento y correcciones GTA 6 aplicadas al
    texto de cada segmento.
    """
    sub_cfg = sub_cfg or {}
    initial_prompt = build_initial_prompt(sub_cfg.get("gta6_terms"))
    kwargs = dict(
        language=detected, task=task, word_timestamps=True,
        condition_on_previous_text=False,
        no_speech_threshold=float(sub_cfg.get(
            "no_speech_threshold", DEFAULT_NO_SPEECH_THRESHOLD)),
        compression_ratio_threshold=float(sub_cfg.get(
            "compression_ratio_threshold",
            DEFAULT_COMPRESSION_RATIO_THRESHOLD)),
        log_prob_threshold=float(sub_cfg.get(
            "logprob_threshold", DEFAULT_LOGPROB_THRESHOLD)),
        initial_prompt=initial_prompt,
    )
    if vad:
        kwargs["vad_filter"] = True
        kwargs["vad_parameters"] = dict(min_silence_duration_ms=500)
    segments, info = model.transcribe(audio, **kwargs)
    corrections = sub_cfg.get("gta6_corrections") or {}
    transcript = {"segments": [], "language": info.language}
    n_corrections = 0
    for segment in segments:
        text, k = apply_gta6_corrections(segment.text.strip(), corrections)
        n_corrections += k
        seg_data = {
            "start": segment.start,
            "end": segment.end,
            "text": text,
            "avg_logprob": round(float(segment.avg_logprob), 3),
            "words": [],
        }
        if segment.words:
            for word in segment.words:
                seg_data["words"].append(
                    {"word": word.word.strip(),
                     "start": word.start, "end": word.end}
                )
        transcript["segments"].append(seg_data)
    transcript["corrections_applied"] = n_corrections
    return transcript


def transcribe_audio(video_path, model_name=DEFAULT_MODEL, language=None,
                     subtitle_lang="auto", translate=None, policy=None,
                     sub_cfg=None, cfg=None, chat_lang=None,
                     vad=True, escalate=True):
    """Transcribe (o traduce) con faster-whisper. Devuelve (transcript, info).

    transcript tiene el mismo formato que openshorts: {"segments": [...],
    "language": ...}, cada segmento con "words" [{word, start, end}],
    más "source_language", "task" y "subtitle_language" (v2.5).

    Política de traducción (regla del usuario, 2026-11-16):
    español -> solo transcribe, sin traducir; cualquier otro idioma ->
    task="translate" a inglés. Se detecta el idioma con
    model.detect_language() (salvo que se fuerce con --lang) y se
    decide con config.resolve_subtitle_task().

    Novedades v2.6 (bloque 5 del tribunal):
      * Idioma por tramos: detect_language() en trozos de 30 s y
        voto por mayoría; empate -> idioma del chat (--chat-lang o
        config subtitles.chat_language), si no fallback de config
        (subtitles.streamer_language o channel.language). Se guardan
        los votos en transcript["language_votes"].
      * VAD + anti-alucinación en cada pasada (ver
        _transcribe_with_model).
      * Escalado condicional tiny -> base: si la pasada tiny dispara
        un umbral objetivo (ver check_escalation), se repite con
        base y se guarda el motivo en
        transcript["escalation_reasons"]. Nunca base por defecto.
      * Puerta "sin voz" (regla 2026-11-17 22:10): si el resultado
        final tiene <5 palabras por cada 60 s o la voz cubre <10%
        de la duración, transcript["no_speech"]=True,
        transcript["needs_human_transcription"]=True y
        transcript["publish_blocked"]=True,
        transcript["audio_note"]="solo gameplay/música": main() no
        genera SRT ni quema nada y termina con código 0, pero el
        clip queda bloqueado para publicar hasta revisión humana.
      * Siempre se guardan source_language, probabilidad y task.

    Nota: no se pasa el vídeo (ni un archivo de audio) directamente a
    faster-whisper, porque su decodificación con PyAV falla con av 19
    (``metadata_errors`` ya no existe en av.open). Se extrae primero el
    audio a WAV 16 kHz mono con ffmpeg —que el proyecto ya exige—,
    se lee con el módulo ``wave`` de la stdlib y se pasa a
    ``model.transcribe`` como array numpy, que es la vía que evita
    decode_audio/PyAV por completo.
    """
    import tempfile
    import wave

    import numpy as np
    from faster_whisper import WhisperModel

    sub_cfg = dict(sub_cfg or {})
    cfg = cfg or {}
    try:
        from .config import get_subtitle_policy, resolve_subtitle_task
        policy = policy or get_subtitle_policy(cfg)
    except Exception:
        resolve_subtitle_task = None
        policy = policy or {"es": "transcribe-only",
                            "others": "translate-to-en"}

    # Fallback de idioma: streamer > canal (channel.language) > "es".
    channel_lang = ((cfg.get("channel") or {}).get("language")
                    or sub_cfg.get("fallback_language") or "es")
    fallback_lang = sub_cfg.get("streamer_language") or channel_lang
    chat_lang = chat_lang or sub_cfg.get("chat_language") or None
    vad = bool(sub_cfg.get("vad", True)) if vad else False
    escalate = bool(sub_cfg.get("escalate", True)) if escalate else False

    print(f"Transcribiendo audio de: {video_path} (modelo {model_name})")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", video_path,
             "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav_path],
            check=True, capture_output=True, text=True,
        )
        with wave.open(wav_path, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        duration = len(audio) / SAMPLE_RATE
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        if language:
            detected, lang_prob = language, 1.0
            votes, tie_break = [], None
        else:
            chunk_s = int(sub_cfg.get("language_chunk_s",
                                      DEFAULT_LANGUAGE_CHUNK_S))
            votes = detect_language_chunks(model, audio, chunk_s=chunk_s)
            detected, lang_prob, tie_break = majority_language(
                votes, fallback=fallback_lang, chat_lang=chat_lang)
        if resolve_subtitle_task is not None:
            task, sub_lang = resolve_subtitle_task(
                detected, subtitle_lang=subtitle_lang,
                translate=translate, policy=policy)
        else:  # sin config.py: misma política, hardcodeada
            if (subtitle_lang or "auto") == "es" or detected == "es":
                task, sub_lang = "transcribe", "es"
            elif translate is False:
                task, sub_lang = "transcribe", detected
            else:
                task, sub_lang = "translate", "en"
        if task == "translate":
            print(f"Idioma detectado: {detected} (prob. {lang_prob:.2f}) "
                  f"-> traduciendo subtítulos a inglés (task=translate)")
        else:
            print(f"Idioma detectado: {detected} (prob. {lang_prob:.2f}) "
                  f"-> solo transcribe, sin traducir (task=transcribe)")
        if tie_break:
            print(f"Idioma por voto empatado: desempate por {tie_break} "
                  f"-> {detected}")
        transcript = _transcribe_with_model(
            model, audio, detected, task, sub_cfg, vad=vad)
        transcript.update({
            "source_language": detected,
            "source_language_probability": round(float(lang_prob), 2),
            "language_votes": votes,
            "language_tie_break": tie_break,
            "task": task,
            "subtitle_language": sub_lang,
            "model": model_name,
            "vad": vad,
        })
        word_count, speech_s, ratio = speech_stats(transcript, duration)
        transcript["word_count"] = word_count
        transcript["speech_seconds"] = round(speech_s, 1)
        transcript["speech_ratio"] = round(ratio, 3)
        transcript["words_per_min"] = round(
            word_count / (duration / 60.0), 1) if duration else 0.0
        logprobs = [s["avg_logprob"] for s in transcript["segments"]
                    if "avg_logprob" in s]
        transcript["avg_logprob"] = round(
            sum(logprobs) / len(logprobs), 3) if logprobs else None
        print(
            f"Transcripción completa ({model_name}). Idioma fuente: "
            f"{detected} (probabilidad {lang_prob:.2f}) | tarea: {task} | "
            f"subtítulos en: {sub_lang} | palabras: {word_count} "
            f"({transcript['words_per_min']}/min) | habla: "
            f"{speech_s:.1f}s de {duration:.0f}s "
            f"({ratio * 100:.0f}%) | logprob medio: "
            f"{transcript['avg_logprob']}"
        )
        # Escalado condicional tiny -> base (v2.6): solo si salta un
        # umbral objetivo y el modelo actual es tiny. La puerta
        # "sin voz" se evalúa DESPUÉS, sobre el resultado final.
        escalation_reasons = []
        if escalate and model_name == "tiny":
            escalation_reasons = check_escalation(
                transcript, duration, lang_prob, sub_cfg)
        if escalation_reasons:
            print(f"Escalado tiny -> base: {'; '.join(escalation_reasons)}")
            base_model = WhisperModel("base", device="cpu",
                                      compute_type="int8")
            if not language:
                votes = detect_language_chunks(
                    base_model, audio,
                    chunk_s=int(sub_cfg.get(
                        "language_chunk_s", DEFAULT_LANGUAGE_CHUNK_S)))
                detected, lang_prob, tie_break = majority_language(
                    votes, fallback=fallback_lang, chat_lang=chat_lang)
                if resolve_subtitle_task is not None:
                    task, sub_lang = resolve_subtitle_task(
                        detected, subtitle_lang=subtitle_lang,
                        translate=translate, policy=policy)
            base_transcript = _transcribe_with_model(
                base_model, audio, detected, task, sub_cfg, vad=vad)
            base_transcript.update({
                "source_language": detected,
                "source_language_probability": round(float(lang_prob), 2),
                "language_votes": votes,
                "language_tie_break": tie_break,
                "task": task,
                "subtitle_language": sub_lang,
                "model": "base",
                "escalated_from": "tiny",
                "escalation_reasons": escalation_reasons,
                "vad": vad,
            })
            word_count, speech_s, ratio = speech_stats(
                base_transcript, duration)
            base_transcript["word_count"] = word_count
            base_transcript["speech_seconds"] = round(speech_s, 1)
            base_transcript["speech_ratio"] = round(ratio, 3)
            base_transcript["words_per_min"] = round(
                word_count / (duration / 60.0), 1) if duration else 0.0
            logprobs = [s["avg_logprob"]
                        for s in base_transcript["segments"]
                        if "avg_logprob" in s]
            base_transcript["avg_logprob"] = round(
                sum(logprobs) / len(logprobs), 3) if logprobs else None
            print(
                f"Transcripción con base. Palabras: {word_count} "
                f"({base_transcript['words_per_min']}/min) | habla: "
                f"{speech_s:.1f}s ({ratio * 100:.0f}%) | logprob medio: "
                f"{base_transcript['avg_logprob']}"
            )
            transcript, info_lang = base_transcript, detected
        else:
            info_lang = detected
        # Puerta "sin voz" (v2.6 + regla 2026-11-17 22:10: SIEMPRE
        # subtítulos): <5 palabras por cada 60 s o habla <10% de la
        # duración -> no hay voz que subtitular, pero el clip queda
        # BLOQUEADO para publicar hasta que el usuario lo transcriba
        # de oído. No es un error del paso, es un bloqueo de salida.
        min_words = max(3, round(sub_cfg.get(
            "no_speech_min_words", DEFAULT_NO_SPEECH_MIN_WORDS)
            * duration / 60.0))
        min_ratio = float(sub_cfg.get(
            "no_speech_min_ratio", DEFAULT_NO_SPEECH_MIN_RATIO))
        word_count = transcript["word_count"]
        ratio = transcript["speech_ratio"]
        if word_count < min_words or ratio < min_ratio:
            transcript["no_speech"] = True
            transcript["needs_human_transcription"] = True
            transcript["publish_blocked"] = True
            transcript["audio_note"] = "solo gameplay/música"
            print(f"SIN VOZ: {word_count} palabras "
                  f"(mín. {min_words}) | habla {ratio * 100:.0f}% "
                  f"(mín. {min_ratio * 100:.0f}%) -> sin SRT, sin quemar; "
                  f"clip BLOQUEADO para publicar: necesita "
                  f"transcripción humana.")
        else:
            transcript["no_speech"] = False
            transcript["needs_human_transcription"] = False
            transcript["publish_blocked"] = False
            transcript["audio_note"] = "con voz"
        info = type("Info", (), {"language": info_lang})()
        return transcript, info
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass


def format_srt_block(index, start, end, text):
    def fmt(seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    return f"{index}\n{fmt(start)} --> {fmt(end)}\n{text}\n\n"


def generate_srt(transcript, clip_start, clip_end, output_path,
                 max_chars=DEFAULT_MAX_CHARS, max_duration=DEFAULT_MAX_DURATION,
                 corrections=None):
    """Genera un SRT agrupando palabras en líneas cortas (openshorts).

    v2.6: aplica las correcciones del diccionario GTA 6
    (``corrections``) al texto de cada bloque. Devuelve
    (generado, n_correcciones): generado es False si no hay
    palabras que subtitular.
    """
    words = []
    for segment in transcript.get("segments", []):
        for word_info in segment.get("words", []):
            if word_info["end"] > clip_start and word_info["start"] < clip_end:
                words.append(word_info)
    if not words:
        return False, 0

    srt_content = ""
    index = 1
    n_corrections = 0
    current_block = []
    block_start = None
    for word in words:
        start = max(0, word["start"] - clip_start)
        end = max(0, word["end"] - clip_start)
        if not current_block:
            current_block.append(word)
            block_start = start
        else:
            current_text_len = sum(len(w["word"]) + 1 for w in current_block)
            duration = end - block_start
            if current_text_len + len(word["word"]) > max_chars or duration > max_duration:
                block_end = current_block[-1]["end"] - clip_start
                # task="translate" a veces da palabras con end == start:
                # un bloque de duración 0 no se ve al quemar.
                block_end = max(block_end, block_start + 0.3)
                text = " ".join(w["word"] for w in current_block).strip()
                text, k = apply_gta6_corrections(text, corrections)
                n_corrections += k
                srt_content += format_srt_block(index, block_start, block_end, text)
                index += 1
                current_block = [word]
                block_start = start
            else:
                current_block.append(word)
    if current_block:
        block_end = current_block[-1]["end"] - clip_start
        block_end = max(block_end, block_start + 0.3)
        text = " ".join(w["word"] for w in current_block).strip()
        text, k = apply_gta6_corrections(text, corrections)
        n_corrections += k
        srt_content += format_srt_block(index, block_start, block_end, text)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(srt_content)
    return True, n_corrections


def hex_to_ass_color(hex_color, opacity=1.0):
    """Convierte #RRGGBB a ASS &HAABBGGRR. opacity 0.0=transparente."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        hex_color = "FFFFFF"
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    alpha = round((1.0 - opacity) * 255)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


_SRT_TIME_RE = re.compile(
    r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def parse_srt(srt_path):
    """Lee un SRT y devuelve [(start, end, text)] en segundos.

    Los bloques se separan por líneas en blanco; cada bloque tiene
    índice, línea de tiempos y una o más líneas de texto (se unen
    con espacios: el karaoke trabaja palabra por palabra).
    """
    with open(srt_path, encoding="utf-8") as f:
        content = f.read()
    blocks = []
    for chunk in re.split(r"\n\s*\n", content.strip()):
        lines = [ln.strip() for ln in chunk.strip().splitlines()
                 if ln.strip()]
        if len(lines) < 2:
            continue
        m = _SRT_TIME_RE.search(lines[1])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
        text = " ".join(lines[2:])
        if text:
            blocks.append((start, end, text))
    return blocks


def _ass_time(seconds):
    """Segundos -> tiempo ASS H:MM:SS.cc."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs == 100:  # redondeo al límite del segundo
        s += 1
        cs = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def srt_to_karaoke_ass(blocks, style, font_name="DejaVu Sans",
                       offset=0.0, max_end=None, game_voice=None,
                       alignment=2, margin_v=None):
    """Convierte bloques SRT en un ASS karaoke palabra por palabra.

    ``alignment`` / ``margin_v`` colocan el subtitulo: por defecto
    Alignment 2 (abajo-centrado) con el margin_v del estilo, que es
    la parte baja del gameplay. La plantilla RDJavi pasa
    alignment=8, margin_v=608 (al pie de la camara, banda 595..749),
    para no solapar los subtitulos propios de GTA 6 (regla del
    usuario 2026-11-18 19:02). Si no se pasa margin_v, usa el del
    estilo.

    Cada bloque se reparte entre sus palabras en proporción a su
    longitud (palabra + espacio): la etiqueta ``\\k`` de cada palabra
    dura esos centisegundos. libass pinta lo ya "cantado" con
    PrimaryColour (palabra activa, active_word_color) y lo que falta
    con SecondaryColour (resto, font_color). No necesita los tiempos
    por palabra de Whisper: el SRT ya trae el bloque y el reparto
    proporcional basta para el efecto viral. ``offset`` desplaza los
    tiempos (p. ej. al quemar sobre un recorte del clip) y
    ``max_end`` recorta los bloques que se salen del recorte.

    ``game_voice`` (idea de Uri, 2026-11-17): tramos de voz del
    juego (ver game_voice.py). Los bloques que caen dentro salen
    con el estilo "Game" (cursiva, palabra activa azul #7FE3FF,
    como Uri) y con la etiqueta "JUEGO:" delante: es el subtítulo
    propio del juego, distinto del del streamer. Esos bloques NO
    cuentan en la comprobación final de subtítulos del streamer
    (ver game_voice.words_outside_game_voice).
    """
    from .game_voice import normalize_game_voice

    font_color = style.get("font_color", "#FFFFFF")
    active_color = style.get("active_word_color") or "#FFFF00"
    border_color = style.get("border_color", "#000000")
    border_width = style.get("border_width", 3)
    fontsize = style.get("fontsize", 64)
    margin_v = style.get("margin_v", 140) if margin_v is None else margin_v
    bold = -1 if style.get("bold", True) else 0
    game_spans = normalize_game_voice(
        game_voice, max_end) if game_voice else []
    game_style = (
        f"Style: Game,{font_name},{fontsize},"
        f"{hex_to_ass_color('#7FE3FF', 1.0)},"
        f"{hex_to_ass_color(font_color, 1.0)},"
        f"{hex_to_ass_color(border_color, 1.0)},"
        f"{hex_to_ass_color('#000000', 0.0)},"
        f"0,1,0,0,100,100,0,0,1,{border_width},0,2,10,10,{margin_v},1\n"
    ) if game_spans else ""
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Karaoke,{font_name},{fontsize},"
        f"{hex_to_ass_color(active_color, 1.0)},"
        f"{hex_to_ass_color(font_color, 1.0)},"
        f"{hex_to_ass_color(border_color, 1.0)},"
        f"{hex_to_ass_color('#000000', 0.0)},"
        f"{bold},0,0,0,100,100,0,0,1,{border_width},0,{alignment},10,10,{margin_v},1\n"
        + game_style +
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )
    lines = [header]
    for start, end, text in blocks:
        start -= offset
        end -= offset
        if max_end is not None and start >= max_end:
            continue
        if end <= 0:
            continue
        start = max(0.0, start)
        if max_end is not None:
            end = min(end, max_end)
        if end <= start:
            continue
        # Voz del juego: estilo Game + etiqueta JUEGO (ver docstring).
        is_game = any(s["start"] <= (start + end) / 2.0 < s["end"]
                      for s in game_spans)
        block_style = "Game" if is_game else "Karaoke"
        if is_game and not text.strip().startswith("JUEGO:"):
            text = "JUEGO: " + text.strip()
        # Las llaves serían etiquetas de anulación en ASS: se
        # neutralizan para que el texto se vea tal cual.
        words = [w.replace("{", "(").replace("}", ")")
                 for w in text.split()]
        if not words:
            continue
        total_cs = max(len(words), int(round((end - start) * 100)))
        weights = [len(w) + 1 for w in words]
        total_w = sum(weights)
        durs = [max(1, int(round(total_cs * w / total_w)))
                for w in weights]
        # Ajuste de redondeo en la última palabra: la suma debe ser
        # exactamente total_cs para que el bloque no se corte antes.
        durs[-1] += total_cs - sum(durs)
        if durs[-1] < 1:
            durs[-1] = 1
        karaoke_text = "".join(
            f"{{\\k{d}}}{w} " for d, w in zip(durs, words)).rstrip()
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{block_style},,"
            f"0,0,0,,{karaoke_text}\n"
        )
    return "".join(lines)


# ---------------------------------------------------------------------------
# Arreglo 4 (2026-11-16) — contexto en pantalla para clips de reacción
# ---------------------------------------------------------------------------

CONTEXT_BANNER_FONTFILE = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
CONTEXT_BANNER_DEFAULT_DURATION = 5.0  # s: solo el arranque del clip
CONTEXT_BANNER_FONTSIZE = 40
CONTEXT_BANNER_MARGIN_TOP = 120  # px desde arriba (vertical 1080x1920)


def _drawtext_escape(text):
    """Escapa texto para el filtro drawtext de ffmpeg (: ' \\ %)."""
    return (str(text).replace("\\", "\\\\").replace(":", "\\:")
            .replace("'", "\\'").replace("%", "\\%"))


def _context_banner_font():
    """fontfile si existe DejaVu Bold, si no font=DejaVu Sans."""
    if os.path.isfile(CONTEXT_BANNER_FONTFILE):
        return f"fontfile={CONTEXT_BANNER_FONTFILE}"
    return "font='DejaVu Sans'"


def context_banner_filter(context_text,
                          duration_s=CONTEXT_BANNER_DEFAULT_DURATION,
                          fontsize=CONTEXT_BANNER_FONTSIZE,
                          margin_top=CONTEXT_BANNER_MARGIN_TOP):
    """Filtro drawtext del banner de contexto (arreglo 4).

    Texto blanco en negrita con caja semitransparente, centrado
    arriba (los subtítulos van abajo, no se pisan), solo los
    primeros ``duration_s`` segundos. Devuelve el fragmento de
    filtergraph para encadenar tras el filtro de subtítulos.
    """
    safe = _drawtext_escape(context_text)
    return (
        f"drawtext={_context_banner_font()}:text='{safe}':"
        f"fontsize={fontsize}:fontcolor=white:borderw=2:bordercolor=black:"
        f"box=1:boxcolor=black@0.6:boxborderw=16:"
        f"x=(w-text_w)/2:y={margin_top}:enable='between(t,0,{duration_s})'"
    )


def burn_context_banner(video_path, context_text, output_path,
                        duration_s=CONTEXT_BANNER_DEFAULT_DURATION):
    """Quema SOLO el banner de contexto, sin subtítulos.

    Para clips de reacción que no llevan subtítulos (o que ya los
    llevan quemados): una pasada de ffmpeg solo con drawtext.
    Sin context_text no hace nada (devuelve False).
    """
    if not context_text:
        return False
    vf = context_banner_filter(context_text, duration_s=duration_s)
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", vf,
        "-c:a", "copy",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        output_path,
    ]
    print(f"Quemando contexto en pantalla en {output_path}: {context_text!r}")
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(f"Error de FFmpeg: {result.stderr.decode()[-2000:]}", file=sys.stderr)
        raise RuntimeError("FFmpeg falló al quemar el contexto en pantalla")
    return True


def _self_test_context_banner():
    """Pruebas rápidas del banner de contexto (arreglo 4, sin ffmpeg)."""
    failures = []

    def check(name, cond, extra=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {extra}" if extra else ""))
        if not cond:
            failures.append(name)

    print("== banner de contexto (subtitles.py) ==")
    f = context_banner_filter("reaccionando al tráiler de GTA 3")
    check("filtro drawtext con texto, caja y enable 0-5 s",
          "drawtext=" in f and "reaccionando al tráiler de GTA 3" in f
          and "box=1" in f and "enable='between(t,0,5.0)'" in f, f[:130])
    f2 = context_banner_filter("a:b 'c' 100% \\ d", duration_s=8)
    check("escape de : ' % \\ para drawtext",
          "a\\:b" in f2 and "100\\%" in f2, f2[:170])
    try:
        from .metadata import extract_reaction_context
        ctx = extract_reaction_context(
            {"transcript_text": "GT A3. Todo el mundo jugando a jugar legacy "
                                "y yo viendo un trailer, vale, de 1998."},
            visible="cams-only")
        check("extracción desde texto: tráiler de GTA 3",
              ctx is not None and ctx["text"] == "reaccionando al tráiler de GTA 3",
              str(ctx))
        check("sin contexto claro -> None",
              extract_reaction_context(
                  {"transcript_text": "no, no, no, mira mira"},
                  visible="cams-only") is None)
    except ImportError as e:
        check("viral_package importable", False, str(e))
    if failures:
        print(f"\n{len(failures)} FALLOS:", failures)
        return 1
    print("\nSelf-test contexto OK.")
    return 0


def burn_subtitles(video_path, srt_path, output_path, fontsize=DEFAULT_FONTSIZE,
                   font_name="DejaVu Sans", font_color="#FFFFFF",
                   border_color="#000000", border_width=3, margin_v=140,
                   style=None, config=None, offset=0.0,
                   context_text=None,
                   context_duration=CONTEXT_BANNER_DEFAULT_DURATION,
                   game_voice=None):
    """Quema el SRT en el vídeo con ffmpeg (estilo openshorts, adaptado
    a vertical 1080x1920: PlayRes explícito y fuente grande).

    v2.4: ``style`` es el nombre de un estilo de config.yaml
    (tiktok | highlight | karaoke, ver config.py). Si se pasa, sus
    valores mandan sobre los argumentos sueltos. ``box=True``
    (highlight) usa BorderStyle=3: caja semitransparente detrás del
    texto. Flags de plataforma: yuv420p + faststart.

    v2.7 (arreglo 5, 2026-11-16): ``style="karaoke"`` ya no quema el
    SRT tal cual: genera un ASS con etiquetas ``\\k`` palabra por
    palabra (ver srt_to_karaoke_ass) y quema ese ASS, sin
    force_style (el ASS ya lleva el estilo). ``offset`` desplaza los
    tiempos del SRT (p. ej. al quemar sobre un recorte del clip).

    Arreglo 4 (2026-11-16): ``context_text`` quema además el banner
    de contexto arriba los primeros ``context_duration`` segundos
    (ver context_banner_filter), también con karaoke. Sin él, el
    quemado es exactamente el de siempre.

    Voz del juego (idea de Uri, 2026-11-17): ``game_voice`` (tramos
    de game_voice.py) marca los bloques del juego en karaoke con
    el estilo "Game" (cursiva, azul #7FE3FF, etiqueta "JUEGO:").
    Sin ``game_voice`` el quemado es exactamente el de siempre.
    """
    if style:
        cfg = config or {}
        try:
            from .config import get_subtitle_style, load_config
            cfg = cfg or load_config()
            st = get_subtitle_style(cfg, style)
        except Exception:
            st = {}
        if st:
            font_color = st.get("font_color", font_color)
            border_color = st.get("border_color", border_color)
            border_width = st.get("border_width", border_width)
            fontsize = st.get("fontsize", fontsize)
            margin_v = st.get("margin_v", margin_v)
            box = bool(st.get("box", False))
            bold = bool(st.get("bold", True))
            active_word_color = st.get("active_word_color")
        else:
            box, bold, active_word_color = False, True, None
    else:
        box, bold, active_word_color = False, True, None
    # REGLA 2026-11-17, geometría RDJavi 2026-11-18: el subtítulo
    # nunca tapa la cara del influencer / la webcam (arriba, 0-730)
    # ni baja a la banda inferior (borde inferior > 1720). El
    # MarginV efectivo queda dentro del rango seguro [200, máximo]:
    # un estilo viejo con MarginV 140 sube solo a 200 y uno con
    # MarginV 1400 baja al máximo. La posición de referencia es 230
    # (texto en y≈1690, parte baja del gameplay). Vale para karaoke
    # (ASS) y para SRT (force_style), porque el clamp va antes de
    # las dos ramas.
    try:
        _margin_cfg = cfg if style else config
    except NameError:  # sin style no existe cfg: manda config o 200
        _margin_cfg = config
    _safe_mv = effective_subtitle_margin_v(margin_v, _margin_cfg,
                                           fontsize=fontsize)
    if margin_v != _safe_mv:
        print(f"MarginV {margin_v} -> {_safe_mv}: el subtítulo no puede "
              f"tapar la webcam ni bajar a la banda (regla del usuario)")
        margin_v = _safe_mv
    if (style or "").lower() == "karaoke":
        # Karaoke real: SRT -> ASS con \k por palabra. El ASS se
        # escribe a un temporal y se borra tras quemar.
        import tempfile

        ass_style = {
            "font_color": font_color,
            "active_word_color": active_word_color,
            "border_color": border_color,
            "border_width": border_width,
            "fontsize": fontsize,
            "margin_v": margin_v,
            "bold": bold,
        }
        duration = get_duration(video_path)
        ass_text = srt_to_karaoke_ass(
            parse_srt(srt_path), ass_style, font_name=font_name,
            offset=offset, max_end=duration, game_voice=game_voice)
        with tempfile.NamedTemporaryFile(
                suffix=".ass", delete=False,
                mode="w", encoding="utf-8") as tmp:
            tmp.write(ass_text)
            ass_path = tmp.name
        try:
            safe_ass_path = ass_path.replace("\\", "/").replace(":", "\\:")
            vf = f"subtitles='{safe_ass_path}'"
            if context_text:  # arreglo 4: banner también con karaoke
                vf += "," + context_banner_filter(
                    context_text, duration_s=context_duration)
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-vf", vf,
                "-c:a", "copy",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                output_path,
            ]
            print(f"Quemando subtítulos karaoke en {output_path}")
            result = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE)
            if result.returncode != 0:
                print(f"Error de FFmpeg: {result.stderr.decode()[-2000:]}",
                      file=sys.stderr)
                raise RuntimeError("FFmpeg falló al quemar subtítulos")
            return True
        finally:
            try:
                os.unlink(ass_path)
            except OSError:
                pass
    safe_srt_path = srt_path.replace("\\", "/").replace(":", "\\:")
    style_string = (
        f"PlayResX=1080,PlayResY=1920,"
        f"Alignment=2,"
        f"Fontname={font_name},"
        f"Fontsize={fontsize},"
        f"PrimaryColour={hex_to_ass_color(font_color, 1.0)},"
        f"OutlineColour={hex_to_ass_color(border_color, 1.0)},"
        f"BackColour={hex_to_ass_color('#000000', 0.5 if box else 0.0)},"
        f"BorderStyle={3 if box else 1},"
        f"Outline={border_width},"
        f"Shadow=0,"
        f"MarginV={margin_v},"
        f"Bold={1 if bold else 0}"
    )
    vf = f"subtitles='{safe_srt_path}':force_style='{style_string}'"
    if context_text:  # arreglo 4: banner de contexto, sin tocar el SRT
        vf += "," + context_banner_filter(context_text,
                                         duration_s=context_duration)
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", vf,
        "-c:a", "copy",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        output_path,
    ]
    print(f"Quemando subtítulos en {output_path}")
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(f"Error de FFmpeg: {result.stderr.decode()[-2000:]}", file=sys.stderr)
        raise RuntimeError("FFmpeg falló al quemar subtítulos")
    return True


def get_duration(video_path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, check=True,
        )
        return float(r.stdout.strip())
    except Exception:
        return None


def main() -> int:
    p = argparse.ArgumentParser(
        description="Transcribe un clip con faster-whisper (tiny, local) y "
                    "quema subtítulos estilo TikTok en el vídeo vertical."
    )
    p.add_argument("video", nargs="?",
                   help="Clip de entrada (p. ej. segment_7h12m.mp4)")
    p.add_argument("--self-test", action="store_true",
                   help="Pruebas rápidas del banner de contexto (arreglo 4, "
                        "sin ffmpeg ni transcripción) y sale.")
    p.add_argument("--srt", help="Ruta del SRT (default: <video>.srt)")
    p.add_argument("--burn", help="Vídeo de salida con subtítulos quemados")
    p.add_argument("--no-burn", action="store_true",
                   help="Solo genera el SRT, sin quemarlo")
    p.add_argument("--transcript", help="Guarda la transcripción completa en JSON")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help="Modelo faster-whisper (default: tiny)")
    p.add_argument("--lang", default=None,
                   help="Idioma fuente forzado: es, en... (default: auto)")
    p.add_argument("--subtitle-lang", default=None, choices=["auto", "es", "en"],
                   help="Idioma de salida de los subtítulos: auto (aplica "
                        "la política: es=transcribe, resto=traduce a en), "
                        "es o en (default: config.yaml subtitles.subtitle_lang)")
    translate_group = p.add_mutually_exclusive_group()
    translate_group.add_argument("--translate", dest="translate",
                                 action="store_true", default=None,
                                 help="Fuerza traducir a inglés lo que no "
                                      "sea español")
    translate_group.add_argument("--no-translate", dest="translate",
                                 action="store_false", default=None,
                                 help="Nunca traducir: transcribe siempre "
                                      "en el idioma original")
    p.add_argument("--max-chars", type=int, default=None,
                   help="Máx. caracteres por bloque (default: según estilo)")
    p.add_argument("--max-duration", type=float, default=None,
                   help="Máx. duración por bloque en s (default: según estilo)")
    p.add_argument("--fontsize", type=int, default=None,
                   help="Tamaño de fuente (default: según estilo)")
    p.add_argument("--subtitle-style", default=None,
                   help="Estilo de subtítulo: tiktok, highlight, karaoke "
                        "(default: config.yaml subtitles.default_style)")
    p.add_argument("--no-transcribe", action="store_true",
                   help="No transcribe: quema el SRT dado con --srt tal "
                        "cual, sin re-transcribir (el SRT debe existir). "
                        "Para probar un estilo sobre un SRT ya corregido.")
    p.add_argument("--config", default=None,
                   help="Ruta a config.yaml (default: busca en el proyecto)")
    p.add_argument("--offset", type=float, default=0.0,
                   help="Desplazamiento del SRT respecto al vídeo (s)")
    p.add_argument("--chat-lang", default=None,
                   help="Idioma mayoritario del chat (es, en...): "
                        "desempata el voto de idioma por tramos "
                        "(default: config.yaml subtitles.chat_language)")
    p.add_argument("--no-vad", action="store_true",
                   help="Desactiva el VAD (vad_filter=False)")
    p.add_argument("--no-escalate", action="store_true",
                   help="Desactiva el escalado condicional tiny -> base")
    # Arreglo 4: banner de contexto en clips de reacción.
    p.add_argument("--context", default=None,
                   help="Texto de contexto quemado arriba los primeros segundos "
                        "(p. ej. 'reaccionando al tráiler de GTA 3'). "
                        "Sin él no se quema banner.")
    p.add_argument("--context-from-transcript", default=None,
                   help="JSON de transcripción (transcript.json): extrae el "
                        "contexto con viral_package.extract_reaction_context. "
                        "Si no hay contexto claro, no se quema nada.")
    p.add_argument("--context-visible", default="cams-only",
                   choices=["cams-only", "gameplay", "cams+gameplay", "unknown"],
                   help="Lo visible del clip (default: cams-only): el contexto "
                        "solo se extrae en clips solo cams.")
    p.add_argument("--context-duration", type=float,
                   default=CONTEXT_BANNER_DEFAULT_DURATION,
                   help="Segundos que dura el banner de contexto (default: 5).")
    args = p.parse_args()

    if args.self_test:
        return _self_test_context_banner()

    if not args.video:
        p.error("falta el vídeo de entrada")
    if not os.path.isfile(args.video):
        print(f"Error: no existe el vídeo: {args.video}", file=sys.stderr)
        return 1

    base, _ = os.path.splitext(args.video)
    srt_path = args.srt or f"{base}.srt"
    # Estilo de subtítulo (v2.4, config.yaml): sus max_chars,
    # max_duration y fontsize mandan salvo que se pasen por CLI.
    cfg = {}
    style_name = args.subtitle_style
    try:
        from .config import get_subtitle_style, load_config
        cfg = load_config(args.config)
        st = get_subtitle_style(cfg, style_name)
        style_name = st.get("name", "tiktok")
    except Exception:
        st = {}
    max_chars = args.max_chars if args.max_chars is not None else st.get("max_chars", DEFAULT_MAX_CHARS)
    max_duration = args.max_duration if args.max_duration is not None else st.get("max_duration", DEFAULT_MAX_DURATION)
    fontsize = args.fontsize if args.fontsize is not None else st.get("fontsize", DEFAULT_FONTSIZE)
    print(f"Estilo de subtítulo: {style_name} "
          f"(max_chars={max_chars}, max_duration={max_duration}s, fontsize={fontsize})")
    # Política de traducción (v2.5, config.yaml subtitle_policy):
    # es -> transcribe-only; resto -> translate-to-en. La CLI manda
    # sobre la config: --subtitle-lang y --translate/--no-translate.
    try:
        from .config import get_subtitle_policy
        policy = get_subtitle_policy(cfg)
    except Exception:
        policy = {"es": "transcribe-only", "others": "translate-to-en"}
    sub_cfg = cfg.get("subtitles") or {}
    subtitle_lang = args.subtitle_lang or sub_cfg.get("subtitle_lang", "auto")
    translate = args.translate
    if translate is None:
        translate = sub_cfg.get("translate", True)
    # Arreglo 4: texto de contexto quemado (explícito o extraído del
    # transcript). Si no hay contexto claro, context_text es None y
    # no se quema nada. Va antes de --no-transcribe: ambas ramas lo usan.
    context_text = args.context
    if args.context_from_transcript:
        try:
            from .metadata import extract_reaction_context
            with open(args.context_from_transcript, encoding="utf-8") as fh:
                transcript_data = json.load(fh)
            ctx = extract_reaction_context(transcript=transcript_data,
                                           visible=args.context_visible)
            context_text = ctx["text"] if ctx else None
            if context_text:
                print(f"Contexto extraído: {context_text!r}")
            else:
                print("Contexto: sin contexto claro en la transcripción, "
                      "no se quema nada.")
        except ImportError:
            print("Contexto: no se pudo importar viral_package, "
                  "se omite el banner.", file=sys.stderr)
        except (OSError, json.JSONDecodeError) as e:
            print(f"Contexto: no se pudo leer {args.context_from_transcript}: "
                  f"{e}; se omite el banner.", file=sys.stderr)
    # --no-transcribe (arreglo 5): el CLI re-transcribía siempre y
    # pisaba el SRT dado con --srt. Con este flag se quema el SRT
    # existente tal cual, con el estilo por defecto de config.yaml.
    if args.no_transcribe:
        if not os.path.isfile(srt_path):
            print(f"Error: --no-transcribe necesita un SRT existente: "
                  f"{srt_path}", file=sys.stderr)
            return 1
        print(f"Sin transcribir: usando SRT existente {srt_path} "
              f"(estilo {style_name}, offset {args.offset}s)")
        if not args.no_burn:
            out = args.burn or f"{base}_subs.mp4"
            burn_subtitles(args.video, srt_path, out, fontsize=fontsize,
                           style=style_name, config=cfg,
                           offset=args.offset,
                           context_text=context_text,
                           context_duration=args.context_duration)
            print(f"Clip con subtítulos: {out}")
        else:
            print("SRT existente, sin quemar (--no-burn).")
        return 0
    print(f"Política de subtítulos: {policy} | subtitle_lang={subtitle_lang} "
          f"| translate={translate}")
    transcript, info = transcribe_audio(
        args.video, args.model, args.lang,
        subtitle_lang=subtitle_lang, translate=translate, policy=policy,
        sub_cfg=sub_cfg, cfg=cfg, chat_lang=args.chat_lang,
        vad=not args.no_vad, escalate=not args.no_escalate)
    if args.transcript:
        with open(args.transcript, "w", encoding="utf-8") as f:
            json.dump(transcript, f, ensure_ascii=False, indent=2)

    # Puerta "sin voz" (v2.6 + regla 2026-11-17 22:10): no hay SRT
    # ni quemado, y NO es un error del paso, pero el clip queda
    # BLOQUEADO para publicar hasta transcripción humana.
    if transcript.get("no_speech"):
        print(f"Clip sin voz ({transcript.get('audio_note')}): "
              f"sin SRT ni quemado. BLOQUEADO para publicar: "
              f"necesita transcripción humana. Código 0.")
        return 0

    duration = get_duration(args.video)
    clip_end = duration if duration else 1e9
    ok, n_corrections = generate_srt(
        transcript, args.offset, clip_end, srt_path,
        max_chars=max_chars, max_duration=max_duration,
        corrections=sub_cfg.get("gta6_corrections"))
    if not ok:
        print("Sin palabras transcritas: no se generó SRT.", file=sys.stderr)
        return 1
    print(f"SRT: {srt_path} (correcciones GTA 6 aplicadas: {n_corrections})")

    if not args.no_burn:
        out = args.burn or f"{base}_subs.mp4"
        burn_subtitles(args.video, srt_path, out, fontsize=fontsize, style=style_name, config=cfg, offset=args.offset,
                       context_text=context_text, context_duration=args.context_duration)
        print(f"Clip con subtítulos: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
