"""Decisión de plantilla por segmento (spec inglés correcto v7.1, §4.3).

Una sola función de decisión, compartida por el pre-chequeo (§4.2) y
el rescate (§4.4). Vive en este módulo de forma permanente (la v7
retira el traslado a ``final_review.py``: moverla removería 9 call
sites sin ganancia conductual) y ``final_review.py`` NO la
re-exporta. Devuelve SIEMPRE una 4-tupla
``(plantilla, caja, estado, avatar)``:

- ``("rubius", caja, "firme", False)`` — hay facecam (voto >= 2 o
  rescate); la caja es la de la fuente, en px.
- ``("rubius", None, "ambiguo", avatar)`` — la detección no es
  concluyente; nunca ``sin_webcam`` sobre una señal débil (M5).
  El flag ``avatar`` se calcula igual en esta fila (I-25): un
  voto único + candidato avatar es evidencia contradictoria
  (ambiguo), pero si hay avatar la validación lo sabe.
- ``("rubius", None, "firme", True)`` — candidato avatar sin cara
  humana; la caja la pone el render (§4.3.1), no la decisión.
- ``("rubius_sin_webcam", None, "firme", False)`` — no hay
  facecam (prueba en pasos 1-4) ni avatar.

El orden de filas ES el orden de evaluación: avatar DESPUÉS de
ambiguo (I-25). La fila "caja presente pero inválida (§4.1)" NO
es rama de esta función: si ``detect_vod_facecam`` devuelve caja,
la decisión devuelve rubius con caja siempre; el desenlace
sin_webcam para caja inválida vive en las guardas de
``decide_layout``, no en esta tabla.

La decisión se toma sobre ``(facecam_box, hits)`` CONJUNTAMENTE,
nunca sobre ``hits`` solo: un rescate con éxito devuelve ``hits == 1``
CON caja, y un solo voto sin rescate devuelve ``hits == 1`` SIN caja.
"""

RUBIUS = "rubius"
SIN_WEBCAM = "rubius_sin_webcam"
AMBIGUO = "ambiguo"
FIRME = "firme"


def _a_px_fuente(box_muestra, segment_path, cfg=None):
    """Caja de muestra (160x90) a px del vídeo fuente, o None."""
    if not box_muestra or len(box_muestra) < 4:
        return None
    try:
        from .render import _probe_size

        sw, sh = _probe_size(str(segment_path))
    except Exception:  # noqa: BLE001 - sin tamaño no hay conversión
        return None
    if not sw or not sh:
        return None
    return [int(round(box_muestra[0] * sw / 160)),
            int(round(box_muestra[1] * sh / 90)),
            int(round(box_muestra[2] * sw / 160)),
            int(round(box_muestra[3] * sh / 90))]


def hay_avatar(segment_path, duration, cfg=None):
    """True si hay avatar reactivo en el segmento (paso 4).

    Misma heurística que usa ``decide_layout``: ``_scan_frames``
    anota ``vtuber`` por muestra. Nunca levanta.
    """
    try:
        from .render import _scan_frames

        samples, _corners = _scan_frames(
            str(segment_path), float(duration or 0), 0, cfg or {})
        return any((s or {}).get("vtuber") for s in samples)
    except Exception:  # noqa: BLE001 - sin escaneo no hay avatar
        return False


# Alias privado (nombre histórico): la misma primitiva.
_hay_avatar = hay_avatar


def decide_template_for_segment(segment_path, duration, cfg=None,
                                detect_fn=None, avatar_fn=None):
    """Decide la plantilla de un segmento fuente (spec §4.3).

    ``segment_path``: vídeo fuente ya descargado. ``duration``: segundos.
    ``detect_fn``/``avatar_fn``: inyectables en tests (por defecto, la
    cadena real). Devuelve siempre la 4-tupla
    ``(plantilla, caja, estado, avatar)``. Nunca levanta: ante duda
    devuelve ambiguo (rubius).
    """
    cfg = cfg or {}
    if (not segment_path or not duration or float(duration) <= 0
            or not __import__("os").path.isfile(str(segment_path))):
        # Sin segmento no hay señal: default rubius ante duda (M5).
        return (RUBIUS, None, AMBIGUO, False)
    try:
        from .render import detect_vod_facecam

        detect = detect_fn or detect_vod_facecam
        res = detect(str(segment_path), float(duration), cfg=cfg) or {}
    except Exception:  # noqa: BLE001 - sin detección no hay decisión
        return (RUBIUS, None, AMBIGUO, False)
    box = res.get("facecam_box")
    hits = res.get("hits", 0) or 0
    if box:
        # Paso 1: caja presente (voto >= 2 o rescate interno) -> rubius.
        # La caja de `detect_vod_facecam` viene en coords de muestra
        # (160x90), salvo method "config" (ya en px de la fuente).
        if res.get("method") == "config":
            caja = [int(v) for v in box[:4]]
        else:
            caja = _a_px_fuente(box, segment_path, cfg)
            if caja is None:
                return (RUBIUS, None, AMBIGUO, False)
        return (RUBIUS, caja, FIRME, False)
    # Paso 2: rescates EXTERNOS (YuNet directo -> bordes fijos),
    # en el orden de `find_facecam_box_in_source`.
    try:
        from .yunet_facecam import yunet_facecam_box

        ybox = yunet_facecam_box(str(segment_path), 0, float(duration))
        if ybox:
            return (RUBIUS, [int(v) for v in ybox[:4]], FIRME, False)
    except Exception:  # noqa: BLE001
        pass
    try:
        from .edge_facecam import detect as _edge_detect

        found = _edge_detect(str(segment_path))
        if found and found.get("conf") == "high":
            return (RUBIUS, [int(found["x"]), int(found["y"]),
                             int(found["w"]), int(found["h"])],
                    FIRME, False)
    except Exception:  # noqa: BLE001
        pass
    # Paso 3: ambiguo — una sola muestra con caja, sin rescate.
    # El flag avatar se calcula igual (I-25): la validación lo
    # sabe aunque la decisión no fuerce caja.
    if hits == 1:
        avatar = (avatar_fn() if avatar_fn is not None
                  else hay_avatar(segment_path, duration, cfg))
        return (RUBIUS, None, AMBIGUO, bool(avatar))
    # Paso 4: avatar reactivo -> rubius (pip -> split), nunca sin_webcam.
    avatar = (avatar_fn() if avatar_fn is not None
              else hay_avatar(segment_path, duration, cfg))
    if avatar:
        return (RUBIUS, None, FIRME, True)
    # Paso 5: sin caja, hits 0, sin rescate y sin avatar -> sin_webcam.
    return (SIN_WEBCAM, None, FIRME, False)
