"""Validación final del preview con Gemini (2026-11-18).

Reemplaza al preview humano obligatorio: antes de encolar un clip
al panel (LIVE) o a publicar (VOD), Gemini VE el MP4 final ya
renderizado — 4 fotogramas en varios tiempos — y lo valida con un
prompt de juez visual estricto.

Diferencia con ``clipagent/review.py`` (juez pre-render): aquella
revisión puntúa la viralidad de un pico ANTES de gastar render,
con fotogramas de la ventana + transcripción. Esta valida el
producto final DESPUÉS del render: layout split, webcam, título,
subtítulos quemados, barras negras y coherencia con la
transcripción. Un clip puede tener viralidad 60 y aun así salir
mal cortado; eso es lo que frena aquí.

Decisiones (``decision``):
- ``aprobado``: Gemini vio el vídeo y todos los criterios pasan.
- ``rechazado``: Gemini vio el vídeo y algún criterio duro falla.
  El clip NO se encola ni se publica; queda registrado con motivos.
- ``pendiente_revision``: Gemini NO pudo validar (sin key, error
  de red, JSON inválido, sin fotogramas). El clip NO se aprueba
  nunca por esta vía: va a la Cola del panel para revisión humana
  y, en VOD, su job de publicar se encola en modo ``draft`` (nunca
  ``api``). No inventar una aprobación.
- ``desactivada``: la validación está apagada EXPLÍCITAMENTE
  (``FINAL_REVIEW_ENABLED=0`` / ``cfg["final_review"]["enabled"]
  = False``). Por defecto está ENCENDIDA (orden del usuario
  2026-11-18 22:57, 100% automático). No hay veredicto; el flujo
  sigue como antes.

Reglas:
- $0: mismo cliente Gemini Flash-Lite por REST que ``vision.py``
  (sin SDK nuevo), 4 fotogramas por clip, 1 llamada por clip.
- El veredicto NO se fía del ``pasa`` que diga Gemini: se
  re-calcula en código (todos los criterios >= ``MIN_SCORE`` y
  sin fallos duros). Un juez que aprueba con un criterio en 2 no
  aprueba.
- La puerta 50/90 y la fórmula de prioridad no se tocan: esto es
  una puerta visual aparte, después del render.
- Nunca lanza excepción: cualquier fallo devuelve
  ``pendiente_revision`` con el motivo.
"""

import base64
import json
import os
import re

from .vision import MODEL_DEFAULT

# 4 fotogramas del clip final: inicio, payoff, reacción, final.
# El payoff del cerebro cae a los 20 s de la ventana de 40 s, así
# que 0,50 ≈ payoff y 0,72 ≈ reacción posterior al momento.
FRAME_FRACTIONS = (0.05, 0.50, 0.72, 0.95)
FRAME_ROLES = ("inicio", "payoff", "reaccion", "final")
FALLBACK_TIMES = (2.0, 20.0, 29.0, 38.0)  # sin duración medible

# Puntuación mínima por criterio (0-10). Un 5 es mediocre pero
# aceptable; por debajo, el criterio frena el clip.
MIN_SCORE = 5

CRITERIA = (
    "webcam_visible",
    "titulo_legible",
    "subtitulos_ok",
    "sin_barras_negras",
    "momento_comprensible",
    "subtitulos_coherentes",
)

CRITERIA_ES = {
    "webcam_visible": "webcam visible",
    "titulo_legible": "título legible",
    "subtitulos_ok": "subtítulos bien puestos",
    "sin_barras_negras": "sin barras negras",
    "momento_comprensible": "momento comprensible",
    "subtitulos_coherentes": "subtítulos coherentes",
}

FINAL_REVIEW_PROMPT = """Eres el validador final de un canal de clips de GTA 6 en español. Te paso 4 fotogramas REALES del vídeo final ya renderizado, en orden: 1 = inicio, 2 = payoff (el momento), 3 = reacción, 4 = final. El layout es vertical 9:16 en dos bandas: el streamer (webcam) ARRIBA y el gameplay ABAJO. El título es: {title}

La transcripción de lo que se dice en el clip es:
{transcript}

Puntúa cada criterio de 0 a 10 (un decimal, sé exigente: un 5 es mediocre, un 8 es muy bueno, un 10 es excepcional) y explica cada nota en una frase corta:
- webcam_visible: se ve la webcam del streamer en la banda de arriba, con su cara visible, sin que el gameplay invada esa banda ni que la banda quede negra o vacía.
- titulo_legible: el título está en una sola línea, se lee entero sin cortarse, sin duplicarse y sin tapar la cara del streamer ni el gameplay importante.
- subtitulos_ok: los subtítulos amarillos están abajo, se leen bien, no tapan el diálogo del juego ni el minimapa ni el HUD importante, y no se salen de la pantalla.
- sin_barras_negras: no hay barras negras laterales ni arriba/abajo, ni vídeo duplicado, ni la imagen repetida en dos bandas a la vez.
- momento_comprensible: se entiende qué pasa en el clip sin conocer el contexto del directo (se ve la jugada o la reacción que promete el título).
- subtitulos_coherentes: lo que dicen los subtítulos quemados coincide con la transcripción (no inventan palabras ni frases que nadie dijo).

En "fallos" lista SOLO los criterios con nota menor de 5 (los que frenan el clip). "pasa" es true solo si ningún criterio tiene menos de 5.

Responde SOLO con este JSON (sin texto fuera):
{{"criterios": {{"webcam_visible": {{"score": 0.0, "motivo": "..."}}, "titulo_legible": {{"score": 0.0, "motivo": "..."}}, "subtitulos_ok": {{"score": 0.0, "motivo": "..."}}, "sin_barras_negras": {{"score": 0.0, "motivo": "..."}}, "momento_comprensible": {{"score": 0.0, "motivo": "..."}}, "subtitulos_coherentes": {{"score": 0.0, "motivo": "..."}}}}, "fallos": [], "pasa": true, "resumen": "una frase corta en español"}}
"""


def _extract_json(text):
    """Primer objeto JSON de la respuesta (con o sin cercas)."""
    raw = (text or "").strip()
    if not raw:
        return None
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        raw = match.group(1)
    else:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        raw = raw[start:end + 1]
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _parse_final_review(text):
    """Valida el JSON del juez visual.

    Devuelve (scores, motivos, fallos, resumen). Lanza ValueError
    si falta algún criterio, algún score no es número o ``fallos``
    menciona un criterio inexistente. El ``pasa`` de Gemini se
    ignora: el veredicto se re-calcula en código.
    """
    data = _extract_json(text)
    if data is None:
        raise ValueError("Gemini sin JSON válido")
    criterios = data.get("criterios")
    if not isinstance(criterios, dict):
        raise ValueError("Gemini sin 'criterios'")
    scores, motivos = {}, {}
    for name in CRITERIA:
        entry = criterios.get(name)
        if not isinstance(entry, dict):
            raise ValueError(f"Gemini sin criterio {name!r}")
        try:
            value = float(entry.get("score"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Gemini sin score en {name!r}") from exc
        scores[name] = max(0.0, min(10.0, value))
        motivos[name] = str(entry.get("motivo") or "").strip()
    fallos = data.get("fallos") or []
    if not isinstance(fallos, list):
        raise ValueError("Gemini con 'fallos' no lista")
    fallos = [str(f) for f in fallos]
    for f in fallos:
        if f not in CRITERIA:
            raise ValueError(f"Gemini con fallo desconocido {f!r}")
    resumen = str(data.get("resumen") or "").strip()
    return scores, motivos, fallos, resumen


def _decide(scores, fallos):
    """Veredicto en código: todos >= MIN_SCORE y sin fallos."""
    hard = [c for c in CRITERIA if scores.get(c, 0.0) < MIN_SCORE]
    fallos_reales = sorted(set(fallos) | set(hard))
    return fallos_reales


def transcript_text_for_srt(srt_path):
    """Texto plano del .transcript.json hermano de un .srt.

    Reutiliza ``streamer_hype.transcript_text``. "" si no hay
    transcripción en disco (el juez valora entonces solo imagen).
    """
    if not srt_path:
        return ""
    path = os.path.splitext(str(srt_path))[0] + ".transcript.json"
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ""
    from .streamer_hype import transcript_text
    return transcript_text(data)


def extract_final_review_frames(clip_path, out_dir=None, duration=None):
    """4 fotogramas del clip final (inicio/payoff/reacción/final).

    Reutiliza ``thumbnails.get_duration`` y ``_extract_frame``
    (ffmpeg). Devuelve [{"path", "ts_clip", "role"}]. Lanza
    FileNotFoundError si no hay clip y RuntimeError si ffmpeg falla.
    """
    from .thumbnails import _extract_frame, get_duration

    clip_path = str(clip_path)
    if not os.path.isfile(clip_path):
        raise FileNotFoundError(f"clip no encontrado: {clip_path}")
    if duration is None:
        duration = get_duration(clip_path)
    if duration:
        times = [duration * f for f in FRAME_FRACTIONS]
    else:
        times = list(FALLBACK_TIMES)
    out_dir = out_dir or os.path.join(
        os.path.dirname(clip_path), "final_review")
    os.makedirs(out_dir, exist_ok=True)
    frames = []
    for i, (ts, role) in enumerate(zip(times, FRAME_ROLES), start=1):
        ts = max(0.5, ts)
        if duration:
            ts = min(ts, max(0.5, duration - 0.5))
        path = _extract_frame(
            clip_path, ts,
            os.path.join(out_dir, f"final_{i:02d}_{role}_{ts:.1f}s.jpg"))
        frames.append({"path": str(path), "ts_clip": round(ts, 1),
                       "role": role})
    return frames


def final_review_enabled(cfg=None):
    """¿Está encendida la validación final?

    Orden del usuario (2026-11-18 22:57, 100% automático):
    ENCENDIDA POR DEFECTO, en el código del repo — no depende de
    ninguna variable puesta a mano. Orden: ``cfg["final_review"]
    ["enabled"]`` si viene, si no la variable
    ``FINAL_REVIEW_ENABLED`` (1/true/yes/on la enciende;
    0/false/no/off la apaga), y si no hay ni una ni otra,
    ENCENDIDA. Para apagarla hay que hacerlo explícito.
    """
    cfg = cfg or {}
    flag = (cfg.get("final_review") or {}).get("enabled")
    if flag is not None:
        return bool(flag)
    raw = os.environ.get("FINAL_REVIEW_ENABLED", "").strip().lower()
    if raw:
        return raw not in ("0", "false", "no", "off")
    return True


def _pending(motivo, frames_n=0, model=MODEL_DEFAULT):
    """Resultado sin veredicto: a revisión humana, nunca aprobado."""
    return {
        "decision": "pendiente_revision",
        "pasa": False,
        "revision": "no_disponible",
        "scores": {},
        "motivos": {},
        "fallos": [],
        "motivo": motivo,
        "resumen": motivo,
        "frames_n": frames_n,
        "model": model,
        "transcript_words": 0,
    }


def review_final_clip(clip_path, title, transcript_text="",
                      out_dir=None, gemini_fn=None, model=None):
    """Valida el MP4 final con Gemini. Nunca lanza excepción.

    ``gemini_fn``: callable(parts) -> texto, inyectable en tests;
    por defecto ``vision._call_gemini``. Devuelve el dict de la
    revisión (ver docstring del módulo).
    """
    model = model or MODEL_DEFAULT
    transcript_text = (transcript_text or "").strip()
    if not clip_path or not os.path.isfile(str(clip_path)):
        return _pending(f"clip no encontrado: {clip_path}", model=model)
    from .vision import _call_gemini, get_api_key
    # Sin cliente inyectado (producción), sin key no hay juez.
    # Con cliente inyectado (tests), el mock decide.
    if gemini_fn is None and not get_api_key():
        return _pending("sin GEMINI_API_KEY", model=model)
    try:
        frames = extract_final_review_frames(clip_path, out_dir=out_dir)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        return _pending(f"sin fotogramas para la validación: {exc}",
                        model=model)
    prompt = FINAL_REVIEW_PROMPT.format(
        title=title or "(sin título)",
        transcript=transcript_text[:2000] if transcript_text
        else "(sin transcripción en disco)")
    parts = [{"text": prompt}]
    try:
        for frame in frames:
            with open(frame["path"], "rb") as f:
                parts.append({"inlineData": {
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(f.read()).decode(),
                }})
    except OSError as exc:
        return _pending(f"fotograma no legible: {exc}",
                        frames_n=len(frames), model=model)
    call = gemini_fn or _call_gemini
    try:
        text = call(parts, model=model) if gemini_fn is None else call(parts)
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de
        # Gemini (red, HTTP, key) deja el clip pendiente, no aprobado.
        return _pending(f"Gemini no responde: {exc}",
                        frames_n=len(frames), model=model)
    try:
        scores, motivos, fallos, resumen = _parse_final_review(text)
    except ValueError as exc:
        return _pending(f"respuesta de Gemini sin JSON válido: {exc}",
                        frames_n=len(frames), model=model)
    fallos_reales = _decide(scores, fallos)
    pasa = not fallos_reales
    return {
        "decision": "aprobado" if pasa else "rechazado",
        "pasa": pasa,
        "revision": "gemini",
        "scores": scores,
        "motivos": motivos,
        "fallos": fallos_reales,
        "motivo": "" if pasa else (
            "validación final: " + ", ".join(
                f"{CRITERIA_ES[c]} ({scores[c]:.1f})"
                for c in fallos_reales)),
        "resumen": resumen,
        "frames_n": len(frames),
        "model": model,
        "transcript_words": len(transcript_text.split()),
    }


def maybe_review_final_clip(clip_path, title, transcript_text="",
                            out_dir=None, cfg=None, gemini_fn=None,
                            model=None):
    """Enganche de los flujos: si está apagada, no valida nada.

    Devuelve ``decision == "desactivada"`` (sin veredicto) cuando
    la validación está apagada; si no, lo mismo que
    ``review_final_clip``.
    """
    if not final_review_enabled(cfg):
        return {
            "decision": "desactivada",
            "pasa": None,
            "revision": "desactivada",
            "scores": {},
            "motivos": {},
            "fallos": [],
            "motivo": "validación final desactivada "
                      "(FINAL_REVIEW_ENABLED=0)",
            "resumen": "validación final desactivada",
            "frames_n": 0,
            "model": model or MODEL_DEFAULT,
            "transcript_words": len((transcript_text or "").split()),
        }
    return review_final_clip(
        clip_path, title, transcript_text=transcript_text,
        out_dir=out_dir, gemini_fn=gemini_fn, model=model)
