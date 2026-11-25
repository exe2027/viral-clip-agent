#!/usr/bin/env python3
"""Loop de revalidación + backfill de SEO (2026-11-24, auto-seo).

Cada corrida revisa la Cola del panel y deja listos los clips que
quedaron a medias: los que llegaron sin SEO (el scorer falló o son
anteriores al cambio) y los que quedaron en `pendiente_revision`
(Gemini caído al validar) o sin veredicto (el camino VOD no lo
mandaba antes de este cambio). Escribe de vuelta a D1 con PATCH;
nunca publica ni encola (eso lo hace el ciclo del Worker con
`auto_publish_top`).

Por corrida (tope 15, listos salvo SEO/veredicto primero; dentro
de cada grupo, orden de la Cola: LIVE, manual_rank, score desc,
created_at asc):
1. Candidatos: clips con `quality_gate` en (None, pendiente_revision)
   O sin `seo_scores` en alguna plataforma. Los `rechazado` NO se
   tocan. Un clip con `seo_scores` ya puesto NO se re-puntúa y un
   clip con veredicto ya puesto NO se re-valida (cada escritor solo
   escribe lo que el otro no escribió: no solapa con el trabajo
   manual en curso).
2. Anti-atasco: se salta los candidatos con un evento de tracking
   `revalidado` en las últimas 24 h (si Gemini cae varios días, los
   mismos 10 más antiguos no se reprocesarían en cada corrida).
3. SEO que falta: con `seo_inputs` guardados reproduce el score
   EXACTO del corte (`backfill_exacto`); sin ellos, aproximado con
   hashtags del juego (exactos), visible=None (coherencia 80,
   conservador), game_verified=True (`backfill_aproximado`). Sin MP4
   y sin Gemini.
4. Veredicto que falta o pendiente: descarga el MP4, transcribe a
   SRT (reutiliza `transcribe_clip_to_srt` de validate_all), corre
   `validate_final_clip` + `build_quality_gate` y hace PATCH con el
   veredicto. Si sigue pendiente no se escribe nada. Si es
   rechazado se escribe (bloqueo honesto). NO se llama a
   `preview_seen`: el gate pasa por el veredicto solo.
5. Cada intento deja un evento `revalidado` en el tracking del clip
   y un `worker_event`; nunca levanta por un clip.

Orden de estreno (spec 2026-11-24): el workflow nace solo con
`workflow_dispatch` y su primera corrida manual es DESPUÉS de que
Fix 3 esté desplegado (si corriera antes, podría grabar `rechazado`
por el falso positivo de `subtitulos_una_palabra`).
"""

import argparse
import json
import os
import sys
import tempfile
import time

from .final_review import validate_final_clip
from .metadata import game_hashtags_tags
from .panel_bridge import PanelBridge
from .quality_gate import build_quality_gate
from .seo_score import score_all_platforms
from .validate_all import transcribe_clip_to_srt

PLATFORMS = ["youtube_shorts", "tiktok", "instagram_reels",
             "facebook_reels"]
MAX_CLIPS_PER_RUN = 15
RETRY_COOLDOWN_S = 24 * 3600
STEP_REVALIDADO = "revalidado"


def _viral(clip):
    raw = clip.get("viral") or {}
    return raw if isinstance(raw, dict) else {}


def _seo_scores(clip):
    scores = _viral(clip).get("seo_scores") or {}
    if not isinstance(scores, dict):
        return {}
    return {p: s for p, s in scores.items()
            if isinstance(s, (int, float))}


def _missing_seo_platforms(clip):
    scores = _seo_scores(clip)
    return [p for p in PLATFORMS if p not in scores]


def _gate_decision(clip):
    gate = _viral(clip).get("quality_gate") or {}
    return gate.get("decision") if isinstance(gate, dict) else None


def _needs_work(clip):
    """True si el clip necesita SEO y/o veredicto."""
    if clip.get("status") == "publicado":
        return False
    if _missing_seo_platforms(clip):
        return True
    return _gate_decision(clip) in (None, "pendiente_revision")


def _numeric(value):
    """El número o None (los booleanos no son score/rank)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _is_ready(clip):
    """True si el clip está listo salvo SEO/veredicto (F5): tiene
    MP4 en R2 (`video_r2_key` no vacío), subtítulos completos, no
    está publicado, `score >= 45` y necesita SEO o veredicto según
    `_needs_work`."""
    if not clip.get("video_r2_key"):
        return False
    if clip.get("subtitles_complete") != 1:
        return False
    if clip.get("status") == "publicado":
        return False
    score = _numeric(clip.get("score"))
    if score is None or score < 45:
        return False
    return _needs_work(clip)


def _candidate_key(clip):
    """Orden F5: listos primero; dentro de cada grupo, el orden de
    la Cola en la aproximación disponible en Python (listQueueClips,
    db.ts:1675, SIN el boost de clip_stats que el endpoint no
    expone): LIVE primero, luego `manual_rank` (NULL al final),
    luego `score` descendente, luego `created_at` ascendente."""
    ready = 0 if _is_ready(clip) else 1
    live = 0 if clip.get("origin") == "live" else 1
    rank = _numeric(clip.get("manual_rank"))
    rank_key = (0, rank) if rank is not None else (1, 0)
    score = _numeric(clip.get("score"))
    score_key = -score if score is not None else float("inf")
    created = _numeric(clip.get("created_at"))
    clip_id = _numeric(clip.get("id"))
    return (ready, live, rank_key, score_key,
            created if created is not None else 0,
            clip_id if clip_id is not None else 0)


def _recently_attempted(bridge, clip_id, now_ms):
    """True si el clip tiene un `revalidado` en las últimas 24 h."""
    try:
        events = bridge.list_clip_events(clip_id) or []
    except Exception:  # noqa: BLE001 - sin lectura no se salta
        return False
    cutoff = now_ms - RETRY_COOLDOWN_S * 1000
    for event in events:
        event = event or {}
        if event.get("step") != STEP_REVALIDADO:
            continue
        at = event.get("at")
        if isinstance(at, (int, float)) and at > cutoff:
            return True
    return False


def _seo_inputs_exact(clip):
    """Entradas exactas del corte, si el clip las guarda."""
    inputs = _viral(clip).get("seo_inputs")
    if not isinstance(inputs, dict):
        return None
    if not isinstance(inputs.get("hashtags"), list):
        return None
    return inputs


def _seo_inputs_approx(clip):
    """Entradas aproximadas (clip anterior al cambio): hashtags del
    juego (exactos), visible desconocido (coherencia 80,
    conservador), juego verificado (los clips del cerebro pasaron
    el chequeo de juego GTA VI)."""
    hashtags, _tags = game_hashtags_tags("GTA 6")
    return {
        "hashtags": list(hashtags),
        "visible": None,
        "game_verified": True,
        "keyword": "GTA 6",
        "lang": clip.get("language") or "es",
    }


def _tags_of(clip):
    tags = clip.get("seo_tags") or []
    if isinstance(tags, str):
        return [t.strip() for t in tags.split(",") if t.strip()]
    return list(tags) if isinstance(tags, list) else []


def _backfill_seo(bridge, clip, dry_run=False):
    """Puntúa el SEO que falta y lo escribe. Devuelve
    (n_plataformas_escritas, fuente). Sin MP4 y sin Gemini."""
    missing = _missing_seo_platforms(clip)
    if not missing:
        return 0, None
    exact = _seo_inputs_exact(clip)
    if exact is not None:
        inputs = exact
        source = "backfill_exacto"
    else:
        inputs = _seo_inputs_approx(clip)
        source = "backfill_aproximado"
    scores = score_all_platforms({
        "title": clip.get("seo_title") or clip.get("title"),
        "description": clip.get("seo_description") or "",
        "tags": _tags_of(clip),
        "thumbnail_text": clip.get("thumb_text") or "",
        "hashtags": inputs.get("hashtags") or [],
        "game_verified": inputs.get("game_verified"),
        "visible": inputs.get("visible"),
        "lang": inputs.get("lang") or clip.get("language") or "es",
        "keyword": inputs.get("keyword") or "GTA 6",
    })
    patch = {}
    for platform in missing:
        entry = (scores or {}).get(platform) or {}
        value = entry.get("score") if isinstance(entry, dict) else None
        if isinstance(value, (int, float)):
            patch[platform] = value
    if not patch:
        return 0, source
    if not dry_run:
        bridge.update_clip(clip["id"], {
            "seo_scores": patch,
            "viral": {"seo_source": source},
        })
    return len(patch), source


def _revalidate_one(bridge, clip, cfg, workdir, validate_fn=None,
                    transcribe_fn=None, dry_run=False):
    """Valida UN clip sin veredicto o pendiente. Devuelve
    (decision_nueva, motivo). Nunca levanta por un clip: un fallo
    de descarga o del validador devuelve ("error", motivo)."""
    validate_fn = validate_fn or validate_final_clip
    transcribe_fn = transcribe_fn or transcribe_clip_to_srt
    dest = os.path.join(workdir, f"clip_{clip['id']}.mp4")
    try:
        path = bridge.download_video(clip["id"], dest)
    except Exception as exc:  # noqa: BLE001 - sin MP4 no se valida
        return "error", f"sin MP4 ({exc})"
    srt_path = None
    try:
        srt_path = transcribe_fn(
            path, workdir, clip["id"], clip.get("language") or "es")
    except Exception:  # noqa: BLE001 - sin SRT se valida igual
        srt_path = None
    try:
        result = validate_fn(
            path, clip.get("title") or "",
            plantilla=clip.get("plantilla"), cfg=cfg, srt_path=srt_path)
    except Exception as exc:  # noqa: BLE001 - el validador promete
        # no lanzar; si lo hace, es error, no un pasa.
        return "error", f"el validador lanzó: {exc}"
    gate = build_quality_gate(
        result, thumbnail_text=clip.get("thumb_text"),
        no_speech=False)
    decision = gate.get("decision") or "pendiente_revision"
    if decision == "pendiente_revision":
        # Gemini sigue caído: no se escribe nada, la próxima
        # corrida lo reintenta (con el anti-atasco de 24 h).
        return decision, gate.get("motivo") or "pendiente de revisión"
    if not dry_run:
        bridge.update_clip(clip["id"], {"viral": {"quality_gate": gate}})
    return decision, gate.get("motivo") or ""


def run(bridge, cfg=None, limit=MAX_CLIPS_PER_RUN, workdir=None,
        now_ms=None, validate_fn=None, transcribe_fn=None,
        dry_run=False):
    """Una corrida del loop. Devuelve el resumen (dict). Nunca
    levanta por un clip; un fallo de infraestructura (sin Cola)
    sí levanta."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    workdir = workdir or tempfile.mkdtemp(prefix="revalidate-")
    clips = bridge.list_clips() or []
    candidates = [c for c in clips if _needs_work(c)]
    candidates.sort(key=_candidate_key)
    summary = {"revisados": 0, "seo_rellenados": 0, "veredictos": 0,
               "saltados": 0, "errores": [], "clips": []}
    for clip in candidates:
        if summary["revisados"] >= limit:
            break
        clip_id = clip.get("id")
        if _recently_attempted(bridge, clip_id, now_ms):
            summary["saltados"] += 1
            continue
        detail = {"id": clip_id, "title": clip.get("title") or ""}
        try:
            n_seo, source = _backfill_seo(bridge, clip, dry_run=dry_run)
            detail["seo"] = {"plataformas": n_seo, "fuente": source}
            summary["seo_rellenados"] += n_seo
        except Exception as exc:  # noqa: BLE001 - un clip no tumba
            detail["seo"] = {"error": str(exc)}
            summary["errores"].append(
                f"clip {clip_id}: SEO falló ({exc})")
        if _gate_decision(clip) in (None, "pendiente_revision"):
            decision, motivo = _revalidate_one(
                bridge, clip, cfg, workdir,
                validate_fn=validate_fn, transcribe_fn=transcribe_fn,
                dry_run=dry_run)
            detail["veredicto"] = decision
            detail["motivo"] = motivo
            if decision in ("aprobado", "rechazado"):
                summary["veredictos"] += 1
            elif decision == "error":
                summary["errores"].append(
                    f"clip {clip_id}: validación falló ({motivo})")
        summary["revisados"] += 1
        summary["clips"].append(detail)
        if not dry_run:
            try:
                bridge.tracking_step(
                    clip_id, STEP_REVALIDADO,
                    f"revalidación automática: SEO {detail.get('seo')} "
                    f"veredicto {detail.get('veredicto')}",
                    estado=("fallido" if summary["errores"] and
                            detail.get("veredicto") == "error"
                            else "completado"))
            except Exception:  # noqa: BLE001 - el tracking no tumba
                pass
            try:
                bridge.report_event(
                    "revalidado",
                    f"clip {clip_id} «{clip.get('title') or ''}»: "
                    f"SEO {detail.get('seo')} "
                    f"veredicto {detail.get('veredicto')}")
            except Exception:  # noqa: BLE001 - el evento no tumba
                pass
    return summary


def main(argv=None):
    """CLI: python -m clipagent.revalidate_pending [--limit N]
    [--dry-run]. La sesión del panel sale de `panel_credentials`
    (bootstrap del workflow), nunca de secretos en el repo."""
    parser = argparse.ArgumentParser(
        description="Revalida clips pendientes y rellena el SEO "
                    "que falta en la Cola del panel.")
    parser.add_argument("--limit", type=int, default=MAX_CLIPS_PER_RUN,
                        help="Máximo de clips por corrida")
    parser.add_argument("--dry-run", action="store_true",
                        help="Lista candidatos sin escribir nada")
    args = parser.parse_args(argv)
    bridge = PanelBridge.from_env()
    if bridge is None:
        print("ERROR: sin panel (PANEL_TENANT/PANEL_BASE_URL): "
              "dispara desde el panel con Iniciar.", file=sys.stderr)
        return 2
    try:
        from .config import load_config
        cfg = load_config()
    except Exception as exc:
        print(f"ERROR: sin config.yaml válido: {exc}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="revalidate-") as workdir:
        summary = run(bridge, cfg=cfg, limit=args.limit,
                      workdir=workdir, dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
