"""Revisión automática de los mejores picos (2026-11-18).

Regla del usuario (APROBADA 2026-11-18, LISTONES BAJADOS
2026-11-19 por su orden directa): en el loop en vivo, los picos
con viralidad AUTO >= 45 van directos a render; los top 3 por
directo con AUTO 40-44,9 pasan por revisión automática con
fotogramas + transcripción (Gemini) y se re-calcula su viralidad.
Si tras la revisión pasan >= 45 y SEO >= 80, se renderizan y se
encolan en LIVE; si no, se descartan SIN render. Por qué se bajó:
la revisión bajaba picos de 48-49 a ~35 (alta varianza, no un
sesgo sistemático) y el SEO 81-86 moría en el umbral 90; la
prioridad es que salgan clips.

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
# Listón de la revisión BAJADO 2026-11-19 (orden del usuario):
# era 50,0; los picos auto 48-49 que Gemini bajaba a ~35 morían
# aquí. Con 45,0 los picos auto 45-49,9 van directos (no pasan por
# la revisión que los tumbaba) y la revisión solo ve 40-44,9.
REVIEW_VIRAL_MAX = 45.0
REVIEW_TOP_N = 3
REVIEW_SEO_MIN = 80
REVIEW_PEAK_REL_S = 20.0  # el pico cae a los 20 s de la ventana 40 s

# Rescate por gritos DEL STREAMER (2026-11-18, aprobado por el
# usuario): punto aparte, no toca la puerta 45/80. Un clip
# borderline (viralidad revisada 45-49,9) con 2+ segundos de grito
# del streamer (decibeles medidos solo mientras habla, ver
# audio.streamer_shout_spikes) pasa aunque no llegue a 50, si el
# SEO también pasa (>= 80). Con 1 solo segundo de grito, o sin
# voz, no hay rescate: un grito aislado puede ser una risa o un
# golpe al micrófono.
SHOUT_RESCUE_VIRAL_MIN = 45.0
SHOUT_RESCUE_MIN = 2

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

REVIEW_PROMPT_EN = """You are the virality judge for a GTA 6 clips channel in English.
You get 3 frames from a 40-second moment of a GTA VI livestream
and the transcript of what the streamer says in that moment. The
provisional title of the clip is: {title}

Score each component 0 to 10 (one decimal, be demanding: a 5 is mediocre,
an 8 is very good, a 10 is exceptional):
- hook: the first 3 seconds grab attention and make you stay.
- claridad: you understand what is happening without knowing the stream context.
- novedad: the moment is unexpected, surprising or funny, not routine.
- loop: it makes you want to watch it again or share it.
- packaging: the moment sells itself as a short clip (title + thumbnail).

Reply ONLY with this JSON (no text outside):
{{"hook": 0.0, "claridad": 0.0, "novedad": 0.0, "loop": 0.0, "packaging": 0.0,
  "razones": {{"hook": "...", "claridad": "...", "novedad": "...", "loop": "...", "packaging": "..."}}}}
"""

REVIEW_PROMPTS = {"es": REVIEW_PROMPT, "en": REVIEW_PROMPT_EN}


def decide_render(viral_auto, reviewed_count):
    """Decisión pre-render de UN pico en el loop en vivo.

    Devuelve ("directo" | "revision" | "descartar", motivo).
    ``reviewed_count``: revisiones ya hechas en este directo.
    """
    viral_auto = float(viral_auto or 0.0)
    if viral_auto >= REVIEW_VIRAL_MAX:
        return ("directo",
                f"viralidad auto {viral_auto:.1f} >= 45: a render")
    if viral_auto >= REVIEW_VIRAL_MIN:
        if reviewed_count >= REVIEW_TOP_N:
            return ("descartar",
                    f"viralidad auto {viral_auto:.1f} pero ya van "
                    f"{REVIEW_TOP_N} revisiones en este directo "
                    f"(top 3)")
        return ("revision",
                f"viralidad auto {viral_auto:.1f} en 40-44,9: "
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


def review_human_scores(frames, transcript_text, title, game="GTA 6",
                        lang="es"):
    """Gemini puntúa los 5 componentes humanos del clip.

    ``frames``: lista de ``extract_thumbnail_frames`` ({path}).
    ``lang``: idioma del canal del clip ("es" o "en"): el juez
    usa el prompt en ese idioma (2026-11-19, calibración: el
    prompt en español para un clip en inglés juzgaba el momento
    como si fuera para público hispano).
    Devuelve (human, reasons). Lanza RuntimeError si no hay key,
    no hay fotogramas o Gemini no responde con JSON válido.
    """
    from .vision import _call_gemini, get_api_key
    if not frames:
        raise RuntimeError("sin fotogramas para la revisión")
    if not get_api_key():
        raise RuntimeError("sin GEMINI_API_KEY")
    prompt = REVIEW_PROMPTS.get(lang) or REVIEW_PROMPT
    parts = [{"text": prompt.format(
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
              "package": None, "seo_detail": None,
              "revision": "gemini", "pasa": False, "motivo": "",
              "transcript_words": 0, "frames_n": 0,
              "streamer_hype": {"count": 0, "distinct": 0,
                                "phrases": []}}
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
        # El paquete pre-corte y su desglose SEO quedan guardados
        # (2026-11-19, calibración): el paso 7 del cutter reutiliza
        # ESTE paquete para el SEO post-corte, y el embudo queda
        # auditable sin reconstruir nada (antes solo vivían los
        # totales y los paquetes descartados se perdían).
        result["package"] = {
            "best_title": pkg.get("best_title"),
            "description": pkg.get("description_first_2_lines"),
            "tags": pkg.get("tags") or [],
            "hashtags": pkg.get("hashtags") or [],
            "thumbnail_text": pkg.get("thumbnail_text"),
            "pkg_source": pkg.get("pkg_source"),
        }
        result["seo_detail"] = seo_full
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
        # Frases hype del streamer en su propia voz (2026-11-18):
        # quedan registradas en la revisión; no tocan la puerta.
        from .streamer_hype import find_streamer_hype
        result["streamer_hype"] = find_streamer_hype(text)
        full = audio_verdict(window_path, chat_score=score,
                             peak_rel=REVIEW_PEAK_REL_S,
                             transcript=transcript, lang=lang)
        result["audio"] = {"stats": full.get("stats"),
                           "words": full.get("words"),
                           "shout_count": full.get("shout_count", 0),
                           "shout_seconds": full.get("shout_seconds",
                                                     []),
                           "streamer_shout_count": full.get(
                               "streamer_shout_count", 0),
                           "streamer_shout_seconds": full.get(
                               "streamer_shout_seconds", [])}
        result["transcript_words"] = int(full.get("words") or 0)
    except Exception as e:  # noqa: BLE001 - sin fotogramas ni
        # audio no hay revisión posible: fallback pre-corte.
        return _fallback(result, viral_auto, seo_for_gate,
                         f"revisión sin fotogramas/audio: {e}",
                         cfg=cfg)
    # 3. Juez Gemini: los 5 componentes humanos.
    try:
        human, reasons = review_human_scores(
            frames, text, result["pre_corte_title"], game=game,
            lang=lang if lang in ("es", "en") else "es")
    except Exception as e:  # noqa: BLE001 - sin juez no hay
        return _fallback(result, viral_auto, seo_for_gate,  # revisión:
                         f"revisión sin juez Gemini: {e}", cfg=cfg)
    result["human"] = human
    result["human_reasons"] = reasons
    # 4. Viralidad revisada (human + audio reales, no estimados).
    viral = score_clip(detail, multiple, human=human,
                       audio=result["audio"])
    result["viral_revisado"] = float(viral.get("total") or 0.0)
    # 5. Puerta REAL 45/80 (bajada 2026-11-19, orden del usuario).
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
    # 5b. Rescate por gritos del streamer (punto aparte, 2026-11-18):
    # SUBSUMIDO por la puerta 45 (2026-11-19): un revisado 45-49,9
    # ya pasa por la puerta principal, así que este bloque solo se
    # dispara si la puerta se vuelve a subir. Se conserva como red
    # de seguridad, no se borra.
    streamer_shouts = int((result["audio"] or {}).get(
        "streamer_shout_count") or 0)
    result["streamer_shout_rescue"] = False
    if (not result["pasa"]
            and result["viral_revisado"] >= SHOUT_RESCUE_VIRAL_MIN
            and streamer_shouts >= SHOUT_RESCUE_MIN):
        rescue = render_gate(result["viral_revisado"], seo_for_gate,
                             cfg=cfg,
                             viral_min=SHOUT_RESCUE_VIRAL_MIN)
        if rescue["pasa"]:
            result["pasa"] = True
            result["streamer_shout_rescue"] = True
            result["motivo"] += (
                f"; RESCATE por gritos del streamer "
                f"({streamer_shouts} s, viral "
                f"{result['viral_revisado']:.1f} >= "
                f"{SHOUT_RESCUE_VIRAL_MIN:.0f}, SEO >= 90)")
            result["estados"] = rescue["estados"]
            result["plataformas_publicables"] = rescue[
                "plataformas_publicables"]
    return result


def _fallback(result, viral_auto, seo_for_gate, reason, cfg=None):
    """Fallback pre-corte cuando la revisión no se puede hacer.

    Misma puerta que el flujo VOD (viral_auto >= 45, SEO >= 80):
    el umbral ya calibrado y aprobado, y la puerta de publicación
    (45 real) sigue frenando lo que no llegue. Queda registrado
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
