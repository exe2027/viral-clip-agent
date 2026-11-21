"""Modo batch del validador final (2026-11-19).

Valida TODOS los clips del panel (Cola + publicados) con el
validador final (``validate_final_clip``) sobre el VÍDEO FINAL
descargado, y deja la lista de trabajo para el batch de re-hacer
a rubius en un JSON: qué pasa, qué falla (y en qué capa) y qué
acción toca. El batch NO re-renderiza ni re-hace nada: solo
clasifica. El batch de re-hacer lee ese JSON después.

Acciones (por clip):
- ``re-render``: falla y está en Cola (no publicado). Se re-hace
  con la plantilla que toca y se sube el vídeo corregido.
- ``re-make-replace``: falla y está PUBLICADO. Se re-hace y se
  REEMPLAZA la copia publicada. TikTok NO tiene API de borrado:
  esa copia la borra el usuario desde la app (queda en la nota
  del JSON, nunca se inventa un borrado).
- ``avisar-humano``: no hay vídeo que validar (sin MP4 en el
  panel, descarga fallida, o el validador lanzó) o el clip no se
  puede re-hacer. El motivo visible dice qué falta; el batch no
  decide por el humano. También es la acción a la que el batch
  de re-hacer promueve un clip cuando su re-render falla.
- ``reintentar``: el veredicto quedó pendiente (Gemini no
  disponible). No es un fallo del clip: se vuelve a validar
  después, no se re-renderiza.
- ``nada``: pasa, o la validación está desactivada.

Descarga del vídeo: por el panel, ``GET /api/clips/:id/video``
(con la sesión del bootstrap, la misma que usa el cerebro en
vivo). NO hay ``PanelBridge.download_video`` y NO se inventa:
el puente no descarga binario (su ``_request`` espera JSON).
La vía R2 directa de la auditoría no sirve en Actions: no hay
secreto de Cloudflare en el repo (``gh api``: total_count 0) y
el diseño genérico no mete secretos por tenant en GitHub. El
panel sirve el MP4 desde el mismo bucket privado, así que la
descarga por sesión es el mismo vídeo sin secreto nuevo.

Plantilla: la del registro del clip si la trae; si no, el
default de config.yaml (rubius, decisión del usuario
2026-11-19). El panel aún NO tiene el campo ``plantilla`` por
clip: el JSON lo deja anotado (``plantilla_campo_en_panel``) y
todos los clips se validan contra el default hasta que el
campo exista. Un nombre de plantilla desconocido falla en voz
alta (``ConfigError``), nunca se valida contra otra en
silencio.

SRT: el validador final va CON el SRT. El MP4 se transcribe
(modelo tiny) y el SRT generado se pasa al validador, para que
el gate de plantilla mida la capa de subtítulos con los
tiempos reales de la voz. Sin voz o si la transcripción falla,
se valida sin SRT y la capa queda "sin medida" (débil). Antes
se validaba siempre SIN SRT y la capa quedaba sin medir: un
clip con subtítulos de línea completa (no rubius) pasaba la
capa de subtítulos sin que nadie la midiera.

Sin SRT: el panel no guarda el SRT del clip, así que el batch
valida sin él. La puerta de plantilla lo registra como débil
(``template_gate``: sin SRT la presencia de subtítulos no se
puede verificar) y el resto de chequeos sigue. No se inventa
ningún subtítulo.

Salida: el batch NUNCA falla por un clip (un fallo de clip es
un dato: queda en el JSON con outcome ``error`` y el batch
sigue). Solo falla en voz alta (exit 2) por infraestructura:
sin config del validador, sin panel, sin sesión, o si la
validación está apagada y se pidió validar (eso es un error de
operación, no un veredicto).

Invocación:
  python -m clipagent.validate_all --from-panel
  python -m clipagent.validate_all --from-panel --ids 155,156
  python -m clipagent.validate_all --from-panel --limit 20
  python -m clipagent.validate_all --dry-run --from-audit \\
      runs/face-audit-2026-11-19/clips.json --out resultado.json
El modo --dry-run NO descarga ni valida: clasifica el veredicto
ya auditado de clips.json (cara/formato/basura) con las mismas
reglas de acción. Sirve para sacar la lista de trabajo sin
gastar llamadas a Gemini.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

from .final_review import validate_final_clip
from .panel_bridge import DEFAULT_BASE_URL, PanelBridge

# Acciones posibles (ver docstring). "nada" = pasa o desactivada.
ACCION_NADA = "nada"
ACCION_RERENDER = "re-render"
ACCION_REMAKE = "re-make-replace"
ACCION_HUMANO = "avisar-humano"
ACCION_REINTENTAR = "reintentar"

NOTA_TIKTOK = (
    "TikTok no tiene API de borrado: la copia publicada en "
    "TikTok la borra el usuario desde la app."
)


class ValidateAllError(Exception):
    """Fallo de infraestructura del batch (no de un clip)."""


class VideoMissingError(ValidateAllError):
    """El clip no tiene MP4 que validar (404 del panel)."""


def _base_result(clip, outcome, capas, accion, motivo, extra=None):
    rec = {
        "id": clip.get("id"),
        "title": clip.get("title") or "",
        "streamer": clip.get("streamer"),
        "status": clip.get("status") or "en_carpeta",
        "plantilla": clip.get("plantilla"),
        "outcome": outcome,
        "capas": list(capas),
        "accion": accion,
        "motivo": motivo or "",
    }
    if extra:
        rec.update(extra)
    return rec


def _accion_por_estado(status):
    """re-make-replace si está publicado, re-render si está en Cola."""
    if (status or "en_carpeta") == "publicado":
        return ACCION_REMAKE
    return ACCION_RERENDER


def classify_validation(clip, result):
    """Clasifica el veredicto de ``validate_final_clip``.

    ``clip`` es el registro normalizado (id, title, status,
    plantilla). ``result`` es el dict del validador (pasa,
    hard_pasa, needs_rerender, hard_failures, decision, motivo).
    La duda nunca pasa: un ``pasa`` False es falla aunque la
    capa que dude sea solo Gemini.
    """
    result = result or {}
    pasa = result.get("pasa")
    decision = result.get("decision") or ""
    hard = list(result.get("hard_failures") or [])
    motivo = (
        result.get("motivo") or result.get("rerender_reason") or ""
    ).strip()
    if pasa is True:
        return _base_result(clip, "pasa", [], ACCION_NADA, "")
    if pasa is None:
        return _base_result(
            clip,
            "desactivada",
            [],
            ACCION_NADA,
            motivo or "validación final desactivada",
        )
    if decision == "pendiente_revision":
        return _base_result(
            clip,
            "pendiente",
            [],
            ACCION_REINTENTAR,
            motivo or "Gemini no disponible (pendiente de revisión)",
        )
    capas = hard if hard else ["gemini"]
    if not motivo:
        motivo = "puerta dura: " + ", ".join(capas)
    return _base_result(
        clip, "falla", capas, _accion_por_estado(clip.get("status")), motivo
    )


def classify_audit_record(rec):
    """Clasifica un registro de la auditoría manual (clips.json).

    Pasa solo si cara + formato + basura están limpios. La duda
    (``dudoso``) es falla: el validador nunca pasa con duda. Sin
    vídeo o con error de descarga es outcome ``error`` (no hay
    nada que validar; no se inventa un veredicto).
    """
    rec = rec or {}
    cara = rec.get("cara")
    formato = rec.get("formato")
    basura = rec.get("basura")
    extra = {
        "cara": cara,
        "formato": formato,
        "basura": basura,
        "formato_problemas": list(rec.get("formato_problemas") or []),
    }
    clip = {
        "id": rec.get("id"),
        "title": rec.get("title") or "",
        "streamer": rec.get("streamer"),
        "status": rec.get("status") or "en_carpeta",
        "plantilla": rec.get("plantilla"),
    }
    if cara in ("sin_video", "error") or formato in ("sin_video", "error"):
        return _base_result(
            clip,
            "error",
            ["sin_video"],
            ACCION_HUMANO,
            rec.get("error") or "sin vídeo en el panel para validar",
            extra,
        )
    if cara == "con_cara" and formato == "correcto" and basura == "no":
        return _base_result(clip, "pasa", [], ACCION_NADA, "", extra)
    capas = []
    if cara in ("sin_cara", "dudoso"):
        capas.append("cara")
    if formato == "mal":
        capas.append("plantilla")
    if basura in ("sí", "dudoso"):
        capas.append("basura")
    motivo = (
        "; ".join(rec.get("formato_problemas") or [])
        or "falla de cara/formato según la auditoría"
    )
    return _base_result(
        clip,
        "falla",
        capas,
        _accion_por_estado(clip.get("status")),
        motivo,
        extra,
    )


def panel_base_url():
    """URL del Worker: PANEL_URL, PANEL_BASE_URL o el default."""
    return (
        os.environ.get("PANEL_URL")
        or os.environ.get("PANEL_BASE_URL")
        or DEFAULT_BASE_URL
    ).rstrip("/")


def panel_session_token():
    """Sesión del bootstrap (PANEL_SESSION_TOKEN).

    La escribe ``python -m clipagent.panel_credentials`` en
    $GITHUB_ENV. Sin ella no hay descarga: falla en voz alta.
    """
    token = os.environ.get("PANEL_SESSION_TOKEN") or ""
    if not token.strip():
        raise ValidateAllError(
            "sin PANEL_SESSION_TOKEN: corre el paso bootstrap "
            "(python -m clipagent.panel_credentials) antes del "
            "batch; sin sesión no se puede descargar el vídeo"
        )
    return token.strip()


def download_clip_video(
    clip_id, dest_path, base_url=None, session_token=None, timeout=300
):
    """Descarga el MP4 final por el panel (GET /api/clips/:id/video).

    El panel sirve el objeto de R2 con la sesión; el bucket es
    privado y la key lleva un componente aleatorio, así que la
    única vía sin secreto nuevo es esta. 404 = sin MP4
    (VideoMissingError). Cualquier otro fallo HTTP/ red es
    ValidateAllError (infraestructura, no veredicto del clip).
    """
    base = (base_url or panel_base_url()).rstrip("/")
    token = session_token or panel_session_token()
    url = f"{base}/api/clips/{clip_id}/video"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "clipagent-validate-all",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise ValidateAllError(
                    f"GET /api/clips/{clip_id}/video -> HTTP {resp.status}"
                )
            ctype = resp.headers.get("Content-Type") or ""
            if ctype and not ctype.startswith("video/"):
                raise ValidateAllError(
                    f"GET /api/clips/{clip_id}/video -> "
                    f"Content-Type {ctype!r} (no es vídeo)"
                )
            total = 0
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
            if total == 0:
                raise VideoMissingError(
                    f"clip {clip_id}: el panel devolvió 0 bytes"
                )
            return dest_path
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise VideoMissingError(
                f"clip {clip_id}: sin MP4 en el panel (HTTP 404)"
            )
        raise ValidateAllError(
            f"GET /api/clips/{clip_id}/video -> HTTP {exc.code}"
        )
    except urllib.error.URLError as exc:
        raise ValidateAllError(f"GET /api/clips/{clip_id}/video -> {exc}")


def load_clips_from_panel(bridge):
    """Cola del tenant normalizada (GET /api/clips).

    Cada clip: id, title, streamer, status, plantilla (None si
    el panel aún no tiene el campo), video_r2_key, language. Si
    la lista no trae la key, se pide el registro completo con
    get_clip; si tampoco la trae, el clip queda sin vídeo y el
    validador lo marca error (no se inventa).
    """
    raw = bridge.list_clips() or []
    clips = []
    for c in raw or []:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        clip = {
            "id": c.get("id"),
            "title": c.get("title") or "",
            "streamer": (
                c.get("streamer")
                or c.get("influencer_name")
                or c.get("influencer")
            ),
            "status": c.get("status") or "en_carpeta",
            "plantilla": c.get("plantilla"),
            "video_r2_key": c.get("video_r2_key") or "",
            "language": c.get("language") or "es",
        }
        if not clip["video_r2_key"]:
            try:
                full = bridge.get_clip(clip["id"]) or {}
                clip["video_r2_key"] = full.get("video_r2_key") or ""
                clip["title"] = clip["title"] or full.get("title") or ""
                clip["status"] = full.get("status") or clip["status"]
                clip["plantilla"] = full.get("plantilla")
            except Exception as exc:  # noqa: BLE001 - queda sin
                # vídeo y el validador lo marca error con el
                # motivo guardado, no en silencio.
                clip["_get_clip_error"] = str(exc)[:200]
        clips.append(clip)
    return clips


def load_clips_from_audit(path):
    """Registros de la auditoría manual (clips.json)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValidateAllError(
            f"{path}: se esperaba una lista de clips (clips.json)"
        )
    return data


def transcribe_clip_to_srt(video_path, work_dir, clip_id, language):
    """Transcribe el MP4 del clip y genera un SRT (una palabra por
    línea) para que el gate de plantilla mida la capa de
    subtítulos con los tiempos reales de la voz.

    Sin SRT, el gate de plantilla marca la capa de subtítulos
    como "sin medida" (pasa débil): un clip con subtítulos de
    línea completa (no rubius, caso scooter 2026-11-21) pasaría
    la capa de subtítulos sin que nadie la midiera. Con el SRT,
    el gate comprueba subtítulos blancos y una-palabra-a-la-vez
    solo en los instantes en que hay subtítulo.

    Devuelve la ruta del SRT, o None si no hay voz (clip sin
    habla: el SRT no aplica y el gate vuelve a "sin medida", que
    es lo correcto para un clip sin voz, regla 2026-11-19) o si
    la transcripción falla (entonces se valida sin SRT: la capa
    queda débil, no fuerte, y el fallo queda en el log).
    """
    from .transcribe import generate_srt, transcribe_audio

    try:
        transcript, _info = transcribe_audio(
            video_path, language=language)
    except Exception as exc:
        print(f"  [validate_all] transcripción falló (clip {clip_id}): {exc}")
        return None
    if not transcript or transcript.get("no_speech"):
        return None
    words = [w for s in transcript.get("segments", [])
             for w in s.get("words", [])]
    if not words:
        return None
    clip_end = max(float(w.get("end", 0)) for w in words)
    srt_path = os.path.join(work_dir, f"clip_{clip_id}.srt")
    generado, _n = generate_srt(transcript, 0, clip_end, srt_path)
    if not generado:
        return None
    return srt_path


def validate_one(
    clip,
    work_dir,
    cfg=None,
    plantilla_override=None,
    download_fn=None,
    validate_fn=None,
    transcribe_fn=None,
):
    """Descarga + valida UN clip; nunca lanza por un clip.

    Devuelve el registro clasificado (ver classify_validation).
    Sin vídeo o con el validador lanzando: outcome ``error`` con
    acción ``avisar-humano`` y el motivo visible.

    La validación va CON el SRT: el MP4 se transcribe (modelo
    tiny) y el SRT generado se pasa al validador, para que el
    gate de plantilla mida la capa de subtítulos con los tiempos
    reales de la voz (antes se validaba SIN SRT y la capa quedaba
    "sin medida"). Sin voz o si la transcripción falla, se valida
    sin SRT (capa débil, como antes).
    """
    download_fn = download_fn or download_clip_video
    validate_fn = validate_fn or validate_final_clip
    plantilla = plantilla_override or clip.get("plantilla")
    base = {
        "id": clip.get("id"),
        "title": clip.get("title") or "",
        "streamer": clip.get("streamer"),
        "status": clip.get("status") or "en_carpeta",
        "plantilla": plantilla,
    }
    dest = os.path.join(work_dir, f"clip_{clip['id']}.mp4")
    try:
        path = download_fn(clip["id"], dest)
    except VideoMissingError as exc:
        motivo = str(exc)
        if clip.get("_get_clip_error"):
            motivo += f" (get_clip: {clip['_get_clip_error']})"
        return _base_result(
            base, "error", ["sin_video"], ACCION_HUMANO, motivo
        )
    except ValidateAllError as exc:
        return _base_result(
            base, "error", ["sin_video"], ACCION_HUMANO, str(exc)
        )
    # Con SRT: transcribe el MP4 y pasa los tiempos reales de la
    # voz al validador, para que la capa de subtítulos se mida
    # (sin SRT queda "sin medida" y un clip no-rubius pasaría).
    srt_path = None
    if transcribe_fn is None:
        transcribe_fn = transcribe_clip_to_srt
    if transcribe_fn is not None:
        try:
            srt_path = transcribe_fn(
                path, work_dir, clip["id"],
                clip.get("language") or "es")
        except Exception as exc:  # noqa: BLE001 - sin SRT se
            # valida igual; la capa de subtítulos queda débil.
            print(f"  [validate_all] sin SRT (clip {clip['id']}): {exc}")
            srt_path = None
    try:
        result = validate_fn(path, base["title"], plantilla=plantilla, cfg=cfg, srt_path=srt_path)
    except Exception as exc:  # noqa: BLE001 - el validador
        # promete no lanzar; si lo hace, es error, no un pasa.
        return _base_result(
            base,
            "error",
            ["sin_video"],
            ACCION_HUMANO,
            f"el validador lanzó: {exc}",
        )
    # La plantilla del registro es la RESUELTA por el validador
    # (default de config si el clip no declaraba ninguna).
    base["plantilla"] = result.get("plantilla") or base["plantilla"]
    return classify_validation(base, result)


def _summary(results):
    outcomes = {
        "pasa": 0,
        "falla": 0,
        "pendiente": 0,
        "desactivada": 0,
        "error": 0,
    }
    actions = {
        ACCION_RERENDER: 0,
        ACCION_REMAKE: 0,
        ACCION_HUMANO: 0,
        ACCION_REINTENTAR: 0,
        ACCION_NADA: 0,
    }
    for r in results:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
        actions[r["accion"]] = actions.get(r["accion"], 0) + 1
    return {
        "total": len(results),
        "pasa": outcomes["pasa"],
        "falla": outcomes["falla"],
        "pendiente": outcomes["pendiente"],
        "desactivada": outcomes["desactivada"],
        "error": outcomes["error"],
        "re_render": actions[ACCION_RERENDER],
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
        f"total={summary['total']} pasa={summary['pasa']} "
        f"falla={summary['falla']} "
        f"pendiente={summary['pendiente']} "
        f"desactivada={summary['desactivada']} "
        f"error={summary['error']}",
        flush=True,
    )
    print(
        f"acciones: re-render={summary['re_render']} "
        f"re-make-replace={summary['re_make_replace']} "
        f"avisar-humano={summary['avisar_humano']} "
        f"reintentar={summary['reintentar']}",
        flush=True,
    )


def run_batch(
    clips,
    *,
    out_path,
    work_dir,
    tenant,
    modo,
    download_fn=None,
    validate_fn=None,
    plantilla_override=None,
    default_template=None,
    transcribe_fn=None,
):
    """Valida la lista y escribe el JSON de trabajo.

    Un clip que falla no tumba el batch: queda registrado y se
    sigue con el siguiente. Devuelve el payload escrito.
    """
    os.makedirs(work_dir, exist_ok=True)
    results = []
    for clip in clips:
        try:
            rec = validate_one(
                clip,
                work_dir,
                plantilla_override=plantilla_override,
                download_fn=download_fn,
                validate_fn=validate_fn,
                transcribe_fn=transcribe_fn,
            )
        except Exception as exc:  # noqa: BLE001 - un clip no
            # tumba el batch; queda como error visible.
            rec = _base_result(
                clip,
                "error",
                ["sin_video"],
                ACCION_HUMANO,
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
        "modo": modo,
        "tenant": tenant,
        "generado_en": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "plantilla_default": default_template,
        "plantilla_campo_en_panel": any(
            bool(c.get("plantilla")) for c in clips
        ),
        "resumen": summary,
        "notas": [
            NOTA_TIKTOK,
            "El batch no re-renderiza ni publica: solo "
            "clasifica. El batch de re-hacer lee este JSON.",
        ],
        "clips": results,
    }
    _write_json(out_path, payload)
    _print_summary(summary)
    print(f"JSON: {out_path}", flush=True)
    return payload


def run_dry_run(records, out_path, tenant):
    """Clasifica la auditoría sin descargar ni validar nada."""
    results = [classify_audit_record(r) for r in records]
    summary = _summary(results)
    payload = {
        "modo": "dry-run",
        "tenant": tenant,
        "generado_en": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "plantilla_default": None,
        "plantilla_campo_en_panel": False,
        "resumen": summary,
        "notas": [
            NOTA_TIKTOK,
            "Dry-run: clasifica el veredicto ya auditado "
            "(receta willyrex de la auditoría manual), sin "
            "descargar vídeos ni llamar a Gemini.",
        ],
        "clips": results,
    }
    _write_json(out_path, payload)
    _print_summary(summary)
    print(f"JSON: {out_path}", flush=True)
    return payload


def _default_out_path(tenant):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return os.path.join("runs", f"validate-all-{tenant}-{stamp}.json")


def main(argv=None):
    """CLI del batch. Exit 0 = batch completo (los fallos de
    clip son datos, no errores); exit 2 = infraestructura."""
    parser = argparse.ArgumentParser(
        description="Valida todos los clips del panel con el "
        "validador final y deja la lista de trabajo "
        "para el batch de re-hacer."
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "--from-panel",
        action="store_true",
        help="lista de clips desde el panel (por defecto)",
    )
    src.add_argument(
        "--from-audit",
        metavar="PATH",
        help="lista de clips desde clips.json de la auditoría manual",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="clasifica el veredicto auditado sin descargar ni validar",
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
        "runs/validate-all-<tenant>-<fecha>.json)",
    )
    parser.add_argument(
        "--ids", default="", help="solo estos ids, separados por comas"
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="máximo de clips a validar"
    )
    parser.add_argument(
        "--plantilla",
        default=None,
        help="plantilla a validar (rdjavi, "
        "willyrex o rubius; por defecto, la "
        "del clip o el default de config.yaml)",
    )
    parser.add_argument(
        "--work-dir",
        default=os.path.join("runs", "validate-all-work"),
        help="directorio de vídeos descargados",
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
                "ERROR: --dry-run necesita --from-audit PATH", file=sys.stderr
            )
            return 2
        records = load_clips_from_audit(args.from_audit)
        if want:
            records = [r for r in records if r.get("id") in want]
        if args.limit:
            records = records[: args.limit]
        run_dry_run(records, out_path, tenant)
        return 0

    if args.from_audit:
        records = load_clips_from_audit(args.from_audit)
        if want:
            records = [r for r in records if r.get("id") in want]
        if args.limit:
            records = records[: args.limit]
        clips = [
            {
                "id": r.get("id"),
                "title": r.get("title") or "",
                "streamer": r.get("streamer"),
                "status": r.get("status") or "en_carpeta",
                "plantilla": r.get("plantilla"),
                "video_r2_key": r.get("video_r2_key") or "",
                "language": r.get("language") or "es",
            }
            for r in records
            if r.get("id")
        ]
        default_template = None
    else:
        bridge = PanelBridge.from_env()
        if bridge is None:
            print(
                "ERROR: sin credenciales del panel en el "
                "entorno (PANEL_TENANT + PANEL_SESSION_TOKEN o "
                "PANEL_BRAIN_TOKEN): corre el paso bootstrap "
                "primero.",
                file=sys.stderr,
            )
            return 2
        try:
            clips = load_clips_from_panel(bridge)
        except Exception as exc:  # noqa: BLE001 - sin lista no
            # hay batch: falla en voz alta, no con lista vacía.
            print(
                f"ERROR: no se pudo leer la Cola del panel: {exc}",
                file=sys.stderr,
            )
            return 2
        tenant = bridge.tenant or tenant
        try:
            from .config import load_validator_config

            default_template = load_validator_config(None).get(
                "default_template"
            )
        except Exception as exc:  # noqa: BLE001 - la config
            # del validador es obligatoria (single-source): sin
            # ella el batch no valida nada.
            print(f"ERROR: config del validador: {exc}", file=sys.stderr)
            return 2
        if want:
            clips = [c for c in clips if c["id"] in want]
        if args.limit:
            clips = clips[: args.limit]

    run_batch(
        clips,
        out_path=out_path,
        work_dir=args.work_dir,
        tenant=tenant,
        modo="panel" if not args.from_audit else "audit",
        plantilla_override=args.plantilla,
        default_template=default_template,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
