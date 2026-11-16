#!/usr/bin/env python3
"""
queue.py — cola FIFO persistente de viral-clip-agent (bloque 1 del tribunal).

Un VOD entra en la cola, el worker (worker.py) lo procesa con
auto_clips.py y el clip queda en ``ready_for_review`` hasta que un
humano lo aprueba o lo rechaza. Nada se publica desde aquí: publicar
es otro bloque, con su propia aprobación.

Persistencia: SQLite en ``queue.db`` (gitignored), nunca solo en
memoria. Si el proceso muere, la cola sigue ahí.

Estados:
    queued -> processing -> ready_for_review -> approved -> published
                                          -> rejected
    processing -> queued (reintento transitorio)
    processing -> failed (fallo permanente)
    processing -> dead (3 reintentos transitorios agotados)

Reglas de entrada (se verifican en ``add``):
  * El streamer tiene que estar en la lista blanca de ``config.yaml``
    (``allowlist``). Hoy está vacía: sin ficha no se encola. Con
    ``--force`` se encola igual para pruebas locales, pero queda
    marcado (``allowlist_bypass``) y el semáforo lo pone en rojo.
  * Dedup por vod_id + idioma: no hay dos jobs activos del mismo VOD
    en el mismo idioma.
  * Disco: si quedan menos de 2 GB libres, no se encola nada.

Semáforo (``check_semaphore``): un job en ``ready_for_review`` pasa
a rojo —no aprobable sin ``--force-confirm`` explícito— si:
  * el streamer no tiene permiso (no está en la lista blanca),
  * el juego detectado no es GTA 6 (o no se puede verificar),
  * el VOD es anterior al lanzamiento de GTA 6 (19 nov 2026),
  * el título o la categoría hacen sospechar música con copyright,
  * el manifiesto no trae clips, falta un archivo o un clip no trae
    su decisión de layout, o el paso de subtítulos quedó marcado
    como fallido.

Kill-switch: si existe el archivo ``queue.kill`` en esta carpeta, el
worker se pausa inmediatamente. Se reanuda borrando el archivo.

Uso:
    python queue.py add <url> --lang es [--force] [--streamer coker]
    python queue.py list [--state queued]
    python queue.py approve <id> [--force-confirm]
    python queue.py reject <id>
    python queue.py status
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "queue.db")
KILL_SWITCH_PATH = os.path.join(HERE, "queue.kill")
RUNS_DIR = os.path.join(HERE, "runs")

MIN_FREE_GB = 2.0
MAX_ATTEMPTS = 3
GTA6_LAUNCH = date(2026, 11, 19)  # jueves 19 nov 2026

STATES = ("queued", "processing", "ready_for_review", "approved",
          "rejected", "published", "failed", "dead")
# Dedup: no puede haber dos jobs ACTIVOS del mismo VOD + idioma.
ACTIVE_STATES = ("queued", "processing", "ready_for_review",
                 "approved", "published")

MUSIC_KEYWORDS = ("music", "música", "musica", "dj", "karaoke",
                  "concierto", "concert", "radio", "spotify",
                  "cantando", "singing")

# Patrones para detectar el juego a partir de la categoría/título del VOD.
GTA6_PATTERNS = ("gta 6", "gta vi", "gta6", "grand theft auto vi",
                 "grand theft auto 6")
GTA_OTHER_PATTERNS = ("gta 5", "gta v", "gta5", "grand theft auto v",
                      "grand theft auto 5", "gta online", "gta rp",
                      "gta roleplay")


class QueueError(Exception):
    """Error de validación o de transición de la cola (mensaje para el usuario)."""


# ---------------------------------------------------------------------------
# Utilidades de URL / juego / disco
# ---------------------------------------------------------------------------

def parse_url(url: str):
    """Devuelve (platform, vod_id, streamer) a partir de una URL o un ID.

    Kick: https://kick.com/<canal>/videos/<uuid> -> vod_id kick-<canal>-<uuid[:8]>
    Twitch: https://twitch.tv/videos/<id> o el ID pelado.
    """
    url = (url or "").strip()
    m = re.search(r"kick\.com/([^/]+)/videos/([0-9a-fA-F-]{36})", url)
    if m:
        slug, uuid = m.group(1).lower(), m.group(2).lower()
        return "kick", f"kick-{slug}-{uuid[:8]}", slug
    m = re.search(r"videos/(\d+)", url)
    if m:
        return "twitch", m.group(1), None
    if re.fullmatch(r"\d+", url):
        return "twitch", url, None
    raise QueueError(
        f"No reconozco el VOD: {url!r}. Usa una URL kick.com/<canal>/videos/<uuid>, "
        "twitch.tv/videos/<id> o el ID de Twitch.")


def detect_game(category: str | None, title: str | None):
    """'gta6' si el VOD es de GTA 6, 'other' si es de otro juego, None si no se sabe."""
    text = f"{category or ''} {title or ''}".lower()
    if any(p in text for p in GTA6_PATTERNS):
        return "gta6"
    if any(p in text for p in GTA_OTHER_PATTERNS):
        return "other"
    if "gta" in text:
        return "other"
    if (category or "").strip() or (title or "").strip():
        return "other"
    return None


def disk_free_gb(path: str) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / (1024 ** 3)


def kill_switch_active(path: str = KILL_SWITCH_PATH) -> bool:
    """True si existe el archivo kill-switch (el worker debe pararse)."""
    return os.path.exists(path)


def load_allowlist(config_path: str | None = None):
    """Streamers con permiso, desde ``allowlist`` en config.yaml.

    Vacía (o ausente) = nadie tiene permiso todavía. Se compara en
    minúsculas y sin espacios.
    """
    try:
        from config import load_config
        cfg = load_config(config_path)
    except Exception:
        return set()
    raw = (cfg or {}).get("allowlist") or []
    if isinstance(raw, str):
        raw = [raw]
    return {str(s).strip().lower() for s in raw if str(s).strip()}


# ---------------------------------------------------------------------------
# Cola (SQLite)
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    vod_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    streamer TEXT,
    lang TEXT NOT NULL DEFAULT 'es',
    state TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    out_dir TEXT NOT NULL,
    manifest_path TEXT,
    error TEXT,
    error_kind TEXT,
    red_flags TEXT,
    vod_title TEXT,
    vod_category TEXT,
    vod_start_time TEXT,
    subtitles_failed INTEGER NOT NULL DEFAULT 0,
    allowlist_bypass INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_dedup_active
    ON jobs (vod_id, lang) WHERE state IN
    ('queued', 'processing', 'ready_for_review', 'approved', 'published');
CREATE TABLE IF NOT EXISTS job_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_log_job ON job_log (job_id);
"""


class Queue:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _now():
        return datetime.now().isoformat(timespec="seconds")

    def _log(self, job_id: int, event: str, detail: str = ""):
        self.conn.execute(
            "INSERT INTO job_log (job_id, ts, event, detail) VALUES (?, ?, ?, ?)",
            (job_id, self._now(), event, detail))
        self.conn.commit()

    def _row_to_dict(self, row):
        d = dict(row)
        if d.get("red_flags"):
            try:
                d["red_flags"] = json.loads(d["red_flags"])
            except json.JSONDecodeError:
                d["red_flags"] = [d["red_flags"]]
        else:
            d["red_flags"] = []
        return d

    def get_job(self, job_id: int):
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def set_state(self, job_id: int, state: str, **fields):
        if state not in STATES:
            raise QueueError(f"Estado desconocido: {state!r}")
        fields["state"] = state
        fields["updated_at"] = self._now()
        if "red_flags" in fields and isinstance(fields["red_flags"], list):
            fields["red_flags"] = json.dumps(fields["red_flags"], ensure_ascii=False)
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE jobs SET {cols} WHERE id = ?",
            (*fields.values(), job_id))
        self.conn.commit()

    def log(self, job_id: int, event: str, detail: str = ""):
        self._log(job_id, event, detail)

    # -- operaciones -----------------------------------------------------
    def add(self, url: str, lang: str = "es", streamer: str | None = None,
            force: bool = False, allowlist: set | None = None,
            min_free_gb: float = MIN_FREE_GB):
        """Encola un VOD. Valida lista blanca, dedup y disco.

        ``force=True`` salta SOLO la lista blanca (pruebas locales) y
        deja el job marcado: el semáforo lo pondrá en rojo y no se
        podrá aprobar sin --force-confirm.
        """
        platform, vod_id, slug = parse_url(url)
        streamer = (streamer or slug or "").strip().lower() or None
        allowlist = load_allowlist() if allowlist is None else allowlist

        if not force:
            if streamer is None:
                raise QueueError(
                    "No sé quién es el streamer de este VOD (la URL de Twitch "
                    "no lo dice). Pasa --streamer <canal> o usa una URL de Kick.")
            if streamer not in allowlist:
                raise QueueError(
                    f"Streamer sin permiso: '{streamer}' no está en la lista "
                    "blanca de config.yaml (allowlist). Sin ficha no se encola.")
        bypass = 1 if force else 0

        existing = self.conn.execute(
            "SELECT id, state FROM jobs WHERE vod_id = ? AND lang = ? "
            "AND state IN (%s)" % ",".join("?" * len(ACTIVE_STATES)),
            (vod_id, lang, *ACTIVE_STATES)).fetchone()
        if existing:
            raise QueueError(
                f"Ya hay un job activo de este VOD ({vod_id}, lang={lang}): "
                f"#{existing['id']} en estado {existing['state']}.")

        free = disk_free_gb(RUNS_DIR if os.path.isdir(RUNS_DIR) else HERE)
        if free < min_free_gb:
            raise QueueError(
                f"Disco casi lleno: quedan {free:.2f} GB libres, hacen falta "
                f"{min_free_gb:.0f} GB. No se encola nada hasta liberar espacio.")

        out_dir = os.path.join(RUNS_DIR, vod_id)
        now = self._now()
        cur = self.conn.execute(
            "INSERT INTO jobs (url, vod_id, platform, streamer, lang, state,"
            " attempts, max_attempts, out_dir, allowlist_bypass, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, ?)",
            (url, vod_id, platform, streamer, lang, MAX_ATTEMPTS,
             out_dir, bypass, now, now))
        job_id = cur.lastrowid
        self._log(job_id, "added",
                  f"url={url} lang={lang} bypass={'sí' if bypass else 'no'} "
                  f"(disco libre: {free:.1f} GB)")
        return job_id

    def next_queued(self):
        """El job más antiguo en 'queued' (FIFO)."""
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE state = 'queued' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def requeue_stale_processing(self):
        """Jobs en 'processing' de un worker que murió (crash/reinicio).

        Solo se llama con el lock del worker tomado, así que ningún
        worker vivo los está procesando: vuelven a 'queued' sin gastar
        intento. Devuelve cuántos se recuperaron.
        """
        rows = self.conn.execute(
            "SELECT id FROM jobs WHERE state = 'processing'").fetchall()
        for r in rows:
            self.set_state(r["id"], "queued")
            self._log(r["id"], "requeued_stale",
                      "el worker anterior murió con el job en processing; "
                      "vuelve a queued sin gastar intento")
        return len(rows)

    def list_jobs(self, state: str | None = None):
        q = "SELECT * FROM jobs"
        args: tuple = ()
        if state:
            q += " WHERE state = ?"
            args = (state,)
        q += " ORDER BY id ASC"
        return [self._row_to_dict(r)
                for r in self.conn.execute(q, args).fetchall()]

    def counts(self):
        rows = self.conn.execute(
            "SELECT state, COUNT(*) n FROM jobs GROUP BY state").fetchall()
        return {r["state"]: r["n"] for r in rows}

    def approve(self, job_id: int, force_confirm: bool = False,
                allowlist: set | None = None):
        """Aprueba un job en ready_for_review.

        Con rojos en el semáforo se niega salvo --force-confirm
        explícito (que queda registrado en el log del job).
        """
        job = self.get_job(job_id)
        if job is None:
            raise QueueError(f"No existe el job #{job_id}.")
        if job["state"] != "ready_for_review":
            raise QueueError(
                f"El job #{job_id} está en {job['state']}, no en "
                "ready_for_review: no se puede aprobar.")
        reds = check_semaphore(job, allowlist=allowlist)
        if reds and not force_confirm:
            raise QueueError(
                f"El job #{job_id} tiene {len(reds)} rojo(s) en el semáforo "
                "y no se puede aprobar sin --force-confirm:\n" +
                "\n".join(f"  - {r}" for r in reds))
        self.set_state(job_id, "approved", red_flags=reds)
        self._log(job_id, "approved",
                  "force-confirm" if (reds and force_confirm)
                  else "sin rojos" if not reds
                  else f"con {len(reds)} rojo(s), force-confirm")
        return reds

    def reject(self, job_id: int, reason: str = ""):
        job = self.get_job(job_id)
        if job is None:
            raise QueueError(f"No existe el job #{job_id}.")
        if job["state"] != "ready_for_review":
            raise QueueError(
                f"El job #{job_id} está en {job['state']}, no en "
                "ready_for_review: no se puede rechazar.")
        self.set_state(job_id, "rejected")
        self._log(job_id, "rejected", reason)
        return job


# ---------------------------------------------------------------------------
# Semáforo
# ---------------------------------------------------------------------------

def check_semaphore(job: dict, allowlist: set | None = None,
                    manifest: dict | None = None):
    """Rojos de un job en ready_for_review. Lista vacía = aprobable.

    ``job`` es el dict de la BD (ver Queue.get_job). Si no se pasa el
    manifiesto, se lee de job['manifest_path'] si existe.
    """
    reds = []
    allowlist = load_allowlist() if allowlist is None else allowlist
    streamer = (job.get("streamer") or "").lower()

    # 1. Streamer sin permiso (o encolado con bypass de la lista blanca).
    if job.get("allowlist_bypass"):
        reds.append(
            f"streamer sin permiso: '{streamer or '?'}' se encoló con "
            "--force (bypass de la lista blanca) y no está en allowlist")
    elif streamer and streamer not in allowlist:
        reds.append(
            f"streamer sin permiso: '{streamer}' no está en la lista "
            "blanca de config.yaml (allowlist)")

    # 2. Juego detectado distinto de GTA 6 (o no verificable).
    game = detect_game(job.get("vod_category"), job.get("vod_title"))
    if game is None:
        reds.append("juego no verificable: sin categoría ni título del VOD")
    elif game != "gta6":
        reds.append(
            f"juego detectado ≠ GTA 6 (categoría: "
            f"{job.get('vod_category') or 'sin categoría'})")

    # 3. VOD anterior al lanzamiento de GTA 6 (19 nov 2026).
    raw_date = job.get("vod_start_time")
    vod_date = None
    if raw_date:
        try:
            vod_date = datetime.fromisoformat(raw_date).date()
        except ValueError:
            vod_date = None
    if vod_date is None:
        reds.append("fecha del VOD no verificable (sin start_time)")
    elif vod_date < GTA6_LAUNCH:
        reds.append(
            f"VOD anterior al lanzamiento de GTA 6 (19 nov 2026): {vod_date}")

    # 4. Música con copyright sospechada (título o categoría).
    haystack = f"{job.get('vod_title') or ''} {job.get('vod_category') or ''}".lower()
    hit = next((k for k in MUSIC_KEYWORDS if k in haystack), None)
    if hit:
        reds.append(
            f"música con copyright sospechada (título/categoría menciona "
            f"'{hit}')")

    # 5. Fallo de layout o de subtítulos (lo que dejó el worker).
    if manifest is None and job.get("manifest_path"):
        try:
            with open(job["manifest_path"], encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError):
            manifest = None
    if manifest is None:
        reds.append("sin manifiesto: no hay manifest.json del worker")
    else:
        clips = manifest.get("clips") or []
        if not clips:
            reds.append("sin clips generados (el manifiesto viene vacío)")
        base = os.path.dirname(job.get("manifest_path") or "")
        for c in clips:
            rel = c.get("file", "")
            if rel and not os.path.isfile(os.path.join(base, rel)):
                reds.append(f"falta el archivo del clip {c.get('n')}: {rel}")
            if not (c.get("layout") or {}).get("segments"):
                reds.append(
                    f"clip {c.get('n')} sin decisión de layout registrada")
    if job.get("subtitles_failed"):
        reds.append("subtítulos fallidos (marcado por el worker)")

    return reds


# ---------------------------------------------------------------------------
# Clasificación de errores (reintentos)
# ---------------------------------------------------------------------------

TRANSIENT_HINTS = ("connection", "timeout", "timed out", "temporary",
                   "502", "503", "429", "reset by peer", "network",
                   "unreachable", "econnreset", "broken pipe",
                   "failed to fetch", "name resolution", "ssl")
PERMANENT_HINTS = ("no encontré el vod", "borrado o expirado",
                   "no reconozco el vod", "sin clips generados",
                   "chat vacío", "invalid url", "not found")


def classify_error(text: str) -> str:
    """'transient' (red/descarga: reintentar, máx 3) o 'permanent'."""
    low = (text or "").lower()
    if any(h in low for h in PERMANENT_HINTS):
        return "permanent"
    if any(h in low for h in TRANSIENT_HINTS):
        return "transient"
    # Por defecto, un fallo de auto_clips sin pista clara es permanente:
    # reintentar a ciegas no arregla un VOD mal formado o un corte roto.
    return "permanent"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_job(job: dict):
    reds = job.get("red_flags") or []
    print(f"#{job['id']:>3} [{job['state']:<16}] {job['vod_id']} "
          f"({job['platform']}, lang={job['lang']}, "
          f"streamer={job['streamer'] or '?'}, intentos={job['attempts']})")
    if job.get("error"):
        print(f"      error ({job.get('error_kind') or '?'}): "
              f"{job['error'][:200]}")
    if reds:
        print(f"      rojos ({len(reds)}):")
        for r in reds:
            print(f"        - {r}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Cola FIFO persistente de clips (SQLite, queue.db).")
    p.add_argument("--db", default=DB_PATH, help="Ruta de la base de datos")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("add", help="Encolar un VOD")
    pa.add_argument("url", help="URL kick.com/<canal>/videos/<uuid>, "
                    "twitch.tv/videos/<id> o ID de Twitch")
    pa.add_argument("--lang", choices=["es", "en", "both"], default="es")
    pa.add_argument("--streamer", help="Canal del streamer (si la URL no lo dice)")
    pa.add_argument("--force", action="store_true",
                    help="Saltar la lista blanca (solo pruebas locales; "
                    "el semáforo lo marcará en rojo)")

    pl = sub.add_parser("list", help="Listar jobs")
    pl.add_argument("--state", choices=list(STATES))

    pap = sub.add_parser("approve", help="Aprobar un job en ready_for_review")
    pap.add_argument("job_id", type=int)
    pap.add_argument("--force-confirm", action="store_true",
                     help="Aprobar aunque el semáforo tenga rojos "
                     "(queda registrado)")

    pr = sub.add_parser("reject", help="Rechazar un job en ready_for_review")
    pr.add_argument("job_id", type=int)
    pr.add_argument("--reason", default="")

    sub.add_parser("status", help="Resumen de la cola")

    args = p.parse_args(argv)
    q = Queue(args.db)
    try:
        if args.cmd == "add":
            job_id = q.add(args.url, lang=args.lang,
                           streamer=args.streamer, force=args.force)
            job = q.get_job(job_id)
            print(f"Encolado: job #{job_id} ({job['vod_id']}, "
                  f"{job['platform']}, lang={job['lang']})")
            if job["allowlist_bypass"]:
                print("  OJO: encolado con --force (bypass de la lista "
                      "blanca); el semáforo lo marcará en rojo.")
            return 0
        if args.cmd == "list":
            jobs = q.list_jobs(args.state)
            if not jobs:
                print("Cola vacía" + (f" (estado {args.state})"
                                      if args.state else "") + ".")
                return 0
            for job in jobs:
                _print_job(job)
            return 0
        if args.cmd == "approve":
            reds = q.approve(args.job_id, force_confirm=args.force_confirm)
            print(f"Job #{args.job_id} aprobado"
                  + (f" con {len(reds)} rojo(s) (force-confirm)."
                     if reds else "."))
            return 0
        if args.cmd == "reject":
            q.reject(args.job_id, reason=args.reason)
            print(f"Job #{args.job_id} rechazado.")
            return 0
        if args.cmd == "status":
            counts = q.counts()
            total = sum(counts.values())
            print(f"Jobs: {total}")
            for s in STATES:
                if counts.get(s):
                    print(f"  {s:<16} {counts[s]}")
            free = disk_free_gb(RUNS_DIR if os.path.isdir(RUNS_DIR) else HERE)
            print(f"Disco libre: {free:.1f} GB (mínimo para encolar: "
                  f"{MIN_FREE_GB:.0f} GB)")
            print(f"Kill-switch (queue.kill): "
                  f"{'ACTIVO — worker pausado' if kill_switch_active() else 'no'}")
            lock_path = os.path.join(HERE, "worker.lock")
            print(f"Worker: {'corriendo (worker.lock existe)' if os.path.exists(lock_path) else 'parado'}")
            return 0
    except QueueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        q.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
