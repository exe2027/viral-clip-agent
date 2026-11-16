#!/usr/bin/env python3
"""
counter.py — contador "funciona" + fases de auto-publicación + circuit breaker.

Bloque 10 del tribunal (2026-11-16). Documentación completa en
``AUTOPUBLISH.md``. Resumen:

* **Contador "funciona"**: clips seguidos aprobados SIN editar
  título/caption Y publicados sin fallo técnico. Se reinicia a cero
  con un rojo, un rechazo por contenido o un fallo de subida.
  Estado persistente en disco (JSON) en ``state/counter.json``.
* **4 fases de auto-publicación**, con cambio MANUAL, nunca
  automático: 1 manual → 2 semi-auto → 3 auto con ventana de veto →
  4 auto total. Cada fase mínimo 3-5 días y 10 clips.
* **Circuit breaker**: auto-pausa si algo va mal; reanudación manual.
* **Kill-switch**: archivo ``queue.kill`` en la raíz del repo,
  compartido con la cola (bloque 1). Si existe, nada se publica.

NO publica nada por sí mismo: solo registra eventos y decide si la
cola PUEDE publicar. La cola (``queue.py``) es la que publica, y debe
consultar a este módulo antes de cada publicación (ver
``gate_publish()`` y el subcomando ``gate``).

Interfaz con la cola (``queue.py``, bloque 1 — ya existe):
  estados: queued → processing → ready_for_review → approved →
  published / failed (además: rejected, dead). El semáforo de
  queue.py marca en rojo los jobs con juego ≠ GTA 6,
  VOD anterior al 19 nov 2026, música con copyright sospechada, o
  manifiesto/layout/subtítulos incompletos.
  * tras la revisión humana:  ``record approved_clean`` /
    ``approved_edited`` / ``rejected_content`` (por clip).
  * tras intentar publicar:  ``record published_ok`` /
    ``record published_fail --platform <plataforma>``.
  * antes de publicar:       ``gate_publish()`` o ``counter.py gate``;
    si dice DENY o PROPOSE, no se publica.
  * antes de cada lote:      si existe ``queue.kill``, parar.
  La lista blanca canónica es influencers.yaml (solo "concedido") +
  allowed_streamers.yaml, leída por ``publisher.load_whitelist()``
  (bloque 7); queue.py usa además ``allowlist`` en config.yaml.
  ``phase4_ready()`` consulta esas fuentes en ese orden.
  Publicar es trabajo de ``publisher.py`` (bloque 7): este módulo
  solo decide si se PUEDE publicar.

Uso (CLI):
    counter.py status                          # estado actual
    counter.py record approved_clean --clip c1
    counter.py record published_ok --clip c1
    counter.py record published_fail --clip c1 --platform youtube
    counter.py record rejected_content --clip c1
    counter.py gate --platform youtube --clip c1
    counter.py phase                           # fase actual
    counter.py set-phase 2 --yes
    counter.py kill [--reason ...]             # crea queue.kill
    counter.py resume --yes                    # quita queue.kill + limpia breaker
    counter.py breaker-reset --yes
    counter.py simulate                        # simulación en estado temporal

Eventos que acepta ``record``:
    approved_clean    clip aprobado sin editar título/caption
    approved_edited   clip aprobado CON edición (reinicia el contador)
    published_ok      clip publicado sin fallo técnico
    published_fail    fallo de subida (reinicia el contador; 2 seguidos
                      en una plataforma = circuit breaker)
    rejected_content  rechazado por contenido (reinicia el contador)
    red_flag          un rojo que intentó colarse (breaker inmediato)
    copyright         aviso de copyright (breaker inmediato)
    strike            strike (breaker inmediato)
    lang_low_conf     confianza de idioma < 0,5 (2 seguidos = breaker)
    below_median      clip muy por debajo de la mediana (3 seguidos = breaker)

Umbrales (a validar por el usuario, configurables en config.yaml,
sección ``autopublish``): 15 seguidos sin edición y sin fallos,
0 rechazados por contenido en los últimos 15.
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

try:
    from config import load_config, get_autopublish_config
except ImportError:  # ejecutado desde otro directorio sin config.py
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from config import load_config, get_autopublish_config

# ---------------------------------------------------------------------------
# Rutas y constantes
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STATE_PATH = os.path.join(REPO_ROOT, "state", "counter.json")
KILL_SWITCH_PATH = os.path.join(REPO_ROOT, "queue.kill")

STATE_VERSION = 1
RECENT_KEPT = 30          # decisiones recientes guardadas (el umbral mira las últimas 15)
HISTORY_KEPT = 200        # eventos guardados en el log de auditoría

PHASES = {
    1: "manual",
    2: "semi-auto",
    3: "auto con ventana de veto",
    4: "auto total",
}

# Eventos que cuentan como "decisión sobre un clip" para los últimos 15.
DECISION_EVENTS = {
    "approved_clean", "approved_edited", "rejected_content",
    "published_ok", "published_fail",
}

# Eventos que reinician el contador a cero.
STREAK_RESET_EVENTS = {
    "approved_edited", "rejected_content", "published_fail",
    "red_flag", "copyright", "strike",
}


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------

def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_state():
    return {
        "version": STATE_VERSION,
        "streak": 0,
        "total_approved_clean": 0,
        "total_published_ok": 0,
        "total_rejected_content": 0,
        "pending_clean": {},      # clip_id -> {"at": iso} aprobado limpio, pendiente de publicar
        "recent": [],             # últimas decisiones {event, clip, at} (máx. RECENT_KEPT)
        "history": [],            # log de auditoría {event, clip, platform, detail, at}
        "fail_streak_by_platform": {},  # plataforma -> fallos de subida seguidos
        "lang_low_conf_streak": 0,
        "below_median_streak": 0,
        "circuit_breaker": {
            "tripped": False,
            "reason": None,
            "platform": None,
            "tripped_at": None,
        },
        "phase": {
            "current": 1,
            "entered_at": _now_iso(),
            "clips_in_phase": 0,
            "history": [{"phase": 1, "at": _now_iso(), "note": "fase inicial"}],
        },
    }


def load_state(path=None):
    """Carga el estado desde disco; si no existe, devuelve uno vacío."""
    path = path or DEFAULT_STATE_PATH
    if not os.path.isfile(path):
        return default_state()
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    # Migración suave: rellenar claves que falten en estados viejos.
    base = default_state()
    base.update(state)
    for key in ("circuit_breaker", "phase"):
        merged = dict(default_state()[key])
        merged.update(state.get(key) or {})
        base[key] = merged
    return base


def save_state(state, path=None):
    """Guarda el estado en disco (escritura atómica)."""
    path = path or DEFAULT_STATE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# Kill-switch (compartido con la cola, bloque 1)
# ---------------------------------------------------------------------------

def kill_switch_active(path=None):
    """True si existe el flag queue.kill (nada debe publicarse)."""
    return os.path.isfile(path or KILL_SWITCH_PATH)


def set_kill_switch(reason=None, path=None):
    path = path or KILL_SWITCH_PATH
    with open(path, "w", encoding="utf-8") as f:
        f.write(_now_iso())
        if reason:
            f.write(" " + reason)
        f.write("\n")
    return path


def clear_kill_switch(path=None):
    path = path or KILL_SWITCH_PATH
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

def trip_breaker(state, reason, platform=None):
    """Dispara el circuit breaker. Reanudación solo manual."""
    state["circuit_breaker"] = {
        "tripped": True,
        "reason": reason,
        "platform": platform,
        "tripped_at": _now_iso(),
    }
    return state["circuit_breaker"]


def reset_breaker(state):
    state["circuit_breaker"] = {
        "tripped": False,
        "reason": None,
        "platform": None,
        "tripped_at": None,
    }
    return state["circuit_breaker"]


def breaker_tripped(state):
    return bool((state.get("circuit_breaker") or {}).get("tripped"))


# ---------------------------------------------------------------------------
# Contador
# ---------------------------------------------------------------------------

def content_rejections_last_n(state, n=15):
    """Rechazos por contenido entre las últimas n decisiones."""
    recent = (state.get("recent") or [])[-n:]
    return sum(1 for e in recent if e.get("event") == "rejected_content")


def record_event(state, event, clip=None, platform=None, detail=None):
    """Registra un evento y actualiza contador, rachas y breaker.

    Devuelve la lista de motivos por los que se disparó el breaker
    en este evento (vacía si no se disparó nada nuevo).
    """
    now = _now_iso()
    trips = []

    # Log de auditoría (acotado).
    state.setdefault("history", []).append({
        "event": event, "clip": clip, "platform": platform,
        "detail": detail, "at": now,
    })
    state["history"] = state["history"][-HISTORY_KEPT:]

    # Decisiones recientes (acotadas).
    if event in DECISION_EVENTS:
        state.setdefault("recent", []).append(
            {"event": event, "clip": clip, "at": now})
        state["recent"] = state["recent"][-RECENT_KEPT:]

    if event == "approved_clean":
        state["total_approved_clean"] = state.get("total_approved_clean", 0) + 1
        if clip:
            state.setdefault("pending_clean", {})[clip] = {"at": now}
        # Un clip que pasa limpio corta las rachas de problemas.
        state["lang_low_conf_streak"] = 0
        state["below_median_streak"] = 0

    elif event == "approved_edited":
        state["streak"] = 0
        if clip:
            state.get("pending_clean", {}).pop(clip, None)

    elif event == "published_ok":
        state["total_published_ok"] = state.get("total_published_ok", 0) + 1
        state["phase"]["clips_in_phase"] = state["phase"].get("clips_in_phase", 0) + 1
        if platform:
            state.setdefault("fail_streak_by_platform", {})[platform] = 0
        state["lang_low_conf_streak"] = 0
        state["below_median_streak"] = 0
        # Solo suma al contador si el clip se aprobó limpio: la racha
        # mide el pipeline completo (revisión sin editar + subida sin fallo).
        if clip and clip in state.get("pending_clean", {}):
            state["streak"] = state.get("streak", 0) + 1
            del state["pending_clean"][clip]

    elif event == "published_fail":
        state["streak"] = 0
        if clip:
            state.get("pending_clean", {}).pop(clip, None)
        if platform:
            fails = state.setdefault("fail_streak_by_platform", {})
            fails[platform] = fails.get(platform, 0) + 1
            if fails[platform] >= 2 and not breaker_tripped(state):
                trips.append(
                    "2 fallos de subida seguidos en %s" % platform)
                trip_breaker(state, trips[-1], platform=platform)

    elif event == "rejected_content":
        state["streak"] = 0
        state["total_rejected_content"] = state.get("total_rejected_content", 0) + 1
        if clip:
            state.get("pending_clean", {}).pop(clip, None)

    elif event == "red_flag":
        state["streak"] = 0
        if not breaker_tripped(state):
            trips.append("un rojo intentó colarse (clip %s)" % (clip or "?"))
            trip_breaker(state, trips[-1])

    elif event == "copyright":
        state["streak"] = 0
        if not breaker_tripped(state):
            trips.append("aviso de copyright (clip %s)" % (clip or "?"))
            trip_breaker(state, trips[-1])

    elif event == "strike":
        state["streak"] = 0
        if not breaker_tripped(state):
            trips.append("strike (clip %s)" % (clip or "?"))
            trip_breaker(state, trips[-1])

    elif event == "lang_low_conf":
        state["lang_low_conf_streak"] = state.get("lang_low_conf_streak", 0) + 1
        if state["lang_low_conf_streak"] >= 2 and not breaker_tripped(state):
            trips.append("idioma conf. < 0,5 en 2 clips seguidos")
            trip_breaker(state, trips[-1])

    elif event == "below_median":
        state["below_median_streak"] = state.get("below_median_streak", 0) + 1
        if state["below_median_streak"] >= 3 and not breaker_tripped(state):
            trips.append("3 clips seguidos muy por debajo de la mediana")
            trip_breaker(state, trips[-1])

    elif event in STREAK_RESET_EVENTS:
        state["streak"] = 0

    return trips


# ---------------------------------------------------------------------------
# Umbral "funciona" y fases
# ---------------------------------------------------------------------------

def meets_threshold(state, cfg=None, kill_path=None):
    """¿El contador cumple el umbral para confiar en la automatización?

    Umbral (config.yaml → autopublish.threshold, default 15):
    streak >= umbral Y 0 rechazados por contenido en los últimos 15.
    Además el breaker no puede estar disparado ni el kill-switch activo.

    Devuelve (ok: bool, motivos: list[str]).
    """
    cfg = cfg or get_autopublish_config(load_config())
    threshold = int(cfg.get("threshold", 15))
    reasons = []
    if state.get("streak", 0) < threshold:
        reasons.append("racha %d < umbral %d" % (state.get("streak", 0), threshold))
    rejected = content_rejections_last_n(state, 15)
    if rejected:
        reasons.append("%d rechazo(s) por contenido en los últimos 15" % rejected)
    if breaker_tripped(state):
        reasons.append("circuit breaker disparado: %s"
                       % (state["circuit_breaker"].get("reason") or "?"))
    if kill_switch_active(kill_path):
        reasons.append("kill-switch activo (existe queue.kill)")
    return (not reasons), reasons


def current_phase(state):
    """Fase actual de auto-publicación (1-4)."""
    return int((state.get("phase") or {}).get("current", 1))


def phase_age_days(state, now=None):
    entered = (state.get("phase") or {}).get("entered_at")
    if not entered:
        return 0.0
    start = datetime.fromisoformat(entered)
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - start).total_seconds() / 86400.0)


def phase_can_advance(state, cfg=None):
    """¿La fase actual cumple los mínimos para poder avanzar?

    Mínimo 3-5 días y 10 clips por fase (config.yaml).
    Devuelve (ok: bool, motivos: list[str]).
    """
    cfg = cfg or get_autopublish_config(load_config())
    min_days = int(cfg.get("min_days_per_phase", 3))
    min_clips = int(cfg.get("min_clips_per_phase", 10))
    reasons = []
    age = phase_age_days(state)
    if age < min_days:
        reasons.append("fase lleva %.1f días, mínimo %d" % (age, min_days))
    clips = int((state.get("phase") or {}).get("clips_in_phase", 0))
    if clips < min_clips:
        reasons.append("%d clips en la fase, mínimo %d" % (clips, min_clips))
    return (not reasons), reasons


def whitelist_nonempty(cfg=None):
    """¿Hay al menos un streamer en la lista blanca?

    Fuente canónica: ``publisher.load_whitelist()`` (bloque 7), que
    lee influencers.yaml + allowed_streamers.yaml. Si publisher.py
    no se puede importar, se cae a ``queue.load_allowlist()``
    (bloque 1, ``allowlist`` en config.yaml) y por último a las
    claves de config. Vacío en todas las fuentes = lista vacía =
    fase 4 bloqueada. (2026-11-16: estar en la lista = permiso
    ya dado, no hay ficha de permiso.)
    """
    try:
        from publisher import load_whitelist
        if load_whitelist():
            return True
    except Exception:
        pass
    try:
        from queue import load_allowlist
        if load_allowlist():
            return True
    except Exception:
        pass
    cfg = cfg or get_autopublish_config(load_config())
    # La allowlist de nivel superior vive en el config COMPLETO, no en
    # la sección autopublish: recargarlo para el fallback.
    try:
        full = load_config()
    except Exception:
        full = {}
    if (full or {}).get("allowlist"):
        return True
    ap = (full or {}).get("autopublish") or {}
    return bool(ap.get("whitelist"))


def phase4_ready(state, cfg=None):
    """¿Se cumplen las condiciones extra de la fase 4 (auto total)?

    Semáforo verde (umbral cumplido) + lista blanca no vacía +
    métricas en verde (declaración manual en config.yaml).
    Devuelve (ok: bool, motivos: list[str]).
    """
    cfg = cfg or get_autopublish_config(load_config())
    ok, reasons = meets_threshold(state, cfg)
    reasons = list(reasons)
    if not whitelist_nonempty(cfg):
        reasons.append("lista blanca vacía (sin streamers en la lista: "
                       "influencers.yaml / allowed_streamers.yaml)")
    if not cfg.get("metrics_green", False):
        reasons.append("métricas no marcadas en verde (autopublish.metrics_green)")
    return (not reasons), reasons


def set_phase(state, target, cfg=None, force=False, note=None):
    """Cambio de fase MANUAL (nunca automático).

    Reglas:
    * target 1-4. Retroceder (a una fase menor) siempre se permite:
      es el camino seguro.
    * Avanzar debe ser secuencial (n → n+1) salvo --force.
    * Avanzar exige los mínimos de la fase (días + clips) salvo --force.
    * Avanzar a la fase 4 exige además phase4_ready() salvo --force.
    La confirmación la pide el CLI, no esta función.
    Devuelve (ok: bool, mensaje: str).
    """
    cfg = cfg or get_autopublish_config(load_config())
    if target not in PHASES:
        return False, "fase %s no existe (1-4)" % target
    current = current_phase(state)
    if target == current:
        return False, "ya estás en la fase %d (%s)" % (current, PHASES[current])
    if target > current:
        if target != current + 1 and not force:
            return False, ("no se puede saltar de la fase %d a la %d sin --force; "
                           "avanza de una en una" % (current, target))
        ok, reasons = phase_can_advance(state, cfg)
        if not ok and not force:
            return False, "la fase %d aún no cumple los mínimos: %s" % (
                current, "; ".join(reasons))
        if target == 4:
            ok4, reasons4 = phase4_ready(state, cfg)
            if not ok4 and not force:
                return False, "la fase 4 exige: %s" % "; ".join(reasons4)
    state["phase"]["current"] = target
    state["phase"]["entered_at"] = _now_iso()
    state["phase"]["clips_in_phase"] = 0
    state.setdefault("phase", {}).setdefault("history", []).append({
        "phase": target, "at": _now_iso(),
        "note": (note or "") + (" (forzado)" if force else ""),
    })
    return True, "fase %d → %d (%s)" % (current, target, PHASES[target])


# ---------------------------------------------------------------------------
# Puerta de publicación (lo que consulta la cola antes de publicar)
# ---------------------------------------------------------------------------

def gate_publish(state, platform, clip=None, cfg=None, kill_path=None):
    """Decide si la cola puede publicar este clip ahora.

    Devuelve un dict:
      {"allow": bool, "mode": str, "reason": str, "veto_minutes": int|None}
    Modos:
      * fase 1 "manual":    nunca publica solo; allow=False.
      * fase 2 "propose":   el lote se propone; si no hay respuesta
                            humana, NO se publica; allow=False.
      * fase 3 "veto":      se programa la publicación tras la ventana
                            de veto (30 min YT / 2 h resto, configurable);
                            allow=True solo si ya pasó la ventana (la cola
                            debe re-consultar tras la ventana; aquí se
                            devuelve la ventana a esperar).
      * fase 4 "auto":      publica ya, solo con semáforo verde +
                            lista blanca + métricas en verde.
    DENY siempre si: kill-switch activo, breaker disparado, o (fase 4)
    sin cumplir phase4_ready().
    """
    cfg = cfg or get_autopublish_config(load_config())
    phase = current_phase(state)
    veto = (cfg.get("veto_window_minutes") or {})
    veto_minutes = int(veto.get(platform, veto.get("default", 120)))

    if kill_switch_active(kill_path):
        return {"allow": False, "mode": "deny", "reason":
                "kill-switch activo (existe queue.kill)", "veto_minutes": None}
    if breaker_tripped(state):
        return {"allow": False, "mode": "deny", "reason":
                "circuit breaker disparado: %s"
                % (state["circuit_breaker"].get("reason") or "?"),
                "veto_minutes": None}

    if phase == 1:
        return {"allow": False, "mode": "manual", "reason":
                "fase 1: publicación manual, nada automático",
                "veto_minutes": None}
    if phase == 2:
        return {"allow": False, "mode": "propose", "reason":
                "fase 2: el lote se propone; sin respuesta humana no se publica",
                "veto_minutes": None}
    if phase == 3:
        return {"allow": True, "mode": "veto", "reason":
                "fase 3: publicar tras la ventana de veto de %d min en %s "
                "(si nadie veta)" % (veto_minutes, platform),
                "veto_minutes": veto_minutes}
    # Fase 4.
    ok, reasons = phase4_ready(state, cfg)
    if not ok:
        return {"allow": False, "mode": "deny", "reason":
                "fase 4 sin semáforo verde: %s" % "; ".join(reasons),
                "veto_minutes": None}
    return {"allow": True, "mode": "auto", "reason":
            "fase 4: semáforo verde, lista blanca y métricas en verde",
            "veto_minutes": None}


# ---------------------------------------------------------------------------
# Presentación
# ---------------------------------------------------------------------------

def status_text(state, cfg=None, kill_path=None):
    cfg = cfg or get_autopublish_config(load_config())
    threshold = int(cfg.get("threshold", 15))
    ok, reasons = meets_threshold(state, cfg, kill_path)
    phase = current_phase(state)
    lines = [
        "Contador «funciona» — estado",
        "  racha (aprobados sin editar + publicados sin fallo): %d / %d"
        % (state.get("streak", 0), threshold),
        "  rechazados por contenido en los últimos 15: %d"
        % content_rejections_last_n(state, 15),
        "  totales: %d aprobados limpios, %d publicados ok, %d rechazados"
        % (state.get("total_approved_clean", 0),
           state.get("total_published_ok", 0),
           state.get("total_rejected_content", 0)),
        "  aprobados limpios pendientes de publicar: %d"
        % len(state.get("pending_clean") or {}),
        "  umbral «funciona»: %s%s"
        % ("VERDE" if ok else "ROJO",
           "" if ok else " — " + "; ".join(reasons)),
        "  fase actual: %d (%s), %.1f días en la fase, %d clips en la fase"
        % (phase, PHASES[phase], phase_age_days(state),
           int((state.get("phase") or {}).get("clips_in_phase", 0))),
        "  circuit breaker: %s"
        % ("DISPARADO — " + str((state.get("circuit_breaker") or {}).get("reason"))
           if breaker_tripped(state) else "ok"),
        "  kill-switch (queue.kill): %s"
        % ("ACTIVO — nada se publica" if kill_switch_active(kill_path) else "inactivo"),
        "  rachas de problemas: fallos subida %s, idioma<0,5 seguidos: %d, "
        "bajo mediana seguidos: %d"
        % (state.get("fail_streak_by_platform") or {},
           state.get("lang_low_conf_streak", 0),
           state.get("below_median_streak", 0)),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Simulación
# ---------------------------------------------------------------------------

def run_simulation():
    """Simula: 10 aprobados sin editar + publicados ok, luego 1 rechazo.

    Usa un estado temporal (no toca state/counter.json). Después
    demuestra el circuit breaker con 2 fallos de subida seguidos.
    Devuelve el texto a mostrar.
    """
    out = ["SIMULACIÓN (estado temporal, no toca state/counter.json)", ""]
    with tempfile.TemporaryDirectory(prefix="counter-sim-") as tmp:
        sim_path = os.path.join(tmp, "counter.json")
        state = load_state(sim_path)
        cfg = get_autopublish_config(load_config())

        out.append("— 10 clips aprobados sin editar y publicados sin fallo —")
        for i in range(1, 11):
            clip = "sim-clip-%02d" % i
            record_event(state, "approved_clean", clip=clip)
            record_event(state, "published_ok", clip=clip, platform="youtube")
        save_state(state, sim_path)
        out.append(status_text(state, cfg))
        ok, _ = meets_threshold(state, cfg)
        out.append("  umbral (15): %s (racha 10 < 15, faltan 5)"
                   % ("VERDE" if ok else "ROJO"))
        out.append("")

        out.append("— 1 rechazo por contenido (clip sim-clip-11) —")
        record_event(state, "rejected_content", clip="sim-clip-11")
        save_state(state, sim_path)
        out.append(status_text(state, cfg))
        out.append("  → el contador vuelve a CERO (racha 10 → 0).")
        out.append("")

        out.append("— extra: 2 fallos de subida seguidos en YouTube —")
        record_event(state, "published_fail", clip="sim-clip-12", platform="youtube")
        trips = record_event(state, "published_fail", clip="sim-clip-13", platform="youtube")
        save_state(state, sim_path)
        out.append("  breaker disparado: %s" % "; ".join(trips))
        out.append(status_text(state, cfg))
        out.append("")
        out.append("Reanudación: solo manual → `counter.py resume --yes` "
                   "(limpia el breaker y quita queue.kill si existe).")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

EVENTS = [
    "approved_clean", "approved_edited", "published_ok", "published_fail",
    "rejected_content", "red_flag", "copyright", "strike",
    "lang_low_conf", "below_median",
]


def _confirm(prompt):
    answer = input(prompt + " [s/N] ").strip().lower()
    return answer in ("s", "si", "sí", "y", "yes")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Contador «funciona» + fases de auto-publicación + circuit breaker.")
    parser.add_argument("--state", default=None,
                        help="ruta del JSON de estado (default: state/counter.json)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="muestra el estado actual")
    sub.add_parser("phase", help="muestra la fase actual")

    p_record = sub.add_parser("record", help="registra un evento")
    p_record.add_argument("event", choices=EVENTS)
    p_record.add_argument("--clip", default=None)
    p_record.add_argument("--platform", default=None,
                          help="youtube | tiktok | instagram | facebook")
    p_record.add_argument("--detail", default=None)

    p_gate = sub.add_parser("gate", help="¿puede la cola publicar este clip?")
    p_gate.add_argument("--platform", required=True)
    p_gate.add_argument("--clip", default=None)

    p_set = sub.add_parser("set-phase", help="cambio MANUAL de fase (con confirmación)")
    p_set.add_argument("phase", type=int, choices=[1, 2, 3, 4])
    p_set.add_argument("--yes", action="store_true", help="confirmar sin preguntar")
    p_set.add_argument("--force", action="store_true",
                       help="saltarse mínimos / orden secuencial / requisitos fase 4")
    p_set.add_argument("--reason", default=None)

    p_kill = sub.add_parser("kill", help="activa el kill-switch (crea queue.kill)")
    p_kill.add_argument("--reason", default=None)

    p_resume = sub.add_parser("resume", help="reanudación MANUAL (breaker + kill-switch)")
    p_resume.add_argument("--yes", action="store_true", help="confirmar sin preguntar")

    p_br = sub.add_parser("breaker-reset", help="limpia solo el circuit breaker")
    p_br.add_argument("--yes", action="store_true", help="confirmar sin preguntar")

    sub.add_parser("simulate", help="simulación en estado temporal")

    args = parser.parse_args(argv)
    state_path = args.state or DEFAULT_STATE_PATH
    cfg = get_autopublish_config(load_config())

    if args.cmd == "simulate":
        print(run_simulation())
        return 0

    state = load_state(state_path)

    if args.cmd == "status":
        print(status_text(state, cfg))
        return 0

    if args.cmd == "phase":
        phase = current_phase(state)
        print("fase %d (%s) — %.1f días en la fase, %d clips en la fase"
              % (phase, PHASES[phase], phase_age_days(state),
                 int((state.get("phase") or {}).get("clips_in_phase", 0))))
        return 0

    if args.cmd == "record":
        trips = record_event(state, args.event, clip=args.clip,
                             platform=args.platform, detail=args.detail)
        save_state(state, state_path)
        print("registrado: %s%s%s — racha: %d"
              % (args.event,
                 (" clip=%s" % args.clip) if args.clip else "",
                 (" plataforma=%s" % args.platform) if args.platform else "",
                 state.get("streak", 0)))
        for trip in trips:
            print("⚠️  CIRCUIT BREAKER DISPARADO: %s" % trip)
            print("   Reanudación solo manual: counter.py resume --yes")
        return 0

    if args.cmd == "gate":
        decision = gate_publish(state, args.platform, clip=args.clip, cfg=cfg)
        print("%s — modo %s: %s"
              % ("PERMITIDO" if decision["allow"] else "DENEGADO",
                 decision["mode"], decision["reason"]))
        if decision.get("veto_minutes") is not None:
            print("ventana de veto: %d min" % decision["veto_minutes"])
        return 0 if decision["allow"] else 1

    if args.cmd == "set-phase":
        ok, message = set_phase(state, args.phase, cfg=cfg,
                                force=args.force, note=args.reason)
        if not ok:
            print("NO se cambia la fase: %s" % message)
            return 1
        print("Cambio propuesto: %s" % message)
        if not args.yes and not _confirm("¿Confirmas el cambio de fase?"):
            print("Cancelado: la fase sigue en %d." % current_phase(state))
            return 1
        save_state(state, state_path)
        print("Fase actual: %d (%s). Este cambio es manual y queda en el historial."
              % (current_phase(state), PHASES[current_phase(state)]))
        return 0

    if args.cmd == "kill":
        set_kill_switch(args.reason)
        print("Kill-switch ACTIVO (creado queue.kill). Nada se publica hasta `resume`.")
        return 0

    if args.cmd == "resume":
        if not args.yes and not _confirm(
                "¿Reanudar? Esto limpia el circuit breaker y quita queue.kill"):
            print("Cancelado: todo sigue parado.")
            return 1
        had_breaker = breaker_tripped(state)
        reset_breaker(state)
        had_kill = clear_kill_switch()
        state["lang_low_conf_streak"] = 0
        state["below_median_streak"] = 0
        state.setdefault("fail_streak_by_platform", {}).clear()
        save_state(state, state_path)
        print("Reanudado manualmente (breaker: %s, kill-switch: %s)."
              % ("limpiado" if had_breaker else "ya estaba limpio",
                 "quitado" if had_kill else "no estaba activo"))
        return 0

    if args.cmd == "breaker-reset":
        if not args.yes and not _confirm("¿Limpiar el circuit breaker?"):
            print("Cancelado: el breaker sigue disparado.")
            return 1
        reset_breaker(state)
        save_state(state, state_path)
        print("Circuit breaker limpio. (El kill-switch, si existe, sigue activo.)")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
