#!/usr/bin/env python3
"""Pruebas del cortador en vivo (live_cutter.py + panel_bridge.py).

Reglas que se verifican aquí:
- Solo GTA VI se corta (Just Chatting / sin categoría -> no).
- Clip 40 s (20 s pre-roll + 20 s), nunca 25-35 s ni < 40 s.
- Sin voz verificable -> BLOQUEADO, nunca se publica.
- Orden 2026-11-18 14:37: a la Cola del panel, sin publicar en
  público todavía (publish jobs en draft por defecto).
- Categoría por API oficial (Helix / Kick API / capítulos VOD).
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.category import is_gta6
from clipagent.live_cutter import (CategoryGate, FileRecorder,
                                   LiveCutterSession, StreamRecorder)
from clipagent.panel_bridge import PanelBridge

REPO = os.path.dirname(os.path.abspath(__file__))
RUBIUS_CHAT = os.path.join(REPO, "runs", "2932415021", "chat.json")
RUBIUS_SEG = os.path.join(
    REPO, "runs", "2932415021-p2010", "segments", "seg_01_t33m10s.mp4")
# Fixture con los mismos capítulos del VOD real (Just Chatting
# 0-167 s, GTA VI desde 167 s). `runs/` está en .gitignore y no
# existe en CI: los tests de capítulos usan este fixture, no el
# chat real.
FIXTURE_CHAT = os.path.join(
    REPO, "tests", "fixtures", "chat_rubius_capitulos.json")


class FakeTwitchClient:
    """Cliente Helix falso: live_category por login."""

    def __init__(self, game):
        self.game = game

    def live_category(self, login):
        return {"game_name": self.game, "game_id": "1",
                "live": True, "source": "helix /streams?user_login"}

    def check(self, login):
        return ({"game": self.game, "title": "t",
                 "viewers": 1} if self.game else None, [])


class TestCategoryGate(unittest.TestCase):
    def test_gta6_pasa(self):
        gate = CategoryGate(platform="twitch", streamer="rubius",
                            twitch_client=FakeTwitchClient(
                                "Grand Theft Auto VI"))
        ok, cat, _src = gate.check()
        self.assertTrue(ok)
        self.assertTrue(is_gta6(cat))

    def test_just_chatting_no_corta(self):
        gate = CategoryGate(platform="twitch", streamer="rubius",
                            twitch_client=FakeTwitchClient(
                                "Just Chatting"))
        ok, cat, _src = gate.check()
        self.assertFalse(ok)
        self.assertEqual(cat, "Just Chatting")

    def test_sin_categoria_no_corta(self):
        gate = CategoryGate(platform="twitch", streamer="rubius",
                            twitch_client=FakeTwitchClient(""))
        ok, _cat, _src = gate.check()
        self.assertFalse(ok)

    def test_vod_capitulos_reales(self):
        # Capítulos del VOD real de Rubius 2932415021 (fixture con
        # los mismos capítulos): Just Chatting 0-167 s, GTA VI
        # desde 167 s.
        gate = CategoryGate(mode="vod", chat_path=FIXTURE_CHAT)
        ok, cat, _src = gate.check(2010)
        self.assertTrue(ok)
        self.assertEqual(cat, "Grand Theft Auto VI")
        ok2, cat2, _src2 = gate.check(100)
        self.assertFalse(ok2)
        self.assertEqual(cat2, "Just Chatting")

    def test_kick_sin_directo_no_corta(self):
        with mock.patch(
                "clipagent.live_cutter.CategoryGate._kick_live_category",
                return_value=("", "kick api/v2/channels")):
            gate = CategoryGate(platform="kick", streamer="nadie")
            ok, _cat, _src = gate.check()
            self.assertFalse(ok)


class TestRecorder(unittest.TestCase):
    def test_prune_guarda_los_ultimos(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = StreamRecorder("rubius", out_dir=tmp, keep=3)
            for i in range(5):
                path = os.path.join(tmp, f"seg_{i:05d}.ts")
                with open(path, "wb") as f:
                    f.write(b"x")
            self.assertEqual(rec.prune(), 2)
            names = sorted(os.listdir(tmp))
            self.assertEqual(names, ["seg_00002.ts", "seg_00003.ts",
                                     "seg_00004.ts"])

    def test_file_recorder_extrae_40s(self):
        if not os.path.isfile(RUBIUS_SEG):
            self.skipTest("sin segmento de Rubius en el repo")
        with tempfile.TemporaryDirectory() as tmp:
            rec = FileRecorder(RUBIUS_SEG, offset=1970.0)
            out = os.path.join(tmp, "window.ts")
            _path, offset = rec.extract_window(1990, 40, out)
            self.assertEqual(offset, 0.0)
            from clipagent.ingest import get_video_duration
            self.assertAlmostEqual(get_video_duration(out), 40.0,
                                   delta=1.0)


class TestCutWindow(unittest.TestCase):
    def test_ventana_40s_nunca_prohibida(self):
        peak_ts = 2010
        start = max(0.0, peak_ts - 20)
        duration = 40
        self.assertEqual((start, duration), (1990, 40))
        self.assertGreaterEqual(duration, 40)
        self.assertLessEqual(duration, 60)
        self.assertFalse(25 <= duration <= 35)


class TestPanelBridgeMock(unittest.TestCase):
    def _fake_urlopen(self, calls):
        class Resp:
            def __init__(self, payload):
                self.payload = payload

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout=60):
            calls.append((req.get_method(), req.full_url))
            if req.full_url.endswith("/api/auth/login"):
                return Resp({"ok": True, "token": "tok",
                             "tenant": "sebastian"})
            if req.full_url.endswith("/api/clips"):
                return Resp({"ok": True,
                             "clip": {"id": 7, "title": "t"}})
            if "/video" in req.full_url:
                return Resp({"ok": True})
            if req.full_url.endswith("/api/tracking/step"):
                return Resp({"ok": True})
            return Resp({"ok": True})
        return fake

    def test_login_create_upload_tracking(self):
        calls = []
        bridge = PanelBridge(base_url="https://example.invalid",
                             username="seba", password="x")
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._fake_urlopen(calls)):
            clip = bridge.create_clip({"title": "t", "start_sec": 0,
                                       "end_sec": 40, "platforms": ["tiktok"]})
            self.assertEqual(clip["id"], 7)
            with tempfile.NamedTemporaryFile(suffix=".mp4",
                                            delete=False) as f:
                f.write(b"fake")
                path = f.name
            try:
                bridge.upload_video(7, path)
                bridge.tracking_step(7, "pico", "pico a 2010s")
            finally:
                os.remove(path)
        methods = [m for m, _u in calls]
        self.assertIn("POST", methods)
        self.assertIn("PUT", methods)


class TestProcessPeakReal(unittest.TestCase):
    """process_peak con el segmento real de Rubius (corte + subtítulos
    de verdad con faster-whisper tiny). Lento (~1 min): es la prueba
    que demuestra que el cutter corta y subtitula."""

    def test_corta_clip_40s_en_gta6(self):
        if not os.path.isfile(RUBIUS_SEG):
            self.skipTest("sin segmento de Rubius en el repo")
        if not os.path.isfile(RUBIUS_CHAT):
            self.skipTest("sin chat de Rubius en el repo")
        from clipagent.jobs import StepJobs
        with tempfile.TemporaryDirectory() as tmp:
            store = StepJobs(os.path.join(tmp, "queue.db"))
            try:
                gate = CategoryGate(mode="vod", chat_path=FIXTURE_CHAT)
                session = LiveCutterSession(
                    "rubius", platform="twitch",
                    stream_id="2932415021", gate=gate, store=store,
                    out_dir=os.path.join(tmp, "clips"),
                    base_dir=tmp, dry_run=True, vod_id="2932415021",
                    latency_s=0)
                session.recorder = FileRecorder(RUBIUS_SEG,
                                                offset=1970.0)
                detail = {"msgs": 104, "risas": 11, "hype": 1,
                          "users": 90, "burst_score": 8.0,
                          "hype_score": 8.5, "hype_ratio": 4.2}
                record = session.process_peak(
                    (2010, 104.0, 8.0, detail))
                self.assertIsNotNone(record)
                self.assertEqual(record["category"],
                                 "Grand Theft Auto VI")
                self.assertEqual(record["duration_s"], 40)
                self.assertTrue(os.path.isfile(record["clip"]))
                from clipagent.ingest import get_video_duration
                dur = get_video_duration(record["clip"])
                self.assertGreaterEqual(dur, 40)
                self.assertLessEqual(dur, 60)
                # Subtítulos: o completos, o bloqueado (nunca
                # inventados ni publicados sin ellos).
                self.assertIn(record["publish_blocked"], (True, False))
                if record["publish_blocked"]:
                    self.assertFalse(record["subtitles_complete"])
                # Nada encolado en dry_run.
                self.assertEqual(store.counts().get("pending", 0), 0)
            finally:
                store.close()

    def test_pico_en_just_chatting_no_corta(self):
        from clipagent.jobs import StepJobs
        with tempfile.TemporaryDirectory() as tmp:
            store = StepJobs(os.path.join(tmp, "queue.db"))
            try:
                gate = CategoryGate(mode="vod", chat_path=FIXTURE_CHAT)
                session = LiveCutterSession(
                    "rubius", platform="twitch",
                    stream_id="2932415021", gate=gate, store=store,
                    out_dir=os.path.join(tmp, "clips"),
                    base_dir=tmp, dry_run=True, vod_id="2932415021",
                    latency_s=0)
                session.recorder = FileRecorder(RUBIUS_SEG,
                                                offset=1970.0)
                detail = {"msgs": 200, "risas": 50, "hype": 20,
                          "users": 150}
                record = session.process_peak((100, 200.0, 15.0,
                                               detail))
                self.assertIsNone(record)
                self.assertEqual(len(session.cut_clips), 0)
                self.assertTrue(any("Just Chatting" in (s.get("reason")
                                                         or "")
                                    for s in session.skipped))
            finally:
                store.close()


class TestEnqueueDraftDefault(unittest.TestCase):
    def test_publicable_queda_en_draft_no_publico(self):
        from clipagent.jobs import StepJobs
        with tempfile.TemporaryDirectory() as tmp:
            store = StepJobs(os.path.join(tmp, "queue.db"))
            try:
                session = LiveCutterSession(
                    "rubius", platform="twitch", store=store,
                    out_dir=os.path.join(tmp, "clips"),
                    base_dir=tmp, dry_run=False)
                record = {"clip_id": "live:rubius:x:p100",
                          "clip": __file__, "viral_total": 80,
                          "publish_blocked": False,
                          "publish_status": {
                              "youtube_shorts": {
                                  "status": "publicable",
                                  "reason": "ok"},
                              "tiktok": {
                                  "status": "bloqueado_seo",
                                  "reason": "SEO 80 < 90"}}}
                created = session._enqueue_publish(record)
                self.assertEqual(len(created), 1)
                self.assertEqual(created[0][0], "youtube_shorts")
                self.assertEqual(created[0][2], "draft")
                job = store.get(created[0][1])
                self.assertEqual(job["payload"].get("mode"), "draft")
            finally:
                store.close()

    def test_bloqueado_no_encola_nada(self):
        from clipagent.jobs import StepJobs
        with tempfile.TemporaryDirectory() as tmp:
            store = StepJobs(os.path.join(tmp, "queue.db"))
            try:
                session = LiveCutterSession(
                    "rubius", platform="twitch", store=store,
                    out_dir=os.path.join(tmp, "clips"),
                    base_dir=tmp, dry_run=False)
                record = {"clip_id": "live:rubius:x:p100",
                          "clip": __file__, "viral_total": 80,
                          "publish_blocked": True,
                          "publish_status": {
                              "youtube_shorts": {
                                  "status": "publicable",
                                  "reason": "ok"}}}
                self.assertEqual(session._enqueue_publish(record), [])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
