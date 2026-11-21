#!/usr/bin/env python3
"""Simulacros anti-recaída — Parte C, 2026-11-20 (lado cerebro / pytest).

Espejo en Python de los simulacros bun del Worker
(worker/test/anti-recaida-simulacros.test.ts): mismo contrato A4/A5/B1-B2,
mismo modelo de referencia, adaptado al cerebro (sqlite3 stdlib + diario
JSONL en un directorio temporal que simula el objeto R2
wal/<tenant>/<fecha>.jsonl).

Entorno de PRUEBA: SQLite local + ficheros temporales + plataforma
mockeada. NUNCA la D1 real, NUNCA la cuota real: el 7500 se FINGE con un
flag; este archivo no escribe 100.000 filas en ningún sitio.

A4.md, A5.md y B1-B2.md NO existían cuando se escribió este archivo y sus
módulos tampoco están en el árbol. Los simulacros prueban el CONTRATO; la
integración contra el código real queda marcada PENDIENTE (tests en skip).
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DAILY_D1_QUOTA_ROWS = 100_000
MIGRATION_MAX_FRACTION_OF_REMAINING = 0.2
TENANT = "sebastian"
DATE = "2026-11-20"
NOW = 1794600000  # 2026-11-20T12:00:00-03:00 aprox.


def idempotency_key(clip_id: int, account: str, date: str) -> str:
    """B1/B2: idempotency_key = clip_id + cuenta + fecha."""
    return f"{clip_id}:{account}:{date}"


def journal_key(tenant: str, date: str) -> str:
    """B1: ruta del diario en R2."""
    return f"wal/{tenant}/{date}.jsonl"


class QuotaExceededError(Exception):
    """Error D1 7500 (FINGIDO en el simulacro; ver is_quota_error)."""

    code = 7500


def is_quota_error(err: BaseException) -> bool:
    # ADAPTER: el texto/código exacto del error real de D1 NO está
    # verificado (verificarlo exigiría escribir en D1, hoy agotada:
    # 104.610/100.000). A4 debe confirmar el marcador real.
    return getattr(err, "code", None) in (7500, "7500") or "7500" in str(err)


class MockD1:
    """SQLite local que FINGE el 7500: con exhausted=True toda escritura
    lanza QuotaExceededError ANTES de ejecutar nada (0 filas escritas)."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE publish_log ("
            "idempotency_key TEXT PRIMARY KEY, clip_id INTEGER NOT NULL, "
            "account TEXT NOT NULL, date TEXT NOT NULL, "
            "platform_post_id TEXT, applied_at INTEGER NOT NULL)"
        )
        self.exhausted = False
        self.writes_attempted = 0
        self.writes_succeeded = 0

    def insert_publish_log(self, key, clip_id, account, date, post_id, now):
        self.writes_attempted += 1
        if self.exhausted:
            raise QuotaExceededError(
                "D1 error 7500: daily row writes quota exceeded"
            )
        self.conn.execute(
            "INSERT INTO publish_log "
            "(idempotency_key, clip_id, account, date, platform_post_id, applied_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(idempotency_key) DO NOTHING",
            (key, clip_id, account, date, post_id, now),
        )
        self.conn.commit()
        self.writes_succeeded += 1

    def count_publish_log(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM publish_log"
        ).fetchone()[0]


class MockR2Journal:
    """Diario JSONL wal/<tenant>/<fecha>.jsonl sobre un dir temporal."""

    def __init__(self, root: Path):
        self.root = root

    def _path(self, tenant, date):
        return self.root / journal_key(tenant, date)

    def append(self, tenant, date, line: dict):
        path = self._path(tenant, date)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    def read_lines(self, tenant, date):
        path = self._path(tenant, date)
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def has_result(self, tenant, date, key):
        return next(
            (line for line in self.read_lines(tenant, date)
             if line["idempotency_key"] == key and line["status"] != "intent"),
            None,
        )

    def has_intent(self, tenant, date, key):
        return any(
            line["idempotency_key"] == key and line["status"] == "intent"
            for line in self.read_lines(tenant, date)
        )

    def line_count(self, tenant, date):
        return len(self.read_lines(tenant, date))


class MockPlatform:
    """La plataforma (TikTok/YT/IG/FB) simulada; recuerda por key."""

    def __init__(self):
        self.posts = {}
        self.publish_calls = 0

    def publish(self, key):
        self.publish_calls += 1
        if key in self.posts:
            return self.posts[key]
        post_id = "post_" + key.replace(":", "_").replace("-", "_")
        self.posts[key] = post_id
        return post_id

    def find_by_key(self, key):
        return self.posts.get(key)

    def published_count(self):
        return len(self.posts)


ESSENTIAL_LOOPS = {"publisher"}


class QuotaCircuit:
    """A4: al 7500, parar bucles no esenciales + una sola alerta."""

    def __init__(self):
        self.tripped = False
        self.alerts = []

    def on_d1_error(self, err, now=NOW):
        if not is_quota_error(err):
            return
        if self.tripped:
            return  # una sola alerta
        self.tripped = True
        self.alerts.append(
            "ALERTA CUOTA D1 AGOTADA (7500): bucles no esenciales "
            "detenidos; el publicador sigue con el diario wal/. "
            "Una sola alerta."
        )

    def should_run_loop(self, loop):
        return (not self.tripped) or loop in ESSENTIAL_LOOPS

    def reset(self):
        self.tripped = False


def migration_precheck(estimated_rows, used_today, daily_quota=DAILY_D1_QUOTA_ROWS):
    """A5: rechazo si la estimación supera el 20% de la cuota restante."""
    remaining = daily_quota - used_today
    max_allowed = int(remaining * MIGRATION_MAX_FRACTION_OF_REMAINING)
    base = {"estimated_rows": estimated_rows,
            "remaining": remaining, "max_allowed": max_allowed}
    if estimated_rows <= max_allowed:
        return {"allowed": True, **base}
    return {"allowed": False,
            "reason": (f"Migración RECHAZADA: estima {estimated_rows} filas, "
                       f"más del 20% de la cuota restante "
                       f"({max_allowed} de {remaining})."),
            **base}


def run_migration_if_allowed(d1, precheck, rows, now=NOW):
    """Ejecuta la migración solo si el precheck la acepta; si no, 0 escrituras."""
    if not precheck["allowed"]:
        return {"ran": False, "inserted": 0, "reason": precheck["reason"]}
    for row in rows:
        d1.insert_publish_log(row["key"], row["clip_id"], row["account"],
                              row["date"], None, now)
    return {"ran": True, "inserted": len(rows)}


class JournalPublisher:
    """B1/B2: intención -> publicar -> anotar resultado (por key)."""

    def __init__(self, journal, platform, tenant=TENANT):
        self.journal = journal
        self.platform = platform
        self.tenant = tenant

    def publish(self, clip_id, account, date, now=NOW):
        key = idempotency_key(clip_id, account, date)
        existing = self.journal.has_result(self.tenant, date, key)
        if existing and existing.get("platform_post_id"):
            return {"post_id": existing["platform_post_id"], "duplicate": True}
        if not self.journal.has_intent(self.tenant, date, key):
            self.journal.append(self.tenant, date, {
                "idempotency_key": key, "clip_id": clip_id, "account": account,
                "date": date, "action": "publish", "status": "intent", "ts": now})
        post_id = self.platform.publish(key)
        self.journal.append(self.tenant, date, {
            "idempotency_key": key, "clip_id": clip_id, "account": account,
            "date": date, "action": "publish", "status": "published",
            "platform_post_id": post_id, "ts": now})
        return {"post_id": post_id, "duplicate": False}

    def publish_with_crash_after_platform_call(self, clip_id, account, date,
                                               now=NOW):
        """Simula el reinicio del Worker a mitad de subida."""
        key = idempotency_key(clip_id, account, date)
        if not self.journal.has_intent(self.tenant, date, key):
            self.journal.append(self.tenant, date, {
                "idempotency_key": key, "clip_id": clip_id, "account": account,
                "date": date, "action": "publish", "status": "intent", "ts": now})
        self.platform.publish(key)
        raise RuntimeError(
            "SIMULATED WORKER RESTART: crash tras publicar, antes de anotar")

    def recover(self, clip_id, account, date, now=NOW):
        key = idempotency_key(clip_id, account, date)
        existing = self.journal.has_result(self.tenant, date, key)
        if existing and existing.get("platform_post_id"):
            return {"post_id": existing["platform_post_id"], "republished": False}
        on_platform = self.platform.find_by_key(key)
        if on_platform:
            self.journal.append(self.tenant, date, {
                "idempotency_key": key, "clip_id": clip_id, "account": account,
                "date": date, "action": "publish", "status": "published",
                "platform_post_id": on_platform, "ts": now})
            return {"post_id": on_platform, "republished": False}
        result = self.publish(clip_id, account, date, now)
        return {"post_id": result["post_id"], "republished": True}


def drain_journal_to_d1(d1, journal, tenant, date, now=NOW):
    """Vuelca el diario en D1 cuando vuelve la cuota. Idempotente."""
    applied = skipped = 0
    for line in journal.read_lines(tenant, date):
        if line["status"] != "published":
            continue
        before = d1.count_publish_log()
        d1.insert_publish_log(line["idempotency_key"], line["clip_id"],
                              line["account"], line["date"],
                              line.get("platform_post_id"), now)
        if d1.count_publish_log() > before:
            applied += 1
        else:
            skipped += 1
    return {"applied": applied, "skipped": skipped}


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory(prefix="anti-recaida-") as tmp:
        yield {
            "d1": MockD1(),
            "journal": MockR2Journal(Path(tmp)),
            "platform": MockPlatform(),
            "circuit": QuotaCircuit(),
        }


def _publisher(env):
    return JournalPublisher(env["journal"], env["platform"], TENANT)


# --- Simulacro 1: cuota agotada (7500 fingido) ---------------------------

def test_sim1_fake_7500_writes_nothing_real(env):
    d1 = env["d1"]
    d1.exhausted = True
    for i in range(5):
        with pytest.raises(QuotaExceededError, match="7500"):
            d1.insert_publish_log(f"k{i}", 100 + i, "tiktok_es", DATE, None, NOW)
    assert d1.writes_attempted == 5
    assert d1.writes_succeeded == 0
    assert d1.count_publish_log() == 0


def test_sim1_a_nonessential_loops_stop(env):
    d1, circuit = env["d1"], env["circuit"]
    d1.exhausted = True
    assert circuit.should_run_loop("analytics") is True
    with pytest.raises(QuotaExceededError) as exc:
        d1.insert_publish_log("k", 1, "tiktok_es", DATE, None, NOW)
    assert is_quota_error(exc.value)
    circuit.on_d1_error(exc.value)
    assert circuit.tripped is True
    for loop in ("analytics", "brain-dispatch", "watcher-scan", "seo-retry"):
        assert circuit.should_run_loop(loop) is False
    assert circuit.should_run_loop("publisher") is True


def test_sim1_a2_single_alert(env):
    circuit = env["circuit"]
    for _ in range(3):
        circuit.on_d1_error(QuotaExceededError())
    assert len(circuit.alerts) == 1
    assert "7500" in circuit.alerts[0]


def test_sim1_b_publisher_continues_via_journal(env):
    d1, circuit = env["d1"], env["circuit"]
    publisher = _publisher(env)
    d1.exhausted = True
    circuit.on_d1_error(QuotaExceededError())
    assert circuit.should_run_loop("publisher") is True
    assert publisher.publish(91, "tiktok_es", DATE)["duplicate"] is False
    assert publisher.publish(91, "youtube_es", DATE)["duplicate"] is False
    assert env["platform"].published_count() == 2
    assert env["journal"].line_count(TENANT, DATE) == 4  # intent + result x2
    assert journal_key(TENANT, DATE) == "wal/sebastian/2026-11-20.jsonl"
    assert d1.writes_succeeded == 0
    assert d1.count_publish_log() == 0


def test_sim1_c_no_duplicates(env):
    publisher = _publisher(env)
    first = publisher.publish(91, "tiktok_es", DATE)
    again = publisher.publish(91, "tiktok_es", DATE)
    assert again["duplicate"] is True
    assert again["post_id"] == first["post_id"]
    assert env["platform"].publish_calls == 1
    assert env["platform"].published_count() == 1
    assert idempotency_key(91, "tiktok_es", DATE) == "91:tiktok_es:2026-11-20"
    publisher.publish(91, "youtube_es", DATE)
    publisher.publish(91, "tiktok_es", "2026-11-21")
    assert env["platform"].published_count() == 3


def test_sim1_d_journal_drains_when_quota_returns(env):
    d1, circuit = env["d1"], env["circuit"]
    publisher = _publisher(env)
    d1.exhausted = True
    circuit.on_d1_error(QuotaExceededError())
    publisher.publish(91, "tiktok_es", DATE)
    publisher.publish(91, "youtube_es", DATE)
    publisher.publish(117, "tiktok_es", DATE)
    with pytest.raises(QuotaExceededError, match="7500"):
        drain_journal_to_d1(d1, env["journal"], TENANT, DATE)
    assert d1.count_publish_log() == 0
    d1.exhausted = False
    circuit.reset()
    assert circuit.should_run_loop("analytics") is True
    assert drain_journal_to_d1(d1, env["journal"], TENANT, DATE) == {
        "applied": 3, "skipped": 0}
    assert d1.count_publish_log() == 3
    assert drain_journal_to_d1(d1, env["journal"], TENANT, DATE) == {
        "applied": 0, "skipped": 3}
    assert d1.count_publish_log() == 3


# --- Simulacro 2: reinicio del Worker a mitad de una subida --------------

def test_sim2_intent_plus_platform_post_no_republish(env):
    publisher = _publisher(env)
    with pytest.raises(RuntimeError, match="SIMULATED WORKER RESTART"):
        publisher.publish_with_crash_after_platform_call(91, "tiktok_es", DATE)
    assert env["platform"].published_count() == 1
    assert env["platform"].publish_calls == 1
    key = idempotency_key(91, "tiktok_es", DATE)
    assert env["journal"].has_result(TENANT, DATE, key) is None
    assert env["journal"].has_intent(TENANT, DATE, key) is True
    recovered = publisher.recover(91, "tiktok_es", DATE, NOW + 1000)
    assert recovered["republished"] is False
    assert env["platform"].publish_calls == 1
    assert env["platform"].published_count() == 1
    assert (env["journal"].has_result(TENANT, DATE, key)["platform_post_id"]
            == recovered["post_id"])


def test_sim2_intent_without_platform_post_publishes_once(env):
    publisher = _publisher(env)
    key = idempotency_key(117, "tiktok_es", DATE)
    env["journal"].append(TENANT, DATE, {
        "idempotency_key": key, "clip_id": 117, "account": "tiktok_es",
        "date": DATE, "action": "publish", "status": "intent", "ts": NOW})
    assert env["platform"].find_by_key(key) is None
    recovered = publisher.recover(117, "tiktok_es", DATE, NOW + 1000)
    assert recovered["republished"] is True
    assert env["platform"].publish_calls == 1
    assert env["platform"].published_count() == 1
    again = publisher.recover(117, "tiktok_es", DATE, NOW + 2000)
    assert again["republished"] is False
    assert env["platform"].publish_calls == 1


# --- Simulacro 3: migración con cálculo previo y rechazo automático ------

def test_sim3_accept_case(env):
    d1 = env["d1"]
    pre = migration_precheck(estimated_rows=1_500, used_today=90_000)
    assert pre["allowed"] is True
    assert pre["remaining"] == 10_000
    assert pre["max_allowed"] == 2_000
    rows = [{"key": f"mig-{i}", "clip_id": 200 + i,
             "account": "tiktok_es", "date": DATE} for i in range(1_500)]
    result = run_migration_if_allowed(d1, pre, rows)
    assert result == {"ran": True, "inserted": 1_500}
    assert d1.count_publish_log() == 1_500


def test_sim3_accept_at_boundary():
    assert migration_precheck(estimated_rows=2_000,
                              used_today=90_000)["allowed"] is True


def test_sim3_reject_case(env):
    d1 = env["d1"]
    pre = migration_precheck(estimated_rows=3_000, used_today=90_000)
    assert pre["allowed"] is False
    assert "RECHAZADA" in pre["reason"]
    assert pre["max_allowed"] == 2_000
    rows = [{"key": f"mig-{i}", "clip_id": 200 + i,
             "account": "tiktok_es", "date": DATE} for i in range(3_000)]
    before = d1.writes_attempted
    result = run_migration_if_allowed(d1, pre, rows)
    assert result["ran"] is False
    assert result["inserted"] == 0
    assert d1.writes_attempted == before  # rechazo = CERO escrituras
    assert d1.count_publish_log() == 0


def test_sim3_reject_near_exhausted_quota():
    assert migration_precheck(estimated_rows=101,
                              used_today=99_500)["allowed"] is False
    assert migration_precheck(estimated_rows=100,
                              used_today=99_500)["allowed"] is True
    assert DAILY_D1_QUOTA_ROWS == 100_000


# PENDIENTE — integración contra el código real A4/A5/B1-B2.
# Motivo: a 2026-11-20 ~05:50 -03 ni los informes A4.md, A5.md y B1-B2.md ni
# sus módulos existen en el árbol (se escriben en paralelo). En skip hasta
# que aterricen; entonces deben re-enlazarse y pasar en verde contra la
# implementación real, no contra este modelo de referencia.
@pytest.mark.skip(reason="PENDIENTE: módulo A4 aún no existe en el árbol")
def test_pending_real_a4_circuit():
    raise AssertionError("PENDIENTE: módulo A4 aún no existe en el árbol")


@pytest.mark.skip(reason="PENDIENTE: módulo A5 aún no existe en el árbol")
def test_pending_real_a5_migration_gate():
    raise AssertionError("PENDIENTE: módulo A5 aún no existe en el árbol")


@pytest.mark.skip(reason="PENDIENTE: módulos B1/B2 aún no existen en el árbol")
def test_pending_real_b1_b2_journal():
    raise AssertionError("PENDIENTE: módulos B1/B2 aún no existen en el árbol")
