"""Tests de la tabla step_jobs (clipagent/jobs.py).

Cubre: esquema con las columnas pedidas, idempotencia (misma clave
= mismo job; publicar ignora el payload), reclamo atómico,
transiciones, reintentos acotados con backoff, esperando_humano,
recuperación tras caída, orden por prioridad y el DDL de la
migración D1.
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from clipagent.jobs import (JobError, StepJobs,  # noqa: E402
                            make_idempotency_key, next_retry_at)

REQUIRED_COLUMNS = {
    "id", "clip_id", "plataforma", "paso", "estado", "intentos",
    "proximo_reintento", "clave_anti_duplicado", "created_at",
    "updated_at",
}


@pytest.fixture()
def store(tmp_path):
    s = StepJobs(str(tmp_path / "jobs.db"))
    yield s
    s.close()


def test_schema_has_required_columns(store):
    cols = {r[1] for r in store.conn.execute(
        "PRAGMA table_info(step_jobs)").fetchall()}
    assert REQUIRED_COLUMNS <= cols


def test_migration_sql_creates_same_table(tmp_path):
    db = str(tmp_path / "d1.db")
    conn = sqlite3.connect(db)
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "migrations", "0001_step_jobs.sql"),
              encoding="utf-8") as f:
        conn.executescript(f.read())
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(step_jobs)").fetchall()}
    assert REQUIRED_COLUMNS <= cols
    conn.close()


def test_enqueue_is_idempotent(store):
    a = store.enqueue("clip1", "corte", payload={"video": "v.mp4"})
    b = store.enqueue("clip1", "corte", payload={"video": "v.mp4"})
    assert a["id"] == b["id"]
    assert len(store.list_jobs()) == 1


def test_enqueue_different_payload_is_new_job(store):
    a = store.enqueue("clip1", "corte", payload={"video": "v.mp4"})
    b = store.enqueue("clip1", "corte", payload={"video": "w.mp4"})
    assert a["id"] != b["id"]


def test_publish_key_ignores_payload(store):
    # Un clip no se publica dos veces en la misma plataforma aunque
    # cambie el título: la clave de publicar no lleva el payload.
    a = store.enqueue("clip1", "publicar", plataforma="tiktok",
                      payload={"clip": "c.mp4", "title": "uno"})
    b = store.enqueue("clip1", "publicar", plataforma="tiktok",
                      payload={"clip": "c.mp4", "title": "dos"})
    assert a["id"] == b["id"]
    c = store.enqueue("clip1", "publicar", plataforma="youtube_shorts",
                      payload={"clip": "c.mp4", "title": "uno"})
    assert c["id"] != a["id"]
    assert (make_idempotency_key("clip1", "tiktok", "publicar",
                                 {"title": "x"})
            == make_idempotency_key("clip1", "tiktok", "publicar",
                                    {"title": "y"}))


def test_publish_requires_platform(store):
    with pytest.raises(JobError):
        store.enqueue("clip1", "publicar", payload={"clip": "c.mp4"})


def test_claim_is_atomic_and_complete(store):
    job = store.enqueue("clip1", "vod", payload={"url": "https://x"})
    claimed = store.claim(job["id"])
    assert claimed["estado"] == "running"
    assert claimed["intentos"] == 1
    assert store.claim(job["id"]) is None  # ya reclamado: nadie más
    done = store.complete(job["id"], {"vod_id": "v1"})
    assert done["estado"] == "done"
    assert done["result"]["vod_id"] == "v1"


def test_publish_claim_goes_to_processing_and_posts(store):
    job = store.enqueue("clip1", "publicar", plataforma="tiktok",
                        payload={"clip": "c.mp4"})
    claimed = store.claim(job["id"])
    assert claimed["estado"] == "processing"
    posted = store.complete(job["id"], {"action": "published"})
    assert posted["estado"] == "posted"


def test_dry_run_completes_as_done_not_posted(store):
    job = store.enqueue("clip1", "publicar", plataforma="tiktok",
                        payload={"clip": "c.mp4"})
    store.claim(job["id"])
    done = store.complete(job["id"], {"action": "dry-run"},
                          target="done")
    assert done["estado"] == "done"


def test_invalid_transition_raises(store):
    job = store.enqueue("clip1", "vod", payload={"url": "https://x"})
    with pytest.raises(JobError):
        store.complete(job["id"], {})  # pending -> done no existe
    with pytest.raises(JobError):
        store.set_state(job["id"], "posted")


def test_transient_fail_retries_with_backoff_then_fails(store):
    job = store.enqueue("clip1", "corte", payload={"video": "v.mp4"},
                        max_intentos=2)
    store.claim(job["id"])
    again = store.fail(job["id"], "timeout de red", "transient")
    assert again["estado"] == "pending"
    assert again["proximo_reintento"] is not None
    assert again["intentos"] == 1
    store.claim(job["id"])
    dead = store.fail(job["id"], "timeout de red", "transient")
    assert dead["estado"] == "failed"
    assert dead["error_kind"] == "transient"


def test_permanent_fail_goes_straight_to_failed(store):
    job = store.enqueue("clip1", "corte", payload={"video": "v.mp4"})
    store.claim(job["id"])
    failed = store.fail(job["id"], "vídeo no existe", "permanent")
    assert failed["estado"] == "failed"


def test_human_fail_waits_without_retry(store):
    job = store.enqueue("clip1", "publicar", plataforma="tiktok",
                        payload={"clip": "c.mp4"})
    store.claim(job["id"])
    waiting = store.fail(job["id"], "captcha de TikTok", "human")
    assert waiting["estado"] == "esperando_humano"
    assert waiting["proximo_reintento"] is None
    assert waiting not in store.next_due(include_scheduled=True)
    assert waiting in store.stuck()
    resumed = store.resume(job["id"])
    assert resumed["estado"] == "pending"
    assert resumed["intentos"] == 0


def test_requeue_stale_does_not_spend_attempt(store):
    job = store.enqueue("clip1", "corte", payload={"video": "v.mp4"})
    store.claim(job["id"])
    store.conn.execute(
        "UPDATE step_jobs SET updated_at = '2000-01-01T00:00:00+00:00'"
        " WHERE id = ?", (job["id"],))
    store.conn.commit()
    assert store.requeue_stale(timeout_s=60) == 1
    back = store.get(job["id"])
    assert back["estado"] == "pending"
    assert back["intentos"] == 1  # el reclamo queda, no se suma otro


def test_next_due_orders_by_priority_then_age(store):
    low = store.enqueue("clip-low", "picos", payload={"chat_path": "c"},
                        prioridad=10)
    high = store.enqueue("clip-high", "picos", payload={"chat_path": "c"},
                         prioridad=90)
    mid = store.enqueue("clip-mid", "picos", payload={"chat_path": "c"},
                        prioridad=50)
    due = store.next_due(paso="picos", limit=3)
    assert [j["id"] for j in due] == [high["id"], mid["id"], low["id"]]


def test_next_due_excludes_failed_and_waiting(store):
    job = store.enqueue("clip1", "vod", payload={"url": "https://x"})
    store.claim(job["id"])
    store.fail(job["id"], "roto", "permanent")
    assert store.next_due() == []
    job2 = store.enqueue("clip2", "vod", payload={"url": "https://x"})
    store.claim(job2["id"])
    store.wait_human(job2["id"], "falta credencial")
    assert store.next_due() == []


def test_scheduled_only_due_with_flag(store):
    job = store.enqueue("clip1", "publicar", plataforma="tiktok",
                        payload={"clip": "c.mp4"})
    store.claim(job["id"])
    store.schedule(job["id"], "2000-01-01T00:00:00+00:00")
    assert store.next_due() == []
    assert [j["id"] for j in store.next_due(include_scheduled=True)] == \
        [job["id"]]


def test_backoff_grows_and_caps():
    first = next_retry_at(1)
    second = next_retry_at(2)
    assert first < second
    assert next_retry_at(100) <= next_retry_at(101)


def test_events_are_logged(store):
    job = store.enqueue("clip1", "vod", payload={"url": "https://x"})
    store.claim(job["id"])
    store.complete(job["id"], {})
    events = [e["event"] for e in store.events(job["id"])]
    assert events[0] == "enqueued"
    assert "claimed" in events
    assert "done" in events


def test_counts_and_list_filters(store):
    store.enqueue("clip1", "vod", payload={"url": "https://x"})
    store.enqueue("clip2", "picos", payload={"chat_path": "c"})
    assert store.counts() == {"pending": 2}
    assert len(store.list_jobs(paso="picos")) == 1
    assert len(store.list_jobs(clip_id="clip1")) == 1
    assert json.loads(json.dumps(store.list_jobs()))[0]["payload"] \
        is not None
