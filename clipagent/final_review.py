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
- ``observado`` (2026-11-19, ORDEN DEL USUARIO: MODO OBSERVACIÓN
  POR DEFECTO hasta calibrar con 10-20 clips reales): Gemini vio
  el vídeo y algún criterio falla, pero el veredicto SOLO SE
  REGISTRA, no bloquea nada: el clip se encola y se publica como
  si hubiera pasado, con los scores y fallos guardados para la
  calibración. Para volver a bloquear hay que poner explícito
  ``FINAL_REVIEW_OBSERVE_ONLY=0`` o
  ``cfg["final_review"]["observe_only"] = False``. Sin calibración
  real no se vuelve a bloquear: el validador sin calibrar tumbó
  los 3 clips en vivo de la 01:08 (webcam 0.0 en los tres) y la
  Cola se quedó seca.
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

PUERTA DURA (2026-11-19, ``validate_final_clip``): además del
juez Gemini, el vídeo final pasa por dos capas deterministas que
el modo observación NO anula nunca: la cara (cadena face-crop ->
bordes fijos -> YuNet, muestreada en todo el clip, en la zona de
cámara de LA PLANTILLA DEL CLIP) y la plantilla (una de TRES
recetas en píxeles, config.yaml `templates`: rdjavi v1, willyrex
v2 o rubius; default rubius cuando el clip no declara plantilla).
Si la cara o la plantilla fallan, el clip NO se encola ni se
publica: se re-renderiza automáticamente (máx.
``validator.max_rerenders``) y, si tras re-hacerlo sigue sin
cara, queda marcado para revisión humana con motivo visible y se
avisa al usuario; nunca se bloquea en silencio y nunca se deja
pasar con duda. La config del gate vive SOLO en config.yaml
(secciones ``validator``, ``final_review`` y ``templates``) y se
carga al inicio de cada punto de entrada; si falta un valor, el
run falla en voz alta (ConfigError) en vez de seguir con un
default inventado: ese default silencioso es lo que dejó pasar
los clips sin cara (ver ``load_validator_config`` en config.py).
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
    "plantilla",
)

CRITERIA_ES = {
    "webcam_visible": "webcam visible",
    "titulo_legible": "título legible",
    "subtitulos_ok": "subtítulos bien puestos",
    "sin_barras_negras": "sin barras negras",
    "momento_comprensible": "momento comprensible",
    "subtitulos_coherentes": "subtítulos coherentes",
    "plantilla": "plantilla",
}

# Tamaño de fotograma (1080x1920). La GEOMETRÍA de cada receta
# vive SOLO en config.yaml (`templates`): este módulo no lleva
# copias literales de bandas ni zonas (la única fuente es
# load_validator_config). W/H solo dicen cuánto mide el lienzo.
W, H = 1080, 1920

# Descripción de cada receta para el prompt de Gemini:
# (párrafo de layout, descripción del criterio `plantilla`).
RECIPE_TEXT = {
    "rdjavi": (
        "el streamer ARRIBA CENTRADO casi 1:1 (583x595 px, x=248, "
        "y=0) sobre fondo desenfocado morado/magenta con gradiente "
        "atardecer; los subtítulos KARAOKE palabra por palabra "
        "(palabra activa amarilla, resto blanco, 56 px negrita, "
        "contorno negro) AL PIE DE LA CÁMARA (banda 595-749 px), "
        "nunca sobre el gameplay; el gameplay ABAJO (749-1786 px) "
        "y una barra negra ABAJO DEL TODO (1786-1920 px).",
        "montaje, fuentes y subtítulos son los del estilo rdjavi "
        "v1: streamer centrado 583x595 arriba sobre fondo "
        "desenfocado, subtítulos karaoke al pie de la cámara "
        "(activa amarilla, resto blanco), gameplay abajo y barra "
        "negra inferior, letra gruesa estilo Arial Black; SIN "
        "subtítulos sobre el gameplay, SIN subtítulos fijos "
        "amarillos en barra inferior, SIN cámara a ancho completo.",
    ),
    "willyrex": (
        "la webcam del streamer A ANCHO COMPLETO arriba (0-702 "
        "px), una banda negra en el MEDIO con el TÍTULO en una sola "
        "línea en blanco (702-800 px), el gameplay ABAJO (800-1740 "
        "px) y una banda negra ABAJO DEL TODO con LOS SUBTÍTULOS "
        "fijos amarillos (1740-1920 px). Los subtítulos del juego "
        "viven DENTRO del gameplay: los subtítulos amarillos NUNCA "
        "van sobre el gameplay.",
        "montaje, fuentes y subtítulos son los del estilo Willyrex: "
        "webcam a ancho completo arriba, banda negra con el título "
        "en el medio, gameplay abajo, subtítulos fijos amarillos "
        "en la banda negra inferior, letra gruesa estilo Arial "
        "Black; SIN fondo borroso detrás de la cámara, SIN cámara "
        "en esquina, SIN banda de título ausente, SIN karaoke "
        "(palabra por palabra con cambio de color).",
    ),
    "rubius": (
        "la cámara del streamer A ANCHO COMPLETO arriba (0-840 "
        "px), el gameplay ABAJO (840-1920 px) con corte limpio SIN "
        "bandas negras; el TÍTULO va en un banner amarillo "
        "redondeado con borde blanco (x 103-967, y 811-1099), dos "
        "líneas centradas (línea 1 NEGRA, línea 2 ROJA, Arial Black "
        "gruesa), transitorio (solo en el momento clave); los "
        "SUBTÍTULOS van palabra por palabra, una palabra a la vez, "
        "en BLANCO con borde negro grueso, centrados, sobre la "
        "parte superior del gameplay (y 900-1020).",
        "montaje, fuentes y subtítulos son los del estilo rubius: "
        "cámara a ancho completo arriba, gameplay abajo "
        "sin bandas, título en banner amarillo con borde blanco y "
        "dos líneas (negra y roja), subtítulos blancos palabra por "
        "palabra sobre la parte superior del gameplay, letra gruesa "
        "estilo Arial Black; SIN bandas negras, SIN subtítulos "
        "amarillos fijos, SIN karaoke.",
    ),
}

FINAL_REVIEW_PROMPT = """Eres el validador final de un canal de clips de GTA 6 en español. Te paso 4 fotogramas REALES del vídeo final ya renderizado, en orden: 1 = inicio, 2 = payoff (el momento), 3 = reacción, 4 = final. El layout es vertical 9:16, en este orden exacto de arriba a abajo: {receta} El título es: {title}

La webcam puede ser un avatar animado o VTuber (dibujo, sin cara humana): si se ve el avatar en la zona de cámara, webcam_visible puntúa alto, no cero.

La transcripción de lo que se dice en el clip es:
{transcript}

Puntúa cada criterio de 0 a 10 (un decimal, sé exigente: un 5 es mediocre, un 8 es muy bueno, un 10 es excepcional) y explica cada nota en una frase corta:
- webcam_visible: se ve la webcam del streamer en la zona de cámara de la receta, con su cara visible (o su avatar, si es VTuber), sin que el gameplay invada esa zona ni que la zona quede negra o vacía.
- titulo_legible: el título está donde dice la receta, se lee entero sin cortarse, sin duplicarse y sin tapar la cara del streamer ni el gameplay importante.
- subtitulos_ok: los subtítulos están donde dice la receta y se leen bien, sin tapar el gameplay importante ni el minimapa ni el HUD importante, y no se salen de la pantalla.
- sin_barras_negras: no hay barras negras laterales ni arriba/abajo fuera de las bandas de la receta, ni vídeo duplicado, ni la imagen repetida en dos bandas a la vez.
- momento_comprensible: se entiende qué pasa en el clip sin conocer el contexto del directo (se ve la jugada o la reacción que promete el título).
- subtitulos_coherentes: lo que dicen los subtítulos quemados coincide con la transcripción (no inventan palabras ni frases que nadie dijo).
- plantilla: {plantilla_desc}

En "fallos" lista SOLO los criterios con nota menor de 5 (los que frenan el clip). "pasa" es true solo si ningún criterio tiene menos de 5.

Responde SOLO con este JSON (sin texto fuera):
{{"criterios": {{"webcam_visible": {{"score": 0.0, "motivo": "..."}}, "titulo_legible": {{"score": 0.0, "motivo": "..."}}, "subtitulos_ok": {{"score": 0.0, "motivo": "..."}}, "sin_barras_negras": {{"score": 0.0, "motivo": "..."}}, "momento_comprensible": {{"score": 0.0, "motivo": "..."}}, "subtitulos_coherentes": {{"score": 0.0, "motivo": "..."}}, "plantilla": {{"score": 0.0, "motivo": "..."}}}}, "fallos": [], "pasa": true, "resumen": "una frase corta en español"}}
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


def _decide(scores, fallos, min_score=None):
    """Veredicto en código: todos >= MIN_SCORE y sin fallos.

    ``min_score`` sale de config.yaml (validator.min_score); sin
    él, MIN_SCORE. El veredicto nunca se fía del ``pasa`` de
    Gemini.
    """
    threshold = MIN_SCORE if min_score is None else float(min_score)
    hard = [c for c in CRITERIA if scores.get(c, 0.0) < threshold]
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


def final_review_observe_only(cfg=None):
    """¿La validación final solo observa (registra, no bloquea)?

    Orden del usuario (2026-11-19): OBSERVACIÓN POR DEFECTO hasta
    calibrar el validador con 10-20 clips reales. Sin calibrar
    tumbó los 3 clips en vivo de la 01:08 (webcam 0.0 en los tres)
    y la Cola se quedó seca: un juez sin calibrar no puede frenar
    el encolado. Orden: ``cfg["final_review"]["observe_only"]``
    si viene, si no la variable ``FINAL_REVIEW_OBSERVE_ONLY``
    (1/true/yes/on = observa; 0/false/no/off = vuelve a bloquear),
    y si no hay ni una ni otra, OBSERVA. Para volver a bloquear
    hay que hacerlo explícito, con clips reales ya calibrados.
    """
    cfg = cfg or {}
    flag = (cfg.get("final_review") or {}).get("observe_only")
    if flag is not None:
        return bool(flag)
    raw = os.environ.get("FINAL_REVIEW_OBSERVE_ONLY", "").strip().lower()
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


# ---------------------------------------------------------------------------
# Puerta dura (2026-11-19): cara + plantilla sobre el VÍDEO FINAL.
#
# Causa raíz que esto corrige (clips 17/22/23 sin cara en la Cola):
# (1) maybe_review_final_clip reescribía un "rechazado" de Gemini
# como "observado" (pasa=True) por el modo observación por defecto
# (final_review_observe_only, True si no hay config ni variable);
# los tres enganches solo frenan con "rechazado", así que un fallo
# de cara fluía como aprobado. (2) El juez solo veía 4 fotogramas
# del vídeo final, y el camino VOD no validaba nada hasta el
# commit 2e307c7; el clip 23 se renderizó antes (run 44501550028,
# código 242acee, sin llamada a final_review en VOD). (3) La cara
# se detectaba en la fuente pero el render la perdía después:
# decide_layout no encontraba webcam estable (TheGrefg: 7% de
# celdas estables) y devolvía "center" (juego a pantalla completa)
# sin avisar. (4) config.yaml NO tenía sección final_review: el
# modo observación era un default silencioso, y el camino VOD ni
# cargaba la config (default "karaoke" en el quemado, clip 23).
#
# La puerta dura no la anula el modo observación: si la cara o la
# plantilla fallan, el clip NO se encola ni se publica, se
# re-renderiza automáticamente (máx. max_rerenders) y, si tras
# re-hacerlo sigue sin cara, queda marcado para revisión humana
# con motivo visible y se avisa al usuario; nunca se bloquea en
# silencio y nunca se deja pasar con duda.
# ---------------------------------------------------------------------------

# Umbrales de MEDIDA de los chequeos de píxeles (calibración del
# instrumento, no valores del usuario: los valores del gate — cara,
# muestreo, plantilla, estilo — viven en config.yaml y los carga
# load_validator_config; estos números solo dicen qué cuenta como
# "negro", "amarillo" o "texto" en un fotograma).
_GATE_MAX_FRAMES = 24       # tope de fotogramas muestreados por clip
_GATE_BLACK_LUMA = 40.0     # luma media < 40 = banda negra
_GATE_GAME_LUMA_MIN = 12.0  # gameplay con luma media menor = vacío/negro
_GATE_TEXT_PX = 50          # px de texto para dar por presente un texto
_GATE_SUB_PX = 300          # px amarillos/blancos que delatan subtítulo
_GATE_BLUR_RATIO = 0.30     # energía de bordes laterales/centro: menos
                            # = cámara centrada sobre fondo borroso
_GATE_LIVE_DIFF = 2.0       # diferencia media entre fotogramas: menos
                            # = banda congelada
_GATE_BAR_STD = 10.0       # luma std dentro de una banda real: una
                            # banda es sólida; el juego oscuro varía
_GATE_BAR_STEP = 25.0      # salto mínimo de luma de la banda al
                            # contenido adyacente (banda real)
_GATE_SUB_WORD_PX = 50     # px blancos que delatan UNA palabra corta
                            # en la zona de subtítulos (174: 1679 px;
                            # "y"/"a" a 110 px dan 1656-1935 px, "i"
                            # fina 728 px; el suelo es _GATE_TEXT_PX,
                            # el mínimo que cuenta como texto presente,
                            # para que un glifo fino degradado por el
                            # contorno negro y el JPEG no se pierda)
_YELLOW = ((200, 255), (200, 255), (0, 90))  # R, G altos, B bajo
_WHITE = ((200, 255), (200, 255), (200, 255))
_BLACK = ((0, 60), (0, 60), (0, 60))  # texto negro del banner rubius
_RED = ((180, 255), (0, 90), (0, 90))  # línea 2 roja del banner rubius

# Receta rubius: el banner es TRANSITORIO (t=0.5-3.5 s del render,
# rubius.py: enable=between(t,0.5,3.5)) y el amarillo DOMINA su
# zona. Un conteo bajo de píxeles amarillos lo pasa cualquier
# escena de juego con amarillo (caso scooter 2026-11-21: 890 px
# amarillos de la calle en la zona del banner, sin banner).
# PERO el título también puede ir como TEXTO amarillo sobre fondo
# casi negro (caso 170, 2026-11-23: 10.410-33.517 px amarillos en
# títulos reales a 90-110 px, negro 75-85% de la zona): ahí el
# amarillo no domina, pero los trazos del título sí son muchos
# más que el amarillo disperso del juego. La rama de texto exige
# por eso amarillo SUFICIENTE (>= 3.000 px, 3,4x sobre el
# scooter) Y fondo casi negro (>= 50%): el negro solo no basta
# (un juego oscuro sin título es 89% negro con 0 px amarillos)
# y el amarillo solo tampoco (la calle del scooter).
# FUENTE ÚNICA de estos valores: config.yaml (validator.*), leída
# por load_validator_config y pasada por template_gate. Las
# constantes de abajo solo son el fallback de las llamadas
# directas a los helpers (tests); valen lo mismo que config.yaml
# y hay que mantenerlas sincronizadas si cambia la calibración.
_RUBIUS_BANNER_T0 = 0.5
_RUBIUS_BANNER_T1 = 3.5
_RUBIUS_BANNER_YELLOW_FRAC = 0.30  # scooter 0.004, 52h 0.79
# Rama de texto amarillo sobre negro (170): umbrales calibrados
# V7 2026-11-23 en títulos sintéticos realistas (DejaVu Bold
# 90-110 px) y en el negativo del scooter. Mantener sincronizados
# con clipagent/render_gate.py (espejo del gate en CircleCI).
_RUBIUS_BANNER_YELLOW_TEXT_PX = 3000  # px amarillos mínimos de
    # los trazos del título (170: 10.410-33.517; scooter: 890)
_RUBIUS_BANNER_DARK_FRAC = 0.50  # fracción mínima de fondo casi
    # negro en la zona (170: 0.75-0.85)
# Subtítulos palabra por palabra: el subtítulo centrado nunca toca
# los dos bordes de su zona a la vez; una línea completa sí (caso
# scooter: 4/7 fotogramas con texto en ambos bordes; 52h: 0/5).
# Cobertura mínima de blanco en cada banda para darla por tocada.
_RUBIUS_SUBS_EDGE_W = 150
_RUBIUS_SUBS_EDGE_COV = 0.10
_RUBIUS_SUBS_OUTLINE_FRAC = 0.03  # fracción mínima de píxeles
# blancos-con-contorno-negro en cada banda para darla por tocada
# (Fix 3, 2026-11-24: scooter real 0.034-0.092 por borde,
# FP 119/179 <= 0.0148; umbral en config.yaml
# validator.rubius_subs_outline_frac, requerido)
# SFX: correlación mínima con un efecto del pack (scooter ≤0.22,
# 52h ≥0.98: boom 0.994, riser 0.979).
_SFX_MIN_NCC = 0.5


def _frame_array(path):
    """Fotograma JPG -> array numpy RGB (H, W, 3)."""
    try:
        from PIL import Image
        import numpy as np
        with Image.open(path) as im:
            return np.asarray(im.convert("RGB"))
    except ImportError:
        import cv2
        import numpy as np
        img = cv2.imread(str(path))
        if img is None:
            raise OSError(f"fotograma ilegible: {path}")
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def _luma(arr):
    """Luminancia de un array RGB."""
    return (0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1]
            + 0.114 * arr[:, :, 2])


def _count_color(arr, ranges):
    """Nº de píxeles dentro de los rangos (R, G, B)."""
    import numpy as np
    mask = np.ones(arr.shape[:2], dtype=bool)
    for ch, (lo, hi) in enumerate(ranges):
        mask &= (arr[:, :, ch] >= lo) & (arr[:, :, ch] <= hi)
    return int(mask.sum())


def _banner_yellow_frac(zone):
    """Fracción de píxeles amarillos en la zona del banner."""
    total = int(zone.shape[0]) * int(zone.shape[1])
    if total == 0:
        return 0.0
    return _count_color(zone, _YELLOW) / total


def _banner_title_present(zone, yellow_frac):
    """¿Hay título en la zona del banner? Dos formas válidas
    (V7, caso 170): (a) banner amarillo clásico: el amarillo
    DOMINA la zona (>= yellow_frac) con texto blanco y negro o
    rojo; (b) TEXTO amarillo sobre fondo casi negro: los trazos
    del título suman >= _RUBIUS_BANNER_YELLOW_TEXT_PX px
    amarillos y el fondo es >= _RUBIUS_BANNER_DARK_FRAC negro.
    Devuelve (pasa, detalle). Un clip sin título sigue
    fallando: juego oscuro sin amarillo (0 px) y amarillo
    disperso de la calle (scooter: 890 px) quedan por debajo
    del mínimo de trazos en ambas ramas."""
    frac = _banner_yellow_frac(zone)
    if (frac >= yellow_frac
            and _count_color(zone, _WHITE) > 100
            and (_count_color(zone, _BLACK) > 50
                 or _count_color(zone, _RED) > 50)):
        return True, f"banner amarillo ({frac:.0%} de la zona)"
    total = int(zone.shape[0]) * int(zone.shape[1])
    yellow_px = _count_color(zone, _YELLOW)
    black_frac = (_count_color(zone, _BLACK) / total
                  if total else 0.0)
    if (yellow_px >= _RUBIUS_BANNER_YELLOW_TEXT_PX
            and black_frac >= _RUBIUS_BANNER_DARK_FRAC):
        return True, (f"texto amarillo sobre negro "
                      f"({yellow_px} px amarillos, "
                      f"negro {black_frac:.0%})")
    return False, (f"amarillo {frac:.0%} ({yellow_px} px), "
                   f"negro {black_frac:.0%}")


def _subs_edge_coverages(arr, zy0, zy1, edge_w=None):
    """Cobertura blanca en los bordes izquierdo/derecho de la zona
    de subtítulos (fracción de columnas con texto).

    El subtítulo rubius es UNA palabra centrada: nunca toca los dos
    bordes a la vez. Una línea completa sí (caso scooter 2026-11-21).
    ``edge_w`` sale de config.yaml (validator.rubius_subs_edge_w)
    vía template_gate; sin él, la constante calibrada.

    LEGADO (Fix 3, 2026-11-24): la puerta ya no usa este medidor
    para ``subtitulos_una_palabra`` — el blanco sin contorno es
    juego, no texto (ver ``_subs_edge_outlined_coverages``). Se
    conserva porque el espejo ``render_gate`` lo usaba y hay
    calibraciones antiguas que lo citan; no volver a usarlo como
    detector de texto.
    """
    zone = arr[zy0:zy1]
    white = ((zone[:, :, 0] >= 200) & (zone[:, :, 1] >= 200)
             & (zone[:, :, 2] >= 200))
    colcount = white.sum(axis=0)
    has = colcount >= 5
    w = int(arr.shape[1])
    edge = min(edge_w if edge_w is not None else _RUBIUS_SUBS_EDGE_W,
               w // 2)
    if edge <= 0:
        return 0.0, 0.0
    return float(has[:edge].mean()), float(has[-edge:].mean())


def _subs_edge_outlined_coverages(arr, zy0, zy1, edge_w=None):
    """Cobertura de TEXTO (blanco con contorno negro) en los
    bordes izquierdo/derecho de la zona de subtítulos, por borde
    (fracción de píxeles de la franja, denominador
    ``edge_w`` x alto de la zona).

    Blanco = 3 canales >= 200; negro = 3 canales < 60 (convención
    por canal, como ``_BLACK``). El negro se dilata 5x5 SOBRE el
    recorte zy0:zy1 (el negro fuera del recorte no cuenta). Un
    píxel cuenta si es blanco Y tiene negro dilatado cerca: texto
    blanco con contorno negro. El juego claro (cielo, mapa, suelo)
    es blanco sin negro pegado y no cuenta (Fix 3, casos 119/179
    del run 45310466231: blanco en bordes 0.10-0.92, con contorno
    <= 0.0148). Solo numpy, sin rama cv2: los jobs vivos sin
    ``vision`` no tienen cv2.
    """
    import numpy as np
    zone = arr[zy0:zy1]
    white = ((zone[:, :, 0] >= 200) & (zone[:, :, 1] >= 200)
             & (zone[:, :, 2] >= 200))
    black = ((zone[:, :, 0] < 60) & (zone[:, :, 1] < 60)
             & (zone[:, :, 2] < 60))
    padded = np.pad(black, 2, mode="constant", constant_values=False)
    dil = np.zeros_like(black)
    for dy in range(5):
        for dx in range(5):
            dil |= padded[dy:dy + black.shape[0],
                          dx:dx + black.shape[1]]
    out = white & dil
    w = int(arr.shape[1])
    edge = min(edge_w if edge_w is not None else _RUBIUS_SUBS_EDGE_W,
               w // 2)
    if edge <= 0:
        return 0.0, 0.0
    denom = float((zy1 - zy0) * edge)
    return (float(out[:, :edge].sum()) / denom,
            float(out[:, -edge:].sum()) / denom)


def _ncc_best(x, y, sr):
    """Mejor correlación cruzada normalizada de la plantilla y en x.

    Devuelve (mejor_corr, lag_s). FFT con numpy, sin scipy:
    corr[j] = irfft(X * rfft(y[::-1]))[j + M - 1]. Una NCC es
    ≤ 1.0 por construcción (autocomprobado con un asset contra
    sí mismo: 1.000).
    """
    import numpy as np
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    m = len(y)
    if m == 0 or len(x) < m:
        return 0.0, 0.0
    n = len(x) + m
    nfft = 1 << int(np.ceil(np.log2(n)))
    X = np.fft.rfft(x, nfft)
    Y = np.fft.rfft(y[::-1], nfft)
    full = np.fft.irfft(X * Y, nfft)
    corr = full[m - 1:m - 1 + (len(x) - m + 1)]
    x2 = np.concatenate([[0.0], np.cumsum(x ** 2)])
    win = x2[m:] - x2[:-m]
    y_e = float(np.sum(y ** 2))
    denom = np.sqrt(np.maximum(win * y_e, 1e-12))
    ncc = corr / denom
    i = int(np.argmax(ncc))
    return float(ncc[i]), i / sr


_SFX_TEMPLATE_CACHE = {}


def _sfx_template(effect, sr=16000):
    """Ventana audible de un efecto del pack, decodificada (16 kHz).

    Cacheada por proceso: el gate la usa una vez por clip.
    """
    from .audio import decode_audio
    from .sfx_mix import EFFECTS, sfx_path

    if effect in _SFX_TEMPLATE_CACHE:
        return _SFX_TEMPLATE_CACHE[effect]
    info = EFFECTS[effect]
    tpl, _ = decode_audio(sfx_path(effect))
    a = int(round(info["loud_offset"] * sr))
    b = int(round((info["loud_offset"] + info["loud_duration"]) * sr))
    out = (tpl[a:b], sr)
    _SFX_TEMPLATE_CACHE[effect] = out
    return out


def detect_sfx_in_clip(clip_path, min_ncc=None):
    """¿Se oye algún SFX del pack en el audio del clip?

    Correlación cruzada normalizada de cada efecto del pack (su
    ventana audible) contra el audio del clip: el SFX mezclado es
    el propio archivo con ganancia, así que la correlación en su
    segundo es ~1 (52h: boom 0.994, riser 0.979); sin SFX, el
    mejor asset queda ≤ 0.22 (scooter). ``min_ncc`` sale de
    config.yaml (validator.sfx_min_ncc) vía template_gate; sin él,
    la constante calibrada. Nunca levanta: sin audio o sin
    decodificar, no encontrado con el motivo.
    """
    import subprocess

    threshold = (_SFX_MIN_NCC if min_ncc is None else float(min_ncc))
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", str(clip_path)],
            capture_output=True, text=True, timeout=30)
        has_audio = "audio" in (probe.stdout or "")
    except Exception:  # noqa: BLE001 - sin ffprobe no hay audio
        has_audio = False
    if not has_audio:
        return {"found": False, "effect": None, "corr": 0.0,
                "lag_s": None,
                "motivo": "sin pista de audio: SFX no verificable"}
    try:
        from .audio import decode_audio
        audio, sr = decode_audio(str(clip_path))
    except Exception as exc:  # noqa: BLE001 - el audio no tumba
        return {"found": False, "effect": None, "corr": 0.0,
                "lag_s": None,
                "motivo": f"audio no decodificable: {exc}"}
    if len(audio) == 0:
        return {"found": False, "effect": None, "corr": 0.0,
                "lag_s": None, "motivo": "audio vacío"}
    try:
        from .sfx_mix import EFFECTS
    except Exception as exc:  # noqa: BLE001
        return {"found": False, "effect": None, "corr": 0.0,
                "lag_s": None,
                "motivo": f"pack SFX no disponible: {exc}"}
    best = {"effect": None, "corr": 0.0, "lag_s": None}
    for effect in EFFECTS:
        try:
            template, _sr = _sfx_template(effect, sr)
        except Exception:  # noqa: BLE001 - un asset roto no tumba
            continue
        if len(template) == 0 or len(audio) < len(template):
            continue
        corr, lag = _ncc_best(audio, template, sr)
        if corr > best["corr"]:
            best = {"effect": effect, "corr": corr, "lag_s": lag}
    found = best["corr"] >= threshold
    best["found"] = found
    best["motivo"] = "" if found else (
        "sin SFX del pack en el audio "
        f"(mejor NCC {best['corr']:.2f}"
        + (f" con {best['effect']}" if best.get("effect") else "")
        + f", mínimo {threshold:.2f})")
    return best


def _srt_active_times(srt_path):
    """(inicio, fin) en segundos de cada entrada de un .srt, o [].

    Si no hay SRT o no se puede leer, []: el chequeo de presencia
    de subtítulos queda débil (se registra) y nunca se inventa.
    """
    if not srt_path or not os.path.isfile(str(srt_path)):
        return []
    try:
        with open(srt_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []

    def _to_s(ts):
        parts = ts.strip().replace(",", ".").split(":")
        try:
            if len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + float(s)
            if len(parts) == 2:
                m, s = parts
                return int(m) * 60 + float(s)
        except ValueError:
            return None
        return None

    spans = []
    for line in text.splitlines():
        if "-->" not in line:
            continue
        a, b = line.split("-->", 1)
        start, end = _to_s(a), _to_s(b)
        if start is not None and end is not None and end > start:
            spans.append((start, end))
    return spans


def _is_active(ts, spans, margin=0.5):
    """True si el instante cae dentro de algún tramo de subtítulo."""
    return any(s - margin <= ts <= e + margin for s, e in spans)


def extract_gate_frames(clip_path, out_dir, every_s, max_frames=None):
    """Fotogramas muestreados en TODO el clip (no 4, no pre-render).

    Un fotograma cada ``every_s`` segundos (de config.yaml), con
    tope ``max_frames``. Devuelve [{"path", "ts"}]. Lanza
    FileNotFoundError si no hay clip y RuntimeError si ffmpeg falla.
    """
    from .thumbnails import _extract_frame, get_duration

    clip_path = str(clip_path)
    if not os.path.isfile(clip_path):
        raise FileNotFoundError(f"clip no encontrado: {clip_path}")
    duration = get_duration(clip_path) or 0.0
    if duration <= 0:
        raise RuntimeError(f"sin duración medible: {clip_path}")
    times = []
    t = 1.0
    while t < duration - 0.5:
        times.append(round(t, 2))
        t += float(every_s)
    if not times:
        times = [max(0.5, duration / 2.0)]
    if max_frames is not None:
        times = times[:int(max_frames)]
    os.makedirs(out_dir, exist_ok=True)
    frames = []
    for i, ts in enumerate(times, start=1):
        path = _extract_frame(
            clip_path, ts,
            os.path.join(out_dir, f"gate_{i:02d}_{ts:.1f}s.jpg"))
        frames.append({"path": str(path), "ts": float(ts)})
    return frames


def _haar_faces_on_band(band_arr, min_face_w):
    """Caras Haar en la banda de webcam (array RGB 1080x702).

    Devuelve [(x, y, w, h)] en px de la banda, o None si Haar no
    está disponible en este entorno (cv2 sin CascadeClassifier).
    Es el eslabón "face-crop" de la cadena.
    """
    try:
        from .render import _haar_classifier
    except ImportError:
        return None
    clf = _haar_classifier()
    if clf is False or clf is None:
        return None
    try:
        import cv2
        gray = cv2.cvtColor(band_arr, cv2.COLOR_RGB2GRAY)
    except ImportError:
        return None
    try:
        faces = clf.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=4,
            minSize=(int(min_face_w), int(min_face_w)))
    except Exception:  # noqa: BLE001 - Haar nunca tumba el gate
        return None
    return [(int(x), int(y), int(w), int(h)) for x, y, w, h in faces]


def _band_video(clip_path, out_path, recipe):
    """Vídeo solo con la zona de cámara de la receta.

    Crop a la caja ``cam`` de la plantilla (p. ej. 1080x702:0:0 en
    willyrex, 583x595:248:0 en rdjavi): es lo que ve YuNet.
    """
    import subprocess
    cam = recipe["cam"]
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(clip_path),
           "-vf", (f"crop={cam['w']}:{cam['h']}:{cam['x']}:"
                   f"{cam['y']}"),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-an", str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return out_path


def face_gate(clip_path, vcfg, recipe, frames=None, out_dir=None,
              facecrop_fn=None, edge_fn=None, yunet_fn=None):
    """Cara visible en el VÍDEO FINAL, a lo largo de todo el clip.

    Cadena de detección en ESTE orden (fallbacks): face-crop
    (Haar sobre la zona de cámara de LA PLANTILLA DEL CLIP),
    bordes fijos (rectángulo de cámara con bordes que no se
    mueven) y YuNet (cara estable + rectángulo). El primer eslabón
    que encuentra cara en >= ``face_min_share`` de las muestras
    hace pasar la puerta. Si un eslabón encuentra cara pero por
    debajo del mínimo, es DUDA: no pasa, pero NO cierra la
    cadena (se guarda y se sigue con el siguiente eslabón: YuNet
    puede verla donde Haar no — caso 117, Haar 5/20 y YuNet
    12/12). Si un eslabón no encuentra ninguna cara, se sigue
    con el siguiente fallback (un detector que no ve la cara no
    es duda, es "no la encontró"). Si ningún eslabón encuentra
    cara, no pasa; si alguno dejó duda, se devuelve la duda con
    más muestras (no un fallo limpio). Si ningún detector está
    disponible en el entorno, no pasa (se registra como fallo de
    entorno, no como aprobado).

    ``recipe`` es la receta de la plantilla (config.yaml
    `templates`): la cara se busca en su zona de cámara, no en
    una banda fija (en rdjavi la cámara es centrada 583x595, no
    ancho completo).
    como fallo de entorno, no como aprobado).

    ``facecrop_fn`` / ``edge_fn`` / ``yunet_fn``: inyectables en
    tests (por defecto, los detectores reales). Devuelven el dict
    de la puerta: pasa, método, muestras con cara, totales, anchos
    de cara y detalle por eslabón.
    """
    min_share = float(vcfg["face_min_share"])
    min_frames = int(vcfg["face_min_frames"])
    cam = recipe["cam"]
    cam_w = int(cam["w"])
    cam_y1 = int(cam["y"]) + int(cam["h"])
    game_y0 = int(recipe["game"]["y0"])
    min_face_w = float(vcfg["face_min_width_frac"]) * cam_w
    out_dir = out_dir or os.path.join(
        os.path.dirname(str(clip_path)), "final_gate")
    os.makedirs(out_dir, exist_ok=True)
    links = {}
    # Duda guardada (no devuelta): un eslabón que ve cara pero por
    # debajo del mínimo no cierra la cadena; si ningún eslabón
    # pasa, se devuelve la duda con más muestras al final.
    duda_result = None
    try:
        frames = frames or extract_gate_frames(
            clip_path, out_dir,
            every_s=float(vcfg["face_sample_every_s"]),
            max_frames=_GATE_MAX_FRAMES)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        return {"pasa": False, "method": None, "share": 0.0,
                "frames_with_face": 0, "frames_total": 0,
                "face_widths": [], "links": {},
                "motivo": f"sin fotogramas para la puerta de cara: {exc}",
                "duda": False}
    total = len(frames)
    band_arrays = []
    for fr in frames:
        try:
            arr = _frame_array(fr["path"])
        except (OSError, ValueError):
            continue
        band_arrays.append((fr["ts"], arr[
            int(cam["y"]):int(cam["y"]) + int(cam["h"]),
            int(cam["x"]):int(cam["x"]) + int(cam["w"]), :]))
    if len(band_arrays) < max(1, min(min_frames, total)):
        return {"pasa": False, "method": None, "share": 0.0,
                "frames_with_face": 0, "frames_total": total,
                "face_widths": [], "links": links,
                "motivo": ("muy pocos fotogramas legibles para la "
                           "puerta de cara"),
                "duda": False}
    n = len(band_arrays)

    # Eslabón 1: face-crop (Haar en cada fotograma de la banda).
    if facecrop_fn is not None:
        crop = facecrop_fn(band_arrays, min_face_w)
    else:
        crop = None
        per_frame = []
        for _ts, band in band_arrays:
            faces = _haar_faces_on_band(band, min_face_w)
            if faces is None:
                per_frame = None
                break
            per_frame.append(faces)
        if per_frame is not None:
            crop = {"available": True,
                    "frames_with_face": sum(
                        1 for f in per_frame if f),
                    "frames_total": n,
                    "face_widths": [w for f in per_frame
                                    for (_x, _y, w, _h) in f],
                    "detail": "Haar en la banda de webcam"}
        else:
            crop = {"available": False,
                    "detail": "Haar no disponible en este entorno"}
    links["facecrop"] = crop
    if crop.get("available"):
        share = crop["frames_with_face"] / max(1, crop["frames_total"])
        if share >= min_share:
            return {"pasa": True, "method": "facecrop", "share": share,
                    "frames_with_face": crop["frames_with_face"],
                    "frames_total": crop["frames_total"],
                    "face_widths": crop.get("face_widths") or [],
                    "links": links, "motivo": "",
                    "duda": False}
        if crop["frames_with_face"] > 0:
            # Duda: se guarda y se sigue la cadena (YuNet puede
            # ver la cara donde Haar no: caso 117).
            duda_result = {"pasa": False, "method": "facecrop",
                           "share": share,
                           "frames_with_face": crop["frames_with_face"],
                           "frames_total": crop["frames_total"],
                           "face_widths": crop.get("face_widths") or [],
                           "links": links,
                           "motivo": (f"cara en solo "
                                      f"{crop['frames_with_face']}/"
                                      f"{crop['frames_total']} muestras "
                                      f"({share:.0%}, mínimo "
                                      f"{min_share:.0%}): duda, no pasa"),
                           "duda": True}
        # 0 caras: no es duda, es "no la encontró": se sigue con
        # bordes y YuNet (fallbacks en ese orden).

    # Eslabón 2: bordes fijos (rectángulo de cámara en el vídeo
    # final + cara dentro de ese rectángulo). En un render correcto
    # (banda a ancho completo) no hay rectángulo interior: el
    # eslabón no dispara y se sigue a YuNet, no es un fallo.
    if edge_fn is not None:
        edge = edge_fn(clip_path, band_arrays, min_face_w)
    else:
        edge = None
        try:
            from .edge_facecam import detect as _edge_detect
            found = _edge_detect(clip_path)
        except Exception:  # noqa: BLE001 - el rescate nunca tumba
            found = None
        if not found:
            edge = {"available": True, "fired": False,
                    "detail": "sin rectángulo fijo (banda a ancho "
                              "completo, lo esperado)"}
        else:
            bx, by = int(found["x"]), int(found["y"])
            bw, bh = int(found["w"]), int(found["h"])
            in_band = by < cam_y1 and by + bh <= game_y0
            inside = 0
            for _ts, band in band_arrays:
                sub = band[max(0, by):by + bh, max(0, bx):bx + bw]
                if sub.size == 0:
                    continue
                faces = _haar_faces_on_band(
                    sub, min_face_w * 0.5)
                if faces:
                    inside += 1
            edge = {"available": True, "fired": bool(in_band),
                    "box": [bx, by, bw, bh],
                    "frames_with_face": inside, "frames_total": n,
                    "detail": ("rectángulo fijo "
                               f"{[bx, by, bw, bh]} "
                               f"con cara dentro en {inside}/{n} "
                               "muestras" if in_band else
                               "rectángulo fijo fuera de la banda "
                               "de webcam (layout no-Willyrex)")}
    links["edge"] = edge
    if edge.get("available") and edge.get("fired"):
        share = edge["frames_with_face"] / max(1, edge["frames_total"])
        if share >= min_share:
            return {"pasa": True, "method": "edge", "share": share,
                    "frames_with_face": edge["frames_with_face"],
                    "frames_total": edge["frames_total"],
                    "face_widths": [],
                    "links": links, "motivo": "",
                    "duda": False}
        if edge["frames_with_face"] > 0:
            # Duda: se guarda y se sigue a YuNet (misma regla que
            # en el eslabón face-crop).
            if duda_result is None or (
                    edge["frames_with_face"]
                    > duda_result["frames_with_face"]):
                duda_result = {"pasa": False, "method": "edge",
                               "share": share,
                               "frames_with_face":
                                   edge["frames_with_face"],
                               "frames_total": edge["frames_total"],
                               "face_widths": [],
                               "links": links,
                               "motivo": (f"rectángulo fijo con cara en "
                                          f"solo {edge['frames_with_face']}/"
                                          f"{edge['frames_total']} muestras: "
                                          "duda, no pasa"),
                               "duda": True}
        # 0 caras dentro del rectángulo: no es duda, se sigue a YuNet.

    # Eslabón 3: YuNet (cara estable en el vídeo de la banda).
    if yunet_fn is not None:
        yunet = yunet_fn(clip_path, band_arrays)
    else:
        yunet = None
        try:
            band_video = _band_video(
                clip_path, os.path.join(out_dir, "band.mp4"), recipe)
            from .yunet_facecam import find_cam
            res = find_cam(band_video)
        except Exception as exc:  # noqa: BLE001 - YuNet nunca tumba
            res = {"mode": "none", "confidence": 0.0,
                   "reason": f"YuNet no disponible: {exc}"}
        mode = (res or {}).get("mode")
        if mode in ("cam", "face"):
            face = (res or {}).get("face") or []
            widths = []
            if len(face) == 4:
                widths = [float(face[2]) * cam_w]
            yunet = {"available": True, "fired": True,
                     "frames_with_face": n, "frames_total": n,
                     "face_widths": widths,
                     "detail": (f"YuNet modo {mode}, confianza "
                                f"{(res or {}).get('confidence')}: "
                                f"{(res or {}).get('reason') or ''}")}
        else:
            yunet = {"available": True, "fired": False,
                     "detail": (f"YuNet modo {mode}: "
                                f"{(res or {}).get('reason') or ''}")}
    links["yunet"] = yunet
    if yunet.get("available") and yunet.get("fired"):
        return {"pasa": True, "method": "yunet", "share": 1.0,
                "frames_with_face": yunet.get("frames_with_face") or n,
                "frames_total": yunet.get("frames_total") or n,
                "face_widths": yunet.get("face_widths") or [],
                "links": links, "motivo": "",
                "duda": False}
    # Ningún eslabón pasó. Si alguno dejó duda, se devuelve la
    # duda (con los links completos), no un fallo limpio.
    if duda_result is not None:
        duda_result["links"] = links
        return duda_result
    tried = [k for k, v in links.items() if v.get("available")]
    if not tried:
        motivo = ("sin detector de cara disponible en este entorno "
                  "(ni Haar, ni bordes, ni YuNet): no se puede "
                  "verificar la cara, no pasa")
    else:
        motivo = ("sin cara verificada en el vídeo final "
                  f"(eslabones probados: {', '.join(tried)})")
    return {"pasa": False, "method": None, "share": 0.0,
            "frames_with_face": 0, "frames_total": total,
            "face_widths": [], "links": links, "motivo": motivo,
            "duda": False}


def template_gate(clip_path, vcfg, recipe, frames=None, srt_path=None,
                  face_result=None, out_dir=None, sfx_events=None):
    """La receta en el VÍDEO FINAL: montaje + fuentes + subtítulos.

    Comprueba en píxeles, sobre los mismos fotogramas muestreados
    en todo el clip, la receta de LA PLANTILLA DEL CLIP
    (config.yaml `templates`, `recipe`): rdjavi v1 (karaoke al pie
    de la cámara), willyrex v2 (fijos amarillos en banda inferior)
    o rubius (blancos palabra por palabra sobre el gameplay).
    La regla "subtítulos sobre el gameplay" es POR PLANTILLA: en
    rubius VAN sobre el gameplay y eso es correcto; en rdjavi y
    willyrex siguen prohibidos.

    ``sfx_events`` es la lista de eventos SFX que apply_sfx
    decidió mezclar (None = el llamador no la conoce: se exige
    presencia como antes; [] = no se decidió nada: el chequeo
    pasa, ver sfx_presente abajo).

    Sin SRT, la presencia de subtítulos no se puede verificar y
    queda registrada como débil (nunca se inventa): el resto de
    chequeos sigue. Devuelve {pasa, checks, fallos, motivo}.
    """
    out_dir = out_dir or os.path.join(
        os.path.dirname(str(clip_path)), "final_gate")
    checks = {}

    def _check(name, ok, medicion, motivo=""):
        checks[name] = {"pasa": bool(ok), "medicion": medicion,
                        "motivo": motivo if not ok else ""}
        return bool(ok)

    try:
        frames = frames or extract_gate_frames(
            clip_path, out_dir,
            every_s=float(vcfg["face_sample_every_s"]),
            max_frames=_GATE_MAX_FRAMES)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        return {"pasa": False, "checks": checks,
                "fallos": ["sin_fotogramas"],
                "motivo": f"sin fotogramas para la plantilla: {exc}"}
    spans = _srt_active_times(srt_path)
    arrays = []
    for fr in frames:
        try:
            arrays.append((fr["ts"], _frame_array(fr["path"])))
        except (OSError, ValueError):
            continue
    if not arrays:
        return {"pasa": False, "checks": checks,
                "fallos": ["sin_fotogramas"],
                "motivo": "ningún fotograma legible para la plantilla"}
    # 1. Dimensiones 1080x1920 (común a las tres).
    h0, w0 = arrays[0][1].shape[:2]
    _check("dimensiones", (w0, h0) == (W, H),
           f"{w0}x{h0}", "el clip no es 1080x1920")
    style = (recipe or {}).get("subs_style")
    if style == "fixed_yellow":
        _template_fixed_yellow(
            _check, arrays, spans, recipe, face_result)
    elif style == "karaoke":
        _template_karaoke(_check, arrays, spans, recipe, face_result)
    elif style == "word_white":
        _template_word_white(
            _check, arrays, spans, recipe, face_result, vcfg=vcfg)
        # SFX del pack en el audio (solo rubius). 2026-11-24: la
        # puerta verifica lo que SE DECIDIÓ poner, no lo que nunca
        # se decidió. decide_sfx devuelve [] por diseño en clips
        # hablados sin dip de silencio ni grito ni fail (sin boom,
        # sin riser, sin fail); exigir SFX entonces rechaza clips
        # buenos (run 45299157326: 4/5 cortes con sfx []). Si no
        # se decidió nada (sfx_events == []), el chequeo pasa. Si
        # se decidió SFX y no aparece en el audio, sigue fallando:
        # el mezclador roto sigue detectándose (apply_sfx traga
        # errores y devuelve el vídeo sin tocar, y su early-return
        # `reused` devuelve events: [] sin mezclar). Sin
        # sfx_events (None: llamador sin la info), se exige
        # presencia como antes.
        if sfx_events is not None and len(sfx_events) == 0:
            _check("sfx_presente", True,
                   "sin SFX decididos (decide_sfx vacío)", "")
        else:
            sfx = detect_sfx_in_clip(
                clip_path, min_ncc=float(vcfg["sfx_min_ncc"]))
            if sfx["found"]:
                _check("sfx_presente", True,
                       f"{sfx['effect']} (NCC {sfx['corr']:.2f} en "
                       f"t={sfx['lag_s']:.1f}s)", "")
            else:
                _check("sfx_presente", False,
                       f"mejor NCC {sfx['corr']:.2f}",
                       sfx.get("motivo") or "sin SFX del pack en el audio")
    else:
        return {"pasa": False, "checks": checks,
                "fallos": ["subs_style"],
                "motivo": ("plantilla: subs_style desconocido "
                           f"{style!r}")}
    # Fuente del estilo disponible en el entorno (la letra Arial
    # Black es la de las tres recetas; sin ella libass cae a otra
    # fuente y el clip no sigue la receta aunque el montaje lo sea).
    font_ok, font_detail = _arial_black_available()
    if font_ok is None:
        _check("fuente_ariblk", True,
               f"no verificable: {font_detail}", "")
    else:
        _check("fuente_ariblk", bool(font_ok), font_detail,
               "fuente Arial Black no disponible en este entorno")
    fallos = [k for k, v in checks.items() if not v["pasa"]]
    motivo = ""
    if fallos:
        motivo = "plantilla: " + ", ".join(
            f"{k} ({checks[k]['motivo'] or checks[k]['medicion']})"
            for k in fallos)
    return {"pasa": not fallos, "checks": checks, "fallos": fallos,
            "motivo": motivo}


def _template_share(arrays, pred):
    if not arrays:
        return 0.0
    return sum(1 for _ts, a in arrays if pred(a)) / len(arrays)


def _template_fixed_yellow(check, arrays, spans, recipe, face_result):
    """Receta willyrex (v2): dos bandas negras, título en el medio,
    subtítulos fijos amarillos abajo, nada sobre el gameplay."""
    import numpy as np

    cam = recipe["cam"]
    cam_y1 = int(cam["y"]) + int(cam["h"])
    ty0, ty1 = (int(recipe["title_band"]["y0"]),
                int(recipe["title_band"]["y1"]))
    gy0, gy1 = (int(recipe["game"]["y0"]),
                int(recipe["game"]["y1"]))
    sy0, sy1 = (int(recipe["subs_band"]["y0"]),
                int(recipe["subs_band"]["y1"]))
    active = [(ts, a) for ts, a in arrays
              if not spans or _is_active(ts, spans)]
    # Banda del medio negra con título blanco.
    title_black = _template_share(
        arrays,
        lambda a: _luma(a[ty0:ty1]).mean() < _GATE_BLACK_LUMA)
    title_text = _template_share(
        arrays,
        lambda a: _count_color(a[ty0:ty1], _WHITE) > _GATE_TEXT_PX)
    check("banda_titulo", title_black >= 0.8 and title_text >= 0.5,
          f"negra {title_black:.0%}, texto blanco {title_text:.0%}",
          "sin banda negra del medio con título blanco")
    # Gameplay presente (no vacío/negro).
    game_ok = _template_share(
        arrays,
        lambda a: _luma(a[gy0:gy1]).mean() > _GATE_GAME_LUMA_MIN
        and _luma(a[gy0:gy1]).std() > 5.0)
    check("gameplay", game_ok >= 0.8, f"presente {game_ok:.0%}",
          "gameplay vacío o negro")
    # Banda inferior negra.
    subs_black = _template_share(
        arrays,
        lambda a: _luma(a[sy0:sy1]).mean() < _GATE_BLACK_LUMA)
    check("banda_subtitulos", subs_black >= 0.8,
          f"negra {subs_black:.0%}", "sin banda negra inferior")
    # Subtítulos amarillos en la banda inferior (solo con SRT).
    if spans and active:
        yellow_share = sum(
            1 for _ts, a in active
            if _count_color(a[sy0:sy1], _YELLOW)
            > _GATE_SUB_PX) / len(active)
        check("subtitulos_amarillos", yellow_share >= 0.5,
              f"amarillos en {yellow_share:.0%} de los instantes "
              "con subtítulo",
              "sin subtítulos amarillos en la banda inferior")
    else:
        check("subtitulos_amarillos", True,
              "sin SRT: presencia no verificada (débil)",
              "")
    # Nada de subtítulos sobre el gameplay (franja inferior,
    # centro: ahí caería un subtítulo quemado con MarginV viejo).
    over_game = 0
    for _ts, a in active or arrays:
        strip = a[gy1 - 140:gy1, 200:880]
        if _count_color(strip, _YELLOW) > _GATE_SUB_PX:
            over_game += 1
    check("subs_sin_solapar", over_game == 0,
          f"{over_game} fotogramas con amarillo sobre el gameplay",
          "subtítulos sobre el gameplay (tapan los del juego)")
    # Nada de karaoke: texto blanco en la banda inferior.
    karaoke = 0
    for _ts, a in active or arrays:
        if _count_color(a[sy0:sy1], _WHITE) > _GATE_SUB_PX:
            karaoke += 1
    check("sin_karaoke", karaoke == 0,
          f"{karaoke} fotogramas con texto blanco en la banda "
          "inferior",
          "subtítulos karaoke (palabras en blanco)")
    # Cámara a ancho completo: si el eslabón de bordes encontró
    # un rectángulo dentro de la banda, no es a ancho completo.
    _check_full_width(check, face_result)
    # Sin fondo borroso: energía de bordes en las franjas
    # laterales de la banda vs centro (cámara centrada sobre fondo
    # desenfocado = laterales mucho más borrosos que el centro).
    ratios = []
    for _ts, a in arrays:
        band = _luma(a[:cam_y1])
        gy, gx = np.gradient(band)
        energy = np.abs(gx) + np.abs(gy)
        edge_e = float(np.concatenate(
            [energy[:, :120].ravel(),
             energy[:, -120:].ravel()]).mean())
        center_e = float(energy[:, 360:720].mean())
        if center_e > 0:
            ratios.append(edge_e / center_e)
    if ratios:
        ratios.sort()
        median_ratio = ratios[len(ratios) // 2]
        check("sin_fondo_borroso", median_ratio >= _GATE_BLUR_RATIO,
              f"ratio bordes/centro {median_ratio:.2f}",
              "fondo borroso detrás de la cámara")
    else:
        check("sin_fondo_borroso", True, "sin medida", "")
    _check_cam_live(check, arrays, cam)


def _template_karaoke(check, arrays, spans, recipe, face_result):
    """Receta rdjavi (v1): streamer centrado 583x595 sobre fondo
    desenfocado, karaoke al pie de la cámara, gameplay abajo y
    barra negra inferior vacía."""
    import numpy as np

    cam = recipe["cam"]
    cx0, cy0 = int(cam["x"]), int(cam["y"])
    cx1, cy1 = cx0 + int(cam["w"]), cy0 + int(cam["h"])
    ky0, ky1 = (int(recipe["karaoke_band"]["y0"]),
                int(recipe["karaoke_band"]["y1"]))
    gy0, gy1 = (int(recipe["game"]["y0"]),
                int(recipe["game"]["y1"]))
    sy0, sy1 = (int(recipe["subs_band"]["y0"]),
                int(recipe["subs_band"]["y1"]))
    active = [(ts, a) for ts, a in arrays
              if not spans or _is_active(ts, spans)]
    # Cámara centrada con textura + laterales desenfocados (el
    # fondo morado/magenta es liso: su energía de bordes es mucho
    # menor que la de la cámara).
    ratios = []
    cam_std_ok = 0
    for _ts, a in arrays:
        band = _luma(a[cy0:cy1])
        gy, gx = np.gradient(band)
        energy = np.abs(gx) + np.abs(gy)
        lat = float(np.concatenate(
            [energy[:, :cx0].ravel(),
             energy[:, cx1:].ravel()]).mean()) if cx0 > 0 else 0.0
        cen = float(energy[:, cx0:cx1].mean())
        if cen > 0:
            ratios.append(lat / cen)
        if float(_luma(a[cy0:cy1, cx0:cx1]).std()) > 5.0:
            cam_std_ok += 1
    if ratios:
        ratios.sort()
        median_ratio = ratios[len(ratios) // 2]
    else:
        median_ratio = 0.0
    cam_share = cam_std_ok / len(arrays) if arrays else 0.0
    check("camara_centrada",
          median_ratio < 0.5 and cam_share >= 0.8,
          f"ratio laterales/cámara {median_ratio:.2f}, cámara con "
          f"textura {cam_share:.0%}",
          "cámara no centrada con fondo desenfocado")
    # Karaoke al pie de la cámara: palabra activa amarilla + resto
    # blanco en la banda 595-749 (solo con SRT).
    if spans and active:
        yellow_share = sum(
            1 for _ts, a in active
            if _count_color(a[ky0:ky1], _YELLOW)
            > _GATE_SUB_PX) / len(active)
        white_share = sum(
            1 for _ts, a in active
            if _count_color(a[ky0:ky1], _WHITE)
            > _GATE_SUB_PX) / len(active)
        check("banda_karaoke",
              yellow_share >= 0.5 and white_share >= 0.3,
              f"amarillo {yellow_share:.0%}, blanco "
              f"{white_share:.0%} en instantes con subtítulo",
              "sin karaoke (amarillo + blanco) al pie de la cámara")
    else:
        check("banda_karaoke", True,
              "sin SRT: presencia no verificada (débil)", "")
    # Gameplay presente (no vacío/negro).
    game_ok = _template_share(
        arrays,
        lambda a: _luma(a[gy0:gy1]).mean() > _GATE_GAME_LUMA_MIN
        and _luma(a[gy0:gy1]).std() > 5.0)
    check("gameplay", game_ok >= 0.8, f"presente {game_ok:.0%}",
          "gameplay vacío o negro")
    # Barra negra inferior vacía.
    subs_black = _template_share(
        arrays,
        lambda a: _luma(a[sy0:sy1]).mean() < _GATE_BLACK_LUMA)
    check("barra_negra", subs_black >= 0.8,
          f"negra {subs_black:.0%}", "sin barra negra inferior")
    # Nada de subtítulos sobre el gameplay (franja justo bajo la
    # banda karaoke: ahí caería un karaoke mal quemado).
    over_game = 0
    for _ts, a in active or arrays:
        strip = a[ky1 + 10:ky1 + 150, 200:880]
        if _count_color(strip, _YELLOW) > _GATE_SUB_PX:
            over_game += 1
    check("subs_sin_solapar", over_game == 0,
          f"{over_game} fotogramas con amarillo sobre el gameplay",
          "subtítulos sobre el gameplay (tapan los del juego)")
    # Nada de texto en la barra inferior (en v1 no van subtítulos
    # ahí: ni amarillos fijos ni karaoke).
    in_bar = 0
    for _ts, a in active or arrays:
        if (_count_color(a[sy0:sy1], _WHITE) > _GATE_SUB_PX
                or _count_color(a[sy0:sy1], _YELLOW)
                > _GATE_SUB_PX):
            in_bar += 1
    check("sin_subs_en_barra", in_bar == 0,
          f"{in_bar} fotogramas con texto en la barra inferior",
          "subtítulos en la barra inferior (van al pie de cámara)")
    _check_cam_live(check, arrays, cam)


def _template_word_white(check, arrays, spans, recipe, face_result,
                         vcfg=None):
    """Receta rubius: cámara a ancho completo, gameplay
    sin bandas, título en banner amarillo transitorio, subtítulos
    blancos palabra por palabra SOBRE el gameplay (arriba).

    Los umbrales (ventana y fracción del banner, ancho y
    cobertura de los bordes de subtítulos) salen de config.yaml
    (validator.*) vía ``vcfg``; sin él, las constantes calibradas.
    """
    cam = recipe["cam"]
    gy0, gy1 = (int(recipe["game"]["y0"]),
                int(recipe["game"]["y1"]))
    banner = recipe["title_banner"]
    bx0, bx1 = int(banner["x0"]), int(banner["x1"])
    by0, by1 = int(banner["y0"]), int(banner["y1"])
    zy0, zy1 = (int(recipe["subs_zone"]["y0"]),
                int(recipe["subs_zone"]["y1"]))
    t0 = float(vcfg["rubius_banner_t0"]) if vcfg else _RUBIUS_BANNER_T0
    t1 = float(vcfg["rubius_banner_t1"]) if vcfg else _RUBIUS_BANNER_T1
    # Instantes con subtítulo: fuera de la ventana del banner
    # (make_word_ass suprime las palabras que caen ahí: contar
    # esos instantes como activos era el falso negativo del 174)
    # y con pertenencia estricta al cue (sin margen: un instante
    # 0.5 s antes de la palabra no tiene palabra en pantalla).
    active = [(ts, a) for ts, a in arrays
              if not spans or (_is_active(ts, spans, margin=0)
                              and not (t0 <= ts <= t1))]
    # Cámara a ancho completo (sin rectángulo interior).
    _check_full_width(check, face_result)
    # Gameplay presente (no vacío/negro).
    game_ok = _template_share(
        arrays,
        lambda a: _luma(a[gy0:gy1]).mean() > _GATE_GAME_LUMA_MIN
        and _luma(a[gy0:gy1]).std() > 5.0)
    check("gameplay", game_ok >= 0.8, f"presente {game_ok:.0%}",
          "gameplay vacío o negro")
    # Sin bandas negras: ni arriba del gameplay ni abajo del todo
    # (el corte es limpio; una banda delata otra plantilla). Una
    # barra con subtítulos encima sigue siendo barra: se mide la
    # fracción de píxeles casi negros, no la media. Pero una
    # escena OSCURA del juego no es una banda: una banda real es
    # casi negra Y uniforme Y con un salto nítido al contenido
    # adyacente (el juego oscuro no tiene salto: lo de al lado
    # está igual de oscuro; un fundido a negro, tampoco).
    def _is_bar(a, y0, y1, ay0, ay1):
        zone = _luma(a[y0:y1])
        if float((zone < _GATE_BLACK_LUMA).mean()) <= 0.9:
            return False
        if float(zone.std()) > _GATE_BAR_STD:
            return False
        step = float(_luma(a[ay0:ay1]).mean()) - float(zone.mean())
        return step >= _GATE_BAR_STEP

    # La ventana adyacente está 100 px fuera de la franja (no
    # pegada a ella): una banda real puede medir hasta ~180 px y
    # si la ventana cae dentro de la banda el salto da 0 y la
    # banda no se detecta (test_rubius_con_bandas_falla).
    top_ok = _template_share(
        arrays, lambda a: not _is_bar(a, gy0, gy0 + 100,
                                      gy0 + 200, gy0 + 220))
    bottom_ok = _template_share(
        arrays, lambda a: not _is_bar(a, gy1 - 120, gy1,
                                      gy1 - 220, gy1 - 200))
    check("sin_bandas", top_ok >= 0.8 and bottom_ok >= 0.8,
          f"arriba {top_ok:.0%}, abajo {bottom_ok:.0%} sin banda "
          "negra",
          "hay banda negra (no es el corte limpio rubius)")
    # Título en banner amarillo TRANSITORIO (t=0.5-3.5 s de la
    # receta): tiene que aparecer en esa ventana, con el amarillo
    # DOMINANDO su zona, O como texto amarillo sobre fondo casi
    # negro (caso 170: _banner_title_present, rama b). Un conteo
    # bajo de píxeles amarillos lo pasa cualquier escena de juego
    # con amarillo (caso scooter 2026-11-21: 890 px amarillos de
    # la calle en la zona del banner, sin banner).
    yellow_frac = (float(vcfg["rubius_banner_yellow_frac"]) if vcfg
                   else _RUBIUS_BANNER_YELLOW_FRAC)
    edge_w = int(vcfg["rubius_subs_edge_w"]) if vcfg else None
    outline_frac = (float(vcfg["rubius_subs_outline_frac"]) if vcfg
                    else _RUBIUS_SUBS_OUTLINE_FRAC)
    window = [(ts, a) for ts, a in arrays
              if t0 <= ts <= t1]
    pool = window if window else arrays
    banner_frames = 0
    best_frac = 0.0
    best_detail = ""
    for _ts, a in pool:
        zone = a[by0:by1, bx0:bx1]
        frac = _banner_yellow_frac(zone)
        best_frac = max(best_frac, frac)
        ok, detail = _banner_title_present(zone, yellow_frac)
        if ok:
            banner_frames += 1
            best_detail = detail
    check("titulo_banner", banner_frames >= 1,
          f"banner en {banner_frames}/{len(pool)} fotogramas de "
          f"la ventana (amarillo máx. {best_frac:.0%}"
          + (f", {best_detail}" if best_detail else "") + ")",
          f"sin banner amarillo de título en t={t0}-{t1} s")
    # Subtítulos blancos palabra por palabra sobre el gameplay
    # (zona central de la franja 900-1020; solo con SRT). El
    # umbral es el de UNA palabra corta (_GATE_SUB_WORD_PX): con
    # habla escasa y palabras cortas, 300 px pedía más blanco
    # del que cabe en una palabra (caso 174: palabra real de
    # 1679 px, pero el instante con una sola palabra corta no
    # llegaba a 300). V7: el suelo es el de texto presente (50
    # px) para que tampoco se pierda un glifo fino de 1-2 letras
    # degradado por contorno y JPEG.
    if spans and active:
        white_share = sum(
            1 for _ts, a in active
            if _count_color(a[zy0:zy1, 350:730], _WHITE)
            > _GATE_SUB_WORD_PX) / len(active)
        check("subtitulos_blancos", white_share >= 0.5,
              f"blancos en {white_share:.0%} de los instantes con "
              "subtítulo",
              "sin subtítulos blancos sobre el gameplay")
    else:
        check("subtitulos_blancos", True,
              "sin SRT: presencia no verificada (débil)", "")
    # Una palabra a la vez: el subtítulo centrado nunca toca los
    # dos bordes de su zona a la vez; una línea completa sí (caso
    # scooter 2026-11-21: 4/7 fotogramas con texto en ambos
    # bordes; 52h: 0/5). Solo con SRT: sin SRT la presencia no se
    # puede verificar y queda débil, como subtitulos_blancos.
    # 2026-11-24 (Fix 3): el defecto era de ATRIBUCIÓN, no de
    # conteo — el juego claro (cielo, mapa, suelo) cubre los
    # bordes con blanco aunque el subtítulo sea una sola palabra
    # centrada (119/179 del run 45310466231: 2 fotogramas cada
    # uno). El check ya no mide blanco, mide TEXTO: blanco con
    # contorno negro pegado (``_subs_edge_outlined_coverages``,
    # por borde, umbral ``rubius_subs_outline_frac``). El scooter
    # real da 0.034-0.092 por borde; los FP 119/179, <= 0.0148.
    # Se conserva la regla de conteo Fix 2 (falla solo si hay
    # >=2 fotogramas Y >10% de los activos: la línea completa
    # real es sistemática) y NO hay vía de severidad: un solo
    # fotograma, por alto que sea su outlined, nunca falla
    # (decisión conservadora, spec §8.4).
    if spans and active:
        both = 0
        both_ts = []
        worst_l = 0.0
        worst_r = 0.0
        for _ts, a in active:
            left, right = _subs_edge_outlined_coverages(
                a, zy0, zy1, edge_w=edge_w)
            worst_l = max(worst_l, left)
            worst_r = max(worst_r, right)
            if left > outline_frac and right > outline_frac:
                both += 1
                both_ts.append(_ts)
        share = both / len(active) if active else 0.0
        check("subtitulos_una_palabra",
              not (both >= 2 and share > 0.10),
              f"{both} fotogramas con texto con contorno en ambos "
              f"bordes ({share:.0%} de {len(active)} activos; "
              f"con contorno máx L={worst_l:.3f} R={worst_r:.3f}, "
              f"umbral {outline_frac:.2f})"
              + (f" en t={both_ts}" if both_ts else ""),
              "subtítulos de línea completa (no palabra por palabra)")
    else:
        check("subtitulos_una_palabra", True,
              "sin SRT: presencia no verificada (débil)", "")
    _check_cam_live(check, arrays, cam)


def _check_full_width(check, face_result):
    """Cámara a ancho completo: si el eslabón de bordes encontró
    un rectángulo dentro de la banda, no es a ancho completo."""
    edge_link = (face_result or {}).get("links", {}).get("edge") or {}
    edge_box = edge_link.get("box")
    if edge_box:
        full_width = edge_box[2] >= 0.9 * W
        check("camara_ancho_completo", bool(full_width),
              f"rectángulo fijo {edge_box}",
              "cámara en esquina o centrada, no a ancho completo")
    else:
        check("camara_ancho_completo", True,
              "sin rectángulo interior (banda a ancho completo)", "")


def _check_cam_live(check, arrays, cam):
    """Zona de cámara viva (no congelada)."""
    import numpy as np

    cam_y0 = int(cam["y"])
    cam_y1 = cam_y0 + int(cam["h"])
    cam_x0 = int(cam["x"])
    cam_x1 = cam_x0 + int(cam["w"])
    if len(arrays) > 1:
        diffs = []
        for (_t1, a1), (_t2, a2) in zip(arrays, arrays[1:]):
            diffs.append(float(np.abs(
                _luma(a1[cam_y0:cam_y1, cam_x0:cam_x1])
                - _luma(a2[cam_y0:cam_y1, cam_x0:cam_x1])).mean()))
        live_share = sum(1 for d in diffs if d > _GATE_LIVE_DIFF) \
            / len(diffs)
        check("banda_viva", live_share >= 0.5,
              f"viva en {live_share:.0%} de los pares",
              "zona de cámara congelada")
    else:
        check("banda_viva", True, "un solo fotograma", "")


def _arial_black_available():
    """(True/False/None, detalle) para la fuente Arial Black.

    None = no se puede comprobar en este entorno (sin fc-list ni
    ruta conocida): no falla, se registra. False = comprobado que
    falta: el render no pudo usar la fuente del estilo.
    """
    import shutil
    import subprocess
    candidates = [
        os.path.expanduser("~/.fonts/arial-black.ttf"),
        "/usr/share/fonts/truetype/arial-black.ttf",
    ]
    if any(os.path.isfile(p) for p in candidates):
        return True, "arial-black.ttf presente"
    fc_list = shutil.which("fc-list")
    if not fc_list:
        return None, "sin fc-list ni ruta conocida"
    try:
        out = subprocess.run(
            [fc_list, ":family"], capture_output=True, text=True,
            timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None, "fc-list no responde"
    if "Arial Black" in (out.stdout or ""):
        return True, "Arial Black en fc-list"
    return False, "Arial Black ausente (fc-list)"


def find_facecam_box_in_source(segment_path, duration, cfg=None):
    """Caja de webcam en px del vídeo FUENTE, o None.

    Cadena en orden face-crop -> bordes fijos -> YuNet, con el
    código que ya existe (detect_vod_facecam + detectores
    directos). Es lo que usa el re-render automático para forzar
    la cara que el primer render perdió. Nunca levanta.
    """
    if not segment_path or not os.path.isfile(str(segment_path)):
        return None
    try:
        from .render import _probe_size, detect_vod_facecam
        res = detect_vod_facecam(
            str(segment_path), float(duration or 0), cfg=cfg)
        box = (res or {}).get("facecam_box")
        if box:
            sw, sh = _probe_size(str(segment_path))
            if sw and sh:
                return {
                    "box": [int(round(box[0] * sw / 160)),
                            int(round(box[1] * sh / 90)),
                            int(round(box[2] * sw / 160)),
                            int(round(box[3] * sh / 90))],
                    "method": (res or {}).get("method"),
                }
    except Exception:  # noqa: BLE001 - el rescate nunca tumba
        pass
    try:
        from .yunet_facecam import yunet_facecam_box
        box = yunet_facecam_box(str(segment_path), 0,
                                float(duration or 0))
        if box:
            return {"box": [int(v) for v in box], "method": "yunet"}
    except Exception:  # noqa: BLE001
        pass
    try:
        from .edge_facecam import detect as _edge_detect
        found = _edge_detect(str(segment_path))
        if found and found.get("conf") == "high":
            return {"box": [int(found["x"]), int(found["y"]),
                            int(found["w"]), int(found["h"])],
                    "method": "edge"}
    except Exception:  # noqa: BLE001
        pass
    return None


def review_final_clip(clip_path, title, transcript_text="",
                      out_dir=None, gemini_fn=None, model=None,
                      min_score=None, template_name="rubius"):
    """Valida el MP4 final con Gemini. Nunca lanza excepción.

    ``gemini_fn``: callable(parts) -> texto, inyectable en tests;
    por defecto ``vision._call_gemini``. ``min_score``: nota mínima
    por criterio (de config.yaml; sin él, MIN_SCORE).
    ``template_name``: la plantilla del clip (su receta va en el
    prompt; default rubius, como el default del gate). Devuelve el
    dict de la revisión (ver docstring del módulo).
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
    receta, plantilla_desc = RECIPE_TEXT.get(
        str(template_name or "rubius").lower(),
        RECIPE_TEXT["rubius"])
    prompt = FINAL_REVIEW_PROMPT.format(
        title=title or "(sin título)",
        transcript=transcript_text[:2000] if transcript_text
        else "(sin transcripción en disco)",
        receta=receta, plantilla_desc=plantilla_desc)
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
    fallos_reales = _decide(scores, fallos, min_score=min_score)
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
                            model=None, template_name="rubius"):
    """Enganche de los flujos: si está apagada, no valida nada.

    Devuelve ``decision == "desactivada"`` (sin veredicto) cuando
    la validación está apagada; si no, lo mismo que
    ``review_final_clip``, con una salvedad (2026-11-19, modo
    observación por defecto): un ``rechazado`` se reescribe como
    ``observado`` (``pasa`` True, scores y fallos intactos) para
    que el clip se encole igual y el fallo quede registrado para
    la calibración. Los tres enganches (live_cutter, workers,
    vod_fallback) solo frenan con ``rechazado``, así que
    ``observado`` fluye como un aprobado, con el motivo guardado.
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
            "observe_only": final_review_observe_only(cfg),
        }
    result = review_final_clip(
        clip_path, title, transcript_text=transcript_text,
        out_dir=out_dir, gemini_fn=gemini_fn, model=model,
        template_name=template_name)
    observe = final_review_observe_only(cfg)
    result = dict(result)
    result["observe_only"] = observe
    if observe and result.get("decision") == "rechazado":
        fallos = result.get("fallos") or []
        scores = result.get("scores") or {}
        detalle = result.get("motivo") or ", ".join(
            f"{CRITERIA_ES[c]} ({scores.get(c, 0.0):.1f})"
            for c in fallos)
        result["decision"] = "observado"
        result["pasa"] = True
        result["motivo"] = (
            "observación (no bloquea el encolado): " + detalle
            if detalle else "observación (no bloquea el encolado)")
    return result


def validate_final_clip(clip_path, title, transcript_text="",
                        srt_path=None, out_dir=None, cfg=None,
                        gemini_fn=None, model=None, plantilla=None,
                        facecrop_fn=None, edge_fn=None, yunet_fn=None,
                        sfx_events=None):
    """Validador final DEFINITIVO (2026-11-19): puerta dura de cara
    + plantilla sobre el VÍDEO FINAL RENDERIZADO.

    ``sfx_events``: eventos SFX decididos por apply_sfx en este
    clip (None = desconocido, se exige presencia como antes;
    [] = no se decidió nada, sfx_presente pasa; ver
    template_gate). Sin él, la puerta dura sigue exigiendo SFX
    en el audio aunque decide_sfx no haya puesto ninguno.

    Tres capas independientes, TODAS sobre el vídeo final, y TODAS
    tienen que pasar:
    (i) cara determinista a lo largo de todo el clip (cadena
    face-crop -> bordes fijos -> YuNet, en ese orden, en la zona
    de cámara de LA PLANTILLA DEL CLIP);
    (ii) plantilla determinista (una de TRES recetas en píxeles:
    rdjavi v1, willyrex v2 o rubius; `plantilla` es la declarada
    por el clip y sin ella se usa el default de config.yaml,
    rubius);
    (iii) revisión visual Gemini (con el prompt de la receta).

    La puerta de cara y de plantilla es DURA y no la anula el modo
    observación: si fallan, ``pasa`` es False y ``needs_rerender``
    es True, se re-haga o no el clip después. Si Gemini rechaza
    SOLO criterios subjetivos (momento, coherencia, legibilidad)
    y el modo observación está activo, ese rechazo se registra
    como "observado" y no frena (el juez sin calibrar tumbó 3
    clips buenos en la 01:08). Si Gemini rechaza la CARA o la
    PLANTILLA pero la medida determinista dice que están bien, la
    medida manda y la discrepancia queda registrada. Con
    ``gemini_hard=True`` en config, cualquier rechazo de Gemini
    también dispara re-render.

    La config del gate se carga AQUÍ, al inicio, desde config.yaml
    (load_validator_config): si falta la sección o una clave, el
    run falla en voz alta con ConfigError, nunca sigue con un
    valor inventado. Si la validación está apagada explícitamente
    (final_review.enabled=False), no hay veredicto (desactivada).

    Devuelve el dict de la revisión con, además: ``plantilla``
    (la receta validada), ``hard_pasa`` (cara y plantilla),
    ``needs_rerender``, ``rerender_reason``, ``hard_failures`` y
    las tres capas (``face``, ``template``, ``gemini``). Nunca
    lanza: sin clip o sin fotogramas, la puerta dura falla (no
    pasa) y el motivo lo dice; sin Gemini, el veredicto queda
    pendiente_revision.
    """
    from .config import load_validator_config, resolve_template

    # La config se exige ANTES de mirar nada: un gate sin sus
    # umbrales no valida, falla en voz alta y para el run.
    vcfg = load_validator_config(cfg)
    if not vcfg["enabled"]:
        # Apagada: no hay veredicto ni receta que resolver (la
        # vcfg desactivada no trae geometría); el nombre es el
        # declarado o el default de config.
        template_name = (
            (str(plantilla).strip().lower() if plantilla else None)
            or vcfg.get("default_template") or "rubius")
        return {
            "decision": "desactivada",
            "pasa": None,
            "plantilla": template_name,
            "hard_pasa": None,
            "needs_rerender": False,
            "rerender_reason": "",
            "hard_failures": [],
            "revision": "desactivada",
            "scores": {},
            "motivos": {},
            "fallos": [],
            "motivo": "validación final desactivada "
                      "(final_review.enabled=False)",
            "resumen": "validación final desactivada",
            "frames_n": 0,
            "model": model or MODEL_DEFAULT,
            "transcript_words": len((transcript_text or "").split()),
            "observe_only": bool(vcfg["observe_only"]),
            "face": None, "template": None, "gemini": None,
        }
    template_name, recipe = resolve_template(vcfg, plantilla)
    out_dir = out_dir or os.path.join(
        os.path.dirname(str(clip_path)), "final_gate")
    if not clip_path or not os.path.isfile(str(clip_path)):
        return {
            "decision": "rechazado",
            "pasa": False,
            "plantilla": template_name,
            "hard_pasa": False,
            "needs_rerender": True,
            "rerender_reason": f"clip no encontrado: {clip_path}",
            "hard_failures": ["sin_clip"],
            "revision": "no_disponible",
            "scores": {},
            "motivos": {},
            "fallos": ["sin_clip"],
            "motivo": f"clip no encontrado: {clip_path}",
            "resumen": f"clip no encontrado: {clip_path}",
            "frames_n": 0,
            "model": model or MODEL_DEFAULT,
            "transcript_words": len((transcript_text or "").split()),
            "observe_only": bool(vcfg["observe_only"]),
            "face": None, "template": None, "gemini": None,
        }
    try:
        frames = extract_gate_frames(
            clip_path, out_dir,
            every_s=float(vcfg["face_sample_every_s"]),
            max_frames=_GATE_MAX_FRAMES)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        return {
            "decision": "rechazado",
            "pasa": False,
            "plantilla": template_name,
            "hard_pasa": False,
            "needs_rerender": True,
            "rerender_reason": ("sin fotogramas para la puerta "
                                f"dura: {exc}"),
            "hard_failures": ["sin_fotogramas"],
            "revision": "no_disponible",
            "scores": {},
            "motivos": {},
            "fallos": ["sin_fotogramas"],
            "motivo": f"sin fotogramas para la puerta dura: {exc}",
            "resumen": f"sin fotogramas para la puerta dura: {exc}",
            "frames_n": 0,
            "model": model or MODEL_DEFAULT,
            "transcript_words": len((transcript_text or "").split()),
            "observe_only": bool(vcfg["observe_only"]),
            "face": None, "template": None, "gemini": None,
        }
    face = face_gate(clip_path, vcfg, recipe, frames=frames,
                     out_dir=out_dir, facecrop_fn=facecrop_fn,
                     edge_fn=edge_fn, yunet_fn=yunet_fn)
    template = template_gate(clip_path, vcfg, recipe, frames=frames,
                             srt_path=srt_path, face_result=face,
                             out_dir=out_dir, sfx_events=sfx_events)
    # Gemini ANTES de la puerta dura: su lectura del título es la
    # única que distingue un título amarillo sobre negro (170, bueno
    # a mano) de un clip sin título — en píxeles son iguales (5
    # métricas probadas, ninguna separa). Si la plantilla falla
    # SOLO por titulo_banner y el juez lee el título, no es fallo
    # duro: se registra en `overridden` y la puerta sigue.
    gemini = review_final_clip(
        clip_path, title, transcript_text=transcript_text,
        out_dir=out_dir, gemini_fn=gemini_fn, model=model,
        min_score=float(vcfg["min_score"]),
        template_name=template_name)
    overridden = []
    hard_failures = []
    if not face.get("pasa"):
        hard_failures.append("cara")
    if not template.get("pasa"):
        template_fallos = template.get("fallos") or []
        titulo_legible = (gemini.get("scores") or {}).get(
            "titulo_legible", 0.0)
        if (template_fallos == ["titulo_banner"]
                and titulo_legible >= float(vcfg["min_score"])):
            overridden.append(
                "titulo_banner: píxeles sin banner pero el juez "
                f"lee el título ({titulo_legible:.1f})")
        else:
            hard_failures.append("plantilla")
    hard_pasa = not hard_failures
    gemini_fallos = list(gemini.get("fallos") or [])
    if gemini.get("decision") == "rechazado":
        # La medida determinista manda sobre el juez en cara y
        # plantilla: si Gemini dice que no hay cara pero la cadena
        # la verificó en todo el clip, es el juez quien se equivoca
        # (la 01:08: webcam 0.0 en 3 clips buenos). La discrepancia
        # queda registrada, nunca en silencio.
        if "webcam_visible" in gemini_fallos and face.get("pasa"):
            gemini_fallos.remove("webcam_visible")
            overridden.append(
                f"webcam_visible: Gemini "
                f"{gemini['scores'].get('webcam_visible'):.1f} pero "
                f"cara verificada ({face.get('method')}, "
                f"{face.get('share', 0):.0%} de muestras)")
        if ("plantilla" in gemini_fallos
                and template.get("pasa")
                and (template.get("checks") or {}).get(
                    "fuente_ariblk", {}).get("pasa")):
            gemini_fallos.remove("plantilla")
            overridden.append(
                "plantilla: Gemini "
                f"{gemini['scores'].get('plantilla'):.1f} "
                "pero montaje y fuente verificados en píxeles")
    if gemini.get("decision") == "rechazado" and not gemini_fallos:
        gemini_decision = "observado"
    elif gemini.get("decision") == "rechazado":
        if vcfg["observe_only"] and not vcfg["gemini_hard"]:
            gemini_decision = "observado"
        else:
            gemini_decision = "rechazado"
    else:
        gemini_decision = gemini.get("decision")
    if not hard_pasa:
        decision = "rechazado"
        pasa = False
        needs_rerender = True
        rerender_reason = "; ".join(
            m for m in (face.get("motivo") or "",
                        template.get("motivo") or "") if m)
        if not rerender_reason:
            rerender_reason = "puerta dura: " + ", ".join(hard_failures)
    elif gemini_decision == "rechazado":
        decision = "rechazado"
        pasa = False
        needs_rerender = True
        rerender_reason = ("Gemini: " + ", ".join(
            f"{CRITERIA_ES[c]} ({gemini['scores'].get(c, 0.0):.1f})"
            for c in gemini_fallos))
    elif gemini_decision == "pendiente_revision":
        decision = "pendiente_revision"
        pasa = False
        needs_rerender = False
        rerender_reason = ""
    else:
        decision = gemini_decision
        pasa = True
        needs_rerender = False
        rerender_reason = ""
    motivo = ""
    if decision == "rechazado":
        motivo = rerender_reason
    elif decision == "observado":
        detalle = ", ".join(
            f"{CRITERIA_ES[c]} ({gemini['scores'].get(c, 0.0):.1f})"
            for c in gemini_fallos) or gemini.get("motivo") or ""
        motivo = "observación (no bloquea el encolado): " + detalle
    elif decision == "pendiente_revision":
        motivo = gemini.get("motivo") or ""
    scores = dict(gemini.get("scores") or {})
    if overridden:
        scores["_determinista_manda"] = "; ".join(overridden)
    return {
        "decision": decision,
        "pasa": pasa,
        "plantilla": template_name,
        "hard_pasa": hard_pasa,
        "needs_rerender": needs_rerender,
        "rerender_reason": rerender_reason,
        "hard_failures": hard_failures,
        "revision": gemini.get("revision"),
        "scores": scores,
        "motivos": dict(gemini.get("motivos") or {}),
        "fallos": gemini_fallos,
        "motivo": motivo,
        "resumen": gemini.get("resumen") or motivo,
        "frames_n": len(frames),
        "model": gemini.get("model") or MODEL_DEFAULT,
        "transcript_words": len((transcript_text or "").split()),
        "observe_only": bool(vcfg["observe_only"]),
        "overridden": overridden,
        "face": face,
        "template": template,
        "gemini": gemini,
    }
