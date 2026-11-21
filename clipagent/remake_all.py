"""Re-hecho automático a rubius de los clips bloqueados (2026-11-20).

Toma los clips con ``publish_blocked=1`` de la Cola del panel y
por cada uno ejecuta el ciclo completo: descarga su segmento del
VOD de Twitch -> transcribe -> render rubius (cámara arriba,
gameplay abajo, banner del título, subtítulos palabra por palabra,
SFX y marca de agua) -> valida el vídeo final con
``validate_final_clip`` (plantilla rubius) -> si pasa, sube el
vídeo corregido (PUT /api/clips/:id/video, que además resetea
``preview_seen`` a 0 en el panel) y desbloquea el clip
(publish_blocked=0). Si la validación falla, re-renderiza con la
otra estrategia de caja de cámara (detectada <-> default) hasta
``--max-intentos``; si sigue fallando, el clip queda para revisión
humana. Un clip que falla nunca tumba el batch.

P0 #4 (2026-11-21, auditoría #25): cada re-make que pasa
recalcula el veredicto de calidad (``build_quality_gate`` sobre
el review del intento que pasó) y lo guarda en el PATCH
(``viral.quality_gate``): el veredicto viejo (rechazado) no
puede quedar, porque el gate del Worker lo seguiría leyendo y
el clip re-hecho nunca publicaría. El Worker reemplaza el
veredicto entero (mergeViralJson), nunca lo fusiona por
criterio.

Origen del VOD: el registro del panel trae ``source_id`` (id del
VOD de Twitch) y ``start_sec``/``end_sec``. Sin ``source_id`` el
momento no se puede localizar automáticamente (los clips en vivo
se cortaron del directo y el VOD no quedó registrado): el clip
queda como ``sin_vod`` con acción ``avisar-humano`` y el batch
sigue con el siguiente. Es el caso de 109 de los 118 bloqueados
(2026-11-20: solo los 9 de origen VOD traen source_id); la
resolución del VOD por fecha la hace ``recover_vod_source.py``
(corrida de GitHub + VOD de Twitch + match de audio) y su mapa
(``--source-map``) se pasa aquí: un clip en vivo con fuente
recuperada de confianza alta se re-hace igual que uno de VOD.

Acciones (por clip):
- ``desbloqueado``: re-hecho, validado, subido y desbloqueado
  (clip en Cola, no publicado).
- ``re-make-replace``: re-hecho y validado, vídeo del panel
  reemplazado, pero el clip está PUBLICADO: NO se desbloquea
  (desbloquearlo haría que el auto-publicador lo volviera a
  publicar y duplicaría las copias). Las copias publicadas se
  borran y se republican a mano (TikTok no tiene API de borrado).
- ``avisar-humano``: sin VOD, descarga/transcripción/render sin
  remedio, o validación fallida tras todos los intentos.
- ``reintentar``: el veredicto quedó pendiente (Gemini no
  disponible). No es un fallo del clip: se vuelve a procesar
  después, no se sube nada.

Sin voz verificable: tras transcribir, si no hay voz se aplica
la misma regla que el camino en vivo (policy.no_speech_blocked):
sin voz + pasa las puertas (viralidad >=45, SEO >=80) se re-hace
y se publica igual SIN subtítulos (nunca se inventa texto);
sin voz + bajo las puertas no se sube. Los scores salen del
registro del panel (viral_score / seo_scores); sin ellos la
regla falla cerrada (humano).

TODO vive en el cerebro (regla del usuario 2026-11-20 00:04):
el panel solo dispara (repository_dispatch ``panel-remake-all``)
y recibe el vídeo + el desbloqueo; ningún procesamiento va en
el Worker.

Salida: el batch NUNCA falla por un clip (un fallo de clip es
un dato: queda en el JSON con outcome ``error``/``falla``/
``sin_vod`` y el batch sigue). Solo falla en voz alta (exit 2)
por infraestructura: sin config del validador, sin panel, sin
sesión, o sin la herramienta de descarga del VOD.

Invocación:
  python -m clipagent.remake_all --from-panel
  python -m clipagent.remake_all --from-panel --ids 7,16 --limit 20
  python -m clipagent.remake_all --from-panel --max-intentos 2
  python -m clipagent.remake_all --dry-run --from-audit \\
      runs/face-audit-2026-11-19/clips.json --source-map \\
      runs/remake-source-map.json --out resultado.json
El modo --dry-run NO descarga, ni renderiza, ni sube nada: solo
clasifica cada clip bloqueado según tenga VOD resoluble o no.
"""

import argparse
import json
import os
import sys
import time

from .final_review import (
    find_facecam_box_in_source,
    transcript_text_for_srt,
    validate_final_clip,
)
from .panel_bridge import DEFAULT_BASE_URL, PanelBridge
from .rdjavi import DEFAULT_CAM_BOX, DEFAULT_CAM_SQUARE
from .rubius import (
    BANNER_SHOW,
    make_banner_png,
    make_word_ass,
    render as render_rubius,
)
from .transcribe import parse_srt
from .validate import ConfigError

# Acciones posibles (ver docstring).
ACCION_DESBLOQUEADO = "desbloqueado"
ACCION_REMAKE = "re-make-replace"
ACCION_HUMANO = "avisar-humano"
ACCION_REINTENTAR = "reintentar"

# Outcomes por clip.
OUTCOME_REHECHO = "rehecho"
OUTCOME_REEMPLAZO = "rehecho-reemplazo"
OUTCOME_SIN_VOD = "sin_vod"
OUTCOME_ERROR = "error"
OUTCOME_FALLA = "falla"
OUTCOME_PENDIENTE = "pendiente"
OUTCOME_SIN_VOZ = "sin_voz"

MAX_INTENTOS_DEFAULT = 3
DOWNLOAD_REINTENTOS = 2


class RemakeError(Exception):
    """Fallo de infraestructura del batch (no de un clip)."""


def _safe_get(clip, key, default=None):
    """clip.get(key) tolerante: un registro que no es dict no
    puede tumbar el batch (queda como error visible)."""
    if isinstance(clip, dict):
        return clip.get(key, default)
    return default


def _base_result(clip, outcome, accion, motivo, extra=None):
    rec = {
        "id": _safe_get(clip, "id"),
        "title": _safe_get(clip, "title") or "",
        "streamer": _safe_get(clip, "streamer"),
        "status": _safe_get(clip, "status") or "en_carpeta",
        "origin": _safe_get(clip, "origin") or "",
        "source_id": _safe_get(clip, "source_id"),
        "outcome": outcome,
        "accion": accion,
        "motivo": motivo or "",
    }
    if extra:
        rec.update(extra)
    return rec


def resolve_source(clip, source_map=None):
    """VOD de un clip: (vod_id, start_sec, end_sec) o (None, motivo).

    Solo el VOD directo (source_id del panel). Sin source_id el
    momento no se puede localizar: los clips en vivo se cortaron
    del directo y el VOD no quedó registrado en el panel. Con un
    ``source_map`` (``{id: {source_id, start_sec, end_sec}}`` del
    recuperador ``recover_vod_source.py``, solo recuperaciones de
    confianza alta verificadas por audio) se usa la fuente
    recuperada cuando el panel no trae ninguna; si el panel ya
    trae source_id, manda el panel (es el registro directo).
    """
    raw = clip.get("source_id")
    try:
        vod_id = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        vod_id = 0
    start = clip.get("start_sec")
    end = clip.get("end_sec")
    try:
        start = int(start)
        end = int(end)
    except (TypeError, ValueError):
        start, end = 0, 0
    if vod_id <= 0 and source_map:
        entry = (source_map or {}).get(clip.get("id")) or {}
        try:
            vod_id = int(entry.get("source_id") or 0)
            start = int(entry.get("start_sec"))
            end = int(entry.get("end_sec"))
        except (TypeError, ValueError):
            vod_id, start, end = 0, 0, 0
    if vod_id <= 0:
        return None, (
            "sin source_id: el VOD no está registrado en el panel "
            "(clip en vivo sin VOD asociado); el momento no se "
            "puede localizar automáticamente"
        )
    if end <= start:
        return None, (
            f"rango inválido en el panel: start_sec={start} "
            f"end_sec={end} (VOD {vod_id})"
        )
    return (vod_id, start, end), ""


def _quality_gate_for_review(review, title, no_speech):
    """Veredicto B4 nuevo a partir del review del intento que pasó.

    P0 #4 (2026-11-21, auditoría #25): al desbloquear, el
    veredicto viejo de quality_gate quedaba en el clip y el gate
    del Worker lo seguía leyendo (rechazado) aunque el re-make
    pasara. Cada re-make que pasa recalcula el veredicto con
    ``build_quality_gate`` (los 6 criterios B4 sobre el review
    de ``validate_final_clip``) y lo guarda en el PATCH
    (viral.quality_gate): el Worker lo reemplaza entero
    (mergeViralJson), nunca lo fusiona por criterio. Nunca
    levanta: sin veredicto usable devuelve pendiente_revision.
    """
    from .quality_gate import build_quality_gate
    try:
        return build_quality_gate(
            review or {}, thumbnail_text=title,
            no_speech=bool(no_speech))
    except Exception:  # noqa: BLE001 - sin veredicto, a revisión
        return {"decision": "pendiente_revision", "pasa": False,
                "criterios": {}, "fallos": [], "fallos_duros": [],
                "motivo": "puerta de calidad sin ejecutar"}


def _box_str(cam_box_list):
    """[x, y, w, h] (px de la fuente) -> 'w:h:x:y' (ffmpeg crop)."""
    x, y, w, h = (int(v) for v in cam_box_list)
    return f"{w}:{h}:{x}:{y}"


def _square_str(cam_box_list):
    """Cuadrado centrado en la caja (la cara suele estar centrada
    en el PiP; mismo patrón que el default 365:365:90:0 dentro de
    561:365:0:0). Es el centro del punch-in; sin cara detectada no
    se inventa un cuadrado: el llamador usa el default."""
    x, y, w, h = (int(v) for v in cam_box_list)
    side = min(w, h)
    sx = x + (w - side) // 2
    sy = y + (h - side) // 2
    return f"{side}:{side}:{sx}:{sy}"


def box_strategies(segment, duration, cfg, detect_fn=None):
    """Estrategias de caja de cámara para el render, en orden.

    Primera: la caja detectada en el segmento (face-crop ->
    bordes -> YuNet). Segunda: el default de la receta. Si la
    detección no encuentra nada o coincide con el default, solo
    hay una estrategia: re-renderizar con la misma caja no
    arregla nada y no se quema un intento en ello.
    """
    detect_fn = detect_fn or find_facecam_box_in_source
    strategies = []
    try:
        found = detect_fn(segment, duration, cfg)
    except Exception:  # noqa: BLE001 - sin detección no hay
        # estrategia detectada; queda el default.
        found = None
    box = (found or {}).get("box") if isinstance(found, dict) else None
    detected = None
    if box:
        try:
            detected = (_box_str(box), _square_str(box))
        except (TypeError, ValueError):
            detected = None
    default = (DEFAULT_CAM_BOX, DEFAULT_CAM_SQUARE)
    # Deduplicar por la caja: el cuadrado solo mueve el centro
    # del punch-in (98 vs 90 px en el default) y re-renderizar
    # por eso no arregla un fallo de cara.
    if detected and detected[0] != default[0]:
        strategies.append(("detectada", detected[0], detected[1]))
    strategies.append(("default", default[0], default[1]))
    return strategies


def download_segment(vod_id, start_sec, end_sec, out_path,
                     download_fn=None, reintentos=DOWNLOAD_REINTENTOS):
    """Baja [start_sec, end_sec] del VOD (solo ese tramo).

    Reintenta la descarga (la herramienta a veces falla por red).
    Si el segmento sale truncado (< duración pedida - 2 s) se
    reintenta también: un recorte corto rompería el render.
    """
    if download_fn is None:
        from auto_clips import download_twitch_segment

        download_fn = download_twitch_segment
    last_exc = None
    for intento in range(1, reintentos + 1):
        try:
            download_fn(vod_id, start_sec, end_sec, out_path)
        except Exception as exc:  # noqa: BLE001 - reintento o
            # error visible, nunca un fallo silencioso.
            last_exc = exc
            if intento < reintentos:
                print(
                    f"  descarga intento {intento} falló ({exc}); "
                    f"reintentando...",
                    flush=True,
                )
                continue
            raise RemakeError(
                f"no se pudo bajar el segmento [{start_sec}s, "
                f"{end_sec}s] del VOD {vod_id}: {exc}"
            ) from exc
        if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            try:
                from .thumbnails import get_duration

                got = get_duration(out_path) or 0
            except Exception:  # noqa: BLE001 - sin duración no
                got = 0
            want = float(end_sec - start_sec)
            if got and got < want - 2:
                last_exc = RuntimeError(
                    f"segmento truncado: {got:.1f}s de {want:.0f}s"
                )
                if intento < reintentos:
                    print(
                        f"  descarga intento {intento} truncada "
                        f"({got:.1f}s); reintentando...",
                        flush=True,
                    )
                    continue
                raise RemakeError(str(last_exc)) from last_exc
            return out_path
        last_exc = RuntimeError("descarga vacía (0 bytes)")
        if intento < reintentos:
            continue
        raise RemakeError(
            f"no se pudo bajar el segmento [{start_sec}s, "
            f"{end_sec}s] del VOD {vod_id}: descarga vacía"
        ) from last_exc
    raise RemakeError(f"descarga fallida: {last_exc}")


def render_rubius_clip(segment, duration, title, out_path, cfg,
                       lang="es", cam_box=None, cam_square=None,
                       transcribe_fn=None, render_fn=None):
    """Transcribe + render rubius de UN segmento. Devuelve dict.

    ``transcribe_fn`` es workers.subtitle_clip (sin quemado: el
    rubius render quema el ASS); ``render_fn`` es rubius.render.
    Sin voz verificable el SRT sale vacío y el render quema solo
    el título (nunca se inventa texto). La marca de agua sale de
    cfg["watermark"] (Ajustes del panel); sin ella el clip sale
    sin marca y queda anotado, nunca se inventa una marca.
    """
    from .workers import subtitle_clip

    transcribe_fn = transcribe_fn or subtitle_clip
    render_fn = render_fn or render_rubius
    base = os.path.splitext(out_path)[0]
    srt_path = base + ".srt"
    sub = transcribe_fn(
        segment, srt=srt_path, burn=None, cfg=cfg or {}, lang=lang,
        title=title,
    )
    no_speech = bool((sub or {}).get("no_speech"))
    srt_path = (sub or {}).get("srt") or srt_path
    transcript_path = (sub or {}).get("transcript")
    banner_png = None
    if (title or "").strip():
        banner_png = base + "_banner.png"
        make_banner_png(title, banner_png)
    ass_path = base + ".ass"
    blocks = parse_srt(srt_path) if os.path.isfile(srt_path) else []
    ass_text = make_word_ass(
        blocks,
        max_end=float(duration),
        banner_show=BANNER_SHOW if banner_png else None,
    )
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_text)
    watermark = (cfg or {}).get("watermark") or None
    _dt, final = render_fn(
        segment,
        0,
        float(duration),
        ass_path,
        out_path,
        banner_png=banner_png,
        cam_box=cam_box or DEFAULT_CAM_BOX,
        cam_square=cam_square or DEFAULT_CAM_SQUARE,
        cfg=cfg or {},
        watermark=watermark,
    )
    return {
        "final": final,
        "srt": srt_path,
        "transcript": transcript_path,
        "no_speech": no_speech,
        "watermark": bool(watermark),
    }


def _is_published(clip):
    return (clip.get("status") or "en_carpeta") == "publicado"


def remake_one(clip, bridge, work_dir, cfg, max_intentos=3,
               download_fn=None, transcribe_fn=None, render_fn=None,
               validate_fn=None, detect_fn=None, source_map=None):
    """Re-hace UN clip; nunca lanza por un clip.

    Devuelve el registro del JSON (outcome + acción + motivo +
    intentos). Sin VOD, con la descarga/transcripción rota o con
    la validación fallida tras todos los intentos: acción
    ``avisar-humano`` y el batch sigue.
    """
    from .policy import no_speech_blocked

    validate_fn = validate_fn or validate_final_clip
    clip_id = clip.get("id")
    title = clip.get("seo_title") or clip.get("title") or ""
    lang = clip.get("language") or "es"
    if lang not in ("es", "en"):
        lang = "es"
    if not (title or "").strip():
        return _base_result(
            clip, OUTCOME_ERROR, ACCION_HUMANO, "sin título en el panel"
        )
    source, motivo = resolve_source(clip, source_map)
    if source is None:
        return _base_result(clip, OUTCOME_SIN_VOD, ACCION_HUMANO, motivo)
    vod_id, start_sec, end_sec = source
    duration = float(end_sec - start_sec)
    clip_dir = os.path.join(work_dir, f"clip_{clip_id}")
    os.makedirs(clip_dir, exist_ok=True)
    seg_path = os.path.join(clip_dir, "segment.mp4")
    try:
        download_segment(
            vod_id, start_sec, end_sec, seg_path,
            download_fn=download_fn,
        )
    except RemakeError as exc:
        return _base_result(clip, OUTCOME_ERROR, ACCION_HUMANO, str(exc))
    except Exception as exc:  # noqa: BLE001 - la descarga rota
        # es error del clip, no del batch.
        return _base_result(
            clip, OUTCOME_ERROR, ACCION_HUMANO,
            f"descarga del segmento falló: {exc}",
        )
    strategies = box_strategies(seg_path, duration, cfg, detect_fn)
    attempts = strategies[: max(1, int(max_intentos))]
    intentos = []
    last_review = None
    render_ok = False
    for n, (nombre, cam_box, cam_square) in enumerate(attempts, start=1):
        out_path = os.path.join(clip_dir, f"rubius_r{n}.mp4")
        try:
            rendered = render_rubius_clip(
                seg_path, duration, title, out_path, cfg, lang=lang,
                cam_box=cam_box, cam_square=cam_square,
                transcribe_fn=transcribe_fn, render_fn=render_fn,
            )
        except ConfigError:
            raise
        except Exception as exc:  # noqa: BLE001 - el render
            # roto es error del clip (o de la estrategia).
            intentos.append({
                "n": n, "estrategia": nombre, "error": str(exc)[:300],
            })
            continue
        render_ok = True
        final = rendered["final"]
        no_speech = bool(rendered.get("no_speech"))
        if no_speech:
            viral_total = clip.get("viral_score")
            seo_scores = clip.get("seo_scores") or {}
            if no_speech_blocked(
                    True, viral_total, seo_scores, cfg):
                return _base_result(
                    clip, OUTCOME_SIN_VOZ, ACCION_HUMANO,
                    "sin voz verificable y bajo las puertas "
                    "(viralidad/SEO): no se sube",
                    {"intentos": intentos, "no_speech": True},
                )
        try:
            review = validate_fn(
                final, title,
                transcript_text=transcript_text_for_srt(
                    rendered.get("srt") or ""),
                srt_path=rendered.get("srt") or "",
                cfg=cfg or {}, plantilla="rubius",
            )
        except ConfigError:
            raise
        except Exception as exc:  # noqa: BLE001 - sin veredicto
            # no se aprueba: humano, no un pasa inventado.
            intentos.append({
                "n": n, "estrategia": nombre,
                "error": f"el validador lanzó: {exc}",
                "no_speech": no_speech,
            })
            continue
        last_review = review or {}
        decision = last_review.get("decision") or ""
        pasa = last_review.get("pasa")
        intento_rec = {
            "n": n,
            "estrategia": nombre,
            "pasa": pasa,
            "decision": decision,
            "motivo": (last_review.get("motivo")
                       or last_review.get("rerender_reason") or ""),
            "hard_failures": list(last_review.get("hard_failures") or []),
            "final": final,
            "no_speech": no_speech,
        }
        intentos.append(intento_rec)
        if pasa is True:
            break
        if decision == "pendiente_revision":
            return _base_result(
                clip, OUTCOME_PENDIENTE, ACCION_REINTENTAR,
                last_review.get("motivo")
                or "Gemini no disponible (pendiente de revisión)",
                {"intentos": intentos, "no_speech": no_speech},
            )
        # Falla: solo hay otro intento si queda otra estrategia
        # de caja (la cara es lo único que un re-render puede
        # arreglar; re-renderizar igual daría lo mismo).
    if not render_ok:
        # Ningún intento llegó a renderizar: humano.
        return _base_result(
            clip, OUTCOME_ERROR, ACCION_HUMANO,
            "el render falló en todos los intentos",
            {"intentos": intentos},
        )
    passed = next(
        (i for i in intentos if i.get("pasa") is True), None)
    if passed is None:
        motivo = ""
        if last_review:
            motivo = (last_review.get("motivo")
                      or last_review.get("rerender_reason") or "")
        if not motivo:
            motivo = "validación final fallida tras todos los intentos"
        return _base_result(
            clip, OUTCOME_FALLA, ACCION_HUMANO, motivo,
            {"intentos": intentos},
        )
    final = passed["final"]
    no_speech = bool(passed.get("no_speech", False))
    # El intento que pasó es el último registrado con pasa=True;
    # su no_speech vive en el render de ese intento.
    # P0 #4 (2026-11-21, auditoría #25): el intento que pasó es
    # el último (el bucle rompe al pasar), así que last_review es
    # su veredicto. Se recalcula el quality_gate con él: el viejo
    # (rechazado) no puede quedar en el clip.
    new_gate = _quality_gate_for_review(last_review, title, no_speech)
    try:
        bridge.upload_video(clip_id, final)
    except Exception as exc:  # noqa: BLE001 - sin subida no
        # hay re-hecho: humano, el vídeo queda en el work dir.
        return _base_result(
            clip, OUTCOME_ERROR, ACCION_HUMANO,
            f"no se pudo subir el vídeo corregido: {exc}",
            {"intentos": intentos, "final": final},
        )
    # La subida (PUT /api/clips/:id/video) ya resetea
    # preview_seen a 0 en el panel: el usuario revisa el vídeo
    # nuevo antes de que se pueda publicar.
    subtitles_complete = 0 if no_speech else 1
    if _is_published(clip):
        try:
            bridge.update_clip(
                clip_id, {"subtitles_complete": subtitles_complete,
                          "viral": {"quality_gate": new_gate}})
        except Exception as exc:  # noqa: BLE001 - el vídeo ya
            # está reemplazado; el flag es secundario.
            print(
                f"  clip {clip_id}: PATCH subtitles_complete falló "
                f"({exc}); el vídeo sí quedó reemplazado",
                flush=True,
            )
        return _base_result(
            clip, OUTCOME_REEMPLAZO, ACCION_REMAKE,
            "vídeo del panel reemplazado; las copias publicadas "
            "se borran y se republican a mano (TikTok no tiene "
            "API de borrado)",
            {"intentos": intentos, "final": final,
             "no_speech": no_speech, "quality_gate": new_gate},
        )
    try:
        bridge.update_clip(clip_id, {
            "publish_blocked": 0,
            "subtitles_complete": subtitles_complete,
            "viral": {"quality_gate": new_gate},
        })
    except Exception as exc:  # noqa: BLE001 - sin desbloqueo
        # el re-hecho no está completo: humano.
        return _base_result(
            clip, OUTCOME_ERROR, ACCION_HUMANO,
            f"vídeo subido pero no se pudo desbloquear: {exc}",
            {"intentos": intentos, "final": final},
        )
    return _base_result(
        clip, OUTCOME_REHECHO, ACCION_DESBLOQUEADO,
        "" if not no_speech else
        "sin voz verificable: se publica igual sin subtítulos "
        "(pasa las puertas)",
        {"intentos": intentos, "final": final,
         "no_speech": no_speech, "quality_gate": new_gate},
    )


def load_blocked_clips(bridge):
    """Clips bloqueados del panel con el registro completo.

    GET /api/clips devuelve la fila entera (clipOut), así que
    source_id/start_sec/end_sec ya vienen; si un registro los
    trae vacíos se pide el registro completo con get_clip antes
    de darlo por sin VOD.
    """
    raw = bridge.list_clips() or []
    clips = []
    for c in raw or []:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        if not c.get("publish_blocked"):
            continue
        clip = {
            "id": c.get("id"),
            "title": c.get("title") or "",
            "seo_title": c.get("seo_title") or "",
            "streamer": (
                c.get("streamer")
                or c.get("influencer_name")
                or c.get("influencer")
            ),
            "status": c.get("status") or "en_carpeta",
            "origin": c.get("origin") or "",
            "language": c.get("language") or "es",
            "source_id": c.get("source_id"),
            "start_sec": c.get("start_sec"),
            "end_sec": c.get("end_sec"),
            "publish_blocked": c.get("publish_blocked"),
            "viral_score": c.get("viral_score"),
            "seo_scores": c.get("seo_scores"),
        }
        if not clip.get("source_id"):
            try:
                full = bridge.get_clip(clip["id"]) or {}
                for key in ("source_id", "start_sec", "end_sec",
                            "seo_title", "title", "status", "origin",
                            "language", "viral_score", "seo_scores"):
                    if clip.get(key) in (None, "", 0) and full.get(key) not in (None, ""):
                        clip[key] = full.get(key)
            except Exception as exc:  # noqa: BLE001 - sin el
                # registro completo el clip queda como sin VOD con
                # el motivo guardado, no en silencio.
                clip["_get_clip_error"] = str(exc)[:200]
        clips.append(clip)
    return clips


def _summary(results):
    outcomes = {
        OUTCOME_REHECHO: 0,
        OUTCOME_REEMPLAZO: 0,
        OUTCOME_SIN_VOD: 0,
        OUTCOME_ERROR: 0,
        OUTCOME_FALLA: 0,
        OUTCOME_PENDIENTE: 0,
        OUTCOME_SIN_VOZ: 0,
    }
    actions = {
        ACCION_DESBLOQUEADO: 0,
        ACCION_REMAKE: 0,
        ACCION_HUMANO: 0,
        ACCION_REINTENTAR: 0,
    }
    for r in results:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
        actions[r["accion"]] = actions.get(r["accion"], 0) + 1
    return {
        "total": len(results),
        "rehecho": outcomes[OUTCOME_REHECHO],
        "rehecho_reemplazo": outcomes[OUTCOME_REEMPLAZO],
        "sin_vod": outcomes[OUTCOME_SIN_VOD],
        "error": outcomes[OUTCOME_ERROR],
        "falla": outcomes[OUTCOME_FALLA],
        "pendiente": outcomes[OUTCOME_PENDIENTE],
        "sin_voz": outcomes[OUTCOME_SIN_VOZ],
        "desbloqueado": actions[ACCION_DESBLOQUEADO],
        "re_make_replace": actions[ACCION_REMAKE],
        "avisar_humano": actions[ACCION_HUMANO],
        "reintentar": actions[ACCION_REINTENTAR],
    }


def _write_json(out_path, payload):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, out_path)


def _print_summary(summary):
    print(
        f"total={summary['total']} rehecho={summary['rehecho']} "
        f"reemplazo={summary['rehecho_reemplazo']} "
        f"sin_vod={summary['sin_vod']} error={summary['error']} "
        f"falla={summary['falla']} "
        f"pendiente={summary['pendiente']} "
        f"sin_voz={summary['sin_voz']}",
        flush=True,
    )
    print(
        f"acciones: desbloqueado={summary['desbloqueado']} "
        f"re-make-replace={summary['re_make_replace']} "
        f"avisar-humano={summary['avisar_humano']} "
        f"reintentar={summary['reintentar']}",
        flush=True,
    )


def run_batch(clips, bridge, *, out_path, work_dir, tenant, cfg,
              max_intentos=MAX_INTENTOS_DEFAULT, source_map=None, **fns):
    """Re-hace la lista y escribe el JSON de la corrida.

    Un clip que falla no tumba el batch: queda registrado y se
    sigue con el siguiente. Devuelve el payload escrito.
    """
    os.makedirs(work_dir, exist_ok=True)
    results = []
    for clip in clips:
        try:
            rec = remake_one(
                clip, bridge, work_dir, cfg,
                max_intentos=max_intentos, source_map=source_map,
                **fns,
            )
        except ConfigError:
            raise
        except Exception as exc:  # noqa: BLE001 - un clip no
            # tumba el batch; queda como error visible.
            rec = _base_result(
                clip, OUTCOME_ERROR, ACCION_HUMANO,
                f"error inesperado en el batch: {exc}",
            )
        results.append(rec)
        print(
            f"clip {rec['id']}: {rec['outcome']} "
            f"({rec['accion']}) {rec['motivo']}",
            flush=True,
        )
    summary = _summary(results)
    payload = {
        "modo": "panel",
        "tenant": tenant,
        "generado_en": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "plantilla": "rubius",
        "max_intentos": max_intentos,
        "resumen": summary,
        "notas": [
            "TikTok no tiene API de borrado: la copia publicada "
            "en TikTok la borra el usuario desde la app.",
            "PUT /api/clips/:id/video resetea preview_seen a 0: "
            "el usuario revisa el vídeo nuevo antes de publicar.",
            "Los clips publicados que pasan se reemplazan en el "
            "panel pero NO se desbloquean: desbloquearlos haría "
            "que el auto-publicador los volviera a publicar.",
        ],
        "clips": results,
    }
    _write_json(out_path, payload)
    _print_summary(summary)
    print(f"JSON: {out_path}", flush=True)
    return payload


def run_dry_run(records, source_map, out_path, tenant):
    """Clasifica los bloqueados sin descargar ni renderizar nada.

    ``records`` son los registros de la auditoría (clips.json);
    ``source_map`` es {id: {source_id, start_sec, end_sec}} (de
    D1, solo lectura). Un clip con VOD queda como rehecho
    pendiente (re-render si está en Cola, re-make-replace si
    está publicado); sin VOD queda sin_vod / avisar-humano.
    """
    results = []
    for rec in records or []:
        if not rec.get("publish_blocked"):
            continue
        clip = {
            "id": rec.get("id"),
            "title": rec.get("title") or "",
            "streamer": rec.get("streamer"),
            "status": rec.get("status") or "en_carpeta",
            "origin": rec.get("origin") or "",
        }
        src = (source_map or {}).get(rec.get("id")) or {}
        clip["source_id"] = src.get("source_id", rec.get("source_id"))
        clip["start_sec"] = src.get("start_sec", rec.get("start_sec"))
        clip["end_sec"] = src.get("end_sec", rec.get("end_sec"))
        source, motivo = resolve_source(clip)
        if source is None:
            results.append(_base_result(
                clip, OUTCOME_SIN_VOD, ACCION_HUMANO, motivo))
        else:
            accion = (ACCION_REMAKE if _is_published(clip)
                      else ACCION_DESBLOQUEADO)
            results.append(_base_result(
                clip, "rehacer", accion,
                f"VOD {source[0]} [{source[1]}s, {source[2]}s] "
                f"(dry-run: sin descargar ni renderizar)",
                {"vod": {"vod_id": source[0],
                         "start_sec": source[1],
                         "end_sec": source[2]}},
            ))
    summary = _summary([
        {**r, "outcome": (
            OUTCOME_SIN_VOD if r["outcome"] == OUTCOME_SIN_VOD
            else OUTCOME_REHECHO)}
        for r in results
    ])
    payload = {
        "modo": "dry-run",
        "tenant": tenant,
        "generado_en": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "plantilla": "rubius",
        "resumen": summary,
        "notas": [
            "Dry-run: clasifica los bloqueados de la auditoría "
            "según tengan VOD resoluble o no, sin descargar, sin "
            "renderizar y sin subir nada.",
            "TikTok no tiene API de borrado: la copia publicada "
            "en TikTok la borra el usuario desde la app.",
        ],
        "clips": results,
    }
    _write_json(out_path, payload)
    _print_summary(summary)
    print(f"JSON: {out_path}", flush=True)
    return payload


def _default_out_path(tenant):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return os.path.join("runs", f"remake-all-{tenant}-{stamp}.json")


def panel_base_url():
    """URL del Worker: PANEL_URL, PANEL_BASE_URL o el default."""
    return (
        os.environ.get("PANEL_URL")
        or os.environ.get("PANEL_BASE_URL")
        or DEFAULT_BASE_URL
    ).rstrip("/")


def main(argv=None):
    """CLI del batch. Exit 0 = batch completo (los fallos de
    clip son datos, no errores); exit 2 = infraestructura."""
    parser = argparse.ArgumentParser(
        description="Re-hace a rubius los clips bloqueados de la "
        "Cola: render desde el VOD, validación final, subida y "
        "desbloqueo si pasa."
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "--from-panel",
        action="store_true",
        help="lista de bloqueados desde el panel (por defecto)",
    )
    src.add_argument(
        "--from-audit",
        metavar="PATH",
        help="lista de bloqueados desde clips.json de la auditoría",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="clasifica sin descargar, renderizar ni subir nada",
    )
    parser.add_argument(
        "--source-map",
        default=None,
        help="JSON {id: {source_id, start_sec, end_sec}} del "
        "recuperador (recover_vod_source.py): fuentes VOD "
        "recuperadas para los clips en vivo sin source_id. Vale "
        "para el dry-run y para el modo real.",
    )
    parser.add_argument(
        "--tenant",
        default=None,
        help="tenant del panel (por defecto, PANEL_TENANT)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="JSON de salida (por defecto, "
        "runs/remake-all-<tenant>-<fecha>.json)",
    )
    parser.add_argument(
        "--ids", default="", help="solo estos ids, separados por comas"
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="máximo de clips a re-hacer"
    )
    parser.add_argument(
        "--max-intentos", type=int, default=MAX_INTENTOS_DEFAULT,
        help="intentos de render por clip (default 3)",
    )
    parser.add_argument(
        "--work-dir",
        default=os.path.join("runs", "remake-all-work"),
        help="directorio de trabajo (segmentos y renders)",
    )
    args = parser.parse_args(argv)

    tenant = args.tenant or os.environ.get("PANEL_TENANT") or "sin-tenant"
    out_path = args.out or _default_out_path(tenant)
    want = (
        {int(x) for x in args.ids.split(",") if x.strip()}
        if args.ids.strip()
        else None
    )

    if args.dry_run:
        if not args.from_audit:
            print(
                "ERROR: --dry-run necesita --from-audit PATH",
                file=sys.stderr,
            )
            return 2
        with open(args.from_audit, encoding="utf-8") as f:
            records = json.load(f)
        if not isinstance(records, list):
            print(
                f"ERROR: {args.from_audit}: se esperaba una lista",
                file=sys.stderr,
            )
            return 2
        source_map = {}
        if args.source_map:
            with open(args.source_map, encoding="utf-8") as f:
                source_map = json.load(f)
            source_map = {
                int(k): v for k, v in (source_map or {}).items()
            }
        if want:
            records = [r for r in records if r.get("id") in want]
        if args.limit:
            records = records[: args.limit]
        run_dry_run(records, source_map, out_path, tenant)
        return 0

    try:
        from .panel_settings import load_config_with_panel

        cfg = load_config_with_panel()
    except ConfigError as exc:
        print(f"ERROR: config del validador: {exc}", file=sys.stderr)
        return 2
    bridge = PanelBridge.from_env()
    if bridge is None:
        print(
            "ERROR: sin credenciales del panel en el entorno "
            "(PANEL_TENANT + PANEL_SESSION_TOKEN o PANEL_BRAIN_TOKEN): "
            "corre el paso bootstrap primero.",
            file=sys.stderr,
        )
        return 2
    try:
        clips = load_blocked_clips(bridge)
    except Exception as exc:  # noqa: BLE001 - sin lista no hay
        # batch: falla en voz alta, no con lista vacía.
        print(
            f"ERROR: no se pudo leer la Cola del panel: {exc}",
            file=sys.stderr,
        )
        return 2
    tenant = bridge.tenant or tenant
    if want:
        clips = [c for c in clips if c["id"] in want]
    if args.limit:
        clips = clips[: args.limit]
    if not (cfg.get("watermark") or {}).get("enabled"):
        print(
            "AVISO: sin marca de agua en la config (Ajustes del "
            "panel no leídos o marca apagada): los re-hechos salen "
            "sin marca.",
            flush=True,
        )
    source_map = {}
    if args.source_map:
        with open(args.source_map, encoding="utf-8") as f:
            source_map = json.load(f)
        source_map = {int(k): v for k, v in (source_map or {}).items()}
        print(f"mapa de fuentes VOD: {len(source_map)} entradas",
              flush=True)
    run_batch(
        clips, bridge,
        out_path=out_path,
        work_dir=args.work_dir,
        tenant=tenant,
        cfg=cfg,
        max_intentos=args.max_intentos,
        source_map=source_map,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
