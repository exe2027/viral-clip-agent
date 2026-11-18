"""Revisión automática de los mejores picos (2026-11-18).

Regla del usuario (APROBADA 2026-11-18): en el loop en vivo, los
top 3 picos por directo con viralidad AUTO 40-49,9 pasan por
revisión automática con fotogramas + transcripción (Gemini) y se
re-calcula su viralidad. Si tras la revisión pasan >= 50 y
SEO >= 90, se renderizan y se encolan en LIVE; si no, se
descartan SIN render. La puerta de publicación sigue en 50: NO
se baja.

Por qué hace falta: la viralidad AUTO pre-corte usa los 5
componentes humanos y el audio en su estimación conservadora 5,0
(``score_clip`` sin human ni audio). La calibración pre-corte
midió que un clip bueno (p3220) estima 46,3 y vale 53,8 de
verdad: la estimación subestima ~7 puntos. La revisión sustituye
esas estimaciones por medidas reales — Gemini puntúa los 5
componentes humanos viendo 3 fotogramas + la transcripción, y el
audio se mide con Whisper + ``audio_verdict`` — y vuelve a
decidir con el 50 REAL.

Decisión por pico (``decide_render``), ANTES de gastar render:
- viral_auto >= 50: DIRECTO a render (sin revisión; la puerta
  de publicación lo vuelve a medir post-corte).
- 40 <= viral_auto < 50: REVISIÓN, solo los top 3 por directo
  (los de mayor viral_auto). El 4º y siguientes se descartan
  sin revisión ni render.
- viral_auto < 40: se descarta sin revisión ni render.

La revisión (``review_peak``) nunca lanza excepción: si Gemini
no está disponible (sin key, error de red, JSON inválido) o no
hay fotogramas/transcripción, se aplica el FALLBACK pre-corte
(la misma puerta que el flujo VOD: viral_auto >= 45 y SEO
provisional >= 90, ``PRE_CORTE_VIRAL_MIN``) y queda registrado
como ``revision: "fallback-pre-corte"`` con el motivo. Sin
fallback, una caída de Gemini dejaría sin segunda oportunidad
a todos los picos borderline; el fallback usa el umbral ya
calibrado y aprobado para el flujo VOD, y la puerta de
publicación (50 real) sigue frenando lo que no llegue.

El SEO de la revisión es el PROVISIONAL pre-corte (el mismo de
``gate_peak_for_render``: paquete sin vídeo + ``score_package_seo``);
el definitivo se mide post-corte como siempre.
"""
import base64
import json
import os

REVIEW_VIRAL_MIN = 40.0
REVIEW_VIRAL_MAX = 50.0
REVIEW_TOP_N = 3
REVIEW_SEO_MIN = 90
REVIEW_PEAK_REL_S = 20.0  # el pico cae a los 20 s de la ventana 40 s

HUMAN_COMPONENTS = ("hook", "claridad", "novedad", "loop", "packaging")

REVIEW_PROMPT = """Eres el juez de viralidad de un canal de clips de GTA 6 en español.
Te paso 3 fotogramas de un momento de 40 segundos de un directo de GTA VI
y la transcripción de lo que dice el streamer en ese momento. El título
provisional del clip es: {title}

Puntúa cada componente de 0 a 10 (un decimal, sé exigente: un 5 es mediocre,
un 8 es muy bueno, un 10 es excepcional):
- hook: los primeros 3 segundos enganchan y hacen quedarse a verlo.
- claridad: se entiende lo que pasa sin conocer el contexto del directo.
- novedad: el momento es inesperado, sorprendente o cómico, no rutinario.
- loop: dan ganas de verlo otra vez o de compartirlo.
- packaging: el momento se vende solo como clip corto (título + miniatura).

Responde SOLO con este JSON (sin texto fuera):
{{"hook": 0.0, "claridad": 0.0, "novedad": 0.0, "loop": 0.0, "packaging": 0.0,
  "razones": {{"hook": "...", "claridad": "...", "novedad": "...", "loop": "...", "packaging": "..."}}}}
"""


def decide_render(viral_auto, reviewed_count):
    """Decisión pre-render de UN pico en el loop en vivo.

    Devuelve ("directo" | "revision" | "descartar", motivo).
    ``reviewed_count``: revisiones ya hechas en este directo.
    """
    viral_auto = float(viral_auto or 0.0)
    if viral_auto >= REVIEW_VIRAL_MAX:
        return ("directo",
                f"viralidad auto {viral_auto:.1f} >= 50: a render")
    if viral_auto >= REVIEW_VIRAL_MIN:
        if reviewed_count >= REVIEW_TOP_N:
            return ("descartar",
                    f"viralidad auto {viral_auto:.1f} pero ya van "
                    f"{REVIEW_TOP_N} revisiones en este directo "
                    f"(top 3)")
        return ("revision",
                f"viralidad auto {viral_auto:.1f} en 40-49,9: "
                f"a revisión automática (top 3)")
    return ("descartar",
            f"viralidad auto {viral_auto:.1f} < 40: sin revisión "
            f"ni render")


def _transcript_text(transcript):
    """Texto plano de la transcripción Whisper (o \"\" sin voz)."""
    if not transcript:
        return ""
    parts = []
    for seg in transcript.get("segments") or []:
        text = (seg.get("text") or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def _parse_human_scores(text):
    """Valida el JSON del juez Gemini: 5 componentes 0-10.

    Devuelve (human_dict, reasons_dict). Lanza ValueError si el
    JSON no es válido o falta algún componente.
    """
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    try:
        data = json.loads(cleaned)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Gemini sin JSON válido: {e}") from e
    human, reasons = {}, {}
    for name in HUMAN_COMPONENTS:
        try:
            value = float(data[name])
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(
                f"Gemini sin componente {name!r}") from e
        human[name] = max(0.0, min(10.0, value))
        reasons[name] = str((data.get("razones") or {}).get(name, ""))
    return human, reasons


def review_human_scores(frames, transcript_text, title, game="GTA 6"):
    """Gemini puntúa los 5 componentes humanos del clip.

    ``frames``: lista de ``extract_thumbnail_frames`` ({path}).
    Devuelve (human, reasons). Lanza RuntimeError si no hay key,
    no hay fotogramas o Gemini no responde con JSON válido.
    """
    from .vision import _call_gemini, get_api_key
    if not frames:
        raise RuntimeError("sin fotogramas para la revisión")
    if not get_api_key():
        raise RuntimeError("sin GEMINI_API_KEY")
    parts = [{"text": REVIEW_PROMPT.format(
        title=title or "(sin título provisional)")}]
    if transcript_text:
        parts.append({"text": "Transcripción del momento:\n"
                      + transcript_text})
    else:
        parts.append({"text": "Transcripción del momento: "
                      "(sin voz transcrita en el momento)"})
    for frame in frames:
        path = frame.get("path") or ""
        if not path or not os.path.isfile(path):
            raise RuntimeError(f"fotograma no encontrado: {path}")
        with open(path, "rb") as f:
            parts.append({"inlineData": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(f.read()).decode(),
            }})
    text = _call_gemini(parts)
    return _parse_human_scores(text)


def review_peak(window_path, detail, multiple, chat_path, peak_ts,
                score, game="GTA 6", lang="es", streamer="", cfg=None):
    """Revisión automática de UN pico borderline (40-49,9).

    1. Paquete provisional + SEO pre-corte (sin vídeo).
    2. Fotogramas de la ventana + transcripción Whisper + audio.
    3. Gemini puntúa los 5 componentes humanos.
    4. Viralidad REVISADA = score_clip(detail, multiple,
       human=Gemini, audio=medido).
    5. Puerta REAL 50/90 (``render_gate`` con viral_min=50).

    Nunca lanza: cualquier fallo devuelve {"pasa": False} con el
    motivo (o el fallback pre-corte, ver docstring del módulo).
    Devuelve el dict completo de la revisión (viral_auto,
    viral_revisado, human, audio, seo, pasa, motivo, revision).
    """
    from .detect import score_clip
    from .policy import render_gate
    from .workers import pre_corte_package
    viral_auto = float((score_clip(detail, multiple) or {}).get(
        "total") or 0.0)
    result = {"viral_auto": viral_auto, "viral_revisado": None,
              "human": None, "human_reasons": None, "audio": None,
              "seo": {}, "seo_medido": False, "pre_corte_title": None,
              "revision": "gemini", "pasa": False, "motivo": "",
              "transcript_words": 0, "frames_n": 0}
    # 1. SEO provisional (sin vídeo) + título para el juez.
    try:
        median_score = None
        if multiple and float(multiple) > 0 and score:
            median_score = float(score) / float(multiple)
        pkg, seo_full = pre_corte_package(
            chat_path, peak_ts, score, multiple, detail,
            median_score=median_score, all_scores=[],
            lang=lang, game=game, streamer=streamer)
        result["pre_corte_title"] = pkg.get("best_title")
        result["seo"] = {p: r.get("score")
                         for p, r in (seo_full or {}).items()}
        result["seo_medido"] = bool(game) and bool(
            result["pre_corte_title"])
    except Exception as e:  # noqa: BLE001 - SEO sin medir: la
        result["seo_error"] = str(e)  # puerta decide por viralidad.
    seo_for_gate = result["seo"] if result["seo_medido"] else {}
    # 2. Fotogramas + transcripción + audio medido.
    try:
        from .thumbnails import extract_thumbnail_frames
        from .transcribe import transcribe_audio
        from .audio import audio_verdict
        frames = extract_thumbnail_frames(window_path)
        result["frames_n"] = len(frames)
        transcript, _info = transcribe_audio(
            window_path, language=lang if lang in ("es", "en")
            else None)
        text = _transcript_text(transcript)
        full = audio_verdict(window_path, chat_score=score,
                             peak_rel=REVIEW_PEAK_REL_S,
                             transcript=transcript, lang=lang)
        result["audio"] = {"stats": full.get("stats"),
                           "words": full.get("words")}
        result["transcript_words"] = int(full.get("words") or 0)
    except Exception as e:  # noqa: BLE001 - sin fotogramas ni
        # audio no hay revisión posible: fallback pre-corte.
        return _fallback(result, viral_auto, seo_for_gate,
                         f"revisión sin fotogramas/audio: {e}",
                         cfg=cfg)
    # 3. Juez Gemini: los 5 componentes humanos.
    try:
        human, reasons = review_human_scores(
            frames, text, result["pre_corte_title"], game=game)
    except Exception as e:  # noqa: BLE001 - sin juez no hay
        return _fallback(result, viral_auto, seo_for_gate,  # revisión:
                         f"revisión sin juez Gemini: {e}", cfg=cfg)
    result["human"] = human
    result["human_reasons"] = reasons
    # 4. Viralidad revisada (human + audio reales, no estimados).
    viral = score_clip(detail, multiple, human=human,
                       audio=result["audio"])
    result["viral_revisado"] = float(viral.get("total") or 0.0)
    # 5. Puerta REAL 50/90.
    gate = render_gate(result["viral_revisado"], seo_for_gate,
                       cfg=cfg, viral_min=REVIEW_VIRAL_MAX)
    result["pasa"] = bool(gate["pasa"])
    result["motivo"] = (
        f"revisión Gemini: viralidad {viral_auto:.1f} -> "
        f"{result['viral_revisado']:.1f}, SEO máx. "
        f"{max(result['seo'].values()) if result['seo'] else 'sin medir'}"
        f"; {gate['motivo']}")
    result["estados"] = gate["estados"]
    result["plataformas_publicables"] = gate["plataformas_publicables"]
    return result


def _fallback(result, viral_auto, seo_for_gate, reason, cfg=None):
    """Fallback pre-corte cuando la revisión no se puede hacer.

    Misma puerta que el flujo VOD (viral_auto >= 45, SEO >= 90):
    el umbral ya calibrado y aprobado, y la puerta de publicación
    (50 real) sigue frenando lo que no llegue. Queda registrado
    como revisión "fallback-pre-corte" con el motivo.
    """
    from .policy import render_gate
    from .workers import PRE_CORTE_VIRAL_MIN
    gate = render_gate(viral_auto, seo_for_gate, cfg=cfg,
                       viral_min=PRE_CORTE_VIRAL_MIN)
    result["revision"] = "fallback-pre-corte"
    result["pasa"] = bool(gate["pasa"])
    result["motivo"] = (f"{reason}; fallback pre-corte "
                        f"(viral {viral_auto:.1f}, umbral "
                        f"{PRE_CORTE_VIRAL_MIN}): {gate['motivo']}")
    result["estados"] = gate["estados"]
    result["plataformas_publicables"] = gate["plataformas_publicables"]
    return result
