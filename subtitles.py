#!/usr/bin/env python3
"""
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

Requisitos: el venv del proyecto (.venv/) con faster-whisper, y
FFmpeg en el PATH. El Python del sistema NO sirve: instalar
faster-whisper ahí falla por PEP 668 / typing_extensions de Debian.

Uso:
  .venv/bin/python subtitles.py runs/2929669422/segment_7h12m.mp4
  .venv/bin/python subtitles.py clip.mp4 --srt clip.srt --burn clip_subs.mp4
  .venv/bin/python subtitles.py clip.mp4 --srt clip.srt --no-burn
  .venv/bin/python subtitles.py clip.mp4 --model tiny --lang en --fontsize 48
"""

import argparse
import json
import os
import subprocess
import sys

DEFAULT_MODEL = "tiny"
DEFAULT_MAX_CHARS = 20
DEFAULT_MAX_DURATION = 2.0
DEFAULT_FONTSIZE = 48


def transcribe_audio(video_path, model_name=DEFAULT_MODEL, language=None):
    """Transcribe con faster-whisper. Devuelve (transcript, info).

    transcript tiene el mismo formato que openshorts: {"segments": [...],
    "language": ...}, cada segmento con "words" [{word, start, end}].

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
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, info = model.transcribe(
            audio, language=language, word_timestamps=True
        )
        transcript = {"segments": [], "language": info.language}
        for segment in segments:
            seg_data = {
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "words": [],
            }
            if segment.words:
                for word in segment.words:
                    seg_data["words"].append(
                        {"word": word.word.strip(), "start": word.start, "end": word.end}
                    )
            transcript["segments"].append(seg_data)
        print(
            f"Transcripción completa. Idioma: {info.language} "
            f"(probabilidad {info.language_probability:.2f})"
        )
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
                 max_chars=DEFAULT_MAX_CHARS, max_duration=DEFAULT_MAX_DURATION):
    """Genera un SRT agrupando palabras en líneas cortas (openshorts)."""
    words = []
    for segment in transcript.get("segments", []):
        for word_info in segment.get("words", []):
            if word_info["end"] > clip_start and word_info["start"] < clip_end:
                words.append(word_info)
    if not words:
        return False

    srt_content = ""
    index = 1
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
                text = " ".join(w["word"] for w in current_block).strip()
                srt_content += format_srt_block(index, block_start, block_end, text)
                index += 1
                current_block = [word]
                block_start = start
            else:
                current_block.append(word)
    if current_block:
        block_end = current_block[-1]["end"] - clip_start
        text = " ".join(w["word"] for w in current_block).strip()
        srt_content += format_srt_block(index, block_start, block_end, text)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(srt_content)
    return True


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


def burn_subtitles(video_path, srt_path, output_path, fontsize=DEFAULT_FONTSIZE,
                   font_name="DejaVu Sans", font_color="#FFFFFF",
                   border_color="#000000", border_width=3, margin_v=140):
    """Quema el SRT en el vídeo con ffmpeg (estilo openshorts, adaptado
    a vertical 1080x1920: PlayRes explícito y fuente grande)."""
    safe_srt_path = srt_path.replace("\\", "/").replace(":", "\\:")
    style_string = (
        f"PlayResX=1080,PlayResY=1920,"
        f"Alignment=2,"
        f"Fontname={font_name},"
        f"Fontsize={fontsize},"
        f"PrimaryColour={hex_to_ass_color(font_color, 1.0)},"
        f"OutlineColour={hex_to_ass_color(border_color, 1.0)},"
        f"BackColour={hex_to_ass_color('#000000', 0.0)},"
        f"BorderStyle=1,"
        f"Outline={border_width},"
        f"Shadow=0,"
        f"MarginV={margin_v},"
        f"Bold=1"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"subtitles='{safe_srt_path}':force_style='{style_string}'",
        "-c:a", "copy",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
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
    p.add_argument("video", help="Clip de entrada (p. ej. segment_7h12m.mp4)")
    p.add_argument("--srt", help="Ruta del SRT (default: <video>.srt)")
    p.add_argument("--burn", help="Vídeo de salida con subtítulos quemados")
    p.add_argument("--no-burn", action="store_true",
                   help="Solo genera el SRT, sin quemarlo")
    p.add_argument("--transcript", help="Guarda la transcripción completa en JSON")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help="Modelo faster-whisper (default: tiny)")
    p.add_argument("--lang", default=None,
                   help="Idioma: es, en... (default: auto)")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    p.add_argument("--max-duration", type=float, default=DEFAULT_MAX_DURATION)
    p.add_argument("--fontsize", type=int, default=DEFAULT_FONTSIZE)
    p.add_argument("--offset", type=float, default=0.0,
                   help="Desplazamiento del SRT respecto al vídeo (s)")
    args = p.parse_args()

    if not os.path.isfile(args.video):
        print(f"Error: no existe el vídeo: {args.video}", file=sys.stderr)
        return 1

    base, _ = os.path.splitext(args.video)
    srt_path = args.srt or f"{base}.srt"
    transcript, info = transcribe_audio(args.video, args.model, args.lang)
    if args.transcript:
        with open(args.transcript, "w", encoding="utf-8") as f:
            json.dump(transcript, f, ensure_ascii=False, indent=2)

    duration = get_duration(args.video)
    clip_end = duration if duration else 1e9
    if not generate_srt(transcript, args.offset, clip_end, srt_path,
                        max_chars=args.max_chars, max_duration=args.max_duration):
        print("Sin palabras transcritas: no se generó SRT.", file=sys.stderr)
        return 1
    print(f"SRT: {srt_path}")

    if not args.no_burn:
        out = args.burn or f"{base}_subs.mp4"
        burn_subtitles(args.video, srt_path, out, fontsize=args.fontsize)
        print(f"Clip con subtítulos: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
