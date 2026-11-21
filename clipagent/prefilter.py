#!/usr/bin/env python3
"""Pre-filtro rápido de Óscar Boix (2026-11-18, autorizado por el usuario).

Origen: el ``clips_chat.py`` del reel de @oscarboix.ia (buscador de
clips de GTA 6 por el chat), pegado por el usuario el 2026-11-18.
La idea, tal cual: cuando pasa algo en directo, el chat explota.

    1 punto por mensaje, 2 si es una reacción
    (ja{2,}|j{2,}a|xd+|kekw|lul|omegalul|lmao+|clip|noo+|wtf|pog|emojis).
    Tramos de 10 s. Un pico es un tramo >= 3x sobre la mediana de
    sus vecinos de ±5 min. Separación mínima 90 s entre picos.

Dónde va en el pipeline (``auto_clips.py``): puerta BARATA entre la
detección (``score_windows`` + ``select_top_peaks``) y el trabajo
caro por pico (descarga del segmento, Whisper, viralidad, SEO y
render RDJavi). Solo los picos que pasan el pre-filtro llegan a
``filter_candidates`` y al bucle de corte. No se toca nada más:

  * NO reemplaza ``hype_score`` v2 ni el score de viralidad de
    7 componentes ni la fórmula de prioridad
    (viralidad^0,5 x SEO^0,3 x chat_norm^0,2) ni los filtros duros
    (viralidad >= 45, SEO >= 80).
  * NO cambia la duración (40-60 s), los subtítulos (siempre y
    completos) ni el layout (RDJavi). El corte simple de Óscar
    (fondo difuminado) NO se agrega: el look final sigue siendo
    RDJavi.

Salvaguarda de chat caliente (medido 2026-11-18, VOD Rubius
2932415021): en un directo con el chat siempre caliente, el
múltiplo en puntos crudos se comprime y 3x es inalcanzable — la
mediana global era 110 pts/10 s, el máximo 173, el mayor múltiplo
2,23x y NINGÚN tramo llegó a 3x; los picos buenos conocidos
(3220: 1,19x, viralidad 53,8; 7775: 1,87x, viralidad 52,5) tampoco.
Si el pre-filtro no encuentra NINGÚN pico en todo el VOD, se
desactiva solo para ese VOD (``active: False``) y deja pasar todos
los picos seleccionados a la puntuación completa: el pre-filtro
está para descartar rápido los flojos, nunca para tumbar un VOD
entero. Los filtros que ya existen (v2.5, ``filter_passed``) siguen
mandando en ese caso.

Sin dependencias externas, sin red. Solo stdlib.
"""

import json
import re
import statistics

from .detect import fallback_way_passes, hype_way_passes

# Regex de reacciones del clips_chat.py original, tal cual la pegó el
# usuario (no la versión posterior de LAUGH_RE en clips_chat.py del
# proyecto, que es otra lista). 2 puntos si casa, 1 si no.
REACTION_RE = re.compile(
    r"(ja){2,}|j{2,}a|xd+|kekw|lul|omegalul|lmao+|clip|noo+|wtf|pog|😂|🤣|💀",
    re.IGNORECASE,
)

# Defaults de Óscar: tramo 10 s, mínimo 3x la mediana, separación 90 s,
# vecinos de ±5 min (300 s) a cada lado.
WINDOW_S = 10
MIN_MULTIPLE = 3.0
SEPARATION_S = 90
NEIGHBOR_HALF_S = 300


def is_reaction(text: str) -> bool:
    """True si el mensaje es una reacción (vale doble en el conteo)."""
    return bool(REACTION_RE.search(text or ""))


def reaction_weight(text: str) -> int:
    """Peso Óscar: 2 si es reacción, 1 si es un mensaje normal."""
    return 2 if is_reaction(text) else 1


def count_windows(messages, window: int = WINDOW_S):
    """Puntos por tramo de ``window`` segundos.

    ``messages``: iterable de (offset_segundos, texto). Devuelve la
    lista de puntos por tramo, INCLUIDOS los tramos vacíos (0), como
    el original: si no, la mediana de los vecinos se infla. Un
    tramo es ``int(offset // window)``.
    """
    points = []
    for offset, text in messages:
        bucket = int(float(offset) // window)
        while len(points) <= bucket:
            points.append(0.0)
        points[bucket] += reaction_weight(text)
    return points


def multiples_per_bucket(points, window: int = WINDOW_S,
                         neighbor_half_s: int = NEIGHBOR_HALF_S):
    """Múltiplo (puntos / mediana de vecinos ±neighbor_half_s) por tramo.

    La mediana tiene suelo 1,0. Es la medida que usa
    ``find_prefilter_peaks``; se expone aparte para que la puerta
    pueda registrar el múltiplo del PROPIO tramo de un pico
    descartado (no el del tramo más cercano).
    """
    radio = max(1, int(neighbor_half_s // window))
    out = []
    for i, v in enumerate(points):
        neighbors = points[max(0, i - radio):i] + points[i + 1:i + 1 + radio]
        median = statistics.median(neighbors) if neighbors else 0.0
        out.append(v / max(median, 1.0))
    return out


def find_prefilter_peaks(points, window: int = WINDOW_S,
                         min_multiple: float = MIN_MULTIPLE,
                         separation: int = SEPARATION_S,
                         neighbor_half_s: int = NEIGHBOR_HALF_S):
    """Picos Óscar sobre los puntos por tramo.

    Un tramo es pico si ``puntos / mediana(vecinos ±neighbor_half_s)
    >= min_multiple`` (la mediana tiene suelo 1,0). Los candidatos
    se ordenan por múltiplo (mayor primero) y se eligen con
    separación mínima ``separation`` entre picos. Devuelve la lista
    ordenada por tiempo: [{peak_sec, window_start_sec, points,
    median, multiple}]. ``peak_sec`` es el inicio del tramo, la
    misma rejilla que usan ``select_top_peaks`` y el manifiesto.

    OJO: ``separation`` es la separación entre picos DEL PRE-FILTRO,
    no entre clips finales. En ``auto_clips.py`` la puerta se llama
    con ``separation = min(--min-separation, config)``: el pre-filtro
    nunca puede dedupar más agresivo que ``select_top_peaks``, que
    ya eligió los picos. Con el default de Óscar (90 s) un pico real
    a 50 s de otro (40 s y 90 s en el E2E de test_auto_clips_filter)
    se perdería.
    """
    if not points:
        return []
    multiples = multiples_per_bucket(points, window, neighbor_half_s)
    candidates = []
    for i, v in enumerate(points):
        if multiples[i] >= min_multiple:
            neighbors = points[max(0, i - max(1, int(neighbor_half_s // window))):i] \
                + points[i + 1:i + 1 + max(1, int(neighbor_half_s // window))]
            median = statistics.median(neighbors) if neighbors else 0.0
            candidates.append((multiples[i], i, v, median))
    candidates.sort(reverse=True)
    chosen = []
    for multiple, i, v, median in candidates:
        if all(abs(i - j) * window >= separation for _, j, _, _ in chosen):
            chosen.append((multiple, i, v, median))
    return sorted(
        ({"peak_sec": i * window, "window_start_sec": i * window,
          "points": v, "median": round(median, 1),
          "multiple": round(multiple, 2)}
         for multiple, i, v, median in chosen),
        key=lambda p: p["peak_sec"])


def load_chat_messages(chat_path: str):
    """[(offset_segundos, texto)] desde un chat.json de TwitchDownloader.

    Mismo formato que ``clipagent.ingest._load_chat`` (sin usuarios
    ni caché: el pre-filtro solo necesita offset y texto).
    """
    with open(chat_path, encoding="utf-8") as f:
        data = json.load(f)
    messages = []
    for comment in data.get("comments", []):
        offset = comment.get("content_offset_seconds")
        if offset is None:
            continue
        body = (comment.get("message") or {}).get("body", "") or ""
        messages.append((float(offset), body))
    messages.sort(key=lambda m: m[0])
    return messages


def prefilter_peaks_from_chat(chat_path: str, window: int = WINDOW_S,
                              min_multiple: float = MIN_MULTIPLE,
                              separation: int = SEPARATION_S):
    """Atajo: picos del pre-filtro directamente desde un chat.json."""
    points = count_windows(load_chat_messages(chat_path), window=window)
    return find_prefilter_peaks(points, window=window,
                                min_multiple=min_multiple,
                                separation=separation)


def prefilter_gate(peaks, prefilter_peaks, window: int = WINDOW_S,
                   tolerance_windows: int = 1, multiples=None,
                   detail=None):
    """Puerta del pre-filtro sobre los picos ya seleccionados.

    ``peaks``: [(peak_ts, score)] de ``select_top_peaks``.
    ``prefilter_peaks``: lista de ``find_prefilter_peaks``.
    ``multiples``: lista opcional de ``multiples_per_bucket`` para
    registrar en cada descarte el múltiplo de su PROPIO tramo.
    ``detail``: detail de score_windows (opcional; sin él solo se
    evalúa la vía del múltiplo). Un pico pasa si su tramo
    (``peak_ts // window``) está a ``tolerance_windows`` tramos o
    menos de un tramo del pre-filtro (tolerancia ±1 tramo: la rejilla
    deslizante de v2.5 es de 5 s y el pre-filtro de 10 s, un pico real
    puede caer en el tramo vecino) O por la vía hype v2 (chat
    caliente: hype_ratio >= 1,3 + score >= 14 + hype_score >= 1,0,
    mismos umbrales que la puerta live) — en un chat siempre caliente
    ningún tramo llega a 3x y la vía del múltiplo tumbaría picos
    buenos conocidos (Rubius 3220: 1,19x; 7775: 1,87x).
    Devuelve (passed, discarded, summary):

      * passed: [(peak_ts, score)] en el mismo orden de entrada.
      * discarded: [{"peak", "score", "multiple", "rank", "motivos"}]
        con la misma forma que ``filter_candidates`` para que el
        manifiesto los registre igual.
      * summary: {"active", "prefilter_peaks", "passed", "discarded",
        "reason"} — si el pre-filtro no encontró ningún pico en el
        VOD, ``active`` es False y ``passed`` son TODOS los picos
        (salvaguarda de chat caliente, ver docstring del módulo).
    """
    passed_buckets = {int(p["peak_sec"] // window) for p in prefilter_peaks}
    if not passed_buckets:
        return list(peaks), [], {
            "active": False,
            "prefilter_peaks": [],
            "passed": len(peaks),
            "discarded": 0,
            "reason": ("pre-filtro sin picos en el VOD (chat caliente: "
                       "ningún tramo llega a 3x la mediana); se desactiva "
                       "solo y todos los picos pasan a la puntuación "
                       "completa"),
        }
    nearest = {}
    for peak_ts, _score in peaks:
        bucket = int(peak_ts // window)
        best = None
        for p in prefilter_peaks:
            d = abs(int(p["peak_sec"] // window) - bucket)
            if best is None or d < best[0]:
                best = (d, p)
        nearest[peak_ts] = best
    passed, discarded = [], []
    for peak_ts, score in peaks:
        dist, closest = nearest[peak_ts]
        via_hype = hype_way_passes(
            (detail or {}).get(peak_ts) if detail else None, score)
        if dist <= tolerance_windows or via_hype:
            passed.append((peak_ts, score))
        else:
            bucket = int(peak_ts // window)
            own = (round(multiples[bucket], 2)
                   if multiples is not None and 0 <= bucket < len(multiples)
                   else closest["multiple"])
            motivos = [
                "pre-filtro Óscar: sin tramo >= 3x la mediana en "
                f"±{tolerance_windows} tramo(s) "
                f"(tramo propio {peak_ts}s, {own}x; tramo más cercano "
                f"{closest['peak_sec']}s, {closest['multiple']}x)"]
            if detail is not None:
                d = (detail or {}).get(peak_ts) or {}
                motivos.append(
                    "sin ráfaga hype "
                    f"(hype_ratio {d.get('hype_ratio')}, "
                    f"score {score}, hype_score {d.get('hype_score')})")
            discarded.append({
                "peak": peak_ts, "score": score,
                "multiple": own,
                "rank": None,
                "motivos": motivos,
            })
    if not passed and discarded and detail is not None:
        # Salvaguarda anti-cero (como el best_fallback live): si
        # ningún pico pasó la puerta principal, se re-evalúan con la
        # vía relajada (hype_ratio >= 1,2 + score >= 13).
        rescued, still = [], []
        for dc in discarded:
            if fallback_way_passes((detail or {}).get(dc["peak"]),
                                  dc["score"]):
                rescued.append((dc["peak"], dc["score"]))
            else:
                d = (detail or {}).get(dc["peak"]) or {}
                dc["motivos"] = dc["motivos"] + [
                    "sin ráfaga ni siquiera relajada "
                    f"(hype_ratio {d.get('hype_ratio')}, "
                    f"score {dc['score']})"]
                still.append(dc)
        if rescued:
            passed, discarded = rescued, still
    return passed, discarded, {
        "active": True,
        "prefilter_peaks": [p["peak_sec"] for p in prefilter_peaks],
        "passed": len(passed),
        "discarded": len(discarded),
        "reason": (f"{len(prefilter_peaks)} tramo(s) >= 3x la mediana; "
                   f"{len(passed)} pico(s) pasan, {len(discarded)} "
                   "descartado(s) antes de Whisper/viralidad/SEO/render"),
    }
