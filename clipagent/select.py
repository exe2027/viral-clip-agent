"""Selección de momentos con LLM (Fase 7.1, opcional con fallback).

Entrada: transcripción por ventana + 15-20 candidatos del chat.
Salida: JSON validado [{start, end, hook, por_que_viral, score}].
Una sola llamada al LLM con todos los candidatos; si no hay LLM,
si falla, o si la respuesta no valida, se sigue con los candidatos
del chat tal cual (fallback): el LLM es una señal más del score de
viralidad existente, nunca lo reemplaza ni bloquea el pipeline.

El score del LLM (0-100) alimenta el componente ``hook`` del score
de viralidad (0-10): sin LLM ese componente es la estimación
conservadora 5,0; con LLM es la medida. Las fórmulas y los pesos
no cambian (ver 6B).
"""
import json

MAX_CANDIDATES = 20
MAX_TRANSCRIPT_CHARS = 1500
MIN_CLIP_S = 10
MAX_CLIP_S = 90
MATCH_TOLERANCE_S = 30


def transcript_text(transcript):
    """Texto plano de una transcripción faster-whisper (dict)."""
    if transcript is None:
        return ""
    if isinstance(transcript, str):
        return transcript[:MAX_TRANSCRIPT_CHARS]
    parts = []
    for seg in transcript.get("segments", []):
        text = (seg.get("text") or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts)[:MAX_TRANSCRIPT_CHARS]


def build_selection_prompt(candidates, lang="es"):
    """Prompt con los candidatos: ventana + transcripción + chat."""
    lines = [
        "Eres un editor de clips virales de directos de Twitch/Kick.",
        "Te doy los momentos con más chat de un VOD, cada uno con su "
        "transcripción. Elige los momentos más virales y devuelve SOLO "
        "un array JSON, sin texto fuera del JSON, con este formato:",
        '[{"start": <segundos en el VOD>, "end": <segundos en el VOD>, '
        '"hook": "<frase gancho, máx. 80 caracteres>", '
        '"por_que_viral": "<motivo en una frase>", '
        '"score": <0-100>}]',
        "Reglas: start y end dentro de la ventana del candidato; el "
        "clip dura entre 10 y 90 segundos; score 0-100 (100 = clip "
        "seguro viral); como mucho una selección por candidato; no "
        "inventes lo que no está en la transcripción ni en el chat; "
        "si ningún momento vale, devuelve [].",
        "",
        "Candidatos:",
    ]
    for i, c in enumerate(candidates[:MAX_CANDIDATES], 1):
        lines.append(
            f"{i}. ventana {c['start']:.0f}-{c['end']:.0f}s | "
            f"chat {c.get('chat_score', 0):.0f} pts "
            f"({c.get('chat_multiple', 1.0):.1f}x la mediana, "
            f"{c.get('risas', 0)} risas, {c.get('hype', 0)} hype) | "
            f"transcripción: {transcript_text(c.get('transcript')) or '(sin transcripción)'}")
    return "\n".join(lines)


def parse_selection(text, candidates):
    """Valida la respuesta del LLM y la casa con los candidatos.

    Devuelve [{candidate_index, start, end, hook, por_que_viral,
    score}]. Las entradas inválidas o fuera de ventana se descartan
    (nunca se inventa un momento). `start`/`end` se recortan a la
    ventana del candidato.
    """
    if not text:
        return []
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        raw = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    out = []
    used = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            start_s = float(entry["start"])
            end_s = float(entry["end"])
            score = float(entry["score"])
        except (KeyError, TypeError, ValueError):
            continue
        hook = str(entry.get("hook") or "").strip()[:80]
        why = str(entry.get("por_que_viral") or "").strip()
        if not hook or not why:
            continue
        if not 0 <= score <= 100:
            continue
        # El candidato más cercano por inicio (tolerancia 30 s).
        best, best_dist = None, None
        for idx, c in enumerate(candidates):
            if idx in used:
                continue
            dist = abs(start_s - c["start"])
            if best_dist is None or dist < best_dist:
                best, best_dist = idx, dist
        if best is None or best_dist > MATCH_TOLERANCE_S:
            continue
        cand = candidates[best]
        start_s = max(cand["start"], min(start_s, cand["end"] - 1))
        end_s = max(start_s + 1, min(end_s, cand["end"]))
        if not MIN_CLIP_S <= end_s - start_s <= MAX_CLIP_S:
            continue
        used.add(best)
        out.append({"candidate_index": best, "start": round(start_s, 1),
                    "end": round(end_s, 1), "hook": hook,
                    "por_que_viral": why, "score": round(score, 1)})
    return out


def select_moments(candidates, client):
    """Una llamada al LLM; fallback a los candidatos del chat.

    Devuelve (selections, used_llm): `selections` es la salida de
    parse_selection (vacía si no hubo LLM o no validó); `used_llm`
    dice si el LLM participó. Nunca levanta excepciones.
    """
    if not candidates:
        return [], False
    if client is None or not client.available():
        return [], False
    try:
        text = client.generate(build_selection_prompt(candidates))
    except Exception:  # noqa: BLE001 - el fallback manda
        return [], False
    selections = parse_selection(text, candidates)
    return selections, True


def apply_llm_selection(manifest, selections, score_inputs):
    """Aplica las selecciones al manifiesto (post-corte, 7.1).

    `score_inputs`: {peak_ts: (detail, multiple, rank, human, audio)}
    con los datos que usó score_clip. Para cada clip seleccionado:
    guarda clip["llm"] (score, hook, motivo, ventana) y recalcula
    su viral_score con hook = score LLM / 10 — SOLO si el hook era
    una estimación (sin entrada humana): el humano manda siempre.
    Devuelve cuántos clips se actualizaron.
    """
    from .detect import score_clip
    clips = manifest.get("clips", [])
    updated = 0
    for sel in selections:
        idx = sel["candidate_index"]
        if idx >= len(clips):
            continue
        clip = clips[idx]
        clip["llm"] = {"score": sel["score"], "hook": sel["hook"],
                       "por_que_viral": sel["por_que_viral"],
                       "start": sel["start"], "end": sel["end"]}
        # 7.5: el hook del LLM (de la transcripción real) pasa a ser
        # el hook del paquete: es el que se usa en la metadata.
        viral = clip.get("viral")
        if isinstance(viral, dict):
            viral["hook_first_line"] = sel["hook"]
        peak = clip.get("peak")
        if peak not in score_inputs:
            continue
        detail, multiple, rank, human, audio = score_inputs[peak]
        human = dict(human or {})
        hook_entry = human.get("hook")
        hook_is_estimated = hook_entry is None
        if hook_is_estimated:
            human["hook"] = {"value": round(sel["score"] / 10.0, 2),
                             "reason": "selección LLM (7.1)"}
            new_score = score_clip(detail, multiple, rank=rank,
                                   human=human, audio=audio)
            clip["viral_score"] = new_score
            updated += 1
    return updated
