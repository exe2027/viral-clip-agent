"""Dedup de clips (Fase 7.3).

Sin clips solapados del mismo VOD ni el mismo momento de dos VODs:
- Mismo VOD: las ventanas [start, start+duration] del manifiesto
  no se pueden solapar (ventana temporal exacta). La huella NO se
  usa dentro del mismo VOD: dos momentos distintos pueden verse
  igual (misma escena, mismo menú) y son momentos distintos.
- Dos VODs: hash perceptual de 3 frames (inicio, medio, fin) por
  clip. Dos clips del mismo momento tienen los mismos frames aunque
  vengan de VODs distintos (re-subidas, VODs solapados del mismo
  directo). El hash es un aHash 16x16 en gris, sin dependencias
  nuevas (ffmpeg saca los frames, el resto es Python puro), y exige
  los 3 frames cercanos: un 8x8 con 2 de 3 colisionaba en vídeo
  sintético periódico (testsrc).

La huella se guarda en el manifiesto (clip["fingerprint"], 3
enteros en hexadecimal) para no re-calcularla en cada corrida.
"""
import os
import subprocess

HASH_SIZE = 16
FRAME_MAX_DIST = 8
MIN_MATCHING_FRAMES = 3
MAX_OTHER_RUNS = 10


def frame_hash(video_path, t, size=HASH_SIZE):
    """aHash 8x8 de un frame en el segundo `t`. None si no se puede."""
    if not os.path.isfile(video_path):
        return None
    cmd = ["ffmpeg", "-v", "error", "-ss", str(t), "-i", video_path,
           "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray",
           "-s", f"{size}x{size}", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=30).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    if len(raw) < size * size:
        return None
    pixels = list(raw[:size * size])
    mean = sum(pixels) / len(pixels)
    bits = 0
    for px in pixels:
        bits = (bits << 1) | (1 if px > mean else 0)
    return bits


def clip_fingerprint(video_path, duration):
    """(h_inicio, h_medio, h_fin) o None si no se pudo medir."""
    if not duration or duration <= 0:
        return None
    times = [1.0, duration / 2.0, max(1.0, duration - 1.0)]
    hashes = [frame_hash(video_path, t) for t in times]
    if any(h is None for h in hashes):
        return None
    return tuple(hashes)


def hash_distance(h1, h2):
    """Distancia de Hamming entre dos hashes (bits distintos)."""
    return bin(h1 ^ h2).count("1")


def is_duplicate(fp1, fp2, max_dist=FRAME_MAX_DIST):
    """¿Mismo momento? Los 3 frames coinciden (16x16, <=8 bits)."""
    if fp1 is None or fp2 is None:
        return False
    close = sum(1 for a, b in zip(fp1, fp2)
                if hash_distance(a, b) <= max_dist)
    return close >= MIN_MATCHING_FRAMES


def windows_overlap(a_start, a_end, b_start, b_end):
    """¿Se solapan dos ventanas [start, end]?"""
    return a_start < b_end and b_start < a_end


def _fingerprint_of(entry):
    fp = entry.get("fingerprint")
    if not fp:
        return None
    try:
        return tuple(int(h, 16) if isinstance(h, str) else int(h)
                     for h in fp)
    except (TypeError, ValueError):
        return None


def _entries_from_manifests(runs_dir, exclude_vod=None,
                            exclude_path=None):
    """(vod_id, clip_entry) de los manifiestos en runs/ (recientes).

    `exclude_path` es el manifiesto de la corrida actual: sin él,
    la propia corrida se trataría como "otro VOD" (su directorio
    no coincide con `vod_id` cuando este es None o es un id de
    Twitch/Kick) y un clip re-cortado con --fresh se descartaría
    como duplicado de sí mismo.
    """
    if not os.path.isdir(runs_dir):
        return []
    run_dirs = []
    for name in os.listdir(runs_dir):
        path = os.path.join(runs_dir, name)
        manifest = os.path.join(path, "manifest.json")
        if os.path.isdir(path) and os.path.isfile(manifest):
            run_dirs.append((os.path.getmtime(manifest), name, manifest))
    run_dirs.sort(reverse=True)
    out = []
    for _mtime, vod_id, manifest_path in run_dirs[:MAX_OTHER_RUNS]:
        if vod_id == exclude_vod:
            continue
        if exclude_path is not None and os.path.abspath(
                manifest_path) == os.path.abspath(exclude_path):
            continue
        try:
            import json
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError):
            continue
        for clip in manifest.get("clips", []):
            out.append((vod_id, clip))
    return out


def find_duplicate(clip_path, duration, start, runs_dir, vod_id,
                   manifest_clips=None, exclude_manifest=None):
    """Busca un duplicado de un clip recién cortado.

    Devuelve (motivo, clip_duplicado) o (None, None).
    1. Mismo VOD: ventana solapada con un clip ya en el manifiesto.
    2. Dos VODs: huella de 3 frames coincidente con un clip de
       otro VOD (mismo momento capturado en dos VODs).
    `manifest_clips` son las entradas ya cortadas de este VOD;
    `exclude_manifest` es el manifiesto de la corrida actual, que
    nunca se trata como otro VOD (ver _entries_from_manifests).
    """
    end = start + duration
    for entry in manifest_clips or []:
        e_start = entry.get("start", 0)
        e_end = e_start + entry.get("duration", 0)
        if windows_overlap(start, end, e_start, e_end):
            return (f"ventana solapada con {entry.get('file')} "
                    f"({e_start:.0f}-{e_end:.0f}s)", entry)
    fingerprint = clip_fingerprint(clip_path, duration)
    if fingerprint is None:
        return None, None
    for other_vod, entry in _entries_from_manifests(
            runs_dir, exclude_vod=vod_id, exclude_path=exclude_manifest):
        other_fp = _fingerprint_of(entry)
        if other_fp is None:
            continue
        if is_duplicate(fingerprint, other_fp):
            return (f"mismo momento que {entry.get('file')} "
                    f"(otro VOD ({other_vod}), huella de 3 frames)",
                    entry)
    return None, None
