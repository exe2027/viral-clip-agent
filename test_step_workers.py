"""Tests de los workers por paso (clipagent/workers.py).

Cubre: vod (lista blanca), picos con chat sintético real, corte
idempotente (sin re-cortar), subtítulos (SRT reutilizado, sin voz),
empaquetar con la fórmula importada (no reimplementada), publicar
dry-run/draft, avance de la cadena vod->picos->corte, supervisor
--once y los CLI cortar.py / subtitular.py.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from clipagent.jobs import StepJobs  # noqa: E402
from clipagent import workers  # noqa: E402
from clipagent.workers import (StepError, bridge_legacy_queue,  # noqa: E402
                              enqueue_pipeline, run_step,
                              run_supervisor, workers_status)

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture()
def store(tmp_path):
    s = StepJobs(str(tmp_path / "jobs.db"))
    yield s
    s.close()


def _chat_with_burst(path, burst_start=100, n_burst=40, n_base=20):
    """chat.json: base dispersa + ráfaga de risas en burst_start."""
    comments = []
    for i in range(n_base):
        comments.append({
            "content_offset_seconds": i * 30,
            "message": {"body": "hola, qué tal"},
            "commenter": {"name": f"base{i}"}})
    for i in range(n_burst):
        comments.append({
            "content_offset_seconds": burst_start + (i % 8),
            "message": {"body": "JAJAJAJA qué bueno"},
            "commenter": {"name": f"fan{i}"}})
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"comments": comments}, f)
    return path


def _patch_allowlist(monkeypatch, names):
    import jobqueue
    monkeypatch.setattr(jobqueue, "load_allowlist",
                        lambda config_path=None: set(names))


def _patch_publish_queue(monkeypatch, tmp_path):
    """PublishQueue real pero en un archivo temporal (no toca el
    publish_queue.json del repo)."""
    from clipagent.publish import PublishQueue
    from functools import partial
    fake = partial(PublishQueue, str(tmp_path / "pubq.json"))
    monkeypatch.setattr("clipagent.publish.PublishQueue", fake)


def test_vod_ok_and_allowlist_rejects(store, monkeypatch):
    _patch_allowlist(monkeypatch, {"illojuan"})
    job = store.enqueue(
        "vod1", "vod",
        payload={"url": "https://kick.com/illojuan/videos/"
                        "12345678-1234-1234-1234-123456789012"})
    done = run_step(store, job["id"], cfg={})
    assert done["estado"] == "done"
    assert done["result"]["vod_id"].startswith("kick-illojuan-")
    assert done["result"]["streamer"] == "illojuan"

    bad = store.enqueue(
        "vod2", "vod",
        payload={"url": "https://kick.com/otro/videos/"
                        "12345678-1234-1234-1234-123456789012"})
    failed = run_step(store, bad["id"], cfg={})
    assert failed["estado"] == "failed"
    assert "lista blanca" in (failed["error"] or "")


def test_picos_detects_burst(store, tmp_path):
    chat = _chat_with_burst(str(tmp_path / "chat.json"))
    job = store.enqueue("vod1", "picos", payload={"chat_path": chat})
    done = run_step(store, job["id"], cfg={})
    assert done["estado"] == "done"
    peaks = done["result"]["peaks"]
    assert len(peaks) >= 1
    assert any(95 <= p["start"] <= 115 for p in peaks)
    assert all("viral_total" in p for p in peaks)


def test_picos_missing_chat_fails_permanent(store):
    job = store.enqueue("vod1", "picos",
                        payload={"chat_path": "/no/existe/chat.json"})
    failed = run_step(store, job["id"], cfg={})
    assert failed["estado"] == "failed"
    assert failed["error_kind"] == "permanent"


def test_corte_idempotent_reuses_existing_output(store, tmp_path,
                                                 monkeypatch):
    out = tmp_path / "clip_01.mp4"
    out.write_bytes(b"fake-mp4" * 10)
    called = []
    monkeypatch.setattr(workers, "_do_cut",
                        lambda *a, **k: called.append(True))
    job = store.enqueue("c1", "corte", payload={
        "video": str(tmp_path / "src.mp4"), "start_sec": 100,
        "duration": 60, "output": str(out)})
    (tmp_path / "src.mp4").write_bytes(b"src" * 10)
    done = run_step(store, job["id"], cfg={})
    assert done["estado"] == "done"
    assert done["result"]["reused"] is True
    assert called == []  # no se llamó a ffmpeg


def test_subtitulos_reuses_srt(store, tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v" * 10)
    srt = tmp_path / "clip.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nhola\n\n")
    monkeypatch.setattr(
        workers, "_do_transcribe",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no debe re-transcribir")))
    job = store.enqueue("c1", "subtitulos",
                        payload={"video": str(video),
                                 "srt": str(srt)})
    done = run_step(store, job["id"], cfg={})
    assert done["estado"] == "done"
    assert done["result"]["reused_srt"] is True


def test_subtitulos_no_speech_is_done_not_failed(store, tmp_path,
                                                 monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v" * 10)
    monkeypatch.setattr(
        workers, "_do_transcribe",
        lambda *a, **k: ({"no_speech": True, "word_count": 0}, None))
    job = store.enqueue("c1", "subtitulos",
                        payload={"video": str(video)})
    done = run_step(store, job["id"], cfg={})
    assert done["estado"] == "done"
    assert done["result"]["no_speech"] is True


def test_empaquetar_uses_imported_priority(store, tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"v" * 10)
    job = store.enqueue("c1", "empaquetar", payload={
        "clip": str(clip),
        "title": "GTA 6: la jugada que rompió el chat",
        "description": "GTA 6: el chat explotó en segundos. ¿Lo viste?",
        "tags": ["gta 6", "gta 6 clips"],
        "hashtags": ["#GTA6", "#GTA6Clips"],
        "virality": 78, "chat_multiple": 13,
        "out_path": str(tmp_path / "clip.package.json")})
    done = run_step(store, job["id"], cfg={})
    assert done["estado"] == "done"
    # La prioridad sale de clipagent.priority.priority (importada):
    from clipagent.priority import priority as priority_fn
    seo = done["result"]["seo"]
    assert seo["youtube_shorts"] > 0
    expected = priority_fn(78, seo["tiktok"], 13)["priority"]
    assert done["result"]["priority"]["tiktok"] == expected
    assert os.path.isfile(str(tmp_path / "clip.package.json"))


def test_empaquetar_without_title_fails(store, tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"v" * 10)
    job = store.enqueue("c1", "empaquetar",
                        payload={"clip": str(clip)})
    failed = run_step(store, job["id"], cfg={})
    assert failed["estado"] == "failed"
    assert "title" in (failed["error"] or "")


def test_publicar_dry_run_done_and_draft_waits_human(
        store, tmp_path, monkeypatch):
    _patch_publish_queue(monkeypatch, tmp_path)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"v" * 10)
    dry = store.enqueue("c1", "publicar", plataforma="tiktok",
                        payload={"clip": str(clip), "mode": "dry-run"})
    done = run_step(store, dry["id"], cfg={})
    assert done["estado"] == "done"
    assert done["result"]["action"] == "dry-run"

    draft = store.enqueue("c2", "publicar", plataforma="tiktok",
                          payload={"clip": str(clip), "mode": "draft"})
    waiting = run_step(store, draft["id"], cfg={})
    assert waiting["estado"] == "esperando_humano"
    assert "borrador" in (waiting["error"] or "")


def test_pipeline_advances_vod_to_picos_to_corte(store, tmp_path,
                                                 monkeypatch):
    _patch_allowlist(monkeypatch, {"illojuan"})
    chat = _chat_with_burst(str(tmp_path / "chat.json"))
    video = tmp_path / "vod.mp4"
    video.write_bytes(b"v" * 10)
    out_dir = tmp_path / "runs" / "vod1"
    out_dir.mkdir(parents=True)
    job = enqueue_pipeline(store, "vod1", {
        "url": "https://kick.com/illojuan/videos/"
               "12345678-1234-1234-1234-123456789012",
        "chat_path": chat, "video": str(video),
        "out_dir": str(out_dir)})
    done = run_step(store, job["id"], cfg={})
    assert done["estado"] == "done"
    picos = store.list_jobs(paso="picos")
    assert len(picos) == 1
    done2 = run_step(store, picos[0]["id"], cfg={})
    assert done2["estado"] == "done"
    cortes = store.list_jobs(paso="corte")
    assert len(cortes) == len(done2["result"]["peaks"]) >= 1
    assert all(c["payload"]["output"].endswith(".mp4")
               for c in cortes)


def test_pipeline_stops_at_empaquetar(store, tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"v" * 10)
    job = store.enqueue("c1", "empaquetar", payload={
        "clip": str(clip), "title": "GTA 6: jugada increíble",
        "out_path": str(tmp_path / "clip.package.json")})
    done = run_step(store, job["id"], cfg={})
    assert done["estado"] == "done"
    assert store.list_jobs(paso="publicar") == []  # publicar es manual


def test_supervisor_once_processes_chain(store, tmp_path, monkeypatch):
    _patch_allowlist(monkeypatch, {"illojuan"})
    chat = _chat_with_burst(str(tmp_path / "chat.json"))
    video = tmp_path / "vod.mp4"
    video.write_bytes(b"v" * 10)
    out_dir = tmp_path / "runs" / "vod1"
    out_dir.mkdir(parents=True)
    enqueue_pipeline(store, "vod1", {
        "url": "https://kick.com/illojuan/videos/"
               "12345678-1234-1234-1234-123456789012",
        "chat_path": chat, "video": str(video),
        "out_dir": str(out_dir)})
    # El supervisor con --once procesa vod y picos; los cortes
    # quedan encolados (ffmpeg no corre en este test).
    rc = run_supervisor(store, once=True)
    assert rc == 0
    assert store.get(1)["estado"] == "done"
    assert len(store.list_jobs(paso="corte")) >= 1


def test_supervisor_recovers_stale_and_status(store, tmp_path):
    job = store.enqueue("c1", "vod", payload={"url": "https://x"})
    store.claim(job["id"])
    store.conn.execute(
        "UPDATE step_jobs SET updated_at = '2000-01-01T00:00:00+00:00'"
        " WHERE id = ?", (job["id"],))
    store.conn.commit()
    st = workers_status(store)
    assert st["counts"] == {"running": 1}
    assert st["compute"]["corte"] == "external"
    assert st["compute"]["picos"] == "worker"


def test_run_step_unknown_job_raises():
    with pytest.raises(StepError):
        run_step(StepJobs(":memory:"), 999, cfg={})


def test_cli_cortar_help():
    r = subprocess.run([sys.executable, "cortar.py", "--help"],
                       cwd=HERE, capture_output=True, text=True)
    assert r.returncode == 0
    assert "--start" in r.stdout and "--out" in r.stdout


def test_cli_subtitular_help():
    r = subprocess.run([sys.executable, "subtitular.py", "--help"],
                       cwd=HERE, capture_output=True, text=True)
    assert r.returncode == 0
    assert "--burn" in r.stdout


def test_bridge_legacy_queue_to_pipeline(store, tmp_path):
    from jobqueue import Queue
    q = Queue(str(tmp_path / "legacy.db"))
    try:
        job_id = q.add(
            "https://kick.com/illojuan/videos/"
            "12345678-1234-1234-1234-123456789012", force=True)
        created = bridge_legacy_queue(store, q, limit=5)
        assert len(created) == 1
        assert created[0]["paso"] == "vod"
        assert created[0]["clip_id"].startswith("kick-illojuan-")
        assert created[0]["payload"]["vod_job_id"] == job_id
        assert created[0]["payload"]["url"].startswith("https://kick.com")
        assert q.get_job(job_id)["state"] == "processing"
        # Segundo puente: no duplica (idempotencia del paso vod).
        assert bridge_legacy_queue(store, q, limit=5) == []
    finally:
        q.close()


def test_cli_pipeline_bridge_empty(tmp_path):
    db = str(tmp_path / "jobs.db")
    r = subprocess.run(
        [sys.executable, "-m", "clipagent", "pipeline", "bridge",
         "--db", db],
        cwd=HERE, capture_output=True, text=True)
    assert r.returncode == 0
    assert "Puenteados 0" in r.stdout


def test_cli_workers_status(tmp_path):
    db = str(tmp_path / "jobs.db")
    r = subprocess.run(
        [sys.executable, "-m", "clipagent", "workers", "status",
         "--db", db],
        cwd=HERE, capture_output=True, text=True)
    assert r.returncode == 0
    assert "Atascados: ninguno." in r.stdout
    assert "external" in r.stdout
