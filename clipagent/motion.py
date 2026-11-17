#!/usr/bin/env python3
"""motion.py — % de juego en movimiento (idea de Uri).

Origen: clipper-directos de Uri Cortina (``scripts/layout.py``):
mide el movimiento por segundo en la zona de juego (``GAME_MOV =
3,0``: cambio medio por segundo por encima del cual el juego "se
mueve"; un tráiler en pausa o un vídeo congelado da ~0) y lo usa
para elegir momentos: el gameplay que se mueve retiene más que una
cara hablando sobre una imagen fija. Aquí se mide igual, como
SEÑAL DIAGNÓSTICA, no como parte de la fórmula de viralidad ni de
prioridad (MÁSTER PROMPT FINAL, R1: el sistema de puntaje es
intocable).

Qué hace:
  * ``measure_motion``: 1 fotograma por segundo en gris a 160x90
    (``render.extract_gray_frames``: UNA sola pasada de ffmpeg,
    sin OpenCV ni dependencias nuevas) y diferencia media absoluta
    entre fotogramas consecutivos. Devuelve ``motion_pct``: % de
    segundos con movimiento por encima del umbral, más la serie
    por segundo y la media.
  * ``motion_flag``: 0-10% de movimiento = vídeo en pausa o
    pantalla fija (criterio de Uri, ``references/criterio.md``):
    solo vale si la reacción es muy fuerte. El flag es un aviso
    en el manifiesto, NUNCA un filtro duro nuevo: no descarta ni
    bloquea nada por sí solo (los filtros duros son viralidad < 50
    y SEO < 90, y no se tocan).

Umbral de "reacción muy fuerte": viralidad >= 85 (banda
"urgente"). Un clip congelado con viralidad menor queda marcado
"pausa_pantalla_fija: revisar a mano, no publicar a ciegas".

$0 y local: solo ffmpeg, que el proyecto ya exige.
"""

# Umbral de movimiento por segundo (diferencia media absoluta
# 0-255 entre fotogramas en gris a 160x90). Adaptado de Uri
# (GAME_MOV = 3,0 a 640 px en BGR): la escala 0-255 es la misma,
# solo cambia la resolución de muestreo.
MOTION_THRESHOLD = 3.0
# 0-10% de segundos con movimiento = vídeo en pausa / pantalla
# fija (criterio de Uri: "un tramo con 0-10% de movimiento es un
# vídeo en pausa: solo vale si la reacción es muy buena").
FROZEN_MAX_PCT = 10.0
# Viralidad mínima para que un clip congelado valga ("reacción
# muy fuerte"): banda "urgente" (>= 85).
STRONG_VIRALITY_MIN = 85.0


def measure_motion(video_path, duration=None, threshold=MOTION_THRESHOLD):
    """% de juego en movimiento de un vídeo (o clip).

    Devuelve {"available", "motion_pct", "mean_diff",
    "motion_per_second", "seconds", "threshold", "reason"}.
    Sin vídeo o si ffmpeg falla: available False y el pipeline
    sigue sin esta señal (nunca se inventa un %).
    """
    from .render import extract_gray_frames

    if duration is None:
        try:
            from .render import _probe_duration
            duration = _probe_duration(video_path)
        except Exception:
            duration = None
    if not duration or duration <= 0:
        return {"available": False, "motion_pct": None, "mean_diff": None,
                "motion_per_second": [], "seconds": 0,
                "threshold": threshold,
                "reason": "sin duración: no se pudo medir el movimiento"}
    frames = extract_gray_frames(video_path, duration)
    if len(frames) < 2:
        return {"available": False, "motion_pct": None, "mean_diff": None,
                "motion_per_second": [], "seconds": len(frames),
                "threshold": threshold,
                "reason": "menos de 2 fotogramas a 1 fps: no se pudo medir"}
    diffs = []
    for prev, cur in zip(frames, frames[1:]):
        total = sum(abs(c - p) for p, c in zip(prev, cur))
        diffs.append(round(total / len(prev), 2))
    moving = sum(1 for d in diffs if d > threshold)
    pct = round(100.0 * moving / len(diffs), 1) if diffs else 0.0
    return {"available": True, "motion_pct": pct,
            "mean_diff": round(sum(diffs) / len(diffs), 2) if diffs else 0.0,
            "motion_per_second": [0.0] + diffs,
            "seconds": len(frames),
            "threshold": threshold,
            "reason": (f"{moving}/{len(diffs)} segundos con movimiento "
                       f"(>{threshold}) -> {pct}%")}


def motion_flag(motion_pct, virality_total=None):
    """Flag diagnóstico para un % de movimiento (nunca un veto).

    Devuelve (flag, motivo):
      * "sin_medir": no hay % (sin vídeo o ffmpeg falló).
      * "en_movimiento": > 10% de segundos con movimiento.
      * "pausa_pero_reaccion_fuerte": <= 10% PERO viralidad >= 85
        (banda urgente): la reacción compensa la imagen fija.
      * "pausa_pantalla_fija": <= 10% y viralidad < 85 (o sin
        viralidad): revisar a mano antes de publicar, no publicar
        a ciegas. No descarta el clip: solo lo marca.
    """
    if motion_pct is None:
        return "sin_medir", "movimiento sin medir (sin vídeo o ffmpeg falló)"
    if motion_pct > FROZEN_MAX_PCT:
        return ("en_movimiento",
                f"{motion_pct}% de segundos con movimiento "
                f"(> {FROZEN_MAX_PCT:.0f}%)")
    if virality_total is not None and virality_total >= STRONG_VIRALITY_MIN:
        return ("pausa_pero_reaccion_fuerte",
                f"solo {motion_pct}% en movimiento (<= {FROZEN_MAX_PCT:.0f}%), "
                f"pero viralidad {virality_total:.0f} >= "
                f"{STRONG_VIRALITY_MIN:.0f} (urgente): la reacción vale")
    return ("pausa_pantalla_fija",
            f"solo {motion_pct}% en movimiento (<= {FROZEN_MAX_PCT:.0f}%): "
            f"vídeo en pausa o pantalla fija; solo vale si la reacción "
            f"es muy fuerte (viralidad >= {STRONG_VIRALITY_MIN:.0f}), "
            f"revisar a mano, no publicar a ciegas")
