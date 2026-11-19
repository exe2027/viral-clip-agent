#!/usr/bin/env python3
"""
publisher.py — publicación de clips (Bloque 7 del tribunal).

YouTube Shorts por API + draft-first para el resto + auditoría +
cola con topes + cuarentena + lista blanca verificada AL PUBLICAR.

Reglas del tribunal (Bloque 7) que implementa este módulo:
  * YouTube Shorts es la ÚNICA plataforma con publicación directa por
    API (YouTube Data API v3, videos.insert). TikTok, Instagram y
    Facebook son draft-first: se genera el paquete listo para subir
    a mano y NUNCA se publica directo sin auditoría aprobada.
    (2026-11-18: TikTok va por Zernio — ZernioAdapter, presign +
    PUT + POST /v1/posts — con publicación DIRECTA en público:
    pre-chequeo dryRun y draft=False, publishNow True, por la app
    TikTok for Business auditada de Zernio. La auditoría propia
    NO hace falta para esta vía. El adaptador directo de TikTok
    ya no se usa para publicar.)
  * Sin fila en verde en la auditoría, esa cuenta queda en draft-first
    (salvo TikTok por Zernio, ver arriba).
  * La lista blanca se verifica EN EL MOMENTO de publicar, no solo al
    detectar: si el streamer ya no está en la lista, no se publica.
    (2026-11-16: estar en la lista que sube el usuario = permiso ya
    dado; no hay ficha de permiso que registrar.)
  * Rechazos: error de formato -> 1 reintento; copyright/contenido ->
    cuarentena y revisión humana, nunca reintento ciego.
  * Topes: YT 1-2/día, TikTok 2-3/día, IG/FB 1-2/día,
    espaciamiento 3 h (4 h en YouTube), rolling 24 h máx 3 por
    cuenta, calentamiento nuevo (< 14 días o < 20 publicaciones:
    semana 1 y 2 a 1/día). Franjas horarias Chile 12-14 / 18-20 /
    21-23 (reglas de publicación 2026-11-19: la decisión
    2026-11-18 "sin franja" queda revocada; las franjas se
    aplican al planificar el día en plan_daily()).
  * --dry-run es el modo por defecto: sin credenciales no hace nada
    real, solo valida el paquete y reporta qué bloquearía la subida.

Integración con el resto del repo (solo lee, no edita):
  * viral_package.py — el paquete por plataforma (título, caption,
    hashtags, tags, miniatura, subtítulos) sale de manifest.json,
    que auto_clips.py / clips_chat_v2.py ya escriben por clip.
  * jobqueue.py (otro agente lo crea en paralelo) — estados:
    queued -> ready_for_review -> approved -> published / failed.
    Este módulo define su interfaz contra ese diseño: un ítem de
    publicación es un dict con {"clip", "platform", "state", ...} y
    los estados que usa son exactamente esos, más "quarantined"
    (terminal, para copyright/contenido) y "draft" (paquete listo
    para subir manual, sin publicar).
  * streamers.yaml — lista blanca única de streamers (Fase 4.1;
    antes influencers.yaml + allowed_streamers.yaml). REGLA DEL
    USUARIO (2026-11-16): estar en la lista que sube el usuario YA
    es permiso, sin ficha ni contacto previo. Se relee en cada
    publicación: si el streamer ya no está en la lista, la subida
    se bloquea aunque el clip ya estuviera aprobado.
  * accounts.yaml — solo identificadores no secretos; el estado de
    cada cuenta se CALCULA (clipagent/accounts.py, Fase 3.2).

Credenciales: NINGUNA se crea ni se guarda aquí. YouTube usa OAuth
2.0 (client_secrets.json + token.json, ver PUBLISHING.md). Sin esos
archivos, el cliente YouTube solo valida en dry-run.

Uso:
    python publisher.py audit
    python publisher.py dry-run --clip runs/kick-coker-01a1dffd/clips/clip_01_t03h14m50s_s56.5_r14h1_v2.mp4 --platform youtube_shorts
    python publisher.py draft --clip <clip.mp4> --platform tiktok
    python publisher.py publish --clip <clip.mp4> --platform youtube_shorts --live
    python publisher.py status --platform youtube_shorts
    python publisher.py quarantine --clip <clip.mp4> --platform tiktok --reason "copyright"
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    CHILE_TZ = ZoneInfo("America/Santiago")
except Exception:  # pragma: no cover - Python <3.9
    CHILE_TZ = timezone(timedelta(hours=-3))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QUEUE_STATE_FILE = os.path.join(BASE_DIR, "publish_queue.json")
ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.yaml")
# Lista blanca: la fuente canónica es influencers.yaml (Bloques 8+9:
# estado pendiente/contactado/concedido/denegado/revocado; REGLA
# 2026-11-16: estar en la lista que sube el usuario = permiso ya
# dado, solo denegado/revocado excluyen).
# allowed_streamers.yaml es un atajo con el mismo formato simple;
# se lee también, por si el usuario agrega ahí un nombre.
WHITELIST_FILE = os.path.join(BASE_DIR, "influencers.yaml")
WHITELIST_FALLBACK = os.path.join(BASE_DIR, "allowed_streamers.yaml")
DRAFTS_DIR = os.path.join(BASE_DIR, "drafts")

# ---------------------------------------------------------------------------
# Topes (Bloque 7). Se leen de limits.yaml (Fase 2.4); los
# valores de abajo son solo el fallback si falta ese archivo.
# Reglas de publicación 2026-11-19 (reemplazan las de 2026-11-18):
# franjas horarias Chile 12-14 / 18-20 / 21-23 (la decisión
# "sin franja" queda revocada), espaciamiento 3 h (4 h en YT),
# rolling 24 h máx 3, calentamiento < 14 días o < 20 publicaciones.
# Los topes por plataforma son prudencia con cuentas nuevas (ver
# README: "Cuentas nuevas: 1 publicación por día por plataforma"
# la primera semana; estos topes son el techo una vez calentada la
# cuenta). La cuota de YouTube sí es un dato de la plataforma,
# verificado 2026-11-17 en la documentación oficial:
#   videos.insert (subida): 1.600 unidades; cuota diaria por
#   defecto: 10.000 unidades -> máx. ~6 Shorts/día por cuota.
# ---------------------------------------------------------------------------
PLATFORMS = ("youtube_shorts", "tiktok", "instagram_reels", "facebook_reels")

_DEFAULT_LIMITS = {
    "youtube_shorts": {"plan_per_day": 1, "max_per_day": 2,
                       "days_per_week_min": 4, "days_per_week_max": 5,
                       "quota_upload_cost_units": 1,
                       "quota_upload_calls_per_day": 100,
                       "quota_other_daily_units": 10000},
    "tiktok": {"plan_per_day": 2, "max_per_day": 3,
               "days_per_week_min": 5, "days_per_week_max": 6,
               "zernio_per_day": 15, "zernio_pending_drafts_max": 5},
    "instagram_reels": {"plan_per_day": 1, "max_per_day": 2,
                        "days_per_week_min": 4, "days_per_week_max": 5},
    "facebook_reels": {"plan_per_day": 1, "max_per_day": 2,
                       "days_per_week_min": 4, "days_per_week_max": 5},
    "min_spacing_hours": 3,
    "youtube_spacing_hours": 4,
    "min_spacing_minutes": 5,
    "cross_platform_min_minutes": 15,
    "cross_platform_ideal_hours_min": 12,
    "rolling_24h_max": 3,
    "rest_day_per_week": 1,
    "hashtag_max": {"instagram_reels": 5, "default": 5},
    "warmup": {"new_if_days": 14, "new_if_publishes": 20,
               "week1_max_per_day": 1, "week2_max_per_day": 1},
    "new_account_daily": 1,
    "hard_max_min_age_days": 30,
    "pause_hours": 48,
    "retry_backoff_minutes": [5, 15, 45],
    "max_transient_retries": 3,
    "time_slots": {"morning": ["12:00", "14:00"],
                   "afternoon": ["18:00", "20:00"],
                   "night": ["21:00", "23:00"]},
    "slot_jitter_minutes": 10,
}


def load_limits(path=None):
    """Límites de plataforma desde limits.yaml (o el fallback).

    Devuelve un dict con una entrada por plataforma (plan_per_day,
    max_per_day, días activos por semana, y en YouTube la cuota)
    más min_spacing_hours, youtube_spacing_hours, rolling_24h_max,
    warmup, time_slots, hashtag_max, retry_backoff_minutes y
    pause_hours. Si falta el archivo o PyYAML, devuelve los
    valores por defecto del código (reglas 2026-11-19).
    """
    limits = {k: dict(v) if isinstance(v, dict) else
              (list(v) if isinstance(v, list) else v)
              for k, v in _DEFAULT_LIMITS.items()}
    if path is None:
        # limits.yaml vive en la RAÍZ del repo (junto a config.yaml),
        # no en clipagent/: se busca ahí primero y luego junto a este
        # módulo. (Bug 2026-11-18: antes solo se miraba en clipagent/,
        # así que el archivo de la raíz nunca se leía y siempre se
        # usaba el fallback; los valores coincidían, por eso no se
        # notaba, pero los campos nuevos como zernio_per_day se
        # perdían.)
        candidates = [os.path.join(BASE_DIR, "..", "limits.yaml"),
                      os.path.join(BASE_DIR, "limits.yaml")]
        path = next((p for p in candidates if os.path.exists(p)),
                    candidates[0])
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, ImportError):
        return limits
    for platform in PLATFORMS:
        entry = data.get(platform)
        if isinstance(entry, dict):
            limits[platform].update(entry)
    for key in ("min_spacing_hours", "youtube_spacing_hours",
                "min_spacing_minutes", "cross_platform_min_minutes",
                "cross_platform_ideal_hours_min", "rolling_24h_max",
                "rest_day_per_week", "new_account_daily",
                "hard_max_min_age_days", "pause_hours",
                "max_transient_retries", "slot_jitter_minutes"):
        if key in data:
            limits[key] = data[key]
    for key in ("hashtag_max", "warmup", "time_slots",
                "retry_backoff_minutes"):
        if key in data:
            if isinstance(data[key], dict) and isinstance(
                    limits.get(key), dict):
                merged = dict(limits[key])
                merged.update(data[key])
                limits[key] = merged
            else:
                limits[key] = data[key]
    return limits


_LIMITS = load_limits()

DAILY_LIMITS = {
    # plataforma: (plan_día, máximo_día)
    p: (int(_LIMITS[p]["plan_per_day"]), int(_LIMITS[p]["max_per_day"]))
    for p in PLATFORMS
}
DAYS_PER_WEEK = {
    # plataforma: (mín, máx de días activos por semana)
    p: (int(_LIMITS[p].get("days_per_week_min", 4)),
        int(_LIMITS[p].get("days_per_week_max", 5)))
    for p in PLATFORMS
}
MIN_SPACING_HOURS = int(_LIMITS["min_spacing_hours"])
YOUTUBE_SPACING_HOURS = int(_LIMITS["youtube_spacing_hours"])
MIN_SPACING_MINUTES = int(_LIMITS["min_spacing_minutes"])
CROSS_PLATFORM_MIN_MINUTES = int(_LIMITS["cross_platform_min_minutes"])
CROSS_PLATFORM_IDEAL_HOURS_MIN = int(
    _LIMITS["cross_platform_ideal_hours_min"])
ROLLING_24H_MAX = int(_LIMITS["rolling_24h_max"])
REST_DAY_PER_WEEK = int(_LIMITS["rest_day_per_week"])
# Franjas horarias Chile (reglas 2026-11-19; la decisión 2026-11-18
# "sin franja" queda revocada). PUBLISH_WINDOW es el dict de
# franjas {mañana/tarde/noche: (inicio, fin)}; se aplica al
# planificar el día (plan_daily), no como freno duro en
# check_limits (ver check_limits: enforce_window=False por
# defecto, porque la regla 4b las llama "sugeridas").
PUBLISH_WINDOW = {
    slot: tuple(times)
    for slot, times in _LIMITS["time_slots"].items()
}
SLOT_JITTER_MINUTES = int(_LIMITS["slot_jitter_minutes"])
HASHTAG_MAX = dict(_LIMITS["hashtag_max"])
WARMUP = dict(_LIMITS["warmup"])
HARD_MAX_MIN_AGE_DAYS = int(_LIMITS["hard_max_min_age_days"])
PAUSE_HOURS = int(_LIMITS["pause_hours"])
RETRY_BACKOFF_MINUTES = list(_LIMITS["retry_backoff_minutes"])
NEW_ACCOUNT_DAILY = int(_LIMITS["new_account_daily"])

# YouTube Data API v3 — cuota (ver limits.yaml y el historial
# oficial de revisiones, 2026-11-17): videos.insert cuesta 1 unidad
# en su propio bucket "Video Uploads" (por defecto 100 llamadas/día,
# valor conservador); el pool compartido de 10.000 u./día es solo
# para el resto de métodos (lecturas, thumbnails.set 50 u., etc.).
# El 1.600 u./subida era el valor anterior a dic 2025.
YOUTUBE_UPLOAD_COST_UNITS = int(
    _LIMITS["youtube_shorts"]["quota_upload_cost_units"])
YOUTUBE_UPLOAD_CALLS_PER_DAY = int(
    _LIMITS["youtube_shorts"]["quota_upload_calls_per_day"])
YOUTUBE_DAILY_QUOTA_UNITS = int(
    _LIMITS["youtube_shorts"]["quota_other_daily_units"])
YOUTUBE_MAX_UPLOADS_BY_QUOTA = YOUTUBE_UPLOAD_CALLS_PER_DAY  # 100

# Estados de la cola (diseño de jobqueue.py, creado en paralelo):
#   queued -> ready_for_review -> approved -> published / failed
# Este módulo añade dos estados terminales propios:
#   quarantined — rechazo por copyright/contenido, revisión humana.
#   draft       — paquete listo para subir manual (draft-first).
QUEUE_STATES = ("queued", "ready_for_review", "approved",
                "published", "failed", "quarantined", "draft",
                # Fase 6.3: la política retiene o descarta sin publicar.
                "retained", "discarded",
                # 6B.2: clip viejo de prioridad baja, archivado solo.
                "archived")

# ---------------------------------------------------------------------------
# Estado de las cuentas (Fase 3.2): se CALCULA en
# clipagent/accounts.py desde credenciales reales (tokens/ y
# client_secrets.json) y sondas de solo lectura. accounts.yaml solo
# guarda identificadores no secretos (handles, nombre de la Page).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _now_chile():
    return datetime.now(CHILE_TZ)


def _load_json(path, default):
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _probe_video(path):
    """Duración y resolución con ffprobe; None si no se puede medir."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-show_entries", "format=duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30)
        data = json.loads(out.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        return {
            "width": stream.get("width"),
            "height": stream.get("height"),
            "duration": float(data.get("format", {}).get("duration") or 0),
        }
    except Exception:
        return None


def _streamer_from_manifest(manifest_path):
    """Streamer del clip, desde el manifest.json de su run."""
    data = _load_json(manifest_path, {})
    url = data.get("source_url", "") or ""
    # kick.com/<streamer>/videos/... | twitch.tv/videos/<id> (sin streamer)
    if "kick.com/" in url:
        try:
            return url.split("kick.com/")[1].split("/")[0].lower()
        except IndexError:
            return None
    return data.get("streamer") or None


# ---------------------------------------------------------------------------
# Lista blanca — se verifica EN EL MOMENTO de publicar.
# ---------------------------------------------------------------------------
def _parse_whitelist_file(path):
    """Lee un archivo de lista blanca (YAML o parseo mínimo).

    Formatos aceptados:
      * influencers.yaml:  influencers: [{usuario, estado, ...}]
        Solo cuenta estado == "concedido".
      * allowed_streamers.yaml: streamers: [{name, permission, ...}]
    Devuelve {nombre_en_minúsculas: ficha}.
    """
    if not os.path.isfile(path):
        return {}
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except ImportError:
        data = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("- name:"):
                    data.setdefault("streamers", []).append(
                        {"name": line.split(":", 1)[1].strip()})
                if line.startswith("- usuario:"):
                    data.setdefault("influencers", []).append(
                        {"usuario": line.split(":", 1)[1].strip().strip('"')})
    out = {}
    for entry in data.get("streamers") or []:
        name = (entry.get("name") or "").strip().lower()
        if name:
            out[name] = entry
    for entry in data.get("influencers") or []:
        name = (entry.get("usuario") or "").strip().lower()
        # 2026-11-16: estar en la lista = permiso ya dado. Solo
        # `denegado` / `revocado` excluyen (el usuario lo sacó).
        if name and (entry.get("estado") or "").strip().lower() not in (
                "denegado", "revocado"):
            out[name] = entry
    return out


def load_whitelist(path=None):
    """Lista blanca vigente, releída fresca en cada llamada.

    Fuente canónica: streamers.yaml (Fase 4.1, un solo lector en
    clipagent/streamers.py). Si se pasa una ruta explícita se lee
    ese archivo; si no existe streamers.yaml, se suman las listas
    viejas como respaldo. Vacía = lista vacía.
    """
    if path:
        return _parse_whitelist_file(path)
    from .streamers import load_streamers
    merged = {}
    for entry in load_streamers():
        merged[entry["name"]] = {
            "name": entry["name"],
            "platform": entry["platform"],
            "permission": "lista",
            "granted": entry["fecha_permiso"],
            "estado": entry["estado"],
        }
    if not merged:
        merged.update(_parse_whitelist_file(WHITELIST_FALLBACK))
        merged.update(_parse_whitelist_file(WHITELIST_FILE))
    return merged


def check_whitelist(streamer):
    """True solo si el streamer está en la lista blanca AHORA.

    Se llama en el momento de publicar, no al detectar: si el
    streamer ya no está en la lista entre la detección y la subida,
    esto lo bloquea. Sin streamer identificado -> bloqueado.
    """
    if not streamer:
        return False, "sin streamer identificado en el manifiesto"
    allowed = load_whitelist()
    if streamer.lower() in allowed:
        entry = allowed[streamer.lower()]
        return True, f"en lista blanca ({entry.get('permission', '?')}, {entry.get('granted', '?')})"
    return False, f"'{streamer}' NO está en la lista blanca (influencers.yaml / allowed_streamers.yaml vacíos o sin ese nombre)"


# ---------------------------------------------------------------------------
# Auditoría de cuentas (Fase 3.2): el estado se calcula en
# clipagent/accounts.py desde credenciales reales y sondas de solo
# lectura. accounts.yaml ya no se edita a mano para decir que algo
# "está configurado": si no hay token, no está configurado.
# ---------------------------------------------------------------------------
def audit_accounts(probes=None):
    """Checklist pasa/no-pasa por cuenta. Sin fila en verde, draft-first.

    Devuelve {plataforma: {"label", "status": "green"|"amber"|"red",
    "checks": [(nombre, pasa|None, detalle)], "notes",
    "publish_mode": ...}}. Un chequeo en None es "sin verificar"
    (ni pasa ni falla). ``probes`` son resultados de sondas de solo
    lectura (las pasa `clipagent doctor`, ver 3.5).
    """
    from .accounts import compute_state
    return compute_state(probes=probes)


def audit_report_text(audit):
    lines = ["AUDITORÍA DE CUENTAS — " + _now_chile().strftime("%Y-%m-%d %H:%M %Z")]
    for platform in PLATFORMS:
        a = audit[platform]
        icon = {"green": "🟢", "amber": "🟡", "red": "🔴"}[a["status"]]
        lines.append(f"\n{icon} {a['label']} — {a['status'].upper()}")
        for name, ok, detail in a["checks"]:
            lines.append(f"   [{'x' if ok else ' '}] {name} — {detail}")
        lines.append(f"   Modo: {a['publish_mode']}")
        if a.get("notes"):
            lines.append(f"   Nota: {a['notes']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cola de publicación (interfaz compatible con jobqueue.py)
# ---------------------------------------------------------------------------
class PublishQueue:
    """Cola persistente en publish_queue.json.

    Un ítem: {"clip", "platform", "streamer", "state", "history": [...],
    "scheduled_for", "published_at", "platform_id", "quarantine_reason"}.
    Estados: queued -> ready_for_review -> approved -> published/failed,
    más quarantined (copyright/contenido) y draft (paquete manual).
    """

    def __init__(self, path=None):
        self.path = path or QUEUE_STATE_FILE
        self.data = _load_json(self.path, {"items": []})

    def save(self):
        _save_json(self.path, self.data)

    def _find(self, clip, platform):
        for item in self.data["items"]:
            if item["clip"] == clip and item["platform"] == platform:
                return item
        return None

    def enqueue(self, clip, platform, streamer=None, moment_key=None,
                red_flags=None, game=None, vod_id=None):
        """Encola un clip. La política (6.3) necesita estos metadatos
        guardados en el ítem: moment_key (duplicados), red_flags
        (semáforo), game y vod_id."""
        if self._find(clip, platform):
            return self._find(clip, platform)
        item = {"clip": clip, "platform": platform, "streamer": streamer,
                "state": "queued", "history": [
                    {"at": _now_chile().isoformat(), "state": "queued"}],
                "scheduled_for": None, "published_at": None,
                "platform_id": None, "quarantine_reason": None,
                "moment_key": moment_key, "red_flags": red_flags or [],
                "game": game, "vod_id": vod_id}
        self.data["items"].append(item)
        self.save()
        return item

    def set_state(self, item, state, note=None):
        assert state in QUEUE_STATES, f"estado inválido: {state}"
        item["state"] = state
        item["history"].append({"at": _now_chile().isoformat(),
                                "state": state, "note": note})
        self.save()
        return item

    def add_note(self, item, note):
        """Nota en el historial SIN cambiar el estado.

        Para fallos transitorios y procesados pendientes: el ítem
        sigue en su estado (approved/queued) y el siguiente ciclo
        del bucle lo retoma. Sin esto, un 5xx lo sacaría de la cola.
        """
        item["history"].append({"at": _now_chile().isoformat(),
                                "state": item["state"], "note": note})
        self.save()
        return item

    def approve(self, item):
        return self.set_state(item, "approved", "aprobado por el usuario")

    def mark_published(self, item, platform_id):
        item["platform_id"] = platform_id
        item["published_at"] = _now_chile().isoformat()
        return self.set_state(item, "published", f"id={platform_id}")

    def mark_failed(self, item, reason):
        return self.set_state(item, "failed", reason)

    def quarantine(self, item, reason):
        """Rechazo por copyright/contenido: a cuarentena, sin reintento."""
        item["quarantine_reason"] = reason
        return self.set_state(item, "quarantined", reason)

    def published_today(self, platform):
        today = _now_chile().date().isoformat()
        return [i for i in self.data["items"]
                if i["platform"] == platform and i["state"] == "published"
                and (i.get("published_at") or "").startswith(today)]

    def published_in_last_24h(self, platform, now=None):
        """Publicados en las últimas 24 h móviles (rolling, no día
        calendario; regla 2, 2026-11-19). `now` es un datetime con
        zona (por defecto, ahora en Chile)."""
        ref = now or _now_chile()
        out = []
        for i in self.data["items"]:
            if i["platform"] != platform or i["state"] != "published":
                continue
            at = i.get("published_at")
            if not at:
                continue
            try:
                dt = datetime.fromisoformat(at)
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ref.tzinfo)
            if (ref - dt).total_seconds() < 24 * 3600:
                out.append(i)
        return out

    def published_count(self, platform):
        """Total histórico de publicados en esa plataforma."""
        return sum(1 for i in self.data["items"]
                   if i["platform"] == platform
                   and i["state"] == "published")

    def last_published_at(self, platform):
        published = [i for i in self.data["items"]
                     if i["platform"] == platform and i["state"] == "published"
                     and i.get("published_at")]
        if not published:
            return None
        return max(i["published_at"] for i in published)

    def due_items(self, platform=None, states=("approved",)):
        """Ítems en `states` cuya hora programada ya llegó (o sin hora).

        El flujo manual usa solo "approved"; el bucle automático
        (6.3) pasa ("approved", "queued") y la política decide.
        6B.1: la cola NUNCA es FIFO — el orden lo da
        priority.order_queue por plataforma (el SEO cambia por
        plataforma, así que la prioridad también). Los ítems sin
        manifiesto (sin puntaje) quedan al final, en su orden.
        """
        now = _now_chile().isoformat()
        due = [i for i in self.data["items"]
               if i["state"] in states
               and (platform is None or i["platform"] == platform)
               and (not i.get("scheduled_for")
                    or i["scheduled_for"] <= now)]
        if platform is None:
            return due
        scored, no_score = _order_items(due, platform)
        return [s["_item"] for s in scored] + no_score

    def scored_items(self, platform, states=("approved", "queued")):
        """Ítems con su desglose de prioridad (6B.1/6B.6).

        Devuelve la lista de order_queue (cada entrada trae
        ``priority`` con el desglose, ``status``, ``status_reason``,
        ``queue_position``, ``left_behind`` y ``auto_archive``) con
        la clave ``_item`` apuntando al ítem de la cola. Los ítems
        sin manifiesto no se puntúan y no aparecen aquí.
        """
        now = _now_chile().isoformat()
        due = [i for i in self.data["items"]
               if i["state"] in states
               and i["platform"] == platform
               and (not i.get("scheduled_for")
                    or i["scheduled_for"] <= now)]
        scored, _no_score = _order_items(due, platform)
        return scored


def _apply_improved_package(clip_path, platform, best_pkg, best_score):
    """6B.4: escribe el paquete SEO mejorado en el manifiesto.

    Actualiza title_es/desc_es/hashtags_es, el paquete por
    plataforma que lee la subida (viral.platforms) y el score SEO
    guardado. Escritura atómica (tmp + os.replace). Devuelve True
    si se escribió.
    """
    run_dir = os.path.dirname(os.path.dirname(os.path.abspath(clip_path)))
    manifest_path = os.path.join(run_dir, "manifest.json")
    manifest = _load_json(manifest_path, None)
    if not manifest:
        return False
    clip_name = os.path.basename(clip_path)
    target = None
    for clip in manifest.get("clips", []):
        listed = os.path.basename(clip.get("file", ""))
        base = listed.rsplit(".mp4", 1)[0]
        if clip_name == listed or clip_name.startswith(base):
            target = clip
            break
    if target is None:
        return False
    if best_pkg.get("title"):
        target["title_es"] = best_pkg["title"]
    if best_pkg.get("description"):
        target["desc_es"] = best_pkg["description"]
    if best_pkg.get("hashtags"):
        target["hashtags_es"] = " ".join(best_pkg["hashtags"])
    if best_score is not None:
        target.setdefault("seo_scores", {}).setdefault(platform, {})["score"] = best_score
    platforms = target.setdefault("viral", {}).setdefault("platforms", {})
    entry = platforms.setdefault(platform, {})
    if best_pkg.get("title"):
        entry["title"] = best_pkg["title"]
    if best_pkg.get("description"):
        entry["caption"] = best_pkg["description"]
    tags = best_pkg.get("tags") or best_pkg.get("hashtags") or []
    if tags:
        entry["tags"] = list(tags)[:15]
    tmp = manifest_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(tmp, manifest_path)
    return True


def _item_age_days(item):
    """Días desde que el ítem entró en la cola (0 si no se sabe)."""
    from datetime import datetime
    history = item.get("history") or [{}]
    created = history[0].get("at", "")
    if not created:
        return 0
    try:
        dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    except ValueError:
        return 0
    now = _now_chile()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=now.tzinfo)
    return max(0, (now - dt).days)


def _clip_scores(item, platform):
    """Puntajes de un ítem para order_queue, o None sin manifiesto.

    Lee viralidad, SEO de la plataforma, múltiplo de chat, vod_id,
    inicio y fecha de creación del ítem (para la penalización por
    edad), y si hay override registrado en el manifiesto.
    """
    clip, manifest = load_clip_package(item["clip"])
    if clip is None:
        return None
    seo_entry = (clip.get("seo_scores") or {}).get(platform) or {}
    seo = seo_entry.get("score") if isinstance(seo_entry, dict) else seo_entry
    overrides = {o.get("platform")
                 for o in (manifest or {}).get("seo_overrides", [])}
    history = item.get("history") or [{}]
    return {
        "virality": (clip.get("viral_score") or {}).get("total", 0),
        "seo": seo if seo is not None else 0,
        "chat_multiple": (clip.get("viral") or {}).get("viral_multiple") or 1.0,
        "vod_id": item.get("vod_id") or (manifest or {}).get("vod_id") or "",
        "start_sec": clip.get("start", 0),
        "created_at": history[0].get("at", ""),
        "uploaded": item["state"] == "published",
        "seo_override": platform in overrides,
    }


def _order_items(items, platform):
    """(scored, no_score): ítems ordenados por priority.order_queue.

    `scored` es la salida de order_queue con ``_item`` apuntando al
    ítem original; `no_score` son los ítems sin manifiesto, en su
    orden original, que quedan al final de la cola.
    """
    from .priority import order_queue
    clips, no_score = [], []
    for item in items:
        scores = _clip_scores(item, platform)
        if scores is None:
            no_score.append(item)
            continue
        clips.append(dict(scores, _item=item))
    return order_queue(clips, platform), no_score


def spacing_hours_for(platform):
    """Espaciamiento mínimo entre subidas de la misma cuenta:
    3 h, 4 h en YouTube Shorts (regla 4, 2026-11-19)."""
    return YOUTUBE_SPACING_HOURS if platform == "youtube_shorts" \
        else MIN_SPACING_HOURS


def hashtag_limit(platform):
    """Máximo de hashtags: 5 en Instagram, 3-5 en el resto
    (techo 5 en todos; regla 5, 2026-11-19)."""
    return int(HASHTAG_MAX.get(platform, HASHTAG_MAX.get("default", 5)))


def check_limits(queue, platform, now=None, warmup_stage=None,
                 rest_day=False, enforce_window=False):
    """Topes antes de publicar: (ok, motivo).

    Reglas de publicación 2026-11-19:
    - Tope diario (plan) y techo (máx) por plataforma; YT además
      el bucket de videos.insert (100 llamadas/día).
    - Rolling 24 h: nunca más de ROLLING_24H_MAX (3) por cuenta
      en 24 h móviles, no día calendario.
    - Espaciamiento mínimo 3 h desde la última subida (4 h en
      YouTube), con mínimo absoluto de 5 min.
    - Calentamiento: si warmup_stage es "week1"/"week2", máx
      1/día (el llamador lo calcula con accounts.warmup_stage).
    - Día libre: si rest_day, la cuenta no publica hoy.
    - Franja: las franjas 12-14 / 18-20 / 21-23 se aplican al
      PLANIFICAR el día (plan_daily), no aquí: check_limits no
      bloquea por hora salvo enforce_window=True, porque la
      regla 4b las llama "sugeridas (configurables)" y el bucle
      publica los ítems vencidos cuando toca.
    """
    now = now or _now_chile()
    if rest_day:
        return False, f"día libre de {platform} esta semana (rotación)"
    plan, maximum = DAILY_LIMITS[platform]
    if warmup_stage in ("week1", "week2"):
        cap = NEW_ACCOUNT_DAILY
        today = queue.published_today(platform)
        if len(today) >= cap:
            return False, (f"calentamiento {warmup_stage}: "
                           f"{len(today)}/{cap} hoy en {platform} "
                           f"(máx 1/día hasta la semana 3)")
    last_24h = queue.published_in_last_24h(platform, now=now)
    if len(last_24h) >= ROLLING_24H_MAX:
        return False, (f"rolling 24 h: {len(last_24h)}/"
                       f"{ROLLING_24H_MAX} en {platform} "
                       f"(ventana móvil, no día calendario)")
    today = queue.published_today(platform)
    if len(today) >= maximum:
        return False, f"tope diario alcanzado: {len(today)}/{maximum} en {platform}"
    if len(today) >= plan:
        return False, (f"plan del día cubierto: {len(today)}/{plan} en {platform} "
                       f"(techo {maximum}, subir más requiere decisión explícita)")
    last = queue.last_published_at(platform)
    if last:
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=now.tzinfo)
        gap_min = (now - last_dt).total_seconds() / 60
        spacing_min = spacing_hours_for(platform) * 60
        if gap_min < MIN_SPACING_MINUTES:
            return False, (f"espaciamiento: última subida hace "
                           f"{gap_min:.0f} min, mínimo absoluto "
                           f"{MIN_SPACING_MINUTES} min")
        if gap_min < spacing_min:
            return False, (f"espaciamiento: última subida hace "
                           f"{gap_min / 60:.1f} h, mínimo "
                           f"{spacing_hours_for(platform)} h en "
                           f"{platform}")
    if enforce_window and not _in_time_slot(now):
        return False, ("fuera de franja: publica solo en "
                       "12:00-14:00, 18:00-20:00 o 21:00-23:00 "
                       "(hora Chile)")
    return True, f"dentro de topes ({len(today)}/{plan} hoy, plan {plan}/día)"


def _in_time_slot(moment):
    """True si `moment` (hora Chile) cae en alguna franja."""
    hhmm = moment.strftime("%H:%M")
    for start, end in PUBLISH_WINDOW.values():
        if start <= hhmm < end:
            return True
    return False


# ---------------------------------------------------------------------------
# Errores y reintentos (regla 8, 2026-11-19)
# ---------------------------------------------------------------------------
_TRANSIENT_WORDS = ("429", "500", "502", "503", "504", "timeout",
                    "timed out", "connection", "red", "network",
                    "rate limit", "rate_limit", "temporal")
_SPAM_LIMIT_WORDS = ("spam", "too_many", "too many posts",
                     "limit exceeded", "limite de publicaciones",
                     "límite de publicaciones",
                     "quotaexceeded", "quota exceeded",
                     "spam_risk_too_many_posts")


def classify_publish_error(error):
    """Clasifica un error de publicación (regla 8).

    Devuelve "transient" (red, 429, 5xx: reintento con backoff),
    "spam_limit" (spam o límite: detener la cuenta, reprogramar
    al día siguiente, SIN reintentar el mismo día) u "other".
    El spam se comprueba primero: un 429 por exceso de
    publicaciones es límite, no temporal.
    """
    text = (error or "").lower()
    if any(w in text for w in _SPAM_LIMIT_WORDS):
        return "spam_limit"
    if any(w in text for w in _TRANSIENT_WORDS):
        return "transient"
    return "other"


def next_retry(error, attempts_made):
    """Qué hacer tras un fallo (regla 8).

    `attempts_made` = reintentos ya usados. Devuelve:
      {"action": "retry", "wait_minutes": N} — temporal, espera
        creciente 5, 15, 45 min, máx 3 intentos;
      {"action": "stop_today"} — spam/límite: detener esa cuenta
        y reprogramar al día siguiente, sin reintentar hoy;
      {"action": "give_up"} — temporal con los 3 intentos
        agotados, u otro error no reintentable.
    Nunca esquivar un límite cambiando de cuenta, IP o método.
    """
    kind = classify_publish_error(error)
    if kind == "spam_limit":
        return {"action": "stop_today",
                "reason": "spam/límite: detener la cuenta y "
                          "reprogramar al día siguiente, sin "
                          "reintentar hoy"}
    if kind == "transient":
        if attempts_made < len(RETRY_BACKOFF_MINUTES):
            return {"action": "retry",
                    "wait_minutes": RETRY_BACKOFF_MINUTES[attempts_made],
                    "reason": f"temporal: reintento "
                              f"{attempts_made + 1}/"
                              f"{len(RETRY_BACKOFF_MINUTES)} en "
                              f"{RETRY_BACKOFF_MINUTES[attempts_made]} min"}
        return {"action": "give_up",
                "reason": "temporal: 3 intentos agotados, a revisión"}
    return {"action": "give_up",
            "reason": f"error no reintentable: {error}"}


# ---------------------------------------------------------------------------
# Anti-spam y contenido (regla 5, 2026-11-19)
# ---------------------------------------------------------------------------
def has_own_value(clip):
    """True si el clip aporta algo propio (subtítulos, zoom,
    edición, hook). Un corte crudo de Twitch/Kick sin ningún
    cambio NO se publica y se marca "requiere_edicion".

    El clip declara su aporte con `aporte_propio: true`, con una
    lista `edits` no vacía (p. ej. ["subtitulos", "zoom"]), o con
    las banderas `subtitles_burned` / `zoom` / `hook`. Sin
    evidencia de aporte, False (no se asume).
    """
    if clip.get("aporte_propio"):
        return True
    edits = clip.get("edits") or []
    if edits:
        return True
    return bool(clip.get("subtitles_burned") or clip.get("zoom")
                or clip.get("hook"))


def requires_editing(clip):
    """Alias legible: True si el clip es un corte crudo sin
    aporte propio (regla 5)."""
    return not has_own_value(clip)


def _normalize_text(text):
    return " ".join((text or "").split()).strip().lower()


# ---------------------------------------------------------------------------
# Plan diario (regla 9, 2026-11-19)
# ---------------------------------------------------------------------------
def _slot_minutes(slot):
    """(inicio, fin) de una franja en minutos desde medianoche."""
    start, end = PUBLISH_WINDOW[slot]
    def _to_min(hhmm):
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    return _to_min(start), _to_min(end)


def _jitter_minutes(clip_id, day_iso, span=10):
    """Variación determinista de ±span min para la hora exacta
    (nunca la misma hora exacta todos los días; regla 4b).

    Depende del clip y del día, así que la misma cuenta no repite
    la hora exacta día tras día.
    """
    import hashlib
    digest = hashlib.md5(f"{day_iso}|{clip_id}".encode("utf-8")).hexdigest()
    return (int(digest[:8], 16) % (2 * span + 1)) - span


def _slot_times_for_count(count, platform):
    """Franjas a usar según cuántos vídeos van ese día en la
    cuenta: 1 -> noche; 2 -> mediodía + noche; 3 -> mediodía +
    tarde + noche (regla 4b). En YouTube nunca tarde+noche
    seguidas (3 h < 4 h de espaciamiento): con 2, mediodía+noche.
    """
    if count <= 1:
        return ["night"]
    if count == 2:
        return ["morning", "night"]
    return ["morning", "afternoon", "night"]


def _time_in_slot(slot, clip_id, day_iso):
    """Hora "HH:MM" dentro de una franja, con jitter ±10 min."""
    start, end = _slot_minutes(slot)
    mid = (start + end) // 2
    moment = mid + _jitter_minutes(clip_id, day_iso, SLOT_JITTER_MINUTES)
    moment = max(start, min(end - 1, moment))
    return f"{moment // 60:02d}:{moment % 60:02d}"


def _rest_weekday(platform, day_iso):
    """Día libre semanal de una cuenta (0=lunes..6=domingo).

    Determinista por plataforma y semana ISO: rota cada semana,
    así cada cuenta tiene 1 día libre por semana (regla 7).
    """
    from datetime import date as _date
    year, week, _wd = _date.fromisoformat(day_iso).isocalendar()
    seed = sum(ord(c) for c in platform) + year * 53 + week
    return seed % 7


def _is_rest_day(platform, day_iso):
    from datetime import date as _date
    return _date.fromisoformat(day_iso).weekday() == _rest_weekday(
        platform, day_iso)


def _clip_platform(clip):
    return clip.get("plataforma") or clip.get("platform")


def _clip_viral(clip):
    score = clip.get("viral_score")
    if isinstance(score, dict):
        return score.get("total", 0) or 0
    return score or 0


def _clip_texts(clip, platform):
    """(título, descripción, hashtags) del clip para una
    plataforma: versión por plataforma si la trae, si no la
    general. Cada plataforma recibe su PROPIA versión (regla 5).
    """
    versions = clip.get("versions") or {}
    entry = versions.get(platform) or {}
    title = (entry.get("titulo") or entry.get("title")
             or clip.get("titulo") or clip.get("title") or "")
    desc = (entry.get("descripcion") or entry.get("description")
            or clip.get("descripcion") or clip.get("description") or "")
    tags = (entry.get("hashtags") or clip.get("hashtags") or [])
    if isinstance(tags, str):
        tags = tags.split()
    return title, desc, [str(t) for t in tags]


def _verify_plan(plan, limits=None):
    """Verifica el plan contra las secciones 2, 3 y 4 (regla 9).

    Devuelve [violaciones]: cada una es (clip_id, plataforma,
    motivo). Un plan sin violaciones devuelve [].
    """
    limits = limits or _LIMITS
    violations = []
    per_account = {}
    seen_texts = set()
    seen_clip_platform = set()
    clip_times = {}
    for pub in plan["publicaciones"]:
        clip_id = pub["clip_id"]
        platform = pub["plataforma"]
        per_account.setdefault(platform, []).append(pub)
        plan_n, max_n = DAILY_LIMITS[platform]
        if len(per_account[platform]) > max_n:
            violations.append((clip_id, platform,
                               f"supera el máximo duro {max_n}/día"))
        if (clip_id, platform) in seen_clip_platform:
            violations.append((clip_id, platform,
                               "mismo clip repetido en la misma cuenta"))
        seen_clip_platform.add((clip_id, platform))
        key = _normalize_text(pub["titulo"] + "\n" + pub["descripcion"])
        if key and key in seen_texts:
            violations.append((clip_id, platform,
                               "mismo texto exacto que otra publicación del día"))
        seen_texts.add(key)
        if len(pub["hashtags"]) > hashtag_limit(platform):
            violations.append((clip_id, platform,
                               f"{len(pub['hashtags'])} hashtags, "
                               f"máx {hashtag_limit(platform)}"))
        hh, mm = (int(x) for x in pub["hora_local"].split(":"))
        moment = hh * 60 + mm
        if not any(s <= moment < e for s, e in
                   (_slot_minutes(slot) for slot in PUBLISH_WINDOW)):
            violations.append((clip_id, platform,
                               f"hora {pub['hora_local']} fuera de franja"))
        prev = clip_times.get(clip_id)
        if prev is not None:
            gap = abs(moment - prev)
            if gap < CROSS_PLATFORM_MIN_MINUTES:
                violations.append((clip_id, platform,
                                   f"mismo clip en otra plataforma a "
                                   f"solo {gap} min (mín "
                                   f"{CROSS_PLATFORM_MIN_MINUTES} min)"))
        clip_times.setdefault(clip_id, moment)
    for platform, pubs in per_account.items():
        ordered_pubs = sorted(
            pubs, key=lambda p: (int(p["hora_local"][:2]) * 60
                                 + int(p["hora_local"][3:])))
        spacing = spacing_hours_for(platform) * 60
        for prev_pub, pub in zip(ordered_pubs, ordered_pubs[1:]):
            prev_min = (int(prev_pub["hora_local"][:2]) * 60
                        + int(prev_pub["hora_local"][3:]))
            cur_min = (int(pub["hora_local"][:2]) * 60
                       + int(pub["hora_local"][3:]))
            if cur_min - prev_min < MIN_SPACING_MINUTES:
                violations.append((pub["clip_id"], platform,
                                   "dos vídeos a menos de 5 min"))
            elif cur_min - prev_min < spacing:
                violations.append((pub["clip_id"], platform,
                                   f"espaciamiento {cur_min - prev_min} "
                                   f"min < {spacing} min"))
    return violations


def plan_daily(clips, queue=None, day=None, accounts_info=None,
               limits=None, platform_order=None):
    """Plan diario de publicación (regla 9, 2026-11-19).

    `clips`: lista de dicts con clip_id, viral_score (número o
    {"total": n}), titulo/descripcion/hashtags (o versions por
    plataforma), y opcionalmente plataforma (si no la trae, se
    reparte entre plataformas). `accounts_info`: por plataforma,
    {"age_days", "published_count", "restricted", "reach_trend"}
    para decidir calentamiento y subida al máximo duro (regla 6);
    sin él, la edad sale de accounts.yaml y el conteo de la cola.
    `day`: fecha "YYYY-MM-DD" (hoy en Chile por defecto).

    Devuelve {"fecha", "publicaciones": [{clip_id, plataforma,
    cuenta, hora_local, titulo, descripcion, hashtags, motivo}],
    "en_cola": [clip_id], "alertas": [...]}. Antes de devolverlo
    se verifica contra las secciones 2, 3 y 4: lo que no cumple
    va a la cola. NUNCA publica nada: solo planifica.
    """
    from datetime import date as _date
    limits = limits or _LIMITS
    day_iso = day or _now_chile().date().isoformat()
    queue = queue or PublishQueue()
    accounts_info = accounts_info or {}
    order = list(platform_order or PLATFORMS)
    alerts = []
    queued = []
    scheduled = []

    # Capacidad por cuenta: plan normal, o 1/día en calentamiento,
    # o máximo duro solo si cumple la regla 6 (cuenta > 30 días
    # sin restricción + cola con viral alto + alcance en subida).
    capacity = {}
    for platform in order:
        info = accounts_info.get(platform) or {}
        stage = info.get("warmup_stage")
        if stage is None:
            try:
                from .accounts import warmup_stage as _ws
                stage = _ws(platform, queue=queue,
                            published_count=info.get("published_count"))
            except Exception:
                stage = "normal"
        if stage in ("week1", "week2"):
            capacity[platform] = NEW_ACCOUNT_DAILY
            alerts.append(f"{platform}: en calentamiento {stage} "
                          f"(máx 1/día)")
            continue
        plan_n, max_n = DAILY_LIMITS[platform]
        age = info.get("age_days")
        if age is None:
            try:
                from .accounts import account_age_days as _aad
                age = _aad(platform)
            except Exception:
                age = None
        eligible_hard = (
            age is not None and age > HARD_MAX_MIN_AGE_DAYS
            and not info.get("restricted", False)
            and info.get("reach_trend") in ("up", "stable"))
        if eligible_hard:
            capacity[platform] = max_n
            alerts.append(f"{platform}: ritmo máximo {max_n}/día "
                          f"(cuenta > 30 días sin restricción, "
                          f"alcance en subida)")
        else:
            capacity[platform] = plan_n
        if _is_rest_day(platform, day_iso):
            capacity[platform] = 0
            alerts.append(f"{platform}: día libre esta semana "
                          f"(rotación, 1/semana)")

    # Cola ordenada por puntaje viral (regla 7), repartida entre
    # plataformas: no todo a una sola.
    ordered = sorted(clips, key=_clip_viral, reverse=True)
    per_platform = {p: [] for p in order}
    used_texts = set()
    clip_first_time = {}
    for clip in ordered:
        clip_id = clip.get("clip_id") or clip.get("clip") or ""
        if requires_editing(clip):
            queued.append(clip_id)
            alerts.append(f"{clip_id}: requiere_edicion (corte "
                          f"crudo sin subtítulos/zoom/edición/hook, "
                          f"no se publica)")
            continue
        wanted = _clip_platform(clip)
        candidates = [wanted] if wanted in order else order
        placed = False
        for platform in candidates:
            if len(per_platform[platform]) >= capacity[platform]:
                continue
            if any(c.get("clip_id") == clip_id
                   for c in per_platform[platform]):
                continue
            if queue._find(clip_id, platform) is not None and \
                    queue._find(clip_id, platform)["state"] == "published":
                continue
            title, desc, tags = _clip_texts(clip, platform)
            text_key = _normalize_text(title + "\n" + desc)
            if text_key and text_key in used_texts:
                continue
            per_platform[platform].append(
                dict(clip, _platform=platform, _title=title,
                     _desc=desc, _tags=tags))
            if text_key:
                used_texts.add(text_key)
            placed = True
            break
        if not placed:
            queued.append(clip_id)
            if wanted in order and capacity.get(wanted, 0) == 0:
                alerts.append(f"{clip_id}: {wanted} sin capacidad "
                              f"hoy (calentamiento, día libre o tope)")
            else:
                alerts.append(f"{clip_id}: sin hueco hoy, pasa al "
                              f"día siguiente")

    # Horas: 1/día -> noche; 2+ -> mediodía + noche (regla 4b).
    for platform in order:
        items = per_platform[platform]
        if not items:
            continue
        slots = _slot_times_for_count(len(items), platform)
        account_label = platform
        try:
            from .accounts import load_identifiers
            ident = load_identifiers().get(platform) or {}
            account_label = (ident.get("handle")
                             or ident.get("page_name") or platform)
        except Exception:
            pass
        for item, slot in zip(items, slots):
            clip_id = item.get("clip_id") or item.get("clip") or ""
            hora = _time_in_slot(slot, clip_id, day_iso)
            # Mismo clip en otra plataforma: mínimo 15 min entre
            # una y otra (regla 4). Si choca, se empuja dentro de
            # la franja; si no cabe, a la cola.
            hh, mm = (int(x) for x in hora.split(":"))
            moment = hh * 60 + mm
            prev = clip_first_time.get(clip_id)
            if prev is not None and abs(moment - prev) < \
                    CROSS_PLATFORM_MIN_MINUTES:
                moment = prev + CROSS_PLATFORM_MIN_MINUTES
                start, end = _slot_minutes(slot)
                if moment >= end:
                    queued.append(clip_id)
                    alerts.append(f"{clip_id}: no cabe con 15 min "
                                  f"de separación entre plataformas")
                    continue
                hora = f"{moment // 60:02d}:{moment % 60:02d}"
            clip_first_time.setdefault(clip_id, moment)
            tags = list(item["_tags"])
            limit_tags = hashtag_limit(platform)
            if len(tags) > limit_tags:
                tags = tags[:limit_tags]
                alerts.append(f"{clip_id} ({platform}): hashtags "
                              f"recortados a {limit_tags}")
            scheduled.append({
                "clip_id": clip_id,
                "plataforma": platform,
                "cuenta": account_label,
                "hora_local": hora,
                "titulo": item["_title"],
                "descripcion": item["_desc"],
                "hashtags": tags,
                "motivo": (f"viral {_clip_viral(item):.0f}, "
                           f"franja {slot} ({PUBLISH_WINDOW[slot][0]}-"
                           f"{PUBLISH_WINDOW[slot][1]}), "
                           f"{len(per_platform[platform])}º del día "
                           f"en {platform}"),
            })

    plan = {"fecha": day_iso, "publicaciones": scheduled,
            "en_cola": queued, "alertas": alerts}
    # Verificación final (regla 9): lo que no cumple las
    # secciones 2, 3 y 4 sale del plan y pasa a la cola.
    for clip_id, platform, why in _verify_plan(plan, limits):
        plan["publicaciones"] = [
            p for p in plan["publicaciones"]
            if not (p["clip_id"] == clip_id
                    and p["plataforma"] == platform)]
        if clip_id not in plan["en_cola"]:
            plan["en_cola"].append(clip_id)
        plan["alertas"].append(
            f"{clip_id} ({platform}): fuera del plan — {why}")
    plan["publicaciones"].sort(key=lambda p: p["hora_local"])
    return plan


# ---------------------------------------------------------------------------
# Validación del paquete (común a todas las plataformas)
# ---------------------------------------------------------------------------
def load_clip_package(clip_path):
    """Paquete viral del clip desde el manifest.json de su run.

    Busca ../manifest.json desde el archivo del clip. Devuelve
    (paquete_plataforma, manifiesto) o (None, manifiesto).
    """
    run_dir = os.path.dirname(os.path.dirname(os.path.abspath(clip_path)))
    manifest_path = os.path.join(run_dir, "manifest.json")
    manifest = _load_json(manifest_path, {})
    clip_name = os.path.basename(clip_path)
    for clip in manifest.get("clips", []):
        # El manifiesto guarda "clips/<archivo>"; el _v2 puede no estar
        # listado: se acepta por prefijo (mismo pico, misma edición).
        listed = os.path.basename(clip.get("file", ""))
        base = listed.rsplit(".mp4", 1)[0]
        if clip_name == listed or clip_name.startswith(base):
            return clip, manifest
    return None, manifest


def validate_package(clip_path, platform):
    """Chequeos del paquete antes de publicar. Devuelve [(ok, texto)]."""
    checks = []
    if not os.path.isfile(clip_path):
        return [(False, f"archivo no existe: {clip_path}")]
    checks.append((True, f"archivo existe ({os.path.getsize(clip_path) / 1e6:.1f} MB)"))
    probe = _probe_video(clip_path)
    if probe is None:
        checks.append((False, "ffprobe no pudo medir el video (¿FFmpeg instalado?)"))
    else:
        checks.append((probe.get("width") == 1080 and probe.get("height") == 1920,
                       f"resolución {probe.get('width')}x{probe.get('height')} (esperado 1080x1920)"))
        dur = probe.get("duration") or 0
        checks.append((55 <= dur <= 65,
                       f"duración {dur:.1f} s (esperado ~60 s)"))
        if platform == "facebook_reels":
            checks.append((3 <= dur <= 90,
                           "duración dentro de 3-90 s (obligatorio en FB Reels)"))

    package, manifest = load_clip_package(clip_path)
    if package is None:
        checks.append((False, "sin paquete viral en manifest.json (¿clip sin empaquetar?)"))
        return checks
    viral = package.get("viral", {})
    platforms_pkg = viral.get("platforms", {}).get(platform, {})
    title = platforms_pkg.get("title") or viral.get("best_title", "")
    checks.append((bool(title), f"título: {title!r}"))
    checks.append((len(title) <= 60, f"título {len(title)} caracteres (máx. 60)"))
    tags = platforms_pkg.get("tags") or viral.get("tags", [])
    checks.append((len(tags) <= 15, f"{len(tags)} tags (máx. 15)"))
    caption = platforms_pkg.get("caption", "")
    if platform == "tiktok":
        checks.append((len(caption) <= 150,
                       f"caption TikTok {len(caption)} caracteres (máx. 150)"))
    checks.append((bool(caption), "caption presente"))
    if platform in ("tiktok", "instagram_reels", "facebook_reels"):
        checks.append((os.path.isfile(clip_path),
                       "subtítulos quemados: usar el _v2 (con subs quemados), no el corte base"))
    return checks


# ---------------------------------------------------------------------------
# Publicadores por plataforma — interfaz común:
#   publish_draft(clip, package) -> dict con el paquete listo
#   publish(clip, package, ...)  -> dict con el resultado
#   status()                     -> dict con el último estado
# ---------------------------------------------------------------------------
class YouTubePublisher:
    """YouTube Shorts por API (Data API v3, videos.insert).

    OAuth 2.0 (flujo de app instalada):
      1. Proyecto en Google Cloud con YouTube Data API v3 habilitada.
      2. Credenciales OAuth (ID de cliente de escritorio) -> client_secrets.json
      3. Primera vez: flujo de consentimiento con scope
         https://www.googleapis.com/auth/youtube.upload -> token.json
      4. Subida: videos.insert, parte snippet+status, video 1080x1920,
         título ≤100 car. (nosotros ≤60), descripción, tags ≤15,
         privacyStatus "public" (o "unlisted" para revisar primero),
         categoría 20 (Gaming). Costo: ~1.600 unidades de cuota.
    Sin client_secrets.json / token.json, publish() se niega y solo
    dry_run() valida. Sin la librería google-api-python-client,
    también se niega (no se inventa una subida por HTTP a mano).
    """

    SCOPE = "https://www.googleapis.com/auth/youtube.upload"
    # Rutas de credenciales: SOLO a través de clipagent.credentials
    # (Fase 3.1). El token vive en tokens/youtube_shorts.json; el
    # token.json de la raíz es legado y se migra al leerlo.
    CLIENT_SECRETS = None  # se resuelve en __init__ vía credentials
    TOKEN_FILE = None

    def __init__(self, queue=None):
        self.queue = queue or PublishQueue()
        from clipagent import credentials as _creds
        self.CLIENT_SECRETS = _creds.client_secrets_path()
        self.TOKEN_FILE = _creds.token_path("youtube_shorts")

    def _token_present(self):
        """True si hay token en tokens/ o legado en la raíz."""
        from clipagent import credentials as _creds
        return (_creds.load_token("youtube_shorts") is not None
                or os.path.isfile(_creds.legacy_token_path()))

    def status(self):
        creds = self._token_present()
        secrets = os.path.isfile(self.CLIENT_SECRETS)
        return {"platform": "youtube_shorts",
                "oauth_configured": secrets and creds,
                "client_secrets": secrets, "token": creds,
                "quota_units_per_day": YOUTUBE_DAILY_QUOTA_UNITS,
                "upload_cost_units": YOUTUBE_UPLOAD_COST_UNITS,
                "upload_calls_per_day": YOUTUBE_UPLOAD_CALLS_PER_DAY,
                "max_uploads_by_quota": YOUTUBE_MAX_UPLOADS_BY_QUOTA,
                "published_today": len(self.queue.published_today("youtube_shorts"))}

    def dry_run(self, clip_path, streamer=None):
        """Valida todo lo que bloquearía la subida, sin subir nada."""
        checks = []
        checks += validate_package(clip_path, "youtube_shorts")
        package, manifest = load_clip_package(clip_path)
        streamer = streamer or _streamer_from_manifest(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(clip_path))),
                         "manifest.json"))
        ok_wl, wl_msg = check_whitelist(streamer)
        checks.append((ok_wl, f"lista blanca: {wl_msg}"))
        audit = audit_accounts()["youtube_shorts"]
        checks.append((audit["status"] == "green",
                       f"auditoría YT: {audit['status']} — {audit['publish_mode']}"))
        st = self.status()
        checks.append((st["oauth_configured"],
                       "OAuth: " + ("token.json presente" if st["oauth_configured"]
                                    else "SIN client_secrets.json/token.json — no se puede subir")))
        ok_limits, limits_msg = check_limits(self.queue, "youtube_shorts")
        checks.append((ok_limits, f"topes: {limits_msg}"))
        # Cuota: el plan (1-2/día) va contra el bucket propio de
        # videos.insert (100 llamadas/día), no contra el pool de
        # 10.000 u. compartido.
        plan, _max = DAILY_LIMITS["youtube_shorts"]
        checks.append((plan <= YOUTUBE_MAX_UPLOADS_BY_QUOTA,
                       f"cuota: plan {plan}/día de {YOUTUBE_MAX_UPLOADS_BY_QUOTA} "
                       f"llamadas/día en el bucket de videos.insert "
                       f"({YOUTUBE_UPLOAD_COST_UNITS} unidad por subida)"))
        return {"platform": "youtube_shorts", "clip": clip_path,
                "streamer": streamer, "dry_run": True,
                "would_publish": all(c[0] for c in checks),
                "checks": [{"ok": c[0], "detail": c[1]} for c in checks]}

    def publish(self, clip_path, package=None, privacy="public", live=False,
                thumbnail_path=None):
        """Subida real. Exige live=True, OAuth y dry-run en verde.

        Si se pasa thumbnail_path, tras la subida se llama a
        thumbnails.set (llamada separada, 50 u.; exige canal
        verificado). Si la miniatura falla, el video YA está subido:
        el fallo se devuelve en "thumbnail" sin tumbar la subida.
        """
        if not live:
            return {"ok": False, "reason": "dry-run por defecto: pasa --live para subir de verdad"}
        dry = self.dry_run(clip_path)
        if not dry["would_publish"]:
            failed = [c["detail"] for c in dry["checks"] if not c["ok"]]
            return {"ok": False, "reason": "dry-run no pasa", "failed_checks": failed}
        try:
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
            from google.oauth2.credentials import Credentials
        except ImportError:
            return {"ok": False,
                    "reason": "falta google-api-python-client: pip install google-api-python-client google-auth-oauthlib"}
        from clipagent import credentials as _creds
        # Migra el token.json legado a tokens/ si hace falta; la
        # ruta que se usa es siempre la del almacén único (3.1).
        _creds.migrate_legacy_youtube_token()
        creds = Credentials.from_authorized_user_file(self.TOKEN_FILE, [self.SCOPE])
        youtube = build("youtube", "v3", credentials=creds)
        package = package or load_clip_package(clip_path)[0]
        viral = (package or {}).get("viral", {})
        yt_pkg = viral.get("platforms", {}).get("youtube_shorts", {})
        body = {
            "snippet": {
                "title": yt_pkg.get("title") or viral.get("best_title", ""),
                "description": yt_pkg.get("caption", ""),
                "tags": (yt_pkg.get("tags") or viral.get("tags", []))[:15],
                "categoryId": "20",  # Gaming
            },
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
        }
        media = MediaFileUpload(clip_path, mimetype="video/mp4", resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = request.execute()
        video_id = response.get("id")
        item = self.queue._find(clip_path, "youtube_shorts") or self.queue.enqueue(
            clip_path, "youtube_shorts")
        self.queue.mark_published(item, video_id)
        result = {"ok": True, "video_id": video_id,
                  "url": f"https://www.youtube.com/shorts/{video_id}"}
        if thumbnail_path:
            from .platforms import YouTubeAdapter
            result["thumbnail"] = YouTubeAdapter().set_thumbnail(
                video_id, thumbnail_path)
        return result

    def publish_draft(self, clip_path):
        """YouTube no tiene borrador por API: el 'draft' es subir en
        privado (unlisted) para revisar y luego pasar a público."""
        return {"platform": "youtube_shorts", "mode": "unlisted-first",
                "note": "Subir con privacy='unlisted', revisar, y luego "
                        "cambiar a 'public' en YouTube Studio.",
                "clip": clip_path}


class DraftFirstPublisher:
    """TikTok / Instagram / Facebook: paquete manual (draft-first).

    Genera el paquete listo para subir a mano (archivo + caption +
    hashtags + portada) en drafts/<clip>_<plataforma>/ y lo deja en
    la cola como estado "draft". La publicación directa por API
    vive en DirectPublisher (fan-out); este publicador solo genera
    el paquete manual, que además se escribe cuando la API no se
    puede usar (sin token, sin auditoría, sin URL pública).
    """

    def __init__(self, platform, queue=None):
        assert platform in ("tiktok", "instagram_reels", "facebook_reels")
        self.platform = platform
        self.queue = queue or PublishQueue()

    def status(self):
        audit = audit_accounts()[self.platform]
        return {"platform": self.platform, "audit": audit["status"],
                "publish_mode": audit["publish_mode"],
                "drafts_today": len(self.queue.published_today(self.platform))}

    def publish_draft(self, clip_path):
        """Escribe el paquete manual. No publica nada."""
        package, manifest = load_clip_package(clip_path)
        if package is None:
            return {"ok": False, "reason": "sin paquete viral en manifest.json"}
        viral = package.get("viral", {})
        pkg = viral.get("platforms", {}).get(self.platform, {})
        streamer = _streamer_from_manifest(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(clip_path))),
                         "manifest.json"))
        ok_wl, wl_msg = check_whitelist(streamer)
        name = os.path.splitext(os.path.basename(clip_path))[0]
        out_dir = os.path.join(DRAFTS_DIR, f"{name}_{self.platform}")
        os.makedirs(out_dir, exist_ok=True)
        bundle = {
            "platform": self.platform,
            "clip": os.path.abspath(clip_path),
            "streamer": streamer,
            "whitelist_ok": ok_wl, "whitelist_detail": wl_msg,
            "title": pkg.get("title"),
            "caption": pkg.get("caption"),
            "hashtags": pkg.get("hashtags"),
            "tags": pkg.get("tags"),
            "thumbnail_text": pkg.get("thumbnail_text"),
            "thumbnail_note": pkg.get("thumbnail"),
            "subtitles": pkg.get("subtitles"),
            "duration_rule": pkg.get("duration_rule"),
            "manual_steps": self._manual_steps(),
        }
        _save_json(os.path.join(out_dir, "draft.json"), bundle)
        with open(os.path.join(out_dir, "UPLOAD.txt"), "w", encoding="utf-8") as f:
            f.write(f"SUBIDA MANUAL — {pkg.get('platform', self.platform)}\n")
            f.write(f"Archivo: {bundle['clip']}\n")
            f.write(f"Streamer: {streamer} — lista blanca: {wl_msg}\n\n")
            f.write("CAPTION (copiar tal cual):\n")
            f.write((pkg.get("caption") or "") + "\n\n")
            if pkg.get("thumbnail_text"):
                f.write(f"Portada / texto miniatura: {pkg['thumbnail_text']}\n")
            f.write("\n" + "\n".join(self._manual_steps()) + "\n")
        item = self.queue._find(clip_path, self.platform) or self.queue.enqueue(
            clip_path, self.platform, streamer)
        self.queue.set_state(item, "draft", f"paquete manual en {out_dir}")
        return {"ok": True, "draft_dir": out_dir, "bundle": bundle,
                "warning": None if ok_wl else
                f"BLOQUEADO: {wl_msg} — no subir hasta que el usuario lo agregue a la lista"}

    def _manual_steps(self):
        if self.platform == "tiktok":
            return ["1. Abrir TikTok (app o web) con @elclipdegta6____bits.",
                    "2. Subir el video, pegar el caption tal cual.",
                    "3. Activar subtítulos automáticos si hace falta (el video ya lleva quemados).",
                    "4. Publicar. No usar 'Direct Post' hasta aprobar la app."]
        if self.platform == "instagram_reels":
            return ["1. Abrir Instagram con @elclipdegta6 (cuando esté creada).",
                    "2. Nuevo Reel, subir el video, pegar el caption.",
                    "3. Portada: usar el frame con el texto de miniatura.",
                    "4. Publicar. Requiere cuenta Profesional + Page vinculada."]
        return ["1. Abrir Facebook con la Página El Clip de GTA 6 (cuando esté creada).",
                "2. Crear Reel, subir el video, pegar el caption.",
                "3. Publicar SOLO en la Página, nunca en el perfil personal.",
                "4. Duración 3-90 s: el clip de 60 s cabe justo."]

    def publish(self, clip_path, live=False):
        """La publicación directa por API vive en DirectPublisher
        (fan-out). Esta clase solo genera el paquete manual."""
        return {"ok": False,
                "reason": "la publicación directa por API es "
                          "DirectPublisher.publish; este publicador "
                          "solo genera el paquete manual "
                          "(publish_draft)"}


class DirectPublisher:
    """TikTok / Instagram / Facebook: publicación directa por API.

    TikTok va por Zernio (ZernioAdapter: presign + PUT + POST
    /v1/posts, publicación DIRECTA en público desde 2026-11-18:
    pre-chequeo dryRun y draft=False, publishNow True,
    PUBLIC_TO_EVERYONE por la app TikTok for Business auditada
    de Zernio — la auditoría propia NO hace falta para esta vía).
    Solo cae a borrador si faltan clave/cuenta/URL, si el
    pre-chequeo dice que no se puede publicar, o si la API
    falla. Instagram usa contenedor -> publicar y Facebook
    Reels en 3 fases. Sin clave, sin IDs de cuenta o sin URL
    pública, devuelve mode "draft" con el motivo explícito y
    genera además el paquete manual (nunca finge una
    publicación). Los fallos transitorios (5xx, rate limit) y
    los procesados pendientes (mode "processing") se retoman en
    el siguiente ciclo con los datos de reanudación guardados
    en el ítem.
    """

    def __init__(self, platform, queue=None, base_dir=None,
                 request_fn=None):
        assert platform in ("tiktok", "instagram_reels", "facebook_reels")
        self.platform = platform
        self.queue = queue or PublishQueue()
        self.base_dir = base_dir
        self.request_fn = request_fn
        self._drafts = DraftFirstPublisher(platform, self.queue)

    def status(self):
        audit = audit_accounts()[self.platform]
        return {"platform": self.platform, "audit": audit["status"],
                "publish_mode": audit["publish_mode"],
                "drafts_today": len(self.queue.published_today(self.platform))}

    def publish_draft(self, clip_path):
        """Paquete manual (el de siempre, para subir a mano)."""
        return self._drafts.publish_draft(clip_path)

    def _package_texts(self, clip_path):
        """(título, caption) del paquete por plataforma, con
        fallback al título/descripción generales del manifiesto."""
        package, _manifest = load_clip_package(clip_path)
        if package is None:
            return "", ""
        viral = package.get("viral", {})
        pkg = viral.get("platforms", {}).get(self.platform, {})
        title = pkg.get("title") or package.get("title_es", "")
        caption = (pkg.get("caption")
                   or package.get("desc_es", "")
                   or title)
        return title, caption

    def _resume_id(self, item):
        """ID de reanudación guardado en el ítem (container_id de
        Instagram), o None. TikTok por Zernio no reanuda: no hay
        publish_id que retomar."""
        if not item:
            return None
        data = item.get("platform_data") or {}
        if self.platform == "instagram_reels":
            return data.get("container_id")
        return None

    def publish(self, clip_path, live=False, item=None, cfg=None,
                request_fn=None, sleep_fn=None):
        """Subida real por API. Exige live=True; sin él, draft.

        Devuelve el resultado del adaptador: {"ok": True, ...} al
        publicar, mode "draft" si falta algo de configuración, mode
        "error" (retryable o no) si la API falla, mode "processing"
        si la plataforma sigue procesando (con el ID para retomar).
        """
        if not live:
            return {"ok": False, "mode": "draft",
                    "reason": "dry-run por defecto: sin --live no se "
                              "sube nada por API"}
        if cfg is None:
            from .config import load_config
            cfg = load_config()
        # La lista blanca se verifica EN EL MOMENTO de publicar: si
        # el streamer ya no está en la lista, no se sube nada.
        streamer = _streamer_from_manifest(
            os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(clip_path))), "manifest.json"))
        ok_wl, wl_msg = check_whitelist(streamer)
        if not ok_wl:
            return {"ok": False, "mode": "draft",
                    "reason": f"lista blanca: {wl_msg}"}
        fn = request_fn if request_fn is not None else self.request_fn
        _title, caption = self._package_texts(clip_path)
        if self.platform == "tiktok":
            # TikTok por Zernio (2026-11-18): el adaptador directo
            # (TikTokAdapter, Content Posting API con app sin
            # auditar) ya no se usa para publicar. ZernioAdapter
            # lee ZERNIO_API_KEY y ZERNIO_TIKTOK_ACCOUNT_ID del
            # entorno; publish_public() pre-chequea con dryRun y
            # publica en PÚBLICO (draft=False, publishNow True)
            # por la app TikTok for Business auditada de Zernio.
            # Solo cae a borrador si falta algo o el pre-chequeo
            # dice que no se puede publicar.
            from .platforms import ZernioAdapter
            return ZernioAdapter(self.base_dir, fn).publish_public(
                clip_path, caption)
        if self.platform == "instagram_reels":
            from . import platform_accounts as _pa
            from .platforms import InstagramAdapter
            from .public_url import get_public_url
            ig_user_id = _pa.get_ig_user_id(cfg)
            if not ig_user_id:
                return {"ok": False, "mode": "draft",
                        "reason": "sin ig_user_id de Instagram "
                                  "configurado (platform_accounts."
                                  "instagram_reels.ig_user_id o "
                                  "IG_USER_ID)"}
            url, url_reason = get_public_url(clip_path, cfg)
            if not url:
                return {"ok": False, "mode": "draft",
                        "reason": url_reason}
            return InstagramAdapter(self.base_dir, fn).publish_reel(
                ig_user_id, url, caption,
                resume_container_id=self._resume_id(item),
                sleep_fn=sleep_fn)
        from . import platform_accounts as _pa
        from .platforms import FacebookAdapter
        page_id = _pa.get_facebook_page_id(cfg)
        if not page_id:
            return {"ok": False, "mode": "draft",
                    "reason": "sin page_id de Facebook configurado "
                              "(platform_accounts.facebook_reels."
                              "page_id o FACEBOOK_PAGE_ID)"}
        return FacebookAdapter(self.base_dir, fn).publish_reel(
            page_id, clip_path, caption)


PUBLISHERS = {
    "youtube_shorts": YouTubePublisher,
    "tiktok": lambda q=None: DirectPublisher("tiktok", q),
    "instagram_reels": lambda q=None: DirectPublisher("instagram_reels", q),
    "facebook_reels": lambda q=None: DirectPublisher("facebook_reels", q),
}


def get_publisher(platform, queue=None):
    return PUBLISHERS[platform](queue)


# Reintentos de fallos transitorios (5xx, rate limit): un draft por
# fallo transitorio se reintenta en el siguiente ciclo del bucle,
# hasta este tope; después va a "failed" (revisión humana), nunca
# se reintenta a ciegas para siempre. Un draft por falta de
# credenciales/auditoría/configuración NO se reintenta: queda en
# draft con motivo hasta que el usuario lo resuelva.
MAX_TRANSIENT_RETRIES = 3


# ---------------------------------------------------------------------------
# Rechazos: 1 reintento solo para formato; copyright/contenido -> cuarentena
# ---------------------------------------------------------------------------
def handle_rejection(queue, clip, platform, error):
    """Clasifica un rechazo de la plataforma.

    - Formato (400, archivo, título, duración): 1 reintento tras
      corregir; el segundo fallo va a cuarentena.
    - Copyright / contenido / reutilización (403, claim, strike):
      cuarentena inmediata + revisión humana. Nunca reintento ciego.
    """
    error_l = (error or "").lower()
    is_copyright = any(k in error_l for k in
                       ("copyright", "reutiliz", "reuse", "claim",
                        "strike", "contenido", "content id", "403"))
    item = queue._find(clip, platform) or queue.enqueue(clip, platform)
    retries = sum(1 for h in item["history"] if h["state"] == "failed")
    if is_copyright:
        queue.quarantine(item, f"copyright/contenido: {error}")
        return {"action": "quarantined",
                "reason": "copyright/contenido: a cuarentena, revisión humana, sin reintento"}
    if retries >= 1:
        queue.quarantine(item, f"formato, 2º fallo: {error}")
        return {"action": "quarantined",
                "reason": "formato: ya se usó el único reintento, a cuarentena"}
    queue.mark_failed(item, f"formato: {error} (reintento 1/1 pendiente)")
    return {"action": "retry_once",
            "reason": "formato: corregir y reintentar UNA vez"}


# ---------------------------------------------------------------------------
# Publicación directa y bucle automático (Fase 6.3)
# ---------------------------------------------------------------------------
def _store_resume(item, queue, platform, result):
    """Guarda el ID de reanudación en el ítem (publish_id de TikTok
    o container_id de Instagram) para retomar en el siguiente ciclo
    sin volver a subir el video."""
    key = value = None
    if platform == "tiktok" and result.get("publish_id"):
        key, value = "publish_id", result["publish_id"]
    elif platform == "instagram_reels" and result.get("container_id"):
        key, value = "container_id", result["container_id"]
    if key:
        item.setdefault("platform_data", {})[key] = value
        queue.save()


def _transient_attempts(item):
    """Fallos transitorios ya registrados en el historial del ítem."""
    return sum(1 for h in (item.get("history") or [])
               if (h.get("note") or "").startswith("fallo transitorio"))


def publish_direct(platform, clip_path, queue=None, live=False,
                   item=None, cfg=None, request_fn=None, sleep_fn=None):
    """Publica un clip por API en esa plataforma (fan-out).

    YouTube: subida real por YouTubePublisher (exige OAuth y live).
    TikTok / Instagram / Facebook: DirectPublisher con los
    adaptadores de clipagent.platforms. Sin token, sin IDs o sin
    URL pública se devuelve mode "draft" con el motivo explícito.
    Nunca se finge una publicación.
    """
    pub = get_publisher(platform, queue)
    if platform == "youtube_shorts":
        return pub.publish(clip_path, live=live)
    return pub.publish(clip_path, live=live, item=item, cfg=cfg,
                       request_fn=request_fn, sleep_fn=sleep_fn)


def publish_one(queue, item, cfg=None, live=False, mode="full",
                counter_state=None):
    """Un ítem de la cola por la política (6.3). Devuelve
    {"action": published|retained|discarded|draft|failed|
    quarantined|limited|paused|processing, "reason": ...}."""
    from .policy import evaluate
    from .status import is_paused
    platform = item["platform"]
    clip_path = item["clip"]
    if not os.path.isfile(clip_path):
        queue.mark_failed(item, f"archivo no existe: {clip_path}")
        return {"action": "failed", "reason": "archivo no existe"}
    clip, manifest = load_clip_package(clip_path)
    if clip is None:
        queue.mark_failed(item, "sin entrada en el manifiesto")
        return {"action": "failed",
                "reason": "sin entrada en el manifiesto"}
    overrides = {o.get("platform")
                 for o in (manifest or {}).get("seo_overrides", [])}
    # 6B.4: si hay LLM disponible, la política puede reintentar el
    # SEO (70-89) con variantes generadas; sin LLM, se retiene.
    from .llm import get_llm_client, make_seo_variant
    llm = get_llm_client()
    make_variant = make_seo_variant(llm) if llm.available() else None
    decision = evaluate(
        clip, platform, queue=queue, cfg=cfg,
        red_flags=item.get("red_flags"), game=item.get("game"),
        vod_id=item.get("vod_id") or (manifest or {}).get("vod_id"),
        mode=mode, counter_state=counter_state,
        seo_override=platform in overrides,
        age_days=_item_age_days(item),
        make_variant=make_variant)
    if decision["decision"] == "publicar" and decision.get("improved_pkg"):
        # El reintento llegó al gate: el manifiesto guarda el
        # paquete mejorado para que la subida use ese título y
        # esa descripción, no los viejos.
        _apply_improved_package(clip_path, platform,
                                decision["improved_pkg"],
                                decision["improved_seo"])
    if decision["decision"] == "descartado":
        queue.set_state(item, "discarded", decision["reason"])
        return {"action": "discarded", "reason": decision["reason"]}
    if decision["decision"] == "retenido":
        queue.set_state(item, "retained", decision["reason"])
        return {"action": "retained", "reason": decision["reason"]}
    # 6.2: streamer desactivado en el panel: no se publican sus
    # clips nuevos (el permiso no se revoca, solo se pausa).
    if item.get("streamer"):
        from .streamers import get_streamer
        entry = get_streamer(item["streamer"])
        if entry is not None and not entry.get("enabled", True):
            reason = (f"streamer {item['streamer']} desactivado "
                      "en el panel")
            queue.set_state(item, "retained", reason)
            return {"action": "retained", "reason": reason}
    if is_paused(platform)[0]:
        return {"action": "paused",
                "reason": f"{platform} pausada: {is_paused(platform)[1]}"}
    # 6.5 / reglas 2026-11-19: calentamiento (< 14 días o < 20
    # publicaciones): semana 1 y 2 a 1/día, con el conteo real de
    # la cola. La etapa sale de accounts.warmup_stage.
    from .accounts import warmup_stage
    stage = warmup_stage(platform, queue=queue)
    if stage in ("week1", "week2"):
        today = queue.published_today(platform)
        if len(today) >= NEW_ACCOUNT_DAILY:
            return {"action": "limited",
                    "reason": (f"cuenta nueva ({stage}): ritmo "
                               f"reducido ({len(today)}/"
                               f"{NEW_ACCOUNT_DAILY} hoy, "
                               f"calentamiento 1/día hasta la semana 3)")}
    ok, why = check_limits(queue, platform, warmup_stage=stage)
    if not ok:
        return {"action": "limited", "reason": why}
    result = publish_direct(platform, clip_path, queue, live=live,
                            item=item, cfg=cfg)
    if result.get("mode") == "draft":
        # El paquete manual se genera igual (para subir a mano), y
        # el motivo queda visible en la cola y en el panel.
        reason = result.get("reason") or "draft"
        try:
            draft = get_publisher(platform, queue).publish_draft(
                clip_path)
            if isinstance(draft, dict) and draft.get("draft_dir"):
                reason = (f"{reason} (paquete manual en "
                          f"{draft['draft_dir']})")
        except Exception:  # noqa: BLE001 - el draft no rompe el bucle
            pass
        queue.set_state(item, "draft", reason)
        return {"action": "draft", "reason": reason}
    if result.get("mode") == "processing":
        # La plataforma sigue procesando: se guarda el ID de
        # reanudación y el ítem queda en su estado para retomar en
        # el siguiente ciclo sin volver a subir.
        _store_resume(item, queue, platform, result)
        reason = result.get("reason") or "procesando en la plataforma"
        queue.add_note(item, f"procesando en {platform}: {reason} "
                             "(se retoma en el siguiente ciclo)")
        return {"action": "processing", "reason": reason}
    if result.get("mode") == "error" and result.get("retryable"):
        # Fallo transitorio (5xx, rate limit): se reintenta en el
        # siguiente ciclo, hasta MAX_TRANSIENT_RETRIES, con espera
        # creciente 5, 15, 45 min (regla 8, 2026-11-19). Si el
        # error es en realidad de spam/límite, se detiene la
        # cuenta y se reprograma al día siguiente, SIN reintentar
        # hoy. Después, cae al tratamiento normal (failed ->
        # cuarentena en el 2º).
        attempts = _transient_attempts(item)
        retry = next_retry(result.get("reason") or "", attempts)
        if retry["action"] == "stop_today":
            return {"action": "limited", "reason": retry["reason"]}
        if retry["action"] == "retry":
            reason = result.get("reason") or "fallo transitorio"
            queue.add_note(
                item,
                f"fallo transitorio (intento {attempts + 1}/"
                f"{MAX_TRANSIENT_RETRIES}): {reason}; reintento en "
                f"{retry['wait_minutes']} min, en el siguiente ciclo")
            return {"action": "failed", "reason": reason}
    if result.get("ok"):
        video_id = (result.get("video_id") or result.get("publish_id")
                    or result.get("post_id") or result.get("media_id")
                    or "api")
        item.pop("platform_data", None)
        queue.mark_published(item, video_id)
        return {"action": "published",
                "reason": decision["reason"],
                "url": result.get("url")}
    action = handle_rejection(queue, clip_path, platform,
                              result.get("reason") or "error desconocido")
    # handle_rejection devuelve "retry_once" cuando el ítem queda en
    # failed con un reintento pendiente: para el bucle es "failed".
    mapped = {"retry_once": "failed"}.get(action["action"],
                                         action["action"])
    return {"action": mapped, "reason": action["reason"]}


# Plataformas a las que se propaga un clip publicado en YouTube
# (fan-out). El orden es el de PLATFORMS sin youtube_shorts.
FANOUT_TARGETS = ("tiktok", "instagram_reels", "facebook_reels")


def fan_out(queue, item, cfg=None):
    """Propaga un clip publicado en YouTube a las otras 3
    plataformas (fan-out).

    Encola el mismo clip en TikTok, Instagram y Facebook con los
    mismos metadatos (streamer, moment_key, red_flags, game,
    vod_id), solo si no tienen ítem ya. NO publica nada por sí
    mismo y NO toca el ítem de YouTube: cada plataforma pasa por
    sus propias puertas (política, topes, breaker, auditoría) en
    su pasada del bucle. Con fanout.enabled: false en config, no
    hace nada. Devuelve las plataformas encoladas.
    """
    if cfg is not None:
        enabled = (cfg.get("fanout") or {}).get("enabled", True)
        if not enabled:
            return []
    enqueued = []
    for platform in FANOUT_TARGETS:
        if queue._find(item["clip"], platform) is not None:
            continue
        new_item = queue.enqueue(
            item["clip"], platform, streamer=item.get("streamer"),
            moment_key=item.get("moment_key"),
            red_flags=item.get("red_flags"), game=item.get("game"),
            vod_id=item.get("vod_id"))
        queue.add_note(
            new_item,
            f"fan-out desde youtube_shorts "
            f"({item.get('platform_id') or 'sin id'})")
        enqueued.append(platform)
    return enqueued


def _record_counter(counter_state, event, clip, platform, path=None):
    """6.7: registra published_ok/published_fail en counter.py y
    guarda el estado. Solo se llama en modo gradual."""
    from counter import record_event, save_state
    record_event(counter_state, event, clip=clip, platform=platform)
    save_state(counter_state, path=path)


def publish_loop(queue=None, interval_s=300, live=False, once=False,
                 cfg=None, stop_event=None, notify_fn=None,
                 counter_path=None, metrics_path=None):
    """Bucle del publicador: evalúa la cola por política y publica.

    Una pasada por plataforma (pausadas se saltan), y espera
    interval_s entre pasadas salvo --once. Devuelve el conteo de
    acciones. `notify_fn(evento, detalle)` se llama al publicar,
    fallar o mandar a cuarentena (6.6).
    """
    import threading
    from . import breaker as _breaker
    from .config import load_config
    queue = queue or PublishQueue()
    cfg = cfg or load_config()
    mode = ((cfg.get("autopublish") or {}).get("mode") or "gradual")
    counter_state = None
    if mode == "gradual":
        from counter import load_state as _load_counter_state
        counter_state = _load_counter_state(counter_path)
    stop = stop_event or threading.Event()
    stats = {"published": 0, "retained": 0, "discarded": 0, "draft": 0,
             "failed": 0, "quarantined": 0, "limited": 0, "paused": 0,
             "breaker_tripped": 0, "archived": 0, "processing": 0,
             "fanout": 0}
    while True:
        for platform in PLATFORMS:
            # 6B.2: archivado automático: clip con 3+ días sin subir
            # por prioridad baja (< 70) se archiva solo, no frena
            # a los buenos ni se reintenta.
            for scored in queue.scored_items(platform):
                if scored["auto_archive"]:
                    queue.set_state(scored["_item"], "archived",
                                    scored["auto_archive_reason"])
                    stats["archived"] += 1
            # 6.4: plataforma con el breaker disparado: se salta entera.
            tripped, trip_reason = _breaker.is_tripped(platform)
            if tripped:
                continue
            for item in queue.due_items(
                    platform, states=("approved", "queued")):
                result = publish_one(queue, item, cfg=cfg, live=live,
                                     mode=mode,
                                     counter_state=counter_state)
                action = result["action"]
                if action in stats:
                    stats[action] += 1
                # 6.7: en modo gradual, cada resultado alimenta el
                # contador de AUTOPUBLISH (fases y rachas). En modo
                # full el contador no se toca.
                if (counter_state is not None
                        and action in ("published", "failed",
                                       "quarantined")):
                    _record_counter(
                        counter_state,
                        "published_ok" if action == "published"
                        else "published_fail",
                        clip=item["clip"], platform=platform,
                        path=counter_path)
                if action == "published":
                    _breaker.record_success(platform)
                    # Fan-out: publicado en YouTube -> se encolan las
                    # otras 3 plataformas en la misma pasada (cada
                    # una pasa por sus propias puertas). El fallo de
                    # una nunca revierte el publicado de YouTube.
                    if platform == "youtube_shorts":
                        targets = fan_out(queue, item, cfg=cfg)
                        stats["fanout"] += len(targets)
                        if targets and notify_fn:
                            notify_fn("fan-out",
                                      f"{item['clip']} -> "
                                      f"{', '.join(targets)}")
                    # 9.1: el clip publicado entra en métricas (vistas
                    # a 24/48 h y 7 d). Nunca rompe el bucle.
                    try:
                        from .metrics import record_publication
                        record_publication(item["clip"], platform,
                                           item.get("platform_id"),
                                           db_path=metrics_path)
                    except Exception:  # noqa: BLE001 - métricas nunca rompen
                        pass
                    if notify_fn:
                        notify_fn("publicado",
                                  f"{platform}: {item['clip']} "
                                  f"({result.get('url', '')})")
                elif action in ("failed", "quarantined"):
                    reason = result.get("reason", "")
                    kind = ("copyright"
                            if any(w in reason.lower()
                                   for w in _breaker.COPYRIGHT_WORDS)
                            else "error")
                    trip = _breaker.record_failure(
                        platform, reason, kind=kind, cfg=cfg)
                    if trip["tripped"]:
                        stats["breaker_tripped"] += 1
                        if notify_fn:
                            notify_fn("plataforma pausada",
                                      f"{platform}: {trip['reason']}")
                    elif notify_fn:
                        notify_fn("falló" if action == "failed"
                                  else "cuarentena",
                                  f"{platform}: {item['clip']}: "
                                  f"{result.get('reason', '')}")
        if once or stop.is_set():
            break
        stop.wait(interval_s)
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description="Publicación de clips (Bloque 7)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("audit", help="auditoría de las 4 cuentas ES")
    p = sub.add_parser("dry-run", help="validar una subida sin subir nada")
    p.add_argument("--clip", required=True)
    p.add_argument("--platform", default="youtube_shorts", choices=list(PLATFORMS))
    p = sub.add_parser("draft", help="generar paquete manual (draft-first)")
    p.add_argument("--clip", required=True)
    p.add_argument("--platform", required=True, choices=list(PLATFORMS))
    p = sub.add_parser("publish", help="publicar (dry-run salvo --live)")
    p.add_argument("--clip", required=True)
    p.add_argument("--platform", default="youtube_shorts", choices=list(PLATFORMS))
    p.add_argument("--live", action="store_true", help="subida real (YT con OAuth)")
    p.add_argument("--privacy", default="public", choices=["public", "unlisted", "private"])
    p = sub.add_parser("status", help="estado de una plataforma")
    p.add_argument("--platform", default="youtube_shorts", choices=list(PLATFORMS))
    p = sub.add_parser("quarantine", help="mandar un ítem a cuarentena")
    p.add_argument("--clip", required=True)
    p.add_argument("--platform", required=True, choices=list(PLATFORMS))
    p.add_argument("--reason", required=True)
    p = sub.add_parser("plan", help="plan diario JSON (regla 9): "
                       "qué subir, dónde y a qué hora, sin publicar")
    p.add_argument("--clips", required=True,
                   help="JSON con la lista de clips "
                        "([{clip_id, viral_score, titulo, descripcion, "
                        "hashtags, plataforma?, aporte_propio?}]) o "
                        '{"clips": [...], "accounts": {...}}')
    p.add_argument("--date", default=None,
                   help="fecha YYYY-MM-DD (hoy en Chile por defecto)")
    p.add_argument("--accounts", default=None,
                   help="JSON con accounts_info por plataforma "
                        "(opcional, ver plan_daily)")
    p.add_argument("--queue", default=None,
                   help="ruta de publish_queue.json (por defecto, "
                        "la del repo; solo se lee el historial)")
    p = sub.add_parser("loop", help="bucle automático: evalúa la cola "
                       "por política y publica (6.3)")
    p.add_argument("--live", action="store_true",
                   help="subidas reales (sin --live, YouTube no sube)")
    p.add_argument("--once", action="store_true",
                   help="una sola pasada y salir")
    p.add_argument("--interval-minutes", type=int, default=5,
                   help="minutos entre pasadas (default 5)")
    args = parser.parse_args(argv)

    queue = PublishQueue()
    if args.cmd == "audit":
        print(audit_report_text(audit_accounts()))
        return 0
    if args.cmd == "status":
        print(json.dumps(get_publisher(args.platform, queue).status(),
                         indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "dry-run":
        pub = get_publisher(args.platform, queue)
        if args.platform == "youtube_shorts":
            result = pub.dry_run(args.clip)
        else:
            result = {"platform": args.platform, "clip": args.clip, "dry_run": True,
                      "checks": [{"ok": c[0], "detail": c[1]}
                                 for c in validate_package(args.clip, args.platform)],
                      "note": "draft-first: el dry-run valida el paquete; la subida es manual"}
            result["would_publish"] = False
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("would_publish") else 1
    if args.cmd == "draft":
        result = get_publisher(args.platform, queue).publish_draft(args.clip)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1
    if args.cmd == "publish":
        pub = get_publisher(args.platform, queue)
        if args.platform == "youtube_shorts":
            result = pub.publish(args.clip, privacy=args.privacy, live=args.live)
        else:
            result = pub.publish(args.clip, live=args.live)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1
    if args.cmd == "quarantine":
        item = queue._find(args.clip, args.platform) or queue.enqueue(args.clip, args.platform)
        queue.quarantine(item, args.reason)
        print(f"En cuarentena: {args.clip} [{args.platform}] — {args.reason}")
        return 0
    if args.cmd == "plan":
        with open(args.clips, encoding="utf-8") as f:
            payload = json.load(f)
        clips = payload.get("clips", payload) if isinstance(
            payload, dict) else payload
        accounts_info = None
        if args.accounts:
            with open(args.accounts, encoding="utf-8") as f:
                accounts_info = json.load(f)
        elif isinstance(payload, dict):
            accounts_info = payload.get("accounts")
        plan_queue = PublishQueue(args.queue) if args.queue else queue
        plan = plan_daily(clips, queue=plan_queue, day=args.date,
                          accounts_info=accounts_info)
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "loop":
        from .notify import notify
        stats = publish_loop(queue, live=args.live, once=args.once,
                             interval_s=args.interval_minutes * 60,
                             notify_fn=notify)
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
