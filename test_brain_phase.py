#!/usr/bin/env python3
"""Pruebas de la fase en vivo del cerebro (2026-11-19).

El panel tiene que mostrar qué está haciendo AHORA el cerebro
(Buscando picos / Cortando clip / Modo VOD / Sin directos), no
solo un piloto verde o rojo. El cerebro reporta su fase a
POST /api/worker/phase en cada etapa:
- detect (live_scan): buscando_picos si hay ES en vivo, si no
  sin_directos.
- cut (run_live_cut): cortando_clip con el nombre del streamer.
- fallback VOD: modo_vod al entrar, sin_directos al terminar.

La fase es informativa: un fallo del panel nunca tumba el barrido,
el corte ni el fallback (sin credenciales, sin red o con error,
solo queda en el log y se sigue).
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import live_scan  # noqa: E402
from clipagent import panel_bridge  # noqa: E402
from clipagent.live_cutter import run_live_cut  # noqa: E402
from clipagent.panel_bridge import PanelBridge, report_brain_phase  # noqa: E402
from clipagent.vod_fallback import run as vod_run  # noqa: E402


class _RecordingBridge:
    """Puente falso: registra las fases reportadas."""

    def __init__(self, clips=None):
        self.clips = list(clips or [])
        self.phases = []

    def list_clips(self):
        return list(self.clips)

    def report_phase(self, phase, detail="", run_id=""):
        self.phases.append((phase, detail, run_id))
        return {"ok": True}


class TestReportPhase(unittest.TestCase):
    def test_post_a_api_worker_phase_con_cuerpo_y_bearer(self):
        calls = []

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"ok": true, "phase": "modo_vod"}'

        def fake_urlopen(req, timeout=60):
            calls.append(req)
            return Resp()

        bridge = PanelBridge(base_url="https://example.invalid",
                             token="tok-123", tenant="sebastian")
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = bridge.report_phase("modo_vod", detail="Rubius",
                                      run_id="444")
        self.assertEqual(res, {"ok": True, "phase": "modo_vod"})
        self.assertEqual(len(calls), 1)
        req = calls[0]
        self.assertEqual(req.method, "POST")
        self.assertTrue(req.full_url.endswith("/api/worker/phase"))
        self.assertEqual(req.headers.get("Authorization"), "Bearer tok-123")
        import json
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body, {"phase": "modo_vod", "detail": "Rubius",
                                "run_id": "444"})

    def test_error_del_panel_no_levanta(self):
        bridge = PanelBridge(base_url="https://example.invalid",
                             token="tok-123", tenant="sebastian")
        with mock.patch.object(bridge, "_request",
                               side_effect=panel_bridge.PanelError("caído")):
            self.assertIsNone(bridge.report_phase("modo_vod"))


class TestReportBrainPhase(unittest.TestCase):
    def test_sin_credenciales_no_hace_nada_y_no_levanta(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(report_brain_phase("modo_vod", "Rubius"))

    def test_con_bridge_inyectado_lo_usa(self):
        rec = _RecordingBridge()
        self.assertTrue(report_brain_phase("cortando_clip", "rubius",
                                           bridge=rec))
        self.assertEqual(len(rec.phases), 1)
        self.assertEqual(rec.phases[0][0], "cortando_clip")
        self.assertEqual(rec.phases[0][1], "rubius")
        # El run_id sale de GITHUB_RUN_ID cuando corre en Actions
        # (en local, vacío); nunca se inventa.
        self.assertEqual(rec.phases[0][2],
                         os.environ.get("GITHUB_RUN_ID", ""))

    def test_error_del_bridge_no_levanta(self):
        class Broken:
            def report_phase(self, *a, **k):
                raise RuntimeError("boom")

        self.assertFalse(report_brain_phase("modo_vod", bridge=Broken()))

    def test_con_credenciales_usa_from_env(self):
        rec = _RecordingBridge()
        fake = mock.Mock()
        fake.report_phase = lambda phase, detail="", run_id="": rec.report_phase(phase, detail, run_id)
        with mock.patch.object(PanelBridge, "from_env", return_value=fake):
            with mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "999"}, clear=False):
                self.assertTrue(report_brain_phase("sin_directos", "nadie"))
        self.assertEqual(rec.phases[0][0], "sin_directos")
        self.assertEqual(rec.phases[0][2], "999")


class TestLiveScanPhase(unittest.TestCase):
    def _run_main(self, live):
        env = {"STREAMERS": "", "GITHUB_OUTPUT": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(live_scan, "scan_live", return_value={
                    "live": live, "skipped": [], "checked": 18,
                    "twitch_mode": "helix"}):
                with mock.patch("clipagent.panel_bridge.report_brain_phase") as rep:
                    self.assertEqual(live_scan.main(), 0)
                    return rep

    def test_con_es_en_vivo_reporta_buscando_picos(self):
        rep = self._run_main([{"name": "rubius", "platform": "twitch"},
                              {"name": "kaicenat", "platform": "twitch"}])
        rep.assert_called_once()
        args = rep.call_args[0]
        self.assertEqual(args[0], "buscando_picos")
        self.assertIn("rubius", args[1])
        self.assertNotIn("kaicenat", args[1])

    def test_sin_es_en_vivo_reporta_sin_directos(self):
        rep = self._run_main([{"name": "kaicenat", "platform": "twitch"}])
        rep.assert_called_once_with("sin_directos", "ningún ES en GTA VI")

    def test_sin_nadie_en_vivo_reporta_sin_directos(self):
        rep = self._run_main([])
        rep.assert_called_once_with("sin_directos", "ningún ES en GTA VI")


class TestLiveCutPhase(unittest.TestCase):
    def test_al_empezar_reporta_cortando_clip(self):
        class FakeSession:
            def __init__(self, *a, **k):
                self.cut_clips = []
                self.skipped = []

            def run_live(self, duration_s=1500):
                return []

        rec = _RecordingBridge()
        with mock.patch("clipagent.live_cutter.LiveCutterSession", FakeSession):
            with mock.patch("clipagent.panel_bridge.report_brain_phase") as rep:
                out = run_live_cut("rubius", platform="twitch", minutes=1,
                                   cfg={"live": {}}, bridge=rec)
        self.assertEqual(out["peaks"], [])
        rep.assert_called_once()
        args, kwargs = rep.call_args
        self.assertEqual(args[0], "cortando_clip")
        self.assertEqual(args[1], "rubius")
        self.assertIs(kwargs.get("bridge"), rec)


class TestVodFallbackPhase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state.json")
        self.out = os.path.join(self.tmp.name, "out")

    def tearDown(self):
        self.tmp.cleanup()

    def _scan(self, live):
        return lambda only=None: {"live": live, "skipped": [],
                                  "checked": 1, "twitch_mode": "helix"}

    def test_modo_vod_al_entrar_y_sin_directos_al_terminar(self):
        vod = {"vod_id": "2932415021",
               "url": "https://www.twitch.tv/videos/2932415021",
               "title": "GTA 6", "category": "Grand Theft Auto VI",
               "live": False}
        bridge = _RecordingBridge()
        n, summary = vod_run(
            state_path=self.state, out_root=self.out, bridge=bridge,
            scan_fn=self._scan([]),
            latest_vod_fn=lambda entry: vod,
            auto_clips_fn=lambda *a, **k: {"clips": []})
        self.assertEqual(n, 0)
        self.assertEqual(len(bridge.phases), 2)
        self.assertEqual(bridge.phases[0][0], "modo_vod")
        self.assertIn("rubius", bridge.phases[0][1])
        self.assertEqual(bridge.phases[1][0], "sin_directos")
        self.assertIn("modo VOD:", bridge.phases[1][1])

    def test_con_es_en_vivo_no_reporta_modo_vod(self):
        bridge = _RecordingBridge()
        n, _ = vod_run(
            state_path=self.state, out_root=self.out, bridge=bridge,
            scan_fn=self._scan([{"name": "rubius",
                                 "platform": "twitch"}]),
            latest_vod_fn=lambda entry: (_ for _ in ()).throw(
                AssertionError("no debe listar VODs con ES en vivo")))
        self.assertEqual(n, 0)
        self.assertEqual(bridge.phases, [])

    def test_dry_run_no_reporta_fase(self):
        vod = {"vod_id": "2932415021",
               "url": "https://www.twitch.tv/videos/2932415021",
               "title": "GTA 6", "category": "Grand Theft Auto VI",
               "live": False}
        bridge = _RecordingBridge()
        n, _ = vod_run(
            state_path=self.state, out_root=self.out, dry_run=True,
            bridge=bridge, scan_fn=self._scan([]),
            latest_vod_fn=lambda entry: vod,
            auto_clips_fn=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("dry-run no debe cortar")))
        self.assertEqual(n, 0)
        self.assertEqual(bridge.phases, [])


if __name__ == "__main__":
    unittest.main()
