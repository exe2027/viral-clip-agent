#!/usr/bin/env python3
"""jobs.py — tabla de jobs por PASO del cerebro (partición en workers).

Por qué hay DOS tablas de jobs en ``queue.db`` (y no es un duplicado):

* ``jobs`` (legacy, en ``jobqueue.py``) es la cola de VODs: un job =
  un VOD entero (``url``, ``vod_id``, ``out_dir``), con estados
  ``queued -> processing -> ready_for_review -> approved ...``. La
  usan el watcher, el worker monolítico y el panel. NO se toca: romper
  su esquema rompería esos flujos y sus tests.
* ``step_jobs`` (esta tabla) es la cola de PASOS: un job = un paso de
  un clip en una plataforma (``clip_id``, ``plataforma``, ``paso``),
  con estados ``pending -> running -> done/failed`` (y
  ``scheduled -> processing -> posted`` para publicación, más
  ``esperando_humano`` cuando solo un humano puede destrabarlo).
  Es la tabla que pide el plan de independencia §2-§4: workers +
  máquina de estados + reintentos + idempotencia, en SQLite hoy y
  en D1 después (el DDL de ``migrations/0001_step_jobs.sql`` es el
  mismo, sin sintaxis propia de SQLite).

Columnas (las pedidas en la especificación, más payload/result/error
para que el worker sea reanudable sin memoria del proceso):

    id, clip_id, plataforma, paso, estado, intentos,
    proximo_reintento, clave_anti_duplicado, created_at, updated_at
    (+ max_intentos, payload_json, result_json, error, error_kind,
    vod_job_id, prioridad)

Idempotencia: ``clave_anti_duplicado`` es UNIQUE. Para un paso normal
es ``sha256(paso|clip_id|plataforma|payload canónico)``: repetir el
mismo paso con los mismos datos devuelve el job existente, nunca crea
otro. Para ``publicar`` es ``sha256(publicar|clip_id|plataforma)``
(sin payload): un clip NO se publica dos veces en la misma
plataforma aunque cambie el título. Ver ``make_idempotency_key``.

Reintentos acotados (nunca a ciegas hasta el infinito): cada claim
suma 1 intento; un fallo transitorio vuelve a ``pending`` con
``proximo_reintento`` en backoff exponencial (60 s, 2 min, 4 min...
con tope 1 h); al agotar ``max_intentos`` (3 por defecto) el job
queda ``failed`` con motivo y hora. Un fallo permanente va directo
a ``failed``. Un fallo que solo un humano puede resolver (captcha,
credencial, aprobación) va a ``esperando_humano`` y NO se reintenta
solo: se reanuda con ``jobs resume``.

Recuperación tras caída: un job en ``running``/``processing`` cuyo
``updated_at`` lleva más de ``STALE_TIMEOUT_S`` (1 h) sin moverse
vuelve a ``pending`` SIN gastar intento (``requeue_stale``): el
worker que murió no lo estaba procesando. El supervisor lo llama
en cada tick, antes de pedir trabajo.

D1: solo SQL estándar (TEXT/INTEGER/REAL, índices normales y uno
UNIQUE). Sin ``ON CONFLICT`` (se usa INSERT OR IGNORE), sin
funciones propias de SQLite, sin triggers. Las fechas son TEXT ISO.
"""

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(HERE, "queue.db")

#: Pasos del pipeline. ``publicar`` es un paso por PLATAFORMA (un job
#: por clip y plataforma); lo crea la aprobación humana (ver
#: ``clipagent.workers.enqueue_publish``) o, desde 2026-11-18, la
#: cadena automática para TikTok al terminar ``empaquetar``.
STEPS = ("vod", "picos", "corte", "subtitulos", "empaquetar", "publicar")
#: Orden automático del pipeline (sin intervención por clip). Al
#: terminar ``empaquetar`` se auto-encola publicar en TikTok
#: (mode api); el resto de plataformas sigue manual.
PIPELINE = ("vod", "picos", "corte", "subtitulos", "empaquetar")
PLATFORMS = ("youtube_shorts", "tiktok", "instagram_reels",
             "facebook_reels")

#: Estados. ``esperando_humano`` es visible en el panel: el job no
#: avanza solo y no se reintenta; un humano lo reanuda o lo descarta.
STATES = ("pending", "running", "done", "failed", "scheduled",
          "processing", "posted", "esperando_humano")
TERMINAL_STATES = ("done", "failed", "posted")
#: Transiciones permitidas (cualquier otra levanta JobError).
TRANSITIONS = {
    "pending": {"running", "scheduled", "failed", "esperando_humano"},
    "scheduled": {"processing", "pending", "failed", "esperando_humano"},
    "running": {"done", "failed", "pending", "esperando_humano",
                "scheduled"},
    "processing": {"posted", "done", "failed", "pending",
                   "esperando_humano", "scheduled"},
    "esperando_humano": {"pending"},
    "done": set(),
    "posted": set(),
    "failed": {"pending"},
}

MAX_ATTEMPTS = 3
RETRY_BASE_S = 60          # 1er reintento a los 60 s
RETRY_MAX_S = 3600         # tope del backoff: 1 h
STALE_TIMEOUT_S = 3600     # running sin moverse 1 h = worker muerto

STEP_JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS step_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id TEXT NOT NULL,
    plataforma TEXT,
    paso TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pending',
    intentos INTEGER NOT NULL DEFAULT 0,
    max_intentos INTEGER NOT NULL DEFAULT 3,
    proximo_reintento TEXT,
    clave_anti_duplicado TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error TEXT,
    error_kind TEXT,
    vod_job_id INTEGER,
    prioridad REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_step_jobs_idempotency
    ON step_jobs (clave_anti_duplicado);
CREATE INDEX IF NOT EXISTS idx_step_jobs_due
    ON step_jobs (estado, paso, prioridad, created_at);
CREATE INDEX IF NOT EXISTS idx_step_jobs_clip
    ON step_jobs (clip_id, paso);
CREATE TABLE IF NOT EXISTS step_job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    step_job_id INTEGER NOT NULL REFERENCES step_jobs(id),
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_step_job_events_job
    ON step_job_events (step_job_id);
"""


class JobError(Exception):
    """Error de validación o de transición de la tabla step_jobs."""


def make_idempotency_key(clip_id, plataforma, paso, payload=None):
    """Clave anti-duplicado de un job de paso.

    Paso normal: incluye el payload canónico (mismos datos = mismo
    job; datos distintos = job nuevo, p. ej. re-cortar con otro
    inicio). Publicar: SOLO clip + plataforma, sin payload: un clip
    no se publica dos veces en la misma plataforma aunque cambie el
    título o la descripción (plan §5, anti-duplicado TikTok).
    """
    if paso == "publicar":
        raw = f"publicar|{clip_id}|{plataforma or ''}"
    else:
        canon = json.dumps(payload or {}, sort_keys=True,
                           ensure_ascii=False)
        raw = f"{paso}|{clip_id}|{plataforma or ''}|{canon}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def next_retry_at(intentos, now=None):
    """Backoff exponencial acotado: 60 s, 2 min, 4 min... tope 1 h."""
    now = now or datetime.now(timezone.utc)
    delay = min(RETRY_BASE_S * (2 ** max(0, intentos - 1)), RETRY_MAX_S)
    return (now + timedelta(seconds=delay)).isoformat(timespec="seconds")


class StepJobs:
    """Tabla ``step_jobs`` en SQLite (mismo archivo ``queue.db`` que
    la cola legacy de VODs, distinta tabla y distinto grano)."""

    TABLE = "step_jobs"

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(STEP_JOBS_SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _log(self, job_id: int, event: str, detail: str = ""):
        self.conn.execute(
            "INSERT INTO step_job_events (step_job_id, ts, event, detail)"
            " VALUES (?, ?, ?, ?)",
            (job_id, self._now(), event, detail))
        self.conn.commit()

    def _row_to_dict(self, row):
        d = dict(row)
        for key in ("payload_json", "result_json"):
            raw = d.get(key)
            if raw:
                try:
                    d[key.replace("_json", "")] = json.loads(raw)
                except json.JSONDecodeError:
                    d[key.replace("_json", "")] = {}
            else:
                d[key.replace("_json", "")] = None if key == "result_json" else {}
        d.pop("payload_json", None)
        d.pop("result_json", None)
        return d

    def get(self, job_id: int):
        row = self.conn.execute(
            "SELECT * FROM step_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def get_by_key(self, key: str):
        row = self.conn.execute(
            "SELECT * FROM step_jobs WHERE clave_anti_duplicado = ?",
            (key,)).fetchone()
        return self._row_to_dict(row) if row else None

    def events(self, job_id: int):
        rows = self.conn.execute(
            "SELECT * FROM step_job_events WHERE step_job_id = ?"
            " ORDER BY id ASC", (job_id,)).fetchall()
        return [dict(r) for r in rows]

    # -- creación --------------------------------------------------------
    def enqueue(self, clip_id: str, paso: str, plataforma: str | None = None,
                payload: dict | None = None, prioridad: float = 0.0,
                max_intentos: int = MAX_ATTEMPTS,
                vod_job_id: int | None = None):
        """Crea el job o devuelve el existente con la misma clave
        anti-duplicado (idempotente: llamar dos veces no duplica)."""
        if paso not in STEPS:
            raise JobError(f"Paso desconocido: {paso!r} (pasos: {STEPS})")
        if paso == "publicar" and not plataforma:
            raise JobError("publicar exige plataforma (un job por "
                           "clip y plataforma).")
        if plataforma and plataforma not in PLATFORMS:
            raise JobError(f"Plataforma desconocida: {plataforma!r}")
        payload = dict(payload or {})
        key = make_idempotency_key(clip_id, plataforma, paso, payload)
        existing = self.get_by_key(key)
        if existing:
            return existing
        now = self._now()
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO step_jobs (clip_id, plataforma, paso,"
            " estado, intentos, max_intentos, clave_anti_duplicado,"
            " payload_json, vod_job_id, prioridad, created_at, updated_at)"
            " VALUES (?, ?, ?, 'pending', 0, ?, ?, ?, ?, ?, ?, ?)",
            (clip_id, plataforma, paso, max_intentos, key,
             json.dumps(payload, ensure_ascii=False), vod_job_id,
             float(prioridad), now, now))
        self.conn.commit()
        if cur.lastrowid:
            job_id = cur.lastrowid
        else:  # carrera: otro proceso lo creó entre el SELECT y el INSERT
            existing = self.get_by_key(key)
            return existing
        self._log(job_id, "enqueued",
                  f"paso={paso} plataforma={plataforma or '-'} "
                  f"clip={clip_id}")
        return self.get(job_id)

    # -- transiciones ----------------------------------------------------
    def set_state(self, job_id: int, state: str, **fields):
        job = self.get(job_id)
        if job is None:
            raise JobError(f"No existe el step job #{job_id}.")
        if state not in STATES:
            raise JobError(f"Estado desconocido: {state!r}")
        if state not in TRANSITIONS.get(job["estado"], set()):
            raise JobError(
                f"Transición no permitida: {job['estado']} -> {state} "
                f"(job #{job_id}, paso {job['paso']}).")
        fields["estado"] = state
        fields["updated_at"] = self._now()
        if "payload" in fields:
            fields["payload_json"] = json.dumps(
                fields.pop("payload"), ensure_ascii=False)
        if "result" in fields:
            fields["result_json"] = json.dumps(
                fields.pop("result"), ensure_ascii=False)
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE step_jobs SET {cols} WHERE id = ?",
            (*fields.values(), job_id))
        self.conn.commit()
        return self.get(job_id)

    def claim(self, job_id: int):
        """Reclamo atómico: pending -> running (o scheduled ->
        processing en publicar). Devuelve el job o None si otro
        worker ya lo tomó. Suma 1 intento.

        Publicar siempre se reclama a ``processing`` (también desde
        pending): sus estados son scheduled/processing/posted, no
        running/done, así ``complete`` lo marca ``posted`` solo con
        evidencia de subida.
        """
        job = self.get(job_id)
        if job is None:
            return None
        target = None
        if job["paso"] == "publicar":
            if job["estado"] in ("pending", "scheduled"):
                target = "processing"
        elif job["estado"] == "pending":
            target = "running"
        if target is None:
            return None
        cur = self.conn.execute(
            "UPDATE step_jobs SET estado = ?, intentos = intentos + 1,"
            " updated_at = ? WHERE id = ? AND estado = ?",
            (target, self._now(), job_id, job["estado"]))
        self.conn.commit()
        if cur.rowcount != 1:
            return None
        claimed = self.get(job_id)
        self._log(job_id, "claimed",
                  f"{job['estado']} -> {target} "
                  f"(intento {claimed['intentos']}/{claimed['max_intentos']})")
        return claimed

    def complete(self, job_id: int, result: dict | None = None,
                 target: str | None = None):
        """running -> done, processing -> posted (publicar con
        evidencia). Solo desde el estado reclamado.

        ``target="done"`` permite que un publicar en modo dry-run
        (simulacro, sin subir nada) termine en ``done`` y no en
        ``posted``: ``posted`` solo se marca con evidencia real de
        subida."""
        job = self.get(job_id)
        if job is None:
            raise JobError(f"No existe el step job #{job_id}.")
        target = target or {"running": "done",
                            "processing": "posted"}.get(job["estado"])
        if target is None:
            raise JobError(
                f"No se puede completar un job en {job['estado']} "
                f"(job #{job_id}): primero hay que reclamarlo.")
        if target not in TRANSITIONS.get(job["estado"], set()):
            raise JobError(
                f"Transición no permitida: {job['estado']} -> {target} "
                f"(job #{job_id}).")
        done = self.set_state(job_id, target, result=result or {})
        self._log(job_id, target, "ok")
        return done

    def fail(self, job_id: int, error: str, kind: str = "transient"):
        """Registra un fallo.

        * ``human`` -> ``esperando_humano`` (captcha, credencial,
          aprobación): visible, sin reintento automático.
        * ``permanent`` -> ``failed`` directo.
        * ``transient``/``timeout`` con intentos restantes ->
          ``pending`` con ``proximo_reintento`` en backoff.
        * sin intentos -> ``failed`` (terminal, con motivo y hora).
        """
        job = self.get(job_id)
        if job is None:
            raise JobError(f"No existe el step job #{job_id}.")
        error = (error or "error sin mensaje")[:2000]
        if kind == "human":
            return self.wait_human(job_id, error)
        if kind in ("transient", "timeout"):
            if job["intentos"] < job["max_intentos"]:
                retry_at = next_retry_at(job["intentos"])
                done = self.set_state(
                    job_id, "pending", error=error, error_kind=kind,
                    proximo_reintento=retry_at)
                self._log(job_id, "retry_scheduled",
                          f"{kind} (intento {job['intentos']}/"
                          f"{job['max_intentos']}): reintento {retry_at}: "
                          f"{error[:300]}")
                return done
        done = self.set_state(job_id, "failed", error=error,
                              error_kind=kind, proximo_reintento=None)
        self._log(job_id, "failed",
                  f"{kind} tras {job['intentos']} intento(s): "
                  f"{error[:300]}")
        return done

    def wait_human(self, job_id: int, motivo: str, result: dict | None = None):
        """running/processing -> esperando_humano. No se reintenta
        solo; el panel lo muestra con motivo y hora, y un humano lo
        reanuda (``resume``) o lo descarta."""
        job = self.get(job_id)
        if job is None:
            raise JobError(f"No existe el step job #{job_id}.")
        fields = {"error": (motivo or "espera humana")[:2000],
                  "error_kind": "human", "proximo_reintento": None}
        if result is not None:
            fields["result"] = result
        done = self.set_state(job_id, "esperando_humano", **fields)
        self._log(job_id, "esperando_humano", fields["error"][:300])
        return done

    def schedule(self, job_id: int, retry_at: str | None,
                 result: dict | None = None):
        """running/processing -> scheduled (la política retiene el
        clip: se reintenta cuando llegue ``retry_at``; sin fecha no
        se reclama solo)."""
        fields = {"proximo_reintento": retry_at}
        if result is not None:
            fields["result"] = result
        done = self.set_state(job_id, "scheduled", **fields)
        self._log(job_id, "scheduled",
                  f"reintento programado: {retry_at or 'sin fecha (manual)'}")
        return done

    def resume(self, job_id: int):
        """esperando_humano/failed -> pending, con intentos a 0
        (decisión humana explícita: vuelve a tener sus reintentos).
        Es la única salida de esos estados."""
        job = self.get(job_id)
        if job is None:
            raise JobError(f"No existe el step job #{job_id}.")
        done = self.set_state(job_id, "pending", intentos=0, error=None,
                              error_kind=None, proximo_reintento=None)
        self._log(job_id, "resumed",
                  "reanudado por un humano: vuelve a pending")
        return done

    # -- lectura / recuperación ------------------------------------------
    def next_due(self, paso: str | None = None,
                 plataforma: str | None = None,
                 include_scheduled: bool = False, limit: int = 1,
                 now: str | None = None):
        """Jobs listos para reclamar: pending con reintento vencido
        (o sin reintento), y —solo con ``include_scheduled``— los
        scheduled con fecha vencida. Orden: prioridad desc, más
        antiguo primero. ``esperando_humano`` y ``failed`` NUNCA
        salen aquí: no se reintentan solos."""
        now = now or self._now()
        q = ("SELECT * FROM step_jobs WHERE "
             "((estado = 'pending' AND (proximo_reintento IS NULL "
             "OR proximo_reintento <= ?))")
        args = [now]
        if include_scheduled:
            q += (" OR (estado = 'scheduled' AND proximo_reintento "
                  "IS NOT NULL AND proximo_reintento <= ?)")
            args.append(now)
        q += ")"
        if paso:
            q += " AND paso = ?"
            args.append(paso)
        if plataforma:
            q += " AND plataforma = ?"
            args.append(plataforma)
        q += " ORDER BY prioridad DESC, created_at ASC LIMIT ?"
        args.append(limit)
        rows = self.conn.execute(q, args).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def requeue_stale(self, timeout_s: int = STALE_TIMEOUT_S):
        """running/processing sin moverse más de ``timeout_s``:
        vuelven a pending SIN gastar intento (el worker murió).
        Devuelve cuántos se recuperaron."""
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(seconds=timeout_s)).isoformat(
                      timespec="seconds")
        rows = self.conn.execute(
            "SELECT id FROM step_jobs WHERE estado IN ('running',"
            " 'processing') AND updated_at <= ?", (cutoff,)).fetchall()
        for r in rows:
            self.set_state(r["id"], "pending", proximo_reintento=None)
            self._log(r["id"], "requeued_stale",
                      "el worker murió con el job reclamado; vuelve a "
                      "pending sin gastar intento")
        return len(rows)

    def list_jobs(self, estado: str | None = None, paso: str | None = None,
                  clip_id: str | None = None, limit: int = 50):
        q = "SELECT * FROM step_jobs WHERE 1 = 1"
        args: list = []
        if estado:
            q += " AND estado = ?"
            args.append(estado)
        if paso:
            q += " AND paso = ?"
            args.append(paso)
        if clip_id:
            q += " AND clip_id = ?"
            args.append(clip_id)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [self._row_to_dict(r)
                for r in self.conn.execute(q, args).fetchall()]

    def counts(self):
        rows = self.conn.execute(
            "SELECT estado, COUNT(*) n FROM step_jobs GROUP BY estado"
        ).fetchall()
        return {r["estado"]: r["n"] for r in rows}

    def stuck(self, limit: int = 20):
        """Jobs atascados visibles en el panel: esperando_humano o
        failed, los más recientes primero, con motivo y hora."""
        rows = self.conn.execute(
            "SELECT * FROM step_jobs WHERE estado IN ('esperando_humano',"
            " 'failed') ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]
