#!/usr/bin/env python3
"""
viral_score.py — score de viralidad 0-100 + señales MVP.

Score de viralidad (0-100), distinto del score de chat de
``clips_chat_v2.py`` (puntos por ventana con baseline local y
anti-spam): ese mide cuánto explotó el chat; este mide cuánto
puede funcionar el clip como pieza viral. No se mezclan: el
manifiesto lleva los dos, cada uno con su nombre.

Fórmula (cada componente 0-10, pesos que suman 10):

    total = Hook x2.0 + Claridad x1.5 + Novedad x1.5 + ChatNorm x1.5
          + Audio x1.0 + Loop x1.5 + Packaging x1.0

  1. Hook (humano): ¿el inicio agarra en 0-3 s?
  2. Claridad (humano): ¿se entiende el momento sin contexto?
  3. Novedad (humano): ¿pasa algo que no se ve todos los días?
  4. ChatNorm (automático): pico de chat normalizado, solo aquí.
  5. Audio (automático): dinámica del audio del segmento.
  6. Loop (humano): ¿dan ganas de verlo otra vez / compartirlo?
  7. Packaging (humano): título + miniatura + caption del paquete.

Bandas: <50 descartar, 50-69 revisar, 70-84 prioritario,
85-100 urgente.

Componentes humanos: si no hay entrada humana, quedan en None
("pendiente") y el total usa una estimación conservadora de 5,0
(punto medio, nunca un 7-8 inventado) y se marca BETA/estimado.
Nada de lo que no se midió se presenta como medido.

Componente 4 (chat), normalizado por múltiplo pico/mediana por
ventana de 10 s:

    <2x:   0-2     2-3,9x:  3-4     4-6,9x:  5-6
    7-9,9x: 7-8    >=10x:  9-10

con topes (respetando el anti-spam de clips_chat_v2.py):
  * (risas + hype) / mensajes < 10%  -> máx. 4/10
  * 0 risas y 0 hype                 -> máx. 3/10
  * usuarios únicos / mensajes < 0,4 -> máx. 5/10

Filtro previo (para encolar): solo top-5 del VOD, múltiplo >= 3x
la mediana, y no solo emote-spam.

Señales MVP (``compute_signals``): todo lo que se puede medir en
local y gratis a partir de detail/chat + audio + transcripción
(si existe). Lo que no se puede medir devuelve None, nunca
inventado.

Uso:
    from .detect import score_clip, band
    r = score_clip(detail, chat_multiple=8.5, rank=1,
                   human={"hook": 8, "claridad": 9, "novedad": 7,
                          "loop": 8, "packaging": 7},
                   audio={"stats": {...}, "words": 77})
    r["total"], r["band"], r["beta"], r["components"]

Sin dependencias externas, sin credenciales, sin red. Todo local.
"""

import os
import statistics

# Pesos de la fórmula. Suman 10,0: con cada componente en 0-10 el
# total queda en 0-100 sin más escalado.
WEIGHTS = {
    "hook": 2.0,
    "claridad": 1.5,
    "novedad": 1.5,
    "chat": 1.5,
    "audio": 1.0,
    "loop": 1.5,
    "packaging": 1.0,
}

HUMAN_COMPONENTS = ("hook", "claridad", "novedad", "loop", "packaging")

# Estimación conservadora para un componente sin entrada humana:
# punto medio de la escala (5,0), marcado como estimado. Nunca un
# 7-8 por defecto: un total con estimados es BETA por definición.
ESTIMATED_DEFAULT = 5.0

# Filtro previo: múltiplo mínimo para encolar.
MIN_MULTIPLE = 3.0
MAX_RANK = 5

# Topes del componente de chat.
CAP_LOW_SIGNAL = 4.0    # (risas+hype)/msgs < 10%
CAP_NO_SIGNAL = 3.0     # 0 risas y 0 hype
CAP_LOW_UNIQUE = 5.0    # usuarios únicos / msgs < 0,4

# Audio: umbrales calibrados con los segmentos ya medidos
# (audio.py): plano = std < 9 dB y >= 45% de segundos estables.
AUDIO_FLAT_STD_DB = 9.0
AUDIO_FLAT_STEADY_FRAC = 0.45
AUDIO_NO_VOICE_CAP = 4.0   # sin palabras transcritas, máx. 4/10


def band(total: float) -> str:
    """Banda de prioridad del total 0-100."""
    if total < 50:
        return "descartar"
    if total < 70:
        return "revisar"
    if total < 85:
        return "prioritario"
    return "urgente"


# ---------------------------------------------------------------------------
# Componente 4: chat normalizado
# ---------------------------------------------------------------------------

def _chat_base(multiple: float) -> float:
    """Base 0-10 por múltiplo pico/mediana (interpolación lineal
    dentro de cada banda)."""
    m = max(0.0, float(multiple or 0.0))
    if m < 2.0:
        return m  # 0-2
    if m < 4.0:
        return 3.0 + (m - 2.0) / 2.0  # 3-4
    if m < 7.0:
        return 5.0 + (m - 4.0) / 3.0  # 5-6
    if m < 10.0:
        return 7.0 + (m - 7.0) / 3.0  # 7-8
    return 9.0 + min(1.0, (m - 10.0) / 10.0)  # 9-10


def chat_component(detail, multiple: float):
    """Componente 4 (chat) 0-10 con topes anti-spam.

    ``detail`` es el detail[ventana] de clips_chat_v2.score_windows
    (msgs, risas, hype, users, emote_ratio, rep_ratio,
    penalty_factor, motivo_penalizacion). Devuelve
    (valor, razones, topes_aplicados).
    """
    base = _chat_base(multiple)
    razones = [f"múltiplo {multiple:.1f}x sobre la mediana -> base {base:.1f}"]
    topes = []
    value = base
    msgs = (detail or {}).get("msgs") or 0
    risas = (detail or {}).get("risas") or 0
    hype = (detail or {}).get("hype") or 0
    users = (detail or {}).get("users") or 0
    if msgs <= 0:
        return 0.0, ["sin mensajes en la ventana"], ["sin mensajes"]
    laugh_hype = (risas + hype) / msgs
    if risas == 0 and hype == 0:
        value = min(value, CAP_NO_SIGNAL)
        topes.append(f"0 risas y 0 hype -> máx. {CAP_NO_SIGNAL:.0f}/10")
    elif laugh_hype < 0.10:
        value = min(value, CAP_LOW_SIGNAL)
        topes.append(
            f"(risas+hype)/msgs {laugh_hype:.0%} < 10% -> máx. {CAP_LOW_SIGNAL:.0f}/10")
    unique_ratio = users / msgs if msgs else 0.0
    if unique_ratio < 0.4:
        value = min(value, CAP_LOW_UNIQUE)
        topes.append(
            f"usuarios únicos/msgs {unique_ratio:.2f} < 0,4 -> máx. {CAP_LOW_UNIQUE:.0f}/10")
    if (detail or {}).get("motivo_penalizacion"):
        razones.append(
            f"anti-spam: {', '.join((detail or {}).get('motivo_penalizacion') or [])} "
            f"(factor x{(detail or {}).get('penalty_factor', 1.0)})")
    return round(value, 2), razones, topes


def filter_passed(detail, multiple: float, rank=None):
    """Filtro previo para encolar: top-5 del VOD, múltiplo >= 3x,
    no solo emote-spam. Devuelve (pasa, motivos)."""
    motivos = []
    ok = True
    if rank is not None and rank > MAX_RANK:
        ok = False
        motivos.append(f"puesto {rank} del VOD (solo top-{MAX_RANK})")
    if (multiple or 0.0) < MIN_MULTIPLE:
        ok = False
        motivos.append(f"múltiplo {multiple:.1f}x < {MIN_MULTIPLE:.0f}x la mediana")
    msgs = (detail or {}).get("msgs") or 0
    if msgs <= 0:
        ok = False
        motivos.append("sin mensajes en la ventana")
    elif ((detail or {}).get("emote_ratio") or 0.0) > 0.6 \
            and ((detail or {}).get("risas") or 0) + ((detail or {}).get("hype") or 0) == 0:
        ok = False
        motivos.append("solo emote-spam (emote_ratio > 60% sin risas ni hype)")
    if ok:
        motivos.append("pasa el filtro previo")
    return ok, motivos


# ---------------------------------------------------------------------------
# Componente 5: audio
# ---------------------------------------------------------------------------

def audio_component(audio=None):
    """Componente 5 (audio) 0-10 desde audio.py.

    ``audio`` puede ser:
      * None -> (None, "sin audio medido") — queda pendiente.
      * dict con "stats" (audio_stats) y "words" (nº de palabras
        transcritas), o el dict completo de audio_verdict.
      * dict con "std_db" / "steady_frac" / "words" directos.

    Regla: audio plano (std < 9 dB y >= 45% estables) -> 2/10;
    si no, por std: >=15 -> 9, 11-15 -> 7-8, 9-11 -> 5-6, <9 -> 4.
    Sin palabras transcritas (0 palabras con transcripción hecha),
    máx. 4/10: un pico de chat con el audio sin voz es sospechoso.
    7.2: picos de energía (range_db = máx - mín del RMS por
    segundo): rango >= 25 dB -> +1; 15-25 dB -> +0,5. Las risas y
    los gritos se miden por el chat (detail risas/hype) y por la
    transcripción (palabras): el audio aporta los picos de energía.
    """
    if audio is None:
        return None, "sin audio medido (pendiente)"
    stats = audio.get("stats") or audio
    std = stats.get("std_db")
    steady = stats.get("steady_frac")
    words = audio.get("words")
    if std is None:
        return None, "sin estadísticas de audio (pendiente)"
    if std < AUDIO_FLAT_STD_DB and steady is not None \
            and steady >= AUDIO_FLAT_STEADY_FRAC:
        value = 2.0
        reason = (f"audio plano: std {std} dB < {AUDIO_FLAT_STD_DB} dB "
                  f"y {steady:.0%} de segundos estables")
    elif std >= 15.0:
        value = 9.0
        reason = f"audio muy dinámico: std {std} dB"
    elif std >= 11.0:
        value = 7.0 + (std - 11.0) / 4.0
        reason = f"audio dinámico: std {std} dB"
    elif std >= 9.0:
        value = 5.0 + (std - 9.0) / 2.0
        reason = f"audio con algo de dinámica: std {std} dB"
    else:
        value = 4.0
        reason = f"audio poco dinámico: std {std} dB"
    # 7.2: picos de energía (gritos, reacciones, subidones).
    range_db = stats.get("range_db")
    if range_db is not None and range_db >= 25.0:
        value += 1.0
        reason += f"; picos de energía (rango {range_db} dB) (+1)"
    elif range_db is not None and range_db >= 15.0:
        value += 0.5
        reason += f"; picos de energía (rango {range_db} dB) (+0,5)"
    if words is not None and words == 0:
        value = min(value, AUDIO_NO_VOICE_CAP)
        reason += f"; 0 palabras transcritas -> máx. {AUDIO_NO_VOICE_CAP:.0f}/10"
    elif words is not None and words >= 50:
        value = min(10.0, value + 1.0)
        reason += f"; {words} palabras transcritas (+1)"
    return round(min(value, 10.0), 2), reason


# 7.2: peso del audio, configurable y mayor en Kick (en Kick el
# chat es menos fiable como señal y el audio manda más). Por
# defecto 1,0 / 1,5: con esos valores el total no cambia.
AUDIO_WEIGHT_DEFAULT = 1.0
AUDIO_WEIGHT_KICK_DEFAULT = 1.5


def audio_weight_for(platform, cfg=None):
    """Peso del componente audio para una plataforma (7.2).

    Lee config.yaml `scoring.audio_weight` y
    `scoring.audio_weight_kick` (por defecto 1,0 y 1,5).
    """
    scoring = (cfg or {}).get("scoring") or {}
    base = scoring.get("audio_weight", AUDIO_WEIGHT_DEFAULT)
    kick = scoring.get("audio_weight_kick", AUDIO_WEIGHT_KICK_DEFAULT)
    try:
        base = float(base)
    except (TypeError, ValueError):
        base = AUDIO_WEIGHT_DEFAULT
    try:
        kick = float(kick)
    except (TypeError, ValueError):
        kick = AUDIO_WEIGHT_KICK_DEFAULT
    return kick if (platform or "").lower() == "kick" else base


# ---------------------------------------------------------------------------
# Score completo
# ---------------------------------------------------------------------------

def _human_value(human, name):
    """Valor humano 0-10 o (None, motivo). Acepta número directo
    o dict {"value": x, "reason": "..."}."""
    if not human or name not in human:
        return None, "sin entrada humana (pendiente)"
    raw = human[name]
    if isinstance(raw, dict):
        value = raw.get("value")
        reason = raw.get("reason") or "entrada humana"
    else:
        value, reason = raw, "entrada humana"
    if value is None:
        return None, "sin entrada humana (pendiente)"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None, "entrada humana no válida (pendiente)"
    if not 0.0 <= value <= 10.0:
        return None, f"entrada humana fuera de 0-10 ({value}) (pendiente)"
    return value, reason


def score_clip(detail, chat_multiple, rank=None, human=None, audio=None,
               audio_weight=None, platform=None):
    """Score de viralidad completo de un clip.

    detail: detail[ventana] de score_windows (o None si no hay chat).
    chat_multiple: múltiplo pico/mediana (score / mediana).
    rank: puesto del pico en el VOD (1 = el mejor).
    human: dict {hook, claridad, novedad, loop, packaging} con
        valores 0-10 (número o {"value", "reason"}).
    audio: ver audio_component (None = pendiente).
    audio_weight / platform (7.2): peso del componente audio; por
        defecto 1,0 (o el de audio_weight_for si se pasa platform).
        El total se renormaliza para seguir en 0-100: con los pesos
        por defecto el resultado es idéntico al de siempre.

    Devuelve dict con total, components (los 7, cada uno con value,
    estimated y reason), band, beta (True si algún componente es
    estimado), chat_multiple y filter_passed (+ motivos).
    """
    components = {}
    estimated_any = False

    for name in HUMAN_COMPONENTS:
        value, reason = _human_value(human, name)
        if value is None:
            components[name] = {
                "value": ESTIMATED_DEFAULT, "estimated": True,
                "reason": reason + f"; estimación conservadora {ESTIMATED_DEFAULT}",
            }
            estimated_any = True
        else:
            components[name] = {
                "value": round(value, 2), "estimated": False, "reason": reason,
            }

    if detail is None or chat_multiple is None:
        components["chat"] = {
            "value": ESTIMATED_DEFAULT, "estimated": True,
            "reason": "sin datos de chat (pendiente); "
                      f"estimación conservadora {ESTIMATED_DEFAULT}",
        }
        estimated_any = True
        chat_reasons, chat_topes = ["sin datos de chat"], []
    else:
        value, chat_reasons, chat_topes = chat_component(detail, chat_multiple)
        components["chat"] = {
            "value": value, "estimated": False,
            "reason": "; ".join(chat_reasons + chat_topes),
        }

    audio_value, audio_reason = audio_component(audio)
    if audio_value is None:
        components["audio"] = {
            "value": ESTIMATED_DEFAULT, "estimated": True,
            "reason": audio_reason + f"; estimación conservadora {ESTIMATED_DEFAULT}",
        }
        estimated_any = True
    else:
        components["audio"] = {
            "value": audio_value, "estimated": False, "reason": audio_reason,
        }

    # 7.2: peso del audio configurable (mayor en Kick). El total se
    # renormaliza a 0-100: con el peso por defecto (1,0) la suma de
    # pesos es 10 y el resultado es idéntico al de siempre.
    weights = dict(WEIGHTS)
    if audio_weight is None and platform is not None:
        audio_weight = audio_weight_for(platform)
    if audio_weight is not None:
        weights["audio"] = float(audio_weight)
    total = round(
        sum(components[name]["value"] * weights[name] for name in weights)
        * 10.0 / sum(weights.values()), 1)
    pasa, motivos = filter_passed(detail, chat_multiple, rank=rank)

    return {
        "total": total,
        "components": components,
        "band": band(total),
        "beta": estimated_any,
        "beta_note": ("Total BETA: algún componente es una estimación "
                      "conservadora (5,0), no una medición. Pasar la "
                      "entrada humana para el total real."
                      if estimated_any else None),
        "chat_multiple": round(float(chat_multiple), 2) if chat_multiple is not None else None,
        "filter_passed": pasa,
        "filter_reasons": motivos,
        "audio_weight": weights["audio"],
    }


# ---------------------------------------------------------------------------
# Señales MVP (todo local y gratis; None si no se puede medir)
# ---------------------------------------------------------------------------

def _transcript_words(transcript):
    """Palabras con timestamps de una transcripción (subtitles.py):
    [{word, start, end}, ...]."""
    words = []
    for seg in (transcript or {}).get("segments", []):
        for w in seg.get("words", []):
            if w.get("start") is not None:
                words.append({"word": w.get("word", ""),
                              "start": float(w["start"]),
                              "end": float(w.get("end") or w["start"])})
    return sorted(words, key=lambda w: w["start"])


def compute_signals(detail=None, chat_scores=None, audio_rms=None,
                    transcript=None, video_path=None, peak_ts=None,
                    clip_start=None, clip_duration=None, lang="es",
                    heatmap_peaks=None, motion=None, game_voice=None,
                    virality_total=None):
    """Señales MVP para un candidato, todo medido en local.

    detail: detail[ventana] del pico (users, msgs, risas, hype).
    chat_scores: dict {ventana: score} (para coincidencia de picos).
    audio_rms: [(segundo_relativo, dbfs), ...] de audio.rms_per_second,
        o None (se calcula desde video_path si se pasa).
    transcript: dict de transcripción (subtitles.py) o None.
    video_path: clip/segmento ya descargado (para RMS y caras Haar).
    peak_ts: pico en segundos absolutos del VOD; clip_start: inicio
        del clip en el VOD. peak_rel = peak_ts - clip_start.

    Señales de Uri (2026-11-17, clipper-directos) — DIAGNÓSTICO,
    no componentes del score de viralidad (la fórmula de 7
    componentes y sus pesos NO cambian; ver detect.score_clip):
      * heatmap_peaks: picos de heatmap.py (YouTube "lo más
        repetido") -> heatmap_peak_s (el más cercano al pico, si
        está a <= 30 s) y heatmap_score (0..1).
      * motion: dict de motion.measure_motion (o un % float) ->
        motion_pct y motion_flag ("en_movimiento" /
        "pausa_pantalla_fija" / "pausa_pero_reaccion_fuerte").
        Un clip congelado NO se descarta por esto: solo se marca.
      * game_voice: tramos normalizados de game_voice.py ->
        game_voice (tramos) y game_voice_estimated (True si la
        detección es estimada, sin texto inventado).

    Devuelve dict; cada señal que no se puede medir es None.
      * unique_users_per_10s: usuarios únicos en la ventana.
      * laugh_hype_ratio: (risas+hype)/msgs en la ventana.
      * rms_peak_delta_db: RMS máx. ±10 s del pico menos la mediana
        de los 30 s previos; silence_before_s: segundos seguidos de
        silencio (< -45 dBFS) justo antes del pico.
      * exclamations_per_min: signos ! y ? por minuto de transcripción.
      * faces_1fps: [{t, faces}] caras Haar muestreadas cada 1 s
        (None si no hay OpenCV/vídeo).
      * peak_coincidence_s: {chat_peak, audio_peak, transcript_peak,
        max_gap_s, coinciden} — picos a ±5 s.
      * payoff_position_s: posición del payoff dentro del clip;
        hook_words_0_3s: palabras transcritas en los primeros 3 s.
    """
    signals = {
        "unique_users_per_10s": None,
        "laugh_hype_ratio": None,
        "rms_peak_delta_db": None,
        "silence_before_s": None,
        "exclamations_per_min": None,
        "faces_1fps": None,
        "peak_coincidence_s": None,
        "payoff_position_s": None,
        "hook_words_0_3s": None,
        # Señales de Uri (diagnóstico, no componentes del score):
        "heatmap_peak_s": None,
        "heatmap_score": None,
        "heatmap_available": None,
        "motion_pct": None,
        "motion_flag": None,
        "motion_flag_reason": None,
        "game_voice": None,
        "game_voice_estimated": None,
    }
    if detail:
        signals["unique_users_per_10s"] = detail.get("users")
        msgs = detail.get("msgs") or 0
        if msgs > 0:
            signals["laugh_hype_ratio"] = round(
                ((detail.get("risas") or 0) + (detail.get("hype") or 0)) / msgs, 3)

    peak_rel = None
    if peak_ts is not None and clip_start is not None:
        peak_rel = float(peak_ts) - float(clip_start)
        signals["payoff_position_s"] = round(peak_rel, 1)

    # RMS: desde la lista pasada o desde el vídeo (audio.py).
    rms = audio_rms
    if rms is None and video_path and os.path.isfile(video_path):
        try:
            from .audio import rms_per_second
            rms = rms_per_second(video_path)
        except Exception:
            rms = None
    if rms and peak_rel is not None:
        vals = {int(s): db for s, db in rms}
        near = [db for s, db in vals.items() if abs(s - peak_rel) <= 10]
        before = [db for s, db in vals.items()
                  if peak_rel - 30 <= s < peak_rel - 5]
        if near and before:
            signals["rms_peak_delta_db"] = round(
                max(near) - statistics.median(before), 1)
        # Silencio justo antes del pico: segundos seguidos < -45 dBFS.
        silence = 0
        s = int(peak_rel) - 1
        while s >= 0 and vals.get(s, 0.0) < -45.0:
            silence += 1
            s -= 1
        signals["silence_before_s"] = silence

    # Transcripción: exclamaciones/min + hook 0-3 s + pico de palabras.
    # Solo cuentan las palabras/segmentos DENTRO del clip: la
    # transcripción cubre toda la ventana (60 s) y el clip es un
    # trozo de ella; sin este filtro el pico de palabras y las
    # exclamaciones medirían fuera del clip.
    words = _transcript_words(transcript) if transcript else []
    if clip_duration is not None:
        words = [w for w in words if 0 <= w["start"] < float(clip_duration)]
    if transcript:
        clip_segs = []
        for seg in (transcript or {}).get("segments", []):
            s0, s1 = float(seg.get("start") or 0), float(seg.get("end") or 0)
            if clip_duration is None or (s0 < float(clip_duration) and s1 > 0):
                clip_segs.append(seg)
        text = " ".join(s.get("text", "") for s in clip_segs)
        if clip_duration is not None:
            dur_s = float(clip_duration)
        else:
            dur_s = max((float(s.get("end") or 0) for s in clip_segs),
                        default=0.0)
        if dur_s > 0:
            import re as _re
            marks = len(_re.findall(r"[!?¡¿]", text))
            signals["exclamations_per_min"] = round(marks / (dur_s / 60.0), 1)
        if peak_rel is not None:
            signals["hook_words_0_3s"] = sum(
                1 for w in words if 0 <= w["start"] <= 3.0)

    # Caras Haar 1 fps (layout.py; None si no hay OpenCV/vídeo).
    # Una sola pasada de ffmpeg para todo el clip
    # (layout.detect_faces_1fps): antes era un ffmpeg por segundo.
    if video_path and os.path.isfile(video_path) and clip_duration:
        try:
            from .render import detect_faces_1fps
            from .config import get_layout_config
            signals["faces_1fps"] = detect_faces_1fps(
                video_path, clip_duration, get_layout_config())
        except Exception:
            signals["faces_1fps"] = None

    # Coincidencia de picos chat/audio/transcripción (±5 s).
    chat_peak = peak_rel
    audio_peak = None
    if rms:
        audio_peak = float(max(rms, key=lambda p: p[1])[0])
    transcript_peak = None
    if words:
        per_sec = {}
        for w in words:
            per_sec[int(w["start"])] = per_sec.get(int(w["start"]), 0) + 1
        transcript_peak = float(max(per_sec, key=per_sec.get))
    if chat_peak is not None and (audio_peak is not None or transcript_peak is not None):
        peaks = [p for p in (audio_peak, transcript_peak) if p is not None]
        gaps = [abs(p - chat_peak) for p in peaks]
        signals["peak_coincidence_s"] = {
            "chat_peak": round(chat_peak, 1),
            "audio_peak": audio_peak,
            "transcript_peak": transcript_peak,
            "max_gap_s": round(max(gaps), 1) if gaps else None,
            "coinciden": all(g <= 5.0 for g in gaps) if gaps else None,
        }

    # Señales de Uri (diagnóstico; no tocan el score de viralidad).
    # Heatmap: el pico de "lo más repetido" más cercano al pico de
    # chat, solo si está a <= 30 s (si no, no confirma nada).
    if heatmap_peaks:
        from .heatmap import nearest_heatmap_peak
        peaks_list = heatmap_peaks
        if isinstance(heatmap_peaks, dict):
            peaks_list = heatmap_peaks.get("peaks") or []
            signals["heatmap_available"] = bool(
                heatmap_peaks.get("available"))
        else:
            signals["heatmap_available"] = bool(peaks_list)
        nearest = nearest_heatmap_peak(peaks_list, peak_rel)
        if nearest is not None:
            signals["heatmap_peak_s"] = nearest["t"]
            signals["heatmap_score"] = nearest["score"]
    elif heatmap_peaks is not None:
        signals["heatmap_available"] = False
    # Movimiento: % medido (dict de motion.measure_motion o % float).
    motion_pct = None
    if isinstance(motion, dict):
        motion_pct = motion.get("motion_pct")
    elif motion is not None:
        try:
            motion_pct = float(motion)
        except (TypeError, ValueError):
            motion_pct = None
    if motion_pct is not None:
        from .motion import motion_flag as _motion_flag
        flag, flag_reason = _motion_flag(motion_pct, virality_total)
        signals["motion_pct"] = motion_pct
        signals["motion_flag"] = flag
        signals["motion_flag_reason"] = flag_reason
    # Voz del juego: tramos normalizados (sin texto inventado).
    if game_voice:
        from .game_voice import normalize_game_voice
        spans = normalize_game_voice(
            game_voice, clip_duration) if clip_duration else list(game_voice)
        signals["game_voice"] = spans
        signals["game_voice_estimated"] = any(
            s.get("estimated") for s in spans if isinstance(s, dict))
    return signals


if __name__ == "__main__":
    # Demo rápida con los fixtures de los tests (ver test_viral_score.py).
    import json
    coker = score_clip(
        {"msgs": 45, "risas": 5, "hype": 9, "users": 47},
        chat_multiple=8.5, rank=1,
        human={"hook": 8, "claridad": 9, "novedad": 7, "loop": 8, "packaging": 7},
        audio={"std_db": 11.5, "steady_frac": 0.2, "words": 77})
    print(json.dumps(coker, ensure_ascii=False, indent=2))
