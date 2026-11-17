#!/usr/bin/env python3
"""
audio.py — audio como segunda señal para viral-clip-agent (bloque 6).

El chat es la señal principal (risas + hype, clips_chat_v2.py), pero
un pico de chat con el audio plano suele ser spam o música de fondo:
es el caso de Locones 03h08m (24,75 pts, 0 risas, 0 hype, transcripción
vacía). Este módulo mide el audio SOLO en los top 10-15 candidatos
(ya descargados, nunca baja más vídeo) y decide:

  * chat alto + audio alto/dinámico = CONFIRMAR
  * chat alto + audio plano        = SOSPECHAR (spam, revisar a mano)

Qué mide, por segundo, con ffmpeg + numpy (sin librosa: no está
instalada; faster-whisper ya trae numpy):
  * RMS por segundo en dBFS (se decodifica a WAV 16 kHz mono con ffmpeg
    y se calcula la raíz cuadrática media por bloques de 1 s).
  * Dinámica: desviación estándar del RMS, rango máx-mín, fracción de
    segundos "estables" (a ±2 dB de la mediana: cama de música/juego
    constante) y segundos activos (RMS > -45 dBFS).

Regla de decisión (calibrada con los 3 VODs conocidos, 2026-11-16):
  * SOSPECHAR si la transcripción (si la hay) tiene 0 palabras en 60 s,
    o si el audio es plano: std < 9 dB Y fracción estable >= 45%.
  * CONFIRMAR en el resto (audio con voz/risas: subidas y bajadas).

  Locones 03h08m: std 7,4 dB, estable ~58%, 0 palabras -> SOSPECHAR.
  Coker   03h15m: std 11,5 dB, rango 35 dB, 77 palabras -> CONFIRMAR.
  NoraExplorer 7h12m: std 19,8 dB, rango 86 dB, 152 palabras -> CONFIRMAR.

  OJO: Locones es incluso MÁS FUERTE que Coker en media (-23,1 dBFS
  frente a -32,5 dBFS): el nivel medio NO discrimina, lo que discrimina
  es la dinámica + la voz. Por eso el veredicto nunca usa solo el
  volumen medio.

La transcripción es un bonus de scoring, nunca un veto:
  * +20% si hay palabras de risa/hype cerca del pico (jaja, lol, qué,
    wow, no, ... según el idioma).
  * +10% si hay voz cerca del pico pero sin palabras de risa/hype.
  * 0% si no hay voz o no hay transcripción.

Uso:
  .venv/bin/python audio.py runs/kick-coker-01a1dffd/segments/seg_01_t03h14m50s.mp4
  .venv/bin/python audio.py clip.mp4 --peak 20 --chat-score 56.5 --transcript clip_transcript.json
  .venv/bin/python audio.py clip.mp4 --json   # salida JSON para auto_clips.py

Requisitos: FFmpeg en el PATH y numpy (viene con faster-whisper).
Todo local y gratis ($0, decisión del usuario 2026-11-16): sin APIs
pagas, sin keys, sin tarjeta ni cuenta de facturación.
"""

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import wave

# Umbrales calibrados con Locones / Coker / NoraExplorer (ver docstring).
FLAT_STD_DB = 9.0          # std del RMS por debajo de esto = audio plano
FLAT_STEADY_FRAC = 0.45    # fracción de segundos a ±2 dB de la mediana
STEADY_TOL_DB = 2.0
ACTIVE_DBFS = -45.0        # por encima de esto el segundo cuenta como activo
BONUS_NEAR_PEAK_S = 10.0   # ventana alrededor del pico para el bonus
BONUS_LAUGH = 0.20         # +20% si hay risa/hype cerca del pico
BONUS_VOICE = 0.10         # +10% si hay voz cerca del pico (sin risa/hype)

LAUGH_WORDS = {
    # "no" NO está en la lista a propósito: en español es negación
    # ("No, no, no" en Coker) y dispara falsos +20%.
    "es": {"jaja", "jeje", "jiji", "qué", "wow", "dios", "madre",
           "increíble", "brutal", "locura", "vamos"},
    "en": {"lol", "lmao", "wow", "what", "oh", "insane", "crazy",
           "let's", "go"},
}


def decode_audio(video_path, start=0.0, end=None, sample_rate=16000):
    """Decodifica el audio a un array numpy float32 mono (16 kHz).

    Misma vía que subtitles.py: ffmpeg a WAV 16 kHz mono + módulo wave,
    sin PyAV ni librosa.
    """
    import numpy as np

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        cmd = ["ffmpeg", "-y", "-v", "error"]
        if start:
            cmd += ["-ss", str(start)]
        cmd += ["-i", video_path]
        if end is not None:
            cmd += ["-t", str(max(0.0, end - start))]
        cmd += ["-vn", "-ac", "1", "-ar", str(sample_rate),
                "-c:a", "pcm_s16le", wav_path]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        with wave.open(wav_path, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
        return (np.frombuffer(frames, dtype=np.int16).astype(np.float32)
                / 32768.0), sample_rate
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass


def rms_per_second(video_path, start=0.0, end=None, sample_rate=16000):
    """RMS por segundo en dBFS. Devuelve [(segundo_relativo, dbfs), ...].

    El segundo 0 es ``start`` dentro del archivo. Un segundo sin audio
    (RMS 0) se marca como -90 dBFS, no como -inf.
    """
    import numpy as np

    audio, sr = decode_audio(video_path, start, end, sample_rate)
    # Solo segundos completos: un resto de 0,02 s (60,016 s de segmento)
    # no es un segundo y distorsiona el RMS.
    n_seconds = len(audio) // sr if len(audio) else 0
    out = []
    for s in range(n_seconds):
        seg = audio[s * sr:(s + 1) * sr]
        if len(seg) == 0:
            out.append((s, -90.0))
            continue
        rms = float(np.sqrt(np.mean(seg ** 2)))
        out.append((s, 20 * math.log10(rms) if rms > 1e-9 else -90.0))
    return out


def audio_stats(rms_list):
    """Resumen de dinámica a partir del RMS por segundo."""
    import numpy as np

    if not rms_list:
        return {"seconds": 0, "mean_dbfs": None, "max_dbfs": None,
                "min_dbfs": None, "std_db": None, "range_db": None,
                "steady_frac": None, "active_seconds": 0}
    vals = np.array([db for _, db in rms_list], dtype=float)
    median = float(np.median(vals))
    steady = float(np.mean(np.abs(vals - median) <= STEADY_TOL_DB))
    return {
        "seconds": len(vals),
        "mean_dbfs": round(float(np.mean(vals)), 1),
        "max_dbfs": round(float(np.max(vals)), 1),
        "min_dbfs": round(float(np.min(vals)), 1),
        "std_db": round(float(np.std(vals)), 1),
        "range_db": round(float(np.max(vals) - np.min(vals)), 1),
        "steady_frac": round(steady, 2),
        "active_seconds": int(np.sum(vals > ACTIVE_DBFS)),
    }


def is_flat(stats):
    """True si el audio es una cama constante (música/juego sin voz)."""
    if stats["std_db"] is None:
        return True
    return (stats["std_db"] < FLAT_STD_DB
            and stats["steady_frac"] is not None
            and stats["steady_frac"] >= FLAT_STEADY_FRAC)


def transcript_words(transcript):
    """Todas las palabras con timestamps de una transcripción (subtitles.py)."""
    words = []
    for seg in (transcript or {}).get("segments", []):
        for w in seg.get("words", []):
            words.append(w)
    return words


def transcription_bonus(transcript, peak_rel=None, lang=None):
    """Bonus de scoring por transcripción: 0, +10% o +20%. Nunca veto.

    peak_rel es el pico en segundos relativos al segmento. Si no se
    pasa, se mira toda la transcripción.
    """
    words = transcript_words(transcript)
    if not words:
        return 0.0, "sin voz transcrita"
    if peak_rel is not None:
        near = [w for w in words
                if peak_rel - BONUS_NEAR_PEAK_S <= w["start"]
                <= peak_rel + BONUS_NEAR_PEAK_S]
    else:
        near = words
    if not near:
        return 0.0, "voz lejos del pico"
    langs = {lang} if lang else {"es", "en"}
    laugh = set().union(*(LAUGH_WORDS.get(lg, set()) for lg in langs))
    text = " ".join(w["word"].strip().lower().strip("¿?¡!.,") for w in near)
    tokens = set(text.split())
    if tokens & laugh:
        return BONUS_LAUGH, f"risa/hype cerca del pico: {sorted(tokens & laugh)}"
    return BONUS_VOICE, f"voz cerca del pico ({len(near)} palabras, sin risa/hype)"


def audio_verdict(video_path, chat_score=None, peak_rel=None,
                  transcript=None, lang=None, start=0.0, end=None):
    """RMS + dinámica + transcripción -> veredicto CONFIRMAR / SOSPECHAR.

    Devuelve un dict con rms_per_second, stats, flat, words, bonus y
    decision + reason. La decisión es una señal, no un veto: un
    SOSPECHAR se revisa a mano, no se descarta solo.
    """
    rms = rms_per_second(video_path, start=start, end=end)
    stats = audio_stats(rms)
    flat = is_flat(stats)
    words = transcript_words(transcript)
    bonus, bonus_reason = transcription_bonus(transcript, peak_rel, lang)

    if transcript is not None and len(words) == 0:
        decision = "sospechar"
        reason = ("sin voz transcrita en 60 s (transcripción vacía) "
                  "pese al pico de chat")
    elif flat:
        decision = "sospechar"
        reason = (f"audio plano: std {stats['std_db']} dB < {FLAT_STD_DB} dB "
                  f"y {stats['steady_frac']:.0%} de segundos estables "
                  f"(cama constante, posible spam/música)")
    else:
        decision = "confirmar"
        reason = (f"audio dinámico: std {stats['std_db']} dB, "
                  f"rango {stats['range_db']} dB"
                  + (f", {len(words)} palabras transcritas"
                     if transcript is not None else ", sin transcripción"))

    return {
        "rms_per_second": [{"second": s, "dbfs": round(db, 1)}
                           for s, db in rms],
        "stats": stats,
        "flat": flat,
        "words": len(words),
        "transcription_bonus": bonus,
        "transcription_bonus_reason": bonus_reason,
        "decision": decision,
        "reason": reason,
        "chat_score": chat_score,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Audio como segunda señal: RMS por segundo y veredicto "
                    "confirmar/sospechar para un candidato (segmento ya descargado).")
    p.add_argument("video", help="Segmento/clip ya descargado (mp4)")
    p.add_argument("--start", type=float, default=0.0,
                   help="Inicio en s dentro del archivo (default: 0)")
    p.add_argument("--end", type=float, default=None,
                   help="Fin en s dentro del archivo (default: hasta el final)")
    p.add_argument("--peak", type=float, default=None,
                   help="Pico de chat en s relativos al segmento (para el bonus)")
    p.add_argument("--chat-score", type=float, default=None,
                   help="Puntuación de chat del candidato (solo informativa)")
    p.add_argument("--transcript", help="JSON de transcripción (subtitles.py)")
    p.add_argument("--lang", default=None, help="Idioma: es, en (default: auto)")
    p.add_argument("--json", action="store_true", help="Salida solo en JSON")
    args = p.parse_args()

    if not os.path.isfile(args.video):
        print(f"Error: no existe el vídeo: {args.video}", file=sys.stderr)
        return 1
    transcript = None
    if args.transcript:
        with open(args.transcript, encoding="utf-8") as f:
            transcript = json.load(f)

    result = audio_verdict(args.video, chat_score=args.chat_score,
                           peak_rel=args.peak, transcript=transcript,
                           lang=args.lang, start=args.start, end=args.end)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"Audio: {args.video}")
    print("RMS por segundo (dBFS):")
    print("  " + " ".join(f"{r['dbfs']:.0f}" for r in result["rms_per_second"]))
    s = result["stats"]
    print(f"Media {s['mean_dbfs']} dBFS | máx {s['max_dbfs']} | mín {s['min_dbfs']} | "
          f"std {s['std_db']} dB | rango {s['range_db']} dB | "
          f"estables {s['steady_frac']:.0%} | activos {s['active_seconds']}/{s['seconds']} s")
    print(f"Palabras transcritas: {result['words']} | "
          f"Bonus transcripción: +{result['transcription_bonus']:.0%} "
          f"({result['transcription_bonus_reason']})")
    print(f"Veredicto: {result['decision'].upper()} — {result['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
