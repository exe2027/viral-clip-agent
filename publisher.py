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
  * Sin fila en verde en la auditoría, esa cuenta queda en draft-first.
  * La lista blanca se verifica EN EL MOMENTO de publicar, no solo al
    detectar: si el streamer ya no está en la lista, no se publica.
    (2026-11-16: estar en la lista que sube el usuario = permiso ya
    dado; no hay ficha de permiso que registrar.)
  * Rechazos: error de formato -> 1 reintento; copyright/contenido ->
    cuarentena y revisión humana, nunca reintento ciego.
  * Topes: YT 3-5/día, resto 2-3/día, espaciamiento 2-3 h, franja
    tarde/noche de Chile (18:00-23:59 America/Santiago).
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
  * influencers.yaml (Bloques 8+9) + allowed_streamers.yaml — lista
    blanca de streamers. La canónica es influencers.yaml. REGLA DEL
    USUARIO (2026-11-16): estar en la lista que sube el usuario YA
    es permiso, sin ficha ni contacto previo. Se relee en cada
    publicación: si el streamer ya no está en la lista, la subida
    se bloquea aunque el clip ya estuviera aprobado.
  * accounts.yaml — estado conocido de cada cuenta (para la
    auditoría). Si no existe, audit_accounts() usa los valores
    por defecto, que son el estado real conocido a 2026-11-16.

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
# Topes y franja (Bloque 7). Se leen de limits.yaml (Fase 2.4); los
# valores de abajo son solo el fallback si falta ese archivo.
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
    "youtube_shorts": {"plan_per_day": 3, "max_per_day": 5,
                       "quota_upload_cost_units": 1,
                       "quota_upload_calls_per_day": 100,
                       "quota_other_daily_units": 10000},
    "tiktok": {"plan_per_day": 2, "max_per_day": 3},
    "instagram_reels": {"plan_per_day": 2, "max_per_day": 3},
    "facebook_reels": {"plan_per_day": 2, "max_per_day": 3},
    "min_spacing_hours": 2,
    "publish_window": [18, 24],
    "new_account_daily": 1,
}


def load_limits(path=None):
    """Límites de plataforma desde limits.yaml (o el fallback).

    Devuelve un dict con una entrada por plataforma (plan_per_day,
    max_per_day, y en YouTube la cuota) más min_spacing_hours,
    publish_window y new_account_daily. Si falta el archivo o
    PyYAML, devuelve los valores por defecto del código.
    """
    limits = {k: dict(v) if isinstance(v, dict) else v
              for k, v in _DEFAULT_LIMITS.items()}
    if path is None:
        path = os.path.join(BASE_DIR, "limits.yaml")
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
    for key in ("min_spacing_hours", "publish_window", "new_account_daily"):
        if key in data:
            limits[key] = data[key]
    return limits


_LIMITS = load_limits()

DAILY_LIMITS = {
    # plataforma: (plan_día, máximo_día)
    p: (int(_LIMITS[p]["plan_per_day"]), int(_LIMITS[p]["max_per_day"]))
    for p in PLATFORMS
}
MIN_SPACING_HOURS = int(_LIMITS["min_spacing_hours"])
PUBLISH_WINDOW = tuple(_LIMITS["publish_window"])
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
                "published", "failed", "quarantined", "draft")

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

    Fuente canónica: influencers.yaml (Bloques 8+9). Se suma
    allowed_streamers.yaml como atajo. Vacía = lista vacía.
    """
    if path:
        return _parse_whitelist_file(path)
    merged = {}
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
    from clipagent.accounts import compute_state
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

    def enqueue(self, clip, platform, streamer=None):
        if self._find(clip, platform):
            return self._find(clip, platform)
        item = {"clip": clip, "platform": platform, "streamer": streamer,
                "state": "queued", "history": [
                    {"at": _now_chile().isoformat(), "state": "queued"}],
                "scheduled_for": None, "published_at": None,
                "platform_id": None, "quarantine_reason": None}
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

    def last_published_at(self, platform):
        published = [i for i in self.data["items"]
                     if i["platform"] == platform and i["state"] == "published"
                     and i.get("published_at")]
        if not published:
            return None
        return max(i["published_at"] for i in published)

    def due_items(self, platform=None):
        """Ítems approved cuya hora programada ya llegó (o sin hora)."""
        now = _now_chile().isoformat()
        return [i for i in self.data["items"]
                if i["state"] == "approved"
                and (platform is None or i["platform"] == platform)
                and (not i.get("scheduled_for") or i["scheduled_for"] <= now)]


def check_limits(queue, platform):
    """Topes antes de publicar: (ok, motivo).

    - Tope diario (plan) y techo (YT: bucket de videos.insert,
      100 llamadas/día; el resto, topes internos de limits.yaml).
    - Espaciamiento mínimo 2 h desde la última subida.
    - Franja 18:00-23:59 Chile.
    """
    plan, maximum = DAILY_LIMITS[platform]
    today = queue.published_today(platform)
    if len(today) >= maximum:
        return False, f"tope diario alcanzado: {len(today)}/{maximum} en {platform}"
    if len(today) >= plan:
        return False, (f"plan del día cubierto: {len(today)}/{plan} en {platform} "
                       f"(techo {maximum}, subir más requiere decisión explícita)")
    last = queue.last_published_at(platform)
    if last:
        last_dt = datetime.fromisoformat(last)
        gap = (_now_chile() - last_dt).total_seconds() / 3600
        if gap < MIN_SPACING_HOURS:
            return False, (f"espaciamiento: última subida hace {gap:.1f} h, "
                           f"mínimo {MIN_SPACING_HOURS} h")
    hour = _now_chile().hour
    if not (PUBLISH_WINDOW[0] <= hour < PUBLISH_WINDOW[1]):
        return False, (f"fuera de franja: son las {hour:02d}:00 Chile, "
                       f"la franja es {PUBLISH_WINDOW[0]:02d}:00-{PUBLISH_WINDOW[1]:02d}:00")
    return True, f"dentro de topes ({len(today)}/{plan} hoy, plan {plan}/día)"


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
        # Cuota: el plan (3-5/día) va contra el bucket propio de
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
            from clipagent.platforms import YouTubeAdapter
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
    """TikTok / Instagram / Facebook: draft-first, nunca directo.

    Genera el paquete listo para subir a mano (archivo + caption +
    hashtags + portada) en drafts/<clip>_<plataforma>/ y lo deja en
    la cola como estado "draft". La publicación directa solo se
    habilita cuando audit_accounts() da verde EN ESA PLATAFORMA
    (app aprobada, cuenta Profesional + Page, Page + token) y el
    usuario lo aprueba explícitamente.
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
        """Directo solo si la auditoría de ESA plataforma está en verde.
        Hoy ninguna lo está: siempre devuelve el draft."""
        audit = audit_accounts()[self.platform]
        if audit["status"] != "green":
            draft = self.publish_draft(clip_path)
            return {"ok": False, "reason": f"auditoría {self.platform}: {audit['status']} — draft-first",
                    "publish_mode": audit["publish_mode"], "draft": draft}
        return {"ok": False,
                "reason": "publicación directa por API no implementada aún "
                          "para esta plataforma (falta el cliente); usar el draft"}


PUBLISHERS = {
    "youtube_shorts": YouTubePublisher,
    "tiktok": lambda q=None: DraftFirstPublisher("tiktok", q),
    "instagram_reels": lambda q=None: DraftFirstPublisher("instagram_reels", q),
    "facebook_reels": lambda q=None: DraftFirstPublisher("facebook_reels", q),
}


def get_publisher(platform, queue=None):
    return PUBLISHERS[platform](queue)


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
    return 0


if __name__ == "__main__":
    sys.exit(main())
