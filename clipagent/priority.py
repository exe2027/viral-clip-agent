"""Prioridad de publicación de la Cola (aprobado por el usuario 2026-11-16).

La Cola NO se sube cronológica ni FIFO: se ordena por puntaje. Los clips
de puntaje bajo quedan atrás, se saltan o se descartan; no frenan a los
buenos ni obligan a publicarlos en orden.

Fórmula POTENTE por plataforma (el SEO cambia por plataforma, así que la
prioridad también). Media geométrica ponderada, todos los puntajes en
0-100, resultado en 0-100:

    Prioridad = viralidad^0,5 x SEO^0,3 x chat_norm^0,2

El SEO actúa como multiplicador: un clip con viralidad alta pero SEO bajo
ya no pasa por encima de uno con viralidad algo menor y SEO alto, porque
el factor bajo lo arrastra. Ejemplo: viralidad 90 / SEO 70 (80,1) queda
por debajo de viralidad 75 / SEO 98 (80,9) con el mismo chat (8x).

- ``chat_norm``: múltiplo pico/mediana pasado a 0-100 con la misma
  escala de ``viral_score._chat_base`` (0-10) x10.
- Filtros duros antes de ordenar (nunca cambian con el aprendizaje):
  viralidad < 45 se descarta; SEO < 80 no se publica salvo override
  registrado; clip con más de 7 días pierde 10 puntos por día de viejo;
  no dos clips del mismo momento el mismo día en la misma plataforma;
  clip con 3 días sin subir por prioridad baja se archiva solo.

APRENDIZAJE (BETA hasta 30 clips):
- Con 30 clips publicados con datos a 7 días en Analíticas (vistas y
  retención por plataforma), ``recalibrate`` ajusta SOLO los exponentes:
  correlaciona cada componente (viralidad, SEO, chat) con las vistas
  reales y mueve cada peso como máximo ±25% por ciclo, y deja registro
  con fecha y motivo. Nunca toca los filtros duros.
- Hasta llegar a 30 clips, los pesos quedan fijos en 0,5 / 0,3 / 0,2 y
  la fórmula se marca BETA.

Ejemplos de referencia (aprobados con el usuario):
- Coker (viralidad 78, SEO 95, chat 13x) -> prioridad ~86.
- IlloJuan verificado (viralidad 51, SEO 84, chat 6,44x) -> ~61, queda atrás.
"""

import json
import math
import os
from datetime import datetime

from .detect import _chat_base, band

DEFAULT_WEIGHTS = {"viral": 0.5, "seo": 0.3, "chat": 0.2}
WEIGHTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "priority_weights.json")

VIRAL_MIN_PUBLISH = 45      # < 45: descartado, no se publica
SEO_GATE = 80               # < 80: no se publica salvo override registrado
AGE_FREE_DAYS = 7           # hasta 7 días sin penalización
AGE_PENALTY_PER_DAY = 10    # puntos que pierde por día de viejo
STALE_DAYS = 3              # días sin subir por prioridad baja -> archivar solo
STALE_PRIORITY_MAX = 70     # por debajo de "prioritario" = prioridad baja

MIN_CLIPS_LEARN = 30        # clips publicados con datos a 7 días para aprender
MAX_ADJUST = 0.25           # cambio máximo de cada peso por ciclo (±25%)

PLATFORMS = ("youtube_shorts", "tiktok", "instagram_reels", "facebook_reels")


def chat_normalized(multiple):
    """Múltiplo pico/mediana -> 0-100 (misma escala que el componente
    de chat del score de viralidad, x10)."""
    return round(_chat_base(multiple) * 10.0, 1)


def age_penalty(days):
    """Puntos que pierde un clip por viejo: 0 hasta 7 días, 10 por cada
    día extra (días completos)."""
    extra = int(days or 0) - AGE_FREE_DAYS
    return max(0, extra) * AGE_PENALTY_PER_DAY


def priority(virality, seo, chat_multiple, age_days=0, weights=None):
    """Prioridad 0-100 de un clip para una plataforma, con desglose.

    Media geométrica ponderada: viralidad^w_v x SEO^w_s x chat_norm^w_c.
    ``virality`` y ``seo`` son 0-100; ``seo`` es el de ESA plataforma.
    ``weights`` es un dict {"viral","seo","chat"}; por defecto los pesos
    fijos 0,5/0,3/0,2 (BETA hasta 30 clips aprendidos).
    Devuelve dict con ``priority`` (ya con penalización por edad),
    ``base`` (sin penalización), ``chat_normalized``, ``age_penalty``,
    ``weights`` usados y ``formula`` = "geometric".
    """
    w = weights or DEFAULT_WEIGHTS
    virality = float(virality or 0.0)
    seo = float(seo or 0.0)
    chat_norm = chat_normalized(chat_multiple)
    base = (virality ** w["viral"]) * (seo ** w["seo"]) \
        * (chat_norm ** w["chat"]) if min(virality, seo, chat_norm) > 0 \
        else 0.0
    penalty = age_penalty(age_days)
    final = round(max(0.0, base - penalty), 1)
    return {
        "priority": final,
        "base": round(base, 1),
        "virality": round(virality, 1),
        "seo": round(seo, 1),
        "chat_normalized": chat_norm,
        "chat_multiple": chat_multiple,
        "age_days": int(age_days or 0),
        "age_penalty": penalty,
        "weights": dict(w),
        "formula": "geometric",
        "band": band(final),
    }


def publish_status(virality, seo, seo_override=False,
                   seo_gate=SEO_GATE, viral_min=VIRAL_MIN_PUBLISH):
    """Filtro duro de publicación. Devuelve (estado, motivo).

    Estados: ``descartado`` (viralidad < 45), ``bloqueado_seo``
    (SEO < 80 sin override), ``publicable_override`` (SEO < 80 con
    override registrado), ``publicable``.

    Los umbrales se pueden leer de config.yaml (scoring.seo_gate,
    scoring.viral_min, 6B.2); los valores por defecto son los de
    siempre y la fórmula no cambia.
    """
    virality = float(virality or 0.0)
    seo = float(seo or 0.0)
    if virality < viral_min:
        return ("descartado",
                f"viralidad {virality:.0f} < {viral_min}: descartado")
    if seo < seo_gate and not seo_override:
        return ("bloqueado_seo",
                f"SEO {seo:.0f} < {seo_gate} sin override: no se publica")
    if seo < seo_gate and seo_override:
        return ("publicable_override",
                f"SEO {seo:.0f} < {seo_gate} con override registrado")
    return ("publicable", f"SEO {seo:.0f} >= {seo_gate}")


def should_auto_archive(age_days, uploaded, priority_value, virality):
    """¿Archivar solo? Clip con 3+ días sin subir por prioridad baja
    (prioridad < 70, es decir, no prioritario ni urgente). Devuelve
    (sí/no, motivo)."""
    if uploaded:
        return False, "ya subido"
    if (age_days or 0) < STALE_DAYS:
        return False, f"lleva {int(age_days or 0)} día(s), menos de {STALE_DAYS}"
    if (priority_value or 0) >= STALE_PRIORITY_MAX:
        return False, (f"prioridad {priority_value:.0f} >= "
                       f"{STALE_PRIORITY_MAX}: sigue en cola")
    return True, (f"{int(age_days)} días sin subir con prioridad "
                  f"{priority_value:.0f} (< {STALE_PRIORITY_MAX}): archivado solo")


def moment_key(vod_id, start_sec):
    """Clave del momento: mismo VOD y mismo inicio (redondeado a 10 s)
    = mismo momento. Dos clips del mismo momento no se suben el mismo
    día en la misma plataforma."""
    return (vod_id, int(float(start_sec or 0) // 10))


def _clip_age_days(clip, now=None):
    created = clip.get("created_at") or clip.get("created") or ""
    if not created:
        return 0
    try:
        dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    except ValueError:
        return 0
    ref = now or datetime.now(dt.tzinfo)
    return max(0, (ref - dt).days)


def order_queue(clips, platform, now=None, weights=None):
    """Ordena la Cola por prioridad para una plataforma.

    ``clips``: lista de dicts con ``virality``, ``seo`` (número o dict
    por plataforma), ``chat_multiple``, ``vod_id``, ``start_sec``,
    ``created_at``, ``uploaded`` y ``seo_override`` opcional.
    Devuelve la lista ordenada por prioridad desc, cada clip anotado
    con ``priority`` (desglose), ``status`` y ``left_behind`` (motivo
    por el que quedó atrás, o None). Los clips del mismo momento el
    mismo día quedan marcados ``duplicate_moment`` y solo el de mayor
    prioridad sigue en cola.
    """
    scored = []
    for clip in clips:
        seo = clip.get("seo")
        if isinstance(seo, dict):
            seo = seo.get(platform)
        age = _clip_age_days(clip, now=now)
        p = priority(clip.get("virality"), seo, clip.get("chat_multiple"),
                     age_days=age, weights=weights)
        status, why = publish_status(clip.get("virality"), seo,
                                     seo_override=bool(clip.get("seo_override")))
        archive, archive_why = should_auto_archive(
            age, bool(clip.get("uploaded")), p["priority"],
            clip.get("virality"))
        item = dict(clip)
        item["priority"] = p
        item["status"] = status
        item["status_reason"] = why
        item["auto_archive"] = archive
        item["auto_archive_reason"] = archive_why if archive else None
        item["left_behind"] = None
        scored.append(item)

    # Mismo momento, mismo día, misma plataforma: solo el mejor sigue.
    seen = {}
    for item in scored:
        key = moment_key(item.get("vod_id"), item.get("start_sec"))
        if key in seen:
            other = seen[key]
            loser = item if item["priority"]["priority"] <= other["priority"]["priority"] else other
            loser["status"] = "duplicate_moment"
            loser["left_behind"] = (
                "mismo momento que otro clip con mayor prioridad: "
                "no se sube el mismo día en esta plataforma")
            if loser is other:
                seen[key] = item
        else:
            seen[key] = item

    scored.sort(key=lambda c: c["priority"]["priority"], reverse=True)
    for i, item in enumerate(scored, 1):
        item["queue_position"] = i
        if item["status"] in ("descartado", "bloqueado_seo",
                              "duplicate_moment"):
            item["left_behind"] = item["left_behind"] or item["status_reason"]
        elif item["auto_archive"]:
            item["left_behind"] = item["auto_archive_reason"]
    return scored


# ---------------------------------------------------------------------------
# Aprendizaje: recalibración de pesos con datos reales (Analíticas)
# ---------------------------------------------------------------------------

def _pearson(xs, ys):
    """Correlación de Pearson; 0,0 si no hay varianza o faltan datos."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs)
                    * sum((y - my) ** 2 for y in ys))
    return num / den if den > 0 else 0.0


def recalibrate(records, weights=None):
    """Recalibra SOLO los exponentes con datos reales a 7 días.

    ``records``: lista de dicts con ``virality``, ``seo``, ``chat_norm``
    (o ``chat_multiple``) y ``views`` (> 0) de clips ya publicados con
    datos a 7 días. Devuelve (nuevos_pesos, informe).
    - Menos de 30 registros: pesos sin cambio, informe lo dice (BETA).
    - Con 30+: correlación de Pearson entre log(componente) y log(vistas)
      por componente; cada peso se mueve en proporción a cuánto su
      correlación supera la media, con tope ±25% por ciclo; luego se
      normaliza para que sumen 1,0. Los filtros duros no se tocan nunca.
    """
    w = dict(weights or DEFAULT_WEIGHTS)
    usable = []
    for r in records:
        if (r.get("views") or 0) <= 0 \
                or (r.get("virality") or 0) <= 0 \
                or (r.get("seo") or 0) <= 0:
            continue
        r = dict(r)
        if "chat_norm" not in r and r.get("chat_multiple") is not None:
            r["chat_norm"] = chat_normalized(r["chat_multiple"])
        if (r.get("chat_norm") or 0) > 0:
            usable.append(r)
    n = len(usable)
    if n < MIN_CLIPS_LEARN:
        return w, {
            "status": "insuficiente",
            "n": n,
            "needed": MIN_CLIPS_LEARN,
            "reason": (f"solo {n}/{MIN_CLIPS_LEARN} clips con datos a 7 días: "
                       "pesos fijos 0,5/0,3/0,2, fórmula BETA"),
            "weights": dict(w),
        }
    log_views = [math.log(r["views"]) for r in usable]
    corrs = {}
    for key in ("viral", "seo", "chat"):
        field = {"viral": "virality", "seo": "seo",
                 "chat": "chat_norm"}[key]
        corrs[key] = round(_pearson(
            [math.log(r[field]) for r in usable], log_views), 3)
    mean_corr = sum(corrs.values()) / len(corrs)
    new = {}
    for key in ("viral", "seo", "chat"):
        # El peso crece si su componente predice mejor que la media.
        factor = 1.0 + MAX_ADJUST * max(-1.0, min(1.0,
                                                  corrs[key] - mean_corr))
        new[key] = w[key] * factor
    total = sum(new.values())
    new = {k: round(v / total, 4) for k, v in new.items()}
    return new, {
        "status": "recalibrated",
        "n": n,
        "date": datetime.now().isoformat(timespec="seconds"),
        "correlations": corrs,
        "old_weights": dict(w),
        "weights": new,
        "reason": (f"recalibración con {n} clips a 7 días: correlaciones "
                   f"{corrs}; pesos {w} -> {new} "
                   f"(máx. ±{MAX_ADJUST:.0%} por ciclo)"),
    }


def load_weights(path=None):
    """Pesos guardados + historial; por defecto los fijos BETA."""
    path = path or WEIGHTS_FILE
    if not os.path.isfile(path):
        return dict(DEFAULT_WEIGHTS), []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("weights", dict(DEFAULT_WEIGHTS)), \
        data.get("history", [])


def recalibrate_and_save(records, path=None):
    """Recalibra con los registros y guarda pesos + historial con fecha
    y motivo (registro que el Panel muestra en Analíticas). Devuelve
    el informe de ``recalibrate``."""
    path = path or WEIGHTS_FILE
    weights, history = load_weights(path)
    new_weights, report = recalibrate(records, weights)
    if report["status"] == "recalibrated":
        history.append(report)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"weights": new_weights, "history": history}, f,
                      ensure_ascii=False, indent=2)
    return report
