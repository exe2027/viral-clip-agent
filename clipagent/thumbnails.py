#!/usr/bin/env python3
"""
thumbnails.py — miniatura REAL: fotogramas del clip, sin texto quemado.

Bloque 2 del tribunal (viral-clip-agent). Nada de miniaturas
generadas ni de texto inventado: se extraen 3 fotogramas del propio
clip con ffmpeg, en distintos timestamps alrededor del payoff
(``payoff_ts_clip`` de la ficha del momento, ``moment_card.py``):

  * antes del payoff  (payoff - 5 s)
  * el payoff         (payoff)
  * después del payoff (payoff + 5 s)

Los 3 quedan guardados para elección humana en la aprobación; la
aprobación elige uno, el código no elige por el humano. La portada
de Instagram es una copia del fotograma del payoff
(``cover_instagram.jpg``), 1080x1920, sin texto quemado por ahora;
con ``--text`` (o ``thumbnail_with_text``) se quema encima el texto
de miniatura estilo GTA 6 / Vice City (condensada neón).

Con ``--vision`` (ver ``vision.py``), Gemini Flash-Lite ordena los
3 frames y propone el mejor para el título dado; la portada pasa a
ser ese frame. Sin key o si la API falla, se cae al payoff sin
romper nada. La elección de Gemini es una propuesta: la aprobación
humana sigue mandando.

Sin payoff conocido se usan 25 % / 50 % / 75 % de la duración, y la
ficha lo marca (evidence low): los frames siguen siendo reales, pero
nadie sabe cuál es el momento.

Uso como librería:

    from .thumbnails import extract_thumbnail_frames, instagram_cover
    frames = extract_thumbnail_frames(
        "runs/.../clip.mp4", payoff_ts_clip=20.0,
        out_dir="runs/.../clips/thumbnails")
    # frames = [{"path": ..., "ts_clip": 15.0, "role": "antes del payoff"}, ...]
    cover = instagram_cover(frames, "runs/.../clips/thumbnails/cover_instagram.jpg")

Uso como CLI:

    python thumbnails.py runs/.../clip.mp4 --payoff 20 --out-dir runs/.../thumbnails
    python thumbnails.py runs/.../clip.mp4 --card runs/.../moment_card.json
    python thumbnails.py runs/.../clip.mp4 --card runs/.../moment_card.json --vision --title "GTA V RP: Errar un tiro y el chat explotó"
"""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

try:
    from .vision import choose_best_frame
except ImportError:  # vision.py es opcional; sin él no hay --vision
    choose_best_frame = None

# Desplazamientos alrededor del payoff (s). 5 s es suficiente para que
# los 3 frames se vean distintos y a la vez sigan siendo "el momento".
PAYOFF_OFFSETS = (-5, 0, +5)
ROLES = ("antes del payoff", "payoff", "después del payoff")
DEFAULT_FRAMES = (0.25, 0.50, 0.75)  # sin payoff: cuartos del clip


def get_duration(clip_path):
    """Duración en s con ffprobe; None si no se puede medir."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(clip_path)],
            capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _extract_frame(clip_path, ts, out_path):
    """Un fotograma con ffmpeg (sin re-codificar el video)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{ts:.2f}",
         "-i", str(clip_path), "-frames:v", "1", "-q:v", "2",
         str(out_path)],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg no pudo extraer el fotograma a {ts:.1f} s: "
                           f"{r.stderr.strip()[-300:]}")
    return out_path


def extract_thumbnail_frames(clip_path, payoff_ts_clip=None, out_dir=None,
                             offsets=PAYOFF_OFFSETS):
    """Extrae 3 fotogramas candidatos del clip.

    Devuelve [{"path", "ts_clip", "ts_vod"(si se pasa), "role"}].
    No elige: los 3 se guardan para que un humano elija en aprobación.
    """
    clip_path = Path(clip_path)
    if not clip_path.exists():
        raise FileNotFoundError(f"clip no encontrado: {clip_path}")
    duration = get_duration(clip_path)
    out_dir = Path(out_dir) if out_dir else clip_path.parent / "thumbnails"
    out_dir.mkdir(parents=True, exist_ok=True)

    if payoff_ts_clip is not None:
        payoff = float(payoff_ts_clip)
        times = [payoff + o for o in offsets]
        roles = list(ROLES)
    else:
        if duration:
            times = [duration * f for f in DEFAULT_FRAMES]
        else:
            times = [15.0, 30.0, 45.0]  # clip de 60 s sin duración medible
        roles = ["25% del clip", "50% del clip", "75% del clip"]

    frames = []
    for i, (ts, role) in enumerate(zip(times, roles), start=1):
        # Dentro del clip, con 0,5 s de margen en cada extremo.
        ts = max(0.5, ts)
        if duration:
            ts = min(ts, max(0.5, duration - 0.5))
        name = f"thumb_{i:02d}_{role.replace(' ', '_')}_{ts:.1f}s.jpg"
        path = _extract_frame(clip_path, ts, out_dir / name)
        frames.append({"path": str(path), "ts_clip": round(ts, 1),
                       "role": role, "has_text": False})
    return frames


def choose_cover_frame(frames, title=None, game=None, use_vision=False, model=None):
    """Frame de portada: con visión, el que elige Gemini; sin ella, el payoff.

    Devuelve (frame, vision_result). ``vision_result`` es None sin
    visión, o el dict de ``vision.choose_best_frame`` (con
    ``vision`` en "gemini" | "skipped" | "error"). Sin key o con
    error, la visión cae al payoff y no lanza excepción.
    """
    if not frames:
        raise ValueError("sin fotogramas: primero extract_thumbnail_frames()")
    if not use_vision or choose_best_frame is None:
        payoff = next((f for f in frames if f["role"] == "payoff"), None)
        return payoff or frames[len(frames) // 2], None
    kwargs = {}
    if model:
        kwargs["model"] = model
    result = choose_best_frame(frames, title=title, game=game, **kwargs)
    return frames[result["best_index"]], result


def instagram_cover(frames, out_path=None, vision_choice=None, vision_result=None):
    """Portada de Instagram: copia del fotograma del payoff.

    ``frames`` es la lista de extract_thumbnail_frames. Sin frame de
    payoff (no había payoff conocido) usa el del 50 % y lo dice.
    Con ``vision_choice`` (frame devuelto por ``choose_cover_frame``
    con visión), la portada es ese frame en vez del payoff.
    """
    if not frames:
        raise ValueError("sin fotogramas: primero extract_thumbnail_frames()")
    if vision_choice is not None:
        chosen = vision_choice
    else:
        payoff = next((f for f in frames if f["role"] == "payoff"), None)
        chosen = payoff or frames[len(frames) // 2]
    out_path = Path(out_path) if out_path else Path(chosen["path"]).parent / "cover_instagram.jpg"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(chosen["path"], out_path)
    if vision_result and vision_result.get("vision") == "gemini":
        note = f"elegido por Gemini: {vision_result.get('reason') or ''}".strip()
    elif vision_result and vision_result.get("vision") in ("skipped", "error"):
        note = (f"visión {vision_result['vision']} ({vision_result.get('reason')}), "
                "portada = fotograma del payoff")
    elif chosen["role"] == "payoff":
        note = "copia del fotograma del payoff, sin texto quemado"
    else:
        note = "SIN payoff conocido: portada = frame del 50% del clip"
    return {"path": str(out_path), "source_ts_clip": chosen["ts_clip"],
            "source_role": chosen["role"], "note": note}


# Miniatura estilo GTA 6 / Vice City (2026-11-18, orden del usuario):
# el texto de miniatura (el del momento, no una exclamación
# genérica — regla endurecida 2026-11-18, ver
# metadata.GENERIC_THUMBNAIL_TEXTS) se quema sobre la portada con
# tipografía condensada negrita, blanco con contorno negro grueso y
# sombra rosa neón, centrado sobre el gameplay (y=780: bajo la
# webcam RDJavi, 0-730, sin tapar la cara). La condensada es Noto
# Sans Condensed Bold (en disco); Barlow Condensed (la del panel)
# no está instalada en la máquina de render: si algún día lo está,
# THUMBNAIL_TEXT_FONTFILE puede apuntar a ella.
THUMBNAIL_TEXT_FONTFILE = ("/usr/share/fonts/truetype/noto/"
                           "NotoSans-CondensedBold.ttf")
THUMBNAIL_TEXT_FONTFILE_FALLBACK = ("/usr/share/fonts/truetype/dejavu/"
                                    "DejaVuSans-Bold.ttf")
THUMBNAIL_TEXT_FONTSIZE = 120
THUMBNAIL_TEXT_Y = 780
THUMBNAIL_TEXT_SHADOW_COLOR = "#FF2E88"  # rosa neón Vice City


def _drawtext_escape(text):
    """Escapa el texto para drawtext (\": \\ ')."""
    return (str(text).replace("\\", "\\\\").replace(":", "\\:")
            .replace("'", "\\'"))


def thumbnail_with_text(frame_path, text, out_path=None, fontsize=None,
                        y=None):
    """Quema el texto de miniatura (estilo GTA 6) sobre un fotograma.

    frame_path: portada 1080x1920 (la elegida por Gemini o el payoff).
    text: texto del momento, 1-4 palabras en MAYÚSCULAS, que nombra
    el momento concreto (no una exclamación genérica: ver
    metadata.GENERIC_THUMBNAIL_TEXTS).
    Devuelve {"path", "text", "fontfile"}. El texto va centrado,
    condensado negrita, blanco con contorno negro y sombra rosa
    neón, sobre el gameplay (no tapa la cara del streamer).
    """
    from .metadata import GENERIC_THUMBNAIL_TEXTS, _norm_title
    frame_path = Path(frame_path)
    text = str(text or "").strip()
    words = text.replace("¡", "").replace("!", "").split()
    generic = _norm_title(text).upper() in {
        _norm_title(t).upper() for t in GENERIC_THUMBNAIL_TEXTS}
    if (not text or not 1 <= len(words) <= 4 or generic
            or text != text.upper()):
        raise ValueError(
            f"texto de miniatura no válido: {text!r} (1-4 palabras en "
            "MAYÚSCULAS que nombren el momento, no una exclamación "
            "genérica)")
    fontfile = (THUMBNAIL_TEXT_FONTFILE
                if Path(THUMBNAIL_TEXT_FONTFILE).exists()
                else THUMBNAIL_TEXT_FONTFILE_FALLBACK)
    out_path = Path(out_path) if out_path else frame_path.with_name(
        frame_path.stem + "_thumb.jpg")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    size = fontsize or THUMBNAIL_TEXT_FONTSIZE
    top = THUMBNAIL_TEXT_Y if y is None else y
    vf = (f"drawtext=fontfile={fontfile}:text='{_drawtext_escape(text)}':"
          f"fontsize={size}:fontcolor=white:borderw=8:bordercolor=black:"
          f"shadowcolor={THUMBNAIL_TEXT_SHADOW_COLOR}:shadowx=0:shadowy=8:"
          f"x=(w-text_w)/2:y={top}")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(frame_path),
                    "-vf", vf, "-frames:v", "1", str(out_path)],
                   check=True)
    return {"path": str(out_path), "text": str(text), "fontfile": fontfile}


def thumbnail_candidates_for_clip(clip_path, moment_card=None, out_dir=None):
    """Atajo: saca los frames desde la ficha del momento (payoff real)."""
    payoff = None
    if moment_card:
        payoff = moment_card.get("payoff_ts_clip")
    return extract_thumbnail_frames(clip_path, payoff_ts_clip=payoff, out_dir=out_dir)


def main():
    p = argparse.ArgumentParser(description="Miniatura real: 3 fotogramas del clip alrededor del payoff.")
    p.add_argument("clip", help="Clip .mp4")
    p.add_argument("--payoff", type=float, default=None, help="Payoff en s dentro del clip")
    p.add_argument("--card", help="moment_card.json (usa su payoff_ts_clip)")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--cover", action="store_true", help="Además, generar cover_instagram.jpg")
    p.add_argument("--vision", action="store_true",
                   help="Gemini elige el mejor frame para el título (apagado por defecto; necesita GEMINI_API_KEY)")
    p.add_argument("--title", default=None, help="Título del clip, para que la visión elija el frame que mejor va con él")
    p.add_argument("--game", default=None, help="Juego del clip")
    p.add_argument("--model", default=None, help="Modelo de Gemini (default: gemini-2.5-flash-lite)")
    p.add_argument("--vision-out", default=None, help="Dónde guardar el resultado de la visión (default: <out-dir>/vision.json)")
    p.add_argument("--text", default=None, help="Texto de miniatura estilo GTA 6 (1-4 palabras en MAYÚSCULAS que nombren el momento): lo quema sobre la portada con tipografía condensada neón")
    args = p.parse_args()

    payoff = args.payoff
    card = None
    if args.card:
        card = json.loads(Path(args.card).read_text(encoding="utf-8"))
        payoff = card.get("payoff_ts_clip")
        print(f"Payoff de la ficha: {payoff} s (evidence: {card.get('evidence')})")
    frames = extract_thumbnail_frames(args.clip, payoff_ts_clip=payoff,
                                      out_dir=args.out_dir)
    for f in frames:
        print(f"  {f['role']}: {f['ts_clip']} s -> {f['path']}")
    vision_choice, vision_result = None, None
    if args.vision:
        title = args.title
        if title is None and card:
            title = (card.get("best_title") or
                     (card.get("titles") or [None])[0])
        vision_choice, vision_result = choose_cover_frame(
            frames, title=title, game=args.game or (card or {}).get("game"),
            use_vision=True, model=args.model)
        print(f"  Visión: {vision_result['vision']} "
              f"(mejor frame: {vision_result['best_index'] + 1}, "
              f"ranking: {vision_result['ranking']}) — {vision_result.get('reason')}")
        out_dir = Path(args.out_dir) if args.out_dir else Path(frames[0]["path"]).parent
        vision_path = Path(args.vision_out) if args.vision_out else out_dir / "vision.json"
        vision_path.write_text(json.dumps(vision_result, indent=2, ensure_ascii=False),
                               encoding="utf-8")
        print(f"  Visión guardada en: {vision_path}")
    if args.cover:
        cover = instagram_cover(frames, (Path(args.out_dir) / "cover_instagram.jpg")
                                if args.out_dir else None,
                                vision_choice=vision_choice,
                                vision_result=vision_result)
        print(f"  Portada IG: {cover['path']} ({cover['note']})")
        if args.text:
            thumb = thumbnail_with_text(cover["path"], args.text)
            print(f"  Miniatura GTA 6: {thumb['path']} (texto: {thumb['text']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
