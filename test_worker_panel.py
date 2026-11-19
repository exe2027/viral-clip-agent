"""Pruebas C8 (auditoría 2026-11-19): el worker sube los clips al
panel al terminar un job con éxito (create_clip + upload_video,
solo encolar para aprobación, nunca publicar).

Sin credenciales del panel, el worker registra el motivo y sigue
sin fingir la subida. Un fallo de subida no pierde el clip local
ni tumba el job (queda en ready_for_review con el motivo en el log).
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from jobqueue import Queue


def _manifest(out_dir):
    """Manifiesto mínimo válido con un clip y su MP4 real."""
    os.makedirs(os.path.join(out_dir, "clips"), exist_ok=True)
    mp4 = os.path.join(out_dir, "clips", "clip_01.mp4")
    with open(mp4, "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42fake")
    manifest = {
        "vod_id": "2929669422",
        "streamer": "rubius",
        "clips": [{
            "n": 1,
            "file": "clips/clip_01.mp4",
            "peak": 1280,
            "start": 1260,
            "duration": 40,
            "score": 51,
            "title_es": "GTA 6: El chat no lo podía creer",
            "desc_es": "El mejor momento de GTA 6 de este directo.",
            "viral": {
                "best_title": "GTA 6: El chat no lo podía creer",
                "description_first_2_lines":
                    "El mejor momento de GTA 6 de este directo.",
                "tags": ["GTA 6", "GTA VI"],
                "viral_multiple": 8.5,
            },
            "viral_score": {"total": 62},
            "seo_scores": {
                "youtube_shorts": {"score": 95},
                "tiktok": {"score": 92},
                "instagram_reels": {"score": 91},
                "facebook_reels": {"score": 93},
            },
            "moment_card": {"game": "GTA VI"},
            "layout": {"segments": ["gameplay"]},
        }],
    }
    with open(os.path.join(out_dir, "manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    return manifest


class TestWorkerPanelUpload(unittest.TestCase):
    """C8: worker --once sube el clip al panel al terminar el job."""

    def _job_en_cola(self, q, tmp):
        # URL de Twitch (no de Kick): con Kick el worker resolvería
        # los metadatos por red antes de correr auto_clips.
        job_id = q.add("2929669422", lang="es", streamer="rubius",
                       force=True, allowlist=set())
        out_dir = os.path.join(tmp, "runs")
        _manifest(out_dir)
        q.set_state(job_id, "queued", out_dir=out_dir)
        return q.get_job(job_id)

    def _run_ok(self, worker_mod, q, job, **kwargs):
        with mock.patch.object(
                worker_mod, "_run_auto_clips",
                return_value=(0, os.path.join(job["out_dir"],
                                              "worker.log"))):
            return worker_mod.process_job(q, job, top_n=5, **kwargs)

    def test_subida_al_terminar_job(self):
        import worker as worker_mod
        with tempfile.TemporaryDirectory() as tmp:
            q = Queue(os.path.join(tmp, "t.db"))
            try:
                job = self._job_en_cola(q, tmp)
                bridge = mock.MagicMock()
                bridge.create_clip.return_value = {"id": 123}
                bridge.upload_video.return_value = {}
                state = self._run_ok(worker_mod, q, job, bridge=bridge)
                self.assertEqual(state, "ready_for_review")
                self.assertEqual(q.get_job(job["id"])["state"],
                                 "ready_for_review")
                # create_clip + upload_video, una vez por clip.
                self.assertEqual(bridge.create_clip.call_count, 1)
                payload = bridge.create_clip.call_args[0][0]
                self.assertEqual(payload["origin"], "vod")
                self.assertEqual(payload["title"],
                                 "GTA 6: El chat no lo podía creer")
                self.assertEqual(payload["start_sec"], 1260)
                self.assertEqual(payload["end_sec"], 1300)
                self.assertEqual(payload["platforms"], [
                    "youtube_shorts", "tiktok", "instagram_reels",
                    "facebook_reels"])
                self.assertEqual(payload["influencer_id"], "rubius")
                self.assertEqual(payload["source_id"], 2929669422)
                self.assertEqual(payload["game"], "GTA VI")
                self.assertEqual(payload["subtitles_complete"], 1)
                self.assertEqual(bridge.upload_video.call_count, 1)
                clip_id, clip_path = \
                    bridge.upload_video.call_args[0]
                self.assertEqual(clip_id, 123)
                self.assertEqual(
                    clip_path,
                    os.path.join(job["out_dir"], "clips", "clip_01.mp4"))
                # Solo encolar: nunca publicar.
                self.assertFalse(bridge.publish_clip.called)
                self.assertEqual(q.count_log(job["id"],
                                             "panel_enqueued"), 1)
            finally:
                q.close()

    def test_sin_credenciales_no_finge_subida(self):
        import worker as worker_mod
        with tempfile.TemporaryDirectory() as tmp:
            q = Queue(os.path.join(tmp, "t.db"))
            try:
                job = self._job_en_cola(q, tmp)
                with mock.patch.object(
                        worker_mod, "_resolve_panel_bridge",
                        return_value=None):
                    state = self._run_ok(worker_mod, q, job)
                self.assertEqual(state, "ready_for_review")
                self.assertEqual(q.count_log(
                    job["id"], "panel_sin_credenciales"), 1)
                self.assertEqual(q.count_log(job["id"],
                                             "panel_enqueued"), 0)
            finally:
                q.close()

    def test_tenant_invalido_no_tumba_job(self):
        import worker as worker_mod
        # Sin depender de clipagent.tenant (otro frente, aún sin
        # commitear): el worker atrapa Exception genérica al
        # resolver el puente, así que cualquier error de config
        # (p. ej. TenantError) sigue el mismo camino.
        with tempfile.TemporaryDirectory() as tmp:
            q = Queue(os.path.join(tmp, "t.db"))
            try:
                job = self._job_en_cola(q, tmp)
                with mock.patch.object(
                        worker_mod, "_resolve_panel_bridge",
                        side_effect=RuntimeError(
                            "sin tenant (TenantError)")):
                    state = self._run_ok(worker_mod, q, job)
                self.assertEqual(state, "ready_for_review")
                self.assertEqual(q.count_log(job["id"],
                                             "panel_config_error"), 1)
            finally:
                q.close()

    def test_fallo_subida_no_pierde_clip_local(self):
        import worker as worker_mod
        from clipagent.panel_bridge import PanelError
        with tempfile.TemporaryDirectory() as tmp:
            q = Queue(os.path.join(tmp, "t.db"))
            try:
                job = self._job_en_cola(q, tmp)
                bridge = mock.MagicMock()
                bridge.create_clip.side_effect = PanelError(
                    "POST /api/clips -> HTTP 401")
                state = self._run_ok(worker_mod, q, job, bridge=bridge)
                self.assertEqual(state, "ready_for_review")
                self.assertEqual(q.get_job(job["id"])["state"],
                                 "ready_for_review")
                self.assertEqual(q.count_log(
                    job["id"], "panel_upload_failed"), 1)
                # El clip local sigue en su sitio.
                self.assertTrue(os.path.isfile(os.path.join(
                    job["out_dir"], "clips", "clip_01.mp4")))
                bridge.upload_video.assert_not_called()
            finally:
                q.close()

    def test_no_duplica_si_ya_subido(self):
        import worker as worker_mod
        with tempfile.TemporaryDirectory() as tmp:
            q = Queue(os.path.join(tmp, "t.db"))
            try:
                job = self._job_en_cola(q, tmp)
                manifest = _manifest(job["out_dir"])
                bridge = mock.MagicMock()
                bridge.create_clip.return_value = {"id": 7}
                up, fail = worker_mod._upload_clips_to_panel(
                    q, job, manifest, bridge=bridge)
                self.assertEqual((up, fail), (1, 0))
                # Segundo intento (job reencolado a mano): no se
                # vuelve a subir nada.
                up, fail = worker_mod._upload_clips_to_panel(
                    q, job, manifest, bridge=bridge)
                self.assertEqual((up, fail), (0, 0))
                self.assertEqual(bridge.create_clip.call_count, 1)
                self.assertEqual(q.count_log(job["id"],
                                             "panel_ya_encolado"), 1)
            finally:
                q.close()

    def test_payload_sin_viral_ni_seo_no_rompe(self):
        import worker as worker_mod
        job = {"streamer": "rubius", "vod_id": "2929669422",
               "out_dir": "/tmp/no-existe"}
        manifest = {"vod_id": "2929669422", "clips": []}
        clip = {"n": 1, "file": "clips/clip_01.mp4", "start": 100,
                "duration": 45, "score": 40}
        payload = worker_mod._panel_payload(job, manifest, clip)
        self.assertEqual(payload["origin"], "vod")
        self.assertEqual(payload["start_sec"], 100)
        self.assertEqual(payload["end_sec"], 145)
        self.assertEqual(payload["game"], "GTA VI")
        self.assertIsInstance(payload["score"], int)


if __name__ == "__main__":
    unittest.main()
