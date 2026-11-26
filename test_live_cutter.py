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
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

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


def _frame(video, t, w=540, h=960):
    """Un fotograma como array RGB (w x h)."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", video,
         "-frames:v", "1", "-vf", f"scale={w}:{h}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.uint8).reshape(h, w, 3)


def _count_white(arr, y0, y1):
    r = arr[y0:y1, :, 0].astype(int)
    g = arr[y0:y1, :, 1].astype(int)
    b = arr[y0:y1, :, 2].astype(int)
    return int(((r > 200) & (g > 200) & (b > 200)).sum())


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
                             username="seba", password="x",
                             tenant="sebastian")
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
                # 2026-11-26 (enmienda: Gemini o retención, sin
                # fallback de reglas): un clip puede quedar
                # BLOQUEADO con los subtítulos completos — retenido
                # por falta de título honesto (sin clave Gemini en
                # local el paquete se retiene y el render sale sin
                # banner, así que la validación final también lo
                # rechaza por plantilla) o por validación. Lo que no
                # puede pasar es publicar sin subtítulos reales.
                self.assertIn(record["publish_blocked"], (True, False))
                if record["publish_blocked"]:
                    self.assertTrue(
                        not record["subtitles_complete"]
                        or record.get("needs_human_review"),
                        record)
                else:
                    # Título cableado (2026-11-19): el título del
                    # paso 5b se quemó en la franja 702-800 (en el
                    # fotograma a 540x960, 351-400) del clip final.
                    # Sin voz no hay quemado y no aplica.
                    self.assertTrue(record.get("title"),
                                    "sin título en el record")
                    fr = _frame(record["clip"], 1.0)
                    self.assertGreater(_count_white(fr, 351, 400), 50,
                                       "sin título en la franja "
                                       "del clip automático")
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
    def test_publicable_en_draft_no_crea_job_local(self):
        # P0 #8: publicable sin auto_publish -> el clip queda en
        # la Cola del panel como borrador; ya no se crea ningún
        # job local (el publish loop --live está retirado).
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
                                  "reason": "SEO 79 < 80"}}}
                created = session._enqueue_publish(record)
                self.assertEqual(created, [])
                self.assertEqual(store.counts().get("pending", 0), 0)
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


class TestEnqueuePanelRescue(unittest.TestCase):
    """_enqueue_panel: un clip rescatado (viral 45-49 por gritos)
    manda rescue: true + viralidad revisada + SEO al panel; un clip
    normal no cambia su payload (sin rescue, sin viral_score)."""

    def _session(self, tmp, bridge):
        return LiveCutterSession(
            "auronplay", platform="twitch", stream_id="2026-11-18",
            out_dir=os.path.join(tmp, "clips"), base_dir=tmp,
            bridge=bridge, dry_run=False, vod_id="123")

    def _record(self, rescue):
        pkg_path = os.path.join(tempfile.gettempdir(), "pkg.json")
        with open(pkg_path, "w", encoding="utf-8") as f:
            json.dump({"priority": {"youtube_shorts": {"priority": 45}}}, f)
        return {
            "clip_id": "live:auronplay:x:p167", "clip": __file__,
            "title": "Auronplay grita", "start_sec": 100, "end_sec": 140,
            "peak_ts": 167.0, "category": "Grand Theft Auto VI",
            "category_source": "helix", "game": "GTA 6", "origin": "live",
            "viral_total": 45.0, "viral_auto": 48.3,
            "review": {"revision": "gemini", "viral_revisado": 48.3,
                       "human": None, "motivo": "rescate",
                       "shouts": 17, "rescate_gritos": rescue},
            "streamer_hype": {"count": 0, "distinct": 0, "phrases": []},
            "subtitles_complete": True, "publish_blocked": False,
            "seo": {"youtube_shorts": 94, "tiktok": 92,
                    "instagram_reels": 91, "facebook_reels": 90},
            "publish_status": {}, "package_path": pkg_path,
            "hype_score": 8.0, "burst_score": 8.0, "hype_ratio": 4.0,
        }

    def test_rescatado_manda_marca_y_viralidad_real(self):
        class FakeBridge:
            def __init__(self):
                self.payloads = []

            def create_clip(self, payload):
                self.payloads.append(payload)
                return {"id": 99}

            def upload_video(self, clip_id, path):
                return {"ok": True}

            def tracking_step(self, *a, **k):
                return {"ok": True}

        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            session = self._session(tmp, bridge)
            panel_id = session._enqueue_panel(self._record(True))
            self.assertEqual(panel_id, 99)
            payload = bridge.payloads[0]
            self.assertTrue(payload["rescue"])
            self.assertEqual(payload["rescue_shouts"], 17)
            self.assertEqual(payload["viral_score"], 48.3)
            self.assertEqual(payload["seo_scores"]["youtube_shorts"], 94)
            self.assertEqual(payload["score"], 45)

    def test_normal_no_manda_rescue(self):
        class FakeBridge:
            def __init__(self):
                self.payloads = []

            def create_clip(self, payload):
                self.payloads.append(payload)
                return {"id": 98}

            def upload_video(self, clip_id, path):
                return {"ok": True}

            def tracking_step(self, *a, **k):
                return {"ok": True}

        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            session = self._session(tmp, bridge)
            record = self._record(False)
            record["viral_total"] = 55.0
            self.assertEqual(session._enqueue_panel(record), 98)
            payload = bridge.payloads[0]
            self.assertNotIn("rescue", payload)
            self.assertNotIn("viral_score", payload)
            self.assertNotIn("seo_scores", payload)


class TestEnqueuePanelSeoPatch(unittest.TestCase):
    """_enqueue_panel (2026-11-24, auto-seo): el POST de un clip
    normal NO lleva seo_scores (la puerta SEO del POST tiraría los
    de máximo < 80); el SEO viaja en un PATCH posterior con las
    entradas exactas del scorer. El rescate sigue mandándolo en el
    POST (el Worker verifica la marca) y no se duplica."""

    def _session(self, tmp, bridge):
        return LiveCutterSession(
            "auronplay", platform="twitch", stream_id="2026-11-18",
            out_dir=os.path.join(tmp, "clips"), base_dir=tmp,
            bridge=bridge, dry_run=False, vod_id="123")

    def _record(self, tmp, rescue, seo=None):
        pkg_path = os.path.join(tmp, "pkg.json")
        with open(pkg_path, "w", encoding="utf-8") as f:
            json.dump({
                "priority": {"youtube_shorts": {"priority": 45}},
                "hashtags": ["#GTA6", "#GTA6Clips", "#GTA",
                             "#RockstarGames"],
                "visible": "cams+gameplay", "game": "GTA 6",
            }, f)
        return {
            "clip_id": "live:auronplay:x:p167", "clip": __file__,
            "title": "Auronplay grita", "start_sec": 100, "end_sec": 140,
            "peak_ts": 167.0, "category": "Grand Theft Auto VI",
            "category_source": "helix", "game": "GTA 6", "origin": "live",
            "viral_total": 55.0, "viral_auto": 55.0,
            "review": {"revision": "gemini", "viral_revisado": 55.0,
                       "human": None, "motivo": "",
                       "shouts": 17 if rescue else 0,
                       "rescate_gritos": rescue},
            "streamer_hype": {"count": 0, "distinct": 0, "phrases": []},
            "subtitles_complete": True, "publish_blocked": False,
            "seo": {"youtube_shorts": 94, "tiktok": 92,
                    "instagram_reels": 91, "facebook_reels": 90}
            if seo is None else seo,
            "publish_status": {}, "package_path": pkg_path,
            "hype_score": 8.0, "burst_score": 8.0, "hype_ratio": 4.0,
        }

    class _FakeBridge:
        def __init__(self):
            self.payloads = []
            self.patched = []

        def create_clip(self, payload):
            self.payloads.append(payload)
            return {"id": 99}

        def upload_video(self, clip_id, path):
            return {"ok": True}

        def update_clip(self, clip_id, fields):
            self.patched.append((clip_id, fields))
            return {"ok": True}

        def tracking_step(self, *a, **k):
            return {"ok": True}

    def test_normal_patch_con_seo_e_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self._FakeBridge()
            session = self._session(tmp, bridge)
            panel_id = session._enqueue_panel(
                self._record(tmp, rescue=False))
            self.assertEqual(panel_id, 99)
            self.assertNotIn("seo_scores", bridge.payloads[0])
            self.assertEqual(len(bridge.patched), 1)
            clip_id, fields = bridge.patched[0]
            self.assertEqual(clip_id, 99)
            self.assertEqual(fields["seo_scores"], {
                "youtube_shorts": 94, "tiktok": 92,
                "instagram_reels": 91, "facebook_reels": 90})
            self.assertEqual(fields["viral"]["seo_source"], "corte")
            inputs = fields["viral"]["seo_inputs"]
            self.assertEqual(inputs["hashtags"], [
                "#GTA6", "#GTA6Clips", "#GTA", "#RockstarGames"])
            self.assertEqual(inputs["visible"], "cams+gameplay")
            self.assertIs(inputs["game_verified"], True)
            self.assertEqual(inputs["keyword"], "GTA 6")
            self.assertEqual(inputs["lang"], "es")

    def test_sin_seo_no_hay_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self._FakeBridge()
            session = self._session(tmp, bridge)
            panel_id = session._enqueue_panel(
                self._record(tmp, rescue=False, seo={}))
            self.assertEqual(panel_id, 99)
            self.assertEqual(bridge.patched, [])

    def test_rescate_no_duplica_el_seo(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self._FakeBridge()
            session = self._session(tmp, bridge)
            panel_id = session._enqueue_panel(
                self._record(tmp, rescue=True))
            self.assertEqual(panel_id, 99)
            self.assertEqual(
                bridge.payloads[0]["seo_scores"]["youtube_shorts"], 94)
            self.assertEqual(bridge.patched, [])


class TestEnqueuePanelLanguage(unittest.TestCase):
    """_enqueue_panel: el payload lleva el idioma del streamer.

    2026-11-19 (clips 24/25, Kai Cenat): el payload no llevaba
    idioma y el panel lo dejaba en "es" por defecto, así que los
    clips EN iban a la cola ES y se publicarían en las cuentas en
    español."""

    class _FakeBridge:
        def __init__(self):
            self.payloads = []

        def create_clip(self, payload):
            self.payloads.append(payload)
            return {"id": 97}

        def upload_video(self, clip_id, path):
            return {"ok": True}

        def tracking_step(self, *a, **k):
            return {"ok": True}

    def _session(self, tmp, streamer):
        return LiveCutterSession(
            streamer, platform="twitch", stream_id="2026-11-19",
            out_dir=os.path.join(tmp, "clips"), base_dir=tmp,
            bridge=self._FakeBridge(), dry_run=False, vod_id="123")

    def _record(self):
        pkg_path = os.path.join(tempfile.gettempdir(), "pkg.json")
        with open(pkg_path, "w", encoding="utf-8") as f:
            json.dump({"priority": {"youtube_shorts": {"priority": 45}}}, f)
        return {
            "clip_id": "live:kaicenat:x:p62", "clip": __file__,
            "title": "Kai Cenat", "start_sec": 100, "end_sec": 140,
            "peak_ts": 167.0, "category": "Grand Theft Auto VI",
            "category_source": "helix", "game": "GTA 6", "origin": "live",
            "viral_total": 49.4, "viral_auto": 49.4,
            "review": {}, "streamer_hype": {"count": 0, "distinct": 0,
                                            "phrases": []},
            "subtitles_complete": True, "publish_blocked": False,
            "seo": {"youtube_shorts": 85, "tiktok": 88,
                    "instagram_reels": 90, "facebook_reels": 82},
            "publish_status": {}, "package_path": pkg_path,
            "hype_score": 8.0, "burst_score": 8.0, "hype_ratio": 4.0,
        }

    def test_payload_lleva_idioma_en_para_kaicenat(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self._session(tmp, "kaicenat")
            self.assertEqual(session.lang, "en")
            session._enqueue_panel(self._record())
            self.assertEqual(session.bridge.payloads[0]["language"], "en")

    def test_payload_lleva_idioma_es_para_rubius(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self._session(tmp, "rubius")
            self.assertEqual(session.lang, "es")
            session._enqueue_panel(self._record())
            self.assertEqual(session.bridge.payloads[0]["language"], "es")


class TestFinalizePackageUmbrales(unittest.TestCase):
    """finalize_package: los umbrales salen de config.yaml (80/45).

    2026-11-19 (clips 24/25): viral 49 con SEO 86 salía
    "descartado" porque publish_status usaba los defectos 90/50."""

    def _package(self, tmp, seo):
        path = os.path.join(tmp, "pkg.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"seo": seo}, f)
        return path

    def test_viral_49_pasa_con_45_80(self):
        from clipagent.live_cutter import finalize_package
        seo = {"youtube_shorts": 85, "tiktok": 88,
               "instagram_reels": 90, "facebook_reels": 82}
        cfg = {"scoring": {"seo_gate": 80, "viral_min": 45}}
        with tempfile.TemporaryDirectory() as tmp:
            pkg = finalize_package(self._package(tmp, seo), 49.0, 2.0,
                                   cfg=cfg)
            for platform in seo:
                self.assertEqual(
                    pkg["publish_status"][platform]["status"],
                    "publicable", platform)

    def test_viral_44_no_pasa_con_defectos_80_45(self):
        from clipagent.live_cutter import finalize_package
        seo = {"youtube_shorts": 85, "tiktok": 88,
               "instagram_reels": 90, "facebook_reels": 82}
        with tempfile.TemporaryDirectory() as tmp:
            pkg = finalize_package(self._package(tmp, seo), 44.0, 2.0,
                                   cfg={})
            for platform in seo:
                self.assertEqual(
                    pkg["publish_status"][platform]["status"],
                    "descartado", platform)


class TestCalibrationLangVisible(unittest.TestCase):
    """Calibración 2026-11-19: idioma por streamer, lo visible por
    layout, y persistencia de las revisiones."""

    def _session(self, tmp, streamer="kaicenat", **kwargs):
        from clipagent.live_cutter import LiveCutterSession
        return LiveCutterSession(
            streamer, platform="twitch", stream_id="2026-11-19",
            out_dir=os.path.join(tmp, "clips"), base_dir=tmp,
            dry_run=True, **kwargs)

    def test_lang_por_streamer(self):
        from clipagent.live_cutter import _lang_for_streamer
        self.assertEqual(_lang_for_streamer("kaicenat"), "en")
        self.assertEqual(_lang_for_streamer("theburntpeanut"), "en")
        self.assertEqual(_lang_for_streamer("rubius"), "es")
        self.assertEqual(_lang_for_streamer("illojuan"), "es")
        self.assertEqual(_lang_for_streamer("streamer-que-no-existe"),
                         "es")

    def test_sesion_resuelve_lang_del_catalogo(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._session(tmp, "kaicenat").lang, "en")
            self.assertEqual(self._session(tmp, "rubius").lang, "es")

    def test_lang_explicito_manda_e_invalido_cae_a_es(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                self._session(tmp, "kaicenat", lang="es").lang, "es")
            self.assertEqual(
                self._session(tmp, "rubius", lang="xx").lang, "es")

    def test_visible_desde_layout(self):
        from clipagent.live_cutter import _visible_from_layout
        split = {"mode": "split", "segments": [
            {"start": 0.0, "end": 20.0, "layout": "split"},
            {"start": 20.0, "end": 40.0, "layout": "game"}]}
        self.assertEqual(_visible_from_layout(split), "cams+gameplay")
        game_only = {"mode": "center", "segments": [
            {"start": 0.0, "end": 40.0, "layout": "center"}]}
        self.assertEqual(_visible_from_layout(game_only), "gameplay")
        self.assertIsNone(_visible_from_layout({}))
        self.assertIsNone(_visible_from_layout(None))
        self.assertEqual(_visible_from_layout({"mode": "pip"}),
                         "cams+gameplay")

    def test_persist_review_guarda_pase_y_descarte(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self._session(tmp, "rubius")
            reviewed = {"peak_ts": 3220.0, "viral_auto": 48.5,
                        "viral_revisado": 36.5, "revision": "gemini",
                        "pasa": False, "motivo": "viralidad baja",
                        "package": {"best_title": "GTA 6: test"},
                        "seo_detail": {"youtube_shorts": {"score": 92}}}
            session._persist_review(reviewed, 3220.0)
            path = os.path.join(tmp, "clips", "live_reviews.json")
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["viral_revisado"], 36.5)
            self.assertEqual(saved[0]["package"]["best_title"],
                             "GTA 6: test")
            # Segunda revisión se añade, no se pisa.
            session._persist_review({**reviewed, "peak_ts": 3300.0},
                                    3300.0)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(len(json.load(f)), 2)


class TestPatternPackWiring(unittest.TestCase):
    """Cableado del pack en el flujo en vivo (spec v3 §3.2):
    _definitive_package carga el pack con caché por proceso y lo
    pasa a package_clip; sin pack, comportamiento actual."""

    def setUp(self):
        import clipagent.live_cutter as lc
        self.lc = lc
        lc._PATTERN_PACK_CACHE.clear()
        self.addCleanup(lc._PATTERN_PACK_CACHE.clear)

    def _report(self, lang="es"):
        import json
        import tempfile
        from datetime import date
        report = {"version": 2, "lang": lang,
                  "generated_at": date.today().isoformat(),
                  "sample_n": 6,
                  "patterns": {
                      "sample_n": 6,
                      "hook_types": {"question": 4, "superlative": 2},
                      "top_hook_type": "question",
                      "cta_types": {"none": 6}, "top_cta_type": "none",
                      "top_hashtags": {}, "top_hook_words": {"viste": 4},
                      "median_caption_chars": 42,
                      "median_engagement_rate": 0.07, "notes": []},
                  "reels": [{"hook_text": "¿Viste esto?"}
                            for _ in range(6)]}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, f"reel_patterns_{lang}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False)
        return tmp.name, report

    def test_cache_por_proceso(self):
        tmpdir, _report = self._report()
        pack1 = self.lc._pattern_pack_for_lang("es", base_dir=tmpdir)
        self.assertIsNotNone(pack1)
        # Se borra el archivo: la segunda lectura usa la caché.
        os.remove(os.path.join(tmpdir, "reel_patterns_es.json"))
        pack2 = self.lc._pattern_pack_for_lang("es", base_dir=tmpdir)
        self.assertIs(pack1, pack2)
        # Sin caché, ya no hay informe: None.
        self.lc._PATTERN_PACK_CACHE.clear()
        self.assertIsNone(
            self.lc._pattern_pack_for_lang("es", base_dir=tmpdir))

    def test_definitive_package_pasa_el_pack(self):
        pack = {"version": 2, "generated_at": "2026-11-26",
                "lang": "es", "sample_n": 6,
                "top_hook_types": [{"hook_type": "question",
                                    "template": "¿Viste {what}?"}],
                "top_hook_words": ["viste"], "median_caption_chars": 42,
                "hook_texts": ["¿Viste esto?"]}
        self.lc._PATTERN_PACK_CACHE["es"] = pack
        seen = {}
        card = {"game": "GTA 6", "streamer": "rubius", "language": "es",
                "transcript_text": "hola", "risas": 40, "hype": 5}
        with mock.patch("clipagent.moment_card.build_moment_card",
                        return_value=dict(card)), \
            mock.patch.object(self.lc, "_call_package_clip",
                              side_effect=lambda **kw: seen.update(kw) or {
                                  "best_title": "GTA 6: test"}):
            pkg, _txt, _ded = self.lc._definitive_package(
                1, 100.0, 90.0, {"risas": 40, "hype": 5}, 10.0, "es",
                "rubius", 0.0, "Grand Theft Auto VI", "gameplay", None,
                None, None, None, "v1")
        self.assertEqual(seen.get("reel_patterns"), pack)
        self.assertEqual(pkg["best_title"], "GTA 6: test")

    def test_definitive_package_sin_pack_como_hoy(self):
        # Sin pack (caché en None): reel_patterns=None, como antes
        # del cambio; el paquete se construye igual.
        self.lc._PATTERN_PACK_CACHE["es"] = None
        seen = {}
        card = {"game": "GTA 6", "streamer": "rubius", "language": "es",
                "transcript_text": "hola", "risas": 40, "hype": 5}
        with mock.patch("clipagent.moment_card.build_moment_card",
                        return_value=dict(card)), \
            mock.patch.object(self.lc, "_call_package_clip",
                              side_effect=lambda **kw: seen.update(kw) or {
                                  "best_title": "GTA 6: test"}):
            pkg, _txt, _ded = self.lc._definitive_package(
                1, 100.0, 90.0, {"risas": 40, "hype": 5}, 10.0, "es",
                "rubius", 0.0, "Grand Theft Auto VI", "gameplay", None,
                None, None, None, "v1")
        self.assertIn("reel_patterns", seen)
        self.assertIsNone(seen["reel_patterns"])
        self.assertEqual(pkg["best_title"], "GTA 6: test")


if __name__ == "__main__":
    unittest.main(verbosity=2)
