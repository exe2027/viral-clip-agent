#!/usr/bin/env python3
"""Tests §8 grupos 8 y retención (spec títulos-verdad 2026-11-25).

Grupo 8 — reordenamiento live: el paquete definitivo se construye
DESPUÉS de la transcripción, con transcript_text no vacío, y el
título quemado es el del paquete verificado (invariante
«título quemado == título publicado»). Retención (R3.4):
package_clip con best None -> publish_blocked=1, marcador
HELD_TITLE, sin banner quemado y viral.hold_reason en el payload
del panel.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.live_cutter import (
    HELD_TITLE, HOLD_REASON, LiveCutterSession, _recent_titles)
from clipagent.panel_bridge import PanelBridge

TRANSCRIPT_TEXT = (
    "vamos a seguir con la misión principal, había que hacerlo "
    "de Brian para saltar el banco, eso va a estar guapo")
VERIFIED_TITLE = "GTA 6: La misión de Brian: saltar el banco"

FAKE_PKG = {
    "best_title": VERIFIED_TITLE,
    "description": ("Rubius sigue la misión principal: el trabajo "
                    "de Brian para saltar el banco. ¿Crees que el "
                    "atraco sale bien?"),
    "description_first_2_lines": (
        "Rubius sigue la misión principal: el trabajo de Brian "
        "para saltar el banco. ¿Crees que el atraco sale bien?"),
    "tags": ["GTA 6", "rubius", "gameplay"],
    "hashtags": ["#GTA6", "#Rubius", "#Atraco", "#MisionBrian"],
    "thumbnail_text": "El atraco de Brian",
    "pkg_source": "gemini",
    "needs_human_review": False,
}

HELD_PKG = {"best_title": None, "needs_human_review": True,
            "pkg_source": "rules", "description": "",
            "description_first_2_lines": "", "tags": [],
            "hashtags": [], "thumbnail_text": None}


class _GateStub:
    def check(self, ts=None, chat_path=None):
        return True, "Grand Theft Auto VI", "test"


class _RecorderStub:
    def wait_until(self, rel_ts, timeout=180):
        return True

    def extract_window(self, start, duration, out_path):
        with open(out_path, "wb") as f:
            f.write(b"fake-window")
        return out_path, 0.0

    def stop(self):
        return None


class _FakeBridge:
    """Panel mínimo: dedupe + encolado, con registro de llamadas."""

    def __init__(self, titles=None):
        self.titles = titles or []
        self.payloads = []
        self.update_calls = []
        self.tracking = []

    def list_titles(self, days=30):
        return list(self.titles)

    def create_clip(self, payload):
        self.payloads.append(payload)
        return {"id": 9, "title": payload.get("title")}

    def upload_video(self, clip_id, video_path):
        return {"ok": True}

    def update_clip(self, clip_id, fields):
        self.update_calls.append((clip_id, fields))
        return {"ok": True}

    def preview_seen(self, clip_id):
        return {"ok": True}

    def tracking_step(self, clip_id, step, message, estado="completado"):
        self.tracking.append((clip_id, step, message))
        return {"ok": True}


class _SubtitleDouble:
    """subtitle_clip con transcripción mock: llamada 1 transcribe
    (burn=None, escribe SRT + .transcript.json), llamada 2 quema
    (reutiliza el SRT). Registra el orden de las llamadas."""

    def __init__(self, events, no_speech=False):
        self.events = events
        self.no_speech = no_speech

    def __call__(self, video, srt=None, burn=None, **kw):
        if burn is None:
            self.events.append("transcribe")
            srt_path = srt or (os.path.splitext(video)[0] + ".srt")
            tpath = os.path.splitext(srt_path)[0] + ".transcript.json"
            if self.no_speech:
                with open(srt_path, "w", encoding="utf-8") as f:
                    f.write("")
                with open(tpath, "w", encoding="utf-8") as f:
                    json.dump({"segments": [], "no_speech": True,
                               "word_count": 0}, f)
                return {"srt": srt_path, "transcript": tpath,
                        "no_speech": True}
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:05,000\n"
                        f"{TRANSCRIPT_TEXT}\n")
            with open(tpath, "w", encoding="utf-8") as f:
                json.dump({"segments": [
                    {"start": 0.0, "end": 5.0,
                     "text": TRANSCRIPT_TEXT}],
                    "word_count": len(TRANSCRIPT_TEXT.split())}, f)
            return {"srt": srt_path, "transcript": tpath,
                    "no_speech": False}
        self.events.append("burn")
        self.burn_kwargs = {"srt": srt, "burn": burn,
                            "title": kw.get("title")}
        with open(burn, "wb") as f:
            f.write(b"fake-burned")
        return {"srt": srt, "burned": burn, "no_speech": False}


def _fake_cut(video, start_sec, duration, output, **kw):
    with open(output, "wb") as f:
        f.write(b"fake-cut")
    return {"output": output, "reused": False,
            "start_sec": float(start_sec),
            "duration": float(duration),
            "layout": {"mode": "split",
                       "segments": [{"layout": "split"}],
                       "plantilla": "rubius"}}


class TestLiveReorder(unittest.TestCase):
    def _session(self, tmp, bridge):
        from clipagent.jobs import StepJobs
        store = StepJobs(os.path.join(tmp, "queue.db"))
        self.addCleanup(store.close)
        session = LiveCutterSession(
            "rubius", platform="twitch", stream_id="2932415021",
            gate=_GateStub(), store=store,
            out_dir=os.path.join(tmp, "clips"), base_dir=tmp,
            dry_run=True, vod_id="2932415021", latency_s=0,
            bridge=bridge)
        session.recorder = _RecorderStub()
        return session

    def _patches(self, events, pkg, no_speech=False):
        calls = {"package_clip": []}

        def fake_package_clip(*a, **kw):
            calls["package_clip"].append(kw)
            events.append("package")
            return dict(pkg)

        return [
            mock.patch("clipagent.detect.score_clip",
                       return_value={"total": 55.0}),
            mock.patch("clipagent.workers.cut_clip",
                       side_effect=_fake_cut),
            mock.patch("clipagent.workers.subtitle_clip",
                       side_effect=_SubtitleDouble(events, no_speech)),
            mock.patch("clipagent.metadata.package_clip",
                       side_effect=fake_package_clip),
            mock.patch("clipagent.workers.apply_sfx",
                       side_effect=lambda v, **kw: (v, {"events": []})),
            mock.patch("clipagent.render.hype_seconds_from_audio",
                       return_value=[]),
            mock.patch("clipagent.config.template_for_variant",
                       return_value="rubius"),
            mock.patch("clipagent.final_review.validate_final_clip",
                       return_value={"decision": "aprobado",
                                     "pasa": True, "fallos": [],
                                     "motivo": "", "resumen": "",
                                     "scores": {}, "frames_n": 0,
                                     "hard_pasa": True,
                                     "needs_human_review": False}),
            mock.patch("clipagent.final_review.transcript_text_for_srt",
                       return_value=TRANSCRIPT_TEXT),
            mock.patch("clipagent.quality_gate.build_quality_gate",
                       return_value={"decision": "aprobado",
                                     "pasa": True, "criterios": {},
                                     "fallos": [], "fallos_duros": [],
                                     "motivo": ""}),
        ], calls

    def _run_peak(self, session, events, pkg, no_speech=False):
        patches, calls = self._patches(events, pkg, no_speech)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        detail = {"msgs": 104, "risas": 11, "hype": 1, "users": 90,
                  "burst_score": 8.0, "hype_score": 8.5,
                  "hype_ratio": 4.2}
        record = session.process_peak((2010, 104.0, 8.0, detail))
        return record, calls

    def test_paquete_con_transcript_y_titulo_quemado_verificado(self):
        events = []
        bridge = _FakeBridge(titles=[
            {"title": "GTA 6: Otro momento distinto",
             "language": "es", "created_at": "2026-11-25T10:00:00Z"}])
        with tempfile.TemporaryDirectory() as tmp:
            session = self._session(tmp, bridge)
            record, calls = self._run_peak(session, events, FAKE_PKG)
            self.assertIsNotNone(record)
            # Orden: transcribir -> paquete -> quemar.
            self.assertEqual(events, ["transcribe", "package", "burn"])
            # El paquete se construyó con transcripción no vacía.
            self.assertEqual(len(calls["package_clip"]), 1)
            card = calls["package_clip"][0]["moment_card"]
            self.assertTrue((card.get("transcript_text") or "").strip())
            self.assertIn("Brian", card["transcript_text"])
            self.assertTrue(calls["package_clip"][0]["register"])
            # Interfaz fija R4.1: mientras metadata.package_clip no
            # traiga recent_titles/dedupe_verified (agente paralelo),
            # call_package_clip los quita (fallback documentado); con
            # la firma nueva, viajan.
            import inspect
            from clipagent.metadata import package_clip as _pc
            if "recent_titles" in inspect.signature(_pc).parameters:
                self.assertEqual(
                    calls["package_clip"][0]["recent_titles"],
                    bridge.titles)
                self.assertTrue(
                    calls["package_clip"][0]["dedupe_verified"])
            else:
                self.assertNotIn("recent_titles",
                                 calls["package_clip"][0])
            # Invariante: título quemado == título publicado.
            self.assertEqual(record["title"], VERIFIED_TITLE)
            self.assertEqual(record["burned_title"], VERIFIED_TITLE)
            self.assertNotIn("explotó", record["title"])
            self.assertEqual(record["pkg_source"], "gemini")
            self.assertFalse(record["publish_blocked"])
            self.assertTrue(record["viral"]["dedupe_verified"])
            # El paquete en disco lleva el título verificado.
            with open(record["package_path"], encoding="utf-8") as f:
                package = json.load(f)
            self.assertEqual(package["title"], VERIFIED_TITLE)

    def test_sin_titulo_honesto_queda_retenido(self):
        events = []
        bridge = _FakeBridge()
        with tempfile.TemporaryDirectory() as tmp:
            session = self._session(tmp, bridge)
            record, _calls = self._run_peak(session, events, HELD_PKG)
            self.assertIsNotNone(record)
            self.assertTrue(record["publish_blocked"])
            self.assertTrue(record["needs_human_review"])
            self.assertEqual(record["title"], HELD_TITLE)
            self.assertEqual(record["burned_title"], "")
            self.assertIsNone(record["thumbnail_text"])
            self.assertEqual(record["pkg_source"], "held")
            self.assertEqual(record["viral"]["hold_reason"],
                             HOLD_REASON)
            # El paquete mínimo no tiene SEO: nunca se descarta.
            with open(record["package_path"],
                      encoding="utf-8") as f:
                package = json.load(f)
            self.assertEqual(package["seo"], {})
            self.assertTrue(package["needs_human_review"])
            # Encolado al panel: marcador + hold_reason, sin PATCH SEO.
            panel_id = session._enqueue_panel(record)
            self.assertEqual(panel_id, 9)
            payload = bridge.payloads[-1]
            self.assertEqual(payload["title"], HELD_TITLE)
            self.assertEqual(payload["seo_title"], HELD_TITLE)
            self.assertEqual(payload["viral"]["hold_reason"],
                             HOLD_REASON)
            self.assertIn("quality_gate", payload["viral"])
            self.assertEqual(bridge.update_calls, [])
            self.assertNotIn("thumbnail_text", payload)

    def test_transcripcion_fallida_retiene_aunque_haya_titulo(self):
        events = []
        bridge = _FakeBridge()
        with tempfile.TemporaryDirectory() as tmp:
            session = self._session(tmp, bridge)
            patches, _calls = self._patches(events, FAKE_PKG)
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            with mock.patch("clipagent.workers.subtitle_clip",
                           side_effect=RuntimeError("whisper caído")):
                detail = {"msgs": 104, "risas": 11, "hype": 1}
                record = session.process_peak((2010, 104.0, 8.0,
                                               detail))
        self.assertIsNotNone(record)
        self.assertTrue(record["publish_blocked"])
        self.assertEqual(record["title"], HELD_TITLE)
        self.assertEqual(record["burned_title"], "")

    def test_sin_voz_con_titulo_no_es_retencion(self):
        events = []
        bridge = _FakeBridge()
        with tempfile.TemporaryDirectory() as tmp:
            session = self._session(tmp, bridge)
            record, _calls = self._run_peak(session, events, FAKE_PKG,
                                            no_speech=True)
        self.assertIsNotNone(record)
        # Sin voz pero con título honesto: no es retención por
        # título (el bloqueo, si lo hay, es la regla sin-voz).
        self.assertFalse(record["needs_human_review"])
        self.assertEqual(record["title"], VERIFIED_TITLE)
        self.assertEqual(record["burned_title"], VERIFIED_TITLE)


class TestRecentTitles(unittest.TestCase):
    def test_sin_bridge_dedupe_no_verificado(self):
        titles, verified = _recent_titles(None)
        self.assertIsNone(titles)
        self.assertFalse(verified)

    def test_lectura_fallida_fail_open_solo_dedupe(self):
        class _Raising:
            def list_titles(self, days=30):
                raise RuntimeError("panel caído")
        titles, verified = _recent_titles(_Raising())
        self.assertIsNone(titles)
        self.assertFalse(verified)


class TestPanelBridgeListTitles(unittest.TestCase):
    def _fake_urlopen(self, calls, payload):
        class Resp:
            def __init__(self, data):
                self.data = data

            def read(self):
                return json.dumps(self.data).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout=60):
            calls.append((req.get_method(), req.full_url))
            if req.full_url.endswith("/api/auth/login"):
                return Resp({"ok": True, "token": "tok",
                             "tenant": "sebastian"})
            return Resp(payload)
        return fake

    def test_acepta_dict_con_titles(self):
        calls = []
        bridge = PanelBridge(base_url="https://example.invalid",
                             username="seba", password="<redacted>",
                             tenant="sebastian")
        payload = {"titles": [
            {"title": "GTA 6: A", "language": "es",
             "created_at": "2026-11-25T10:00:00Z"},
            {"title": "  ", "language": "es", "created_at": ""},
            {"title": "GTA 6: B", "language": "en",
             "created_at": "2026-11-24T10:00:00Z"}]}
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._fake_urlopen(calls, payload)):
            out = bridge.list_titles(days=30)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["title"], "GTA 6: A")
        self.assertEqual(out[1]["language"], "en")
        self.assertTrue(any("days=30" in u for _m, u in calls))

    def test_acepta_lista_directa(self):
        calls = []
        bridge = PanelBridge(base_url="https://example.invalid",
                             username="seba", password="<redacted>",
                             tenant="sebastian")
        payload = [{"title": "GTA 6: A", "language": "es",
                    "created_at": "2026-11-25T10:00:00Z"}]
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._fake_urlopen(calls, payload)):
            out = bridge.list_titles()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "GTA 6: A")


if __name__ == "__main__":
    unittest.main(verbosity=2)
