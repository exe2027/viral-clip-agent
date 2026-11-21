"""Puerta de calidad B4 (2026-11-20, "que no vuelva a pasar").

La auditoría de la Cola encontró 121 clips bloqueados defectuosos
(76% de la Cola, sin cara y formato mal). Esta puerta es el lado
cerebro del arreglo: convierte el veredicto de
``validate_final_clip`` (final_review.py: cara determinista +
plantilla en píxeles + juez Gemini sobre el VÍDEO FINAL
renderizado) en 6 criterios obligatorios ANTES de crear un job:

- formato_9_16: el clip es 1080x1920 sin bandas fuera de receta.
- webcam: cara visible en la zona de cámara de la plantilla.
- hook: el momento se entiende sin contexto (el gancho).
- titulo: el título se lee entero donde dice la receta.
- miniatura: hay texto de miniatura para el panel.
- audio: hay voz verificable (transcripción con palabras).

ÚNICA FUENTE en el cerebro de los 6 criterios, el umbral de
alerta del 30% y la muestra mínima: este módulo. El Worker tiene
su propia copia solo para LEER el veredicto (quality-gate.ts);
el significado de cada criterio vive aquí, donde se calcula.

Criterios DUROS (siempre bloquean): formato_9_16, webcam,
miniatura, audio — se miden en píxeles o por presencia, no
dependen del juez. Criterios SUAVES (solo bloquean cuando el
validador NO está en modo observación): hook, titulo — los
puntúa Gemini y el juez sin calibrar tumbó 3 clips buenos en la
01:08; la orden vigente (2026-11-19) es observación por defecto
hasta calibrar con 10-20 clips reales. Un rechazo del validador
nunca se reescribe aquí: si ``final_review`` dice "rechazado",
la puerta dice "rechazado" (el veredicto manda; los criterios
solo explican el porqué).

Decisiones del veredicto (las mismas que final_review.py):
aprobado / rechazado / observado / pendiente_revision /
desactivada. ``pasa`` es True solo si el clip puede encolarse
como publicable; False si no; None si no hay veredicto
(desactivada). Un clip sin veredicto NUNCA se encola como
publicable: va en borrador (draft) para revisión humana.
"""

import json
import os
import sys
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9: sin zona, día en UTC.
    ZoneInfo = None

from .final_review import MIN_SCORE

# Los 6 criterios obligatorios, en el orden en que se reportan.
QUALITY_CRITERIA = (
    "formato_9_16",
    "webcam",
    "hook",
    "titulo",
    "miniatura",
    "audio",
)

QUALITY_CRITERION_LABELS = {
    "formato_9_16": "formato 9:16",
    "webcam": "webcam presente",
    "hook": "hook",
    "titulo": "título",
    "miniatura": "miniatura",
    "audio": "audio",
}

# Criterios DUROS: se miden sin Gemini y bloquean siempre.
QUALITY_HARD_CRITERIA = frozenset(
    ("formato_9_16", "webcam", "miniatura", "audio"))

# Alerta B4: si los descartes por calidad de un día superan este
# % de los clips validados, se avisa (una vez al día, ver
# QualityDiscardTracker). Muestra mínima: con menos clips el %
# no significa nada (1 de 1 sería 100%).
QUALITY_DISCARD_ALERT_PCT = 30.0
QUALITY_ALERT_MIN_SAMPLE = 5

_SANTIAGO = "America/Santiago"


def _santiago_today():
    """Fecha de hoy en America/Santiago (YYYY-MM-DD)."""
    try:
        tz = ZoneInfo(_SANTIAGO) if ZoneInfo is not None else timezone.utc
    except Exception:  # noqa: BLE001 - sin zona, día en UTC
        tz = timezone.utc
    return datetime.now(tz).date().isoformat()


def _score_ok(scores, name):
    """¿El criterio Gemini llega al mínimo (MIN_SCORE, 5)?"""
    value = (scores or {}).get(name)
    return (isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= MIN_SCORE)


def _template_check(template, name):
    """Pasa/falla/None de un chequeo de plantilla (None = no medido)."""
    checks = (template or {}).get("checks") or {}
    entry = checks.get(name)
    if not isinstance(entry, dict) or "pasa" not in entry:
        return None
    return bool(entry.get("pasa"))


def _criterion(name, pasa, duro, motivo=""):
    return {"pasa": bool(pasa), "duro": bool(duro),
            "motivo": motivo if not pasa else ""}


def build_quality_gate(final_review, thumbnail_text=None,
                       no_speech=False):
    """Los 6 criterios B4 a partir del veredicto de final_review.

    ``final_review`` es el dict de ``validate_final_clip`` (o el
    de fallback "pendiente_revision" cuando no se pudo validar).
    ``thumbnail_text`` es el texto de miniatura del paquete;
    ``no_speech`` es el flag de "sin voz verificable" del corte.
    Nunca levanta: sin veredicto usable devuelve
    "pendiente_revision" (no pasa, a revisión humana).
    """
    try:
        return _build(final_review, thumbnail_text, no_speech)
    except Exception as exc:  # noqa: BLE001 - la puerta no tumba
        return {"decision": "pendiente_revision", "pasa": False,
                "criterios": {}, "fallos": [], "fallos_duros": [],
                "motivo": f"puerta de calidad sin ejecutar: {exc}"}


def _build(final_review, thumbnail_text, no_speech):
    review = final_review or {}
    decision_fr = review.get("decision") or "pendiente_revision"
    scores = review.get("scores") or {}
    face = review.get("face") or {}
    template = review.get("template") or {}
    observe_only = review.get("observe_only", True)
    transcript_words = review.get("transcript_words") or 0

    criterios = {}

    # 1. formato 9:16: dimensiones 1080x1920 en píxeles (duro) +
    # sin barras negras según Gemini (suave: el juez puede
    # equivocarse con las bandas de la receta).
    dimensiones = _template_check(template, "dimensiones")
    barras_ok = _score_ok(scores, "sin_barras_negras")
    if dimensiones is False:
        criterios["formato_9_16"] = _criterion(
            "formato_9_16", False, True,
            "el clip no es 1080x1920")
    elif dimensiones is None and not scores:
        criterios["formato_9_16"] = _criterion(
            "formato_9_16", False, False, "formato sin medir")
    elif not barras_ok and scores:
        criterios["formato_9_16"] = _criterion(
            "formato_9_16", False, False,
            "barras negras fuera de la receta")
    else:
        criterios["formato_9_16"] = _criterion(
            "formato_9_16", True, True)

    # 2. webcam: la cadena determinista manda (duro); sin ella,
    # el score de Gemini (suave).
    if face.get("pasa") is True:
        criterios["webcam"] = _criterion("webcam", True, True)
    elif face.get("pasa") is False:
        criterios["webcam"] = _criterion(
            "webcam", False, True,
            face.get("motivo") or "sin cara verificada")
    elif not _score_ok(scores, "webcam_visible"):
        criterios["webcam"] = _criterion(
            "webcam", False, False, "webcam no visible")
    else:
        criterios["webcam"] = _criterion("webcam", True, True)

    # 3. hook: el momento se entiende sin contexto (Gemini, suave).
    criterios["hook"] = _criterion(
        "hook", _score_ok(scores, "momento_comprensible"), False,
        "momento no comprensible")

    # 4. título: el banner/banda de la receta en píxeles (duro,
    # cuando la receta lo mide) + legibilidad según Gemini (suave).
    title_check = None
    for name in ("titulo_banner", "banda_titulo"):
        checked = _template_check(template, name)
        if checked is not None:
            title_check = checked
            break
    if title_check is False:
        criterios["titulo"] = _criterion(
            "titulo", False, True, "sin título en su banda")
    elif not _score_ok(scores, "titulo_legible"):
        criterios["titulo"] = _criterion(
            "titulo", False, False, "título no legible")
    else:
        criterios["titulo"] = _criterion("titulo", True, True)

    # 5. miniatura: hay texto de miniatura (duro, presencia).
    has_thumb = bool(thumbnail_text and str(thumbnail_text).strip())
    criterios["miniatura"] = _criterion(
        "miniatura", has_thumb, True,
        "sin texto de miniatura" if not has_thumb else "")

    # 6. audio: hay voz verificable (duro: palabras transcritas y
    # no marcado sin voz).
    audio_ok = (not no_speech) and transcript_words > 0
    criterios["audio"] = _criterion(
        "audio", audio_ok, True,
        "sin voz verificable" if not audio_ok else "")

    fallos = [c for c in QUALITY_CRITERIA
              if not criterios[c]["pasa"]]
    fallos_duros = [c for c in fallos if criterios[c]["duro"]]
    fallos_suaves = [c for c in fallos if not criterios[c]["duro"]]
    motivo = ""
    if fallos:
        motivo = "calidad: " + ", ".join(
            f"{QUALITY_CRITERION_LABELS[c]}"
            + (f" ({criterios[c]['motivo']})"
               if criterios[c]["motivo"] else "")
            for c in fallos)

    # El veredicto del validador manda: un "rechazado" no se
    # reescribe a "observado" ni a "aprobado" aquí.
    if decision_fr == "rechazado":
        return {"decision": "rechazado", "pasa": False,
                "criterios": criterios, "fallos": fallos,
                "fallos_duros": fallos_duros,
                "motivo": motivo or (review.get("motivo") or "")}
    if decision_fr == "pendiente_revision":
        return {"decision": "pendiente_revision", "pasa": False,
                "criterios": criterios, "fallos": fallos,
                "fallos_duros": fallos_duros,
                "motivo": motivo or (review.get("motivo") or "")}
    if decision_fr == "desactivada":
        return {"decision": "desactivada", "pasa": None,
                "criterios": criterios, "fallos": fallos,
                "fallos_duros": fallos_duros,
                "motivo": review.get("motivo") or ""}
    if fallos_duros:
        return {"decision": "rechazado", "pasa": False,
                "criterios": criterios, "fallos": fallos,
                "fallos_duros": fallos_duros, "motivo": motivo}
    if fallos_suaves:
        if observe_only:
            return {"decision": "observado", "pasa": True,
                    "criterios": criterios, "fallos": fallos,
                    "fallos_duros": fallos_duros,
                    "motivo": "observación (no bloquea): " + motivo}
        return {"decision": "rechazado", "pasa": False,
                "criterios": criterios, "fallos": fallos,
                "fallos_duros": fallos_duros, "motivo": motivo}
    return {"decision": "aprobado", "pasa": True,
            "criterios": criterios, "fallos": [],
            "fallos_duros": [], "motivo": ""}


def publish_mode_for_quality(quality_gate, auto_publish):
    """Modo de encolado del cerebro según la puerta B4.

    "api" solo si el veredicto pasa Y auto_publish está encendido;
    en cualquier otro caso con veredicto, "draft" (borrador para
    revisión humana). Sin veredicto (None, clip legacy de otro
    camino) el comportamiento NO cambia: "api" si auto_publish
    está encendido, "draft" si no. Un "rechazado" no llega aquí
    como "api" nunca: _enqueue_publish lo descarta antes (return []).
    """
    if quality_gate is None:
        return "api" if auto_publish else "draft"
    if quality_gate.get("pasa") is True and auto_publish:
        return "api"
    return "draft"


class QualityDiscardTracker:
    """Contador diario de descartes por calidad + alerta del 30%.

    Estado en un JSON local (``state_path``), un día por fila:
    {"date", "validados", "rechazados", "pendientes",
    "alertado"}. "Descartado" = rechazado o sin veredicto
    (pendiente/desactivada): en ambos casos el clip no es
    publicable automático. Cuando descartados/validados supera
    QUALITY_DISCARD_ALERT_PCT con muestra suficiente, ``record``
    devuelve el mensaje de alerta UNA vez al día (dedupe por la
    fecha en el propio estado). Nunca levanta: si el estado no se
    puede leer o escribir, no se alerta y se sigue cortando.
    """

    def __init__(self, state_path,
                 alert_pct=QUALITY_DISCARD_ALERT_PCT,
                 min_sample=QUALITY_ALERT_MIN_SAMPLE):
        self.state_path = state_path
        self.alert_pct = float(alert_pct)
        self.min_sample = int(min_sample)

    def _load(self, today):
        try:
            if os.path.isfile(self.state_path):
                with open(self.state_path, encoding="utf-8") as f:
                    state = json.load(f) or {}
                if state.get("date") == today:
                    return state
        except Exception:  # noqa: BLE001 - estado corrupto: de cero
            pass
        return {"date": today, "validados": 0, "rechazados": 0,
                "pendientes": 0, "alertado": None}

    def _save(self, state):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)

    def record(self, quality_gate, date=None):
        """Registra un veredicto. Devuelve el mensaje de alerta si
        la tasa del día acaba de superar el umbral, o None."""
        try:
            today = date or _santiago_today()
            state = self._load(today)
            decision = (quality_gate or {}).get("decision")
            state["validados"] = int(state.get("validados") or 0) + 1
            if decision == "rechazado":
                state["rechazados"] = int(
                    state.get("rechazados") or 0) + 1
            elif decision in ("pendiente_revision", "desactivada"):
                state["pendientes"] = int(
                    state.get("pendientes") or 0) + 1
            descartes = (int(state.get("rechazados") or 0)
                         + int(state.get("pendientes") or 0))
            validados = int(state.get("validados") or 0)
            alert = None
            if (validados >= self.min_sample
                    and state.get("alertado") != today
                    and descartes * 100.0 / max(1, validados)
                    > self.alert_pct):
                pct = descartes * 100.0 / max(1, validados)
                alert = (
                    f"Puerta de calidad: {descartes}/{validados} "
                    f"clips descartados hoy ({pct:.0f}%, umbral "
                    f"{self.alert_pct:.0f}%) — "
                    f"{state.get('rechazados')} rechazados, "
                    f"{state.get('pendientes')} sin veredicto. "
                    f"Revisar el render/validador antes de que se "
                    f"seque la Cola.")
                state["alertado"] = today
            self._save(state)
            return alert
        except Exception:  # noqa: BLE001 - la alerta no tumba nada
            print("aviso calidad: no se pudo registrar el descarte",
                  file=sys.stderr)
            return None
