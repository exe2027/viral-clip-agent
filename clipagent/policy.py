"""Aprobación automática por política (Fase 6.3 + 6B.5).

Sin humano por clip: un clip se publica solo si pasa TODOS los
cheques. Con rojos no se publica: queda "retenido" con motivo
visible (en la cola de publicación) o "descartado" (sin vuelta
atrás salvo override del usuario desde el panel).

La política usa los puntajes de la Fase 6 bis SIN tocar sus
fórmulas: publish_status de priority.py decide por viralidad/SEO,
y los umbrales se leen de config.yaml (scoring.seo_gate,
scoring.viral_min) con los valores por defecto 90/50. Si el
usuario los baja, queda registrado con fecha en scoring_changes.log.

Ningún "override" automático: los overrides solo los registra el
usuario (seo_score.register_override, desde el panel).
"""
import os
from datetime import datetime

from .detect import band as _band
from .priority import (
    SEO_GATE,
    VIRAL_MIN_PUBLISH,
    moment_key,
    priority as _priority,
    publish_status,
)

BAND_ORDER = {"descartar": 0, "revisar": 1, "prioritario": 2, "urgente": 3}
SCORING_LOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scoring_changes.log")


def load_scoring_config(cfg=None):
    """(seo_gate, viral_min) desde config.yaml `scoring:`.

    Por defecto 90/50 (los de priority.py). Si el usuario los baja,
    queda registrado con fecha en scoring_changes.log (6B.2): los
    umbrales no se cambian solos ni en silencio.
    """
    if cfg is None:
        from .config import load_config
        cfg = load_config()
    scoring = (cfg or {}).get("scoring") or {}
    seo_gate = scoring.get("seo_gate", SEO_GATE)
    viral_min = scoring.get("viral_min", VIRAL_MIN_PUBLISH)
    try:
        seo_gate = int(seo_gate)
    except (TypeError, ValueError):
        seo_gate = SEO_GATE
    try:
        viral_min = int(viral_min)
    except (TypeError, ValueError):
        viral_min = VIRAL_MIN_PUBLISH
    if seo_gate < SEO_GATE or viral_min < VIRAL_MIN_PUBLISH:
        with open(SCORING_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} umbrales bajados por "
                    f"el usuario: seo_gate={seo_gate} "
                    f"(defecto {SEO_GATE}), viral_min={viral_min} "
                    f"(defecto {VIRAL_MIN_PUBLISH})\n")
    return seo_gate, viral_min


def load_auto_approve_config(cfg=None):
    """Config `auto_approve:` (6.3)."""
    if cfg is None:
        from .config import load_config
        cfg = load_config()
    auto = (cfg or {}).get("auto_approve") or {}
    return {
        "enabled": bool(auto.get("enabled", True)),
        "min_band": auto.get("min_band"),
    }


def _seo_for_platform(clip, platform):
    seo_scores = clip.get("seo_scores") or {}
    entry = seo_scores.get(platform)
    if isinstance(entry, dict):
        return entry.get("score")
    return entry


def _game_allowed(game, cfg=None):
    """El juego está en la lista `games:` de config (6.3).

    Sin juego detectado (None/vacío) no es un rojo: no se sabe el
    juego, no es que esté prohibido. Un juego fuera de la lista sí
    retiene el clip.
    """
    if not game:
        return True, None
    from jobqueue import detect_game, load_games_config
    games, _, _ = load_games_config()
    found = detect_game(str(game), str(game), games)
    if found in (None, "other"):
        return False, f"juego no permitido: {game!r} no está en games:"
    return True, None


def evaluate(clip, platform, queue=None, cfg=None, red_flags=None,
             game=None, vod_id=None, mode="full", counter_state=None,
             seo_override=False, make_variant=None, age_days=0):
    """Decide sobre un clip para una plataforma.

    Devuelve {"decision": "publicar"|"retenido"|"descartado",
    "reason": str, "virality": int|None, "seo": int|None,
    "priority": dict|None, "band": str|None, "checks": [...]}.

    `clip` es la entrada del manifiesto (viral_score, seo_scores,
    audio, peak, start). `make_variant` es el generador de variantes
    del LLM (6B.4): sin él, un SEO 70-89 queda retenido.
    """
    checks = []
    seo_gate, viral_min = load_scoring_config(cfg)
    auto_cfg = load_auto_approve_config(cfg)
    if not auto_cfg["enabled"]:
        return {"decision": "retenido",
                "reason": "auto_approve desactivado en config",
                "virality": None, "seo": None, "priority": None,
                "band": None, "checks": checks}

    vs = (clip.get("viral_score") or {})
    virality = vs.get("total")
    seo = _seo_for_platform(clip, platform)
    band = _band(virality) if virality is not None else None

    def _deny(decision, reason):
        return {"decision": decision, "reason": reason,
                "virality": virality, "seo": seo,
                "priority": None, "band": band, "checks": checks}

    if virality is None:
        return _deny("retenido", "sin score de viralidad en el manifiesto")
    if seo is None:
        return _deny("retenido",
                     f"sin score SEO para {platform} en el manifiesto")

    # 0. Duración (reglas del usuario 2026-11-18): ningún clip por
    # debajo de 40 s, ninguno dentro del rango prohibido 25-35 s
    # (regla de oro: ningún clip de 30 s) y ninguno por encima del
    # tope duro de 60 s. Un clip corto no se publica aunque pase
    # viralidad y SEO: se retiene para re-cortarlo más largo.
    duration = clip.get("duration")
    if duration is not None:
        try:
            from .config import clip_duration_ok
            ok_dur, why_dur = clip_duration_ok(float(duration), cfg)
        except Exception:
            ok_dur, why_dur = True, ""
        checks.append(f"duración: {duration} s")
        if not ok_dur:
            return _deny("retenido", why_dur)

    # 0.5. Categoría GTA VI (regla del usuario 2026-11-18): un clip
    # solo es válido si la categoría en la que streamea el
    # influencer es GTA VI (Grand Theft Auto VI). La categoría es el
    # capítulo del VOD donde cae el pico (clip["category"]), o el
    # juego (clip["game"] / parámetro game). Un momento de Just
    # Chatting u otro juego se descarta; sin categoría verificada
    # no se puede confirmar GTA VI y se retiene.
    from .category import effective_category, is_gta6
    cat = effective_category(clip, game)
    if cat and not is_gta6(cat):
        return _deny("descartado",
                     f"categoría {cat!r}: no es GTA VI")
    if not cat:
        return _deny("retenido",
                     "sin categoría verificada: no se puede confirmar "
                     "que el clip sea GTA VI")

    # 1. Filtros duros de puntaje (6B.2 / 6B.5).
    status, why = publish_status(virality, seo, seo_override=seo_override,
                                 seo_gate=seo_gate, viral_min=viral_min)
    checks.append(f"puntaje: {why}")
    if status == "descartado":
        return _deny("descartado", why)
    if seo < 70:
        # Bloqueo duro de SEO (0-69): sin override posible.
        return _deny("descartado",
                     f"SEO {seo:.0f} < 70: bloqueo duro, sin override")
    improved_pkg = None
    improved_seo = None
    if status == "bloqueado_seo":
        # 6B.4: auto-mejora del SEO antes de descartar (máx. 3
        # reintentos con el LLM). Sin LLM, se retiene con motivo.
        if make_variant is not None:
            from .seo_score import retry_best
            base_pkg = {"title": clip.get("title_es", ""),
                        "description": clip.get("desc_es", ""),
                        "tags": [], "thumbnail_text": None,
                        "hashtags": (clip.get("hashtags_es") or "").split(),
                        "game_verified": bool(game),
                        "visible": clip.get("visible"), "lang": "es"}
            result = retry_best(base_pkg, make_variant, platform)
            best = result["best_result"]
            checks.append(
                f"seo_retry: {len(result['attempts']) - 1} intento(s), "
                f"mejor SEO {best['score']}")
            if best["score"] >= seo_gate:
                checks.append("seo_retry: llegó al gate, se publica")
                # El paquete mejorado vuelve al llamador para
                # escribirlo en el manifiesto (6B.4): si no, la
                # subida usaría el título viejo.
                improved_pkg = result["best_pkg"]
                improved_seo = best["score"]
            else:
                return _deny("retenido",
                             f"SEO {seo:.0f} < {seo_gate} y el reintento "
                             f"llegó solo a {best['score']}")
        else:
            return _deny("retenido", why + " (sin LLM no hay reintento)")

    # 2. Rojos del semáforo: no se publica.
    reds = list(red_flags or [])
    if reds:
        return _deny("retenido", "rojos del semáforo: " + "; ".join(reds))

    # 3. Música/copyright sospechada por el audio.
    audio = clip.get("audio") or {}
    if audio.get("decision") == "sospechar":
        return _deny("retenido",
                     "audio sospechoso (posible spam/música): "
                     + str(audio.get("reason") or ""))

    # 4. Juego permitido.
    ok_game, game_reason = _game_allowed(game, cfg)
    if not ok_game:
        return _deny("retenido", game_reason)

    # 5. Sin duplicados: mismo momento, mismo día, misma plataforma.
    # La clave del momento se guarda en el ítem al encolarlo (el
    # worker la saca del manifiesto); en JSON es una lista.
    if queue is not None and vod_id:
        key = list(moment_key(vod_id, clip.get("start")))
        for item in queue.published_today(platform):
            if item.get("moment_key") == key:
                return _deny(
                    "retenido",
                    "mismo momento ya publicado hoy en esta plataforma")

    # 6. Banda mínima (si está configurada).
    min_band = auto_cfg["min_band"]
    if min_band and BAND_ORDER.get(band, 0) < BAND_ORDER.get(min_band, 0):
        return _deny("retenido",
                     f"banda {band} < mínima {min_band} en auto_approve")

    # 7. Modo gradual (6.7): además pasa por el gate de counter.py.
    if mode == "gradual":
        from counter import gate_publish
        gate = gate_publish(counter_state, platform)
        checks.append(f"gate gradual: {gate['mode']} — {gate['reason']}")
        if not gate["allow"]:
            return _deny("retenido",
                         f"modo gradual: {gate['reason']}")

    multiple = (clip.get("viral") or {}).get("viral_multiple") or 1.0
    # 6B.2: la penalización por edad (clip > 7 días: -10/día) se
    # aplica también a la prioridad que muestra la decisión, igual
    # que en el orden de la cola.
    prio = _priority(virality, seo, multiple, age_days=age_days)
    checks.append(f"prioridad {prio['priority']:.1f}")
    return {"decision": "publicar",
            "reason": f"pasa la política (viralidad {virality:.0f}, "
                      f"SEO {seo:.0f}, prioridad {prio['priority']:.1f})",
            "virality": virality, "seo": seo, "priority": prio,
            "band": band, "checks": checks,
            "improved_pkg": improved_pkg,
            "improved_seo": improved_seo}
