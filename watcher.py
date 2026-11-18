#!/usr/bin/env python3
"""
watcher.py — monitor de influencers para viral-clip-agent.

Vigila la lista blanca de ``streamers.yaml`` (hoy: SOLO IlloJuan,
https://www.twitch.tv/illojuan) y, cada pasada:

  * detecta si cada influencer está EN VIVO (título, juego, espectadores),
  * detecta VODs NUEVOS desde su última pasada,
  * encola los VODs que pasan TODOS los frenos (ver abajo).

REGLA DE DISEÑO (usuario, 2026-11-16): si varios influencers están en
vivo a la vez, la detección en vivo SÍ es para todos, pero NO se sube
nada en el momento: los clips se hacen solo cuando termina el directo
y sale el VOD, y siempre con aprobación humana antes de publicar.
El watcher detecta en vivo y REGISTRA; encola SOLO VODs terminados.
Un VOD en curso (el directo sigue) se registra como "en curso" y se
revisa en la siguiente pasada, cuando ya esté terminado.

FRENOS (antes de encolar un VOD; si falla uno, no se encola y se
registra el motivo):
  1. Kill-switch: si existe ``queue.kill``, no se encola NADA.
  2. Lista blanca: solo influencers de ``streamers.yaml`` (la
     canónica). REGLA 2026-11-16: estar en la lista que sube el
     usuario YA es permiso; solo `denegado`/`revocado` excluyen.
     Un influencer fuera de la lista no se encola, aunque el VOD
     sea de GTA 6.
  3. Solo GTA 6 verificado: la categoría/juego del VOD (o del directo)
     tiene que ser GTA 6. GTA V / GTA V RP / otro juego NO se encola.
  4. Solo VOD terminado: duración > 0 y directo ya cerrado. Un VOD en
     curso no se encola (el pre-análisis de un VOD en curso, sin
     publicar, queda como opción futura, no implementada).
  5. Dedup: ``seen_vods.json`` (gitignored) guarda cada VOD visto con
     su resultado. Un VOD ya visto no se vuelve a proponer. La cola
     (``jobqueue.py``) además rechaza un VOD + idioma que ya tenga un job
     activo.
  6. Tope diario: máx. N VODs nuevos por influencer y día (defecto 5,
     configurable en ``config.yaml`` -> ``watcher``).
  7. Regla nº 1 (no-ban): nunca más de 1 job por influencer a la vez
     en ``processing``. Si el worker ya está procesando un VOD suyo,
     el siguiente espera.
  8. Aprobación humana: el job queda en ``queued`` -> el worker lo
     procesa -> ``ready_for_review``. Nada se publica solo: publicar
     es otro bloque, con su propia aprobación.

MODO SEGURO POR DEFECTO: ``--dry-run`` es el comportamiento por
defecto: muestra qué encolaría sin tocar la cola. Solo ``--live``
encola de verdad (y aun así, solo lo que pasa los frenos).

Fuentes de datos (todo gratis, $0):
  * Kick: API pública sin key (la que usa la propia web). Reutiliza
    ``kick_downloader.core.http_json`` (reintentos + fallback curl).
  * Twitch: API Helix oficial (gratis, sin tarjeta) si hay
    credenciales (``TWITCH_CLIENT_ID`` / ``TWITCH_CLIENT_SECRET`` o
    ``config.yaml`` -> ``watcher.twitch``). Sin credenciales usa la
    ruta pública GQL que usa la propia web de Twitch (sin key, no
    oficial: funciona hoy, puede cambiar; Helix es la vía estable).
    Ver WATCHER.md para crear la app gratis en dev.twitch.tv.

Uso:
    python3 watcher.py --once --dry-run     # una pasada, sin encolar (defecto)
    python3 watcher.py --once --live        # una pasada, encola lo que pase los frenos
    python3 watcher.py --loop --live        # bucle cada poll_minutes (config.yaml, defecto 15)
    python3 watcher.py --once --dry-run --streamer illojuan   # solo un influencer
    python3 watcher.py --once --dry-run --state /tmp/seen-test.json  # estado aparte

Estado persistente: ``seen_vods.json`` (gitignored). En ``--dry-run``
también se guarda lo visto (con outcome ``would_enqueue`` o
``skipped:<motivo>``), para que una segunda pasada no vuelva a
proponer lo mismo; en ``--live`` un VOD marcado ``would_enqueue`` se
re-evalúa y se encola de verdad (el dry-run nunca "gasta" un VOD).
Un VOD marcado ``skipped:juego_no_permitido`` no se re-evalúa (el
juego de un VOD terminado no cambia).

Sin push, sin publicar, sin contactar streamers: esto solo LEE datos
públicos y encola en la cola local.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from jobqueue import (Queue, QueueError, detect_game,  # noqa: E402
                    kill_switch_active, load_games_config)

# --- Twitch: GQL público (el que usa la web twitch.tv) -------------------
# Client-ID público y estático de la web de Twitch. No es una key
# personal ni un secreto: es el que envía cualquier navegador al abrir
# twitch.tv. Sirve para leer datos públicos sin cuenta. Vía no oficial:
# si Twitch lo cambia o lo bloquea, el watcher lo dice y Twitch queda
# en "no configurado" hasta poner credenciales Helix (ver WATCHER.md).
TWITCH_GQL_URL = "https://gql.twitch.tv/gql"
TWITCH_GQL_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"

TWITCH_GQL_QUERY = """query($login: String!) {
  user(login: $login) {
    id login displayName
    stream { id type title createdAt viewersCount game { name } }
    videos(first: 20, type: ARCHIVE) {
      edges { node { id title createdAt lengthSeconds game { name } viewCount } }
    }
  }
}"""

# --- Twitch: Helix oficial ------------------------------------------------
TWITCH_HELIX = "https://api.twitch.tv/helix"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"

DEFAULT_POLL_MINUTES = 15
DEFAULT_MAX_NEW_PER_DAY = 5
DEFAULT_STATE_FILE = os.path.join(HERE, "seen_vods.json")

# Motivos de salto que se re-evalúan en cada pasada (pueden cambiar).
# (2026-11-16: "sin_permiso" ya no existe como motivo: estar en la
# lista = permiso ya dado.)
RETRYABLE_SKIPS = ("kill_switch", "tope_diario",
                   "en_processing", "vod_en_curso", "ya_en_cola",
                   "error_red")
# Motivo definitivo: el juego de un VOD terminado no cambia.
PERMANENT_SKIP = "juego_no_permitido"


class WatcherError(Exception):
    """Error de configuración o de ejecución (mensaje para el usuario)."""


# ---------------------------------------------------------------------------
# HTTP genérico (stdlib; Kick reutiliza kick_downloader.core.http_json)
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def http_json(url, data=None, headers=None, timeout=30):
    """GET (o POST si hay ``data``) que devuelve JSON. Lanza WatcherError."""
    h = dict(HEADERS)
    h.update(headers or {})
    body = json.dumps(data).encode() if data is not None else None
    if body is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise WatcherError(f"HTTP {e.code} en {url}: {detail}")
    except Exception as e:
        raise WatcherError(f"No se pudo leer {url}: {e}")


def parse_dt(s):
    """Fecha ISO / 'YYYY-MM-DD HH:MM:SS' -> datetime con tz UTC (o None)."""
    if not s:
        return None
    s = str(s).strip()
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Config y lista blanca
# ---------------------------------------------------------------------------

def load_yaml(path):
    """Carga un YAML con PyYAML (instalado). Error claro si falta."""
    try:
        import yaml
    except ImportError:
        raise WatcherError(
            "Hace falta PyYAML para leer streamers.yaml "
            "(pip install pyyaml).")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_influencers(path=None):
    """Lista blanca canónica: entradas de streamers.yaml (Fase 4.1).

    Devuelve [{usuario, plataforma, idioma, juego, estado, ...}].
    Si se pasa una ruta explícita a un archivo viejo, se lee ese
    archivo; si no, manda streamers.yaml (con respaldo a las listas
    viejas si no existe).
    """
    if path:
        if not os.path.isfile(path):
            raise WatcherError(f"No existe {path}: sin lista blanca no se vigila a nadie.")
        data = load_yaml(path)
        entries = data.get("influencers") or data.get("streamers") or []
        out = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            usuario = str(e.get("usuario") or e.get("name") or "").strip()
            if not usuario:
                continue
            out.append({
                "usuario": usuario.lower(),
                "plataforma": str(e.get("plataforma") or e.get("platform") or "twitch").strip().lower(),
                "idioma": str(e.get("idioma") or e.get("language") or "es").strip().lower() or "es",
                "juego": str(e.get("juego") or e.get("game") or "").strip(),
                "estado": str(e.get("estado") or "pendiente").strip().lower(),
                "canal_original": str(e.get("canal_original") or e.get("channel_url") or "").strip(),
            })
        return out
    from clipagent.streamers import load_streamers
    return [{
        "usuario": e["name"],
        "plataforma": e["platform"],
        "idioma": e["language"],
        "juego": e["game"],
        "estado": e["estado"],
        "canal_original": e["channel_url"],
    } for e in load_streamers() if e.get("enabled", True)]


def load_watcher_config(config_path=None):
    """Sección ``watcher`` de config.yaml + credenciales de Twitch.

    Las credenciales van SOLO por variables de entorno
    (TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET, o .env en la raíz,
    que config.load_config carga). config.yaml es versionado y
    nunca lleva secretos: aunque tenga client_id/client_secret,
    se ignoran.
    """
    try:
        from clipagent.config import load_config
        cfg = load_config(config_path)
    except Exception:
        cfg = {}
    w = (cfg or {}).get("watcher") or {}
    return {
        "poll_minutes": int(w.get("poll_minutes") or DEFAULT_POLL_MINUTES),
        "max_new_per_day": int(
            w.get("max_new_vods_per_streamer_per_day") or DEFAULT_MAX_NEW_PER_DAY),
        "state_file": w.get("state_file") or DEFAULT_STATE_FILE,
        "twitch_client_id": (os.environ.get("TWITCH_CLIENT_ID") or "").strip(),
        "twitch_client_secret": (os.environ.get("TWITCH_CLIENT_SECRET") or "").strip(),
    }


# ---------------------------------------------------------------------------
# Estado persistente (seen_vods.json)
# ---------------------------------------------------------------------------

def load_state(path):
    """{'vods': {...}, 'live': {...}, 'daily': {...}}. Vacío si no existe."""
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("vods", {})
                data.setdefault("live", {})
                data.setdefault("daily", {})
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 1, "vods": {}, "live": {}, "daily": {}}


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Twitch
# ---------------------------------------------------------------------------

class TwitchHelix:
    """API Helix oficial con token de app (client credentials, gratis)."""

    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token = None

    def _auth(self):
        data = http_json(TWITCH_TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        })
        self.token = data.get("access_token")
        if not self.token:
            raise WatcherError("Twitch Helix: no devolvió access_token.")

    def _get(self, path, params):
        if not self.token:
            self._auth()
        url = TWITCH_HELIX + path + "?" + urllib.parse.urlencode(params)
        headers = {"Client-Id": self.client_id,
                   "Authorization": f"Bearer {self.token}"}
        try:
            return http_json(url, headers=headers)
        except WatcherError as e:
            if "HTTP 401" in str(e):
                self._auth()
                return http_json(url, headers={
                    "Client-Id": self.client_id,
                    "Authorization": f"Bearer {self.token}"})
            raise

    def live_category(self, login):
        """Categoría ACTUAL del directo (Helix oficial).

        GET /helix/streams?user_login=<login> (game_name/game_id).
        Devuelve {"game_name", "game_id", "live", "source"}; si no
        está en vivo, live=False y game_name="" (sin asumir).
        """
        streams = self._get("/streams", {"user_login": login}).get("data") or []
        if not streams:
            return {"game_name": "", "game_id": "", "live": False,
                    "source": "helix /streams (offline)"}
        s = streams[0]
        return {"game_name": s.get("game_name") or "",
                "game_id": s.get("game_id") or "",
                "live": True,
                "source": "helix /streams?user_login"}

    def channel_category(self, login):
        """Categoría del canal (Helix oficial).

        GET /helix/users?login= -> id, luego GET
        /helix/channels?broadcaster_id=<id> (game_name/game_id).
        Es la categoría que Twitch muestra en el canal (la del
        directo en curso o la última).
        """
        users = self._get("/users", {"login": login}).get("data") or []
        if not users:
            raise WatcherError(f"Twitch Helix: no existe el usuario {login!r}.")
        user_id = users[0]["id"]
        channels = self._get("/channels", {"broadcaster_id": user_id}).get("data") or []
        if not channels:
            return {"game_name": "", "game_id": "",
                    "source": "helix /channels (sin datos)"}
        c = channels[0]
        return {"game_name": c.get("game_name") or "",
                "game_id": c.get("game_id") or "",
                "source": "helix /channels?broadcaster_id"}

    def check(self, login):
        """Devuelve (live_dict_o_None, [vods])."""
        users = self._get("/users", {"login": login}).get("data") or []
        if not users:
            raise WatcherError(f"Twitch Helix: no existe el usuario {login!r}.")
        user_id = users[0]["id"]
        streams = self._get("/streams", {"user_login": login}).get("data") or []
        live = None
        if streams:
            s = streams[0]
            live = {"id": s["id"], "title": s.get("title") or "",
                    "game": s.get("game_name") or "",
                    "viewers": s.get("viewer_count") or 0,
                    "started_at": s.get("started_at") or ""}
        videos = self._get("/videos", {"user_id": user_id, "type": "archive",
                                        "first": 20}).get("data") or []
        vods = []
        for v in videos:
            start = parse_dt(v.get("created_at"))
            vods.append({
                "platform": "twitch",
                "vod_id": str(v["id"]),
                "url": f"https://www.twitch.tv/videos/{v['id']}",
                "title": v.get("title") or "",
                "category": v.get("game_name") or "",
                "start_time": start,
                "duration_s": int(v.get("duration") or 0) if str(v.get("duration") or "").isdigit()
                             else _twitch_duration_s(v.get("duration") or ""),
                "views": v.get("view_count") or 0,
                # El VOD del directo en curso existe con la misma id que
                # el stream: en curso hasta que el directo termine.
                "live": bool(live and str(v["id"]) == str(live["id"])),
            })
        return live, vods


def _twitch_duration_s(raw):
    """'7h13m22s' de Helix -> segundos."""
    import re
    total = 0
    for num, unit in re.findall(r"(\d+)([hms])", str(raw)):
        total += int(num) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


class TwitchPublicGQL:
    """Ruta pública GQL que usa la propia web de Twitch (sin credenciales).

    No oficial: funciona hoy (probado 2026-11-16 con illojuan), pero
    Twitch puede cambiarla o bloquearla. Si falla, el watcher lo dice
    y Twitch queda en "no configurado" hasta poner credenciales Helix.
    """

    def check(self, login):
        resp = http_json(TWITCH_GQL_URL, data=[{
            "operationName": None,
            "query": TWITCH_GQL_QUERY,
            "variables": {"login": login},
        }], headers={"Client-ID": TWITCH_GQL_CLIENT_ID})
        if not isinstance(resp, list) or not resp:
            raise WatcherError("Twitch GQL: respuesta vacía.")
        data = (resp[0].get("data") or {})
        user = data.get("user")
        if not user:
            raise WatcherError(f"Twitch GQL: no existe el usuario {login!r}.")
        stream = user.get("stream") or {}
        live = None
        if stream.get("type") == "live":
            live = {"id": str(stream.get("id") or ""),
                    "title": stream.get("title") or "",
                    "game": (stream.get("game") or {}).get("name") or "",
                    "viewers": stream.get("viewersCount") or 0,
                    "started_at": stream.get("createdAt") or ""}
        vods = []
        edges = ((user.get("videos") or {}).get("edges") or [])
        live_started = parse_dt(live["started_at"]) if live else None
        for edge in edges:
            node = edge.get("node") or {}
            vid = str(node.get("id") or "")
            if not vid:
                continue
            start = parse_dt(node.get("createdAt"))
            # En GQL el id del stream NO es el id del VOD (en Helix sí).
            # El VOD del directo en curso es el que empezó a la vez que
            # el stream (mismo título, createdAt a segundos del inicio).
            is_live_vod = False
            if live and start and live_started:
                is_live_vod = abs((start - live_started).total_seconds()) <= 120
            vods.append({
                "platform": "twitch",
                "vod_id": vid,
                "url": f"https://www.twitch.tv/videos/{vid}",
                "title": node.get("title") or "",
                "category": (node.get("game") or {}).get("name") or "",
                "start_time": start,
                "duration_s": int(node.get("lengthSeconds") or 0),
                "views": node.get("viewCount") or 0,
                "live": is_live_vod,
            })
        return live, vods


def twitch_client(cfg):
    """Helix si hay credenciales, si no GQL público. Devuelve (cliente, modo)."""
    if cfg["twitch_client_id"] and cfg["twitch_client_secret"]:
        return TwitchHelix(cfg["twitch_client_id"], cfg["twitch_client_secret"]), "helix"
    return TwitchPublicGQL(), "gql-publico (sin credenciales)"


# ---------------------------------------------------------------------------
# Kick (API pública, sin key; reutiliza kick_downloader.core)
# ---------------------------------------------------------------------------

def kick_check(slug):
    """Devuelve (live_dict_o_None, [vods]) para un canal de Kick."""
    try:
        from kick_downloader.core import http_json as kick_http_json
        from kick_downloader.core import parse_kick_datetime
    except ImportError as e:
        raise WatcherError(f"No se pudo importar kick_downloader.core: {e}")
    channel = kick_http_json(f"https://kick.com/api/v2/channels/{slug}")
    ls = channel.get("livestream") or {}
    live = None
    live_id = None
    if ls.get("is_live"):
        cats = ls.get("categories") or []
        live_id = ls.get("id")
        live = {"id": str(live_id or ""),
                "title": ls.get("session_title") or "",
                "game": cats[0].get("name") if cats else "",
                "viewers": ls.get("viewer_count") or 0,
                "started_at": ls.get("created_at") or ""}
    videos = kick_http_json(f"https://kick.com/api/v2/channels/{slug}/videos")
    if not isinstance(videos, list):
        raise WatcherError(f"Kick: el listado de vídeos de {slug!r} no es una lista.")
    vods = []
    for item in videos:
        uuid = (item.get("video") or {}).get("uuid") or ""
        if not uuid:
            continue
        cats = item.get("categories") or []
        duration_ms = item.get("duration") or 0
        # El listado trae duration en ms (0 = directo en curso).
        duration_s = int(duration_ms / 1000) if duration_ms else 0
        vods.append({
            "platform": "kick",
            "vod_id": f"kick-{slug}-{uuid[:8]}",
            "url": f"https://kick.com/{slug}/videos/{uuid}",
            "title": item.get("session_title") or "",
            "category": cats[0].get("name") if cats else "",
            "start_time": parse_kick_datetime(item["start_time"])
                          if item.get("start_time") else None,
            "duration_s": duration_s,
            "views": item.get("views") or 0,
            "live": bool(live_id is not None and item.get("id") == live_id) or duration_s == 0,
        })
    return live, vods


# ---------------------------------------------------------------------------
# Decisión por VOD (frenos)
# ---------------------------------------------------------------------------

def decide(vod, influencer, state, queue, cfg, kill_switch, dry_run):
    """Devuelve (accion, motivo) para un VOD terminado.

    accion: 'enqueue' | 'would_enqueue' | 'skip' | 'seen'.
    """
    vod_id = vod["vod_id"]
    prev = state["vods"].get(vod_id)
    if prev:
        outcome = prev.get("outcome") or ""
        if outcome == "enqueued":
            return "seen", "ya encolado antes (job #%s)" % prev.get("job_id")
        if outcome == f"skipped:{PERMANENT_SKIP}":
            return "seen", "ya visto: juego fuera de la lista (definitivo)"
        if dry_run:
            if outcome == "skipped:vod_en_curso":
                pass  # re-evaluar: el VOD pudo terminar desde la última pasada
            else:
                return "seen", f"ya visto ({outcome})"
        elif outcome.startswith("skipped:") and outcome[8:] not in RETRYABLE_SKIPS:
            return "seen", "ya visto (%s)" % outcome[8:]
        # En --live se re-evalúan los saltos reintentables (permiso,
        # kill-switch, tope, en curso, en processing...) y los VODs
        # marcados would_enqueue en un dry-run anterior.

    if kill_switch:
        return "skip", "kill_switch"
    # 2026-11-16: estar en la lista = permiso ya dado. Solo
    # `denegado` / `revocado` excluyen.
    if influencer["estado"] in ("denegado", "revocado"):
        return "skip", f"fuera_de_lista (estado: {influencer['estado']})"
    games, _, _ = load_games_config()
    allowed_ids = {(g.get("id") or g.get("name")) for g in games}
    game = detect_game(vod.get("category"), vod.get("title"), games)
    if game not in allowed_ids:
        return "skip", (f"juego_no_permitido (categoría: "
                        f"{vod.get('category') or 'sin categoría'})")
    today = date.today().isoformat()
    key = f"{influencer['usuario']}|{today}"
    used = state["daily"].get(key, 0)
    if used >= cfg["max_new_per_day"]:
        return "skip", (f"tope_diario ({used}/{cfg['max_new_per_day']} "
                        "VODs hoy)")
    if queue is not None:
        processing = [j for j in queue.list_jobs("processing")
                      if (j.get("streamer") or "").lower() == influencer["usuario"]]
        if processing:
            return "skip", (f"en_processing (job #{processing[0]['id']} "
                            "de este influencer ya en processing)")
    return ("would_enqueue" if dry_run else "enqueue"), ""


def record_vod(state, vod, influencer, outcome, job_id=None):
    """Guarda/actualiza un VOD en seen_vods.json."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rec = state["vods"].get(vod["vod_id"]) or {}
    rec.update({
        "streamer": influencer["usuario"],
        "platform": vod["platform"],
        "title": vod.get("title") or "",
        "category": vod.get("category") or "",
        "game": detect_game(vod.get("category"), vod.get("title")),
        "start_time": vod["start_time"].isoformat() if vod.get("start_time") else "",
        "duration_s": vod.get("duration_s") or 0,
        "url": vod.get("url") or "",
        "first_seen": rec.get("first_seen") or now,
        "last_seen": now,
        "live": bool(vod.get("live")),
        "outcome": outcome,
    })
    if job_id is not None:
        rec["job_id"] = job_id
    state["vods"][vod["vod_id"]] = rec


# ---------------------------------------------------------------------------
# Una pasada
# ---------------------------------------------------------------------------

def run_once(influencers, cfg, state_path, db_path, dry_run, only=None,
             verbose=True):
    """Una pasada del monitor. Devuelve (resumen_dict, n_errores)."""
    state = load_state(state_path)
    kill_switch = kill_switch_active()
    queue = None if dry_run else Queue(db_path)
    tw_client = None
    tw_mode = None
    summary = {"live": 0, "new_vods": 0, "would_enqueue": 0,
               "enqueued": 0, "skipped": 0, "seen": 0, "errors": 0}
    # 2026-11-16: en la lista = permiso ya dado; solo denegado/revocado excluyen.
    conceded = {i["usuario"] for i in influencers
                if i["estado"] not in ("denegado", "revocado")}

    def log(msg):
        if verbose:
            print(msg)

    log(f"Watcher — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
        f"({'DRY-RUN (no encola nada)' if dry_run else 'LIVE (encola lo que pase los frenos)'})")
    if kill_switch:
        log("  OJO: kill-switch ACTIVO (existe queue.kill) — no se encola nada.")
    log(f"  Influencers vigilados: {len(influencers)} "
        f"({', '.join(i['usuario'] + ' [' + i['plataforma'] + ', ' + i['estado'] + ']' for i in influencers) or 'ninguno'})")
    if not influencers:
        log("  Lista blanca vacía: nada que vigilar. "
            "Agrega un influencer a streamers.yaml (clipagent add <link>).")
        return summary, 0

    for inf in influencers:
        if only and inf["usuario"] != only:
            continue
        slug = inf["usuario"]
        platform = inf["plataforma"]
        log(f"\n[{slug}] ({platform}, permiso: {inf['estado']})")
        try:
            if platform == "kick":
                live, vods = kick_check(slug)
                mode = "kick-api-publica"
            elif platform == "twitch":
                if tw_client is None:
                    tw_client, tw_mode = twitch_client(cfg)
                live, vods = tw_client.check(slug)
                mode = f"twitch-{tw_mode}"
            else:
                log(f"  Plataforma no soportada: {platform!r} (solo kick/twitch).")
                summary["errors"] += 1
                continue
        except WatcherError as e:
            log(f"  ERROR al consultar {platform}: {e}")
            summary["errors"] += 1
            continue
        except Exception as e:  # red, JSON roto, etc.: no tumbar la pasada
            log(f"  ERROR al consultar {platform}: {type(e).__name__}: {e}")
            summary["errors"] += 1
            continue

        log(f"  Fuente: {mode}")
        # --- en vivo: solo se REGISTRA, nunca se encola ---
        prev_live = state["live"].get(slug)
        if live:
            summary["live"] += 1
            game = detect_game(live.get("game"), live.get("title"))
            log(f"  EN VIVO: {live['title']!r} | juego: {live['game'] or '?'} "
                f"(detectado: {game or 'sin verificar'}) | "
                f"{live['viewers']} espectadores | desde {live['started_at']}")
            log("    -> solo registro: no se encola nada hasta que termine "
                "el directo y salga el VOD.")
            state["live"][slug] = {
                "stream_id": live["id"], "title": live["title"],
                "game": live["game"], "viewers": live["viewers"],
                "started_at": live["started_at"],
                "last_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        else:
            if prev_live:
                log("  Directo TERMINADO (estaba en vivo en la pasada anterior). "
                    "El VOD se detecta abajo cuando aparezca.")
                state["live"].pop(slug, None)
            log("  No está en vivo.")

        # --- VODs: solo los terminados son candidatos ---
        vods_sorted = sorted(
            vods, key=lambda v: v.get("start_time") or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True)
        for vod in vods_sorted:
            start = vod.get("start_time")
            start_s = start.strftime("%Y-%m-%d %H:%M") if start else "sin fecha"
            dur = vod.get("duration_s") or 0
            dur_s = f"{dur // 3600}h{dur % 3600 // 60:02d}m" if dur else "en curso"
            if vod.get("live") or dur == 0:
                # VOD del directo en curso: se registra, no se encola.
                record_vod(state, vod, inf, "skipped:vod_en_curso")
                log(f"  VOD {vod['vod_id']} ({start_s}, {dur_s}): "
                    f"{vod.get('title') or '(sin título)'!r} "
                    f"[{vod.get('category') or '?'}] -> NO: directo en curso "
                    "(se encolará cuando termine, si pasa los frenos)")
                continue
            summary["new_vods"] += 1
            action, reason = decide(vod, inf, state, queue, cfg,
                                    kill_switch, dry_run)
            if action == "seen":
                summary["seen"] += 1
                log(f"  VOD {vod['vod_id']} ({start_s}, {dur_s}): ya visto — {reason}")
                record_vod(state, vod, inf,
                           state["vods"][vod["vod_id"]].get("outcome") or "seen")
                continue
            if action == "skip":
                summary["skipped"] += 1
                outcome = f"skipped:{reason.split(' ')[0]}"
                record_vod(state, vod, inf, outcome)
                log(f"  VOD {vod['vod_id']} ({start_s}, {dur_s}): "
                    f"{vod.get('title') or '(sin título)'!r} "
                    f"[{vod.get('category') or '?'}] -> NO: {reason}")
                continue
            if action == "would_enqueue":
                summary["would_enqueue"] += 1
                record_vod(state, vod, inf, "would_enqueue")
                log(f"  VOD {vod['vod_id']} ({start_s}, {dur_s}): "
                    f"{vod.get('title') or '(sin título)'!r} "
                    f"[{vod.get('category') or '?'}] -> ENCOLARÍA (dry-run, "
                    "no se toca la cola)")
                continue
            # action == "enqueue" (solo --live y con frenos pasados)
            try:
                job_id = queue.add(vod["url"], lang=inf["idioma"],
                                   streamer=slug, allowlist=conceded)
            except QueueError as e:
                summary["skipped"] += 1
                record_vod(state, vod, inf, "skipped:ya_en_cola")
                log(f"  VOD {vod['vod_id']} -> NO: la cola lo rechazó: {e}")
                continue
            summary["enqueued"] += 1
            key = f"{slug}|{date.today().isoformat()}"
            state["daily"][key] = state["daily"].get(key, 0) + 1
            record_vod(state, vod, inf, "enqueued", job_id=job_id)
            log(f"  VOD {vod['vod_id']} ({start_s}, {dur_s}): "
                f"{vod.get('title') or '(sin título)'!r} -> "
                f"ENCOLADO como job #{job_id} (queda en queued; el worker "
                "lo procesa y un humano lo revisa antes de publicar)")

    save_state(state_path, state)
    if queue is not None:
        queue.close()
    log(f"\nResumen: en vivo={summary['live']} | VODs revisados="
        f"{summary['new_vods']} | encolados={summary['enqueued']} | "
        f"encolaría (dry-run)={summary['would_enqueue']} | "
        f"descartados={summary['skipped']} | ya vistos={summary['seen']} | "
        f"errores={summary['errors']}")
    log(f"Estado guardado en {state_path}")
    return summary, summary["errors"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Monitor de influencers: detecta directos en vivo y "
                    "VODs nuevos, y encola los de GTA 6 con permiso "
                    "(ver WATCHER.md). Por defecto --dry-run.")
    p.add_argument("--once", action="store_true", default=True,
                   help="Una pasada y salir (defecto)")
    p.add_argument("--loop", action="store_true",
                   help="Bucle: una pasada cada poll_minutes de config.yaml")
    p.add_argument("--live", action="store_true",
                   help="Encolar de verdad lo que pase los frenos "
                   "(sin esto, --dry-run)")
    p.add_argument("--dry-run", action="store_true",
                   help="No tocar la cola (es el defecto)")
    p.add_argument("--streamer", help="Vigilar solo este influencer (slug)")
    p.add_argument("--config", help="Ruta de config.yaml")
    p.add_argument("--influencers", help="Ruta de streamers.yaml (o archivo viejo)")
    p.add_argument("--state", help="Ruta de seen_vods.json")
    p.add_argument("--db", help="Ruta de queue.db")
    p.add_argument("--interval-minutes", type=int,
                   help="Minutos entre pasadas en --loop (defecto: config)")
    args = p.parse_args(argv)

    dry_run = not args.live
    try:
        cfg = load_watcher_config(args.config)
    except WatcherError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if args.interval_minutes:
        cfg["poll_minutes"] = args.interval_minutes
    state_path = args.state or cfg["state_file"]
    if not os.path.isabs(state_path):
        state_path = os.path.join(HERE, state_path)
    db_path = args.db or os.path.join(HERE, "queue.db")
    try:
        influencers = load_influencers(args.influencers)
    except WatcherError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if args.streamer:
        only = args.streamer.strip().lower()
        if not any(i["usuario"] == only for i in influencers):
            print(f"Error: {only!r} no está en streamers.yaml.",
                  file=sys.stderr)
            return 1
    else:
        only = None

    if args.loop:
        print(f"Watcher en bucle cada {cfg['poll_minutes']} min "
              f"({'DRY-RUN' if dry_run else 'LIVE'}). Ctrl-C para parar.")
        while True:
            try:
                _, errors = run_once(influencers, cfg, state_path, db_path,
                                     dry_run, only=only)
            except KeyboardInterrupt:
                print("\nWatcher parado por el usuario.")
                return 0
            except Exception as e:
                print(f"Error en la pasada: {type(e).__name__}: {e}",
                      file=sys.stderr)
            try:
                time.sleep(cfg["poll_minutes"] * 60)
            except KeyboardInterrupt:
                print("\nWatcher parado por el usuario.")
                return 0
    _, errors = run_once(influencers, cfg, state_path, db_path, dry_run,
                         only=only)
    return 2 if errors else 0


if __name__ == "__main__":
    # DEPRECADO (Fase 5.2): usa el CLI único. Se mantiene
    # como wrapper para no romper cron/scripts viejos.
    import sys as _sys
    print("aviso: watcher.py está deprecado; usa `clipagent watch`",
          file=_sys.stderr)
    raise SystemExit(main())
