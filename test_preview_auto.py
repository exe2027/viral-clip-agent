#!/usr/bin/env python3
"""Preview validado automáticamente (2026-11-19, Fallo 1).

Regla: el preview de un clip queda validado (preview_seen) SOLO
cuando su render pasó la validación final del cerebro
(final_review, decisión "aprobado"). Cualquier otro veredicto
(pendiente/rechazado/desactivada/sin registro) NO marca nada y el
freno sigue. El marcado nunca tumba el encolado.

Cubre los tres caminos que encolan en el panel:
- live_cutter._enqueue_panel (usa record["final_review"] del paso 8b)
- vod_fallback.burn_and_enqueue (valida el render antes de encolar;
  rechazado -> no se encola)
- worker._upload_clips_to_panel (valida el MP4 del manifiesto;
  rechazado -> no se sube, queda en local)
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from clipagent.live_cutter import LiveCutterSession
from clipagent.vod_fallback import burn_and_enqueue


class FakeBridge:
    """Puente falso: registra create/upload/preview_seen."""

    def __init__(self):
        self.created = []
        self.uploaded = []
        self.seen = []
        self.tracked = []
        self.fail_seen = False
        self._next_id = 100

    def create_clip(self, payload):
        self.created.append(payload)
        self._next_id += 1
        return {"id": self._next_id}

    def upload_video(self, clip_id, path):
        self.uploaded.append((clip_id, path))
        return {"ok": True}

    def preview_seen(self, clip_id):
        if self.fail_seen:
            raise RuntimeError("panel caído")
        self.seen.append(clip_id)
        return {"ok": True}

    def tracking_step(self, *a, **k):
        self.tracked.append((a, k))
        return {"ok": True}


def _seo(score=92):
    return {p: {"score": score} for p in (
        "youtube_shorts", "tiktok", "instagram_reels", "facebook_reels")}


class TestLivePreviewAuto(unittest.TestCase):
    def _session(self, tmp, bridge):
        return LiveCutterSession(
            "auronplay", platform="twitch", stream_id="2026-11-18",
            out_dir=os.path.join(tmp, "clips"), base_dir=tmp,
            bridge=bridge, dry_run=False, vod_id="123")

    def _record(self, final_review):
        pkg_path = os.path.join(tempfile.gettempdir(), "pkg.json")
        with open(pkg_path, "w", encoding="utf-8") as f:
            json.dump({"priority": {"youtube_shorts": {"priority": 45}}}, f)
        record = {
            "clip_id": "live:auronplay:x:p167", "clip": __file__,
            "title": "Auronplay grita", "start_sec": 100, "end_sec": 140,
            "peak_ts": 167.0, "category": "Grand Theft Auto VI",
            "category_source": "helix", "game": "GTA 6", "origin": "live",
            "viral_total": 55.0, "viral_auto": 52.0, "review": {},
            "streamer_hype": {"count": 0, "distinct": 0, "phrases": []},
            "subtitles_complete": True, "publish_blocked": False,
            "seo": {"youtube_shorts": 94, "tiktok": 92,
                    "instagram_reels": 91, "facebook_reels": 90},
            "publish_status": {}, "package_path": pkg_path,
            "hype_score": 8.0, "burst_score": 8.0, "hype_ratio": 4.0,
        }
        if final_review is not None:
            record["final_review"] = final_review
        return record

    def test_aprobado_marca_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            session = self._session(tmp, bridge)
            panel_id = session._enqueue_panel(self._record(
                {"decision": "aprobado", "pasa": True}))
            self.assertEqual(panel_id, 101)
            self.assertEqual(bridge.seen, [101])

    def test_pendiente_no_marca(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            session = self._session(tmp, bridge)
            session._enqueue_panel(self._record(
                {"decision": "pendiente_revision", "pasa": False}))
            self.assertEqual(bridge.seen, [])

    def test_rechazado_no_marca(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            session = self._session(tmp, bridge)
            session._enqueue_panel(self._record(
                {"decision": "rechazado", "pasa": False}))
            self.assertEqual(bridge.seen, [])

    def test_observado_no_marca(self):
        # Modo observación (final_review.py, ON por defecto): un
        # render que NO pasa el criterio visual se reescribe como
        # "observado" (pasa True) para que se encole igual, pero NO
        # pasó la validación: el preview NO se marca.
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            session = self._session(tmp, bridge)
            session._enqueue_panel(self._record(
                {"decision": "observado", "pasa": True}))
            self.assertEqual(bridge.seen, [])

    def test_sin_registro_no_marca(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            session = self._session(tmp, bridge)
            session._enqueue_panel(self._record(None))
            self.assertEqual(bridge.seen, [])

    def test_fallo_al_marcar_no_tumba_el_encolado(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            bridge.fail_seen = True
            session = self._session(tmp, bridge)
            panel_id = session._enqueue_panel(self._record(
                {"decision": "aprobado", "pasa": True}))
            self.assertEqual(panel_id, 101)
            self.assertEqual(len(bridge.created), 1)
            self.assertEqual(len(bridge.uploaded), 1)


def _vod_clip():
    return {"file": "clip_01.mp4", "start": 3200, "duration": 48,
            "title_es": "Rubius ríe", "title_en": "Rubius laughs",
            "viral": {"best_title": "Rubius ríe",
                      "description": "d", "tags": ["#GTA6"]},
            "viral_score": {"total": 55.0, "chat_multiple": 1.2},
            "seo_scores": _seo(92)}


class TestVodPreviewAuto(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.video = os.path.join(self.tmp.name, "clip_01.mp4")
        with open(self.video, "wb") as f:
            f.write(b"fake-mp4")

    def tearDown(self):
        self.tmp.cleanup()

    def _patch_subtitles(self):
        patcher = mock.patch(
            "clipagent.workers.subtitle_clip",
            return_value={"publish_blocked": False, "no_speech": False,
                          "srt": self.video + ".srt",
                          "burn": self.video + "_subs.mp4"})
        self.addCleanup(patcher.stop)
        return patcher.start()

    def _patch_review(self, decision):
        verdict = {"decision": decision, "pasa": decision == "aprobado",
                   "hard_pasa": decision != "rechazado",
                   "needs_rerender": False, "rerender_reason": "",
                   "hard_failures": [],
                   "needs_human_review": decision == "rechazado",
                   "motivo": "x" if decision == "rechazado" else "",
                   "fallos": [], "scores": {}, "frames_n": 0,
                   "model": "", "transcript_words": 0,
                   "observe_only": True,
                   "face": {"pasa": decision != "rechazado"},
                   "template": {"pasa": decision != "rechazado"},
                   "gemini": {"decision": decision}}
        patcher = mock.patch(
            "clipagent.final_review.validate_final_clip",
            return_value=verdict)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_aprobado_encola_y_marca(self):
        self._patch_subtitles()
        self._patch_review("aprobado")
        bridge = FakeBridge()
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", "2932415021", self.tmp.name, _vod_clip())
        self.assertEqual(panel_id, 101, why)
        self.assertEqual(bridge.seen, [101])

    def test_pendiente_encola_sin_marcar(self):
        self._patch_subtitles()
        self._patch_review("pendiente_revision")
        bridge = FakeBridge()
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", "2932415021", self.tmp.name, _vod_clip())
        self.assertEqual(panel_id, 101, why)
        self.assertEqual(bridge.seen, [])

    def test_rechazado_no_se_encola(self):
        self._patch_subtitles()
        self._patch_review("rechazado")
        bridge = FakeBridge()
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", "2932415021", self.tmp.name, _vod_clip())
        self.assertIsNone(panel_id)
        self.assertIn("validación final", why)
        self.assertEqual(bridge.created, [])
        self.assertEqual(bridge.uploaded, [])

    def test_observado_encola_sin_marcar(self):
        # "observado" fluye como aprobado para el encolado, pero
        # el render NO pasó el criterio: se encola sin marcar.
        self._patch_subtitles()
        self._patch_review("observado")
        bridge = FakeBridge()
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", "2932415021", self.tmp.name, _vod_clip())
        self.assertEqual(panel_id, 101, why)
        self.assertEqual(bridge.seen, [])


class FakeQueue:
    def __init__(self):
        self.logs = []

    def count_log(self, job_id, event):
        return sum(1 for j, e, _ in self.logs
                   if j == job_id and e == event)

    def log(self, job_id, event, message):
        self.logs.append((job_id, event, message))


class TestWorkerPreviewAuto(unittest.TestCase):
    def _setup(self, tmp):
        out_dir = os.path.join(tmp, "out")
        os.makedirs(out_dir, exist_ok=True)
        clip_path = os.path.join(out_dir, "clip_01_subs.mp4")
        with open(clip_path, "wb") as f:
            f.write(b"fake-mp4")
        job = {"id": 7, "out_dir": out_dir, "streamer": "illojuan"}
        manifest = {"vod_id": "2932866051", "clips": [
            {"n": 1, "file": "clip_01_subs.mp4", "start": 100,
             "duration": 48, "score": 55,
             "title_es": "Risa", "title_en": "Laugh",
             "viral": {"best_title": "Risa", "viral_multiple": 1.2,
                       "description_first_2_lines": "d"},
             "viral_score": {"total": 55.0},
             "seo_scores": _seo(92)}]}
        return job, manifest

    def _run(self, decision):
        import worker as worker_mod
        with tempfile.TemporaryDirectory() as tmp:
            job, manifest = self._setup(tmp)
            q = FakeQueue()
            bridge = FakeBridge()
            with mock.patch.object(
                    worker_mod, "_review_clip_for_panel",
                    return_value={"decision": decision,
                                  "motivo": "x" if decision == "rechazado" else ""}):
                up, fail = worker_mod._upload_clips_to_panel(
                    q, job, manifest, bridge=bridge)
            return up, fail, bridge, q

    def test_aprobado_sube_y_marca(self):
        up, fail, bridge, _ = self._run("aprobado")
        self.assertEqual((up, fail), (1, 0))
        self.assertEqual(bridge.seen, [101])

    def test_pendiente_sube_sin_marcar(self):
        up, fail, bridge, _ = self._run("pendiente_revision")
        self.assertEqual((up, fail), (1, 0))
        self.assertEqual(bridge.seen, [])

    def test_rechazado_no_se_sube(self):
        up, fail, bridge, q = self._run("rechazado")
        self.assertEqual((up, fail), (0, 1))
        self.assertEqual(bridge.created, [])
        self.assertEqual(bridge.uploaded, [])
        self.assertTrue(any(e == "panel_revision_rechazada"
                            for _, e, _ in q.logs))

    def test_observado_sube_sin_marcar(self):
        up, fail, bridge, _ = self._run("observado")
        self.assertEqual((up, fail), (1, 0))
        self.assertEqual(bridge.seen, [])


if __name__ == "__main__":
    unittest.main()
