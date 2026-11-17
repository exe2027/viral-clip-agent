#!/usr/bin/env python3
"""heatmap.py — curva de "lo más repetido" de YouTube (idea de Uri).

Origen: clipper-directos de Uri Cortina (``scripts/hype.py``): la
curva de hype combina el heatmap de YouTube (lo más repetido del
VOD) con picos de voz y velocidad del chat. Aquí solo se toma el
heatmap, como SEÑAL DIAGNÓSTICA, no como parte de la fórmula de
viralidad ni de prioridad (MÁSTER PROMPT FINAL, R1: el sistema de
puntaje es intocable en fórmulas, pesos y umbrales).

Qué hace:
  * ``peaks_from_heatmap_data`` (puro, sin red): lee el campo
    ``heatmap`` de un ``yt-dlp -J`` (lista de {start_time,
    end_time, value 0..1}), lo expande a buckets de 10 s, lo
    normaliza a 0..1 por el máximo y devuelve los picos top con
    separación mínima entre ellos (60 s por defecto, como Uri).
  * ``heatmap_for_url``: corre ``yt-dlp --skip-download -J <url>``
    y parsea lo anterior. NUNCA levanta excepciones ni rompe el
    pipeline: sin yt-dlp, sin heatmap (YouTube no lo da en todos
    los VODs; Twitch/Kick no lo tienen) o con cualquier error,
    devuelve ``available: False`` con el motivo, y el resto del
    pipeline sigue solo con el chat.

Uso:
    from .heatmap import heatmap_for_url, peaks_from_heatmap_data
    h = heatmap_for_url("https://www.youtube.com/watch?v=...")
    # h = {"available": True, "source": "youtube_heatmap",
    #      "peaks": [{"t": 1230, "score": 0.92, "hms": "0:20:30"}, ...],
    #      "reason": "heatmap de YouTube: 3 picos"}

$0 y sin credenciales: yt-dlp es local y el heatmap viene en el
JSON de metadatos, sin key ni login.
"""

import json
import subprocess

BUCKET_S = 10            # ancho del bucket, como Uri (10 s)
DEFAULT_TOP_N = 15       # picos a devolver, como Uri
DEFAULT_MIN_SEPARATION_S = 60  # picos a >= 60 s entre sí, como Uri
YOUTUBE_HOSTS = ("youtube.com", "youtu.be")


def _hms(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def peaks_from_heatmap_data(data, top_n=DEFAULT_TOP_N,
                            min_separation=DEFAULT_MIN_SEPARATION_S,
                            bucket_s=BUCKET_S):
    """Picos top del heatmap de un JSON de yt-dlp (puro, sin red).

    ``data`` es el dict de ``yt-dlp -J`` (o solo su campo
    ``heatmap``). Devuelve (picos, motivo): picos es una lista de
    {"t", "score", "hms"} con ``score`` normalizado 0..1 por el
    máximo del VOD (el heatmap ya viene 0..1, pero la suma por
    bucket puede pasar de 1 si un bucket lo cubren varios tramos;
    normalizar lo deja comparable entre VODs). Sin heatmap o con
    valores vacíos devuelve ([], motivo).
    """
    heatmap = (data or {}).get("heatmap") or []
    buckets = {}
    for entry in heatmap:
        try:
            start = int(float(entry.get("start_time", 0)))
            end = int(float(entry.get("end_time", start)))
            value = float(entry.get("value", 0))
        except (AttributeError, TypeError, ValueError):
            continue
        if value <= 0 or end <= start:
            continue
        for t in range(start, end, bucket_s):
            bucket = (t // bucket_s) * bucket_s
            buckets[bucket] = buckets.get(bucket, 0.0) + value
    if not buckets:
        return [], "sin heatmap en los metadatos (YouTube no lo da en todos los VODs)"
    peak_value = max(buckets.values())
    ranked = sorted(buckets.items(), key=lambda kv: -kv[1])
    picked = []
    for bucket, value in ranked:
        if all(abs(bucket - p["t"]) >= min_separation for p in picked):
            picked.append({"t": bucket,
                           "score": round(value / peak_value, 3),
                           "hms": _hms(bucket)})
        if len(picked) >= top_n:
            break
    picked.sort(key=lambda p: p["t"])
    return picked, (f"heatmap de YouTube: {len(picked)} pico(s) "
                    f"de {len(buckets)} buckets de {bucket_s} s")


def heatmap_for_url(url, top_n=DEFAULT_TOP_N,
                    min_separation=DEFAULT_MIN_SEPARATION_S,
                    runner=None, timeout=60):
    """Heatmap de una URL de YouTube con yt-dlp (nunca falla).

    ``runner`` es una función (cmd, timeout) -> stdout (para tests:
    inyectar un fake; por defecto subprocess). Devuelve un dict con
    ``available``, ``source``, ``peaks`` y ``reason``. Sin yt-dlp,
    sin red, con URL no YouTube o con JSON inválido: available
    False y el pipeline sigue sin heatmap.
    """
    url = (url or "").strip()
    if not any(host in url for host in YOUTUBE_HOSTS):
        return {"available": False, "source": "youtube_heatmap",
                "peaks": [],
                "reason": "no es una URL de YouTube (el heatmap solo existe ahí)"}
    if runner is None:
        def runner(cmd, timeout):
            return subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout).stdout
    try:
        out = runner(["yt-dlp", "--skip-download", "-J", url],
                     timeout=timeout)
        data = json.loads(out or "{}")
    except FileNotFoundError:
        return {"available": False, "source": "youtube_heatmap",
                "peaks": [], "reason": "yt-dlp no está instalado"}
    except (subprocess.TimeoutExpired, json.JSONDecodeError,
            OSError, ValueError) as exc:
        return {"available": False, "source": "youtube_heatmap",
                "peaks": [], "reason": f"no se pudo leer el heatmap: {exc}"}
    peaks, reason = peaks_from_heatmap_data(
        data, top_n=top_n, min_separation=min_separation)
    return {"available": bool(peaks), "source": "youtube_heatmap",
            "peaks": peaks, "reason": reason}


def nearest_heatmap_peak(peaks, timestamp, max_gap_s=30):
    """Pico de heatmap más cercano a un instante (o None).

    ``peaks`` es la lista de ``peaks_from_heatmap_data``. Solo
    cuenta si está a <= ``max_gap_s``: un pico de heatmap a 3
    minutos del pico de chat no confirma nada.
    """
    if not peaks or timestamp is None:
        return None
    best = min(peaks, key=lambda p: abs(p["t"] - float(timestamp)))
    if abs(best["t"] - float(timestamp)) > max_gap_s:
        return None
    return {"t": best["t"], "score": best["score"],
            "gap_s": round(abs(best["t"] - float(timestamp)), 1)}
