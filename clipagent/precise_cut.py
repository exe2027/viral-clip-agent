#!/usr/bin/env python3
"""
precise_cut.py — recorte preciso por frase/silencio (bloque 6).

El corte fijo actual (pico -20 s / +40 s) corta frases por la mitad.
Este módulo afina inicio y fin SOLO dentro del segmento ya descargado
(nunca baja más vídeo: el segmento de 60 s es todo lo que hay):

  * Inicio: 2-3 s antes del disparador (pico de chat), retrocediendo
    hasta el inicio de la frase que lo contiene (hueco entre palabras
    >= 0,5 s o silencio detectado).
  * Fin: hasta el fin de la frase/risa que contiene el pico +1-2 s,
    avanzando hasta el siguiente hueco/silencio.
  * Tope: 60 s. Si la frase afinada superara 60 s, se recorta el fin.

Fuentes de verdad, en este orden:
  1. Timestamps por palabra de faster-whisper: subtitles.py ya los
     genera (transcript JSON con segments[].words[].start/end). Si se
     pasa --transcript se reutiliza; si no, se transcribe el segmento
     con el modelo tiny (local, mismo que subtitles.py).
  2. Silencio detectado con ffmpeg (filtro silencedetect): marca los
     huecos sin voz para no cortar dentro de una palabra.
  3. Si no hay transcripción fiable (< 3 palabras, o la transcripción
     falla): cae al corte fijo actual, pico -20 s / +40 s, recortado
     al segmento.

El corte en sí lo hace layout.py (regla del proyecto: stack/pip/
center por tramos) si está disponible, o clips_chat_v2.cut_vertical_clip
(recorte centrado) si no. El inicio/fin afinado se guarda en el
manifiesto como precise_cut {start, end, method}.

Uso:
  .venv/bin/python precise_cut.py runs/kick-coker-01a1dffd/segments/seg_01_t03h14m50s.mp4 \\
      --peak 11710 --segment-start 11690 --transcript runs/kick-coker-01a1dffd/clips/clip_01_transcript.json
  .venv/bin/python precise_cut.py seg.mp4 --peak 11710 --segment-start 11690 --out clip_preciso.mp4
  .venv/bin/python precise_cut.py seg.mp4 --peak 11710 --segment-start 11690 --no-transcribe --json

Requisitos: FFmpeg en el PATH; faster-whisper solo si hay que
transcribir (sin --transcript y sin --no-transcribe). Todo local y
gratis ($0, decisión del usuario 2026-11-16): modelos faster-whisper
tiny/base descargados de HuggingFace sin token, VAD local, sin Whisper
API de OpenAI, sin Gemini ni ninguna API paga, sin tarjeta ni cuenta
de facturación.
"""

import argparse
import json
import os
import re
import subprocess
import sys


DEFAULT_MAX_DURATION = 60
DEFAULT_MIN_DURATION = 36   # decisión del usuario 2026-11-17: el clip
# de 21 s publicado en test era "muy poco". 36 s = primer segundo
# entero por encima del rango prohibido 25-35 (regla de oro: ningún
# clip de 30 s); el objetivo es 40 s y el tope duro 60 s.
DEFAULT_TARGET_DURATION = 40
FORBIDDEN_DURATION_RANGE = (25, 35)  # regla de oro, inclusivo
DEFAULT_PRE_ROLL = 2.5      # inicio 2-3 s antes del disparador
DEFAULT_POST_ROLL = 1.5     # fin 1-2 s después del fin de frase/risa
DEFAULT_FIXED_PRE = 20      # caída: corte fijo actual (pico -20 s)
DEFAULT_FIXED_DUR = 40      # caída: corte fijo actual (+40 s)


def enforce_min_duration(start, end, segment_duration,
                         min_duration=DEFAULT_MIN_DURATION,
                         max_duration=DEFAULT_MAX_DURATION):
    """Alarga (nunca recorta) un corte hasta la duración mínima.

    Si el corte afinado por frase queda corto, se alarga el fin hasta
    min_duration; si el segmento se acaba, se retrocede el inicio.
    Un corte de 25-35 s (rango prohibido, regla de oro) también se
    alarga hasta 36 s. Si el segmento entero es más corto que el
    mínimo, se devuelve lo que hay y el motivo lo dice: ese clip no
    debe publicarse (lo retiene policy.py).
    Devuelve (start, end, nota_o_None).
    """
    duration = end - start
    extended = False
    if duration < min_duration:
        end = min(segment_duration, start + min_duration)
        if end - start < min_duration:
            start = max(0.0, end - min_duration)
        extended = True
    # Tope duro: un corte fijo de 40 s con --max-duration 36 también se
    # recorta hasta el tope (la config garantiza min <= max, ver
    # validate.py, así que el tope nunca baja del mínimo).
    if end - start > max_duration:
        end = start + max_duration
    if end - start < min_duration:
        return start, end, (f"segmento de {segment_duration:.1f} s: no "
                            f"llega al mínimo de {min_duration} s")
    if extended:
        return start, end, (f"alargado a {min_duration} s (mínimo, regla "
                            f"del usuario 2026-11-17)")
    return start, end, None
PHRASE_GAP = 0.5            # hueco entre palabras que separa frases
SILENCE_NOISE_DB = -40      # umbral de silencio para silencedetect
SILENCE_MIN_DUR = 0.3       # silencio mínimo que cuenta como hueco


def detect_silences(video_path, start=0.0, end=None,
                    noise_db=SILENCE_NOISE_DB, min_duration=SILENCE_MIN_DUR):
    """Silencios con ffmpeg silencedetect. Devuelve [(inicio, fin), ...]
    en segundos relativos al archivo (start=0 es el inicio del archivo).

    ffmpeg escribe silence_start/silence_end en stderr.
    """
    cmd = ["ffmpeg", "-v", "info", "-i", video_path]
    if start:
        cmd = ["ffmpeg", "-v", "info", "-ss", str(start), "-i", video_path]
    if end is not None:
        cmd += ["-t", str(max(0.0, end - start))]
    cmd += ["-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
            "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    silences = []
    cur_start = None
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m:
            cur_start = float(m.group(1)) + start
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and cur_start is not None:
            silences.append((cur_start, float(m.group(1)) + start))
            cur_start = None
    return silences


def _words_from_transcript(transcript):
    words = []
    for seg in (transcript or {}).get("segments", []):
        for w in seg.get("words", []):
            if w.get("start") is not None and w.get("end") is not None:
                words.append({"word": w["word"], "start": float(w["start"]),
                              "end": float(w["end"])})
    return sorted(words, key=lambda w: w["start"])


def _phrase_start(words, anchor_idx):
    """Retrocede desde words[anchor_idx] hasta el inicio de su frase:
    mientras el hueco con la palabra anterior sea < PHRASE_GAP, es la
    misma frase."""
    i = anchor_idx
    while i > 0 and words[i]["start"] - words[i - 1]["end"] < PHRASE_GAP:
        i -= 1
    return words[i]["start"]


def _phrase_end(words, anchor_idx):
    """Avanza desde words[anchor_idx] hasta el fin de su frase."""
    i = anchor_idx
    while (i + 1 < len(words)
           and words[i + 1]["start"] - words[i]["end"] < PHRASE_GAP):
        i += 1
    return words[i]["end"]


def refine_cut(segment_duration, peak_rel, transcript=None, silences=None,
               pre_roll=DEFAULT_PRE_ROLL, post_roll=DEFAULT_POST_ROLL,
               max_duration=DEFAULT_MAX_DURATION,
               min_duration=DEFAULT_MIN_DURATION,
               fixed_pre=DEFAULT_FIXED_PRE, fixed_dur=DEFAULT_FIXED_DUR):
    """Afinar (inicio, fin) relativos al segmento. peak_rel es el pico
    de chat en segundos dentro del segmento.

    Devuelve dict {start, end, duration, method, reason}.
    method: 'phrase' (por palabra+silencio) o 'fixed' (caída).

    REGLA 2026-11-17: ningún corte sale de aquí por debajo del mínimo
    (36 s) ni dentro del rango prohibido 25-35 s: el corte afinado se
    ALARGA hasta el mínimo (nunca se recorta el momento), y el tope
    duro es 60 s.
    """
    words = _words_from_transcript(transcript)
    if len(words) < 3:
        # Sin transcripción fiable: corte fijo actual, recortado al segmento.
        start = max(0.0, peak_rel - fixed_pre)
        end = min(segment_duration, start + fixed_dur)
        start, end, note = enforce_min_duration(
            start, end, segment_duration, min_duration, max_duration)
        return {"start": round(start, 2), "end": round(end, 2),
                "duration": round(end - start, 2), "method": "fixed",
                "reason": (f"sin transcripción fiable "
                           f"({len(words)} palabras): corte fijo "
                           f"pico -{fixed_pre}s / +{fixed_dur}s"
                           + (f" — {note}" if note else ""))}

    # Disparador: primera palabra que empieza en/tras (pico - pre_roll);
    # si no hay, la última palabra antes del pico.
    anchor_t = peak_rel - pre_roll
    anchor_idx = next((i for i, w in enumerate(words)
                       if w["start"] >= anchor_t), None)
    if anchor_idx is None:
        anchor_idx = max(i for i, w in enumerate(words)
                         if w["start"] <= peak_rel)
    start = _phrase_start(words, anchor_idx) - pre_roll
    # No empezar dentro de un silencio largo: si el inicio cae en un
    # silencio, se avanza hasta su fin (la voz empieza ahí).
    if silences:
        for s0, s1 in silences:
            if s0 <= start < s1:
                start = s1
                break
    start = max(0.0, start)

    # Fin: fin de la frase que contiene el pico (o la primera palabra
    # en/tras el pico) + post_roll; si un silencio empieza justo
    # después, se corta en el inicio del silencio + post_roll.
    peak_idx = next((i for i, w in enumerate(words)
                     if w["end"] >= peak_rel), len(words) - 1)
    end = _phrase_end(words, peak_idx) + post_roll
    if silences:
        for s0, s1 in silences:
            if end - post_roll <= s0 <= end:
                end = s0 + post_roll
                break
    end = min(segment_duration, end)

    # Tope 60 s: si la frase afinada es más larga, se recorta el fin
    # (el inicio manda: es donde empieza el momento).
    if end - start > max_duration:
        end = start + max_duration
    # Mínimo 36 s (regla 2026-11-17): una frase corta no deja un clip
    # de 5 s ni de 21 s; se alarga el fin (o se retrocede el inicio
    # si el segmento se acaba) hasta el mínimo.
    start, end, note = enforce_min_duration(
        start, end, segment_duration, min_duration, max_duration)
    return {"start": round(start, 2), "end": round(end, 2),
            "duration": round(end - start, 2), "method": "phrase",
            "reason": (f"inicio en frase '{words[anchor_idx]['word']}' "
                       f"({words[anchor_idx]['start']:.1f}s) -{pre_roll}s, "
                       f"fin en fin de frase +{post_roll}s"
                       + (f" — {note}" if note else ""))}


def get_duration(video_path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def cut_precise(source_path, cut, out_path, crf=23, preset="fast",
                crop="center", layout_mode=None, cfg=None):
    """Corta [cut['start'], cut['end']] en vertical 1080x1920.

    Con layout.py (regla del proyecto) si está disponible, si no con
    clips_chat_v2.cut_vertical_clip. Devuelve la decisión de layout
    (o None si se usó el recorte centrado).
    """
    try:
        from .render import cut_clip_with_layout
        return cut_clip_with_layout(
            source_path, cut["start"], cut["duration"], out_path,
            crf=crf, preset=preset, crop=crop,
            layout_mode=layout_mode, cfg=cfg or {})
    except ImportError:
        from .ingest import cut_vertical_clip
        cut_vertical_clip(source_path, cut["start"], cut["duration"],
                          out_path, crf=crf, preset=preset, crop=crop)
        return None


def main() -> int:
    p = argparse.ArgumentParser(
        description="Recorte preciso por frase/silencio dentro de un "
                    "segmento ya descargado (nunca baja más vídeo).")
    p.add_argument("segment", help="Segmento ya descargado (mp4, 60 s)")
    p.add_argument("--peak", type=float, required=True,
                   help="Pico de chat en segundos ABSOLUTOS del VOD")
    p.add_argument("--segment-start", type=float, required=True,
                   help="Inicio del segmento en segundos absolutos del VOD")
    p.add_argument("--transcript", help="JSON de transcripción (subtitles.py); "
                   "si no se pasa, se transcribe el segmento (tiny, local)")
    p.add_argument("--no-transcribe", action="store_true",
                   help="No transcribir: usa solo silencio o el corte fijo")
    p.add_argument("--out", help="Clip de salida (mp4 vertical 1080x1920)")
    p.add_argument("--pre-roll", type=float, default=DEFAULT_PRE_ROLL)
    p.add_argument("--post-roll", type=float, default=DEFAULT_POST_ROLL)
    p.add_argument("--max-duration", type=float, default=DEFAULT_MAX_DURATION,
                   help="Tope duro del corte en segundos (default: 60, "
                        "decisión del usuario 2026-11-17)")
    p.add_argument("--min-duration", type=float, default=DEFAULT_MIN_DURATION,
                   help="Duración mínima del corte en segundos (default: "
                        "36, decisión del usuario 2026-11-17: el clip de "
                        "21 s era 'muy poco'; por debajo se alarga, nunca "
                        "se deja corto)")
    p.add_argument("--lang", default=None, help="Idioma para transcribir (es, en...)")
    p.add_argument("--layout", choices=["auto", "stack", "pip", "center"],
                   default=None, help="Forzar layout (default: auto)")
    p.add_argument("--crf", type=int, default=23)
    p.add_argument("--preset", default="fast")
    p.add_argument("--json", action="store_true", help="Salida solo en JSON")
    args = p.parse_args()

    if not os.path.isfile(args.segment):
        print(f"Error: no existe el segmento: {args.segment}", file=sys.stderr)
        return 1
    segment_duration = get_duration(args.segment)
    peak_rel = args.peak - args.segment_start
    if not (0 <= peak_rel <= segment_duration):
        print(f"Error: el pico ({args.peak}) no cae dentro del segmento "
              f"[{args.segment_start}, {args.segment_start + segment_duration}].",
              file=sys.stderr)
        return 1

    transcript = None
    if args.transcript:
        with open(args.transcript, encoding="utf-8") as f:
            transcript = json.load(f)
    elif not args.no_transcribe:
        from .transcribe import transcribe_audio
        transcript, _ = transcribe_audio(args.segment, language=args.lang)

    silences = detect_silences(args.segment)
    cut = refine_cut(segment_duration, peak_rel, transcript=transcript,
                     silences=silences, pre_roll=args.pre_roll,
                     post_roll=args.post_roll,
                     max_duration=args.max_duration,
                     min_duration=args.min_duration)
    result = {"segment": args.segment, "peak_abs": args.peak,
              "segment_start": args.segment_start, "peak_rel": round(peak_rel, 2),
              "segment_duration": round(segment_duration, 2),
              "silences": [[round(a, 2), round(b, 2)] for a, b in silences],
              **cut}
    if args.out:
        layout_decision = cut_precise(
            args.segment, cut, args.out, crf=args.crf, preset=args.preset,
            layout_mode=args.layout)
        result["out"] = args.out
        result["layout"] = (layout_decision or {}).get("mode")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"Segmento: {args.segment} ({segment_duration:.1f}s) | "
          f"pico {args.peak}s (rel {peak_rel:.1f}s)")
    print(f"Silencios: {len(silences)} "
          f"({', '.join(f'{a:.1f}-{b:.1f}s' for a, b in silences[:8])}"
          f"{'...' if len(silences) > 8 else ''})")
    print(f"Corte preciso: {cut['start']:.1f}s -> {cut['end']:.1f}s "
          f"({cut['duration']:.1f}s) [{cut['method']}] — {cut['reason']}")
    if args.out:
        print(f"Clip: {args.out} (layout: {result['layout']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
