"""Métricas de clips publicados (Fase 9.1).

Vistas a 24 y 48 h por clip (y a 7 días, que alimentan
priority.recalibrate, 6B.7) en SQLite ``metrics.db`` (gitignored),
con un reporte simple en el panel.

Donde la API lo permite:
- YouTube: videos.list(part=statistics) con el token OAuth de
  tokens/ (1 unidad por consulta). Si el token venció, se renueva
  antes (refresh.py).
- TikTok / Instagram / Facebook: sin consulta de métricas cableada
  todavía (la app de TikTok no está auditada; Instagram/Facebook
  exigen App Review para leer insights): esos clips quedan
  pendientes, nunca rompen la recolección de los demás.

La recolección nunca levanta excepciones: un fallo de red o de
token deja la fila pendiente para la próxima pasada.
"""
import json
import os
import sqlite3
import urllib.request
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRICS_DB = os.path.join(BASE_DIR, "metrics.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS clip_metrics (
    clip TEXT NOT NULL,
    platform TEXT NOT NULL,
    platform_id TEXT,
    published_at TEXT,
    views_24h INTEGER,
    views_48h INTEGER,
    views_7d INTEGER,
    measured_at TEXT,
    PRIMARY KEY (clip, platform)
);
"""

WINDOWS = (("views_24h", 24), ("views_48h", 48), ("views_7d", 24 * 7))

YOUTUBE_STATS_URL = "https://www.googleapis.com/youtube/v3/videos"


def _connect(db_path=None):
    path = db_path or METRICS_DB
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def record_publication(clip, platform, platform_id=None,
                       published_at=None, db_path=None):
    """Anota un clip publicado (lo llama el bucle al publicar)."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO clip_metrics "
            "(clip, platform, platform_id, published_at) "
            "VALUES (?, ?, ?, ?)",
            (clip, platform, platform_id,
             published_at or datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()


def _age_hours(published_at, now):
    if not published_at:
        return 0.0
    try:
        dt = datetime.fromisoformat(
            str(published_at).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def fetch_youtube_views(video_id, request_fn=None):
    """Vistas de un vídeo de YouTube por la Data API v3, o None.

    Usa el token OAuth guardado (lo renueva si venció). Sin token,
    devuelve None (pendiente, no es un error).
    """
    from . import credentials as _creds
    from .refresh import refresh_platform
    token = _creds.load_token("youtube_shorts")
    if token is None:
        return None
    if _creds.token_expired(token):
        ok, _msg = refresh_platform("youtube_shorts")
        if not ok:
            return None
        token = _creds.load_token("youtube_shorts")
        if token is None:
            return None
    access = token.get("access_token")
    if not access or not video_id:
        return None
    url = (f"{YOUTUBE_STATS_URL}?part=statistics&id={video_id}")
    try:
        if request_fn is not None:
            status, payload = request_fn(
                "GET", url, {"Authorization": f"Bearer {access}"}, None)
        else:
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {access}"},
                method="GET")
            with urllib.request.urlopen(req, timeout=30) as resp:
                status, payload = resp.status, json.loads(
                    resp.read().decode("utf-8", "ignore") or "{}")
    except Exception:  # noqa: BLE001 - métricas nunca rompen nada
        return None
    if status != 200:
        return None
    try:
        items = payload.get("items") or []
        return int(items[0]["statistics"]["viewCount"])
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def _fetch_views(platform, platform_id, request_fn=None):
    if platform == "youtube_shorts":
        return fetch_youtube_views(platform_id, request_fn=request_fn)
    # TikTok / Instagram / Facebook: sin consulta cableada (9.1).
    return None


def collect(db_path=None, now=None, request_fn=None):
    """Mide lo vencido: 24 h, 48 h y 7 d por clip publicado.

    Devuelve {"measured": n, "pending": n, "failed": n}. Nunca
    levanta: cada fallo queda pendiente para la próxima pasada.
    """
    now = now or datetime.now(timezone.utc)
    path = db_path or METRICS_DB
    if not os.path.isfile(path):
        return {"measured": 0, "pending": 0, "failed": 0}
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT clip, platform, platform_id, published_at, "
            "views_24h, views_48h, views_7d FROM clip_metrics"
        ).fetchall()
    finally:
        conn.close()
    measured = pending = failed = 0
    for clip, platform, platform_id, published_at, v24, v48, v7d in rows:
        age = _age_hours(published_at, now)
        current = {"views_24h": v24, "views_48h": v48, "views_7d": v7d}
        due = [col for col, hours in WINDOWS
               if age >= hours and current[col] is None]
        if not due:
            pending += 1
            continue
        if not platform_id:
            failed += 1
            continue
        views = _fetch_views(platform, platform_id,
                             request_fn=request_fn)
        if views is None:
            failed += 1
            continue
        conn = _connect(db_path)
        try:
            sets = ", ".join(f"{col} = ?" for col in due)
            conn.execute(
                f"UPDATE clip_metrics SET {sets}, measured_at = ? "
                "WHERE clip = ? AND platform = ?",
                (*[views] * len(due),
                 now.isoformat(), clip, platform))
            conn.commit()
        finally:
            conn.close()
        measured += 1
    return {"measured": measured, "pending": pending, "failed": failed}


def _median(values):
    values = sorted(values)
    if not values:
        return None
    n = len(values)
    mid = n // 2
    if n % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def report(db_path=None):
    """Resumen simple para el panel: por plataforma, clips medidos,
    mediana de vistas a 24/48 h y mejor clip (a 7 días si lo hay)."""
    path = db_path or METRICS_DB
    if not os.path.isfile(path):
        return {}
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT clip, platform, views_24h, views_48h, views_7d "
            "FROM clip_metrics").fetchall()
    finally:
        conn.close()
    by_platform = {}
    for clip, platform, v24, v48, v7d in rows:
        entry = by_platform.setdefault(
            platform, {"clips": 0, "v24": [], "v48": [], "best": None})
        entry["clips"] += 1
        if v24 is not None:
            entry["v24"].append(v24)
        if v48 is not None:
            entry["v48"].append(v48)
        best_views = v7d if v7d is not None else v48
        if best_views is not None and (
                entry["best"] is None
                or best_views > entry["best"]["views"]):
            entry["best"] = {"clip": clip, "views": best_views}
    out = {}
    for platform, entry in sorted(by_platform.items()):
        out[platform] = {
            "clips": entry["clips"],
            "medidos_24h": len(entry["v24"]),
            "mediana_24h": _median(entry["v24"]),
            "medidos_48h": len(entry["v48"]),
            "mediana_48h": _median(entry["v48"]),
            "mejor_clip": entry["best"],
        }
    return out
